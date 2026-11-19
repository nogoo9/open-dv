from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, ValidatorFunctionWrapHandler, field_validator, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ontology_delta import OntologyDelta
from opendv_contracts.dtos.ontology_waiver import _try_validate_bootstrap_delta_field


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

    @model_validator(mode="before")
    @classmethod
    def _coerce_confidence_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "confidence" in data:
            copied = dict(data)
            conf = copied.pop("confidence")
            if "confidence_score" not in copied and conf is not None:
                copied["confidence_score"] = conf
            return copied
        return data

    @property
    def confidence(self) -> float:
        """Backwards-compatible alias for `confidence_score`."""
        return self.confidence_score

    @field_validator("delta", mode="wrap")
    @classmethod
    def _coerce_bootstrap_proposal_delta(cls, v: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        boot = _try_validate_bootstrap_delta_field(v)
        if boot is not None:
            return boot
        if (
            isinstance(v, OntologyDelta)
            and v.base_ontology_version == "1.0.0"
            and v.target_ontology_version == "1.0.0"
            and v.operations
        ):
            return handler(v.model_dump(mode="python"))
        return handler(v)


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

    @field_validator("delta", mode="wrap")
    @classmethod
    def _coerce_bootstrap_decision_delta(cls, v: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        boot = _try_validate_bootstrap_delta_field(v)
        if boot is not None:
            return boot
        if (
            isinstance(v, OntologyDelta)
            and v.base_ontology_version == "1.0.0"
            and v.target_ontology_version == "1.0.0"
            and v.operations
        ):
            return handler(v.model_dump(mode="python"))
        return handler(v)


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


def is_destructive_delta(delta: OntologyDelta, base_ontology: Any = None) -> bool:
    """Return True iff `delta` requires a MAJOR SemVer bump against `base_ontology`."""
    return delta.highest_semver_class(base_ontology) == "MAJOR"


class ConstructRequest(BaseContractModel):
    """Request payload to trigger ontology construction job."""

    base_version: str | None = Field(default=None, description="Base ontology version")
    base_sha256: str | None = Field(
        default=None, pattern=r"^([a-f0-9]{64}|)$", description="Base ontology digest"
    )
    auto_commit: bool = Field(
        default=False, description="Whether to automatically commit proposal if valid"
    )
    fibo_modules: list[str] = Field(
        default_factory=lambda: ["FND", "BE", "FBC", "SEC", "LOAN", "IND"],
        description="FIBO module codes included in search space",
    )
    mode: Literal["auto", "agent", "deterministic"] | str | None = Field(
        default="auto", description="Construction execution engine mode"
    )
    steering_prompt: str | None = Field(
        default=None,
        max_length=5000,
        description="Optional natural-language steering prompt guiding selection and hierarchy",
    )


class SteeringRefineRequest(BaseContractModel):
    """Request payload to refine an existing proposal using natural-language steering prompts."""

    steering_prompt: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Natural-language steering guidance for refining ontology proposal",
    )
    mode: Literal["auto", "agent", "deterministic"] | str = Field(
        default="auto", description="Execution engine mode"
    )
    base_version: str | None = Field(default=None, description="Base ontology version precondition")
    base_sha256: str | None = Field(
        default=None,
        pattern=r"^([a-f0-9]{64}|)$",
        description="Base ontology SHA-256 digest precondition",
    )


RefineProposalRequest = SteeringRefineRequest
