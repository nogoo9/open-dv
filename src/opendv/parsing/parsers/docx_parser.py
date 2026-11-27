"""Pure Layer-2 DOCX format adapter with BoundedZipReader and DOM-order extraction."""

from __future__ import annotations

import importlib.metadata
import io
import time
import zipfile
from dataclasses import dataclass
from typing import Any

import docx
from docx.opc.exceptions import OpcError
from docx.oxml.ns import qn

from opendv.models.document_ir import (
    BlockKind,
    DocumentBlock,
    InspectResult,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    ParseOptions,
    SourceBlob,
    compute_canonical_text_sha256,
    normalize_page_text,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import table_has_aba_context
from opendv.parsing.parsers._helpers import (
    VIRTUAL_SLICE_MAX_CHARS,
    NonRenderableSliceParserMixin,
    finalize_virtual_ir,
    split_oversized_text,
)
from opendv.parsing.parsers._zip_guard import BoundedZipReader
from opendv.parsing.redaction import (
    format_table_markdown,
    mask_page_text_with_aba_tokens,
    mask_row_cells,
)

_TAG_P = qn("w:p")
_TAG_TBL = qn("w:tbl")
_TAG_SDT = qn("w:sdt")
_TAG_SDT_CONTENT = qn("w:sdtContent")
_TAG_TR = qn("w:tr")
_TAG_TR_PR = qn("w:trPr")
_TAG_TC = qn("w:tc")
_TAG_TC_PR = qn("w:tcPr")
_TAG_T = qn("w:t")
_TAG_TAB = qn("w:tab")
_TAG_BR = qn("w:br")
_TAG_CR = qn("w:cr")
_TAG_PPR = qn("w:pPr")
_TAG_RPR = qn("w:rPr")
_TAG_PSTYLE = qn("w:pStyle")
_TAG_NUMPR = qn("w:numPr")
_TAG_VAL = qn("w:val")
_TAG_GRID_SPAN = qn("w:gridSpan")
_TAG_VMERGE = qn("w:vMerge")
_TAG_GRID_BEFORE = qn("w:gridBefore")
_TAG_GRID_AFTER = qn("w:gridAfter")
_SKIP_TAGS = frozenset(
    {qn("w:del"), qn("w:instrText"), qn("w:delText"), _TAG_PPR, _TAG_RPR}
)


def _docx_version() -> str:
    try:
        return importlib.metadata.version("python-docx")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dataclass(slots=True)
class _DocxElement:
    kind: BlockKind
    text: str
    heading_level: int | None = None
    list_ordered: bool | None = None
    table_headers: list[str] | None = None
    table_rows: list[list[str]] | None = None


def _collect_runs_text(el: Any, parts: list[str]) -> None:
    tag = el.tag
    if tag in _SKIP_TAGS or (isinstance(tag, str) and tag.endswith("}Fallback")):
        return
    if tag == _TAG_T:
        if el.text:
            parts.append(el.text)
        return
    if tag == _TAG_TAB:
        parts.append("\t")
        return
    if tag in (_TAG_BR, _TAG_CR):
        parts.append("\n")
        return
    for child in el:
        _collect_runs_text(child, parts)


def _extract_p_text(p_el: Any) -> str:
    parts: list[str] = []
    _collect_runs_text(p_el, parts)
    return normalize_page_text("".join(parts)).strip()


def _parse_int_attr(el: Any, attr_name: str, default: int = 0) -> int:
    if el is None:
        return default
    raw = el.get(attr_name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _classify_paragraph(
    p_el: Any, style_names: dict[str, str]
) -> tuple[BlockKind, int | None, bool | None]:
    ppr = p_el.find(_TAG_PPR)
    if ppr is not None:
        pstyle = ppr.find(_TAG_PSTYLE)
        style_id = pstyle.get(_TAG_VAL) if pstyle is not None else None
        style_name = (style_names.get(style_id or "", style_id or "")).lower()
        if style_name.startswith("heading") or style_name == "title":
            digits = "".join(ch for ch in style_name if ch.isdigit())
            level = max(1, min(6, int(digits))) if digits else 1
            return "heading", level, None
        if ppr.find(_TAG_NUMPR) is not None or "list" in style_name:
            return "list", None, False
    return "paragraph", None, None


def _extract_cell_text_and_nested(
    tc_el: Any,
    style_names: dict[str, str],
    nested_elements: list[_DocxElement],
) -> str:
    para_texts: list[str] = []
    for child in tc_el:
        if child.tag == _TAG_P:
            txt = _extract_p_text(child)
            if txt:
                para_texts.append(txt)
        elif child.tag == _TAG_TBL:
            _walk_table(child, style_names, nested_elements)
        elif child.tag == _TAG_SDT:
            sdt_content = child.find(_TAG_SDT_CONTENT)
            if sdt_content is not None:
                for sub in sdt_content:
                    if sub.tag == _TAG_P:
                        txt = _extract_p_text(sub)
                        if txt:
                            para_texts.append(txt)
                    elif sub.tag == _TAG_TBL:
                        _walk_table(sub, style_names, nested_elements)
    return " ".join(para_texts).strip()


def _walk_table(
    tbl_el: Any,
    style_names: dict[str, str],
    elements: list[_DocxElement],
) -> None:
    raw_rows: list[list[str]] = []
    nested_tables: list[_DocxElement] = []

    for tr_el in tbl_el.findall(_TAG_TR):
        tr_pr = tr_el.find(_TAG_TR_PR)
        grid_before = _parse_int_attr(
            tr_pr.find(_TAG_GRID_BEFORE) if tr_pr is not None else None, _TAG_VAL, 0
        )
        grid_after = _parse_int_attr(
            tr_pr.find(_TAG_GRID_AFTER) if tr_pr is not None else None, _TAG_VAL, 0
        )
        row_cells: list[str] = [""] * max(0, grid_before)

        for tc_el in tr_el.findall(_TAG_TC):
            tc_pr = tc_el.find(_TAG_TC_PR)
            span = max(
                1,
                _parse_int_attr(
                    tc_pr.find(_TAG_GRID_SPAN) if tc_pr is not None else None, _TAG_VAL, 1
                ),
            )
            vmerge = tc_pr.find(_TAG_VMERGE) if tc_pr is not None else None
            is_vmerge_cont = vmerge is not None and vmerge.get(_TAG_VAL) != "restart"
            cell_text = (
                ""
                if is_vmerge_cont
                else _extract_cell_text_and_nested(tc_el, style_names, nested_tables)
            )
            if is_vmerge_cont:
                _extract_cell_text_and_nested(tc_el, style_names, nested_tables)
            row_cells.append(cell_text)
            if span > 1:
                row_cells.extend([""] * (span - 1))

        if grid_after > 0:
            row_cells.extend([""] * grid_after)
        if row_cells:
            raw_rows.append(row_cells)

    if raw_rows:
        width = max(len(r) for r in raw_rows)
        rect_rows = [r + [""] * (width - len(r)) for r in raw_rows]
        headers = rect_rows[0]
        body_rows = rect_rows[1:]
        gfm_lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(r) + " |" for r in body_rows),
        ]
        elements.append(
            _DocxElement(
                kind="table",
                text=normalize_page_text("\n".join(gfm_lines)),
                table_headers=headers,
                table_rows=body_rows,
            )
        )
    elements.extend(nested_tables)


def _walk_body_container(
    container: Any,
    style_names: dict[str, str],
    elements: list[_DocxElement],
) -> None:
    for child in container:
        if child.tag == _TAG_P:
            txt = _extract_p_text(child)
            if not txt:
                continue
            kind, lvl, ordered = _classify_paragraph(child, style_names)
            elements.append(
                _DocxElement(kind=kind, text=txt, heading_level=lvl, list_ordered=ordered)
            )
        elif child.tag == _TAG_TBL:
            _walk_table(child, style_names, elements)
        elif child.tag == _TAG_SDT:
            sdt_content = child.find(_TAG_SDT_CONTENT)
            if sdt_content is not None:
                _walk_body_container(sdt_content, style_names, elements)


def _load_validated_docx(blob: SourceBlob) -> list[_DocxElement]:
    entries = BoundedZipReader().read_validated(blob.path)
    clean_buf = io.BytesIO()
    with zipfile.ZipFile(clean_buf, "w", compression=zipfile.ZIP_STORED) as zf_out:
        for name, data in entries.items():
            zf_out.writestr(name, data)
    clean_buf.seek(0)
    try:
        doc = docx.Document(clean_buf)
        style_names = {
            s.style_id: s.name
            for s in doc.styles
            if getattr(s, "style_id", None) and getattr(s, "name", None)
        }
        elements: list[_DocxElement] = []
        _walk_body_container(doc.element.body, style_names, elements)
    except (
        OpcError,
        KeyError,
        AttributeError,
        ValueError,
        TypeError,
        SyntaxError,
        zipfile.BadZipFile,
        OSError,
    ) as exc:
        raise OpenDVError(
            f"Invalid DOCX document '{blob.uri_sanitized}': {exc}",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
        ) from exc
    return elements


def _partition_elements_to_slices(
    elements: list[_DocxElement],
    *,
    redact: bool,
) -> tuple[list[PageSlice], list[DocumentBlock], list[tuple[int, str, int, str]]]:
    normalized_elements: list[_DocxElement] = []
    for el in elements:
        if redact:
            if el.kind == "table":
                hdr_aba = table_has_aba_context(el.table_headers, el.table_rows)
                masked_headers = (
                    mask_row_cells(el.table_headers)
                    if el.table_headers is not None
                    else None
                )
                masked_rows = (
                    [mask_row_cells(row, force_aba_ctx=hdr_aba) for row in el.table_rows]
                    if el.table_rows is not None
                    else None
                )
                raw_masked_text = (
                    format_table_markdown(masked_headers, masked_rows)
                    if (masked_headers or masked_rows)
                    else mask_page_text_with_aba_tokens(el.text)
                )
            else:
                masked_headers = None
                masked_rows = None
                raw_masked_text = mask_page_text_with_aba_tokens(el.text)
            normalized_elements.append(
                _DocxElement(
                    kind=el.kind,
                    text=normalize_page_text(raw_masked_text),
                    heading_level=el.heading_level,
                    list_ordered=el.list_ordered,
                    table_headers=masked_headers,
                    table_rows=masked_rows,
                )
            )
        else:
            norm_text = normalize_page_text(el.text)
            normalized_elements.append(
                el
                if norm_text == el.text
                else _DocxElement(
                    kind=el.kind,
                    text=norm_text,
                    heading_level=el.heading_level,
                    list_ordered=el.list_ordered,
                    table_headers=el.table_headers,
                    table_rows=el.table_rows,
                )
            )
    elements = normalized_elements

    if not elements:
        empty_sha = compute_canonical_text_sha256("")
        return (
            [
                PageSlice(
                    page_number=1,
                    slice_kind="virtual_section_page",
                    renderable=False,
                    canonical_text="",
                    canonical_text_sha256=empty_sha,
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

    slice_entries: list[list[tuple[_DocxElement, str, int | None, list[PageSpan]]]] = [[]]
    slice_texts: list[str] = [""]
    block_records: list[tuple[_DocxElement, int, list[PageSpan]]] = []

    for b_idx, el in enumerate(elements):
        pieces = split_oversized_text(el.text, VIRTUAL_SLICE_MAX_CHARS)
        spans: list[PageSpan] = []
        for piece in pieces:
            cur_text = slice_texts[-1]
            sep = "\n" if cur_text else ""
            if len(cur_text) + len(sep) + len(piece) > VIRTUAL_SLICE_MAX_CHARS and cur_text:
                slice_entries.append([])
                slice_texts.append("")
                cur_text = ""
                sep = ""
            start = len(cur_text) + len(sep)
            new_text = cur_text + sep + piece
            slice_texts[-1] = new_text
            page_num = len(slice_texts)
            spans.append(
                PageSpan(
                    page_number=page_num,
                    char_start=start,
                    char_end=start + len(piece),
                    bbox=None,
                )
            )
        block_records.append((el, b_idx, spans))

    pages: list[PageSlice] = []
    for idx, raw_slice in enumerate(slice_texts, start=1):
        norm = normalize_page_text(raw_slice)
        non_ws = sum(1 for ch in norm if not ch.isspace())
        pages.append(
            PageSlice(
                page_number=idx,
                slice_kind="virtual_section_page",
                renderable=False,
                canonical_text=norm,
                canonical_text_sha256=compute_canonical_text_sha256(norm),
                raw_text_length=len(norm),
                non_ws_chars=non_ws,
                image_block_count=0,
                needs_ocr=False,
                extracted_via="native_text",
            )
        )

    blocks: list[DocumentBlock] = []
    headings: list[tuple[int, str, int, str]] = []
    for el, b_idx, spans in block_records:
        block_id = f"docx_b{b_idx}"
        blocks.append(
            DocumentBlock(
                block_id=block_id,
                kind=el.kind,
                reading_order=b_idx,
                spans=spans,
                heading_level=el.heading_level,
                text=el.text,
                list_ordered=el.list_ordered,
                table_headers=el.table_headers,
                table_rows=el.table_rows,
            )
        )
        if el.kind == "heading":
            headings.append((el.heading_level or 1, el.text, spans[0].page_number, block_id))
    return pages, blocks, headings


class DocxParser(NonRenderableSliceParserMixin):
    """Pure Layer-2 FormatParser for .docx OOXML archives."""

    parser_id: str = "python_docx"
    parser_version: str = _docx_version()

    def inspect(self, blob: SourceBlob, opts: ParseOptions) -> InspectResult:
        """Validate .docx zip safety and report virtual section slice metadata."""
        _ = opts
        elements = _load_validated_docx(blob)
        pages, _, _ = _partition_elements_to_slices(elements, redact=False)
        return InspectResult(
            source_uri_sanitized=blob.uri_sanitized,
            binary_sha256=blob.binary_sha256,
            byte_size=blob.byte_size,
            format_id="docx",
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
        elements = _load_validated_docx(blob)
        all_pages, all_blocks, headings = _partition_elements_to_slices(elements, redact=redact)
        return finalize_virtual_ir(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            blob=blob,
            format_id="docx",
            opts=opts,
            started_at=started,
            redact=redact,
            all_pages=all_pages,
            all_blocks=all_blocks,
            headings=headings,
        )


__all__ = ["DocxParser"]
