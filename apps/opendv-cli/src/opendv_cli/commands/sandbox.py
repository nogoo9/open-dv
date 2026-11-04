"""`opendv sandbox` command handler (4-Batch DuckDB Shadow Sandbox & Queries D.10 / D.10b)."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_core import find_workspace_root
from opendv_service import OpenDVService, SandboxQueryRequest


def run_sandbox_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute 4-Batch DuckDB sandbox or bi-temporal queries in `--local` or `--api-url` mode."""
    api_url: str | None = getattr(args, "api_url", None)
    use_local: bool = bool(getattr(args, "local", False)) or not api_url
    run_id: str | None = getattr(args, "run_id", None)
    contract_arg: str | None = getattr(args, "contract", None)
    ir_arg: str | None = getattr(args, "canonical_ir", None)
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"
    query_type: str = (
        getattr(args, "query_type", None)
        or getattr(args, "query_id", None)
        or "ADVERSARIAL_BATCHES_1_TO_4"
    )
    cutoff: str = (
        getattr(args, "assertion_cutoff_ts", None)
        or getattr(args, "cutoff", None)
        or "2026-11-15 23:59:59"
    )

    if not use_local and api_url:
        if not run_id:
            return 2, {
                "status": "ERROR",
                "error": "--run-id is required in --api-url mode",
            }
        with OpenDVCliApiClient(base_url=api_url, role=role, transport=transport) as client:
            resp = client.execute_sandbox(
                run_id,
                {
                    "query_type": query_type,
                    "assertion_cutoff_ts": cutoff,
                },
            )
            passed = bool(resp.get("all_batches_passed", True))
            return (0 if passed else 1), resp

    if not run_id and not contract_arg and not ir_arg:
        return 2, {
            "status": "ERROR",
            "error": (
                "At least one of --run-id, --canonical-ir, or --contract is required "
                "in --local mode"
            ),
        }

    repo_root = find_workspace_root()
    effective_run_id = run_id or "run-local-sandbox"

    service = OpenDVService(workspace_root=repo_root)
    try:
        resp = service.execute_sandbox_query(
            SandboxQueryRequest(
                run_id=effective_run_id,
                contract_path=contract_arg,
                canonical_ir_path=ir_arg,
                query_type=query_type,  # type: ignore[arg-type]
                assertion_cutoff_ts=cutoff,
            )
        )
    except Exception as exc:
        return 2, {
            "status": "ERROR",
            "error": str(exc),
        }

    batch_report_dict = {
        "all_passed": resp.all_batches_passed,
        "batches": [b.model_dump(mode="json") for b in resp.batches],
    }
    dyn_report_dict = {
        "passed_count": resp.dynamic_rules_passed,
        "failed_count": resp.dynamic_rules_total - resp.dynamic_rules_passed,
        "checks": resp.rows if resp.query_type == "VERIFY_DYN_001_015" else [],
    }
    result: dict[str, Any] = {
        "mode": "local",
        "run_id": resp.run_id,
        "query_type": resp.query_type,
        "assertion_cutoff_ts": resp.assertion_cutoff_ts,
        "all_batches_passed": resp.all_batches_passed,
        "batch_verdicts": resp.batch_verdicts,
        "batch_report": batch_report_dict,
        "dynamic_check_report": dyn_report_dict,
        "dynamic_rules_passed": resp.dynamic_rules_passed,
        "dynamic_rules_total": resp.dynamic_rules_total,
        "replay_delta_rows": resp.replay_delta_rows,
        "row_count": resp.row_count,
        "rows": resp.rows,
        "query_d10_rows": resp.query_d10_rows,
        "query_d10b_rows": resp.query_d10b_rows,
    }
    return (0 if resp.all_batches_passed else 1), result


__all__ = ["run_sandbox_command"]
