"""Data transfer objects for Asset Management starter packs and presets."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel


class AssetManagementPackId(StrEnum):
    """Authoritative identifier for Asset Management starter packs."""

    CORE_SPINE = "core_spine"
    PUBLIC_EQUITIES_OMS = "public_equities_oms"
    FIXED_INCOME_RATES = "fixed_income_rates"
    MULTI_ASSET_IBOR = "multi_asset_ibor"
    PRIVATE_EQUITY_BUYOUT = "private_equity_buyout"
    PRIVATE_CREDIT_DIRECT_LENDING = "private_credit_direct_lending"
    CUSTODY_CLEARING = "custody_clearing"
    ALL_DOMAINS = "all_domains"


class StarterPackMetadata(BaseContractModel):
    """Metadata summary describing a composable starter pack."""

    pack_id: AssetManagementPackId = Field(..., description="Unique pack identifier")
    display_name: str = Field(..., description="Human-readable title for UI display")
    description: str = Field(..., description="Detailed description of pack domain")
    domain_namespace: str = Field(..., description="Domain namespace key")
    standards_references: list[str] = Field(
        default_factory=list, description="Authoritative industry standards referenced"
    )
    concept_count: int = Field(..., ge=1, description="Count of Canonical Business Concepts")
    relationship_count: int = Field(..., ge=0, description="Count of Canonical Relationships")
    concepts: list[str] = Field(default_factory=list, description="List of concept names or URNs")
    dependencies: list[AssetManagementPackId] = Field(
        default_factory=list, description="Required prerequisite packs"
    )
    version: str = Field(default="1.0.0", description="Pack semver version")


StarterPackSummaryDTO = StarterPackMetadata


class StarterPackListResponse(BaseContractModel):
    """Response payload listing all available starter packs."""

    packs: list[StarterPackMetadata] = Field(
        ..., description="Collection of registered starter packs"
    )
    default_preset: AssetManagementPackId = Field(
        default=AssetManagementPackId.ALL_DOMAINS,
        description="Recommended starter pack preset",
    )


class ApplyStarterPackRequest(BaseContractModel):
    """Request payload to apply one or more starter packs to a Studio session."""

    pack_ids: list[AssetManagementPackId] = Field(
        default_factory=list,
        description="Pack identifiers to compose and apply",
    )
    pack_id: AssetManagementPackId | None = Field(
        default=None,
        description="Single pack identifier alias (normalized into pack_ids)",
    )
    pack: AssetManagementPackId | None = Field(
        default=None,
        description="Pack identifier alias",
    )
    base_version: str | None = Field(default=None, description="Expected base ontology version")
    base_sha256: str | None = Field(
        default=None, description="Expected base ontology SHA-256 for OCC"
    )
    domain_namespace: str | None = Field(default=None, description="Optional namespace override")
    enterprise_owner: str | None = Field(
        default=None, description="Optional enterprise owner override"
    )

    @model_validator(mode="after")
    def _normalize_pack_ids(self) -> ApplyStarterPackRequest:
        if not self.pack_ids:
            target = self.pack_id if self.pack_id is not None else self.pack
            if target is not None:
                object.__setattr__(self, "pack_ids", [target])
        if self.pack_id is None and self.pack is not None:
            object.__setattr__(self, "pack_id", self.pack)
        if not self.pack_ids:
            raise ValueError("At least one pack identifier must be provided in pack_ids or pack_id")
        return self


class ApplyStarterPackResponse(BaseContractModel):
    """Response payload returned upon applying starter packs."""

    applied: bool = Field(default=True, description="Whether applying starter pack succeeded")
    applied_pack_ids: list[AssetManagementPackId] = Field(
        ..., description="List of successfully applied pack identifiers"
    )
    ontology_version: str = Field(..., description="Version of the updated ontology")
    ontology_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="Canonical JCS SHA-256 of updated ontology",
    )
    concept_count: int = Field(..., ge=1, description="Total active CBCs in session ontology")
    relationship_count: int = Field(..., ge=0, description="Total active relationships")
    applied_delta_id: str | None = Field(
        default=None, description="ID of generated and applied delta if committed"
    )


__all__ = [
    "ApplyStarterPackRequest",
    "ApplyStarterPackResponse",
    "AssetManagementPackId",
    "StarterPackListResponse",
    "StarterPackMetadata",
    "StarterPackSummaryDTO",
]
