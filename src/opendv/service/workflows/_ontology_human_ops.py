"""Human-only destructive/steward OntologyDelta handlers, receipt checks, and lock checks."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal

from opendv.grounding.providers.fibo.loader import (
    FiboPackQueryAdapter,
    FiboReferenceOntologyLoader,
    get_bundled_fibo_pack_sha256,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import GroundingReceipt
from opendv.models.grounding import FiboDriftItem
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.ontology_deltas import (
    OntologyDelta,
    RepinExternalMapping,
    UpdateAttributeMetadata,
    UpdateEnumerationMetadata,
    UpdateRelationshipMetadata,
)
from opendv.models.project import (
    EvolutionLockSpec,
    RevisionMetadata,
    validate_domain_namespace,
)
from opendv.models.sources import SourceProfile
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._ontology_grounding import compute_bump_spec


def locked_hub_keys(locks: Sequence[EvolutionLockSpec]) -> set[str]:
    names: set[str] = set()
    for lock in locks:
        names.update(lock.locked_hub_bks.keys())
    return names


def locked_link_keys(locks: Sequence[EvolutionLockSpec]) -> set[str]:
    names: set[str] = set()
    for lock in locks:
        names.update(lock.locked_links.keys())
    return names


def is_concept_locked(concept: CanonicalBusinessConcept | None, locked_hubs: set[str]) -> bool:
    if concept is None or not locked_hubs:
        return False
    slug = concept.concept_name.lower().replace(" ", "_")
    return (
        concept.concept_uri in locked_hubs
        or concept.concept_name in locked_hubs
        or slug in locked_hubs
        or f"hub_{slug}" in locked_hubs
    )


def rescore_mapping(
    concept: CanonicalBusinessConcept,
    mapping: ExternalMapping,
    matcher: FiboPackQueryAdapter,
    *,
    clear_receipt: bool = False,
) -> ExternalMapping:
    if mapping.grounding_receipt_id is not None and not clear_receipt:
        return mapping
    if clear_receipt and mapping.grounding_receipt_id is not None:
        mapping = mapping.model_copy(update={"grounding_receipt_id": None})
    if mapping.mapping_type not in {"skos:exactMatch", "skos:closeMatch"}:
        return mapping
    terms = [concept.concept_name, *concept.synonyms]
    best: Decimal | None = None
    for term in terms:
        if not term.strip():
            continue
        hit = matcher.score(term, mapping.standard_uri)
        if hit is not None and (best is None or hit > best):
            best = hit
    if best is None:
        return mapping.model_copy(update={"confidence": Decimal("0.0000")})
    return mapping.model_copy(
        update={
            "confidence": best,
            "reference_pack_sha256": (
                mapping.reference_pack_sha256 or get_bundled_fibo_pack_sha256()
            ),
        }
    )


def rescore_concept(
    concept: CanonicalBusinessConcept,
    matcher: FiboPackQueryAdapter,
    *,
    clear_receipts: bool = False,
) -> CanonicalBusinessConcept:
    if not concept.external_mappings:
        return concept
    return concept.model_copy(
        update={
            "external_mappings": [
                rescore_mapping(concept, m, matcher, clear_receipt=clear_receipts)
                for m in concept.external_mappings
            ]
        }
    )


def require_concept(
    concepts: dict[str, CanonicalBusinessConcept], uri: str
) -> CanonicalBusinessConcept:
    item = concepts.get(uri)
    if item is None:
        raise OpenDVError(
            f"Concept '{uri}' not found in Graph A ontology.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return item


def require_enum(
    enums: dict[str, OntologyEnumeration], uri: str
) -> OntologyEnumeration:
    item = enums.get(uri)
    if item is None:
        raise OpenDVError(
            f"Enumeration '{uri}' not found in Graph A ontology.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return item


def apply_human_delta_op(
    op: OntologyDelta,
    *,
    concepts: dict[str, CanonicalBusinessConcept],
    rels: dict[str, OntologyRelationship],
    enums: dict[str, OntologyEnumeration],
    namespace: str,
    locks: Sequence[EvolutionLockSpec],
    locked_hubs: set[str],
    locked_links: set[str],
    bm25: FiboPackQueryAdapter,
    receipts_by_id: Mapping[str, GroundingReceipt] | None = None,
) -> str:
    """Execute one of the 19 HUMAN_ONLY_OPS onto mutable dicts and return updated namespace."""
    if op.op_type == "ReparentConcept":
        c = require_concept(concepts, op.concept_uri)
        if is_concept_locked(c, locked_hubs):
            raise OpenDVError(
                f"Cannot reparent locked concept '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(
            update={"parent_concept_uri": op.parent_concept_uri}
        )
    elif op.op_type == "ClearParentConcept":
        c = require_concept(concepts, op.concept_uri)
        if is_concept_locked(c, locked_hubs):
            raise OpenDVError(
                f"Cannot clear parent on locked concept '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(update={"parent_concept_uri": None})
    elif op.op_type == "DeleteConcept":
        c = require_concept(concepts, op.concept_uri)
        if is_concept_locked(c, locked_hubs):
            raise OpenDVError(
                f"Cannot delete Vault-locked concept '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        del concepts[op.concept_uri]
    elif op.op_type == "RenameConcept":
        c = require_concept(concepts, op.concept_uri)
        if is_concept_locked(c, locked_hubs):
            raise OpenDVError(
                f"Cannot rename Vault-locked concept '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = rescore_concept(
            c.model_copy(update={"concept_name": op.new_concept_name}), bm25, clear_receipts=True
        )
    elif op.op_type == "RemoveAttribute":
        c = require_concept(concepts, op.concept_uri)
        target_attr = next((a for a in c.attributes if a.name == op.attribute_name), None)
        if target_attr is None:
            raise OpenDVError(
                f"Attribute '{op.attribute_name}' not found on '{op.concept_uri}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        if is_concept_locked(c, locked_hubs) and target_attr.is_business_key:
            raise OpenDVError(
                f"Cannot remove business-key '{op.attribute_name}' on locked concept.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(
            update={"attributes": [a for a in c.attributes if a.name != op.attribute_name]}
        )
    elif op.op_type == "RemoveExternalMapping":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={
                "external_mappings": [
                    m for m in c.external_mappings if m.standard_uri != op.standard_uri
                ]
            }
        )
    elif op.op_type == "RemoveSynonym":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = rescore_concept(
            c.model_copy(update={"synonyms": [s for s in c.synonyms if s != op.synonym]}),
            bm25,
            clear_receipts=True,
        )
    elif op.op_type == "DeleteRelationship":
        rel = rels.get(op.relationship_uri)
        if rel is None:
            raise OpenDVError(
                f"Relationship '{op.relationship_uri}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        if rel.relationship_uri in locked_links or rel.target_link in locked_links:
            raise OpenDVError(
                f"Cannot delete Vault-locked relationship '{op.relationship_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        del rels[op.relationship_uri]
    elif op.op_type == "DeleteEnumeration":
        if op.enum_uri not in enums:
            raise OpenDVError(
                f"Enumeration '{op.enum_uri}' not found in Graph A ontology.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        del enums[op.enum_uri]
    elif op.op_type == "RemoveEnumerationCode":
        enum_obj = require_enum(enums, op.enum_uri)
        codes = {k: v for k, v in enum_obj.codes.items() if k != op.code}
        enums[op.enum_uri] = enum_obj.model_copy(update={"codes": codes})
    elif op.op_type == "ChangeDomainNamespace":
        if locks:
            raise OpenDVError(
                "Cannot change domain_namespace while Vault evolution_locks are active.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        return validate_domain_namespace(op.domain_namespace)
    elif op.op_type == "ReviseDescription":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(update={"description": op.description})
    elif op.op_type == "RepinExternalMapping":
        c = require_concept(concepts, op.concept_uri)
        if op.replaced_standard_uri is not None:
            entry = bm25.lookup_concept(op.standard_uri)
            if entry is None or op.replaced_standard_uri not in entry.get("replaces_uris", []):
                raise OpenDVError(
                    f"Repin '{op.standard_uri}' does not replace '{op.replaced_standard_uri}'.",
                    error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                )
        rm_uris = {op.standard_uri} | (
            {op.replaced_standard_uri} if op.replaced_standard_uri else set()
        )
        if op.grounding_receipt_id is not None:
            rcpt = (receipts_by_id or {}).get(op.grounding_receipt_id)
            if (
                rcpt is None or rcpt.provenance != "server_verified"
                or rcpt.standard_uri != op.standard_uri or rcpt.mapping_type == "skos:relatedMatch"
            ):
                raise OpenDVError(
                    f"Invalid grounding_receipt_id '{op.grounding_receipt_id}' for repin.",
                    error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                )
            rescored_map = ExternalMapping(
                standard_uri=op.standard_uri, reference_pack_id=op.reference_pack_id,
                reference_pack_sha256=op.reference_pack_sha256, mapping_type=op.mapping_type,
                confidence=rcpt.confidence, grounding_receipt_id=op.grounding_receipt_id,
            )
        else:
            upd = ExternalMapping(
                standard_uri=op.standard_uri, reference_pack_id=op.reference_pack_id,
                reference_pack_sha256=op.reference_pack_sha256, mapping_type=op.mapping_type,
                confidence=Decimal("0.0000"), grounding_receipt_id=None,
            )
            rescored_map = rescore_mapping(c, upd, bm25, clear_receipt=True)
        rest = [m for m in c.external_mappings if m.standard_uri not in rm_uris]
        concepts[op.concept_uri] = c.model_copy(update={"external_mappings": [*rest, rescored_map]})
    elif op.op_type == "RemoveTableBinding":
        c = require_concept(concepts, op.concept_uri)
        trimmed = c.model_copy(update={"table_bindings": [
            b for b in c.table_bindings
            if (b.source_id, b.table_id, b.col_name) != (op.source_id, op.table_id, op.col_name)
        ]})
        concepts[op.concept_uri] = rescore_concept(trimmed, bm25, clear_receipts=True)
    elif op.op_type == "RemoveSourceCitation":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(update={"source_citations": [
            cit for cit in c.source_citations
            if not (cit.doc_id == op.doc_id and cit.page_number == op.page_number
                    and cit.verbatim_quote == op.verbatim_quote and cit.occurrence == op.occurrence)
        ]})
    elif op.op_type == "RemoveMcpEvidence":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(update={
            "mcp_evidence": [ev for ev in c.mcp_evidence if ev.receipt_id != op.receipt_id]
        })
    elif op.op_type == "RemoveKeyAuthorityPattern":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(update={"key_authority_patterns": [
            p for p in c.key_authority_patterns if p.authority_name != op.authority_name
        ]})
    elif op.op_type == "UpdateRelationshipMetadata":
        rel = rels.get(op.relationship_uri)
        if rel is None:
            raise OpenDVError(
                f"Relationship '{op.relationship_uri}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        patch = {
            f: getattr(op, f)
            for f in ("standard_property_uri", "role_uri", "played_by_uri")
            if getattr(op, f) is not None
        }
        rels[op.relationship_uri] = rel.model_copy(update=patch) if patch else rel
    elif op.op_type == "UpdateEnumerationMetadata":
        enum_obj = require_enum(enums, op.enum_uri)
        if op.standard_uri is not None:
            enums[op.enum_uri] = enum_obj.model_copy(update={"standard_uri": op.standard_uri})
    return namespace


def _evo_err(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION)


def verify_grounding_receipt_binding(
    mapping: ExternalMapping,
    concept: CanonicalBusinessConcept,
    *,
    actor: str,
    receipts_by_id: Mapping[str, GroundingReceipt] | None,
    table_fp_map: Mapping[str, str],
) -> None:
    """Fail closed unless a non-null grounding_receipt_id matches server receipt + scope."""
    rid = mapping.grounding_receipt_id
    if rid is None:
        return
    rcpt = (receipts_by_id or {}).get(rid)
    if (
        rcpt is None
        or rcpt.provenance != "server_verified"
        or rcpt.mapping_type == "skos:relatedMatch"
    ):
        raise _evo_err(f"Invalid grounding_receipt_id '{rid}' on '{concept.concept_uri}'.")
    pred_ok = (
        rcpt.mapping_type == mapping.mapping_type
        if actor == "agent"
        else {rcpt.mapping_type, mapping.mapping_type} <= {"skos:exactMatch", "skos:closeMatch"}
    )
    if (
        not pred_ok or rcpt.standard_uri != mapping.standard_uri
        or rcpt.reference_pack_id != mapping.reference_pack_id
        or rcpt.reference_pack_sha256 != mapping.reference_pack_sha256
        or rcpt.confidence != mapping.confidence
        or table_fp_map.get(rcpt.table_id) != rcpt.schema_fingerprint
    ):
        raise _evo_err(f"Grounding receipt '{rid}' mismatch on '{concept.concept_uri}'.")
    if not any(
        b.table_id == rcpt.table_id
        and (rcpt.target_kind == "table_anchor" or b.col_name == rcpt.source_column)
        for b in concept.table_bindings
    ):
        raise _evo_err(f"Grounding receipt '{rid}' scope mismatch on '{concept.concept_uri}'.")


def reconcile_yaml_ontology(
    base: GraphAOntology, candidate: GraphAOntology, matcher: FiboPackQueryAdapter, *,
    receipts_by_id: Mapping[str, GroundingReceipt], profiles: Sequence[SourceProfile],
) -> GraphAOntology:
    """Preserve unchanged committed mappings while verifying/rescoring new/edited mappings."""
    base_by_uri = {c.concept_uri: c for c in base.concepts}
    fp_map = {t.table_id: p.schema_fingerprint for p in profiles for t in p.tables}
    upd: list[CanonicalBusinessConcept] = []
    for c in candidate.concepts:
        bc = base_by_uri.get(c.concept_uri)
        id_ok = (
            bc is not None and c.concept_name == bc.concept_name and c.synonyms == bc.synonyms
            and all(b in c.table_bindings for b in bc.table_bindings)
        )
        bmaps = {m.standard_uri: m for m in bc.external_mappings} if bc else {}
        nmaps: list[ExternalMapping] = []
        for m in c.external_mappings:
            pm = bmaps.get(m.standard_uri)
            if pm is not None and m == pm:
                nmaps.append(m if id_ok else rescore_mapping(c, m, matcher, clear_receipt=True))
            elif m.grounding_receipt_id is not None:
                verify_grounding_receipt_binding(
                    m, c, actor="human", receipts_by_id=receipts_by_id, table_fp_map=fp_map,
                )
                nmaps.append(m)
            else:
                nmaps.append(rescore_mapping(c, m, matcher, clear_receipt=True))
        upd.append(c.model_copy(update={"external_mappings": nmaps}))
    return candidate.model_copy(update={"concepts": upd})


def apply_fibo_repin(
    state_manager: ProjectStateManager,
    project_id: str,
    active_rev_id: str,
    ontology: GraphAOntology,
    loader: FiboReferenceOntologyLoader,
    items: Sequence[FiboDriftItem],
    *,
    plan_fn: Callable[..., tuple[GroundingReceipt | None, Decimal, str | None]],
) -> int:
    """Compile and commit live 36-op repin deltas for deprecated_with_replacement FIBO items."""
    profs, fps = [], {}
    for src in state_manager.list_sources(project_id):
        rec = state_manager.get_source_record(project_id, src.source_id)
        p = SourceProfile.model_validate(rec["profile"])
        profs.append(p)
        for t in p.tables:
            fps[t.table_id] = p.schema_fingerprint
    rows = state_manager.list_events_or_receipts(project_id, record_kind="grounding_receipt")
    rcpts = {
        r.receipt_id: r
        for row in rows
        if (r := GroundingReceipt.model_validate(row["payload"]))
    }
    adp = FiboPackQueryAdapter(loader=loader, verify_integrity=loader.integrity_verified)
    c_map = {c.concept_uri: c for c in ontology.concepts}
    deltas: list[OntologyDelta] = []
    for it in items:
        if it.tier != "deprecated_with_replacement" or it.replacement_uri is None:
            continue
        repl = it.replacement_uri
        rat = f"FIBO repin {it.old_standard_uri} -> {repl}"
        if it.target_kind == "concept":
            c = c_map.get(it.target_id)
            ou = it.old_standard_uri
            pm = next((m for m in c.external_mappings if m.standard_uri == ou), None) if c else None
            if c is None or pm is None:
                continue
            lex = max(
                (
                    h for t in [c.concept_name, *c.synonyms]
                    if t.strip() and (h := adp.score(t, repl)) is not None
                ),
                default=Decimal("0.0000"),
            )
            fresh, _cf, nrid = plan_fn(
                concept=c, prior_mapping=pm, replacement_uri=repl, new_pack_id=loader.pack_id,
                new_pack_sha256=loader.pack_sha256, lexical_score=lex,
                receipts_by_id=rcpts, table_fingerprints=fps,
            )
            if fresh is not None:
                state_manager.append_event_or_receipt(
                    project_id, record_kind="grounding_receipt", run_id=None, payload=fresh,
                )
                rcpts[fresh.receipt_id] = fresh
            deltas.append(RepinExternalMapping(
                concept_uri=c.concept_uri, standard_uri=repl, reference_pack_id=loader.pack_id,
                reference_pack_sha256=loader.pack_sha256, mapping_type=pm.mapping_type,
                replaced_standard_uri=it.old_standard_uri, grounding_receipt_id=nrid, rationale=rat,
            ))
        elif it.target_kind == "attribute" and "::" in it.target_id:
            cu, an = it.target_id.rsplit("::", 1)
            deltas.append(UpdateAttributeMetadata(
                concept_uri=cu, attribute_name=an, standard_property_uri=repl, rationale=rat,
            ))
        elif it.target_kind == "relationship":
            deltas.append(UpdateRelationshipMetadata(
                relationship_uri=it.target_id, standard_property_uri=repl, rationale=rat,
            ))
        elif it.target_kind == "enumeration":
            deltas.append(UpdateEnumerationMetadata(
                enum_uri=it.target_id, standard_uri=repl, rationale=rat,
            ))
    if not deltas:
        return 0
    from opendv.service.workflows._ontology_deltas import compile_and_apply_deltas

    lock_rows = state_manager.list_revisions(project_id, rev_kind="evolution_lock")
    locks = [EvolutionLockSpec.model_validate(r["payload"]) for r in lock_rows]
    upd = compile_and_apply_deltas(
        ontology, deltas, actor="human", locks=locks,
        matcher=adp, receipts_by_id=rcpts, profiles=profs,
    )
    b = compute_bump_spec(ontology, upd)
    fver = b.next_version if b.bump_kind != "NONE" else ontology.ontology_version
    rid = f"ont-{fver}-{uuid.uuid4().hex[:8]}"
    state_manager.save_revision(
        project_id, revision_id=rid, rev_kind="ontology_version", rev_key=rid,
        parent_rev_id=active_rev_id, payload=upd.model_copy(update={"ontology_version": fver}),
        metadata=RevisionMetadata(
            actor="human", reason=f"FIBO repin ({len(deltas)})", base_revision_id=active_rev_id,
        ),
    )
    state_manager.update_project_pointers(project_id, active_ontology_rev_id=rid)
    return len(deltas)
