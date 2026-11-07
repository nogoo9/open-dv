"""Holds use case: List and resolve human steward holds on pipeline runs."""

from __future__ import annotations

from typing import Any

from opendv_service.errors import NotFoundError
from opendv_service.models import (
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
)
from opendv_service.state import StateStore


def list_holds(state: StateStore, run_id: str) -> list[dict[str, Any]]:
    """List all pending or recorded steward holds for the specified run."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")

    hold_mgr = state.hold_managers.get(run_id)
    if hold_mgr is None:
        return []

    holds: list[dict[str, Any]] = []
    for h in hold_mgr.list_holds():
        holds.append(h.model_dump(mode="json") if hasattr(h, "model_dump") else dict(h))
    return holds


def resolve_hold(
    state: StateStore,
    run_id: str,
    request: CaptainHoldDecisionRequest,
) -> CaptainHoldDecisionResponse:
    """Submit a steward decision to resolve an active hold and resume pipeline execution."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")

    hold_id = request.hold_id or (
        state.runs[run_id].active_hold_ids[0] if state.runs[run_id].active_hold_ids else None
    )
    if not hold_id:
        raise NotFoundError(f"No active hold specified or found on run '{run_id}'.")

    decision_action = request.action or request.decision or "APPROVE"
    notes = request.resolution_notes or request.steward_rationale or ""

    return state.resolve_captain_hold(
        run_id=run_id,
        hold_id=hold_id,
        decision=decision_action,
        notes=notes,
    )


__all__ = ["list_holds", "resolve_hold"]
