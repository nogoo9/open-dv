"""Automated socket-blocking standalone tests for opendv CLI (T15, Decision D6, REQ-CLI-02)."""

from __future__ import annotations

import inspect
import json
import socket
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from opendv_cli import app_main, axi_dv_main
from opendv_contracts.jcs import dumps_jcs

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def block_network_sockets() -> Generator[None, None, None]:
    """Block all external network connections while allowing internal asyncio process self-pipes."""
    orig_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        stack = inspect.stack()
        # Allow internal asyncio loop self-pipe on Windows (_fallback_socketpair)
        if any(
            frame.function in ("socketpair", "_fallback_socketpair", "_make_self_pipe")
            for frame in stack
        ):
            return orig_connect(self, address)
        raise RuntimeError(
            f"Network socket connection blocked in standalone mode: attempted connect to {address}"
        )

    socket.socket.connect = guarded_connect
    try:
        yield
    finally:
        socket.socket.connect = orig_connect


def test_network_connection_attempt_is_strictly_blocked(
    block_network_sockets: None,
) -> None:
    """Assert that genuine remote network connection attempts are blocked."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(
            RuntimeError, match="Network socket connection blocked in standalone mode"
        ):
            sock.connect(("127.0.0.1", 8420))
    finally:
        sock.close()


def test_opendv_standalone_capabilities_with_sockets_blocked(
    capsys: pytest.CaptureFixture[str],
    block_network_sockets: None,
) -> None:
    """Verify opendv runs verify, profile, compile, and sandbox with offline socket isolation."""
    pe_contract = str(REPO_ROOT / "examples/private_equity/dv_pe_model_contract.yaml")
    pe_graph_a = str(REPO_ROOT / "examples/private_equity/pe_graph_a_ontology.yaml")
    pe_graph_b = str(REPO_ROOT / "examples/private_equity/pe_graph_b_profile.json")
    pe_ir = str(REPO_ROOT / "examples/private_equity/pe_canonical_ir.json")

    # 1. verify
    rc_v = app_main(
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
    assert rc_v == 0
    out_v = json.loads(capsys.readouterr().out)
    assert out_v["overall_passed"] is True
    assert out_v["mode"] == "local"

    # 2. profile
    rc_p = app_main(["profile", "--graph-b", pe_graph_b, "--json"])
    assert rc_p == 0
    out_p = json.loads(capsys.readouterr().out)
    assert out_p["status"] == "PASS"
    assert out_p["mode"] == "local"

    # 3. compile
    rc_c = app_main(["compile", "--contract", pe_contract, "--canonical-ir", pe_ir, "--json"])
    assert rc_c == 0
    out_c = json.loads(capsys.readouterr().out)
    assert out_c["status"] == "PASS"
    assert out_c["mode"] == "local"

    # 4. sandbox
    rc_s = app_main(["sandbox", "--contract", pe_contract, "--canonical-ir", pe_ir, "--json"])
    assert rc_s == 0
    out_s = json.loads(capsys.readouterr().out)
    assert out_s["all_batches_passed"] is True
    assert out_s["mode"] == "local"


def test_opendv_pipeline_run_standalone_with_sockets_blocked(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    block_network_sockets: None,
) -> None:
    """Verify opendv pipeline run executes all 8 stages completely offline with sockets blocked."""
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
    from opendv_service import set_pipeline_runner_override
    from opendv_testkit import build_deterministic_agy_sdk_shim

    def shim_runner(*args: Any, **kwargs: Any) -> Any:
        return run_agentic_vault_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(shim_runner)
    try:
        wave0_spec = str(
            REPO_ROOT / "examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml"
        )
        sample_harness = str(
            REPO_ROOT / "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"
        )
        out_dir = tmp_path / "pipeline_offline_out"

        rc = app_main(
            [
                "pipeline",
                "run",
                "--input",
                wave0_spec,
                "--harness-config",
                sample_harness,
                "--output-dir",
                str(out_dir),
                "--provider",
                "agy",
                "--json",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "COMPLETED"
        assert out["overall_passed"] is True
        assert (out_dir / "pipeline_report.json").is_file()
        assert (out_dir / "final_contract.yaml").is_file()
    finally:
        set_pipeline_runner_override(None)


def test_dump_cli_tree_canonical_jcs(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --dump-cli-tree outputs valid RFC 8785 canonical JSON matching effective tree."""
    rc = app_main(["--dump-cli-tree"])
    assert rc == 0
    raw_output = capsys.readouterr().out.strip()

    data = json.loads(raw_output)
    assert data["prog"] == "opendv"
    assert data["spec_version"] == "1.0.0"
    assert data["contract_version"] == "1.0.0"
    assert "commands" in data
    assert "global_options" in data

    # Verify key commands are present
    commands = data["commands"]
    assert "verify" in commands
    assert "profile" in commands
    assert "compile" in commands
    assert "sandbox" in commands
    assert "pipeline" in commands
    assert "run" in commands
    assert "hold" in commands

    # Verify standalone-only command is included in standalone CLI tree
    pipeline_subcmds = commands["pipeline"]["subcommands"]
    assert "report" in pipeline_subcmds
    assert pipeline_subcmds["report"]["availability"] == "standalone_only"

    # Assert byte-for-byte RFC 8785 canonical formatting
    expected_jcs = dumps_jcs(data)
    assert raw_output == expected_jcs


def test_axi_dv_dump_cli_tree_canonical_jcs(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify axi-dv --dump-cli-tree outputs valid RFC 8785 canonical JSON with prog=axi-dv."""
    rc = axi_dv_main(["--dump-cli-tree"])
    assert rc == 0
    raw_output = capsys.readouterr().out.strip()

    data = json.loads(raw_output)
    assert data["prog"] == "axi-dv"
    assert "commands" in data


def test_invalid_cli_arguments_deterministic_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify invalid arguments with --json produce deterministic error JSON and exit code 2."""
    rc = app_main(["verify", "--non-existent-flag", "--json"])
    assert rc == 2
    raw_out = capsys.readouterr().out.strip()
    assert raw_out == '{"detail":"Invalid CLI arguments.","status":"ERROR"}'

    # Also test with -f json
    rc2 = app_main(["profile", "--unknown-option", "-f", "json"])
    assert rc2 == 2
    raw_out2 = capsys.readouterr().out.strip()
    assert raw_out2 == '{"detail":"Invalid CLI arguments.","status":"ERROR"}'
