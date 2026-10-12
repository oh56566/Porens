"""Registry mapping signature kinds to structure-aware carvers.

The scanner finds a signature, looks up the carver by kind here, and calls
it to determine the true file boundaries.
"""

from __future__ import annotations

from .base import Buffer, CarveResult, Carver
from . import jpeg, png, isobmff, misc

CARVERS: dict[str, Carver] = {
    "jpeg": jpeg.carve,
    "png": png.carve,
    "isobmff": isobmff.carve,
    "gif": misc.carve_gif,
    "webp": misc.carve_webp,
    "tiff": misc.carve_tiff,
}

__all__ = ["CARVERS", "Buffer", "CarveResult", "Carver"]
