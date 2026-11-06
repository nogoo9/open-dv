"""Unit tests for compute_ontology_coverage service engine (Decision D9, ONT-03)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
    KeyAuthorityTierSpec,
    KeyAuthorityTiers,
)
from opendv_service.ontology.coverage import compute_ontology_coverage

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_compute_ontology_coverage_fully_mapped_pe() -> None:
    """Verify fully mapped Private Equity ontology yields coverage ratio 1.0."""
    pe_dir = REPO_ROOT / "examples" / "private_equity"
    ont_path = pe_dir / "pe_graph_a_ontology.yaml"
    prof_path = pe_dir / "pe_graph_b_profile.json"
    contract_path = pe_dir / "dv_pe_model_contract.yaml"

    assert ont_path.is_file()
    assert prof_path.is_file()
    assert contract_path.is_file()

    ont_dict = yaml.safe_load(ont_path.read_text(encoding="utf-8"))
    prof_dict = json.loads(prof_path.read_text(encoding="utf-8"))
    contract_dict = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

    art = compute_ontology_coverage(
        graph_a=ont_dict,
        profiles=prof_dict,
        contract=contract_dict,
        wave_id="wave_0",
        run_id="run-pe-test",
    )

    assert art.artifact_id == "art-wave_0-stage01-ontology-coverage"
    assert art.coverage_ratio == 1.0
    assert art.unmapped_tables_count == 0
    assert len(art.unmapped_tables) == 0
    assert len(art.unmapped_columns) == 0
    assert art.total_tables > 0
    assert art.mapped_tables_count == art.total_tables
    assert len(art.provisional_uris_found) == 0


def test_compute_ontology_coverage_scenario_s3_unmapped_feed() -> None:
    """Verify Scenario S3 unmapped feed yields coverage ratio 0.0 with gap diagnostics."""
    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    ont_path = s3_dir / "graph_a_ontology.yaml"
    contract_path = s3_dir / "contract_s3.yaml"

    assert ont_path.is_file()
    assert contract_path.is_file()

    art = compute_ontology_coverage(
        graph_a=ont_path,
        contract=contract_path,
        wave_id="w0",
        run_id="run-s3-test",
    )

    assert art.artifact_id == "art-w0-stage01-ontology-coverage"
    assert art.coverage_ratio == 0.0
    assert art.unmapped_tables_count == 1
    assert "bronze_logistics.feed_unmapped_supplier" in art.unmapped_tables
    assert art.unmapped_columns_count > 0
    assert any("feed_unmapped_supplier" in c["table"] for c in art.unmapped_columns)
    assert any("ONT-COV-GAP" in d for d in art.diagnostics)
    assert len(art.detailed_unmapped_tables) == 1
    assert art.detailed_unmapped_tables[0].table_name == "bronze_logistics.feed_unmapped_supplier"


def test_compute_ontology_coverage_detects_provisional_uris() -> None:
    """Verify forbidden provisional URIs are rejected by core model and detected by coverage."""
    # 1. Core model strict validation rejects provisional URIs
    with pytest.raises(Exception):
        CanonicalBusinessConcept(
            concept_uri="urn:ontology:provisional:entity:legacy_order",
            concept_name="LEGACY_ORDER",
            elm_archetype="HUB",
            definition="A legacy order with provisional URI.",
            target_hub="hub_legacy_order",
        )

    # 2. Coverage engine detects provisional URIs in raw input and records diagnostic
    raw_ont = {
        "ontology_version": "1.0.0",
        "domain_namespace": "test_domain",
        "enterprise_owner": "Enterprise Steward",
        "canonical_business_concepts": [
            {
                "concept_uri": "urn:ontology:provisional:entity:legacy_order",
                "concept_name": "LEGACY_ORDER",
                "elm_archetype": "HUB",
                "definition": "A legacy order with provisional URI.",
                "target_hub": "hub_legacy_order",
            }
        ],
    }
    art = compute_ontology_coverage(
        graph_a=raw_ont,
        profiles={"source_systems": []},
        wave_id="w1",
    )

    assert "urn:ontology:provisional:entity:legacy_order" in art.provisional_uris_found
    assert any("ONT-PROV-URI" in d for d in art.diagnostics)


def test_compute_ontology_coverage_unstructured_mention_gaps() -> None:
    """Verify unstructured mentions with unknown hub targets produce mention gaps."""
    ont = GraphAOntology(
        domain_namespace="commerce",
        enterprise_owner="Steward",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="CUSTOMER",
                elm_archetype="HUB",
                definition="Customer entity.",
                target_hub="hub_customer",
            )
        ],
    )
    unstructured_docs = [
        {
            "doc_sha256": "1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
            "extracted_candidates": [
                {
                    "target_hub": "hub_unknown_supplier",
                    "verbatim_quote": "Acme Logistics Inc",
                    "char_start": 5,
                    "char_end": 23,
                }
            ],
        }
    ]
    art = compute_ontology_coverage(
        graph_a=ont,
        unstructured_docs=unstructured_docs,
        wave_id="wave_0",
    )

    assert len(art.unmapped_mentions) == 1
    assert art.unmapped_mentions[0] == "Acme Logistics Inc"
    assert len(art.detailed_unmapped_mentions) == 1
    assert art.detailed_unmapped_mentions[0].inferred_entity_type == "hub_unknown_supplier"
    assert any("Document mention 'Acme Logistics Inc'" in d for d in art.diagnostics)
