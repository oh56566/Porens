"""End-to-end carving tests against a synthetic 'disk image'.

We scatter real (synthetic) files through random junk, carve the image,
and assert that the recovered bytes match the originals exactly. This is
the property that matters: recover the file *close to the original*.
"""

from __future__ import annotations

import os
import random

import pytest

from porens.carve import carve_source, open_source
from porens.recover import recover_to_dir
from tests.synth import make_gif, make_jpeg, make_png


def _build_image(tmp_path) -> tuple[str, dict[str, bytes]]:
    rng = random.Random(1234)
    jpeg = make_jpeg(with_embedded_thumb=True)
    png = make_png(4, 4)
    gif = make_gif()

    def junk(n: int) -> bytes:
        return bytes(rng.randrange(256) for _ in range(n))

    blob = bytearray()
    blob += junk(1000)
    jpeg_off = len(blob)
    blob += jpeg
    blob += junk(777)          # gap
    png_off = len(blob)
    blob += png
    blob += junk(50)
    gif_off = len(blob)
    blob += gif
    blob += junk(2048)

    path = os.path.join(tmp_path, "disk.img")
    with open(path, "wb") as fh:
        fh.write(blob)
    return path, {"jpeg": jpeg, "png": png, "gif": gif}


def test_carve_recovers_exact_bytes(tmp_path):
    path, originals = _build_image(tmp_path)
    with open_source(path) as src:
        results = list(carve_source(src, min_size=8))
        carved = {}
        for r in results:
            carved.setdefault(r.kind, src.mm[r.start : r.end])

    # Every planted type is found and byte-identical.
    for kind, original in originals.items():
        assert kind in carved, f"{kind} not recovered"
        assert carved[kind] == original, f"{kind} bytes differ from original"


def test_jpeg_not_truncated_at_embedded_thumbnail(tmp_path):
    """The classic failure mode: stopping at the EXIF thumbnail's EOI."""
    path, originals = _build_image(tmp_path)
    with open_source(path) as src:
        jpegs = [r for r in carve_source(src, min_size=8) if r.kind == "jpeg"]
    assert len(jpegs) == 1
    assert jpegs[0].size == len(originals["jpeg"])
    assert not jpegs[0].truncated
    assert jpegs[0].confidence >= 0.9


def test_recover_to_dir_writes_files_and_dedupes(tmp_path):
    # Two identical PNGs in the image should collapse to one on dedupe.
    png = make_png(3, 3)
    img = os.path.join(tmp_path, "dup.img")
    with open(img, "wb") as fh:
        fh.write(b"\x00" * 100 + png + b"\x11" * 40 + png + b"\x22" * 100)
    out = os.path.join(tmp_path, "out")

    recs = list(recover_to_dir(img, out, kinds={"png"}, min_size=8, dedupe=True))
    assert len(recs) == 1
    assert os.path.exists(recs[0].out_path)
    with open(recs[0].out_path, "rb") as fh:
        assert fh.read() == png

    # Without dedupe, both copies are written.
    out2 = os.path.join(tmp_path, "out2")
    recs2 = list(recover_to_dir(img, out2, kinds={"png"}, min_size=8, dedupe=False))
    assert len(recs2) == 2


def test_min_size_filter(tmp_path):
    gif = make_gif()
    img = os.path.join(tmp_path, "g.img")
    with open(img, "wb") as fh:
        fh.write(gif)
    out = os.path.join(tmp_path, "o")
    # min_size larger than the file -> nothing recovered.
    recs = list(recover_to_dir(img, out, kinds={"gif"}, min_size=10_000))
    assert recs == []


def test_false_positive_rejected(tmp_path):
    # A JPEG signature with no valid structure after it must be rejected.
    img = os.path.join(tmp_path, "fp.img")
    with open(img, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0" + b"\x00" * 50)  # no SOS, no EOI
    out = os.path.join(tmp_path, "o")
    recs = list(recover_to_dir(img, out, kinds={"jpeg"}, min_size=4))
    assert recs == []
