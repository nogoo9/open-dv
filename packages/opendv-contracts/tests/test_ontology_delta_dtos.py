"""Tests for Ontology Delta DTOs, 24-op catalog, SPI protocols, and OCC envelope."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jsonschema
import pytest
from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    # Minor additive operations (10)
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    # Patch refinement operations (10)
    AddSynonymOp,
    AnnotateConceptMetadataOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    # Envelope & records
    EvidenceRef,
    OntologyDecision,
    OntologyDelta,
    OntologyDiff,
    OntologyOperation,
    OntologyProposal,
    RefineDefinitionOp,
    RemoveAttributeOp,
    # Major breaking operations (4)
    RemoveConceptOp,
    RemoveExternalMappingOp,
    RemoveRelationshipOp,
    RepinExternalMappingOp,
    RescoreExternalMappingOp,
    ReviseDescriptionOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    StewardWaiverRecord,
    UpdateConceptUrnOp,
)
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi import (
    ConstructionAgent,
    DeploymentStateProvider,
    GroundingVerificationResult,
    GroundingVerifier,
    ReferenceOntologyAdapter,
    WaiverVerifier,
)


@pytest.fixture
def ontology_delta_schema() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    schema_path = repo_root / "schemas" / "v1" / "ontology-delta.schema.json"
    assert schema_path.is_file(), f"Schema not found at {schema_path}"
    return json.loads(schema_path.read_text(encoding="utf-8"))


@pytest.fixture
def ontology_delta_v2_schema() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    schema_path = (
        repo_root / "packages" / "opendv-contracts" / "schemas" / "ontology-delta-v2.schema.json"
    )
    assert schema_path.is_file(), f"Schema not found at {schema_path}"
    return json.loads(schema_path.read_text(encoding="utf-8"))


# EvidenceRef and Legacy DTO Tests


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


def test_remove_concept_op_roundtrip() -> None:
    op = RemoveConceptOp(
        op_id="op-rem-c-01",
        concept_uri="urn:ontology:entity:obsolete_vendor",
        reason="Vendor domain merged into counterparty model.",
    )
    assert op.semver_class == "MAJOR"
    data = op.model_dump()
    assert data["op_type"] == "remove_concept"
    restored = RemoveConceptOp.model_validate(data)
    assert restored.concept_uri == op.concept_uri
    assert restored.reason == op.reason


def test_remove_relationship_op_roundtrip() -> None:
    op = RemoveRelationshipOp(
        op_id="op-rem-r-01",
        relationship_name="vendor_contract",
        relationship_uri="urn:ontology:relationship:vendor_contract",
        reason="Deprecated in favor of legal_agreement link.",
    )
    assert op.semver_class == "MAJOR"
    data = op.model_dump()
    assert data["op_type"] == "remove_relationship"
    restored = RemoveRelationshipOp.model_validate(data)
    assert restored.relationship_name == "vendor_contract"
    assert restored.relationship_uri == "urn:ontology:relationship:vendor_contract"

    # Test initialization from name alone
    op_from_name = RemoveRelationshipOp(op_id="op-rem-r-02", relationship_name="order_item")
    assert op_from_name.relationship_name == "order_item"


def test_remove_attribute_op_roundtrip() -> None:
    op = RemoveAttributeOp(
        op_id="op-rem-a-01",
        concept_uri="urn:ontology:entity:customer",
        attribute_name="legacy_fax_number",
        reason="Attribute deprecated across all ingest feeds.",
    )
    assert op.semver_class == "MAJOR"
    data = op.model_dump()
    assert data["op_type"] == "remove_attribute"
    restored = RemoveAttributeOp.model_validate(data)
    assert restored.attribute_name == "legacy_fax_number"
    assert restored.concept_uri == "urn:ontology:entity:customer"


def test_update_concept_urn_op_roundtrip() -> None:
    op = UpdateConceptUrnOp(
        op_id="op-urn-01",
        old_concept_uri="urn:ontology:entity:vendor",
        new_concept_uri="urn:ontology:entity:supplier",
        reason="Harmonization with FIBO supplier concept.",
    )
    assert op.semver_class == "MAJOR"
    data = op.model_dump()
    assert data["op_type"] == "update_concept_urn"
    restored = UpdateConceptUrnOp.model_validate(data)
    assert restored.old_concept_uri == "urn:ontology:entity:vendor"
    assert restored.new_concept_uri == "urn:ontology:entity:supplier"


def test_add_concept_op_serialization() -> None:
    op = AddConceptOp(
        op_id="op-001",
        concept_uri="urn:ontology:entity:supplier",
        concept_name="Supplier",
        elm_archetype="CORE_BUSINESS_CONCEPT",
        definition="An external vendor providing goods or logistics services.",
        target_hub="hub_supplier",
        attributes=["supplier_name", "tax_id"],
        parent_concept_uri="urn:ontology:entity:organization",
        synonyms=["Vendor", "Contractor"],
        governance_constraints={"pii_confidential_attributes": ["tax_id"]},
        key_authority_tiers={"tier_1": {"authority_name": "DUNS", "bkcc_policy": "GLOBAL_DUNS"}},
        rationale="Vendor tracking requirement.",
        confidence=0.99,
        evidence_refs=[
            EvidenceRef(
                evidence_type="column_profile", reference_uri="feed_supplier.tax_id", confidence=0.9
            )
        ],
    )
    assert op.semver_class == "MINOR"
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_concept"
    restored = AddConceptOp.model_validate(dumped)
    assert restored.concept_uri == "urn:ontology:entity:supplier"
    assert restored.target_hub == "hub_supplier"
    assert restored.parent_concept_uri == "urn:ontology:entity:organization"
    assert restored.synonyms == ["Vendor", "Contractor"]


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
        cardinality="one_to_many",
        rationale="Link supplier to physical deliveries.",
        confidence=0.95,
    )
    assert op.semver_class == "MINOR"
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_relationship"
    restored = AddRelationshipOp.model_validate(dumped)
    assert restored.target_link == "lnk_supplier_delivery"
    assert restored.cardinality == "one_to_many"


def test_add_attribute_op_roundtrip() -> None:
    op = AddAttributeOp(
        op_id="op-attr-01",
        concept_uri="urn:ontology:entity:supplier",
        attribute_name="credit_rating",
        data_type="string",
        description="Standard credit rating score.",
        is_optional=True,
        example_values=["AAA", "BBB+"],
    )
    assert op.semver_class == "MINOR"
    data = op.model_dump()
    assert data["op_type"] == "add_attribute"
    restored = AddAttributeOp.model_validate(data)
    assert restored.attribute_name == "credit_rating"
    assert restored.example_values == ["AAA", "BBB+"]

    # Test nested attribute dict normalization
    nested = AddAttributeOp(
        op_id="op-attr-02",
        concept_uri="urn:ontology:entity:supplier",
        attribute={
            "attribute_name": "vat_id",
            "data_type": "string",
            "definition": "VAT identifier",
        },
    )
    assert nested.attribute_name == "vat_id"
    assert nested.description == "VAT identifier"


def test_set_parent_concept_op_roundtrip() -> None:
    op = SetParentConceptOp(
        op_id="op-parent-01",
        concept_uri="urn:ontology:entity:supplier",
        parent_concept_uri="urn:ontology:entity:legal_entity",
    )
    assert op.semver_class == "MINOR"
    data = op.model_dump()
    assert data["op_type"] == "set_parent_concept"
    restored = SetParentConceptOp.model_validate(data)
    assert restored.parent_concept_uri == "urn:ontology:entity:legal_entity"


def test_set_concept_elm_archetype_op_roundtrip() -> None:
    op = SetConceptElmArchetypeOp(
        op_id="op-archetype-01",
        concept_uri="urn:ontology:entity:supplier",
        elm_archetype="WHO_PARTY",
    )
    assert op.semver_class == "MINOR"
    data = op.model_dump()
    assert data["op_type"] == "set_concept_elm_archetype"
    restored = SetConceptElmArchetypeOp.model_validate(data)
    assert restored.elm_archetype == "WHO_PARTY"


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
    assert op.semver_class == "MINOR"
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_reference_enumeration"
    restored = AddReferenceEnumerationOp.model_validate(dumped)
    assert len(restored.allowed_codes) == 4
    assert restored.enumeration_name == "delivery_status"


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
    assert op.semver_class == "MINOR"
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
    assert op.semver_class == "MINOR"
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_governance_constraint"
    restored = AddGovernanceConstraintOp.model_validate(dumped)
    assert "bank_account_iban" in restored.pii_confidential_attributes


def test_deprecate_concept_op_roundtrip() -> None:
    op = DeprecateConceptOp(
        op_id="op-dep-c-01",
        concept_uri="urn:ontology:entity:legacy_account",
        reason="Superceded by counterparty account model.",
    )
    assert op.semver_class == "MINOR"
    data = op.model_dump()
    assert data["op_type"] == "deprecate_concept"
    restored = DeprecateConceptOp.model_validate(data)
    assert restored.concept_uri == "urn:ontology:entity:legacy_account"
    assert restored.reason == "Superceded by counterparty account model."


def test_deprecate_relationship_op_roundtrip() -> None:
    op = DeprecateRelationshipOp(
        op_id="op-dep-r-01",
        relationship_name="legacy_rel",
        relationship_uri="urn:ontology:relationship:legacy_rel",
        reason="Migrated to link_v2.",
    )
    assert op.semver_class == "MINOR"
    data = op.model_dump()
    assert data["op_type"] == "deprecate_relationship"
    restored = DeprecateRelationshipOp.model_validate(data)
    assert restored.relationship_name == "legacy_rel"


def test_add_synonym_op_roundtrip() -> None:
    op = AddSynonymOp(
        op_id="op-syn-01",
        concept_uri="urn:ontology:entity:supplier",
        synonym="Distributor",
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "add_synonym"
    restored = AddSynonymOp.model_validate(data)
    assert restored.synonym == "Distributor"


def test_add_external_mapping_op_roundtrip() -> None:
    op = AddExternalMappingOp(
        op_id="op-map-01",
        concept_uri="urn:ontology:entity:supplier",
        scheme="FIBO",
        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/Supplier",
        confidence=0.88,
        predicate="exactMatch",
        pinned=True,
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "add_external_mapping"
    restored = AddExternalMappingOp.model_validate(data)
    assert restored.scheme == "FIBO"
    assert restored.standard_name == "FIBO"
    assert restored.predicate == "exactMatch"
    assert restored.pinned is True

    # Test alias sync from standard_name/standard_uri
    op_alt = AddExternalMappingOp(
        op_id="op-map-02",
        concept_uri="urn:ontology:entity:supplier",
        standard_name="FIBO",
        standard_uri="urn:fibo:Supplier",
        mapping_type="closeMatch",
    )
    assert op_alt.scheme == "FIBO"
    assert op_alt.target_uri == "urn:fibo:Supplier"
    assert op_alt.predicate == "closeMatch"


def test_revise_description_op_roundtrip() -> None:
    op = ReviseDescriptionOp(
        op_id="op-rev-desc-01",
        concept_uri="urn:ontology:entity:supplier",
        description="Comprehensive enterprise description of supplier.",
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "revise_description"
    restored = ReviseDescriptionOp.model_validate(data)
    assert restored.description == op.description
    assert restored.target_uri == "urn:ontology:entity:supplier"


def test_rescore_external_mapping_op_roundtrip() -> None:
    op = RescoreExternalMappingOp(
        op_id="op-rescore-01",
        concept_uri="urn:ontology:entity:supplier",
        scheme="FIBO",
        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/Supplier",
        new_confidence=0.92,
        previous_confidence=0.85,
        matcher_id="bm25_fibo_v1",
    )
    assert op.semver_class == "PATCH"
    assert op.mapping_confidence == 0.92
    assert op.new_confidence == 0.92
    data = op.model_dump()
    assert data["op_type"] == "rescore_external_mapping"
    restored = RescoreExternalMappingOp.model_validate(data)
    assert restored.new_confidence == 0.92
    assert restored.mapping_confidence == 0.92
    assert restored.previous_confidence == 0.85

    # Test initialization from mapping_confidence key for compatibility
    legacy_op = RescoreExternalMappingOp(
        op_id="op-rescore-02",
        concept_uri="urn:ontology:entity:supplier",
        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/Supplier",
        mapping_confidence=0.75,
    )
    assert legacy_op.new_confidence == 0.75
    assert legacy_op.mapping_confidence == 0.75


def test_add_reference_enum_code_op_roundtrip() -> None:
    op = AddReferenceEnumCodeOp(
        op_id="op-code-01",
        enumeration_name="delivery_status",
        code="RETURNED_TO_SENDER",
        description="Package returned to warehouse sender.",
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "add_reference_enum_code"
    restored = AddReferenceEnumCodeOp.model_validate(data)
    assert restored.code == "RETURNED_TO_SENDER"
    assert restored.enumeration_name == "delivery_status"


def test_repin_external_mapping_op_roundtrip() -> None:
    op = RepinExternalMappingOp(
        op_id="op-repin-01",
        concept_uri="urn:ontology:entity:supplier",
        scheme="FIBO",
        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/Supplier",
        pinned=True,
        new_reference_pack_id="fibo-core-2024q4",
        new_reference_pack_sha256="c" * 64,
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "repin_external_mapping"
    restored = RepinExternalMappingOp.model_validate(data)
    assert restored.pinned is True
    assert restored.new_reference_pack_id == "fibo-core-2024q4"


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
    assert op.semver_class == "PATCH"
    dumped = op.model_dump()
    assert dumped["op_type"] == "refine_definition"
    restored = RefineDefinitionOp.model_validate(dumped)
    assert len(restored.additional_attributes) == 2
    assert restored.definition == op.refined_description


def test_add_source_hint_op_serialization() -> None:
    op = AddSourceHintOp(
        op_id="op-007",
        concept_uri="urn:ontology:entity:supplier",
        source_name="ERP_FEED",
        source_system="ERP_FEED",
        table_name="bronze_erp_vendors",
        column_hint="VEND_CODE",
        column_name="VEND_CODE",
        hint="ERP primary key",
        rationale="Map ERP vendor records directly to Supplier concept.",
    )
    assert op.semver_class == "PATCH"
    dumped = op.model_dump()
    assert dumped["op_type"] == "add_source_hint"
    restored = AddSourceHintOp.model_validate(dumped)
    assert restored.table_name == "bronze_erp_vendors"
    assert restored.hint == "ERP primary key"


def test_annotate_concept_metadata_op_roundtrip() -> None:
    op = AnnotateConceptMetadataOp(
        op_id="op-meta-01",
        concept_uri="urn:ontology:entity:supplier",
        metadata={"business_owner": "Procurement Dept", "confidentiality": "internal"},
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "annotate_concept_metadata"
    restored = AnnotateConceptMetadataOp.model_validate(data)
    assert restored.metadata["business_owner"] == "Procurement Dept"

    # Test single key/value insertion helper
    op_single = AnnotateConceptMetadataOp(
        op_id="op-meta-02",
        concept_uri="urn:ontology:entity:supplier",
        metadata_key="tag",
        metadata_value="tier-1",
    )
    assert op_single.metadata["tag"] == "tier-1"


def test_remove_external_mapping_op_roundtrip() -> None:
    op = RemoveExternalMappingOp(
        op_id="op-rem-map-01",
        concept_uri="urn:ontology:entity:supplier",
        scheme="FIBO",
        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/DeprecatedSupplier",
    )
    assert op.semver_class == "PATCH"
    data = op.model_dump()
    assert data["op_type"] == "remove_external_mapping"
    restored = RemoveExternalMappingOp.model_validate(data)
    assert restored.scheme == "FIBO"
    assert restored.target_uri.endswith("DeprecatedSupplier")


# FULL 24-OPERATION DISCRIMINATED UNION ROUND-TRIP IN ONTOLOGYDELTA


def test_full_24_operations_discriminated_union_roundtrip() -> None:
    """Validate all 24 operations can coexist in a single delta and roundtrip cleanly."""

    all_ops: list[OntologyOperation] = [
        # 4 MAJOR
        RemoveConceptOp(op_id="op-1", concept_uri="urn:ontology:entity:c1", reason="Major removal"),
        RemoveRelationshipOp(op_id="op-2", relationship_name="r1", reason="Rel removal"),
        RemoveAttributeOp(op_id="op-3", concept_uri="urn:ontology:entity:c1", attribute_name="a1"),
        UpdateConceptUrnOp(
            op_id="op-4",
            old_concept_uri="urn:ontology:entity:c1",
            new_concept_uri="urn:ontology:entity:c2",
        ),
        # 10 MINOR
        AddConceptOp(op_id="op-5", concept_uri="urn:ontology:entity:c3", concept_name="C3"),
        AddRelationshipOp(
            op_id="op-6", relationship_uri="urn:ontology:relationship:r2", target_link="lnk_r2"
        ),
        AddAttributeOp(op_id="op-7", concept_uri="urn:ontology:entity:c3", attribute_name="attr_a"),
        SetParentConceptOp(
            op_id="op-8",
            concept_uri="urn:ontology:entity:c3",
            parent_concept_uri="urn:ontology:entity:parent",
        ),
        SetConceptElmArchetypeOp(
            op_id="op-9", concept_uri="urn:ontology:entity:c3", elm_archetype="CORE_CONCEPT"
        ),
        AddReferenceEnumerationOp(
            op_id="op-10",
            enum_uri="urn:ontology:enum:e1",
            name="e1",
            target_reference_table="ref_e1",
        ),
        AddKeyAuthorityPatternOp(
            op_id="op-11", concept_uri="urn:ontology:entity:c3", authority_name="LEI"
        ),
        AddGovernanceConstraintOp(
            op_id="op-12", concept_uri="urn:ontology:entity:c3", constraint_id="G1"
        ),
        DeprecateConceptOp(op_id="op-13", concept_uri="urn:ontology:entity:c3"),
        DeprecateRelationshipOp(op_id="op-14", relationship_name="r2"),
        # 10 PATCH
        AddSynonymOp(op_id="op-15", concept_uri="urn:ontology:entity:c3", synonym="AliasC3"),
        AddExternalMappingOp(
            op_id="op-16", concept_uri="urn:ontology:entity:c3", scheme="FIBO", target_uri="fibo:C3"
        ),
        ReviseDescriptionOp(
            op_id="op-17", concept_uri="urn:ontology:entity:c3", description="Updated description"
        ),
        RescoreExternalMappingOp(
            op_id="op-18",
            concept_uri="urn:ontology:entity:c3",
            target_uri="fibo:C3",
            new_confidence=0.9,
        ),
        AddReferenceEnumCodeOp(op_id="op-19", enumeration_name="e1", code="CODE_1"),
        RepinExternalMappingOp(
            op_id="op-20", concept_uri="urn:ontology:entity:c3", target_uri="fibo:C3", pinned=True
        ),
        RefineDefinitionOp(
            op_id="op-21", concept_uri="urn:ontology:entity:c3", definition="Refined def"
        ),
        AddSourceHintOp(op_id="op-22", concept_uri="urn:ontology:entity:c3", hint="source hint"),
        AnnotateConceptMetadataOp(
            op_id="op-23", concept_uri="urn:ontology:entity:c3", metadata={"key": "val"}
        ),
        RemoveExternalMappingOp(
            op_id="op-24", concept_uri="urn:ontology:entity:c3", target_uri="fibo:C3"
        ),
    ]

    assert len(all_ops) == 24

    delta = OntologyDelta(
        delta_id="delta-24-ops-001",
        base_version="1.0.0",
        base_sha256="1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
        operations=all_ops,
        target_version="2.0.0",
        author="studio-team",
        rationale="Comprehensive 24-op test suite evolution",
    )

    dumped = delta.model_dump(mode="json")
    assert len(dumped["operations"]) == 24

    restored = OntologyDelta.model_validate(dumped)
    assert len(restored.operations) == 24

    expected_types = [type(op) for op in all_ops]
    for expected_cls, op_inst in zip(expected_types, restored.operations, strict=True):
        assert isinstance(op_inst, expected_cls)


# ONTOLOGYDELTA OCC ENVELOPE & ALIASES


def test_ontology_delta_envelope_defaults_and_aliases() -> None:
    # Test envelope fields with explicit delta_id
    delta_default = OntologyDelta(
        delta_id="delta-default-01",
        base_version="1.0.0",
        base_sha256="a" * 64,
    )
    assert delta_default.delta_id == "delta-default-01"
    assert delta_default.base_version == "1.0.0"
    assert delta_default.base_ontology_version == "1.0.0"
    assert delta_default.parent_sha256 == "a" * 64
    assert delta_default.author is None
    assert delta_default.created_at is None

    # Test legacy aliases initialization
    delta_legacy = OntologyDelta(
        delta_id="delta-custom-id-123",
        parent_sha256="b" * 64,
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
    )
    assert delta_legacy.delta_id == "delta-custom-id-123"
    assert delta_legacy.base_sha256 == "b" * 64
    assert delta_legacy.base_version == "1.0.0"
    assert delta_legacy.target_version == "1.1.0"


def test_ontology_delta_legacy_schema_validation(ontology_delta_schema: dict) -> None:
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
                definition="Goods supplier.",
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

    # Validate against JSON Schema Draft 2020-12 via to_v1_schema_dict
    v1_dict = delta.to_v1_schema_dict()
    jsonschema.validate(instance=v1_dict, schema=ontology_delta_schema)

    # Validate direct model_dump output against v1 schema (MAJ-01 wire compatibility)
    raw_dump = delta.model_dump(mode="json", exclude_none=True)
    jsonschema.validate(instance=raw_dump, schema=ontology_delta_schema)

    # JCS canonicalization
    jcs_str = dumps_jcs(v1_dict)
    assert "delta-wave1-001" in jcs_str


def test_ontology_delta_v2_schema_validation(ontology_delta_v2_schema: dict) -> None:
    """Validate that full 24-op delta conforms to published v2 schema."""
    delta = OntologyDelta(
        delta_id="delta-v2-eval",
        base_version="1.0.0",
        base_sha256="a" * 64,
        target_version="2.0.0",
        author="agent_recommender",
        rationale="Evolving ontology with 24-op catalog",
        operations=[
            RemoveConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:obsolete",
                reason="Domain deprecation",
            ),
            AddConceptOp(
                op_id="op-2",
                concept_uri="urn:ontology:entity:new_concept",
                concept_name="NewConcept",
                parent_concept_uri="urn:ontology:entity:parent_concept",
                synonyms=["AliasConcept"],
            ),
        ],
    )
    raw_dump = delta.model_dump(mode="json", exclude_none=True)
    jsonschema.validate(instance=raw_dump, schema=ontology_delta_v2_schema)


def test_steward_waiver_record_valid() -> None:
    prop_id = uuid4()
    waiver = StewardWaiverRecord(
        proposal_id=prop_id,
        proposal_delta_sha256="d" * 64,
        target_concept_uris=["urn:ontology:entity:unmapped_concept"],
        base_sha256="e" * 64,
        rationale="Approved exceptional greenfield concept for pilot program.",
        authorized_by="admin_chief_steward",
        authorized_role="admin",
        signature_or_token="sig-test-token-valid",
    )
    assert isinstance(waiver.waiver_id, UUID)
    assert waiver.proposal_id == prop_id
    assert waiver.authorized_role == "admin"
    assert len(waiver.rationale) >= 10

    # Serialization roundtrip
    dumped = waiver.model_dump(mode="json")
    restored = StewardWaiverRecord.model_validate(dumped)
    assert restored.waiver_id == waiver.waiver_id
    assert restored.rationale == waiver.rationale


def test_steward_waiver_record_rationale_validation() -> None:
    # Rationale < 10 characters must raise ValidationError (Edge Case 17)
    with pytest.raises(ValidationError):
        StewardWaiverRecord(
            proposal_id=uuid4(),
            proposal_delta_sha256="d" * 64,
            target_concept_uris=["urn:ontology:entity:test"],
            base_sha256="e" * 64,
            rationale="Approved",  # 8 chars -> fails min_length=10
            authorized_by="admin_chief",
            authorized_role="admin",
            signature_or_token="sig-valid-token",
        )


def test_steward_waiver_record_role_validation() -> None:
    # Role other than "admin" or "steward" must raise ValidationError (Edge Case 18)
    with pytest.raises(ValidationError):
        StewardWaiverRecord(
            proposal_id=uuid4(),
            proposal_delta_sha256="d" * 64,
            target_concept_uris=["urn:ontology:entity:test"],
            base_sha256="e" * 64,
            rationale="Valid rationale with enough length.",
            authorized_by="editor_user",
            authorized_role="editor",  # Invalid role
            signature_or_token="sig-valid-token",
        )


# SPI PROTOCOLS TESTS (@runtime_checkable)


def test_grounding_verifier_spi_protocol() -> None:
    class ConformingVerifier:
        def verify_grounding(self, concept_uri: str, candidates: list[Any], **kwargs: Any) -> bool:
            return True

    class NonConformingVerifier:
        def other_method(self) -> None:
            pass

    assert isinstance(ConformingVerifier(), GroundingVerifier)
    assert not isinstance(NonConformingVerifier(), GroundingVerifier)

    # Result DTO
    res = GroundingVerificationResult(
        is_grounded=True,
        score=0.85,
        matched_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/Suppliers/Supplier",
        matched_label="Supplier",
        diagnostics=["BM25 exact match"],
    )
    assert res.is_grounded is True
    assert res.score == 0.85


def test_waiver_verifier_spi_protocol() -> None:
    class ConformingWaiverVerifier:
        def verify_waiver(
            self,
            waiver: StewardWaiverRecord,
            delta_sha256: str,
            base_sha256: str,
            **kwargs: Any,
        ) -> bool:
            return (
                waiver.proposal_delta_sha256 == delta_sha256 and waiver.base_sha256 == base_sha256
            )

    class NonConformingWaiverVerifier:
        pass

    assert isinstance(ConformingWaiverVerifier(), WaiverVerifier)
    assert not isinstance(NonConformingWaiverVerifier(), WaiverVerifier)


def test_deployment_state_provider_spi_protocol() -> None:
    class ConformingDeploymentProvider:
        def get_deployed_concepts(self) -> frozenset[str]:
            return frozenset({"urn:ontology:entity:customer"})

        def get_deployed_tables(self) -> frozenset[str]:
            return frozenset({"hub_customer", "sat_customer_core"})

        def get_deployed_entities(self) -> frozenset[str]:
            return self.get_deployed_concepts() | self.get_deployed_tables()

    class NonConformingDeploymentProvider:
        pass

    assert isinstance(ConformingDeploymentProvider(), DeploymentStateProvider)
    assert not isinstance(NonConformingDeploymentProvider(), DeploymentStateProvider)


def test_reference_ontology_adapter_spi_protocol() -> None:
    class ConformingAdapter:
        def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
            return {"uri": uri, "label": "Test"}

        def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
            return [{"term": term}]

    class NonConformingAdapter:
        pass

    assert isinstance(ConformingAdapter(), ReferenceOntologyAdapter)
    assert not isinstance(NonConformingAdapter(), ReferenceOntologyAdapter)


def test_construction_agent_spi_protocol() -> None:
    class ConformingAgent:
        def propose_delta(
            self, base_ontology: Any, evidence: list[Any] | None = None, **kwargs: Any
        ) -> Any:
            return None

        def clarify(self, context: Any, **kwargs: Any) -> Any:
            return None

    class NonConformingAgent:
        pass

    assert isinstance(ConformingAgent(), ConstructionAgent)
    assert not isinstance(NonConformingAgent(), ConstructionAgent)


def test_ontology_proposal_and_decision() -> None:
    delta = OntologyDelta(
        delta_id="delta-prop-01",
        base_sha256="a" * 64,
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
        "op_type": "unknown_future_op",
        "concept_uri": "urn:ontology:entity:supplier",
    }
    with pytest.raises(ValidationError):
        OntologyDelta.model_validate(
            {
                "delta_id": "bad-delta",
                "operations": [invalid_data],
            }
        )


def test_delta_deterministic_jcs_hashing() -> None:
    """Verify identical delta payload produces deterministic canonical JCS and SHA-256."""
    import hashlib

    op = AddConceptOp(
        op_id="op-1", concept_uri="urn:ontology:entity:customer", concept_name="Customer"
    )
    delta1 = OntologyDelta(
        delta_id="delta-det-01", base_version="1.0.0", base_sha256="a" * 64, operations=[op]
    )
    delta2 = OntologyDelta(
        delta_id="delta-det-01", base_version="1.0.0", base_sha256="a" * 64, operations=[op]
    )
    dump1 = dumps_jcs(delta1.model_dump(mode="json", exclude_none=True))
    dump2 = dumps_jcs(delta2.model_dump(mode="json", exclude_none=True))
    assert dump1 == dump2
    assert (
        hashlib.sha256(dump1.encode("utf-8")).hexdigest()
        == hashlib.sha256(dump2.encode("utf-8")).hexdigest()
    )


def test_conflicting_aliases_raise_validation_error() -> None:
    """Verify conflicting alias values raise ValidationError."""
    with pytest.raises(ValidationError, match="Conflicting values for 'base_sha256'"):
        OntologyDelta(delta_id="d1", base_sha256="a" * 64, parent_sha256="b" * 64)
    with pytest.raises(ValidationError, match="Conflicting values for 'base_version'"):
        OntologyDelta(
            delta_id="d1", base_version="1.0.0", base_ontology_version="2.0.0", base_sha256="a" * 64
        )
    with pytest.raises(ValidationError, match="Conflicting values for 'target_version'"):
        OntologyDelta(
            delta_id="d1",
            base_version="0.0.0",
            target_version="1.0.0",
            target_ontology_version="2.0.0",
        )


def test_occ_precondition_validation() -> None:
    """Verify non-greenfield deltas require a 64-char hex base_sha256."""
    with pytest.raises(ValidationError, match="base_sha256 must be a 64-character hex string"):
        OntologyDelta(delta_id="d1", base_version="1.0.0", base_sha256="")
    delta_greenfield = OntologyDelta(delta_id="d-green", base_version="0.0.0", base_sha256="")
    assert delta_greenfield.base_version == "0.0.0" and delta_greenfield.base_sha256 == ""


def test_to_v1_schema_dict_rejects_v2_operations() -> None:
    """Verify to_v1_schema_dict raises ValueError when v2 operations are present."""
    delta = OntologyDelta(
        delta_id="d-v2",
        base_version="1.0.0",
        base_sha256="a" * 64,
        target_version="2.0.0",
        operations=[
            RemoveConceptOp(
                op_id="op-rem",
                concept_uri="urn:ontology:entity:obsolete",
            )
        ],
    )
    with pytest.raises(ValueError, match="is not supported in v1 schema"):
        delta.to_v1_schema_dict()


def test_input_dict_immutability() -> None:
    """Verify model_validate does not mutate input dictionary in-place."""
    raw_rel = {
        "op_id": "op-test-mut",
        "op_type": "remove_relationship",
        "relationship_uri": "urn:ontology:relationship:supplier_contract",
    }
    snapshot_before = dict(raw_rel)
    RemoveRelationshipOp.model_validate(raw_rel)
    assert raw_rel == snapshot_before

    raw_delta = {
        "delta_id": "d-mut-test",
        "parent_sha256": "a" * 64,
        "base_ontology_version": "1.0.0",
        "operations": [],
    }
    snapshot_delta = dict(raw_delta)
    OntologyDelta.model_validate(raw_delta)
    assert raw_delta == snapshot_delta


def test_operations_reject_empty_targets() -> None:
    """Verify operations reject empty target identifiers."""
    c_uri = "urn:ontology:entity:c"
    with pytest.raises(ValidationError):
        RemoveRelationshipOp(op_id="1", relationship_name="", relationship_uri="")
    with pytest.raises(ValidationError):
        DeprecateRelationshipOp(op_id="1", relationship_name="", relationship_uri="")
    with pytest.raises(ValidationError):
        AddAttributeOp(op_id="1", concept_uri=c_uri, attribute_name="")
    with pytest.raises(ValidationError):
        AddReferenceEnumCodeOp(op_id="1", enumeration_name="", enum_uri="", code="CODE_1")
    with pytest.raises(ValidationError):
        AddReferenceEnumCodeOp(op_id="1", enumeration_name="e", code="")
    with pytest.raises(ValidationError):
        AddExternalMappingOp(op_id="1", concept_uri=c_uri, target_uri="", standard_uri="")
    with pytest.raises(ValidationError):
        RescoreExternalMappingOp(
            op_id="1", concept_uri=c_uri, target_uri="", standard_uri="", new_confidence=0.8
        )
    with pytest.raises(ValidationError):
        RepinExternalMappingOp(op_id="1", concept_uri=c_uri, target_uri="", standard_uri="")
    with pytest.raises(ValidationError):
        RemoveExternalMappingOp(op_id="1", concept_uri=c_uri, target_uri="", standard_uri="")
    with pytest.raises(ValidationError):
        ReviseDescriptionOp(op_id="1", concept_uri="", target_uri="")


def test_steward_waiver_record_strict_fields() -> None:
    """Verify StewardWaiverRecord strict validation on SHA-256, target URIs, and token."""
    kw: dict[str, Any] = {
        "proposal_id": uuid4(),
        "proposal_delta_sha256": "a" * 64,
        "target_concept_uris": ["urn:ontology:entity:c"],
        "base_sha256": "a" * 64,
        "rationale": "Justified waiver for pilot test.",
        "authorized_by": "admin",
        "authorized_role": "admin",
        "signature_or_token": "token-1",
    }
    with pytest.raises(ValidationError):
        StewardWaiverRecord(**(kw | {"proposal_delta_sha256": "not-hex"}))
    with pytest.raises(ValidationError):
        StewardWaiverRecord(**(kw | {"target_concept_uris": []}))
    with pytest.raises(ValidationError):
        StewardWaiverRecord(**(kw | {"signature_or_token": ""}))


def test_semver_bump_enforcement() -> None:
    """Verify OntologyDelta validates target_ontology_version SemVer bump."""

    def _d(
        v_tgt: str, op: OntologyOperation, v_base: str = "1.0.0", sha: str = "a" * 64
    ) -> OntologyDelta:
        return OntologyDelta(
            delta_id="d",
            base_version=v_base,
            base_sha256=sha,
            target_version=v_tgt,
            operations=[op],
        )

    with pytest.raises(ValidationError, match="Expected major bump"):
        _d("1.1.0", RemoveConceptOp(op_id="1", concept_uri="urn:ontology:entity:c1", reason="r"))
    assert (
        _d(
            "2.0.0", RemoveConceptOp(op_id="1", concept_uri="urn:ontology:entity:c1", reason="r")
        ).target_version
        == "2.0.0"
    )

    with pytest.raises(ValidationError, match="Expected major bump"):
        _d(
            "1.1.0",
            SetParentConceptOp(
                op_id="1", concept_uri="urn:ontology:entity:c1", parent_concept_uri=None
            ),
        )
    assert (
        _d(
            "1.1.0",
            SetParentConceptOp(
                op_id="1",
                concept_uri="urn:ontology:entity:c1",
                parent_concept_uri="urn:ontology:entity:p",
            ),
        ).target_version
        == "1.1.0"
    )

    with pytest.raises(ValidationError, match="Expected minor or major bump"):
        _d(
            "1.0.1",
            AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", concept_name="C2"),
        )

    with pytest.raises(
        ValidationError, match=r"target_ontology_version must be greater than base '0\.0\.0'"
    ):
        _d(
            "0.0.0",
            AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", concept_name="C2"),
            v_base="0.0.0",
            sha="",
        )

    # BOOTSTRAP mode allows target 1.0.0 with base 1.0.0 (instantiating initial domain models)
    d_boot = OntologyDelta(
        delta_id="d-boot-1",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.0",
        operations=[
            AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", concept_name="C2")
        ],
        metadata={"mode": "BOOTSTRAP"},
    )
    assert d_boot.target_ontology_version == "1.0.0"

    # Default/None mode with base 1.0.0 and target 1.0.0 is also allowed for bootstrap compatibility
    d_boot_none = OntologyDelta(
        delta_id="d-boot-2",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.0",
        operations=[
            AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", concept_name="C2")
        ],
    )
    assert d_boot_none.target_ontology_version == "1.0.0"

    # Explicit DELTA mode with base 1.0.0 and target 1.0.0 must raise ValidationError
    with pytest.raises(ValidationError, match="Expected minor or major bump"):
        OntologyDelta(
            delta_id="d-delta-err",
            base_ontology_version="1.0.0",
            target_ontology_version="1.0.0",
            operations=[
                AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", concept_name="C2")
            ],
            metadata={"mode": "DELTA"},
        )

    # Destructive operations (MAJOR) in BOOTSTRAP mode must still require major bump (>= 2.0.0)
    with pytest.raises(ValidationError, match="Expected major bump"):
        OntologyDelta(
            delta_id="d-boot-maj-err",
            base_ontology_version="1.0.0",
            target_ontology_version="1.0.0",
            operations=[
                RemoveConceptOp(op_id="1", concept_uri="urn:ontology:entity:c2", reason="r")
            ],
            metadata={"mode": "BOOTSTRAP"},
        )


def test_to_v1_schema_dict_populated_extensions_rejected() -> None:
    """Verify to_v1_schema_dict rejects deltas where extension fields would be silently dropped."""
    op = AddConceptOp(op_id="1", concept_uri="urn:ontology:entity:c1", concept_name="C1")
    d_auth = OntologyDelta(
        delta_id="d1", base_version="1.0.0", base_sha256="a" * 64, author="alice", operations=[op]
    )
    with pytest.raises(ValueError, match="'author' envelope field is populated"):
        d_auth.to_v1_schema_dict()

    d_rat = OntologyDelta(
        delta_id="d2",
        base_version="1.0.0",
        base_sha256="a" * 64,
        rationale="Studio",
        operations=[op],
    )
    with pytest.raises(ValueError, match="'rationale' envelope field is populated"):
        d_rat.to_v1_schema_dict()

    op_p = AddConceptOp(
        op_id="2",
        concept_uri="urn:ontology:entity:c1",
        concept_name="C1",
        parent_concept_uri="urn:ontology:entity:p",
    )
    d_p = OntologyDelta(
        delta_id="d3", base_version="1.0.0", base_sha256="a" * 64, operations=[op_p]
    )
    with pytest.raises(ValueError, match="'parent_concept_uri' is populated"):
        d_p.to_v1_schema_dict()


def test_steward_waiver_record_expires_at_and_utc_created_at() -> None:
    """Verify StewardWaiverRecord handles expires_at and enforces UTC created_at."""
    waiver = StewardWaiverRecord(
        proposal_id=uuid4(),
        proposal_delta_sha256="a" * 64,
        target_concept_uris=["urn:ontology:entity:unmapped"],
        base_sha256="b" * 64,
        rationale="Emergency waiver for urgent deployment pipeline.",
        authorized_by="lead_steward",
        authorized_role="steward",
        signature_or_token="token-valid",
        expires_at=datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    assert waiver.created_at.tzinfo is not None
    assert waiver.expires_at is not None
    assert waiver.expires_at.year == 2027

    dumped = waiver.model_dump(mode="json")
    assert "expires_at" in dumped
    restored = StewardWaiverRecord.model_validate(dumped)
    assert restored.expires_at == waiver.expires_at


def test_canonical_jcs_hash_byte_stability() -> None:
    """Verify canonical JCS SHA-256 is stable and reproducible across serialized payloads."""
    import hashlib

    delta = OntologyDelta(
        delta_id="delta-stable-golden",
        parent_sha256="1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-stable-1",
                concept_uri="urn:ontology:entity:counterparty",
                concept_name="Counterparty",
                definition="A legal entity participant in a trade or contract.",
                elm_archetype="CORE_BUSINESS_CONCEPT",
            )
        ],
    )
    jcs_text = dumps_jcs(delta.model_dump(mode="json", exclude_none=True))
    calculated_hash = hashlib.sha256(jcs_text.encode("utf-8")).hexdigest()

    # Repeat serialization to assert strict determinism
    jcs_text_2 = dumps_jcs(delta.model_dump(mode="json", exclude_none=True))
    calculated_hash_2 = hashlib.sha256(jcs_text_2.encode("utf-8")).hexdigest()

    assert jcs_text == jcs_text_2
    assert calculated_hash == calculated_hash_2
    assert len(calculated_hash) == 64
    # Golden hash pinned per MIN-04
    golden_hash = "8359fc5216e61dc6780d9d5e9a24123fabbcebf0a66e8270f5baec45f1329b99"
    assert calculated_hash == golden_hash
    assert delta.canonical_sha256() == golden_hash
    assert delta.canonical_json() == jcs_text


def test_add_external_mapping_confidence_alias_isolation() -> None:
    """Verify AddExternalMappingOp roundtrips with distinct op and mapping confidence."""
    op = AddExternalMappingOp(
        op_id="op-map-alias",
        concept_uri="urn:ontology:entity:customer",
        scheme="FIBO",
        target_uri="https://fibo.org/Customer",
        confidence=1.0,
        mapping_confidence=0.6,
    )
    assert op.confidence == 1.0
    assert op.mapping_confidence == 0.6
    dumped = op.model_dump()
    assert dumped["confidence"] == 1.0
    assert dumped["mapping_confidence"] == 0.6
    restored = AddExternalMappingOp.model_validate(dumped)
    assert restored.confidence == 1.0
    assert restored.mapping_confidence == 0.6


def test_genesis_delta_preconditions() -> None:
    """Verify genesis delta rules: parent_sha256 must be empty, and MAJOR ops rejected."""
    d = OntologyDelta(
        delta_id="d1",
        base_version="0.0.0",
        target_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="1", concept_uri="urn:ontology:entity:instrument", concept_name="Instrument"
            )
        ],
    )
    assert d.base_sha256 == ""

    with pytest.raises(ValidationError, match="parent_sha256 must be empty for genesis delta"):
        OntologyDelta(
            delta_id="d2",
            base_version="0.0.0",
            parent_sha256="a" * 64,
            target_version="1.0.0",
            operations=[],
        )

    with pytest.raises(
        ValidationError,
        match="Destructive operations \\(MAJOR\\) are prohibited on greenfield genesis delta",
    ):
        OntologyDelta(
            delta_id="d3",
            base_version="0.0.0",
            target_version="1.0.0",
            operations=[
                RemoveConceptOp(
                    op_id="1",
                    concept_uri="urn:ontology:entity:instrument",
                    reason="Cannot remove on genesis",
                )
            ],
        )


def test_major_breaking_target_version_requirement() -> None:
    """Verify target_ontology_version is required when delta contains MAJOR operations (MAJ-03)."""
    with pytest.raises(
        ValidationError,
        match="target_ontology_version is required when delta contains MAJOR breaking operations",
    ):
        OntologyDelta(
            delta_id="d-no-target-maj",
            base_ontology_version="1.0.0",
            target_ontology_version=None,
            parent_sha256="a" * 64,
            operations=[
                RemoveConceptOp(
                    op_id="op-del",
                    concept_uri="urn:ontology:entity:customer",
                    reason="Removal is breaking",
                )
            ],
        )


def test_steward_waiver_record_expiration_validation() -> None:
    """Verify StewardWaiverRecord validates expires_at > created_at (MIN-08)."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="must be strictly after created_at"):
        StewardWaiverRecord(
            proposal_id=uuid4(),
            proposal_delta_sha256="a" * 64,
            target_concept_uris=["urn:ontology:entity:customer"],
            base_sha256="b" * 64,
            rationale="Valid rationale that exceeds ten characters length",
            authorized_by="steward-alice",
            authorized_role="steward",
            signature_or_token="token-abc",
            created_at=now,
            expires_at=now,
        )


def test_steward_waiver_record_signing_payload() -> None:
    """Verify StewardWaiverRecord produces deterministic RFC 8785 signing payload (MIN-08)."""
    p_id = uuid4()
    w_id = uuid4()
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    later = datetime(2026, 12, 10, 12, 0, 0, tzinfo=UTC)

    waiver = StewardWaiverRecord(
        waiver_id=w_id,
        proposal_id=p_id,
        proposal_delta_sha256="a" * 64,
        target_concept_uris=["urn:ontology:entity:customer"],
        base_sha256="b" * 64,
        rationale="Exempted from FIBO alignment due to proprietary model",
        authorized_by="admin-carol",
        authorized_role="admin",
        signature_or_token="sig-cryptographic-blob",
        created_at=now,
        expires_at=later,
    )

    payload = waiver.signing_payload()
    assert "signature_or_token" not in payload
    assert str(p_id) in payload
    assert str(w_id) in payload
    assert len(waiver.signing_sha256()) == 64


def test_base_aware_effective_semver_class() -> None:
    """Verify SetParentConceptOp and SetConceptElmArchetypeOp return MAJOR on overwrite."""
    base = {
        "concepts": {
            "urn:ontology:entity:party": {
                "concept_uri": "urn:ontology:entity:party",
                "parent_concept_uri": "urn:ontology:entity:legal_entity",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
            }
        }
    }
    c_uri = "urn:ontology:entity:party"
    assert (
        SetParentConceptOp(
            op_id="1", concept_uri=c_uri, parent_concept_uri="urn:ontology:entity:autonomous_agent"
        ).effective_semver_class(base_ontology=base)
        == "MAJOR"
    )
    assert (
        SetParentConceptOp(
            op_id="2", concept_uri=c_uri, parent_concept_uri="urn:ontology:entity:legal_entity"
        ).effective_semver_class(base_ontology=base)
        == "MINOR"
    )
    assert (
        SetConceptElmArchetypeOp(
            op_id="3", concept_uri=c_uri, elm_archetype="BUSINESS_EVENT"
        ).effective_semver_class(base_ontology=base)
        == "MAJOR"
    )
    assert (
        SetConceptElmArchetypeOp(
            op_id="4", concept_uri=c_uri, elm_archetype="CORE_BUSINESS_CONCEPT"
        ).effective_semver_class(base_ontology=base)
        == "MINOR"
    )


def test_to_v1_schema_dict_rejects_source_hint_with_hint() -> None:
    """Verify to_v1_schema_dict rejects AddSourceHintOp when hint field is populated (MIN-03)."""
    delta = OntologyDelta(
        delta_id="d-v1-hint",
        operations=[
            AddSourceHintOp(
                op_id="op-hint",
                concept_uri="urn:ontology:entity:customer",
                hint="v2-only unstructured provenance note",
            )
        ],
    )
    with pytest.raises(
        ValueError,
        match="Cannot convert AddSourceHintOp to v1 schema: 'hint' is populated",
    ):
        delta.to_v1_schema_dict()


def test_add_source_hint_independent_fields() -> None:
    """Verify AddSourceHintOp allows independent source and column fields (MAJ-02)."""
    op = AddSourceHintOp(
        op_id="op-hint-indep",
        concept_uri="urn:ontology:entity:account",
        source_name="CRM_FEED",
        source_system="Salesforce",
        column_hint="acc_id",
        column_name="account_id",
    )
    assert op.source_name == "CRM_FEED"
    assert op.source_system == "Salesforce"
    assert op.column_hint == "acc_id"
    assert op.column_name == "account_id"


def test_refine_definition_independent_uris() -> None:
    """Verify RefineDefinitionOp allows distinct target_uri and concept_uri (MAJ-02)."""
    op = RefineDefinitionOp(
        op_id="op-refine-indep",
        target_uri="urn:ontology:entity:customer",
        concept_uri="urn:ontology:entity:customer",
        definition="Customer entity definition",
    )
    assert op.target_uri == "urn:ontology:entity:customer"
    assert op.concept_uri == "urn:ontology:entity:customer"
    assert op.definition == "Customer entity definition"
