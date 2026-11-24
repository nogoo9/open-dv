"""Step 6 cluster-sharded CEGAR controller (S6-01..S6-07)."""

from __future__ import annotations

from typing import Literal

from opendv.agents._cegar_finalize import execute_v8_horizon_review, finalize_cegar_outcome
from opendv.agents._cegar_helpers import (
    build_resume_payload,
    commit_hold_checkpoint,
    emit_run_event,
    enforce_accept_best_gate,
    poll_control_signals,
    run_ship_vault_synthesis,
    sources_fingerprint,
)
from opendv.agents._cegar_horizon import record_horizon_assumptions_from_answers
from opendv.agents._cegar_models import (
    CandidateState,
    CegarRunRequest,
    CegarRunResult,
    CegarStatus,
    is_better_candidate,
)
from opendv.agents.critics import audit_v6b_justifications, run_v7_semantic_critic
from opendv.agents.ports import enforce_v7_tier_lock
from opendv.models.diagnostics import HorizonQuestion
from opendv.models.json_hasher import sha256_jcs
from opendv.models.policy import CEGAR_POLICY_SPECS, compute_cegar_energy
from opendv.models.project import OntologyProposalSpec
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec, SemanticCriticVerdict
from opendv.verifier.l1_codemods import run_l1_codemods
from opendv.verifier.ports import VerifierContext, VerifierReport
from opendv.verifier.v2_5_gates import run_v1_to_v6_gates
from opendv.verifier.v6b_antipatterns import evaluate_v6b_accounting


async def run_step6_cegar(req: CegarRunRequest) -> CegarRunResult:
    """Execute S6-01..S6-07 Step 6 CEGAR loop with L1, L2, V7, L3 Hold, and V8 Horizon."""
    enforce_v7_tier_lock(req.policy, req.v7_critic_tier)
    policy_spec = CEGAR_POLICY_SPECS[req.policy]
    effective_budget = req.budget or policy_spec.budget
    wave_number = req.wave_number_override or (
        req.prior_certified_ir.wave_number + 1
        if req.prior_certified_ir is not None
        else (req.evolution_locks.wave_number + 1 if req.evolution_locks is not None else 1)
    )
    src_fp = sources_fingerprint(req.profiles)
    base_ctx = (req.verifier_ctx or VerifierContext()).model_copy(
        update={
            "profiles": list(req.profiles),
            "ontology": req.ontology,
            "evolution_locks": req.evolution_locks,
            "prior_certified_ir": req.prior_certified_ir,
            "policy": req.policy,
        }
    )

    seq_ref = [int(req.checkpoint.last_seq) if req.checkpoint else 0]
    current_seq_fn = getattr(req.event_sink, "current_seq", None)
    if callable(current_seq_fn):
        seq_ref[0] = max(seq_ref[0], int(current_seq_fn(req.run_id)))
    turns_used = int(req.checkpoint.budget_turns_used) if req.checkpoint else 0
    tokens_used = int(req.checkpoint.budget_tokens_used) if req.checkpoint else 0
    tok_source: Literal["reported", "estimated"] = (
        req.checkpoint.token_count_source if req.checkpoint else "estimated"
    )
    assumptions = list(req.horizon_assumptions)
    if req.checkpoint is not None and req.checkpoint.phase == "S6-06":
        hq_list = [q for q in req.checkpoint.pending_questions if isinstance(q, HorizonQuestion)]
        assumptions = record_horizon_assumptions_from_answers(
            assumptions,
            hq_list,
            req.hold_answers,
        )

    emit_run_event(
        req.event_sink,
        seq_ref,
        run_id=req.run_id,
        event_type="STAGE_UPDATE",
        payload={"stage_id": "S6-01", "attempt": 0, "energy_e": 0, "status": "STARTED"},
    )

    cp_payload = req.checkpoint.resume_payload if req.checkpoint is not None else {}
    raw_sig_seq = cp_payload.get("last_sig_seq", req.last_sig_seq)
    last_sig_seq = [
        int(raw_sig_seq)
        if isinstance(raw_sig_seq, int) and not isinstance(raw_sig_seq, bool)
        else 0
    ]
    raw_hints = cp_payload.get("hints")
    hints: list[str] = [str(h) for h in raw_hints] if isinstance(raw_hints, list) else []
    b_turns = cp_payload.get("budget_max_turns")
    b_tokens = cp_payload.get("budget_max_tokens")
    b_timeout = cp_payload.get("budget_timeout_seconds")
    if (
        req.budget is None
        and isinstance(b_turns, int)
        and not isinstance(b_turns, bool)
    ):
        effective_budget = effective_budget.model_copy(
            update={
                "max_turns": b_turns,
                "max_tokens": (
                    b_tokens
                    if isinstance(b_tokens, int) and not isinstance(b_tokens, bool)
                    else effective_budget.max_tokens
                ),
                "timeout_seconds": (
                    b_timeout
                    if isinstance(b_timeout, int) and not isinstance(b_timeout, bool)
                    else effective_budget.timeout_seconds
                ),
            }
        )
    attempts: list[AttemptSnapshotSpec] = []
    proposals: list[OntologyProposalSpec] = []
    seen_digests: set[str] = set()
    best: CandidateState | None = None
    prior_report: VerifierReport | None = None
    prior_v7_verdict: SemanticCriticVerdict | None = None
    cycle_halted = False
    accept_best_requested = bool(cp_payload.get("accept_best_requested", False))
    horizon_review_requested = bool(
        req.checkpoint is not None and cp_payload.get("horizon_review_requested", False)
    )
    skip_synthesis = False

    if (
        req.checkpoint is not None
        and req.checkpoint.phase == "S6-06"
        and req.resumed_certified_ir is not None
        and req.resumed_draft_ir is not None
    ):
        resumed_report = run_v1_to_v6_gates(
            req.resumed_certified_ir,
            base_ctx,
            prover=req.prover,
            include_v6b=True,
        )
        raw_v7_score = req.checkpoint.resume_payload.get("v7_score")
        resumed_v7 = (
            SemanticCriticVerdict(
                score=int(raw_v7_score),
                findings=[],
                accepted_waiver_ids=[
                    j.waiver_id for j in req.resumed_certified_ir.justifications if j.reviewed_by_v7
                ],
            )
            if isinstance(raw_v7_score, int) and not isinstance(raw_v7_score, bool)
            else None
        )
        best = CandidateState(
            draft_ir=req.resumed_draft_ir,
            certified_ir=req.resumed_certified_ir,
            report=resumed_report,
            v7_verdict=resumed_v7,
            energy_e=resumed_report.energy_e,
            ir_sha256=sha256_jcs(req.resumed_certified_ir.model_dump(mode="json")),
        )
        prior_report = resumed_report
        if resumed_report.v1_v6_fail_count == 0 and (
            req.policy != "strict"
            or (
                resumed_report.v6b_fail_count == 0
                and resumed_v7 is not None
                and resumed_v7.score >= (policy_spec.v7_floor or 95)
            )
        ):
            skip_synthesis = True

    max_attempts = 1 if req.policy == "symbolic_only" else max(1, policy_spec.k_max)

    for attempt_idx in range(1, max_attempts + 1):
        pause_req, acc_best, hor_rev, effective_budget = poll_control_signals(
            req.control_reader,
            run_id=req.run_id,
            last_signal_seq=last_sig_seq,
            budget=effective_budget,
            hints=hints,
        )
        if acc_best:
            accept_best_requested = True
        if hor_rev:
            horizon_review_requested = True
        if pause_req:
            pause_resume = build_resume_payload(
                {"paused_before_attempt": attempt_idx},
                last_sig_seq=last_sig_seq[0],
                hints=hints,
                accept_best_requested=accept_best_requested,
                horizon_review_requested=horizon_review_requested,
                effective_budget=effective_budget,
                base_budget=policy_spec.budget,
            )
            cp = commit_hold_checkpoint(
                run_id=req.run_id,
                phase="S6-02",
                ontology_version=req.ontology.ontology_version,
                src_fingerprint=src_fp,
                pending_questions=[],
                turns_used=turns_used,
                tokens_used=tokens_used,
                token_count_source=tok_source,
                resume_payload=pause_resume,
                seq_ref=seq_ref,
                event_sink=req.event_sink,
                checkpoint_store=req.checkpoint_store,
            )
            return CegarRunResult(
                run_id=req.run_id,
                status="HOLD_CHECKPOINTED",
                policy=req.policy,
                wave_number=wave_number,
                attempts_used=len(attempts),
                draft_ir=best.draft_ir if best else req.resumed_draft_ir,
                certified_ir=best.certified_ir if best else req.resumed_certified_ir,
                verifier_report=best.report if best else None,
                attempts=attempts,
                ontology_proposals=proposals,
                horizon_assumptions=assumptions,
                checkpoint=cp,
                turns_used=turns_used,
                tokens_used=tokens_used,
                token_count_source=tok_source,
                last_seq=seq_ref[0],
            )

        if skip_synthesis:
            break

        if accept_best_requested and best is not None:
            enforce_accept_best_gate(
                policy=req.policy,
                v1_v6_fail=best.report.v1_v6_fail_count,
                v6b_fail=best.report.v6b_fail_count,
                v7_score=best.v7_verdict.score if best.v7_verdict else None,
            )
            if best.report.v1_v6_fail_count == 0:
                break

        if turns_used >= effective_budget.max_turns or tokens_used >= effective_budget.max_tokens:
            break

        emit_run_event(
            req.event_sink,
            seq_ref,
            run_id=req.run_id,
            event_type="STAGE_UPDATE",
            payload={
                "stage_id": "S6-02",
                "attempt": attempt_idx,
                "energy_e": best.energy_e if best else 0,
                "status": "RUNNING",
            },
        )
        draft, turn_props, t_used, tok_used, t_src = await run_ship_vault_synthesis(
            run_id=req.run_id,
            project_id=req.project_id,
            wave_number=wave_number,
            attempt=attempt_idx,
            ontology=req.ontology,
            profiles=req.profiles,
            clusters=req.clusters,
            locks=req.evolution_locks,
            prior_report=prior_report,
            hints=hints,
            hold_answers=req.hold_answers,
            policy=req.policy,
            budget=effective_budget,
            harness=req.harness,
            model_overrides=req.model_overrides,
            event_sink=req.event_sink,
            prior_v7_verdict=prior_v7_verdict,
        )
        turns_used += t_used
        tokens_used += tok_used
        if t_src == "reported":
            tok_source = "reported"
        proposals.extend(turn_props)
        if any(j.actor != "agent" or j.reviewed_by_v7 for j in draft.justifications):
            draft = draft.model_copy(
                update={
                    "justifications": [
                        j.model_copy(update={"actor": "agent", "reviewed_by_v7": False})
                        if j.actor != "agent" or j.reviewed_by_v7
                        else j
                        for j in draft.justifications
                    ]
                }
            )

        answered_ids = set(req.hold_answers.answers.keys()) if req.hold_answers else set()
        unresolved_clars = [
            q for q in draft.pending_clarifications if q.question_id not in answered_ids
        ]
        if unresolved_clars:
            clar_resume = build_resume_payload(
                {"attempt": attempt_idx},
                last_sig_seq=last_sig_seq[0],
                hints=hints,
                accept_best_requested=accept_best_requested,
                horizon_review_requested=horizon_review_requested,
                effective_budget=effective_budget,
                base_budget=policy_spec.budget,
            )
            cp = commit_hold_checkpoint(
                run_id=req.run_id,
                phase="S6-05",
                ontology_version=req.ontology.ontology_version,
                src_fingerprint=src_fp,
                pending_questions=unresolved_clars,
                turns_used=turns_used,
                tokens_used=tokens_used,
                token_count_source=tok_source,
                resume_payload=clar_resume,
                seq_ref=seq_ref,
                event_sink=req.event_sink,
                checkpoint_store=req.checkpoint_store,
            )
            return CegarRunResult(
                run_id=req.run_id,
                status="HOLD_CHECKPOINTED",
                policy=req.policy,
                wave_number=wave_number,
                attempts_used=len(attempts),
                draft_ir=draft,
                attempts=attempts,
                ontology_proposals=proposals,
                horizon_assumptions=assumptions,
                checkpoint=cp,
                turns_used=turns_used,
                tokens_used=tokens_used,
                token_count_source=tok_source,
                last_seq=seq_ref[0],
            )

        emit_run_event(
            req.event_sink,
            seq_ref,
            run_id=req.run_id,
            event_type="STAGE_UPDATE",
            payload={
                "stage_id": "S6-03",
                "attempt": attempt_idx,
                "energy_e": 0,
                "status": "RUNNING",
            },
        )
        draft_l1, certified_ir, _ = run_l1_codemods(
            draft,
            profiles=req.profiles,
            ontology=req.ontology,
            locks=req.evolution_locks,
            prior_ir=req.prior_certified_ir,
            cdc_volatility_by_table=base_ctx.cdc_volatility_by_table,
            fk_turnover_by_table=base_ctx.fk_turnover_by_table,
            entity_count_overrides=base_ctx.entity_count_overrides,
        )

        emit_run_event(
            req.event_sink,
            seq_ref,
            run_id=req.run_id,
            event_type="STAGE_UPDATE",
            payload={
                "stage_id": "S6-04",
                "attempt": attempt_idx,
                "energy_e": 0,
                "status": "RUNNING",
            },
        )
        report = run_v1_to_v6_gates(certified_ir, base_ctx, prover=req.prover, include_v6b=True)
        emit_run_event(
            req.event_sink,
            seq_ref,
            run_id=req.run_id,
            event_type="GATE_CHECK_STREAM",
            payload={
                "gate_id": "V1-V6",
                "passed_count": 0 if report.v1_v6_fail_count else 1,
                "total_count": 1,
                "diagnostics": [d.model_dump(mode="json") for d in report.v1_v6_diagnostics[:10]],
            },
        )

        v7_verdict: SemanticCriticVerdict | None = None
        if (
            req.policy != "symbolic_only"
            and report.v1_v6_fail_count == 0
            and policy_spec.v7_floor is not None
        ):
            v7_verdict, v7_res = await run_v7_semantic_critic(
                run_id=req.run_id,
                ir=certified_ir,
                verifier_report=report,
                profiles=req.profiles,
                ontology=req.ontology,
                policy=req.policy,
                budget=effective_budget,
                harness=req.harness,
                v7_critic_tier=req.v7_critic_tier,
                model_overrides=req.model_overrides,
                event_sink=req.event_sink,
            )
            turns_used += v7_res.turns_used
            tokens_used += v7_res.total_tokens
            if v7_res.token_count_source == "reported":
                tok_source = "reported"
            certified_ir, accepted_ids = audit_v6b_justifications(certified_ir, v7_verdict)
            if accepted_ids:
                accepted_set = set(accepted_ids)
                draft_l1 = draft_l1.model_copy(
                    update={
                        "justifications": [
                            j.model_copy(update={"reviewed_by_v7": True})
                            if j.waiver_id in accepted_set
                            else j
                            for j in draft_l1.justifications
                        ]
                    }
                )
            v6b_fail, waived_cnt, adv_cnt = evaluate_v6b_accounting(
                report.v6b_diagnostics,
                justifications=certified_ir.justifications,
                ontology_waivers=base_ctx.ontology_waivers,
            )
            energy_e = compute_cegar_energy(
                report.v1_v6_fail_count,
                v6b_fail,
                policy=req.policy,
                v7_score=v7_verdict.score,
            )
            report = report.model_copy(
                update={
                    "v6b_fail_count": v6b_fail,
                    "waived_v6b_count": waived_cnt,
                    "advisory_count": adv_cnt,
                    "energy_e": energy_e,
                    "passed": report.v1_v6_fail_count == 0
                    and not (policy_spec.require_zero_unwaived_v6b and v6b_fail > 0),
                }
            )
        else:
            energy_e = compute_cegar_energy(
                report.v1_v6_fail_count,
                report.v6b_fail_count,
                policy=req.policy,
            )

        ir_sha = sha256_jcs(certified_ir.model_dump(mode="json"))
        snap = AttemptSnapshotSpec(
            attempt=attempt_idx,
            ir_sha256=ir_sha,
            energy_e=energy_e,
            v7_score=v7_verdict.score if v7_verdict else None,
            diagnostics=report.all_diagnostics,
            draft_ir=draft_l1,
        )
        attempts.append(snap)
        if req.checkpoint_store is not None:
            req.checkpoint_store.save_attempt_snapshot(req.run_id, snap)
        emit_run_event(
            req.event_sink,
            seq_ref,
            run_id=req.run_id,
            event_type="ATTEMPT_SNAPSHOT",
            payload={
                "attempt": attempt_idx,
                "energy_e": energy_e,
                "ir_sha256": ir_sha,
                "gate_summary": {
                    "v1_v6_fail": report.v1_v6_fail_count,
                    "v6b_fail": report.v6b_fail_count,
                },
            },
        )

        cand = CandidateState(
            draft_ir=draft_l1,
            certified_ir=certified_ir,
            report=report,
            v7_verdict=v7_verdict,
            energy_e=energy_e,
            ir_sha256=ir_sha,
        )
        if is_better_candidate(cand, best):
            best = cand
        prior_report = report
        if v7_verdict is not None and (
            (policy_spec.v7_floor is not None and v7_verdict.score < policy_spec.v7_floor)
            or report.v6b_fail_count > 0
            or any(not j.reviewed_by_v7 for j in certified_ir.justifications)
        ):
            prior_v7_verdict = v7_verdict
        else:
            prior_v7_verdict = None

        if ir_sha in seen_digests:
            cycle_halted = True
            break
        seen_digests.add(ir_sha)

        if req.policy == "symbolic_only":
            break
        if req.policy == "fast" and report.v1_v6_fail_count == 0:
            break
        if (
            report.v1_v6_fail_count == 0
            and policy_spec.v7_floor is not None
            and v7_verdict is not None
            and v7_verdict.score >= policy_spec.v7_floor
            and (not policy_spec.require_zero_unwaived_v6b or report.v6b_fail_count == 0)
        ):
            break

    _pause, acc_best, hor_rev, effective_budget = poll_control_signals(
        req.control_reader,
        run_id=req.run_id,
        last_signal_seq=last_sig_seq,
        budget=effective_budget,
        hints=hints,
    )
    if acc_best:
        accept_best_requested = True
    if hor_rev:
        horizon_review_requested = True

    return await finalize_cegar_outcome(
        req=req,
        best=best,
        wave_number=wave_number,
        base_ctx=base_ctx,
        effective_budget=effective_budget,
        src_fp=src_fp,
        attempts=attempts,
        proposals=proposals,
        assumptions=assumptions,
        turns_used=turns_used,
        tokens_used=tokens_used,
        tok_source=tok_source,
        cycle_halted=cycle_halted,
        accept_best_requested=accept_best_requested,
        horizon_review_requested=horizon_review_requested,
        seq_ref=seq_ref,
        last_sig_seq=last_sig_seq[0],
        hints=hints,
    )


async def run_v8_horizon_review(
    req: CegarRunRequest,
    prior_result: CegarRunResult,
) -> CegarRunResult:
    """In-place V8 Horizon upgrade for a CERTIFIED_FAST wave without bumping wave_number."""
    return await execute_v8_horizon_review(req, prior_result)


__all__ = [
    "CegarRunRequest",
    "CegarRunResult",
    "CegarStatus",
    "run_step6_cegar",
    "run_v8_horizon_review",
]
