"""Unit tests for CatalogSourceAdapter SPI and enriched catalog metadata models (WS2A.1)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    ForeignKeyHint,
    NamespaceDescriptor,
    ResolvedTableSource,
    SourceCapabilities,
    TableSchemaDescriptor,
    TableSummary,
    TabularProfileSummary,
)
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
    TableSelectionSpec,
)
from opendv_sources.spi.catalog import CatalogSourceAdapter


def test_namespace_and_table_ref_construction() -> None:
    """Verify NamespaceRef and TableRef field assignment and canonical naming."""
    ns = NamespaceRef(catalog="am_prod", schema_name="ibor")
    assert ns.catalog == "am_prod"
    assert ns.schema_name == "ibor"

    tbl = TableRef(table_name="positions", namespace=ns)
    assert tbl.table_name == "positions"
    assert tbl.namespace == ns
    assert tbl.canonical_name == "am_prod.ibor.positions"

    simple_tbl = TableRef(table_name="trades")
    assert simple_tbl.canonical_name == "trades"


def test_table_selection_spec_modes() -> None:
    """Verify TableSelectionSpec defaults and explicit / pattern modes."""
    default_sel = TableSelectionSpec()
    assert default_sel.mode == "ALL_USER_TABLES"
    assert default_sel.tables == []
    assert default_sel.include_pattern is None
    assert default_sel.exclude_pattern is None

    explicit_sel = TableSelectionSpec(mode="EXPLICIT", tables=["orders", "order_items"])
    assert explicit_sel.mode == "EXPLICIT"
    assert explicit_sel.tables == ["orders", "order_items"]

    pattern_sel = TableSelectionSpec(
        mode="PATTERN",
        include_pattern=r"^fact_.*",
        exclude_pattern=r".*_tmp$",
    )
    assert pattern_sel.mode == "PATTERN"
    assert pattern_sel.include_pattern == r"^fact_.*"
    assert pattern_sel.exclude_pattern == r".*_tmp$"


def test_sampling_policy_defaults_and_validation() -> None:
    """Verify SamplingPolicy defaults and valid bounds."""
    policy = SamplingPolicy()
    assert policy.max_sandbox_rows == 10000
    assert policy.strategy == "DETERMINISTIC_LIMIT"
    assert policy.seed is None

    bernoulli = SamplingPolicy(
        max_sandbox_rows=5000,
        strategy="TABLESAMPLE_BERNOULLI",
        seed=42,
    )
    assert bernoulli.max_sandbox_rows == 5000
    assert bernoulli.strategy == "TABLESAMPLE_BERNOULLI"
    assert bernoulli.seed == 42

    with pytest.raises(ValidationError):
        SamplingPolicy(max_sandbox_rows=0)


def test_source_spec_with_catalog_extensions() -> None:
    """Verify SourceSpec retains backward compatibility while supporting catalog fields."""
    ns = NamespaceRef(catalog="lakehouse", schema_name="analytics")
    sel = TableSelectionSpec(mode="EXPLICIT", tables=["holdings"])
    samp = SamplingPolicy(max_sandbox_rows=2000)

    spec = SourceSpec(
        adapter="databricks",
        uri="databricks://my-host/lakehouse/analytics",
        namespace=ns,
        table_selection=sel,
        sampling_policy=samp,
    )

    assert spec.adapter == "databricks"
    assert spec.namespace == ns
    assert spec.table_selection == sel
    assert spec.sampling_policy == samp


def test_enriched_column_descriptor_and_foreign_keys() -> None:
    """Verify ColumnDescriptor primary key, foreign key, and governance tags."""
    fk = ForeignKeyHint(
        target_table="dim_security",
        target_column="security_id",
        constraint_name="fk_positions_security",
    )
    assert fk.target_table == "dim_security"
    assert fk.target_column == "security_id"
    assert fk.constraint_name == "fk_positions_security"

    col_pk = ColumnDescriptor(
        name="position_id",
        physical_type="BIGINT",
        nullable=False,
        is_primary_key=True,
        primary_key_ordinal=1,
        description="Synthetic primary key",
    )
    assert col_pk.is_primary_key is True
    assert col_pk.primary_key_ordinal == 1
    assert col_pk.foreign_key_ref is None

    col_fk = ColumnDescriptor(
        name="sec_id",
        physical_type="BIGINT",
        nullable=True,
        foreign_key_ref=fk,
        governance_tags={"classification": "confidential"},
    )
    assert col_fk.is_primary_key is False
    assert col_fk.foreign_key_ref == fk
    assert col_fk.governance_tags == {"classification": "confidential"}


def test_enriched_source_capabilities() -> None:
    """Verify SourceCapabilities execution modes and catalog flags."""
    caps = SourceCapabilities(
        execution_mode="REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
        supports_multi_table=True,
        supports_catalog_hierarchy=True,
        supports_foreign_keys=True,
        supports_governance_tags=True,
        supports_time_travel=True,
    )
    assert caps.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert caps.supports_multi_table is True
    assert caps.supports_catalog_hierarchy is True
    assert caps.supports_foreign_keys is True
    assert caps.supports_governance_tags is True
    assert caps.supports_time_travel is True
    assert caps.supports_streaming is False
    assert caps.supports_drift_detection is True


def test_table_summary_and_schema_descriptor() -> None:
    """Verify TableSummary, TableSchemaDescriptor, and ResolvedTableSource models."""
    ns = NamespaceRef(schema_name="main")
    tbl_ref = TableRef(table_name="securities", namespace=ns)

    summary = TableSummary(
        table_name=tbl_ref.table_name,
        namespace=tbl_ref.namespace,
        table_type="BASE_TABLE",
        row_count_estimate=500,
        column_count=4,
        comment="Securities master",
    )
    assert summary.table_name == "securities"
    assert summary.row_count_estimate == 500

    col1 = ColumnDescriptor(name="ticker", physical_type="VARCHAR", is_primary_key=True)
    col2 = ColumnDescriptor(name="cik", physical_type="VARCHAR", nullable=False)

    schema_desc = TableSchemaDescriptor(
        table_name=tbl_ref.table_name,
        namespace=ns,
        columns=[col1, col2],
        primary_key_columns=["ticker"],
        fingerprint="sha256_mock_digest",
        comment="Securities master table",
    )
    assert schema_desc.primary_key_columns == ["ticker"]
    assert len(schema_desc.columns) == 2

    source_spec = SourceSpec(
        adapter="sqlite", uri="data/am.sqlite", options={"table": "securities"}
    )
    resolved = ResolvedTableSource(
        source_system="am_sec",
        table_name="securities",
        qualified_name="am_sec.securities",
        schema_descriptor=schema_desc,
        spec=source_spec,
        execution_mode="DIRECT_DUCKDB_ATTACH",
    )
    assert resolved.qualified_name == "am_sec.securities"
    assert resolved.execution_mode == "DIRECT_DUCKDB_ATTACH"

    profile = TabularProfileSummary(
        table_name="securities",
        namespace=ns,
        total_row_count=500,
        column_stats={"ticker": {"null_count": 0, "approx_distinct": 500}},
    )
    assert profile.total_row_count == 500
    assert "ticker" in profile.column_stats


class DummyConnectorConfig(BaseModel):
    """Example configuration schema for connector UI."""

    catalog_name: str = Field(default="default")
    timeout_seconds: int = Field(default=30)


class MockCatalogAdapter(CatalogSourceAdapter):
    """Concrete mock implementation verifying CatalogSourceAdapter protocol."""

    @property
    def adapter_name(self) -> str:
        return "mock_catalog"

    def list_namespaces(self, spec: SourceSpec) -> list[NamespaceDescriptor]:
        return [NamespaceDescriptor(catalog="cat1", schema_name="public")]

    def list_tables(
        self,
        spec: SourceSpec,
        namespace: NamespaceRef | None = None,
    ) -> list[TableSummary]:
        return [TableSummary(table_name="orders", namespace=namespace)]

    def inspect_table(self, spec: SourceSpec, table: TableRef) -> TableSchemaDescriptor:
        return TableSchemaDescriptor(
            table_name=table.table_name,
            namespace=table.namespace,
            columns=[
                ColumnDescriptor(
                    name="id",
                    physical_type="INT",
                    is_primary_key=True,
                    primary_key_ordinal=1,
                )
            ],
            primary_key_columns=["id"],
            fingerprint="mock_digest_123",
        )

    def expand_feed(
        self,
        spec: SourceSpec,
        table: TableRef | None = None,
    ) -> list[ResolvedTableSource]:
        table_ref = table or TableRef(table_name="orders")
        schema = self.inspect_table(spec, table_ref)
        return [
            ResolvedTableSource(
                source_system="mock_sys",
                table_name=table_ref.table_name,
                qualified_name=f"mock_sys.{table_ref.table_name}",
                schema_descriptor=schema,
                spec=spec,
                execution_mode="DIRECT_DUCKDB_ATTACH",
            )
        ]

    def profile_pushdown(
        self,
        spec: SourceSpec,
        table: TableRef,
        policy: SamplingPolicy,
    ) -> TabularProfileSummary:
        return TabularProfileSummary(
            table_name=table.table_name,
            namespace=table.namespace,
            total_row_count=100,
            column_stats={"id": {"null_count": 0}},
        )

    @classmethod
    def config_schema(cls) -> type[BaseModel] | None:
        return DummyConnectorConfig


def test_catalog_source_adapter_protocol() -> None:
    """Verify concrete subclass satisfies CatalogSourceAdapter interface."""
    adapter = MockCatalogAdapter()
    assert adapter.adapter_name == "mock_catalog"
    assert adapter.config_schema() is DummyConnectorConfig

    spec = SourceSpec(adapter="mock_catalog")
    namespaces = adapter.list_namespaces(spec)
    assert len(namespaces) == 1
    assert namespaces[0].catalog == "cat1"

    tables = adapter.list_tables(spec, NamespaceRef(catalog="cat1", schema_name="public"))
    assert len(tables) == 1
    assert tables[0].table_name == "orders"

    tbl_ref = TableRef(table_name="orders")
    schema = adapter.inspect_table(spec, tbl_ref)
    assert schema.primary_key_columns == ["id"]

    expanded = adapter.expand_feed(spec, tbl_ref)
    assert len(expanded) == 1
    assert expanded[0].qualified_name == "mock_sys.orders"

    profile = adapter.profile_pushdown(spec, tbl_ref, SamplingPolicy())
    assert profile.total_row_count == 100
