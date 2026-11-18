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

import hashlib
import json
import uuid
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

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
        "sources": [],
        "unstructured_documents": [
            {
                "doc_id": "DOC-CALPERS-001",
                "document_path": str(doc_path),
                "record_source": "CAS_INGESTION",
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
        "sources": [],
        "unstructured_documents": [
            {
                "doc_id": "DOC-CALPERS-002",
                "document_path": str(doc_path),
                "record_source": "CAS_INGESTION",
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


def _create_ecommerce_sqlite_db(db_path: Path) -> Path:
    """Create a temporary SQLite database with PK and FK constraints for parity testing."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            full_name TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_total REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
        """
    )
    cur.execute("INSERT INTO customers VALUES (1, 'alice@example.com', 'Alice Smith')")
    cur.execute("INSERT INTO orders VALUES (101, 1, 99.50)")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Workstream 4 (WS-4.3): Dual-CLI Single-Run & Source Inspect Parity
# ---------------------------------------------------------------------------


@pytest.mark.parity
def test_source_inspect_dual_cli_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity for `opendv source inspect` and `opendvctl source inspect`.

    1. Positive parity with SQLite DB:
       - Extracts tables, columns, PK, and FK constraints.
       - Returns exit code 0 on both CLIs with identical table structures.
    2. Positional URI vs --uri flag parity:
       - Both CLIs accept `source inspect <path>` and `source inspect --uri <path>`.
    3. Table filtering parity:
       - Both CLIs support `--table <tbl>` filtering.
    """
    db_path = tmp_path / "retail.sqlite"
    _create_ecommerce_sqlite_db(db_path)

    # 1. Positional URI
    cmd_pos = ["source", "inspect", str(db_path), "--json"]
    c_py, out_py, err_py = cli_transport.invoke_raw(cmd_pos)
    c_ts, out_ts, err_ts = client_cli_transport.invoke_raw(cmd_pos)

    assert c_py == 0, f"opendv source inspect positional failed: {err_py}"
    assert c_ts == 0, f"opendvctl source inspect positional failed: {err_ts}"
    assert c_py == c_ts

    data_py = json.loads(out_py.strip())
    data_ts = json.loads(out_ts.strip())

    assert data_py.get("adapter_id") == "sqlite"
    assert data_ts.get("adapter_id") == "sqlite"
    assert len(data_py.get("tables", [])) == 2
    assert len(data_ts.get("tables", [])) == 2

    py_table_names = sorted(t["table_name"] for t in data_py["tables"])
    ts_table_names = sorted(t["table_name"] for t in data_ts["tables"])
    assert py_table_names == ["customers", "orders"]
    assert ts_table_names == ["customers", "orders"]

    # Verify primary keys and foreign keys parity
    py_tables = {t["table_name"]: t for t in data_py["tables"]}
    ts_tables = {t["table_name"]: t for t in data_ts["tables"]}
    assert py_tables["customers"]["primary_key_columns"] == ["customer_id"]
    assert ts_tables["customers"]["primary_key_columns"] == ["customer_id"]
    assert len(py_tables["orders"]["foreign_keys"]) >= 1
    assert len(ts_tables["orders"]["foreign_keys"]) >= 1
    assert py_tables["orders"]["foreign_keys"][0]["target_table"] == "customers"
    assert ts_tables["orders"]["foreign_keys"][0]["target_table"] == "customers"

    # 2. Flag-based URI: --uri
    cmd_flag = ["source", "inspect", "--uri", f"sqlite:///{db_path.as_posix()}", "--json"]
    c_py_f, out_py_f, err_py_f = cli_transport.invoke_raw(cmd_flag)
    c_ts_f, out_ts_f, err_ts_f = client_cli_transport.invoke_raw(cmd_flag)
    assert c_py_f == 0, f"opendv source inspect --uri failed: {err_py_f}"
    assert c_ts_f == 0, f"opendvctl source inspect --uri failed: {err_ts_f}"
    assert c_py_f == c_ts_f

    # 3. Table filter: --table customers
    cmd_tbl = ["source", "inspect", str(db_path), "--table", "customers", "--json"]
    c_py_t, out_py_t, err_py_t = cli_transport.invoke_raw(cmd_tbl)
    c_ts_t, out_ts_t, err_ts_t = client_cli_transport.invoke_raw(cmd_tbl)
    assert c_py_t == 0, f"opendv table filter failed: {err_py_t}"
    assert c_ts_t == 0, f"opendvctl table filter failed: {err_ts_t}"
    t_py = json.loads(out_py_t.strip())
    t_ts = json.loads(out_ts_t.strip())
    assert len(t_py.get("tables", [])) == 1
    assert len(t_ts.get("tables", [])) == 1
    assert t_py["tables"][0]["table_name"] == "customers"
    assert t_ts["tables"][0]["table_name"] == "customers"


@pytest.mark.parity
def test_source_inspect_negative_dual_cli_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity on syntax errors and security enforcement for `source inspect`.

    1. Missing --uri: both CLIs exit 2 with SYNTAX_INVALID_ARGUMENT.
    2. Invalid credentials_ref: both CLIs exit 2 with SYNTAX_INVALID_ARGUMENT.
    """
    db_path = tmp_path / "retail.sqlite"
    _create_ecommerce_sqlite_db(db_path)

    # 1. Missing URI
    cmd_no_uri = ["source", "inspect", "--json"]
    c_py_m, out_py_m, err_py_m = cli_transport.invoke_raw(cmd_no_uri)
    c_ts_m, out_ts_m, err_ts_m = client_cli_transport.invoke_raw(cmd_no_uri)

    assert c_py_m == 2, f"Expected exit 2, got {c_py_m}: {err_py_m}"
    assert c_ts_m == 2, f"Expected exit 2, got {c_ts_m}: {err_ts_m}"
    assert c_py_m == c_ts_m
    assert _extract_error_code(out_py_m, err_py_m) == "SYNTAX_INVALID_ARGUMENT"
    assert _extract_error_code(out_ts_m, err_ts_m) == "SYNTAX_INVALID_ARGUMENT"

    # 2. Invalid credentials_ref pattern
    cmd_bad_cred = [
        "source",
        "inspect",
        str(db_path),
        "--credentials-ref",
        "plaintext_secret_123",
        "--json",
    ]
    c_py_c, out_py_c, err_py_c = cli_transport.invoke_raw(cmd_bad_cred)
    c_ts_c, out_ts_c, err_ts_c = client_cli_transport.invoke_raw(cmd_bad_cred)

    assert c_py_c == 2, f"Expected exit 2, got {c_py_c}: {err_py_c}"
    assert c_ts_c == 2, f"Expected exit 2, got {c_ts_c}: {err_ts_c}"
    assert c_py_c == c_ts_c
    assert _extract_error_code(out_py_c, err_py_c) == "SYNTAX_INVALID_ARGUMENT"
    assert _extract_error_code(out_ts_c, err_ts_c) == "SYNTAX_INVALID_ARGUMENT"


@pytest.mark.parity
def test_single_run_pipeline_validate_dual_cli_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity for `pipeline validate` single-run options (--source, --ontology).

    1. Positive parity: Greenfield Cold Start with SQLite source and Graph A ontology
       exits 0 on both CLIs and returns InferredExecutionPlanDTO with primary_action COLD_START_N1.
    2. Negative parity: Missing --input, --blueprint, and --ontology
       exits 2 on both CLIs with SYNTAX_INVALID_ARGUMENT.
    """
    db_path = tmp_path / "crm.sqlite"
    _create_ecommerce_sqlite_db(db_path)

    # Positive: single-run with --source and --ontology
    cmd_val = [
        "pipeline",
        "validate",
        "--source",
        f"sqlite:///{db_path.as_posix()}",
        "--ontology",
        str(COMMERCE_ONTOLOGY_PATH),
        "--dialect",
        "duckdb",
        "--json",
    ]
    c_py, out_py, err_py = cli_transport.invoke_raw(cmd_val)
    c_ts, out_ts, err_ts = client_cli_transport.invoke_raw(cmd_val)

    assert c_py == 0, f"opendv pipeline validate failed: {err_py}"
    assert c_ts == 0, f"opendvctl pipeline validate failed: {err_ts}"
    assert c_py == c_ts

    plan_py = json.loads(out_py.strip())
    plan_ts = json.loads(out_ts.strip())

    assert plan_py.get("primary_action") == "COLD_START_N1"
    assert plan_ts.get("primary_action") == "COLD_START_N1"
    assert "plan_id" in plan_py
    assert "plan_id" in plan_ts
    assert len(plan_py.get("table_actions", [])) >= 1
    assert len(plan_ts.get("table_actions", [])) >= 1

    # Negative: missing ingress
    cmd_neg = ["pipeline", "validate", "--json"]
    c_py_n, out_py_n, err_py_n = cli_transport.invoke_raw(cmd_neg)
    c_ts_n, out_ts_n, err_ts_n = client_cli_transport.invoke_raw(cmd_neg)

    assert c_py_n == 2, f"Expected exit 2, got {c_py_n}: {err_py_n}"
    assert c_ts_n == 2, f"Expected exit 2, got {c_ts_n}: {err_ts_n}"
    assert c_py_n == c_ts_n
    assert _extract_error_code(out_py_n, err_py_n) == "SYNTAX_INVALID_ARGUMENT"
    assert _extract_error_code(out_ts_n, err_ts_n) == "SYNTAX_INVALID_ARGUMENT"


@pytest.mark.parity
def test_single_run_pipeline_run_dual_cli_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity for `pipeline run` single-run options (--source, --ontology).

    1. Missing ingress: both CLIs exit 2 with SYNTAX_INVALID_ARGUMENT.
    2. Single-run execution: both CLIs accept single-run options and exit 0.
    """
    monkeypatch.setenv("OPENDV_STORAGE_ROOT", str(tmp_path / "cli_state"))
    db_path = tmp_path / "crm_run.sqlite"
    _create_ecommerce_sqlite_db(db_path)

    # 1. Missing ingress negative check
    cmd_neg = ["pipeline", "run", "--json"]
    c_py_n, out_py_n, err_py_n = cli_transport.invoke_raw(cmd_neg)
    c_ts_n, out_ts_n, err_ts_n = client_cli_transport.invoke_raw(cmd_neg)

    assert c_py_n == 2, f"Expected exit 2, got {c_py_n}: {err_py_n}"
    assert c_ts_n == 2, f"Expected exit 2, got {c_ts_n}: {err_ts_n}"
    assert c_py_n == c_ts_n
    assert _extract_error_code(out_py_n, err_py_n) == "SYNTAX_INVALID_ARGUMENT"
    assert _extract_error_code(out_ts_n, err_ts_n) == "SYNTAX_INVALID_ARGUMENT"

    # 2. Single-run execution (configure symbolic_completion on both isolated roots for offline CI)
    cmd_cfg = [
        "config",
        "set",
        "--stage-output-policy",
        "symbolic_completion",
        "--json",
    ]
    c_py_cfg, _, err_py_cfg = cli_transport.invoke_raw(cmd_cfg)
    c_ts_cfg, _, err_ts_cfg = client_cli_transport.invoke_raw(cmd_cfg)
    assert c_py_cfg == 0, f"opendv config set failed: {err_py_cfg}"
    assert c_ts_cfg == 0, f"opendvctl config set failed: {err_ts_cfg}"

    cmd_run = [
        "pipeline",
        "run",
        "--source",
        f"sqlite:///{db_path.as_posix()}",
        "--ontology",
        str(COMMERCE_ONTOLOGY_PATH),
        "--dialect",
        "duckdb",
        "--json",
    ]
    c_py, out_py, err_py = cli_transport.invoke_raw(cmd_run)
    c_ts, out_ts, err_ts = client_cli_transport.invoke_raw(cmd_run)

    assert c_py == 0, f"opendv pipeline run failed: {err_py}"
    assert c_ts == 0, f"opendvctl pipeline run failed: {err_ts}"
    assert c_py == c_ts

    res_py = json.loads(out_py.strip())
    res_ts = json.loads(out_ts.strip())
    assert isinstance(res_py, dict)
    assert isinstance(res_ts, dict)


@pytest.mark.parity
def test_r3_c2_pipeline_input_dto_e2e_validator_parity(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    platform_api_client: TestClient,
    tmp_path: Path,
) -> None:
    """Verify dual-CLI parity hitting the real FastAPI PipelineInputDTO validator (R3-C2).

    1. Ensures client omits client *_path fields and undeclared keys.
    2. Ensures steward_hold_policy is correctly mapped inside governance_policy.
    3. Confirms both CLIs exit 0 without 422 extra_forbidden validation errors.
    """
    db_path = tmp_path / "crm_validator.sqlite"
    _create_ecommerce_sqlite_db(db_path)

    cmd_val = [
        "pipeline",
        "validate",
        "--source",
        f"sqlite:///{db_path.as_posix()}",
        "--ontology",
        str(COMMERCE_ONTOLOGY_PATH),
        "--hold-policy",
        "pause",
        "--provider",
        "agy",
        "--json",
    ]
    c_py_val, out_py_val, err_py_val = cli_transport.invoke_raw(cmd_val)
    c_ts_val, out_ts_val, err_ts_val = client_cli_transport.invoke_raw(cmd_val)

    assert c_py_val == 0, f"opendv validate failed: {err_py_val}"
    assert c_ts_val == 0, f"opendvctl validate failed: {err_ts_val}"
    assert c_py_val == c_ts_val

    # Test direct REST submission to ensure FastAPI accepts single-run payload
    loaded_ont = yaml.safe_load(COMMERCE_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    payload = {
        "ontology_source": loaded_ont,
        "sources": [
            {
                "feed_id": "feed-crm-1",
                "source_system": "crm",
                "uri": f"sqlite:///{db_path.as_posix()}",
                "target_dialect": "duckdb",
            }
        ],
        "target_dialect": "duckdb",
        "harness_provider": "agy",
        "governance_policy": {
            "steward_hold_policy": "pause",
            "stage_output_policy": "symbolic_completion",
        },
    }
    resp = platform_api_client.post(
        "/api/v1/runs?sync=true",
        json=payload,
        headers={"X-OpenDV-Role": "INTERNAL_MODELER"},
    )
    assert resp.status_code == 200, f"FastAPI validation rejected single-run payload: {resp.text}"
    data = resp.json()
    assert isinstance(data, dict)
    assert data.get("status") in {"COMPLETED", "PAUSED_HOLD", "AUTO_REPAIRED"}


@pytest.mark.parity
def test_r3_c3_cas_upload_inspect_and_run_e2e(
    platform_api_client: TestClient,
    tmp_path: Path,
) -> None:
    """Verify CAS blob upload, source inspection of staged CAS files, and pipeline run with cas:// URI (R3-C3)."""
    db_path = tmp_path / "cas_test.sqlite"
    _create_ecommerce_sqlite_db(db_path)
    sqlite_bytes = db_path.read_bytes()
    sqlite_hash = hashlib.sha256(sqlite_bytes).hexdigest().lower()

    csv_path = tmp_path / "customers.csv"
    csv_path.write_text("customer_id,email,created_at\n1,alice@example.com,2026-01-01\n", encoding="utf-8")
    csv_bytes = csv_path.read_bytes()
    csv_hash = hashlib.sha256(csv_bytes).hexdigest().lower()

    auth_hdr = {"X-OpenDV-Role": "DATA_STEWARD"}

    # 1. Upload SQLite blob to POST /api/v1/cas/blobs
    resp_up_sqlite = platform_api_client.post(
        "/api/v1/cas/blobs",
        content=sqlite_bytes,
        headers={**auth_hdr, "Content-Type": "application/octet-stream", "X-CAS-SHA256": sqlite_hash},
    )
    assert resp_up_sqlite.status_code == 201, f"Failed to upload SQLite: {resp_up_sqlite.text}"
    assert resp_up_sqlite.json()["sha256"] == sqlite_hash

    # 2. Upload CSV blob to POST /api/v1/cas/blobs
    resp_up_csv = platform_api_client.post(
        "/api/v1/cas/blobs",
        content=csv_bytes,
        headers={**auth_hdr, "Content-Type": "application/octet-stream", "X-CAS-SHA256": csv_hash},
    )
    assert resp_up_csv.status_code == 201, f"Failed to upload CSV: {resp_up_csv.text}"
    assert resp_up_csv.json()["sha256"] == csv_hash

    # 3. Inspect staged SQLite blob via POST /api/v1/sources/inspect
    resp_ins_sqlite = platform_api_client.post(
        "/api/v1/sources/inspect",
        json={"uri": f"cas://{sqlite_hash}", "adapter": "sqlite"},
        headers=auth_hdr,
    )
    assert resp_ins_sqlite.status_code == 200, f"Failed to inspect CAS SQLite: {resp_ins_sqlite.text}"
    sqlite_meta = resp_ins_sqlite.json()
    assert len(sqlite_meta["tables"]) >= 1
    table_names = [t["table_name"] for t in sqlite_meta["tables"]]
    assert any("order" in name.lower() or "customer" in name.lower() for name in table_names)

    # 4. Inspect staged CSV blob via POST /api/v1/sources/inspect
    resp_ins_csv = platform_api_client.post(
        "/api/v1/sources/inspect",
        json={"uri": f"cas://{csv_hash}", "adapter": "file"},
        headers=auth_hdr,
    )
    assert resp_ins_csv.status_code == 200, f"Failed to inspect CAS CSV: {resp_ins_csv.text}"
    csv_meta = resp_ins_csv.json()
    assert csv_meta["adapter_id"] == "file"
    assert "fingerprint" in csv_meta

    # 5. Trigger single-run execution via POST /api/v1/runs with cas:// URI
    loaded_ont = yaml.safe_load(COMMERCE_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    run_payload = {
        "ontology_source": loaded_ont,
        "sources": [
            {
                "feed_id": "cas-sqlite-feed-1",
                "source_system": "cas_store",
                "uri": f"cas://{sqlite_hash}",
                "target_dialect": "duckdb",
            }
        ],
        "target_dialect": "duckdb",
        "harness_provider": "agy",
        "governance_policy": {
            "stage_output_policy": "symbolic_completion",
        },
    }
    resp_run = platform_api_client.post(
        "/api/v1/runs?sync=true",
        json=run_payload,
        headers={"X-OpenDV-Role": "INTERNAL_MODELER"},
    )
    assert resp_run.status_code == 200, f"Pipeline run with cas:// failed: {resp_run.text}"
    run_data = resp_run.json()
    assert isinstance(run_data, dict)
    assert run_data.get("status") in {"COMPLETED", "PAUSED_HOLD", "AUTO_REPAIRED"}

