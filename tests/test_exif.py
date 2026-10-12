"""EXIF parsing tests using a hand-built EXIF/TIFF block."""

from __future__ import annotations

import struct

from porens.exif_meta import parse_exif


def _build_jpeg_with_exif() -> bytes:
    dt = b"2017:08:21 13:45:02\x00"  # 20 bytes incl. NUL
    assert len(dt) == 20
    exif_ifd_off = 38  # header(8) + IFD0(30)
    dt_off = 56        # + Exif IFD(18)

    ifd0 = struct.pack("<H", 2)
    ifd0 += struct.pack("<HHI", 0x0110, 2, 3) + b"S8\x00\x00"       # Model
    ifd0 += struct.pack("<HHI", 0x8769, 4, 1) + struct.pack("<I", exif_ifd_off)
    ifd0 += struct.pack("<I", 0)
    assert len(ifd0) == 30

    exif_ifd = struct.pack("<H", 1)
    exif_ifd += struct.pack("<HHI", 0x9003, 2, 20) + struct.pack("<I", dt_off)
    exif_ifd += struct.pack("<I", 0)
    assert len(exif_ifd) == 18

    tiff = b"II\x2a\x00" + struct.pack("<I", 8) + ifd0 + exif_ifd + dt
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return b"\xff\xd8" + app1 + b"\xff\xd9"


def test_parse_exif_model_and_datetime():
    data = _build_jpeg_with_exif()
    exif = parse_exif(data)
    assert exif.model == "S8"
    assert exif.datetime_original == "2017:08:21 13:45:02"
    assert exif.best_timestamp == "2017:08:21 13:45:02"


def test_parse_exif_on_non_jpeg_is_safe():
    assert parse_exif(b"not a jpeg at all").model is None


def test_parse_exif_on_jpeg_without_exif_is_safe():
    data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
    exif = parse_exif(data)
    assert exif.model is None and exif.datetime_original is None
