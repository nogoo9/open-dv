"""Gate V7 Semantic Critic and Gate V8 Frontier Horizon Critic runners."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from opendv.modeling.ports import (
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingPackLoader,
    ModelingTurnRequest,
    ModelingTurnResult,
    enforce_v7_tier_lock,
)
from opendv.modeling.prompts import (
    _load_pack_text,
    build_v7_prompt,
    build_v8_prompt,
    resolve_modeling_guide,
    scan_fragility_surface,
)
from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import FragilitySurfaceSummary, ModelingPreferences
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import (
    CEGAR_POLICY_SPECS,
    CegarPolicy,
    HarnessBudget,
    ModelTier,
    compute_cegar_energy,
)
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.verifier.ports import VerifierContext, VerifierReport
from opendv.verifier.v6b_antipatterns import evaluate_v6b_accounting

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
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


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


def _resolve_pack_instructions(
    agent_id: str,
    pack_loader: ModelingPackLoader | None,
) -> str:
    return _load_pack_text(agent_id, pack_loader=pack_loader)


def _resolve_guide_text(
    guide_markdown: str | None,
    pack_loader: ModelingPackLoader | None,
) -> str:
    text, _ = resolve_modeling_guide(guide_markdown, pack_loader=pack_loader)
    return text


def _extract_json_payload(raw_text: str) -> Any:
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("empty text output")
    fence_match = _JSON_FENCE_RE.search(stripped)
    candidate = fence_match.group(1) if fence_match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


def _parse_critic_verdict[TVerdict: (SemanticCriticVerdict, HorizonCriticVerdict)](
    turn_result: Any,
    schema_cls: type[TVerdict],
    *,
    agent_id: str,
    run_id: str,
) -> TVerdict:
    """Safely parse SemanticCriticVerdict or HorizonCriticVerdict from turn output."""
    raw = getattr(turn_result, "structured_output", None)
    if isinstance(raw, schema_cls):
        return raw
    try:
        if isinstance(raw, Mapping):
            return schema_cls.model_validate(raw)
        if isinstance(raw, str) and raw.strip():
            return schema_cls.model_validate(_extract_json_payload(raw))
        if raw is not None:
            return schema_cls.model_validate(raw)
        text_output = getattr(turn_result, "text_output", "")
        if isinstance(text_output, str) and text_output.strip():
            return schema_cls.model_validate(_extract_json_payload(text_output))
    except (ValidationError, ValueError, TypeError) as exc:
        raise OpenDVError(
            f"Critic '{agent_id}' returned malformed {schema_cls.__name__}: {exc}",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            run_id=run_id,
        ) from exc
    raise OpenDVError(
        f"Critic '{agent_id}' returned empty structured output for {schema_cls.__name__}.",
        error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        run_id=run_id,
    )


def _normalize_horizon_verdict(verdict: HorizonCriticVerdict) -> HorizonCriticVerdict:
    """Filter native-absorbed items from watchlist and enforce fast-path classification."""
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
    harness: ModelingHarnessPort,
    v7_critic_tier: ModelTier = "FRONTIER_CRITIC",
    model_overrides: Mapping[str, str] | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    business_context_hints: str | None = None,
    guide_markdown: str | None = None,
    pack_loader: ModelingPackLoader | None = None,
    fragility_summary: FragilitySurfaceSummary | None = None,
    horizon_assumptions: Sequence[HorizonAssumptionSpec] = (),
    prior_v8_verdict: HorizonCriticVerdict | None = None,
    event_sink: ModelingEventSink | None = None,
) -> tuple[SemanticCriticVerdict, ModelingTurnResult]:
    """Execute single-pass Gate V7 (@dv-semantic-critic) in a zero-tool context."""
    enforce_v7_tier_lock(policy, v7_critic_tier)
    effective_tier: ModelTier = "FRONTIER_CRITIC" if policy == "strict" else v7_critic_tier
    effective_guide = _resolve_guide_text(guide_markdown, pack_loader)
    sys_instructions = _resolve_pack_instructions("dv-semantic-critic", pack_loader)
    fragility = fragility_summary or scan_fragility_surface(ir, profiles)
    prefs = modeling_preferences or ModelingPreferences()
    prompt = build_v7_prompt(
        ir,
        verifier_report,
        profiles,
        ontology,
        modeling_preferences=prefs,
        business_context_hints=business_context_hints,
        guide_markdown=effective_guide,
        pack_loader=pack_loader,
        horizon_assumptions=horizon_assumptions,
        prior_v8_verdict=prior_v8_verdict,
    )
    ovr = model_overrides or {}
    v7_override = (ovr.get("dv-semantic-critic") or ovr.get(effective_tier) or "").strip() or None
    req = ModelingTurnRequest(
        run_id=run_id,
        agent_id="dv-semantic-critic",
        step="step6",
        prompt=prompt,
        system_instructions=sys_instructions,
        output_schema=SemanticCriticVerdict,
        model_tier=effective_tier,
        model_override=v7_override,
        cegar_policy=policy,
        role_kind="none",
        allowed_tools=[],
        budget=budget,
        metadata={
            "model_overrides": dict(ovr),
            "table_count": sum(len(p.tables) for p in profiles),
            "fragility_surface": fragility.model_dump(mode="json"),
        },
    )
    res = await harness.run_turn(req, event_sink=event_sink)
    verdict = _parse_critic_verdict(
        res,
        SemanticCriticVerdict,
        agent_id="dv-semantic-critic",
        run_id=run_id,
    )
    return verdict, res


async def run_v8_horizon_critic(
    *,
    run_id: str,
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None,
    horizon_assumptions: Sequence[HorizonAssumptionSpec],
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: ModelingHarnessPort,
    model_overrides: Mapping[str, str] | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    business_context_hints: str | None = None,
    guide_markdown: str | None = None,
    pack_loader: ModelingPackLoader | None = None,
    fragility_summary: FragilitySurfaceSummary | None = None,
    event_sink: ModelingEventSink | None = None,
) -> tuple[HorizonCriticVerdict, ModelingTurnResult]:
    """Execute single-pass Gate V8 (@dv-horizon-critic) at FRONTIER_CRITIC tier."""
    effective_guide = _resolve_guide_text(guide_markdown, pack_loader)
    sys_instructions = _resolve_pack_instructions("dv-horizon-critic", pack_loader)
    fragility = fragility_summary or scan_fragility_surface(ir, profiles, locks)
    prefs = modeling_preferences or ModelingPreferences()
    prompt = build_v8_prompt(
        ir,
        fragility,
        profiles,
        locks,
        horizon_assumptions,
        modeling_preferences=prefs,
        business_context_hints=business_context_hints,
        guide_markdown=effective_guide,
        pack_loader=pack_loader,
    )
    ovr = model_overrides or {}
    v8_override = (
        ovr.get("dv-horizon-critic") or ovr.get("FRONTIER_CRITIC") or ""
    ).strip() or None
    req = ModelingTurnRequest(
        run_id=run_id,
        agent_id="dv-horizon-critic",
        step="step6",
        prompt=prompt,
        system_instructions=sys_instructions,
        output_schema=HorizonCriticVerdict,
        model_tier="FRONTIER_CRITIC",
        model_override=v8_override,
        cegar_policy=policy,
        role_kind="none",
        allowed_tools=[],
        budget=budget,
        metadata={
            "model_overrides": dict(ovr),
            "fragility_surface": fragility.model_dump(mode="json"),
            "table_count": sum(len(p.tables) for p in profiles),
        },
    )
    res = await harness.run_turn(req, event_sink=event_sink)
    raw_verdict = _parse_critic_verdict(
        res,
        HorizonCriticVerdict,
        agent_id="dv-horizon-critic",
        run_id=run_id,
    )
    return _normalize_horizon_verdict(raw_verdict), res


async def evaluate_v7_and_v6b(
    *,
    run_id: str,
    draft_l1: DraftVaultIR,
    cert_ir: CertifiedVaultIR,
    report: VerifierReport,
    base_ctx: VerifierContext,
    profiles: Sequence[SourceProfile],
    ontology: GraphAOntology | None,
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: ModelingHarnessPort,
    v7_critic_tier: ModelTier,
    model_overrides: Mapping[str, str] | None,
    modeling_preferences: ModelingPreferences,
    business_context_hints: str | None,
    guide_markdown: str | None,
    pack_loader: ModelingPackLoader | None,
    horizon_assumptions: Sequence[HorizonAssumptionSpec],
    prior_v8_verdict: HorizonCriticVerdict | None,
    event_sink: ModelingEventSink | None,
) -> tuple[
    DraftVaultIR, CertifiedVaultIR, VerifierReport, SemanticCriticVerdict | None, int, int, int, str
]:
    """Run V7 critic (when eligible), audit V6b waivers, and return updated IR/report/counters."""
    pspec = CEGAR_POLICY_SPECS[policy]
    if policy == "symbolic_only" or report.v1_v6_fail_count > 0 or pspec.v7_floor is None:
        e = compute_cegar_energy(report.v1_v6_fail_count, report.v6b_fail_count, policy=policy)
        return draft_l1, cert_ir, report, None, e, 0, 0, "estimated"

    v7_v, v7_res = await run_v7_semantic_critic(
        run_id=run_id, ir=cert_ir, verifier_report=report, profiles=profiles, ontology=ontology,
        policy=policy, budget=budget, harness=harness, v7_critic_tier=v7_critic_tier,
        model_overrides=model_overrides, modeling_preferences=modeling_preferences,
        business_context_hints=business_context_hints, guide_markdown=guide_markdown,
        pack_loader=pack_loader, horizon_assumptions=horizon_assumptions,
        prior_v8_verdict=prior_v8_verdict, event_sink=event_sink,
    )
    cert_ir, acc_ids = audit_v6b_justifications(cert_ir, v7_v)
    if acc_ids:
        acc_set = set(acc_ids)
        draft_l1 = draft_l1.model_copy(update={
            "justifications": [
                j.model_copy(update={"reviewed_by_v7": True}) if j.waiver_id in acc_set else j
                for j in draft_l1.justifications
            ]
        })
    v6b_fail, waived_cnt, adv_cnt = evaluate_v6b_accounting(
        report.v6b_diagnostics, justifications=cert_ir.justifications,
        ontology_waivers=base_ctx.ontology_waivers,
    )
    energy_e = compute_cegar_energy(
        report.v1_v6_fail_count, v6b_fail, policy=policy, v7_score=v7_v.score
    )
    upd_report = report.model_copy(update={
        "v6b_fail_count": v6b_fail, "waived_v6b_count": waived_cnt, "advisory_count": adv_cnt,
        "energy_e": energy_e,
        "passed": (
            report.v1_v6_fail_count == 0 and report.deterministic_energy == 0
            and not (pspec.require_zero_unwaived_v6b and v6b_fail > 0)
        ),
    })
    return (
        draft_l1, cert_ir, upd_report, v7_v, energy_e,
        v7_res.turns_used, v7_res.total_tokens, v7_res.token_count_source,
    )


async def score_v7_step(
    ctx: Any, draft_l1: DraftVaultIR, cert_ir: CertifiedVaultIR, report: VerifierReport,
) -> tuple[DraftVaultIR, CertifiedVaultIR, VerifierReport, SemanticCriticVerdict | None, int]:
    """Execute evaluate_v7_and_v6b using RunCtx and accumulate token/turn counters."""
    req = ctx.req
    dl1, cir, rep, v7_v, e, t, tok, src = await evaluate_v7_and_v6b(
        run_id=req.run_id, draft_l1=draft_l1, cert_ir=cert_ir, report=report, base_ctx=ctx.base_ctx,
        profiles=req.profiles, ontology=req.ontology, policy=req.policy, budget=ctx.budget,
        harness=ctx.harness, v7_critic_tier=req.v7_critic_tier, model_overrides=req.model_overrides,
        modeling_preferences=req.modeling_preferences,
        business_context_hints=req.business_context_hints,
        guide_markdown=ctx.guide_text, pack_loader=req.pack_loader,
        horizon_assumptions=ctx.assumptions,
        prior_v8_verdict=ctx.prior_v8_verdict, event_sink=ctx.sink,
    )
    ctx.turns_used += t
    ctx.tokens_used += tok
    if src == "reported":
        ctx.tok_source = "reported"
    return dl1, cir, rep, v7_v, e


__all__ = [
    "audit_v6b_justifications",
    "evaluate_v7_and_v6b",
    "run_v7_semantic_critic",
    "run_v8_horizon_critic",
    "score_v7_step",
]
