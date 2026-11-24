"""Stateless citation span resolver, OCR receipt fallback, and redacted placeholder guard."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import OcrPageReceipt
from opendv.models.sources import DocumentMap, PageMapSpec
from opendv.models.wire_dtos_discovery import CitationResolveReq, CitationSpanResp
from opendv.sources.document_map import compute_canonical_text_sha256, normalize_page_text

_REDACTED_LEAK_RE: Final[re.Pattern[str]] = re.compile(r"(?:<MASKED_PII>|\[REDACTED_)")


def ensure_no_redacted_placeholder(text: str) -> None:
    """Reject quotes/payloads leaking <MASKED_PII> or [REDACTED_*] placeholders."""
    if _REDACTED_LEAK_RE.search(text):
        raise OpenDVError(
            "Citation quote contains forbidden redaction placeholder "
            "(<MASKED_PII> or [REDACTED_*]).",
            error_code=ErrorCode.REDACTED_PLACEHOLDER_LEAK,
        )


def _find_nth_occurrence(haystack: str, needle: str, occurrence: int) -> int | None:
    if not needle or occurrence < 1:
        return None
    start = 0
    for idx in range(1, occurrence + 1):
        pos = haystack.find(needle, start)
        if pos == -1:
            return None
        if idx == occurrence:
            return pos
        start = pos + len(needle)
    return None


def _find_page_spec(document_map: DocumentMap | None, page_number: int) -> PageMapSpec | None:
    if document_map is None:
        return None
    for spec in document_map.pages:
        if spec.page_number == page_number:
            return spec
    return None


def _select_ocr_receipt(
    req: CitationResolveReq,
    ocr_receipts: Sequence[OcrPageReceipt],
) -> OcrPageReceipt | None:
    matching = [
        r for r in ocr_receipts if r.doc_id == req.doc_id and r.page_number == req.page_number
    ]
    if not matching:
        return None
    if req.content_sha256 is not None:
        for rec in matching:
            if rec.content_sha256 == req.content_sha256:
                return rec
    return matching[-1]


def resolve_citation(
    req: CitationResolveReq,
    *,
    parsed_pages: Mapping[int, str] | None = None,
    ocr_receipts: Sequence[OcrPageReceipt] = (),
    metadata_only_binary_absent: bool = False,
    document_map: DocumentMap | None = None,
    stored_page_digests: Mapping[int, str] | None = None,
) -> CitationSpanResp:
    """Resolve exact character span (occurrence=N) against page text, OCR receipt, or digest."""
    ensure_no_redacted_placeholder(req.verbatim_quote)
    norm_quote = normalize_page_text(req.verbatim_quote)
    if not norm_quote.strip():
        raise OpenDVError(
            "Citation verbatim_quote cannot be blank.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    page_spec = _find_page_spec(document_map, req.page_number)
    ocr_rec = _select_ocr_receipt(req, ocr_receipts)

    if (
        not metadata_only_binary_absent
        and parsed_pages is not None
        and req.page_number in parsed_pages
    ):
        norm_page = normalize_page_text(parsed_pages[req.page_number])
        page_sha = compute_canonical_text_sha256(norm_page)
        prefer_ocr = ocr_rec is not None and (
            not norm_page.strip()
            or (
                page_spec is not None
                and page_spec.is_scanned_or_low_density
                and req.content_sha256 != page_sha
            )
            or (
                req.content_sha256 is not None
                and req.content_sha256 == ocr_rec.content_sha256
                and req.content_sha256 != page_sha
            )
        )
        if not prefer_ocr:
            if req.content_sha256 is not None and req.content_sha256 != page_sha:
                raise OpenDVError(
                    f"Citation content_sha256 mismatch for {req.doc_id} p.{req.page_number}.",
                    error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                )
            pos = _find_nth_occurrence(norm_page, norm_quote, req.occurrence)
            if pos is None:
                raise OpenDVError(
                    f"Quote occurrence {req.occurrence} not found in "
                    f"{req.doc_id} p.{req.page_number}.",
                    error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                )
            return CitationSpanResp(
                char_start=pos,
                char_end=pos + len(norm_quote),
                matched=True,
                resolved_sha256=page_sha,
                source_kind="canonical_text",
            )

    if ocr_rec is not None:
        norm_ocr = normalize_page_text(ocr_rec.ocr_text_masked)
        if compute_canonical_text_sha256(norm_ocr) != ocr_rec.content_sha256:
            raise OpenDVError(
                f"OCR receipt digest self-integrity check failed for "
                f"{req.doc_id} p.{req.page_number}.",
                error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
            )
        if req.content_sha256 is not None and req.content_sha256 != ocr_rec.content_sha256:
            raise OpenDVError(
                f"Citation content_sha256 mismatch against OCR receipt for "
                f"{req.doc_id} p.{req.page_number}.",
                error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
            )
        is_pre_ocr_scanned_spec = (
            page_spec is not None
            and page_spec.is_scanned_or_low_density
            and page_spec.ocr_box_count == 0
        )
        if (
            page_spec is not None
            and page_spec.canonical_text_sha256
            and not is_pre_ocr_scanned_spec
            and ocr_rec.content_sha256 != page_spec.canonical_text_sha256
        ):
            raise OpenDVError(
                f"OCR receipt digest mismatch against DocumentMap for "
                f"{req.doc_id} p.{req.page_number}.",
                error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
            )
        pos = _find_nth_occurrence(norm_ocr, norm_quote, req.occurrence)
        if pos is None:
            raise OpenDVError(
                f"Quote occurrence {req.occurrence} not found in OCR receipt for "
                f"{req.doc_id} p.{req.page_number}.",
                error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
            )
        return CitationSpanResp(
            char_start=pos,
            char_end=pos + len(norm_quote),
            matched=True,
            resolved_sha256=ocr_rec.content_sha256,
            source_kind="ocr_receipt",
        )

    if metadata_only_binary_absent:
        expected_digest = (
            page_spec.canonical_text_sha256
            if page_spec is not None
            else (
                stored_page_digests.get(req.page_number)
                if stored_page_digests is not None
                else None
            )
        )
        if expected_digest and req.content_sha256 is not None:
            if req.content_sha256 != expected_digest:
                raise OpenDVError(
                    f"Stored digest mismatch for absent source {req.doc_id} p.{req.page_number}.",
                    error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                )
            if page_spec is not None and len(norm_quote) > page_spec.char_count:
                raise OpenDVError(
                    f"Citation quote exceeds stored char_count for "
                    f"{req.doc_id} p.{req.page_number}.",
                    error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
                )
            return CitationSpanResp(
                char_start=None,
                char_end=None,
                matched=True,
                resolved_sha256=expected_digest,
                source_kind="absent",
            )
        raise OpenDVError(
            f"Source binary is absent for {req.doc_id} p.{req.page_number} and no matching "
            "stored digest/OCR receipt is available.",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )

    if parsed_pages is None:
        raise OpenDVError(
            f"Source binary pages are absent for {req.doc_id} p.{req.page_number}.",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )

    raise OpenDVError(
        f"Page {req.page_number} not found in parsed_pages for {req.doc_id}.",
        error_code=ErrorCode.CITATION_SPAN_UNRESOLVED,
    )


__all__ = [
    "ensure_no_redacted_placeholder",
    "resolve_citation",
]
