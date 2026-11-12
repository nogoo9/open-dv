"""Unit and integration tests for hold resolution and ontology delta application (ONT-04)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from opendv_contracts.dtos.ontology import (
    AcceptOntologyDeltaOption,
    AddConceptOp,
    OntologyDelta,
    RejectOntologyDeltaOption,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_service.models import CaptainHoldDecisionRequest, RunStatus
from opendv_service.ontology import compute_ontology_sha256, resolve_ontology_hold
from opendv_service.state import StateStore
from opendv_telemetry import ContentAddressableStore


@pytest.fixture
def base_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="logistics_and_commerce",
        enterprise_owner="Enterprise Architecture Council",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="CBC_CUSTOMER",
                elm_archetype="WHO_PARTY",
                definition="Enterprise B2B or B2C customer counterparty.",
                target_hub="hub_customer",
            )
        ],
        canonical_relationships=[],
    )


@pytest.fixture
def sample_delta(base_ontology: GraphAOntology) -> OntologyDelta:
    base_sha = compute_ontology_sha256(base_ontology)
    return OntologyDelta(
        delta_id="delta-s3-supplier",
        parent_sha256=base_sha,
        domain_namespace="logistics_and_commerce",
        operations=[
            AddConceptOp(
                op_id="op-add-supplier-delivery",
                concept_uri="urn:ontology:entity:supplier_delivery",
                concept_name="CBC_SUPPLIER_DELIVERY",
                elm_archetype="WHAT_RESOURCE_EVENT",
                definition="Supplier delivery inbound freight consignment.",
                target_hub="hub_supplier_delivery",
            )
        ],
    )


def test_resolve_ontology_hold_accept(
    base_ontology: GraphAOntology, sample_delta: OntologyDelta
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        cas = ContentAddressableStore(Path(tmp_dir) / "cas")
        opt = AcceptOntologyDeltaOption(
            delta=sample_delta,
            delta_sha256="c" * 64,
            steward_note="Approved supplier delivery CBC addition",
            accepted_op_ids=["op-add-supplier-delivery"],
        )

        res = resolve_ontology_hold(
            base_ontology=base_ontology,
            option=opt,
            run_id="run-test-01",
            hold_id="hold-g1-01",
            steward_id="lead-steward",
            cas_store=cas,
        )

        assert res.status == "ACCEPTED"
        assert res.action == "APPROVE"
        assert res.resumed_stage == "STAGE_03_SHIP_VAULT_PASS1"
        assert res.new_ontology is not None
        assert res.new_ontology.ontology_version == "1.1.0"
        assert res.new_ontology.parent_sha256 == compute_ontology_sha256(base_ontology)
        assert len(res.new_ontology.canonical_business_concepts) == 2

        # Check CAS storage
        assert res.new_graph_a_sha256 is not None
        assert cas.has(res.new_graph_a_sha256)
        cas_data = cas.get_bytes(res.new_graph_a_sha256)
        assert len(cas_data) > 0

        # Check OntologyDecision
        dec = res.decision
        assert dec.action == "ACCEPT"
        assert dec.hold_id == "hold-g1-01"
        assert dec.decided_by == "lead-steward"
        assert dec.accepted_op_ids == ["op-add-supplier-delivery"]
        assert dec.resulting_ontology_sha256 == res.new_graph_a_sha256


def test_resolve_ontology_hold_invalid_delta_raises(base_ontology: GraphAOntology) -> None:
    # Delta with provisional URI should fail validation under ONT-EVO-002
    invalid_delta = OntologyDelta(
        delta_id="delta-invalid-01",
        parent_sha256=compute_ontology_sha256(base_ontology),
        domain_namespace="logistics_and_commerce",
        operations=[
            AddConceptOp(
                op_id="op-provisional",
                concept_uri="urn:ontology:provisional:entity:temp",
                concept_name="CBC_TEMP",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="Temporary provisional concept",
                target_hub="hub_temp",
            )
        ],
    )
    opt = AcceptOntologyDeltaOption(delta=invalid_delta, delta_sha256="d" * 64)

    with pytest.raises(ValueError) as exc:
        resolve_ontology_hold(
            base_ontology=base_ontology,
            option=opt,
            run_id="run-test-01",
            hold_id="hold-g1-01",
        )
    assert "Cannot accept invalid ontology delta" in str(exc.value)


def test_resolve_ontology_hold_reject(base_ontology: GraphAOntology) -> None:
    opt = RejectOntologyDeltaOption(
        reason="Does not meet domain standards",
        steward_note="Refactor into existing customer attributes.",
    )

    res = resolve_ontology_hold(
        base_ontology=base_ontology,
        option=opt,
        run_id="run-test-01",
        hold_id="hold-g1-01",
        steward_id="lead-steward",
    )

    assert res.status == "REJECTED"
    assert res.action == "REJECT"
    assert res.new_ontology is None
    assert res.resumed_stage == ""
    assert res.decision.action == "REJECT"
    assert res.decision.hold_id == "hold-g1-01"


def test_state_store_resolve_captain_hold_with_hold_option(
    base_ontology: GraphAOntology, sample_delta: OntologyDelta
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = StateStore(repo_root=Path(tmp_dir), state_dir=Path(tmp_dir) / "state")
        project_id = "proj-test-s3"

        # Setup base ontology file
        ont_path = Path(tmp_dir) / "graph_a_ontology.yaml"
        import yaml

        ont_path.write_text(
            yaml.safe_dump(base_ontology.model_dump(mode="json", exclude_none=True)),
            encoding="utf-8",
        )

        from opendv_service.models import CreateProjectRequest, CreateRunRequest

        store.create_project(
            CreateProjectRequest(
                project_id=project_id,
                display_name="S3 Logistics",
                domain_namespace="logistics_and_commerce",
                graph_a_ontology_path=str(ont_path),
            )
        )

        run = store.create_run(
            CreateRunRequest(
                project_id=project_id,
            )
        )
        run_id = run.run_id
        store.runs[run_id] = store.runs[run_id].model_copy(
            update={
                "status": RunStatus.PAUSED_HOLD,
                "gate_scorecard": store.runs[run_id].gate_scorecard.model_copy(
                    update={"gate_1_contract_grain": "ASK-USER"}
                ),
            }
        )

        hold_mgr = store.hold_managers[run_id]
        hold_rec = hold_mgr.record_hold(
            run_id=run_id,
            task_id="task-g1-hold",
            session_id="sess-g1",
            stage_id="STAGE_05_AXI_DV_STATIC_GATES",
            persona="axi-dv",
            headline="Gate 1 ontology concept gap",
            escalation_reason="UNMAPPED_ONTOLOGY_CONCEPT",
            rule_id="DV-G4-001",
            options=[],
        )

        opt = AcceptOntologyDeltaOption(
            delta=sample_delta,
            delta_sha256="d" * 64,
            steward_note="Approved S3 delivery concept",
        )
        req = CaptainHoldDecisionRequest(
            action="APPROVE",
            steward_id="architect",
            resolution_notes="Approved S3 delivery concept",
            hold_option=opt,
        )

        resp = store.resolve_captain_hold(run_id=run_id, req=req, hold_id=hold_rec.decision_id)

        assert resp.updated_run_status == RunStatus.RUNNING
        assert resp.next_stage_id == "STAGE_03_SHIP_VAULT_PASS1"
        assert store.runs[run_id].gate_scorecard.gate_1_contract_grain in {"AUTO_REPAIRED", "PASS"}

        # Verify project state has updated graph_a_sha256
        updated_proj = store.projects[project_id]
        assert updated_proj.graph_a_sha256 != compute_ontology_sha256(base_ontology)
        assert store.cas_store.has(updated_proj.graph_a_sha256)


def test_r3_execute_ontology_decision_strictly_scopes_legacy_ip1_cold_bootstrap(
    base_ontology: GraphAOntology,
) -> None:
    """P06b MAJ-01: execute_ontology_decision allows 1.0.0 -> 1.0.0 BOOTSTRAP ONLY for genuine
    Wave-1 IP-1 cold-start payloads (no parent_sha256, no v2 envelope author/created_at/rationale,
    and only the 7 Wave-1 v1 additive op types), rejecting Studio v2 spoofed bootstrap deltas.
    """
    import yaml

    from opendv_contracts.dtos.ontology import OntologyDecisionRequest
    from opendv_service.errors import InputValidationError
    from opendv_service.models import CreateProjectRequest
    from opendv_service.use_cases.ontology import execute_ontology_decision

    with tempfile.TemporaryDirectory() as tmp_dir:
        store = StateStore(repo_root=Path(tmp_dir), state_dir=Path(tmp_dir) / "state")
        ont_path = Path(tmp_dir) / "graph_a_ontology.yaml"
        ont_path.write_text(
            yaml.safe_dump(base_ontology.model_dump(mode="json", exclude_none=True)),
            encoding="utf-8",
        )
        store.create_project(
            CreateProjectRequest(
                project_id="proj-ip1-r3",
                display_name="IP1 Cold Start",
                domain_namespace="logistics_and_commerce",
                graph_a_ontology_path=str(ont_path),
            )
        )

        # 1. Genuine Wave-1 IP-1 cold-start bootstrap succeeds
        genuine_ip1_delta = {
            "delta_id": "delta-ip1-genuine",
            "base_ontology_version": "1.0.0",
            "target_ontology_version": "1.0.0",
            "metadata": {"mode": "BOOTSTRAP"},
            "operations": [
                {
                    "op_id": "op-warehouse",
                    "op_type": "add_concept",
                    "concept_uri": "urn:ontology:entity:warehouse",
                    "concept_name": "CBC_WAREHOUSE",
                    "target_hub": "hub_warehouse",
                }
            ],
        }
        resp = execute_ontology_decision(
            store,
            OntologyDecisionRequest(
                action="ACCEPT",
                decided_by="captain",
                base_ontology_path=str(ont_path),
                delta=genuine_ip1_delta,
            ),
            workspace_root=Path(tmp_dir),
        )
        assert resp.status == "COMPLETED"
        assert resp.resulting_ontology_version == "1.0.0"

        # 2. Spoofed BOOTSTRAP with Studio v2 envelope field (`rationale`) is rejected
        spoofed_v2_author = dict(genuine_ip1_delta)
        spoofed_v2_author["delta_id"] = "delta-spoof-author"
        spoofed_v2_author["rationale"] = "Spoofed studio v2 rationale"
        spoofed_v2_author["operations"] = [
            {
                "op_id": "op-spoof",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:spoof_a",
                "concept_name": "Spoof A",
                "target_hub": "hub_spoof_a",
            }
        ]
        with pytest.raises(InputValidationError):
            execute_ontology_decision(
                store,
                OntologyDecisionRequest(
                    action="ACCEPT",
                    decided_by="captain",
                    base_ontology_path=str(ont_path),
                    delta_content=yaml.safe_dump(spoofed_v2_author),
                ),
                workspace_root=Path(tmp_dir),
            )

        # 3. Spoofed BOOTSTRAP containing a Studio v2 non-Wave-1 op (`add_synonym`) is rejected
        spoofed_v2_op = {
            "delta_id": "delta-spoof-v2op",
            "base_ontology_version": "1.0.0",
            "target_ontology_version": "1.0.0",
            "metadata": {"mode": "BOOTSTRAP"},
            "operations": [
                {
                    "op_id": "op-syn",
                    "op_type": "add_synonym",
                    "concept_uri": "urn:ontology:entity:customer",
                    "synonym": "Buyer",
                }
            ],
        }
        with pytest.raises(InputValidationError, match=r"Target version '1\.0\.0' is invalid"):
            execute_ontology_decision(
                store,
                OntologyDecisionRequest(
                    action="ACCEPT",
                    decided_by="captain",
                    base_ontology_path=str(ont_path),
                    delta_content=yaml.safe_dump(spoofed_v2_op),
                ),
                workspace_root=Path(tmp_dir),
            )
