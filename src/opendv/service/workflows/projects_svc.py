"""Step 1 project lifecycle, seeds, starter packs, concept memory, and .opendv.zip workflows."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv.agents import compute_workspace_agents_sha256
from opendv.modeling.prompts import MAX_BUSINESS_CONTEXT_HINTS_TOKENS
from opendv.modeling.token_utils import count_tokens
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.policy import VALID_V6B_RULE_IDS
from opendv.models.project import (
    BundleManifest,
    ConceptMemorySpec,
    ImportReadinessReport,
    ProjectSettingsSnapshot,
    ProjectSettingsUpdateReq,
    ProjectState,
    ProjectSummary,
    RevisionMetadata,
    TrustedWorkspaceSpec,
    WorkspaceSettings,
)
from opendv.models.wire_dtos_discovery import (
    ConceptMemoryUpdate,
    ProjectInitReq,
    StarterPackPreview,
    StarterPackSummary,
    WorkspaceSeedSummary,
)
from opendv.service.bundles import (
    BundleMode,
    ConflictPolicy,
    export_project_bundle,
    import_project_bundle,
)
from opendv.service.os_platform import validate_workspace_containment
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._project_seeds import (
    discover_workspace_seeds,
    resolve_initial_ontology,
)
from opendv.service.workflows._run_adapters import (
    append_audit_log,
    list_revision_models,
    validate_project_id_or_error,
)
from opendv.sources.starter_packs import list_starter_packs, preview_starter_pack

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class ProjectsService:
    """Headless workflow service for Step 1 projects, seeds, starter packs, and bundles."""

    def __init__(
        self,
        workspace_root: Path | str,
        state_manager: ProjectStateManager,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state_manager = state_manager
        self.settings_store = settings_store
        self.secret_store = secret_store

    def list_projects(self) -> list[ProjectSummary]:
        """List all projects in the workspace state store."""
        return self.state_manager.list_projects()

    def get_project(self, project_id: str) -> ProjectState:
        """Fetch full ProjectState for project_id."""
        clean_pid = validate_project_id_or_error(project_id)
        return self.state_manager.get_project(clean_pid)

    def update_project_settings(
        self,
        project_id: str,
        req: ProjectSettingsUpdateReq | ProjectSettingsSnapshot,
    ) -> ProjectState:
        """Partially or fully update per-project settings snapshot with governance checks."""
        clean_pid = validate_project_id_or_error(project_id)
        proj = self.state_manager.get_project(clean_pid)
        if isinstance(req, ProjectSettingsUpdateReq):
            merged: dict[str, Any] = proj.settings_snapshot.model_dump()
            for field_name in req.model_fields_set:
                val = getattr(req, field_name)
                merged[field_name] = val.model_dump() if hasattr(val, "model_dump") else val
            try:
                updated = ProjectSettingsSnapshot.model_validate(merged)
            except ValidationError as exc:
                raise OpenDVError(
                    f"Invalid project settings update: {exc}",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc
        else:
            updated = req

        if updated.cegar_policy == "strict" and updated.v7_critic_tier != "FRONTIER_CRITIC":
            raise OpenDVError(
                "v7_critic_tier must be FRONTIER_CRITIC when cegar_policy is strict.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if (
            updated.business_context_hints
            and count_tokens(updated.business_context_hints) > MAX_BUSINESS_CONTEXT_HINTS_TOKENS
        ):
            raise OpenDVError(
                f"business_context_hints exceeds {MAX_BUSINESS_CONTEXT_HINTS_TOKENS} "
                "cl100k_base tokens.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        dis = updated.modeling_preferences.disabled_v6b_blocks
        invalid_rules = [r for r in dis if r not in VALID_V6B_RULE_IDS]
        if invalid_rules:
            raise OpenDVError(
                f"Invalid disabled_v6b_blocks rule ID(s): {invalid_rules}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if updated.cegar_policy == "strict" and dis:
            raise OpenDVError(
                "disabled_v6b_blocks must be empty under strict CEGAR policy.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        state = self.state_manager.update_project_pointers(
            clean_pid,
            settings_snapshot=updated,
        )
        append_audit_log(
            self.state_manager,
            clean_pid,
            event_type="PROJECT_SETTINGS_UPDATED",
            actor="human",
            step="step1",
            summary=f"Updated settings for project '{clean_pid}'.",
            details={
                "cegar_policy": updated.cegar_policy,
                "v7_critic_tier": updated.v7_critic_tier,
                "real_row_proving": updated.real_row_proving,
                "proving_sample_rows": updated.proving_sample_rows,
            },
        )
        return state

    def init_project(self, req: ProjectInitReq) -> ProjectState:
        """Create a project from Blank, StarterPack, WorkspaceSeed, or YAML/JSON baseline."""
        ont, init_mode = resolve_initial_ontology(req, self.workspace_root)
        raw_ver = ont.ontology_version.strip().lstrip("v")
        if not _SEMVER_RE.fullmatch(raw_ver):
            raise OpenDVError(
                f"Initial ontology_version '{ont.ontology_version}' must be SemVer (x.y.z).",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        normalized_ont = ont.model_copy(update={"ontology_version": raw_ver})

        ws_settings = self.settings_store.load_settings()
        snapshot = ProjectSettingsSnapshot(
            harness_provider=ws_settings.harness_provider,
            v7_critic_tier=ws_settings.v7_critic_tier,
            cegar_policy=ws_settings.cegar_policy,
            dbt_sql_dialect=ws_settings.dbt_sql_dialect,
            max_turns=ws_settings.max_turns,
            max_tokens=ws_settings.max_tokens,
            timeout_seconds=ws_settings.timeout_seconds,
            secret_precedence=ws_settings.secret_precedence,
            modeling_preferences=ws_settings.modeling_preferences,
        )

        self.state_manager.create_project(req.project_id, req.domain_namespace, snapshot)
        try:
            rev_key = f"v{raw_ver}"
            rev_id = f"rev-ont-{rev_key}-{sha256_jcs(normalized_ont)[:8]}"
            self.state_manager.save_revision(
                req.project_id,
                revision_id=rev_id,
                rev_kind="ontology_version",
                rev_key=rev_key,
                parent_rev_id=None,
                payload=normalized_ont,
                metadata=RevisionMetadata(actor="human", reason=f"init:{init_mode}"),
            )
            self.state_manager.update_project_pointers(
                req.project_id,
                active_ontology_rev_id=rev_id,
            )
            append_audit_log(
                self.state_manager,
                req.project_id,
                event_type="PROJECT_CREATED",
                actor="human",
                step="step1",
                summary=f"Initialized project '{req.project_id}' ({init_mode}).",
                details={"init_mode": init_mode, "ontology_version": raw_ver},
            )
            return self.state_manager.get_project(req.project_id)
        except Exception:
            self.state_manager.delete_project(req.project_id)
            raise

    def delete_project(self, project_id: str) -> None:
        """Delete a project if no active job lease is held."""
        proj = self.get_project(project_id)
        if proj.has_active_lease:
            raise OpenDVError(
                f"Cannot delete project '{project_id}' while an active job lease is held.",
                error_code=ErrorCode.PROJECT_JOB_ACTIVE,
            )
        self.state_manager.delete_project(proj.project_id)

    def list_workspace_seeds(
        self, domain_namespace: str | None = None
    ) -> list[WorkspaceSeedSummary]:
        """Discover domain-scoped ontology seed files under workspace_root."""
        return [
            summary
            for summary, _ in discover_workspace_seeds(
                self.workspace_root, domain_namespace=domain_namespace
            )
        ]

    def list_starter_packs(self) -> list[StarterPackSummary]:
        """Return metadata summaries for all built-in starter packs."""
        return list_starter_packs()

    def preview_starter_pack(self, pack_id: str) -> StarterPackPreview:
        """Return full GraphAOntology preview for pack_id."""
        return preview_starter_pack(pack_id)

    def export_bundle(
        self,
        project_id: str,
        *,
        mode: BundleMode = "core_project_state",
        output_path: Path | str | None = None,
    ) -> tuple[Path, BundleManifest]:
        """Export project_id into a portable .opendv.zip archive."""
        proj = self.get_project(project_id)
        if output_path is None:
            suffix = "full_demo" if mode == "full_demo" else "core"
            target_path = (
                self.state_manager.opendv_home
                / "exports"
                / f"{proj.project_id}.{suffix}.opendv.zip"
            )
        else:
            target_path = Path(output_path)
        return export_project_bundle(
            self.state_manager,
            proj.project_id,
            target_path,
            mode=mode,
            project_dir=self.state_manager.project_dir(proj.project_id),
        )

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
        """Verify and import an .opendv.zip archive into the workspace state store."""
        if enforce_workspace_containment:
            effective_roots = (
                list(allowed_roots)
                if allowed_roots is not None
                else [self.workspace_root, self.state_manager.opendv_home]
            )
            resolved_bundle = validate_workspace_containment(
                bundle_path,
                allowed_roots=effective_roots,
                is_remote_request=is_remote_request,
            )
        else:
            resolved_bundle = Path(bundle_path)
        return import_project_bundle(
            self.state_manager,
            self.secret_store,
            resolved_bundle,
            projects_root=self.state_manager.opendv_home / "projects",
            conflict=conflict,
            trust_workspace=trust_workspace,
        )

    def get_concept_memory(self, project_id: str) -> list[ConceptMemorySpec]:
        """List persisted user concept-memory pin/mute preferences."""
        clean_pid = validate_project_id_or_error(project_id)
        return [
            spec
            for _, spec in list_revision_models(
                self.state_manager, clean_pid, "concept_memory", ConceptMemorySpec
            )
        ]

    def upsert_concept_memory(
        self, project_id: str, req: ConceptMemoryUpdate
    ) -> ConceptMemorySpec:
        """Persist or update a ConceptMemorySpec preference."""
        clean_pid = validate_project_id_or_error(project_id)
        spec = ConceptMemorySpec(
            term=req.term.strip(),
            state=req.state,
            concept_uri=req.concept_uri,
        )
        term_hash = sha256_jcs({"term": spec.term.lower()})[:8]
        rev_key = f"MEM-{term_hash}"
        rev_id = f"rev-mem-{term_hash}"
        self.state_manager.save_revision(
            clean_pid,
            revision_id=rev_id,
            rev_kind="concept_memory",
            rev_key=rev_key,
            parent_rev_id=None,
            payload=spec,
            metadata=RevisionMetadata(actor="human", reason=f"concept_memory:{spec.state}"),
        )
        return spec

    def trust_workspace_path(self, path: Path | str | None = None) -> WorkspaceSettings:
        """Record the SHA-256 digest of workspace .opendv/agents/*/SKILL.md overrides."""
        ws = Path(path if path is not None else self.workspace_root).expanduser().resolve()
        if not ws.is_dir():
            raise OpenDVError(
                f"Workspace directory '{ws}' does not exist.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        digest = compute_workspace_agents_sha256(ws) or ""
        settings = self.settings_store.load_settings()
        kept = [
            spec
            for spec in settings.trusted_workspaces
            if Path(spec.workspace_root).resolve() != ws
        ]
        kept.append(
            TrustedWorkspaceSpec(
                workspace_root=str(ws),
                agent_pack_sha256=digest,
                trusted_at=datetime.now(tz=UTC).isoformat(),
            )
        )
        return self.settings_store.save_settings(
            settings.model_copy(update={"trusted_workspaces": kept})
        )


__all__ = ["ProjectsService"]
