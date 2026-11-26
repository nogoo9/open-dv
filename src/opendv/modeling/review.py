"""In-place Gate V8 Horizon review upgrade for CERTIFIED_FAST or CERTIFIED waves."""

from __future__ import annotations

from typing import Literal

from opendv.modeling._engine_helpers import emit_run_event, init_run_ctx
from opendv.modeling.horizon import (
    record_horizon_assumptions_from_answers,
    run_v8_outer_loop,
)
from opendv.modeling.models import (
    DVModelingRequest,
    DVModelingResult,
    attach_wave_delta,
    resolve_next_action,
)
from opendv.modeling.ports import (
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
)
from opendv.modeling.prompts import scan_fragility_surface
from opendv.models.diagnostics import HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import CEGAR_POLICY_SPECS
from opendv.models.project import HorizonAssumptionSpec
from opendv.verifier.ports import IRRuntimeProver
from opendv.verifier.v2_5_gates import build_evolution_lock_from_certified, run_v1_to_v6_gates


async def run_v8_horizon_review(
    request: DVModelingRequest,
    prior_result: DVModelingResult,
    *,
    harness: ModelingHarnessPort | None = None,
    prover: IRRuntimeProver | None = None,
    event_sink: ModelingEventSink | None = None,
    control_reader: ModelingControlReader | None = None,
    checkpoint_store: ModelingCheckpointStore | None = None,
) -> DVModelingResult:
    """In-place V8 Horizon upgrade for a CERTIFIED_FAST or CERTIFIED wave."""
    if (
        prior_result.status not in ("CERTIFIED_FAST", "CERTIFIED")
        or prior_result.certified_ir is None
        or prior_result.draft_ir is None
    ):
        raise OpenDVError(
            "In-place horizon review requires a CERTIFIED_FAST or CERTIFIED wave.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            run_id=prior_result.run_id,
        )

    eff_req = request.model_copy(update={"policy": prior_result.policy})
    ctx, _ = init_run_ctx(
        eff_req,
        harness=harness,
        prover=prover,
        event_sink=event_sink,
        control_reader=control_reader,
        checkpoint_store=checkpoint_store,
    )
    policy_spec = CEGAR_POLICY_SPECS[prior_result.policy]
    effective_budget = request.budget or policy_spec.budget
    report = prior_result.verifier_report or run_v1_to_v6_gates(
        prior_result.certified_ir,
        ctx.base_ctx,
        prover=ctx.prover,
        include_v6b=True,
    )
    if report.v1_v6_fail_count > 0 or report.deterministic_energy > 0:
        raise OpenDVError(
            "Cannot run in-place horizon review when deterministic gates V1-V6 fail.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            run_id=prior_result.run_id,
        )

    cp = request.resume.checkpoint
    start_seq = max(
        prior_result.last_seq,
        prior_result.checkpoint.last_seq if prior_result.checkpoint is not None else 0,
        cp.last_seq if cp is not None else 0,
    )
    seq_ref = [start_seq]
    merged_assumptions: dict[str, HorizonAssumptionSpec] = {
        a.assumption_id: a for a in prior_result.horizon_assumptions
    }
    for a in request.horizon_assumptions:
        merged_assumptions[a.assumption_id] = a
    assumptions = list(merged_assumptions.values())
    answered_keys = list(ctx.answered_hkeys)
    if cp is not None and cp.phase == "S6-06":
        hq_list = [q for q in cp.pending_questions if isinstance(q, HorizonQuestion)]
        assumptions, answered_keys = record_horizon_assumptions_from_answers(
            assumptions,
            hq_list,
            request.resume.hold_answers,
            existing_keys=answered_keys,
        )
    sig_seq = request.resume.last_sig_seq
    if ctx.ctrl is not None:
        sig_seq = max(
            sig_seq,
            len(ctx.ctrl.poll_signals(prior_result.run_id, after_seq=0)),
        )

    horizon_out = await run_v8_outer_loop(
        run_id=prior_result.run_id,
        draft_ir=prior_result.draft_ir,
        certified_ir=prior_result.certified_ir,
        verifier_report=report,
        verifier_ctx=ctx.base_ctx,
        profiles=request.profiles,
        locks=request.wave.evolution_locks,
        prior_ir=request.wave.prior_certified_ir,
        prover=ctx.prover,
        policy=prior_result.policy,
        budget=effective_budget,
        harness=ctx.harness,
        horizon_assumptions=assumptions,
        src_fingerprint=ctx.src_fp,
        turns_used=prior_result.turns_used,
        tokens_used=prior_result.tokens_used,
        token_count_source=prior_result.token_count_source,
        seq_ref=seq_ref,
        event_sink=ctx.sink,
        checkpoint_store=ctx.cp_store,
        model_overrides=request.model_overrides,
        modeling_preferences=request.modeling_preferences,
        business_context_hints=request.business_context_hints,
        guide_markdown=ctx.guide_text,
        pack_loader=request.pack_loader,
        v7_verdict=prior_result.v7_verdict,
        horizon_review_requested=True,
        last_sig_seq=sig_seq,
        answered_horizon_keys=answered_keys,
    )
    total_turns = prior_result.turns_used + horizon_out.turns_added
    total_tokens = prior_result.tokens_used + horizon_out.tokens_added
    out_tok_src: Literal["reported", "estimated"] = (
        "reported"
        if (
            prior_result.token_count_source == "reported"
            or horizon_out.token_count_source == "reported"
        )
        else "estimated"
    )
    if horizon_out.status == "HOLD_CHECKPOINTED":
        pending = (
            list(horizon_out.checkpoint.pending_questions)
            if horizon_out.checkpoint is not None
            else []
        )
        return prior_result.model_copy(
            update={
                "status": "HOLD_CHECKPOINTED",
                "next_action": "ASK_HUMAN_HOLD",
                "certified_ir": horizon_out.certified_ir,
                "draft_ir": horizon_out.draft_ir,
                "verifier_report": horizon_out.verifier_report,
                "fragility_summary": scan_fragility_surface(
                    horizon_out.certified_ir, request.profiles, request.wave.evolution_locks
                ),
                "v8_verdict": horizon_out.v8_verdict,
                "horizon_assumptions": horizon_out.horizon_assumptions,
                "pending_questions": pending,
                "checkpoint": horizon_out.checkpoint,
                "turns_used": total_turns,
                "tokens_used": total_tokens,
                "token_count_source": out_tok_src,
                "horizon_auto_steers_used": horizon_out.horizon_auto_steers_used,
                "v8_calls_used": horizon_out.v8_calls_used,
                "deterministic_energy": horizon_out.verifier_report.deterministic_energy,
                "last_seq": seq_ref[0],
            }
        )

    emit_run_event(
        ctx.sink,
        seq_ref,
        run_id=prior_result.run_id,
        event_type="STAGE_UPDATE",
        payload={
            "stage_id": "S6-07",
            "attempt": prior_result.attempts_used,
            "energy_e": horizon_out.verifier_report.energy_e,
            "status": "CERTIFIED",
        },
    )
    sealed_ir = attach_wave_delta(
        horizon_out.certified_ir,
        prior_ir=request.wave.prior_certified_ir,
        locks=request.wave.evolution_locks,
        force_refresh=(horizon_out.horizon_auto_steers_used > 0),
    )
    evo_lock = build_evolution_lock_from_certified(sealed_ir)
    emit_run_event(
        ctx.sink,
        seq_ref,
        run_id=prior_result.run_id,
        event_type="RUN_COMPLETED",
        payload={
            "revision_id": f"wave-{prior_result.wave_number}",
            "completion_status": "CERTIFIED",
        },
    )
    return prior_result.model_copy(
        update={
            "status": "CERTIFIED",
            "next_action": resolve_next_action(
                status="CERTIFIED", report=horizon_out.verifier_report
            ),
            "wave_number": prior_result.wave_number,
            "policy": prior_result.policy,
            "certified_ir": sealed_ir,
            "draft_ir": horizon_out.draft_ir,
            "evolution_lock": evo_lock,
            "verifier_report": horizon_out.verifier_report,
            "fragility_summary": scan_fragility_surface(
                sealed_ir, request.profiles, request.wave.evolution_locks
            ),
            "v8_verdict": horizon_out.v8_verdict,
            "horizon_assumptions": horizon_out.horizon_assumptions,
            "pending_questions": [],
            "turns_used": total_turns,
            "tokens_used": total_tokens,
            "token_count_source": out_tok_src,
            "horizon_auto_steers_used": horizon_out.horizon_auto_steers_used,
            "v8_calls_used": horizon_out.v8_calls_used,
            "final_energy_e": horizon_out.verifier_report.energy_e,
            "deterministic_energy": horizon_out.verifier_report.deterministic_energy,
            "last_seq": seq_ref[0],
        }
    )


__all__ = ["run_v8_horizon_review"]
