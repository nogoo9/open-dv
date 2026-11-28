"""Layer-2 Pillow pixel redaction, span-to-word-box PII alignment, and optional [ocr]."""

from __future__ import annotations

import importlib
import importlib.util
import io
import math
import re
import shutil
from collections.abc import Mapping, Sequence

from PIL import Image, ImageDraw

from opendv.models.document_ir import (
    DocumentBlock,
    DocumentOutlineNode,
    OcrBox,
    PageSlice,
    PageSpan,
    ParsedDocumentIR,
    compute_canonical_text_sha256,
)
from opendv.models.errors import (
    ErrorCode,
    ErrorRemediation,
    OpenDVError,
    RemediationAction,
)
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    RE_ABA_CONTEXT,
    RE_ABA_DIGITS,
    RE_CC_PAN,
    RE_EIN,
    RE_EMAIL,
    RE_HEX_64,
    RE_IBAN,
    RE_PHONE,
    RE_SSN,
    find_high_entropy_tokens,
    find_pipe_table_aba_spans,
    is_non_credential_digest_column,
    is_valid_aba_routing,
    is_valid_luhn,
    mask_pii_text,
    mask_pipe_tables_in_text,
    table_has_aba_context,
    truncate_preserving_masked_pii,
)
from opendv.parsing._protocols import NormalizedWordBox

_TOKEN_STRIP_CHARS = ",;()[]{}'\"<>"
_WS_STRIP_RE = re.compile(r"\s+")


def format_table_markdown(
    headers: Sequence[str] | None,
    rows: Sequence[Sequence[str]] | None,
) -> str:
    """Render rectangular GFM pipe table."""
    hdrs, body = list(headers or ()), [list(r) for r in (rows or ())]
    if not hdrs and not body:
        return ""
    cols = max([len(hdrs), *(len(r) for r in body)], default=0)

    def _row(cells: list[str]) -> str:
        return "| " + " | ".join(cells + [""] * (cols - len(cells))) + " |"

    lines = [_row(hdrs), "| " + " | ".join(["---"] * cols) + " |"] if hdrs else []
    lines.extend(_row(r) for r in body)
    return "\n".join(lines)


def collect_table_aba_tokens_by_page(blocks: Sequence[DocumentBlock]) -> dict[int, set[str]]:
    """Collect valid ABA digits in table blocks with header/row ABA context."""
    by_page: dict[int, set[str]] = {}
    for blk in blocks:
        if blk.kind != "table":
            continue
        hdr_aba = table_has_aba_context(blk.table_headers, blk.table_rows)
        found: set[str] = set()
        rows = ([blk.table_headers] if blk.table_headers else []) + list(blk.table_rows or ())
        for r_idx, row in enumerate(rows):
            has_ctx = (hdr_aba and (r_idx > 0 or not blk.table_headers)) or bool(
                RE_ABA_CONTEXT.search(" ".join(row))
            )
            if has_ctx:
                for cell in row:
                    found.update(
                        m.group(0)
                        for m in RE_ABA_DIGITS.finditer(cell)
                        if is_valid_aba_routing(m.group(0))
                    )
        if found:
            for sp in blk.spans:
                by_page.setdefault(sp.page_number, set()).update(found)
    return by_page


def mask_page_text_with_aba_tokens(
    text: str, aba_tokens: set[str] | frozenset[str] | None = None
) -> str:
    """Mask pipe tables, table-context ABA digits, and standard PII in text."""
    if not text:
        return text
    masked = mask_pipe_tables_in_text(text)
    if aba_tokens:
        masked = RE_ABA_DIGITS.sub(
            lambda m: MASKED_PII_TOKEN if m.group(0) in aba_tokens else m.group(0), masked
        )
    return mask_pii_text(masked)


def _merge_spans(raw_spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not raw_spans:
        return []
    raw_spans.sort()
    merged: list[tuple[int, int]] = [raw_spans[0]]
    for start, end in raw_spans[1:]:
        prev_s, prev_e = merged[-1]
        if start <= prev_e:
            merged[-1] = (prev_s, max(prev_e, end))
        else:
            merged.append((start, end))
    return merged


def find_pii_spans(text: str, *, col_name: str | None = None) -> list[tuple[int, int]]:
    """Return sorted, merged [start, end) character spans of PII/secrets in text."""
    if not text:
        return []
    raw_spans: list[tuple[int, int]] = []

    for match in RE_ABA_DIGITS.finditer(text):
        if is_valid_aba_routing(match.group(0)):
            ctx_start = max(0, match.start() - 32)
            ctx_end = min(len(text), match.end() + 32)
            if RE_ABA_CONTEXT.search(text[ctx_start:ctx_end]):
                raw_spans.append((match.start(), match.end()))
    raw_spans.extend(find_pipe_table_aba_spans(text))

    for pattern in (RE_SSN, RE_EIN, RE_EMAIL, RE_PHONE, RE_IBAN):
        for match in pattern.finditer(text):
            raw_spans.append((match.start(), match.end()))

    for match in RE_CC_PAN.finditer(text):
        if is_valid_luhn(match.group(0)):
            raw_spans.append((match.start(), match.end()))

    if not is_non_credential_digest_column(col_name):
        for match in RE_HEX_64.finditer(text):
            raw_spans.append((match.start(), match.end()))
        secret_tokens = set(find_high_entropy_tokens(text))
        if secret_tokens:
            for match in re.finditer(r"\S+", text):
                raw_tok = match.group(0)
                stripped = raw_tok.strip(_TOKEN_STRIP_CHARS)
                if stripped in secret_tokens:
                    lead = len(raw_tok) - len(raw_tok.lstrip(_TOKEN_STRIP_CHARS))
                    s_idx = match.start() + lead
                    raw_spans.append((s_idx, s_idx + len(stripped)))

    return _merge_spans(raw_spans)


def _find_compact_windows(
    target_compact: str,
    word_boxes: Sequence[NormalizedWordBox],
) -> list[tuple[int, int]]:
    """Find minimal contiguous [start_box, end_box) windows matching compact PII text."""
    if not target_compact:
        return []
    windows: list[tuple[int, int]] = []
    n = len(word_boxes)
    i = 0
    while i < n:
        acc = ""
        matched_end: int | None = None
        for j in range(i, n):
            acc += _WS_STRIP_RE.sub("", word_boxes[j].text)
            if target_compact in acc:
                matched_end = j + 1
                break
            if len(acc) > len(target_compact) + 32:
                break
        if matched_end is not None:
            # Trim leading boxes that are unnecessary for the suffix match
            start_i = i
            while start_i + 1 < matched_end:
                tail = "".join(
                    _WS_STRIP_RE.sub("", word_boxes[k].text)
                    for k in range(start_i + 1, matched_end)
                )
                if target_compact in tail:
                    start_i += 1
                else:
                    break
            windows.append((start_i, matched_end))
            i = matched_end
        else:
            i += 1
    return windows


def align_pii_spans_to_boxes(
    page_text: str,
    word_boxes: Sequence[NormalizedWordBox],
    *,
    aba_tokens: set[str] | frozenset[str] | None = None,
) -> list[NormalizedWordBox]:
    """Align PII spans in page_text/word_boxes to NormalizedWordBox entries; fail closed on miss."""
    page_spans = find_pii_spans(page_text)
    if aba_tokens and page_text:
        extra_spans = [
            (m.start(), m.end())
            for m in RE_ABA_DIGITS.finditer(page_text)
            if m.group(0) in aba_tokens
        ]
        if extra_spans:
            page_spans = _merge_spans([*page_spans, *extra_spans])
    has_masked_diff = bool(aba_tokens) or (
        bool(page_text) and mask_pii_text(page_text) != page_text
    )

    if not word_boxes:
        if page_spans or has_masked_diff:
            raise OpenDVError(
                "Detected PII on page with zero word bounding boxes; cannot safely redact.",
                error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
            )
        return []

    parts: list[str] = []
    wb_ranges: list[tuple[int, int, int]] = []
    cursor = 0
    for idx, wb in enumerate(word_boxes):
        if idx > 0:
            parts.append(" ")
            cursor += 1
        start = cursor
        parts.append(wb.text)
        cursor += len(wb.text)
        wb_ranges.append((start, cursor, idx))
    wb_joined = "".join(parts)

    matched_indices: set[int] = set()
    for js, je in find_pii_spans(wb_joined):
        for bs, be, idx in wb_ranges:
            if bs < je and be > js:
                matched_indices.add(idx)

    compact_counts: dict[str, int] = {}
    for ps, pe in page_spans:
        compact = _WS_STRIP_RE.sub("", page_text[ps:pe])
        if compact:
            compact_counts[compact] = compact_counts.get(compact, 0) + 1
    for tok in aba_tokens or ():
        compact_counts.setdefault(tok, 1)

    for compact, needed_count in compact_counts.items():
        windows = _find_compact_windows(compact, word_boxes)
        if len(windows) < needed_count:
            raise OpenDVError(
                "Detected PII span failed word-box alignment; refusing partial image redaction.",
                error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
            )
        for ws, we in windows:
            for idx in range(ws, we):
                matched_indices.add(idx)

    if (page_spans or has_masked_diff) and not matched_indices:
        raise OpenDVError(
            "Detected PII on page matched zero word bounding boxes.",
            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
        )

    return [word_boxes[idx] for idx in sorted(matched_indices)]


def redact_png_boxes(
    png_bytes: bytes,
    boxes: Sequence[OcrBox | NormalizedWordBox],
    *,
    padding_px: int = 0,
) -> bytes:
    """Draw opaque black rectangles over normalized or pixel boxes in a PNG image."""
    with Image.open(io.BytesIO(png_bytes)) as src_img:
        img = src_img.convert("RGBA")
    width, height = img.size
    draw = ImageDraw.Draw(img)
    pad = max(0, padding_px)

    for box in boxes:
        if isinstance(box, NormalizedWordBox):
            x0 = max(0, math.floor(box.bbox.x0 * width) - pad)
            y0 = max(0, math.floor(box.bbox.y0 * height) - pad)
            x1 = min(width, math.ceil(box.bbox.x1 * width) + pad)
            y1 = min(height, math.ceil(box.bbox.y1 * height) + pad)
        else:
            x0 = max(0, min(width, box.x0 - pad))
            y0 = max(0, min(height, box.y0 - pad))
            x1 = max(0, min(width, box.x1 + pad))
            y1 = max(0, min(height, box.y1 + pad))
        if x1 > x0 and y1 > y0:
            draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def is_ocr_driver_available() -> bool:
    """Return True iff optional pytesseract package and system tesseract binary are present."""
    return (
        importlib.util.find_spec("pytesseract") is not None
        and shutil.which("tesseract") is not None
    )


def ensure_ocr_driver_available() -> None:
    """Raise DRIVER_MISSING with actionable agent/human remediation if OCR driver is absent."""
    if importlib.util.find_spec("pytesseract") is None:
        raise OpenDVError(
            "Optional [ocr] dependency 'pytesseract' is not installed.",
            error_code=ErrorCode.DRIVER_MISSING,
            remediation=ErrorRemediation(
                summary="Install optional 'ocr' extra required for OCR processing.",
                actions=[
                    RemediationAction(
                        actor="agent",
                        label="Install optional OCR extra",
                        command="uv sync --extra ocr",
                        requires_tty=False,
                    )
                ],
            ),
        )
    if shutil.which("tesseract") is None:
        raise OpenDVError(
            "System 'tesseract' binary was not found on PATH.",
            error_code=ErrorCode.DRIVER_MISSING,
            remediation=ErrorRemediation(
                summary="Install system Tesseract OCR binary and ensure 'tesseract' is on PATH.",
                actions=[
                    RemediationAction(
                        actor="human",
                        label="Install Tesseract OCR binary on PATH",
                        command=None,
                        requires_tty=True,
                    )
                ],
            ),
        )


def run_pytesseract_ocr(png_bytes: bytes) -> tuple[list[OcrBox], str]:
    """Run pytesseract.image_to_data on PNG bytes, failing loudly with DRIVER_MISSING if absent."""
    ensure_ocr_driver_available()
    pytesseract = importlib.import_module("pytesseract")
    with Image.open(io.BytesIO(png_bytes)) as img:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

    boxes: list[OcrBox] = []
    words: list[str] = []
    raw_texts = data.get("text", [])
    raw_confs = data.get("conf", [])
    raw_lefts = data.get("left", [])
    raw_tops = data.get("top", [])
    raw_widths = data.get("width", [])
    raw_heights = data.get("height", [])
    count = min(
        len(raw_texts),
        len(raw_confs),
        len(raw_lefts),
        len(raw_tops),
        len(raw_widths),
        len(raw_heights),
    )
    for idx in range(count):
        token = str(raw_texts[idx]).strip()
        if not token:
            continue
        try:
            score = float(raw_confs[idx])
        except (TypeError, ValueError):
            score = -1.0
        if score >= 0:
            x0 = int(raw_lefts[idx])
            y0 = int(raw_tops[idx])
            boxes.append(
                OcrBox(x0, y0, x0 + int(raw_widths[idx]), y0 + int(raw_heights[idx]), token)
            )
            words.append(token)
    return boxes, " ".join(words)


def mask_row_cells(cells: Sequence[str], *, force_aba_ctx: bool = False) -> list[str]:
    """Mask PII in table cells with row-level ABA routing context."""
    if not cells:
        return []
    has_aba_ctx = force_aba_ctx or bool(RE_ABA_CONTEXT.search(" ".join(cells)))
    out: list[str] = []
    for cell in cells:
        masked = mask_pii_text(cell)
        if has_aba_ctx:
            masked = RE_ABA_DIGITS.sub(
                lambda m: MASKED_PII_TOKEN if is_valid_aba_routing(m.group(0)) else m.group(0),
                masked,
            )
        out.append(masked)
    return out


def _first_heading_preview(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return truncate_preserving_masked_pii(cleaned, 120)
    return ""


def _redact_outline_nodes(
    nodes: Sequence[DocumentOutlineNode],
    *,
    page_text_map: Mapping[int, str],
    block_text_by_id: Mapping[str, str],
) -> list[DocumentOutlineNode]:
    redacted: list[DocumentOutlineNode] = []
    for node in nodes:
        if (
            node.node_id.startswith("out_p")
            and node.block_id is None
            and node.start_page in page_text_map
        ):
            title = _first_heading_preview(page_text_map[node.start_page]) or mask_pii_text(
                node.title
            )
        elif node.block_id and node.block_id in block_text_by_id:
            title = truncate_preserving_masked_pii(block_text_by_id[node.block_id].strip(), 240)
        else:
            title = mask_pii_text(node.title)
        redacted.append(
            node.model_copy(
                update={
                    "title": title,
                    "children": _redact_outline_nodes(
                        node.children,
                        page_text_map=page_text_map,
                        block_text_by_id=block_text_by_id,
                    ),
                }
            )
        )
    return redacted


def redact_ir(ir: ParsedDocumentIR) -> ParsedDocumentIR:
    """Return a non-destructive PII-redacted copy of ParsedDocumentIR."""
    aba_tokens_by_page = collect_table_aba_tokens_by_page(ir.blocks)
    new_pages: list[PageSlice] = []
    page_text_map: dict[int, str] = {}
    for page in ir.pages:
        masked = mask_page_text_with_aba_tokens(
            page.canonical_text, aba_tokens_by_page.get(page.page_number)
        )
        page_text_map[page.page_number] = masked
        non_ws = sum(1 for ch in masked if not ch.isspace())
        new_pages.append(
            page.model_copy(
                update={
                    "canonical_text": masked,
                    "canonical_text_sha256": compute_canonical_text_sha256(masked),
                    "raw_text_length": len(masked),
                    "non_ws_chars": non_ws,
                }
            )
        )

    new_blocks: list[DocumentBlock] = []
    for block in ir.blocks:
        hdr_aba = table_has_aba_context(block.table_headers, block.table_rows)
        masked_headers = (
            mask_row_cells(block.table_headers)
            if block.table_headers is not None
            else None
        )
        masked_rows = (
            [mask_row_cells(row, force_aba_ctx=hdr_aba) for row in block.table_rows]
            if block.table_rows is not None
            else None
        )
        if block.kind == "table" and (masked_headers or masked_rows):
            masked_text = format_table_markdown(masked_headers, masked_rows)
        else:
            masked_text = mask_pii_text(mask_pipe_tables_in_text(block.text))
        masked_caption = mask_pii_text(block.caption) if block.caption is not None else None
        new_spans: list[PageSpan] = []
        for span in block.spans:
            if span.char_start is not None and span.char_end is not None:
                p_txt = page_text_map.get(span.page_number, "")
                if (
                    span.char_end <= len(p_txt)
                    and p_txt[span.char_start : span.char_end] == masked_text
                ):
                    new_spans.append(span)
                else:
                    new_spans.append(
                        PageSpan(
                            page_number=span.page_number,
                            char_start=None,
                            char_end=None,
                            bbox=span.bbox,
                        )
                    )
            else:
                new_spans.append(span)
        new_blocks.append(
            block.model_copy(
                update={
                    "text": masked_text,
                    "table_headers": masked_headers,
                    "table_rows": masked_rows,
                    "caption": masked_caption,
                    "spans": new_spans,
                }
            )
        )

    block_text_by_id = {b.block_id: b.text for b in new_blocks}
    new_prov = ir.provenance.model_copy(update={"redacted": True})
    return ir.model_copy(
        update={
            "provenance": new_prov,
            "pages": new_pages,
            "outline": _redact_outline_nodes(
                ir.outline,
                page_text_map=page_text_map,
                block_text_by_id=block_text_by_id,
            ),
            "blocks": new_blocks,
        }
    )


__all__ = [
    "align_pii_spans_to_boxes",
    "collect_table_aba_tokens_by_page",
    "ensure_ocr_driver_available",
    "find_pii_spans",
    "find_pipe_table_aba_spans",
    "format_table_markdown",
    "is_ocr_driver_available",
    "mask_page_text_with_aba_tokens",
    "mask_pipe_tables_in_text",
    "mask_row_cells",
    "redact_ir",
    "redact_png_boxes",
    "run_pytesseract_ocr",
]
