"""Internal helpers for Mode-2 CEGAR: validation, controls, checkpoints, and results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from opendv.modeling.models import (
    CandidateState,
    DVModelingRequest,
    DVModelingResult,
    attach_wave_delta,
    compute_preferences_sha256,
    resolve_next_action,
)
from opendv.modeling.ports import (
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
    enforce_v7_tier_lock,
)
from opendv.modeling.prompts import (
    resolve_modeling_guide,
    scan_fragility_surface,
    validate_business_context_hints,
)
from opendv.modeling.verify_mode import (
    _build_remediation_hints,
    _build_verifier_context,
    validate_source_rows_pii_tokenized,
)
from opendv.models.diagnostics import ClarificationQuestion, HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunCheckpointRecord, RunEventType, RunStreamEvent
from opendv.models.json_hasher import sha256_jcs
from opendv.models.modeling_preferences import ModelingStatus
from opendv.models.policy import CEGAR_POLICY_SPECS, VALID_V6B_RULE_IDS, CegarPolicy, HarnessBudget
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec, OntologyProposalSpec
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec, HorizonCriticVerdict
from opendv.verifier.ports import IRRuntimeProver, VerifierContext
from opendv.verifier.v2_5_gates import build_evolution_lock_from_certified

type _PVal = str | int | bool | list[str]
type _PDict = dict[str, _PVal]


def _int_or(val: Any, default: int) -> int:
    return val if isinstance(val, int) and not isinstance(val, bool) else default


def _slist(val: Any) -> list[str]:
    return [str(x) for x in val] if isinstance(val, list) else []


@dataclass
class RunCtx:
    """Mutable Mode-2 CEGAR run state shared across stages."""

    req: DVModelingRequest
    harness: ModelingHarnessPort
    prover: IRRuntimeProver | None
    sink: ModelingEventSink | None
    ctrl: ModelingControlReader | None
    cp_store: ModelingCheckpointStore | None
    base_ctx: VerifierContext
    budget: HarnessBudget
    wave_number: int
    src_fp: str
    guide_text: str
    guide_sha: str
    seq_ref: list[int]
    last_sig_seq: list[int]
    turns_used: int = 0
    tokens_used: int = 0
    tok_source: Literal["reported", "estimated"] = "estimated"
    hints: list[str] = field(default_factory=list)
    assumptions: list[HorizonAssumptionSpec] = field(default_factory=list)
    answered_hkeys: list[str] = field(default_factory=list)
    attempts: list[AttemptSnapshotSpec] = field(default_factory=list)
    proposals: list[OntologyProposalSpec] = field(default_factory=list)
    suggested_qs: list[ClarificationQuestion] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    accept_best_requested: bool = False
    horizon_review_requested: bool = False
    post_steer: bool = False
    cycle_halted: bool = False
    fb_ref: str | None = None
    fb_draft: DraftVaultIR | None = None
    fb_cert: CertifiedVaultIR | None = None
    prior_v8_verdict: HorizonCriticVerdict | None = None


def emit_run_event(
    sink: ModelingEventSink | None, seq_ref: list[int], *,
    run_id: str, event_type: RunEventType, payload: dict[str, Any],
) -> None:
    """Emit monotonic Step 6 RunStreamEvent."""
    if sink is None:
        return
    cur = getattr(sink, "current_seq", None)
    if callable(cur):
        seq_ref[0] = max(seq_ref[0], int(cur(run_id)))
    seq_ref[0] += 1
    sink.emit(RunStreamEvent(
        seq=seq_ref[0], run_id=run_id, ts=datetime.now(tz=UTC).isoformat(),
        event_type=event_type, step="step6", payload=payload,
    ))


def init_run_ctx(
    req: DVModelingRequest, *, harness: ModelingHarnessPort | None,
    prover: IRRuntimeProver | None, event_sink: ModelingEventSink | None,
    control_reader: ModelingControlReader | None,
    checkpoint_store: ModelingCheckpointStore | None,
) -> tuple[RunCtx, dict[str, Any]]:
    """Validate inputs/ports and initialize RunCtx."""
    h, p = harness or req.harness, prover or req.prover
    if h is None or (p is None and req.policy != "symbolic_only"):
        raise OpenDVError(
            "Missing ModelingHarnessPort or IRRuntimeProver.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT, run_id=req.run_id,
        )
    enforce_v7_tier_lock(req.policy, req.v7_critic_tier)
    validate_business_context_hints(req.business_context_hints)
    guide_text, guide_sha = resolve_modeling_guide(req.guide_markdown, req.pack_loader)
    dis = req.modeling_preferences.disabled_v6b_blocks
    if any(r not in VALID_V6B_RULE_IDS for r in dis) or (req.policy == "strict" and dis):
        raise OpenDVError(
            "Invalid disabled_v6b_blocks.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT, run_id=req.run_id,
        )
    validate_source_rows_pii_tokenized(req.profiles, req.source_rows_by_table)

    sink, cp = event_sink or req.event_sink, req.resume.checkpoint
    budget = req.budget or CEGAR_POLICY_SPECS[req.policy].budget
    cp_p: dict[str, Any] = dict(cp.resume_payload) if cp else {}
    cur = getattr(sink, "current_seq", None)
    seq0 = max(cp.last_seq if cp else 0, int(cur(req.run_id)) if callable(cur) else 0)
    bt = cp_p.get("budget_max_turns")
    if req.budget is None and isinstance(bt, int) and not isinstance(bt, bool):
        budget = budget.model_copy(update={
            "max_turns": bt,
            "max_tokens": _int_or(cp_p.get("budget_max_tokens"), budget.max_tokens),
            "timeout_seconds": _int_or(cp_p.get("budget_timeout_seconds"), budget.timeout_seconds),
        })
    w = req.wave
    wave_no = (
        w.wave_number_override if w.wave_number_override is not None
        else (w.prior_certified_ir.wave_number + 1 if w.prior_certified_ir
              else (w.evolution_locks.wave_number + 1 if w.evolution_locks else 1))
    )
    src_fp = sha256_jcs([
        {"source_id": s.source_id, "schema_fingerprint": s.schema_fingerprint}
        for s in sorted(req.profiles, key=lambda x: x.source_id)
    ])
    nt = sum(len(x.tables) for x in req.profiles)
    nc = sum(len(t.columns) for x in req.profiles for t in x.tables)
    v8j = cp.prior_v8_verdict_json if cp else None
    return RunCtx(
        req=req, harness=h, prover=p, sink=sink, ctrl=control_reader or req.control_reader,
        cp_store=checkpoint_store or req.checkpoint_store, base_ctx=_build_verifier_context(req),
        budget=budget, wave_number=wave_no, src_fp=src_fp, guide_text=guide_text,
        guide_sha=guide_sha, seq_ref=[seq0],
        last_sig_seq=[_int_or(cp_p.get("last_sig_seq"), req.resume.last_sig_seq)],
        turns_used=cp.budget_turns_used if cp else 0,
        tokens_used=cp.budget_tokens_used if cp else 0,
        tok_source=cp.token_count_source if cp else "estimated",
        hints=_slist(cp_p.get("hints")), assumptions=list(req.horizon_assumptions),
        answered_hkeys=_slist(cp_p.get("answered_horizon_keys")),
        telemetry={"table_count": nt, "column_count": nc,
                   "sharded": nt > 40 or nc > 150, "post_steer": False, "reverted": False},
        accept_best_requested=bool(cp_p.get("accept_best_requested", False)),
        horizon_review_requested=bool(cp and cp_p.get("horizon_review_requested", False)),
        post_steer=bool(cp_p.get("post_steer", False)),
        fb_ref=cp.pre_steer_fallback_ref if cp else None,
        fb_draft=req.resume.pre_steer_fallback_draft_ir,
        fb_cert=req.resume.pre_steer_fallback_certified_ir,
        prior_v8_verdict=HorizonCriticVerdict.model_validate_json(v8j) if v8j else None,
    ), cp_p


def poll_control_signals(
    reader: ModelingControlReader | None, *, run_id: str,
    last_signal_seq: list[int], budget: HarnessBudget, hints: list[str],
) -> tuple[bool, bool, bool, bool, HarnessBudget]:
    """Poll ControlSignalReader -> (pause, accept_best, horizon_review, revert, budget)."""
    if reader is None:
        return False, False, False, False, budget
    fl = {"pause": False, "accept-best": False, "horizon-review": False, "revert": False}
    cur = budget
    for sig in reader.poll_signals(run_id, after_seq=last_signal_seq[0]):
        last_signal_seq[0] += 1
        if sig.action in fl:
            fl[sig.action] = True
        elif sig.action == "hint" and sig.hint and sig.hint.strip() not in hints:
            hints.append(sig.hint.strip())
        elif sig.action == "raise-budget":
            cur = cur.model_copy(update={
                "max_turns": cur.max_turns + int(sig.extra_turns or 0),
                "max_tokens": cur.max_tokens + int(sig.extra_tokens or 0),
                "timeout_seconds": cur.timeout_seconds + int(sig.extra_timeout_seconds or 0),
            })
    return fl["pause"], fl["accept-best"], fl["horizon-review"], fl["revert"], cur


def save_checkpoint(
    *, run_id: str, phase: str, status: Literal["HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"],
    ontology_version: str, src_fingerprint: str,
    pending_questions: Sequence[ClarificationQuestion | HorizonQuestion],
    turns_used: int, tokens_used: int, token_count_source: Literal["reported", "estimated"],
    seq_ref: list[int], event_sink: ModelingEventSink | None,
    checkpoint_store: ModelingCheckpointStore | None,
    extra: Mapping[str, _PVal] | None = None, last_sig_seq: int = 0, hints: Sequence[str] = (),
    accept_best_requested: bool = False, horizon_review_requested: bool = False,
    effective_budget: HarnessBudget | None = None, policy: CegarPolicy | None = None,
    v7_score: int | None = None, post_steer: bool = False,
    answered_horizon_keys: Sequence[str] = (), discovery_hold_completed: bool = False,
    pre_steer_fallback_ref: str | None = None, prior_v8_verdict_json: str | None = None,
) -> RunCheckpointRecord:
    """Build resume payload, persist checkpoint, and emit HOLD events when held."""
    ql, is_hold = list(pending_questions), status == "HOLD_CHECKPOINTED"
    rp: _PDict = {k: v for k, v in (extra or {}).items() if v is not None}
    if is_hold:
        if last_sig_seq > 0 or "last_sig_seq" not in rp:
            rp["last_sig_seq"] = last_sig_seq
        if hints:
            rp["hints"] = list(hints)
        for flag, key in (
            (accept_best_requested, "accept_best_requested"),
            (horizon_review_requested, "horizon_review_requested"),
            (post_steer, "post_steer"), (discovery_hold_completed, "discovery_hold_completed"),
        ):
            if flag:
                rp[key] = True
        if answered_horizon_keys:
            rp["answered_horizon_keys"] = list(dict.fromkeys(answered_horizon_keys))
        if (
            policy is not None and effective_budget is not None
            and effective_budget != CEGAR_POLICY_SPECS[policy].budget
        ):
            rp["budget_max_turns"] = effective_budget.max_turns
            rp["budget_max_tokens"] = effective_budget.max_tokens
            rp["budget_timeout_seconds"] = effective_budget.timeout_seconds
        if v7_score is not None:
            rp["v7_score"] = v7_score

    dumped = [q.model_dump(mode="json") for q in ql] if is_hold else []
    if is_hold:
        emit_run_event(
            event_sink, seq_ref, run_id=run_id, event_type="HOLD_REQUESTED",
            payload={"pending_questions": dumped},
        )
    cp_seq = seq_ref[0] + (1 if (is_hold and event_sink is not None) else 0)
    rec = RunCheckpointRecord(
        run_id=run_id, step="step6", phase=phase, status=status, last_seq=cp_seq,
        ontology_rev_id=ontology_version, sources_fingerprint=src_fingerprint,
        pending_questions=ql, budget_turns_used=turns_used, budget_tokens_used=tokens_used,
        token_count_source=token_count_source, resume_payload=rp,
        pre_steer_fallback_ref=pre_steer_fallback_ref, prior_v8_verdict_json=prior_v8_verdict_json,
    )
    if checkpoint_store is not None:
        checkpoint_store.save_checkpoint(rec)
        checkpoint_store.release_active_lease(run_id)
    if is_hold:
        emit_run_event(
            event_sink, seq_ref, run_id=run_id, event_type="HOLD_CHECKPOINTED",
            payload={
                "checkpoint_sha256": sha256_jcs(rec.model_dump(mode="json")),
                "pending_questions": dumped, "last_seq": cp_seq,
            },
        )
    return rec


def can_accept_best(*, policy: CegarPolicy, best: CandidateState | None) -> bool:
    """True only when best exists and satisfies deterministic + policy gates."""
    if best is None or best.report.v1_v6_fail_count > 0 or best.report.deterministic_energy > 0:
        return False
    if policy == "strict":
        floor = CEGAR_POLICY_SPECS[policy].v7_floor or 95
        v7 = best.v7_verdict
        return best.report.v6b_fail_count == 0 and v7 is not None and v7.score >= floor
    return True


def enforce_accept_best_gate(
    *, policy: CegarPolicy, best: CandidateState | None, run_id: str | None = None,
) -> None:
    """Raise VERIFICATION_GATE_FAILED when strict policy rejects accept-best."""
    if policy == "strict" and not can_accept_best(policy=policy, best=best):
        raise OpenDVError(
            "Strict policy forbids accept-best when gates or V7 < 95 fail.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED, run_id=run_id,
        )


def build_result(
    ctx: RunCtx, *, status: ModelingStatus, best: CandidateState | None = None,
    certified_ir: CertifiedVaultIR | None = None, draft_ir: DraftVaultIR | None = None,
    evolution_lock: EvolutionLockSpec | None = None,
    v8_verdict: HorizonCriticVerdict | None = None,
    pending_questions: Sequence[ClarificationQuestion | HorizonQuestion] = (),
    checkpoint: RunCheckpointRecord | None = None, auto_steers: int = 0, v8_calls: int = 0,
) -> DVModelingResult:
    """Assemble DVModelingResult from RunCtx and outcome artifacts."""
    req = ctx.req
    c_ir = certified_ir or (best.certified_ir if best else None)
    d_ir = draft_ir or (best.draft_ir if best else None)
    rep = best.report if best else None
    justs = (
        list(c_ir.justifications) if c_ir
        else (list(d_ir.justifications) if d_ir else list(req.persisted_waivers))
    )
    rem = (
        _build_remediation_hints(rep)
        if (rep is not None and status not in ("CERTIFIED", "CERTIFIED_FAST")) else []
    )
    return DVModelingResult(
        run_id=req.run_id, status=status,
        next_action=resolve_next_action(status=status, report=rep),
        policy=req.policy, wave_number=ctx.wave_number, attempts_used=len(ctx.attempts),
        certified_ir=c_ir, draft_ir=d_ir, evolution_lock=evolution_lock, verifier_report=rep,
        fragility_summary=scan_fragility_surface(c_ir, req.profiles, req.wave.evolution_locks)
        if c_ir else None,
        v7_verdict=best.v7_verdict if best else None, v8_verdict=v8_verdict,
        anti_pattern_justifications=justs, pending_questions=list(pending_questions),
        suggested_questions=list(ctx.suggested_qs), remediation_hints=rem, attempts=ctx.attempts,
        ontology_proposals=list(ctx.proposals), horizon_assumptions=ctx.assumptions,
        checkpoint=checkpoint,
        turns_used=ctx.turns_used, tokens_used=ctx.tokens_used, token_count_source=ctx.tok_source,
        horizon_auto_steers_used=auto_steers, v8_calls_used=v8_calls,
        final_energy_e=best.energy_e if best else None,
        deterministic_energy=rep.deterministic_energy if rep else None,
        preferences_sha256=compute_preferences_sha256(req.modeling_preferences),
        modeling_guide_sha256=ctx.guide_sha, telemetry=ctx.telemetry,
        cycle_halted=ctx.cycle_halted, last_seq=ctx.seq_ref[0],
    )


def checkpoint_result(
    ctx: RunCtx, *, status: Literal["HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"], phase: str,
    questions: Sequence[ClarificationQuestion | HorizonQuestion] = (),
    extra: Mapping[str, _PVal] | None = None,
    best: CandidateState | None = None, draft_ir: DraftVaultIR | None = None,
) -> DVModelingResult:
    """Persist checkpoint and return HOLD_CHECKPOINTED or UNCERTIFIED_DRAFT result."""
    is_hold, req = status == "HOLD_CHECKPOINTED", ctx.req
    pending = questions if is_hold else ()
    ex: Mapping[str, _PVal] | None = (
        extra if is_hold else {"best_ir_sha256": best.ir_sha256 if best else ""}
    )
    v8j = ctx.prior_v8_verdict.model_dump_json() if ctx.prior_v8_verdict else None
    cp = save_checkpoint(
        run_id=req.run_id, phase=phase, status=status,
        ontology_version=req.ontology.ontology_version, src_fingerprint=ctx.src_fp,
        pending_questions=pending, turns_used=ctx.turns_used, tokens_used=ctx.tokens_used,
        token_count_source=ctx.tok_source, seq_ref=ctx.seq_ref,
        event_sink=ctx.sink if is_hold else None, checkpoint_store=ctx.cp_store, extra=ex,
        last_sig_seq=ctx.last_sig_seq[0], hints=ctx.hints,
        accept_best_requested=ctx.accept_best_requested,
        horizon_review_requested=ctx.horizon_review_requested, effective_budget=ctx.budget,
        policy=req.policy, post_steer=ctx.post_steer, answered_horizon_keys=ctx.answered_hkeys,
        discovery_hold_completed=True, pre_steer_fallback_ref=ctx.fb_ref,
        prior_v8_verdict_json=v8j,
    )
    return build_result(
        ctx, status=status, best=best,
        draft_ir=draft_ir or (None if best else (req.resume.resumed_draft_ir if is_hold else None)),
        v8_verdict=ctx.prior_v8_verdict, pending_questions=pending, checkpoint=cp,
    )


def seal_result(
    ctx: RunCtx, best: CandidateState, *, status: Literal["CERTIFIED", "CERTIFIED_FAST"],
    v8_verdict: HorizonCriticVerdict | None = None, auto_steers: int = 0, v8_calls: int = 0,
) -> DVModelingResult:
    """Seal CertifiedVaultIR, build EvolutionLockSpec, emit RUN_COMPLETED."""
    emit_run_event(
        ctx.sink, ctx.seq_ref, run_id=ctx.req.run_id, event_type="STAGE_UPDATE",
        payload={
            "stage_id": "S6-07", "attempt": len(ctx.attempts),
            "energy_e": best.energy_e, "status": status,
        },
    )
    sealed = attach_wave_delta(
        best.certified_ir, prior_ir=ctx.req.wave.prior_certified_ir,
        locks=ctx.req.wave.evolution_locks, force_refresh=(auto_steers > 0),
    )
    emit_run_event(
        ctx.sink, ctx.seq_ref, run_id=ctx.req.run_id, event_type="RUN_COMPLETED",
        payload={"revision_id": f"wave-{ctx.wave_number}", "completion_status": status},
    )
    return build_result(
        ctx, status=status, best=best, certified_ir=sealed,
        evolution_lock=build_evolution_lock_from_certified(sealed),
        v8_verdict=v8_verdict, auto_steers=auto_steers, v8_calls=v8_calls,
    )
