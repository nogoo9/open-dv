"""Task 6B.1 (#230): Tier 2 Boundaries and Edge Cases E2E Tests.

Tests boundary conditions across:
1. 0-byte and empty files (0-byte CSV, empty markdown, blank text)
2. Scaling boundaries (10,000 rows, 100 columns, 50,000 char text)
3. Concept slug 55-char physical identifier budget guard
4. Enum cardinality boundary (>20 distinct values held back from reference enum)
5. Multibyte Unicode & i18n (Nordic, German, CJK, Emojis, NFC normalization)
6. Maximum text lengths (256-char synonyms, 500-char definitions)
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from starlette.testclient import TestClient

from opendv_contracts.dtos.ingestion import TableStructure
from opendv_contracts.dtos.matching import ReferenceConcept
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddReferenceEnumerationOp,
    AddSynonymOp,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_service.construction.deterministic_pipeline import (
    MAX_PHYSICAL_SLUG_CHARS,
    ConstructOptions,
    DeterministicConstructionPipeline,
)
from opendv_service.ontology.matching import Bm25ConceptMatcher
from opendv_service.studio.session_manager import GENESIS_DRAFT_SHA256


def _make_test_matcher() -> Bm25ConceptMatcher:
    concepts = [
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
            concept_name="Legal Entity",
            module="BE",
            definition="An entity that has legal identity and rights under law.",
            synonyms=["Artificial Person"],
        ),
    ]
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts(concepts)
    return matcher


def test_boundary_empty_and_zero_byte_files(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Boundary 1: 0-byte and empty files are strictly rejected with CORRUPT_SOURCE_BLOB."""
    session_id = "boundary-empty-files-01"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. 0-byte CSV upload is rejected with HTTP 422 CORRUPT_SOURCE_BLOB
    resp_zero_csv = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        headers=auth_headers,
    )
    assert resp_zero_csv.status_code == 422
    assert resp_zero_csv.json()["error_code"] == "CORRUPT_SOURCE_BLOB"

    # 2. 0-byte Markdown upload is rejected with HTTP 422 CORRUPT_SOURCE_BLOB
    resp_zero_md = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
        headers=auth_headers,
    )
    assert resp_zero_md.status_code == 422
    assert resp_zero_md.json()["error_code"] == "CORRUPT_SOURCE_BLOB"

    # 3. Blank whitespace markdown is accepted as valid text document
    resp_blank_txt = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("blank.txt", io.BytesIO(b"   \n\n   \t\n"), "text/plain")},
        headers=auth_headers,
    )
    assert resp_blank_txt.status_code == 201


def test_boundary_large_volume_and_scaling(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Boundary 2: Scaling boundaries: 10,000 rows, 100 columns, 50,000 char text."""
    session_id = "boundary-scale-02"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Large Table: 10,000 rows x 4 columns
    csv_buf = io.StringIO()
    csv_buf.write("id,portfolio_name,account_type,balance_usd\n")
    for i in range(10000):
        csv_buf.write(f"{i},Portfolio_{i},MANAGED,{i * 100.5}\n")
    csv_bytes = csv_buf.getvalue().encode("utf-8")

    resp_large_table = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("scale_10k_rows.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers=auth_headers,
    )
    assert resp_large_table.status_code == 201, resp_large_table.text
    assert resp_large_table.json()["size_bytes"] == len(csv_bytes)

    # 2. Wide Table: 100 columns
    headers = [f"col_{j:03d}" for j in range(100)]
    wide_buf = io.StringIO()
    wide_buf.write(",".join(headers) + "\n")
    wide_buf.write(",".join([f"val_{j}" for j in range(100)]) + "\n")
    wide_bytes = wide_buf.getvalue().encode("utf-8")

    resp_wide_table = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("scale_100_cols.csv", io.BytesIO(wide_bytes), "text/csv")},
        headers=auth_headers,
    )
    assert resp_wide_table.status_code == 201, resp_wide_table.text

    # 3. Large text: 50,000 characters
    large_text = (
        "# ENTERPRISE INVESTMENT MEMORANDUM\n\n"
        + ("The fund shall allocate capital according to strict risk parameters. " * 750)
    )
    assert len(large_text) >= 50000
    large_text_bytes = large_text.encode("utf-8")

    resp_large_text = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("scale_50k_chars.md", io.BytesIO(large_text_bytes), "text/markdown")},
        headers=auth_headers,
    )
    assert resp_large_text.status_code == 201, resp_large_text.text


def test_boundary_identifier_budget_guard(tmp_path: Path) -> None:
    """Boundary 3: Concept slug 55-char physical identifier budget guard."""
    assert MAX_PHYSICAL_SLUG_CHARS == 55

    pipeline = DeterministicConstructionPipeline(matcher=_make_test_matcher())
    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Global Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legal_entity",
                concept_name="Legal Entity",
                target_hub="hub_legal_entity",
            )
        ],
    )

    # Case A: Identifier exceeding 55 chars is held back with PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED
    long_col = "super_long_identifier_that_definitely_exceeds_the_fifty_five_character_limit_value"
    assert len(long_col) > 55

    table_long = TableStructure(
        table_id="tbl_long",
        headers=["legal_entity_id", long_col],
        rows=[["LE-1", "100.50"]],
    )
    opts = ConstructOptions(session_id="sess_long", session_dir=tmp_path / "long")
    proposal_long = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table_long],
        options=opts,
    )

    # No AddAttributeOp emitted for the long identifier
    attr_ops_long = [
        op for op in proposal_long.delta.operations if isinstance(op, AddAttributeOp)
    ]
    assert not any(a.attribute_name == long_col for a in attr_ops_long)

    # Staged candidates must record the budget rejection reason
    staged_file = tmp_path / "long" / "evidence" / "staged_candidates.json"
    assert staged_file.is_file()
    staged_data = staged_file.read_text(encoding="utf-8")
    assert "PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED" in staged_data

    # Case B: Slug exactly 55 chars is valid and permitted
    exact_55_col = "a" * 55
    assert len(exact_55_col) == 55

    table_exact = TableStructure(
        table_id="tbl_exact",
        headers=["legal_entity_id", exact_55_col],
        rows=[["LE-1", "200.00"]],
    )
    opts_exact = ConstructOptions(session_id="sess_exact", session_dir=tmp_path / "exact")
    proposal_exact = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table_exact],
        options=opts_exact,
    )
    # The 55-char attribute is accepted without budget rejection
    attr_ops_exact = [
        op for op in proposal_exact.delta.operations if isinstance(op, AddAttributeOp)
    ]
    assert any(a.attribute_name == exact_55_col for a in attr_ops_exact)


def test_boundary_reference_enumeration_cardinality(tmp_path: Path) -> None:
    """Boundary 4: Discrete column > 20 distinct values held back from reference enum."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_test_matcher())
    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Global Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legal_entity",
                concept_name="Legal Entity",
                target_hub="hub_legal_entity",
            )
        ],
    )

    # 1. 25 distinct values (> 20 threshold)
    codes_25 = [f"CODE_{i:03d}" for i in range(1, 26)]
    table_high_card = TableStructure(
        table_id="tbl_high_card",
        headers=["legal_entity_id", "status_code"],
        rows=[["LE-1", codes_25[i - 1]] for i in range(1, 26)],
    )

    opts = ConstructOptions(session_id="sess_cardinality", session_dir=tmp_path / "high")
    proposal_high = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table_high_card],
        options=opts,
    )

    # Over-cardinality enum must NOT be in proposal operations
    enum_ops = [
        op for op in proposal_high.delta.operations if isinstance(op, AddReferenceEnumerationOp)
    ]
    assert len(enum_ops) == 0

    staged_file = tmp_path / "high" / "evidence" / "staged_candidates.json"
    assert "ENUM_CARDINALITY_EXCEEDED" in staged_file.read_text(encoding="utf-8")

    # 2. 4 distinct values (<= 20 threshold)
    table_low_card = TableStructure(
        table_id="tbl_low_card",
        headers=["legal_entity_id", "risk_level"],
        rows=[["LE-1", f"LVL_{i % 4}"] for i in range(1, 20)],
    )
    opts_low = ConstructOptions(session_id="sess_card_low", session_dir=tmp_path / "low")
    proposal_low = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table_low_card],
        options=opts_low,
    )
    low_enum_ops = [
        op for op in proposal_low.delta.operations if isinstance(op, AddReferenceEnumerationOp)
    ]
    assert len(low_enum_ops) == 1


def test_boundary_internationalization_multibyte_unicode(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Boundary 5: Multibyte Unicode & i18n (Nordic, German, CJK, Emojis, NFC normalization)."""
    session_id = "boundary-i18n-unicode-05"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Multibyte characters in definitions, concepts, and synonyms
    nordic_text = "Företag och Räkenskaper för nordiska investerare (DK/SE/NO: æ, ø, å)."
    german_text = "Vermögensverwaltung & Großaktionäre mit Überweisungsträgern (ä, ö, ü, ß)."
    cjk_text = "投資ファンドおよび資産管理機構 (東京・上海・シンガポール)."
    emoji_text = "🏦 Depository Banking & 💼 Wealth Portfolio Holdings."

    # Verify Unicode NFC normalization ensures deterministic byte representations
    nfc_nordic = unicodedata.normalize("NFC", nordic_text)
    nfc_german = unicodedata.normalize("NFC", german_text)
    nfc_cjk = unicodedata.normalize("NFC", cjk_text)
    nfc_emoji = unicodedata.normalize("NFC", emoji_text)

    delta_payload = {
        "delta_id": "delta-i18n-01",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:münchen_üõ",
        "rationale": f"i18n Multi-Lingual Grounding: {nfc_german}",
        "operations": [
            {
                "op_id": "op-cjk-fund",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:asian_fund",
                "concept_name": "AsianFund",
                "definition": f"{nfc_cjk} - {nfc_emoji}",
                "target_hub": "hub_asian_fund",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:asian_fund",
                        "confidence": 0.85,
                    }
                ],
            },
            {
                "op_id": "op-nordic-holding",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:nordic_holding",
                "concept_name": "NordicHolding",
                "definition": f"{nfc_nordic}",
                "target_hub": "hub_nordic_holding",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:nordic_holding",
                        "confidence": 0.78,
                    }
                ],
            },
        ],
    }

    resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{GENESIS_DRAFT_SHA256}"'},
    )
    assert resp.status_code == 200, resp.text
    commit_data = resp.json()
    assert commit_data["target_version"] in ("1.0.0", "v1.0.0")

    # Verify export preserves Unicode content without mojibake (YAML & JSON roundtrips)
    exp_yaml = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=yaml",
        headers=auth_headers,
    )
    assert exp_yaml.status_code == 200
    yaml_text = exp_yaml.content.decode("utf-8")
    parsed_yaml = yaml.safe_load(yaml_text)
    cjk_def = parsed_yaml["canonical_business_concepts"][0]["definition"]
    nordic_def = parsed_yaml["canonical_business_concepts"][1]["definition"]
    assert "ファンド" in cjk_def
    assert "🏦" in cjk_def
    assert "Företag" in nordic_def

    # Also verify JSON export roundtrips unescaped
    exp_json = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=json",
        headers=auth_headers,
    )
    assert exp_json.status_code == 200
    json_data = exp_json.json()
    cjk_json = json_data["canonical_business_concepts"][0]["definition"]
    nordic_json = json_data["canonical_business_concepts"][1]["definition"]
    assert "ファンド" in cjk_json
    assert "🏦" in cjk_json
    assert "Företag" in nordic_json


def test_boundary_extreme_text_lengths(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Boundary 6: Maximum text lengths: 256-char synonyms, 500-char definitions."""
    session_id = "boundary-text-lengths-06"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Exactly 500-character definition (allowed maximum)
    exact_500_def = ("Corporate enterprise with complex governance structures " * 10)[:500]
    assert len(exact_500_def) == 500

    # 2. Exactly 256-character synonym (allowed maximum for AddSynonymOp)
    exact_256_synonym = ("Alternative enterprise designation label for entity " * 10)[:256]
    assert len(exact_256_synonym) == 256

    # 3. Valid boundary commit
    delta_payload = {
        "delta_id": "delta-max-lengths-01",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:compliance",
        "rationale": "Testing maximum allowable field lengths",
        "operations": [
            {
                "op_id": "op-add-corp",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:large_corporation",
                "concept_name": "LargeCorporation",
                "definition": exact_500_def,
                "target_hub": "hub_large_corporation",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:corporation",
                        "confidence": 0.88,
                    }
                ],
            },
            {
                "op_id": "op-add-syn-256",
                "op_type": "add_synonym",
                "concept_uri": "urn:ontology:entity:large_corporation",
                "synonym": exact_256_synonym,
            },
        ],
    }
    resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{GENESIS_DRAFT_SHA256}"'},
    )
    assert resp.status_code == 200, resp.text

    # 4. Exceeding boundary: 257-character synonym rejected by Pydantic validation
    too_long_synonym = exact_256_synonym + "X"
    assert len(too_long_synonym) == 257

    with pytest.raises(ValidationError) as exc_info:
        AddSynonymOp(
            op_id="op-too-long",
            concept_uri="urn:ontology:entity:large_corporation",
            synonym=too_long_synonym,
        )
    assert "String should have at most 256 characters" in str(exc_info.value)
