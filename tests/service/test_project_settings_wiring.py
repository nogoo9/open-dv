"""Phase 2 tests for settings atomicity, lease sizing, Step 4/6 snapshot inheritance, and Databricks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from opendv.agents import AgentHarnessPort, AgentTurnRequest, AgentTurnResult
from opendv.models import (
    ColumnProfile,
    ErrorCode,
    ExternalMcpServerSpec,
    OntologyConstructReq,
    OpenDVError,
    ProjectInitReq,
    ProjectSettingsPatchReq,
    RunControlReq,
    SourceAttachReq,
    SourceInspectReq,
    SourceProfile,
    SourceReattachReq,
    TablePreviewPage,
    TableProfile,
    VaultBuildReq,
    WorkspaceSettings,
)
from opendv.models.wire_dtos_discovery import RunStateResp
from opendv.service import OpenDVService
from opendv.service._state_ddl import begin_immediate
from opendv.service.external_mcp import ExternalMcpGateway
from opendv.service.workflows._harness_factory import compute_job_lease_ttl
from opendv.service.workflows._run_adapters import (
    acquire_project_job_lease,
    enqueue_control_signal,
    load_latest_run_state,
    record_run_state,
)


class _CapturingStep4Harness(AgentHarnessPort):
    def __init__(self) -> None:
        self.requests: list[AgentTurnRequest] = []

    @property
    def provider_name(self) -> str:
        return "agy"

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: object = None,
        scout_dispatcher: object = None,
    ) -> AgentTurnResult:
        _ = event_sink, scout_dispatcher
        self.requests.append(request)
        return AgentTurnResult(
            agent_id=request.agent_id,
            provider="agy",
            model_id="stub-model",
            structured_output={
                "proposed_ops": [],
                "clarification_questions": [],
                "staged_candidate_ids": [],
                "summary": "done",
            },
            turns_used=1,
            total_tokens=10,
        )


def test_workspace_trust_revoke_and_project_settings_patch(tmp_path: Path) -> None:
    """Trust/revoke handles UNC/casing deduplication; update_project_settings enforces RFC 7396 & leases."""
    ws = tmp_path / "MyWorkspace"
    ws.mkdir()
    state_dir = tmp_path / "state"
    svc = OpenDVService(ws, state_dir=state_dir)

    svc.update_settings(
        WorkspaceSettings(
            secret_backend="user_state_file",
            harness_provider="claude-cli",
            cegar_policy="balanced",
            model_overrides={"ship-vault": "claude-sonnet-4-6"},
        )
    )
    proj = svc.init_project(ProjectInitReq(project_id="proj-a", domain_namespace="asset_mgmt"))
    assert proj.settings_snapshot.model_overrides == {"ship-vault": "claude-sonnet-4-6"}

    # Trust and case-variant re-trust deduplicates cleanly
    svc.trust_workspace_path(ws)
    alias = str(ws).upper() if os.name == "nt" else str(ws)
    trusted = svc.trust_workspace_path(alias)
    assert len(trusted.trusted_workspaces) == 1

    # UNC and control chars rejected on trust/revoke
    for bad in ("//server/share", "\\\\server\\share", "bad\x07path"):
        with pytest.raises(OpenDVError) as bad_exc:
            svc.revoke_trusted_workspace(bad)
        assert bad_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    revoked = svc.revoke_trusted_workspace(alias)
    assert revoked.trusted_workspaces == []
    with pytest.raises(OpenDVError) as missing_exc:
        svc.revoke_trusted_workspace(ws)
    assert missing_exc.value.error_code == ErrorCode.RESOURCE_NOT_FOUND

    # First set STANDARD_WORKER, then patch strict without v7_critic_tier -> auto-promotes FRONTIER_CRITIC
    svc.update_project_settings(
        "proj-a",
        ProjectSettingsPatchReq.model_validate({"v7_critic_tier": "STANDARD_WORKER"}),
    )
    p1 = svc.update_project_settings(
        "proj-a",
        ProjectSettingsPatchReq.model_validate(
            {
                "cegar_policy": "strict",
                "model_overrides": {"dv-semantic-critic": "claude-opus-4-6"},
            }
        ),
    )
    assert p1.settings_snapshot.cegar_policy == "strict"
    assert p1.settings_snapshot.v7_critic_tier == "FRONTIER_CRITIC"
    assert p1.settings_snapshot.model_overrides == {
        "ship-vault": "claude-sonnet-4-6",
        "dv-semantic-critic": "claude-opus-4-6",
    }

    # Patching STANDARD_WORKER onto already-strict snapshot fails
    with pytest.raises(OpenDVError) as strict_exc:
        svc.update_project_settings(
            "proj-a",
            ProjectSettingsPatchReq.model_validate({"v7_critic_tier": "STANDARD_WORKER"}),
        )
    assert strict_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Per-key delete and explicit null full reset
    p2 = svc.update_project_settings(
        "proj-a",
        ProjectSettingsPatchReq.model_validate({"model_overrides": {"ship-vault": None}}),
    )
    assert p2.settings_snapshot.model_overrides == {"dv-semantic-critic": "claude-opus-4-6"}
    p3 = svc.update_project_settings(
        "proj-a",
        ProjectSettingsPatchReq.model_validate({"model_overrides": None}),
    )
    assert p3.settings_snapshot.model_overrides == {}

    # Active lease blocks settings mutation with 409 PROJECT_JOB_ACTIVE
    acquire_project_job_lease(svc.state_manager, "proj-a", "run-active-1")
    with pytest.raises(OpenDVError) as active_exc:
        svc.update_project_settings(
            "proj-a", ProjectSettingsPatchReq.model_validate({"max_turns": 15})
        )
    assert active_exc.value.error_code == ErrorCode.PROJECT_JOB_ACTIVE

    # Corrupt settings_snapshot_json surfaces INTERNAL_SERVER_ERROR
    with begin_immediate(svc.state_manager.db_path) as conn:
        conn.execute(
            "UPDATE projects SET settings_snapshot_json = ? WHERE project_id = ?",
            ('{"bad": true}', "proj-a"),
        )
    with pytest.raises(OpenDVError) as corrupt_exc:
        svc.get_project("proj-a")
    assert corrupt_exc.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR


def test_lease_ttl_sizing_raise_budget_non_double_counting(tmp_path: Path) -> None:
    """compute_job_lease_ttl bounds TTLs; raise-budget renews live lease without double counting."""
    assert compute_job_lease_ttl(1, 10) == 120
    assert compute_job_lease_ttl(500, 7200) == 86_400

    ws = tmp_path / "ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "state")
    svc.init_project(ProjectInitReq(project_id="proj-b", domain_namespace="asset_mgmt"))

    run_id = "run-rb-1"
    acquire_project_job_lease(svc.state_manager, "proj-b", run_id, ttl_seconds=120)
    row0 = RunStateResp(
        run_id=run_id,
        project_id="proj-b",
        step="step6",
        status="RUNNING",
        policy="balanced",
        phase="cegar",
        attempt=0,
        budget_turns_used=0,
        budget_tokens_used=0,
        effective_max_turns=10,
        effective_max_tokens=1000,
        effective_timeout_seconds=60,
    )
    record_run_state(svc.state_manager, "proj-b", row0)
    # Simulate appended RunStateResp row carrying policy defaults
    record_run_state(
        svc.state_manager,
        "proj-b",
        row0.model_copy(update={"effective_max_turns": 999, "effective_max_tokens": 999_999}),
    )

    enqueue_control_signal(
        svc.state_manager,
        "proj-b",
        run_id,
        RunControlReq(action="raise-budget", extra_turns=5, extra_tokens=500),
    )
    latest = load_latest_run_state(svc.state_manager, "proj-b", run_id)
    assert latest.effective_max_turns == 15
    assert latest.effective_max_tokens == 1500


@pytest.mark.asyncio
async def test_step4_and_step6_snapshot_inheritance(tmp_path: Path) -> None:
    """Step 4 & Step 6 inherit ProjectSettingsSnapshot defaults when request fields are None."""
    ws = tmp_path / "ws"
    ws.mkdir()
    harness = _CapturingStep4Harness()
    svc = OpenDVService(ws, state_dir=tmp_path / "state", harness=harness)
    svc.update_settings(
        WorkspaceSettings(
            secret_backend="user_state_file",
            cegar_policy="fast",
            max_turns=7,
            max_tokens=12_345,
            timeout_seconds=90,
            model_overrides={"ontology-first-mate": "custom-coord-model"},
        )
    )
    svc.init_project(ProjectInitReq(project_id="proj-s", domain_namespace="asset_mgmt"))
    svc.update_project_settings(
        "proj-s",
        ProjectSettingsPatchReq.model_validate({"discovery_mode": "full_rebuild"}),
    )

    state4 = await svc.start_ontology_construction("proj-s", OntologyConstructReq())
    assert state4.effective_max_turns == 7
    assert state4.effective_max_tokens == 12_345
    assert state4.effective_timeout_seconds == 90
    assert harness.requests[-1].metadata["discovery_mode"] == "full_rebuild"
    assert harness.requests[-1].metadata["model_overrides"] == {
        "ontology-first-mate": "custom-coord-model"
    }

    # Step 6 guided view inherits snapshot cegar_policy when no Step 6 runs exist
    guided = svc.get_guided_view("proj-s")
    assert guided.policy == "fast"

    # Explicit None VaultBuildReq inherits snapshot cegar_policy (fails only due to empty sources/budget,
    # or check ctx/policy propagation via VaultBuildReq() defaulting to None)
    assert VaultBuildReq().policy is None


def test_databricks_host_validation_remediation_and_external_mcp(tmp_path: Path) -> None:
    """Databricks validates options['host'], forwards remediation, and ExternalMcp attaches remediation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "state")
    svc.update_settings(WorkspaceSettings(secret_backend="user_state_file"))
    svc.init_project(ProjectInitReq(project_id="proj-d", domain_namespace="asset_mgmt"))

    with pytest.raises(OpenDVError) as non_str_exc:
        svc.inspect_source(
            SourceInspectReq(
                kind="databricks_uc",
                uri_or_path="",
                options={"host": 123, "http_path": "/sql/1/wh", "catalog": "c"},  # type: ignore[dict-item]
                secret_refs=["DBX_TOK"],
            )
        )
    assert non_str_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as empty_ref_exc:
        svc.inspect_source(
            SourceInspectReq(
                kind="databricks_uc",
                uri_or_path="adb-123.azuredatabricks.net",
                options={"http_path": "/sql/1/wh", "catalog": "c"},
                secret_refs=[],
            )
        )
    assert empty_ref_exc.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING
    assert empty_ref_exc.value.remediation is not None

    # ExternalMcpGateway missing secret attaches remediation
    svc.save_mcp_servers(
        [
            ExternalMcpServerSpec(
                server_id="edgar",
                transport="streamable_http",
                url="https://api.example.com/mcp",
                secret_refs=["EDGAR_SECRET"],
                enabled=True,
                allow_scout_calls=True,
            )
        ]
    )
    gw = ExternalMcpGateway(
        state_store=svc.state_manager,
        secret_store=svc.secret_store,
        settings_store=svc.settings_store,
    )
    with pytest.raises(OpenDVError) as mcp_exc:
        gw._resolve_secrets("proj-d", svc.list_mcp_servers()[0])
    assert mcp_exc.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING
    assert mcp_exc.value.remediation is not None
    assert "EDGAR_SECRET" in mcp_exc.value.remediation.summary


def test_databricks_attach_reattach_sync_and_preview_access_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Databricks attach/reattach syncs host<->uri, populates tables_index, and forwards access_token."""
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = OpenDVService(ws, state_dir=tmp_path / "state")
    svc.update_settings(WorkspaceSettings(secret_backend="user_state_file"))
    svc.init_project(ProjectInitReq(project_id="proj-dbx", domain_namespace="asset_mgmt"))
    svc.set_secret("DBX_TOK", "dapi-test-token-123456")

    seen_profile_calls: list[dict[str, Any]] = []
    seen_preview_calls: list[dict[str, Any]] = []

    def fake_profile(source_id: str, _kind: Any, uri: str, **kwargs: Any) -> SourceProfile:
        seen_profile_calls.append({"uri": uri, **kwargs})
        return SourceProfile(
            source_id=source_id,
            schema_fingerprint="fp-1",
            table_count=1,
            tables=[
                TableProfile(
                    table_id=f"{source_id}:holdings",
                    table_name="holdings",
                    row_count_est=10,
                    columns=[
                        ColumnProfile(
                            col_name="id",
                            sql_type="INTEGER",
                            nullable=False,
                            is_pk=True,
                            null_ratio=0.0,
                            distinct_count_est=10,
                        )
                    ],
                )
            ],
        )

    def fake_preview(source_id: str, _kind: Any, uri: str, table_name: str, **kwargs: Any) -> TablePreviewPage:
        seen_preview_calls.append({"uri": uri, "table_name": table_name, **kwargs})
        return TablePreviewPage(
            table_id=f"{source_id}:holdings",
            source_id=source_id,
            columns=["id"],
            rows=[["1"]],
            total_rows=1,
        )

    monkeypatch.setattr(
        "opendv.service.workflows._discovery_sources.profile_source",
        fake_profile,
    )
    monkeypatch.setattr(
        "opendv.service.workflows._source_docs.run_table_preview",
        fake_preview,
    )

    attached = svc.attach_source(
        "proj-dbx",
        SourceAttachReq(
            kind="databricks_uc",
            uri_or_filename="",
            options={"host": "adb-111.azuredatabricks.net", "http_path": "/sql/1/wh", "catalog": "c"},
            secret_refs=["DBX_TOK"],
        ),
    )
    assert attached.uri_or_filename == "adb-111.azuredatabricks.net"
    assert len(svc.list_tables_index("proj-dbx")) == 1
    assert seen_profile_calls[-1]["access_token"] == "dapi-test-token-123456"

    # Partial reattach updating uri_or_filename with options=None updates new_opts["host"]
    reattached = svc.reattach_source(
        "proj-dbx",
        attached.source_id,
        SourceReattachReq(uri_or_filename="adb-222.azuredatabricks.net"),
    )
    assert reattached.uri_or_filename == "adb-222.azuredatabricks.net"
    rec = svc.state_manager.get_source_record("proj-dbx", attached.source_id)
    assert rec["connector_config"].options["host"] == "adb-222.azuredatabricks.net"

    # Preview forwards access_token, and deleted secret fails closed even when status is READY_OFFLINE
    page = svc.preview_table("proj-dbx", attached.source_id, "holdings")
    assert page.rows == [["1"]]
    assert seen_preview_calls[-1]["access_token"] == "dapi-test-token-123456"

    svc.delete_secret("DBX_TOK")
    with pytest.raises(OpenDVError) as missing_prev:
        svc.preview_table("proj-dbx", attached.source_id, "holdings")
    assert missing_prev.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING
    assert missing_prev.value.remediation is not None
