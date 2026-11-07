"""Human-in-the-Loop (`ASK-USER` / `STEWARD_HOLD`) Lifecycle Manager.

Manages Captain-Hold escalations (e.g., Tier-U3 unstructured entity resolution
margin < 0.15 or DV-EVO-003 grain conflicts), steward decision resolution, and
session resumption via `AgentTaskSpec.resume_session_id`.

Conforms to docs/08-platform-mvp-monorepo-and-api-spec.md (§1.1, §5.2)
and docs/09-agnostic-agent-harness-and-sdk-spec.md (§2-§4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessRunResult,
    HarnessStreamEvent,
    OutputT,
    PipelineStageId,
)

StewardHoldStatus = Literal["PENDING", "APPROVED", "REJECTED"]
StewardDecisionAction = Literal["APPROVE", "APPROVED", "REJECT", "REJECTED", "ANSWER"]


class StewardHoldRecord(BaseModel):
    """Durable record of an `ASK-USER` / `STEWARD_HOLD` pipeline pause and resolution."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"dec-{uuid.uuid4().hex[:10]}")
    hold_id: str = Field(default="")
    event_id: str | None = None
    run_id: str
    task_id: str
    session_id: str
    persona: AgentPersona = AgentPersona.VAULT_FIRST_MATE
    stage_id: PipelineStageId = PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED
    headline: str = "Steward Captain-Hold decision required."
    escalation_reason: str = "TIER_U3_AMBIGUOUS_ENTITY"
    rule_id: str = "ALG-A9B"
    options: list[dict[str, Any]] = Field(default_factory=list)
    status: StewardHoldStatus = "PENDING"
    selected_option_id: str | None = None
    resolution_notes: str | None = None
    created_at_utc: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    resolved_at_utc: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.hold_id:
            object.__setattr__(self, "hold_id", self.decision_id)


class StewardHoldManager:
    """In-memory and optional file-backed (`state/holds/<id>.json`) Captain-Hold manager."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir
        self._holds_by_id: dict[str, StewardHoldRecord] = {}
        self._alias_index: dict[str, str] = {}
        self._load_persisted_holds()

    def _load_persisted_holds(self) -> None:
        if self._state_dir is None:
            return
        holds_dir = self._state_dir / "holds"
        if not holds_dir.is_dir():
            return
        for path in sorted(holds_dir.glob("*.json")):
            record = StewardHoldRecord.model_validate_json(path.read_text(encoding="utf-8"))
            self._index_record(record, persist=False)

    def _persist(self, record: StewardHoldRecord) -> None:
        if self._state_dir is not None:
            holds_dir = self._state_dir / "holds"
            holds_dir.mkdir(parents=True, exist_ok=True)
            target = holds_dir / f"{record.decision_id}.json"
            target.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def _index_record(self, record: StewardHoldRecord, *, persist: bool = True) -> None:
        self._holds_by_id[record.decision_id] = record
        self._alias_index[record.decision_id] = record.decision_id
        if record.hold_id:
            self._alias_index[record.hold_id] = record.decision_id
        if record.event_id:
            self._alias_index[record.event_id] = record.decision_id
        if persist:
            self._persist(record)

    def record_hold(
        self,
        *,
        run_id: str,
        task_id: str,
        session_id: str,
        stage_id: PipelineStageId | str = PipelineStageId.STAGE_02_SCOUT_UNSTRUCTURED,
        options: list[dict[str, Any]] | None = None,
        decision_id: str | None = None,
        hold_id: str | None = None,
        event_id: str | None = None,
        persona: AgentPersona | str = AgentPersona.VAULT_FIRST_MATE,
        headline: str = "Steward Captain-Hold decision required.",
        escalation_reason: str = "TIER_U3_AMBIGUOUS_ENTITY",
        rule_id: str = "ALG-A9B",
        status: StewardHoldStatus = "PENDING",
    ) -> StewardHoldRecord:
        """Create and register a new `StewardHoldRecord` (`status='PENDING'`)."""
        eff_decision_id = decision_id or hold_id or f"dec-{uuid.uuid4().hex[:10]}"
        eff_hold_id = hold_id or eff_decision_id
        record = StewardHoldRecord(
            decision_id=eff_decision_id,
            hold_id=eff_hold_id,
            event_id=event_id,
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            persona=AgentPersona(persona),
            stage_id=PipelineStageId(stage_id),
            headline=headline,
            escalation_reason=escalation_reason,
            rule_id=rule_id,
            options=list(options or []),
            status=status,
        )
        self._index_record(record)
        return record

    def record_from_run_result(
        self,
        spec: AgentTaskSpec[Any],
        result: HarnessRunResult[Any],
        *,
        options: list[dict[str, Any]] | None = None,
        decision_id: str | None = None,
    ) -> StewardHoldRecord:
        """Extract `ASK-USER` metadata from a paused `HarnessRunResult` and record a hold."""
        ask_ev: dict[str, Any] = {}
        for ev in reversed(result.emitted_events):
            if isinstance(ev, dict) and ev.get("status") == "ASK-USER":
                ask_ev = ev
                break

        banner = ask_ev.get("layer1_story_banner") or {}
        layer2 = ask_ev.get("layer2_visual_payload") or {}
        metrics = layer2.get("summary_metrics") or {}
        rule_ids = ask_ev.get("rule_ids_evaluated") or ["ALG-A9B"]

        extracted_hold_id = str(metrics.get("hold_id") or "") or None
        extracted_event_id = str(ask_ev.get("event_id") or "") or None
        headline = str(
            banner.get("headline") or f"Steward hold requested at {spec.stage_id.value}."
        )
        escalation_reason = str(metrics.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY")

        return self.record_hold(
            run_id=result.run_id,
            task_id=result.task_id,
            session_id=result.session_id,
            stage_id=ask_ev.get("stage_id") or spec.stage_id,
            options=options,
            decision_id=decision_id or extracted_hold_id,
            hold_id=extracted_hold_id,
            event_id=extracted_event_id,
            persona=spec.persona,
            headline=headline,
            escalation_reason=escalation_reason,
            rule_id=str(rule_ids[0]) if rule_ids else "ALG-A9B",
        )

    def record_from_stream_event(
        self,
        event: HarnessStreamEvent,
        *,
        session_id: str | None = None,
        options: list[dict[str, Any]] | None = None,
        decision_id: str | None = None,
    ) -> StewardHoldRecord:
        """Record a hold directly from a `STEWARD_HOLD` or `ASK-USER` `HarnessStreamEvent`."""
        obs = event.observability_event or {}
        banner = obs.get("layer1_story_banner") or {}
        layer2 = obs.get("layer2_visual_payload") or {}
        metrics = layer2.get("summary_metrics") or {}
        rule_ids = obs.get("rule_ids_evaluated") or ["ALG-A9B"]

        extracted_hold_id = str(metrics.get("hold_id") or "") or None
        extracted_event_id = str(obs.get("event_id") or event.event_id)
        headline = str(
            banner.get("headline") or f"Steward hold requested at {event.stage_id.value}."
        )
        escalation_reason = str(metrics.get("escalation_reason") or "TIER_U3_AMBIGUOUS_ENTITY")

        return self.record_hold(
            run_id=event.run_id,
            task_id=event.task_id,
            session_id=session_id or event.task_id,
            stage_id=obs.get("stage_id") or event.stage_id,
            options=options,
            decision_id=decision_id or extracted_hold_id,
            hold_id=extracted_hold_id,
            event_id=extracted_event_id,
            persona=event.persona,
            headline=headline,
            escalation_reason=escalation_reason,
            rule_id=str(rule_ids[0]) if rule_ids else "ALG-A9B",
        )

    def get_hold(self, identifier: str) -> StewardHoldRecord:
        """Look up a `StewardHoldRecord` by `decision_id`, `hold_id`, or `event_id`."""
        canonical_id = self._alias_index.get(identifier, identifier)
        if canonical_id not in self._holds_by_id:
            raise KeyError(f"No steward hold found for identifier '{identifier}'.")
        return self._holds_by_id[canonical_id]

    def list_holds(
        self,
        *,
        run_id: str | None = None,
        status: StewardHoldStatus | None = None,
    ) -> list[StewardHoldRecord]:
        """List recorded holds, optionally filtered by `run_id` and/or `status`."""
        results: list[StewardHoldRecord] = []
        for rec in self._holds_by_id.values():
            if run_id is not None and rec.run_id != run_id:
                continue
            if status is not None and rec.status != status:
                continue
            results.append(rec)
        return results

    def list_pending_holds(self, run_id: str | None = None) -> list[StewardHoldRecord]:
        """List all holds currently in `PENDING` state."""
        return self.list_holds(run_id=run_id, status="PENDING")

    def resolve_hold(
        self,
        identifier: str,
        *,
        action: StewardDecisionAction = "APPROVE",
        selected_option_id: str | None = None,
        selected_candidate_id: str | None = None,
        resolution_notes: str | None = None,
    ) -> StewardHoldRecord:
        """Resolve a pending hold with steward approval (`APPROVED`) or rejection (`REJECTED`)."""
        record = self.get_hold(identifier)
        action_upper = action.strip().upper()
        if action_upper in {"APPROVE", "APPROVED", "ANSWER"}:
            new_status: StewardHoldStatus = "APPROVED"
        elif action_upper in {"REJECT", "REJECTED"}:
            new_status = "REJECTED"
        else:
            raise ValueError(f"Unsupported steward decision action '{action}'.")

        chosen_option = (
            selected_option_id if selected_option_id is not None else selected_candidate_id
        )
        updated = record.model_copy(
            update={
                "status": new_status,
                "selected_option_id": chosen_option,
                "resolution_notes": resolution_notes,
                "resolved_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        self._index_record(updated)
        return updated

    resolve_decision = resolve_hold

    def prepare_resumed_task_spec(
        self,
        original_spec: AgentTaskSpec[OutputT],
        hold: str | StewardHoldRecord,
        *,
        prompt_override: str | None = None,
    ) -> AgentTaskSpec[OutputT]:
        """Build a resumed `AgentTaskSpec` carrying `resume_session_id` and steward resolution."""
        record = self.get_hold(hold) if isinstance(hold, str) else hold
        if record.status == "PENDING":
            raise ValueError(
                f"Cannot resume task '{original_spec.task_id}': hold '{record.decision_id}' "
                "is still PENDING."
            )

        if prompt_override is not None:
            resumed_prompt = prompt_override
        else:
            option_clause = (
                f" Selected option: '{record.selected_option_id}'."
                if record.selected_option_id
                else ""
            )
            notes_clause = f" Notes: {record.resolution_notes}" if record.resolution_notes else ""
            resumed_prompt = (
                f"{original_spec.prompt}\n\n"
                f"[STEWARD_DECISION {record.decision_id}]: status={record.status}."
                f"{option_clause}{notes_clause}"
            )

        return original_spec.model_copy(
            update={
                "resume_session_id": record.session_id,
                "prompt": resumed_prompt,
            }
        )

    build_resumed_task_spec = prepare_resumed_task_spec


__all__ = [
    "StewardDecisionAction",
    "StewardHoldManager",
    "StewardHoldRecord",
    "StewardHoldStatus",
]
