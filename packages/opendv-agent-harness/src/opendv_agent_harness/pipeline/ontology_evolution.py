"""STAGE_03/04: Ontology Evolution, Delta Proposals & Governance Policies.

Conforms to ADR-017, ADR-018, ONT-EVO-001..003, and
docs/25-data-driven-single-run-pipeline-design.md.
Governs semantic oracle evolution across three policies:
  - strict_lock: Rejects any ontology changes, raising escalations.
  - recommend: Proposes OntologyDelta proposals for human steward review.
  - auto_accept: Automatically applies validated additive OntologyDeltas to GraphAOntology vN+1.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness.pipeline.discovery_unstructured import (
    _resolve_hub_and_uri,
    _slugify,
)
from opendv_agent_harness.pipeline.ontology_coverage_helpers import (
    _collect_ontology_coverage_index,
    _is_feed_covered_by_ontology,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineArtifactRef,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)


def resolve_ontology_policy(policy_str: str | None) -> str:
    """Normalize user-supplied ontology policy string."""
    if not policy_str:
        return "off"
    p = policy_str.strip().lower()
    if p in {"auto_accept", "auto-accept", "accept", "apply"}:
        return "auto_accept"
    if p in {"recommend", "propose", "review"}:
        return "recommend"
    if p in {"strict_lock", "strict-lock", "lock", "frozen"}:
        return "strict_lock"
    return "off"


def propose_concept_delta(
    concept_name: str,
    domain_namespace: str,
    target_hub: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Construct an additive OntologyDelta proposal dictionary."""
    uri = f"urn:ontology:entity:{concept_name.lower()}"
    hub = target_hub or f"hub_{concept_name.lower()}"
    return {
        "operation": "ADD_CONCEPT",
        "concept_name": concept_name,
        "concept_uri": uri,
        "target_hub": hub,
        "domain_namespace": domain_namespace,
        "description": description or f"Auto-discovered concept for {concept_name}",
        "attributes": [],
    }


def apply_ontology_deltas(
    ontology: GraphAOntology,
    deltas: Sequence[dict[str, Any]],
    policy: str = "auto_accept",
) -> tuple[GraphAOntology, list[dict[str, Any]], list[str]]:
    """Apply validated additive deltas to GraphAOntology, emitting updated ontology vN+1.

    Returns:
        (updated_ontology, applied_deltas, rejected_notes)
    """
    pol = resolve_ontology_policy(policy)
    if pol not in {"auto_accept", "recommend"}:
        return ontology, [], ["Ontology policy is locked or off; no deltas applied."]

    new_onto = copy.deepcopy(ontology)
    applied: list[dict[str, Any]] = []
    notes: list[str] = []

    existing_uris = {cbc.concept_uri for cbc in new_onto.canonical_business_concepts}
    existing_names = {cbc.concept_name.lower() for cbc in new_onto.canonical_business_concepts}

    for d in deltas:
        op = d.get("operation")
        if op == "ADD_CONCEPT":
            uri = str(d.get("concept_uri", ""))
            name = str(d.get("concept_name", ""))
            if uri in existing_uris or name.lower() in existing_names:
                notes.append(f"Skipping duplicate concept '{name}' ({uri}).")
                continue

            if pol == "auto_accept":
                new_cbc = CanonicalBusinessConcept(
                    concept_name=name,
                    concept_uri=uri,
                    target_hub=d.get("target_hub") or f"hub_{name.lower()}",
                    key_authority_tier=d.get("key_authority_tier", "TIER_2_ENTERPRISE_SOR"),
                    governance_classification=d.get("governance_classification", "INTERNAL"),
                )
                new_onto.canonical_business_concepts.append(new_cbc)
                existing_uris.add(uri)
                existing_names.add(name.lower())
                applied.append(d)
                notes.append(f"Auto-accepted new concept '{name}' ({uri}).")
            else:
                applied.append(d)
                notes.append(f"Recommended concept delta for review: '{name}'.")

    return new_onto, applied, notes


def emit_stage01_ontology_coverage(
    ontology: GraphAOntology | None,
    sources: Sequence[Any],
    discovered_feeds: Sequence[Any],
    output_dir: Path,
    cas_store: Any | None = None,
    wave_index: int = 0,
) -> tuple[PipelineArtifactRef | None, list[str]]:
    if not discovered_feeds:
        return None, []
    src_map = {
        getattr(s, "table_name", None) or getattr(s, "source_name", None): s for s in sources
    }
    ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets = (
        _collect_ontology_coverage_index(ontology, discovered_feeds)
    )
    mapped_tables: list[str] = []
    unmapped_tables: list[str] = []
    for f in discovered_feeds:
        s_in = src_map.get(f.table_name)
        if _is_feed_covered_by_ontology(
            f, s_in, ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets, ontology
        ):
            mapped_tables.append(f.table_name)
        else:
            unmapped_tables.append(f.table_name)
    cov_ratio = len(mapped_tables) / len(discovered_feeds)
    cov_id = f"art-w{wave_index}-stage01-ontology-coverage"
    file_name = f"wave_{wave_index}_ontology_coverage.json"
    cov_payload = {
        "artifact_id": cov_id,
        "wave_id": f"w{wave_index}",
        "coverage_ratio": cov_ratio,
        "unmapped_tables_count": len(unmapped_tables),
        "mapped_tables": mapped_tables,
        "unmapped_tables": unmapped_tables,
    }
    cov_bytes = json.dumps(cov_payload, indent=2).encode("utf-8")
    cov_sha = hashlib.sha256(cov_bytes).hexdigest()
    if cas_store is not None:
        cas_store.put(cov_bytes)
    out_file = output_dir / file_name
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(cov_bytes)
    art = PipelineArtifactRef(
        artifact_id=cov_id,
        name=file_name,
        sha256=cov_sha,
        path=str(out_file),
        file_path=str(out_file),
        artifact_type="JSON",
        media_type="application/json",
        stage_id="STAGE_01_SCOUT_SOURCE",
        byte_length=len(cov_bytes),
    )
    return art, unmapped_tables


async def _get_recommender_proposal(
    ontology: GraphAOntology | None,
    base_ontology_sha: str | None,
    req_prefix: str = "req",
    wave_id: str = "w0",
) -> Any | None:
    try:
        import inspect

        from opendv_contracts import OntologyRecommendationRequest
        from opendv_service.ontology.registry import get_ontology_recommender_registry

        reg = get_ontology_recommender_registry()
        rec_ids = reg.list_recommenders() if reg else []
        if not rec_ids:
            return None, None
        rec = reg.get_recommender(rec_ids[0])
        req = OntologyRecommendationRequest(
            request_id=f"{req_prefix}-{uuid.uuid4().hex[:8]}",
            mode="DELTA",
            wave_id=wave_id,
            base_ontology_sha256=base_ontology_sha or "",
            base_ontology=ontology.model_dump(mode="json") if ontology else {},
        )
        fn = getattr(rec, "recommend", None) or rec
        res = await fn(req) if inspect.iscoroutinefunction(fn) else fn(req)
        return res, None
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        return None, str(exc)


def _write_proposal_artifact(
    prop: Any,
    output_dir: Path,
    cas_store: Any | None,
    art_id: str,
    file_name: str,
    stage_id: str,
    wave_id: str = "w0",
) -> PipelineArtifactRef:
    payload = {
        "artifact_id": art_id,
        "wave_id": wave_id,
        "artifact_kind": "ontology_proposal",
        "proposal_id": prop.proposal_id,
        "recommender_id": prop.recommender_id,
        "confidence_score": prop.confidence_score,
        "operations_count": len(prop.delta.operations) if prop.delta else 0,
        "delta": prop.delta.model_dump(mode="json") if prop.delta else {},
    }
    b = json.dumps(payload, indent=2).encode("utf-8")
    sha = hashlib.sha256(b).hexdigest()
    if cas_store is not None:
        cas_store.put(b)
    f = output_dir / file_name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b)
    return PipelineArtifactRef(
        artifact_id=art_id,
        name=file_name,
        sha256=sha,
        path=str(f),
        file_path=str(f),
        artifact_type="JSON",
        media_type="application/json",
        stage_id=stage_id,
        byte_length=len(b),
    )


async def emit_stage01_advisory_proposal(
    ontology_policy: str | None,
    ontology: GraphAOntology | None,
    base_ontology_sha: str | None,
    output_dir: Path,
    cas_store: Any | None = None,
    wave_index: int = 0,
) -> tuple[PipelineArtifactRef | None, list[str]]:
    if str(ontology_policy).strip().lower() not in {"suggest_only", "recommend", "steward_review"}:
        return None, []
    prop, err = await _get_recommender_proposal(
        ontology, base_ontology_sha, "req", f"w{wave_index}"
    )
    r_errs = [err] if err else []
    if not prop:
        return None, r_errs
    art = _write_proposal_artifact(
        prop,
        output_dir,
        cas_store,
        f"art-w{wave_index}-stage01-ontology-proposal",
        f"wave_{wave_index}_ontology_proposal.json",
        "STAGE_01_SCOUT_SOURCE",
        f"w{wave_index}",
    )
    return art, r_errs


async def emit_stage05_ontology_proposal_and_options(
    ontology_policy: str | None,
    ontology: GraphAOntology | None,
    base_ontology_sha: str | None,
    output_dir: Path,
    cas_store: Any | None = None,
    cov_art_id: str = "art-w0-stage01-ontology-coverage",
    diag_msg: str = "Unmapped entity detected in Gate 1 (DV-G4-001)",
) -> tuple[PipelineArtifactRef | None, list[dict[str, Any]]]:
    """Emit IP-3 stage 05 proposal and enrich hold options for steward review."""
    hold_options: list[dict[str, Any]] = [
        {
            "option_id": "REJECT_WITH_GAP_REPORT",
            "coverage_artifact_id": cov_art_id,
            "diagnostic": diag_msg,
        }
    ]
    if str(ontology_policy).strip().lower() not in {"steward_review", "recommend", "suggest_only"}:
        return None, hold_options

    prop, _err = await _get_recommender_proposal(ontology, base_ontology_sha, "req-g1", "w0")
    if not prop:
        return None, hold_options

    prop_ref = _write_proposal_artifact(
        prop,
        output_dir,
        cas_store,
        "art-w0-stage05-ontology-proposal",
        "wave_0_ontology_proposal.json",
        "STAGE_05_AXI_DV_STATIC_GATES",
        "w0",
    )
    from opendv_contracts.dtos.ontology import AcceptOntologyDeltaOption, RejectOntologyDeltaOption
    from opendv_contracts.jcs import dumps_jcs_bytes

    d_bytes = dumps_jcs_bytes(prop.delta.model_dump(mode="json", exclude_none=True))
    d_sha = hashlib.sha256(d_bytes).hexdigest().lower()

    accept_opt = AcceptOntologyDeltaOption(
        delta=prop.delta,
        delta_sha256=d_sha,
        accepted_op_ids=[op.op_id for op in prop.delta.operations],
        steward_note=(
            f"Auto-prepared IP-3 proposal {prop.proposal_id} with confidence "
            f"{prop.confidence_score:.2f}."
        ),
    ).model_dump(mode="json")
    accept_opt.update(
        {
            "option_id": "ACCEPT_ONTOLOGY_DELTA",
            "proposal_id": prop.proposal_id,
            "delta_id": prop.delta.delta_id,
            "confidence_score": prop.confidence_score,
            "operations_count": len(prop.delta.operations),
        }
    )

    reject_opt = RejectOntologyDeltaOption(
        reason="Rejected by steward",
        rejected_op_ids=[op.op_id for op in prop.delta.operations],
        steward_note="",
    ).model_dump(mode="json")
    reject_opt.update({"option_id": "REJECT_ONTOLOGY_DELTA", "proposal_id": prop.proposal_id})

    hold_options.extend([accept_opt, reject_opt])
    return prop_ref, hold_options


def _make_entry(b: str, c: str, elm: str, tgt: str, st: str = "VARCHAR(255)") -> dict[str, Any]:
    return {
        "bronze_table": b,
        "column_name": c,
        "elm_classification": elm,
        "target_dv_entity": tgt,
        "source_sql_type": st,
    }


def _bks(
    b: str,
    k: list[str],
    s: str = "ONTOLOGY_SEED",
    tier: str = "TIER_2_ENTERPRISE_SOR",
    req_src: bool = False,
    bkcc: str | None = None,
) -> dict[str, Any]:
    return {
        "bronze_table": b,
        "key_columns": k,
        "record_source_code": s.upper(),
        "key_authority_tier": tier,
        "require_src_qualifier": req_src,
        "bkcc_prefix": bkcc,
    }


def _cbc_tier(cbc: Any) -> str:
    kat = getattr(cbc, "key_authority_tiers", None)
    if kat and getattr(kat, "tier_1_global_standard", None):
        return "TIER_1_GLOBAL_STANDARD"
    if kat and getattr(kat, "tier_3_local_scoped", None):
        return "TIER_3_LOCAL_SCOPED"
    return getattr(cbc, "key_authority_tier", None) or "TIER_2_ENTERPRISE_SOR"


def synthesize_ontology_seed_contract(
    ontology: GraphAOntology,
    domain_namespace: str = "default",
) -> dict[str, Any]:
    """Row 1: Synthesize a future-proof DV 2.0 contract directly from GraphAOntology (zero data)."""
    hubs: list[dict[str, Any]] = []
    satellites: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    for cbc in ontology.canonical_business_concepts:
        slug = _slugify(cbc.concept_name)
        h_name = cbc.target_hub or f"hub_{slug}"
        b_table = f"ontology_studio.{slug}"

        # 1. Resolve Key Authority Tier & Business Key Column from CBC
        kat = getattr(cbc, "key_authority_tiers", None)
        t1 = getattr(kat, "tier_1_global_standard", None) if kat else None
        t2 = getattr(kat, "tier_2_enterprise_sor", None) if kat else None
        t3 = getattr(kat, "tier_3_local_scoped", None) if kat else None

        if t1:
            tier = "TIER_1_GLOBAL_STANDARD"
            bk_col = (getattr(t1, "column_patterns", None) or [f"{slug}_id"])[0]
            bkcc = getattr(t1, "bkcc_policy", "GLOBAL") or "GLOBAL"
            req_src = False
        elif t2:
            tier = "TIER_2_ENTERPRISE_SOR"
            bk_col = (getattr(t2, "column_patterns", None) or [f"{slug}_id"])[0]
            bkcc = getattr(t2, "bkcc_policy", "GLOBAL")
            req_src = getattr(t2, "require_src_qualifier", False)
        elif t3:
            tier = "TIER_3_LOCAL_SCOPED"
            bk_col = (getattr(t3, "column_patterns", None) or [f"{slug}_id"])[0]
            bkcc = getattr(t3, "bkcc_policy", "SOURCE")
            req_src = True
        else:
            tier = getattr(cbc, "key_authority_tier", None) or "TIER_2_ENTERPRISE_SOR"
            bk_col = f"{slug}_id"
            bkcc = "GLOBAL" if tier == "TIER_1_GLOBAL_STANDARD" else None
            req_src = tier == "TIER_3_LOCAL_SCOPED"

        hubs.append(
            {
                "name": h_name,
                "ontology_concept_uri": cbc.concept_uri,
                "business_key_sources": [
                    _bks(b_table, [bk_col], "ONTOLOGY_SEED", tier, req_src, bkcc)
                ],
            }
        )
        ledger.append(_make_entry(b_table, bk_col, "BUSINESS_KEY", h_name, "VARCHAR(64)"))

        # 2. Extract Attributes & Partition via Governance Constraints (Alg A.1)
        raw_attrs = [
            a.attribute_name
            for a in (getattr(cbc, "attributes", None) or [])
            if getattr(a, "attribute_name", None) and a.attribute_name != bk_col
        ]
        if not raw_attrs:
            raw_attrs = [f"{slug}_name", f"{slug}_desc"]

        gov = getattr(cbc, "governance_constraints", None)
        pii_set = set(getattr(gov, "pii_confidential_attributes", None) or [])
        mnpi_set = set(getattr(gov, "mnpi_confidential_attributes", None) or [])
        req_split = getattr(gov, "required_satellite_split", None)

        pii_cols = [c for c in raw_attrs if c in pii_set]
        mnpi_cols = [c for c in raw_attrs if c in mnpi_set and c not in pii_set]
        std_cols = [c for c in raw_attrs if c not in pii_set and c not in mnpi_set]

        sat_plan: list[tuple[str, str, list[str]]] = []
        if std_cols or (not pii_cols and not mnpi_cols):
            sat_plan.append((f"sat_{slug}", "INTERNAL", std_cols or raw_attrs))
        if pii_cols:
            sat_plan.append((req_split or f"sat_{slug}_pii", "CONFIDENTIAL", pii_cols))
        if mnpi_cols:
            sat_plan.append(
                (req_split if not pii_cols else f"sat_{slug}_mnpi", "CONFIDENTIAL", mnpi_cols)
            )

        for sname, sec, cols in sat_plan:
            satellites.append(
                {
                    "name": sname,
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": h_name,
                    "source_bronze_table": b_table,
                    "security_classification": sec,
                    "volatility_tier": "STANDARD",
                    "payload_columns": cols,
                    "soft_deprecated_columns": [],
                }
            )
            for col in cols:
                ledger.append(_make_entry(b_table, col, "DESCRIPTIVE_PAYLOAD", sname))

    # 3. Canonical Relationships -> Links with FK Ledger Entries
    for rel in ontology.canonical_relationships:
        involved_hubs: list[str] = []
        for ep in rel.endpoints:
            try:
                h_name, _ = _resolve_hub_and_uri(ep, ontology)
                involved_hubs.append(h_name)
            except (KeyError, ValueError, AttributeError):
                pass

        if len(involved_hubs) >= 2:
            lnk_name = (
                rel.target_link
                or f"lnk_{'_'.join(h.removeprefix('hub_') for h in involved_hubs[:3])}"
            )
            hub_refs = [
                {
                    "target_hub": h,
                    "role_alias": h.removeprefix("hub_"),
                    "source_fk_columns": [f"{h.removeprefix('hub_')}_id"],
                }
                for h in involved_hubs
            ]
            lnk_b_table = f"ontology_studio.{lnk_name.removeprefix('lnk_')}"
            links.append(
                {
                    "name": lnk_name,
                    "link_type": "STANDARD_LINK",
                    "ontology_concept_uri": rel.relationship_uri,
                    "source_bronze_table": lnk_b_table,
                    "hub_references": hub_refs,
                    "degenerate_columns": [],
                }
            )
            for ref in hub_refs:
                for fk in ref["source_fk_columns"]:
                    ledger.append(
                        _make_entry(lnk_b_table, fk, "FOREIGN_KEY_REF", lnk_name, "VARCHAR(64)")
                    )

    return {
        "contract_version": "5.0",
        "domain_namespace": domain_namespace,
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": ledger,
        "hubs": hubs,
        "links": links,
        "satellites": satellites,
        "reference_tables": [],
        "unmapped_escalations": [],
    }


__all__ = [
    "apply_ontology_deltas",
    "emit_stage01_advisory_proposal",
    "emit_stage01_ontology_coverage",
    "emit_stage05_ontology_proposal_and_options",
    "propose_concept_delta",
    "resolve_ontology_policy",
    "synthesize_ontology_seed_contract",
]
