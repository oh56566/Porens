"""Locate filesystem volumes inside a full-disk image.

An image dumped from a whole card/device may start with an MBR partition
table rather than a filesystem boot sector. This finds the byte offset of
each partition so the FAT/exFAT recovers can be pointed at the volume. If
there is no MBR (the image is a bare volume), we report a single volume at
offset 0.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTOR = 512


@dataclass
class Volume:
    offset: int          # byte offset of the volume within the image
    size: int            # bytes (0 = unknown / to end)
    type_byte: int       # MBR partition type, or -1 for a bare volume
    label: str = ""


def _looks_like_fat_or_exfat(head: bytes) -> bool:
    if len(head) < 512:
        return False
    if head[510:512] != b"\x55\xaa":
        return False
    return (
        head[3:11] == b"EXFAT   "
        or head[82:87] == b"FAT32"
        or head[54:59] in (b"FAT12", b"FAT16")
        or head[54:57] == b"FAT"
    )


def find_volumes(read_at, image_size: int) -> list[Volume]:
    """Return candidate filesystem volumes in the image.

    ``read_at(offset, length)`` returns bytes from the image.
    """

    head = read_at(0, 512)
    volumes: list[Volume] = []

    # A bare FAT/exFAT volume begins right at offset 0.
    if _looks_like_fat_or_exfat(head):
        volumes.append(Volume(offset=0, size=image_size, type_byte=-1))
        return volumes

    # Otherwise try to parse an MBR partition table.
    if len(head) >= 512 and head[510:512] == b"\x55\xaa":
        for i in range(4):
            e = 446 + i * 16
            entry = head[e : e + 16]
            ptype = entry[4]
            if ptype == 0:
                continue
            start_lba = int.from_bytes(entry[8:12], "little")
            num_sectors = int.from_bytes(entry[12:16], "little")
            if start_lba == 0:
                continue
            off = start_lba * SECTOR
            vhead = read_at(off, 512)
            if _looks_like_fat_or_exfat(vhead):
                volumes.append(
                    Volume(offset=off, size=num_sectors * SECTOR, type_byte=ptype)
                )

    if not volumes:
        # Last resort: scan the first slice of the image for a volume boot
        # record (some cards have an unconventional layout).
        step = SECTOR
        for off in range(0, min(image_size, 64 * 1024 * 1024), step * 2048):
            if _looks_like_fat_or_exfat(read_at(off, 512)):
                volumes.append(Volume(offset=off, size=image_size - off, type_byte=-1))
                break

    return volumes
