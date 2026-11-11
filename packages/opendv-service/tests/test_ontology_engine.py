"""Comprehensive tests for the deterministic ontology engine.

Validates:
- ONT-EVO-001: Breaking change evolution locks against deployed contracts.
- ONT-EVO-002: Strict rejection of provisional URIs (urn:ontology:provisional:*).
- ONT-EVO-003: Non-additive conflict, anti-silo, and dangling endpoint rejection.
- Deterministic, idempotent apply_ontology_delta with lineage.
- Comprehensive ontology_diff detection of additions, refinements, and breaking changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    OntologyDelta,
    RefineDefinitionOp,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_service.ontology import (
    apply_ontology_delta,
    compute_ontology_sha256,
    ontology_diff,
    validate_ontology_delta,
)


@pytest.fixture
def base_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="logistics",
        enterprise_owner="Apex Logistics",
        standards_references=["ISO 8601"],
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:depot",
                concept_name="Depot",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A distribution center depot.",
                target_hub="hub_depot",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:vehicle",
                concept_name="Vehicle",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A transport fleet vehicle.",
                target_hub="hub_vehicle",
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:vehicle_assignment",
                relationship_name="VehicleAssignment",
                target_link="lnk_vehicle_assignment",
                endpoints=["urn:ontology:entity:depot", "urn:ontology:entity:vehicle"],
                grain_semantics="Assignment of vehicle to depot.",
            )
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:vehicle_type",
                target_reference_table="ref_vehicle_type",
                allowed_codes=["VAN", "TRUCK", "TRAIN"],
            )
        ],
    )


@pytest.fixture
def deployed_contract() -> dict:
    return {
        "contract_version": "5.0",
        "domain_namespace": "logistics",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "raw_depot",
                "column_name": "depot_code",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_depot",
            }
        ],
        "hubs": [
            {
                "name": "hub_depot",
                "ontology_concept_uri": "urn:ontology:entity:depot",
                "business_key_sources": [
                    {
                        "bronze_table": "raw_depot",
                        "key_columns": ["depot_code"],
                        "record_source_code": "DEPOT_ERP",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                    }
                ],
            }
        ],
        "links": [
            {
                "name": "lnk_vehicle_assignment",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:vehicle_assignment",
                "source_bronze_table": "raw_assignment",
                "hub_references": [
                    {
                        "target_hub": "hub_depot",
                        "role_alias": "depot",
                        "source_fk_columns": ["depot_code"],
                    },
                    {
                        "target_hub": "hub_vehicle",
                        "role_alias": "vehicle",
                        "source_fk_columns": ["vin"],
                    },
                ],
            }
        ],
        "satellites": [],
        "reference_tables": [
            {
                "name": "ref_vehicle_type",
                "ontology_concept_uri": "urn:ontology:enum:vehicle_type",
                "source_bronze_table": "raw_vehicle_types",
                "code_column": "code",
                "description_columns": ["descr"],
            }
        ],
        "unmapped_escalations": [],
    }


def test_ont_evo_001_rejects_breaking_change_on_deployed_contract(
    base_ontology: GraphAOntology,
    deployed_contract: dict,
) -> None:
    # Rebind deployed concept 'urn:ontology:entity:depot' to a different hub 'hub_depot_v2'
    breaking_delta = OntologyDelta(
        delta_id="delta-breaking-01",
        operations=[
            AddConceptOp(
                op_id="op-break-hub",
                concept_uri="urn:ontology:entity:depot",
                target_hub="hub_depot_v2",
            )
        ],
    )
    result = validate_ontology_delta(
        base_ontology, breaking_delta, deployed_contract=deployed_contract
    )
    assert not result.valid
    assert result.error_code == "ONT-EVO-001"
    assert any("ONT-EVO-001" in v for v in result.violations)
    assert "rebound to 'hub_depot_v2'" in result.violations[0]

    # Rebind deployed relationship 'vehicle_assignment' to different link
    breaking_rel_delta = OntologyDelta(
        delta_id="delta-breaking-02",
        operations=[
            AddRelationshipOp(
                op_id="op-break-link",
                relationship_uri="urn:ontology:relationship:vehicle_assignment",
                target_link="lnk_vehicle_assignment_v2",
                endpoints=["urn:ontology:entity:depot", "urn:ontology:entity:vehicle"],
            )
        ],
    )
    result_rel = validate_ontology_delta(
        base_ontology, breaking_rel_delta, deployed_contract=deployed_contract
    )
    assert not result_rel.valid
    assert result_rel.error_code == "ONT-EVO-001"
    assert "cannot be rebound to 'lnk_vehicle_assignment_v2'" in result_rel.violations[0]


def test_ont_evo_002_rejects_provisional_uris(base_ontology: GraphAOntology) -> None:
    # Concept with provisional URI
    delta_concept_prov = OntologyDelta(
        delta_id="delta-prov-01",
        operations=[
            AddConceptOp(
                op_id="op-prov-1",
                concept_uri="urn:ontology:provisional:entity:cargo",
                target_hub="hub_cargo",
            )
        ],
    )
    res1 = validate_ontology_delta(base_ontology, delta_concept_prov)
    assert not res1.valid
    assert res1.error_code == "ONT-EVO-002"
    assert "urn:ontology:provisional:entity:cargo" in res1.violations[0]

    # Relationship with provisional URI
    delta_rel_prov = OntologyDelta(
        delta_id="delta-prov-02",
        operations=[
            AddRelationshipOp(
                op_id="op-prov-2",
                relationship_uri="urn:ontology:provisional:relationship:depot_cargo",
                target_link="lnk_depot_cargo",
                endpoints=["urn:ontology:entity:depot", "urn:ontology:entity:vehicle"],
            )
        ],
    )
    res2 = validate_ontology_delta(base_ontology, delta_rel_prov)
    assert not res2.valid
    assert res2.error_code == "ONT-EVO-002"

    # Endpoints containing provisional URI
    delta_ep_prov = OntologyDelta(
        delta_id="delta-prov-03",
        operations=[
            AddRelationshipOp(
                op_id="op-prov-3",
                relationship_uri="urn:ontology:relationship:depot_cargo",
                target_link="lnk_depot_cargo",
                endpoints=["urn:ontology:entity:depot", "urn:ontology:provisional:entity:cargo"],
            )
        ],
    )
    res3 = validate_ontology_delta(base_ontology, delta_ep_prov)
    assert not res3.valid
    assert res3.error_code == "ONT-EVO-002"


def test_ont_evo_003_anti_silo_and_dangling_endpoints(base_ontology: GraphAOntology) -> None:
    # 1. Anti-silo collision: new concept mapping to an already bound hub
    anti_silo_delta = OntologyDelta(
        delta_id="delta-anti-silo",
        operations=[
            AddConceptOp(
                op_id="op-silo-1",
                concept_uri="urn:ontology:entity:warehouse",
                concept_name="Warehouse",
                target_hub="hub_depot",  # hub_depot is already bound to depot!
            )
        ],
    )
    res_silo = validate_ontology_delta(base_ontology, anti_silo_delta)
    assert not res_silo.valid
    assert res_silo.error_code == "ONT-EVO-003"
    assert any("Anti-silo violation" in v for v in res_silo.violations)

    # 2. Dangling endpoint in relationship
    dangling_delta = OntologyDelta(
        delta_id="delta-dangling",
        operations=[
            AddRelationshipOp(
                op_id="op-dang-1",
                relationship_uri="urn:ontology:relationship:depot_route",
                target_link="lnk_depot_route",
                endpoints=["urn:ontology:entity:depot", "urn:ontology:entity:non_existent_route"],
            )
        ],
    )
    res_dang = validate_ontology_delta(base_ontology, dangling_delta)
    assert not res_dang.valid
    assert res_dang.error_code == "ONT-EVO-003"
    assert any("Dangling endpoint" in v for v in res_dang.violations)

    # 3. Lineage parent_sha256 mismatch
    lineage_delta = OntologyDelta(
        delta_id="delta-lineage-bad",
        parent_sha256="0" * 64,
        operations=[
            AddConceptOp(
                op_id="op-clean-1",
                concept_uri="urn:ontology:entity:driver",
                target_hub="hub_driver",
            )
        ],
    )
    res_lineage = validate_ontology_delta(base_ontology, lineage_delta)
    assert not res_lineage.valid
    assert res_lineage.error_code == "ONT-EVO-003"
    assert any("Lineage mismatch" in v for v in res_lineage.violations)


def test_apply_ontology_delta_determinism_and_lineage(base_ontology: GraphAOntology) -> None:
    base_sha = compute_ontology_sha256(base_ontology)

    valid_delta = OntologyDelta(
        delta_id="delta-valid-01",
        parent_sha256=base_sha,
        domain_namespace="logistics",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_uri="urn:ontology:entity:driver",
                concept_name="Driver",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A certified fleet driver.",
                target_hub="hub_driver",
                attributes=["driver_license", "badge_id"],
            ),
            AddRelationshipOp(
                op_id="op-2",
                relationship_uri="urn:ontology:relationship:driver_vehicle",
                relationship_name="DriverVehicle",
                target_link="lnk_driver_vehicle",
                endpoints=["urn:ontology:entity:driver", "urn:ontology:entity:vehicle"],
                grain_semantics="Driver operates vehicle.",
            ),
            AddReferenceEnumerationOp(
                op_id="op-3",
                enum_uri="urn:ontology:enum:driver_status",
                name="driver_status",
                target_reference_table="ref_driver_status",
                values=["ACTIVE", "ON_LEAVE", "TERMINATED"],
                description="Driver employment status.",
            ),
            RefineDefinitionOp(
                op_id="op-4",
                target_uri="urn:ontology:entity:depot",
                refined_description="Includes cross-dock facilities and regional sorting hubs.",
            ),
        ],
    )

    # Validation succeeds
    val_res = validate_ontology_delta(base_ontology, valid_delta)
    assert val_res.valid
    assert len(val_res.violations) == 0

    # First application
    applied1 = apply_ontology_delta(
        base_ontology, valid_delta, decision_id="dec-test-001", target_version="1.1.0"
    )
    applied2 = apply_ontology_delta(
        base_ontology, valid_delta, decision_id="dec-test-001", target_version="1.1.0"
    )

    # 1. Determinism and idempotence: Bit-for-bit identical hashes
    sha1 = compute_ontology_sha256(applied1)
    sha2 = compute_ontology_sha256(applied2)
    assert sha1 == sha2

    # 2. Lineage properties
    assert applied1.ontology_version == "1.1.0"
    assert applied1.parent_sha256 == base_sha
    assert applied1.decision_refs == ["dec-test-001"]

    # 3. Lexicographical sorting
    concept_uris = [c.concept_uri for c in applied1.canonical_business_concepts]
    assert concept_uris == sorted(concept_uris)
    rel_uris = [r.relationship_uri for r in applied1.canonical_relationships]
    assert rel_uris == sorted(rel_uris)
    enum_uris = [e.enum_uri for e in applied1.reference_enumerations]
    assert enum_uris == sorted(enum_uris)

    # 4. Refinement check
    depot_c = next(
        c
        for c in applied1.canonical_business_concepts
        if c.concept_uri == "urn:ontology:entity:depot"
    )
    assert "Includes cross-dock facilities" in depot_c.definition


def test_apply_ontology_delta_with_accepted_op_ids(base_ontology: GraphAOntology) -> None:
    delta = OntologyDelta(
        delta_id="delta-partial",
        operations=[
            AddConceptOp(
                op_id="op-accept-1",
                concept_uri="urn:ontology:entity:driver",
                target_hub="hub_driver",
            ),
            AddConceptOp(
                op_id="op-reject-2",
                concept_uri="urn:ontology:entity:trailer",
                target_hub="hub_trailer",
            ),
        ],
    )

    # Only accept op-accept-1
    result = apply_ontology_delta(
        base_ontology, delta, accepted_op_ids=["op-accept-1"], decision_id="dec-partial-01"
    )

    uris = [c.concept_uri for c in result.canonical_business_concepts]
    assert "urn:ontology:entity:driver" in uris
    assert "urn:ontology:entity:trailer" not in uris


def test_apply_invalid_delta_raises_value_error(base_ontology: GraphAOntology) -> None:
    invalid_delta = OntologyDelta(
        delta_id="delta-invalid",
        operations=[
            AddConceptOp(
                op_id="op-bad",
                concept_uri="urn:ontology:provisional:entity:bad",
                target_hub="hub_bad",
            )
        ],
    )
    with pytest.raises(ValueError, match="Cannot apply invalid ontology delta"):
        apply_ontology_delta(base_ontology, invalid_delta)


def test_ontology_diff_comprehensive(base_ontology: GraphAOntology) -> None:
    # Build evolved ontology with 1 addition, 1 refinement, and 1 breaking change
    evolved = GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace="logistics",
        enterprise_owner="Apex Logistics",
        canonical_business_concepts=[
            # Refined depot definition
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:depot",
                concept_name="Depot",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A distribution center depot with automated sorters.",
                target_hub="hub_depot",
            ),
            # Added concept
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:driver",
                concept_name="Driver",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A certified driver.",
                target_hub="hub_driver",
            ),
            # Dropped 'urn:ontology:entity:vehicle' -> breaking change!
        ],
        canonical_relationships=[],  # Dropped vehicle_assignment -> breaking change!
        reference_enumerations=[],  # Dropped vehicle_type -> breaking change!
    )

    diff = ontology_diff(base_ontology, evolved)
    assert diff.base_version == "1.0.0"
    assert diff.target_version == "1.1.0"
    assert diff.added_concepts == ["urn:ontology:entity:driver"]
    assert diff.refined_concepts == ["urn:ontology:entity:depot"]
    assert len(diff.breaking_changes) == 3
    assert any(
        "Concept 'urn:ontology:entity:vehicle' was removed" in b for b in diff.breaking_changes
    )
    assert any(
        "Relationship 'urn:ontology:relationship:vehicle_assignment' was removed" in b
        for b in diff.breaking_changes
    )
    assert any(
        "Reference enumeration 'urn:ontology:enum:vehicle_type' was removed" in b
        for b in diff.breaking_changes
    )


def test_gt06_golden_vector_integrity() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    gt06_dir = repo_root / "tests" / "golden" / "GT-06"

    base_raw = yaml.safe_load((gt06_dir / "base_ontology.yaml").read_text(encoding="utf-8"))
    base = GraphAOntology.model_validate(base_raw)

    proposal_raw = json.loads((gt06_dir / "proposal.json").read_text(encoding="utf-8"))
    delta = OntologyDelta.model_validate(proposal_raw["delta"])

    # 1. Validation matches validation.json
    val = validate_ontology_delta(base, delta)
    assert val.valid is True
    expected_val = json.loads((gt06_dir / "validation.json").read_text(encoding="utf-8"))
    assert val.model_dump(mode="json", exclude_none=True) == expected_val

    # 2. Application produces accepted_ontology.yaml
    accepted = apply_ontology_delta(base, delta, decision_id="dec-gt06-001", target_version="1.1.0")
    expected_accepted_raw = yaml.safe_load(
        (gt06_dir / "accepted_ontology.yaml").read_text(encoding="utf-8")
    )
    expected_accepted = GraphAOntology.model_validate(expected_accepted_raw)
    assert compute_ontology_sha256(accepted) == compute_ontology_sha256(expected_accepted)

    # 3. Diff matches ontology_diff.json
    diff = ontology_diff(base, accepted)
    expected_diff = json.loads((gt06_dir / "ontology_diff.json").read_text(encoding="utf-8"))
    assert diff.model_dump(mode="json", exclude_none=True) == expected_diff


def test_apply_ontology_delta_bootstrap_mode(base_ontology: GraphAOntology) -> None:
    """Verify applying a BOOTSTRAP delta with base 1.0.0 and target 1.0.0 succeeds."""
    base_sha = compute_ontology_sha256(base_ontology)
    boot_delta = OntologyDelta(
        delta_id="delta-boot-apply-01",
        parent_sha256=base_sha,
        domain_namespace="logistics",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-boot-1",
                concept_uri="urn:ontology:entity:driver",
                concept_name="Driver",
                elm_archetype="CORE_BUSINESS_CONCEPT",
                definition="A certified fleet driver.",
                target_hub="hub_driver",
            ),
        ],
        metadata={"mode": "BOOTSTRAP"},
    )
    result = apply_ontology_delta(base_ontology, boot_delta)
    assert result.ontology_version == "1.0.0"
    assert any(
        c.concept_uri == "urn:ontology:entity:driver" for c in result.canonical_business_concepts
    )
