"""File-format signatures used to locate candidate files in raw data.

A *signature* is a short, fairly distinctive byte string that marks the
start of a file of a given type. Finding the signature is only the first
step: each format then has a structure-aware carver (see
``porens.formats``) that walks the real container to find the true end of
the file, instead of guessing. The signatures here are only the triggers.

We deliberately keep the signature list tight. A signature that is too
short (say a lone ``0xFFD8``) matches far too often and wastes the
carver's time on garbage; the ones below are chosen to be specific enough
to keep false positives manageable while still catching real files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signature:
    """A named start-of-file marker bound to a carver key.

    Attributes:
        kind: Short format key (e.g. ``"jpeg"``). Maps to a carver in
            ``porens.formats.CARVERS``.
        pattern: Raw bytes (or a regex-bytes pattern when ``is_regex``)
            that marks the start of the file.
        is_regex: When True, ``pattern`` is a regular-expression byte
            string; otherwise it is matched literally.
        description: Human-readable description for reports.
    """

    kind: str
    pattern: bytes
    is_regex: bool
    description: str


# Order matters only for reporting; the scanner matches all at once.
SIGNATURES: tuple[Signature, ...] = (
    # --- JPEG / JFIF / EXIF ------------------------------------------------
    # SOI (FFD8) followed by the first marker (FF Ex / FF DB / FF EE / FF C0).
    # Requiring the second marker byte cuts the false-positive rate hugely
    # versus matching a bare FFD8.
    Signature(
        kind="jpeg",
        pattern=rb"\xff\xd8\xff[\xe0-\xef\xdb\xee\xc0\xc4]",
        is_regex=True,
        description="JPEG image (JFIF/EXIF)",
    ),
    # --- PNG ---------------------------------------------------------------
    Signature(
        kind="png",
        pattern=b"\x89PNG\r\n\x1a\n",
        is_regex=False,
        description="PNG image",
    ),
    # --- GIF ---------------------------------------------------------------
    Signature(
        kind="gif",
        pattern=rb"GIF8[79]a",
        is_regex=True,
        description="GIF image",
    ),
    # --- ISO base media (HEIC/HEIF, MP4, MOV, 3GP) -------------------------
    # The ftyp box lives at offset 4, so we anchor on "....ftyp". The box
    # length prefix (first 4 bytes) is captured by backing up in the carver.
    Signature(
        kind="isobmff",
        pattern=rb"ftyp[a-zA-Z0-9 ]{4}",
        is_regex=True,
        description="ISO base media (HEIC/HEIF/MP4/MOV/3GP)",
    ),
    # --- WebP --------------------------------------------------------------
    Signature(
        kind="webp",
        pattern=rb"RIFF.{4}WEBP",
        is_regex=True,
        description="WebP image",
    ),
    # --- TIFF / DNG / common raw containers --------------------------------
    Signature(
        kind="tiff",
        pattern=rb"(?:II\x2a\x00|MM\x00\x2a)",
        is_regex=True,
        description="TIFF / DNG / raw image container",
    ),
)


def _compile_combined() -> re.Pattern[bytes]:
    """Build one alternation regex over all signatures.

    Using a single combined pattern lets us scan a (possibly huge) image in
    one pass. Named groups tie each hit back to its format key.
    """

    parts = []
    for i, sig in enumerate(SIGNATURES):
        body = sig.pattern if sig.is_regex else re.escape(sig.pattern)
        parts.append(b"(?P<g%d>%s)" % (i, body))
    return re.compile(b"|".join(parts), re.DOTALL)


COMBINED = _compile_combined()

# Map group index -> kind, and the ISOBMFF signature anchors at +4, so we
# record a per-signature start offset adjustment.
GROUP_KIND: dict[int, str] = {i: sig.kind for i, sig in enumerate(SIGNATURES)}
GROUP_ADJUST: dict[int, int] = {
    i: (-4 if sig.kind == "isobmff" else 0) for i, sig in enumerate(SIGNATURES)
}


def kind_for_match(match: re.Match[bytes]) -> tuple[str, int]:
    """Return ``(kind, start_offset)`` for a combined-regex match.

    ``start_offset`` is where the *file* begins, which for ISO base media is
    4 bytes before the matched ``ftyp`` (to include the box length prefix),
    clamped to zero.
    """

    gi = int(match.lastgroup[1:])  # strip the leading "g"
    kind = GROUP_KIND[gi]
    start = match.start() + GROUP_ADJUST[gi]
    return kind, max(start, 0)
