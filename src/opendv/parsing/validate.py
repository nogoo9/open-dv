"""Layer-2 structural integrity validator for ParsedDocumentIR."""

from __future__ import annotations

from opendv.models.document_ir import (
    DocumentOutlineNode,
    ParsedDocumentIR,
    ValidationDiagnostic,
    compute_canonical_text_sha256,
)


def _walk_outline_checks(
    nodes: list[DocumentOutlineNode],
    pointer_prefix: str,
    page_numbers: set[int],
    block_ids: set[str],
    diagnostics: list[ValidationDiagnostic],
) -> None:
    for idx, node in enumerate(nodes):
        ptr = f"{pointer_prefix}/{idx}"
        if page_numbers and (
            node.start_page not in page_numbers or node.end_page not in page_numbers
        ):
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="OUTLINE_PAGE_OUT_OF_RANGE",
                    json_pointer=ptr,
                    message=(
                        f"Outline node '{node.node_id}' references pages "
                        f"{node.start_page}..{node.end_page} outside parsed pages."
                    ),
                    suggested_fix="Align outline node page range with ParsedDocumentIR.pages[].",
                )
            )
        if node.block_id is not None and node.block_id not in block_ids:
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="OUTLINE_BLOCK_MISSING",
                    json_pointer=f"{ptr}/block_id",
                    message=(
                        f"Outline node '{node.node_id}' references unknown block_id "
                        f"'{node.block_id}'."
                    ),
                    suggested_fix="Ensure outline block_id exists in ParsedDocumentIR.blocks[].",
                )
            )
        if node.children:
            _walk_outline_checks(
                node.children,
                f"{ptr}/children",
                page_numbers,
                block_ids,
                diagnostics,
            )


def validate_ir(ir: ParsedDocumentIR) -> list[ValidationDiagnostic]:
    """Verify canonical digests, span bounds, reading order, and outline integrity."""
    diagnostics: list[ValidationDiagnostic] = []

    if ir.provenance.page_count != len(ir.pages):
        diagnostics.append(
            ValidationDiagnostic(
                severity="error",
                code="PROVENANCE_PAGE_COUNT_MISMATCH",
                json_pointer="/provenance/page_count",
                message=(
                    f"provenance.page_count={ir.provenance.page_count} does not match "
                    f"len(pages)={len(ir.pages)}."
                ),
                suggested_fix="Set provenance.page_count equal to len(pages).",
            )
        )

    page_map: dict[int, int] = {}
    for idx, page in enumerate(ir.pages):
        if page.page_number in page_map:
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="DUPLICATE_PAGE_NUMBER",
                    json_pointer=f"/pages/{idx}/page_number",
                    message=f"Duplicate PageSlice page_number={page.page_number}.",
                    suggested_fix="Ensure every PageSlice has a unique 1-based page_number.",
                )
            )
        page_map[page.page_number] = len(page.canonical_text)

        expected_sha = compute_canonical_text_sha256(page.canonical_text)
        if page.canonical_text_sha256 != expected_sha:
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="CANONICAL_HASH_MISMATCH",
                    json_pointer=f"/pages/{idx}/canonical_text_sha256",
                    message=(
                        f"Page {page.page_number} canonical_text_sha256 mismatch: "
                        f"expected {expected_sha}, got {page.canonical_text_sha256}."
                    ),
                    suggested_fix=(
                        "Recompute canonical_text_sha256 via compute_canonical_text_sha256()."
                    ),
                )
            )

        if page.raw_text_length != len(page.canonical_text):
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="PAGE_LENGTH_MISMATCH",
                    json_pointer=f"/pages/{idx}/raw_text_length",
                    message=(
                        f"Page {page.page_number} raw_text_length={page.raw_text_length} "
                        f"does not match len(canonical_text)={len(page.canonical_text)}."
                    ),
                    suggested_fix="Update raw_text_length to len(canonical_text).",
                )
            )

    seen_block_ids: set[str] = set()
    prev_order = -1
    for b_idx, block in enumerate(ir.blocks):
        if block.block_id in seen_block_ids:
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="DUPLICATE_BLOCK_ID",
                    json_pointer=f"/blocks/{b_idx}/block_id",
                    message=f"Duplicate block_id '{block.block_id}'.",
                    suggested_fix="Assign unique block_id values per DocumentBlock.",
                )
            )
        seen_block_ids.add(block.block_id)

        if block.reading_order < prev_order:
            diagnostics.append(
                ValidationDiagnostic(
                    severity="error",
                    code="READING_ORDER_NON_MONOTONIC",
                    json_pointer=f"/blocks/{b_idx}/reading_order",
                    message=(
                        f"Block '{block.block_id}' reading_order={block.reading_order} "
                        f"is less than previous {prev_order}."
                    ),
                    suggested_fix="Sort blocks in non-decreasing reading_order.",
                )
            )
        prev_order = block.reading_order

        for s_idx, span in enumerate(block.spans):
            if span.page_number not in page_map:
                diagnostics.append(
                    ValidationDiagnostic(
                        severity="error",
                        code="SPAN_PAGE_OUT_OF_RANGE",
                        json_pointer=f"/blocks/{b_idx}/spans/{s_idx}/page_number",
                        message=(
                            f"Block '{block.block_id}' span references missing page_number="
                            f"{span.page_number}."
                        ),
                        suggested_fix="Ensure span.page_number exists in pages[].",
                    )
                )
                continue
            page_len = page_map[span.page_number]
            if (
                span.char_start is not None
                and span.char_end is not None
                and (span.char_end > page_len or span.char_start > span.char_end)
            ):
                diagnostics.append(
                    ValidationDiagnostic(
                        severity="error",
                        code="SPAN_BOUNDS_INVALID",
                        json_pointer=f"/blocks/{b_idx}/spans/{s_idx}",
                        message=(
                            f"Block '{block.block_id}' char span "
                            f"[{span.char_start}, {span.char_end}) "
                            f"exceeds page {span.page_number} length {page_len}."
                        ),
                        suggested_fix=(
                            "Clamp span offsets within [0, len(canonical_text)] or set None."
                        ),
                    )
                )

    _walk_outline_checks(
        ir.outline,
        "/outline",
        set(page_map),
        seen_block_ids,
        diagnostics,
    )
    return diagnostics


__all__ = ["validate_ir"]
