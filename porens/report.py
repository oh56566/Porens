"""Write recovery manifests (JSON + CSV) describing what was carved.

A forensic recovery is only useful if you can say *where each file came
from* and *how confident the reconstruction is*. The manifest records the
source offset, size, confidence, truncation flag, hash, and any EXIF
timestamp/camera/GPS for every recovered file.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Iterable

from .recover import Recovered


def _row(rec: Recovered) -> dict:
    return {
        "index": rec.index,
        "file": os.path.basename(rec.out_path),
        "kind": rec.kind,
        "ext": rec.ext,
        "source_offset": rec.source_offset,
        "size": rec.size,
        "confidence": round(rec.confidence, 3),
        "truncated": rec.truncated,
        "sha256": rec.sha256,
        "exif_timestamp": rec.exif.best_timestamp,
        "camera_make": rec.exif.make,
        "camera_model": rec.exif.model,
        "gps_lat": rec.exif.gps_lat,
        "gps_lon": rec.exif.gps_lon,
        "notes": "; ".join(rec.notes),
    }


def write_manifest(records: Iterable[Recovered], out_dir: str) -> dict:
    """Write ``manifest.json`` and ``manifest.csv`` into ``out_dir``.

    Returns summary statistics (also useful for the CLI's closing line).
    """

    rows = [_row(r) for r in records]
    json_path = os.path.join(out_dir, "manifest.json")
    csv_path = os.path.join(out_dir, "manifest.csv")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)

    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    by_kind: dict[str, int] = {}
    clean = partial = 0
    total_bytes = 0
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        total_bytes += r["size"]
        if r["truncated"]:
            partial += 1
        else:
            clean += 1
    return {
        "count": len(rows),
        "by_kind": by_kind,
        "clean": clean,
        "truncated": partial,
        "total_bytes": total_bytes,
        "manifest_json": json_path,
        "manifest_csv": csv_path if rows else None,
    }
