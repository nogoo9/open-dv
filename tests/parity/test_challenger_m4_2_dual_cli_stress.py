"""tests/parity/test_challenger_m4_2_dual_cli_stress.py — Challenger Stress Suite for Feature 15.

Adversarial Stress Testing & Parity Verification for Dual-CLI Command Parity (Feature 15):
1. Concurrent Session Creation & Atomicity (Race condition stress on opendv and opendvctl)
2. RBAC Permission Escalation & Authorization Gate Verification
3. Adversarial Arguments, Corrupted Payloads, Path Traversal & Windows Reserved Devices
4. Comprehensive 0..7 Exit Code Matrix & RFC 9457 Problem Details Parity
5. Wave-0 Pipeline Validation Parity with and without `--unstructured-only`
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMERCE_ONTOLOGY_PATH = (
    REPO_ROOT
    / "examples"
    / "pipeline_samples"
    / "global_commerce_vault"
    / "ontology"
    / "commerce_graph_a_ontology.yaml"
)

PYTHON_EXE = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.is_file():
    PYTHON_EXE = Path(sys.executable)

OPENDVCTL_TS = REPO_ROOT / "apps" / "opendvctl" / "src" / "main.ts"


@pytest.fixture(autouse=True)
def studio_parity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OPENDV_TOKEN and OPENDV_STUDIO_TOKEN are set for authenticated parity testing."""
    token = "challenger-m4-2-stress-token"
    monkeypatch.setenv("OPENDV_TOKEN", token)
    monkeypatch.setenv("OPENDV_STUDIO_TOKEN", token)


def _extract_error_details(stdout: str, stderr: str) -> dict[str, Any]:
    """Parse JSON error payload from stdout or stderr adhering to RFC 9457."""
    for text in (stdout, stderr):
        clean = text.strip()
        if not clean:
            continue
        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback line scanning
        for line in clean.splitlines():
            for prefix in ("ERROR:", "Error:", "error_code:", "code:"):
                if prefix in line:
                    return {
                        "error_code": line.split(prefix, 1)[1].strip(),
                        "detail": line.strip(),
                    }
    return {}


# ===========================================================================
# 1. Concurrent Session Creation & Atomicity Stress
# ===========================================================================


class TestDualCliConcurrentSessionCreation:
    """Stress tests concurrent session creations across opendv and opendvctl."""

    def test_high_concurrency_distinct_session_creation(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify that creating distinct sessions concurrently in both CLIs operates cleanly."""
        num_sessions = 6
        distinct_ids_py = [f"conc_py_{uuid.uuid4().hex[:8]}_{i}" for i in range(num_sessions)]
        distinct_ids_ts = [f"conc_ts_{uuid.uuid4().hex[:8]}_{i}" for i in range(num_sessions)]

        # Standalone opendv distinct creations
        for sid in distinct_ids_py:
            code, _, err = cli_transport.invoke_raw(
                [
                    "ontology",
                    "studio",
                    "create",
                    "-s",
                    sid,
                    "--domain-namespace",
                    "asset_management",
                    "--json",
                ]
            )
            assert code == 0, f"opendv create failed for {sid}: {err}"

        # Subprocess client opendvctl distinct creations concurrently
        def _invoke_ts_create(sid: str) -> tuple[int, str, str]:
            transport = ClientCliTransport(
                workspace_dir=tmp_path / f"ws_{sid}",
                server_url=client_cli_transport.server_url,
            )
            return transport.invoke_raw(
                [
                    "ontology",
                    "studio",
                    "create",
                    "-s",
                    sid,
                    "--domain-namespace",
                    "asset_management",
                    "--json",
                ]
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_sessions) as executor:
            ts_results = list(executor.map(_invoke_ts_create, distinct_ids_ts))

        for (code, out, err), sid in zip(ts_results, distinct_ids_ts, strict=False):
            assert code == 0, f"opendvctl concurrent create failed for {sid}: {err} (out={out})"

    def test_racing_identical_session_creation_atomicity_opendv(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify that when 6 concurrent workers race to create the EXACT SAME session in opendv,
        exactly ONE succeeds with exit 0 and exactly FIVE fail with exit 6 (SESSION_ALREADY_EXISTS).
        """
        shared_sid = f"race_py_{uuid.uuid4().hex[:8]}"
        workspace_dir = tmp_path / "race_ws_py"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        concurrency = 6

        def _worker(worker_id: int) -> tuple[int, str, str]:
            cmd = [
                str(PYTHON_EXE),
                "-m",
                "opendv_cli.main",
                "ontology",
                "studio",
                "create",
                "-s",
                shared_sid,
                "--domain-namespace",
                "asset_management",
                "--json",
            ]
            env = {
                **os.environ,
                "OPENDV_WORKSPACE_ROOT": str(workspace_dir),
                "OPENDV_STUDIO_TOKEN": "challenger-m4-2-stress-token",
            }
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(workspace_dir),
                env=env,
            )
            return proc.returncode, proc.stdout, proc.stderr

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(_worker, range(concurrency)))

        exit_codes = [r[0] for r in results]
        assert exit_codes.count(0) == 1, (
            f"Expected exactly ONE success (exit 0) in opendv race, got: {exit_codes}"
        )
        assert exit_codes.count(6) == concurrency - 1, (
            f"Expected {concurrency - 1} duplicates (exit 6) in opendv race, got: {exit_codes}"
        )

        # Verify that losing workers received SESSION_ALREADY_EXISTS
        for code, out, err in results:
            if code == 6:
                details = _extract_error_details(out, err)
                assert details.get("error_code") == "SESSION_ALREADY_EXISTS"

    def test_racing_identical_session_creation_atomicity_opendvctl(
        self,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify that when 6 concurrent workers race to create the EXACT SAME session in opendvctl,
        exactly ONE succeeds with exit 0 and exactly FIVE fail with exit 6 (SESSION_ALREADY_EXISTS).
        """
        shared_sid = f"race_ts_{uuid.uuid4().hex[:8]}"
        concurrency = 6

        def _worker(worker_id: int) -> tuple[int, str, str]:
            t = ClientCliTransport(
                workspace_dir=tmp_path / f"race_ts_{worker_id}",
                server_url=client_cli_transport.server_url,
            )
            return t.invoke_raw(
                [
                    "ontology",
                    "studio",
                    "create",
                    "-s",
                    shared_sid,
                    "--domain-namespace",
                    "asset_management",
                    "--json",
                ]
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(_worker, range(concurrency)))

        exit_codes = [r[0] for r in results]
        assert exit_codes.count(0) == 1, (
            f"Expected exactly ONE success (exit 0) in opendvctl race, got: {exit_codes}"
        )
        assert exit_codes.count(6) == concurrency - 1, (
            f"Expected {concurrency - 1} duplicates (exit 6) in opendvctl race, got: {exit_codes}"
        )

        for code, out, err in results:
            if code == 6:
                details = _extract_error_details(out, err)
                assert details.get("error_code") == "SESSION_ALREADY_EXISTS"


# ===========================================================================
# 2. RBAC Permission Escalation & Authorization Gate Verification
# ===========================================================================


class TestDualCliRbacEscalationAndGovernance:
    """Stress tests RBAC boundaries, role escalations, and token tampering across both CLIs."""

    @pytest.mark.parametrize(
        "unauthorized_role",
        ["VIEWER", "INTERNAL_MODELER", "PUBLIC", "AUDITOR_FINANCE"],
    )
    def test_rbac_unauthorized_grounding_waiver_matrix(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        unauthorized_role: str,
    ) -> None:
        """Verify unprivileged roles attempting low-confidence grounding waiver
        are strictly rejected with exit code 7 on both CLIs (403 Forbidden).
        """
        sid_py = f"rbac_py_{uuid.uuid4().hex[:8]}"
        sid_ts = f"rbac_ts_{uuid.uuid4().hex[:8]}"

        cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_py, "--json"])
        client_cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_ts, "--json"])

        waiver_args_py = [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_py,
            "--proposal-id",
            "prop_test_01",
            "--base-sha256",
            "0" * 64,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adversarial unprivileged escalation attempt with lengthy rationale",
            "--role",
            unauthorized_role,
            "--json",
        ]
        waiver_args_ts = [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_ts,
            "--proposal-id",
            "prop_test_01",
            "--base-sha256",
            "0" * 64,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adversarial unprivileged escalation attempt with lengthy rationale",
            "--role",
            unauthorized_role,
            "--json",
        ]

        old_role = client_cli_transport.role
        try:
            client_cli_transport.role = unauthorized_role
            code_py, out_py, err_py = cli_transport.invoke_raw(waiver_args_py)
            code_ts, out_ts, err_ts = client_cli_transport.invoke_raw(waiver_args_ts)
        finally:
            client_cli_transport.role = old_role

        assert code_py == 7, f"opendv allowed role {unauthorized_role}: {err_py}"
        assert code_ts == 7, f"opendvctl allowed role {unauthorized_role}: {err_ts}"
        assert code_py == code_ts

        det_py = _extract_error_details(out_py, err_py)
        det_ts = _extract_error_details(out_ts, err_ts)
        # Both error codes indicate 403 authorization denial
        # (FORBIDDEN_ADMIN_REQUIRED or AGENT_PROPOSAL_ACCEPT_FORBIDDEN)
        assert det_py.get("error_code") in (
            "FORBIDDEN_ADMIN_REQUIRED",
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
        )
        assert det_ts.get("error_code") in (
            "FORBIDDEN_ADMIN_REQUIRED",
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
        )

    def test_rbac_tampered_token_rejection(
        self,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify legacy OPENDV_TOKEN is tolerated on loopback while OPENDV_ROLE=PUBLIC fails with exit 7."""
        import shutil

        sid_noop = f"tampered_sess_noop_{uuid.uuid4().hex[:8]}"
        env_noop = {
            **os.environ,
            "OPENDV_STUDIO_TOKEN": "bogus_tampered_token_xyz_999",
            "OPENDV_TOKEN": "bogus_tampered_token_xyz_999",
            "OPENDV_API_URL": client_cli_transport.server_url,
        }
        cmd_noop = [
            "bun",
            "run",
            str(OPENDVCTL_TS),
            "ontology",
            "studio",
            "create",
            "-s",
            sid_noop,
            "--json",
        ]
        try:
            proc_noop = subprocess.run(
                cmd_noop,
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=env_noop,
            )
            assert proc_noop.returncode == 0, (
                f"Expected loopback OPENDV_TOKEN no-op exit 0, got {proc_noop.returncode}: {proc_noop.stderr}"
            )
        finally:
            shutil.rmtree(REPO_ROOT / ".opendv" / "ontology_studio" / sid_noop, ignore_errors=True)
            (REPO_ROOT / ".opendv" / "ontology_studio" / f".lifecycle_{sid_noop}.lock").unlink(
                missing_ok=True
            )

        # Explicit PUBLIC role still denied mutation (exit 7)
        env_pub = {**env_noop, "OPENDV_ROLE": "PUBLIC"}
        cmd_pub = [
            "bun",
            "run",
            str(OPENDVCTL_TS),
            "ontology",
            "studio",
            "create",
            "-s",
            "tampered_sess_pub",
            "--json",
        ]
        proc_pub = subprocess.run(
            cmd_pub,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env_pub,
        )
        assert proc_pub.returncode == 7, (
            f"Expected exit 7 for PUBLIC role mutation, got {proc_pub.returncode}: {proc_pub.stderr}"
        )


# ===========================================================================
# 3. Adversarial Arguments, Corrupted Payloads, Path Traversal
# ===========================================================================


class TestDualCliAdversarialArgumentsAndCorruptPayloads:
    """Stress tests CLI error handling against malformed inputs, traversal, and invalid flags."""

    @pytest.mark.parametrize(
        "bad_cmd",
        [
            ["ontology", "studio", "status", "--unrecognized-bogus-option-adversarial"],
            ["ontology", "studio", "create", "--unknown-flag-123"],
            ["ontology", "studio", "export", "--bogus-arg"],
            ["pipeline", "validate", "--totally-fake-option"],
        ],
    )
    def test_unrecognized_flags_exit_2_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        bad_cmd: list[str],
    ) -> None:
        """Verify that passing unrecognized flags exits with code 2 on both CLIs."""
        code_py, _, _ = cli_transport.invoke_raw([*bad_cmd, "--json"])
        code_ts, _, _ = client_cli_transport.invoke_raw([*bad_cmd, "--json"])

        assert code_py == 2, f"opendv did not exit 2 on bad args {bad_cmd}: got {code_py}"
        assert code_ts == 2, f"opendvctl did not exit 2 on bad args {bad_cmd}: got {code_ts}"
        assert code_py == code_ts

    @pytest.mark.parametrize(
        "missing_arg_cmd",
        [
            ["ontology", "studio", "text", "add"],  # missing -s and -t
            [
                "ontology",
                "studio",
                "starter-pack",
                "apply",
                "-s",
                "some_session",
            ],  # missing --pack
            [
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "some_session",
            ],  # missing --proposal-id
        ],
    )
    def test_missing_mandatory_arguments_exit_2_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        missing_arg_cmd: list[str],
    ) -> None:
        """Verify that missing required arguments exits with code 2 on both CLIs."""
        code_py, _out_py, err_py = cli_transport.invoke_raw([*missing_arg_cmd, "--json"])
        code_ts, _out_ts, err_ts = client_cli_transport.invoke_raw([*missing_arg_cmd, "--json"])

        assert code_py == 2, f"opendv did not exit 2 on {missing_arg_cmd}: {err_py}"
        assert code_ts == 2, f"opendvctl did not exit 2 on {missing_arg_cmd}: {err_ts}"
        assert code_py == code_ts

    def test_corrupt_delta_payload_rejection_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify that passing malformed/corrupted JSON payloads to `delta apply` fails cleanly."""
        sid_py = f"corrupt_py_{uuid.uuid4().hex[:8]}"
        sid_ts = f"corrupt_ts_{uuid.uuid4().hex[:8]}"
        cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_py, "--json"])
        client_cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_ts, "--json"])

        corrupt_file = tmp_path / "corrupted_delta.json"
        corrupt_file.write_text("{ this is NOT valid JSON ::: <<< >>> }", encoding="utf-8")

        cmd_py = [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            sid_py,
            "-f",
            str(corrupt_file),
            "--json",
        ]
        cmd_ts = [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            sid_ts,
            "-f",
            str(corrupt_file),
            "--json",
        ]

        old_role = client_cli_transport.role
        try:
            client_cli_transport.role = "DATA_STEWARD"
            code_py, _out_py, err_py = cli_transport.invoke_raw(cmd_py)
            code_ts, _out_ts, err_ts = client_cli_transport.invoke_raw(cmd_ts)
        finally:
            client_cli_transport.role = old_role

        assert code_py in (2, 4), f"opendv unexpected code for corrupt delta: {code_py} ({err_py})"
        assert code_ts in (
            2,
            4,
        ), f"opendvctl unexpected code for corrupt delta: {code_ts} ({err_ts})"

    @pytest.mark.parametrize(
        "traversal_vector",
        [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "session/../../../forbidden",
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
        ],
    )
    def test_path_traversal_and_reserved_device_names_rejection(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        traversal_vector: str,
    ) -> None:
        """Verify path traversal strings and Windows reserved device names are strictly rejected."""
        cmd_py = ["ontology", "studio", "create", "-s", traversal_vector, "--json"]
        cmd_ts = ["ontology", "studio", "create", "-s", traversal_vector, "--json"]

        code_py, _, _ = cli_transport.invoke_raw(cmd_py)
        code_ts, _, _ = client_cli_transport.invoke_raw(cmd_ts)

        # In both CLIs, invalid session IDs must fail (exit 2 or 3)
        assert code_py in (
            2,
            3,
        ), f"opendv did not reject traversal '{traversal_vector}': {code_py}"
        assert code_ts in (
            2,
            3,
        ), f"opendvctl did not reject traversal '{traversal_vector}': {code_ts}"

    @pytest.mark.parametrize("short_rat", ["a", "tiny", "123456789"])
    def test_promotion_rationale_short_boundary_rejected(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        short_rat: str,
    ) -> None:
        """Verify that promotion rationale < 10 characters exits with code 1 on both CLIs."""
        sid_py = f"rat_py_{uuid.uuid4().hex[:8]}"
        sid_ts = f"rat_ts_{uuid.uuid4().hex[:8]}"
        cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_py, "--json"])
        client_cli_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_ts, "--json"])

        args_py = [
            "ontology",
            "studio",
            "promote",
            "-s",
            sid_py,
            "--workspace-sha256",
            "0" * 64,
            "--head-sha256",
            "1" * 64,
            "-r",
            short_rat,
            "--json",
        ]
        args_ts = [
            "ontology",
            "studio",
            "promote",
            "-s",
            sid_ts,
            "--workspace-sha256",
            "0" * 64,
            "--head-sha256",
            "1" * 64,
            "-r",
            short_rat,
            "--json",
        ]

        code_py, out_py, err_py = cli_transport.invoke_raw(args_py)
        code_ts, out_ts, err_ts = client_cli_transport.invoke_raw(args_ts)

        assert code_py == 1
        assert code_ts == 1
        assert code_py == code_ts
        d_py = _extract_error_details(out_py, err_py)
        d_ts = _extract_error_details(out_ts, err_ts)
        assert d_py.get("error_code") == "PROMOTION_RATIONALE_REQUIRED"
        assert d_ts.get("error_code") == "PROMOTION_RATIONALE_REQUIRED"


# ===========================================================================
# 4. Comprehensive 0..7 Exit Code Matrix & RFC 9457 Parity
# ===========================================================================


class TestDualCliExitCodeMatrixAndRfc9457Parity:
    """Stress tests complete 0..7 exit code matrix and RFC 9457 structured error format."""

    def test_exit_code_0_to_7_rfc9457_structure(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify all 8 exit codes (0..7) and assert RFC 9457 details match across CLIs."""
        sid_py = f"mat_py_{uuid.uuid4().hex[:8]}"
        sid_ts = f"mat_ts_{uuid.uuid4().hex[:8]}"

        # Exit 0: Success
        c0_py, out0_py, _ = cli_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_py, "--json"]
        )
        c0_ts, out0_ts, _ = client_cli_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_ts, "--json"]
        )
        assert c0_py == 0 and c0_ts == 0
        j0_py = json.loads(out0_py.strip())
        j0_ts = json.loads(out0_ts.strip())
        assert j0_py.get("session_id") == sid_py
        assert j0_ts.get("session_id") == sid_ts

        # Exit 1: Promotion rationale missing / too short
        c1_py, out1_py, err1_py = cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                sid_py,
                "--workspace-sha256",
                "0" * 64,
                "--head-sha256",
                "1" * 64,
                "-r",
                "tiny",
                "--json",
            ]
        )
        c1_ts, out1_ts, err1_ts = client_cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                sid_ts,
                "--workspace-sha256",
                "0" * 64,
                "--head-sha256",
                "1" * 64,
                "-r",
                "tiny",
                "--json",
            ]
        )
        assert c1_py == 1 and c1_ts == 1
        d1_py = _extract_error_details(out1_py, err1_py)
        d1_ts = _extract_error_details(out1_ts, err1_ts)
        assert d1_py.get("error_code") == "PROMOTION_RATIONALE_REQUIRED"
        assert d1_ts.get("error_code") == "PROMOTION_RATIONALE_REQUIRED"

        # Exit 2: Syntax error / unrecognized flag
        c2_py, _out2_py, _err2_py = cli_transport.invoke_raw(
            ["ontology", "studio", "status", "-s", sid_py, "--non-existent-flag-999", "--json"]
        )
        c2_ts, _out2_ts, _err2_ts = client_cli_transport.invoke_raw(
            ["ontology", "studio", "status", "-s", sid_ts, "--non-existent-flag-999", "--json"]
        )
        assert c2_py == 2 and c2_ts == 2

        # Exit 3: Not found (ghost session)
        ghost_id = f"ghost_{uuid.uuid4().hex[:8]}"
        c3_py, out3_py, err3_py = cli_transport.invoke_raw(
            ["ontology", "studio", "status", "-s", ghost_id, "--json"]
        )
        c3_ts, out3_ts, err3_ts = client_cli_transport.invoke_raw(
            ["ontology", "studio", "status", "-s", ghost_id, "--json"]
        )
        assert c3_py == 3 and c3_ts == 3
        d3_py = _extract_error_details(out3_py, err3_py)
        d3_ts = _extract_error_details(out3_ts, err3_ts)
        assert d3_py.get("error_code") == "SESSION_NOT_FOUND"
        assert d3_ts.get("error_code") == "SESSION_NOT_FOUND"

        # Exit 4: Invalid waiver rationale (< 10 chars)
        dummy_key = tmp_path / "valid.key"
        dummy_key.write_text("waiver-secret-key-12345", encoding="utf-8")
        w_args_py = [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_py,
            "--proposal-id",
            "p1",
            "--base-sha256",
            "0" * 64,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "tiny",
            "--waiver-key-file",
            str(dummy_key),
            "--json",
        ]
        w_args_ts = [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_ts,
            "--proposal-id",
            "p1",
            "--base-sha256",
            "0" * 64,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "tiny",
            "--waiver-key-file",
            str(dummy_key),
            "--json",
        ]
        c4_py, out4_py, err4_py = cli_transport.invoke_raw(w_args_py)
        old_role = client_cli_transport.role
        try:
            client_cli_transport.role = "DATA_STEWARD"
            c4_ts, out4_ts, err4_ts = client_cli_transport.invoke_raw(w_args_ts)
        finally:
            client_cli_transport.role = old_role
        assert c4_py == 4 and c4_ts == 4
        assert (
            _extract_error_details(out4_py, err4_py).get("error_code") == "INVALID_GROUNDING_WAIVER"
        )
        assert (
            _extract_error_details(out4_ts, err4_ts).get("error_code") == "INVALID_GROUNDING_WAIVER"
        )

        # Exit 5: OCC conflict on stale base SHA
        delta_file = tmp_path / "delta_stale.json"
        delta_file.write_text(
            json.dumps(
                {
                    "delta_id": "d-stale",
                    "parent_sha256": "e" * 64,
                    "base_version": "0.0.0",
                    "target_version": "0.1.0",
                    "operations": [
                        {
                            "op_id": "op1",
                            "op_type": "add_concept",
                            "concept_uri": "urn:concept:1",
                            "target_hub": "hub_1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        occ_args_py = [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            sid_py,
            "-f",
            str(delta_file),
            "--base-sha256",
            "e" * 64,
            "--json",
        ]
        occ_args_ts = [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            sid_ts,
            "-f",
            str(delta_file),
            "--base-sha256",
            "e" * 64,
            "--json",
        ]
        c5_py, out5_py, err5_py = cli_transport.invoke_raw(occ_args_py)
        try:
            client_cli_transport.role = "DATA_STEWARD"
            c5_ts, out5_ts, err5_ts = client_cli_transport.invoke_raw(occ_args_ts)
        finally:
            client_cli_transport.role = old_role
        assert c5_py == 5 and c5_ts == 5
        assert (
            _extract_error_details(out5_py, err5_py).get("error_code")
            == "ONT_EVO_CONCURRENT_MODIFICATION"
        )
        assert (
            _extract_error_details(out5_ts, err5_ts).get("error_code")
            == "ONT_EVO_CONCURRENT_MODIFICATION"
        )

        # Exit 6: State conflict (duplicate session creation)
        c6_py, out6_py, err6_py = cli_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_py, "--json"]
        )
        c6_ts, out6_ts, err6_ts = client_cli_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_ts, "--json"]
        )
        assert c6_py == 6 and c6_ts == 6
        assert (
            _extract_error_details(out6_py, err6_py).get("error_code") == "SESSION_ALREADY_EXISTS"
        )
        assert (
            _extract_error_details(out6_ts, err6_ts).get("error_code") == "SESSION_ALREADY_EXISTS"
        )

        # Exit 7: Remote security restriction (`ontology studio init` on remote client)
        c7_ts, out7_ts, err7_ts = client_cli_transport.invoke_raw(
            ["ontology", "studio", "init", "--json"]
        )
        assert c7_ts == 7
        assert (
            _extract_error_details(out7_ts, err7_ts).get("error_code")
            == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"
        )


# ===========================================================================
# 5. Pipeline Validation Parity with and without `--unstructured-only`
# ===========================================================================


class TestDualCliUnstructuredOnlyPipelineValidation:
    """Stress tests pipeline manifest validation parity across opendv and opendvctl."""

    def test_unstructured_only_validation_parity(
        self,
        cli_transport: StandaloneCliTransport,
        client_cli_transport: ClientCliTransport,
        tmp_path: Path,
    ) -> None:
        """Verify identical behavior when validating unstructured documents."""
        doc_file = tmp_path / "im_private_credit_memo.txt"
        doc_file.write_text("Private Credit Direct Lending Term Sheet 2026", encoding="utf-8")

        # 1. Valid Manifest (with unstructured_only: True)
        manifest_valid = {
            "pipeline_id": "pipe-unstructured-valid",
            "project_id": "proj-am-parity",
            "run_id": "run-parity-valid",
            "display_name": "Unstructured Valid Parity Test",
            "domain_namespace": "asset_management",
            "graph_a_ontology_path": str(COMMERCE_ONTOLOGY_PATH),
            "unstructured_only": True,
            "sources": [],
            "unstructured_documents": [
                {
                    "doc_id": "DOC-PC-01",
                    "document_path": str(doc_file),
                    "record_source": "CAS_INGESTION",
                }
            ],
        }
        manifest_valid_path = tmp_path / "unstructured_manifest_valid.yaml"
        manifest_valid_path.write_text(yaml.dump(manifest_valid), encoding="utf-8")

        # 2. Invalid Manifest (missing unstructured_only, missing relational source)
        manifest_invalid = {
            "pipeline_id": "pipe-unstructured-invalid",
            "project_id": "proj-am-parity",
            "run_id": "run-parity-invalid",
            "display_name": "Unstructured Invalid Parity Test",
            "domain_namespace": "asset_management",
            "graph_a_ontology_path": str(COMMERCE_ONTOLOGY_PATH),
            "sources": [],
            "unstructured_documents": [
                {
                    "doc_id": "DOC-PC-02",
                    "document_path": str(doc_file),
                    "record_source": "CAS_INGESTION",
                }
            ],
        }
        manifest_invalid_path = tmp_path / "unstructured_manifest_invalid.yaml"
        manifest_invalid_path.write_text(yaml.dump(manifest_invalid), encoding="utf-8")

        # Case A: Valid manifest with --unstructured-only: Both CLIs must exit 0
        cmd_valid = [
            "pipeline",
            "validate",
            "-i",
            str(manifest_valid_path),
            "--unstructured-only",
            "--json",
        ]
        code_py_v, out_py_v, err_py_v = cli_transport.invoke_raw(cmd_valid)
        code_ts_v, out_ts_v, err_ts_v = client_cli_transport.invoke_raw(cmd_valid)

        assert code_py_v == 0, f"opendv validate failed: {err_py_v}"
        assert code_ts_v == 0, f"opendvctl validate failed: {err_ts_v}"
        assert code_py_v == code_ts_v

        j_py_v = json.loads(out_py_v.strip())
        j_ts_v = json.loads(out_ts_v.strip())
        assert j_py_v.get("passed") is True
        assert j_ts_v.get("passed") is True
        assert j_py_v.get("diagnostics") == []
        assert j_ts_v.get("diagnostics") == []

        # Case B: Invalid manifest without --unstructured-only: Both CLIs must exit 1
        cmd_invalid = ["pipeline", "validate", "-i", str(manifest_invalid_path), "--json"]
        code_py_inv, out_py_inv, err_py_inv = cli_transport.invoke_raw(cmd_invalid)
        code_ts_inv, out_ts_inv, err_ts_inv = client_cli_transport.invoke_raw(cmd_invalid)

        assert code_py_inv == 1, f"opendv unexpected code without flag: {code_py_inv}"
        assert code_ts_inv == 1, f"opendvctl unexpected code without flag: {code_ts_inv}"
        assert code_py_inv == code_ts_inv

        assert (
            "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in out_py_inv
            or "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in err_py_inv
        )
        assert (
            "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in out_ts_inv
            or "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in err_ts_inv
        )
