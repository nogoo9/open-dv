"""Pipeline Extension Hooks (IP-1..6) for Ontology Recommendation (Decision D9, ONT-05).

Conforms to docs/18-implementation-plan-platform-parity-and-architecture.md (§3E.3).
Provides deterministic, governed injection seams connecting the Open-DV agentic pipeline
to external OntologyRecommender modules without modifying core DV2 logic.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opendv_contracts import (
    OntologyProposal,
    OntologyRecommendationRequest,
    OntologyRecommender,
    OntologyRecommenderUnavailableError,
    OpenDVError,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import canonicalize_graph_a_dict, compute_ontology_sha256
from opendv_service.ontology.policy import OntologyPolicy
from opendv_service.ontology.redaction import redact_sample_data
from opendv_service.ontology.registry import (
    OntologyRecommenderRegistry,
    get_ontology_recommender_registry,
)
from opendv_service.ontology.validator import validate_ontology_delta


def _ensure_graph_a_model(ontology: Any) -> GraphAOntology:
    """Coerce dictionary or existing GraphAOntology into a validated GraphAOntology instance."""
    if isinstance(ontology, GraphAOntology):
        return ontology
    if isinstance(ontology, dict):
        canon = canonicalize_graph_a_dict(ontology)
        return GraphAOntology.model_validate(canon)
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="general",
        enterprise_owner="Enterprise Data Steward",
        canonical_business_concepts=[],
    )


# ---------------------------------------------------------------------------
# Frozen Context DTOs for Pipeline Extension Points
# ---------------------------------------------------------------------------


class BootstrapContext(BaseModel):
    """Frozen context for IP-1 standalone ontology bootstrap/suggestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_namespace: str = Field(default="general")
    bronze_manifest: dict[str, Any] | list[Any] | None = None
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    samples: dict[str, Any] | None = None
    base_ontology: dict[str, Any] | None = None
    base_ontology_sha256: str | None = None
    context_documents: list[dict[str, Any]] = Field(default_factory=list)
    business_context: dict[str, Any] | str | None = None
    focus: dict[str, Any] | None = None


class WaveProfilingContext(BaseModel):
    """Frozen context for IP-2 advisory post-profiling hook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    wave_index: int
    wave_id: str = "w0"
    ontology: dict[str, Any] = Field(default_factory=dict)
    ontology_sha256: str | None = None
    domain_namespace: str = "general"
    bronze_manifest: dict[str, Any] | list[Any] | None = None
    profiles: list[dict[str, Any]] = Field(default_factory=list)
    samples: dict[str, Any] | None = None
    coverage_artifact: dict[str, Any] | None = None
    context_documents: list[dict[str, Any]] = Field(default_factory=list)
    business_context: dict[str, Any] | str | None = None
    deployed_contract_summary: dict[str, Any] | None = None


class GateViolationContext(BaseModel):
    """Frozen context for IP-3 Gate 1 ontology violation hold hook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    wave_index: int
    wave_id: str = "w0"
    ontology: dict[str, Any] = Field(default_factory=dict)
    ontology_sha256: str | None = None
    domain_namespace: str = "general"
    coverage_artifact: dict[str, Any] | None = None
    g1_violations: list[dict[str, Any]] = Field(default_factory=list)
    deployed_contract_summary: dict[str, Any] | None = None
    samples: dict[str, Any] | None = None
    context_documents: list[dict[str, Any]] = Field(default_factory=list)
    business_context: dict[str, Any] | str | None = None


class UnresolvedMentionsContext(BaseModel):
    """Frozen context for IP-4 advisory unstructured unresolved mentions hook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    wave_index: int
    wave_id: str = "w0"
    ontology: dict[str, Any] = Field(default_factory=dict)
    ontology_sha256: str | None = None
    domain_namespace: str = "general"
    unresolved_mentions: list[dict[str, Any]] = Field(default_factory=list)
    context_documents: list[dict[str, Any]] = Field(default_factory=list)
    business_context: dict[str, Any] | str | None = None


# ---------------------------------------------------------------------------
# Pipeline Extension Hooks Implementation
# ---------------------------------------------------------------------------


class PipelineExtensionHooks:
    """Orchestrates IP-1..6 extension injection points."""

    def __init__(
        self,
        policy: OntologyPolicy | str | None = None,
        registry: OntologyRecommenderRegistry | None = None,
    ) -> None:
        self.policy: OntologyPolicy = (
            OntologyPolicy.from_value(policy) if not isinstance(policy, OntologyPolicy) else policy
        )
        self.registry: OntologyRecommenderRegistry = (
            registry if registry is not None else get_ontology_recommender_registry()
        )
        self.recommender_errors: list[str] = []
        self.proposals: list[OntologyProposal] = []

    def get_recommender(self) -> OntologyRecommender:
        """Resolve the configured or default OntologyRecommender module."""
        return self.registry.get_recommender(self.policy.recommender_id)

    # -----------------------------------------------------------------------
    # IP-1: Direct Suggestion / Bootstrap
    # -----------------------------------------------------------------------
    async def suggest_ontology_deltas(
        self,
        ctx: BootstrapContext,
    ) -> OntologyProposal:
        """IP-1: Direct call to suggest ontology deltas or bootstrap a new domain ontology."""
        self.policy.validate_availability(self.registry)
        recommender = self.get_recommender()

        # RBAC sample, profile, and context document redaction at policy clearance
        redacted_samples = redact_sample_data(
            ctx.samples,
            clearance_level=self.policy.clearance_level,
        )
        redacted_docs = (
            redact_sample_data(ctx.context_documents, clearance_level=self.policy.clearance_level)
            if ctx.context_documents
            else []
        )
        redacted_profiles = (
            redact_sample_data(ctx.profiles, clearance_level=self.policy.clearance_level)
            if ctx.profiles
            else []
        )

        base_sha = ctx.base_ontology_sha256
        if base_sha is None and ctx.base_ontology:
            base_sha = compute_ontology_sha256(ctx.base_ontology)

        mode = "BOOTSTRAP" if not ctx.base_ontology else "DELTA"

        req = OntologyRecommendationRequest(
            request_id=f"req-ip1-{uuid.uuid4().hex[:12]}",
            mode=mode,
            domain_namespace=ctx.domain_namespace,
            base_ontology=ctx.base_ontology,
            base_ontology_sha256=base_sha,
            bronze_manifest=ctx.bronze_manifest,
            profiles=redacted_profiles,
            samples=redacted_samples,
            context_documents=redacted_docs,
            business_context=ctx.business_context,
            focus=ctx.focus,
            injection_point="IP-1",
            clearance_level=self.policy.clearance_level,
        )

        proposal = await recommender.recommend(req)

        # Deterministic validation if base ontology exists
        if ctx.base_ontology:
            base_model = _ensure_graph_a_model(ctx.base_ontology)
            val_res = validate_ontology_delta(base_model, proposal.delta)
            if not val_res.valid:
                proposal.diagnostics.extend(val_res.violations)

        self.proposals.append(proposal)
        return proposal

    # -----------------------------------------------------------------------
    # IP-2: Advisory Post-Profiling Hook
    # -----------------------------------------------------------------------
    async def on_after_profiling(
        self,
        ctx: WaveProfilingContext,
    ) -> OntologyProposal | None:
        """IP-2: Advisory hook fired after STAGE_01 profiling when coverage has gaps."""
        if not self.policy.should_invoke_ip2():
            return None

        # Advisory hooks fail safely without halting execution unless availability check fails
        try:
            self.policy.validate_availability(self.registry)
            recommender = self.get_recommender()
        except OntologyRecommenderUnavailableError:
            raise
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-2 recommender initialization failed: {exc}")
            return None

        redacted_samples = redact_sample_data(
            ctx.samples,
            clearance_level=self.policy.clearance_level,
        )
        redacted_docs = (
            redact_sample_data(ctx.context_documents, clearance_level=self.policy.clearance_level)
            if ctx.context_documents
            else []
        )
        redacted_profiles = (
            redact_sample_data(ctx.profiles, clearance_level=self.policy.clearance_level)
            if ctx.profiles
            else []
        )

        base_sha = ctx.ontology_sha256 or compute_ontology_sha256(ctx.ontology)

        req = OntologyRecommendationRequest(
            request_id=f"req-ip2-{ctx.wave_id}-{uuid.uuid4().hex[:8]}",
            mode="DELTA",
            domain_namespace=ctx.domain_namespace,
            base_ontology=ctx.ontology,
            base_ontology_sha256=base_sha,
            run_id=ctx.run_id,
            wave_id=ctx.wave_id,
            bronze_manifest=ctx.bronze_manifest,
            profiles=redacted_profiles,
            samples=redacted_samples,
            coverage_artifact=ctx.coverage_artifact,
            context_documents=redacted_docs,
            business_context=ctx.business_context,
            deployed_contract_summary=ctx.deployed_contract_summary,
            injection_point="IP-2",
            clearance_level=self.policy.clearance_level,
        )

        try:
            proposal = await recommender.recommend(req)
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-2 recommendation error: {exc}")
            return None

        base_model = _ensure_graph_a_model(ctx.ontology)
        val_res = validate_ontology_delta(
            base_model,
            proposal.delta,
            deployed_contract=ctx.deployed_contract_summary,
        )
        if not val_res.valid:
            proposal.diagnostics.extend(val_res.violations)

        self.proposals.append(proposal)
        return proposal

    # -----------------------------------------------------------------------
    # IP-3: Blocking Hold on Gate 1 DV-G4-001
    # -----------------------------------------------------------------------
    async def on_ontology_violation(
        self,
        ctx: GateViolationContext,
    ) -> OntologyProposal | None:
        """IP-3: Blocking hook fired on Gate 1 unmapped concept violation (DV-G4-001)."""
        if not self.policy.should_invoke_ip3():
            return None

        self.policy.validate_availability(self.registry)
        try:
            recommender = self.get_recommender()
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-3 recommender lookup failed: {exc}")
            return None

        redacted_samples = redact_sample_data(
            ctx.samples,
            clearance_level=self.policy.clearance_level,
        )
        redacted_docs = (
            redact_sample_data(ctx.context_documents, clearance_level=self.policy.clearance_level)
            if ctx.context_documents
            else []
        )

        base_sha = ctx.ontology_sha256 or compute_ontology_sha256(ctx.ontology)

        req = OntologyRecommendationRequest(
            request_id=f"req-ip3-{ctx.wave_id}-{uuid.uuid4().hex[:8]}",
            mode="HOLD_RESOLUTION",
            domain_namespace=ctx.domain_namespace,
            base_ontology=ctx.ontology,
            base_ontology_sha256=base_sha,
            run_id=ctx.run_id,
            wave_id=ctx.wave_id,
            coverage_artifact=ctx.coverage_artifact,
            samples=redacted_samples,
            context_documents=redacted_docs,
            business_context=ctx.business_context,
            deployed_contract_summary=ctx.deployed_contract_summary,
            focus={"violations": ctx.g1_violations},
            injection_point="IP-3",
            clearance_level=self.policy.clearance_level,
        )

        try:
            proposal = await recommender.recommend(req)
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-3 recommendation execution failed: {exc}")
            return None

        base_model = _ensure_graph_a_model(ctx.ontology)
        val_res = validate_ontology_delta(
            base_model,
            proposal.delta,
            deployed_contract=ctx.deployed_contract_summary,
        )
        if not val_res.valid:
            proposal.diagnostics.extend(val_res.violations)

        self.proposals.append(proposal)
        return proposal

    # -----------------------------------------------------------------------
    # IP-4: Advisory Unresolved Mentions Hook
    # -----------------------------------------------------------------------
    async def on_unresolved_mentions(
        self,
        ctx: UnresolvedMentionsContext,
    ) -> OntologyProposal | None:
        """IP-4: Advisory hook fired when Tier-U3 mentions have no candidate hub/concept."""
        if not self.policy.should_invoke_ip4():
            return None

        try:
            self.policy.validate_availability(self.registry)
            recommender = self.get_recommender()
        except OntologyRecommenderUnavailableError:
            raise
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-4 recommender lookup failed: {exc}")
            return None

        base_sha = ctx.ontology_sha256 or compute_ontology_sha256(ctx.ontology)
        redacted_docs = (
            redact_sample_data(ctx.context_documents, clearance_level=self.policy.clearance_level)
            if ctx.context_documents
            else []
        )

        req = OntologyRecommendationRequest(
            request_id=f"req-ip4-{ctx.wave_id}-{uuid.uuid4().hex[:8]}",
            mode="DELTA",
            domain_namespace=ctx.domain_namespace,
            base_ontology=ctx.ontology,
            base_ontology_sha256=base_sha,
            run_id=ctx.run_id,
            wave_id=ctx.wave_id,
            context_documents=redacted_docs,
            business_context=ctx.business_context,
            focus={"unresolved_mentions": ctx.unresolved_mentions},
            injection_point="IP-4",
            clearance_level=self.policy.clearance_level,
        )

        try:
            proposal = await recommender.recommend(req)
        except (
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            IndexError,
            OSError,
            TimeoutError,
            ImportError,
            OpenDVError,
        ) as exc:
            self.recommender_errors.append(f"IP-4 recommendation execution failed: {exc}")
            return None

        base_model = _ensure_graph_a_model(ctx.ontology)
        val_res = validate_ontology_delta(base_model, proposal.delta)
        if not val_res.valid:
            proposal.diagnostics.extend(val_res.violations)

        self.proposals.append(proposal)
        return proposal

    # -----------------------------------------------------------------------
    # IP-5: Context Inputs Extraction Hook
    # -----------------------------------------------------------------------
    def inject_business_context(
        self,
        documents: list[Any] | None = None,
        business_context: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | str | None]:
        """IP-5: Extract and ground enterprise definitions and context documents."""
        norm_docs: list[dict[str, Any]] = []
        if documents:
            for doc in documents:
                if isinstance(doc, dict):
                    norm_docs.append(dict(doc))
                elif hasattr(doc, "model_dump"):
                    norm_docs.append(doc.model_dump(mode="json"))

        norm_ctx: dict[str, Any] | str | None = None
        if isinstance(business_context, (dict, str)):
            norm_ctx = copy.deepcopy(business_context)
        elif hasattr(business_context, "model_dump"):
            norm_ctx = business_context.model_dump(mode="json")

        return norm_docs, norm_ctx

    # -----------------------------------------------------------------------
    # IP-6: Explorer Review Surfaces Hook
    # -----------------------------------------------------------------------
    def format_review_artifacts(
        self,
        proposal: OntologyProposal,
        base_ontology: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """IP-6: Format proposal and delta artifacts for Explorer review and CLI presentation."""
        artifact: dict[str, Any] = {
            "artifact_kind": "ontology_proposal",
            "proposal_id": proposal.proposal_id,
            "recommender_id": proposal.recommender_id,
            "recommender_version": proposal.recommender_version,
            "delta_id": proposal.delta.delta_id,
            "confidence_score": proposal.confidence_score,
            "operations_count": len(proposal.delta.operations),
            "diagnostics": proposal.diagnostics,
            "delta": proposal.delta.model_dump(mode="json"),
        }
        return artifact


def get_pipeline_extension_hooks(
    policy: OntologyPolicy | str | None = None,
    registry: OntologyRecommenderRegistry | None = None,
) -> PipelineExtensionHooks:
    """Convenience factory to create a PipelineExtensionHooks instance."""
    return PipelineExtensionHooks(policy=policy, registry=registry)


__all__ = [
    "BootstrapContext",
    "GateViolationContext",
    "PipelineExtensionHooks",
    "UnresolvedMentionsContext",
    "WaveProfilingContext",
    "get_pipeline_extension_hooks",
]
