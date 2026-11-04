"""Unit tests for opendv-contracts DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts.dtos import (
    ArtifactMetadataResponse,
    CompileRequest,
    CompileResponse,
    CreateProjectRequest,
    EventType,
    GateScorecardSummary,
    GateVerdict,
    HoldAction,
    HoldDecisionRequest,
    HoldOptionCandidate,
    HoldResponse,
    HoldStatus,
    PipelineEvent,
    PipelineRunHandle,
    ProfileRequest,
    RenderedOutput,
    RuleEvaluationDTO,
    RunStatus,
    SandboxBatchResult,
    SandboxQueryResponse,
    VerifyRequest,
    VerifyResponse,
)


def test_base_contract_model_immutability_and_forbid_extra() -> None:
    req = ProfileRequest(source_system="ERP_SYS", table_name="customers")
    with pytest.raises(ValidationError):
        # Frozen model should reject attribute mutation
        req.source_system = "NEW_SYS"  # type: ignore

    with pytest.raises(ValidationError):
        # Forbid extra should reject undeclared attributes
        ProfileRequest(source_system="ERP_SYS", undeclared_field="illegal")  # type: ignore


def test_decision_d1_inline_and_file_paths() -> None:
    # ProfileRequest accepts file_content and client_path (D1)
    req = ProfileRequest(
        file_content="cust_id,cust_name\n1,Alice\n2,Bob",
        client_path="C:/data/cust.csv",
        source_system="RAW_CSV",
    )
    assert req.file_content is not None
    assert req.client_path == "C:/data/cust.csv"

    # CompileRequest accepts contract_content (D1)
    compile_req = CompileRequest(contract_content="version: 1.0.0\nentities: []")
    assert compile_req.contract_content is not None

    # VerifyRequest accepts contract_content, graph_a_content, graph_b_content (D1)
    verify_req = VerifyRequest(
        contract_content="version: 1.0.0",
        graph_a_content="ontology: test",
        graph_b_content='{"tables": []}',
    )
    assert verify_req.contract_content == "version: 1.0.0"


def test_decision_d2_async_only_pipeline_runs() -> None:
    handle = PipelineRunHandle(
        run_id="run-1234",
        status=RunStatus.RUNNING,
        created_at_utc="2026-11-04T12:00:00Z",
        event_stream_url="/api/v1/runs/run-1234/events",
        status_url="/api/v1/runs/run-1234",
    )
    assert handle.run_id == "run-1234"
    assert handle.status == RunStatus.RUNNING
    assert handle.event_stream_url == "/api/v1/runs/run-1234/events"


def test_decision_d3_purged_legacy_identifiers() -> None:
    # Assert workbench_view_id and viz_id DO NOT exist on models
    assert "workbench_view_id" not in PipelineEvent.model_fields
    assert "viz_id" not in PipelineEvent.model_fields
    assert "workbench_view_id" not in PipelineRunHandle.model_fields
    assert "viz_id" not in PipelineRunHandle.model_fields
    assert "workbench_view_id" not in VerifyResponse.model_fields
    assert "viz_id" not in VerifyResponse.model_fields

    event = PipelineEvent(
        event_id="evt-1",
        run_id="run-1",
        timestamp_utc="2026-11-04T12:00:00Z",
        stage_id="STAGE_01_INGESTION",
        event_type=EventType.STAGE_STARTED,
        payload={"tables_discovered": 3},
    )
    assert not hasattr(event, "workbench_view_id")
    assert not hasattr(event, "viz_id")


def test_decisions_d6_d7_pre_rendered_output() -> None:
    rendered = RenderedOutput(
        toon="TOON: stage=compile status=PASS hubs=2 links=1",
        text="Compilation Summary Table:\nHubs: 2\nLinks: 1",
        summary="Compiled 2 hubs and 1 link",
    )
    resp = CompileResponse(
        run_id="run-compile-1",
        status="PASS",
        canonical_ir_sha256="a" * 64,
        hubs_compiled=2,
        links_compiled=1,
        rendered=rendered,
    )
    assert resp.rendered is not None
    assert resp.rendered.summary == "Compiled 2 hubs and 1 link"


def test_verify_response_scorecard() -> None:
    scorecard = GateScorecardSummary(
        gate_0_profile_drift=GateVerdict.PASS,
        gate_1_contract_grain=GateVerdict.AUTO_REPAIRED,
        gate_2_sqlglot_ast=GateVerdict.PASS,
        gate_3_networkx_dag=GateVerdict.PASS,
        gate_4_yagni_cost_guard=GateVerdict.AUTO_FIX,
        gate_5_duckdb_sandbox=GateVerdict.PASS,
    )
    eval_item = RuleEvaluationDTO(
        rule_id="DV-GRAIN-001",
        gate_id="G1",
        verdict=GateVerdict.AUTO_REPAIRED,
        message="Primary grain auto-repaired to include tenant_id",
        entity_ref="hub_customer",
    )
    resp = VerifyResponse(
        run_id="run-v-1",
        status="AUTO-FIX",
        verdict=GateVerdict.AUTO_REPAIRED,
        overall_passed=True,
        applied_codemods=["CODEMOD-GRAIN-01"],
        evaluations=[eval_item],
        gate_scorecard=scorecard,
    )
    assert resp.overall_passed is True
    assert resp.gate_scorecard.gate_1_contract_grain == GateVerdict.AUTO_REPAIRED
    assert len(resp.evaluations) == 1


def test_sandbox_query_dtos() -> None:
    batch = SandboxBatchResult(
        batch_number=1,
        batch_name="Batch 1 Initial Inserts",
        passed=True,
        row_count=100,
        delta_rows=100,
    )
    resp = SandboxQueryResponse(
        run_id="run-sb-1",
        query_type="ADVERSARIAL_BATCHES_1_TO_4",
        assertion_cutoff_ts="2026-11-15 23:59:59",
        all_batches_passed=True,
        batches=[batch],
        dynamic_rules_passed=15,
        dynamic_rules_total=15,
    )
    assert resp.all_batches_passed is True
    assert len(resp.batches) == 1
    assert resp.dynamic_rules_passed == 15


def test_hold_dtos() -> None:
    candidate = HoldOptionCandidate(
        candidate_id="cand-1",
        label="Accept composite key (company_id, deal_id)",
        score=0.95,
    )
    resp = HoldResponse(
        hold_id="hold-101",
        run_id="run-h-1",
        stage_id="STAGE_02_GRAIN",
        event_id="evt-42",
        status=HoldStatus.PENDING,
        reason="Ambiguous candidate grain keys discovered",
        candidates=[candidate],
        created_at_utc="2026-11-04T12:00:00Z",
    )
    assert resp.status == HoldStatus.PENDING
    assert len(resp.candidates) == 1

    decision = HoldDecisionRequest(
        run_id="run-h-1",
        hold_id="hold-101",
        action=HoldAction.APPROVE,
        selected_candidate_id="cand-1",
        resolution_notes="Composite key approved by finance steward",
        steward_id="steward-alice",
    )
    assert decision.action == HoldAction.APPROVE


def test_project_and_artifact_dtos() -> None:
    proj = CreateProjectRequest(
        project_id="proj-apex",
        name="Apex Private Equity",
        description="Demo project for PE DV 2.0 modeling",
    )
    assert proj.project_id == "proj-apex"

    art = ArtifactMetadataResponse(
        artifact_id="art-1",
        run_id="run-1",
        artifact_type="canonical_ir",
        filename="canonical-ir.json",
        size_bytes=1024,
        sha256="b" * 64,
        created_at_utc="2026-11-04T12:00:00Z",
    )
    assert art.size_bytes == 1024
