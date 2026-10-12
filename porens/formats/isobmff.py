"""Carver for ISO base media files: HEIC/HEIF, MP4, MOV, 3GP.

These are box-structured: a sequence of ``[size][type]`` boxes, where
``size`` covers the whole box including its header. We walk top-level boxes
from the ``ftyp`` box onward, consuming valid boxes until the structure
stops making sense (implausible type or size), which marks the end of the
recovered file. The ``ftyp`` major/compatible brands choose the extension.
"""

from __future__ import annotations

from .base import Buffer, CarveResult, u32be, u64be

MAX_ISOBMFF = 4 * 1024 * 1024 * 1024  # videos can be large
_BRAND_EXT = {
    b"heic": "heic", b"heix": "heic", b"heim": "heic", b"heis": "heic",
    b"hevc": "heic", b"hevx": "heic", b"mif1": "heic", b"msf1": "heic",
    b"avif": "avif",
    b"qt  ": "mov",
    b"3gp": "3gp", b"3g2": "3gp",
}


def _printable_type(t: bytes) -> bool:
    return all(0x20 <= c <= 0x7E for c in t)


def _ext_for(brands: list[bytes]) -> str:
    for b in brands:
        if b in _BRAND_EXT:
            return _BRAND_EXT[b]
        if b[:3] in _BRAND_EXT:
            return _BRAND_EXT[b[:3]]
    return "mp4"  # isom/mp41/mp42/avc1/dash and friends


def carve(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_ISOBMFF)
    pos = start
    first = True
    boxes = 0
    ext = "mp4"
    truncated = False

    while pos + 8 <= hard:
        chunk = bytes(buf[pos : pos + 16])
        size32 = u32be(chunk, 0)
        btype = chunk[4:8]
        if not _printable_type(btype):
            break
        if first:
            if btype != b"ftyp":
                return None
            # Parse ftyp: major brand (4) + minor version (4) + compat brands.
            box_data_len = (size32 if size32 > 1 else 0)
            brands_region = bytes(
                buf[pos + 8 : pos + max(box_data_len, 16)]
            )
            brands = [brands_region[0:4]]
            for i in range(8, len(brands_region) - 3, 4):
                brands.append(brands_region[i : i + 4])
            ext = _ext_for(brands)
            first = False

        if size32 == 1:
            size = u64be(chunk, 8)
            header = 16
        elif size32 == 0:
            # "to end of data" — only sensible for the final box.
            size = hard - pos
            header = 8
            truncated = truncated or (hard < limit)
        else:
            size = size32
            header = 8

        if size < header:
            break
        box_end = pos + size
        if box_end > hard:
            truncated = True
            box_end = hard
            boxes += 1
            pos = box_end
            break
        boxes += 1
        pos = box_end
        if size32 == 0:
            break

    if boxes < 2:
        return None  # ftyp alone is not a usable media file
    conf = 0.5 if truncated else 0.72
    return CarveResult(
        kind="isobmff", start=start, end=pos, confidence=conf, ext=ext,
        truncated=truncated,
        notes=[f"{boxes} boxes, brand->.{ext}"],
    )
