# Porens v0.1.1

Android media recovery & PC-side forensic file carving.

Recover deleted photos and videos from disk images, SD-card images, raw
devices, and Android thumbnail stores, on your own PC, with the standard
library alone.

## What's new in v0.1.1

- **Desktop app — Source now accepts a file, a folder, or a device.** The
  Source row has separate **File…** and **Folder…** buttons (a folder is
  what the Thumbnails operation needs), and you can type a device path such
  as `/dev/sdb` or `\\.\PhysicalDrive1`. Choosing a folder for a non-thumbnail
  operation now gives a clear message instead of a cryptic error.

## Highlights

- **Filesystem recovery** (`fs`) — FAT12/16/32 and exFAT deleted-entry
  recovery that restores each file's original **name, path, exact size and
  timestamps**; long filenames rebuilt from LFN entries; walks
  subdirectories and MBR partitions.
- **Structure-aware carving** (`carve`) — JPEG (progressive, restart
  markers, EXIF-embedded thumbnails handled), PNG (CRC-verified), HEIC/HEIF,
  MP4/MOV/3GP, GIF, WebP, TIFF/DNG. Finds the true file end, not the first
  terminator.
- **Full recovery** (`recover`) — filesystem pass then carving, de-duplicated
  into one output and gallery.
- **Thumbnails** (`thumbnails`) — carve surviving images from Android
  `.thumbdata` stores (reachable over ADB even when `/data` is encrypted).
- **Triage** (`info`) — read-only report of partitions, filesystems,
  recoverable deleted entries, and a signature sample, with a recommendation.
- **Decode verification** (`--verify`) — optionally decode each recovered
  image (Pillow) to flag files that are well-formed but will not render.
- **HTML gallery** — review results with thumbnails, EXIF, confidence and GPS.
- **Desktop app** — a Tkinter GUI (`porens gui`), and a PyInstaller build
  script for a standalone `Porens` executable (Windows/macOS/Linux).

## Honest scope

Porens performs no rooting and no encryption bypass. On a file-based-encrypted
device (e.g. the Galaxy S8), raw `/data` carving yields ciphertext; the
realistic paths are removable SD cards, surviving thumbnails, app copies, and
cloud trash. See the README.

## Install

```bash
pip install .            # then: porens --help  /  porens gui
```

Or download a standalone app binary from the assets below (no Python needed).

## Verification

All carvers and the FAT/exFAT recovery are tested byte-exact against real
encoder output and real filesystem images built by `mkfs.vfat`/`mtools` and
`mkfs.exfat`.
