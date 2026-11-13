"""Test Suite for Layout Complexity Evaluator & Tesseract OCR Routing (Task 4B.3 / Issue #203).

Validates:
1. evaluate_page_complexity on sparse text pages (< 50 characters).
2. evaluate_page_complexity on raster-heavy pages (raster area ratio > 0.40).
3. evaluate_page_complexity on clean, dense vector text pages (is_complex=False).
4. evaluate_page_complexity on unmapped font glyphs (CIDs and replacement characters).
5. calculate_page_raster_ratio affine CTM determinant tracking without pixel decoding.
6. OCR routing logic across modes ('off', 'auto', 'force'):
   - 'off': skips OCR unconditionally, setting ocr_skipped=True.
   - 'auto': graceful fallback when Tesseract is absent (logs warning, records ocr_skipped=True).
   - 'force': hard failure when Tesseract is absent, raising typed OCRUnavailableError
     (exit 1, HTTP 501).
   - 'auto' / 'force': routes to OCR when Tesseract is available.
7. End-to-end integration with LiteParseDocumentAdapter and PageMapEntry metadata.
8. End-to-end integration with IngestionService and evaluate_layout_complexity hook.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pypdf
import pytest

from opendv_sources.adapters.liteparse_adapter import LiteParseDocumentAdapter
from opendv_sources.exceptions import (
    DocumentExtractionError,
    OCRExecutionError,
    OCRUnavailableError,
)
from opendv_sources.ingestion.layout_evaluator import (
    LayoutComplexityResult,
    OCRRouteDecision,
    TesseractOCRRouter,
    calculate_page_raster_ratio,
    count_unmapped_glyphs,
    evaluate_page_complexity,
    resolve_tesseract_cmd,
)
from opendv_sources.ingestion.service import IngestionService

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MSA_PDF_PATH = FIXTURES_DIR / "enterprise_msa_addendum_2026.pdf"


def _create_test_pdf_bytes(
    page_texts: list[str],
    *,
    width: float = 612.0,
    height: float = 792.0,
    image_cm: list[float] | None = None,
    with_image_xobject: bool = False,
) -> bytes:
    """Generate a multi-page PDF with optional affine-transformed image XObject."""
    writer = pypdf.PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=width, height=height)

        # Build content stream commands
        stream_parts: list[str] = []
        if text:
            safe_lines = [
                line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                for line in text.split("\n")
            ]
            stream_parts.extend(["BT", "/Helvetica 12 Tf", "50 720 Td"])
            for idx, line in enumerate(safe_lines):
                if idx > 0:
                    stream_parts.append("0 -18 Td")
                stream_parts.append(f"({line}) Tj")
            stream_parts.append("ET")

        if image_cm and with_image_xobject:
            # Place image via graphics state and CTM concat
            a, b, c, d, e, f = image_cm
            stream_parts.append(f"q {a} {b} {c} {d} {e} {f} cm /Im1 Do Q")

        content_stream = pypdf.generic.DecodedStreamObject()
        content_stream.set_data("\n".join(stream_parts).encode("latin-1", errors="replace"))
        page[pypdf.generic.NameObject("/Contents")] = writer._add_object(content_stream)

        # Add font and image resources
        font_dict = pypdf.generic.DictionaryObject(
            {
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Font"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Type1"),
                pypdf.generic.NameObject("/BaseFont"): pypdf.generic.NameObject("/Helvetica"),
            }
        )
        resources_dict: dict[Any, Any] = {
            pypdf.generic.NameObject("/Font"): pypdf.generic.DictionaryObject(
                {pypdf.generic.NameObject("/Helvetica"): writer._add_object(font_dict)}
            )
        }

        if with_image_xobject:
            img_stream = pypdf.generic.DecodedStreamObject()
            img_stream.set_data(b"\x00" * 100)
            img_stream.update(
                {
                    pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/XObject"),
                    pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Image"),
                    pypdf.generic.NameObject("/Width"): pypdf.generic.NumberObject(100),
                    pypdf.generic.NameObject("/Height"): pypdf.generic.NumberObject(100),
                    pypdf.generic.NameObject("/ColorSpace"): pypdf.generic.NameObject(
                        "/DeviceGray"
                    ),
                    pypdf.generic.NameObject("/BitsPerComponent"): pypdf.generic.NumberObject(8),
                }
            )
            resources_dict[pypdf.generic.NameObject("/XObject")] = pypdf.generic.DictionaryObject(
                {pypdf.generic.NameObject("/Im1"): writer._add_object(img_stream)}
            )

        page[pypdf.generic.NameObject("/Resources")] = pypdf.generic.DictionaryObject(
            resources_dict
        )

    import io

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# =============================================================================
# 1. Layout Complexity Evaluator Unit Tests
# =============================================================================


class TestLayoutComplexityEvaluator:
    """Tests evaluating page layout complexity (character count, raster ratio, unmapped glyphs)."""

    def test_sparse_text_page_below_threshold_is_complex(self) -> None:
        """Verify character density < 50 triggers complexity with low_character_density reason."""
        sparse_text = "Short note: approval pending."
        assert len(sparse_text.strip()) < 50

        result = evaluate_page_complexity(text=sparse_text)
        assert isinstance(result, LayoutComplexityResult)
        assert result.is_complex is True
        assert result.char_count == len(sparse_text.strip())
        assert result.raster_ratio == 0.0
        assert result.unmapped_glyphs == 0
        assert any("low_character_density" in r for r in result.reasons)

    def test_empty_page_is_complex(self) -> None:
        """Verify completely empty page (0 characters) is flagged complex."""
        result = evaluate_page_complexity(text="")
        assert result.is_complex is True
        assert result.char_count == 0
        assert any("low_character_density" in r for r in result.reasons)

    def test_sparse_text_boundary_exact_50_chars(self) -> None:
        """Verify exact 50 characters is not flagged sparse (boundary condition)."""
        exact_50_chars = "1234567890" * 5
        assert len(exact_50_chars) == 50

        result = evaluate_page_complexity(text=exact_50_chars)
        assert result.char_count == 50
        assert result.is_complex is False
        assert result.reasons == []

    def test_sparse_text_boundary_49_chars(self) -> None:
        """Verify 49 characters is flagged sparse (< 50)."""
        text_49_chars = "a" * 49
        result = evaluate_page_complexity(text=text_49_chars)
        assert result.char_count == 49
        assert result.is_complex is True
        assert any("low_character_density (49 < 50)" in r for r in result.reasons)

    def test_dense_vector_text_page_is_not_complex(self) -> None:
        """Verify clean vector text page (> 50 chars, no raster, no unmapped) is clean."""

        dense_text = (
            "This Enterprise Master Services Agreement governs the provision of software "
            "development, data architecture, and verification services. All intellectual property "
            "developed under this Statement of Work shall vest exclusively in the Client upon full "
            "and final payment of milestone fees."
        )
        assert len(dense_text.strip()) > 50

        result = evaluate_page_complexity(
            text=dense_text,
            raster_ratio=0.05,
            unmapped_glyphs=0,
        )
        assert result.is_complex is False
        assert result.raster_ratio == 0.05
        assert result.char_count == len(dense_text.strip())
        assert result.unmapped_glyphs == 0
        assert result.reasons == []

    def test_raster_heavy_page_exceeding_threshold_is_complex(self) -> None:
        """Verify raster area ratio > 0.40 triggers complexity even on dense text."""
        dense_text = "A" * 200
        result = evaluate_page_complexity(text=dense_text, raster_ratio=0.55)

        assert result.is_complex is True
        assert result.raster_ratio == 0.55
        assert any("high_raster_ratio" in r for r in result.reasons)

    def test_raster_ratio_boundary_exact_0_40(self) -> None:
        """Verify exact 0.40 raster ratio is not flagged complex (boundary condition > 0.40)."""
        dense_text = "A" * 200
        result = evaluate_page_complexity(text=dense_text, raster_ratio=0.40)
        assert result.is_complex is False
        assert result.reasons == []

    def test_raster_ratio_boundary_above_0_40(self) -> None:
        """Verify 0.4001 raster ratio is flagged complex (> 0.40)."""
        dense_text = "A" * 200
        result = evaluate_page_complexity(text=dense_text, raster_ratio=0.4001)
        assert result.is_complex is True
        assert any("high_raster_ratio" in r for r in result.reasons)

    def test_unmapped_cid_glyphs_trigger_complexity(self) -> None:
        """Verify unmapped CID font references like (cid:123) trigger complexity."""
        text_with_cids = (
            "Standard paragraph text with unmapped glyphs: (cid:45) and (cid:128) "
            "which happens when a PDF font lacks a /ToUnicode CMap mapping."
        )
        assert len(text_with_cids) > 50

        result = evaluate_page_complexity(text=text_with_cids)
        assert result.is_complex is True
        assert result.unmapped_glyphs == 2
        assert any("unmapped_glyphs (2)" in r for r in result.reasons)

    def test_unicode_replacement_char_triggers_complexity(self) -> None:
        """Verify Unicode replacement characters (U+FFFD) trigger unmapped glyph complexity."""
        text_with_replacement = "Character \ufffd and \ufffd were corrupted during font rendering."
        result = evaluate_page_complexity(
            text=text_with_replacement,
            char_count=100,  # Force char_count > 50
        )
        assert result.is_complex is True
        assert result.unmapped_glyphs == 2
        assert any("unmapped_glyphs (2)" in r for r in result.reasons)

    def test_count_unmapped_glyphs_helper(self) -> None:
        """Verify count_unmapped_glyphs counts CIDs, replacement chars, and PUA characters."""
        assert count_unmapped_glyphs("") == 0
        assert count_unmapped_glyphs(None) == 0
        assert count_unmapped_glyphs("Normal text") == 0
        assert count_unmapped_glyphs("(cid:1) (cid:2) (cid:999)") == 3
        assert count_unmapped_glyphs("Bad \ufffd char") == 1
        assert count_unmapped_glyphs("PUA \ue000 char") == 1
        assert count_unmapped_glyphs("(cid:10) \ufffd \ue005") == 3

    def test_dict_input_to_evaluate_page_complexity(self) -> None:
        """Verify dictionary input conforming to metrics format is evaluated correctly."""
        metrics_dict = {
            "text": "A" * 100,
            "char_count": 100,
            "raster_ratio": 0.65,
            "unmapped_glyphs": 0,
        }
        result = evaluate_page_complexity(metrics_dict)
        assert result.is_complex is True
        assert result.raster_ratio == 0.65
        assert result.char_count == 100
        assert result.to_dict()["is_complex"] is True

    def test_zero_dimensions_graceful_handling(self) -> None:
        """Verify zero or negative page dimensions return 0.0 raster ratio without error."""
        ratio = calculate_page_raster_ratio(None, page_width=0.0, page_height=0.0)
        assert ratio == 0.0

        ratio_neg = calculate_page_raster_ratio(None, page_width=-100.0, page_height=-100.0)
        assert ratio_neg == 0.0


# =============================================================================
# 2. Raster Ratio Calculation via Affine Tracking Tests
# =============================================================================


class TestCalculatePageRasterRatio:
    """Tests calculating raster ratio via affine CTM tracking without pixel decoding."""

    def test_raster_ratio_on_synthetic_large_image(self) -> None:
        """Verify PDF with image scaled to 70% of page area computes raster_ratio > 0.40."""
        page_w, page_h = 612.0, 792.0
        # Image covering 500 x 600 points -> 300,000 / 484,704 ≈ 0.619
        pdf_bytes = _create_test_pdf_bytes(
            ["Sparse"],
            width=page_w,
            height=page_h,
            image_cm=[500.0, 0.0, 0.0, 600.0, 50.0, 50.0],
            with_image_xobject=True,
        )
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page = reader.pages[0]

        ratio = calculate_page_raster_ratio(page)
        assert ratio > 0.40
        assert 0.60 <= ratio <= 0.65

        complexity = evaluate_page_complexity(page)
        assert complexity.is_complex is True
        assert complexity.raster_ratio == ratio

    def test_raster_ratio_on_small_logo(self) -> None:
        """Verify PDF with small logo (e.g. 50 x 50 points) computes raster_ratio < 0.40."""
        page_w, page_h = 612.0, 792.0
        # Logo covering 50 x 50 points -> 2,500 / 484,704 ≈ 0.005
        pdf_bytes = _create_test_pdf_bytes(
            ["This is a legal document with plenty of valid text characters to exceed 50."],
            width=page_w,
            height=page_h,
            image_cm=[50.0, 0.0, 0.0, 50.0, 500.0, 700.0],
            with_image_xobject=True,
        )
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

        page = reader.pages[0]

        ratio = calculate_page_raster_ratio(page)
        assert ratio < 0.40
        assert ratio < 0.02

    def test_shared_resources_unused_image_xobject_has_zero_raster_ratio(self) -> None:
        """Regression P03b::MAJ-01: Shared /Resources image not drawn via Do yields 0.0 ratio."""
        writer = pypdf.PdfWriter()

        # Shared 300-DPI US-Letter image XObject (2550 x 3300 pixels)
        img_stream = pypdf.generic.DecodedStreamObject()
        img_stream.set_data(b"\x00" * 128)
        img_stream.update(
            {
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/XObject"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Image"),
                pypdf.generic.NameObject("/Width"): pypdf.generic.NumberObject(2550),
                pypdf.generic.NameObject("/Height"): pypdf.generic.NumberObject(3300),
                pypdf.generic.NameObject("/ColorSpace"): pypdf.generic.NameObject("/DeviceGray"),
                pypdf.generic.NameObject("/BitsPerComponent"): pypdf.generic.NumberObject(8),
            }
        )
        font_dict = pypdf.generic.DictionaryObject(
            {
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Font"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Type1"),
                pypdf.generic.NameObject("/BaseFont"): pypdf.generic.NameObject("/Helvetica"),
            }
        )
        shared_resources = writer._add_object(
            pypdf.generic.DictionaryObject(
                {
                    pypdf.generic.NameObject("/Font"): pypdf.generic.DictionaryObject(
                        {pypdf.generic.NameObject("/Helvetica"): writer._add_object(font_dict)}
                    ),
                    pypdf.generic.NameObject("/XObject"): pypdf.generic.DictionaryObject(
                        {pypdf.generic.NameObject("/ImShared"): writer._add_object(img_stream)}
                    ),
                }
            )
        )

        # Page 1 draws /ImShared across most of the page
        page1 = writer.add_blank_page(width=612.0, height=792.0)
        cs1 = pypdf.generic.DecodedStreamObject()
        cs1.set_data(b"q 500 0 0 600 50 50 cm /ImShared Do Q")
        page1[pypdf.generic.NameObject("/Contents")] = writer._add_object(cs1)
        page1[pypdf.generic.NameObject("/Resources")] = shared_resources

        # Page 2 inherits shared /Resources but draws ONLY vector text (no Do operator!)
        page2 = writer.add_blank_page(width=612.0, height=792.0)
        dense_line = (
            "Pure vector legal text on page two exceeding fifty characters with no raster images."
        )
        cs2 = pypdf.generic.DecodedStreamObject()
        cs2.set_data(f"BT\n/Helvetica 12 Tf\n50 720 Td\n({dense_line}) Tj\nET".encode("latin-1"))
        page2[pypdf.generic.NameObject("/Contents")] = writer._add_object(cs2)
        page2[pypdf.generic.NameObject("/Resources")] = shared_resources

        buf = io.BytesIO()
        writer.write(buf)
        reader = pypdf.PdfReader(io.BytesIO(buf.getvalue()))

        assert calculate_page_raster_ratio(reader.pages[0]) > 0.40
        assert calculate_page_raster_ratio(reader.pages[1]) == 0.0
        p2_complexity = evaluate_page_complexity(reader.pages[1], text=dense_line)
        assert p2_complexity.raster_ratio == 0.0
        assert p2_complexity.is_complex is False

    def test_broken_content_stream_does_not_divide_pixels_by_points(self) -> None:
        """Regression P03b::MAJ-01: Never divide raw pixel dimensions by PDF point area."""
        pdf_bytes = _create_test_pdf_bytes(
            ["Dense vector text exceeding fifty characters on a standard page."],
            width=612.0,
            height=792.0,
            with_image_xobject=True,
        )
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page = reader.pages[0]

        with patch("pypdf.generic.ContentStream", side_effect=RuntimeError("corrupt stream")):
            assert calculate_page_raster_ratio(page) == 0.0


# =============================================================================
# 3. Tesseract OCR Router Logic Tests
# =============================================================================


class TestTesseractOCRRouter:
    """Tests OCR routing logic across modes ('off', 'auto', 'force') and availability."""

    def test_invalid_ocr_mode_raises_value_error(self) -> None:
        """Verify constructor rejects unrecognized ocr_mode string."""
        with pytest.raises(ValueError, match="Invalid ocr_mode 'invalid'"):
            TesseractOCRRouter(ocr_mode="invalid")

    def test_non_complex_page_always_routes_to_native(self) -> None:
        """Verify clean non-complex pages route to native (and force checks engine availability)."""
        complexity = LayoutComplexityResult(
            is_complex=False,
            raster_ratio=0.02,
            char_count=500,
            unmapped_glyphs=0,
            reasons=[],
        )
        for mode in ("off", "auto"):
            router = TesseractOCRRouter(ocr_mode=mode)
            router.set_availability_override(False)
            decision = router.route_page(complexity, page_number=1)

            assert isinstance(decision, OCRRouteDecision)
            assert decision.should_ocr is False
            assert decision.ocr_skipped is False
            assert decision.action == "native"
            assert decision.confidence == 1.0

        # In 'force' mode, clean pages route to native when OCR engine is available,
        # and fail fast with OCRUnavailableError if Tesseract is missing (040b4599::MAJ-02).
        force_available = TesseractOCRRouter(ocr_mode="force")
        force_available.set_availability_override(True)
        force_decision = force_available.route_page(complexity, page_number=1)
        assert force_decision.should_ocr is False
        assert force_decision.ocr_skipped is False
        assert force_decision.action == "native"
        assert force_decision.confidence == 1.0

        force_missing = TesseractOCRRouter(ocr_mode="force")
        force_missing.set_availability_override(False)
        with pytest.raises(OCRUnavailableError) as exc_info:
            force_missing.route_page(complexity, page_number=1)
        assert exc_info.value.http_status == 501
        assert exc_info.value.exit_code == 1

    def test_ocr_mode_off_skips_ocr_unconditionally(self) -> None:
        """Verify ocr_mode='off' skips OCR on complex pages with ocr_skipped=True and conf=0.8."""
        complexity = LayoutComplexityResult(
            is_complex=True,
            raster_ratio=0.75,
            char_count=10,
            unmapped_glyphs=0,
            reasons=["low_character_density", "high_raster_ratio"],
        )
        router = TesseractOCRRouter(ocr_mode="off")
        decision = router.route_page(complexity, page_number=1)

        assert decision.should_ocr is False
        assert decision.ocr_skipped is True
        assert decision.action == "skipped"
        assert decision.confidence == 0.8
        assert "ocr_mode is 'off'" in decision.reason

    def test_ocr_mode_auto_when_tesseract_absent_degrades_gracefully(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify ocr_mode='auto' logs warning, sets ocr_skipped=True, and does not abort."""
        complexity = LayoutComplexityResult(
            is_complex=True,
            raster_ratio=0.80,
            char_count=5,
            unmapped_glyphs=0,
            reasons=["low_character_density", "high_raster_ratio"],
        )
        router = TesseractOCRRouter(ocr_mode="auto")
        router.set_availability_override(False)

        with caplog.at_level(logging.WARNING):
            decision = router.route_page(complexity, page_number=3)

        assert decision.should_ocr is False
        assert decision.ocr_skipped is True
        assert decision.action == "fallback"
        assert decision.confidence == 0.8
        assert "Tesseract OCR unavailable for complex page 3" in caplog.text

    def test_ocr_mode_auto_when_tesseract_available_routes_to_ocr(self) -> None:
        """Verify ocr_mode='auto' routes to OCR when engine is present."""
        complexity = LayoutComplexityResult(
            is_complex=True,
            raster_ratio=0.80,
            char_count=5,
            unmapped_glyphs=0,
            reasons=["low_character_density"],
        )
        router = TesseractOCRRouter(ocr_mode="auto")
        router.set_availability_override(True)

        decision = router.route_page(complexity, page_number=1)
        assert decision.should_ocr is True
        assert decision.ocr_skipped is False
        assert decision.action == "ocr"
        assert decision.confidence == 1.0

    def test_ocr_mode_force_when_tesseract_absent_raises_ocr_unavailable(self) -> None:
        """Verify ocr_mode='force' raises typed OCRUnavailableError (exit 1 / 501) when absent."""

        complexity = LayoutComplexityResult(
            is_complex=True,
            raster_ratio=0.85,
            char_count=2,
            unmapped_glyphs=0,
            reasons=["low_character_density", "high_raster_ratio"],
        )
        router = TesseractOCRRouter(ocr_mode="force")
        router.set_availability_override(False)

        with pytest.raises(OCRUnavailableError) as exc_info:
            router.route_page(complexity, page_number=2)

        err = exc_info.value
        assert isinstance(err, DocumentExtractionError)
        assert err.error_code == "OCR_UNAVAILABLE"
        assert err.exit_code == 1
        assert err.http_status == 501
        assert "OCR_UNAVAILABLE" in str(err)
        assert err.details.get("page_number") == 2

    def test_ocr_mode_force_when_tesseract_available_routes_to_ocr(self) -> None:
        """Verify ocr_mode='force' routes to OCR when engine is present."""
        complexity = LayoutComplexityResult(
            is_complex=True,
            raster_ratio=0.85,
            char_count=2,
            unmapped_glyphs=0,
            reasons=["low_character_density"],
        )
        router = TesseractOCRRouter(ocr_mode="force")
        router.set_availability_override(True)

        decision = router.route_page(complexity, page_number=1)
        assert decision.should_ocr is True
        assert decision.ocr_skipped is False
        assert decision.action == "ocr"
        assert decision.confidence == 1.0

    def test_evaluate_and_route_convenience_method(self) -> None:
        """Verify evaluate_and_route computes complexity and produces decision in one call."""
        router = TesseractOCRRouter(ocr_mode="off")
        complexity, decision = router.evaluate_and_route(text="Short", page_number=1)

        assert complexity.is_complex is True
        assert decision.action == "skipped"
        assert decision.ocr_skipped is True

    def test_execute_ocr_mocked(self, tmp_path: Path) -> None:
        """Verify execute_ocr sets pytesseract.tesseract_cmd and extracts text (P03b::MAJ-02)."""
        fake_bin = tmp_path / "custom_tesseract.exe"
        fake_bin.write_bytes(b"fake binary")

        router = TesseractOCRRouter(ocr_mode="auto", tesseract_cmd=str(fake_bin))

        mock_pyt = MagicMock()
        mock_pyt.pytesseract = MagicMock()
        mock_pyt.image_to_string.return_value = "Recognized OCR text"
        with patch.dict("sys.modules", {"pytesseract": mock_pyt}):
            assert router.is_available() is True
            mock_img = MagicMock()
            extracted = router.execute_ocr(mock_img, page_number=1)
            assert extracted == "Recognized OCR text"
            assert mock_pyt.pytesseract.tesseract_cmd == str(fake_bin.resolve())

    def test_resolve_tesseract_cmd_checks_nested_pytesseract_and_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression P03b::MAJ-02: resolve_tesseract_cmd checks env & pytesseract.pytesseract."""
        fake_bin = tmp_path / "env_tesseract.exe"
        fake_bin.write_bytes(b"fake binary")

        mock_pyt = MagicMock()
        mock_pyt.pytesseract = MagicMock()
        mock_pyt.pytesseract.tesseract_cmd = str(fake_bin)
        # Top-level pytesseract module does NOT expose tesseract_cmd in real pytesseract!
        del mock_pyt.tesseract_cmd

        monkeypatch.delenv("OPENDV_TESSERACT_CMD", raising=False)
        monkeypatch.delenv("TESSERACT_CMD", raising=False)
        with (
            patch.dict("sys.modules", {"pytesseract": mock_pyt}),
            patch("shutil.which", return_value=None),
        ):
            assert resolve_tesseract_cmd() == str(fake_bin.resolve())

        # Explicit nonexistent OPENDV_TESSERACT_CMD must fail closed (return None)
        monkeypatch.setenv("OPENDV_TESSERACT_CMD", str(tmp_path / "missing_tesseract"))
        with patch.dict("sys.modules", {"pytesseract": mock_pyt}):
            assert resolve_tesseract_cmd() is None

    def test_execute_ocr_auto_mode_failure_raises_ocr_execution_error(self) -> None:
        """Regression P03b::MAJ-02: auto OCR runtime failure raises OCRExecutionError."""
        router = TesseractOCRRouter(ocr_mode="auto")
        router.set_availability_override(True)

        mock_pyt = MagicMock()
        mock_pyt.image_to_string.side_effect = RuntimeError("Tesseract crashed")
        with (
            patch.dict("sys.modules", {"pytesseract": mock_pyt}),
            pytest.raises(OCRExecutionError, match="Tesseract crashed"),
        ):
            router.execute_ocr(MagicMock(), page_number=1)

    def test_execute_ocr_force_mode_failure_raises_ocr_unavailable(self) -> None:
        """Verify execute_ocr in force mode raises OCRUnavailableError when engine fails."""

        router = TesseractOCRRouter(ocr_mode="force")
        router.set_availability_override(True)

        mock_pyt = MagicMock()
        mock_pyt.image_to_string.side_effect = RuntimeError("Tesseract crashed")
        with (
            patch.dict("sys.modules", {"pytesseract": mock_pyt}),
            pytest.raises(OCRUnavailableError) as exc_info,
        ):
            mock_img = MagicMock()
            router.execute_ocr(mock_img, page_number=1)

        assert exc_info.value.exit_code == 1
        assert exc_info.value.http_status == 501


# =============================================================================
# 4. LiteParseDocumentAdapter Integration Tests
# =============================================================================


class TestLiteParseAdapterOCRIntegration:
    """End-to-end integration tests between LiteParseDocumentAdapter and OCR routing."""

    def test_scanned_pdf_with_ocr_mode_off(self) -> None:
        """Verify scanned PDF under ocr_mode='off' extracts with ocr_skipped=True and conf=0.8."""
        pdf_bytes = _create_test_pdf_bytes([""], with_image_xobject=True)

        adapter = LiteParseDocumentAdapter(ocr_mode="off", force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="scanned_off.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is True
        assert page.ocr_skipped is True
        assert page.confidence == 0.8
        assert result.metadata["scanned_pages"] == 1

    def test_scanned_pdf_with_ocr_mode_auto_when_tesseract_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify scanned PDF under ocr_mode='auto' without Tesseract falls back gracefully."""
        pdf_bytes = _create_test_pdf_bytes([""], with_image_xobject=True)

        adapter = LiteParseDocumentAdapter(ocr_mode="auto", force_fallback=True)
        adapter.ocr_router.set_availability_override(False)

        with caplog.at_level(logging.WARNING):
            result = adapter.parse_bytes(pdf_bytes, filename="scanned_auto.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is True
        assert page.ocr_skipped is True
        assert page.confidence == 0.8
        assert "Tesseract OCR unavailable for complex page 1" in caplog.text

    def test_scanned_pdf_with_ocr_mode_force_when_tesseract_missing_raises_501(self) -> None:
        """Verify scanned PDF under ocr_mode='force' without Tesseract fails with exit 1 / 501."""
        pdf_bytes = _create_test_pdf_bytes([""], with_image_xobject=True)

        adapter = LiteParseDocumentAdapter(ocr_mode="force", force_fallback=True)
        adapter.ocr_router.set_availability_override(False)

        with pytest.raises(OCRUnavailableError) as exc_info:
            adapter.parse_bytes(pdf_bytes, filename="scanned_forced.pdf")

        err = exc_info.value
        assert err.exit_code == 1
        assert err.http_status == 501
        assert err.error_code == "OCR_UNAVAILABLE"

    def test_vector_pdf_with_ocr_mode_force_does_not_fail(self) -> None:
        """Verify clean vector PDF under ocr_mode='force' succeeds when engine is available
        and fails fast with OCRUnavailableError when engine is missing (040b4599::MAJ-02).
        """
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(ocr_mode="force", force_fallback=True)
        adapter.ocr_router.set_availability_override(True)

        result = adapter.parse_bytes(data, filename="clean_msa.pdf")
        assert len(result.page_map) == 2
        for p in result.page_map:
            assert p.is_scanned is False
            assert p.ocr_skipped is False
            assert p.confidence == 1.0

        adapter_missing = LiteParseDocumentAdapter(ocr_mode="force", force_fallback=True)
        adapter_missing.ocr_router.set_availability_override(False)
        with pytest.raises(OCRUnavailableError) as exc_info:
            adapter_missing.parse_bytes(data, filename="clean_msa.pdf")
        assert exc_info.value.http_status == 501

    def test_auto_mode_ocr_runtime_failure_preserves_native_text_and_marks_skipped(self) -> None:
        """Regression P03b::MAJ-02: auto OCR runtime failure preserves native text & ocr_skipped."""
        partial_native_text = "Clause 1: Liability Cap $5M."
        pdf_bytes = _create_test_pdf_bytes(
            [partial_native_text],
            image_cm=[500.0, 0.0, 0.0, 600.0, 50.0, 50.0],
            with_image_xobject=True,
        )

        adapter = LiteParseDocumentAdapter(ocr_mode="auto", force_fallback=True)
        adapter.ocr_router.set_availability_override(True)

        with (
            patch.object(
                LiteParseDocumentAdapter,
                "_extract_page_image_payloads",
                return_value=[b"fake_image_bytes"],
            ),
            patch.object(
                TesseractOCRRouter,
                "execute_ocr",
                side_effect=OCRExecutionError("tesseract exited 1"),
            ),
        ):
            result = adapter.parse_bytes(pdf_bytes, filename="hybrid_scan.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert partial_native_text in page.text
        assert page.ocr_skipped is True
        assert page.confidence == 0.8

    def test_hybrid_pdf_page_with_ocr_mode_force_executes_xobject_ocr(self) -> None:
        """Regression S1::R3-MIN-02: Hybrid page (is_scanned=False) runs XObject OCR on force."""
        native_text = (
            "Section 4.2: Canonical Reference Architecture Diagram and System Context Overview."
        )
        pdf_bytes = _create_test_pdf_bytes(
            [native_text],
            image_cm=[100.0, 0.0, 0.0, 100.0, 50.0, 50.0],
            with_image_xobject=True,
        )

        adapter = LiteParseDocumentAdapter(ocr_mode="force", force_fallback=True)
        adapter.ocr_router.set_availability_override(True)

        with (
            patch.object(
                LiteParseDocumentAdapter,
                "_extract_page_image_payloads",
                return_value=[b"fake_diagram_xobject_bytes"],
            ) as mock_extract_imgs,
            patch.object(
                TesseractOCRRouter,
                "execute_ocr",
                return_value="Diagram Box: Counterparty Hub",
            ) as mock_exec_ocr,
        ):
            result = adapter.parse_bytes(pdf_bytes, filename="hybrid_diagram.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is False
        assert native_text in page.text
        assert "Diagram Box: Counterparty Hub" in page.text
        mock_extract_imgs.assert_called_once()
        mock_exec_ocr.assert_called_once()


# =============================================================================
# 5. IngestionService Integration Tests
# =============================================================================


class TestIngestionServiceLayoutIntegration:
    """Tests IngestionService integration with layout evaluator and OCR routing."""

    def test_service_ingest_bytes_with_ocr_mode_auto(self) -> None:
        """Verify IngestionService forwards ocr_mode to adapter and handles graceful fallback."""
        service = IngestionService(adapters=[LiteParseDocumentAdapter(force_fallback=True)])
        pdf_bytes = _create_test_pdf_bytes([""], with_image_xobject=True)

        result = service.ingest_bytes(
            pdf_bytes,
            filename="scanned.pdf",
            mime_type="application/pdf",
            ocr_mode="auto",
        )
        assert len(result.page_map) == 1
        assert result.page_map[0].ocr_skipped is True

    def test_service_ingest_bytes_with_ocr_mode_force_raises(self) -> None:
        """Verify IngestionService raises OCRUnavailableError when ocr_mode='force' on scan."""

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        adapter.ocr_router.set_availability_override(False)
        service = IngestionService(adapters=[adapter])

        pdf_bytes = _create_test_pdf_bytes([""], with_image_xobject=True)

        with pytest.raises(OCRUnavailableError) as exc_info:
            service.ingest_bytes(
                pdf_bytes,
                filename="scanned.pdf",
                mime_type="application/pdf",
                ocr_mode="force",
            )

        assert exc_info.value.exit_code == 1
        assert exc_info.value.http_status == 501

    def test_service_evaluate_layout_complexity_helper(self) -> None:
        """Verify service.evaluate_layout_complexity evaluates PageMapEntries on IngestionResult."""
        service = IngestionService()
        result = service.ingest_text(
            "Short text",
            filename="short.txt",
        )
        complexities = service.evaluate_layout_complexity(result)
        assert len(complexities) == 1
        assert complexities[0].is_complex is True
        assert any("low_character_density" in r for r in complexities[0].reasons)
