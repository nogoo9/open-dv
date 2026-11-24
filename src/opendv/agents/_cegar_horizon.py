"""Internal Gate V8 Horizon outer-loop execution, Bucket A steer cap, and regression ratchet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from opendv.agents._cegar_helpers import (
    build_resume_payload,
    commit_hold_checkpoint,
    emit_run_event,
)
from opendv.agents._cegar_models import make_v8_low_score_question, preserve_v7_waivers
from opendv.agents.critics import run_v8_horizon_critic
from opendv.agents.ports import (
    AgentHarnessPort,
    RunCheckpointStore,
    RunEventSink,
)
from opendv.models.diagnostics import HorizonOptionSpec, HorizonQuestion
from opendv.models.events import RunCheckpointRecord
from opendv.models.policy import CEGAR_POLICY_SPECS, CegarPolicy, HarnessBudget
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.verifier.l1_codemods import run_l1_codemods
from opendv.verifier.ports import IRRuntimeProver, VerifierContext, VerifierReport
from opendv.verifier.v2_5_gates import run_v1_to_v6_gates


@dataclass
class HorizonOutcome:
    """Result of executing the bounded S6-06 V8 Horizon outer loop."""

    status: str  # "PROCEED" | "HOLD_CHECKPOINTED"
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


def _v8_resume_payload(
    base: dict[str, str | int | bool | list[str]],
    *,
    horizon_review_requested: bool,
    v7_verdict: SemanticCriticVerdict | None,
    last_sig_seq: int = 0,
    hints: Sequence[str] = (),
    accept_best_requested: bool = False,
    effective_budget: HarnessBudget | None = None,
    base_budget: HarnessBudget | None = None,
) -> dict[str, str | int | bool | list[str]]:
    return build_resume_payload(
        base,
        last_sig_seq=last_sig_seq,
        hints=hints,
        accept_best_requested=accept_best_requested,
        horizon_review_requested=horizon_review_requested,
        effective_budget=effective_budget,
        base_budget=base_budget,
        v7_score=v7_verdict.score if v7_verdict is not None else None,
    )


def record_horizon_assumptions_from_answers(
    existing: Sequence[HorizonAssumptionSpec],
    pending_questions: Sequence[HorizonQuestion],
    answers: HoldAnswersReq | None,
) -> list[HorizonAssumptionSpec]:
    """Persist Steward choices for HorizonQuestion prompts into HorizonAssumptionSpec list."""
    updated = list(existing)
    if answers is None or not answers.answers:
        return updated
    index_by_id = {a.assumption_id: idx for idx, a in enumerate(updated)}
    for q in pending_questions:
        ans = answers.answers.get(q.question_id)
        if not ans:
            continue
        assump_id = f"ha-{q.question_id}"
        decision: Literal["ACCEPT", "STEER"] = "STEER" if "steer" in ans.lower() else "ACCEPT"
        new_spec = HorizonAssumptionSpec(
            assumption_id=assump_id,
            brk_code=q.brk_code,
            title=q.title,
            notes=ans,
            steward_decision=decision,
            selected_option_ids=[ans],
            added_fields=[],
        )
        if assump_id in index_by_id:
            updated[index_by_id[assump_id]] = new_spec
        else:
            index_by_id[assump_id] = len(updated)
            updated.append(new_spec)
    return updated


async def run_v8_outer_loop(
    *,
    run_id: str,
    draft_ir: DraftVaultIR,
    certified_ir: CertifiedVaultIR,
    verifier_report: VerifierReport,
    verifier_ctx: VerifierContext,
    profiles: Sequence[SourceProfile],
    locks: EvolutionLockSpec | None,
    prior_ir: CertifiedVaultIR | None,
    prover: IRRuntimeProver | None,
    policy: CegarPolicy,
    budget: HarnessBudget,
    harness: AgentHarnessPort,
    horizon_assumptions: Sequence[HorizonAssumptionSpec],
    src_fingerprint: str,
    turns_used: int,
    tokens_used: int,
    token_count_source: str,
    seq_ref: list[int],
    event_sink: RunEventSink | None,
    checkpoint_store: RunCheckpointStore | None,
    model_overrides: Mapping[str, str] | None = None,
    v7_verdict: SemanticCriticVerdict | None = None,
    horizon_review_requested: bool = False,
    last_sig_seq: int = 0,
    hints: Sequence[str] = (),
    accept_best_requested: bool = False,
) -> HorizonOutcome:
    """Execute bounded V8 Horizon outer loop (max 1 Bucket A steer, 0 Bucket B retries)."""
    base_budget = CEGAR_POLICY_SPECS[policy].budget

    def _hold_payload(
        extra: dict[str, str | int | bool | list[str]],
    ) -> dict[str, str | int | bool | list[str]]:
        return _v8_resume_payload(
            extra,
            horizon_review_requested=horizon_review_requested,
            v7_verdict=v7_verdict,
            last_sig_seq=last_sig_seq,
            hints=hints,
            accept_best_requested=accept_best_requested,
            effective_budget=budget,
            base_budget=base_budget,
        )
    emit_run_event(
        event_sink,
        seq_ref,
        run_id=run_id,
        event_type="STAGE_UPDATE",
        payload={
            "stage_id": "S6-06",
            "attempt": 1,
            "energy_e": verifier_report.energy_e,
            "status": "RUNNING",
        },
    )
    assumptions = list(horizon_assumptions)
    turns_added = 0
    tokens_added = 0
    tok_src: Literal["reported", "estimated"] = (
        "reported" if token_count_source == "reported" else "estimated"
    )
    auto_steers = 0
    v8_calls = 0

    best_draft = draft_ir
    best_cert = certified_ir
    best_report = verifier_report

    verdict, v8_res = await run_v8_horizon_critic(
        run_id=run_id,
        ir=best_cert,
        profiles=profiles,
        locks=locks,
        horizon_assumptions=assumptions,
        policy=policy,
        budget=budget,
        harness=harness,
        model_overrides=model_overrides,
        event_sink=event_sink,
    )
    v8_calls += 1
    turns_added += v8_res.turns_used
    tokens_added += v8_res.total_tokens
    if v8_res.token_count_source == "reported":
        tok_src = "reported"

    # Guard 2: Bucket B pauses immediately (0 blind retries)
    if verdict.bucket_b_questions:
        cp = commit_hold_checkpoint(
            run_id=run_id,
            phase="S6-06",
            ontology_version=best_cert.ontology_version,
            src_fingerprint=src_fingerprint,
            pending_questions=verdict.bucket_b_questions,
            turns_used=turns_used + turns_added,
            tokens_used=tokens_used + tokens_added,
            token_count_source=tok_src,
            resume_payload=_hold_payload({"v8_classification": verdict.classification}),
            seq_ref=seq_ref,
            event_sink=event_sink,
            checkpoint_store=checkpoint_store,
        )
        return HorizonOutcome(
            status="HOLD_CHECKPOINTED",
            draft_ir=best_draft,
            certified_ir=best_cert,
            verifier_report=best_report,
            v8_verdict=verdict,
            horizon_assumptions=assumptions,
            horizon_auto_steers_used=auto_steers,
            v8_calls_used=v8_calls,
            turns_added=turns_added,
            tokens_added=tokens_added,
            token_count_source=tok_src,
            checkpoint=cp,
        )

    # Guard 4 fast-path: >= 85 and no Bucket A/B actions
    if verdict.score >= 85 and not verdict.bucket_a_ops:
        return HorizonOutcome(
            status="PROCEED",
            draft_ir=best_draft,
            certified_ir=best_cert,
            verifier_report=best_report,
            v8_verdict=verdict,
            horizon_assumptions=assumptions,
            horizon_auto_steers_used=auto_steers,
            v8_calls_used=v8_calls,
            turns_added=turns_added,
            tokens_added=tokens_added,
            token_count_source=tok_src,
        )

    already_accepted_low = any(
        a.assumption_id == "ha-hq-v8-low-score" and a.steward_decision == "ACCEPT"
        for a in assumptions
    )
    if not verdict.bucket_a_ops:
        if already_accepted_low:
            return HorizonOutcome(
                status="PROCEED",
                draft_ir=best_draft,
                certified_ir=best_cert,
                verifier_report=best_report,
                v8_verdict=verdict,
                horizon_assumptions=assumptions,
                horizon_auto_steers_used=auto_steers,
                v8_calls_used=v8_calls,
                turns_added=turns_added,
                tokens_added=tokens_added,
                token_count_source=tok_src,
            )
        cp = commit_hold_checkpoint(
            run_id=run_id,
            phase="S6-06",
            ontology_version=best_cert.ontology_version,
            src_fingerprint=src_fingerprint,
            pending_questions=[make_v8_low_score_question(verdict.score)],
            turns_used=turns_used + turns_added,
            tokens_used=tokens_used + tokens_added,
            token_count_source=tok_src,
            resume_payload=_hold_payload(
                {"v8_low_score_pause": True, "v8_score": verdict.score}
            ),
            seq_ref=seq_ref,
            event_sink=event_sink,
            checkpoint_store=checkpoint_store,
        )
        return HorizonOutcome(
            status="HOLD_CHECKPOINTED",
            draft_ir=best_draft,
            certified_ir=best_cert,
            verifier_report=best_report,
            v8_verdict=verdict,
            horizon_assumptions=assumptions,
            horizon_auto_steers_used=auto_steers,
            v8_calls_used=v8_calls,
            turns_added=turns_added,
            tokens_added=tokens_added,
            token_count_source=tok_src,
            checkpoint=cp,
        )

    # Guard 3: Bucket A structural steer (straight through L1 -> L2, capped at <= 1)
    if verdict.bucket_a_ops and auto_steers < 1:
        steered_draft, steered_cert, bucket_res = run_l1_codemods(
            best_draft,
            profiles=profiles,
            ontology=verifier_ctx.ontology,
            locks=locks,
            prior_ir=prior_ir,
            bucket_a_ops=verdict.bucket_a_ops,
            cdc_volatility_by_table=verifier_ctx.cdc_volatility_by_table,
            fk_turnover_by_table=verifier_ctx.fk_turnover_by_table,
            entity_count_overrides=verifier_ctx.entity_count_overrides,
        )
        if bucket_res is not None and bucket_res.requires_bucket_b:
            cp = commit_hold_checkpoint(
                run_id=run_id,
                phase="S6-06",
                ontology_version=best_cert.ontology_version,
                src_fingerprint=src_fingerprint,
                pending_questions=bucket_res.bucket_b_escalations,
                turns_used=turns_used + turns_added,
                tokens_used=tokens_used + tokens_added,
                token_count_source=tok_src,
                resume_payload=_hold_payload({"v8_bucket_b_escalation": True}),
                seq_ref=seq_ref,
                event_sink=event_sink,
                checkpoint_store=checkpoint_store,
            )
            return HorizonOutcome(
                status="HOLD_CHECKPOINTED",
                draft_ir=best_draft,
                certified_ir=best_cert,
                verifier_report=best_report,
                v8_verdict=verdict,
                horizon_assumptions=assumptions,
                horizon_auto_steers_used=auto_steers,
                v8_calls_used=v8_calls,
                turns_added=turns_added,
                tokens_added=tokens_added,
                token_count_source=tok_src,
                checkpoint=cp,
            )

        steered_cert = preserve_v7_waivers(steered_cert, best_cert, v7_verdict)
        steered_report = run_v1_to_v6_gates(
            steered_cert,
            verifier_ctx,
            prover=prover,
            include_v6b=True,
        )
        if steered_report.v1_v6_fail_count > 0 or (
            policy == "strict" and steered_report.v6b_fail_count > 0
        ):
            # Regression ratchet: retain prior best_candidate and pause cleanly for human review
            reg_q = HorizonQuestion(
                question_id="hq-v8-regression",
                brk_code="BRK-01",
                title="Horizon auto-steer regressed symbolic gates",
                prompt=(
                    "Automatic Bucket A structural steer introduced symbolic verification "
                    "failures. Prior converged schema candidate was retained."
                ),
                options=[
                    HorizonOptionSpec(
                        option_id="accept_best",
                        label="Accept retained schema",
                        description="Seal prior converged candidate and log boundary assumption.",
                        implications="Freezes Wave 1 schema as-is.",
                    )
                ],
                impact_summary="Retains prior V1-V6 passing candidate.",
            )
            cp = commit_hold_checkpoint(
                run_id=run_id,
                phase="S6-06",
                ontology_version=best_cert.ontology_version,
                src_fingerprint=src_fingerprint,
                pending_questions=[reg_q],
                turns_used=turns_used + turns_added,
                tokens_used=tokens_used + tokens_added,
                token_count_source=tok_src,
                resume_payload=_hold_payload({"v8_regression_ratchet": True}),
                seq_ref=seq_ref,
                event_sink=event_sink,
                checkpoint_store=checkpoint_store,
            )
            return HorizonOutcome(
                status="HOLD_CHECKPOINTED",
                draft_ir=best_draft,
                certified_ir=best_cert,
                verifier_report=best_report,
                v8_verdict=verdict,
                horizon_assumptions=assumptions,
                horizon_auto_steers_used=auto_steers,
                v8_calls_used=v8_calls,
                turns_added=turns_added,
                tokens_added=tokens_added,
                token_count_source=tok_src,
                checkpoint=cp,
            )

        auto_steers += 1
        approved_ids = {j.waiver_id for j in steered_cert.justifications if j.reviewed_by_v7}
        if approved_ids:
            steered_draft = steered_draft.model_copy(
                update={
                    "justifications": [
                        j.model_copy(update={"reviewed_by_v7": True})
                        if j.waiver_id in approved_ids
                        else j
                        for j in steered_draft.justifications
                    ]
                }
            )
        best_draft = steered_draft
        best_cert = steered_cert
        best_report = steered_report

        # Single V8 re-score after 1 steer
        verdict, v8_res_2 = await run_v8_horizon_critic(
            run_id=run_id,
            ir=best_cert,
            profiles=profiles,
            locks=locks,
            horizon_assumptions=assumptions,
            policy=policy,
            budget=budget,
            harness=harness,
            model_overrides=model_overrides,
            event_sink=event_sink,
        )
        v8_calls += 1
        turns_added += v8_res_2.turns_used
        tokens_added += v8_res_2.total_tokens
        if v8_res_2.token_count_source == "reported":
            tok_src = "reported"

        low_score_needs_pause = verdict.score < 85 and not already_accepted_low
        if verdict.bucket_b_questions or verdict.bucket_a_ops or low_score_needs_pause:
            questions = list(verdict.bucket_b_questions)
            resume_extra: dict[str, str | int | bool | list[str]]
            if questions:
                resume_extra = {"v8_classification": verdict.classification}
            elif verdict.bucket_a_ops:
                resume_extra = {"v8_steer_cap_reached": True}
                questions = [
                    HorizonQuestion(
                        question_id="hq-v8-steer-cap",
                        brk_code="BRK-01",
                        title="Horizon auto-steer limit reached",
                        prompt="Additional horizon steering requires steward confirmation.",
                        options=[
                            HorizonOptionSpec(
                                option_id="accept_steered",
                                label="Accept hardened schema",
                                description="Seal current hardened candidate.",
                                implications="Proceeds to Step 7 targets.",
                            )
                        ],
                        impact_summary="Enforces max 1 automatic horizon steer.",
                    )
                ]
            else:
                resume_extra = {"v8_low_score_pause": True, "v8_score": verdict.score}
                questions = [make_v8_low_score_question(verdict.score)]
            cp = commit_hold_checkpoint(
                run_id=run_id,
                phase="S6-06",
                ontology_version=best_cert.ontology_version,
                src_fingerprint=src_fingerprint,
                pending_questions=questions,
                turns_used=turns_used + turns_added,
                tokens_used=tokens_used + tokens_added,
                token_count_source=tok_src,
                resume_payload=_hold_payload(resume_extra),
                seq_ref=seq_ref,
                event_sink=event_sink,
                checkpoint_store=checkpoint_store,
            )
            return HorizonOutcome(
                status="HOLD_CHECKPOINTED",
                draft_ir=best_draft,
                certified_ir=best_cert,
                verifier_report=best_report,
                v8_verdict=verdict,
                horizon_assumptions=assumptions,
                horizon_auto_steers_used=auto_steers,
                v8_calls_used=v8_calls,
                turns_added=turns_added,
                tokens_added=tokens_added,
                token_count_source=tok_src,
                checkpoint=cp,
            )

    return HorizonOutcome(
        status="PROCEED",
        draft_ir=best_draft,
        certified_ir=best_cert,
        verifier_report=best_report,
        v8_verdict=verdict,
        horizon_assumptions=assumptions,
        horizon_auto_steers_used=auto_steers,
        v8_calls_used=v8_calls,
        turns_added=turns_added,
        tokens_added=tokens_added,
        token_count_source=tok_src,
    )
