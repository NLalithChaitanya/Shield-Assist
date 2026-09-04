"""
tests/test_quality_detection.py

Tests for backend.quality_detection — real document quality detection
using Laplacian variance.

Verifies:
  - Real demo documents (PDFs) all score "clear" with high variance
  - Deliberately blurry images score "degraded" with low variance
  - Uniform white images score "clear" (uniformity bypass)
  - Edge cases: missing file, unsupported mime type
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageFilter, ImageDraw

from backend.quality_detection import (
    VARIANCE_THRESHOLD,
    UNIFORMITY_UPPER_BOUND,
    assess_document_quality,
    _laplacian_variance,
    _grayscale_pixels,
)

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent

# Real demo documents — all should be "clear"
DEMO_DOCS = [
    ("delivery_confirmation.pdf", "application/pdf"),
    ("customer_communication.pdf", "application/pdf"),
    ("terms_and_conditions.pdf", "application/pdf"),
    ("invoice.pdf", "application/pdf"),
]


class TestVarianceComputation:
    """Unit tests for the low-level Laplacian variance function."""

    def test_uniform_image_has_near_zero_variance(self):
        """A solid white image should have variance ~0."""
        pixels = [255] * (100 * 100)
        variance = _laplacian_variance(pixels, 100, 100)
        assert variance < 1.0, f"Expected near-zero variance, got {variance}"

    def test_text_image_has_high_variance(self):
        """An image with text edges should have significant variance."""
        img = Image.new("L", (200, 100), 255)
        draw = ImageDraw.Draw(img)
        draw.text((10, 40), "HELLO WORLD TEST", fill=0)
        pixels = _grayscale_pixels(img)
        variance = _laplacian_variance(pixels, 200, 100)
        assert variance > 50, f"Expected high variance for text image, got {variance}"

    def test_blurry_image_has_low_variance(self):
        """A blurred image should have lower variance than sharp."""
        sharp = Image.new("L", (200, 100), 255)
        draw = ImageDraw.Draw(sharp)
        for y in range(10, 90, 8):
            draw.text((10, y), "Line of text content here", fill=0)
        sharp_px = _grayscale_pixels(sharp)
        sharp_var = _laplacian_variance(sharp_px, 200, 100)

        blurry = sharp.filter(ImageFilter.GaussianBlur(radius=8))
        blurry_px = _grayscale_pixels(blurry)
        blurry_var = _laplacian_variance(blurry_px, 200, 100)

        assert sharp_var > blurry_var, (
            f"Sharp variance ({sharp_var}) should exceed blurry ({blurry_var})"
        )


class TestThresholds:
    """Verify threshold constants are reasonable and documented."""

    def test_variance_threshold_is_positive(self):
        assert VARIANCE_THRESHOLD > 0

    def test_uniformity_bound_is_below_variance_threshold(self):
        assert UNIFORMITY_UPPER_BOUND < VARIANCE_THRESHOLD

    def test_variance_threshold_is_defensible(self):
        """Threshold should separate real blurry photos (~10-80) from
        clean text documents (~500+)."""
        # This is a sanity check, not a hard assertion — the threshold
        # is a judgment call, but it should be in a reasonable range.
        assert 10 <= VARIANCE_THRESHOLD <= 500, (
            f"VARIANCE_THRESHOLD={VARIANCE_THRESHOLD} seems unusual. "
            "Revisit if blurry photos score above this or clear docs below."
        )


class TestRealDemoDocuments:
    """Integration tests against the actual demo PDF files."""

    @pytest.mark.parametrize("filename,mime_type", DEMO_DOCS)
    def test_demo_pdf_scores_clear(self, filename, mime_type):
        """All demo documents should score 'clear' — they are clean,
        computer-generated PDFs."""
        path = PROJECT_DIR / filename
        if not path.exists():
            pytest.skip(f"{filename} not found in project root")

        status, variance = assess_document_quality(str(path), mime_type)
        assert status == "clear", (
            f"{filename} should be 'clear', got '{status}' (variance={variance})"
        )
        assert variance > VARIANCE_THRESHOLD, (
            f"{filename} variance {variance} should exceed threshold {VARIANCE_THRESHOLD}"
        )


class TestSyntheticImages:
    """Tests using programmatically generated images."""

    def test_sharp_detailed_image_is_clear(self):
        """A detailed, sharp image should score 'clear'."""
        img = Image.new("L", (400, 300), 255)
        draw = ImageDraw.Draw(img)
        # Dense text-like content
        for y in range(20, 280, 10):
            for x in range(20, 380, 6):
                draw.rectangle([x, y, x + 4, y + 7], fill=0)
        # Lines and borders
        draw.rectangle([10, 10, 390, 290], outline=0, width=2)
        draw.line([10, 100, 390, 100], fill=0, width=1)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        img.save(tmp_path)
        try:
            status, variance = assess_document_quality(tmp_path, "image/png")
            assert status == "clear"
            assert variance > VARIANCE_THRESHOLD
        finally:
            os.unlink(tmp_path)

    def test_heavily_blurry_image_is_degraded(self):
        """A heavily blurred image should score 'degraded'."""
        sharp = Image.new("L", (400, 300), 255)
        draw = ImageDraw.Draw(sharp)
        for y in range(20, 280, 8):
            for x in range(20, 380, 5):
                draw.rectangle([x, y, x + 3, y + 6], fill=0)
        draw.rectangle([10, 10, 390, 290], outline=0, width=2)

        blurry = sharp.filter(ImageFilter.GaussianBlur(radius=10))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        blurry.save(tmp_path)
        try:
            status, variance = assess_document_quality(tmp_path, "image/png")
            assert status == "degraded", (
                f"Expected 'degraded' for blurry image, got '{status}' (var={variance})"
            )
            assert variance < VARIANCE_THRESHOLD
        finally:
            os.unlink(tmp_path)

    def test_uniform_white_image_is_clear(self):
        """A solid white image should be 'clear' (uniformity bypass)."""
        img = Image.new("L", (400, 300), 255)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        img.save(tmp_path)
        try:
            status, variance = assess_document_quality(tmp_path, "image/png")
            assert status == "clear"
            assert variance < UNIFORMITY_UPPER_BOUND
        finally:
            os.unlink(tmp_path)

    def test_low_resolution_upscaled_is_degraded(self):
        """A tiny image upscaled should lose detail and score 'degraded'."""
        sharp = Image.new("L", (400, 300), 255)
        draw = ImageDraw.Draw(sharp)
        for y in range(20, 280, 8):
            for x in range(20, 380, 5):
                draw.rectangle([x, y, x + 3, y + 6], fill=0)

        low_res = sharp.resize((50, 37), Image.BILINEAR)
        up = low_res.resize((400, 300), Image.BILINEAR)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        up.save(tmp_path)
        try:
            status, variance = assess_document_quality(tmp_path, "image/png")
            assert status == "degraded", (
                f"Expected 'degraded' for low-res, got '{status}' (var={variance})"
            )
        finally:
            os.unlink(tmp_path)


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_missing_file_returns_unknown(self):
        status, variance = assess_document_quality("/nonexistent/file.pdf", "application/pdf")
        assert status == "unknown"
        assert variance == 0.0

    def test_unsupported_mime_type_returns_unknown(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp_path = tmp.name
        with open(tmp_path, 'wb') as f:
            f.write(b"hello world")
        try:
            status, variance = assess_document_quality(tmp_path, "text/plain")
            assert status == "unknown"
        finally:
            os.unlink(tmp_path)


class TestMerchantOverride:
    """The quality detection module doesn't handle override logic (that's
    in app.py), but we verify the raw detection works independently."""

    def test_detection_returns_tuple(self):
        """assess_document_quality always returns (status, variance)."""
        path = PROJECT_DIR / "delivery_confirmation.pdf"
        if not path.exists():
            pytest.skip("delivery_confirmation.pdf not found")

        result = assess_document_quality(str(path), "application/pdf")
        assert isinstance(result, tuple)
        assert len(result) == 2
        status, variance = result
        assert status in ("clear", "degraded", "unknown")
        assert isinstance(variance, float)
