"""Core carving engine: scan a data source and reconstruct files.

The engine works on any seekable byte source — a ``dd`` image, a raw block
device (``/dev/sdX``), an SD-card image, or an Android thumbnail database.
It memory-maps the source for random access (so multi-gigabyte images cost
little RAM), scans it in windows for signatures, then hands each hit to a
structure-aware carver.

Overlap handling is important: when a file is accepted, signatures that
fall *inside* it (for example a thumbnail embedded in a JPEG's EXIF) are
skipped, so we recover the real photo once rather than the photo plus a
litter of fragments.
"""

from __future__ import annotations

import mmap
import os
from dataclasses import dataclass
from typing import Callable, Iterator

from .formats import CARVERS, CarveResult
from .signatures import MAX_NEEDLE_REACH, NEEDLES

# Window for the signature scan. Carvers read past the window edge directly
# from the mmap, so a file larger than the window is no problem.
SCAN_WINDOW = 64 * 1024 * 1024
# Must exceed the furthest byte any needle+validator reads past its start.
SCAN_OVERLAP = max(64, MAX_NEEDLE_REACH)

ProgressFn = Callable[[int, int], None]  # (bytes_scanned, total)


@dataclass
class Source:
    """An opened, memory-mapped byte source."""

    path: str
    fd: int
    mm: mmap.mmap
    size: int

    def close(self) -> None:
        try:
            self.mm.close()
        finally:
            os.close(self.fd)

    def __enter__(self) -> "Source":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_source(path: str) -> Source:
    """Open ``path`` (regular file or block device) for carving."""

    fd = os.open(path, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        size = st.st_size
        if size == 0:
            # Block devices often report size 0 via fstat; probe the end.
            size = os.lseek(fd, 0, os.SEEK_END)
            os.lseek(fd, 0, os.SEEK_SET)
        if size == 0:
            raise ValueError(f"{path}: cannot determine size (empty source?)")
        mm = mmap.mmap(fd, size, prot=mmap.PROT_READ)
    except Exception:
        os.close(fd)
        raise
    return Source(path=path, fd=fd, mm=mm, size=size)


def scan_signatures(
    src: Source,
    kinds: set[str] | None = None,
    progress: ProgressFn | None = None,
) -> Iterator[tuple[str, int]]:
    """Yield ``(kind, start_offset)`` for every signature hit, in order.

    Each format is located with a fixed-literal ``bytes.find`` (a C
    ``memmem``) rather than a regex, then confirmed by its validator. Hits
    within a window are sorted by offset and de-duplicated across window
    boundaries. ``kinds`` optionally restricts which formats are reported.
    """

    needles = [n for n in NEEDLES if kinds is None or n.kind in kinds]
    pos = 0
    last_yielded = -1
    size = src.size
    mm = src.mm
    while pos < size:
        end = min(pos + SCAN_WINDOW + SCAN_OVERLAP, size)
        window = mm[pos:end]
        wlen = len(window)
        hits: list[tuple[int, str]] = []
        for n in needles:
            lit = n.literal
            i = window.find(lit)
            while i != -1:
                # Validate using bytes from the window (plus tail); the tail
                # is covered by SCAN_OVERLAP except at end-of-file.
                seg = window[i : i + len(lit) + n.tail]
                if n.validate(seg):
                    start = pos + i + n.adjust
                    if start >= 0:
                        hits.append((start, n.kind))
                nxt = window.find(lit, i + 1)
                if nxt == -1:
                    break
                i = nxt
        hits.sort()
        for start, kind in hits:
            if start <= last_yielded:
                continue
            last_yielded = start
            yield kind, start
        if progress is not None:
            progress(min(end, size), size)
        pos += SCAN_WINDOW


def carve_source(
    src: Source,
    kinds: set[str] | None = None,
    min_size: int = 512,
    progress: ProgressFn | None = None,
) -> Iterator[CarveResult]:
    """Scan ``src`` and yield reconstructed files.

    Signatures inside an already-accepted file are skipped. Files smaller
    than ``min_size`` bytes are dropped (they are almost always icons,
    stray thumbnails, or false positives rather than recoverable photos).
    """

    cursor = 0  # end of the most recently accepted file
    for kind, start in scan_signatures(src, kinds=kinds, progress=progress):
        if start < cursor:
            continue  # falls inside a file we already recovered
        carver = CARVERS.get(kind)
        if carver is None:
            continue
        result = carver(src.mm, start, src.size)
        if result is None:
            continue
        if result.size < min_size:
            continue
        cursor = result.end
        yield result
