"""Algorithm A.9b: Gate 5 Unstructured Document Grounding, Exact Span Substring Verifier
& 3-Tier Entity Resolution (`ER`) Confidence Margin Router.

Enforces Data Vault 2.0 unstructured & multimodal invariants (`Rules U1-U4`):
  - `BLOB_OR_VEC_IN_RAW_SAT` (Rule U1): Prohibits binary blobs (`raw_blob`, `binary_bytes`)
    and embedding vectors (`embedding_vec`, `vector_*`) inside `STANDARD_SATELLITE`.
  - `MISSING_PROVENANCE_SAT` (Rule U3): Requires every `UNSTRUCTURED_EXTRACTION_LINK`
    to have an attached `AI_PROVENANCE_SATELLITE` declaring `extractor_model_id`,
    `confidence_score`, and `evidence_quote`.
  - `SPAN_HALLUCINATION` (Rule U2): Verifies that every extracted `evidence_quote` /
    `verbatim_quote` is an exact character-offset substring `doc_text[start:end]`
    and that `row_chunk_span` bounds all row-level extracted spans.
  - 3-Tier ER Confidence Margin Routing (Rule U4):
    - `Tier-U1` (`TIER_U1_DETERMINISTIC`): Exact regex/identifier match (`conf == 1.0`) -> `PASS`
    - `Tier-U2` (`TIER_U2_HYBRID_EXACT`): `conf >= 0.85` and `top2_margin >= 0.15` -> `PASS`
    - `Tier-U3` (`TIER_U3_CAPTAIN_HOLD`): `conf < 0.85` or `top2_margin < 0.15` -> `ASK-USER`
"""

from __future__ import annotations

from typing import Any

FORBIDDEN_RAW_SAT_BINARY_COLS: frozenset[str] = frozenset(
    {
        "raw_blob",
        "embedding_vec",
        "binary_bytes",
        "pdf_bytes",
        "vector_768d",
        "vector_1536d",
    }
)
MANDATORY_PROVENANCE_COLS: frozenset[str] = frozenset(
    {"extractor_model_id", "confidence_score", "evidence_quote"}
)


def _normalize_line_endings(text: str) -> str:
    """Normalize Windows CRLF (`\\r\\n`) and legacy CR (`\\r`) line endings to LF (`\\n`)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _span_matches_document(
    raw_body: str,
    start: int,
    end: int,
    raw_quote: str,
) -> bool:
    """Return True if `[start:end]` matches `raw_quote` after CRLF/CR -> LF normalization."""
    norm_quote = _normalize_line_endings(raw_quote)
    if not norm_quote or start < 0 or end <= start:
        return False
    norm_body = _normalize_line_endings(raw_body)
    return end <= len(norm_body) and norm_body[start:end] == norm_quote


def classify_er_resolution_tier(
    confidence_score: float,
    top2_margin: float = 1.0,
    *,
    is_exact_identifier_match: bool = False,
) -> tuple[str, str]:
    """Classify an Unstructured-to-Structured Entity Resolution candidate into
    `Tier-U1`, `Tier-U2`, or `Tier-U3` and return `(tier_name, action)`.

    - `Tier-U1`: `is_exact_identifier_match=True` or `confidence_score >= 0.999999`
      -> `("TIER_U1_DETERMINISTIC", "PASS")`
    - `Tier-U2`: `confidence_score >= 0.85` and `top2_margin >= 0.15`
      -> `("TIER_U2_HYBRID_EXACT", "PASS")`
    - `Tier-U3`: `confidence_score < 0.85` or `top2_margin < 0.15`
      -> `("TIER_U3_CAPTAIN_HOLD", "ASK-USER")`
    """
    if is_exact_identifier_match or confidence_score >= 0.999999:
        return "TIER_U1_DETERMINISTIC", "PASS"
    if confidence_score >= 0.85 and top2_margin >= 0.15:
        return "TIER_U2_HYBRID_EXACT", "PASS"
    return "TIER_U3_CAPTAIN_HOLD", "ASK-USER"


def evaluate_unstructured_er_routing(
    bronze_profile: dict[str, Any] | None,
    parsed_doc_samples: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate 3-Tier ER confidence margin routing and span bounding across all
    `unstructured_documents` in a Graph B profile.
    """
    decisions: list[dict[str, Any]] = []
    if not bronze_profile or not isinstance(bronze_profile, dict):
        return decisions

    docs_lookup = parsed_doc_samples or {}

    for sys_entry in bronze_profile.get("source_systems", []):
        if not isinstance(sys_entry, dict):
            continue
        for doc in sys_entry.get("unstructured_documents", []):
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("doc_id") or doc.get("doc_uri") or "unknown_doc")
            chunk_span = doc.get("row_chunk_span") or {}
            chunk_start = int(chunk_span.get("span_start", 0))
            chunk_end = int(chunk_span.get("span_end", 0))

            span_errors: list[str] = []
            er_spans: list[dict[str, Any]] = []
            for sp in doc.get("extracted_spans", []):
                if not isinstance(sp, dict):
                    continue
                s_start = int(sp.get("span_start", -1))
                s_end = int(sp.get("span_end", -1))
                quote = str(sp.get("verbatim_quote", ""))
                attr_name = str(sp.get("attribute_name") or sp.get("field") or "")
                declared_tier = str(sp.get("er_tier", ""))

                if (
                    chunk_end > 0
                    and declared_tier != "TIER_U3_CAPTAIN_HOLD"
                    and (s_start < chunk_start or s_end > chunk_end)
                ):
                    span_errors.append(
                        f"Span '{attr_name}' [{s_start}:{s_end}] falls outside "
                        f"row_chunk_span [{chunk_start}:{chunk_end}] in '{doc_id}'."
                    )
                if doc_id in docs_lookup:
                    raw_body = docs_lookup[doc_id]
                    norm_quote = _normalize_line_endings(quote)
                    if not _span_matches_document(raw_body, s_start, s_end, quote):
                        span_errors.append(
                            f"Span '{attr_name}' quote '{norm_quote}' != "
                            f"doc['{doc_id}'][{s_start}:{s_end}]."
                        )
                if declared_tier.startswith("TIER_U"):
                    er_spans.append(sp)

            if er_spans:
                for sp in er_spans:
                    conf = float(sp.get("confidence", sp.get("confidence_score", 0.0)))
                    margin = float(sp.get("top2_margin", 1.0))
                    cand_name = _normalize_line_endings(
                        str(sp.get("verbatim_quote") or sp.get("field") or "")
                    )
                    field_name = str(sp.get("field") or sp.get("attribute_name") or "")
                    tier, action = classify_er_resolution_tier(conf, margin)
                    diag_msg = (
                        f"ASK_USER [Gate 5 - TIER_U3_CAPTAIN_HOLD]: Document '{doc_id}' "
                        f"candidate '{cand_name}' (conf={conf:.2f}, margin={margin:.2f} < 0.15) "
                        f"routed to Captain-Hold queue."
                        if action == "ASK-USER"
                        else (
                            f"PASS [Gate 5 - {tier}]: Document '{doc_id}' candidate "
                            f"'{cand_name}' auto-bound (conf={conf:.2f}, margin={margin:.2f})."
                        )
                    )
                    decisions.append(
                        {
                            "doc_id": doc_id,
                            "field": field_name,
                            "candidate_name": cand_name,
                            "confidence_score": conf,
                            "top2_margin": margin,
                            "computed_tier": tier,
                            "action": action,
                            "span_errors": list(span_errors),
                            "diagnostic": diag_msg,
                        }
                    )
            else:
                conf = float(doc.get("er_confidence_score", 0.0))
                margin = float(doc.get("er_top2_margin", 1.0))
                cand_name = _normalize_line_endings(str(doc.get("er_candidate_lp_name", "")))
                tier, action = classify_er_resolution_tier(conf, margin)
                diag_msg = (
                    f"ASK_USER [Gate 5 - TIER_U3_CAPTAIN_HOLD]: Document '{doc_id}' "
                    f"candidate '{cand_name}' (conf={conf:.2f}, margin={margin:.2f} < 0.15) "
                    f"routed to Captain-Hold queue."
                    if action == "ASK-USER"
                    else (
                        f"PASS [Gate 5 - {tier}]: Document '{doc_id}' candidate "
                        f"'{cand_name}' auto-bound (conf={conf:.2f}, margin={margin:.2f})."
                    )
                )
                decisions.append(
                    {
                        "doc_id": doc_id,
                        "field": "document_er",
                        "candidate_name": cand_name,
                        "confidence_score": conf,
                        "top2_margin": margin,
                        "computed_tier": tier,
                        "action": action,
                        "span_errors": span_errors,
                        "diagnostic": diag_msg,
                    }
                )

    return decisions


def verify_gate5_unstructured_invariants(
    contract: dict[str, Any],
    parsed_doc_samples: dict[str, str] | None = None,
    *,
    bronze_profile: dict[str, Any] | None = None,
    enforce_er_holds_as_diagnostics: bool = False,
) -> list[str]:
    """Algorithm A.9b: Gate 5 Unstructured Document Grounding & Span Substring Verifier."""
    violations: list[str] = []
    docs_lookup = parsed_doc_samples or {}

    # 1. Rule U1: No binary blobs or embedding vectors in STANDARD_SATELLITE
    for sat in contract.get("satellites", []):
        if sat.get("satellite_type") == "STANDARD_SATELLITE":
            for col in sat.get("payload_columns", []):
                if str(col).lower() in FORBIDDEN_RAW_SAT_BINARY_COLS:
                    violations.append(
                        f"UNSTRUCTURED_VIOLATION [Gate 5 - BLOB_OR_VEC_IN_RAW_SAT]: "
                        f"Satellite '{sat.get('name')}' contains '{col}'. Move to "
                        f"DOCUMENT_POINTER_SATELLITE or VECTOR_INDEX_SATELLITE."
                    )

    # 2. Rule U3: Every UNSTRUCTURED_EXTRACTION_LINK must have an AI_PROVENANCE_SATELLITE
    prov_sats_by_parent = {
        str(s.get("parent_entity", "")): s
        for s in contract.get("satellites", [])
        if s.get("satellite_type") == "AI_PROVENANCE_SATELLITE"
    }

    for lnk in contract.get("links", []):
        if lnk.get("link_type") == "UNSTRUCTURED_EXTRACTION_LINK":
            l_name = str(lnk.get("name", ""))
            if l_name not in prov_sats_by_parent:
                violations.append(
                    f"UNSTRUCTURED_VIOLATION [Gate 5 - MISSING_PROVENANCE_SAT]: "
                    f"Unstructured link '{l_name}' lacks an AI_PROVENANCE_SATELLITE."
                )
            else:
                p_cols = set(prov_sats_by_parent[l_name].get("payload_columns", []))
                if not MANDATORY_PROVENANCE_COLS.issubset(p_cols):
                    missing = sorted(MANDATORY_PROVENANCE_COLS - p_cols)
                    violations.append(
                        f"UNSTRUCTURED_VIOLATION [Gate 5 - INCOMPLETE_PROVENANCE]: "
                        f"Provenance satellite for '{l_name}' is missing {missing}."
                    )

    # 3. Rule U2: Exact Character-Span Substring Verification on sample_extractions
    for sat in contract.get("satellites", []):
        if sat.get("satellite_type") == "AI_PROVENANCE_SATELLITE":
            s_name = str(sat.get("name", ""))
            for row in sat.get("sample_extractions", []):
                if not isinstance(row, dict):
                    continue
                doc_id = str(row.get("doc_id", ""))
                body = docs_lookup.get(doc_id, "")
                start = int(row.get("char_start", -1))
                end = int(row.get("char_end", -1))
                quote = str(row.get("evidence_quote") or row.get("verbatim_quote") or "")
                norm_quote = _normalize_line_endings(quote)
                if not _span_matches_document(body, start, end, quote):
                    violations.append(
                        f"UNSTRUCTURED_VIOLATION [Gate 5 - SPAN_HALLUCINATION]: "
                        f"Extracted quote '{norm_quote}' in '{s_name}' does not match "
                        f"exact span [{start}:{end}] of document '{doc_id}'."
                    )

                if enforce_er_holds_as_diagnostics and "confidence_score" in row:
                    conf = float(row.get("confidence_score", 0.0))
                    margin = float(row.get("top2_margin", 1.0))
                    tier, action = classify_er_resolution_tier(conf, margin)
                    if action == "ASK-USER":
                        violations.append(
                            f"ASK_USER [Gate 5 - {tier}]: Extraction in '{s_name}' "
                            f"for '{doc_id}' has conf={conf:.2f}, margin={margin:.2f} "
                            f"and requires human confirmation."
                        )

    # 4. Optional Graph B unstructured_documents span & ER hold checks
    if bronze_profile is not None:
        er_decisions = evaluate_unstructured_er_routing(bronze_profile, docs_lookup)
        seen_span_errors: set[str] = set()
        for dec in er_decisions:
            for sp_err in dec["span_errors"]:
                if sp_err not in seen_span_errors:
                    seen_span_errors.add(sp_err)
                    violations.append(
                        f"UNSTRUCTURED_VIOLATION [Gate 5 - SPAN_HALLUCINATION]: {sp_err}"
                    )
            if enforce_er_holds_as_diagnostics and dec["action"] == "ASK-USER":
                violations.append(str(dec["diagnostic"]))

    return violations
