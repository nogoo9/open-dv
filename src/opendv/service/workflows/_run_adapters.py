"""Shared workflow adapters: harness resolution, leases, revisions, and run telemetry."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import BaseModel

from opendv.agents import (
    AgentHarnessPort,
    SdkHarnessRunner,
    SubprocessHarnessRunner,
    SubprocessStreamFn,
    resolve_cli_binary,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import (
    AuditLogRecord,
    ControlSignalRecord,
    ExternalMcpReceipt,
    RunEventLogPage,
    RunStep,
    RunStreamEvent,
)
from opendv.models.policy import CEGAR_POLICY_SPECS, STEP4_DEFAULT_BUDGET
from opendv.models.project import (
    EvolutionLockSpec,
    ProjectSettingsSnapshot,
    WorkspaceSettings,
    validate_project_id,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import RunControlReq, RunStateResp
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec
from opendv.service.secrets import KeyringSecretStore
from opendv.service.state_store import (
    ProjectStateManager,
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
)


def validate_project_id_or_error(project_id: str) -> str:
    """Validate project_id or raise OpenDVError(SYNTAX_INVALID_ARGUMENT)."""
    try:
        return validate_project_id(project_id)
    except ValueError as exc:
        raise OpenDVError(str(exc), error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT) from exc


def active_job_lease_key(project_id: str) -> str:
    """Return canonical active_job_lease key for a project."""
    return f"active_job_lease:{project_id}"


def duckdb_write_lease_key(project_id: str, target_id: str) -> str:
    """Return canonical single-writer DuckDB lease key."""
    return f"duckdb_write:{project_id}:{target_id}"


def acquire_project_job_lease(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
    *,
    ttl_seconds: int = 120,
) -> None:
    """Claim active_job_lease:<project_id>, raising PROJECT_JOB_ACTIVE if already held."""
    state_store.claim_lease(
        project_id,
        lease_key=active_job_lease_key(project_id),
        holder_run_id=run_id,
        ttl_seconds=ttl_seconds,
    )


def release_project_job_lease(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> None:
    """Release active_job_lease:<project_id> held by run_id."""
    state_store.release_lease(
        project_id,
        lease_key=active_job_lease_key(project_id),
        holder_run_id=run_id,
    )


def resolve_harness(
    settings: WorkspaceSettings | ProjectSettingsSnapshot,
    secret_store: KeyringSecretStore,
    *,
    injected_harness: AgentHarnessPort | None = None,
    model_overrides: Mapping[str, str] | None = None,
    stream_fn: SubprocessStreamFn | None = None,
) -> AgentHarnessPort:
    """Resolve AgentHarnessPort from settings/secrets unless an explicit harness is injected."""
    if injected_harness is not None:
        return injected_harness

    base_ovr = getattr(settings, "model_overrides", None)
    merged_ovr: dict[str, str] = dict(base_ovr) if isinstance(base_ovr, Mapping) else {}
    if model_overrides:
        merged_ovr.update(model_overrides)

    provider = settings.harness_provider
    if provider in ("agy", "claude-cli"):
        resolve_cli_binary(provider)
        return SubprocessHarnessRunner(
            provider=provider,
            model_overrides=merged_ovr,
            stream_fn=stream_fn,
        )

    if provider == "claude":
        api_key = secret_store.get_secret(
            "ANTHROPIC_API_KEY",
            project_precedence=settings.secret_precedence,
        )
        if not api_key:
            raise OpenDVError(
                "Provider 'claude' requires ANTHROPIC_API_KEY in secret store or environment.",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            )
        return SdkHarnessRunner(
            provider="claude",
            api_key=api_key,
            model_overrides=merged_ovr,
        )

    if provider == "gemini":
        api_key = secret_store.get_secret(
            "GEMINI_API_KEY",
            project_precedence=settings.secret_precedence,
        ) or secret_store.get_secret(
            "GOOGLE_API_KEY",
            project_precedence=settings.secret_precedence,
        )
        if not api_key:
            raise OpenDVError(
                "Provider 'gemini' requires GEMINI_API_KEY or GOOGLE_API_KEY.",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            )
        return SdkHarnessRunner(
            provider="gemini",
            api_key=api_key,
            model_overrides=merged_ovr,
        )

    raise OpenDVError(
        f"Unsupported harness provider '{provider}'.",
        error_code=ErrorCode.AGENT_NOT_CONFIGURED,
    )


def load_latest_revision_model[ModelT: BaseModel](
    state_store: ProjectStateManager,
    project_id: str,
    rev_kind: str,
    model_cls: type[ModelT],
) -> tuple[str, ModelT] | None:
    """Load the most recent revision payload of rev_kind validated as model_cls."""
    revs = state_store.list_revisions(project_id, rev_kind=rev_kind)
    if not revs:
        return None
    latest = revs[-1]
    return str(latest["revision_id"]), model_cls.model_validate(latest["payload"])


def list_revision_models[ModelT: BaseModel](
    state_store: ProjectStateManager,
    project_id: str,
    rev_kind: str,
    model_cls: type[ModelT],
) -> list[tuple[str, ModelT]]:
    """Load all revisions of rev_kind validated as model_cls in creation order."""
    revs = state_store.list_revisions(project_id, rev_kind=rev_kind)
    return [(str(r["revision_id"]), model_cls.model_validate(r["payload"])) for r in revs]


def load_latest_evolution_lock(
    state_store: ProjectStateManager,
    project_id: str,
) -> EvolutionLockSpec | None:
    """Return latest EvolutionLockSpec for project_id, if any wave has sealed."""
    loaded = load_latest_revision_model(
        state_store, project_id, "evolution_lock", EvolutionLockSpec
    )
    return loaded[1] if loaded is not None else None


def load_active_certified_ir(
    state_store: ProjectStateManager,
    project_id: str,
) -> CertifiedVaultIR:
    """Return active CertifiedVaultIR for project_id or raise RESOURCE_NOT_FOUND."""
    proj = state_store.get_project(project_id)
    if proj.active_wave_id is None:
        raise OpenDVError(
            f"Project '{project_id}' has no active certified Vault IR.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    rev = state_store.get_revision(project_id, proj.active_wave_id)
    return CertifiedVaultIR.model_validate(rev["payload"])


def append_audit_log(
    state_store: ProjectStateManager,
    project_id: str,
    *,
    event_type: str,
    actor: str,
    step: str,
    summary: str,
    run_id: str | None = None,
    details: dict[str, str | int | float | bool | None] | None = None,
) -> int:
    """Append an AuditLogRecord to events_and_receipts."""
    norm_actor = actor if actor in ("human", "agent", "system") else "system"
    rec = AuditLogRecord(
        event_type=event_type,
        actor=norm_actor,  # type: ignore[arg-type]
        step=step,
        summary=summary,
        details=dict(details or {}),
    )
    return state_store.append_event_or_receipt(
        project_id,
        record_kind="audit_log",
        run_id=run_id,
        payload=rec,
    )


def record_run_state(
    state_store: ProjectStateManager,
    project_id: str,
    state: RunStateResp,
) -> int:
    """Persist a RunStateResp snapshot in events_and_receipts."""
    return state_store.append_event_or_receipt(
        project_id,
        record_kind="run_state",
        run_id=state.run_id,
        payload=state,
    )


def mark_run_failed(
    state_store: ProjectStateManager,
    project_id: str,
    state: RunStateResp,
    *,
    error_code: ErrorCode | str,
    detail: str,
) -> None:
    """Persist FAILED RunStateResp and emit RUN_FAILED stream event if not already emitted."""
    if state.status != "FAILED":
        record_run_state(
            state_store,
            project_id,
            state.model_copy(update={"status": "FAILED", "phase": "error"}),
        )
    page = state_store.tail_run_stream_events(project_id, state.run_id, since_seq=0)
    if page.events and page.events[-1].event_type == "RUN_FAILED":
        return
    ServiceRunEventSink(state_store, project_id).emit(
        RunStreamEvent(
            seq=1,
            run_id=state.run_id,
            ts=datetime.now(tz=UTC).isoformat(),
            event_type="RUN_FAILED",
            step=state.step,
            payload={"error_code": str(error_code), "detail": detail},
        )
    )


def mark_run_failed_from_exception(
    state_store: ProjectStateManager,
    project_id: str,
    state: RunStateResp,
    exc: BaseException,
) -> None:
    """Persist FAILED state and emit RUN_FAILED from an OpenDVError or generic Exception."""
    err_code = (
        str(exc.error_code)
        if isinstance(exc, OpenDVError)
        else str(ErrorCode.INTERNAL_SERVER_ERROR)
    )
    detail = exc.detail if isinstance(exc, OpenDVError) else str(exc)
    mark_run_failed(state_store, project_id, state, error_code=err_code, detail=detail)


def _compute_run_state_with_overlays(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
    rows: list[dict[str, object]],
) -> RunStateResp:
    latest_row = rows[-1]
    base = RunStateResp.model_validate(latest_row["payload"])

    start_iso = str(rows[0]["created_at"])
    end_iso = (
        datetime.now(tz=UTC).isoformat()
        if base.status in ("QUEUED", "RUNNING", "HOLD_REQUESTED")
        else str(latest_row["created_at"])
    )
    try:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt = datetime.fromisoformat(end_iso)
        elapsed = max(0, int((end_dt - start_dt).total_seconds()))
    except ValueError:
        elapsed = base.elapsed_seconds

    base_budget = (
        CEGAR_POLICY_SPECS[base.policy].budget
        if base.step == "step6" and base.policy is not None
        else STEP4_DEFAULT_BUDGET
    )
    eff_turns = base.effective_max_turns or base_budget.max_turns
    eff_tokens = base.effective_max_tokens or base_budget.max_tokens
    eff_timeout = base.effective_timeout_seconds or base_budget.timeout_seconds

    sig_rows = state_store.list_events_or_receipts(
        project_id, record_kind="control_signal", run_id=run_id
    )
    for sr in sig_rows:
        sig = ControlSignalRecord.model_validate(sr["payload"])
        if sig.action == "raise-budget":
            eff_turns += int(sig.extra_turns or 0)
            eff_tokens += int(sig.extra_tokens or 0)
            eff_timeout += int(sig.extra_timeout_seconds or 0)

    log_page = state_store.tail_run_stream_events(project_id, run_id, since_seq=0)
    max_seq = max([base.last_seq, *(ev.seq for ev in log_page.events)])

    return base.model_copy(
        update={
            "project_id": base.project_id or project_id,
            "elapsed_seconds": max(base.elapsed_seconds, elapsed),
            "effective_max_turns": eff_turns,
            "effective_max_tokens": eff_tokens,
            "effective_timeout_seconds": eff_timeout,
            "last_seq": max_seq,
        }
    )


def load_latest_run_state(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> RunStateResp:
    """Return latest RunStateResp with elapsed time and raise-budget overlays."""
    rows = state_store.list_events_or_receipts(project_id, record_kind="run_state", run_id=run_id)
    if not rows:
        raise OpenDVError(
            f"Run '{run_id}' not found in project '{project_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return _compute_run_state_with_overlays(state_store, project_id, run_id, rows)


def list_project_runs(
    state_store: ProjectStateManager,
    project_id: str,
    *,
    step: RunStep | None = None,
) -> list[RunStateResp]:
    """Return latest RunStateResp for every run in project_id ordered newest-first."""
    state_store.get_project(project_id)
    rows = state_store.list_events_or_receipts(project_id, record_kind="run_state")
    by_run: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        rid = str(row.get("run_id") or "")
        if rid:
            by_run.setdefault(rid, []).append(row)

    ordered = sorted(
        by_run.items(),
        key=lambda item: (
            str(item[1][-1].get("created_at") or ""),
            int(str(item[1][-1].get("seq_id") or 0)),
            item[0],
        ),
        reverse=True,
    )
    runs: list[RunStateResp] = []
    for rid, r_rows in ordered:
        state = _compute_run_state_with_overlays(state_store, project_id, rid, r_rows)
        if step is not None and state.step != step:
            continue
        runs.append(state)
    return runs


def list_external_mcp_receipts(
    state_store: ProjectStateManager,
    project_id: str,
) -> list[ExternalMcpReceipt]:
    """List persisted ExternalMcpReceipt records for project_id."""
    state_store.get_project(project_id)
    rows = state_store.list_events_or_receipts(project_id, record_kind="mcp_receipt")
    return [ExternalMcpReceipt.model_validate(row["payload"]) for row in rows]


def list_run_attempts(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
) -> list[AttemptSnapshotSpec]:
    """Return persisted AttemptSnapshotSpec history for run_id."""
    rows = state_store.list_events_or_receipts(
        project_id, record_kind="attempt_snapshot", run_id=run_id
    )
    if not rows:
        # Ensure run_id itself exists before returning empty list vs 404
        load_latest_run_state(state_store, project_id, run_id)
        return []
    return [AttemptSnapshotSpec.model_validate(r["payload"]) for r in rows]


def tail_run_logs(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
    *,
    since_seq: int = 0,
    limit: int | None = None,
) -> RunEventLogPage:
    """Tail JSONL RunStreamEvent log for run_id."""
    state_store.get_project(project_id)
    return state_store.tail_run_stream_events(project_id, run_id, since_seq=since_seq, limit=limit)


def enqueue_control_signal(
    state_store: ProjectStateManager,
    project_id: str,
    run_id: str,
    req: RunControlReq,
) -> ControlSignalRecord:
    """Validate run existence and persist a ControlSignalRecord."""
    state = load_latest_run_state(state_store, project_id, run_id)
    if req.action == "accept-best" and state.policy == "strict":
        raise OpenDVError(
            "Strict CEGAR policy forbids accept-best bypass.",
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
            run_id=run_id,
        )
    sig = ControlSignalRecord(
        signal_id=f"sig-{uuid.uuid4().hex[:10]}",
        run_id=run_id,
        action=req.action,
        hint=req.hint,
        extra_turns=req.extra_turns,
        extra_tokens=req.extra_tokens,
        extra_timeout_seconds=req.extra_timeout_seconds,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    state_store.append_event_or_receipt(
        project_id,
        record_kind="control_signal",
        run_id=run_id,
        payload=sig,
    )
    return sig


__all__ = [
    "ServiceControlSignalReader",
    "ServiceRunCheckpointStore",
    "ServiceRunEventSink",
    "acquire_project_job_lease",
    "active_job_lease_key",
    "append_audit_log",
    "duckdb_write_lease_key",
    "enqueue_control_signal",
    "list_external_mcp_receipts",
    "list_project_runs",
    "list_revision_models",
    "list_run_attempts",
    "load_active_certified_ir",
    "load_latest_evolution_lock",
    "load_latest_revision_model",
    "load_latest_run_state",
    "mark_run_failed",
    "mark_run_failed_from_exception",
    "record_run_state",
    "release_project_job_lease",
    "resolve_harness",
    "tail_run_logs",
    "validate_project_id_or_error",
]
