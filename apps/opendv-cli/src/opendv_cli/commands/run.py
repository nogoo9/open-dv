"""`opendv run` command handler (manage runs and stream SSE observability events)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_service import CreateRunRequest, PlatformStateContainer, redact_event_for_role


def run_run_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv run` actions (`list`, `get`, `create`, `abort`, `events`)."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    run_id: str | None = getattr(args, "run_id", None)
    project_id: str | None = getattr(args, "project_id", None)
    action: str = getattr(args, "run_action", None) or "get"
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"
    harness: str = getattr(args, "harness", None) or "antigravity"
    last_event_id: str | None = getattr(args, "last_event_id", None)

    if action == "create" and not project_id:
        return 2, {
            "status": "ERROR",
            "error": "--project-id is required for 'run create'",
        }
    if action in {"get", "events", "abort"} and not run_id:
        return 2, {
            "status": "ERROR",
            "error": f"--run-id is required for 'run {action}'",
        }

    if not use_local and api_url:
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            if action == "list":
                return 0, client.list_runs(project_id=project_id)
            if action == "create":
                assert project_id is not None
                return 0, client.create_run({"project_id": project_id, "harness_provider": harness})
            assert run_id is not None
            if action == "abort":
                return 0, client.abort_run(run_id)
            if action == "events":
                sse_text = client.stream_events_text(run_id, last_event_id=last_event_id)
                return 0, {
                    "mode": "remote",
                    "run_id": run_id,
                    "role": role,
                    "sse_stream": sse_text,
                }
            return 0, client.get_run(run_id)

    state = PlatformStateContainer()
    if action == "list":
        runs = list(state.runs.values())
        if project_id is not None:
            runs = [r for r in runs if r.project_id == project_id]
        items = [r.model_dump(mode="json") for r in runs]
        return 0, {"mode": "local", "items": items, "total": len(items)}
    if action == "create":
        assert project_id is not None
        if project_id not in state.projects:
            raise ValueError(f"Project '{project_id}' not found.")
        created = state.create_run(
            CreateRunRequest(
                project_id=project_id,
                harness_provider=harness,  # type: ignore[arg-type]
            )
        )
        return 0, created.model_dump(mode="json")

    assert run_id is not None
    if action == "abort":
        if run_id not in state.runs:
            raise ValueError(f"Run '{run_id}' not found.")
        aborted = state.abort_run(run_id)
        return 0, aborted.model_dump(mode="json")
    if action == "events":
        if run_id not in state.runs:
            raise ValueError(f"Run '{run_id}' not found.")
        raw_events = state.run_events.get(run_id, [])
        if last_event_id is not None:
            skipping = True
            sliced: list[dict[str, Any]] = []
            for ev in raw_events:
                if skipping:
                    if str(ev.get("event_id")) == last_event_id:
                        skipping = False
                    continue
                sliced.append(ev)
            if not skipping:
                raw_events = sliced
        events = [redact_event_for_role(ev, role) for ev in raw_events]
        return 0, {
            "mode": "local",
            "run_id": run_id,
            "role": role,
            "last_event_id": last_event_id,
            "event_count": len(events),
            "events": events,
        }

    if run_id not in state.runs:
        raise ValueError(f"Run '{run_id}' not found.")
    run_dto = state.runs[run_id]
    return 0, run_dto.model_dump(mode="json")
