"""GUI tests.

The worker (`_run_job`) carries the app's logic and imports no Tk, so it is
tested everywhere. The actual Tk window is built only where Tkinter and a
display are available (it skips otherwise), using the headless self-test
affordance.
"""

from __future__ import annotations

import os
import queue

from porens.gui import _run_job
from tests.synth import make_jpeg


def _drain(q: "queue.Queue"):
    msgs = []
    while not q.empty():
        msgs.append(q.get())
    return msgs


def test_run_job_carve(tmp_path):
    img = tmp_path / "c.img"
    img.write_bytes(b"\x00" * 300 + make_jpeg() + b"\xff" * 300)
    out = tmp_path / "out"
    q: queue.Queue = queue.Queue()
    _run_job("carve", str(img), str(out), {
        "dedupe": True, "html": True, "embed": False, "verify": False,
        "min_size": 64,
    }, q)
    msgs = _drain(q)
    done = [m for m in msgs if m[0] == "done"]
    assert done, f"no done message; got {[m[0] for m in msgs]}"
    stats, gallery = done[-1][1], done[-1][2]
    assert stats["count"] >= 1
    assert gallery and os.path.exists(gallery)
    assert (out / "manifest.json").exists()


def test_run_job_info(tmp_path):
    img = tmp_path / "i.img"
    img.write_bytes(b"\x11" * 4096)
    q: queue.Queue = queue.Queue()
    _run_job("info", str(img), "", {}, q)
    msgs = _drain(q)
    assert any(m[0] == "done" for m in msgs)
    assert any(m[0] == "log" for m in msgs)


def test_run_job_reports_errors(tmp_path):
    q: queue.Queue = queue.Queue()
    _run_job("carve", "/no/such/image.img", str(tmp_path / "o"), {
        "dedupe": True, "html": False, "embed": False, "verify": False,
        "min_size": 512,
    }, q)
    msgs = _drain(q)
    assert any(m[0] == "error" for m in msgs)


def test_gui_builds_if_display_available():
    import pytest

    tk = pytest.importorskip("tkinter")
    if not os.environ.get("DISPLAY"):
        pytest.skip("no X display")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk cannot open a display")
    root.destroy()
    # Build the full app briefly, then auto-close via the self-test hook.
    from porens.gui import launch
    os.environ["PORENS_GUI_SELFTEST"] = "200"
    try:
        assert launch() == 0
    finally:
        os.environ.pop("PORENS_GUI_SELFTEST", None)
