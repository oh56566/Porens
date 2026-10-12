"""Porens desktop app — a Tkinter GUI over the recovery engine.

A native window so recovery needs no command line: pick an image or device,
choose what to do (full recovery, deleted-file recovery, carving, thumbnail
recovery, or read-only triage), and watch progress; then open the HTML
gallery or the output folder. Tkinter ships with CPython, so the app adds
no dependencies.

The heavy work runs on a worker thread and talks to the UI through a queue
polled on the Tk event loop — Tkinter is not thread-safe, so the worker
never touches widgets directly.
"""

from __future__ import annotations

import os
import queue
import threading
import webbrowser

from . import __version__

OPERATIONS = [
    ("Full recovery (filesystem + carve)", "recover"),
    ("Deleted files only (FAT/exFAT)", "fs"),
    ("Carve by signature", "carve"),
    ("Android thumbnails", "thumbnails"),
    ("Inspect only (read-only triage)", "info"),
]


class _Msg:
    """Queue message kinds (plain tuples tagged by the first element)."""


def _run_job(op: str, source: str, out_dir: str, opts: dict, q: "queue.Queue") -> None:
    """Worker-thread entry point. Posts progress/log/done/error to ``q``."""

    try:
        from .recover import (recover_all, recover_filesystem_to_dir,
                              recover_to_dir, verify_records)
        from .report import write_html_gallery, write_manifest

        def log(msg: str) -> None:
            q.put(("log", msg))

        def progress(done: int, total: int) -> None:
            q.put(("progress", (done / total) if total else 0.0))

        if op == "info":
            from .triage import inspect_image
            log(f"Inspecting {source} …")
            rep = inspect_image(source)
            gib = rep.size / (1024 ** 3)
            log(f"\nSize: {rep.size:,} bytes ({gib:.2f} GiB)")
            log(f"Volumes: {len(rep.volumes)}")
            for i, v in enumerate(rep.volumes):
                log(f"  [{i}] @{v.offset}  {v.fs}  {v.detail}")
                if v.fs != "unknown":
                    log(f"       deleted entries recoverable: {v.deleted_files}")
            if rep.sample_hits:
                kinds = ", ".join(f"{k}={n}" for k, n in sorted(rep.sample_hits.items()))
                log(f"Signature sample: {kinds}")
            log("\nRecommendations:")
            for r in rep.recommendations:
                log(f"  • {r}")
            q.put(("done", {"count": 0, "mode": "info"}, None))
            return

        os.makedirs(out_dir, exist_ok=True)
        records = []
        if op == "recover":
            log("Full recovery: filesystem entries first, then carving …")
            q.put(("indeterminate", True))
            for rec in recover_all(source, out_dir, min_size=opts["min_size"],
                                   on_progress=progress):
                records.append(rec)
        elif op == "fs":
            log("Recovering deleted files from FAT/exFAT directory entries …")
            q.put(("indeterminate", True))
            for rec in recover_filesystem_to_dir(source, out_dir, deleted_only=True,
                                                 dedupe=opts["dedupe"]):
                records.append(rec)
        elif op == "carve":
            log("Carving by signature …")
            for rec in recover_to_dir(source, out_dir, min_size=opts["min_size"],
                                      dedupe=opts["dedupe"], on_progress=progress):
                records.append(rec)
        elif op == "thumbnails":
            from .thumbnails import find_thumb_stores
            q.put(("indeterminate", True))
            stores = ([t.path for t in find_thumb_stores(source)]
                      if os.path.isdir(source) else [source])
            if not stores:
                log("No .thumbdata stores found under that folder.")
            for store in stores:
                log(f"Carving thumbnails from {os.path.basename(store)} …")
                for rec in recover_to_dir(store, out_dir, kinds={"jpeg", "png", "webp"},
                                          min_size=256, dedupe=opts["dedupe"]):
                    records.append(rec)
            for i, rec in enumerate(records):
                rec.index = i

        if opts.get("verify") and records:
            log("Verifying (decoding) recovered images …")
            verify_records(records)

        stats = write_manifest(records, out_dir)
        gallery = None
        if opts.get("html") and records:
            gallery = write_html_gallery(records, out_dir,
                                         title=f"Porens — {op}",
                                         embed=opts.get("embed", False))
        log(f"\nDone. Recovered {stats['count']} file(s).")
        q.put(("done", {**stats, "mode": op}, gallery))
    except Exception as e:  # noqa: BLE001 - surface any failure in the UI
        import traceback
        q.put(("error", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"))


def launch() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"Porens — media recovery  v{__version__}")
    root.minsize(760, 560)

    state = {"q": None, "thread": None, "gallery": None, "out": None}

    # Detect optional decode verification support.
    try:
        from .validate_image import pillow_available
        have_pillow = pillow_available()
    except Exception:
        have_pillow = False

    main = ttk.Frame(root, padding=14)
    main.pack(fill="both", expand=True)
    main.columnconfigure(1, weight=1)

    ttk.Label(main, text="Porens", font=("", 16, "bold")).grid(
        row=0, column=0, sticky="w")
    ttk.Label(main, foreground="#888",
              text="Recover deleted photos & video from images, SD cards and thumbnail stores."
              ).grid(row=0, column=1, columnspan=2, sticky="w", padx=(10, 0))

    # Source --------------------------------------------------------------
    ttk.Label(main, text="Source image / device:").grid(row=1, column=0, sticky="w", pady=(14, 2))
    src_var = tk.StringVar()
    src_entry = ttk.Entry(main, textvariable=src_var)
    src_entry.grid(row=1, column=1, sticky="ew", pady=(14, 2))

    def browse_src():
        p = filedialog.askopenfilename(title="Select a disk/SD-card image")
        if p:
            src_var.set(p)
    ttk.Button(main, text="Browse…", command=browse_src).grid(row=1, column=2, padx=(8, 0), pady=(14, 2))

    # Operation -----------------------------------------------------------
    ttk.Label(main, text="Operation:").grid(row=2, column=0, sticky="w", pady=2)
    op_var = tk.StringVar(value=OPERATIONS[0][0])
    op_combo = ttk.Combobox(main, textvariable=op_var, state="readonly",
                            values=[label for label, _ in OPERATIONS])
    op_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=2)

    # Output --------------------------------------------------------------
    ttk.Label(main, text="Output folder:").grid(row=3, column=0, sticky="w", pady=2)
    out_var = tk.StringVar()
    out_entry = ttk.Entry(main, textvariable=out_var)
    out_entry.grid(row=3, column=1, sticky="ew", pady=2)

    def browse_out():
        p = filedialog.askdirectory(title="Select an output folder")
        if p:
            out_var.set(p)
    ttk.Button(main, text="Browse…", command=browse_out).grid(row=3, column=2, padx=(8, 0), pady=2)

    # Options -------------------------------------------------------------
    opt = ttk.LabelFrame(main, text="Options", padding=10)
    opt.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 6))
    dedupe_var = tk.BooleanVar(value=True)
    html_var = tk.BooleanVar(value=True)
    embed_var = tk.BooleanVar(value=False)
    verify_var = tk.BooleanVar(value=False)
    minsize_var = tk.StringVar(value="512")
    ttk.Checkbutton(opt, text="De-duplicate", variable=dedupe_var).grid(row=0, column=0, sticky="w", padx=4)
    ttk.Checkbutton(opt, text="HTML gallery", variable=html_var).grid(row=0, column=1, sticky="w", padx=4)
    ttk.Checkbutton(opt, text="Embed media in gallery", variable=embed_var).grid(row=0, column=2, sticky="w", padx=4)
    verify_cb = ttk.Checkbutton(
        opt, text="Verify (decode) images" + ("" if have_pillow else " — needs Pillow"),
        variable=verify_var)
    verify_cb.grid(row=0, column=3, sticky="w", padx=4)
    if not have_pillow:
        verify_cb.state(["disabled"])
    ttk.Label(opt, text="Min carve size (bytes):").grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 0))
    ttk.Entry(opt, textvariable=minsize_var, width=10).grid(row=1, column=2, sticky="w", pady=(8, 0))

    # Run + progress ------------------------------------------------------
    run_btn = ttk.Button(main, text="Run recovery")
    run_btn.grid(row=5, column=0, sticky="w", pady=(4, 6))
    prog = ttk.Progressbar(main, mode="determinate", maximum=1.0)
    prog.grid(row=5, column=1, columnspan=2, sticky="ew", pady=(4, 6), padx=(10, 0))
    status_var = tk.StringVar(value="Ready.")
    ttk.Label(main, textvariable=status_var, foreground="#666").grid(
        row=6, column=0, columnspan=3, sticky="w")

    # Log -----------------------------------------------------------------
    logframe = ttk.Frame(main)
    logframe.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(6, 6))
    main.rowconfigure(7, weight=1)
    logframe.rowconfigure(0, weight=1)
    logframe.columnconfigure(0, weight=1)
    log_text = tk.Text(logframe, height=12, wrap="word", state="disabled",
                       background="#111418", foreground="#d6dbe0", insertbackground="#d6dbe0")
    log_text.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(logframe, command=log_text.yview)
    sb.grid(row=0, column=1, sticky="ns")
    log_text.configure(yscrollcommand=sb.set)

    # Result buttons ------------------------------------------------------
    resbar = ttk.Frame(main)
    resbar.grid(row=8, column=0, columnspan=3, sticky="w")
    open_gallery_btn = ttk.Button(resbar, text="Open gallery", state="disabled")
    open_gallery_btn.grid(row=0, column=0, padx=(0, 8))
    open_folder_btn = ttk.Button(resbar, text="Open output folder", state="disabled")
    open_folder_btn.grid(row=0, column=1)

    def append_log(msg: str) -> None:
        log_text.configure(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def set_running(running: bool) -> None:
        run_btn.state(["disabled"] if running else ["!disabled"])

    def open_path(path: str) -> None:
        try:
            webbrowser.open("file://" + os.path.abspath(path))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Porens", f"Could not open:\n{path}\n{e}")

    def poll() -> None:
        q = state["q"]
        if q is not None:
            try:
                while True:
                    kind, *payload = q.get_nowait()
                    if kind == "log":
                        append_log(payload[0])
                    elif kind == "status":
                        status_var.set(payload[0])
                    elif kind == "progress":
                        prog.configure(mode="determinate")
                        prog["value"] = payload[0]
                        status_var.set(f"Scanning… {payload[0]*100:.0f}%")
                    elif kind == "indeterminate":
                        prog.configure(mode="indeterminate")
                        prog.start(12)
                        status_var.set("Working…")
                    elif kind == "error":
                        prog.stop(); prog.configure(mode="determinate"); prog["value"] = 0
                        append_log("ERROR:\n" + payload[0])
                        status_var.set("Failed — see log.")
                        messagebox.showerror("Porens", payload[0].splitlines()[0])
                        set_running(False)
                    elif kind == "done":
                        prog.stop(); prog.configure(mode="determinate")
                        stats, gallery = payload[0], payload[1]
                        prog["value"] = 1.0 if stats.get("mode") != "info" else 0.0
                        status_var.set(
                            "Inspection complete." if stats.get("mode") == "info"
                            else f"Done — {stats.get('count', 0)} file(s) recovered.")
                        state["gallery"] = gallery
                        if gallery:
                            open_gallery_btn.state(["!disabled"])
                        if state["out"] and os.path.isdir(state["out"]):
                            open_folder_btn.state(["!disabled"])
                        set_running(False)
            except queue.Empty:
                pass
        root.after(120, poll)

    def start() -> None:
        source = src_var.get().strip()
        op = dict((label, key) for label, key in OPERATIONS)[op_var.get()]
        out_dir = out_var.get().strip()
        if not source or not os.path.exists(source):
            messagebox.showwarning("Porens", "Pick a source image/device that exists.")
            return
        if op != "info" and not out_dir:
            messagebox.showwarning("Porens", "Pick an output folder.")
            return
        try:
            min_size = max(0, int(minsize_var.get()))
        except ValueError:
            min_size = 512
        opts = {"dedupe": dedupe_var.get(), "html": html_var.get(),
                "embed": embed_var.get(), "verify": verify_var.get(),
                "min_size": min_size}
        # reset UI
        log_text.configure(state="normal"); log_text.delete("1.0", "end"); log_text.configure(state="disabled")
        open_gallery_btn.state(["disabled"]); open_folder_btn.state(["disabled"])
        prog.configure(mode="determinate"); prog["value"] = 0
        state["q"] = queue.Queue(); state["out"] = out_dir
        set_running(True)
        append_log(f"Porens v{__version__} — {op_var.get()}")
        t = threading.Thread(target=_run_job, args=(op, source, out_dir, opts, state["q"]),
                             daemon=True)
        state["thread"] = t
        t.start()

    run_btn.configure(command=start)
    open_gallery_btn.configure(command=lambda: state["gallery"] and open_path(state["gallery"]))
    open_folder_btn.configure(command=lambda: state["out"] and open_path(state["out"]))

    root.after(120, poll)
    # Headless smoke-test affordance: build the UI, run the event loop
    # briefly, then exit cleanly (used by the test suite under Xvfb).
    # Optional demo/testing prefill + auto-run (used for screenshots/tests).
    if os.environ.get("PORENS_GUI_DEMO_SOURCE"):
        src_var.set(os.environ["PORENS_GUI_DEMO_SOURCE"])
        out_var.set(os.environ.get("PORENS_GUI_DEMO_OUT", ""))
        if os.environ.get("PORENS_GUI_DEMO_OP"):
            op_var.set(os.environ["PORENS_GUI_DEMO_OP"])
        root.after(300, start)

    _selftest = os.environ.get("PORENS_GUI_SELFTEST")
    if _selftest:
        try:
            delay = int(_selftest)
        except ValueError:
            delay = 600
        root.after(delay, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
