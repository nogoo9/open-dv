from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AliasChoices, Field, model_validator

from opendv_contracts.dtos.ontology_ops_base import (
    CONCEPT_URN_REGEX,
    AddAttributeOp,
    AddConceptOp,
    AddRelationshipOp,
    BaseOntologyDeltaOp,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveRelationshipOp,
    SemVerClass,
    UpdateConceptUrnOp,
    _assert_no_alias_conflict,
    _check_and_sync_alias_pair,
    _check_name_and_uri_compatibility,
    _extract_attr_spec_name_and_optional,
    _lookup_existing_attr_map,
)
from opendv_contracts.dtos.ontology_ops_minor import (
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddSynonymOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
)


class ReviseDescriptionOp(BaseOntologyDeltaOp):
    """Patch operation revising textual description of a concept."""

    semver_class: ClassVar[SemVerClass] = "PATCH"
    op_type: Literal["revise_description"] = "revise_description"
    concept_uri: str = Field(
        default="",
        pattern=f"^({CONCEPT_URN_REGEX[1:-1]})?$",
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
            has_new = ("new_confidence" in data and data["new_confidence"] is not None) or (
                "mapping_confidence" in data and data["mapping_confidence"] is not None
            )
            has_conf = "confidence" in data and data["confidence"] is not None
            if has_conf and not has_new:
                data = dict(data)
                data["new_confidence"] = data["confidence"]
            elif has_new and not has_conf:
                data = dict(data)
                val = data.get("new_confidence")
                if val is None:
                    val = data.get("mapping_confidence")
                data["confidence"] = val
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

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Compute effective SemVer class: MAJOR for mandatory attr, MINOR for optional attr."""
        if self.additional_attributes:
            target_u = self.concept_uri or self.target_uri
            existing_attrs = (
                _lookup_existing_attr_map(base_ontology, target_u)
                if (base_ontology is not None and target_u)
                else None
            )
            has_added = False
            for item in self.additional_attributes:
                a_name, is_opt = _extract_attr_spec_name_and_optional(item)
                if not a_name:
                    continue
                if existing_attrs is not None and a_name in existing_attrs:
                    if existing_attrs[a_name] != is_opt:
                        return "MAJOR"
                    continue
                has_added = True
                if not is_opt and (base_ontology is None or existing_attrs is not None):
                    return "MAJOR"
            if has_added and (base_ontology is None or existing_attrs is not None):
                return "MINOR"
        return self.semver_class


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

# Backward-compatibility aliases
OntologyDeltaItem = OntologyOperation
OntologyDeltaOp = OntologyOperation

# ---------------------------------------------------------------------------
# Authoritative Governance & ONT-EVO-001 / ONT-EVO-002 Constants
# ---------------------------------------------------------------------------

EXISTING_CONCEPT_MUTATING_OP_TYPES: frozenset[str] = frozenset(
    {
        "update_concept_urn",
        "set_parent_concept",
        "set_concept_elm_archetype",
        "deprecate_concept",
        "add_attribute",
        "remove_attribute",
        "add_key_authority_pattern",
        "add_governance_constraint",
        "add_synonym",
        "add_external_mapping",
        "revise_description",
        "refine_definition",
        "rescore_external_mapping",
        "repin_external_mapping",
        "remove_external_mapping",
        "add_source_hint",
        "annotate_concept_metadata",
    }
)

CONCEPT_MUTATING_OP_TYPES: frozenset[str] = EXISTING_CONCEPT_MUTATING_OP_TYPES | frozenset(
    {"add_concept"}
)
CONCEPT_MUTATING_OPS: frozenset[str] = CONCEPT_MUTATING_OP_TYPES

RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES: frozenset[str] = frozenset(
    {
        "remove_relationship",
        "deprecate_relationship",
    }
)

DESTRUCTIVE_OP_TYPES: frozenset[str] = frozenset(
    {
        "remove_concept",
        "remove_relationship",
        "remove_attribute",
        "update_concept_urn",
    }
)

ALLOWED_PATH_A_PREDICATES: frozenset[str] = frozenset(
    {
        "skos:exactMatch",
        "skos:closeMatch",
        "http://www.w3.org/2004/02/skos/core#exactMatch",
        "http://www.w3.org/2004/02/skos/core#closeMatch",
    }
)

PATH_A_GROUNDING_THRESHOLD: float = 0.65


