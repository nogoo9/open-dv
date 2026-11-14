"""Tests for Feature 7: Unstructured-Only Pipeline Mode."""

from pathlib import Path

from opendv_agent_harness.pipeline import (
    PipelineInputSpec,
    PipelineUnstructuredDocInput,
    PipelineWaveInput,
    _synthesize_cumulative_contract,
    validate_pipeline_inputs,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology


def _sample_ontology() -> GraphAOntology:
    return GraphAOntology(
        domain_namespace="test",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:document",
                concept_name="Document",
                definition="Document concept",
                target_hub="hub_document",
            )
        ],
    )


def test_unstructured_only_validation_bypass(tmp_path: Path) -> None:
    """Verify unstructured_only=True bypasses UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE."""
    doc_file = tmp_path / "doc.txt"
    doc_file.write_text("Sample contract text", encoding="utf-8")

    ont_file = tmp_path / "ont.json"
    ont_file.write_text(_sample_ontology().model_dump_json(), encoding="utf-8")

    wave = PipelineWaveInput(
        wave_index=0,
        wave_name="Unstructured Ingestion",
        wave_type="UNSTRUCTURED_ONBOARDING",
        unstructured_documents=[
            PipelineUnstructuredDocInput(
                doc_id="DOC-001",
                document_path=str(doc_file),
                record_source="UNSTRUCTURED",
            )
        ],
    )

    # 1. With unstructured_only=False (default): fails validation
    spec_default = PipelineInputSpec(
        pipeline_id="pipe-1",
        project_id="proj-1",
        graph_a_ontology_path=str(ont_file),
        waves=[wave],
        unstructured_only=False,
    )
    report_default = validate_pipeline_inputs(spec_default, base_dir=tmp_path)
    assert report_default.passed is False
    assert any(
        d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" for d in report_default.diagnostics
    )

    # 2. With unstructured_only=True: passes validation
    spec_unstr = PipelineInputSpec(
        pipeline_id="pipe-1",
        project_id="proj-1",
        graph_a_ontology_path=str(ont_file),
        waves=[wave],
        unstructured_only=True,
    )
    report_unstr = validate_pipeline_inputs(spec_unstr, base_dir=tmp_path)
    assert not any(
        d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" for d in report_unstr.diagnostics
    )


def test_unstructured_only_contract_synthesis() -> None:
    """Verify contract synthesis in unstructured-only mode produces tier-1 hub_document,
    sat_document_metadata, and 6 ledger entries.
    """
    ontology = _sample_ontology()
    doc_input = PipelineUnstructuredDocInput(
        doc_id="DOC-001",
        document_path="inputs/doc.txt",
        record_source="UNSTRUCTURED",
    )
    cum_unstr = [(doc_input, "sha256_mock_hash")]

    contract = _synthesize_cumulative_contract(
        ontology=ontology,
        cumulative_sources={},
        cumulative_source_systems={},
        cumulative_unstructured_docs=cum_unstr,
        added_drift_cols_by_table={},
        current_wave_drift_tables=set(),
        wave_index=0,
        domain_namespace="test",
        deployed_contract=None,
    )

    # 1. Hub document synthesized
    hubs = contract["hubs"]
    assert any(h["name"] == "hub_document" for h in hubs)
    doc_hub = next(h for h in hubs if h["name"] == "hub_document")
    bk_src = doc_hub["business_key_sources"][0]
    assert bk_src["bronze_table"] == "bronze.unstructured_documents"
    assert bk_src["key_columns"] == ["document_id"]
    assert bk_src["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
    assert bk_src["require_src_qualifier"] is False
    assert bk_src["bkcc_prefix"] == "GLOBAL"

    # 2. Metadata satellite synthesized
    sats = contract["satellites"]
    assert any(s["name"] == "sat_document_metadata" for s in sats)
    doc_sat = next(s for s in sats if s["name"] == "sat_document_metadata")
    assert doc_sat["parent_entity"] == "hub_document"
    assert set(doc_sat["payload_columns"]) == {
        "file_name",
        "sha256",
        "byte_size",
        "mime_type",
        "ingested_at",
    }

    # 3. Column triage ledger: 6 entries, 0 dropped columns
    ledger = contract["column_triage_ledger"]
    assert len(ledger) == 6
    cols = {e["column_name"]: e["elm_classification"] for e in ledger}
    assert cols["document_id"] == "BUSINESS_KEY"
    assert cols["file_name"] == "DESCRIPTIVE_PAYLOAD"
    assert cols["sha256"] == "DESCRIPTIVE_PAYLOAD"
    assert cols["byte_size"] == "DESCRIPTIVE_PAYLOAD"
    assert cols["mime_type"] == "DESCRIPTIVE_PAYLOAD"
    assert cols["ingested_at"] == "DESCRIPTIVE_PAYLOAD"
