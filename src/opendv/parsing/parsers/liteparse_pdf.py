"""Pure Layer-2 PDF format adapter backed by LiteParse (Apache-2.0)."""

from __future__ import annotations

import importlib.metadata
import math
import time
from pathlib import Path
from typing import Final

from liteparse import LiteParse
from liteparse.types import (
    LayoutBlock,
    ParsedPage,
    ParseError,
    ParseResult,
    ParseTimeoutError,
)

from opendv.models.document_ir import (
    BlockKind,
    DocumentBlock,
    InspectResult,
    NormalizedBBox,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    SourceBlob,
    compute_canonical_text_sha256,
    is_page_scanned_or_low_density,
    normalize_page_text,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.parsing._protocols import NormalizedWordBox, RenderedPageBuffer
from opendv.parsing.parsers._helpers import (
    build_outline_from_headings,
    check_page_window_bounds,
    normalize_requested_pages,
)

MAX_RENDER_DPI: Final[int] = 300
MAX_RENDER_PIXELS: Final[int] = 40_000_000
_UNCONSTRAINED_INSPECT_PAGES: Final[int] = 1_000_000

_KIND_MAP: Final[dict[str, BlockKind]] = {
    "heading": "heading",
    "paragraph": "paragraph",
    "code": "paragraph",
    "grid_fallback": "paragraph",
    "list_item": "list",
    "table": "table",
    "figure": "figure",
}


def _liteparse_version() -> str:
    try:
        return importlib.metadata.version("liteparse")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _page_image_count(pg: ParsedPage) -> int:
    if pg.complexity is None:
        return 0
    return pg.complexity.image_block_count + (1 if pg.complexity.full_page_image else 0)


def _format_table_block(blk: LayoutBlock) -> tuple[list[str], list[list[str]], str]:
    headers = [c.text.strip() for c in (blk.header or [])]
    rows = [[c.text.strip() for c in r] for r in (blk.rows or [])]
    if not headers and rows:
        headers = rows[0]
        rows = rows[1:]
    if not headers and not rows:
        return [], [], ""
    cols = max([len(headers), *(len(r) for r in rows)], default=0)
    headers = headers + [""] * (cols - len(headers))
    rows = [r + [""] * (cols - len(r)) for r in rows]
    lines: list[str] = []
    if headers:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return headers, rows, "\n".join(lines)


def _convert_bbox(
    blk: LayoutBlock, width_pt: float, height_pt: float, page_num: int
) -> NormalizedBBox | None:
    if blk.bbox is None:
        return None
    try:
        return NormalizedBBox.from_points(
            blk.bbox.x,
            blk.bbox.y,
            blk.bbox.x + blk.bbox.width,
            blk.bbox.y + blk.bbox.height,
            width_pt=width_pt,
            height_pt=height_pt,
        )
    except ValueError as exc:
        raise OpenDVError(
            f"Invalid layout bounding box on page {page_num}: {exc}",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
        ) from exc


class LiteParsePdfParser:
    """Pure Layer-2 FormatParser for PDF documents using bounded LiteParse subprocesses."""

    parser_id: str = "liteparse_pdf"
    parser_version: str = _liteparse_version()
    supports_render: bool = True

    def _run_liteparse(
        self,
        path: Path,
        *,
        timeout_ms: int,
        max_pages: int,
        target_pages: str | None = None,
        extract_blocks: bool = True,
        include_complexity: bool = True,
        extract_screenshots: bool = False,
        dpi: int = 150,
        emit_word_boxes: bool = False,
    ) -> ParseResult:
        if timeout_ms < 1:
            raise OpenDVError(
                "timeout_ms must be >= 1.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        timeout_s = max(0.001, timeout_ms / 1000.0)
        try:
            with LiteParse(
                quiet=True,
                ocr_enabled=False,
                keep_headers_footers=True,
                extract_blocks=extract_blocks,
                include_complexity=include_complexity,
                extract_screenshots=extract_screenshots,
                dpi=float(dpi),
                emit_word_boxes=emit_word_boxes,
                max_pages=max_pages,
                target_pages=target_pages,
                pool_size=1,
                parse_timeout=timeout_s,
            ) as lp:
                res = lp.parse(path)
        except ParseTimeoutError as exc:
            raise OpenDVError(
                f"PDF parse timed out after {timeout_ms} ms: {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc
        except ParseError as exc:
            msg = str(exc)
            code = (
                ErrorCode.SYNTAX_INVALID_ARGUMENT
                if "target-pages" in msg or "target_pages" in msg
                else ErrorCode.VERIFICATION_GATE_FAILED
            )
            raise OpenDVError(f"PDF parse failed: {msg}", error_code=code) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise OpenDVError(
                f"PDF parse failed: {exc}",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            ) from exc

        if not isinstance(res, ParseResult) or res.total_pages < 1:
            raise OpenDVError(
                "PDF document contains zero pages.",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            )
        return res

    def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
        """Inspect page count and digital-vs-scanned status unconstrained by max_pages."""
        res = self._run_liteparse(
            blob.path,
            timeout_ms=opts.timeout_ms,
            max_pages=_UNCONSTRAINED_INSPECT_PAGES,
            extract_blocks=False,
            include_complexity=True,
        )
        scanned_pages: list[int] = []
        digital_pages: list[int] = []
        for pg in res.pages:
            norm_text = normalize_page_text(pg.text)
            non_ws = sum(1 for ch in norm_text if not ch.isspace())
            img_cnt = _page_image_count(pg)
            if is_page_scanned_or_low_density(non_ws, img_cnt):
                scanned_pages.append(pg.page_num)
            else:
                digital_pages.append(pg.page_num)
        return InspectResult(
            source_uri_sanitized=blob.uri_sanitized,
            binary_sha256=blob.binary_sha256,
            byte_size=blob.byte_size,
            format_id="pdf",
            page_count=res.total_pages,
            scanned_pages=scanned_pages,
            digital_pages=digital_pages,
            renderable=True,
            warnings=[],
        )

    def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
        """Parse PDF pages, layout blocks, spans, and outline into ParsedDocumentIR."""
        started = time.monotonic()
        req_pages = normalize_requested_pages(opts)
        if req_pages is not None:
            target_pages = ",".join(str(p) for p in req_pages)
            lite_max = max(opts.max_pages, req_pages[-1])
        else:
            target_pages = None
            lite_max = opts.max_pages + 1

        res = self._run_liteparse(
            blob.path,
            timeout_ms=opts.timeout_ms,
            max_pages=lite_max,
            target_pages=target_pages,
            extract_blocks=True,
            include_complexity=True,
            emit_word_boxes=opts.emit_word_boxes,
        )
        check_page_window_bounds(req_pages, res.total_pages, opts.max_pages)

        pages: list[PageSlice] = []
        blocks: list[DocumentBlock] = []
        headings: list[tuple[int, str, int, str]] = []
        ocr_pages_detected = 0
        reading_order = 0

        for pg in res.pages:
            width_pt = float(pg.width)
            height_pt = float(pg.height)
            if not (
                math.isfinite(width_pt)
                and width_pt > 0.0
                and math.isfinite(height_pt)
                and height_pt > 0.0
            ):
                raise OpenDVError(
                    f"Invalid PDF page dimensions on page {pg.page_num}: {pg.width}x{pg.height}.",
                    error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                )

            norm_text = normalize_page_text(pg.text)
            sha = compute_canonical_text_sha256(norm_text)
            non_ws = sum(1 for ch in norm_text if not ch.isspace())
            img_cnt = _page_image_count(pg)
            needs_ocr = is_page_scanned_or_low_density(non_ws, img_cnt)
            if needs_ocr:
                ocr_pages_detected += 1

            pages.append(
                PageSlice(
                    page_number=pg.page_num,
                    slice_kind="physical_page",
                    renderable=True,
                    canonical_text=norm_text,
                    canonical_text_sha256=sha,
                    raw_text_length=len(norm_text),
                    non_ws_chars=non_ws,
                    image_block_count=img_cnt,
                    needs_ocr=needs_ocr,
                    extracted_via="layout_text",
                    width_pt=width_pt,
                    height_pt=height_pt,
                    rotation_deg=None,
                )
            )

            cursor = 0
            for blk in pg.blocks or []:
                mapped_kind = _KIND_MAP.get(blk.kind)
                if mapped_kind is None:
                    continue
                bbox = _convert_bbox(blk, width_pt, height_pt, pg.page_num)
                table_headers: list[str] | None = None
                table_rows: list[list[str]] | None = None
                caption: str | None = None
                heading_level: int | None = None
                list_ordered: bool | None = None

                if mapped_kind == "table":
                    table_headers, table_rows, blk_text = _format_table_block(blk)
                    if not table_headers and not table_rows:
                        continue
                else:
                    raw_blk = (
                        blk.text
                        if blk.text is not None
                        else ("\n".join(blk.lines) if blk.lines else "")
                    )
                    blk_text = normalize_page_text(raw_blk).strip()
                    if mapped_kind == "figure":
                        caption = blk_text or None
                        if not blk_text and blk.id:
                            blk_text = f"[figure:{blk.id}]"
                    elif not blk_text:
                        continue

                if mapped_kind == "heading":
                    heading_level = max(1, min(6, blk.level or 1))
                elif mapped_kind == "list":
                    list_ordered = bool(blk.ordered) if blk.ordered is not None else False

                char_start: int | None = None
                char_end: int | None = None
                if mapped_kind not in ("table", "figure") and blk_text:
                    found = norm_text.find(blk_text, cursor)
                    if found != -1:
                        char_start = found
                        char_end = found + len(blk_text)
                        cursor = char_end

                block_id = f"p{pg.page_num}_b{reading_order}"
                blocks.append(
                    DocumentBlock(
                        block_id=block_id,
                        kind=mapped_kind,
                        reading_order=reading_order,
                        spans=[
                            PageSpan(
                                page_number=pg.page_num,
                                char_start=char_start,
                                char_end=char_end,
                                bbox=bbox,
                            )
                        ],
                        heading_level=heading_level,
                        text=blk_text,
                        list_ordered=list_ordered,
                        table_headers=table_headers,
                        table_rows=table_rows,
                        caption=caption,
                    )
                )
                if mapped_kind == "heading":
                    headings.append((heading_level or 1, blk_text, pg.page_num, block_id))
                reading_order += 1

        outline = build_outline_from_headings(headings, pages)
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return ParsedDocumentIR(
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                source_uri_sanitized=blob.uri_sanitized,
                binary_sha256=blob.binary_sha256,
                detected_mime=blob.content_type,
                format_id="pdf",
                page_count=len(pages),
                ocr_pages_detected=ocr_pages_detected,
                redacted=False,
                sink_class="LOCAL_HUMAN_IO",
                elapsed_ms=elapsed_ms,
                warnings=[],
            ),
            pages=pages,
            outline=outline,
            blocks=blocks,
        )

    def render_page(
        self,
        blob: SourceBlob,
        page_number: int,
        opts: ParseOptions,
    ) -> RenderedPageBuffer:
        """Preflight pixel budget and render a single PDF page to PNG + normalized word boxes."""
        if page_number < 1:
            raise OpenDVError(
                "Page number must be >= 1.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if opts.dpi > MAX_RENDER_DPI:
            raise OpenDVError(
                f"Requested dpi={opts.dpi} exceeds maximum {MAX_RENDER_DPI}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        preflight = self._run_liteparse(
            blob.path,
            timeout_ms=opts.timeout_ms,
            max_pages=page_number,
            target_pages=str(page_number),
            extract_blocks=False,
            include_complexity=False,
            extract_screenshots=False,
        )
        if page_number > preflight.total_pages or not preflight.pages:
            raise OpenDVError(
                f"Requested page {page_number} exceeds total pages ({preflight.total_pages}).",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        pre_pg = preflight.pages[0]
        if not (
            math.isfinite(pre_pg.width)
            and pre_pg.width > 0.0
            and math.isfinite(pre_pg.height)
            and pre_pg.height > 0.0
        ):
            raise OpenDVError(
                f"Invalid page dimensions on page {page_number}: {pre_pg.width}x{pre_pg.height}.",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            )
        est_pixels = float(pre_pg.width) * float(pre_pg.height) * ((float(opts.dpi) / 72.0) ** 2)
        if est_pixels > MAX_RENDER_PIXELS:
            raise OpenDVError(
                f"Estimated render pixel budget ({int(est_pixels)}) exceeds {MAX_RENDER_PIXELS}.",
                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            )

        rendered = self._run_liteparse(
            blob.path,
            timeout_ms=opts.timeout_ms,
            max_pages=page_number,
            target_pages=str(page_number),
            extract_blocks=False,
            include_complexity=False,
            extract_screenshots=True,
            dpi=opts.dpi,
            emit_word_boxes=opts.emit_word_boxes,
        )
        screenshot = next((s for s in rendered.screenshots if s.page_num == page_number), None)
        if (
            screenshot is None
            or not screenshot.image_bytes
            or screenshot.width < 1
            or screenshot.height < 1
        ):
            raise OpenDVError(
                f"Failed to render screenshot for page {page_number}.",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            )

        word_boxes: list[NormalizedWordBox] = []
        if opts.emit_word_boxes and rendered.pages:
            r_pg = rendered.pages[0]
            w_pt = float(r_pg.width)
            h_pt = float(r_pg.height)
            if not (math.isfinite(w_pt) and w_pt > 0.0 and math.isfinite(h_pt) and h_pt > 0.0):
                raise OpenDVError(
                    f"Invalid page dimensions during word-box mapping on page {page_number}.",
                    error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                )
            for item in r_pg.text_items:
                for wb in item.words:
                    if not wb.text:
                        continue
                    try:
                        bbox = NormalizedBBox.from_points(
                            wb.x,
                            wb.y,
                            wb.x + wb.width,
                            wb.y + wb.height,
                            width_pt=w_pt,
                            height_pt=h_pt,
                        )
                    except ValueError as exc:
                        raise OpenDVError(
                            f"Invalid word box on page {page_number}: {exc}",
                            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                        ) from exc
                    word_boxes.append(NormalizedWordBox(bbox=bbox, text=wb.text))

        return RenderedPageBuffer(
            png_bytes=screenshot.image_bytes,
            width_px=screenshot.width,
            height_px=screenshot.height,
            dpi=opts.dpi,
            word_boxes=tuple(word_boxes),
        )


__all__ = [
    "MAX_RENDER_DPI",
    "MAX_RENDER_PIXELS",
    "LiteParsePdfParser",
]
