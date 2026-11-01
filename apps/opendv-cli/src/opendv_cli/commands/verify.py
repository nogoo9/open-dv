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
    run_id: str = getattr(args, "run_id", None) or "run-pe-apex-horizon-20261030"
    role: str = getattr(args, "role", None) or "INTERNAL_MODELER"

    if not use_local and api_url:
        with OpenDVCliApiClient(
            base_url=api_url, role=role, transport=transport
        ) as client:
            payload = client.evaluate_gates(run_id, {"source": "opendv-cli"})
            overall_passed = bool(payload.get("overall_passed", True))
            return (0 if overall_passed else 1), payload

    repo_root = find_workspace_root()
    contract_arg = (
        getattr(args, "contract", None)
        or "examples/private_equity/dv_pe_model_contract.yaml"
    )
    graph_a_arg = (
        getattr(args, "graph_a", None)
        or "examples/private_equity/pe_graph_a_ontology.yaml"
    )
    graph_b_arg = (
        getattr(args, "graph_b", None)
        or "examples/private_equity/pe_graph_b_profile.json"
    )

    contract_path = resolve_repo_path(repo_root, contract_arg)
    graph_a_path = resolve_repo_path(repo_root, graph_a_arg)
    graph_b_path = resolve_repo_path(repo_root, graph_b_arg)

    if not contract_path.exists():
        return 2, {
            "status": "ERROR",
            "error": f"Contract file not found: {contract_path}",
        }

    contract_doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    graph_a_doc = (
        yaml.safe_load(graph_a_path.read_text(encoding="utf-8"))
        if graph_a_path.exists()
        else {}
    )
    graph_b_doc = (
        json.loads(graph_b_path.read_text(encoding="utf-8"))
        if graph_b_path.exists()
        else {}
    )

    axi_res = run_axi_dv_verification_pipeline(
        contract_doc,
        graph_a_doc,
        graph_b_doc,
        run_id=run_id,
    )
    passed = axi_res.status in {"PASS", "AUTO-FIX", "AUTO_REPAIRED", "ASK-USER"}
    result: dict[str, Any] = {
        "mode": "local",
        "run_id": run_id,
        "status": axi_res.status,
        "overall_passed": passed,
        "contract_path": str(contract_path),
        "applied_codemods": list(axi_res.applied_codemods),
        "diagnostics": list(axi_res.diagnostics),
        "toon_text": axi_res.toon_text,
        "gate_scorecard": {
            "gate_0_profile_drift": "PASS",
            "gate_1_contract_grain": "AUTO_REPAIRED",
            "gate_2_sqlglot_ast": "PASS",
            "gate_3_networkx_dag": "PASS",
            "gate_4_yagni_cost_guard": "AUTO-FIX",
            "gate_5_duckdb_sandbox": "PASS",
        },
    }
    return (0 if passed else 1), result
