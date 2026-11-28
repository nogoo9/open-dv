"""Layer-2 heading-scoped ChunkRecord projector with uniform DuckDB-safe JSONL output."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Final

from opendv.models.document_ir import (
    ChunkRecord,
    PageSlice,
    ParsedDocumentIR,
    RedactionMode,
    TargetProjection,
)
from opendv.models.json_hasher import dumps_jcs
from opendv.models.pii_tokenizer import mask_pii_text, table_has_aba_context
from opendv.parsing.parsers._helpers import split_oversized_text
from opendv.parsing.redaction import (
    format_table_markdown,
    mask_pipe_tables_in_text,
    mask_row_cells,
)

MAX_CHUNK_CHARS: Final[int] = 4_000


class ChunksProjector:
    """Project ParsedDocumentIR into <=4,000-char heading-scoped ChunkRecord entries."""

    target_id: TargetProjection = "chunks"

    def project(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
        doc_id: str | None = None,
        filename: str | None = None,
        approved_scan_pages: Sequence[int] = (),
    ) -> list[ChunkRecord]:
        """Project ParsedDocumentIR blocks into uniform-schema ChunkRecord objects."""
        _ = doc_id, filename, approved_scan_pages
        redact = redaction == "on"
        is_redacted = redact or ir.provenance.redacted
        page_by_num: dict[int, PageSlice] = {p.page_number: p for p in ir.pages}
        doc_prefix = ir.provenance.binary_sha256[:12]

        chunks: list[ChunkRecord] = []
        heading_stack: list[tuple[int, str]] = []
        ordered = sorted(ir.blocks, key=lambda b: b.reading_order)

        for block in ordered:
            if block.kind == "heading":
                lvl = max(1, min(6, block.heading_level or 1))
                while heading_stack and heading_stack[-1][0] >= lvl:
                    heading_stack.pop()
                title = (mask_pii_text(block.text) if redact else block.text).strip()
                heading_stack.append((lvl, title))

            heading_path = [t for _, t in heading_stack]
            span = block.spans[0]
            page = page_by_num.get(span.page_number)
            if page is None:
                continue

            bbox_json = (
                dumps_jcs(span.bbox.model_dump()) if span.bbox is not None else None
            )
            table_json: str | None = None
            t_headers: list[str] = []
            t_rows: list[list[str]] = []
            if block.kind == "table":
                if redact:
                    hdr_aba = table_has_aba_context(block.table_headers, block.table_rows)
                    t_headers = (
                        mask_row_cells(block.table_headers)
                        if block.table_headers is not None
                        else []
                    )
                    t_rows = (
                        [mask_row_cells(row, force_aba_ctx=hdr_aba) for row in block.table_rows]
                        if block.table_rows is not None
                        else []
                    )
                else:
                    t_headers = list(block.table_headers or [])
                    t_rows = [list(row) for row in (block.table_rows or [])]
                table_json = dumps_jcs({"headers": t_headers, "rows": t_rows})

            if redact:
                if block.kind == "table" and (t_headers or t_rows):
                    raw_or_masked = format_table_markdown(t_headers, t_rows)
                else:
                    raw_or_masked = mask_pii_text(mask_pipe_tables_in_text(block.text))
            else:
                raw_or_masked = block.text
            pieces = split_oversized_text(raw_or_masked, MAX_CHUNK_CHARS)
            preserve_offsets = (
                len(pieces) == 1
                and not redact
                and not ir.provenance.redacted
                and span.char_start is not None
                and span.char_end is not None
            )

            for sub_idx, piece in enumerate(pieces):
                chunks.append(
                    ChunkRecord(
                        chunk_id=f"{doc_prefix}:{block.block_id}:{sub_idx}",
                        doc_sha256=ir.provenance.binary_sha256,
                        format_id=ir.provenance.format_id,
                        page_number=span.page_number,
                        slice_kind=page.slice_kind,
                        canonical_text_sha256=page.canonical_text_sha256,
                        heading_path=list(heading_path),
                        block_id=block.block_id,
                        block_kind=block.kind,
                        reading_order=block.reading_order,
                        text=piece,
                        char_start=span.char_start if preserve_offsets else None,
                        char_end=span.char_end if preserve_offsets else None,
                        bbox_json=bbox_json,
                        table_json=table_json,
                        redacted=is_redacted,
                    )
                )

        if not ordered:
            for p_idx, page in enumerate(ir.pages):
                if not page.canonical_text.strip():
                    continue
                text_val = (
                    mask_pii_text(mask_pipe_tables_in_text(page.canonical_text))
                    if redact
                    else page.canonical_text
                )
                for sub_idx, piece in enumerate(split_oversized_text(text_val, MAX_CHUNK_CHARS)):
                    chunks.append(
                        ChunkRecord(
                            chunk_id=f"{doc_prefix}:p{page.page_number}_raw:{sub_idx}",
                            doc_sha256=ir.provenance.binary_sha256,
                            format_id=ir.provenance.format_id,
                            page_number=page.page_number,
                            slice_kind=page.slice_kind,
                            canonical_text_sha256=page.canonical_text_sha256,
                            heading_path=[],
                            block_id=f"p{page.page_number}_raw",
                            block_kind="paragraph",
                            reading_order=p_idx,
                            text=piece,
                            char_start=None,
                            char_end=None,
                            bbox_json=None,
                            table_json=None,
                            redacted=is_redacted,
                        )
                    )
        return chunks

    def stream_jsonl(
        self,
        ir: ParsedDocumentIR,
        *,
        redaction: RedactionMode = "off",
    ) -> Iterator[str]:
        """Yield uniform-key ChunkRecord JSON lines without exclude_none=True."""
        for chunk in self.project(ir, redaction=redaction):
            yield chunk.model_dump_json() + "\n"


__all__ = ["MAX_CHUNK_CHARS", "ChunksProjector"]
