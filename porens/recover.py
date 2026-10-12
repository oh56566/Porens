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
