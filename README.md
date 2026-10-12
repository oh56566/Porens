# Porens

**Android media recovery & PC-side forensic file carving.**

Porens recovers deleted photos and videos by *structure-aware file carving*:
it scans a disk image, SD-card image, raw device, or Android thumbnail store
for file signatures, then walks each file format's real container to
reconstruct the file close to its original bytes — not just the first chunk.
It runs on your own PC with only the Python standard library, so it works on a
locked-down examiner's workstation with no third-party dependencies.

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

### 1. Acquire (get the bytes onto your PC)

```bash
porens acquire --guide          # print the full acquisition playbook
porens acquire --pull ./pulled/ # ADB-pull photo folders + gallery thumbnails
```

For an SD card, image it with `dd` on the PC (the guide shows how to identify
the right device safely) — **never write to the source**.

### 2. Carve a disk / SD-card image

```bash
porens carve sdcard.img -o recovered/
porens carve /dev/sdX   -o recovered/          # raw device (read-only)
porens carve phone.img  -o recovered/ --types jpeg,heic
```

### 3. Carve surviving thumbnails

```bash
porens thumbnails ./pulled/ -o recovered-thumbs/     # walks for .thumbdata*
porens thumbnails .thumbdata3--1967290299 -o out/    # a single store
```

Each run writes the recovered files plus `manifest.json` and `manifest.csv`
describing every file: source offset, size, reconstruction confidence,
truncation flag, SHA-256, and (for JPEGs) EXIF timestamp, camera make/model,
and GPS. JPEGs are named with their capture date when EXIF carries one, e.g.
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
  exif_meta.py    dependency-free EXIF reader (date, camera, GPS)
  thumbnails.py   find & carve Android .thumbdata stores
  recover.py      carve -> write files -> dedupe -> collect metadata
  report.py       JSON + CSV manifests
  acquire.py      ADB pull + dd imaging guidance (no rooting, no bypass)
  cli.py          `porens carve | thumbnails | acquire`
```

Run the tests with `pytest`. They build real (synthetic) images, scatter them
through junk, and assert byte-exact recovery — including the embedded-thumbnail
trap and false-positive rejection.

---

## Limitations (stated plainly)

- Cannot decrypt FBE/FDE-encrypted data. Ciphertext in, ciphertext out.
- Cannot recover blocks the flash controller has already TRIMmed/erased.
- Fragmented files (non-contiguous on disk) are recovered only up to the first
  fragment boundary; signature-carving assumes contiguity. This is a property
  of carving in general, not a bug.
- It will not root your phone or unlock your bootloader, by design.
