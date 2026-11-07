"""Integration tests for Pipeline Extension Hooks IP-1..6 (Decision D9, ONT-05).

Verifies:
1. When ontology_policy="off", hooks are never invoked and no proposals are emitted.
2. When ontology_policy != "off" but no recommender is registered, execution fails loud with
   ONTOLOGY_RECOMMENDER_UNAVAILABLE.
3. When ontology_policy="suggest_only" with a registered recommender, IP-2 emits an advisory
   ontology proposal artifact in STAGE_01.
4. When ontology_policy="steward_review" on Gate 1 unmapped concept hold, IP-3 populates typed
   hold options (ACCEPT_ONTOLOGY_DELTA, REJECT_ONTOLOGY_DELTA).
5. Transient recommender errors in advisory modes are captured in report.recommender_errors
   without crashing the core Data Vault pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from opendv_agent_harness import (
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    OntologyRecommendationRequest,
    RecommenderCapabilities,
)
from opendv_service.ontology.registry import (
    OntologyRecommenderRegistry,
    set_default_ontology_recommender_registry,
)
from opendv_telemetry import ContentAddressableStore
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[3]


class MockPipelineRecommender:
    """Mock recommender implementing the OntologyRecommender protocol."""

    def __init__(self, recommender_id: str = "mock-pipeline-recommender"):
        self._id = recommender_id
        self.call_count = 0
        self.fail_mode = False

    @property
    def recommender_id(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return "1.0.0"

    def capabilities(self) -> RecommenderCapabilities:
        return RecommenderCapabilities(
            supported_modes=["BOOTSTRAP", "DELTA"],
            max_sample_rows=100,
            deterministic=True,
        )

    async def recommend(self, req: OntologyRecommendationRequest) -> OntologyProposal:
        self.call_count += 1
        if self.fail_mode:
            raise RuntimeError("Simulated recommender inference timeout")

        return OntologyProposal(
            proposal_id=f"prop-{self.call_count}",
            recommender_id=self._id,
            recommender_version="1.0.0",
            wave_id=req.wave_id,
            delta=OntologyDelta(
                delta_id=f"delta-{self.call_count}",
                parent_sha256=req.base_ontology_sha256,
                operations=[
                    AddConceptOp(
                        op_id=f"op-{self.call_count}",
                        concept_uri="urn:ontology:entity:delivery_logistics",
                        concept_name="DeliveryLogistics",
                        target_hub="hub_delivery_logistics",
                        definition="Logistics delivery fulfillment concept",
                    )
                ],
            ),
            confidence_score=0.92,
            diagnostics=["Recommender proposal generated successfully"],
        )


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure a clean registry before each test and restore default afterwards."""
    reg = OntologyRecommenderRegistry()
    set_default_ontology_recommender_registry(reg)
    yield reg
    set_default_ontology_recommender_registry(None)


def test_pipeline_extension_hooks_off_never_invoked(tmp_path: Path) -> None:
    """When ontology_policy is 'off', pipeline never calls extension hooks or emits proposals."""
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_off"
    cas = ContentAddressableStore(out_dir / "cas")

    report = run_agentic_vault_pipeline(
        sample_dir / "pipeline_input.yaml",
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "COMPLETED"
    art_ids = [a.artifact_id for a in report.intermediate_artifacts]
    assert not any("stage01-ontology-proposal" in aid for aid in art_ids)
    assert not any("stage05-ontology-proposal" in aid for aid in art_ids)
    assert report.recommender_errors is None


def test_pipeline_active_policy_without_recommender_fails_fast(tmp_path: Path) -> None:
    """When policy != 'off' and no recommender is available, pipeline halts with UNAVAILABLE."""
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_unavail"
    cas = ContentAddressableStore(out_dir / "cas")

    # Load existing sample input and set ontology_policy="suggest_only"
    raw_spec = yaml.safe_load((sample_dir / "pipeline_input.yaml").read_text(encoding="utf-8"))
    raw_spec["ontology_policy"] = "suggest_only"
    input_file = tmp_path / "input_suggest_only.yaml"
    input_file.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")

    report = run_agentic_vault_pipeline(
        input_file,
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "FAILED"
    assert report.overall_passed is False
    codes = [d["code"] for d in report.input_validation.diagnostics]
    assert "ONTOLOGY_RECOMMENDER_UNAVAILABLE" in codes


def test_pipeline_stage01_emits_ontology_proposal_with_active_recommender(
    clean_registry: OntologyRecommenderRegistry,
    tmp_path: Path,
) -> None:
    """When policy is 'suggest_only' and recommender is registered, IP-2 produces wave proposal."""
    mock_rec = MockPipelineRecommender("test-recommender-ip2")
    clean_registry.register(mock_rec)

    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_suggest"
    cas = ContentAddressableStore(out_dir / "cas")

    raw_spec = yaml.safe_load((sample_dir / "pipeline_input.yaml").read_text(encoding="utf-8"))
    raw_spec["ontology_policy"] = "suggest_only"
    input_file = tmp_path / "input_suggest_valid.yaml"
    input_file.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")

    report = run_agentic_vault_pipeline(
        input_file,
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "COMPLETED"
    assert report.overall_passed is True

    # Verify IP-2 artifact emitted
    art_map = {a.artifact_id: a for a in report.intermediate_artifacts}
    assert "art-w0-stage01-ontology-proposal" in art_map

    prop_ref = art_map["art-w0-stage01-ontology-proposal"]
    assert prop_ref.stage_id == "STAGE_01_SCOUT_SOURCE"
    assert prop_ref.media_type == "application/json"

    # Verify content in CAS
    content_bytes = cas.get_bytes(prop_ref.sha256)
    assert content_bytes is not None
    payload = json.loads(content_bytes.decode("utf-8"))
    assert payload["artifact_kind"] == "ontology_proposal"
    assert payload["recommender_id"] == "test-recommender-ip2"
    assert payload["confidence_score"] == 0.92
    assert payload["operations_count"] == 1
    assert mock_rec.call_count >= 1


def test_pipeline_scenario_s3_gate1_hold_populates_delta_options(
    clean_registry: OntologyRecommenderRegistry,
    tmp_path: Path,
) -> None:
    """When policy is 'steward_review' on Gate 1 hold, IP-3 populates typed delta options."""
    mock_rec = MockPipelineRecommender("test-recommender-s3")
    clean_registry.register(mock_rec)

    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_s3_review"
    cas = ContentAddressableStore(out_dir / "cas")

    s3_spec = PipelineInputSpec(
        pipeline_id="pipe-s3-ontology-hooks",
        project_id="proj-s3-hooks",
        domain_namespace="logistics_and_commerce",
        graph_a_ontology_path=str((s3_dir / "graph_a_ontology.yaml").resolve()),
        ontology_policy="steward_review",
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

    s3_input_file = tmp_path / "s3_pipeline_input.yaml"
    s3_input_file.write_text(yaml.safe_dump(s3_spec.model_dump(mode="json")), encoding="utf-8")

    report = run_agentic_vault_pipeline(
        s3_input_file,
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=REPO_ROOT,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "PAUSED_HOLD"

    # Verify IP-3 stage 05 proposal was emitted
    art_map = {a.artifact_id: a for a in report.intermediate_artifacts}
    assert "art-w0-stage05-ontology-proposal" in art_map

    # Verify hold options were enriched with ACCEPT_ONTOLOGY_DELTA and REJECT_ONTOLOGY_DELTA
    assert len(report.active_holds) >= 1
    g1_hold = report.active_holds[0]
    option_ids = [opt.get("option_id") for opt in g1_hold.get("options", [])]
    assert "REJECT_WITH_GAP_REPORT" in option_ids
    assert "ACCEPT_ONTOLOGY_DELTA" in option_ids
    assert "REJECT_ONTOLOGY_DELTA" in option_ids

    accept_opt = next(
        opt for opt in g1_hold["options"] if opt.get("option_id") == "ACCEPT_ONTOLOGY_DELTA"
    )
    assert accept_opt["confidence_score"] == 0.92
    assert accept_opt["operations_count"] == 1


def test_pipeline_recommender_error_in_suggest_only_recorded_nonfatal(
    clean_registry: OntologyRecommenderRegistry,
    tmp_path: Path,
) -> None:
    """An error in an advisory recommender is recorded in recommender_errors without halting."""
    failing_rec = MockPipelineRecommender("failing-recommender")
    failing_rec.fail_mode = True
    clean_registry.register(failing_rec)

    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_err"
    cas = ContentAddressableStore(out_dir / "cas")

    raw_spec = yaml.safe_load((sample_dir / "pipeline_input.yaml").read_text(encoding="utf-8"))
    raw_spec["ontology_policy"] = "suggest_only"
    input_file = tmp_path / "input_failing.yaml"
    input_file.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")

    report = run_agentic_vault_pipeline(
        input_file,
        sample_dir / "harness_config.yaml",
        output_dir=out_dir,
        base_dir=sample_dir,
        cas_store=cas,
        sdk_module=build_deterministic_agy_sdk_shim(),
    )

    assert report.status == "COMPLETED"
    assert report.recommender_errors is not None
    assert any("inference timeout" in err for err in report.recommender_errors)
