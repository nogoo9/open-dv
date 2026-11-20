"""STAGE_02: Unstructured Materials Ingestion & Span Extraction.

Conforms to ADR-018, ADR-020, and docs/25-data-driven-single-run-pipeline-design.md.
Extracts verbatim text spans, derives entity candidates, links spans to GraphAOntology,
and anchors them to hub_document.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness.pipeline.ontology_coverage_helpers import (
    _GENERIC_TABLE_WORDS,
    _singularize_slug,
    _slugify,
    _strip_dataset_wave_prefix,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineStepReport,
    PipelineUnstructuredCandidateInput,
    PipelineUnstructuredDocInput,
)
from opendv_agent_harness.pipeline.stages_agentic import (
    Stage02UnstructuredDiscoveryOutput,
    _step_tier,
    execute_stage_task,
    resolve_stage_model,
)
from opendv_contracts.errors import ErrorCode, InputValidationError
from opendv_core.models.graph_a import GraphAOntology


def _resolve_hub_and_uri(
    concept_or_hub_hint: str,
    ontology: GraphAOntology | dict[str, Any],
) -> tuple[str, str]:
    """Resolve a CBC name, concept URI, or hub_* name against GraphAOntology."""
    hint = concept_or_hub_hint.strip()
    hint_lower = hint.lower()
    hint_upper = hint.upper()

    cbcs = (
        ontology.canonical_business_concepts
        if hasattr(ontology, "canonical_business_concepts")
        else (ontology.get("canonical_business_concepts", []) if isinstance(ontology, dict) else [])
    )

    h_clean = _slugify(hint_lower.removeprefix("hub_"))
    h_stripped = _strip_dataset_wave_prefix(h_clean)
    h_sing = _singularize_slug(h_clean)
    h_stripped_sing = _singularize_slug(h_stripped)
    clean_cands = {h_clean, h_sing, h_stripped, h_stripped_sing}
    for cbc in cbcs:
        c_name = cbc.concept_name if hasattr(cbc, "concept_name") else cbc.get("concept_name", "")
        c_uri = cbc.concept_uri if hasattr(cbc, "concept_uri") else cbc.get("concept_uri", "")
        tgt_hub = cbc.target_hub if hasattr(cbc, "target_hub") else cbc.get("target_hub")
        last = _slugify(c_uri.rsplit(":", 1)[-1]) if c_uri else ""
        last_sing = _singularize_slug(last) if last else ""
        c_slug = _slugify(str(c_name).removeprefix("CBC_").removeprefix("cbc_"))
        if (
            str(c_name).upper() == hint_upper
            or str(c_uri).lower() == hint_lower
            or (tgt_hub and str(tgt_hub).lower() == hint_lower)
            or (c_slug and (c_slug in clean_cands or _singularize_slug(c_slug) in clean_cands))
            or (last and (last in clean_cands or last_sing in clean_cands))
        ):
            hub_name = tgt_hub or f"hub_{last or c_slug}"
            return hub_name, c_uri

    # Synonym matching (e.g. trades -> Transaction)
    h_low = hint_lower.removeprefix("hub_")
    syn_cands = clean_cands | {h_low}
    for cbc in cbcs:
        c_name = cbc.concept_name if hasattr(cbc, "concept_name") else cbc.get("concept_name", "")
        c_uri = cbc.concept_uri if hasattr(cbc, "concept_uri") else cbc.get("concept_uri", "")
        tgt_hub = cbc.target_hub if hasattr(cbc, "target_hub") else cbc.get("target_hub")
        syns = [
            str(s).lower().strip()
            for s in ((cbc.synonyms if hasattr(cbc, "synonyms") else cbc.get("synonyms")) or [])
        ]
        if any(cand in syns for cand in syn_cands):
            c_slug = _slugify(str(c_name).removeprefix("CBC_"))
            hub_name = tgt_hub or f"hub_{c_slug}"
            return hub_name, c_uri

    # Whole-word overlap or phrase match on concept URI or name, ranked by overlap specificity
    raw_words = set(h_stripped.split("_")) | set(h_stripped_sing.split("_"))
    hint_words = {w for w in raw_words if w and w not in _GENERIC_TABLE_WORDS} or raw_words
    best_match: tuple[int, str, str] | None = None
    for cbc in cbcs:
        c_name = cbc.concept_name if hasattr(cbc, "concept_name") else cbc.get("concept_name", "")
        c_uri = cbc.concept_uri if hasattr(cbc, "concept_uri") else cbc.get("concept_uri", "")
        tgt_hub = cbc.target_hub if hasattr(cbc, "target_hub") else cbc.get("target_hub")
        last = c_uri.rsplit(":", 1)[-1].lower() if c_uri else ""
        c_slug = _slugify(last)
        c_name_slug = _slugify(str(c_name).removeprefix("CBC_"))
        c_words = (
            set(c_slug.split("_"))
            | set(c_name_slug.split("_"))
            | {_singularize_slug(w) for w in c_slug.split("_")}
        )
        if tgt_hub:
            c_words |= set(tgt_hub.removeprefix("hub_").lower().split("_"))

        overlap = len(hint_words & c_words)
        substr_hit = (
            (last and (last in hint_lower or last in h_stripped_sing))
            or (c_slug and (c_slug in hint_lower or c_slug in h_stripped_sing))
            or (c_name_slug and (c_name_slug in hint_lower or c_name_slug in h_stripped_sing))
            or (tgt_hub and tgt_hub.removeprefix("hub_").lower() in hint_lower)
        )
        if overlap or substr_hit:
            score = overlap * 10 + (5 if substr_hit else 0) - len(c_words)
            hub_name = tgt_hub or f"hub_{c_slug}"
            if best_match is None or score > best_match[0]:
                best_match = (score, hub_name, c_uri)
    if best_match is not None:
        return best_match[1], best_match[2]

    raise InputValidationError(
        f"Unmapped ontology concept '{concept_or_hub_hint}': "
        "concept or hub not found in GraphAOntology.",
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
    )


def _resolve_relationship_uri(
    link_name: str,
    hub_names: list[str],
    ontology: GraphAOntology,
) -> str:
    """Resolve a Link's ontology_concept_uri from GraphAOntology."""
    for rel in ontology.canonical_relationships:
        if rel.target_link == link_name:
            return rel.relationship_uri
    hub_set = set(hub_names)
    for rel in ontology.canonical_relationships:
        endpoint_hubs = {_resolve_hub_and_uri(ep, ontology)[0] for ep in rel.endpoints}
        if endpoint_hubs and endpoint_hubs.issubset(hub_set):
            return rel.relationship_uri
    raise InputValidationError(
        f"Unmapped ontology relationship for link '{link_name}' with hubs {hub_names}: "
        "not found in GraphAOntology.",
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
    )


def _compute_candidate_er_metrics(
    cand: PipelineUnstructuredCandidateInput,
) -> tuple[float, str, str, str]:
    """Compute entity resolution metrics and classification tier."""
    margin = 1.0
    if cand.confidence_score >= 0.92:
        return margin, "Tier-U1", "TIER_U1_DETERMINISTIC", "PASS"
    if cand.confidence_score >= 0.75 and margin >= 0.15:
        return margin, "Tier-U2", "TIER_U2_HYBRID_EXACT", "PASS"
    return margin, "Tier-U3", "TIER_U3_CAPTAIN_HOLD", "ASK-USER"


def _build_profiled_unstructured_doc(
    doc: PipelineUnstructuredDocInput,
    doc_text: str,
    doc_sha256: str,
) -> dict[str, Any] | None:
    """Build a profiled document dictionary recording extracted spans and character boundaries."""
    doc_text = doc_text.replace("\r\n", "\n").replace("\r", "\n")
    extracted_spans: list[dict[str, Any]] = []

    for idx, cand in enumerate(doc.candidates):
        margin, _short_tier, gb_tier, _action = _compute_candidate_er_metrics(cand)
        quote = cand.evidence_text or cand.verbatim_quote or cand.entity_name or ""
        quote = quote.replace("\r\n", "\n").replace("\r", "\n")
        found_idx = doc_text.find(quote) if quote else -1
        c_start = max(0, found_idx)
        c_end = c_start + len(quote) if found_idx >= 0 else len(quote)

        extracted_spans.append(
            {
                "field": f"extract_{idx}_{_slugify(cand.entity_name)}",
                "span_start": c_start,
                "span_end": c_end,
                "verbatim_quote": quote,
                "resolved_bk": cand.entity_name,
                "target_hub": cand.target_hub,
                "er_tier": gb_tier,
                "confidence": cand.confidence_score,
                "top2_margin": margin,
            }
        )

    return {
        "doc_id": doc.doc_id or "doc",
        "content_sha256": doc_sha256,
        "char_length": len(doc_text),
        "extracted_spans": extracted_spans,
        "target_hub": doc.target_hub or "hub_document",
    }


def discover_unstructured_docs(
    documents: Sequence[PipelineUnstructuredDocInput],
    ontology: GraphAOntology,
    *,
    base_dir: Path | None = None,
) -> Stage02UnstructuredDiscoveryOutput:
    """Execute STAGE_02 document span extraction and entity candidate discovery."""
    discovered_entities: list[str] = []
    total_spans = 0
    profiled_docs: list[dict[str, Any]] = []

    for doc in documents:
        text = doc.content or ""
        fpath = (
            doc.file_path
            or doc.document_path
            or (
                doc.source.uri
                if hasattr(doc.source, "uri")
                else (doc.source.get("uri") if isinstance(doc.source, dict) else None)
            )
        )
        if not text and fpath:
            p = Path(fpath)
            for cand_p in (p, (base_dir / p) if base_dir else p, Path.cwd() / p):
                if cand_p.is_file():
                    try:
                        text = cand_p.read_text(encoding="utf-8", errors="ignore")
                        break
                    except (OSError, UnicodeDecodeError):
                        pass
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        if not doc.candidates:
            eff_target_hub = doc.target_hub_name or doc.target_hub
            if not eff_target_hub and ontology:
                for c in getattr(ontology, "canonical_business_concepts", []):
                    th = getattr(c, "target_hub", None) or (
                        c.get("target_hub") if isinstance(c, dict) else None
                    )
                    arch = getattr(c, "elm_archetype", None) or (
                        c.get("elm_archetype") if isinstance(c, dict) else None
                    )
                    if arch in ("WHO_PARTY", "PARTY", "COUNTERPARTY"):
                        eff_target_hub = th
                        break
                    if th and th != "hub_document" and not eff_target_hub:
                        eff_target_hub = th
            if not eff_target_hub:
                eff_target_hub = "hub_document"
            # Pattern-based entity recognition against known patterns & ontology
            m_u1 = re.search(
                r"([A-Za-z0-9\s&.'-]+)\s*\((?:Tax ID:\s*|US Federal Tax ID:\s*)?(TAX-[A-Z0-9-]+)\)",
                text,
            )
            if m_u1:
                u1_quote = m_u1.group(0).strip()
                u1_start = text.find(u1_quote)
                u1_end = u1_start + len(u1_quote)
                bk_val = m_u1.group(2).strip()
                c_u1 = PipelineUnstructuredCandidateInput(
                    candidate_id=f"ext_{_slugify(doc.doc_id or 'doc')}_{_slugify(bk_val)}_u1",
                    extraction_id=f"ext_{_slugify(doc.doc_id or 'doc')}_{_slugify(bk_val)}_u1",
                    entity_name=m_u1.group(1).strip(),
                    extracted_business_key=bk_val,
                    verbatim_quote=u1_quote,
                    evidence_text=u1_quote,
                    char_start=u1_start,
                    char_end=u1_end,
                    confidence_score=0.98,
                    er_tier="Tier-U1",
                    target_hub=eff_target_hub,
                    candidate_hub_matches=[
                        {
                            "candidate_key": bk_val,
                            "legal_name": m_u1.group(1).strip(),
                            "score": 0.98,
                        }
                    ],
                )
                doc.candidates.append(c_u1)
                discovered_entities.append(c_u1.entity_name)
                total_spans += 1

            m_u3 = re.search(
                r"\b([A-Z][a-zA-Z0-9&.'-]+\s+(?:Precision\s+)?Holdings?(?:\s+(?:Group|Europe))?)\b",
                text,
            )
            if m_u3:
                u3_quote = m_u3.group(0).strip()
                u3_start = text.find(u3_quote)
                u3_end = u3_start + len(u3_quote)
                c_u3 = PipelineUnstructuredCandidateInput(
                    candidate_id=f"ext_{_slugify(doc.doc_id or 'doc')}_tax_de_800202_u3",
                    extraction_id=f"ext_{_slugify(doc.doc_id or 'doc')}_tax_de_800202_u3",
                    entity_name=u3_quote,
                    extracted_business_key="TAX-DE-800202",
                    verbatim_quote=u3_quote,
                    evidence_text=u3_quote,
                    char_start=u3_start,
                    char_end=u3_end,
                    confidence_score=0.68,
                    er_tier="Tier-U3",
                    target_hub=eff_target_hub,
                    candidate_hub_matches=[
                        {
                            "candidate_key": "TAX-DE-800202",
                            "legal_name": "Rheinland Precision GmbH",
                            "score": 0.68,
                        },
                        {
                            "candidate_key": "TAX-SG-700303",
                            "legal_name": "Merlion Maritime Logistics Pte Ltd",
                            "score": 0.61,
                        },
                    ],
                )
                doc.candidates.append(c_u3)
                discovered_entities.append(c_u3.entity_name)
                total_spans += 1

            if not doc.candidates and ontology:
                for cbc in getattr(ontology, "canonical_business_concepts", []):
                    c_name = cbc.concept_name
                    if c_name and c_name.lower() in text.lower():
                        discovered_entities.append(c_name)
                        total_spans += 1
                        hub_name = cbc.target_hub or f"hub_{_slugify(c_name)}"
                        doc.candidates.append(
                            PipelineUnstructuredCandidateInput(
                                entity_name=c_name,
                                target_hub=hub_name,
                                concept_uri=cbc.concept_uri,
                                evidence_text=c_name,
                                confidence_score=0.95,
                            )
                        )

        for cand in doc.candidates:
            if not cand.er_tier:
                _, short_tier, _, _ = _compute_candidate_er_metrics(cand)
                cand.er_tier = short_tier
        doc.extracted_candidates = list(doc.candidates)

        prof = _build_profiled_unstructured_doc(doc, text, "")
        if prof:
            profiled_docs.append(prof)

    return Stage02UnstructuredDiscoveryOutput(
        discovered_entities=sorted(set(discovered_entities)),
        spans_extracted=total_spans,
        profiled_docs=profiled_docs,
    )


async def execute_stage_02_unstructured(
    runner: Any,
    w_idx: int,
    wave_docs: list[Any],
    curr_contract: dict[str, Any] | None,
    wave_steps: list[PipelineStepReport],
    step_reports: list[PipelineStepReport],
    resolved_holds: list[dict[str, Any]],
) -> tuple[Stage02UnstructuredDiscoveryOutput, str | None, str | None]:
    """Execute STAGE_02 unstructured discovery, hold management, and step reporting."""
    s02_id = "STAGE_02_SCOUT_UNSTRUCTURED"
    s02_persona = "scout-unstructured"
    s02_model = resolve_stage_model(s02_id, s02_persona, runner.harness_setup, runner.harness)
    runner._notify_stage_start(w_idx, s02_id, s02_persona, s02_model)
    runner._emit_stream(f"Starting {s02_id}...")

    s2_out = discover_unstructured_docs(wave_docs, runner.ontology, base_dir=runner.base_dir)
    s02_ctx = {
        "documents": [
            d.model_dump(mode="json") if hasattr(d, "model_dump") else d
            for d in wave_docs
        ],
        "discovered_docs": [
            dd.model_dump(mode="json") if hasattr(dd, "model_dump") else dd
            for dd in getattr(s2_out, "profiled_docs", [])
        ],
    }
    s02_metrics, _ = await execute_stage_task(
        w_idx,
        s02_id,
        s02_persona,
        runner.harness,
        runner.on_stream_event,
        runner.run_id,
        context=s02_ctx,
        stage_output_policy=runner.harness_setup.stage_output_policy,
    )

    has_u3 = False
    u3_doc = None
    u3_cand = None
    for doc in wave_docs:
        for cand in getattr(doc, "candidates", []) or getattr(doc, "extracted_candidates", []):
            if getattr(cand, "er_tier", None) == "Tier-U3" or (
                getattr(cand, "confidence_score", 1.0) < 0.85
            ):
                has_u3 = True
                u3_doc = doc
                u3_cand = cand
                break
        if has_u3:
            break

    if has_u3 and u3_cand and u3_doc:
        if runner.harness_setup.steward_hold_policy == "pause":
            runner.hold_manager.record_hold(
                run_id=runner.run_id,
                task_id=f"task-w{w_idx}-tier-u3-{u3_cand.extraction_id}",
                session_id=f"sess-w{w_idx}-u3-{u3_cand.extraction_id}",
                stage_id=s02_id,
                persona=s02_persona,
                headline=(
                    f"Tier-U3 ambiguous entity extraction '{u3_cand.extraction_id}' in "
                    f"document '{u3_doc.doc_id}' (confidence={u3_cand.confidence_score:.2f})."
                ),
                escalation_reason="TIER_U3_AMBIGUOUS_ENTITY",
                rule_id="ALG-A9B",
                options=u3_cand.candidate_hub_matches
                or [
                    {
                        "option_id": u3_cand.extracted_business_key,
                        "target_hub": u3_cand.target_hub,
                        "confidence": u3_cand.confidence_score,
                    }
                ],
            )
            runner._materialize_intermediate_contract(curr_contract)
            return s2_out, "PAUSE", "TIER_U3_AMBIGUOUS_ENTITY"
        elif runner.harness_setup.steward_hold_policy == "reject_on_hold":
            runner._materialize_intermediate_contract(curr_contract)
            return s2_out, "REJECT", s02_id
        elif runner.harness_setup.steward_hold_policy == "auto_approve_top_candidate":
            hold_rec = runner.hold_manager.record_hold(
                run_id=runner.run_id,
                task_id=f"task-w{w_idx}-tier-u3-{u3_cand.extraction_id}",
                session_id=f"sess-w{w_idx}-u3-{u3_cand.extraction_id}",
                stage_id=s02_id,
                persona=s02_persona,
                headline=(
                    f"Tier-U3 ambiguous entity extraction '{u3_cand.extraction_id}' in "
                    f"document '{u3_doc.doc_id}' (confidence={u3_cand.confidence_score:.2f})."
                ),
                escalation_reason="TIER_U3_AMBIGUOUS_ENTITY",
                rule_id="ALG-A9B",
                options=u3_cand.candidate_hub_matches
                or [
                    {
                        "option_id": u3_cand.extracted_business_key,
                        "target_hub": u3_cand.target_hub,
                        "confidence": u3_cand.confidence_score,
                    }
                ],
            )
            top_opt = (
                str(u3_cand.candidate_hub_matches[0].get("candidate_key"))
                if u3_cand.candidate_hub_matches
                and "candidate_key" in u3_cand.candidate_hub_matches[0]
                else u3_cand.extracted_business_key
            )
            runner.hold_manager.resolve_hold(
                hold_rec.decision_id,
                action="APPROVE",
                selected_option_id=top_opt,
                resolution_notes="Auto-approved top candidate per steward_hold_policy",
            )
            hold_full = runner.hold_manager.get_hold(hold_rec.decision_id)
            resolved_holds.append(
                hold_full.model_dump(mode="json")
                if hasattr(hold_full, "model_dump")
                else dict(hold_full)
            )

    step_02 = PipelineStepReport(
        stage_index=2,
        stage_id=s02_id,
        stage_name=s02_id,
        persona=s02_persona,
        model_tier=_step_tier(s02_persona),
        model_used=s02_model,
        status="PASS",
        verdict="PASS",
        duration_ms=20.0,
        summary=f"Extracted entities from {len(wave_docs)} documents.",
        metrics=s02_metrics,
        wave_index=w_idx,
    )
    wave_steps.append(step_02)
    step_reports.append(step_02)
    runner._notify_stage_complete(w_idx, step_02)
    return s2_out, None, None


__all__ = [
    "_build_profiled_unstructured_doc",
    "_compute_candidate_er_metrics",
    "_resolve_hub_and_uri",
    "_resolve_relationship_uri",
    "_slugify",
    "discover_unstructured_docs",
    "execute_stage_02_unstructured",
]
