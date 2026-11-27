"""Pure Layer-2 ParsingEngine orchestrating parsing, rendering, redaction, and projection."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
from collections.abc import Iterator, Sequence
from typing import Any, Final, Protocol, runtime_checkable

from opendv.models.document_ir import (
    ChunkRecord,
    DocumentFormat,
    InspectResult,
    NormalizedBBox,
    OcrBox,
    OcrProviderFn,
    PageSlice,
    ParsedDocumentIR,
    ParseOptions,
    ParsingCapabilitiesSnapshot,
    RedactionMode,
    RenderPageResult,
    SinkClass,
    SourceBlob,
    TargetProjection,
    ValidationDiagnostic,
    compute_canonical_text_sha256,
    infer_document_format,
    normalize_page_text,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.parsing._protocols import FormatParser, NormalizedWordBox
from opendv.parsing._registry import ParsingRegistry, default_registry
from opendv.parsing.parsers.liteparse_pdf import MAX_RENDER_DPI
from opendv.parsing.redaction import (
    align_pii_spans_to_boxes,
    collect_table_aba_tokens_by_page,
    ensure_ocr_driver_available,
    is_ocr_driver_available,
    mask_page_text_with_aba_tokens,
    redact_ir,
    redact_png_boxes,
    run_pytesseract_ocr,
)
from opendv.parsing.validate import validate_ir

_MIME_FORMAT_MAP: Final[dict[str, DocumentFormat]] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/plain": "domain_note",
}
_PII_BLACKOUT_PADDING_PX: Final[int] = 2


@runtime_checkable
class _RedactingParser(Protocol):
    def parse_redacted(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR: ...


def _resolve_redaction(
    sink_class: SinkClass,
    redaction: RedactionMode | None,
) -> RedactionMode:
    if sink_class == "MODEL_EGRESS" and redaction == "off":
        raise OpenDVError(
            "MODEL_EGRESS requires redaction='on'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return "on" if (redaction == "on" or sink_class == "MODEL_EGRESS") else "off"


def parse_page_range_spec(spec: str) -> list[int]:
    """Parse a 1-based page specification like '1-3,5,8-10' into sorted unique ints."""
    raw = spec.strip()
    if not raw:
        raise OpenDVError(
            "Page range specification cannot be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    pages: set[int] = set()
    for token in raw.split(","):
        part = token.strip()
        if not part:
            raise OpenDVError(
                f"Empty segment in page range specification '{spec}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if "-" in part:
            bounds = part.split("-")
            if (
                len(bounds) != 2
                or not bounds[0].strip().isdigit()
                or not bounds[1].strip().isdigit()
            ):
                raise OpenDVError(
                    f"Invalid page range segment '{part}' in '{spec}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            start = int(bounds[0].strip())
            end = int(bounds[1].strip())
            if start < 1 or end < start:
                raise OpenDVError(
                    f"Invalid page range bounds '{part}' in '{spec}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            pages.update(range(start, end + 1))
        else:
            if not part.isdigit():
                raise OpenDVError(
                    f"Invalid page number '{part}' in '{spec}'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            num = int(part)
            if num < 1:
                raise OpenDVError(
                    f"Page numbers must be >= 1 (got {num}).",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            pages.add(num)
    return sorted(pages)


def _pkg_version(dist_name: str) -> str | None:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def capabilities_snapshot() -> ParsingCapabilitiesSnapshot:
    """Return Tier-1 installed driver and library availability snapshot."""
    return ParsingCapabilitiesSnapshot(
        liteparse_version=_pkg_version("liteparse"),
        docx_available=importlib.util.find_spec("docx") is not None,
        pillow_version=_pkg_version("Pillow"),
        pytesseract_available=importlib.util.find_spec("pytesseract") is not None,
        tesseract_binary=shutil.which("tesseract"),
    )


def _ocr_boxes_to_normalized(
    boxes: Sequence[OcrBox],
    width_px: int,
    height_px: int,
) -> list[NormalizedWordBox]:
    normalized: list[NormalizedWordBox] = []
    for box in boxes:
        if not box.text.strip():
            continue
        bbox = NormalizedBBox.from_points(
            float(box.x0),
            float(box.y0),
            float(box.x1),
            float(box.y1),
            width_pt=float(width_px),
            height_pt=float(height_px),
        )
        normalized.append(NormalizedWordBox(bbox=bbox, text=box.text))
    return normalized


class ParsingEngine:
    """Pure Layer-2 parsing orchestrator over explicit FormatParser and IRProjector SPIs."""

    def __init__(
        self,
        registry: ParsingRegistry | None = None,
        ocr_provider: OcrProviderFn | None = None,
    ) -> None:
        self._registry = registry or default_registry()
        self._ocr_provider = ocr_provider

    @property
    def registry(self) -> ParsingRegistry:
        """Return the active ParsingRegistry."""
        return self._registry

    def _resolve_format(self, blob: SourceBlob) -> DocumentFormat:
        fmt = infer_document_format(blob.filename_hint or blob.path)
        if fmt is None and blob.content_type:
            mime = blob.content_type.split(";", 1)[0].strip().lower()
            fmt = _MIME_FORMAT_MAP.get(mime)
        if fmt is None:
            raise OpenDVError(
                f"Unsupported document format for '{blob.uri_sanitized}'. "
                f"Supported formats: {', '.join(self._registry.supported_formats())}.",
                error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            )
        return fmt

    def _run_ocr_on_png(
        self,
        png_bytes: bytes,
        page_number: int,
        *,
        ocr_provider: OcrProviderFn | None,
        ocr_boxes: Sequence[OcrBox] | None,
        require_if_missing: bool,
    ) -> tuple[list[OcrBox], str]:
        if ocr_boxes is not None:
            boxes_list = list(ocr_boxes)
            return boxes_list, " ".join(b.text.strip() for b in boxes_list if b.text.strip())
        if ocr_provider is not None:
            return ocr_provider(png_bytes, page_number)
        if require_if_missing or is_ocr_driver_available():
            return run_pytesseract_ocr(png_bytes)
        raise OpenDVError(
            f"Scanned page {page_number} requires OCR driver or ocr_provider under MODEL_EGRESS.",
            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
        )

    def _apply_ocr_to_ir(
        self,
        blob: SourceBlob,
        parser: FormatParser,
        ir: ParsedDocumentIR,
        opts: ParseOptions,
        *,
        redaction: RedactionMode,
    ) -> ParsedDocumentIR:
        if not parser.supports_render or not any(p.needs_ocr for p in ir.pages):
            return ir
        updated_pages: list[PageSlice] = []
        render_opts = opts.model_copy(update={"emit_word_boxes": False})
        for page in ir.pages:
            if not page.needs_ocr:
                updated_pages.append(page)
                continue
            buf = parser.render_page(blob, page.page_number, render_opts)
            _, ocr_text = self._run_ocr_on_png(
                buf.png_bytes,
                page.page_number,
                ocr_provider=self._ocr_provider,
                ocr_boxes=None,
                require_if_missing=True,
            )
            if ocr_text.strip():
                norm_ocr = normalize_page_text(ocr_text)
                combined = (
                    f"{page.canonical_text}\n{norm_ocr}".strip()
                    if page.canonical_text.strip()
                    else norm_ocr
                )
                if redaction == "on":
                    combined = mask_page_text_with_aba_tokens(combined)
                updated_pages.append(
                    page.model_copy(
                        update={
                            "canonical_text": combined,
                            "canonical_text_sha256": compute_canonical_text_sha256(combined),
                            "raw_text_length": len(combined),
                            "non_ws_chars": sum(1 for ch in combined if not ch.isspace()),
                            "extracted_via": "ocr_text",
                        }
                    )
                )
            else:
                updated_pages.append(page)
        return ir.model_copy(update={"pages": updated_pages})

    def inspect(self, blob: SourceBlob, opts: ParseOptions | None = None) -> InspectResult:
        """Inspect page counts and scan classification for a SourceBlob."""
        effective_opts = opts or ParseOptions()
        fmt = self._resolve_format(blob)
        parser = self._registry.get_parser(fmt)
        return parser.inspect(blob, effective_opts)

    def parse(
        self,
        blob: SourceBlob,
        opts: ParseOptions | None = None,
        *,
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
    ) -> ParsedDocumentIR:
        """Parse a SourceBlob into ParsedDocumentIR, enforcing redaction under MODEL_EGRESS."""
        effective_redaction = _resolve_redaction(sink_class, redaction)
        effective_opts = opts or ParseOptions()
        if effective_opts.ocr and self._ocr_provider is None:
            ensure_ocr_driver_available()

        fmt = self._resolve_format(blob)
        parser = self._registry.get_parser(fmt)

        if effective_redaction == "on":
            if isinstance(parser, _RedactingParser):
                ir = parser.parse_redacted(blob, effective_opts)
            else:
                raw_ir = parser.parse(blob, effective_opts)
                ir = redact_ir(raw_ir)
        else:
            ir = parser.parse(blob, effective_opts)

        if effective_opts.ocr:
            ir = self._apply_ocr_to_ir(
                blob, parser, ir, effective_opts, redaction=effective_redaction
            )

        updated_prov = ir.provenance.model_copy(
            update={
                "sink_class": sink_class,
                "redacted": effective_redaction == "on",
            }
        )
        return ir.model_copy(update={"provenance": updated_prov})

    def render_page(
        self,
        blob: SourceBlob,
        page_number: int,
        opts: ParseOptions | None = None,
        *,
        sink_class: SinkClass = "LOCAL_HUMAN_IO",
        redaction: RedactionMode | None = None,
        approve_scan: bool = False,
        ocr_provider: OcrProviderFn | None = None,
        ocr_boxes: Sequence[OcrBox] | None = None,
    ) -> tuple[bytes, RenderPageResult]:
        """Render a page PNG with scan gating and fail-closed MODEL_EGRESS PII blackout."""
        effective_redaction = _resolve_redaction(sink_class, redaction)
        effective_opts = opts or ParseOptions()
        effective_ocr = ocr_provider or self._ocr_provider
        fmt = self._resolve_format(blob)
        parser = self._registry.get_parser(fmt)

        if not parser.supports_render:
            raise OpenDVError(
                f"Format '{fmt}' does not support visual page rendering.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if page_number < 1:
            raise OpenDVError(
                f"Page number must be >= 1 (got {page_number}).",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if effective_opts.dpi > MAX_RENDER_DPI:
            raise OpenDVError(
                f"Requested dpi={effective_opts.dpi} exceeds maximum {MAX_RENDER_DPI}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if effective_opts.ocr and effective_ocr is None and ocr_boxes is None:
            ensure_ocr_driver_available()

        render_opts = effective_opts.model_copy(
            update={"emit_word_boxes": (effective_redaction == "on")}
        )
        buf = parser.render_page(blob, page_number, render_opts)
        single_ir = parser.parse(
            blob,
            effective_opts.model_copy(update={"pages": [page_number], "emit_word_boxes": False}),
        )
        page_slice = single_ir.pages[0]

        if effective_redaction == "off":
            if page_slice.needs_ocr and not approve_scan:
                raise OpenDVError(
                    f"Scanned page {page_number} blocked without approve_scan "
                    "under LOCAL_HUMAN_IO.",
                    error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
                )
            ocr_used = False
            ocr_count = 0
            if effective_opts.ocr:
                detected, _ = self._run_ocr_on_png(
                    buf.png_bytes,
                    page_number,
                    ocr_provider=effective_ocr,
                    ocr_boxes=ocr_boxes,
                    require_if_missing=True,
                )
                ocr_used = True
                ocr_count = len(detected)
            return buf.png_bytes, RenderPageResult(
                page_number=page_number,
                width=buf.width_px,
                height=buf.height_px,
                dpi=buf.dpi,
                ocr_used=ocr_used,
                redacted=False,
                ocr_boxes_applied=ocr_count,
            )

        # MODEL_EGRESS / redaction == "on": approve_scan NEVER bypasses redaction
        all_boxes: list[NormalizedWordBox] = list(buf.word_boxes)
        combined_text = page_slice.canonical_text
        ocr_used = False
        if page_slice.needs_ocr:
            raw_ocr_boxes, ocr_text = self._run_ocr_on_png(
                buf.png_bytes,
                page_number,
                ocr_provider=effective_ocr,
                ocr_boxes=ocr_boxes,
                require_if_missing=effective_opts.ocr,
            )
            norm_ocr_boxes = _ocr_boxes_to_normalized(raw_ocr_boxes, buf.width_px, buf.height_px)
            if not norm_ocr_boxes and not ocr_text.strip() and not buf.word_boxes:
                raise OpenDVError(
                    f"Scanned page {page_number} returned no OCR text or boxes under MODEL_EGRESS.",
                    error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
                )
            ocr_used = True
            all_boxes.extend(norm_ocr_boxes)
            combined_text = f"{combined_text}\n{ocr_text}".strip() if combined_text else ocr_text

        aba_tokens = collect_table_aba_tokens_by_page(single_ir.blocks).get(page_number, set())
        matched_boxes = align_pii_spans_to_boxes(
            combined_text, all_boxes, aba_tokens=aba_tokens
        )
        out_png = (
            redact_png_boxes(buf.png_bytes, matched_boxes, padding_px=_PII_BLACKOUT_PADDING_PX)
            if matched_boxes
            else buf.png_bytes
        )
        return out_png, RenderPageResult(
            page_number=page_number,
            width=buf.width_px,
            height=buf.height_px,
            dpi=buf.dpi,
            ocr_used=ocr_used,
            redacted=True,
            ocr_boxes_applied=len(matched_boxes),
        )

    def validate(self, ir: ParsedDocumentIR) -> list[ValidationDiagnostic]:
        """Validate ParsedDocumentIR canonical hashes, span bounds, and outline structure."""
        return validate_ir(ir)

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        target: TargetProjection,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> dict[str, Any] | list[ChunkRecord] | str:
        """Project ParsedDocumentIR into the requested target output."""
        projector = self._registry.get_projector(target)
        return projector.project(
            ir,
            redaction=redaction,
            doc_id=doc_id,
            filename=filename,
            approved_scan_pages=approved_scan_pages,
        )

    def stream_jsonl(
        self,
        ir: ParsedDocumentIR,
        *,
        target: TargetProjection = "chunks",
        redaction: RedactionMode = "off",
    ) -> Iterator[str]:
        """Stream newline-delimited JSON records for the requested target projection."""
        projector = self._registry.get_projector(target)
        return projector.stream_jsonl(ir, redaction=redaction)

    def capabilities(self) -> ParsingCapabilitiesSnapshot:
        """Return Tier-1 installed driver availability snapshot."""
        return capabilities_snapshot()


__all__ = [
    "ParsingEngine",
    "capabilities_snapshot",
    "parse_page_range_spec",
]
