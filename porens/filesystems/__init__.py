"""Filesystem-aware recovery.

Where plain carving treats the medium as an undifferentiated byte stream,
these modules understand the on-disk filesystem. That buys two things that
carving cannot provide:

* the file's **real name, size and timestamps**, read from its (now
  deleted) directory entry, and
* an **exact** byte length, so the recovered file is neither truncated nor
  padded with the following file's data.

This is the high-value path for SD cards, which are normally FAT32 or
exFAT and unencrypted.
"""

from __future__ import annotations

__all__ = ["mbr", "fat", "exfat"]
