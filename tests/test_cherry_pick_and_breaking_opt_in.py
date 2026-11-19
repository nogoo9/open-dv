"""Phase 2 Track B TDD Tests: Unchecked Opt-In MAJOR Breaking Suggestions, Cascading Cherry-Pick & Inline Edits.

Covers:
- FR-AGT-08 / D5 / D7:
  - Unchecked-by-default MAJOR · Opt-In breaking suggestions bucket (routine runs preserve MINOR SemVer).
  - Parent-to-child cascading uncheck with zero dangling URIs.
  - Inline edits (concept name, attribute data type, mapping) & live Q&A cascade.
  - cherry_pick_commit producing valid GraphAOntology version + JCS SHA-256.
"""

from __future__ import annotations

from pathlib import Path

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddRelationshipOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
    RemoveAttributeOp,
)
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.studio.session_manager import StudioSessionManager


def _base_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="custody_ops",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:position",
                concept_name="Position",
                target_hub="hub_position",
                definition="Custody holding position.",
                attributes=[
                    ConceptAttribute(attribute_name="legacy_desk_memo", data_type="string", is_optional=True)
                ],
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position",
                        predicate="skos:exactMatch",
                        confidence=0.92,
                    )
                ],
            )
        ],
    )


def _sample_proposal(base: GraphAOntology) -> OntologyProposal:
    base_sha = compute_ontology_sha256(base)
    ev = [EvidenceRef(evidence_type="column_profile", bronze_table="positions", column_name="batch_id", confidence=0.9)]
    ops = [
        AddConceptOp(
            op_id="op-concept-batch",
            concept_uri="urn:ontology:entity:settlement_batch",
            concept_name="Settlement Batch",
            target_hub="hub_settlement_batch",
            definition="Batch of settlement instructions.",
            evidence_refs=ev,
        ),
        AddAttributeOp(
            op_id="op-attr-batch-cut",
            concept_uri="urn:ontology:entity:settlement_batch",
            attribute_name="cutoff_time",
            data_type="string",
            is_optional=True,
            evidence_refs=ev,
        ),
        AddRelationshipOp(
            op_id="op-rel-pos-batch",
            relationship_uri="urn:ontology:relationship:position_in_batch",
            relationship_name="position_in_batch",
            target_link="lnk_position_batch",
            source_concept_uri="urn:ontology:entity:position",
            target_concept_uri="urn:ontology:entity:settlement_batch",
            endpoints=["urn:ontology:entity:position", "urn:ontology:entity:settlement_batch"],
            evidence_refs=ev,
        ),
        RemoveAttributeOp(
            op_id="op-major-drop-memo",
            concept_uri="urn:ontology:entity:position",
            attribute_name="legacy_desk_memo",
            reason="Dropped from Wave 3 feed.",
            evidence_refs=ev,
        ),
    ]
    delta = OntologyDelta(
        delta_id="d-cp-01",
        parent_sha256=base_sha,
        base_version=base.ontology_version,
        target_version="2.0.0",
        domain_namespace=base.domain_namespace,
        operations=ops,
    )
    return OntologyProposal(
        proposal_id="prop-cp-01",
        recommender_id="agent:cp",
        base_ontology_sha256=base_sha,
        delta=delta,
        confidence=0.9,
        rationale="Mixed MINOR and MAJOR proposal.",
    )


def test_breaking_suggestions_isolated_and_unchecked_by_default() -> None:
    """MAJOR ops are isolated in breaking_suggestions and unchecked by default so default commit is MINOR."""
    from opendv_service.studio.cherry_pick_commit import (
        build_cherry_pick_plan,
        materialize_cherry_pick_delta,
    )

    base = _base_ontology()
    proposal = _sample_proposal(base)
    plan = build_cherry_pick_plan(base, proposal)

    assert len(plan.breaking_suggestions) == 1
    assert plan.breaking_suggestions[0].op_id == "op-major-drop-memo"
    assert plan.breaking_suggestions[0].checked is False
    assert plan.breaking_suggestions[0].bucket == "MAJOR · Opt-In"

    # Non-breaking items are checked by default
    assert all(item.checked is True for item in plan.standard_suggestions)

    # Materializing default selection excludes MAJOR ops -> MINOR SemVer bump (1.1.0)
    staged_delta = materialize_cherry_pick_delta(base, proposal, plan)
    op_ids = {op.op_id for op in staged_delta.operations}
    assert "op-major-drop-memo" not in op_ids
    assert staged_delta.target_version == "1.1.0"


def test_parent_to_child_cascade_uncheck_zero_dangling_uris() -> None:
    """Unchecking a parent concept cascades-uncheck to dependent attributes & links with zero dangling URIs."""
    from opendv_service.studio.cherry_pick_commit import (
        build_cherry_pick_plan,
        materialize_cherry_pick_delta,
        toggle_cherry_pick_item,
    )

    base = _base_ontology()
    proposal = _sample_proposal(base)
    plan = build_cherry_pick_plan(base, proposal)

    updated = toggle_cherry_pick_item(plan, op_id="op-concept-batch", checked=False)
    by_id = {item.op_id: item for item in updated.standard_suggestions}
    assert by_id["op-concept-batch"].checked is False
    assert by_id["op-attr-batch-cut"].checked is False
    assert by_id["op-rel-pos-batch"].checked is False

    staged_delta = materialize_cherry_pick_delta(base, proposal, updated)
    assert staged_delta.operations == []
    # Also verify no dangling concept URIs when partial ops remain
    target = apply_delta(base, staged_delta, validate_structural=True)
    known_uris = {c.concept_uri for c in target.canonical_business_concepts}
    for rel in target.canonical_relationships:
        for ep in rel.endpoints:
            assert ep in known_uris


def test_inline_edits_qa_cascade_and_cherry_pick_commit(tmp_path: Path) -> None:
    """Inline edits and Q&A cascade refine ops prior to cherry_pick_commit persisting new version."""
    from opendv_service.studio.cherry_pick_commit import (
        apply_inline_op_edit,
        build_cherry_pick_plan,
        cherry_pick_commit,
    )

    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path / "ws")
    base = create_genesis_ontology("custody_ops")
    mgr.create_session(session_id="cp-commit-01", domain_namespace="custody_ops")

    # Seed v1.0.0 from _base_ontology()
    base_v1 = _base_ontology()
    proposal = _sample_proposal(base_v1)
    plan = build_cherry_pick_plan(base_v1, proposal)

    # Inline edit attribute data_type from 'string' to 'timestamp' and rename concept
    plan = apply_inline_op_edit(
        plan,
        op_id="op-attr-batch-cut",
        updates={"data_type": "timestamp"},
    )
    plan = apply_inline_op_edit(
        plan,
        op_id="op-concept-batch",
        updates={"concept_name": "Custody Settlement Batch"},
    )

    result = cherry_pick_commit(
        session_mgr=mgr,
        session_id="cp-commit-01",
        base_ontology=base_v1,
        proposal=proposal,
        plan=plan,
        qa_answers={"q-1": {"selected_options": ["opt-separate-entity"]}},
    )
    assert result["committed_version"] == "1.1.0"
    committed_ont: GraphAOntology = result["ontology"]
    batch_c = next(
        c for c in committed_ont.canonical_business_concepts if c.concept_uri == "urn:ontology:entity:settlement_batch"
    )
    assert batch_c.concept_name == "Custody Settlement Batch"
    cutoff_attr = next(a for a in (batch_c.attributes or []) if a.attribute_name == "cutoff_time")
    assert cutoff_attr.data_type == "timestamp"
    assert result["ontology_sha256"] == compute_ontology_sha256(committed_ont)
