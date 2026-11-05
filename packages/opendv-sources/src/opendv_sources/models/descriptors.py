"""Metadata models describing resolved data sources and schemas."""

from __future__ import annotations

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ColumnDescriptor(BaseContractModel):
    """Metadata describing a single table column."""

    name: str = Field(..., description="Column identifier")
    physical_type: str = Field(..., description="Physical storage or SQL type")
    nullable: bool = Field(default=True, description="Nullability constraint")


class SourceCapabilities(BaseContractModel):
    """Capabilities exposed by a source adapter."""

    supports_streaming: bool = Field(default=False)
    supports_pushdown: bool = Field(default=False)
    supports_drift_detection: bool = Field(default=True)
    client_uploadable: bool = Field(default=False)
    server_resolved: bool = Field(default=True)


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
    "SourceCapabilities",
    "SourceDescriptor",
]
