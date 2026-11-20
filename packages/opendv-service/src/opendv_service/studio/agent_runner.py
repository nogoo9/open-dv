"""StudioAgentRunner: Fail-closed real VaultAgentHarness 3-persona runtime (D21, D28)."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness.hold_manager import StewardHoldManager
from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessConfig,
    HarnessRunResult,
    HarnessStopReason,
    HarnessStreamEvent,
    ModelTier,
    PipelineStageId,
)
from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.privacy import Pseudonymizer
from opendv_agent_harness.protocol import VaultAgentHarness
from opendv_agent_harness.registry import create_agent_harness
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import OntologyProposal, StewardWaiverRecord
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.construction.deterministic_pipeline import DeterministicConstructionPipeline
from opendv_service.construction.models import ConstructOptions
from opendv_service.state.platform_config_probe import (
    find_any_ready_provider,
    is_provider_ready,
)
from opendv_service.studio.agent_runner_helpers import (
    _BUDGET_STOP_REASONS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    DETERMINISTIC_FALLBACK_SLA_SECONDS,
    MAX_PROMPT_SAMPLE_VALUES_PER_COL,
    _has_provider_credentials,
    _mask_pii_text,
    _run_coro_sync,
    build_safe_agent_prompt,
    resolve_budget_hold,
    serialize_and_mask_telemetry,
)

logger = logging.getLogger(__name__)


class StudioAgentRunner:
    """Production 3-persona runner invoking VaultAgentHarness (fail-closed in agent mode)."""

    def __init__(
        self,
        harness: VaultAgentHarness | None = None,
        *,
        harness_config: HarnessConfig | None = None,
        state_container: Any | None = None,
        harness_factory: Callable[[HarnessConfig], VaultAgentHarness] | None = None,
        pseudonymizer: Pseudonymizer | None = None,
        deterministic_fallback: DeterministicConstructionPipeline | None = None,
        timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
        waiver_secret: bytes | str | None = None,
    ) -> None:
        self._harness = harness
        self._harness_config = harness_config
        self._state_container = state_container
        self._harness_factory = harness_factory
        self._pseudonymizer = pseudonymizer
        self._fallback_pipeline = deterministic_fallback or DeterministicConstructionPipeline()
        self._timeout_seconds = timeout_seconds
        self._waiver_secret = waiver_secret

    def _resolve_runtime_harness(self) -> VaultAgentHarness:
        """Resolve active VaultAgentHarness per run or fail closed (422 AGENT_NOT_CONFIGURED)."""
        if self._harness is not None:
            return self._harness

        factory = self._harness_factory or create_agent_harness

        if self._state_container is not None:
            setup_dict = self._state_container.get_active_harness_setup_dict()
            setup_obj = HarnessSetupConfig.model_validate(setup_dict)
            if not _has_provider_credentials(setup_obj, require_sdk=self._harness_factory is None):
                raise OpenDVError(
                    f"Configured provider '{setup_obj.provider}' is not ready "
                    "(missing CLI binary, SDK, or credentials).",
                    error_code=ErrorCode.AGENT_NOT_CONFIGURED,
                )
            return factory(setup_obj.to_harness_config())

        if self._harness_config is not None:
            tmp_setup = HarnessSetupConfig(
                provider=self._harness_config.provider,
                env=dict(self._harness_config.env),
                budget=self._harness_config.budget,
            )
            ready, _ = is_provider_ready(tmp_setup)
            if not ready and self._harness_factory is None:
                raise OpenDVError(
                    f"Harness provider '{self._harness_config.provider}' is not ready.",
                    error_code=ErrorCode.AGENT_NOT_CONFIGURED,
                )
            return factory(self._harness_config)

        env_provider = os.environ.get("OPENDV_AGENT_PROVIDER", "").strip()
        if env_provider:
            cand = HarnessSetupConfig(provider=env_provider)  # type: ignore[arg-type]
            ready, _ = is_provider_ready(cand)
            if ready:
                return factory(cand.to_harness_config())

        ready_dto = find_any_ready_provider()
        if ready_dto is not None:
            cand = HarnessSetupConfig(provider=ready_dto.provider)  # type: ignore[arg-type]
            return factory(cand.to_harness_config())

        raise OpenDVError(
            "No LLM agent provider is configured or ready. Configure a provider in Settings.",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
        )

    def _resolve_harness(self) -> VaultAgentHarness:
        """Alias for `_resolve_runtime_harness()` used by Studio workflow preflight."""
        return self._resolve_runtime_harness()

    @staticmethod
    def _build_safe_prompt(
        *,
        base_ontology: GraphAOntology | None,
        evidence_chunks: Sequence[EvidenceChunk],
        tables: Sequence[TableStructure],
        clarification_answers: dict[str, Any],
        fibo_modules: Sequence[str],
        steering_prompt: str | None,
    ) -> str:
        """Build 3-persona prompt excluding do_not_send_to_agent & capping PII-masked samples."""
        return build_safe_agent_prompt(
            base_ontology=base_ontology,
            evidence_chunks=evidence_chunks,
            tables=tables,
            clarification_answers=clarification_answers,
            fibo_modules=fibo_modules,
            steering_prompt=steering_prompt,
        )

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
        on_event: Callable[[HarnessStreamEvent], None] | None = None,
        writer: Any | None = None,
        **kwargs: Any,
    ) -> OntologyProposal:
        """Synchronous entry point called by job_runners.py worker thread."""
        clean_mode = (mode or "agent").strip().lower()
        if clean_mode == "deterministic":
            return self._run_deterministic_fallback(
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
                mode=clean_mode,
                writer=writer,
            )

        harness = self._resolve_runtime_harness()
        return self._run_real_harness_sync(
            harness=harness,
            base_ontology=base_ontology,
            evidence_chunks=evidence_chunks or [],
            tables=tables or [],
            clarification_answers=clarification_answers or {},
            session_dir=session_dir,
            fibo_modules=list(fibo_modules or []),
            steering_prompt=steering_prompt,
            job_id=job_id,
            principal=principal,
            mode=clean_mode,
            on_event=on_event,
            writer=writer,
        )

    def _open_budget_hold(
        self,
        *,
        reason_marker: str,
        session_dir: Path | str | None,
        job_id: str,
        cost_usd: float,
        total_tokens: int,
        writer: Any | None,
    ) -> None:
        """Record a StewardHoldRecord and emit a fail-closed budget_hold SSE journal event."""
        if session_dir is not None:
            sdir = Path(session_dir)
            hold_mgr = StewardHoldManager(state_dir=sdir / "holds")
            hold_mgr.record_hold(
                run_id=job_id,
                task_id=f"task-{job_id}",
                session_id=sdir.name,
                stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                persona=AgentPersona.ONTOLOGY_ARCHITECT,
                headline=(
                    f"Agent execution paused ({reason_marker}): "
                    f"tokens={total_tokens}, cost=${cost_usd:.4f}"
                ),
                escalation_reason=reason_marker,
                rule_id="BUDGET-01",
                options=[
                    {"option_id": "raise_budget_and_resume", "label": "Raise budget & resume"},
                    {"option_id": "abort", "label": "Abort run"},
                ],
            )
        if writer is not None:
            writer.buffer_event(
                "budget_hold",
                {
                    "job_id": job_id,
                    "reason": reason_marker,
                    "escalation_reason": reason_marker,
                    "total_tokens": total_tokens,
                    "cost_usd": cost_usd,
                },
            )
            writer.flush()

    def _run_real_harness_sync(
        self,
        *,
        harness: VaultAgentHarness,
        base_ontology: GraphAOntology | None,
        evidence_chunks: list[EvidenceChunk],
        tables: list[TableStructure],
        clarification_answers: dict[str, Any],
        session_dir: Path | str | None,
        fibo_modules: list[str],
        steering_prompt: str | None,
        job_id: str | None,
        principal: str | None,
        mode: str,
        on_event: Callable[[HarnessStreamEvent], None] | None,
        writer: Any | None,
    ) -> OntologyProposal:
        """Execute VaultAgentHarness.run_task with budget and telemetry enforcement."""
        eff_job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
        eff_principal = principal or "system:unknown"
        prompt_text = self._build_safe_prompt(
            base_ontology=base_ontology,
            evidence_chunks=evidence_chunks,
            tables=tables,
            clarification_answers=clarification_answers,
            fibo_modules=fibo_modules,
            steering_prompt=steering_prompt,
        )
        spec = AgentTaskSpec(
            task_id=f"task-{eff_job_id}",
            run_id=eff_job_id,
            trace_id=uuid.uuid4().hex,
            persona=AgentPersona.ONTOLOGY_ARCHITECT,
            stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
            prompt=prompt_text,
            system_instructions=(
                "Synthesize a valid OntologyProposal via @term-scout -> @domain-clarifier -> "
                "@ontology-architect using strictly grounded evidence."
            ),
            model_tier=ModelTier.HIGH_REASONING,
            output_schema=OntologyProposal,
        )

        cum_tokens = 0
        cum_cost = 0.0

        def _handle_stream_event(evt: HarnessStreamEvent) -> None:
            nonlocal cum_tokens, cum_cost
            if on_event is not None:
                on_event(evt)
            obs = evt.observability_event or {}
            usage = obs.get("usage") if isinstance(obs, dict) else None
            if isinstance(usage, dict):
                cum_tokens = max(cum_tokens, int(usage.get("total_tokens", 0) or 0))
                cum_cost = max(cum_cost, float(usage.get("cost_usd", 0.0) or 0.0))
            if writer is not None:
                writer.buffer_event(
                    "agent_stream",
                    {
                        "job_id": eff_job_id,
                        "stage_id": evt.stage_id.value,
                        "persona": evt.persona.value,
                        "kind": evt.kind.value,
                        "delta_text": _mask_pii_text(evt.delta_text or ""),
                        "token_usage": usage or {"total_tokens": cum_tokens},
                        "cost_usd": cum_cost,
                        "cumulative_cost_usd": cum_cost,
                        "mode": mode,
                    },
                )

        budget_cfg = getattr(harness.config, "budget", None)
        timeout_sec = float(getattr(budget_cfg, "timeout_seconds", self._timeout_seconds))
        start_mono = time.monotonic()

        try:
            run_result: HarnessRunResult[Any] = _run_coro_sync(
                harness.run_task(spec, on_event=_handle_stream_event),
                timeout=timeout_sec,
            )
        except TimeoutError as exc:
            self._open_budget_hold(
                reason_marker="TIMEOUT",
                session_dir=session_dir,
                job_id=eff_job_id,
                cost_usd=cum_cost,
                total_tokens=cum_tokens,
                writer=writer,
            )
            raise OpenDVError(
                f"Agent execution TIMEOUT after {timeout_sec}s; Steward Hold opened.",
                error_code=ErrorCode.AGENT_UNAVAILABLE,
            ) from exc

        usage_obj = run_result.usage
        total_tok = max(cum_tokens, int(getattr(usage_obj, "total_tokens", 0) or 0))
        total_cost = max(cum_cost, float(getattr(usage_obj, "cost_usd", 0.0) or 0.0))
        raw_resp = (
            run_result.structured_output
            if run_result.structured_output is not None
            else run_result.text_output
        )

        if writer is not None:
            writer.buffer_event(
                "agent_telemetry",
                {
                    "job_id": eff_job_id,
                    "wizard_step": 4,
                    "stage_id": PipelineStageId.STAGE_04_SHIP_VAULT_PASS2.value,
                    "persona": AgentPersona.ONTOLOGY_ARCHITECT.value,
                    "category": "agent",
                    "provider": harness.provider_name,
                    "model_id": run_result.model_used,
                    "token_usage": usage_obj.model_dump(mode="json"),
                    "cost_usd": total_cost,
                    "cumulative_cost_usd": total_cost,
                    "mode": mode,
                    "masked_prompt": _mask_pii_text(prompt_text),
                    "structured_or_raw_response": serialize_and_mask_telemetry(raw_resp) or "",
                    "status": (
                        "PASS"
                        if run_result.stop_reason == HarnessStopReason.COMPLETED
                        else run_result.stop_reason.value
                    ),
                },
            )

        if run_result.stop_reason in _BUDGET_STOP_REASONS:
            elapsed_sec = time.monotonic() - start_mono
            marker = (
                "TIMEOUT"
                if run_result.stop_reason == HarnessStopReason.MAX_TURNS_EXCEEDED
                and elapsed_sec >= timeout_sec * 0.9
                else run_result.stop_reason.value.upper()
            )
            self._open_budget_hold(
                reason_marker=marker,
                session_dir=session_dir,
                job_id=eff_job_id,
                cost_usd=total_cost,
                total_tokens=total_tok,
                writer=writer,
            )
            raise OpenDVError(
                f"Agent execution stopped on {marker}; Steward Hold opened.",
                error_code=ErrorCode.AGENT_UNAVAILABLE,
            )

        if run_result.stop_reason != HarnessStopReason.COMPLETED:
            if writer is not None:
                writer.flush()
            raise OpenDVError(
                f"Agent execution failed ({run_result.stop_reason.value}): "
                f"{run_result.text_output[:200]}",
                error_code=ErrorCode.AGENT_UNAVAILABLE,
            )

        raw_out = run_result.structured_output
        if isinstance(raw_out, OntologyProposal):
            proposal = raw_out
        elif isinstance(raw_out, dict):
            proposal = OntologyProposal.model_validate(raw_out)
        else:
            proposal = OntologyProposal.model_validate_json(run_result.text_output)

        if writer is not None:
            writer.flush()

        clean_proposer = f"{mode}:{eff_job_id}"
        diagnostics = list(proposal.diagnostics or [])
        diagnostics.extend(
            [
                f"proposer_principal:{clean_proposer}",
                f"triggered_by:{eff_principal}",
                "persona_chain:@term-scout->@domain-clarifier->@ontology-architect",
                f"harness_provider:{harness.provider_name}",
                f"mode:{mode}",
            ]
        )
        return proposal.model_copy(
            update={"recommender_id": clean_proposer, "diagnostics": diagnostics}
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
        writer: Any | None = None,
    ) -> OntologyProposal:
        """Explicit CLI-only deterministic construction tagged mode=deterministic."""
        fallback_start = time.monotonic()
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
            base_ontology if base_ontology is not None else create_genesis_ontology("core")
        )
        proposal = self._fallback_pipeline.construct_proposal(
            base_ontology=effective_base,
            evidence_chunks=evidence_chunks,
            tables=tables,
            clarification_answers=clarification_answers,
            options=opts,
        )
        if writer is not None:
            writer.buffer_event(
                "deterministic_construct",
                {"job_id": eff_job_id, "mode": "deterministic", "tag": "mode=deterministic"},
            )
            writer.flush()

        diagnostics = list(proposal.diagnostics or [])
        diagnostics.extend(
            [
                "engine_mode:deterministic",
                "mode=deterministic",
                f"proposer_principal:{proposer_principal}",
            ]
        )
        if time.monotonic() - fallback_start >= DETERMINISTIC_FALLBACK_SLA_SECONDS:
            logger.warning(
                "Deterministic construct exceeded SLA %.2fs", DETERMINISTIC_FALLBACK_SLA_SECONDS
            )
        return proposal.model_copy(
            update={"recommender_id": proposer_principal, "diagnostics": diagnostics}
        )


__all__ = [
    "DEFAULT_AGENT_TIMEOUT_SECONDS",
    "DETERMINISTIC_FALLBACK_SLA_SECONDS",
    "MAX_PROMPT_SAMPLE_VALUES_PER_COL",
    "StudioAgentRunner",
    "resolve_budget_hold",
]
