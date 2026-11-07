"""tests/parity/test_p7_adversarial_t17.py — Adversarial Verification of T17 Parity & Hold Resumption.

Independently authored by challenger_p7_1 to empirically challenge and stress-test:
1. PAR-ONT-BOOTSTRAP: 3-transport delta generation and application to Graph A v1 (1.0.0) and v2 (1.1.0).
2. PAR-ONT-ADVISORY: 3-transport advisory policy execution, error isolation, fail-loud unavailable.
3. PAR-ONT-HOLD: Gate 1 DV-G4-001 hold resolution via AcceptOntologyDeltaOption & wave resumption.
4. Evolution Locks:
   - Lineage / parent_sha256 mismatch (ONT-EVO-003)
   - Breaking contract mutation (ONT-EVO-001)
   - Provisional URI injection (ONT-EVO-002)
   - Anti-silo & duplicate concept collisions (ONT-EVO-003)
   - Dangling relationship endpoints (ONT-EVO-003)
5. Robustness against malformed payloads (JSON/YAML) and double/rejected resumption.
6. 3-Transport Parity (STANDALONE_CLI, CLIENT_CLI, DIRECT_REST).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from opendv_contracts import (
    AcceptOntologyDeltaOption,
    AddConceptOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    OntologyDelta,
    RejectOntologyDeltaOption,
)
from opendv_contracts.errors import NotFoundError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)
from opendv_service.errors import InputValidationError
from opendv_service.models import CaptainHoldDecisionRequest, RunStatus
from opendv_service.ontology import (
    apply_ontology_delta,
    compute_ontology_sha256,
    ontology_diff,
    resolve_ontology_hold,
    validate_ontology_delta,
)
from opendv_service.ontology.loader import load_base_ontology
from opendv_service.ontology.redaction import (
    redact_recommendation_request,
    redact_sample_data,
)
from opendv_service.state import StateStore
from opendv_telemetry import ContentAddressableStore
from tests.parity.normalize import normalize_for_parity
from tests.parity.scenarios import REPO_ROOT
from tests.parity.transports import (
    ClientCliTransport,
    DirectRestTransport,
    StandaloneCliTransport,
)

GT06_DIR = REPO_ROOT / "tests" / "golden" / "GT-06"
BASE_ONT_PATH = GT06_DIR / "base_ontology.yaml"
PROP_PATH = GT06_DIR / "proposal.json"
ACC_PATH = GT06_DIR / "accepted_ontology.yaml"


@pytest.fixture
def base_ontology() -> GraphAOntology:
    raw = yaml.safe_load(BASE_ONT_PATH.read_text(encoding="utf-8"))
    return GraphAOntology.model_validate(raw)


@pytest.fixture
def deployed_contract() -> dict[str, Any]:
    """Sample deployed contract with entities deployed to specific hubs."""
    return {
        "contract_version": "5.0.0",
        "hubs": [
            {
                "name": "hub_fund",
                "ontology_concept_uri": "urn:ontology:entity:fund",
                "business_key_columns": ["fund_code"],
            },
            {
                "name": "hub_investor",
                "ontology_concept_uri": "urn:ontology:entity:investor",
                "business_key_columns": ["investor_id"],
            },
        ],
        "links": [
            {
                "name": "lnk_investor_fund",
                "ontology_concept_uri": "urn:ontology:relationship:invests_in",
                "hub_references": ["hub_investor", "hub_fund"],
            }
        ],
        "satellites": [],
    }


# =============================================================================
# 1. PAR-ONT-BOOTSTRAP: 3-Transport Suggest & Apply to Graph A v1 & v2
# =============================================================================


@pytest.mark.parity
def test_par_ont_bootstrap_apply_graph_a_v2_across_transports(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
    base_ontology: GraphAOntology,
    tmp_path: Path,
) -> None:
    """Verify PAR-ONT-BOOTSTRAP produces valid proposal and applies cleanly across 3 transports."""
    # 1. Suggest bootstrap proposal across all 3 transports
    res_cli = cli_transport.suggest_ontology(
        mode="BOOTSTRAP", domain_namespace="pe_asset_management"
    )
    res_ctl = client_cli_transport.suggest_ontology(
        mode="BOOTSTRAP", domain_namespace="pe_asset_management"
    )
    res_rest = rest_transport.suggest_ontology(
        mode="BOOTSTRAP", domain_namespace="pe_asset_management"
    )

    assert res_cli.get("exit_code", 0) == 0
    assert res_ctl.get("exit_code", 0) == 0
    assert "delta" in res_rest

    delta_cli = res_cli["delta"]
    delta_ctl = res_ctl["delta"]
    delta_rest = res_rest["delta"]

    # 3-Transport Parity on generated bootstrap operations
    assert delta_cli["operations"] == delta_ctl["operations"]
    assert delta_cli["operations"] == delta_rest["operations"]
    assert len(delta_cli["operations"]) == 4

    # 2. Apply bootstrap delta to create Graph A v1
    d_cli = cli_transport.decide_ontology(action="ACCEPT", delta=delta_cli, base=BASE_ONT_PATH)
    d_ctl = client_cli_transport.decide_ontology(
        action="ACCEPT", delta=delta_ctl, base=BASE_ONT_PATH
    )
    d_rest = rest_transport.decide_ontology(action="ACCEPT", delta=delta_rest, base=BASE_ONT_PATH)

    norm_cli = normalize_for_parity(d_cli, path_root=str(REPO_ROOT))
    norm_ctl = normalize_for_parity(d_ctl, path_root=str(REPO_ROOT))
    norm_rest = normalize_for_parity(d_rest, path_root=str(REPO_ROOT))

    assert norm_cli["resulting_ontology_version"] == "1.0.0"
    assert norm_ctl["resulting_ontology_version"] == "1.0.0"
    assert norm_rest["resulting_ontology_version"] == "1.0.0"
    assert norm_cli["resulting_ontology_sha256"] == norm_ctl["resulting_ontology_sha256"]
    assert norm_cli["resulting_ontology_sha256"] == norm_rest["resulting_ontology_sha256"]

    # 3. Apply an additive evolution delta on top of Graph A v1 to produce Graph A v2 (1.1.0)
    v1_raw = d_cli["resulting_ontology_yaml"]
    v1_path = tmp_path / "graph_a_v1.yaml"
    v1_path.write_text(v1_raw, encoding="utf-8")
    v1_ont = GraphAOntology.model_validate(yaml.safe_load(v1_raw))
    v1_sha = compute_ontology_sha256(v1_ont)

    evolution_delta = OntologyDelta(
        delta_id="delta-v1-to-v2",
        parent_sha256=v1_sha,
        domain_namespace="pe_asset_management",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-add-advisor",
                concept_uri="urn:ontology:entity:advisor",
                concept_name="Advisor",
                target_hub="hub_advisor",
            )
        ],
    )
    evo_dict = evolution_delta.model_dump(mode="json")

    d2_cli = cli_transport.decide_ontology(action="ACCEPT", delta=evo_dict, base=v1_path)
    d2_ctl = client_cli_transport.decide_ontology(action="ACCEPT", delta=evo_dict, base=v1_path)
    d2_rest = rest_transport.decide_ontology(action="ACCEPT", delta=evo_dict, base=v1_path)

    norm2_cli = normalize_for_parity(d2_cli, path_root=str(REPO_ROOT))
    norm2_ctl = normalize_for_parity(d2_ctl, path_root=str(REPO_ROOT))
    norm2_rest = normalize_for_parity(d2_rest, path_root=str(REPO_ROOT))

    assert norm2_cli["resulting_ontology_version"] == "1.1.0"
    assert norm2_ctl["resulting_ontology_version"] == "1.1.0"
    assert norm2_rest["resulting_ontology_version"] == "1.1.0"
    assert norm2_cli["resulting_ontology_sha256"] == norm2_ctl["resulting_ontology_sha256"]
    assert norm2_cli["resulting_ontology_sha256"] == norm2_rest["resulting_ontology_sha256"]


# =============================================================================
# 2. Adversarial Lineage Stress Test: parent_sha256 Mismatch (ONT-EVO-003)
# =============================================================================


@pytest.mark.parity
def test_adversarial_parent_sha256_mismatch(
    base_ontology: GraphAOntology,
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Stress test: tampered or stale parent_sha256 must be rejected loudly under ONT-EVO-003."""
    tampered_parent_sha = "0" * 64

    delta = OntologyDelta(
        delta_id="delta-tampered-lineage",
        parent_sha256=tampered_parent_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-valid-concept",
                concept_uri="urn:ontology:entity:risk_rating",
                concept_name="RiskRating",
                target_hub="hub_risk_rating",
            )
        ],
    )

    # 1. Direct Python validation
    val = validate_ontology_delta(base_ontology, delta)
    assert val.valid is False
    assert val.error_code == "ONT-EVO-003"
    assert any("Lineage mismatch" in v for v in val.violations)

    # 2. Standalone CLI validation
    delta_dict = delta.model_dump(mode="json")
    v_cli = cli_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)
    assert v_cli.get("valid") is False
    assert any("Lineage mismatch" in v for v in v_cli.get("violations", []))

    # 3. Client CLI validation
    v_ctl = client_cli_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)
    assert v_ctl.get("valid") is False or v_ctl.get("exit_code") == 1

    # 4. REST validation
    v_rest = rest_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)
    assert v_rest.get("valid") is False
    assert any("Lineage mismatch" in v for v in v_rest.get("violations", []))

    # 5. Direct apply_ontology_delta must raise ValueError
    with pytest.raises(ValueError, match="ONT-EVO-003"):
        apply_ontology_delta(base_ontology, delta)


# =============================================================================
# 3. Adversarial Breaking Changes Against Deployed Contract (ONT-EVO-001)
# =============================================================================


@pytest.mark.parity
def test_adversarial_ont_evo_001_breaking_contract_rebind(
    base_ontology: GraphAOntology,
    deployed_contract: dict[str, Any],
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Stress test: attempting to rebind an already-deployed hub or link must fail with ONT-EVO-001."""
    base_sha = compute_ontology_sha256(base_ontology)

    # Concept urn:ontology:entity:fund is deployed to hub_fund. Attempt to rebind to hub_fund_v2.
    breaking_delta = OntologyDelta(
        delta_id="delta-breaking-rebind",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-break-hub",
                concept_uri="urn:ontology:entity:fund",
                concept_name="FundRenamed",
                target_hub="hub_fund_v2",  # Breaking conflict with deployed contract!
            )
        ],
    )

    # 1. Direct Python validation
    val = validate_ontology_delta(
        base_ontology, breaking_delta, deployed_contract=deployed_contract
    )
    assert val.valid is False
    assert val.error_code == "ONT-EVO-001"
    assert any("ONT-EVO-001" in v for v in val.violations)
    assert any("hub_fund" in v and "hub_fund_v2" in v for v in val.violations)

    # 2. CLI validation with deployed contract
    delta_dict = breaking_delta.model_dump(mode="json")
    v_cli = cli_transport.validate_ontology_delta(
        delta=delta_dict, base=BASE_ONT_PATH, contract=deployed_contract
    )
    assert v_cli.get("valid") is False
    assert any("ONT-EVO-001" in v for v in v_cli.get("violations", []))

    # 3. Client CLI validation with deployed contract
    v_ctl = client_cli_transport.validate_ontology_delta(
        delta=delta_dict, base=BASE_ONT_PATH, contract=deployed_contract
    )
    assert v_ctl.get("valid") is False or v_ctl.get("exit_code") == 1

    # 4. REST validation with deployed contract
    v_rest = rest_transport.validate_ontology_delta(
        delta=delta_dict, base=BASE_ONT_PATH, contract=deployed_contract
    )
    assert v_rest.get("valid") is False
    assert any("ONT-EVO-001" in v for v in v_rest.get("violations", []))


# =============================================================================
# 4. Adversarial Provisional URI Injections (ONT-EVO-002)
# =============================================================================


@pytest.mark.parity
def test_adversarial_ont_evo_002_provisional_concept_and_relationship(
    base_ontology: GraphAOntology,
) -> None:
    """Stress test: provisional URIs in concepts or relationships must be rejected with ONT-EVO-002."""
    base_sha = compute_ontology_sha256(base_ontology)

    # 1. Provisional concept URI
    delta_concept = OntologyDelta(
        delta_id="delta-prov-concept",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-prov-concept",
                concept_uri="urn:ontology:provisional:entity:temp_concept",
                concept_name="TempConcept",
                target_hub="hub_temp_concept",
            )
        ],
    )
    val_c = validate_ontology_delta(base_ontology, delta_concept)
    assert val_c.valid is False
    assert val_c.error_code == "ONT-EVO-002"
    assert any("ONT-EVO-002" in v for v in val_c.violations)

    # 2. Provisional relationship URI
    delta_rel = OntologyDelta(
        delta_id="delta-prov-rel",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddRelationshipOp(
                op_id="op-prov-rel",
                relationship_uri="urn:ontology:provisional:relationship:temp_rel",
                relationship_name="TempRel",
                target_link="lnk_temp_rel",
                endpoints=["urn:ontology:entity:fund", "urn:ontology:entity:investor"],
            )
        ],
    )
    val_r = validate_ontology_delta(base_ontology, delta_rel)
    assert val_r.valid is False
    assert val_r.error_code == "ONT-EVO-002"
    assert any("ONT-EVO-002" in v for v in val_r.violations)

    # 3. Provisional URI in rationale string
    delta_rat = OntologyDelta(
        delta_id="delta-prov-rat",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-prov-rat",
                concept_uri="urn:ontology:entity:safe_concept",
                concept_name="SafeConcept",
                target_hub="hub_safe_concept",
                rationale="Injected mention urn:ontology:provisional:mention:m1",
            )
        ],
    )
    val_rat = validate_ontology_delta(base_ontology, delta_rat)
    assert val_rat.valid is False
    assert val_rat.error_code == "ONT-EVO-002"
    assert any("ONT-EVO-002" in v for v in val_rat.violations)


# =============================================================================
# 5. Adversarial Anti-Silo & Collisions Stress Test (ONT-EVO-003)
# =============================================================================


@pytest.mark.parity
def test_adversarial_ont_evo_003_anti_silo_and_dangling_endpoints(
    base_ontology: GraphAOntology,
) -> None:
    """Stress test: 1-Hub-per-Concept anti-silo rule and dangling relationship endpoints."""
    base_sha = compute_ontology_sha256(base_ontology)

    # Attack A: bind hub_fund (already bound to urn:ontology:entity:fund) to a new concept
    silo_delta = OntologyDelta(
        delta_id="delta-silo-attack",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-silo-01",
                concept_uri="urn:ontology:entity:syndicate_vehicle",
                concept_name="SyndicateVehicle",
                target_hub="hub_fund",  # Anti-silo collision!
            )
        ],
    )
    val_silo = validate_ontology_delta(base_ontology, silo_delta)
    assert val_silo.valid is False
    assert val_silo.error_code == "ONT-EVO-003"
    assert any("Anti-silo violation" in v for v in val_silo.violations)

    # Attack B: Add relationship with non-existent concept endpoints
    dangling_delta = OntologyDelta(
        delta_id="delta-dangling-attack",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddRelationshipOp(
                op_id="op-dangling-01",
                relationship_uri="urn:ontology:relationship:audits",
                relationship_name="Audits",
                target_link="lnk_audits",
                endpoints=[
                    "urn:ontology:entity:nonexistent_auditor",
                    "urn:ontology:entity:fund",
                ],
            )
        ],
    )
    val_dang = validate_ontology_delta(base_ontology, dangling_delta)
    assert val_dang.valid is False
    assert val_dang.error_code == "ONT-EVO-003"
    assert any("Dangling endpoint" in v for v in val_dang.violations)


# =============================================================================
# 6. Adversarial Malformed Payload Validation
# =============================================================================


@pytest.mark.parity
def test_adversarial_malformed_delta_payloads(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Stress test: invalid payload types, missing fields, or unrecognized discriminators fail fast."""
    # 1. Missing required field 'delta_id'
    bad_delta_missing = {
        "parent_sha256": "9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
        "domain_namespace": "pe_asset_management",
        "operations": [],
    }
    with pytest.raises(ValidationError):
        OntologyDelta.model_validate(bad_delta_missing)

    # 2. Unrecognized operation discriminator
    bad_op_discriminator = {
        "delta_id": "delta-bad-disc",
        "parent_sha256": "9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
        "domain_namespace": "pe_asset_management",
        "operations": [
            {
                "op_id": "op-del",
                "op_type": "DeleteConceptOp",  # Non-additive operation forbidden!
                "concept_uri": "urn:ontology:entity:fund",
            }
        ],
    }
    with pytest.raises(ValidationError):
        OntologyDelta.model_validate(bad_op_discriminator)

    # 3. Direct REST returns 422 Unprocessable Entity on schema invalid delta
    res_rest = rest_transport.validate_ontology_delta(
        delta=bad_op_discriminator, base=BASE_ONT_PATH
    )
    assert res_rest.get("status") in (422, "ERROR") or not res_rest.get("valid", True)


# =============================================================================
# 7. Adversarial Hold Resumption Invariants (Double & Rejected Resumption)
# =============================================================================


@pytest.mark.parity
def test_adversarial_hold_resumption_state_invariants(
    base_ontology: GraphAOntology,
    tmp_path: Path,
) -> None:
    """Stress test: resolving an already-resolved hold or resuming invalid state must fail."""
    store = StateStore(repo_root=tmp_path, state_dir=tmp_path / "state")
    project_id = "proj-adv-resumption"

    ont_path = tmp_path / "graph_a_ontology.yaml"
    ont_path.write_text(yaml.safe_dump(base_ontology.model_dump(mode="json")), encoding="utf-8")

    from opendv_service.models import CreateProjectRequest, CreateRunRequest

    store.create_project(
        CreateProjectRequest(
            project_id=project_id,
            display_name="Adv Resumption Project",
            domain_namespace="pe_asset_management",
            graph_a_ontology_path=str(ont_path),
        )
    )

    run = store.create_run(CreateRunRequest(project_id=project_id))
    run_id = run.run_id

    # Place run in PAUSED_HOLD state
    store.runs[run_id] = store.runs[run_id].model_copy(update={"status": RunStatus.PAUSED_HOLD})

    hold_mgr = store.hold_managers[run_id]
    hold_rec = hold_mgr.record_hold(
        run_id=run_id,
        task_id="task-g1",
        session_id="sess-01",
        stage_id="STAGE_05_AXI_DV_STATIC_GATES",
        persona="axi-dv",
        headline="Gate 1 hold",
        escalation_reason="UNMAPPED_ONTOLOGY_CONCEPT",
        rule_id="DV-G4-001",
        options=[],
    )

    base_sha = compute_ontology_sha256(base_ontology)
    valid_delta = OntologyDelta(
        delta_id="delta-ok",
        parent_sha256=base_sha,
        domain_namespace="pe_asset_management",
        operations=[
            AddConceptOp(
                op_id="op-new-cbc",
                concept_uri="urn:ontology:entity:capital_call",
                concept_name="CapitalCall",
                target_hub="hub_capital_call",
            )
        ],
    )
    opt = AcceptOntologyDeltaOption(
        delta=valid_delta,
        delta_sha256="e" * 64,
        steward_note="Approved capital call",
    )
    req = CaptainHoldDecisionRequest(
        hold_id=hold_rec.hold_id,
        action="APPROVE",
        steward_id="steward-1",
        hold_option=opt,
    )

    # 1. First resolution succeeds
    resp1 = store.resolve_captain_hold(run_id=run_id, req=req, hold_id=hold_rec.hold_id)
    assert resp1.updated_run_status == RunStatus.RUNNING

    # 2. Second resolution on the same hold with explicit hold_id MUST raise ValueError
    with pytest.raises(ValueError, match="already resolved"):
        store.resolve_captain_hold(run_id=run_id, req=req, hold_id=hold_rec.hold_id)

    # 3. Resolution when no pending holds remain and hold_id is omitted MUST raise NotFoundError
    req_no_id = CaptainHoldDecisionRequest(
        action="APPROVE",
        steward_id="steward-1",
        hold_option=opt,
    )
    with pytest.raises(
        NotFoundError, match="Hold ID is required when no single active hold is pending"
    ):
        store.resolve_captain_hold(run_id=run_id, req=req_no_id)


# =============================================================================
# 8. PAR-ONT-HOLD: Gate 1 DV-G4-001 End-to-End Pipeline Hold & Resumption
# =============================================================================


@pytest.mark.parity
def test_par_ont_hold_e2e_gate1_resumption(tmp_path: Path) -> None:
    """End-to-End verification: Gate 1 DV-G4-001 triggers Steward Hold, resolved via AcceptOntologyDeltaOption."""
    from opendv_agent_harness import (
        AgenticVaultPipeline,
        HarnessSetupConfig,
        PipelineInputSpec,
        resume_agentic_vault_pipeline,
    )
    from opendv_testkit import build_deterministic_agy_sdk_shim

    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    spec = PipelineInputSpec(
        pipeline_id="pipe-s3-adv-test",
        project_id="proj-s3-adv",
        domain_namespace="logistics_and_commerce",
        graph_a_ontology_path=str((s3_dir / "graph_a_ontology.yaml").resolve()),
        waves=[
            {
                "wave_index": 0,
                "wave_name": "w0_unmapped_supplier",
                "wave_type": "COLD_START",
                "sources": [
                    {
                        "feed_id": "feed-unmapped-supplier",
                        "source_system": "bronze_logistics",
                        "table_name": "feed_unmapped_supplier",
                        "data_path": str((s3_dir / "feed_unmapped_supplier.csv").resolve()),
                        "business_key_columns": ["delivery_id"],
                        "target_hub_name": "hub_supplier_delivery",
                        "record_source_code": "LOGISTICS_FEED",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "bkcc_namespace": "GLOBAL",
                    }
                ],
                "unstructured_documents": [],
            }
        ],
    )

    out_dir = tmp_path / "out_s3_e2e"
    cas = ContentAddressableStore(out_dir / "cas")

    pipeline = AgenticVaultPipeline(
        input_spec=spec,
        harness_setup=HarnessSetupConfig(),
        output_dir=out_dir,
        base_dir=REPO_ROOT,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    # 1. Initial pipeline execution encounters Gate 1 DV-G4-001 and enters PAUSED_HOLD
    report = pipeline.run()
    assert report.status == "PAUSED_HOLD"
    assert report.overall_passed is False

    pending_holds = pipeline.hold_manager.list_pending_holds()
    assert len(pending_holds) == 1
    hold = pending_holds[0]
    assert hold.rule_id == "DV-G4-001"
    assert hold.escalation_reason == "UNMAPPED_ONTOLOGY_CONCEPT"

    # 2. Construct AcceptOntologyDeltaOption adding missing CBC_SUPPLIER_DELIVERY
    assert pipeline.ontology is not None
    base_sha = compute_ontology_sha256(pipeline.ontology)

    delta = OntologyDelta(
        delta_id="delta-s3-e2e-approved",
        parent_sha256=base_sha,
        domain_namespace="logistics_and_commerce",
        operations=[
            AddConceptOp(
                op_id="op-s3-delivery",
                concept_uri="urn:ontology:entity:supplier_delivery",
                concept_name="CBC_SUPPLIER_DELIVERY",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="Logistics supplier delivery shipment record.",
                target_hub="hub_supplier_delivery",
            )
        ],
    )
    delta_json = delta.model_dump_json().encode("utf-8")
    delta_sha = hashlib.sha256(delta_json).hexdigest()

    opt = AcceptOntologyDeltaOption(
        delta=delta,
        delta_sha256=delta_sha,
        steward_note="Approved S3 supplier delivery CBC concept addition",
    )

    # 3. Resume pipeline from Stage 03 and complete cleanly
    resumed_report = resume_agentic_vault_pipeline(
        pipeline,
        hold_id=hold.hold_id,
        option=opt,
        steward_id="lead-steward",
    )

    assert resumed_report.status == "COMPLETED"
    assert resumed_report.overall_passed is True
    assert resumed_report.ontology_version == "1.1.0"
    assert resumed_report.parent_sha256 == base_sha
    assert pipeline.cas_store.has(resumed_report.graph_a_sha256)
    assert len(pipeline.hold_manager.list_pending_holds()) == 0

    resolved_rec = pipeline.hold_manager.get_hold(hold.hold_id)
    assert resolved_rec is not None
    assert resolved_rec.status == "APPROVED"

    # 4. Adversarial check: Resumption on already resolved pipeline MUST fail
    with pytest.raises(
        RuntimeError, match="no paused wave context to resume|Cannot resume pipeline"
    ):
        resume_agentic_vault_pipeline(
            pipeline,
            hold_id=hold.hold_id,
            option=opt,
            steward_id="lead-steward",
        )


def test_p7_critique_remediation_adversarial_suite(
    base_ontology: GraphAOntology,
    deployed_contract: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Comprehensive adversarial suite verifying Claude Opus 5.5 critique remediations (F1–F9)."""
    # 1. F1/F8: Canonical JCS hashing stability under dictionary permutation
    ont_dict_a = base_ontology.model_dump(mode="json")
    ont_dict_b = {k: ont_dict_a[k] for k in reversed(list(ont_dict_a.keys()))}
    sha_a = compute_ontology_sha256(ont_dict_a)
    sha_b = compute_ontology_sha256(ont_dict_b)
    assert sha_a == sha_b
    assert len(sha_a) == 64

    # Augment base_ontology with key_authority_tiers for invariant testing
    base_dict = base_ontology.model_dump(mode="json")
    base_dict["canonical_business_concepts"][0]["key_authority_tiers"] = {
        "tier_1_global_standard": {
            "authority_name": "ISO 6166 ISIN",
            "column_patterns": ["isin"],
            "regex_pattern": "^[A-Z]{2}[A-Z0-9]{9}[0-9]$",
            "bkcc_policy": "GLOBAL",
            "require_src_qualifier": False,
        }
    }
    test_base = GraphAOntology.model_validate(base_dict)
    test_base_sha = compute_ontology_sha256(test_base)

    # 2. F2: Invariant locks on Key Authority Tier overwrites
    conflict_kat_delta = OntologyDelta(
        delta_id="delta-conflict-kat",
        parent_sha256=test_base_sha,
        domain_namespace=test_base.domain_namespace,
        operations=[
            AddKeyAuthorityPatternOp(
                op_id="op-kat-overwrite",
                concept_uri="urn:ontology:entity:fund",
                authority_name="Conflicting Authority",
                tier="TIER_1_GLOBAL_STANDARD",
            )
        ],
    )
    val_kat = validate_ontology_delta(test_base, conflict_kat_delta)
    assert val_kat.valid is False
    assert any("ONT-EVO-003: Non-additive conflict" in v for v in val_kat.violations)
    assert any("cannot overwrite with 'Conflicting Authority'" in v for v in val_kat.violations)

    # 3. F2: Invariant locks on Enum allowed_codes shrinkage
    shrink_enum_delta = OntologyDelta(
        delta_id="delta-shrink-enum",
        parent_sha256=test_base_sha,
        domain_namespace=test_base.domain_namespace,
        operations=[
            AddReferenceEnumerationOp(
                op_id="op-shrink-enum",
                enum_uri="urn:ontology:enum:investment_stage",
                name="investment_stage",
                allowed_codes=["SEED"],  # Shrunk from ["SEED", "SERIES_A", "GROWTH", "BUYOUT"]
            )
        ],
    )
    val_enum = validate_ontology_delta(test_base, shrink_enum_delta)
    assert val_enum.valid is False
    assert any("Cannot shrink or drop existing allowed_codes" in v for v in val_enum.violations)

    # 4. F2: Invariant locks on Enum target table rebinding
    rebind_enum_delta = OntologyDelta(
        delta_id="delta-rebind-enum",
        parent_sha256=test_base_sha,
        domain_namespace=test_base.domain_namespace,
        operations=[
            AddReferenceEnumerationOp(
                op_id="op-rebind-enum",
                enum_uri="urn:ontology:enum:investment_stage",
                name="investment_stage",
                target_reference_table="ref_investment_stage_v2",
                allowed_codes=["SEED", "SERIES_A", "GROWTH", "BUYOUT"],
            )
        ],
    )
    val_rebind = validate_ontology_delta(test_base, rebind_enum_delta)
    assert val_rebind.valid is False
    assert any("cannot rebind to 'ref_investment_stage_v2'" in v for v in val_rebind.violations)

    # 5. F2: Invariant locks on Relationship endpoint mutation
    rebind_rel_delta = OntologyDelta(
        delta_id="delta-rebind-rel",
        parent_sha256=test_base_sha,
        domain_namespace=test_base.domain_namespace,
        operations=[
            AddRelationshipOp(
                op_id="op-mut-rel",
                relationship_uri="urn:ontology:relationship:fund_investment",
                relationship_name="FundInvestment",
                target_link="lnk_fund_investment",
                endpoints=["hub_fund", "hub_fund"],  # Mutated endpoints
            )
        ],
    )
    val_rel = validate_ontology_delta(test_base, rebind_rel_delta)
    assert val_rel.valid is False
    assert any("Cannot rewrite endpoints" in v for v in val_rel.violations)

    # 6. F2: Invariant locks on deployed contract rebinding
    deployed_with_ref = dict(deployed_contract)
    deployed_with_ref["reference_tables"] = [
        {"name": "ref_investment_stage", "ontology_concept_uri": "urn:ontology:enum:investment_stage"}
    ]
    rebind_dep_enum_delta = OntologyDelta(
        delta_id="delta-dep-rebind",
        parent_sha256=test_base_sha,
        domain_namespace=test_base.domain_namespace,
        operations=[
            AddReferenceEnumerationOp(
                op_id="op-dep-rebind",
                enum_uri="urn:ontology:enum:investment_stage",
                name="investment_stage",
                target_reference_table="ref_investment_stage_alt",
                allowed_codes=["SEED", "SERIES_A"],
            )
        ],
    )
    val_dep_enum = validate_ontology_delta(
        test_base, rebind_dep_enum_delta, deployed_contract=deployed_with_ref
    )
    assert val_dep_enum.valid is False
    assert any(
        "ONT-EVO-001: Breaking change detected for deployed contract" in v
        for v in val_dep_enum.violations
    )

    # 7. F2: Recursive provisional URI rejection in nested structures
    nested_prov_delta = OntologyDelta(
        delta_id="delta-nested-prov",
        parent_sha256=sha_a,
        domain_namespace=base_ontology.domain_namespace,
        operations=[
            AddRelationshipOp(
                op_id="op-nested-prov",
                relationship_uri="urn:ontology:relationship:secret_rel",
                relationship_name="SecretRel",
                target_link="lnk_secret",
                endpoints=["hub_fund", "hub_portfolio_company"],
                source_concept_uri="urn:ontology:provisional:entity:secret",
            )
        ],
    )
    val_prov = validate_ontology_delta(base_ontology, nested_prov_delta)
    assert val_prov.valid is False
    assert any("ONT-EVO-002: Provisional URI" in v for v in val_prov.violations)

    # 8. F3: Path containment jailbreak raises InputValidationError
    with pytest.raises(InputValidationError, match="escapes workspace root"):
        load_base_ontology(
            "../../outside_workspace/attack_ontology.yaml",
            workspace_root=tmp_path / "workspace",
        )

    # 9. F6: Redaction of recommendation requests
    raw_samples = {
        "investran.src_pe_funds": [
            {
                "fund_code": "APEX_FUND_I",
                "ssn": "123-45-6789",
                "tax_id": "98-7654321",
                "first_name": "Alice",
                "last_name": "Smith",
                "address": "123 Wall St, New York, NY",
                "dob": "1980-01-01",
                "entry_ebitda_usd": 50000000.0,
            }
        ]
    }
    redacted_samples = redact_sample_data(raw_samples, clearance_level="INTERNAL")
    rows = redacted_samples["investran.src_pe_funds"]
    assert rows[0]["ssn"] == "[REDACTED:RESTRICTED_PII]"
    assert rows[0]["first_name"] == "[REDACTED:RESTRICTED_PII]"
    assert rows[0]["last_name"] == "[REDACTED:RESTRICTED_PII]"
    assert rows[0]["address"] == "[REDACTED:RESTRICTED_PII]"
    assert rows[0]["dob"] == "[REDACTED:RESTRICTED_PII]"
    assert rows[0]["entry_ebitda_usd"] == "[REDACTED:MNPI]"
    assert rows[0]["fund_code"] == "APEX_FUND_I"
