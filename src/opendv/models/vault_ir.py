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

    name: str = Field(min_length=1)
    logical_type: PortableLogicalType
    nullable: bool
    is_pii: bool
    classification_tag: str | None = None


class DraftSourceBinding(StrictModel):
    """Source table and column lineage binding on a DraftVaultIR entity."""

    source_id: str
    table_id: str
    col_mappings: dict[str, str]
    doc_citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(default_factory=list)


class LinkParticipantSpec(StrictModel):
    """Hub role participant inside a Data Vault Link."""

    hub_name: str
    role_name: str
    fk_col_name: str
    source_col_names: list[str]


class BusinessKeyPart(StrictModel):
    """Source column contributing to a Hub business key and its BKCC tier."""

    table_id: str
    col_name: str
    source_id: str
    key_tier: KeyTierEnum
    bkcc_value: str
    bkcc_col: str | None = None


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

    hub_name: str = Field(min_length=1)
    business_keys: list[BusinessKeyPart] = Field(default_factory=list)
    key_tier: KeyTierEnum
    source_bindings: list[DraftSourceBinding] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)
    is_planned_concept: bool = False


LinkKind = Literal[
    "STANDARD",
    "TRANSACTIONAL",
    "SAME_AS",
    "HIERARCHICAL",
    "UNSTRUCTURED_EXTRACTION_LINK",
]


class DraftLinkSpec(StrictModel):
    """Draft Link specification emitted by Step 6 agent synthesis."""

    link_name: str = Field(min_length=1)
    link_kind: LinkKind
    participants: list[LinkParticipantSpec]
    driving_hub: str | None = None
    degenerate_keys: list[str] = Field(default_factory=list)
    source_bindings: list[DraftSourceBinding] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)


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

    sat_name: str = Field(min_length=1)
    sat_kind: SatKind
    parent_name: str = Field(min_length=1)
    source_bindings: list[DraftSourceBinding] = Field(default_factory=list)
    extends_sat_name: str | None = None
    companion_epoch: int | None = None
    is_bitemporal: bool = False
    applied_dts_col: str | None = None
    load_mode: SatLoadMode = "INSERT_ONLY_DELTA"
    child_dependent_keys: list[str] = Field(default_factory=list)
    driving_keys: list[str] = Field(default_factory=list)
    immutable_payload_fields: list[str] = Field(default_factory=list)
    attributes: list[DraftField] = Field(default_factory=list)


class DraftShardIR(StrictModel):
    """Cluster-sharded Draft IR synthesized before deterministic L1 merge."""

    cluster_id: str
    hubs: list[DraftHubSpec] = Field(default_factory=list)
    links: list[DraftLinkSpec] = Field(default_factory=list)
    satellites: list[DraftSatelliteSpec] = Field(default_factory=list)
    column_triage: list[ColumnTriageEntry] = Field(default_factory=list)
    pending_clarifications: list[ClarificationQuestion] = Field(default_factory=list)


class DraftVaultIR(StrictModel):
    """Pre-L1/L2 Draft Vault IR authored by Step 6 agent runs."""

    ir_version: Literal["2.0"] = "2.0"
    project_id: str = Field(min_length=1)
    ontology_version: str
    wave_number: int = Field(ge=1)
    hubs: list[DraftHubSpec] = Field(default_factory=list)
    links: list[DraftLinkSpec] = Field(default_factory=list)
    satellites: list[DraftSatelliteSpec] = Field(default_factory=list)
    column_triage: list[ColumnTriageEntry] = Field(default_factory=list)
    justifications: list[AntiPatternJustification] = Field(default_factory=list)
    pending_clarifications: list[ClarificationQuestion] = Field(default_factory=list)


__all__ = [
    "ArrayType",
    "BusinessKeyPart",
    "ColumnTriageEntry",
    "DecimalType",
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
