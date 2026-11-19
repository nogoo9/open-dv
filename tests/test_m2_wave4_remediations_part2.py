"""Regression tests for M2 Wave 4 Review & Challenger remediations (Part 2: Tasks 5-8)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness import (
    AgenticPipelineReport,
    PipelineStepReport,
    StewardHoldManager,
)
from opendv_contracts.dtos.ingestion import TableStructure, TaintClassification
from opendv_contracts.dtos.pipeline import PipelineInputDTO
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.models import GateScorecardSummary, ProjectRunDTO, RunStatus
from opendv_service.runs.engine import RunContext
from opendv_service.seams import set_pipeline_runner_override
from opendv_service.studio.agent_runner_helpers import build_safe_agent_prompt
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.ttl_importer import parse_ontology_import_payload
from opendv_service.use_cases.pipeline import execute_pipeline_run_sync
from opendv_telemetry import VaultLedgerEmitter


def _genesis_blueprint_dict(domain_namespace: str = "custody") -> dict[str, Any]:
    ont = create_genesis_ontology(domain_namespace)
    return {
        "domain_namespace": domain_namespace,
        "ontology": ont.model_dump(mode="json", exclude_none=True),
        "lineage": {
            "domain_namespace": domain_namespace,
            "ontology_sha256": compute_ontology_sha256(ont),
            "blueprint_revision": 1,
        },
    }


@pytest.fixture
def studio_api_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Path, Path, Path]:
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=state_dir, is_server=True)
    app = create_app(settings=settings, workspace_root=ws_root, storage_root=state_dir)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    return client, ws_root, studio_root, state_dir


def test_sse_paused_hold_and_resolve_hold_background_stream(
    studio_api_env: tuple[TestClient, Path, Path, Path],
) -> None:
    """Task 5: PAUSED_HOLD emits event: hold and resolve_captain_hold streams Stages 03-07 live."""
    client, _ws_root, _studio_root, state_dir = studio_api_env
    svc: OpenDVService = client.app.state.service
    run_id = "run-hold-sse-01"

    svc.state.runs[run_id] = ProjectRunDTO(
        run_id=run_id,
        project_id="default-project",
        harness_provider="agy-cli",
        status=RunStatus.PAUSED_HOLD,
        started_at_utc="2026-11-19T00:00:00Z",
        current_stage_id="STAGE_02_SCOUT_UNSTRUCTURED",
        active_hold_ids=["hold-01"],
        gate_scorecard=GateScorecardSummary(),
        events_sse_url=f"/api/v1/runs/{run_id}/events",
        gates_url=f"/api/v1/runs/{run_id}/gates",
        sandbox_url=f"/api/v1/runs/{run_id}/sandbox",
        decisions_url=f"/api/v1/runs/{run_id}/decisions",
    )
    svc.state.run_events[run_id] = [
        {"event_id": "evt-00", "run_id": run_id, "stage_id": "STAGE_00_ORCHESTRATION", "event_type": "stage_started"}
    ]
    ctx = RunContext(run_id=run_id, is_done=True, final_status=RunStatus.PAUSED_HOLD)
    ctx.event_buffer.append(
        {"event_id": f"evt-{run_id}-hold", "run_id": run_id, "event_type": "hold", "payload": {"status": "PAUSED_HOLD"}}
    )
    with svc.run_engine._engine_lock:
        svc.run_engine._contexts[run_id] = ctx

    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp_hold:
        text_hold = "".join(resp_hold.iter_text())
    assert "event: hold" in text_hold
    assert "PAUSED_HOLD" in text_hold
    assert "event: run_complete" not in text_hold

    hm = StewardHoldManager(state_dir=state_dir / run_id)
    hm.record_hold(
        run_id=run_id,
        task_id="t1",
        session_id="s1",
        stage_id="STAGE_02_SCOUT_UNSTRUCTURED",
        persona="vault-first-mate",
        decision_id="hold-01",
        hold_id="hold-01",
        event_id="evt-00",
        headline="Ambiguous entity",
        escalation_reason="TIER_U3",
        rule_id="ALG-A9B",
        options=[],
    )
    svc.state.hold_managers[run_id] = hm
    svc.state.run_decisions[run_id] = []
    svc.state.run_ledgers[run_id] = VaultLedgerEmitter(
        ledger_path=state_dir / "ledgers" / f"{run_id}.jsonl",
        cas_store=svc.state.cas,
    )
    resumable_dir = state_dir / "resumable_runs"
    resumable_dir.mkdir(parents=True, exist_ok=True)
    (resumable_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "input_spec": {
                    "domain_namespace": "custody",
                    "ontology": create_genesis_ontology("custody").model_dump(mode="json"),
                    "sources": [],
                },
                "harness_setup": {},
                "output_dir": str(state_dir / "out"),
                "base_dir": str(state_dir),
            }
        ),
        encoding="utf-8",
    )

    def _delayed_resume_runner(spec_obj: Any, *args: Any, on_stage_complete: Any = None, **kwargs: Any) -> AgenticPipelineReport:
        time.sleep(0.15)
        rep3 = PipelineStepReport(stage_id="STAGE_03_SHIP_VAULT_PASS1", stage_index=3, persona="ship-vault", status="PASS", summary="Pass 1 ok")
        if on_stage_complete:
            on_stage_complete(0, rep3)
        return AgenticPipelineReport(run_id=run_id, status="COMPLETED", overall_passed=True, step_reports=[rep3])

    set_pipeline_runner_override(_delayed_resume_runner)
    try:
        dec = svc.state.resolve_captain_hold(
            run_id,
            hold_id="hold-01",
            decision="APPROVE",
            notes="Go",
        )
        assert dec.updated_run_status == RunStatus.RUNNING
        with client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/events",
            headers={"Last-Event-ID": f"evt-{run_id}-hold"},
        ) as resp_resumed:
            resumed_sse = "".join(resp_resumed.iter_text())
        assert "STAGE_03_SHIP_VAULT_PASS1" in resumed_sse
        assert "event: run_complete" in resumed_sse
        assert "COMPLETED" in resumed_sse
    finally:
        set_pipeline_runner_override(None)


def test_ttl_export_import_lossless_jcs_sha256_parity() -> None:
    """Task 6: Lossless Turtle (.ttl) export + import preserves exact Graph A JCS SHA-256."""
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="ttl_roundtrip_test",
        enterprise_owner="Parity Architecture",
        description="Rich ontology verifying lossless Turtle JCS parity.",
        standards_references=["FIBO-FBC", "FIBO-BE"],
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:zeta_party",
                concept_name="Zeta Party",
                target_hub="hub_zeta_party",
                definition="Party entity.",
                grounding_origin="FIBO_GROUNDED",
                synonyms=["ZetaAlias", "AlphaAlias"],
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/Party",
                        predicate="skos:exactMatch",
                        confidence=0.95,
                        pinned=True,
                        provenance="human",
                    )
                ],
                attributes=[
                    ConceptAttribute(attribute_name="z_ratio", data_type="double", is_optional=False, description="Double metric"),
                    ConceptAttribute(attribute_name="a_code", data_type="string", is_optional=True, description="String code"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:alpha_account",
                concept_name="Alpha Account",
                target_hub="hub_alpha_account",
                definition="Account entity.",
                grounding_origin="STRUCTURED_SCHEMA",
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:zeta_owns_alpha",
                relationship_name="Owns",
                target_link="lnk_zeta_owns_alpha",
                endpoints=["urn:ontology:entity:zeta_party", "urn:ontology:entity:alpha_account"],
                grain_semantics="One Zeta Party owns one or more Alpha Accounts.",
                cardinality="1:N",
                description="Ownership edge.",
            )
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:status_code",
                target_reference_table="ref_status_code",
                enumeration_name="StatusCode",
                description="Account status codes.",
                allowed_codes=["Z_PENDING", "A_ACTIVE"],
                code_descriptions={"Z_PENDING": "Pending approval", "A_ACTIVE": "Active account"},
            )
        ],
    )
    ttl_text, _ = export_ontology(ont, export_format="ttl")
    reimported = parse_ontology_import_payload(ttl_text, import_format="ttl")
    assert compute_ontology_sha256(reimported) == compute_ontology_sha256(ont)


def test_column_pii_flag_masking_step6_session_linkage_and_legacy_version_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tasks 7 & 8: Column pii:true masking, Step 6 session_id linkage, and legacy None version."""
    tbl = TableStructure(
        table_id="tbl-pii-1",
        headers=["acct_id", "customer_tax_ref"],
        rows=[["A100", "TIN-991-0022"]],
        caption="clients",
        taint=TaintClassification.INTERNAL,
        metadata={"columns": [{"column_name": "customer_tax_ref", "pii": True}]},
    )
    prompt_ctx = build_safe_agent_prompt(
        base_ontology=None,
        evidence_chunks=[],
        tables=[tbl],
        clarification_answers={},
        fibo_modules=["FBC"],
        steering_prompt=None,
    )
    assert "TIN-991-0022" not in prompt_ctx
    assert "<MASKED_PII>" in prompt_ctx

    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=tmp_path)
    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path)
    sid = "sess-step6-link"
    mgr.create_session(sid, domain_namespace="custody")

    sjson = mgr.get_session_dir(sid) / "session.json"
    raw_s = json.loads(sjson.read_text(encoding="utf-8"))
    raw_s["wizard_schema_version"] = None
    sjson.write_text(json.dumps(raw_s), encoding="utf-8")

    def _step6_runner(spec_obj: Any, *args: Any, on_stage_complete: Any = None, **kwargs: Any) -> AgenticPipelineReport:
        rep = PipelineStepReport(stage_id="STAGE_07_DETERMINISTIC_COMPILER", stage_index=7, persona="deterministic-compiler", status="PASS", summary="Compiled 5-zone SQL")
        if on_stage_complete:
            on_stage_complete(0, rep)
        return AgenticPipelineReport(run_id=spec_obj.run_id or "run-s6-1", status="COMPLETED", overall_passed=True, step_reports=[rep])

    set_pipeline_runner_override(_step6_runner)
    try:
        dto = PipelineInputDTO(
            session_id=sid,
            blueprint=_genesis_blueprint_dict("custody"),
            sources=[{"feed_id": "f1", "source_system": "crm", "uri": "sqlite:///crm.db"}],
        )
        execute_pipeline_run_sync(state, dto)
    finally:
        set_pipeline_runner_override(None)

    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(tmp_path / "studio"))
    settings = OpenDVSettings.load(workspace_root=tmp_path, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, platform_state=state, workspace_root=tmp_path, storage_root=tmp_path / "state")
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    sess_detail = client.get(f"/api/v1/ontology/studio/sessions/{sid}")
    assert sess_detail.status_code == 200, sess_detail.text
    assert sess_detail.json()["wizard_schema_version"] == 1

    logs_resp = client.get(f"/api/v1/ontology/studio/sessions/{sid}/logs")
    assert logs_resp.status_code == 200, logs_resp.text
    stages = [e["stage_id"] for e in logs_resp.json()["entries"]]
    assert "STAGE_07_DETERMINISTIC_COMPILER" in stages
