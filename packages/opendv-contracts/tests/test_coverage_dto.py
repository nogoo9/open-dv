"""Unit tests for OntologyCoverageArtifact DTO and JSON Schema compliance (Decision D9, ONT-03)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from opendv_contracts.dtos.coverage import (
    MappedConceptCoverage,
    OntologyCoverageArtifact,
    UnmappedColumnGap,
    UnmappedMentionGap,
    UnmappedTableGap,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ontology_coverage_artifact_creation_and_defaults() -> None:
    art = OntologyCoverageArtifact(
        artifact_id="art-w0-stage01-ontology-coverage",
        wave_id="wave_0",
        domain_namespace="logistics_and_commerce",
        graph_a_version="1.0.0",
        graph_a_sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        total_tables=2,
        mapped_tables_count=1,
        unmapped_tables_count=1,
        total_columns=10,
        mapped_columns_count=6,
        unmapped_columns_count=4,
        coverage_ratio=0.6,
        mapped_concepts=["urn:ontology:entity:customer"],
        mapped_tables=["crm.customers"],
        unmapped_tables=["logistics.feed_unmapped_supplier"],
        unmapped_columns=[{"table": "logistics.feed_unmapped_supplier", "column": "delivery_id"}],
        unmapped_mentions=["unknown supplier mention"],
        provisional_uris_found=[],
        diagnostics=["ONT-COV-GAP: Table 'logistics.feed_unmapped_supplier' has no matching CBC."],
    )

    assert art.artifact_id == "art-w0-stage01-ontology-coverage"
    assert art.coverage_ratio == 0.6
    assert art.unmapped_tables_count == 1
    assert art.unmapped_columns_count == 4
    assert len(art.diagnostics) == 1
    assert art.generated_at_utc is None


def test_ontology_coverage_schema_validation() -> None:
    schema_path = REPO_ROOT / "packages" / "opendv-contracts" / "schemas" / "ontology-coverage.schema.json"
    assert schema_path.is_file(), f"Schema file not found at {schema_path}"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    art = OntologyCoverageArtifact(
        artifact_id="art-w0-stage01-ontology-coverage",
        wave_id="wave_0",
        domain_namespace="private_equity",
        graph_a_version="1.0.0",
        graph_a_sha256="0" * 64,
        total_tables=1,
        mapped_tables_count=1,
        unmapped_tables_count=0,
        total_columns=5,
        mapped_columns_count=5,
        unmapped_columns_count=0,
        coverage_ratio=1.0,
        mapped_concepts=["urn:ontology:entity:portfolio_company"],
        mapped_tables=["pe.portfolio_company"],
        unmapped_tables=[],
        unmapped_columns=[],
        unmapped_mentions=[],
        provisional_uris_found=[],
        diagnostics=[],
        detailed_mapped_concepts=[
            MappedConceptCoverage(
                concept_uri="urn:ontology:entity:portfolio_company",
                concept_name="PORTFOLIO_COMPANY",
                target_hub="hub_portfolio_company",
                mapped_tables=["pe.portfolio_company"],
                mapped_columns=[{"table": "pe.portfolio_company", "column": "company_id"}],
                confidence=1.0,
            )
        ],
        detailed_unmapped_tables=[],
        detailed_unmapped_columns=[],
        detailed_unmapped_mentions=[],
    )

    payload = json.loads(art.model_dump_json())
    jsonschema.validate(instance=payload, schema=schema)


def test_ontology_coverage_gap_models() -> None:
    tbl_gap = UnmappedTableGap(
        table_name="bronze_logistics.feed_unmapped_supplier",
        columns=["delivery_id", "supplier_code"],
        row_count=100,
        candidate_hints=["hub_supplier_delivery"],
        reason="ABSENT_FROM_ONTOLOGY",
    )
    assert tbl_gap.table_name == "bronze_logistics.feed_unmapped_supplier"
    assert tbl_gap.reason == "ABSENT_FROM_ONTOLOGY"

    col_gap = UnmappedColumnGap(
        table_name="bronze_logistics.feed_unmapped_supplier",
        column_name="delivery_id",
        inferred_type="VARCHAR",
        candidate_concept_uris=["urn:ontology:entity:delivery"],
    )
    assert col_gap.column_name == "delivery_id"

    mention_gap = UnmappedMentionGap(
        doc_sha256="abc" * 20,
        char_start=10,
        char_end=25,
        text_snippet="Apex Supplier Co",
        inferred_entity_type="supplier",
    )
    assert mention_gap.char_end == 25
