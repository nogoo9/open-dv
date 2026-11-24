"""34-operation discriminated OntologyDelta union and evidence grounding models."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from opendv.models.diagnostics import (
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    KeyTierEnum,
    StrictModel,
)
from opendv.models.ontology import (
    ConceptAttribute,
    ConceptTableBinding,
    ExternalMapping,
    KeyAuthorityPattern,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.vault_ir import PortableLogicalType


class EvidenceGroundingApproval(StrictModel):
    """Explicit evidence grounding approval tying a concept to formatted source_refs."""

    concept_uri: str
    rationale: str
    source_refs: list[str]


class GroundingWaiverMixin(StrictModel):
    """Waiver and explicit grounding payload attached to ontology commit requests."""

    low_confidence_grounding_waiver: bool = False
    grounding_rationale: str | None = None
    low_confidence_waiver_concepts: list[str] | None = None
    evidence_grounding: list[EvidenceGroundingApproval] | None = None


class BaseOntologyDeltaOp(StrictModel):
    """Shared strict base for all 34 PascalCase ontology delta operations."""

    op_id: str | None = None
    rationale: str = ""


# ---------------------------------------------------------------------------
# 16 Agent-Additive Operations (_AOps)
# ---------------------------------------------------------------------------


class AddConcept(BaseOntologyDeltaOp):
    """Introduce a new CanonicalBusinessConcept."""

    op_type: Literal["AddConcept"] = "AddConcept"
    concept_uri: str = Field(min_length=1)
    concept_name: str = Field(min_length=1)
    description: str = ""
    parent_concept_uri: str | None = None
    key_authority_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR"
    key_authority_patterns: list[KeyAuthorityPattern] = Field(default_factory=list)
    bkcc_default: str | None = None
    governance_tags: list[str] = Field(default_factory=list)
    governance_constraints: dict[str, str | list[str]] = Field(default_factory=dict)
    attributes: list[ConceptAttribute] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    external_mappings: list[ExternalMapping] = Field(default_factory=list)
    table_bindings: list[ConceptTableBinding] = Field(default_factory=list)
    source_citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(default_factory=list)


class AddAttribute(BaseOntologyDeltaOp):
    """Attach a ConceptAttribute to an existing concept."""

    op_type: Literal["AddAttribute"] = "AddAttribute"
    concept_uri: str
    attribute: ConceptAttribute


class AddRelationship(BaseOntologyDeltaOp):
    """Introduce an OntologyRelationship."""

    op_type: Literal["AddRelationship"] = "AddRelationship"
    relationship: OntologyRelationship


class AddEnumeration(BaseOntologyDeltaOp):
    """Introduce an OntologyEnumeration."""

    op_type: Literal["AddEnumeration"] = "AddEnumeration"
    enumeration: OntologyEnumeration


class AddEnumerationCode(BaseOntologyDeltaOp):
    """Append a code/description entry to an existing enumeration."""

    op_type: Literal["AddEnumerationCode"] = "AddEnumerationCode"
    enum_uri: str
    code: str = Field(min_length=1)
    description: str


class AddSynonym(BaseOntologyDeltaOp):
    """Append a lexical synonym to a concept."""

    op_type: Literal["AddSynonym"] = "AddSynonym"
    concept_uri: str
    synonym: str = Field(min_length=1)


class AddExternalMapping(BaseOntologyDeltaOp):
    """Attach an ExternalMapping to a concept."""

    op_type: Literal["AddExternalMapping"] = "AddExternalMapping"
    concept_uri: str
    mapping: ExternalMapping


class SetParentConcept(BaseOntologyDeltaOp):
    """Set parent_concept_uri when currently unset."""

    op_type: Literal["SetParentConcept"] = "SetParentConcept"
    concept_uri: str
    parent_concept_uri: str


class SetDescription(BaseOntologyDeltaOp):
    """Set concept description when blank or additive."""

    op_type: Literal["SetDescription"] = "SetDescription"
    concept_uri: str
    description: str


class SetConceptKeyAuthority(BaseOntologyDeltaOp):
    """Configure key authority tier and optional default BKCC on an unlocked concept."""

    op_type: Literal["SetConceptKeyAuthority"] = "SetConceptKeyAuthority"
    concept_uri: str
    key_authority_tier: KeyTierEnum
    bkcc_default: str | None = None


class SetConceptGovernanceTags(BaseOntologyDeltaOp):
    """Set governance tags and constraints on a concept."""

    op_type: Literal["SetConceptGovernanceTags"] = "SetConceptGovernanceTags"
    concept_uri: str
    governance_tags: list[str] = Field(default_factory=list)
    governance_constraints: dict[str, str | list[str]] = Field(default_factory=dict)


class UpdateAttributeMetadata(BaseOntologyDeltaOp):
    """Update non-breaking metadata on a ConceptAttribute."""

    op_type: Literal["UpdateAttributeMetadata"] = "UpdateAttributeMetadata"
    concept_uri: str
    attribute_name: str = Field(min_length=1)
    logical_type: PortableLogicalType | None = None
    sql_type_hint: str | None = None
    nullable: bool | None = None
    is_business_key: bool | None = None
    is_pii: bool | None = None
    pii_category: str | None = None
    enum_uri: str | None = None


class AddTableBinding(BaseOntologyDeltaOp):
    """Bind a source table/column to a concept."""

    op_type: Literal["AddTableBinding"] = "AddTableBinding"
    concept_uri: str
    binding: ConceptTableBinding


class AddSourceCitation(BaseOntologyDeltaOp):
    """Attach a DocumentCitationRef to a concept."""

    op_type: Literal["AddSourceCitation"] = "AddSourceCitation"
    concept_uri: str
    citation: DocumentCitationRef


class AddMcpEvidence(BaseOntologyDeltaOp):
    """Attach an ExternalMcpEvidenceRef to a concept."""

    op_type: Literal["AddMcpEvidence"] = "AddMcpEvidence"
    concept_uri: str
    evidence: ExternalMcpEvidenceRef


class AddKeyAuthorityPattern(BaseOntologyDeltaOp):
    """Attach a KeyAuthorityPattern to a concept."""

    op_type: Literal["AddKeyAuthorityPattern"] = "AddKeyAuthorityPattern"
    concept_uri: str
    pattern: KeyAuthorityPattern


# ---------------------------------------------------------------------------
# 17 Human-Only Destructive/Steward Operations (_DOps)
# ---------------------------------------------------------------------------


class ReparentConcept(BaseOntologyDeltaOp):
    """Change a concept's existing parent_concept_uri."""

    op_type: Literal["ReparentConcept"] = "ReparentConcept"
    concept_uri: str
    parent_concept_uri: str


class ClearParentConcept(BaseOntologyDeltaOp):
    """Clear a concept's parent_concept_uri."""

    op_type: Literal["ClearParentConcept"] = "ClearParentConcept"
    concept_uri: str


class DeleteConcept(BaseOntologyDeltaOp):
    """Delete an unlocked concept from GraphAOntology."""

    op_type: Literal["DeleteConcept"] = "DeleteConcept"
    concept_uri: str


class RenameConcept(BaseOntologyDeltaOp):
    """Rename a concept's human-readable concept_name."""

    op_type: Literal["RenameConcept"] = "RenameConcept"
    concept_uri: str
    new_concept_name: str = Field(min_length=1)


class RemoveAttribute(BaseOntologyDeltaOp):
    """Remove an attribute from a concept."""

    op_type: Literal["RemoveAttribute"] = "RemoveAttribute"
    concept_uri: str
    attribute_name: str = Field(min_length=1)


class RemoveExternalMapping(BaseOntologyDeltaOp):
    """Remove an external mapping from a concept."""

    op_type: Literal["RemoveExternalMapping"] = "RemoveExternalMapping"
    concept_uri: str
    standard_uri: str


class RemoveSynonym(BaseOntologyDeltaOp):
    """Remove a lexical synonym from a concept."""

    op_type: Literal["RemoveSynonym"] = "RemoveSynonym"
    concept_uri: str
    synonym: str


class DeleteRelationship(BaseOntologyDeltaOp):
    """Delete an OntologyRelationship."""

    op_type: Literal["DeleteRelationship"] = "DeleteRelationship"
    relationship_uri: str


class DeleteEnumeration(BaseOntologyDeltaOp):
    """Delete an OntologyEnumeration."""

    op_type: Literal["DeleteEnumeration"] = "DeleteEnumeration"
    enum_uri: str


class RemoveEnumerationCode(BaseOntologyDeltaOp):
    """Remove a code from an OntologyEnumeration."""

    op_type: Literal["RemoveEnumerationCode"] = "RemoveEnumerationCode"
    enum_uri: str
    code: str


class ChangeDomainNamespace(BaseOntologyDeltaOp):
    """Update the ontology's domain_namespace."""

    op_type: Literal["ChangeDomainNamespace"] = "ChangeDomainNamespace"
    domain_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class ReviseDescription(BaseOntologyDeltaOp):
    """Overwrite a concept's non-empty description."""

    op_type: Literal["ReviseDescription"] = "ReviseDescription"
    concept_uri: str
    description: str


class RepinExternalMapping(BaseOntologyDeltaOp):
    """Repin an external mapping onto a different reference pack or predicate."""

    op_type: Literal["RepinExternalMapping"] = "RepinExternalMapping"
    concept_uri: str
    standard_uri: str
    reference_pack_id: str
    reference_pack_sha256: str
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = "skos:exactMatch"


class RemoveTableBinding(BaseOntologyDeltaOp):
    """Remove a source table binding from a concept."""

    op_type: Literal["RemoveTableBinding"] = "RemoveTableBinding"
    concept_uri: str
    source_id: str
    table_id: str
    col_name: str


class RemoveSourceCitation(BaseOntologyDeltaOp):
    """Remove a document citation reference from a concept."""

    op_type: Literal["RemoveSourceCitation"] = "RemoveSourceCitation"
    concept_uri: str
    doc_id: str
    page_number: int = Field(ge=1)
    verbatim_quote: str
    occurrence: int = Field(default=1, ge=1)


class RemoveMcpEvidence(BaseOntologyDeltaOp):
    """Remove an external MCP evidence reference from a concept."""

    op_type: Literal["RemoveMcpEvidence"] = "RemoveMcpEvidence"
    concept_uri: str
    receipt_id: str


class RemoveKeyAuthorityPattern(BaseOntologyDeltaOp):
    """Remove a KeyAuthorityPattern from a concept."""

    op_type: Literal["RemoveKeyAuthorityPattern"] = "RemoveKeyAuthorityPattern"
    concept_uri: str
    authority_name: str


# ---------------------------------------------------------------------------
# 1 Server-Only Operation
# ---------------------------------------------------------------------------


class RescoreExternalMapping(BaseOntologyDeltaOp):
    """Server-only operation updating BM25 confidence on an ExternalMapping."""

    op_type: Literal["RescoreExternalMapping"] = "RescoreExternalMapping"
    concept_uri: str
    standard_uri: str
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] | None = None
    reference_pack_id: str | None = None
    reference_pack_sha256: str | None = None


OntologyDelta = Annotated[
    AddConcept
    | AddAttribute
    | AddRelationship
    | AddEnumeration
    | AddEnumerationCode
    | AddSynonym
    | AddExternalMapping
    | SetParentConcept
    | SetDescription
    | SetConceptKeyAuthority
    | SetConceptGovernanceTags
    | UpdateAttributeMetadata
    | AddTableBinding
    | AddSourceCitation
    | AddMcpEvidence
    | AddKeyAuthorityPattern
    | ReparentConcept
    | ClearParentConcept
    | DeleteConcept
    | RenameConcept
    | RemoveAttribute
    | RemoveExternalMapping
    | RemoveSynonym
    | DeleteRelationship
    | DeleteEnumeration
    | RemoveEnumerationCode
    | ChangeDomainNamespace
    | ReviseDescription
    | RepinExternalMapping
    | RemoveTableBinding
    | RemoveSourceCitation
    | RemoveMcpEvidence
    | RemoveKeyAuthorityPattern
    | RescoreExternalMapping,
    Field(discriminator="op_type"),
]

AGENT_ADDITIVE_OPS: frozenset[str] = frozenset(
    {
        "AddConcept",
        "AddAttribute",
        "AddRelationship",
        "AddEnumeration",
        "AddEnumerationCode",
        "AddSynonym",
        "AddExternalMapping",
        "SetParentConcept",
        "SetDescription",
        "SetConceptKeyAuthority",
        "SetConceptGovernanceTags",
        "UpdateAttributeMetadata",
        "AddTableBinding",
        "AddSourceCitation",
        "AddMcpEvidence",
        "AddKeyAuthorityPattern",
    }
)

HUMAN_ONLY_OPS: frozenset[str] = frozenset(
    {
        "ReparentConcept",
        "ClearParentConcept",
        "DeleteConcept",
        "RenameConcept",
        "RemoveAttribute",
        "RemoveExternalMapping",
        "RemoveSynonym",
        "DeleteRelationship",
        "DeleteEnumeration",
        "RemoveEnumerationCode",
        "ChangeDomainNamespace",
        "ReviseDescription",
        "RepinExternalMapping",
        "RemoveTableBinding",
        "RemoveSourceCitation",
        "RemoveMcpEvidence",
        "RemoveKeyAuthorityPattern",
    }
)

SERVER_ONLY_OPS: frozenset[str] = frozenset({"RescoreExternalMapping"})

__all__ = [
    "AGENT_ADDITIVE_OPS",
    "HUMAN_ONLY_OPS",
    "SERVER_ONLY_OPS",
    "AddAttribute",
    "AddConcept",
    "AddEnumeration",
    "AddEnumerationCode",
    "AddExternalMapping",
    "AddKeyAuthorityPattern",
    "AddMcpEvidence",
    "AddRelationship",
    "AddSourceCitation",
    "AddSynonym",
    "AddTableBinding",
    "BaseOntologyDeltaOp",
    "ChangeDomainNamespace",
    "ClearParentConcept",
    "DeleteConcept",
    "DeleteEnumeration",
    "DeleteRelationship",
    "EvidenceGroundingApproval",
    "GroundingWaiverMixin",
    "OntologyDelta",
    "RemoveAttribute",
    "RemoveEnumerationCode",
    "RemoveExternalMapping",
    "RemoveKeyAuthorityPattern",
    "RemoveMcpEvidence",
    "RemoveSourceCitation",
    "RemoveSynonym",
    "RemoveTableBinding",
    "RenameConcept",
    "ReparentConcept",
    "RepinExternalMapping",
    "RescoreExternalMapping",
    "ReviseDescription",
    "SetConceptGovernanceTags",
    "SetConceptKeyAuthority",
    "SetDescription",
    "SetParentConcept",
    "UpdateAttributeMetadata",
]
