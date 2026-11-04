"""`opendv compile` command handler (Canonical IR & 4-Zone DuckDB SQL compiler)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_core import find_workspace_root
from opendv_service import CompileRequest, OpenDVService


def run_compile_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Compile DV 2.0 contract into Canonical IR & 4-Zone SQL (`--local` or `--api-url`)."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    run_id: str | None = getattr(args, "run_id", None)
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"

    if not use_local and api_url:
        if not run_id:
            return 2, {
                "status": "ERROR",
                "error": "--run-id is required in --api-url mode",
            }
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            wb11 = client.get_workbench_view(run_id, "WB-11")
            return 0, {
                "mode": "remote",
                "run_id": run_id,
                "status": "PASS",
                "workbench_view": wb11,
            }

    contract_arg: str | None = getattr(args, "contract", None)
    if not contract_arg:
        return 2, {
            "status": "ERROR",
            "error": "--contract is required in --local mode",
        }

    repo_root = find_workspace_root()
    ir_arg: str | None = getattr(args, "canonical_ir", None)

    service = OpenDVService(workspace_root=repo_root)
    try:
        resp = service.compile_pipeline(
            CompileRequest(
                contract_path=contract_arg,
                canonical_ir_path=ir_arg,
                run_id=run_id,
            )
        )
    except Exception as exc:
        return 2, {
            "status": "ERROR",
            "error": str(exc),
        }

    return 0, {
        "mode": "local",
        "run_id": resp.run_id,
        "status": resp.status,
        "contract_path": resp.contract_path,
        "canonical_ir_path": resp.canonical_ir_path,
        "canonical_ir_sha256": resp.canonical_ir_sha256,
        "hubs_compiled": resp.hubs_compiled,
        "links_compiled": resp.links_compiled,
        "satellites_compiled": resp.satellites_compiled,
        "topological_load_order": resp.topological_load_order,
        "sql_zones": resp.sql_zones,
    }


__all__ = ["run_compile_command"]
