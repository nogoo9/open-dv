"""Unit and Integration Tests for DocumentAdapter and IngestionService (Task 4B.1 / Issue #201)."""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TableStructure,
    TaintClassification,
)
from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.ingestion import (
    BaseDocumentAdapter,
    IngestionService,
    PlainTextDocumentAdapter,
    build_normalization_offset_map,
    compute_utf16_code_units,
    compute_utf16_offset,
    compute_utf16_offset_map,
    max_taint,
    normalize_line_endings,
    normalize_text,
    normalize_unicode_nfc,
)

# =============================================================================
# 1. Evidence and Ingestion DTO Tests
# =============================================================================


class TestIngestionDTOs:
    """Test suite for contracts/dtos/ingestion.py data transfer objects."""

    def test_taint_classification_enum_and_case_insensitivity(self) -> None:
        """Verify TaintClassification enum values and case-insensitive resolution."""
        assert TaintClassification.PUBLIC == "public"
        assert TaintClassification.INTERNAL == "internal"
        assert TaintClassification.CONFIDENTIAL == "confidential"
        assert TaintClassification.RESTRICTED == "restricted"

        # Case-insensitivity via _missing_
        assert TaintClassification("PUBLIC") == TaintClassification.PUBLIC
        assert TaintClassification("Internal") == TaintClassification.INTERNAL
        assert TaintClassification("confidential") == TaintClassification.CONFIDENTIAL

        text_val = "Private equity fund performance metric."
        chunk = EvidenceChunk(
            chunk_id="chk_001",
            document_id="doc_123",
            text=text_val,
            page_number=1,
            start_char=10,
            end_char=10 + len(text_val),
            confidence=0.95,
            taint="confidential",
            metadata={"source": "report.pdf"},
        )
        assert chunk.chunk_id == "chk_001"
        assert chunk.document_id == "doc_123"
        assert chunk.text == text_val
        assert chunk.page_number == 1
        assert chunk.start_char == 10
        assert chunk.end_char == 10 + len(text_val)
        assert chunk.start_offset == 10
        assert chunk.end_offset == 10 + len(text_val)
        assert chunk.taint == TaintClassification.CONFIDENTIAL
        assert chunk.taint_classification == TaintClassification.CONFIDENTIAL
        assert chunk.confidence == 0.95
        assert chunk.is_table is False

    def test_evidence_chunk_invalid_offsets_raises(self) -> None:
        """Verify EvidenceChunk raises ValueError when start_char > end_char."""
        with pytest.raises(ValidationError, match=r"start_char .* must be <= end_char"):
            EvidenceChunk(
                chunk_id="chk_bad",
                document_id="doc_bad",
                text="Invalid span",
                start_char=100,
                end_char=50,
            )

    def test_evidence_chunk_span_length_mismatch_raises(self) -> None:
        """Verify EvidenceChunk raises ValidationError when span length != len(text)."""
        with pytest.raises(ValidationError, match=r"does not match text length"):
            EvidenceChunk(
                chunk_id="chk_bad_len",
                document_id="doc_bad",
                text="Exact five",
                start_char=0,
                end_char=20,  # 20 != len("Exact five") == 10
            )

    def test_page_map_entry_validation_and_properties(self) -> None:
        """Verify PageMapEntry construction, char_count, and offset aliases."""
        page = PageMapEntry(
            page_number=2,
            text="Executive summary content on page two.",
            start_offset=120,
            end_offset=158,
            utf16_start=120,
            utf16_end=158,
            confidence=0.98,
            is_scanned=False,
            ocr_skipped=False,
        )
        assert page.page_number == 2
        assert page.start_char == 120
        assert page.end_char == 158
        assert page.char_count == len("Executive summary content on page two.")
        assert page.confidence == 0.98

        # Test alias input: start_char and end_char
        page_alias = PageMapEntry(
            page_number=1,
            text="Short",
            start_char=0,
            end_char=5,
        )
        assert page_alias.start_offset == 0
        assert page_alias.end_offset == 5

    def test_page_map_entry_invalid_offsets_raises(self) -> None:
        """Verify PageMapEntry raises ValueError when start_offset > end_offset."""
        with pytest.raises(ValidationError, match=r"start_offset .* must be <= end_offset"):
            PageMapEntry(
                page_number=1,
                start_offset=200,
                end_offset=100,
            )

    def test_table_structure_validation_and_properties(self) -> None:
        """Verify TableStructure counts and headers/rows dimensions."""
        table = TableStructure(
            table_id="tbl_01",
            page_number=3,
            headers=["Fund Name", "Vintage", "IRR (%)"],
            rows=[
                ["Alpha Buyout Fund I", "2018", "18.5%"],
                ["Beta Growth Fund II", "2020", "22.1%"],
            ],
            caption="Historical Fund Performance",
            raw_html_or_md="| Fund Name | Vintage | IRR (%) |\n|---|---|---|\n",
        )
        assert table.table_id == "tbl_01"
        assert table.page_number == 3
        assert table.row_count == 2
        assert table.column_count == 3
        assert table.caption == "Historical Fund Performance"

    def test_table_structure_empty_rows(self) -> None:
        """Verify TableStructure handles empty rows gracefully."""
        table = TableStructure(table_id="tbl_empty", headers=["ColA", "ColB"])
        assert table.row_count == 0
        assert table.column_count == 2

        empty_table = TableStructure(table_id="tbl_none")
        assert empty_table.row_count == 0
        assert empty_table.column_count == 0

    def test_ingestion_result_validation_and_properties(self) -> None:
        """Verify IngestionResult convenience accessors and defaults."""
        result = IngestionResult(
            document_id="doc_abc",
            filename="financials.txt",
            mime_type="text/plain",
            total_pages=1,
            total_characters=45,
            normalized_text="Normalized clean plain text content for test.",
        )
        assert result.doc_id == "doc_abc"
        assert result.media_type == "text/plain"
        assert result.char_count == 45
        assert result.text == "Normalized clean plain text content for test."
        assert result.chunks == []
        assert result.page_map == []
        assert result.tables == []
        assert result.taint == TaintClassification.INTERNAL


# =============================================================================
# 2. Normalization and Code Unit Pipeline Tests
# =============================================================================


class TestNormalizationPipeline:
    """Test suite for Unicode NFC and LF line ending normalization."""

    def test_normalize_line_endings_crlf_and_cr(self) -> None:
        """Verify CRLF (\\r\\n) and legacy CR (\\r) are converted to LF (\\n)."""
        raw = "Line 1\r\nLine 2\rLine 3\nLine 4\r\n\r\nEnd"
        normalized = normalize_line_endings(raw)
        assert "\r" not in normalized
        assert normalized == "Line 1\nLine 2\nLine 3\nLine 4\n\nEnd"

    def test_normalize_line_endings_empty(self) -> None:
        """Verify empty string returns empty."""
        assert normalize_line_endings("") == ""

    def test_normalize_unicode_nfc(self) -> None:
        """Verify NFD decomposition (e + combining acute) is normalized to NFC (é)."""
        decomposed = "e\u0301"  # NFD
        composed = "\u00e9"  # NFC: é
        assert len(decomposed) == 2
        assert len(composed) == 1

        normalized = normalize_unicode_nfc(decomposed)
        assert len(normalized) == 1
        assert normalized == composed

    def test_normalize_text_combined(self) -> None:
        """Verify combined LF conversion and NFC normalization."""
        raw = "Pr\u0065\u0301sentation\r\nLine 2\rDone."
        result = normalize_text(raw)
        assert result == "Présentation\nLine 2\nDone."
        assert "\r" not in result

    def test_compute_utf16_code_units(self) -> None:
        """Verify UTF-16 code units calculation for ASCII and surrogate pairs."""
        ascii_text = "Hello world"
        assert compute_utf16_code_units(ascii_text) == 11

        # Emoji 🚀 has code point U+1F680 (> 0xFFFF), requiring 2 code units in UTF-16
        emoji_text = "Launch 🚀 now"
        # Length in Python code points: 12
        assert len(emoji_text) == 12
        # Length in UTF-16 code units: 13
        assert compute_utf16_code_units(emoji_text) == 13

    def test_compute_utf16_offset(self) -> None:
        """Verify conversion from Unicode code point offset to browser UTF-16 offset."""
        text = "Doc 🚀 with emoji and text."
        # Character indices:
        # 'D'(0), 'o'(1), 'c'(2), ' '(3), '🚀'(4), ' '(5), 'w'(6)...
        assert compute_utf16_offset(text, 0) == 0
        assert compute_utf16_offset(text, 4) == 4
        # Offset 5 is after emoji (1 char in Python, but 2 code units in UTF-16)
        assert compute_utf16_offset(text, 5) == 6
        assert compute_utf16_offset(text, 6) == 7


# =============================================================================
# 3. DocumentAdapter Registry and Resolution Tests
# =============================================================================


class DummyTestAdapter(BaseDocumentAdapter):
    """Custom adapter for testing registry resolution."""

    adapter_name: str = "dummy_custom"
    supported_mime_types = ("application/x-custom-doc",)
    supported_extensions = (".custom", ".cdoc")

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        text = data.decode("utf-8")
        return IngestionResult(
            document_id="dummy_id",
            filename=filename,
            mime_type=mime_type or "application/x-custom-doc",
            total_pages=1,
            total_characters=len(text),
            normalized_text=text,
        )


class TestAdapterRegistry:
    """Test suite for IngestionService adapter registration and resolution."""

    def test_default_adapters_include_plain_text(self) -> None:
        """Verify default IngestionService registers PlainTextDocumentAdapter."""
        service = IngestionService()
        adapters = service.list_adapters()
        assert len(adapters) == 1
        assert isinstance(adapters[0], PlainTextDocumentAdapter)

    def test_register_and_unregister_adapter(self) -> None:
        """Verify custom adapter can be registered and unregistered."""
        service = IngestionService()
        dummy = DummyTestAdapter()
        service.register_adapter(dummy)

        assert dummy in service.list_adapters()
        assert service.get_adapter_for_mime("application/x-custom-doc") is dummy
        assert service.get_adapter_for_extension(".custom") is dummy

        # Unregister
        assert service.unregister_adapter(dummy) is True
        assert dummy not in service.list_adapters()
        assert service.unregister_adapter(dummy) is False

    def test_priority_registration(self) -> None:
        """Verify priority=True registers adapter at front of chain."""
        service = IngestionService()
        default_adapter = service.list_adapters()[0]
        custom_adapter = DummyTestAdapter()

        service.register_adapter(custom_adapter, priority=True)
        adapters = service.list_adapters()
        assert adapters[0] is custom_adapter
        assert adapters[1] is default_adapter

    def test_resolution_by_mime_type(self) -> None:
        """Verify adapter resolution by exact and parameterized MIME types."""
        service = IngestionService()
        adapter = service.get_adapter(mime_type="text/plain")
        assert isinstance(adapter, PlainTextDocumentAdapter)

        # Parameterized MIME
        adapter_with_param = service.get_adapter(mime_type="text/plain; charset=utf-8")
        assert isinstance(adapter_with_param, PlainTextDocumentAdapter)

    def test_resolution_by_extension(self) -> None:
        """Verify adapter resolution by extension with and without leading dot."""
        service = IngestionService()
        adapter_dot = service.get_adapter_for_extension(".md")
        adapter_no_dot = service.get_adapter_for_extension("md")
        assert adapter_dot is not None
        assert adapter_no_dot is not None
        assert adapter_dot is adapter_no_dot

    def test_unsupported_format_raises_exception(self) -> None:
        """Verify resolving unsupported extension or MIME type raises UnsupportedFormatError."""
        service = IngestionService()
        with pytest.raises(UnsupportedFormatError, match="No document adapter available"):
            service.get_adapter(
                filename="archive.unknown_ext_xyz",
                mime_type="application/unknown-xyz",
            )


# =============================================================================
# 4. Chunking Pipeline and Character Offset Accuracy Tests
# =============================================================================


class TestChunkingPipeline:
    """Test suite for sentence-boundary aware chunking and exact offset verification."""

    def test_chunk_text_empty(self) -> None:
        """Verify empty text produces no chunks."""
        service = IngestionService()
        chunks = service.chunk_text("", document_id="doc_1")
        assert chunks == []

    def test_chunk_text_single_short_document(self) -> None:
        """Verify text smaller than chunk_size produces a single chunk."""
        service = IngestionService(default_chunk_size=500)
        text = "This is a brief single-sentence document."
        chunks = service.chunk_text(text, document_id="doc_1")

        assert len(chunks) == 1
        assert chunks[0].text == text
        assert chunks[0].start_char == 0
        assert chunks[0].end_char == len(text)
        assert text[chunks[0].start_char : chunks[0].end_char] == chunks[0].text

    def test_chunk_text_exact_char_offset_invariant(self) -> None:
        """MANDATORY INVARIANT: text[chunk.start_char : chunk.end_char] == chunk.text."""
        service = IngestionService(default_chunk_size=150, default_chunk_overlap=30)
        text = (
            "Private equity represents investments in companies not publicly traded on exchange. "
            "Investors typically commit capital for several years to fund strategic growth. "
            "Key return metrics include the Internal Rate of Return (IRR) and TVPI multiple. "
            "Exit strategies commonly involve initial public offerings (IPOs) or acquisitions. "
            "Governance is maintained through board seats and protective negative covenants."
        )

        chunks = service.chunk_text(text, document_id="pe_doc")
        assert len(chunks) > 1

        for i, chunk in enumerate(chunks):
            # Assert offsets are within bounds
            assert chunk.start_char is not None
            assert chunk.end_char is not None
            assert 0 <= chunk.start_char < chunk.end_char <= len(text)

            # Assert bit-for-bit slice equality
            slice_text = text[chunk.start_char : chunk.end_char]
            assert slice_text == chunk.text, f"Offset mismatch in chunk {i}"

    def test_chunk_text_sentence_boundary_awareness(self) -> None:
        """Verify chunking prefers breaking on sentence ending punctuation over words."""
        service = IngestionService(default_chunk_size=120, default_chunk_overlap=20)
        text = (
            "First sentence about financial modeling. "
            "Second sentence regarding risk metrics and volatility. "
            "Third sentence covering compliance and audits."
        )

        chunks = service.chunk_text(text, document_id="doc_sent")
        assert len(chunks) >= 2

        # First chunk should end with a period
        assert chunks[0].text.endswith(".")

    def test_chunk_text_page_mapping_resolution(self) -> None:
        """Verify chunks correctly inherit page_number from PageMapEntries."""
        service = IngestionService(default_chunk_size=100, default_chunk_overlap=20)
        page_1_text = "Page 1: Overview of the portfolio asset allocation and fund goals. "
        page_2_text = "Page 2: Detailed risk breakdown across private credit and debt instruments."
        full_text = page_1_text + page_2_text

        page_map = [
            PageMapEntry(
                page_number=1,
                text=page_1_text,
                start_offset=0,
                end_offset=len(page_1_text),
            ),
            PageMapEntry(
                page_number=2,
                text=page_2_text,
                start_offset=len(page_1_text),
                end_offset=len(full_text),
            ),
        ]

        chunks = service.chunk_text(full_text, document_id="doc_multipage", page_map=page_map)
        assert len(chunks) >= 2

        # Verify first chunk is mapped to page 1
        assert chunks[0].page_number == 1
        # Verify last chunk is mapped to page 2
        assert chunks[-1].page_number == 2

    def test_chunk_text_taint_propagation(self) -> None:
        """Verify evidence chunks inherit taint classification."""
        service = IngestionService()
        text = "Restricted confidential customer identification record."
        chunks = service.chunk_text(
            text,
            document_id="doc_tainted",
            taint=TaintClassification.RESTRICTED,
        )
        assert len(chunks) == 1
        assert chunks[0].taint == TaintClassification.RESTRICTED


# =============================================================================
# 5. End-to-End Ingestion Service Tests
# =============================================================================


class TestIngestionServiceE2E:
    """Test suite for end-to-end ingestion across bytes, streams, files, and text."""

    def test_ingest_text_convenience(self) -> None:
        """Verify ingest_text parses, normalizes, and chunks plain text."""
        service = IngestionService(default_chunk_size=200, default_chunk_overlap=40)
        raw_text = (
            "First paragraph of ontology definition.\r\n\r\n"
            "Second paragraph detailing relationships."
        )

        result = service.ingest_text(raw_text, filename="ontology.txt")
        assert result.filename == "ontology.txt"
        assert result.mime_type == "text/plain"
        assert "\r" not in result.text
        assert result.total_characters == len(result.text)
        assert len(result.chunks) > 0
        assert len(result.page_map) == 1
        assert result.source_sha256 is not None

        # Verify chunk offset invariance
        for chunk in result.chunks:
            assert result.text[chunk.start_char : chunk.end_char] == chunk.text

    def test_ingest_bytes_with_utf16_offsets(self) -> None:
        """Verify ingest_bytes automatically computes UTF-16 code unit offsets on page_map."""
        service = IngestionService()
        raw_bytes = "Strategic growth asset 🚀 valuation report.".encode()

        result = service.ingest_bytes(raw_bytes, filename="report.txt", mime_type="text/plain")
        assert len(result.page_map) == 1
        page = result.page_map[0]
        assert page.utf16_start == 0
        # Text has 1 emoji (1 char in Python, 2 code units in UTF-16)
        assert page.utf16_end == len(result.text) + 1

    def test_ingest_stream(self) -> None:
        """Verify ingest_stream parses binary stream content."""
        service = IngestionService()
        stream = io.BytesIO(b"Data stream content for unit test verification.")

        result = service.ingest_stream(stream, filename="stream.txt", mime_type="text/plain")
        assert result.text == "Data stream content for unit test verification."
        assert len(result.chunks) == 1

    def test_ingest_file(self, tmp_path: Path) -> None:
        """Verify ingest_file reads from disk path and parses correctly."""
        service = IngestionService()
        test_file = tmp_path / "sample_input.md"
        test_file.write_text("# Title\n\nBody content of markdown document.", encoding="utf-8")

        result = service.ingest_file(test_file)
        assert result.filename == "sample_input.md"
        assert "# Title" in result.text
        assert len(result.chunks) == 1

    def test_mocked_custom_adapter_integration(self) -> None:
        """Verify custom adapter parsing tabular structures through IngestionService."""

        class MockCsvAdapter(BaseDocumentAdapter):
            adapter_name = "mock_csv_custom"
            supported_mime_types = ("text/csv",)
            supported_extensions = (".csv",)

            def parse_bytes(
                self,
                data: bytes,
                filename: str,
                mime_type: str | None = None,
                **kwargs: Any,
            ) -> IngestionResult:
                text = data.decode("utf-8")
                table = TableStructure(
                    table_id="csv_tbl_1",
                    headers=["Concept", "Definition"],
                    rows=[
                        ["Company", "Legal corporate entity"],
                        ["Security", "Financial asset"],
                    ],
                )
                return IngestionResult(
                    document_id="mock_csv_doc",
                    filename=filename,
                    mime_type="text/csv",
                    total_pages=1,
                    total_characters=len(text),
                    normalized_text=text,
                    tables=[table],
                )

        service = IngestionService()
        service.register_adapter(MockCsvAdapter(), priority=True)

        csv_content = (
            b"Concept,Definition\nCompany,Legal corporate entity\nSecurity,Financial asset\n"
        )
        result = service.ingest_bytes(csv_content, filename="concepts.csv", mime_type="text/csv")

        assert result.document_id == "mock_csv_doc"
        assert len(result.tables) == 1
        assert result.tables[0].headers == ["Concept", "Definition"]
        assert len(result.tables[0].rows) == 2
        # Verify chunks were generated by IngestionService post-processing
        assert len(result.chunks) >= 1

    def test_non_normalizing_adapter_remapping_crlf_and_chunks(self) -> None:
        """Verify service remaps page_map and adapter-supplied chunks when adapter text has CRLF."""

        class NonNormalizingAdapter(BaseDocumentAdapter):
            adapter_name = "non_norm_custom"
            supported_mime_types = ("text/x-raw",)
            supported_extensions = (".raw",)

            def parse_bytes(
                self,
                data: bytes,
                filename: str,
                mime_type: str | None = None,
                **kwargs: Any,
            ) -> IngestionResult:
                # Returns raw text with CRLF line endings
                raw_text = "Page 1 heading\r\nPage 1 details\r\nPage 2 summary\r\n"
                # Page 1 covers "Page 1 heading\r\nPage 1 details\r\n" (0..32 in raw text)
                # Page 2 covers "Page 2 summary\r\n" (32..48 in raw text)
                page_1 = PageMapEntry(
                    page_number=1,
                    text="Page 1 heading\r\nPage 1 details\r\n",
                    start_offset=0,
                    end_offset=32,
                )
                page_2 = PageMapEntry(
                    page_number=2,
                    text="Page 2 summary\r\n",
                    start_offset=32,
                    end_offset=48,
                )
                # Adapter-supplied chunk in page 2
                raw_chunk_text = "Page 2 summary\r\n"
                chk = EvidenceChunk(
                    chunk_id="raw_chk_1",
                    document_id="doc_raw",
                    text=raw_chunk_text,
                    page_number=2,
                    start_char=32,
                    end_char=48,
                )
                return IngestionResult(
                    document_id="doc_raw",
                    filename=filename,
                    mime_type="text/x-raw",
                    total_pages=2,
                    total_characters=len(raw_text),
                    normalized_text=raw_text,
                    page_map=[page_1, page_2],
                    chunks=[chk],
                )

        service = IngestionService()
        service.register_adapter(NonNormalizingAdapter(), priority=True)

        result = service.ingest_bytes(b"dummy_bytes", filename="test.raw")

        # In normalized text, CRLF (\r\n) is converted to LF (\n)
        # "Page 1 heading\nPage 1 details\nPage 2 summary\n"
        # Page 1: 0..30 (was 0..32)
        # Page 2: 30..45 (was 32..48)
        assert "\r" not in result.text
        assert len(result.page_map) == 2

        p1 = result.page_map[0]
        p2 = result.page_map[1]
        assert p1.start_offset == 0
        assert p1.end_offset == 30
        assert result.text[p1.start_offset : p1.end_offset] == p1.text

        assert p2.start_offset == 30
        assert p2.end_offset == 45
        assert result.text[p2.start_offset : p2.end_offset] == p2.text

        # Verify adapter-supplied chunk was remapped and matches normalized text slice
        assert len(result.chunks) == 1
        remapped_chunk = result.chunks[0]
        assert remapped_chunk.start_char == 30
        assert remapped_chunk.end_char == 45
        slice_text = result.text[remapped_chunk.start_char : remapped_chunk.end_char]
        assert slice_text == remapped_chunk.text

        # Verify accurate UTF-16 code unit offsets after remapping (MAJ-01, MIN-07)
        assert p1.utf16_start == compute_utf16_offset(result.text, p1.start_offset)
        assert p1.utf16_end == compute_utf16_offset(result.text, p1.end_offset)
        assert p1.utf16_start == 0
        assert p1.utf16_end == 30

        assert p2.utf16_start == compute_utf16_offset(result.text, p2.start_offset)
        assert p2.utf16_end == compute_utf16_offset(result.text, p2.end_offset)
        assert p2.utf16_start == 30
        assert p2.utf16_end == 45

        expected_u_start = compute_utf16_offset(result.text, remapped_chunk.start_char)
        expected_u_end = compute_utf16_offset(result.text, remapped_chunk.end_char)
        assert remapped_chunk.utf16_start == expected_u_start
        assert remapped_chunk.utf16_end == expected_u_end
        assert remapped_chunk.utf16_start == 30
        assert remapped_chunk.utf16_end == 45

    def test_taint_propagation_override_adapter_downgrade(self) -> None:
        """Verify caller-specified taint is strictly enforced and not downgraded."""

        class IgnoringTaintAdapter(BaseDocumentAdapter):
            adapter_name = "ignoring_taint"
            supported_mime_types = ("text/plain",)
            supported_extensions = (".txt",)

            def parse_bytes(
                self,
                data: bytes,
                filename: str,
                mime_type: str | None = None,
                **kwargs: Any,
            ) -> IngestionResult:
                # Adapter unconditionally emits INTERNAL taint despite kwargs
                text = data.decode("utf-8")
                chunk = EvidenceChunk(
                    chunk_id="chk_internal",
                    document_id="doc_internal",
                    text=text,
                    start_char=0,
                    end_char=len(text),
                    taint=TaintClassification.INTERNAL,
                )
                return IngestionResult(
                    document_id="doc_internal",
                    filename=filename,
                    mime_type="text/plain",
                    total_pages=1,
                    total_characters=len(text),
                    normalized_text=text,
                    chunks=[chunk],
                    taint=TaintClassification.INTERNAL,
                )

        service = IngestionService()
        service.register_adapter(IgnoringTaintAdapter(), priority=True)

        result = service.ingest_bytes(
            b"Top secret document",
            filename="secret.txt",
            taint=TaintClassification.RESTRICTED,
        )

        # Assert result and chunks are NOT downgraded to INTERNAL
        assert result.taint == TaintClassification.RESTRICTED
        assert len(result.chunks) == 1
        assert result.chunks[0].taint == TaintClassification.RESTRICTED

    def test_plain_text_adapter_mime_type_inference(self) -> None:
        """Verify PlainTextDocumentAdapter infers MIME type from filename extension."""
        adapter = PlainTextDocumentAdapter()
        res_json = adapter.parse_bytes(b'{"key": "value"}', filename="data.json")
        assert res_json.mime_type == "application/json"

        res_md = adapter.parse_bytes(b"# Header", filename="readme.md")
        assert res_md.mime_type in ("text/markdown", "text/x-markdown")

        res_csv = adapter.parse_bytes(b"a,b,c\n1,2,3", filename="records.csv")
        assert res_csv.mime_type == "text/csv"

    def test_configuration_validation_raises(self) -> None:
        """Verify invalid chunk_size or chunk_overlap raises ValueError."""
        with pytest.raises(ValueError, match="default_chunk_size must be >= 1"):
            IngestionService(default_chunk_size=0)

        with pytest.raises(
            ValueError, match=r"default_chunk_overlap .* must not exceed default_chunk_size // 2"
        ):
            IngestionService(default_chunk_size=100, default_chunk_overlap=60)

        service = IngestionService(default_chunk_size=500, default_chunk_overlap=50)
        with pytest.raises(ValueError, match=r"chunk_overlap .* must not exceed chunk_size // 2"):
            service.chunk_text("Sample text", document_id="doc_1", chunk_size=50, chunk_overlap=30)

    def test_max_taint_high_water_mark(self) -> None:
        """Verify max_taint strictly obeys the classification order."""
        assert max_taint("public", "internal") == TaintClassification.INTERNAL
        assert max_taint("internal", "confidential") == TaintClassification.CONFIDENTIAL
        assert max_taint("confidential", "restricted") == TaintClassification.RESTRICTED
        assert max_taint("public", "restricted") == TaintClassification.RESTRICTED
        assert max_taint(None, "confidential") == TaintClassification.CONFIDENTIAL
        assert max_taint(None, None) == TaintClassification.INTERNAL

    def test_build_normalization_offset_map_monotonicity_and_nfd(self) -> None:
        """Verify build_normalization_offset_map on NFD and CRLF input is monotonic."""
        # Decomposed e + acute ("e\u0301") followed by CRLF and text
        raw = "Pr\u0065\u0301sentation\r\nLine 2"
        norm_text, offset_map = build_normalization_offset_map(raw)

        assert norm_text == "Présentation\nLine 2"
        assert len(offset_map) == len(raw) + 1
        # Monotonicity check
        for i in range(len(offset_map) - 1):
            assert offset_map[i] <= offset_map[i + 1]

        # Check mapped endpoints
        assert offset_map[0] == 0
        assert offset_map[len(raw)] == len(norm_text)

    def test_hangul_and_indic_nfc_compositions(self) -> None:
        """Verify compositions where both characters have combining class 0 (Hangul, Indic)."""
        # Hangul conjoining jamo: L (U+1100), V (U+1161), T (U+11A8) -> GAK (U+AC01)
        hangul_raw = "Start \u1100\u1161\u11a8 End"
        h_norm, h_map = build_normalization_offset_map(hangul_raw)
        assert h_norm == unicodedata.normalize("NFC", hangul_raw)
        assert len(h_map) == len(hangul_raw) + 1
        assert all(h_map[i] <= h_map[i + 1] for i in range(len(h_map) - 1))

        # Indic Oriya split vowel: U+0B47 + U+0B3E -> U+0B4B
        indic_raw = "Prefix \u0b47\u0b3e Suffix"
        i_norm, i_map = build_normalization_offset_map(indic_raw)
        assert i_norm == unicodedata.normalize("NFC", indic_raw)
        assert len(i_map) == len(indic_raw) + 1
        assert all(i_map[i] <= i_map[i + 1] for i in range(len(i_map) - 1))

    def test_utf16_and_utf32_bom_handling(self) -> None:
        """Verify UTF-16, UTF-32, and UTF-8 BOM detection, decoding, and stripping."""
        service = IngestionService()

        # UTF-16 LE with BOM: \xff\xfe
        utf16_bytes = "BOM test text".encode("utf-16")
        res_utf16 = service.ingest_bytes(utf16_bytes, filename="utf16.txt")
        assert not res_utf16.normalized_text.startswith("\ufeff")
        assert res_utf16.normalized_text == "BOM test text"

        # UTF-32 LE with BOM: \xff\xfe\x00\x00
        utf32_bytes = "UTF-32 test content".encode("utf-32")
        res_utf32 = service.ingest_bytes(utf32_bytes, filename="utf32.txt")
        assert not res_utf32.normalized_text.startswith("\ufeff")
        assert res_utf32.normalized_text == "UTF-32 test content"

        # Corrupt explicit BOM raises ValueError
        corrupt_utf16 = b"\xff\xfe\x00"  # odd byte length
        with pytest.raises(ValueError, match="Corrupt text file with explicit BOM"):
            service.ingest_bytes(corrupt_utf16, filename="bad_utf16.txt")

    def test_generated_chunks_carry_source_sha256(self) -> None:
        """Verify chunks produced by IngestionService carry source_sha256 digest."""
        service = IngestionService(default_chunk_size=30, default_chunk_overlap=10)
        data = b"This is a sample document for testing chunk source_sha256 propagation."
        res = service.ingest_bytes(data, filename="sample.txt")

        assert res.source_sha256 is not None
        assert len(res.chunks) > 1
        for chk in res.chunks:
            assert chk.source_sha256 == res.source_sha256

    def test_adapter_out_of_bounds_chunk_raises(self) -> None:
        """Verify adapter producing out-of-bounds chunk offsets raises descriptive ValueError."""

        class BadAdapter(PlainTextDocumentAdapter):
            def parse_bytes(self, data: bytes, filename: str, **kwargs: Any) -> IngestionResult:
                res = super().parse_bytes(data, filename, **kwargs)
                bad_chunk = EvidenceChunk(
                    chunk_id="bad_chk_01",
                    document_id=res.document_id,
                    text="Overflow",
                    start_char=500,
                    end_char=508,
                    confidence=1.0,
                )
                res.chunks.append(bad_chunk)
                return res

        service = IngestionService(adapters=[BadAdapter()])
        with pytest.raises(ValueError, match="produced chunk 'bad_chk_01' with end_char 508"):
            service.ingest_bytes(b"Short text", filename="bad.txt")

    def test_adapter_chunks_document_id_enforced(self) -> None:
        """Verify remapped chunks have document_id matching the IngestionResult."""

        class ForeignIdAdapter(PlainTextDocumentAdapter):
            def parse_bytes(self, data: bytes, filename: str, **kwargs: Any) -> IngestionResult:
                res = super().parse_bytes(data, filename, **kwargs)
                foreign_chunk = EvidenceChunk(
                    chunk_id="chk_foreign",
                    document_id="wrong_doc_id",
                    text="Hello",
                    start_char=0,
                    end_char=5,
                    confidence=1.0,
                )
                res.chunks.append(foreign_chunk)
                return res

        service = IngestionService(adapters=[ForeignIdAdapter()])
        res = service.ingest_bytes(b"Hello World", filename="foreign.txt")
        assert len(res.chunks) == 1
        assert res.chunks[0].document_id == res.document_id

    def test_ingest_file_non_regular_file_raises(self, tmp_path: Path) -> None:
        """Verify ingest_file rejects directories or non-regular files."""
        service = IngestionService()
        sub_dir = tmp_path / "sub_directory"
        sub_dir.mkdir()

        with pytest.raises(ValueError, match="Path is not a regular file"):
            service.ingest_file(sub_dir)

    def test_compute_utf16_offset_map_table(self) -> None:
        """Verify compute_utf16_offset_map matches compute_utf16_offset for all points."""
        text = "Strategic 🚀 growth 🌍 report"
        table = compute_utf16_offset_map(text)
        assert len(table) == len(text) + 1

        for i in range(len(text) + 1):
            assert table[i] == compute_utf16_offset(text, i)

    def test_ingestion_result_roundtrip_model_validate(self) -> None:
        """Verify IngestionResult serialized to JSON round-trips through model_validate."""
        service = IngestionService()
        result = service.ingest_text("Roundtrip test text content.", filename="doc.txt")
        dumped = result.model_dump(mode="json", exclude_none=True)
        reloaded = IngestionResult.model_validate(dumped)

        assert reloaded.document_id == result.document_id
        assert reloaded.total_characters == result.total_characters
        assert len(reloaded.chunks) == len(result.chunks)
        assert len(reloaded.page_map) == len(result.page_map)
        assert reloaded.taint == result.taint

    def test_security_limits_max_bytes_rejection(self, tmp_path: Path) -> None:
        """Verify max_bytes limits are enforced across bytes, stream, and file ingestion."""
        service = IngestionService(max_bytes=30)
        oversized_data = b"This data is definitely longer than thirty bytes."

        with pytest.raises(ValueError, match="exceeds maximum limit"):
            service.ingest_bytes(oversized_data, filename="big.txt")

        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            service.ingest_stream(io.BytesIO(oversized_data), filename="big.txt")

        f_path = tmp_path / "oversized.txt"
        f_path.write_bytes(oversized_data)
        with pytest.raises(ValueError, match="exceeds maximum allowed size"):
            service.ingest_file(f_path)

    def test_security_limits_max_chunks_rejection(self) -> None:
        """Verify max_chunks limits are enforced in chunk_text and for adapter chunks."""
        service = IngestionService(default_chunk_size=20, default_chunk_overlap=5, max_chunks=3)
        sample = "One sentence here. Two sentence here. Three sentence here. Four sentence here."

        with pytest.raises(ValueError, match="exceeded safety limit of 3 chunks"):
            service.chunk_text(sample, document_id="doc_overflow")

        class ProlificAdapter(PlainTextDocumentAdapter):
            def parse_bytes(self, data: bytes, filename: str, **kwargs: Any) -> IngestionResult:
                res = super().parse_bytes(data, filename, **kwargs)
                chunks = [
                    EvidenceChunk(
                        chunk_id=f"chk_{i}",
                        document_id=res.document_id,
                        text="A",
                        start_char=0,
                        end_char=1,
                    )
                    for i in range(10)
                ]
                return res.model_copy(update={"chunks": chunks})

        service_prolific = IngestionService(adapters=[ProlificAdapter()], max_chunks=5)
        with pytest.raises(ValueError, match="exceeding max_chunks limit of 5"):
            service_prolific.ingest_bytes(b"A", filename="prolific.txt")

    def test_chunk_overlap_and_boundary_alignment(self) -> None:
        """Verify consecutive chunks have valid overlap and advance on word boundaries."""
        service = IngestionService(default_chunk_size=40, default_chunk_overlap=15)
        text = "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet Kilo Lima Mike."
        chunks = service.chunk_text(text, document_id="doc_overlap")

        assert len(chunks) >= 2
        for i in range(len(chunks) - 1):
            curr_c = chunks[i]
            next_c = chunks[i + 1]
            # Ensure consecutive chunks advance monotonically
            assert next_c.start_char is not None and curr_c.start_char is not None
            assert next_c.start_char > curr_c.start_char
            assert curr_c.end_char is not None
            # Overlap region exists
            assert next_c.start_char < curr_c.end_char
            # Overlap does not exceed chunk_size // 2
            overlap_len = curr_c.end_char - next_c.start_char
            assert overlap_len <= 20

    def test_path_confinement_and_symlinks(self, tmp_path: Path) -> None:
        """Verify ingest_file enforces allowed_roots and rejects symlinks."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        forbidden_dir = tmp_path / "forbidden"
        forbidden_dir.mkdir()

        valid_file = allowed_dir / "valid.txt"
        valid_file.write_text("Valid content", encoding="utf-8")

        outside_file = forbidden_dir / "outside.txt"
        outside_file.write_text("Outside content", encoding="utf-8")

        service = IngestionService()
        # Outside allowed roots
        with pytest.raises(ValueError, match="does not resolve within any allowed roots"):
            service.ingest_file(outside_file, allowed_roots=[allowed_dir])

        # Allowed root succeeds
        res = service.ingest_file(valid_file, allowed_roots=[allowed_dir])
        assert res.normalized_text == "Valid content"

        # Symlink check
        symlink_file = allowed_dir / "symlink.txt"
        try:
            symlink_file.symlink_to(valid_file)
            with pytest.raises(ValueError, match="Symlinks are prohibited"):
                service.ingest_file(symlink_file)
        except OSError:
            pytest.skip("Symlink creation not permitted in this environment")

    def test_plain_text_adapter_metadata_none(self) -> None:
        """Verify PlainTextDocumentAdapter handles metadata=None safely."""
        adapter = PlainTextDocumentAdapter()
        res = adapter.parse_bytes(b"Sample data", filename="test.txt", metadata=None)
        assert res.metadata["encoding"] == "utf-8"

    def test_chunk_utf16_offsets_populated(self) -> None:
        """Verify created EvidenceChunks have accurate utf16_start and utf16_end offsets."""
        service = IngestionService(default_chunk_size=30, default_chunk_overlap=10)
        text = "Hello 🚀 world! Strategic 🌍 mission."
        res = service.ingest_text(text)

        assert len(res.chunks) >= 2
        for chk in res.chunks:
            assert chk.utf16_start is not None
            assert chk.utf16_end is not None
            assert chk.start_char is not None
            assert chk.end_char is not None
            assert chk.utf16_start == compute_utf16_offset(text, chk.start_char)
            assert chk.utf16_end == compute_utf16_offset(text, chk.end_char)

    def test_adapter_chunks_raise_document_taint_high_water_mark(self) -> None:
        """Verify document taint is raised when an adapter chunk has higher taint (MAJ-02)."""

        class TaintUpgradeAdapter(BaseDocumentAdapter):
            adapter_name = "taint_upgrade"
            supported_mime_types = ("text/x-taint",)
            supported_extensions = (".taint",)

            def parse_bytes(self, data: bytes, filename: str, **kwargs: Any) -> IngestionResult:
                text = "Section 1 public info. Section 2 top secret restricted info."
                chunk_public = EvidenceChunk(
                    chunk_id="chk_pub",
                    document_id="doc_t",
                    text="Section 1 public info. ",
                    start_char=0,
                    end_char=23,
                    taint=TaintClassification.PUBLIC,
                )
                chunk_restricted = EvidenceChunk(
                    chunk_id="chk_sec",
                    document_id="doc_t",
                    text="Section 2 top secret restricted info.",
                    start_char=23,
                    end_char=60,
                    taint=TaintClassification.RESTRICTED,
                )
                return IngestionResult(
                    document_id="doc_t",
                    filename=filename,
                    mime_type="text/x-taint",
                    total_pages=1,
                    total_characters=len(text),
                    normalized_text=text,
                    taint=TaintClassification.INTERNAL,
                    chunks=[chunk_public, chunk_restricted],
                )

        service = IngestionService(adapters=[TaintUpgradeAdapter()])
        result = service.ingest_bytes(b"dummy", filename="sample.taint")

        # Document taint must be elevated to RESTRICTED because of chunk_restricted
        assert result.taint == TaintClassification.RESTRICTED
        assert len(result.chunks) == 2
        for chk in result.chunks:
            assert chk.taint == TaintClassification.RESTRICTED

    def test_chunk_size_without_explicit_overlap_uses_min_half_size(self) -> None:
        """Verify chunk_size < overlap * 2 does not error when overlap is None (MIN-01)."""
        service = IngestionService(default_chunk_size=800, default_chunk_overlap=100)
        # Passing chunk_size=150 with chunk_overlap=None should not error
        text = "Short text with sentence one. Sentence two follows here."
        chunks = service.chunk_text(text, document_id="doc_min01", chunk_size=150)
        assert len(chunks) >= 1

    def test_plain_text_adapter_rejects_binary_data(self) -> None:
        """Verify PlainTextDocumentAdapter rejects binary files with NUL bytes (MIN-05)."""
        adapter = PlainTextDocumentAdapter()
        binary_payload = b"%PDF-1.7\x00\x01\x02\x03binary-stream"
        with pytest.raises(UnsupportedFormatError, match="binary data"):
            adapter.parse_bytes(binary_payload, filename="file.txt")

    def test_unsupported_explicit_mime_raises_error(self) -> None:
        """Verify explicit unsupported MIME type raises UnsupportedFormatError (MIN-05)."""
        service = IngestionService()
        err_msg = "No document adapter available for explicit mime_type"
        with pytest.raises(UnsupportedFormatError, match=err_msg):
            service.get_adapter(filename="document.txt", mime_type="application/x-custom-unknown")

    def test_dropped_chunks_metadata_no_in_place_mutation(self) -> None:
        """Verify dropped_chunks does not mutate adapter metadata in place (MIN-06)."""
        orig_meta = {"source": "adapter", "dropped_chunks": ("pre_existing_dropped",)}

        class DroppingAdapter(BaseDocumentAdapter):
            adapter_name = "dropping_adapter"
            supported_mime_types = ("text/x-drop",)
            supported_extensions = (".drop",)

            def parse_bytes(self, data: bytes, filename: str, **kwargs: Any) -> IngestionResult:
                chk = EvidenceChunk.model_construct(
                    chunk_id="chk_empty",
                    document_id="doc_d",
                    text="",  # Empty text will be dropped
                )
                return IngestionResult(
                    document_id="doc_d",
                    filename=filename,
                    mime_type="text/x-drop",
                    total_pages=1,
                    total_characters=0,
                    normalized_text="",
                    metadata=orig_meta,
                    chunks=[chk],
                )

        service = IngestionService(adapters=[DroppingAdapter()])
        result = service.ingest_bytes(b"dummy", filename="doc.drop")
        assert "chk_empty" in result.metadata["dropped_chunks"]
        # Original metadata tuple remains untouched
        assert orig_meta["dropped_chunks"] == ("pre_existing_dropped",)

    def test_ingest_file_binary_mode_windows_crlf_and_ctrl_z(self, tmp_path: Path) -> None:
        """Verify ingest_file reads in binary mode on Windows with CRLF and 0x1A (MAJ-01)."""
        content = b"Line 1\r\nLine 2\x1aEnd of stream content."
        file_path = tmp_path / "ctrl_z.txt"
        file_path.write_bytes(content)

        service = IngestionService()
        result = service.ingest_file(file_path)

        # Hash must match raw disk bytes exactly
        import hashlib

        expected_hash = hashlib.sha256(content).hexdigest()
        assert result.source_sha256 == expected_hash

        # Content past 0x1A must NOT be truncated
        assert "End of stream content." in result.normalized_text

    def test_ingest_text_without_mime_type_uses_filename_extension(self) -> None:
        """Verify ingest_text with mime_type=None resolves from filename extension (MIN-06)."""
        service = IngestionService()
        result = service.ingest_text('{"key": "value"}', filename="data.json")
        assert result.mime_type == "application/json"

    def test_normalization_slice_correspondence(self) -> None:
        """Verify build_normalization_offset_map maps cluster slices accurately (MIN-07)."""
        # Mixed CRLF and NFD text
        raw = "Caf\u0065\u0301\r\ngrowth\r\n\u1100\u1161"
        norm, offset_map = build_normalization_offset_map(raw)

        # "Café\ngrowth\n가"
        assert "\r" not in norm
        assert unicodedata.is_normalized("NFC", norm)

        # Validate that mapped slice starts correspond to starter characters
        assert offset_map[0] == 0
        assert norm[offset_map[0] : offset_map[5]] == "Café"
        assert norm[offset_map[5] : offset_map[7]] == "\n"
        assert norm[offset_map[7] : offset_map[13]] == "growth"
        assert norm[offset_map[13] : offset_map[15]] == "\n"
        assert norm[offset_map[15] : offset_map[len(raw)]] == "가"
