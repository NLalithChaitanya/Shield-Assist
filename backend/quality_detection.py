"""
backend/quality_detection.py

Real document quality detection using Laplacian variance.

Replaces the form-field-driven quality classification ("clear" / "degraded"
from a merchant-uploaded value) with a computed signal from the actual file.

Algorithm:
  1. Convert the document to a grayscale image (PDFs: render first page via
     PyMuPDF; images: read directly via Pillow).
  2. Apply a Laplacian kernel (edge detector).  Blur or low-resolution images
     produce a smooth, low-variance Laplacian response; sharp documents produce
     a high-variance response with many strong edges.
  3. Compute the variance of the Laplacian output.  This single scalar
     captures legibility: high variance = many sharp edges = clear text;
     low variance = few or smeared edges = blurry/low-quality scan.

Thresholds (defensible starting points, not magic numbers):
  - VARIANCE_THRESHOLD = 100: below this, the document is flagged "degraded".
    Computer-generated PDFs with text easily exceed 500+; blurry phone
    photos of receipts typically land between 10-80.
  - UNIFORMITY_UPPER_BOUND = 0.1: below this, the image is so uniform
    (solid color, near-zero texture) that Laplacian variance is
    meaningless.  Computer-generated white-background documents often
    fall here.  These are classified "clear" by default since uniformity
    implies intentional design, not blur.

Dependencies: Pillow (image I/O + Laplacian), PyMuPDF (PDF rendering).
Both are pure-Python wheels, no system-level install needed.

Usage:
    from backend.quality_detection import assess_document_quality
    status = assess_document_quality("/path/to/file.pdf", "application/pdf")
    # -> "clear" or "degraded"
"""

from __future__ import annotations

import logging
import statistics
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageFilter

logger = logging.getLogger("shield_assist.quality_detection")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
# Laplacian variance below this -> document is blurry / low-quality.
VARIANCE_THRESHOLD = 100

# Below this, the image is so uniform (solid/near-solid color) that
# Laplacian variance is not meaningful for blur detection.  Must be
# well below the variance of even heavily blurred text (~0.2).
UNIFORMITY_UPPER_BOUND = 0.1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _grayscale_pixels(image: Image.Image) -> list[int]:
    """Extract grayscale pixel values from a Pillow Image."""
    if image.mode != "L":
        image = image.convert("L")
    return list(image.getdata())


def _laplacian_variance(pixels: list[int], width: int, height: int) -> float:
    """Compute variance of a Laplacian-filtered image.

    Uses a 3x3 Laplacian kernel [[0,1,0],[1,-4,1],[0,1,0]] applied via
    Pillow's ImageFilter.Kernel.  Edge pixels (1px border) are excluded
    to avoid border artifacts.
    """
    img = Image.new("L", (width, height))
    img.putdata(pixels)

    kernel = [0, 1, 0, 1, -4, 1, 0, 1, 0]
    laplacian = img.filter(ImageFilter.Kernel(size=(3, 3), kernel=kernel, scale=1, offset=128))

    lap_data = list(laplacian.getdata())
    inner = []
    for y in range(1, height - 1):
        row_start = y * width
        for x in range(1, width - 1):
            inner.append(lap_data[row_start + x])

    if not inner:
        return 0.0

    mean = sum(inner) / len(inner)
    return sum((v - mean) ** 2 for v in inner) / len(inner)


def _assess_image(image: Image.Image) -> Tuple[str, float]:
    """Assess a Pillow Image and return (status, variance)."""
    width, height = image.size
    if width < 3 or height < 3:
        return ("clear", 0.0)

    pixels = _grayscale_pixels(image)
    variance = _laplacian_variance(pixels, width, height)

    if variance < UNIFORMITY_UPPER_BOUND:
        status = "clear"  # Very uniform image, almost always clean
    elif variance < VARIANCE_THRESHOLD:
        status = "degraded"
    else:
        status = "clear"

    return (status, round(variance, 2))


def _assess_pdf(file_path: Path) -> Tuple[str, float]:
    """Render the first page of a PDF and assess its quality."""
    try:
        import pymupdf as fitz  # PyMuPDF
    except ImportError:
        try:
            import fitz  # PyMuPDF (legacy API)
        except ImportError:
            logger.warning(
                "PyMuPDF not installed — cannot assess PDF quality. "
                "Install with: pip install PyMuPDF"
            )
            return ("unknown", 0.0)

    try:
        doc = fitz.open(str(file_path))
        if doc.page_count == 0:
            doc.close()
            return ("unknown", 0.0)

        page = doc[0]
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()

        return _assess_image(img)

    except Exception as exc:
        logger.warning("PDF quality assessment failed for %s: %s", file_path, exc)
        return ("unknown", 0.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assess_document_quality(file_path: str | Path, mime_type: str) -> Tuple[str, float]:
    """Assess the legibility/quality of a document file.

    Args:
        file_path: Path to the file on disk.
        mime_type: MIME type (e.g. "application/pdf", "image/png").

    Returns:
        (status, variance) where:
          - status: "clear" | "degraded" | "unknown"
          - variance: the raw Laplacian variance (float).
    """
    path = Path(file_path)

    if not path.exists():
        logger.warning("File not found for quality assessment: %s", path)
        return ("unknown", 0.0)

    if mime_type in ("image/jpeg", "image/png"):
        try:
            img = Image.open(path)
            return _assess_image(img)
        except Exception as exc:
            logger.warning("Image quality assessment failed for %s: %s", path, exc)
            return ("unknown", 0.0)

    if mime_type == "application/pdf":
        return _assess_pdf(path)

    logger.debug("Quality assessment not supported for mime_type=%s", mime_type)
    return ("unknown", 0.0)
