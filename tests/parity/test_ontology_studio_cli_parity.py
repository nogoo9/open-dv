"""tests/parity/test_ontology_studio_cli_parity.py — Track 5C Dual-CLI Studio Parity Suite.

Verifies:
1. `--dump-cli-tree` Canonical Tree Parity across standalone `opendv` and Bun client `opendvctl`
   for the entire `ontology studio` command tree (14 groups, 21 leaf commands).
2. Shared Subcommands Bit-for-Bit Parity: JCS canonical stdout equality and exit code
   parity across all shared studio subcommands with volatile fields normalized.
3. Exit Code Parity Matrix across 0..7:
   - 0: Clean operation execution
   - 1: Promotion rationale missing / too short (`PROMOTION_RATIONALE_REQUIRED`)
   - 2: CLI syntax error / argument validation (`SYNTAX_INVALID_ARGUMENT`)
   - 3: Not found (`SESSION_NOT_FOUND`)
   - 4: Invalid waiver rationale / contract violation (`INVALID_GROUNDING_WAIVER`)
   - 5: OCC conflict on stale base SHA (`ONT_EVO_CONCURRENT_MODIFICATION`)
   - 6: Duplicate session creation (`SESSION_ALREADY_EXISTS`)
        and re-init (`STUDIO_ALREADY_INITIALIZED`)
   - 7: Remote init disabled (`LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE`)
        and unauthorized waiver (`FORBIDDEN_ADMIN_REQUIRED`)
4. Mode-Specific Policy:
   - `ontology studio init` prohibited in remote client mode with exit 7.
   - `ontology studio init` permitted in standalone mode for local workstation initialization.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.jcs import dumps_jcs
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

# Volatile fields that vary by execution instance, time, or environment
_VOLATILE_FIELDS = frozenset(
    {
        "job_id",
        "created_at",
        "updated_at",
        "proposal_id",
        "session_id",
        "waiver_id",
        "authorized_by",
        "signature_or_token",
        "promoted_at",
        "indexed_at",
        "bundle_path",
    }
)


@pytest.fixture(autouse=True)
def studio_parity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OPENDV_TOKEN and OPENDV_STUDIO_TOKEN are set for authenticated parity testing."""
    token = "parity-test-token-012345"
    monkeypatch.setenv("OPENDV_TOKEN", token)
    monkeypatch.setenv("OPENDV_STUDIO_TOKEN", token)


def normalize_studio_for_parity(payload: Any) -> Any:
    """Recursively mask volatile runtime fields and environment-dependent paths."""
    if isinstance(payload, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(payload.keys()):
            val = payload[key]
            if key in _VOLATILE_FIELDS:
                normalized[key] = f"<{key.upper()}>"
            elif isinstance(val, (dict, list)):
                normalized[key] = normalize_studio_for_parity(val)
            elif isinstance(val, str) and ("\\" in val or "/" in val):
                # Normalize file path separators
                normalized[key] = val.replace("\\", "/")
            else:
                normalized[key] = val
        return normalized
    elif isinstance(payload, list):
        return [normalize_studio_for_parity(item) for item in payload]
    return payload


@pytest.mark.parity
def test_dump_cli_tree_studio_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify opendv and opendvctl dump bit-for-bit identical command trees for ontology studio."""
    code_py, stdout_py, _ = cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_py == 0, f"opendv --dump-cli-tree failed with code {code_py}"
    py_tree = json.loads(stdout_py.strip())

    code_ts, stdout_ts, _ = client_cli_transport.invoke_raw(["--dump-cli-tree"])
    assert code_ts == 0, f"opendvctl --dump-cli-tree failed with code {code_ts}"
    ts_tree = json.loads(stdout_ts.strip())

    # Locate ontology studio in both trees
    py_ont = py_tree["commands"].get("ontology")
    assert py_ont is not None, "Missing ontology command in python CLI tree"
    py_studio = py_ont["subcommands"].get("studio")
    assert py_studio is not None, "Missing studio subcommand in python CLI tree"

    ts_ont = ts_tree["commands"].get("ontology")
    assert ts_ont is not None, "Missing ontology command in TypeScript CLI tree"
    ts_studio = ts_ont["subcommands"].get("studio")
    assert ts_studio is not None, "Missing studio subcommand in TypeScript CLI tree"

    # Verify studio subcommands are declared in both trees (`init` is standalone_only)
    py_groups = py_studio.get("subcommands", {})
    ts_groups = ts_studio.get("subcommands", {})

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
    assert set(py_groups.keys()) == expected_groups
    assert py_groups["init"]["availability"] == "standalone_only"
    assert set(ts_groups.keys()) == expected_shared_groups

    # Verify shared groups and leaf commands have identical structure and options
    leaf_count_py = 1  # standalone-only `init`
    leaf_count_ts = 0

    for group_name in sorted(expected_shared_groups):
        py_grp = py_groups[group_name]
        ts_grp = ts_groups[group_name]

        py_subs = py_grp.get("subcommands", {})
        ts_subs = ts_grp.get("subcommands", {})

        assert set(py_subs.keys()) == set(ts_subs.keys()), (
            f"Subcommand mismatch in group '{group_name}': "
            f"py={sorted(py_subs.keys())} ts={sorted(ts_subs.keys())}"
        )

        if not py_subs:
            # Single-leaf command
            leaf_count_py += 1
            leaf_count_ts += 1
            py_opts = sorted([o["flag"] for o in py_grp.get("options", [])])
            ts_opts = sorted([o["flag"] for o in ts_grp.get("options", [])])
            assert py_opts == ts_opts, (
                f"Option mismatch in group '{group_name}': py={py_opts} ts={ts_opts}"
            )
        else:
            # Multi-leaf command group
            for leaf_name in sorted(py_subs.keys()):
                leaf_count_py += 1
                leaf_count_ts += 1
                py_leaf = py_subs[leaf_name]
                ts_leaf = ts_subs[leaf_name]
                py_opts = sorted([o["flag"] for o in py_leaf.get("options", [])])
                ts_opts = sorted([o["flag"] for o in ts_leaf.get("options", [])])
                assert py_opts == ts_opts, (
                    f"Option mismatch in '{group_name} {leaf_name}': py={py_opts} ts={ts_opts}"
                )

    assert leaf_count_py == 22, f"Expected 22 python leaf commands, got {leaf_count_py}"
    assert leaf_count_ts == 21, f"Expected 21 TypeScript shared leaf commands, got {leaf_count_ts}"


@pytest.mark.parity
def test_studio_mode_specific_init_policy(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify mode-specific policy enforcement on 'ontology studio init':
    - Standalone mode: loopback init succeeds or reports already initialized (exit 0 or 6).
    - Remote mode: strictly disabled with exit code 7 (LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE).
    """
    # 1. Remote mode: opendvctl prohibits init with exit code 7
    code_remote, stdout_remote, stderr_remote = client_cli_transport.invoke_raw(
        ["ontology", "studio", "init", "--json"]
    )
    assert code_remote == 7, (
        f"Remote opendvctl studio init must exit 7, got {code_remote}. Stderr: {stderr_remote}"
    )
    err_remote = json.loads(stderr_remote.strip() or stdout_remote.strip())
    assert err_remote.get("error_code") == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    # 2. Standalone mode: opendv allows local init
    code_local, stdout_local, stderr_local = cli_transport.invoke_raw(
        ["ontology", "studio", "init", "--json"]
    )
    assert code_local in (0, 6), (
        f"Standalone opendv studio init must exit 0 or 6, got {code_local}. Stderr: {stderr_local}"
    )
    if code_local == 6:
        err_local = json.loads(stderr_local.strip() or stdout_local.strip())
        assert err_local.get("error_code") == "STUDIO_ALREADY_INITIALIZED"


@pytest.mark.parity
def test_studio_session_lifecycle_bit_for_bit_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify bit-for-bit JCS stdout equality and exit code parity across session lifecycle:
    create -> list -> status.
    """
    session_id_py = f"parity_sess_py_{uuid.uuid4().hex[:8]}"
    session_id_ts = f"parity_sess_ts_{uuid.uuid4().hex[:8]}"

    # 1. Create session
    create_args_py = [
        "ontology",
        "studio",
        "create",
        "--session",
        session_id_py,
        "--domain-namespace",
        "logistics",
        "--json",
    ]
    create_args_ts = [
        "ontology",
        "studio",
        "create",
        "--session",
        session_id_ts,
        "--domain-namespace",
        "logistics",
        "--json",
    ]
    code_py_c, stdout_py_c, stderr_py_c = cli_transport.invoke_raw(create_args_py)
    code_ts_c, stdout_ts_c, stderr_ts_c = client_cli_transport.invoke_raw(create_args_ts)

    assert code_py_c == 0, f"Local create failed: {stderr_py_c}"
    assert code_ts_c == 0, f"Remote create failed: {stderr_ts_c}"
    assert code_py_c == code_ts_c

    py_create_json = normalize_studio_for_parity(json.loads(stdout_py_c.strip()))
    ts_create_json = normalize_studio_for_parity(json.loads(stdout_ts_c.strip()))
    assert dumps_jcs(py_create_json) == dumps_jcs(ts_create_json), (
        "Create output divergence:\n"
        f"LOCAL:  {dumps_jcs(py_create_json)}\n"
        f"REMOTE: {dumps_jcs(ts_create_json)}"
    )

    # 2. Status check
    status_args_py = ["ontology", "studio", "status", "--session", session_id_py, "--json"]
    status_args_ts = ["ontology", "studio", "status", "--session", session_id_ts, "--json"]
    code_py_s, stdout_py_s, _ = cli_transport.invoke_raw(status_args_py)
    code_ts_s, stdout_ts_s, _ = client_cli_transport.invoke_raw(status_args_ts)

    assert code_py_s == 0
    assert code_ts_s == 0
    assert code_py_s == code_ts_s

    py_status_json = normalize_studio_for_parity(json.loads(stdout_py_s.strip()))
    ts_status_json = normalize_studio_for_parity(json.loads(stdout_ts_s.strip()))
    assert dumps_jcs(py_status_json) == dumps_jcs(ts_status_json), (
        "Status output divergence:\n"
        f"LOCAL:  {dumps_jcs(py_status_json)}\n"
        f"REMOTE: {dumps_jcs(ts_status_json)}"
    )

    # 3. List check
    list_args = ["ontology", "studio", "list", "--limit", "500", "--json"]
    code_py_l, stdout_py_l, stderr_py_l = cli_transport.invoke_raw(list_args)
    code_ts_l, stdout_ts_l, stderr_ts_l = client_cli_transport.invoke_raw(list_args)

    assert code_py_l == 0, f"Local list failed: {stderr_py_l}"
    assert code_ts_l == 0, f"Remote list failed: {stderr_ts_l}"
    assert code_py_l == code_ts_l

    py_list_data = json.loads(stdout_py_l.strip())
    ts_list_data = json.loads(stdout_ts_l.strip())

    py_sessions = (
        py_list_data if isinstance(py_list_data, list) else py_list_data.get("sessions", [])
    )
    ts_sessions = (
        ts_list_data if isinstance(ts_list_data, list) else ts_list_data.get("sessions", [])
    )

    py_entry = next((s for s in py_sessions if s.get("session_id") == session_id_py), None)
    ts_entry = next((s for s in ts_sessions if s.get("session_id") == session_id_ts), None)
    assert py_entry is not None, f"Local list missing session {session_id_py}"
    assert ts_entry is not None, f"Remote list missing session {session_id_ts}"

    py_norm_entry = normalize_studio_for_parity(py_entry)
    ts_norm_entry = normalize_studio_for_parity(ts_entry)
    assert dumps_jcs(py_norm_entry) == dumps_jcs(ts_norm_entry), (
        "List session entry divergence:\n"
        f"LOCAL:  {dumps_jcs(py_norm_entry)}\n"
        f"REMOTE: {dumps_jcs(ts_norm_entry)}"
    )


def _extract_error_code(stdout: str, stderr: str) -> str | None:
    """Extract ProblemDetail error_code symbol from stdout or stderr payload."""
    for text in (stdout.strip(), stderr.strip()):
        if not text:
            continue
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                code = payload.get("error_code") or payload.get("code")
                if code:
                    return str(code)
        except json.JSONDecodeError:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        p = json.loads(line)
                        if isinstance(p, dict):
                            code = p.get("error_code") or p.get("code")
                            if code:
                                return str(code)
                    except json.JSONDecodeError:
                        pass
    return None


@pytest.mark.parity
def test_studio_exit_code_matrix_parity_0_to_7(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify strict exit code parity across 0..7 between opendv and opendvctl:
    - Exit 0: Success
    - Exit 1: Promotion rationale missing / short (PROMOTION_RATIONALE_REQUIRED)
    - Exit 2: Invalid CLI syntax / missing flags (SYNTAX_INVALID_ARGUMENT)
    - Exit 3: Session not found (SESSION_NOT_FOUND)
    - Exit 4: Invalid waiver rationale (INVALID_GROUNDING_WAIVER)
    - Exit 5: OCC conflict on stale base SHA (ONT_EVO_CONCURRENT_MODIFICATION)
    - Exit 6: Duplicate session creation (SESSION_ALREADY_EXISTS)
    - Exit 7: Unauthorized admin / disabled remote init
    """
    sid_py = f"matrix_sess_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"matrix_sess_ts_{uuid.uuid4().hex[:8]}"
    dummy_key = tmp_path / "test_matrix.key"
    dummy_key.write_text("test-steward-secret-key\n", encoding="utf-8")

    # Exit 0 & 6: Create session and then create duplicate
    create_args_py = [
        "ontology",
        "studio",
        "create",
        "--session",
        sid_py,
        "--domain-namespace",
        "logistics",
        "--json",
    ]
    create_args_ts = [
        "ontology",
        "studio",
        "create",
        "--session",
        sid_ts,
        "--domain-namespace",
        "logistics",
        "--json",
    ]
    code_local_0, _stdout_local_0, stderr_local_0 = cli_transport.invoke_raw(create_args_py)
    code_remote_0, _stdout_remote_0, stderr_remote_0 = client_cli_transport.invoke_raw(
        create_args_ts
    )
    assert code_local_0 == 0, f"Local create failed: {stderr_local_0}"
    assert code_remote_0 == 0, f"Remote create failed: {stderr_remote_0}"
    assert code_local_0 == code_remote_0

    # Exit 6: Duplicate session
    code_local_6, stdout_local_6, stderr_local_6 = cli_transport.invoke_raw(create_args_py)
    code_remote_6, stdout_remote_6, stderr_remote_6 = client_cli_transport.invoke_raw(
        create_args_ts
    )
    assert code_local_6 == 6, f"Local dup create exit code expected 6, got {code_local_6}"
    assert code_remote_6 == 6, f"Remote dup create exit code expected 6, got {code_remote_6}"
    assert _extract_error_code(stdout_local_6, stderr_local_6) == "SESSION_ALREADY_EXISTS"
    assert _extract_error_code(stdout_remote_6, stderr_remote_6) == "SESSION_ALREADY_EXISTS"

    # Exit 1: Promote with missing/short rationale
    promote_short_py = [
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
        "short",
        "--json",
    ]
    promote_short_ts = [
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
        "short",
        "--json",
    ]
    code_local_1, stdout_local_1, stderr_local_1 = cli_transport.invoke_raw(promote_short_py)
    code_remote_1, stdout_remote_1, stderr_remote_1 = client_cli_transport.invoke_raw(
        promote_short_ts
    )
    assert code_local_1 == 1, (
        f"Local promote short rationale exit code expected 1, got {code_local_1}"
    )
    assert code_remote_1 == 1, (
        f"Remote promote short rationale exit code expected 1, got {code_remote_1}"
    )
    assert _extract_error_code(stdout_local_1, stderr_local_1) == "PROMOTION_RATIONALE_REQUIRED"
    assert _extract_error_code(stdout_remote_1, stderr_remote_1) == "PROMOTION_RATIONALE_REQUIRED"

    # Exit 2: Syntax error / missing required flags
    syntax_args = ["ontology", "studio", "status", "--json"]
    code_local_2, _stdout_local_2, _stderr_local_2 = cli_transport.invoke_raw(syntax_args)
    code_remote_2, _stdout_remote_2, _stderr_remote_2 = client_cli_transport.invoke_raw(syntax_args)
    assert code_local_2 == 2, f"Local syntax error expected exit 2, got {code_local_2}"
    assert code_remote_2 == 2, f"Remote syntax error expected exit 2, got {code_remote_2}"

    # Exit 3: Session not found
    not_found_args = ["ontology", "studio", "status", "--session", "ghost_session_xyz", "--json"]
    code_local_3, stdout_local_3, stderr_local_3 = cli_transport.invoke_raw(not_found_args)
    code_remote_3, stdout_remote_3, stderr_remote_3 = client_cli_transport.invoke_raw(
        not_found_args
    )
    assert code_local_3 == 3, f"Local not found expected exit 3, got {code_local_3}"
    assert code_remote_3 == 3, f"Remote not found expected exit 3, got {code_remote_3}"
    assert _extract_error_code(stdout_local_3, stderr_local_3) == "SESSION_NOT_FOUND"
    assert _extract_error_code(stdout_remote_3, stderr_remote_3) == "SESSION_NOT_FOUND"

    # Exit 4: Waiver rationale too short
    waiver_short_py = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_py,
        "--proposal-id",
        "p1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "short",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    waiver_short_ts = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_ts,
        "--proposal-id",
        "p1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "short",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    code_local_4, stdout_local_4, stderr_local_4 = cli_transport.invoke_raw(waiver_short_py)
    old_role_4 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_remote_4, stdout_remote_4, stderr_remote_4 = client_cli_transport.invoke_raw(
            waiver_short_ts
        )
    finally:
        client_cli_transport.role = old_role_4
    assert code_local_4 == 4, f"Local short waiver expected exit 4, got {code_local_4}"
    assert code_remote_4 == 4, f"Remote short waiver expected exit 4, got {code_remote_4}"
    assert _extract_error_code(stdout_local_4, stderr_local_4) == "INVALID_GROUNDING_WAIVER"
    assert _extract_error_code(stdout_remote_4, stderr_remote_4) == "INVALID_GROUNDING_WAIVER"

    # Exit 5: Stale base SHA / OCC conflict on delta apply
    stale_delta_file = tmp_path / "stale_delta.json"
    stale_delta_file.write_text(
        json.dumps(
            {
                "delta_id": "delta-stale-01",
                "parent_sha256": "f" * 64,
                "base_version": "0.0.0",
                "target_version": "0.1.0",
                "operations": [
                    {
                        "op_id": "op-1",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:carrier_company",
                        "concept_name": "CarrierCompany",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:carrier", "confidence": 0.95}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    stale_apply_py = [
        "ontology",
        "studio",
        "delta",
        "apply",
        "--session",
        sid_py,
        "--file",
        str(stale_delta_file),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    stale_apply_ts = [
        "ontology",
        "studio",
        "delta",
        "apply",
        "--session",
        sid_ts,
        "--file",
        str(stale_delta_file),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    code_local_5, stdout_local_5, stderr_local_5 = cli_transport.invoke_raw(stale_apply_py)
    old_role = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_remote_5, stdout_remote_5, stderr_remote_5 = client_cli_transport.invoke_raw(
            stale_apply_ts
        )
    finally:
        client_cli_transport.role = old_role
    assert code_local_5 == 5, f"Local OCC mismatch expected exit 5, got {code_local_5}"
    assert code_remote_5 == 5, (
        f"Remote OCC mismatch expected exit 5, got {code_remote_5}, "
        f"out: {stdout_remote_5}, err: {stderr_remote_5}"
    )
    assert _extract_error_code(stdout_local_5, stderr_local_5) == "ONT_EVO_CONCURRENT_MODIFICATION"
    assert (
        _extract_error_code(stdout_remote_5, stderr_remote_5) == "ONT_EVO_CONCURRENT_MODIFICATION"
    )

    # Exit 7: Missing waiver credentials on low_confidence_waiver
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)
    waiver_unauth_py = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_py,
        "--proposal-id",
        "p1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "Valid rationale of at least ten characters",
        "--json",
    ]
    waiver_unauth_ts = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "--session",
        sid_ts,
        "--proposal-id",
        "p1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "Valid rationale of at least ten characters",
        "--json",
    ]
    code_local_7, stdout_local_7, stderr_local_7 = cli_transport.invoke_raw(waiver_unauth_py)
    old_role_7 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        code_remote_7, stdout_remote_7, stderr_remote_7 = client_cli_transport.invoke_raw(
            waiver_unauth_ts
        )
    finally:
        client_cli_transport.role = old_role_7

    assert code_local_7 == 7, f"Local unauth waiver expected exit 7, got {code_local_7}"
    assert code_remote_7 == 7, f"Remote unauth waiver expected exit 7, got {code_remote_7}"
    err_py_7 = _extract_error_code(stdout_local_7, stderr_local_7)
    err_ts_7 = _extract_error_code(stdout_remote_7, stderr_remote_7)
    assert err_py_7 == "FORBIDDEN_ADMIN_REQUIRED"
    assert err_ts_7 == "FORBIDDEN_ADMIN_REQUIRED"
    assert err_py_7 == err_ts_7


@pytest.mark.parity
def test_studio_delta_validate_bit_for_bit_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify bit-for-bit JCS stdout equality and exit code parity on delta validate."""
    delta_file = tmp_path / "valid_delta.json"
    delta_file.write_text(
        json.dumps(
            {
                "delta_id": "delta-test-01",
                "operations": [
                    {
                        "op_id": "op-1",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:carrier_company",
                        "concept_name": "CarrierCompany",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    validate_args = [
        "ontology",
        "studio",
        "delta",
        "validate",
        "--file",
        str(delta_file),
        "--base",
        str(
            Path(__file__).resolve().parents[2]
            / "examples"
            / "private_equity"
            / "pe_graph_a_ontology.yaml"
        ),
        "--json",
    ]
    code_local, stdout_local, stderr_local = cli_transport.invoke_raw(validate_args)
    code_remote, stdout_remote, stderr_remote = client_cli_transport.invoke_raw(validate_args)
    assert code_local == 0, f"Local delta validate failed: {stderr_local}"
    assert code_remote == 0, f"Remote delta validate failed: {stderr_remote}"
    assert code_local == code_remote

    py_val_json = normalize_studio_for_parity(json.loads(stdout_local.strip()))
    ts_val_json = normalize_studio_for_parity(json.loads(stdout_remote.strip()))
    assert dumps_jcs(py_val_json) == dumps_jcs(ts_val_json), (
        "Delta validate divergence:\n"
        f"LOCAL:  {dumps_jcs(py_val_json)}\n"
        f"REMOTE: {dumps_jcs(ts_val_json)}"
    )
