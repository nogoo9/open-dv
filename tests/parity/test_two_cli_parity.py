"""tests/parity/test_two_cli_parity.py — T15 Two-CLI Consistency & Parity Suite.

Verifies:
1. `--dump-cli-tree` Canonical Tree Equality (RFC 8785 JCS, error maps, standalone filter).
2. Command Output Parity across STANDALONE_CLI vs CLIENT_CLI on profile, compile, verify, sandbox.
3. Error Exit Code Parity (0 for OK, 1 for verification failure, 2 for syntax/arg errors).
4. Pre-rendered summary parity across formatters.
5. CLI Reference documentation drift check against schemas/v1/cli-spec.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_contracts import ErrorCode
from opendv_contracts.jcs import dumps_jcs
from opendv_service.cli_spec.docs_generator import generate_cli_reference_markdown
from tests.parity.golden_fixtures import build_gt03_01_dropped_hub
from tests.parity.normalize import normalize_for_parity
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
PE_CONTRACT_PATH = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"


@pytest.mark.parity
def test_dump_cli_tree_canonical_equality(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify opendv and opendvctl emit bit-for-bit identical command trees
    in RFC 8785 canonical JCS.
    """
    # 1. Standalone Python CLI tree
    code_py, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_py == 0, f"opendv --dump-cli-tree failed with code {code_py}"
    py_tree = json.loads(stdout_py.strip())

    # 2. Bun Platform Client CLI tree
    code_ts, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_ts == 0, f"opendvctl --dump-cli-tree failed with code {code_ts}"
    ts_tree = json.loads(stdout_ts.strip())

    # 3. Spec and contract versions
    assert py_tree["spec_version"] == ts_tree["spec_version"]
    assert py_tree["contract_version"] == ts_tree["contract_version"]

    # 4. Error maps: all domain error codes must match bit-for-bit
    assert len(py_tree["error_map"]) == len(ErrorCode)
    assert dumps_jcs(py_tree["error_map"]) == dumps_jcs(ts_tree["error_map"])

    # 5. Global options: client CLI injects --api-url; shared options match
    ts_opt_flags = [opt["flag"] for opt in ts_tree["global_options"]]
    py_opt_flags = [opt["flag"] for opt in py_tree["global_options"]]
    assert "--api-url" in ts_opt_flags
    assert "--api-url" not in py_opt_flags

    ts_shared_opts = [o for o in ts_tree["global_options"] if o["flag"] != "--api-url"]
    py_shared_opts = [o for o in py_tree["global_options"] if o["flag"] != "--api-url"]
    assert dumps_jcs(py_shared_opts) == dumps_jcs(ts_shared_opts)

    # 6. Shared commands: leaf commands match bit-for-bit in JCS; standalone_only excluded in ts
    py_cmds: dict[str, Any] = py_tree["commands"]
    ts_cmds: dict[str, Any] = {c["name"]: c for c in ts_tree["commands"]}
    assert "serve" in py_cmds
    assert py_cmds["serve"]["availability"] == "standalone_only"
    assert "serve" not in ts_cmds
    py_shared_cmd_names = {
        name for name, cmd in py_cmds.items() if cmd.get("availability") != "standalone_only"
    }
    assert py_shared_cmd_names == set(ts_cmds.keys())

    for leaf in ["profile", "compile", "verify", "sandbox"]:
        assert dumps_jcs(py_cmds[leaf]) == dumps_jcs(ts_cmds[leaf]), (
            f"Command definition mismatch for '{leaf}'"
        )

    # 7. Pipeline subcommands: report is standalone_only; validate and run match
    py_pipe_subs = py_cmds["pipeline"]["subcommands"]
    ts_pipe_subs = {s["name"]: s for s in ts_cmds["pipeline"]["subcommands"]}

    assert "report" in py_pipe_subs
    assert py_pipe_subs["report"]["availability"] == "standalone_only"
    assert "report" not in ts_pipe_subs

    for sub in ["validate", "run"]:
        assert dumps_jcs(py_pipe_subs[sub]) == dumps_jcs(ts_pipe_subs[sub]), (
            f"Pipeline subcommand mismatch for '{sub}'"
        )

    # 8. Run, Hold, and Config subcommands match bit-for-bit
    for parent in ["run", "hold", "config"]:
        py_subs = py_cmds[parent]["subcommands"]
        ts_subs = {s["name"]: s for s in ts_cmds[parent]["subcommands"]}
        assert set(py_subs.keys()) == set(ts_subs.keys())
        for sub_name in py_subs:
            assert dumps_jcs(py_subs[sub_name]) == dumps_jcs(ts_subs[sub_name]), (
                f"{parent} subcommand mismatch for '{sub_name}'"
            )


@pytest.mark.parity
@pytest.mark.parametrize("operation", ["compile", "verify", "sandbox"])
def test_two_cli_command_output_parity(
    operation: str,
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify bit-for-bit normalized output parity across STANDALONE_CLI and CLIENT_CLI."""
    op_method_cli = getattr(cli_transport, operation)
    op_method_client = getattr(client_cli_transport, operation)

    res_cli = op_method_cli(contract=PE_CONTRACT_PATH)
    res_client = op_method_client(contract=PE_CONTRACT_PATH)

    norm_cli = normalize_for_parity(res_cli, path_root=str(tmp_path))
    norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

    assert norm_cli == norm_client, (
        f"Parity divergence on '{operation}':\nSTANDALONE: {norm_cli}\nCLIENT:     {norm_client}"
    )


@pytest.mark.parity
def test_two_cli_profile_output_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify profile command parity across STANDALONE_CLI and CLIENT_CLI."""
    sample_csv = tmp_path / "bronze_customers.csv"
    sample_csv.write_text(
        "customer_id,customer_name,country_code,annual_spend\n"
        "C001,Alpha Capital,USA,1500000.50\n"
        "C002,Beta Ventures,GBR,2300000.00\n"
        "C003,Gamma Holdings,DEU,850000.75\n",
        encoding="utf-8",
    )

    spec = {"source_csv": str(sample_csv.resolve())}
    res_cli = cli_transport.profile(spec)
    res_client = client_cli_transport.profile(spec)

    norm_cli = normalize_for_parity(res_cli, path_root=str(tmp_path))
    norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

    assert norm_cli == norm_client, (
        f"Parity divergence on 'profile':\nSTANDALONE: {norm_cli}\nCLIENT:     {norm_client}"
    )


@pytest.mark.parity
def test_two_cli_exit_code_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify strict exit code parity across 0 (OK), 1 (verification rejection),
    and 2 (syntax/arg errors).
    """
    # 1. Exit code 0 on clean execution
    code_cli_0, _, _ = cli_transport.invoke_raw(
        ["compile", "--contract", str(PE_CONTRACT_PATH), "--json"]
    )
    code_client_0, _, _ = client_cli_transport.invoke_raw(
        ["compile", "--contract", str(PE_CONTRACT_PATH), "--json"]
    )
    assert code_cli_0 == 0
    assert code_client_0 == 0

    # 2. Exit code 1 on verification failure (DV-EVO-002 dropped hub violation)
    base_contract = yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8"))
    bad_contract = build_gt03_01_dropped_hub(base_contract)
    bad_contract_file = tmp_path / "bad_dropped_hub_contract.yaml"
    bad_contract_file.write_text(yaml.safe_dump(bad_contract), encoding="utf-8")

    code_cli_1, _, _ = cli_transport.invoke_raw(
        [
            "verify",
            "--contract",
            str(bad_contract_file.resolve()),
            "--prior-contract",
            str(PE_CONTRACT_PATH.resolve()),
            "--json",
        ]
    )
    code_client_1, _, _ = client_cli_transport.invoke_raw(
        [
            "verify",
            "--contract",
            str(bad_contract_file.resolve()),
            "--prior-contract",
            str(PE_CONTRACT_PATH.resolve()),
            "--json",
        ]
    )
    assert code_cli_1 == 1
    assert code_client_1 == 1

    # 3. Exit code 2 on unknown option syntax error
    code_cli_2a, _, _ = cli_transport.invoke_raw(["verify", "--non-existent-flag-xyz"])
    code_client_2a, _, _ = client_cli_transport.invoke_raw(["verify", "--non-existent-flag-xyz"])
    assert code_cli_2a == 2
    assert code_client_2a == 2

    # 4. Exit code 2 on missing required argument
    code_cli_2b, _, _ = cli_transport.invoke_raw(["compile"])
    code_client_2b, _, _ = client_cli_transport.invoke_raw(["compile"])
    assert code_cli_2b == 2
    assert code_client_2b == 2


@pytest.mark.parity
def test_two_cli_rendered_text_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify pre-rendered human-readable report formatting between standalone and client CLIs."""
    _, text_cli, _ = cli_transport.invoke_raw(
        ["compile", "--contract", str(PE_CONTRACT_PATH), "--format", "text"]
    )
    _, text_client, _ = client_cli_transport.invoke_raw(
        ["compile", "--contract", str(PE_CONTRACT_PATH), "--format", "text"]
    )

    # Core pre-rendered text report is identical in both CLIs
    assert "Open-DV 4-Zone SQL Compilation Report" in text_cli
    assert "Open-DV 4-Zone SQL Compilation Report" in text_client
    assert "Compiled Constructs: 4 Hubs, 5 Links, 11 Satellites" in text_cli
    assert "Compiled Constructs: 4 Hubs, 5 Links, 11 Satellites" in text_client
    assert "Topological Load Order" in text_cli
    assert "Topological Load Order" in text_client


@pytest.mark.parity
def test_cli_reference_docs_drift() -> None:
    """CI Drift Check: verify docs/16-cli-reference.md strictly matches generator output."""
    doc_path = REPO_ROOT / "docs" / "16-cli-reference.md"
    assert doc_path.is_file(), f"CLI reference doc missing: {doc_path}"

    committed_doc = doc_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    generated_doc = generate_cli_reference_markdown().replace("\r\n", "\n")

    assert committed_doc == generated_doc, (
        "Documentation drift detected in docs/16-cli-reference.md! "
        "Run 'uv run python scripts/generate_cli_reference.py' to synchronize."
    )
