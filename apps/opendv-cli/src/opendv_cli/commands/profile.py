"""`opendv profile` command handler (Bronze Graph B empirical profiling & discovery)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_core import find_workspace_root
from opendv_service import OpenDVService, ProfileRequest


def run_profile_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run Bronze source profiling in `--local` or `--api-url` mode."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    project_id: str | None = getattr(args, "project_id", None)
    run_id: str | None = getattr(args, "run_id", None)
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"

    if not use_local and api_url:
        if not run_id:
            return 2, {
                "status": "ERROR",
                "error": "--run-id is required in --api-url mode",
            }
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            wb02 = client.get_workbench_view(run_id, "WB-02")
            return 0, {
                "mode": "remote",
                "project_id": project_id,
                "run_id": run_id,
                "workbench_view": wb02,
            }

    repo_root = find_workspace_root()
    source_csv_arg: str | None = getattr(args, "source_csv", None)
    graph_b_arg: str | None = getattr(args, "graph_b", None)

    if not source_csv_arg and not graph_b_arg:
        return 2, {
            "status": "ERROR",
            "error": "Either --graph-b or --source-csv is required in --local mode",
        }

    source_system_raw: str = getattr(args, "source_system", None) or "bronze_source"
    table_name_raw: str | None = getattr(args, "table_name", None)

    service = OpenDVService(workspace_root=repo_root)
    try:
        resp = service.profile_pipeline(
            ProfileRequest(
                source_path=source_csv_arg,
                graph_b_path=graph_b_arg,
                project_id=project_id,
                source_system=source_system_raw,
                table_name=table_name_raw,
            )
        )
    except Exception as exc:
        return 2, {
            "status": "ERROR",
            "error": str(exc),
        }

    if source_csv_arg:
        return 0, {
            "mode": "local",
            "project_id": project_id,
            "status": "PASS",
            "profile_id": resp.profile_id,
            "source_system": resp.source_system,
            "table_name": resp.table_name,
            "table_count": resp.table_count,
            "column_count": resp.column_count,
            "source_csv_path": resp.source_path,
            "graph_b_profile": resp.graph_b_profile,
        }

    return 0, {
        "mode": "local",
        "project_id": project_id,
        "status": "PASS",
        "profile_id": resp.profile_id,
        "table_count": resp.table_count,
        "column_count": resp.column_count,
        "graph_b_path": resp.source_path,
    }


__all__ = ["run_profile_command"]
