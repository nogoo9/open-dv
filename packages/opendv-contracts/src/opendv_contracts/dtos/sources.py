"""Normative Data Transfer Objects for Universal Multi-Connector Sources (ADR-021).

Defines SourceFeedDTO, ConnectorUIManifestDTO, and source introspection contracts
supporting local files, SQLite, DuckDB, Delta stores, and Databricks Unity Catalog.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from opendv_contracts.dtos.common import BaseContractModel

_CREDENTIALS_REF_RE = re.compile(r"^(env|secret):[A-Za-z0-9_-]+$")


class NamespaceSpecDTO(BaseContractModel):
    """Specification of catalog/schema hierarchy (e.g. Databricks catalog.schema)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    catalog: str | None = Field(default=None, description="Catalog name (for 3-level catalogs)")
    schema_name: str | None = Field(default=None, description="Schema / database name")


class TableSelectionDTO(BaseContractModel):
    """Policy for selecting user tables within a multi-table catalog or database."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: Literal["ALL_USER_TABLES", "EXPLICIT", "PATTERN"] = Field(
        default="ALL_USER_TABLES",
        description="Table selection mode",
    )
    tables: list[str] = Field(
        default_factory=list,
        description="Explicit table names to include when mode=EXPLICIT",
    )
    include_pattern: str | None = Field(
        default=None,
        description="Regex pattern to filter included tables when mode=PATTERN",
    )
    exclude_pattern: str | None = Field(
        default=None,
        description="Regex pattern to filter excluded tables",
    )


class SamplingPolicyDTO(BaseContractModel):
    """Policy governing sandbox row bounds and profiling sampling strategy."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    max_sandbox_rows: int = Field(
        default=10_000,
        ge=1,
        description="Maximum rows streamed into DuckDB shadow sandbox",
    )
    strategy: Literal["DETERMINISTIC_LIMIT", "TABLESAMPLE_BERNOULLI"] = Field(
        default="DETERMINISTIC_LIMIT",
        description="Sampling strategy for remote pushdown profilers",
    )
    sample_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional sampling ratio for Bernoulli sampling",
    )


class SourceFeedDTO(BaseContractModel):
    """Universal source feed contract supporting single files and multi-table catalogs."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    feed_id: str = Field(..., description="Unique feed identifier within the pipeline run")
    source_system: str = Field(..., description="Normalized source system identifier")
    uri: str = Field(..., description="Source path, URI, or connection string")
    adapter: str | None = Field(
        default=None,
        description="Optional adapter identifier override (inferred from URI scheme if omitted)",
    )
    namespace: NamespaceSpecDTO | None = Field(
        default=None,
        description="Optional 2- or 3-level catalog/schema namespace",
    )
    table_selection: TableSelectionDTO | None = Field(
        default=None,
        description="Table selection rules for multi-table sources",
    )
    credentials_ref: str | None = Field(
        default=None,
        description=(
            "Safe reference to credentials formatted strictly as 'env:VAR' or 'secret:NAME'"
        ),
    )
    sampling_policy: SamplingPolicyDTO = Field(
        default_factory=SamplingPolicyDTO,
        description="Sandbox row bounds and profiling sampling policy",
    )
    target_dialect: str | None = Field(
        default=None,
        description="Optional target SQL output dialect alongside canonical DuckDB",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific configuration options (e.g. http_path, time travel)",
    )

    @field_validator("credentials_ref")
    @classmethod
    def _validate_credentials_ref(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            if not _CREDENTIALS_REF_RE.match(v_clean):
                raise ValueError(
                    f"Invalid credentials_ref '{v}'. Must match '^(env|secret):[A-Za-z0-9_-]+$' "
                    "to prevent hardcoded literal secrets in pipeline specifications."
                )
            return v_clean
        return None


class ConnectorUIManifestDTO(BaseContractModel):
    """Declarative UI descriptor for a source connector adapter."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    adapter_id: str = Field(
        ..., description="Unique adapter identifier (e.g. 'sqlite', 'databricks')"
    )
    display_name: str = Field(..., description="Human-readable connector name")
    description: str = Field(..., description="Connector description and capabilities summary")
    schemes: list[str] = Field(default_factory=list, description="Supported URI schemes")
    supported_file_extensions: list[str] = Field(
        default_factory=list,
        description="Supported file extensions (e.g. ['.sqlite', '.db'])",
    )
    execution_mode: Literal[
        "DIRECT_DUCKDB_ATTACH",
        "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
        "INLINE_CAS_STAGED",
    ] = Field(..., description="Execution mode within the Open-DV engine")
    client_uploadable: bool = Field(
        default=False,
        description="Whether this source type supports direct file upload via CAS",
    )
    supports_multi_table: bool = Field(default=False)
    supports_catalog_hierarchy: bool = Field(default=False)
    supports_foreign_keys: bool = Field(default=False)
    supports_governance_tags: bool = Field(default=False)
    supports_time_travel: bool = Field(default=False)
    sample_uri: str | None = Field(default=None)
    connection_fields: list[dict[str, Any]] = Field(default_factory=list)
    secret_ref_fields: list[str] = Field(default_factory=list)


class SourceInspectRequestDTO(BaseContractModel):
    """Request payload to inspect and enumerate tables from a data source."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    uri: str = Field(..., description="Source path, URI, or upload handle")
    adapter: str | None = Field(default=None, description="Optional adapter scheme override")
    credentials_ref: str | None = Field(
        default=None, description="Credentials reference (env: / secret:)"
    )
    namespace: NamespaceSpecDTO | None = Field(
        default=None, description="Optional namespace filter"
    )
    cas_hash: str | None = Field(default=None, description="Optional CAS hash for staged uploads")
    options: dict[str, Any] = Field(default_factory=dict, description="Adapter connection options")

    @field_validator("credentials_ref")
    @classmethod
    def _validate_credentials_ref(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            if not _CREDENTIALS_REF_RE.match(v_clean):
                raise ValueError(
                    f"Invalid credentials_ref '{v}'. Must match '^(env|secret):[A-Za-z0-9_-]+$' "
                    "to prevent hardcoded literal secrets in pipeline specifications."
                )
            return v_clean
        return None

    @field_validator("cas_hash")
    @classmethod
    def _validate_cas_hash(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            if not re.match(r"^[0-9a-fA-F]{64}$", v_clean):
                raise ValueError(
                    f"Invalid cas_hash '{v}'. "
                    "Must be a 64-character hex digest matching '^[0-9a-fA-F]{64}$'."
                )
            return v_clean.lower()
        return None


class SourceInspectResponseDTO(BaseContractModel):
    """Response payload returning discovered tables, columns, constraints, and tags."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    adapter_id: str = Field(..., description="Resolved adapter identifier")
    namespaces: list[dict[str, Any]] = Field(
        default_factory=list, description="Available namespaces"
    )
    tables: list[dict[str, Any]] = Field(
        default_factory=list, description="Discovered tables and metadata"
    )
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="Source capabilities map"
    )
    fingerprint: str | None = Field(
        default=None, description="Deterministic source snapshot digest"
    )
    inspected_at_utc: str = Field(..., description="ISO 8601 UTC inspection timestamp")


__all__ = [
    "ConnectorUIManifestDTO",
    "NamespaceSpecDTO",
    "SamplingPolicyDTO",
    "SourceFeedDTO",
    "SourceInspectRequestDTO",
    "SourceInspectResponseDTO",
    "TableSelectionDTO",
]
