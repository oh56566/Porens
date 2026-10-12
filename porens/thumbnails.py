"""Recover thumbnails from Android ``.thumbdata`` databases.

Android's gallery stores thumbnails in files named like
``DCIM/.thumbnails/.thumbdata3--1967290299`` (and ``.thumbdata4--*`` /
``.thumbdata5--*`` and ``.thumbcache_*``). Structurally these are little
more than concatenated JPEG (sometimes PNG/WebP) thumbnails with a small
index. When an original photo has been deleted, its thumbnail often still
lives here — a lower-resolution copy, but frequently the *only* surviving
image of the moment.

This matters for a device like the Galaxy S8: because ``/data`` is
file-based encrypted, you cannot usefully carve the raw partition, but you
*can* ``adb pull`` the ``.thumbnails`` directory from the unlocked phone
(where the files are transparently decrypted on read) and then carve the
thumbnails here on the PC. See :mod:`porens.acquire`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator

from .carve import carve_source, open_source
from .formats import CarveResult

# Image formats a thumbnail store may hold.
THUMB_KINDS = {"jpeg", "png", "webp"}

# Names that look like Android thumbnail stores.
_THUMB_PREFIXES = (".thumbdata", ".thumbcache", "thumbdata")


@dataclass
class ThumbSource:
    path: str
    size: int


def find_thumb_stores(root: str) -> list[ThumbSource]:
    """Walk ``root`` (e.g. an ``adb pull`` of the phone) for thumb stores."""

    found: list[ThumbSource] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            low = name.lower()
            if low.startswith(_THUMB_PREFIXES):
                full = os.path.join(dirpath, name)
                try:
                    found.append(ThumbSource(full, os.path.getsize(full)))
                except OSError:
                    continue
    found.sort(key=lambda t: t.size, reverse=True)
    return found


def carve_thumbnails(path: str, min_size: int = 256) -> Iterator[CarveResult]:
    """Carve every embedded thumbnail out of one ``.thumbdata`` file.

    Thumbnails are small, so ``min_size`` is lower than the default carving
    threshold. Results are tagged in their notes as thumbnail-derived.
    """

    with open_source(path) as src:
        for res in carve_source(src, kinds=THUMB_KINDS, min_size=min_size):
            res.notes.append(f"from thumbnail store {os.path.basename(path)}")
            yield res
