"""Data transfer objects for semantic concept matching (Task 5A.1 / Issue #211)."""

from __future__ import annotations

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ReferenceConcept(BaseContractModel):
    """Reference concept entry indexed in a semantic concept matcher."""

    concept_uri: str = Field(..., description="Unique reference concept URI")
    concept_name: str = Field(..., description="Human-readable concept name or label")
    module: str = Field(..., description="Ontology module or domain code (e.g. FND, BE)")
    definition: str = Field(default="", description="Formal text definition of concept")
    synonyms: list[str] = Field(
        default_factory=list, description="Alternative names or known synonyms"
    )
    parent_concept_uris: list[str] = Field(
        default_factory=list, description="Parent or super-concept URIs"
    )


class ScoredConceptMatch(BaseContractModel):
    """Scored match result emitted by a ConceptMatcher."""

    concept_uri: str = Field(..., description="Matched reference concept URI")
    concept_name: str = Field(..., description="Matched reference concept name")
    module: str = Field(..., description="Ontology module code")
    raw_score: float = Field(..., description="Unnormalized multi-field raw BM25 score")
    normalized_score: float = Field(
        ..., ge=0.0, le=1.0, description="Symmetrically normalized score quantized to 4 decimals"
    )
    matched_term: str = Field(..., description="Query term or phrase evaluated")
    definition: str = Field(default="", description="Definition of matched concept")
