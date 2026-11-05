"""Conformance and unit tests for Text and PDF document source adapters (T16 / #86)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from opendv_core.paths import PathContainmentError
from opendv_sources.adapters.pdf_document import PdfDocumentSourceAdapter
from opendv_sources.adapters.text_document import TextDocumentSourceAdapter
from opendv_sources.conformance.document_suite import DocumentAdapterConformanceSuite
from opendv_sources.exceptions import (
    DocumentExtractionError,
    SourceNotFoundError,
)
from opendv_sources.models.specs import SourceSpec
from opendv_sources.registry.source_registry import get_source_registry

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestTextDocumentAdapterConformance(DocumentAdapterConformanceSuite):
    """Run TextDocumentSourceAdapter through DocumentAdapterConformanceSuite."""

    @pytest.fixture
    def adapter(self, tmp_path: Path) -> TextDocumentSourceAdapter:
        return TextDocumentSourceAdapter(workspace_root=tmp_path)

    @pytest.fixture
    def sample_spec(self, tmp_path: Path) -> SourceSpec:
        p = tmp_path / "sample.txt"
        p.write_bytes(b"Uniform text content\n")
        return SourceSpec(adapter="text", uri="sample.txt", format="text")

    @pytest.fixture
    def raw_bytes(self) -> bytes:
        return b"Uniform text content\n"

    @pytest.fixture
    def lf_spec(self, tmp_path: Path) -> SourceSpec:
        p = tmp_path / "lf.txt"
        p.write_text("Line 1\nLine 2\n", encoding="utf-8", newline="\n")
        return SourceSpec(adapter="text", uri="lf.txt")

    @pytest.fixture
    def crlf_spec(self, tmp_path: Path) -> SourceSpec:
        p = tmp_path / "crlf.txt"
        p.write_bytes(b"Line 1\r\nLine 2\r\n")
        return SourceSpec(adapter="text", uri="crlf.txt")

    @pytest.fixture
    def multi_page_spec(self, tmp_path: Path) -> SourceSpec:
        p = tmp_path / "single_page_doc.txt"
        p.write_text("Header\nBody\nFooter\n", encoding="utf-8")
        return SourceSpec(adapter="text", uri="single_page_doc.txt")

    @pytest.fixture
    def corrupt_spec(self, tmp_path: Path) -> SourceSpec:
        p = tmp_path / "corrupt.txt"
        p.write_bytes(b"\xff\xfe\x00\x00\x80\x99invalid_utf8")
        return SourceSpec(adapter="text", uri="corrupt.txt")


class TestPdfDocumentAdapterConformance(DocumentAdapterConformanceSuite):
    """Run PdfDocumentSourceAdapter through DocumentAdapterConformanceSuite."""

    @pytest.fixture
    def adapter(self) -> PdfDocumentSourceAdapter:
        return PdfDocumentSourceAdapter(workspace_root=FIXTURES_DIR)

    @pytest.fixture
    def sample_spec(self) -> SourceSpec:
        return SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf", format="pdf")

    @pytest.fixture
    def raw_bytes(self) -> bytes:
        return (FIXTURES_DIR / "enterprise_msa_addendum_2026.pdf").read_bytes()

    @pytest.fixture
    def lf_spec(self) -> SourceSpec:
        return SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf")

    @pytest.fixture
    def crlf_spec(self) -> SourceSpec:
        return SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf")

    @pytest.fixture
    def multi_page_spec(self) -> SourceSpec:
        return SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf")

    @pytest.fixture
    def corrupt_spec(self) -> SourceSpec:
        return SourceSpec(adapter="pdf", uri="corrupt_document.pdf")


def test_msa_fixture_offset_equality_and_span_grounding() -> None:
    """T16 requirement: .txt and 2-page .pdf give identical text, offsets, and page numbers."""
    text_adapter = TextDocumentSourceAdapter(workspace_root=FIXTURES_DIR)
    pdf_adapter = PdfDocumentSourceAdapter(workspace_root=FIXTURES_DIR)

    txt_spec = SourceSpec(adapter="text", uri="enterprise_msa_addendum_2026.txt")
    pdf_spec = SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf")
    crlf_spec = SourceSpec(adapter="text", uri="enterprise_msa_addendum_2026_crlf.txt")

    doc_txt = text_adapter.extract_document(txt_spec)
    doc_pdf = pdf_adapter.extract_document(pdf_spec)
    doc_crlf = text_adapter.extract_document(crlf_spec)

    # 1. Byte-identical normalized text across all three
    assert "\r" not in doc_txt.normalized_text
    assert "\r" not in doc_pdf.normalized_text
    assert "\r" not in doc_crlf.normalized_text
    assert doc_txt.normalized_text == doc_pdf.normalized_text
    assert doc_txt.normalized_text == doc_crlf.normalized_text
    assert doc_txt.char_count == doc_pdf.char_count == doc_crlf.char_count == 733

    # 2. Page map assertions
    assert len(doc_txt.page_map) == 1
    assert doc_txt.page_map[0].page_number == 1
    assert doc_txt.page_map[0].start_char == 0
    assert doc_txt.page_map[0].end_char == 733

    assert len(doc_pdf.page_map) == 2
    assert doc_pdf.page_map[0].page_number == 1
    assert doc_pdf.page_map[0].start_char == 0
    assert doc_pdf.page_map[0].end_char == 400
    assert doc_pdf.page_map[1].page_number == 2
    assert doc_pdf.page_map[1].start_char == 400
    assert doc_pdf.page_map[1].end_char == 733

    # 3. Span grounding equality (Algorithm A.9b)
    # Tier-U1 mention on Page 1
    quote_u1 = "Apex Industrial Holdings LLC (Tax ID: TAX-US-900101)"
    start_u1_txt = doc_txt.normalized_text.find(quote_u1)
    start_u1_pdf = doc_pdf.normalized_text.find(quote_u1)
    assert start_u1_txt == start_u1_pdf
    assert start_u1_txt >= 0
    end_u1 = start_u1_txt + len(quote_u1)
    assert doc_txt.normalized_text[start_u1_txt:end_u1] == quote_u1
    assert doc_pdf.normalized_text[start_u1_pdf:end_u1] == quote_u1
    # Page 1 contains Tier-U1 span
    assert doc_pdf.page_map[0].start_char <= start_u1_pdf < doc_pdf.page_map[0].end_char

    # Tier-U3 mention on Page 2
    quote_u3 = "Rheinland Precision Holding Group"
    start_u3_txt = doc_txt.normalized_text.find(quote_u3)
    start_u3_pdf = doc_pdf.normalized_text.find(quote_u3)
    assert start_u3_txt == start_u3_pdf
    assert start_u3_txt >= 400
    end_u3 = start_u3_txt + len(quote_u3)
    assert doc_txt.normalized_text[start_u3_txt:end_u3] == quote_u3
    assert doc_pdf.normalized_text[start_u3_pdf:end_u3] == quote_u3
    # Page 2 contains Tier-U3 span
    assert doc_pdf.page_map[1].start_char <= start_u3_pdf < doc_pdf.page_map[1].end_char


def test_corrupt_and_missing_document_errors(tmp_path: Path) -> None:
    """Verify corrupt and missing documents raise typed errors."""
    text_adapter = TextDocumentSourceAdapter(workspace_root=tmp_path)
    pdf_adapter = PdfDocumentSourceAdapter(workspace_root=tmp_path)

    # Missing file
    missing_spec = SourceSpec(adapter="pdf", uri="does_not_exist.pdf")
    with pytest.raises(SourceNotFoundError):
        pdf_adapter.extract_document(missing_spec)

    # Corrupt PDF (non-PDF header)
    corrupt_file = tmp_path / "bad_header.pdf"
    corrupt_file.write_bytes(b"NOT A REAL PDF FILE")
    with pytest.raises(DocumentExtractionError, match="Invalid PDF document"):
        pdf_adapter.extract_document(SourceSpec(adapter="pdf", uri="bad_header.pdf"))

    # Empty PDF (0 bytes)
    empty_file = tmp_path / "empty.pdf"
    empty_file.write_bytes(b"")
    with pytest.raises(DocumentExtractionError, match="empty"):
        pdf_adapter.extract_document(SourceSpec(adapter="pdf", uri="empty.pdf"))

    # Corrupt text file (invalid UTF-8 sequence)
    bad_utf8 = tmp_path / "invalid_utf8.txt"
    bad_utf8.write_bytes(b"\xff\xfe\x00\x00\x80\x99")
    with pytest.raises(DocumentExtractionError, match="Failed to decode text document"):
        text_adapter.extract_document(SourceSpec(adapter="text", uri="invalid_utf8.txt"))


def test_security_path_containment(tmp_path: Path) -> None:
    """Verify path traversal outside permitted roots is rejected."""
    adapter = TextDocumentSourceAdapter(workspace_root=tmp_path)
    with pytest.raises(PathContainmentError):
        adapter.extract_document(SourceSpec(adapter="text", uri="../escaped.txt"))

    with pytest.raises(PathContainmentError):
        adapter.extract_document(SourceSpec(adapter="text", uri="doc.txt\x00malicious"))


def test_inline_document_extraction() -> None:
    """Verify inline content (Decision D1) functions with zero disk files."""
    text_adapter = TextDocumentSourceAdapter()
    pdf_adapter = PdfDocumentSourceAdapter()

    # Inline plain text
    txt_doc = text_adapter.extract_document(
        SourceSpec(adapter="text", inline_content="Hello Inline World\r\nLine 2")
    )
    assert txt_doc.normalized_text == "Hello Inline World\nLine 2"
    assert txt_doc.media_type == "text/plain"

    # Inline base64 PDF
    raw_pdf = (FIXTURES_DIR / "enterprise_msa_addendum_2026.pdf").read_bytes()
    b64_pdf = base64.b64encode(raw_pdf).decode("ascii")
    pdf_doc = pdf_adapter.extract_document(
        SourceSpec(adapter="pdf", inline_content=b64_pdf, options={"is_base64": True})
    )
    assert pdf_doc.char_count == 733
    assert len(pdf_doc.page_map) == 2


def test_registry_integration_and_entry_points() -> None:
    """Verify dynamic discovery and registration under entry point opendv.source_adapters."""
    registry = get_source_registry()
    registry.reset()

    text_ad = registry.get_document_adapter("text")
    assert isinstance(text_ad, TextDocumentSourceAdapter)

    pdf_ad = registry.get_document_adapter("pdf")
    assert isinstance(pdf_ad, PdfDocumentSourceAdapter)

    # Document adapter aliases
    assert registry.get_document_adapter("txt") is not None
    assert registry.get_document_adapter("md") is not None
    assert registry.get_document_adapter("markdown") is not None
