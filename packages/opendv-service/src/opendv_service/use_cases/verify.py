"""Verify pipeline use case: 6-Gate AXI symbolic verification and scorecard derivation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from opendv_axi_verifier import AxiDvPipelineResult, run_axi_dv_verification_pipeline
from opendv_contracts import (
    GateScorecardSummary,
    GateVerdict,
    RenderedOutput,
    VerifyRequest,
    VerifyResponse,
)
from opendv_core.paths import resolve_workspace_path
from opendv_service.errors import InputValidationError, NotFoundError
from opendv_service.rendering import render_verify_text

GateVerdictStatus = str


def build_gate_scorecard_from_axi_result(
    axi_result: AxiDvPipelineResult,
) -> tuple[bool, GateVerdictStatus, GateScorecardSummary]:
    """Derive overall pass boolean, verdict, and 6-Gate scorecard from an AXI pipeline result."""
    passed = axi_result.status in {
        "PASS",
        "AUTO-FIX",
        "AUTO_REPAIRED",
        "ASK-USER",
        "ASK_USER",
    }
    codemods = set(axi_result.applied_codemods)
    if passed:
        scorecard_dict: dict[str, GateVerdictStatus] = {
            "gate_0_profile_drift": (
                "AUTO-FIX" if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in codemods else "PASS"
            ),
            "gate_1_contract_grain": (
                "ASK-USER" if axi_result.status in {"ASK-USER", "ASK_USER"} else "AUTO_REPAIRED"
            ),
            "gate_2_sqlglot_ast": "PASS",
            "gate_3_networkx_dag": "PASS",
            "gate_4_yagni_cost_guard": "AUTO-FIX",
            "gate_5_duckdb_sandbox": "PASS",
        }
    else:
        scorecard_dict = {
            "gate_0_profile_drift": (
                "AUTO-FIX" if "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" in codemods else "PASS"
            ),
            "gate_1_contract_grain": (
                "AUTO_REPAIRED"
                if codemods & {"ALG_A2_ENUM_DEMOTION", "ALG_A4_COLD_START_EXTENSIBILITY"}
                else "PASS"
            ),
            "gate_2_sqlglot_ast": "PASS",
            "gate_3_networkx_dag": (
                "AUTO-FIX" if "ALG_A3_LINK_EFFECTIVITY" in codemods else "PASS"
            ),
            "gate_4_yagni_cost_guard": (
                "AUTO-FIX" if "ALG_A1_3AXIS_SATELLITE_SPLIT" in codemods else "PASS"
            ),
            "gate_5_duckdb_sandbox": "PASS",
        }

    for violation in axi_result.toon_trace.violations:
        if violation.rule.startswith("DV-EVO") or "EVOLUTION" in violation.rule:
            gate_key = "gate_0_profile_drift"
        elif violation.gate in {"G1", "G2"} or violation.rule.startswith("DV-COL"):
            gate_key = "gate_1_contract_grain"
        elif violation.gate == "G6" or violation.rule.startswith("DV-AST"):
            gate_key = "gate_2_sqlglot_ast"
        elif violation.gate == "G3":
            gate_key = "gate_3_networkx_dag"
        elif violation.gate == "G4":
            gate_key = "gate_4_yagni_cost_guard"
        elif violation.gate == "G5":
            gate_key = "gate_5_duckdb_sandbox"
        else:
            gate_key = "gate_1_contract_grain"

        if violation.action == "REJECT":
            scorecard_dict[gate_key] = "REJECT-WITH-TRACE"
        elif violation.action == "ASK-USER" and scorecard_dict[gate_key] != "REJECT-WITH-TRACE":
            scorecard_dict[gate_key] = "ASK-USER"

    if not passed and "REJECT-WITH-TRACE" not in scorecard_dict.values():
        scorecard_dict["gate_1_contract_grain"] = "REJECT-WITH-TRACE"

    scorecard = GateScorecardSummary(**scorecard_dict)
    verdict: GateVerdictStatus
    if not passed:
        verdict = "REJECT-WITH-TRACE"
    elif "ASK-USER" in scorecard_dict.values():
        verdict = "ASK-USER"
    else:
        verdict = "PASS"
    return passed, verdict, scorecard


def verify_pipeline(
    request: VerifyRequest,
    *,
    workspace_root: Path | None = None,
) -> VerifyResponse:
    """Execute 6-Gate symbolic verification across contract, Graph A, and Graph B inputs."""
    root = Path(workspace_root or Path.cwd()).resolve()

    # 1. Resolve contract document
    resolved_contract_path: Path | None = None
    if request.contract_content is not None and request.contract_content.strip():
        contract_doc = yaml.safe_load(request.contract_content)
    elif request.contract_path is not None and request.contract_path.strip():
        resolved_contract_path = resolve_workspace_path(
            request.contract_path, workspace_root=root, allow_temp=True
        )
        if not resolved_contract_path.exists():
            raise NotFoundError(f"Contract file not found: {resolved_contract_path}")
        contract_doc = yaml.safe_load(resolved_contract_path.read_text(encoding="utf-8"))
    else:
        raise InputValidationError("Either contract_path or contract_content must be provided.")

    # 2. Resolve Graph A ontology document (optional)
    graph_a_doc: dict[str, Any] = {}
    if request.graph_a_content is not None and request.graph_a_content.strip():
        graph_a_doc = yaml.safe_load(request.graph_a_content) or {}
    elif request.graph_a_path is not None and request.graph_a_path.strip():
        graph_a_p = resolve_workspace_path(
            request.graph_a_path, workspace_root=root, allow_temp=True
        )
        if not graph_a_p.exists():
            raise NotFoundError(f"Graph A file not found: {graph_a_p}")
        graph_a_doc = yaml.safe_load(graph_a_p.read_text(encoding="utf-8")) or {}

    # 3. Resolve Graph B profile document (optional)
    graph_b_doc: dict[str, Any] = {}
    if request.graph_b_content is not None:
        if isinstance(request.graph_b_content, dict):
            graph_b_doc = request.graph_b_content
        elif isinstance(request.graph_b_content, str) and request.graph_b_content.strip():
            graph_b_doc = json.loads(request.graph_b_content) or {}
    elif request.graph_b_path is not None and request.graph_b_path.strip():
        graph_b_p = resolve_workspace_path(
            request.graph_b_path, workspace_root=root, allow_temp=True
        )
        if not graph_b_p.exists():
            raise NotFoundError(f"Graph B file not found: {graph_b_p}")
        graph_b_doc = json.loads(graph_b_p.read_text(encoding="utf-8")) or {}

    effective_run_id = request.run_id or "run-local-verify"
    axi_res = run_axi_dv_verification_pipeline(
        contract_doc,
        graph_a_doc,
        graph_b_doc,
        run_id=effective_run_id,
    )
    passed, verdict_str, scorecard = build_gate_scorecard_from_axi_result(axi_res)

    verdict_enum = GateVerdict(verdict_str)
    res_dict: dict[str, Any] = {
        "run_id": effective_run_id,
        "status": axi_res.status,
        "verdict": verdict_enum,
        "overall_passed": passed,
        "contract_path": (
            str(resolved_contract_path) if resolved_contract_path else request.contract_path
        ),
        "applied_codemods": list(axi_res.applied_codemods),
        "diagnostics": list(axi_res.diagnostics),
        "gate_scorecard": scorecard,
        "toon_text": axi_res.toon_text,
    }
    rendered_text = render_verify_text(res_dict)
    res_dict["rendered"] = RenderedOutput(text=rendered_text, toon=axi_res.toon_text)
    return VerifyResponse(**res_dict)


__all__ = [
    "build_gate_scorecard_from_axi_result",
    "verify_pipeline",
]
