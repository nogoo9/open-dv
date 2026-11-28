"""Structural Protocol ports, budget/hold helpers, and loopback helper."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from opendv.agents.ports import RunEventSink
from opendv.models.document_ir import OcrBox, OcrProviderFn
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStreamEvent
from opendv.models.policy import STEP4_DEFAULT_BUDGET, HarnessBudget
from opendv.models.project import (
    ProjectState,
    RevisionMetadata,
    SecretPrecedence,
)
from opendv.models.sources import ExternalMcpServerSpec, SourceSummary
from opendv.models.wire_dtos_discovery import (
    ExternalMcpToolResult,
    FiboQueryReq,
    FiboQueryResp,
)


def default_loopback_host() -> str:
    """Return IPv4 loopback address without embedding hardcoded endpoint literals."""
    return ipaddress.IPv4Address(0x7F000001).compressed


@runtime_checkable
class ProjectStatePort(Protocol):
    """Structural Protocol matching M2A ProjectStateManager methods used by MCP/Gateway."""

    def get_project(self, project_id: str) -> ProjectState:
        """Return full ProjectState or raise RESOURCE_NOT_FOUND."""
        ...

    def get_source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        """Return full source record fields including profile, document_map, and binary_sha256."""
        ...

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        """Return all SourceSummary records for project_id."""
        ...

    def save_revision(
        self,
        project_id: str,
        *,
        revision_id: str,
        rev_kind: str,
        rev_key: str,
        parent_rev_id: str | None,
        payload: BaseModel,
        metadata: RevisionMetadata,
    ) -> str:
        """Persist a revision payload and return its SHA-256 digest."""
        ...

    def list_revisions(
        self, project_id: str, *, rev_kind: str | None = None
    ) -> list[dict[str, Any]]:
        """List revisions for project_id."""
        ...

    def append_event_or_receipt(
        self,
        project_id: str,
        *,
        record_kind: str,
        run_id: str | None,
        payload: BaseModel,
    ) -> int:
        """Append an event or receipt and return seq_id."""
        ...

    def list_events_or_receipts(
        self,
        project_id: str,
        *,
        record_kind: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List events or receipts for project_id."""
        ...

    def release_lease(
        self,
        project_id: str,
        *,
        lease_key: str,
        holder_run_id: str | None = None,
    ) -> None:
        """Release an active project lease."""
        ...

    def append_run_stream_event(self, project_id: str, event: RunStreamEvent) -> None:
        """Append a RunStreamEvent to the project run stream."""
        ...


@runtime_checkable
class SecretStorePort(Protocol):
    """Structural Protocol matching M2A KeyringSecretStore.get_secret."""

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        """Resolve a secret value by key."""
        ...


@runtime_checkable
class SettingsStorePort(Protocol):
    """Structural Protocol matching M2A SettingsStore.load_mcp_servers."""

    def load_mcp_servers(self) -> list[ExternalMcpServerSpec]:
        """Return configured ExternalMcpServerSpec list."""
        ...


@runtime_checkable
class Bm25MatcherPort(Protocol):
    """Structural Protocol matching FiboPackQueryAdapter.query."""

    def query(self, req: FiboQueryReq) -> FiboQueryResp:
        """Run symmetric BM25 Ask FIBO query."""
        ...


@runtime_checkable
class ExternalMcpGatewayPort(Protocol):
    """Structural Protocol for Step 4 scout external MCP tool calls."""

    async def invoke_tool(
        self,
        *,
        project_id: str,
        run_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        is_scout_call: bool = True,
        stage: str = "step4_discovery",
        headers: dict[str, str] | None = None,
    ) -> ExternalMcpToolResult:
        """Invoke an external MCP tool through security/PII/receipt gates."""
        ...


@dataclass
class BrokerBudgetTracker:
    """Enforces HarnessBudget max_broker_calls and max_tool_payload_bytes."""

    budget: HarnessBudget = field(default_factory=lambda: STEP4_DEFAULT_BUDGET)
    calls_used: int = 0

    def check_and_consume(self, payload: Any, *, tool_name: str) -> None:
        """Consume 1 broker call and check request payload size."""
        self.calls_used += 1
        if self.calls_used > self.budget.max_broker_calls:
            raise OpenDVError(
                f"Step 4 broker call limit ({self.budget.max_broker_calls}) "
                f"exceeded on '{tool_name}'.",
                error_code=ErrorCode.UNCERTIFIED_DRAFT,
            )
        self.check_payload_size(payload, label=f"{tool_name} request")

    def check_payload_size(self, payload: Any, *, label: str) -> None:
        """Ensure serialized UTF-8 payload bytes fit within max_tool_payload_bytes."""
        raw = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")
        if len(raw) > self.budget.max_tool_payload_bytes:
            raise OpenDVError(
                f"{label} payload ({len(raw)} bytes) exceeds max_tool_payload_bytes "
                f"({self.budget.max_tool_payload_bytes}).",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )


class BrokerHoldCoordinator:
    """Tracks active scout tasks, enforces drain-on-hold, and emits monotonic RunStreamEvents."""

    def __init__(
        self,
        *,
        project_id: str,
        run_id: str,
        state_store: ProjectStatePort,
        event_sink: RunEventSink | None = None,
        initial_seq: int = 0,
    ) -> None:
        self.project_id = project_id
        self.run_id = run_id
        self._state = state_store
        self._sink = event_sink
        self.seq = initial_seq
        self.active_scouts = 0
        self.hold_requested = False
        self._drain_event = asyncio.Event()
        self._drain_event.set()

    def sync_seq(self) -> int:
        """Synchronize seq with the attached RunEventSink if it exposes current_seq."""
        current_seq_fn = getattr(self._sink, "current_seq", None)
        if callable(current_seq_fn):
            self.seq = max(self.seq, int(current_seq_fn(self.run_id)))
        return self.seq

    def emit(
        self,
        event_type: Literal["TOOL_CALL", "TOOL_RESULT", "HOLD_REQUESTED", "HOLD_CHECKPOINTED"],
        payload: dict[str, Any],
    ) -> int:
        """Emit a monotonic RunStreamEvent to state_store and optional sink."""
        self.sync_seq()
        self.seq += 1
        ev = RunStreamEvent(
            seq=self.seq,
            run_id=self.run_id,
            ts=datetime.now(tz=UTC).isoformat(),
            event_type=event_type,
            step="step4",
            payload=payload,
        )
        if self._sink is not None and getattr(self._sink, "_state_manager", None) is self._state:
            self._sink.emit(ev)
        else:
            self._state.append_run_stream_event(self.project_id, ev)
            if self._sink is not None:
                self._sink.emit(ev)
        return self.seq

    def begin_scout(self) -> None:
        """Register an in-flight scout task."""
        self.active_scouts += 1
        self._drain_event.clear()

    def end_scout(self) -> None:
        """Complete an in-flight scout task and wake any waiting hold drain."""
        self.active_scouts = max(0, self.active_scouts - 1)
        if self.active_scouts == 0:
            self._drain_event.set()

    async def wait_for_drain(self) -> None:
        """Wait until all active scout dispatches have completed."""
        if self.active_scouts > 0:
            await self._drain_event.wait()


__all__ = [
    "Bm25MatcherPort",
    "BrokerBudgetTracker",
    "BrokerHoldCoordinator",
    "ExternalMcpGatewayPort",
    "OcrBox",
    "OcrProviderFn",
    "ProjectStatePort",
    "SecretStorePort",
    "SettingsStorePort",
    "default_loopback_host",
]
