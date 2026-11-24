"""CEGAR request/result models and sealing helpers for Step 6."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opendv.agents._cegar_helpers import emit_run_event
from opendv.agents.ports import (
    AgentHarnessPort,
    ControlSignalReader,
    RunCheckpointStore,
    RunEventSink,
)
from opendv.models.diagnostics import HorizonOptionSpec, HorizonQuestion, StrictModel
from opendv.models.events import RunCheckpointRecord
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy, HarnessBudget, ModelTier
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec, OntologyProposalSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR, SchemaWaveDelta
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    HorizonCriticVerdict,
    SemanticCriticVerdict,
)
from opendv.verifier.ports import IRRuntimeProver, VerifierContext, VerifierReport
from opendv.verifier.v2_5_gates import build_evolution_lock_from_certified

CegarStatus = Literal[
    "CERTIFIED",
    "CERTIFIED_FAST",
    "HOLD_CHECKPOINTED",
    "UNCERTIFIED_DRAFT",
    "FAILED",
]


class CegarRunRequest(BaseModel):
    """Input configuration and injected ports for executing Step 6 CEGAR."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    ontology: GraphAOntology
    profiles: Sequence[SourceProfile]
    clusters: Sequence[tuple[str, Sequence[str]]] = ()
    policy: CegarPolicy = "balanced"
    budget: HarnessBudget | None = None
    evolution_locks: EvolutionLockSpec | None = None
    prior_certified_ir: CertifiedVaultIR | None = None
    verifier_ctx: VerifierContext | None = None
    prover: IRRuntimeProver | None = None
    harness: AgentHarnessPort
    event_sink: RunEventSink | None = None
    control_reader: ControlSignalReader | None = None
    checkpoint_store: RunCheckpointStore | None = None
    horizon_assumptions: Sequence[HorizonAssumptionSpec] = ()
    hold_answers: HoldAnswersReq | None = None
    checkpoint: RunCheckpointRecord | None = None
    resumed_draft_ir: DraftVaultIR | None = None
    resumed_certified_ir: CertifiedVaultIR | None = None
    wave_number_override: int | None = Field(default=None, ge=1)
    last_sig_seq: int = Field(default=0, ge=0)
    v7_critic_tier: ModelTier = "FRONTIER_CRITIC"
    model_overrides: Mapping[str, str] | None = None


class CegarRunResult(StrictModel):
    """Complete output of a Step 6 CEGAR or in-place V8 Horizon review run."""

    run_id: str
    status: CegarStatus
    policy: CegarPolicy
    wave_number: int = Field(ge=1)
    attempts_used: int = Field(ge=0)
    certified_ir: CertifiedVaultIR | None = None
    draft_ir: DraftVaultIR | None = None
    evolution_lock: EvolutionLockSpec | None = None
    verifier_report: VerifierReport | None = None
    v7_verdict: SemanticCriticVerdict | None = None
    v8_verdict: HorizonCriticVerdict | None = None
    attempts: list[AttemptSnapshotSpec] = Field(default_factory=list)
    ontology_proposals: list[OntologyProposalSpec] = Field(default_factory=list)
    horizon_assumptions: list[HorizonAssumptionSpec] = Field(default_factory=list)
    checkpoint: RunCheckpointRecord | None = None
    turns_used: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    token_count_source: Literal["reported", "estimated"] = "estimated"
    horizon_auto_steers_used: int = Field(default=0, ge=0)
    v8_calls_used: int = Field(default=0, ge=0)
    final_energy_e: int | None = None
    cycle_halted: bool = False
    last_seq: int = Field(default=0, ge=0)


@dataclass
class CandidateState:
    """Best candidate snapshot tracked across CEGAR iterations."""

    draft_ir: DraftVaultIR
    certified_ir: CertifiedVaultIR
    report: VerifierReport
    v7_verdict: SemanticCriticVerdict | None
    energy_e: int
    ir_sha256: str


def is_better_candidate(new: CandidateState, current_best: CandidateState | None) -> bool:
    """Return True if new improves over current_best by symbolic pass then energy."""
    if current_best is None:
        return True
    new_key = (1 if new.report.v1_v6_fail_count > 0 else 0, new.energy_e)
    cur_key = (1 if current_best.report.v1_v6_fail_count > 0 else 0, current_best.energy_e)
    return new_key < cur_key


def attach_wave_delta(
    ir: CertifiedVaultIR,
    *,
    prior_ir: CertifiedVaultIR | None,
    locks: EvolutionLockSpec | None,
    force_refresh: bool = False,
) -> CertifiedVaultIR:
    """Attach SchemaWaveDelta onto Wave 2+ CertifiedVaultIR when not already populated."""
    if ir.wave_number < 2 or (ir.wave_delta is not None and not force_refresh):
        return ir
    from_wave = (
        prior_ir.wave_number
        if prior_ir
        else (locks.wave_number if locks else ir.wave_number - 1)
    )
    prior_hubs = (
        {h.hub_name for h in prior_ir.hubs}
        if prior_ir
        else set(locks.locked_hub_bks if locks else ())
    )
    prior_links = (
        {lnk.link_name for lnk in prior_ir.links}
        if prior_ir
        else set(locks.locked_links if locks else ())
    )
    prior_sats = (
        {s.sat_name for s in prior_ir.satellites}
        if prior_ir
        else set(locks.locked_sat_hash_diffs if locks else ())
    )

    added_entities = sorted(
        [h.hub_name for h in ir.hubs if h.hub_name not in prior_hubs]
        + [lnk.link_name for lnk in ir.links if lnk.link_name not in prior_links]
        + [
            s.sat_name
            for s in ir.satellites
            if s.sat_name not in prior_sats and not s.extends_sat_name
        ]
    )
    added_exts = sorted(
        s.sat_name
        for s in ir.satellites
        if s.extends_sat_name is not None and s.sat_name not in prior_sats
    )
    locked_entities = sorted(prior_hubs | prior_links | prior_sats)
    delta = SchemaWaveDelta(
        from_wave=max(1, from_wave),
        to_wave=ir.wave_number,
        added_entities=added_entities,
        added_sat_extensions=added_exts,
        locked_entities=locked_entities,
    )
    return ir.model_copy(update={"wave_delta": delta})


def seal_cegar_result(
    *,
    req: CegarRunRequest,
    best: CandidateState,
    status: Literal["CERTIFIED", "CERTIFIED_FAST"],
    wave_number: int,
    attempts: list[AttemptSnapshotSpec],
    proposals: list[OntologyProposalSpec],
    assumptions: list[HorizonAssumptionSpec],
    v8_verdict: HorizonCriticVerdict | None,
    turns_used: int,
    tokens_used: int,
    tok_source: Literal["reported", "estimated"],
    auto_steers: int,
    v8_calls: int,
    cycle_halted: bool,
    seq_ref: list[int],
) -> CegarRunResult:
    """Seal CertifiedVaultIR, build EvolutionLockSpec, emit RUN_COMPLETED, and return result."""
    emit_run_event(
        req.event_sink,
        seq_ref,
        run_id=req.run_id,
        event_type="STAGE_UPDATE",
        payload={
            "stage_id": "S6-07",
            "attempt": len(attempts),
            "energy_e": best.energy_e,
            "status": status,
        },
    )
    sealed_ir = attach_wave_delta(
        best.certified_ir,
        prior_ir=req.prior_certified_ir,
        locks=req.evolution_locks,
        force_refresh=(auto_steers > 0),
    )
    evo_lock = build_evolution_lock_from_certified(sealed_ir)
    emit_run_event(
        req.event_sink,
        seq_ref,
        run_id=req.run_id,
        event_type="RUN_COMPLETED",
        payload={"revision_id": f"wave-{wave_number}", "completion_status": status},
    )
    return CegarRunResult(
        run_id=req.run_id,
        status=status,
        policy=req.policy,
        wave_number=wave_number,
        attempts_used=len(attempts),
        certified_ir=sealed_ir,
        draft_ir=best.draft_ir,
        evolution_lock=evo_lock,
        verifier_report=best.report,
        v7_verdict=best.v7_verdict,
        v8_verdict=v8_verdict,
        attempts=attempts,
        ontology_proposals=proposals,
        horizon_assumptions=assumptions,
        turns_used=turns_used,
        tokens_used=tokens_used,
        token_count_source=tok_source,
        horizon_auto_steers_used=auto_steers,
        v8_calls_used=v8_calls,
        final_energy_e=best.energy_e,
        cycle_halted=cycle_halted,
        last_seq=seq_ref[0],
    )


def make_v8_low_score_question(score: int) -> HorizonQuestion:
    """Build Steward Accept vs Steer HorizonQuestion when V8 score < 85."""
    return HorizonQuestion(
        question_id="hq-v8-low-score",
        brk_code="BRK-01",
        title=f"Horizon resilience score {score}/100 requires Steward review",
        prompt=(
            f"V8 Horizon Critic scored architectural resilience at {score}/100 (< 85). "
            "Accept the current schema as-is or steer for revision."
        ),
        options=[
            HorizonOptionSpec(
                option_id="accept",
                label="Accept schema",
                description="Accept current schema and record horizon boundary assumption.",
                implications="Proceeds to seal and Step 7 target emission.",
            ),
            HorizonOptionSpec(
                option_id="steer",
                label="Steer schema",
                description="Hold and steer schema architecture before certification.",
                implications="Allows Steward guidance or structural hints before sealing.",
            ),
        ],
        impact_summary="Gates Horizon resilience scores below 85/100 for Steward review.",
    )


def preserve_v7_waivers(
    steered_cert: CertifiedVaultIR,
    best_cert: CertifiedVaultIR,
    v7_verdict: SemanticCriticVerdict | None,
) -> CertifiedVaultIR:
    """Re-apply V7-reviewed waiver flags onto steered CertifiedVaultIR."""
    approved = {j.waiver_id for j in best_cert.justifications if j.reviewed_by_v7}
    if v7_verdict is not None:
        approved.update(v7_verdict.accepted_waiver_ids)
    if not approved:
        return steered_cert
    return steered_cert.model_copy(
        update={
            "justifications": [
                j.model_copy(update={"reviewed_by_v7": True})
                if j.waiver_id in approved
                else j
                for j in steered_cert.justifications
            ]
        }
    )
