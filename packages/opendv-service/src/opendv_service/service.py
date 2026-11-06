"""Headless OpenDVService application facade (Step 3.5)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opendv_contracts import (
    CompileRequest,
    CompileResponse,
    ErrorCode,
    ExplorerBundleDTO,
    PipelineEvent,
    PipelineRunHandle,
    ProfileRequest,
    ProfileResponse,
    RunArtifactDTO,
    SandboxQueryRequest,
    SandboxQueryResponse,
    VerifyRequest,
    VerifyResponse,
)
from opendv_core import resolve_workspace_path
from opendv_service.errors import ConflictError, NotFoundError, OpenDVError
from opendv_service.legacy_demo import WORKBENCH_VIEW_TITLES
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
    RunStatus,
    SandboxExecutionResponse,
)
from opendv_service.rbac import RbacRole, normalize_rbac_role, redact_event_for_role
from opendv_service.runs import RunExecutionEngine, subscribe_run_sse
from opendv_service.seams import PipelineRunner, get_default_pipeline_runner
from opendv_service.settings import OpenDVSettings
from opendv_service.state import StateStore
from opendv_service.use_cases.artifacts import get_artifact, store_artifact
from opendv_service.use_cases.compile import compile_pipeline
from opendv_service.use_cases.explorer import get_explorer_bundle, get_run_artifact
from opendv_service.use_cases.holds import list_holds, resolve_hold
from opendv_service.use_cases.pipeline import (
    execute_pipeline_run_sync,
    stream_events,
    validate_pipeline,
)
from opendv_service.use_cases.profile import profile_pipeline
from opendv_service.use_cases.runs import (
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
        state: StateStore | None = None,
        *,
        state_store: StateStore | None = None,
        pipeline_runner: PipelineRunner | None = None,
        workspace_root: Path | None = None,
        storage_root: Path | None = None,
        settings: OpenDVSettings | None = None,
        is_server: bool = False,
    ) -> None:
        self.is_server = is_server
        active_state = state if state is not None else state_store
        self._explicit_workspace = (workspace_root is not None) or (
            bool(getattr(active_state, "_explicit_workspace", False)) if active_state else False
        )
        self._explicit_storage = (storage_root is not None) or (
            bool(getattr(active_state, "_explicit_storage", False)) if active_state else False
        )
        eff_ws = workspace_root or (
            getattr(active_state, "workspace_root", None) if active_state else None
        )
        eff_storage = storage_root or (
            getattr(active_state, "storage_root", None) if active_state else None
        )
        self.settings = settings or OpenDVSettings.load(
            workspace_root=eff_ws,
            state_dir=eff_storage,
            is_server=is_server,
        )
        self.workspace_root = eff_ws or self.settings.workspace_root or Path.cwd().resolve()
        self.storage_root = eff_storage or self.settings.state_dir
        self.state = active_state or StateStore(
            workspace_root=self.workspace_root,
            storage_root=self.storage_root,
        )
        self.pipeline_runner = pipeline_runner or get_default_pipeline_runner()
        self.run_engine = RunExecutionEngine()

    @property
    def repo_root(self) -> Path:
        """Root path for workspace and file resolution."""
        if self.workspace_root is not None:
            return self.workspace_root
        if hasattr(self.state, "workspace_root"):
            return self.state.workspace_root
        return Path.cwd().resolve()

    def list_capabilities(self) -> list[str]:
        """Return authoritative list of headless application core capabilities (T13)."""
        return [
            "profile",
            "compile",
            "verify",
            "sandbox",
            "pipeline",
            "run",
            "hold",
        ]

    # 1. Profiling
    def profile_pipeline(
        self,
        request: ProfileRequest | dict[str, Any],
        *,
        allow_temp: bool | None = None,
        allowed_roots: list[Path] | None = None,
    ) -> ProfileResponse:
        req_obj = (
            request
            if isinstance(request, ProfileRequest)
            else ProfileRequest.model_validate(request)
        )
        eff_allow_temp = (not self.is_server) if allow_temp is None else allow_temp
        return profile_pipeline(
            req_obj,
            workspace_root=self.workspace_root,
            allow_temp=eff_allow_temp,
            allowed_roots=allowed_roots,
        )

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
        req_obj = (
            request
            if isinstance(request, SandboxQueryRequest)
            else SandboxQueryRequest.model_validate(request)
        )
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
        return self.run_engine.submit_pipeline_run(
            self.state, req_obj, role=RbacRole.PUBLIC, pipeline_runner=self.pipeline_runner
        )

    async def create_run_async(
        self, request: CreateRunRequest | dict[str, Any]
    ) -> PipelineRunHandle:
        req_obj = (
            request
            if isinstance(request, CreateRunRequest)
            else CreateRunRequest.model_validate(request)
        )
        return self.run_engine.submit_run(self.state, req_obj, pipeline_runner=self.pipeline_runner)

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

    def list_runs(
        self,
        project_id: str | None = None,
        status: RunStatus | str | None = None,
        stage_id: str | None = None,
    ) -> list[ProjectRunDTO]:
        return list_runs(self.state, project_id=project_id, status=status, stage_id=stage_id)

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
        return self.run_engine.abort_run(self.state, run_id)

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

    def list_projects(self) -> list[ProjectResponse]:
        """List all registered Data Vault 2.0 projects."""
        return list(self.state.projects.values())

    # 10. Gate Scorecard & Reports
    def get_gate_report(self, run_id: str) -> dict[str, Any]:
        """Return Gate G0-G5 scorecard and verification report for a run."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        report = self.state.run_gate_reports.get(run_id)
        if report is None:
            report = self.state._build_default_gate_report(run_id)
            self.state.run_gate_reports[run_id] = report
        return report

    def evaluate_run_gates(
        self,
        run_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger or re-evaluate Gate G0-G5 verification for a run."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        opts = options or {}
        run_dto = self.state.runs[run_id]
        project = self.state.projects.get(run_dto.project_id)
        contract_rel: str | None = opts.get("contract_path") or (
            project.contract_path if project is not None else None
        )
        if not contract_rel:
            raise OpenDVError(
                "contract_path is required in request body or project configuration "
                "to evaluate gates.",
                error_code=ErrorCode.INVALID_REQUEST,
            )
        graph_a_rel: str | None = opts.get("graph_a_ontology_path") or (
            project.graph_a_ontology_path if project is not None else None
        )
        graph_b_rel: str | None = opts.get("graph_b_path") or (
            project.graph_b_path if project is not None else None
        )

        contract_path = resolve_workspace_path(contract_rel, workspace_root=self.repo_root)
        if not contract_path.exists():
            raise NotFoundError(f"Contract file not found: {contract_rel}")
        if graph_a_rel:
            graph_a_path = resolve_workspace_path(graph_a_rel, workspace_root=self.repo_root)
            if not graph_a_path.exists():
                raise NotFoundError(f"Graph A file not found: {graph_a_rel}")
        if graph_b_rel:
            graph_b_path = resolve_workspace_path(graph_b_rel, workspace_root=self.repo_root)
            if not graph_b_path.exists():
                raise NotFoundError(f"Graph B file not found: {graph_b_rel}")

        verify_req = VerifyRequest(
            contract_path=contract_rel,
            graph_a_path=graph_a_rel,
            graph_b_path=graph_b_rel,
            run_id=run_id,
        )
        verify_resp = self.verify_pipeline(verify_req)

        scorecard = verify_resp.gate_scorecard
        overall_passed = verify_resp.overall_passed
        verdict = (
            verify_resp.verdict.value
            if hasattr(verify_resp.verdict, "value")
            else str(verify_resp.verdict)
        )
        updated_updates: dict[str, Any] = {"gate_scorecard": scorecard}
        if not overall_passed:
            updated_updates["status"] = RunStatus.FAILED
        elif run_dto.status == RunStatus.FAILED:
            updated_updates["status"] = (
                RunStatus.PAUSED_HOLD if run_dto.active_hold_ids else RunStatus.COMPLETED
            )
        self.state.runs[run_id] = run_dto.model_copy(update=updated_updates)
        self.state._sync_pipeline_report(run_id)

        base_report = self.state._build_default_gate_report(run_id)
        base_report["evaluated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base_report["overall_passed"] = overall_passed
        base_report["verdict"] = verdict
        base_report["axi_pipeline_status"] = verify_resp.status
        base_report["diagnostics"] = list(verify_resp.diagnostics)
        base_report["toon_text"] = verify_resp.toon_text
        base_report["applied_codemods"] = list(verify_resp.applied_codemods)
        base_report["requested_options"] = opts
        self.state.run_gate_reports[run_id] = base_report
        return base_report

    # 11. DuckDB Sandbox State & Execution
    def get_sandbox_state(self, run_id: str) -> dict[str, Any]:
        """Return 4-Zone DuckDB sandbox state and execution history for a run."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        history = self.state.run_sandbox_history.get(run_id, [])
        return {
            "run_id": run_id,
            "engine": "duckdb",
            "mode": ":memory:",
            "zones": [
                "staging",
                "raw_vault",
                "business_vault",
                "info_mart",
            ],
            "supported_queries": [
                "ADVERSARIAL_BATCHES_1_TO_4",
                "QUERY_D10_PIT",
                "QUERY_D10B_VALUATION",
                "VERIFY_DYN_001_015",
            ],
            "execution_count": len(history),
            "history": [item.model_dump(mode="json") for item in history],
        }

    def execute_run_sandbox_query(
        self,
        run_id: str,
        req: SandboxQueryRequest,
    ) -> SandboxExecutionResponse:
        """Execute a parameterized audit query or 4-Batch sequence in the DuckDB sandbox."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")

        run_dto = self.state.runs[run_id]
        updates: dict[str, Any] = {"run_id": run_id}
        if not (
            req.contract_path
            or req.contract_content
            or req.canonical_ir_path
            or req.canonical_ir_content
        ):
            proj = self.state.projects.get(run_dto.project_id)
            if proj and proj.contract_path:
                updates["contract_path"] = proj.contract_path
            elif proj and proj.canonical_ir_path:
                updates["canonical_ir_path"] = proj.canonical_ir_path

        req_copy = req.model_copy(update=updates)
        resp_obj = self.execute_sandbox_query(req_copy)
        resp = SandboxExecutionResponse(
            run_id=run_id,
            query_type=req.query_type,
            assertion_cutoff_ts=req.assertion_cutoff_ts,
            all_batches_passed=resp_obj.all_batches_passed,
            batch_verdicts=resp_obj.batch_verdicts
            or (
                {
                    f"batch_{i}": ("PASS" if resp_obj.all_batches_passed else "FAIL")
                    for i in range(1, 5)
                }
                if resp_obj.batches
                else {}
            ),
            dynamic_rules_passed=resp_obj.dynamic_rules_passed,
            dynamic_rules_total=resp_obj.dynamic_rules_total,
            replay_delta_rows=resp_obj.replay_delta_rows,
            row_count=resp_obj.row_count,
            rows=resp_obj.rows,
            query_d10_rows=resp_obj.query_d10_rows,
            query_d10b_rows=resp_obj.query_d10b_rows,
        )
        self.state.run_sandbox_history.setdefault(run_id, []).append(resp)
        return resp

    # 12. Steward Decision Management
    def list_run_decisions(self, run_id: str) -> dict[str, Any]:
        """List active Captain-Holds and resolved Steward decisions for a run."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        hold_mgr = self.state.hold_managers.get(run_id)
        active_holds = []
        if hold_mgr is not None:
            active_holds = [
                {
                    "hold_id": rec.hold_id,
                    "decision_id": rec.decision_id,
                    "run_id": rec.run_id,
                    "source_event_id": rec.event_id,
                    "stage_id": (
                        rec.stage_id.value if hasattr(rec.stage_id, "value") else str(rec.stage_id)
                    ),
                    "workbench_view_id": (
                        rec.workbench_view_id.value
                        if hasattr(rec.workbench_view_id, "value")
                        else str(rec.workbench_view_id)
                    ),
                    "status": rec.status,
                    "headline": rec.headline,
                }
                for rec in hold_mgr.list_pending_holds(run_id=run_id)
            ]
        decisions = [
            d.model_dump(mode="json") if hasattr(d, "model_dump") else d
            for d in self.state.run_decisions.get(run_id, [])
        ]
        return {
            "run_id": run_id,
            "active_holds": active_holds,
            "decisions": decisions,
        }

    def resolve_captain_hold(
        self,
        run_id: str,
        request: CaptainHoldDecisionRequest | dict[str, Any],
    ) -> CaptainHoldDecisionResponse:
        """Resolve an active Tier U3 / G3 / G4 Captain-Hold with a Steward decision."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")
        req_obj = (
            request
            if isinstance(request, CaptainHoldDecisionRequest)
            else CaptainHoldDecisionRequest.model_validate(request)
        )
        try:
            return self.state.resolve_captain_hold(run_id=run_id, req=req_obj)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    # 13. Pipeline Run Execution & Event Streaming
    def execute_pipeline_run(
        self,
        request: PipelineRunRequest | dict[str, Any],
        *,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> dict[str, Any]:
        """Execute the end-to-end 8-stage agentic pipeline and return the report."""
        req_obj = (
            request
            if isinstance(request, PipelineRunRequest)
            else PipelineRunRequest.model_validate(request)
        )
        return self.state.execute_agentic_pipeline_run(req_obj, role=role)

    async def stream_run_events(
        self,
        run_id: str,
        *,
        role: RbacRole = RbacRole.PUBLIC,
        last_event_id: str | None = None,
        ping_interval: float = 15.0,
    ) -> AsyncIterator[str]:
        """Stream formatted Server-Sent Events (SSE) text frames for a run."""
        if run_id not in self.state.runs and not self.run_engine.has_run(run_id):
            raise NotFoundError(f"Run '{run_id}' not found.")
        context = self.run_engine.get_context(run_id)
        async for frame in subscribe_run_sse(
            context,
            self.state,
            run_id,
            role=role,
            last_event_id=last_event_id,
            ping_interval=ping_interval,
        ):
            yield frame

    # 14. Health & Platform Capabilities
    def get_health_status(self) -> dict[str, Any]:
        """Liveness and readiness probe for the Open-DV Platform."""
        import duckdb

        from opendv_contracts.spec import load_cli_spec

        schema_files = sorted(self.state.schema_registry.schemas_dir.glob("*.json"))
        cli_spec = load_cli_spec()
        res: dict[str, Any] = {
            "status": "ok",
            "service": "opendv-platform-api",
            "version": "0.1.0",
            "api_version": "v1",
            "contract_version": cli_spec.get("contract_version", "1.0.0"),
            "duckdb_version": duckdb.__version__,
            "schemas_loaded": len(schema_files),
            "registered_projects": len(self.state.projects),
            "registered_runs": len(self.state.runs),
        }
        if self.settings is not None:
            res["settings"] = self.settings.to_redacted_dict()
        return res

    def get_platform_capabilities(self) -> dict[str, Any]:
        """Return supported agent harnesses, 5-Tier RBAC roles, WB-00..WB-11 views, and gates."""
        harnesses = ["agy", "agy-cli", "antigravity", "claude", "claude-cli", "claude-code"]
        roles = [r.value for r in RbacRole]
        gates = ["G0", "G1", "G2", "G3", "G4", "G5"]
        classifications = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI", "RESTRICTED_PII"]
        return {
            "api_version": "v1",
            "contract_version": "6.0",
            "canonical_ir_version": "1.0.0",
            "supported_harnesses": harnesses,
            "harness_providers": harnesses,
            "supported_rbac_roles": roles,
            "rbac_roles": roles,
            "security_classifications": classifications,
            "workbench_views": [f"WB-{i:02d}" for i in range(12)],
            "workbench_view_titles": list(WORKBENCH_VIEW_TITLES.values()),
            "verification_gates": gates,
            "gates": gates,
        }

    # 15. Workbench View Projections
    def get_workbench_view_projection(
        self,
        run_id: str,
        view_id: str,
        *,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> dict[str, Any]:
        """Return RBAC-redacted 3-Layer workbench view state (`WB-00` through `WB-11`)."""
        if run_id not in self.state.runs:
            raise NotFoundError(f"Run '{run_id}' not found.")

        view_lookup = {k.upper(): k for k in WORKBENCH_VIEW_TITLES}
        for k, v in WORKBENCH_VIEW_TITLES.items():
            view_lookup[v.upper()] = k

        short_code = view_lookup.get(view_id.strip().upper())
        if short_code is None:
            valid_short = sorted(WORKBENCH_VIEW_TITLES.keys())
            raise NotFoundError(
                f"Unknown workbench view '{view_id}'. Expected one of {valid_short}."
            )

        canonical_title = WORKBENCH_VIEW_TITLES[short_code]
        seen_ids: set[str] = set()
        matched_events: list[dict[str, Any]] = []
        eff_role = normalize_rbac_role(role, default=RbacRole.PUBLIC)
        for source_list in (
            self.state.run_events.get(run_id, []),
            self.state.run_workbench_events.get(run_id, []),
            self.state.run_events_by_view.get(run_id, {}).get(short_code, []),
        ):
            for evt in source_list:
                evt_id = str(evt.get("event_id", ""))
                evt_view = str(evt.get("workbench_view_id") or evt.get("workbench_view") or "")
                if evt_view.startswith(short_code) and evt_id not in seen_ids:
                    seen_ids.add(evt_id)
                    matched_events.append(redact_event_for_role(evt, eff_role))

        primary_event = matched_events[-1] if matched_events else None
        return {
            "run_id": run_id,
            "view_id": short_code,
            "canonical_view_name": canonical_title,
            "role": eff_role.value if hasattr(eff_role, "value") else str(eff_role),
            "event_count": len(matched_events),
            "event": primary_event,
            "primary_event": primary_event,
            "events": matched_events,
        }

    # 16. 3-Level Explorer Bundle & Run-Isolated Artifacts (GAP-05, T6)
    def get_explorer_bundle(
        self,
        run_id: str,
        *,
        wave_id: str | None = None,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> ExplorerBundleDTO:
        """Retrieve 3-level explorer bundle for a run, optionally filtered by wave."""
        return get_explorer_bundle(self.state, run_id, wave_id=wave_id, role=role)

    def get_run_artifact(
        self,
        run_id: str,
        artifact_id: str,
        *,
        role: RbacRole = RbacRole.PUBLIC,
    ) -> RunArtifactDTO:
        """Retrieve a run-isolated artifact with lowercase 64-hex CAS hash and RBAC redaction."""
        return get_run_artifact(self.state, run_id, artifact_id, role=role)


__all__ = ["OpenDVService"]
