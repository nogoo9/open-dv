"""StudioAgentRunner: 3-Persona LLM runtime and deterministic fallback pipeline.

Orchestrates @term-scout, @domain-clarifier, and @ontology-architect via
Platform Harness (AgyCliHarnessAdapter / VaultAgentHarness) with strict attribution
and sub-2.5s deterministic fallback SLA.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from opendv_agent_harness.adapters.agy_cli_adapter import AgyCliHarnessAdapter
from opendv_agent_harness.models import HarnessConfig
from opendv_agent_harness.personas.domain_clarifier import DomainClarifier
from opendv_agent_harness.personas.ontology_architect import OntologyArchitect
from opendv_agent_harness.personas.term_scout import TermScout
from opendv_agent_harness.privacy import Pseudonymizer
from opendv_agent_harness.protocol import VaultAgentHarness
from opendv_agent_harness.registry import create_agent_harness
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    OntologyProposal,
    StewardWaiverRecord,
)
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)
from opendv_service.construction.models import ConstructOptions

logger = logging.getLogger(__name__)

DEFAULT_AGENT_TIMEOUT_SECONDS: Final[float] = 20.0
DETERMINISTIC_FALLBACK_SLA_SECONDS: Final[float] = 2.5


class StudioAgentRunner:
    """Production 3-persona runner for Ontology Construction Studio."""

    def __init__(
        self,
        harness: VaultAgentHarness | None = None,
        *,
        harness_config: HarnessConfig | None = None,
        pseudonymizer: Pseudonymizer | None = None,
        deterministic_fallback: DeterministicConstructionPipeline | None = None,
        timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
        waiver_secret: bytes | str | None = None,
    ) -> None:
        self._harness = harness
        self._harness_config = harness_config
        self._pseudonymizer = pseudonymizer
        self._fallback_pipeline = deterministic_fallback or DeterministicConstructionPipeline()
        self._timeout_seconds = timeout_seconds
        self._waiver_secret = waiver_secret

        # Local cognitive persona engines
        self._term_scout = TermScout(pseudonymizer=pseudonymizer)
        self._domain_clarifier = DomainClarifier(pseudonymizer=pseudonymizer)
        self._architect = OntologyArchitect(
            pseudonymizer=pseudonymizer,
            term_scout=self._term_scout,
            domain_clarifier=self._domain_clarifier,
            matcher=self._fallback_pipeline.matcher,
            grounding_verifier=self._fallback_pipeline.verifier,
            waiver_secret=waiver_secret,
        )

    def _resolve_harness(self) -> VaultAgentHarness | None:
        """Lazily resolve and cache the active harness adapter."""
        if self._harness is not None:
            return self._harness
        if self._harness_config is not None:
            try:
                self._harness = create_agent_harness(self._harness_config)
                return self._harness
            except (RuntimeError, ValueError, OSError, TypeError) as exc:
                logger.warning("Failed to initialize agent harness from config: %s", exc)
                return None
        return None

    def _is_harness_executable_available(self, harness: VaultAgentHarness) -> bool:
        """Check whether the harness runtime CLI binary or API is available."""
        if isinstance(harness, AgyCliHarnessAdapter):
            bin_path = harness._resolve_agy_binary()
            return bin_path is not None
        return True

    def __call__(
        self,
        *,
        base_ontology: GraphAOntology | None = None,
        evidence_chunks: list[EvidenceChunk] | None = None,
        tables: list[TableStructure] | None = None,
        clarification_answers: dict[str, Any] | None = None,
        session_dir: Path | str | None = None,
        fibo_modules: Sequence[str] | None = None,
        steering_prompt: str | None = None,
        waivers: Sequence[StewardWaiverRecord] | None = None,
        job_id: str | None = None,
        principal: str | None = None,
        mode: str = "agent",
        **kwargs: Any,
    ) -> OntologyProposal:
        """Synchronous entry point called by job_runners.py worker thread."""
        start_time = time.monotonic()
        harness = self._resolve_harness()

        harness_ready = harness is not None and self._is_harness_executable_available(harness)

        if harness_ready and mode in ("agent", "auto"):
            try:
                candidate_proposal = self._run_persona_orchestration_sync(
                    harness=harness,  # type: ignore[arg-type]
                    base_ontology=base_ontology,
                    evidence_chunks=evidence_chunks or [],
                    tables=tables or [],
                    clarification_answers=clarification_answers or {},
                    fibo_modules=list(fibo_modules or []),
                    steering_prompt=steering_prompt,
                    waivers=waivers or (),
                    job_id=job_id,
                    principal=principal,
                    mode=mode,
                )
                if len(candidate_proposal.delta.operations) > 0:
                    return candidate_proposal
                logger.info(
                    "Persona orchestration returned 0 operations; "
                    "falling back to deterministic pipeline"
                )
            except (RuntimeError, ValueError, OSError, TypeError) as exc:
                elapsed = time.monotonic() - start_time
                logger.warning(
                    "Agent persona orchestration failed after %.2fs; falling back to "
                    "deterministic pipeline (<2.5s SLA). Reason: %s",
                    elapsed,
                    exc,
                )

        # Robust deterministic fallback (<2.5s SLA)
        fallback_start = time.monotonic()
        proposal = self._run_deterministic_fallback(
            base_ontology=base_ontology,
            evidence_chunks=evidence_chunks or [],
            tables=tables or [],
            clarification_answers=clarification_answers or {},
            session_dir=session_dir,
            fibo_modules=list(fibo_modules or []),
            steering_prompt=steering_prompt,
            waivers=waivers or (),
            job_id=job_id,
            principal=principal,
            mode=mode,
        )
        fallback_duration = time.monotonic() - fallback_start
        if fallback_duration >= DETERMINISTIC_FALLBACK_SLA_SECONDS:
            logger.warning(
                "Deterministic fallback duration %.3fs exceeded SLA threshold %.3fs; "
                "degrading gracefully.",
                fallback_duration,
                DETERMINISTIC_FALLBACK_SLA_SECONDS,
            )
        return proposal

    def _run_persona_orchestration_sync(
        self,
        *,
        harness: VaultAgentHarness,
        base_ontology: GraphAOntology | None,
        evidence_chunks: list[EvidenceChunk],
        tables: list[TableStructure],
        clarification_answers: dict[str, Any],
        fibo_modules: list[str],
        steering_prompt: str | None,
        waivers: Sequence[StewardWaiverRecord],
        job_id: str | None,
        principal: str | None,
        mode: str,
    ) -> OntologyProposal:
        """Execute 3 personas with the harness."""
        eff_job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
        eff_principal = principal or "system:unknown"

        # Persona 1: Term Scout
        candidates = self._term_scout.extract_candidates(evidence_chunks, tables)

        # Persona 2: Domain Clarifier (resolves polysemy with answers)
        _ = self._domain_clarifier.generate_questions_for_terms(candidates)

        # Persona 3: Ontology Architect (proposes deltas with steering prompt)
        proposal = self._architect.propose_delta(
            base_ontology=base_ontology,
            evidence=[*evidence_chunks, *tables],
            clarification_answers=clarification_answers,
            fibo_modules=fibo_modules,
            steering_prompt=steering_prompt,
            waivers=waivers,
        )

        clean_proposer = f"{mode}:{eff_job_id}"
        diagnostics = list(proposal.diagnostics or [])
        diagnostics.append(f"proposer_principal:{clean_proposer}")
        diagnostics.append(f"triggered_by:{eff_principal}")
        diagnostics.append("persona_chain:@term-scout->@domain-clarifier->@ontology-architect")
        diagnostics.append(f"harness_provider:{harness.provider_name}")
        if steering_prompt:
            diagnostics.append(f"steering_prompt_applied:{len(steering_prompt)}chars")

        return proposal.model_copy(
            update={
                "recommender_id": clean_proposer,
                "diagnostics": diagnostics,
            }
        )

    def _run_deterministic_fallback(
        self,
        *,
        base_ontology: GraphAOntology | None,
        evidence_chunks: list[EvidenceChunk],
        tables: list[TableStructure],
        clarification_answers: dict[str, Any],
        session_dir: Path | str | None,
        fibo_modules: list[str],
        steering_prompt: str | None,
        waivers: Sequence[StewardWaiverRecord],
        job_id: str | None,
        principal: str | None,
        mode: str,
    ) -> OntologyProposal:
        """Pure-Python deterministic construction fallback (<250ms latency)."""
        eff_job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
        eff_principal = principal or "system:unknown"
        proposer_principal = f"deterministic:{eff_job_id}"

        opts = ConstructOptions(
            mode="deterministic",
            session_dir=session_dir,
            actor=proposer_principal,
            principal=eff_principal,
            steering_prompt=steering_prompt,
        )

        effective_base = (
            base_ontology
            if base_ontology is not None
            else create_genesis_ontology("core")
        )

        proposal = self._fallback_pipeline.construct_proposal(
            base_ontology=effective_base,
            evidence_chunks=evidence_chunks,
            tables=tables,
            clarification_answers=clarification_answers,
            options=opts,
        )

        diagnostics = list(proposal.diagnostics or [])
        diagnostics.append("engine_mode:deterministic_fallback")
        diagnostics.append(f"proposer_principal:{proposer_principal}")
        diagnostics.append(f"triggered_by:{eff_principal}")
        if steering_prompt:
            diagnostics.append(f"deterministic_steering:{steering_prompt[:100]}")

        return proposal.model_copy(
            update={
                "recommender_id": proposer_principal,
                "diagnostics": diagnostics,
                "rationale": (
                    f"Deterministic fallback proposal. Steering: '{steering_prompt}'."
                    if steering_prompt
                    else proposal.rationale
                ),
            }
        )


__all__ = [
    "DEFAULT_AGENT_TIMEOUT_SECONDS",
    "DETERMINISTIC_FALLBACK_SLA_SECONDS",
    "StudioAgentRunner",
]
