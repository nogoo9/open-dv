"""Component sub-models for Enterprise Canonical Business Concept (CBC) Ontology (Graph A)."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

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


class YagniCostGuardPolicy(StrictGraphAModel):
    """Gate 4 YAGNI cost guard thresholds attached to a canonical relationship."""

    max_general_columns_threshold: int = Field(ge=1)
    min_write_amplification_mb_per_day: float = Field(ge=0.0)


__all__ = [
    "ConceptAttribute",
    "ExternalMapping",
    "GovernanceConstraints",
    "KeyAuthorityTierSpec",
    "KeyAuthorityTiers",
    "StrictGraphAModel",
    "YagniCostGuardPolicy",
    "_validate_json_metadata",
]
