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

    tier: KeyTierEnum
    authority_name: str
    column_patterns: list[str]
    bkcc_policy: str


class ConceptAttribute(StrictModel):
    """Conceptual business attribute on a CanonicalBusinessConcept."""

    name: str = Field(min_length=1)
    logical_type: PortableLogicalType
    sql_type_hint: str
    nullable: bool
    is_business_key: bool
    is_pii: bool
    pii_category: str | None = None
    enum_uri: str | None = None


class ExternalMapping(StrictModel):
    """FIBO or external reference ontology mapping with quantized BM25 confidence."""

    standard_uri: str
    reference_pack_id: str
    reference_pack_sha256: str
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"]
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class ConceptTableBinding(StrictModel):
    """Source column grounding reference on a concept or staged candidate."""

    source_id: str
    table_id: str
    col_name: str


class CanonicalBusinessConcept(StrictModel):
    """Canonical Business Concept (CBC) node in GraphAOntology."""

    concept_uri: str = Field(min_length=1)
    concept_name: str = Field(min_length=1)
    description: str
    parent_concept_uri: str | None = None
    key_authority_tier: KeyTierEnum
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


class OntologyRelationship(StrictModel):
    """Canonical Unit-of-Work relationship between concepts in GraphAOntology."""

    relationship_uri: str = Field(min_length=1)
    relationship_name: str = Field(min_length=1)
    source_concept_uri: str
    target_concept_uri: str
    cardinality: Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"]
    target_link: str
    grain_semantics: str
    description: str


class OntologyEnumeration(StrictModel):
    """Reference code enumeration mapped to a Zone Z0 ref_* table."""

    enum_uri: str = Field(min_length=1)
    enumeration_name: str = Field(min_length=1)
    target_reference_table: str
    codes: dict[str, str]
    description: str


class GraphAOntology(StrictModel):
    """Committed or draft Graph A domain ontology."""

    ontology_version: str
    domain_namespace: str
    concepts: list[CanonicalBusinessConcept] = Field(default_factory=list)
    relationships: list[OntologyRelationship] = Field(default_factory=list)
    enumerations: list[OntologyEnumeration] = Field(default_factory=list)


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
