"""Shared types and helpers for structure-aware carvers.

Each carver receives a read-only buffer (an ``mmap`` or ``bytes``), the
offset at which a signature was found, and a hard upper bound (the length
of the backing data). It walks the file's real container structure to find
the true end offset, returning a :class:`CarveResult`, or ``None`` when the
bytes at ``start`` do not actually form a valid file of that type.

Returning ``None`` is how we reject the many false positives that a plain
signature scan produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class Buffer(Protocol):
    """Minimal read-only buffer interface (satisfied by mmap and bytes)."""

    def __len__(self) -> int: ...
    def __getitem__(self, item: slice) -> bytes: ...


@dataclass
class CarveResult:
    """Outcome of carving one file candidate.

    Attributes:
        kind: Format key (``"jpeg"`` etc.).
        start: Absolute start offset in the backing data.
        end: Absolute end offset (exclusive).
        confidence: 0.0-1.0 estimate that this is a genuine, complete file.
            A cleanly terminated file (e.g. JPEG with EOI, PNG with IEND)
            scores high; a truncated or size-capped one scores lower.
        ext: File extension to use when writing out (no dot).
        truncated: True when we hit the data boundary or a sanity cap
            before a proper terminator — the file may be incomplete.
        notes: Free-form diagnostic notes for the report.
    """

    kind: str
    start: int
    end: int
    confidence: float
    ext: str
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.end - self.start


# A carver reads from the buffer; signature-scan already found ``start``.
Carver = Callable[[Buffer, int, int], "CarveResult | None"]


def u16be(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def u32be(b: bytes, off: int) -> int:
    return (b[off] << 24) | (b[off + 1] << 16) | (b[off + 2] << 8) | b[off + 3]


def u32le(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)


def u64be(b: bytes, off: int) -> int:
    return int.from_bytes(bytes(b[off : off + 8]), "big")
