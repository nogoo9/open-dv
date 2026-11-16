"""Unit tests for DatabricksPushdownProfiler.

Validates batched remote SQL aggregate generation, pushdown profiling execution,
approximate distinct, null counts, min/max lengths, candidate BKCC regex match ratios,
and bounded PyArrow record batch sampling.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from opendv_sources.adapters.databricks_profiler import (
    DatabricksPushdownProfiler,
)
from opendv_sources.models.descriptors import ColumnDescriptor, TabularProfileSummary
from opendv_sources.models.specs import NamespaceRef, SamplingPolicy, TableRef
from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator


@pytest.fixture
def uc_emulator() -> DuckDBUnityCatalogEmulator:
    """Fixture providing local DuckDB Unity Catalog emulator seeded with IBOR data."""
    emulator = DuckDBUnityCatalogEmulator(default_catalog="am_prod")
    yield emulator
    emulator.close()


@pytest.fixture
def profiler() -> DatabricksPushdownProfiler:
    """Fixture providing DatabricksPushdownProfiler instance."""
    return DatabricksPushdownProfiler()


def test_generate_pushdown_query(profiler: DatabricksPushdownProfiler) -> None:
    """Verify single batched remote aggregate profiling query structure."""
    table = TableRef(
        table_name="security_master",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    columns = [
        ColumnDescriptor(name="instrument_id", physical_type="VARCHAR"),
        ColumnDescriptor(name="isin", physical_type="VARCHAR"),
        ColumnDescriptor(name="quantity", physical_type="DOUBLE"),
    ]

    sql = profiler.generate_pushdown_query(table, columns)
    assert "COUNT(*) AS __total_rows__" in sql
    assert "`am_prod`.`ibor`.`security_master`" in sql
    # APPROX_COUNT_DISTINCT on each column
    assert "APPROX_COUNT_DISTINCT(`instrument_id`)" in sql
    assert "APPROX_COUNT_DISTINCT(`isin`)" in sql
    assert "APPROX_COUNT_DISTINCT(`quantity`)" in sql
    # COUNT_IF on nulls
    assert "COUNT_IF(`instrument_id` IS NULL)" in sql
    assert "COUNT_IF(`quantity` IS NULL)" in sql
    # String min/max length on VARCHAR
    assert "MIN(LENGTH(CAST(`isin` AS STRING)))" in sql
    assert "MAX(LENGTH(CAST(`isin` AS STRING)))" in sql
    # DOUBLE column should NOT have string min/max length or regex
    assert "MIN(LENGTH(CAST(`quantity` AS STRING)))" not in sql
    # BKCC regex patterns on text columns
    assert "REGEXP_LIKE(CAST(`isin` AS STRING)" in sql
    assert "`isin__regex_isin`" in sql
    assert "`isin__regex_cusip`" in sql


def test_profile_table_security_master(
    profiler: DatabricksPushdownProfiler, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify pushdown profiling execution on security_master."""
    conn = uc_emulator.connect()
    cursor = conn.cursor()

    table = TableRef(
        table_name="security_master",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    columns = [
        ColumnDescriptor(name="instrument_id", physical_type="VARCHAR"),
        ColumnDescriptor(name="isin", physical_type="VARCHAR"),
        ColumnDescriptor(name="cusip", physical_type="VARCHAR"),
        ColumnDescriptor(name="ticker", physical_type="VARCHAR"),
    ]

    summary = profiler.profile_table(cursor, table, columns)
    assert isinstance(summary, TabularProfileSummary)
    assert summary.table_name == "security_master"
    assert summary.total_row_count == 5
    assert summary.profiling_strategy_used == "PUSHDOWN_SQL"

    # ISIN assertions
    isin_stats = summary.column_stats["isin"]
    assert isin_stats["approx_distinct_count"] == 5
    assert isin_stats["null_count"] == 0
    assert isin_stats["null_ratio"] == 0.0
    assert isin_stats["min_length"] == 12
    assert isin_stats["max_length"] == 12
    assert isin_stats["regex_match_ratios"]["isin"] == 1.0
    assert isin_stats["regex_match_ratios"]["cusip"] == 0.0

    # CUSIP assertions
    cusip_stats = summary.column_stats["cusip"]
    assert cusip_stats["min_length"] == 9
    assert cusip_stats["max_length"] == 9
    assert cusip_stats["regex_match_ratios"]["cusip"] == 1.0


def test_profile_table_positions_with_nulls_and_numerics(
    profiler: DatabricksPushdownProfiler, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify profiling on table with mixed data types and foreign keys."""
    conn = uc_emulator.connect()
    cursor = conn.cursor()

    table = TableRef(
        table_name="positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    columns = [
        ColumnDescriptor(name="position_id", physical_type="VARCHAR"),
        ColumnDescriptor(name="instrument_id", physical_type="VARCHAR"),
        ColumnDescriptor(name="quantity", physical_type="DOUBLE"),
        ColumnDescriptor(name="market_value", physical_type="DOUBLE"),
        ColumnDescriptor(name="custodian_account_no", physical_type="VARCHAR"),
    ]

    summary = profiler.profile_table(cursor, table, columns)
    assert summary.total_row_count == 5

    qty_stats = summary.column_stats["quantity"]
    assert qty_stats["null_count"] == 0
    assert qty_stats["approx_distinct_count"] >= 3
    # Numeric column has no min_length / regex
    assert qty_stats["min_length"] is None
    assert qty_stats["regex_match_ratios"] == {}

    cust_stats = summary.column_stats["custodian_account_no"]
    assert cust_stats["null_count"] == 0
    assert cust_stats["min_length"] > 0


def test_profile_empty_table(
    profiler: DatabricksPushdownProfiler, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify pushdown profiling handles 0-row tables without division by zero."""
    uc_emulator.register_table(
        catalog="am_prod",
        schema="ibor",
        table="empty_table",
        columns=[("col_a", "VARCHAR", True, None), ("col_b", "BIGINT", True, None)],
        rows=[],
    )
    conn = uc_emulator.connect()
    cursor = conn.cursor()

    table = TableRef(
        table_name="empty_table",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    columns = [
        ColumnDescriptor(name="col_a", physical_type="VARCHAR"),
        ColumnDescriptor(name="col_b", physical_type="BIGINT"),
    ]

    summary = profiler.profile_table(cursor, table, columns)
    assert summary.total_row_count == 0
    assert summary.column_stats["col_a"]["null_ratio"] == 0.0
    assert summary.column_stats["col_a"]["approx_distinct_count"] == 0


def test_fetch_arrow_sample_bounds_and_structure(
    profiler: DatabricksPushdownProfiler, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Verify bounded Arrow streaming respects max_sandbox_rows and yields valid PyArrow table."""
    conn = uc_emulator.connect()
    cursor = conn.cursor()

    table = TableRef(
        table_name="positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )

    # 1. Bounded to 2 rows
    policy_2 = SamplingPolicy(max_sandbox_rows=2, strategy="DETERMINISTIC_LIMIT")
    arrow_tbl_2 = profiler.fetch_arrow_sample(cursor, table, policy_2)
    assert isinstance(arrow_tbl_2, pa.Table)
    assert len(arrow_tbl_2) == 2
    assert "position_id" in arrow_tbl_2.column_names

    # 2. Bounded to 10 rows (table has 5)
    policy_10 = SamplingPolicy(max_sandbox_rows=10, strategy="DETERMINISTIC_LIMIT")
    arrow_tbl_5 = profiler.fetch_arrow_sample(cursor, table, policy_10)
    assert len(arrow_tbl_5) == 5

    # 3. Strategy TABLESAMPLE_BERNOULLI
    policy_bern = SamplingPolicy(max_sandbox_rows=5, strategy="TABLESAMPLE_BERNOULLI")
    arrow_bern = profiler.fetch_arrow_sample(cursor, table, policy_bern)
    assert isinstance(arrow_bern, pa.Table)
    assert len(arrow_bern) <= 5
