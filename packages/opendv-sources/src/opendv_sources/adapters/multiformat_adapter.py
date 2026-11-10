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


def decode_text_payload(
    data: bytes,
    filename: str,
    mime_type: str | None = None,
    adapter_name: str = "text",
) -> tuple[str, str]:
    """Decode raw document bytes into normalized Unicode text with BOM and binary detection.

    Args:
        data: Raw file bytes.
        filename: Original filename.
        mime_type: Optional MIME media type.
        adapter_name: Adapter identifier for error messages.

    Returns:
        Tuple of (normalized_text, encoding_name).

    Raises:
        InvalidDocumentError: If data is empty.
        UnsupportedFormatError: If binary data is detected without text encoding.
    """
    if not data:
        raise InvalidDocumentError(f"Document '{filename}' is empty (0 bytes)")

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

    def __init__(self, *, exclude_code_blocks: bool = False) -> None:
        """Initialize MarkdownDocumentAdapter.

        Args:
            exclude_code_blocks: If True, fenced code blocks are excluded from normalized_text
                and quarantined into metadata only.
        """
        self.exclude_code_blocks = exclude_code_blocks

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
            data, filename, mime_type=mime_type, adapter_name=self.adapter_name
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        # 1. Parse fenced code blocks
        code_blocks: list[dict[str, Any]] = []
        code_block_spans: list[tuple[int, int]] = []
        lines = raw_text.split("\n")
        in_code_block = False
        code_lang = ""
        block_lines: list[str] = []
        block_start_char = 0
        current_offset = 0

        for line in lines:
            line_len = len(line) + 1  # includes newline
            stripped = line.strip()
            fence_match = re.match(r"^(```+|~~~+)(.*)$", stripped)
            if fence_match:
                if not in_code_block:
                    in_code_block = True
                    code_lang = fence_match.group(2).strip()
                    block_start_char = current_offset
                    block_lines = []
                else:
                    in_code_block = False
                    block_end_char = current_offset + len(line)
                    code_content = "\n".join(block_lines)
                    code_blocks.append(
                        {
                            "language": code_lang,
                            "code": code_content,
                            "start_char": block_start_char,
                            "end_char": block_end_char,
                        }
                    )
                    code_block_spans.append((block_start_char, block_end_char))
            elif in_code_block:
                block_lines.append(line)
            current_offset += line_len

        # If code block wasn't closed at EOF
        if in_code_block:
            code_blocks.append(
                {
                    "language": code_lang,
                    "code": "\n".join(block_lines),
                    "start_char": block_start_char,
                    "end_char": len(raw_text),
                }
            )
            code_block_spans.append((block_start_char, len(raw_text)))

        # 2. Parse heading hierarchy (ignoring lines inside code blocks)
        headings: list[dict[str, Any]] = []
        offset = 0
        for line in lines:
            line_start = offset
            line_end = offset + len(line)
            offset += len(line) + 1
            # Check if this line is inside a code block
            inside_code = any(s <= line_start and line_end <= e for s, e in code_block_spans)
            if inside_code:
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                headings.append(
                    {
                        "level": level,
                        "title": title,
                        "start_char": line_start,
                        "end_char": line_end,
                    }
                )

        # 3. Optional code block exclusion from normalized text
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

        # 4. Table extraction using liteparse helper
        detected_tables = extract_tables_with_spans(effective_text, page_number=1)
        tables = [d.table for d in detected_tables]

        # 5. Build page map entry and chunks
        page_entry = _build_page_map_entry(
            effective_text, page_number=1, has_tables=bool(tables), confidence=1.0
        )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "encoding": encoding,
                "headings": headings,
                "heading_count": len(headings),
                "code_blocks": code_blocks,
                "code_block_count": len(code_blocks),
                "quarantined_code_blocks": len(code_blocks),
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
    ) -> None:
        """Initialize DelimitedTabularAdapter.

        Args:
            max_cell_chars: Maximum character length per cell before truncation guard activates.
            max_rows: Optional row count limit for preview or bounding ingestion memory.
        """
        self.max_cell_chars = max_cell_chars
        self.max_rows = max_rows

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
            data, filename, mime_type=mime_type, adapter_name=self.adapter_name
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

        # Safeguard csv field size limit
        with contextlib.suppress(OverflowError, OSError):
            csv.field_size_limit(10 * 1024 * 1024)

        # Streaming row parsing with cell truncation guard
        reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        raw_rows: list[list[str]] = []
        truncated_cells_count = 0

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
    supported_extensions: Sequence[str] = (
        ".parquet",
    )

    def __init__(self, *, preview_rows: int = 50) -> None:
        """Initialize PyArrowTabularAdapter.

        Args:
            preview_rows: Number of initial rows to include in the preview TableStructure.
        """
        self.preview_rows = preview_rows

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

        caller_sha256 = kwargs.pop("source_sha256", None)
        actual_sha256 = _verify_source_sha256(data, filename, caller_sha256)

        if not data:
            raise InvalidDocumentError(f"Parquet document '{filename}' is empty (0 bytes)")

        # Validate standard Parquet magic bytes: 'PAR1' at offset 0 and at last 4 bytes
        if len(data) < 8 or not (data.startswith(b"PAR1") and data.endswith(b"PAR1")):
            raise InvalidDocumentError(
                f"Invalid Parquet document '{filename}': missing 'PAR1' magic header/footer bytes"
            )

        try:
            parquet_file = pq.ParquetFile(io.BytesIO(data))
            parquet_meta = parquet_file.metadata
            schema_arrow = parquet_file.schema_arrow
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

        # Key-value custom metadata
        custom_metadata: dict[str, str] = {}
        if parquet_meta.metadata:
            for k, v in parquet_meta.metadata.items():
                try:
                    custom_metadata[k.decode("utf-8")] = v.decode("utf-8")
                except UnicodeDecodeError:
                    custom_metadata[str(k)] = str(v)

        # Read preview rows
        preview_table_rows: list[list[str]] = []
        headers = schema_arrow.names
        if num_rows > 0:
            try:
                if num_row_groups > 0:
                    preview_batch = parquet_file.read_row_group(0)
                else:
                    preview_batch = parquet_file.read()
                preview_pydict = preview_batch.to_pydict()
                first_col = next(iter(preview_pydict.values()))
                total_preview = min(num_rows, self.preview_rows, len(first_col))
                for r_idx in range(total_preview):
                    row_vals = [str(preview_pydict[col][r_idx]) for col in headers]
                    preview_table_rows.append(row_vals)
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


def _check_depth_and_flatten(
    obj: Any,
    prefix: str = "",
    current_depth: int = 1,
    max_depth: int = MAX_RECURSION_DEPTH_DEFAULT,
    paths: list[str] | None = None,
    field_names: set[str] | None = None,
) -> tuple[int, list[str], set[str]]:
    """Validate recursion depth limit and flatten JSON paths."""
    if paths is None:
        paths = []
    if field_names is None:
        field_names = set()

    if current_depth > max_depth:
        raise DocumentExtractionError(
            f"Structured payload exceeds maximum recursion depth of {max_depth}"
        )

    max_found_depth = current_depth

    if isinstance(obj, dict):
        for key, value in obj.items():
            field_name = str(key)
            field_names.add(field_name)
            child_prefix = f"{prefix}.{field_name}" if prefix else field_name
            paths.append(child_prefix)
            child_depth, _, _ = _check_depth_and_flatten(
                value,
                prefix=child_prefix,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                paths=paths,
                field_names=field_names,
            )
            max_found_depth = max(max_found_depth, child_depth)
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            child_prefix = f"{prefix}[{idx}]"
            paths.append(child_prefix)
            child_depth, _, _ = _check_depth_and_flatten(
                item,
                prefix=child_prefix,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                paths=paths,
                field_names=field_names,
            )
            max_found_depth = max(max_found_depth, child_depth)

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

    def __init__(self, *, max_depth: int = MAX_RECURSION_DEPTH_DEFAULT) -> None:
        """Initialize StructuredDataDocumentAdapter.

        Args:
            max_depth: Maximum allowable nesting depth to prevent stack overflow on deep payloads.
        """
        self.max_depth = max_depth

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
            data, filename, mime_type=mime_type, adapter_name=self.adapter_name
        )

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        jsonlines_mimes = ("application/x-jsonlines", "application/jsonlines")
        is_jsonl = ext == "jsonl" or mime_type in jsonlines_mimes
        yaml_mimes = ("application/x-yaml", "text/yaml", "application/yaml")
        is_yaml = ext in ("yaml", "yml") or mime_type in yaml_mimes

        # Parse data structure
        if is_jsonl:
            parsed_data: list[Any] = []
            for line_no, line in enumerate(raw_text.splitlines(), start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    parsed_data.append(json.loads(clean_line))
                except json.JSONDecodeError as err:
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
            try:
                payload = yaml.safe_load(raw_text)
            except yaml.YAMLError as err:
                raise InvalidDocumentError(
                    f"Invalid YAML payload in '{filename}': {err}"
                ) from err
            resolved_mime = mime_type or "application/x-yaml"
        else:
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as err:
                raise InvalidDocumentError(
                    f"Invalid JSON payload in '{filename}': {err}"
                ) from err
            resolved_mime = mime_type or "application/json"

        # Check recursion depth and unnest jsonpaths
        observed_depth, json_paths, discovered_fields = _check_depth_and_flatten(
            payload, max_depth=self.max_depth
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
                for k in item:
                    if str(k) not in headers:
                        headers.append(str(k))
            if headers:
                rows: list[list[str]] = []
                for item in candidate_list[:500]:
                    row = [str(item.get(h, "")) for h in headers]
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
            normalized_text = normalize_text(json.dumps(payload, indent=2, ensure_ascii=False))

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
                "json_path_count": len(json_paths),
                "json_paths_sample": json_paths[:100],
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
            data, filename, mime_type=mime_type, adapter_name=self.adapter_name
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
