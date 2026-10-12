"""Structure-aware PNG carver.

PNG is a chunked format: an 8-byte signature followed by a stream of
``[length][type][data][crc]`` chunks ending at ``IEND``. We walk the chunk
list to the true ``IEND`` and verify each chunk's CRC-32. CRC verification
makes false positives vanishingly unlikely and lets us flag a file as a
high-confidence clean recovery.
"""

from __future__ import annotations

import zlib

from .base import Buffer, CarveResult, u32be

MAX_PNG = 256 * 1024 * 1024
SIG_LEN = 8


def carve(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_PNG)
    window = bytes(buf[start:hard])
    n = len(window)
    pos = SIG_LEN  # offset within window
    first = True
    crc_ok = True
    chunks = 0

    while pos + 8 <= n:
        length = u32be(window, pos)
        ctype = window[pos + 4 : pos + 8]
        if not ctype.isalpha():
            break  # not a real chunk type; stop
        if first and ctype != b"IHDR":
            return None  # a real PNG must open with IHDR
        first = False
        data_start = pos + 8
        crc_pos = data_start + length
        chunk_end = crc_pos + 4
        if chunk_end > n:
            # Boundary hit before the chunk completes.
            return CarveResult(
                kind="png", start=start, end=start + n, confidence=0.35,
                ext="png", truncated=True,
                notes=[f"{chunks} chunks, truncated before IEND"],
            )
        # Verify CRC over type+data.
        want = u32be(window, crc_pos)
        got = zlib.crc32(window[pos + 4 : crc_pos]) & 0xFFFFFFFF
        if got != want:
            crc_ok = False
        chunks += 1
        pos = chunk_end
        if ctype == b"IEND":
            conf = 0.97 if crc_ok else 0.6
            note = f"{chunks} chunks, IEND found" + ("" if crc_ok else ", CRC mismatch")
            return CarveResult(
                kind="png", start=start, end=start + pos, confidence=conf,
                ext="png", notes=[note],
            )

    # Walked off the end without IEND.
    if chunks == 0:
        return None
    return CarveResult(
        kind="png", start=start, end=start + pos, confidence=0.3,
        ext="png", truncated=True,
        notes=[f"{chunks} chunks, no IEND"],
    )
