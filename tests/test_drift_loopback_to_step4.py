"""Phase 2 TDD Tests: Stage 02 Unmodeled Drift (`UF-1..UF-5`) Loopback (`FR-DVM-16`).

Covers:
1. Stage 02 detects unmodeled feeds and column drift (`UF-1..UF-5` taxonomy codes)
   and surfaces structured drift findings in `AgenticPipelineReport` for Step 4 sync (`D10`).
2. Each structured drift finding includes `code` in `{"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}`,
   `source_table`, `column_name`, `drift_type`, and `suggested_op`.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.pipeline.discovery_tabular import discover_tabular_feeds
from opendv_agent_harness.pipeline.mode_detector import infer_execution_plan
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_agent_harness.pipeline.spec import PipelineInputSpec, PipelineSourceFeedInput
from opendv_core.models.graph_a import GraphAOntology
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_service import OpenDVService, PipelineRunRequest, StateStore
from opendv_testkit import build_deterministic_agy_sdk_shim
import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
S3_DIR = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
UF2_DIR = REPO_ROOT / "examples" / "scenarios" / "uf2_renamed_column"


def test_stage02_surfaces_uf1_to_uf5_structured_drift_findings_in_report(tmp_path: Path) -> None:
    """Stage 02 surfaces structured `UF-1..UF-5` drift findings in `AgenticPipelineReport`."""
    spec = PipelineInputSpec.model_validate(
        {
            "pipeline_id": "uf_drift_loopback_test",
            "project_id": "proj_uf_drift",
            "domain_namespace": "enterprise_crm",
            "graph_a_ontology_path": str(UF2_DIR / "graph_a_ontology.yaml"),
            "initial_contract_path": str(UF2_DIR / "contract_w0.yaml"),
            "sources": [
                {
                    "feed_id": "crm_customers_w1",
                    "source_system": "bronze_crm",
                    "table_name": "customers",
                    "data_path": str(UF2_DIR / "sources" / "wave1_renamed" / "crm_customers.csv"),
                    "record_source_code": "CRM_CORE",
                    "business_key_columns": ["customer_tax_id"],
                },
                {
                    "feed_id": "unmapped_supplier",
                    "source_system": "bronze_logistics",
                    "table_name": "feed_unmapped_supplier",
                    "data_path": str(S3_DIR / "sources" / "feed_unmapped_supplier.csv"),
                    "record_source_code": "LOGISTICS_FEED",
                    "business_key_columns": ["delivery_id"],
                },
            ],
        }
    )
    setup = HarnessSetupConfig(
        provider="agy",
        stage_output_policy="symbolic_completion",
        steward_hold_policy="auto_approve_top_candidate",
    )
    report = run_agentic_vault_pipeline(
        spec,
        setup,
        output_dir=tmp_path / "out_uf_drift",
        base_dir=REPO_ROOT,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    findings = list(getattr(report, "drift_findings", None) or [])
    if not findings:
        findings = [
            f
            for w in report.wave_reports
            for f in (getattr(w, "schema_drift_events", None) or [])
        ]
    assert findings, "Expected Stage 02 structured UF-1..UF-5 drift findings in AgenticPipelineReport"

    uf_codes = {str(f.get("code", "")) for f in findings if isinstance(f, dict)}
    assert uf_codes & {"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}, (
        f"Expected structured UF-1..UF-5 code in drift findings, got: {findings}"
    )
    for item in findings:
        if item.get("code") in {"UF-1", "UF-2", "UF-3", "UF-4", "UF-5"}:
            assert item.get("source_table")
            assert item.get("drift_type")
            assert item.get("suggested_op")


def _build_positions_wave_fixtures(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Create ds01 baseline CSV, ds03 wave-3 SQLite DB, ontology YAML, and W0 contract."""
    ds01_csv = tmp_path / "ds01_positions.csv"
    ds01_csv.write_text(
        "position_id,security_id,quantity,market_value,legacy_desk_memo,customer_email\n"
        "POS-101,SEC-AAPL,100,125000.00,desk_a,alice@example.com\n"
        "POS-102,SEC-MSFT,200,98000.50,desk_b,bob@example.com\n"
        "POS-103,SEC-NVDA,50,410000.00,desk_c,carol@example.com\n",
        encoding="utf-8",
    )

    ds03_sqlite = tmp_path / "ds03_w3_positions.sqlite"
    with sqlite3.connect(ds03_sqlite) as sconn:
        sconn.execute(
            "CREATE TABLE positions ("
            "position_id TEXT PRIMARY KEY, security_id TEXT, quantity INTEGER, "
            "mkt_val_usd REAL, esg_score REAL, risk_bucket TEXT, customer_email TEXT)"
        )
        sconn.executemany(
            "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("POS-101", "SEC-AAPL", 100, 125000.00, 82.5, "LOW", "alice@example.com"),
                ("POS-102", "SEC-MSFT", 200, 98000.50, 77.0, "MEDIUM", "bob@example.com"),
                ("POS-103", "SEC-NVDA", 50, 410000.00, 91.0, "LOW", "carol@example.com"),
            ],
        )

    ont_path = tmp_path / "positions_ontology.yaml"
    ont_path.write_text(
        "ontology_version: '1.0.0'\n"
        "domain_namespace: 'wealth_mgmt'\n"
        "canonical_business_concepts:\n"
        "  - concept_uri: 'urn:ontology:entity:position'\n"
        "    concept_name: 'Position'\n"
        "    target_hub: 'hub_position'\n"
        "    definition: 'Portfolio security position.'\n"
        "canonical_relationships: []\n",
        encoding="utf-8",
    )

    w0_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "wealth_mgmt",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "position_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_position",
                "source_sql_type": "VARCHAR(64)",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "security_id",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "VARCHAR(64)",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "quantity",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "INTEGER",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "market_value",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "DOUBLE",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "legacy_desk_memo",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "VARCHAR(128)",
            },
            {
                "bronze_table": "ds01_sec_treasury.positions",
                "column_name": "customer_email",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions_pii",
                "source_sql_type": "VARCHAR(255)",
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
                        "record_source_code": "DS01_TREASURY",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
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
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": [
                    "security_id",
                    "quantity",
                    "market_value",
                    "legacy_desk_memo",
                ],
                "soft_deprecated_columns": [],
            },
            {
                "name": "sat_positions_pii",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_position",
                "source_bronze_table": "ds01_sec_treasury.positions",
                "security_classification": "CONFIDENTIAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["customer_email"],
                "soft_deprecated_columns": [],
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }
    return ds01_csv, ds03_sqlite, ont_path, w0_contract


def test_cross_source_wave_evolution_value_jaccard_privacy_and_pit(tmp_path: Path) -> None:
    """Cross-source wave evolution (ds01 + ds03) triggers INTRA_SOURCE_DRIFT, value_jaccard rename, sat_*_ext_v2, pit_position, and drift_findings."""
    ds01_csv, ds03_sqlite, ont_path, w0_contract = _build_positions_wave_fixtures(tmp_path)

    ds01_feed = PipelineSourceFeedInput(
        feed_id="ds01_positions",
        source_system="ds01_sec_treasury",
        table_name="positions",
        data_path=str(ds01_csv),
        record_source_code="DS01_TREASURY",
        business_key_columns=["position_id"],
        pii_columns=["customer_email"],
    )
    ds03_feed = PipelineSourceFeedInput(
        feed_id="ds03_positions",
        source_system="ds03_w3_positions_sqlite",
        table_name="positions",
        adapter="sqlite",
        data_path=str(ds03_sqlite),
        record_source_code="DS03_W3_POSITIONS",
        business_key_columns=["position_id"],
        pii_columns=["customer_email"],
    )
    disjoint_feed = PipelineSourceFeedInput(
        feed_id="ds99_alerts",
        source_system="ds99_unrelated",
        table_name="positions",
        columns=["alert_id", "severity", "notes"],
        business_key_columns=["alert_id"],
    )

    # 1. Mode detector distinguishes cross-source wave evolution vs disjoint collision
    mnpi_feed = PipelineSourceFeedInput(
        feed_id="mnpi_restricted",
        source_system="mnpi_src",
        table_name="positions_mnpi",
        data_path=str(ds01_csv),
        business_key_columns=["position_id"],
        options={"do_not_send_to_agent": True},
    )
    conn = duckdb.connect(":memory:")
    try:
        s1_out = discover_tabular_feeds([ds01_feed, ds03_feed, mnpi_feed], conn, base_dir=tmp_path)
    finally:
        conn.close()

    # Privacy boundary: PII column & do_not_send_to_agent source must NOT have value_signatures
    ds01_prof = s1_out.profile_summaries["ds01_sec_treasury.positions"]["column_stats"]
    ds03_prof = s1_out.profile_summaries["ds03_w3_positions.positions"]["column_stats"]
    mnpi_prof = s1_out.profile_summaries["mnpi_src.positions_mnpi"]["column_stats"]
    assert ds01_prof["market_value"].get("value_signatures"), "Expected non-PII value_signatures for market_value"
    assert ds03_prof["mkt_val_usd"].get("value_signatures"), "Expected non-PII value_signatures for mkt_val_usd"
    assert not ds01_prof["customer_email"].get("value_signatures"), "PII column must be excluded from value_signatures"
    assert not mnpi_prof["market_value"].get("value_signatures"), "do_not_send_to_agent source must be excluded"
    assert "alice@example.com" not in str(s1_out.profile_summaries)
    assert "125000" not in str(ds01_prof["market_value"].get("value_signatures"))

    # Feed inputs enriched from discovery descriptors
    ds01_cols = [cd["name"] for cd in s1_out.discovered_feeds[0].column_descriptors]
    ds03_cols = [cd["name"] for cd in s1_out.discovered_feeds[1].column_descriptors]
    plan = infer_execution_plan(
        contract=w0_contract,
        sources=[
            ds01_feed.model_copy(update={"columns": ds01_cols}),
            ds03_feed.model_copy(update={"columns": ds03_cols}),
            disjoint_feed,
        ],
    )
    by_ss = {a.source_system: a for a in plan.table_actions}
    assert by_ss["ds01_sec_treasury"].action == "NO_OP"
    assert by_ss[s1_out.discovered_feeds[1].source_system].action == "INTRA_SOURCE_DRIFT"
    assert by_ss[s1_out.discovered_feeds[1].source_system].matched_existing_entity == "sat_positions"
    assert by_ss["ds99_unrelated"].action == "INCREMENTAL_ONBOARDING"

    # 2. Full pipeline run surfaces sat_positions_ext_v2, soft_deprecated_columns, pit_position, and drift_findings
    spec = PipelineInputSpec.model_validate(
        {
            "pipeline_id": "uat14_cross_source_drift",
            "project_id": "proj_uat14",
            "domain_namespace": "wealth_mgmt",
            "graph_a_ontology_path": str(ont_path),
            "initial_contract": w0_contract,
            "sources": [
                {
                    "feed_id": "ds01_positions",
                    "source_system": "ds01_sec_treasury",
                    "table_name": "positions",
                    "data_path": str(ds01_csv),
                    "record_source_code": "DS01_TREASURY",
                    "business_key_columns": ["position_id"],
                    "pii_columns": ["customer_email"],
                },
                {
                    "feed_id": "ds03_positions",
                    "source_system": "ds03_w3_positions_sqlite",
                    "table_name": "positions",
                    "adapter": "sqlite",
                    "data_path": str(ds03_sqlite),
                    "record_source_code": "DS03_W3_POSITIONS",
                    "business_key_columns": ["position_id"],
                    "pii_columns": ["customer_email"],
                },
            ],
        }
    )
    setup = HarnessSetupConfig(
        provider="agy",
        stage_output_policy="symbolic_completion",
        steward_hold_policy="auto_approve_top_candidate",
    )
    report = run_agentic_vault_pipeline(
        spec,
        setup,
        output_dir=tmp_path / "out_uat14",
        base_dir=tmp_path,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "COMPLETED"
    assert report.blueprint is not None and report.blueprint.contract is not None
    evolved_dict = report.blueprint.contract.model_dump(mode="json")
    sats_by_name = {s["name"]: s for s in evolved_dict.get("satellites", [])}

    assert "sat_positions_ext_v2" in sats_by_name, f"Missing sat_positions_ext_v2 in {list(sats_by_name)}"
    assert {"esg_score", "risk_bucket"}.issubset(set(sats_by_name["sat_positions_ext_v2"]["payload_columns"]))
    assert "legacy_desk_memo" in sats_by_name["sat_positions"].get("soft_deprecated_columns", [])

    ir_model = lower_contract_to_canonical_ir(evolved_dict)
    pit_names = {p.name for p in ir_model.pit_tables}
    assert "pit_position" in pit_names, f"Expected pit_position when 2+ sats attach to hub_position, got {pit_names}"

    assert report.drift_findings, "Expected populated report.drift_findings on COMPLETED drift run"
    by_col = {str(f.get("column_name")): f for f in report.drift_findings if isinstance(f, dict)}
    assert by_col.get("esg_score", {}).get("code") == "UF-1"
    assert by_col.get("risk_bucket", {}).get("code") == "UF-1"
    assert by_col.get("legacy_desk_memo", {}).get("code") == "UF-3"
    assert by_col.get("market_value", {}).get("code") == "UF-2"
    assert by_col.get("market_value", {}).get("new_column_name") == "mkt_val_usd"


def test_run_execution_engine_sse_run_complete_includes_drift_columns_and_findings(
    tmp_path: Path,
) -> None:
    """RunExecutionEngine SSE run_complete payload includes drift_columns and drift_findings."""
    store = StateStore(state_dir=tmp_path / "state")
    service = OpenDVService(state_store=store, workspace_root=tmp_path)

    ont_path = tmp_path / "ont.yaml"
    ont_path.write_text(
        "ontology_version: '1.0.0'\n"
        "domain_namespace: 'wealth_mgmt'\n"
        "canonical_business_concepts: []\n"
        "canonical_relationships: []\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "src.csv"
    csv_path.write_text("id,val\n1,a\n", encoding="utf-8")

    drift_fixture = [
        {
            "code": "UF-1",
            "category": "ADDITIVE",
            "drift_type": "ADDITIVE_COLUMN",
            "suggested_op": "SPLIT_SATELLITE",
            "source_table": "ds03.positions",
            "column_name": "esg_score",
        },
        {
            "code": "UF-2",
            "category": "RENAMED",
            "drift_type": "COLUMN_RENAME",
            "suggested_op": "ALIAS_IN_ZONE1",
            "source_table": "ds03.positions",
            "column_name": "market_value",
            "new_column_name": "mkt_val_usd",
        },
        {
            "code": "UF-3",
            "category": "SOFT_DEPRECATED",
            "drift_type": "COLUMN_REMOVAL",
            "suggested_op": "SOFT_DEPRECATE_NULL",
            "source_table": "ds03.positions",
            "column_name": "legacy_desk_memo",
        },
    ]

    def _drift_runner(spec_obj: Any, *args: Any, **kwargs: Any) -> Any:
        from opendv_agent_harness.pipeline.spec import (
            AgenticPipelineReport,
            DeterministicValidationReport,
        )

        rid = getattr(spec_obj, "run_id", "run-sse-drift")
        val = DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True)
        return AgenticPipelineReport(
            run_id=rid,
            pipeline_id=spec_obj.pipeline_id,
            project_id=spec_obj.project_id,
            domain_namespace=spec_obj.domain_namespace,
            status="COMPLETED",
            overall_passed=True,
            started_at="2026-11-19T00:00:00Z",
            completed_at="2026-11-19T00:00:01Z",
            input_validation=val,
            output_validation=val,
            drift_findings=drift_fixture,
        )

    req = PipelineRunRequest(
        input_spec={
            "pipeline_id": "sse_drift_test",
            "project_id": "proj_sse",
            "domain_namespace": "wealth_mgmt",
            "graph_a_ontology_path": str(ont_path),
            "sources": [
                {
                    "feed_id": "f1",
                    "source_system": "ds03",
                    "table_name": "positions",
                    "data_path": str(csv_path),
                    "business_key_columns": ["id"],
                }
            ],
        },
        provider_override="agy",
    )
    handle = service.run_engine.submit_pipeline_run(
        store,
        req,
        pipeline_runner=_drift_runner,
    )
    ctx = service.run_engine.get_context(handle.run_id)
    assert ctx is not None
    for _ in range(100):
        if ctx.is_done:
            break
        time.sleep(0.02)
    assert ctx.is_done

    complete_evts = [
        e for e in ctx.event_buffer if isinstance(e, dict) and e.get("event_type") == "run_complete"
    ]
    assert len(complete_evts) == 1
    payload = complete_evts[0]["payload"]
    assert payload["status"] == "COMPLETED"
    assert payload["drift_columns"] == ["esg_score", "market_value", "legacy_desk_memo"]
    assert len(payload["drift_findings"]) == 3

