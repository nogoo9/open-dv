from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Literal
from uuid import uuid4

from pydantic import UUID4, AliasChoices, AwareDatetime, Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.jcs import dumps_jcs

SemVerClass = Literal["MAJOR", "MINOR", "PATCH"]

CONCEPT_URN_REGEX = r"^urn:ontology:(provisional:)?entity:[a-z0-9_]+$"
ENUM_URN_REGEX = r"^(urn:ontology:(provisional:)?enum:[a-z0-9_]+)?$"

SEMVER_RANKS: dict[SemVerClass, int] = {
    "MAJOR": 3,
    "MINOR": 2,
    "PATCH": 1,
}


def _assert_no_alias_conflict(
    data: dict[str, Any],
    primary_key: str,
    alias_key: str,
) -> None:
    """Validate alias pairs for conflicts without mutating caller dict in place."""
    if primary_key in data and alias_key in data:
        p_val = data[primary_key]
        a_val = data[alias_key]
        if p_val is not None and a_val is not None and p_val != a_val:
            raise ValueError(
                f"Conflicting values for '{primary_key}' ({p_val!r}) and '{alias_key}' ({a_val!r})"
            )


# Backward-compatibility alias
_check_and_sync_alias_pair = _assert_no_alias_conflict


def _check_name_and_uri_compatibility(
    name: Any,
    uri: Any,
    name_key: str = "relationship_name",
    uri_key: str = "relationship_uri",
) -> None:
    """Validate that short name matches the terminal slug of the URN if both are provided."""
    name_str = str(name).strip() if name is not None else ""
    uri_str = str(uri).strip() if uri is not None else ""
    if name_str and uri_str:
        uri_slug = uri_str.rsplit(":", 1)[-1]
        if uri_slug != name_str and uri_str != name_str:
            raise ValueError(
                f"Conflicting values for '{name_key}' ({name!r}) and '{uri_key}' ({uri!r})"
            )


def _lookup_concept_attr(base_ontology: Any, concept_uri: str, attr_name: str) -> Any:
    """Safely look up attribute value of a concept in dict or object base ontology."""
    if base_ontology is None:
        return None
    if isinstance(base_ontology, dict):
        concepts = base_ontology.get("concepts", {})
        if isinstance(concepts, dict):
            c_obj = concepts.get(concept_uri)
            if isinstance(c_obj, dict):
                return c_obj.get(attr_name)
            return getattr(c_obj, attr_name, None)
        if isinstance(concepts, (list, tuple)):
            for c in concepts:
                if isinstance(c, dict) and c.get("concept_uri") == concept_uri:
                    return c.get(attr_name)
                if getattr(c, "concept_uri", None) == concept_uri:
                    return getattr(c, attr_name, None)
    elif hasattr(base_ontology, "concepts"):
        concepts = base_ontology.concepts
        if isinstance(concepts, dict):
            c_obj = concepts.get(concept_uri)
            if isinstance(c_obj, dict):
                return c_obj.get(attr_name)
            return getattr(c_obj, attr_name, None)
        if isinstance(concepts, (list, tuple)):
            for c in concepts:
                if getattr(c, "concept_uri", None) == concept_uri:
                    return getattr(c, attr_name, None)
    return None


class EvidenceRef(BaseContractModel):
    """Grounding evidence reference for an ontology delta operation."""

    evidence_type: str = Field(
        ...,
        description=(
            "Classification of evidence, e.g. column_profile, sample_values, "
            "document_span, standard_reference, existing_concept"
        ),
    )
    reference_uri: str | None = Field(
        default=None,
        description="Target reference pointer or URI",
    )
    snippet: str | None = Field(
        default=None,
        description="Text snippet, excerpt, or sample value grounding the proposal",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    doc_sha256: str | None = Field(default=None, description="Source document SHA-256")
    char_start: int | None = Field(default=None, description="Starting character offset")
    char_end: int | None = Field(default=None, description="Ending character offset")
    bronze_table: str | None = Field(default=None, description="Source bronze table name")
    column_name: str | None = Field(default=None, description="Source column name")


class BaseOntologyDeltaOp(BaseContractModel):
    """Abstract base class for all ontology delta operations."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_id: str = Field(..., min_length=1, description="Unique operation identifier")
    op_type: str = Field(..., description="Operation type discriminator")
    rationale: str = Field(default="", description="Business justification for the operation")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Operation confidence score")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Grounding evidence references"
    )

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Compute the effective SemVer classification of this operation."""
        return self.semver_class


# ---------------------------------------------------------------------------
# MAJOR BREAKING OPERATIONS (4)
# ---------------------------------------------------------------------------


class RemoveConceptOp(BaseOntologyDeltaOp):
    """Major breaking operation removing a Canonical Business Concept / Hub."""

    semver_class: ClassVar[SemVerClass] = "MAJOR"
    op_type: Literal["remove_concept"] = "remove_concept"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    reason: str = Field(default="", description="Removal rationale / reason")


class RemoveRelationshipOp(BaseOntologyDeltaOp):
    """Major breaking operation removing a Unit-of-Work relationship / Link."""

    semver_class: ClassVar[SemVerClass] = "MAJOR"
    op_type: Literal["remove_relationship"] = "remove_relationship"
    relationship_name: str = Field(default="", description="Target relationship name")
    relationship_uri: str = Field(default="", description="Target relationship URN")
    reason: str = Field(default="", description="Removal rationale / reason")

    @model_validator(mode="before")
    @classmethod
    def _check_rel_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_name_and_uri_compatibility(
                data.get("relationship_name"),
                data.get("relationship_uri"),
            )
        return data

    @model_validator(mode="after")
    def _validate_targets(self) -> RemoveRelationshipOp:
        if not self.relationship_name.strip() and not self.relationship_uri.strip():
            raise ValueError(
                "At least one of 'relationship_name' or 'relationship_uri' must be non-empty"
            )
        return self


class RemoveAttributeOp(BaseOntologyDeltaOp):
    """Major breaking operation removing an attribute from a concept."""

    semver_class: ClassVar[SemVerClass] = "MAJOR"
    op_type: Literal["remove_attribute"] = "remove_attribute"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    attribute_name: str = Field(..., min_length=1, description="Target attribute name to remove")
    reason: str = Field(default="", description="Removal rationale / reason")


class UpdateConceptUrnOp(BaseOntologyDeltaOp):
    """Major breaking operation renaming a Canonical Business Concept URN identity."""

    semver_class: ClassVar[SemVerClass] = "MAJOR"
    op_type: Literal["update_concept_urn"] = "update_concept_urn"
    old_concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Original concept URN",
    )
    new_concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="New concept URN",
    )
    reason: str = Field(default="", description="Identity update rationale / reason")


# ---------------------------------------------------------------------------
# MINOR ADDITIVE OPERATIONS (10)
# ---------------------------------------------------------------------------


class AddConceptOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Canonical Business Concept (CBC) / Hub."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_concept"] = "add_concept"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Unique canonical concept URN",
    )
    concept_name: str = Field(default="", description="Human-readable concept name")
    elm_archetype: str = Field(default="CORE_BUSINESS_CONCEPT", description="ELM archetype")
    definition: str = Field(default="", description="Normative definition of concept")
    description: str = Field(default="", description="Alternative concept description")
    target_hub: str | None = Field(
        default=None,
        pattern=r"^(hub_[a-z0-9_]+)?$",
        description="Target Data Vault 2.0 Hub table name",
    )
    attributes: list[str] = Field(
        default_factory=list, description="Associated business attributes"
    )
    parent_concept_uri: str | None = Field(
        default=None,
        pattern=CONCEPT_URN_REGEX,
        description="Hierarchical parent concept URI within Graph A ontology",
    )
    synonyms: list[str] | None = Field(default=None, description="Discovered lexical synonyms")
    external_mappings: list[Any] | None = Field(
        default=None, description="Grounded external ontology mappings"
    )
    governance_constraints: dict[str, Any] | None = Field(
        default=None, description="Governance, PII, and MNPI constraints"
    )
    key_authority_tiers: dict[str, Any] | None = Field(
        default=None, description="3-Tier Business Key Authority specification"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary conceptual metadata"
    )
    source_hints: list[str] | None = Field(
        default=None, description="Provenance hints identifying source data feeds"
    )


class AddRelationshipOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Unit-of-Work relationship / Link."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_relationship"] = "add_relationship"
    relationship_uri: str = Field(
        ...,
        pattern=r"^urn:ontology:(provisional:)?(event|relationship):[a-z0-9_]+$",
        description="Unique canonical relationship URN",
    )
    relationship_name: str = Field(default="", description="Human-readable relationship name")
    target_link: str = Field(
        ...,
        pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$",
        description="Target Data Vault 2.0 Link table name",
    )
    source_concept_uri: str | None = Field(
        default=None,
        description="Source endpoint concept URN",
    )
    target_concept_uri: str | None = Field(
        default=None,
        description="Target endpoint concept URN",
    )
    endpoints: list[str] = Field(
        default_factory=list, description="List of participant concept URN endpoints"
    )
    grain_semantics: str = Field(default="", description="Relationship grain semantics")
    description: str = Field(default="", description="Alternative relationship description")
    cardinality: str | None = Field(
        default=None,
        description="Cardinality classification: one_to_one, one_to_many, many_to_many",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary relationship metadata"
    )
    yagni_cost_guard_policy: dict[str, Any] | None = Field(
        default=None, description="Attached Gate 4 YAGNI cost guard policy"
    )


class AddAttributeOp(BaseOntologyDeltaOp):
    """Additive operation to attach an attribute to a concept."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_attribute"] = "add_attribute"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    attribute_name: str = Field(default="", description="Attribute identifier")
    data_type: str = Field(default="string", description="Logical data type classification")
    description: str | None = Field(default=None, description="Conceptual definition of attribute")
    is_optional: bool = Field(
        default=True, description="Whether attribute is optional in Data Vault satellite"
    )
    example_values: list[str] = Field(
        default_factory=list, description="Sample values extracted from evidence"
    )
    attribute: Any | None = Field(
        default=None,
        exclude=True,
        description="Optional nested attribute specification",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary attribute metadata"
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_attribute_spec(cls, data: Any) -> Any:
        if isinstance(data, dict):
            attr = data.get("attribute")
            if isinstance(attr, dict):
                data = dict(data)
                if not data.get("attribute_name") and "attribute_name" in attr:
                    data["attribute_name"] = attr["attribute_name"]
                if not data.get("data_type") and "data_type" in attr:
                    data["data_type"] = attr["data_type"]
                if not data.get("description") and "definition" in attr:
                    data["description"] = attr["definition"]
                elif not data.get("description") and "description" in attr:
                    data["description"] = attr["description"]
        return data

    @model_validator(mode="after")
    def _validate_attr_target(self) -> AddAttributeOp:
        if not self.attribute_name.strip():
            raise ValueError("'attribute_name' must be a non-empty string")
        return self


class SetParentConceptOp(BaseOntologyDeltaOp):
    """Minor (or Major on overwrite/unset) operation to set or unset parent concept hierarchy."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["set_parent_concept"] = "set_parent_concept"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    parent_concept_uri: str | None = Field(
        default=None,
        pattern=CONCEPT_URN_REGEX,
        description="Parent concept URN within Graph A hierarchy",
    )

    @model_validator(mode="after")
    def _validate_not_self_parent(self) -> SetParentConceptOp:
        if self.parent_concept_uri is not None and self.parent_concept_uri == self.concept_uri:
            raise ValueError("Concept cannot be its own parent")
        return self

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Compute the effective SemVer classification of this operation."""
        if self.parent_concept_uri is None:
            if base_ontology is not None:
                existing_parent = _lookup_concept_attr(
                    base_ontology, self.concept_uri, "parent_concept_uri"
                )
                if existing_parent is None:
                    return "MINOR"
            return "MAJOR"
        if base_ontology is not None:
            existing_parent = _lookup_concept_attr(
                base_ontology, self.concept_uri, "parent_concept_uri"
            )
            if existing_parent is not None and existing_parent != self.parent_concept_uri:
                return "MAJOR"
        return self.semver_class


class SetConceptElmArchetypeOp(BaseOntologyDeltaOp):
    """Minor (or Major on overwrite) operation to set ELM archetype."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["set_concept_elm_archetype"] = "set_concept_elm_archetype"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    elm_archetype: str = Field(..., min_length=1, description="Target ELM archetype classification")

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Compute the effective SemVer classification of this operation."""
        if base_ontology is not None:
            existing_archetype = _lookup_concept_attr(
                base_ontology, self.concept_uri, "elm_archetype"
            )
            if existing_archetype is not None and existing_archetype != self.elm_archetype:
                return "MAJOR"
        return self.semver_class


class AddReferenceEnumerationOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Reference Enumeration / ref_* table."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_reference_enumeration"] = "add_reference_enumeration"
    enum_uri: str = Field(
        default="",
        pattern=ENUM_URN_REGEX,
        description="Unique reference enumeration URN",
    )
    name: str = Field(
        default="",
        validation_alias=AliasChoices("name", "enumeration_name"),
        description="Enumeration name",
    )
    target_reference_table: str = Field(
        default="",
        pattern=r"^(ref_[a-z0-9_]+)?$",
        description="Target reference table name",
    )
    values: list[str] = Field(default_factory=list, description="Allowed code values")
    allowed_codes: list[str] = Field(default_factory=list, description="Allowed code values")
    description: str = Field(default="", description="Enumeration description")
    code_descriptions: dict[str, str] | None = Field(
        default=None, description="Per-code descriptive text"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary enumeration metadata"
    )

    @property
    def enumeration_name(self) -> str:
        return self.name

    @model_validator(mode="before")
    @classmethod
    def _sync_enum_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_alias_conflict(data, "name", "enumeration_name")
        return data


class AddKeyAuthorityPatternOp(BaseOntologyDeltaOp):
    """Additive operation to attach a Business Key Authority tier pattern."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_key_authority_pattern"] = "add_key_authority_pattern"
    concept_uri: str = Field(
        ...,
        description="Target Canonical Business Concept URN",
    )
    authority_name: str = Field(default="", description="Name of the Business Key Authority")
    pattern_regex: str | None = Field(default=None, description="Regex pattern for authority")
    tier: str = Field(default="TIER_1_GLOBAL_STANDARD", description="Authority tier classification")
    tier_spec: dict[str, Any] | None = Field(default=None, description="Full tier specification")
    description: str = Field(default="", description="Authority description")


class AddGovernanceConstraintOp(BaseOntologyDeltaOp):
    """Additive operation to attach PII / MNPI security and satellite split constraints."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_governance_constraint"] = "add_governance_constraint"
    concept_uri: str = Field(
        ...,
        description="Target Canonical Business Concept URN",
    )
    constraint_id: str = Field(default="", description="Unique constraint identifier")
    constraint_type: str = Field(default="", description="Constraint classification type")
    pii_confidential_attributes: list[str] = Field(
        default_factory=list, description="Attributes requiring PII confidentiality"
    )
    mnpi_confidential_attributes: list[str] = Field(
        default_factory=list, description="Attributes requiring MNPI confidentiality"
    )
    required_satellite_split: str | None = Field(
        default=None, description="Target security satellite split name"
    )
    description: str = Field(default="", description="Constraint description")


class DeprecateConceptOp(BaseOntologyDeltaOp):
    """Minor operation marking a Canonical Business Concept as deprecated."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["deprecate_concept"] = "deprecate_concept"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    reason: str | None = Field(default=None, description="Deprecation rationale / reason")
    superseded_by_concept_uri: str | None = Field(
        default=None,
        pattern=CONCEPT_URN_REGEX,
        description="Optional superseding concept URN",
    )


class DeprecateRelationshipOp(BaseOntologyDeltaOp):
    """Minor operation marking a relationship as deprecated."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["deprecate_relationship"] = "deprecate_relationship"
    relationship_name: str = Field(default="", description="Target relationship name")
    relationship_uri: str = Field(default="", description="Target relationship URN")
    reason: str | None = Field(default=None, description="Deprecation rationale / reason")

    @model_validator(mode="before")
    @classmethod
    def _check_rel_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_name_and_uri_compatibility(
                data.get("relationship_name"),
                data.get("relationship_uri"),
            )
        return data

    @model_validator(mode="after")
    def _validate_targets(self) -> DeprecateRelationshipOp:
        if not self.relationship_name.strip() and not self.relationship_uri.strip():
            raise ValueError(
                "At least one of 'relationship_name' or 'relationship_uri' must be non-empty"
            )
        return self


# ---------------------------------------------------------------------------
# PATCH REFINEMENT OPERATIONS (10)
# ---------------------------------------------------------------------------


class AddSynonymOp(BaseOntologyDeltaOp):
    """Patch operation attaching a discovered lexical synonym to a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["add_synonym"] = "add_synonym"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    synonym: str = Field(
        ..., min_length=1, max_length=256, description="Discovered lexical synonym"
    )


class AddExternalMappingOp(BaseOntologyDeltaOp):
    """Patch operation attaching a reference ontology mapping (e.g. FIBO)."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["add_external_mapping"] = "add_external_mapping"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    scheme: str = Field(
        default="",
        validation_alias=AliasChoices("scheme", "standard_name"),
        description="Reference ontology standard scheme (e.g. 'FIBO')",
    )
    target_uri: str = Field(
        default="",
        validation_alias=AliasChoices("target_uri", "standard_uri"),
        description="Fully qualified reference ontology target URI",
    )
    predicate: str | None = Field(
        default=None,
        validation_alias=AliasChoices("predicate", "mapping_type"),
        description="SKOS mapping predicate classification",
    )
    pinned: bool | None = Field(
        default=None, description="Pinned flag preventing automated re-scoring"
    )
    mapping_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Mapping confidence score",
    )
    mapping: Any | None = Field(
        default=None,
        exclude=True,
        description="Optional nested mapping specification",
    )

    @property
    def standard_name(self) -> str:
        return self.scheme

    @property
    def standard_uri(self) -> str:
        return self.target_uri

    @property
    def mapping_type(self) -> str | None:
        return self.predicate

    @model_validator(mode="before")
    @classmethod
    def _sync_mapping_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_and_sync_alias_pair(data, "scheme", "standard_name")
            _check_and_sync_alias_pair(data, "target_uri", "standard_uri")
            _check_and_sync_alias_pair(data, "predicate", "mapping_type")

            mapping = data.get("mapping")
            if isinstance(mapping, dict):
                data = dict(data)
                has_scheme = data.get("scheme") or data.get("standard_name")
                if not has_scheme and "standard_name" in mapping:
                    data["scheme"] = mapping["standard_name"]
                has_target = data.get("target_uri") or data.get("standard_uri")
                if not has_target and "standard_uri" in mapping:
                    data["target_uri"] = mapping["standard_uri"]
                if data.get("mapping_confidence") is None and "confidence" in mapping:
                    data["mapping_confidence"] = mapping["confidence"]
                has_pred = data.get("predicate") or data.get("mapping_type")
                if not has_pred and "mapping_type" in mapping:
                    data["predicate"] = mapping["mapping_type"]
                if data.get("pinned") is None and "pinned" in mapping:
                    data["pinned"] = mapping["pinned"]

        return data

    @model_validator(mode="after")
    def _validate_mapping_target(self) -> AddExternalMappingOp:
        if not self.target_uri.strip():
            raise ValueError("At least one of 'target_uri' or 'standard_uri' must be non-empty")
        return self


class ReviseDescriptionOp(BaseOntologyDeltaOp):
    """Patch operation revising textual description of a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["revise_description"] = "revise_description"
    concept_uri: str = Field(
        default="",
        pattern=r"^(urn:ontology:(provisional:)?entity:[a-z0-9_]+)?$",
        validation_alias=AliasChoices("concept_uri", "target_uri"),
        description="Target Canonical Business Concept URN",
    )
    description: str = Field(default="", description="Revised concept description")

    @property
    def target_uri(self) -> str:
        return self.concept_uri

    @model_validator(mode="before")
    @classmethod
    def _sync_uris(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_and_sync_alias_pair(data, "concept_uri", "target_uri")
        return data

    @model_validator(mode="after")
    def _validate_target(self) -> ReviseDescriptionOp:
        if not self.concept_uri.strip():
            raise ValueError("At least one of 'concept_uri' or 'target_uri' must be non-empty")
        return self


class RescoreExternalMappingOp(BaseOntologyDeltaOp):
    """Patch operation updating match confidence score for an external mapping."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["rescore_external_mapping"] = "rescore_external_mapping"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    scheme: str = Field(default="", description="Reference ontology standard scheme")
    target_uri: str = Field(
        default="",
        validation_alias=AliasChoices("target_uri", "standard_uri"),
        description="Target reference ontology URI",
    )
    new_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("new_confidence", "mapping_confidence"),
        description="New confidence score",
    )
    previous_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Previous confidence score"
    )
    matcher_id: str | None = Field(default=None, description="Matcher identifier")
    matcher_version: str | None = Field(default=None, description="Matcher version")
    matcher_sha256: str | None = Field(default=None, description="Matcher digest")

    @property
    def mapping_confidence(self) -> float:
        return self.new_confidence

    @property
    def standard_uri(self) -> str:
        return self.target_uri

    @model_validator(mode="before")
    @classmethod
    def _sync_rescore_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_alias_conflict(data, "target_uri", "standard_uri")
            _assert_no_alias_conflict(data, "new_confidence", "mapping_confidence")
        return data

    @model_validator(mode="after")
    def _validate_target(self) -> RescoreExternalMappingOp:
        if not self.target_uri.strip():
            raise ValueError("At least one of 'target_uri' or 'standard_uri' must be non-empty")
        return self


class AddReferenceEnumCodeOp(BaseOntologyDeltaOp):
    """Patch operation appending an allowed code to a reference enumeration."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["add_reference_enum_code"] = "add_reference_enum_code"
    enumeration_name: str = Field(default="", description="Reference enumeration name")
    enum_uri: str = Field(default="", description="Reference enumeration URN")
    code: str = Field(..., min_length=1, description="Code value to append")
    description: str | None = Field(default=None, description="Code value description")

    @model_validator(mode="before")
    @classmethod
    def _check_enum_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_name_and_uri_compatibility(
                data.get("enumeration_name"),
                data.get("enum_uri"),
                name_key="enumeration_name",
                uri_key="enum_uri",
            )
        return data

    @model_validator(mode="after")
    def _validate_enum_target(self) -> AddReferenceEnumCodeOp:
        if not self.enumeration_name.strip() and not self.enum_uri.strip():
            raise ValueError("At least one of 'enumeration_name' or 'enum_uri' must be non-empty")
        if not self.code.strip():
            raise ValueError("'code' must be a non-empty string")
        return self


class RepinExternalMappingOp(BaseOntologyDeltaOp):
    """Patch operation updating pinned state or target pack for external mapping."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["repin_external_mapping"] = "repin_external_mapping"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    scheme: str = Field(default="", description="Reference ontology standard scheme")
    target_uri: str = Field(
        default="",
        validation_alias=AliasChoices("target_uri", "standard_uri"),
        description="Target reference ontology URI",
    )
    pinned: bool = Field(default=True, description="Pinned status flag")
    new_reference_pack_id: str | None = Field(default=None, description="Target pack ID")
    new_reference_pack_sha256: str | None = Field(default=None, description="Target pack SHA-256")

    @property
    def standard_uri(self) -> str:
        return self.target_uri

    @model_validator(mode="before")
    @classmethod
    def _sync_uris(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_and_sync_alias_pair(data, "target_uri", "standard_uri")
        return data

    @model_validator(mode="after")
    def _validate_target(self) -> RepinExternalMappingOp:
        if not self.target_uri.strip():
            raise ValueError("At least one of 'target_uri' or 'standard_uri' must be non-empty")
        return self


class RefineDefinitionOp(BaseOntologyDeltaOp):
    """Additive operation to refine the textual definition or attributes of a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["refine_definition"] = "refine_definition"
    target_uri: str = Field(default="", description="Target concept or relationship URN")
    concept_uri: str = Field(default="", description="Target concept URN")
    refined_description: str = Field(
        default="",
        validation_alias=AliasChoices("refined_description", "definition"),
        description="Refined definition or description",
    )
    additional_attributes: list[str] = Field(
        default_factory=list, description="Additional attributes to append"
    )

    @property
    def definition(self) -> str:
        return self.refined_description

    @model_validator(mode="before")
    @classmethod
    def _sync_definition_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_alias_conflict(data, "refined_description", "definition")
        return data


class AddSourceHintOp(BaseOntologyDeltaOp):
    """Additive operation to register a source mapping hint linking source data to a CBC."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["add_source_hint"] = "add_source_hint"
    concept_uri: str = Field(
        default="",
        description="Target Canonical Business Concept URN",
    )
    hint: str | None = Field(default=None, description="Provenance or mapping hint")
    source_name: str = Field(default="", description="Source system identifier")
    source_system: str = Field(default="", description="Source system identifier")
    table_name: str = Field(default="", description="Source table or file name")
    column_hint: str = Field(default="", description="Source column hint")
    column_name: str = Field(default="", description="Source column name")


class AnnotateConceptMetadataOp(BaseOntologyDeltaOp):
    """Patch operation annotating arbitrary metadata key-values on a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["annotate_concept_metadata"] = "annotate_concept_metadata"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary")
    metadata_key: str | None = Field(default=None, exclude=True, description="Single metadata key")
    metadata_value: Any | None = Field(
        default=None, exclude=True, description="Single metadata value"
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict):
            k = data.get("metadata_key")
            v = data.get("metadata_value")
            if k is not None:
                data = dict(data)
                meta = dict(data.get("metadata") or {})
                meta[k] = v
                data["metadata"] = meta
        return data


class RemoveExternalMappingOp(BaseOntologyDeltaOp):
    """Patch operation removing an external reference mapping from a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["remove_external_mapping"] = "remove_external_mapping"
    concept_uri: str = Field(
        ...,
        pattern=CONCEPT_URN_REGEX,
        description="Target Canonical Business Concept URN",
    )
    scheme: str = Field(default="", description="Reference ontology standard scheme")
    target_uri: str = Field(
        default="",
        validation_alias=AliasChoices("target_uri", "standard_uri"),
        description="Target reference ontology URI",
    )
    mapping_type: str | None = Field(default=None, description="Optional mapping type filter")

    @property
    def standard_uri(self) -> str:
        return self.target_uri

    @model_validator(mode="before")
    @classmethod
    def _sync_uris(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _check_and_sync_alias_pair(data, "target_uri", "standard_uri")
        return data

    @model_validator(mode="after")
    def _validate_target(self) -> RemoveExternalMappingOp:
        if not self.target_uri.strip():
            raise ValueError("At least one of 'target_uri' or 'standard_uri' must be non-empty")
        return self


# Discriminated union of all 24 operations
OntologyOperation = Annotated[
    # Major breaking operations (4)
    RemoveConceptOp
    | RemoveRelationshipOp
    | RemoveAttributeOp
    | UpdateConceptUrnOp
    # Minor additive operations (10)
    | AddConceptOp
    | AddRelationshipOp
    | AddAttributeOp
    | SetParentConceptOp
    | SetConceptElmArchetypeOp
    | AddReferenceEnumerationOp
    | AddKeyAuthorityPatternOp
    | AddGovernanceConstraintOp
    | DeprecateConceptOp
    | DeprecateRelationshipOp
    # Patch refinement operations (10)
    | AddSynonymOp
    | AddExternalMappingOp
    | ReviseDescriptionOp
    | RescoreExternalMappingOp
    | AddReferenceEnumCodeOp
    | RepinExternalMappingOp
    | RefineDefinitionOp
    | AddSourceHintOp
    | AnnotateConceptMetadataOp
    | RemoveExternalMappingOp,
    Field(discriminator="op_type"),
]

# Backward-compatibility alias
OntologyDeltaItem = OntologyOperation


class OntologyDelta(BaseContractModel):
    """Ordered collection of additive and structural operations evolving Graph A to vN+1."""

    delta_id: str = Field(
        ...,
        min_length=1,
        description="Unique delta identifier",
    )
    parent_sha256: str | None = Field(
        default=None,
        pattern=r"^([a-f0-9]{64}|)$",
        validation_alias=AliasChoices("parent_sha256", "base_sha256"),
        description="Parent Graph A CAS digest precondition",
    )
    domain_namespace: str | None = Field(
        default=None,
        pattern=r"^([a-z][a-z0-9_]*)?$",
        description="Domain namespace matching base Graph A",
    )
    base_ontology_version: str = Field(
        default="1.0.0",
        pattern=r"^([0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?)$",
        validation_alias=AliasChoices("base_ontology_version", "base_version"),
        description="Base ontology version precondition",
    )
    target_ontology_version: str | None = Field(
        default="1.1.0",
        pattern=r"^([0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?)$",
        validation_alias=AliasChoices("target_ontology_version", "target_version"),
        description="Target ontology version after delta application",
    )
    operations: list[OntologyOperation] = Field(
        default_factory=list,
        description="Ordered sequence of ontology operations",
    )
    created_at: AwareDatetime | None = Field(
        default=None,
        description="UTC creation timestamp",
    )
    author: str | None = Field(
        default=None,
        description="Author or agent identity that created the delta",
    )
    rationale: str | None = Field(
        default=None,
        description="Overall business rationale for delta",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary authoring provenance metadata",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_conflicting_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_alias_conflict(data, "base_sha256", "parent_sha256")
            _assert_no_alias_conflict(data, "base_version", "base_ontology_version")
            _assert_no_alias_conflict(data, "target_version", "target_ontology_version")
        return data

    @model_validator(mode="after")
    def _validate_occ_preconditions(self) -> OntologyDelta:
        if self.base_ontology_version == "0.0.0":
            if self.parent_sha256:
                raise ValueError(
                    "parent_sha256 must be empty for genesis delta (base_ontology_version '0.0.0')"
                )
        else:
            is_v2_envelope = bool(self.author or self.created_at or self.rationale)
            if (is_v2_envelope or self.parent_sha256 is not None) and not self.parent_sha256:
                raise ValueError(
                    f"base_sha256 must be a 64-character hex string when base_version "
                    f"is '{self.base_ontology_version}' (empty base_sha256 is only allowed when "
                    "base_version is '0.0.0')"
                )
        return self

    @model_validator(mode="after")
    def _validate_semver_bump(self) -> OntologyDelta:
        base_ver = self.base_ontology_version or "0.0.0"
        req = self.highest_semver_class()

        if req == "MAJOR":
            if not self.target_ontology_version:
                raise ValueError(
                    "target_ontology_version is required when delta contains "
                    "MAJOR breaking operations"
                )
            if base_ver == "0.0.0":
                raise ValueError(
                    "Destructive operations (MAJOR) are prohibited on greenfield genesis "
                    "delta (base_version '0.0.0')"
                )

        if self.target_ontology_version and self.base_ontology_version:
            base_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", self.base_ontology_version)
            target_match = re.match(r"^(\d+)\.(\d+)\.(\d+)", self.target_ontology_version)
            if base_match and target_match:
                b_maj, b_min, b_pat = map(int, base_match.groups())
                t_maj, t_min, t_pat = map(int, target_match.groups())

                meta = self.metadata or {}
                mode_val = meta.get("mode")
                action_val = meta.get("action")
                is_bootstrap = (
                    mode_val == "BOOTSTRAP"
                    or action_val == "BOOTSTRAP"
                    or (
                        self.base_ontology_version == "1.0.0"
                        and self.target_ontology_version == "1.0.0"
                        and mode_val in ("BOOTSTRAP", None)
                    )
                )
                if (
                    is_bootstrap
                    and self.base_ontology_version == "1.0.0"
                    and self.target_ontology_version == "1.0.0"
                    and req != "MAJOR"
                ):
                    return self

                if self.base_ontology_version == "0.0.0":
                    if (t_maj, t_min, t_pat) <= (0, 0, 0):
                        raise ValueError(
                            "target_ontology_version must be greater than base '0.0.0'"
                        )
                elif req == "MAJOR":
                    if t_maj <= b_maj:
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' is invalid for "
                            f"base '{self.base_ontology_version}' with MAJOR breaking operations. "
                            f"Expected major bump (>= {b_maj + 1}.0.0)."
                        )
                elif req == "MINOR":
                    if (t_maj < b_maj) or (t_maj == b_maj and t_min <= b_min):
                        raise ValueError(
                            f"Target version '{self.target_ontology_version}' is invalid for "
                            f"base '{self.base_ontology_version}' with MINOR operations. "
                            f"Expected minor or major bump (>= {b_maj}.{b_min + 1}.0)."
                        )
                elif req == "PATCH" and (t_maj, t_min, t_pat) <= (b_maj, b_min, b_pat):
                    raise ValueError(
                        f"Target version '{self.target_ontology_version}' is invalid for "
                        f"base '{self.base_ontology_version}'. "
                        "Target must be strictly greater than base."
                    )
        return self

    @property
    def base_sha256(self) -> str:
        return self.parent_sha256 or ""

    @property
    def base_version(self) -> str:
        return self.base_ontology_version or "0.0.0"

    @property
    def target_version(self) -> str | None:
        return self.target_ontology_version

    def highest_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Determine the highest SemVer classification across all operations."""
        if not self.operations:
            return "PATCH"
        ranks = [SEMVER_RANKS[op.effective_semver_class(base_ontology)] for op in self.operations]
        highest_rank = max(ranks)
        for s_class, rank in SEMVER_RANKS.items():
            if rank == highest_rank:
                return s_class
        return "PATCH"

    def canonical_dict(self) -> dict[str, Any]:
        """Return the dictionary representation for canonical hashing (excluding None fields)."""
        return self.model_dump(mode="json", exclude_none=True)

    def canonical_json(self) -> str:
        """Serialize delta to RFC 8785 canonical JSON string."""
        return dumps_jcs(self.canonical_dict())

    def canonical_sha256(self) -> str:
        """Compute RFC 8785 canonical SHA-256 digest of the delta."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_v1_schema_dict(self) -> dict[str, Any]:
        """Export dictionary conforming strictly to schemas/v1/ontology-delta.schema.json.

        Raises:
            ValueError: If non-v1 operations or populated extension fields are present.
        """
        if self.author is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'author' envelope field is populated"
            )
        if self.rationale is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'rationale' envelope field is populated"
            )
        if self.created_at is not None:
            raise ValueError(
                "Cannot convert delta to v1 schema: 'created_at' envelope field is populated"
            )

        v1_op_allowed: dict[str, set[str]] = {
            "add_concept": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "concept_name",
                "elm_archetype",
                "definition",
                "description",
                "target_hub",
                "attributes",
                "governance_constraints",
                "key_authority_tiers",
            },
            "add_relationship": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "relationship_uri",
                "relationship_name",
                "target_link",
                "source_concept_uri",
                "target_concept_uri",
                "endpoints",
                "grain_semantics",
                "description",
                "yagni_cost_guard_policy",
            },
            "add_reference_enumeration": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "enum_uri",
                "name",
                "target_reference_table",
                "values",
                "allowed_codes",
                "description",
            },
            "add_key_authority_pattern": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "authority_name",
                "pattern_regex",
                "tier",
                "tier_spec",
                "description",
            },
            "add_governance_constraint": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "constraint_id",
                "constraint_type",
                "pii_confidential_attributes",
                "mnpi_confidential_attributes",
                "required_satellite_split",
                "description",
            },
            "refine_definition": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "target_uri",
                "concept_uri",
                "refined_description",
                "additional_attributes",
            },
            "add_source_hint": {
                "op_id",
                "op_type",
                "rationale",
                "confidence",
                "evidence_refs",
                "concept_uri",
                "source_name",
                "source_system",
                "table_name",
                "column_hint",
                "column_name",
            },
        }

        # Check for non-v1 operations
        for op in self.operations:
            if op.op_type not in v1_op_allowed:
                raise ValueError(
                    f"Operation type '{op.op_type}' is not supported in v1 schema "
                    "(only 7 additive op types allowed in v1)"
                )
            if isinstance(op, AddConceptOp):
                if op.parent_concept_uri is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: "
                        "'parent_concept_uri' is populated"
                    )
                if op.synonyms is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'synonyms' is populated"
                    )
                if op.external_mappings is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'external_mappings' is populated"
                    )
                if op.source_hints is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'source_hints' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddConceptOp to v1 schema: 'metadata' is populated"
                    )
            elif isinstance(op, AddRelationshipOp):
                if op.cardinality is not None:
                    raise ValueError(
                        "Cannot convert AddRelationshipOp to v1 schema: 'cardinality' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddRelationshipOp to v1 schema: 'metadata' is populated"
                    )
            elif isinstance(op, AddReferenceEnumerationOp):
                if op.code_descriptions is not None:
                    raise ValueError(
                        "Cannot convert AddReferenceEnumerationOp to v1 schema: "
                        "'code_descriptions' is populated"
                    )
                if op.metadata is not None:
                    raise ValueError(
                        "Cannot convert AddReferenceEnumerationOp to v1 schema: "
                        "'metadata' is populated"
                    )
            elif isinstance(op, AddSourceHintOp):
                if op.hint is not None:
                    raise ValueError(
                        "Cannot convert AddSourceHintOp to v1 schema: 'hint' is populated"
                    )

        pruned_ops: list[dict[str, Any]] = []
        for op in self.operations:
            op_dict = op.model_dump(mode="json", exclude_none=True)
            allowed = v1_op_allowed[op.op_type]
            pruned_ops.append({k: v for k, v in op_dict.items() if k in allowed})

        result: dict[str, Any] = {
            "delta_id": self.delta_id,
            "parent_sha256": self.parent_sha256 or None,
            "domain_namespace": self.domain_namespace,
            "base_ontology_version": (
                self.base_ontology_version
                if (self.base_ontology_version and self.base_ontology_version != "0.0.0")
                else None
            ),
            "target_ontology_version": self.target_ontology_version,
            "operations": pruned_ops,
            "metadata": self.metadata,
        }
        return {k: v for k, v in result.items() if v is not None}


class StewardWaiverRecord(BaseContractModel):
    """Cryptographically verifiable administrative waiver for ungrounded concept evolution."""

    waiver_id: UUID4 = Field(default_factory=uuid4, description="Unique waiver identifier")
    proposal_id: UUID4 = Field(..., description="Referenced ontology proposal identifier")
    proposal_delta_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of proposed delta",
    )
    target_concept_uris: list[str] = Field(
        ...,
        min_length=1,
        description="Target concept URIs exempted from grounding check",
    )
    base_sha256: str = Field(
        ...,
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of base ontology precondition",
    )
    rationale: str = Field(
        ...,
        min_length=10,
        description="Business and architectural justification for waiver (>= 10 chars)",
    )
    authorized_by: str = Field(
        ..., min_length=1, description="Identity of approving administrator or steward"
    )
    authorized_role: Literal["admin", "steward"] = Field(
        ..., description="Role authorization tier for waiver approval"
    )
    signature_or_token: str = Field(
        ...,
        min_length=1,
        repr=False,
        description="Cryptographic signature or authorization token",
    )
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of authorization",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="Optional expiration timestamp for time-bounded waiver",
    )

    @model_validator(mode="after")
    def _validate_expiration_and_targets(self) -> StewardWaiverRecord:
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError(
                f"expires_at ({self.expires_at.isoformat()}) must be strictly after "
                f"created_at ({self.created_at.isoformat()})"
            )
        for uri in self.target_concept_uris:
            if not re.match(CONCEPT_URN_REGEX, uri):
                raise ValueError(
                    f"target_concept_uri '{uri}' does not match pattern {CONCEPT_URN_REGEX}"
                )
        return self

    def signing_payload(self) -> str:
        """Compute the RFC 8785 canonical JSON signing payload."""
        created_str = self.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_str = (
            self.expires_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if self.expires_at
            else None
        )
        payload = {
            "authorized_by": self.authorized_by,
            "authorized_role": self.authorized_role,
            "base_sha256": self.base_sha256,
            "created_at": created_str,
            "expires_at": expires_str,
            "proposal_delta_sha256": self.proposal_delta_sha256,
            "proposal_id": str(self.proposal_id),
            "rationale": self.rationale,
            "target_concept_uris": sorted(set(self.target_concept_uris)),
            "waiver_id": str(self.waiver_id),
        }
        return dumps_jcs(payload)

    def signing_sha256(self) -> str:
        """Compute SHA-256 digest of canonical signing payload."""
        return hashlib.sha256(self.signing_payload().encode("utf-8")).hexdigest()


class OntologyProposal(BaseContractModel):
    """Advisory or blocking proposal emitted by an Ontology Recommender."""

    proposal_id: str = Field(..., min_length=1, description="Unique proposal identifier")
    wave_id: str | None = Field(default=None, description="Optional pipeline wave identifier")
    recommender_id: str = Field(default="recommender-default", description="Recommender module ID")
    recommender_version: str = Field(default="1.0.0", description="Recommender version")
    base_ontology_sha256: str = Field(
        default="",
        pattern=r"^([a-f0-9]{64})?$",
        description="SHA-256 of base ontology",
    )
    delta: OntologyDelta = Field(..., description="Proposed ontology delta")
    rationale: str = Field(default="", description="Overall business rationale for proposal")
    confidence_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Aggregate proposal confidence score"
    )
    coverage_before: float | None = Field(
        default=None, description="Projected coverage ratio before"
    )
    coverage_after: float | None = Field(default=None, description="Projected coverage ratio after")
    diagnostics: list[str] = Field(default_factory=list, description="Advisory diagnostic messages")
    created_at_utc: str = Field(default="", description="UTC ISO-8601 creation timestamp")


class OntologyDecision(BaseContractModel):
    """Steward or policy decision record governing delta application."""

    decision_id: str = Field(..., min_length=1, description="Unique decision record ID")
    proposal_id: str | None = Field(default=None, description="Referenced proposal ID")
    run_id: str | None = Field(default=None, description="Pipeline run ID")
    hold_id: str | None = Field(default=None, description="Steward hold ID")
    action: str = Field(default="ACCEPT", description="Decision action (ACCEPT, REJECT, PARTIAL)")
    base_ontology_sha256: str | None = Field(default=None, description="SHA-256 of base ontology")
    accepted_op_ids: list[str] = Field(
        default_factory=list, description="Operation IDs accepted for application"
    )
    rejected_op_ids: list[str] = Field(
        default_factory=list, description="Operation IDs explicitly rejected"
    )
    applied_delta_sha256: str | None = Field(default=None, description="SHA-256 of accepted delta")
    resulting_ontology_sha256: str | None = Field(
        default=None, description="SHA-256 of resulting Graph A vN+1"
    )
    decided_by: str = Field(default="steward", description="Steward identifier or policy name")
    steward_notes: str = Field(default="", description="Human steward rationale notes")
    notes: str = Field(default="", description="Alternative notes field")
    timestamp: str = Field(default="", description="Timestamp of decision")
    created_at_utc: str = Field(default="", description="UTC ISO timestamp of decision")


class OntologyDiff(BaseContractModel):
    """Structural delta between two Graph A ontology instances."""

    base_version: str = Field(default="1.0.0", description="Base ontology version")
    target_version: str = Field(default="1.1.0", description="Target ontology version")
    base_sha256: str | None = Field(default=None, description="Base ontology SHA-256")
    target_sha256: str | None = Field(default=None, description="Target ontology SHA-256")
    added_concepts: list[str] = Field(default_factory=list, description="Added concept URNs")
    added_relationships: list[str] = Field(
        default_factory=list, description="Added relationship URNs"
    )
    added_enumerations: list[str] = Field(
        default_factory=list, description="Added reference enumeration URNs"
    )
    refined_concepts: list[str] = Field(
        default_factory=list, description="Concepts refined by definition or constraints"
    )
    breaking_changes: list[str] = Field(
        default_factory=list, description="Detected breaking changes"
    )
    diagnostics: list[str] = Field(
        default_factory=list, description="Informational diff diagnostics"
    )


class OntologyValidationResult(BaseContractModel):
    """Validation report returned by validate_ontology_delta."""

    valid: bool = Field(default=True, description="Whether the delta is valid")
    diagnostics: list[str] = Field(default_factory=list, description="Informational diagnostics")
    violations: list[str] = Field(default_factory=list, description="Validation violation messages")
    error_code: str | None = Field(default=None, description="Primary error code if invalid")


ValidationResult = OntologyValidationResult


class OntologySuggestionRequest(BaseContractModel):
    """Request DTO to generate ontology proposal from source profiles or context docs (IP-1)."""

    mode: Literal["BOOTSTRAP", "DELTA", "HOLD_RESOLUTION"] = Field(
        default="DELTA", description="Recommender operation mode"
    )
    domain_namespace: str | None = Field(default=None, description="Target domain namespace")
    base_ontology_path: str | None = Field(
        default=None, description="Path to base Graph A ontology file"
    )
    base_ontology_content: str | None = Field(
        default=None, description="Inline base Graph A ontology YAML/JSON text (Decision D1)"
    )
    base_ontology_sha256: str | None = Field(
        default=None, description="CAS SHA-256 digest of base ontology"
    )
    source_paths: list[str] = Field(default_factory=list, description="Source dataset file paths")
    bronze_source_path: str | None = Field(
        default=None, description="Path to bronze source dataset file"
    )
    bronze_source_content: str | None = Field(
        default=None, description="Inline bronze source dataset content"
    )
    source_profiles: list[dict[str, Any]] = Field(
        default_factory=list, description="Source empirical profiles (Graph B)"
    )
    empirical_profile_path: str | None = Field(
        default=None, description="Path to empirical data profile (Graph B) JSON file"
    )
    empirical_profile_content: str | None = Field(
        default=None, description="Inline empirical data profile JSON text"
    )
    context_document_paths: list[str] = Field(
        default_factory=list, description="Context document paths (glossary, policy)"
    )
    context_doc_path: str | None = Field(default=None, description="Path to context document file")
    context_doc_content: str | None = Field(
        default=None, description="Inline context document text"
    )
    business_context: str | None = Field(
        default=None, description="Free-text business description or brief"
    )
    focus: list[str] = Field(
        default_factory=list, description="Focus areas: unmapped tables, columns, mentions"
    )
    recommender_id: str | None = Field(default=None, description="Specific recommender plugin ID")


class OntologyValidateDeltaRequest(BaseContractModel):
    """Request DTO to deterministically validate an ontology delta (ONT-EVO-001..003)."""

    delta: OntologyDelta | None = Field(default=None, description="OntologyDelta object")
    delta_path: str | None = Field(default=None, description="Path to delta file")
    delta_content: str | None = Field(
        default=None, description="Inline delta JSON/YAML text (Decision D1)"
    )
    base_ontology: Any | None = Field(
        default=None, description="Base Graph A ontology object or dict"
    )
    base_ontology_path: str | None = Field(
        default=None, description="Path to base Graph A ontology file"
    )
    base_ontology_content: str | None = Field(
        default=None, description="Inline base Graph A ontology YAML/JSON text"
    )
    contract_path: str | None = Field(
        default=None, description="Path to deployed Data Vault contract file"
    )
    contract_content: str | None = Field(
        default=None, description="Inline deployed Data Vault contract YAML text"
    )
    deployed_contract: Any | None = Field(
        default=None, description="Deployed contract object or dict"
    )


class OntologyDiffRequest(BaseContractModel):
    """Request DTO to compute structural diff between two Graph A ontologies."""

    base_ontology: Any | None = Field(
        default=None, description="Base Graph A ontology object or dict"
    )
    base_ontology_path: str | None = Field(
        default=None, description="Path to base Graph A ontology file"
    )
    base_ontology_content: str | None = Field(
        default=None, description="Inline base Graph A ontology YAML/JSON text"
    )
    target_ontology: Any | None = Field(
        default=None, description="Target Graph A ontology object or dict"
    )
    target_ontology_path: str | None = Field(
        default=None, description="Path to target Graph A ontology file"
    )
    target_ontology_content: str | None = Field(
        default=None, description="Inline target Graph A ontology YAML/JSON text"
    )


class OntologyDecisionRequest(BaseContractModel):
    """Request DTO to record a steward decision and apply/reject an ontology delta."""

    action: Literal["ACCEPT", "REJECT"] = Field(
        default="ACCEPT", description="Decision action: ACCEPT or REJECT"
    )
    delta: OntologyDelta | None = Field(default=None, description="OntologyDelta object")
    delta_path: str | None = Field(default=None, description="Path to delta file")
    delta_content: str | None = Field(
        default=None, description="Inline delta JSON/YAML text (Decision D1)"
    )
    proposal_id: str | None = Field(default=None, description="Proposal identifier")
    base_ontology: Any | None = Field(
        default=None, description="Base Graph A ontology object or dict"
    )
    base_ontology_path: str | None = Field(
        default=None, description="Path to base Graph A ontology file"
    )
    base_ontology_content: str | None = Field(
        default=None, description="Inline base Graph A ontology YAML/JSON text"
    )
    contract_path: str | None = Field(
        default=None, description="Path to deployed Data Vault contract file"
    )
    contract_content: str | None = Field(
        default=None, description="Inline deployed Data Vault contract YAML text"
    )
    deployed_contract: Any | None = Field(
        default=None, description="Deployed contract object or dict"
    )
    accepted_op_ids: list[str] | None = Field(
        default=None, description="List of accepted operation IDs (None = accept all)"
    )
    rejected_op_ids: list[str] | None = Field(
        default=None, description="List of explicitly rejected operation IDs"
    )
    decided_by: str = Field(default="steward", description="Decision maker identifier")
    steward_notes: str = Field(default="", description="Steward rationale notes")
    run_id: str | None = Field(
        default=None, description="Pipeline run identifier if attached to run"
    )
    hold_id: str | None = Field(
        default=None, description="Steward hold identifier if resolving hold"
    )


class OntologyDecisionResponse(BaseContractModel):
    """Response DTO for an ontology decision action."""

    status: str = Field(default="COMPLETED", description="Status label")
    decision: OntologyDecision = Field(..., description="Recorded ontology decision record")
    resulting_ontology_yaml: str | None = Field(
        default=None, description="Serialized Graph A vN+1 YAML content if ACCEPT"
    )
    resulting_ontology_sha256: str | None = Field(
        default=None, description="CAS SHA-256 digest of resulting Graph A vN+1"
    )
    resulting_ontology_version: str | None = Field(
        default=None, description="Target version string of Graph A vN+1"
    )
    validation: OntologyValidationResult | None = Field(
        default=None, description="Validation result if delta validation was performed"
    )


class GeneralHoldOption(BaseContractModel):
    """General action option for steward holds."""

    kind: Literal["general_hold"] = "general_hold"
    action: str = Field(..., description="Steward action name")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary parameters for action"
    )
    steward_note: str = Field(default="", description="Steward notes or rationale")


class AcceptOntologyDeltaOption(BaseContractModel):
    """Steward option to accept a proposed ontology delta."""

    kind: Literal["accept_ontology_delta"] = "accept_ontology_delta"
    delta: OntologyDelta = Field(..., description="Ontology delta to accept and apply")
    delta_sha256: str = Field(..., description="Cryptographic SHA-256 of the delta")
    accepted_op_ids: list[str] = Field(
        default_factory=list,
        description="Explicit list of accepted operation IDs (empty means all)",
    )
    steward_note: str = Field(default="", description="Human steward rationale notes")


class RejectOntologyDeltaOption(BaseContractModel):
    """Steward option to explicitly reject a proposed ontology delta."""

    kind: Literal["reject_ontology_delta"] = "reject_ontology_delta"
    reason: str = Field(..., description="Rejection rationale or explanation")
    rejected_op_ids: list[str] = Field(
        default_factory=list,
        description="Explicit list of rejected operation IDs",
    )
    steward_note: str = Field(default="", description="Human steward notes")


class CustomOntologyDeltaOption(BaseContractModel):
    """Steward option providing a custom authoring delta."""

    kind: Literal["custom_ontology_delta"] = "custom_ontology_delta"
    custom_delta: OntologyDelta = Field(..., description="Custom ontology delta to apply")
    delta_sha256: str | None = Field(default=None, description="Optional SHA-256 of custom delta")
    steward_note: str = Field(default="", description="Human steward rationale notes")


StewardHoldOption = Annotated[
    AcceptOntologyDeltaOption
    | RejectOntologyDeltaOption
    | CustomOntologyDeltaOption
    | GeneralHoldOption,
    Field(discriminator="kind"),
]

OntologyDeltaOption = Annotated[
    AcceptOntologyDeltaOption | RejectOntologyDeltaOption | CustomOntologyDeltaOption,
    Field(discriminator="kind"),
]
