"""Gate V7 Semantic Critic and Gate V8 Frontier Horizon Critic runners and compact prompts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import Field

from opendv.agents.pack_loader import load_agent_pack
from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    RunEventSink,
    enforce_v7_tier_lock,
)
from opendv.models.diagnostics import AntiPatternJustification, StrictModel
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy, HarnessBudget, ModelTier
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DecimalType
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.verifier.ports import VerifierReport

CompactColumnTuple = tuple[str, str, str, int | None, str | None, float, str | None]

_ABSORBED_NATIVE_PREFIXES: tuple[str, ...] = (
    "ABSORBED_NATIVELY",
    "sat_*_ext",
    "sal_*",
    "sat_*_pii",
    "EVO-02",
    "EVO-03",
)
_ABSORBED_NATIVE_RE = re.compile(
    r"(?i)(?:\bABSORBED_NATIVELY\b|\bEVO-0[23]\b|\bsat_[a-z0-9_]+_ext(?:_v\d+)?\b"
    r"|\bsal_[a-z0-9_]+\b|\bsat_[a-z0-9_]+_pii(?:_v\d+)?\b)"
)


def _matches_absorbed_native(item: str, absorbed_natively: Sequence[str]) -> bool:
    if _ABSORBED_NATIVE_RE.search(item):
        return True
    for pat in (*_ABSORBED_NATIVE_PREFIXES, *absorbed_natively):
        clean = pat.strip()
        if not clean:
            continue
        if "*" in clean or "?" in clean:
            body = re.escape(clean).replace(r"\*", r"[A-Za-z0-9_]*").replace(r"\?", r"[A-Za-z0-9_]")
            if re.search(rf"(?i)(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", item) or clean in item:
                return True
        elif re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(clean)}(?![A-Za-z0-9_])", item):
            return True
    return False


class FragilitySurfaceSummary(StrictModel):
    """Zero-token deterministic summary of immutable wave-1 commitments for V8."""

    hub_bks: list[str] = Field(default_factory=list)
    link_arities: list[str] = Field(default_factory=list)
    single_active_sats: list[str] = Field(default_factory=list)
    narrow_numeric_cols: list[str] = Field(default_factory=list)
    bitemporal_gaps: list[str] = Field(default_factory=list)
    candidate_alt_bks: list[str] = Field(default_factory=list)


def build_compact_7tuple_summary(profiles: Sequence[SourceProfile]) -> list[CompactColumnTuple]:
    """Serialize Graph B columns as compact 7-tuples safe for 200+ table critic prompts."""
    tuples: list[CompactColumnTuple] = []
    for prof in sorted(profiles, key=lambda p: p.source_id):
        for tbl in sorted(prof.tables, key=lambda t: t.table_id):
            for col in tbl.columns:
                pii_tag = col.pii_tags[0] if col.pii_tags else None
                tuples.append(
                    (
                        tbl.table_id,
                        col.col_name,
                        col.sql_type,
                        col.pk_ord,
                        col.fk_ref,
                        round(col.null_ratio, 4),
                        pii_tag,
                    )
                )
    return tuples


def scan_fragility_surface(
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None = None,
) -> FragilitySurfaceSummary:
    """Deterministically extract immutable locks and fragility surfaces (0 LLM tokens)."""
    hub_bks = [
        f"{h.hub_name}({h.key_tier}):{','.join(b.col_name for b in h.business_keys)}"
        for h in ir.hubs
        if not h.is_planned_concept
    ]
    link_arities = [
        f"{lnk.link_name}[{lnk.link_kind}]:"
        + ",".join(f"{p.role_name}->{p.hub_name}" for p in lnk.participants)
        for lnk in ir.links
    ]
    single_active_sats = [
        f"{s.sat_name}->{s.parent_name}"
        for s in ir.satellites
        if s.sat_kind == "STANDARD" and not s.child_dependent_keys
    ]
    narrow_numeric_cols: list[str] = []
    bitemporal_gaps: list[str] = []
    for sat in ir.satellites:
        for attr in sat.attributes:
            if attr.logical_type == "INT32" or (
                isinstance(attr.logical_type, DecimalType) and attr.logical_type.precision < 18
            ):
                narrow_numeric_cols.append(f"{sat.sat_name}.{attr.name}")
        if sat.sat_kind == "STANDARD" and not sat.is_bitemporal and not sat.applied_dts_col:
            bitemporal_gaps.append(sat.sat_name)

    modeled_bks = {b.col_name for h in ir.hubs for b in h.business_keys}
    candidate_alt_bks: list[str] = []
    for prof in profiles:
        for tbl in prof.tables:
            for col in tbl.columns:
                if (
                    (col.is_pk or col.col_name.endswith(("_isin", "_lei", "_cik", "_id")))
                    and col.col_name not in modeled_bks
                ):
                    candidate_alt_bks.append(f"{tbl.table_id}.{col.col_name}")

    if locks is not None:
        for hname, cols in sorted(locks.locked_hub_bks.items()):
            hub_bks.append(f"LOCKED:{hname}:{','.join(cols)}")

    return FragilitySurfaceSummary(
        hub_bks=hub_bks[:64],
        link_arities=link_arities[:64],
        single_active_sats=single_active_sats[:64],
        narrow_numeric_cols=narrow_numeric_cols[:64],
        bitemporal_gaps=bitemporal_gaps[:32],
        candidate_alt_bks=candidate_alt_bks[:64],
    )


def _compact_ir_summary(ir: CertifiedVaultIR) -> dict[str, Any]:
    return {
        "project_id": ir.project_id,
        "wave_number": ir.wave_number,
        "ontology_version": ir.ontology_version,
        "hubs": [
            {
                "hub_name": h.hub_name,
                "key_tier": h.key_tier,
                "bks": [b.col_name for b in h.business_keys],
                "is_planned": h.is_planned_concept,
            }
            for h in ir.hubs
        ],
        "links": [
            {
                "link_name": lnk.link_name,
                "kind": lnk.link_kind,
                "participants": [p.hub_name for p in lnk.participants],
            }
            for lnk in ir.links
        ],
        "satellites": [
            {
                "sat_name": s.sat_name,
                "kind": s.sat_kind,
                "parent": s.parent_name,
                "cdks": s.child_dependent_keys,
                "driving_keys": s.driving_keys,
                "attrs": [a.name for a in s.attributes],
            }
            for s in ir.satellites
        ],
        "justifications": [j.model_dump(mode="json") for j in ir.justifications],
    }


def build_v7_critic_prompt(
    ir: CertifiedVaultIR,
    verifier_report: VerifierReport,
    profiles: Sequence[SourceProfile],
    ontology: GraphAOntology | None,
) -> str:
    """Build an isolated V7 critic prompt using compact 7-tuples and non-L1 IR summary."""
    compact_cols = build_compact_7tuple_summary(profiles)
    payload = {
        "gate": "V7",
        "ontology_concepts": [c.concept_name for c in ontology.concepts] if ontology else [],
        "ir_summary": _compact_ir_summary(ir),
        "v6b_diagnostics": [d.model_dump(mode="json") for d in verifier_report.v6b_diagnostics],
        "graph_b_columns_7tuple": compact_cols,
    }
    return json.dumps(payload, separators=(",", ":"))


def build_v8_horizon_prompt(
    ir: CertifiedVaultIR,
    fragility: FragilitySurfaceSummary,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None,
    horizon_assumptions: Sequence[HorizonAssumptionSpec],
) -> str:
    """Build an isolated V8 Horizon prompt with FragilitySurfaceSummary and 7-tuples."""
    compact_cols = build_compact_7tuple_summary(profiles)
    payload = {
        "gate": "V8",
        "brk_classes": [f"BRK-0{i}" for i in range(1, 9)],
        "fragility_surface": fragility.model_dump(mode="json"),
        "ir_summary": _compact_ir_summary(ir),
        "evolution_locks": locks.model_dump(mode="json") if locks else None,
        "horizon_assumptions": [a.model_dump(mode="json") for a in horizon_assumptions],
        "graph_b_columns_7tuple": compact_cols,
    }
    return json.dumps(payload, separators=(",", ":"))


def audit_v6b_justifications(
    ir: CertifiedVaultIR,
    verdict: SemanticCriticVerdict,
) -> tuple[CertifiedVaultIR, list[str]]:
    """Mark V7-approved waivers reviewed_by_v7=True and clear unapproved agent claims."""
    accepted = set(verdict.accepted_waiver_ids)
    updated_justs: list[AntiPatternJustification] = []
    marked_ids: list[str] = []
    changed = False
    for just in ir.justifications:
        if just.waiver_id in accepted:
            if not just.reviewed_by_v7:
                changed = True
            updated_justs.append(just.model_copy(update={"reviewed_by_v7": True}))
            marked_ids.append(just.waiver_id)
        elif just.actor == "agent" and just.reviewed_by_v7:
            changed = True
            updated_justs.append(just.model_copy(update={"reviewed_by_v7": False}))
        else:
            updated_justs.append(just)
    if not changed and not marked_ids:
        return ir, []
    return ir.model_copy(update={"justifications": updated_justs}), marked_ids


async def run_v7_semantic_critic(
    *,
    run_id: str,
    ir: CertifiedVaultIR,
    verifier_report: VerifierReport,
    profiles: Sequence[SourceProfile],
    ontology: GraphAOntology | None,
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: AgentHarnessPort,
    v7_critic_tier: ModelTier = "FRONTIER_CRITIC",
    model_overrides: Mapping[str, str] | None = None,
    event_sink: RunEventSink | None = None,
) -> tuple[SemanticCriticVerdict, AgentTurnResult]:
    """Execute Gate V7 (@dv-semantic-critic) in an isolated zero-tool context."""
    enforce_v7_tier_lock(policy, v7_critic_tier)
    pack = load_agent_pack("dv-semantic-critic")
    prompt = build_v7_critic_prompt(ir, verifier_report, profiles, ontology)
    effective_tier: ModelTier = "FRONTIER_CRITIC" if policy == "strict" else v7_critic_tier
    ovr = model_overrides or {}
    v7_override = (ovr.get("dv-semantic-critic") or ovr.get(effective_tier) or "").strip() or None
    req = AgentTurnRequest(
        run_id=run_id,
        agent_id="dv-semantic-critic",
        step="step6",
        prompt=prompt,
        system_instructions=pack.markdown_body,
        output_schema=SemanticCriticVerdict,
        model_tier=effective_tier,
        model_override=v7_override,
        cegar_policy=policy,
        role_kind="none",
        allowed_tools=[],
        budget=budget,
        metadata={
            "model_overrides": dict(model_overrides or {}),
            "table_count": sum(len(p.tables) for p in profiles),
        },
    )
    res = await harness.run_turn(req, event_sink=event_sink)
    verdict = (
        res.structured_output
        if isinstance(res.structured_output, SemanticCriticVerdict)
        else SemanticCriticVerdict.model_validate(res.structured_output)
    )
    return verdict, res


def _normalize_horizon_verdict(verdict: HorizonCriticVerdict) -> HorizonCriticVerdict:
    """Filter native-absorbed items from action lists and enforce fast-path classification."""
    clean_watchlist = [
        item
        for item in verdict.bucket_c_watchlist
        if not _matches_absorbed_native(item, verdict.absorbed_natively)
    ]
    if verdict.score >= 85 and not verdict.bucket_a_ops and not verdict.bucket_b_questions:
        return verdict.model_copy(
            update={"classification": "REASONABLY_STABLE", "bucket_c_watchlist": clean_watchlist}
        )
    if verdict.bucket_b_questions or (verdict.score < 85 and not verdict.bucket_a_ops):
        return verdict.model_copy(
            update={
                "classification": "HUMAN_REVIEW_REQUIRED",
                "bucket_c_watchlist": clean_watchlist,
            }
        )
    if verdict.bucket_a_ops:
        return verdict.model_copy(
            update={"classification": "NEEDS_STEER", "bucket_c_watchlist": clean_watchlist}
        )
    return verdict.model_copy(update={"bucket_c_watchlist": clean_watchlist})


async def run_v8_horizon_critic(
    *,
    run_id: str,
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None,
    horizon_assumptions: Sequence[HorizonAssumptionSpec],
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: AgentHarnessPort,
    model_overrides: Mapping[str, str] | None = None,
    event_sink: RunEventSink | None = None,
) -> tuple[HorizonCriticVerdict, AgentTurnResult]:
    """Execute Gate V8 (@dv-horizon-critic) at FRONTIER_CRITIC tier with zero tools."""
    pack = load_agent_pack("dv-horizon-critic")
    fragility = scan_fragility_surface(ir, profiles, locks)
    prompt = build_v8_horizon_prompt(ir, fragility, profiles, locks, horizon_assumptions)
    ovr = model_overrides or {}
    v8_override = (
        ovr.get("dv-horizon-critic") or ovr.get("FRONTIER_CRITIC") or ""
    ).strip() or None
    req = AgentTurnRequest(
        run_id=run_id,
        agent_id="dv-horizon-critic",
        step="step6",
        prompt=prompt,
        system_instructions=pack.markdown_body,
        output_schema=HorizonCriticVerdict,
        model_tier="FRONTIER_CRITIC",
        model_override=v8_override,
        cegar_policy=policy,
        role_kind="none",
        allowed_tools=[],
        budget=budget,
        metadata={
            "model_overrides": dict(model_overrides or {}),
            "fragility_surface": fragility.model_dump(mode="json"),
            "table_count": sum(len(p.tables) for p in profiles),
        },
    )
    res = await harness.run_turn(req, event_sink=event_sink)
    raw_verdict = (
        res.structured_output
        if isinstance(res.structured_output, HorizonCriticVerdict)
        else HorizonCriticVerdict.model_validate(res.structured_output)
    )
    return _normalize_horizon_verdict(raw_verdict), res


__all__ = [
    "CompactColumnTuple",
    "FragilitySurfaceSummary",
    "audit_v6b_justifications",
    "build_compact_7tuple_summary",
    "build_v7_critic_prompt",
    "build_v8_horizon_prompt",
    "run_v7_semantic_critic",
    "run_v8_horizon_critic",
    "scan_fragility_surface",
]
