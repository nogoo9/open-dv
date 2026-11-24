"""Unified OpenDVService facade orchestrating all 7 V2 Data Vault lifecycle steps."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from opendv import __version__
from opendv.agents import AgentHarnessPort, list_agent_packs, load_agent_pack
from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ExternalMcpReceipt, RunEventLogPage, RunStep
from opendv.models.project import (
    BundleManifest,
    ConceptMemorySpec,
    DbtSqlDialect,
    ImportReadinessReport,
    ProjectState,
    ProjectSummary,
    TargetManifest,
    WorkspaceSettings,
)
from opendv.models.sources import (
    ConnectorAdapterSpec,
    DocumentMap,
    ExternalMcpServerSpec,
    PageTextView,
    SourceProfile,
    SourceSummary,
    TableIndexEntry,
    TablePreviewPage,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_discovery import (
    AgentPackDetail,
    AgentPackSummary,
    CitationResolveReq,
    CitationSpanResp,
    ConceptMemoryUpdate,
    FiboQueryReq,
    FiboQueryResp,
    HealthResp,
    HoldAnswersReq,
    OntologyBundleResp,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
    ProjectInitReq,
    RunControlReq,
    RunStateResp,
    ScanApprovalReq,
    SecretSummary,
    SourceAttachReq,
    SourceInspectReq,
    SourceReattachReq,
    StarterPackPreview,
    StarterPackSummary,
    WorkspaceSeedSummary,
)
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    GuidedRunView,
    HorizonActionReq,
    MigrationApplyResp,
    SqlQueryReq,
    SqlQueryResult,
    StorageCompileReq,
    StorageTargetReq,
    TargetBundleResp,
    VaultBuildReq,
    VaultVerifyResp,
    WaiverSubmitReq,
)
from opendv.service.bm25_matcher import Bm25ConceptMatcher
from opendv.service.bundles import BundleMode, ConflictPolicy
from opendv.service.os_platform import resolve_user_state_dir
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    list_external_mcp_receipts,
    list_project_runs,
    load_latest_run_state,
    tail_run_logs,
)
from opendv.service.workflows.discovery_svc import DiscoveryService
from opendv.service.workflows.projects_svc import ProjectsService
from opendv.service.workflows.storage_svc import StorageService
from opendv.service.workflows.vault_svc import VaultService


class OpenDVService:
    """Headless application service unifying Steps 1-7 over SQLite WAL and filesystem state."""

    def __init__(
        self,
        workspace_root: Path | str,
        state_dir: Path | None = None,
        harness: AgentHarnessPort | None = None,
        *,
        keyring_backend: Any | None = None,
        allow_headless_fallback: bool = True,
    ) -> None:
        self.workspace_root: Path = Path(workspace_root).resolve()
        self.state_dir: Path = resolve_user_state_dir(state_dir)
        self.injected_harness: AgentHarnessPort | None = harness

        self.settings_store = SettingsStore(user_state_dir=self.state_dir)
        self.secret_store = KeyringSecretStore(
            self.workspace_root,
            state_dir=self.state_dir,
            settings_store=self.settings_store,
            keyring_backend=keyring_backend,
            allow_headless_fallback=allow_headless_fallback,
        )
        self.state_manager = ProjectStateManager(self.workspace_root)
        self.state_manager.initialize()
        self.matcher = Bm25ConceptMatcher()

        self.projects = ProjectsService(
            self.workspace_root,
            self.state_manager,
            self.settings_store,
            self.secret_store,
        )
        self.discovery = DiscoveryService(
            state_manager=self.state_manager,
            settings_store=self.settings_store,
            secret_store=self.secret_store,
            workspace_root=self.workspace_root,
            matcher=self.matcher,
            injected_harness=self.injected_harness,
        )
        self.vault = VaultService(
            state_manager=self.state_manager,
            secret_store=self.secret_store,
            workspace_root=self.workspace_root,
            injected_harness=self.injected_harness,
        )
        self.storage = StorageService(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

    # ------------------------------------------------------------------
    # System, Settings, Secrets, MCP Servers, Agent Packs, & Telemetry
    # ------------------------------------------------------------------

    def get_health(self) -> HealthResp:
        """Return service liveness health check payload."""
        return HealthResp(status="ok", version=__version__)

    def get_settings(self) -> WorkspaceSettings:
        """Load non-secret WorkspaceSettings."""
        return self.settings_store.load_settings()

    def update_settings(self, settings: WorkspaceSettings) -> WorkspaceSettings:
        """Persist validated WorkspaceSettings without secret literals."""
        return self.settings_store.save_settings(settings)

    def list_secrets(self, keys: Sequence[str] = ()) -> list[SecretSummary]:
        """List masked presence summaries for stored/known secret keys."""
        return self.secret_store.list_secret_summaries(keys)

    def set_secret(self, key: str, value: str) -> SecretSummary:
        """Write a workspace-scoped secret to OS keyring or user_state_file."""
        return self.secret_store.set_secret(key, value)

    def delete_secret(self, key: str) -> None:
        """Remove a secret from workspace storage."""
        self.secret_store.delete_secret(key)

    def list_mcp_servers(self) -> list[ExternalMcpServerSpec]:
        """List registered external MCP servers."""
        return self.settings_store.load_mcp_servers()

    def save_mcp_servers(
        self, servers: Sequence[ExternalMcpServerSpec]
    ) -> list[ExternalMcpServerSpec]:
        """Replace registered external MCP server catalog atomically."""
        self.settings_store.save_mcp_servers(servers)
        return self.settings_store.load_mcp_servers()

    def upsert_mcp_server(self, spec: ExternalMcpServerSpec) -> ExternalMcpServerSpec:
        """Insert or update a single ExternalMcpServerSpec by server_id."""
        existing = [
            s for s in self.settings_store.load_mcp_servers() if s.server_id != spec.server_id
        ]
        existing.append(spec)
        self.settings_store.save_mcp_servers(existing)
        return spec

    def delete_mcp_server(self, server_id: str) -> None:
        """Delete a registered external MCP server by server_id."""
        servers = self.settings_store.load_mcp_servers()
        remaining = [s for s in servers if s.server_id != server_id]
        if len(remaining) == len(servers):
            raise OpenDVError(
                f"External MCP server '{server_id}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        self.settings_store.save_mcp_servers(remaining)

    def list_agent_packs(self) -> list[AgentPackSummary]:
        """List builtin and trusted workspace SKILL.md agent packs."""
        ws = self.settings_store.load_settings()
        return list_agent_packs(
            workspace_root=self.workspace_root,
            trusted_workspaces=ws.trusted_workspaces,
        )

    def get_agent_pack(self, agent_id: str) -> AgentPackDetail:
        """Inspect a single SKILL.md agent pack."""
        ws = self.settings_store.load_settings()
        return load_agent_pack(
            agent_id,
            workspace_root=self.workspace_root,
            trusted_workspaces=ws.trusted_workspaces,
        )

    def list_runs(
        self, project_id: str, *, step: RunStep | None = None
    ) -> list[RunStateResp]:
        return list_project_runs(self.state_manager, project_id, step=step)

    def get_run(self, project_id: str, run_id: str) -> RunStateResp:
        return load_latest_run_state(self.state_manager, project_id, run_id)

    def get_run_logs(
        self,
        project_id: str,
        run_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> RunEventLogPage:
        """Tail JSONL RunStreamEvent log records for run_id."""
        return tail_run_logs(
            self.state_manager, project_id, run_id, since_seq=since_seq, limit=limit
        )

    # ------------------------------------------------------------------
    # Step 1: Projects, Seeds, Starter Packs, Bundles, & Concept Memory
    # ------------------------------------------------------------------

    def list_projects(self) -> list[ProjectSummary]:
        return self.projects.list_projects()

    def get_project(self, project_id: str) -> ProjectState:
        return self.projects.get_project(project_id)

    def init_project(self, req: ProjectInitReq) -> ProjectState:
        return self.projects.init_project(req)

    def delete_project(self, project_id: str) -> None:
        self.projects.delete_project(project_id)

    def list_workspace_seeds(
        self, domain_namespace: str | None = None
    ) -> list[WorkspaceSeedSummary]:
        return self.projects.list_workspace_seeds(domain_namespace=domain_namespace)

    def trust_workspace_path(self, path: Path | str | None = None) -> WorkspaceSettings:
        return self.projects.trust_workspace_path(path)

    def list_starter_packs(self) -> list[StarterPackSummary]:
        return self.projects.list_starter_packs()

    def preview_starter_pack(self, pack_id: str) -> StarterPackPreview:
        return self.projects.preview_starter_pack(pack_id)

    def export_bundle(
        self,
        project_id: str,
        *,
        mode: BundleMode = "core_project_state",
        output_path: Path | str | None = None,
    ) -> tuple[Path, BundleManifest]:
        return self.projects.export_bundle(project_id, mode=mode, output_path=output_path)

    def import_bundle(
        self,
        bundle_path: Path | str,
        *,
        conflict: ConflictPolicy = "fail",
        trust_workspace: bool = False,
        enforce_workspace_containment: bool = True,
        is_remote_request: bool = False,
        allowed_roots: Sequence[Path | str] | None = None,
    ) -> ImportReadinessReport:
        return self.projects.import_bundle(
            bundle_path,
            conflict=conflict,
            trust_workspace=trust_workspace,
            enforce_workspace_containment=enforce_workspace_containment,
            is_remote_request=is_remote_request,
            allowed_roots=allowed_roots,
        )

    def get_concept_memory(self, project_id: str) -> list[ConceptMemorySpec]:
        return self.projects.get_concept_memory(project_id)

    def upsert_concept_memory(
        self, project_id: str, req: ConceptMemoryUpdate
    ) -> ConceptMemorySpec:
        return self.projects.upsert_concept_memory(project_id, req)

    # ------------------------------------------------------------------
    # Steps 2-5: Sources, Documents, Scans, Discovery, Validate, Commit
    # ------------------------------------------------------------------

    def list_source_adapters(self) -> list[ConnectorAdapterSpec]:
        return self.discovery.list_adapters()

    def inspect_source(self, req: SourceInspectReq) -> SourceProfile:
        return self.discovery.inspect_source(req)

    def attach_source(self, project_id: str, req: SourceAttachReq) -> SourceSummary:
        return self.discovery.attach_source(project_id, req)

    def upload_source(
        self,
        project_id: str,
        *,
        filename: str,
        content: bytes,
        kind: str | None = None,
    ) -> SourceSummary:
        return self.discovery.upload_source(
            project_id, filename=filename, content=content, kind=kind
        )

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        return self.discovery.list_sources(project_id)

    def reattach_source(
        self, project_id: str, source_id: str, req: SourceReattachReq
    ) -> SourceSummary:
        return self.discovery.reattach_source(project_id, source_id, req)

    def detach_source(self, project_id: str, source_id: str) -> None:
        self.discovery.detach_source(project_id, source_id)

    def list_tables_index(self, project_id: str) -> list[TableIndexEntry]:
        return self.discovery.list_tables_index(project_id)

    def preview_table(
        self,
        project_id: str,
        source_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> TablePreviewPage:
        return self.discovery.preview_table(
            project_id, source_id, table_id, offset=offset, limit=limit
        )

    def preview_table_by_id(
        self,
        project_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        source_id: str | None = None,
    ) -> TablePreviewPage:
        return self.discovery.preview_table_by_id(
            project_id, table_id, offset=offset, limit=limit, source_id=source_id
        )

    def list_document_maps(self, project_id: str) -> list[DocumentMap]:
        return self.discovery.list_document_maps(project_id)

    def get_document_map(self, project_id: str, source_id: str) -> DocumentMap:
        return self.discovery.get_document_map(project_id, source_id)

    def get_document_pages(
        self,
        project_id: str,
        source_id: str,
        *,
        page_start: int = 1,
        page_end: int | None = None,
    ) -> list[PageTextView]:
        return self.discovery.get_document_pages(
            project_id, source_id, page_start=page_start, page_end=page_end
        )

    def approve_scan_pages(
        self, project_id: str, source_id: str, req: ScanApprovalReq
    ) -> DocumentMap:
        return self.discovery.approve_scan_pages(project_id, source_id, req)

    async def start_ontology_construction(
        self,
        project_id: str,
        req: OntologyConstructReq,
        *,
        run_id: str | None = None,
    ) -> RunStateResp:
        return await self.discovery.start_ontology_construction(project_id, req, run_id=run_id)

    def get_ontology_run(self, project_id: str, run_id: str) -> RunStateResp:
        return self.discovery.get_ontology_run(project_id, run_id)

    async def answer_ontology_hold(
        self, project_id: str, run_id: str, req: HoldAnswersReq
    ) -> RunStateResp:
        return await self.discovery.answer_ontology_hold(project_id, run_id, req)

    def control_ontology_run(
        self, project_id: str, run_id: str, req: RunControlReq
    ) -> RunStateResp:
        return self.discovery.control_ontology_run(project_id, run_id, req)

    def get_ontology_bundle(self, project_id: str) -> OntologyBundleResp:
        return self.discovery.get_ontology_bundle(project_id)

    def validate_ontology(
        self, project_id: str, req: OntologyValidateReq
    ) -> OntologyValidationResp:
        return self.discovery.validate_ontology(project_id, req)

    def commit_ontology(
        self, project_id: str, req: OntologyCommitReq
    ) -> OntologyRevisionResp:
        return self.discovery.commit_ontology(project_id, req)

    def fibo_qa(
        self, req: FiboQueryReq, *, project_id: str | None = None
    ) -> FiboQueryResp:
        return self.discovery.fibo_qa(req, project_id=project_id)

    def resolve_citation_span(
        self, project_id: str, req: CitationResolveReq
    ) -> CitationSpanResp:
        return self.discovery.resolve_citation_span(project_id, req)

    def export_ontology(
        self,
        project_id: str,
        *,
        fmt: Literal["yaml", "json", "ttl"] = "yaml",
        include_staged: bool = False,
    ) -> tuple[str, str]:
        return self.discovery.export_ontology(project_id, fmt=fmt, include_staged=include_staged)

    # ------------------------------------------------------------------
    # Step 6: Vault Synthesis, CEGAR, Horizon Critic, Waivers, & Guided UX
    # ------------------------------------------------------------------

    async def build_vault(
        self, project_id: str, req: VaultBuildReq, *, run_id: str | None = None
    ) -> RunStateResp:
        return await self.vault.build_vault(project_id, req, run_id=run_id)

    def get_vault_run(self, project_id: str, run_id: str) -> RunStateResp:
        return self.vault.get_vault_run(project_id, run_id)

    def list_vault_attempts(self, project_id: str, run_id: str) -> list[AttemptSnapshotSpec]:
        return self.vault.list_vault_attempts(project_id, run_id)

    async def send_vault_control(
        self, project_id: str, run_id: str, req: RunControlReq
    ) -> RunStateResp:
        return await self.vault.send_vault_control(project_id, run_id, req)

    async def submit_vault_decisions(
        self, project_id: str, run_id: str, req: HoldAnswersReq
    ) -> RunStateResp:
        return await self.vault.submit_vault_decisions(project_id, run_id, req)

    async def submit_horizon_steering(
        self, project_id: str, run_id: str, req: HorizonActionReq
    ) -> RunStateResp:
        return await self.vault.submit_horizon_steering(project_id, run_id, req)

    def submit_vault_waiver(
        self, project_id: str, req: WaiverSubmitReq
    ) -> AntiPatternJustification:
        return self.vault.submit_vault_waiver(project_id, req)

    def apply_vault_waivers(
        self, project_id: str, waivers: Sequence[WaiverSubmitReq]
    ) -> VaultVerifyResp:
        return self.vault.apply_vault_waivers(project_id, waivers)

    def get_guided_view(self, project_id: str, run_id: str | None = None) -> GuidedRunView:
        return self.vault.get_guided_view(project_id, run_id=run_id)

    def get_vault_ir(self, project_id: str) -> CertifiedVaultIR:
        return self.vault.get_vault_ir(project_id)

    def verify_vault(self, project_id: str) -> VaultVerifyResp:
        return self.vault.verify_vault(project_id)

    # ------------------------------------------------------------------
    # Step 7: Storage Compile, Migrate, Regenerate, Receipts, & Query
    # ------------------------------------------------------------------

    def list_targets(self, project_id: str) -> list[dict[str, Any]]:
        return self.storage.list_targets(project_id)

    def list_target_manifests(self, project_id: str) -> list[TargetManifest]:
        return self.storage.list_target_manifests(project_id)

    def upsert_target_config(
        self, project_id: str, target_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        return self.storage.upsert_target_config(project_id, target_id, config)

    def list_storage_receipts(self, project_id: str) -> list[dict[str, Any]]:
        return self.storage.list_receipts(project_id)

    def list_external_mcp_receipts(self, project_id: str) -> list[ExternalMcpReceipt]:
        return list_external_mcp_receipts(self.state_manager, project_id)

    def compile_target(
        self, project_id: str, req: StorageCompileReq
    ) -> TargetBundleResp | list[TargetBundleResp]:
        return self.storage.compile_target(project_id, req)

    def compile_all_targets(
        self,
        project_id: str,
        *,
        dbt_sql_dialect: DbtSqlDialect = "duckdb",
        verify_runtime: bool = False,
    ) -> list[TargetBundleResp]:
        return self.storage.compile_all_targets(
            project_id, dbt_sql_dialect=dbt_sql_dialect, verify_runtime=verify_runtime
        )

    def migrate_target(
        self, project_id: str, req: StorageTargetReq
    ) -> MigrationApplyResp:
        return self.storage.migrate_target(project_id, req)

    def regenerate_target(
        self, project_id: str, req: StorageTargetReq
    ) -> MigrationApplyResp:
        return self.storage.regenerate_target(project_id, req)

    def query_target(
        self, project_id: str, req: SqlQueryReq
    ) -> SqlQueryResult:
        return self.storage.query_target(project_id, req)


__all__ = ["OpenDVService"]
