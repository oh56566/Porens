"""Decode-verification of recovered images (needs Pillow).

``synth.make_jpeg`` is a *structural* skeleton (dummy Huffman tables), so it
is intentionally not a decodable image — perfect for carving tests, wrong
for a decode test. Here we build a genuinely decodable JPEG with Pillow and
truncate a copy of it for the failure case.
"""

from __future__ import annotations

import io
import os

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from porens.recover import recover_to_dir, verify_records  # noqa: E402
from porens import validate_image  # noqa: E402


def _real_jpeg() -> bytes:
    im = Image.new("RGB", (64, 48), (200, 120, 60))
    for x in range(64):
        im.putpixel((x, x % 48), (x * 3 % 256, 10, 90))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_verify_flags_good_and_truncated(tmp_path):
    good = _real_jpeg()
    # Cut just past the Start-of-Scan header, keeping almost no entropy data:
    # the carver still accepts it (it saw SOS) but the image cannot decode.
    sos = good.index(b"\xff\xda")
    seglen = int.from_bytes(good[sos + 2 : sos + 4], "big")
    truncated = good[: sos + 2 + seglen + 3]
    img = os.path.join(tmp_path, "v.img")
    with open(img, "wb") as fh:
        fh.write(b"\x11" * 400 + good + b"\x22" * 300 + truncated + b"\x33" * 400)

    out = os.path.join(tmp_path, "out")
    recs = list(recover_to_dir(img, out, kinds={"jpeg"}, min_size=32, dedupe=False))
    verify_records(recs)

    good_recs = [r for r in recs if r.verified is True]
    bad_recs = [r for r in recs if r.verified is False]
    assert good_recs, "the complete JPEG should decode"
    assert bad_recs, "the truncated JPEG should fail to decode"
    assert max(r.confidence for r in good_recs) >= 0.9
    assert all(r.confidence <= 0.3 and r.truncated for r in bad_recs)


def test_decode_check_skips_non_images():
    ok, _ = validate_image.decode_check(__file__, "isobmff")
    assert ok is None
