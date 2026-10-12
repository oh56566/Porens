"""Structure-aware PNG carver.

PNG is a chunked format: an 8-byte signature followed by a stream of
``[length][type][data][crc]`` chunks ending at ``IEND``. We walk the chunk
list to the true ``IEND`` and verify each chunk's CRC-32. CRC verification
makes false positives vanishingly unlikely and lets us flag a file as a
high-confidence clean recovery. Chunks are read from the buffer in place
(no large upfront copy).
"""

from __future__ import annotations

import zlib

from .base import Buffer, CarveResult

MAX_PNG = 256 * 1024 * 1024
SIG_LEN = 8


def _u32(buf: Buffer, pos: int) -> int:
    return int.from_bytes(bytes(buf[pos : pos + 4]), "big")


def carve(buf: Buffer, start: int, limit: int) -> CarveResult | None:
    hard = min(limit, start + MAX_PNG)
    pos = start + SIG_LEN
    first = True
    crc_ok = True
    chunks = 0

    while pos + 8 <= hard:
        length = _u32(buf, pos)
        ctype = bytes(buf[pos + 4 : pos + 8])
        if not ctype.isalpha():
            break  # not a real chunk type
        if first and ctype != b"IHDR":
            return None  # a real PNG must open with IHDR
        first = False
        crc_pos = pos + 8 + length
        chunk_end = crc_pos + 4
        if chunk_end > hard:
            return CarveResult(
                kind="png", start=start, end=hard, confidence=0.35,
                ext="png", truncated=True,
                notes=[f"{chunks} chunks, truncated before IEND"],
            )
        want = _u32(buf, crc_pos)
        got = zlib.crc32(bytes(buf[pos + 4 : crc_pos])) & 0xFFFFFFFF
        if got != want:
            crc_ok = False
        chunks += 1
        pos = chunk_end
        if ctype == b"IEND":
            conf = 0.97 if crc_ok else 0.6
            note = f"{chunks} chunks, IEND found" + ("" if crc_ok else ", CRC mismatch")
            return CarveResult(
                kind="png", start=start, end=pos, confidence=conf, ext="png",
                notes=[note],
            )

    if chunks == 0:
        return None
    return CarveResult(
        kind="png", start=start, end=pos, confidence=0.3, ext="png",
        truncated=True, notes=[f"{chunks} chunks, no IEND"],
    )
