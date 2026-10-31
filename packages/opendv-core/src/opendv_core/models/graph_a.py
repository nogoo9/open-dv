"""Pydantic v2 models for Enterprise Canonical Business Concept (CBC) Ontology (Graph A).

Validates `examples/private_equity/pe_graph_a_ontology.yaml` including 3-Tier Business Key
Authority registries, governance satellite split rules, canonical relationships, and
reference enumerations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictGraphAModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class KeyAuthorityTierSpec(StrictGraphAModel):
    """Authority rule for Tier-1 Global, Tier-2 Enterprise SoR, or Tier-3 Local Scoped BKs."""

    authority_name: str
    column_patterns: list[str] | None = None
    regex_pattern: str | None = None
    bkcc_policy: str
    require_src_qualifier: bool
    cross_source_resolution_target: str | None = None


class KeyAuthorityTiers(StrictGraphAModel):
    """3-Tier Business Key Authority hierarchy for a Canonical Business Concept."""

    tier_1_global_standard: KeyAuthorityTierSpec | None = None
    tier_2_enterprise_sor: KeyAuthorityTierSpec | None = None
    tier_3_local_scoped: KeyAuthorityTierSpec | None = None


class GovernanceConstraints(StrictGraphAModel):
    """PII and MNPI security isolation constraints for a Canonical Business Concept."""

    pii_confidential_attributes: list[str] | None = None
    mnpi_confidential_attributes: list[str] | None = None
    required_satellite_split: str | None = None
    required_extraction_satellite_split: str | None = None


class CanonicalBusinessConcept(StrictGraphAModel):
    """Core Business Concept (CBC) node in Graph A mapping to a Data Vault 2.0 Hub."""

    concept_uri: str = Field(pattern=r"^urn:ontology:(provisional:)?entity:[a-z0-9_]+$")
    concept_name: str = Field(min_length=2)
    elm_archetype: str = Field(min_length=2)
    definition: str = Field(min_length=5)
    target_hub: str | None = Field(default=None, pattern=r"^hub_[a-z0-9_]+$")
    governance_constraints: GovernanceConstraints | None = None
    key_authority_tiers: KeyAuthorityTiers | None = None


class YagniCostGuardPolicy(StrictGraphAModel):
    """Gate 4 YAGNI cost guard thresholds attached to a canonical relationship."""

    max_general_columns_threshold: int = Field(ge=1)
    min_write_amplification_mb_per_day: float = Field(ge=0.0)


class CanonicalRelationship(StrictGraphAModel):
    """Unit-of-Work Relationship / Event edge in Graph A mapping to a Data Vault 2.0 Link."""

    relationship_uri: str = Field(
        pattern=r"^urn:ontology:(provisional:)?(event|relationship):[a-z0-9_]+$"
    )
    relationship_name: str = Field(min_length=2)
    target_link: str = Field(pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    endpoints: list[str] = Field(min_length=2)
    grain_semantics: str = Field(min_length=5)
    yagni_cost_guard_policy: YagniCostGuardPolicy | None = None


class ReferenceEnumeration(StrictGraphAModel):
    """Reference enumeration specification in Graph A mapping to a `ref_*` table."""

    enum_uri: str = Field(pattern=r"^urn:ontology:enum:[a-z0-9_]+$")
    target_reference_table: str = Field(pattern=r"^ref_[a-z0-9_]+$")
    allowed_codes: list[str] = Field(min_length=1)


class GraphAOntology(StrictGraphAModel):
    """Root Semantic Ontology Graph A model (`examples/private_equity/pe_graph_a_ontology.yaml`)."""

    ontology_version: str = "1.0.0"
    domain_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    enterprise_owner: str = Field(min_length=2)
    standards_references: list[str] = Field(default_factory=list)
    canonical_business_concepts: list[CanonicalBusinessConcept] = Field(min_length=1)
    canonical_relationships: list[CanonicalRelationship] = Field(default_factory=list)
    reference_enumerations: list[ReferenceEnumeration] = Field(default_factory=list)


SemanticGraphA = GraphAOntology
CBCNode = CanonicalBusinessConcept

__all__ = [
    "CBCNode",
    "CanonicalBusinessConcept",
    "CanonicalRelationship",
    "GovernanceConstraints",
    "GraphAOntology",
    "KeyAuthorityTierSpec",
    "KeyAuthorityTiers",
    "ReferenceEnumeration",
    "SemanticGraphA",
    "YagniCostGuardPolicy",
]
