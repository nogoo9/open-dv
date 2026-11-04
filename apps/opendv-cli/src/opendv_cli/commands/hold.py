"""`opendv hold` command handler (list and resolve Steward Captain-Holds)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_service import (
    CaptainHoldDecisionRequest,
    PlatformStateContainer,
)


def run_hold_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """List or resolve Captain-Holds in `--local` or `--api-url` mode."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    run_id: str | None = getattr(args, "run_id", None)
    role: str = getattr(args, "role", None) or "DATA_STEWARD"
    hold_action: str = (getattr(args, "hold_action", None) or "list").upper()
    hold_id: str | None = getattr(args, "hold_id", None)
    event_id: str | None = getattr(args, "event_id", None)
    decision_val: str = (
        getattr(args, "decision", None)
        or (hold_action if hold_action in {"APPROVE", "REJECT", "ANSWER"} else "APPROVE")
    ).upper()
    candidate_id: str | None = getattr(args, "selected_candidate_id", None)
    notes: str = getattr(args, "notes", None) or "Confirmed via opendv hold CLI command."
    steward_id: str = getattr(args, "steward_id", None) or "steward-cli"

    if not run_id:
        return 2, {
            "status": "ERROR",
            "error": "--run-id is required for 'hold' subcommands",
        }

    if not use_local and api_url:
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            if hold_action == "LIST":
                return 0, client.get_decisions(run_id)
            if not hold_id or not event_id:
                decisions_data = client.get_decisions(run_id)
                active_holds = decisions_data.get("active_holds", [])
                if len(active_holds) == 1:
                    single = active_holds[0]
                    hold_id = hold_id or single.get("hold_id") or single.get("decision_id")
                    event_id = (
                        event_id
                        or single.get("source_event_id")
                        or single.get("event_id")
                        or hold_id
                    )
                else:
                    return 2, {
                        "status": "ERROR",
                        "error": (
                            "--hold-id and --event-id are required when no single active hold "
                            "is pending"
                        ),
                    }
            resp = client.submit_decision(
                run_id,
                {
                    "event_id": event_id,
                    "hold_id": hold_id,
                    "decision_type": "RESOLVE_CAPTAIN_HOLD",
                    "action": decision_val,
                    "selected_candidate_id": candidate_id,
                    "resolution_notes": notes,
                    "steward_id": steward_id,
                },
            )
            return 0, resp

    state = PlatformStateContainer()
    if run_id not in state.runs:
        raise ValueError(f"Run '{run_id}' not found.")
    mgr = state.hold_managers[run_id]
    if hold_action == "LIST":
        pending = [rec.model_dump(mode="json") for rec in mgr.list_pending_holds(run_id=run_id)]
        return 0, {
            "mode": "local",
            "run_id": run_id,
            "active_holds": pending,
            "decisions": [],
        }

    if not hold_id or not event_id:
        pending_recs = mgr.list_pending_holds(run_id=run_id)
        if len(pending_recs) == 1:
            single_rec = pending_recs[0]
            hold_id = hold_id or single_rec.hold_id or single_rec.decision_id
            event_id = event_id or single_rec.event_id or hold_id
        else:
            return 2, {
                "status": "ERROR",
                "error": (
                    "--hold-id and --event-id are required when no single active hold is pending"
                ),
            }

    decision_resp = state.resolve_captain_hold(
        run_id,
        CaptainHoldDecisionRequest(
            event_id=event_id,
            hold_id=hold_id,
            action=decision_val,  # type: ignore[arg-type]
            selected_candidate_id=candidate_id,
            resolution_notes=notes,
            steward_id=steward_id,
        ),
    )
    return 0, decision_resp.model_dump(mode="json")
