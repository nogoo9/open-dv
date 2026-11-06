from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ArtifactKind(StrEnum):
    """Canonical artifact kinds across Open-DV pipeline stages and Explorer (IP-6)."""

    # Core Data Vault pipeline artifacts
    CONTRACT = "contract"
    IR = "ir"
    SQL = "sql"
    REPORT = "report"
    LEDGER = "ledger"
    PROFILE = "profile"
    GROUNDING = "grounding"
    DRIFT = "drift"
    EXTRACT = "extract"
    TRIAGE = "triage"
    TOON = "toon"

    # Business Ontology Recommender extension artifacts (Phase P7)
    ONTOLOGY_PROPOSAL = "ontology_proposal"
    ONTOLOGY_DELTA = "ontology_delta"
    ONTOLOGY_COVERAGE = "ontology_coverage"


ArtifactKindLiteral = Literal[
    "contract",
    "ir",
    "sql",
    "report",
    "ledger",
    "profile",
    "grounding",
    "drift",
    "extract",
    "triage",
    "toon",
    "ontology_proposal",
    "ontology_delta",
    "ontology_coverage",
]


class ArtifactUploadRequest(BaseContractModel):
    """Request DTO to upload an artifact into the run/project store (Decision D1)."""

    run_id: str = Field(..., description="Target run identifier")
    artifact_type: str = Field(
        ..., description="Artifact category (e.g. contract, ir, sql, report, ontology_delta)"
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


def get_known_artifact_kinds() -> list[str]:
    """Return all registered artifact kinds."""
    return [k.value for k in ArtifactKind]


def is_known_artifact_kind(kind: str) -> bool:
    """Return whether the specified string matches a registered artifact kind."""
    normalized = kind.strip().lower()
    return any(k.value == normalized for k in ArtifactKind)


__all__ = [
    "ArtifactKind",
    "ArtifactKindLiteral",
    "ArtifactMetadataResponse",
    "ArtifactUploadRequest",
    "get_known_artifact_kinds",
    "is_known_artifact_kind",
]
