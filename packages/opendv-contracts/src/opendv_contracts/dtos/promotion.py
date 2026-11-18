"""Data transfer objects for held-back candidate queries and Path A / Path B promotion."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel


class StagedCandidateDTO(BaseContractModel):
    """Held-back candidate concept in the staging holding area."""

    candidate_id: str = Field(..., description="Unique staging identifier")
    concept_name: str = Field(..., description="Proposed business concept name")
    suggested_hub: str = Field(..., description="Suggested physical hub slug (e.g. hub_slug)")
    top_match_uri: str | None = Field(default=None, description="Top reference URI")
    top_match_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Highest BM25 match score"
    )
    reason_held_back: str = Field(
        default="GROUNDING_THRESHOLD_UNMET", description="Reason candidate was withheld"
    )
    status: Literal["held_back", "promoted", "rejected"] = Field(
        default="held_back", description="Staging lifecycle status"
    )
    evidence_refs: list[dict[str, Any]] = Field(
        default_factory=list, description="Citations to source evidence chunks"
    )
    attributes: list[dict[str, Any]] = Field(
        default_factory=list, description="Candidate conceptual attributes"
    )
    dependent_links: list[dict[str, Any]] = Field(
        default_factory=list, description="Associated candidate link relationships"
    )


class StagedCandidatesQueryResponse(BaseContractModel):
    """Response payload querying all currently staged candidates for a session."""

    session_id: str = Field(..., description="Session identifier")
    staged_candidates: list[StagedCandidateDTO] = Field(
        default_factory=list, description="Collection of staged candidates"
    )
    candidates: list[StagedCandidateDTO] = Field(
        default_factory=list, description="Backwards-compatibility alias for staged_candidates"
    )
    total_count: int = Field(default=0, ge=0, description="Total count of held-back candidates")

    @model_validator(mode="after")
    def _sync_candidate_lists(self) -> StagedCandidatesQueryResponse:
        if self.staged_candidates and not self.candidates:
            object.__setattr__(self, "candidates", self.staged_candidates)
        elif self.candidates and not self.staged_candidates:
            object.__setattr__(self, "staged_candidates", self.candidates)
        if self.total_count == 0:
            object.__setattr__(self, "total_count", len(self.staged_candidates))
        return self


class PromoteCandidateRequest(BaseContractModel):
    """Request payload to promote a held-back candidate via Path A or Path B."""

    path: Literal["path_a", "path_b"] = Field(
        default="path_a",
        description="Promotion path: Path A (synonym re-scoring) or Path B (signed waiver)",
    )
    synonyms: list[str] | None = Field(
        default=None, description="Path A: Additional synonyms to elevate BM25 score >= 0.65"
    )
    waiver_rationale: str | None = Field(
        default=None,
        description="Path B: Architectural justification for ungrounded waiver (>= 10 chars)",
    )
    rationale: str | None = Field(default=None, description="Alias for waiver_rationale")
    waiver: bool = Field(default=False, description="CLI flag indicating Path B waiver")
    admin_signature: str | None = Field(
        default=None, description="Optional cryptographic signature or token of authorizing admin"
    )

    @model_validator(mode="after")
    def _normalize_request(self) -> PromoteCandidateRequest:
        if self.waiver and self.path != "path_b":
            object.__setattr__(self, "path", "path_b")
        if not self.waiver_rationale and self.rationale:
            object.__setattr__(self, "waiver_rationale", self.rationale)
        return self


CandidatePromotionRequest = PromoteCandidateRequest


class PromoteCandidateResponse(BaseContractModel):
    """Response emitted upon executing candidate promotion."""

    candidate_id: str = Field(..., description="Identifier of the evaluated candidate")
    promoted: bool = Field(default=True, description="Whether promotion succeeded")
    status: Literal["PROMOTED", "REJECTED"] = Field(
        default="PROMOTED", description="Promotion status code"
    )
    path: Literal["path_a", "path_b"] = Field(default="path_a", description="Path executed")
    path_used: Literal["path_a", "path_b"] | None = Field(
        default=None, description="Alias for path"
    )
    concept_uri: str = Field(..., description="Assigned canonical concept URN")
    target_hub: str = Field(default="", description="Assigned target physical hub")
    fibo_uri: str | None = Field(default=None, description="Grounded FIBO class URI")
    confidence_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Resulting match confidence"
    )
    new_confidence: float | None = Field(default=None, description="Alias for confidence_score")
    operations_count: int = Field(default=0, ge=0, description="Count of operations emitted")
    operations: list[dict[str, Any]] = Field(
        default_factory=list, description="Additive delta operations emitted"
    )
    applied_operation_id: str | None = Field(
        default=None, description="ID of generated additive delta operation"
    )
    waiver_id: str | None = Field(
        default=None, description="ID of generated StewardWaiverRecord if Path B"
    )
    proposal_id: str | None = Field(default=None, description="ID of staged or created proposal")

    @model_validator(mode="after")
    def _sync_response_aliases(self) -> PromoteCandidateResponse:
        if self.path_used is None:
            object.__setattr__(self, "path_used", self.path)
        if self.new_confidence is None:
            object.__setattr__(self, "new_confidence", self.confidence_score)
        if self.operations_count == 0 and self.operations:
            object.__setattr__(self, "operations_count", len(self.operations))
        return self

    @property
    def success(self) -> bool:
        return self.promoted

    @property
    def confidence(self) -> float:
        return self.confidence_score

    @property
    def applied_operations(self) -> list[dict[str, Any]]:
        return self.operations


PromotionResult = PromoteCandidateResponse

__all__ = [
    "CandidatePromotionRequest",
    "PromoteCandidateRequest",
    "PromoteCandidateResponse",
    "PromotionResult",
    "StagedCandidateDTO",
    "StagedCandidatesQueryResponse",
]
