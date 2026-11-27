"""End-to-end verification tests for UAT-V26-CLI-01..05, Layer 4 isolation, and stdio MCP."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest.mock
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from opendv.cli.export_contracts import (
    build_cli_spec,
    check_contracts,
    find_repo_root,
    generate_cli_reference_markdown,
)
from opendv.cli.main import app, main
from opendv.cli.mcp_stdio import MCP_TOOL_NAMES
from opendv.models import (
    CliEnvelope,
    CliExitCode,
    ColumnTriageEntry,
    ErrorCode,
    ProblemDetails,
    ProjectInitReq,
    ProjectSettingsPatchReq,
    RevisionMetadata,
    SourceAttachReq,
    VaultBuildReq,
)
from opendv.service import OpenDVService
from tests.cli._cli_test_helpers import (
    ScriptedCliHarness,
    build_account_ontology_yaml,
    create_fake_agy_binary,
    free_loopback_port,
    run_loopback_api_server,
)

_RUNNER = CliRunner()


def _invoke_cli(
    args: list[str], *, env: dict[str, str] | None = None, input_text: str | None = None
) -> tuple[int, str, dict[str, Any] | None]:
    if "--json" in args:
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
                exit_code = main(args)
        finally:
            sys.stdin = old_stdin
        stdout_text = stdout_buf.getvalue()
        parsed: dict[str, Any] | None = None
        if stdout_text.strip():
            parsed = json.loads(stdout_text)
            CliEnvelope.model_validate(parsed)
        return exit_code, stdout_text, parsed
    result = _RUNNER.invoke(app, args, env=env, input=input_text)
    return result.exit_code, result.stdout, None


def _setup_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    harness: ScriptedCliHarness | None = None,
) -> tuple[Path, Path, Path, OpenDVService]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    state_root = tmp_path / "user_state"
    state_root.mkdir(parents=True, exist_ok=True)
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
    svc = OpenDVService(ws, state_dir=state_dir, harness=harness)
    cur = svc.get_settings()
    svc.update_settings(cur.model_copy(update={"secret_backend": "user_state_file"}))
    return ws, state_root, csv_path, svc


def _isolated_subprocess_env(ws: Path, state_root: Path, repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["OPENDV_HOME"] = str(ws / ".opendv")
    env["LOCALAPPDATA"] = str(state_root)
    env["XDG_STATE_HOME"] = str(state_root)
    env["HOME"] = str(state_root)
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_path
    return env


def _create_certified_project(
    ws: Path,
    csv_path: Path,
    svc: OpenDVService,
    harness: ScriptedCliHarness,
    project_id: str = "p1",
) -> tuple[str, str, str]:
    ont_yaml = build_account_ontology_yaml()
    svc.init_project(
        ProjectInitReq(
            project_id=project_id,
            domain_namespace="ibor",
            baseline_content=ont_yaml,
            baseline_format="yaml",
        )
    )
    src = svc.attach_source(
        project_id,
        SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)),
    )
    table_id = svc.list_tables_index(project_id)[0].table_id
    harness.bind_source(src.source_id, table_id)
    run_st = asyncio.run(svc.build_vault(project_id, VaultBuildReq(policy="balanced")))
    assert run_st.status == "COMPLETED"
    return src.source_id, table_id, run_st.run_id


@pytest.mark.parametrize("use_remote", [False, True])
def test_cli_01_all_subcommands_and_env_first_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """UAT-V26-CLI-01: All 11 subcommand groups + --json CliEnvelope + --env-first parity."""
    harness = ScriptedCliHarness(project_id="proj_cli")
    ws, state_root, csv_path, svc = _setup_workspace(tmp_path, monkeypatch, harness=harness)
    ont_file = ws / "ont_v1.yaml"
    ont_file.write_text(build_account_ontology_yaml(), encoding="utf-8")
    ont_v2_file = ws / "ont_v2.yaml"
    ont_v2_file.write_text(
        build_account_ontology_yaml(version="1.1.0", extra_concept="Counterparty"),
        encoding="utf-8",
    )

    with run_loopback_api_server(svc) as api_url:
        prefix: list[str] = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # 1. serve --help (runtime guard tested separately in CLI-02)
        rc, _, _ = _invoke_cli(["serve", "--help"])
        assert rc == 0

        # 2. doctor
        rc, _, env = _invoke_cli([*prefix, "doctor", "--check-agents", "--check-mcp"])
        assert rc == 0 and env is not None and env["ok"] is True
        assert "agents" in env["data"] and "mcp_servers" in env["data"]

        # 3. settings (show, set, trust-workspace)
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "settings",
                "set",
                "--cegar-policy",
                "fast",
                "--secret-precedence",
                "store_first",
                "--secret-backend",
                "user_state_file",
            ]
        )
        assert rc == 0 and env is not None and env["data"]["cegar_policy"] == "fast"
        rc, _, env = _invoke_cli([*prefix, "settings", "show"])
        assert rc == 0 and env is not None and env["data"]["cegar_policy"] == "fast"
        rc, _, env = _invoke_cli([*prefix, "settings", "trust-workspace", "--path", str(ws)])
        assert rc == 0 and env is not None and len(env["data"]["trusted_workspaces"]) >= 1

        # 4. agents (list, inspect)
        rc, _, env = _invoke_cli([*prefix, "agents", "list"])
        assert rc == 0 and env is not None and len(env["data"]) >= 1
        agent_id = env["data"][0]["agent_id"]
        rc, _, env = _invoke_cli([*prefix, "agents", "inspect", agent_id])
        assert rc == 0 and env is not None and env["data"]["summary"]["agent_id"] == agent_id

        # 5. mcp (register, list, remove)
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "mcp",
                "register",
                "--id",
                "demo_mcp",
                "--transport",
                "stdio",
                "--command",
                json.dumps([sys.executable, "--version"]),
                "--allow-scout-calls",
            ]
        )
        assert rc == 0 and env is not None and env["data"]["server_id"] == "demo_mcp"
        rc, _, env = _invoke_cli([*prefix, "mcp", "list"])
        assert rc == 0 and env is not None and any(x["server_id"] == "demo_mcp" for x in env["data"])
        rc, _, env = _invoke_cli([*prefix, "mcp", "remove", "--id", "demo_mcp"])
        assert rc == 0 and env is not None and env["data"]["deleted"] is True

        # 6. secrets (set --stdin, list, delete) + --env-first real backend parity check
        secret_val = "top-secret-token-xyz"
        rc, stdout_text, env = _invoke_cli(
            [*prefix, "secrets", "set", "CLI_TEST_SECRET", "--stdin"],
            input_text=secret_val + "\n",
        )
        assert rc == 0 and secret_val not in stdout_text
        assert env is not None and env["data"]["present"] is True

        monkeypatch.setenv("CLI_TEST_SECRET", "env-override-token")
        rc, _, env = _invoke_cli([*prefix, "secrets", "list", "--keys", "CLI_TEST_SECRET"])
        assert rc == 0 and env is not None and env["data"][0]["backend"] == "user_state_file"

        rc, _, env = _invoke_cli(
            [*prefix, "--env-first", "secrets", "list", "--keys", "CLI_TEST_SECRET"]
        )
        assert rc == 0 and env is not None
        assert env["data"][0]["present"] is True and env["data"][0]["backend"] == "env"

        rc, _, env = _invoke_cli([*prefix, "secrets", "delete", "CLI_TEST_SECRET"])
        assert rc == 0 and env is not None and env["data"]["deleted"] is True

        # 7. project (init, list, show, export, import, delete)
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "project",
                "init",
                "--project",
                "proj_cli",
                "--namespace",
                "ibor",
                "--baseline",
                str(ont_file),
                "--baseline-format",
                "yaml",
            ]
        )
        assert rc == 0 and env is not None
        base_rev = env["data"]["active_ontology_rev_id"]

        rc, _, env = _invoke_cli([*prefix, "project", "list"])
        assert rc == 0 and env is not None and any(p["project_id"] == "proj_cli" for p in env["data"])
        rc, _, env = _invoke_cli([*prefix, "project", "show", "--project", "proj_cli"])
        assert rc == 0 and env is not None and env["data"]["project_id"] == "proj_cli"

        # 8. source (adapters, inspect, attach, list, reattach, note, detach)
        rc, _, env = _invoke_cli([*prefix, "source", "adapters"])
        assert rc == 0 and env is not None and any(a["adapter_id"] == "csv" for a in env["data"])
        rc, _, env = _invoke_cli(
            [*prefix, "source", "inspect", "--kind", "csv", "--uri", str(csv_path)]
        )
        assert rc == 0 and env is not None and len(env["data"]["tables"]) == 1
        rc, _, env = _invoke_cli(
            [*prefix, "source", "attach", "--project", "proj_cli", "--kind", "csv", "--uri", str(csv_path)]
        )
        assert rc == 0 and env is not None
        src_id = env["data"]["source_id"]
        rc, _, env = _invoke_cli([*prefix, "source", "list", "--project", "proj_cli"])
        assert rc == 0 and env is not None and any(s["source_id"] == src_id for s in env["data"])
        table_id = svc.list_tables_index("proj_cli")[0].table_id
        harness.bind_source(src_id, table_id)
        if not use_remote:
            fake_bin = create_fake_agy_binary(ws, source_id=src_id, table_id=table_id, project_id="proj_cli")
            monkeypatch.setenv("OPENDV_AGY_CLI_BIN", str(fake_bin))

        rc, _, env = _invoke_cli(
            [*prefix, "source", "reattach", "--project", "proj_cli", "--source", src_id]
        )
        assert rc == 0 and env is not None and env["data"]["source_id"] == src_id
        rc, _, env = _invoke_cli(
            [*prefix, "source", "note", "--project", "proj_cli", "--text", "Account is canonical key."]
        )
        assert rc == 0 and env is not None
        note_id = env["data"]["source_id"]
        rc, _, env = _invoke_cli(
            [*prefix, "source", "detach", "--project", "proj_cli", "--source", note_id]
        )
        assert rc == 0 and env is not None and env["data"]["deleted"] is True

        # 9. ontology (construct, fibo-qa, validate, commit, export)
        rc, _, env = _invoke_cli(
            [*prefix, "ontology", "construct", "--project", "proj_cli", "--discovery-mode", "incremental_new", "--wait"]
        )
        assert rc == 0 and env is not None and env["data"]["status"] == "COMPLETED"
        run4_id = env["data"]["run_id"]
        rc, _, env = _invoke_cli(
            [*prefix, "ontology", "control", "--project", "proj_cli", "--run", run4_id, "--action", "hint", "--hint", "check status"]
        )
        assert rc == 0 and env is not None
        rc, _, env = _invoke_cli(
            [*prefix, "ontology", "control", "--project", "proj_cli", "--run", run4_id, "--action", "accept-best"]
        )
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value
        rc, _, env = _invoke_cli([*prefix, "ontology", "fibo-qa", "--query", "Account", "--project", "proj_cli"])
        assert rc == 0 and env is not None and len(env["data"]["matches"]) >= 1
        rc, _, env = _invoke_cli(
            [*prefix, "ontology", "validate", "--project", "proj_cli", "--yaml", str(ont_v2_file), "--base-revision", base_rev]
        )
        assert rc == 0 and env is not None and env["data"]["valid"] is True
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "ontology",
                "commit",
                "--project",
                "proj_cli",
                "--yaml",
                str(ont_v2_file),
                "--base-revision",
                base_rev,
                "--rationale",
                "Add Counterparty concept vetted by domain steward",
                "--waive-quarantine",
            ]
        )
        assert rc == 0 and env is not None, env
        rc, _, env = _invoke_cli([*prefix, "ontology", "export", "--project", "proj_cli", "--format", "ttl"])
        assert rc == 0 and env is not None and "Account" in env["data"]["content"]

        # 10. vault (build, verify, waive, logs)
        rc, _, env = _invoke_cli(
            [*prefix, "vault", "build", "--project", "proj_cli", "--policy", "balanced", "--wait"]
        )
        assert rc == 0 and env is not None and env["data"]["status"] == "COMPLETED"
        run6_id = env["data"]["run_id"]
        rc, _, env = _invoke_cli([*prefix, "vault", "verify", "--project", "proj_cli"])
        assert rc == 0 and env is not None and env["data"]["passed"] is True
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "vault",
                "waive",
                "--project",
                "proj_cli",
                "--rule",
                "DV-AP-01",
                "--entity",
                "hub_account",
                "--rationale",
                "Approved architectural waiver reviewed by data steward",
            ]
        )
        assert rc == 0 and env is not None and env["data"]["rule_id"] == "DV-AP-01"
        rc, _, env = _invoke_cli(
            [*prefix, "vault", "logs", "--project", "proj_cli", "--run", run6_id]
        )
        assert rc == 0 and env is not None and len(env["data"]["events"]) >= 1
        # Verify `vault logs` without `--run` selects the newest run (`runs[0]` == run6_id, not run4_id)
        rc, _, env = _invoke_cli([*prefix, "vault", "logs", "--project", "proj_cli"])
        assert rc == 0 and env is not None and env["data"]["events"][0]["run_id"] == run6_id
        # Verify Step-4 `ontology control` rejects Step-6 run id cleanly with exit 3 in both modes
        rc, _, env = _invoke_cli(
            [*prefix, "ontology", "control", "--project", "proj_cli", "--run", run6_id, "--action", "pause"]
        )
        assert rc == int(CliExitCode.NOT_FOUND)
        assert env is not None and env["error_code"] == ErrorCode.RESOURCE_NOT_FOUND.value

        # 11. storage (compile, migrate, regenerate, query)
        rc, _, env = _invoke_cli(
            [*prefix, "storage", "compile", "--project", "proj_cli", "--target", "duckdb_local"]
        )
        assert rc == 0 and env is not None and env["data"]["target_id"] == "duckdb_local"
        rc, _, env = _invoke_cli(
            [*prefix, "storage", "migrate", "--project", "proj_cli", "--target", "duckdb_local"]
        )
        assert rc == 0 and env is not None and env["data"]["verified"] is True
        rc, _, env = _invoke_cli(
            [*prefix, "storage", "regenerate", "--project", "proj_cli", "--target", "duckdb_local"]
        )
        assert rc == 0 and env is not None and env["data"]["verified"] is True
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "storage",
                "query",
                "--project",
                "proj_cli",
                "--sql",
                "SELECT COUNT(*) AS cnt FROM raw_vault.hub_account",
            ]
        )
        assert rc == 0 and env is not None and env["data"]["rows"][0][0] >= 2

        # project export -> import -> delete
        bundle_zip = ws / "proj_cli.core.opendv.zip"
        rc, _, env = _invoke_cli(
            [*prefix, "project", "export", "--project", "proj_cli", "--out", str(bundle_zip)]
        )
        assert rc == 0 and bundle_zip.is_file()
        rc, _, env = _invoke_cli([*prefix, "project", "delete", "--project", "proj_cli"])
        assert rc == 0 and env is not None and env["data"]["deleted"] is True
        rc, _, env = _invoke_cli(
            [*prefix, "project", "import", str(bundle_zip), "--conflict", "fail"]
        )
        assert rc == 0 and env is not None and env["data"]["project_id"] == "proj_cli"


@pytest.mark.parametrize("use_remote", [False, True])
def test_cli_02_all_8_exit_codes_and_serve_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_remote: bool,
) -> None:
    """UAT-V26-CLI-02: Verify all 8 CliExitCodes (0, 1, 2, 3, 4, 5, 40, 50) in both modes."""
    harness = ScriptedCliHarness(project_id="p_codes", hold_step6_once=False)
    ws, state_root, csv_path, svc = _setup_workspace(tmp_path, monkeypatch, harness=harness)
    src_id, table_id, _ = _create_certified_project(ws, csv_path, svc, harness, project_id="p_codes")
    harness.hold_step6_once = True

    # Save valid wave_ir for reference before triggering gate/emitter failures.
    good_ir = svc.get_vault_ir("p_codes")

    with run_loopback_api_server(svc) as api_url:
        prefix: list[str] = ["--api-url", api_url, "--json"] if use_remote else ["--json"]

        # Exit 0: OK
        rc, _, env = _invoke_cli([*prefix, "project", "list"])
        assert rc == int(CliExitCode.OK) and env is not None and env["ok"] is True

        # Exit 40: serve --host 0.0.0.0 before bind
        rc, _, env = _invoke_cli(["--json", "serve", "--host", "0.0.0.0"])
        assert rc == int(CliExitCode.REMOTE_BIND_UNAUTHENTICATED)
        assert env is not None and env["error_code"] == ErrorCode.REMOTE_BIND_UNAUTHENTICATED.value
        assert env["problem"]["status"] is None

        # Exit 2: SYNTAX_INVALID_ARGUMENT + VERIFICATION_GATE_FAILED
        rc, _, env = _invoke_cli([*prefix, "settings", "set", "--secret-precedence", "invalid_mode"])
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

        # Exit 3: RESOURCE_NOT_FOUND
        rc, _, env = _invoke_cli([*prefix, "project", "show", "--project", "no_such_project"])
        assert rc == int(CliExitCode.NOT_FOUND)
        assert env is not None and env["error_code"] == ErrorCode.RESOURCE_NOT_FOUND.value

        # Exit 4: PROJECT_ID_CONFLICT
        ont_path = ws / "ont.yaml"
        ont_path.write_text(build_account_ontology_yaml(), encoding="utf-8")
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "project",
                "init",
                "--project",
                "p_codes",
                "--namespace",
                "ibor",
                "--baseline",
                str(ont_path),
            ]
        )
        assert rc == int(CliExitCode.CONFLICT_OR_LOCKED)
        assert env is not None and env["error_code"] == ErrorCode.PROJECT_ID_CONFLICT.value

        # Exit 5: HOLD_CHECKPOINTED on `--wait`
        if use_remote:
            hold_harness = ScriptedCliHarness(project_id="p_codes_hold", hold_step6_once=True)
            hold_ws = tmp_path / "hold_ws"
            hold_ws.mkdir(exist_ok=True)
            hold_csv = hold_ws / "accounts.csv"
            hold_csv.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
            hold_state = tmp_path / "hold_state"
            hold_state.mkdir(exist_ok=True)
            hold_svc = OpenDVService(hold_ws, state_dir=hold_state, harness=hold_harness)
            hold_svc.init_project(
                ProjectInitReq(
                    project_id="p_codes_hold",
                    domain_namespace="ibor",
                    baseline_content=ont_path.read_text(encoding="utf-8"),
                    baseline_format="yaml",
                )
            )
            hold_src = hold_svc.attach_source(
                "p_codes_hold", SourceAttachReq(kind="csv", uri_or_filename=str(hold_csv))
            )
            hold_tbl = hold_svc.list_tables_index("p_codes_hold")[0].table_id
            hold_harness.bind_source(hold_src.source_id, hold_tbl)
            with run_loopback_api_server(hold_svc) as hold_url:
                hold_prefix = ["--api-url", hold_url, "--json"]
                rc, _, env = _invoke_cli(
                    [
                        *hold_prefix,
                        "vault",
                        "build",
                        "--project",
                        "p_codes_hold",
                        "--policy",
                        "balanced",
                        "--wait",
                    ]
                )
                assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
                assert env is not None and env["error_code"] == ErrorCode.HOLD_CHECKPOINTED.value
                hold_run_id = env["data"]["run_id"]
                rc, _, env = _invoke_cli(
                    [
                        *hold_prefix,
                        "vault",
                        "hold",
                        "--project",
                        "p_codes_hold",
                        "--run",
                        hold_run_id,
                        "--answers-json",
                        json.dumps({"q1": "account_id"}),
                        "--wait",
                    ]
                )
                assert rc == int(CliExitCode.OK), env
        else:
            # Trigger UNCERTIFIED_DRAFT via CLI subprocess without monkeypatching guards.
            unres_ir_harness = ScriptedCliHarness(project_id="p_unres", unresolved_mode=True)
            unres_svc = OpenDVService(ws, state_dir=state_root / "opendv", harness=unres_ir_harness)
            unres_svc.init_project(
                ProjectInitReq(
                    project_id="p_unres",
                    domain_namespace="ibor",
                    baseline_content=ont_path.read_text(encoding="utf-8"),
                    baseline_format="yaml",
                )
            )
            unres_src = unres_svc.attach_source(
                "p_unres", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path))
            )
            unres_tbl = unres_svc.list_tables_index("p_unres")[0].table_id
            # Create fake agy returning unresolved columns to enter UNCERTIFIED_DRAFT natively
            bad_draft_dir = tmp_path / "unres_bin"
            bad_draft_dir.mkdir(exist_ok=True)
            bad_bin = create_fake_agy_binary(
                bad_draft_dir,
                source_id=unres_src.source_id,
                table_id=unres_tbl,
                project_id="p_unres",
                unresolved=True,
            )
            rc, _, env = _invoke_cli(
                [*prefix, "vault", "build", "--project", "p_unres", "--policy", "balanced", "--wait"],
                env={**os.environ, "OPENDV_AGY_CLI_BIN": str(bad_bin)},
            )
            assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
            assert env is not None and env["error_code"] == ErrorCode.UNCERTIFIED_DRAFT.value

        # Exit 50: EMITTER_VERIFICATION_FAILED
        bad_binding = good_ir.hubs[0].source_bindings[0].model_copy(
            update={"col_mappings": {"account_id": "missing_col"}}
        )
        bad_emit_ir = good_ir.model_copy(
            update={"hubs": [good_ir.hubs[0].model_copy(update={"source_bindings": [bad_binding]})]}
        )
        svc.state_manager.save_revision(
            "p_codes",
            revision_id="wave-bad-emit",
            rev_kind="wave_ir",
            rev_key="wave-bad-emit",
            parent_rev_id=None,
            payload=bad_emit_ir,
            metadata=RevisionMetadata(actor="human", reason="bad col for eval_gate"),
        )
        svc.state_manager.update_project_pointers("p_codes", active_wave_id="wave-bad-emit")
        rc, _, env = _invoke_cli(
            [
                *prefix,
                "storage",
                "compile",
                "--project",
                "p_codes",
                "--target",
                "duckdb_local",
                "--verify-runtime",
            ]
        )
        assert rc == int(CliExitCode.EMITTER_VERIFICATION_FAILED)
        assert env is not None and env["error_code"] == ErrorCode.EMITTER_VERIFICATION_FAILED.value

        # Exit 2: VERIFICATION_GATE_FAILED on `vault verify`
        bad_verify_ir = good_ir.model_copy(
            update={
                "column_triage": [
                    ColumnTriageEntry(
                        table_id=table_id,
                        col_name="account_id",
                        disposition="UNRESOLVED",
                        target_ref=None,
                        rationale="test fail",
                    )
                ]
            }
        )
        svc.state_manager.save_revision(
            "p_codes",
            revision_id="wave-bad-verify",
            rev_kind="wave_ir",
            rev_key="wave-bad-verify",
            parent_rev_id=None,
            payload=bad_verify_ir,
            metadata=RevisionMetadata(actor="human", reason="unresolved test"),
        )
        svc.state_manager.update_project_pointers("p_codes", active_wave_id="wave-bad-verify")
        rc, _, env = _invoke_cli([*prefix, "vault", "verify", "--project", "p_codes"])
        assert rc == int(CliExitCode.VALIDATION_ERROR)
        assert env is not None and env["error_code"] == ErrorCode.VERIFICATION_GATE_FAILED.value

        # Exit 1: EXECUTION_ERROR (closed --api-url port)
        closed_port = free_loopback_port()
        rc, _, env = _invoke_cli(
            ["--api-url", f"http://127.0.0.1:{closed_port}", "--json", "project", "list"]
        )
        assert rc == int(CliExitCode.EXECUTION_ERROR)
        assert env is not None and env["ok"] is False


def test_cli_03_subprocess_isolation_and_vault_build_req(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-CLI-03: Child CLI process never imports api/fastapi/starlette/uvicorn; real exits & CWD."""
    ws, state_root, csv_path, _ = _setup_workspace(tmp_path, monkeypatch)
    repo_root = find_repo_root()
    env = _isolated_subprocess_env(ws, state_root, repo_root)

    # Subprocess execution of `python -m opendv.cli --json project list`
    proc = subprocess.run(
        [sys.executable, "-m", "opendv.cli", "--json", "project", "list"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    assert parsed["ok"] is True

    # Real non-zero exit codes via `python -m opendv.cli` subprocess & `main()` (40, 2, 3, 0 on help)
    from opendv.cli.main import main as cli_main

    assert cli_main([]) == 0
    assert cli_main(["project"]) == 0
    assert cli_main(["--json", "bad-cmd"]) == int(CliExitCode.VALIDATION_ERROR)

    for argv, expected_rc, expected_code in (
        (["--json", "serve", "--host", "0.0.0.0"], 40, ErrorCode.REMOTE_BIND_UNAUTHENTICATED.value),
        (["--json", "bad-cmd"], 2, ErrorCode.SYNTAX_INVALID_ARGUMENT.value),
        (["--json", "project", "show", "--project", "missing"], 3, ErrorCode.RESOURCE_NOT_FOUND.value),
    ):
        err_proc = subprocess.run(
            [sys.executable, "-m", "opendv.cli", *argv],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert err_proc.returncode == expected_rc, err_proc.stderr + err_proc.stdout
        err_env = json.loads(err_proc.stdout)
        assert err_env["exit_code"] == expected_rc and err_env["error_code"] == expected_code

    # Fresh CWD without OPENDV_HOME set: `source attach --uri accounts.csv` works natively
    fresh_ws = tmp_path / "fresh_cwd_ws"
    fresh_ws.mkdir(parents=True, exist_ok=True)
    (fresh_ws / "accounts.csv").write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
    cwd_env = _isolated_subprocess_env(fresh_ws, state_root, repo_root)
    cwd_env.pop("OPENDV_HOME", None)
    for cmd_args in (
        ["--json", "project", "init", "--project", "cwd_p", "--namespace", "ibor", "--starter-pack", "core_spine"],
        ["--json", "source", "attach", "--project", "cwd_p", "--kind", "csv", "--uri", "accounts.csv"],
    ):
        cwd_proc = subprocess.run(
            [sys.executable, "-m", "opendv.cli", *cmd_args],
            cwd=str(fresh_ws),
            env=cwd_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert cwd_proc.returncode == 0, cwd_proc.stderr + cwd_proc.stdout

    # Verify sys.modules isolation in a fresh child process running `main(["--json", "project", "list"])`
    probe = (
        "import sys; from opendv.cli.main import main; "
        "rc = main(['--json', 'project', 'list']); "
        "banned = [m for m in ('opendv.api', 'fastapi', 'starlette', 'uvicorn') if m in sys.modules]; "
        "assert rc == 0 and not banned, f'rc={rc} banned={banned}'"
    )
    probe_proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe_proc.returncode == 0, probe_proc.stderr + probe_proc.stdout

    # Verify zero `dbt_sql_dialect` in VaultBuildReq or cli-spec `vault build`
    assert "dbt_sql_dialect" not in VaultBuildReq.model_fields
    spec = build_cli_spec()
    vault_build_cmd = next(c for c in spec["subcommands"] if c["path"] == ["vault", "build"])
    assert all(opt["name"] != "dbt_sql_dialect" for opt in vault_build_cmd["options"])


def test_cli_04_stdio_mcp_17_tools_end_to_end_and_error_survival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-CLI-04: Real child stdio JSON-RPC MCP client exercising all 17 tools + error recovery."""
    harness = ScriptedCliHarness(project_id="mcp_p1")
    ws, state_root, csv_path, svc = _setup_workspace(tmp_path, monkeypatch, harness=harness)
    svc.init_project(
        ProjectInitReq(
            project_id="mcp_p1",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    src = svc.attach_source("mcp_p1", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    src_id = src.source_id
    table_id = svc.list_tables_index("mcp_p1")[0].table_id
    harness.bind_source(src_id, table_id)
    fake_bin = create_fake_agy_binary(ws, source_id=src_id, table_id=table_id, project_id="mcp_p1")
    repo_root = find_repo_root()
    child_env = _isolated_subprocess_env(ws, state_root, repo_root)
    child_env["OPENDV_AGY_CLI_BIN"] = str(fake_bin)

    def _decode_result(res: Any) -> Any:
        assert not res.is_error, f"Unexpected MCP error: {res.content}"
        if res.structured_content is not None:
            return res.structured_content.get("result", res.structured_content)
        return json.loads(res.content[0].text)

    async def _run_stdio_e2e() -> None:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", "mcp_p1"],
            env=child_env,
            cwd=str(repo_root),
        )
        async with (
            stdio_client(server_params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools_resp = await session.list_tools()
            listed_names = [t.name for t in tools_resp.tools]
            assert listed_names == list(MCP_TOOL_NAMES)
            assert len(listed_names) == 17

            # Lifecycle read & memory tools
            proj = _decode_result(await session.call_tool("project_get", {}))
            assert proj["project_id"] == "mcp_p1"
            base_rev = proj["active_ontology_rev_id"]

            sources = _decode_result(await session.call_tool("source_list", {}))
            assert len(sources) == 1

            tables = _decode_result(await session.call_tool("evidence_tables_index", {}))
            assert len(tables) == 1

            mem = _decode_result(
                await session.call_tool(
                    "concept_memory_update",
                    {"term": "Account", "state": "PINNED", "concept_uri": "urn:ibor:Account"},
                )
            )
            assert mem["state"] == "PINNED"

            hits = _decode_result(await session.call_tool("ontology_search", {"query": "Account"}))
            assert any(h["concept_name"] == "Account" for h in hits)

            # Step 4 tools over stdio subprocess (omitting discovery_mode inherits snapshot)
            c_run = _decode_result(
                await session.call_tool("ontology_construct", {})
            )
            assert c_run["status"] == "COMPLETED"
            st4 = _decode_result(
                await session.call_tool("ontology_status", {"run_id": c_run["run_id"]})
            )
            assert st4["run_id"] == c_run["run_id"]

            # Step 4 validate + Step 6 build/verify + Step 7 compile/migrate/query over stdio subprocess
            v2_yaml = build_account_ontology_yaml(version="1.1.0", extra_concept="Book")
            val_res = _decode_result(
                await session.call_tool(
                    "ontology_validate", {"yaml_text": v2_yaml, "base_revision_id": base_rev}
                )
            )
            assert val_res["valid"] is True

            b_run = _decode_result(
                await session.call_tool("vault_build", {})
            )
            assert b_run["status"] == "COMPLETED"
            assert b_run["policy"] == "balanced"
            st6 = _decode_result(
                await session.call_tool("vault_status", {"run_id": b_run["run_id"]})
            )
            assert st6["run_id"] == b_run["run_id"]
            v_ver = _decode_result(await session.call_tool("vault_verify", {}))
            assert v_ver["passed"] is True

            comp = _decode_result(
                await session.call_tool("storage_compile", {"target_id": "duckdb_local"})
            )
            assert comp["target_id"] == "duckdb_local"
            mig = _decode_result(
                await session.call_tool("storage_migrate", {"target_id": "duckdb_local"})
            )
            assert mig["verified"] is True
            qres = _decode_result(
                await session.call_tool(
                    "storage_query",
                    {"sql": "SELECT COUNT(*) AS cnt FROM raw_vault.hub_account", "limit": 10},
                )
            )
            assert qres["rows"][0][0] >= 2

            com_res = _decode_result(
                await session.call_tool(
                    "ontology_commit",
                    {
                        "base_revision_id": base_rev,
                        "rationale": "Add Book concept approved by data governance steward",
                        "yaml_text": v2_yaml,
                        "waive_quarantine": True,
                    },
                )
            )
            assert com_res["ontology_version"] == "1.1.0"

            # Exercise `vault_horizon` (submit_waiver action) and `ontology_answer` error over stdio JSON-RPC
            hz_res = _decode_result(
                await session.call_tool(
                    "vault_horizon",
                    {
                        "run_id": b_run["run_id"],
                        "action": "submit_waiver",
                        "waiver": {
                            "waiver_id": "w-mcp",
                            "rule_id": "DV-AP-01",
                            "entity_name": "hub_account",
                            "rationale": "Approved waiver verified via stdio MCP test suite",
                        },
                    },
                )
            )
            assert hz_res["run_id"] == b_run["run_id"]

            ans_err = await session.call_tool(
                "ontology_answer", {"run_id": "missing-run", "answers": {}}
            )
            assert ans_err.is_error is True
            ans_txt = ans_err.content[0].text
            ans_prob = ProblemDetails.model_validate_json(ans_txt[ans_txt.find("{") :])
            assert ans_prob.error_code == ErrorCode.RESOURCE_NOT_FOUND

            # Error propagation WITHOUT crashing stdio
            err_res = await session.call_tool(
                "storage_query", {"sql": "DROP TABLE raw_vault.hub_account", "limit": 10}
            )
            assert err_res.is_error is True
            err_txt = err_res.content[0].text
            problem = ProblemDetails.model_validate_json(err_txt[err_txt.find("{") :])
            assert problem.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

            # Verify stdio channel remains alive and responsive after structured error
            still_alive = _decode_result(await session.call_tool("project_get", {}))
            assert still_alive["project_id"] == "mcp_p1"

    asyncio.run(_run_stdio_e2e())


def test_cli_mcp_snapshot_inheritance_and_remediation_survival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify stdio MCP omitted defaults inherit project snapshot and ToolError carries remediation."""
    harness = ScriptedCliHarness(project_id="mcp_snap")
    ws, state_root, csv_path, svc = _setup_workspace(tmp_path, monkeypatch, harness=harness)
    svc.init_project(
        ProjectInitReq(
            project_id="mcp_snap",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    src = svc.attach_source("mcp_snap", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    table_id = svc.list_tables_index("mcp_snap")[0].table_id
    harness.bind_source(src.source_id, table_id)
    svc.update_project_settings(
        "mcp_snap",
        ProjectSettingsPatchReq(cegar_policy="fast", discovery_mode="full_rebuild"),
    )

    svc.init_project(
        ProjectInitReq(
            project_id="mcp_rem",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    svc.attach_source("mcp_rem", SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)))
    svc.update_project_settings("mcp_rem", ProjectSettingsPatchReq(harness_provider="claude"))

    fake_bin = create_fake_agy_binary(ws, source_id=src.source_id, table_id=table_id, project_id="mcp_snap")
    repo_root = find_repo_root()
    child_env = _isolated_subprocess_env(ws, state_root, repo_root)
    child_env["OPENDV_AGY_CLI_BIN"] = str(fake_bin)
    child_env.pop("ANTHROPIC_API_KEY", None)

    async def _run() -> None:
        snap_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", "mcp_snap"],
            env=child_env,
            cwd=str(repo_root),
        )
        async with (
            stdio_client(snap_params) as (r_s, w_s),
            ClientSession(r_s, w_s) as sess,
        ):
            await sess.initialize()
            c_res = await sess.call_tool("ontology_construct", {})
            assert not c_res.is_error, c_res.content
            b_res = await sess.call_tool("vault_build", {})
            assert not b_res.is_error, b_res.content
            b_data = (
                b_res.structured_content.get("result", b_res.structured_content)
                if b_res.structured_content is not None
                else json.loads(b_res.content[0].text)
            )
            assert b_data["policy"] == "fast"

        rem_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", "mcp_rem"],
            env=child_env,
            cwd=str(repo_root),
        )
        async with (
            stdio_client(rem_params) as (r_s, w_s),
            ClientSession(r_s, w_s) as sess,
        ):
            await sess.initialize()
            err_res = await sess.call_tool("ontology_construct", {})
            assert err_res.is_error is True
            err_txt = err_res.content[0].text
            problem = ProblemDetails.model_validate_json(err_txt[err_txt.find("{") :])
            assert problem.error_code in (ErrorCode.DRIVER_MISSING, ErrorCode.AGENT_NOT_CONFIGURED)
            assert problem.remediation is not None
            assert problem.remediation.summary
            assert len(problem.remediation.actions) >= 1

    asyncio.run(_run())


def test_cli_05_contract_exports_and_markdown_reference() -> None:
    """UAT-V26-CLI-05: Contract exports match disk with zero drift and pass schemas/v2/cli-spec.schema.json."""
    repo_root = find_repo_root()
    schemas_v2 = repo_root / "schemas" / "v2"

    # CI drift check
    assert check_contracts(repo_root) == []

    # Exact schema files present and valid
    cli_spec = json.loads((schemas_v2 / "cli-spec.json").read_text(encoding="utf-8"))
    cli_schema = json.loads((schemas_v2 / "cli-spec.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=cli_spec, schema=cli_schema)

    top_groups = {cmd["path"][0] for cmd in cli_spec["subcommands"]}
    assert len(top_groups) == 12
    assert len(cli_spec["subcommands"]) == 59
    assert top_groups == {
        "serve",
        "doctor",
        "settings",
        "agents",
        "mcp",
        "secrets",
        "project",
        "source",
        "ontology",
        "vault",
        "storage",
        "model",
    }
    cmd_paths = {" ".join(cmd["path"]) for cmd in cli_spec["subcommands"]}
    assert {
        "settings models",
        "settings profile list",
        "settings profile set",
        "settings profile delete",
        "settings profile test",
        "project settings show",
        "project settings set",
    }.issubset(cmd_paths)
    settings_set_spec = next(c for c in cli_spec["subcommands"] if c["path"] == ["settings", "set"])
    hp_opt = next(o for o in settings_set_spec["options"] if o["flag"] == "--harness-provider")
    assert hp_opt["type"] == "choice"
    assert set(hp_opt["choices"]) == {"agy", "claude-cli", "claude", "gemini"}

    openapi = json.loads((schemas_v2 / "openapi.json").read_text(encoding="utf-8"))
    assert "/api/v1/health" in openapi["paths"]
    vault_ir_schema = json.loads(
        (schemas_v2 / "vault-schema-ir-v2.schema.json").read_text(encoding="utf-8")
    )
    assert vault_ir_schema["title"] == "VaultSchemaIR v2.0"
    bundle_schema = json.loads(
        (schemas_v2 / "project-bundle-v2.schema.json").read_text(encoding="utf-8")
    )
    assert "BundleManifest" in bundle_schema.get("title", "") or "Bundle" in bundle_schema.get(
        "title", ""
    )

    md = generate_cli_reference_markdown(cli_spec)
    assert "# Open-DV V2 CLI Reference" in md
    assert "`REMOTE_BIND_UNAUTHENTICATED`" in md
    assert "`EMITTER_VERIFICATION_FAILED`" in md
