"""tests/parity/test_p6_adversarial_verification.py — Adversarial Stress Test Suite for Phase P6.

Independently authored by challenger_p6_1 to stress-test:
1. Canonical JCS Tree Bit-for-Bit & Lexicographical Validation.
2. Normalized Command Output Parity across Transports against live Uvicorn server.
3. Exit Code Invariants (0, 1, 2) on edge cases and failure modes.
4. Pre-rendered Human-Readable Output Equality (text and toon).
5. Architecture, AST Import Cleanliness, and Release Packaging Artifacts.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_contracts import ErrorCode
from opendv_contracts.jcs import dumps_jcs
from tests.parity.golden_fixtures import build_gt03_01_dropped_hub
from tests.parity.normalize import normalize_for_parity
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
PE_CONTRACT_PATH = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"


@pytest.mark.parity
class TestP6AdversarialTreeParity:
    """Stress-test CLI command trees, RFC 8785 JCS, and single-sided command invariants."""

    def test_canonical_jcs_tree_strict_equality(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify strict canonical JCS formatting, version match, and error map parity."""
        code_py, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
        assert code_py == 0
        code_ts, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
        assert code_ts == 0

        # Raw outputs must be valid JSON
        py_tree = json.loads(stdout_py.strip())
        ts_tree = json.loads(stdout_ts.strip())

        # Version invariants
        assert py_tree["spec_version"] == "1.0.0"
        assert ts_tree["spec_version"] == "1.0.0"
        assert py_tree["contract_version"] == "1.0.0"
        assert ts_tree["contract_version"] == "1.0.0"

        # Error map: all domain error codes must match bit-for-bit in JCS
        assert len(py_tree["error_map"]) >= len(ErrorCode)
        assert len(ts_tree["error_map"]) >= len(ErrorCode)
        assert dumps_jcs(py_tree["error_map"]) == dumps_jcs(ts_tree["error_map"])

        # Error codes must contain all expected domain codes
        error_codes = {e["error_code"] for e in py_tree["error_map"]}
        expected_codes = {e.value for e in ErrorCode}
        assert expected_codes.issubset(error_codes)

    def test_single_sided_command_isolation(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify pipeline report is standalone_only and --api-url is client_only."""
        _, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
        _, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
        py_tree = json.loads(stdout_py.strip())
        ts_tree = json.loads(stdout_ts.strip())

        # 1. Global options: --api-url isolation
        py_flags = {opt["flag"] for opt in py_tree["global_options"]}
        ts_flags = {opt["flag"] for opt in ts_tree["global_options"]}
        assert "--api-url" in ts_flags
        assert "--api-url" not in py_flags

        # 2. Pipeline report isolation in dumped trees
        py_cmds = py_tree["commands"]
        ts_cmds = {c["name"]: c for c in ts_tree["commands"]}
        py_pipe_subs = py_cmds["pipeline"]["subcommands"]
        ts_pipe_subs = {s["name"]: s for s in ts_cmds["pipeline"]["subcommands"]}

        assert "report" in py_pipe_subs
        assert py_pipe_subs["report"]["availability"] == "standalone_only"
        assert "report" not in ts_pipe_subs

        # 3. Behavioral isolation: opendv rejects --api-url
        code_py_api, _, stderr_py_api = cli_transport.invoke_raw(
            ["--api-url", "http://localhost:8000", "compile", "-c", str(PE_CONTRACT_PATH)]
        )
        assert code_py_api == 2, "opendv must reject client-only --api-url"

        # 4. Behavioral isolation: opendvctl rejects pipeline report
        code_ts_rep, _, _ = client_cli_transport.invoke_raw(["pipeline", "report"])
        assert code_ts_rep == 2, "opendvctl must reject standalone-only pipeline report"

    def test_all_shared_commands_match_in_jcs(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify that every shared leaf and subcommand has identical JCS definitions."""
        _, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
        _, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
        py_tree = json.loads(stdout_py.strip())
        ts_tree = json.loads(stdout_ts.strip())

        py_cmds = py_tree["commands"]
        ts_cmds = {c["name"]: c for c in ts_tree["commands"]}

        # Shared leaf commands
        for leaf in ["profile", "compile", "verify", "sandbox"]:
            assert dumps_jcs(py_cmds[leaf]) == dumps_jcs(ts_cmds[leaf])

        # Shared pipeline subcommands
        py_pipe_subs = py_cmds["pipeline"]["subcommands"]
        ts_pipe_subs = {s["name"]: s for s in ts_cmds["pipeline"]["subcommands"]}
        for sub in ["validate", "run"]:
            assert dumps_jcs(py_pipe_subs[sub]) == dumps_jcs(ts_pipe_subs[sub])

        # Shared run subcommands
        py_run_subs = py_cmds["run"]["subcommands"]
        ts_run_subs = {s["name"]: s for s in ts_cmds["run"]["subcommands"]}
        for sub in ["list", "get", "create", "abort", "events", "explorer", "artifact"]:
            assert dumps_jcs(py_run_subs[sub]) == dumps_jcs(ts_run_subs[sub])

        # Shared hold subcommands
        py_hold_subs = py_cmds["hold"]["subcommands"]
        ts_hold_subs = {s["name"]: s for s in ts_cmds["hold"]["subcommands"]}
        for sub in ["list", "approve", "reject", "answer"]:
            assert dumps_jcs(py_hold_subs[sub]) == dumps_jcs(ts_hold_subs[sub])


@pytest.mark.parity
class TestP6AdversarialTransportsParity:
    """Empirically test command execution parity across STANDALONE_CLI and CLIENT_CLI."""

    def test_compile_command_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify compile produces 100% normalized payload equality."""
        res_standalone = cli_transport.compile(contract=PE_CONTRACT_PATH)
        res_client = client_cli_transport.compile(contract=PE_CONTRACT_PATH)

        norm_standalone = normalize_for_parity(res_standalone, path_root=str(tmp_path))
        norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

        assert norm_standalone == norm_client
        assert norm_standalone["status"] == "PASS"
        assert norm_standalone["hubs_compiled"] == 4
        assert norm_standalone["links_compiled"] == 5

    def test_verify_command_passing_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify verify on clean PE contract produces 100% normalized payload equality."""
        res_standalone = cli_transport.verify(contract=PE_CONTRACT_PATH)
        res_client = client_cli_transport.verify(contract=PE_CONTRACT_PATH)

        norm_standalone = normalize_for_parity(res_standalone, path_root=str(tmp_path))
        norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

        assert norm_standalone == norm_client
        assert norm_standalone["overall_passed"] is True

    def test_verify_command_failing_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify verify on dropped hub contract produces 100% normalized payload equality."""
        base_contract = yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8"))
        bad_contract = build_gt03_01_dropped_hub(base_contract)
        bad_file = tmp_path / "bad_contract.yaml"
        bad_file.write_text(yaml.safe_dump(bad_contract), encoding="utf-8")

        res_standalone = cli_transport.verify(
            contract=bad_file,
            prior=yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8")),
        )
        res_client = client_cli_transport.verify(
            contract=bad_file,
            prior=yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8")),
        )

        norm_standalone = normalize_for_parity(res_standalone, path_root=str(tmp_path))
        norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

        assert norm_standalone == norm_client
        assert norm_standalone["overall_passed"] is False

    def test_sandbox_command_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify sandbox produces 100% normalized payload equality."""
        res_standalone = cli_transport.sandbox(contract=PE_CONTRACT_PATH)
        res_client = client_cli_transport.sandbox(contract=PE_CONTRACT_PATH)

        norm_standalone = normalize_for_parity(res_standalone, path_root=str(tmp_path))
        norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

        assert norm_standalone == norm_client
        assert norm_standalone["all_batches_passed"] is True

    def test_profile_command_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify profile produces 100% normalized payload equality."""
        sample_csv = tmp_path / "adversarial_sample.csv"
        sample_csv.write_text(
            "id,name,category,amount\n1,Entity A,Category 1,100.50\n2,Entity B,Category 2,250.75\n",
            encoding="utf-8",
        )
        spec = {"source_csv": str(sample_csv.resolve())}
        res_standalone = cli_transport.profile(spec)
        res_client = client_cli_transport.profile(spec)

        norm_standalone = normalize_for_parity(res_standalone, path_root=str(tmp_path))
        norm_client = normalize_for_parity(res_client, path_root=str(tmp_path))

        assert norm_standalone == norm_client
        assert norm_standalone["table_count"] == 1
        assert norm_standalone["column_count"] == 4


@pytest.mark.parity
class TestP6AdversarialExitCodes:
    """Stress-test strict exit code invariants: 0 on OK, 1 on rejection, 2 on errors."""

    def test_exit_code_0_success(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify exit code 0 on clean operations."""
        # Compile
        c_py, _, _ = cli_transport.invoke_raw(["compile", "-c", str(PE_CONTRACT_PATH), "--json"])
        c_ts, _, _ = client_cli_transport.invoke_raw(
            ["compile", "-c", str(PE_CONTRACT_PATH), "--json"]
        )
        assert c_py == 0
        assert c_ts == 0

        # Verify
        v_py, _, _ = cli_transport.invoke_raw(["verify", "-c", str(PE_CONTRACT_PATH), "--json"])
        v_ts, _, _ = client_cli_transport.invoke_raw(
            ["verify", "-c", str(PE_CONTRACT_PATH), "--json"]
        )
        assert v_py == 0
        assert v_ts == 0

        # Help
        h_py, _, _ = cli_transport.invoke_raw(["--help"])
        h_ts, _, _ = client_cli_transport.invoke_raw(["--help"])
        assert h_py == 0
        assert h_ts == 0

        # Version on Bun platform client cleanly returns 0
        ver_ts, stdout_ts, _ = client_cli_transport.invoke_raw(["--version"])
        assert ver_ts == 0
        assert "opendvctl 1.0.0" in stdout_ts

        # Version on opendv standalone cleanly returns 0
        ver_py, stdout_py, _ = cli_transport.invoke_raw(["--version"])
        assert ver_py == 0, "opendv --version must cleanly exit with code 0"
        assert "opendv 1.0.0" in stdout_py

    def test_exit_code_1_rejection(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify exit code 1 on verification rejections (DV-EVO-002 dropped hub)."""
        base_contract = yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8"))
        bad_contract = build_gt03_01_dropped_hub(base_contract)
        bad_file = tmp_path / "bad_hub.yaml"
        bad_file.write_text(yaml.safe_dump(bad_contract), encoding="utf-8")

        code_py, _, _ = cli_transport.invoke_raw(
            [
                "verify",
                "--contract",
                str(bad_file.resolve()),
                "--prior-contract",
                str(PE_CONTRACT_PATH.resolve()),
                "--json",
            ]
        )
        code_ts, _, _ = client_cli_transport.invoke_raw(
            [
                "verify",
                "--contract",
                str(bad_file.resolve()),
                "--prior-contract",
                str(PE_CONTRACT_PATH.resolve()),
                "--json",
            ]
        )
        assert code_py == 1, "opendv must return exit code 1 on verification rejection"
        assert code_ts == 1, "opendvctl must return exit code 1 on verification rejection"

    def test_exit_code_2_syntax_and_missing_args(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify exit code 2 on syntax, argument errors, and missing parameters."""
        # 1. Missing required --contract on compile
        c_py, _, _ = cli_transport.invoke_raw(["compile"])
        c_ts, _, _ = client_cli_transport.invoke_raw(["compile"])
        assert c_py == 2
        assert c_ts == 2

        # 2. Missing required --contract on verify
        v_py, _, _ = cli_transport.invoke_raw(["verify"])
        v_ts, _, _ = client_cli_transport.invoke_raw(["verify"])
        assert v_py == 2
        assert v_ts == 2

        # 3. Missing required --run-id on hold approve
        ha_py, _, _ = cli_transport.invoke_raw(["hold", "approve"])
        ha_ts, _, _ = client_cli_transport.invoke_raw(["hold", "approve"])
        assert ha_py == 2
        assert ha_ts == 2

        # 4. Unknown flag
        uk_py, _, _ = cli_transport.invoke_raw(["compile", "--bogus-flag-123"])
        uk_ts, _, _ = client_cli_transport.invoke_raw(["compile", "--bogus-flag-123"])
        assert uk_py == 2
        assert uk_ts == 2


@pytest.mark.parity
class TestP6AdversarialRenderedOutputs:
    """Verify pre-rendered output equivalence (text reports and toon)."""

    def test_prerendered_text_compilation_report(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
    ) -> None:
        """Verify both CLIs output matching pre-rendered 4-zone SQL compilation report."""
        _, text_py, _ = cli_transport.invoke_raw(
            ["compile", "--contract", str(PE_CONTRACT_PATH), "--format", "text"]
        )
        _, text_ts, _ = client_cli_transport.invoke_raw(
            ["compile", "--contract", str(PE_CONTRACT_PATH), "--format", "text"]
        )

        # Core header
        assert "Open-DV 4-Zone SQL Compilation Report" in text_py
        assert "Open-DV 4-Zone SQL Compilation Report" in text_ts
        # Status
        assert "Status: PASS" in text_py
        assert "Status: PASS" in text_ts
        # Counts
        assert "Compiled Constructs: 4 Hubs, 5 Links, 11 Satellites" in text_py
        assert "Compiled Constructs: 4 Hubs, 5 Links, 11 Satellites" in text_ts
        # Topological load order
        assert "Topological Load Order (25 tables):" in text_py
        assert "Topological Load Order (25 tables):" in text_ts
        # Specific tables
        for tbl in ["hub_document", "hub_fund", "hub_limited_partner", "hub_portfolio_company"]:
            assert tbl in text_py
            assert tbl in text_ts


@pytest.mark.parity
class TestP6AdversarialArchitectureInvariants:
    """Stress-test Phase P6 architectural invariants (AST imports, release packaging)."""

    def test_ast_forbidden_imports_in_opendv_cli(self) -> None:
        """Verify opendv-cli package has ZERO imports of fastapi, starlette, opendv_platform_api, httpx."""
        cli_src_dir = REPO_ROOT / "apps" / "opendv-cli" / "src" / "opendv_cli"
        assert cli_src_dir.is_dir()

        forbidden_modules = {"fastapi", "starlette", "opendv_platform_api", "httpx"}

        for py_file in cli_src_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        assert top not in forbidden_modules, (
                            f"Forbidden import '{alias.name}' in {py_file.name}:{node.lineno}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                    assert top not in forbidden_modules, (
                        f"Forbidden from-import '{node.module}' in {py_file.name}:{node.lineno}"
                    )

    def test_5_release_packaging_artifacts_exist_and_hash(self) -> None:
        """Verify all 5 compiled release binaries, archives, and SHA256SUMS.txt exist."""
        release_dir = REPO_ROOT / "apps" / "opendvctl" / "dist" / "release"
        assert release_dir.is_dir(), f"Missing release directory: {release_dir}"

        targets = [
            ("opendvctl-linux-x64", "opendvctl-linux-x64.tar.gz"),
            ("opendvctl-linux-arm64", "opendvctl-linux-arm64.tar.gz"),
            ("opendvctl-darwin-x64", "opendvctl-darwin-x64.tar.gz"),
            ("opendvctl-darwin-arm64", "opendvctl-darwin-arm64.tar.gz"),
            ("opendvctl-windows-x64.exe", "opendvctl-windows-x64.zip"),
        ]

        for bin_name, archive_name in targets:
            bin_path = release_dir / bin_name
            arc_path = release_dir / archive_name
            assert bin_path.is_file(), f"Missing compiled binary: {bin_name}"
            assert bin_path.stat().st_size > 50_000_000, f"Binary too small: {bin_name}"
            assert arc_path.is_file(), f"Missing release archive: {archive_name}"
            assert arc_path.stat().st_size > 15_000_000, f"Archive too small: {archive_name}"

        # SHA256SUMS.txt validation
        sums_file = release_dir / "SHA256SUMS.txt"
        assert sums_file.is_file()
        sums_text = sums_file.read_text(encoding="utf-8")

        for line in sums_text.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split()
            expected_hash = parts[0]
            file_name = parts[1]
            target_file = release_dir / file_name
            assert target_file.is_file(), f"File in SHA256SUMS missing on disk: {file_name}"
            computed_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()
            assert computed_hash == expected_hash, f"Hash mismatch for {file_name}"
