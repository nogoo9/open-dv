"""LiteParse 0.4.x Native PDF Adapter with pypdf fallback (Task 4B.2 / Issue #202).

Provides LiteParseDocumentAdapter embedding Google PDFium C++ wheel via liteparse
for high-performance PDF text extraction, layout bounding box calculation, and
table structure extraction, with graceful fallback to pypdf when liteparse is absent.
Enforces Unicode NFC, Unix LF line endings, Unicode codepoint offset counting,
and browser UTF-16 translation index mapping on PageMapEntry DTOs.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pypdf
import pypdf.errors

from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TableStructure,
    TaintClassification,
)
from opendv_sources.exceptions import DocumentExtractionError, OCRUnavailableError
from opendv_sources.ingestion.adapter import BaseDocumentAdapter
from opendv_sources.ingestion.layout_evaluator import (
    DEFAULT_RASTER_RATIO_THRESHOLD,
    DEFAULT_SPARSE_TEXT_THRESHOLD,
    TesseractOCRRouter,
    evaluate_page_complexity,
)
from opendv_sources.ingestion.normalization import (
    compute_utf16_offsets_for_points,
    normalize_text,
)

logger = logging.getLogger(__name__)


# Check native liteparse availability
try:
    import liteparse  # type: ignore[import-untyped]

    HAS_LITEPARSE = True
except (ImportError, OSError):
    liteparse = None  # type: ignore[assignment]
    HAS_LITEPARSE = False

# Robust parse error tuple covering pypdf, struct, recursion, and system exceptions (MIN-02)
PARSER_ERRORS = (
    pypdf.errors.PyPdfError,
    DocumentExtractionError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    IndexError,
    ZeroDivisionError,
    RecursionError,
    OverflowError,
    MemoryError,
    struct.error,
    OSError,
    RuntimeError,
)

# Liteparse errors tuple dynamically including native liteparse exceptions (MIN-03)
LITEPARSE_BASE_ERROR = getattr(liteparse, "LiteParseError", None)
if LITEPARSE_BASE_ERROR is not None and issubclass(LITEPARSE_BASE_ERROR, Exception):
    LITEPARSE_ERRORS = (LITEPARSE_BASE_ERROR, *PARSER_ERRORS, Exception)
else:
    LITEPARSE_ERRORS = (*PARSER_ERRORS, Exception)

VALID_OCR_MODES = frozenset({"auto", "force", "off"})


class InvalidDocumentError(DocumentExtractionError):
    """Raised when a document is corrupt, malformed, or cannot be parsed."""


@dataclass(frozen=True)
class DetectedTable:
    """Internal container linking an extracted TableStructure to its source page character span."""

    table: TableStructure
    start_in_page: int
    end_in_page: int


def _split_pipe_table_row(line: str) -> list[str]:
    """Extract trimmed cell values from a pipe-delimited table row."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_divider_row(cells: list[str]) -> bool:
    """Check if all cells in a row look like markdown divider syntax (e.g. '---', ':---:')."""
    if not cells:
        return False
    return all(bool(re.match(r"^:?-+:?$", cell)) for cell in cells)


def extract_tables_with_spans(
    page_text: str,
    page_number: int,
    page_bbox: tuple[float, float, float, float] | None = None,
) -> list[DetectedTable]:
    """Detect tabular structures from normalized page text, recording source character spans.

    Args:
        page_text: Normalized text content of the page.
        page_number: 1-indexed page number.
        page_bbox: Optional parent page MediaBox (left, bottom, right, top). Reserved for
            future cell geometry mapping; text-based extraction produces None bounding_box (MIN-08).

    Supports:
    1. Markdown / pipe-delimited tables (| Col 1 | Col 2 |).
    2. Tab-delimited tables (Col 1 \\t Col 2).
    3. Whitespace-aligned tables with >= 2 columns across >= 3 lines with vertical
       alignment (MIN-07).
    """
    if not page_text or not page_text.strip():
        return []

    detected: list[DetectedTable] = []
    lines = page_text.split("\n")
    n_lines = len(lines)
    idx = 0
    table_counter = 1

    # Track character offset of each line start in page_text
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1  # accounts for '\n' separator

    while idx < n_lines:
        line = lines[idx]

        # ---------------------------------------------------------------------
        # 1. Pipe-delimited tables
        # ---------------------------------------------------------------------
        if "|" in line and line.count("|") >= 2:
            start_line_idx = idx
            pipe_block: list[str] = []
            while idx < n_lines and "|" in lines[idx] and lines[idx].count("|") >= 2:
                pipe_block.append(lines[idx])
                idx += 1

            if len(pipe_block) >= 2:
                raw_rows = [_split_pipe_table_row(row) for row in pipe_block]
                headers: list[str] = []
                data_rows: list[list[str]] = []
                has_divider = False

                for row in raw_rows:
                    if _is_table_divider_row(row):
                        has_divider = True
                        continue
                    if not headers:
                        headers = row
                    else:
                        data_rows.append(row)

                if headers and (data_rows or has_divider):
                    col_count = len(headers)
                    normalized_rows = [
                        row[:col_count] + [""] * max(0, col_count - len(row)) for row in data_rows
                    ]
                    raw_md = "\n".join(pipe_block)
                    span_start = line_starts[start_line_idx]
                    span_end = line_starts[idx - 1] + len(lines[idx - 1])
                    tbl = TableStructure(
                        table_id=f"tbl_p{page_number}_{table_counter}",
                        page_number=page_number,
                        headers=headers,
                        rows=normalized_rows,
                        raw_html_or_md=raw_md,
                        bounding_box=None,
                    )
                    detected.append(
                        DetectedTable(
                            table=tbl,
                            start_in_page=span_start,
                            end_in_page=span_end,
                        )
                    )
                    table_counter += 1
            continue

        # ---------------------------------------------------------------------
        # 2. Tab-delimited tables
        # ---------------------------------------------------------------------
        if "\t" in line and len(line.split("\t")) >= 2:
            start_line_idx = idx
            tab_block: list[str] = []
            col_target = len(line.split("\t"))
            while (
                idx < n_lines and "\t" in lines[idx] and len(lines[idx].split("\t")) == col_target
            ):
                tab_block.append(lines[idx])
                idx += 1

            if len(tab_block) >= 2:
                headers = [c.strip() for c in tab_block[0].split("\t")]
                data_rows = [[c.strip() for c in r.split("\t")] for r in tab_block[1:]]
                raw_md = (
                    "| "
                    + " | ".join(headers)
                    + " |\n"
                    + "| "
                    + " | ".join(["---"] * len(headers))
                    + " |\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in data_rows)
                )
                span_start = line_starts[start_line_idx]
                span_end = line_starts[idx - 1] + len(lines[idx - 1])
                tbl = TableStructure(
                    table_id=f"tbl_p{page_number}_{table_counter}",
                    page_number=page_number,
                    headers=headers,
                    rows=data_rows,
                    raw_html_or_md=raw_md,
                    bounding_box=None,
                )
                detected.append(
                    DetectedTable(
                        table=tbl,
                        start_in_page=span_start,
                        end_in_page=span_end,
                    )
                )
                table_counter += 1
            continue

        # ---------------------------------------------------------------------
        # 3. Space-aligned multi-column tables (>= 2 columns, >= 3 lines with vertical alignment)
        # ---------------------------------------------------------------------
        space_cells = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        if len(space_cells) >= 2 and all(len(c) <= 80 for c in space_cells):
            start_line_idx = idx
            space_block: list[str] = [line]
            col_target = len(space_cells)
            curr = idx + 1
            while curr < n_lines:
                next_line = lines[curr]
                next_cells = [
                    c.strip() for c in re.split(r"\s{2,}", next_line.strip()) if c.strip()
                ]
                if len(next_cells) == col_target and all(len(c) <= 80 for c in next_cells):
                    space_block.append(next_line)
                    curr += 1
                else:
                    break

            if len(space_block) >= 3:
                # Check column start alignment across rows to avoid misclassifying
                # justified prose with accidental double spaces (MIN-07)
                col_starts_per_line: list[list[int]] = []
                for blk_line in space_block:
                    starts = [0]
                    for m in re.finditer(r"\s{2,}(\S)", blk_line):
                        starts.append(m.start(1))
                    col_starts_per_line.append(starts)

                is_aligned = True
                for col_idx in range(1, col_target):
                    col_positions = [
                        starts[col_idx] for starts in col_starts_per_line if len(starts) > col_idx
                    ]
                    if (
                        len(col_positions) != len(space_block)
                        or (max(col_positions) - min(col_positions)) > 4
                    ):
                        is_aligned = False
                        break

                if is_aligned:
                    idx = curr
                    first_cells = re.split(r"\s{2,}", space_block[0].strip())
                    headers = [c.strip() for c in first_cells if c.strip()]
                    data_rows = [
                        [c.strip() for c in re.split(r"\s{2,}", r.strip()) if c.strip()]
                        for r in space_block[1:]
                    ]
                    raw_md = (
                        "| "
                        + " | ".join(headers)
                        + " |\n"
                        + "| "
                        + " | ".join(["---"] * len(headers))
                        + " |\n"
                        + "\n".join("| " + " | ".join(r) + " |" for r in data_rows)
                    )
                    span_start = line_starts[start_line_idx]
                    span_end = line_starts[idx - 1] + len(lines[idx - 1])
                    tbl = TableStructure(
                        table_id=f"tbl_p{page_number}_{table_counter}",
                        page_number=page_number,
                        headers=headers,
                        rows=data_rows,
                        raw_html_or_md=raw_md,
                        bounding_box=None,
                    )
                    detected.append(
                        DetectedTable(
                            table=tbl,
                            start_in_page=span_start,
                            end_in_page=span_end,
                        )
                    )
                    table_counter += 1
                    continue

        idx += 1

    return detected


def extract_tables_from_text(
    page_text: str,
    page_number: int,
    page_bbox: tuple[float, float, float, float] | None = None,
) -> list[TableStructure]:
    """Detect and extract tabular structures from normalized page text."""
    return [d.table for d in extract_tables_with_spans(page_text, page_number, page_bbox)]


class LiteParseDocumentAdapter(BaseDocumentAdapter):
    """Native PDF document adapter with embedded PDFium / liteparse support and pypdf fallback."""

    supported_mime_types: Sequence[str] = (
        "application/pdf",
        "application/x-pdf",
    )
    supported_extensions: Sequence[str] = (".pdf",)

    def __init__(
        self,
        *,
        ocr_mode: str = "auto",
        extract_tables: bool = True,
        extract_chunks: bool = True,
        force_fallback: bool = False,
        max_pages: int | None = 2000,
        max_bytes: int | None = 100 * 1024 * 1024,
    ) -> None:
        """Initialize LiteParseDocumentAdapter.

        Args:
            ocr_mode: OCR strategy ('auto', 'force', 'off').
            extract_tables: Whether to detect and extract tabular structures.
            extract_chunks: Whether to generate initial EvidenceChunk instances.
            force_fallback: If True, forces fallback to pypdf even if liteparse is present.
            max_pages: Maximum allowable pages in a document (None for unlimited) (SUG-02).
            max_bytes: Maximum allowable payload byte size (None for unlimited) (SUG-02).

        Raises:
            ValueError: If ocr_mode is not one of {'auto', 'force', 'off'}.
        """
        if ocr_mode not in VALID_OCR_MODES:
            raise ValueError(
                f"Invalid ocr_mode '{ocr_mode}'; must be one of {sorted(VALID_OCR_MODES)}"
            )
        self.ocr_mode = ocr_mode
        self.extract_tables = extract_tables
        self.extract_chunks = extract_chunks
        self.force_fallback = force_fallback
        self.max_pages = max_pages
        self.max_bytes = max_bytes
        self.ocr_router = TesseractOCRRouter(
            ocr_mode=ocr_mode,
            sparse_threshold=DEFAULT_SPARSE_TEXT_THRESHOLD,
            raster_threshold=DEFAULT_RASTER_RATIO_THRESHOLD,
        )

    @property
    def adapter_name(self) -> str:
        return "liteparse"

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw PDF document bytes into an IngestionResult.

        Validates the binary PDF header, attempts native liteparse extraction if
        available and not forced to fallback, and seamlessly degrades to pypdf.

        Args:
            data: Raw PDF file bytes.
            filename: Original PDF filename.
            mime_type: Optional MIME media type.
            **kwargs: Extra parameters including source_sha256, document_id, taint.

        Returns:
            Structured IngestionResult with PageMapEntry list and TableStructures.

        Raises:
            InvalidDocumentError: If PDF is empty, malformed, or corrupt.
            DocumentExtractionError: If caller-supplied source_sha256 mismatches data.
        """
        if not data:
            raise InvalidDocumentError(f"PDF document '{filename}' is empty (0 bytes)")

        # Resource guard: payload byte size limit (SUG-02)
        if self.max_bytes is not None and len(data) > self.max_bytes:
            raise InvalidDocumentError(
                f"PDF document '{filename}' exceeds maximum allowed size "
                f"({len(data)} > {self.max_bytes} bytes)"
            )

        # Validate standard PDF magic bytes in header window
        if b"%PDF-" not in data[:1024]:
            raise InvalidDocumentError(
                f"Invalid PDF document '{filename}': missing '%PDF-' header marker"
            )

        # Integrity verification of caller-supplied source_sha256 (MIN-04, SUG-01)
        actual_sha256 = hashlib.sha256(data).hexdigest()
        caller_sha256 = kwargs.pop("source_sha256", None)
        if caller_sha256 and caller_sha256 != actual_sha256:
            raise DocumentExtractionError(
                f"Supplied source_sha256 '{caller_sha256}' does not match computed "
                f"digest '{actual_sha256}' for '{filename}'"
            )

        # Strip caller-supplied fallback_reason to prevent provenance spoofing (MIN-04)
        kwargs.pop("fallback_reason", None)
        force_fallback_kwarg = kwargs.pop("force_fallback", False)

        # Normalize taint parameter with safe default (MIN-05)
        raw_taint = kwargs.pop("taint", None)
        taint = (
            TaintClassification(raw_taint)
            if raw_taint is not None
            else TaintClassification.INTERNAL
        )

        # Resolve and validate effective OCR mode (allowing caller override)
        ocr_mode_kwarg = kwargs.pop("ocr_mode", None)
        effective_ocr_mode = ocr_mode_kwarg if ocr_mode_kwarg is not None else self.ocr_mode
        if effective_ocr_mode not in VALID_OCR_MODES:
            raise ValueError(
                f"Invalid ocr_mode '{effective_ocr_mode}'; must be one of {sorted(VALID_OCR_MODES)}"
            )

        use_liteparse = HAS_LITEPARSE and not self.force_fallback and not force_fallback_kwarg

        if use_liteparse:
            try:
                return self._parse_with_liteparse(
                    data,
                    filename=filename,
                    mime_type=mime_type,
                    source_sha256=actual_sha256,
                    taint=taint,
                    ocr_mode=effective_ocr_mode,
                    **kwargs,
                )
            except LITEPARSE_ERRORS as exc:
                if isinstance(exc, (InvalidDocumentError, OCRUnavailableError)):
                    raise
                sanitized_reason = f"{type(exc).__name__}: {str(exc)[:150]}"
                logger.warning(
                    "Native liteparse extraction failed on '%s' (%s); falling back to pypdf",
                    filename,
                    sanitized_reason,
                    exc_info=True,
                )
                return self._parse_with_pypdf(
                    data,
                    filename=filename,
                    mime_type=mime_type,
                    fallback_reason=sanitized_reason,
                    source_sha256=actual_sha256,
                    taint=taint,
                    ocr_mode=effective_ocr_mode,
                    **kwargs,
                )

        return self._parse_with_pypdf(
            data,
            filename=filename,
            mime_type=mime_type,
            source_sha256=actual_sha256,
            taint=taint,
            ocr_mode=effective_ocr_mode,
            **kwargs,
        )

    def _parse_with_liteparse(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        source_sha256: str | None = None,
        taint: TaintClassification = TaintClassification.INTERNAL,
        **kwargs: Any,
    ) -> IngestionResult:
        """Execute native parsing via Google PDFium / liteparse 0.4.x."""
        if liteparse is None:
            raise ImportError("liteparse library is not installed")

        # Parse via LiteParse class or module parse function
        parser_cls = getattr(liteparse, "LiteParse", None)
        # In both 'auto' and 'force' modes, OCR is enabled for native liteparse (MAJ-01)
        ocr_enabled = self.ocr_mode != "off"
        if parser_cls is not None:
            try:
                parser = parser_cls(
                    ocr_enabled=ocr_enabled,
                    ocr_mode=self.ocr_mode,
                )
            except TypeError:
                try:
                    parser = parser_cls(ocr_enabled=ocr_enabled)
                except TypeError:
                    parser = parser_cls()
            parsed = parser.parse(data)
        elif hasattr(liteparse, "parse"):
            try:
                parsed = liteparse.parse(data, ocr_enabled=ocr_enabled)
            except TypeError:
                parsed = liteparse.parse(data)
        else:
            raise AttributeError("liteparse module does not export LiteParse or parse")

        # Extract pages and text from parsed result
        raw_pages: list[Any] = getattr(parsed, "pages", [])
        if not raw_pages and hasattr(parsed, "text"):
            # Single-text result representation
            single_text = normalize_text(str(parsed.text or ""))
            raw_pages = [{"text": single_text, "page_num": 1}]

        if not raw_pages:
            raise InvalidDocumentError(f"PDF document '{filename}' contains zero pages")

        if self.max_pages is not None and len(raw_pages) > self.max_pages:
            raise InvalidDocumentError(
                f"PDF document '{filename}' exceeds maximum allowed page count "
                f"({len(raw_pages)} > {self.max_pages})"
            )

        page_texts: list[str] = []
        page_bboxes: list[tuple[float, float, float, float] | None] = []
        page_scanned_flags: list[bool] = []
        page_ocr_applied_flags: list[bool | None] = []
        page_conf_overrides: list[float | None] = []

        for page in raw_pages:
            if isinstance(page, dict):
                p_text_raw = str(page.get("text") or "")
                bbox_raw = page.get("bounding_box") or page.get("bbox")
                p_is_scanned = bool(page.get("is_scanned", False))
                p_ocr = page.get("ocr_applied")
                p_conf = page.get("confidence")
            else:
                p_text_raw = str(getattr(page, "text", "") or "")
                # Validate bounding_box structure; fall back to bbox if absent or invalid (MIN-09)
                bbox_val = getattr(page, "bounding_box", None)
                if not (
                    isinstance(bbox_val, (list, tuple))
                    and len(bbox_val) == 4
                    and all(isinstance(v, (int, float)) for v in bbox_val)
                ):
                    bbox_val = getattr(page, "bbox", None)
                bbox_raw = bbox_val
                p_is_scanned = bool(getattr(page, "is_scanned", False))
                p_ocr = getattr(page, "ocr_applied", None)
                p_conf = getattr(page, "confidence", None)

            norm_page = normalize_text(p_text_raw)
            if norm_page and not norm_page.endswith("\n"):
                norm_page += "\n"
            page_texts.append(norm_page)
            page_bboxes.append(
                tuple(bbox_raw)
                if bbox_raw and isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4
                else None
            )
            page_scanned_flags.append(p_is_scanned)
            page_ocr_applied_flags.append(p_ocr if isinstance(p_ocr, bool) else None)
            page_conf_overrides.append(float(p_conf) if isinstance(p_conf, (int, float)) else None)

        return self._build_ingestion_result(
            page_texts=page_texts,
            page_bboxes=page_bboxes,
            page_scanned_flags=page_scanned_flags,
            data=data,
            filename=filename,
            mime_type=mime_type or "application/pdf",
            engine="liteparse",
            source_sha256=source_sha256,
            taint=taint,
            page_ocr_applied_flags=page_ocr_applied_flags,
            page_conf_overrides=page_conf_overrides,
            **kwargs,
        )

    def _parse_with_pypdf(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        fallback_reason: str | None = None,
        source_sha256: str | None = None,
        taint: TaintClassification = TaintClassification.INTERNAL,
        ocr_mode: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Execute PDF text and layout extraction via pypdf."""
        effective_mode = ocr_mode or self.ocr_mode
        if effective_mode != self.ocr_mode:
            router = TesseractOCRRouter(
                ocr_mode=effective_mode,
                sparse_threshold=self.ocr_router.sparse_threshold,
                raster_threshold=self.ocr_router.raster_threshold,
            )
            router.set_availability_override(self.ocr_router._availability_override)
        else:
            router = self.ocr_router

        try:
            reader = pypdf.PdfReader(io.BytesIO(data))

            if reader.is_encrypted:
                decrypted = False
                with contextlib.suppress(
                    ValueError,
                    TypeError,
                    AttributeError,
                    KeyError,
                    OSError,
                    pypdf.errors.PyPdfError,
                ):
                    decrypted = bool(reader.decrypt(""))
                if not decrypted and reader.is_encrypted:
                    raise InvalidDocumentError(
                        f"Password-protected PDF '{filename}' cannot be read without credentials"
                    )

            pages = reader.pages
            if not pages:
                raise InvalidDocumentError(f"PDF document '{filename}' contains zero pages")

            if self.max_pages is not None and len(pages) > self.max_pages:
                raise InvalidDocumentError(
                    f"PDF document '{filename}' exceeds maximum allowed page count "
                    f"({len(pages)} > {self.max_pages})"
                )

            page_texts: list[str] = []
            page_bboxes: list[tuple[float, float, float, float] | None] = []
            page_scanned_flags: list[bool] = []

            # Wrap whole page access and extraction loop inside try/except (MIN-02)
            for p_idx in range(len(pages)):
                page = pages[p_idx]
                raw_text = page.extract_text() or ""
                norm_page = normalize_text(raw_text)
                if norm_page and not norm_page.endswith("\n"):
                    norm_page += "\n"
                page_texts.append(norm_page)

                # Extract bounding box from MediaBox
                bbox: tuple[float, float, float, float] | None = None
                with contextlib.suppress(AttributeError, ValueError, KeyError, TypeError, OSError):
                    mb = page.mediabox
                    left = float(mb.left)
                    bottom = float(mb.bottom)
                    right = float(mb.right)
                    top = float(mb.top)
                    bbox = (left, bottom, right, top)
                page_bboxes.append(bbox)

                # Inspect images via XObject without raster pixel decoding (MAJ-01, MIN-06)
                has_images = False
                with contextlib.suppress(
                    pypdf.errors.PyPdfError,
                    ImportError,
                    NotImplementedError,
                    AttributeError,
                    ValueError,
                    KeyError,
                    TypeError,
                    OSError,
                ):

                    def _check_xobjects_for_image(xobjects_dict: Any, depth: int = 0) -> bool:
                        if depth > 3 or not isinstance(xobjects_dict, dict):
                            return False
                        for _, xobj in xobjects_dict.items():
                            resolved = xobj.get_object() if hasattr(xobj, "get_object") else xobj
                            if not isinstance(resolved, dict):
                                continue
                            subtype = resolved.get("/Subtype")
                            if subtype == "/Image":
                                return True
                            # Recursively inspect Form XObjects (MIN-06)
                            if subtype == "/Form":
                                sub_res = resolved.get("/Resources")
                                if isinstance(sub_res, dict):
                                    sub_x = sub_res.get("/XObject")
                                    if _check_xobjects_for_image(sub_x, depth + 1):
                                        return True
                        return False

                    resources = getattr(page, "get", lambda k: None)("/Resources")
                    if resources:
                        xobjects = resources.get("/XObject")
                        if xobjects:
                            has_images = _check_xobjects_for_image(xobjects, 0)

                # Evaluate page layout complexity and route OCR (Task 4B.3 / Issue #203)
                char_density = len(norm_page.strip())
                complexity = evaluate_page_complexity(
                    page,
                    text=norm_page,
                    char_count=char_density,
                )
                is_scanned = (
                    (char_density < 50 and has_images)
                    or complexity.raster_ratio > self.ocr_router.raster_threshold
                    or (has_images and complexity.is_complex)
                )
                if is_scanned or complexity.is_complex:
                    # Route complex/scanned page via TesseractOCRRouter
                    router.route_page(complexity, page_number=p_idx + 1)

                page_scanned_flags.append(is_scanned)

        except (InvalidDocumentError, OCRUnavailableError):
            raise
        except Exception as exc:
            raise InvalidDocumentError(
                f"PDF extraction failed on corrupt document '{filename}': {exc}"
            ) from exc

        return self._build_ingestion_result(
            page_texts=page_texts,
            page_bboxes=page_bboxes,
            page_scanned_flags=page_scanned_flags,
            data=data,
            filename=filename,
            mime_type=mime_type or "application/pdf",
            engine="pypdf",
            fallback_reason=fallback_reason,
            source_sha256=source_sha256,
            taint=taint,
            ocr_mode=effective_mode,
            **kwargs,
        )

    def _build_ingestion_result(
        self,
        page_texts: list[str],
        page_bboxes: list[tuple[float, float, float, float] | None],
        page_scanned_flags: list[bool],
        data: bytes,
        filename: str,
        mime_type: str,
        engine: str,
        fallback_reason: str | None = None,
        source_sha256: str | None = None,
        taint: TaintClassification = TaintClassification.INTERNAL,
        page_ocr_applied_flags: list[bool | None] | None = None,
        page_conf_overrides: list[float | None] | None = None,
        ocr_mode: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Assemble standardized IngestionResult with accurate offsets, tables, and chunks."""
        effective_mode = ocr_mode or self.ocr_mode
        actual_sha256 = source_sha256 or hashlib.sha256(data).hexdigest()

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")

        # Build concatenated normalized text
        full_normalized_text = "".join(page_texts)
        total_chars = len(full_normalized_text)

        # Extract tables per page with accurate source character spans (MIN-01, MIN-02)
        all_tables: list[TableStructure] = []
        page_detected_tables: dict[int, list[DetectedTable]] = {}
        should_extract_tables = self.extract_tables and kwargs.get("extract_tables", True)

        if should_extract_tables:
            for p_idx, (p_text, p_bbox) in enumerate(zip(page_texts, page_bboxes, strict=True)):
                page_num = p_idx + 1
                detected = extract_tables_with_spans(p_text, page_number=page_num, page_bbox=p_bbox)
                if detected:
                    page_detected_tables[page_num] = detected
                    all_tables.extend([d.table for d in detected])

        # Precompute page boundaries and boundary points for UTF-16 calculation
        page_specs: list[
            tuple[int, str, int, int, tuple[float, float, float, float] | None, bool]
        ] = []
        curr_offset = 0
        boundary_points: set[int] = {0, total_chars}

        for p_idx, (p_text, p_bbox, p_is_scanned) in enumerate(
            zip(page_texts, page_bboxes, page_scanned_flags, strict=True)
        ):
            page_num = p_idx + 1
            start_off = curr_offset
            end_off = start_off + len(p_text)
            curr_offset = end_off

            boundary_points.add(start_off)
            boundary_points.add(end_off)
            page_specs.append((page_num, p_text, start_off, end_off, p_bbox, p_is_scanned))

        # Compute UTF-16 code unit translation index across all boundary points
        utf16_points = compute_utf16_offsets_for_points(full_normalized_text, list(boundary_points))

        # Construct immutable PageMapEntry instances directly with precomputed UTF-16 offsets
        page_map: list[PageMapEntry] = []
        for p_idx, (page_num, p_text, start_off, end_off, p_bbox, p_is_scanned) in enumerate(
            page_specs
        ):
            p_ocr = (
                page_ocr_applied_flags[p_idx]
                if page_ocr_applied_flags and p_idx < len(page_ocr_applied_flags)
                else None
            )
            p_conf = (
                page_conf_overrides[p_idx]
                if page_conf_overrides and p_idx < len(page_conf_overrides)
                else None
            )

            # OCR skipped / confidence logic relative to engine and ocr_mode (MAJ-01, MAJ-03)
            if p_is_scanned:
                if engine == "pypdf" or effective_mode == "off":
                    # OCR did not run: pypdf cannot OCR, and ocr_mode='off' disables it
                    ocr_skipped = True
                    confidence = 0.8

                else:
                    # Native liteparse with OCR enabled (auto or force mode)
                    if p_ocr is not None:
                        ocr_skipped = not p_ocr
                        confidence = 1.0 if p_ocr else 0.8
                    else:
                        ocr_skipped = False
                        confidence = 1.0
            else:
                ocr_skipped = False
                confidence = 1.0

            if p_conf is not None:
                confidence = p_conf

            page_entry = PageMapEntry(
                page_number=page_num,
                text=p_text,
                start_offset=start_off,
                end_offset=end_off,
                utf16_start=utf16_points[start_off],
                utf16_end=utf16_points[end_off],
                bounding_box=p_bbox,
                is_scanned=p_is_scanned,
                ocr_skipped=ocr_skipped,
                confidence=confidence,
                has_tables=(page_num in page_detected_tables),
            )
            page_map.append(page_entry)

        # Extract EvidenceChunks if enabled
        chunks: list[EvidenceChunk] = []
        should_extract_chunks = self.extract_chunks and kwargs.get("extract_chunks", True)

        if should_extract_chunks and full_normalized_text:
            chunks = self._generate_evidence_chunks(
                page_map=page_map,
                full_text=full_normalized_text,
                doc_id=doc_id,
                source_sha256=actual_sha256,
                taint=taint,
                page_detected_tables=page_detected_tables,
            )

        metadata = dict(kwargs.get("metadata") or {})
        metadata.update(
            {
                "engine": engine,
                "has_liteparse": HAS_LITEPARSE,
                "total_pages": len(page_texts),
                "total_characters": total_chars,
                "scanned_pages": sum(1 for p in page_map if p.is_scanned),
                "tables_extracted": len(all_tables),
            }
        )
        if fallback_reason:
            metadata["fallback_reason"] = fallback_reason
        if engine == "pypdf" and effective_mode == "force":
            metadata["ocr_requested_but_unavailable"] = True

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=mime_type,
            total_pages=len(page_texts),
            total_characters=total_chars,
            chunks=chunks,
            page_map=page_map,
            tables=all_tables,
            taint=taint,
            source_sha256=source_sha256,
            normalized_text=full_normalized_text,
            metadata=metadata,
        )

    def _generate_evidence_chunks(
        self,
        page_map: list[PageMapEntry],
        full_text: str,
        doc_id: str,
        source_sha256: str | None,
        taint: TaintClassification,
        page_detected_tables: dict[int, list[DetectedTable]],
    ) -> list[EvidenceChunk]:
        """Split document pages into paragraph-level EvidenceChunk records with exact offsets."""
        chunks: list[EvidenceChunk] = []
        chunk_idx = 1
        boundary_points: set[int] = set()
        raw_chunks: list[tuple[str, int, int, int, bool, str | None]] = []

        for page in page_map:
            p_text = page.text
            p_start = page.start_offset
            if not p_text.strip():
                continue

            # Split by double newlines or non-empty paragraph blocks
            paragraphs = list(re.finditer(r"\S[^\n]*(?:\n(?!\n)\S[^\n]*)*", p_text))
            if not paragraphs:
                continue

            detected_on_page = page_detected_tables.get(page.page_number, [])

            for p_match in paragraphs:
                chunk_slice = p_match.group()
                c_start_in_page = p_match.start()
                c_end_in_page = p_match.end()
                c_start = p_start + c_start_in_page
                c_end = p_start + c_end_in_page

                # Check if paragraph overlaps with an extracted table by character span (MIN-01)
                is_table = False
                matched_table_id: str | None = None
                for d in detected_on_page:
                    overlap_start = max(c_start_in_page, d.start_in_page)
                    overlap_end = min(c_end_in_page, d.end_in_page)
                    overlap = max(0, overlap_end - overlap_start)
                    chunk_len = c_end_in_page - c_start_in_page
                    if overlap > 0 and overlap >= (chunk_len // 2):
                        is_table = True
                        matched_table_id = d.table.table_id
                        break

                raw_chunks.append(
                    (chunk_slice, page.page_number, c_start, c_end, is_table, matched_table_id)
                )
                boundary_points.add(c_start)
                boundary_points.add(c_end)

        if not raw_chunks:
            return []

        utf16_map = compute_utf16_offsets_for_points(full_text, list(boundary_points))

        for c_slice, p_num, c_start, c_end, is_table, matched_tbl_id in raw_chunks:
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"{doc_id}_p{p_num}_c{chunk_idx}",
                    document_id=doc_id,
                    text=c_slice,
                    page_number=p_num,
                    start_char=c_start,
                    end_char=c_end,
                    utf16_start=utf16_map[c_start],
                    utf16_end=utf16_map[c_end],
                    confidence=1.0,
                    is_table=is_table,
                    table_id=matched_tbl_id,
                    taint=taint,
                    source_sha256=source_sha256,
                )
            )
            chunk_idx += 1

        return chunks


__all__ = [
    "HAS_LITEPARSE",
    "InvalidDocumentError",
    "LiteParseDocumentAdapter",
    "extract_tables_from_text",
]
