"""Gate G1/G2/G3/G4 verification scorecard and theorem endpoints (`/api/v1/runs/{run_id}/gates`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, status

from opendv_axi_verifier import run_axi_dv_verification_pipeline
from opendv_platform_api.deps import (
    PlatformStateContainer,
    RunStatus,
    build_gate_scorecard_from_axi_result,
    get_platform_state,
)

router = APIRouter(prefix="/runs/{run_id}/gates", tags=["gates"])


@router.get("")
async def get_run_gates(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> dict[str, Any]:
    """Return Gate G1/G2/G3/G4 scorecard and theorem proof report for a run."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    report = state.run_gate_reports.get(run_id)
    if report is None:
        report = state._build_default_gate_report(run_id)
        state.run_gate_reports[run_id] = report
    return report


@router.post("")
async def evaluate_run_gates(
    run_id: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Trigger or re-evaluate Gate G1/G2/G3/G4 verification for a run."""
    if run_id not in state.runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    run_dto = state.runs[run_id]
    project = state.projects.get(run_dto.project_id)
    options = payload or {}
    contract_rel = str(
        options.get("contract_path")
        or (project.contract_path if project is not None else None)
        or "examples/private_equity/dv_pe_model_contract.yaml"
    )
    graph_a_rel = str(
        options.get("graph_a_ontology_path")
        or (project.graph_a_ontology_path if project is not None else None)
        or "examples/private_equity/pe_graph_a_ontology.yaml"
    )
    graph_b_rel = str(
        options.get("graph_b_path")
        or (project.graph_b_path if project is not None and project.graph_b_path else None)
        or "examples/private_equity/pe_graph_b_profile.json"
    )
    contract_path = state._resolve_repo_path(contract_rel)
    graph_a_path = state._resolve_repo_path(graph_a_rel)
    graph_b_path = state._resolve_repo_path(graph_b_rel)
    if not contract_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract file not found: {contract_rel}",
        )
    contract_doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    graph_a_doc = (
        yaml.safe_load(graph_a_path.read_text(encoding="utf-8")) if graph_a_path.exists() else {}
    )
    graph_b_doc = (
        json.loads(graph_b_path.read_text(encoding="utf-8")) if graph_b_path.exists() else {}
    )
    axi_result = run_axi_dv_verification_pipeline(
        contract_doc,
        graph_a_doc,
        graph_b_doc,
        run_id=run_id,
    )
    overall_passed, verdict, scorecard = build_gate_scorecard_from_axi_result(axi_result)
    updated_updates: dict[str, Any] = {"gate_scorecard": scorecard}
    if not overall_passed:
        updated_updates["status"] = RunStatus.FAILED
    state.runs[run_id] = run_dto.model_copy(update=updated_updates)

    base_report = state._build_default_gate_report(run_id)
    base_report["evaluated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_report["overall_passed"] = overall_passed
    base_report["verdict"] = verdict
    base_report["axi_pipeline_status"] = axi_result.status
    base_report["diagnostics"] = list(axi_result.diagnostics)
    base_report["toon_text"] = axi_result.toon_text
    base_report["applied_codemods"] = list(axi_result.applied_codemods)
    base_report["requested_options"] = options
    state.run_gate_reports[run_id] = base_report
    return base_report
