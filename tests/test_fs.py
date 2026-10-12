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
