"""`opendv profile` command handler (Bronze Graph B empirical profiling & discovery)."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_cli.commands.verify import resolve_repo_path
from opendv_core import find_workspace_root, validate_graph_b
from opendv_duckdb_sandbox import GraphBProfiler


def _sanitize_sql_name(raw: str, default: str = "profiled_csv") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not cleaned:
        return default
    if not cleaned[0].isalpha() and cleaned[0] != "_":
        cleaned = f"t_{cleaned}"
    return cleaned


def _sanitize_domain_ns(raw: str, default: str = "bronze_source") -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
    if not cleaned:
        return default
    if not cleaned[0].isalpha():
        cleaned = f"d_{cleaned}"
    return cleaned


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

    if source_csv_arg:
        csv_path = resolve_repo_path(repo_root, source_csv_arg)
        if not csv_path.exists():
            return 2, {
                "status": "ERROR",
                "error": f"Source CSV file not found: {csv_path}",
            }
        source_system_raw: str = getattr(args, "source_system", None) or "bronze_source"
        source_sys_code = (
            re.sub(r"[^A-Z0-9_]+", "_", source_system_raw.upper()).strip("_") or "BRONZE_SOURCE"
        )
        table_name_raw: str | None = getattr(args, "table_name", None)
        resolved_table_name = _sanitize_sql_name(table_name_raw or csv_path.stem)
        domain_ns = _sanitize_domain_ns(project_id or source_system_raw)

        profiler = GraphBProfiler()
        try:
            profiler.profile_csv(csv_path, view_name=resolved_table_name)
            profile_obj = profiler.build_graph_b_profile(
                domain=domain_ns,
                source_system_id=source_sys_code,
                bkcc_namespace=source_sys_code,
                table_names=[resolved_table_name],
            )
        finally:
            profiler.close()

        graph_b_doc = profile_obj.model_dump(mode="json", exclude_none=True)
        validate_graph_b(graph_b_doc)
        total_cols = sum(
            len(t.columns) for sys_item in profile_obj.source_systems for t in sys_item.tables
        )
        total_tables = sum(len(sys_item.tables) for sys_item in profile_obj.source_systems)
        return 0, {
            "mode": "local",
            "project_id": project_id,
            "status": "PASS",
            "profile_id": f"{domain_ns}_profile",
            "source_system": source_sys_code,
            "table_name": resolved_table_name,
            "table_count": total_tables,
            "column_count": total_cols,
            "source_csv_path": str(csv_path),
            "graph_b_profile": graph_b_doc,
        }

    if not graph_b_arg:
        return 2, {
            "status": "ERROR",
            "error": "Either --graph-b or --source-csv is required in --local mode",
        }

    graph_b_path = resolve_repo_path(repo_root, graph_b_arg)
    if not graph_b_path.exists():
        return 2, {
            "status": "ERROR",
            "error": f"Graph B profile not found: {graph_b_path}",
        }

    graph_b_doc = json.loads(graph_b_path.read_text(encoding="utf-8"))
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
        "profile_id": (
            graph_b_doc.get("profile_id")
            or graph_b_doc.get("domain_namespace")
            or "graph_b_profile"
        ),
        "table_count": len(tables),
        "column_count": total_cols,
        "graph_b_path": str(graph_b_path),
    }
