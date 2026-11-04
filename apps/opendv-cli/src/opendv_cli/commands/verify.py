"""`opendv verify` and `axi-dv verify` command handler (6-Gate verification pipeline)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiClient
from opendv_core import find_workspace_root, resolve_workspace_path
from opendv_service import (
    OpenDVService,
    VerifyRequest,
    build_gate_scorecard_from_axi_result,
)


def resolve_repo_path(repo_root: Path, rel_or_abs: str) -> Path:
    """Resolve a repository-relative path using canonical resolve_workspace_path."""
    return resolve_workspace_path(rel_or_abs, workspace_root=repo_root)


def run_verify_command(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute 6-Gate verification in `--local` or `--api-url` mode."""
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
            req_body: dict[str, Any] = {"source": "opendv-cli"}
            if getattr(args, "contract", None):
                req_body["contract_path"] = args.contract
            if getattr(args, "graph_a", None):
                req_body["graph_a_ontology_path"] = args.graph_a
            if getattr(args, "graph_b", None):
                req_body["graph_b_path"] = args.graph_b
            payload = client.evaluate_gates(run_id, req_body)
            overall_passed = bool(payload.get("overall_passed", False))
            return (0 if overall_passed else 1), payload

    contract_arg: str | None = getattr(args, "contract", None)
    if not contract_arg:
        return 2, {
            "status": "ERROR",
            "error": "--contract is required in --local mode",
        }

    repo_root = find_workspace_root()
    graph_a_arg: str | None = getattr(args, "graph_a", None)
    graph_b_arg: str | None = getattr(args, "graph_b", None)

    service = OpenDVService(workspace_root=repo_root)
    try:
        resp = service.verify_pipeline(
            VerifyRequest(
                contract_path=contract_arg,
                graph_a_path=graph_a_arg,
                graph_b_path=graph_b_arg,
                run_id=run_id,
            )
        )
    except Exception as exc:
        return 2, {
            "status": "ERROR",
            "error": str(exc),
        }

    passed = resp.overall_passed
    verdict_str = resp.verdict.value if hasattr(resp.verdict, "value") else str(resp.verdict)
    result: dict[str, Any] = {
        "mode": "local",
        "run_id": resp.run_id,
        "status": resp.status,
        "verdict": verdict_str,
        "overall_passed": passed,
        "contract_path": resp.contract_path,
        "applied_codemods": list(resp.applied_codemods),
        "diagnostics": list(resp.diagnostics),
        "toon_text": resp.toon_text,
        "gate_scorecard": resp.gate_scorecard.model_dump(mode="json"),
    }
    return (0 if passed else 1), result


__all__ = ["build_gate_scorecard_from_axi_result", "resolve_repo_path", "run_verify_command"]
