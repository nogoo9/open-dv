"""Unit tests for the Deterministic Fallback Construction Pipeline.

Tests Task 5A.2 / Issue #212:
- Zero-API-key offline construction from evidence chunks and tabular structures.
- Schema Heuristics Engine (Hub CBCs, Links, Ref Enums, Satellites).
- Pinned BM25 grounding lookup against EDM Council FIBO.
- Holding area evidence/staged_candidates.json for ungrounded terms.
- Dependency Closure Guard (transitively holding back Links/Sats of ungrounded Hubs).
- Physical identifier budget guard (capping slugs at 55 chars).
- Clarification answer incorporation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.matching import ReferenceConcept
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    OntologyProposal,
)
from opendv_contracts.spi.grounding import GroundingVerificationResult
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)
from opendv_service.construction.models import (
    ConstructOptions,
    StagedCandidatesFile,
)
from opendv_service.ontology.matching import Bm25ConceptMatcher, FiboPackGroundingVerifier


def _make_sample_base_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="finance-data-office",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:initial_seed",
                concept_name="Initial Seed",
                target_hub="hub_initial_seed",
            )
        ],
    )


def _make_mock_matcher() -> Bm25ConceptMatcher:
    concepts = [
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
            concept_name="Legal Entity",
            module="BE",
            definition="An entity that has legal identity and rights under law.",
            synonyms=["Artificial Person"],
        ),
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/BoardAgreement",
            concept_name="Board Agreement",
            module="BE",
            definition="Formal legally binding agreement between members of the Board.",
            synonyms=[],
        ),
        ReferenceConcept(
            concept_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/Debt/Bond",
            concept_name="Bond",
            module="SEC",
            definition="A debt instrument issued by an entity.",
            synonyms=[],
        ),
    ]
    matcher = Bm25ConceptMatcher()
    matcher.index_concepts(concepts)
    return matcher


def test_deterministic_pipeline_schema_heuristics(tmp_path: Path) -> None:
    """Verify table heuristics: _id -> Hub, FK pairs -> Link, discrete -> Ref Enum,
    non-key -> Sat.
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Table 1: tbl_entities (1 key: legal_entity_id, 1 non-key: amount)
    table_entities = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id", "amount"],
        rows=[
            ["LE-001", "100000"],
            ["LE-002", "250000"],
        ],
    )
    # Table 2: tbl_positions (2 keys: legal_entity_id, bond_id, discrete: status_code)
    table_positions = TableStructure(
        table_id="tbl_positions",
        headers=["legal_entity_id", "bond_id", "status_code"],
        rows=[
            ["LE-001", "BOND-99", "ACTIVE"],
            ["LE-002", "BOND-88", "PENDING"],
        ],
    )

    opts = ConstructOptions(session_id="s1", session_dir=tmp_path)
    start = time.monotonic()
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table_entities, table_positions],
        options=opts,
    )
    elapsed = time.monotonic() - start

    # Must complete in < 2.5s (budget requirement)
    assert elapsed < 2.5, f"Pipeline took {elapsed:.3f}s, exceeding 2.5s budget"

    assert isinstance(proposal, OntologyProposal)
    delta = proposal.delta
    assert delta.author == "deterministic:pipeline"

    # Hub CBCs: legal_entity and bond must be accepted because they match FIBO
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    concept_uris = {op.concept_uri for op in concept_ops}
    assert "urn:ontology:entity:legal_entity" in concept_uris
    assert "urn:ontology:entity:bond" in concept_uris

    # External mappings must be present with >= 0.6500 confidence
    mapping_ops = [op for op in delta.operations if isinstance(op, AddExternalMappingOp)]
    assert len(mapping_ops) >= 2
    for m in mapping_ops:
        assert m.confidence >= 0.6500
        assert m.scheme == "fibo"

    # Link: co-occurring keys legal_entity_id and bond_id
    link_ops = [op for op in delta.operations if isinstance(op, AddRelationshipOp)]
    assert len(link_ops) == 1
    assert link_ops[0].relationship_name == "lnk_legal_entity_bond"

    # Reference Enumeration: status_code
    enum_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(enum_ops) == 1
    assert enum_ops[0].name == "ref_status"

    enum_code_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    codes = {op.code for op in enum_code_ops}
    assert "ACTIVE" in codes
    assert "PENDING" in codes

    # Satellite attribute: amount
    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert len(attr_ops) >= 1
    assert any(a.attribute_name == "amount" for a in attr_ops)


def test_grounding_hold_back_and_dependency_closure(tmp_path: Path) -> None:
    """Verify that ungrounded concepts and dependent links are held back
    in staged_candidates.json.
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Table with legal_entity_id (grounded) and proprietary_widget_id (ungrounded)
    table = TableStructure(
        table_id="tbl_holdback",
        headers=["legal_entity_id", "proprietary_widget_id", "widget_serial"],
        rows=[
            ["LE-001", "WIDGET-01", "SN-9999"],
        ],
    )

    opts = ConstructOptions(session_id="sess_holdback", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )

    delta = proposal.delta
    concept_uris = {op.concept_uri for op in delta.operations if isinstance(op, AddConceptOp)}
    assert "urn:ontology:entity:legal_entity" in concept_uris
    # proprietary_widget must NOT be in delta
    assert "urn:ontology:entity:proprietary_widget" not in concept_uris

    # widget_serial must NOT be emitted as AddAttributeOp on hub_legal_entity
    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert not any(a.attribute_name == "widget_serial" for a in attr_ops)

    # Link lnk_legal_entity_proprietary_widget must NOT be in delta (dependency closure guard!)
    link_ops = [op for op in delta.operations if isinstance(op, AddRelationshipOp)]
    assert len(link_ops) == 0

    # Inspect staged_candidates.json
    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    assert staged_file.exists()

    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    staged_reasons = {c.reason_held_back for c in payload.staged_candidates}
    assert "GROUNDING_THRESHOLD_UNMET" in staged_reasons
    assert "DEPENDENCY_CLOSURE_UNMET" in staged_reasons

    # Check that widget was held back for grounding threshold
    widget_cands = [c for c in payload.staged_candidates if "widget" in c.suggested_hub]
    assert len(widget_cands) >= 1
    assert any(c.reason_held_back == "GROUNDING_THRESHOLD_UNMET" for c in widget_cands)

    # Check that widget_serial was held back with DEPENDENCY_CLOSURE_UNMET
    widget_attr_cands = [
        c for c in payload.staged_candidates if c.candidate_id == "staged-attr-widget_serial"
    ]
    assert len(widget_attr_cands) == 1
    assert widget_attr_cands[0].reason_held_back == "DEPENDENCY_CLOSURE_UNMET"


def test_physical_identifier_budget_guard(tmp_path: Path) -> None:
    """Verify that identifier slugs exceeding 55 chars are held back."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Create a 60-character column name
    long_col = "super_long_identifier_that_definitely_exceeds_the_fifty_five_character_limit_key"
    table = TableStructure(
        table_id="tbl_long",
        headers=[long_col],
        rows=[["VAL-1"]],
    )

    opts = ConstructOptions(session_id="sess_long", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )

    # Must not contain long concept in delta
    assert len([op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]) == 0

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    assert any(
        c.reason_held_back == "PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED"
        for c in payload.staged_candidates
    )


def test_clarification_answer_override(tmp_path: Path) -> None:
    """Verify that human clarification answers override BM25 ranking and ground concept."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Column that normally wouldn't match FIBO with >= 0.6500
    table = TableStructure(
        table_id="tbl_ambiguous",
        headers=["custom_advisor_id"],
        rows=[["ADV-1"]],
    )

    # Provide steward answer mapping custom_advisor to FIBO Legal Entity
    clarification_answers = {
        "preferred_mappings": {
            "custom_advisor": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
        }
    }

    opts = ConstructOptions(session_id="sess_clarify", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        clarification_answers=clarification_answers,
        options=opts,
    )

    # Because steward provided answer, concept is grounded and accepted into delta!
    delta = proposal.delta
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    assert len(concept_ops) == 1
    assert concept_ops[0].concept_uri == "urn:ontology:entity:custom_advisor"

    mapping_ops = [op for op in delta.operations if isinstance(op, AddExternalMappingOp)]
    assert len(mapping_ops) == 1
    assert mapping_ops[0].confidence == 1.0
    assert (
        mapping_ops[0].target_uri
        == "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    )


def test_withheld_attribute_structured_schema(tmp_path: Path) -> None:
    """Verify withheld attribute is stored as dict and validates through StagedCandidatesFile."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Table where the sole hub proprietary_widget is ungrounded and has a satellite attribute
    table = TableStructure(
        table_id="tbl_withheld_attr",
        headers=["proprietary_widget_id", "widget_weight"],
        rows=[["W-1", "10.5"], ["W-2", "20.3"]],
    )

    opts = ConstructOptions(session_id="sess_attr_schema", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )

    assert len(proposal.delta.operations) == 0

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    assert staged_file.exists()

    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    cands = [c for c in payload.staged_candidates if "proprietary_widget" in c.suggested_hub]
    assert len(cands) == 1
    assert len(cands[0].attributes) == 1
    assert cands[0].attributes[0]["attribute_name"] == "widget_weight"
    assert cands[0].attributes[0]["column"] == "widget_weight"


def test_proposal_and_delta_id_deterministic_and_distinct() -> None:
    """Verify proposal_id and delta_id are deterministic and distinct for different operations."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    t1 = TableStructure(
        table_id="tbl_1",
        headers=["legal_entity_id"],
        rows=[["LE-1"]],
    )
    t2 = TableStructure(
        table_id="tbl_2",
        headers=["bond_id"],
        rows=[["B-1"]],
    )

    p1_a = pipeline.construct_proposal(base_ontology=base_ont, tables=[t1])
    p1_b = pipeline.construct_proposal(base_ontology=base_ont, tables=[t1])
    p2 = pipeline.construct_proposal(base_ontology=base_ont, tables=[t2])

    # Determinism: same input produces same IDs
    assert p1_a.proposal_id == p1_b.proposal_id
    assert p1_a.delta.delta_id == p1_b.delta.delta_id

    # Distinctness: different ops produce different IDs
    assert p1_a.proposal_id != p2.proposal_id
    assert p1_a.delta.delta_id != p2.delta.delta_id


def test_multitable_deduplication() -> None:
    """Verify operations across multiple tables are deduplicated via emit_op and base ontology."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Two entity tables that both have legal_entity_id, status_code, and amount
    t1 = TableStructure(
        table_id="tbl_pos_1",
        headers=["legal_entity_id", "status_code", "amount"],
        rows=[["LE-1", "ACTIVE", "100"]],
    )
    t2 = TableStructure(
        table_id="tbl_pos_2",
        headers=["legal_entity_id", "status_code", "amount"],
        rows=[["LE-2", "PENDING", "200"]],
    )
    t_lnk = TableStructure(
        table_id="tbl_links",
        headers=["legal_entity_id", "bond_id"],
        rows=[["LE-1", "B-1"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[t1, t2, t_lnk])
    delta = proposal.delta

    # Concepts: legal_entity and bond must each appear exactly once
    concept_names = [op.concept_name for op in delta.operations if isinstance(op, AddConceptOp)]
    assert concept_names.count("Legal Entity") == 1
    assert concept_names.count("Bond") == 1

    # Link: lnk_legal_entity_bond must appear exactly once
    links = [op.relationship_name for op in delta.operations if isinstance(op, AddRelationshipOp)]
    assert links.count("lnk_legal_entity_bond") == 1

    # Reference Enum: ref_status must appear exactly once
    enums = [op.name for op in delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert enums.count("ref_status") == 1

    # Both ACTIVE and PENDING must reach delta without being dropped (MAJ-01)
    enum_codes = [op.code for op in delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert "ACTIVE" in enum_codes
    assert "PENDING" in enum_codes
    assert enum_codes.count("ACTIVE") == 1
    assert enum_codes.count("PENDING") == 1

    # Attribute: amount must appear exactly once
    attrs = [op.attribute_name for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert attrs.count("amount") == 1


def test_enum_code_addition_to_existing_base_enum() -> None:
    """Verify new discrete codes are proposed for enums that already exist in
    base ontology (MAJ-01).
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    from opendv_core.models.graph_a import ReferenceEnumeration

    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:initial_seed",
                concept_name="Initial Seed",
                target_hub="hub_initial_seed",
            )
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:status",
                target_reference_table="ref_status",
                allowed_codes=["ACTIVE"],
            )
        ],
    )

    # Table with status_code containing existing 'ACTIVE' and new 'PENDING'
    table = TableStructure(
        table_id="tbl_status_updates",
        headers=["status_code"],
        rows=[["ACTIVE"], ["PENDING"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta

    # AddReferenceEnumerationOp must NOT be proposed (enum already exists in base!)
    enum_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(enum_ops) == 0

    # Only 'PENDING' should be proposed (ACTIVE already exists in base ontology allowed_codes!)
    code_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert len(code_ops) == 1
    assert code_ops[0].code == "PENDING"
    assert code_ops[0].enumeration_name == "ref_status"


def test_satellite_attribute_budget_guard(tmp_path: Path) -> None:
    """Verify that attribute names exceeding 55 chars are held back (SUG-03)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    long_attr = (
        "super_long_attribute_name_that_definitely_exceeds_the_fifty_five_character_limit_value"
    )
    table = TableStructure(
        table_id="tbl_long_attr",
        headers=["legal_entity_id", long_attr],
        rows=[["LE-1", "100.50"]],
    )

    opts = ConstructOptions(session_id="sess_attr_budget", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )

    # Long attribute must NOT be in delta
    attr_ops = [op for op in proposal.delta.operations if isinstance(op, AddAttributeOp)]
    assert not any(a.attribute_name == long_attr for a in attr_ops)

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    assert any(
        c.reason_held_back == "PHYSICAL_IDENTIFIER_BUDGET_EXCEEDED"
        and c.candidate_id == f"staged-attr-{long_attr}"
        for c in payload.staged_candidates
    )


def test_enum_cardinality_cap_exceeded_held_back(tmp_path: Path) -> None:
    """Verify columns with > 20 distinct codes are held back to prevent delta flooding (MAJ-01)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # postal_code with 30 distinct values (> MAX_ENUM_CARDINALITY of 20)
    codes = [f"ZIP-{i:03d}" for i in range(1, 31)]
    table = TableStructure(
        table_id="tbl_postcodes",
        headers=["legal_entity_id", "postal_code"],
        rows=[["LE-1", c] for c in codes],
    )

    opts = ConstructOptions(session_id="sess_enum_cap", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )

    # Ref enum and codes must NOT be in delta
    enum_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(enum_ops) == 0
    code_ops = [op for op in proposal.delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    assert len(code_ops) == 0

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    assert any(
        c.reason_held_back == "ENUM_CARDINALITY_EXCEEDED" and "postal_code" in c.candidate_id
        for c in payload.staged_candidates
    )


def test_single_row_free_text_column_is_satellite_not_enum() -> None:
    """Verify single-row free-text columns remain satellites and do not become enums (MAJ-01)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # 1-row table with legal_entity_id and customer_name='Acme Corp'
    table = TableStructure(
        table_id="tbl_single_row_sample",
        headers=["legal_entity_id", "customer_name"],
        rows=[["LE-001", "Acme Corp"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta

    # customer_name must be an AddAttributeOp, NOT an AddReferenceEnumerationOp
    enum_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    assert len(enum_ops) == 0
    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert any(a.attribute_name == "customer_name" for a in attr_ops)


def test_external_mapping_idempotency_against_base_ontology() -> None:
    """Verify existing external mappings on base concepts are not re-proposed (MIN-05)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    from opendv_core.models.graph_a import ExternalMapping

    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legal_entity",
                concept_name="Legal Entity",
                target_hub="hub_legal_entity",
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                        confidence=1.0,
                    )
                ],
            )
        ],
    )

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id"],
        rows=[["LE-001"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta

    # AddExternalMappingOp must NOT be proposed again since it already exists in base_ont!
    mapping_ops = [op for op in delta.operations if isinstance(op, AddExternalMappingOp)]
    assert len(mapping_ops) == 0


def test_target_hub_collision_against_base_ontology_is_not_duplicated() -> None:
    """Verify that when base ontology already contains a hub with target_hub="hub_legal_entity",
    even with a different URI, AddConceptOp is not duplicated and dependent ops use
    existing URI (MAJ-02).
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legal_person",
                concept_name="Legal Person Seed",
                target_hub="hub_legal_entity",
            )
        ],
    )

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id", "amount"],
        rows=[["LE-001", "1000"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta

    # AddConceptOp for hub_legal_entity must NOT be proposed (target_hub already exists!)
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    assert not any(op.target_hub == "hub_legal_entity" for op in concept_ops)

    # Any mapping or attribute op must point to existing base URI (MAJ-02)
    mapping_ops = [op for op in delta.operations if isinstance(op, AddExternalMappingOp)]
    for m in mapping_ops:
        assert m.concept_uri == "urn:ontology:entity:legal_person"

    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    for a in attr_ops:
        assert a.concept_uri == "urn:ontology:entity:legal_person"

    # Assert that EVERY operation's concept_uri resolves to a concept in either base_ont or delta
    base_and_delta_uris = {c.concept_uri for c in base_ont.canonical_business_concepts} | {
        op.concept_uri for op in concept_ops
    }

    for op in delta.operations:
        if hasattr(op, "concept_uri"):
            assert op.concept_uri in base_and_delta_uris, (
                f"Dangling concept_uri '{op.concept_uri}' in {op}"
            )


def test_unverified_preferred_mapping_staged_not_substituted(tmp_path: Path) -> None:
    """Verify that unverified preferred mapping URI is staged with PREFERRED_MAPPING_UNVERIFIED
    and not automatically substituted with BM25 top match (MIN-03).
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id"],
        rows=[["LE-001"]],
    )

    opts = ConstructOptions(
        session_id="sess_unverified",
        session_dir=tmp_path,
        preferred_mappings={
            "legal_entity": "https://spec.edmcouncil.org/fibo/ontology/Fake/NonExistent"
        },
    )

    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )
    delta = proposal.delta

    # Must NOT have accepted concept or mapping for legal_entity
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    assert len(concept_ops) == 0

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    assert staged_file.exists()
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    staged_reasons = {c.reason_held_back for c in payload.staged_candidates}
    assert "PREFERRED_MAPPING_UNVERIFIED" in staged_reasons


def test_multi_key_table_satellites_withheld_under_dependency_closure(tmp_path: Path) -> None:
    """Verify that multi-key table with one ungrounded key withholds all non-key columns
    under DEPENDENCY_CLOSURE_UNMET and does not emit them on the grounded key (MAJ-01).
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Table with legal_entity_id (grounded), custom_device_id (ungrounded), and device_setting
    table = TableStructure(
        table_id="tbl_device_allocations",
        headers=["legal_entity_id", "custom_device_id", "device_setting"],
        rows=[["LE-001", "DEV-01", "HIGH_SECURITY"]],
    )

    opts = ConstructOptions(session_id="sess_dep_closure", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(
        base_ontology=base_ont,
        tables=[table],
        options=opts,
    )
    delta = proposal.delta

    # device_setting must NOT be emitted as AddAttributeOp
    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert not any(a.attribute_name == "device_setting" for a in attr_ops)

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    assert staged_file.exists()
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    dev_attr = next(
        (c for c in payload.staged_candidates if "device_setting" in c.candidate_id),
        None,
    )
    assert dev_attr is not None
    assert dev_attr.reason_held_back == "DEPENDENCY_CLOSURE_UNMET"


def test_verifier_binding_accepted() -> None:
    """Verify that when FiboPackGroundingVerifier is attached and accepts,
    Hub and mappings are emitted.
    """
    verifier = FiboPackGroundingVerifier()
    pipeline = DeterministicConstructionPipeline(verifier=verifier)
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id"],
        rows=[["LE-001"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    assert any(op.target_hub == "hub_legal_entity" for op in concept_ops)


def test_verifier_binding_rejected_stages_candidate(tmp_path: Path) -> None:
    """Verify that when verifier rejects a candidate mapping (is_grounded=False),
    the candidate is staged with VERIFIER_REJECTED even if score is high (MAJ-01).
    """

    class StrictRejectionVerifier(FiboPackGroundingVerifier):
        def verify_grounding(self, **kwargs: Any) -> GroundingVerificationResult:
            return GroundingVerificationResult(
                is_grounded=False,
                score=0.9999,
                diagnostics=["Policy rejection: simulated verification failure."],
            )

    pipeline = DeterministicConstructionPipeline(verifier=StrictRejectionVerifier())
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id"],
        rows=[["LE-001"]],
    )

    opts = ConstructOptions(session_id="sess_rej", session_dir=tmp_path)
    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table], options=opts)
    delta = proposal.delta

    # Must NOT have emitted AddConceptOp or AddExternalMappingOp
    assert not any(isinstance(op, AddConceptOp) for op in delta.operations)
    assert not any(isinstance(op, AddExternalMappingOp) for op in delta.operations)

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    assert staged_file.exists()
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    staged_cands = [c for c in payload.staged_candidates if "legal_entity" in c.suggested_hub]
    assert len(staged_cands) == 1
    assert staged_cands[0].reason_held_back == "VERIFIER_REJECTED"


def test_text_chunk_candidate_extraction() -> None:
    """Verify capitalized terms from EvidenceChunk text are extracted when no tables present."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    chunks = [
        EvidenceChunk(
            chunk_id="chk-doc-1",
            document_id="doc-1",
            text="The Legal Entity entered into a Board Agreement pursuant to the Debt Bond.",
            is_table=False,
        )
    ]

    proposal = pipeline.construct_proposal(
        base_ontology=base_ont, evidence_chunks=chunks, tables=[]
    )
    delta = proposal.delta
    concept_names = {op.concept_name for op in delta.operations if isinstance(op, AddConceptOp)}
    assert "Legal Entity" in concept_names or "Debt Bond" in concept_names


def test_table_embedded_in_chunk_metadata() -> None:
    """Verify table structure embedded in EvidenceChunk metadata is deserialized (MIN-07)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    chunks = [
        EvidenceChunk(
            chunk_id="chk-tbl-1",
            document_id="doc-tbl-1",
            text="[Table Data]",
            is_table=True,
            metadata={
                "table_data": {
                    "table_id": "tbl_embedded",
                    "headers": ["legal_entity_id", "amount"],
                    "rows": [["LE-001", "50000"]],
                }
            },
        )
    ]

    proposal = pipeline.construct_proposal(
        base_ontology=base_ont, evidence_chunks=chunks, tables=[]
    )
    delta = proposal.delta
    concept_ops = [op for op in delta.operations if isinstance(op, AddConceptOp)]
    assert any(op.target_hub == "hub_legal_entity" for op in concept_ops)
    attr_ops = [op for op in delta.operations if isinstance(op, AddAttributeOp)]
    assert any(a.attribute_name == "amount" for a in attr_ops)


def test_occ_precondition_check() -> None:
    """Verify ConstructOptions.if_match raises ValueError when it mismatches base SHA (MIN-03)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_entities",
        headers=["legal_entity_id"],
        rows=[["LE-001"]],
    )

    opts = ConstructOptions(
        if_match="0000000000000000000000000000000000000000000000000000000000000000"
    )
    with pytest.raises(ValueError, match="OCC precondition failed"):
        pipeline.construct_proposal(base_ontology=base_ont, tables=[table], options=opts)


def test_bare_id_hub_derivation_strips_table_prefixes() -> None:
    """Verify bare id column derives hub_bond (not hub_tbl_bond) via removeprefix (MAJ-04)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_bond",
        headers=["id", "coupon_rate"],
        rows=[["1", "5.5"]],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    concept_ops = [op for op in proposal.delta.operations if isinstance(op, AddConceptOp)]
    assert any(op.target_hub == "hub_bond" for op in concept_ops)
    assert not any(op.target_hub == "hub_tbl_bond" for op in concept_ops)


def test_distinct_enum_columns_in_same_table() -> None:
    """Verify account_type and account_status create separate enums.

    Ensures distinct reference tables without merging codes (MAJ-03).
    """
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    table = TableStructure(
        table_id="tbl_accounts",
        headers=["account_id", "account_type", "account_status"],
        rows=[
            ["1", "SAVINGS", "ACTIVE"],
            ["2", "CHECKING", "CLOSED"],
            ["3", "SAVINGS", "ACTIVE"],
            ["4", "MONEY_MARKET", "SUSPENDED"],
        ],
    )

    proposal = pipeline.construct_proposal(base_ontology=base_ont, tables=[table])
    delta = proposal.delta

    enum_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumerationOp)]
    enum_names = {op.name for op in enum_ops}
    assert "ref_account_type" in enum_names
    assert "ref_account_status" in enum_names

    code_ops = [op for op in delta.operations if isinstance(op, AddReferenceEnumCodeOp)]
    type_codes = {op.code for op in code_ops if op.enumeration_name == "ref_account_type"}
    status_codes = {op.code for op in code_ops if op.enumeration_name == "ref_account_status"}

    assert type_codes == {"CHECKING", "MONEY_MARKET", "SAVINGS"}
    assert status_codes == {"ACTIVE", "CLOSED", "SUSPENDED"}


def test_reference_enum_identifier_collision_staged(tmp_path: Path) -> None:
    """Verify enum collision from distinct columns stages candidate (MAJ-03)."""
    pipeline = DeterministicConstructionPipeline(matcher=_make_mock_matcher())
    base_ont = _make_sample_base_ontology()

    # Two tables with conflicting columns both trying to emit into ref_status
    table1 = TableStructure(
        table_id="tbl_orders",
        headers=["order_id", "status_code"],
        rows=[["1", "NEW"], ["2", "SHIPPED"]],
    )
    table2 = TableStructure(
        table_id="tbl_invoices",
        headers=["invoice_id", "status"],
        rows=[
            ["1", "PAID"],
            ["2", "UNPAID"],
            ["3", "PAID"],
            ["4", "UNPAID"],
            ["5", "PAID"],
            ["6", "UNPAID"],
            ["7", "PAID"],
            ["8", "UNPAID"],
        ],
    )

    opts = ConstructOptions(session_id="s_enum_col", session_dir=tmp_path)
    pipeline.construct_proposal(base_ontology=base_ont, tables=[table1, table2], options=opts)

    staged_file = tmp_path / "evidence" / "staged_candidates.json"
    payload = StagedCandidatesFile.model_validate_json(staged_file.read_text(encoding="utf-8"))
    assert any(
        c.reason_held_back == "REFERENCE_ENUM_IDENTIFIER_COLLISION"
        for c in payload.staged_candidates
    )


def test_construction_package_import_smoke() -> None:
    """Verify package imports work without error (MAJ-02)."""
    from opendv_service.construction.deterministic_pipeline import (
        DeterministicConstructionPipeline,
    )
    from opendv_service.construction.models import (
        ConstructOptions,
        StagedCandidate,
        StagedCandidatesFile,
    )
    from opendv_service.construction.utils import atomic_write_text

    assert DeterministicConstructionPipeline is not None
    assert ConstructOptions is not None
    assert StagedCandidate is not None
    assert StagedCandidatesFile is not None
    assert atomic_write_text is not None
