"""Pydantic wire DTOs for Ontology Construction Studio REST & SSE API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_contracts.dtos.clarification import ClarificationAnswerItem, ClarifyRequest


class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    domain_namespace: str = "default"
    enterprise_owner: str = "Enterprise"
    description: str | None = None
    from_workspace: str | None = None


class ImportOntologyRequest(BaseModel):
    session_id: str | None = None
    domain_namespace: str = "default"
    enterprise_owner: str = "Enterprise"
    format: str = "yaml"
    content: str = Field(..., min_length=1)
    expected_sha256: str | None = None
    description: str | None = None


class StudioSessionSummaryDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    created_at: str
    updated_at: str
    wizard_schema_version: int = 2


class WizardSessionListResponse(BaseModel):
    sessions: list[StudioSessionSummaryDTO]
    legacy_session_count: int = 0


class StudioSessionDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    head_sha256: str
    created_at: str
    updated_at: str
    wizard_schema_version: int = 2


class StudioSessionDetailDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    head_sha256: str
    created_at: str
    updated_at: str
    sources_count: int
    proposals_count: int
    versions_count: int
    grandfathered_concept_uris: list[str] = Field(default_factory=list)
    wizard_schema_version: int = 2


class StudioSessionStatusDTO(BaseModel):
    session_id: str
    status: str
    active_version: str
    head_sha256: str
    active_jobs: int = 0
    taint_classification: str = "UNTAINTED"


class ManifestDTO(BaseModel):
    session_id: str
    files: list[dict[str, Any]]
    total_bytes: int


class SourceDocumentDTO(BaseModel):
    source_id: str
    source_sha256: str
    file_name: str
    filename: str = ""
    size_bytes: int
    byte_count: int = 0
    mime_type: str
    do_not_send_to_agent: bool
    tombstoned: bool = False


class SourceUploadResponse(BaseModel):
    source_id: str
    source_sha256: str
    file_name: str
    filename: str = ""
    size_bytes: int
    byte_count: int = 0
    mime_type: str
    do_not_send_to_agent: bool
    ocr_mode: str | None = None


class TextSourceUploadRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10_000_000)
    source_name: str | None = Field(default=None, max_length=256)
    format: str = Field(default="text")
    do_not_send_to_agent: bool = Field(default=False)
    description: str | None = Field(default=None, max_length=1000)


class ConnectorSourceRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    adapter: str = Field(..., min_length=1)
    uri: str | None = None
    table: str | None = None
    namespace: str | dict[str, Any] | None = None
    credentials_ref: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    cas_hash: str | None = None
    source_name: str | None = None

    @model_validator(mode="after")
    def _validate_uri_or_cas_and_no_allowed_hosts(self) -> ConnectorSourceRequest:
        if "allowed_hosts" in (self.model_extra or {}):
            raise ValueError(
                "Client-supplied 'allowed_hosts' is forbidden; configure server OPENDV_ALLOWED_HOSTS."
            )
        has_uri = bool(self.uri and self.uri.strip())
        has_cas = bool(self.cas_hash and self.cas_hash.strip())
        if not (has_uri or has_cas):
            raise ValueError("Either 'uri' or 'cas_hash' must be provided.")
        return self


class ParseRequest(BaseModel):
    source_ids: list[str] | None = None
    ocr_mode: str = "auto"


class StudioClarifyRequest(ClarifyRequest):
    base_version: str | None = None


class ConstructRequest(BaseModel):
    base_version: str | None = None
    base_sha256: str | None = None
    auto_commit: bool = False
    fibo_modules: list[str] = Field(
        default_factory=lambda: ["FND", "BE", "FBC", "SEC", "LOAN", "IND"]
    )
    mode: str | None = None
    steering_prompt: str | None = None


class RefineProposalRequest(BaseModel):
    steering_prompt: str = Field(..., min_length=1, max_length=5000)
    mode: str = Field(default="auto")


class JobHandleDTO(BaseModel):
    job_id: str
    session_id: str
    job_type: str
    status: str
    created_at: str


class JobStatusDTO(BaseModel):
    job_id: str
    session_id: str
    job_type: str
    status: str
    stage: str
    pct_complete: int
    created_at: str
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class StudioClarificationAnswerRequest(BaseModel):
    answers: list[ClarificationAnswerItem | dict[str, Any]] | dict[str, Any] = Field(
        default_factory=list
    )
    expected_answers_sha256: str | None = None


class OntologyDeltaRequest(BaseModel):
    delta_id: str | None = None
    parent_sha256: str | None = None
    base_sha256: str | None = None
    domain_namespace: str | None = None
    base_version: str | None = None
    target_version: str | None = None
    operations: list[dict[str, Any]]
    author: str | None = None
    rationale: str | None = None
    low_confidence_waiver: bool = False
    grounding_rationale: str | None = None
    ungrounded_concept: list[str] | str | None = None
    evidence_grounding_file: str | None = None


class DeltaCommitResponse(BaseModel):
    session_id: str
    target_version: str
    target_sha256: str
    parent_sha256: str
    delta_id: str
    bump_type: str
    applied_operations_count: int


class ProposalCreateRequest(BaseModel):
    proposal_id: str | None = None
    base_version: str | None = None
    base_sha256: str | None = None
    operations: list[dict[str, Any]]
    rationale: str | None = None


class ProposalRecordDTO(BaseModel):
    proposal_id: str
    session_id: str
    base_version: str
    base_sha256: str
    status: str
    operations: list[dict[str, Any]]
    proposer: str
    created_at: str
    rationale: str | None = None


class ProposalAcceptRequest(BaseModel):
    base_sha256: str | None = None
    low_confidence_waiver: bool = False
    self_acceptance_override: bool = False
    grounding_rationale: str | None = None
    evidence_grounding_file: str | None = None
    ungrounded_concept: list[str] | str | None = None


class ProposalRebaseRequest(BaseModel):
    base_sha256: str | None = None


class RejectProposalRequest(BaseModel):
    reason: str | None = None


class WaiverSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    waiver_id: str | None = None
    proposal_id: str | None = None
    proposal_delta_sha256: str | None = None
    concept_uri: str | None = None
    target_concept_uris: list[str] | None = None
    concept_uris: list[str] | None = None
    ungrounded_concepts: list[str] | None = None
    base_sha256: str | None = None
    rationale: str = ""
    authorized_by: str | None = None
    steward: str | None = None
    authorized_role: str | None = None
    signature_or_token: str | None = None
    created_at: str | None = None
    expires_at: str | None = None


class FeedbackSubmissionRequest(BaseModel):
    rating: str | int
    comment: str | None = Field(default=None, max_length=4000)
    concept_uri: str | None = None
    proposal_id: str | None = None


class FeedbackSubmissionResponse(BaseModel):
    status: str
    feedback_id: str
    session_id: str
    active_version: str
    head_sha256: str
    timestamp: str


class CloneSessionRequest(BaseModel):
    new_session_id: str | None = None


class PromoteRequest(BaseModel):
    workspace_sha256: str | None = None
    head_sha256: str | None = None
    rationale: str = ""
    workspace: str | None = None
    acknowledge_historical_contract_warning: bool = False
    historical_contract_rationale: str | None = None


class PromoteResponse(BaseModel):
    session_id: str
    promoted_version: str
    head_sha256: str
    workspace_sha256: str
    promoted_at: str
    target_contract_path: str


class BundleExportRequest(BaseModel):
    acknowledge_confidential_source_export: bool = False
    export_rationale: str | None = None


class ResolveCitationsRequest(BaseModel):
    citations: list[dict[str, Any]] = Field(default_factory=list)


class FiboQAApiRequest(BaseModel):
    question: str = Field(..., min_length=1)
    source_ids: list[str] | None = None
    concept_uri: str | None = None


class InitStudioRequest(BaseModel):
    domain_namespace: str | None = None
    enterprise_owner: str | None = None
    force: bool = False


class InitStudioResponse(BaseModel):
    status: str
    token_path: str
    principal: str
    role: str | None = None
    local_token: str | None = None
    domain_namespace: str | None = None


class LaunchNonceResponse(BaseModel):
    launch_nonce: str
    nonce: str
    expires_in: int = 60


class TokenExchangeRequest(BaseModel):
    launch_nonce: str | None = None
    nonce: str | None = None


class TokenExchangeResponse(BaseModel):
    status: str
    access_token: str | None = None
    principal: str
    role: str | None = None
