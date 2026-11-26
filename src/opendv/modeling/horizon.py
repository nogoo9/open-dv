"""Gate V8 Horizon outer loop, S6-06 STEER/REVERT resolution, and question deduplication."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from opendv.modeling._engine_helpers import emit_run_event, save_checkpoint
from opendv.modeling.critics import run_v8_horizon_critic
from opendv.modeling.models import make_v8_low_score_question, preserve_v7_waivers
from opendv.modeling.ports import (
    ModelingCheckpointStore,
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingPackLoader,
)
from opendv.models.diagnostics import HorizonOptionSpec, HorizonQuestion
from opendv.models.events import RunCheckpointRecord
from opendv.models.json_hasher import sha256_jcs
from opendv.models.modeling_preferences import ModelingPreferences
from opendv.models.policy import CegarPolicy, HarnessBudget
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.verifier.l1_codemods import run_l1_codemods
from opendv.verifier.ports import IRRuntimeProver, VerifierContext, VerifierReport
from opendv.verifier.v2_5_gates import run_v1_to_v6_gates

_ENTITY_COL_RE = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\b")
_DV_ENTITY_RE = re.compile(r"\b((?:hub|lnk|t_lnk|sat|mas|esat|ref|brg|pit|sal)_[a-z0-9_]+)\b")


@dataclass
class HorizonOutcome:
    """Result of executing the bounded S6-06 V8 Horizon outer loop."""

    status: Literal["PROCEED", "HOLD_CHECKPOINTED"]
    draft_ir: DraftVaultIR
    certified_ir: CertifiedVaultIR
    verifier_report: VerifierReport
    v8_verdict: HorizonCriticVerdict
    horizon_assumptions: list[HorizonAssumptionSpec]
    horizon_auto_steers_used: int
    v8_calls_used: int
    turns_added: int
    tokens_added: int
    token_count_source: Literal["reported", "estimated"] = "estimated"
    checkpoint: RunCheckpointRecord | None = None
    pre_steer_fallback_ref: str | None = None
    pre_steer_fallback_draft_ir: DraftVaultIR | None = None
    pre_steer_fallback_certified_ir: CertifiedVaultIR | None = None
    answered_horizon_keys: list[str] = field(default_factory=list)


def horizon_question_dedup_key(question: HorizonQuestion) -> tuple[str, str, str]:
    """Derive canonical (brk_code, entity_name, column_name) deduplication tuple."""
    brk = question.brk_code.strip().upper()
    parts = (*(o.operator_hint or "" for o in question.options),
             question.title, question.prompt, question.question_id)
    text = " ".join(p for p in parts if p)
    dot = _ENTITY_COL_RE.search(text)
    if dot:
        return (brk, dot.group(1), dot.group(2))
    ent = _DV_ENTITY_RE.search(text)
    return (brk, ent.group(1), "") if ent else (brk, question.question_id, "")


def format_horizon_key(key: tuple[str, str, str]) -> str:
    """Serialize (brk_code, entity_name, column_name) key into a stable string."""
    return f"{key[0]}|{key[1]}|{key[2]}"


def deduplicate_horizon_questions(
    questions: Sequence[HorizonQuestion], answered_keys: Collection[str] = (),
) -> list[HorizonQuestion]:
    """Deduplicate HorizonQuestion items by (brk_code, entity_name, column_name)."""
    seen, out = set(answered_keys), []
    for q in questions:
        k = format_horizon_key(horizon_question_dedup_key(q))
        if k not in seen and q.question_id not in seen:
            seen.add(k)
            out.append(q)
    return out


def _classify_option(
    question: HorizonQuestion, answer: str,
) -> Literal["ACCEPT", "STEER", "REVERT"]:
    norm = answer.strip().lower()
    if norm in ("revert", "steer"):
        return "REVERT" if norm == "revert" else "STEER"
    for opt in question.options:
        if opt.option_id.strip().lower() == norm:
            oid = opt.option_id.strip().lower()
            if oid == "revert":
                return "REVERT"
            hint = (opt.operator_hint or "").strip().upper()
            return "STEER" if oid == "steer" or hint.startswith("STEER") else "ACCEPT"
    return "ACCEPT"


def resolve_s606_resume_action(
    pending_questions: Sequence[HorizonQuestion],
    answers: HoldAnswersReq | None,
    *,
    revert_requested: bool = False,
) -> Literal["PROCEED", "STEER", "REVERT"]:
    """Resolve explicit Steward S6-06 resume mode from structured answers and flags."""
    if revert_requested:
        return "REVERT"
    if answers is None or not answers.answers:
        return "PROCEED"
    top = (answers.answers.get("action") or answers.answers.get("__action__") or "").strip().lower()
    if top in ("revert", "steer"):
        return "REVERT" if top == "revert" else "STEER"
    q_by_id = {q.question_id: q for q in pending_questions}
    decs = [
        _classify_option(q_by_id[qid], raw) if qid in q_by_id else raw.strip().upper()
        for qid, raw in answers.answers.items()
        if qid not in ("action", "__action__")
    ]
    if "REVERT" in decs:
        return "REVERT"
    return "STEER" if "STEER" in decs else "PROCEED"


def record_horizon_assumptions_from_answers(
    existing: Sequence[HorizonAssumptionSpec],
    pending_questions: Sequence[HorizonQuestion],
    answers: HoldAnswersReq | None,
    *,
    existing_keys: Sequence[str] = (),
) -> tuple[list[HorizonAssumptionSpec], list[str]]:
    """Persist Steward Horizon choices into HorizonAssumptionSpec and answered dedup keys."""
    updated, keys = list(existing), list(dict.fromkeys(existing_keys))
    if answers is None or not answers.answers:
        return updated, keys
    idx_by_id = {a.assumption_id: i for i, a in enumerate(updated)}
    raw_added = answers.answers.get("added_fields", "")
    extra_fields = [f.strip() for f in raw_added.split(",") if f.strip()] if raw_added else []
    for q in pending_questions:
        ans = answers.answers.get(q.question_id)
        if not ans or _classify_option(q, ans) == "REVERT":
            continue
        aid = f"ha-{q.question_id}"
        spec = HorizonAssumptionSpec(
            assumption_id=aid, brk_code=q.brk_code, title=q.title, notes=ans,
            steward_decision="STEER" if _classify_option(q, ans) == "STEER" else "ACCEPT",
            selected_option_ids=[ans], added_fields=extra_fields,
        )
        if aid in idx_by_id:
            updated[idx_by_id[aid]] = spec
        else:
            idx_by_id[aid] = len(updated)
            updated.append(spec)
        k = format_horizon_key(horizon_question_dedup_key(q))
        if k not in keys:
            keys.append(k)
    return updated, keys


def with_revert_option(question: HorizonQuestion) -> HorizonQuestion:
    """Append an explicit 'revert' HorizonOptionSpec when pre-steer fallback is available."""
    if any(opt.option_id == "revert" for opt in question.options):
        return question
    revert_opt = HorizonOptionSpec(
        option_id="revert", label="Revert to pre-steer schema",
        description="Restore pre-steer snapshot without re-synthesis.",
        implications="Discards steered modifications.",
    )
    return question.model_copy(update={"options": [*question.options, revert_opt]})


def _q(
    qid: str, title: str, prompt: str, opt_id: str, opt_label: str, impact: str,
) -> HorizonQuestion:
    return HorizonQuestion(
        question_id=qid, brk_code="BRK-01", title=title, prompt=prompt,
        options=[HorizonOptionSpec(
            option_id=opt_id, label=opt_label,
            description="Proceed with choice.", implications="Updates S6-06 horizon state.",
        )],
        impact_summary=impact,
    )


def make_post_steer_exhausted_question() -> HorizonQuestion:
    """Build S6-06 hold question when post-STEER synthesis exhausts K_max."""
    return with_revert_option(_q(
        "hq-v8-post-steer-exhausted", "Post-steer synthesis did not reach certification",
        "Steered synthesis exhausted K_max without passing all gates. Revert or re-steer.",
        "steer", "Retry steer", "Preserves pre-steer certified snapshot for direct revert.",
    ))


async def run_v8_outer_loop(
    *,
    run_id: str, draft_ir: DraftVaultIR, certified_ir: CertifiedVaultIR,
    verifier_report: VerifierReport, verifier_ctx: VerifierContext,
    profiles: Sequence[SourceProfile], locks: EvolutionLockSpec | None,
    prior_ir: CertifiedVaultIR | None, prover: IRRuntimeProver | None, policy: CegarPolicy,
    budget: HarnessBudget, harness: ModelingHarnessPort,
    horizon_assumptions: Sequence[HorizonAssumptionSpec], src_fingerprint: str,
    turns_used: int, tokens_used: int, token_count_source: str, seq_ref: list[int],
    event_sink: ModelingEventSink | None, checkpoint_store: ModelingCheckpointStore | None,
    model_overrides: Mapping[str, str] | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    business_context_hints: str | None = None, guide_markdown: str | None = None,
    pack_loader: ModelingPackLoader | None = None, v7_verdict: SemanticCriticVerdict | None = None,
    horizon_review_requested: bool = False, last_sig_seq: int = 0, hints: Sequence[str] = (),
    accept_best_requested: bool = False, post_steer: bool = False,
    pre_steer_fallback_ref: str | None = None,
    pre_steer_fallback_draft_ir: DraftVaultIR | None = None,
    pre_steer_fallback_certified_ir: CertifiedVaultIR | None = None,
    answered_horizon_keys: Sequence[str] = (),
) -> HorizonOutcome:
    """Execute bounded V8 Horizon outer loop (<= 1 steer, <= 1 post-steer V8 pass)."""
    assumptions = list(horizon_assumptions)
    answered_keys = list(dict.fromkeys(answered_horizon_keys))
    turns_added = tokens_added = auto_steers = v8_calls = 0
    tok_src: Literal["reported", "estimated"] = (
        "reported" if token_count_source == "reported" else "estimated"
    )
    best_draft, best_cert, best_report = draft_ir, certified_ir, verifier_report
    fb_ref, fb_draft, fb_cert = (
        pre_steer_fallback_ref, pre_steer_fallback_draft_ir, pre_steer_fallback_certified_ir
    )
    accepted_low = any(a.steward_decision == "ACCEPT" for a in assumptions)

    def _out(
        status: Literal["PROCEED", "HOLD_CHECKPOINTED"], verdict: HorizonCriticVerdict,
        cp: RunCheckpointRecord | None = None,
    ) -> HorizonOutcome:
        return HorizonOutcome(
            status=status, draft_ir=best_draft, certified_ir=best_cert,
            verifier_report=best_report, v8_verdict=verdict, horizon_assumptions=assumptions,
            horizon_auto_steers_used=auto_steers, v8_calls_used=v8_calls,
            turns_added=turns_added, tokens_added=tokens_added, token_count_source=tok_src,
            checkpoint=cp, pre_steer_fallback_ref=fb_ref,
            pre_steer_fallback_draft_ir=fb_draft, pre_steer_fallback_certified_ir=fb_cert,
            answered_horizon_keys=answered_keys,
        )

    def _hold(
        verdict: HorizonCriticVerdict, qs: Sequence[HorizonQuestion],
        extra: dict[str, str | int | bool | list[str]],
    ) -> HorizonOutcome:
        q_list = [with_revert_option(q) for q in qs] if (fb_cert or fb_ref) else list(qs)
        cp = save_checkpoint(
            run_id=run_id, phase="S6-06", status="HOLD_CHECKPOINTED",
            ontology_version=best_cert.ontology_version, src_fingerprint=src_fingerprint,
            pending_questions=q_list, turns_used=turns_used + turns_added,
            tokens_used=tokens_used + tokens_added, token_count_source=tok_src,
            seq_ref=seq_ref, event_sink=event_sink, checkpoint_store=checkpoint_store,
            extra=extra, last_sig_seq=last_sig_seq, hints=hints,
            accept_best_requested=accept_best_requested,
            horizon_review_requested=horizon_review_requested, effective_budget=budget,
            policy=policy, v7_score=v7_verdict.score if v7_verdict else None,
            post_steer=post_steer or auto_steers > 0, answered_horizon_keys=answered_keys,
            pre_steer_fallback_ref=fb_ref, prior_v8_verdict_json=verdict.model_dump_json(),
        )
        return _out("HOLD_CHECKPOINTED", verdict, cp)

    async def _call_v8() -> HorizonCriticVerdict:
        nonlocal v8_calls, turns_added, tokens_added, tok_src
        v, v_res = await run_v8_horizon_critic(
            run_id=run_id, ir=best_cert, profiles=profiles, locks=locks,
            horizon_assumptions=assumptions, policy=policy, budget=budget, harness=harness,
            model_overrides=model_overrides, modeling_preferences=modeling_preferences,
            business_context_hints=business_context_hints, guide_markdown=guide_markdown,
            pack_loader=pack_loader, event_sink=event_sink,
        )
        v8_calls += 1
        turns_added += v_res.turns_used
        tokens_added += v_res.total_tokens
        if v_res.token_count_source == "reported":
            tok_src = "reported"
        deduped_b = deduplicate_horizon_questions(v.bucket_b_questions, answered_keys)
        return v.model_copy(update={"bucket_b_questions": deduped_b})

    def _hold_if_needed(verdict: HorizonCriticVerdict, *, is_post: bool) -> HorizonOutcome | None:
        if verdict.bucket_b_questions:
            return _hold(
                verdict, verdict.bucket_b_questions, {"v8_classification": verdict.classification},
            )
        if verdict.score >= 85 and not verdict.bucket_a_ops:
            return _out("PROCEED", verdict)
        if is_post or not verdict.bucket_a_ops:
            if not verdict.bucket_a_ops and accepted_low:
                return _out("PROCEED", verdict)
            if is_post and verdict.bucket_a_ops:
                cap_q = _q(
                    "hq-v8-steer-cap", "Post-steer horizon review required",
                    "Additional steering requires confirmation.", "accept",
                    "Accept steered schema", "Limits post-steer V8 check to 1 pass.",
                )
                return _hold(verdict, [cap_q], {"v8_steer_cap_reached": True})
            return _hold(
                verdict, [make_v8_low_score_question(verdict.score)],
                {"v8_low_score_pause": True, "v8_score": verdict.score},
            )
        return None

    emit_run_event(
        event_sink, seq_ref, run_id=run_id, event_type="STAGE_UPDATE",
        payload={
            "stage_id": "S6-06", "attempt": 1,
            "energy_e": best_report.energy_e, "status": "RUNNING",
        },
    )
    verdict = await _call_v8()
    early = _hold_if_needed(verdict, is_post=post_steer)
    if early is not None:
        return early

    fb_ref = fb_ref or f"sha256:{sha256_jcs(best_cert.model_dump(mode='json'))}"
    fb_draft, fb_cert = fb_draft or best_draft, fb_cert or best_cert
    steered_draft, steered_cert, bucket_res = run_l1_codemods(
        best_draft, profiles=profiles, ontology=verifier_ctx.ontology, locks=locks,
        prior_ir=prior_ir, bucket_a_ops=verdict.bucket_a_ops,
        cdc_volatility_by_table=verifier_ctx.cdc_volatility_by_table,
        fk_turnover_by_table=verifier_ctx.fk_turnover_by_table,
        entity_count_overrides=verifier_ctx.entity_count_overrides,
        modeling_preferences=modeling_preferences,
    )
    if bucket_res is not None and bucket_res.requires_bucket_b:
        esc_b = deduplicate_horizon_questions(bucket_res.bucket_b_escalations, answered_keys)
        return _hold(
            verdict, esc_b or bucket_res.bucket_b_escalations, {"v8_bucket_b_escalation": True},
        )

    steered_cert = preserve_v7_waivers(steered_cert, best_cert, v7_verdict)
    steered_report = run_v1_to_v6_gates(steered_cert, verifier_ctx, prover=prover, include_v6b=True)
    regressed = (
        steered_report.v1_v6_fail_count > 0 or steered_report.deterministic_energy > 0
        or (policy == "strict" and steered_report.v6b_fail_count > 0)
    )
    if regressed:
        reg_q = _q(
            "hq-v8-regression", "Horizon auto-steer regressed symbolic gates",
            "Bucket A steer regressed gates; prior candidate retained.", "accept_best",
            "Accept retained schema", "Retains prior passing candidate.",
        )
        return _hold(verdict, [reg_q], {"v8_regression_ratchet": True})

    auto_steers += 1
    acc_ids = {j.waiver_id for j in steered_cert.justifications if j.reviewed_by_v7}
    if acc_ids:
        steered_draft = steered_draft.model_copy(update={
            "justifications": [
                j.model_copy(update={"reviewed_by_v7": True}) if j.waiver_id in acc_ids else j
                for j in steered_draft.justifications
            ]
        })
    best_draft, best_cert, best_report = steered_draft, steered_cert, steered_report

    verdict = await _call_v8()
    return _hold_if_needed(verdict, is_post=True) or _out("PROCEED", verdict)


async def run_v8_step(ctx: Any, best: Any) -> HorizonOutcome:
    """Execute run_v8_outer_loop using RunCtx and CandidateState."""
    req = ctx.req
    return await run_v8_outer_loop(
        run_id=req.run_id, draft_ir=best.draft_ir, certified_ir=best.certified_ir,
        verifier_report=best.report, verifier_ctx=ctx.base_ctx, profiles=req.profiles,
        locks=req.wave.evolution_locks, prior_ir=req.wave.prior_certified_ir, prover=ctx.prover,
        policy=req.policy, budget=ctx.budget, harness=ctx.harness,
        horizon_assumptions=ctx.assumptions, src_fingerprint=ctx.src_fp,
        turns_used=ctx.turns_used, tokens_used=ctx.tokens_used, token_count_source=ctx.tok_source,
        seq_ref=ctx.seq_ref, event_sink=ctx.sink, checkpoint_store=ctx.cp_store,
        model_overrides=req.model_overrides, modeling_preferences=req.modeling_preferences,
        business_context_hints=req.business_context_hints, guide_markdown=ctx.guide_text,
        pack_loader=req.pack_loader, v7_verdict=best.v7_verdict,
        horizon_review_requested=ctx.horizon_review_requested, last_sig_seq=ctx.last_sig_seq[0],
        hints=ctx.hints, accept_best_requested=ctx.accept_best_requested,
        post_steer=ctx.post_steer, pre_steer_fallback_ref=ctx.fb_ref,
        pre_steer_fallback_draft_ir=ctx.fb_draft, pre_steer_fallback_certified_ir=ctx.fb_cert,
        answered_horizon_keys=ctx.answered_hkeys,
    )


__all__ = [
    "HorizonOutcome", "deduplicate_horizon_questions", "format_horizon_key",
    "horizon_question_dedup_key", "make_post_steer_exhausted_question",
    "record_horizon_assumptions_from_answers", "resolve_s606_resume_action",
    "run_v8_outer_loop", "run_v8_step", "with_revert_option",
]
