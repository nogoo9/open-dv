"""Clarification question and answer data transfer objects for Ontology Studio.

Conforms to docs/19-ontology-studio-architecture.md (§5.4) and
docs/20-ontology-studio-spec.md.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ontology import EvidenceRef


class ClarificationOption(BaseContractModel):
    """Multiple-choice option presented to human steward for resolving ambiguity."""

    option_id: str = Field(..., description="Unique option identifier within question")
    label: str = Field(..., description="Human-readable concept or resolution label")
    fibo_uri: str | None = Field(
        default=None, description="Optional FIBO concept URI associated with option"
    )
    rationale: str = Field(
        ..., description="Semantic explanation of how this option models the concept"
    )


class ClarificationQuestion(BaseContractModel):
    """Structured question generated to resolve domain or mapping ambiguity."""

    question_id: str = Field(..., description="Unique question identifier")
    focus_concept: str = Field(
        ..., description="Candidate entity or concept requiring clarification"
    )
    question_text: str = Field(..., description="Natural language question for steward")
    evidence_citations: list[EvidenceRef] = Field(
        default_factory=list, description="Citations to evidence chunks prompting question"
    )
    options: list[ClarificationOption] = Field(
        default_factory=list, description="Candidate resolution choices"
    )
    allow_custom_text: bool = Field(
        default=True, description="Whether human steward may provide custom input"
    )
    matched_key: str | None = Field(default=None, description="Polysemic catalog key if matched")


class ClarificationAnswerItem(BaseContractModel):
    """Steward response to a single clarification question."""

    question_id: str = Field(..., description="Identifier of the answered question")
    selected_options: list[str] = Field(
        default_factory=list, description="List of selected option_ids"
    )
    user_text: str | None = Field(
        default=None, description="Optional free-form steward guidance or custom definition"
    )
    answered_by: str = Field(
        default="human:steward", description="Identity or role of the responding user"
    )
    answered_at: str | None = Field(default=None, description="ISO-8601 UTC timestamp of response")


class ClarificationAnswerRequest(BaseContractModel):
    """Payload for submitting clarification answers to a studio session."""

    answers: list[ClarificationAnswerItem] = Field(
        default_factory=list, description="Collection of answered clarification items"
    )
    expected_answers_sha256: str | None = Field(
        default=None,
        description="Optional OCC pre-condition against answers JCS digest (ONT-EVO-003)",
    )


class ClarificationAnswerResponse(BaseContractModel):
    """Response returned upon successfully persisting clarification answers."""

    saved_count: int = Field(..., description="Count of successfully saved answer items")
    answers_sha256: str = Field(..., description="Canonical JCS SHA-256 digest of stored answers")


class ClarifyRequest(BaseContractModel):
    """Request payload to trigger clarification question generation."""

    mode: Literal["auto", "agent", "deterministic"] = Field(
        default="auto", description="Generation mode ('auto', 'agent', 'deterministic')"
    )
    focus_concepts: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Optional list of concept URIs or names to focus clarification on",
    )
    focus_concept: str | None = Field(
        default=None,
        description="Optional single focus concept (normalized into focus_concepts)",
    )
    max_questions: int = Field(
        default=10, ge=1, le=100, description="Maximum number of questions to generate"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_focus_concepts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            fc_single = data.get("focus_concept")
            if isinstance(fc_single, str) and fc_single.strip():
                data = dict(data)
                single = fc_single.strip()
                existing_raw = data.get("focus_concepts")
                existing = list(existing_raw) if isinstance(existing_raw, (list, tuple)) else []
                if single not in existing:
                    existing.append(single)
                data["focus_concepts"] = existing
        return data


__all__ = [
    "ClarificationAnswerItem",
    "ClarificationAnswerRequest",
    "ClarificationAnswerResponse",
    "ClarificationOption",
    "ClarificationQuestion",
    "ClarifyRequest",
]
