"""Append-Only Telemetry Ledger Emitter and Async SSE Streaming Engine.

Conforms to docs/06-explainability-and-observability-spec.md (§1.1, §1.4),
docs/08-platform-mvp-monorepo-and-api-spec.md (§4.2, §5.2),
and schemas/vault-observability-event.schema.json.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opendv_core.models.observability import VaultObservabilityEvent
from opendv_core.schema_registry import validate_observability_event
from opendv_telemetry.cas_store import ContentAddressableStore
from opendv_telemetry.pii_detector import validate_no_pii_leaks
from opendv_telemetry.rbac_redactor import RbacRole, redact_event_for_role
from opendv_telemetry.trace_context import get_current_trace_context


async def format_sse_event_stream(
    events: list[dict[str, Any]],
    *,
    role: RbacRole | str = RbacRole.INTERNAL_MODELER,
    last_event_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream schema-valid VaultObservabilityEvent records as W3C Server-Sent Events (SSE).

    Supports seamless Last-Event-ID reconnection and terminal 'run_complete' signalling.
    """
    skipping = last_event_id is not None
    matched_last = False

    for raw_event in events:
        ev_id = str(raw_event.get("event_id", ""))
        if skipping:
            if ev_id == last_event_id:
                skipping = False
                matched_last = True
            continue

        redacted = redact_event_for_role(raw_event, role)
        payload_json = json.dumps(redacted, sort_keys=True)
        yield f"id: {ev_id}\ndata: {payload_json}\n\n"

    # If last_event_id was provided but never matched in the stream, replay all
    if skipping and not matched_last:
        for raw_event in events:
            ev_id = str(raw_event.get("event_id", ""))
            redacted = redact_event_for_role(raw_event, role)
            payload_json = json.dumps(redacted, sort_keys=True)
            yield f"id: {ev_id}\ndata: {payload_json}\n\n"

    yield 'event: run_complete\ndata: {"status":"COMPLETED"}\n\n'


class VaultLedgerEmitter:
    """Thread-safe append-only ledger emitter and asynchronous SSE broadcaster.

    Guarantees:
    1. Every emitted event validates against schemas/vault-observability-event.schema.json
       and VaultObservabilityEvent domain model invariants.
    2. Zero raw 64-hex strings or unmasked PII permitted in Layer 1 story banners.
    3. Broadcasts events to live SSE subscribers and replays monotonic events on reconnect.
    """

    def __init__(
        self,
        ledger_path: Path | str = "state/vault-ledger.jsonl",
        cas_store: ContentAddressableStore | None = None,
    ) -> None:
        self.ledger_path = Path(ledger_path).resolve()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.cas_store = cas_store or ContentAddressableStore(self.ledger_path.parent / "artifacts")
        self._lock = threading.Lock()
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def _prepare_event_dict(
        self, event: dict[str, Any] | VaultObservabilityEvent
    ) -> dict[str, Any]:
        if isinstance(event, VaultObservabilityEvent):
            data: dict[str, Any] = event.model_dump(mode="json")
        else:
            data = copy.deepcopy(event)

        # Attach active W3C trace context if missing
        active_trace = get_current_trace_context()
        if active_trace is not None:
            if not data.get("trace_id"):
                data["trace_id"] = active_trace.trace_id
            if not data.get("span_id"):
                data["span_id"] = active_trace.span_id
            if not data.get("parent_span_id") and active_trace.parent_span_id:
                data["parent_span_id"] = active_trace.parent_span_id

        # Ensure timestamp_utc is populated
        if not data.get("timestamp_utc"):
            data["timestamp_utc"] = datetime.now(UTC).isoformat()
        elif isinstance(data["timestamp_utc"], datetime):
            data["timestamp_utc"] = data["timestamp_utc"].isoformat()

        # Validate against Pydantic model invariants
        VaultObservabilityEvent.model_validate(data)

        # Validate against JSON Schema Draft 2020-12
        validate_observability_event(data)

        # Scan for Layer 1 leaks (raw 64-hex strings, SSN/EIN/ABA)
        validate_no_pii_leaks(data)

        return data

    def emit(self, event: dict[str, Any] | VaultObservabilityEvent) -> dict[str, Any]:
        """Validate, append to the JSONL ledger, and broadcast to async SSE subscribers."""
        validated = self._prepare_event_dict(event)
        line = json.dumps(validated, sort_keys=True) + "\n"

        with self._lock:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

        # Broadcast copy to live subscribers
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(copy.deepcopy(validated))

        return validated

    async def emit_async(self, event: dict[str, Any] | VaultObservabilityEvent) -> dict[str, Any]:
        """Asynchronously validate, append to the ledger, and broadcast to SSE subscribers."""
        validated = self._prepare_event_dict(event)
        line = json.dumps(validated, sort_keys=True) + "\n"

        def _sync_write() -> None:
            with self._lock:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger_path.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()

        await asyncio.to_thread(_sync_write)

        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(copy.deepcopy(validated))

        return validated

    def read_all_events(self) -> list[dict[str, Any]]:
        """Read all JSONL events from the ledger file in monotonic order."""
        if not self.ledger_path.is_file():
            return []

        events: list[dict[str, Any]] = []
        with self._lock, self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned:
                    events.append(json.loads(cleaned))
        return events

    def replay_events(
        self,
        after_event_id: str | None = None,
        role: RbacRole | str = RbacRole.INTERNAL_MODELER,
    ) -> list[dict[str, Any]]:
        """Replay monotonic ledger events starting after `after_event_id`, redacted for `role`."""
        all_events = self.read_all_events()
        if after_event_id is None:
            return [redact_event_for_role(ev, role=role) for ev in all_events]

        skipping = True
        replayed: list[dict[str, Any]] = []
        for ev in all_events:
            if skipping:
                if str(ev.get("event_id")) == str(after_event_id):
                    skipping = False
                continue
            replayed.append(redact_event_for_role(ev, role=role))

        # If after_event_id was not matched, return all events
        if skipping:
            return [redact_event_for_role(ev, role=role) for ev in all_events]

        return replayed

    async def subscribe_sse(
        self,
        last_event_id: str | None = None,
        role: RbacRole | str = RbacRole.INTERNAL_MODELER,
        replay_history: bool = True,
    ) -> AsyncIterator[str]:
        """Subscribe to Server-Sent Events (SSE) with cursor replay and monotonic ordering."""
        seen_event_ids: set[str] = set()

        if replay_history:
            past_events = self.replay_events(after_event_id=last_event_id, role=role)
            for ev in past_events:
                ev_id = str(ev.get("event_id", ""))
                seen_event_ids.add(ev_id)
                payload_json = json.dumps(ev, sort_keys=True)
                yield f"id: {ev_id}\ndata: {payload_json}\n\n"

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        self._subscribers.add(queue)

        try:
            while True:
                raw_event = await queue.get()
                ev_id = str(raw_event.get("event_id", ""))
                if ev_id in seen_event_ids:
                    continue
                seen_event_ids.add(ev_id)

                redacted = redact_event_for_role(raw_event, role=role)
                payload_json = json.dumps(redacted, sort_keys=True)
                yield f"id: {ev_id}\ndata: {payload_json}\n\n"

                # Check if pipeline completed
                if raw_event.get("stage_id") == "STAGE_07_DETERMINISTIC_COMPILER":
                    yield 'event: run_complete\ndata: {"status":"COMPLETED"}\n\n'
                    break
        finally:
            self._subscribers.discard(queue)

    def count_events(self) -> int:
        """Count total events in the ledger."""
        return len(self.read_all_events())


__all__ = [
    "VaultLedgerEmitter",
    "format_sse_event_stream",
]
