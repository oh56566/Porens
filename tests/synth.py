"""Build tiny but structurally valid images for carving tests.

These are hand-constructed so the tests need no image libraries and no
sample binaries in the repo. The carvers walk container structure (they do
not decode pixels), so structurally valid skeletons exercise the real code
paths — including the awkward ones like an EXIF-embedded thumbnail that a
naive carver would stop at.
"""

from __future__ import annotations

import struct
import zlib


def make_jpeg(*, with_embedded_thumb: bool = True) -> bytes:
    """A minimal JPEG: SOI, (APP1 w/ embedded thumbnail), DQT, SOF0, DHT,
    SOS, entropy data with stuffing + a restart marker, then EOI."""

    def seg(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    out = bytearray(b"\xff\xd8")  # SOI

    if with_embedded_thumb:
        # APP1 whose payload is "Exif\0\0" + a *complete* inner JPEG. A naive
        # carver stops at the inner FFD9; a correct one skips APP1 by length.
        inner = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01" \
                b"\x00\x01\x00\x00\xff\xd9"
        out += seg(0xE1, b"Exif\x00\x00" + inner)

    out += seg(0xDB, b"\x00" + b"\x10" * 64)          # DQT
    out += seg(0xC0, b"\x08\x00\x01\x00\x01\x01\x11\x00")  # SOF0 1x1
    out += seg(0xC4, b"\x00" + b"\x01" * 17)          # DHT (dummy)
    out += seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")     # SOS header

    # Entropy-coded data: includes FF00 stuffing and an FFD0 restart marker,
    # neither of which must terminate the scan.
    out += b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"
    out += b"\xff\xd9"  # EOI
    return bytes(out)


def make_png(width: int = 2, height: int = 2) -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        body = ctype + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_gif() -> bytes:
    out = bytearray(b"GIF89a")
    out += struct.pack("<HH", 1, 1)          # logical screen 1x1
    out += bytes([0x00, 0x00, 0x00])         # packed(no GCT), bg, aspect
    out += bytes([0x2C])                     # image descriptor
    out += struct.pack("<HHHH", 0, 0, 1, 1)  # left,top,w,h
    out += bytes([0x00])                     # packed (no LCT)
    out += bytes([0x02])                     # LZW min code size
    out += bytes([0x02, 0x44, 0x01])         # one data sub-block (2 bytes)
    out += bytes([0x00])                     # block terminator
    out += bytes([0x3B])                     # trailer
    return bytes(out)
