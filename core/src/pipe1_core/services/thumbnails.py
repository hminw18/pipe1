"""Downscaled JPEG copies of capture images for embedding in Excel and PDF reports.

Captures are stored as full-resolution PNGs; reports display them at a few centimetres, so
embedding the originals made workbooks and PDFs needlessly large (docs/Web_Architecture.md §6).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

EXCEL_MAX_PIXELS = 480
PDF_MAX_PIXELS = 1280
JPEG_QUALITY = 82


def thumbnail_jpeg(path: str | Path, max_pixels: int) -> BytesIO:
    """Return a JPEG no larger than max_pixels on its longest side."""
    with Image.open(path) as source:
        image = source.convert("RGB")
    image.thumbnail((max_pixels, max_pixels), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    buffer.seek(0)
    return buffer


def thumbnail_or_original(path: str | Path, max_pixels: int) -> BytesIO | str:
    """The thumbnail, or the original path if the image cannot be decoded, so one damaged
    capture never breaks a whole export."""
    try:
        return thumbnail_jpeg(path, max_pixels)
    except (OSError, ValueError):
        return str(path)
