"""Carve a *real* HEIC file (modern phone photo format).

HEIC/HEIF is an ISO base-media container. Samsung and Apple phones shoot
it widely, so the box-walking carver must handle it. We generate a real
HEIC with pillow-heif when it is installed and assert byte-exact recovery;
otherwise the test skips (pillow-heif is a test-only convenience, not a
Porens dependency).
"""

from __future__ import annotations

import hashlib
import io
import os
import random

import pytest

pillow_heif = pytest.importorskip("pillow_heif")
from PIL import Image  # noqa: E402

from porens.carve import carve_source, open_source  # noqa: E402


def _make_heic() -> bytes:
    pillow_heif.register_heif_opener()
    im = Image.new("RGB", (160, 120), (90, 160, 210))
    buf = io.BytesIO()
    im.save(buf, "HEIF", quality=70)
    return buf.getvalue()


def test_carve_real_heic(tmp_path):
    heic = _make_heic()
    assert heic[4:8] == b"ftyp"
    rng = random.Random(3)
    junk = bytes(rng.randrange(256) for _ in range(2000))
    img = os.path.join(tmp_path, "h.img")
    with open(img, "wb") as fh:
        fh.write(junk + heic + junk)

    with open_source(img) as src:
        results = [r for r in carve_source(src, kinds={"isobmff"}, min_size=64)]
        assert len(results) == 1
        r = results[0]
        assert r.ext == "heic"
        carved = bytes(src.mm[r.start : r.end])
    assert hashlib.md5(carved).hexdigest() == hashlib.md5(heic).hexdigest()
