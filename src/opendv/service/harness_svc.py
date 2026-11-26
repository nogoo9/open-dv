"""Standalone harness readiness, doctor diagnostics, model catalog, packs, and resolution."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opendv import __version__
from opendv.agents import (
    AgentHarnessPort,
    SubprocessStreamFn,
    default_model_options,
    list_agent_packs,
    load_agent_pack,
)
from opendv.models.errors import ErrorCode, ErrorRemediation, OpenDVError
from opendv.models.project import HarnessProvider, ProjectSettingsSnapshot, WorkspaceSettings
from opendv.models.wire_dtos_discovery import AgentPackDetail, AgentPackSummary
from opendv.models.wire_dtos_readiness import (
    DoctorResponse,
    ModelOption,
    ProviderReadinessRecord,
    ReadinessResponse,
)
from opendv.service._remediation import build_remediation
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.workflows._harness_factory import resolve_harness


class HarnessService:
    """Layer-4 component encapsulating harness readiness, models, packs, doctor, and resolution."""

    def __init__(
        self,
        workspace_root: Path | str,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
        readiness_service: HarnessReadinessService | None = None,
        *,
        db_path: Path | None = None,
    ) -> None:
        self.workspace_root: Path = Path(workspace_root).resolve()
        self.settings_store = settings_store
        self.secret_store = secret_store
        effective_db_path = (
            Path(db_path)
            if db_path is not None
            else self.workspace_root / ".opendv" / "project.sqlite"
        )
        self.readiness: HarnessReadinessService = readiness_service or HarnessReadinessService(
            settings_store=settings_store,
            secret_store=secret_store,
            db_path=effective_db_path,
        )

    def get_readiness(self) -> ReadinessResponse:
        """Evaluate fast Tier-1 prerequisites merged with persisted readiness cache."""
        return self.readiness.get_readiness()

    async def test_readiness(self, provider: HarnessProvider) -> ProviderReadinessRecord:
        """Execute bounded Tier-2 authentication verification probe for provider."""
        return await self.readiness.test_agent_readiness(provider)

    def get_provider_statuses(self) -> dict[HarnessProvider, str]:
        """Return {provider: status} map from current Tier-1 + cache evaluation."""
        return self.readiness.get_provider_statuses()

    def list_models(self, provider: HarnessProvider | None = None) -> list[ModelOption]:
        """Return offline default ModelOption suggestions for provider or active default."""
        target = (
            provider
            if provider is not None
            else self.settings_store.load_settings().harness_provider
        )
        return default_model_options(target)

    def clear_for_secret(self, key: str) -> None:
        """Clear cached verification state for providers bound to key."""
        self.readiness.clear_for_secret(key)

    def clear_all(self) -> None:
        """Clear all cached provider verification state."""
        self.readiness.clear_all()

    def list_packs(self) -> list[AgentPackSummary]:
        """List builtin and trusted workspace SKILL.md agent packs."""
        ws = self.settings_store.load_settings()
        return list_agent_packs(
            workspace_root=self.workspace_root,
            trusted_workspaces=ws.trusted_workspaces,
        )

    def get_pack(self, agent_id: str) -> AgentPackDetail:
        """Inspect a single SKILL.md agent pack."""
        ws = self.settings_store.load_settings()
        return load_agent_pack(
            agent_id,
            workspace_root=self.workspace_root,
            trusted_workspaces=ws.trusted_workspaces,
        )

    def run_doctor(
        self,
        *,
        check_agents: bool = False,
        check_mcp: bool = False,
    ) -> DoctorResponse:
        """Run synchronous read-only Tier-1 diagnostics across settings, agents, and MCP."""
        run_all = not check_agents and not check_mcp
        ws = self.settings_store.load_settings()
        readiness = self.get_readiness()
        remediations: list[ErrorRemediation] = []

        active_rec = next(
            (rec for rec in readiness.providers if rec.provider == ws.harness_provider),
            None,
        )
        if active_rec is not None and active_rec.remediation is not None:
            remediations.append(active_rec.remediation)

        agents_payload: dict[str, Any] | None = None
        if check_agents or run_all:
            agy_rec = next(r for r in readiness.providers if r.provider == "agy")
            claude_cli_rec = next(r for r in readiness.providers if r.provider == "claude-cli")
            provider_secrets = [
                s.model_dump(mode="json")
                for s in self.secret_store.list_secret_summaries(
                    ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"],
                    include_stored=False,
                )
            ]
            try:
                pack_count = len(self.list_packs())
                workspace_overrides_trusted = True
            except OpenDVError as exc:
                if exc.error_code != ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE:
                    raise
                pack_count = 0
                workspace_overrides_trusted = False
                remediations.append(
                    build_remediation(
                        ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE,
                        workspace_root=str(self.workspace_root),
                    )
                )
            agents_payload = {
                "binaries": {
                    "agy": agy_rec.binary_path,
                    "claude_cli": claude_cli_rec.binary_path,
                },
                "pack_count": pack_count,
                "workspace_overrides_trusted": workspace_overrides_trusted,
                "provider_secrets": provider_secrets,
            }

        mcp_payload: list[dict[str, Any]] | None = None
        if check_mcp or run_all:
            env_path = self.secret_store.effective_env().get("PATH", "")
            mcp_payload = []
            missing_mcp_keys: set[str] = set()
            for spec in self.settings_store.load_mcp_servers():
                secret_status = [
                    s.model_dump(mode="json")
                    for s in self.secret_store.list_secret_summaries(
                        spec.secret_refs, include_stored=False
                    )
                ]
                secrets_ok = all(bool(item["present"]) for item in secret_status)
                if spec.enabled and not secrets_ok:
                    missing_mcp_keys.update(
                        str(item["key"]) for item in secret_status if not item["present"]
                    )
                if spec.transport == "stdio":
                    cmd = spec.command[0] if spec.command else ""
                    found_cmd = shutil.which(cmd, path=env_path) if cmd else None
                    transport_ok = bool(cmd and (found_cmd is not None or Path(cmd).is_file()))
                else:
                    parsed_url = urlparse(spec.url or "")
                    transport_ok = bool(
                        parsed_url.scheme in ("http", "https") and parsed_url.netloc
                    )
                mcp_payload.append(
                    {
                        "server_id": spec.server_id,
                        "transport": spec.transport,
                        "enabled": spec.enabled,
                        "ready": bool(spec.enabled and secrets_ok and transport_ok),
                        "secret_status": secret_status,
                    }
                )
            if missing_mcp_keys:
                remediations.append(
                    build_remediation(
                        ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                        missing_secrets=sorted(missing_mcp_keys),
                    )
                )

        return DoctorResponse(
            status="ok",
            version=__version__,
            workspace_root=str(self.workspace_root),
            harness_provider=ws.harness_provider,
            cegar_policy=ws.cegar_policy,
            secret_precedence=ws.secret_precedence,
            agents=agents_payload,
            mcp_servers=mcp_payload,
            readiness=readiness,
            remediation=remediations,
        )

    def resolve_for_run(
        self,
        settings: WorkspaceSettings | ProjectSettingsSnapshot | None = None,
        *,
        injected_harness: AgentHarnessPort | None = None,
        model_overrides: Mapping[str, str] | None = None,
        stream_fn: SubprocessStreamFn | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        find_spec_fn: Callable[[str], object | None] | None = None,
    ) -> AgentHarnessPort:
        """Resolve an AgentHarnessPort wrapped with readiness recording."""
        return resolve_harness(
            settings or self.settings_store.load_settings(),
            self.secret_store,
            injected_harness=injected_harness,
            model_overrides=model_overrides,
            stream_fn=stream_fn,
            readiness_service=self.readiness,
            which_fn=which_fn,
            find_spec_fn=find_spec_fn,
        )


__all__ = ["HarnessService"]
