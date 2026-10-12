"""Carvers for GIF, WebP (RIFF) and TIFF/DNG.

* **GIF** — walk the block stream (extensions, image descriptors, data
  sub-blocks) to the ``0x3B`` trailer, so animated GIFs carve whole.
* **WebP** — the RIFF header declares the payload size, giving an exact end.
* **TIFF/DNG** — follow IFD chains and track the furthest byte referenced
  by strip/tile offsets to estimate the true extent (raw photos).
"""

from __future__ import annotations

from .base import Buffer, CarveResult, u16be, u32be, u32le


# --------------------------------------------------------------------------- GIF
MAX_GIF = 128 * 1024 * 1024


def carve_gif(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_GIF)
    if start + 13 > hard:
        return None
    packed = buf[start + 10]
    pos = start + 13
    if packed & 0x80:  # global color table present
        pos += 3 * (1 << ((packed & 0x07) + 1))
    while pos < hard:
        intro = buf[pos]
        if intro == 0x3B:  # trailer
            return CarveResult(
                kind="gif", start=start, end=pos + 1,
                confidence=0.9, ext="gif", notes=["trailer found"],
            )
        if intro == 0x21:  # extension
            pos += 2  # introducer + label
            pos = _skip_subblocks(buf, pos, hard)
        elif intro == 0x2C:  # image descriptor
            if pos + 10 > hard:
                break
            lp = buf[pos + 9]  # packed field of image descriptor
            pos += 10
            if lp & 0x80:  # local color table
                pos += 3 * (1 << ((lp & 0x07) + 1))
            pos += 1  # LZW minimum code size
            pos = _skip_subblocks(buf, pos, hard)
        else:
            break
        if pos < 0:
            break
    return None  # no trailer -> reject (GIF false positives are cheap to drop)


def _skip_subblocks(buf: Buffer, pos: int, hard: int) -> int:
    while pos < hard:
        size = buf[pos]
        pos += 1
        if size == 0:  # block terminator
            return pos
        pos += size
    return -1


# --------------------------------------------------------------------------- WebP
def carve_webp(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    head = bytes(buf[start : start + 12])
    if len(head) < 12:
        return None
    riff_size = u32le(head, 4)
    end = start + 8 + riff_size
    if riff_size < 4 or end > limit:
        return None
    return CarveResult(
        kind="webp", start=start, end=end, confidence=0.9, ext="webp",
        notes=["RIFF size exact"],
    )


# --------------------------------------------------------------------------- TIFF/DNG
MAX_TIFF = 512 * 1024 * 1024
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def carve_tiff(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_TIFF)
    head = bytes(buf[start : start + 8])
    if len(head) < 8:
        return None
    le = head[:2] == b"II"
    rd16 = (lambda b, o: u16be_le(b, o, le))
    rd32 = (lambda b, o: u32_le(b, o, le))
    ifd_off = rd32(head, 4)
    furthest = 8
    seen: set[int] = set()
    ifds = 0
    is_dng = False
    # Collect strip/tile offset+count pairs across all IFDs.
    strip_offsets: list[int] = []
    strip_counts: list[int] = []
    tile_offsets: list[int] = []
    tile_counts: list[int] = []

    while ifd_off and ifd_off not in seen and ifds < 64:
        seen.add(ifd_off)
        if start + ifd_off + 2 > hard:
            break
        entry_region = bytes(buf[start + ifd_off : min(start + ifd_off + 2, hard)])
        count = rd16(entry_region, 0)
        table_end = ifd_off + 2 + count * 12 + 4
        if start + table_end > hard:
            break
        furthest = max(furthest, table_end)
        table = bytes(buf[start + ifd_off + 2 : start + ifd_off + 2 + count * 12])
        for i in range(count):
            e = i * 12
            tag = rd16(table, e)
            typ = rd16(table, e + 2)
            num = rd32(table, e + 4)
            tsize = _TYPE_SIZE.get(typ, 0) * num
            val_off = e + 8
            if tsize > 4:
                data_off = rd32(table, val_off)
                furthest = max(furthest, data_off + tsize)
                values = _read_longs(buf, start + data_off, hard, typ, num, le)
            else:
                values = _read_longs_inline(table, val_off, typ, num, le)
            if tag == 0x0111:  # StripOffsets
                strip_offsets = values
            elif tag == 0x0117:  # StripByteCounts
                strip_counts = values
            elif tag == 0x0144:  # TileOffsets
                tile_offsets = values
            elif tag == 0x0145:  # TileByteCounts
                tile_counts = values
            elif tag == 0xC612:  # DNGVersion
                is_dng = True
        next_off_pos = ifd_off + 2 + count * 12
        ndata = bytes(buf[start + next_off_pos : start + next_off_pos + 4])
        ifd_off = rd32(ndata, 0)
        ifds += 1

    for offs, cnts in ((strip_offsets, strip_counts), (tile_offsets, tile_counts)):
        for o, c in zip(offs, cnts):
            furthest = max(furthest, o + c)

    if ifds == 0:
        return None
    end = start + min(furthest, hard - start)
    truncated = start + furthest > hard
    return CarveResult(
        kind="tiff", start=start, end=end,
        confidence=0.6 if not truncated else 0.4,
        ext="dng" if is_dng else "tif", truncated=truncated,
        notes=[f"{ifds} IFD(s), extent={furthest}"],
    )


def u16be_le(b: bytes, o: int, le: bool) -> int:
    return (b[o] | (b[o + 1] << 8)) if le else u16be(b, o)


def u32_le(b: bytes, o: int, le: bool) -> int:
    return u32le(b, o) if le else u32be(b, o)


def _read_longs_inline(table: bytes, off: int, typ: int, num: int, le: bool) -> list[int]:
    out = []
    unit = _TYPE_SIZE.get(typ, 0)
    if unit == 0:
        return out
    for i in range(num):
        o = off + i * unit
        if o + unit > len(table):
            break
        if unit == 2:
            out.append(u16be_le(table, o, le))
        elif unit == 4:
            out.append(u32_le(table, o, le))
        elif unit == 1:
            out.append(table[o])
    return out


def _read_longs(buf: Buffer, abs_off: int, hard: int, typ: int, num: int, le: bool) -> list[int]:
    unit = _TYPE_SIZE.get(typ, 0)
    if unit == 0 or unit == 1:
        return []
    region = bytes(buf[abs_off : min(abs_off + unit * num, hard)])
    return _read_longs_inline(region, 0, typ, num, le)
