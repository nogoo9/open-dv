"""Human-in-the-loop Steward decision endpoints (`/api/v1/runs/{run_id}/decisions`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from opendv_platform_api.deps import (
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    PlatformStateContainer,
    get_platform_state,
)

router = APIRouter(prefix="/runs/{run_id}/decisions", tags=["decisions"])


@router.get("")
async def list_run_decisions(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> dict[str, Any]:
    """List active Captain-Holds and resolved Steward decisions for a run."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    hold_mgr = state.hold_managers[run_id]
    active_holds = [
        {
            "hold_id": rec.hold_id,
            "decision_id": rec.decision_id,
            "run_id": rec.run_id,
            "source_event_id": rec.event_id,
            "stage_id": rec.stage_id.value,
            "workbench_view_id": rec.workbench_view_id.value,
            "status": rec.status,
            "headline": rec.headline,
        }
        for rec in hold_mgr.list_pending_holds(run_id=run_id)
    ]
    decisions = [d.model_dump(mode="json") for d in state.run_decisions.get(run_id, [])]
    return {
        "run_id": run_id,
        "active_holds": active_holds,
        "decisions": decisions,
    }


@router.post("", response_model=CaptainHoldDecisionResponse)
async def submit_captain_hold_decision(
    run_id: str,
    req: CaptainHoldDecisionRequest,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> CaptainHoldDecisionResponse:
    """Resolve an active Tier U3 / G3 / G4 Captain-Hold with a Steward decision."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    try:
        return state.resolve_captain_hold(run_id=run_id, req=req)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
