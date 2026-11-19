"""Declarative specifications for data sources and format options."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_core.config import get_core_modeling_config, validate_credentials_ref

_DISALLOWED_OPTION_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "auth",
    "access_key",
    "private_key",
}


_FORBIDDEN_CONTAINMENT_OPTION_KEYS = {
    "workspace_root",
    "allowed_roots",
    "base_dir",
    "allow_temp",
}


class NamespaceRef(BaseContractModel):
    """Reference identifying a database, catalog, or schema namespace."""

    catalog: str | None = Field(default=None, description="Optional 3-level catalog identifier")
    schema_name: str | None = Field(default=None, description="Schema or database identifier")


class TableRef(BaseContractModel):
    """Reference identifying a specific table within an optional namespace."""

    table_name: str = Field(..., description="Table or view identifier")
    namespace: NamespaceRef | None = Field(default=None, description="Optional namespace reference")

    @property
    def canonical_name(self) -> str:
        """Canonical dot-delimited identifier (e.g. catalog.schema.table or table)."""
        parts: list[str] = []
        if self.namespace:
            if self.namespace.catalog:
                parts.append(self.namespace.catalog)
            if self.namespace.schema_name:
                parts.append(self.namespace.schema_name)
        parts.append(self.table_name)
        return ".".join(parts)


TableSelectionMode = Literal["ALL_USER_TABLES", "EXPLICIT", "PATTERN"]


class TableSelectionSpec(BaseContractModel):
    """Specification defining which tables to ingest or inspect from a catalog feed."""

    mode: TableSelectionMode = Field(
        default="ALL_USER_TABLES",
        description="Selection mode: ALL_USER_TABLES, EXPLICIT, or PATTERN",
    )
    tables: list[str] = Field(
        default_factory=list,
        description="Explicit table names to ingest when mode is EXPLICIT",
    )
    include_pattern: str | None = Field(
        default=None,
        description="Regex or glob pattern of table names to include when mode is PATTERN",
    )
    exclude_pattern: str | None = Field(
        default=None,
        description="Regex or glob pattern of table names to exclude",
    )


SamplingStrategy = Literal["DETERMINISTIC_LIMIT", "TABLESAMPLE_BERNOULLI"]


class SamplingPolicy(BaseContractModel):
    """Sampling configuration for sandbox staging and profile pushdown."""

    max_sandbox_rows: int = Field(
        default=get_core_modeling_config().max_sandbox_rows,
        ge=1,
        description="Maximum row count staged into DuckDB shadow sandbox",
    )
    strategy: SamplingStrategy = Field(
        default="DETERMINISTIC_LIMIT",
        description="Sampling method: DETERMINISTIC_LIMIT or TABLESAMPLE_BERNOULLI",
    )
    seed: int | None = Field(
        default=None,
        description="Optional deterministic random seed for repeatable Bernoulli sampling",
    )


class SourceSpec(BaseContractModel):
    """Declarative specification for an input data source."""

    adapter: str = Field(
        ...,
        description="Adapter plugin identifier (e.g. 'file', 'inline', 'duckdb_attach')",
    )
    uri: str | None = Field(
        default=None,
        description="Workspace-relative URI, file path, or connection string",
    )
    format: str | None = Field(
        default=None,
        description="Explicit format override (e.g. 'csv', 'parquet', 'jsonl')",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter- and format-specific configuration options",
    )
    credentials_ref: str | None = Field(
        default=None,
        description="Reference to credentials in 'env:VAR' or 'secret:NAME' form",
    )
    inline_content: str | bytes | None = Field(
        default=None,
        description="Inline content for API uploads (Decision D1)",
    )
    client_path: str | None = Field(
        default=None,
        description="Client display path for remote inline uploads",
    )
    namespace: NamespaceRef | None = Field(
        default=None,
        description="Optional catalog/schema namespace specification",
    )
    table_selection: TableSelectionSpec | None = Field(
        default=None,
        description="Optional table selection filter for multi-table catalogs",
    )
    sampling_policy: SamplingPolicy | None = Field(
        default=None,
        description="Optional sampling policy for pushdown profiling and sandbox staging",
    )

    @field_validator("credentials_ref")
    @classmethod
    def _validate_credentials_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            return validate_credentials_ref(v)
        except ValueError as exc:
            raise ValueError(
                f"credentials_ref must use 'env:NAME' or 'secret:NAME' format, got '{v}'. "
                "Literal passwords/secrets are strictly forbidden (REQ-CFG-01)."
            ) from exc

    @field_validator("options")
    @classmethod
    def _validate_options_no_secrets(cls, v: dict[str, Any]) -> dict[str, Any]:
        for k in v:
            lower_k = k.lower()
            if any(banned in lower_k for banned in _DISALLOWED_OPTION_KEYS):
                raise ValueError(
                    f"Option '{k}' rejected: literal credentials in options are forbidden. "
                    "Use credentials_ref instead."
                )
            if lower_k in _FORBIDDEN_CONTAINMENT_OPTION_KEYS:
                raise ValueError(
                    f"Option '{k}' rejected: client override of containment option '{k}' "
                    "is forbidden. Configure containment boundaries on the server adapter instead."
                )
        return v


class FormatSpec(BaseContractModel):
    """Specification for data format parsing options."""

    format: str = Field(
        ...,
        description="Format identifier (e.g. 'csv', 'parquet', 'json', 'pdf')",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Format parser options (e.g. delimiter, header)",
    )
    schema_hint: dict[str, Any] | None = Field(
        default=None,
        description="Optional expected schema column hint",
    )


__all__ = [
    "FormatSpec",
    "NamespaceRef",
    "SamplingPolicy",
    "SamplingStrategy",
    "SourceSpec",
    "TableRef",
    "TableSelectionMode",
    "TableSelectionSpec",
]
