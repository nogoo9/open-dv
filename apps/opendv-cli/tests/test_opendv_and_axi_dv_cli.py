"""Integration tests for spec-driven standalone `opendv` and `axi-dv` CLI commands (Decision D6)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_cli import app_main, axi_dv_main, build_parser
from opendv_core import find_workspace_root
from opendv_service import set_pipeline_runner_override


@pytest.fixture(autouse=True)
def _clean_pipeline_runner_override() -> Generator[None, None, None]:
    yield
    set_pipeline_runner_override(None)


def test_opendv_cli_standalone_subcommands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify spec-driven standalone `opendv` subcommands
    (`verify`, `profile`, `compile`, `sandbox`, `run`, `hold`).
    """
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    pe_ir = "examples/private_equity/pe_canonical_ir.json"

    # 1. verify --contract ... --json with explicit contract, graph-a, graph-b
    rc = app_main(
        [
            "verify",
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

    # Also test backward compatibility: --local is silently ignored and succeeds
    rc_local = app_main(
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
    assert rc_local == 0
    capsys.readouterr()

    # 2. axi-dv default verify with --toon
    rc_toon = axi_dv_main(
        [
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

    # 3. profile --json (both --graph-b and --source-csv)
    rc_prof = app_main(
        [
            "profile",
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

    # 4. compile --json (with --canonical-ir and dynamic lowering without --canonical-ir)
    rc_comp = app_main(
        [
            "compile",
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
    assert out_comp["hubs_compiled"] >= 4
    assert out_comp["links_compiled"] >= 5
    assert out_comp["satellites_compiled"] >= 5
    assert len(out_comp["topological_load_order"]) >= 15
    assert len(out_comp["sql_zones"]) >= 4

    rc_comp_dynamic = app_main(["compile", "--contract", pe_contract, "--json"])
    assert rc_comp_dynamic == 0
    out_comp_dynamic = json.loads(capsys.readouterr().out)
    assert out_comp_dynamic["status"] == "PASS"
    assert out_comp_dynamic["hubs_compiled"] == out_comp["hubs_compiled"]

    # 5. sandbox --json (4 adversarial batches and bi-temporal queries)
    rc_sbx_batches = app_main(
        [
            "sandbox",
            "--contract",
            pe_contract,
            "--canonical-ir",
            pe_ir,
            "--query-type",
            "ADVERSARIAL_BATCHES_1_TO_4",
            "--json",
        ]
    )
    assert rc_sbx_batches == 0
    out_sbx_batches = json.loads(capsys.readouterr().out)
    assert out_sbx_batches["mode"] == "local"
    assert out_sbx_batches["all_batches_passed"] is True
    assert len(out_sbx_batches["batch_report"]["batches"]) == 4

    rc_sbx_pit = app_main(
        [
            "sandbox",
            "--contract",
            pe_contract,
            "--query-type",
            "QUERY_D10_PIT",
            "--cutoff",
            "2026-11-15 23:59:59",
            "--json",
        ]
    )
    assert rc_sbx_pit == 0
    out_sbx_pit = json.loads(capsys.readouterr().out)
    assert out_sbx_pit["query_type"] == "QUERY_D10_PIT"
    assert len(out_sbx_pit["query_d10_rows"]) >= 2

    # 6. run list, get, create, events
    rc_run_list = app_main(["run", "list", "--project-id", "proj-apex-horizon-pe", "--json"])
    assert rc_run_list == 0
    out_run_list = json.loads(capsys.readouterr().out)
    assert out_run_list["mode"] == "local"
    assert out_run_list["total"] >= 1

    rc_run_get = app_main(["run", "get", "--run-id", "run-pe-apex-horizon-20261030", "--json"])
    assert rc_run_get == 0
    out_run_get = json.loads(capsys.readouterr().out)
    assert out_run_get["run_id"] == "run-pe-apex-horizon-20261030"

    rc_run_events = app_main(
        ["run", "events", "--run-id", "run-pe-apex-horizon-20261030", "--json"]
    )
    assert rc_run_events == 0
    out_run_events = json.loads(capsys.readouterr().out)
    assert out_run_events["event_count"] >= 9

    # 7. hold list
    rc_hold_list = app_main(["hold", "list", "--run-id", "run-pe-apex-horizon-20261030", "--json"])
    assert rc_hold_list == 0
    out_hold_list = json.loads(capsys.readouterr().out)
    assert out_hold_list["mode"] == "local"


def test_opendv_cli_missing_required_flags_return_exit_code_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify missing required flags return exit code 2 and formatted error message."""
    # verify without --contract
    rc_verify = app_main(["verify", "--json"])
    assert rc_verify == 2
    out_v = json.loads(capsys.readouterr().out)
    assert out_v["status"] == "ERROR"

    # compile without --contract
    rc_comp = app_main(["compile", "--json"])
    assert rc_comp == 2
    out_c = json.loads(capsys.readouterr().out)
    assert out_c["status"] == "ERROR"

    # profile without --source-csv or --graph-b
    rc_prof = app_main(["profile", "--json"])
    assert rc_prof == 2
    out_p = json.loads(capsys.readouterr().out)
    assert out_p["status"] == "ERROR"

    # sandbox without --contract or --run-id
    rc_sbx = app_main(["sandbox", "--json"])
    assert rc_sbx == 2
    out_s = json.loads(capsys.readouterr().out)
    assert out_s["status"] == "ERROR"


def test_api_url_flag_excised(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --api-url flag is excised from opendv and rejected with exit code 2 (Decision D6)."""
    parser = build_parser(prog="opendv")
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["verify", "--api-url", "http://localhost:8000"])
    assert exc_info.value.code == 2


def test_subprocess_standalone_isolation() -> None:
    """Subprocess execution test asserting zero fastapi/opendv_platform_api in sys.modules
    (Decision D6).
    """
    code = (
        "import sys\n"
        "from opendv_cli.main import app_main\n"
        "rc = app_main([\n"
        "    'verify',\n"
        "    '--contract', 'examples/private_equity/dv_pe_model_contract.yaml',\n"
        "    '--json',\n"
        "])\n"
        "assert rc == 0, f'verify returned {rc}'\n"
        "banned = {'fastapi', 'starlette', 'opendv_platform_api', 'httpx'}\n"
        "loaded_banned = banned.intersection(sys.modules.keys())\n"
        "assert not loaded_banned, f'Banned modules loaded: {loaded_banned}'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_verify_cli_rejected_and_ask_user_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv verify` and `axi-dv` reflect REJECT_WITH_TRACE and ASK_USER verdicts."""
    repo_root = find_workspace_root()

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
    rc_bad = app_main(["verify", "--contract", str(bad_contract), "--json"])
    assert rc_bad == 1
    out_bad = json.loads(capsys.readouterr().out)
    assert out_bad["overall_passed"] is False
    assert out_bad["verdict"] == "REJECT-WITH-TRACE"
    assert out_bad["gate_scorecard"]["gate_1_contract_grain"] == "REJECT-WITH-TRACE"

    pe_contract_doc = yaml.safe_load(
        (repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml").read_text(
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
    rc_ask = axi_dv_main(["--contract", str(ask_contract), "--json"])
    assert rc_ask == 0
    out_ask = json.loads(capsys.readouterr().out)
    assert out_ask["overall_passed"] is True
    assert out_ask["verdict"] == "ASK-USER"
    assert out_ask["gate_scorecard"]["gate_1_contract_grain"] == "ASK-USER"


def test_opendv_pipeline_cli_standalone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `opendv pipeline validate`, `run`, and `report` standalone commands."""
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
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # Negative check: --provider deterministic fails with non-zero exit code
    rc_bad_provider = app_main(
        [
            "pipeline",
            "validate",
            "--input",
            sample_input,
            "--provider",
            "deterministic",
            "--json",
        ]
    )
    assert rc_bad_provider != 0
    capsys.readouterr()

    # 1. opendv pipeline validate
    rc_val = app_main(
        [
            "pipeline",
            "validate",
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
    assert rc_val == 0
    out_val = json.loads(capsys.readouterr().out)
    assert out_val["stage"] == "INPUT_VALIDATION"
    assert out_val["passed"] is True

    # 2. opendv pipeline run
    local_out_dir = tmp_path / "cli_pipeline_out"
    rc_run = app_main(
        [
            "pipeline",
            "run",
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
    assert rc_run == 0
    out_run = json.loads(capsys.readouterr().out)
    assert out_run["status"] == "COMPLETED"
    assert out_run["overall_passed"] is True
    assert (local_out_dir / "pipeline_report.json").is_file()

    # 3. opendv pipeline report
    rc_rep = app_main(
        [
            "pipeline",
            "report",
            "--report-path",
            str(local_out_dir / "pipeline_report.json"),
            "--json",
        ]
    )
    assert rc_rep == 0
    out_rep = json.loads(capsys.readouterr().out)
    assert out_rep["run_id"] == out_run["run_id"]
    assert out_rep["overall_passed"] is True


def test_opendv_pipeline_cli_incremental_initial_contract_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify `--initial-contract` flag on `opendv pipeline validate` and `run`."""
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
    wave0_spec = "examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml"
    wave1_spec = "examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # 1. Run Wave 0 Cold Start
    w0_out_dir = tmp_path / "wave0_out"
    rc_w0 = app_main(
        [
            "pipeline",
            "run",
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

    # 2. Validate Wave 1 Incremental Billing with --initial-contract
    rc_w1_val = app_main(
        [
            "pipeline",
            "validate",
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
    assert rc_w1_val == 0
    out_w1_val = json.loads(capsys.readouterr().out)
    assert out_w1_val["passed"] is True

    # 3. Run Wave 1 Incremental Billing with --initial-contract
    w1_out_dir = tmp_path / "wave1_out"
    rc_w1_run = app_main(
        [
            "pipeline",
            "run",
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
    assert rc_w1_run == 0
    out_w1_run = json.loads(capsys.readouterr().out)
    assert out_w1_run["status"] == "COMPLETED"
    assert out_w1_run["overall_passed"] is True
    w1_contract = w1_out_dir / "final_contract.yaml"
    assert w1_contract.is_file()
    w1_contract_doc = yaml.safe_load(w1_contract.read_text(encoding="utf-8"))
    hub_names = {h["name"] for h in w1_contract_doc["hubs"]}
    assert {"hub_customer", "hub_order", "hub_invoice"}.issubset(hub_names)


def test_wp24_opendv_pipeline_run_live_stage_progress_and_token_streaming(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify live stage progress & token streaming on `opendv pipeline run`."""
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
    wave0_spec = "examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    # 1. Human mode (no --json): live [Wave 0] progress lines
    out_human_dir = tmp_path / "wp24_human_out"
    rc_human = app_main(
        [
            "pipeline",
            "run",
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
    assert "[Wave 0]" in captured_human.out
    assert "[opendv pipeline] status=COMPLETED" in captured_human.out

    # 2. JSON mode (--json): stdout is pure JSON
    out_json_dir = tmp_path / "wp24_json_out"
    rc_json = app_main(
        [
            "pipeline",
            "run",
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
