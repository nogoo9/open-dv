"""`opendv compile` command handler (Canonical IR & 4-Zone DuckDB SQL compiler)."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import httpx
import yaml

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_cli.commands.verify import resolve_repo_path
from opendv_core import (
    find_workspace_root,
    validate_canonical_ir,
    validate_contract_v5,
)
from opendv_duckdb_sandbox import (
    compile_canonical_ir_to_4zone_sql,
    lower_contract_to_canonical_ir,
)


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
    contract_path = resolve_repo_path(repo_root, contract_arg)
    if not contract_path.exists():
        return 2, {
            "status": "ERROR",
            "error": f"Contract file not found: {contract_path}",
        }

    contract_doc: dict[str, Any] = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    validate_contract_v5(contract_doc)

    ir_arg: str | None = getattr(args, "canonical_ir", None)
    canonical_ir_path_str: str | None = None
    if ir_arg:
        ir_path = resolve_repo_path(repo_root, ir_arg)
        if not ir_path.exists():
            return 2, {
                "status": "ERROR",
                "error": f"Canonical IR file not found: {ir_path}",
            }
        ir_raw = ir_path.read_text(encoding="utf-8")
        ir_doc: dict[str, Any] = json.loads(ir_raw)
        ir_obj = validate_canonical_ir(ir_doc)
        canonical_ir_path_str = str(ir_path)
    else:
        ir_obj = lower_contract_to_canonical_ir(contract_doc)
        ir_doc = ir_obj.model_dump(mode="json", exclude_none=True)
        ir_raw = json.dumps(ir_doc, sort_keys=True)

    compiled_bundle = compile_canonical_ir_to_4zone_sql(
        ir_obj,
        include_batch4_restatement_in_seed=False,
    )
    ir_sha256 = hashlib.sha256(ir_raw.encode("utf-8")).hexdigest().upper()
    hubs = ir_doc.get("hubs", [])
    links = ir_doc.get("links", [])
    satellites = ir_doc.get("satellites", [])

    return 0, {
        "mode": "local",
        "run_id": run_id or "run-local-compile",
        "status": "PASS",
        "contract_path": str(contract_path),
        "canonical_ir_path": canonical_ir_path_str,
        "canonical_ir_sha256": ir_sha256,
        "hubs_compiled": len(hubs),
        "links_compiled": len(links),
        "satellites_compiled": len(satellites),
        "topological_load_order": list(compiled_bundle.topological_load_order),
        "sql_zones": [
            "Zone 0: Bronze & Vault Schemas",
            "Zone 1: Hashed Staging Views (Algorithm A.5)",
            "Zone 2: Raw Vault Hubs, Links, Satellites, and SAL",
            "Zone 3: Business Vault PIT & Ghost Records",
            "Zone 4: Bi-Temporal Queries D.10 & D.10b",
        ],
    }
