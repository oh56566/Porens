"""FAT12/16/32 deleted-file recovery.

When a file is deleted on FAT, the directory entry survives almost intact:
the first filename byte is overwritten with ``0xE5``, and the FAT cluster
chain is freed (zeroed). Everything else — the starting cluster, the exact
byte size, the timestamps, and the long filename in the preceding LFN
entries — usually remains. Because the chain is gone we assume the file
was stored contiguously (the common case for photos written once and never
rewritten) and read ``ceil(size / cluster)`` clusters from the start
cluster.

This recovers the original name, size and dates, and an exactly-sized
file — strictly better than signature carving whenever it applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mbr import Volume

ATTR_LFN = 0x0F
ATTR_DIRECTORY = 0x10
ATTR_VOLUME_ID = 0x08
DELETED = 0xE5
END_OF_DIR = 0x00


@dataclass
class FatParams:
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entries: int
    fat_size: int
    total_sectors: int
    root_cluster: int
    fat_type: str  # "FAT12" | "FAT16" | "FAT32"
    vol_offset: int

    @property
    def cluster_bytes(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def root_dir_sectors(self) -> int:
        return ((self.root_entries * 32) + (self.bytes_per_sector - 1)) // self.bytes_per_sector

    @property
    def first_data_sector(self) -> int:
        return self.reserved_sectors + self.num_fats * self.fat_size + self.root_dir_sectors

    def cluster_offset(self, cluster: int) -> int:
        sector = self.first_data_sector + (cluster - 2) * self.sectors_per_cluster
        return self.vol_offset + sector * self.bytes_per_sector

    @property
    def data_byte_start(self) -> int:
        return self.vol_offset + self.first_data_sector * self.bytes_per_sector


@dataclass
class DeletedFile:
    name: str
    size: int
    start_cluster: int
    attr: int
    create_date: str
    write_date: str
    data_offset: int       # absolute byte offset of the first cluster
    is_dir: bool = False
    path: str = ""         # directory path on the volume, e.g. "DCIM/Camera"
    notes: list[str] = field(default_factory=list)


def _u16(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8)


def _u32(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def parse_bpb(boot: bytes, vol_offset: int) -> FatParams | None:
    try:
        bps = _u16(boot, 11)
        spc = boot[13]
        if bps not in (512, 1024, 2048, 4096) or spc == 0:
            return None
        reserved = _u16(boot, 14)
        num_fats = boot[16]
        root_entries = _u16(boot, 17)
        total16 = _u16(boot, 19)
        fat16 = _u16(boot, 22)
        total32 = _u32(boot, 32)
        fat32 = _u32(boot, 36)
        root_cluster = _u32(boot, 44)
        fat_size = fat16 if fat16 else fat32
        total_sectors = total16 if total16 else total32
        if fat_size == 0 or total_sectors == 0 or num_fats == 0:
            return None

        root_dir_sectors = ((root_entries * 32) + (bps - 1)) // bps
        data_sectors = total_sectors - (reserved + num_fats * fat_size + root_dir_sectors)
        if data_sectors <= 0:
            return None
        count_clusters = data_sectors // spc
        if count_clusters < 4085:
            ftype = "FAT12"
        elif count_clusters < 65525:
            ftype = "FAT16"
        else:
            ftype = "FAT32"
        return FatParams(
            bytes_per_sector=bps, sectors_per_cluster=spc,
            reserved_sectors=reserved, num_fats=num_fats,
            root_entries=root_entries, fat_size=fat_size,
            total_sectors=total_sectors,
            root_cluster=root_cluster if ftype == "FAT32" else 0,
            fat_type=ftype, vol_offset=vol_offset,
        )
    except (IndexError, ValueError):
        return None


def _decode_lfn(entries: list[bytes]) -> str:
    """Rebuild a long filename from its LFN entries (already in order)."""
    chunks = []
    for e in entries:
        raw = bytes(e[1:11]) + bytes(e[14:26]) + bytes(e[28:32])
        chunks.append(raw)
    data = b"".join(chunks)
    try:
        name = data.decode("utf-16-le")
    except UnicodeDecodeError:
        name = data.decode("utf-16-le", "replace")
    # Trim at the UTF-16 NUL terminator / 0xFFFF padding.
    for term in ("\x00", "￿"):
        idx = name.find(term)
        if idx != -1:
            name = name[:idx]
    return name


def _short_name(entry: bytes) -> str:
    base = bytes(entry[0:8]).rstrip(b" ")
    ext = bytes(entry[8:11]).rstrip(b" ")
    # First char of a deleted entry is lost; mark it.
    b = base.decode("ascii", "replace")
    if entry[0] == DELETED:
        b = "_" + b[1:] if b else "_"
    name = b
    if ext:
        name += "." + ext.decode("ascii", "replace")
    return name


def _fmt_date(entry: bytes, off: int) -> str:
    d = _u16(entry, off)
    if d == 0:
        return ""
    year = 1980 + ((d >> 9) & 0x7F)
    month = (d >> 5) & 0x0F
    day = d & 0x1F
    return f"{year:04d}-{month:02d}-{day:02d}"


def _iter_dir(read_at, params: FatParams, abs_offset: int, num_bytes: int):
    """Yield 32-byte raw entries from a directory region."""
    data = read_at(abs_offset, num_bytes)
    for i in range(0, len(data) - 31, 32):
        yield data[i : i + 32]


def _collect_from_entries(
    entries: list[bytes], params: FatParams, deleted_only: bool, path: str = ""
) -> list[DeletedFile]:
    out: list[DeletedFile] = []
    pending_lfn: list[bytes] = []
    for entry in entries:
        first = entry[0]
        attr = entry[11]
        if first == END_OF_DIR:
            pending_lfn.clear()
            continue
        if attr == ATTR_LFN:
            # LFN entries stack up (deleted or not); keep raw order.
            pending_lfn.insert(0, entry)
            continue
        if attr & ATTR_VOLUME_ID and not (attr & ATTR_DIRECTORY):
            pending_lfn.clear()
            continue
        if first == 0x2E:  # "." / ".." dot entries
            pending_lfn.clear()
            continue

        is_deleted = first == DELETED
        if deleted_only and not is_deleted:
            pending_lfn.clear()
            continue

        long_name = _decode_lfn(pending_lfn) if pending_lfn else ""
        pending_lfn.clear()
        name = long_name or _short_name(entry)
        size = _u32(entry, 28)
        start = (_u16(entry, 20) << 16) | _u16(entry, 26)
        is_dir = bool(attr & ATTR_DIRECTORY)
        if start < 2 and not is_dir:
            continue  # no cluster -> nothing to recover
        out.append(
            DeletedFile(
                name=name, size=size, start_cluster=start, attr=attr,
                create_date=_fmt_date(entry, 16), write_date=_fmt_date(entry, 24),
                data_offset=params.cluster_offset(start) if start >= 2 else 0,
                is_dir=is_dir, path=path,
                notes=["deleted" if is_deleted else "live"],
            )
        )
    return out


def _read_fat_chain(read_at, params: FatParams, start: int, max_clusters: int) -> list[int]:
    """Follow a live FAT32/16 chain (used to walk live subdirectories)."""
    clusters = []
    cur = start
    fat_base = params.vol_offset + params.reserved_sectors * params.bytes_per_sector
    eoc = {"FAT16": 0xFFF8, "FAT32": 0x0FFFFFF8, "FAT12": 0xFF8}[params.fat_type]
    seen = set()
    while 2 <= cur < eoc and cur not in seen and len(clusters) < max_clusters:
        seen.add(cur)
        clusters.append(cur)
        if params.fat_type == "FAT32":
            ent = read_at(fat_base + cur * 4, 4)
            cur = _u32(ent, 0) & 0x0FFFFFFF
        elif params.fat_type == "FAT16":
            ent = read_at(fat_base + cur * 2, 2)
            cur = _u16(ent, 0)
        else:  # FAT12
            fat_off = cur + (cur // 2)
            ent = read_at(fat_base + fat_off, 2)
            val = _u16(ent, 0)
            cur = (val >> 4) if (cur & 1) else (val & 0x0FFF)
    return clusters


def recover_volume(
    read_at, image_size: int, vol: Volume, deleted_only: bool = True,
    max_dir_depth: int = 8,
) -> tuple[FatParams, list[DeletedFile]]:
    """Enumerate (deleted) files in a FAT volume.

    Walks the live directory tree, collecting deleted entries found in each
    directory — the realistic case of files deleted from otherwise intact
    folders such as ``DCIM/Camera``.
    """

    boot = read_at(vol.offset, 512)
    params = parse_bpb(boot, vol.offset)
    if params is None:
        return (None, [])  # type: ignore[return-value]

    found: list[DeletedFile] = []

    def walk_root():
        if params.fat_type == "FAT32":
            clusters = _read_fat_chain(read_at, params, params.root_cluster, 1 << 20)
            for c in clusters:
                yield from _iter_dir(read_at, params, params.cluster_offset(c), params.cluster_bytes)
        else:
            root_off = params.vol_offset + (
                params.reserved_sectors + params.num_fats * params.fat_size
            ) * params.bytes_per_sector
            yield from _iter_dir(read_at, params, root_off, params.root_dir_sectors * params.bytes_per_sector)

    visited_dirs: set[int] = set()

    def walk_dir_cluster(start_cluster: int, depth: int, prefix: str):
        if start_cluster in visited_dirs:
            return
        visited_dirs.add(start_cluster)
        clusters = _read_fat_chain(read_at, params, start_cluster, 1 << 16)
        entries = []
        for c in clusters:
            entries.extend(
                _iter_dir(read_at, params, params.cluster_offset(c), params.cluster_bytes)
            )
        found.extend(
            _collect_from_entries(entries, params, deleted_only=deleted_only, path=prefix)
        )
        if depth < max_dir_depth:
            for sub in _collect_from_entries(entries, params, deleted_only=False):
                if sub.is_dir and "live" in sub.notes and sub.start_cluster >= 2:
                    child = f"{prefix}/{sub.name}" if prefix else sub.name
                    walk_dir_cluster(sub.start_cluster, depth + 1, child)

    root_entries = list(walk_root())
    found.extend(_collect_from_entries(root_entries, params, deleted_only=deleted_only, path=""))
    for sub in _collect_from_entries(root_entries, params, deleted_only=False):
        if sub.is_dir and "live" in sub.notes and sub.start_cluster >= 2:
            walk_dir_cluster(sub.start_cluster, 1, sub.name)

    return params, found


def read_file_bytes(read_at, params: FatParams, f: DeletedFile) -> bytes:
    """Read a (deleted) file's bytes assuming contiguous allocation."""
    if f.size == 0 or f.start_cluster < 2:
        return b""
    clusters_needed = (f.size + params.cluster_bytes - 1) // params.cluster_bytes
    start_off = params.cluster_offset(f.start_cluster)
    raw = read_at(start_off, clusters_needed * params.cluster_bytes)
    return raw[: f.size]
