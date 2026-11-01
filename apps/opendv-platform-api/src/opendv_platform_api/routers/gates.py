"""Gate G1/G2/G3/G4 verification scorecard and theorem endpoints (`/api/v1/runs/{run_id}/gates`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, status

from opendv_axi_verifier import run_axi_dv_verification_pipeline
from opendv_platform_api.deps import PlatformStateContainer, get_platform_state

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
    contract_rel = (
        project.contract_path
        if project is not None
        else "examples/private_equity/dv_pe_model_contract.yaml"
    )
    graph_a_rel = (
        project.graph_a_ontology_path
        if project is not None
        else "examples/private_equity/pe_graph_a_ontology.yaml"
    )
    graph_b_rel = (
        project.graph_b_path
        if project is not None and project.graph_b_path
        else "examples/private_equity/pe_graph_b_profile.json"
    )
    contract_path = state.repo_root / contract_rel
    graph_a_path = state.repo_root / graph_a_rel
    graph_b_path = state.repo_root / graph_b_rel
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
    axi_result = run_axi_dv_verification_pipeline(
        contract_doc,
        graph_a_doc,
        graph_b_doc,
        run_id=run_id,
    )

    base_report = state._build_default_gate_report(run_id)
    base_report["evaluated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_report["axi_pipeline_status"] = axi_result.status
    base_report["requested_options"] = payload or {}
    state.run_gate_reports[run_id] = base_report
    return base_report
