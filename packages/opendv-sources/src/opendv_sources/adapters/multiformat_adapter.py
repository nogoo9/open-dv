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
import xml.parsers.expat as expat
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
    ArchiveSecurityViolationError,
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

# Parquet decompression / preview safety bounds (P04::MAJ-01, P04b::MAJ-01)
MAX_PARQUET_INPUT_BYTES_DEFAULT: int = 50 * 1024 * 1024
MAX_PARQUET_METADATA_ROWS_DEFAULT: int = 100_000_000
MAX_PARQUET_UNCOMPRESSED_BYTES_DEFAULT: int = 512 * 1024 * 1024
MAX_PARQUET_COMPRESSION_RATIO_DEFAULT: float = 500.0
MAX_PARQUET_COLUMNS_DEFAULT: int = 10_000
MAX_PARQUET_PREVIEW_COLUMNS_DEFAULT: int = 64
MAX_PARQUET_PREVIEW_CELL_CHARS_DEFAULT: int = 4_096
MAX_PARQUET_PREVIEW_BYTES_DEFAULT: int = 1 * 1024 * 1024
MAX_PARQUET_PREVIEW_MEMORY_BYTES_CAP: int = 64 * 1024 * 1024
MAX_PARQUET_PAGES_DEFAULT: int = 100_000
_PARQUET_COL_RATIO_MIN_UNCOMPRESSED_BYTES: int = 1_048_576

# Wide table & preview cell bounds (P04b::MAJ-02)
MAX_DELIMITED_COLUMNS_DEFAULT: int = 10_000
MAX_TABLE_COLUMNS_DEFAULT: int = 256
MAX_TABLE_CELLS_DEFAULT: int = 100_000
MAX_TYPE_INFERENCE_CELLS_DEFAULT: int = 250_000

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
        raise DocumentPayloadTooLargeError(
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
    """Compute and verify SHA-256 against caller-provided digest (case-insensitive hex)."""
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if caller_sha256 and str(caller_sha256).strip().lower() != actual_sha256:
        raise DocumentExtractionError(
            f"Supplied source_sha256 '{caller_sha256}' does not match computed "
            f"digest '{actual_sha256}' for '{filename}'"
        )
    return actual_sha256


def _resolve_taint(taint_raw: Any, filename: str) -> TaintClassification:
    """Validate and resolve caller-provided taint classification into TaintClassification."""
    if not taint_raw:
        return TaintClassification.INTERNAL
    try:
        return TaintClassification(taint_raw)
    except ValueError as exc:
        raise InvalidDocumentError(
            f"Invalid taint classification '{taint_raw}' for '{filename}': {exc}"
        ) from exc


# =============================================================================
# 1. MarkdownDocumentAdapter
# =============================================================================

MAX_MARKDOWN_METADATA_ITEMS_DEFAULT: int = 50_000
_MD_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
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
        taint = _resolve_taint(kwargs.get("taint", TaintClassification.INTERNAL), filename)

        # 1. Single O(lines) pass for fenced code blocks + heading hierarchy (P04b::MAJ-01)
        code_blocks: list[dict[str, Any]] = []
        code_block_spans: list[tuple[int, int]] = []
        code_block_count = 0
        headings: list[dict[str, Any]] = []
        heading_count = 0

        lines = raw_text.split("\n")
        in_code_block = False
        open_fence_char = ""
        open_fence_len = 0
        code_lang = ""
        block_lines: list[str] = []
        block_start_char = 0
        current_offset = 0
        max_meta = self.max_metadata_items

        for line in lines:
            line_start = current_offset
            line_end = line_start + len(line)
            current_offset = line_end + 1  # includes newline

            line_no_cr = line[:-1] if line.endswith("\r") else line
            stripped_leading = line_no_cr.lstrip(" ")
            leading_spaces = len(line_no_cr) - len(stripped_leading)

            if leading_spaces <= 3 and stripped_leading.startswith(("```", "~~~")):
                fence_match = _MD_FENCE_RE.match(stripped_leading)
                if fence_match is not None:
                    fence_seq = fence_match.group(1)
                    fence_rest = fence_match.group(2)
                    fence_char = fence_seq[0]
                    if not in_code_block:
                        # CommonMark §4.5: backtick info string cannot contain backticks
                        if fence_char != "`" or "`" not in fence_rest:
                            in_code_block = True
                            open_fence_char = fence_char
                            open_fence_len = len(fence_seq)
                            code_lang = fence_rest.strip()
                            block_start_char = line_start
                            block_lines = []
                            continue
                    elif (
                        fence_char == open_fence_char
                        and len(fence_seq) >= open_fence_len
                        and fence_rest.strip() == ""
                    ):
                        in_code_block = False
                        open_fence_char = ""
                        open_fence_len = 0
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

            stripped = stripped_leading.strip()
            if leading_spaces <= 3 and stripped.startswith("#"):
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

        # 2. Optional code block exclusion (re-align heading spans to effective_text)
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
            headings = []
            heading_count = 0
            eff_offset = 0
            for eff_line in effective_text.split("\n"):
                eff_start = eff_offset
                eff_end = eff_start + len(eff_line)
                eff_offset = eff_end + 1
                eff_no_cr = eff_line[:-1] if eff_line.endswith("\r") else eff_line
                eff_stripped_lead = eff_no_cr.lstrip(" ")
                if len(eff_no_cr) - len(eff_stripped_lead) <= 3:
                    eff_stripped = eff_stripped_lead.strip()
                    if eff_stripped.startswith("#"):
                        h_match = _MD_HEADING_RE.match(eff_stripped)
                        if h_match is not None:
                            heading_count += 1
                            if len(headings) < max_meta:
                                headings.append(
                                    {
                                        "level": len(h_match.group(1)),
                                        "title": h_match.group(2).strip(),
                                        "start_char": eff_start,
                                        "end_char": eff_end,
                                    }
                                )
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


_BOOL_VALUES = frozenset({"true", "false", "yes", "no", "1", "0", "t", "f"})
_INT_VALUE_RE = re.compile(r"^-?\d+$")
_FLOAT_VALUE_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def _infer_column_type(values: list[str]) -> str:
    """Infer high-level column type from a list of non-empty string values."""
    if not values:
        return "string"

    is_int = True
    is_float = True
    is_bool = True

    for val in values:
        v = val.strip().lower()
        if not v:
            continue
        if is_bool and v not in _BOOL_VALUES:
            is_bool = False
        if is_int and _INT_VALUE_RE.match(v) is None:
            is_int = False
        if is_float and _FLOAT_VALUE_RE.match(v) is None:
            is_float = False
        if not is_bool and not is_int and not is_float:
            return "string"

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
        max_columns: int = MAX_DELIMITED_COLUMNS_DEFAULT,
        max_table_columns: int = MAX_TABLE_COLUMNS_DEFAULT,
        max_table_cells: int = MAX_TABLE_CELLS_DEFAULT,
    ) -> None:
        """Initialize DelimitedTabularAdapter.

        Args:
            max_cell_chars: Maximum character length per cell before truncation guard activates.
            max_rows: Optional row count limit for preview or bounding ingestion memory.
            max_bytes: Maximum allowed raw byte size.
            max_columns: Maximum allowed columns in CSV/TSV header or rows.
            max_table_columns: Maximum columns synthesized in TableStructure / Markdown preview.
            max_table_cells: Maximum total cells materialized in TableStructure.
        """
        self.max_cell_chars = max_cell_chars
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.max_columns = max_columns
        self.max_table_columns = max(1, int(max_table_columns))
        self.max_table_cells = max(1, int(max_table_cells))

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
        taint = _resolve_taint(kwargs.get("taint", TaintClassification.INTERNAL), filename)

        # Determine delimiter
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        is_tsv = (
            ext == "tsv"
            or mime_type == "text/tab-separated-values"
            or ("\t" in raw_text[:2048] and "," not in raw_text[:2048])
        )
        delimiter = "\t" if is_tsv else ","

        # Streaming row parsing with column count, cell truncation, and cell budget guards
        all_headers: list[str] | None = None
        table_headers: list[str] = []
        headers_truncated = False
        effective_max_table_rows = 1
        sample_row_budget = 100
        sample_rows: list[list[str]] = []
        table_rows: list[list[str]] = []
        total_data_rows = 0
        truncated_cells_count = 0

        def _guard_cell(cell: str) -> str:
            nonlocal truncated_cells_count
            if len(cell) > self.max_cell_chars:
                truncated_cells_count += 1
                return cell[: self.max_cell_chars] + "…[TRUNCATED]"
            return cell

        try:
            reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
            for row_idx, row in enumerate(reader):
                if self.max_rows is not None and row_idx >= self.max_rows:
                    break
                if len(row) > self.max_columns:
                    raise ArchiveSecurityViolationError(
                        f"Tabular document '{filename}' contains {len(row)} columns at row "
                        f"{row_idx}, exceeding maximum allowed column count ({self.max_columns})"
                    )
                if row_idx == 0:
                    guarded_header = [_guard_cell(cell) for cell in row]
                    all_headers = [h.strip() for h in guarded_header]
                    headers_truncated = len(all_headers) > self.max_table_columns
                    table_headers = all_headers[: self.max_table_columns]
                    effective_max_table_rows = max(
                        1, self.max_table_cells // max(1, len(table_headers))
                    )
                    sample_row_budget = max(
                        1,
                        min(100, MAX_TYPE_INFERENCE_CELLS_DEFAULT // max(1, len(all_headers))),
                    )
                    continue

                total_data_rows += 1
                need_sample = len(sample_rows) < sample_row_budget
                need_table = len(table_rows) < effective_max_table_rows
                if need_sample or need_table:
                    guarded_row = [_guard_cell(cell) for cell in row]
                    if need_sample:
                        sample_rows.append(guarded_row)
                    if need_table:
                        clipped = guarded_row[: len(table_headers)]
                        if len(clipped) < len(table_headers):
                            clipped = clipped + [""] * (len(table_headers) - len(clipped))
                        table_rows.append(clipped)
                else:
                    for cell in row:
                        if len(cell) > self.max_cell_chars:
                            truncated_cells_count += 1
        except csv.Error as exc:
            raise InvalidDocumentError(f"Malformed CSV/TSV payload in '{filename}': {exc}") from exc

        if all_headers is None:
            raise InvalidDocumentError(f"Tabular document '{filename}' contains no rows")

        # Column type inference bounded to <= MAX_TYPE_INFERENCE_CELLS_DEFAULT cells
        column_types: dict[str, str] = {}
        for col_idx, h in enumerate(all_headers):
            col_vals = [r[col_idx] for r in sample_rows if col_idx < len(r)]
            column_types[h or f"col_{col_idx}"] = _infer_column_type(col_vals)

        # Build Markdown table representation bounded by table_headers (<=256) and <=50 rows
        divider = ["---"] * len(table_headers)
        table_lines = [
            "| " + " | ".join(table_headers) + " |",
            "| " + " | ".join(divider) + " |",
        ]
        md_preview_cap = min(50, effective_max_table_rows)
        for r in table_rows[:md_preview_cap]:
            padded_row = r[: len(table_headers)] + [""] * max(0, len(table_headers) - len(r))
            escaped_cells = [c.replace("|", "\\|").replace("\n", " ") for c in padded_row]
            table_lines.append("| " + " | ".join(escaped_cells) + " |")

        if total_data_rows > md_preview_cap:
            table_lines.append(f"\n*... and {total_data_rows - md_preview_cap} more rows.*")

        normalized_table_text = normalize_text("\n".join(table_lines))

        table_structure = TableStructure(
            table_id="tbl_1",
            page_number=1,
            headers=table_headers,
            rows=table_rows,
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
                "row_count": total_data_rows,
                "column_count": len(all_headers),
                "headers_truncated": headers_truncated,
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


def _read_thrift_uvarint(buf: bytes, idx: int, limit: int, filename: str) -> tuple[int, int]:
    """Read an unsigned LEB128 varint (bounded to <=10 bytes) from a Thrift Compact stream."""
    val = 0
    shift = 0
    for _ in range(10):
        if idx >= limit:
            raise ArchiveSecurityViolationError(
                f"Truncated Thrift PageHeader varint in Parquet document '{filename}'"
            )
        byte = buf[idx]
        idx += 1
        val |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return val, idx
        shift += 7
    raise ArchiveSecurityViolationError(
        f"Overflowing Thrift PageHeader varint in Parquet document '{filename}'"
    )


def _skip_thrift_compact_value(
    buf: bytes,
    idx: int,
    limit: int,
    wire_type: int,
    filename: str,
    *,
    in_collection: bool = False,
    depth: int = 0,
) -> int:
    """Advance past a single Thrift CompactProtocol value with bounded recursion and counts."""
    if depth > 8:
        raise ArchiveSecurityViolationError(
            f"Parquet PageHeader exceeds maximum Thrift nesting depth in '{filename}'"
        )
    if wire_type in (1, 2):
        if in_collection:
            if idx + 1 > limit:
                raise ArchiveSecurityViolationError(
                    f"Truncated Thrift boolean list in Parquet '{filename}'"
                )
            return idx + 1
        return idx
    if wire_type == 3:
        if idx + 1 > limit:
            raise ArchiveSecurityViolationError(
                f"Truncated Thrift byte field in Parquet '{filename}'"
            )
        return idx + 1
    if wire_type in (4, 5, 6):
        _, idx = _read_thrift_uvarint(buf, idx, limit, filename)
        return idx
    if wire_type == 7:
        if idx + 8 > limit:
            raise ArchiveSecurityViolationError(
                f"Truncated Thrift double field in Parquet '{filename}'"
            )
        return idx + 8
    if wire_type == 8:
        length, idx = _read_thrift_uvarint(buf, idx, limit, filename)
        if length < 0 or idx + length > limit:
            raise ArchiveSecurityViolationError(
                f"Out-of-bounds Thrift binary field in Parquet '{filename}'"
            )
        return idx + length
    if wire_type in (9, 10):
        if idx >= limit:
            raise ArchiveSecurityViolationError(
                f"Truncated Thrift collection header in Parquet '{filename}'"
            )
        hb = buf[idx]
        idx += 1
        cnt = (hb >> 4) & 0x0F
        elem_type = hb & 0x0F
        if cnt == 15:
            cnt, idx = _read_thrift_uvarint(buf, idx, limit, filename)
        if cnt > 65_536:
            raise ArchiveSecurityViolationError(
                f"Excessive Thrift PageHeader collection length ({cnt}) in '{filename}'"
            )
        for _ in range(cnt):
            idx = _skip_thrift_compact_value(
                buf, idx, limit, elem_type, filename, in_collection=True, depth=depth + 1
            )
        return idx
    if wire_type == 11:
        cnt, idx = _read_thrift_uvarint(buf, idx, limit, filename)
        if cnt == 0:
            return idx
        if cnt > 65_536 or idx >= limit:
            raise ArchiveSecurityViolationError(
                f"Malformed Thrift PageHeader map ({cnt}) in '{filename}'"
            )
        kv = buf[idx]
        idx += 1
        kt = (kv >> 4) & 0x0F
        vt = kv & 0x0F
        for _ in range(cnt):
            idx = _skip_thrift_compact_value(
                buf, idx, limit, kt, filename, in_collection=True, depth=depth + 1
            )
            idx = _skip_thrift_compact_value(
                buf, idx, limit, vt, filename, in_collection=True, depth=depth + 1
            )
        return idx
    if wire_type == 12:
        last_fid = 0
        fields_seen = 0
        while True:
            if idx >= limit:
                raise ArchiveSecurityViolationError(
                    f"Unterminated Thrift PageHeader struct in '{filename}'"
                )
            fb = buf[idx]
            idx += 1
            if fb == 0:
                return idx
            fields_seen += 1
            if fields_seen > 256:
                raise ArchiveSecurityViolationError(
                    f"Excessive Thrift PageHeader fields in '{filename}'"
                )
            delta = (fb >> 4) & 0x0F
            wt = fb & 0x0F
            if delta == 0:
                zig_fid, idx = _read_thrift_uvarint(buf, idx, limit, filename)
                last_fid = (zig_fid >> 1) ^ -(zig_fid & 1)
            else:
                last_fid += delta
            idx = _skip_thrift_compact_value(buf, idx, limit, wt, filename, depth=depth + 1)
    if wire_type == 13:
        if idx + 16 > limit:
            raise ArchiveSecurityViolationError(
                f"Truncated Thrift UUID field in Parquet '{filename}'"
            )
        return idx + 16
    raise ArchiveSecurityViolationError(
        f"Unknown Thrift Compact wire type {wire_type} in Parquet PageHeader of '{filename}'"
    )


def _parse_parquet_page_header(
    buf: bytes, start_idx: int, chunk_end: int, filename: str
) -> tuple[int, int, int, int]:
    """Decode `(page_type, uncompressed_page_size, compressed_page_size, hdr_end)`."""
    hdr_limit = min(chunk_end, start_idx + 65_536)
    idx = start_idx
    last_fid = 0
    page_type: int | None = None
    uncomp_sz: int | None = None
    comp_sz: int | None = None
    fields_seen = 0

    while True:
        if idx >= hdr_limit:
            raise ArchiveSecurityViolationError(
                f"Malformed or oversized Parquet PageHeader at offset {start_idx} in '{filename}'"
            )
        fb = buf[idx]
        idx += 1
        if fb == 0:
            break
        fields_seen += 1
        if fields_seen > 64:
            raise ArchiveSecurityViolationError(
                f"Excessive fields in Parquet PageHeader at offset {start_idx} in '{filename}'"
            )
        delta = (fb >> 4) & 0x0F
        wt = fb & 0x0F
        if delta == 0:
            zig_fid, idx = _read_thrift_uvarint(buf, idx, hdr_limit, filename)
            fid = (zig_fid >> 1) ^ -(zig_fid & 1)
        else:
            fid = last_fid + delta
        last_fid = fid

        if fid in (1, 2, 3) and wt == 5:
            zig_val, idx = _read_thrift_uvarint(buf, idx, hdr_limit, filename)
            val = (zig_val >> 1) ^ -(zig_val & 1)
            if fid == 1:
                page_type = val
            elif fid == 2:
                uncomp_sz = val
            elif fid == 3:
                comp_sz = val
        else:
            idx = _skip_thrift_compact_value(buf, idx, hdr_limit, wt, filename, depth=1)

    if page_type is None or uncomp_sz is None or comp_sz is None:
        raise ArchiveSecurityViolationError(
            f"Incomplete Parquet PageHeader at offset {start_idx} in '{filename}'"
        )
    return page_type, uncomp_sz, comp_sz, idx


def _verify_parquet_column_page_headers(
    data: bytes,
    col_meta: Any,
    *,
    rg_idx: int,
    col_idx: int,
    rg_rows: int,
    col_uncomp: int,
    col_comp: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
    total_pages_seen: int,
    filename: str,
) -> tuple[int, int]:
    """Walk all Thrift PageHeaders of a ColumnChunk before Arrow decompresses any page."""
    ext_file_path = getattr(col_meta, "file_path", None)
    if ext_file_path:
        raise ArchiveSecurityViolationError(
            f"Parquet document '{filename}' references forbidden external column file_path "
            f"'{ext_file_path}' in row group {rg_idx} column {col_idx}"
        )
    if getattr(col_meta, "crypto_metadata", None) is not None:
        raise ArchiveSecurityViolationError(
            f"Encrypted Parquet column chunks are unsupported in '{filename}' "
            f"(row group {rg_idx} column {col_idx})"
        )
    if rg_rows == 0 or col_comp == 0:
        return 0, total_pages_seen

    has_dict = bool(getattr(col_meta, "has_dictionary_page", False))
    dict_off = getattr(col_meta, "dictionary_page_offset", None)
    data_off = getattr(col_meta, "data_page_offset", None)
    use_dict_off = (
        dict_off is not None
        and int(dict_off) >= 4
        and (has_dict or data_off is None or int(dict_off) < int(data_off))
    )
    start_off = int(dict_off) if use_dict_off else int(data_off or 0)
    end_off = start_off + col_comp
    if start_off < 4 or end_off > len(data) - 4:
        raise ArchiveSecurityViolationError(
            f"Parquet document '{filename}' has out-of-bounds ColumnChunk offsets "
            f"[{start_off}, {end_off}) in row group {rg_idx} column {col_idx}"
        )

    pos = start_off
    sum_page_uncomp_only = 0
    sum_page_with_hdr = 0
    first_decode_uncomp = 0
    seen_first_data_page = False

    while pos < end_off:
        total_pages_seen += 1
        if total_pages_seen > MAX_PARQUET_PAGES_DEFAULT:
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' exceeds maximum page header count "
                f"({MAX_PARQUET_PAGES_DEFAULT})"
            )
        page_type, uncomp_sz, comp_sz, hdr_end = _parse_parquet_page_header(
            data, pos, end_off, filename
        )
        if uncomp_sz < 0 or comp_sz < 0 or hdr_end + comp_sz > end_off:
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' contains invalid PageHeader sizes "
                f"(uncompressed={uncomp_sz}, compressed={comp_sz}) in row group {rg_idx} "
                f"column {col_idx}"
            )
        if uncomp_sz > max_uncompressed_bytes:
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' PageHeader uncompressed_page_size ({uncomp_sz}) "
                f"exceeds maximum allowed limit ({max_uncompressed_bytes} bytes)"
            )
        page_ratio = uncomp_sz / max(1, comp_sz)
        if (
            uncomp_sz > _PARQUET_COL_RATIO_MIN_UNCOMPRESSED_BYTES
            and page_ratio > max_compression_ratio
        ):
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' page in row group {rg_idx} column {col_idx} "
                f"exhibits extreme compression ratio ({page_ratio:.1f}:1 > "
                f"{max_compression_ratio:.1f}:1)"
            )
        if not seen_first_data_page:
            if page_type == 2:
                first_decode_uncomp += uncomp_sz
            elif page_type in (0, 3):
                first_decode_uncomp += uncomp_sz
                seen_first_data_page = True

        sum_page_uncomp_only += uncomp_sz
        sum_page_with_hdr += (hdr_end - pos) + uncomp_sz
        pos = hdr_end + comp_sz

    if (
        pos != end_off
        or sum_page_uncomp_only > col_uncomp
        or (col_uncomp > 0 and sum_page_with_hdr < col_uncomp // 2)
    ):
        raise ArchiveSecurityViolationError(
            f"Parquet document '{filename}' has forged PageHeader vs footer uncompressed size "
            f"(pages={sum_page_uncomp_only}, with_headers={sum_page_with_hdr}, "
            f"footer={col_uncomp}) in row group {rg_idx} column {col_idx}"
        )

    return first_decode_uncomp, total_pages_seen


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
        max_preview_columns: int = MAX_PARQUET_PREVIEW_COLUMNS_DEFAULT,
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
            max_preview_columns: Maximum columns projected during preview batch extraction.
            max_preview_cell_chars: Maximum characters materialized per preview cell.
            max_preview_bytes: Maximum total UTF-8 bytes materialized across preview cells.
        """
        self.preview_rows = preview_rows
        self.max_input_bytes = max_input_bytes
        self.max_metadata_rows = max_metadata_rows
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio
        self.max_columns = max_columns
        self.max_preview_columns = max(1, int(max_preview_columns))
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
        taint = _resolve_taint(kwargs.get("taint", TaintClassification.INTERNAL), filename)

        num_rows = parquet_meta.num_rows
        num_columns = parquet_meta.num_columns
        num_row_groups = parquet_meta.num_row_groups

        if num_rows < 0 or num_rows > self.max_metadata_rows:
            raise DocumentPayloadTooLargeError(
                f"Parquet document '{filename}' declares {num_rows} rows, exceeding maximum "
                f"allowed row count ({self.max_metadata_rows})"
            )
        if num_columns < 0 or num_columns > self.max_columns:
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' declares {num_columns} columns, exceeding maximum "
                f"allowed column count ({self.max_columns})"
            )

        schema_names = list(schema_arrow.names)
        preview_columns_truncated = len(schema_names) > self.max_preview_columns
        preview_col_names = schema_names[: self.max_preview_columns]
        preview_col_set = set(preview_col_names)
        max_preview_memory_bytes = min(
            self.max_uncompressed_bytes, MAX_PARQUET_PREVIEW_MEMORY_BYTES_CAP
        )

        total_declared_bytes = 0
        total_col_uncompressed_bytes = 0
        total_col_compressed_bytes = 0
        total_pages_seen = 0
        rg0_preview_first_decode: dict[str, int] = {}

        for rg_idx in range(num_row_groups):
            rg_meta = parquet_meta.row_group(rg_idx)
            raw_rg_bytes = getattr(rg_meta, "total_byte_size", 0)
            rg_bytes = int(raw_rg_bytes) if raw_rg_bytes is not None else 0
            raw_rg_rows = getattr(rg_meta, "num_rows", 0)
            rg_rows = int(raw_rg_rows) if raw_rg_rows is not None else 0
            if rg_bytes < 0 or rg_rows < 0:
                raise ArchiveSecurityViolationError(
                    f"Parquet document '{filename}' declares negative size or row count in "
                    f"row group {rg_idx}"
                )

            rg_col_uncompressed = 0
            rg_col_compressed = 0
            rg_num_cols = int(getattr(rg_meta, "num_columns", 0) or 0)
            col_metas: list[tuple[Any, int, int]] = []
            for col_idx in range(rg_num_cols):
                col_meta = rg_meta.column(col_idx)
                raw_col_uncomp = getattr(col_meta, "total_uncompressed_size", 0)
                raw_col_comp = getattr(col_meta, "total_compressed_size", 0)
                col_uncomp = int(raw_col_uncomp) if raw_col_uncomp is not None else 0
                col_comp = int(raw_col_comp) if raw_col_comp is not None else 0
                if col_uncomp < 0 or col_comp < 0:
                    raise ArchiveSecurityViolationError(
                        f"Parquet document '{filename}' declares negative column-chunk size in "
                        f"row group {rg_idx} column {col_idx}"
                    )
                col_metas.append((col_meta, col_uncomp, col_comp))
                rg_col_uncompressed += col_uncomp
                rg_col_compressed += col_comp

            if (
                rg_rows > 0
                and rg_col_uncompressed > 0
                and (rg_bytes <= 0 or rg_bytes < rg_col_uncompressed // 2)
            ):
                raise ArchiveSecurityViolationError(
                    f"Parquet document '{filename}' has inconsistent or forged footer in "
                    f"row group {rg_idx} (total_byte_size={rg_bytes} vs column uncompressed "
                    f"sum={rg_col_uncompressed})"
                )

            for col_idx, (col_meta, col_uncomp, col_comp) in enumerate(col_metas):
                if col_uncomp > self.max_uncompressed_bytes:
                    max_unc = self.max_uncompressed_bytes
                    raise DocumentPayloadTooLargeError(
                        f"Parquet document '{filename}' uncompressed row-group size exceeds "
                        f"maximum allowed limit ({col_uncomp} > {max_unc} bytes)"
                    )
                col_ratio = col_uncomp / max(1, col_comp)
                if (
                    col_uncomp > _PARQUET_COL_RATIO_MIN_UNCOMPRESSED_BYTES
                    and col_ratio > self.max_compression_ratio
                ):
                    raise ArchiveSecurityViolationError(
                        f"Parquet document '{filename}' column {col_idx} in row group {rg_idx} "
                        f"exhibits extreme compression ratio "
                        f"({col_ratio:.1f}:1 > {self.max_compression_ratio:.1f}:1)"
                    )
                first_decode_uncomp, total_pages_seen = _verify_parquet_column_page_headers(
                    data,
                    col_meta,
                    rg_idx=rg_idx,
                    col_idx=col_idx,
                    rg_rows=rg_rows,
                    col_uncomp=col_uncomp,
                    col_comp=col_comp,
                    max_uncompressed_bytes=self.max_uncompressed_bytes,
                    max_compression_ratio=self.max_compression_ratio,
                    total_pages_seen=total_pages_seen,
                    filename=filename,
                )
                if rg_idx == 0:
                    col_path = str(getattr(col_meta, "path_in_schema", "") or "")
                    root_col = (
                        col_path if col_path in preview_col_set else col_path.split(".", 1)[0]
                    )
                    if root_col in preview_col_set:
                        rg0_preview_first_decode[root_col] = (
                            rg0_preview_first_decode.get(root_col, 0) + first_decode_uncomp
                        )

            total_declared_bytes += rg_bytes
            total_col_uncompressed_bytes += rg_col_uncompressed
            total_col_compressed_bytes += rg_col_compressed
            effective_rg_sum = max(total_declared_bytes, total_col_uncompressed_bytes)
            if effective_rg_sum > self.max_uncompressed_bytes:
                max_unc = self.max_uncompressed_bytes
                raise DocumentPayloadTooLargeError(
                    f"Parquet document '{filename}' uncompressed row-group size exceeds "
                    f"maximum allowed limit ({effective_rg_sum} > {max_unc} bytes)"
                )

        effective_uncompressed_bytes = max(total_declared_bytes, total_col_uncompressed_bytes)
        ratio_floor = min(16 * 1024 * 1024, max(65_536, self.max_uncompressed_bytes // 4))
        ratio_vs_file = effective_uncompressed_bytes / max(len(data), 1)
        ratio_vs_col = (
            effective_uncompressed_bytes / max(1, total_col_compressed_bytes)
            if total_col_compressed_bytes > 0
            else ratio_vs_file
        )
        compression_ratio = max(ratio_vs_file, ratio_vs_col)
        if (
            effective_uncompressed_bytes > ratio_floor
            and compression_ratio > self.max_compression_ratio
        ):
            raise ArchiveSecurityViolationError(
                f"Parquet document '{filename}' exhibits extreme compression ratio "
                f"({compression_ratio:.1f}:1 > {self.max_compression_ratio:.1f}:1)"
            )

        # Schema and column metadata (up to max_columns)
        columns_meta: list[dict[str, Any]] = []
        column_types_map: dict[str, str] = {}
        for field in schema_arrow:
            type_str = str(field.type)
            columns_meta.append(
                {
                    "name": field.name,
                    "type": type_str,
                    "nullable": field.nullable,
                }
            )
            column_types_map[field.name] = type_str

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

        # Pre-verified single-column preview: check PageHeader decode budget BEFORE Arrow allocates,
        # then decode 1 column at a time (`use_threads=False`) and drop each Arrow RecordBatch slice
        # immediately so C++ `shared_ptr<arrow::Buffer>` page memory is never pinned across columns.
        preview_table_rows: list[list[str]] = []
        target_preview = max(0, min(num_rows, self.preview_rows))
        if target_preview > 0 and preview_col_names:
            for col_name in preview_col_names:
                est_first_decode = rg0_preview_first_decode.get(col_name, 0)
                if est_first_decode > max_preview_memory_bytes:
                    raise ArchiveSecurityViolationError(
                        f"Parquet document '{filename}' preview column '{col_name}' first-page "
                        f"uncompressed size ({est_first_decode}) exceeds preview memory cap "
                        f"({max_preview_memory_bytes} bytes)"
                    )

            base_pool = pa.default_memory_pool() if hasattr(pa, "default_memory_pool") else None
            baseline_alloc = base_pool.bytes_allocated() if base_pool is not None else 0
            per_col_cells: list[list[str]] = []
            rows_in_preview = 0
            try:
                for col_idx_p, col_name in enumerate(preview_col_names):
                    batch_iter = parquet_file.iter_batches(
                        batch_size=max(1, target_preview),
                        columns=[col_name],
                        use_threads=False,
                    )
                    first_batch = next(batch_iter, None)
                    del batch_iter
                    if first_batch is None:
                        break
                    batch_nbytes = int(getattr(first_batch, "nbytes", 0) or 0)
                    current_pool_delta = (
                        max(0, base_pool.bytes_allocated() - baseline_alloc)
                        if base_pool is not None
                        else 0
                    )
                    observed_mem = max(batch_nbytes, current_pool_delta)
                    if observed_mem > max_preview_memory_bytes:
                        del first_batch
                        raise ArchiveSecurityViolationError(
                            f"Parquet document '{filename}' preview exceeded memory cap "
                            f"({observed_mem} > {max_preview_memory_bytes} bytes)"
                        )
                    sliced_batch = first_batch[:target_preview]
                    col_len = min(target_preview, len(sliced_batch))
                    rows_in_preview = col_len if col_idx_p == 0 else min(rows_in_preview, col_len)
                    col_arr = sliced_batch.column(0)
                    col_vals: list[str] = []
                    for r_idx in range(rows_in_preview):
                        py_val = col_arr[r_idx].as_py()
                        raw_cell = "None" if py_val is None else str(py_val)
                        if len(raw_cell) > self.max_preview_cell_chars:
                            raw_cell = raw_cell[: self.max_preview_cell_chars] + "…[TRUNCATED]"
                        col_vals.append(raw_cell)
                    per_col_cells.append(col_vals)
                    del col_arr, sliced_batch, first_batch

                if per_col_cells and len(per_col_cells) == len(preview_col_names):
                    materialized_preview_bytes = 0
                    budget_exceeded = False
                    for r_idx in range(rows_in_preview):
                        row_vals = []
                        for col_vals in per_col_cells:
                            cell_str = col_vals[r_idx]
                            materialized_preview_bytes += len(
                                cell_str.encode("utf-8", errors="replace")
                            )
                            if materialized_preview_bytes > self.max_preview_bytes:
                                budget_exceeded = True
                                break
                            row_vals.append(cell_str)
                        if budget_exceeded:
                            break
                        preview_table_rows.append(row_vals)
            except ArchiveSecurityViolationError:
                raise
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
            lines.append("| " + " | ".join(preview_col_names) + " |")
            lines.append("| " + " | ".join(["---"] * len(preview_col_names)) + " |")
            for r in preview_table_rows:
                escaped = [c.replace("|", "\\|").replace("\n", " ") for c in r]
                lines.append("| " + " | ".join(escaped) + " |")

        normalized_text = normalize_text("\n".join(lines))

        table_structure = TableStructure(
            table_id="tbl_parquet_1",
            page_number=1,
            headers=preview_col_names,
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
                "column_names": schema_names,
                "column_types": column_types_map,
                "columns": columns_meta,
                "preview_columns_truncated": preview_columns_truncated,
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
    """Scan a YAML line for unquoted flow brackets while tracking scalar-start quote state."""
    escaped = False
    comment_pos: int | None = None
    idx = 0
    line_len = len(line)
    while idx < line_len:
        ch = line[idx]
        if in_double:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            idx += 1
            continue
        if in_single:
            if ch == "'":
                if idx + 1 < line_len and line[idx + 1] == "'":
                    idx += 2
                    continue
                in_single = False
            idx += 1
            continue

        can_open_quote = idx == 0 or line[idx - 1].isspace() or line[idx - 1] in ":[{,?-"
        if ch == '"' and can_open_quote:
            in_double = True
        elif ch == "'" and can_open_quote:
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
        idx += 1

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
        max_table_columns: int = MAX_TABLE_COLUMNS_DEFAULT,
        max_table_cells: int = MAX_TABLE_CELLS_DEFAULT,
    ) -> None:
        """Initialize StructuredDataDocumentAdapter.

        Args:
            max_depth: Maximum allowable nesting depth to prevent stack overflow on deep payloads.
            max_nodes: Maximum allowable visited DAG nodes across expanded structures.
            max_aliases: Maximum allowable YAML alias expansions.
            max_bytes: Maximum allowed raw byte size.
            max_table_columns: Maximum columns synthesized in TableStructure.
            max_table_cells: Maximum total cells materialized in TableStructure.
        """
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_aliases = max_aliases
        self.max_bytes = max_bytes
        self.max_table_columns = max(1, int(max_table_columns))
        self.max_table_cells = max(1, int(max_table_cells))

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
        taint = _resolve_taint(kwargs.get("taint", TaintClassification.INTERNAL), filename)

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

        headers_truncated = False
        if candidate_list:
            headers: list[str] = []
            seen_headers: set[str] = set()
            for item in candidate_list[:100]:
                if isinstance(item, dict):
                    for k in item:
                        k_str = str(k)
                        if k_str not in seen_headers:
                            if len(headers) >= self.max_table_columns:
                                headers_truncated = True
                                break
                            seen_headers.add(k_str)
                            headers.append(k_str)
                if headers_truncated:
                    break
            if headers:
                effective_max_rows = min(500, max(1, self.max_table_cells // max(1, len(headers))))
                rows: list[list[str]] = []
                for item in candidate_list[:effective_max_rows]:
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
                "headers_truncated": headers_truncated,
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


def _reject_xml_dtd_and_entities(raw_text: str, filename: str) -> None:
    """Screen XML payloads with Expat to forbid DTD/ENTITY expansion regardless of system expat."""
    parser = expat.ParserCreate()
    with contextlib.suppress(AttributeError, expat.ExpatError):
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def _on_doctype(
        _doctype_name: str,
        sysid: str | None,
        pubid: str | None,
        has_internal_subset: int | bool,
    ) -> None:
        if sysid or pubid or bool(has_internal_subset):
            raise ArchiveSecurityViolationError(
                f"XML document '{filename}' contains forbidden DTD declaration"
            )

    def _on_entity(*_args: Any) -> None:
        raise ArchiveSecurityViolationError(
            f"XML document '{filename}' contains forbidden ENTITY declaration"
        )

    parser.StartDoctypeDeclHandler = _on_doctype
    parser.EntityDeclHandler = _on_entity
    try:
        parser.Parse(raw_text.encode("utf-8"), True)
    except ArchiveSecurityViolationError:
        raise
    except expat.ExpatError:
        # Malformed XML without DTD/ENTITY is handled by ET.fromstring / lenient fallback below
        return


class _HTMLReadabilityParser(HTMLParser):
    """Lenient HTML parser stripping scripts, styles, navigation, and extracting text/tables."""

    HARD_IGNORED_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe"})
    LAYOUT_IGNORED_TAGS = frozenset({"nav", "header", "footer", "aside"})
    IGNORED_TAGS = HARD_IGNORED_TAGS | LAYOUT_IGNORED_TAGS

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

    def __init__(
        self,
        *,
        suppress_layout_tags: bool = True,
        max_table_columns: int = MAX_TABLE_COLUMNS_DEFAULT,
        max_table_cells: int = MAX_TABLE_CELLS_DEFAULT,
        max_cell_chars: int = MAX_CELL_CHARS_DEFAULT,
    ) -> None:
        super().__init__()
        self._ignored_tags = self.IGNORED_TAGS if suppress_layout_tags else self.HARD_IGNORED_TAGS
        self.max_table_columns = max(1, int(max_table_columns))
        self.max_table_cells = max(1, int(max_table_cells))
        self.max_cell_chars = max(1, int(max_cell_chars))
        self.ignore_depth = 0
        self.text_parts: list[str] = []
        self.title: str = ""
        self.in_title = False
        self.tables: list[list[list[str]]] = []
        self._total_table_cells = 0
        self._table_stack: list[tuple[list[list[str]], list[str] | None, list[str] | None]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low_tag = tag.lower()
        if low_tag in self._ignored_tags:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return

        if low_tag == "title":
            self.in_title = True
        elif low_tag == "table":
            if self._current_table is not None:
                self._table_stack.append(
                    (self._current_table, self._current_row, self._current_cell)
                )
            self._current_table = []
            self._current_row = None
            self._current_cell = None
        elif low_tag == "tr" and self._current_table is not None:
            self._current_row = []
            self._current_cell = None
        elif low_tag in ("td", "th") and self._current_row is not None:
            self._current_cell = []
        elif low_tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        low_tag = tag.lower()
        if low_tag in self._ignored_tags:
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if self.ignore_depth > 0:
            return

        if low_tag == "title":
            self.in_title = False
        elif low_tag in ("td", "th") and self._current_cell is not None:
            cell_text = "".join(self._current_cell).strip()
            if len(cell_text) > self.max_cell_chars:
                cell_text = cell_text[: self.max_cell_chars] + "…[TRUNCATED]"
            if (
                self._current_row is not None
                and len(self._current_row) < self.max_table_columns
                and self._total_table_cells < self.max_table_cells
            ):
                self._current_row.append(cell_text)
                self._total_table_cells += 1
            self._current_cell = None
        elif low_tag == "tr" and self._current_row is not None:
            if self._current_table is not None and self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif low_tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            if self._table_stack:
                self._current_table, self._current_row, self._current_cell = self._table_stack.pop()
            else:
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

    def __init__(
        self,
        *,
        max_bytes: int | None = MAX_TEXT_DOCUMENT_BYTES_DEFAULT,
        max_table_columns: int = MAX_TABLE_COLUMNS_DEFAULT,
        max_table_cells: int = MAX_TABLE_CELLS_DEFAULT,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_table_columns = max(1, int(max_table_columns))
        self.max_table_cells = max(1, int(max_table_cells))

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
        taint = _resolve_taint(kwargs.get("taint", TaintClassification.INTERNAL), filename)

        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        is_xml = ext == "xml" or mime_type in ("application/xml", "text/xml")

        extracted_tables: list[TableStructure] = []
        title: str = ""
        unclosed_layout_recovered = False

        if is_xml:
            _reject_xml_dtd_and_entities(raw_text, filename)
            try:
                root = ET.fromstring(raw_text)
                title = root.tag
                xml_texts = [seg.strip() for seg in root.itertext() if seg and seg.strip()]
                clean_text = "\n\n".join(xml_texts) if xml_texts else raw_text
                resolved_mime = mime_type or "application/xml"
            except (RecursionError, MemoryError) as exc:
                raise InvalidDocumentError(
                    f"XML document '{filename}' exceeded parser resource limits: {exc}"
                ) from exc
            except ET.ParseError:
                # Lenient fallback to HTML parser if XML is slightly malformed
                parser = _HTMLReadabilityParser(
                    max_table_columns=self.max_table_columns,
                    max_table_cells=self.max_table_cells,
                )
                parser.feed(raw_text)
                clean_text = "".join(parser.text_parts)
                title = parser.title.strip()
                resolved_mime = mime_type or "application/xml"
        else:
            parser = _HTMLReadabilityParser(
                max_table_columns=self.max_table_columns,
                max_table_cells=self.max_table_cells,
            )
            parser.feed(raw_text)
            if parser.ignore_depth > 0:
                fallback_parser = _HTMLReadabilityParser(
                    suppress_layout_tags=False,
                    max_table_columns=self.max_table_columns,
                    max_table_cells=self.max_table_cells,
                )
                fallback_parser.feed(raw_text)
                if fallback_parser.ignore_depth == 0:
                    parser = fallback_parser
                    unclosed_layout_recovered = True

            clean_text = "".join(parser.text_parts)
            title = parser.title.strip()

            # Process HTML tables with rectangular header-width padding
            for idx, raw_tbl in enumerate(parser.tables, start=1):
                if not raw_tbl:
                    continue
                headers = raw_tbl[0][: self.max_table_columns]
                if not headers:
                    continue
                width = len(headers)
                rows = [r[:width] + [""] * max(0, width - len(r)) for r in raw_tbl[1:]]
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
        if unclosed_layout_recovered:
            metadata["unclosed_ignored_tag_recovered"] = True

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
