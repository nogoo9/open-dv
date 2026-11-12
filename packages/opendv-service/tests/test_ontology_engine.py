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
    """P05::MAJ-05: Caller-supplied metadata={'mode': 'BOOTSTRAP'} cannot bypass 1.0.0->1.0.0
    SemVer rules, while explicit server allow_bootstrap=True on a 0-CBC bootstrap base succeeds.
    """
    base_sha = compute_ontology_sha256(base_ontology)
    with pytest.raises(ValueError):
        OntologyDelta(
            delta_id="delta-boot-spoof-01",
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

    # Server-internal 0-CBC bootstrap with explicit context + allow_bootstrap=True
    empty_boot_base = GraphAOntology.model_construct(
        ontology_version="1.0.0",
        domain_namespace="logistics",
        enterprise_owner="Apex Logistics",
        standards_references=[],
        canonical_business_concepts=[],
        canonical_relationships=[],
        reference_enumerations=[],
        decision_refs=[],
    )
    boot_delta = OntologyDelta.model_validate(
        {
            "delta_id": "delta-boot-apply-01",
            "domain_namespace": "logistics",
            "base_ontology_version": "1.0.0",
            "target_ontology_version": "1.0.0",
            "operations": [
                {
                    "op_id": "op-boot-1",
                    "op_type": "add_concept",
                    "concept_uri": "urn:ontology:entity:driver",
                    "concept_name": "Driver",
                    "elm_archetype": "CORE_BUSINESS_CONCEPT",
                    "definition": "A certified fleet driver.",
                    "target_hub": "hub_driver",
                }
            ],
        },
        context={"allow_bootstrap": True},
    )
    # Even with context-validated DTO, apply_ontology_delta requires allow_bootstrap=True
    with pytest.raises(ValueError):
        apply_ontology_delta(empty_boot_base, boot_delta, allow_bootstrap=False)

    result = apply_ontology_delta(empty_boot_base, boot_delta, allow_bootstrap=True)
    assert result.ontology_version == "1.0.0"
    assert any(
        c.concept_uri == "urn:ontology:entity:driver" for c in result.canonical_business_concepts
    )


def test_pre_cbc_0_x_draft_promotion_and_major_rejection() -> None:
    """P05::MAJ-06 & MAJ-02: 0.0.0 -> 0.1.0 -> 1.0.0 promotion when first CBC arrives,
    and rejection of MAJOR ops on 0-CBC drafts.
    """
    from opendv_contracts.dtos.ontology import RemoveConceptOp
    from opendv_core.genesis import create_genesis_ontology
    from opendv_service.ontology import SemVerCalculusError, compute_next_semver

    g0 = create_genesis_ontology(domain_namespace="logistics", enterprise_owner="Apex Logistics")
    assert g0.ontology_version == "0.0.0"

    # Pre-CBC enum addition advances 0.0.0 -> 0.1.0
    d_enum = OntologyDelta(
        delta_id="d-0-1",
        base_ontology_version="0.0.0",
        target_ontology_version="0.1.0",
        operations=[
            AddReferenceEnumerationOp(
                op_id="op-e1",
                enum_uri="urn:ontology:enum:region",
                name="region",
                target_reference_table="ref_region",
                allowed_codes=["NA", "EU"],
            )
        ],
    )
    g1 = apply_ontology_delta(g0, d_enum)
    assert g1.ontology_version == "0.1.0"
    assert len(g1.canonical_business_concepts) == 0

    # MAJOR op on 0.1.0 draft with 0 CBCs is rejected by SemVer calculus & applier
    d_major = OntologyDelta(
        delta_id="d-bad-major",
        base_ontology_version="0.1.0",
        target_ontology_version="0.2.0",
        operations=[RemoveConceptOp(op_id="rm-1", concept_uri="urn:ontology:entity:ghost")],
    )
    with pytest.raises(SemVerCalculusError):
        compute_next_semver("0.1.0", d_major, base_ontology=g1)
    with pytest.raises(SemVerCalculusError):
        apply_ontology_delta(g1, d_major, actor_role="admin")

    # Adding first CBC to 0.1.0 promotes to 1.0.0!
    d_first_cbc = OntologyDelta(
        delta_id="d-promote-1",
        base_ontology_version="0.1.0",
        target_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="op-c1",
                concept_uri="urn:ontology:entity:depot",
                concept_name="Depot",
                target_hub="hub_depot",
            )
        ],
    )
    g2 = apply_ontology_delta(g1, d_first_cbc)
    assert g2.ontology_version == "1.0.0"
    assert len(g2.canonical_business_concepts) == 1


def test_applier_preserves_100_percent_dto_fields_and_enforces_duplicate_preconditions(
    base_ontology: GraphAOntology,
) -> None:
    """MAJ-01, P05::MAJ-07 & MAJ-03: Preserve all DTO fields on new entities/relationships/enums
    and enforce non-destructive additive semantics + duplicate preconditions on existing items.
    """
    from opendv_contracts.dtos.ontology import (
        AddAttributeOp,
        AddReferenceEnumCodeOp,
        AddSynonymOp,
        SetParentConceptOp,
    )
    from opendv_contracts.errors import ErrorCode, OpenDVError

    d_rich = OntologyDelta(
        delta_id="d-rich-1",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="c-shipment",
                concept_uri="urn:ontology:entity:shipment",
                concept_name="Shipment",
                target_hub="hub_shipment",
                definition="Freight shipment unit.",
                description="Detailed shipment narrative.",
                parent_concept_uri="urn:ontology:entity:depot",
                synonyms=["Consignment", "Load"],
                attributes=["weight_kg"],
                external_mappings=[
                    {
                        "scheme": "FIBO",
                        "target_uri": "https://spec.edmcouncil.org/fibo/Shipment",
                        "predicate": "skos:closeMatch",
                        "confidence": 0.80,
                    }
                ],
                source_hints=["erp.shipments.shipment_id"],
                metadata={"steward_team": "freight"},
            ),
            AddRelationshipOp(
                op_id="r-depot-shipment",
                relationship_uri="urn:ontology:relationship:depot_shipment",
                relationship_name="DepotShipment",
                target_link="lnk_depot_shipment",
                source_concept_uri="urn:ontology:entity:depot",
                target_concept_uri="urn:ontology:entity:shipment",
                description="Origin depot of shipment",
                cardinality="one_to_many",
            ),
            AddReferenceEnumerationOp(
                op_id="e-mode",
                enum_uri="urn:ontology:enum:transport_mode",
                name="transport_mode",
                target_reference_table="ref_transport_mode",
                allowed_codes=["AIR", "SEA"],
                description="Mode of transport",
                code_descriptions={"AIR": "Air cargo", "SEA": "Ocean freight"},
                metadata={"iso_ref": "ISO-MODES"},
            ),
            RefineDefinitionOp(
                op_id="ref-def-1",
                target_uri="urn:ontology:entity:depot",
                refined_description="Refined depot definition.",
                additional_attributes=["dock_count"],
            ),
        ],
    )
    updated = apply_ontology_delta(base_ontology, d_rich)
    ship = next(
        c
        for c in updated.canonical_business_concepts
        if c.concept_uri == "urn:ontology:entity:shipment"
    )
    assert ship.description == "Detailed shipment narrative."
    assert ship.parent_concept_uri == "urn:ontology:entity:depot"
    assert ship.synonyms == ["Consignment", "Load"]
    assert ship.attributes is not None and ship.attributes[0].attribute_name == "weight_kg"
    assert ship.external_mappings is not None and len(ship.external_mappings) == 1
    assert ship.source_hints == ["erp.shipments.shipment_id"]
    assert ship.metadata == {"steward_team": "freight"}

    rel = next(
        r
        for r in updated.canonical_relationships
        if r.relationship_uri == "urn:ontology:relationship:depot_shipment"
    )
    assert rel.description == "Origin depot of shipment"
    assert rel.cardinality == "one_to_many"

    enum = next(
        e
        for e in updated.reference_enumerations
        if e.enum_uri == "urn:ontology:enum:transport_mode"
    )
    assert enum.enumeration_name == "transport_mode"
    assert enum.description == "Mode of transport"
    assert enum.code_descriptions == {"AIR": "Air cargo", "SEA": "Ocean freight"}
    assert enum.metadata == {"iso_ref": "ISO-MODES"}

    depot = next(
        c
        for c in updated.canonical_business_concepts
        if c.concept_uri == "urn:ontology:entity:depot"
    )
    assert depot.attributes is not None and any(
        a.attribute_name == "dock_count" for a in depot.attributes
    )

    # Duplicate preconditions raise typed OpenDVError codes
    for dup_op, expected_code in (
        (
            AddAttributeOp(
                op_id="dup-a",
                concept_uri="urn:ontology:entity:shipment",
                attribute_name="weight_kg",
            ),
            ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
        ),
        (
            AddSynonymOp(
                op_id="dup-s",
                concept_uri="urn:ontology:entity:shipment",
                synonym="Consignment",
            ),
            ErrorCode.DUPLICATE_SYNONYM,
        ),
        (
            AddReferenceEnumCodeOp(
                op_id="dup-c",
                enum_uri="urn:ontology:enum:transport_mode",
                code="AIR",
            ),
            ErrorCode.DUPLICATE_ENUM_CODE,
        ),
        (
            AddConceptOp(
                op_id="dup-parent-overwrite",
                concept_uri="urn:ontology:entity:shipment",
                concept_name="Shipment",
                parent_concept_uri="urn:ontology:entity:vehicle",
            ),
            ErrorCode.PARENT_ALREADY_EXISTS,
        ),
    ):
        d_dup = OntologyDelta(
            delta_id="d-dup",
            base_ontology_version="1.1.0",
            target_ontology_version="1.2.0" if dup_op.semver_class == "MINOR" else "1.1.1",
            operations=[dup_op],
        )
        with pytest.raises(OpenDVError) as exc_info:
            apply_ontology_delta(updated, d_dup)
        assert exc_info.value.error_code == expected_code
    _ = SetParentConceptOp


def test_apply_ontology_delta_enforces_destructive_admin_gate_and_deployed_contracts(
    base_ontology: GraphAOntology, deployed_contract: dict
) -> None:
    """P05::MAJ-09: apply_ontology_delta rejects destructive ops when actor_role != 'admin'
    (FORBIDDEN_ADMIN_REQUIRED 403/7) and enforces deployed contract immutability even for admin.
    """
    from opendv_contracts.dtos.ontology import RemoveConceptOp
    from opendv_contracts.errors import ErrorCode, OpenDVError
    from opendv_service.ontology import ContractViolationError

    d_rm = OntologyDelta(
        delta_id="d-rm-depot",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            RemoveConceptOp(
                op_id="rm-depot", concept_uri="urn:ontology:entity:depot", reason="drop"
            )
        ],
    )
    with pytest.raises(OpenDVError) as exc_info:
        apply_ontology_delta(base_ontology, d_rm, actor_role="steward")
    assert exc_info.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED
    assert exc_info.value.http_status == 403
    assert exc_info.value.exit_code == 7

    # Admin role passes RBAC gate, but deployed_contract locks hub_depot (ONT-EVO-001)
    with pytest.raises(ContractViolationError):
        apply_ontology_delta(
            base_ontology,
            d_rm,
            actor_role="admin",
            deployed_contract=deployed_contract,
        )


def test_r3_loader_rejects_untrusted_bootstrap_metadata_by_default() -> None:
    """S2-MAJ-02: load_ontology_delta, load_ontology_delta_dict, and load_ontology_delta_from_bytes
    never auto-enable allow_bootstrap from untrusted metadata.mode == 'BOOTSTRAP'.
    """
    import json

    from opendv_contracts.errors import InputValidationError
    from opendv_service.ontology.loader import (
        load_ontology_delta,
        load_ontology_delta_dict,
        load_ontology_delta_from_bytes,
    )

    spoofed_payload = {
        "delta_id": "d-spoof-boot",
        "base_ontology_version": "1.0.0",
        "target_ontology_version": "1.0.0",
        "metadata": {"mode": "BOOTSTRAP"},
        "operations": [
            {
                "op_id": "op-1",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:attacker",
                "concept_name": "Attacker",
                "target_hub": "hub_attacker",
            }
        ],
    }
    raw_bytes = json.dumps(spoofed_payload).encode("utf-8")

    with pytest.raises(InputValidationError, match=r"Target version '1\.0\.0' is invalid"):
        load_ontology_delta(spoofed_payload)
    with pytest.raises(InputValidationError, match=r"Target version '1\.0\.0' is invalid"):
        load_ontology_delta_dict(spoofed_payload)
    with pytest.raises(InputValidationError, match=r"Target version '1\.0\.0' is invalid"):
        load_ontology_delta_from_bytes(raw_bytes)

    explicit = load_ontology_delta_dict(spoofed_payload, allow_bootstrap=True)
    assert explicit.target_ontology_version == "1.0.0"


def test_r3_applier_gates_bootstrap_domain_namespace_and_enforce_grounding(
    base_ontology: GraphAOntology,
) -> None:
    """P05a MAJ-01..03:
    - allow_bootstrap=True blocks 1.0.0 -> 1.0.0 on populated base (needs legacy IP-1 flag);
    - mismatched domain_namespace raises OntologyContractViolationError outside legacy bootstrap;
    - apply_ontology_delta(..., enforce_grounding=True) enforces Path A FIBO & HMAC K_waiver.
    """
    import hashlib
    import hmac
    from uuid import uuid4

    from opendv_contracts.dtos.ontology import StewardWaiverRecord
    from opendv_contracts.errors import ErrorCode
    from opendv_service.ontology.applier import apply, apply_ontology_delta
    from opendv_service.ontology.loader import load_ontology_delta_dict
    from opendv_service.ontology.validator import (
        InvalidGroundingWaiverError,
        OntologyContractViolationError,
        SemVerCalculusError,
        UngroundedConceptError,
    )

    boot_same_ns = load_ontology_delta_dict(
        {
            "delta_id": "d-ip1-same-ns",
            "domain_namespace": base_ontology.domain_namespace,
            "base_ontology_version": "1.0.0",
            "target_ontology_version": "1.0.0",
            "metadata": {"mode": "BOOTSTRAP"},
            "operations": [
                {
                    "op_id": "op-c-boot-0",
                    "op_type": "add_concept",
                    "concept_uri": "urn:ontology:entity:route",
                    "concept_name": "Route",
                    "target_hub": "hub_route",
                }
            ],
        },
        allow_bootstrap=True,
    )
    with pytest.raises(SemVerCalculusError):
        apply(base_ontology, boot_same_ns, allow_bootstrap=True)

    boot_delta = load_ontology_delta_dict(
        {
            "delta_id": "d-ip1-boot",
            "domain_namespace": "override_ns",
            "base_ontology_version": "1.0.0",
            "target_ontology_version": "1.0.0",
            "metadata": {"mode": "BOOTSTRAP"},
            "operations": [
                {
                    "op_id": "op-c-boot",
                    "op_type": "add_concept",
                    "concept_uri": "urn:ontology:entity:route",
                    "concept_name": "Route",
                    "target_hub": "hub_route",
                }
            ],
        },
        allow_bootstrap=True,
    )
    legacy_applied = apply(base_ontology, boot_delta, allow_legacy_ip1_bootstrap=True)
    assert legacy_applied.ontology_version == "1.0.0"
    assert legacy_applied.domain_namespace == "override_ns"

    # P05a MAJ-03: non-bootstrap domain_namespace mismatch raises ONT_EVO_CONTRACT_VIOLATION
    ns_mismatch_delta = OntologyDelta(
        delta_id="d-ns-mismatch",
        domain_namespace="foreign_domain",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-c-ns",
                concept_uri="urn:ontology:entity:carrier",
                concept_name="Carrier",
                target_hub="hub_carrier",
            )
        ],
    )
    with pytest.raises(OntologyContractViolationError) as ns_exc:
        apply(base_ontology, ns_mismatch_delta)
    assert ns_exc.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    # P05a MAJ-01: enforce_grounding=True blocks ungrounded concepts and verifies HMAC K_waiver
    ungrounded_delta = OntologyDelta(
        delta_id="d-enforce-gnd",
        domain_namespace=base_ontology.domain_namespace,
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        operations=[
            AddConceptOp(
                op_id="op-ungrounded",
                concept_uri="urn:ontology:entity:custom_widget",
                concept_name="Custom Widget",
                target_hub="hub_custom_widget",
            )
        ],
    )
    existing_uris = {c.concept_uri for c in base_ontology.canonical_business_concepts}
    with pytest.raises(UngroundedConceptError):
        apply_ontology_delta(
            base_ontology,
            ungrounded_delta,
            enforce_grounding=True,
            grandfathered_concept_uris=existing_uris,
        )

    secret = b"r3-applier-waiver-hmac-secret-32"
    unsigned_waiver = StewardWaiverRecord(
        proposal_id=uuid4(),
        proposal_delta_sha256=ungrounded_delta.canonical_sha256(),
        base_sha256=compute_ontology_sha256(base_ontology),
        target_concept_uris=["urn:ontology:entity:custom_widget"],
        rationale="Approved bespoke logistics widget concept",
        authorized_by="chief-steward",
        authorized_role="admin",
        signature_or_token="forged-signature",
    )
    with pytest.raises(InvalidGroundingWaiverError):
        apply_ontology_delta(
            base_ontology,
            ungrounded_delta,
            enforce_grounding=True,
            waivers=[unsigned_waiver],
            waiver_secret=secret,
            grandfathered_concept_uris=existing_uris,
        )

    valid_sig = hmac.new(
        secret,
        unsigned_waiver.signing_payload().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    signed_waiver = unsigned_waiver.model_copy(update={"signature_or_token": valid_sig})
    waived_res = apply_ontology_delta(
        base_ontology,
        ungrounded_delta,
        enforce_grounding=True,
        waivers=[signed_waiver],
        waiver_secret=secret,
        grandfathered_concept_uris=existing_uris,
    )
    assert any(
        c.concept_uri == "urn:ontology:entity:custom_widget"
        for c in waived_res.canonical_business_concepts
    )
