"""Layer-4 ParsingService, workspace-contained SourceBlob resolution, and grounding notes."""

from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from opendv.models.document_ir import (
    DEFAULT_MAX_SOURCE_BYTES,
    ChunkRecord,
    DocumentOutlineNode,
    InspectResult,
    OcrBox,
    OcrProviderFn,
    ParsedDocumentIR,
    ParseOptions,
    ParseSpecResponse,
    ParsingCapabilitiesSnapshot,
    RedactionMode,
    RenderPageResult,
    SinkClass,
    SourceBlob,
    SpilloverRef,
    TargetProjection,
    ValidationDiagnostic,
    sanitize_source_uri,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import strip_pii_spans
from opendv.models.sources import DocumentMap
from opendv.parsing import ParsingEngine, ParsingRegistry
from opendv.parsing import parse_page_range_spec as _parse_page_range_spec
from opendv.service._remediation import build_remediation
from opendv.service.bundles.zip_guard import (
    reject_symlinks_on_path_and_ancestors,
    safe_open_no_follow,
)
from opendv.service.os_platform import validate_workspace_containment

_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_URI_WIN_DRIVE_RE = re.compile(r"^/?[A-Za-z]:[\\/]")
_WIN_ILLEGAL_PATH_CHARS = frozenset('?<>*\"')
_HASH_CHUNK_BYTES = 65_536
_SECURITY_REMAPPED_MARKERS = (
    "UNC ",
    "UNC share",
    "Alternate Data Stream",
    "escapes configured",
)


def _has_control_or_null(text: str) -> bool:
    return "\x00" in text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _resolve_redaction(sink_class: SinkClass, redaction: RedactionMode | None) -> RedactionMode:
    if sink_class == "MODEL_EGRESS" and redaction == "off":
        raise OpenDVError(
            "MODEL_EGRESS requires redaction='on'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return "on" if (redaction == "on" or sink_class == "MODEL_EGRESS") else "off"


def _arg_err(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


def _sec_err(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION)


def _size_limit_error(name: str, max_bytes: int, actual_bytes: int) -> OpenDVError:
    return OpenDVError(
        f"Source file '{name}' exceeds {max_bytes} bytes ({actual_bytes} bytes).",
        error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
    )


def resolve_local_source_blob(
    source_or_uri: Path | str,
    *,
    workspace_root: Path | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> SourceBlob:
    """Validate local path/file URI containment, reject symlinks, and wrap a bounded SourceBlob."""
    raw_orig = os.fspath(source_or_uri)
    if _has_control_or_null(raw_orig):
        raise _arg_err("Source path contains forbidden control or null bytes.")
    raw = raw_orig.strip()
    if not raw:
        raise _arg_err("Source path or URI cannot be empty.")
    if "::" in raw:
        raise _arg_err(f"Chained URI schemes are forbidden: '{raw}'.")

    is_win_drive = bool(_WIN_DRIVE_RE.match(raw))
    if not is_win_drive and raw.startswith(("\\\\", "//")):
        raise _sec_err(f"UNC source paths are forbidden: '{raw}'.")

    uri_sanitized: str | None = None
    if not is_win_drive and "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme.lower() != "file":
            raise _arg_err(
                f"Unsupported source URI scheme '{parsed.scheme}'; only file URIs are allowed."
            )
        if parsed.netloc:
            raise _arg_err(
                "Remote or non-empty URI host authority is forbidden for local file URIs."
            )
        unquoted_path = urllib.parse.unquote(parsed.path)
        if _has_control_or_null(unquoted_path):
            raise _arg_err("Source URI path contains forbidden control or null bytes.")
        drive_match = _URI_WIN_DRIVE_RE.match(unquoted_path)
        remainder = unquoted_path[drive_match.end() :] if drive_match else unquoted_path
        if ":" in remainder or "|" in remainder:
            raise _sec_err(
                f"Source URI contains forbidden Alternate Data Stream or drive separator: '{raw}'."
            )
        try:
            decoded = urllib.request.url2pathname(unquoted_path)
        except (OSError, ValueError) as exc:
            err_msg = str(exc)
            if ":" in err_msg or "|" in err_msg:
                raise _sec_err(
                    f"Source URI contains forbidden Alternate Data Stream or bad path: '{raw}'."
                ) from exc
            raise _arg_err(f"Malformed local file URI '{raw}': {exc}") from exc
        uri_sanitized = sanitize_source_uri(raw)
    else:
        decoded = raw

    if _has_control_or_null(decoded) or not decoded.strip():
        raise _arg_err("Decoded source path is empty or contains forbidden control bytes.")
    if not _WIN_DRIVE_RE.match(decoded) and decoded.startswith(("\\\\", "//")):
        raise _sec_err(f"UNC source paths are forbidden: '{decoded}'.")
    decoded_drive_match = _WIN_DRIVE_RE.match(decoded)
    decoded_remainder = decoded[decoded_drive_match.end() :] if decoded_drive_match else decoded
    if ":" in decoded_remainder or "|" in decoded_remainder:
        raise _sec_err(
            f"Source path contains forbidden Alternate Data Stream or pipe character: '{raw}'."
        )
    if os.name == "nt" and any(ch in _WIN_ILLEGAL_PATH_CHARS for ch in decoded_remainder):
        raise _arg_err(f"Source path contains illegal Windows filename character: '{raw}'.")

    cand = Path(decoded)
    base_ws = workspace_root if workspace_root is not None else Path.cwd()
    if not cand.is_absolute():
        cand = base_ws / cand

    if allowed_roots is not None:
        raw_roots = [os.fspath(item) for item in allowed_roots]
    else:
        env_val = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
        raw_roots = env_val.split(os.pathsep) if env_val else [str(base_ws)]
    effective_roots = [Path(c) for item in raw_roots if (c := item.strip())]
    if not effective_roots:
        raise _arg_err("No allowed workspace roots configured.")

    stop_roots: list[Path] = []
    seen_stops: set[str] = set()
    for root in effective_roots:
        for cand_stop in (os.path.abspath(str(root)), os.path.realpath(root)):
            norm_stop = os.path.normcase(cand_stop)
            if norm_stop not in seen_stops:
                seen_stops.add(norm_stop)
                stop_roots.append(Path(cand_stop))

    reject_symlinks_on_path_and_ancestors(cand, stop_roots=stop_roots)
    try:
        contained = validate_workspace_containment(cand, allowed_roots=effective_roots)
    except OpenDVError as exc:
        if exc.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT and any(
            m in exc.detail for m in _SECURITY_REMAPPED_MARKERS
        ):
            raise _sec_err(exc.detail) from exc
        raise

    try:
        fd = safe_open_no_follow(contained, os.O_RDONLY, stop_roots=stop_roots)
    except FileNotFoundError as exc:
        raise OpenDVError(
            f"Source file not found: '{contained.name}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        ) from exc
    except (IsADirectoryError, PermissionError) as exc:
        if os.path.isdir(contained):
            raise _arg_err(
                f"Expected a local file path, got directory: '{contained.name}'."
            ) from exc
        raise _sec_err(f"Permission denied for '{contained.name}': {exc}") from exc
    except OpenDVError as exc:
        if (
            exc.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
            and "Non-regular file rejected" in exc.detail
            and os.path.isdir(contained)
        ):
            raise _arg_err(
                f"Expected a local file path, got directory: '{contained.name}'."
            ) from exc
        raise
    except OSError as exc:
        raise _arg_err(f"Unreadable local path '{contained.name}': {exc}") from exc

    hasher = hashlib.sha256()
    total_bytes = 0
    with os.fdopen(fd, "rb") as fh:
        st_size = os.fstat(fh.fileno()).st_size
        if st_size > max_bytes:
            raise _size_limit_error(contained.name, max_bytes, st_size)
        while chunk := fh.read(_HASH_CHUNK_BYTES):
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise _size_limit_error(contained.name, max_bytes, total_bytes)
            hasher.update(chunk)

    return SourceBlob.from_path(
        contained, uri_sanitized=uri_sanitized or str(contained), binary_sha256=hasher.hexdigest(),
        byte_size=total_bytes, filename_hint=contained.name, max_bytes=max_bytes,
    )


def _strip_pii_clean(text: str) -> str:
    return strip_pii_spans(text, strip_placeholders=True).strip()


def _strip_outline_pii(
    nodes: Sequence[DocumentOutlineNode],
    block_text_by_id: Mapping[str, str],
) -> list[DocumentOutlineNode]:
    cleaned: list[DocumentOutlineNode] = []
    for node in nodes:
        if node.node_id.startswith("out_p") and node.block_id is None:
            continue
        raw_title = (
            block_text_by_id[node.block_id][:240]
            if node.block_id and node.block_id in block_text_by_id
            else strip_pii_spans(node.title, strip_placeholders=True)
        )
        cleaned.append(
            node.model_copy(
                update={
                    "title": raw_title,
                    "children": _strip_outline_pii(node.children, block_text_by_id),
                }
            )
        )
    return cleaned


def _summarize_document_map(dmap: DocumentMap, snippets: Sequence[str] = ()) -> str:
    clean_name = _strip_pii_clean(dmap.filename) or "document"
    parts = [f"Document {clean_name} ({dmap.format}, {dmap.page_count} pages)"]
    outlines = [
        clean_t
        for node in dmap.outline[:8]
        if (clean_t := _strip_pii_clean(node.title))
    ]
    if outlines:
        parts.append("Outline: " + "; ".join(outlines))
    headings: list[str] = []
    headers: list[str] = []
    for pg in dmap.pages[:8]:
        clean_hd = _strip_pii_clean(pg.heading_preview)
        if clean_hd and clean_hd not in headings:
            headings.append(clean_hd)
        for hdr in pg.table_headers:
            clean_h = _strip_pii_clean(hdr)
            if clean_h and clean_h not in headers:
                headers.append(clean_h)
    if headings:
        parts.append("Headings: " + "; ".join(headings[:8]))
    if headers:
        parts.append("Table headers: " + ", ".join(headers[:16]))
    clean_snippets = [clean_s for s in snippets if (clean_s := _strip_pii_clean(s))]
    if clean_snippets:
        parts.append("Context: " + " | ".join(clean_snippets[:6]))
    return "\n".join(parts)


class ParsingService:
    """Standalone Layer-4 unstructured parsing service (zero ProjectStateManager dependency)."""

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        registry: ParsingRegistry | None = None,
        ocr_provider: OcrProviderFn | None = None,
    ) -> None:
        self._workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else None
        )
        self._allowed_roots = (
            tuple(Path(r) for r in allowed_roots) if allowed_roots is not None else None
        )
        self._engine = ParsingEngine(registry=registry, ocr_provider=ocr_provider)

    def _resolve_blob(self, source: Path | str | SourceBlob) -> AbstractContextManager[SourceBlob]:
        if isinstance(source, SourceBlob):
            return nullcontext(source)
        blob = resolve_local_source_blob(
            source,
            workspace_root=self._workspace_root,
            allowed_roots=self._allowed_roots,
        )
        return nullcontext(blob)

    def get_capabilities(self) -> ParsingCapabilitiesSnapshot:
        """Return Tier-1 installed parsing and OCR driver availability."""
        return self._engine.capabilities()

    @staticmethod
    def parse_page_range_spec(spec: str) -> list[int]:
        """Parse a 1-based page range string into sorted unique page numbers."""
        return _parse_page_range_spec(spec)

    def get_spec(self) -> ParseSpecResponse:
        """Return machine-readable JSON schemas, supported formats, projectors, and drivers."""
        schema_types = (
            ParsedDocumentIR, ChunkRecord, InspectResult, RenderPageResult,
            ValidationDiagnostic, ParseOptions, SpilloverRef,
        )
        return ParseSpecResponse(
            schemas={cls.__name__: cls.model_json_schema() for cls in schema_types},
            formats=self._engine.registry.supported_formats(),
            projectors=self._engine.registry.supported_projectors(),
            capabilities=self.get_capabilities(),
        )

    def inspect_document(
        self, source: Path | str | SourceBlob, *, options: ParseOptions | None = None
    ) -> InspectResult:
        """Inspect page count and digital-vs-scanned classification without mutating state."""
        with self._resolve_blob(source) as blob:
            return self._engine.inspect(blob, options)

    def parse_document(
        self,
        source: Path | str | SourceBlob,
        options: ParseOptions | None = None,
        *,
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
    ) -> ParsedDocumentIR:
        """Parse a validated local document into ParsedDocumentIR with sink redaction rules."""
        with self._resolve_blob(source) as blob:
            return self._engine.parse(blob, options, sink_class=sink_class, redaction=redaction)

    def export_ir(
        self,
        ir: ParsedDocumentIR,
        *,
        target: TargetProjection,
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> dict[str, Any] | list[ChunkRecord] | str:
        """Project ParsedDocumentIR into document_map, markdown, or chunks."""
        return self._engine.project(
            ir, target=target, redaction=_resolve_redaction(sink_class, redaction),
            doc_id=doc_id, filename=filename, approved_scan_pages=approved_scan_pages,
        )

    def stream_export(
        self,
        ir: ParsedDocumentIR,
        *,
        target: TargetProjection = "chunks",
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
    ) -> Iterator[str]:
        """Stream JSONL records from ParsedDocumentIR for the requested target projection."""
        return self._engine.stream_jsonl(
            ir, target=target, redaction=_resolve_redaction(sink_class, redaction)
        )

    def render_document_page(
        self,
        source: Path | str | SourceBlob,
        page_number: int,
        *,
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
        approve_scan: bool = False,
        approved_scan_pages: Sequence[int] = (),
        ocr_provider: OcrProviderFn | None = None,
        ocr_boxes: Sequence[OcrBox] | None = None,
        options: ParseOptions | None = None,
    ) -> tuple[bytes, RenderPageResult]:
        """Render a document page PNG with scan gating and MODEL_EGRESS PII blackout."""
        effective_approve = approve_scan or (page_number in approved_scan_pages)
        with self._resolve_blob(source) as blob:
            try:
                return self._engine.render_page(
                    blob, page_number, options, sink_class=sink_class, redaction=redaction,
                    approve_scan=effective_approve, ocr_provider=ocr_provider, ocr_boxes=ocr_boxes,
                )
            except OpenDVError as exc:
                if exc.error_code == ErrorCode.DRIVER_MISSING and exc.remediation is None:
                    caps = self.get_capabilities()
                    raise OpenDVError(
                        exc.detail,
                        error_code=exc.error_code,
                        remediation=build_remediation(
                            ErrorCode.DRIVER_MISSING, extra_name="ocr",
                            tesseract_missing=(
                                caps.pytesseract_available and caps.tesseract_binary is None
                            ),
                        ),
                    ) from exc
                raise

    def validate_ir(self, ir: ParsedDocumentIR) -> list[ValidationDiagnostic]:
        """Validate canonical digests, span bounds, and outline structure of ParsedDocumentIR."""
        return self._engine.validate(ir)

    def build_document_map(
        self,
        source: Path | str | SourceBlob,
        *,
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
        options: ParseOptions | None = None,
    ) -> tuple[DocumentMap, dict[int, str]]:
        """Parse raw pages for internal callers and project a MODEL_EGRESS-masked DocumentMap."""
        with self._resolve_blob(source) as blob:
            ir_raw = self.parse_document(
                blob, options=options, sink_class="LOCAL_HUMAN_IO", redaction="off"
            )
            resolved_name = filename or blob.filename_hint
        projected = self.export_ir(
            ir_raw, target="document_map", sink_class="MODEL_EGRESS", redaction="on",
            doc_id=doc_id, filename=resolved_name, approved_scan_pages=approved_scan_pages,
        )
        assert isinstance(projected, dict)
        return projected["document_map"], {pg.page_number: pg.canonical_text for pg in ir_raw.pages}

    def build_grounding_summary_notes(
        self,
        sources: Sequence[Path | str | SourceBlob | DocumentMap],
        *,
        max_notes: int = 5,
        max_chars_per_note: int = 1500,
    ) -> list[str]:
        """Build bounded, placeholder-free PII-stripped summary notes for ontology grounding."""
        if max_notes <= 0 or max_chars_per_note <= 0:
            return []
        notes: list[str] = []
        for item in sources:
            if len(notes) >= max_notes:
                break
            if isinstance(item, DocumentMap):
                raw_note = _summarize_document_map(item)
            else:
                info = self.inspect_document(item)
                opts = (
                    ParseOptions(pages=list(range(1, min(info.page_count, 5) + 1)))
                    if info.renderable and info.page_count > 5
                    else ParseOptions()
                )
                ir = self.parse_document(
                    item, options=opts, sink_class="MODEL_EGRESS", redaction="on"
                )
                clean_blocks = [
                    blk.model_copy(
                        update={
                            "text": _strip_pii_clean(blk.text),
                            "table_headers": (
                                [_strip_pii_clean(h) for h in blk.table_headers]
                                if blk.table_headers is not None else None
                            ),
                        }
                    )
                    for blk in ir.blocks
                ]
                block_text_by_id = {blk.block_id: blk.text for blk in clean_blocks}
                clean_ir = ir.model_copy(
                    update={
                        "outline": _strip_outline_pii(ir.outline, block_text_by_id),
                        "pages": [
                            pg.model_copy(
                                update={"canonical_text": _strip_pii_clean(pg.canonical_text)}
                            )
                            for pg in ir.pages
                        ],
                        "blocks": clean_blocks,
                    }
                )
                proj = self.export_ir(
                    clean_ir, target="document_map", sink_class="LOCAL_HUMAN_IO", redaction="off"
                )
                assert isinstance(proj, dict)
                dmap: DocumentMap = proj["document_map"]
                if info.page_count > dmap.page_count:
                    dmap = dmap.model_copy(update={"page_count": info.page_count})
                snippets = [blk.text[:240] for blk in clean_ir.blocks[:6] if blk.text.strip()]
                if not snippets:
                    snippets = [
                        pg.canonical_text[:300]
                        for pg in clean_ir.pages[:3]
                        if pg.canonical_text.strip()
                    ]
                raw_note = _summarize_document_map(dmap, snippets=snippets)
            cleaned = _strip_pii_clean(raw_note)
            if len(cleaned) > max_chars_per_note:
                cleaned = _strip_pii_clean(cleaned[:max_chars_per_note].rstrip())
            if cleaned:
                notes.append(cleaned)
        return notes


__all__ = [
    "ParsingService",
    "resolve_local_source_blob",
]
