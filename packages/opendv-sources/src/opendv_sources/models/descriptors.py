"""Metadata models describing resolved data sources and schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_sources.models.specs import NamespaceRef, SourceSpec, TableRef


class ForeignKeyHint(BaseContractModel):
    """Foreign key relationship hint between source tables."""

    target_table: str = Field(..., description="Target referenced table name")
    target_column: str = Field(default="", description="Target referenced column name")
    target_namespace: NamespaceRef | None = Field(
        default=None, description="Optional target table namespace"
    )
    constraint_name: str | None = Field(
        default=None, description="Optional foreign key constraint identifier"
    )
    source_column: str | None = Field(
        default=None, description="Referencing source table column name"
    )
    source_columns: list[str] = Field(
        default_factory=list, description="Ordered source column names for composite foreign keys"
    )
    target_columns: list[str] = Field(
        default_factory=list, description="Ordered target column names for composite foreign keys"
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_columns(cls, data: Any) -> Any:
        if isinstance(data, dict):
            tgt_cols = data.get("target_columns")
            tgt_col = data.get("target_column")
            if tgt_cols and not tgt_col:
                data["target_column"] = tgt_cols[0]
            elif tgt_col and not tgt_cols:
                data["target_columns"] = [tgt_col]

            src_cols = data.get("source_columns")
            src_col = data.get("source_column")
            if src_cols and not src_col:
                data["source_column"] = src_cols[0]
            elif src_col and not src_cols:
                data["source_columns"] = [src_col]
        return data


class ColumnDescriptor(BaseContractModel):
    """Metadata describing a single table column."""

    name: str = Field(..., description="Column identifier")
    physical_type: str = Field(..., description="Physical storage or SQL type")
    nullable: bool = Field(default=True, description="Nullability constraint")
    is_primary_key: bool = Field(
        default=False, description="Flag indicating if column participates in primary key"
    )
    primary_key_ordinal: int | None = Field(
        default=None, description="1-based ordinal position in composite primary key"
    )
    foreign_key_ref: ForeignKeyHint | None = Field(
        default=None, description="Optional foreign key relationship metadata"
    )
    description: str | None = Field(
        default=None, description="Column comment, documentation, or semantic description"
    )
    governance_tags: dict[str, str] = Field(
        default_factory=dict,
        description="Governance classification tags (e.g. {'classification': 'pii'})",
    )


class SourceCapabilities(BaseContractModel):
    """Capabilities exposed by a source adapter."""

    execution_mode: Literal[
        "DIRECT_DUCKDB_ATTACH", "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE", "INLINE_CAS_STAGED"
    ] = Field(
        default="DIRECT_DUCKDB_ATTACH",
        description="Execution mode for profiling and sandbox loading",
    )
    supports_multi_table: bool = Field(
        default=False, description="Indicates multi-table catalog discovery capability"
    )
    supports_catalog_hierarchy: bool = Field(
        default=False, description="Indicates 3-level catalog.schema.table discovery"
    )
    supports_foreign_keys: bool = Field(
        default=False, description="Indicates foreign key constraint extraction"
    )
    supports_governance_tags: bool = Field(
        default=False, description="Indicates ingestion of governance tags"
    )
    supports_time_travel: bool = Field(
        default=False, description="Indicates support for version_as_of / time travel"
    )
    supports_streaming: bool = Field(default=False)
    supports_pushdown: bool = Field(default=False)
    supports_drift_detection: bool = Field(default=True)
    client_uploadable: bool = Field(default=False)
    server_resolved: bool = Field(default=True)


class NamespaceDescriptor(BaseContractModel):
    """Descriptive metadata for a database catalog or schema namespace."""

    catalog: str | None = Field(default=None, description="Optional 3-level catalog name")
    schema_name: str = Field(..., description="Schema, database, or namespace name")
    description: str | None = Field(default=None, description="Namespace description or comment")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Additional namespace properties or tags"
    )


class TableSummary(BaseContractModel):
    """Lightweight summary of a catalog table for discovery listings."""

    table_name: str = Field(..., description="Table name")
    namespace: NamespaceRef | None = Field(default=None, description="Table namespace")
    table_type: Literal["MANAGED", "EXTERNAL", "VIEW", "BASE_TABLE"] = Field(
        default="BASE_TABLE", description="Relational table type"
    )
    row_count_estimate: int | None = Field(default=None, description="Estimated row count")
    column_count: int | None = Field(default=None, description="Total column count")
    comment: str | None = Field(default=None, description="Table description or comment")


class TableSchemaDescriptor(BaseContractModel):
    """Comprehensive schema metadata for a single inspected catalog table."""

    table_name: str = Field(..., description="Table name")
    namespace: NamespaceRef | None = Field(default=None, description="Optional namespace reference")
    columns: list[ColumnDescriptor] = Field(
        default_factory=list, description="Ordered list of column descriptors"
    )
    primary_key_columns: list[str] = Field(
        default_factory=list, description="Ordered column names forming the primary key"
    )
    foreign_keys: list[ForeignKeyHint] = Field(
        default_factory=list, description="Foreign key relationships declared on this table"
    )
    row_count_estimate: int | None = Field(default=None, description="Estimated or exact row count")
    fingerprint: str = Field(
        ..., description="Deterministic schema digest or snapshot ID for CAS provenance"
    )
    governance_tags: dict[str, str] = Field(
        default_factory=dict, description="Table-level governance tags"
    )
    comment: str | None = Field(default=None, description="Table comment or documentation")


class ResolvedTableSource(BaseContractModel):
    """Expanded concrete table descriptor ready for pipeline triage and sandbox ingestion."""

    source_system: str = Field(..., description="Originating source system identifier")
    table_name: str = Field(..., description="Table name within source system")
    qualified_name: str = Field(
        ..., description="Normalized identifier (e.g. source_system.table_name)"
    )
    schema_descriptor: TableSchemaDescriptor = Field(
        ..., description="Full schema descriptor for table"
    )
    spec: SourceSpec = Field(..., description="Concrete table-targeted SourceSpec")
    physical_location: str | None = Field(
        default=None, description="Underlying physical URI, table path, or connection string"
    )
    execution_mode: Literal[
        "DIRECT_DUCKDB_ATTACH", "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE", "INLINE_CAS_STAGED"
    ] = Field(
        default="DIRECT_DUCKDB_ATTACH",
        description="Target execution and profiling mode",
    )


class TabularProfileSummary(BaseContractModel):
    """Pushdown profiling statistics summary for a table."""

    table_name: str = Field(..., description="Target table name")
    namespace: NamespaceRef | None = Field(default=None, description="Optional namespace")
    total_row_count: int = Field(default=0, ge=0, description="Total row count")
    column_stats: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Column profiling statistics (null_count, distinct_count, min, max, etc.)",
    )
    profile_timestamp: str | None = Field(
        default=None, description="ISO-8601 timestamp of profiling execution"
    )
    profiling_strategy_used: str = Field(
        default="PUSHDOWN_SQL", description="Profiling strategy executed"
    )


class SourceDescriptor(BaseContractModel):
    """Comprehensive descriptive metadata for a resolved data source."""

    adapter: str = Field(..., description="Resolved adapter name")
    format: str = Field(..., description="Resolved format name")
    redacted_uri: str = Field(..., description="Sanitized, credential-redacted location")
    columns: list[ColumnDescriptor] = Field(
        default_factory=list, description="Profiled column schema"
    )
    row_count_estimate: int | None = Field(default=None, description="Estimated or exact row count")
    fingerprint: str = Field(
        ..., description="Deterministic content SHA-256 or snapshot identifier for CAS"
    )
    capabilities: SourceCapabilities = Field(default_factory=SourceCapabilities)


__all__ = [
    "ColumnDescriptor",
    "ForeignKeyHint",
    "NamespaceDescriptor",
    "ResolvedTableSource",
    "SourceCapabilities",
    "SourceDescriptor",
    "TableRef",
    "TableSchemaDescriptor",
    "TableSummary",
    "TabularProfileSummary",
]
