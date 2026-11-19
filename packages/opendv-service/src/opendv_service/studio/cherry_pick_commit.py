"""Unchecked MAJOR opt-in breaking isolation, cascading cherry-pick, inline edits, and commit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddRelationshipOp,
    OntologyDelta,
    OntologyOperation,
    OntologyProposal,
    SetParentConceptOp,
    StewardWaiverRecord,
    UpdateConceptUrnOp,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.session_manager import StudioSessionManager

MAJOR_OPT_IN_BUCKET = "MAJOR · Opt-In"


class CherryPickItem(BaseModel):
    """Selectable delta operation inside a cherry-pick plan."""

    model_config = ConfigDict(extra="forbid")

    op_id: str
    op_type: str
    semver_class: str
    bucket: str
    checked: bool
    parent_concept_uri: str | None = None
    target_uris: list[str] = Field(default_factory=list)
    operation: dict[str, Any]


class CherryPickPlan(BaseModel):
    """Plan separating standard additive ops from unchecked MAJOR Opt-In breaking suggestions."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    standard_suggestions: list[CherryPickItem] = Field(default_factory=list)
    breaking_suggestions: list[CherryPickItem] = Field(default_factory=list)

    def all_items(self) -> list[CherryPickItem]:
        """Return combined standard and breaking suggestion items."""
        return [*self.standard_suggestions, *self.breaking_suggestions]


def _op_target_uris(op: OntologyOperation) -> tuple[str | None, list[str]]:
    """Return `(primary_concept_uri, all_referenced_concept_uris)` for dependency cascade."""
    if isinstance(op, AddConceptOp):
        return op.concept_uri, [op.concept_uri]
    if isinstance(op, AddRelationshipOp):
        eps = list(op.endpoints)
        if op.source_concept_uri and op.source_concept_uri not in eps:
            eps.append(op.source_concept_uri)
        if op.target_concept_uri and op.target_concept_uri not in eps:
            eps.append(op.target_concept_uri)
        return None, eps
    if isinstance(op, UpdateConceptUrnOp):
        return op.new_concept_uri, [op.old_concept_uri, op.new_concept_uri]
    if isinstance(op, SetParentConceptOp):
        refs = [op.concept_uri] + ([op.parent_concept_uri] if op.parent_concept_uri else [])
        return op.concept_uri, refs
    c_uri = getattr(op, "concept_uri", None) or getattr(op, "target_uri", None)
    return (c_uri, [c_uri]) if c_uri else (None, [])


def build_cherry_pick_plan(
    base_ontology: GraphAOntology, proposal: OntologyProposal
) -> CherryPickPlan:
    """Partition proposal ops into checked standard items and unchecked MAJOR Opt-In items."""
    standard: list[CherryPickItem] = []
    breaking: list[CherryPickItem] = []

    for op in proposal.delta.operations:
        eff_cls = op.effective_semver_class(base_ontology)
        primary_uri, refs = _op_target_uris(op)
        is_major = eff_cls == "MAJOR"
        item = CherryPickItem(
            op_id=op.op_id,
            op_type=op.op_type,
            semver_class=eff_cls,
            bucket=MAJOR_OPT_IN_BUCKET if is_major else f"{eff_cls} · Additive",
            checked=not is_major,
            parent_concept_uri=primary_uri,
            target_uris=refs,
            operation=op.model_dump(mode="json", exclude_none=True),
        )
        if is_major:
            breaking.append(item)
        else:
            standard.append(item)

    return CherryPickPlan(
        proposal_id=proposal.proposal_id,
        standard_suggestions=standard,
        breaking_suggestions=breaking,
    )


def toggle_cherry_pick_item(
    plan: CherryPickPlan,
    *,
    op_id: str,
    checked: bool,
) -> CherryPickPlan:
    """Toggle selection of `op_id` with parent-to-child cascading uncheck (zero dangling URIs)."""
    updated = plan.model_copy(deep=True)
    all_items = updated.all_items()
    target = next((item for item in all_items if item.op_id == op_id), None)
    if target is None:
        return updated

    target.checked = checked
    if not checked and target.op_type == "add_concept" and target.parent_concept_uri:
        unchecked_uris: set[str] = {target.parent_concept_uri}
        changed = True
        while changed:
            changed = False
            for item in all_items:
                if not item.checked:
                    continue
                if any(u in unchecked_uris for u in item.target_uris):
                    item.checked = False
                    if item.op_type == "add_concept" and item.parent_concept_uri:
                        unchecked_uris.add(item.parent_concept_uri)
                    changed = True
    return updated


def apply_inline_op_edit(
    plan: CherryPickPlan,
    *,
    op_id: str,
    updates: dict[str, Any],
) -> CherryPickPlan:
    """Apply inline edits (concept_name, data_type, external_mappings, etc.) to a staged op."""
    updated = plan.model_copy(deep=True)
    for item in updated.all_items():
        if item.op_id == op_id:
            item.operation = {**item.operation, **updates}
            break
    return updated


def apply_qa_cascade(
    plan: CherryPickPlan,
    qa_answers: dict[str, Any] | None = None,
) -> CherryPickPlan:
    """Propagate clarification Q&A selections into staged cherry-pick operation metadata."""
    if not qa_answers:
        return plan
    updated = plan.model_copy(deep=True)
    for item in updated.all_items():
        if item.op_type == "add_concept":
            meta = dict(item.operation.get("metadata") or {})
            meta["qa_answers_applied"] = sorted(qa_answers.keys())
            item.operation["metadata"] = meta
    return updated


def materialize_cherry_pick_delta(
    base_ontology: GraphAOntology,
    proposal: OntologyProposal,
    plan: CherryPickPlan,
    *,
    qa_answers: dict[str, Any] | None = None,
) -> OntologyDelta:
    """Build a valid OntologyDelta from checked items with zero dangling URIs and exact SemVer."""
    eff_plan = apply_qa_cascade(plan, qa_answers)
    base_uris = {c.concept_uri for c in base_ontology.canonical_business_concepts}

    selected_items = [item for item in eff_plan.all_items() if item.checked]
    added_uris = {
        str(item.operation.get("concept_uri"))
        for item in selected_items
        if item.op_type == "add_concept" and item.operation.get("concept_uri")
    }
    removed_uris = {
        str(item.operation.get("concept_uri"))
        for item in selected_items
        if item.op_type == "remove_concept" and item.operation.get("concept_uri")
    }
    valid_uris = (base_uris - removed_uris) | added_uris

    final_ops: list[dict[str, Any]] = []
    for item in selected_items:
        op_dict = dict(item.operation)
        if item.op_type in ("add_attribute", "remove_attribute", "add_external_mapping"):
            if op_dict.get("concept_uri") not in valid_uris:
                continue
        elif item.op_type == "add_relationship":
            eps = list(op_dict.get("endpoints") or [])
            if op_dict.get("source_concept_uri") and op_dict["source_concept_uri"] not in eps:
                eps.append(op_dict["source_concept_uri"])
            if op_dict.get("target_concept_uri") and op_dict["target_concept_uri"] not in eps:
                eps.append(op_dict["target_concept_uri"])
            if not eps or any(ep not in valid_uris for ep in eps):
                continue
        final_ops.append(op_dict)

    base_sha = compute_ontology_sha256(base_ontology)
    probe_delta = OntologyDelta.model_validate(
        {
            "delta_id": f"{proposal.delta.delta_id}-cp",
            "parent_sha256": base_sha,
            "base_ontology_version": base_ontology.ontology_version,
            "target_ontology_version": None,
            "domain_namespace": base_ontology.domain_namespace,
            "operations": final_ops,
        }
    )
    target_version = (
        compute_next_semver(base_ontology.ontology_version, probe_delta, base_ontology)
        if probe_delta.operations
        else base_ontology.ontology_version
    )
    return OntologyDelta.model_validate(
        {
            "delta_id": probe_delta.delta_id,
            "parent_sha256": base_sha,
            "base_ontology_version": base_ontology.ontology_version,
            "target_ontology_version": target_version,
            "domain_namespace": base_ontology.domain_namespace,
            "operations": final_ops,
        }
    )


def cherry_pick_commit(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    base_ontology: GraphAOntology,
    proposal: OntologyProposal,
    plan: CherryPickPlan,
    qa_answers: dict[str, Any] | None = None,
    author: str = "human:local_admin",
    actor_role: str = "admin",
    waivers: list[StewardWaiverRecord] | None = None,
) -> dict[str, Any]:
    """Materialize cherry-picked delta and commit atomically into session."""
    delta = materialize_cherry_pick_delta(
        base_ontology,
        proposal,
        plan,
        qa_answers=qa_answers,
    )
    meta = session_mgr.get_session(session_id)
    if meta.head_version == "0.0.0" and base_ontology.ontology_version != "0.0.0":
        # Seed base ontology version cleanly if session was created at genesis for caller-supplied base
        from opendv_service.studio.atomic_commit import AtomicCommitService as _Svc  # noqa: F401

        vdir = (
            session_mgr.get_session_dir(session_id)
            / "versions"
            / f"v{base_ontology.ontology_version}"
        )
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "ontology.json").write_text(
            base_ontology.model_dump_json(indent=2), encoding="utf-8"
        )
        from opendv_service.studio.session_manager import _atomic_write_session_json

        meta = meta.model_copy(
            update={
                "head_version": base_ontology.ontology_version,
                "head_sha256": compute_ontology_sha256(base_ontology),
            }
        )
        _atomic_write_session_json(session_mgr.get_session_dir(session_id), meta)

    svc = AtomicCommitService(
        session_manager=session_mgr,
        security_manager=session_mgr.security_manager,
    )
    commit_res = svc.commit_delta(
        session_id=session_id,
        delta=delta,
        proposal_id=proposal.proposal_id,
        author=author,
        if_match=f'"{meta.head_sha256}"',
        actor_role=actor_role,
        waivers=waivers or [],
    )
    ver_clean = str(commit_res.version).lstrip("v")
    committed_ont = GraphAOntology.model_validate_json(
        (
            session_mgr.get_session_dir(session_id) / "versions" / f"v{ver_clean}" / "ontology.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "committed_version": ver_clean,
        "ontology_sha256": commit_res.target_sha256,
        "ontology": committed_ont,
        "delta": delta,
    }


__all__ = [
    "MAJOR_OPT_IN_BUCKET",
    "CherryPickItem",
    "CherryPickPlan",
    "apply_inline_op_edit",
    "apply_qa_cascade",
    "build_cherry_pick_plan",
    "cherry_pick_commit",
    "materialize_cherry_pick_delta",
    "toggle_cherry_pick_item",
]
