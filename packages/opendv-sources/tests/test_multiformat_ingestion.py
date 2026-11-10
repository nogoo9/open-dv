"""Comprehensive unit and integration tests for Multi-Format Ingestion Adapters.

Tests:
- MarkdownDocumentAdapter: Headings, fenced code blocks, table extraction, quarantine.
- DelimitedTabularAdapter: CSV/TSV, headers, type inference, 32k cell truncation.
- PyArrowTabularAdapter: Parquet schema, metadata, preview chunks, invalid bytes.
- StructuredDataDocumentAdapter: JSON, JSONL, YAML, unnesting, depth limits (32).
- MarkupDocumentAdapter: HTML/XML, readability, script/style/nav stripping, tables.
- LibreOfficeDocumentAdapter: Mock headless soffice conversion, missing binary 501.
- IngestionService integration and adapter registry resolution.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    PageMapEntry,
    TaintClassification,
)
from opendv_sources.adapters.libreoffice_adapter import (
    LibreOfficeDocumentAdapter,
    find_soffice_binary,
)
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    LiteParseDocumentAdapter,
)
from opendv_sources.adapters.multiformat_adapter import (
    DelimitedTabularAdapter,
    MarkdownDocumentAdapter,
    MarkupDocumentAdapter,
    PyArrowTabularAdapter,
    StructuredDataDocumentAdapter,
    decode_text_payload,
)
from opendv_sources.exceptions import (
    DocumentExtractionError,
    OfficeConversionUnavailableError,
    UnsupportedFormatError,
)
from opendv_sources.ingestion.service import IngestionService

# =============================================================================
# 1. MarkdownDocumentAdapter Tests
# =============================================================================


class TestMarkdownDocumentAdapter:
    """Test suite for MarkdownDocumentAdapter."""

    def test_supported_types_and_extensions(self) -> None:
        adapter = MarkdownDocumentAdapter()
        assert adapter.supports_extension(".md")
        assert adapter.supports_extension(".markdown")
        assert adapter.supports_extension("md")
        assert not adapter.supports_extension(".txt")
        assert adapter.supports_mime_type("text/markdown")
        assert adapter.supports_mime_type("text/x-markdown")
        assert not adapter.supports_mime_type("application/pdf")

    def test_heading_hierarchy_extraction(self) -> None:
        md_text = (
            "# Main Title\n\n"
            "Intro paragraph.\n\n"
            "## Section 1\n\n"
            "Details here.\n\n"
            "### Subsection 1.1\n\n"
            "Deep notes.\n"
        )
        adapter = MarkdownDocumentAdapter()
        result = adapter.parse_bytes(md_text.encode("utf-8"), filename="test.md")

        assert result.total_pages == 1
        assert "Main Title" in result.text
        headings = result.metadata["headings"]
        assert len(headings) == 3
        assert headings[0]["level"] == 1
        assert headings[0]["title"] == "Main Title"
        assert headings[1]["level"] == 2
        assert headings[1]["title"] == "Section 1"
        assert headings[2]["level"] == 3
        assert headings[2]["title"] == "Subsection 1.1"

    def test_fenced_code_blocks_quarantined(self) -> None:
        md_text = (
            "# Code Example\n\n"
            "Here is python:\n\n"
            "```python\n"
            "def hello():\n"
            "    print('world')\n"
            "```\n\n"
            "End of doc.\n"
        )
        adapter = MarkdownDocumentAdapter()
        result = adapter.parse_bytes(md_text.encode("utf-8"), filename="code.md")

        code_blocks = result.metadata["code_blocks"]
        assert len(code_blocks) == 1
        assert code_blocks[0]["language"] == "python"
        assert "def hello():" in code_blocks[0]["code"]
        assert result.metadata["code_block_count"] == 1

    def test_exclude_code_blocks_flag(self) -> None:
        md_text = (
            "# Secure Spec\n\n"
            "Narrative text.\n\n"
            "```bash\n"
            "rm -rf /tmp/sensitive\n"
            "```\n\n"
            "Conclusion.\n"
        )
        adapter = MarkdownDocumentAdapter(exclude_code_blocks=True)
        result = adapter.parse_bytes(md_text.encode("utf-8"), filename="sec.md")

        assert "rm -rf /tmp/sensitive" not in result.text
        assert "[CODE BLOCK QUARANTINED]" in result.text
        assert len(result.metadata["code_blocks"]) == 1

    def test_markdown_table_extraction(self) -> None:
        md_text = (
            "# Data Report\n\n"
            "| Entity | Type | Status |\n"
            "| :--- | :--- | :--- |\n"
            "| Customer | Class | Active |\n"
            "| Account | Class | Pending |\n\n"
            "Footer note.\n"
        )
        adapter = MarkdownDocumentAdapter()
        result = adapter.parse_bytes(md_text.encode("utf-8"), filename="table.md")

        assert len(result.tables) == 1
        table = result.tables[0]
        assert table.headers == ["Entity", "Type", "Status"]
        assert len(table.rows) == 2
        assert table.rows[0] == ["Customer", "Class", "Active"]
        assert table.rows[1] == ["Account", "Class", "Pending"]
        assert result.page_map[0].has_tables is True

    def test_empty_markdown_raises_error(self) -> None:
        adapter = MarkdownDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="is empty"):
            adapter.parse_bytes(b"", filename="empty.md")

    def test_taint_and_sha256_propagation(self) -> None:
        content = b"# Classified Plan\n\nSecret details.\n"
        expected_sha = hashlib.sha256(content).hexdigest()
        adapter = MarkdownDocumentAdapter()
        result = adapter.parse_bytes(
            content,
            filename="classified.md",
            taint=TaintClassification.RESTRICTED,
            source_sha256=expected_sha,
        )

        assert result.taint == TaintClassification.RESTRICTED
        assert result.source_sha256 == expected_sha

    def test_sha256_mismatch_raises_extraction_error(self) -> None:
        content = b"# Document\n\nText.\n"
        adapter = MarkdownDocumentAdapter()
        with pytest.raises(DocumentExtractionError, match="does not match computed digest"):
            adapter.parse_bytes(content, filename="test.md", source_sha256="0" * 64)


# =============================================================================
# 2. DelimitedTabularAdapter Tests (CSV / TSV)
# =============================================================================


class TestDelimitedTabularAdapter:
    """Test suite for DelimitedTabularAdapter."""

    def test_supported_types_and_extensions(self) -> None:
        adapter = DelimitedTabularAdapter()
        assert adapter.supports_extension(".csv")
        assert adapter.supports_extension(".tsv")
        assert adapter.supports_mime_type("text/csv")
        assert adapter.supports_mime_type("text/tab-separated-values")

    def test_csv_parsing_and_type_inference(self) -> None:
        csv_data = (
            b"id,name,active,balance,created_at\n"
            b"1,Alice,true,1050.50,2024-01-15\n"
            b"2,Bob,false,200.00,2024-02-20\n"
            b"3,Charlie,true,0.00,2024-03-10\n"
        )
        adapter = DelimitedTabularAdapter()
        result = adapter.parse_bytes(csv_data, filename="accounts.csv")

        assert len(result.tables) == 1
        tbl = result.tables[0]
        assert tbl.headers == ["id", "name", "active", "balance", "created_at"]
        assert len(tbl.rows) == 3
        types = result.metadata["column_types"]
        assert types["id"] == "integer"
        assert types["name"] == "string"
        assert types["active"] == "boolean"
        assert types["balance"] == "float"
        assert result.metadata["row_count"] == 3
        assert result.metadata["column_count"] == 5

    def test_tsv_parsing(self) -> None:
        tsv_data = (
            b"concept\tparent\tdefinition\n"
            b"Party\tThing\tA legal person or organization\n"
            b"Account\tContract\tFinancial account\n"
        )
        adapter = DelimitedTabularAdapter()
        result = adapter.parse_bytes(tsv_data, filename="concepts.tsv")

        assert len(result.tables) == 1
        tbl = result.tables[0]
        assert tbl.headers == ["concept", "parent", "definition"]
        assert len(tbl.rows) == 2
        assert result.metadata["delimiter"] == "\\t"

    def test_cell_truncation_guard_over_32k(self) -> None:
        huge_cell = "X" * 40_000
        csv_data = f"id,description\n1,{huge_cell}\n2,Normal cell\n".encode()
        adapter = DelimitedTabularAdapter(max_cell_chars=32_768)
        result = adapter.parse_bytes(csv_data, filename="large_cells.csv")

        tbl = result.tables[0]
        truncated_val = tbl.rows[0][1]
        assert len(truncated_val) == 32_768 + len("…[TRUNCATED]")
        assert truncated_val.endswith("…[TRUNCATED]")
        assert tbl.rows[1][1] == "Normal cell"
        assert result.metadata["truncated_cells_count"] == 1

    def test_empty_csv_raises_error(self) -> None:
        adapter = DelimitedTabularAdapter()
        with pytest.raises(InvalidDocumentError, match="is empty"):
            adapter.parse_bytes(b"", filename="empty.csv")


# =============================================================================
# 3. PyArrowTabularAdapter Tests (Parquet)
# =============================================================================


class TestPyArrowTabularAdapter:
    """Test suite for PyArrowTabularAdapter."""

    @pytest.fixture
    def sample_parquet_bytes(self) -> bytes:
        data = {
            "account_id": [101, 102, 103],
            "customer_name": ["Alice Corp", "Bob LLC", "Charlie Inc"],
            "balance": [12500.5, 450.0, 99000.75],
            "is_vip": [True, False, True],
        }
        table = pa.Table.from_pydict(
            data,
            metadata={b"data_origin": b"fibo_benchmark", b"env": b"test"},
        )
        sink = io.BytesIO()
        pq.write_table(table, sink)
        return sink.getvalue()

    def test_supported_types_and_extensions(self) -> None:
        adapter = PyArrowTabularAdapter()
        assert adapter.supports_extension(".parquet")
        assert adapter.supports_extension("parquet")
        assert adapter.supports_mime_type("application/vnd.apache.parquet")
        assert adapter.supports_mime_type("application/parquet")

    def test_parquet_schema_and_preview_extraction(self, sample_parquet_bytes: bytes) -> None:
        adapter = PyArrowTabularAdapter(preview_rows=10)
        result = adapter.parse_bytes(sample_parquet_bytes, filename="customers.parquet")

        assert result.total_pages == 1
        assert "Parquet Dataset: customers.parquet" in result.text
        assert result.metadata["num_rows"] == 3
        assert result.metadata["num_columns"] == 4
        columns = result.metadata["columns"]
        col_names = [c["name"] for c in columns]
        assert col_names == ["account_id", "customer_name", "balance", "is_vip"]
        assert result.metadata["custom_metadata"]["data_origin"] == "fibo_benchmark"

        # Check preview table
        assert len(result.tables) == 1
        tbl = result.tables[0]
        assert tbl.headers == ["account_id", "customer_name", "balance", "is_vip"]
        assert len(tbl.rows) == 3
        assert tbl.rows[0][1] == "Alice Corp"

    def test_invalid_parquet_header_raises_error(self) -> None:
        adapter = PyArrowTabularAdapter()
        with pytest.raises(InvalidDocumentError, match="missing 'PAR1' magic"):
            adapter.parse_bytes(b"NOT_A_PARQUET_FILE", filename="corrupt.parquet")

    def test_empty_parquet_raises_error(self) -> None:
        adapter = PyArrowTabularAdapter()
        with pytest.raises(InvalidDocumentError, match="is empty"):
            adapter.parse_bytes(b"", filename="empty.parquet")

    def test_missing_pyarrow_graceful_fallback(self, sample_parquet_bytes: bytes) -> None:
        with patch("opendv_sources.adapters.multiformat_adapter.HAS_PYARROW", False):
            adapter = PyArrowTabularAdapter()
            with pytest.raises(UnsupportedFormatError, match="PyArrow library is not installed"):
                adapter.parse_bytes(sample_parquet_bytes, filename="test.parquet")


# =============================================================================
# 4. StructuredDataDocumentAdapter Tests (JSON, JSONL, YAML)
# =============================================================================


class TestStructuredDataDocumentAdapter:
    """Test suite for StructuredDataDocumentAdapter."""

    def test_supported_types_and_extensions(self) -> None:
        adapter = StructuredDataDocumentAdapter()
        assert adapter.supports_extension(".json")
        assert adapter.supports_extension(".jsonl")
        assert adapter.supports_extension(".yaml")
        assert adapter.supports_extension(".yml")
        assert adapter.supports_mime_type("application/json")
        assert adapter.supports_mime_type("application/x-jsonlines")
        assert adapter.supports_mime_type("application/x-yaml")

    def test_json_parsing_and_unnesting(self) -> None:
        payload = {
            "version": "1.0",
            "ontology": {
                "name": "FinancialServices",
                "entities": [
                    {"id": "ENT-1", "label": "Security"},
                    {"id": "ENT-2", "label": "Bond"},
                ],
            },
        }
        json_bytes = json.dumps(payload).encode("utf-8")
        adapter = StructuredDataDocumentAdapter()
        result = adapter.parse_bytes(json_bytes, filename="model.json")

        assert result.metadata["max_depth"] == 5
        assert "version" in result.metadata["fields"]
        assert "ontology" in result.metadata["fields"]
        assert "entities" in result.metadata["fields"]
        assert "ontology.entities[0].label" in result.metadata["json_paths_sample"]
        assert "FinancialServices" in result.text

    def test_json_lines_parsing(self) -> None:
        jsonl_data = (
            b'{"id": 1, "term": "Equity", "category": "Instrument"}\n'
            b'{"id": 2, "term": "Option", "category": "Derivative"}\n'
        )
        adapter = StructuredDataDocumentAdapter()
        result = adapter.parse_bytes(jsonl_data, filename="terms.jsonl")

        assert result.mime_type == "application/x-jsonlines"
        assert len(result.tables) == 1
        tbl = result.tables[0]
        assert tbl.headers == ["id", "term", "category"]
        assert len(tbl.rows) == 2
        assert tbl.rows[0] == ["1", "Equity", "Instrument"]

    def test_yaml_parsing(self) -> None:
        yaml_data = (
            b"schema_version: 2.0\n"
            b"service:\n"
            b"  name: ontology-studio\n"
            b"  port: 8080\n"
            b"features:\n"
            b"  - bm25\n"
            b"  - fibo_pack\n"
        )
        adapter = StructuredDataDocumentAdapter()
        result = adapter.parse_bytes(yaml_data, filename="config.yaml")

        assert result.mime_type == "application/x-yaml"
        assert "ontology-studio" in result.text
        assert "service.name" in result.metadata["json_paths_sample"]

    def test_recursion_depth_limit_exceeded(self) -> None:
        # Create deeply nested payload > 32 levels
        nested: dict[str, Any] = {"leaf": True}
        for i in range(40):
            nested = {f"level_{i}": nested}

        json_bytes = json.dumps(nested).encode("utf-8")
        adapter = StructuredDataDocumentAdapter(max_depth=32)
        with pytest.raises(DocumentExtractionError, match="exceeds maximum recursion depth of 32"):
            adapter.parse_bytes(json_bytes, filename="nested.json")

    def test_recursion_within_depth_limit_succeeds(self) -> None:
        nested: dict[str, Any] = {"value": 42}
        for i in range(15):
            nested = {f"level_{i}": nested}
        json_bytes = json.dumps(nested).encode("utf-8")
        adapter = StructuredDataDocumentAdapter(max_depth=32)
        result = adapter.parse_bytes(json_bytes, filename="safe_nesting.json")
        assert result.metadata["max_depth"] == 17

    def test_malformed_json_raises_error(self) -> None:
        adapter = StructuredDataDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="Invalid JSON payload"):
            adapter.parse_bytes(b'{"incomplete": ', filename="bad.json")

    def test_malformed_yaml_raises_error(self) -> None:
        adapter = StructuredDataDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="Invalid YAML payload"):
            adapter.parse_bytes(b"key: [unclosed flow\nother: {bad", filename="bad.yaml")


# =============================================================================
# 5. MarkupDocumentAdapter Tests (HTML / XML)
# =============================================================================


class TestMarkupDocumentAdapter:
    """Test suite for MarkupDocumentAdapter."""

    def test_supported_types_and_extensions(self) -> None:
        adapter = MarkupDocumentAdapter()
        assert adapter.supports_extension(".html")
        assert adapter.supports_extension(".htm")
        assert adapter.supports_extension(".xml")
        assert adapter.supports_mime_type("text/html")
        assert adapter.supports_mime_type("application/xml")
        assert adapter.supports_mime_type("text/xml")

    def test_html_script_and_style_stripping(self) -> None:
        html_data = (
            b"<!DOCTYPE html>\n"
            b"<html>\n"
            b"<head>\n"
            b"  <title>Ontology Specification</title>\n"
            b"  <style>body { font-size: 14px; }</style>\n"
            b"  <script>alert('malicious script');</script>\n"
            b"</head>\n"
            b"<body>\n"
            b"  <header><nav><a href='/'>Home</a></nav></header>\n"
            b"  <h1>Domain Concepts</h1>\n"
            b"  <p>Financial Instrument classification rules.</p>\n"
            b"  <footer>Copyright 2026</footer>\n"
            b"</body>\n"
            b"</html>\n"
        )
        adapter = MarkupDocumentAdapter()
        result = adapter.parse_bytes(html_data, filename="spec.html")

        assert result.metadata["title"] == "Ontology Specification"
        assert "Domain Concepts" in result.text
        assert "Financial Instrument classification rules." in result.text
        assert "alert('malicious script')" not in result.text
        assert "font-size" not in result.text
        assert "Home" not in result.text
        assert "Copyright 2026" not in result.text

    def test_html_table_extraction(self) -> None:
        html_data = (
            b"<html><body>\n"
            b"<h2>Metrics</h2>\n"
            b"<table>\n"
            b"  <tr><th>Indicator</th><th>Score</th></tr>\n"
            b"  <tr><td>Precision</td><td>0.95</td></tr>\n"
            b"  <tr><td>Recall</td><td>0.91</td></tr>\n"
            b"</table>\n"
            b"</body></html>\n"
        )
        adapter = MarkupDocumentAdapter()
        result = adapter.parse_bytes(html_data, filename="metrics.html")

        assert len(result.tables) == 1
        tbl = result.tables[0]
        assert tbl.headers == ["Indicator", "Score"]
        assert len(tbl.rows) == 2
        assert tbl.rows[0] == ["Precision", "0.95"]
        assert tbl.rows[1] == ["Recall", "0.91"]

    def test_xml_parsing(self) -> None:
        xml_data = (
            b"<?xml version='1.0' encoding='UTF-8'?>\n"
            b"<ontology name='fibo'>\n"
            b"  <concept uri='https://spec.edmcouncil.org/fibo/ontology/FND/LegalEntities/Party'>\n"
            b"    <label>Legal Party</label>\n"
            b"    <definition>An entity with legal standing</definition>\n"
            b"  </concept>\n"
            b"</ontology>\n"
        )
        adapter = MarkupDocumentAdapter()
        result = adapter.parse_bytes(xml_data, filename="fibo.xml")

        assert result.mime_type == "application/xml"
        assert "Legal Party" in result.text
        assert "An entity with legal standing" in result.text

    def test_empty_markup_raises_error(self) -> None:
        adapter = MarkupDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="is empty"):
            adapter.parse_bytes(b"", filename="empty.html")


# =============================================================================
# 6. LibreOfficeDocumentAdapter Tests
# =============================================================================


class TestLibreOfficeDocumentAdapter:
    """Test suite for LibreOfficeDocumentAdapter."""

    def test_supported_types_and_extensions(self) -> None:
        adapter = LibreOfficeDocumentAdapter()
        assert adapter.supports_extension(".docx")
        assert adapter.supports_extension(".pptx")
        assert adapter.supports_extension(".odt")
        assert adapter.supports_extension(".xlsx")
        assert adapter.supports_extension(".doc")
        assert adapter.supports_extension(".ppt")
        assert adapter.supports_extension(".xls")
        assert adapter.supports_mime_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert adapter.supports_mime_type("application/vnd.oasis.opendocument.text")

    def test_missing_soffice_raises_501_error(self) -> None:
        # Pass non-existent path
        adapter = LibreOfficeDocumentAdapter(soffice_path="/nonexistent/bin/soffice")
        with patch(
            "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary", return_value=None
        ):
            with pytest.raises(OfficeConversionUnavailableError) as exc_info:
                adapter.parse_bytes(b"dummy office bytes", filename="spec.docx")

            err = exc_info.value
            assert err.error_code == "OFFICE_CONVERSION_UNAVAILABLE"
            assert err.exit_code == 1
            assert err.http_status == 501

    def test_empty_office_document_raises_error(self) -> None:
        adapter = LibreOfficeDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="is empty"):
            adapter.parse_bytes(b"", filename="empty.docx")

    def test_sha256_mismatch_raises_extraction_error(self) -> None:
        adapter = LibreOfficeDocumentAdapter()
        with pytest.raises(DocumentExtractionError, match="does not match computed digest"):
            adapter.parse_bytes(
                b"some docx bytes", filename="spec.docx", source_sha256="bad" + "0" * 61
            )

    def test_mock_successful_conversion_delegates_to_liteparse(self, tmp_path: Path) -> None:
        office_bytes = b"fake docx binary content for testing"
        expected_sha = hashlib.sha256(office_bytes).hexdigest()

        # Create a mock LiteParse adapter that returns a valid IngestionResult
        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_converted_1",
            filename="document.pdf",
            mime_type="application/pdf",
            total_pages=2,
            total_characters=150,
            chunks=[],
            page_map=[
                PageMapEntry(
                    page_number=1,
                    text="Page 1 converted text.",
                    start_offset=0,
                    end_offset=22,
                ),
                PageMapEntry(
                    page_number=2,
                    text="Page 2 converted text.",
                    start_offset=22,
                    end_offset=44,
                ),
            ],
            tables=[],
            taint=TaintClassification.INTERNAL,
            source_sha256="intermediate_pdf_sha",
            normalized_text="Page 1 converted text.\nPage 2 converted text.",
            metadata={"parser": "liteparse"},
        )

        adapter = LibreOfficeDocumentAdapter(
            soffice_path="mock_soffice", pdf_adapter=mock_pdf_adapter
        )

        def mock_subprocess_run(
            cmd: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            # Simulate soffice creating a PDF in the output dir
            out_dir_str = cmd[cmd.index("--outdir") + 1]
            out_dir = Path(out_dir_str)
            input_file = Path(cmd[-1])
            pdf_path = out_dir / f"{input_file.stem}.pdf"
            pdf_path.write_bytes(b"%PDF-1.5 mock pdf payload")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.run", side_effect=mock_subprocess_run),
        ):
            result = adapter.parse_bytes(
                office_bytes,
                filename="business_requirements.docx",
                taint=TaintClassification.CONFIDENTIAL,
            )

            assert result.filename == "business_requirements.docx"
            assert result.source_sha256 == expected_sha
            assert result.taint == TaintClassification.CONFIDENTIAL
            assert result.metadata["office_converted"] is True
            assert result.metadata["conversion_engine"] == "libreoffice"
            assert result.metadata["original_filename"] == "business_requirements.docx"
            assert result.total_pages == 2
            mock_pdf_adapter.parse_bytes.assert_called_once()

    def test_conversion_failure_raises_extraction_error(self) -> None:
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")

        def mock_failed_run(
            cmd: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout=b"", stderr=b"Error: corrupt document"
            )

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.run", side_effect=mock_failed_run),
            pytest.raises(
                DocumentExtractionError,
                match=r"failed on 'broken\.odt' with exit code 1",
            ),
        ):
            adapter.parse_bytes(b"corrupt", filename="broken.odt")

    def test_find_soffice_binary_custom_path(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "soffice.exe"
        fake_bin.write_bytes(b"fake exe")
        found = find_soffice_binary(str(fake_bin))
        assert found == str(fake_bin.resolve())


# =============================================================================
# 7. IngestionService Integration Tests
# =============================================================================


class TestIngestionServiceMultiFormatIntegration:
    """Test suite verifying end-to-end integration into IngestionService."""

    def test_ingest_all_formats_through_service(self) -> None:
        # Build service with all specialized adapters
        service = IngestionService(
            adapters=[
                MarkdownDocumentAdapter(),
                DelimitedTabularAdapter(),
                PyArrowTabularAdapter(),
                StructuredDataDocumentAdapter(),
                MarkupDocumentAdapter(),
            ]
        )

        # 1. Ingest Markdown
        md_bytes = b"# Header\n\nParagraph with evidence content.\n"
        res_md = service.ingest_bytes(
            md_bytes,
            filename="doc.md",
            taint=TaintClassification.INTERNAL,
        )
        assert res_md.mime_type == "text/markdown"
        assert len(res_md.chunks) >= 1
        assert res_md.chunks[0].taint == TaintClassification.INTERNAL

        # 2. Ingest CSV
        csv_bytes = b"colA,colB\n10,20\n30,40\n"
        res_csv = service.ingest_bytes(
            csv_bytes,
            filename="data.csv",
            taint=TaintClassification.CONFIDENTIAL,
        )
        assert res_csv.mime_type == "text/csv"
        assert len(res_csv.tables) == 1
        assert res_csv.taint == TaintClassification.CONFIDENTIAL

        # 3. Ingest JSON
        json_bytes = json.dumps({"key": "value", "list": [1, 2, 3]}).encode("utf-8")
        res_json = service.ingest_bytes(json_bytes, filename="data.json")
        assert res_json.mime_type == "application/json"
        assert "value" in res_json.text

        # 4. Ingest HTML
        html_bytes = b"<html><head><title>Hi</title></head><body><p>Hello world</p></body></html>"
        res_html = service.ingest_bytes(html_bytes, filename="page.html")
        assert res_html.mime_type == "text/html"
        assert "Hello world" in res_html.text

    def test_decode_text_payload_bom_and_encodings(self) -> None:
        # Test UTF-8 with BOM
        utf8_bom = b"\xef\xbb\xbf# UTF-8 BOM Title\n"
        text, enc = decode_text_payload(utf8_bom, filename="bom.md")
        assert text.startswith("# UTF-8 BOM Title")
        assert enc == "utf-8"

        # Test UTF-16 LE with BOM
        utf16_bom = "\ufeff# UTF-16 Title\n".encode("utf-16")
        text16, enc16 = decode_text_payload(utf16_bom, filename="utf16.md")
        assert "UTF-16 Title" in text16
        assert enc16 == "utf-16"
