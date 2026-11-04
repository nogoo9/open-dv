from __future__ import annotations

from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ArtifactUploadRequest(BaseContractModel):
    """Request DTO to upload an artifact into the run/project store (Decision D1)."""

    run_id: str = Field(..., description="Target run identifier")
    artifact_type: str = Field(
        ..., description="Artifact category (e.g. contract, ir, sql, report)"
    )
    filename: str = Field(..., description="Target artifact filename")
    content: str = Field(
        ..., description="Raw text or base64-encoded artifact content (Decision D1)"
    )
    sha256: str | None = Field(
        default=None, pattern=r"^[0-9A-Fa-f]{64}$", description="Expected SHA-256 digest"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary artifact metadata"
    )


class ArtifactMetadataResponse(BaseContractModel):
    """Metadata descriptor for a stored CAS or run artifact."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    run_id: str = Field(..., description="Associated run identifier")
    artifact_type: str = Field(..., description="Artifact category")
    filename: str = Field(..., description="Filename")
    size_bytes: int = Field(ge=0, description="Content size in bytes")
    sha256: str = Field(..., pattern=r"^[0-9A-Fa-f]{64}$", description="SHA-256 digest")
    created_at_utc: str = Field(..., description="ISO 8601 UTC storage timestamp")
