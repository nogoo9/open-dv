"""Pydantic v2 models for Enterprise Canonical Business Concept (CBC) Ontology (Graph A).

Validates `examples/private_equity/pe_graph_a_ontology.yaml` including 3-Tier Business Key
Authority registries, governance satellite split rules, canonical relationships, and
reference enumerations.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictGraphAModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class KeyAuthorityTierSpec(StrictGraphAModel):
    """Authority rule for Tier-1 Global, Tier-2 Enterprise SoR, or Tier-3 Local Scoped BKs."""

    authority_name: str = "DEFAULT"
    column_patterns: list[str] | None = None
    regex_pattern: str | None = None
    bkcc_policy: str = "GLOBAL"
    require_src_qualifier: bool = False
    cross_source_resolution_target: str | None = None
    authoritative_sor: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_tier(cls, data: Any) -> Any:
        if isinstance(data, dict):
            t_dict = dict(data)
            if not t_dict.get("authority_name"):
                sor = t_dict.get("authoritative_sor")
                if sor and isinstance(sor, list) and sor:
                    t_dict["authority_name"] = str(sor[0])
                else:
                    t_dict["authority_name"] = str(t_dict.get("bkcc_policy", "STANDARD_AUTHORITY"))
            if "require_src_qualifier" not in t_dict:
                t_dict["require_src_qualifier"] = t_dict.get("bkcc_policy") != "GLOBAL"
            return t_dict
        return data


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

    concept_uri: str = Field(pattern=r"^urn:ontology:entity:[a-z0-9_]+$")
    concept_name: str = Field(default="")
    elm_archetype: str = Field(default="CORE_CONCEPT")
    definition: str = Field(default="Canonical business concept.")
    target_hub: str | None = Field(default=None, pattern=r"^hub_[a-z0-9_]+$")
    governance_constraints: GovernanceConstraints | None = None
    key_authority_tiers: KeyAuthorityTiers | None = None
    concept_id: str | None = None
    label: str | None = None
    taxonomy_class: str | None = None
    description: str | None = None
    authoritative_sor: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_concept(cls, data: Any) -> Any:
        if isinstance(data, dict):
            c_dict = dict(data)
            if not c_dict.get("concept_name") and c_dict.get("label"):
                c_dict["concept_name"] = str(c_dict["label"])
            elif not c_dict.get("concept_name") and c_dict.get("concept_uri"):
                uri_slug = str(c_dict["concept_uri"]).split(":")[-1]
                c_dict["concept_name"] = uri_slug.replace("_", " ").title()
            if not c_dict.get("elm_archetype") and c_dict.get("taxonomy_class"):
                c_dict["elm_archetype"] = str(c_dict["taxonomy_class"])
            if not c_dict.get("definition"):
                name = c_dict.get("concept_name") or c_dict.get("label") or "concept"
                c_dict["definition"] = (
                    str(c_dict.get("description"))
                    if c_dict.get("description")
                    else f"Canonical concept: {name}."
                )
            return c_dict
        return data


class YagniCostGuardPolicy(StrictGraphAModel):
    """Gate 4 YAGNI cost guard thresholds attached to a canonical relationship."""

    max_general_columns_threshold: int = Field(ge=1)
    min_write_amplification_mb_per_day: float = Field(ge=0.0)


class CanonicalRelationship(StrictGraphAModel):
    """Unit-of-Work Relationship / Event edge in Graph A mapping to a Data Vault 2.0 Link."""

    relationship_uri: str = Field(pattern=r"^urn:ontology:(event|relationship):[a-z0-9_]+$")
    relationship_name: str = Field(default="")
    target_link: str = Field(pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    endpoints: list[str] = Field(min_length=2)
    grain_semantics: str = Field(default="Canonical relationship edge.")
    yagni_cost_guard_policy: YagniCostGuardPolicy | None = None
    relationship_id: str | None = None
    label: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_rel(cls, data: Any) -> Any:
        if isinstance(data, dict):
            r_dict = dict(data)
            if not r_dict.get("relationship_name") and r_dict.get("label"):
                r_dict["relationship_name"] = str(r_dict["label"])
            elif not r_dict.get("relationship_name") and r_dict.get("relationship_uri"):
                uri_part = str(r_dict["relationship_uri"]).split(":")[-1]
                r_dict["relationship_name"] = uri_part.replace("_", " ").title()
            if not r_dict.get("grain_semantics"):
                name = r_dict.get("relationship_name") or r_dict.get("label") or "relationship"
                r_dict["grain_semantics"] = f"Unit of work edge for {name}."
            if not r_dict.get("target_link"):
                uri = str(r_dict.get("relationship_uri", ""))
                suffix = uri.split(":")[-1] if uri else "rel"
                r_dict["target_link"] = f"lnk_{suffix}"
            return r_dict
        return data


class ReferenceEnumeration(StrictGraphAModel):
    """Reference enumeration specification in Graph A mapping to a `ref_*` table."""

    enum_uri: str = Field(pattern=r"^urn:ontology:enum:[a-z0-9_]+$")
    target_reference_table: str = Field(pattern=r"^ref_[a-z0-9_]+$")
    allowed_codes: list[str] = Field(min_length=1)


class GraphAOntology(StrictGraphAModel):
    """Root Semantic Ontology Graph A model (`examples/private_equity/pe_graph_a_ontology.yaml`)."""

    ontology_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    parent_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description=(
            "SHA-256 of the immediate parent Graph A ontology from which this version was derived."
        ),
    )
    decision_refs: list[str] = Field(
        default_factory=list,
        description="IDs of Steward decisions that produced this version.",
    )
    domain_namespace: str = Field(default="general", pattern=r"^[a-z][a-z0-9_]*$")
    enterprise_owner: str = Field(default="Enterprise Architecture", min_length=2)
    standards_references: list[str] = Field(default_factory=list)
    canonical_business_concepts: list[CanonicalBusinessConcept] = Field(min_length=1)
    canonical_relationships: list[CanonicalRelationship] = Field(default_factory=list)
    reference_enumerations: list[ReferenceEnumeration] = Field(default_factory=list)
    ontology_id: str | None = None
    version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_ontology(cls, data: Any) -> Any:
        if isinstance(data, dict):
            o_dict = dict(data)
            if not o_dict.get("ontology_version") and o_dict.get("version"):
                o_dict["ontology_version"] = str(o_dict["version"])
            if not o_dict.get("domain_namespace"):
                ont_id = str(o_dict.get("ontology_id", ""))
                if ont_id:
                    cleaned = ont_id.split("_ontology")[0].lower()
                    import re

                    cleaned = re.sub(r"[^a-z0-9_]", "_", cleaned)
                    o_dict["domain_namespace"] = cleaned if cleaned else "general"
                else:
                    o_dict["domain_namespace"] = "general"
            if not o_dict.get("enterprise_owner"):
                o_dict["enterprise_owner"] = "Enterprise Architecture"
            return o_dict
        return data


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
