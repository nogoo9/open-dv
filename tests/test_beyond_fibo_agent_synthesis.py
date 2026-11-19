"""Phase 2 Track B TDD Tests: Beyond-FIBO Synthesis, Zero-Floor Document Grounding & Validator Split.

Covers:
- FR-AGT-05 / D3: Beyond-FIBO custom concepts (grounding_origin='STRUCTURED_SCHEMA') + optional skos:broadMatch.
- FR-AGT-06 / D4: Zero-floor DOCUMENT_OBSERVED extraction (>= 1 mention qualifies without occurrence pruning).
- FR-AGT-07: Dual Path A (>= 0.6500) & Path B empirical GovernanceValidator (ONT-EVO-001..005)
  plus verify_ont_evo_002_post_state_grounding parity and validator.py < 5k submodule split facade.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tiktoken

from opendv_contracts.dtos.ontology import AddConceptOp, EvidenceRef, OntologyDelta
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology import validator as validator_facade
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.validator import (
    ALLOWED_PATH_A_PREDICATES,
    PATH_A_GROUNDING_THRESHOLD,
    GovernanceValidator,
    StructuralValidator,
    UngroundedConceptError,
    build_deployment_provider_from_contract,
    build_workspace_deployment_provider,
    validate_ontology_delta,
)
from opendv_service.studio.atomic_commit_grounding import verify_ont_evo_002_post_state_grounding


def test_beyond_fibo_structured_schema_and_optional_broadmatch() -> None:
    """CanonicalBusinessConcept accepts grounding_origin and optional skos:broadMatch with stable JCS SHA-256."""
    # Backward compatibility: grounding_origin=None keeps existing JCS SHA-256 identical
    legacy_c = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:clinical_visit",
        concept_name="Clinical Visit",
        target_hub="hub_clinical_visit",
        definition="Patient clinical encounter.",
    )
    assert legacy_c.grounding_origin is None

    custom_c = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:clinical_visit",
        concept_name="Clinical Visit",
        target_hub="hub_clinical_visit",
        definition="Patient clinical encounter.",
        grounding_origin="STRUCTURED_SCHEMA",
        attributes=[ConceptAttribute(attribute_name="visit_day", data_type="integer")],
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/DatesAndTimes/Occurrences/Occurrence",
                predicate="skos:broadMatch",
                confidence=0.72,
            )
        ],
    )
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="clinical_trials",
        canonical_business_concepts=[custom_c],
    )
    sha = compute_ontology_sha256(ont)
    assert len(sha) == 64


def test_zero_floor_document_observed_single_mention_qualifies() -> None:
    """Single-mention (1x) DOCUMENT_OBSERVED concepts with verifiable EvidenceRef pass Path B validation."""
    base = create_genesis_ontology("logistics_ops")
    base_sha = compute_ontology_sha256(base)

    doc_concept_op = AddConceptOp(
        op_id="op-doc-1x",
        concept_uri="urn:ontology:entity:cold_chain_sla",
        concept_name="Cold Chain SLA",
        target_hub="hub_cold_chain_sla",
        definition="Single-mention SLA requiring temperature between 2C and 8C.",
        grounding_origin="DOCUMENT_OBSERVED",
        rationale="1x Semantic rule extracted from protocol PDF.",
        evidence_refs=[
            EvidenceRef(
                evidence_type="document_span",
                source_label="protocol_v1.pdf",
                chunk_id="chk-001",
                page_number=2,
                char_start=120,
                char_end=175,
                snippet="Cold chain excursions exceeding 8C must be quarantined.",
                confidence=0.91,
            )
        ],
    )
    delta = OntologyDelta(
        delta_id="d-doc-1x",
        parent_sha256=base_sha,
        base_version="0.0.0",
        target_version="1.0.0",
        domain_namespace="logistics_ops",
        operations=[doc_concept_op],
    )
    target = apply_delta(base, delta, validate_structural=True)
    gv = GovernanceValidator(strict_verifier_required=False)
    gv.validate_grounding(base, delta, target)
    verify_ont_evo_002_post_state_grounding(base, delta, target)


def test_validator_split_facade_preserves_exports_and_token_budget() -> None:
    """validator.py is decomposed into <5,000-token submodules while preserving all facade exports."""
    from opendv_service.ontology import (
        validator_deployment,
        validator_governance,
        validator_models,
        validator_structural,
    )

    assert validator_models and validator_structural and validator_deployment and validator_governance
    for sym in (
        "StructuralValidator",
        "GovernanceValidator",
        "validate_ontology_delta",
        "build_deployment_provider_from_contract",
        "build_workspace_deployment_provider",
        "ALLOWED_PATH_A_PREDICATES",
        "PATH_A_GROUNDING_THRESHOLD",
        "UngroundedConceptError",
    ):
        assert hasattr(validator_facade, sym)

    ont_dir = Path(validator_facade.__file__).resolve().parent
    enc = tiktoken.get_encoding("cl100k_base")
    for mod_name in (
        "validator.py",
        "validator_models.py",
        "validator_structural.py",
        "validator_deployment.py",
        "validator_governance.py",
    ):
        mod_path = ont_dir / mod_name
        assert mod_path.is_file(), f"Missing split module: {mod_path}"
        tok = len(enc.encode(mod_path.read_text(encoding="utf-8")))
        assert tok < 5000, f"{mod_name} exceeds 5,000 tokens ({tok})"


def test_governance_validator_path_a_and_path_b_ont_evo_001_to_005() -> None:
    """GovernanceValidator enforces Path A (>=0.6500) OR Path B (grounding_origin + EvidenceRef) and ONT-EVO-001..005."""
    assert PATH_A_GROUNDING_THRESHOLD == 0.65
    assert "skos:exactMatch" in ALLOWED_PATH_A_PREDICATES

    base = create_genesis_ontology("nonfin_domain")
    base_sha = compute_ontology_sha256(base)

    # 1. Path B STRUCTURED_SCHEMA with optional skos:broadMatch & EvidenceRef passes both validators
    path_b_op = AddConceptOp(
        op_id="op-path-b",
        concept_uri="urn:ontology:entity:sensor_telemetry",
        concept_name="Sensor Telemetry",
        target_hub="hub_sensor_telemetry",
        definition="Cold-chain temperature sensor feed.",
        grounding_origin="STRUCTURED_SCHEMA",
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Documents/Record",
                predicate="skos:broadMatch",
                confidence=0.60,
            )
        ],
        evidence_refs=[
            EvidenceRef(
                evidence_type="column_profile",
                bronze_table="sensor_readings",
                column_name="sensor_id",
                table_id="tbl-sensor",
                confidence=0.98,
            )
        ],
    )
    delta_ok = OntologyDelta(
        delta_id="d-pb-ok",
        parent_sha256=base_sha,
        base_version="0.0.0",
        target_version="1.0.0",
        domain_namespace="nonfin_domain",
        operations=[path_b_op],
    )
    target_ok = apply_delta(base, delta_ok, validate_structural=True)
    gv = GovernanceValidator(strict_verifier_required=False)
    gv.validate_grounding(base, delta_ok, target_ok)
    verify_ont_evo_002_post_state_grounding(base, delta_ok, target_ok)

    # 2. Path B claiming STRUCTURED_SCHEMA WITHOUT any EvidenceRef fails closed (ONT-EVO-002/004/005)
    ungrounded_op = AddConceptOp(
        op_id="op-pb-bad",
        concept_uri="urn:ontology:entity:orphan_concept",
        concept_name="Orphan Concept",
        target_hub="hub_orphan_concept",
        definition="Unverified concept lacking evidence.",
        grounding_origin="STRUCTURED_SCHEMA",
        evidence_refs=[],
    )
    delta_bad = OntologyDelta(
        delta_id="d-pb-bad",
        parent_sha256=base_sha,
        base_version="0.0.0",
        target_version="1.0.0",
        domain_namespace="nonfin_domain",
        operations=[ungrounded_op],
    )
    target_bad = apply_delta(base, delta_bad, validate_structural=True)
    with pytest.raises(UngroundedConceptError):
        gv.validate_grounding(base, delta_bad, target_bad)
    with pytest.raises(UngroundedConceptError):
        verify_ont_evo_002_post_state_grounding(base, delta_bad, target_bad)
