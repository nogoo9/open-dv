"""Tests for Pipeline Wave Resumption on resolved ontology hold (Decision D9, ONT-04)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from opendv_agent_harness import (
    AgenticVaultPipeline,
    HarnessSetupConfig,
    PipelineInputSpec,
    resume_agentic_vault_pipeline,
)
from opendv_contracts import (
    AcceptOntologyDeltaOption,
    AddConceptOp,
    OntologyDelta,
    RejectOntologyDeltaOption,
)
from opendv_service.ontology import compute_ontology_sha256
from opendv_telemetry import ContentAddressableStore
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[3]


def _build_s3_pipeline_spec() -> PipelineInputSpec:
    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    return PipelineInputSpec(
        pipeline_id="pipe-s3-resumption-test",
        project_id="proj-s3-test",
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


def test_pipeline_wave_resumption_with_accept_delta(tmp_path: Path) -> None:
    out_dir = tmp_path / "out_resumption"
    cas = ContentAddressableStore(out_dir / "cas")

    spec = _build_s3_pipeline_spec()
    harness_setup = HarnessSetupConfig()

    pipeline = AgenticVaultPipeline(
        input_spec=spec,
        harness_setup=harness_setup,
        output_dir=out_dir,
        base_dir=REPO_ROOT,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    # Initial execution should pause at Gate 1
    report = pipeline.run()
    assert report.status == "PAUSED_HOLD"
    assert report.overall_passed is False

    pending_holds = pipeline.hold_manager.list_pending_holds()
    assert len(pending_holds) >= 1
    hold = pending_holds[0]
    assert hold.rule_id == "DV-G4-001"

    # Base ontology sha
    assert pipeline.ontology is not None
    base_sha = compute_ontology_sha256(pipeline.ontology)

    # Construct delta adding CBC_SUPPLIER_DELIVERY
    delta = OntologyDelta(
        delta_id="delta-s3-supplier-delivery",
        parent_sha256=base_sha,
        operations=[
            AddConceptOp(
                op_id="op-add-supplier-delivery",
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
        steward_note="Approved S3 supplier delivery ontology concept",
    )

    # Resume pipeline
    resumed = resume_agentic_vault_pipeline(
        pipeline,
        hold_id=hold.hold_id,
        option=opt,
        steward_id="lead-steward",
    )

    assert resumed.status == "COMPLETED"
    assert resumed.overall_passed is True
    assert resumed.ontology_version == "1.1.0"
    assert resumed.parent_sha256 == base_sha
    assert pipeline.cas_store.has(resumed.graph_a_sha256)

    # Ensure hold is resolved with APPROVE
    assert len(pipeline.hold_manager.list_pending_holds()) == 0
    resolved_rec = pipeline.hold_manager.get_hold(hold.hold_id)
    assert resolved_rec is not None
    assert resolved_rec.status == "APPROVED"


def test_pipeline_wave_resumption_with_reject_delta(tmp_path: Path) -> None:
    out_dir = tmp_path / "out_reject"
    cas = ContentAddressableStore(out_dir / "cas")

    spec = _build_s3_pipeline_spec()
    harness_setup = HarnessSetupConfig()

    pipeline = AgenticVaultPipeline(
        input_spec=spec,
        harness_setup=harness_setup,
        output_dir=out_dir,
        base_dir=REPO_ROOT,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    report = pipeline.run()
    assert report.status == "PAUSED_HOLD"

    pending_holds = pipeline.hold_manager.list_pending_holds()
    assert len(pending_holds) >= 1
    hold = pending_holds[0]

    reject_opt = RejectOntologyDeltaOption(
        reason="CBC rejected: out of domain scope",
        steward_note="Model as satellite attribute on hub_customer instead.",
    )

    resumed = resume_agentic_vault_pipeline(
        pipeline,
        hold_id=hold.hold_id,
        option=reject_opt,
        steward_id="lead-steward",
    )

    assert resumed.status == "FAILED"
    assert resumed.overall_passed is False
    assert len(pipeline.hold_manager.list_pending_holds()) == 0
    resolved_rec = pipeline.hold_manager.get_hold(hold.hold_id)
    assert resolved_rec is not None
    assert resolved_rec.status == "REJECTED"
