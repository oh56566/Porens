"""Structure-aware JPEG carver.

A naive carver looks for ``FFD8`` then the first ``FFD9`` and stops. That
is wrong in two common ways:

* EXIF (APP1) segments frequently embed a *thumbnail* that is itself a
  complete JPEG with its own ``FFD9``. A naive carver stops at the
  thumbnail's end and recovers a truncated image.
* ``FFD9`` byte pairs occur by chance inside the entropy-coded scan data.

This carver instead walks the JPEG marker stream segment by segment,
skipping length-prefixed segments wholesale (so embedded thumbnails are
stepped over, not scanned into) and reading entropy-coded scan data with
proper handling of byte-stuffing (``FF00``) and restart markers
(``FFD0``-``FFD7``). The result is the *outer* End-of-Image.

It reads from the buffer **in place** (no large upfront copy) and uses the
buffer's ``find`` (a C ``memchr``) to skip entropy data. That keeps a
false-positive ``FFD8FF`` in random data cheap to reject — crucial when
scanning a whole card where such bytes occur by chance.
"""

from __future__ import annotations

from .base import Buffer, CarveResult

# Photos realistically top out well under this; the cap only guards against
# runaway scans on corrupt data where no EOI is ever found.
MAX_JPEG = 128 * 1024 * 1024

# Standalone markers carry no length field.
_STANDALONE = {0x01, *range(0xD0, 0xD8)}  # TEM + RST0..RST7


def carve(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_JPEG)
    pos = start + 2  # just past SOI, at the first marker's 0xFF
    saw_sos = False
    seg_count = 0

    while pos < hard - 1:
        if buf[pos] != 0xFF:
            return _fail_or_trunc(start, pos, saw_sos, seg_count)
        # Collapse a run of 0xFF fill bytes; the code is the byte after them.
        while pos < hard and buf[pos] == 0xFF:
            pos += 1
        if pos >= hard:
            break
        marker = buf[pos]
        pos += 1

        if marker == 0xD9:  # EOI — clean end of the outer image.
            conf = 0.92 if saw_sos else 0.55
            return CarveResult(
                kind="jpeg", start=start, end=pos, confidence=conf, ext="jpg",
                notes=[f"{seg_count} segments, EOI found"],
            )
        if marker == 0xD8:  # unexpected nested SOI -> corruption
            return _fail_or_trunc(start, pos - 2, saw_sos, seg_count)
        if marker in _STANDALONE:
            continue
        if pos + 2 > hard:
            break
        seg_len = (buf[pos] << 8) | buf[pos + 1]
        if seg_len < 2:
            return _fail_or_trunc(start, pos, saw_sos, seg_count)
        seg_count += 1

        if marker == 0xDA:  # SOS: skip header, then scan entropy data.
            pos += seg_len
            saw_sos = True
            nxt = _skip_entropy(buf, pos, hard)
            if nxt is None:
                return _trunc(start, hard, saw_sos, seg_count)
            pos = nxt
            continue

        pos += seg_len  # APPn/DQT/DHT/SOFn/COM — stepped over whole

    return _fail_or_trunc(start, min(pos, hard), saw_sos, seg_count)


def _skip_entropy(buf: Buffer, pos: int, hard: int) -> int | None:
    """Advance past entropy-coded scan data; return the next marker offset.

    Uses ``buf.find`` to jump straight to each ``0xFF`` rather than stepping
    byte by byte, so long scans (and false positives) are both fast.
    """

    while pos < hard - 1:
        nf = buf.find(b"\xff", pos, hard)
        if nf == -1 or nf >= hard - 1:
            return None
        nxt = buf[nf + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
            pos = nf + 2  # stuffed FF, or restart marker — part of the scan
            continue
        return nf  # real marker; hand back to the outer walker
    return None


def _fail_or_trunc(start: int, pos: int, saw_sos: bool, seg_count: int) -> CarveResult | None:
    # Never reached the scan -> almost certainly not a real JPEG; reject.
    if not saw_sos:
        return None
    return _trunc(start, pos, saw_sos, seg_count)


def _trunc(start: int, end: int, saw_sos: bool, seg_count: int) -> CarveResult:
    return CarveResult(
        kind="jpeg", start=start, end=end, confidence=0.4, ext="jpg",
        truncated=True,
        notes=[f"{seg_count} segments, no clean EOI (truncated/corrupt)"],
    )
