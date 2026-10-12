"""Command-line interface for Porens."""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .formats import CARVERS


def _parse_kinds(value: str | None) -> set[str] | None:
    if not value:
        return None
    kinds = {k.strip() for k in value.split(",") if k.strip()}
    unknown = kinds - set(CARVERS)
    if unknown:
        raise SystemExit(
            f"unknown types: {', '.join(sorted(unknown))}. "
            f"known: {', '.join(sorted(CARVERS))}"
        )
    return kinds


def _progress_printer():
    last = [0.0]

    def fn(done: int, total: int) -> None:
        now = time.time()
        if now - last[0] < 0.2 and done < total:
            return
        last[0] = now
        pct = 100.0 * done / total if total else 100.0
        gb = done / (1024 ** 3)
        tgb = total / (1024 ** 3)
        print(f"\r  scanning {pct:5.1f}%  ({gb:.2f}/{tgb:.2f} GiB)",
              end="", file=sys.stderr, flush=True)
        if done >= total:
            print(file=sys.stderr)

    return fn


def _cmd_carve(args: argparse.Namespace) -> int:
    from .recover import recover_to_dir

    kinds = _parse_kinds(args.types)
    progress = None if args.quiet else _progress_printer()
    records = []
    print(f"Carving {args.image} -> {args.out}", file=sys.stderr)
    for rec in recover_to_dir(
        args.image,
        args.out,
        kinds=kinds,
        min_size=args.min_size,
        dedupe=not args.no_dedupe,
        extract_exif=not args.no_exif,
        on_progress=progress,
    ):
        records.append(rec)
    _finish(records, args.out, make_html=not args.no_html, title="Porens carve", embed=args.embed)
    return 0


def _finish(records, out_dir: str, make_html: bool, title: str,
            embed: bool = False) -> None:
    from .report import write_html_gallery, write_manifest

    stats = write_manifest(records, out_dir)
    _print_summary(stats)
    if make_html and records:
        gallery = write_html_gallery(records, out_dir, title=title, embed=embed)
        print(f"gallery         : {gallery}")


def _cmd_thumbnails(args: argparse.Namespace) -> int:
    import os

    from .recover import recover_to_dir
    from .thumbnails import find_thumb_stores

    stores: list[str]
    if os.path.isdir(args.path):
        found = find_thumb_stores(args.path)
        if not found:
            print(f"No thumbnail stores (.thumbdata*) found under {args.path}",
                  file=sys.stderr)
            return 1
        stores = [t.path for t in found]
        print(f"Found {len(stores)} thumbnail store(s):", file=sys.stderr)
        for t in found:
            print(f"  {t.path}  ({t.size/1024:.0f} KiB)", file=sys.stderr)
    else:
        stores = [args.path]

    all_records = []
    for store in stores:
        for rec in recover_to_dir(
            store,
            args.out,
            kinds={"jpeg", "png", "webp"},
            min_size=args.min_size,
            dedupe=not args.no_dedupe,
            extract_exif=True,
        ):
            all_records.append(rec)
    # Re-index sequentially across stores.
    for i, rec in enumerate(all_records):
        rec.index = i
    _finish(all_records, args.out, make_html=not args.no_html, title="Porens thumbnails", embed=args.embed)
    return 0


def _cmd_fs(args: argparse.Namespace) -> int:
    from .recover import recover_filesystem_to_dir

    print(f"Filesystem recovery on {args.image} -> {args.out}", file=sys.stderr)
    records = list(
        recover_filesystem_to_dir(
            args.image,
            args.out,
            deleted_only=not args.include_live,
            dedupe=not args.no_dedupe,
        )
    )
    if not records:
        print("No FAT/exFAT volume with recoverable entries found. "
              "Try `porens carve` to signature-carve instead.", file=sys.stderr)
    # Show a few recovered names — the headline feature of FS recovery.
    for r in records[:10]:
        loc = f"{r.fs_path}/" if r.fs_path else ""
        print(f"  {r.source}: {loc}{r.original_name}  ({r.size} B, conf {r.confidence})",
              file=sys.stderr)
    _finish(records, args.out, make_html=not args.no_html, title="Porens filesystem recovery", embed=args.embed)
    return 0


def _cmd_acquire(args: argparse.Namespace) -> int:
    from . import acquire

    if args.guide:
        acquire.print_guide()
        return 0

    if not acquire.adb_available():
        print("adb is not installed or not on PATH.", file=sys.stderr)
        print("Install Android platform-tools, or use `porens acquire --guide` "
              "for SD-card / cloud paths that need no adb.", file=sys.stderr)
        return 1

    devices = acquire.adb_devices()
    if not devices:
        print("No authorized device. Connect the phone, unlock it, enable USB "
              "debugging, and accept the RSA prompt.", file=sys.stderr)
        return 1
    print(f"Device(s): {', '.join(devices)}", file=sys.stderr)
    rooted = acquire.adb_root_available()
    print(f"Root available: {'yes' if rooted else 'no'}", file=sys.stderr)

    if args.pull:
        print(f"Pulling media + thumbnails into {args.pull} ...", file=sys.stderr)
        results = acquire.adb_pull(acquire.DEFAULT_PULL_PATHS, args.pull)
        for path, res in results:
            mark = "ok " if res.ok else "skip"
            print(f"  [{mark}] {path}", file=sys.stderr)
        print("\nNext: carve the thumbnails you just pulled:", file=sys.stderr)
        print(f"  porens thumbnails {args.pull} -o recovered-thumbs/",
              file=sys.stderr)
    else:
        print("Pass --pull <dir> to copy media + thumbnails, or --guide for "
              "the full acquisition playbook.", file=sys.stderr)
    return 0


def _print_summary(stats: dict) -> None:
    print("\n=== Porens recovery summary ===")
    print(f"recovered files : {stats['count']}")
    if stats["count"]:
        print(f"  clean         : {stats['clean']}")
        print(f"  truncated     : {stats['truncated']}")
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(stats["by_kind"].items()))
        print(f"  by type       : {kinds}")
        print(f"  total bytes   : {stats['total_bytes']:,}")
        print(f"manifest        : {stats['manifest_json']}")
        if stats["manifest_csv"]:
            print(f"                  {stats['manifest_csv']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="porens",
        description="Android media recovery & forensic file carving.",
    )
    p.add_argument("--version", action="version",
                   version=f"porens {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("carve", help="carve files from an image/device")
    c.add_argument("image", help="path to disk image, SD-card image, or /dev/sdX")
    c.add_argument("-o", "--out", required=True, help="output directory")
    c.add_argument("--types", help="comma list, e.g. jpeg,png,heic (default: all)")
    c.add_argument("--min-size", type=int, default=512,
                   help="drop files smaller than this many bytes (default 512)")
    c.add_argument("--no-dedupe", action="store_true",
                   help="keep exact-duplicate files")
    c.add_argument("--no-exif", action="store_true",
                   help="skip EXIF metadata extraction")
    c.add_argument("--quiet", action="store_true", help="no progress bar")
    c.add_argument("--no-html", action="store_true", help="skip the HTML gallery")
    c.add_argument("--embed", action="store_true",
                   help="inline media into the gallery as data URIs (portable, larger)")
    c.set_defaults(func=_cmd_carve)

    t = sub.add_parser("thumbnails",
                       help="carve thumbnails from .thumbdata file(s) or a dir")
    t.add_argument("path", help=".thumbdata file, or a directory to search")
    t.add_argument("-o", "--out", required=True, help="output directory")
    t.add_argument("--min-size", type=int, default=256)
    t.add_argument("--no-dedupe", action="store_true")
    t.add_argument("--no-html", action="store_true", help="skip the HTML gallery")
    t.add_argument("--embed", action="store_true",
                   help="inline media into the gallery as data URIs (portable, larger)")
    t.set_defaults(func=_cmd_thumbnails)

    f = sub.add_parser("fs",
                       help="recover deleted files via FAT/exFAT directory entries")
    f.add_argument("image", help="path to SD-card image or /dev/sdX")
    f.add_argument("-o", "--out", required=True, help="output directory")
    f.add_argument("--include-live", action="store_true",
                   help="also extract files that are not deleted")
    f.add_argument("--no-dedupe", action="store_true")
    f.add_argument("--no-html", action="store_true", help="skip the HTML gallery")
    f.add_argument("--embed", action="store_true",
                   help="inline media into the gallery as data URIs (portable, larger)")
    f.set_defaults(func=_cmd_fs)

    a = sub.add_parser("acquire", help="pull media/thumbnails over ADB, or guide")
    a.add_argument("--pull", metavar="DIR", help="adb-pull media+thumbnails here")
    a.add_argument("--guide", action="store_true",
                   help="print the acquisition playbook and exit")
    a.set_defaults(func=_cmd_acquire)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
