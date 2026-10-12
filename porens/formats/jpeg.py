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
(``FFD0``-``FFD7``). The result is the *outer* End-of-Image, which is what
"recover close to the original" actually requires.
"""

from __future__ import annotations

from .base import Buffer, CarveResult, u16be

# Photos realistically top out well under this; the cap only guards against
# runaway scans on corrupt data where no EOI is ever found.
MAX_JPEG = 128 * 1024 * 1024

# Standalone markers carry no length field.
_STANDALONE = {0x01, *range(0xD0, 0xD8)}  # TEM + RST0..RST7


def carve(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_JPEG)
    # buf[start:start+3] is FF D8 FF (guaranteed by the signature).
    pos = start + 2  # position just past SOI, at the first marker's FF
    saw_sos = False
    seg_count = 0
    window = bytes(buf[start:hard])  # local copy of the region we scan
    n = len(window)

    def at(i: int) -> int:
        return window[i - start]

    while pos < start + n - 1:
        if at(pos) != 0xFF:
            # Fill garbage between segments is unusual; treat as corruption.
            return _fail_or_trunc(buf, start, pos, saw_sos, seg_count)
        # Collapse any run of 0xFF fill bytes; the marker code is the byte
        # after the final 0xFF.
        while pos < start + n and at(pos) == 0xFF:
            pos += 1
        if pos >= start + n:
            break
        marker = at(pos)
        pos += 1

        if marker == 0xD9:  # EOI — clean end of the outer image.
            end = pos
            conf = 0.92 if saw_sos else 0.55
            return CarveResult(
                kind="jpeg",
                start=start,
                end=end,
                confidence=conf,
                ext="jpg",
                notes=[f"{seg_count} segments, EOI found"],
            )
        if marker == 0xD8:  # Unexpected nested SOI — corruption.
            return _fail_or_trunc(buf, start, pos - 2, saw_sos, seg_count)
        if marker in _STANDALONE:
            continue
        # Everything else is length-prefixed.
        if pos + 2 > start + n:
            break
        seg_len = u16be(window, pos - start)
        if seg_len < 2:
            return _fail_or_trunc(buf, start, pos, saw_sos, seg_count)
        seg_count += 1

        if marker == 0xDA:  # SOS: skip header, then scan entropy data.
            pos += seg_len
            saw_sos = True
            pos = _skip_entropy(window, start, pos, start + n)
            if pos is None:
                # Entropy data ran to the boundary without a terminator.
                return _trunc(start, start + n, saw_sos, seg_count)
            continue

        pos += seg_len  # APPn, DQT, DHT, SOFn, COM, ... stepped over whole.

    return _fail_or_trunc(buf, start, min(pos, start + n), saw_sos, seg_count)


def _skip_entropy(window: bytes, start: int, pos: int, end: int) -> int | None:
    """Advance past entropy-coded scan data; return offset of next marker.

    Returns ``None`` if the data boundary is reached first.
    """

    i = pos - start
    w_end = end - start
    while i < w_end - 1:
        if window[i] == 0xFF:
            nxt = window[i + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                i += 2  # stuffed FF, or restart marker — part of the scan
                continue
            return start + i  # real marker; hand back to the outer walker
        i += 1
    return None


def _fail_or_trunc(
    buf: Buffer, start: int, pos: int, saw_sos: bool, seg_count: int
) -> CarveResult | None:
    # If we never even reached the scan, this almost certainly was not a
    # real JPEG — reject it so we don't litter output with junk.
    if not saw_sos:
        return None
    return _trunc(start, pos, saw_sos, seg_count)


def _trunc(start: int, end: int, saw_sos: bool, seg_count: int) -> CarveResult:
    return CarveResult(
        kind="jpeg",
        start=start,
        end=end,
        confidence=0.4,
        ext="jpg",
        truncated=True,
        notes=[f"{seg_count} segments, no clean EOI (truncated/corrupt)"],
    )
