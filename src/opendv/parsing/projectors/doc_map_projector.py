"""Layer-2 projector from ParsedDocumentIR to DocumentMap and PageTextView."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from opendv.models.document_ir import (
    DocumentBlock,
    DocumentOutlineNode,
    PageSlice,
    ParsedDocumentIR,
    RedactionMode,
    TargetProjection,
)
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    RE_ABA_DIGITS,
    mask_pii_text,
    truncate_preserving_masked_pii,
)
from opendv.models.sources import DocumentMap, OutlineNode, PageMapSpec, PageTextView
from opendv.parsing.parsers._helpers import VIRTUAL_SLICE_MAX_CHARS
from opendv.parsing.redaction import (
    collect_table_aba_tokens_by_page,
    find_pii_spans,
    mask_page_text_with_aba_tokens,
    mask_row_cells,
)

_PIPE_SEP_RE = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?$")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def _extract_table_headers_from_text(text: str, *, redact: bool = False) -> list[str]:
    lines = text.splitlines()
    found: list[str] = []
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        if "|" in line and idx + 1 < len(lines) and _PIPE_SEP_RE.match(lines[idx + 1].strip()):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            if len(cells) >= 2:
                found = cells
                break
        if "\t" in line:
            tab_cells = [c.strip() for c in line.split("\t") if c.strip()]
            if len(tab_cells) >= 2 and all(len(c) <= 48 for c in tab_cells):
                found = tab_cells
                break
        spaced = [c.strip() for c in _MULTI_SPACE_RE.split(line) if c.strip()]
        if len(spaced) >= 2 and all("_" in c and " " not in c for c in spaced):
            found = spaced
            break
        tokens = line.split()
        if len(tokens) >= 2 and all(
            "_" in tok and tok.replace("_", "").isalnum() for tok in tokens
        ):
            found = tokens
            break
    return mask_row_cells(found) if (redact and found) else found


def _mask_virtual_run(
    run: Sequence[PageSlice],
    out: dict[int, str],
    aba_tokens_by_page: Mapping[int, set[str]],
) -> None:
    parts: list[str] = []
    ranges: list[tuple[int, int, int, str]] = []
    cursor = 0
    for idx, pg in enumerate(run):
        if idx > 0:
            prev_txt = run[idx - 1].canonical_text
            cur_txt = pg.canonical_text
            sep = (
                ""
                if (
                    (prev_txt and prev_txt[-1].isspace())
                    or (cur_txt and cur_txt[0].isspace())
                    or len(prev_txt) >= VIRTUAL_SLICE_MAX_CHARS
                )
                else "\n"
            )
            if sep:
                parts.append(sep)
                cursor += len(sep)
        start = cursor
        parts.append(pg.canonical_text)
        cursor += len(pg.canonical_text)
        ranges.append((pg.page_number, start, cursor, pg.canonical_text))

    joined = "".join(parts)
    raw_spans = list(find_pii_spans(joined))
    for p_num, p_start, p_end, _txt in ranges:
        p_aba = aba_tokens_by_page.get(p_num)
        if p_aba:
            for m in RE_ABA_DIGITS.finditer(joined, p_start, p_end):
                if m.group(0) in p_aba:
                    raw_spans.append((m.start(), m.end()))
    spans: list[tuple[int, int]] = []
    if raw_spans:
        raw_spans.sort()
        spans.append(raw_spans[0])
        for s, e in raw_spans[1:]:
            ps, pe = spans[-1]
            if s <= pe:
                spans[-1] = (ps, max(pe, e))
            else:
                spans.append((s, e))

    if not spans:
        for p_num, _s, _e, txt in ranges:
            out[p_num] = mask_page_text_with_aba_tokens(txt, aba_tokens_by_page.get(p_num))
        return

    for p_num, p_start, p_end, txt in ranges:
        p_len = p_end - p_start
        buf: list[str] = []
        pos = 0
        for s, e in spans:
            if e <= p_start or s >= p_end:
                continue
            local_s = max(0, s - p_start)
            local_e = min(p_len, e - p_start)
            if local_s > pos:
                buf.append(txt[pos:local_s])
            if s >= p_start:
                buf.append(MASKED_PII_TOKEN)
            pos = local_e
        if pos < p_len:
            buf.append(txt[pos:])
        out[p_num] = mask_pii_text("".join(buf))


def _mask_pages_map(ir: ParsedDocumentIR) -> dict[int, str]:
    aba_tokens_by_page = collect_table_aba_tokens_by_page(ir.blocks)
    if ir.provenance.redacted or len(ir.pages) <= 1:
        return {
            pg.page_number: mask_page_text_with_aba_tokens(
                pg.canonical_text, aba_tokens_by_page.get(pg.page_number)
            )
            for pg in ir.pages
        }
    out: dict[int, str] = {}
    run: list[PageSlice] = []
    for pg in ir.pages:
        if pg.slice_kind != "virtual_section_page":
            if run:
                _mask_virtual_run(run, out, aba_tokens_by_page)
                run = []
            out[pg.page_number] = mask_page_text_with_aba_tokens(
                pg.canonical_text, aba_tokens_by_page.get(pg.page_number)
            )
        elif not run or pg.page_number == run[-1].page_number + 1:
            run.append(pg)
        else:
            _mask_virtual_run(run, out, aba_tokens_by_page)
            run = [pg]
    if run:
        _mask_virtual_run(run, out, aba_tokens_by_page)
    return out


def _extract_heading_preview(text: str, *, redact: bool = False) -> str:
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            if redact:
                cleaned = mask_pii_text(cleaned)
            return truncate_preserving_masked_pii(cleaned, 120)
    return ""


def _flatten_outline(
    nodes: Sequence[DocumentOutlineNode],
    *,
    redact: bool,
    block_text_by_id: Mapping[str, str],
    heading_preview_by_page: Mapping[int, str],
    out: list[OutlineNode],
) -> None:
    for node in nodes:
        if redact and node.node_id.startswith("out_p") and node.block_id is None:
            title = heading_preview_by_page.get(node.start_page) or mask_pii_text(node.title)
        elif node.block_id and node.block_id in block_text_by_id:
            raw_title = block_text_by_id[node.block_id].strip()
            title = truncate_preserving_masked_pii(
                mask_pii_text(raw_title) if redact else raw_title, 240
            )
        else:
            title = mask_pii_text(node.title) if redact else node.title
        out.append(OutlineNode(title=title, page_start=node.start_page, level=node.level))
        if node.children:
            _flatten_outline(
                node.children,
                redact=redact,
                block_text_by_id=block_text_by_id,
                heading_preview_by_page=heading_preview_by_page,
                out=out,
            )


class DocumentMapProjector:
    """Project ParsedDocumentIR into DocumentMap + PageTextView payloads."""

    target_id: TargetProjection = "document_map"

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> dict[str, Any]:
        """Return {'document_map': DocumentMap, 'page_text_views': list[PageTextView]}."""
        redact = redaction == "on"
        resolved_doc_id = doc_id or ir.provenance.binary_sha256[:16]
        resolved_filename = filename or Path(ir.provenance.source_uri_sanitized).name or "document"
        approved_list = sorted(set(approved_scan_pages))
        approved_set = set(approved_list)

        blocks_by_page: dict[int, list[DocumentBlock]] = {}
        for block in sorted(ir.blocks, key=lambda b: b.reading_order):
            p_num = block.spans[0].page_number
            blocks_by_page.setdefault(p_num, []).append(block)

        masked_pages = _mask_pages_map(ir) if redact else {}
        page_specs: list[PageMapSpec] = []
        text_views: list[PageTextView] = []
        for page in ir.pages:
            p_blocks = blocks_by_page.get(page.page_number, [])
            page_text = masked_pages[page.page_number] if redact else page.canonical_text
            headers: list[str] = []
            for blk in p_blocks:
                if blk.kind == "table" and blk.table_headers:
                    raw_headers = [h for h in blk.table_headers if h]
                    headers = mask_row_cells(raw_headers) if redact else raw_headers
                    if headers:
                        break
            if not headers:
                headers = _extract_table_headers_from_text(page_text, redact=redact)

            heading = ""
            for blk in p_blocks:
                if blk.kind == "heading" and blk.text.strip():
                    raw_heading = blk.text.strip()
                    heading = truncate_preserving_masked_pii(
                        mask_pii_text(raw_heading) if redact else raw_heading, 120
                    )
                    break
            if not heading:
                heading = _extract_heading_preview(page_text, redact=redact)

            page_specs.append(
                PageMapSpec(
                    page_number=page.page_number,
                    char_count=page.raw_text_length,
                    has_images=page.image_block_count > 0,
                    is_scanned_or_low_density=page.needs_ocr,
                    ocr_box_count=0,
                    table_headers=headers,
                    heading_preview=heading,
                    canonical_text_sha256=page.canonical_text_sha256,
                )
            )
            text_views.append(
                PageTextView(
                    doc_id=resolved_doc_id,
                    page_number=page.page_number,
                    masked_text=page_text,
                    is_scanned=page.needs_ocr,
                    scan_approved=page.page_number in approved_set,
                )
            )

        block_text_by_id = {b.block_id: b.text for b in ir.blocks}
        heading_preview_by_page = {s.page_number: s.heading_preview for s in page_specs}
        outline: list[OutlineNode] = []
        _flatten_outline(
            ir.outline,
            redact=redact,
            block_text_by_id=block_text_by_id,
            heading_preview_by_page=heading_preview_by_page,
            out=outline,
        )
        if not outline:
            for spec in page_specs:
                if spec.heading_preview:
                    outline.append(
                        OutlineNode(
                            title=spec.heading_preview,
                            page_start=spec.page_number,
                            level=1,
                        )
                    )

        doc_map = DocumentMap(
            doc_id=resolved_doc_id,
            filename=resolved_filename,
            format=ir.provenance.format_id,
            page_count=max(len(page_specs), 1),
            approved_scan_pages=approved_list,
            outline=outline,
            pages=page_specs,
        )
        return {"document_map": doc_map, "page_text_views": text_views}

    def stream_jsonl(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
    ) -> Iterator[str]:
        """Yield DocumentMap and PageTextView JSON lines."""
        projected = self.project(ir, redaction=redaction)
        doc_map: DocumentMap = projected["document_map"]
        views: list[PageTextView] = projected["page_text_views"]
        yield json.dumps(
            {"kind": "document_map", "payload": doc_map.model_dump(mode="json")}
        ) + "\n"
        for view in views:
            yield json.dumps(
                {"kind": "page_text_view", "payload": view.model_dump(mode="json")}
            ) + "\n"


__all__ = ["DocumentMapProjector"]
