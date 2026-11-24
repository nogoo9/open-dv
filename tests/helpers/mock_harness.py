"""DeterministicMockHarness, fault injection, and in-memory opendv.agents.ports doubles."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    ControlSignalReader,
    RunCheckpointStore,
    RunEventSink,
    ScoutToolDispatcher,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ControlSignalRecord, RunCheckpointRecord, RunStreamEvent
from opendv.models.project import HarnessProvider
from opendv.models.wire_dtos_discovery import ScoutDigest
from opendv.models.wire_dtos_vault import (
    AttemptSnapshotSpec,
    HorizonCriticVerdict,
    SemanticCriticVerdict,
)
from tests.helpers._testkit_v2 import (
    RecordsSourceAdapter,
    default_for_annotation,
    synthesize_minimal_schema_instance,
    validate_candidate_with_schema,
)


@dataclass
class MockFaultSpec:
    """Optional fault-injection configuration for DeterministicMockHarness."""

    raise_error_code: ErrorCode | None = None
    raise_message: str = "Injected deterministic harness fault"
    jargon_injection: bool = False
    budget_exhaustion: bool = False
    duplicate_ir_cycle: bool = False


@dataclass
class MockTurnScript:
    """Scripted outcome for a single DeterministicMockHarness turn."""

    structured_output: Any = None
    text_output: str = ""
    turns_used: int = 1
    input_tokens: int = 120
    output_tokens: int = 80
    token_count_source: Literal["reported", "estimated"] = "reported"
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    scout_calls: list[tuple[Literal["table_cluster", "page_range"], list[str]]] = field(
        default_factory=list
    )


ScriptSelector = Callable[[AgentTurnRequest], MockTurnScript | Any]


class InMemoryRunEventSink(RunEventSink):
    """In-memory RunEventSink collecting monotonic RunStreamEvent frames."""

    def __init__(self) -> None:
        self.events: list[RunStreamEvent] = []

    def emit(self, event: RunStreamEvent) -> None:
        self.events.append(event)


class InMemoryControlSignalReader(ControlSignalReader):
    """In-memory ControlSignalReader returning queued ControlSignalRecord signals."""

    def __init__(self, initial_signals: Sequence[ControlSignalRecord] = ()) -> None:
        self.signals: list[ControlSignalRecord] = list(initial_signals)

    def push(self, signal: ControlSignalRecord) -> None:
        self.signals.append(signal)

    def poll_signals(self, run_id: str, *, after_seq: int = 0) -> list[ControlSignalRecord]:
        matching = [s for s in self.signals if s.run_id == run_id]
        return matching[after_seq:]


class InMemoryRunCheckpointStore(RunCheckpointStore):
    """In-memory RunCheckpointStore tracking checkpoints, attempt snapshots, and leases."""

    def __init__(self) -> None:
        self.checkpoints: dict[str, RunCheckpointRecord] = {}
        self.snapshots: dict[str, list[AttemptSnapshotSpec]] = {}
        self.lease_held: dict[str, bool] = {}

    def acquire_lease(self, run_id: str) -> None:
        self.lease_held[run_id] = True

    def save_checkpoint(self, record: RunCheckpointRecord) -> None:
        self.checkpoints[record.run_id] = record

    def load_checkpoint(self, run_id: str) -> RunCheckpointRecord | None:
        return self.checkpoints.get(run_id)

    def save_attempt_snapshot(self, run_id: str, snapshot: AttemptSnapshotSpec) -> None:
        self.snapshots.setdefault(run_id, []).append(snapshot)

    def release_active_lease(self, run_id: str) -> None:
        self.lease_held[run_id] = False


class InMemoryScoutToolDispatcher(ScoutToolDispatcher):
    """In-memory Step 4 ScoutToolDispatcher with optional asyncio drain barrier."""

    def __init__(
        self,
        digests_by_target: dict[str, ScoutDigest] | None = None,
        *,
        started_event: asyncio.Event | None = None,
        release_event: asyncio.Event | None = None,
    ) -> None:
        self._digests = dict(digests_by_target or {})
        self.started_event = started_event
        self.release_event = release_event
        self.calls: list[tuple[str, list[str]]] = []
        self.active_count = 0
        self.completed_count = 0

    async def dispatch_scout(
        self,
        scout_kind: Literal["table_cluster", "page_range"],
        target_ids: Sequence[str],
        *,
        run_id: str,
    ) -> list[ScoutDigest]:
        _ = run_id
        self.active_count += 1
        try:
            self.calls.append((scout_kind, list(target_ids)))
            if self.started_event is not None:
                self.started_event.set()
            if self.release_event is not None:
                await self.release_event.wait()
            results: list[ScoutDigest] = []
            for tid in target_ids:
                if tid in self._digests:
                    results.append(self._digests[tid])
                else:
                    results.append(
                        ScoutDigest(
                            scout_kind=scout_kind,
                            target_id=tid,
                            candidate_concepts=[],
                            key_columns=[],
                        )
                    )
            return results
        finally:
            self.active_count -= 1
            self.completed_count += 1


class DeterministicMockHarness(AgentHarnessPort):
    """Test-only deterministic AgentHarnessPort for DI into CEGAR and OpenDVService."""

    def __init__(
        self,
        *,
        provider: HarnessProvider = "agy",
        scripts_by_agent: dict[str, list[MockTurnScript | Any]] | None = None,
        selector: ScriptSelector | None = None,
        fault: MockFaultSpec | None = None,
    ) -> None:
        self._provider: HarnessProvider = provider
        self._queues: dict[str, list[MockTurnScript | Any]] = {
            k: list(v) for k, v in (scripts_by_agent or {}).items()
        }
        self._selector = selector
        self._fault = fault or MockFaultSpec()
        self.requests: list[AgentTurnRequest] = []
        self._last_ship_vault_output: Any = None

    @property
    def provider_name(self) -> HarnessProvider:
        return self._provider

    def _next_script(self, request: AgentTurnRequest) -> MockTurnScript:
        if self._selector is not None:
            raw = self._selector(request)
            if isinstance(raw, MockTurnScript):
                return raw
            return MockTurnScript(structured_output=raw)

        queue = self._queues.get(request.agent_id)
        if queue:
            raw_item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(raw_item, MockTurnScript):
                return raw_item
            return MockTurnScript(structured_output=raw_item)

        if request.output_schema is not None:
            synth = synthesize_minimal_schema_instance(request.output_schema, request.prompt)
            return MockTurnScript(structured_output=synth)
        return MockTurnScript(text_output=json.dumps({"ok": True}))

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        _ = event_sink
        self.requests.append(request)

        if self._fault.raise_error_code is not None:
            raise OpenDVError(
                self._fault.raise_message,
                error_code=self._fault.raise_error_code,
                run_id=request.run_id,
            )

        script = self._next_script(request)
        if (
            self._fault.duplicate_ir_cycle
            and request.agent_id == "ship-vault"
            and self._last_ship_vault_output is not None
        ):
            structured = self._last_ship_vault_output
        else:
            structured = script.structured_output
            if request.output_schema is not None and structured is not None:
                structured = validate_candidate_with_schema(request.output_schema, structured)
            if request.agent_id == "ship-vault":
                self._last_ship_vault_output = structured

        if self._fault.jargon_injection:
            if isinstance(structured, SemanticCriticVerdict):
                structured = structured.model_copy(
                    update={"findings": [*structured.findings, "Check DV-DYN-004 and BKCC alignment."]}
                )
            elif isinstance(structured, HorizonCriticVerdict):
                structured = structured.model_copy(
                    update={"bucket_c_watchlist": [*structured.bucket_c_watchlist, "Watch BRK-02 CDK."]}
                )

        tool_events = list(script.tool_events)
        if script.scout_calls and scout_dispatcher is not None:
            for s_kind, t_ids in script.scout_calls:
                digests = await scout_dispatcher.dispatch_scout(
                    s_kind,
                    t_ids,
                    run_id=request.run_id,
                )
                tool_events.append(
                    {
                        "tool_name": "dispatch_scout",
                        "digests": [d.model_dump(mode="json") for d in digests],
                    }
                )

        turns_used = script.turns_used
        in_tok = script.input_tokens
        out_tok = script.output_tokens
        if self._fault.budget_exhaustion:
            turns_used = request.budget.max_turns + 1
            out_tok = request.budget.max_tokens + 1

        text_out = script.text_output
        if not text_out and structured is not None:
            text_out = (
                structured.model_dump_json()
                if hasattr(structured, "model_dump_json")
                else json.dumps(structured)
            )

        return AgentTurnResult(
            agent_id=request.agent_id,
            provider=self._provider,
            model_id=f"mock-{self._provider}",
            text_output=text_out,
            structured_output=structured,
            turns_used=turns_used,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=in_tok + out_tok,
            token_count_source=script.token_count_source,
            text_deltas=[text_out] if text_out else [],
            tool_events=tool_events,
        )


__all__ = [
    "DeterministicMockHarness",
    "InMemoryControlSignalReader",
    "InMemoryRunCheckpointStore",
    "InMemoryRunEventSink",
    "InMemoryScoutToolDispatcher",
    "MockFaultSpec",
    "MockTurnScript",
    "RecordsSourceAdapter",
    "ScriptSelector",
    "default_for_annotation",
    "synthesize_minimal_schema_instance",
    "validate_candidate_with_schema",
]
