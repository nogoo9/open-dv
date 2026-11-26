"""Phase-2 CertifiedVaultIR (VaultSchemaIR v2.0) models across Zones Z0-Z4."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from opendv.models.diagnostics import (
    AntiPatternJustification,
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    KeyTierEnum,
    StrictModel,
)
from opendv.models.vault_ir import (
    BridgeHopSpec,
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftField,
    LinkKind,
    LinkParticipantSpec,
    PortableLogicalType,
    PredicateNode,
    SatKind,
    SatLoadMode,
    hub_canonical_bk_cols,
)

ZoneId = Literal["Z0", "Z1", "Z2", "Z3", "Z4"]


class IRField(StrictModel):
    """Physical column definition with monotonic field_id for Iceberg/DDL stability."""

    field_id: int = Field(ge=1)
    name: str = Field(min_length=1)
    logical_type: PortableLogicalType
    nullable: bool
    is_pii: bool
    classification_tag: str | None = None


def _assert_monotonic_field_ids(fields: list[IRField], label: str) -> None:
    prev = 0
    for field in fields:
        if field.field_id <= prev:
            raise ValueError(
                f"{label} IRField.field_id must be strictly increasing "
                f"(got {field.field_id} after {prev})"
            )
        prev = field.field_id


class LinkParticipantHashSpec(StrictModel):
    """Per-participant Algorithm A.5 hash specification inside a Link HashRecipeSpec."""

    hub_name: str = ""
    role_name: str
    target_hub_binding_table_id: str | None = None
    bkcc_literal: str | None = None
    bkcc_col: str | None = None
    key_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR"
    source_columns: list[str]
    unknown_predicate: PredicateNode | None = None
    error_predicate: PredicateNode | None = None
    is_degenerate: bool = False
    is_nullable: bool | None = None


class HashRecipeSpec(StrictModel):
    """Storage-neutral Algorithm A.5 hash recipe lowered by target compilers."""

    target_col: str = Field(min_length=1)
    recipe_kind: Literal["HUB_HK", "LINK_HK", "SAT_HASH_DIFF"]
    bkcc_literal: str | None = None
    bkcc_col: str | None = None
    source_columns: list[str]
    sort_alphabetically: bool
    key_columns: list[str] = Field(default_factory=list)
    participant_specs: list[LinkParticipantHashSpec] = Field(default_factory=list)
    unknown_predicate: PredicateNode | None = None
    error_predicate: PredicateNode | None = None
    is_optional_inapplicable: bool = False


class SeedRecordsSpec(StrictModel):
    """Canonical Zero-Key (-1 Unknown, -2 Error) and Ghost record specification."""

    unknown_hk_hex: str = "0" * 64
    error_hk_hex: str = "f" * 64
    ghost_hash_diff_hex: str = "0" * 64
    ghost_load_dts: str = "1900-01-01T00:00:00Z"
    ghost_rec_src: str = "SYSTEM_SEED"
    default_string: str = "UNKNOWN"
    default_num: int = -1
    default_hash_input: str = "0"


class LoadSemanticsSpec(StrictModel):
    """Storage-neutral ingestion filter and intra-batch deduplication specification."""

    source_table_id: str
    filter_ast: PredicateNode | None = None
    require_review_status: Literal["APPROVED"] | None = None
    dedup_order_cols: list[str] = Field(default_factory=list)


class SourceBindingSpec(StrictModel):
    """Enriched source lineage binding on a CertifiedVaultIR entity."""

    source_id: str
    table_id: str
    col_mappings: dict[str, str]
    key_tier: KeyTierEnum | None = None
    hash_recipe: HashRecipeSpec | None = None
    load_semantics: LoadSemanticsSpec | None = None
    doc_citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(default_factory=list)


class PhysicalLayoutHints(StrictModel):
    """Optional clustering, partitioning, and Iceberg tagging metadata."""

    cluster_by_cols: list[str] = Field(default_factory=list)
    partition_grain: str | None = None
    iceberg_tags: dict[str, str] = Field(default_factory=dict)


class SchemaWaveDelta(StrictModel):
    """Wave-to-wave structural delta summary across CertifiedVaultIR revisions."""

    from_wave: int = Field(ge=1)
    to_wave: int = Field(ge=2)
    added_entities: list[str] = Field(default_factory=list)
    added_sat_extensions: list[str] = Field(default_factory=list)
    locked_entities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_wave_order(self) -> SchemaWaveDelta:
        if self.to_wave <= self.from_wave:
            raise ValueError(
                f"to_wave ({self.to_wave}) must be greater than from_wave ({self.from_wave})"
            )
        return self


class CertifiedHubSpec(StrictModel):
    """Certified Zone Z2 Hub specification."""

    hub_name: str = Field(min_length=1)
    zone_id: ZoneId = "Z2"
    business_keys: list[BusinessKeyPart] = Field(default_factory=list)
    key_tier: KeyTierEnum
    source_bindings: list[SourceBindingSpec] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)
    is_planned_concept: bool = False
    survivorship_priority: list[str] = Field(default_factory=list)
    columns: list[IRField] = Field(default_factory=list)
    hash_recipe: HashRecipeSpec | None = None
    load_semantics: LoadSemanticsSpec | None = None
    physical_hints: PhysicalLayoutHints | None = None

    @model_validator(mode="after")
    def _validate_certified_hub(self) -> CertifiedHubSpec:
        if self.attributes:
            raise ValueError("INV-05 violation: CertifiedHubSpec.attributes must be empty")
        if not self.is_planned_concept and self.physical_hints is None:
            raise ValueError("physical_hints is required unless is_planned_concept=True")
        _assert_monotonic_field_ids(self.columns, f"CertifiedHubSpec({self.hub_name})")
        return self


class CertifiedLinkSpec(StrictModel):
    """Certified Zone Z2 Link specification."""

    link_name: str = Field(min_length=1)
    zone_id: ZoneId = "Z2"
    link_kind: LinkKind
    participants: list[LinkParticipantSpec]
    driving_hub: str | None = None
    degenerate_keys: list[str] = Field(default_factory=list)
    source_bindings: list[SourceBindingSpec] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)
    columns: list[IRField] = Field(default_factory=list)
    hash_recipe: HashRecipeSpec | None = None
    load_semantics: LoadSemanticsSpec | None = None
    physical_hints: PhysicalLayoutHints

    @model_validator(mode="after")
    def _validate_field_ids(self) -> CertifiedLinkSpec:
        _assert_monotonic_field_ids(self.columns, f"CertifiedLinkSpec({self.link_name})")
        return self


class CertifiedSatelliteSpec(StrictModel):
    """Certified Zone Z2 Satellite specification."""

    sat_name: str = Field(min_length=1)
    zone_id: ZoneId = "Z2"
    sat_kind: SatKind
    parent_name: str = Field(min_length=1)
    source_id: str
    source_table_id: str
    source_bindings: list[SourceBindingSpec] = Field(default_factory=list)
    extends_sat_name: str | None = None
    companion_epoch: int | None = Field(default=None, ge=1)
    is_bitemporal: bool = False
    applied_dts_col: str | None = None
    load_mode: SatLoadMode = "INSERT_ONLY_DELTA"
    child_dependent_keys: list[str] = Field(default_factory=list)
    driving_keys: list[str] = Field(default_factory=list)
    immutable_payload_fields: list[str] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)
    columns: list[IRField] = Field(default_factory=list)
    hash_recipe: HashRecipeSpec | None = None
    load_semantics: LoadSemanticsSpec | None = None
    physical_hints: PhysicalLayoutHints

    @model_validator(mode="after")
    def _validate_field_ids(self) -> CertifiedSatelliteSpec:
        _assert_monotonic_field_ids(self.columns, f"CertifiedSatelliteSpec({self.sat_name})")
        return self


class BronzeTableSpec(StrictModel):
    """Zone Z0 raw bronze table specification."""

    table_name: str = Field(min_length=1)
    source_id: str
    source_table_id: str
    zone_id: Literal["Z0"] = "Z0"
    columns: list[IRField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_field_ids(self) -> BronzeTableSpec:
        _assert_monotonic_field_ids(self.columns, f"BronzeTableSpec({self.table_name})")
        return self


class StagingViewSpec(StrictModel):
    """Zone Z1 staging view specification carrying Alg A.5 hash recipes."""

    view_name: str = Field(min_length=1)
    source_id: str
    source_table_id: str
    zone_id: Literal["Z1"] = "Z1"
    col_lineage_mappings: dict[str, str]
    hash_recipes: list[HashRecipeSpec] = Field(default_factory=list)
    projected_fields: list[IRField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_field_ids(self) -> StagingViewSpec:
        _assert_monotonic_field_ids(
            self.projected_fields, f"StagingViewSpec({self.view_name})"
        )
        return self


class RefTableSpec(StrictModel):
    """Zone Z0 reference enumeration table specification."""

    ref_name: str = Field(min_length=1)
    enum_uri: str
    zone_id: Literal["Z0"] = "Z0"
    code_col: str
    label_col: str
    codes: dict[str, str]


class PitBridgeSpec(StrictModel):
    """Zone Z3 Business Vault PIT or Bridge specification."""

    object_name: str = Field(min_length=1)
    object_kind: Literal["PIT", "BRIDGE"]
    zone_id: Literal["Z3"] = "Z3"
    root_hub: str
    target_satellites_or_links: list[str]
    grain_cols: list[str]
    hops: list[BridgeHopSpec] = Field(default_factory=list)
    traversed_hk_cols: list[str] = Field(default_factory=list)


class MartViewSpec(StrictModel):
    """Zone Z4 Information Mart view specification."""

    view_name: str = Field(min_length=1)
    zone_id: Literal["Z3", "Z4"] = "Z4"
    view_kind: Literal["CURRENT", "ESAT_INTERVALS"] = "CURRENT"
    root_entity: str
    joined_entities: list[str]
    columns: list[IRField] = Field(default_factory=list)
    select_exprs: dict[str, str]
    dedup_field_specs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_field_ids(self) -> MartViewSpec:
        _assert_monotonic_field_ids(self.columns, f"MartViewSpec({self.view_name})")
        return self


class CertifiedVaultIR(StrictModel):
    """Zero-SQL certified UniversalVaultSchemaIR v2.0 across Zones Z0-Z4."""

    ir_version: Literal["2.0"] = "2.0"
    project_id: str = Field(min_length=1)
    ontology_version: str
    wave_number: int = Field(ge=1)
    bronze_tables: list[BronzeTableSpec] = Field(default_factory=list)
    staging_views: list[StagingViewSpec] = Field(default_factory=list)
    hubs: list[CertifiedHubSpec] = Field(default_factory=list)
    links: list[CertifiedLinkSpec] = Field(default_factory=list)
    satellites: list[CertifiedSatelliteSpec] = Field(default_factory=list)
    reference_tables: list[RefTableSpec] = Field(default_factory=list)
    pit_bridges: list[PitBridgeSpec] = Field(default_factory=list)
    information_marts: list[MartViewSpec] = Field(default_factory=list)
    seed_records: SeedRecordsSpec = Field(default_factory=SeedRecordsSpec)
    column_triage: list[ColumnTriageEntry] = Field(default_factory=list)
    justifications: list[AntiPatternJustification] = Field(default_factory=list)
    wave_delta: SchemaWaveDelta | None = None


__all__ = [
    "BronzeTableSpec",
    "CertifiedHubSpec",
    "CertifiedLinkSpec",
    "CertifiedSatelliteSpec",
    "CertifiedVaultIR",
    "HashRecipeSpec",
    "IRField",
    "LinkParticipantHashSpec",
    "LoadSemanticsSpec",
    "MartViewSpec",
    "PhysicalLayoutHints",
    "PitBridgeSpec",
    "RefTableSpec",
    "SchemaWaveDelta",
    "SeedRecordsSpec",
    "SourceBindingSpec",
    "StagingViewSpec",
    "ZoneId",
    "hub_canonical_bk_cols",
]
