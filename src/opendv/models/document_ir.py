"""Layer-1 canonical contracts for unstructured document parsing and IR projections."""

from __future__ import annotations

import hashlib
import math
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError

SinkClass = Literal["LOCAL_HUMAN_IO", "MODEL_EGRESS"]
RedactionMode = Literal["off", "on"]
SliceKind = Literal["physical_page", "virtual_section_page"]
BlockKind = Literal["heading", "paragraph", "list", "table", "figure"]
TargetProjection = Literal["document_map", "markdown", "chunks"]
OutputFormat = Literal["json", "jsonl"]
DocumentFormat = Literal["pdf", "markdown", "docx", "domain_note"]
ExtractedVia = Literal["native_text", "layout_text", "ocr_text", "empty"]
RotationDeg = Literal[0, 90, 180, 270]

DOC_SOURCE_KINDS: Final[frozenset[str]] = frozenset({"pdf", "markdown", "docx", "domain_note"})
DOCUMENT_FORMAT_BY_EXTENSION: Final[dict[str, DocumentFormat]] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "domain_note",
}
DEFAULT_MAX_SOURCE_BYTES: Final[int] = 262_144_000
_HASH_CHUNK_BYTES: Final[int] = 65_536

_CR_OR_CRLF_RE: Final[re.Pattern[str]] = re.compile(r"\r\n|\r")
_HORIZ_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")


def infer_document_format(path_or_name: str | Path) -> DocumentFormat | None:
    """Infer DocumentFormat from a filename or path extension via dict lookup."""
    suffix = Path(path_or_name).suffix.lower()
    return DOCUMENT_FORMAT_BY_EXTENSION.get(suffix)


def sanitize_source_uri(uri_or_path: str) -> str:
    """Strip userinfo, query strings, and fragments from URI-like source identifiers."""
    is_win_drive = (
        len(uri_or_path) >= 3
        and uri_or_path[0].isalpha()
        and uri_or_path[1] == ":"
        and uri_or_path[2] in ("\\", "/")
    )
    if "://" in uri_or_path and not is_win_drive:
        parts = urlsplit(uri_or_path)
        netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return uri_or_path


def normalize_page_text(text: str) -> str:
    """Normalize line endings (\\r\\n|\\r -> \\n) and horizontal whitespace ([ \\t]+ -> ' ')."""
    unified = _CR_OR_CRLF_RE.sub("\n", text)
    return _HORIZ_WS_RE.sub(" ", unified)


def compute_canonical_text_sha256(text: str) -> str:
    """Compute SHA-256 over whitespace-normalized canonical page text."""
    return hashlib.sha256(normalize_page_text(text).encode("utf-8")).hexdigest()


def is_page_scanned_or_low_density(non_ws_chars: int, image_xobjects: int) -> bool:
    """Apply authoritative scanned/low-density page classification predicate."""
    return (non_ws_chars < 50 and image_xobjects >= 1) or (non_ws_chars < 15)


@dataclass(frozen=True, slots=True)
class OcrBox:
    """Detected OCR word/line bounding box in pixel coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int
    text: str


OcrProviderFn = Callable[[bytes, int], tuple[list[OcrBox], str]]


class SourceBlob(StrictModel):
    """Context-managed handle over a local file or bounded spooled byte payload."""

    uri_sanitized: str
    binary_sha256: str
    byte_size: int = Field(ge=0)
    content_type: str | None = None
    filename_hint: str | None = None
    path: Path
    is_spooled_temp: bool = False

    def close(self) -> None:
        """Delete temporary spooled file if owned by this SourceBlob."""
        if self.is_spooled_temp:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> SourceBlob:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        uri_sanitized: str | None = None,
        binary_sha256: str | None = None,
        byte_size: int | None = None,
        content_type: str | None = None,
        filename_hint: str | None = None,
        max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> SourceBlob:
        """Wrap an existing validated local file without copying bytes into RAM."""
        if not path.is_file():
            raise OpenDVError(
                f"Source file not found: '{path.name}'",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        resolved_size = path.stat().st_size if byte_size is None else byte_size
        if resolved_size > max_bytes:
            raise OpenDVError(
                f"Source file '{path.name}' exceeds maximum allowed size of "
                f"{max_bytes} bytes ({resolved_size} bytes).",
                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            )
        resolved_sha = binary_sha256
        if resolved_sha is None:
            hasher = hashlib.sha256()
            with path.open("rb") as fh:
                while chunk := fh.read(_HASH_CHUNK_BYTES):
                    hasher.update(chunk)
            resolved_sha = hasher.hexdigest()
        resolved_hint = path.name if filename_hint is None else filename_hint
        resolved_uri = (
            sanitize_source_uri(uri_sanitized)
            if uri_sanitized is not None
            else sanitize_source_uri(str(path))
        )
        return cls(
            uri_sanitized=resolved_uri,
            binary_sha256=resolved_sha,
            byte_size=resolved_size,
            content_type=content_type,
            filename_hint=resolved_hint,
            path=path,
            is_spooled_temp=False,
        )

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        *,
        filename_hint: str | None = None,
        content_type: str | None = None,
        uri_sanitized: str | None = None,
        max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> SourceBlob:
        """Spool raw bytes to a closed temp file with size cap and partial-write cleanup."""
        if len(content) > max_bytes:
            raise OpenDVError(
                f"Source payload exceeds maximum allowed size of "
                f"{max_bytes} bytes ({len(content)} bytes).",
                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            )
        safe_suffix = ""
        if filename_hint:
            candidate_suffix = Path(filename_hint).suffix.lower()
            if candidate_suffix in DOCUMENT_FORMAT_BY_EXTENSION:
                safe_suffix = candidate_suffix
        digest = hashlib.sha256(content).hexdigest()
        default_name = Path(filename_hint).name if filename_hint else "blob"
        resolved_uri = (
            sanitize_source_uri(uri_sanitized)
            if uri_sanitized is not None
            else f"bytes://{default_name}"
        )

        tmp_path: Path | None = None
        completed = False
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="opendv_blob_",
                suffix=safe_suffix,
                delete=False,
            ) as fh:
                tmp_path = Path(fh.name)
                fh.write(content)
                fh.flush()
            blob = cls(
                uri_sanitized=resolved_uri,
                binary_sha256=digest,
                byte_size=len(content),
                content_type=content_type,
                filename_hint=filename_hint,
                path=tmp_path,
                is_spooled_temp=True,
            )
            completed = True
            return blob
        except OSError as exc:
            raise OpenDVError(
                f"Failed to spool source bytes: {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc
        finally:
            if not completed and tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


class NormalizedBBox(StrictModel):
    """Normalized page bounding box quantized to 6 decimal places for RFC 8785 JCS."""

    x0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    x1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _quantize_and_validate(self) -> NormalizedBBox:
        self.x0 = round(self.x0, 6) + 0.0
        self.y0 = round(self.y0, 6) + 0.0
        self.x1 = round(self.x1, 6) + 0.0
        self.y1 = round(self.y1, 6) + 0.0
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("Bounding box requires x0 <= x1 and y0 <= y1.")
        return self

    @classmethod
    def from_points(
        cls,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        width_pt: float,
        height_pt: float,
    ) -> NormalizedBBox:
        """Convert point coordinates into a clamped, quantized [0.0, 1.0] NormalizedBBox."""
        if not (
            math.isfinite(width_pt)
            and width_pt > 0.0
            and math.isfinite(height_pt)
            and height_pt > 0.0
        ):
            raise ValueError("Page dimensions width_pt and height_pt must be finite and positive.")
        if not all(math.isfinite(val) for val in (x0, y0, x1, y1)):
            raise ValueError("Bounding box point coordinates must be finite.")
        nx0 = max(0.0, min(1.0, x0 / width_pt))
        ny0 = max(0.0, min(1.0, y0 / height_pt))
        nx1 = max(0.0, min(1.0, x1 / width_pt))
        ny1 = max(0.0, min(1.0, y1 / height_pt))
        return cls(
            x0=min(nx0, nx1),
            y0=min(ny0, ny1),
            x1=max(nx0, nx1),
            y1=max(ny0, ny1),
        )


class PageSpan(StrictModel):
    """Addressable span linking a DocumentBlock to a 1-based PageSlice and optional bbox."""

    page_number: int = Field(ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    bbox: NormalizedBBox | None = None

    @model_validator(mode="after")
    def _validate_char_bounds(self) -> PageSpan:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must both be set or both be None.")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end must be >= char_start.")
        return self


class PageSlice(StrictModel):
    """Addressable text slice within a parsed document."""

    page_number: int = Field(ge=1)
    slice_kind: SliceKind
    renderable: bool
    canonical_text: str
    canonical_text_sha256: str
    raw_text_length: int = Field(ge=0)
    non_ws_chars: int = Field(ge=0)
    image_block_count: int = Field(ge=0)
    needs_ocr: bool
    extracted_via: ExtractedVia
    width_pt: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    height_pt: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    rotation_deg: RotationDeg | None = None


class DocumentOutlineNode(StrictModel):
    """Hierarchical outline node within a ParsedDocumentIR."""

    node_id: str
    title: str
    level: int = Field(ge=1, le=6)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    block_id: str | None = None
    children: list[DocumentOutlineNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_page_range(self) -> DocumentOutlineNode:
        if self.end_page < self.start_page:
            raise ValueError("end_page must be >= start_page.")
        return self


DocumentOutlineNode.model_rebuild()


class DocumentBlock(StrictModel):
    """Ordered structural block extracted from a document."""

    block_id: str
    kind: BlockKind
    reading_order: int = Field(ge=0)
    spans: list[PageSpan] = Field(min_length=1)
    heading_level: int | None = Field(default=None, ge=1, le=6)
    text: str
    list_ordered: bool | None = None
    table_headers: list[str] | None = None
    table_rows: list[list[str]] | None = None
    caption: str | None = None


class ChunkRecord(StrictModel):
    """Uniform DuckDB-safe JSONL projection contract emitted by ChunksProjector."""

    chunk_id: str
    doc_sha256: str
    format_id: DocumentFormat
    page_number: int = Field(ge=1)
    slice_kind: SliceKind
    canonical_text_sha256: str
    heading_path: list[str] = Field(default_factory=list)
    block_id: str
    block_kind: BlockKind
    reading_order: int = Field(ge=0)
    text: str
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    bbox_json: str | None = None
    table_json: str | None = None
    redacted: bool = False

    @model_validator(mode="after")
    def _validate_char_bounds(self) -> ChunkRecord:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must both be set or both be None.")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end must be >= char_start.")
        return self


class ParseWarning(StrictModel):
    """Non-fatal structural or parsing diagnostic emitted during document processing."""

    code: str
    message: str
    page_number: int | None = Field(default=None, ge=1)
    json_pointer: str | None = None


class ValidationDiagnostic(StrictModel):
    """Structural integrity diagnostic produced by IR validation."""

    severity: Literal["error", "warning"]
    code: str
    json_pointer: str
    message: str
    suggested_fix: str | None = None


class ParseProvenance(StrictModel):
    """Provenance and execution telemetry for a ParsedDocumentIR (Rules U1/U2)."""

    parser_id: str
    parser_version: str
    ir_schema_version: Literal["1.0"] = "1.0"
    normalizer_version: Literal["1.0"] = "1.0"
    source_uri_sanitized: str
    binary_sha256: str
    detected_mime: str | None = None
    format_id: DocumentFormat
    page_count: int = Field(ge=0)
    ocr_pages_detected: int = Field(default=0, ge=0)
    redacted: bool = False
    sink_class: SinkClass = "LOCAL_HUMAN_IO"
    elapsed_ms: int = Field(default=0, ge=0)
    warnings: list[ParseWarning] = Field(default_factory=list)


class ParsedDocumentIR(StrictModel):
    """Canonical Layer-1 document intermediate representation (Rules U1-U4)."""

    provenance: ParseProvenance
    pages: list[PageSlice]
    outline: list[DocumentOutlineNode] = Field(default_factory=list)
    blocks: list[DocumentBlock] = Field(default_factory=list)


class ParseOptions(StrictModel):
    """Execution options passed to FormatParser and ParsingEngine."""

    pages: list[int] | None = None
    max_pages: int = Field(default=1000, ge=1)
    timeout_ms: int = Field(default=30000, ge=1)
    ocr: bool = False
    emit_word_boxes: bool = False
    dpi: int = Field(default=150, ge=1)


class InspectResult(StrictModel):
    """Fast metadata inspection summary for an unstructured document."""

    source_uri_sanitized: str
    binary_sha256: str
    byte_size: int = Field(ge=0)
    format_id: DocumentFormat
    page_count: int = Field(ge=0)
    scanned_pages: list[int] = Field(default_factory=list)
    digital_pages: list[int] = Field(default_factory=list)
    renderable: bool
    warnings: list[ParseWarning] = Field(default_factory=list)


class SpilloverRef(StrictModel):
    """Reference to an oversized inline response written atomically to spill_dir."""

    spilled: Literal[True] = True
    spill_path: str
    byte_size: int = Field(ge=0)
    sha256: str
    format: OutputFormat | Literal["png"]


class RenderPageResult(StrictModel):
    """Metadata and optional base64/path reference for a rendered document page."""

    page_number: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    dpi: int = Field(ge=1)
    ocr_used: bool = False
    redacted: bool = False
    ocr_boxes_applied: int = Field(default=0, ge=0)
    output_path: str | None = None
    png_b64: str | None = None
    spillover: SpilloverRef | None = None


class ParsingCapabilitiesSnapshot(StrictModel):
    """Tier-1 installed driver capabilities for opendv.parsing."""

    liteparse_version: str | None = None
    docx_available: bool
    pillow_version: str | None = None
    pytesseract_available: bool
    tesseract_binary: str | None = None


class ParseSpecResponse(StrictModel):
    """Machine-readable schema, supported formats, projectors, and capabilities."""

    schemas: dict[str, Any]
    formats: list[DocumentFormat]
    projectors: list[TargetProjection]
    capabilities: ParsingCapabilitiesSnapshot


__all__ = [
    "DEFAULT_MAX_SOURCE_BYTES",
    "DOCUMENT_FORMAT_BY_EXTENSION",
    "DOC_SOURCE_KINDS",
    "BlockKind",
    "ChunkRecord",
    "DocumentBlock",
    "DocumentFormat",
    "DocumentOutlineNode",
    "ExtractedVia",
    "InspectResult",
    "NormalizedBBox",
    "OcrBox",
    "OcrProviderFn",
    "OutputFormat",
    "PageSlice",
    "PageSpan",
    "ParseOptions",
    "ParseProvenance",
    "ParseSpecResponse",
    "ParseWarning",
    "ParsedDocumentIR",
    "ParsingCapabilitiesSnapshot",
    "RedactionMode",
    "RenderPageResult",
    "RotationDeg",
    "SinkClass",
    "SliceKind",
    "SourceBlob",
    "SpilloverRef",
    "TargetProjection",
    "ValidationDiagnostic",
    "compute_canonical_text_sha256",
    "infer_document_format",
    "is_page_scanned_or_low_density",
    "normalize_page_text",
    "sanitize_source_uri",
]
