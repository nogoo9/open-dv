"""Pydantic v2 models for Enterprise Canonical Business Concept (CBC) Ontology (Graph A).

Validates `examples/private_equity/pe_graph_a_ontology.yaml` including 3-Tier Business Key
Authority registries, governance satellite split rules, canonical relationships, and
reference enumerations.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictGraphAModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _validate_json_metadata(v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    """Validate metadata dictionary contains only finite, JSON-representable values."""
    if v is None:
        return None

    def _check_finite(val: Any) -> None:
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                raise ValueError(f"Non-finite float {val} is not representable in canonical JSON")
        elif isinstance(val, dict):
            for k, subv in val.items():
                if not isinstance(k, str):
                    raise ValueError("Metadata dictionary keys must be strings")
                _check_finite(subv)
        elif isinstance(val, list):
            for item in val:
                _check_finite(item)

    _check_finite(v)
    return v


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


class ConceptAttribute(StrictGraphAModel):
    """Normalized conceptual attribute associated with a Canonical Business Concept."""

    attribute_name: str = Field(
        pattern=r"^[a-z0-9_]{1,64}$",
        description="Normalized conceptual attribute identifier (lowercase, 1..64 chars)",
    )
    data_type: str = Field(
        default="string",
        description=(
            "Logical data type classification "
            "(e.g. string, integer, decimal, boolean, date, timestamp, binary)"
        ),
    )
    description: str | None = Field(
        default=None,
        description="Conceptual definition / description of the attribute",
    )
    is_optional: bool | None = Field(
        default=None,
        description="Whether the attribute is optional in Data Vault satellite storage",
    )
    example_values: list[str] | None = Field(
        default=None,
        description="Sample representative values extracted from source evidence",
    )
    deprecated: bool | None = Field(
        default=None,
        description="Deprecation flag under human edit governance",
    )
    metadata: dict[str, JsonValue] | None = Field(
        default=None,
        description="Arbitrary conceptual metadata key-values",
    )
    source_hints: list[str] | None = Field(
        default=None,
        description="Provenance hints identifying source data feeds or columns",
    )

    @property
    def definition(self) -> str | None:
        """Alias for description."""
        return self.description

    @model_validator(mode="before")
    @classmethod
    def _normalize_attribute(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "name" in d:
                val = d.pop("name")
                if "attribute_name" in d and d["attribute_name"] != val:
                    msg = f"attribute_name '{d['attribute_name']}' != name '{val}'"
                    raise ValueError(f"Conflicting attribute name: {msg}")
                d.setdefault("attribute_name", val)
            if "definition" in d:
                val = d.pop("definition")
                if "description" in d and d["description"] != val:
                    msg = f"description '{d['description']}' != definition '{val}'"
                    raise ValueError(f"Conflicting description: {msg}")
                d.setdefault("description", val)
            return d
        return data

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)


class ExternalMapping(StrictGraphAModel):
    """Grounded external reference ontology mapping (e.g. FIBO, ISO 20022, Wikidata)."""

    scheme: str = Field(
        min_length=1,
        description=(
            "Registered standard/scheme name "
            "(e.g. 'fibo', 'edmcouncil', 'iso20022', 'wikidata', 'custom')"
        ),
    )
    target_uri: str = Field(
        min_length=1,
        description="Target reference ontology URI",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0 (quantized to 4 decimal places)",
    )
    predicate: str | None = Field(
        default=None,
        description=(
            "Mapping relation predicate "
            "(e.g. 'skos:exactMatch', 'skos:closeMatch', 'skos:broadMatch', 'skos:relatedMatch')"
        ),
    )
    pinned: bool | None = Field(
        default=None,
        description="Whether mapping is pinned against automated re-scoring overrides",
    )
    provenance: str | None = Field(
        default=None,
        description=(
            "Actor or pipeline attribution that authored the mapping "
            "(e.g. 'human', 'agent', 'deterministic')"
        ),
    )
    reference_pack_id: str | None = Field(
        default=None,
        description="Registered reference ontology pack ID",
    )
    reference_pack_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 digest of reference ontology pack",
    )

    @property
    def standard_name(self) -> str:
        """Alias for scheme."""
        return self.scheme

    @property
    def standard_uri(self) -> str:
        """Alias for target_uri."""
        return self.target_uri

    @property
    def mapping_type(self) -> str | None:
        """Alias for predicate."""
        return self.predicate

    @model_validator(mode="before")
    @classmethod
    def _normalize_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "standard_name" in d:
                val = d.pop("standard_name")
                if "scheme" in d and d["scheme"] != val:
                    raise ValueError(
                        f"Conflicting alias: scheme '{d['scheme']}' != standard_name '{val}'"
                    )
                d.setdefault("scheme", val)
            if "standard_uri" in d:
                val = d.pop("standard_uri")
                if "target_uri" in d and d["target_uri"] != val:
                    raise ValueError(
                        f"Conflicting alias: target_uri '{d['target_uri']}' != standard_uri '{val}'"
                    )
                d.setdefault("target_uri", val)
            if "mapping_type" in d:
                val = d.pop("mapping_type")
                if "predicate" in d and d["predicate"] != val:
                    raise ValueError(
                        f"Conflicting alias: predicate '{d['predicate']}' != mapping_type '{val}'"
                    )
                d.setdefault("predicate", val)
            return d
        return data

    @field_validator("confidence")
    @classmethod
    def _quantize_confidence(cls, v: float | None) -> float | None:
        if v is None:
            return None
        return float(Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN))


class CanonicalBusinessConcept(StrictGraphAModel):
    """Core Business Concept (CBC) node in Graph A mapping to a Data Vault 2.0 Hub."""

    concept_uri: str = Field(pattern=r"^urn:(ontology:entity|opendv:concept):[a-z0-9_]+$")
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

    # Extension fields (strictly defaulting to None for backward compatibility)
    parent_concept_uri: str | None = Field(
        default=None,
        pattern=r"^urn:(ontology:entity|opendv:concept):[a-z0-9_]+$",
        description="Hierarchical parent concept URI within Graph A ontology",
    )
    attributes: list[ConceptAttribute] | None = Field(
        default=None,
        description="Associated conceptual attributes",
    )
    synonyms: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Discovered lexical synonyms (1..256 characters each)",
    )
    external_mappings: list[ExternalMapping] | None = Field(
        default=None,
        description="Grounded external ontology mappings (e.g. FIBO)",
    )
    deprecated: bool | None = Field(
        default=None,
        description="Deprecation flag under human edit governance",
    )
    metadata: dict[str, JsonValue] | None = Field(
        default=None,
        description="Arbitrary conceptual metadata key-values",
    )
    source_hints: list[str] | None = Field(
        default=None,
        description="Provenance hints identifying source data feeds",
    )

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

    @field_validator("synonyms")
    @classmethod
    def _validate_synonyms(cls, syns: list[str] | None) -> list[str] | None:
        if syns is None:
            return None
        for s in syns:
            cleaned = s.strip()
            if not (1 <= len(cleaned) <= 256):
                raise ValueError(f"Synonym must be between 1 and 256 characters (trimmed): '{s}'")
        normalized = [s.strip().casefold() for s in syns]
        if len(normalized) != len(set(normalized)):
            raise ValueError("DUPLICATE_SYNONYM: synonyms list contains duplicate values")
        return syns

    @field_validator("attributes")
    @classmethod
    def _validate_attributes(
        cls, attrs: list[ConceptAttribute] | None
    ) -> list[ConceptAttribute] | None:
        if attrs is None:
            return None
        names = [a.attribute_name for a in attrs]
        if len(names) != len(set(names)):
            raise ValueError(
                "DUPLICATE_ATTRIBUTE_NAME: attributes list contains duplicate attribute names"
            )
        return attrs

    @field_validator("external_mappings")
    @classmethod
    def _validate_external_mappings(
        cls, mappings: list[ExternalMapping] | None
    ) -> list[ExternalMapping] | None:
        if mappings is None:
            return None
        seen: dict[str, set[str | None]] = {}
        for m in mappings:
            if m.target_uri not in seen:
                seen[m.target_uri] = set()
            if (
                None in seen[m.target_uri]
                or (m.predicate is None and len(seen[m.target_uri]) > 0)
                or m.predicate in seen[m.target_uri]
            ):
                raise ValueError(
                    f"AMBIGUOUS_EXTERNAL_MAPPING: duplicate mapping for '{m.target_uri}'"
                )
            seen[m.target_uri].add(m.predicate)
        return mappings

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)


class YagniCostGuardPolicy(StrictGraphAModel):
    """Gate 4 YAGNI cost guard thresholds attached to a canonical relationship."""

    max_general_columns_threshold: int = Field(ge=1)
    min_write_amplification_mb_per_day: float = Field(ge=0.0)


class CanonicalRelationship(StrictGraphAModel):
    """Unit-of-Work Relationship / Event edge in Graph A mapping to a Data Vault 2.0 Link."""

    relationship_uri: str = Field(
        pattern=r"^urn:(ontology:(event|relationship)|opendv:(event|relationship)):[a-z0-9_]+$"
    )
    relationship_name: str = Field(default="")
    target_link: str = Field(pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    endpoints: list[str] = Field(min_length=2)
    grain_semantics: str = Field(default="Canonical relationship edge.")
    yagni_cost_guard_policy: YagniCostGuardPolicy | None = None
    relationship_id: str | None = None
    label: str | None = None

    # Extension fields (strictly defaulting to None for backward compatibility)
    description: str | None = Field(default=None, description="Relationship description")
    cardinality: str | None = Field(default=None, description="Relationship cardinality")
    deprecated: bool | None = Field(default=None, description="Deprecation flag")
    metadata: dict[str, JsonValue] | None = Field(default=None, description="Relationship metadata")
    source_hints: list[str] | None = Field(default=None, description="Source provenance hints")

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

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)


class ReferenceEnumeration(StrictGraphAModel):
    """Reference enumeration specification in Graph A mapping to a `ref_*` table."""

    enum_uri: str = Field(pattern=r"^urn:(ontology|opendv):enum:[a-z0-9_]+$")
    target_reference_table: str = Field(pattern=r"^ref_[a-z0-9_]+$")
    allowed_codes: list[str] = Field(min_length=1)

    # Extension fields (strictly defaulting to None for backward compatibility)
    enumeration_name: str | None = Field(
        default=None, description="Human-readable enumeration name"
    )
    description: str | None = Field(default=None, description="Enumeration description")
    code_descriptions: dict[str, str] | None = Field(
        default=None,
        description="Descriptions for individual allowed codes",
    )
    deprecated: bool | None = Field(default=None, description="Deprecation flag")
    metadata: dict[str, JsonValue] | None = Field(default=None, description="Enumeration metadata")

    @model_validator(mode="after")
    def _validate_enum_codes(self) -> Self:
        if (
            self.code_descriptions
            and self.allowed_codes
            and not set(self.code_descriptions.keys()).issubset(set(self.allowed_codes))
        ):
            raise ValueError(
                "DANGLING_ENUM_CODE_DESCRIPTION: code_descriptions keys must be in allowed_codes"
            )
        return self

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)


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
    canonical_business_concepts: list[CanonicalBusinessConcept] = Field(default_factory=list)
    canonical_relationships: list[CanonicalRelationship] = Field(default_factory=list)
    reference_enumerations: list[ReferenceEnumeration] = Field(default_factory=list)
    ontology_id: str | None = None
    version: str | None = None

    # Extension fields (strictly defaulting to None for backward compatibility)
    description: str | None = Field(default=None, description="Ontology description")
    metadata: dict[str, JsonValue] | None = Field(
        default=None, description="Ontology-level metadata"
    )

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

    @model_validator(mode="after")
    def _validate_version_consistency(self) -> Self:
        if (
            self.version is not None
            and self.ontology_version
            and self.version != self.ontology_version
        ):
            msg = f"version '{self.version}' != ontology_version '{self.ontology_version}'"
            raise ValueError(f"VERSION_CONSISTENCY_MISMATCH: {msg}")
        major = int(self.ontology_version.split(".", 1)[0])
        if major >= 1 and len(self.canonical_business_concepts) < 1:
            raise ValueError(
                "ONTOLOGY_REQUIRES_CONCEPT: canonical_business_concepts must contain at least "
                f"1 concept for ontology_version '{self.ontology_version}' (>= 1.0.0)"
            )
        return self

    def get_concept(self, concept_uri: str) -> CanonicalBusinessConcept | None:
        """Look up a Canonical Business Concept by its canonical URN."""
        for concept in self.canonical_business_concepts:
            if concept.concept_uri == concept_uri:
                return concept
        return None

    @model_validator(mode="after")
    def _validate_concept_hierarchy(self) -> Self:
        if not self.canonical_business_concepts:
            return self

        concept_uris: set[str] = set()
        for c in self.canonical_business_concepts:
            if c.concept_uri in concept_uris:
                raise ValueError(
                    f"DUPLICATE_CONCEPT_URI: Duplicate concept URI '{c.concept_uri}' in ontology"
                )
            concept_uris.add(c.concept_uri)

        parent_map: dict[str, str] = {}
        for c in self.canonical_business_concepts:
            if c.parent_concept_uri:
                if c.parent_concept_uri == c.concept_uri:
                    raise ValueError(
                        f"SELF_REFERENTIAL_PARENT: '{c.concept_uri}' cannot be its own parent"
                    )
                if c.parent_concept_uri not in concept_uris:
                    raise ValueError(
                        f"DANGLING_PARENT_CONCEPT: Concept '{c.concept_uri}' references parent "
                        f"'{c.parent_concept_uri}' which does not exist in ontology"
                    )
                parent_map[c.concept_uri] = c.parent_concept_uri

        # Iterative cycle detection (protects against recursion limits)
        for start_uri in parent_map:
            visited_in_path: set[str] = {start_uri}
            curr: str | None = parent_map.get(start_uri)
            while curr:
                if curr in visited_in_path:
                    raise ValueError(
                        f"CYCLIC_CONCEPT_HIERARCHY: Cycle detected in hierarchy at '{curr}'"
                    )
                visited_in_path.add(curr)
                curr = parent_map.get(curr)

        return self

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)


SemanticGraphA = GraphAOntology
CBCNode = CanonicalBusinessConcept

__all__ = [
    "CBCNode",
    "CanonicalBusinessConcept",
    "CanonicalRelationship",
    "ConceptAttribute",
    "ExternalMapping",
    "GovernanceConstraints",
    "GraphAOntology",
    "KeyAuthorityTierSpec",
    "KeyAuthorityTiers",
    "ReferenceEnumeration",
    "SemanticGraphA",
    "YagniCostGuardPolicy",
]
