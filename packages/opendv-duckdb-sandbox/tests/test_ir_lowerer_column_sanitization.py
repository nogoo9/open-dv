"""TDD tests for SQL-safe identifier sanitization in `lower_contract_to_canonical_ir`."""

from __future__ import annotations

import sqlglot

from opendv_duckdb_sandbox import (
    DuckDbShadowSandbox,
    compile_canonical_ir_to_4zone_sql,
    lower_contract_to_canonical_ir,
)
from opendv_duckdb_sandbox.dialects.databricks_transpiler import (
    compile_canonical_ir_to_databricks,
)


def _sample_spaced_columns_contract() -> dict:
    return {
        "contract_version": "5.0",
        "domain_namespace": "clinical_trials",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "NCT Number",
                "elm_classification": "BUSINESS_KEY",
                "source_sql_type": "VARCHAR(64)",
                "target_dv_entity": "hub_study",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "Study Title",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "VARCHAR",
                "target_dv_entity": "sat_study_core",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "Total ($)",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "INTEGER",
                "target_dv_entity": "sat_study_core",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "1st_Phase",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "VARCHAR",
                "target_dv_entity": "sat_study_core",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "Foo Bar",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "VARCHAR",
                "target_dv_entity": "sat_study_core",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "Foo-Bar",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "VARCHAR",
                "target_dv_entity": "sat_study_core",
            },
            {
                "bronze_table": "trials_csv.studies",
                "column_name": "market_value",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "source_sql_type": "DECIMAL(18,2)",
                "target_dv_entity": "sat_study_core",
            },
        ],
        "hubs": [
            {
                "name": "hub_study",
                "ontology_concept_uri": "urn:ontology:entity:clinical_study",
                "business_key_sources": [
                    {
                        "bronze_table": "trials_csv.studies",
                        "key_columns": ["NCT Number"],
                        "record_source_code": "TRIALS_CSV",
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
                "name": "sat_study_core",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_study",
                "source_bronze_table": "trials_csv.studies",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": [
                    "Study Title",
                    "Total ($)",
                    "1st_Phase",
                    "Foo Bar",
                    "Foo-Bar",
                    "market_value",
                ],
                "soft_deprecated_columns": [],
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }


def test_ir_lowerer_sanitizes_spaced_and_punctuated_columns_for_4zone_and_databricks() -> None:
    """Space-containing CSV headers are lowered to SQL-safe idents and pass 15/15 DV-DYN."""
    contract = _sample_spaced_columns_contract()
    ir = lower_contract_to_canonical_ir(contract, validate_schema=True)

    hub_bk_names = [bk.name for bk in ir.hubs[0].business_keys if not bk.is_bkcc]
    assert hub_bk_names == ["nct_number"]

    sat_col_names = [c.name for c in ir.satellites[0].payload_columns]
    assert sat_col_names == [
        "study_title",
        "total",
        "col_1st_phase",
        "foo_bar",
        "foo_bar_2",
        "market_value",
    ]

    bundle = compile_canonical_ir_to_4zone_sql(ir)
    assert "study_title VARCHAR" in bundle.zone_0_sql
    assert "CREATE TABLE IF NOT EXISTS raw_vault.sat_study_core" in bundle.zone_0_sql

    db_bundle = compile_canonical_ir_to_databricks(ir)
    assert "USING DELTA" in db_bundle.full_sql_bundle.upper()
    parsed = [s for s in sqlglot.parse(db_bundle.full_sql_bundle, read="databricks") if s]
    assert len(parsed) > 0

    bronze_rows = {
        "trials_csv.studies": [
            {
                "NCT Number": "NCT02408120",
                "Study Title": "Phase II Trial",
                "Total ($)": 42,
                "1st_Phase": "PHASE2",
                "Foo Bar": "alpha",
                "Foo-Bar": "beta",
                "market_value": "100.50",
            },
            {
                "NCT Number": "NCT09999999",
                "Study Title": "Followup Trial",
                "Total ($)": 12,
                "1st_Phase": "PHASE3",
                "Foo Bar": "gamma",
                "Foo-Bar": "delta",
                "market_value": "200.00",
            },
        ]
    }
    with DuckDbShadowSandbox() as sbx:
        rep = sbx.run_full_verification(ir, bronze_tables=bronze_rows)
        assert rep.passed is True
        assert rep.dynamic_check_report.passed_count == 15
        rows = sbx.conn.execute(
            "SELECT study_title, foo_bar, foo_bar_2 FROM raw_vault.sat_study_core "
            "WHERE rec_src != 'SYSTEM_GHOST' ORDER BY study_title"
        ).fetchall()
        assert ("Followup Trial", "gamma", "delta") in rows
        assert ("Phase II Trial", "alpha", "beta") in rows
