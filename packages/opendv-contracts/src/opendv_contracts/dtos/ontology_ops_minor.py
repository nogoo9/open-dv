from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import AliasChoices, Field, model_validator

from opendv_contracts.dtos.ontology_ops_base import (
    CONCEPT_URN_REGEX,
    ENUM_URN_REGEX,
    BaseOntologyDeltaOp,
    SemVerClass,
    _assert_no_alias_conflict,
    _check_and_sync_alias_pair,
    _lookup_concept_attr,
    _RelationshipTargetOp,
)


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


class DeprecateRelationshipOp(_RelationshipTargetOp):
    """Minor operation marking a relationship as deprecated."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["deprecate_relationship"] = "deprecate_relationship"
    reason: str | None = Field(default=None, description="Deprecation rationale / reason")


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

            data = dict(data)
            mapping = data.get("mapping")
            if isinstance(mapping, dict):
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

            # Synchronize confidence <-> mapping_confidence when only one is supplied
            has_explicit_conf = "confidence" in data and data["confidence"] is not None
            has_explicit_map_conf = (
                "mapping_confidence" in data and data["mapping_confidence"] is not None
            )
            if has_explicit_conf and not has_explicit_map_conf:
                data["mapping_confidence"] = data["confidence"]
            elif has_explicit_map_conf and not has_explicit_conf:
                data["confidence"] = data["mapping_confidence"]

        return data

    @model_validator(mode="after")
    def _validate_mapping_target(self) -> AddExternalMappingOp:
        if not self.target_uri.strip():
            raise ValueError("At least one of 'target_uri' or 'standard_uri' must be non-empty")
        return self


