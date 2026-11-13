"""Multi-Format Ingestion Adapters (Task 4B.4 / Issue #204).

Implements concrete document adapters inheriting from DocumentAdapter SPI:
- MarkdownDocumentAdapter: Heading hierarchy, code block quarantine, table extraction.
- DelimitedTabularAdapter: CSV/TSV streaming parsing, type inference, cell truncation.
- PyArrowTabularAdapter: Parquet schema metadata, row counts, preview chunks.
- StructuredDataDocumentAdapter: JSON, JSONL, YAML unnesting, jsonpath mapping, depth limits.
- MarkupDocumentAdapter: HTML/XML readability extraction, script/style/nav stripping.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any

import yaml

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    PageMapEntry,
    TableStructure,
    TaintClassification,
)
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    extract_tables_with_spans,
)
from opendv_sources.exceptions import (
    DocumentExtractionError,
    DocumentPayloadTooLargeError,
    UnsupportedFormatError,
)
from opendv_sources.ingestion.adapter import BaseDocumentAdapter
from opendv_sources.ingestion.normalization import (
    compute_utf16_offsets_for_points,
    normalize_text,
)

logger = logging.getLogger(__name__)

# Try importing pyarrow
try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    HAS_PYARROW = True
except (ImportError, OSError):
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]
    HAS_PYARROW = False

# Maximum allowed CSV/TSV cell characters to prevent memory exhaustion (Task 4B.4)
MAX_CELL_CHARS_DEFAULT: int = 32_768

# Maximum recursion depth for structured data unnesting (Task 4B.4)
MAX_RECURSION_DEPTH_DEFAULT: int = 32

# Maximum byte size for text-based documents (50 MiB default, P04::MAJ-03)
MAX_TEXT_DOCUMENT_BYTES_DEFAULT: int = 50 * 1024 * 1024

# Maximum YAML alias expansions and expanded DAG nodes (P04::MAJ-04, MAJ-07)
MAX_YAML_ALIASES_DEFAULT: int = 1_000
MAX_STRUCTURED_NODES_DEFAULT: int = 100_000
MAX_JSON_PATHS_SAMPLE: int = 100

# Parquet decompression / preview safety bounds (P04::MAJ-01)
MAX_PARQUET_INPUT_BYTES_DEFAULT: int = 50 * 1024 * 1024
MAX_PARQUET_METADATA_ROWS_DEFAULT: int = 100_000_000
MAX_PARQUET_UNCOMPRESSED_BYTES_DEFAULT: int = 512 * 1024 * 1024
MAX_PARQUET_COMPRESSION_RATIO_DEFAULT: float = 500.0
MAX_PARQUET_COLUMNS_DEFAULT: int = 10_000
MAX_PARQUET_PREVIEW_CELL_CHARS_DEFAULT: int = 4_096
MAX_PARQUET_PREVIEW_BYTES_DEFAULT: int = 1 * 1024 * 1024

# Configure process-wide CSV field size limit once safely at import time (MAJ-07)
_CSV_MIN_FIELD_SIZE_LIMIT: int = 10 * 1024 * 1024
with contextlib.suppress(OverflowError, OSError):
    if csv.field_size_limit() < _CSV_MIN_FIELD_SIZE_LIMIT:
        csv.field_size_limit(_CSV_MIN_FIELD_SIZE_LIMIT)


def decode_text_payload(
    data: bytes,
    filename: str,
    mime_type: str | None = None,
    adapter_name: str = "text",
    *,
    max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT,
) -> tuple[str, str]:
    """Decode raw document bytes into normalized Unicode text with BOM and binary detection.

    Args:
        data: Raw file bytes.
        filename: Original filename.
        mime_type: Optional MIME media type.
        adapter_name: Adapter identifier for error messages.
        max_bytes: Optional maximum byte size guard.

    Returns:
        Tuple of (normalized_text, encoding_name).

    Raises:
        InvalidDocumentError: If data is empty or exceeds ``max_bytes``.
        UnsupportedFormatError: If binary data is detected without text encoding.
    """
    if not data:
        raise InvalidDocumentError(f"Document '{filename}' is empty (0 bytes)")
    if max_bytes is not None and len(data) > max_bytes:
        raise InvalidDocumentError(
            f"Document '{filename}' exceeds maximum allowed size ({len(data)} > {max_bytes} bytes)"
        )

    has_explicit_bom = True
    if data.startswith(b"\xef\xbb\xbf"):
        clean_data = data[3:]
        encoding = "utf-8"
    elif data.startswith(b"\xff\xfe\x00\x00"):
        clean_data = data[4:]
        encoding = "utf-32-le"
    elif data.startswith(b"\x00\x00\xfe\xff"):
        clean_data = data[4:]
        encoding = "utf-32-be"
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        clean_data = data
        encoding = "utf-16"
    else:
        has_explicit_bom = False
        clean_data = data
        encoding = "utf-8"

    # Reject binary data with NUL bytes if no explicit BOM
    if not has_explicit_bom and b"\x00" in clean_data[:4096]:
        raise UnsupportedFormatError(
            f"File '{filename}' contains binary data (NUL bytes detected) and cannot be "
            "parsed as text",
            requested=mime_type or filename,
            available=[adapter_name],
        )

    try:
        raw_text = clean_data.decode(encoding)
    except UnicodeDecodeError as err:
        if has_explicit_bom:
            raise InvalidDocumentError(
                f"Corrupt file with explicit BOM {encoding} failed to decode: {err}"
            ) from err

        # Check control character density for binary files
        sample = clean_data[:4096]
        control_count = sum(1 for b in sample if b < 32 and b not in (9, 10, 13))
        if control_count > len(sample) * 0.15:
            raise UnsupportedFormatError(
                f"File '{filename}' contains binary data and cannot be parsed as text",
                requested=mime_type or filename,
                available=[adapter_name],
            ) from None

        try:
            raw_text = clean_data.decode("cp1252")
            encoding = "cp1252"
        except UnicodeDecodeError:
            raw_text = clean_data.decode("latin-1")
            encoding = "latin-1"

    normalized_text = normalize_text(raw_text)
    return normalized_text, encoding


def _build_page_map_entry(
    text: str,
    page_number: int = 1,
    has_tables: bool = False,
    confidence: float = 1.0,
) -> PageMapEntry:
    """Build a standard PageMapEntry with UTF-16 offsets."""
    char_count = len(text)
    utf16_map = compute_utf16_offsets_for_points(text, (0, char_count))
    return PageMapEntry(
        page_number=page_number,
        text=text,
        start_offset=0,
        end_offset=char_count,
        utf16_start=utf16_map.get(0, 0),
        utf16_end=utf16_map.get(char_count, char_count),
        confidence=confidence,
        has_tables=has_tables,
    )


def _verify_source_sha256(data: bytes, filename: str, caller_sha256: str | None) -> str:
    """Compute and verify SHA-256 against caller-provided digest."""
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if caller_sha256 and caller_sha256 != actual_sha256:
        raise DocumentExtractionError(
            f"Supplied source_sha256 '{caller_sha256}' does not match computed "
            f"digest '{actual_sha256}' for '{filename}'"
        )
    return actual_sha256


# =============================================================================
# 1. MarkdownDocumentAdapter
# =============================================================================

MAX_MARKDOWN_METADATA_ITEMS_DEFAULT: int = 50_000
_MD_FENCE_RE = re.compile(r"^(```+|~~~+)(.*)$")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


class MarkdownDocumentAdapter(BaseDocumentAdapter):
    """Adapter for Markdown documents (.md, .markdown) with heading hierarchies,
    code block quarantine, and table extraction.
    """

    supported_mime_types: Sequence[str] = (
        "text/markdown",
        "text/x-markdown",
    )
    supported_extensions: Sequence[str] = (
        ".md",
        ".markdown",
    )

    def __init__(
        self,
        *,
        exclude_code_blocks: bool = False,
        max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT,
        max_metadata_items: int = MAX_MARKDOWN_METADATA_ITEMS_DEFAULT,
    ) -> None:
        """Initialize MarkdownDocumentAdapter.

        Args:
            exclude_code_blocks: If True, fenced code blocks are excluded from normalized_text
                and quarantined into metadata only.
            max_bytes: Maximum allowed raw byte size.
            max_metadata_items: Maximum stored heading / code-block metadata dicts.
        """
        self.exclude_code_blocks = exclude_code_blocks
        self.max_bytes = max_bytes
        self.max_metadata_items = max(0, int(max_metadata_items))

    @property
    def adapter_name(self) -> str:
        return "markdown"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw Markdown document bytes into an IngestionResult."""
        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)
        raw_text, encoding = decode_text_payload(
            data,
            filename,
            mime_type=mime_type,
            adapter_name=self.adapter_name,
            max_bytes=self.max_bytes,
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        # 1. Single O(lines) pass for fenced code blocks + heading hierarchy (P04b::MAJ-01)
        code_blocks: list[dict[str, Any]] = []
        code_block_spans: list[tuple[int, int]] = []
        code_block_count = 0
        headings: list[dict[str, Any]] = []
        heading_count = 0

        lines = raw_text.split("\n")
        in_code_block = False
        code_lang = ""
        block_lines: list[str] = []
        block_start_char = 0
        current_offset = 0
        max_meta = self.max_metadata_items

        for line in lines:
            line_start = current_offset
            line_end = line_start + len(line)
            current_offset = line_end + 1  # includes newline

            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                fence_match = _MD_FENCE_RE.match(stripped)
                if fence_match is not None:
                    if not in_code_block:
                        in_code_block = True
                        code_lang = fence_match.group(2).strip()
                        block_start_char = line_start
                        block_lines = []
                    else:
                        in_code_block = False
                        code_block_count += 1
                        code_block_spans.append((block_start_char, line_end))
                        if len(code_blocks) < max_meta:
                            code_blocks.append(
                                {
                                    "language": code_lang,
                                    "code": "\n".join(block_lines),
                                    "start_char": block_start_char,
                                    "end_char": line_end,
                                }
                            )
                    continue

            if in_code_block:
                if len(code_blocks) < max_meta:
                    block_lines.append(line)
                continue

            if stripped.startswith("#"):
                heading_match = _MD_HEADING_RE.match(stripped)
                if heading_match is not None:
                    heading_count += 1
                    if len(headings) < max_meta:
                        headings.append(
                            {
                                "level": len(heading_match.group(1)),
                                "title": heading_match.group(2).strip(),
                                "start_char": line_start,
                                "end_char": line_end,
                            }
                        )

        # If code block wasn't closed at EOF
        if in_code_block:
            code_block_count += 1
            code_block_spans.append((block_start_char, len(raw_text)))
            if len(code_blocks) < max_meta:
                code_blocks.append(
                    {
                        "language": code_lang,
                        "code": "\n".join(block_lines),
                        "start_char": block_start_char,
                        "end_char": len(raw_text),
                    }
                )

        # 2. Optional code block exclusion from normalized text
        if self.exclude_code_blocks and code_block_spans:
            # Reconstruct text excluding code blocks with replacement indicator
            text_segments: list[str] = []
            last_idx = 0
            for start, end in code_block_spans:
                text_segments.append(raw_text[last_idx:start])
                text_segments.append("[CODE BLOCK QUARANTINED]")
                last_idx = end
            text_segments.append(raw_text[last_idx:])
            effective_text = normalize_text("".join(text_segments))
        else:
            effective_text = raw_text

        # 3. Table extraction using liteparse helper
        detected_tables = extract_tables_with_spans(effective_text, page_number=1)
        tables = [d.table for d in detected_tables]

        # 4. Build page map entry and chunks
        page_entry = _build_page_map_entry(
            effective_text, page_number=1, has_tables=bool(tables), confidence=1.0
        )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "encoding": encoding,
                "headings": headings,
                "heading_count": heading_count,
                "code_blocks": code_blocks,
                "code_block_count": code_block_count,
                "quarantined_code_blocks": code_block_count,
            }
        )

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=mime_type or "text/markdown",
            total_pages=1,
            total_characters=len(effective_text),
            chunks=[],
            page_map=[page_entry],
            tables=tables,
            taint=taint,
            source_sha256=actual_sha256,
            normalized_text=effective_text,
            metadata=metadata,
        )


# =============================================================================
# 2. DelimitedTabularAdapter (CSV / TSV)
# =============================================================================


def _infer_column_type(values: list[str]) -> str:
    """Infer high-level column type from a list of non-empty string values."""
    if not values:
        return "string"

    is_int = True
    is_float = True
    is_bool = True

    bool_values = {"true", "false", "yes", "no", "1", "0", "t", "f"}

    for val in values:
        v = val.strip().lower()
        if not v:
            continue
        if v not in bool_values:
            is_bool = False
        if not re.match(r"^-?\d+$", v):
            is_int = False
        if not re.match(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$", v):
            is_float = False

    if is_bool:
        return "boolean"
    if is_int:
        return "integer"
    if is_float:
        return "float"
    return "string"


class DelimitedTabularAdapter(BaseDocumentAdapter):
    """Adapter for delimited tabular data (CSV, TSV) with streaming parsing,
    header type inference, and cell truncation guards.
    """

    supported_mime_types: Sequence[str] = (
        "text/csv",
        "text/tab-separated-values",
    )
    supported_extensions: Sequence[str] = (
        ".csv",
        ".tsv",
    )

    def __init__(
        self,
        *,
        max_cell_chars: int = MAX_CELL_CHARS_DEFAULT,
        max_rows: int | None = None,
        max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT,
    ) -> None:
        """Initialize DelimitedTabularAdapter.

        Args:
            max_cell_chars: Maximum character length per cell before truncation guard activates.
            max_rows: Optional row count limit for preview or bounding ingestion memory.
            max_bytes: Maximum allowed raw byte size.
        """
        self.max_cell_chars = max_cell_chars
        self.max_rows = max_rows
        self.max_bytes = max_bytes

    @property
    def adapter_name(self) -> str:
        return "delimited_tabular"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw CSV or TSV bytes into an IngestionResult."""
        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)
        raw_text, encoding = decode_text_payload(
            data,
            filename,
            mime_type=mime_type,
            adapter_name=self.adapter_name,
            max_bytes=self.max_bytes,
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        # Determine delimiter
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        is_tsv = (
            ext == "tsv"
            or mime_type == "text/tab-separated-values"
            or ("\t" in raw_text[:2048] and "," not in raw_text[:2048])
        )
        delimiter = "\t" if is_tsv else ","

        # Streaming row parsing with cell truncation guard and csv.Error translation (MAJ-07)
        raw_rows: list[list[str]] = []
        truncated_cells_count = 0

        try:
            reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
            for row_idx, row in enumerate(reader):
                if self.max_rows is not None and row_idx >= self.max_rows:
                    break
                guarded_row: list[str] = []
                for cell in row:
                    if len(cell) > self.max_cell_chars:
                        guarded_row.append(cell[: self.max_cell_chars] + "…[TRUNCATED]")
                        truncated_cells_count += 1
                    else:
                        guarded_row.append(cell)
                raw_rows.append(guarded_row)
        except csv.Error as exc:
            raise InvalidDocumentError(f"Malformed CSV/TSV payload in '{filename}': {exc}") from exc

        if not raw_rows:
            raise InvalidDocumentError(f"Tabular document '{filename}' contains no rows")

        headers = [h.strip() for h in raw_rows[0]]
        data_rows = raw_rows[1:]

        # Column type inference
        sample_rows = data_rows[:100]
        column_types: dict[str, str] = {}
        for col_idx, h in enumerate(headers):
            col_vals = [r[col_idx] for r in sample_rows if col_idx < len(r)]
            column_types[h or f"col_{col_idx}"] = _infer_column_type(col_vals)

        # Build Markdown table representation
        divider = ["---"] * len(headers)
        table_lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(divider) + " |",
        ]
        # Include first 50 rows in markdown text representation for readability
        for r in data_rows[:50]:
            padded_row = r[: len(headers)] + [""] * max(0, len(headers) - len(r))
            escaped_cells = [c.replace("|", "\\|").replace("\n", " ") for c in padded_row]
            table_lines.append("| " + " | ".join(escaped_cells) + " |")

        if len(data_rows) > 50:
            table_lines.append(f"\n*... and {len(data_rows) - 50} more rows.*")

        normalized_table_text = normalize_text("\n".join(table_lines))

        table_structure = TableStructure(
            table_id="tbl_1",
            page_number=1,
            headers=headers,
            rows=data_rows,
            raw_html_or_md=normalized_table_text,
            bounding_box=None,
        )

        page_entry = _build_page_map_entry(
            normalized_table_text, page_number=1, has_tables=True, confidence=1.0
        )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "encoding": encoding,
                "delimiter": "\\t" if delimiter == "\t" else delimiter,
                "row_count": len(data_rows),
                "column_count": len(headers),
                "column_types": column_types,
                "truncated_cells_count": truncated_cells_count,
            }
        )

        default_mime = "text/tab-separated-values" if delimiter == "\t" else "text/csv"
        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=mime_type or default_mime,
            total_pages=1,
            total_characters=len(normalized_table_text),
            chunks=[],
            page_map=[page_entry],
            tables=[table_structure],
            taint=taint,
            source_sha256=actual_sha256,
            normalized_text=normalized_table_text,
            metadata=metadata,
        )


# =============================================================================
# 3. PyArrowTabularAdapter (Parquet)
# =============================================================================


class PyArrowTabularAdapter(BaseDocumentAdapter):
    """Adapter for Apache Parquet documents (.parquet) with schema metadata extraction,
    logical type inference, and preview chunk extraction.
    """

    supported_mime_types: Sequence[str] = (
        "application/vnd.apache.parquet",
        "application/parquet",
        "application/x-parquet",
    )
    supported_extensions: Sequence[str] = (".parquet",)

    def __init__(
        self,
        *,
        preview_rows: int = 50,
        max_input_bytes: int | None = MAX_PARQUET_INPUT_BYTES_DEFAULT,
        max_metadata_rows: int = MAX_PARQUET_METADATA_ROWS_DEFAULT,
        max_uncompressed_bytes: int = MAX_PARQUET_UNCOMPRESSED_BYTES_DEFAULT,
        max_compression_ratio: float = MAX_PARQUET_COMPRESSION_RATIO_DEFAULT,
        max_columns: int = MAX_PARQUET_COLUMNS_DEFAULT,
        max_preview_cell_chars: int = MAX_PARQUET_PREVIEW_CELL_CHARS_DEFAULT,
        max_preview_bytes: int = MAX_PARQUET_PREVIEW_BYTES_DEFAULT,
    ) -> None:
        """Initialize PyArrowTabularAdapter.

        Args:
            preview_rows: Number of initial rows to include in the preview TableStructure.
            max_input_bytes: Maximum allowed raw Parquet payload byte size (50 MiB default).
            max_metadata_rows: Maximum allowed row count declared in Parquet metadata.
            max_uncompressed_bytes: Maximum allowed total uncompressed row-group bytes.
            max_compression_ratio: Maximum uncompressed-to-compressed byte ratio for large files.
            max_columns: Maximum allowed column count in schema metadata.
            max_preview_cell_chars: Maximum characters materialized per preview cell.
            max_preview_bytes: Maximum total UTF-8 bytes materialized across preview cells.
        """
        self.preview_rows = preview_rows
        self.max_input_bytes = max_input_bytes
        self.max_metadata_rows = max_metadata_rows
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio
        self.max_columns = max_columns
        self.max_preview_cell_chars = max_preview_cell_chars
        self.max_preview_bytes = max_preview_bytes

    @property
    def adapter_name(self) -> str:
        return "pyarrow_parquet"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw Parquet bytes into an IngestionResult with schema metadata."""
        if not HAS_PYARROW or pq is None:
            raise UnsupportedFormatError(
                "PyArrow library is not installed and is required for Parquet document ingestion",
                requested=mime_type or filename,
                available=[self.adapter_name],
            )

        if not data:
            raise InvalidDocumentError(f"Parquet document '{filename}' is empty (0 bytes)")

        if self.max_input_bytes is not None and len(data) > self.max_input_bytes:
            raise DocumentPayloadTooLargeError(
                f"Parquet document '{filename}' exceeds maximum allowed input size "
                f"({len(data)} > {self.max_input_bytes} bytes)"
            )

        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)

        # Validate standard Parquet magic bytes: 'PAR1' at offset 0 and at last 4 bytes
        if len(data) < 8 or not (data.startswith(b"PAR1") and data.endswith(b"PAR1")):
            raise InvalidDocumentError(
                f"Invalid Parquet document '{filename}': missing 'PAR1' magic header/footer bytes"
            )

        arrow_mem_err: type[BaseException] = getattr(pa, "ArrowMemoryError", MemoryError)
        try:
            parquet_file = pq.ParquetFile(io.BytesIO(data))
            parquet_meta = parquet_file.metadata
            schema_arrow = parquet_file.schema_arrow
        except (MemoryError, arrow_mem_err) as exc:
            raise DocumentPayloadTooLargeError(
                f"Parquet document '{filename}' exhausted memory limits during footer decode: {exc}"
            ) from exc
        except RecursionError as exc:
            raise InvalidDocumentError(
                f"Parquet document '{filename}' exceeded recursion limits during decode: {exc}"
            ) from exc
        except (pa.ArrowException, OSError, ValueError) as exc:
            raise InvalidDocumentError(
                f"Failed to decode Parquet file '{filename}': {exc}"
            ) from exc

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        num_rows = parquet_meta.num_rows
        num_columns = parquet_meta.num_columns
        num_row_groups = parquet_meta.num_row_groups

        if num_rows < 0 or num_rows > self.max_metadata_rows:
            raise DocumentPayloadTooLargeError(
                f"Parquet document '{filename}' declares {num_rows} rows, exceeding maximum "
                f"allowed row count ({self.max_metadata_rows})"
            )
        if num_columns > self.max_columns:
            raise InvalidDocumentError(
                f"Parquet document '{filename}' declares {num_columns} columns, exceeding maximum "
                f"allowed column count ({self.max_columns})"
            )

        total_uncompressed_bytes = 0
        for rg_idx in range(num_row_groups):
            rg_meta = parquet_meta.row_group(rg_idx)
            rg_bytes = max(0, int(getattr(rg_meta, "total_byte_size", 0) or 0))
            total_uncompressed_bytes += rg_bytes
            if total_uncompressed_bytes > self.max_uncompressed_bytes:
                max_unc = self.max_uncompressed_bytes
                raise DocumentPayloadTooLargeError(
                    f"Parquet document '{filename}' uncompressed row-group size exceeds "
                    f"maximum allowed limit ({total_uncompressed_bytes} > {max_unc} bytes)"
                )

        ratio_floor = min(16 * 1024 * 1024, max(65_536, self.max_uncompressed_bytes // 4))
        compression_ratio = total_uncompressed_bytes / max(len(data), 1)
        if (
            total_uncompressed_bytes > ratio_floor
            and compression_ratio > self.max_compression_ratio
        ):
            raise DocumentPayloadTooLargeError(
                f"Parquet document '{filename}' exhibits extreme compression ratio "
                f"({compression_ratio:.1f}:1 > {self.max_compression_ratio:.1f}:1)"
            )

        # Schema and column metadata
        columns_meta: list[dict[str, Any]] = []
        for field in schema_arrow:
            columns_meta.append(
                {
                    "name": field.name,
                    "type": str(field.type),
                    "nullable": field.nullable,
                }
            )

        # Key-value custom metadata (bounded to prevent metadata bomb)
        custom_metadata: dict[str, str] = {}
        if parquet_meta.metadata:
            for idx, (k, v) in enumerate(parquet_meta.metadata.items()):
                if idx >= 256:
                    break
                try:
                    k_str = k[:1024].decode("utf-8")
                    v_str = v[:8192].decode("utf-8")
                except UnicodeDecodeError:
                    k_str = str(k[:1024])
                    v_str = str(v[:8192])
                custom_metadata[k_str] = v_str

        # Stream ONLY a bounded preview batch via iter_batches (never read_row_group / read)
        preview_table_rows: list[list[str]] = []
        headers = list(schema_arrow.names)
        target_preview = max(0, min(num_rows, self.preview_rows))
        if target_preview > 0 and headers:
            try:
                batch_iter = parquet_file.iter_batches(batch_size=max(1, target_preview))
                first_batch = next(batch_iter, None)
                if first_batch is not None:
                    sliced_batch = first_batch[:target_preview]
                    rows_in_batch = min(target_preview, len(sliced_batch))
                    col_arrays = [sliced_batch.column(i) for i in range(len(headers))]
                    materialized_preview_bytes = 0
                    budget_exceeded = False
                    for r_idx in range(rows_in_batch):
                        row_vals: list[str] = []
                        for col_arr in col_arrays:
                            py_val = col_arr[r_idx].as_py()
                            raw_cell = "None" if py_val is None else str(py_val)
                            if len(raw_cell) > self.max_preview_cell_chars:
                                raw_cell = raw_cell[: self.max_preview_cell_chars] + "…[TRUNCATED]"
                            materialized_preview_bytes += len(
                                raw_cell.encode("utf-8", errors="replace")
                            )
                            if materialized_preview_bytes > self.max_preview_bytes:
                                budget_exceeded = True
                                break
                            row_vals.append(raw_cell)
                        if budget_exceeded:
                            break
                        preview_table_rows.append(row_vals)
            except (MemoryError, arrow_mem_err) as exc:
                raise DocumentPayloadTooLargeError(
                    f"Parquet preview extraction exhausted memory on '{filename}': {exc}"
                ) from exc
            except RecursionError as exc:
                raise InvalidDocumentError(
                    f"Parquet preview extraction exceeded recursion limits on '{filename}': {exc}"
                ) from exc
            except (pa.ArrowException, OSError, ValueError, KeyError) as err:
                logger.warning(
                    "Failed to extract Parquet preview batch from '%s': %s", filename, err
                )

        # Format textual representation
        lines = [
            f"# Parquet Dataset: {filename}",
            f"- Total Rows: {num_rows}",
            f"- Columns: {num_columns}",
            f"- Row Groups: {num_row_groups}",
            "",
            "## Schema Definition",
            "| Column | Arrow Type | Nullable |",
            "| --- | --- | --- |",
        ]
        for c in columns_meta:
            lines.append(f"| {c['name']} | {c['type']} | {c['nullable']} |")

        if preview_table_rows:
            lines.append("")
            lines.append(f"## Preview Rows (first {len(preview_table_rows)})")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for r in preview_table_rows:
                escaped = [c.replace("|", "\\|").replace("\n", " ") for c in r]
                lines.append("| " + " | ".join(escaped) + " |")

        normalized_text = normalize_text("\n".join(lines))

        table_structure = TableStructure(
            table_id="tbl_parquet_1",
            page_number=1,
            headers=headers,
            rows=preview_table_rows,
            raw_html_or_md=normalized_text,
            bounding_box=None,
        )

        page_entry = _build_page_map_entry(
            normalized_text, page_number=1, has_tables=bool(preview_table_rows), confidence=1.0
        )

        ingestion_metadata = dict(kwargs.get("metadata") or {})
        ingestion_metadata.update(
            {
                "num_rows": num_rows,
                "num_columns": num_columns,
                "num_row_groups": num_row_groups,
                "columns": columns_meta,
                "custom_metadata": custom_metadata,
            }
        )

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=mime_type or "application/vnd.apache.parquet",
            total_pages=1,
            total_characters=len(normalized_text),
            chunks=[],
            page_map=[page_entry],
            tables=[table_structure] if preview_table_rows else [],
            taint=taint,
            source_sha256=actual_sha256,
            normalized_text=normalized_text,
            metadata=ingestion_metadata,
        )


# =============================================================================
# 4. StructuredDataDocumentAdapter (JSON, JSONL, YAML)
# =============================================================================


class _BoundedSafeLoader(yaml.SafeLoader):
    """Safe YAML loader bounding alias expansions to defend against 'billion laughs' payloads."""

    max_aliases: int = MAX_YAML_ALIASES_DEFAULT

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._alias_count = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            self._alias_count += 1
            if self._alias_count > self.max_aliases:
                event = self.peek_event()
                raise yaml.composer.ComposerError(
                    None,
                    None,
                    (
                        "YAML alias expansion count exceeds maximum allowed limit "
                        f"({self.max_aliases})"
                    ),
                    getattr(event, "start_mark", None),
                )
        return super().compose_node(parent, index)


_YAML_BLOCK_SCALAR_HEADER_RE = re.compile(r"(?:^|[:\-\?]\s*)[|>][0-9+-]*\s*$")
_YAML_INLINE_SEQ_CHAIN_RE = re.compile(r"^(?:-\s+)+")


def _check_json_bracket_depth(raw_text: str, *, max_depth: int, filename: str) -> None:
    """Fast O(N) pre-parse unquoted bracket depth guard prior to `json.loads`."""
    in_string = False
    escaped = False
    depth = 0
    for ch in raw_text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > max_depth:
                raise InvalidDocumentError(
                    f"Structured payload in '{filename}' exceeds maximum recursion depth of "
                    f"{max_depth}"
                )
        elif ch in "]}" and depth > 0:
            depth -= 1


def _strip_yaml_unquoted_comment_and_scan_flow(
    line: str,
    *,
    flow_depth: int,
    in_single: bool,
    in_double: bool,
    max_depth: int,
    filename: str,
) -> tuple[str, int, bool, bool]:
    """Scan a YAML line for unquoted flow brackets while tracking quote state."""
    escaped = False
    comment_pos: int | None = None
    for idx, ch in enumerate(line):
        if in_double:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            continue
        if in_single:
            if ch == "'":
                # In YAML single-quoted strings, '' escapes a single quote (toggles twice -> True)
                in_single = False
            continue
        if ch == '"':
            in_double = True
        elif ch == "'":
            in_single = True
        elif ch == "#" and (idx == 0 or line[idx - 1].isspace()):
            comment_pos = idx
            break
        elif ch in "[{":
            flow_depth += 1
            if flow_depth > max_depth:
                raise InvalidDocumentError(
                    f"Structured payload in '{filename}' exceeds maximum recursion depth of "
                    f"{max_depth}"
                )
        elif ch in "]}" and flow_depth > 0:
            flow_depth -= 1

    effective_line = line[:comment_pos] if comment_pos is not None else line
    return effective_line, flow_depth, in_single, in_double


def _check_yaml_preparse_depth(raw_text: str, *, max_depth: int, filename: str) -> None:
    """Fast O(N) pre-parse bracket and indentation depth guard prior to PyYAML composition."""
    flow_depth = 0
    in_single = False
    in_double = False
    block_scalar_indent: int | None = None
    indent_stack: list[int] = []

    for line in raw_text.splitlines():
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)

        if block_scalar_indent is not None:
            if not stripped or stripped.startswith("#") or indent > block_scalar_indent:
                continue
            block_scalar_indent = None

        if not in_single and not in_double:
            if not stripped or stripped.startswith("#"):
                continue
            if flow_depth == 0:
                if stripped in ("---", "..."):
                    indent_stack.clear()
                    continue
                while indent_stack and indent_stack[-1] >= indent:
                    indent_stack.pop()
                indent_stack.append(indent)
                seq_match = _YAML_INLINE_SEQ_CHAIN_RE.match(stripped)
                extra_seq_depth = seq_match.group(0).count("-") if seq_match else 0
                if len(indent_stack) + extra_seq_depth > max_depth + 1:
                    raise InvalidDocumentError(
                        f"Structured payload in '{filename}' exceeds maximum recursion depth of "
                        f"{max_depth}"
                    )

        effective_line, flow_depth, in_single, in_double = (
            _strip_yaml_unquoted_comment_and_scan_flow(
                line,
                flow_depth=flow_depth,
                in_single=in_single,
                in_double=in_double,
                max_depth=max_depth,
                filename=filename,
            )
        )

        if (
            not in_single
            and not in_double
            and flow_depth == 0
            and _YAML_BLOCK_SCALAR_HEADER_RE.search(effective_line.rstrip())
        ):
            block_scalar_indent = indent


def _safe_load_yaml_bounded(raw_text: str, *, max_aliases: int = MAX_YAML_ALIASES_DEFAULT) -> Any:
    """Parse YAML using SafeLoader with a bounded alias expansion budget."""
    if max_aliases == MAX_YAML_ALIASES_DEFAULT:
        loader_cls: type[yaml.SafeLoader] = _BoundedSafeLoader
    else:
        loader_cls = type(
            "_CustomBoundedSafeLoader",
            (_BoundedSafeLoader,),
            {"max_aliases": max_aliases},
        )
    return yaml.load(raw_text, Loader=loader_cls)


def _format_bounded_cell(val: Any, max_chars: int = MAX_CELL_CHARS_DEFAULT) -> str:
    """Format a structured table cell without unbounded DAG expansion."""
    if val is None:
        return ""
    if isinstance(val, (str, int, float, bool)):
        text = str(val)
    elif isinstance(val, dict):
        text = f"<dict len={len(val)}>"
    elif isinstance(val, (list, tuple)):
        text = f"<list len={len(val)}>"
    else:
        text = str(val)
    if len(text) > max_chars:
        return text[:max_chars] + "…[TRUNCATED]"
    return text


def _check_depth_and_flatten(
    obj: Any,
    prefix: str = "",
    current_depth: int = 1,
    max_depth: int = MAX_RECURSION_DEPTH_DEFAULT,
    paths: list[str] | None = None,
    field_names: set[str] | None = None,
    *,
    max_nodes: int = MAX_STRUCTURED_NODES_DEFAULT,
    max_path_samples: int = MAX_JSON_PATHS_SAMPLE,
    active_ids: set[int] | None = None,
    _counters: list[int] | None = None,
) -> tuple[int, list[str], set[str]]:
    """Validate recursion depth, detect cycles, bound visited nodes, and sample JSON paths."""
    if paths is None:
        paths = []
    if field_names is None:
        field_names = set()
    if active_ids is None:
        active_ids = set()
    if _counters is None:
        # _counters[0] = visited_nodes, _counters[1] = total_path_count
        _counters = [0, 0]

    _counters[0] += 1
    if _counters[0] > max_nodes:
        raise InvalidDocumentError(
            f"Structured payload exceeds maximum expanded node count of {max_nodes}"
        )

    if current_depth > max_depth:
        raise InvalidDocumentError(
            f"Structured payload exceeds maximum recursion depth of {max_depth}"
        )

    max_found_depth = current_depth

    if isinstance(obj, dict):
        obj_id = id(obj)
        if obj_id in active_ids:
            raise InvalidDocumentError("Cyclic reference detected in structured payload")
        active_ids.add(obj_id)
        try:
            for key, value in obj.items():
                field_name = str(key)
                field_names.add(field_name)
                _counters[1] += 1
                if len(paths) < max_path_samples:
                    child_prefix = f"{prefix}.{field_name}" if prefix else field_name
                    paths.append(child_prefix)
                else:
                    child_prefix = ""
                child_depth, _, _ = _check_depth_and_flatten(
                    value,
                    prefix=child_prefix,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    paths=paths,
                    field_names=field_names,
                    max_nodes=max_nodes,
                    max_path_samples=max_path_samples,
                    active_ids=active_ids,
                    _counters=_counters,
                )
                max_found_depth = max(max_found_depth, child_depth)
        finally:
            active_ids.discard(obj_id)
    elif isinstance(obj, (list, tuple)):
        obj_id = id(obj)
        if obj_id in active_ids:
            raise InvalidDocumentError("Cyclic reference detected in structured payload")
        active_ids.add(obj_id)
        try:
            for idx, item in enumerate(obj):
                _counters[1] += 1
                if len(paths) < max_path_samples:
                    child_prefix = f"{prefix}[{idx}]"
                    paths.append(child_prefix)
                else:
                    child_prefix = ""
                child_depth, _, _ = _check_depth_and_flatten(
                    item,
                    prefix=child_prefix,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    paths=paths,
                    field_names=field_names,
                    max_nodes=max_nodes,
                    max_path_samples=max_path_samples,
                    active_ids=active_ids,
                    _counters=_counters,
                )
                max_found_depth = max(max_found_depth, child_depth)
        finally:
            active_ids.discard(obj_id)

    return max_found_depth, paths, field_names


class StructuredDataDocumentAdapter(BaseDocumentAdapter):
    """Adapter for JSON, JSON Lines, and YAML documents with schema-aware unnesting,
    jsonpath mapping, and recursion depth limits.
    """

    supported_mime_types: Sequence[str] = (
        "application/json",
        "application/x-jsonlines",
        "application/jsonlines",
        "application/x-yaml",
        "text/yaml",
        "application/yaml",
    )
    supported_extensions: Sequence[str] = (
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
    )

    def __init__(
        self,
        *,
        max_depth: int = MAX_RECURSION_DEPTH_DEFAULT,
        max_nodes: int = MAX_STRUCTURED_NODES_DEFAULT,
        max_aliases: int = MAX_YAML_ALIASES_DEFAULT,
        max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT,
    ) -> None:
        """Initialize StructuredDataDocumentAdapter.

        Args:
            max_depth: Maximum allowable nesting depth to prevent stack overflow on deep payloads.
            max_nodes: Maximum allowable visited DAG nodes across expanded structures.
            max_aliases: Maximum allowable YAML alias expansions.
            max_bytes: Maximum allowed raw byte size.
        """
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_aliases = max_aliases
        self.max_bytes = max_bytes

    @property
    def adapter_name(self) -> str:
        return "structured_data"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw JSON, JSONL, or YAML bytes into an IngestionResult."""
        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)
        raw_text, encoding = decode_text_payload(
            data,
            filename,
            mime_type=mime_type,
            adapter_name=self.adapter_name,
            max_bytes=self.max_bytes,
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        jsonlines_mimes = ("application/x-jsonlines", "application/jsonlines")
        is_jsonl = ext == "jsonl" or mime_type in jsonlines_mimes
        yaml_mimes = ("application/x-yaml", "text/yaml", "application/yaml")
        is_yaml = ext in ("yaml", "yml") or mime_type in yaml_mimes

        # Parse data structure with fast pre-parse depth guard + safe exception translation
        if is_jsonl:
            parsed_data: list[Any] = []
            for line_no, line in enumerate(raw_text.splitlines(), start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                _check_json_bracket_depth(clean_line, max_depth=self.max_depth, filename=filename)
                try:
                    parsed_data.append(json.loads(clean_line))
                except json.JSONDecodeError as err:
                    raise InvalidDocumentError(
                        f"Invalid JSON Lines payload in '{filename}' at line {line_no}: {err}"
                    ) from err
                except (RecursionError, ValueError, MemoryError) as err:
                    raise InvalidDocumentError(
                        f"Invalid JSON Lines payload in '{filename}' at line {line_no}: {err}"
                    ) from err
            if not parsed_data:
                raise InvalidDocumentError(
                    f"JSON Lines document '{filename}' contains no valid objects"
                )
            payload = parsed_data
            resolved_mime = "application/x-jsonlines"
        elif is_yaml:
            _check_yaml_preparse_depth(raw_text, max_depth=self.max_depth, filename=filename)
            try:
                payload = _safe_load_yaml_bounded(raw_text, max_aliases=self.max_aliases)
            except (yaml.YAMLError, RecursionError, ValueError, MemoryError) as err:
                raise InvalidDocumentError(f"Invalid YAML payload in '{filename}': {err}") from err
            resolved_mime = mime_type or "application/x-yaml"
        else:
            _check_json_bracket_depth(raw_text, max_depth=self.max_depth, filename=filename)
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as err:
                raise InvalidDocumentError(f"Invalid JSON payload in '{filename}': {err}") from err
            except (RecursionError, ValueError, MemoryError) as err:
                raise InvalidDocumentError(f"Invalid JSON payload in '{filename}': {err}") from err
            resolved_mime = mime_type or "application/json"

        # Check recursion depth, cycles, bounded node count, and unnest jsonpaths
        counters = [0, 0]
        observed_depth, json_paths, discovered_fields = _check_depth_and_flatten(
            payload,
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            _counters=counters,
        )

        # Extract potential tabular data (e.g., list of dicts)
        tables: list[TableStructure] = []
        candidate_list: list[Any] | None = None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            candidate_list = payload
        elif isinstance(payload, dict):
            for key in ("data", "items", "rows", "records", "results"):
                val = payload.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    candidate_list = val
                    break

        if candidate_list:
            headers: list[str] = []
            for item in candidate_list[:100]:
                if isinstance(item, dict):
                    for k in item:
                        if str(k) not in headers:
                            headers.append(str(k))
            if headers:
                rows: list[list[str]] = []
                for item in candidate_list[:500]:
                    if isinstance(item, dict):
                        row = [_format_bounded_cell(item.get(h)) for h in headers]
                        rows.append(row)
                tbl = TableStructure(
                    table_id="tbl_struct_1",
                    page_number=1,
                    headers=headers,
                    rows=rows,
                    raw_html_or_md=None,
                    bounding_box=None,
                )
                tables.append(tbl)

        # Format normalized text representation
        if is_yaml:
            normalized_text = normalize_text(raw_text)
        else:
            try:
                normalized_text = normalize_text(json.dumps(payload, indent=2, ensure_ascii=False))
            except (RecursionError, ValueError, TypeError, MemoryError) as err:
                raise InvalidDocumentError(
                    f"Invalid structured payload in '{filename}': {err}"
                ) from err

        page_entry = _build_page_map_entry(
            normalized_text, page_number=1, has_tables=bool(tables), confidence=1.0
        )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "encoding": encoding,
                "max_depth": observed_depth,
                "field_count": len(discovered_fields),
                "fields": sorted(discovered_fields),
                "json_path_count": counters[1],
                "json_paths_sample": json_paths[:MAX_JSON_PATHS_SAMPLE],
            }
        )

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=resolved_mime,
            total_pages=1,
            total_characters=len(normalized_text),
            chunks=[],
            page_map=[page_entry],
            tables=tables,
            taint=taint,
            source_sha256=actual_sha256,
            normalized_text=normalized_text,
            metadata=metadata,
        )


# =============================================================================
# 5. MarkupDocumentAdapter (HTML, XML)
# =============================================================================


class _HTMLReadabilityParser(HTMLParser):
    """Lenient HTML parser stripping scripts, styles, navigation, and extracting text/tables."""

    IGNORED_TAGS = frozenset(
        {
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "noscript",
            "svg",
            "aside",
            "iframe",
        }
    )

    BLOCK_TAGS = frozenset(
        {
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "div",
            "article",
            "section",
            "li",
            "blockquote",
            "pre",
            "hr",
            "tr",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self.ignore_depth = 0
        self.text_parts: list[str] = []
        self.title: str = ""
        self.in_title = False
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low_tag = tag.lower()
        if low_tag in self.IGNORED_TAGS:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return

        if low_tag == "title":
            self.in_title = True
        elif low_tag == "table":
            self._current_table = []
        elif low_tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif low_tag in ("td", "th") and self._current_row is not None:
            self._current_cell = []
        elif low_tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        low_tag = tag.lower()
        if low_tag in self.IGNORED_TAGS:
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if self.ignore_depth > 0:
            return

        if low_tag == "title":
            self.in_title = False
        elif low_tag in ("td", "th") and self._current_cell is not None:
            cell_text = "".join(self._current_cell).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell = None
        elif low_tag == "tr" and self._current_row is not None:
            if self._current_table is not None and self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif low_tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None
        elif low_tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignore_depth > 0:
            return
        if self.in_title:
            self.title += data
        if self._current_cell is not None:
            self._current_cell.append(data)
        self.text_parts.append(data)


class MarkupDocumentAdapter(BaseDocumentAdapter):
    """Adapter for HTML and XML markup documents with readability text extraction,
    script/style/nav stripping, and tabular structural extraction.
    """

    supported_mime_types: Sequence[str] = (
        "text/html",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
    )
    supported_extensions: Sequence[str] = (
        ".html",
        ".htm",
        ".xhtml",
        ".xml",
    )

    def __init__(self, *, max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT) -> None:
        self.max_bytes = max_bytes

    @property
    def adapter_name(self) -> str:
        return "markup"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw HTML or XML bytes into an IngestionResult."""
        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)
        raw_text, encoding = decode_text_payload(
            data,
            filename,
            mime_type=mime_type,
            adapter_name=self.adapter_name,
            max_bytes=self.max_bytes,
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        is_xml = ext == "xml" or mime_type in ("application/xml", "text/xml")

        extracted_tables: list[TableStructure] = []
        title: str = ""

        if is_xml:
            # Parse XML via standard ElementTree
            try:
                root = ET.fromstring(raw_text)
                title = root.tag
                xml_texts: list[str] = []
                for elem in root.iter():
                    if elem.text and elem.text.strip():
                        xml_texts.append(elem.text.strip())
                    if elem.tail and elem.tail.strip():
                        xml_texts.append(elem.tail.strip())
                clean_text = "\n\n".join(xml_texts) if xml_texts else raw_text
                resolved_mime = mime_type or "application/xml"
            except ET.ParseError:
                # Lenient fallback to HTML parser if XML is slightly malformed
                parser = _HTMLReadabilityParser()
                parser.feed(raw_text)
                clean_text = "".join(parser.text_parts)
                title = parser.title.strip()
                resolved_mime = mime_type or "application/xml"
        else:
            parser = _HTMLReadabilityParser()
            parser.feed(raw_text)
            clean_text = "".join(parser.text_parts)
            title = parser.title.strip()

            # Process HTML tables
            for idx, raw_tbl in enumerate(parser.tables, start=1):
                if not raw_tbl:
                    continue
                headers = raw_tbl[0]
                rows = raw_tbl[1:]
                table_struct = TableStructure(
                    table_id=f"tbl_html_{idx}",
                    page_number=1,
                    headers=headers,
                    rows=rows,
                    raw_html_or_md=None,
                    bounding_box=None,
                )
                extracted_tables.append(table_struct)
            resolved_mime = mime_type or "text/html"

        # Clean and normalize whitespace
        lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
        normalized_text = normalize_text("\n\n".join(lines))

        page_entry = _build_page_map_entry(
            normalized_text, page_number=1, has_tables=bool(extracted_tables), confidence=1.0
        )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "encoding": encoding,
                "title": title,
                "table_count": len(extracted_tables),
            }
        )

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=resolved_mime,
            total_pages=1,
            total_characters=len(normalized_text),
            chunks=[],
            page_map=[page_entry],
            tables=extracted_tables,
            taint=taint,
            source_sha256=actual_sha256,
            normalized_text=normalized_text,
            metadata=metadata,
        )


__all__ = [
    "DelimitedTabularAdapter",
    "MarkdownDocumentAdapter",
    "MarkupDocumentAdapter",
    "PyArrowTabularAdapter",
    "StructuredDataDocumentAdapter",
    "decode_text_payload",
]
