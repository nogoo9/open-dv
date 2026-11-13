"""Greenfield Genesis (0.0.0) ontology draft model, factory, and canonical SHA-256 constant.

Conforms to ADR-005, docs/19-ontology-studio-architecture.md §7.4, and
docs/20-ontology-studio-spec.md FR-5:
- Greenfield studio sessions initialize at version `0.0.0` with zero Canonical Business Concepts.
- `compute_ontology_sha256(create_genesis_ontology())` evaluates deterministically via RFC 8785
  JCS to `GENESIS_DRAFT_SHA256`.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
    ReferenceEnumeration,
    StrictGraphAModel,
    _validate_json_metadata,
)

GENESIS_VERSION: str = "0.0.0"
DEFAULT_DOMAIN_NAMESPACE: str = "general"
DEFAULT_ENTERPRISE_OWNER: str = "Enterprise Architecture"
GENESIS_DRAFT_SHA256: str = "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9"


class GenesisDraft(StrictGraphAModel):
    """Pre-CBC Greenfield Genesis Draft Ontology (`0.x.y`, major == 0)."""

    ontology_version: str = Field(default=GENESIS_VERSION, pattern=r"^0\.\d+\.\d+$")
    parent_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    decision_refs: list[str] = Field(default_factory=list)
    domain_namespace: str = Field(default=DEFAULT_DOMAIN_NAMESPACE, pattern=r"^[a-z][a-z0-9_]*$")
    enterprise_owner: str = Field(default=DEFAULT_ENTERPRISE_OWNER, min_length=2)
    standards_references: list[str] = Field(default_factory=list)
    canonical_business_concepts: list[CanonicalBusinessConcept] = Field(default_factory=list)
    canonical_relationships: list[CanonicalRelationship] = Field(default_factory=list)
    reference_enumerations: list[ReferenceEnumeration] = Field(default_factory=list)
    ontology_id: str | None = None
    version: str | None = None
    description: str | None = Field(default=None, description="Ontology description")
    metadata: dict[str, JsonValue] | None = Field(
        default=None, description="Ontology-level metadata"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_genesis_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if d.get("ontology_id") == "":
                d["ontology_id"] = None
            if d.get("description") == "":
                d["description"] = None
            if not d.get("ontology_version") and d.get("version"):
                d["ontology_version"] = str(d["version"])
            return d
        return data

    @model_validator(mode="after")
    def _validate_version_consistency(self) -> Self:
        if (
            self.version is not None
            and self.ontology_version
            and self.version != self.ontology_version
        ):
            msg = f"version '{self.version}' != ontology_version '{self.ontology_version}'"
            raise ValueError(f"VERSION_CONSISTENCY_MISMATCH: {msg}")
        return self

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return _validate_json_metadata(v)

    def to_graph_a(self) -> GraphAOntology:
        """Convert this `0.x.y` genesis draft into a canonical `GraphAOntology` instance."""
        return GraphAOntology.model_validate(self.model_dump(mode="python", exclude_none=True))


def create_genesis_ontology(
    domain_namespace: str = DEFAULT_DOMAIN_NAMESPACE,
    enterprise_owner: str = DEFAULT_ENTERPRISE_OWNER,
    *,
    ontology_version: str = GENESIS_VERSION,
    ontology_id: str | None = None,
    description: str | None = None,
    metadata: dict[str, JsonValue] | None = None,
) -> GraphAOntology:
    """Create a greenfield `0.x.y` Genesis `GraphAOntology` with zero CBCs."""
    draft = GenesisDraft(
        ontology_version=ontology_version,
        domain_namespace=domain_namespace,
        enterprise_owner=enterprise_owner,
        ontology_id=ontology_id or None,
        description=description or None,
        metadata=metadata,
    )
    return draft.to_graph_a()


__all__ = [
    "DEFAULT_DOMAIN_NAMESPACE",
    "DEFAULT_ENTERPRISE_OWNER",
    "GENESIS_DRAFT_SHA256",
    "GENESIS_VERSION",
    "GenesisDraft",
    "create_genesis_ontology",
]
