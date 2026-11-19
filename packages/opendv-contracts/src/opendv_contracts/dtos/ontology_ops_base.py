from __future__ import annotations

from typing import Any, ClassVar, Literal, Self

from pydantic import (
    Field,
    model_validator,
)

from opendv_contracts.dtos.common import BaseContractModel

SemVerClass = Literal["MAJOR", "MINOR", "PATCH"]

CONCEPT_URN_REGEX = (
    r"^urn:(?:ontology:(?:provisional:)?entity|"
    r"opendv:(?:provisional(?::(?:entity|concept))?|concept)):[a-z0-9_]+$"
)
RELATIONSHIP_URN_REGEX = (
    r"^urn:(?:ontology:(?:provisional:)?(?:event|relationship)|"
    r"opendv:(?:provisional(?::(?:event|relationship))?|event|relationship)):[a-z0-9_]+$"
)
ENUM_URN_REGEX = (
    r"^(urn:(?:ontology:(?:provisional:)?enum|"
    r"opendv:(?:provisional(?::enum)?|enum)):[a-z0-9_]+)?$"
)

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
    """Safely look up attribute value of a concept in GraphAOntology, dict, or legacy object."""
    if base_ontology is None:
        return None
    if isinstance(base_ontology, dict):
        for key in ("canonical_business_concepts", "concepts"):
            concepts = base_ontology.get(key)
            if concepts is None:
                continue
            if isinstance(concepts, dict):
                c_obj = concepts.get(concept_uri)
                if c_obj is not None:
                    if isinstance(c_obj, dict):
                        return c_obj.get(attr_name)
                    return getattr(c_obj, attr_name, None)
            elif isinstance(concepts, (list, tuple)):
                for c in concepts:
                    if isinstance(c, dict) and c.get("concept_uri") == concept_uri:
                        return c.get(attr_name)
                    if getattr(c, "concept_uri", None) == concept_uri:
                        return getattr(c, attr_name, None)
        return None

    get_concept_fn = getattr(base_ontology, "get_concept", None)
    if callable(get_concept_fn):
        c_obj = get_concept_fn(concept_uri)
        if c_obj is not None:
            if isinstance(c_obj, dict):
                return c_obj.get(attr_name)
            return getattr(c_obj, attr_name, None)

    for attr_collection in ("canonical_business_concepts", "concepts"):
        if hasattr(base_ontology, attr_collection):
            concepts = getattr(base_ontology, attr_collection)
            if isinstance(concepts, dict):
                c_obj = concepts.get(concept_uri)
                if c_obj is not None:
                    if isinstance(c_obj, dict):
                        return c_obj.get(attr_name)
                    return getattr(c_obj, attr_name, None)
            elif isinstance(concepts, (list, tuple)):
                for c in concepts:
                    if isinstance(c, dict) and c.get("concept_uri") == concept_uri:
                        return c.get(attr_name)
                    if getattr(c, "concept_uri", None) == concept_uri:
                        return getattr(c, attr_name, None)
    return None


def _extract_attr_spec_name_and_optional(item: Any) -> tuple[str, bool]:
    """Return `(attribute_name, is_optional)` for a string, dict, or ConceptAttribute-like item."""
    if isinstance(item, str):
        return item.strip(), True
    if isinstance(item, dict):
        name = str(item.get("attribute_name") or item.get("name") or "").strip()
        return name, item.get("is_optional", True) is not False
    name = str(getattr(item, "attribute_name", "") or "").strip()
    return name, getattr(item, "is_optional", True) is not False


def _lookup_existing_attr_map(base_ontology: Any, concept_uri: str) -> dict[str, bool] | None:
    """Return `{attribute_name: is_optional}` if `concept_uri` exists in `base_ontology`."""
    if base_ontology is None or not concept_uri:
        return None
    if _lookup_concept_attr(base_ontology, concept_uri, "concept_uri") is None:
        return None
    raw_attrs = _lookup_concept_attr(base_ontology, concept_uri, "attributes") or []
    result: dict[str, bool] = {}
    if isinstance(raw_attrs, (list, tuple)):
        for item in raw_attrs:
            a_name, is_opt = _extract_attr_spec_name_and_optional(item)
            if a_name:
                result[a_name] = is_opt
    return result


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
    source_label: str | None = Field(
        default=None, description="Human-readable source filename or label"
    )
    chunk_id: str | None = Field(default=None, description="Evidence chunk identifier")
    page_number: int | None = Field(default=None, description="1-based source page number")
    table_id: str | None = Field(default=None, description="Extracted table identifier")
    fibo_uri: str | None = Field(
        default=None, description="Referenced FIBO concept or property URI"
    )


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


class _RelationshipTargetOp(BaseOntologyDeltaOp):
    """Shared target identification and validation for relationship removal/deprecation."""

    relationship_name: str = Field(default="", description="Target relationship name")
    relationship_uri: str = Field(default="", description="Target relationship URN")

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
    def _validate_targets(self) -> Self:
        if not self.relationship_name.strip() and not self.relationship_uri.strip():
            raise ValueError(
                "At least one of 'relationship_name' or 'relationship_uri' must be non-empty"
            )
        return self


class RemoveRelationshipOp(_RelationshipTargetOp):
    """Major breaking operation removing a Unit-of-Work relationship / Link."""

    semver_class: ClassVar[SemVerClass] = "MAJOR"
    op_type: Literal["remove_relationship"] = "remove_relationship"
    reason: str = Field(default="", description="Removal rationale / reason")


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
    grounding_origin: Literal["FIBO_GROUNDED", "STRUCTURED_SCHEMA", "DOCUMENT_OBSERVED"] | None = (
        Field(
            default=None,
            description="Ground-truth origin classification for ONT-EVO-002/004/005",
        )
    )

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Return MAJOR if merging a mandatory attribute onto an existing CBC."""
        if base_ontology is not None and self.attributes:
            existing_attrs = _lookup_existing_attr_map(base_ontology, self.concept_uri)
            if existing_attrs is not None:
                for item in self.attributes:
                    a_name, is_opt = _extract_attr_spec_name_and_optional(item)
                    if not a_name:
                        continue
                    if a_name not in existing_attrs and not is_opt:
                        return "MAJOR"
                    if a_name in existing_attrs and existing_attrs[a_name] != is_opt:
                        return "MAJOR"
        return self.semver_class


class AddRelationshipOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Unit-of-Work relationship / Link."""

    semver_class: ClassVar[SemVerClass] = "MINOR"
    op_type: Literal["add_relationship"] = "add_relationship"
    relationship_uri: str = Field(
        ...,
        pattern=RELATIONSHIP_URN_REGEX,
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

    def effective_semver_class(self, base_ontology: Any = None) -> SemVerClass:
        """Compute the effective SemVer classification of this operation."""
        if (
            not self.is_optional
            and base_ontology is not None
            and _lookup_concept_attr(base_ontology, self.concept_uri, "concept_uri") is not None
        ):
            return "MAJOR"
        return self.semver_class


