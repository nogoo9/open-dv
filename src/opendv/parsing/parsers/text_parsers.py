"""Pure Layer-2 stdlib parsers for Markdown (.md/.markdown) and domain notes (.txt)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from opendv.models.document_ir import (
    BlockKind,
    DocumentBlock,
    DocumentFormat,
    InspectResult,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    SourceBlob,
    compute_canonical_text_sha256,
    infer_document_format,
    normalize_page_text,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import mask_pii_text, table_has_aba_context
from opendv.parsing.parsers._helpers import (
    VIRTUAL_SLICE_MAX_CHARS,
    NonRenderableSliceParserMixin,
    finalize_virtual_ir,
    split_oversized_text,
)
from opendv.parsing.redaction import (
    format_table_markdown,
    mask_pipe_tables_in_text,
    mask_row_cells,
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_TABLE_SEP_RE = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?$")
_UL_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_OL_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")


@dataclass(slots=True)
class _TextSegment:
    kind: BlockKind
    raw_slice: str
    text: str
    heading_level: int | None = None
    list_ordered: bool | None = None
    table_headers: list[str] | None = None
    table_rows: list[list[str]] | None = None


def _split_pipe_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _parse_text_segments(norm_text: str) -> list[_TextSegment]:
    lines = norm_text.splitlines(keepends=True)
    segments: list[_TextSegment] = []
    idx = 0
    n = len(lines)

    while idx < n:
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            idx += 1
            continue

        if stripped.startswith("```"):
            end_idx = idx + 1
            while end_idx < n and not lines[end_idx].strip().startswith("```"):
                end_idx += 1
            if end_idx < n:
                end_idx += 1
            raw_chunk = "".join(lines[idx:end_idx]).rstrip("\n")
            segments.append(_TextSegment(kind="paragraph", raw_slice=raw_chunk, text=raw_chunk))
            idx = end_idx
            continue

        heading_match = _MD_HEADING_RE.match(stripped)
        if heading_match:
            raw_chunk = line.rstrip("\n")
            segments.append(
                _TextSegment(
                    kind="heading",
                    raw_slice=raw_chunk,
                    text=heading_match.group(2).strip(),
                    heading_level=len(heading_match.group(1)),
                )
            )
            idx += 1
            continue

        if (
            "|" in stripped
            and idx + 1 < n
            and _MD_TABLE_SEP_RE.match(lines[idx + 1].strip())
        ):
            headers = _split_pipe_row(stripped)
            end_idx = idx + 2
            rows: list[list[str]] = []
            while end_idx < n and "|" in lines[end_idx] and lines[end_idx].strip():
                row_cells = _split_pipe_row(lines[end_idx])
                if len(row_cells) < len(headers):
                    row_cells.extend([""] * (len(headers) - len(row_cells)))
                rows.append(row_cells[: len(headers)])
                end_idx += 1
            raw_chunk = "".join(lines[idx:end_idx]).rstrip("\n")
            segments.append(
                _TextSegment(
                    kind="table",
                    raw_slice=raw_chunk,
                    text=raw_chunk,
                    table_headers=headers,
                    table_rows=rows,
                )
            )
            idx = end_idx
            continue

        ul_match = _UL_ITEM_RE.match(stripped)
        ol_match = _OL_ITEM_RE.match(stripped)
        if ul_match or ol_match:
            raw_chunk = line.rstrip("\n")
            item_text = (ul_match or ol_match).group(1).strip()  # type: ignore[union-attr]
            segments.append(
                _TextSegment(
                    kind="list",
                    raw_slice=raw_chunk,
                    text=item_text,
                    list_ordered=bool(ol_match),
                )
            )
            idx += 1
            continue

        end_idx = idx + 1
        while end_idx < n:
            nxt = lines[end_idx].strip()
            if (
                not nxt
                or nxt.startswith("```")
                or _MD_HEADING_RE.match(nxt)
                or _UL_ITEM_RE.match(nxt)
                or _OL_ITEM_RE.match(nxt)
                or (
                    "|" in nxt
                    and end_idx + 1 < n
                    and _MD_TABLE_SEP_RE.match(lines[end_idx + 1].strip())
                )
            ):
                break
            end_idx += 1
        raw_chunk = "".join(lines[idx:end_idx]).rstrip("\n")
        segments.append(_TextSegment(kind="paragraph", raw_slice=raw_chunk, text=raw_chunk))
        idx = end_idx
    return segments


def _read_utf8_normalized(blob: SourceBlob) -> str:
    try:
        raw_text = blob.path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OpenDVError(
            f"Invalid UTF-8 content in '{blob.uri_sanitized}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    return normalize_page_text(raw_text)


def _slice_text_with_blocks(
    norm_text: str,
    *,
    redact: bool = False,
) -> tuple[list[PageSlice], list[DocumentBlock], list[tuple[int, str, int, str]]]:
    if not norm_text:
        return (
            [
                PageSlice(
                    page_number=1,
                    slice_kind="virtual_section_page",
                    renderable=False,
                    canonical_text="",
                    canonical_text_sha256=compute_canonical_text_sha256(""),
                    raw_text_length=0,
                    non_ws_chars=0,
                    image_block_count=0,
                    needs_ocr=False,
                    extracted_via="empty",
                )
            ],
            [],
            [],
        )

    segments = _parse_text_segments(norm_text)
    if len(norm_text) <= VIRTUAL_SLICE_MAX_CHARS:
        slice_texts = [norm_text]
        seg_spans: list[list[PageSpan]] = []
        cursor = 0
        for seg in segments:
            pos = norm_text.find(seg.raw_slice, cursor)
            if pos != -1:
                seg_spans.append(
                    [
                        PageSpan(
                            page_number=1,
                            char_start=pos,
                            char_end=pos + len(seg.raw_slice),
                            bbox=None,
                        )
                    ]
                )
                cursor = pos + len(seg.raw_slice)
            else:
                seg_spans.append(
                    [PageSpan(page_number=1, char_start=None, char_end=None, bbox=None)]
                )
    else:
        slice_texts = [""]
        seg_spans = []
        for seg in segments:
            pieces = split_oversized_text(seg.raw_slice, VIRTUAL_SLICE_MAX_CHARS)
            spans: list[PageSpan] = []
            for piece in pieces:
                cur = slice_texts[-1]
                sep = "\n" if cur else ""
                if len(cur) + len(sep) + len(piece) > VIRTUAL_SLICE_MAX_CHARS and cur:
                    slice_texts.append("")
                    cur = ""
                    sep = ""
                start = len(cur) + len(sep)
                slice_texts[-1] = cur + sep + piece
                spans.append(
                    PageSpan(
                        page_number=len(slice_texts),
                        char_start=start,
                        char_end=start + len(piece),
                        bbox=None,
                    )
                )
            seg_spans.append(spans)
        if not segments:
            slice_texts = split_oversized_text(norm_text, VIRTUAL_SLICE_MAX_CHARS)

    pages: list[PageSlice] = []
    for p_num, slice_str in enumerate(slice_texts, start=1):
        non_ws = sum(1 for ch in slice_str if not ch.isspace())
        pages.append(
            PageSlice(
                page_number=p_num,
                slice_kind="virtual_section_page",
                renderable=False,
                canonical_text=slice_str,
                canonical_text_sha256=compute_canonical_text_sha256(slice_str),
                raw_text_length=len(slice_str),
                non_ws_chars=non_ws,
                image_block_count=0,
                needs_ocr=False,
                extracted_via="native_text",
            )
        )

    blocks: list[DocumentBlock] = []
    headings: list[tuple[int, str, int, str]] = []
    for idx, (seg, spans) in enumerate(zip(segments, seg_spans, strict=True)):
        block_id = f"txt_b{idx}"
        blk_text = seg.text
        blk_headers = seg.table_headers
        blk_rows = seg.table_rows
        if redact and seg.kind == "table":
            hdr_aba = table_has_aba_context(blk_headers, blk_rows)
            blk_headers = (
                mask_row_cells(blk_headers) if blk_headers is not None else None
            )
            blk_rows = (
                [mask_row_cells(row, force_aba_ctx=hdr_aba) for row in blk_rows]
                if blk_rows is not None
                else None
            )
            blk_text = (
                format_table_markdown(blk_headers, blk_rows)
                if (blk_headers or blk_rows)
                else mask_pii_text(mask_pipe_tables_in_text(blk_text))
            )
        blocks.append(
            DocumentBlock(
                block_id=block_id,
                kind=seg.kind,
                reading_order=idx,
                spans=spans,
                heading_level=seg.heading_level,
                text=blk_text,
                list_ordered=seg.list_ordered,
                table_headers=blk_headers,
                table_rows=blk_rows,
            )
        )
        if seg.kind == "heading":
            headings.append((seg.heading_level or 1, blk_text, spans[0].page_number, block_id))
    return pages, blocks, headings


def _resolve_text_format(blob: SourceBlob) -> DocumentFormat:
    inferred = infer_document_format(blob.filename_hint or blob.path)
    if inferred in ("markdown", "domain_note"):
        return inferred
    if blob.content_type:
        mime = blob.content_type.split(";", 1)[0].strip().lower()
        if mime in ("text/markdown", "text/x-markdown"):
            return "markdown"
    return "domain_note"


class MarkdownTextParser(NonRenderableSliceParserMixin):
    """Pure Layer-2 FormatParser for Markdown (.md/.markdown) and domain notes (.txt)."""

    parser_id: str = "text_stdlib"
    parser_version: str = "1.0"

    def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
        """Inspect UTF-8 text document page count and virtual slices."""
        _ = opts
        fmt = _resolve_text_format(blob)
        norm_text = _read_utf8_normalized(blob)
        pages, _, _ = _slice_text_with_blocks(norm_text)
        return InspectResult(
            source_uri_sanitized=blob.uri_sanitized,
            binary_sha256=blob.binary_sha256,
            byte_size=blob.byte_size,
            format_id=fmt,
            page_count=len(pages),
            scanned_pages=[],
            digital_pages=[p.page_number for p in pages],
            renderable=False,
            warnings=[],
        )

    def _parse_impl(
        self, blob: SourceBlob, opts: ParseOptions, *, redact: bool
    ) -> ParsedDocumentIR:
        started = time.monotonic()
        fmt = _resolve_text_format(blob)
        norm_text = _read_utf8_normalized(blob)
        if redact and norm_text:
            norm_text = mask_pii_text(mask_pipe_tables_in_text(norm_text))
        all_pages, all_blocks, headings = _slice_text_with_blocks(norm_text, redact=redact)
        return finalize_virtual_ir(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            blob=blob,
            format_id=fmt,
            opts=opts,
            started_at=started,
            redact=redact,
            all_pages=all_pages,
            all_blocks=all_blocks,
            headings=headings,
        )


__all__ = ["MarkdownTextParser"]
