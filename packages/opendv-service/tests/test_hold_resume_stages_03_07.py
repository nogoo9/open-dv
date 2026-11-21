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


def test_has_initial_contract_respects_initial_contract_content_with_blueprint(
    tmp_path: Path,
) -> None:
    """Bug 1: has_initial_contract checks initial_contract_content even with blueprint."""
    from opendv_contracts.dtos.pipeline import PipelineInputDTO
    from opendv_core.models.vault_blueprint import create_vault_blueprint
    from opendv_service.runs.options import resolve_run_creation_options
    from opendv_service.use_cases.pipeline import execute_pipeline_run_sync

    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    ont_yaml = (GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml").read_text(encoding="utf-8")
    ont_model = GraphAOntology.model_validate(yaml.safe_load(ont_yaml))
    bp = create_vault_blueprint(
        domain_namespace=ont_model.domain_namespace,
        ontology=ont_model,
        contract=None,
    )
    sample_contract = {
        "contract_version": "5.0",
        "domain_namespace": ont_model.domain_namespace,
        "lifecycle_stage": "COLD_START_N1",
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "customers",
                        "key_columns": ["customer_id"],
                        "record_source_code": "CRM",
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                        "require_src_qualifier": False,
                    }
                ],
            }
        ],
        "links": [],
        "satellites": [],
        "reference_tables": [],
        "unmapped_escalations": [],
        "column_triage_ledger": [
            {
                "bronze_table": "customers",
                "column_name": "customer_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_customer",
                "source_sql_type": "VARCHAR(64)",
            }
        ],
    }
    cnt_yaml = yaml.safe_dump(sample_contract, sort_keys=False)

    # 1. resolve_run_creation_options with input_spec dict containing initial_contract_content
    req_from_dto = PipelineRunRequest(
        input_spec={
            "blueprint": bp.model_dump(mode="json"),
            "initial_contract_content": cnt_yaml,
            "sources": [],
        },
        blueprint=bp.model_dump(mode="json"),
    )
    opts = resolve_run_creation_options(req_from_dto, workspace_root=ws)
    assert opts.has_initial_contract is True
    assert opts.lifecycle_stage == "MULTI_SOURCE_EXPANSION"

    # 2. execute_pipeline_run_sync with PipelineInputDTO(blueprint, initial_contract_content)
    orders_csv = ws / "orders.csv"
    orders_csv.write_text("order_id,customer_id,amount\nO1,C1,42.0\n", encoding="utf-8")
    dto = PipelineInputDTO(
        blueprint=bp.model_dump(mode="json"),
        initial_contract_content=cnt_yaml,
        sources=[
            {
                "feed_id": "src-orders",
                "source_system": "erp",
                "uri": str(orders_csv),
            }
        ],
    )
    with deterministic_pipeline_seam():
        res = execute_pipeline_run_sync(service.state, dto)
    run_id = res.run_id
    run_dto = service.state.runs[run_id]
    assert run_dto.has_initial_contract is True
    assert run_dto.lifecycle_stage == "MULTI_SOURCE_EXPANSION"
    assert res.inference_plan is not None
    assert res.inference_plan.primary_action == "MULTI_SOURCE_EXPANSION"


def test_hold_resume_mirrors_paused_hold_into_state_hold_manager_and_active_hold_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Bug 3b: Resumed PAUSED_HOLD mirrors hold into state.hold_managers & active_hold_ids."""
    from opendv_agent_harness.pipeline.spec import (
        AgenticPipelineReport,
        DeterministicValidationReport,
        PipelineStepReport,
    )
    from opendv_service.state import hold_resume as hr_mod

    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    ont_path = ws / "commerce_graph_a_ontology.yaml"
    shutil.copy2(GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml", ont_path)
    w0_spec = yaml.safe_load((GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    w0_spec["project_id"] = "proj-resume-hold-sync"
    w0_spec["graph_a_ontology_path"] = str(ont_path.resolve())
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

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
    run_id = paused_rep["run_id"]
    pending = service.state.hold_managers[run_id].list_pending_holds(run_id=run_id)
    assert len(pending) >= 1

    def _paused_stage05_runner(spec_obj, setup_obj, **_kw):
        return AgenticPipelineReport(
            run_id=run_id,
            status="PAUSED_HOLD",
            overall_passed=False,
            verdict="HOLD",
            input_validation=DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True),
            output_validation=DeterministicValidationReport(stage="OUTPUT_VALIDATION", passed=True),
            step_reports=[
                PipelineStepReport(
                    stage_index=5,
                    stage_id="STAGE_05_AXI_DV_STATIC_GATES",
                    persona="axi-dv",
                    status="ASK-USER",
                    verdict="HOLD",
                    summary="AXI-DV static gate hold: DV-G4-001",
                )
            ],
            active_holds=[
                {
                    "decision_id": "dec-stage05-001",
                    "hold_id": "dec-stage05-001",
                    "run_id": run_id,
                    "task_id": "task-w0-g1",
                    "session_id": "sess-w0-g1",
                    "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
                    "persona": "axi-dv",
                    "headline": "Stage 05 static gate hold",
                    "escalation_reason": "UNMAPPED_ONTOLOGY_CONCEPT",
                    "rule_id": "DV-G4-001",
                    "options": [],
                    "status": "PENDING",
                }
            ],
        )

    monkeypatch.setattr(hr_mod, "get_default_pipeline_runner", lambda: _paused_stage05_runner)
    service.state.resolve_captain_hold(
        run_id,
        CaptainHoldDecisionRequest(action="APPROVE", steward_id="steward_lead"),
        hold_id=pending[0].decision_id,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if service.state.runs[run_id].status != RunStatus.RUNNING:
            break
        time.sleep(0.05)

    final_run = service.state.runs[run_id]
    assert final_run.status == RunStatus.PAUSED_HOLD
    assert final_run.active_hold_ids == ["dec-stage05-001"]
    assert len(service.state.hold_managers[run_id].list_pending_holds(run_id=run_id)) == 1
    assert len(service.state.pipeline_reports[run_id]["active_holds"]) == 1


def test_resolve_captain_hold_resumes_unmapped_named_feed_without_repausing(
    tmp_path: Path,
) -> None:
    """Approving a PAUSED_HOLD on an 'unmapped'-named CSV resumes Stages 03-07 to COMPLETED."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    csv_path = ws / "ds06_unmapped_feed.csv"
    csv_path.write_text(
        "telemetry_event_id,facility_telemetry,unmodeled_risk_score,sensor_zone_code,recorded_at_utc\n"
        "TEL_1,CHILLER_94C,0.91,BAY_01,2026-11-01T08:15:00Z\n",
        encoding="utf-8",
    )
    ont_path = ws / "empty_ont.yaml"
    ont_path.write_text(
        yaml.safe_dump(
            {
                "ontology_version": "0.1.0",
                "domain_namespace": "telemetry_ops",
                "enterprise_owner": "Enterprise",
                "canonical_business_concepts": [],
                "canonical_relationships": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = {
        "pipeline_id": "pipe-unmapped-resume",
        "project_id": "proj-unmapped-resume",
        "domain_namespace": "telemetry_ops",
        "graph_a_ontology_path": str(ont_path.resolve()),
        "sources": [
            {
                "feed_id": "feed-ds06",
                "source_system": "telemetry",
                "table_name": "ds06_unmapped_feed",
                "data_path": str(csv_path.resolve()),
            }
        ],
    }

    with deterministic_pipeline_seam():
        paused_rep = service.execute_pipeline_run_sync(
            PipelineRunRequest(input_spec=spec, steward_hold_policy="pause")
        )
    assert paused_rep["status"] == "PAUSED_HOLD", paused_rep
    run_id = paused_rep["run_id"]
    pending = service.state.hold_managers[run_id].list_pending_holds(run_id=run_id)
    assert len(pending) == 1

    with deterministic_pipeline_seam():
        resp = service.state.resolve_captain_hold(
            run_id,
            CaptainHoldDecisionRequest(
                action="APPROVE",
                steward_id="steward_lead",
                resolution_notes="Approve unmapped feed onboarding",
            ),
            hold_id=pending[0].decision_id,
        )
        assert resp.updated_run_status == RunStatus.RUNNING
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if service.state.runs[run_id].status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                break
            time.sleep(0.05)

    final_run = service.state.runs[run_id]
    assert final_run.status == RunStatus.COMPLETED, (
        f"Expected COMPLETED after hold APPROVE resume, got {final_run.status}"
    )
