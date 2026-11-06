from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


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
    """Abstract base class for all additive ontology delta operations."""

    op_id: str = Field(..., description="Unique operation identifier")
    op_type: str = Field(..., description="Additive operation type discriminator")
    rationale: str = Field(default="", description="Business justification for the operation")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Operation confidence score")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Grounding evidence references"
    )


class AddConceptOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Canonical Business Concept (CBC) / Hub."""

    op_type: Literal["add_concept"] = "add_concept"
    concept_uri: str = Field(
        ...,
        pattern=r"^urn:ontology:(provisional:)?entity:[a-z0-9_]+$",
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
    governance_constraints: dict[str, Any] | None = Field(
        default=None, description="Governance, PII, and MNPI constraints"
    )
    key_authority_tiers: dict[str, Any] | None = Field(
        default=None, description="3-Tier Business Key Authority specification"
    )


class AddRelationshipOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Unit-of-Work relationship / Link."""

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
    source_concept_uri: str | None = Field(default=None, description="Source endpoint concept URN")
    target_concept_uri: str | None = Field(default=None, description="Target endpoint concept URN")
    endpoints: list[str] = Field(
        default_factory=list, description="List of participant concept URN endpoints"
    )
    grain_semantics: str = Field(default="", description="Relationship grain semantics")
    description: str = Field(default="", description="Alternative relationship description")
    yagni_cost_guard_policy: dict[str, Any] | None = Field(
        default=None, description="Attached Gate 4 YAGNI cost guard policy"
    )


class AddReferenceEnumerationOp(BaseOntologyDeltaOp):
    """Additive operation to introduce a Reference Enumeration / ref_* table."""

    op_type: Literal["add_reference_enumeration"] = "add_reference_enumeration"
    enum_uri: str = Field(
        default="",
        pattern=r"^(urn:ontology:enum:[a-z0-9_]+)?$",
        description="Unique reference enumeration URN",
    )
    name: str = Field(default="", description="Enumeration name")
    target_reference_table: str = Field(
        default="",
        pattern=r"^(ref_[a-z0-9_]+)?$",
        description="Target reference table name",
    )
    values: list[str] = Field(default_factory=list, description="Allowed code values")
    allowed_codes: list[str] = Field(default_factory=list, description="Allowed code values")
    description: str = Field(default="", description="Enumeration description")


class AddKeyAuthorityPatternOp(BaseOntologyDeltaOp):
    """Additive operation to attach a Business Key Authority tier pattern."""

    op_type: Literal["add_key_authority_pattern"] = "add_key_authority_pattern"
    concept_uri: str = Field(..., description="Target Canonical Business Concept URN")
    authority_name: str = Field(..., description="Name of the Business Key Authority")
    pattern_regex: str | None = Field(default=None, description="Regex pattern for authority")
    tier: str = Field(default="TIER_1_GLOBAL_STANDARD", description="Authority tier classification")
    tier_spec: dict[str, Any] | None = Field(default=None, description="Full tier specification")
    description: str = Field(default="", description="Authority description")


class AddGovernanceConstraintOp(BaseOntologyDeltaOp):
    """Additive operation to attach PII / MNPI security and satellite split constraints."""

    op_type: Literal["add_governance_constraint"] = "add_governance_constraint"
    concept_uri: str = Field(..., description="Target Canonical Business Concept URN")
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


class RefineDefinitionOp(BaseOntologyDeltaOp):
    """Additive operation to refine the textual definition or attributes of a concept."""

    op_type: Literal["refine_definition"] = "refine_definition"
    target_uri: str = Field(default="", description="Target concept or relationship URN")
    concept_uri: str = Field(default="", description="Target concept URN")
    refined_description: str = Field(default="", description="Refined definition or description")
    additional_attributes: list[str] = Field(
        default_factory=list, description="Additional attributes to append"
    )


class AddSourceHintOp(BaseOntologyDeltaOp):
    """Additive operation to register a source mapping hint linking source data to a CBC."""

    op_type: Literal["add_source_hint"] = "add_source_hint"
    concept_uri: str = Field(..., description="Target Canonical Business Concept URN")
    source_name: str = Field(default="", description="Source system identifier")
    source_system: str = Field(default="", description="Source system identifier")
    table_name: str = Field(default="", description="Source table or file name")
    column_hint: str = Field(default="", description="Source column hint")
    column_name: str = Field(default="", description="Source column name")


OntologyDeltaItem = Annotated[
    AddConceptOp
    | AddRelationshipOp
    | AddReferenceEnumerationOp
    | AddKeyAuthorityPatternOp
    | AddGovernanceConstraintOp
    | RefineDefinitionOp
    | AddSourceHintOp,
    Field(discriminator="op_type"),
]


class OntologyDelta(BaseContractModel):
    """Ordered collection of additive operations evolving Graph A to vN+1."""

    delta_id: str = Field(..., min_length=1, description="Unique delta identifier")
    parent_sha256: str | None = Field(
        default=None,
        pattern=r"^([a-f0-9]{64})?$",
        description="Cryptographic SHA-256 of the base Graph A ontology",
    )
    domain_namespace: str | None = Field(
        default=None,
        pattern=r"^([a-z][a-z0-9_]*)?$",
        description="Domain namespace matching base Graph A",
    )
    base_ontology_version: str = Field(default="1.0.0", description="Base ontology version")
    target_ontology_version: str = Field(
        default="1.1.0", description="Target ontology version after delta application"
    )
    operations: list[OntologyDeltaItem] = Field(
        default_factory=list, description="Ordered sequence of additive operations"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary authoring provenance metadata"
    )


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
    AcceptOntologyDeltaOption
    | RejectOntologyDeltaOption
    | CustomOntologyDeltaOption,
    Field(discriminator="kind"),
]

