"""Workflow HTTP/in-process client mixin: ontology (Step 4-5), vault (Step 6), storage (Step 7)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter

from opendv.cli._client_grounding import execute_fibo_sync, execute_ground_ontology
from opendv.cli._output import CliHandledError
from opendv.models import (
    AntiPatternJustification,
    CertifiedVaultIR,
    ErrorCode,
    FiboQueryReq,
    FiboQueryResp,
    FiboSyncReq,
    FiboSyncResp,
    GroundingResponse,
    HoldAnswersReq,
    HorizonActionReq,
    MigrationApplyResp,
    NextActionEnum,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyGroundReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
    OpenDVError,
    ProjectState,
    RunControlReq,
    RunEventLogPage,
    RunStateResp,
    SqlQueryReq,
    SqlQueryResult,
    StorageCompileReq,
    StorageTargetReq,
    TargetBundleResp,
    VaultBuildReq,
    VaultStatusResp,
    VaultVerifyResp,
    WaiverSubmitReq,
)

if TYPE_CHECKING:
    from opendv.service import OpenDVService

_TARGET_LIST_ADAPTER: TypeAdapter[list[TargetBundleResp]] = TypeAdapter(list[TargetBundleResp])
_TERMINAL_OR_HOLD = frozenset({"COMPLETED", "FAILED", "HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"})


def proj_route(project_id: str, suffix: str = "") -> str:
    """Build `/api/v1/projects/{project_id}{suffix}` with URL-escaped identifier."""
    return f"/api/v1/projects/{quote(project_id, safe='')}{suffix}"


def esc(val: str) -> str:
    """URL-escape a path segment cleanly."""
    return quote(val, safe="")


class CliWorkflowMixin:
    """Workflow transport operations mixed into `CliClient` cleanly within token limits."""

    @property
    def is_remote(self) -> bool:
        """Implemented by `CliClient`."""
        raise NotImplementedError

    def service(self) -> OpenDVService:
        """Implemented by `CliClient`."""
        raise NotImplementedError

    def _http(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: httpx.QueryParams | dict[str, str | int | float | bool | None] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Implemented by `CliClient`."""
        raise NotImplementedError

    def _call[MT: BaseModel](
        self,
        local_fn: Callable[[OpenDVService], MT],
        model_cls: type[MT],
        method: str,
        path: str,
        **kw: Any,
    ) -> MT:
        if self.is_remote:
            return model_cls.model_validate(self._http(method, path, **kw).json())
        return local_fn(self.service())

    def _call_list[MT: BaseModel](
        self,
        local_fn: Callable[[OpenDVService], list[MT]],
        model_cls: type[MT],
        method: str,
        path: str,
        **kw: Any,
    ) -> list[MT]:
        if self.is_remote:
            resp = self._http(method, path, **kw)
            adapter: TypeAdapter[list[MT]] = TypeAdapter(list[model_cls])  # type: ignore[valid-type]
            return adapter.validate_json(resp.text)
        return local_fn(self.service())

    def _call_async_run(
        self,
        local_coro: Callable[[OpenDVService], Any],
        pid: str,
        method: str,
        path: str,
        *,
        wait: bool,
        poll_fn: Callable[[str, str], RunStateResp],
        json_body: Any,
    ) -> RunStateResp:
        if self.is_remote:
            state = RunStateResp.model_validate(
                self._http(method, path, json_body=json_body).json()
            )
        else:
            state = asyncio.run(local_coro(self.service()))
        return self._finalize_run(pid, state, wait=wait, poll_fn=poll_fn)

    def _finalize_run(
        self,
        pid: str,
        state: RunStateResp,
        *,
        wait: bool,
        poll_fn: Callable[[str, str], RunStateResp],
        timeout_seconds: float = 600.0,
    ) -> RunStateResp:
        deadline = time.monotonic() + timeout_seconds
        current = state
        resuming = state.phase == "resuming"
        while wait:
            if current.status in _TERMINAL_OR_HOLD:
                stale_resume = (
                    resuming
                    and current.status in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT")
                    and current.last_seq <= state.last_seq
                    and current.attempt <= state.attempt
                )
                if not stale_resume:
                    break
            if time.monotonic() >= deadline:
                raise OpenDVError(
                    f"Timed out waiting for run '{current.run_id}' to finish.",
                    error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                    run_id=current.run_id,
                )
            time.sleep(0.15)
            current = poll_fn(pid, current.run_id)
            if current.status not in ("HOLD_CHECKPOINTED", "UNCERTIFIED_DRAFT"):
                resuming = False

        if current.status == "HOLD_CHECKPOINTED":
            raise CliHandledError(
                OpenDVError(
                    f"Run '{current.run_id}' paused at HOLD_CHECKPOINTED.",
                    error_code=ErrorCode.HOLD_CHECKPOINTED,
                    run_id=current.run_id,
                ),
                data=current,
            )
        if current.status == "UNCERTIFIED_DRAFT":
            raise CliHandledError(
                OpenDVError(
                    f"Run '{current.run_id}' ended in UNCERTIFIED_DRAFT.",
                    error_code=ErrorCode.UNCERTIFIED_DRAFT,
                    run_id=current.run_id,
                ),
                data=current,
            )
        if current.status == "FAILED":
            raise CliHandledError(self._extract_failed_error(pid, current), data=current)
        return current

    def _extract_failed_error(self, pid: str, state: RunStateResp) -> OpenDVError:
        try:
            logs = self.get_run_logs(pid, state.run_id)
            for event in reversed(logs.events):
                if event.event_type == "RUN_FAILED":
                    payload = event.payload or {}
                    raw_code = str(payload.get("error_code", ""))
                    detail = str(payload.get("detail") or payload.get("message") or "")
                    matched = next((c for c in ErrorCode if c.value == raw_code), None)
                    if matched is not None:
                        return OpenDVError(
                            detail or f"Run '{state.run_id}' failed ({matched.value}).",
                            error_code=matched,
                            run_id=state.run_id,
                        )
                    if detail:
                        return OpenDVError(
                            detail,
                            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                            run_id=state.run_id,
                        )
        except OpenDVError:
            pass
        return OpenDVError(
            f"Run '{state.run_id}' failed.",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            run_id=state.run_id,
        )

    # ---- Step 4 Ontology ----

    def get_ontology_run(self, project_id: str, run_id: str) -> RunStateResp:
        """Fetch Step 4 run state."""
        return self._call(
            lambda s: s.get_ontology_run(project_id, run_id),
            RunStateResp,
            "GET",
            proj_route(project_id, f"/ontology/runs/{esc(run_id)}"),
        )

    def start_ontology_construct(
        self, project_id: str, req: OntologyConstructReq, *, wait: bool = False
    ) -> RunStateResp:
        """Start Step 4 ontology construction run."""
        return self._call_async_run(
            lambda s: s.start_ontology_construction(project_id, req),
            project_id,
            "POST",
            proj_route(project_id, "/ontology/construct"),
            wait=wait,
            poll_fn=self.get_ontology_run,
            json_body=req.model_dump(mode="json"),
        )

    def control_ontology_run(
        self, project_id: str, run_id: str, req: RunControlReq
    ) -> RunStateResp:
        """Send live control signal to Step 4 run."""
        return self._call(
            lambda s: s.control_ontology_run(project_id, run_id, req),
            RunStateResp,
            "POST",
            proj_route(project_id, f"/ontology/runs/{esc(run_id)}/controls"),
            json_body=req.model_dump(mode="json"),
        )

    def answer_ontology_hold(
        self, project_id: str, run_id: str, req: HoldAnswersReq, *, wait: bool = False
    ) -> RunStateResp:
        """Answer clarification questions for a paused Step 4 run."""
        return self._call_async_run(
            lambda s: s.answer_ontology_hold(project_id, run_id, req),
            project_id,
            "POST",
            proj_route(project_id, f"/ontology/runs/{esc(run_id)}/answers"),
            wait=wait,
            poll_fn=self.get_ontology_run,
            json_body=req.model_dump(mode="json"),
        )

    def fibo_qa(
        self, req: FiboQueryReq, *, project_id: str | None = None
    ) -> FiboQueryResp:
        """Run grounded FIBO reference Q&A."""
        path = (
            proj_route(project_id, "/ontology/fibo-qa")
            if project_id
            else "/api/v1/ontology/fibo-qa"
        )
        return self._call(
            lambda s: s.fibo_qa(req, project_id=project_id),
            FiboQueryResp,
            "POST",
            path,
            json_body=req.model_dump(mode="json"),
        )

    def validate_ontology(
        self, project_id: str, req: OntologyValidateReq
    ) -> OntologyValidationResp:
        """Dry-run ontology validation."""
        return self._call(
            lambda s: s.validate_ontology(project_id, req),
            OntologyValidationResp,
            "POST",
            proj_route(project_id, "/ontology/validate"),
            json_body=req.model_dump(mode="json"),
        )

    def commit_ontology(
        self, project_id: str, req: OntologyCommitReq
    ) -> OntologyRevisionResp:
        """Commit an ontology revision."""
        return self._call(
            lambda s: s.commit_ontology(project_id, req),
            OntologyRevisionResp,
            "POST",
            proj_route(project_id, "/ontology/commit"),
            json_body=req.model_dump(mode="json"),
        )

    def export_ontology(
        self, project_id: str, *, fmt: str = "yaml", include_staged: bool = False
    ) -> dict[str, Any]:
        """Export active Graph A ontology in YAML, JSON, or Turtle."""
        if self.is_remote:
            resp = self._http(
                "GET",
                proj_route(project_id, "/ontology/export"),
                params={"format": fmt, "include_staged": "true" if include_staged else "false"},
            )
            return {"format": fmt, "content": resp.text}
        content, _media = self.service().export_ontology(
            project_id,
            fmt=fmt,  # type: ignore[arg-type]
            include_staged=include_staged,
        )
        return {"format": fmt, "content": content}

    def ground_ontology(self, req: OntologyGroundReq) -> GroundingResponse:
        """Ground table columns against FIBO Pack v2 or YAML glossary."""
        return execute_ground_ontology(self, req)

    def fibo_sync(self, req: FiboSyncReq) -> FiboSyncResp:
        """Install/verify FIBO Pack v2 locally and optionally audit/repin a project."""
        return execute_fibo_sync(self, req)

    # ---- Step 6 Vault ----

    def get_vault_run(self, project_id: str, run_id: str) -> RunStateResp:
        """Fetch Step 6 vault build run state."""
        return self._call(
            lambda s: s.get_vault_run(project_id, run_id),
            RunStateResp,
            "GET",
            proj_route(project_id, f"/runs/{esc(run_id)}"),
        )

    def get_vault_status(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        holds_only: bool = False,
    ) -> VaultStatusResp:
        """Fetch Step 6 vault governance, active wave, open Holds, and waived rules."""
        if not self.is_remote:
            return self.service().get_vault_status(
                project_id, run_id=run_id, holds_only=holds_only
            )

        proj = ProjectState.model_validate(self._http("GET", proj_route(project_id)).json())
        runs = self.list_runs(project_id, step="step6")
        selected: RunStateResp | None = None
        if run_id is not None:
            selected = next((r for r in runs if r.run_id == run_id), None)
            if selected is None:
                raise OpenDVError(
                    f"Step 6 run '{run_id}' not found in project '{project_id}'.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
        elif runs:
            selected = runs[0]

        next_action: NextActionEnum | None = None
        if selected is not None:
            action_map: dict[str, NextActionEnum] = {
                "HOLD_CHECKPOINTED": "ASK_HUMAN_HOLD",
                "COMPLETED": "READY_FOR_DOWNSTREAM",
                "FAILED": "FAILED",
                "UNCERTIFIED_DRAFT": "REPAIR_DRAFT_IR",
            }
            next_action = action_map.get(selected.status)

        waived_rules: list[AntiPatternJustification] = []
        if not holds_only and proj.active_wave_id is not None:
            ir = CertifiedVaultIR.model_validate(
                self._http("GET", proj_route(project_id, "/vault/ir")).json()
            )
            waived_rules = list(ir.justifications)

        return VaultStatusResp(
            project_id=proj.project_id,
            active_wave_id=proj.active_wave_id,
            run_id=selected.run_id if selected is not None else None,
            status=selected.status if selected is not None else None,
            next_action=next_action,
            open_holds=list(selected.pending_questions) if selected is not None else [],
            waived_rules=waived_rules,
        )

    def list_runs(self, project_id: str, *, step: str | None = None) -> list[RunStateResp]:
        """List runs recorded for a project."""
        params = {"step": step} if step else None
        return self._call_list(
            lambda s: s.list_runs(project_id, step=step),  # type: ignore[arg-type]
            RunStateResp,
            "GET",
            proj_route(project_id, "/runs"),
            params=params,
        )

    def build_vault(
        self, project_id: str, req: VaultBuildReq, *, wait: bool = False
    ) -> RunStateResp:
        """Launch Step 6 Data Vault 2.0 synthesis run."""
        return self._call_async_run(
            lambda s: s.build_vault(project_id, req),
            project_id,
            "POST",
            proj_route(project_id, "/runs"),
            wait=wait,
            poll_fn=self.get_vault_run,
            json_body=req.model_dump(mode="json"),
        )

    def verify_vault(self, project_id: str) -> VaultVerifyResp:
        """Run deterministic DV2 verifier gate; fail closed with exit 2 on failure."""
        result = self._call(
            lambda s: s.verify_vault(project_id),
            VaultVerifyResp,
            "POST",
            proj_route(project_id, "/vault/verify"),
        )
        if not result.passed:
            raise CliHandledError(
                OpenDVError(
                    "Vault deterministic verification gate failed.",
                    error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                    diagnostics=result.diagnostics,
                ),
                data=result,
            )
        return result

    def submit_vault_waiver(
        self, project_id: str, req: WaiverSubmitReq
    ) -> AntiPatternJustification:
        """Submit an anti-pattern waiver rationale."""
        return self._call(
            lambda s: s.submit_vault_waiver(project_id, req),
            AntiPatternJustification,
            "POST",
            proj_route(project_id, "/vault/waivers"),
            json_body=req.model_dump(mode="json"),
        )

    def submit_vault_decisions(
        self, project_id: str, run_id: str, req: HoldAnswersReq, *, wait: bool = False
    ) -> RunStateResp:
        """Answer a Step 6 HOLD_CHECKPOINTED decision point."""
        return self._call_async_run(
            lambda s: s.submit_vault_decisions(project_id, run_id, req),
            project_id,
            "POST",
            proj_route(project_id, f"/runs/{esc(run_id)}/decisions"),
            wait=wait,
            poll_fn=self.get_vault_run,
            json_body=req.model_dump(mode="json"),
        )

    def send_vault_control(
        self, project_id: str, run_id: str, req: RunControlReq
    ) -> RunStateResp:
        """Send live control action to a Step 6 vault build run."""
        if self.is_remote:
            return RunStateResp.model_validate(
                self._http(
                    "POST",
                    proj_route(project_id, f"/runs/{esc(run_id)}/controls"),
                    json_body=req.model_dump(mode="json"),
                ).json()
            )
        return asyncio.run(self.service().send_vault_control(project_id, run_id, req))

    def submit_horizon_steering(
        self, project_id: str, run_id: str, req: HorizonActionReq, *, wait: bool = False
    ) -> RunStateResp:
        """Accept or steer horizon assumptions on a Step 6 run."""
        return self._call_async_run(
            lambda s: s.submit_horizon_steering(project_id, run_id, req),
            project_id,
            "POST",
            proj_route(project_id, f"/runs/{esc(run_id)}/horizon"),
            wait=wait,
            poll_fn=self.get_vault_run,
            json_body=req.model_dump(mode="json"),
        )

    def get_run_logs(
        self,
        project_id: str,
        run_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> RunEventLogPage:
        """Fetch paginated JSONL stream events for a run."""
        params: dict[str, int] = {"since_seq": since_seq}
        if limit is not None:
            params["limit"] = limit
        return self._call(
            lambda s: s.get_run_logs(project_id, run_id, since_seq=since_seq, limit=limit),
            RunEventLogPage,
            "GET",
            proj_route(project_id, f"/runs/{esc(run_id)}/logs"),
            params=params,
        )

    # ---- Step 7 Storage ----

    def compile_storage(
        self, project_id: str, req: StorageCompileReq
    ) -> TargetBundleResp | list[TargetBundleResp]:
        """Compile storage target artifacts."""
        if self.is_remote:
            payload = self._http(
                "POST",
                proj_route(project_id, "/storage/compile"),
                json_body=req.model_dump(mode="json"),
            ).json()
            if isinstance(payload, list):
                return _TARGET_LIST_ADAPTER.validate_python(payload)
            return TargetBundleResp.model_validate(payload)
        return self.service().compile_target(project_id, req)

    def migrate_storage(
        self, project_id: str, req: StorageTargetReq
    ) -> MigrationApplyResp:
        """Apply storage migration for DuckDB or Parquet."""
        return self._call(
            lambda s: s.migrate_target(project_id, req),
            MigrationApplyResp,
            "POST",
            proj_route(project_id, "/storage/migrate"),
            json_body=req.model_dump(mode="json"),
        )

    def regenerate_storage(
        self, project_id: str, req: StorageTargetReq
    ) -> MigrationApplyResp:
        """Re-verify and regenerate local storage artifacts."""
        return self._call(
            lambda s: s.regenerate_target(project_id, req),
            MigrationApplyResp,
            "POST",
            proj_route(project_id, "/storage/regenerate"),
            json_body=req.model_dump(mode="json"),
        )

    def query_storage(self, project_id: str, req: SqlQueryReq) -> SqlQueryResult:
        """Execute a read-only SQL query against local storage."""
        return self._call(
            lambda s: s.query_target(project_id, req),
            SqlQueryResult,
            "POST",
            proj_route(project_id, "/storage/query"),
            json_body=req.model_dump(mode="json"),
        )
