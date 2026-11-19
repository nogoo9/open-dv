"""Unit tests for Databricks Transpiler and Delta Compiler.

Validates:
  - Databricks physical type normalization
  - Canonical Algorithm A.5 SHA-256 length-prefixed hashing for Databricks
  - Delta Lake DDL generation with USING DELTA
  - Idempotent Delta MERGE INTO patterns for Hubs, Links, and Satellites
  - DuckDB-to-Databricks sqlglot transpilation
  - CompiledDatabricksBundle structure and full SQL generation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.bundle import Compiled4ZoneBundle
from opendv_duckdb_sandbox.dialects.databricks_transpiler import (
    CompiledDatabricksBundle,
    build_databricks_algorithm_a5_sha256_sql,
    compile_canonical_ir_to_databricks,
    compile_databricks_hub_ddl_and_zero_keys,
    compile_databricks_link_ddl,
    compile_databricks_satellite_ddl,
    compile_databricks_zone_2_merge,
    normalize_databricks_type,
    transpile_bundle_to_databricks,
    transpile_duckdb_to_databricks_sql,
)

PE_IR_PATH = (
    Path(__file__).resolve().parents[3] / "examples" / "private_equity" / "pe_canonical_ir.json"
)


@pytest.fixture
def pe_canonical_ir() -> CanonicalIR:
    """Fixture loading canonical Private Equity CanonicalIR model."""
    assert PE_IR_PATH.exists(), f"Missing fixture at {PE_IR_PATH}"
    return CanonicalIR.model_validate_json(PE_IR_PATH.read_text(encoding="utf-8"))


def test_normalize_databricks_type() -> None:
    """Verify data type mappings to standard Databricks Delta SQL types."""
    assert normalize_databricks_type("CHAR(64)") == "STRING"
    assert normalize_databricks_type("VARCHAR(255)") == "STRING"
    assert normalize_databricks_type("TEXT") == "STRING"
    assert normalize_databricks_type("STRING") == "STRING"
    assert normalize_databricks_type("TIMESTAMP_NTZ(6)") == "TIMESTAMP"
    assert normalize_databricks_type("TIMESTAMP") == "TIMESTAMP"
    assert normalize_databricks_type("DATE") == "DATE"
    assert normalize_databricks_type("BIGINT") == "BIGINT"
    assert normalize_databricks_type("INT8") == "BIGINT"
    assert normalize_databricks_type("INTEGER") == "INT"
    assert normalize_databricks_type("INT4") == "INT"
    assert normalize_databricks_type("DOUBLE") == "DOUBLE"
    assert normalize_databricks_type("FLOAT") == "DOUBLE"
    assert normalize_databricks_type("DECIMAL(18, 4)") == "DECIMAL(18, 4)"
    assert normalize_databricks_type("BOOLEAN") == "BOOLEAN"


def test_build_databricks_algorithm_a5_sha256_sql_business_key() -> None:
    """Verify length-prefixed Algorithm A.5 SHA-256 for business keys in Databricks."""
    sql = build_databricks_algorithm_a5_sha256_sql(
        ["fund_id"],
        is_business_key=True,
    )
    # Uses SHA2(..., 256)
    assert "SHA2(" in sql
    assert ", 256)" in sql
    # Length-prefixed tokenization
    assert "OCTET_LENGTH(" in sql
    # Dual zero-key handling
    assert "REPEAT('f', 64)" in sql
    assert "REPEAT('0', 64)" in sql
    assert "'-1'" in sql
    assert "'-2'" in sql


def test_build_databricks_algorithm_a5_sha256_sql_payload() -> None:
    """Verify payload hash_diff generation without zero-key routing."""
    sql = build_databricks_algorithm_a5_sha256_sql(
        ["valuation_amount", "currency"],
        is_business_key=False,
        sort_alphabetically=True,
    )
    assert "SHA2(" in sql
    assert ", 256)" in sql
    assert "REPEAT('0', 64)" not in sql
    assert "REPEAT('f', 64)" not in sql
    # Alphabetical order: currency, then valuation_amount
    curr_idx = sql.find("currency")
    val_idx = sql.find("valuation_amount")
    assert curr_idx < val_idx


def test_build_databricks_algorithm_a5_sha256_sql_bkcc_literal() -> None:
    """Verify BKCC literal prefix in Databricks hash key generation."""
    sql = build_databricks_algorithm_a5_sha256_sql(
        ["instrument_id"],
        is_business_key=True,
        bkcc_literal="SEDOL",
    )
    assert "'5:SEDOL'" in sql
    assert "instrument_id" in sql


def test_transpile_duckdb_to_databricks_sql() -> None:
    """Verify sqlglot transpilation translates functions and appends USING DELTA."""
    duck_sql = (
        "CREATE TABLE raw_vault.hub_test (\n"
        "    hk STRING NOT NULL PRIMARY KEY,\n"
        "    id STRING NOT NULL\n"
        ");\n\n"
        "SELECT sha256(x) AS h FROM raw_vault.hub_test;"
    )
    db_sql = transpile_duckdb_to_databricks_sql(duck_sql)
    assert "USING DELTA" in db_sql
    assert "SHA2(x, 256)" in db_sql


def test_compile_databricks_hub_ddl_and_zero_keys(
    pe_canonical_ir: CanonicalIR,
) -> None:
    """Verify Delta DDL and zero-key MERGE generation for Hub."""
    hub = pe_canonical_ir.hubs[0]
    ddl_and_merge = compile_databricks_hub_ddl_and_zero_keys(hub)

    assert f"CREATE TABLE IF NOT EXISTS opendv_catalog.raw_vault.{hub.name}" in ddl_and_merge
    assert "USING DELTA" in ddl_and_merge
    assert f"CLUSTER BY ({hub.hk_column.name}, load_dts)" in ddl_and_merge
    assert "TBLPROPERTIES ('delta.appendOnly' = 'true')" in ddl_and_merge
    assert f"MERGE INTO opendv_catalog.raw_vault.{hub.name} AS target" in ddl_and_merge
    assert "WHEN NOT MATCHED THEN" in ddl_and_merge
    assert "INSERT" in ddl_and_merge
    # Zero key tokens
    assert "'0000000000000000000000000000000000000000000000000000000000000000'" in ddl_and_merge
    assert "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'" in ddl_and_merge


def test_compile_databricks_link_ddl(pe_canonical_ir: CanonicalIR) -> None:
    """Verify Delta DDL generation for Link."""
    link = pe_canonical_ir.links[0]
    ddl = compile_databricks_link_ddl(link)
    assert f"CREATE TABLE IF NOT EXISTS opendv_catalog.raw_vault.{link.name}" in ddl
    assert "USING DELTA" in ddl
    assert f"CLUSTER BY ({link.hk_column.name}, load_dts)" in ddl
    assert "TBLPROPERTIES ('delta.appendOnly' = 'true')" in ddl
    assert link.hk_column.name in ddl


def test_compile_databricks_satellite_ddl(pe_canonical_ir: CanonicalIR) -> None:
    """Verify Delta DDL generation for Satellite."""
    sat = pe_canonical_ir.satellites[0]
    ddl = compile_databricks_satellite_ddl(sat)
    assert f"CREATE TABLE IF NOT EXISTS opendv_catalog.raw_vault.{sat.name}" in ddl
    assert "USING DELTA" in ddl
    assert f"CLUSTER BY ({sat.parent_hk_column.name}, load_dts)" in ddl
    assert "TBLPROPERTIES ('delta.appendOnly' = 'true')" in ddl
    assert sat.parent_hk_column.name in ddl
    assert sat.hash_diff_column.name in ddl


def test_compile_databricks_zone_2_merge(pe_canonical_ir: CanonicalIR) -> None:
    """Verify idempotent MERGE INTO statements for Hubs, Links, and Satellites."""
    merge_sql = compile_databricks_zone_2_merge(pe_canonical_ir)
    assert "ZONE 2: IDEMPOTENT DELTA MERGE INTO RAW VAULT LOADS" in merge_sql

    for hub in pe_canonical_ir.hubs:
        assert f"MERGE INTO opendv_catalog.raw_vault.{hub.name} AS target" in merge_sql
        assert f"ON target.{hub.hk_column.name} = source.{hub.hk_column.name}" in merge_sql
        assert "WHEN NOT MATCHED THEN" in merge_sql

    for link in pe_canonical_ir.links:
        assert f"MERGE INTO opendv_catalog.raw_vault.{link.name} AS target" in merge_sql
        assert f"ON target.{link.hk_column.name} = source.{link.hk_column.name}" in merge_sql

    for sat in pe_canonical_ir.satellites:
        assert f"MERGE INTO opendv_catalog.raw_vault.{sat.name} AS target" in merge_sql
        on_clause = (
            f"ON target.{sat.parent_hk_column.name} = source.{sat.parent_hk_column.name} "
            f"AND target.load_dts = source.load_dts"
        )
        assert on_clause in merge_sql


def test_compile_canonical_ir_to_databricks(pe_canonical_ir: CanonicalIR) -> None:
    """Verify end-to-end direct compilation of CanonicalIR into CompiledDatabricksBundle."""
    bundle = compile_canonical_ir_to_databricks(pe_canonical_ir)
    assert isinstance(bundle, CompiledDatabricksBundle)
    assert bundle.dialect == "databricks"
    assert bundle.model_name == pe_canonical_ir.model_name
    assert "USING DELTA" in bundle.zone_0_sql
    assert "TBLPROPERTIES ('delta.appendOnly' = 'true')" in bundle.zone_0_sql
    assert "MERGE INTO" in bundle.zone_2_sql
    assert len(bundle.full_sql_bundle) > 500


def test_transpile_bundle_to_databricks(pe_canonical_ir: CanonicalIR) -> None:
    """Verify transpiling an existing Compiled4ZoneBundle to CompiledDatabricksBundle."""
    duck_bundle = Compiled4ZoneBundle(
        model_name="pe_test",
        zone_0_sql="CREATE TABLE raw_vault.hub_test (hk VARCHAR PRIMARY KEY);",
        zone_1_sql="SELECT sha256(id) FROM stg_test;",
        zone_2_sql="INSERT INTO raw_vault.hub_test SELECT * FROM stg_test;",
        zone_3_sql="SELECT count(*) FROM raw_vault.hub_test;",
        zone_4_query_d10_sql="SELECT 1;",
        zone_4_query_d10b_sql="SELECT 2;",
        canonical_ir=pe_canonical_ir,
    )

    db_bundle = transpile_bundle_to_databricks(duck_bundle)
    assert isinstance(db_bundle, CompiledDatabricksBundle)
    assert db_bundle.dialect == "databricks"
    assert "USING DELTA;" in db_bundle.zone_0_sql
    assert "MERGE INTO" in db_bundle.zone_2_sql
    assert "SHA2(" in db_bundle.zone_1_sql
