"""Profile pipeline use case: Profile bronze tabular data into Statistical Graph B."""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from opendv_contracts import ProfileRequest, ProfileResponse, RenderedOutput
from opendv_core import validate_graph_b
from opendv_core.paths import resolve_workspace_path
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_service.errors import InputValidationError
from opendv_service.rendering import render_profile_text


def _sanitize_sql_name(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return cleaned or "bronze_table"


def _sanitize_domain_ns(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
    return cleaned or "default_domain"


def profile_pipeline(
    request: ProfileRequest,
    *,
    workspace_root: Path | None = None,
) -> ProfileResponse:
    """Profile tabular data (CSV/inline) or validate an existing Graph B profile descriptor."""
    root = Path(workspace_root or Path.cwd()).resolve()

    # Case A: Source data profiling (CSV file or inline content)
    if (request.source_path is not None and request.source_path.strip()) or (
        request.file_content is not None and request.file_content.strip()
    ):
        tmp_csv_path: Path | None = None
        if request.file_content is not None and request.file_content.strip():
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_csv_file:
                tmp_csv_file.write(request.file_content.encode("utf-8"))
                tmp_csv_file.flush()
                tmp_csv_path = Path(tmp_csv_file.name)
            csv_path = tmp_csv_path
            display_source_path = request.client_path or "inline:source.csv"
        else:
            assert request.source_path is not None
            csv_path = resolve_workspace_path(
                request.source_path, workspace_root=root, allow_temp=True
            )
            if not csv_path.exists():
                raise InputValidationError(f"Source data file not found: {csv_path}")
            display_source_path = str(csv_path)

        source_system_raw = request.source_system or "bronze_source"
        source_sys_code = (
            re.sub(r"[^A-Z0-9_]+", "_", source_system_raw.upper()).strip("_") or "BRONZE_SOURCE"
        )
        table_name_raw = request.table_name
        resolved_table_name = _sanitize_sql_name(table_name_raw or csv_path.stem)
        domain_ns = _sanitize_domain_ns(request.project_id or source_system_raw)

        try:
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
        finally:
            if tmp_csv_path is not None and tmp_csv_path.exists():
                with contextlib.suppress(OSError):
                    tmp_csv_path.unlink(missing_ok=True)

        graph_b_doc = profile_obj.model_dump(mode="json", exclude_none=True)
        validate_graph_b(graph_b_doc)

        total_cols = sum(
            len(t.columns) for sys_item in profile_obj.source_systems for t in sys_item.tables
        )
        total_tables = sum(len(sys_item.tables) for sys_item in profile_obj.source_systems)

        res_dict: dict[str, Any] = {
            "status": "PASS",
            "profile_id": f"{domain_ns}_profile",
            "source_system": source_sys_code,
            "table_name": resolved_table_name,
            "table_count": total_tables,
            "column_count": total_cols,
            "source_path": display_source_path,
            "graph_b_profile": graph_b_doc,
        }
        rendered_text = render_profile_text(res_dict)
        res_dict["rendered"] = RenderedOutput(text=rendered_text)
        return ProfileResponse(**res_dict)

    # Case B: Validation of pre-existing Graph B profile
    if request.graph_b_content is not None or (
        request.graph_b_path is not None and request.graph_b_path.strip()
    ):
        if request.graph_b_content is not None:
            graph_b_doc = request.graph_b_content
            display_path = "inline:graph_b.json"
        else:
            assert request.graph_b_path is not None
            graph_b_path = resolve_workspace_path(
                request.graph_b_path, workspace_root=root, allow_temp=True
            )
            if not graph_b_path.exists():
                raise InputValidationError(f"Graph B profile file not found: {graph_b_path}")
            graph_b_doc = json.loads(graph_b_path.read_text(encoding="utf-8"))
            display_path = str(graph_b_path)

        validate_graph_b(graph_b_doc)
        tables: list[dict[str, Any]] = list(graph_b_doc.get("tables", []))
        for sys_item in graph_b_doc.get("source_systems", []):
            if isinstance(sys_item, dict):
                tables.extend(t for t in sys_item.get("tables", []) if isinstance(t, dict))
        total_cols = sum(len(t.get("columns", [])) for t in tables if isinstance(t, dict))

        source_sys = request.source_system or "UNKNOWN_SOURCE"
        table_name = request.table_name or (tables[0].get("table_name", "") if tables else "")

        res_dict = {
            "status": "PASS",
            "profile_id": (
                graph_b_doc.get("profile_id")
                or graph_b_doc.get("domain_namespace")
                or "graph_b_profile"
            ),
            "source_system": source_sys,
            "table_name": table_name,
            "table_count": len(tables),
            "column_count": total_cols,
            "source_path": display_path,
            "graph_b_profile": graph_b_doc,
        }
        rendered_text = render_profile_text(res_dict)
        res_dict["rendered"] = RenderedOutput(text=rendered_text)
        return ProfileResponse(**res_dict)

    raise InputValidationError(
        "Either source_path, file_content, or graph_b_path must be provided."
    )


__all__ = ["profile_pipeline"]
