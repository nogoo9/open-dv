"""`opendv run` command handler (manage runs and stream SSE observability events)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_platform_api.deps import PlatformStateContainer, redact_event_for_role


def run_run_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv run` actions (`list`, `get`, `create`, `abort`, `events`)."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    run_id: str = getattr(args, "run_id", None) or "run-pe-apex-horizon-20261030"
    project_id: str = getattr(args, "project_id", None) or "proj-apex-horizon-pe"
    action: str = getattr(args, "run_action", None) or "get"
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"
    harness: str = getattr(args, "harness", None) or "antigravity"
    last_event_id: str | None = getattr(args, "last_event_id", None)

    if not use_local and api_url:
        with OpenDVCliApiClient(
            base_url=api_url, role=role, transport=transport
        ) as client:
            if action == "list":
                return 0, client.list_runs(project_id=project_id)
            if action == "create":
                return 0, client.create_run(
                    {"project_id": project_id, "harness_provider": harness}
                )
            if action == "abort":
                return 0, client.abort_run(run_id)
            if action == "events":
                sse_text = client.stream_events_text(
                    run_id, last_event_id=last_event_id
                )
                return 0, {
                    "mode": "remote",
                    "run_id": run_id,
                    "role": role,
                    "sse_stream": sse_text,
                }
            return 0, client.get_run(run_id)

    state = PlatformStateContainer()
    if action == "list":
        items = [r.model_dump(mode="json") for r in state.runs.values()]
        return 0, {"mode": "local", "items": items, "total": len(items)}
    if action == "create":
        from opendv_platform_api.deps import CreateRunRequest

        created = state.create_run(
            CreateRunRequest(
                project_id=project_id,
                harness_provider=harness,  # type: ignore[arg-type]
            )
        )
        return 0, created.model_dump(mode="json")
    if action == "abort":
        aborted = state.abort_run(run_id)
        return 0, aborted.model_dump(mode="json")
    if action == "events":
        events = [
            redact_event_for_role(ev, role)
            for ev in state.run_events.get(run_id, [])
        ]
        return 0, {
            "mode": "local",
            "run_id": run_id,
            "role": role,
            "event_count": len(events),
            "events": events,
        }

    run_dto = state.runs[run_id]
    return 0, run_dto.model_dump(mode="json")
