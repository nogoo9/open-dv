"""Layer-2 deterministic GFM Markdown projector for ParsedDocumentIR."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from opendv.models.document_ir import (
    DocumentBlock,
    ParsedDocumentIR,
    RedactionMode,
    TargetProjection,
)
from opendv.models.pii_tokenizer import mask_pii_text, table_has_aba_context
from opendv.parsing.redaction import mask_pipe_tables_in_text, mask_row_cells


def _render_block_markdown(block: DocumentBlock, *, redact: bool) -> str:
    def _m(value: str) -> str:
        return mask_pii_text(mask_pipe_tables_in_text(value)) if redact else value

    if block.kind == "heading":
        lvl = max(1, min(6, block.heading_level or 1))
        return f"{'#' * lvl} {_m(block.text).strip()}"

    if block.kind == "list":
        body = _m(block.text).strip()
        if block.list_ordered:
            return body if body[:2].isdigit() else f"1. {body}"
        return body if body.startswith(("- ", "* ", "+ ")) else f"- {body}"

    if block.kind == "table":
        raw_headers = block.table_headers or []
        raw_rows = block.table_rows or []
        if redact:
            hdr_aba = table_has_aba_context(raw_headers, raw_rows)
            headers = mask_row_cells(raw_headers)
            rows = [mask_row_cells(row, force_aba_ctx=hdr_aba) for row in raw_rows]
        else:
            headers = list(raw_headers)
            rows = [list(row) for row in raw_rows]
        if not headers and not rows:
            return _m(block.text).strip()
        col_count = max([len(headers), *(len(r) for r in rows)], default=0)
        if col_count == 0:
            return _m(block.text).strip()
        padded_headers = headers + [""] * (col_count - len(headers))
        lines = [
            "| " + " | ".join(padded_headers) + " |",
            "| " + " | ".join("---" for _ in range(col_count)) + " |",
        ]
        for row in rows:
            padded_row = row + [""] * (col_count - len(row))
            lines.append("| " + " | ".join(padded_row) + " |")
        return "\n".join(lines)

    if block.kind == "figure":
        caption = _m(block.caption or block.text).strip()
        return f"![{caption}](figure)" if caption else "![figure](figure)"

    return _m(block.text).strip()


class MarkdownProjector:
    """Project ParsedDocumentIR blocks in reading order into deterministic GFM Markdown."""

    target_id: TargetProjection = "markdown"

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> str:
        """Render ordered Markdown string across all ParsedDocumentIR blocks."""
        _ = doc_id, filename, approved_scan_pages
        redact = redaction == "on"
        ordered = sorted(ir.blocks, key=lambda b: b.reading_order)
        if ordered:
            rendered = [_render_block_markdown(blk, redact=redact) for blk in ordered]
            return "\n\n".join(part for part in rendered if part)
        fallback = [
            (
                mask_pii_text(mask_pipe_tables_in_text(p.canonical_text))
                if redact
                else p.canonical_text
            ).strip()
            for p in ir.pages
        ]
        return "\n\n".join(part for part in fallback if part)

    def stream_jsonl(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
    ) -> Iterator[str]:
        """Yield per-page Markdown JSON lines."""
        redact = redaction == "on"
        ordered = sorted(ir.blocks, key=lambda b: b.reading_order)
        by_page: dict[int, list[str]] = {}
        for blk in ordered:
            p_num = blk.spans[0].page_number
            md = _render_block_markdown(blk, redact=redact)
            if md:
                by_page.setdefault(p_num, []).append(md)
        for page in ir.pages:
            parts = by_page.get(page.page_number)
            md_text = (
                "\n\n".join(parts)
                if parts
                else (
                    mask_pii_text(mask_pipe_tables_in_text(page.canonical_text))
                    if redact
                    else page.canonical_text
                ).strip()
            )
            rec: dict[str, Any] = {"page_number": page.page_number, "markdown": md_text}
            yield json.dumps(rec) + "\n"


__all__ = ["MarkdownProjector"]
