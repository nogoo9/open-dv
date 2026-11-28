"""Gate V4 (Alg A.9b): Unstructured & multimodal invariants and receipt verification.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import re

from opendv.models.diagnostics import (
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    VerifierDiagnostic,
)
from opendv.models.document_ir import compute_canonical_text_sha256, normalize_page_text
from opendv.models.events import ExternalMcpReceipt, OcrPageReceipt
from opendv.models.pii_tokenizer import REDACTED_LEAK_RE
from opendv.models.sources import PageMapSpec
from opendv.models.vault_ir import DecimalType, PortableLogicalType
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier.ports import VerifierContext

_RAW_BLOB_VEC_RE = re.compile(
    r"(?i)(?:raw_blob|binary_bytes|pdf_bytes|embedding_vec|^vector_|_vector$)"
)

_REQUIRED_AI_PROV_ATTRS: dict[str, str] = {
    "extractor_version": "UTF8_STRING",
    "prompt_or_rule_id": "UTF8_STRING",
    "confidence_score": "DECIMAL(5,4)",
    "review_status": "UTF8_STRING",
    "citation_span": "JSON_DOCUMENT",
    "source_doc_id": "UTF8_STRING",
}


def _diag(rule_id: str, entity_name: str, message: str) -> VerifierDiagnostic:
    return VerifierDiagnostic(
        gate="V4",
        rule_id=rule_id,
        severity="BLOCKER",
        entity_name=entity_name,
        waivable=False,
        message=message,
    )


def _format_ltype(ltype: PortableLogicalType) -> str:
    if isinstance(ltype, DecimalType):
        return f"DECIMAL({ltype.precision},{ltype.scale})"
    if isinstance(ltype, str):
        return ltype
    return ltype.kind


def _nth_occurrence_offset(haystack: str, needle: str, occurrence: int) -> int | None:
    if not needle:
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


def _verify_citation(
    cit: DocumentCitationRef,
    entity_name: str,
    page_texts: dict[tuple[str, int], str],
    page_specs: dict[tuple[str, int], PageMapSpec],
    ocr_receipts: dict[tuple[str, int], OcrPageReceipt],
    diags: list[VerifierDiagnostic],
    *,
    metadata_only_binary_absent: bool = False,
) -> None:
    if REDACTED_LEAK_RE.search(cit.verbatim_quote):
        diags.append(
            _diag(
                "REDACTED_PLACEHOLDER_LEAK",
                entity_name,
                f"Citation on {entity_name} ({cit.doc_id} p.{cit.page_number}) leaks placeholder.",
            )
        )
        return

    key = (cit.doc_id, cit.page_number)
    raw_page = page_texts.get(key)
    page_spec = page_specs.get(key)
    ocr_rec = ocr_receipts.get(key)

    if raw_page is not None:
        norm_page = normalize_page_text(raw_page)
        norm_quote = normalize_page_text(cit.verbatim_quote)
        if cit.char_start is None or cit.char_end is None:
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"Citation ({cit.doc_id} p.{cit.page_number}) missing char_start/char_end.",
                )
            )
            return
        if not (0 <= cit.char_start < cit.char_end <= len(norm_page)):
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"Span [{cit.char_start}:{cit.char_end}] out of bounds for {cit.doc_id}.",
                )
            )
            return
        if norm_page[cit.char_start : cit.char_end] != norm_quote:
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"Slice mismatch at [{cit.char_start}:{cit.char_end}] for {cit.doc_id}.",
                )
            )
            return
        expected_occ = _nth_occurrence_offset(norm_page, norm_quote, cit.occurrence)
        if expected_occ is None or expected_occ != cit.char_start:
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"occurrence={cit.occurrence} != [{cit.char_start}:{cit.char_end}].",
                )
            )
            return
        if (
            page_spec is not None
            and cit.content_sha256 is not None
            and cit.content_sha256 != page_spec.canonical_text_sha256
        ):
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"Digest mismatch against DocumentMap for {cit.doc_id} p.{cit.page_number}.",
                )
            )
        return

    # METADATA_ONLY_BINARY_ABSENT / offline receipt verification
    if ocr_rec is not None:
        if cit.char_start is None or cit.char_end is None:
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"OCR citation ({cit.doc_id} p.{cit.page_number}) missing char_start/char_end.",
                )
            )
            return
        if not cit.content_sha256 or cit.content_sha256 != ocr_rec.content_sha256:
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"OcrPageReceipt sha256 mismatch for {cit.doc_id} p.{cit.page_number}.",
                )
            )
            return
        if page_spec is not None and ocr_rec.content_sha256 != page_spec.canonical_text_sha256:
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"OcrPageReceipt sha256 disagrees with DocumentMap for {cit.doc_id}.",
                )
            )
            return
        norm_ocr = normalize_page_text(ocr_rec.ocr_text_masked)
        norm_quote = normalize_page_text(cit.verbatim_quote)
        if (
            (cit.char_end - cit.char_start) != len(norm_quote)
            or not (0 <= cit.char_start < cit.char_end <= len(norm_ocr))
            or norm_ocr[cit.char_start : cit.char_end] != norm_quote
        ):
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"OCR receipt span mismatch for {cit.doc_id} p.{cit.page_number}.",
                )
            )
            return
        expected_occ = _nth_occurrence_offset(norm_ocr, norm_quote, cit.occurrence)
        if expected_occ is None or expected_occ != cit.char_start:
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"OCR occurrence={cit.occurrence} != [{cit.char_start}:{cit.char_end}].",
                )
            )
        return

    if page_spec is not None:
        if (
            not cit.content_sha256
            or cit.content_sha256 != page_spec.canonical_text_sha256
            or cit.char_start is None
            or cit.char_end is None
        ):
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    entity_name,
                    f"Offline stored-digest check failed for {cit.doc_id} p.{cit.page_number}.",
                )
            )
            return
        norm_quote = normalize_page_text(cit.verbatim_quote)
        if (
            (cit.char_end - cit.char_start) != len(norm_quote)
            or not (0 <= cit.char_start < cit.char_end <= page_spec.char_count)
        ):
            diags.append(
                _diag(
                    "SPAN_HALLUCINATION",
                    entity_name,
                    f"Offline span length/bounds mismatch for {cit.doc_id} p.{cit.page_number}.",
                )
            )
        return

    diags.append(
        _diag(
            "CITATION_SPAN_UNRESOLVED",
            entity_name,
            f"No page text, OcrPageReceipt, or DocumentMap for {cit.doc_id} p.{cit.page_number}.",
        )
    )


def _verify_mcp_ref(
    ref: ExternalMcpEvidenceRef,
    entity_name: str,
    mcp_receipts: dict[str, ExternalMcpReceipt],
    diags: list[VerifierDiagnostic],
) -> None:
    if REDACTED_LEAK_RE.search(ref.masked_excerpt):
        diags.append(
            _diag(
                "REDACTED_PLACEHOLDER_LEAK",
                entity_name,
                f"ExternalMcpEvidenceRef '{ref.receipt_id}' on {entity_name} leaks placeholder.",
            )
        )
    rec = mcp_receipts.get(ref.receipt_id)
    if rec is None:
        diags.append(
            _diag(
                "CITATION_SPAN_UNRESOLVED",
                entity_name,
                f"Missing ExternalMcpReceipt '{ref.receipt_id}' on {entity_name}.",
            )
        )
        return
    if REDACTED_LEAK_RE.search(rec.masked_excerpt):
        diags.append(
            _diag(
                "REDACTED_PLACEHOLDER_LEAK",
                entity_name,
                f"ExternalMcpReceipt '{ref.receipt_id}' on {entity_name} leaks placeholder.",
            )
        )
    if (
        rec.server_id != ref.server_id
        or rec.tool_name != ref.tool_name
        or rec.content_sha256 != ref.content_sha256
        or normalize_page_text(rec.masked_excerpt) != normalize_page_text(ref.masked_excerpt)
    ):
        diags.append(
            _diag(
                "CITATION_SPAN_UNRESOLVED",
                entity_name,
                f"ExternalMcpReceipt '{ref.receipt_id}' digest/metadata mismatch.",
            )
        )


def verify_v4_unstructured(
    ir: CertifiedVaultIR,
    ctx: VerifierContext,
) -> list[VerifierDiagnostic]:
    """Run Gate V4 (Alg A.9b) unstructured & multimodal invariants."""
    diags: list[VerifierDiagnostic] = []

    for sat in ir.satellites:
        if sat.sat_kind in ("STANDARD", "PII", "MAS", "EXT"):
            for fld in sat.attributes:
                if _RAW_BLOB_VEC_RE.search(fld.name):
                    diags.append(
                        _diag(
                            "BLOB_OR_VEC_IN_RAW_SAT",
                            sat.sat_name,
                            f"Rule U1 forbids blob/vector '{fld.name}' in {sat.sat_name}.",
                        )
                    )

    page_specs: dict[tuple[str, int], PageMapSpec] = {}
    for dm in ctx.document_maps:
        for pg in dm.pages:
            page_specs[(dm.doc_id, pg.page_number)] = pg

    page_texts: dict[tuple[str, int], str] = {}
    for view in ctx.page_text_views:
        page_texts[(view.doc_id, view.page_number)] = view.masked_text
    for doc_id, pages_map in ctx.parsed_doc_pages.items():
        for p_num, p_text in pages_map.items():
            page_texts[(doc_id, p_num)] = p_text

    ocr_by_key = {(r.doc_id, r.page_number): r for r in ctx.ocr_receipts}
    mcp_by_id = {r.receipt_id: r for r in ctx.mcp_receipts}
    meta_absent = ctx.metadata_only_binary_absent

    for h in ir.hubs:
        for sb in h.source_bindings:
            for cit in sb.doc_citations:
                _verify_citation(
                    cit,
                    h.hub_name,
                    page_texts,
                    page_specs,
                    ocr_by_key,
                    diags,
                    metadata_only_binary_absent=meta_absent,
                )
            for mref in sb.mcp_evidence:
                _verify_mcp_ref(mref, h.hub_name, mcp_by_id, diags)

    for sat in ir.satellites:
        for sb in sat.source_bindings:
            for cit in sb.doc_citations:
                _verify_citation(
                    cit,
                    sat.sat_name,
                    page_texts,
                    page_specs,
                    ocr_by_key,
                    diags,
                    metadata_only_binary_absent=meta_absent,
                )
            for mref in sb.mcp_evidence:
                _verify_mcp_ref(mref, sat.sat_name, mcp_by_id, diags)

    prov_by_parent = {
        s.parent_name: s for s in ir.satellites if s.sat_kind == "AI_PROVENANCE_SATELLITE"
    }
    for lnk in ir.links:
        for sb in lnk.source_bindings:
            for cit in sb.doc_citations:
                _verify_citation(
                    cit,
                    lnk.link_name,
                    page_texts,
                    page_specs,
                    ocr_by_key,
                    diags,
                    metadata_only_binary_absent=meta_absent,
                )
            for mref in sb.mcp_evidence:
                _verify_mcp_ref(mref, lnk.link_name, mcp_by_id, diags)

        if lnk.link_kind != "UNSTRUCTURED_EXTRACTION_LINK":
            continue

        if lnk.load_semantics is None or lnk.load_semantics.require_review_status != "APPROVED":
            diags.append(
                _diag(
                    "DV-UNST-APPROVED",
                    lnk.link_name,
                    f"Unstructured Link {lnk.link_name} requires require_review_status='APPROVED'.",
                )
            )
        for sb in lnk.source_bindings:
            if (
                sb.load_semantics is not None
                and sb.load_semantics.require_review_status != "APPROVED"
            ):
                diags.append(
                    _diag(
                        "DV-UNST-APPROVED",
                        lnk.link_name,
                        f"Binding {sb.table_id} on {lnk.link_name} must require APPROVED.",
                    )
                )

        has_evidence = any(sb.doc_citations or sb.mcp_evidence for sb in lnk.source_bindings)
        if not lnk.source_bindings or not has_evidence:
            diags.append(
                _diag(
                    "CITATION_SPAN_UNRESOLVED",
                    lnk.link_name,
                    f"Unstructured Link {lnk.link_name} lacks doc_citations or mcp_evidence.",
                )
            )
        else:
            for sb in lnk.source_bindings:
                for cit in sb.doc_citations:
                    if cit.doc_id != sb.source_id:
                        diags.append(
                            _diag(
                                "CITATION_SPAN_UNRESOLVED",
                                lnk.link_name,
                                f"Binding source_id '{sb.source_id}' != doc_id '{cit.doc_id}'.",
                            )
                        )

        prov_sat = prov_by_parent.get(lnk.link_name)
        if prov_sat is None:
            diags.append(
                _diag(
                    "AI_PROVENANCE_MISSING",
                    lnk.link_name,
                    f"Unstructured Link {lnk.link_name} lacks AI_PROVENANCE_SATELLITE.",
                )
            )
        else:
            attr_map = {a.name: _format_ltype(a.logical_type) for a in prov_sat.attributes}
            for req_name, req_type in _REQUIRED_AI_PROV_ATTRS.items():
                actual = attr_map.get(req_name)
                if actual != req_type:
                    diags.append(
                        _diag(
                            "AI_PROVENANCE_SCHEMA",
                            prov_sat.sat_name,
                            f"{prov_sat.sat_name} requires '{req_name}: {req_type}' "
                            f"(got {actual!r}).",
                        )
                    )

    return diags


__all__ = [
    "compute_canonical_text_sha256",
    "normalize_page_text",
    "verify_v4_unstructured",
]
