"""Layer-2c request, result, resume, wave-evolution, and candidate models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opendv.modeling.ports import (
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingPackLoader,
)
from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    HorizonOptionSpec,
    HorizonQuestion,
    StrictModel,
)
from opendv.models.events import RunCheckpointRecord
from opendv.models.json_hasher import sha256_jcs
from opendv.models.modeling_preferences import (
    FragilitySurfaceSummary,
    ModelingPreferences,
    ModelingStatus,
    NextActionEnum,
    RemediationHint,
)
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


class WaveEvolutionInput(StrictModel):
    """Wave 2+ evolution lock and prior certified IR inputs."""

    evolution_locks: EvolutionLockSpec | None = Field(
        default=None,
        description="Optional Wave N-1 immutable lock specification.",
    )
    prior_certified_ir: CertifiedVaultIR | None = Field(
        default=None,
        description="Prior CertifiedVaultIR carried forward into Wave N.",
    )
    prior_wave_id: str | None = Field(
        default=None,
        description="Optional prior wave revision identifier.",
    )
    wave_number_override: int | None = Field(
        default=None,
        ge=1,
        description="Explicit wave number override.",
    )


class ModelingResumeInput(StrictModel):
    """Hold/Draft resume checkpoint and steward answer inputs."""

    checkpoint: RunCheckpointRecord | None = Field(
        default=None,
        description="Persisted Hold or UncertifiedDraft checkpoint to resume from.",
    )
    checkpoint_seq: int | None = Field(
        default=None,
        ge=0,
        description="Optional checkpoint event sequence marker.",
    )
    hold_answers: HoldAnswersReq | None = Field(
        default=None,
        description="Steward answers resolving pending Hold questions.",
    )
    answered_question_ids: list[str] = Field(
        default_factory=list,
        description="Already-answered survey or hold question IDs to skip.",
    )
    revert_to_pre_steer: bool = Field(
        default=False,
        description="Restore pre-steer certified snapshot directly without re-synthesis.",
    )
    resumed_draft_ir: DraftVaultIR | None = Field(
        default=None,
        description="DraftVaultIR restored from checkpoint artifacts.",
    )
    resumed_certified_ir: CertifiedVaultIR | None = Field(
        default=None,
        description="CertifiedVaultIR restored for direct V8 review or revert.",
    )
    pre_steer_fallback_draft_ir: DraftVaultIR | None = Field(
        default=None,
        description="Snapshot DraftVaultIR preserved before auto/steward horizon steer.",
    )
    pre_steer_fallback_certified_ir: CertifiedVaultIR | None = Field(
        default=None,
        description="Snapshot CertifiedVaultIR preserved before auto/steward horizon steer.",
    )
    last_sig_seq: int = Field(
        default=0,
        ge=0,
        description="Last consumed control signal sequence number.",
    )


class VerifyDraftRequest(BaseModel):
    """Input payload for zero-LLM Mode-1 draft verification."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    draft_ir: DraftVaultIR = Field(description="Synthesized DraftVaultIR from external agent.")
    profiles: Sequence[SourceProfile] = Field(description="Graph B source profiles.")
    ontology: GraphAOntology | None = Field(default=None, description="Optional Graph A ontology.")
    policy: CegarPolicy = Field(default="balanced", description="Target CEGAR gate strictness.")
    modeling_preferences: ModelingPreferences = Field(
        default_factory=ModelingPreferences,
        description="Tier-1 deterministic DV2.0 preferences.",
    )
    prover: IRRuntimeProver | None = Field(default=None, description="Optional DuckDB prover.")
    verifier_ctx: VerifierContext | None = Field(default=None, description="Extra verifier ctx.")
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = Field(
        default=None,
        description="Optional PII-tokenized real sample rows keyed by table_id.",
    )
    table_sample_flags: Mapping[str, bool] | None = Field(
        default=None,
        description="Completeness flags indicating full vs sampled tables.",
    )
    real_row_proving: bool | None = Field(
        default=None,
        description="Explicit toggle for Pass-2 real-row proving.",
    )
    cdc_volatility_by_table: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Optional column-level CDC volatility telemetry per table_id.",
    )
    fk_turnover_by_table: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Optional foreign-key relationship turnover telemetry per table_id.",
    )
    persisted_waivers: Sequence[AntiPatternJustification] = Field(
        default=(),
        description="Prior Steward V6b waivers to preserve.",
    )
    prior_locks: Sequence[EvolutionLockSpec] = Field(
        default=(),
        description="Optional prior EvolutionLockSpec history.",
    )
    business_context_hints: str | None = Field(
        default=None,
        description="Forbidden in Mode 1 verify_draft_vault; presence triggers fail-closed error.",
    )
    guide_markdown: str | None = Field(
        default=None,
        description="Forbidden in Mode 1 verify_draft_vault; presence triggers fail-closed error.",
    )
    wave: WaveEvolutionInput = Field(
        default_factory=WaveEvolutionInput,
        description="Wave 2+ evolution lock inputs.",
    )


class VerifyDraftResult(StrictModel):
    """Structured result returned by zero-LLM Mode-1 draft verification."""

    status: Literal["CERTIFIED_FAST", "UNCERTIFIED_DRAFT"] = Field(
        description="Certification outcome under deterministic L1 + V1-V6b verification."
    )
    next_action: NextActionEnum = Field(
        description="Structured next action telling an agent or UI what to do next."
    )
    draft_ir: DraftVaultIR = Field(description="DraftVaultIR after deterministic L1 lowering.")
    certified_ir: CertifiedVaultIR = Field(description="CertifiedVaultIR candidate.")
    evolution_lock: EvolutionLockSpec | None = Field(
        default=None,
        description="Sealed EvolutionLockSpec when certified or previewed.",
    )
    verifier_report: VerifierReport = Field(description="Full V1-V6b + prover verification report.")
    remediation_hints: list[RemediationHint] = Field(
        default_factory=list,
        description="Actionable remediation hints derived from failing rules.",
    )
    deterministic_energy: int = Field(
        default=0,
        ge=0,
        description="Zero-LLM deterministic verification energy score.",
    )
    final_energy_e: int = Field(
        default=0,
        ge=0,
        description="Total gate energy score after deterministic checks.",
    )
    fragility_summary: FragilitySurfaceSummary | None = Field(
        default=None,
        description="Deterministic fragility surface summary for wave-1 lock-in awareness.",
    )
    preferences_sha256: str = Field(
        default="",
        description="SHA-256 JCS digest of active ModelingPreferences.",
    )


class DVModelingRequest(BaseModel):
    """Input configuration and injected ports for Mode-2 managed CEGAR modeling."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str = Field(default="run-local", min_length=1, description="Run identifier.")
    project_id: str = Field(
        default="proj-local",
        min_length=1,
        description="Owning project identifier.",
    )
    ontology: GraphAOntology = Field(description="Graph A conceptual ontology.")
    profiles: Sequence[SourceProfile] = Field(description="Graph B physical source profiles.")
    clusters: Sequence[tuple[str, Sequence[str]]] = Field(
        default=(),
        description="Explicit cluster partitions as (cluster_id, table_ids).",
    )
    policy: CegarPolicy = Field(default="balanced", description="CEGAR certification policy.")
    budget: HarnessBudget | None = Field(default=None, description="Budget override.")
    harness: ModelingHarnessPort | None = Field(
        default=None,
        description="Optional default harness (or pass harness=... to model_vault).",
    )
    pack_loader: ModelingPackLoader | None = Field(
        default=None,
        description="Optional loader for SKILL.md packs and dv2-modeling-guide.md.",
    )
    prover: IRRuntimeProver | None = Field(default=None, description="Optional DuckDB prover.")
    verifier_ctx: VerifierContext | None = Field(default=None, description="Extra verifier ctx.")
    event_sink: ModelingEventSink | None = Field(default=None, description="Run event sink.")
    control_reader: ModelingControlReader | None = Field(default=None, description="Signal reader.")
    checkpoint_store: ModelingCheckpointStore | None = Field(
        default=None,
        description="Checkpoint persistence port.",
    )
    modeling_preferences: ModelingPreferences = Field(
        default_factory=ModelingPreferences,
        description="Tier-1 deterministic DV2.0 preferences.",
    )
    business_context_hints: str | None = Field(
        default=None,
        max_length=12_000,
        description="Untrusted natural-language business context fenced into LLM prompts.",
    )
    guide_markdown: str | None = Field(
        default=None,
        description="Trusted concise DV2.0 guide markdown injected by Layer 4/caller.",
    )
    cdc_volatility_by_table: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Optional column-level CDC volatility rates keyed by table_id.",
    )
    fk_turnover_by_table: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Optional relationship turnover rates keyed by table_id.",
    )
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = Field(
        default=None,
        description="Optional PII-tokenized real sample rows keyed by table_id.",
    )
    table_sample_flags: Mapping[str, bool] | None = Field(
        default=None,
        description="Completeness flags per table_id.",
    )
    real_row_proving: bool | None = Field(
        default=None,
        description="Explicit override for Pass-2 real-row proving.",
    )
    persisted_waivers: Sequence[AntiPatternJustification] = Field(
        default=(),
        description="Preserved Steward V6b waivers.",
    )
    prior_locks: Sequence[EvolutionLockSpec] = Field(
        default=(),
        description="Optional prior EvolutionLockSpec history.",
    )
    horizon_assumptions: Sequence[HorizonAssumptionSpec] = Field(
        default=(),
        description="Prior Steward horizon decisions.",
    )
    wave: WaveEvolutionInput = Field(
        default_factory=WaveEvolutionInput,
        description="Wave 2+ evolution lock inputs.",
    )
    resume: ModelingResumeInput = Field(
        default_factory=ModelingResumeInput,
        description="Checkpoint resume and pre-steer fallback inputs.",
    )
    v7_critic_tier: ModelTier = Field(
        default="FRONTIER_CRITIC",
        description="Tier requested for Gate V7 Semantic Critic.",
    )
    model_overrides: Mapping[str, str] | None = Field(
        default=None,
        description="Optional agent or tier model overrides.",
    )


class DVModelingResult(StrictModel):
    """Complete output of Mode-2 CEGAR or in-place V8 Horizon review modeling run."""

    run_id: str = Field(description="Executed run identifier.")
    status: ModelingStatus = Field(description="Terminal or checkpoint status.")
    next_action: NextActionEnum = Field(description="Actionable next step for callers/UIs.")
    policy: CegarPolicy = Field(description="Policy enforced for this run.")
    wave_number: int = Field(ge=1, description="Model wave number.")
    attempts_used: int = Field(ge=0, description="Number of synthesis attempts evaluated.")
    certified_ir: CertifiedVaultIR | None = Field(default=None, description="Best certified IR.")
    draft_ir: DraftVaultIR | None = Field(default=None, description="Best DraftVaultIR.")
    evolution_lock: EvolutionLockSpec | None = Field(default=None, description="Sealed wave lock.")
    verifier_report: VerifierReport | None = Field(default=None, description="Final report.")
    fragility_summary: FragilitySurfaceSummary | None = Field(
        default=None,
        description="Deterministic fragility surface summary.",
    )
    v7_verdict: SemanticCriticVerdict | None = Field(default=None, description="V7 critic verdict.")
    v8_verdict: HorizonCriticVerdict | None = Field(default=None, description="V8 critic verdict.")
    anti_pattern_justifications: list[AntiPatternJustification] = Field(
        default_factory=list,
        description="Effective audited V6b waivers attached to the candidate IR.",
    )
    pending_questions: list[ClarificationQuestion | HorizonQuestion] = Field(
        default_factory=list,
        description="Open Hold clarification or Horizon review questions.",
    )
    suggested_questions: list[ClarificationQuestion] = Field(
        default_factory=list,
        description="Advisories discovered by hint_survey without halting auto runs.",
    )
    remediation_hints: list[RemediationHint] = Field(
        default_factory=list,
        description="Structured remediation hints when verification did not fully pass.",
    )
    attempts: list[AttemptSnapshotSpec] = Field(
        default_factory=list,
        description="Recorded per-attempt snapshots.",
    )
    ontology_proposals: list[OntologyProposalSpec] = Field(
        default_factory=list,
        description="Ontology delta proposals harvested from DraftVaultIR.",
    )
    horizon_assumptions: list[HorizonAssumptionSpec] = Field(
        default_factory=list,
        description="Recorded Steward Horizon decisions.",
    )
    checkpoint: RunCheckpointRecord | None = Field(
        default=None,
        description="Checkpoint record when held or uncertified.",
    )
    turns_used: int = Field(default=0, ge=0, description="Cumulative LLM turns used.")
    tokens_used: int = Field(default=0, ge=0, description="Cumulative tokens consumed.")
    token_count_source: Literal["reported", "estimated"] = Field(
        default="estimated",
        description="Origin of reported token counters.",
    )
    horizon_auto_steers_used: int = Field(default=0, ge=0, description="V8 steers used.")
    v8_calls_used: int = Field(default=0, ge=0, description="V8 critic calls used.")
    final_energy_e: int | None = Field(default=None, description="Combined final energy score.")
    deterministic_energy: int | None = Field(
        default=None,
        description="Zero-LLM deterministic energy component.",
    )
    preferences_sha256: str = Field(
        default="",
        description="SHA-256 JCS digest of active ModelingPreferences.",
    )
    modeling_guide_sha256: str = Field(
        default="",
        description="SHA-256 hex digest of injected modeling guide markdown.",
    )
    telemetry: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic counters and shard summary metrics.",
    )
    cycle_halted: bool = Field(default=False, description="True if oscillation cycle halted.")
    last_seq: int = Field(default=0, ge=0, description="Highest emitted event sequence.")


@dataclass
class CandidateState:
    """Best candidate snapshot tracked across CEGAR iterations."""

    draft_ir: DraftVaultIR
    certified_ir: CertifiedVaultIR
    report: VerifierReport
    v7_verdict: SemanticCriticVerdict | None
    energy_e: int
    ir_sha256: str


def compute_preferences_sha256(prefs: ModelingPreferences) -> str:
    """Return canonical JCS SHA-256 digest of active ModelingPreferences."""
    return sha256_jcs(prefs.model_dump(mode="json"))


def is_better_candidate(new: CandidateState, current_best: CandidateState | None) -> bool:
    """Return True if new improves over current_best by symbolic pass then energy."""
    if current_best is None:
        return True
    new_key = (1 if new.report.v1_v6_fail_count > 0 else 0, new.energy_e)
    cur_key = (1 if current_best.report.v1_v6_fail_count > 0 else 0, current_best.energy_e)
    return new_key < cur_key


def resolve_next_action(
    *,
    status: ModelingStatus,
    report: VerifierReport | None,
) -> NextActionEnum:
    """Derive deterministic NextActionEnum from terminal/checkpoint status and report."""
    if status in ("CERTIFIED", "CERTIFIED_FAST"):
        return "READY_FOR_DOWNSTREAM"
    if status == "HOLD_CHECKPOINTED":
        return "ASK_HUMAN_HOLD"
    if status == "FAILED":
        return "FAILED"
    if report is not None and report.v1_v6_fail_count == 0 and report.v6b_fail_count > 0:
        return "WAIVE_OR_ADJUST_PREFERENCES"
    return "REPAIR_DRAFT_IR"


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
                implications="Proceeds to seal and target emission.",
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


__all__ = [
    "CandidateState",
    "DVModelingRequest",
    "DVModelingResult",
    "ModelingResumeInput",
    "VerifyDraftRequest",
    "VerifyDraftResult",
    "WaveEvolutionInput",
    "attach_wave_delta",
    "compute_preferences_sha256",
    "is_better_candidate",
    "make_v8_low_score_question",
    "preserve_v7_waivers",
    "resolve_next_action",
]
