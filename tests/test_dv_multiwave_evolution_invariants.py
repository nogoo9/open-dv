"""Phase 2 TDD Tests: Multi-Wave Schema Drift (`EVO-01..05`) & Auto-Bound `deployed_contract` (`FR-DVM-11,17`, `D12`).

Covers:
1. `FR-DVM-11` (`Algorithms A.7 & A.9a`, `EVO-01..05`):
   - `EVO-01`: Additive columns routed to `sat_*_ext_v2` without mutating frozen Hub PKs or original Satellite `hash_diff` columns.
   - `EVO-02`: Dropped columns projected as `CAST(NULL AS <type>)` in Zone 1 with 0 `DROP COLUMN` in Zone 2.
   - `EVO-03`: Renamed columns mapped via Zone 1 alias (`<new_col> AS <old_col>`).
   - `EVO-04/05`: Lossless type widening allowed (`TYPE_WIDENED`); narrowing flagged `ASK-USER` (`KEY_DRIFT`).
2. `FR-DVM-17` (`D12`): Auto-bound `deployed_contract` on subsequent runs for a project with a prior certified DV 2.0 run.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness.pipeline.contract_evolver import evolve_contract_single_run
from opendv_agent_harness.pipeline.spec import Stage01FeedDiscoveryItem
from opendv_axi_verifier import (
    detect_intra_source_schema_drift,
    verify_additive_evolution_invariants,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.schema_registry import validate_contract_v5
from opendv_duckdb_sandbox.compiler_4zone import compile_canonical_ir_to_4zone_sql
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_service import OpenDVService, OpenDVSettings, PipelineRunRequest
from opendv_testkit import deterministic_pipeline_seam

REPO_ROOT = Path(__file__).resolve().parents[1]
UF2_DIR = REPO_ROOT / "examples" / "scenarios" / "uf2_renamed_column"
GC_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_evo_01_to_05_non_destructive_intra_source_drift_and_zone1_projections() -> None:
    """`EVO-01..05` preserves deployed Hub/Sat hash_diffs, emits `CAST(NULL AS ...)` in Zone 1, and 0 `DROP COLUMN` in Zone 2."""
    ont = GraphAOntology.model_validate(
        yaml.safe_load((UF2_DIR / "graph_a_ontology.yaml").read_text(encoding="utf-8"))
    )
    w0_contract = dict(yaml.safe_load((UF2_DIR / "contract_w0.yaml").read_text(encoding="utf-8")))

    # Detect EVO-04 widening + EVO-05 narrowing
    drift_types = detect_intra_source_schema_drift(
        deployed_columns={"customer_tax_id", "customer_legal_name", "credit_limit_usd"},
        candidate_columns={"customer_tax_id", "customer_legal_name", "credit_limit_usd"},
        source_table="bronze_crm.customers",
        target_entity="sat_customer_core",
        key_columns={"customer_tax_id"},
        old_types={"customer_legal_name": "VARCHAR(100)", "credit_limit_usd": "DECIMAL(18,2)"},
        new_types={"customer_legal_name": "VARCHAR(255)", "credit_limit_usd": "DECIMAL(10,2)"},
    )
    cats = {d.column_name: (d.category, d.action) for d in drift_types}
    assert cats["customer_legal_name"] == ("TYPE_WIDENED", "AUTO-FIX")
    assert cats["credit_limit_usd"] == ("KEY_DRIFT", "ASK-USER")

    # Evolve contract across EVO-01 (added), EVO-02 (dropped -> CAST(NULL AS ...)), EVO-03 (renamed)
    feed = Stage01FeedDiscoveryItem(
        source_system="bronze_crm",
        table_name="bronze_crm.customers",
        record_source_code="CRM_CORE",
        primary_keys=["customer_tax_id"],
        business_key_columns=["customer_tax_id"],
        column_descriptors=[
            {"name": "customer_tax_id", "physical_type": "VARCHAR(64)", "is_primary_key": True},
            {"name": "customer_legal_name", "physical_type": "VARCHAR(255)"},
            {"name": "country_iso_2", "physical_type": "VARCHAR(8)"},  # renamed from country_iso2 (sim >= 0.85)
            # account_segment dropped (EVO-02)
            {"name": "credit_limit_usd", "physical_type": "DECIMAL(18,2)"},
            {"name": "loyalty_tier", "physical_type": "VARCHAR(32)"},  # added (EVO-01)
        ],
    )
    evolved, findings = evolve_contract_single_run(
        deployed_contract=w0_contract,
        ontology=ont,
        discovered_feeds=[feed],
        schema_epoch_tag="v2",
        steward_hold_policy="auto_approve_top_candidate",
    )

    assert verify_additive_evolution_invariants(w0_contract, evolved) == []
    finding_cats = {f["category"] for f in findings}
    assert {"ADDITIVE", "RENAMED", "SOFT_DEPRECATED"}.issubset(finding_cats)

    sats_by_name = {s["name"]: s for s in evolved["satellites"]}
    assert set(sats_by_name["sat_customer_core"]["payload_columns"]) == set(
        w0_contract["satellites"][0]["payload_columns"]
    )
    assert any(name.endswith("_ext_v2") and "loyalty_tier" in s["payload_columns"] for name, s in sats_by_name.items())

    ir_model = lower_contract_to_canonical_ir(validate_contract_v5(evolved))
    bundle = compile_canonical_ir_to_4zone_sql(ir_model)
    assert "DROP COLUMN" not in bundle.zone_2_sql.upper()
    assert "CAST(NULL AS" in bundle.zone_1_sql.upper()
    assert "country_iso_2 AS country_iso2" in bundle.zone_1_sql


def test_d12_auto_bounds_deployed_contract_across_project_runs(tmp_path: Path) -> None:
    """Subsequent run on a project with a prior certified contract auto-binds `deployed_contract` (`D12`)."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=tmp_path / "state", is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")

    w0_spec = yaml.safe_load((GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    w0_spec["project_id"] = "proj-d12-autobind"
    w0_spec["graph_a_ontology_path"] = str((GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml").resolve())
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

    with deterministic_pipeline_seam():
        r1 = service.execute_pipeline_run_sync(PipelineRunRequest(input_spec=w0_spec))
    assert r1["status"] == "COMPLETED"
    assert service.state.projects["proj-d12-autobind"].contract_path is not None

    # Wave 2 request omits initial_contract / initial_contract_path — service must auto-bind Wave 1 contract!
    w1_spec = copy.deepcopy(w0_spec)
    w1_spec["pipeline_id"] = "proj_d12_wave2"
    w1_spec.pop("initial_contract", None)
    w1_spec.pop("initial_contract_path", None)

    captured_specs: list[Any] = []

    def _spy_runner(spec_obj: Any, setup_obj: Any, **kwargs: Any) -> Any:
        captured_specs.append(spec_obj)
        from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
        from opendv_testkit import build_deterministic_agy_sdk_shim

        kwargs.setdefault("sdk_module", build_deterministic_agy_sdk_shim())
        return run_agentic_vault_pipeline(spec_obj, setup_obj, **kwargs)

    with deterministic_pipeline_seam():
        r2 = service.state.execute_agentic_pipeline_run(
            PipelineRunRequest(input_spec=w1_spec),
            pipeline_runner=_spy_runner,
        )
    assert r2["status"] == "COMPLETED"
    assert len(captured_specs) == 1
    bound_spec = captured_specs[0]
    assert bool(
        bound_spec.initial_contract
        or bound_spec.initial_contract_path
        or bound_spec.initial_contract_content
    ), "D12 violated: second run did not auto-bind deployed_contract from latest certified run"


def test_evo_03_wave3_column_aliases_pit_databricks_and_4batch_sandbox() -> None:
    """Wave 3 evolution (`market_value` -> `mkt_val_usd`, +2 additive, -1 dropped) lowers aliases, PIT, and passes 4-batch DuckDB execution."""
    from opendv_axi_verifier.codemods.alg_a9a_schema_drift import codemod_intra_source_schema_drift
    from opendv_duckdb_sandbox.dialects.databricks_transpiler import transpile_bundle_to_databricks
    from opendv_duckdb_sandbox.sandbox_engine import DuckDbShadowSandbox

    w0_contract: dict[str, Any] = {
        "contract_version": "5.0",
        "domain_namespace": "custody_positions",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "positions",
                "column_name": "position_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_position",
                "source_sql_type": "VARCHAR(64)",
            },
            {
                "bronze_table": "positions",
                "column_name": "market_value",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "NUMERIC(14,2)",
            },
            {
                "bronze_table": "positions",
                "column_name": "quantity",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "NUMERIC(18,4)",
            },
            {
                "bronze_table": "positions",
                "column_name": "legacy_desk_memo",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_positions",
                "source_sql_type": "VARCHAR(255)",
            },
        ],
        "hubs": [
            {
                "name": "hub_position",
                "ontology_concept_uri": "urn:ontology:entity:position",
                "business_key_sources": [
                    {
                        "bronze_table": "positions",
                        "key_columns": ["position_id"],
                        "record_source_code": "POS_CORE",
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
                "source_bronze_table": "positions",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["market_value", "quantity", "legacy_desk_memo"],
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }

    cand_contract = copy.deepcopy(w0_contract)
    cand_contract["satellites"][0]["payload_columns"] = [
        "mkt_val_usd",
        "quantity",
        "asset_class",
        "desk_code",
    ]
    cand_contract["satellites"][0]["column_types"] = {
        "mkt_val_usd": "DECIMAL(18,2)",
        "quantity": "NUMERIC(18,4)",
        "asset_class": "VARCHAR(64)",
        "desk_code": "VARCHAR(32)",
    }
    cand_contract["satellites"][0]["value_jaccard"] = {("market_value", "mkt_val_usd"): 0.95}

    evolved = codemod_intra_source_schema_drift(
        deployed_contract=w0_contract,
        candidate_contract=cand_contract,
        schema_epoch_tag="v2",
    )

    assert verify_additive_evolution_invariants(w0_contract, evolved) == []
    base_sat = next(s for s in evolved["satellites"] if s["name"] == "sat_positions")
    assert base_sat["column_aliases"] == {"market_value": "mkt_val_usd"}
    assert base_sat["soft_deprecated_columns"] == ["legacy_desk_memo"]
    assert set(base_sat["payload_columns"]) == {"market_value", "quantity", "legacy_desk_memo"}

    comp_sat = next(s for s in evolved["satellites"] if s["name"] == "sat_positions_ext_v2")
    assert set(comp_sat["payload_columns"]) == {"asset_class", "desk_code"}

    validated = validate_contract_v5(evolved)
    assert validated.satellites[0].column_aliases == {"market_value": "mkt_val_usd"}

    ir_model = lower_contract_to_canonical_ir(validated)
    ir_base_sat = next(s for s in ir_model.satellites if s.name == "sat_positions")
    assert ir_base_sat.source_mapping.column_aliases == {"market_value": "mkt_val_usd"}
    assert ir_base_sat.source_mapping.soft_deprecated_columns == ["legacy_desk_memo"]
    assert "market_value" in ir_base_sat.hash_diff_column.hash_expression.input_columns
    assert any(p.name == "pit_position" for p in (ir_model.pit_tables or []))

    bronze_rows = {
        "positions": [
            {
                "position_id": "POS-001",
                "mkt_val_usd": 125000.50,
                "quantity": 100.0,
                "asset_class": "EQUITY",
                "desk_code": "DESK_NY",
            },
            {
                "position_id": "POS-002",
                "mkt_val_usd": 250000.75,
                "quantity": 200.0,
                "asset_class": "FIXED_INCOME",
                "desk_code": "DESK_LDN",
            },
        ]
    }
    bundle = compile_canonical_ir_to_4zone_sql(ir_model, bronze_tables=bronze_rows)
    assert "mkt_val_usd AS market_value" in bundle.zone_1_sql
    assert "CAST(NULL AS VARCHAR) AS legacy_desk_memo" in bundle.zone_1_sql
    assert "DROP COLUMN" not in bundle.zone_2_sql.upper()

    db_bundle = transpile_bundle_to_databricks(bundle)
    assert "mkt_val_usd AS market_value" in db_bundle.zone_1_sql
    assert "AS legacy_desk_memo" in db_bundle.zone_1_sql

    with DuckDbShadowSandbox() as sandbox:
        report = sandbox.run_full_verification(
            ir_model,
            bronze_tables=bronze_rows,
            emit_telemetry=False,
        )
        assert report.passed is True
        assert report.batch_report.all_passed is True
        assert report.dynamic_check_report.all_passed is True
        pit_cnt = sandbox.conn.execute("SELECT COUNT(*) FROM business_vault.pit_position").fetchone()
        assert pit_cnt is not None and pit_cnt[0] > 0
