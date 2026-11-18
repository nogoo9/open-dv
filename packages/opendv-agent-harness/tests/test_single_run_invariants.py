"""Tests for WS3.4: Single-run pipeline invariants, VaultBlueprint vN+1 lineage,
BlueprintEvolutionDiff, InferredExecutionPlan, and 4-zone SQL compilation across dialects.

Conforms to ADR-017, ADR-018, ADR-020, and docs/25-data-driven-single-run-pipeline-design.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from opendv_agent_harness.pipeline import (
    AgenticVaultPipeline,
    HarnessSetupConfig,
    PipelineInputSpec,
    PipelineSourceFeedInput,
    advance_blueprint_lineage,
    compile_and_execute_sandbox,
    compute_blueprint_evolution_diff,
)
from opendv_agent_harness.pipeline.contract_synthesizer import (
    _synthesize_cumulative_contract,
    synthesize_ontology_seed_contract,
)
from opendv_agent_harness.pipeline.discovery_tabular import discover_tabular_feeds
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)
from opendv_core.models.vault_blueprint import (
    compute_blueprint_sha256,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_testkit import build_deterministic_agy_sdk_shim


@pytest.fixture
def banking_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="banking",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                definition="A banking customer",
                target_hub="hub_customer",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                definition="A financial deposit account",
                target_hub="hub_account",
            ),
        ],
    )


@pytest.fixture
def sample_banking_sqlite(tmp_path: Path) -> Path:
    db_path = tmp_path / "banking.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            customer_id VARCHAR(32) PRIMARY KEY,
            full_name VARCHAR(128) NOT NULL,
            email_address VARCHAR(128),
            country_code VARCHAR(3)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE accounts (
            account_id VARCHAR(32) PRIMARY KEY,
            customer_id VARCHAR(32) NOT NULL,
            account_type VARCHAR(16) NOT NULL,
            balance_amount NUMERIC(14,2),
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        """
    )
    cur.execute("INSERT INTO customers VALUES ('C101', 'Alice Banker', 'alice@bank.org', 'USA');")
    cur.execute("INSERT INTO accounts VALUES ('A501', 'C101', 'CHECKING', 12500.50);")
    conn.commit()
    conn.close()
    return db_path


def test_vault_blueprint_lineage_chaining_v_n_plus_1(banking_ontology: GraphAOntology) -> None:
    """Verify ADR-017 cryptographic SHA-256 parent lineage chaining (RFC 8785 canonical hash)."""
    contract_seed = synthesize_ontology_seed_contract(banking_ontology, domain_namespace="banking")
    contract_model = NoMistakeDataVault2ContractV5.model_validate(contract_seed)

    # 1. Revision 1 (Cold Start Greenfield)
    bp_v1 = advance_blueprint_lineage(
        prior_blueprint=None,
        ontology=banking_ontology,
        contract=contract_model,
        domain_namespace="banking",
        lineage_notes="Initial greenfield blueprint",
    )
    assert bp_v1.lineage.blueprint_revision == 1
    assert bp_v1.lineage.parent_blueprint_sha256 is None
    v1_sha = compute_blueprint_sha256(bp_v1)
    assert len(v1_sha) == 64
    assert all(c in "0123456789abcdef" for c in v1_sha)

    # Determinism check (RFC 8785)
    assert compute_blueprint_sha256(bp_v1) == v1_sha

    # 2. Revision 2 (Lineage Chaining)
    bp_v2 = advance_blueprint_lineage(
        prior_blueprint=bp_v1,
        ontology=banking_ontology,
        contract=contract_model,
        domain_namespace="banking",
        lineage_notes="Lineage chain step 1",
    )
    assert bp_v2.lineage.blueprint_revision == 2
    assert bp_v2.lineage.parent_blueprint_sha256 == v1_sha
    v2_sha = compute_blueprint_sha256(bp_v2)
    assert len(v2_sha) == 64
    assert v2_sha != v1_sha

    # 3. Revision 3 (Lineage Chaining Step 2)
    bp_v3 = advance_blueprint_lineage(
        prior_blueprint=bp_v2,
        ontology=banking_ontology,
        contract=contract_model,
        domain_namespace="banking",
        lineage_notes="Lineage chain step 2",
    )
    assert bp_v3.lineage.blueprint_revision == 3
    assert bp_v3.lineage.parent_blueprint_sha256 == v2_sha
    v3_sha = compute_blueprint_sha256(bp_v3)
    assert v3_sha != v2_sha


def test_blueprint_evolution_diff_detection() -> None:
    """Verify structural diff detection between prior and newly evolved DV contracts."""
    prior = {
        "hubs": [{"name": "hub_customer"}],
        "links": [],
        "satellites": [{"name": "sat_customer", "volatility_tier": "STANDARD"}],
        "column_triage_ledger": [{"column_name": "c_id"}],
    }

    new_contract = {
        "hubs": [{"name": "hub_customer"}, {"name": "hub_account"}],
        "links": [{"name": "lnk_customer_account"}],
        "satellites": [
            {"name": "sat_customer", "volatility_tier": "STANDARD"},
            {"name": "sat_account", "volatility_tier": "STANDARD"},
            {"name": "sat_customer_companion", "volatility_tier": "ADDITIVE_DRIFT_COMPANION"},
        ],
        "column_triage_ledger": [
            {"column_name": "c_id"},
            {"column_name": "a_id"},
            {"column_name": "c_ref"},
            {"column_name": "extra_attr"},
        ],
    }

    diff = compute_blueprint_evolution_diff(prior, new_contract)
    assert diff["added_hubs"] == ["hub_account"]
    assert diff["added_links"] == ["lnk_customer_account"]
    assert sorted(diff["added_satellites"]) == ["sat_account", "sat_customer_companion"]
    assert diff["companion_satellites"] == ["sat_customer_companion"]
    assert diff["total_ledger_columns"] == 4


def test_sandbox_compiler_four_zone_sql_duckdb_and_databricks(
    banking_ontology: GraphAOntology,
) -> None:
    """Verify STAGE_06 compiles 4-zone SQL for both DuckDB and Databricks dialects."""
    contract_seed = synthesize_ontology_seed_contract(banking_ontology, domain_namespace="banking")

    four_zone_sql, sbx_sum, _sbx_rep = compile_and_execute_sandbox(
        contract_dict=contract_seed,
        ontology_dict=banking_ontology.model_dump(mode="json"),
        target_dialect="databricks",
        execute_shadow_sandbox=True,
    )

    # Check DuckDB zones
    assert "duckdb" in four_zone_sql
    duck = four_zone_sql["duckdb"]
    for k in (
        "zone_0_staging",
        "zone_1_raw_vault",
        "zone_2_business_vault",
        "zone_3_info_mart",
        "all_zones",
    ):
        assert k in duck
        assert isinstance(duck[k], str)
        assert len(duck[k]) > 0

    # Check Databricks zones
    assert "databricks" in four_zone_sql
    db = four_zone_sql["databricks"]
    for k in (
        "zone_0_staging",
        "zone_1_raw_vault",
        "zone_2_business_vault",
        "zone_3_info_mart",
        "all_zones",
    ):
        assert k in db
        assert isinstance(db[k], str)
    assert len(db["all_zones"]) > 0

    # Check shadow sandbox execution
    assert sbx_sum["executed"] is True
    assert sbx_sum["passed"] is True


def test_single_run_pipeline_e2e_invariants(
    banking_ontology: GraphAOntology, sample_banking_sqlite: Path, tmp_path: Path
) -> None:
    """Verify single-run coordinator emits VaultBlueprint v1, artifacts, and evolution diff."""
    out_dir_1 = tmp_path / "run_1_out"
    src_input = PipelineSourceFeedInput(
        source_system="core_banking",
        uri=str(sample_banking_sqlite),
        adapter="sqlite",
        options={"multi_table": True},
    )

    spec_v1 = PipelineInputSpec(
        pipeline_id="pipe_banking_test",
        project_id="proj_banking_test",
        domain_namespace="banking",
        ontology=banking_ontology,
        sources=[src_input],
        target_dialect="databricks",
    )

    setup = HarnessSetupConfig(
        provider="agy",
    )

    pipeline_1 = AgenticVaultPipeline(
        input_spec=spec_v1,
        harness_setup=setup,
        output_dir=out_dir_1,
        base_dir=tmp_path,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    rep_1 = pipeline_1.run()

    # 1. Report Status & Inferred Plan
    assert rep_1.status == "COMPLETED"
    assert rep_1.overall_passed is True
    assert rep_1.verdict == "PASS"
    assert rep_1.inference_plan["primary_action"] == "COLD_START_N1"

    # 2. VaultBlueprint Lineage Invariants
    assert rep_1.blueprint is not None
    assert rep_1.blueprint.lineage.blueprint_revision == 1
    assert rep_1.blueprint.lineage.parent_blueprint_sha256 is None
    expected_ont_sha = compute_ontology_sha256(banking_ontology)
    assert rep_1.blueprint.lineage.ontology_sha256 == expected_ont_sha

    # 3. Artifacts Integrity
    bp_disk = out_dir_1 / "vault_blueprint.yaml"
    cnt_disk = out_dir_1 / "dv_contract.json"
    assert bp_disk.exists()
    assert cnt_disk.exists()

    # 4. Dialect SQL Generation
    assert "duckdb" in rep_1.four_zone_sql
    assert "databricks" in rep_1.four_zone_sql

    # 5. Evolution Diff & Ledger
    assert rep_1.evolution_diff is not None
    assert len(rep_1.evolution_diff.get("added_hubs", [])) >= 1

    # --- Run 2: Evolution on top of v1 Blueprint (v2 Lineage Chaining) ---
    out_dir_2 = tmp_path / "run_2_out"
    spec_v2 = PipelineInputSpec(
        pipeline_id="pipe_banking_test",
        project_id="proj_banking_test",
        domain_namespace="banking",
        blueprint=rep_1.blueprint,
        sources=[src_input],
        target_dialect="databricks",
    )

    pipeline_2 = AgenticVaultPipeline(
        input_spec=spec_v2,
        harness_setup=setup,
        output_dir=out_dir_2,
        base_dir=tmp_path,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )
    rep_2 = pipeline_2.run()

    assert rep_2.status == "COMPLETED"
    assert rep_2.overall_passed is True
    assert rep_2.verdict == "PASS"
    assert rep_2.blueprint is not None
    assert rep_2.blueprint.lineage.blueprint_revision == 2
    assert rep_2.blueprint.lineage.parent_blueprint_sha256 == compute_blueprint_sha256(
        rep_1.blueprint
    )


def test_axi_dv_g0_to_g5_invariants_and_toon_v1(
    banking_ontology: GraphAOntology, sample_banking_sqlite: Path, tmp_path: Path
) -> None:
    """Verify AXI-DV G0-G5 static gates and @toon_v1 invariants on synthesized contract."""
    conn = duckdb.connect(":memory:")
    src_input = PipelineSourceFeedInput(
        source_system="core_banking",
        uri=str(sample_banking_sqlite),
        adapter="sqlite",
        options={"multi_table": True},
    )

    disc = discover_tabular_feeds([src_input], conn)
    contract_dict = _synthesize_cumulative_contract(
        ontology=banking_ontology,
        discovered_feeds=disc.discovered_feeds,
        deployed_contract=None,
        domain_namespace="banking",
        is_cold_start=True,
    )

    # G0: Hub invariants
    for h in contract_dict.get("hubs", []):
        assert h["name"].startswith("hub_")
        assert len(h["business_key_sources"]) >= 1
        for bks in h["business_key_sources"]:
            assert len(bks["key_columns"]) >= 1
            assert "record_source_code" in bks

    # G1: Link invariants
    for lnk in contract_dict.get("links", []):
        assert lnk["name"].startswith("lnk_")
        assert len(lnk["hub_references"]) >= 2
        for hr in lnk["hub_references"]:
            assert hr["target_hub"].startswith("hub_")
            assert len(hr["source_fk_columns"]) >= 1

    # G2: Satellite invariants
    for s in contract_dict.get("satellites", []):
        assert s["name"].startswith("sat_")
        assert s["parent_entity"].startswith("hub_") or s["parent_entity"].startswith("lnk_")
        assert isinstance(s["payload_columns"], list)

    # G3: Column Triage Ledger Completeness
    ledger = contract_dict.get("column_triage_ledger", [])
    assert len(ledger) > 0
    for entry in ledger:
        assert entry["bronze_table"]
        assert entry["column_name"]
        assert entry["elm_classification"] in {
            "BUSINESS_KEY",
            "FOREIGN_KEY_REF",
            "OPTIONAL_FOREIGN_KEY_REF",
            "DESCRIPTIVE_PAYLOAD",
            "PII_CONFIDENTIAL_PAYLOAD",
            "MNPI_CONFIDENTIAL_PAYLOAD",
            "DEGENERATE_KEY",
            "TENANT_ISOLATION_KEY",
            "EXCLUDE_FROM_VAULT",
        }
        assert entry["source_sql_type"] is not None
        assert len(entry["source_sql_type"]) > 0
