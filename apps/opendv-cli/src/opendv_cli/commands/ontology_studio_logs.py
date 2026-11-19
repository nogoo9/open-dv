"""Ontology Studio CLI session execution logs query and SSE follow handler."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import TYPE_CHECKING, Any

from opendv_cli.commands.ontology_studio_common import (
    _err,
    _err_from_opendv_error,
    _err_from_workflow_error,
    _require_session_context,
    _resolve_studio_principal,
)
from opendv_contracts.errors import OpenDVError
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.wizard_workflows import query_logs_workflow, stream_logs_workflow

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


async def _collect_sse_entries(
    session_mgr: Any,
    session_id: str,
    *,
    stage_id: str | None,
    wizard_step: int | None,
    persona: str | None,
    category: str | None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    async for chunk in stream_logs_workflow(
        session_mgr,
        session_id,
        once=True,
        stage_id=stage_id,
        wizard_step=wizard_step,
        persona=persona,
        category=category,
    ):
        for block in chunk.split("\n\n"):
            if "event: log_entry" not in block:
                continue
            for line in block.splitlines():
                if line.startswith("data: "):
                    collected.append(json.loads(line[6:]))
    return collected


def handle_logs(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """`opendv ontology studio logs` query and live SSE follow handler."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    sid_raw = getattr(args, "session", None) or getattr(args, "session_id", None)
    if not sid_raw:
        return _err(40, "SYNTAX_INVALID_ARGUMENT", "Missing required --session or --session-id flag.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, _, sid = ctx  # type: ignore[misc]
    stage = getattr(args, "stage", None)
    step = getattr(args, "step", None)
    category = getattr(args, "category", None)
    persona = getattr(args, "persona", None)
    follow = bool(getattr(args, "follow", False))
    try:
        if follow:
            entries = asyncio.run(
                _collect_sse_entries(
                    session_mgr,
                    sid,
                    stage_id=stage,
                    wizard_step=step,
                    persona=persona,
                    category=category,
                )
            )
            return 0, {"session_id": sid, "entries": entries, "total": len(entries), "follow": True}
        return 0, query_logs_workflow(
            session_mgr,
            sid,
            stage_id=stage,
            wizard_step=step,
            persona=persona,
            category=category,
        )
    except StudioWorkflowError as exc:
        return _err_from_workflow_error(exc, wizard_mode=True)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc, wizard_mode=True)
