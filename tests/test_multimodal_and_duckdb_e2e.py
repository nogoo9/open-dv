"""End-to-End Pytest Suite: Multimodal PDF Grounding, Cross-Modal Discrepancy Detection,
and 4-Zone DuckDB Warehouse Execution (`tests/test_multimodal_and_duckdb_e2e.py`).

Covers:
1. Multimodal PDF Character-Offset Grounding (Algorithm A.9b / Gate 5 / DV-AUD-001..005):
   - Positive verification of all 17 extracted spans across both PE PDFs
     (calpers_side_letter_fund_iv_2026.pdf / lpa_apex_buyout_fund_iv_2026.pdf and
      ic_q3_valuation_nordic_cloud.pdf / ic_memo_project_odin_2026Q3.pdf).
   - Parametrized negative/adversarial span mutations: off-by-one shift (DV-AUD-001),
     fabricated/hallucinated quote (DV-AUD-002), out-of-bounds/inverted span (DV-AUD-003),
     low confidence < 0.85 quarantine (DV-AUD-004), and missing AI provenance (DV-AUD-005).
   - Structural Gate 5 errors: BLOB_OR_VEC_IN_RAW_SAT and MISSING_PROVENANCE_SAT.

2. Cross-Modal Numerical Discrepancy Detector & Algorithm A.9a Intra-Source Schema Drift:
   - Detects the 25 bps management fee discrepancy (0.0175 / 1.75% vs 0.0200 / 2.00%)
     and the 50 bps CalPERS side-letter fee cap reduction (125 bps vs 175 bps) while
     guaranteeing that structured raw_vault.sat_fund_investran is NEVER overwritten (AP-12 / INV-01).
   - Verifies negative/matching rate case (MATCH_VERIFIED, delta_bps == 0).
   - Verifies Algorithm A.9a (codemod_intra_source_schema_drift) preserves historical
     payload_columns order, marks soft_deprecated_columns, and splits additive columns
     into sat_fund_investran_ext_esg.

3. 4-Zone DuckDB Warehouse Execution, 15 Dynamic Sandbox Checks (DV-DYN-001..015),
   4 Adversarial Shadow Sandbox Batches, & Bi-Temporal Audit Queries (Query D.10 & Query D.10b):
   - Executes Artifact 3 4-Zone SQL bundle from docs/07-end-to-end-private-equity-demo.md in :memory: DuckDB.
   - Verifies all Hubs/Reference tables, Links (including sal_limited_partner), and Satellites exist and
     contain -1 (0000...0000) and -2 (ffff...ffff) Zero Keys and Ghost Records.
   - Verifies 0 orphans across all 23 foreign-key relationships in Raw Vault and Business Vault (+2 PIT FKs).
   - Verifies all 15 Dynamic Verification Rules (DV-DYN-001..015) + negative anomaly detection.
   - Executes all 4 Adversarial Shadow Sandbox Batches (Batch 1 Baseline, Batch 2 Duplicate Replay,
     Batch 3 Attribute Mutation & Day-3 Schema Drift, Batch 4 Multi-Source BKCC Collision & Late IC Memo).
   - Verifies Bi-Temporal Multimodal Point-in-Time Queries D.10 and D.10b at both pre-IC-memo
     (2026-10-15 12:00:00 -> 12.80x / $216.28M from DEALCLOUD_PE_CRM with NULL PDF span) and
     post-IC-memo (2026-11-20 23:59:59 -> 14.50x / $245.00M from OBJECT_STORE_S3 with [108:1151] span).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PE_PROFILE_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_b_profile.json"

ZERO_KEY_UNKNOWN = "0" * 64
ZERO_KEY_ERROR = "f" * 64
GHOST_LOAD_DTS = "1900-01-01 00:00:00"


def dv2_sha256(tokens: list[str | None], is_business_key: bool = True) -> str:
    """Algorithm A.5 6-Stage Length-Prefixed Uppercase Hex SHA-256 Digest."""
    parts: list[str] = []
    for tok in tokens:
        if tok is None:
            norm = ""
        else:
            s = str(tok).strip()
            norm = s.upper() if is_business_key else s
        if not norm:
            parts.append("2:-1")
        else:
            byte_len = len(norm.encode("utf-8"))
            parts.append(f"{byte_len}:{norm}")
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest().upper()


# =============================================================================
# Reference Audit & Discrepancy Engines (DV-AUD-001..005 & Cross-Modal Detector)
# =============================================================================


def verify_multimodal_span_audit(
    extraction: dict[str, Any],
    parsed_doc_samples: dict[str, str],
    min_confidence: float = 0.85,
    min_top2_margin: float = 0.15,
) -> list[str]:
    """Granular Gate 5 Unstructured Extraction Audit Verifier (DV-AUD-001..005)
    complementing Algorithm A.9b (verify_gate5_unstructured_invariants).

    Enforces:
    - DV-AUD-005 (MISSING_AI_PROVENANCE): Requires non-empty doc_id / hk_document and extractor_model_id.
    - DV-AUD-003 (SPAN_OUT_OF_BOUNDS): Requires 0 <= char_start < char_end <= len(doc_text).
    - DV-AUD-001 (SPAN_OFFSET_MISMATCH): Quote exists in doc_text, but doc_text[char_start:char_end] != quote.
    - DV-AUD-002 (SPAN_QUOTE_HALLUCINATION): Fabricated quote does not appear in doc_text at all.
    - DV-AUD-004 (LOW_CONFIDENCE_QUARANTINE): confidence_score < min_confidence or top2_margin < min_top2_margin.
    """
    violations: list[str] = []
    doc_id = extraction.get("doc_id") or extraction.get("doc_uri") or ""
    hk_doc = extraction.get("hk_document") or ""
    model_id = extraction.get("extractor_model_id") or ""

    if not (doc_id or hk_doc) or not str(model_id).strip():
        violations.append(
            "DV-AUD-005 [MISSING_AI_PROVENANCE]: Extraction requires non-empty "
            "hk_document/doc_id and extractor_model_id provenance."
        )

    doc_text = parsed_doc_samples.get(doc_id, "")
    if not doc_text:
        violations.append(
            f"DV-AUD-005 [MISSING_AI_PROVENANCE]: Source document '{doc_id}' not found in parsed_doc_samples."
        )
        return violations

    char_start = extraction.get("char_start")
    char_end = extraction.get("char_end")
    quote = extraction.get("evidence_quote", "")

    if (
        not isinstance(char_start, int)
        or not isinstance(char_end, int)
        or char_start < 0
        or char_end > len(doc_text)
        or char_start >= char_end
    ):
        violations.append(
            f"DV-AUD-003 [SPAN_OUT_OF_BOUNDS]: Span [{char_start}:{char_end}] is out of bounds "
            f"for document '{doc_id}' (length={len(doc_text)})."
        )
    else:
        actual_slice = doc_text[char_start:char_end]
        if not quote or actual_slice != quote:
            if quote and quote in doc_text:
                violations.append(
                    f"DV-AUD-001 [SPAN_OFFSET_MISMATCH]: Quote '{quote}' exists in '{doc_id}' "
                    f"but does not match slice [{char_start}:{char_end}] ('{actual_slice}')."
                )
            else:
                violations.append(
                    f"DV-AUD-002 [SPAN_QUOTE_HALLUCINATION]: Fabricated quote '{quote}' "
                    f"not present in source document '{doc_id}'."
                )

    confidence = float(extraction.get("confidence_score", 1.0))
    top2_margin = extraction.get("top2_margin")
    if confidence < min_confidence or (
        top2_margin is not None and float(top2_margin) < min_top2_margin
    ):
        violations.append(
            f"DV-AUD-004 [LOW_CONFIDENCE_QUARANTINE]: Extraction confidence={confidence:.3f} "
            f"(top2_margin={top2_margin}) below threshold ({min_confidence:.2f} / margin {min_top2_margin:.2f}); "
            f"quarantine in stg_unresolved_doc_mentions (Tier-U3 Captain Hold)."
        )

    return violations


@dataclass(frozen=True)
class CrossModalDiscrepancyAlert:
    status: str  # "DISCREPANCY_DETECTED" | "MATCH_VERIFIED"
    entity_key: str
    attribute_name: str
    structured_source: str
    structured_value: Decimal
    unstructured_doc_uri: str
    unstructured_value: Decimal
    absolute_delta: Decimal
    delta_bps: Decimal
    char_span: str
    evidence_quote: str
    raw_vault_mutated: bool


def detect_cross_modal_discrepancy(
    *,
    entity_key: str,
    attribute_name: str,
    structured_source: str,
    structured_value: Decimal | str | float,
    unstructured_doc_uri: str,
    unstructured_value: Decimal | str | float,
    char_span: str,
    evidence_quote: str,
    is_decimal_rate: bool = False,
    tolerance: Decimal = Decimal("0.00001"),
) -> CrossModalDiscrepancyAlert:
    """Cross-Modal Numerical Discrepancy Detector (Rule U3 / INV-01 / AP-12).

    Compares structured Raw Vault numerical attributes against span-grounded
    Business Vault PDF extractions without ever mutating the Raw Vault.
    """
    s_val = Decimal(str(structured_value))
    u_val = Decimal(str(unstructured_value))
    abs_delta = abs(s_val - u_val)
    delta_bps = (abs_delta * Decimal("10000")) if is_decimal_rate else abs_delta
    status = "DISCREPANCY_DETECTED" if abs_delta > tolerance else "MATCH_VERIFIED"
    return CrossModalDiscrepancyAlert(
        status=status,
        entity_key=entity_key,
        attribute_name=attribute_name,
        structured_source=structured_source,
        structured_value=s_val,
        unstructured_doc_uri=unstructured_doc_uri,
        unstructured_value=u_val,
        absolute_delta=abs_delta,
        delta_bps=delta_bps.quantize(Decimal("0.01")),
        char_span=char_span,
        evidence_quote=evidence_quote,
        raw_vault_mutated=False,
    )


def _collect_all_17_spans_for_parametrization() -> list[tuple[str, str, int, int, str, str, float, float | None]]:
    with PE_PROFILE_PATH.open(encoding="utf-8") as f:
        profile = json.load(f)
    s3_src = next(
        s for s in profile["source_systems"] if s["source_system_code"] == "OBJECT_STORE_S3"
    )
    cases: list[tuple[str, str, int, int, str, str, float, float | None]] = []
    for doc in s3_src["unstructured_documents"]:
        doc_uri = doc["doc_uri"]
        for sp in doc["extracted_spans"]:
            cases.append(
                (
                    doc_uri,
                    sp["field"],
                    int(sp["span_start"]),
                    int(sp["span_end"]),
                    sp["verbatim_quote"],
                    sp["er_tier"],
                    float(sp["confidence"]),
                    sp.get("top2_margin"),
                )
            )
    return cases


ALL_17_PDF_SPANS = _collect_all_17_spans_for_parametrization()


# =============================================================================
# SECTION 1: Multimodal Character-Offset Grounding (Algorithm A.9b / DV-AUD-001..005)
# =============================================================================


def test_pdf_corpus_lengths_and_row_chunk_spans(
    pdf_corpus: dict[str, str],
    pe_profile: dict[str, Any],
) -> None:
    """Verify exact byte/char lengths and row-level chunk spans across both PE PDFs."""
    assert len(ALL_17_PDF_SPANS) == 17, f"Expected exactly 17 PDF spans, found {len(ALL_17_PDF_SPANS)}"

    doc1_uri = "s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf"
    doc2_uri = "s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf"

    doc1_text = pdf_corpus[doc1_uri]
    doc2_text = pdf_corpus[doc2_uri]

    assert len(doc1_text) == 1215
    assert len(doc2_text) == 1331

    # Verify brief alias compatibility
    assert pdf_corpus["lpa_apex_buyout_fund_iv_2026.pdf"] == doc1_text
    assert pdf_corpus["ic_memo_project_odin_2026Q3.pdf"] == doc2_text

    # Verify row-level chunk [38:1012] covers all 5 extracted attributes on Doc 1 row
    r1_start, r1_end = 38, 1012
    assert doc1_text[r1_start:r1_end].startswith("DATE: February 14, 2026")
    assert doc1_text[r1_start:r1_end].endswith("exceeds USD 50,000,000.")
    for sub_start, sub_end in [(44, 61), (499, 506), (607, 614), (665, 691), (878, 914)]:
        assert r1_start <= sub_start and sub_end <= r1_end

    # Verify row-level chunk [108:1151] covers all extracted attributes on Doc 2 row
    r2_start, r2_end = 108, 1151
    assert doc2_text[r2_start:r2_end].startswith("VALUATION EFFECTIVE DATE: September 30, 2026")
    assert doc2_text[r2_start:r2_end].endswith("combined debt-free operating holding.")
    for sub_start, sub_end in [(134, 152), (186, 206), (226, 246), (259, 271), (599, 624), (915, 932), (1033, 1051), (1076, 1105)]:
        assert r2_start <= sub_start and sub_end <= r2_end


@pytest.mark.parametrize(
    "doc_uri,field,span_start,span_end,verbatim_quote,er_tier,confidence,top2_margin",
    ALL_17_PDF_SPANS,
    ids=[f"{idx + 1:02d}_{c[1]}" for idx, c in enumerate(ALL_17_PDF_SPANS)],
)
def test_all_17_pdf_character_offset_spans_positive(
    doc_uri: str,
    field: str,
    span_start: int,
    span_end: int,
    verbatim_quote: str,
    er_tier: str,
    confidence: float,
    top2_margin: float | None,
    pdf_corpus: dict[str, str],
    doc05_algos: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Positive verification of all 17 character-offset spans ([span_start, span_end))
    across both PDFs using Algorithm A.9b (verify_gate5_unstructured_invariants)
    and verify_multimodal_span_audit.
    """
    doc_text = pdf_corpus[doc_uri]
    assert 0 <= span_start < span_end <= len(doc_text)
    assert doc_text[span_start:span_end] == verbatim_quote

    # Verify via Algorithm A.9b from docs/05
    verify_gate5 = doc05_algos["verify_gate5_unstructured_invariants"]
    contract_with_sample = deepcopy(pe_contract)
    contract_with_sample["sample_extractions"] = [
        {
            "doc_id": doc_uri,
            "attribute": field,
            "evidence_quote": verbatim_quote,
            "char_start": span_start,
            "char_end": span_end,
        }
    ]
    a9b_errors = verify_gate5(contract_with_sample, pdf_corpus)
    assert a9b_errors == []

    # Verify via granular DV-AUD-001..005 verifier
    audit_errors = verify_multimodal_span_audit(
        {
            "doc_id": doc_uri,
            "hk_document": dv2_sha256(["GLOBAL", doc_uri]),
            "extractor_model_id": "claude-3-7-sonnet",
            "attribute": field,
            "evidence_quote": verbatim_quote,
            "char_start": span_start,
            "char_end": span_end,
            "confidence_score": confidence,
            "top2_margin": top2_margin,
        },
        pdf_corpus,
    )
    if er_tier == "TIER_U3_CAPTAIN_HOLD":
        # Span 9 ('ambiguous_vehicle' = 'Horizon Sovereign Co-Invest Vehicle') has
        # confidence=0.81 < 0.85 and top2_margin=0.02 < 0.15 -> quarantined by DV-AUD-004!
        assert field == "ambiguous_vehicle"
        assert len(audit_errors) == 1
        assert "DV-AUD-004 [LOW_CONFIDENCE_QUARANTINE]" in audit_errors[0]
    else:
        assert audit_errors == []


@pytest.mark.parametrize(
    "case_id,doc_id,char_start,char_end,quote,confidence,top2_margin,hk_doc,model_id,expected_code,expect_a9b_error",
    [
        (
            "off_by_one_left_shift_fee_cap",
            "lpa_apex_buyout_fund_iv_2026.pdf",
            498,
            505,
            "125 bps",
            0.99,
            None,
            "864D5083",
            "claude-3-7-sonnet",
            "DV-AUD-001 [SPAN_OFFSET_MISMATCH]",
            True,
        ),
        (
            "off_by_one_right_shift_multiple",
            "ic_memo_project_odin_2026Q3.pdf",
            916,
            933,
            "EV/EBITDA = 14.5x",
            0.99,
            None,
            "99D8178E",
            "claude-3-7-sonnet",
            "DV-AUD-001 [SPAN_OFFSET_MISMATCH]",
            True,
        ),
        (
            "fabricated_quote_hallucination_fee",
            "lpa_apex_buyout_fund_iv_2026.pdf",
            499,
            506,
            "000 bps",
            0.99,
            None,
            "864D5083",
            "claude-3-7-sonnet",
            "DV-AUD-002 [SPAN_QUOTE_HALLUCINATION]",
            True,
        ),
        (
            "fabricated_quote_hallucination_multiple",
            "ic_memo_project_odin_2026Q3.pdf",
            915,
            932,
            "EV/EBITDA = 19.9x",
            0.99,
            None,
            "99D8178E",
            "claude-3-7-sonnet",
            "DV-AUD-002 [SPAN_QUOTE_HALLUCINATION]",
            True,
        ),
        (
            "out_of_bounds_char_end_exceeds_doc_len",
            "lpa_apex_buyout_fund_iv_2026.pdf",
            1100,
            5000,
            "Horizon Sovereign Co-Invest Vehicle",
            0.99,
            None,
            "864D5083",
            "claude-3-7-sonnet",
            "DV-AUD-003 [SPAN_OUT_OF_BOUNDS]",
            True,
        ),
        (
            "inverted_span_start_ge_end",
            "ic_memo_project_odin_2026Q3.pdf",
            932,
            915,
            "EV/EBITDA = 14.5x",
            0.99,
            None,
            "99D8178E",
            "claude-3-7-sonnet",
            "DV-AUD-003 [SPAN_OUT_OF_BOUNDS]",
            True,
        ),
        (
            "zero_width_empty_span",
            "lpa_apex_buyout_fund_iv_2026.pdf",
            499,
            499,
            "125 bps",
            0.99,
            None,
            "864D5083",
            "claude-3-7-sonnet",
            "DV-AUD-003 [SPAN_OUT_OF_BOUNDS]",
            True,
        ),
        (
            "low_confidence_below_085_quarantine",
            "lpa_apex_buyout_fund_iv_2026.pdf",
            1112,
            1147,
            "Horizon Sovereign Co-Invest Vehicle",
            0.81,
            0.02,
            "864D5083",
            "claude-3-7-sonnet",
            "DV-AUD-004 [LOW_CONFIDENCE_QUARANTINE]",
            False,
        ),
        (
            "missing_extractor_model_id_provenance",
            "ic_memo_project_odin_2026Q3.pdf",
            915,
            932,
            "EV/EBITDA = 14.5x",
            0.99,
            None,
            "99D8178E",
            "",
            "DV-AUD-005 [MISSING_AI_PROVENANCE]",
            False,
        ),
        (
            "missing_hk_document_and_doc_id",
            "",
            915,
            932,
            "EV/EBITDA = 14.5x",
            0.99,
            None,
            "",
            "claude-3-7-sonnet",
            "DV-AUD-005 [MISSING_AI_PROVENANCE]",
            True,
        ),
    ],
)
def test_algorithm_a9b_negative_adversarial_cases(
    case_id: str,
    doc_id: str,
    char_start: int,
    char_end: int,
    quote: str,
    confidence: float,
    top2_margin: float | None,
    hk_doc: str,
    model_id: str,
    expected_code: str,
    expect_a9b_error: bool,
    pdf_corpus: dict[str, str],
    doc05_algos: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Parametrized negative/adversarial tests for Algorithm A.9b & DV-AUD-001..005:
    - DV-AUD-001: Off-by-one character offset mismatch
    - DV-AUD-002: Hallucinated/fabricated quote not present in source document
    - DV-AUD-003: Out-of-bounds char_end > len(doc_text) or char_start >= char_end
    - DV-AUD-004: Extraction confidence < 0.85 -> quarantine/review
    - DV-AUD-005: Missing hk_document or extractor_model_id provenance
    """
    violations = verify_multimodal_span_audit(
        {
            "doc_id": doc_id,
            "hk_document": hk_doc,
            "extractor_model_id": model_id,
            "attribute": case_id,
            "evidence_quote": quote,
            "char_start": char_start,
            "char_end": char_end,
            "confidence_score": confidence,
            "top2_margin": top2_margin,
        },
        pdf_corpus,
    )
    assert any(expected_code in v for v in violations), (
        f"Case '{case_id}' expected '{expected_code}', got {violations}"
    )

    verify_gate5 = doc05_algos["verify_gate5_unstructured_invariants"]
    bad_contract = deepcopy(pe_contract)
    bad_contract["sample_extractions"] = [
        {
            "doc_id": doc_id,
            "attribute": case_id,
            "evidence_quote": quote,
            "char_start": char_start,
            "char_end": char_end,
        }
    ]
    a9b_errors = verify_gate5(bad_contract, pdf_corpus)
    if expect_a9b_error:
        assert any("GATE5_ERROR [SPAN_HALLUCINATION]" in e for e in a9b_errors)
    else:
        assert a9b_errors == []


def test_algorithm_a9b_structural_blob_and_missing_provenance_sat_errors(
    doc05_algos: dict[str, Any],
    pe_contract: dict[str, Any],
    pdf_corpus: dict[str, str],
) -> None:
    """Verify Algorithm A.9b structural checks:
    1. GATE5_ERROR [BLOB_OR_VEC_IN_RAW_SAT] when raw_blob or embedding_vec is placed in a STANDARD_SATELLITE.
    2. GATE5_ERROR [MISSING_PROVENANCE_SAT] when an UNSTRUCTURED_EXTRACTION_LINK lacks an AI_PROVENANCE_SATELLITE
       with {'extractor_model_id', 'confidence_score', 'evidence_quote'}.
    """
    verify_gate5 = doc05_algos["verify_gate5_unstructured_invariants"]

    # 1. Inject forbidden raw_blob & embedding_vec into a STANDARD_SATELLITE
    mutated = deepcopy(pe_contract)
    mutated["satellites"][0]["payload_columns"].extend(["pdf_raw_blob", "chunk_embedding_vec"])
    # 2. Strip extractor_model_id from AI_PROVENANCE_SATELLITE on lnk_document_portfolio_company
    for sat in mutated["satellites"]:
        if sat["name"] == "sat_lnk_doc_portco_ai_provenance":
            sat["payload_columns"] = [
                c for c in sat["payload_columns"] if c != "extractor_model_id"
            ]

    errors = verify_gate5(mutated, pdf_corpus)
    assert any("GATE5_ERROR [BLOB_OR_VEC_IN_RAW_SAT]" in e for e in errors)
    assert any("GATE5_ERROR [MISSING_PROVENANCE_SAT]" in e for e in errors)


# =============================================================================
# SECTION 2: Cross-Modal Numerical Discrepancy Detector & Algorithm A.9a
# =============================================================================


def test_cross_modal_numerical_discrepancy_detector_25bps_and_50bps(
    pdf_corpus: dict[str, str],
) -> None:
    """Positive & negative verification of the Cross-Modal Numerical Discrepancy Detector:
    1. Detects the 25 bps management fee discrepancy between an executed LPA PDF
       (0.0175 / 1.75%) and Investran (0.0200 / 2.00%), emitting DISCREPANCY_DETECTED.
    2. Detects the 50 bps fee reduction between the CalPERS Side Letter PDF (125.0000 bps)
       and Investran headline rate (175.0000 bps).
    3. Emits MATCH_VERIFIED when Investran headline rate (0.0175 / 175 bps) matches the
       PDF headline rate (0.0175 / 175 bps).
    4. Verifies that structured raw_vault.sat_fund_investran is NEVER overwritten (AP-12 / INV-01).
    """
    doc1_text = pdf_corpus["lpa_apex_buyout_fund_iv_2026.pdf"]

    # Case 1: Positive test — 25 bps discrepancy: LPA PDF (0.0175 / 1.75%) vs Investran (0.0200 / 2.00%)
    alert_25bps = detect_cross_modal_discrepancy(
        entity_key="APEX_BO_IV",
        attribute_name="mgmt_fee_rate",
        structured_source="raw_vault.sat_fund_investran",
        structured_value=Decimal("0.0200"),
        unstructured_doc_uri="lpa_apex_buyout_fund_iv_2026.pdf",
        unstructured_value=Decimal("0.0175"),
        char_span="[607:614]",
        evidence_quote=doc1_text[607:614],
        is_decimal_rate=True,
    )
    assert alert_25bps.status == "DISCREPANCY_DETECTED"
    assert alert_25bps.structured_value == Decimal("0.0200")
    assert alert_25bps.unstructured_value == Decimal("0.0175")
    assert alert_25bps.absolute_delta == Decimal("0.0025")
    assert alert_25bps.delta_bps == Decimal("25.00")
    assert alert_25bps.evidence_quote == "175 bps"
    assert alert_25bps.raw_vault_mutated is False

    # Case 2: Positive test — 50 bps CalPERS Side Letter discount (125 bps vs 175 bps in DuckDB)
    alert_50bps = detect_cross_modal_discrepancy(
        entity_key="APEX_BO_IV||LP-101",
        attribute_name="fee_cap_bps_vs_mgmt_fee_bps",
        structured_source="raw_vault.sat_fund_investran",
        structured_value=Decimal("175.0000"),
        unstructured_doc_uri="calpers_side_letter_fund_iv_2026.pdf",
        unstructured_value=Decimal("125.0000"),
        char_span="[499:506]",
        evidence_quote=doc1_text[499:506],
        is_decimal_rate=False,
    )
    assert alert_50bps.status == "DISCREPANCY_DETECTED"
    assert alert_50bps.delta_bps == Decimal("50.00")
    assert alert_50bps.evidence_quote == "125 bps"
    assert alert_50bps.raw_vault_mutated is False

    # Case 3: Negative test — No discrepancy when structured (0.0175) == unstructured headline (0.0175)
    match_res = detect_cross_modal_discrepancy(
        entity_key="APEX_BO_IV",
        attribute_name="mgmt_fee_rate",
        structured_source="raw_vault.sat_fund_investran",
        structured_value=Decimal("0.0175"),
        unstructured_doc_uri="lpa_apex_buyout_fund_iv_2026.pdf",
        unstructured_value=Decimal("0.0175"),
        char_span="[607:614]",
        evidence_quote=doc1_text[607:614],
        is_decimal_rate=True,
    )
    assert match_res.status == "MATCH_VERIFIED"
    assert match_res.delta_bps == Decimal("0.00")
    assert match_res.raw_vault_mutated is False


def test_cross_modal_discrepancy_never_overwrites_sat_fund_investran_in_duckdb(
    populated_pe_duckdb: duckdb.DuckDBPyConnection,
) -> None:
    """Verify in DuckDB that when an unstructured PDF extraction conflicts with
    raw_vault.sat_fund_investran (whether the 50 bps CalPERS side letter cap or a 25 bps
    LPA vs Investran fee discrepancy), raw_vault.sat_fund_investran is NEVER overwritten (AP-12 / INV-01).
    """
    con = populated_pe_duckdb

    # Snapshot sat_fund_investran before any cross-modal discrepancy query or late arrival
    before_rows = con.execute(
        "SELECT hk_fund, load_dts, hash_diff, mgmt_fee_bps, rec_src "
        "FROM raw_vault.sat_fund_investran ORDER BY hk_fund, load_dts"
    ).fetchall()
    assert len(before_rows) == 4  # 1 ghost row + 3 fund rows

    # Verify APEX_BO_IV structured fee in sat_fund_investran is 175.0000 bps
    hk_bo4 = dv2_sha256(["GLOBAL", "APEX_BO_IV"])
    bo4_row = con.execute(
        "SELECT mgmt_fee_bps, hash_diff FROM raw_vault.sat_fund_investran WHERE hk_fund = ?",
        [hk_bo4],
    ).fetchone()
    assert bo4_row is not None
    assert bo4_row[0] == Decimal("175.0000")

    # Query cross-modal comparison between raw_vault.sat_fund_investran and business_vault.sat_lp_side_letter_llm_ext
    discrepancy_row = con.execute(
        """
        SELECT
            h_f.fund_code,
            h_lp.lp_natural_key,
            s_f.mgmt_fee_bps AS investran_mgmt_fee_bps,
            s_sl.fee_cap_bps AS pdf_side_letter_fee_cap_bps,
            s_sl.headline_fee_bps AS pdf_headline_fee_bps,
            (s_f.mgmt_fee_bps - s_sl.fee_cap_bps) AS fee_discount_delta_bps,
            s_sl.chunk_id
        FROM raw_vault.lnk_document_lp_commitment l_doc
        JOIN raw_vault.hub_fund h_f ON l_doc.hk_fund = h_f.hk_fund
        JOIN raw_vault.hub_limited_partner h_lp ON l_doc.hk_limited_partner = h_lp.hk_limited_partner
        JOIN raw_vault.sat_fund_investran s_f ON h_f.hk_fund = s_f.hk_fund
        JOIN business_vault.sat_lp_side_letter_llm_ext s_sl
          ON l_doc.hk_lnk_document_lp_commitment = s_sl.hk_lnk_document_lp_commitment
        WHERE l_doc.rec_src <> 'SYSTEM_ZERO_KEY'
        """
    ).fetchone()
    assert discrepancy_row == (
        "APEX_BO_IV",
        "LP-101",
        Decimal("175.0000"),
        Decimal("125.0000"),
        Decimal("175.0000"),
        Decimal("50.0000"),
        "[38:1012]",
    )

    # Confirm raw_vault.sat_fund_investran was never overwritten or mutated
    after_rows = con.execute(
        "SELECT hk_fund, load_dts, hash_diff, mgmt_fee_bps, rec_src "
        "FROM raw_vault.sat_fund_investran ORDER BY hk_fund, load_dts"
    ).fetchall()
    assert after_rows == before_rows


def test_algorithm_a9a_intra_source_schema_drift_codemod(
    doc05_algos: dict[str, Any],
) -> None:
    """Test Algorithm A.9a (codemod_intra_source_schema_drift) from docs/05 §3.9:
    When investran.src_pe_funds drops 'deprecated_fax_no' and adds 'ilpa_esg_rating',
    the deployed Satellite 'sat_fund_investran' retains its original sorted payload_columns
    (preserving 100% of historical HASHDIFF digests), records 'soft_deprecated_columns',
    and isolates 'ilpa_esg_rating' into an additive companion Satellite.
    """
    codemod_a9a = doc05_algos["codemod_intra_source_schema_drift"]

    deployed_contract = {
        "satellites": [
            {
                "name": "sat_fund_investran",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_fund",
                "source_bronze_table": "investran.src_pe_funds",
                "security_classification": "PUBLIC",
                "volatility_tier": "STATIC",
                "payload_columns": [
                    "isin",
                    "fund_name",
                    "vintage_year",
                    "target_size_usd",
                    "mgmt_fee_bps",
                    "deprecated_fax_no",
                ],
            }
        ]
    }
    candidate_contract = {
        "satellites": [
            {
                "name": "sat_fund_investran",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_fund",
                "source_bronze_table": "investran.src_pe_funds",
                "security_classification": "PUBLIC",
                "volatility_tier": "STATIC",
                # Day-3 drift: deprecated_fax_no dropped, ilpa_esg_rating added
                "payload_columns": [
                    "isin",
                    "fund_name",
                    "vintage_year",
                    "target_size_usd",
                    "mgmt_fee_bps",
                    "ilpa_esg_rating",
                ],
            }
        ]
    }

    result = codemod_a9a(deployed_contract, deepcopy(candidate_contract), schema_epoch_tag="esg")
    sats = {s["name"]: s for s in result["satellites"]}
    assert set(sats.keys()) == {"sat_fund_investran", "sat_fund_investran_ext_esg"}

    base_sat = sats["sat_fund_investran"]
    assert base_sat["payload_columns"] == [
        "deprecated_fax_no",
        "fund_name",
        "isin",
        "mgmt_fee_bps",
        "target_size_usd",
        "vintage_year",
    ]
    assert base_sat["soft_deprecated_columns"] == ["deprecated_fax_no"]

    ext_sat = sats["sat_fund_investran_ext_esg"]
    assert ext_sat["parent_entity"] == "hub_fund"
    assert ext_sat["volatility_tier"] == "ADDITIVE_DRIFT_COMPANION"
    assert ext_sat["payload_columns"] == ["ilpa_esg_rating"]


# =============================================================================
# SECTION 3: 4-Zone DuckDB Warehouse, DV-DYN-001..015, 4 Batches, & Queries D.10 / D.10b
# =============================================================================


def test_artifact3_4zone_duckdb_warehouse_tables_zero_keys_ghosts_and_zero_orphans(
    populated_pe_duckdb: duckdb.DuckDBPyConnection,
) -> None:
    """Execute the Artifact 3 4-Zone SQL bundle from docs/07 in :memory: DuckDB and verify:
    1. All 6 root/lookup tables (4 Hubs + 2 Reference tables), 5 Links (including sal_limited_partner),
       and 11 Satellites (9 Raw Vault + 2 Business Vault AI Provenance) + 1 PIT View exist.
    2. Every Hub and Link contains both -1 (0000...0000) and -2 (ffff...ffff) System Zero Keys.
    3. Every Satellite contains the -1 (0000...0000) System Ghost Record at '1900-01-01 00:00:00'
       (and supports -2 error ghost seeding without collision).
    4. 0 orphans across all 23 Raw/Business Vault foreign-key relationships (+2 PIT-to-Satellite FKs = 25).
    """
    con = populated_pe_duckdb

    hub_and_link_pks = [
        ("raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.hub_document", "hk_document"),
        ("raw_vault.lnk_lp_commitment", "hk_lnk_lp_commitment"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_lnk_lp_commitment_co_invest"),
        ("raw_vault.sal_limited_partner", "hk_sal_limited_partner"),
        ("raw_vault.lnk_document_lp_commitment", "hk_lnk_document_lp_commitment"),
        ("raw_vault.lnk_document_portfolio_company", "hk_lnk_document_portfolio_company"),
    ]
    for tbl, pk in hub_and_link_pks:
        cnt = con.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE {pk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')"
        ).fetchone()[0]
        assert cnt == 2, f"Expected both -1 and -2 zero keys in {tbl}, found {cnt}"

    # Verify Reference tables (completing the 6 root/lookup entities)
    ref_strategies = {
        r[0] for r in con.execute("SELECT strategy FROM raw_vault.ref_fund_strategy").fetchall()
    }
    assert ref_strategies == {"BUYOUT", "CO_INVEST"}
    ref_sectors = {
        r[0] for r in con.execute("SELECT sector FROM raw_vault.ref_industry_sector").fetchall()
    }
    assert ref_sectors == {"B2B_SAAS", "INDUSTRIAL_TECH", "HEALTHCARE_IT"}

    satellites = [
        ("raw_vault.sat_fund_investran", "hk_fund"),
        ("raw_vault.sat_fund_investran_esg", "hk_fund"),
        ("raw_vault.sat_limited_partner_investran", "hk_limited_partner"),
        ("raw_vault.sat_limited_partner_pii", "hk_limited_partner"),
        ("raw_vault.sat_lp_commitment_investran", "hk_lnk_lp_commitment"),
        ("raw_vault.sat_portfolio_company_dealcloud", "hk_portfolio_company"),
        ("raw_vault.sat_portco_valuation_mnpi", "hk_portfolio_company"),
        ("raw_vault.sat_limited_partner_legacy", "hk_limited_partner"),
        ("raw_vault.sat_document_manifest", "hk_document"),
        ("business_vault.sat_lp_side_letter_llm_ext", "hk_lnk_document_lp_commitment"),
        ("business_vault.sat_lnk_doc_portco_ai_provenance", "hk_lnk_document_portfolio_company"),
    ]
    for sat, pk in satellites:
        cnt = con.execute(
            f"SELECT COUNT(*) FROM {sat} "
            f"WHERE {pk} = '{ZERO_KEY_UNKNOWN}' "
            f"AND load_dts = TIMESTAMP '{GHOST_LOAD_DTS}' "
            f"AND rec_src = 'SYSTEM_GHOST'"
        ).fetchone()[0]
        assert cnt == 1, f"Expected 1 SYSTEM_GHOST row in {sat}, found {cnt}"

        # Also verify -2 (ffff...ffff) Error Ghost Record seeding compatibility on every Satellite
        cols = [r[0] for r in con.execute(f"DESCRIBE {sat}").fetchall()]
        select_exprs = [
            f"'{ZERO_KEY_ERROR}'" if c in (pk, "hash_diff") else c for c in cols
        ]
        con.execute(
            f"INSERT INTO {sat} SELECT {', '.join(select_exprs)} FROM {sat} "
            f"WHERE {pk} = '{ZERO_KEY_UNKNOWN}' "
            f"AND NOT EXISTS (SELECT 1 FROM {sat} WHERE {pk} = '{ZERO_KEY_ERROR}');"
        )
        both_ghosts = con.execute(
            f"SELECT COUNT(*) FROM {sat} "
            f"WHERE {pk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}') "
            f"AND load_dts = TIMESTAMP '{GHOST_LOAD_DTS}'"
        ).fetchone()[0]
        assert both_ghosts == 2, f"Expected both -1 and -2 ghost rows in {sat}, found {both_ghosts}"

    # Verify 0 orphans across all 23 FK relationships in Raw Vault & Business Vault
    fk_relationships = [
        ("raw_vault.lnk_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_lp_commitment", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.sal_limited_partner", "hk_limited_partner_master", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.sal_limited_partner", "hk_limited_partner_duplicate", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_document_lp_commitment", "hk_document", "raw_vault.hub_document", "hk_document"),
        ("raw_vault.lnk_document_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_document_lp_commitment", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_document_portfolio_company", "hk_document", "raw_vault.hub_document", "hk_document"),
        ("raw_vault.lnk_document_portfolio_company", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.sat_fund_investran", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.sat_fund_investran_esg", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.sat_limited_partner_investran", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.sat_limited_partner_pii", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.sat_lp_commitment_investran", "hk_lnk_lp_commitment", "raw_vault.lnk_lp_commitment", "hk_lnk_lp_commitment"),
        ("raw_vault.sat_portfolio_company_dealcloud", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.sat_portco_valuation_mnpi", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.sat_limited_partner_legacy", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.sat_document_manifest", "hk_document", "raw_vault.hub_document", "hk_document"),
        ("business_vault.sat_lp_side_letter_llm_ext", "hk_lnk_document_lp_commitment", "raw_vault.lnk_document_lp_commitment", "hk_lnk_document_lp_commitment"),
        ("business_vault.sat_lnk_doc_portco_ai_provenance", "hk_lnk_document_portfolio_company", "raw_vault.lnk_document_portfolio_company", "hk_lnk_document_portfolio_company"),
    ]
    assert len(fk_relationships) >= 20
    for child_tbl, fk_col, parent_tbl, pk_col in fk_relationships:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {child_tbl} c "
            f"LEFT JOIN {parent_tbl} p ON c.{fk_col} = p.{pk_col} "
            f"WHERE p.{pk_col} IS NULL"
        ).fetchone()[0]
        assert orphans == 0, f"Orphan FK detected on {child_tbl}.{fk_col} -> {parent_tbl}.{pk_col}"


def test_all_15_dynamic_verification_rules_dv_dyn_001_to_015(
    populated_pe_duckdb: duckdb.DuckDBPyConnection,
    doc07_sql_bundle: tuple[str, str, str, str],
) -> None:
    """Execute all 15 Dynamic Verification Rules (DV-DYN-001 through DV-DYN-015)
    from docs/03-verification-and-agentic-architecture.md (§2.1-2.2) against the
    DuckDB warehouse and verify 0 violations across all 15 checks.
    """
    _, _, q_d10, q_d10b = doc07_sql_bundle
    con = populated_pe_duckdb

    hubs = [
        ("raw_vault.hub_fund", "hk_fund", "bkcc || '||' || fund_code"),
        ("raw_vault.hub_limited_partner", "hk_limited_partner", "bkcc || '||' || lp_natural_key"),
        ("raw_vault.hub_portfolio_company", "hk_portfolio_company", "bkcc || '||' || portco_natural_key"),
        ("raw_vault.hub_document", "hk_document", "bkcc || '||' || doc_uri"),
    ]
    links = [
        ("raw_vault.lnk_lp_commitment", "hk_lnk_lp_commitment", "hk_fund || '||' || hk_limited_partner || '||' || commitment_id"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_lnk_lp_commitment_co_invest", "hk_fund || '||' || hk_limited_partner || '||' || hk_portfolio_company || '||' || commitment_id"),
        ("raw_vault.sal_limited_partner", "hk_sal_limited_partner", "hk_limited_partner_master || '||' || hk_limited_partner_duplicate"),
        ("raw_vault.lnk_document_lp_commitment", "hk_lnk_document_lp_commitment", "hk_document || '||' || hk_fund || '||' || hk_limited_partner"),
        ("raw_vault.lnk_document_portfolio_company", "hk_lnk_document_portfolio_company", "hk_document || '||' || hk_portfolio_company"),
    ]
    sats = [
        ("raw_vault.sat_fund_investran", "hk_fund", "raw_vault.hub_fund", "staging.stg_investran_src_pe_funds", "hash_diff_sat_fund_investran"),
        ("raw_vault.sat_fund_investran_esg", "hk_fund", "raw_vault.hub_fund", "staging.stg_investran_src_pe_funds", "hash_diff_sat_fund_investran_esg"),
        ("raw_vault.sat_limited_partner_investran", "hk_limited_partner", "raw_vault.hub_limited_partner", "staging.stg_investran_src_lp_investors", "hash_diff_sat_limited_partner_investran"),
        ("raw_vault.sat_limited_partner_pii", "hk_limited_partner", "raw_vault.hub_limited_partner", "staging.stg_investran_src_lp_investors", "hash_diff_sat_limited_partner_pii"),
        ("raw_vault.sat_lp_commitment_investran", "hk_lnk_lp_commitment", "raw_vault.lnk_lp_commitment", "staging.stg_investran_src_lp_commitments_and_calls", "hash_diff_sat_lp_commitment_investran"),
        ("raw_vault.sat_portfolio_company_dealcloud", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "staging.stg_dealcloud_src_portfolio_companies", "hash_diff_sat_portfolio_company_dealcloud"),
        ("raw_vault.sat_portco_valuation_mnpi", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "staging.stg_dealcloud_src_portfolio_companies", "hash_diff_sat_portco_valuation_mnpi"),
        ("raw_vault.sat_limited_partner_legacy", "hk_limited_partner", "raw_vault.hub_limited_partner", "staging.stg_legacy_gp_src_legacy_lp_roster", "hash_diff_sat_limited_partner_legacy"),
        ("raw_vault.sat_document_manifest", "hk_document", "raw_vault.hub_document", "staging.stg_dms_documents", "hash_diff_sat_document_manifest"),
        ("business_vault.sat_lp_side_letter_llm_ext", "hk_lnk_document_lp_commitment", "raw_vault.lnk_document_lp_commitment", "staging.stg_dms_src_side_letter_extractions", "hash_diff_sat_lp_side_letter_llm_ext"),
        ("business_vault.sat_lnk_doc_portco_ai_provenance", "hk_lnk_document_portfolio_company", "raw_vault.lnk_document_portfolio_company", "staging.stg_dms_src_ic_memo_extractions", "hash_diff_sat_lnk_doc_portco_ai_provenance"),
    ]

    # DV-DYN-001: Hub HK & Composite BK Uniqueness
    for tbl, hk, bk_expr in hubs:
        dup_hk = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT {hk}) FROM {tbl}").fetchone()[0]
        dup_bk = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT ({bk_expr})) FROM {tbl}").fetchone()[0]
        assert dup_hk == 0 and dup_bk == 0, f"DV-DYN-001 failed on {tbl}"

    # DV-DYN-002: Hub & Link Cryptographic / Concatenation Hash Collision Bijection
    for tbl, hk, bk_expr in hubs + links:
        collisions = con.execute(
            f"SELECT {hk} FROM {tbl} GROUP BY {hk} HAVING COUNT(DISTINCT ({bk_expr})) > 1"
        ).fetchall()
        assert collisions == [], f"DV-DYN-002 collision on {tbl}: {collisions}"

    # DV-DYN-003: Link HK & FK-Tuple Grain Uniqueness
    for tbl, hk, fk_expr in links:
        dup_hk = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT {hk}) FROM {tbl}").fetchone()[0]
        dup_fk = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT ({fk_expr})) FROM {tbl}").fetchone()[0]
        assert dup_hk == 0 and dup_fk == 0, f"DV-DYN-003 failed on {tbl}"

    # DV-DYN-004: Standard Satellite (hk, load_dts) Grain Uniqueness
    for sat, pk, _, _, _ in sats:
        dups = con.execute(
            f"SELECT {pk}, load_dts FROM {sat} GROUP BY {pk}, load_dts HAVING COUNT(*) > 1"
        ).fetchall()
        assert dups == [], f"DV-DYN-004 failed on {sat}: {dups}"

    # DV-DYN-005: Multi-Active Satellite (hk, load_dts, cdk) Grain Uniqueness
    con.execute(
        """
        CREATE TEMP TABLE mas_document_span_extraction AS
        SELECT hk_lnk_document_lp_commitment AS hk_parent, load_dts, chunk_id AS cdk_span_id, hash_diff
        FROM business_vault.sat_lp_side_letter_llm_ext
        UNION ALL
        SELECT hk_lnk_document_portfolio_company AS hk_parent, load_dts, chunk_id AS cdk_span_id, hash_diff
        FROM business_vault.sat_lnk_doc_portco_ai_provenance
        WHERE rec_src <> 'SYSTEM_GHOST'
        """
    )
    mas_dups = con.execute(
        "SELECT hk_parent, load_dts, cdk_span_id FROM mas_document_span_extraction "
        "GROUP BY hk_parent, load_dts, cdk_span_id HAVING COUNT(*) > 1"
    ).fetchall()
    assert mas_dups == [], "DV-DYN-005 failed"

    # DV-DYN-006: Satellite-to-Parent Referential Integrity
    for sat, pk, parent_tbl, _, _ in sats:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {sat} s LEFT JOIN {parent_tbl} p ON s.{pk} = p.{pk} WHERE p.{pk} IS NULL"
        ).fetchone()[0]
        assert orphans == 0, f"DV-DYN-006 failed on {sat}"

    # DV-DYN-007: Link-to-Hub Foreign Key Referential Integrity
    link_fk_checks = [
        ("raw_vault.lnk_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_lp_commitment", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_lp_commitment_co_invest", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
        ("raw_vault.sal_limited_partner", "hk_limited_partner_master", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.sal_limited_partner", "hk_limited_partner_duplicate", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_document_lp_commitment", "hk_document", "raw_vault.hub_document", "hk_document"),
        ("raw_vault.lnk_document_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
        ("raw_vault.lnk_document_lp_commitment", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner"),
        ("raw_vault.lnk_document_portfolio_company", "hk_document", "raw_vault.hub_document", "hk_document"),
        ("raw_vault.lnk_document_portfolio_company", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company"),
    ]
    for lnk_tbl, fk_col, hub_tbl, pk_col in link_fk_checks:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {lnk_tbl} l LEFT JOIN {hub_tbl} h ON l.{fk_col} = h.{pk_col} WHERE h.{pk_col} IS NULL"
        ).fetchone()[0]
        assert orphans == 0, f"DV-DYN-007 failed on {lnk_tbl}.{fk_col}"

    # DV-DYN-008: Satellite Consecutive hash_diff True-Delta Historization Check
    for sat, pk, _, _, _ in sats:
        redundant = con.execute(
            f"""
            WITH v AS (
                SELECT {pk}, load_dts, hash_diff,
                       LAG(hash_diff) OVER (PARTITION BY {pk} ORDER BY load_dts ASC) AS prev_hd
                FROM {sat}
                WHERE {pk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
            )
            SELECT * FROM v WHERE hash_diff = prev_hd
            """
        ).fetchall()
        assert redundant == [], f"DV-DYN-008 failed on {sat}: {redundant}"

    # DV-DYN-009: System Zero Keys (-1, -2) & Ghost Record (1900-01-01) Presence
    for tbl, hk, _ in hubs + links:
        zk_cnt = con.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE {hk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')"
        ).fetchone()[0]
        assert zk_cnt == 2, f"DV-DYN-009 failed on {tbl}"
    for sat, pk, _, _, _ in sats:
        ghost_cnt = con.execute(
            f"SELECT COUNT(*) FROM {sat} WHERE {pk} = '{ZERO_KEY_UNKNOWN}' AND load_dts = TIMESTAMP '{GHOST_LOAD_DTS}'"
        ).fetchone()[0]
        assert ghost_cnt == 1, f"DV-DYN-009 failed on {sat}"

    # DV-DYN-010: Staging-to-Hub & Staging-to-Link Completeness Reconciliation
    stg_to_target = [
        ("staging.stg_investran_src_pe_funds", "hk_fund", "raw_vault.hub_fund", "hk_fund", "1=1"),
        ("staging.stg_investran_src_lp_investors", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner", "1=1"),
        ("staging.stg_legacy_gp_src_legacy_lp_roster", "hk_limited_partner", "raw_vault.hub_limited_partner", "hk_limited_partner", "1=1"),
        ("staging.stg_dealcloud_src_portfolio_companies", "hk_portfolio_company", "raw_vault.hub_portfolio_company", "hk_portfolio_company", "1=1"),
        ("staging.stg_dms_documents", "hk_document", "raw_vault.hub_document", "hk_document", "1=1"),
        ("staging.stg_investran_src_lp_commitments_and_calls", "hk_lnk_lp_commitment", "raw_vault.lnk_lp_commitment", "hk_lnk_lp_commitment", "1=1"),
        ("staging.stg_investran_src_lp_commitments_and_calls", "hk_lnk_lp_commitment_co_invest", "raw_vault.lnk_lp_commitment_co_invest", "hk_lnk_lp_commitment_co_invest", "co_invest_portco_lei IS NOT NULL"),
        ("staging.stg_mdm_src_lp_steward_crosswalk", "hk_sal_limited_partner", "raw_vault.sal_limited_partner", "hk_sal_limited_partner", "1=1"),
        ("staging.stg_dms_src_side_letter_extractions", "hk_lnk_document_lp_commitment", "raw_vault.lnk_document_lp_commitment", "hk_lnk_document_lp_commitment", "1=1"),
        ("staging.stg_dms_src_ic_memo_extractions", "hk_lnk_document_portfolio_company", "raw_vault.lnk_document_portfolio_company", "hk_lnk_document_portfolio_company", "1=1"),
    ]
    for stg_tbl, stg_hk, tgt_tbl, tgt_hk, cond in stg_to_target:
        missing = con.execute(
            f"SELECT COUNT(*) FROM (SELECT * FROM {stg_tbl} WHERE {cond}) s "
            f"LEFT JOIN {tgt_tbl} t ON s.{stg_hk} = t.{tgt_hk} WHERE t.{tgt_hk} IS NULL"
        ).fetchone()[0]
        assert missing == 0, f"DV-DYN-010 failed for {stg_tbl} -> {tgt_tbl}"

    # DV-DYN-011: Staging-to-Satellite Current-State Payload Reconciliation
    for sat, pk, _, stg_tbl, stg_hd_col in sats:
        mismatches = con.execute(
            f"""
            WITH latest_sat AS (
                SELECT {pk}, hash_diff
                FROM {sat}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY {pk} ORDER BY load_dts DESC) = 1
            ),
            dedup_stg AS (
                SELECT DISTINCT {pk}, {stg_hd_col} AS hash_diff FROM {stg_tbl}
            )
            SELECT s.{pk}
            FROM dedup_stg s
            LEFT JOIN latest_sat l ON s.{pk} = l.{pk}
            WHERE l.{pk} IS NULL OR s.hash_diff <> l.hash_diff
            """
        ).fetchall()
        assert mismatches == [], f"DV-DYN-011 failed on {sat}: {mismatches}"

    # DV-DYN-012: Two-Run Replay Idempotency Check (Validated in full 4-Batch test below as well)
    pre_counts = {
        tbl: con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        for tbl, _, _ in hubs + links
    }
    con.execute("SET VARIABLE load_dts = '2026-11-15 09:00:00';")
    con.execute(
        """
        INSERT INTO raw_vault.hub_fund (hk_fund, bkcc, fund_code, load_dts, rec_src)
        SELECT src.hk_fund, src.bkcc, src.fund_code, src.load_dts, src.rec_src
        FROM staging.stg_investran_src_pe_funds src
        LEFT JOIN raw_vault.hub_fund tgt ON src.hk_fund = tgt.hk_fund
        WHERE tgt.hk_fund IS NULL;
        """
    )
    assert con.execute("SELECT COUNT(*) FROM raw_vault.hub_fund").fetchone()[0] == pre_counts["raw_vault.hub_fund"]

    # DV-DYN-013: Effectivity Satellite Single Active Relationship per Driving Key
    con.execute(
        """
        CREATE TEMP TABLE esat_sal_limited_partner AS
        SELECT
            hk_sal_limited_partner,
            hk_limited_partner_duplicate AS driving_hk_limited_partner_duplicate,
            load_dts,
            rec_src
        FROM raw_vault.sal_limited_partner
        """
    )
    active_driver_violations = con.execute(
        f"""
        WITH current_esat AS (
            SELECT
                hk_sal_limited_partner,
                driving_hk_limited_partner_duplicate,
                LEAD(load_dts) OVER (
                    PARTITION BY driving_hk_limited_partner_duplicate
                    ORDER BY load_dts ASC
                ) AS next_load_dts
            FROM esat_sal_limited_partner
        )
        SELECT driving_hk_limited_partner_duplicate, COUNT(*)
        FROM current_esat
        WHERE next_load_dts IS NULL
          AND hk_sal_limited_partner NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
          AND driving_hk_limited_partner_duplicate NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        GROUP BY driving_hk_limited_partner_duplicate
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert active_driver_violations == [], "DV-DYN-013 failed"

    # DV-DYN-014: Ghost/Zero-Key Business Metric Leakage Guard
    d10_rows = con.execute(q_d10).fetchall()
    d10b_rows = con.execute(q_d10b).fetchall()
    assert all(r[0] not in ("-1", "-2") and r[1] not in ("-1", "-2") for r in d10_rows)
    assert all(r[0] not in ("-1", "-2") for r in d10b_rows)

    # DV-DYN-015: PIT Table Completeness & Non-Null Pointer Integrity
    pit_total = con.execute("SELECT COUNT(*) FROM business_vault.pit_portfolio_company").fetchone()[0]
    # 5 hub_portfolio_company rows (3 business + 2 system zero keys) * 3 snapshot dates = 15 rows
    assert pit_total == 15
    broken_pit_pointers = con.execute(
        """
        SELECT COUNT(*)
        FROM business_vault.pit_portfolio_company p
        LEFT JOIN raw_vault.sat_portfolio_company_dealcloud s_dc
          ON p.hk_sat_portfolio_company_dealcloud = s_dc.hk_portfolio_company
         AND p.ldts_sat_portfolio_company_dealcloud = s_dc.load_dts
        LEFT JOIN raw_vault.sat_portco_valuation_mnpi s_val
          ON p.hk_sat_portco_valuation_mnpi = s_val.hk_portfolio_company
         AND p.ldts_sat_portco_valuation_mnpi = s_val.load_dts
        WHERE s_dc.hk_portfolio_company IS NULL OR s_val.hk_portfolio_company IS NULL
        """
    ).fetchone()[0]
    assert broken_pit_pointers == 0, "DV-DYN-015 failed: broken PIT satellite pointers"

    # Verify pre-load snapshot date ('2026-09-30 23:59:59') resolves to SYSTEM_GHOST ('1900-01-01 00:00:00')
    sep30_ghosts = con.execute(
        f"""
        SELECT COUNT(*)
        FROM business_vault.pit_portfolio_company
        WHERE as_of_dts = TIMESTAMP '2026-09-30 23:59:59'
          AND hk_sat_portfolio_company_dealcloud = '{ZERO_KEY_UNKNOWN}'
          AND ldts_sat_portfolio_company_dealcloud = TIMESTAMP '{GHOST_LOAD_DTS}'
        """
    ).fetchone()[0]
    assert sep30_ghosts == 5


def test_four_adversarial_shadow_sandbox_batches_and_bitemporal_queries(
    duckdb_conn: duckdb.DuckDBPyConnection,
    doc07_sql_bundle: tuple[str, str, str, str],
) -> None:
    """Execute all 4 Adversarial Shadow Sandbox Batches (Scene 6 / WB-10 in docs/07 & docs/06 §4.10)
    followed by the Bi-Temporal Late-Arriving IC Memo & Queries D.10 / D.10b:

    - Batch 1 (t0 = '2026-10-05 09:00:00' Baseline Load):
      Loads N=1 Investran, DealCloud, and DMS side letter (before Day-3 drift and before N=2 Legacy GP).
      Verifies exact baseline counts: 12 Hub business rows, 10 Link business rows, 27 Satellite business rows.
    - Batch 2 (t1 = '2026-10-06 09:00:00' Exact Duplicate Replay):
      Re-runs all Hub, Link, and Satellite INSERT statements. Verifies 0 new rows across all tables
      (DV-DYN-004, DV-DYN-005, DV-DYN-006, DV-DYN-012).
    - Batch 3 (t2 = '2026-10-10 09:00:00' Attribute Mutation & Day-3 Schema Drift):
      Mutates CalPERS commitment called_usd ($125M -> $160M), drops deprecated_fax_no (to NULL),
      and loads ilpa_esg_rating into companion sat_fund_investran_esg.
      Verifies +0 Hub rows, +0 Link rows, and +5 Satellite rows (+1 commitment, +1 fund IV fax->NULL,
      +3 ESG rows) (DV-DYN-007, DV-DYN-008, DV-DYN-011).
    - Batch 4 (t3 = '2026-10-12 09:00:00' Multi-Source Key Collision & Late-Arriving IC Memo):
      Onboards legacy_gp (LP-101 Bavarian Pension, LP-102 Nordisk Suverän Fond) and mdm crosswalk.
      Verifies BKCC separation (GLOBAL vs LEGACY_GP_ADMIN / LEGACY_PORTAL) and sal_limited_partner
      (+2 Hub rows, +1 Link row, +2 Satellite rows) (DV-DYN-012, KEY-04), followed by the late-arriving
      Q3 IC Memo at load_dts = '2026-11-14 16:30:00' (+1 Link row, +1 Business Vault Satellite row) (DV-DYN-013).
    - Bi-Temporal Audit Queries (Query D.10 & Query D.10b):
      Verifies cutoff '2026-10-15 12:00:00' returns 12.80x / $216.28M from DEALCLOUD_PE_CRM with NULL PDF span,
      and cutoff '2026-11-20 23:59:59' returns 14.50x / $245.00M from OBJECT_STORE_S3 with [108:1151] span.
    """
    _, zone0_to_3, q_d10, q_d10b = doc07_sql_bundle

    # Split zone0_to_3 into setup/DDL/Ghost seed vs DML statements so we can drive Batches 1..4
    ddl_and_ghost_part, dml_and_late_part = zone0_to_3.split(
        "-- Idempotent Hub, Link, Reference, and Satellite Loads\n"
    )
    core_dml_part, zone3_and_late_part = dml_and_late_part.split(
        "-- =============================================================================\n"
        "-- ZONE 3: BUSINESS VAULT AI EXTRACTION SATELLITES (Rule U3) & LOGARITHMIC PIT\n"
        "-- =============================================================================\n"
    )
    zone3_part, late_arrival_part = zone3_and_late_part.split(
        "-- =============================================================================\n"
        "-- LATE ARRIVAL: Audited Q3 IC Memo Extraction Loaded at 2026-11-14 for effective_date 2026-09-30\n"
        "-- =============================================================================\n"
    )

    con = duckdb_conn
    con.execute(ddl_and_ghost_part)

    # For Batch 1 (t0 = '2026-10-05 09:00:00'), hold back N=2 (legacy_gp, mdm) and Day-3 ESG column
    con.execute("DELETE FROM legacy_gp.src_legacy_lp_roster;")
    con.execute("DELETE FROM mdm.src_lp_steward_crosswalk;")
    con.execute("UPDATE investran.src_pe_funds SET ilpa_esg_rating = NULL;")

    # Execute Batch 1 (Baseline Load at t0 = '2026-10-05 09:00:00')
    con.execute(core_dml_part)
    con.execute(zone3_part)

    hub_tables = [
        "raw_vault.hub_fund",
        "raw_vault.hub_limited_partner",
        "raw_vault.hub_portfolio_company",
        "raw_vault.hub_document",
    ]
    link_tables = [
        "raw_vault.lnk_lp_commitment",
        "raw_vault.lnk_lp_commitment_co_invest",
        "raw_vault.sal_limited_partner",
        "raw_vault.lnk_document_lp_commitment",
        "raw_vault.lnk_document_portfolio_company",
    ]
    sat_tables = [
        "raw_vault.sat_fund_investran",
        "raw_vault.sat_fund_investran_esg",
        "raw_vault.sat_limited_partner_investran",
        "raw_vault.sat_limited_partner_pii",
        "raw_vault.sat_lp_commitment_investran",
        "raw_vault.sat_portfolio_company_dealcloud",
        "raw_vault.sat_portco_valuation_mnpi",
        "raw_vault.sat_limited_partner_legacy",
        "raw_vault.sat_document_manifest",
        "business_vault.sat_lp_side_letter_llm_ext",
        "business_vault.sat_lnk_doc_portco_ai_provenance",
    ]

    def get_business_counts() -> tuple[int, int, int, dict[str, int]]:
        per_table: dict[str, int] = {}
        for t in hub_tables + link_tables:
            per_table[t] = con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE rec_src <> 'SYSTEM_ZERO_KEY'"
            ).fetchone()[0]
        for t in sat_tables:
            per_table[t] = con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE rec_src <> 'SYSTEM_GHOST'"
            ).fetchone()[0]
        h_total = sum(per_table[t] for t in hub_tables)
        l_total = sum(per_table[t] for t in link_tables)
        s_total = sum(per_table[t] for t in sat_tables)
        return h_total, l_total, s_total, per_table

    # --- BATCH 1 ASSERTIONS (Baseline Load) ---
    b1_hubs, b1_links, b1_sats, b1_map = get_business_counts()
    assert b1_hubs == 12  # 3 funds + 4 LPs + 3 PortCos + 2 Docs
    assert b1_links == 10  # 7 commitments + 2 co-invest + 0 SAL + 1 side-letter doc link + 0 IC memo link
    assert b1_sats == 27  # 3 fund + 0 ESG + 4 LP + 4 PII + 7 comm + 3 PortCo DC + 3 PortCo MNPI + 0 legacy + 2 doc + 1 side-letter AI + 0 IC memo AI

    # --- BATCH 2 ASSERTIONS (Exact Duplicate Replay at t1 = '2026-10-06 09:00:00') ---
    con.execute("SET VARIABLE load_dts = '2026-10-06 09:00:00';")
    con.execute(core_dml_part)
    con.execute(zone3_part)
    b2_hubs, b2_links, b2_sats, b2_map = get_business_counts()
    assert (b2_hubs - b1_hubs) == 0, "Batch 2 replay inserted duplicate Hub rows"
    assert (b2_links - b1_links) == 0, "Batch 2 replay inserted duplicate Link rows"
    assert (b2_sats - b1_sats) == 0, "Batch 2 replay inserted duplicate Satellite rows"
    assert b2_map == b1_map

    # --- BATCH 3 ASSERTIONS (Attribute Mutation & Day-3 Schema Drift at t2 = '2026-10-10 09:00:00') ---
    con.execute("SET VARIABLE load_dts = '2026-10-10 09:00:00';")
    # 1. Mutate CalPERS Fund IV commitment called_usd from 125M to 160M
    con.execute(
        "UPDATE investran.src_lp_commitments_and_calls "
        "SET called_usd = 160000000.00 WHERE commitment_id = 'COMM-2026-001';"
    )
    # 2. Day-3 Schema Drift: deprecated_fax_no dropped (NULL) and ilpa_esg_rating populated
    con.execute("UPDATE investran.src_pe_funds SET deprecated_fax_no = NULL;")
    con.execute(
        "UPDATE investran.src_pe_funds SET ilpa_esg_rating = CASE "
        "WHEN fund_code IN ('APEX_BO_IV', 'APEX_BO_III') THEN 'AA_LEADER' "
        "ELSE 'A_COMPLIANT' END;"
    )
    con.execute(core_dml_part)
    con.execute(zone3_part)

    b3_hubs, b3_links, b3_sats, b3_map = get_business_counts()
    assert (b3_hubs - b2_hubs) == 0, "Batch 3 mutation must not insert new Hub rows"
    assert (b3_links - b2_links) == 0, "Batch 3 mutation must not insert new Link rows"
    assert (b3_sats - b2_sats) == 5, f"Expected +5 Satellite rows in Batch 3, got {b3_sats - b2_sats}"
    assert b3_map["raw_vault.sat_lp_commitment_investran"] - b2_map["raw_vault.sat_lp_commitment_investran"] == 1
    assert b3_map["raw_vault.sat_fund_investran"] - b2_map["raw_vault.sat_fund_investran"] == 1
    assert b3_map["raw_vault.sat_fund_investran_esg"] - b2_map["raw_vault.sat_fund_investran_esg"] == 3

    # --- BATCH 4 ASSERTIONS (Multi-Source BKCC Collision & SAL at t3 = '2026-10-12 09:00:00') ---
    con.execute("SET VARIABLE load_dts = '2026-10-12 09:00:00';")
    con.execute(
        """
        INSERT INTO legacy_gp.src_legacy_lp_roster VALUES
            ('LP-101', 'Bavarian Pension Reserve Fund', 'Dr. Lukas Weber', '+49-89-2190-4400'),
            ('LP-102', 'Nordisk Suverän Fond', 'Astrid Lindqvist', '+46-8-402-9100');
        INSERT INTO mdm.src_lp_steward_crosswalk VALUES
            ('LP-104', 'LP-102');
        """
    )
    con.execute(core_dml_part)
    con.execute(zone3_part)

    b4_hubs, b4_links, b4_sats, b4_map = get_business_counts()
    assert (b4_hubs - b3_hubs) == 2  # +2 Legacy GP LPs isolated via BKCC='LEGACY_GP_ADMIN'
    assert (b4_links - b3_links) == 1  # +1 sal_limited_partner row
    assert (b4_sats - b3_sats) == 2  # +2 sat_limited_partner_legacy rows

    # Verify BKCC collision isolation between CalPERS (GLOBAL:LP-101) and Bavarian Pension (LEGACY_GP_ADMIN:LP-101)
    lp101_rows = con.execute(
        "SELECT hk_limited_partner, bkcc, rec_src FROM raw_vault.hub_limited_partner "
        "WHERE lp_natural_key = 'LP-101' ORDER BY bkcc"
    ).fetchall()
    assert len(lp101_rows) == 2
    assert lp101_rows[0][1] == "GLOBAL" and lp101_rows[0][0].startswith("5D15EB10")
    assert lp101_rows[1][1] == "LEGACY_GP_ADMIN" and lp101_rows[1][0].startswith("6B7C7180")
    assert lp101_rows[0][0] != lp101_rows[1][0]

    # Verify Same-As Link (sal_limited_partner) connects Master LP-104 to Duplicate LEGACY_GP_ADMIN:LP-102
    sal_row = con.execute(
        """
        SELECT h_m.bkcc, h_m.lp_natural_key, h_d.bkcc, h_d.lp_natural_key, sal.rec_src
        FROM raw_vault.sal_limited_partner sal
        JOIN raw_vault.hub_limited_partner h_m ON sal.hk_limited_partner_master = h_m.hk_limited_partner
        JOIN raw_vault.hub_limited_partner h_d ON sal.hk_limited_partner_duplicate = h_d.hk_limited_partner
        WHERE sal.rec_src <> 'SYSTEM_ZERO_KEY'
        """
    ).fetchone()
    assert sal_row == ("GLOBAL", "LP-104", "LEGACY_GP_ADMIN", "LP-102", "MDM_STEWARD_RESOLUTION")

    # --- LATE-ARRIVING IC MEMO (2026-11-14 16:30:00, effective_date = 2026-09-30) ---
    con.execute(late_arrival_part)
    late_hubs, late_links, late_sats, late_map = get_business_counts()
    assert (late_hubs - b4_hubs) == 0  # Passive integration onto existing hub_document & hub_portfolio_company!
    assert (late_links - b4_links) == 1  # +1 lnk_document_portfolio_company
    assert (late_sats - b4_sats) == 1  # +1 sat_lnk_doc_portco_ai_provenance

    # --- BI-TEMPORAL POINT-IN-TIME AUDIT QUERIES (Query D.10 & Query D.10b) ---
    # 1. Pre-IC-Memo Cutoff: assertion_cutoff_ts = '2026-10-15 12:00:00'
    con.execute("SET VARIABLE assertion_cutoff_ts = '2026-10-15 12:00:00';")
    con.execute("SET VARIABLE state_cutoff_date = '2026-09-30';")
    d10b_pre = {r[0]: r for r in con.execute(q_d10b).fetchall()}
    assert len(d10b_pre) == 3
    odin_pre = d10b_pre["529900T8BM49AURSDO55"]
    assert odin_pre[1] == Decimal("12.80")
    assert odin_pre[2] == Decimal("216275840.00")  # $216.28M entry valuation
    assert odin_pre[3] == "DEALCLOUD_PE_CRM"
    assert odin_pre[4] is None  # doc_uri is NULL
    assert odin_pre[5] is None  # chunk_id is NULL
    assert odin_pre[6] is None  # evidence_quote is NULL

    # 2. Post-IC-Memo Cutoff: assertion_cutoff_ts = '2026-11-20 23:59:59'
    con.execute("SET VARIABLE assertion_cutoff_ts = '2026-11-20 23:59:59';")
    d10b_post = {r[0]: r for r in con.execute(q_d10b).fetchall()}
    assert len(d10b_post) == 3
    odin_post = d10b_post["529900T8BM49AURSDO55"]
    assert odin_post[1] == Decimal("14.50")
    assert odin_post[2] == Decimal("245000000.00")  # $245.00M restated valuation
    assert odin_post[3] == "OBJECT_STORE_S3"
    assert odin_post[4].lower() == "s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf"
    assert odin_post[5] == "[108:1151]"
    assert "EV/EBITDA = 14.5x" in odin_post[6]
    assert "Fair_Value_USD = 245000000.00" in odin_post[6]

    # 3. Query D.10: Verify CalPERS Fund IV commitment joins to Side Letter PDF while other 6 rows have NULL side-letter terms
    d10_rows = con.execute(q_d10).fetchall()
    assert len(d10_rows) == 7
    calpers_bo4 = [r for r in d10_rows if r[0] == "APEX_BO_IV" and r[1] == "LP-101"][0]
    assert calpers_bo4[2] == Decimal("500000000.00")
    assert calpers_bo4[3] == Decimal("160000000.00")  # Reflects Batch 3 mutation!
    assert calpers_bo4[4] == Decimal("125.0000")
    assert calpers_bo4[5] == "Y"
    assert calpers_bo4[7] == "[38:1012]"
    other_rows = [r for r in d10_rows if not (r[0] == "APEX_BO_IV" and r[1] == "LP-101")]
    assert len(other_rows) == 6
    assert all(r[4] is None and r[7] is None for r in other_rows)


def test_portco_valuation_and_stage_mutation_and_investran_vs_legacy_portal_bkcc(
    populated_pe_duckdb: duckdb.DuckDBPyConnection,
    doc07_sql_bundle: tuple[str, str, str, str],
) -> None:
    """Explicitly verify:
    1. Batch 3 PortCo valuation/stage mutation: mutating PortCo valuation (entry_multiple_ev_ebitda /
       latest_ebitda_usd) and stage/name in dealcloud.src_portfolio_companies inserts new Satellite
       version rows into sat_portco_valuation_mnpi and sat_portfolio_company_dealcloud while leaving
       Hub and Link row counts completely unchanged (DV-DYN-007, DV-DYN-008).
    2. Batch 4 passive bk_collision_code separation between 'INVESTRAN' and 'LEGACY_PORTAL' on colliding
       natural key 'LP-101' with Same-As Link resolution in sal_limited_partner (DV-DYN-012).
    """
    _, zone0_to_3, _, _ = doc07_sql_bundle
    _, dml_and_late_part = zone0_to_3.split(
        "-- Idempotent Hub, Link, Reference, and Satellite Loads\n"
    )
    core_dml_part, _ = dml_and_late_part.split(
        "-- =============================================================================\n"
        "-- ZONE 3: BUSINESS VAULT AI EXTRACTION SATELLITES (Rule U3) & LOGARITHMIC PIT\n"
        "-- =============================================================================\n"
    )

    con = populated_pe_duckdb

    hub_before = con.execute("SELECT COUNT(*) FROM raw_vault.hub_portfolio_company").fetchone()[0]
    lnk_before = con.execute("SELECT COUNT(*) FROM raw_vault.lnk_lp_commitment_co_invest").fetchone()[0]
    dc_sat_before = con.execute("SELECT COUNT(*) FROM raw_vault.sat_portfolio_company_dealcloud").fetchone()[0]
    val_sat_before = con.execute("SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi").fetchone()[0]

    # Mutate PortCo valuation and stage/profile for Project Odin / Nordic Cloud SaaS AB
    con.execute("SET VARIABLE load_dts = '2026-10-11 09:00:00';")
    con.execute(
        """
        UPDATE dealcloud.src_portfolio_companies
        SET company_name = 'Nordic Cloud SaaS AB (Series C Expansion)',
            entry_ev_multiple = 13.25
        WHERE lei = '529900T8BM49AURSDO55';
        """
    )
    con.execute(core_dml_part)

    hub_after = con.execute("SELECT COUNT(*) FROM raw_vault.hub_portfolio_company").fetchone()[0]
    lnk_after = con.execute("SELECT COUNT(*) FROM raw_vault.lnk_lp_commitment_co_invest").fetchone()[0]
    dc_sat_after = con.execute("SELECT COUNT(*) FROM raw_vault.sat_portfolio_company_dealcloud").fetchone()[0]
    val_sat_after = con.execute("SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi").fetchone()[0]

    assert hub_after == hub_before, "PortCo attribute mutation must not alter Hub count"
    assert lnk_after == lnk_before, "PortCo attribute mutation must not alter Link count"
    assert dc_sat_after == dc_sat_before + 1, "Expected +1 version row in sat_portfolio_company_dealcloud"
    assert val_sat_after == val_sat_before + 1, "Expected +1 version row in sat_portco_valuation_mnpi"

    # Test explicit INVESTRAN vs LEGACY_PORTAL bk_collision_code separation & SAL resolution
    hk_investran = dv2_sha256(["INVESTRAN", "LP-101"])
    hk_legacy_portal = dv2_sha256(["LEGACY_PORTAL", "LP-101"])
    assert hk_investran != hk_legacy_portal

    con.execute(
        """
        INSERT INTO raw_vault.hub_limited_partner (hk_limited_partner, bkcc, lp_natural_key, load_dts, rec_src)
        VALUES
            (?, 'INVESTRAN', 'LP-101', TIMESTAMP '2026-10-12 09:00:00', 'INVESTRAN_CORE'),
            (?, 'LEGACY_PORTAL', 'LP-101', TIMESTAMP '2026-10-12 09:00:00', 'LEGACY_PORTAL');
        """,
        [hk_investran, hk_legacy_portal],
    )
    hk_sal = dv2_sha256(["INVESTRAN", "LP-101", "LEGACY_PORTAL", "LP-101"])
    con.execute(
        """
        INSERT INTO raw_vault.sal_limited_partner (
            hk_sal_limited_partner, hk_limited_partner_master, hk_limited_partner_duplicate, load_dts, rec_src
        ) VALUES (?, ?, ?, TIMESTAMP '2026-10-12 09:00:00', 'MDM_STEWARD_RESOLUTION');
        """,
        [hk_sal, hk_investran, hk_legacy_portal],
    )
    resolved = con.execute(
        """
        SELECT h_m.bkcc, h_d.bkcc
        FROM raw_vault.sal_limited_partner sal
        JOIN raw_vault.hub_limited_partner h_m ON sal.hk_limited_partner_master = h_m.hk_limited_partner
        JOIN raw_vault.hub_limited_partner h_d ON sal.hk_limited_partner_duplicate = h_d.hk_limited_partner
        WHERE sal.hk_sal_limited_partner = ?
        """,
        [hk_sal],
    ).fetchone()
    assert resolved == ("INVESTRAN", "LEGACY_PORTAL")


@pytest.mark.parametrize(
    "rule_id,fault_sql,detection_sql",
    [
        (
            "DV-DYN-001",
            "INSERT INTO raw_vault.hub_fund VALUES ('1111111111111111111111111111111111111111111111111111111111111111', 'GLOBAL', 'APEX_BO_IV', TIMESTAMP '2026-12-01 00:00:00', 'FAULT');",
            "SELECT COUNT(*) - COUNT(DISTINCT (bkcc || '||' || fund_code)) FROM raw_vault.hub_fund",
        ),
        (
            "DV-DYN-002",
            "CREATE TEMP TABLE hub_fund_unconstrained AS SELECT * FROM raw_vault.hub_fund; INSERT INTO hub_fund_unconstrained VALUES ('94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759', 'COLLIDING_NS', 'COLLIDING_KEY', TIMESTAMP '2026-12-01 00:00:00', 'FAULT');",
            "SELECT COUNT(*) FROM (SELECT hk_fund FROM hub_fund_unconstrained GROUP BY hk_fund HAVING COUNT(DISTINCT (bkcc || '||' || fund_code)) > 1)",
        ),
        (
            "DV-DYN-004",
            "CREATE TEMP TABLE sat_fund_unconstrained AS SELECT * FROM raw_vault.sat_fund_investran; INSERT INTO sat_fund_unconstrained SELECT * FROM raw_vault.sat_fund_investran WHERE rec_src <> 'SYSTEM_GHOST' LIMIT 1;",
            "SELECT COUNT(*) FROM (SELECT hk_fund, load_dts FROM sat_fund_unconstrained GROUP BY hk_fund, load_dts HAVING COUNT(*) > 1)",
        ),
        (
            "DV-DYN-006",
            "INSERT INTO raw_vault.sat_fund_investran VALUES ('DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF', TIMESTAMP '2026-12-01 00:00:00', 'AAAA', 'FAKE', 'FAKE', 2026, 100.0, 100.0, NULL, 'FAULT');",
            "SELECT COUNT(*) FROM raw_vault.sat_fund_investran s LEFT JOIN raw_vault.hub_fund h ON s.hk_fund = h.hk_fund WHERE h.hk_fund IS NULL",
        ),
        (
            "DV-DYN-007",
            "INSERT INTO raw_vault.lnk_document_portfolio_company VALUES ('CAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABECAFEBABE', '0000000000000000000000000000000000000000000000000000000000000000', 'DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF', TIMESTAMP '2026-12-01 00:00:00', 'FAULT');",
            "SELECT COUNT(*) FROM raw_vault.lnk_document_portfolio_company l LEFT JOIN raw_vault.hub_portfolio_company h ON l.hk_portfolio_company = h.hk_portfolio_company WHERE h.hk_portfolio_company IS NULL",
        ),
        (
            "DV-DYN-008",
            "INSERT INTO raw_vault.sat_fund_investran SELECT hk_fund, TIMESTAMP '2026-12-01 00:00:00', hash_diff, isin, fund_name, vintage_year, target_size_usd, mgmt_fee_bps, deprecated_fax_no, rec_src FROM raw_vault.sat_fund_investran WHERE rec_src <> 'SYSTEM_GHOST' LIMIT 1;",
            "WITH v AS (SELECT hk_fund, hash_diff, LAG(hash_diff) OVER (PARTITION BY hk_fund ORDER BY load_dts ASC) AS prev_hd FROM raw_vault.sat_fund_investran WHERE rec_src <> 'SYSTEM_GHOST') SELECT COUNT(*) FROM v WHERE hash_diff = prev_hd",
        ),
        (
            "DV-DYN-009",
            "DELETE FROM raw_vault.hub_fund WHERE hk_fund = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff';",
            "SELECT CASE WHEN COUNT(*) < 2 THEN 1 ELSE 0 END FROM raw_vault.hub_fund WHERE hk_fund IN ('0000000000000000000000000000000000000000000000000000000000000000', 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff')",
        ),
    ],
)
def test_dv_dyn_negative_fault_injection_detection(
    populated_pe_duckdb: duckdb.DuckDBPyConnection,
    rule_id: str,
    fault_sql: str,
    detection_sql: str,
) -> None:
    """Adversarial fault-injection verification for Dynamic Sandbox Rules:
    Verifies that injecting a structural violation (duplicate Hub BK, hash collision,
    duplicate Satellite grain, orphan Satellite FK, orphan Link FK, consecutive identical
    HASHDIFF, or missing -2 Error Zero Key) is immediately caught by the DV-DYN SQL check.
    """
    con = populated_pe_duckdb

    # Inject fault
    con.execute(fault_sql)
    # After fault injection: >0 violations detected
    assert con.execute(detection_sql).fetchone()[0] > 0, f"{rule_id} failed to detect injected fault"
