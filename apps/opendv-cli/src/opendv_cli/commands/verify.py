"""`opendv verify` and `axi-dv verify` command handler (6-Gate verification pipeline)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from opendv_axi_verifier import run_axi_dv_verification_pipeline
from opendv_cli.api_client import OpenDVCliApiClient
from opendv_core import find_workspace_root
from opendv_platform_api.deps import build_gate_scorecard_from_axi_result


def resolve_repo_path(repo_root: Path, rel_or_abs: str) -> Path:
    """Resolve a path relative to the workspace root or return if absolute."""
    candidate = Path(rel_or_abs)
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


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
    contract_path = resolve_repo_path(repo_root, contract_arg)
    if not contract_path.exists():
        return 2, {
            "status": "ERROR",
            "error": f"Contract file not found: {contract_path}",
        }

    contract_doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    graph_a_arg: str | None = getattr(args, "graph_a", None)
    graph_b_arg: str | None = getattr(args, "graph_b", None)

    graph_a_doc: dict[str, Any] = {}
    if graph_a_arg:
        graph_a_path = resolve_repo_path(repo_root, graph_a_arg)
        if graph_a_path.exists():
            graph_a_doc = yaml.safe_load(graph_a_path.read_text(encoding="utf-8")) or {}

    graph_b_doc: dict[str, Any] = {}
    if graph_b_arg:
        graph_b_path = resolve_repo_path(repo_root, graph_b_arg)
        if graph_b_path.exists():
            graph_b_doc = json.loads(graph_b_path.read_text(encoding="utf-8")) or {}

    effective_run_id = run_id or "run-local-verify"
    axi_res = run_axi_dv_verification_pipeline(
        contract_doc,
        graph_a_doc,
        graph_b_doc,
        run_id=effective_run_id,
    )
    passed, verdict, scorecard = build_gate_scorecard_from_axi_result(axi_res)
    result: dict[str, Any] = {
        "mode": "local",
        "run_id": effective_run_id,
        "status": axi_res.status,
        "verdict": verdict,
        "overall_passed": passed,
        "contract_path": str(contract_path),
        "applied_codemods": list(axi_res.applied_codemods),
        "diagnostics": list(axi_res.diagnostics),
        "toon_text": axi_res.toon_text,
        "gate_scorecard": scorecard.model_dump(mode="json"),
    }
    return (0 if passed else 1), result
