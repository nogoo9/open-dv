"""OntologyRecommender SPI port protocol and recommendation request DTOs (Decision D9, ONT-05).

Conforms to docs/18-implementation-plan-platform-parity-and-architecture.md (§3E.4).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ontology import OntologyProposal


class RecommenderCapabilities(BaseContractModel):
    """Declared capabilities of an Ontology Recommender module."""

    supported_modes: list[str] = Field(
        default_factory=lambda: ["BOOTSTRAP", "DELTA", "HOLD_RESOLUTION"],
        description="Supported recommendation modes (BOOTSTRAP, DELTA, HOLD_RESOLUTION)",
    )
    supported_input_kinds: list[str] = Field(
        default_factory=lambda: [
            "profiles",
            "samples",
            "context_documents",
            "unmapped_gaps",
            "deployed_contract",
        ],
        description="Supported input data categories",
    )
    needs_llm: bool = Field(
        default=False,
        description="Whether this recommender requires an LLM provider connection",
    )
    max_input_sizes: dict[str, int] = Field(
        default_factory=dict,
        description="Optional maximum payload limits (e.g. max_samples, max_tokens)",
    )
    clearance_level: str = Field(
        default="INTERNAL",
        description="RBAC data classification clearance level accepted by this recommender",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplementary recommender metadata",
    )


class OntologyRecommendationRequest(BaseContractModel):
    """Frozen, read-only request context passed to an OntologyRecommender."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        use_enum_values=True,
    )

    request_id: str = Field(..., min_length=1, description="Unique recommendation request ID")
    mode: str = Field(
        default="DELTA",
        description="Recommendation mode: BOOTSTRAP, DELTA, or HOLD_RESOLUTION",
    )
    domain_namespace: str = Field(
        default="general",
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Business domain namespace",
    )
    base_ontology: dict[str, Any] | None = Field(
        default=None,
        description="Base Graph A ontology content as dictionary (null for BOOTSTRAP)",
    )
    base_ontology_sha256: str | None = Field(
        default=None,
        pattern=r"^([a-f0-9]{64})?$",
        description="SHA-256 digest of base Graph A ontology",
    )
    current_ontology: dict[str, Any] | None = Field(
        default=None,
        description="Alias for base_ontology for wave-context compatibility",
    )
    deployed_contract_summary: dict[str, Any] | None = Field(
        default=None,
        description="Summary of deployed contract (hub/link/satellite names + URIs)",
    )
    run_id: str | None = Field(
        default=None,
        description="Optional pipeline run identifier",
    )
    wave_id: str | None = Field(
        default=None,
        description="Optional wave index or identifier (e.g. 'w1')",
    )
    bronze_manifest: dict[str, Any] | list[Any] | None = Field(
        default=None,
        description="Raw bronze source manifest",
    )
    profiles: list[dict[str, Any]] | list[Any] = Field(
        default_factory=list,
        description="Statistical and structural profiles from STAGE_01",
    )
    samples: dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None = Field(
        default=None,
        description="Bounded, RBAC-redacted sample records from incoming sources",
    )
    coverage_artifact: dict[str, Any] | None = Field(
        default=None,
        description="Deterministic ontology coverage artifact payload",
    )
    context_documents: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Extracted business context documents (glossaries, data dictionaries, policies)"
        ),
    )
    business_context: dict[str, Any] | str | None = Field(
        default=None,
        description="Enterprise business context description, standards refs, or guidelines",
    )
    focus: dict[str, Any] | None = Field(
        default=None,
        description="Specific focal items (unmapped columns, unmapped tables, provisional URIs)",
    )
    injection_point: str = Field(
        default="IP-1",
        description="Pipeline injection point: IP-1, IP-2, IP-3, IP-4, IP-5, IP-6",
    )
    clearance_level: str = Field(
        default="INTERNAL",
        description="RBAC clearance level under which inputs were redacted",
    )
    budget: dict[str, Any] | None = Field(
        default=None,
        description="Resource/execution budget (timeout_seconds, max_tokens, cost_limit)",
    )
    provenance: dict[str, Any] | None = Field(
        default=None,
        description="Invocation provenance (requested_by, stage_id, timestamp)",
    )


@runtime_checkable
class OntologyRecommender(Protocol):
    """Port implemented by an independently installed recommendation module (Decision D9)."""

    recommender_id: str
    recommender_version: str
    capabilities: RecommenderCapabilities

    async def recommend(self, request: OntologyRecommendationRequest) -> OntologyProposal:
        """Generate an additive ontology proposal given a frozen recommendation request."""
        ...


__all__ = [
    "OntologyRecommendationRequest",
    "OntologyRecommender",
    "RecommenderCapabilities",
]
