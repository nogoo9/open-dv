"""Models for the construction subsystem (Task 5A.2 / Issue #212)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConstructOptions(BaseModel):
    """Configuration options governing ontology construction runs."""

    mode: Literal["auto", "agent", "deterministic"] = "deterministic"
    auto_commit: bool = False
    session_id: str | None = None
    session_dir: Path | str | None = None
    min_confidence: float = Field(default=0.6500, ge=0.6500, le=1.0)
    actor: str = "deterministic:pipeline"
    principal: str = "system:deterministic"
    if_match: str | None = None
    preferred_mappings: dict[str, str] = Field(default_factory=dict)


class StagedCandidate(BaseModel):
    """Candidate concept held back in the staging holding area."""

    candidate_id: str = Field(..., description="Unique staging identifier")
    concept_name: str = Field(..., description="Proposed business concept name")
    suggested_hub: str = Field(..., description="Suggested physical hub slug (e.g. hub_slug)")
    top_match_uri: str | None = Field(default=None, description="Top FIBO reference URI if any")
    top_match_score: float = Field(default=0.0, description="Highest BM25 match score")
    reason_held_back: str = Field(
        default="GROUNDING_THRESHOLD_UNMET",
        description="Reason candidate was withheld from OntologyDelta",
    )
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    dependent_links: list[dict[str, Any]] = Field(default_factory=list)


class StagedCandidatesFile(BaseModel):
    """Payload schema for evidence/staged_candidates.json."""

    session_id: str
    generated_at: str
    pipeline_mode: str = "deterministic"
    staged_candidates: list[StagedCandidate] = Field(default_factory=list)
