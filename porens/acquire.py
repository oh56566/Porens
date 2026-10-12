"""Acquisition helpers: get bytes off the device and onto the PC safely.

Porens does not try to root your phone or bypass encryption. Acquisition
is deliberately limited to things that are both legal on your own device
and technically sound:

* **ADB logical pull** — copy the files Android already exposes over USB
  debugging: the photo folders, and crucially the gallery thumbnail
  stores. On an unlocked phone these are read back *decrypted*, so this is
  the realistic way to reach surviving thumbnails of deleted photos on an
  FBE device like the Galaxy S8.
* **SD-card / image carving** — if the photos lived on a removable microSD
  card, image it on the PC with ``dd`` and carve that image. SD cards are
  normally unencrypted FAT/exFAT, which is the single best recovery target.
* **Rooted raw imaging** — only if the device is *already* rooted (we never
  root it for you), image a block device with ``dd`` over ADB.

The forensic principle throughout: never write to the source, always work
from a copy, and record hashes.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# On-device locations worth pulling. Thumbnail stores first — they are the
# payload for deleted-photo recovery when originals are gone.
DEFAULT_PULL_PATHS = (
    "/sdcard/DCIM/.thumbnails",
    "/sdcard/.thumbnails",
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Download",
    "/sdcard/Android/media",  # WhatsApp/Telegram/etc. keep media copies here
)


@dataclass
class AdbResult:
    ok: bool
    output: str


def adb_available() -> bool:
    return shutil.which("adb") is not None


def _run(args: list[str], timeout: int = 0) -> AdbResult:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout or None,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return AdbResult(ok=proc.returncode == 0, output=out.strip())
    except FileNotFoundError:
        return AdbResult(ok=False, output="adb not found on PATH")
    except subprocess.TimeoutExpired:
        return AdbResult(ok=False, output="adb command timed out")


def adb_devices() -> list[str]:
    """Return serial numbers of connected, authorized devices."""

    res = _run(["adb", "devices"])
    serials = []
    for line in res.output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def adb_root_available() -> bool:
    """Best-effort check for an already-rooted device (never roots it)."""

    res = _run(["adb", "shell", "su", "-c", "id"], timeout=10)
    return res.ok and "uid=0" in res.output


def adb_pull(paths: tuple[str, ...], dest: str) -> list[tuple[str, AdbResult]]:
    """Pull each on-device path into ``dest``. Missing paths fail softly."""

    results = []
    for p in paths:
        res = _run(["adb", "pull", p, dest])
        results.append((p, res))
    return results


def dd_image_command(device: str, out_path: str) -> str:
    """Return a copy-pasteable ``dd`` command for imaging a block device.

    We return the command rather than running it: imaging the wrong device
    is destructive, so the user confirms the target themselves.
    """

    return (
        f"sudo dd if={device} of={out_path} bs=4M conv=noerror,sync status=progress"
    )


ACQUISITION_GUIDE = """\
Porens acquisition guide — Galaxy S8 (and Android generally)
============================================================

Read this once before acquiring. The order below is by realistic payoff.

1. microSD card (best case)
   If the deleted photos were ever on a removable microSD card, power off
   the phone, remove the card, and image it on your PC with a card reader:

       # identify the card device first (do NOT guess):
       lsblk            # Linux
       diskutil list    # macOS
       # then image it (replace /dev/sdX with the CARD, never your system disk):
       sudo dd if=/dev/sdX of=sdcard.img bs=4M conv=noerror,sync status=progress

   Then carve it:
       porens carve sdcard.img -o recovered/

   SD cards are usually unencrypted FAT/exFAT, so deleted JPEGs carve well.

2. Thumbnails over ADB (works even with /data encrypted)
   Enable Developer options -> USB debugging on the phone, unlock it, and:
       porens acquire --pull ./pulled/
   This copies DCIM/.thumbnails and media folders. Even when an original
   photo is gone, its gallery thumbnail often survives. Carve them:
       porens thumbnails ./pulled/ -o recovered-thumbs/

3. Cloud trash (often the real rescue)
   Before deep carving, check: Google Photos > Trash (kept ~60 days),
   Samsung Cloud > Recycle bin, Google Drive trash, and any auto-backup
   you had enabled. This is frequently the only path to full-resolution
   originals once a photo is deleted from an encrypted phone.

4. Rooted raw imaging (ONLY if already rooted)
   Porens will not root your device. Unlocking the S8 bootloader factory-
   resets it and destroys exactly the data you want. If, and only if, the
   phone is already rooted, you can image internal storage over ADB:
       adb shell su -c 'dd if=/dev/block/sda of=/sdcard/userdata.img bs=4M'
   Note: on an FBE device the carved result is encrypted ciphertext, so
   this rarely yields recoverable photos. Thumbnails (step 2) remain the
   better bet.

Forensic hygiene
   * Never write to the source. Always carve a copy/image.
   * Keep the original image file; record its SHA-256.
   * Work on a device you own or are authorized to examine.
"""


def print_guide() -> None:
    print(ACQUISITION_GUIDE)
