"""High-level recovery: carve, write files out, dedupe, collect metadata.

This ties the engine together into the operation a user actually wants:
point at an image or device, get a directory of recovered photos plus a
manifest describing each one (offset, size, confidence, EXIF timestamp,
camera, GPS, hash).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .carve import Source, carve_source, open_source
from .exif_meta import ExifData, parse_exif
from .formats import CarveResult

StatusFn = Callable[[str], None]


@dataclass
class Recovered:
    """A file written to the output directory, with its provenance."""

    index: int
    kind: str
    ext: str
    source_offset: int
    size: int
    confidence: float
    truncated: bool
    sha256: str
    out_path: str
    exif: ExifData = field(default_factory=ExifData)
    notes: list[str] = field(default_factory=list)
    source: str = "carve"            # "carve" | "fat" | "exfat"
    original_name: str | None = None  # filename from the directory entry
    fs_path: str | None = None        # directory path on the volume


def _read_region(src: Source, res: CarveResult) -> bytes:
    return src.mm[res.start : res.end]


def _safe_name(index: int, res: CarveResult, exif: ExifData) -> str:
    ts = exif.best_timestamp
    stamp = ""
    if ts:
        # EXIF datetime looks like "2017:08:21 13:45:02"
        stamp = "_" + ts.replace(":", "").replace(" ", "_")
    flag = "_partial" if res.truncated else ""
    return f"{index:06d}_{res.kind}{stamp}{flag}.{res.ext}"


def recover_to_dir(
    image_path: str,
    out_dir: str,
    kinds: set[str] | None = None,
    min_size: int = 512,
    dedupe: bool = True,
    extract_exif: bool = True,
    on_status: StatusFn | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Iterator[Recovered]:
    """Carve ``image_path`` and write recovered files into ``out_dir``.

    Yields a :class:`Recovered` per written file. Exact-duplicate files
    (same SHA-256) are skipped when ``dedupe`` is set — flash images and
    thumbnail stores are full of repeats.
    """

    os.makedirs(out_dir, exist_ok=True)
    seen_hashes: set[str] = set()
    index = 0

    with open_source(image_path) as src:
        for res in carve_source(
            src, kinds=kinds, min_size=min_size, progress=on_progress
        ):
            data = _read_region(src, res)
            digest = hashlib.sha256(data).hexdigest()
            if dedupe and digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            exif = ExifData()
            if extract_exif and res.kind == "jpeg":
                exif = parse_exif(data)

            name = _safe_name(index, res, exif)
            out_path = os.path.join(out_dir, name)
            with open(out_path, "wb") as fh:
                fh.write(data)

            rec = Recovered(
                index=index,
                kind=res.kind,
                ext=res.ext,
                source_offset=res.start,
                size=res.size,
                confidence=res.confidence,
                truncated=res.truncated,
                sha256=digest,
                out_path=out_path,
                exif=exif,
                notes=list(res.notes),
            )
            index += 1
            if on_status is not None and index % 50 == 0:
                on_status(f"recovered {index} files so far...")
            yield rec


def _detect_kind(head: bytes) -> tuple[str, str] | None:
    """Return ``(kind, ext)`` if ``head`` starts with a known signature."""
    from .signatures import COMBINED, kind_for_match

    ext_map = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp",
               "isobmff": "mp4", "tiff": "tif"}
    # ISO base media files carry "ftyp" at offset 4, not 0.
    if head[4:8] == b"ftyp":
        return "isobmff", "mp4"
    m = COMBINED.match(head)
    if not m:
        return None
    kind, _ = kind_for_match(m)
    return kind, ext_map.get(kind, "bin")


def _sanitize(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in name)
    return keep.strip() or "unnamed"


def recover_filesystem_to_dir(
    image_path: str,
    out_dir: str,
    deleted_only: bool = True,
    dedupe: bool = True,
    extract_exif: bool = True,
) -> Iterator[Recovered]:
    """Recover (deleted) files via FAT/exFAT directory entries.

    Unlike carving, this yields the file's original name, exact size and a
    directory path, read from the filesystem's own metadata. Falls back to
    nothing if the image holds no recognizable FAT/exFAT volume (use
    :func:`recover_to_dir` to carve in that case).
    """

    from .filesystems import exfat, fat, mbr

    os.makedirs(out_dir, exist_ok=True)
    seen_hashes: set[str] = set()
    index = 0

    with open_source(image_path) as src:
        def read_at(off: int, length: int) -> bytes:
            return src.mm[off : off + length]

        volumes = mbr.find_volumes(read_at, src.size)
        for vi, vol in enumerate(volumes):
            boot = read_at(vol.offset, 512)
            fat_params = fat.parse_bpb(boot, vol.offset)
            exfat_params = exfat.parse_boot(boot, vol.offset)

            if exfat_params is not None:
                _, files = exfat.recover_volume(read_at, src.size, vol, deleted_only)
                reader = lambda f: exfat.read_file_bytes(read_at, exfat_params, f)
                src_kind = "exfat"
            elif fat_params is not None:
                _, files = fat.recover_volume(read_at, src.size, vol, deleted_only)
                reader = lambda f: fat.read_file_bytes(read_at, fat_params, f)
                src_kind = "fat"
            else:
                continue

            for f in files:
                data = reader(f)
                if not data:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if dedupe and digest in seen_hashes:
                    continue
                seen_hashes.add(digest)

                det = _detect_kind(data[:32])
                name_ext = (f.name.rsplit(".", 1)[-1].lower()
                            if "." in f.name else "")
                if det:
                    kind, ext = det
                    magic_ok = (ext == name_ext) or (ext == "jpg" and name_ext in ("jpg", "jpeg"))
                    conf = 0.95 if magic_ok else 0.8
                else:
                    kind, ext = ("file", name_ext or "bin")
                    conf = 0.55
                best_effort = "fat-chain(best-effort)" in f.notes
                if best_effort:
                    conf *= 0.7

                exif = ExifData()
                if extract_exif and kind == "jpeg":
                    exif = parse_exif(data)

                out_name = f"{index:06d}_{_sanitize(f.name)}"
                out_path = os.path.join(out_dir, out_name)
                with open(out_path, "wb") as fh:
                    fh.write(data)

                rec = Recovered(
                    index=index, kind=kind, ext=ext,
                    source_offset=f.data_offset, size=len(data),
                    confidence=round(conf, 3),
                    truncated=best_effort, sha256=digest, out_path=out_path,
                    exif=exif, notes=list(f.notes),
                    source=src_kind, original_name=f.name,
                    fs_path=(f.path or None),
                )
                index += 1
                yield rec
