"""Phase 2 tests for HarnessReadinessService, ReadinessRecordingHarness, remediation, and doctor."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from opendv.agents import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    Tier2ProbeOutcome,
)
from opendv.models import (
    ErrorCode,
    ExternalMcpServerSpec,
    HarnessProvider,
    OpenDVError,
    WorkspaceSettings,
)
from opendv.service import HarnessService, OpenDVService, SettingsService, scoped_env_first
from opendv.service._remediation import build_remediation
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._harness_factory import resolve_harness
from tests.helpers.secret_backends import InMemoryKeyringBackend, OwnerAclStateHelper


class _RecordingStubHarness(AgentHarnessPort):
    def __init__(
        self,
        provider: HarnessProvider,
        *,
        error: OpenDVError | None = None,
    ) -> None:
        self._provider = provider
        self._error = error

    @property
    def provider_name(self) -> HarnessProvider:
        return self._provider

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: Any = None,
        scout_dispatcher: Any = None,
    ) -> AgentTurnResult:
        _ = event_sink, scout_dispatcher
        if self._error is not None:
            raise self._error
        return AgentTurnResult(
            agent_id=request.agent_id,
            provider=self._provider,
            model_id="stub-model",
            text_output="ok",
        )


def test_build_remediation_total_coverage_and_switch_order() -> None:
    """build_remediation handles all 34 ErrorCodes and deterministic switch-provider order."""
    for code in ErrorCode:
        rem = build_remediation(code)
        assert rem.summary

    statuses: dict[HarnessProvider, str] = {
        "agy": "AUTH_FAILED",
        "claude-cli": "MISSING_PREREQ",
        "claude": "CONFIGURED_UNVERIFIED",
        "gemini": "VERIFIED",
    }
    rem_agy = build_remediation(
        ErrorCode.AGENT_NOT_CONFIGURED,
        provider="agy",
        provider_statuses=statuses,
        credential_source="cli_login",
    )
    assert rem_agy.actions[0].command == "agy"
    assert rem_agy.actions[1].command == "uv run opendv settings set --harness-provider claude"

    rem_cli_store = build_remediation(
        ErrorCode.AGENT_NOT_CONFIGURED,
        provider="claude-cli",
        provider_statuses=statuses,
        credential_source="store",
    )
    assert rem_cli_store.actions[0].command == "uv run opendv secrets delete ANTHROPIC_API_KEY"
    assert rem_cli_store.actions[1].command == "claude auth login"

    rem_cli_env = build_remediation(
        ErrorCode.AGENT_NOT_CONFIGURED,
        provider="claude-cli",
        provider_statuses=statuses,
        credential_source="os_env",
    )
    assert rem_cli_env.actions[0].command is None
    assert "unset" in rem_cli_env.actions[0].label.lower()

    rem_sdk = build_remediation(
        ErrorCode.DRIVER_MISSING,
        provider="claude",
        provider_statuses=statuses,
    )
    assert rem_sdk.actions[0].command == "uv sync --extra sdk"

    rem_dbx = build_remediation(ErrorCode.DRIVER_MISSING, provider=None)
    assert rem_dbx.actions[0].command == "uv sync --extra databricks"

    rem_trust = build_remediation(
        ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE,
        workspace_root="/tmp/ws",
    )
    assert rem_trust.actions[0].command == "uv run opendv settings trust-workspace --path /tmp/ws"

    rem_busy = build_remediation(ErrorCode.PROJECT_JOB_ACTIVE, active_run_id="run-123")
    assert "run-123" in rem_busy.summary and rem_busy.actions == []


@pytest.mark.asyncio
async def test_readiness_tier1_tier2_cache_clearing_and_single_flight(tmp_path: Path) -> None:
    """HarnessReadinessService evaluates Tier-1, runs single-flight probes, and isolates overrides."""
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    acl = OwnerAclStateHelper(state_dir)

    settings_store = SettingsStore(user_state_dir=state_dir)
    settings_store.save_settings(
        WorkspaceSettings(secret_backend="keyring", harness_provider="claude")
    )
    kr = InMemoryKeyringBackend()
    env: dict[str, str] = {}
    secret_store = KeyringSecretStore(
        ws,
        state_dir=state_dir,
        settings_store=settings_store,
        keyring_backend=kr,
        environ=env,
    )
    state_mgr = ProjectStateManager(ws)
    state_mgr.initialize()

    probe_gate = asyncio.Event()
    probe_started = asyncio.Event()
    next_outcome = Tier2ProbeOutcome(kind="verified")

    async def fake_probe(
        prov: HarnessProvider,
        _bin: str | None,
        _key: str | None,
        _env: Any,
    ) -> Tier2ProbeOutcome:
        if prov == "claude":
            probe_started.set()
            await probe_gate.wait()
        return next_outcome

    readiness = HarnessReadinessService(
        settings_store=settings_store,
        secret_store=secret_store,
        db_path=state_mgr.db_path,
        which_fn=lambda cmd: f"/bin/{cmd}" if cmd == "claude" else None,
        find_spec_fn=lambda name: object() if name == "anthropic" else None,
        probe_fn=fake_probe,
    )

    # Tier-1: gemini missing both SDK and key -> DRIVER_MISSING remediation first
    initial = {r.provider: r for r in readiness.get_readiness().providers}
    assert initial["agy"].status == "MISSING_PREREQ"
    assert initial["claude-cli"].status == "CONFIGURED_UNVERIFIED"
    assert initial["claude-cli"].credential_source == "cli_login"
    assert initial["claude"].status == "MISSING_PREREQ"
    assert initial["gemini"].status == "MISSING_PREREQ"
    assert initial["gemini"].remediation is not None
    assert initial["gemini"].remediation.actions[0].command == "uv sync --extra sdk"

    # MISSING_PREREQ probe returns 200 record with zero disk writes
    missing_rec = await readiness.test_agent_readiness("claude")
    assert missing_rec.status == "MISSING_PREREQ"
    assert not acl.readiness_path.exists()

    # Bind key -> CONFIGURED_UNVERIFIED; test concurrent single-flight probe -> second gets PROJECT_JOB_ACTIVE
    secret_store.set_secret("ANTHROPIC_API_KEY", "sk-ant-valid-1234567890")
    t1 = asyncio.create_task(readiness.test_agent_readiness("claude"))
    await probe_started.wait()
    with pytest.raises(OpenDVError) as busy_exc:
        await readiness.test_agent_readiness("claude")
    assert busy_exc.value.error_code == ErrorCode.PROJECT_JOB_ACTIVE
    assert busy_exc.value.remediation is not None
    probe_gate.set()
    verified_rec = await t1
    assert verified_rec.status == "VERIFIED"
    assert acl.verify_owner_only(acl.readiness_path) is True

    # Verify minimal 4-field readiness.json schema
    raw_cache = json.loads(acl.readiness_path.read_text(encoding="utf-8"))
    assert set(raw_cache["providers"]["claude"].keys()) == {
        "status",
        "last_verified_at",
        "last_auth_error_at",
        "last_auth_error_summary",
    }

    # Transient error never overwrites VERIFIED cache
    next_outcome = Tier2ProbeOutcome(kind="transient_error", error_summary="429 rate limited")
    probe_gate.set()
    trans_rec = await readiness.test_agent_readiness("claude")
    assert trans_rec.status == "VERIFIED"
    assert trans_rec.last_auth_error_summary == "429 rate limited"

    # --env-first override skips disk persistence while still returning fresh outcome
    next_outcome = Tier2ProbeOutcome(
        kind="auth_failed",
        error_summary="401 invalid sk-ant-secret1234567890",
    )
    with scoped_env_first(True):
        assert readiness.should_record_to_disk() is False
        ephemeral = await readiness.test_agent_readiness("claude")
        assert ephemeral.status == "AUTH_FAILED"
        assert "sk-ant-secret" not in (ephemeral.last_auth_error_summary or "")
        assert ephemeral.remediation is not None
    assert readiness.get_provider_statuses()["claude"] == "VERIFIED"

    # Corrupt readiness.json falls back cleanly to CONFIGURED_UNVERIFIED with zero writes
    acl.readiness_path.write_text("{corrupt", encoding="utf-8")
    assert readiness.get_provider_statuses()["claude"] == "CONFIGURED_UNVERIFIED"


@pytest.mark.asyncio
async def test_passive_wrapper_secret_invalidation_and_doctor(tmp_path: Path) -> None:
    """ReadinessRecordingHarness, OpenDVService invalidation, and run_doctor work end-to-end."""
    ws = tmp_path / "ws"
    ws.mkdir()
    override_dir = ws / ".opendv" / "agents" / "ontology-first-mate"
    override_dir.mkdir(parents=True)
    (override_dir / "SKILL.md").write_text("# untrusted override", encoding="utf-8")

    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)
    settings_store.save_settings(
        WorkspaceSettings(secret_backend="user_state_file", harness_provider="claude")
    )
    settings_store.save_mcp_servers(
        [
            ExternalMcpServerSpec(
                server_id="fibo",
                transport="streamable_http",
                url="https://mcp.example.com/mcp",
                secret_refs=["MCP_TOKEN"],
                enabled=True,
                allow_scout_calls=True,
            )
        ]
    )
    secret_store = KeyringSecretStore(ws, state_dir=state_dir, settings_store=settings_store, environ={})
    state_mgr = ProjectStateManager(ws)
    state_mgr.initialize()

    readiness = HarnessReadinessService(
        settings_store=settings_store,
        secret_store=secret_store,
        db_path=state_mgr.db_path,
        which_fn=lambda cmd: f"/bin/{cmd}" if cmd == "claude" else None,
        find_spec_fn=lambda name: object() if name == "anthropic" else None,
    )
    svc = OpenDVService(ws, state_dir=state_dir, readiness_service=readiness)
    svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-1234567890abcdef")

    # Passive ReadinessRecordingHarness stamps VERIFIED on success, AUTH_FAILED only on AGENT_NOT_CONFIGURED
    wrapped_ok = resolve_harness(
        svc.get_settings(),
        svc.secret_store,
        readiness_service=readiness,
        which_fn=lambda _c: None,
        find_spec_fn=lambda name: object() if name == "anthropic" else None,
    )
    # Replace inner runner with recording stub (keeping ReadinessRecordingHarness wrapper)
    wrapped_ok._inner = _RecordingStubHarness("claude")  # type: ignore[attr-defined]
    await wrapped_ok.run_turn(
        AgentTurnRequest(run_id="r-pass", agent_id="ship-vault", step="step6", prompt="hi")
    )
    assert readiness.get_provider_statuses()["claude"] == "VERIFIED"

    # DRIVER_MISSING does NOT persist AUTH_FAILED
    wrapped_ok._inner = _RecordingStubHarness(  # type: ignore[attr-defined]
        "claude",
        error=OpenDVError("missing sdk", error_code=ErrorCode.DRIVER_MISSING),
    )
    with pytest.raises(OpenDVError) as drv_exc:
        await wrapped_ok.run_turn(
            AgentTurnRequest(run_id="r-drv", agent_id="ship-vault", step="step6", prompt="hi")
        )
    assert drv_exc.value.remediation is not None
    assert readiness.get_provider_statuses()["claude"] == "VERIFIED"

    # AGENT_NOT_CONFIGURED persists AUTH_FAILED and attaches remediation
    wrapped_ok._inner = _RecordingStubHarness(  # type: ignore[attr-defined]
        "claude",
        error=OpenDVError("bad auth sk-ant-secret1234567890", error_code=ErrorCode.AGENT_NOT_CONFIGURED),
    )
    with pytest.raises(OpenDVError) as auth_exc:
        await wrapped_ok.run_turn(
            AgentTurnRequest(run_id="r-auth", agent_id="ship-vault", step="step6", prompt="hi")
        )
    assert auth_exc.value.remediation is not None
    assert readiness.get_provider_statuses()["claude"] == "AUTH_FAILED"

    # run_doctor handles untrusted workspace overrides & missing MCP secrets without throwing
    doc = svc.run_doctor()
    assert doc.agents is not None
    assert doc.agents["workspace_overrides_trusted"] is False
    assert doc.mcp_servers is not None and doc.mcp_servers[0]["ready"] is False
    assert any(
        "trust-workspace" in (a.command or "")
        for rem in doc.remediation
        for a in rem.actions
    )
    assert any(
        "MCP_TOKEN" in (a.command or "")
        for rem in doc.remediation
        for a in rem.actions
    )

    # Updating ANTHROPIC_API_KEY clears claude cache back to CONFIGURED_UNVERIFIED
    svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-rotated-1234567890")
    assert readiness.get_provider_statuses()["claude"] == "CONFIGURED_UNVERIFIED"


@pytest.mark.asyncio
async def test_standalone_settings_and_harness_components(tmp_path: Path) -> None:
    """SettingsService and HarnessService work standalone and compose cleanly into OpenDVService."""
    ws = tmp_path / "ws"
    ws.mkdir()
    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)
    secret_store = KeyringSecretStore(ws, state_dir=state_dir, settings_store=settings_store, environ={})

    harnesses = HarnessService(
        ws,
        settings_store,
        secret_store,
        readiness_service=HarnessReadinessService(
            settings_store=settings_store,
            secret_store=secret_store,
            db_path=ws / ".opendv" / "project.sqlite",
            which_fn=lambda cmd: f"/bin/{cmd}" if cmd == "claude" else None,
            find_spec_fn=lambda name: object() if name == "anthropic" else None,
        ),
    )
    settings_svc = SettingsService(
        ws,
        settings_store,
        secret_store,
        on_secret_changed=harnesses.clear_for_secret,
        on_secret_policy_changed=harnesses.clear_all,
    )

    assert len(harnesses.list_models("gemini")) == 2
    assert len(harnesses.list_packs()) > 0
    settings_svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-1234567890abcdef")
    harnesses.readiness.record_turn_success("claude")
    assert harnesses.get_provider_statuses()["claude"] == "VERIFIED"

    # Secret update triggers HarnessService invalidation callback without OpenDVService
    settings_svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-rotated-1234567890")
    assert harnesses.get_provider_statuses()["claude"] == "CONFIGURED_UNVERIFIED"

    # SettingsService manages workspace trust, revocation, and MCP registry without ProjectStateManager
    trusted = settings_svc.trust_workspace_path(ws)
    assert len(trusted.trusted_workspaces) == 1
    revoked = settings_svc.revoke_trusted_workspace(ws)
    assert revoked.trusted_workspaces == []
    mcp_spec = ExternalMcpServerSpec(
        server_id="fibo",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        enabled=True,
    )
    settings_svc.upsert_mcp_server(mcp_spec)
    doc = harnesses.run_doctor()
    assert doc.mcp_servers is not None and doc.mcp_servers[0]["ready"] is True
    settings_svc.delete_mcp_server("fibo")
    assert settings_svc.list_mcp_servers() == []

    # OpenDVService composes both components cleanly
    full_svc = OpenDVService(ws, state_dir=state_dir)
    assert isinstance(full_svc.harnesses, HarnessService)
    assert isinstance(full_svc.settings, SettingsService)
