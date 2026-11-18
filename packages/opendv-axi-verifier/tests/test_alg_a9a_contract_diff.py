"""Unit tests for Algorithm A.9a Contract-Diff Drift Detection.

Verifies that baseline columns, physical SQL types, and key columns are reconstructed
directly from NoMistakeDataVault2ContractV5 / column_triage_ledger.source_sql_type
without requiring raw baseline data files (ADR-018 / WS-3).
"""

from __future__ import annotations

import pytest

from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    codemod_intra_source_schema_drift,
    detect_schema_drift_from_contract,
    reconstruct_baseline_metadata_from_contract,
)
from opendv_core.models.contract_v5 import (
    BusinessKeySource,
    ColumnTriageEntry,
    ContractHubSpec,
    ContractSatelliteSpec,
    NoMistakeDataVault2ContractV5,
)


@pytest.fixture
def sample_v5_contract() -> NoMistakeDataVault2ContractV5:
    """Construct a normative Contract V5 with populated source_sql_type fields."""
    return NoMistakeDataVault2ContractV5(
        domain_namespace="asset_management",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="bronze_holdings",
                column_name="holding_id",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_holding",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="bronze_holdings",
                column_name="fund_id",
                elm_classification="FOREIGN_KEY_REF",
                target_dv_entity="lnk_holding_fund",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="bronze_holdings",
                column_name="market_value",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_holding",
                source_sql_type="NUMERIC(14,2)",
            ),
            ColumnTriageEntry(
                bronze_table="bronze_holdings",
                column_name="holding_name",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_holding",
                source_sql_type="VARCHAR(128)",
            ),
            ColumnTriageEntry(
                bronze_table="bronze_holdings",
                column_name="obsolete_field",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_holding",
                source_sql_type="VARCHAR(32)",
            ),
        ],
        hubs=[
            ContractHubSpec(
                name="hub_holding",
                ontology_concept_uri="urn:ontology:entity:holding",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="bronze_holdings",
                        key_columns=["holding_id"],
                        record_source_code="SRC_HOLDINGS",
                        key_authority_tier="TIER_2_ENTERPRISE_SOR",
                        require_src_qualifier=False,
                    )
                ],
            )
        ],
        links=[],
        satellites=[
            ContractSatelliteSpec(
                name="sat_holding",
                satellite_type="STANDARD_SATELLITE",
                parent_entity="hub_holding",
                source_bronze_table="bronze_holdings",
                payload_columns=["market_value", "holding_name", "obsolete_field"],
            )
        ],
        reference_tables=[],
        unmapped_escalations=[],
    )


def test_reconstruct_baseline_metadata_from_contract(
    sample_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Test extracting deployed_columns, old_types, and key_columns from contract
    without raw files.
    """
    dep_cols, old_types, key_cols = reconstruct_baseline_metadata_from_contract(
        sample_v5_contract, "bronze_holdings"
    )

    assert dep_cols == {
        "holding_id",
        "fund_id",
        "market_value",
        "holding_name",
        "obsolete_field",
    }
    assert old_types == {
        "holding_id": "VARCHAR(64)",
        "fund_id": "VARCHAR(64)",
        "market_value": "NUMERIC(14,2)",
        "holding_name": "VARCHAR(128)",
        "obsolete_field": "VARCHAR(32)",
    }
    assert key_cols == {"holding_id", "fund_id"}


def test_detect_schema_drift_from_contract_all_categories(
    sample_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Test detecting ADDITIVE, SOFT_DEPRECATED, RENAMED, and TYPE_WIDENED
    from contract directly.
    """
    # Incoming drifted table:
    # - "holding_id" (retained key, unchanged)
    # - "fund_id" (retained FK, unchanged)
    # - "market_value" (widened: NUMERIC(14,2) -> NUMERIC(18,2))
    # - "holding_nme" (renamed from "holding_name", similarity 0.9167 >= 0.85)
    # - "obsolete_field" (dropped -> SOFT_DEPRECATED)
    # - "esg_score" (new column -> ADDITIVE)
    candidate_cols = [
        "holding_id",
        "fund_id",
        "market_value",
        "holding_nme",
        "esg_score",
    ]
    candidate_types = {
        "holding_id": "VARCHAR(64)",
        "fund_id": "VARCHAR(64)",
        "market_value": "NUMERIC(18,2)",
        "holding_nme": "VARCHAR(128)",
        "esg_score": "DECIMAL(5,2)",
    }

    findings = detect_schema_drift_from_contract(
        contract=sample_v5_contract,
        source_table="bronze_holdings",
        candidate_columns=candidate_cols,
        candidate_types=candidate_types,
        target_entity="sat_holding",
    )

    by_category = {f.category: f for f in findings}
    assert "TYPE_WIDENED" in by_category
    assert by_category["TYPE_WIDENED"].column_name == "market_value"
    assert by_category["TYPE_WIDENED"].old_type == "NUMERIC(14,2)"
    assert by_category["TYPE_WIDENED"].new_type == "NUMERIC(18,2)"

    assert "RENAMED" in by_category
    assert by_category["RENAMED"].column_name == "holding_name"
    assert by_category["RENAMED"].new_column_name == "holding_nme"

    assert "SOFT_DEPRECATED" in by_category
    assert by_category["SOFT_DEPRECATED"].column_name == "obsolete_field"

    assert "ADDITIVE" in by_category
    assert by_category["ADDITIVE"].column_name == "esg_score"


def test_detect_schema_drift_key_drift_escalation(
    sample_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Test that missing or incompatible key column triggers KEY_DRIFT escalation."""
    # Missing holding_id key column
    candidate_cols = ["fund_id", "market_value", "holding_name"]
    candidate_types = {
        "fund_id": "VARCHAR(64)",
        "market_value": "NUMERIC(14,2)",
        "holding_name": "VARCHAR(128)",
    }

    findings = detect_schema_drift_from_contract(
        contract=sample_v5_contract,
        source_table="bronze_holdings",
        candidate_columns=candidate_cols,
        candidate_types=candidate_types,
        target_entity="sat_holding",
    )

    key_drift = [f for f in findings if f.category == "KEY_DRIFT"]
    assert len(key_drift) >= 1
    assert any(f.column_name == "holding_id" and f.action == "ASK-USER" for f in key_drift)


def test_codemod_intra_source_schema_drift_contract_only(
    sample_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Test codemod produces companion satellite and records source_sql_type in triage ledger."""
    dep_dict = sample_v5_contract.model_dump(mode="json")
    candidate_dict = sample_v5_contract.model_dump(mode="json")

    # Mutate candidate satellite to reflect new schema:
    # drop obsolete_field, rename holding_name -> holding_nme, add esg_score
    cand_sat = candidate_dict["satellites"][0]
    cand_sat["payload_columns"] = ["market_value", "holding_nme", "esg_score"]
    cand_sat["column_types"] = {
        "market_value": "NUMERIC(18,2)",
        "holding_nme": "VARCHAR(128)",
        "esg_score": "DECIMAL(5,2)",
    }

    evolved = codemod_intra_source_schema_drift(
        deployed_contract=dep_dict,
        candidate_contract=candidate_dict,
        schema_epoch_tag="v2",
    )

    # 1. Base satellite retains immutable payload columns
    base_sat = next(s for s in evolved["satellites"] if s["name"] == "sat_holding")
    assert "obsolete_field" in base_sat["soft_deprecated_columns"]
    assert "market_value" in base_sat["payload_columns"]
    assert "holding_name" in base_sat["payload_columns"]

    # 2. Companion satellite spawned for esg_score
    comp_sat = next(s for s in evolved["satellites"] if s["name"] == "sat_holding_ext_v2")
    assert comp_sat["payload_columns"] == ["esg_score"]
    assert comp_sat["volatility_tier"] == "ADDITIVE_DRIFT_COMPANION"

    # 3. Column triage ledger records source_sql_type for the additive column
    ledger_esg = next(
        e
        for e in evolved["column_triage_ledger"]
        if e.get("column_name") == "esg_score" and e.get("bronze_table") == "bronze_holdings"
    )
    assert ledger_esg["target_dv_entity"] == "sat_holding_ext_v2"
    assert ledger_esg["source_sql_type"] == "DECIMAL(5,2)"
