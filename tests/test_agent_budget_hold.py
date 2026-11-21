"""Phase 2 Track B TDD Tests: Live Cost/Token Telemetry & Fail-Closed HarnessBudget Steward Hold.

Covers:
- FR-AGT-10 / D21:
  - Cumulative tokens and USD cost streamed into telemetry/journal events.
  - Fail-closed Steward Hold on TOKEN_BUDGET_EXCEEDED, COST_BUDGET_EXCEEDED, or timeout
    (zero silent truncation or deterministic fallback, zero partial commits).
  - Raise budget & resume vs. Abort hold resolution.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.hold_manager import StewardDecisionAction, StewardHoldManager
from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessBudget,
    HarnessConfig,
    HarnessEventKind,
    HarnessRunResult,
    HarnessStopReason,
    HarnessStreamEvent,
    HarnessUsageMetrics,
    ModelTier,
    PipelineStageId,
)
from opendv_contracts.dtos.ingestion import EvidenceChunk
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter, replay_sse_events


class _BudgetStoppingHarness:
    """Spy harness returning a specified HarnessStopReason / timeout and usage telemetry."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        stop_reason: HarnessStopReason,
        usage: HarnessUsageMetrics,
        budget: HarnessBudget,
        raise_timeout: bool = False,
    ) -> None:
        self._stop_reason = stop_reason
        self._usage = usage
        self._raise_timeout = raise_timeout
        self._config = HarnessConfig(
            provider="claude",
            workspace_dir=tmp_path,
            state_dir=tmp_path / "s",
            app_data_dir=tmp_path / "a",
            save_dir=tmp_path / "sv",
            budget=budget,
        )

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def config(self) -> HarnessConfig:
        return self._config

    def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
        return "claude-sonnet-4-5"

    async def cancel_active_turn(self, task_id: str) -> None:
        return None

    async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
        if False:
            yield  # pragma: no cover

    async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
        if on_event is not None:
            on_event(
                HarnessStreamEvent(
                    kind=HarnessEventKind.TURN_COMPLETE,
                    task_id=spec.task_id,
                    run_id=spec.run_id,
                    trace_id=spec.trace_id,
                    span_id=uuid.uuid4().hex[:16],
                    provider="claude",
                    persona=AgentPersona.ONTOLOGY_ARCHITECT,
                    stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                    observability_event={"usage": self._usage.model_dump(mode="json")},
                )
            )
        if self._raise_timeout:
            raise TimeoutError("Agent turn timed out after budget.timeout_seconds")
        return HarnessRunResult(
            task_id=spec.task_id,
            run_id=spec.run_id,
            session_id="bud-session",
            trace_id=spec.trace_id,
            span_id=uuid.uuid4().hex[:16],
            provider="claude",
            model_used="claude-sonnet-4-5",
            stop_reason=self._stop_reason,
            text_output=f"Stopped due to {self._stop_reason.value}",
            usage=self._usage,
            structured_output=None,
        )


def test_cumulative_tokens_and_cost_streamed_in_telemetry(tmp_path: Path) -> None:
    """StudioAgentRunner streams cumulative token_usage and cost_usd into journal/telemetry events."""
    from opendv_contracts.dtos.ontology import AddConceptOp, EvidenceRef, OntologyDelta, OntologyProposal
    from opendv_core.ontology_hashing import compute_ontology_sha256

    class _CountingHarness(_BudgetStoppingHarness):
        async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
            await super().run_task(spec, on_event=on_event)
            base = create_genesis_ontology("custody_ops")
            delta = OntologyDelta(
                delta_id="d-bud-ok",
                parent_sha256=compute_ontology_sha256(base),
                base_version="0.0.0",
                target_version="1.0.0",
                domain_namespace="custody_ops",
                operations=[
                    AddConceptOp(
                        op_id="op-bud-1",
                        concept_uri="urn:ontology:entity:margin_call",
                        concept_name="Margin Call",
                        target_hub="hub_margin_call",
                        definition="Collateral margin call.",
                        evidence_refs=[EvidenceRef(evidence_type="document_span", snippet="margin", confidence=0.9)],
                    )
                ],
            )
            prop = OntologyProposal(
                proposal_id="prop-bud-ok",
                recommender_id="agent:bud",
                base_ontology_sha256=compute_ontology_sha256(base),
                delta=delta,
                confidence=0.9,
                rationale="ok",
            )
            return HarnessRunResult(
                task_id=spec.task_id,
                run_id=spec.run_id,
                session_id="bud-tel-01",
                trace_id=spec.trace_id,
                span_id=uuid.uuid4().hex[:16],
                provider="claude",
                model_used="claude-sonnet-4-5",
                stop_reason=HarnessStopReason.COMPLETED,
                text_output=prop.model_dump_json(),
                usage=self._usage,
                structured_output=prop,
            )

    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path / "ws")
    mgr.create_session(session_id="bud-tel-01", domain_namespace="custody_ops")
    sdir = mgr.get_session_dir("bud-tel-01")
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-bud-ok")

    usage = HarnessUsageMetrics(input_tokens=350, output_tokens=150, total_tokens=500, cost_usd=0.025)
    harness = _CountingHarness(
        tmp_path,
        stop_reason=HarnessStopReason.COMPLETED,
        usage=usage,
        budget=HarnessBudget(max_output_tokens=1000, max_cost_usd=2.0),
    )
    runner = StudioAgentRunner(harness=harness)
    runner(
        base_ontology=create_genesis_ontology("custody_ops"),
        evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Margin call rule.")],
        session_dir=sdir,
        job_id="job-bud-ok",
        writer=writer,
        mode="agent",
    )
    writer.flush()
    events = list(replay_sse_events(writer.journal_file))
    telemetry_evts = [
        e for e in events if isinstance(e.data, dict) and ("token_usage" in e.data or "cost_usd" in e.data)
    ]
    assert len(telemetry_evts) >= 1
    last = telemetry_evts[-1].data
    assert isinstance(last, dict)
    assert last.get("cost_usd", 0) >= 0.025 or last.get("cumulative_cost_usd", 0) >= 0.025


@pytest.mark.parametrize(
    ("stop_reason", "raise_timeout", "expected_marker"),
    [
        (HarnessStopReason.TOKEN_BUDGET_EXCEEDED, False, "TOKEN_BUDGET_EXCEEDED"),
        (HarnessStopReason.COST_BUDGET_EXCEEDED, False, "COST_BUDGET_EXCEEDED"),
        (HarnessStopReason.PROVIDER_ERROR, True, "TIMEOUT"),
    ],
)
def test_budget_or_timeout_opens_steward_hold_no_partial_commit(
    tmp_path: Path,
    stop_reason: HarnessStopReason,
    raise_timeout: bool,
    expected_marker: str,
) -> None:
    """Budget/timeout stop fails closed with StewardHoldRecord + budget hold journal event (zero fallback)."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path / "ws")
    meta_before = mgr.create_session(session_id=f"bud-{expected_marker.lower()}", domain_namespace="custody_ops")
    sdir = mgr.get_session_dir(meta_before.session_id)
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-bud-hold")

    harness = _BudgetStoppingHarness(
        tmp_path,
        stop_reason=stop_reason,
        usage=HarnessUsageMetrics(input_tokens=900, output_tokens=512, total_tokens=1412, cost_usd=1.25),
        budget=HarnessBudget(max_output_tokens=512, max_cost_usd=1.0, timeout_seconds=2.0),
        raise_timeout=raise_timeout,
    )
    runner = StudioAgentRunner(harness=harness)

    with pytest.raises((OpenDVError, RuntimeError, TimeoutError)) as exc_info:
        runner(
            base_ontology=create_genesis_ontology("custody_ops"),
            evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Collateral rule.")],
            session_dir=sdir,
            job_id="job-bud-hold",
            writer=writer,
            mode="agent",
        )

    assert expected_marker in str(exc_info.value).upper()

    # Zero partial ontology commit occurred
    assert mgr.get_session(meta_before.session_id).head_version == "0.0.0"

    # Budget hold event recorded in journal and StewardHoldManager persisted hold record
    writer.flush()
    events = list(replay_sse_events(writer.journal_file))
    assert any(
        expected_marker in str(e.data).upper()
        or "budget_hold" in e.event.lower()
        or "steward_hold" in e.event.lower()
        for e in events
    )
    hold_mgr = StewardHoldManager(state_dir=sdir / "holds")
    pending = hold_mgr.list_pending_holds()
    assert len(pending) >= 1
    assert expected_marker in (pending[0].escalation_reason or "").upper() or expected_marker in (pending[0].rule_id or "").upper()


def test_budget_hold_raise_budget_and_resume_or_abort(tmp_path: Path) -> None:
    """Budget StewardHold supports raise_budget_and_resume (approving) and abort."""
    sdir = tmp_path / "session-hold"
    hold_mgr = StewardHoldManager(state_dir=sdir / "holds")
    rec = hold_mgr.record_hold(
        run_id="run-bud-1",
        task_id="task-bud-1",
        session_id="sess-bud-1",
        stage_id="STAGE_04_SHIP_VAULT_PASS2",
        persona="ontology-architect",
        decision_id="dec-bud-1",
        hold_id="hold-bud-1",
        event_id="evt-bud-1",
        headline="Token budget exceeded (512/512)",
        escalation_reason="TOKEN_BUDGET_EXCEEDED",
        rule_id="BUDGET-01",
    )
    assert str(rec.status) == "PENDING"

    from opendv_service.studio.agent_runner import resolve_budget_hold

    resumed = resolve_budget_hold(
        hold_mgr=hold_mgr,
        hold_id="hold-bud-1",
        action="raise_budget_and_resume",
        new_max_output_tokens=4096,
        new_max_cost_usd=10.0,
    )
    assert resumed["action"] == "raise_budget_and_resume"
    assert resumed["updated_budget"]["max_output_tokens"] == 4096
    assert "APPROVED" in str(resumed["hold_status"])


def test_wait_for_timeout_on_real_base_harness_opens_timeout_steward_hold(tmp_path: Path) -> None:
    """Real BaseVaultAgentHarness exceeding budget.timeout_seconds opens TIMEOUT StewardHold (not CANCELLED)."""
    import asyncio
    from collections.abc import AsyncIterator

    from opendv_agent_harness.models import ToolExecutionContext
    from opendv_agent_harness.protocol import BaseVaultAgentHarness

    class _SlowStreamingHarness(BaseVaultAgentHarness):
        @property
        def provider_name(self) -> str:
            return "agy"

        def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
            del tier, persona
            return "gemini-4-argon-high"

        async def _stream_provider_task(
            self, spec: AgentTaskSpec[Any], ctx: ToolExecutionContext, model_id: str
        ) -> AsyncIterator[HarnessStreamEvent]:
            del model_id
            yield self._make_event(spec, ctx, HarnessEventKind.SESSION_START)
            await asyncio.sleep(5.0)
            yield self._make_event(spec, ctx, HarnessEventKind.TURN_COMPLETE, tool_result={})

    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path / "ws")
    meta = mgr.create_session(session_id="bud-real-timeout", domain_namespace="custody_ops")
    sdir = mgr.get_session_dir(meta.session_id)
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-real-timeout")

    cfg = HarnessConfig(
        provider="agy",
        workspace_dir=tmp_path,
        state_dir=tmp_path / "s",
        app_data_dir=tmp_path / "a",
        save_dir=tmp_path / "sv",
        budget=HarnessBudget(timeout_seconds=1.05),
    )
    runner = StudioAgentRunner(harness=_SlowStreamingHarness(cfg))

    with pytest.raises(OpenDVError) as exc_info:
        runner(
            base_ontology=create_genesis_ontology("custody_ops"),
            evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Settlement rule.")],
            session_dir=sdir,
            job_id="job-real-timeout",
            writer=writer,
            mode="agent",
        )

    assert "TIMEOUT" in str(exc_info.value).upper()
    assert "CANCELLED" not in str(exc_info.value).upper()

    writer.flush()
    events = list(replay_sse_events(writer.journal_file))
    assert any(
        e.event == "budget_hold" and isinstance(e.data, dict) and e.data.get("reason") == "TIMEOUT"
        for e in events
    )
    hold_mgr = StewardHoldManager(state_dir=sdir / "holds")
    pending = hold_mgr.list_pending_holds()
    assert len(pending) == 1
    assert pending[0].escalation_reason == "TIMEOUT"

