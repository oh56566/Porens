"""Optional decode-validation of recovered images.

Structure-aware carving tells us a file is *well-formed*; it cannot by
itself tell us the pixels are intact. A JPEG whose middle was overwritten
by a later file can still start with a valid header and end with ``FFD9``
yet fail to decode. When Pillow is available, ``--verify`` actually decodes
each recovered image and records whether it rendered — the honest answer to
"is this close to the original?".

Pillow is not a Porens dependency; without it, verification is skipped and
reported as unavailable rather than failing.
"""

from __future__ import annotations

_IMAGE_KINDS = {"jpeg", "png", "gif", "webp", "tiff"}


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def decode_check(path: str, kind: str) -> tuple[bool | None, str]:
    """Return ``(ok, detail)``.

    ``ok`` is True if the image decoded, False if it failed, or None when
    the check did not apply (non-image) or Pillow is missing.
    """

    if kind not in _IMAGE_KINDS:
        return None, "not a still image"
    try:
        from PIL import Image, ImageFile
    except Exception:
        return None, "Pillow not installed"

    # Insist on a complete image: truncated data must fail, not be padded.
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(path) as im:
            im.load()
            return True, f"{im.format} {im.width}x{im.height}"
    except Exception as e:  # noqa: BLE001 - any decode error means "not clean"
        return False, f"{type(e).__name__}: {e}"
