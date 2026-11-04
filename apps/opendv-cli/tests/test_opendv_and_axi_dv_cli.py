"""Integration tests for `opendv` and `axi-dv` CLI commands in `--local` and `--api-url` modes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from opendv_cli import (
    OpenDVCliApiClient,
    OpenDVCliApiError,
    app_main,
    axi_dv_main,
    validate_api_url,
)
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_platform_api.deps import REPO_ROOT


class _AsgiSyncBridgeTransport(httpx.BaseTransport):
    """Bridge synchronous `httpx.Client` requests to an in-process FastAPI ASGI application."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _send() -> httpx.Response:
            async_transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(
                transport=async_transport, base_url="http://testserver"
            ) as client:
                async_res = await client.request(
                    method=request.method,
                    url=str(request.url),
                    headers=dict(request.headers),
                    content=request.read(),
                )
                return httpx.Response(
                    status_code=async_res.status_code,
                    headers=async_res.headers,
                    content=async_res.content,
                    request=request,
                )

        return asyncio.run(_send())


def test_opendv_cli_local_subcommands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv` subcommands (`verify`, `profile`, `compile`, `sandbox`, `run`, `hold`)."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    pe_ir = "examples/private_equity/pe_canonical_ir.json"
    pe_run_id = "run-pe-apex-horizon-20261030"

    # 1. verify --local --json with explicit contract, graph-a, graph-b
    rc = app_main(
        [
            "verify",
            "--local",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--json",
        ]
    )
    assert rc == 0
    out_verify = json.loads(capsys.readouterr().out)
    assert out_verify["mode"] == "local"
    assert out_verify["overall_passed"] is True
    assert "@toon_v1" in out_verify["toon_text"]

    # 2. axi-dv default verify with --toon and option value matching subcommand name
    rc_toon = axi_dv_main(
        [
            "--local",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--toon",
        ]
    )
    assert rc_toon == 0
    out_toon = capsys.readouterr().out.strip()
    assert out_toon.startswith("@toon_v1")

    rc_opt_sub = axi_dv_main(
        [
            "--run-id",
            "hold",
            "--local",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--json",
        ]
    )
    assert rc_opt_sub == 0
    out_opt_sub = json.loads(capsys.readouterr().out)
    assert out_opt_sub["run_id"] == "hold"
    assert out_opt_sub["overall_passed"] is True

    rc_default_opendv = app_main(
        [
            "--local",
            "--contract",
            pe_contract,
            "--graph-a",
            pe_graph_a,
            "--graph-b",
            pe_graph_b,
            "--json",
        ]
    )
    assert rc_default_opendv == 0
    out_default_opendv = json.loads(capsys.readouterr().out)
    assert out_default_opendv["overall_passed"] is True

    # 3. profile --local --json (both --graph-b and --source-csv)
    rc_prof = app_main(
        [
            "profile",
            "--local",
            "--project-id",
            "proj-apex-horizon-pe",
            "--graph-b",
            pe_graph_b,
            "--json",
        ]
    )
    assert rc_prof == 0
    out_prof = json.loads(capsys.readouterr().out)
    assert out_prof["mode"] == "local"
    assert out_prof["status"] == "PASS"
    assert out_prof["table_count"] >= 5

    sample_csv = tmp_path / "src_customers.csv"
    sample_csv.write_text(
        "customer_id,legal_name,country_iso,annual_revenue_usd\n"
        "CUST-001,Alpha Corp,US,1200000.00\n"
        "CUST-002,Beta GmbH,DE,850000.00\n"
        "CUST-003,Gamma Ltd,GB,2100000.00\n",
        encoding="utf-8",
    )
    rc_prof_csv = app_main(
        [
            "profile",
            "--local",
            "--source-csv",
            str(sample_csv),
            "--source-system",
            "crm_bronze",
            "--table-name",
            "src_customers",
            "--json",
        ]
    )
    assert rc_prof_csv == 0
    out_prof_csv = json.loads(capsys.readouterr().out)
    assert out_prof_csv["mode"] == "local"
    assert out_prof_csv["status"] == "PASS"
    assert out_prof_csv["source_system"] == "CRM_BRONZE"
    assert out_prof_csv["table_name"] == "src_customers"
    assert out_prof_csv["table_count"] == 1
    assert out_prof_csv["column_count"] == 4
    assert "graph_b_profile" in out_prof_csv

    # 4. compile --local --json (with --canonical-ir and dynamic lowering without --canonical-ir)
    rc_comp = app_main(
        [
            "compile",
            "--local",
            "--contract",
            pe_contract,
            "--canonical-ir",
            pe_ir,
            "--json",
        ]
    )
    assert rc_comp == 0
    out_comp = json.loads(capsys.readouterr().out)
    assert out_comp["mode"] == "local"
    assert out_comp["status"] == "PASS"
    assert out_comp["hubs_compiled"] == 4
    assert out_comp["links_compiled"] == 5
    assert out_comp["satellites_compiled"] == 11

    rc_comp_dyn = app_main(
        [
            "compile",
            "--local",
            "--contract",
            pe_contract,
            "--json",
        ]
    )
    assert rc_comp_dyn == 0
    out_comp_dyn = json.loads(capsys.readouterr().out)
    assert out_comp_dyn["mode"] == "local"
    assert out_comp_dyn["status"] == "PASS"
    assert out_comp_dyn["canonical_ir_path"] is None
    assert len(out_comp_dyn["canonical_ir_sha256"]) == 64
    assert out_comp_dyn["hubs_compiled"] == 4
    assert out_comp_dyn["links_compiled"] == 5
    assert out_comp_dyn["satellites_compiled"] == 11
    assert len(out_comp_dyn["sql_zones"]) == 5

    # 5. sandbox --local --json
    rc_sbx = app_main(
        [
            "sandbox",
            "--local",
            "--contract",
            pe_contract,
            "--query-type",
            "ADVERSARIAL_BATCHES_1_TO_4",
            "--cutoff",
            "2026-11-15 23:59:59",
            "--json",
        ]
    )
    assert rc_sbx == 0
    out_sbx = json.loads(capsys.readouterr().out)
    assert out_sbx["all_batches_passed"] is True
    assert out_sbx["dynamic_rules_passed"] == 15
    assert out_sbx["batch_report"]["all_passed"] is True
    assert out_sbx["dynamic_check_report"]["passed_count"] == 15
    assert len(out_sbx["query_d10b_rows"]) == 1

    # 6. run --local --json (get, list filter, events with RBAC redaction & cursor, missing run_id)
    rc_run = app_main(["run", "get", "--local", "--run-id", pe_run_id, "--json"])
    assert rc_run == 0
    out_run = json.loads(capsys.readouterr().out)
    assert out_run["run_id"] == pe_run_id

    rc_list_filtered = app_main(
        ["run", "list", "--local", "--project-id", "proj-nonexistent", "--json"]
    )
    assert rc_list_filtered == 0
    out_list_filtered = json.loads(capsys.readouterr().out)
    assert out_list_filtered["total"] == 0

    rc_ev = app_main(
        ["run", "events", "--local", "--run-id", pe_run_id, "--role", "PUBLIC", "--json"]
    )
    assert rc_ev == 0
    out_ev = json.loads(capsys.readouterr().out)
    assert out_ev["event_count"] == 9
    assert out_ev["events"][0]["layer1_story_banner"]["headline"].startswith("[REDACTED ")

    rc_ev_cursor = app_main(
        [
            "run",
            "events",
            "--local",
            "--run-id",
            pe_run_id,
            "--last-event-id",
            "evt-pe-005",
            "--json",
        ]
    )
    assert rc_ev_cursor == 0
    out_ev_cursor = json.loads(capsys.readouterr().out)
    assert out_ev_cursor["event_count"] == 4
    assert out_ev_cursor["events"][0]["event_id"] == "evt-pe-006"

    rc_abort_missing = app_main(["run", "abort", "--local", "--run-id", "run-missing", "--json"])
    assert rc_abort_missing == 1
    out_abort_missing = json.loads(capsys.readouterr().out)
    assert out_abort_missing["detail"] == "Run 'run-missing' not found."

    # 7. hold --local --json (list, approve, & missing run_id)
    rc_hold_list = app_main(["hold", "list", "--local", "--run-id", pe_run_id, "--json"])
    assert rc_hold_list == 0
    out_hold_list = json.loads(capsys.readouterr().out)
    assert len(out_hold_list["active_holds"]) == 1

    rc_hold_missing = app_main(["hold", "approve", "--local", "--run-id", "run-missing", "--json"])
    assert rc_hold_missing == 1
    out_hold_missing = json.loads(capsys.readouterr().out)
    assert out_hold_missing["detail"] == "Run 'run-missing' not found."

    rc_hold_approve = app_main(
        [
            "hold",
            "approve",
            "--local",
            "--run-id",
            pe_run_id,
            "--hold-id",
            "hold-tier-u3-horizon-sovereign-vehicle",
            "--event-id",
            "evt-pe-004",
            "--candidate",
            "APEX_CO_I",
            "--notes",
            "Approved via local CLI test.",
            "--json",
        ]
    )
    assert rc_hold_approve == 0
    out_hold_approve = json.loads(capsys.readouterr().out)
    assert out_hold_approve["status"] == "ACCEPTED"
    assert out_hold_approve["updated_run_status"] == "COMPLETED"


def test_opendv_cli_missing_required_flags_return_exit_code_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify omitting required CLI flags returns exit code 2 with status=ERROR."""
    missing_flag_invocations = [
        ["verify", "--local", "--json"],
        ["verify", "--api-url", "http://testserver", "--json"],
        ["profile", "--local", "--json"],
        ["profile", "--api-url", "http://testserver", "--json"],
        ["compile", "--local", "--json"],
        ["compile", "--api-url", "http://testserver", "--json"],
        ["sandbox", "--local", "--json"],
        ["sandbox", "--api-url", "http://testserver", "--json"],
        ["run", "get", "--local", "--json"],
        ["run", "events", "--local", "--json"],
        ["run", "abort", "--local", "--json"],
        ["run", "create", "--local", "--json"],
        ["hold", "list", "--local", "--json"],
        ["hold", "approve", "--local", "--json"],
        ["pipeline", "validate", "--local", "--json"],
        ["pipeline", "run", "--local", "--json"],
        ["pipeline", "report", "--local", "--json"],
        ["pipeline", "validate", "--api-url", "http://testserver", "--json"],
        ["pipeline", "run", "--api-url", "http://testserver", "--json"],
        ["pipeline", "report", "--api-url", "http://testserver", "--json"],
        [
            "verify",
            "--local",
            "--contract",
            str(REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"),
            "--graph-a",
            "examples/private_equity/does_not_exist.yaml",
            "--json",
        ],
        [
            "verify",
            "--local",
            "--contract",
            str(REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"),
            "--graph-b",
            "examples/private_equity/does_not_exist.json",
            "--json",
        ],
    ]
    for argv in missing_flag_invocations:
        rc = app_main(argv)
        assert rc == 2, f"Expected exit code 2 for {argv}, got {rc}"
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ERROR"


def test_opendv_and_axi_dv_cli_remote_api_url_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv` and `axi-dv` subcommands in `--api-url` mode against the FastAPI ASGI app."""
    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)
    api_url = "http://testserver"
    pe_run_id = "run-pe-apex-horizon-20261030"

    # 1. axi-dv verify --api-url
    rc_verify = axi_dv_main(
        ["verify", "--api-url", api_url, "--run-id", pe_run_id, "--json"],
        transport=transport,
    )
    assert rc_verify == 0
    out_verify = json.loads(capsys.readouterr().out)
    assert out_verify["overall_passed"] is True

    rc_verify_bad_graph_a = axi_dv_main(
        [
            "verify",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--graph-a",
            "examples/private_equity/does_not_exist.yaml",
            "--json",
        ],
        transport=transport,
    )
    assert rc_verify_bad_graph_a == 1
    out_verify_bad_graph_a = json.loads(capsys.readouterr().out)
    assert out_verify_bad_graph_a["status"] == "ERROR"
    assert out_verify_bad_graph_a["status_code"] == 404

    # 2. opendv profile --api-url
    rc_prof = app_main(
        [
            "profile",
            "--api-url",
            api_url,
            "--project-id",
            "proj-apex-horizon-pe",
            "--run-id",
            pe_run_id,
            "--json",
        ],
        transport=transport,
    )
    assert rc_prof == 0
    out_prof = json.loads(capsys.readouterr().out)
    assert out_prof["mode"] == "remote"
    assert out_prof["workbench_view"]["view_id"] == "WB-02"

    # 3. opendv compile --api-url
    rc_comp = app_main(
        ["compile", "--api-url", api_url, "--run-id", pe_run_id, "--json"],
        transport=transport,
    )
    assert rc_comp == 0
    out_comp = json.loads(capsys.readouterr().out)
    assert out_comp["mode"] == "remote"
    assert out_comp["workbench_view"]["view_id"] == "WB-11"

    # 4. opendv sandbox --api-url (QUERY_D10B_VALUATION)
    rc_sbx = app_main(
        [
            "sandbox",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--query-type",
            "QUERY_D10B_VALUATION",
            "--cutoff",
            "2026-11-15 23:59:59",
            "--json",
        ],
        transport=transport,
    )
    assert rc_sbx == 0
    out_sbx = json.loads(capsys.readouterr().out)
    assert out_sbx["all_batches_passed"] is True
    nordic = next(r for r in out_sbx["rows"] if r["portco_natural_key"] == "529900T8BM49AURSDO55")
    assert float(nordic["ev_ebitda_multiple"]) == 14.50

    # 5. opendv run events --api-url with Last-Event-ID
    rc_events = app_main(
        [
            "run",
            "events",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--role",
            "AUDITOR_FINANCE",
            "--last-event-id",
            "evt-pe-007",
            "--json",
        ],
        transport=transport,
    )
    assert rc_events == 0
    out_events = json.loads(capsys.readouterr().out)
    assert "id: evt-pe-008" in out_events["sse_stream"]
    assert "id: evt-pe-009" in out_events["sse_stream"]
    assert "id: evt-pe-001" not in out_events["sse_stream"]
    assert "event: run_complete" in out_events["sse_stream"]

    # 6. opendv hold approve --api-url (auto-resolves single pending hold when --hold-id omitted)
    rc_hold = app_main(
        [
            "hold",
            "approve",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--candidate",
            "APEX_CO_I",
            "--notes",
            "Approved via remote CLI.",
            "--json",
        ],
        transport=transport,
    )
    assert rc_hold == 0
    out_hold = json.loads(capsys.readouterr().out)
    assert out_hold["status"] == "ACCEPTED"
    assert out_hold["updated_run_status"] == "COMPLETED"

    # 7. Double-approving the same hold over --api-url returns exit code 1 with HTTP 409 error
    rc_conflict = app_main(
        [
            "hold",
            "approve",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--hold-id",
            "hold-tier-u3-horizon-sovereign-vehicle",
            "--event-id",
            "evt-pe-004",
            "--json",
        ],
        transport=transport,
    )
    assert rc_conflict == 1
    out_conflict = json.loads(capsys.readouterr().out)
    assert out_conflict["status"] == "ERROR"
    assert out_conflict["status_code"] == 409

    # 8. Omitting --hold-id when no active holds are pending returns exit code 2
    rc_no_pending = app_main(
        [
            "hold",
            "approve",
            "--api-url",
            api_url,
            "--run-id",
            pe_run_id,
            "--json",
        ],
        transport=transport,
    )
    assert rc_no_pending == 2
    out_no_pending = json.loads(capsys.readouterr().out)
    assert out_no_pending["status"] == "ERROR"


def test_api_client_url_validation_and_methods(tmp_path: Path) -> None:
    """Verify `validate_api_url` rejects non-HTTP schemes and `OpenDVCliApiClient` methods work."""
    with pytest.raises(ValueError, match="Invalid --api-url"):
        validate_api_url("file:///etc/passwd")

    state = PlatformStateContainer(state_dir=tmp_path / "state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)

    with OpenDVCliApiClient(
        base_url="http://testserver",
        role="RESTRICTED_PII_CUSTODIAN",
        transport=transport,
    ) as client:
        assert client.get_health()["status"] == "ok"
        assert "antigravity" in client.get_capabilities()["harness_providers"]
        assert client.list_projects()["total"] >= 1
        assert client.get_project("proj-apex-horizon-pe")["project_id"] == "proj-apex-horizon-pe"
        assert client.list_runs()["total"] >= 1
        assert client.get_gates("run-pe-apex-horizon-20261030")["overall_passed"] is True
        assert client.get_sandbox("run-pe-apex-horizon-20261030")["run_id"] == (
            "run-pe-apex-horizon-20261030"
        )
        uploaded = client.upload_artifact({"content": {"cli": True}})
        fetched = client.get_artifact(uploaded["sha256"])
        assert fetched["content"] == {"cli": True}

        with pytest.raises(OpenDVCliApiError) as exc_info:
            client.get_run("run-missing")
        assert exc_info.value.status_code == 404


def test_verify_cli_rejected_and_ask_user_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv verify` and `axi-dv` reflect REJECT_WITH_TRACE and ASK_USER verdicts."""
    bad_contract = tmp_path / "broken_contract.yaml"
    bad_contract.write_text(
        "contract_version: '5.0'\n"
        "hubs:\n"
        "  - name: hub_broken\n"
        "    target_table: raw_vault.hub_broken\n"
        "    grain_statement: 'short grain'\n"
        "    business_keys: []\n"
        "links: []\n"
        "satellites: []\n",
        encoding="utf-8",
    )
    rc_bad = app_main(["--local", "--contract", str(bad_contract), "--json"])
    assert rc_bad == 1
    out_bad = json.loads(capsys.readouterr().out)
    assert out_bad["overall_passed"] is False
    assert out_bad["verdict"] == "REJECT-WITH-TRACE"
    assert out_bad["gate_scorecard"]["gate_1_contract_grain"] == "REJECT-WITH-TRACE"

    pe_contract_doc = yaml.safe_load(
        (REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml").read_text(
            encoding="utf-8"
        )
    )
    pe_contract_doc["unmapped_escalations"] = [
        {
            "bronze_table": "src_mystery",
            "columns": ["col_a"],
            "escalation_reason": "MISSING_ONTOLOGY_CONCEPT",
            "architect_question": "Map src_mystery to which hub?",
        }
    ]
    ask_contract = tmp_path / "ask_contract.yaml"
    ask_contract.write_text(yaml.safe_dump(pe_contract_doc), encoding="utf-8")
    rc_ask = axi_dv_main(["--local", "--contract", str(ask_contract), "--json"])
    assert rc_ask == 0
    out_ask = json.loads(capsys.readouterr().out)
    assert out_ask["overall_passed"] is True
    assert out_ask["verdict"] == "ASK-USER"
    assert out_ask["gate_scorecard"]["gate_1_contract_grain"] == "ASK-USER"


def test_opendv_pipeline_cli_local_and_remote_modes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `opendv pipeline validate`, `run --local`, `run --api-url`, and `report`."""
    from opendv_agent_harness.adapters.deterministic_adapter import (
        build_deterministic_agy_sdk_shim,
    )
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
    from opendv_service import set_pipeline_runner_override

    def shim_runner(*args: Any, **kwargs: Any) -> Any:
        return run_agentic_vault_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    monkeypatch.setattr("opendv_service.seams._CURRENT_PIPELINE_RUNNER", shim_runner)
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # Negative check: --provider deterministic fails with non-zero exit code
    rc_bad_provider = app_main(
        [
            "pipeline",
            "validate",
            "--local",
            "--input",
            sample_input,
            "--provider",
            "deterministic",
            "--json",
        ]
    )
    assert rc_bad_provider != 0
    capsys.readouterr()

    rc_bad_run_provider = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            sample_input,
            "--provider",
            "deterministic",
            "--json",
        ]
    )
    assert rc_bad_run_provider != 0
    capsys.readouterr()

    # 1. opendv pipeline validate --local
    rc_val_local = app_main(
        [
            "pipeline",
            "validate",
            "--local",
            "--input",
            sample_input,
            "--harness-config",
            sample_harness,
            "--provider",
            "agy",
            "--hold-policy",
            "auto_approve_top_candidate",
            "--json",
        ]
    )
    assert rc_val_local == 0
    out_val_local = json.loads(capsys.readouterr().out)
    assert out_val_local["stage"] == "INPUT_VALIDATION"
    assert out_val_local["passed"] is True

    # 1b. opendv pipeline validate --local with harness_config_claude_cli.yaml & claude-cli
    sample_harness_claude_cli = (
        "examples/pipeline_samples/global_commerce_vault/harness_config_claude_cli.yaml"
    )
    rc_val_claude_cli = app_main(
        [
            "pipeline",
            "validate",
            "--local",
            "--input",
            sample_input,
            "--harness-config",
            sample_harness_claude_cli,
            "--provider",
            "claude-cli",
            "--hold-policy",
            "auto_approve_top_candidate",
            "--json",
        ]
    )
    assert rc_val_claude_cli == 0
    out_val_claude_cli = json.loads(capsys.readouterr().out)
    assert out_val_claude_cli["stage"] == "INPUT_VALIDATION"
    assert out_val_claude_cli["passed"] is True

    # 2. opendv pipeline run --local with --output-dir
    local_out_dir = tmp_path / "cli_local_pipeline_out"
    rc_run_local = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            sample_input,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(local_out_dir),
            "--provider",
            "agy",
            "--hold-policy",
            "auto_approve_top_candidate",
            "--json",
        ]
    )
    assert rc_run_local == 0
    out_run_local = json.loads(capsys.readouterr().out)
    assert out_run_local["status"] == "COMPLETED"
    assert out_run_local["overall_passed"] is True
    assert len(out_run_local["wave_reports"]) == 4
    assert (local_out_dir / "pipeline_report.json").is_file()

    # 3. opendv pipeline report --local --report-path and --run-id (with RBAC redaction)
    rc_rep_local = app_main(
        [
            "pipeline",
            "report",
            "--local",
            "--report-path",
            str(local_out_dir / "pipeline_report.json"),
            "--json",
        ]
    )
    assert rc_rep_local == 0
    out_rep_local = json.loads(capsys.readouterr().out)
    assert out_rep_local["run_id"] == out_run_local["run_id"]
    assert out_rep_local["overall_passed"] is True

    rc_rep_pe_modeler = app_main(
        [
            "pipeline",
            "report",
            "--local",
            "--run-id",
            "run-pe-apex-horizon-20261030",
            "--role",
            "INTERNAL_MODELER",
            "--json",
        ]
    )
    assert rc_rep_pe_modeler == 0
    out_rep_pe_modeler = json.loads(capsys.readouterr().out)
    assert not out_rep_pe_modeler["observability_events"][0]["layer1_story_banner"][
        "headline"
    ].startswith("[REDACTED")

    rc_rep_pe_public = app_main(
        [
            "pipeline",
            "report",
            "--local",
            "--run-id",
            "run-pe-apex-horizon-20261030",
            "--role",
            "PUBLIC",
            "--json",
        ]
    )
    assert rc_rep_pe_public == 0
    out_rep_pe_public = json.loads(capsys.readouterr().out)
    assert out_rep_pe_public["observability_events"][0]["layer1_story_banner"][
        "headline"
    ].startswith("[REDACTED")

    bad_spec_file = tmp_path / "invalid_pipeline_spec.yaml"
    bad_spec_file.write_text("pipeline_id: bad-only\n", encoding="utf-8")
    rc_val_invalid = app_main(
        [
            "pipeline",
            "validate",
            "--local",
            "--input",
            str(bad_spec_file),
            "--json",
        ]
    )
    assert rc_val_invalid == 1
    out_val_invalid = json.loads(capsys.readouterr().out)
    assert out_val_invalid["stage"] == "INPUT_VALIDATION"
    assert out_val_invalid["passed"] is False
    assert out_val_invalid["diagnostics"][0]["code"] == "INPUT_SPEC_SCHEMA_INVALID"

    # 4. opendv pipeline validate / run / report over --api-url
    state = PlatformStateContainer(state_dir=tmp_path / "remote_state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)
    api_url = "http://testserver"

    rc_val_remote = app_main(
        [
            "pipeline",
            "validate",
            "--api-url",
            api_url,
            "--input",
            sample_input,
            "--harness-config",
            sample_harness,
            "--json",
        ],
        transport=transport,
    )
    assert rc_val_remote == 0
    out_val_remote = json.loads(capsys.readouterr().out)
    assert out_val_remote["passed"] is True

    remote_out_dir = tmp_path / "cli_remote_pipeline_out"
    rc_run_remote = app_main(
        [
            "pipeline",
            "run",
            "--api-url",
            api_url,
            "--input",
            sample_input,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(remote_out_dir),
            "--provider",
            "agy",
            "--hold-policy",
            "auto_approve_top_candidate",
            "--json",
        ],
        transport=transport,
    )
    assert rc_run_remote == 0
    out_run_remote = json.loads(capsys.readouterr().out)
    remote_run_id = out_run_remote["run_id"]
    assert out_run_remote["status"] == "COMPLETED"
    assert out_run_remote["overall_passed"] is True
    assert len(out_run_remote["wave_reports"]) == 4

    rc_rep_remote = app_main(
        [
            "pipeline",
            "report",
            "--api-url",
            api_url,
            "--run-id",
            remote_run_id,
            "--json",
        ],
        transport=transport,
    )
    assert rc_rep_remote == 0
    out_rep_remote = json.loads(capsys.readouterr().out)
    assert out_rep_remote["run_id"] == remote_run_id
    assert out_rep_remote["overall_passed"] is True
    assert len(out_rep_remote["wave_reports"]) == 4


def test_opendv_pipeline_cli_incremental_initial_contract_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `--initial-contract` flag on `opendv pipeline validate` and `run`."""
    from opendv_agent_harness.adapters.deterministic_adapter import (
        build_deterministic_agy_sdk_shim,
    )
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
    from opendv_service import set_pipeline_runner_override

    def shim_runner(*args, **kwargs):
        return run_agentic_vault_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    monkeypatch.setattr("opendv_service.seams._CURRENT_PIPELINE_RUNNER", shim_runner)
    wave0_spec = "examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml"
    wave1_spec = "examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # 1. Run Wave 0 Cold Start locally
    w0_out_dir = tmp_path / "wave0_out"
    rc_w0 = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            wave0_spec,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(w0_out_dir),
            "--provider",
            "agy",
            "--json",
        ]
    )
    assert rc_w0 == 0
    out_w0 = json.loads(capsys.readouterr().out)
    assert out_w0["status"] == "COMPLETED"
    assert out_w0["overall_passed"] is True
    w0_contract = w0_out_dir / "final_contract.yaml"
    assert w0_contract.is_file()

    # 2. Validate Wave 1 Incremental Billing locally with --initial-contract
    rc_w1_val_local = app_main(
        [
            "pipeline",
            "validate",
            "--local",
            "--input",
            wave1_spec,
            "--harness-config",
            sample_harness,
            "--initial-contract",
            str(w0_contract),
            "--provider",
            "agy",
            "--json",
        ]
    )
    assert rc_w1_val_local == 0
    out_w1_val_local = json.loads(capsys.readouterr().out)
    assert out_w1_val_local["passed"] is True

    # 3. Run Wave 1 Incremental Billing locally with --initial-contract
    w1_out_dir = tmp_path / "wave1_out"
    rc_w1_run_local = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            wave1_spec,
            "--harness-config",
            sample_harness,
            "--initial-contract",
            str(w0_contract),
            "--output-dir",
            str(w1_out_dir),
            "--provider",
            "agy",
            "--json",
        ]
    )
    assert rc_w1_run_local == 0
    out_w1_run_local = json.loads(capsys.readouterr().out)
    assert out_w1_run_local["status"] == "COMPLETED"
    assert out_w1_run_local["overall_passed"] is True
    w1_contract = w1_out_dir / "final_contract.yaml"
    assert w1_contract.is_file()
    w1_contract_doc = yaml.safe_load(w1_contract.read_text(encoding="utf-8"))
    hub_names = {h["name"] for h in w1_contract_doc["hubs"]}
    assert {"hub_customer", "hub_order", "hub_invoice"}.issubset(hub_names)

    # 4. Validate and run Wave 1 over --api-url with --initial-contract
    state = PlatformStateContainer(state_dir=tmp_path / "remote_inc_state")
    app = create_app(platform_state=state)
    transport = _AsgiSyncBridgeTransport(app)
    api_url = "http://testserver"

    rc_w1_val_remote = app_main(
        [
            "pipeline",
            "validate",
            "--api-url",
            api_url,
            "--input",
            wave1_spec,
            "--harness-config",
            sample_harness,
            "--initial-contract",
            str(w0_contract),
            "--provider",
            "agy",
            "--json",
        ],
        transport=transport,
    )
    assert rc_w1_val_remote == 0
    out_w1_val_remote = json.loads(capsys.readouterr().out)
    assert out_w1_val_remote["passed"] is True

    w1_remote_out_dir = tmp_path / "wave1_remote_out"
    rc_w1_run_remote = app_main(
        [
            "pipeline",
            "run",
            "--api-url",
            api_url,
            "--input",
            wave1_spec,
            "--harness-config",
            sample_harness,
            "--initial-contract",
            str(w0_contract),
            "--output-dir",
            str(w1_remote_out_dir),
            "--provider",
            "agy",
            "--json",
        ],
        transport=transport,
    )
    assert rc_w1_run_remote == 0
    out_w1_run_remote = json.loads(capsys.readouterr().out)
    assert out_w1_run_remote["status"] == "COMPLETED"
    assert out_w1_run_remote["overall_passed"] is True


def test_wp24_opendv_pipeline_run_live_stage_progress_and_token_streaming(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify WP-24 live stage progress & token streaming on `opendv pipeline run`."""
    from opendv_agent_harness.adapters.deterministic_adapter import (
        build_deterministic_agy_sdk_shim,
    )
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
    from opendv_service import set_pipeline_runner_override

    def shim_runner(*args, **kwargs):
        return run_agentic_vault_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    monkeypatch.setattr("opendv_service.seams._CURRENT_PIPELINE_RUNNER", shim_runner)
    wave0_spec = "examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # 1. Human mode (no --json): live [Wave 0][STAGE 00..07] progress & streamed tokens go to stdout
    out_human_dir = tmp_path / "wp24_human_out"
    rc_human = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            wave0_spec,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(out_human_dir),
            "--provider",
            "agy",
        ]
    )
    assert rc_human == 0
    captured_human = capsys.readouterr()
    assert "[Wave 0][STAGE 00] STAGE_00_ORCHESTRATION" in captured_human.out
    assert "[Wave 0][STAGE 01] STAGE_01_SCOUT_SOURCE" in captured_human.out
    assert "[Wave 0][STAGE 07] STAGE_07_DETERMINISTIC_COMPILER" in captured_human.out
    assert "Profiled 2 source(s) (15 columns)" in captured_human.out
    assert "[opendv pipeline] status=COMPLETED" in captured_human.out

    # 2. JSON mode (--json + default --stream + --stream-tokens):
    #    stdout remains 100% pure JSON while live progress & tokens stream to stderr
    out_json_dir = tmp_path / "wp24_json_out"
    rc_json = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--input",
            wave0_spec,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(out_json_dir),
            "--provider",
            "agy",
            "--stream-tokens",
            "--json",
        ]
    )
    assert rc_json == 0
    captured_json = capsys.readouterr()
    parsed_json = json.loads(captured_json.out)
    assert parsed_json["status"] == "COMPLETED"
    assert parsed_json["overall_passed"] is True
    assert "[Wave 0][STAGE 00] STAGE_00_ORCHESTRATION" in captured_json.err
    assert "[Wave 0][STAGE 01] STAGE_01_SCOUT_SOURCE" in captured_json.err
    assert "[Wave 0][STAGE 07] STAGE_07_DETERMINISTIC_COMPILER" in captured_json.err

    # 3. --no-stream suppresses live stage progress lines
    out_nostream_dir = tmp_path / "wp24_nostream_out"
    rc_nostream = app_main(
        [
            "pipeline",
            "run",
            "--local",
            "--no-stream",
            "--input",
            wave0_spec,
            "--harness-config",
            sample_harness,
            "--output-dir",
            str(out_nostream_dir),
            "--provider",
            "agy",
            "--json",
        ]
    )
    assert rc_nostream == 0
    captured_nostream = capsys.readouterr()
    assert json.loads(captured_nostream.out)["overall_passed"] is True
    assert "[STAGE " not in captured_nostream.err
