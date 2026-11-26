"""Phase-1 DraftVaultIR models, PortableLogicalType union, and PredicateNode AST."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    KeyTierEnum,
    StrictModel,
)


class DecimalType(StrictModel):
    """Fixed-precision decimal logical type."""

    kind: Literal["DECIMAL"] = "DECIMAL"
    precision: int = Field(ge=1, le=38)
    scale: int = Field(ge=0, le=38)

    @model_validator(mode="after")
    def _validate_scale(self) -> DecimalType:
        if self.scale > self.precision:
            raise ValueError(
                f"Decimal scale ({self.scale}) cannot exceed precision ({self.precision})"
            )
        return self


class ArrayType(StrictModel):
    """Ordered array logical type."""

    kind: Literal["ARRAY"] = "ARRAY"
    element_type: PortableLogicalType


class MapType(StrictModel):
    """Key-value map logical type."""

    kind: Literal["MAP"] = "MAP"
    key_type: PortableLogicalType
    value_type: PortableLogicalType


PortableLogicalType = (
    Literal[
        "FIXED_BINARY_32",
        "UTF8_STRING",
        "INT32",
        "INT64",
        "DATE32",
        "TIMESTAMP_UTC_US",
        "BOOLEAN",
        "JSON_DOCUMENT",
    ]
    | DecimalType
    | ArrayType
    | MapType
)

ArrayType.model_rebuild()
MapType.model_rebuild()


class PredicateLeaf(StrictModel):
    """Atomic filter/sentinel predicate leaf node."""

    op: Literal["EQ", "IS_NOT_NULL"]
    column: str = Field(min_length=1)
    literal: str | int | float | bool | None = None


class PredicateBranch(StrictModel):
    """Boolean conjunction or disjunction over PredicateNode children."""

    op: Literal["AND", "OR"]
    children: list[PredicateNode]


PredicateNode = Annotated[PredicateBranch | PredicateLeaf, Field(discriminator="op")]
PredicateBranch.model_rebuild()


class DraftField(StrictModel):
    """Descriptive attribute column on a DraftVaultIR entity."""

    name: str = Field(min_length=1, description="Logical column name.")
    logical_type: PortableLogicalType = Field(description="Portable physical-neutral logical type.")
    nullable: bool = Field(description="Whether NULL values are permitted.")
    is_pii: bool = Field(description="Whether this column contains PII.")
    classification_tag: str | None = Field(
        default=None, description="Optional governance or PII classification tag."
    )


class DraftSourceBinding(StrictModel):
    """Source table and column lineage binding on a DraftVaultIR entity."""

    source_id: str
    table_id: str
    col_mappings: dict[str, str]
    doc_citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(default_factory=list)


class LinkParticipantSpec(StrictModel):
    """Hub role participant inside a Data Vault Link."""

    hub_name: str = Field(description="Referenced target Hub name (or empty for degenerate).")
    role_name: str = Field(description="Semantic role name of this participant in the Link.")
    fk_col_name: str = Field(description="Physical foreign hash-key column name in the Link.")
    source_col_names: list[str] = Field(description="Ordered source columns feeding this role.")
    target_hub_binding_table_id: str | None = Field(
        default=None,
        description=(
            "Disambiguating Hub SourceBinding table_id when a source_id binds multiple tables."
        ),
    )
    is_nullable: bool | None = Field(
        default=None,
        description=(
            "Explicit tri-state nullability override "
            "(False=mandatory, True=optional, None=profile inferred)."
        ),
    )
    key_tier: KeyTierEnum | None = Field(
        default=None, description="Optional per-Link-binding KeyTier override."
    )
    bkcc_value: str | None = Field(
        default=None, description="Optional static BKCC override for this Link participant."
    )
    bkcc_col: str | None = Field(
        default=None, description="Optional source column providing per-row BKCC."
    )
    unknown_predicate: PredicateNode | None = Field(
        default=None, description="Predicate matching Unknown (-1) sentinel participant rows."
    )
    error_predicate: PredicateNode | None = Field(
        default=None, description="Predicate matching Error (-2) sentinel participant rows."
    )


class BusinessKeyPart(StrictModel):
    """Source column contributing to a Hub business key and its BKCC tier."""

    table_id: str = Field(description="Source table identifier.")
    col_name: str = Field(description="Source column name contributing to the business key.")
    source_id: str = Field(description="Source system identifier.")
    key_tier: KeyTierEnum = Field(description="3-tier Key Authority policy for BKCC resolution.")
    bkcc_value: str = Field(description="Static BKCC literal value.")
    bkcc_col: str | None = Field(
        default=None, description="Optional source column carrying dynamic per-row BKCC."
    )
    canonical_col_name: str | None = Field(
        default=None, description="Deduplicated canonical physical BK column name."
    )


TriageDisposition = Literal[
    "UNRESOLVED",
    "MODELED",
    "SYSTEM_METADATA",
    "SYSTEM_SURROGATE_DROPPED",
    "OMITTED_INTENTIONALLY",
]


class ColumnTriageEntry(StrictModel):
    """100% column triage ledger entry."""

    table_id: str
    col_name: str
    disposition: TriageDisposition
    target_ref: str | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _validate_omitted_rationale(self) -> ColumnTriageEntry:
        if self.disposition == "OMITTED_INTENTIONALLY" and len(self.rationale.strip()) < 15:
            raise ValueError(
                "OMITTED_INTENTIONALLY requires a rationale of at least 15 non-whitespace chars"
            )
        return self


class DraftHubSpec(StrictModel):
    """Draft Hub specification emitted by Step 6 agent synthesis."""

    hub_name: str = Field(min_length=1, description="Zone Z2 Hub table name (hub_*).")
    business_keys: list[BusinessKeyPart] = Field(
        default_factory=list, description="Contributing business key parts across sources."
    )
    key_tier: KeyTierEnum = Field(description="Dominant 3-tier Key Authority policy.")
    source_bindings: list[DraftSourceBinding] = Field(
        default_factory=list, description="Source table lineage bindings feeding this Hub."
    )
    attributes: list[DraftField] = Field(
        default_factory=list, description="Must remain empty per INV-05."
    )
    is_planned_concept: bool = Field(
        default=False, description="True if concept is planned without active source bindings."
    )
    survivorship_priority: list[str] = Field(
        default_factory=list,
        description="Ordered source_id (or source_id:table_id) survivorship precedence.",
    )

    @model_validator(mode="after")
    def _validate_empty_attributes(self) -> DraftHubSpec:
        if self.attributes:
            raise ValueError(
                "INV-05 violation: DraftHubSpec.attributes must be empty; "
                "place descriptive columns in Satellites."
            )
        return self


LinkKind = Literal[
    "STANDARD",
    "TRANSACTIONAL",
    "SAME_AS",
    "HIERARCHICAL",
    "UNSTRUCTURED_EXTRACTION_LINK",
]


class DraftLinkSpec(StrictModel):
    """Draft Link specification emitted by Step 6 agent synthesis."""

    link_name: str = Field(min_length=1, description="Zone Z2 Link table name (lnk_*).")
    link_kind: LinkKind = Field(description="Structural Data Vault Link classification.")
    participants: list[LinkParticipantSpec] = Field(
        description="Ordered Hub role participants in this Link."
    )
    driving_hub: str | None = Field(
        default=None, description="Optional driving Hub name for effectivity tracking."
    )
    degenerate_keys: list[str] = Field(
        default_factory=list, description="Degenerate business key columns hashed into the Link."
    )
    source_bindings: list[DraftSourceBinding] = Field(
        default_factory=list, description="Source table lineage bindings feeding this Link."
    )
    attributes: list[DraftField] = Field(
        default_factory=list,
        description="Descriptive payload attributes (valid only on TRANSACTIONAL links).",
    )


SatKind = Literal[
    "STANDARD",
    "MAS",
    "ESAT",
    "PII",
    "AI_PROVENANCE_SATELLITE",
    "EXT",
]

SatLoadMode = Literal[
    "INSERT_ONLY_DELTA",
    "SNAPSHOT_DIFF",
    "APPEND_WITH_EFFECTIVITY",
]


class DraftSatelliteSpec(StrictModel):
    """Draft Satellite specification emitted by Step 6 agent synthesis."""

    sat_name: str = Field(min_length=1, description="Zone Z2 Satellite table name (sat_*).")
    sat_kind: SatKind = Field(description="Data Vault Satellite classification.")
    parent_name: str = Field(min_length=1, description="Parent Hub or Link entity name.")
    source_bindings: list[DraftSourceBinding] = Field(
        default_factory=list, description="Source table lineage bindings feeding this Satellite."
    )
    extends_sat_name: str | None = Field(
        default=None, description="Base Satellite name when this is an EXT companion split."
    )
    companion_epoch: int | None = Field(
        default=None, description="Monotonic wave epoch for companion Satellite splits."
    )
    is_bitemporal: bool = Field(
        default=False, description="True if Satellite tracks applied_dts alongside load_dts."
    )
    applied_dts_col: str | None = Field(
        default=None, description="Source column providing business effective/applied timestamp."
    )
    load_mode: SatLoadMode = Field(
        default="INSERT_ONLY_DELTA", description="Satellite historization and delta load strategy."
    )
    child_dependent_keys: list[str] = Field(
        default_factory=list, description="Dependent child key columns (required on MAS)."
    )
    driving_keys: list[str] = Field(
        default_factory=list, description="Driving key column names (for ESAT effectivity)."
    )
    immutable_payload_fields: list[str] = Field(
        default_factory=list, description="Payload columns excluded from mutable diffing."
    )
    attributes: list[DraftField] = Field(
        default_factory=list, description="Descriptive payload attributes in this Satellite."
    )


class BridgeHopSpec(StrictModel):
    """Single traversed Link hop inside a Zone Z3 Bridge table."""

    link_name: str = Field(
        min_length=1, description="Standard Link name traversed by this bridge hop."
    )
    from_fk_col: str | None = Field(
        default=None,
        description="Optional explicit FK column on incoming hub side for role-aliased links.",
    )
    to_fk_col: str | None = Field(
        default=None,
        description="Optional explicit FK column on outgoing hub side for role-aliased links.",
    )


class DraftBridgeSpec(StrictModel):
    """Draft Zone Z3 multi-hop Bridge specification."""

    bridge_name: str = Field(min_length=1, description="Zone Z3 Bridge table name (brg_*).")
    root_hub: str = Field(
        min_length=1, description="Anchor Hub name from which bridge hops originate."
    )
    hops: list[BridgeHopSpec] = Field(
        min_length=1, description="Ordered sequence of connected Link hops."
    )


class DraftShardIR(StrictModel):
    """Cluster-sharded Draft IR synthesized before deterministic L1 merge."""

    cluster_id: str
    hubs: list[DraftHubSpec] = Field(default_factory=list)
    links: list[DraftLinkSpec] = Field(default_factory=list)
    satellites: list[DraftSatelliteSpec] = Field(default_factory=list)
    column_triage: list[ColumnTriageEntry] = Field(default_factory=list)
    justifications: list[AntiPatternJustification] = Field(
        default_factory=list, description="Per-shard V6b anti-pattern justifications."
    )
    pending_clarifications: list[ClarificationQuestion] = Field(default_factory=list)


class DraftVaultIR(StrictModel):
    """Pre-L1/L2 Draft Vault IR authored by Step 6 agent runs."""

    ir_version: Literal["2.0"] = Field(default="2.0", description="Draft IR schema version.")
    project_id: str = Field(min_length=1, description="Project identifier.")
    ontology_version: str = Field(description="Source GraphAOntology version.")
    wave_number: int = Field(ge=1, description="Incremental modeling wave number.")
    hubs: list[DraftHubSpec] = Field(default_factory=list, description="Draft Hub specifications.")
    links: list[DraftLinkSpec] = Field(
        default_factory=list, description="Draft Link specifications."
    )
    satellites: list[DraftSatelliteSpec] = Field(
        default_factory=list, description="Draft Satellite specifications."
    )
    bridges: list[DraftBridgeSpec] = Field(
        default_factory=list, description="Post-merge Zone Z3 multi-hop Bridge declarations."
    )
    column_triage: list[ColumnTriageEntry] = Field(
        default_factory=list, description="100% source column triage ledger."
    )
    justifications: list[AntiPatternJustification] = Field(
        default_factory=list, description="Waivable V6b anti-pattern justifications."
    )
    pending_clarifications: list[ClarificationQuestion] = Field(
        default_factory=list, description="Open Steward clarification questions."
    )


__all__ = [
    "ArrayType",
    "BridgeHopSpec",
    "BusinessKeyPart",
    "ColumnTriageEntry",
    "DecimalType",
    "DraftBridgeSpec",
    "DraftField",
    "DraftHubSpec",
    "DraftLinkSpec",
    "DraftSatelliteSpec",
    "DraftShardIR",
    "DraftSourceBinding",
    "DraftVaultIR",
    "LinkKind",
    "LinkParticipantSpec",
    "MapType",
    "PortableLogicalType",
    "PredicateBranch",
    "PredicateLeaf",
    "PredicateNode",
    "SatKind",
    "SatLoadMode",
    "TriageDisposition",
]
