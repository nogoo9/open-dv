"""Wire DTOs for System, Secrets, Agents, Seeds, Sources, and Steps 4-5 Discovery."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from opendv.models.diagnostics import (
    ClarificationQuestion,
    DocumentCitationRef,
    HorizonQuestion,
    StrictModel,
    VerifierDiagnostic,
)
from opendv.models.ontology import GraphAOntology
from opendv.models.ontology_deltas import GroundingWaiverMixin, OntologyDelta
from opendv.models.policy import CegarPolicy, ModelTier
from opendv.models.project import (
    DiscoveryMode,
    EvolutionLockSpec,
    OntologyProposalSpec,
    OntologyWaiverSpec,
    StagedCandidateSpec,
    validate_domain_namespace,
    validate_project_id,
)


class HealthResp(StrictModel):
    """Response for GET /api/v1/health."""

    status: Literal["ok"] = "ok"
    version: str


class SecretWriteReq(StrictModel):
    """Write-only payload for POST /api/v1/secrets."""

    key: str = Field(min_length=1)
    value: str = Field(min_length=1)


class SecretSummary(StrictModel):
    """Masked metadata summary for a stored secret key."""

    key: str
    present: bool
    backend: Literal["keyring", "user_state_file", "env", "unset"]


class AgentPackSummary(StrictModel):
    """Summary metadata for a builtin or trusted workspace SKILL.md agent pack."""

    agent_id: str
    name: str
    role: str
    stage: Literal["step4", "step6"]
    source: Literal["builtin", "workspace_override"]
    sha256: str


class AgentPackDetail(StrictModel):
    """Full agent pack inspection payload."""

    summary: AgentPackSummary
    markdown_body: str
    model_tier: ModelTier
    trusted: bool


class WorkspaceSeedSummary(StrictModel):
    """Discovered domain seed ontology under the active workspace."""

    seed_id: str
    domain_namespace: str
    relpath: str
    concept_count: int = Field(ge=0)


class StarterPackSummary(StrictModel):
    """Built-in starter pack metadata."""

    pack_id: str
    domain_namespace: str
    title: str
    hub_count: int = Field(ge=0)


class StarterPackPreview(StrictModel):
    """Full GraphAOntology preview for a built-in starter pack."""

    pack_id: str
    ontology: GraphAOntology


class ProjectInitReq(StrictModel):
    """Request payload for POST /api/v1/projects."""

    project_id: str
    domain_namespace: str
    starter_pack_id: str | None = None
    workspace_seed_id: str | None = None
    baseline_content: str | None = None
    baseline_format: Literal["yaml", "json"] = "yaml"

    @field_validator("project_id")
    @classmethod
    def _check_pid(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("domain_namespace")
    @classmethod
    def _check_ns(cls, v: str) -> str:
        return validate_domain_namespace(v)


class ProjectImportPathReq(StrictModel):
    """JSON body for path-based POST /api/v1/projects/import."""

    bundle_path: str = Field(min_length=1)
    conflict: Literal["fail", "rename", "replace"] = "fail"
    trust_workspace: bool = False


class SourceInspectReq(StrictModel):
    """Request payload for POST /api/v1/sources/inspect."""

    kind: str
    uri_or_path: str
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)


class SourceAttachReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/sources."""

    kind: str
    uri_or_filename: str
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)
    selected_tables: list[str] | None = None
    profile_timeout_seconds: int = Field(default=30, ge=1)


class SourceReattachReq(StrictModel):
    """Request payload for JSON PATCH /api/v1/projects/{id}/sources/{sid}."""

    uri_or_filename: str | None = None
    options: dict[str, str | int | bool] | None = None
    secret_refs: list[str] | None = None
    selected_tables: list[str] | None = None


class ScanApprovalReq(StrictModel):
    """Steward consent payload for scanned document pages."""

    page_numbers: list[int]
    rationale: str = Field(min_length=1)
    allow_unredacted_external_ocr: bool = False


class ConceptMemoryUpdate(StrictModel):
    """Update term pin/mute state in ConceptMemory."""

    term: str = Field(min_length=1)
    state: Literal["PINNED", "MUTED", "UNSET"]
    concept_uri: str | None = None


class OntologyConstructReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/ontology/construct."""

    discovery_mode: DiscoveryMode = "incremental_new"
    hint: str | None = None


class RunControlReq(StrictModel):
    """Run control signal payload for Step 4 and Step 6."""

    action: Literal["pause", "hint", "accept-best", "raise-budget", "horizon-review"]
    hint: str | None = None
    extra_turns: int | None = Field(default=None, ge=1)
    extra_tokens: int | None = Field(default=None, ge=1)
    extra_timeout_seconds: int | None = Field(default=None, ge=1)


class HoldAnswersReq(StrictModel):
    """Steward answers resuming a HOLD_CHECKPOINTED run."""

    answers: dict[str, str] = Field(default_factory=dict)


RunStatus = Literal[
    "QUEUED",
    "RUNNING",
    "HOLD_REQUESTED",
    "HOLD_CHECKPOINTED",
    "UNCERTIFIED_DRAFT",
    "COMPLETED",
    "FAILED",
]


class RunStateResp(StrictModel):
    """Unified Step 4 & Step 6 async run state payload."""

    run_id: str
    project_id: str | None = Field(default=None, min_length=1)
    step: Literal["step4", "step6"]
    status: RunStatus
    policy: CegarPolicy | None = None
    phase: str
    attempt: int = Field(ge=0)
    budget_turns_used: int = Field(ge=0)
    budget_tokens_used: int = Field(ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    effective_max_turns: int | None = None
    effective_max_tokens: int | None = None
    effective_timeout_seconds: int | None = None
    pending_questions: list[ClarificationQuestion | HorizonQuestion] = Field(default_factory=list)
    last_seq: int = Field(default=0, ge=0)


class HoldAck(StrictModel):
    """Coordinator MCP acknowledgement when raising a Steward Hold."""

    run_id: str
    checkpoint_seq: int = Field(ge=0)
    status: Literal["HOLD_CHECKPOINTED"] = "HOLD_CHECKPOINTED"
    pending_question_ids: list[str]


class ScoutDigest(StrictModel):
    """Structured digest returned by isolated Step 4 scouts."""

    scout_kind: Literal["table_cluster", "page_range"]
    target_id: str
    candidate_concepts: list[str] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)
    citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_receipt_ids: list[str] = Field(default_factory=list)
    pii_columns: list[str] = Field(default_factory=list)


class ImageOcrResult(StrictModel):
    """Result returned by scout view_page_image tool."""

    redacted_png_b64: str | None = None
    ocr_text: str
    ocr_box_count: int = Field(ge=0)
    receipt_id: str | None = None
    redaction_applied: bool


class ExternalMcpToolResult(StrictModel):
    """Masked result returned by ExternalMcpGateway."""

    receipt_id: str
    content_sha256: str
    masked_excerpt: str


class ConceptSummary(StrictModel):
    """Compact concept search result across committed, staged, memory, and seed layers."""

    concept_uri: str
    concept_name: str
    source_layer: Literal["committed_graph_a", "in_run", "concept_memory", "workspace_seed"]
    locked_in_vault: bool


class FiboQueryReq(StrictModel):
    """Query payload for Ask FIBO BM25 search."""

    query: str = Field(min_length=1)
    domain_namespace: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class FiboMatchSpec(StrictModel):
    """Single BM25 FIBO match with quantized Decimal string scores."""

    concept_uri: str
    label: str
    parent_uri: str | None = None
    normalized_score: str
    raw_bm25: str
    suggested_path: Literal["PATH_A1", "PATH_A2"]
    definition: str


class FiboQueryResp(StrictModel):
    """Response payload for Ask FIBO BM25 search."""

    matches: list[FiboMatchSpec]


class CitationResolveReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/ontology/citations/resolve."""

    doc_id: str
    page_number: int = Field(ge=1)
    verbatim_quote: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    content_sha256: str | None = None


class CitationSpanResp(StrictModel):
    """Resolved span offsets inside normalized page text or OCR receipt."""

    char_start: int | None = None
    char_end: int | None = None
    matched: bool
    resolved_sha256: str | None = None
    source_kind: Literal["canonical_text", "ocr_receipt", "absent"]


class ClientAdditiveOp(StrictModel):
    """Wrapper for a single OntologyDelta proposed via Coordinator MCP."""

    op: OntologyDelta


class DeltaStageResp(StrictModel):
    """Response for Coordinator propose_ontology_delta tool."""

    staged_count: int = Field(ge=0)
    candidate_ids: list[str]
    diagnostics: list[VerifierDiagnostic]


class OntologyValidateReq(StrictModel):
    """Validation payload for Step 5 Mode 3/4 (XOR yaml_text vs operations)."""

    yaml_text: str | None = None
    operations: list[OntologyDelta] | None = None
    base_revision_id: str | None = None

    @model_validator(mode="after")
    def _validate_xor(self) -> OntologyValidateReq:
        if (self.yaml_text is None) == (self.operations is None):
            raise ValueError("Exactly one of yaml_text or operations must be provided")
        return self


class ValidationBumpSpec(StrictModel):
    """Proposed SemVer bump computed during ontology validation."""

    current_version: str
    next_version: str
    bump_kind: Literal["NONE", "PATCH", "MINOR", "MAJOR"]
    breaking_reasons: list[str] = Field(default_factory=list)


class OntologyValidationResp(StrictModel):
    """Server validation result for Graph A YAML or delta operations."""

    valid: bool
    diagnostics: list[VerifierDiagnostic]
    proposed_bump: ValidationBumpSpec


class OntologyCommitReq(GroundingWaiverMixin):
    """OCC-protected ontology commit request."""

    yaml_text: str | None = None
    operations: list[OntologyDelta] | None = None
    base_revision_id: str
    rationale: str
    waive_quarantine: bool = False
    ont_ap_waivers: list[OntologyWaiverSpec] = Field(default_factory=list)
    grounding_waiver: GroundingWaiverMixin | None = None
    committed_proposal_ids: list[str] = Field(default_factory=list)
    dismissed_proposal_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_commit_req(self) -> OntologyCommitReq:
        if (self.yaml_text is None) == (self.operations is None):
            raise ValueError("Exactly one of yaml_text or operations must be provided")
        if self.waive_quarantine and len(self.rationale.strip()) < 20:
            raise ValueError(
                "waive_quarantine=True requires rationale of at least 20 non-whitespace chars"
            )
        return self


class OntologyRevisionResp(StrictModel):
    """Committed Graph A revision receipt."""

    revision_id: str
    ontology_version: str
    content_sha256: str
    updated_at: str


class OntologyBundleResp(StrictModel):
    """Complete Step 5 ontology hydration bundle."""

    ontology: GraphAOntology
    staged_candidates: list[StagedCandidateSpec] = Field(default_factory=list)
    ontology_proposals: list[OntologyProposalSpec] = Field(default_factory=list)
    evolution_locks: EvolutionLockSpec | None = None


__all__ = [
    "AgentPackDetail",
    "AgentPackSummary",
    "CitationResolveReq",
    "CitationSpanResp",
    "ClarificationQuestion",
    "ClientAdditiveOp",
    "ConceptMemoryUpdate",
    "ConceptSummary",
    "DeltaStageResp",
    "ExternalMcpToolResult",
    "FiboMatchSpec",
    "FiboQueryReq",
    "FiboQueryResp",
    "HealthResp",
    "HoldAck",
    "HoldAnswersReq",
    "ImageOcrResult",
    "OntologyBundleResp",
    "OntologyCommitReq",
    "OntologyConstructReq",
    "OntologyRevisionResp",
    "OntologyValidateReq",
    "OntologyValidationResp",
    "ProjectImportPathReq",
    "ProjectInitReq",
    "RunControlReq",
    "RunStateResp",
    "RunStatus",
    "ScanApprovalReq",
    "ScoutDigest",
    "SecretSummary",
    "SecretWriteReq",
    "SourceAttachReq",
    "SourceInspectReq",
    "SourceReattachReq",
    "StarterPackPreview",
    "StarterPackSummary",
    "ValidationBumpSpec",
    "WorkspaceSeedSummary",
]
