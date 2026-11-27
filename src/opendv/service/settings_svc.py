"""Standalone workspace settings, project snapshot, secret, and MCP registry component."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv.agents import compute_workspace_agents_sha256
from opendv.modeling.prompts import MAX_BUSINESS_CONTEXT_HINTS_TOKENS
from opendv.modeling.token_utils import count_tokens
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import VALID_V6B_RULE_IDS
from opendv.models.project import (
    ProjectSettingsPatchReq,
    ProjectSettingsSnapshot,
    ProjectState,
    TrustedWorkspaceSpec,
    WorkspaceSettings,
)
from opendv.models.sources import ExternalMcpServerSpec
from opendv.models.wire_dtos_discovery import SecretSummary
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    append_audit_log,
    validate_project_id_or_error,
)


def _validate_workspace_path_input(path: Path | str) -> str:
    raw = os.fspath(path)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise OpenDVError(
            "Workspace path must not contain control characters.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    cleaned = raw.strip()
    if not cleaned:
        raise OpenDVError(
            "Workspace path must not be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if cleaned.replace("\\", "/").startswith("//"):
        raise OpenDVError(
            f"UNC or device workspace paths are forbidden: '{cleaned}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return cleaned


def _normalize_local_workspace_path(path: Path | str) -> str:
    raw = os.fspath(path).strip()
    if raw.replace("\\", "/").startswith("//"):
        return os.path.normcase(raw)
    expanded = os.path.expanduser(raw)
    try:
        return os.path.normcase(os.path.realpath(expanded))
    except OSError:
        return os.path.normcase(expanded)


class SettingsService:
    """Layer-4 component managing workspace/project settings, secrets, MCP, and workspace trust."""

    def __init__(
        self,
        workspace_root: Path | str,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
        state_manager: ProjectStateManager | None = None,
        *,
        on_secret_changed: Callable[[str], None] | None = None,
        on_secret_policy_changed: Callable[[], None] | None = None,
    ) -> None:
        self.workspace_root: Path = Path(workspace_root).resolve()
        self.settings_store = settings_store
        self.secret_store = secret_store
        self.state_manager = state_manager
        self._on_secret_changed = on_secret_changed
        self._on_secret_policy_changed = on_secret_policy_changed

    def get_settings(self) -> WorkspaceSettings:
        """Load non-secret WorkspaceSettings."""
        return self.settings_store.load_settings()

    def update_settings(self, settings: WorkspaceSettings) -> WorkspaceSettings:
        """Persist validated WorkspaceSettings without secret literals."""
        prior, saved = self.settings_store.update_settings(lambda _prior: settings)
        if (
            prior.secret_precedence != saved.secret_precedence
            or prior.secret_backend != saved.secret_backend
        ) and self._on_secret_policy_changed is not None:
            self._on_secret_policy_changed()
        return saved

    def list_secrets(
        self, keys: Sequence[str] = (), *, include_stored: bool = True
    ) -> list[SecretSummary]:
        """List masked presence summaries for stored/known secret keys."""
        return self.secret_store.list_secret_summaries(keys, include_stored=include_stored)

    def set_secret(self, key: str, value: str) -> SecretSummary:
        """Write a user-global secret to OS keyring or user_state_file."""
        summary = self.secret_store.set_secret(key, value)
        if self._on_secret_changed is not None:
            self._on_secret_changed(summary.key)
        return summary

    def delete_secret(self, key: str) -> None:
        """Remove a secret from user-global storage."""
        self.secret_store.delete_secret(key)
        if self._on_secret_changed is not None:
            self._on_secret_changed(key)

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

    def trust_workspace_path(self, path: Path | str | None = None) -> WorkspaceSettings:
        """Record the SHA-256 digest of workspace .opendv/agents/*/SKILL.md overrides."""
        cleaned = _validate_workspace_path_input(path if path is not None else self.workspace_root)
        ws = Path(cleaned).expanduser().resolve()
        if not ws.is_dir():
            raise OpenDVError(
                f"Workspace directory '{ws}' does not exist.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        digest = compute_workspace_agents_sha256(ws) or ""
        norm_target = _normalize_local_workspace_path(ws)
        now_iso = datetime.now(tz=UTC).isoformat()

        def _mut(settings: WorkspaceSettings) -> WorkspaceSettings:
            kept = [
                spec
                for spec in settings.trusted_workspaces
                if _normalize_local_workspace_path(spec.workspace_root) != norm_target
            ]
            kept.append(
                TrustedWorkspaceSpec(
                    workspace_root=str(ws),
                    agent_pack_sha256=digest,
                    trusted_at=now_iso,
                )
            )
            return settings.model_copy(update={"trusted_workspaces": kept})

        _, saved = self.settings_store.update_settings(_mut)
        return saved

    def revoke_trusted_workspace(self, path: Path | str) -> WorkspaceSettings:
        """Remove a trusted workspace entry matching exact or normalized local path."""
        cleaned = _validate_workspace_path_input(path)
        norm_target = _normalize_local_workspace_path(cleaned)

        def _mut(settings: WorkspaceSettings) -> WorkspaceSettings:
            kept = [
                spec
                for spec in settings.trusted_workspaces
                if spec.workspace_root != cleaned
                and _normalize_local_workspace_path(spec.workspace_root) != norm_target
            ]
            if len(kept) == len(settings.trusted_workspaces):
                raise OpenDVError(
                    f"Workspace '{cleaned}' is not registered in trusted_workspaces.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
            return settings.model_copy(update={"trusted_workspaces": kept})

        _, saved = self.settings_store.update_settings(_mut)
        return saved

    def update_project_settings(
        self,
        project_id: str,
        patch: ProjectSettingsPatchReq | ProjectSettingsSnapshot,
    ) -> ProjectState:
        """Atomically patch or replace ProjectSettingsSnapshot under active-lease guard."""
        return apply_project_settings_update(
            self.state_manager,
            project_id,
            patch,
            require_no_active_lease=True,
        )


def _validate_snapshot_governance(snap: ProjectSettingsSnapshot) -> None:
    if snap.cegar_policy == "strict" and snap.v7_critic_tier != "FRONTIER_CRITIC":
        raise OpenDVError(
            "v7_critic_tier must be FRONTIER_CRITIC when cegar_policy is strict.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if (
        snap.business_context_hints
        and count_tokens(snap.business_context_hints) > MAX_BUSINESS_CONTEXT_HINTS_TOKENS
    ):
        raise OpenDVError(
            f"business_context_hints exceeds {MAX_BUSINESS_CONTEXT_HINTS_TOKENS} "
            "cl100k_base tokens.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    dis = snap.modeling_preferences.disabled_v6b_blocks
    invalid_rules = [r for r in dis if r not in VALID_V6B_RULE_IDS]
    if invalid_rules:
        raise OpenDVError(
            f"Invalid disabled_v6b_blocks rule ID(s): {invalid_rules}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if snap.cegar_policy == "strict" and dis:
        raise OpenDVError(
            "disabled_v6b_blocks must be empty under strict CEGAR policy.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )


def apply_project_settings_update(
    state_manager: ProjectStateManager | None,
    project_id: str,
    patch: ProjectSettingsPatchReq | ProjectSettingsSnapshot,
    *,
    require_no_active_lease: bool = True,
) -> ProjectState:
    """Atomically patch or replace ProjectSettingsSnapshot with governance checks & audit log."""
    if state_manager is None:
        raise OpenDVError(
            "ProjectStateManager is required for project settings updates.",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )
    clean_pid = validate_project_id_or_error(project_id)

    def _mut(snap: ProjectSettingsSnapshot) -> ProjectSettingsSnapshot:
        if isinstance(patch, ProjectSettingsSnapshot):
            updated: ProjectSettingsSnapshot = patch
        else:
            data: dict[str, Any] = snap.model_dump(mode="python")
            patch_data = patch.model_dump(mode="python", exclude_unset=True)
            if "model_overrides" in patch.model_fields_set:
                ovr_patch = patch_data.pop("model_overrides")
                if ovr_patch is None:
                    data["model_overrides"] = {}
                else:
                    merged_ovr = dict(data.get("model_overrides") or {})
                    for k, v in ovr_patch.items():
                        if v is None:
                            merged_ovr.pop(k, None)
                        else:
                            merged_ovr[k] = v
                    data["model_overrides"] = merged_ovr
            data.update(patch_data)
            if (
                data.get("cegar_policy") == "strict"
                and "v7_critic_tier" not in patch.model_fields_set
            ):
                data["v7_critic_tier"] = "FRONTIER_CRITIC"
            try:
                updated = ProjectSettingsSnapshot.model_validate(data)
            except ValidationError as exc:
                raise OpenDVError(
                    f"Invalid project settings patch: {exc}",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc
        _validate_snapshot_governance(updated)
        return updated

    state = state_manager.update_project_pointers(
        clean_pid,
        require_no_active_lease=require_no_active_lease,
        snapshot_mutator=_mut,
    )
    snap = state.settings_snapshot
    append_audit_log(
        state_manager,
        clean_pid,
        event_type="PROJECT_SETTINGS_UPDATED",
        actor="human",
        step="step1",
        summary=f"Updated settings for project '{clean_pid}'.",
        details={
            "cegar_policy": snap.cegar_policy,
            "v7_critic_tier": snap.v7_critic_tier,
            "real_row_proving": snap.real_row_proving,
            "proving_sample_rows": snap.proving_sample_rows,
        },
    )
    return state


__all__ = ["SettingsService", "apply_project_settings_update"]
