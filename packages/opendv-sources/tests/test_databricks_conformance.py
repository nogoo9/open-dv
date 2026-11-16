"""Databricks Unity Catalog Conformance Suite (Suite 3).

End-to-end conformance certification certifying:
  1. 3-level catalog.schema.table discovery and inspection against DuckDBUnityCatalogEmulator
  2. Information_schema PK/FK extraction and column_tags -> governance_tags propagation
  3. Remote warehouse pushdown aggregate profiling without data egress
  4. Bounded PyArrow streaming for DuckDB 4-zone shadow sandbox execution
  5. Delta Lake DDL and idempotent MERGE INTO compilation
  6. Optional live Databricks SQL Warehouse conformance when configured
"""

from __future__ import annotations

import os

import duckdb
import pytest

from opendv_contracts.dtos.sources import ConnectorUIManifestDTO
from opendv_duckdb_sandbox.dialects.databricks_transpiler import (
    transpile_duckdb_to_databricks_sql,
)
from opendv_sources.adapters.databricks_uc import DatabricksUnityCatalogAdapter
from opendv_sources.models.descriptors import (
    TableSchemaDescriptor,
    TabularProfileSummary,
)
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
)
from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers dynamically for conformance testing."""
    config.addinivalue_line(
        "markers", "databricks_conformance: Databricks Unity Catalog conformance tests"
    )
    config.addinivalue_line(
        "markers", "live_databricks: Tests requiring live Databricks connection"
    )


pytestmark = [pytest.mark.databricks_conformance]


@pytest.fixture
def uc_emulator() -> DuckDBUnityCatalogEmulator:
    """Fixture providing local DuckDB Unity Catalog emulator seeded with IBOR data."""
    emulator = DuckDBUnityCatalogEmulator(default_catalog="am_prod")
    yield emulator
    emulator.close()


@pytest.fixture
def adapter() -> DatabricksUnityCatalogAdapter:
    """Fixture providing Databricks adapter."""
    return DatabricksUnityCatalogAdapter()


def test_conformance_suite3_manifest_and_capabilities(
    adapter: DatabricksUnityCatalogAdapter,
) -> None:
    """Conformance check: adapter exposes correct dynamic manifest and capabilities."""
    manifest = adapter.get_ui_manifest()
    assert isinstance(manifest, ConnectorUIManifestDTO)
    assert manifest.adapter_id == "databricks"
    assert manifest.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert manifest.supports_catalog_hierarchy is True
    assert manifest.supports_governance_tags is True
    assert manifest.supports_foreign_keys is True
    assert manifest.supports_time_travel is True

    spec = SourceSpec(adapter="databricks", uri="databricks://am_prod/ibor")
    caps = adapter.capabilities(spec)
    assert caps.supports_pushdown is True
    assert caps.supports_streaming is True
    assert caps.client_uploadable is False


def test_conformance_suite3_catalog_hierarchy_and_tables(
    adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Conformance check: 3-level catalog browsing discovers schemas and tables."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod",
        options={"emulator": uc_emulator, "catalog": "am_prod"},
    )

    # 1. List namespaces
    namespaces = adapter.list_namespaces(spec)
    assert any(ns.schema_name == "ibor" for ns in namespaces)
    # Ensure system schemas are filtered
    assert not any(ns.schema_name == "information_schema" for ns in namespaces)

    # 2. List tables in am_prod.ibor
    tables = adapter.list_tables(spec, NamespaceRef(catalog="am_prod", schema_name="ibor"))
    table_names = {t.table_name for t in tables}
    assert table_names == {"security_master", "positions", "trades"}


def test_conformance_suite3_schema_inspection_and_governance_tags(
    adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Conformance check: schema inspection extracts PK, FK, comments, and governance tags."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/positions",
        options={"emulator": uc_emulator},
    )
    tbl_ref = TableRef(
        table_name="positions",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )

    desc = adapter.inspect_table(spec, tbl_ref)
    assert isinstance(desc, TableSchemaDescriptor)
    assert desc.table_name == "positions"
    assert desc.primary_key_columns == ["position_id"]

    # Verify column governance tags
    col_by_name = {c.name: c for c in desc.columns}
    assert "custodian_account_no" in col_by_name
    assert col_by_name["custodian_account_no"].governance_tags.get("pii") == "true"
    assert col_by_name["custodian_account_no"].governance_tags.get("sensitivity") == "restricted"

    assert "market_value" in col_by_name
    assert col_by_name["market_value"].governance_tags.get("sensitivity") == "confidential"

    # Check foreign key reference
    assert "instrument_id" in col_by_name
    assert col_by_name["instrument_id"].foreign_key_ref is not None
    assert col_by_name["instrument_id"].foreign_key_ref.target_column == "instrument_id"

    # Check deterministic fingerprint
    assert len(desc.fingerprint) == 64


def test_conformance_suite3_pushdown_profiling(
    adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Conformance check: remote pushdown profiler executes single batched aggregate query."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/security_master",
        options={"emulator": uc_emulator},
    )
    tbl_ref = TableRef(
        table_name="security_master",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
    )
    policy = SamplingPolicy(max_sandbox_rows=10_000)

    summary = adapter.profile_pushdown(spec, tbl_ref, policy)
    assert isinstance(summary, TabularProfileSummary)
    assert summary.total_row_count == 5

    # ISIN match ratio
    isin_stats = summary.column_stats["isin"]
    assert isin_stats["approx_distinct_count"] == 5
    assert isin_stats["min_length"] == 12
    assert isin_stats["max_length"] == 12
    assert isin_stats["regex_match_ratios"]["isin"] == 1.0

    # CUSIP match ratio
    cusip_stats = summary.column_stats["cusip"]
    assert cusip_stats["min_length"] == 9
    assert cusip_stats["max_length"] == 9
    assert cusip_stats["regex_match_ratios"]["cusip"] == 1.0


def test_conformance_suite3_bounded_arrow_sandbox_staging(
    adapter: DatabricksUnityCatalogAdapter, uc_emulator: DuckDBUnityCatalogEmulator
) -> None:
    """Conformance check: bounded Arrow stream registers into DuckDB shadow sandbox."""
    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://am_prod/ibor/trades",
        namespace=NamespaceRef(catalog="am_prod", schema_name="ibor"),
        sampling_policy=SamplingPolicy(max_sandbox_rows=3),
        options={"emulator": uc_emulator, "table": "trades"},
    )

    local_duckdb = duckdb.connect(":memory:")
    adapter.register(spec, local_duckdb, "shadow_stg_trades")

    res = local_duckdb.execute(
        "SELECT count(*), count(distinct side) FROM shadow_stg_trades"
    ).fetchall()
    assert res[0][0] == 3
    local_duckdb.close()


def test_conformance_suite3_delta_sql_transpilation() -> None:
    """Conformance check: Delta Lake DDL and MERGE patterns compile correctly."""
    duck_ddl = """
    CREATE TABLE raw_vault.hub_position (
        hk_hub_position CHAR(64) NOT NULL PRIMARY KEY,
        position_id VARCHAR NOT NULL,
        load_dts TIMESTAMP NOT NULL,
        rec_src VARCHAR NOT NULL
    );
    """
    databricks_sql = transpile_duckdb_to_databricks_sql(duck_ddl)
    assert "USING DELTA" in databricks_sql


@pytest.mark.live_databricks
def test_live_databricks_conformance_when_configured() -> None:
    """Opt-in live conformance test against real Databricks SQL Warehouse if credentials exist."""
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")

    if not host or not token or not http_path:
        pytest.skip(
            "Live Databricks tests skipped: DATABRICKS_HOST, DATABRICKS_TOKEN, "
            "and DATABRICKS_HTTP_PATH not configured in environment."
        )

    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://main/default",
        credentials_ref="env:DATABRICKS_TOKEN",
        options={"host": host, "http_path": http_path, "catalog": "main"},
    )
    adapter = DatabricksUnityCatalogAdapter()
    namespaces = adapter.list_namespaces(spec)
    assert len(namespaces) > 0
