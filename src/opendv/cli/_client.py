"""Unified in-process (`OpenDVService`) and `--api-url` (`httpx`) core CLI backend client."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import ValidationError

from opendv.cli._client_workflows import CliWorkflowMixin, esc, proj_route
from opendv.cli._output import CliGlobalOptions, extract_json_error_payload
from opendv.models import (
    AgentPackDetail,
    AgentPackSummary,
    BundleManifest,
    ConnectorAdapterSpec,
    ErrorCode,
    ExternalMcpServerSpec,
    HealthResp,
    ImportReadinessReport,
    OpenDVError,
    ProblemDetails,
    ProjectInitReq,
    ProjectSettingsUpdateReq,
    ProjectState,
    ProjectSummary,
    ScanApprovalReq,
    SecretSummary,
    SecretWriteReq,
    SourceAttachReq,
    SourceInspectReq,
    SourceProfile,
    SourceReattachReq,
    SourceSummary,
    TrustedWorkspaceSpec,
    WorkspaceSettings,
    compute_workspace_agents_sha256,
)

if TYPE_CHECKING:
    from opendv.service import OpenDVService


def compute_local_workspace_agents_sha256(workspace_root: Path) -> str:
    """Hash `.opendv/agents` overrides via Layer-1 `compute_workspace_agents_sha256`."""
    return compute_workspace_agents_sha256(workspace_root) or ""


def resolve_cli_workspace_root(workspace_root: Path | str | None = None) -> Path:
    """Resolve the workspace directory (never `<cwd>/.opendv`) for `OpenDVService`."""
    if workspace_root is not None:
        ws = Path(workspace_root).expanduser()
        return (ws.parent if ws.name == ".opendv" else ws).resolve()
    env_home = os.environ.get("OPENDV_HOME", "").strip()
    if env_home:
        cand = Path(env_home).expanduser()
        return (cand.parent if cand.name == ".opendv" else cand).resolve()
    return Path.cwd().resolve()


def _error_from_http_response(response: httpx.Response) -> OpenDVError:
    payload = extract_json_error_payload(response.text)
    if payload and "error_code" in payload and "detail" in payload:
        try:
            problem = ProblemDetails.model_validate(payload)
            return OpenDVError(
                problem.detail,
                error_code=problem.error_code,
                diagnostics=problem.diagnostics,
                defect_report=problem.defect_report,
                run_id=problem.run_id,
            )
        except ValidationError:
            raw_code = str(payload.get("error_code", ""))
            for code in ErrorCode:
                if code.value == raw_code:
                    return OpenDVError(
                        str(payload.get("detail", response.text)), error_code=code
                    )

    status = response.status_code
    fallback = ErrorCode.AGENT_EXECUTION_FAILED
    if status == 404:
        fallback = ErrorCode.RESOURCE_NOT_FOUND
    elif status == 409:
        fallback = ErrorCode.PROJECT_JOB_ACTIVE
    elif status in (400, 413, 415, 422):
        fallback = ErrorCode.SYNTAX_INVALID_ARGUMENT
    elif status == 403:
        fallback = ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE
    detail = payload.get("detail") if isinstance(payload, dict) else None
    msg = str(detail) if detail else f"HTTP {status}: {response.text.strip() or 'request failed'}"
    return OpenDVError(msg, error_code=fallback)


class CliClient(CliWorkflowMixin):
    """Dispatch CLI operations either directly to `OpenDVService` or via `--api-url`."""

    def __init__(
        self,
        opts: CliGlobalOptions,
        *,
        workspace_root: Path | None = None,
        state_dir: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.opts = opts
        self._explicit_workspace_root = workspace_root
        self._resolved_workspace_root: Path | None = None
        self.state_dir = state_dir
        self.timeout_seconds = timeout_seconds
        self._service: OpenDVService | None = None

    @property
    def workspace_root(self) -> Path:
        """Resolve workspace root lazily without double-appending `.opendv`."""
        if self._resolved_workspace_root is None:
            self._resolved_workspace_root = resolve_cli_workspace_root(
                self._explicit_workspace_root
            )
        return self._resolved_workspace_root

    @property
    def is_remote(self) -> bool:
        """Return True when commands route over HTTP to `--api-url`."""
        return bool(self.opts.api_url)

    def service(self) -> OpenDVService:
        """Lazily create an in-process `OpenDVService` using resolved workspace settings."""
        if self._service is None:
            from opendv.service import OpenDVService

            self._service = OpenDVService(self.workspace_root, state_dir=self.state_dir)
        return self._service

    def _base_url(self) -> str:
        assert self.opts.api_url is not None
        return self.opts.api_url.rstrip("/")

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
        url = f"{self._base_url()}{path}"
        headers = {"X-OpenDV-Env-First": "1"} if self.opts.env_first else {}
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    files=files,
                    data=data,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise OpenDVError(
                f"Failed to connect to Open-DV API server at '{url}': {exc}",
                error_code=ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
            ) from exc

        if response.status_code >= 400:
            raise _error_from_http_response(response)
        return response

    # ---- System / Settings / Agents / MCP / Secrets ----

    def get_health(self) -> HealthResp:
        """Return API/service health status."""
        return self._call(lambda s: s.get_health(), HealthResp, "GET", "/api/v1/health")

    def get_settings(self) -> WorkspaceSettings:
        """Fetch current workspace settings."""
        return self._call(lambda s: s.get_settings(), WorkspaceSettings, "GET", "/api/v1/settings")

    def update_settings(self, settings: WorkspaceSettings) -> WorkspaceSettings:
        """Persist updated workspace settings."""
        return self._call(
            lambda s: s.update_settings(settings),
            WorkspaceSettings,
            "PUT",
            "/api/v1/settings",
            json_body=settings.model_dump(mode="json"),
        )

    def trust_workspace(self, path: Path) -> WorkspaceSettings:
        """Trust workspace `.opendv/agents/*/SKILL.md` overrides."""
        resolved = path.expanduser().resolve()
        if not self.is_remote:
            return self.service().trust_workspace_path(resolved)
        if not resolved.is_dir():
            raise OpenDVError(
                f"Workspace directory '{resolved}' does not exist.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        digest = compute_local_workspace_agents_sha256(resolved)
        settings = self.get_settings()
        kept = [
            spec
            for spec in settings.trusted_workspaces
            if Path(spec.workspace_root).resolve() != resolved
        ]
        kept.append(
            TrustedWorkspaceSpec(
                workspace_root=str(resolved),
                agent_pack_sha256=digest,
                trusted_at=datetime.now(tz=UTC).isoformat(),
            )
        )
        return self.update_settings(settings.model_copy(update={"trusted_workspaces": kept}))

    def list_agents(self) -> list[AgentPackSummary]:
        """List discovered agent packs."""
        return self._call_list(
            lambda s: s.list_agent_packs(), AgentPackSummary, "GET", "/api/v1/agents"
        )

    def get_agent(self, agent_id: str) -> AgentPackDetail:
        """Inspect one agent pack."""
        return self._call(
            lambda s: s.get_agent_pack(agent_id),
            AgentPackDetail,
            "GET",
            f"/api/v1/agents/{esc(agent_id)}",
        )

    def list_mcp_servers(self) -> list[ExternalMcpServerSpec]:
        """List registered external MCP servers."""
        return self._call_list(
            lambda s: s.list_mcp_servers(),
            ExternalMcpServerSpec,
            "GET",
            "/api/v1/mcp/servers",
        )

    def register_mcp_server(self, spec: ExternalMcpServerSpec) -> ExternalMcpServerSpec:
        """Register or update an external MCP server."""
        return self._call(
            lambda s: s.upsert_mcp_server(spec),
            ExternalMcpServerSpec,
            "POST",
            "/api/v1/mcp/servers",
            json_body=spec.model_dump(mode="json"),
        )

    def remove_mcp_server(self, server_id: str) -> dict[str, Any]:
        """Delete an external MCP server."""
        if self.is_remote:
            self._http("DELETE", f"/api/v1/mcp/servers/{esc(server_id)}")
        else:
            self.service().delete_mcp_server(server_id)
        return {"deleted": True, "server_id": server_id}

    def list_secrets(self, keys: list[str] | None = None) -> list[SecretSummary]:
        """List secret availability metadata."""
        params = httpx.QueryParams([("keys", k) for k in keys]) if keys else None
        return self._call_list(
            lambda s: s.list_secrets(keys or ()),
            SecretSummary,
            "GET",
            "/api/v1/secrets",
            params=params,
        )

    def set_secret(self, key: str, value: str) -> SecretSummary:
        """Store a secret via `SecretWriteReq`."""
        req = SecretWriteReq(key=key, value=value)
        return self._call(
            lambda s: s.set_secret(req.key, req.value),
            SecretSummary,
            "POST",
            "/api/v1/secrets",
            json_body=req.model_dump(mode="json"),
        )

    def delete_secret(self, key: str) -> dict[str, Any]:
        """Delete a secret by key."""
        if self.is_remote:
            self._http("DELETE", f"/api/v1/secrets/{esc(key)}")
        else:
            self.service().delete_secret(key)
        return {"deleted": True, "key": key}

    # ---- Projects ----

    def list_projects(self) -> list[ProjectSummary]:
        """List workspace projects."""
        return self._call_list(
            lambda s: s.list_projects(), ProjectSummary, "GET", "/api/v1/projects"
        )

    def init_project(self, req: ProjectInitReq) -> ProjectState:
        """Create a new project."""
        return self._call(
            lambda s: s.init_project(req),
            ProjectState,
            "POST",
            "/api/v1/projects",
            json_body=req.model_dump(mode="json"),
        )

    def get_project(self, project_id: str) -> ProjectState:
        """Retrieve a project."""
        return self._call(
            lambda s: s.get_project(project_id), ProjectState, "GET", proj_route(project_id)
        )

    def update_project_settings(
        self, project_id: str, req: ProjectSettingsUpdateReq
    ) -> ProjectState:
        """Partially update per-project settings snapshot."""
        return self._call(
            lambda s: s.update_project_settings(project_id, req),
            ProjectState,
            "PATCH",
            proj_route(project_id, "/settings"),
            json_body=req.model_dump(mode="json", exclude_unset=True),
        )

    def delete_project(self, project_id: str) -> dict[str, Any]:
        """Delete a project."""
        if self.is_remote:
            self._http("DELETE", proj_route(project_id))
        else:
            self.service().delete_project(project_id)
        return {"deleted": True, "project_id": project_id}

    def export_project(
        self, project_id: str, *, out_path: Path, full_demo: bool = False
    ) -> dict[str, Any]:
        """Export a `.opendv.zip` bundle."""
        mode: Literal["core_project_state", "full_demo"] = (
            "full_demo" if full_demo else "core_project_state"
        )
        resolved_out = out_path.expanduser().resolve()
        if not self.is_remote:
            saved_path, manifest = self.service().export_bundle(
                project_id, mode=mode, output_path=resolved_out
            )
            return {
                "output_path": str(saved_path),
                "manifest": manifest.model_dump(mode="json"),
            }

        resp = self._http("GET", proj_route(project_id, "/bundle"), params={"mode": mode})
        from opendv.service import atomic_write_bytes

        atomic_write_bytes(resolved_out, resp.content)
        manifest_data: dict[str, Any] | None = None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            if "manifest.json" in zf.namelist():
                manifest = BundleManifest.model_validate_json(zf.read("manifest.json"))
                manifest_data = manifest.model_dump(mode="json")
        return {"output_path": str(resolved_out), "manifest": manifest_data}

    def import_project(
        self, zip_path: Path, *, conflict: str = "fail", trust_workspace: bool = False
    ) -> ImportReadinessReport:
        """Import a `.opendv.zip` archive."""
        resolved = zip_path.expanduser().resolve()
        if not resolved.is_file():
            raise OpenDVError(
                f"Bundle file not found: '{resolved}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        if not self.is_remote:
            return self.service().import_bundle(
                resolved,
                conflict=conflict,  # type: ignore[arg-type]
                trust_workspace=trust_workspace,
            )
        raw_bytes = resolved.read_bytes()
        resp = self._http(
            "POST",
            "/api/v1/projects/import",
            files={"file": (resolved.name, raw_bytes, "application/zip")},
            data={
                "conflict": conflict,
                "trust_workspace": "true" if trust_workspace else "false",
            },
        )
        return ImportReadinessReport.model_validate(resp.json())

    # ---- Sources ----

    def list_source_adapters(self) -> list[ConnectorAdapterSpec]:
        """List supported connector adapters."""
        return self._call_list(
            lambda s: s.list_source_adapters(),
            ConnectorAdapterSpec,
            "GET",
            "/api/v1/sources/adapters",
        )

    def inspect_source(self, req: SourceInspectReq) -> SourceProfile:
        """Preview candidate source tables and PII profile."""
        return self._call(
            lambda s: s.inspect_source(req),
            SourceProfile,
            "POST",
            "/api/v1/sources/inspect",
            json_body=req.model_dump(mode="json"),
        )

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        """List attached sources."""
        return self._call_list(
            lambda s: s.list_sources(project_id),
            SourceSummary,
            "GET",
            proj_route(project_id, "/sources"),
        )

    def attach_source(self, project_id: str, req: SourceAttachReq) -> SourceSummary:
        """Attach and profile a source."""
        return self._call(
            lambda s: s.attach_source(project_id, req),
            SourceSummary,
            "POST",
            proj_route(project_id, "/sources"),
            json_body=req.model_dump(mode="json"),
        )

    def reattach_source(
        self, project_id: str, source_id: str, req: SourceReattachReq
    ) -> SourceSummary:
        """Reattach or reselect source tables."""
        return self._call(
            lambda s: s.reattach_source(project_id, source_id, req),
            SourceSummary,
            "PATCH",
            proj_route(project_id, f"/sources/{esc(source_id)}"),
            json_body=req.model_dump(mode="json"),
        )

    def upload_domain_note(
        self, project_id: str, *, filename: str, content: bytes
    ) -> SourceSummary:
        """Upload an inline or file domain note."""
        return self._call(
            lambda s: s.upload_source(
                project_id, filename=filename, content=content, kind="domain_note"
            ),
            SourceSummary,
            "POST",
            proj_route(project_id, "/sources/upload"),
            files={"file": (filename, content, "text/markdown")},
            data={"kind": "domain_note"},
        )

    def approve_scan_pages(
        self, project_id: str, doc_id: str, req: ScanApprovalReq
    ) -> Any:
        """Approve scanned PDF pages for OCR processing."""
        if self.is_remote:
            route = proj_route(project_id, f"/evidence/documents/{esc(doc_id)}/approve-scan")
            return self._http("POST", route, json_body=req.model_dump(mode="json")).json()
        return self.service().approve_scan_pages(project_id, doc_id, req)

    def detach_source(self, project_id: str, source_id: str) -> dict[str, Any]:
        """Detach an uncommitted source."""
        if self.is_remote:
            self._http("DELETE", proj_route(project_id, f"/sources/{esc(source_id)}"))
        else:
            self.service().detach_source(project_id, source_id)
        return {"deleted": True, "project_id": project_id, "source_id": source_id}
