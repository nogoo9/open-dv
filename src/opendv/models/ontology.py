"""Layer-1 Pydantic v2 models for GraphAOntology and concept grounding metadata."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field

from opendv.models.diagnostics import (
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    KeyTierEnum,
    StrictModel,
)
from opendv.models.vault_ir import PortableLogicalType


class KeyAuthorityPattern(StrictModel):
    """3-Tier Business Key Authority pattern attached to a concept."""

    tier: KeyTierEnum = Field(description="3-tier Key Authority policy level.")
    authority_name: str = Field(description="Governing authority or standard name.")
    column_patterns: list[str] = Field(
        description="Source column naming patterns matching this key."
    )
    bkcc_policy: str = Field(description="BKCC collision-code assignment policy.")


class ConceptAttribute(StrictModel):
    """Conceptual business attribute on a CanonicalBusinessConcept."""

    name: str = Field(min_length=1, description="Canonical attribute name.")
    logical_type: PortableLogicalType = Field(description="Portable physical-neutral logical type.")
    sql_type_hint: str = Field(description="Informational SQL type hint from profiling.")
    nullable: bool = Field(description="Whether NULL values are permitted.")
    is_business_key: bool = Field(description="True if this attribute participates in the BK.")
    is_pii: bool = Field(description="True if this attribute contains PII.")
    pii_category: str | None = Field(
        default=None, description="Optional PII category classification."
    )
    enum_uri: str | None = Field(
        default=None, description="Optional reference enumeration URI for domain values."
    )


class ExternalMapping(StrictModel):
    """FIBO or external reference ontology mapping with quantized BM25 confidence."""

    standard_uri: str = Field(description="External standard concept URI (e.g. FIBO).")
    reference_pack_id: str = Field(description="Pinned reference ontology pack identifier.")
    reference_pack_sha256: str = Field(description="SHA-256 digest of the pinned reference pack.")
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = Field(
        description="SKOS alignment strength."
    )
    confidence: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"), description="Quantized alignment confidence [0, 1]."
    )


class ConceptTableBinding(StrictModel):
    """Source column grounding reference on a concept or staged candidate."""

    source_id: str = Field(description="Source system identifier.")
    table_id: str = Field(description="Source table identifier.")
    col_name: str = Field(description="Source column name grounded to this concept.")


class CanonicalBusinessConcept(StrictModel):
    """Canonical Business Concept (CBC) node in GraphAOntology."""

    concept_uri: str = Field(min_length=1, description="Unique canonical concept URI.")
    concept_name: str = Field(min_length=1, description="Human-readable concept name.")
    description: str = Field(description="Business definition of the concept.")
    parent_concept_uri: str | None = Field(
        default=None, description="Optional super-concept URI for specialization."
    )
    key_authority_tier: KeyTierEnum = Field(description="Primary 3-tier Key Authority policy.")
    key_authority_patterns: list[KeyAuthorityPattern] = Field(
        default_factory=list, description="Key authority patterns across sources."
    )
    bkcc_default: str | None = Field(
        default=None, description="Default BKCC scope when tier permits static override."
    )
    governance_tags: list[str] = Field(
        default_factory=list, description="Governance and compliance classification tags."
    )
    governance_constraints: dict[str, str | list[str]] = Field(
        default_factory=dict, description="Structured governance policy constraints."
    )
    attributes: list[ConceptAttribute] = Field(
        default_factory=list, description="Conceptual attributes belonging to this CBC."
    )
    synonyms: list[str] = Field(
        default_factory=list, description="Known business or source-system synonyms."
    )
    external_mappings: list[ExternalMapping] = Field(
        default_factory=list, description="Verified FIBO or external standard mappings."
    )
    table_bindings: list[ConceptTableBinding] = Field(
        default_factory=list, description="Grounded source table/column bindings."
    )
    source_citations: list[DocumentCitationRef] = Field(
        default_factory=list, description="Verbatim document citations supporting this CBC."
    )
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(
        default_factory=list, description="External MCP evidence receipts supporting this CBC."
    )


class OntologyRelationship(StrictModel):
    """Canonical Unit-of-Work relationship between concepts in GraphAOntology."""

    relationship_uri: str = Field(min_length=1, description="Unique canonical relationship URI.")
    relationship_name: str = Field(min_length=1, description="Human-readable relationship name.")
    source_concept_uri: str = Field(description="Origin concept URI.")
    target_concept_uri: str = Field(description="Target concept URI.")
    cardinality: Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"] = Field(
        description="Structural relationship cardinality."
    )
    target_link: str = Field(description="Intended Zone Z2 Link entity name (lnk_*).")
    grain_semantics: str = Field(description="Unit-of-Work grain definition for the relationship.")
    description: str = Field(description="Business meaning of the relationship.")


class OntologyEnumeration(StrictModel):
    """Reference code enumeration mapped to a Zone Z0 ref_* table."""

    enum_uri: str = Field(min_length=1, description="Unique reference enumeration URI.")
    enumeration_name: str = Field(min_length=1, description="Enumeration name.")
    target_reference_table: str = Field(description="Target Zone Z0 reference table name (ref_*).")
    codes: dict[str, str] = Field(description="Mapping of canonical code values to labels.")
    description: str = Field(description="Business definition of the enumeration.")


class GraphAOntology(StrictModel):
    """Committed or draft Graph A domain ontology."""

    ontology_version: str = Field(description="Semantic or monotonic ontology version string.")
    domain_namespace: str = Field(description="Domain namespace prefix for concept URIs.")
    concepts: list[CanonicalBusinessConcept] = Field(
        default_factory=list, description="Canonical Business Concepts in the domain."
    )
    relationships: list[OntologyRelationship] = Field(
        default_factory=list, description="Canonical Unit-of-Work relationships."
    )
    enumerations: list[OntologyEnumeration] = Field(
        default_factory=list, description="Reference code enumerations."
    )


__all__ = [
    "CanonicalBusinessConcept",
    "ConceptAttribute",
    "ConceptTableBinding",
    "DocumentCitationRef",
    "ExternalMapping",
    "ExternalMcpEvidenceRef",
    "GraphAOntology",
    "KeyAuthorityPattern",
    "KeyTierEnum",
    "OntologyEnumeration",
    "OntologyRelationship",
]
