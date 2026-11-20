"""TDD tests for Multi-Source Wave 2 Modeling (Bug A) and Report Lineage Metadata (Bug D)."""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace

from opendv_agent_harness.pipeline.contract_evolver import evolve_contract_single_run
from opendv_agent_harness.pipeline.contract_synthesizer import _synthesize_cumulative_contract
from opendv_agent_harness.pipeline.ontology_coverage_helpers import (
    _collect_ontology_coverage_index,
    _is_feed_covered_by_ontology,
)
from opendv_agent_harness.pipeline.spec import PipelineInputSpec, Stage01FeedDiscoveryItem
from opendv_agent_harness.pipeline.validation_postflight import build_completed_pipeline_report
from opendv_axi_verifier.codemods.alg_a4_cold_start import (
    codemod_enforce_cold_start_extensibility,
)
from opendv_axi_verifier.verifiers.alg_a7_evolution_lock import (
    verify_additive_evolution_invariants,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)


def _build_multisource_ontology() -> GraphAOntology:
    """Synthetic Studio-style ontology (no key_authority_tiers) for multi-source Wave 2 tests."""
    return GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace="clinical_operations",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:patient",
                concept_name="Patient Record",
                target_hub="hub_patient",
                synonyms=["patient", "member"],
                attributes=[
                    {"name": "patient_code"},
                    {"name": "master_patient_id"},
                    {"name": "duplicate_patient_code"},
                    {"name": "confidence_score"},
                    {"name": "match_method"},
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:treatment",
                concept_name="Treatment Plan",
                target_hub="hub_treatment",
                synonyms=["treatment", "therapy"],
                attributes=[{"name": "treatment_code"}, {"name": "protocol_name"}],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:care_encounter",
                concept_name="Care Encounter",
                target_hub="hub_care_encounter",
                synonyms=["encounter", "visit"],
                attributes=[
                    {"name": "encounter_id"},
                    {"name": "patient_code"},
                    {"name": "treatment_code"},
                    {"name": "partner_clinic_id"},
                    {"name": "encounter_date"},
                    {"name": "encounter_status"},
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:partner_clinic",
                concept_name="Partner Clinic",
                target_hub="hub_partner_clinic",
                synonyms=["partner_clinic", "clinic"],
                attributes=[{"name": "partner_clinic_id"}, {"name": "clinic_name"}],
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:encounter_patient",
                relationship_name="encounter_for_patient",
                endpoints=[
                    "urn:ontology:entity:care_encounter",
                    "urn:ontology:entity:patient",
                ],
                target_link="lnk_care_encounter_patient",
            ),
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:encounter_treatment",
                relationship_name="encounter_uses_treatment",
                endpoints=[
                    "urn:ontology:entity:care_encounter",
                    "urn:ontology:entity:treatment",
                ],
                target_link="lnk_care_encounter_treatment",
            ),
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:encounter_partner_clinic",
                relationship_name="encounter_at_partner_clinic",
                endpoints=[
                    "urn:ontology:entity:care_encounter",
                    "urn:ontology:entity:partner_clinic",
                ],
                target_link="lnk_care_encounter_partner_clinic",
            ),
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:patient_crosswalk",
                relationship_name="patient_same_as_crosswalk",
                endpoints=[
                    "urn:ontology:entity:patient",
                    "urn:ontology:entity:patient",
                ],
                target_link="sal_patient",
            ),
        ],
    )


def _build_wave2_feeds() -> tuple[list[Stage01FeedDiscoveryItem], dict[str, dict]]:
    encounter_feed = Stage01FeedDiscoveryItem(
        feed_id="feed-ds04-w2-encounter",
        source_system="bronze_clinical",
        table_name="ds04_w2_encounter",
        row_count=4,
        primary_keys=[],
        foreign_keys=[],
        column_descriptors=[
            {"name": "encounter_id", "physical_type": "VARCHAR(64)"},
            {"name": "patient_code", "physical_type": "VARCHAR(64)"},
            {"name": "treatment_code", "physical_type": "VARCHAR(64)"},
            {"name": "partner_clinic_id", "physical_type": "VARCHAR(64)"},
            {"name": "encounter_date", "physical_type": "DATE"},
            {"name": "encounter_status", "physical_type": "VARCHAR(64)"},
        ],
    )
    crosswalk_feed = Stage01FeedDiscoveryItem(
        feed_id="feed-ds04-patient-crosswalk",
        source_system="bronze_clinical",
        table_name="ds04_patient_crosswalk",
        row_count=3,
        primary_keys=[],
        foreign_keys=[],
        column_descriptors=[
            {"name": "master_patient_id", "physical_type": "VARCHAR(64)"},
            {"name": "duplicate_patient_code", "physical_type": "VARCHAR(64)"},
            {"name": "confidence_score", "physical_type": "DOUBLE"},
            {"name": "match_method", "physical_type": "VARCHAR(64)"},
        ],
    )
    unmapped_feed = Stage01FeedDiscoveryItem(
        feed_id="feed-unknown-widget",
        source_system="bronze_clinical",
        table_name="ds04_w2_orphan_inventory",
        row_count=3,
        primary_keys=[],
        foreign_keys=[],
        column_descriptors=[
            {"name": "orphan_sku", "physical_type": "VARCHAR(64)"},
            {"name": "warehouse_bin", "physical_type": "VARCHAR(64)"},
            {"name": "bin_capacity", "physical_type": "INTEGER"},
        ],
    )
    profile_summaries = {
        "ds04_w2_encounter": {
            "row_count": 4,
            "column_stats": {
                "encounter_id": {"non_null_ratio": 1.0, "distinct_count": 4},
                "patient_code": {"non_null_ratio": 1.0, "distinct_count": 4},
                "treatment_code": {"non_null_ratio": 1.0, "distinct_count": 3},
                "partner_clinic_id": {"non_null_ratio": 0.75, "distinct_count": 2},
                "encounter_date": {"non_null_ratio": 1.0, "distinct_count": 4},
                "encounter_status": {"non_null_ratio": 1.0, "distinct_count": 2},
            },
        },
        "ds04_patient_crosswalk": {
            "row_count": 3,
            "column_stats": {
                "master_patient_id": {"non_null_ratio": 1.0, "distinct_count": 2},
                "duplicate_patient_code": {"non_null_ratio": 1.0, "distinct_count": 3},
                "confidence_score": {"non_null_ratio": 1.0, "distinct_count": 3},
                "match_method": {"non_null_ratio": 1.0, "distinct_count": 2},
            },
        },
    }
    return [encounter_feed, crosswalk_feed, unmapped_feed], profile_summaries


def _baseline_v1_contract() -> dict:
    return {
        "contract_version": "5.0",
        "domain_namespace": "clinical_operations",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "bronze_clinical.patients",
                "column_name": "patient_code",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_patient",
                "source_sql_type": "VARCHAR(64)",
            },
            {
                "bronze_table": "bronze_clinical.treatments",
                "column_name": "treatment_code",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_treatment",
                "source_sql_type": "VARCHAR(64)",
            },
        ],
        "hubs": [
            {
                "name": "hub_patient",
                "ontology_concept_uri": "urn:ontology:entity:patient",
                "business_key_sources": [
                    {
                        "bronze_table": "bronze_clinical.patients",
                        "record_source_code": "CLINICAL",
                        "key_columns": ["patient_code"],
                        "bkcc_prefix": "GLOBAL",
                        "require_src_qualifier": False,
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                    }
                ],
            },
            {
                "name": "hub_treatment",
                "ontology_concept_uri": "urn:ontology:entity:treatment",
                "business_key_sources": [
                    {
                        "bronze_table": "bronze_clinical.treatments",
                        "record_source_code": "CLINICAL",
                        "key_columns": ["treatment_code"],
                        "bkcc_prefix": "GLOBAL",
                        "require_src_qualifier": False,
                        "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                    }
                ],
            },
        ],
        "links": [],
        "satellites": [],
        "reference_tables": [],
        "unmapped_escalations": [],
    }


def test_prefix_stripped_and_attribute_matched_feed_is_covered_without_false_hold() -> None:
    """ds04_w2_encounter matches Care Encounter via prefix strip + attributes."""
    ontology = _build_multisource_ontology()
    feeds, _ = _build_wave2_feeds()
    encounter_feed, crosswalk_feed, unmapped_feed = feeds

    ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets = _collect_ontology_coverage_index(
        ontology, feeds
    )

    assert _is_feed_covered_by_ontology(
        encounter_feed, None, ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets, ontology
    )
    assert _is_feed_covered_by_ontology(
        crosswalk_feed, None, ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets, ontology
    )
    assert not _is_feed_covered_by_ontology(
        unmapped_feed, None, ont_hubs, ont_concepts, ont_slugs, ont_attrs, fk_targets, ontology
    )


def test_synthesizer_emits_sal_and_sparse_fk_companion_link_when_csv_metadata_empty() -> None:
    """Bug A: even with primary_keys=[], foreign_keys=[], and key_authority_tiers=None,
    synthesizer & evolution emit sparse lnk_* (`IS NOT NULL`) and sal_* + esat_sal_*.
    """
    ontology = _build_multisource_ontology()
    feeds, profile_summaries = _build_wave2_feeds()
    w2_feeds = feeds[:2]  # encounter + crosswalk

    evolved, _drift = evolve_contract_single_run(
        _baseline_v1_contract(),
        ontology,
        w2_feeds,
        schema_epoch_tag="v2",
        profile_summaries=profile_summaries,
    )

    # 1. No phantom ds04_w2_encounter_id or ds04_patient_crosswalk_id BKs
    all_bk_cols = {
        c
        for h in evolved.get("hubs", [])
        for bks in h.get("business_key_sources", [])
        for c in (bks.get("source_key_columns") or bks.get("key_columns") or [])
    }
    assert "ds04_w2_encounter_id" not in all_bk_cols
    assert "ds04_patient_crosswalk_id" not in all_bk_cols
    assert "encounter_id" in all_bk_cols

    # 2. Sparse FK partner_clinic_id (< 0.95 non-null) is NOT buried in sat_* payload;
    #    it is promoted to a sparse companion binary link with WHERE <col> IS NOT NULL.
    links = {lnk["name"]: lnk for lnk in evolved.get("links", [])}
    assert "lnk_care_encounter_partner_clinic" in links
    sparse_lnk = links["lnk_care_encounter_partner_clinic"]
    assert sparse_lnk.get("where_filter_condition") == "partner_clinic_id IS NOT NULL"
    assert len(sparse_lnk.get("hub_references", [])) == 2

    encounter_sats = [
        s
        for s in evolved.get("satellites", [])
        if "ds04_w2_encounter" in s.get("source_bronze_table", "")
    ]
    for esat in encounter_sats:
        assert "partner_clinic_id" not in esat.get("payload_columns", [])
        assert "patient_code" not in esat.get("payload_columns", [])
        assert "treatment_code" not in esat.get("payload_columns", [])

    # 3. Same-As Link (sal_*) + Effectivity Satellite (esat_sal_*) on [patient, patient]
    sal_links = [
        lnk
        for lnk in evolved.get("links", [])
        if lnk.get("name", "").startswith("sal_") or lnk.get("link_type") == "SAME_AS_LINK"
    ]
    assert len(sal_links) == 1
    sal = sal_links[0]
    assert sal["link_type"] == "SAME_AS_LINK"
    refs = sal["hub_references"]
    assert len(refs) == 2
    assert refs[0]["target_hub"] == "hub_patient"
    assert refs[1]["target_hub"] == "hub_patient"
    assert refs[0]["source_fk_columns"] != refs[1]["source_fk_columns"]
    assert refs[0]["source_fk_columns"] == ["master_patient_id"]
    assert refs[1]["source_fk_columns"] == ["duplicate_patient_code"]

    esats = [
        s
        for s in evolved.get("satellites", [])
        if s.get("parent_entity") == sal["name"]
        and s.get("satellite_type") == "EFFECTIVITY_SATELLITE"
    ]
    assert len(esats) == 1
    assert esats[0].get("driving_key_column") == "master_patient_id"

    # 4. Evolution invariants (Alg A.7) MUST accept baseline -> evolved
    evo_violations = verify_additive_evolution_invariants(_baseline_v1_contract(), evolved)
    assert evo_violations == [], f"Unexpected Alg A.7 violations: {evo_violations}"


def test_alg_a4_codemod_promotes_sparse_fk_out_of_satellite_and_repairs_sal() -> None:
    """Alg A.4 must promote an unmodeled sparse FK column out of sat_* into filtered binary link."""
    ontology = _build_multisource_ontology()
    feeds, profile_summaries = _build_wave2_feeds()
    contract = _synthesize_cumulative_contract(
        ontology,
        feeds[:2],
        profile_summaries=profile_summaries,
        is_cold_start=True,
    )
    # Simulate Stage 04 LLM regression: demote partner_clinic_id back into sat_care_encounter
    for sat in contract["satellites"]:
        if sat["parent_entity"] == "hub_care_encounter":
            sat["payload_columns"].append("partner_clinic_id")
            break
    contract["links"] = [
        lnk for lnk in contract["links"] if lnk["name"] != "lnk_care_encounter_partner_clinic"
    ]

    repaired = codemod_enforce_cold_start_extensibility(
        contract,
        ontology_graph=ontology,
        profile_summaries=profile_summaries,
    )

    repaired_links = {lnk["name"]: lnk for lnk in repaired.get("links", [])}
    assert "lnk_care_encounter_partner_clinic" in repaired_links
    assert (
        repaired_links["lnk_care_encounter_partner_clinic"].get("where_filter_condition")
        == "partner_clinic_id IS NOT NULL"
    )
    for sat in repaired.get("satellites", []):
        if sat["parent_entity"] == "hub_care_encounter":
            assert "partner_clinic_id" not in sat.get("payload_columns", [])


def test_completed_pipeline_report_sets_initial_contract_metadata_without_breaking_path_parity(
    tmp_path: Path,
) -> None:
    """Bug D: report.has_initial_contract reflects inline/blueprint initial contract,
    while leaving path-only inputs at COLD_START_N1 for GT-01/wave1 parity.
    """
    ont_path = tmp_path / "ont.json"
    ont_path.write_text(_build_multisource_ontology().model_dump_json(), encoding="utf-8")

    inline_spec = PipelineInputSpec(
        pipeline_id="p-w2",
        project_id="proj-w2",
        domain_namespace="clinical_operations",
        graph_a_ontology_path=str(ont_path),
        initial_contract_content='{"contract_version": "5.0"}',
    )
    runner_inline = SimpleNamespace(
        run_id="run-w2-test",
        input_spec=inline_spec,
        harness_setup=SimpleNamespace(
            provider="agy",
            execution_mode="production",
            steward_hold_policy="pause",
            hitl_tier2_enabled=False,
        ),
        observability_events=[],
        fallbacks_applied=[],
        graph_a_sha256="abc",
        ontology=SimpleNamespace(ontology_version="1.1.0", parent_sha256=None, decision_refs=[]),
        output_dir=tmp_path,
        cas_store=None,
    )
    report_inline = build_completed_pipeline_report(
        runner_inline,
        datetime.datetime.now(datetime.UTC),
        {"passed": True},
        None,
        [],
        [],
        [],
        {},
        [],
        {},
        [],
        None,
        {},
    )
    assert report_inline.has_initial_contract is True
    assert report_inline.lifecycle_stage == "MULTI_SOURCE_EXPANSION"

    # When only initial_contract_path is set (GT-01/wave1 pattern), preserve COLD_START_N1 parity
    path_spec = PipelineInputSpec(
        pipeline_id="p-w1-parity",
        project_id="proj-parity",
        domain_namespace="retail",
        graph_a_ontology_path=str(ont_path),
        initial_contract_path="some/prior_contract.json",
    )
    runner_path = SimpleNamespace(
        run_id="run-w1-parity",
        input_spec=path_spec,
        harness_setup=runner_inline.harness_setup,
        observability_events=[],
        fallbacks_applied=[],
        graph_a_sha256="abc",
        ontology=runner_inline.ontology,
        output_dir=tmp_path,
        cas_store=None,
    )
    report_path = build_completed_pipeline_report(
        runner_path,
        datetime.datetime.now(datetime.UTC),
        {"passed": True},
        None,
        [],
        [],
        [],
        {},
        [],
        {},
        [],
        None,
        {},
    )
    assert report_path.has_initial_contract is False
    assert report_path.lifecycle_stage == "COLD_START_N1"
