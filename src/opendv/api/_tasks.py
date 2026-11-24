"""Background asyncio.Task lifecycle manager for 202 Accepted Step 4 & Step 6 runs."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStep
from opendv.models.policy import CegarPolicy
from opendv.models.wire_dtos_discovery import RunStateResp
from opendv.service.app_service import OpenDVService
from opendv.service.workflows._run_adapters import mark_run_failed


def _ensure_failed_state_and_release(
    service: OpenDVService,
    project_id: str,
    run_id: str,
    step: RunStep,
    policy: CegarPolicy | None,
    *,
    error_code: str,
    detail: str,
) -> None:
    try:
        state = service.get_run(project_id, run_id)
    except OpenDVError:
        state = RunStateResp(
            run_id=run_id,
            project_id=project_id,
            step=step,
            status="RUNNING",
            policy=policy,
            phase="error",
            attempt=0,
            budget_turns_used=0,
            budget_tokens_used=0,
        )
    mark_run_failed(
        service.state_manager,
        project_id,
        state,
        error_code=error_code,
        detail=detail,
    )
    with contextlib.suppress(OpenDVError):
        service.state_manager.release_lease(
            project_id,
            lease_key=f"active_job_lease:{project_id}",
            holder_run_id=run_id,
        )


class RunTaskManager:
    """Tracks background asyncio.Task runs with synchronous preflight error surfacing."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task[RunStateResp]] = {}

    def is_running(self, project_id: str, run_id: str) -> bool:
        task = self._tasks.get((project_id, run_id))
        return task is not None and not task.done()

    async def launch_run(
        self,
        service: OpenDVService,
        project_id: str,
        run_id: str,
        coro_factory: Callable[[str], Coroutine[Any, Any, RunStateResp]],
        *,
        step: RunStep,
        policy: CegarPolicy | None = None,
        allow_sync_failure_cleanup: bool = True,
    ) -> RunStateResp:
        """Execute synchronous preconditions, return 202 RunStateResp, track background task."""
        service.get_project(project_id)
        was_running = self.is_running(project_id, run_id)
        started_async = False

        async def _execute_supervised() -> RunStateResp:
            try:
                return await coro_factory(run_id)
            except asyncio.CancelledError:
                raise
            except OpenDVError as exc:
                if started_async:
                    _ensure_failed_state_and_release(
                        service,
                        project_id,
                        run_id,
                        step,
                        policy,
                        error_code=str(exc.error_code),
                        detail=exc.detail,
                    )
                raise
            except Exception as exc:
                if started_async:
                    _ensure_failed_state_and_release(
                        service,
                        project_id,
                        run_id,
                        step,
                        policy,
                        error_code=str(ErrorCode.INTERNAL_SERVER_ERROR),
                        detail=str(exc),
                    )
                raise

        task: asyncio.Task[RunStateResp] = asyncio.create_task(_execute_supervised())
        await asyncio.sleep(0)

        if task.done():
            exc = task.exception()
            if exc is not None:
                if allow_sync_failure_cleanup and not was_running:
                    with contextlib.suppress(OpenDVError):
                        cur = service.get_run(project_id, run_id)
                        if cur.step == step and cur.status in ("RUNNING", "HOLD_REQUESTED"):
                            err_code = (
                                str(exc.error_code)
                                if isinstance(exc, OpenDVError)
                                else str(ErrorCode.INTERNAL_SERVER_ERROR)
                            )
                            detail = exc.detail if isinstance(exc, OpenDVError) else str(exc)
                            _ensure_failed_state_and_release(
                                service,
                                project_id,
                                run_id,
                                step,
                                policy,
                                error_code=err_code,
                                detail=detail,
                            )
                raise exc
            return task.result()

        started_async = True
        key = (project_id, run_id)

        def _cleanup(done_task: asyncio.Task[RunStateResp]) -> None:
            if self._tasks.get(key) is done_task:
                self._tasks.pop(key, None)
            with contextlib.suppress(BaseException):
                done_task.exception()

        if not was_running:
            self._tasks[key] = task
            task.add_done_callback(_cleanup)
        try:
            state = service.get_run(project_id, run_id)
        except OpenDVError:
            return RunStateResp(
                run_id=run_id,
                project_id=project_id,
                step=step,
                status="RUNNING",
                policy=policy,
                phase="starting",
                attempt=0,
                budget_turns_used=0,
                budget_tokens_used=0,
            )
        if state.status in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"):
            return state.model_copy(update={"status": "RUNNING", "phase": "resuming"})
        return state

    async def shutdown(self) -> None:
        """Cancel and drain all pending background run tasks cleanly on app shutdown."""
        pending = [t for t in self._tasks.values() if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()


__all__ = ["RunTaskManager"]
