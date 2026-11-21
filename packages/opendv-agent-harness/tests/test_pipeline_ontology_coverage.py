"""Tests for Pipeline STAGE_01 ontology coverage emission and Gate 1 PAUSED_HOLD.

Decision D9, ONT-03.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from opendv_agent_harness import (
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_telemetry import ContentAddressableStore
from opendv_testkit import build_deterministic_agy_sdk_shim

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_pipeline_stage01_emits_ontology_coverage_artifact(tmp_path: Path) -> None:
    """Verify STAGE_01 emits art-w{wave}-stage01-ontology-coverage into CAS and run artifacts."""
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out"
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

    # Verify art-w0-stage01-ontology-coverage exists in report intermediate_artifacts
    art_map = {a.artifact_id: a for a in report.intermediate_artifacts}
    cov_id = "art-w0-stage01-ontology-coverage"
    assert cov_id in art_map, (
        f"Coverage artifact {cov_id} not found in artifacts: {list(art_map.keys())}"
    )

    cov_art_ref = art_map[cov_id]
    assert cov_art_ref.media_type == "application/json"
    assert cov_art_ref.stage_id == "STAGE_01_SCOUT_SOURCE"

    # Verify content in CAS
    content_bytes = cas.get_bytes(cov_art_ref.sha256)
    assert content_bytes is not None
    cov_payload = json.loads(content_bytes.decode("utf-8"))

    assert cov_payload["artifact_id"] == cov_id
    assert cov_payload["wave_id"] == "w0"
    assert cov_payload["coverage_ratio"] == 1.0
    assert cov_payload["unmapped_tables_count"] == 0
    assert any("crm" in t for t in cov_payload["mapped_tables"])


def test_pipeline_scenario_s3_unmapped_feed_halts_at_gate1_hold(tmp_path: Path) -> None:
    """Verify Scenario S3 unmapped feed halts at Gate 1 with PAUSED_HOLD."""
    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    sample_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    out_dir = tmp_path / "out_s3"
    cas = ContentAddressableStore(out_dir / "cas")

    # Build PipelineInputSpec with Scenario S3 unmapped supplier feed and S3 ontology
    s3_spec = PipelineInputSpec(
        pipeline_id="pipe-s3-unmapped-test",
        project_id="proj-s3-test",
        domain_namespace="logistics_and_commerce",
        graph_a_ontology_path=str((s3_dir / "graph_a_ontology.yaml").resolve()),
        sources=[
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
        unstructured_documents=[],
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

    # Status must be PAUSED_HOLD per Decision D9 and Gate 1 hold specification
    assert report.status == "PAUSED_HOLD"
    assert report.overall_passed is False

    # Check that art-w0-stage01-ontology-coverage was emitted
    art_map = {a.artifact_id: a for a in report.intermediate_artifacts}
    assert "art-w0-stage01-ontology-coverage" in art_map

    cov_bytes = cas.get_bytes(art_map["art-w0-stage01-ontology-coverage"].sha256)
    assert cov_bytes is not None
    cov_payload = json.loads(cov_bytes.decode("utf-8"))
    assert cov_payload["coverage_ratio"] == 0.0
    assert cov_payload["unmapped_tables_count"] == 1
    assert "feed_unmapped_supplier" in cov_payload["unmapped_tables"][0]

    # Verify hold recorded in hold manager / active steward holds
    assert len(report.active_holds) >= 1
    g1_hold = report.active_holds[0]
    assert g1_hold["rule_id"] == "DV-G4-001"
    assert g1_hold["escalation_reason"] == "UNMAPPED_ONTOLOGY_CONCEPT"
    assert any(
        opt.get("coverage_artifact_id") == "art-w0-stage01-ontology-coverage"
        for opt in g1_hold.get("options", [])
    )


def test_ds01_stage01_ontology_coverage_and_singularization(tmp_path: Path) -> None:
    """Bug B TDD: DS-01-shaped ontology + feeds achieve coverage_ratio == 1.0,
    unmapped_tables == [], and _resolve_hub_and_uri('securities', ont) -> ('hub_security', ...).
    """
    from opendv_agent_harness.pipeline.discovery_unstructured import _resolve_hub_and_uri
    from opendv_agent_harness.pipeline.ontology_evolution import emit_stage01_ontology_coverage
    from opendv_agent_harness.pipeline.spec import Stage01FeedDiscoveryItem
    from opendv_core.models.graph_a import GraphAOntology

    ontology = GraphAOntology.model_validate(
        {
            "ontology_version": "1.0.0",
            "domain_namespace": "wealth_mgmt",
            "enterprise_owner": "Enterprise",
            "canonical_business_concepts": [
                {
                    "concept_uri": "urn:opendv:concept:issuer",
                    "concept_name": "Issuer",
                    "target_hub": "hub_issuer",
                    "elm_archetype": "CORE_BUSINESS_CONCEPT",
                    "attributes": [{"attribute_name": "cik"}],
                },
                {
                    "concept_uri": "urn:opendv:concept:position",
                    "concept_name": "Position",
                    "target_hub": "hub_position",
                    "elm_archetype": "CORE_BUSINESS_CONCEPT",
                    "attributes": [
                        {"attribute_name": "position_id"},
                        {"attribute_name": "currency_code"},
                    ],
                },
                {
                    "concept_uri": "urn:opendv:concept:security",
                    "concept_name": "Security",
                    "target_hub": "hub_security",
                    "elm_archetype": "CORE_BUSINESS_CONCEPT",
                    "attributes": [
                        {"attribute_name": "isin_code"},
                        {"attribute_name": "currency_code"},
                    ],
                },
                {
                    "concept_uri": "urn:opendv:concept:treasury_rate",
                    "concept_name": "Treasury Rate",
                    "target_hub": "hub_treasury_rate",
                    "elm_archetype": "CORE_BUSINESS_CONCEPT",
                    "attributes": [
                        {"attribute_name": "series_id"},
                        {"attribute_name": "currency_code"},
                    ],
                },
            ],
            "canonical_relationships": [],
        }
    )

    hub_name, hub_uri = _resolve_hub_and_uri("securities", ontology)
    assert hub_name == "hub_security"
    assert hub_uri == "urn:opendv:concept:security"

    tr_hub, tr_uri = _resolve_hub_and_uri("treasury_rates", ontology)
    assert tr_hub == "hub_treasury_rate"
    assert tr_uri == "urn:opendv:concept:treasury_rate"

    feeds = [
        Stage01FeedDiscoveryItem(
            source_system="ds01",
            table_name="issuers",
            row_count=45,
            primary_keys=["cik"],
            foreign_keys=[],
        ),
        Stage01FeedDiscoveryItem(
            source_system="ds01",
            table_name="positions",
            row_count=20,
            primary_keys=["row_seq_sk"],
            foreign_keys=[
                {"fk_column": "currency_code", "target_table": "currency_code"},
                {"fk_column": "cik", "target_table": "issuers"},
                {"fk_column": "isin_code", "target_table": "securities"},
            ],
        ),
        Stage01FeedDiscoveryItem(
            source_system="ds01",
            table_name="securities",
            row_count=20,
            primary_keys=["isin_code"],
            foreign_keys=[
                {"fk_column": "currency_code", "target_table": "currency_code"},
                {"fk_column": "cik", "target_table": "issuers"},
            ],
        ),
        Stage01FeedDiscoveryItem(
            source_system="ds01",
            table_name="treasury_rates",
            row_count=30,
            primary_keys=["observation_date", "series_id"],
            foreign_keys=[{"fk_column": "currency_code", "target_table": "currency_code"}],
        ),
        Stage01FeedDiscoveryItem(
            source_system="ds01",
            table_name="currency_code",
            row_count=8,
            primary_keys=["currency_code"],
            foreign_keys=[],
        ),
    ]

    cov_art, unmapped = emit_stage01_ontology_coverage(
        ontology,
        sources=[],
        discovered_feeds=feeds,
        output_dir=tmp_path,
        wave_index=0,
    )
    assert cov_art is not None
    payload = json.loads(Path(cov_art.file_path).read_text(encoding="utf-8"))
    assert unmapped == []
    assert payload["unmapped_tables"] == []
    assert payload["coverage_ratio"] == 1.0
