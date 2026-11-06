"""Background execution engine for asynchronous pipeline and verification runs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opendv_contracts import (
    PipelineRunHandle,
    PipelineRunRequest,
    RbacRole,
    RunStatus,
)
from opendv_service.errors import NotFoundError, PipelineAbortedError
from opendv_service.models import (
    CreateRunRequest,
    GateScorecardSummary,
    ProjectRunDTO,
)
from opendv_service.runs.callbacks import build_pipeline_callbacks
from opendv_service.runs.options import resolve_run_creation_options
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner

if TYPE_CHECKING:
    from opendv_service.state import StateStore

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    """Runtime execution state and subscription bus for an active or completed run."""

    run_id: str
    abort_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    event_buffer: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[tuple[asyncio.Queue[dict[str, Any] | str], asyncio.AbstractEventLoop]] = field(
        default_factory=set
    )
    is_done: bool = False
    final_status: RunStatus | None = None
    created_at_utc: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class RunExecutionEngine:
    """Manages asynchronous background execution, thread pools, and cancellation."""

    def __init__(self) -> None:
        self._contexts: dict[str, RunContext] = {}
        self._engine_lock = threading.Lock()

    def has_run(self, run_id: str) -> bool:
        """Check if an execution context exists for run_id."""
        with self._engine_lock:
            return run_id in self._contexts

    def get_context(self, run_id: str) -> RunContext | None:
        """Retrieve execution context for run_id if known."""
        with self._engine_lock:
            return self._contexts.get(run_id)

    def broadcast_event(self, context: RunContext, event_dict: dict[str, Any]) -> None:
        """Append an event to the run buffer and dispatch to all active live subscribers."""
        with context.lock:
            context.event_buffer.append(event_dict)
            subscribers = list(context.subscribers)

        for queue, loop in subscribers:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, event_dict)

    def submit_pipeline_run(
        self,
        state: StateStore,
        request: PipelineRunRequest,
        *,
        role: RbacRole = RbacRole.PUBLIC,
        pipeline_runner: PipelineRunner | None = None,
    ) -> PipelineRunHandle:
        """Launch an agentic pipeline run in the background returning an HTTP 202 handle."""
        run_id = getattr(request, "run_id", None) or f"run-{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        provider = getattr(request, "provider_override", None) or "antigravity"

        req_with_id = (
            request.model_copy(update={"run_id": run_id})
            if hasattr(request, "model_copy")
            else request
        )

        run_opts = resolve_run_creation_options(request, workspace_root=state.repo_root)
        has_initial_contract = run_opts.has_initial_contract
        initial_contract_path = run_opts.initial_contract_path
        lifecycle_stage = run_opts.lifecycle_stage
        target_wave_id = run_opts.target_wave_id

        run_dto = ProjectRunDTO(
            run_id=run_id,
            project_id="default-project",
            harness_provider=provider,
            status=RunStatus.RUNNING,
            started_at_utc=now_utc,
            current_stage_id="STAGE_00_ORCHESTRATION",
            current_view_id="WB-00",
            event_count=0,
            gate_scorecard=GateScorecardSummary(),
            active_hold_ids=[],
            events_sse_url=f"/api/v1/runs/{run_id}/events",
            gates_url=f"/api/v1/runs/{run_id}/gates",
            sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
            decisions_url=f"/api/v1/runs/{run_id}/decisions",
            has_initial_contract=has_initial_contract,
            initial_contract_path=initial_contract_path,
            wave_id=target_wave_id,
            lifecycle_stage=lifecycle_stage,
        )
        state.runs[run_id] = run_dto
        state.run_events[run_id] = []

        context = RunContext(run_id=run_id, created_at_utc=now_utc)
        with self._engine_lock:
            self._contexts[run_id] = context

        runner = pipeline_runner or get_default_pipeline_runner()

        def _worker() -> None:
            callbacks = build_pipeline_callbacks(context, state, self)
            final_status: RunStatus = RunStatus.COMPLETED
            try:
                if context.abort_event.is_set():
                    raise PipelineAbortedError(f"Run '{run_id}' aborted prior to startup.")

                state.execute_agentic_pipeline_run(
                    req_with_id,
                    role=role,
                    pipeline_runner=runner,
                    on_stage_start=callbacks.on_stage_start,
                    on_stage_complete=callbacks.on_stage_complete,
                    on_stream_event=callbacks.on_stream_event,
                )
                if run_id in state.runs:
                    final_status = state.runs[run_id].status
            except PipelineAbortedError:
                final_status = RunStatus.ABORTED
                now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if run_id in state.runs:
                    state.runs[run_id] = state.runs[run_id].model_copy(
                        update={"status": RunStatus.ABORTED, "completed_at_utc": now_iso}
                    )
            except Exception as exc:
                logger.error(
                    "Background execution for run '%s' failed: %s", run_id, exc, exc_info=True
                )
                final_status = RunStatus.FAILED
                now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if run_id in state.runs:
                    state.runs[run_id] = state.runs[run_id].model_copy(
                        update={"status": RunStatus.FAILED, "completed_at_utc": now_iso}
                    )
            finally:
                with context.lock:
                    context.is_done = True
                    context.final_status = final_status
                    term_stat = (
                        final_status.value if hasattr(final_status, "value") else str(final_status)
                    )
                    is_hold = term_stat == "PAUSED_HOLD" or final_status == RunStatus.PAUSED_HOLD
                    terminal_event = {
                        "event_id": f"evt-{run_id}-hold" if is_hold else f"evt-{run_id}-complete",
                        "run_id": run_id,
                        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "stage_id": (
                            "STAGE_02_SCOUT_UNSTRUCTURED" if is_hold else "STAGE_07_CERTIFICATION"
                        ),
                        "event_type": "hold" if is_hold else "run_complete",
                        "payload": {"status": term_stat},
                    }
                    context.event_buffer.append(terminal_event)
                    subs = list(context.subscribers)

                for queue, loop in subs:
                    with contextlib.suppress(Exception):
                        loop.call_soon_threadsafe(queue.put_nowait, terminal_event)

        thread = threading.Thread(target=_worker, name=f"run-worker-{run_id}", daemon=True)
        thread.start()

        return PipelineRunHandle(
            run_id=run_id,
            status=RunStatus.RUNNING,
            created_at_utc=now_utc,
            event_stream_url=f"/api/v1/runs/{run_id}/events",
            status_url=f"/api/v1/runs/{run_id}",
            project_id=run_dto.project_id,
            harness_provider=provider,
            has_initial_contract=has_initial_contract,
            initial_contract_path=initial_contract_path,
            wave_id=target_wave_id,
            lifecycle_stage=lifecycle_stage,
        )

    def submit_run(
        self,
        state: StateStore,
        req: CreateRunRequest,
        *,
        pipeline_runner: PipelineRunner | None = None,
    ) -> PipelineRunHandle:
        """Launch a generic platform run or pipeline asynchronously."""
        run_opts = resolve_run_creation_options(req, workspace_root=state.repo_root)
        provider_override = run_opts.provider_override
        has_initial_contract = run_opts.has_initial_contract
        initial_contract_path = run_opts.initial_contract_path
        lifecycle_stage = run_opts.lifecycle_stage
        target_wave_id = run_opts.target_wave_id

        if req.pipeline_input_path is not None:
            return self.submit_pipeline_run(
                state,
                PipelineRunRequest(
                    input_spec_path=req.pipeline_input_path,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                    initial_contract_path=req.initial_contract_path,
                    initial_contract=req.initial_contract,
                    wave_id=target_wave_id,
                ),
                pipeline_runner=pipeline_runner,
            )

        target_project_id = req.project_id or "default-project"
        if target_project_id not in state.projects:
            if target_project_id == "default-project":
                from opendv_contracts import CreateProjectRequest

                state.create_project(
                    CreateProjectRequest(project_id="default-project", name="Default Project")
                )
            else:
                raise NotFoundError(f"Unknown project_id '{req.project_id}'")

        if req.contract_path is not None:
            state._resolve_repo_path(req.contract_path)
            state.projects[target_project_id] = state.projects[target_project_id].model_copy(
                update={"contract_path": req.contract_path}
            )

        dynamic_input_spec = state.build_dynamic_project_input_spec(target_project_id)
        if dynamic_input_spec is not None:
            return self.submit_pipeline_run(
                state,
                PipelineRunRequest(
                    input_spec=dynamic_input_spec,
                    harness_config_path=req.harness_config_path,
                    provider_override=provider_override,
                    steward_hold_policy="auto_approve_top_candidate",
                    initial_contract_path=req.initial_contract_path,
                    initial_contract=req.initial_contract,
                    wave_id=target_wave_id,
                ),
                pipeline_runner=pipeline_runner,
            )

        # Baseline project-scoped verification / sandbox execution
        run_index = len(state.runs) + 1
        run_id = f"run-{target_project_id}-{run_index:04d}"
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_dto = state._init_run_artifacts(
            run_id=run_id,
            project_id=target_project_id,
            harness_provider=req.harness_provider,
            started_at_utc=now_iso,
            has_initial_contract=has_initial_contract,
            initial_contract_path=initial_contract_path,
            wave_id=target_wave_id,
            lifecycle_stage=lifecycle_stage,
        )
        context = RunContext(run_id=run_id, created_at_utc=now_iso)
        with self._engine_lock:
            self._contexts[run_id] = context

        # Mark completed for zero-pipeline static run
        with context.lock:
            context.is_done = True
            stat_str = getattr(run_dto.status, "value", str(run_dto.status))
            context.final_status = (
                RunStatus(stat_str) if stat_str in RunStatus._value2member_map_ else stat_str
            )
            term_evt = {
                "event_id": f"evt-{run_id}-complete",
                "run_id": run_id,
                "timestamp_utc": now_iso,
                "stage_id": "STAGE_07_CERTIFICATION",
                "event_type": "run_complete",
                "payload": {"status": stat_str},
            }
            context.event_buffer.append(term_evt)

        return PipelineRunHandle(
            run_id=run_id,
            status=RunStatus.RUNNING,
            created_at_utc=now_iso,
            event_stream_url=f"/api/v1/runs/{run_id}/events",
            status_url=f"/api/v1/runs/{run_id}",
            project_id=target_project_id,
            harness_provider=req.harness_provider,
            has_initial_contract=has_initial_contract,
            initial_contract_path=initial_contract_path,
            wave_id=target_wave_id,
            lifecycle_stage=lifecycle_stage,
        )

    def abort_run(self, state: StateStore, run_id: str) -> ProjectRunDTO:
        """Cooperatively abort an active run and notify all subscribers."""
        if run_id not in state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")

        context = self.get_context(run_id)
        if context is not None:
            context.abort_event.set()
            with context.lock:
                context.is_done = True
                context.final_status = RunStatus.ABORTED
                terminal_evt = {
                    "event_id": f"evt-{run_id}-aborted",
                    "run_id": run_id,
                    "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "stage_id": "STAGE_07_CERTIFICATION",
                    "event_type": "run_complete",
                    "payload": {"status": "ABORTED"},
                }
                context.event_buffer.append(terminal_evt)
                subs = list(context.subscribers)

            for queue, loop in subs:
                with contextlib.suppress(Exception):
                    loop.call_soon_threadsafe(queue.put_nowait, terminal_evt)

        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_dto = state.runs[run_id].model_copy(
            update={"status": RunStatus.ABORTED, "completed_at_utc": now_iso}
        )
        state.runs[run_id] = run_dto
        return run_dto


__all__ = ["RunContext", "RunExecutionEngine"]
