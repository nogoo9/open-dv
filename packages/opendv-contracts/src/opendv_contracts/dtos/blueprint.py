"""Normative Data Transfer Objects for VaultBlueprint and Lineage (v1.0 / ADR-017).

Enables language-neutral serialization and protocol exchange of VaultBlueprints
between Open-DV service, CLI, Web Workbench, and agent harness.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from opendv_contracts.dtos.common import BaseContractModel


class BlueprintLineageDTO(BaseContractModel):
    """Lineage provenance metadata for a VaultBlueprint (ADR-017)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Lineage schema version",
    )
    blueprint_revision: int = Field(
        default=1,
        ge=1,
        description="Sequential blueprint revision counter across pipeline runs",
    )
    domain_namespace: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Target business domain namespace matching ontology",
    )
    ontology_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="Deterministic RFC 8785 JCS SHA-256 of the GraphAOntology",
    )
    contract_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="Deterministic RFC 8785 JCS SHA-256 of NoMistakeDataVault2ContractV5",
    )
    parent_blueprint_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of immediate parent VaultBlueprint (null on genesis)",
    )
    lineage_notes: str | None = Field(
        default=None,
        description="Human-readable provenance or evolution note",
    )


class VaultBlueprintDTO(BaseContractModel):
    """Unified envelope packaging ontology, contract, and lineage (ADR-017)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0.0"] = Field(
        default="1.0.0",
        alias="blueprint_version",
        description="Normative VaultBlueprint specification version",
    )
    domain_namespace: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Target business domain namespace",
    )
    lineage: BlueprintLineageDTO = Field(
        ...,
        description="Cryptographic lineage metadata chaining parent revisions",
    )
    ontology: dict[str, Any] = Field(
        ...,
        description="Semantic oracle: GraphAOntology v1.0 payload",
    )
    contract: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Physical Data Vault 2.0 model contract (or null for zero-data greenfield seed)"
        ),
    )


class BlueprintExportResponseDTO(BaseContractModel):
    """Response DTO for Studio blueprint export endpoints (/export-blueprint)."""

    blueprint: VaultBlueprintDTO = Field(..., description="Exported VaultBlueprint envelope")
    raw_vault_ddl: str = Field(default="", description="Zone 2 Raw Vault DDL preview")
    target_tables: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Summary of target Hubs, Links, and Satellites in the blueprint",
    )
    exported_at_utc: str = Field(..., description="ISO 8601 UTC export timestamp")


class BlueprintEvolutionDiffDTO(BaseContractModel):
    """Summary of schema and ontology diff between input and output blueprints."""

    added_hubs: list[str] = Field(default_factory=list)
    added_links: list[str] = Field(default_factory=list)
    added_satellites: list[str] = Field(default_factory=list)
    widened_columns: list[dict[str, Any]] = Field(default_factory=list)
    renamed_columns: list[dict[str, Any]] = Field(default_factory=list)
    companion_satellites: list[str] = Field(default_factory=list)
    ontology_deltas_applied: list[dict[str, Any]] = Field(default_factory=list)
    lineage_sha256_chain: list[str] = Field(default_factory=list)


__all__ = [
    "BlueprintEvolutionDiffDTO",
    "BlueprintExportResponseDTO",
    "BlueprintLineageDTO",
    "VaultBlueprintDTO",
]
