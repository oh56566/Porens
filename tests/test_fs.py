"""Filesystem-recovery tests against *real* FAT/exFAT images.

These build an actual filesystem with standard tools (``mkfs.vfat`` +
``mtools`` for FAT; ``mkfs.exfat`` + loopback + ``mount.exfat-fuse`` for
exFAT), write files, delete some, and assert Porens recovers them with the
right name, path and byte-exact content. When the tools (or privileges for
loopback/FUSE) are unavailable, the test skips rather than fails — the
pure-Python engine is exercised either way; only the fixture is external.
"""

from __future__ import annotations

import hashlib
import mmap
import os
import shutil
import subprocess

import pytest

from porens.filesystems import exfat, fat, mbr
from tests.synth import make_jpeg, make_png

HAVE_VFAT = bool(shutil.which("mkfs.vfat") and shutil.which("mcopy"))
HAVE_EXFAT = bool(shutil.which("mkfs.exfat") and shutil.which("mount.exfat-fuse")
                  and shutil.which("losetup"))


def _read_at_factory(path):
    fd = os.open(path, os.O_RDONLY)
    size = os.fstat(fd).st_size
    mm = mmap.mmap(fd, size, prot=mmap.PROT_READ)
    return mm, size, (lambda o, n: mm[o : o + n])


def _payloads(tmp_path):
    a = make_jpeg() * 40          # ~6 KB, spans several clusters
    b = make_png(16, 16)
    pa = os.path.join(tmp_path, "a.jpg")
    pb = os.path.join(tmp_path, "b.png")
    open(pa, "wb").write(a)
    open(pb, "wb").write(b)
    return (pa, a), (pb, b)


@pytest.mark.skipif(not HAVE_VFAT, reason="mkfs.vfat/mtools not available")
def test_fat32_deleted_recovery(tmp_path):
    (pa, a), (pb, b) = _payloads(tmp_path)
    img = os.path.join(tmp_path, "fat.img")
    subprocess.run(["truncate", "-s", "64M", img], check=True)
    subprocess.run(["mkfs.vfat", "-F", "32", "-n", "TEST", img],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["mmd", "-i", img, "::/DCIM"], check=True)
    subprocess.run(["mmd", "-i", img, "::/DCIM/Camera"], check=True)
    subprocess.run(["mcopy", "-i", img, pa, "::/DCIM/Camera/Holiday Beach.jpg"], check=True)
    subprocess.run(["mcopy", "-i", img, pb, "::/DCIM/Camera/note.png"], check=True)
    subprocess.run(["mdel", "-i", img, "::/DCIM/Camera/Holiday Beach.jpg"], check=True)
    subprocess.run(["mdel", "-i", img, "::/DCIM/Camera/note.png"], check=True)

    mm, size, read_at = _read_at_factory(img)
    try:
        vols = mbr.find_volumes(read_at, size)
        assert vols
        params, files = fat.recover_volume(read_at, size, vols[0], deleted_only=True)
        assert params.fat_type == "FAT32"
        contents = {bytes(fat.read_file_bytes(read_at, params, f)) for f in files}
        # Content recovers byte-exact regardless of how the name was stored.
        assert a in contents
        assert b in contents
        # A long filename is fully reconstructed from its LFN entries, with
        # its directory path. (A short-name-only file loses its first char
        # on deletion — a property of FAT, surfaced honestly, not a bug.)
        by_name = {f.name: f for f in files}
        assert "Holiday Beach.jpg" in by_name
        assert by_name["Holiday Beach.jpg"].path == "DCIM/Camera"
    finally:
        mm.close()


@pytest.mark.skipif(not HAVE_EXFAT, reason="exfat tools/loopback/fuse not available")
def test_exfat_deleted_recovery(tmp_path):
    (pa, a), (pb, b) = _payloads(tmp_path)
    img = os.path.join(tmp_path, "exfat.img")
    mnt = os.path.join(tmp_path, "mnt")
    os.makedirs(mnt, exist_ok=True)
    subprocess.run(["truncate", "-s", "96M", img], check=True)
    subprocess.run(["mkfs.exfat", "-n", "TESTX", img],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    loop = subprocess.run(["losetup", "-f"], capture_output=True, text=True).stdout.strip()
    if not loop:
        pytest.skip("no free loop device")
    subprocess.run(["losetup", loop, img], check=True)
    try:
        r = subprocess.run(["mount.exfat-fuse", loop, mnt], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.ismount(mnt):
            pytest.skip(f"exfat fuse mount unavailable: {r.stderr.strip()}")
        try:
            os.makedirs(os.path.join(mnt, "DCIM/Camera"), exist_ok=True)
            shutil.copy(pa, os.path.join(mnt, "DCIM/Camera/Holiday Beach.jpg"))
            shutil.copy(pb, os.path.join(mnt, "DCIM/Camera/note.png"))
            subprocess.run(["sync"])
            os.remove(os.path.join(mnt, "DCIM/Camera/Holiday Beach.jpg"))
            os.remove(os.path.join(mnt, "DCIM/Camera/note.png"))
            subprocess.run(["sync"])
        finally:
            subprocess.run(["umount", mnt])
    finally:
        subprocess.run(["losetup", "-d", loop])

    mm, size, read_at = _read_at_factory(img)
    try:
        vols = mbr.find_volumes(read_at, size)
        p, files = exfat.recover_volume(read_at, size, vols[0], deleted_only=True)
        got = {f.name: exfat.read_file_bytes(read_at, p, f) for f in files}
        assert got.get("Holiday Beach.jpg") == a
        assert got.get("note.png") == b
        paths = {f.name: f.path for f in files}
        assert paths["Holiday Beach.jpg"] == "DCIM/Camera"
    finally:
        mm.close()


def test_bpb_parser_rejects_garbage():
    assert fat.parse_bpb(b"\x00" * 512, 0) is None
    assert exfat.parse_boot(b"\x00" * 512, 0) is None


def _build_mbr(part_lba: int, num_sectors: int, ptype: int = 0x0C) -> bytes:
    """A 512-byte MBR with one partition entry at ``part_lba``."""
    mbr = bytearray(512)
    e = 446
    mbr[e + 4] = ptype  # 0x0C = FAT32 LBA
    mbr[e + 8 : e + 12] = part_lba.to_bytes(4, "little")
    mbr[e + 12 : e + 16] = num_sectors.to_bytes(4, "little")
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


@pytest.mark.skipif(not HAVE_VFAT, reason="mkfs.vfat/mtools not available")
def test_whole_disk_image_with_mbr(tmp_path):
    """A dd of a whole card: MBR + a FAT partition at a byte offset."""
    (pa, a), _ = _payloads(tmp_path)
    vol = os.path.join(tmp_path, "vol.img")
    subprocess.run(["truncate", "-s", "64M", vol], check=True)
    subprocess.run(["mkfs.vfat", "-F", "32", vol],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["mcopy", "-i", vol, pa, "::/photo.jpg"], check=True)
    subprocess.run(["mdel", "-i", vol, "::/photo.jpg"], check=True)

    part_lba = 2048  # 1 MiB, the conventional first-partition offset
    vol_bytes = open(vol, "rb").read()
    disk = os.path.join(tmp_path, "disk.img")
    with open(disk, "wb") as fh:
        fh.write(_build_mbr(part_lba, len(vol_bytes) // 512))
        fh.write(b"\x00" * (part_lba * 512 - 512))  # gap to the partition
        fh.write(vol_bytes)

    mm, size, read_at = _read_at_factory(disk)
    try:
        vols = mbr.find_volumes(read_at, size)
        assert vols and vols[0].offset == part_lba * 512
        params, files = fat.recover_volume(read_at, size, vols[0], deleted_only=True)
        assert params is not None
        contents = {bytes(fat.read_file_bytes(read_at, params, f)) for f in files}
        assert a in contents
    finally:
        mm.close()


@pytest.mark.skipif(not HAVE_VFAT, reason="mkfs.vfat/mtools not available")
def test_recover_all_and_info(tmp_path):
    from porens.recover import recover_all
    from porens.triage import inspect_image

    (pa, a), (pb, b) = _payloads(tmp_path)
    img = os.path.join(tmp_path, "mix.img")
    subprocess.run(["truncate", "-s", "64M", img], check=True)
    subprocess.run(["mkfs.vfat", "-F", "32", img],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["mcopy", "-i", img, pa, "::/gone.jpg"], check=True)
    subprocess.run(["mcopy", "-i", img, pb, "::/kept.png"], check=True)
    subprocess.run(["mdel", "-i", img, "::/gone.jpg"], check=True)  # delete one

    # Triage sees the FAT volume and the surviving deleted entry.
    rep = inspect_image(img, sample_mb=64)
    assert rep.volumes and rep.volumes[0].fs == "fat32"
    assert rep.volumes[0].deleted_files >= 1

    # Full recovery: deleted file via fat, the live one via carve, no dupes.
    out = os.path.join(tmp_path, "out")
    recs = list(recover_all(img, out, min_size=8))
    contents = {open(r.out_path, "rb").read() for r in recs}
    assert a in contents       # deleted jpg recovered
    assert b in contents       # live png found by carving
    hashes = [r.sha256 for r in recs]
    assert len(hashes) == len(set(hashes))   # cross-pass dedupe held
    assert {r.index for r in recs} == set(range(len(recs)))  # unique indices
