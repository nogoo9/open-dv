"""tests/sandbox/test_asset_management_3wave_stability.py — 3-Wave Stability Certification Suite.

Certifies Features 13 & 14 for Milestone M4:
- Wave 0: Genesis unstructured text only (hub_document, sat_document_metadata, zero keys,
  empty relational sources, unstructured_only: True).
- Wave 1: Additive OMS / IBOR (hub_security, hub_investor, hub_rate_index, lnk_investor_holding,
  sat_security_details, 16-table 4-zone DuckDB compilation).
- Wave 2: Complex Alternative Assets (Private Credit hub_credit_facility, hub_borrower,
  custody CUSIP TIER_1_GLOBAL_STANDARD vs TIER_3_LOCAL_SCOPED custody bkcc disambiguation via
  Same-As Link sal_instrument, and PII/MNPI satellite splits sat_borrower_pii,
  sat_credit_facility_mnpi).
- Algorithm A.7 additive evolution invariant checks (verify_additive_evolution_invariants,
  DV-EVO-001 through DV-EVO-008).
- Algorithm A.6 physical SQL AST verification (verify_physical_dv2_sql_ast, DV-AST-001..007).
- Cumulative DuckDB shadow sandbox execution across Wave 0 -> Wave 1 -> Wave 2 in an in-memory
  DuckDB instance with zero table drops, zero data corruption, zero PK violations
  (duckdb.ConstraintException), zero orphan links, and dynamic checks passing.
- Adversarial breaking change guards: tests confirming rejection of destructive drops,
  BK signature mutations, column drops, and in-place unmigrated hash_diff alterations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import jsonschema
import pytest

from opendv_axi_verifier import (
    verify_additive_evolution_invariants,
    verify_physical_dv2_sql_ast,
)
from opendv_duckdb_sandbox.dynamic_checks import (
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    check_dv_dyn_001_pk_uniqueness,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_006_bkcc_collision_and_sal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V5_PATH = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"


# ---------------------------------------------------------------------------
# Contract Fixture Builders conforming to no-mistake-dv2-contract-v5.schema.json
# ---------------------------------------------------------------------------


def build_wave0_contract() -> dict[str, Any]:
    """Wave 0 Genesis Unstructured Only Contract."""
    return {
        "contract_version": "5.0",
        "domain_namespace": "asset_management",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "dms.documents",
                "column_name": "doc_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_document",
            },
            {
                "bronze_table": "dms.documents",
                "column_name": "file_hash",
                "elm_classification": "TECHNICAL_METADATA",
                "target_dv_entity": "sat_document_metadata",
            },
            {
                "bronze_table": "dms.documents",
                "column_name": "byte_count",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_document_metadata",
            },
            {
                "bronze_table": "dms.documents",
                "column_name": "mime_type",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_document_metadata",
            },
        ],
        "hubs": [
            {
                "name": "hub_document",
                "ontology_concept_uri": "urn:ontology:entity:document",
                "business_key_sources": [
                    {
                        "bronze_table": "dms.documents",
                        "key_columns": ["doc_id"],
                        "record_source_code": "CAS_INGESTION",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [
            {
                "name": "sat_document_metadata",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_document",
                "source_bronze_table": "dms.documents",
                "payload_columns": ["file_hash", "byte_count", "mime_type"],
                "volatility_tier": "STATIC",
                "security_classification": "INTERNAL",
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }


def build_wave1_contract() -> dict[str, Any]:
    """Wave 1 Public OMS / IBOR Additive Contract."""
    w0 = build_wave0_contract()
    return {
        "contract_version": "5.0",
        "domain_namespace": "asset_management",
        "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
        "column_triage_ledger": [
            *w0["column_triage_ledger"],
            {
                "bronze_table": "sec.stg_sec_security_master",
                "column_name": "ticker",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_security",
            },
            {
                "bronze_table": "sec.stg_sec_security_master",
                "column_name": "cik",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_security",
            },
            {
                "bronze_table": "sec.stg_sec_security_master",
                "column_name": "company_name",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_security_details",
            },
            {
                "bronze_table": "sec.stg_sec_security_master",
                "column_name": "exchange",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_security_details",
            },
            {
                "bronze_table": "sec.stg_sec_13f_holdings",
                "column_name": "cik",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_investor",
            },
            {
                "bronze_table": "sec.stg_sec_13f_holdings",
                "column_name": "holding_id",
                "elm_classification": "DEGENERATE_KEY",
                "target_dv_entity": "lnk_investor_holding",
            },
            {
                "bronze_table": "sec.stg_sec_13f_holdings",
                "column_name": "shares",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_holding_position",
            },
            {
                "bronze_table": "sec.stg_sec_13f_holdings",
                "column_name": "market_value_usd",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_holding_position",
            },
            {
                "bronze_table": "sec.stg_sec_13f_holdings",
                "column_name": "investment_discretion",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_holding_position",
            },
            {
                "bronze_table": "fred.stg_fred_treasury_rates",
                "column_name": "series_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_rate_index",
            },
            {
                "bronze_table": "fred.stg_fred_treasury_rates",
                "column_name": "observation_date",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_rate_observation",
            },
            {
                "bronze_table": "fred.stg_fred_treasury_rates",
                "column_name": "yield_pct",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_rate_observation",
            },
        ],
        "hubs": [
            *w0["hubs"],
            {
                "name": "hub_security",
                "ontology_concept_uri": "urn:ontology:entity:security",
                "business_key_sources": [
                    {
                        "bronze_table": "sec.stg_sec_security_master",
                        "key_columns": ["ticker", "cik"],
                        "record_source_code": "SEC_EDGAR",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
            {
                "name": "hub_investor",
                "ontology_concept_uri": "urn:ontology:entity:investor",
                "business_key_sources": [
                    {
                        "bronze_table": "sec.stg_sec_13f_holdings",
                        "key_columns": ["cik"],
                        "record_source_code": "SEC_EDGAR",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
            {
                "name": "hub_rate_index",
                "ontology_concept_uri": "urn:ontology:entity:rate_index",
                "business_key_sources": [
                    {
                        "bronze_table": "fred.stg_fred_treasury_rates",
                        "key_columns": ["series_id"],
                        "record_source_code": "FRED_API",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_investor_holding",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:investor_holding",
                "source_bronze_table": "sec.stg_sec_13f_holdings",
                "degenerate_columns": ["holding_id"],
                "hub_references": [
                    {
                        "target_hub": "hub_investor",
                        "role_alias": "INVESTOR",
                        "source_fk_columns": ["cik"],
                    },
                    {
                        "target_hub": "hub_security",
                        "role_alias": "SECURITY",
                        "source_fk_columns": ["ticker", "cik"],
                    },
                ],
            }
        ],
        "satellites": [
            *w0["satellites"],
            {
                "name": "sat_security_details",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_security",
                "source_bronze_table": "sec.stg_sec_security_master",
                "payload_columns": ["company_name", "exchange"],
                "volatility_tier": "STANDARD",
                "security_classification": "PUBLIC",
            },
            {
                "name": "sat_holding_position",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "lnk_investor_holding",
                "source_bronze_table": "sec.stg_sec_13f_holdings",
                "payload_columns": [
                    "holding_id",
                    "shares",
                    "market_value_usd",
                    "investment_discretion",
                ],
                "volatility_tier": "STANDARD",
                "security_classification": "INTERNAL",
            },
            {
                "name": "sat_rate_observation",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_rate_index",
                "source_bronze_table": "fred.stg_fred_treasury_rates",
                "payload_columns": ["observation_date", "yield_pct"],
                "volatility_tier": "HIGH_FREQ",
                "security_classification": "PUBLIC",
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }


def build_wave2_contract() -> dict[str, Any]:
    """Wave 2 Alternative Assets Additive Contract (Private Credit, SAL, PII/MNPI)."""
    w1 = build_wave1_contract()
    return {
        "contract_version": "5.0",
        "domain_namespace": "asset_management",
        "lifecycle_stage": "MULTI_SOURCE_EXPANSION",
        "column_triage_ledger": [
            *w1["column_triage_ledger"],
            {
                "bronze_table": "credit.stg_facilities",
                "column_name": "facility_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_credit_facility",
            },
            {
                "bronze_table": "credit.stg_facilities",
                "column_name": "borrower_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_borrower",
            },
            {
                "bronze_table": "credit.stg_compliance",
                "column_name": "tax_identifier",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_borrower_pii",
            },
            {
                "bronze_table": "credit.stg_covenants",
                "column_name": "leverage_covenant_max",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_credit_facility_mnpi",
            },
            {
                "bronze_table": "custody.stg_crosswalk",
                "column_name": "global_cusip",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "sal_instrument",
            },
            {
                "bronze_table": "custody.stg_crosswalk",
                "column_name": "custody_local_id",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "sal_instrument",
            },
        ],
        "hubs": [
            *w1["hubs"],
            {
                "name": "hub_credit_facility",
                "ontology_concept_uri": "urn:ontology:entity:credit_facility",
                "business_key_sources": [
                    {
                        "bronze_table": "credit.stg_facilities",
                        "key_columns": ["facility_id"],
                        "record_source_code": "DIRECT_LENDING_DB",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    }
                ],
            },
            {
                "name": "hub_borrower",
                "ontology_concept_uri": "urn:ontology:entity:borrower",
                "business_key_sources": [
                    {
                        "bronze_table": "credit.stg_facilities",
                        "key_columns": ["borrower_id"],
                        "record_source_code": "DIRECT_LENDING_DB",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                        "bkcc_prefix": None,
                    }
                ],
            },
        ],
        "links": [
            *w1["links"],
            {
                "name": "sal_instrument",
                "link_type": "SAME_AS_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:instrument_equivalence",
                "source_bronze_table": "custody.stg_crosswalk",
                "hub_references": [
                    {
                        "target_hub": "hub_security",
                        "role_alias": "MASTER",
                        "source_fk_columns": ["global_cusip"],
                    },
                    {
                        "target_hub": "hub_security",
                        "role_alias": "DUPLICATE",
                        "source_fk_columns": ["custody_local_id"],
                    },
                ],
            },
        ],
        "satellites": [
            w1["satellites"][0],
            w1["satellites"][2],
            w1["satellites"][3],
            {
                "name": "sat_security_details",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_security",
                "source_bronze_table": "sec.stg_sec_security_master",
                "payload_columns": ["company_name", "exchange", "esg_score"],
                "volatility_tier": "STANDARD",
                "security_classification": "PUBLIC",
                "hash_diff_migration_policy": "NULL_EQUIVALENT_PADDING",
            },
            {
                "name": "sat_borrower_pii",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_borrower",
                "source_bronze_table": "credit.stg_compliance",
                "payload_columns": ["tax_identifier"],
                "volatility_tier": "STATIC",
                "security_classification": "PII_HIGH",
            },
            {
                "name": "sat_credit_facility_mnpi",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_credit_facility",
                "source_bronze_table": "credit.stg_covenants",
                "payload_columns": ["leverage_covenant_max"],
                "volatility_tier": "STANDARD",
                "security_classification": "CONFIDENTIAL",
            },
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }


# ---------------------------------------------------------------------------
# SQL DDL Scripts
# ---------------------------------------------------------------------------


def build_wave0_ddl() -> list[str]:
    return [
        "CREATE SCHEMA IF NOT EXISTS staging;",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
        "CREATE SCHEMA IF NOT EXISTS business_vault;",
        "CREATE SCHEMA IF NOT EXISTS marts;",
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_document ("
            "hk_document VARCHAR PRIMARY KEY, "
            "bk_document_id VARCHAR NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, "
            "record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_document_metadata ("
            "hk_document VARCHAR NOT NULL, "
            "file_hash VARCHAR NOT NULL, "
            "byte_count BIGINT, "
            "mime_type VARCHAR, "
            "load_dts TIMESTAMP NOT NULL, "
            "record_source VARCHAR NOT NULL, "
            "hash_diff VARCHAR NOT NULL);"
        ),
    ]


def build_wave1_additive_ddl() -> list[str]:
    """Additive DDL for Wave 1 (completes the 16 tables across 4 zones)."""
    return [
        (
            "CREATE TABLE IF NOT EXISTS staging.stg_sec_security_master ("
            "ticker VARCHAR NOT NULL, cik VARCHAR NOT NULL, company_name VARCHAR, "
            "exchange VARCHAR, load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS staging.stg_sec_13f_holdings ("
            "holding_id VARCHAR NOT NULL, cik VARCHAR NOT NULL, cusip VARCHAR, "
            "ticker VARCHAR, issuer_name VARCHAR, shares BIGINT, "
            "market_value_usd DECIMAL(18,2), investment_discretion VARCHAR, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS staging.stg_fred_treasury_rates ("
            "observation_date DATE NOT NULL, series_id VARCHAR NOT NULL, "
            "yield_pct DECIMAL(6,4), load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_security ("
            "hk_security VARCHAR PRIMARY KEY, bk_ticker VARCHAR NOT NULL, "
            "bk_cik VARCHAR NOT NULL, load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_investor ("
            "hk_investor VARCHAR PRIMARY KEY, bk_cik VARCHAR NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_rate_index ("
            "hk_rate_index VARCHAR PRIMARY KEY, bk_series_id VARCHAR NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding ("
            "hk_investor_holding VARCHAR PRIMARY KEY, hk_investor VARCHAR NOT NULL, "
            "hk_security VARCHAR NOT NULL, load_dts TIMESTAMP NOT NULL, "
            "record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_security_details ("
            "hk_security VARCHAR NOT NULL, company_name VARCHAR, exchange VARCHAR, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL, "
            "hash_diff VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_holding_position ("
            "hk_investor_holding VARCHAR NOT NULL, holding_id VARCHAR, "
            "shares BIGINT, market_value_usd DECIMAL(18,2), investment_discretion VARCHAR, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL, "
            "hash_diff VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_rate_observation ("
            "hk_rate_index VARCHAR NOT NULL, observation_date DATE NOT NULL, "
            "yield_pct DECIMAL(6,4), load_dts TIMESTAMP NOT NULL, "
            "record_source VARCHAR NOT NULL, hash_diff VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS business_vault.pit_security ("
            "hk_security VARCHAR NOT NULL, snapshot_dts TIMESTAMP NOT NULL, "
            "sat_security_details_load_dts TIMESTAMP, PRIMARY KEY (hk_security, snapshot_dts));"
        ),
        (
            "CREATE TABLE IF NOT EXISTS business_vault.bridge_portfolio_holdings ("
            "hk_investor_holding VARCHAR NOT NULL, hk_investor VARCHAR NOT NULL, "
            "hk_security VARCHAR NOT NULL, PRIMARY KEY (hk_investor_holding));"
        ),
        (
            "CREATE TABLE IF NOT EXISTS marts.dim_security ("
            "security_key VARCHAR PRIMARY KEY, ticker VARCHAR NOT NULL, "
            "cik VARCHAR NOT NULL, company_name VARCHAR, exchange VARCHAR);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS marts.fct_institutional_holding ("
            "holding_key VARCHAR PRIMARY KEY, investor_cik VARCHAR NOT NULL, "
            "security_ticker VARCHAR NOT NULL, shares BIGINT, "
            "market_value_usd DECIMAL(18,2), observation_date DATE);"
        ),
    ]


def build_wave2_additive_ddl() -> list[str]:
    """Additive DDL for Wave 2 (Alternative assets, SAL, PII/MNPI, in-place column addition)."""
    return [
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_credit_facility ("
            "hk_credit_facility VARCHAR PRIMARY KEY, bk_facility_id VARCHAR NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_borrower ("
            "hk_borrower VARCHAR PRIMARY KEY, bk_borrower_id VARCHAR NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sal_instrument ("
            "hk_sal_instrument VARCHAR PRIMARY KEY, hk_master_security VARCHAR NOT NULL, "
            "hk_duplicate_security VARCHAR NOT NULL, load_dts TIMESTAMP NOT NULL, "
            "record_source VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_borrower_pii ("
            "hk_borrower VARCHAR NOT NULL, tax_identifier VARCHAR, "
            "authorized_signer_email VARCHAR, guarantor_ssn VARCHAR, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL, "
            "hash_diff VARCHAR NOT NULL);"
        ),
        (
            "CREATE TABLE IF NOT EXISTS raw_vault.sat_credit_facility_mnpi ("
            "hk_credit_facility VARCHAR NOT NULL, leverage_covenant_max DECIMAL(5,2), "
            "ebitda_headroom_pct DECIMAL(5,2), insider_restricted_flag BOOLEAN, "
            "load_dts TIMESTAMP NOT NULL, record_source VARCHAR NOT NULL, "
            "hash_diff VARCHAR NOT NULL);"
        ),
        (
            "ALTER TABLE raw_vault.sat_security_details ADD COLUMN IF NOT EXISTS "
            "esg_score DECIMAL(5,2);"
        ),
    ]


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------


class TestWave0UnstructuredGenesis:
    """Feature 13 & 14 Wave 0: Genesis unstructured text only."""

    def test_wave0_contract_conforms_to_v5_schema(self) -> None:
        """Assert Wave 0 genesis contract conforms strictly to v5 JSON schema."""
        contract = build_wave0_contract()
        schema = json.loads(SCHEMA_V5_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"
        assert contract["lifecycle_stage"] == "COLD_START_N1"
        assert len(contract["hubs"]) == 1
        assert contract["hubs"][0]["name"] == "hub_document"

    def test_wave0_sql_ast_safety(self) -> None:
        """Verify Algorithm A.6 SQL AST static verifier passes Wave 0 DDL with 0 violations."""
        for ddl in build_wave0_ddl():
            violations = verify_physical_dv2_sql_ast(ddl)
            assert violations == [], f"AST violation in Wave 0 DDL: {violations}"

    def test_wave0_duckdb_genesis_sandbox_execution(self) -> None:
        """Execute Wave 0 DDL in DuckDB in-memory database with Dual Zero-Keys and test document."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl():
            conn.execute(stmt)

        # Seed Dual Zero-Keys and Ghost Record
        conn.execute(f"""
            INSERT INTO raw_vault.hub_document VALUES
            ('{ZERO_KEY_UNKNOWN}', 'SYSTEM_ZERO_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
            ('{ZERO_KEY_ERROR}', 'SYSTEM_ERROR_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY');
        """)
        conn.execute(f"""
            INSERT INTO raw_vault.sat_document_metadata VALUES
            ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_UNKNOWN}', 0, 'application/octet-stream',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY', '{ZERO_KEY_UNKNOWN}');
        """)

        # Insert real Wave 0 unstructured document
        conn.execute("""
            INSERT INTO raw_vault.hub_document VALUES
            ('hk_doc_calpers_2026', 'DOC-CALPERS-2026-ALLOCATION',
             CURRENT_TIMESTAMP, 'CAS_INGESTION');
            INSERT INTO raw_vault.sat_document_metadata VALUES
            ('hk_doc_calpers_2026', 'a1b2c3d4e5f6', 2048576, 'application/pdf',
             CURRENT_TIMESTAMP, 'CAS_INGESTION', 'hd_calpers_doc_01');
        """)

        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'raw_vault' ORDER BY table_name;"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert table_names == ["hub_document", "sat_document_metadata"]

        doc_count = conn.execute("SELECT COUNT(*) FROM raw_vault.hub_document;").fetchone()[0]
        assert doc_count == 3  # 2 zero keys + 1 real document


class TestWave1PublicOmsIborProgression:
    """Feature 13 & 14 Wave 1: Additive OMS / IBOR on top of Wave 0."""

    def test_wave1_contract_conforms_to_v5_schema(self) -> None:
        """Assert Wave 1 additive contract conforms strictly to v5 JSON schema."""
        contract = build_wave1_contract()
        schema = json.loads(SCHEMA_V5_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"
        assert contract["lifecycle_stage"] == "MULTI_SOURCE_EXPANSION"
        hub_names = {h["name"] for h in contract["hubs"]}
        assert {"hub_document", "hub_security", "hub_investor", "hub_rate_index"}.issubset(
            hub_names
        )

    def test_w0_to_w1_additive_evolution_invariants_pass(self) -> None:
        """Verify Algorithm A.7 confirms 0 breaking changes (DV-EVO-001..008) from W0 to W1."""
        c_w0 = build_wave0_contract()
        c_w1 = build_wave1_contract()
        violations = verify_additive_evolution_invariants(c_w0, c_w1)
        assert violations == [], f"Evolution lock violations from W0 to W1: {violations}"

    def test_wave1_sql_ast_safety(self) -> None:
        """Verify Algorithm A.6 SQL AST static verifier passes all Wave 1 DDL with 0 violations."""
        for ddl in build_wave1_additive_ddl():
            violations = verify_physical_dv2_sql_ast(ddl)
            assert violations == [], f"AST violation in Wave 1 DDL: {violations}"

    def test_wave1_additive_compilation_and_execution_in_duckdb(self) -> None:
        """Execute Wave 1 additive DDL, asserting 16 tables across 4 zones."""
        conn = duckdb.connect(":memory:")
        # 1. Initialize Wave 0
        for stmt in build_wave0_ddl():
            conn.execute(stmt)
        conn.execute("""
            INSERT INTO raw_vault.hub_document VALUES
            ('hk_doc_calpers_2026', 'DOC-CALPERS-2026', CURRENT_TIMESTAMP, 'CAS_INGESTION');
            INSERT INTO raw_vault.sat_document_metadata VALUES
            ('hk_doc_calpers_2026', 'hash_01', 1000, 'application/pdf',
             CURRENT_TIMESTAMP, 'CAS_INGESTION', 'diff_01');
        """)

        # 2. Additive migration to Wave 1
        for stmt in build_wave1_additive_ddl():
            conn.execute(stmt)

        # 3. Verify exactly 16 tables across the 4 zones
        tables_res = conn.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts')
            ORDER BY table_schema, table_name;
        """).fetchall()
        assert len(tables_res) == 16, f"Expected 16 tables across 4 zones, found {len(tables_res)}"

        # 4. Verify Wave 0 data is preserved without corruption
        doc_count = conn.execute(
            "SELECT count(*) FROM raw_vault.hub_document WHERE hk_document = 'hk_doc_calpers_2026';"
        ).fetchone()[0]
        assert doc_count == 1, "Wave 0 document was dropped or lost during Wave 1 migration!"

        # 5. Seed sample OMS/IBOR structured data
        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_aapl', 'AAPL', '0000320193', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.hub_investor VALUES
            ('hk_inv_berkshire', '0001067983', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.hub_rate_index VALUES
            ('hk_rate_dgs10', 'DGS10', CURRENT_TIMESTAMP, 'FRED_API');
            INSERT INTO raw_vault.lnk_investor_holding VALUES
            ('hk_lnk_berkshire_aapl', 'hk_inv_berkshire', 'hk_sec_aapl',
             CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.sat_security_details VALUES
            ('hk_sec_aapl', 'Apple Inc.', 'NASDAQ', CURRENT_TIMESTAMP,
             'SEC_EDGAR', 'diff_sec_aapl');
            INSERT INTO raw_vault.sat_holding_position VALUES
            ('hk_lnk_berkshire_aapl', 'HOLDING_001', 915560382, 174300000.00, 'SOLE',
             CURRENT_TIMESTAMP, 'SEC_EDGAR', 'diff_pos_001');
            INSERT INTO raw_vault.sat_rate_observation VALUES
            ('hk_rate_dgs10', DATE '2026-03-31', 4.2500, CURRENT_TIMESTAMP,
             'FRED_API', 'diff_rate_001');
        """)

        # 6. Verify Dynamic Checks: PK uniqueness & Referential integrity
        pk_res = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_res.passed is True, f"PK uniqueness failed: {pk_res.detail}"

        fk_res = check_dv_dyn_002_referential_integrity(conn)
        assert fk_res.passed is True, f"Referential integrity failed: {fk_res.detail}"


class TestWave2ComplexAlternativeAssetsProgression:
    """Feature 13 & 14 Wave 2: Complex Alternative Assets, SAL, and PII/MNPI splits."""

    def test_wave2_contract_conforms_to_v5_schema(self) -> None:
        """Assert Wave 2 complex contract conforms strictly to v5 JSON schema."""
        contract = build_wave2_contract()
        schema = json.loads(SCHEMA_V5_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"
        hub_names = {h["name"] for h in contract["hubs"]}
        assert {"hub_credit_facility", "hub_borrower"}.issubset(hub_names)
        sat_names = {s["name"] for s in contract["satellites"]}
        assert {"sat_borrower_pii", "sat_credit_facility_mnpi"}.issubset(sat_names)

    def test_w1_to_w2_additive_evolution_invariants_pass(self) -> None:
        """Verify Algorithm A.7 confirms 0 breaking changes from Wave 1 to Wave 2."""
        c_w1 = build_wave1_contract()
        c_w2 = build_wave2_contract()
        violations = verify_additive_evolution_invariants(c_w1, c_w2)
        assert violations == [], f"Evolution lock violations from W1 to W2: {violations}"

    def test_wave2_sql_ast_safety(self) -> None:
        """Verify Algorithm A.6 SQL AST static verifier passes all Wave 2 DDL statements."""
        for ddl in build_wave2_additive_ddl():
            violations = verify_physical_dv2_sql_ast(ddl)
            assert violations == [], f"AST violation in Wave 2 DDL: {violations}"

    def test_wave2_additive_migration_and_dynamic_checks_in_duckdb(self) -> None:
        """Execute Wave 2 additive migration, asserting 21 tables across 4 zones."""
        conn = duckdb.connect(":memory:")
        # 1. Set up Wave 0 and Wave 1
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl():
            conn.execute(stmt)

        # 2. Additive migration to Wave 2
        for stmt in build_wave2_additive_ddl():
            conn.execute(stmt)

        # 3. Verify exactly 21 tables across 4 zones (16 baseline + 5 new)
        tables_res = conn.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts')
            ORDER BY table_schema, table_name;
        """).fetchall()
        assert len(tables_res) == 21, f"Expected 21 tables across 4 zones, found {len(tables_res)}"

        # 4. Verify in-place satellite schema evolution: esg_score column exists
        cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw_vault' AND table_name = 'sat_security_details';"
            ).fetchall()
        ]
        assert "esg_score" in cols

    def test_wave2_cusip_tier1_vs_custody_tier3_sal_disambiguation(self) -> None:
        """Verify Same-As Link sal_instrument links TIER_1 CUSIP to TIER_3 custody bkcc."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        # Global CUSIP standard (TIER_1_GLOBAL_STANDARD)
        hk_master_cusip = "hk_sec_cusip_037833100"
        # Custody local scoped identifier (TIER_3_LOCAL_SCOPED: CUSTODY_BNY)
        hk_custody_local = "hk_sec_bny_custody_99210"

        # Populate Hubs including both master and duplicate keys
        conn.execute(f"""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_seed', 'BORR-SEED', CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB');
            INSERT INTO raw_vault.hub_security VALUES
            ('{hk_master_cusip}', 'AAPL', '0000320193', CURRENT_TIMESTAMP, 'SEC_EDGAR'),
            ('{hk_custody_local}', 'AAPL_BNY', '0000320193', CURRENT_TIMESTAMP, 'CUSTODY_BNY');
        """)

        # Disambiguate via Same-As Link sal_instrument
        conn.execute(f"""
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_inst_001', '{hk_master_cusip}', '{hk_custody_local}',
             CURRENT_TIMESTAMP, 'STEWARD_CROSSWALK');
        """)

        # Evaluate DV-DYN-006 BKCC collision and SAL check
        sal_check = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert sal_check.passed is True, f"SAL check failed: {sal_check.detail}"

        # Assert no self-loops (master != duplicate)
        self_loops = conn.execute("""
            SELECT count(*) FROM raw_vault.sal_instrument
            WHERE hk_master_security = hk_duplicate_security;
        """).fetchone()[0]
        assert self_loops == 0

        # Assert zero FK orphans
        fk_check = check_dv_dyn_002_referential_integrity(conn)
        assert fk_check.passed is True

    def test_wave2_pii_and_mnpi_satellite_splits_isolation(self) -> None:
        """Assert PII and MNPI satellites exist as distinct companion tables."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        # Seed Borrower and Facility
        conn.execute("""
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_001', 'BORR-APEX-TECH', CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB');
            INSERT INTO raw_vault.hub_credit_facility VALUES
            ('hk_fac_001', 'FAC-REVOLVER-2026', CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB');
        """)

        # Insert PII into sat_borrower_pii
        conn.execute("""
            INSERT INTO raw_vault.sat_borrower_pii VALUES
            ('hk_borr_001', 'XX-XXX9876', 'cfo@apextech.com', 'XXX-XX-4321',
             CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB', 'diff_pii_001');
        """)

        # Insert MNPI into sat_credit_facility_mnpi
        conn.execute("""
            INSERT INTO raw_vault.sat_credit_facility_mnpi VALUES
            ('hk_fac_001', 4.50, 18.25, TRUE, CURRENT_TIMESTAMP,
             'DIRECT_LENDING_DB', 'diff_mnpi_001');
        """)

        # Verify tables are distinct and contain exactly 1 row
        pii_cnt = conn.execute("SELECT count(*) FROM raw_vault.sat_borrower_pii;").fetchone()[0]
        mnpi_cnt = conn.execute(
            "SELECT count(*) FROM raw_vault.sat_credit_facility_mnpi;"
        ).fetchone()[0]
        assert pii_cnt == 1
        assert mnpi_cnt == 1

        # Verify dynamic checks pass on Wave 2 state
        pk_res = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_res.passed is True
        fk_res = check_dv_dyn_002_referential_integrity(conn)
        assert fk_res.passed is True

    def test_wave2_duckdb_constraint_exception_on_duplicate_pk(self) -> None:
        """Verify DuckDB enforces PRIMARY KEY constraints natively."""
        conn = duckdb.connect(":memory:")
        for stmt in build_wave0_ddl() + build_wave1_additive_ddl() + build_wave2_additive_ddl():
            conn.execute(stmt)

        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_dup_001', 'MSFT', '0000789019', CURRENT_TIMESTAMP, 'SEC_EDGAR');
        """)

        # Attempt duplicate insertion on PRIMARY KEY must raise duckdb.ConstraintException
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("""
                INSERT INTO raw_vault.hub_security VALUES
                ('hk_dup_001', 'MSFT_ALIAS', '0000789019', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            """)


class TestThreeWaveCumulativeShadowSandbox:
    """Cumulative continuous progression across Wave 0 -> Wave 1 -> Wave 2 in DuckDB."""

    def test_cumulative_progression_wave0_to_wave1_to_wave2_zero_corruption(self) -> None:
        """Execute continuous 3-wave progression in one DuckDB with zero table drops."""
        conn = duckdb.connect(":memory:")

        # ===================================================================
        # Wave 0: Genesis
        # ===================================================================
        for stmt in build_wave0_ddl():
            conn.execute(stmt)

        conn.execute(f"""
            INSERT INTO raw_vault.hub_document VALUES
            ('{ZERO_KEY_UNKNOWN}', 'SYSTEM_ZERO_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
            ('{ZERO_KEY_ERROR}', 'SYSTEM_ERROR_KEY',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY'),
            ('hk_doc_genesis', 'DOC-W0-CALPERS', CURRENT_TIMESTAMP, 'CAS_INGESTION');
        """)
        conn.execute(f"""
            INSERT INTO raw_vault.sat_document_metadata VALUES
            ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_UNKNOWN}', 0, 'application/octet-stream',
             TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO_KEY', '{ZERO_KEY_UNKNOWN}'),
            ('hk_doc_genesis', 'sha256_genesis_hash', 1048576, 'application/pdf',
             CURRENT_TIMESTAMP, 'CAS_INGESTION', 'hd_genesis_01');
        """)

        w0_tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');"
        ).fetchone()[0]
        assert w0_tables == 2

        # ===================================================================
        # Wave 1: Additive OMS / IBOR
        # ===================================================================
        for stmt in build_wave1_additive_ddl():
            conn.execute(stmt)

        w1_tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');"
        ).fetchone()[0]
        assert w1_tables == 16

        # Assert Wave 0 data is bit-for-bit intact
        w0_doc_recheck = conn.execute(
            "SELECT bk_document_id FROM raw_vault.hub_document "
            "WHERE hk_document = 'hk_doc_genesis';"
        ).fetchone()[0]
        assert w0_doc_recheck == "DOC-W0-CALPERS"

        # Populate Wave 1 data
        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_nvda', 'NVDA', '0001045810', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.hub_investor VALUES
            ('hk_inv_vanguard', '0000102909', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.hub_rate_index VALUES
            ('hk_rate_dgs10', 'DGS10', CURRENT_TIMESTAMP, 'FRED_API');
            INSERT INTO raw_vault.lnk_investor_holding VALUES
            ('hk_lnk_vg_nvda', 'hk_inv_vanguard', 'hk_sec_nvda', CURRENT_TIMESTAMP, 'SEC_EDGAR');
            INSERT INTO raw_vault.sat_security_details VALUES
            ('hk_sec_nvda', 'NVIDIA Corporation', 'NASDAQ', CURRENT_TIMESTAMP,
             'SEC_EDGAR', 'diff_sec_nvda');
            INSERT INTO raw_vault.sat_holding_position VALUES
            ('hk_lnk_vg_nvda', 'HOLD_NVDA_01', 200000000, 24000000000.00, 'SOLE',
             CURRENT_TIMESTAMP, 'SEC_EDGAR', 'diff_pos_nvda');
            INSERT INTO raw_vault.sat_rate_observation VALUES
            ('hk_rate_dgs10', DATE '2026-03-31', 4.2500, CURRENT_TIMESTAMP,
             'FRED_API', 'diff_rate_001');
        """)

        # Run dynamic check on Wave 1 state
        assert check_dv_dyn_001_pk_uniqueness(conn).passed is True
        assert check_dv_dyn_002_referential_integrity(conn).passed is True

        # ===================================================================
        # Wave 2: Alternative Assets, SAL & PII/MNPI Splits
        # ===================================================================
        for stmt in build_wave2_additive_ddl():
            conn.execute(stmt)

        w2_tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema IN ('staging', 'raw_vault', 'business_vault', 'marts');"
        ).fetchone()[0]
        assert w2_tables == 21

        # Assert Wave 0 and Wave 1 data still intact
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_document;").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM raw_vault.hub_security;").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM raw_vault.lnk_investor_holding;").fetchone()[0] == 1
        )

        # Populate Wave 2 data (including custody alias in hub_security for clean FK resolution)
        conn.execute("""
            INSERT INTO raw_vault.hub_security VALUES
            ('hk_sec_nvda_custody_alias', 'NVDA_BNY', '0001045810',
             CURRENT_TIMESTAMP, 'CUSTODY_BNY');
            INSERT INTO raw_vault.hub_credit_facility VALUES
            ('hk_fac_mezzanine', 'FAC-MEZZ-2026', CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB');
            INSERT INTO raw_vault.hub_borrower VALUES
            ('hk_borr_titan', 'BORR-TITAN-HOLDINGS', CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB');
            INSERT INTO raw_vault.sal_instrument VALUES
            ('hk_sal_nvda_cross', 'hk_sec_nvda', 'hk_sec_nvda_custody_alias',
             CURRENT_TIMESTAMP, 'CROSSWALK');
            INSERT INTO raw_vault.sat_borrower_pii VALUES
            ('hk_borr_titan', 'XX-XXX5555', 'ceo@titan.com', 'XXX-XX-1111',
             CURRENT_TIMESTAMP, 'DIRECT_LENDING_DB', 'diff_pii_titan');
            INSERT INTO raw_vault.sat_credit_facility_mnpi VALUES
            ('hk_fac_mezzanine', 5.00, 15.00, FALSE, CURRENT_TIMESTAMP,
             'DIRECT_LENDING_DB', 'diff_mnpi_fac');
            UPDATE raw_vault.sat_security_details SET esg_score = 92.50
            WHERE hk_security = 'hk_sec_nvda';
        """)

        # Verify all dynamic invariants pass cumulatively
        pk_check = check_dv_dyn_001_pk_uniqueness(conn)
        assert pk_check.passed is True, f"Cumulative PK uniqueness failed: {pk_check.detail}"

        fk_check = check_dv_dyn_002_referential_integrity(conn)
        assert fk_check.passed is True, (
            f"Cumulative referential integrity failed: {fk_check.detail}"
        )

        sal_check = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert sal_check.passed is True, f"Cumulative SAL/BKCC check failed: {sal_check.detail}"


class TestThreeWaveAdversarialBreakingChangeGuards:
    """Adversarial breaking change guards: verifying rejection of non-additive evolution."""

    def test_reject_hub_deletion_evo_002(self) -> None:
        """Assert deleting hub_document or hub_security in evolved contract triggers DV-EVO-002."""
        w1 = build_wave1_contract()
        # Adversarial: drop hub_document
        w1_bad = {
            **w1,
            "hubs": [h for h in w1["hubs"] if h["name"] != "hub_document"],
        }
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-002" in v and "hub_document" in v for v in violations)

    def test_reject_satellite_deletion_evo_002(self) -> None:
        """Assert deleting sat_security_details triggers DV-EVO-002."""
        w1 = build_wave1_contract()
        w1_bad = {
            **w1,
            "satellites": [s for s in w1["satellites"] if s["name"] != "sat_security_details"],
        }
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-002" in v and "sat_security_details" in v for v in violations)

    def test_reject_link_deletion_evo_002(self) -> None:
        """Assert deleting lnk_investor_holding triggers DV-EVO-002."""
        w1 = build_wave1_contract()
        w1_bad = {
            **w1,
            "links": [link for link in w1["links"] if link["name"] != "lnk_investor_holding"],
        }
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-002" in v and "lnk_investor_holding" in v for v in violations)

    def test_reject_bk_signature_mutation_evo_003(self) -> None:
        """Assert mutating business key columns triggers DV-EVO-003."""
        w1 = build_wave1_contract()
        w1_bad = json.loads(json.dumps(w1))
        # Mutate hub_security key_columns from ['ticker', 'cik'] to ['ticker']
        for h in w1_bad["hubs"]:
            if h["name"] == "hub_security":
                h["business_key_sources"][0]["key_columns"] = ["ticker"]
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-003" in v and "BK_SIGNATURE_MUTATED" in v for v in violations)

    def test_reject_bkcc_mutation_evo_003_evo_007(self) -> None:
        """Assert mutating bkcc_prefix from GLOBAL to LOCAL triggers DV-EVO-003/DV-EVO-007."""
        w1 = build_wave1_contract()
        w1_bad = json.loads(json.dumps(w1))
        for h in w1_bad["hubs"]:
            if h["name"] == "hub_security":
                h["business_key_sources"][0]["bkcc_prefix"] = "LOCAL_SCOPED"
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("BKCC_MUTATED" in v for v in violations)

    def test_reject_satellite_column_dropped_evo_004(self) -> None:
        """Assert dropping a payload column from sat_security_details triggers DV-EVO-004."""
        w1 = build_wave1_contract()
        w1_bad = json.loads(json.dumps(w1))
        for s in w1_bad["satellites"]:
            if s["name"] == "sat_security_details":
                # Drop exchange column
                s["payload_columns"] = ["company_name"]
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-004" in v and "SAT_COLUMN_DROPPED" in v for v in violations)

    def test_reject_in_place_hashdiff_alteration_without_policy_evo_006(self) -> None:
        """Assert adding in-place col without hash_diff_migration_policy triggers DV-EVO-006."""
        w1 = build_wave1_contract()
        w1_bad = json.loads(json.dumps(w1))
        for s in w1_bad["satellites"]:
            if s["name"] == "sat_security_details":
                # Add unmigrated column without companion satellite or policy
                s["payload_columns"] = ["company_name", "exchange", "unmigrated_col"]
                s.pop("hash_diff_migration_policy", None)
        violations = verify_additive_evolution_invariants(w1, w1_bad)
        assert any("DV-EVO-006" in v and "SAT_HASHDIFF_INVALIDATED" in v for v in violations)

    def test_reject_destructive_dml_sql_evo_005(self) -> None:
        """Assert migration scripts containing DROP TABLE or TRUNCATE trigger DV-EVO-005."""
        c = build_wave1_contract()
        bad_scripts = [
            "DROP TABLE raw_vault.hub_security;",
            "TRUNCATE TABLE raw_vault.sat_security_details;",
            "DELETE FROM raw_vault.hub_investor WHERE 1=1;",
        ]
        violations = verify_additive_evolution_invariants(c, c, sql_scripts=bad_scripts)
        assert any("DV-EVO-005" in v for v in violations)

    def test_reject_destructive_ddl_ast_001(self) -> None:
        """Assert verify_physical_dv2_sql_ast flags destructive DROP and TRUNCATE statements."""
        v_drop = verify_physical_dv2_sql_ast("DROP TABLE raw_vault.hub_security;")
        assert len(v_drop) > 0
        assert any("DV-AST-001" in v or "DV-G6-002" in v for v in v_drop)

        v_trunc = verify_physical_dv2_sql_ast("TRUNCATE TABLE raw_vault.sat_security_details;")
        assert len(v_trunc) > 0
        assert any("DV-AST-001" in v or "DV-G6-002" in v for v in v_trunc)

    def test_reject_hash_config_mutation_evo_001(self) -> None:
        """Assert mutating global hash algorithm from sha256 to md5 triggers DV-EVO-001."""
        prev = {"hash_config": {"algorithm": "sha256", "delimiter": "||"}}
        next_ = {"hash_config": {"algorithm": "md5", "delimiter": "||"}}
        violations = verify_additive_evolution_invariants(prev, next_)
        assert any("DV-EVO-001" in v for v in violations)
