"""`opendv profile` command handler (Bronze Graph B empirical profiling & discovery)."""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_cli.commands.verify import resolve_repo_path
from opendv_core import find_workspace_root, validate_graph_b


def run_profile_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run Bronze source profiling in `--local` or `--api-url` mode."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    project_id: str = getattr(args, "project_id", None) or "proj-apex-horizon-pe"
    run_id: str = getattr(args, "run_id", None) or "run-pe-apex-horizon-20261030"
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"

    if not use_local and api_url:
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            wb02 = client.get_workbench_view(run_id, "WB-02")
            return 0, {
                "mode": "remote",
                "project_id": project_id,
                "run_id": run_id,
                "workbench_view": wb02,
            }

    repo_root = find_workspace_root()
    graph_b_arg = (
        getattr(args, "graph_b", None) or "examples/private_equity/pe_graph_b_profile.json"
    )
    graph_b_path = resolve_repo_path(repo_root, graph_b_arg)
    if not graph_b_path.exists():
        return 2, {
            "status": "ERROR",
            "error": f"Graph B profile not found: {graph_b_path}",
        }

    graph_b_doc: dict[str, Any] = json.loads(graph_b_path.read_text(encoding="utf-8"))
    validate_graph_b(graph_b_doc)
    tables: list[dict[str, Any]] = list(graph_b_doc.get("tables", []))
    for sys_item in graph_b_doc.get("source_systems", []):
        if isinstance(sys_item, dict):
            tables.extend(t for t in sys_item.get("tables", []) if isinstance(t, dict))
    total_cols = sum(len(t.get("columns", [])) for t in tables if isinstance(t, dict))

    return 0, {
        "mode": "local",
        "project_id": project_id,
        "status": "PASS",
        "profile_id": graph_b_doc.get("profile_id", "pe_graph_b_profile"),
        "table_count": len(tables),
        "column_count": total_cols,
        "graph_b_path": str(graph_b_path),
    }
