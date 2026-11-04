"""Headless OpenDVService application facade (Step 3.5)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from opendv_contracts import (
    CompileRequest,
    CompileResponse,
    PipelineEvent,
    PipelineRunHandle,
    ProfileRequest,
    ProfileResponse,
    RbacRole,
    SandboxQueryRequest,
    SandboxQueryResponse,
    VerifyRequest,
    VerifyResponse,
)
from opendv_service.errors import NotFoundError
from opendv_service.models import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    CreateProjectRequest,
    CreateRunRequest,
    GateScorecardSummary,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectResponse,
    ProjectRunDTO,
)
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_service.state import StateStore
from opendv_service.use_cases.artifacts import get_artifact, store_artifact
from opendv_service.use_cases.compile import compile_pipeline
from opendv_service.use_cases.holds import list_holds, resolve_hold
from opendv_service.use_cases.pipeline import (
    execute_pipeline_run_sync,
    execute_run_async,
    stream_events,
    validate_pipeline,
)
from opendv_service.use_cases.profile import profile_pipeline
from opendv_service.use_cases.runs import (
    abort_run,
    create_project,
    create_run,
    get_project,
    get_run,
    list_runs,
)
from opendv_service.use_cases.sandbox import execute_sandbox_query
from opendv_service.use_cases.verify import verify_pipeline


class OpenDVService:
    """Headless application core service orchestrating Data Vault 2.0 pipelines."""

    def __init__(
        self,
        state_store: StateStore | None = None,
        *,
        pipeline_runner: PipelineRunner | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.state = state_store or StateStore(repo_root=workspace_root)
        self.pipeline_runner = pipeline_runner or get_default_pipeline_runner()

    # 1. Profiling
    def profile_pipeline(self, request: ProfileRequest | dict[str, Any]) -> ProfileResponse:
        req_obj = (
            request
            if isinstance(request, ProfileRequest)
            else ProfileRequest.model_validate(request)
        )
        return profile_pipeline(req_obj, workspace_root=self.workspace_root)

    # 2. Compilation
    def compile_pipeline(self, request: CompileRequest | dict[str, Any]) -> CompileResponse:
        req_obj = (
            request
            if isinstance(request, CompileRequest)
            else CompileRequest.model_validate(request)
        )
        return compile_pipeline(req_obj, workspace_root=self.workspace_root)

    # 3. Verification (Gates G1-G6)
    def verify_pipeline(self, request: VerifyRequest | dict[str, Any]) -> VerifyResponse:
        req_obj = (
            request if isinstance(request, VerifyRequest) else VerifyRequest.model_validate(request)
        )
        return verify_pipeline(req_obj, workspace_root=self.workspace_root)

    # 4. DuckDB Sandbox
    def execute_sandbox_query(
        self, request: SandboxQueryRequest | dict[str, Any]
    ) -> SandboxQueryResponse:
        mapping = {
            "QUERY_D10_LP_COMMITMENT_PIT": "QUERY_D10_PIT",
            "QUERY_D10B_PORTCO_VALUATION_PIT": "QUERY_D10B_VALUATION",
            "RUN_4_BATCHES": "ADVERSARIAL_BATCHES_1_TO_4",
        }
        if isinstance(request, SandboxQueryRequest):
            req_obj = request
        elif isinstance(request, dict):
            d = dict(request)
            qid = d.pop("query_id", None)
            if qid and "query_type" not in d:
                d["query_type"] = qid
            if d.get("query_type") in mapping:
                d["query_type"] = mapping[d["query_type"]]
            req_obj = SandboxQueryRequest.model_validate(d)
        elif hasattr(request, "model_dump"):
            d = request.model_dump()
            qid = d.pop("query_id", None)
            if qid and "query_type" not in d:
                d["query_type"] = qid
            if d.get("query_type") in mapping:
                d["query_type"] = mapping[d["query_type"]]
            req_obj = SandboxQueryRequest.model_validate(d)
        else:
            req_obj = SandboxQueryRequest.model_validate(request)
        return execute_sandbox_query(req_obj, workspace_root=self.workspace_root)

    # 5. Agentic Pipeline Pre-flight Validation
    def validate_pipeline(
        self, request: PipelineValidateRequest | dict[str, Any]
    ) -> dict[str, Any]:
        req_obj = (
            request
            if isinstance(request, PipelineValidateRequest)
            else PipelineValidateRequest.model_validate(request)
        )
        return validate_pipeline(self.state, req_obj)

    # 6. Pipeline Execution & Streaming (Decision D2: Async)
    async def execute_run_async(
        self, request: PipelineRunRequest | dict[str, Any]
    ) -> PipelineRunHandle:
        req_obj = (
            request
            if isinstance(request, PipelineRunRequest)
            else PipelineRunRequest.model_validate(request)
        )
        return await execute_run_async(self.state, req_obj, pipeline_runner=self.pipeline_runner)

    async def stream_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> AsyncIterator[PipelineEvent]:
        async for event in stream_events(
            self.state, run_id, last_event_id=last_event_id, role=role
        ):
            yield event

    # Synchronous in-process run helper for standalone CLI
    def execute_pipeline_run_sync(
        self,
        request: PipelineRunRequest | dict[str, Any],
        *,
        role: RbacRole = RbacRole.INTERNAL_MODELER,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, Any], None] | None = None,
        on_stream_event: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        req_obj = (
            request
            if isinstance(request, PipelineRunRequest)
            else PipelineRunRequest.model_validate(request)
        )
        return execute_pipeline_run_sync(
            self.state,
            req_obj,
            role=role,
            pipeline_runner=self.pipeline_runner,
            on_stage_start=on_stage_start,
            on_stage_complete=on_stage_complete,
            on_stream_event=on_stream_event,
        )

    # 7. Steward Hold Management
    def list_holds(self, run_id: str) -> list[dict[str, Any]]:
        return list_holds(self.state, run_id)

    def resolve_hold(
        self,
        run_id: str,
        request: CaptainHoldDecisionRequest | dict[str, Any],
    ) -> CaptainHoldDecisionResponse:
        req_obj = (
            request
            if isinstance(request, CaptainHoldDecisionRequest)
            else CaptainHoldDecisionRequest.model_validate(request)
        )
        return resolve_hold(self.state, run_id, req_obj)

    # 8. Scorecard & Reports
    def get_scorecard(self, run_id: str) -> GateScorecardSummary:
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        return self.state.runs[run_id].gate_scorecard

    def get_pipeline_report(
        self,
        run_id: str,
        *,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> dict[str, Any]:
        return self.state.get_pipeline_report(run_id, role=role)

    # 9. Projects, Runs & Artifacts State Management
    def create_project(self, request: CreateProjectRequest | dict[str, Any]) -> ProjectResponse:
        req_obj = (
            request
            if isinstance(request, CreateProjectRequest)
            else CreateProjectRequest.model_validate(request)
        )
        return create_project(self.state, req_obj)

    def get_project(self, project_id: str) -> ProjectResponse:
        return get_project(self.state, project_id)

    def list_runs(self, project_id: str | None = None) -> list[ProjectRunDTO]:
        return list_runs(self.state, project_id=project_id)

    def get_run(self, run_id: str) -> ProjectRunDTO:
        return get_run(self.state, run_id)

    def create_run(self, request: CreateRunRequest | dict[str, Any]) -> ProjectRunDTO:
        req_obj = (
            request
            if isinstance(request, CreateRunRequest)
            else CreateRunRequest.model_validate(request)
        )
        return create_run(self.state, req_obj)

    def abort_run(self, run_id: str) -> ProjectRunDTO:
        return abort_run(self.state, run_id)

    def store_artifact(
        self, request: ArtifactUploadRequest | dict[str, Any]
    ) -> ArtifactMetadataResponse:
        req_obj = (
            request
            if isinstance(request, ArtifactUploadRequest)
            else ArtifactUploadRequest.model_validate(request)
        )
        return store_artifact(self.state, req_obj)

    def get_artifact(self, sha256: str) -> ArtifactMetadataResponse:
        return get_artifact(self.state, sha256)


__all__ = ["OpenDVService"]
