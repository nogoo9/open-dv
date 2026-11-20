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

import json
from pathlib import Path

import yaml
from starlette.testclient import TestClient

from opendv_contracts import RunStatus
from opendv_platform_api import create_app
from opendv_service import CreateProjectRequest, CreateRunRequest, OpenDVSettings
from opendv_service.use_cases.dv_schema_explorer import get_dv_schema_projection
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


def _seed_treasury_run_on_disk(
    tmp_path: Path,
    *,
    run_id: str = "run-treasury-w3",
    sat_payload_order: list[str] | None = None,
    esat_payload_order: list[str] | None = None,
    mas_payload_order: list[str] | None = None,
    extra_standard_col: str | None = None,
) -> tuple[TestClient, object, Path]:
    """Seed a persisted run with real ContractV5 + CanonicalIR (parent_entity PIT) + four_zone_sql."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    run_dir = state_dir / "pipeline_runs" / f"dir_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)

    sat_cols = sat_payload_order or ["market_value", "legacy_desk_memo"]
    if extra_standard_col:
        sat_cols = [*sat_cols, extra_standard_col]
    esat_cols = esat_payload_order or ["crosswalk_id", "effective_date", "match_confidence"]
    mas_cols = mas_payload_order or ["confidence_score", "extracted_value", "extraction_field"]

    contract_dict = {
        "contract_version": "5.0",
        "domain_namespace": "treasury_vault",
        "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
        "column_triage_ledger": [
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "position_id",
                "elm_classification": "BUSINESS_KEY",
                "source_sql_type": "TEXT",
                "target_dv_entity": "hub_position",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "market_value",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "REAL",
                "target_dv_entity": "sat_positions",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "legacy_desk_memo",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "TEXT",
                "target_dv_entity": "sat_positions",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "row_seq_sk",
                "elm_classification": "SYSTEM_SURROGATE_DROPPED",
                "source_sql_type": "INTEGER",
                "target_dv_entity": "DROPPED_SURROGATE",
            },
        ],
        "hubs": [
            {
                "name": "hub_position",
                "ontology_concept_uri": "urn:ontology:entity:position",
                "business_key_sources": [
                    {
                        "bronze_table": "ds01_sec_treasury.positions",
                        "key_columns": ["position_id"],
                        "record_source_code": "DS01_SEC_TREASURY_POSITIONS",
                        "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                        "require_src_qualifier": True,
                        "bkcc_prefix": "DS01_SEC_TREASURY_POSITIONS",
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [
            {
                "name": "sat_positions",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_position",
                "source_bronze_table": "ds01_sec_treasury.positions",
                "column_aliases": {"market_value": "mkt_val_usd"},
                "soft_deprecated_columns": ["legacy_desk_memo"],
                "payload_columns": sat_cols,
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }
    ir_dict = {
        "ir_version": "1.0.0",
        "model_name": "treasury_vault",
        "hubs": [
            {
                "name": "hub_position",
                "business_concept": "Position",
                "bkcc_literal": "DS01_SEC_TREASURY_POSITIONS",
                "hk_column": {"name": "hk_position"},
                "business_keys": [{"name": "bkcc"}, {"name": "position_id"}],
                "source_mappings": [
                    {
                        "staging_table": "stg_ds01_sec_treasury_positions",
                        "bronze_table": "ds01_sec_treasury.positions",
                        "bk_source_columns": ["bkcc", "position_id"],
                        "rec_src_value": "DS01_SEC_TREASURY_POSITIONS",
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [
            {
                "name": "sat_positions",
                "satellite_type": "STANDARD",
                "parent_entity": "hub_position",
                "hash_diff_column": {"name": "hash_diff"},
                "payload_columns": [{"name": c, "data_type": "TEXT"} for c in sat_cols],
                "source_mapping": {
                    "staging_table": "stg_ds01_sec_treasury_positions",
                    "rec_src_value": "DS01_SEC_TREASURY_POSITIONS",
                    "column_aliases": {"market_value": "mkt_val_usd"},
                    "soft_deprecated_columns": ["legacy_desk_memo"],
                },
            },
            {
                "name": "esat_sal_account",
                "satellite_type": "EFFECTIVITY",
                "parent_entity": "sal_account",
                "hash_diff_column": {"name": "hash_diff"},
                "payload_columns": [{"name": c, "data_type": "TEXT"} for c in esat_cols],
                "source_mapping": {
                    "staging_table": "stg_ds02_account_crosswalk",
                    "rec_src_value": "DS02_CROSSWALK",
                },
            },
            {
                "name": "mas_document_extractions",
                "satellite_type": "MULTI_ACTIVE",
                "parent_entity": "hub_document",
                "hash_diff_column": {"name": "hash_diff"},
                "payload_columns": [{"name": c, "data_type": "TEXT"} for c in mas_cols],
                "source_mapping": {
                    "staging_table": "stg_unstructured_documents",
                    "rec_src_value": "LEGAL_DOC_VAULT",
                },
            },
        ],
        "pit_tables": [
            {
                "name": "pit_position",
                "parent_entity": "hub_position",
                "as_of_dts_column": "as_of_dts",
                "tracked_satellites": ["sat_positions"],
            }
        ],
        "reference_tables": [],
    }
    sql_text = (
        "CREATE SCHEMA IF NOT EXISTS raw_vault;\n"
        "CREATE TABLE raw_vault.hub_position (hk_position CHAR(64), position_id VARCHAR);\n"
    )
    fc_path = run_dir / "final_contract.yaml"
    ir_path = run_dir / "final_canonical_ir.json"
    sql_path = run_dir / "final_four_zone.sql"
    fc_yaml = yaml.safe_dump(contract_dict, sort_keys=False)
    ir_json = json.dumps(ir_dict, indent=2)
    fc_path.write_text(fc_yaml, encoding="utf-8")
    ir_path.write_text(ir_json, encoding="utf-8")
    sql_path.write_text(sql_text, encoding="utf-8")

    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=state_dir)
    service = app.state.service
    service.state.create_project(
        CreateProjectRequest(project_id="proj-treasury", display_name="Treasury Vault")
    )
    created_run = service.state.create_run(
        CreateRunRequest(project_id="proj-treasury", harness_provider="antigravity")
    )
    service.state.runs.pop(created_run.run_id, None)
    service.state.runs[run_id] = created_run.model_copy(
        update={"run_id": run_id, "status": RunStatus.COMPLETED}
    )
    service.state.pipeline_reports[run_id] = {
        "run_id": run_id,
        "project_id": "proj-treasury",
        "status": "COMPLETED",
        "wave_id": "W3",
        "contract": contract_dict,
        "contract_content": fc_yaml,
        "four_zone_sql": {
            "duckdb": {
                "zone_0_staging": "CREATE SCHEMA IF NOT EXISTS staging;",
                "zone_1_raw_vault": "CREATE TABLE raw_vault.hub_position (hk_position CHAR(64));",
                "zone_2_business_vault": "CREATE TABLE business_vault.pit_position (hk_position CHAR(64));",
                "zone_3_info_mart": "CREATE VIEW information_mart.vw_pos AS SELECT 1;",
                "all_zones": sql_text,
            }
        },
        "final_artifacts": {
            "final_contract": {
                "artifact_id": "final_contract",
                "name": "final_contract.yaml",
                "file_path": str(fc_path),
            }
        },
        "gate_scorecard": {"overall_verdict": "PASS"},
    }
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    return client, service, run_dir


def test_dv_schema_normalizes_column_triage_ledger_and_aliases(tmp_path: Path) -> None:
    """BUG-1: column_triage_ledger entries are normalized with UI/CSV keys while preserving ContractV5 keys."""
    client, _service, _run_dir = _seed_treasury_run_on_disk(tmp_path)

    resp = client.get("/api/v1/runs/run-treasury-w3/dv-schema")
    assert resp.status_code == 200, resp.text
    ledger = resp.json()["column_triage_ledger"]
    assert len(ledger) == 4

    by_col = {row["column_name"]: row for row in ledger}
    for col_name, row in by_col.items():
        # Original ContractV5 keys preserved
        assert row["bronze_table"] == "ds01_sec_treasury.positions"
        assert row["column_name"] == col_name
        assert row["elm_classification"]
        assert row["target_dv_entity"]
        # Normalized View 1 / View 2 / CSV keys populated
        assert row["wave"] == "W3"
        assert row["source_table"] == "ds01_sec_treasury.positions"
        assert row["source_column"] == col_name
        assert row["triage_role"] == row["elm_classification"]
        assert row["target_entity"] == row["target_dv_entity"]
        assert row["staging_expr"]
        assert row["graph_a_uri"]

    assert by_col["position_id"]["graph_a_uri"] == "urn:ontology:entity:position"
    assert by_col["market_value"]["graph_a_uri"] == "urn:ontology:entity:position"
    assert "mkt_val_usd" in by_col["market_value"]["staging_expr"]
    assert "CAST(NULL" in by_col["legacy_desk_memo"]["staging_expr"]


def test_dv_schema_query_allows_pit_d10b_and_or_rejects_unsafe(tmp_path: Path) -> None:
    """BUG-2: Safe SQL Runner allows SELECT/JOIN queries using AND/OR while rejecting unsafe SQL."""
    client, _ws, _state_dir, run_id = _seed_completed_run(tmp_path)

    pit_like_sql = (
        "SELECT h.hk_customer, s.legal_name "
        "FROM raw_vault.hub_customer h "
        "JOIN raw_vault.sat_customer_core s "
        "ON h.hk_customer = s.hk_customer AND s.rec_src IS NOT NULL "
        "WHERE (s.legal_name IS NOT NULL OR h.customer_tax_id IS NOT NULL) "
        "LIMIT 10"
    )
    ok_resp = client.post(
        f"/api/v1/runs/{run_id}/dv-schema/query",
        json={"sql": pit_like_sql, "max_rows": 50},
    )
    assert ok_resp.status_code == 200, ok_resp.text

    for bad_sql in (
        "DROP TABLE raw_vault.hub_customer",
        "INSERT INTO raw_vault.hub_customer VALUES ('x', 'y', 'z', CURRENT_TIMESTAMP, 'bad')",
        "SELECT * FROM read_csv('test.csv')",
        "SELECT * FROM sqlite_scan('app.db', 'positions')",
        "SELECT * FROM pragma_table_info('raw_vault.hub_customer')",
        "PRAGMA show_tables",
    ):
        bad_resp = client.post(
            f"/api/v1/runs/{run_id}/dv-schema/query",
            json={"sql": bad_sql, "max_rows": 50},
        )
        assert bad_resp.status_code in (400, 422), (
            f"Unsafe SQL allowed: {bad_sql} -> {bad_resp.status_code}"
        )


def test_run_artifacts_returns_real_contract_ir_and_sql_not_dummy_stubs(tmp_path: Path) -> None:
    """BUG-3: `/api/v1/runs/{run_id}/artifacts` serves real contract, IR, and SQL instead of dummy stubs."""
    client, _service, _run_dir = _seed_treasury_run_on_disk(tmp_path)

    resp = client.get("/api/v1/runs/run-treasury-w3/artifacts")
    assert resp.status_code == 200, resp.text
    arts = {a["artifact_id"]: a for a in resp.json()}

    fc_content = arts["final_contract"]["content"]
    ir_content = arts["final_ir"]["content"]
    sql_content = arts["final_sql"]["content"]

    assert "treasury_vault" in fc_content
    assert "hub_position" in fc_content
    assert "global_commerce_vault" not in fc_content

    assert "treasury_vault" in ir_content
    assert "hub_position" in ir_content
    assert "hub_party" not in ir_content

    assert "hub_position" in sql_content
    assert "hub_customer" not in sql_content


def test_dv_schema_pit_tables_resolve_parent_entity_and_wave_diff_set_stability(
    tmp_path: Path,
) -> None:
    """BUG-4: `pit_tables` resolves `parent_entity` and `wave_diff` ignores payload reordering."""
    w2_dir = tmp_path / "w2"
    w3_dir = tmp_path / "w3"
    _, service, _ = _seed_treasury_run_on_disk(
        w3_dir,
        run_id="run-w3",
        sat_payload_order=["market_value", "legacy_desk_memo"],
        esat_payload_order=["crosswalk_id", "effective_date", "match_confidence"],
        mas_payload_order=["confidence_score", "extracted_value", "extraction_field"],
    )
    # Seed W2 in the same service state with reordered esat/mas columns
    w2_run_dir = w2_dir / "run_w2"
    w2_run_dir.mkdir(parents=True, exist_ok=True)
    w2_ir = json.loads(
        (service.state.state_dir / "pipeline_runs" / "dir_run-w3" / "final_canonical_ir.json").read_text(
            encoding="utf-8"
        )
    )
    for sat in w2_ir["satellites"]:
        if sat["name"] == "esat_sal_account":
            sat["payload_columns"] = [
                {"name": c, "data_type": "TEXT"}
                for c in ["crosswalk_id", "match_confidence", "effective_date"]
            ]
        elif sat["name"] == "mas_document_extractions":
            sat["payload_columns"] = [
                {"name": c, "data_type": "TEXT"}
                for c in ["extraction_field", "extracted_value", "confidence_score"]
            ]
    w2_ir_path = w2_run_dir / "final_canonical_ir.json"
    w2_ir_path.write_text(json.dumps(w2_ir, indent=2), encoding="utf-8")
    w2_fc_path = w2_run_dir / "final_contract.yaml"
    w2_fc_path.write_text(
        (service.state.state_dir / "pipeline_runs" / "dir_run-w3" / "final_contract.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    created_w2 = service.state.create_run(
        CreateRunRequest(project_id="proj-treasury", harness_provider="antigravity")
    )
    service.state.runs.pop(created_w2.run_id, None)
    service.state.runs["run-w2"] = created_w2.model_copy(
        update={"run_id": "run-w2", "status": RunStatus.COMPLETED}
    )
    service.state.pipeline_reports["run-w2"] = {
        "run_id": "run-w2",
        "project_id": "proj-treasury",
        "status": "COMPLETED",
        "wave_id": "W2",
        "final_artifacts": {
            "final_canonical_ir": {"file_path": str(w2_ir_path)},
            "final_contract": {"file_path": str(w2_fc_path)},
        },
        "gate_scorecard": {"overall_verdict": "PASS"},
    }

    proj = get_dv_schema_projection(service.state, "run-w3", compare_run_id="run-w2")
    pit_by_name = {p.name: p for p in proj.pit_tables}
    assert pit_by_name["pit_position"].target_hub == "hub_position"
    assert proj.wave_diff is not None
    assert proj.wave_diff.hash_diff_mutations_count == 0
    assert proj.wave_diff.dropped_columns_count == 0

    # Now mutate a STANDARD satellite by adding an in-place payload column and verify detection
    w3_mut_dir = tmp_path / "w3_mut"
    _, mut_svc, _ = _seed_treasury_run_on_disk(
        w3_mut_dir,
        run_id="run-w3-mut",
        extra_standard_col="unexpected_in_place_col",
    )
    mut_svc.state.runs["run-w2"] = service.state.runs["run-w2"]
    mut_svc.state.pipeline_reports["run-w2"] = service.state.pipeline_reports["run-w2"]
    mut_proj = get_dv_schema_projection(mut_svc.state, "run-w3-mut", compare_run_id="run-w2")
    assert mut_proj.wave_diff is not None
    assert mut_proj.wave_diff.hash_diff_mutations_count == 1


def _seed_pii_sandbox_run(tmp_path: Path) -> tuple[TestClient, str]:
    """Seed a completed run with a persisted DuckDB sandbox containing `sat_beneficial_owner_pii`."""
    import duckdb

    client, _ws, state_dir, run_id = _seed_completed_run(tmp_path)
    sandbox_file = state_dir / "sandboxes" / f"{run_id}.duckdb"
    conn = duckdb.connect(str(sandbox_file))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_beneficial_owner_pii ("
            "hk_customer VARCHAR, beneficial_owner_tax_id VARCHAR, load_dts VARCHAR)"
        )
        conn.execute(
            "INSERT INTO raw_vault.sat_beneficial_owner_pii VALUES "
            "('hk-001', '999-11-2222', '2026-11-20T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()
    return client, run_id


def test_dv_schema_explorer_and_config_validate_reject_csrf_and_dns_rebinding(
    tmp_path: Path,
) -> None:
    """Task 1 TDD: /dv-schema*, /explorer, and /platform/config/validate reject Host/Origin/Fetch spoofs with 403."""
    client, run_id = _seed_pii_sandbox_run(tmp_path)

    endpoints = [
        ("GET", f"/api/v1/runs/{run_id}/dv-schema", None),
        ("GET", f"/api/v1/runs/{run_id}/dv-schema/tables/raw_vault.sat_beneficial_owner_pii/rows?limit=2", None),
        ("POST", f"/api/v1/runs/{run_id}/dv-schema/query", {"sql": "SELECT 1"}),
        ("GET", f"/api/v1/runs/{run_id}/explorer", None),
        ("POST", "/api/v1/platform/config/validate", {"harness": {"provider": "claude-cli"}}),
    ]
    attack_vectors = [
        ("dns-rebinding-host", {"Host": "evil.attacker.com:8000"}, "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"),
        ("cross-origin", {"Origin": "https://evil.attacker.com"}, "CSRF_ORIGIN_MISMATCH"),
        ("cross-site-fetch", {"Sec-Fetch-Site": "cross-site"}, "CSRF_ORIGIN_MISMATCH"),
    ]
    for method, url, json_body in endpoints:
        for label, headers, expected_code in attack_vectors:
            if method == "GET":
                resp = client.get(url, headers=headers)
            else:
                resp = client.post(url, json=json_body, headers=headers)
            assert resp.status_code == 403, (
                f"{method} {url} ({label}) expected 403, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("error_code") == expected_code, (
                f"{method} {url} ({label}) unexpected error_code: {resp.json()}"
            )


def test_dv_schema_pii_satellite_rbac_blocks_public_role(tmp_path: Path) -> None:
    """Task 1 TDD: X-OpenDV-Role: PUBLIC cannot read sat_*_pii rows or query PII satellites."""
    client, run_id = _seed_pii_sandbox_run(tmp_path)
    pub_headers = {"X-OpenDV-Role": "PUBLIC"}

    # 1. Direct table rows preview on sat_*_pii -> 403
    rows_pub = client.get(
        f"/api/v1/runs/{run_id}/dv-schema/tables/raw_vault.sat_beneficial_owner_pii/rows?limit=2",
        headers=pub_headers,
    )
    assert rows_pub.status_code == 403, rows_pub.text
    assert "999-11-2222" not in rows_pub.text

    # 2. Custom SQL querying sat_*_pii -> 403
    sql_pub = client.post(
        f"/api/v1/runs/{run_id}/dv-schema/query",
        json={"sql": "SELECT * FROM raw_vault.sat_beneficial_owner_pii LIMIT 2"},
        headers=pub_headers,
    )
    assert sql_pub.status_code == 403, sql_pub.text
    assert "999-11-2222" not in sql_pub.text

    # 3. Non-PII table with tax_id key column under PUBLIC -> 200 with PII column redacted
    hub_pub = client.get(
        f"/api/v1/runs/{run_id}/dv-schema/tables/raw_vault.hub_customer/rows?limit=5",
        headers=pub_headers,
    )
    assert hub_pub.status_code == 200, hub_pub.text
    for row in hub_pub.json()["rows"]:
        if "customer_tax_id" in row and row["customer_tax_id"] is not None:
            assert row["customer_tax_id"] == "[REDACTED:RESTRICTED_PII]"

    # 4. Privileged role (PRIVILEGED_CUSTODIAN) on valid loopback succeeds and sees PII rows
    priv_headers = {"X-OpenDV-Role": "PRIVILEGED_CUSTODIAN"}
    rows_priv = client.get(
        f"/api/v1/runs/{run_id}/dv-schema/tables/raw_vault.sat_beneficial_owner_pii/rows?limit=2",
        headers=priv_headers,
    )
    assert rows_priv.status_code == 200, rows_priv.text
    assert rows_priv.json()["rows"][0]["beneficial_owner_tax_id"] == "999-11-2222"

    sql_priv = client.post(
        f"/api/v1/runs/{run_id}/dv-schema/query",
        json={"sql": "SELECT * FROM raw_vault.sat_beneficial_owner_pii LIMIT 2"},
        headers=priv_headers,
    )
    assert sql_priv.status_code == 200, sql_priv.text
    assert sql_priv.json()["rows"][0]["beneficial_owner_tax_id"] == "999-11-2222"


