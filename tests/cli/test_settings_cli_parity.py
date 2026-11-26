"""Comprehensive Phase 4 CLI & HTTP parity tests for settings, profiles, project settings, and doctor."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
import unittest.mock
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import Request

from opendv.agents import ProbeFn, Tier2ProbeOutcome
from opendv.api.server import create_app
from opendv.cli import commands_core
from opendv.cli._client import CliClient
from opendv.cli.main import main
from opendv.cli.mcp_stdio import create_mcp_stdio_server
from opendv.models import (
    CliEnvelope,
    CliExitCode,
    ConnectorConfigPayload,
    ErrorCode,
    HarnessProvider,
    ProblemDetails,
    ProjectInitReq,
    ProjectSettingsPatchReq,
    SourceAttachReq,
    SourceProfile,
)
from opendv.service import OpenDVService
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    acquire_project_job_lease,
    release_project_job_lease,
)
from tests.cli._cli_test_helpers import (
    ScriptedCliHarness,
    build_account_ontology_yaml,
    create_fake_agy_binary,
    free_loopback_port,
)


def _invoke_main(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> tuple[int, str, str, dict[str, Any] | None]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdin = sys.stdin
    env_ctx = (
        unittest.mock.patch.dict(os.environ, env, clear=False)
        if env is not None
        else contextlib.nullcontext()
    )
    try:
        sys.stdin = io.StringIO(input_text or "")
        with (
            env_ctx,
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            rc = main(args)
    finally:
        sys.stdin = old_stdin
    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue()
    parsed: dict[str, Any] | None = None
    if "--json" in args and out.strip():
        parsed = json.loads(out)
        CliEnvelope.model_validate(parsed)
    return rc, out, err, parsed


def _setup_di_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    harness: ScriptedCliHarness | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    find_spec_fn: Callable[[str], object | None] | None = None,
    probe_fn: ProbeFn | None = None,
) -> tuple[Path, Path, Path, OpenDVService]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    state_root = tmp_path / "user_state"
    state_dir = state_root / "opendv"
    state_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ws / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status\nACC1,Alpha,OPEN\nACC2,Beta,CLOSED\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENDV_HOME", str(ws / ".opendv"))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("HOME", str(state_root))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    settings_store = SettingsStore(user_state_dir=state_dir)
    cur = settings_store.load_settings()
    settings_store.save_settings(cur.model_copy(update={"secret_backend": "user_state_file"}))
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
    return ws, state_dir, csv_path, svc


@contextmanager
def _loopback_server_with_recorder(
    service: OpenDVService,
) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    recorded: list[tuple[str, str]] = []
    app = create_app(service=service)

    @app.middleware("http")
    async def _record_requests(request: Request, call_next: Any) -> Any:
        recorded.append((request.method, request.url.path))
        return await call_next(request)

    port = free_loopback_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    import threading
    import time

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base_url}/api/v1/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Timed out starting loopback API server.")
        yield base_url, recorded
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


@pytest.mark.parametrize("use_remote", [False, True])
def test_settings_set_overrides_clears_and_strict_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """Verify settings show/set budgets, model overrides, --clear conflicts, strict promotion, and StrEnum errors."""
    _, _, _, svc = _setup_di_service(tmp_path, monkeypatch)
    with _loopback_server_with_recorder(svc) as (api_url, _):
        prefix = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # Empty invocation fails with SYNTAX_INVALID_ARGUMENT (exit 2)
        rc, _, _, env = _invoke_main([*prefix, "settings", "set"])
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # Invalid StrEnum value produces JSON CliEnvelope exit 2
        rc, _, _, env = _invoke_main([*prefix, "settings", "set", "--harness-provider", "bogus"])
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # Set budgets, model overrides, and strict policy (auto-promotes v7_critic_tier)
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "set",
                "--harness-provider",
                "claude-cli",
                "--cegar-policy",
                "strict",
                "--dbt-sql-dialect",
                "snowflake",
                "--secret-backend",
                "user_state_file",
                "--secret-precedence",
                "store_first",
                "--max-turns",
                "16",
                "--max-tokens",
                "250000",
                "--timeout-seconds",
                "900",
                "--model-override",
                "STANDARD_WORKER=claude-sonnet-4-6",
                "--model-override",
                "dv-semantic-critic=claude-opus-4-6",
            ]
        )
        assert rc == 0 and env is not None
        data = env["data"]
        assert data["harness_provider"] == "claude-cli"
        assert data["cegar_policy"] == "strict"
        assert data["v7_critic_tier"] == "FRONTIER_CRITIC"
        assert data["dbt_sql_dialect"] == "snowflake"
        assert data["max_turns"] == 16
        assert data["max_tokens"] == 250_000
        assert data["timeout_seconds"] == 900
        assert data["model_overrides"] == {
            "STANDARD_WORKER": "claude-sonnet-4-6",
            "dv-semantic-critic": "claude-opus-4-6",
        }

        # Explicit strict + STANDARD_WORKER rejected
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "set",
                "--cegar-policy",
                "strict",
                "--v7-critic-tier",
                "STANDARD_WORKER",
            ]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # Idempotent --clear-model-override on both set and unset valid targets
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "set",
                "--clear-model-override",
                "STANDARD_WORKER",
                "--clear-model-override",
                "ship-vault",
            ]
        )
        assert rc == 0 and env is not None
        assert env["data"]["model_overrides"] == {"dv-semantic-critic": "claude-opus-4-6"}

        # Conflict checks: <field> + --clear <field|all>, override + --clear model-overrides|all
        for conflict_args in (
            ["--max-turns", "10", "--clear", "max-turns"],
            ["--max-tokens", "100", "--clear", "all"],
            ["--timeout-seconds", "60", "--clear", "timeout-seconds"],
            ["--model-override", "STANDARD_WORKER=m1", "--clear", "model-overrides"],
            ["--clear-model-override", "STANDARD_WORKER", "--clear", "all"],
            ["--model-override", "STANDARD_WORKER=m1", "--clear-model-override", "STANDARD_WORKER"],
        ):
            rc, _, _, env = _invoke_main([*prefix, "settings", "set", *conflict_args])
            assert rc == int(CliExitCode.VALIDATION_ERROR), conflict_args
            assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # --clear single field then --clear all
        rc, _, _, env = _invoke_main([*prefix, "settings", "set", "--clear", "max-turns"])
        assert rc == 0 and env is not None
        assert env["data"]["max_turns"] is None and env["data"]["max_tokens"] == 250_000

        rc, _, _, env = _invoke_main([*prefix, "settings", "set", "--clear", "all"])
        assert rc == 0 and env is not None
        assert env["data"]["max_tokens"] is None
        assert env["data"]["timeout_seconds"] is None
        assert env["data"]["model_overrides"] == {}

        rc, _, _, env = _invoke_main([*prefix, "settings", "show"])
        assert rc == 0 and env is not None and env["data"]["model_overrides"] == {}


@pytest.mark.parametrize("use_remote", [False, True])
def test_settings_models_and_trust_workspace_revoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """Verify offline `settings models` and `trust-workspace [--revoke]` UNC/HTTP parity."""
    ws, _, _, svc = _setup_di_service(tmp_path, monkeypatch)
    override_dir = ws / ".opendv" / "agents" / "ship-vault"
    override_dir.mkdir(parents=True, exist_ok=True)
    (override_dir / "SKILL.md").write_text("# custom skill\n", encoding="utf-8")

    with _loopback_server_with_recorder(svc) as (api_url, recorded):
        prefix = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # settings models (default active provider + explicit --provider)
        rc, _, _, env = _invoke_main([*prefix, "settings", "models"])
        assert rc == 0 and env is not None
        assert env["data"]["provider"] == "agy"
        assert len(env["data"]["models"]) >= 1

        rc, _, _, env = _invoke_main([*prefix, "settings", "models", "--provider", "claude-cli"])
        assert rc == 0 and env is not None
        assert env["data"]["provider"] == "claude-cli"
        assert any("claude" in m["id"] for m in env["data"]["models"])

        # UNC, control-char, and blank rejection BEFORE expanduser/resolve (both trust and revoke)
        for bad_path in ("", "   ", "bad\x00ws", "//server/share", "\\\\server\\share"):
            for extra in ([], ["--revoke"]):
                rc, _, _, env = _invoke_main(
                    [*prefix, "settings", "trust-workspace", "--path", bad_path, *extra]
                )
                assert rc == int(CliExitCode.VALIDATION_ERROR), (bad_path, extra)
                assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # Trust workspace -> revoke via POST /api/v1/settings/trusted-workspaces/revoke
        rc, _, _, env = _invoke_main([*prefix, "settings", "trust-workspace", "--path", str(ws)])
        assert rc == 0 and env is not None
        assert len(env["data"]["trusted_workspaces"]) == 1

        recorded.clear()
        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "trust-workspace", "--path", str(ws), "--revoke"]
        )
        assert rc == 0 and env is not None
        assert env["data"]["trusted_workspaces"] == []
        if use_remote:
            assert ("POST", "/api/v1/settings/trusted-workspaces/revoke") in recorded

        # Revoking an unlisted path returns RESOURCE_NOT_FOUND (exit 3)
        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "trust-workspace", "--path", str(ws), "--revoke"]
        )
        assert rc == int(CliExitCode.NOT_FOUND)
        assert env is not None and env["error_code"] == ErrorCode.RESOURCE_NOT_FOUND.value


@pytest.mark.parametrize("use_remote", [False, True])
def test_settings_profile_crud_and_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """Verify `settings profile {list, set, delete, test}` including databricks_uc rules."""
    _, _, csv_path, svc = _setup_di_service(tmp_path, monkeypatch)
    with _loopback_server_with_recorder(svc) as (api_url, _):
        prefix = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # Blank name rejected, non-databricks missing --uri rejected, secret in options rejected
        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "profile", "set", "--name", "  ", "--adapter", "csv", "--uri", str(csv_path)]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)

        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "profile", "set", "--name", "bad_csv", "--adapter", "csv"]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)

        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "profile",
                "set",
                "--name",
                "leaky",
                "--adapter",
                "csv",
                "--uri",
                str(csv_path),
                "--options-json",
                json.dumps({"api_key": "sk-secret-inline"}),
            ]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)

        # Save csv profile, replace it, list, and test it
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "profile",
                "set",
                "--name",
                "local_accounts",
                "--adapter",
                "csv",
                "--uri",
                str(csv_path),
                "--display-name",
                "Accounts CSV",
            ]
        )
        assert rc == 0 and env is not None
        assert env["data"]["name"] == "local_accounts"
        assert env["data"]["profile"]["display_name"] == "Accounts CSV"

        # Full replacement semantics
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "profile",
                "set",
                "--name",
                "local_accounts",
                "--adapter",
                "csv",
                "--uri",
                str(csv_path),
                "--timeout-seconds",
                "15",
            ]
        )
        assert rc == 0 and env is not None
        assert env["data"]["profile"]["display_name"] == "local_accounts"
        assert env["data"]["profile"]["profile_timeout_seconds"] == 15

        # databricks_uc allows omitting --uri when options.host is non-empty string
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "profile",
                "set",
                "--name",
                "dbx_prod",
                "--adapter",
                "databricks_uc",
                "--options-json",
                json.dumps(
                    {
                        "host": "adb-demo.azuredatabricks.net",
                        "http_path": "/sql/1.0/warehouses/abc",
                        "catalog": "main",
                        "schema": "default",
                    }
                ),
            ]
        )
        assert rc == 0 and env is not None
        assert env["data"]["profile"]["uri_or_path"] == ""

        # databricks_uc without --uri AND without options.host rejected
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "settings",
                "profile",
                "set",
                "--name",
                "dbx_bad",
                "--adapter",
                "databricks_uc",
                "--options-json",
                json.dumps({"http_path": "/sql/1"}),
            ]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)

        rc, _, _, env = _invoke_main([*prefix, "settings", "profile", "list"])
        assert rc == 0 and env is not None
        assert set(env["data"].keys()) == {"local_accounts", "dbx_prod"}

        # Test csv profile succeeds
        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "profile", "test", "--name", "local_accounts"]
        )
        assert rc == 0 and env is not None
        SourceProfile.model_validate(env["data"])

        # Test databricks profile without secret_refs fails with NEEDS_LOCAL_SECRET_BINDING + remediation
        rc, _, _, env = _invoke_main([*prefix, "settings", "profile", "test", "--name", "dbx_prod"])
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.NEEDS_LOCAL_SECRET_BINDING.value
        assert env["problem"]["remediation"] is not None

        # Unknown profile test / delete -> RESOURCE_NOT_FOUND
        rc, _, _, env = _invoke_main([*prefix, "settings", "profile", "test", "--name", "missing"])
        assert rc == int(CliExitCode.NOT_FOUND)
        rc, _, _, env = _invoke_main([*prefix, "settings", "profile", "delete", "--name", "missing"])
        assert rc == int(CliExitCode.NOT_FOUND)

        rc, _, _, env = _invoke_main(
            [*prefix, "settings", "profile", "delete", "--name", "local_accounts"]
        )
        assert rc == 0 and env is not None and env["data"] == {"deleted": True, "name": "local_accounts"}


@pytest.mark.parametrize("use_remote", [False, True])
def test_project_settings_show_set_lease_guard_and_snapshot_inheritance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """Verify `project settings {show, set}`, active-lease 409->exit 4, and omitted workflow defaults."""
    harness = ScriptedCliHarness(project_id="p_snap")
    ws, _, csv_path, svc = _setup_di_service(tmp_path, monkeypatch, harness=harness)
    svc.init_project(
        ProjectInitReq(
            project_id="p_snap",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    src = svc.attach_source("p_snap", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    table_id = svc.list_tables_index("p_snap")[0].table_id
    harness.bind_source(src.source_id, table_id)

    with _loopback_server_with_recorder(svc) as (api_url, _):
        prefix = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "project",
                "settings",
                "set",
                "--project",
                "p_snap",
                "--cegar-policy",
                "fast",
                "--discovery-mode",
                "full_rebuild",
                "--max-turns",
                "9",
                "--model-override",
                "STANDARD_WORKER=scripted-fast",
            ]
        )
        assert rc == 0 and env is not None
        snap = env["data"]["settings_snapshot"]
        assert snap["cegar_policy"] == "fast"
        assert snap["discovery_mode"] == "full_rebuild"
        assert snap["max_turns"] == 9
        assert snap["model_overrides"] == {"STANDARD_WORKER": "scripted-fast"}

        # Sparse second patch preserves untouched fields (discovery_mode & max_turns)
        rc, _, _, env = _invoke_main(
            [
                *prefix,
                "project",
                "settings",
                "set",
                "--project",
                "p_snap",
                "--dbt-sql-dialect",
                "postgres",
                "--clear-model-override",
                "STANDARD_WORKER",
            ]
        )
        assert rc == 0 and env is not None
        snap = env["data"]["settings_snapshot"]
        assert snap["dbt_sql_dialect"] == "postgres"
        assert snap["discovery_mode"] == "full_rebuild"
        assert snap["max_turns"] == 9
        assert snap["model_overrides"] == {}

        rc, _, _, env = _invoke_main([*prefix, "project", "settings", "show", "--project", "p_snap"])
        assert rc == 0 and env is not None and env["data"]["cegar_policy"] == "fast"

        # Active lease blocks `project settings set` with 409 PROJECT_JOB_ACTIVE -> exit 4
        acquire_project_job_lease(svc.state_manager, "p_snap", "run-live-1", ttl_seconds=120)
        try:
            rc, _, _, env = _invoke_main(
                [*prefix, "project", "settings", "set", "--project", "p_snap", "--max-turns", "12"]
            )
            assert rc == int(CliExitCode.CONFLICT_OR_LOCKED)
            assert env is not None and env["error_code"] == ErrorCode.PROJECT_JOB_ACTIVE.value
        finally:
            release_project_job_lease(svc.state_manager, "p_snap", "run-live-1")

        # Omitted --discovery-mode and --policy inherit project settings snapshot
        if not use_remote:
            fake_bin = create_fake_agy_binary(
                ws, source_id=src.source_id, table_id=table_id, project_id="p_snap"
            )
            monkeypatch.setenv("OPENDV_AGY_CLI_BIN", str(fake_bin))

        rc, _, _, env = _invoke_main(
            [*prefix, "ontology", "construct", "--project", "p_snap", "--wait"]
        )
        assert rc == 0 and env is not None and env["data"]["status"] == "COMPLETED"
        assert env["data"]["effective_max_turns"] == 9

        rc, _, _, env = _invoke_main(
            [*prefix, "vault", "build", "--project", "p_snap", "--wait"]
        )
        assert rc == 0 and env is not None and env["data"]["status"] == "COMPLETED"
        assert env["data"]["policy"] == "fast"
        assert env["data"]["effective_max_turns"] == 9


@pytest.mark.parametrize("use_remote", [False, True])
def test_doctor_probe_overlay_and_remediation_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """Verify doctor null stripping, --probe 5-line overlay, exit 0 on AUTH_FAILED, exit 4 on 409, and stderr remediation."""
    outcome_box: dict[str, Tier2ProbeOutcome] = {
        "next": Tier2ProbeOutcome(kind="auth_failed", error_summary="Invalid ANTHROPIC_API_KEY"),
    }

    async def _probe(
        _provider: HarnessProvider,
        _bin: str | None,
        _key: str | None,
        env_map: Mapping[str, str],
    ) -> Tier2ProbeOutcome:
        if env_map.get("ANTHROPIC_API_KEY") == "sk-ant-valid-env-override":
            return Tier2ProbeOutcome(kind="verified")
        return outcome_box["next"]

    ws, _, _, svc = _setup_di_service(
        tmp_path,
        monkeypatch,
        which_fn=lambda name: f"/bin/{name}" if name == "claude" else None,
        find_spec_fn=lambda mod: object() if mod == "anthropic" else None,
        probe_fn=_probe,
    )
    svc.update_settings(svc.get_settings().model_copy(update={"harness_provider": "claude"}))
    svc.set_secret("ANTHROPIC_API_KEY", "sk-ant-bad-stored-key")

    # Add an untrusted workspace override so doc.remediation also holds index-1 remediation
    ov_dir = ws / ".opendv" / "agents" / "ship-vault"
    ov_dir.mkdir(parents=True, exist_ok=True)
    (ov_dir / "SKILL.md").write_text("# override\n", encoding="utf-8")

    if not use_remote:
        def _make_di_client(ctx: Any) -> CliClient:
            client = CliClient(commands_core.cli_context(ctx), workspace_root=ws, state_dir=svc.state_dir)
            client._service = svc
            return client

        monkeypatch.setattr(commands_core, "make_client", _make_di_client)

    with _loopback_server_with_recorder(svc) as (api_url, _):
        prefix = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # Filtering --check-agents strips top-level `mcp_servers` key
        rc, _, _, env = _invoke_main([*prefix, "doctor", "--check-agents"])
        assert rc == 0 and env is not None
        assert "agents" in env["data"] and "mcp_servers" not in env["data"]

        # --probe failing auth still exits 0 and puts probed AUTH_FAILED remediation at index 0
        rc, _, _, env = _invoke_main([*prefix, "doctor", "--probe"])
        assert rc == 0 and env is not None
        claude_rec = next(
            p for p in env["data"]["readiness"]["providers"] if p["provider"] == "claude"
        )
        assert claude_rec["status"] == "AUTH_FAILED"
        assert len(env["data"]["remediation"]) == 2
        assert "claude" in env["data"]["remediation"][0]["summary"].lower()

        # --env-first --probe succeeding overlays VERIFIED & drops orig_rec remediation, keeping workspace override remediation
        rc, _, _, env = _invoke_main(
            [*prefix, "--env-first", "doctor", "--probe"],
            env={"ANTHROPIC_API_KEY": "sk-ant-valid-env-override"},
        )
        assert rc == 0 and env is not None
        claude_rec = next(
            p for p in env["data"]["readiness"]["providers"] if p["provider"] == "claude"
        )
        assert claude_rec["status"] == "VERIFIED"
        assert len(env["data"]["remediation"]) == 1
        assert "trust-workspace" in (env["data"]["remediation"][0]["actions"][0]["command"] or "")

        # Active lease on project with `claude` provider causes `doctor --probe` -> 409 -> exit 4
        svc.init_project(
            ProjectInitReq(
                project_id="p_busy",
                domain_namespace="ibor",
                baseline_content=build_account_ontology_yaml(),
                baseline_format="yaml",
            )
        )
        acquire_project_job_lease(svc.state_manager, "p_busy", "run-claude-busy", ttl_seconds=120)
        try:
            rc, _, _, env = _invoke_main([*prefix, "doctor", "--probe"])
            assert rc == int(CliExitCode.CONFLICT_OR_LOCKED)
            assert env is not None and env["error_code"] == ErrorCode.PROJECT_JOB_ACTIVE.value
        finally:
            release_project_job_lease(svc.state_manager, "p_busy", "run-claude-busy")

        # Plain stderr + --json remediation rendering on synchronous error
        svc.update_settings(
            svc.get_settings().model_copy(
                update={
                    "connection_profiles": {
                        "dbx_err": ConnectorConfigPayload(
                            adapter_id="databricks_uc",
                            display_name="DBX",
                            uri_or_path="",
                            options={"host": "adb.azuredatabricks.net"},
                            secret_refs=[],
                        )
                    }
                }
            )
        )
        plain_prefix = ["--api-url", api_url] if use_remote else []
        rc, _, err_stderr, _ = _invoke_main(
            [*plain_prefix, "settings", "profile", "test", "--name", "dbx_err"]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert "Remediation:" in err_stderr
        assert "[Human action required]" in err_stderr
        assert "(interactive terminal)" in err_stderr


def test_mcp_stdio_inprocess_snapshot_and_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `create_mcp_stdio_server` defaults `None` to project snapshot and emits remediation in ToolError."""
    harness = ScriptedCliHarness(project_id="mcp_inproc")
    _, _, csv_path, svc = _setup_di_service(
        tmp_path,
        monkeypatch,
        harness=harness,
        which_fn=lambda _: None,
        find_spec_fn=lambda _: None,
    )
    svc.init_project(
        ProjectInitReq(
            project_id="mcp_inproc",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    src = svc.attach_source("mcp_inproc", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    table_id = svc.list_tables_index("mcp_inproc")[0].table_id
    harness.bind_source(src.source_id, table_id)

    # Set project snapshot to strict + full_rebuild and verify MCP tool inherits them when omitted
    rc, _, _, env = _invoke_main(
        [
            "--json",
            "project",
            "settings",
            "set",
            "--project",
            "mcp_inproc",
            "--cegar-policy",
            "fast",
            "--discovery-mode",
            "full_rebuild",
        ]
    )
    assert rc == 0 and env is not None

    mcp = create_mcp_stdio_server(project_id="mcp_inproc", service=svc)

    async def _check() -> None:
        res = await mcp.call_tool("vault_build", {})
        payload = (
            res.structured_content.get("result", res.structured_content)
            if res.structured_content is not None
            else json.loads(res.content[0].text)
        )
        assert payload["policy"] == "fast"

    asyncio.run(_check())

    # Switch to non-injected service with missing `claude` prereqs -> ToolError carries ProblemDetails.remediation
    no_harness_svc = OpenDVService(
        svc.workspace_root,
        state_dir=svc.state_dir,
        readiness_service=svc._readiness,
    )
    no_harness_svc.update_project_settings(
        "mcp_inproc",
        ProjectSettingsPatchReq(harness_provider="claude"),
    )
    mcp_err = create_mcp_stdio_server(project_id="mcp_inproc", service=no_harness_svc)

    async def _check_err() -> None:
        with pytest.raises(Exception) as exc_info:
            await mcp_err.call_tool("ontology_construct", {})
        txt = str(exc_info.value)
        prob = ProblemDetails.model_validate_json(txt[txt.find("{") :])
        assert prob.error_code == ErrorCode.DRIVER_MISSING
        assert prob.remediation is not None
        assert any(a.actor == "agent" for a in prob.remediation.actions)

    asyncio.run(_check_err())
