"""exFAT deleted-file recovery.

Large SD cards (and all cards formatted on modern phones for >32 GB) use
exFAT. A directory *set* for a file is a File entry (type ``0x85``) plus a
Stream Extension (``0xC0``) plus one or more File Name entries (``0xC1``).
On deletion the high "in use" bit of each type byte is cleared, giving
``0x05`` / ``0x40`` / ``0x41``, but the set otherwise survives: the stream
extension still carries the first cluster, the exact data length, and the
``NoFatChain`` flag, and the name entries still carry the UTF-16 filename.

When ``NoFatChain`` is set the file is contiguous and recovers exactly.
When it is not, the FAT chain has usually been cleared, so we fall back to
a contiguous read and flag the result as best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mbr import Volume

TYPE_FILE = 0x85
TYPE_STREAM = 0xC0
TYPE_NAME = 0xC1
ATTR_DIRECTORY = 0x10


@dataclass
class ExfatParams:
    bytes_per_sector: int
    sectors_per_cluster: int
    fat_offset: int        # sectors
    fat_length: int        # sectors
    cluster_heap_offset: int  # sectors
    cluster_count: int
    root_cluster: int
    vol_offset: int        # bytes

    @property
    def cluster_bytes(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    def cluster_offset(self, cluster: int) -> int:
        return (
            self.vol_offset
            + self.cluster_heap_offset * self.bytes_per_sector
            + (cluster - 2) * self.cluster_bytes
        )

    @property
    def fat_byte_offset(self) -> int:
        return self.vol_offset + self.fat_offset * self.bytes_per_sector


@dataclass
class ExfatFile:
    name: str
    size: int
    start_cluster: int
    is_dir: bool
    no_fat_chain: bool
    data_offset: int
    deleted: bool
    path: str = ""
    notes: list[str] = field(default_factory=list)


def _u16(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8)


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(bytes(b[o : o + 4]), "little")


def _u64(b: bytes, o: int) -> int:
    return int.from_bytes(bytes(b[o : o + 8]), "little")


def parse_boot(boot: bytes, vol_offset: int) -> ExfatParams | None:
    if boot[3:11] != b"EXFAT   ":
        return None
    try:
        bps_shift = boot[108]
        spc_shift = boot[109]
        if not (9 <= bps_shift <= 12) or spc_shift > 25:
            return None
        return ExfatParams(
            bytes_per_sector=1 << bps_shift,
            sectors_per_cluster=1 << spc_shift,
            fat_offset=_u32(boot, 80),
            fat_length=_u32(boot, 84),
            cluster_heap_offset=_u32(boot, 88),
            cluster_count=_u32(boot, 92),
            root_cluster=_u32(boot, 96),
            vol_offset=vol_offset,
        )
    except (IndexError, ValueError):
        return None


def _read_fat_chain(read_at, p: ExfatParams, start: int, cap: int) -> list[int]:
    clusters = []
    cur = start
    seen = set()
    while 2 <= cur <= p.cluster_count + 1 and cur not in seen and len(clusters) < cap:
        seen.add(cur)
        clusters.append(cur)
        ent = read_at(p.fat_byte_offset + cur * 4, 4)
        nxt = _u32(ent, 0)
        if nxt in (0xFFFFFFFF, 0) or nxt == 0xFFFFFFF7:
            break
        cur = nxt
    return clusters


def _dir_entries(read_at, p: ExfatParams, start_cluster: int, cap_clusters: int = 1 << 16):
    """Yield 32-byte entries of a directory (following its FAT chain)."""
    for c in _read_fat_chain(read_at, p, start_cluster, cap_clusters):
        data = read_at(p.cluster_offset(c), p.cluster_bytes)
        for i in range(0, len(data) - 31, 32):
            yield data[i : i + 32]


def _decode_name(name_entries: list[bytes], name_len: int) -> str:
    raw = b"".join(bytes(e[2:32]) for e in name_entries)
    raw = raw[: name_len * 2]
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le", "replace")


def _walk(read_at, p: ExfatParams, start_cluster: int, deleted_only: bool,
          depth: int, max_depth: int, out: list[ExfatFile],
          prefix: str = "", visited: set[int] | None = None) -> None:
    if visited is None:
        visited = set()
    if start_cluster in visited:
        return
    visited.add(start_cluster)
    entries = list(_dir_entries(read_at, p, start_cluster))
    i = 0
    n = len(entries)
    subdirs: list[tuple[int, str]] = []
    while i < n:
        e = entries[i]
        etype = e[0]
        base = etype & 0x7F  # type ignoring the in-use bit
        if etype == 0x00:
            break
        if base != (TYPE_FILE & 0x7F):
            i += 1
            continue
        deleted = (etype & 0x80) == 0
        secondary = e[1]
        attr = _u16(e, 4)
        is_dir = bool(attr & ATTR_DIRECTORY)
        set_entries = entries[i : i + 1 + secondary]
        i += 1 + secondary
        # Locate stream extension + name entries within the set.
        stream = None
        name_entries: list[bytes] = []
        for se in set_entries[1:]:
            if (se[0] & 0x7F) == (TYPE_STREAM & 0x7F) and stream is None:
                stream = se
            elif (se[0] & 0x7F) == (TYPE_NAME & 0x7F):
                name_entries.append(se)
        if stream is None:
            continue
        flags = stream[1]
        no_fat_chain = bool(flags & 0x02)
        name_len = stream[3]
        first_cluster = _u32(stream, 20)
        data_len = _u64(stream, 24)
        name = _decode_name(name_entries, name_len) if name_entries else "(noname)"

        if is_dir and not deleted and first_cluster >= 2 and depth < max_depth:
            child = f"{prefix}/{name}" if prefix else name
            subdirs.append((first_cluster, child))

        if deleted_only and not deleted:
            continue
        if is_dir:
            continue  # we recover files, not directory clusters
        if first_cluster < 2:
            continue
        out.append(
            ExfatFile(
                name=name, size=data_len, start_cluster=first_cluster,
                is_dir=is_dir, no_fat_chain=no_fat_chain,
                data_offset=p.cluster_offset(first_cluster),
                deleted=deleted, path=prefix,
                notes=["deleted" if deleted else "live",
                       "contiguous" if no_fat_chain else "fat-chain(best-effort)"],
            )
        )

    for sub_cluster, sub_prefix in subdirs:
        _walk(read_at, p, sub_cluster, deleted_only, depth + 1, max_depth, out,
              sub_prefix, visited)


def recover_volume(
    read_at, image_size: int, vol: Volume, deleted_only: bool = True,
    max_depth: int = 8,
) -> tuple[ExfatParams, list[ExfatFile]]:
    boot = read_at(vol.offset, 512)
    p = parse_boot(boot, vol.offset)
    if p is None:
        return (None, [])  # type: ignore[return-value]
    out: list[ExfatFile] = []
    _walk(read_at, p, p.root_cluster, deleted_only, 0, max_depth, out)
    return p, out


def read_file_bytes(read_at, p: ExfatParams, f: ExfatFile) -> bytes:
    if f.size == 0 or f.start_cluster < 2:
        return b""
    if f.no_fat_chain or f.deleted:
        # Contiguous read (exact for NoFatChain; best-effort otherwise).
        raw = read_at(f.data_offset, f.size)
        return raw[: f.size]
    # Live, FAT-chained file: follow the chain.
    need = (f.size + p.cluster_bytes - 1) // p.cluster_bytes
    chunks = []
    for c in _read_fat_chain(read_at, p, f.start_cluster, need):
        chunks.append(read_at(p.cluster_offset(c), p.cluster_bytes))
    return b"".join(chunks)[: f.size]
