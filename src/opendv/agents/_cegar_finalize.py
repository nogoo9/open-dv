"""Finalization, uncertified checkpointing, and V8 Horizon review for Step 6 CEGAR."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from opendv.agents._cegar_helpers import (
    commit_uncertified_checkpoint,
    emit_run_event,
    enforce_accept_best_gate,
    sources_fingerprint,
)
from opendv.agents._cegar_horizon import (
    record_horizon_assumptions_from_answers,
    run_v8_outer_loop,
)
from opendv.agents._cegar_models import (
    CandidateState,
    CegarRunRequest,
    CegarRunResult,
    attach_wave_delta,
    seal_cegar_result,
)
from opendv.models.diagnostics import HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.policy import CEGAR_POLICY_SPECS, HarnessBudget
from opendv.models.project import HorizonAssumptionSpec, OntologyProposalSpec
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec
from opendv.verifier.ports import VerifierContext
from opendv.verifier.v2_5_gates import build_evolution_lock_from_certified, run_v1_to_v6_gates


async def finalize_cegar_outcome(
    *,
    req: CegarRunRequest,
    best: CandidateState | None,
    wave_number: int,
    base_ctx: VerifierContext,
    effective_budget: HarnessBudget,
    src_fp: str,
    attempts: list[AttemptSnapshotSpec],
    proposals: list[OntologyProposalSpec],
    assumptions: list[HorizonAssumptionSpec],
    turns_used: int,
    tokens_used: int,
    tok_source: str,
    cycle_halted: bool,
    accept_best_requested: bool,
    horizon_review_requested: bool,
    seq_ref: list[int],
    last_sig_seq: int = 0,
    hints: Sequence[str] = (),
) -> CegarRunResult:
    """Finalize Step 6 CEGAR after the L1/L2/V7 loop into CERTIFIED, HOLD, or UNCERTIFIED."""
    policy_spec = CEGAR_POLICY_SPECS[req.policy]
    lit_tok_src: Literal["reported", "estimated"] = (
        "reported" if tok_source == "reported" else "estimated"
    )
    current_seq_fn = getattr(req.event_sink, "current_seq", None)
    if callable(current_seq_fn):
        seq_ref[0] = max(seq_ref[0], int(current_seq_fn(req.run_id)))

    if best is None or best.report.v1_v6_fail_count > 0:
        if accept_best_requested and req.policy == "strict":
            raise OpenDVError(
                "Strict policy forbids accept-best while symbolic gates V1-V6 fail.",
                error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                run_id=req.run_id,
            )
        cp = commit_uncertified_checkpoint(
            run_id=req.run_id,
            phase="S6-04",
            ontology_version=req.ontology.ontology_version,
            src_fingerprint=src_fp,
            turns_used=turns_used,
            tokens_used=tokens_used,
            token_count_source=lit_tok_src,
            best_ir_sha=best.ir_sha256 if best else None,
            seq_ref=seq_ref,
            checkpoint_store=req.checkpoint_store,
        )
        return CegarRunResult(
            run_id=req.run_id,
            status="UNCERTIFIED_DRAFT",
            policy=req.policy,
            wave_number=wave_number,
            attempts_used=len(attempts),
            certified_ir=best.certified_ir if best else None,
            draft_ir=best.draft_ir if best else None,
            verifier_report=best.report if best else None,
            v7_verdict=best.v7_verdict if best else None,
            attempts=attempts,
            ontology_proposals=proposals,
            horizon_assumptions=assumptions,
            checkpoint=cp,
            turns_used=turns_used,
            tokens_used=tokens_used,
            token_count_source=lit_tok_src,
            cycle_halted=cycle_halted,
            last_seq=seq_ref[0],
        )

    if accept_best_requested:
        enforce_accept_best_gate(
            policy=req.policy,
            v1_v6_fail=best.report.v1_v6_fail_count,
            v6b_fail=best.report.v6b_fail_count,
            v7_score=best.v7_verdict.score if best.v7_verdict else None,
        )
        return seal_cegar_result(
            req=req,
            best=best,
            status="CERTIFIED_FAST" if req.policy == "fast" else "CERTIFIED",
            wave_number=wave_number,
            attempts=attempts,
            proposals=proposals,
            assumptions=assumptions,
            v8_verdict=None,
            turns_used=turns_used,
            tokens_used=tokens_used,
            tok_source=lit_tok_src,
            auto_steers=0,
            v8_calls=0,
            cycle_halted=cycle_halted,
            seq_ref=seq_ref,
        )

    if req.policy == "strict" and (
        best.report.v6b_fail_count > 0
        or best.v7_verdict is None
        or best.v7_verdict.score < (policy_spec.v7_floor or 95)
    ):
        cp = commit_uncertified_checkpoint(
            run_id=req.run_id,
            phase="S6-04",
            ontology_version=req.ontology.ontology_version,
            src_fingerprint=src_fp,
            turns_used=turns_used,
            tokens_used=tokens_used,
            token_count_source=lit_tok_src,
            best_ir_sha=best.ir_sha256,
            seq_ref=seq_ref,
            checkpoint_store=req.checkpoint_store,
        )
        return CegarRunResult(
            run_id=req.run_id,
            status="UNCERTIFIED_DRAFT",
            policy=req.policy,
            wave_number=wave_number,
            attempts_used=len(attempts),
            certified_ir=best.certified_ir,
            draft_ir=best.draft_ir,
            verifier_report=best.report,
            v7_verdict=best.v7_verdict,
            attempts=attempts,
            ontology_proposals=proposals,
            horizon_assumptions=assumptions,
            checkpoint=cp,
            turns_used=turns_used,
            tokens_used=tokens_used,
            token_count_source=lit_tok_src,
            cycle_halted=cycle_halted,
            last_seq=seq_ref[0],
        )

    if req.policy == "fast" and not horizon_review_requested:
        return seal_cegar_result(
            req=req,
            best=best,
            status="CERTIFIED_FAST",
            wave_number=wave_number,
            attempts=attempts,
            proposals=proposals,
            assumptions=assumptions,
            v8_verdict=None,
            turns_used=turns_used,
            tokens_used=tokens_used,
            tok_source=lit_tok_src,
            auto_steers=0,
            v8_calls=0,
            cycle_halted=cycle_halted,
            seq_ref=seq_ref,
        )

    if policy_spec.v8_mode == "auto" or horizon_review_requested:
        horizon_out = await run_v8_outer_loop(
            run_id=req.run_id,
            draft_ir=best.draft_ir,
            certified_ir=best.certified_ir,
            verifier_report=best.report,
            verifier_ctx=base_ctx,
            profiles=req.profiles,
            locks=req.evolution_locks,
            prior_ir=req.prior_certified_ir,
            prover=req.prover,
            policy=req.policy,
            budget=effective_budget,
            harness=req.harness,
            horizon_assumptions=assumptions,
            src_fingerprint=src_fp,
            turns_used=turns_used,
            tokens_used=tokens_used,
            token_count_source=lit_tok_src,
            seq_ref=seq_ref,
            event_sink=req.event_sink,
            checkpoint_store=req.checkpoint_store,
            model_overrides=req.model_overrides,
            v7_verdict=best.v7_verdict,
            horizon_review_requested=horizon_review_requested,
            last_sig_seq=last_sig_seq,
            hints=hints,
            accept_best_requested=accept_best_requested,
        )
        turns_used += horizon_out.turns_added
        tokens_used += horizon_out.tokens_added
        if horizon_out.token_count_source == "reported":
            lit_tok_src = "reported"
        assumptions = horizon_out.horizon_assumptions
        best = CandidateState(
            draft_ir=horizon_out.draft_ir,
            certified_ir=horizon_out.certified_ir,
            report=horizon_out.verifier_report,
            v7_verdict=best.v7_verdict,
            energy_e=horizon_out.verifier_report.energy_e,
            ir_sha256=sha256_jcs(horizon_out.certified_ir.model_dump(mode="json")),
        )
        if horizon_out.status == "HOLD_CHECKPOINTED":
            return CegarRunResult(
                run_id=req.run_id,
                status="HOLD_CHECKPOINTED",
                policy=req.policy,
                wave_number=wave_number,
                attempts_used=len(attempts),
                certified_ir=best.certified_ir,
                draft_ir=best.draft_ir,
                verifier_report=best.report,
                v7_verdict=best.v7_verdict,
                v8_verdict=horizon_out.v8_verdict,
                attempts=attempts,
                ontology_proposals=proposals,
                horizon_assumptions=assumptions,
                checkpoint=horizon_out.checkpoint,
                turns_used=turns_used,
                tokens_used=tokens_used,
                token_count_source=lit_tok_src,
                horizon_auto_steers_used=horizon_out.horizon_auto_steers_used,
                v8_calls_used=horizon_out.v8_calls_used,
                final_energy_e=best.energy_e,
                cycle_halted=cycle_halted,
                last_seq=seq_ref[0],
            )
        return seal_cegar_result(
            req=req,
            best=best,
            status="CERTIFIED",
            wave_number=wave_number,
            attempts=attempts,
            proposals=proposals,
            assumptions=assumptions,
            v8_verdict=horizon_out.v8_verdict,
            turns_used=turns_used,
            tokens_used=tokens_used,
            tok_source=lit_tok_src,
            auto_steers=horizon_out.horizon_auto_steers_used,
            v8_calls=horizon_out.v8_calls_used,
            cycle_halted=cycle_halted,
            seq_ref=seq_ref,
        )

    return seal_cegar_result(
        req=req,
        best=best,
        status="CERTIFIED",
        wave_number=wave_number,
        attempts=attempts,
        proposals=proposals,
        assumptions=assumptions,
        v8_verdict=None,
        turns_used=turns_used,
        tokens_used=tokens_used,
        tok_source=lit_tok_src,
        auto_steers=0,
        v8_calls=0,
        cycle_halted=cycle_halted,
        seq_ref=seq_ref,
    )


async def execute_v8_horizon_review(
    req: CegarRunRequest,
    prior_result: CegarRunResult,
) -> CegarRunResult:
    """In-place V8 Horizon upgrade for a CERTIFIED_FAST wave without bumping wave_number."""
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
    policy_spec = CEGAR_POLICY_SPECS[prior_result.policy]
    effective_budget = req.budget or policy_spec.budget
    base_ctx = (req.verifier_ctx or VerifierContext()).model_copy(
        update={
            "profiles": list(req.profiles),
            "ontology": req.ontology,
            "evolution_locks": req.evolution_locks,
            "prior_certified_ir": req.prior_certified_ir,
            "policy": prior_result.policy,
        }
    )
    report = prior_result.verifier_report or run_v1_to_v6_gates(
        prior_result.certified_ir,
        base_ctx,
        prover=req.prover,
        include_v6b=True,
    )
    if report.v1_v6_fail_count > 0:
        raise OpenDVError(
            "Cannot run in-place horizon review when symbolic gates V1-V6 fail.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            run_id=prior_result.run_id,
        )
    start_seq = max(
        prior_result.last_seq,
        prior_result.checkpoint.last_seq if prior_result.checkpoint is not None else 0,
        req.checkpoint.last_seq if req.checkpoint is not None else 0,
    )
    seq_ref = [start_seq]
    src_fp = sources_fingerprint(req.profiles)
    merged_assumptions: dict[str, HorizonAssumptionSpec] = {
        a.assumption_id: a for a in prior_result.horizon_assumptions
    }
    for a in req.horizon_assumptions:
        merged_assumptions[a.assumption_id] = a
    assumptions = list(merged_assumptions.values())
    if req.checkpoint is not None and req.checkpoint.phase == "S6-06":
        hq_list = [q for q in req.checkpoint.pending_questions if isinstance(q, HorizonQuestion)]
        assumptions = record_horizon_assumptions_from_answers(
            assumptions,
            hq_list,
            req.hold_answers,
        )
    sig_seq = req.last_sig_seq
    if req.control_reader is not None:
        sig_seq = max(
            sig_seq,
            len(req.control_reader.poll_signals(prior_result.run_id, after_seq=0)),
        )
    horizon_out = await run_v8_outer_loop(
        run_id=prior_result.run_id,
        draft_ir=prior_result.draft_ir,
        certified_ir=prior_result.certified_ir,
        verifier_report=report,
        verifier_ctx=base_ctx,
        profiles=req.profiles,
        locks=req.evolution_locks,
        prior_ir=req.prior_certified_ir,
        prover=req.prover,
        policy=prior_result.policy,
        budget=effective_budget,
        harness=req.harness,
        horizon_assumptions=assumptions,
        src_fingerprint=src_fp,
        turns_used=prior_result.turns_used,
        tokens_used=prior_result.tokens_used,
        token_count_source=prior_result.token_count_source,
        seq_ref=seq_ref,
        event_sink=req.event_sink,
        checkpoint_store=req.checkpoint_store,
        model_overrides=req.model_overrides,
        v7_verdict=prior_result.v7_verdict,
        horizon_review_requested=True,
        last_sig_seq=sig_seq,
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
        return prior_result.model_copy(
            update={
                "status": "HOLD_CHECKPOINTED",
                "certified_ir": horizon_out.certified_ir,
                "draft_ir": horizon_out.draft_ir,
                "verifier_report": horizon_out.verifier_report,
                "v8_verdict": horizon_out.v8_verdict,
                "horizon_assumptions": horizon_out.horizon_assumptions,
                "checkpoint": horizon_out.checkpoint,
                "turns_used": total_turns,
                "tokens_used": total_tokens,
                "token_count_source": out_tok_src,
                "horizon_auto_steers_used": horizon_out.horizon_auto_steers_used,
                "v8_calls_used": horizon_out.v8_calls_used,
                "last_seq": seq_ref[0],
            }
        )
    emit_run_event(
        req.event_sink,
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
        prior_ir=req.prior_certified_ir,
        locks=req.evolution_locks,
        force_refresh=(horizon_out.horizon_auto_steers_used > 0),
    )
    evo_lock = build_evolution_lock_from_certified(sealed_ir)
    emit_run_event(
        req.event_sink,
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
            "wave_number": prior_result.wave_number,
            "policy": prior_result.policy,
            "certified_ir": sealed_ir,
            "draft_ir": horizon_out.draft_ir,
            "evolution_lock": evo_lock,
            "verifier_report": horizon_out.verifier_report,
            "v8_verdict": horizon_out.v8_verdict,
            "horizon_assumptions": horizon_out.horizon_assumptions,
            "turns_used": total_turns,
            "tokens_used": total_tokens,
            "token_count_source": out_tok_src,
            "horizon_auto_steers_used": horizon_out.horizon_auto_steers_used,
            "v8_calls_used": horizon_out.v8_calls_used,
            "final_energy_e": horizon_out.verifier_report.energy_e,
            "last_seq": seq_ref[0],
        }
    )
