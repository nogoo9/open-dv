"""Phase 2 TDD Tests: DV Schema Explorer API & DuckDB Sandbox Rehydration (`FR-DVE-01,05,06`, `D11`).

Covers:
1. `GET /api/v1/runs/{run_id}/dv-schema` projecting `CanonicalIR` + `NoMistakeDataVault2ContractV5` + scorecard.
2. `GET /api/v1/runs/{run_id}/dv-schema?compare=<base_run_id>` multi-wave diff projection.
3. `GET /api/v1/runs/{run_id}/dv-schema/tables/{table}/rows` paginated row preview from persisted
   `.opendv/state/sandboxes/<run_id>.duckdb`, including rehydration into a fresh `PlatformStateContainer`.
4. `POST /api/v1/runs/{run_id}/dv-schema/query` allowing read-only `SELECT`/`UNION` queries while
   blocking mutating (`DROP`/`INSERT`) and file-scan (`read_text`, `FROM 'file.csv'`) SQL with `400`/`422`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

from opendv_platform_api import create_app
from opendv_service import OpenDVSettings
from opendv_testkit import deterministic_pipeline_seam

REPO_ROOT = Path(__file__).resolve().parents[1]
GC_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def _seed_completed_run(tmp_path: Path) -> tuple[TestClient, Path, Path, str]:
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=state_dir)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    ont_dict = yaml.safe_load(
        (GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml").read_text(encoding="utf-8")
    )
    crm_csv = (GC_DIR / "sources" / "wave0_cold_start" / "crm_customers.csv").resolve()
    erp_csv = (GC_DIR / "sources" / "wave0_cold_start" / "erp_orders.csv").resolve()
    payload = {
        "ontology_source": ont_dict,
        "sources": [
            {
                "feed_id": "crm_customers",
                "source_system": "crm_salesforce",
                "adapter": "file",
                "uri": crm_csv.as_posix(),
                "options": {
                    "table_name": "crm_customers",
                    "business_key_columns": ["customer_tax_id"],
                    "record_source_code": "CRM_SALESFORCE",
                },
            },
            {
                "feed_id": "erp_orders",
                "source_system": "erp_netsuite",
                "adapter": "file",
                "uri": erp_csv.as_posix(),
                "options": {
                    "table_name": "erp_orders",
                    "business_key_columns": ["order_id"],
                    "foreign_key_mappings": {"customer_tax_id": "hub_customer"},
                    "record_source_code": "ERP_NETSUITE",
                },
            },
        ],
        "target_dialect": "duckdb",
        "harness_provider": "agy",
        "governance_policy": {
            "steward_hold_policy": "pause",
            "stage_output_policy": "symbolic_completion",
        },
    }

    with deterministic_pipeline_seam():
        resp = client.post("/api/v1/runs?sync=true", json=payload)
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    return client, ws, state_dir, run_id


def test_dv_schema_explorer_projection_and_compare_diff(tmp_path: Path) -> None:
    """`GET /runs/{id}/dv-schema` returns Hubs/Links/Sats/Ledger and `?compare=` computes wave diff."""
    client, ws, state_dir, run1_id = _seed_completed_run(tmp_path)

    schema_resp = client.get(f"/api/v1/runs/{run1_id}/dv-schema")
    assert schema_resp.status_code == 200, schema_resp.text
    data = schema_resp.json()
    assert data["run_id"] == run1_id
    assert len(data["hubs"]) >= 1
    assert len(data["satellites"]) >= 1
    assert len(data["column_triage_ledger"]) >= 1
    assert "scorecard" in data

    # Run a second wave with incremental billing source and compare against run1_id
    ont_dict = yaml.safe_load(
        (GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml").read_text(encoding="utf-8")
    )
    stripe_csv = (GC_DIR / "sources" / "wave1_incremental_billing" / "stripe_invoices.csv").resolve()
    w1_payload = {
        "ontology_source": ont_dict,
        "sources": [
            {
                "feed_id": "stripe_invoices",
                "source_system": "stripe_billing",
                "adapter": "file",
                "uri": stripe_csv.as_posix(),
                "options": {
                    "table_name": "stripe_invoices",
                    "business_key_columns": ["invoice_id"],
                    "record_source_code": "STRIPE_BILLING",
                },
            }
        ],
        "target_dialect": "duckdb",
        "harness_provider": "agy",
        "governance_policy": {
            "steward_hold_policy": "pause",
            "stage_output_policy": "symbolic_completion",
        },
    }

    with deterministic_pipeline_seam():
        r2 = client.post("/api/v1/runs?sync=true", json=w1_payload)
    assert r2.status_code == 200, r2.text
    run2_id = r2.json()["run_id"]

    diff_resp = client.get(f"/api/v1/runs/{run2_id}/dv-schema?compare={run1_id}")
    assert diff_resp.status_code == 200, diff_resp.text
    diff_body = diff_resp.json()
    assert diff_body.get("compare_run_id") == run1_id
    assert "wave_diff" in diff_body
    assert diff_body["wave_diff"]["dropped_columns_count"] == 0
    assert diff_body["wave_diff"]["hash_diff_mutations_count"] == 0


def test_dv_schema_persisted_duckdb_rehydration_rows_and_safe_sql(tmp_path: Path) -> None:
    """Persisted `.duckdb` sandbox rehydrates in a fresh app for `/tables/{table}/rows` and `/query`."""
    client, ws, state_dir, run_id = _seed_completed_run(tmp_path)

    # Confirm persisted DuckDB sandbox file exists under state/sandboxes/<run_id>.duckdb
    sandbox_file = state_dir / "sandboxes" / f"{run_id}.duckdb"
    assert sandbox_file.is_file(), f"Expected persisted DuckDB sandbox at {sandbox_file}"

    # Rehydrate using a brand-new FastAPI app pointing to the same storage_root
    fresh_settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    fresh_app = create_app(settings=fresh_settings, workspace_root=ws, storage_root=state_dir)
    fresh_client = TestClient(fresh_app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    rows_resp = fresh_client.get(f"/api/v1/runs/{run_id}/dv-schema/tables/hub_customer/rows?limit=25&offset=0")
    assert rows_resp.status_code == 200, rows_resp.text
    rows_body = rows_resp.json()
    assert rows_body["table_name"] == "hub_customer"
    assert len(rows_body["rows"]) >= 2  # Includes dual zero keys and loaded rows

    # Safe SELECT query passes
    ok_q = fresh_client.post(
        f"/api/v1/runs/{run_id}/dv-schema/query",
        json={"sql": "SELECT COUNT(*) AS cnt FROM raw_vault.hub_customer", "max_rows": 50},
    )
    assert ok_q.status_code == 200, ok_q.text
    assert ok_q.json()["rows"][0]["cnt"] >= 2

    # Mutating and file-scan SQL must be blocked (400 or 422)
    for bad_sql in (
        "DROP TABLE raw_vault.hub_customer",
        "INSERT INTO raw_vault.hub_customer VALUES ('x', 'y', 'z', CURRENT_TIMESTAMP, 'bad')",
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM 'secret.csv'",
    ):
        bad_q = fresh_client.post(
            f"/api/v1/runs/{run_id}/dv-schema/query",
            json={"sql": bad_sql, "max_rows": 50},
        )
        assert bad_q.status_code in (400, 422), f"Unsafe SQL allowed: {bad_sql} -> {bad_q.status_code}"
