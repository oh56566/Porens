"""Read-only triage: look at an image/device and recommend an approach.

Before spending minutes carving, it helps to know what you're holding: how
big it is, whether it has a partition table, which filesystems are present,
roughly how many deleted directory entries survive, and what a quick
signature sample suggests is carvable. ``porens info`` answers that without
writing anything or modifying the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .carve import SCAN_WINDOW, Source, open_source, scan_signatures
from .filesystems import exfat, fat, mbr


@dataclass
class VolumeInfo:
    offset: int
    size: int
    fs: str
    detail: str
    deleted_files: int = 0


@dataclass
class TriageReport:
    path: str
    size: int
    volumes: list[VolumeInfo] = field(default_factory=list)
    sample_bytes: int = 0
    sample_hits: dict[str, int] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


def _count_deleted(read_at, size: int, vol: mbr.Volume, fs: str) -> int:
    try:
        if fs == "exfat":
            _, files = exfat.recover_volume(read_at, size, vol, deleted_only=True)
        else:
            _, files = fat.recover_volume(read_at, size, vol, deleted_only=True)
        return len(files)
    except Exception:
        return 0


def inspect_image(path: str, sample_mb: int = 256) -> TriageReport:
    """Inspect ``path`` and return a triage report (no writes)."""

    with open_source(path) as src:
        rep = TriageReport(path=path, size=src.size)

        def read_at(off: int, length: int) -> bytes:
            return src.mm[off : off + length]

        for vol in mbr.find_volumes(read_at, src.size):
            boot = read_at(vol.offset, 512)
            ex = exfat.parse_boot(boot, vol.offset)
            ft = fat.parse_bpb(boot, vol.offset)
            if ex is not None:
                vi = VolumeInfo(vol.offset, vol.size, "exfat",
                                f"cluster {ex.cluster_bytes} B, root@{ex.root_cluster}")
                vi.deleted_files = _count_deleted(read_at, src.size, vol, "exfat")
            elif ft is not None:
                vi = VolumeInfo(vol.offset, vol.size, ft.fat_type.lower(),
                                f"cluster {ft.cluster_bytes} B")
                vi.deleted_files = _count_deleted(read_at, src.size, vol, "fat")
            else:
                vi = VolumeInfo(vol.offset, vol.size, "unknown", "no FAT/exFAT boot record")
            rep.volumes.append(vi)

        # Quick signature sample over the first chunk (fast estimate only).
        rep.sample_bytes = min(src.size, sample_mb * 1024 * 1024)
        counts: dict[str, int] = {}
        # Temporarily bound the scan by faking a smaller source view.
        for kind, off in scan_signatures(_BoundedSource(src, rep.sample_bytes)):
            counts[kind] = counts.get(kind, 0) + 1
        rep.sample_hits = counts

        _recommend(rep)
        return rep


class _BoundedSource:
    """A Source view limited to the first ``n`` bytes (for sampling)."""

    def __init__(self, src: Source, n: int):
        self.mm = src.mm
        self.size = n


def _recommend(rep: TriageReport) -> None:
    fs_vols = [v for v in rep.volumes if v.fs not in ("unknown",)]
    deleted_total = sum(v.deleted_files for v in rep.volumes)
    if deleted_total:
        rep.recommendations.append(
            f"Run `porens fs` first — {deleted_total} deleted directory "
            "entries are recoverable with original names and sizes."
        )
    if fs_vols:
        rep.recommendations.append(
            "An intact FAT/exFAT filesystem is present; `fs` will beat raw "
            "carving for named, exactly-sized files."
        )
    if rep.sample_hits:
        kinds = ", ".join(f"{k}×{v}" for k, v in sorted(rep.sample_hits.items()))
        rep.recommendations.append(
            f"Signature sample ({fmt_size(rep.sample_bytes)}): "
            f"{kinds}. Run `porens carve` for a full pass."
        )


def fmt_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"first {n / 1024**3:.2f} GiB"
    if n >= 1024 ** 2:
        return f"first {n // (1024*1024)} MiB"
    if n >= 1024:
        return f"first {n // 1024} KiB"
    return f"{n} B"
    if not rep.volumes or all(v.fs == "unknown" for v in rep.volumes):
        rep.recommendations.append(
            "No recognizable filesystem — this looks like a raw/encrypted or "
            "non-FAT volume. Carving is the only option; if it is an encrypted "
            "Android /data image, carved output will be ciphertext."
        )
