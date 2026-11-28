"""Shared Layer-2 parser helpers for page-window validation and outline synthesis."""

from __future__ import annotations

import time
from typing import Final

from opendv.models.document_ir import (
    DocumentBlock,
    DocumentFormat,
    DocumentOutlineNode,
    PageSlice,
    ParsedDocumentIR,
    ParseOptions,
    ParseProvenance,
    SourceBlob,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN, truncate_preserving_masked_pii
from opendv.parsing._protocols import RenderedPageBuffer

VIRTUAL_SLICE_MAX_CHARS: Final[int] = 24_000


def normalize_requested_pages(opts: ParseOptions) -> list[int] | None:
    """Validate and sort requested 1-based page numbers against ParseOptions."""
    if opts.pages is None:
        return None
    if not opts.pages or any(p < 1 for p in opts.pages):
        raise OpenDVError(
            "Requested page numbers must be non-empty 1-based positive integers.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    req_pages = sorted(set(opts.pages))
    if len(req_pages) > opts.max_pages:
        raise OpenDVError(
            f"Requested {len(req_pages)} pages exceeds max_pages={opts.max_pages}.",
            error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        )
    return req_pages


def check_page_window_bounds(
    req_pages: list[int] | None,
    total_pages: int,
    max_pages: int,
) -> None:
    """Verify requested pages fit total_pages and total_pages fits max_pages."""
    if req_pages is not None:
        oob = [p for p in req_pages if p > total_pages]
        if oob:
            raise OpenDVError(
                f"Requested page(s) {oob} exceed document page count ({total_pages}).",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
    elif total_pages > max_pages:
        raise OpenDVError(
            f"Document page count {total_pages} exceeds max_pages={max_pages}.",
            error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        )


def build_outline_from_headings(
    headings: list[tuple[int, str, int, str]],
    pages: list[PageSlice],
) -> list[DocumentOutlineNode]:
    """Build nested DocumentOutlineNode hierarchy or synthesize page-preview fallback."""
    if headings:
        roots: list[DocumentOutlineNode] = []
        stack: list[DocumentOutlineNode] = []
        for idx, (level, title, page_num, block_id) in enumerate(headings):
            clamped = max(1, min(6, level))
            clean_title = truncate_preserving_masked_pii(title.strip(), 240)
            if not clean_title:
                continue
            node = DocumentOutlineNode(
                node_id=f"out_{idx}",
                title=clean_title,
                level=clamped,
                start_page=page_num,
                end_page=page_num,
                block_id=block_id,
                children=[],
            )
            while stack and stack[-1].level >= clamped:
                popped = stack.pop()
                if stack:
                    stack[-1].end_page = max(stack[-1].end_page, popped.end_page)
            if stack:
                stack[-1].children.append(node)
                stack[-1].end_page = max(stack[-1].end_page, node.end_page)
            else:
                roots.append(node)
            stack.append(node)
        while len(stack) > 1:
            popped = stack.pop()
            stack[-1].end_page = max(stack[-1].end_page, popped.end_page)
        return roots

    fallback: list[DocumentOutlineNode] = []
    for p in pages:
        preview = ""
        for line in p.canonical_text.splitlines():
            clean = line.strip().lstrip("#").strip()
            if clean:
                preview = truncate_preserving_masked_pii(clean, 120)
                break
        if preview:
            fallback.append(
                DocumentOutlineNode(
                    node_id=f"out_p{p.page_number}",
                    title=preview,
                    level=1,
                    start_page=p.page_number,
                    end_page=p.page_number,
                    block_id=None,
                    children=[],
                )
            )
    return fallback


def split_oversized_text(text: str, limit: int = VIRTUAL_SLICE_MAX_CHARS) -> list[str]:
    """Split oversized text at safe boundaries without cutting <MASKED_PII> tokens."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    pos = 0
    n = len(text)
    token_len = len(MASKED_PII_TOKEN)
    while pos < n:
        if n - pos <= limit:
            chunks.append(text[pos:])
            break
        cut = pos + limit
        nl_idx = text.rfind("\n", pos + 1, cut + 1)
        if nl_idx > pos:
            cut = nl_idx + 1
        else:
            sp_idx = text.rfind(" ", pos + 1, cut + 1)
            if sp_idx > pos:
                cut = sp_idx + 1
        token_start = text.rfind(
            MASKED_PII_TOKEN,
            max(pos, cut - token_len + 1),
            min(n, cut + token_len - 1),
        )
        if token_start != -1 and token_start < cut < token_start + token_len:
            cut = token_start if token_start > pos else min(n, token_start + token_len)
        chunks.append(text[pos:cut])
        pos = cut
    return chunks


def apply_page_window(
    req_pages: list[int] | None,
    all_pages: list[PageSlice],
    all_blocks: list[DocumentBlock],
    headings: list[tuple[int, str, int, str]],
) -> tuple[list[PageSlice], list[DocumentBlock], list[tuple[int, str, int, str]]]:
    """Filter virtual slices, block spans, and headings by requested 1-based pages."""
    if req_pages is None:
        return all_pages, all_blocks, headings
    selected = set(req_pages)
    pages = [p for p in all_pages if p.page_number in selected]
    blocks = [
        b.model_copy(update={"spans": [s for s in b.spans if s.page_number in selected]})
        for b in all_blocks
        if any(s.page_number in selected for s in b.spans)
    ]
    filtered_headings = [h for h in headings if h[2] in selected]
    return pages, blocks, filtered_headings


def finalize_virtual_ir(
    *,
    parser_id: str,
    parser_version: str,
    blob: SourceBlob,
    format_id: DocumentFormat,
    opts: ParseOptions,
    started_at: float,
    redact: bool,
    all_pages: list[PageSlice],
    all_blocks: list[DocumentBlock],
    headings: list[tuple[int, str, int, str]],
) -> ParsedDocumentIR:
    """Validate page window, build outline, and assemble ParsedDocumentIR."""
    req_pages = normalize_requested_pages(opts)
    check_page_window_bounds(req_pages, len(all_pages), opts.max_pages)
    pages, blocks, filtered_headings = apply_page_window(
        req_pages, all_pages, all_blocks, headings
    )
    outline = build_outline_from_headings(filtered_headings, pages)
    elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
    return ParsedDocumentIR(
        provenance=ParseProvenance(
            parser_id=parser_id,
            parser_version=parser_version,
            source_uri_sanitized=blob.uri_sanitized,
            binary_sha256=blob.binary_sha256,
            detected_mime=blob.content_type,
            format_id=format_id,
            page_count=len(pages),
            ocr_pages_detected=0,
            redacted=redact,
            sink_class="MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO",
            elapsed_ms=elapsed_ms,
            warnings=[],
        ),
        pages=pages,
        outline=outline,
        blocks=blocks,
    )


class NonRenderableSliceParserMixin:
    """Shared parse/parse_redacted/render_page interface for virtual-slice adapters."""

    supports_render: bool = False

    def _parse_impl(
        self, blob: SourceBlob, opts: ParseOptions, *, redact: bool
    ) -> ParsedDocumentIR:
        raise NotImplementedError

    def parse(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
        """Parse source blob into unredacted ParsedDocumentIR."""
        return self._parse_impl(blob, opts, redact=False)

    def parse_redacted(self, blob: SourceBlob, opts: ParseOptions) -> ParsedDocumentIR:
        """Mask unsplit text before 24,000-char virtual slice partitioning."""
        return self._parse_impl(blob, opts, redact=True)

    def render_page(
        self,
        blob: SourceBlob,
        page_number: int,
        opts: ParseOptions,
    ) -> RenderedPageBuffer:
        """Reject visual page rendering on non-renderable formats."""
        _ = blob, page_number, opts
        raise OpenDVError(
            "This document format does not support visual page rendering.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )


__all__ = [
    "VIRTUAL_SLICE_MAX_CHARS",
    "NonRenderableSliceParserMixin",
    "apply_page_window",
    "build_outline_from_headings",
    "check_page_window_bounds",
    "finalize_virtual_ir",
    "normalize_requested_pages",
    "split_oversized_text",
]
