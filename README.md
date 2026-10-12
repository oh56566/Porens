# Porens

**Android media recovery & PC-side forensic file carving.**

Porens recovers deleted photos and videos two ways — *filesystem-aware
recovery* (FAT/exFAT directory entries, which also restores the original
name, path, size and timestamps) and *structure-aware file carving* (walking
each format's real container to reconstruct the file close to its original
bytes, not just the first chunk). It works on a disk image, SD-card image,
raw device, or Android thumbnail store, produces a reviewable HTML gallery,
and runs on your own PC with only the Python standard library — no
third-party dependencies, so it works on a locked-down examiner's
workstation.

> Porens is for recovering data from **devices and media you own or are
> authorized to examine**. It performs no rooting, no encryption bypass, and
> no security circumvention. It only reads data the device or medium already
> exposes.

---

## Read this first: what is actually recoverable from a Galaxy S8

Be honest with yourself about the physics before you spend hours carving. The
S8 is a hard target, and *where the photos lived* decides everything.

| Vector | Realistic? | Why |
| --- | --- | --- |
| **microSD card** | ★★★★★ | Removable, usually unencrypted FAT/exFAT. Deleted JPEGs carve well. **Best case.** |
| **Gallery thumbnails** (`.thumbdata`) | ★★★★ | Low-res copies often survive after the original is deleted. Reachable over ADB even with `/data` encrypted. |
| **Cloud trash** (Google Photos / Samsung Cloud) | ★★★★ | Deleted items sit in a recycle bin (~60 days). Often the only route to full-resolution originals. |
| **App media** (messengers, `/sdcard/Android/media`) | ★★★ | Apps keep their own copies of photos you sent/received. |
| **Raw `/data` carving** | ★ | **File-Based Encryption + TRIM defeat this.** See below. |

### Why raw internal-storage recovery usually fails on the S8

1. **File-Based Encryption (FBE).** The S8's user partition is encrypted with
   keys bound to hardware (TEE/Knox) and your credentials. Deleted file content
   on flash is *ciphertext*; without the keys and file metadata it cannot be
   reconstructed.
2. **TRIM / flash garbage collection.** UFS/eMMC controllers physically erase
   discarded blocks shortly after deletion. Unlike an old spinning disk, the
   bytes are usually *gone*, not merely unlinked.
3. **Imaging needs root, and gaining root wipes the phone.** Unlocking the S8
   bootloader triggers a factory reset — destroying exactly the data you want.
   Porens will not do this, and you should not either if recovery is the goal.

**The takeaway is not "give up" — it's "attack the vectors that still hold
plaintext."** That means removable media, surviving thumbnails, app copies, and
the cloud. Porens is built to exploit precisely those gaps.

---

## Install

```bash
git clone <this-repo> Porens && cd Porens
pip install -e .          # provides the `porens` command
# or run without installing:
python -m porens --help
```

Requires Python 3.10+. No other dependencies. For ADB acquisition you also need
Android **platform-tools** (`adb`) on your PATH.

---

## Quick start

### 0. The short version

```bash
porens acquire --guide          # how to get the bytes off the device safely
porens info  sdcard.img         # read-only triage: what's here, what to run
porens recover sdcard.img -o recovered/   # do everything (fs + carve), one gallery
```

`recover` runs filesystem recovery and carving in one pass and writes a
`gallery.html` you can open. The steps below are the individual tools it
builds on.

### 1. Acquire (get the bytes onto your PC)

```bash
porens acquire --guide          # print the full acquisition playbook
porens acquire --pull ./pulled/ # ADB-pull photo folders + gallery thumbnails
```

For an SD card, image it with `dd` on the PC (the guide shows how to identify
the right device safely) — **never write to the source**.

Not sure what you're holding? `porens info <image>` reports the partition
table, any FAT/exFAT volumes, how many deleted directory entries survive,
and a quick signature sample — then recommends an approach. It only reads.

### 2. Recover deleted files from an SD card — try the filesystem first

```bash
porens fs sdcard.img -o recovered/     # FAT/exFAT deleted-entry recovery
porens fs /dev/sdX   -o recovered/      # raw device (read-only)
```

This reads the filesystem's own directory entries, so it restores each
deleted file's **original name, directory path, exact size and timestamps**
— e.g. `DCIM/Camera/Beach Sunset 2017.jpg`. It handles FAT12/16/32 (long
filenames rebuilt from LFN entries) and exFAT (contiguous `NoFatChain`
files recover exactly), walking subdirectories and MBR partitions.

### 3. Carve a disk / SD-card image (when there is no intact filesystem)

```bash
porens carve sdcard.img -o recovered/
porens carve /dev/sdX   -o recovered/          # raw device (read-only)
porens carve phone.img  -o recovered/ --types jpeg,heic
```

Run `fs` and `carve` both for best coverage: `fs` gets named files from
intact directories; `carve` finds files whose directory entry is already
gone.

### 4. Carve surviving thumbnails

```bash
porens thumbnails ./pulled/ -o recovered-thumbs/     # walks for .thumbdata*
porens thumbnails .thumbdata3--1967290299 -o out/    # a single store
```

### Output

Each run writes the recovered files plus `manifest.json`, `manifest.csv`,
and a **`gallery.html`** you can open to review results — a responsive,
light/dark gallery with thumbnails, confidence badges, EXIF date/camera, a
GPS map link, and filter/sort controls. Use `--embed` for a single portable
HTML file (media inlined as data URIs), or `--no-html` to skip it.

The manifest describes every file: source offset, size, reconstruction
confidence, truncation flag, SHA-256, original name/path (for `fs`), and
(for JPEGs) EXIF timestamp, camera make/model and GPS. Carved JPEGs are
named with their capture date when EXIF carries one, e.g.
`000000_jpeg_20170821_134502.jpg`.

---

## What makes the carving "close to the original"

A naive carver finds a start signature and stops at the first end signature.
That is wrong often enough to matter. Porens parses the actual container:

- **JPEG** — walks the marker stream segment by segment. Length-prefixed
  segments (including an EXIF-embedded thumbnail, which is itself a complete
  JPEG) are stepped over by length, so Porens returns the *outer* End-of-Image
  rather than truncating at the thumbnail. Entropy-coded scan data is read with
  correct byte-stuffing (`FF00`) and restart-marker (`FFD0`–`FFD7`) handling,
  and multi-scan **progressive** JPEGs are followed across every scan.
- **PNG** — walks the chunk list to `IEND` and verifies every chunk's CRC-32,
  so false positives are rejected and clean recoveries are flagged with high
  confidence.
- **HEIC / HEIF / MP4 / MOV / 3GP** — walks the ISO base-media box tree from
  `ftyp`, using box sizes to find the true extent; the `ftyp` brand chooses the
  extension.
- **GIF** — walks the block stream (extensions, image descriptors, data
  sub-blocks) to the `0x3B` trailer, so animations carve whole.
- **WebP** — uses the RIFF size field for an exact end.
- **TIFF / DNG** — follows IFD chains and strip/tile offsets to estimate the
  real extent of raw images.

Recovered files carry a **confidence** score (cleanly terminated vs. truncated)
and exact duplicates are collapsed by SHA-256.

---

## Forensic hygiene

- **Never write to the source.** Always carve a copy or image. `porens carve`
  opens sources strictly read-only.
- **Record hashes.** Keep the original image and note its SHA-256; the manifest
  records a hash for every carved file.
- **Stay in scope.** Only examine devices and media you own or are authorized
  to examine.

---

## How it fits together

```
porens/
  signatures.py   start-of-file signatures + one combined scan regex
  carve.py        mmap source, window-scan for signatures, dispatch carvers
  formats/        structure-aware carvers (jpeg, png, isobmff, gif/webp/tiff)
  filesystems/    FAT12/16/32 + exFAT deleted-entry recovery, MBR parsing
  exif_meta.py    dependency-free EXIF reader (date, camera, GPS)
  thumbnails.py   find & carve Android .thumbdata stores
  recover.py      carve / fs / recover_all -> write, dedupe, collect metadata
  triage.py       read-only `info`: partitions, filesystems, recommendation
  report.py       JSON + CSV manifests + HTML gallery
  acquire.py      ADB pull + dd imaging guidance (no rooting, no bypass)
  cli.py          `porens recover | fs | carve | thumbnails | info | acquire`
```

Run the tests with `pytest`. They build real (synthetic) images, scatter them
through junk, and assert byte-exact recovery — including the embedded-thumbnail
trap and false-positive rejection.

---

## Limitations (stated plainly)

- Cannot decrypt FBE/FDE-encrypted data. Ciphertext in, ciphertext out.
- Cannot recover blocks the flash controller has already TRIMmed/erased.
- Fragmented files (non-contiguous on disk) are recovered only up to the first
  fragment boundary; both carving and the deleted-entry recovery assume the
  file was stored contiguously (the usual case for write-once photos). A
  property of recovery from freed metadata, not a bug.
- A FAT file that had only a short (8.3) name loses its first character on
  deletion — the filesystem overwrites it with `0xE5`. Porens recovers the
  content exactly and marks the lost character with `_`. Files with a long
  filename recover their name in full.
- It will not root your phone or unlock your bootloader, by design.
