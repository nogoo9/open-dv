"""Data Vault 2.0 Schema Explorer & Persisted DuckDB Query DTOs (`D11`)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class DvHubSchemaDTO(BaseContractModel):
    """Projected Hub table metadata from Canonical IR / Contract V5."""

    name: str
    hk_column: str
    business_keys: list[str] = Field(default_factory=list)
    bkcc_column_expr: str | None = None
    ontology_concept_uri: str | None = None
    source_mappings: list[dict[str, Any]] = Field(default_factory=list)


class DvLinkSchemaDTO(BaseContractModel):
    """Projected Link table metadata from Canonical IR / Contract V5."""

    name: str
    hk_column: str
    link_type: str = "STANDARD"
    hub_references: list[dict[str, Any]] = Field(default_factory=list)
    degenerate_columns: list[str] = Field(default_factory=list)
    where_filter_condition: str | None = None


class DvSatelliteSchemaDTO(BaseContractModel):
    """Projected Satellite table metadata from Canonical IR / Contract V5."""

    name: str
    parent_entity: str
    satellite_type: str = "STANDARD"
    hash_diff_column: str = "hash_diff"
    hash_diff_migration_policy: str = "SPLIT_NEW_SATELLITE"
    payload_columns: list[dict[str, Any]] = Field(default_factory=list)
    soft_deprecated_columns: list[str] = Field(default_factory=list)


class DvReferenceTableSchemaDTO(BaseContractModel):
    """Projected Reference table (`ref_*`) metadata."""

    name: str
    natural_code_column: str
    description_columns: list[str] = Field(default_factory=list)


class DvPitTableSchemaDTO(BaseContractModel):
    """Projected Point-In-Time (`pit_*`) Business Vault table metadata."""

    name: str
    target_hub: str
    tracked_satellites: list[str] = Field(default_factory=list)


class DvSchemaWaveDiffDTO(BaseContractModel):
    """Multi-wave DV schema comparison summary (`?compare=<run_id>`)."""

    compare_run_id: str
    dropped_columns_count: int = 0
    hash_diff_mutations_count: int = 0
    added_hubs: list[str] = Field(default_factory=list)
    added_links: list[str] = Field(default_factory=list)
    added_satellites: list[str] = Field(default_factory=list)
    added_columns: list[dict[str, str]] = Field(default_factory=list)
    soft_deprecated_columns: list[dict[str, str]] = Field(default_factory=list)


class DvSchemaProjectionDTO(BaseContractModel):
    """Full Data Vault 2.0 Schema Explorer projection for a pipeline run."""

    run_id: str
    compare_run_id: str | None = None
    model_name: str = ""
    hubs: list[DvHubSchemaDTO] = Field(default_factory=list)
    links: list[DvLinkSchemaDTO] = Field(default_factory=list)
    satellites: list[DvSatelliteSchemaDTO] = Field(default_factory=list)
    reference_tables: list[DvReferenceTableSchemaDTO] = Field(default_factory=list)
    pit_tables: list[DvPitTableSchemaDTO] = Field(default_factory=list)
    column_triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    scorecard: dict[str, Any] = Field(default_factory=dict)
    wave_diff: DvSchemaWaveDiffDTO | None = None


class DvSchemaTableRowsDTO(BaseContractModel):
    """Paginated read-only row preview from a persisted sandbox DuckDB table."""

    run_id: str
    table_name: str
    schema_name: str = "raw_vault"
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    total_count: int = 0
    limit: int = 50
    offset: int = 0
    truncated: bool = False


class DvSchemaQueryRequestDTO(BaseContractModel):
    """Request payload for read-only custom SQL against a persisted sandbox DuckDB."""

    sql: str = Field(..., min_length=1, max_length=16384)
    max_rows: int = Field(default=500, ge=1, le=2000)


class DvSchemaQueryResponseDTO(BaseContractModel):
    """Response payload for read-only custom SQL execution in DV Schema Explorer."""

    run_id: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0


__all__ = [
    "DvHubSchemaDTO",
    "DvLinkSchemaDTO",
    "DvPitTableSchemaDTO",
    "DvReferenceTableSchemaDTO",
    "DvSatelliteSchemaDTO",
    "DvSchemaProjectionDTO",
    "DvSchemaQueryRequestDTO",
    "DvSchemaQueryResponseDTO",
    "DvSchemaTableRowsDTO",
    "DvSchemaWaveDiffDTO",
]
