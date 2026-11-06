"""Tests for Ontology Delta DTOs, discriminated unions, and JSON schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    EvidenceRef,
    OntologyDecision,
    OntologyDelta,
    OntologyDiff,
    OntologyProposal,
    RefineDefinitionOp,
)
from opendv_contracts.jcs import dumps_jcs


@pytest.fixture
def ontology_delta_schema() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    schema_path = repo_root / "schemas" / "v1" / "ontology-delta.schema.json"
    assert schema_path.is_file(), f"Schema not found at {schema_path}"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def test_evidence_ref_roundtrip() -> None:
    evidence = EvidenceRef(
        evidence_type="column_profile",
        reference_uri="bronze_feed_supplier.supplier_id",
        snippet="SUPP_1001",
        confidence=0.95,
        bronze_table="feed_supplier",
        column_name="supplier_id",
    )
    data = evidence.model_dump()
    loaded = EvidenceRef.model_validate(data)
    assert loaded == evidence


def test_add_concept_op_serialization() -> None:
    op = AddConceptOp(
        op_id="op-001",
        concept_uri="urn:ontology:entity:supplier",
        concept_name="Supplier",
        elm_archetype="CORE_BUSINESS_CONCEPT",
        definition="An external vendor providing goods or logistics services.",
        target_hub="hub_supplier",
        attributes=["supplier_name", "tax_id"],
        governance_constraints={
            "pii_confidential_attributes": ["tax_id"],
            "required_satellite_split": "sat_supplier_pii",
        },
        key_authority_tiers={
            "tier_1_global_standard": {
                "authority_name": "DUNS",
                "regex_pattern": r"^\d{9}$",
                "bkcc_policy": "GLOBAL_DUNS",
                "require_src_qualifier": False,
            }
        },
        rationale="Vendor tracking requirement.",
        confidence=0.99,
        evidence_refs=[
            EvidenceRef(
                evidence_type="column_profile",
                reference_uri="feed_supplier.tax_id",
                snippet="12-3456789",
                confidence=0.9,
            )
        ],
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_concept"
    restored = AddConceptOp.model_validate(dumped)
    assert restored.concept_uri == "urn:ontology:entity:supplier"
    assert restored.target_hub == "hub_supplier"


def test_add_relationship_op_serialization() -> None:
    op = AddRelationshipOp(
        op_id="op-002",
        relationship_uri="urn:ontology:relationship:supplier_delivery",
        relationship_name="Supplier Delivery",
        target_link="lnk_supplier_delivery",
        source_concept_uri="urn:ontology:entity:supplier",
        target_concept_uri="urn:ontology:entity:delivery",
        endpoints=["urn:ontology:entity:supplier", "urn:ontology:entity:delivery"],
        grain_semantics="One supplier delivers many orders.",
        rationale="Link supplier to physical deliveries.",
        confidence=0.95,
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_relationship"
    restored = AddRelationshipOp.model_validate(dumped)
    assert restored.target_link == "lnk_supplier_delivery"


def test_add_reference_enumeration_op_serialization() -> None:
    op = AddReferenceEnumerationOp(
        op_id="op-003",
        enum_uri="urn:ontology:enum:delivery_status",
        name="delivery_status",
        target_reference_table="ref_delivery_status",
        values=["PENDING", "IN_TRANSIT", "DELIVERED", "REJECTED"],
        allowed_codes=["PENDING", "IN_TRANSIT", "DELIVERED", "REJECTED"],
        description="Delivery fulfillment lifecycle statuses.",
        rationale="Standardize order shipping states.",
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_reference_enumeration"
    restored = AddReferenceEnumerationOp.model_validate(dumped)
    assert len(restored.allowed_codes) == 4


def test_add_key_authority_pattern_op_serialization() -> None:
    op = AddKeyAuthorityPatternOp(
        op_id="op-004",
        concept_uri="urn:ontology:entity:supplier",
        authority_name="GLOBAL_LEI",
        pattern_regex=r"^[A-Z0-9]{20}$",
        tier="TIER_1_GLOBAL_STANDARD",
        description="Global Legal Entity Identifier for institutional suppliers.",
        rationale="Enable cross-border legal entity resolution.",
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_key_authority_pattern"
    restored = AddKeyAuthorityPatternOp.model_validate(dumped)
    assert restored.authority_name == "GLOBAL_LEI"


def test_add_governance_constraint_op_serialization() -> None:
    op = AddGovernanceConstraintOp(
        op_id="op-005",
        concept_uri="urn:ontology:entity:supplier",
        constraint_id="GOV-PII-01",
        constraint_type="PII_SECURITY_ISOLATION",
        pii_confidential_attributes=["bank_account_iban", "tax_identification_number"],
        mnpi_confidential_attributes=[],
        required_satellite_split="sat_supplier_pii",
        description="Mandatory PII satellite split for vendor banking details.",
        rationale="GDPR and banking confidentiality compliance.",
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_governance_constraint"
    restored = AddGovernanceConstraintOp.model_validate(dumped)
    assert "bank_account_iban" in restored.pii_confidential_attributes


def test_refine_definition_op_serialization() -> None:
    op = RefineDefinitionOp(
        op_id="op-006",
        target_uri="urn:ontology:entity:supplier",
        concept_uri="urn:ontology:entity:supplier",
        refined_description=(
            "A verified enterprise or individual vendor fulfilling supplier obligations."
        ),
        additional_attributes=["vendor_tier", "compliance_status"],
        rationale="Expand supplier definitions based on procurement contract review.",
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "refine_definition"
    restored = RefineDefinitionOp.model_validate(dumped)
    assert len(restored.additional_attributes) == 2


def test_add_source_hint_op_serialization() -> None:
    op = AddSourceHintOp(
        op_id="op-007",
        concept_uri="urn:ontology:entity:supplier",
        source_name="ERP_FEED",
        source_system="ERP_FEED",
        table_name="bronze_erp_vendors",
        column_hint="VEND_CODE",
        column_name="VEND_CODE",
        rationale="Map ERP vendor records directly to Supplier concept.",
    )
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_source_hint"
    restored = AddSourceHintOp.model_validate(dumped)
    assert restored.table_name == "bronze_erp_vendors"


def test_ontology_delta_discriminated_union_and_schema_validation(
    ontology_delta_schema: dict,
) -> None:
    delta = OntologyDelta(
        delta_id="delta-wave1-001",
        parent_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        domain_namespace="logistics",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:supplier",
                concept_name="Supplier",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A goods supplier.",
                target_hub="hub_supplier",
            ),
            AddRelationshipOp(
                op_id="op-2",
                relationship_uri="urn:ontology:relationship:supplier_delivery",
                relationship_name="Supplier Delivery",
                target_link="lnk_supplier_delivery",
                endpoints=["urn:ontology:entity:supplier", "urn:ontology:entity:delivery"],
                grain_semantics="Supplier deliveries",
            ),
            AddReferenceEnumerationOp(
                op_id="op-3",
                enum_uri="urn:ontology:enum:status",
                name="status",
                target_reference_table="ref_status",
                values=["ACTIVE", "INACTIVE"],
            ),
            AddKeyAuthorityPatternOp(
                op_id="op-4",
                concept_uri="urn:ontology:entity:supplier",
                authority_name="DUNS",
                tier="TIER_1_GLOBAL_STANDARD",
            ),
            AddGovernanceConstraintOp(
                op_id="op-5",
                concept_uri="urn:ontology:entity:supplier",
                pii_confidential_attributes=["ssn"],
            ),
            RefineDefinitionOp(
                op_id="op-6",
                target_uri="urn:ontology:entity:supplier",
                refined_description="Refined definition.",
            ),
            AddSourceHintOp(
                op_id="op-7",
                concept_uri="urn:ontology:entity:supplier",
                source_name="CRM",
                table_name="crm_suppliers",
            ),
        ],
        metadata={"author": "steward-review", "ticket": "P7-103"},
    )

    dumped = delta.model_dump()
    assert len(dumped["operations"]) == 7

    # JSON Schema Draft 2020-12 validation
    jsonschema.validate(instance=dumped, schema=ontology_delta_schema)

    # Roundtrip from dictionary using discriminated union
    restored = OntologyDelta.model_validate(dumped)
    assert len(restored.operations) == 7
    assert isinstance(restored.operations[0], AddConceptOp)
    assert isinstance(restored.operations[1], AddRelationshipOp)
    assert isinstance(restored.operations[2], AddReferenceEnumerationOp)
    assert isinstance(restored.operations[3], AddKeyAuthorityPatternOp)
    assert isinstance(restored.operations[4], AddGovernanceConstraintOp)
    assert isinstance(restored.operations[5], RefineDefinitionOp)
    assert isinstance(restored.operations[6], AddSourceHintOp)

    # JCS canonicalization
    jcs_str = dumps_jcs(dumped)
    assert "delta-wave1-001" in jcs_str


def test_ontology_proposal_and_decision() -> None:
    delta = OntologyDelta(
        delta_id="delta-prop-01",
        parent_sha256="a" * 64,
        domain_namespace="pe",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:advisor",
                concept_name="Advisor",
                definition="Financial advisory firm",
                target_hub="hub_advisor",
            )
        ],
    )
    proposal = OntologyProposal(
        proposal_id="prop-001",
        wave_id="wave_0",
        recommender_id="testkit-recommender",
        recommender_version="1.0.0",
        base_ontology_sha256="a" * 64,
        delta=delta,
        rationale="Unmapped advisor table detected.",
        confidence_score=0.92,
        coverage_before=0.85,
        coverage_after=1.00,
        diagnostics=["Unmapped table 'advisors' resolved"],
        created_at_utc="2026-11-06T12:00:00Z",
    )
    assert proposal.confidence_score == 0.92

    decision = OntologyDecision(
        decision_id="dec-001",
        proposal_id="prop-001",
        run_id="run-001",
        hold_id="hold-001",
        action="ACCEPT",
        base_ontology_sha256="a" * 64,
        accepted_op_ids=["op-1"],
        rejected_op_ids=[],
        applied_delta_sha256="b" * 64,
        resulting_ontology_sha256="c" * 64,
        decided_by="steward-alice",
        steward_notes="Approved advisor concept.",
    )
    assert decision.action == "ACCEPT"
    assert decision.accepted_op_ids == ["op-1"]


def test_ontology_diff_dto() -> None:
    diff = OntologyDiff(
        base_version="1.0.0",
        target_version="1.1.0",
        base_sha256="a" * 64,
        target_sha256="b" * 64,
        added_concepts=["urn:ontology:entity:supplier"],
        added_relationships=["urn:ontology:relationship:supplier_delivery"],
        added_enumerations=["urn:ontology:enum:status"],
        refined_concepts=["urn:ontology:entity:company"],
        breaking_changes=[],
        diagnostics=["Added 1 concept, 1 relationship, 1 enumeration"],
    )
    assert len(diff.added_concepts) == 1
    assert diff.base_version == "1.0.0"


def test_invalid_operation_type_fails_validation() -> None:
    invalid_data = {
        "op_id": "bad-op",
        "op_type": "delete_concept",  # Non-additive / destructive op
        "concept_uri": "urn:ontology:entity:supplier",
    }
    with pytest.raises(ValidationError):
        OntologyDelta.model_validate(
            {
                "delta_id": "bad-delta",
                "operations": [invalid_data],
            }
        )
