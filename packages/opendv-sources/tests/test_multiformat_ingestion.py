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

import csv
import hashlib
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TaintClassification,
)
from opendv_contracts.errors import ErrorCode
from opendv_sources.adapters.libreoffice_adapter import (
    ArchiveSecurityViolationError,
    LibreOfficeDocumentAdapter,
    _inspect_expat_dtd_and_entities,
    _inspect_ole_cfb_security,
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

    def test_max_bytes_guard_raises_invalid_document_error(self) -> None:
        """Regression P04::MAJ-03: Enforce payload byte limit on Markdown ingestion."""
        adapter = MarkdownDocumentAdapter(max_bytes=64)
        with pytest.raises(InvalidDocumentError, match="exceeds maximum allowed size"):
            adapter.parse_bytes(b"# Heading\n\n" + b"x" * 100, filename="oversized.md")

    def test_markdown_linear_time_on_misaligned_double_spaced_lines(self) -> None:
        """Regression P04::MAJ-03: 20,000 misaligned 2-column lines parse in < 0.5s."""
        lines = "\n".join(f"{'k' * ((i * 13) % 29 + 1)}  val_{i}" for i in range(20_000))
        payload = f"# Log Output\n\n{lines}\n".encode()
        adapter = MarkdownDocumentAdapter()

        t0 = time.perf_counter()
        result = adapter.parse_bytes(payload, filename="logs.md")
        elapsed = time.perf_counter() - t0

        assert result.tables == []
        assert elapsed < 0.5, f"Markdown table scan quadratic regression: {elapsed:.3f}s"

    def test_markdown_linear_time_on_25000_fenced_code_blocks_and_headings(self) -> None:
        """Regression P04b::MAJ-01: >= 25,000 fenced code blocks + headings parse in < 1.0s."""
        num_blocks = 26_000
        segments: list[str] = []
        for i in range(num_blocks):
            if i % 10 == 0:
                segments.append(f"## Section {i}")
            segments.append("```\n# inner comment\n```")
        payload = ("\n".join(segments) + "\n").encode("utf-8")

        adapter = MarkdownDocumentAdapter()
        t0 = time.perf_counter()
        result = adapter.parse_bytes(payload, filename="massive_fences.md")
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, (
            f"Markdown fenced code-block + heading scan quadratic regression: {elapsed:.3f}s"
        )
        assert result.metadata["code_block_count"] == num_blocks
        assert result.metadata["quarantined_code_blocks"] == num_blocks
        assert result.metadata["heading_count"] == 2_600
        assert len(result.metadata["code_blocks"]) == num_blocks
        assert len(result.metadata["headings"]) == 2_600

        # Also verify metadata list bounding preserves exact counts and full quarantine
        bounded = MarkdownDocumentAdapter(exclude_code_blocks=True, max_metadata_items=100)
        bounded_res = bounded.parse_bytes(payload, filename="bounded_fences.md")
        assert bounded_res.metadata["code_block_count"] == num_blocks
        assert bounded_res.metadata["heading_count"] == 2_600
        assert len(bounded_res.metadata["code_blocks"]) == 100
        assert len(bounded_res.metadata["headings"]) == 100
        assert "# inner comment" not in bounded_res.normalized_text

    def test_commonmark_fenced_code_block_delimiter_and_length_rules(self) -> None:
        """Regression R6-MAJ-03: Enforce CommonMark §4.5 fence char, length, and backticks."""
        # 1) Tilde fence (~~~) must NOT be closed by inner backtick fence (```bash)
        tilde_md = (
            "# Outer Title\n\n"
            "~~~markdown\n"
            "```bash\n"
            "# Fake Heading\n"
            "SECRET_TOKEN_TILDE\n"
            "```\n"
            "~~~\n\n"
            "## After Tilde\n"
        )
        adapter_excl = MarkdownDocumentAdapter(exclude_code_blocks=True)
        res_tilde = adapter_excl.parse_bytes(tilde_md.encode("utf-8"), filename="tilde.md")
        assert res_tilde.metadata["code_block_count"] == 1
        assert res_tilde.metadata["heading_count"] == 2
        assert [h["title"] for h in res_tilde.metadata["headings"]] == [
            "Outer Title",
            "After Tilde",
        ]
        assert "SECRET_TOKEN_TILDE" not in res_tilde.normalized_text
        assert "Fake Heading" not in res_tilde.normalized_text
        assert "After Tilde" in res_tilde.normalized_text

        # 2) 4-backtick fence (````markdown) must NOT be closed by inner 3-backtick fence (```)
        quad_md = (
            "# Spec Header\n\n"
            "````markdown\n"
            "```python\n"
            "# Inner Fake Heading\n"
            "SECRET_TOKEN_QUAD = 42\n"
            "```\n"
            "````\n\n"
            "### Final Section\n"
        )
        res_quad = adapter_excl.parse_bytes(quad_md.encode("utf-8"), filename="quad.md")
        assert res_quad.metadata["code_block_count"] == 1
        assert res_quad.metadata["heading_count"] == 2
        assert [h["title"] for h in res_quad.metadata["headings"]] == [
            "Spec Header",
            "Final Section",
        ]
        assert "SECRET_TOKEN_QUAD" not in res_quad.normalized_text
        assert "Inner Fake Heading" not in res_quad.normalized_text

        # 3) Single-line ```inline code on single line``` at BOL is an inline code span, NOT a fence
        inline_bol_md = (
            "```inline code on single line```\n\n"
            "## Real Section\n\n"
            "Visible body paragraph after inline code span.\n"
        )
        res_inline = adapter_excl.parse_bytes(inline_bol_md.encode("utf-8"), filename="inline.md")
        assert res_inline.metadata["code_block_count"] == 0
        assert res_inline.metadata["heading_count"] == 1
        assert res_inline.metadata["headings"][0]["title"] == "Real Section"
        assert "Visible body paragraph after inline code span." in res_inline.normalized_text

        # 4) CommonMark §4.5 indentation (<=3 spaces) & closing fence cannot carry info string
        indent_md = (
            "   ```python\n"
            "    ```\n"
            "``` trailing_not_closing\n"
            "STILL_QUARANTINED\n"
            "  ```   \n"
            "    ```ignored_four_space_indent\n"
            "# Post Fence Heading\n"
        )
        res_indent = adapter_excl.parse_bytes(indent_md.encode("utf-8"), filename="indent.md")
        assert res_indent.metadata["code_block_count"] == 1
        assert res_indent.metadata["heading_count"] == 1
        assert res_indent.metadata["headings"][0]["title"] == "Post Fence Heading"
        assert "STILL_QUARANTINED" not in res_indent.normalized_text


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

    def test_malformed_csv_raising_csv_error_wrapped_in_invalid_document_error(self) -> None:
        """Regression 040b4599::MAJ-07: csv.Error is translated to typed InvalidDocumentError."""
        adapter = DelimitedTabularAdapter()
        # Trigger real _csv.Error ("field larger than field limit") in C csv.reader
        orig_limit = csv.field_size_limit(64)
        try:
            oversized_csv = b"id,name\n1," + (b"A" * 128) + b"\n"
            with pytest.raises(InvalidDocumentError, match=r"Malformed CSV/TSV payload"):
                adapter.parse_bytes(oversized_csv, filename="oversized_field.csv")
        finally:
            csv.field_size_limit(orig_limit)

        # Also verify unclosed quote / strict dialect csv.Error translation
        with (
            patch("csv.reader", side_effect=csv.Error("unexpected end of data")),
            pytest.raises(InvalidDocumentError, match="unexpected end of data"),
        ):
            adapter.parse_bytes(b'col1,col2\n"unclosed,val2\n', filename="bad_quote.csv")


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

    def test_parquet_iter_batches_bounds_preview_rows_and_cell_sizes(self) -> None:
        """Regression P04::MAJ-01: Stream bounded preview batch via iter_batches without read()."""
        table = pa.Table.from_pydict(
            {
                "id": list(range(5_000)),
                "payload": ["Z" * 2_000 for _ in range(5_000)],
            }
        )
        sink = io.BytesIO()
        pq.write_table(table, sink, use_dictionary=True)
        parquet_bytes = sink.getvalue()

        adapter = PyArrowTabularAdapter(preview_rows=20, max_preview_cell_chars=128)
        with (
            patch.object(
                pq.ParquetFile,
                "read_row_group",
                side_effect=AssertionError("read_row_group must not be called"),
            ),
            patch.object(
                pq.ParquetFile,
                "read",
                side_effect=AssertionError("ParquetFile.read must not be called"),
            ),
        ):
            result = adapter.parse_bytes(parquet_bytes, filename="dict_encoded.parquet")

        assert result.metadata["num_rows"] == 5_000
        assert len(result.tables) == 1
        assert len(result.tables[0].rows) == 20
        cell_val = result.tables[0].rows[0][1]
        assert cell_val.endswith("…[TRUNCATED]")
        assert len(cell_val) == 128 + len("…[TRUNCATED]")

    def test_parquet_input_size_and_compression_bomb_guards(
        self, sample_parquet_bytes: bytes
    ) -> None:
        """Regression P04::MAJ-01: Reject oversized Parquet inputs and RLE/dictionary bombs."""
        from opendv_contracts.errors import PayloadTooLargeError

        tiny_limit_adapter = PyArrowTabularAdapter(max_input_bytes=32)
        with pytest.raises(PayloadTooLargeError) as exc_info:
            tiny_limit_adapter.parse_bytes(sample_parquet_bytes, filename="too_big.parquet")
        assert isinstance(exc_info.value, InvalidDocumentError)
        assert exc_info.value.http_status == 413

        # Uncompressed row-group byte bomb (gzip-compressed repetitive pages)
        bomb_table = pa.Table.from_pydict({"blob": ["X" * 1_024 for _ in range(2_000)]})
        bomb_sink = io.BytesIO()
        pq.write_table(bomb_table, bomb_sink, use_dictionary=False, compression="gzip")
        bomb_bytes = bomb_sink.getvalue()

        strict_bomb_adapter = PyArrowTabularAdapter(max_uncompressed_bytes=64 * 1024)
        with pytest.raises(PayloadTooLargeError, match="uncompressed row-group size exceeds"):
            strict_bomb_adapter.parse_bytes(bomb_bytes, filename="gzip_bomb.parquet")

        # Dictionary/RLE extreme row-count metadata bomb
        rle_sink = io.BytesIO()
        pq.write_table(bomb_table, rle_sink, use_dictionary=True)
        row_cap_adapter = PyArrowTabularAdapter(max_metadata_rows=500)
        with pytest.raises(PayloadTooLargeError, match="exceeding maximum allowed row count"):
            row_cap_adapter.parse_bytes(rle_sink.getvalue(), filename="rle_bomb.parquet")

        # MemoryError / RecursionError during iter_batches translated cleanly
        adapter = PyArrowTabularAdapter()
        with (
            patch.object(pq.ParquetFile, "iter_batches", side_effect=MemoryError("arrow oom")),
            pytest.raises(PayloadTooLargeError, match="exhausted memory"),
        ):
            adapter.parse_bytes(sample_parquet_bytes, filename="oom.parquet")

        with (
            patch.object(
                pq.ParquetFile, "iter_batches", side_effect=RecursionError("nested struct")
            ),
            pytest.raises(InvalidDocumentError, match="recursion limits"),
        ):
            adapter.parse_bytes(sample_parquet_bytes, filename="deep_parquet.parquet")


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

    def test_yaml_billion_laughs_exponential_dag_rejected(self) -> None:
        """Regression P04::MAJ-04 & 040b4599::MAJ-07: YAML billion-laughs bomb fails fast."""
        bomb_lines = ['a0: &a0 ["x", "x", "x", "x", "x", "x", "x", "x", "x", "x"]']
        for level in range(1, 9):
            prev = f"*a{level - 1}"
            bomb_lines.append(f"a{level}: &a{level} [{', '.join([prev] * 10)}]")
        yaml_bomb = ("\n".join(bomb_lines) + "\n").encode("utf-8")

        adapter = StructuredDataDocumentAdapter()
        t0 = time.perf_counter()
        with pytest.raises(InvalidDocumentError, match="maximum expanded node count"):
            adapter.parse_bytes(yaml_bomb, filename="billion_laughs.yaml")
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"YAML bomb defense took too long: {elapsed:.3f}s"

    def test_yaml_cyclic_anchor_and_alias_flood_rejected(self) -> None:
        """Regression 040b4599::MAJ-07: Reject cyclic YAML anchors and excessive alias counts."""
        adapter = StructuredDataDocumentAdapter()

        # 1. Cyclic anchor graph
        cyclic_yaml = b"node: &self\n  child: *self\n"
        with pytest.raises(InvalidDocumentError, match="Cyclic reference detected"):
            adapter.parse_bytes(cyclic_yaml, filename="cycle.yaml")

        # 2. Alias count exceeding _BoundedSafeLoader.max_aliases (1000)
        alias_flood = ("base: &b 1\nitems: [" + ", ".join(["*b"] * 1050) + "]\n").encode("utf-8")
        with pytest.raises(InvalidDocumentError, match="alias expansion count exceeds maximum"):
            adapter.parse_bytes(alias_flood, filename="alias_flood.yaml")

    def test_deep_json_and_yaml_brackets_raise_invalid_document_error_fast(self) -> None:
        """Regression P04::MAJ-02: Pre-parse guard blocks '[' * 5000 without RecursionError."""
        adapter = StructuredDataDocumentAdapter(max_depth=32)

        for fname, deep_payload in (
            ("deep_closed.json", b"[" * 5000 + b"]" * 5000),
            ("deep_unclosed.json", b"[" * 5000),
            ("deep_line.jsonl", b"[" * 5000 + b"]" * 5000 + b"\n"),
            ("deep_flow.yaml", b"[" * 5000 + b"]" * 5000),
            (
                "deep_block.yaml",
                "".join(f"{'  ' * d}k{d}:\n" for d in range(80)).encode("utf-8")
                + ("  " * 80 + "leaf: 1\n").encode("utf-8"),
            ),
        ):
            t0 = time.perf_counter()
            with pytest.raises(InvalidDocumentError, match="exceeds maximum recursion depth"):
                adapter.parse_bytes(deep_payload, filename=fname)
            assert time.perf_counter() - t0 < 0.25

        # Quoted brackets inside strings must NOT trigger false-positive rejection
        quoted_brackets = json.dumps({"pattern": "[" * 5000}).encode("utf-8")
        res = adapter.parse_bytes(quoted_brackets, filename="quoted_safe.json")
        assert res.metadata["max_depth"] == 2


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

    @staticmethod
    def _minimal_office_zip_bytes(entry_name: str = "word/document.xml") -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(entry_name, '<?xml version="1.0" encoding="UTF-8"?><root>ok</root>')
        return buf.getvalue()

    @staticmethod
    def _minimal_ole_cfb_bytes(body: bytes = b"Benign legacy binary content.") -> bytes:
        magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        header_pad = b"\x00" * (512 - len(magic))
        return magic + header_pad + body

    def test_missing_soffice_raises_501_error(self) -> None:
        # Pass non-existent path
        adapter = LibreOfficeDocumentAdapter(soffice_path="/nonexistent/bin/soffice")
        valid_zip = self._minimal_office_zip_bytes()
        with patch(
            "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary", return_value=None
        ):
            with pytest.raises(OfficeConversionUnavailableError) as exc_info:
                adapter.parse_bytes(valid_zip, filename="spec.docx")

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

    def test_mock_successful_conversion_delegates_to_liteparse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        office_bytes = self._minimal_office_zip_bytes()
        expected_sha = hashlib.sha256(office_bytes).hexdigest()
        mock_pdf_payload = b"%PDF-1.5 mock pdf payload"
        expected_pdf_sha = hashlib.sha256(mock_pdf_payload).hexdigest()

        # Create a mock LiteParse adapter returning chunks stamped with intermediate PDF SHA-256
        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_converted_1",
            filename="document.pdf",
            mime_type="application/pdf",
            total_pages=2,
            total_characters=45,
            chunks=[
                EvidenceChunk(
                    chunk_id="chk_1",
                    document_id="doc_converted_1",
                    text="Page 1 converted text.",
                    page_number=1,
                    start_char=0,
                    end_char=22,
                    taint=TaintClassification.INTERNAL,
                    source_sha256="intermediate_pdf_sha",
                ),
                EvidenceChunk(
                    chunk_id="chk_2",
                    document_id="doc_converted_1",
                    text="Page 2 converted text.",
                    page_number=2,
                    start_char=23,
                    end_char=45,
                    taint=TaintClassification.INTERNAL,
                    source_sha256="intermediate_pdf_sha",
                ),
            ],
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
                    start_offset=23,
                    end_offset=45,
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

        # Inject fake secrets into os.environ to verify sanitized subprocess environment
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-do-not-leak")
        monkeypatch.setenv("DATABASE_URL", "postgresql://secret")

        captured_popen: dict[str, Any] = {}

        def mock_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            captured_popen["cmd"] = cmd
            captured_popen["kwargs"] = kwargs
            out_dir_str = cmd[cmd.index("--outdir") + 1]
            out_dir = Path(out_dir_str)
            input_file = Path(cmd[-1])
            pdf_path = out_dir / f"{input_file.stem}.pdf"
            pdf_path.write_bytes(mock_pdf_payload)

            proc = MagicMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=mock_popen),
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
            assert result.metadata["intermediate_pdf_sha256"] == expected_pdf_sha
            assert result.total_pages == 2
            mock_pdf_adapter.parse_bytes.assert_called_once()

            # P04::MAJ-01: Every EvidenceChunk must carry original Office SHA-256 & doc_id & taint!
            assert len(result.chunks) == 2
            for chunk in result.chunks:
                assert chunk.source_sha256 == expected_sha
                assert chunk.source_sha256 != "intermediate_pdf_sha"
                assert chunk.document_id == result.document_id
                assert chunk.taint == TaintClassification.CONFIDENTIAL

            # 040b4599::MAJ-08 & P04a::MAJ-01: Verify hardened flags, --infilter, & sanitized env
            cmd_used = captured_popen["cmd"]
            for required_flag in (
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nodefault",
                "--nofirststartwizard",
                "--infilter=MS Word 2007 XML",
            ):
                assert required_flag in cmd_used
            assert cmd_used.index("--infilter=MS Word 2007 XML") < cmd_used.index("--convert-to")
            child_env = captured_popen["kwargs"]["env"]
            assert "OPENAI_API_KEY" not in child_env
            assert "DATABASE_URL" not in child_env

    def test_conversion_failure_raises_extraction_error(self) -> None:
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")
        odt_bytes = self._minimal_office_zip_bytes(entry_name="content.xml")

        def mock_failed_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 1
            proc.communicate.return_value = (b"", b"Error: corrupt document")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=mock_failed_popen),
            pytest.raises(
                DocumentExtractionError,
                match=r"failed on 'broken\.odt' with exit code 1",
            ),
        ):
            adapter.parse_bytes(odt_bytes, filename="broken.odt")

    def test_conversion_timeout_terminates_process_tree(self) -> None:
        """Regression 040b4599::MAJ-08: Timeout kills soffice process tree cleanly."""
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice", timeout_seconds=1)
        mock_proc = MagicMock()
        mock_proc.pid = 42424
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="soffice", timeout=1)
        docx_bytes = self._minimal_office_zip_bytes()

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", return_value=mock_proc),
            patch(
                "opendv_sources.adapters.libreoffice_adapter._terminate_process_tree"
            ) as mock_kill_tree,
            pytest.raises(DocumentExtractionError, match="timed out after 1s"),
        ):
            adapter.parse_bytes(docx_bytes, filename="hang.docx")

        mock_kill_tree.assert_called_once_with(mock_proc)

    def test_find_soffice_binary_custom_path(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "soffice.exe"
        fake_bin.write_bytes(b"fake exe")
        found = find_soffice_binary(str(fake_bin))
        assert found == str(fake_bin.resolve())

    def test_libreoffice_zip_bomb_and_max_input_bytes_hardening(self) -> None:
        """Regression P04::MAJ-03: Enforce max_input_bytes and reject >200 MB / >100:1 ZIP bombs."""
        import zipfile

        from opendv_contracts.errors import ErrorCode, PayloadTooLargeError

        small_cap_adapter = LibreOfficeDocumentAdapter(max_input_bytes=64)
        with pytest.raises(PayloadTooLargeError) as exc_size:
            small_cap_adapter.parse_bytes(b"X" * 128, filename="oversized.docx")
        assert isinstance(exc_size.value, InvalidDocumentError)
        assert exc_size.value.http_status == 413

        # High compression ratio (>100:1) ZIP bomb
        bomb_buf = io.BytesIO()
        with zipfile.ZipFile(bomb_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", b"0" * 500_000)
        bomb_bytes = bomb_buf.getvalue()
        assert len(bomb_bytes) < 2_000

        adapter = LibreOfficeDocumentAdapter()
        with pytest.raises(InvalidDocumentError, match="ZIP bomb") as exc_bomb:
            adapter.parse_bytes(bomb_bytes, filename="ratio_bomb.docx")
        assert exc_bomb.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        # Uncompressed byte cap (> max_uncompressed_bytes) with low compression ratio
        uncomp_buf = io.BytesIO()
        with zipfile.ZipFile(uncomp_buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("media/blob.bin", b"A" * 40_000)
        capped_adapter = LibreOfficeDocumentAdapter(max_uncompressed_bytes=20_000)
        with pytest.raises(PayloadTooLargeError, match="uncompressed size exceeds"):
            capped_adapter.parse_bytes(uncomp_buf.getvalue(), filename="huge_uncompressed.docx")

    def test_libreoffice_blocks_external_links_and_writes_hardened_xcu(self) -> None:
        """Regression P04::MAJ-03: Block external image/OLE/DDE/WEBSERVICE; allow https links."""
        import zipfile

        from opendv_contracts.errors import ErrorCode

        def _build_zip(entries: dict[str, str]) -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for name, xml_text in entries.items():
                    zf.writestr(name, xml_text.encode("utf-8"))
            return buf.getvalue()

        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")
        pkg_rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        off_rels_base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        rels_open = f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg_rels_ns}">'

        # 1. Unsafe external relationships, file:// links, field codes, WEBSERVICE, ODF section
        unsafe_archives: list[tuple[str, dict[str, str]]] = [
            (
                "ext_img.docx",
                {
                    "word/_rels/document.xml.rels": (
                        f"{rels_open}"
                        f'<Relationship Id="rId1" Type="{off_rels_base}/image" '
                        'Target="https://evil.example/beacon.png" TargetMode="External"/>'
                        "</Relationships>"
                    )
                },
            ),
            (
                "ext_ole.docx",
                {
                    "word/_rels/document.xml.rels": (
                        f"{rels_open}"
                        f'<Relationship Id="rId1" Type="{off_rels_base}/oleObject" '
                        'Target="https://evil.example/obj.bin" TargetMode="External"/>'
                        "</Relationships>"
                    )
                },
            ),
            (
                "file_link.docx",
                {
                    "word/_rels/document.xml.rels": (
                        f"{rels_open}"
                        f'<Relationship Id="rId1" Type="{off_rels_base}/hyperlink" '
                        'Target="file:///etc/passwd" TargetMode="External"/>'
                        "</Relationships>"
                    )
                },
            ),
            (
                "dde_field.docx",
                {
                    "word/document.xml": (
                        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w_ns}">'
                        "<w:body><w:p><w:r>"
                        r"<w:instrText>DDEAUTO c:\\windows\\system32\\cmd.exe</w:instrText>"
                        "</w:r></w:p></w:body></w:document>"
                    )
                },
            ),
            (
                "webservice.xlsx",
                {
                    "xl/worksheets/sheet1.xml": (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                        '<sheetData><row r="1"><c r="A1">'
                        '<f>WEBSERVICE("http://169.254.169.254/latest/meta-data/")</f>'
                        "</c></row></sheetData></worksheet>"
                    )
                },
            ),
            (
                "linked_section.odt",
                {
                    "content.xml": (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        "<office:document-content "
                        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                        'xmlns:xlink="http://www.w3.org/1999/xlink">'
                        '<office:body><office:text><text:section text:name="Sec1">'
                        '<text:section-source xlink:href="file:///etc/hosts"/>'
                        "</text:section></office:text></office:body></office:document-content>"
                    )
                },
            ),
        ]

        for bad_name, bad_entries in unsafe_archives:
            with pytest.raises(InvalidDocumentError) as exc_sec:
                adapter.parse_bytes(_build_zip(bad_entries), filename=bad_name)
            assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        # 2. Normal .docx with passive https:// hyperlink & prose mentioning "DDE" MUST pass,
        # and hardened user/registrymodifications.xcu MUST be written prior to soffice Popen!
        safe_docx = _build_zip(
            {
                "word/_rels/document.xml.rels": (
                    f"{rels_open}"
                    f'<Relationship Id="rId1" Type="{off_rels_base}/hyperlink" '
                    'Target="https://example.com/policy" TargetMode="External"/>'
                    "</Relationships>"
                ),
                "word/document.xml": (
                    f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w_ns}">'
                    "<w:body><w:p><w:r>"
                    "<w:t>Legacy DDE protocols are retired; see policy link.</w:t>"
                    "</w:r></w:p></w:body></w:document>"
                ),
            }
        )

        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_safe",
            filename="safe.pdf",
            mime_type="application/pdf",
            total_pages=1,
            total_characters=10,
            chunks=[],
            page_map=[],
            tables=[],
            taint=TaintClassification.INTERNAL,
            source_sha256="intermediate",
            normalized_text="Safe text.",
            metadata={},
        )
        safe_adapter = LibreOfficeDocumentAdapter(
            soffice_path="mock_soffice", pdf_adapter=mock_pdf_adapter
        )
        captured_xcu: list[str] = []

        def mock_safe_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            user_inst_arg = next(a for a in cmd if a.startswith("-env:UserInstallation="))
            from urllib.request import url2pathname

            uri_val = user_inst_arg.split("=", 1)[1]
            profile_path = Path(url2pathname(uri_val.removeprefix("file:")))
            xcu_file = profile_path / "user" / "registrymodifications.xcu"
            assert xcu_file.is_file()
            captured_xcu.append(xcu_file.read_text(encoding="utf-8"))

            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            (out_dir / f"{Path(cmd[-1]).stem}.pdf").write_bytes(b"%PDF-1.5 mock")
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=mock_safe_popen),
        ):
            res = safe_adapter.parse_bytes(safe_docx, filename="safe_policy.docx")

        assert res.filename == "safe_policy.docx"
        assert len(captured_xcu) == 1
        assert "MacroSecurityLevel" in captured_xcu[0]
        assert "<value>3</value>" in captured_xcu[0]
        assert "UpdateMode" in captured_xcu[0]
        assert "<value>1</value>" in captured_xcu[0]

    def test_non_zip_and_polyglot_payloads_renamed_to_office_rejected(self) -> None:
        """Regression P04a::MAJ-01: HTML, RTF, and flat-XML ODF renamed to .doc/.xls/.ppt/.docx."""
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")

        html_ssrf = (
            b'<html><body><img src="http://169.254.169.254/latest/meta-data/"/>'
            b'<iframe src="file:///etc/passwd"></iframe></body></html>'
        )
        rtf_dde = (
            rb"{\rtf1\ansi{\field{\*\fldinst{INCLUDEPICTURE "
            rb'"http://evil.example/beacon.png" \\d}}{\fldrslt}}}'
        )
        fodt_xxe = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE office:document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            b'xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<office:body><office:text><text:section text:name="S">'
            b'<text:section-source xlink:href="http://evil.example/leak"/>'
            b"</text:section>&xxe;</office:text></office:body></office:document>"
        )
        fods_webservice = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
            b'xmlns:xlink="http://www.w3.org/1999/xlink">'
            b"<office:body><office:spreadsheet><table:table>"
            b'<table:table-source xlink:href="http://evil.example/sheet.ods"/>'
            b'<table:table-row><table:table-cell table:formula="of:=WEBSERVICE(&quot;http://169.254.169.254&quot;)"/>'
            b"</table:table-row></table:table></office:spreadsheet></office:body></office:document>"
        )
        valid_zip = self._minimal_office_zip_bytes()
        valid_ole = self._minimal_ole_cfb_bytes()
        polyglot_html_zip = html_ssrf + valid_zip

        spoofed_cases: list[tuple[str, bytes, str | None]] = [
            ("spoofed_html.doc", html_ssrf, None),
            ("spoofed_html.xls", html_ssrf, None),
            ("spoofed_html.ppt", html_ssrf, None),
            ("spoofed_html.docx", html_ssrf, None),
            ("spoofed_rtf.doc", rtf_dde, None),
            ("spoofed_rtf.docx", rtf_dde, None),
            ("spoofed_fodt.doc", fodt_xxe, None),
            ("spoofed_fodt.docx", fodt_xxe, None),
            ("spoofed_fodt.odt", fodt_xxe, None),
            ("spoofed_fods.xls", fods_webservice, None),
            ("spoofed_fods.xlsx", fods_webservice, None),
            ("spoofed_fods.ods", fods_webservice, None),
            ("zip_as_legacy.doc", valid_zip, None),
            ("zip_as_legacy.xls", valid_zip, None),
            ("ole_as_ooxml.docx", valid_ole, None),
            ("ole_as_ooxml.xlsx", valid_ole, None),
            ("polyglot_html.docx", polyglot_html_zip, None),
            ("truncated_cfb.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, None),
            ("mime_mismatch.docx", valid_zip, "application/msword"),
            ("mime_mismatch.doc", valid_ole, "text/html"),
        ]

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen") as mock_popen,
        ):
            for fname, payload, explicit_mime in spoofed_cases:
                with pytest.raises(ArchiveSecurityViolationError) as exc_info:
                    adapter.parse_bytes(payload, filename=fname, mime_type=explicit_mime)
                assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
                assert exc_info.value.exit_code == 4
                assert exc_info.value.http_status == 422
            mock_popen.assert_not_called()

    def test_ole_cfb_security_blocks_ascii_and_utf16le_dde_and_links(self) -> None:
        """Regression P04a::MAJ-01: Scan OLE2 CFB streams in ASCII and even/odd UTF-16LE."""
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")

        dangerous_markers = [
            "DDEAUTO c:\\windows\\system32\\cmd.exe",
            "DDE cmd.exe /c calc.exe",
            'dde "http://evil.example/cmd"',
            'INCLUDEPICTURE "https://evil.example/pixel.png"',
            'includetext "C:\\secrets\\keys.txt"',
            'RD "C:\\secrets\\chapter.doc"',
            'IMPORT "https://evil.example/data.xml"',
            'LINK Excel.Sheet.8 "\\\\evil.example\\share\\book.xls"',
            'WEBSERVICE("http://169.254.169.254/latest/meta-data/")',
            "table-source",
            "section-source",
            "file:///etc/passwd",
            "\\\\169.254.169.254\\ipc$\\leak",
            "Ole10Native http://evil.example/payload.bin",
        ]

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen") as mock_popen,
        ):
            for ext in (".doc", ".xls", ".ppt"):
                for marker in dangerous_markers:
                    # 1) ASCII / Latin-1 stream
                    ascii_cfb = self._minimal_ole_cfb_bytes(b"\x00" * 16 + marker.encode("latin-1"))
                    with pytest.raises(ArchiveSecurityViolationError) as exc_ascii:
                        adapter.parse_bytes(ascii_cfb, filename=f"attack_ascii{ext}")
                    assert exc_ascii.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

                    # 2) Even-aligned UTF-16LE stream
                    utf16_even_cfb = self._minimal_ole_cfb_bytes(
                        b"\x00\x00" + marker.encode("utf-16-le") + b"\x00\x00"
                    )
                    with pytest.raises(ArchiveSecurityViolationError) as exc_u16_even:
                        adapter.parse_bytes(utf16_even_cfb, filename=f"attack_u16_even{ext}")
                    assert exc_u16_even.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

                    # 3) Odd-aligned UTF-16LE stream (e.g. BIFF8 1-byte string flags prefix)
                    utf16_odd_cfb = self._minimal_ole_cfb_bytes(
                        b"\x01" + marker.encode("utf-16-le") + b"\x00"
                    )
                    with pytest.raises(ArchiveSecurityViolationError) as exc_u16_odd:
                        adapter.parse_bytes(utf16_odd_cfb, filename=f"attack_u16_odd{ext}")
                    assert exc_u16_odd.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

            mock_popen.assert_not_called()

        # Direct helper test: benign prose with passive https URL passes
        benign_prose = (
            b"Quarterly Deal Review. See https://example.com/policy for link details."
            + " Summary in UTF-16: https://example.com/report".encode("utf-16-le")
        )
        _inspect_ole_cfb_security(self._minimal_ole_cfb_bytes(benign_prose), ".doc")

    def test_explicit_soffice_infilter_passed_for_all_supported_extensions(self) -> None:
        """Regression P04a::MAJ-01: Explicit --infilter=<filter> precedes --convert-to."""
        expected_infilters: list[tuple[str, bytes, str]] = [
            (".doc", self._minimal_ole_cfb_bytes(), "MS Word 97"),
            (".docx", self._minimal_office_zip_bytes(), "MS Word 2007 XML"),
            (".xls", self._minimal_ole_cfb_bytes(), "MS Excel 97"),
            (".xlsx", self._minimal_office_zip_bytes("xl/workbook.xml"), "Calc MS Excel 2007 XML"),
            (".ppt", self._minimal_ole_cfb_bytes(), "MS PowerPoint 97"),
            (
                ".pptx",
                self._minimal_office_zip_bytes("ppt/presentation.xml"),
                "Impress MS PowerPoint 2007 XML",
            ),
            (".odt", self._minimal_office_zip_bytes("content.xml"), "writer8"),
            (".ods", self._minimal_office_zip_bytes("content.xml"), "calc8"),
            (".odp", self._minimal_office_zip_bytes("content.xml"), "impress8"),
        ]

        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_ok",
            filename="out.pdf",
            mime_type="application/pdf",
            total_pages=1,
            total_characters=4,
            chunks=[],
            page_map=[],
            tables=[],
            taint=TaintClassification.INTERNAL,
            source_sha256="intermediate",
            normalized_text="Test",
            metadata={},
        )
        adapter = LibreOfficeDocumentAdapter(
            soffice_path="mock_soffice", pdf_adapter=mock_pdf_adapter
        )
        captured_cmds: list[list[str]] = []

        def mock_ok_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            captured_cmds.append(list(cmd))
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            (out_dir / f"{Path(cmd[-1]).stem}.pdf").write_bytes(b"%PDF-1.5 mock")
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=mock_ok_popen),
        ):
            for ext, payload, expected_filter in expected_infilters:
                captured_cmds.clear()
                res = adapter.parse_bytes(payload, filename=f"sample{ext}")
                assert res.filename == f"sample{ext}"
                assert len(captured_cmds) == 1
                cmd = captured_cmds[0]
                expected_arg = f"--infilter={expected_filter}"
                assert expected_arg in cmd
                assert cmd.index(expected_arg) < cmd.index("--convert-to")

    def test_utf16_dtd_and_non_xml_zip_entries_rejected(self) -> None:
        """Regression R6-MAJ-01: Block UTF-16LE/BE DTD/ENTITY and .vml/.svg/.xhtml/.html SSRF."""
        adapter = LibreOfficeDocumentAdapter(soffice_path="mock_soffice")

        def _zip_from_raw_entries(entries: dict[str, bytes]) -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for name, raw in entries.items():
                    zf.writestr(name, raw)
            return buf.getvalue()

        utf16_ext_dtd = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE root SYSTEM "http://169.254.169.254/latest/meta-data/">'
            "<root>leak</root>"
        )
        utf16_int_entity = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE root [<!ENTITY xxe "secret">]>'
            "<root>&xxe;</root>"
        )

        # Direct Expat helper checks across UTF-16LE/BE with & without BOM
        for bad_xml in (
            b"\xff\xfe" + utf16_ext_dtd.encode("utf-16-le"),
            utf16_ext_dtd.encode("utf-16-le"),
            b"\xfe\xff" + utf16_ext_dtd.encode("utf-16-be"),
            utf16_ext_dtd.encode("utf-16-be"),
            b"\xff\xfe" + utf16_int_entity.encode("utf-16-le"),
        ):
            with pytest.raises(ArchiveSecurityViolationError):
                _inspect_expat_dtd_and_entities(bad_xml, "word/document.xml")

        malicious_archives: list[tuple[str, dict[str, bytes]]] = [
            (
                "utf16le_dtd.docx",
                {"word/document.xml": utf16_ext_dtd.encode("utf-16-le")},
            ),
            (
                "utf16be_dtd.odt",
                {"Pictures/diagram.svg": b"\xfe\xff" + utf16_ext_dtd.encode("utf-16-be")},
            ),
            (
                "utf16le_entity.docx",
                {"word/document.xml": b"\xff\xfe" + utf16_int_entity.encode("utf-16-le")},
            ),
            (
                "vml_href.docx",
                {
                    "word/drawings/vmlDrawing1.vml": (
                        b'<xml xmlns:v="urn:schemas-microsoft-com:vml" '
                        b'xmlns:o="urn:schemas-microsoft-com:office:office">'
                        b'<v:shape><v:imagedata o:href="http://169.254.169.254/beacon.png"/>'
                        b"</v:shape></xml>"
                    )
                },
            ),
            (
                "vml_src.docx",
                {
                    "word/drawings/vmlDrawing1.vml": (
                        b'<xml xmlns:v="urn:schemas-microsoft-com:vml">'
                        b'<v:shape><v:imagedata src="https://evil.example/beacon.png"/>'
                        b"</v:shape></xml>"
                    )
                },
            ),
            (
                "svg_xlink.docx",
                {
                    "word/media/image1.svg": (
                        b'<svg xmlns="http://www.w3.org/2000/svg" '
                        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
                        b'<image xlink:href="http://evil.example/track.svg"/></svg>'
                    )
                },
            ),
            (
                "svg_css_url.odt",
                {
                    "Pictures/chart.svg": (
                        b'<svg xmlns="http://www.w3.org/2000/svg">'
                        b'<rect width="10" height="10" '
                        b'style="fill:url(http://169.254.169.254/latest)"/></svg>'
                    )
                },
            ),
            (
                "xhtml_img_src.odt",
                {
                    "embed/page.xhtml": (
                        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                        b'<img src="https://evil.example/pixel.png"/></body></html>'
                    )
                },
            ),
            (
                "xhtml_iframe_src.docx",
                {
                    "word/embed.xhtml": (
                        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                        b'<iframe src="http://evil.example/frame"/></body></html>'
                    )
                },
            ),
            (
                "xhtml_script_link.docx",
                {
                    "word/embed.xhtml": (
                        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                        b'<link rel="stylesheet" href="http://evil.example/a.css"/>'
                        b'<script src="https://evil.example/a.js"/></head><body/></html>'
                    )
                },
            ),
            (
                "nested_fodt.odt",
                {
                    "sub/part.fodt": (
                        b"<office:document "
                        b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
                        b'<text:section-source xlink:href="http://evil.example/part.odt"/>'
                        b"</office:document>"
                    )
                },
            ),
            (
                "html_ascii_ssrf.docx",
                {
                    "word/afchunk.html": (
                        b'<html><body><img src="http://169.254.169.254/latest"/></body></html>'
                    )
                },
            ),
            (
                "html_utf16le_odd_ssrf.docx",
                {
                    "word/afchunk.htm": (
                        b"\x01"
                        + '<!DOCTYPE html SYSTEM "http://evil.example/ext.dtd"><html></html>'.encode(
                            "utf-16-le"
                        )
                    )
                },
            ),
            (
                "html_meta_refresh.docx",
                {
                    "word/afchunk.html": (
                        b'<html><head><meta http-equiv="refresh" '
                        b'content="0; url=http://evil.example/redir"></head></html>'
                    )
                },
            ),
        ]

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen") as mock_popen,
        ):
            for fname, entries in malicious_archives:
                archive_bytes = _zip_from_raw_entries(entries)
                with pytest.raises(ArchiveSecurityViolationError) as exc_info:
                    adapter.parse_bytes(archive_bytes, filename=fname)
                assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
            mock_popen.assert_not_called()

        # Benign internal SVG & VML with local relative targets pass container inspection
        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_safe_svg",
            filename="out.pdf",
            mime_type="application/pdf",
            total_pages=1,
            total_characters=4,
            chunks=[],
            page_map=[],
            tables=[],
            taint=TaintClassification.INTERNAL,
            source_sha256="intermediate",
            normalized_text="Safe",
            metadata={},
        )
        safe_adapter = LibreOfficeDocumentAdapter(
            soffice_path="mock_soffice", pdf_adapter=mock_pdf_adapter
        )
        benign_zip = _zip_from_raw_entries(
            {
                "word/document.xml": (
                    b'<?xml version="1.0" encoding="UTF-8"?>'
                    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    b"<w:body><w:p/></w:body></w:document>"
                ),
                "word/media/diagram.svg": (
                    b'<svg xmlns="http://www.w3.org/2000/svg" '
                    b'xmlns:xlink="http://www.w3.org/1999/xlink">'
                    b'<defs><linearGradient id="g1"/></defs>'
                    b'<rect width="10" height="10" style="fill:url(#g1)"/>'
                    b'<image xlink:href="local.png"/></svg>'
                ),
                "word/drawings/vmlDrawing1.vml": (
                    b'<xml xmlns:v="urn:schemas-microsoft-com:vml">'
                    b'<v:shape><v:imagedata src="media/image1.png"/></v:shape></xml>'
                ),
            }
        )

        def _mock_safe_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            (out_dir / f"{Path(cmd[-1]).stem}.pdf").write_bytes(b"%PDF-1.5 mock")
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=_mock_safe_popen),
        ):
            res_safe = safe_adapter.parse_bytes(benign_zip, filename="safe_diagram.docx")
            assert res_safe.normalized_text == "Safe"

    def test_ole_cfb_allows_benign_datavault_prose_while_blocking_external_fields(self) -> None:
        """Regression R6-MAJ-02: Allow Data Vault 2.0 prose while blocking OLE field attacks."""
        benign_dv_prose_samples = [
            'Create a link "L_CUSTOMER_ORDER" between H_CUSTOMER and H_ORDER.',
            "Daily ETL jobs import orders.csv into the Bronze staging area.",
            "Join hub H_CUSTOMER to link L_CUST.ACCT on business key.",
            "COLUMN HEADER: LINK_NAME | HUB_SOURCE | LINK BUSINESS_KEY",
            "Architecture Overview: DATA IMPORT PIPELINE",
            "123 MAIN RD SUITE 400",
        ]

        mock_pdf_adapter = MagicMock(spec=LiteParseDocumentAdapter)
        mock_pdf_adapter.parse_bytes.return_value = IngestionResult(
            document_id="doc_dv",
            filename="out.pdf",
            mime_type="application/pdf",
            total_pages=1,
            total_characters=2,
            chunks=[],
            page_map=[],
            tables=[],
            taint=TaintClassification.INTERNAL,
            source_sha256="intermediate",
            normalized_text="OK",
            metadata={},
        )
        adapter = LibreOfficeDocumentAdapter(
            soffice_path="mock_soffice", pdf_adapter=mock_pdf_adapter
        )

        def _mock_ok_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            (out_dir / f"{Path(cmd[-1]).stem}.pdf").write_bytes(b"%PDF-1.5 mock")
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate.return_value = (b"", b"")
            return proc

        with (
            patch(
                "opendv_sources.adapters.libreoffice_adapter.find_soffice_binary",
                return_value="mock_soffice",
            ),
            patch("subprocess.Popen", side_effect=_mock_ok_popen),
        ):
            for ext in (".doc", ".xls", ".ppt"):
                for prose in benign_dv_prose_samples:
                    for raw_stream in (
                        b"\x00" * 16 + prose.encode("latin-1"),
                        b"\x00\x00" + prose.encode("utf-16-le") + b"\x00\x00",
                        b"\x01" + prose.encode("utf-16-le") + b"\x00",
                    ):
                        cfb = self._minimal_ole_cfb_bytes(raw_stream)
                        _inspect_ole_cfb_security(cfb, ext)
                        parsed = adapter.parse_bytes(cfb, filename=f"dv_spec{ext}")
                        assert parsed.normalized_text == "OK"

        extra_attack_vectors = [
            '\x13 LINK Excel.Sheet.8 "book.xls"',
            'LINK Excel.Sheet.8 "C:\\secrets\\book.xls"',
            'LINK "http://evil.example/book.xls"',
            'RD "/etc/passwd"',
            'IMPORT "\\\\169.254.169.254\\share\\orders.csv"',
            'DDE("soffice"; "C:\\secrets\\a.ods")',
            'RTD("my.rtd", "evil.example", "topic")',
        ]
        for ext in (".doc", ".xls", ".ppt"):
            for attack in extra_attack_vectors:
                for raw_stream in (
                    b"\x00" * 16 + attack.encode("latin-1"),
                    b"\x00\x00" + attack.encode("utf-16-le") + b"\x00\x00",
                    b"\x01" + attack.encode("utf-16-le") + b"\x00",
                ):
                    with pytest.raises(ArchiveSecurityViolationError):
                        _inspect_ole_cfb_security(self._minimal_ole_cfb_bytes(raw_stream), ext)


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
