"""Phase 3 API tests for readiness, doctor, trust revoke, project settings, proxy guards, and vault policy."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from opendv.agents import ProbeFn, Tier2ProbeOutcome
from opendv.api import create_app
from opendv.models import (
    ClarificationQuestion,
    ErrorCode,
    ExternalMcpServerSpec,
    HarnessProvider,
    OntologyCommitReq,
    ProjectInitReq,
    SourceAttachReq,
    WorkspaceSettings,
)
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.service.app_service import OpenDVService
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    acquire_project_job_lease,
    release_project_job_lease,
)
from tests.api._api_test_helpers import (
    build_wave1_am_draft,
    build_wave1_commit_ops,
    copy_public_am_wave1,
    load_project_profiles,
    wait_run_via_sse,
)
from tests.helpers.mock_harness import DeterministicMockHarness, MockTurnScript


def _make_di_svc(
    tmp_path: Path,
    *,
    harness: DeterministicMockHarness | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    find_spec_fn: Callable[[str], object | None] | None = None,
    probe_fn: ProbeFn | None = None,
) -> tuple[OpenDVService, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)
    secret_store = KeyringSecretStore(ws, state_dir=state_dir, settings_store=settings_store)
    state_mgr = ProjectStateManager(ws)
    state_mgr.initialize()
    readiness = HarnessReadinessService(
        settings_store=settings_store,
        secret_store=secret_store,
        db_path=state_mgr.db_path,
        which_fn=which_fn or (lambda _: None),
        find_spec_fn=find_spec_fn or (lambda _: None),
        probe_fn=probe_fn,
    )
    svc = OpenDVService(
        ws,
        state_dir=state_dir,
        harness=harness,
        readiness_service=readiness,
    )
    return svc, state_dir


def _assert_problem(
    resp: httpx.Response,
    *,
    status: int,
    error_code: str,
) -> dict[str, Any]:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = resp.json()
    assert body["status"] == status
    assert body["error_code"] == error_code
    for req_key in ("type", "title", "detail", "category", "waivable", "rationale_required"):
        assert req_key in body
    return body


@pytest.mark.asyncio
async def test_readiness_routes_ordering_and_probe_outcomes(tmp_path: Path) -> None:
    probe_calls: list[HarnessProvider] = []
    outcome_box: dict[str, Tier2ProbeOutcome] = {
        "next": Tier2ProbeOutcome(kind="verified"),
    }

    async def _probe(
        provider: HarnessProvider,
        _bin: str | None,
        _key: str | None,
        _env: Mapping[str, str],
    ) -> Tier2ProbeOutcome:
        probe_calls.append(provider)
        return outcome_box["next"]

    svc, state_dir = _make_di_svc(
        tmp_path,
        which_fn=lambda name: f"/bin/{name}" if name == "claude" else None,
        find_spec_fn=lambda mod: object() if mod == "anthropic" else None,
        probe_fn=_probe,
    )
    svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-secret-xyz-123456")
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # Static route /agents/readiness must precede /agents/{agent_id}
        r_get = await client.get("/api/v1/agents/readiness")
        assert r_get.status_code == 200
        providers = {rec["provider"]: rec for rec in r_get.json()["providers"]}
        assert set(providers.keys()) == {"agy", "claude-cli", "claude", "gemini"}
        assert len(providers["claude"]["available_models"]) >= 2
        assert probe_calls == []

        # Dynamic /agents/{agent_id} still works for unknown IDs
        r_missing_pack = await client.get("/api/v1/agents/nonexistent-pack")
        _assert_problem(r_missing_pack, status=404, error_code=ErrorCode.RESOURCE_NOT_FOUND)

        # MISSING_PREREQ provider returns HTTP 200 with remediation and zero probe calls
        r_miss = await client.post("/api/v1/agents/readiness/test", json={"provider": "agy"})
        assert r_miss.status_code == 200
        miss_body = r_miss.json()
        assert miss_body["status"] == "MISSING_PREREQ"
        assert miss_body["remediation"] is not None
        assert probe_calls == []
        assert not (state_dir / "readiness.json").exists()

        # Verified outcome writes readiness.json and updates GET /agents/readiness
        r_ok = await client.post("/api/v1/agents/readiness/test", json={"provider": "claude"})
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "VERIFIED"
        assert r_ok.json()["remediation"] is None
        assert probe_calls == ["claude"]
        assert (state_dir / "readiness.json").is_file()

        r_after_ok = await client.get("/api/v1/agents/readiness")
        after_map = {rec["provider"]: rec for rec in r_after_ok.json()["providers"]}
        assert after_map["claude"]["status"] == "VERIFIED"

        # Transient error returns HTTP 200 and preserves prior VERIFIED cache
        outcome_box["next"] = Tier2ProbeOutcome(kind="transient_error", error_summary="timeout")
        r_trans = await client.post("/api/v1/agents/readiness/test", json={"provider": "claude"})
        assert r_trans.status_code == 200
        assert r_trans.json()["status"] == "VERIFIED"

        # Auth failed scrubs secret and surfaces remediation
        outcome_box["next"] = Tier2ProbeOutcome(
            kind="auth_failed",
            error_summary="rejected sk-ant-secret-xyz-123456",
        )
        r_auth = await client.post("/api/v1/agents/readiness/test", json={"provider": "claude"})
        assert r_auth.status_code == 200
        auth_body = r_auth.json()
        assert auth_body["status"] == "AUTH_FAILED"
        assert "sk-ant-secret-xyz-123456" not in str(auth_body["last_auth_error_summary"])
        assert auth_body["remediation"] is not None
        assert len(auth_body["remediation"]["actions"]) >= 1

        # X-OpenDV-Env-First: 1 skips persisting readiness.json changes for gemini
        readiness_before = (state_dir / "readiness.json").read_text(encoding="utf-8")
        outcome_box["next"] = Tier2ProbeOutcome(kind="verified")
        r_env_first = await client.post(
            "/api/v1/agents/readiness/test",
            json={"provider": "claude"},
            headers={"X-OpenDV-Env-First": "1"},
        )
        assert r_env_first.status_code == 200
        assert r_env_first.json()["status"] == "VERIFIED"
        assert (state_dir / "readiness.json").read_text(encoding="utf-8") == readiness_before


@pytest.mark.asyncio
async def test_system_doctor_read_only_scoping_and_untrusted_override(tmp_path: Path) -> None:
    probe_calls: list[HarnessProvider] = []

    async def _probe(
        provider: HarnessProvider,
        _bin: str | None,
        _key: str | None,
        _env: Mapping[str, str],
    ) -> Tier2ProbeOutcome:
        probe_calls.append(provider)
        return Tier2ProbeOutcome(kind="verified")

    svc, _ = _make_di_svc(tmp_path, probe_fn=_probe)
    override_dir = svc.workspace_root / ".opendv" / "agents" / "ontology-first-mate"
    override_dir.mkdir(parents=True, exist_ok=True)
    (override_dir / "SKILL.md").write_text("# custom override\n", encoding="utf-8")
    svc.upsert_mcp_server(
        ExternalMcpServerSpec(
            server_id="ext-mcp-1",
            transport="streamable_http",
            url="http://127.0.0.1:9999/mcp",
            secret_refs=["MCP_TOK"],
            enabled=True,
        )
    )

    app = create_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_all = await client.get("/api/v1/system/doctor")
        assert r_all.status_code == 200
        doc = r_all.json()
        assert doc["agents"] is not None
        assert doc["agents"]["workspace_overrides_trusted"] is False
        assert doc["agents"]["pack_count"] == 0
        assert doc["mcp_servers"] is not None
        assert doc["mcp_servers"][0]["ready"] is False
        rem_cmds = [
            act.get("command") or ""
            for rem in doc["remediation"]
            for act in rem.get("actions", [])
        ]
        assert any("trust-workspace" in cmd for cmd in rem_cmds)
        assert any("MCP_TOK" in cmd for cmd in rem_cmds)
        assert probe_calls == []

        r_agents_only = await client.get("/api/v1/system/doctor?check_agents=true")
        assert r_agents_only.status_code == 200
        assert r_agents_only.json()["agents"] is not None
        assert r_agents_only.json()["mcp_servers"] is None

        r_mcp_only = await client.get("/api/v1/system/doctor?check_mcp=true")
        assert r_mcp_only.status_code == 200
        assert r_mcp_only.json()["agents"] is None
        assert r_mcp_only.json()["mcp_servers"] is not None
        assert probe_calls == []


@pytest.mark.asyncio
async def test_trusted_workspaces_revoke_exact_normalized_and_404(tmp_path: Path) -> None:
    svc, _ = _make_di_svc(tmp_path)
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        svc.trust_workspace_path(svc.workspace_root)
        r_exact = await client.post(
            "/api/v1/settings/trusted-workspaces/revoke",
            json={"workspace_root": str(svc.workspace_root)},
        )
        assert r_exact.status_code == 200
        assert r_exact.json()["trusted_workspaces"] == []

        # Normalized / case-variant match revokes cleanly
        svc.trust_workspace_path(svc.workspace_root)
        variant = (
            str(svc.workspace_root).upper()
            if os.name == "nt"
            else f"{svc.workspace_root}{os.sep}."
        )
        r_norm = await client.post(
            "/api/v1/settings/trusted-workspaces/revoke",
            json={"workspace_root": variant},
        )
        assert r_norm.status_code == 200
        assert r_norm.json()["trusted_workspaces"] == []

        # Unlisted workspace returns 404 RESOURCE_NOT_FOUND
        r_miss = await client.post(
            "/api/v1/settings/trusted-workspaces/revoke",
            json={"workspace_root": str(svc.workspace_root)},
        )
        _assert_problem(r_miss, status=404, error_code=ErrorCode.RESOURCE_NOT_FOUND)


@pytest.mark.asyncio
async def test_project_settings_patch_rfc7396_strict_and_404(tmp_path: Path) -> None:
    svc, _ = _make_di_svc(tmp_path)
    svc.init_project(ProjectInitReq(project_id="proj-s", domain_namespace="asset_mgmt"))
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_404 = await client.patch(
            "/api/v1/projects/missing-proj/settings",
            json={"max_turns": 20},
        )
        _assert_problem(r_404, status=404, error_code=ErrorCode.RESOURCE_NOT_FOUND)

        r_p1 = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={
                "harness_provider": "claude",
                "max_turns": 25,
                "model_overrides": {
                    "ship-vault": "m1",
                    "dv-semantic-critic": "m2",
                },
            },
        )
        assert r_p1.status_code == 200
        snap1 = r_p1.json()["settings_snapshot"]
        assert snap1["harness_provider"] == "claude"
        assert snap1["max_turns"] == 25
        assert snap1["model_overrides"] == {"ship-vault": "m1", "dv-semantic-critic": "m2"}

        # RFC 7396 per-key delete
        r_del_key = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={"model_overrides": {"ship-vault": None}},
        )
        assert r_del_key.status_code == 200
        assert r_del_key.json()["settings_snapshot"]["model_overrides"] == {
            "dv-semantic-critic": "m2"
        }

        # Explicit null reset
        r_reset = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={"model_overrides": None, "max_turns": None},
        )
        assert r_reset.status_code == 200
        snap_reset = r_reset.json()["settings_snapshot"]
        assert snap_reset["model_overrides"] == {}
        assert snap_reset["max_turns"] is None

        # Strict policy auto-promotes v7_critic_tier to FRONTIER_CRITIC
        r_worker = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={"v7_critic_tier": "STANDARD_WORKER"},
        )
        assert r_worker.status_code == 200
        r_strict = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={"cegar_policy": "strict"},
        )
        assert r_strict.status_code == 200
        snap_strict = r_strict.json()["settings_snapshot"]
        assert snap_strict["cegar_policy"] == "strict"
        assert snap_strict["v7_critic_tier"] == "FRONTIER_CRITIC"

        # Merged conflict when already strict -> 400 SYNTAX_INVALID_ARGUMENT
        r_conflict = await client.patch(
            "/api/v1/projects/proj-s/settings",
            json={"v7_critic_tier": "STANDARD_WORKER"},
        )
        _assert_problem(r_conflict, status=400, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


@pytest.mark.asyncio
async def test_validation_errors_return_400_and_remediation_on_422(tmp_path: Path) -> None:
    svc, _ = _make_di_svc(tmp_path)
    svc.update_settings(WorkspaceSettings(harness_provider="claude-cli"))
    svc.init_project(ProjectInitReq(project_id="proj-val", domain_namespace="asset_mgmt"))
    csv_path = svc.workspace_root / "sample.csv"
    csv_path.write_text("id,name\n1,a\n", encoding="utf-8")
    svc.attach_source(
        "proj-val",
        SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)),
    )
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        invalid_patches: list[dict[str, Any]] = [
            {},
            {"model_overrides": {}},
            {"harness_provider": None},
            {"model_overrides": {"unknown-agent": "m"}},
            {"model_overrides": {"ship-vault": "   "}},
            {"cegar_policy": "strict", "v7_critic_tier": "STANDARD_WORKER"},
            {"max_turns": 501},
            {"timeout_seconds": 7201},
            {"unknown_extra": 1},
        ]
        for payload in invalid_patches:
            resp = await client.patch("/api/v1/projects/proj-val/settings", json=payload)
            body = _assert_problem(resp, status=400, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)
            assert body.get("remediation") is None

        invalid_revokes: list[dict[str, Any]] = [
            {"workspace_root": ""},
            {"workspace_root": "   "},
            {"workspace_root": "//server/share"},
            {"workspace_root": "\\\\server\\share"},
            {"workspace_root": "//?/C:/foo"},
            {"workspace_root": "bad\x07path"},
        ]
        for payload in invalid_revokes:
            resp = await client.post("/api/v1/settings/trusted-workspaces/revoke", json=payload)
            _assert_problem(resp, status=400, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)

        for payload in [{"provider": "invalid-provider"}, {}]:
            resp = await client.post("/api/v1/agents/readiness/test", json=payload)
            _assert_problem(resp, status=400, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)

        # 422 NEEDS_LOCAL_SECRET_BINDING carries ProblemDetails.remediation
        r_dbx = await client.post(
            "/api/v1/sources/inspect",
            json={
                "kind": "databricks_uc",
                "uri_or_path": "https://dbc-1.cloud.databricks.com",
                "secret_refs": ["DBX_MISSING"],
            },
        )
        dbx_body = _assert_problem(
            r_dbx, status=422, error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING
        )
        assert dbx_body["remediation"] is not None
        assert dbx_body["remediation"]["summary"]
        assert dbx_body["remediation"]["actions"][0]["command"] == (
            "uv run opendv secrets set DBX_MISSING --prompt"
        )

        # 422 DRIVER_MISSING carries ProblemDetails.remediation
        r_drv = await client.post("/api/v1/projects/proj-val/ontology/construct", json={})
        drv_body = _assert_problem(r_drv, status=422, error_code=ErrorCode.DRIVER_MISSING)
        assert drv_body["remediation"] is not None
        assert "claude-cli" in drv_body["remediation"]["summary"]


@pytest.mark.asyncio
async def test_409_active_lease_and_concurrent_probe_conflicts(tmp_path: Path) -> None:
    probe_started = asyncio.Event()
    probe_release = asyncio.Event()

    async def _gated_probe(
        _provider: HarnessProvider,
        _bin: str | None,
        _key: str | None,
        _env: Mapping[str, str],
    ) -> Tier2ProbeOutcome:
        probe_started.set()
        await probe_release.wait()
        return Tier2ProbeOutcome(kind="verified")

    svc, _ = _make_di_svc(
        tmp_path,
        which_fn=lambda name: f"/bin/{name}" if name == "claude" else None,
        find_spec_fn=lambda mod: object() if mod == "anthropic" else None,
        probe_fn=_gated_probe,
    )
    svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-valid-key-999")
    svc.update_settings(WorkspaceSettings(harness_provider="claude"))
    svc.init_project(ProjectInitReq(project_id="proj-409", domain_namespace="asset_mgmt"))
    app = create_app(svc)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        # Non-job duckdb_write lease does NOT block PATCH /settings
        svc.state_manager.claim_lease(
            "proj-409", lease_key="duckdb_write:proj-409", holder_run_id="r-db", ttl_seconds=60
        )
        r_db_ok = await client.patch("/api/v1/projects/proj-409/settings", json={"max_turns": 15})
        assert r_db_ok.status_code == 200
        svc.state_manager.release_lease(
            "proj-409", lease_key="duckdb_write:proj-409", holder_run_id="r-db"
        )

        # Live active_job_lease blocks PATCH /settings and provider probe with 409
        acquire_project_job_lease(svc.state_manager, "proj-409", "run-active-1")
        r_patch_409 = await client.patch(
            "/api/v1/projects/proj-409/settings",
            json={"max_turns": 20},
        )
        _assert_problem(r_patch_409, status=409, error_code=ErrorCode.PROJECT_JOB_ACTIVE)

        r_probe_409 = await client.post(
            "/api/v1/agents/readiness/test",
            json={"provider": "claude"},
        )
        probe_409_body = _assert_problem(
            r_probe_409, status=409, error_code=ErrorCode.PROJECT_JOB_ACTIVE
        )
        assert probe_409_body["remediation"] is not None
        assert "run-active-1" in probe_409_body["remediation"]["summary"]
        assert probe_409_body["remediation"]["actions"] == []

        # Different provider is NOT blocked by claude's active lease
        r_other = await client.post("/api/v1/agents/readiness/test", json={"provider": "agy"})
        assert r_other.status_code == 200

        release_project_job_lease(svc.state_manager, "proj-409", "run-active-1")

        # Concurrent single-flight probe conflict -> 409 PROJECT_JOB_ACTIVE
        first_task = asyncio.create_task(
            client.post("/api/v1/agents/readiness/test", json={"provider": "claude"})
        )
        await asyncio.wait_for(probe_started.wait(), timeout=5.0)
        r_second = await client.post(
            "/api/v1/agents/readiness/test",
            json={"provider": "claude"},
        )
        second_body = _assert_problem(
            r_second, status=409, error_code=ErrorCode.PROJECT_JOB_ACTIVE
        )
        assert second_body["remediation"] is not None

        probe_release.set()
        r_first = await first_task
        assert r_first.status_code == 200
        assert r_first.json()["status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_proxy_forwarding_headers_and_is_remote_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, _ = _make_di_svc(tmp_path)
    csv_path = svc.workspace_root / "data.csv"
    csv_path.write_text("id,val\n1,10\n", encoding="utf-8")

    # 1. Default loopback app rejects any proxy forwarding header with 403
    loopback_app = create_app(svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=loopback_app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        for hdr_name, hdr_val in (
            ("x-forwarded-for", "127.0.0.1"),
            ("forwarded", "for=127.0.0.1"),
            ("x-real-ip", "127.0.0.1"),
            ("x-forwarded-host", "127.0.0.1:8420"),
            ("X-Forwarded-For", "203.0.113.9"),
            ("X-Real-IP", ""),
        ):
            r_hdr = await client.get("/api/v1/health", headers={hdr_name: hdr_val})
            _assert_problem(r_hdr, status=403, error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN)

    # 2. Remote-opted-in app treats proxy headers as remote in is_remote_request
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)
    remote_app = create_app(
        svc,
        allow_remote_unauthenticated=True,
        bound_host="127.0.0.1",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote_app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_health = await client.get(
            "/api/v1/health",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert r_health.status_code == 200

        r_direct = await client.post(
            "/api/v1/sources/inspect",
            json={"kind": "csv", "uri_or_path": str(csv_path)},
        )
        assert r_direct.status_code == 200

        for hdr_name, hdr_val in (
            ("x-forwarded-for", "203.0.113.9"),
            ("forwarded", "for=203.0.113.9"),
            ("x-real-ip", "203.0.113.9"),
            ("x-forwarded-host", "proxy.example.com"),
        ):
            r_proxied = await client.post(
                "/api/v1/sources/inspect",
                json={"kind": "csv", "uri_or_path": str(csv_path)},
                headers={hdr_name: hdr_val},
            )
            _assert_problem(r_proxied, status=403, error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN)


@pytest.mark.asyncio
async def test_vault_routes_launch_run_policy_propagation(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    copy_public_am_wave1(ws)
    state_dir = tmp_path / "state"
    settings_store = SettingsStore(user_state_dir=state_dir)
    secret_store = KeyringSecretStore(ws, state_dir=state_dir, settings_store=settings_store)
    state_mgr = ProjectStateManager(ws)
    state_mgr.initialize()
    readiness = HarnessReadinessService(
        settings_store=settings_store,
        secret_store=secret_store,
        db_path=state_mgr.db_path,
        which_fn=lambda _: None,
        find_spec_fn=lambda _: None,
    )

    hold_state: dict[str, bool] = {"held": False}

    def _sel(req: Any) -> MockTurnScript:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc, "proj-pol")
            if not hold_state["held"]:
                hold_state["held"] = True
                return MockTurnScript(
                    structured_output=build_wave1_am_draft("proj-pol", "0.1.0", profs).model_copy(
                        update={
                            "pending_clarifications": [
                                ClarificationQuestion(
                                    question_id="q-pol-1",
                                    concept_name="InstitutionalFiler",
                                    title="Confirm BK",
                                    prompt="Confirm filer_cik",
                                    sample_rows=["0001166559"],
                                    impact_if_locked="Locks hub_filer.",
                                    recommended_answer="filer_cik",
                                )
                            ]
                        }
                    )
                )
            return MockTurnScript(structured_output=build_wave1_am_draft("proj-pol", "0.1.0", profs))
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=95,
                    accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(score=93, classification="REASONABLY_STABLE")
            )
        return MockTurnScript(text_output="{}")

    svc = OpenDVService(
        ws,
        state_dir=state_dir,
        harness=DeterministicMockHarness(selector=_sel),
        readiness_service=readiness,
    )
    proj = svc.init_project(ProjectInitReq(project_id="proj-pol", domain_namespace="asset_mgmt"))
    src_sql = svc.attach_source(
        "proj-pol",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    src_pq = svc.attach_source(
        "proj-pol",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    src_md = svc.attach_source(
        "proj-pol",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    svc.commit_ontology(
        "proj-pol",
        OntologyCommitReq(
            operations=build_wave1_commit_ops(
                src_sql.source_id,
                src_pq.source_id,
                src_md.source_id,
                "Asset Management Stewardship, Risk & Identifier Governance Rules",
            ),  # type: ignore[arg-type]
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit W1 ontology for policy propagation test.",
        ),
    )

    app = create_app(svc)
    recorded_launches: list[tuple[str, str, str, Any]] = []
    orig_launch = app.state.task_manager.launch_run

    async def _spy_launch(
        service: OpenDVService,
        project_id: str,
        run_id: str,
        coro_factory: Any,
        *,
        step: Any,
        policy: Any = None,
        allow_sync_failure_cleanup: bool = True,
    ) -> Any:
        recorded_launches.append((project_id, run_id, step, policy))
        return await orig_launch(
            service,
            project_id,
            run_id,
            coro_factory,
            step=step,
            policy=policy,
            allow_sync_failure_cleanup=allow_sync_failure_cleanup,
        )

    app.state.task_manager.launch_run = _spy_launch

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as client:
        r_patch = await client.patch(
            "/api/v1/projects/proj-pol/settings",
            json={"cegar_policy": "fast"},
        )
        assert r_patch.status_code == 200

        # Build with omitted policy ({}) inherits settings_snapshot.cegar_policy ("fast")
        r_build = await client.post("/api/v1/projects/proj-pol/runs", json={})
        assert r_build.status_code == 202
        rid = r_build.json()["run_id"]
        assert r_build.json()["policy"] == "fast"
        assert recorded_launches[-1] == ("proj-pol", rid, "step6", "fast")

        await wait_run_via_sse(client, f"/api/v1/projects/proj-pol/runs/{rid}/events")
        assert (await client.get(f"/api/v1/projects/proj-pol/runs/{rid}")).json()[
            "status"
        ] == "HOLD_CHECKPOINTED"

        # Resume via /decisions preserves prior_state.policy ("fast")
        r_dec = await client.post(
            f"/api/v1/projects/proj-pol/runs/{rid}/decisions",
            json={"answers": {"q-pol-1": "filer_cik"}},
        )
        assert r_dec.status_code == 202
        assert recorded_launches[-1] == ("proj-pol", rid, "step6", "fast")
        await wait_run_via_sse(client, f"/api/v1/projects/proj-pol/runs/{rid}/events")

        # Horizon steering preserves prior_state.policy ("fast")
        r_hor = await client.post(
            f"/api/v1/projects/proj-pol/runs/{rid}/horizon",
            json={"action": "accept", "notes": "Approve"},
        )
        assert r_hor.status_code == 202
        assert recorded_launches[-1] == ("proj-pol", rid, "step6", "fast")
        await wait_run_via_sse(client, f"/api/v1/projects/proj-pol/runs/{rid}/events")
