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
    from .report import write_manifest

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
    stats = write_manifest(records, args.out)
    _print_summary(stats)
    return 0


def _cmd_thumbnails(args: argparse.Namespace) -> int:
    import os

    from .recover import recover_to_dir
    from .report import write_manifest
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
    stats = write_manifest(all_records, args.out)
    _print_summary(stats)
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
    c.set_defaults(func=_cmd_carve)

    t = sub.add_parser("thumbnails",
                       help="carve thumbnails from .thumbdata file(s) or a dir")
    t.add_argument("path", help=".thumbdata file, or a directory to search")
    t.add_argument("-o", "--out", required=True, help="output directory")
    t.add_argument("--min-size", type=int, default=256)
    t.add_argument("--no-dedupe", action="store_true")
    t.set_defaults(func=_cmd_thumbnails)

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
