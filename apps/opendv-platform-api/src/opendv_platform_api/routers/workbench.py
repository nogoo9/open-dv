"""Workbench view projection endpoints (`/api/v1/runs/{run_id}/workbench/{view_id}`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from opendv_platform_api.deps import (
    WORKBENCH_VIEW_TITLES,
    PlatformStateContainer,
    RbacRole,
    get_platform_state,
    get_rbac_role,
    redact_event_for_role,
)

router = APIRouter(prefix="/runs/{run_id}/workbench", tags=["workbench"])

_VIEW_LOOKUP: dict[str, str] = {}
for _short_id, _full_title in WORKBENCH_VIEW_TITLES.items():
    _VIEW_LOOKUP[_short_id.upper()] = _short_id
    _VIEW_LOOKUP[_full_title.upper()] = _short_id


@router.get("/{view_id}")
async def get_workbench_view_projection(
    run_id: str,
    view_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
    role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> dict[str, Any]:
    """Return RBAC-redacted 3-Layer workbench view state (`WB-00` through `WB-11`)."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    short_code = _VIEW_LOOKUP.get(view_id.strip().upper())
    if short_code is None:
        valid_short = sorted(WORKBENCH_VIEW_TITLES.keys())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown workbench view '{view_id}'. Expected one of {valid_short}.",
        )

    canonical_title = WORKBENCH_VIEW_TITLES[short_code]

    seen_ids: set[str] = set()
    matched_events: list[dict[str, Any]] = []
    for source_list in (
        state.run_events.get(run_id, []),
        state.run_workbench_events.get(run_id, []),
    ):
        for evt in source_list:
            evt_id = str(evt.get("event_id", ""))
            evt_view = str(evt.get("workbench_view_id") or evt.get("workbench_view") or "")
            if evt_view.startswith(short_code) and evt_id not in seen_ids:
                seen_ids.add(evt_id)
                matched_events.append(redact_event_for_role(evt, role.value))

    primary_event = matched_events[-1] if matched_events else None
    return {
        "run_id": run_id,
        "view_id": short_code,
        "canonical_view_name": canonical_title,
        "role": role.value,
        "event_count": len(matched_events),
        "event": primary_event,
        "primary_event": primary_event,
        "events": matched_events,
    }
