"""Mode-2 CEGAR modeling engine (S6-01..S6-07) with discovery survey and V8 Horizon."""

from __future__ import annotations

from typing import Any, Literal

import opendv.modeling._engine_helpers as eh
from opendv.modeling._sharding import run_ship_vault_synthesis
from opendv.modeling.critics import score_v7_step
from opendv.modeling.horizon import (
    make_post_steer_exhausted_question,
    record_horizon_assumptions_from_answers,
    resolve_s606_resume_action,
    run_v8_step,
)
from opendv.modeling.models import (
    CandidateState,
    DVModelingRequest,
    DVModelingResult,
    is_better_candidate,
)
from opendv.modeling.ports import (
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
)
from opendv.modeling.verify_mode import _build_remediation_hints, _merge_persisted_waivers
from opendv.models.diagnostics import HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.policy import CEGAR_POLICY_SPECS
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec, SemanticCriticVerdict
from opendv.verifier.hint_survey import survey_modeling_hints
from opendv.verifier.l1_codemods import run_l1_codemods
from opendv.verifier.ports import IRRuntimeProver, VerifierReport
from opendv.verifier.v2_5_gates import run_v1_to_v6_gates


def _stage_ev(
    ctx: eh.RunCtx, stage_id: str, attempt: int, energy: int = 0, status: str = "RUNNING",
) -> None:
    eh.emit_run_event(
        ctx.sink, ctx.seq_ref, run_id=ctx.req.run_id, event_type="STAGE_UPDATE",
        payload={"stage_id": stage_id, "attempt": attempt, "energy_e": energy, "status": status},
    )


def _run_l1(ctx: eh.RunCtx, draft: DraftVaultIR) -> tuple[DraftVaultIR, CertifiedVaultIR]:
    dl1, cir, _ = run_l1_codemods(
        draft, profiles=ctx.req.profiles, ontology=ctx.req.ontology,
        locks=ctx.req.wave.evolution_locks, prior_ir=ctx.req.wave.prior_certified_ir,
        cdc_volatility_by_table=ctx.base_ctx.cdc_volatility_by_table,
        fk_turnover_by_table=ctx.base_ctx.fk_turnover_by_table,
        entity_count_overrides=ctx.base_ctx.entity_count_overrides,
        modeling_preferences=ctx.req.modeling_preferences,
    )
    return dl1, cir


def _execute_revert(ctx: eh.RunCtx) -> DVModelingResult:
    cert = ctx.fb_cert or ctx.req.resume.resumed_certified_ir
    draft = ctx.fb_draft or ctx.req.resume.resumed_draft_ir
    if cert is None and draft is not None:
        draft, cert = _run_l1(ctx, draft)
    if cert is None or draft is None:
        raise OpenDVError(
            "Revert requested without pre-steer fallback IR.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT, run_id=ctx.req.run_id,
        )
    rep = run_v1_to_v6_gates(cert, ctx.base_ctx, prover=ctx.prover, include_v6b=True)
    cand = CandidateState(
        draft_ir=draft, certified_ir=cert, report=rep, v7_verdict=None,
        energy_e=rep.energy_e, ir_sha256=sha256_jcs(cert.model_dump(mode="json")),
    )
    ctx.telemetry["reverted"] = True
    st: Literal["CERTIFIED", "CERTIFIED_FAST"] = (
        "CERTIFIED_FAST" if ctx.req.policy == "fast" else "CERTIFIED"
    )
    return eh.seal_result(ctx, cand, status=st)


def _seed_resumed(ctx: eh.RunCtx, cp_p: dict[str, Any]) -> tuple[CandidateState | None, bool]:
    req = ctx.req
    if req.resume.resumed_certified_ir is None or req.resume.resumed_draft_ir is None:
        return None, False
    cert_in = req.resume.resumed_certified_ir
    rep = run_v1_to_v6_gates(cert_in, ctx.base_ctx, prover=ctx.prover, include_v6b=True)
    raw_v7 = cp_p.get("v7_score")
    res_v7 = (
        SemanticCriticVerdict(
            score=int(raw_v7), findings=[],
            accepted_waiver_ids=[j.waiver_id for j in cert_in.justifications if j.reviewed_by_v7],
        )
        if isinstance(raw_v7, int) and not isinstance(raw_v7, bool) else None
    )
    cand = CandidateState(
        draft_ir=req.resume.resumed_draft_ir, certified_ir=cert_in, report=rep,
        v7_verdict=res_v7, energy_e=rep.energy_e,
        ir_sha256=sha256_jcs(cert_in.model_dump(mode="json")),
    )
    floor = CEGAR_POLICY_SPECS[req.policy].v7_floor or 95
    can_skip = rep.v1_v6_fail_count == 0 and rep.deterministic_energy == 0 and (
        req.policy != "strict"
        or (rep.v6b_fail_count == 0 and res_v7 is not None and res_v7.score >= floor)
    )
    return cand, can_skip


async def _finalize(ctx: eh.RunCtx, best: CandidateState | None) -> DVModelingResult:
    req, pspec = ctx.req, CEGAR_POLICY_SPECS[ctx.req.policy]
    _p, acc, hor, rev, ctx.budget = eh.poll_control_signals(
        ctx.ctrl, run_id=req.run_id, last_signal_seq=ctx.last_sig_seq,
        budget=ctx.budget, hints=ctx.hints,
    )
    ctx.accept_best_requested = ctx.accept_best_requested or acc
    ctx.horizon_review_requested = ctx.horizon_review_requested or hor
    if rev and (ctx.fb_cert is not None or ctx.fb_draft is not None):
        return _execute_revert(ctx)

    if best is None or best.report.v1_v6_fail_count > 0 or best.report.deterministic_energy > 0:
        if ctx.accept_best_requested and req.policy == "strict":
            eh.enforce_accept_best_gate(policy=req.policy, best=best, run_id=req.run_id)
        if ctx.post_steer and (ctx.fb_cert is not None or ctx.fb_ref is not None):
            if ctx.fb_ref:
                ctx.telemetry["pre_steer_fallback_ref"] = ctx.fb_ref
            return eh.checkpoint_result(
                ctx, status="HOLD_CHECKPOINTED", phase="S6-06",
                questions=[make_post_steer_exhausted_question()],
                extra={"v8_post_steer_exhausted": True}, best=best,
            )
        return eh.checkpoint_result(ctx, status="UNCERTIFIED_DRAFT", phase="S6-04", best=best)

    if ctx.accept_best_requested:
        eh.enforce_accept_best_gate(policy=req.policy, best=best, run_id=req.run_id)
        st: Literal["CERTIFIED", "CERTIFIED_FAST"] = (
            "CERTIFIED_FAST" if req.policy == "fast" else "CERTIFIED"
        )
        return eh.seal_result(ctx, best, status=st)
    strict_fail = req.policy == "strict" and (
        best.report.v6b_fail_count > 0 or best.v7_verdict is None
        or best.v7_verdict.score < (pspec.v7_floor or 95)
    )
    if strict_fail:
        return eh.checkpoint_result(ctx, status="UNCERTIFIED_DRAFT", phase="S6-04", best=best)
    if req.policy == "fast" and not ctx.horizon_review_requested:
        return eh.seal_result(ctx, best, status="CERTIFIED_FAST")

    if pspec.v8_mode == "auto" or ctx.horizon_review_requested:
        hout = await run_v8_step(ctx, best)
        ctx.turns_used += hout.turns_added
        ctx.tokens_used += hout.tokens_added
        if hout.token_count_source == "reported":
            ctx.tok_source = "reported"
        ctx.assumptions = hout.horizon_assumptions
        best = CandidateState(
            draft_ir=hout.draft_ir, certified_ir=hout.certified_ir, report=hout.verifier_report,
            v7_verdict=best.v7_verdict, energy_e=hout.verifier_report.energy_e,
            ir_sha256=sha256_jcs(hout.certified_ir.model_dump(mode="json")),
        )
        if hout.status == "HOLD_CHECKPOINTED":
            if hout.pre_steer_fallback_ref:
                ctx.telemetry["pre_steer_fallback_ref"] = hout.pre_steer_fallback_ref
            pending = hout.checkpoint.pending_questions if hout.checkpoint else []
            return eh.build_result(
                ctx, status="HOLD_CHECKPOINTED", best=best, v8_verdict=hout.v8_verdict,
                pending_questions=pending, checkpoint=hout.checkpoint,
                auto_steers=hout.horizon_auto_steers_used, v8_calls=hout.v8_calls_used,
            )
        return eh.seal_result(
            ctx, best, status="CERTIFIED", v8_verdict=hout.v8_verdict,
            auto_steers=hout.horizon_auto_steers_used, v8_calls=hout.v8_calls_used,
        )
    return eh.seal_result(ctx, best, status="CERTIFIED")


async def model_vault(
    request: DVModelingRequest, *, harness: ModelingHarnessPort | None = None,
    prover: IRRuntimeProver | None = None, event_sink: ModelingEventSink | None = None,
    control_reader: ModelingControlReader | None = None,
    checkpoint_store: ModelingCheckpointStore | None = None,
) -> DVModelingResult:
    """Execute Mode-2 managed CEGAR modeling (S6-01..S6-07) and V8 Horizon review."""
    ctx, cp_p = eh.init_run_ctx(
        request, harness=harness, prover=prover, event_sink=event_sink,
        control_reader=control_reader, checkpoint_store=checkpoint_store,
    )
    pspec = CEGAR_POLICY_SPECS[request.policy]
    _stage_ev(ctx, "S6-01", 0, status="STARTED")

    cp = request.resume.checkpoint
    s606: Literal["NONE", "PROCEED", "STEER", "REVERT"] = "NONE"
    if cp is not None and cp.phase == "S6-06":
        pending_hqs = [q for q in cp.pending_questions if isinstance(q, HorizonQuestion)]
        s606 = resolve_s606_resume_action(
            pending_hqs, request.resume.hold_answers,
            revert_requested=request.resume.revert_to_pre_steer,
        )
        ctx.assumptions, ctx.answered_hkeys = record_horizon_assumptions_from_answers(
            ctx.assumptions, pending_hqs, request.resume.hold_answers,
            existing_keys=ctx.answered_hkeys,
        )
    elif request.resume.revert_to_pre_steer:
        s606 = "REVERT"
    if s606 == "REVERT":
        return _execute_revert(ctx)

    ha_ans = request.resume.hold_answers
    answered_qids = set(request.resume.answered_question_ids) | (
        set(ha_ans.answers) if ha_ans else set()
    )
    survey_qs, survey_counts = survey_modeling_hints(
        request.profiles, ontology=request.ontology,
        source_rows_by_table=ctx.base_ctx.source_rows_by_table,
        draft_ir=request.resume.resumed_draft_ir, answered_question_ids=answered_qids,
        fk_turnover_by_table=ctx.base_ctx.fk_turnover_by_table,
        max_questions=request.modeling_preferences.max_survey_questions or 5,
    )
    ctx.telemetry["survey_counts"] = survey_counts
    already_paused = (
        bool(cp_p.get("discovery_hold_completed", False))
        or (cp is not None and cp.phase in ("S6-02", "S6-05", "S6-06"))
        or s606 in ("STEER", "PROCEED")
    )
    if (
        request.modeling_preferences.clarify_mode == "ask_on_ambiguity"
        and survey_qs and not already_paused
    ):
        return eh.checkpoint_result(
            ctx, status="HOLD_CHECKPOINTED", phase="S6-02", questions=survey_qs,
            extra={"paused_before_attempt": 1},
        )
    if request.modeling_preferences.clarify_mode == "auto":
        ctx.suggested_qs = list(survey_qs)

    best: CandidateState | None = None
    seen_digests: set[str] = set()
    prior_report: VerifierReport | None = None
    prior_draft: DraftVaultIR | None = request.resume.resumed_draft_ir
    prior_v7: SemanticCriticVerdict | None = None
    frozen_hubs: dict[str, list[str]] = {}
    skip_synthesis = False

    if s606 == "STEER":
        ctx.fb_draft = ctx.fb_draft or request.resume.resumed_draft_ir
        ctx.fb_cert = ctx.fb_cert or request.resume.resumed_certified_ir
        if ctx.fb_ref is None and ctx.fb_cert is not None:
            ctx.fb_ref = f"sha256:{sha256_jcs(ctx.fb_cert.model_dump(mode='json'))}"
        ctx.post_steer, ctx.telemetry["post_steer"] = True, True
    elif s606 == "PROCEED":
        best, skip_synthesis = _seed_resumed(ctx, cp_p)
        if best is not None:
            prior_report = best.report

    max_attempts = 1 if request.policy == "symbolic_only" else max(1, pspec.k_max)
    for attempt_idx in range(1, max_attempts + 1):
        pause_req, acc, hor, rev, ctx.budget = eh.poll_control_signals(
            ctx.ctrl, run_id=request.run_id, last_signal_seq=ctx.last_sig_seq,
            budget=ctx.budget, hints=ctx.hints,
        )
        ctx.accept_best_requested = ctx.accept_best_requested or acc
        ctx.horizon_review_requested = ctx.horizon_review_requested or hor
        if rev and (ctx.fb_cert is not None or ctx.fb_draft is not None):
            return _execute_revert(ctx)
        if pause_req:
            return eh.checkpoint_result(
                ctx, status="HOLD_CHECKPOINTED", phase="S6-02",
                extra={"paused_before_attempt": attempt_idx}, best=best,
            )
        if skip_synthesis:
            break
        if ctx.accept_best_requested and best is not None:
            eh.enforce_accept_best_gate(policy=request.policy, best=best, run_id=request.run_id)
            if eh.can_accept_best(policy=request.policy, best=best):
                break
        if ctx.turns_used >= ctx.budget.max_turns or ctx.tokens_used >= ctx.budget.max_tokens:
            break

        _stage_ev(ctx, "S6-02", attempt_idx, best.energy_e if best else 0)
        rem_hints = _build_remediation_hints(prior_report) if prior_report else ()
        draft, turn_props, t_used, tok_used, t_src = await run_ship_vault_synthesis(
            run_id=request.run_id, project_id=request.project_id, wave_number=ctx.wave_number,
            attempt=attempt_idx, ontology=request.ontology, profiles=request.profiles,
            clusters=request.clusters, locks=request.wave.evolution_locks,
            prior_report=prior_report, hints=ctx.hints, hold_answers=ha_ans, policy=request.policy,
            budget=ctx.budget, harness=ctx.harness, pack_loader=request.pack_loader,
            model_overrides=request.model_overrides, event_sink=ctx.sink,
            prior_v7_verdict=prior_v7, prior_v8_verdict=ctx.prior_v8_verdict,
            prior_draft_ir=prior_draft, modeling_preferences=request.modeling_preferences,
            business_context_hints=request.business_context_hints, guide_markdown=ctx.guide_text,
            remediation_hints=rem_hints, horizon_assumptions=ctx.assumptions,
            frozen_shared_hubs=frozen_hubs or None,
        )
        ctx.turns_used += t_used
        ctx.tokens_used += tok_used
        if t_src == "reported":
            ctx.tok_source = "reported"
        ctx.proposals.extend(turn_props)
        if any(j.actor != "agent" or j.reviewed_by_v7 for j in draft.justifications):
            draft = draft.model_copy(update={"justifications": [
                j.model_copy(update={"actor": "agent", "reviewed_by_v7": False})
                if j.actor != "agent" or j.reviewed_by_v7 else j
                for j in draft.justifications
            ]})
        unresolved = [q for q in draft.pending_clarifications if q.question_id not in answered_qids]
        if unresolved:
            return eh.checkpoint_result(
                ctx, status="HOLD_CHECKPOINTED", phase="S6-05",
                questions=unresolved, extra={"attempt": attempt_idx}, draft_ir=draft,
            )

        _stage_ev(ctx, "S6-03", attempt_idx)
        draft_l1, cert_ir = _run_l1(ctx, draft)
        if ctx.base_ctx.persisted_waivers:
            merged_w = _merge_persisted_waivers(
                cert_ir.justifications, ctx.base_ctx.persisted_waivers,
            )
            cert_ir = cert_ir.model_copy(update={"justifications": merged_w})
        for hub in draft_l1.hubs:
            if len({sb.source_id for sb in hub.source_bindings}) >= 2 and hub.business_keys:
                frozen_hubs[hub.hub_name] = [b.col_name for b in hub.business_keys]

        _stage_ev(ctx, "S6-04", attempt_idx)
        report = run_v1_to_v6_gates(cert_ir, ctx.base_ctx, prover=ctx.prover, include_v6b=True)
        eh.emit_run_event(
            ctx.sink, ctx.seq_ref, run_id=request.run_id, event_type="GATE_CHECK_STREAM",
            payload={
                "gate_id": "V1-V6", "passed_count": 0 if report.v1_v6_fail_count else 1,
                "total_count": 1,
                "diagnostics": [d.model_dump(mode="json") for d in report.v1_v6_diagnostics[:10]],
            },
        )

        draft_l1, cert_ir, report, v7_v, energy_e = await score_v7_step(
            ctx, draft_l1, cert_ir, report,
        )
        ir_sha = sha256_jcs(cert_ir.model_dump(mode="json"))
        snap = AttemptSnapshotSpec(
            attempt=attempt_idx, ir_sha256=ir_sha, energy_e=energy_e,
            v7_score=v7_v.score if v7_v else None, diagnostics=report.all_diagnostics,
            draft_ir=draft_l1,
        )
        ctx.attempts.append(snap)
        if ctx.cp_store is not None:
            ctx.cp_store.save_attempt_snapshot(request.run_id, snap)
        eh.emit_run_event(
            ctx.sink, ctx.seq_ref, run_id=request.run_id, event_type="ATTEMPT_SNAPSHOT",
            payload={
                "attempt": attempt_idx, "energy_e": energy_e, "ir_sha256": ir_sha,
                "gate_summary": {
                    "v1_v6_fail": report.v1_v6_fail_count, "v6b_fail": report.v6b_fail_count,
                },
            },
        )

        cand = CandidateState(
            draft_ir=draft_l1, certified_ir=cert_ir, report=report,
            v7_verdict=v7_v, energy_e=energy_e, ir_sha256=ir_sha,
        )
        if is_better_candidate(cand, best):
            best = cand
        prior_report, prior_draft = report, draft_l1
        v7_needs_fix = v7_v is not None and (
            (pspec.v7_floor is not None and v7_v.score < pspec.v7_floor)
            or report.v6b_fail_count > 0
            or any(not j.reviewed_by_v7 for j in cert_ir.justifications)
        )
        prior_v7 = v7_v if v7_needs_fix else None
        if ir_sha in seen_digests:
            ctx.cycle_halted = True
            break
        seen_digests.add(ir_sha)

        det_ok = report.v1_v6_fail_count == 0 and report.deterministic_energy == 0
        if request.policy == "symbolic_only" or (request.policy == "fast" and det_ok):
            break
        if (
            det_ok and pspec.v7_floor is not None and v7_v is not None
            and v7_v.score >= pspec.v7_floor
            and (not pspec.require_zero_unwaived_v6b or report.v6b_fail_count == 0)
        ):
            break

    return await _finalize(ctx, best)


__all__ = ["model_vault"]
