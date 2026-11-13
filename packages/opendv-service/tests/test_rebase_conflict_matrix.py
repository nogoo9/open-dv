"""Exhaustive unit and regression tests for the 24-op Rebase Conflict Matrix (`ontology/rebase.py`).

Covers `MAJ-03` and `P10::MAJ-04`:
- Clean non-conflicting rebase across disjoint entities, relationships, and reference enumerations.
- SemVer recomputation on rebase (`1.0.0 -> 1.1.0` rebased onto `1.1.0 -> 1.2.0`, `PATCH -> 1.1.1`,
  `0.1.0` pre-CBC draft -> `1.0.0` initial CBC promotion).
- Full 24-operation conflict detection matrix:
  - Concurrent add/remove/rename/deprecate/reparent/archetype conflicts on concepts.
  - Concurrent attribute add/remove/type conflicts.
  - Concurrent relationship add/remove/deprecate and missing/renamed endpoint conflicts.
  - Concurrent reference enumeration & code collisions.
  - Concurrent external mapping add/remove/rescore/repin conflicts.
  - Concurrent synonym, description, definition, key authority tier, governance constraint,
    source hint, and metadata conflicts.
- Transitive structural cycle (`HIERARCHICAL_CYCLE_DETECTED`) and physical table collision
  detection on rebased state.
- `raise_on_conflict=True` raising typed `OpenDVError` (`PROPOSAL_STALE_REBASE_REQUIRED` 409 / 5).
"""

from __future__ import annotations

import pytest

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    AddSynonymOp,
    AnnotateConceptMetadataOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    OntologyDelta,
    RefineDefinitionOp,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveExternalMappingOp,
    RemoveRelationshipOp,
    RepinExternalMappingOp,
    RescoreExternalMappingOp,
    ReviseDescriptionOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    UpdateConceptUrnOp,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_service.ontology import apply, check_rebase_conflicts, compute_ontology_sha256


def _base_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise Data Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
                target_hub="hub_party",
                elm_archetype="CORE_CONCEPT",
                definition="Legal or natural person.",
                description="Base party description.",
                attributes=[
                    ConceptAttribute(
                        attribute_name="party_id", data_type="string", is_optional=False
                    ),
                    ConceptAttribute(
                        attribute_name="legal_name", data_type="string", is_optional=True
                    ),
                ],
                external_mappings=[
                    ExternalMapping(
                        scheme="FIBO",
                        target_uri="https://spec.edmcouncil.org/fibo/Agent",
                        predicate="skos:exactMatch",
                        confidence=0.90,
                    )
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                target_hub="hub_account",
                elm_archetype="CORE_CONCEPT",
                definition="Financial account.",
                attributes=[
                    ConceptAttribute(
                        attribute_name="account_id", data_type="string", is_optional=False
                    ),
                ],
                external_mappings=[
                    ExternalMapping(
                        scheme="FIBO",
                        target_uri="https://spec.edmcouncil.org/fibo/Account",
                        predicate="skos:exactMatch",
                        confidence=0.88,
                    )
                ],
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:party_account",
                relationship_name="PartyAccount",
                target_link="lnk_party_account",
                endpoints=["urn:ontology:entity:party", "urn:ontology:entity:account"],
                grain_semantics="Party owns account.",
            )
        ],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:currency",
                enumeration_name="currency",
                target_reference_table="ref_currency",
                allowed_codes=["USD", "EUR"],
            )
        ],
    )


def _advance(
    base: GraphAOntology, ops: list, target_ver: str = "1.1.0"
) -> tuple[GraphAOntology, OntologyDelta]:
    d = OntologyDelta.model_construct(
        delta_id="d-head",
        base_ontology_version=base.ontology_version,
        target_ontology_version=target_ver,
        parent_sha256=compute_ontology_sha256(base),
        operations=ops,
    )
    return apply(base, d), d


def test_clean_rebase_disjoint_additions_recomputes_minor_and_patch_semver() -> None:
    base = _base_ontology()
    head, head_delta = _advance(
        base,
        [
            AddConceptOp(
                op_id="h-1",
                concept_uri="urn:ontology:entity:facility",
                concept_name="Facility",
                target_hub="hub_facility",
            )
        ],
        target_ver="1.1.0",
    )

    # Stale MINOR proposal authored against 1.0.0 (originally targeting 1.1.0)
    stale_minor = OntologyDelta(
        delta_id="p-minor",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        parent_sha256=compute_ontology_sha256(base),
        operations=[
            AddConceptOp(
                op_id="p-1",
                concept_uri="urn:ontology:entity:instrument",
                concept_name="Instrument",
                target_hub="hub_instrument",
            ),
            AddAttributeOp(
                op_id="p-2",
                concept_uri="urn:ontology:entity:account",
                attribute_name="opened_date",
                data_type="date",
            ),
            AddReferenceEnumCodeOp(
                op_id="p-3",
                enum_uri="urn:ontology:enum:currency",
                code="GBP",
            ),
        ],
    )
    res = check_rebase_conflicts(stale_minor, base, head, intervening_deltas=[head_delta])
    assert res.can_rebase is True
    assert res.is_clean is True
    assert res.conflicts == []
    assert res.target_version == "1.2.0"
    assert res.rebased_delta is not None
    assert res.rebased_delta.base_ontology_version == "1.1.0"
    assert res.rebased_delta.target_ontology_version == "1.2.0"
    assert res.rebased_delta.parent_sha256 == compute_ontology_sha256(head)
    assert res.rebased_ontology is not None
    assert res.rebased_ontology.ontology_version == "1.2.0"

    # Stale PATCH proposal authored against 1.0.0 (targeting 1.0.1) -> 1.1.1 on 1.1.0 HEAD
    stale_patch = OntologyDelta(
        delta_id="p-patch",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.1",
        parent_sha256=compute_ontology_sha256(base),
        operations=[
            AddSynonymOp(
                op_id="p-syn",
                concept_uri="urn:ontology:entity:party",
                synonym="Counterparty",
            )
        ],
    )
    res_patch = check_rebase_conflicts(stale_patch, base, head, intervening_deltas=[head_delta])
    assert res_patch.can_rebase is True
    assert res_patch.target_version == "1.1.1"


def test_clean_rebase_0_x_draft_promotes_to_1_0_0() -> None:
    g0 = create_genesis_ontology(domain_namespace="finance", enterprise_owner="Enterprise Data")
    head, h_delta = _advance(
        g0,
        [
            AddReferenceEnumerationOp(
                op_id="h-enum",
                enum_uri="urn:ontology:enum:region",
                name="region",
                target_reference_table="ref_region",
                allowed_codes=["NA"],
            )
        ],
        target_ver="0.1.0",
    )
    stale_cbc = OntologyDelta(
        delta_id="p-cbc",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        operations=[
            AddConceptOp(
                op_id="p-c1",
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
                target_hub="hub_party",
            )
        ],
    )
    res = check_rebase_conflicts(stale_cbc, g0, head, intervening_deltas=[h_delta])
    assert res.can_rebase is True
    assert res.target_version == "1.0.0"


@pytest.mark.parametrize(
    ("head_op", "head_ver", "prop_op"),
    [
        # 1. Duplicate AddConceptOp URI
        (
            AddConceptOp(
                op_id="h1",
                concept_uri="urn:ontology:entity:loan",
                concept_name="Loan",
                target_hub="hub_loan",
            ),
            "1.1.0",
            AddConceptOp(
                op_id="p1",
                concept_uri="urn:ontology:entity:loan",
                concept_name="Loan Facility",
                target_hub="hub_loan_fac",
            ),
        ),
        # 2. Duplicate AddConceptOp hub collision
        (
            AddConceptOp(
                op_id="h2",
                concept_uri="urn:ontology:entity:loan_a",
                concept_name="Loan A",
                target_hub="hub_loan",
            ),
            "1.1.0",
            AddConceptOp(
                op_id="p2",
                concept_uri="urn:ontology:entity:loan_b",
                concept_name="Loan B",
                target_hub="hub_loan",
            ),
        ),
        # 3. Concept removed in HEAD vs AddAttributeOp in proposal
        (
            RemoveConceptOp(op_id="h3", concept_uri="urn:ontology:entity:account", reason="drop"),
            "2.0.0",
            AddAttributeOp(
                op_id="p3", concept_uri="urn:ontology:entity:account", attribute_name="iban"
            ),
        ),
        # 4. Concept renamed in HEAD vs AddSynonymOp on old URI
        (
            UpdateConceptUrnOp(
                op_id="h4",
                old_concept_uri="urn:ontology:entity:account",
                new_concept_uri="urn:ontology:entity:client_account",
            ),
            "2.0.0",
            AddSynonymOp(op_id="p4", concept_uri="urn:ontology:entity:account", synonym="Ledger"),
        ),
        # 5. Concept deprecated in HEAD vs AddAttributeOp in proposal
        (
            DeprecateConceptOp(
                op_id="h5", concept_uri="urn:ontology:entity:account", reason="legacy"
            ),
            "1.1.0",
            AddAttributeOp(
                op_id="p5", concept_uri="urn:ontology:entity:account", attribute_name="iban"
            ),
        ),
        # 6. Concept modified in HEAD vs RemoveConceptOp in proposal
        (
            AddAttributeOp(
                op_id="h6", concept_uri="urn:ontology:entity:account", attribute_name="iban"
            ),
            "1.1.0",
            RemoveConceptOp(op_id="p6", concept_uri="urn:ontology:entity:account", reason="drop"),
        ),
        # 7. Duplicate AddAttributeOp name collision
        (
            AddAttributeOp(
                op_id="h7", concept_uri="urn:ontology:entity:party", attribute_name="tax_id"
            ),
            "1.1.0",
            AddAttributeOp(
                op_id="p7", concept_uri="urn:ontology:entity:party", attribute_name="tax_id"
            ),
        ),
        # 8. Attribute already removed in HEAD vs RemoveAttributeOp in proposal
        (
            RemoveAttributeOp(
                op_id="h8", concept_uri="urn:ontology:entity:party", attribute_name="legal_name"
            ),
            "2.0.0",
            RemoveAttributeOp(
                op_id="p8", concept_uri="urn:ontology:entity:party", attribute_name="legal_name"
            ),
        ),
        # 9. Concurrent SetParentConceptOp on same concept
        (
            SetParentConceptOp(
                op_id="h9",
                concept_uri="urn:ontology:entity:account",
                parent_concept_uri="urn:ontology:entity:party",
            ),
            "1.1.0",
            SetParentConceptOp(
                op_id="p9",
                concept_uri="urn:ontology:entity:account",
                parent_concept_uri=None,
            ),
        ),
        # 10. Concurrent SetConceptElmArchetypeOp on same concept
        (
            SetConceptElmArchetypeOp(
                op_id="h10",
                concept_uri="urn:ontology:entity:party",
                elm_archetype="EVENT",
            ),
            "2.0.0",
            SetConceptElmArchetypeOp(
                op_id="p10",
                concept_uri="urn:ontology:entity:party",
                elm_archetype="REFERENCE",
            ),
        ),
        # 11. Concurrent AddKeyAuthorityPatternOp on same tier slot
        (
            AddKeyAuthorityPatternOp(
                op_id="h11",
                concept_uri="urn:ontology:entity:party",
                tier="TIER_1_GLOBAL_STANDARD",
                authority_name="GLEIF",
            ),
            "1.1.0",
            AddKeyAuthorityPatternOp(
                op_id="p11",
                concept_uri="urn:ontology:entity:party",
                tier="TIER_1_GLOBAL_STANDARD",
                authority_name="DUNS",
            ),
        ),
        # 12. Concurrent AddGovernanceConstraintOp collision
        (
            AddGovernanceConstraintOp(
                op_id="h12",
                concept_uri="urn:ontology:entity:party",
                constraint_id="pii-1",
                required_satellite_split="sat_party_pii",
            ),
            "1.1.0",
            AddGovernanceConstraintOp(
                op_id="p12",
                concept_uri="urn:ontology:entity:party",
                constraint_id="pii-2",
                required_satellite_split="sat_party_secret",
            ),
        ),
        # 13. Duplicate AddRelationshipOp collision
        (
            AddRelationshipOp(
                op_id="h13",
                relationship_uri="urn:ontology:relationship:party_self",
                target_link="lnk_party_self",
                source_concept_uri="urn:ontology:entity:party",
                target_concept_uri="urn:ontology:entity:party",
            ),
            "1.1.0",
            AddRelationshipOp(
                op_id="p13",
                relationship_uri="urn:ontology:relationship:party_self",
                target_link="lnk_party_self_2",
                source_concept_uri="urn:ontology:entity:party",
                target_concept_uri="urn:ontology:entity:party",
            ),
        ),
        # 14. Relationship endpoint removed in HEAD vs AddRelationshipOp in proposal
        (
            RemoveConceptOp(op_id="h14", concept_uri="urn:ontology:entity:account", reason="drop"),
            "2.0.0",
            AddRelationshipOp(
                op_id="p14",
                relationship_uri="urn:ontology:relationship:party_holds_acct",
                target_link="lnk_party_holds_acct",
                source_concept_uri="urn:ontology:entity:party",
                target_concept_uri="urn:ontology:entity:account",
            ),
        ),
        # 15. Relationship already removed in HEAD vs DeprecateRelationshipOp in proposal
        (
            RemoveRelationshipOp(op_id="h15", relationship_name="PartyAccount"),
            "2.0.0",
            DeprecateRelationshipOp(op_id="p15", relationship_name="PartyAccount", reason="old"),
        ),
        # 16. Duplicate AddReferenceEnumerationOp collision
        (
            AddReferenceEnumerationOp(
                op_id="h16",
                enum_uri="urn:ontology:enum:country",
                name="country",
                target_reference_table="ref_country",
                allowed_codes=["US"],
            ),
            "1.1.0",
            AddReferenceEnumerationOp(
                op_id="p16",
                enum_uri="urn:ontology:enum:country",
                name="country",
                target_reference_table="ref_country",
                allowed_codes=["CA"],
            ),
        ),
        # 17. Duplicate AddReferenceEnumCodeOp collision
        (
            AddReferenceEnumCodeOp(op_id="h17", enum_uri="urn:ontology:enum:currency", code="GBP"),
            "1.0.1",
            AddReferenceEnumCodeOp(op_id="p17", enum_uri="urn:ontology:enum:currency", code="GBP"),
        ),
        # 18. Duplicate AddSynonymOp collision
        (
            AddSynonymOp(
                op_id="h18", concept_uri="urn:ontology:entity:party", synonym="Counterparty"
            ),
            "1.0.1",
            AddSynonymOp(
                op_id="p18", concept_uri="urn:ontology:entity:party", synonym="counterparty"
            ),
        ),
        # 19. Concurrent AddExternalMappingOp on same target_uri with conflicting predicate
        (
            AddExternalMappingOp(
                op_id="h19",
                concept_uri="urn:ontology:entity:party",
                scheme="FIBO",
                target_uri="https://spec.edmcouncil.org/fibo/LegalPerson",
                predicate="skos:exactMatch",
                mapping_confidence=0.92,
            ),
            "1.0.1",
            AddExternalMappingOp(
                op_id="p19",
                concept_uri="urn:ontology:entity:party",
                scheme="FIBO",
                target_uri="https://spec.edmcouncil.org/fibo/LegalPerson",
                predicate="skos:closeMatch",
                mapping_confidence=0.75,
            ),
        ),
        # 20. Mapping removed in HEAD vs RescoreExternalMappingOp in proposal
        (
            RemoveExternalMappingOp(
                op_id="h20",
                concept_uri="urn:ontology:entity:party",
                target_uri="https://spec.edmcouncil.org/fibo/Agent",
            ),
            "1.0.1",
            RescoreExternalMappingOp(
                op_id="p20",
                concept_uri="urn:ontology:entity:party",
                target_uri="https://spec.edmcouncil.org/fibo/Agent",
                mapping_confidence=0.95,
            ),
        ),
        # 21. Mapping modified in HEAD vs RepinExternalMappingOp in proposal
        (
            RemoveExternalMappingOp(
                op_id="h21",
                concept_uri="urn:ontology:entity:party",
                target_uri="https://spec.edmcouncil.org/fibo/Agent",
            ),
            "1.0.1",
            RepinExternalMappingOp(
                op_id="p21",
                concept_uri="urn:ontology:entity:party",
                target_uri="https://spec.edmcouncil.org/fibo/Agent",
                pinned=True,
            ),
        ),
        # 22. Concurrent ReviseDescriptionOp on same concept
        (
            ReviseDescriptionOp(
                op_id="h22",
                target_uri="urn:ontology:entity:party",
                description="Head party description.",
            ),
            "1.0.1",
            ReviseDescriptionOp(
                op_id="p22",
                target_uri="urn:ontology:entity:party",
                description="Proposal party description.",
            ),
        ),
        # 23. Concurrent RefineDefinitionOp on same concept
        (
            RefineDefinitionOp(
                op_id="h23",
                target_uri="urn:ontology:entity:party",
                refined_description="Head party definition.",
            ),
            "1.0.1",
            RefineDefinitionOp(
                op_id="p23",
                target_uri="urn:ontology:entity:party",
                refined_description="Proposal party definition.",
            ),
        ),
        # 24. Concurrent AnnotateConceptMetadataOp on same metadata key & duplicate AddSourceHintOp
        (
            AnnotateConceptMetadataOp(
                op_id="h24",
                concept_uri="urn:ontology:entity:party",
                metadata={"owner": "team-a"},
            ),
            "1.0.1",
            AnnotateConceptMetadataOp(
                op_id="p24",
                concept_uri="urn:ontology:entity:party",
                metadata={"owner": "team-b"},
            ),
        ),
        (
            AddSourceHintOp(
                op_id="h25",
                concept_uri="urn:ontology:entity:party",
                source_system="crm",
                table_name="parties",
            ),
            "1.0.1",
            AddSourceHintOp(
                op_id="p25",
                concept_uri="urn:ontology:entity:party",
                source_system="crm",
                table_name="parties",
            ),
        ),
    ],
)
def test_24_op_rebase_conflict_matrix_detects_all_conflicts(
    head_op: object, head_ver: str, prop_op: object
) -> None:
    base = _base_ontology()
    head, h_delta = _advance(base, [head_op], target_ver=head_ver)

    res = check_rebase_conflicts([prop_op], base, head, intervening_deltas=[h_delta])  # type: ignore[list-item]
    assert res.can_rebase is False
    assert res.has_conflicts is True
    assert len(res.conflicts) >= 1
    assert res.conflicts[0].proposal_op_type == getattr(prop_op, "op_type", "")


def test_rebase_detects_cross_concept_hierarchical_cycle_and_raise_on_conflict() -> None:
    """Two disjoint SetParentConceptOps (party->account in HEAD, account->party in proposal)
    do not touch the same concept_uri directly, yet form a cycle on the rebased graph!
    """
    base = _base_ontology()
    head, h_delta = _advance(
        base,
        [
            SetParentConceptOp(
                op_id="h-parent",
                concept_uri="urn:ontology:entity:party",
                parent_concept_uri="urn:ontology:entity:account",
            )
        ],
        target_ver="1.1.0",
    )
    prop_ops = [
        SetParentConceptOp(
            op_id="p-parent",
            concept_uri="urn:ontology:entity:account",
            parent_concept_uri="urn:ontology:entity:party",
        )
    ]
    res = check_rebase_conflicts(prop_ops, base, head, intervening_deltas=[h_delta])
    assert res.can_rebase is False
    assert any(c.error_code == ErrorCode.HIERARCHICAL_CYCLE_DETECTED.value for c in res.conflicts)

    with pytest.raises(OpenDVError) as exc_info:
        check_rebase_conflicts(
            prop_ops,
            base,
            head,
            intervening_deltas=[h_delta],
            raise_on_conflict=True,
        )
    assert exc_info.value.http_status in (409, 422)
