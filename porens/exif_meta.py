"""Minimal, dependency-free EXIF reader for carved JPEGs.

We only need a handful of fields for forensic value and for naming/sorting
recovered files: capture timestamp, camera make/model, and GPS position.
This is a focused TIFF-IFD parser over the APP1/EXIF segment rather than a
full EXIF library, so Porens stays dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

# Tags of interest.
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_DATETIME = 0x0132
_TAG_EXIF_IFD = 0x8769
_TAG_GPS_IFD = 0x8825
_TAG_DATETIME_ORIG = 0x9003
_GPS_LATREF, _GPS_LAT = 0x0001, 0x0002
_GPS_LONREF, _GPS_LON = 0x0003, 0x0004


@dataclass
class ExifData:
    make: str | None = None
    model: str | None = None
    datetime_original: str | None = None
    datetime: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None

    @property
    def best_timestamp(self) -> str | None:
        return self.datetime_original or self.datetime


def _find_app1(data: bytes) -> bytes | None:
    """Return the EXIF TIFF block from a JPEG's APP1 segment, if present."""

    if data[:2] != b"\xff\xd8":
        return None
    pos = 2
    n = len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xDA or marker == 0xD9:  # SOS/EOI — metadata is before this
            return None
        seg_len = (data[pos + 2] << 8) | data[pos + 3]
        seg = data[pos + 4 : pos + 2 + seg_len]
        if marker == 0xE1 and seg[:6] == b"Exif\x00\x00":
            return seg[6:]  # TIFF header starts after "Exif\0\0"
        pos += 2 + seg_len
    return None


class _Tiff:
    def __init__(self, tiff: bytes) -> None:
        self.d = tiff
        self.le = tiff[:2] == b"II"

    def u16(self, o: int) -> int:
        b = self.d
        return (b[o] | (b[o + 1] << 8)) if self.le else ((b[o] << 8) | b[o + 1])

    def u32(self, o: int) -> int:
        b = self.d
        if self.le:
            return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)
        return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]

    def entries(self, ifd_off: int):
        d = self.d
        if ifd_off + 2 > len(d):
            return
        count = self.u16(ifd_off)
        base = ifd_off + 2
        for i in range(count):
            e = base + i * 12
            if e + 12 > len(d):
                return
            tag = self.u16(e)
            typ = self.u16(e + 2)
            num = self.u32(e + 4)
            yield tag, typ, num, e + 8

    def value_offset(self, typ: int, num: int, voff: int) -> int:
        size = _TYPE_SIZE.get(typ, 0) * num
        return self.u32(voff) if size > 4 else voff

    def ascii(self, typ: int, num: int, voff: int) -> str | None:
        if typ != 2:
            return None
        off = self.value_offset(typ, num, voff)
        raw = self.d[off : off + num]
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace") or None

    def rational(self, voff: int) -> float:
        num = self.u32(voff)
        den = self.u32(voff + 4)
        return num / den if den else 0.0

    def gps_coord(self, typ: int, num: int, voff: int) -> float | None:
        if typ != 5 or num < 3:
            return None
        off = self.value_offset(typ, num, voff)
        deg = self.rational(off)
        minutes = self.rational(off + 8)
        seconds = self.rational(off + 16)
        return deg + minutes / 60 + seconds / 3600


def parse_exif(data: bytes) -> ExifData:
    """Extract key EXIF fields from a JPEG byte string. Never raises."""

    out = ExifData()
    try:
        tiff_block = _find_app1(data)
        if tiff_block is None or len(tiff_block) < 8:
            return out
        t = _Tiff(tiff_block)
        ifd0 = t.u32(4)
        exif_ifd = gps_ifd = 0
        for tag, typ, num, voff in t.entries(ifd0):
            if tag == _TAG_MAKE:
                out.make = t.ascii(typ, num, voff)
            elif tag == _TAG_MODEL:
                out.model = t.ascii(typ, num, voff)
            elif tag == _TAG_DATETIME:
                out.datetime = t.ascii(typ, num, voff)
            elif tag == _TAG_EXIF_IFD:
                exif_ifd = t.u32(voff)
            elif tag == _TAG_GPS_IFD:
                gps_ifd = t.u32(voff)
        if exif_ifd:
            for tag, typ, num, voff in t.entries(exif_ifd):
                if tag == _TAG_DATETIME_ORIG:
                    out.datetime_original = t.ascii(typ, num, voff)
        if gps_ifd:
            lat = lon = None
            lat_ref = lon_ref = ""
            for tag, typ, num, voff in t.entries(gps_ifd):
                if tag == _GPS_LATREF:
                    lat_ref = (t.ascii(2, num, voff) or "").upper()
                elif tag == _GPS_LAT:
                    lat = t.gps_coord(typ, num, voff)
                elif tag == _GPS_LONREF:
                    lon_ref = (t.ascii(2, num, voff) or "").upper()
                elif tag == _GPS_LON:
                    lon = t.gps_coord(typ, num, voff)
            if lat is not None:
                out.gps_lat = -lat if lat_ref == "S" else lat
            if lon is not None:
                out.gps_lon = -lon if lon_ref == "W" else lon
    except Exception:
        return out
    return out
