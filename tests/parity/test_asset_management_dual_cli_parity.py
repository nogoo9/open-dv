"""tests/parity/test_asset_management_dual_cli_parity.py — Dual-CLI Command Parity Suite.

Certifies Feature 15 for Milestone M4:
- End-to-end command parity between `opendv` (Python) and `opendvctl` (TypeScript Bun)
  across all 3 Asset Management progression waves:
  * Wave 0: Pipeline validation in unstructured-only mode (`--unstructured-only`)
    and free-form text CAS ingestion.
  * Wave 1: Vertical starter packs listing and application (`core_spine`, etc.).
  * Wave 2: Multi-format Graph A ontology export (`--export-format yaml|turtle|jsonld`).
- Exit-Code Matrix Parity across 0..7:
  * 0: Clean execution (create, starter-pack list, export)
  * 1: Semantic error (promotion without adequate rationale)
  * 2: CLI syntax error / unrecognized flag / missing argument
  * 3: Resource not found (ghost session status query)
  * 4: Governance / waiver validation rejection (short waiver rationale)
  * 5: Optimistic Concurrency Control (OCC) conflict on stale base SHA
  * 6: Duplicate state conflict (duplicate session creation)
  * 7: Remote security restriction (`ontology studio init` on remote client)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

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


@pytest.fixture(autouse=True)
def studio_parity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OPENDV_TOKEN and OPENDV_STUDIO_TOKEN are set for authenticated parity testing."""
    token = "parity-test-token-012345"
    monkeypatch.setenv("OPENDV_TOKEN", token)
    monkeypatch.setenv("OPENDV_STUDIO_TOKEN", token)


def _extract_error_code(stdout: str, stderr: str) -> str:
    """Extract standard OpenDV error code from JSON output or raw stderr."""
    for text in (stdout, stderr):
        clean = text.strip()
        if not clean:
            continue
        try:
            data = json.loads(clean)
            if isinstance(data, dict):
                code = data.get("error_code") or data.get("code") or data.get("error")
                if code:
                    return str(code)
                diag = data.get("diagnostics")
                if isinstance(diag, list) and diag and isinstance(diag[0], dict):
                    c = diag[0].get("code")
                    if c:
                        return str(c)
        except (json.JSONDecodeError, ValueError):
            pass
        for line in clean.splitlines():
            for prefix in ("ERROR:", "Error:", "error_code:", "code:"):
                if prefix in line:
                    return line.split(prefix, 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Wave 0: Unstructured-Only Pipeline & Text CAS Ingestion Parity
# ---------------------------------------------------------------------------


@pytest.mark.parity
def test_wave0_pipeline_validate_unstructured_only_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity for `pipeline validate -i <manifest> --unstructured-only --json`.

    1. Positive parity: When --unstructured-only is enabled on an unstructured-only wave,
       both CLIs deterministically exit with code 0 and return passed: True with empty diagnostics.
    2. Negative parity: When --unstructured-only is omitted on an unstructured-only wave,
       both CLIs deterministically exit with code 1 and flag UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE.
    """
    doc_path = tmp_path / "memo_calpers_2026.txt"
    doc_path.write_text("CalPERS 2026 Asset Management Allocation Strategy Memo", encoding="utf-8")

    # Positive: manifest with unstructured_only: True
    w0_manifest_valid = {
        "pipeline_id": "pipe-am-w0-unstructured",
        "project_id": "proj-am-001",
        "run_id": "run-am-w0-valid",
        "display_name": "Asset Management Wave 0 Unstructured",
        "domain_namespace": "asset_management",
        "graph_a_ontology_path": str(COMMERCE_ONTOLOGY_PATH),
        "unstructured_only": True,
        "waves": [
            {
                "wave_index": 0,
                "wave_name": "Wave 0 — Genesis Unstructured Documents Only",
                "wave_type": "UNSTRUCTURED_ONBOARDING",
                "description": "Cold start unstructured onboarding",
                "unstructured_documents": [
                    {
                        "doc_id": "DOC-CALPERS-001",
                        "document_path": str(doc_path),
                        "record_source": "CAS_INGESTION",
                    }
                ],
            }
        ],
    }
    manifest_valid_path = tmp_path / "w0_manifest_valid.yaml"
    manifest_valid_path.write_text(yaml.dump(w0_manifest_valid), encoding="utf-8")

    cmd_valid = [
        "pipeline",
        "validate",
        "-i",
        str(manifest_valid_path),
        "--unstructured-only",
        "--json",
    ]
    code_py_v, stdout_py_v, stderr_py_v = cli_transport.invoke_raw(cmd_valid)
    code_ts_v, stdout_ts_v, stderr_ts_v = client_cli_transport.invoke_raw(cmd_valid)

    assert code_py_v == 0, f"opendv pipeline validate failed: {stderr_py_v}"
    assert code_ts_v == 0, f"opendvctl pipeline validate failed: {stderr_ts_v}"
    assert code_py_v == code_ts_v

    json_py_v = json.loads(stdout_py_v.strip())
    json_ts_v = json.loads(stdout_ts_v.strip())
    assert json_py_v.get("passed") is True
    assert json_ts_v.get("passed") is True
    assert json_py_v.get("diagnostics") == []
    assert json_ts_v.get("diagnostics") == []

    # Negative: manifest without unstructured_only (and run without --unstructured-only)
    w0_manifest_invalid = {
        "pipeline_id": "pipe-am-w0-invalid",
        "project_id": "proj-am-001",
        "run_id": "run-am-w0-invalid",
        "display_name": "Asset Management Wave 0 Missing Relational Source",
        "domain_namespace": "asset_management",
        "graph_a_ontology_path": str(COMMERCE_ONTOLOGY_PATH),
        "waves": [
            {
                "wave_index": 0,
                "wave_name": "Wave 0 — Unstructured Documents Missing Prior Relational Source",
                "wave_type": "UNSTRUCTURED_ONBOARDING",
                "description": "Cold start unstructured onboarding without flag",
                "unstructured_documents": [
                    {
                        "doc_id": "DOC-CALPERS-002",
                        "document_path": str(doc_path),
                        "record_source": "CAS_INGESTION",
                    }
                ],
            }
        ],
    }
    manifest_invalid_path = tmp_path / "w0_manifest_invalid.yaml"
    manifest_invalid_path.write_text(yaml.dump(w0_manifest_invalid), encoding="utf-8")

    cmd_invalid = [
        "pipeline",
        "validate",
        "-i",
        str(manifest_invalid_path),
        "--json",
    ]
    code_py_inv, stdout_py_inv, stderr_py_inv = cli_transport.invoke_raw(cmd_invalid)
    code_ts_inv, stdout_ts_inv, stderr_ts_inv = client_cli_transport.invoke_raw(cmd_invalid)

    assert code_py_inv == 1, f"Expected opendv to reject with exit 1, got {code_py_inv}"
    assert code_ts_inv == 1, f"Expected opendvctl to reject with exit 1, got {code_ts_inv}"
    assert code_py_inv == code_ts_inv

    assert (
        "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in stdout_py_inv
        or "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in stderr_py_inv
    )
    assert (
        "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in stdout_ts_inv
        or "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in stderr_ts_inv
    )


@pytest.mark.parity
def test_wave0_text_cas_ingestion_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify dual-CLI parity for `ontology studio text add` CAS ingestion."""
    sid_py = f"parity_text_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"parity_text_ts_{uuid.uuid4().hex[:8]}"

    # 1. Create sessions in both
    c_py_c, _, err_py_c = cli_transport.invoke_raw(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            sid_py,
            "--domain-namespace",
            "asset_management",
            "--json",
        ]
    )
    c_ts_c, _, err_ts_c = client_cli_transport.invoke_raw(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            sid_ts,
            "--domain-namespace",
            "asset_management",
            "--json",
        ]
    )
    assert c_py_c == 0, f"Local create failed: {err_py_c}"
    assert c_ts_c == 0, f"Remote create failed: {err_ts_c}"

    # 2. Add text via both CLIs
    text_payload = "Private Credit Direct Lending Allocation Memo 2026: 450bps margin over SOFR."
    add_args_py = [
        "ontology",
        "studio",
        "text",
        "add",
        "-s",
        sid_py,
        "-t",
        text_payload,
        "--source-name",
        "private_credit_memo_01",
        "--json",
    ]
    add_args_ts = [
        "ontology",
        "studio",
        "text",
        "add",
        "-s",
        sid_ts,
        "-t",
        text_payload,
        "--source-name",
        "private_credit_memo_01",
        "--json",
    ]

    c_py_t, out_py_t, err_py_t = cli_transport.invoke_raw(add_args_py)
    c_ts_t, out_ts_t, err_ts_t = client_cli_transport.invoke_raw(add_args_ts)

    assert c_py_t == 0, f"Local text add failed: {err_py_t}"
    assert c_ts_t == 0, f"Remote text add failed: {err_ts_t}"
    assert c_py_t == c_ts_t

    data_py = json.loads(out_py_t.strip())
    data_ts = json.loads(out_ts_t.strip())
    assert data_py.get("file_name") == "private_credit_memo_01"
    assert data_ts.get("file_name") == "private_credit_memo_01"


# ---------------------------------------------------------------------------
# Wave 1: Starter Packs Parity
# ---------------------------------------------------------------------------


@pytest.mark.parity
def test_wave1_starter_pack_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify dual-CLI parity for `ontology studio starter-pack list` and `apply`."""
    # 1. Starter pack list parity
    c_py_l, out_py_l, err_py_l = cli_transport.invoke_raw(
        ["ontology", "studio", "starter-pack", "list", "--json"]
    )
    c_ts_l, out_ts_l, err_ts_l = client_cli_transport.invoke_raw(
        ["ontology", "studio", "starter-pack", "list", "--json"]
    )

    assert c_py_l == 0, f"Local starter-pack list failed: {err_py_l}"
    assert c_ts_l == 0, f"Remote starter-pack list failed: {err_ts_l}"
    assert c_py_l == c_ts_l

    packs_py = json.loads(out_py_l.strip())
    packs_ts = json.loads(out_ts_l.strip())
    list_py = packs_py if isinstance(packs_py, list) else packs_py.get("packs", [])
    list_ts = packs_ts if isinstance(packs_ts, list) else packs_ts.get("packs", [])

    py_ids = {p["pack_id"] for p in list_py if "pack_id" in p}
    ts_ids = {p["pack_id"] for p in list_ts if "pack_id" in p}

    expected_packs = {
        "core_spine",
        "public_equities_oms",
        "multi_asset_ibor",
        "private_credit_direct_lending",
        "custody_clearing",
        "fixed_income_rates",
        "all_domains",
    }
    assert expected_packs.issubset(py_ids), (
        f"Missing starter packs in opendv: {expected_packs - py_ids}"
    )
    assert expected_packs.issubset(ts_ids), (
        f"Missing starter packs in opendvctl: {expected_packs - ts_ids}"
    )
    assert py_ids == ts_ids

    # 2. Starter pack apply parity
    sid_py = f"parity_sp_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"parity_sp_ts_{uuid.uuid4().hex[:8]}"

    old_role = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                sid_py,
                "--domain-namespace",
                "asset_management",
                "--json",
            ]
        )
        client_cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                sid_ts,
                "--domain-namespace",
                "asset_management",
                "--json",
            ]
        )

        apply_py = [
            "ontology",
            "studio",
            "starter-pack",
            "apply",
            "-s",
            sid_py,
            "--pack",
            "public_equities_oms",
            "--json",
        ]
        apply_ts = [
            "ontology",
            "studio",
            "starter-pack",
            "apply",
            "-s",
            sid_ts,
            "--pack",
            "public_equities_oms",
            "--json",
        ]

        c_py_a, _out_py_a, err_py_a = cli_transport.invoke_raw(apply_py)
        c_ts_a, _out_ts_a, err_ts_a = client_cli_transport.invoke_raw(apply_ts)

        assert c_py_a == 0, f"Local apply failed: {err_py_a}"
        assert c_ts_a == 0, f"Remote apply failed: {err_ts_a}"
        assert c_py_a == c_ts_a
    finally:
        client_cli_transport.role = old_role


# ---------------------------------------------------------------------------
# Wave 2: Multi-Format Export Parity (YAML, Turtle, JSON-LD)
# ---------------------------------------------------------------------------


@pytest.mark.parity
def test_wave2_multiformat_export_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify dual-CLI parity for `ontology studio export --export-format yaml|turtle|jsonld`."""
    sid_py = f"parity_exp_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"parity_exp_ts_{uuid.uuid4().hex[:8]}"

    # Setup session with starter pack applied in both
    old_role_exp = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                sid_py,
                "--domain-namespace",
                "asset_management",
                "--json",
            ]
        )
        client_cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                sid_ts,
                "--domain-namespace",
                "asset_management",
                "--json",
            ]
        )
        cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "starter-pack",
                "apply",
                "-s",
                sid_py,
                "--pack",
                "core_spine",
                "--json",
            ]
        )
        client_cli_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "starter-pack",
                "apply",
                "-s",
                sid_ts,
                "--pack",
                "core_spine",
                "--json",
            ]
        )

        # Test formats
        formats_to_test = [
            ("yaml", ["canonical_business_concepts", "domain_namespace"]),
            ("turtle", ["@prefix", "owl:Ontology"]),
            ("jsonld", ["@id", "@type"]),
        ]

        for fmt, expected_tokens in formats_to_test:
            cmd_py = ["ontology", "studio", "export", "-s", sid_py, "-e", fmt, "--json"]
            cmd_ts = ["ontology", "studio", "export", "-s", sid_ts, "-e", fmt, "--json"]

            c_py, out_py, err_py = cli_transport.invoke_raw(cmd_py)
            c_ts, out_ts, err_ts = client_cli_transport.invoke_raw(cmd_ts)

            assert c_py == 0, f"opendv export -e {fmt} failed: {err_py}"
            assert c_ts == 0, f"opendvctl export -e {fmt} failed: {err_ts}"
            assert c_py == c_ts

            data_py = json.loads(out_py.strip())
            data_ts = json.loads(out_ts.strip())

            content_py = data_py.get("content", "")
            content_ts = data_ts.get("content", "")

            assert len(content_py) > 0
            assert len(content_ts) > 0

            for tok in expected_tokens:
                assert tok in content_py, f"Missing '{tok}' in opendv {fmt} export"
                assert tok in content_ts, f"Missing '{tok}' in opendvctl {fmt} export"
    finally:
        client_cli_transport.role = old_role_exp


# ---------------------------------------------------------------------------
# Exit Code Matrix Parity Across 0..7
# ---------------------------------------------------------------------------


@pytest.mark.parity
def test_dual_cli_exit_code_matrix_parity_0_to_7(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Certify exit code matrix parity 0 through 7 between opendv and opendvctl."""
    sid_py = f"matrix_py_{uuid.uuid4().hex[:8]}"
    sid_ts = f"matrix_ts_{uuid.uuid4().hex[:8]}"

    # EXIT 0: Clean Success
    c_py_0, _, err_py_0 = cli_transport.invoke_raw(
        ["ontology", "studio", "create", "-s", sid_py, "--json"]
    )
    c_ts_0, _, err_ts_0 = client_cli_transport.invoke_raw(
        ["ontology", "studio", "create", "-s", sid_ts, "--json"]
    )
    assert c_py_0 == 0, f"Local create failed: {err_py_0}"
    assert c_ts_0 == 0, f"Remote create failed: {err_ts_0}"
    assert c_py_0 == c_ts_0

    # EXIT 1: Promotion rationale missing or too short
    short_rationale_py = [
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
        "short",
        "--json",
    ]
    short_rationale_ts = [
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
        "short",
        "--json",
    ]
    c_py_1, out_py_1, err_py_1 = cli_transport.invoke_raw(short_rationale_py)
    c_ts_1, out_ts_1, err_ts_1 = client_cli_transport.invoke_raw(short_rationale_ts)
    assert c_py_1 == 1
    assert c_ts_1 == 1
    assert _extract_error_code(out_py_1, err_py_1) == "PROMOTION_RATIONALE_REQUIRED"
    assert _extract_error_code(out_ts_1, err_ts_1) == "PROMOTION_RATIONALE_REQUIRED"

    # EXIT 2: CLI syntax error / unrecognized flag
    bad_flag_py = [
        "ontology",
        "studio",
        "status",
        "-s",
        sid_py,
        "--unrecognized-bogus-option",
        "--json",
    ]
    bad_flag_ts = [
        "ontology",
        "studio",
        "status",
        "-s",
        sid_ts,
        "--unrecognized-bogus-option",
        "--json",
    ]
    c_py_2, _, _ = cli_transport.invoke_raw(bad_flag_py)
    c_ts_2, _, _ = client_cli_transport.invoke_raw(bad_flag_ts)
    assert c_py_2 == 2
    assert c_ts_2 == 2

    # EXIT 3: Not found (nonexistent session)
    ghost_sid = f"nonexistent_{uuid.uuid4().hex[:8]}"
    ghost_py = ["ontology", "studio", "status", "-s", ghost_sid, "--json"]
    ghost_ts = ["ontology", "studio", "status", "-s", ghost_sid, "--json"]
    c_py_3, out_py_3, err_py_3 = cli_transport.invoke_raw(ghost_py)
    c_ts_3, out_ts_3, err_ts_3 = client_cli_transport.invoke_raw(ghost_ts)
    assert c_py_3 == 3
    assert c_ts_3 == 3
    assert _extract_error_code(out_py_3, err_py_3) == "SESSION_NOT_FOUND"
    assert _extract_error_code(out_ts_3, err_ts_3) == "SESSION_NOT_FOUND"

    # EXIT 4: Short waiver rationale on proposal accept
    dummy_key = tmp_path / "waiver.key"
    dummy_key.write_text("test-secret-key-123", encoding="utf-8")
    short_waiver_py = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "-s",
        sid_py,
        "--proposal-id",
        "prop_1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "tiny",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    short_waiver_ts = [
        "ontology",
        "studio",
        "proposal",
        "accept",
        "-s",
        sid_ts,
        "--proposal-id",
        "prop_1",
        "--base-sha256",
        "0" * 64,
        "--low-confidence-waiver",
        "--waiver-rationale",
        "tiny",
        "--waiver-key-file",
        str(dummy_key),
        "--json",
    ]
    c_py_4, out_py_4, err_py_4 = cli_transport.invoke_raw(short_waiver_py)
    old_role_4 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        c_ts_4, out_ts_4, err_ts_4 = client_cli_transport.invoke_raw(short_waiver_ts)
    finally:
        client_cli_transport.role = old_role_4

    assert c_py_4 == 4
    assert c_ts_4 == 4
    assert _extract_error_code(out_py_4, err_py_4) == "INVALID_GROUNDING_WAIVER"
    assert _extract_error_code(out_ts_4, err_ts_4) == "INVALID_GROUNDING_WAIVER"

    # EXIT 5: OCC conflict on stale base SHA
    stale_delta = tmp_path / "stale_delta.json"
    stale_delta.write_text(
        json.dumps(
            {
                "delta_id": "delta-stale-01",
                "parent_sha256": "f" * 64,
                "base_version": "0.0.0",
                "target_version": "0.1.0",
                "operations": [
                    {
                        "op_id": "op1",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:concept:stale",
                        "target_hub": "hub_stale",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    occ_py = [
        "ontology",
        "studio",
        "delta",
        "apply",
        "-s",
        sid_py,
        "-f",
        str(stale_delta),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    occ_ts = [
        "ontology",
        "studio",
        "delta",
        "apply",
        "-s",
        sid_ts,
        "-f",
        str(stale_delta),
        "--base-version",
        "0.0.0",
        "--base-sha256",
        "f" * 64,
        "--json",
    ]
    c_py_5, out_py_5, err_py_5 = cli_transport.invoke_raw(occ_py)
    old_role_5 = client_cli_transport.role
    try:
        client_cli_transport.role = "DATA_STEWARD"
        c_ts_5, out_ts_5, err_ts_5 = client_cli_transport.invoke_raw(occ_ts)
    finally:
        client_cli_transport.role = old_role_5

    assert c_py_5 == 5
    assert c_ts_5 == 5
    assert _extract_error_code(out_py_5, err_py_5) == "ONT_EVO_CONCURRENT_MODIFICATION"
    assert _extract_error_code(out_ts_5, err_ts_5) == "ONT_EVO_CONCURRENT_MODIFICATION"

    # EXIT 6: State conflict (duplicate session creation)
    c_py_6, out_py_6, err_py_6 = cli_transport.invoke_raw(
        ["ontology", "studio", "create", "-s", sid_py, "--json"]
    )
    c_ts_6, out_ts_6, err_ts_6 = client_cli_transport.invoke_raw(
        ["ontology", "studio", "create", "-s", sid_ts, "--json"]
    )
    assert c_py_6 == 6
    assert c_ts_6 == 6
    assert _extract_error_code(out_py_6, err_py_6) == "SESSION_ALREADY_EXISTS"
    assert _extract_error_code(out_ts_6, err_ts_6) == "SESSION_ALREADY_EXISTS"

    # EXIT 7: Remote security restriction (ontology studio init forbidden on remote opendvctl)
    c_ts_7, out_ts_7, err_ts_7 = client_cli_transport.invoke_raw(
        ["ontology", "studio", "init", "--json"]
    )
    assert c_ts_7 == 7
    assert _extract_error_code(out_ts_7, err_ts_7) == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"
