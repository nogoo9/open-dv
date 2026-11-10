"""Comprehensive Unit and Integration Tests for LiteParseDocumentAdapter (Task 4B.2 / Issue #202).

Validates:
1. Native PDF parsing using real fixtures and synthetic multi-page streams.
2. PageMapEntry Unicode character code point offsets and browser UTF-16 index.
3. Strict invariant: end_offset - start_offset == len(page_text) across all pages.
4. Fallback mechanism to pypdf when liteparse is absent or raises exceptions.
5. Structured table extraction (pipe-delimited, tab-delimited, whitespace-aligned).
6. Scanned page heuristic detection (image XObjects / character density).
7. Corrupt, empty, encrypted, and non-PDF payload rejection with typed InvalidDocumentError.
8. Stream parsing and IngestionService integration with taint propagation.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pypdf
import pytest

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    TableStructure,
    TaintClassification,
)
from opendv_sources.adapters.liteparse_adapter import (
    HAS_LITEPARSE,
    InvalidDocumentError,
    LiteParseDocumentAdapter,
    extract_tables_from_text,
)
from opendv_sources.exceptions import DocumentExtractionError
from opendv_sources.ingestion.service import IngestionService

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MSA_PDF_PATH = FIXTURES_DIR / "enterprise_msa_addendum_2026.pdf"
CORRUPT_PDF_PATH = FIXTURES_DIR / "corrupt_document.pdf"


def _create_synthetic_pdf_bytes(
    page_contents: list[str],
    *,
    width: float = 612,
    height: float = 792,
    with_image: bool = False,
    with_form_image: bool = False,
) -> bytes:
    """Generate a valid PDF in memory with text streams and optional image XObjects."""
    writer = pypdf.PdfWriter()
    for text in page_contents:
        page = writer.add_blank_page(width=width, height=height)
        safe_lines = [
            line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            for line in text.split("\n")
        ]
        stream_ops = ["BT", "/Helvetica 12 Tf", "50 720 Td"]
        for idx, line in enumerate(safe_lines):
            if idx > 0:
                stream_ops.append("0 -18 Td")
            stream_ops.append(f"({line}) Tj")
        stream_ops.append("ET")
        stream_data = "\n".join(stream_ops).encode("latin-1", errors="replace")

        content_stream = pypdf.generic.DecodedStreamObject()
        content_stream.set_data(stream_data)
        page[pypdf.generic.NameObject("/Contents")] = writer._add_object(content_stream)

        font_dict = pypdf.generic.DictionaryObject({
            pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Font"),
            pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Type1"),
            pypdf.generic.NameObject("/BaseFont"): pypdf.generic.NameObject("/Helvetica"),
        })
        resources_dict: dict[Any, Any] = {
            pypdf.generic.NameObject("/Font"): pypdf.generic.DictionaryObject({
                pypdf.generic.NameObject("/Helvetica"): writer._add_object(font_dict)
            })
        }

        if with_image:
            img_stream = pypdf.generic.DecodedStreamObject()
            img_stream.set_data(b"\x00" * 100)
            img_stream.update({
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/XObject"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Image"),
                pypdf.generic.NameObject("/Width"): pypdf.generic.NumberObject(100),
                pypdf.generic.NameObject("/Height"): pypdf.generic.NumberObject(100),
                pypdf.generic.NameObject("/ColorSpace"): pypdf.generic.NameObject("/DeviceGray"),
                pypdf.generic.NameObject("/BitsPerComponent"): pypdf.generic.NumberObject(8),
            })
            resources_dict[pypdf.generic.NameObject("/XObject")] = pypdf.generic.DictionaryObject({
                pypdf.generic.NameObject("/Im1"): writer._add_object(img_stream)
            })

        if with_form_image:
            img_stream = pypdf.generic.DecodedStreamObject()
            img_stream.set_data(b"\x00" * 100)
            img_stream.update({
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/XObject"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Image"),
                pypdf.generic.NameObject("/Width"): pypdf.generic.NumberObject(100),
                pypdf.generic.NameObject("/Height"): pypdf.generic.NumberObject(100),
                pypdf.generic.NameObject("/ColorSpace"): pypdf.generic.NameObject("/DeviceGray"),
                pypdf.generic.NameObject("/BitsPerComponent"): pypdf.generic.NumberObject(8),
            })
            form_stream = pypdf.generic.DecodedStreamObject()
            form_stream.set_data(b"/Im1 Do")
            form_stream.update({
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/XObject"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Form"),
                pypdf.generic.NameObject("/Resources"): pypdf.generic.DictionaryObject({
                    pypdf.generic.NameObject("/XObject"): pypdf.generic.DictionaryObject({
                        pypdf.generic.NameObject("/Im1"): writer._add_object(img_stream)
                    })
                }),
            })
            resources_dict[pypdf.generic.NameObject("/XObject")] = pypdf.generic.DictionaryObject({
                pypdf.generic.NameObject("/Fm1"): writer._add_object(form_stream)
            })

        page[pypdf.generic.NameObject("/Resources")] = pypdf.generic.DictionaryObject(
            resources_dict
        )

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# =============================================================================
# 1. Adapter Properties and SPI Verification
# =============================================================================


class TestLiteParseAdapterBasics:
    """Test suite for adapter metadata and SPI contract conformance."""

    def test_adapter_identity_and_capabilities(self) -> None:
        """Verify adapter name, supported MIME types, and extension registration."""
        adapter = LiteParseDocumentAdapter()
        assert adapter.adapter_name == "liteparse"
        assert adapter.supports_mime_type("application/pdf") is True
        assert adapter.supports_mime_type("application/x-pdf") is True
        assert adapter.supports_mime_type("APPLICATION/PDF") is True
        assert adapter.supports_mime_type("application/pdf; charset=binary") is True
        assert adapter.supports_mime_type("text/plain") is False
        assert adapter.supports_mime_type("application/json") is False

        assert adapter.supports_extension(".pdf") is True
        assert adapter.supports_extension("pdf") is True
        assert adapter.supports_extension(".PDF") is True
        assert adapter.supports_extension(".txt") is False

    def test_invalid_ocr_mode_raises_value_error(self) -> None:
        """Verify constructor rejects unrecognized ocr_mode string."""
        with pytest.raises(ValueError, match="Invalid ocr_mode"):
            LiteParseDocumentAdapter(ocr_mode="invalid_mode")

    def test_exception_hierarchy(self) -> None:
        """Verify InvalidDocumentError is a typed DocumentExtractionError."""
        err = InvalidDocumentError("Test error")
        assert isinstance(err, DocumentExtractionError)
        assert issubclass(InvalidDocumentError, DocumentExtractionError)


# =============================================================================
# 2. Real PDF Fixture Parsing & Offset Invariants
# =============================================================================


class TestRealPDFFixtureParsing:
    """Test suite parsing committed enterprise PDF fixture enterprise_msa_addendum_2026.pdf."""

    def test_parse_real_msa_fixture_offsets_and_utf16(self) -> None:
        """Verify parsing 2-page MSA PDF fixture produces exact offsets and UTF-16 indices."""
        assert MSA_PDF_PATH.exists(), f"Missing required fixture: {MSA_PDF_PATH}"
        data = MSA_PDF_PATH.read_bytes()

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(data, filename="enterprise_msa_addendum_2026.pdf")

        assert isinstance(result, IngestionResult)
        assert result.filename == "enterprise_msa_addendum_2026.pdf"
        assert result.mime_type == "application/pdf"
        assert result.total_pages == 2
        assert result.total_characters == 733
        assert "\r" not in result.normalized_text
        assert len(result.normalized_text) == 733

        # Page map assertions
        assert len(result.page_map) == 2
        p1 = result.page_map[0]
        p2 = result.page_map[1]

        # Invariant 1: 1-indexed sequential page numbers
        assert p1.page_number == 1
        assert p2.page_number == 2

        # Invariant 2: Page 1 span
        assert p1.start_offset == 0
        assert p1.end_offset == 400
        assert len(p1.text) == 400
        assert p1.end_offset - p1.start_offset == len(p1.text)

        # Invariant 3: Page 2 span
        assert p2.start_offset == 400
        assert p2.end_offset == 733
        assert len(p2.text) == 333
        assert p2.end_offset - p2.start_offset == len(p2.text)

        # Invariant 4: Browser UTF-16 offsets
        assert p1.utf16_start == 0
        assert p1.utf16_end == 400
        assert p2.utf16_start == 400
        assert p2.utf16_end == 733

        # Invariant 5: Clean text page properties
        assert p1.is_scanned is False
        assert p1.ocr_skipped is False
        assert p1.confidence == 1.0
        assert p1.bounding_box is not None
        assert len(p1.bounding_box) == 4

        # Invariant 6: Metadata engine
        assert result.metadata["engine"] == "pypdf"
        assert result.metadata["total_pages"] == 2
        assert result.metadata["scanned_pages"] == 0

        # Invariant 7: Chunks extracted with valid offsets
        assert len(result.chunks) > 0
        for chk in result.chunks:
            assert chk.document_id == result.document_id
            assert chk.start_char is not None
            assert chk.end_char is not None
            assert chk.end_char - chk.start_char == len(chk.text)
            assert result.normalized_text[chk.start_char : chk.end_char] == chk.text
            assert chk.utf16_start is not None
            assert chk.utf16_end is not None
            assert chk.utf16_start <= chk.utf16_end

    def test_msa_text_grounding_matches_raw_txt_fixture(self) -> None:
        """Verify normalized text matches canonical enterprise_msa_addendum_2026.txt."""
        txt_path = FIXTURES_DIR / "enterprise_msa_addendum_2026.txt"
        assert txt_path.exists()
        raw_content = txt_path.read_text(encoding="utf-8")
        expected_text = raw_content.replace("\r\n", "\n").replace("\r", "\n")

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        data = MSA_PDF_PATH.read_bytes()
        result = adapter.parse_bytes(data, filename="enterprise_msa_addendum_2026.pdf")

        assert result.normalized_text == expected_text


# =============================================================================
# 3. Synthetic Multi-Page PDF & Unicode Offset Invariants
# =============================================================================


class TestSyntheticPDFOffsets:
    """Test suite using synthetic PDFs to verify arbitrary page counts and Unicode handling."""

    def test_three_page_sequential_tiling(self) -> None:
        """Verify sequential page tiling without gaps or overlaps across 3 pages."""
        p1_text = "Page 1 First Paragraph.\nPage 1 Second Paragraph."
        p2_text = "Page 2 Single Body Line."
        p3_text = "Page 3 Concluding Remarks."
        pdf_bytes = _create_synthetic_pdf_bytes([p1_text, p2_text, p3_text])

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="three_page.pdf")

        assert result.total_pages == 3
        assert len(result.page_map) == 3

        curr_off = 0
        for idx, page_entry in enumerate(result.page_map):
            assert page_entry.page_number == idx + 1
            assert page_entry.start_offset == curr_off
            expected_len = len(page_entry.text)
            assert page_entry.end_offset - page_entry.start_offset == expected_len
            assert (
                result.normalized_text[page_entry.start_offset : page_entry.end_offset]
                == page_entry.text
            )
            curr_off = page_entry.end_offset

        assert curr_off == len(result.normalized_text)

    def test_non_bmp_unicode_surrogate_offsets_directly(self) -> None:
        """Verify UTF-16 code unit offset calculation handles surrogate pairs (> 0xFFFF)."""
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        # Directly test via _build_ingestion_result to avoid synthetic font encoding limits
        non_bmp_text = "Emoji: \U0001F600 Rocket: \U0001F680 End."
        result = adapter._build_ingestion_result(
            page_texts=[non_bmp_text],
            page_bboxes=[(0.0, 0.0, 612.0, 792.0)],
            page_scanned_flags=[False],
            data=b"%PDF-1.4 mock",
            filename="surrogate.pdf",
            mime_type="application/pdf",
            engine="pypdf",
        )

        assert len(result.page_map) == 1
        p = result.page_map[0]
        assert p.start_offset == 0
        assert p.end_offset == len(non_bmp_text)
        assert p.end_offset - p.start_offset == len(non_bmp_text)
        assert p.utf16_start == 0

        # Each non-BMP char adds 2 UTF-16 code units (surrogate pair)
        code_points = len(non_bmp_text)
        surrogates_count = sum(1 for c in non_bmp_text if ord(c) > 0xFFFF)
        expected_utf16 = code_points + surrogates_count
        assert p.utf16_end == expected_utf16
        assert expected_utf16 > code_points


# =============================================================================
# 4. Fallback Behavior and Engine Metadata
# =============================================================================


class TestLiteParseFallbackMechanism:
    """Test suite verifying graceful fallback to pypdf when liteparse is absent or fails."""

    def test_explicit_force_fallback_records_pypdf_engine(self) -> None:
        """Verify force_fallback=True forces pypdf execution and records metadata."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(data, filename="sample.pdf")

        assert result.metadata["engine"] == "pypdf"
        assert result.total_pages == 2

    def test_liteparse_runtime_exception_triggers_graceful_fallback(self) -> None:
        """Verify when native liteparse raises a runtime exception, adapter falls back (MIN-03)."""
        data = MSA_PDF_PATH.read_bytes()

        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.side_effect = RuntimeError("PDFium parser failed on corrupted stream")
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(force_fallback=False)
            result = adapter.parse_bytes(data, filename="sample.pdf")

            assert result.metadata["engine"] == "pypdf"
            assert "fallback_reason" in result.metadata
            assert "RuntimeError" in result.metadata["fallback_reason"]
            assert result.total_pages == 2
            assert result.total_characters == 733

    def test_fallback_handles_reserved_kwargs_without_collision(self) -> None:
        """Verify fallback forwards extra kwargs without duplicate-keyword TypeError (MIN-06)."""
        data = MSA_PDF_PATH.read_bytes()

        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.side_effect = RuntimeError("Simulated failure")
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(force_fallback=False)
            result = adapter.parse_bytes(
                data,
                filename="sample.pdf",
                fallback_reason="caller_fallback",
                force_fallback=False,
            )
            assert result.metadata["engine"] == "pypdf"

    def test_caller_fallback_reason_ignored_on_liteparse_success(self) -> None:
        """Verify caller-supplied fallback_reason does not appear on liteparse success (MIN-04)."""
        data = MSA_PDF_PATH.read_bytes()

        mock_page = SimpleNamespace(
            text="Single Page Content",
            bounding_box=(0.0, 0.0, 612.0, 792.0),
            is_scanned=False,
        )
        mock_parsed = SimpleNamespace(pages=[mock_page])
        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_parsed
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(force_fallback=False)
            result = adapter.parse_bytes(
                data,
                filename="sample.pdf",
                fallback_reason="spoofed_fallback_reason",
            )
            assert result.metadata["engine"] == "liteparse"
            assert "fallback_reason" not in result.metadata

    def test_explicit_taint_none_defaults_to_internal(self) -> None:
        """Verify passing taint=None defaults safely to TaintClassification.INTERNAL (MIN-05)."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(data, filename="sample.pdf", taint=None)
        assert result.taint == TaintClassification.INTERNAL

    def test_mock_successful_liteparse_execution(self) -> None:
        """Verify successful native liteparse execution returns engine='liteparse' (MIN-09)."""
        data = MSA_PDF_PATH.read_bytes()

        # Use SimpleNamespace to avoid MagicMock auto-attributes masking bounding_box (MIN-09)
        mock_page_1 = SimpleNamespace(
            text="Page 1 Content from native PDFium",
            bounding_box=(0.0, 0.0, 612.0, 792.0),
            is_scanned=False,
        )
        mock_page_2 = SimpleNamespace(
            text="Page 2 Content from native PDFium",
            bounding_box=(0.0, 0.0, 612.0, 792.0),
            is_scanned=False,
        )
        mock_parsed = SimpleNamespace(pages=[mock_page_1, mock_page_2])

        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_parsed
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(force_fallback=False)
            result = adapter.parse_bytes(data, filename="sample.pdf")

            assert result.metadata["engine"] == "liteparse"
            assert result.total_pages == 2
            assert "Page 1 Content from native PDFium" in result.normalized_text
            assert "Page 2 Content from native PDFium" in result.normalized_text
            assert len(result.page_map) == 2
            # Explicitly assert bounding_box was propagated correctly (MIN-09)
            assert result.page_map[0].bounding_box == (0.0, 0.0, 612.0, 792.0)
            assert result.page_map[1].bounding_box == (0.0, 0.0, 612.0, 792.0)

    @pytest.mark.skipif(not HAS_LITEPARSE, reason="liteparse 0.4.x library is not installed")
    def test_real_liteparse_integration_if_available(self) -> None:
        """Verify real liteparse integration when native wheel is installed (MIN-01)."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(force_fallback=False)
        result = adapter.parse_bytes(data, filename="msa.pdf")
        assert result.metadata["engine"] == "liteparse"
        assert result.total_pages == 2


# =============================================================================
# 5. Table Extraction Tests
# =============================================================================


class TestTableExtraction:
    """Test suite verifying table structure detection and extraction into TableStructure DTOs."""

    def test_extract_markdown_pipe_table(self) -> None:
        """Verify extraction of standard Markdown pipe table with headers and data rows."""
        text = (
            "Introduction section header.\n\n"
            "| Concept URN | ElM Archetype | Target Hub |\n"
            "|:---|:---:|---:|\n"
            "| urn:entity:fund | CORE_CONCEPT | hub_fund |\n"
            "| urn:entity:investor | CORE_CONCEPT | hub_investor |\n"
            "| urn:entity:commitment | BALANCE_CONCEPT | hub_commitment |\n\n"
            "Trailing paragraph after table."
        )
        tables = extract_tables_from_text(text, page_number=1)

        assert len(tables) == 1
        tbl = tables[0]
        assert isinstance(tbl, TableStructure)
        assert tbl.table_id == "tbl_p1_1"
        assert tbl.page_number == 1
        assert tbl.headers == ["Concept URN", "ElM Archetype", "Target Hub"]
        assert tbl.row_count == 3
        assert tbl.column_count == 3
        assert tbl.rows[0] == ["urn:entity:fund", "CORE_CONCEPT", "hub_fund"]
        assert tbl.rows[1] == ["urn:entity:investor", "CORE_CONCEPT", "hub_investor"]
        assert tbl.rows[2] == ["urn:entity:commitment", "BALANCE_CONCEPT", "hub_commitment"]
        assert tbl.raw_html_or_md is not None
        assert "| Concept URN |" in tbl.raw_html_or_md
        assert tbl.bounding_box is None  # MIN-02: None when table geometry unknown

    def test_extract_tab_delimited_table(self) -> None:
        """Verify extraction of tab-delimited tabular structure."""
        text = (
            "Tab table heading:\n"
            "Identifier\tName\tStatus\n"
            "ID-001\tAlpha Fund\tActive\n"
            "ID-002\tBeta Fund\tPending\n"
            "ID-003\tGamma Fund\tClosed\n"
        )
        tables = extract_tables_from_text(text, page_number=2)

        assert len(tables) == 1
        tbl = tables[0]
        assert tbl.table_id == "tbl_p2_1"
        assert tbl.page_number == 2
        assert tbl.headers == ["Identifier", "Name", "Status"]
        assert tbl.row_count == 3
        assert tbl.column_count == 3
        assert tbl.rows[0] == ["ID-001", "Alpha Fund", "Active"]
        assert tbl.bounding_box is None

    def test_extract_whitespace_aligned_table(self) -> None:
        """Verify extraction of multi-space column-aligned tables."""
        text = (
            "Columnar financial data:\n"
            "Account Code    Balance USD    Audit Tier\n"
            "ACC-1001        2500000.00     Tier 1\n"
            "ACC-1002        1250000.50     Tier 2\n"
            "ACC-1003         750000.25     Tier 1\n"
        )
        tables = extract_tables_from_text(text, page_number=3)

        assert len(tables) == 1
        tbl = tables[0]
        assert tbl.table_id == "tbl_p3_1"
        assert tbl.page_number == 3
        assert tbl.headers == ["Account Code", "Balance USD", "Audit Tier"]
        assert tbl.row_count == 3
        assert tbl.column_count == 3
        assert tbl.bounding_box is None

    def test_justified_prose_with_double_spaces_is_not_classified_as_table(self) -> None:
        """Verify justified prose with double spaces is not classified as table (MIN-07)."""
        prose = (
            "The quick brown  fox jumps over the lazy dog in the meadow.\n"
            "This second line has  words with irregular spacing across the paragraph.\n"
            "And a third line  with yet different column offsets in the justified text.\n"
        )
        tables = extract_tables_from_text(prose, page_number=1)
        assert len(tables) == 0

    def test_pdf_parsing_with_table_sets_has_tables_flag_and_span_match(self) -> None:
        """Verify PDF containing tabular data marks page_entry.has_tables = True (MIN-01)."""
        table_page = (
            "Leading Header Section\n\n"
            "| Entity | Code |\n"
            "|---|---|\n"
            "| Acme | ACM |\n"
            "| Global | GLB |\n\n"
            "Trailing notes paragraph."
        )
        pdf_bytes = _create_synthetic_pdf_bytes([table_page])

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="table_doc.pdf")

        assert len(result.tables) == 1
        assert len(result.page_map) == 1
        assert result.page_map[0].has_tables is True
        assert result.metadata["tables_extracted"] == 1

        # Check evidence chunk associated with table via span matching (MIN-01)
        table_chunks = [c for c in result.chunks if c.is_table]
        assert len(table_chunks) >= 1
        assert table_chunks[0].table_id == "tbl_p1_1"

    def test_disable_table_extraction_flag(self) -> None:
        """Verify extract_tables=False suppresses table extraction."""
        table_page = (
            "| Entity | Code |\n"
            "|---|---|\n"
            "| Acme | ACM |\n"
        )
        pdf_bytes = _create_synthetic_pdf_bytes([table_page])

        adapter = LiteParseDocumentAdapter(extract_tables=False, force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="no_table.pdf")

        assert len(result.tables) == 0
        assert result.page_map[0].has_tables is False
        assert result.metadata["tables_extracted"] == 0


# =============================================================================
# 6. Scanned Page Detection and OCR Routing Metadata
# =============================================================================


class TestScannedPageDetection:
    """Test suite verifying low-character / raster heuristics and OCR metadata."""

    def test_normal_text_page_not_scanned(self) -> None:
        """Verify standard text pages (> 50 chars, no raster) are not flagged as scanned."""
        text = (
            "This is a standard text document page with sufficient character "
            "density for parsing without OCR."
        )
        pdf_bytes = _create_synthetic_pdf_bytes([text])

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="text.pdf")

        assert result.page_map[0].is_scanned is False
        assert result.page_map[0].ocr_skipped is False
        assert result.page_map[0].confidence == 1.0

    def test_scanned_image_only_page_detection(self) -> None:
        """Verify image-only page (< 50 chars with image) flags is_scanned=True (MAJ-01/02)."""
        # Empty text stream with image XObject
        pdf_bytes = _create_synthetic_pdf_bytes([""], with_image=True)

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="scanned.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is True
        assert page.ocr_skipped is True  # pypdf path did not OCR
        assert page.confidence == 0.8
        assert result.metadata["scanned_pages"] == 1

    def test_text_page_with_logo_not_flagged_as_scanned(self) -> None:
        """Verify normal text page (> 50 chars) with image is not flagged scanned (MAJ-02)."""
        text = (
            "Confidential Corporate Agreement (2026-MSA-001)\n"
            "This document contains standard legal provisions governing "
            "commercial transactions between the parties."
        )
        pdf_bytes = _create_synthetic_pdf_bytes([text], with_image=True)

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="text_with_logo.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is False
        assert page.ocr_skipped is False
        assert page.confidence == 1.0
        assert result.metadata["scanned_pages"] == 0

    def test_scanned_page_with_ocr_mode_off_marks_ocr_skipped(self) -> None:
        """Verify ocr_mode='off' marks scanned page as ocr_skipped with 0.8 conf (MAJ-03)."""
        pdf_bytes = _create_synthetic_pdf_bytes([""], with_image=True)

        adapter = LiteParseDocumentAdapter(ocr_mode="off", force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="scanned_no_ocr.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is True
        assert page.ocr_skipped is True
        assert page.confidence == 0.8

    def test_scanned_page_with_nested_form_image_detected(self) -> None:
        """Verify images nested in Form XObjects are detected by scanned heuristic (MIN-06)."""
        pdf_bytes = _create_synthetic_pdf_bytes([""], with_form_image=True)

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        result = adapter.parse_bytes(pdf_bytes, filename="form_scan.pdf")

        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.is_scanned is True
        assert page.ocr_skipped is True
        assert page.confidence == 0.8
        assert result.metadata["scanned_pages"] == 1

    def test_liteparse_scanned_page_ocr_auto_mode(self) -> None:
        """Verify ocr_mode='auto' enables OCR for native liteparse on scanned pages (MAJ-01)."""
        data = MSA_PDF_PATH.read_bytes()

        mock_page = SimpleNamespace(
            text="OCR-extracted text in auto mode",
            bounding_box=(0.0, 0.0, 612.0, 792.0),
            is_scanned=True,
        )
        mock_parsed = SimpleNamespace(pages=[mock_page])
        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_parsed
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(ocr_mode="auto", force_fallback=False)
            result = adapter.parse_bytes(data, filename="scanned_auto.pdf")

            assert result.metadata["engine"] == "liteparse"
            page = result.page_map[0]
            assert page.is_scanned is True
            assert page.ocr_skipped is False  # In 'auto' mode, OCR ran
            assert page.confidence == 1.0
            mock_liteparse_mod.LiteParse.assert_called_once_with(ocr_enabled=True, ocr_mode="auto")

    def test_liteparse_scanned_page_ocr_off_mode(self) -> None:
        """Verify ocr_mode='off' disables OCR for native liteparse on scanned pages (MAJ-01)."""
        data = MSA_PDF_PATH.read_bytes()

        mock_page = SimpleNamespace(
            text="",
            bounding_box=(0.0, 0.0, 612.0, 792.0),
            is_scanned=True,
        )
        mock_parsed = SimpleNamespace(pages=[mock_page])
        mock_liteparse_mod = MagicMock()
        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_parsed
        mock_liteparse_mod.LiteParse.return_value = mock_parser

        with (
            patch("opendv_sources.adapters.liteparse_adapter.HAS_LITEPARSE", True),
            patch("opendv_sources.adapters.liteparse_adapter.liteparse", mock_liteparse_mod),
        ):
            adapter = LiteParseDocumentAdapter(ocr_mode="off", force_fallback=False)
            result = adapter.parse_bytes(data, filename="scanned_off.pdf")

            assert result.metadata["engine"] == "liteparse"
            page = result.page_map[0]
            assert page.is_scanned is True
            assert page.ocr_skipped is True  # OCR was disabled
            assert page.confidence == 0.8
            mock_liteparse_mod.LiteParse.assert_called_once_with(ocr_enabled=False, ocr_mode="off")

    def test_pypdf_records_ocr_unavailable_when_forced(self) -> None:
        """Verify pypdf records ocr_requested_but_unavailable when ocr_mode='force' (SUG-03)."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(ocr_mode="force", force_fallback=True)
        result = adapter.parse_bytes(data, filename="sample.pdf")
        assert result.metadata.get("ocr_requested_but_unavailable") is True


# =============================================================================
# 7. Corrupted, Empty, Encrypted and Invalid PDF Handling
# =============================================================================


class TestCorruptAndInvalidPDFs:
    """Test suite verifying graceful error raising on invalid payloads."""

    def test_empty_bytes_raises_invalid_document_error(self) -> None:
        """Verify empty 0-byte payload raises typed InvalidDocumentError."""
        adapter = LiteParseDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="empty \\(0 bytes\\)"):
            adapter.parse_bytes(b"", filename="empty.pdf")

    def test_missing_magic_header_raises_invalid_document_error(self) -> None:
        """Verify payload missing '%PDF-' marker raises typed InvalidDocumentError."""
        adapter = LiteParseDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="missing '%PDF-' header marker"):
            adapter.parse_bytes(b"This is not a PDF file", filename="invalid.pdf")

    def test_corrupt_pdf_fixture_raises_invalid_document_error(self) -> None:
        """Verify corrupt PDF fixture raises typed InvalidDocumentError."""
        assert CORRUPT_PDF_PATH.exists()
        corrupt_bytes = CORRUPT_PDF_PATH.read_bytes()

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        with pytest.raises(InvalidDocumentError):
            adapter.parse_bytes(corrupt_bytes, filename="corrupt.pdf")

    def test_corrupted_truncated_stream_raises_invalid_document_error(self) -> None:
        """Verify truncated PDF stream raises typed InvalidDocumentError."""
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        truncated = b"%PDF-1.4\n1 0 obj << /Type /Catalog"
        with pytest.raises(InvalidDocumentError):
            adapter.parse_bytes(truncated, filename="truncated.pdf")

    def test_encrypted_password_protected_pdf_raises_invalid_document_error(self) -> None:
        """Verify password-protected PDF raises typed InvalidDocumentError."""
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("super_secret_password")
        buf = io.BytesIO()
        writer.write(buf)
        encrypted_bytes = buf.getvalue()

        adapter = LiteParseDocumentAdapter(force_fallback=True)
        with pytest.raises(InvalidDocumentError, match="Password-protected PDF"):
            adapter.parse_bytes(encrypted_bytes, filename="protected.pdf")

    def test_mismatched_caller_source_sha256_raises_error(self) -> None:
        """Verify caller-supplied source_sha256 that does not match computed digest
        raises DocumentExtractionError (MIN-04).
        """
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        with pytest.raises(DocumentExtractionError, match="does not match computed digest"):
            adapter.parse_bytes(
                data,
                filename="msa.pdf",
                source_sha256="0000000000000000000000000000000000000000000000000000000000000000",
            )

    def test_malformed_pdf_raising_index_error_wrapped_in_invalid_document_error(self) -> None:
        """Verify malformed PDF causing parser errors raises InvalidDocumentError (MIN-02)."""
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        # Header followed by corrupt xref structure
        corrupt = b"%PDF-1.4\ntrailer\n<< /Size 0 /Root 1 0 R >>\nstartxref\n99999\n%%EOF"
        with pytest.raises(InvalidDocumentError):
            adapter.parse_bytes(corrupt, filename="malformed_xref.pdf")

    def test_max_bytes_limit_exceeded_raises_invalid_document_error(self) -> None:
        """Verify document exceeding max_bytes raises InvalidDocumentError (SUG-02)."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(max_bytes=100)
        with pytest.raises(InvalidDocumentError, match="exceeds maximum allowed size"):
            adapter.parse_bytes(data, filename="sample.pdf")

    def test_max_pages_limit_exceeded_raises_invalid_document_error(self) -> None:
        """Verify document exceeding max_pages raises InvalidDocumentError (SUG-02)."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(max_pages=1, force_fallback=True)
        with pytest.raises(InvalidDocumentError, match="exceeds maximum allowed page count"):
            adapter.parse_bytes(data, filename="sample.pdf")


# =============================================================================
# 8. Stream Parsing & IngestionService Integration
# =============================================================================


class TestStreamAndServiceIntegration:
    """Test suite verifying parse_stream parity and integration into IngestionService."""

    def test_parse_stream_parity_with_parse_bytes(self) -> None:
        """Verify parse_stream delegates to parse_bytes and yields identical result."""
        data = MSA_PDF_PATH.read_bytes()
        adapter = LiteParseDocumentAdapter(force_fallback=True)

        res_bytes = adapter.parse_bytes(data, filename="enterprise_msa_addendum_2026.pdf")
        stream = io.BytesIO(data)
        res_stream = adapter.parse_stream(stream, filename="enterprise_msa_addendum_2026.pdf")

        assert res_bytes.total_characters == res_stream.total_characters
        assert res_bytes.total_pages == res_stream.total_pages
        assert res_bytes.normalized_text == res_stream.normalized_text
        assert len(res_bytes.page_map) == len(res_stream.page_map)
        assert res_bytes.source_sha256 == res_stream.source_sha256

    def test_ingestion_service_e2e_pipeline_with_liteparse_adapter(self) -> None:
        """Verify LiteParseDocumentAdapter integrates into IngestionService framework."""
        adapter = LiteParseDocumentAdapter(force_fallback=True)
        service = IngestionService(adapters=[adapter])

        data = MSA_PDF_PATH.read_bytes()
        result = service.ingest_bytes(
            data,
            filename="enterprise_msa_addendum_2026.pdf",
            mime_type="application/pdf",
            taint=TaintClassification.CONFIDENTIAL,
        )

        assert isinstance(result, IngestionResult)
        assert result.filename == "enterprise_msa_addendum_2026.pdf"
        assert result.total_pages == 2
        assert result.total_characters == 733
        assert result.taint == TaintClassification.CONFIDENTIAL

        # Verify chunks have elevated taint and accurate offsets
        assert len(result.chunks) > 0
        for chk in result.chunks:
            assert chk.taint == TaintClassification.CONFIDENTIAL
            assert chk.start_char is not None
            assert chk.end_char is not None
            assert chk.end_char - chk.start_char == len(chk.text)
            assert result.normalized_text[chk.start_char : chk.end_char] == chk.text
