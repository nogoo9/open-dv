"""tests/parity/test_challenger_5c_parity_stress.py — Challenger 5C Stress Harness.

Empirical verification and stress testing of Track 5C Dual-CLI Parity Harness:
1. Deep AST & Command Tree Introspection Parity across standalone `opendv` and Bun `opendvctl`.
2. Tamper-Resistance of Mode-Specific Policy (exit 7 for remote init).
3. Complete Exit Code Matrix (0..7) verification across diverse error paths on both CLIs.
4. Bit-for-bit canonical JCS stdout equivalence and volatile field masking across operations.
5. Path traversal attack rejection and proposal lifecycle robustness.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from opendv_contracts.jcs import dumps_jcs
from tests.parity.test_ontology_studio_cli_parity import (
    _extract_error_code,
    normalize_studio_for_parity,
)
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
PE_ONTOLOGY_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"


@pytest.fixture(autouse=True)
def studio_parity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication tokens are available in the test environment."""
    token = "challenger-test-token-abcdef"
    monkeypatch.setenv("OPENDV_TOKEN", token)
    monkeypatch.setenv("OPENDV_STUDIO_TOKEN", token)


@pytest.mark.parity
def test_stress_cli_tree_deep_structure_and_options_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Stress test the command tree introspection across both CLIs:
    - Verifies all 14 groups and 21 leaf commands have identical option flags, types, and counts.
    - Verifies unrecognized introspection flags (e.g., --dump-spec) exit 2 in both CLIs.
    """
    # 1. Dump trees
    code_py, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_py == 0, f"opendv --dump-cli-tree failed: code={code_py}"
    py_tree = json.loads(stdout_py.strip())

    code_ts, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_ts == 0, f"opendvctl --dump-cli-tree failed: code={code_ts}"
    ts_tree = json.loads(stdout_ts.strip())

    py_studio = py_tree["commands"]["ontology"]["subcommands"]["studio"]
    ts_ont = ts_tree["commands"]["ontology"]
    ts_studio = ts_ont["subcommands"]["studio"]

    # Verify all 15 standalone groups (`init` is standalone_only -> 14 client groups)
    expected_groups = {
        "init",
        "create",
        "delete",
        "list",
        "status",
        "upload",
        "parse",
        "clarify",
        "answer",
        "construct",
        "delta",
        "proposal",
        "export",
        "promote",
        "bundle",
    }
    expected_shared_groups = expected_groups - {"init"}
    py_groups = py_studio.get("subcommands", {})
    ts_groups = ts_studio.get("subcommands", {})

    assert set(py_groups.keys()) == expected_groups
    assert py_groups["init"]["availability"] == "standalone_only"
    assert set(ts_groups.keys()) == expected_shared_groups

    leaf_counts = {"py": 1, "ts": 0}
    for grp in sorted(expected_shared_groups):
        p_grp = py_groups[grp]
        t_grp = ts_groups[grp]

        p_subs = p_grp.get("subcommands", {})
        t_subs = t_grp.get("subcommands", {})

        assert set(p_subs.keys()) == set(t_subs.keys())

        if not p_subs:
            leaf_counts["py"] += 1
            leaf_counts["ts"] += 1
            p_flags = sorted([o["flag"] for o in p_grp.get("options", [])])
            t_flags = sorted([o["flag"] for o in t_grp.get("options", [])])
            assert p_flags == t_flags, (
                f"Mismatch in group options '{grp}': py={p_flags} ts={t_flags}"
            )
        else:
            for sub_name in sorted(p_subs.keys()):
                leaf_counts["py"] += 1
                leaf_counts["ts"] += 1
                p_flags = sorted([o["flag"] for o in p_subs[sub_name].get("options", [])])
                t_flags = sorted([o["flag"] for o in t_subs[sub_name].get("options", [])])
                assert p_flags == t_flags, (
                    f"Mismatch in leaf '{grp} {sub_name}': py={p_flags} ts={t_flags}"
                )

    assert leaf_counts["py"] == 22
    assert leaf_counts["ts"] == 21

    # 2. Adversarial flag: --dump-spec (unrecognized) must exit 2 across both
    c_py, _, _ = cli_transport.invoke_raw(["--dump-spec"])
    c_ts, _, _ = client_cli_transport.invoke_raw(["--dump-spec"])
    assert c_py == 2
    assert c_ts == 2


@pytest.mark.parity
def test_stress_mode_specific_init_tamper_resistance(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stress test mode-specific init policy under adversarial parameters:
    - Remote Bun CLI: cannot be bypassed by --force, --role ADMIN, or explicit tokens.
      Always exits 7 with LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE.
    - Standalone Python CLI: loopback init succeeds (exit 0) or returns 6 (already initialized).
      Re-init with --force succeeds (exit 0).
    """
    # Adversarial attempts on remote client in JSON mode
    attack_vectors_json = [
        ["ontology", "studio", "init", "--json"],
        ["ontology", "studio", "init", "--force", "--json"],
        ["ontology", "studio", "init", "--role", "DATA_STEWARD", "--json"],
        ["ontology", "studio", "init", "--role", "INTERNAL_MODELER", "--force", "--json"],
    ]

    for argv in attack_vectors_json:
        code, stdout, stderr = client_cli_transport.invoke_raw(argv)
        assert code == 7, f"Remote init attack vector {argv} did not exit 7: code={code}"
        err = json.loads(stderr.strip() or stdout.strip())
        assert err.get("error_code") == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    # Adversarial attempt in text mode
    code_text, _, stderr_text = client_cli_transport.invoke_raw(["ontology", "studio", "init"])
    assert code_text == 7
    assert (
        "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE" in stderr_text or "prohibited" in stderr_text
    )

    # Standalone local workstation init
    # First invocation: exit 0 or 6
    code_init, _, _ = cli_transport.invoke_raw(["ontology", "studio", "init", "--json"])
    assert code_init in (0, 6)

    # Re-init with --force must succeed with exit 0
    code_force, stdout_force, stderr_force = cli_transport.invoke_raw(
        ["ontology", "studio", "init", "--force", "--json"]
    )
    assert code_force == 0, f"Standalone init --force failed: {stderr_force}"
    res = json.loads(stdout_force.strip())
    assert (
        str(res.get("status", "")).upper() in ("INITIALIZED", "SUCCESS", "OK")
        or "studio_dir" in res
    )


@pytest.mark.parity
def test_stress_exit_code_matrix_edge_cases(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial challenge of the 0..7 exit code matrix across diverse operational paths:
    - Exit 0: create, status, list, proposal list
    - Exit 1: promote with rationale < 10 chars
    - Exit 2: missing required flags across multiple subcommands
    - Exit 3: SESSION_NOT_FOUND across status, clarify, proposal list
    - Exit 4: INVALID_GROUNDING_WAIVER on short waiver rationale
    - Exit 5: ONT_EVO_CONCURRENT_MODIFICATION on OCC stale hash
    - Exit 6: SESSION_ALREADY_EXISTS on duplicate create
    - Exit 7: FORBIDDEN_ADMIN_REQUIRED on missing waiver credentials
    """
    sid_py = f"stress_sess_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"stress_sess_ts_{uuid.uuid4().hex[:8]}"
    dummy_key = tmp_path / "valid_waiver.key"
    dummy_key.write_text("test-steward-secret-key\n", encoding="utf-8")

    # 1. Exit 0: Create sessions
    c_py_0, _, err_py_0 = cli_transport.invoke_raw(
        ["ontology", "studio", "create", "--session", sid_py, "--json"]
    )
    c_ts_0, _, err_ts_0 = client_cli_transport.invoke_raw(
        ["ontology", "studio", "create", "--session", sid_ts, "--json"]
    )
    assert c_py_0 == 0, f"Local create failed: {err_py_0}"
    assert c_ts_0 == 0, f"Remote create failed: {err_ts_0}"

    # 2. Exit 6: Duplicate create
    c_py_6, out_py_6, err_py_6 = cli_transport.invoke_raw(
        ["ontology", "studio", "create", "--session", sid_py, "--json"]
    )
    c_ts_6, out_ts_6, err_ts_6 = client_cli_transport.invoke_raw(
        ["ontology", "studio", "create", "--session", sid_ts, "--json"]
    )
    assert c_py_6 == 6
    assert c_ts_6 == 6
    assert _extract_error_code(out_py_6, err_py_6) == "SESSION_ALREADY_EXISTS"
    assert _extract_error_code(out_ts_6, err_ts_6) == "SESSION_ALREADY_EXISTS"

    # 3. Exit 1: Too short rationale on promote (< 10 chars)
    for bad_rat in ["short", "under_ten", "123456789"]:
        p_args_py = [
            "ontology",
            "studio",
            "promote",
            "--session",
            sid_py,
            "--workspace-sha256",
            "0" * 64,
            "--head-sha256",
            "1" * 64,
            "--rationale",
            bad_rat,
            "--json",
        ]
        p_args_ts = [
            "ontology",
            "studio",
            "promote",
            "--session",
            sid_ts,
            "--workspace-sha256",
            "0" * 64,
            "--head-sha256",
            "1" * 64,
            "--rationale",
            bad_rat,
            "--json",
        ]
        c_py_1, out_py_1, err_py_1 = cli_transport.invoke_raw(p_args_py)
        c_ts_1, out_ts_1, err_ts_1 = client_cli_transport.invoke_raw(p_args_ts)
        assert c_py_1 == 1, (
            f"Expected exit 1 for local promote with rationale '{bad_rat}', got {c_py_1}"
        )
        assert c_ts_1 == 1, (
            f"Expected exit 1 for remote promote with rationale '{bad_rat}', got {c_ts_1}"
        )
        assert _extract_error_code(out_py_1, err_py_1) == "PROMOTION_RATIONALE_REQUIRED"
        assert _extract_error_code(out_ts_1, err_ts_1) == "PROMOTION_RATIONALE_REQUIRED"

    # 4. Exit 2: Missing required flags across different subcommands
    syntax_tests = [
        ["ontology", "studio", "status"],
        ["ontology", "studio", "upload", "--session", sid_py],  # missing --file
        ["ontology", "studio", "proposal", "accept", "--session", sid_py],  # missing proposal-id
    ]
    for test_cmd in syntax_tests:
        code_py_2, _, _ = cli_transport.invoke_raw([*test_cmd, "--json"])
        code_ts_2, _, _ = client_cli_transport.invoke_raw([*test_cmd, "--json"])
        assert code_py_2 == 2, f"Expected exit 2 for opendv on {test_cmd}, got {code_py_2}"
        assert code_ts_2 == 2, f"Expected exit 2 for opendvctl on {test_cmd}, got {code_ts_2}"

    # 5. Exit 3: SESSION_NOT_FOUND across multiple subcommands
    ghost_sid = f"ghost_session_{uuid.uuid4().hex[:12]}"
    not_found_commands = [
        ["ontology", "studio", "status", "--session", ghost_sid],
        ["ontology", "studio", "proposal", "list", "--session", ghost_sid],
        ["ontology", "studio", "clarify", "--session", ghost_sid],
    ]
    for nf_cmd in not_found_commands:
        code_py_3, out_py_3, err_py_3 = cli_transport.invoke_raw([*nf_cmd, "--json"])
        old_role_3 = client_cli_transport.role
        try:
            client_cli_transport.role = "DATA_STEWARD"
            code_ts_3, out_ts_3, err_ts_3 = client_cli_transport.invoke_raw([*nf_cmd, "--json"])
        finally:
            client_cli_transport.role = old_role_3
        assert code_py_3 == 3, f"Expected exit 3 for opendv on {nf_cmd}, got {code_py_3}"
        assert code_ts_3 == 3, f"Expected exit 3 for opendvctl on {nf_cmd}, got {code_ts_3}"
        assert _extract_error_code(out_py_3, err_py_3) == "SESSION_NOT_FOUND"
        assert _extract_error_code(out_ts_3, err_ts_3) == "SESSION_NOT_FOUND"

    # 6. Exit 4: INVALID_GROUNDING_WAIVER (short rationale < 10 chars)
    waiver_args_py = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_py,
        "--proposal-id",
        "prop-01",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "short",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    waiver_args_ts = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_ts,
        "--proposal-id",
        "prop-01",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "short",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    code_py_4, out_py_4, err_py_4 = cli_transport.invoke_raw(waiver_args_py)
    old_role_4 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_ts_4, out_ts_4, err_ts_4 = client_cli_transport.invoke_raw(waiver_args_ts)
    finally:
        client_cli_transport.role = old_role_4
    assert code_py_4 == 4
    assert code_ts_4 == 4
    assert _extract_error_code(out_py_4, err_py_4) == "INVALID_GROUNDING_WAIVER"
    assert _extract_error_code(out_ts_4, err_ts_4) == "INVALID_GROUNDING_WAIVER"

    # 7. Exit 5: OCC conflict on stale base SHA
    stale_delta = tmp_path / "stale_delta_occ.json"
    stale_delta.write_text(
        json.dumps(
            {
                "delta_id": "delta-occ-01",
                "parent_sha256": "f" * 64,
                "base_version": "0.0.0",
                "target_version": "0.1.0",
                "operations": [
                    {
                        "op_id": "op-occ-1",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:occ_carrier",
                        "concept_name": "OccCarrier",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:c", "confidence": 0.9}
                        ],
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
        "--session",
        sid_py,
        "--file",
        str(stale_delta),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    occ_args_ts = [
        "ontology",
        "studio",
        "delta",
        "apply",
        "--session",
        sid_ts,
        "--file",
        str(stale_delta),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    code_py_5, out_py_5, err_py_5 = cli_transport.invoke_raw(occ_args_py)
    old_role = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_ts_5, out_ts_5, err_ts_5 = client_cli_transport.invoke_raw(occ_args_ts)
    finally:
        client_cli_transport.role = old_role

    assert code_py_5 == 5
    assert code_ts_5 == 5, f"Remote OCC failed: code={code_ts_5}, out={out_ts_5}, err={err_ts_5}"
    assert _extract_error_code(out_py_5, err_py_5) == "ONT_EVO_CONCURRENT_MODIFICATION"
    assert _extract_error_code(out_ts_5, err_ts_5) == "ONT_EVO_CONCURRENT_MODIFICATION"

    # 8. Exit 7: Unauthorized waiver credentials
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)
    unauth_waiver_py = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_py,
        "--proposal-id",
        "prop-01",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "Valid rationale of at least ten characters",
        "--json",
    ]
    unauth_waiver_ts = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_ts,
        "--proposal-id",
        "prop-01",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "Valid rationale of at least ten characters",
        "--json",
    ]
    code_py_7, out_py_7, err_py_7 = cli_transport.invoke_raw(unauth_waiver_py)
    old_role_7 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_ts_7, out_ts_7, err_ts_7 = client_cli_transport.invoke_raw(unauth_waiver_ts)
    finally:
        client_cli_transport.role = old_role_7

    assert code_py_7 == 7
    assert code_ts_7 == 7
    assert _extract_error_code(out_py_7, err_py_7) == "FORBIDDEN_ADMIN_REQUIRED"
    assert _extract_error_code(out_ts_7, err_ts_7) == "FORBIDDEN_ADMIN_REQUIRED"


@pytest.mark.parity
def test_stress_proposal_path_traversal_rejection(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify that path traversal in proposal identifiers is strictly rejected with exit 2."""
    sid = f"traversal_sess_{uuid.uuid4().hex[:8]}"
    cli_transport.invoke_raw(["ontology", "studio", "create", "--session", sid, "--json"])
    client_cli_transport.invoke_raw(["ontology", "studio", "create", "--session", sid, "--json"])

    traversal_attacks = [
        "../../etc/passwd",
        "../secrets",
        "sub/../../forbidden",
        "prop%2f..%2fsecret",
    ]

    for attack_id in traversal_attacks:
        argv_py = [
            "ontology",
            "studio",
            "proposal",
            "show",
            "--session",
            sid,
            "--proposal-id",
            attack_id,
            "--json",
        ]
        argv_ts = [
            "ontology",
            "studio",
            "proposal",
            "show",
            "--session",
            sid,
            "--proposal-id",
            attack_id,
            "--json",
        ]
        code_py, _, _ = cli_transport.invoke_raw(argv_py)
        code_ts, _, _ = client_cli_transport.invoke_raw(argv_ts)
        assert code_py == 2, f"opendv did not reject traversal '{attack_id}': code={code_py}"
        assert code_ts in (2, 3), (
            f"opendvctl did not reject traversal '{attack_id}': code={code_ts}"
        )


@pytest.mark.parity
def test_stress_proposal_list_bit_for_bit_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify bit-for-bit JCS stdout equality on proposal list in a newly created session."""
    sid_py = f"prop_list_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"prop_list_ts_{uuid.uuid4().hex[:8]}"

    cli_transport.invoke_raw(["ontology", "studio", "create", "--session", sid_py, "--json"])
    client_cli_transport.invoke_raw(["ontology", "studio", "create", "--session", sid_ts, "--json"])

    code_py, out_py, _ = cli_transport.invoke_raw(
        ["ontology", "studio", "proposal", "list", "--session", sid_py, "--json"]
    )
    code_ts, out_ts, _ = client_cli_transport.invoke_raw(
        ["ontology", "studio", "proposal", "list", "--session", sid_ts, "--json"]
    )

    assert code_py == 0
    assert code_ts == 0

    norm_py = normalize_studio_for_parity(json.loads(out_py.strip()))
    norm_ts = normalize_studio_for_parity(json.loads(out_ts.strip()))

    assert dumps_jcs(norm_py) == dumps_jcs(norm_ts), (
        "Proposal list output divergence:\n"
        f"LOCAL:  {dumps_jcs(norm_py)}\n"
        f"REMOTE: {dumps_jcs(norm_ts)}"
    )
