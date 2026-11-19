"""Phase 2 TDD Tests: `resolve_captain_hold` Background Stages `03-07` Resume (Audit #7).

Covers:
1. When a pipeline run pauses on `PAUSED_HOLD` (e.g. `STAGE_02_SCOUT_UNSTRUCTURED` unmapped entity /
   Tier-U3 hold) and `resolve_captain_hold` approves an `AcceptOntologyDeltaOption`, it returns
   `CaptainHoldDecisionResponse` (`updated_run_status=RunStatus.RUNNING`,
   `next_stage_id="STAGE_03_SHIP_VAULT_PASS1"`) synchronously.
2. Immediately launches a background worker that resumes internal Stages `03-07` to `COMPLETED`
   (`current_stage_id == "STAGE_07_DETERMINISTIC_COMPILER"`), registers final contract/IR artifacts,
   updates `pipeline_reports[run_id]`, and emits terminal completion observability events.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import yaml

from opendv_contracts.dtos.ontology import (
    AcceptOntologyDeltaOption,
    AddConceptOp,
    OntologyDelta,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_service import (
    OpenDVService,
    OpenDVSettings,
    PipelineRunRequest,
)
from opendv_service.models import CaptainHoldDecisionRequest, RunStatus
from opendv_service.ontology import compute_ontology_sha256
from opendv_testkit import deterministic_pipeline_seam

REPO_ROOT = Path(__file__).resolve().parents[3]
GC_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_resolve_captain_hold_resumes_stages_03_to_07_in_background_to_completed(
    tmp_path: Path,
) -> None:
    """`resolve_captain_hold` returns `RUNNING` and resumes Stages `03-07` to `COMPLETED`."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    src_ont_path = GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml"
    ont_path = ws / "commerce_graph_a_ontology.yaml"
    shutil.copy2(src_ont_path, ont_path)
    ont_model = GraphAOntology.model_validate(yaml.safe_load(ont_path.read_text(encoding="utf-8")))
    base_sha = compute_ontology_sha256(ont_model)

    w0_spec = yaml.safe_load((GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    w0_spec["project_id"] = "proj-hold-resume"
    w0_spec["graph_a_ontology_path"] = str(ont_path.resolve())
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

    # Include an unmapped unstructured candidate to trigger PAUSED_HOLD in Stage 02
    doc_path = ws / "msa_addendum.txt"
    quote = "Enterprise SLA Penalty applies if settlement exceeds T+2 business days."
    doc_path.write_text(quote, encoding="utf-8")
    w0_spec["unstructured_documents"] = [
        {
            "doc_id": "doc-sla-01",
            "file_path": str(doc_path),
            "content": quote,
            "candidates": [
                {
                    "candidate_id": "cand-sla-01",
                    "entity_name": "SLA_PENALTY",
                    "concept_uri": "urn:ontology:entity:sla_penalty",
                    "verbatim_quote": quote,
                    "char_start": 0,
                    "char_end": len(quote),
                    "confidence_score": 0.72,
                    "er_tier": "Tier-U3",
                }
            ],
        }
    ]

    with deterministic_pipeline_seam():
        paused_rep = service.execute_pipeline_run_sync(
            PipelineRunRequest(input_spec=w0_spec, steward_hold_policy="pause")
        )
    assert paused_rep["status"] == "PAUSED_HOLD"
    run_id = paused_rep["run_id"]
    assert service.state.runs[run_id].status == RunStatus.PAUSED_HOLD

    hold_mgr = service.state.hold_managers[run_id]
    pending = hold_mgr.list_pending_holds(run_id=run_id)
    assert len(pending) >= 1

    delta = OntologyDelta(
        delta_id="delta-sla-penalty",
        parent_sha256=base_sha,
        domain_namespace=ont_model.domain_namespace,
        operations=[
            AddConceptOp(
                op_id="op-add-sla-penalty",
                concept_uri="urn:ontology:entity:sla_penalty",
                concept_name="CBC_SLA_PENALTY",
                elm_archetype="WHAT_RESOURCE_EVENT",
                definition="Contractual settlement SLA penalty clause.",
                target_hub="hub_sla_penalty",
            )
        ],
    )
    req = CaptainHoldDecisionRequest(
        action="APPROVE",
        steward_id="steward_lead",
        resolution_notes="Approved SLA_PENALTY concept for Stage 03-07 resumption",
        hold_option=AcceptOntologyDeltaOption(
            delta=delta,
            delta_sha256="a" * 64,
            steward_note="Approved",
            accepted_op_ids=["op-add-sla-penalty"],
        ),
    )

    with deterministic_pipeline_seam():
        resp = service.state.resolve_captain_hold(run_id, req, hold_id=pending[0].decision_id)
        assert resp.updated_run_status == RunStatus.RUNNING
        assert resp.next_stage_id == "STAGE_03_SHIP_VAULT_PASS1"

        # Wait for background Stages 03-07 worker to finish
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if service.state.runs[run_id].status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                break
            time.sleep(0.05)

    final_run = service.state.runs[run_id]
    assert final_run.status == RunStatus.COMPLETED, (
        f"Expected background resumption to COMPLETED, got {final_run.status}"
    )
    assert final_run.current_stage_id == "STAGE_07_DETERMINISTIC_COMPILER"
    assert service.state.pipeline_reports[run_id]["status"] == "COMPLETED"
    assert any(
        ev.get("stage_id") == "STAGE_07_DETERMINISTIC_COMPILER"
        for ev in service.state.run_events[run_id]
    )
