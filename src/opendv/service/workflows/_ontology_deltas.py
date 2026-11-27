"""Server-side 36-op OntologyDelta compiler, additive ops, and evolution lock checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from opendv.grounding.providers.fibo.loader import FiboPackQueryAdapter
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import GroundingReceipt
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    GraphAOntology,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.ontology_deltas import (
    AGENT_ADDITIVE_OPS,
    HUMAN_ONLY_OPS,
    SERVER_ONLY_OPS,
    OntologyDelta,
)
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import SourceProfile
from opendv.service.workflows._ontology_grounding import GROUNDING_MIN_CONFIDENCE
from opendv.service.workflows._ontology_human_ops import (
    apply_human_delta_op,
    is_concept_locked,
    locked_hub_keys,
    locked_link_keys,
    require_concept,
    require_enum,
    rescore_concept,
    rescore_mapping,
    verify_grounding_receipt_binding,
)


def check_evolution_locks_diff(
    base: GraphAOntology,
    candidate: GraphAOntology,
    locks: Sequence[EvolutionLockSpec],
) -> None:
    """Verify full GraphAOntology candidate does not violate sealed Vault evolution locks."""
    if not locks:
        return
    if candidate.domain_namespace != base.domain_namespace:
        raise OpenDVError(
            "Cannot change domain_namespace while Vault evolution_locks are active.",
            error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
        )
    locked_hubs = locked_hub_keys(locks)
    locked_links = locked_link_keys(locks)
    cand_by_uri = {c.concept_uri: c for c in candidate.concepts}
    for old in base.concepts:
        if not is_concept_locked(old, locked_hubs):
            continue
        new = cand_by_uri.get(old.concept_uri)
        if new is None:
            raise OpenDVError(
                f"Cannot delete Vault-locked concept '{old.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        if new.concept_name != old.concept_name:
            raise OpenDVError(
                f"Cannot rename Vault-locked concept '{old.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        old_bks = {a.name for a in old.attributes if a.is_business_key}
        new_bks = {a.name for a in new.attributes if a.is_business_key}
        if not old_bks.issubset(new_bks):
            raise OpenDVError(
                f"Cannot remove locked business-key attributes on '{old.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
    cand_rels = {r.relationship_uri: r for r in candidate.relationships}
    for rel in base.relationships:
        is_locked = rel.relationship_uri in locked_links or rel.target_link in locked_links
        if not is_locked:
            continue
        new_rel = cand_rels.get(rel.relationship_uri)
        if new_rel is None:
            raise OpenDVError(
                f"Cannot delete Vault-locked relationship '{rel.relationship_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        if (
            new_rel.target_link != rel.target_link
            or new_rel.source_concept_uri != rel.source_concept_uri
            or new_rel.target_concept_uri != rel.target_concept_uri
        ):
            raise OpenDVError(
                f"Cannot mutate Vault-locked relationship '{rel.relationship_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
    base_rels = {r.relationship_uri for r in base.relationships}
    for new_rel in candidate.relationships:
        if new_rel.relationship_uri not in base_rels and (
            new_rel.relationship_uri in locked_links or new_rel.target_link in locked_links
        ):
            raise OpenDVError(
                f"Cannot add relationship '{new_rel.relationship_uri}' onto locked "
                f"target_link '{new_rel.target_link}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )


def _apply_additive_op(
    op: OntologyDelta,
    *,
    concepts: dict[str, CanonicalBusinessConcept],
    rels: dict[str, OntologyRelationship],
    enums: dict[str, OntologyEnumeration],
    actor: Literal["human", "agent"],
    locked_hubs: set[str],
    locked_links: set[str],
    bm25: FiboPackQueryAdapter,
) -> None:
    if op.op_type == "AddConcept":
        if op.concept_uri in concepts:
            raise OpenDVError(
                f"Concept '{op.concept_uri}' already exists.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        new_c = CanonicalBusinessConcept(
            concept_uri=op.concept_uri,
            concept_name=op.concept_name,
            description=op.description,
            parent_concept_uri=op.parent_concept_uri,
            key_authority_tier=op.key_authority_tier,
            key_authority_patterns=list(op.key_authority_patterns),
            bkcc_default=op.bkcc_default,
            governance_tags=list(op.governance_tags),
            governance_constraints=dict(op.governance_constraints),
            attributes=list(op.attributes),
            synonyms=list(op.synonyms),
            external_mappings=list(op.external_mappings),
            table_bindings=list(op.table_bindings),
            source_citations=list(op.source_citations),
            mcp_evidence=list(op.mcp_evidence),
        )
        concepts[op.concept_uri] = rescore_concept(new_c, bm25)
    elif op.op_type == "AddAttribute":
        c = require_concept(concepts, op.concept_uri)
        if any(a.name == op.attribute.name for a in c.attributes):
            raise OpenDVError(
                f"Attribute '{op.attribute.name}' already exists on '{op.concept_uri}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        concepts[op.concept_uri] = c.model_copy(
            update={"attributes": [*c.attributes, op.attribute]}
        )
    elif op.op_type == "AddRelationship":
        existing_rel = rels.get(op.relationship.relationship_uri)
        if existing_rel is not None:
            if (
                existing_rel.relationship_uri in locked_links
                or existing_rel.target_link in locked_links
            ):
                raise OpenDVError(
                    f"Cannot overwrite Vault-locked relationship "
                    f"'{op.relationship.relationship_uri}'.",
                    error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                )
            if actor == "agent":
                raise OpenDVError(
                    f"Relationship '{op.relationship.relationship_uri}' already exists.",
                    error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
                )
        elif (
            op.relationship.relationship_uri in locked_links
            or op.relationship.target_link in locked_links
        ):
            raise OpenDVError(
                f"Cannot add relationship '{op.relationship.relationship_uri}' onto locked "
                f"target_link '{op.relationship.target_link}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        rels[op.relationship.relationship_uri] = op.relationship
    elif op.op_type == "AddEnumeration":
        enums[op.enumeration.enum_uri] = op.enumeration
    elif op.op_type == "AddEnumerationCode":
        enum_obj = require_enum(enums, op.enum_uri)
        codes = dict(enum_obj.codes)
        codes[op.code] = op.description
        enums[op.enum_uri] = enum_obj.model_copy(update={"codes": codes})
    elif op.op_type == "AddSynonym":
        c = require_concept(concepts, op.concept_uri)
        if op.synonym not in c.synonyms:
            concepts[op.concept_uri] = rescore_concept(
                c.model_copy(update={"synonyms": [*c.synonyms, op.synonym]}),
                bm25,
                clear_receipts=True,
            )
    elif op.op_type == "AddExternalMapping":
        c = require_concept(concepts, op.concept_uri)
        rescored_map = rescore_mapping(c, op.mapping, bm25)
        rest = [m for m in c.external_mappings if m.standard_uri != rescored_map.standard_uri]
        concepts[op.concept_uri] = c.model_copy(update={"external_mappings": [*rest, rescored_map]})
    elif op.op_type == "SetParentConcept":
        c = require_concept(concepts, op.concept_uri)
        if c.parent_concept_uri is not None:
            raise OpenDVError(
                f"SetParentConcept requires parent_concept_uri unset on '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(
            update={"parent_concept_uri": op.parent_concept_uri}
        )
    elif op.op_type == "SetDescription":
        c = require_concept(concepts, op.concept_uri)
        if actor == "agent" and c.description.strip():
            raise OpenDVError(
                f"SetDescription requires blank description for agent on '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(update={"description": op.description})
    elif op.op_type == "SetConceptKeyAuthority":
        c = require_concept(concepts, op.concept_uri)
        if is_concept_locked(c, locked_hubs):
            raise OpenDVError(
                f"Cannot change key authority on locked concept '{op.concept_uri}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        concepts[op.concept_uri] = c.model_copy(
            update={"key_authority_tier": op.key_authority_tier, "bkcc_default": op.bkcc_default}
        )
    elif op.op_type == "SetConceptGovernanceTags":
        c = require_concept(concepts, op.concept_uri)
        if actor == "agent":
            merged_tags = list(dict.fromkeys([*c.governance_tags, *op.governance_tags]))
            merged_constraints = dict(c.governance_constraints)
            for k, v in op.governance_constraints.items():
                prev_v = merged_constraints.get(k)
                if isinstance(prev_v, list) and isinstance(v, list):
                    merged_constraints[k] = list(dict.fromkeys([*prev_v, *v]))
                elif isinstance(prev_v, list) and isinstance(v, str):
                    merged_constraints[k] = list(dict.fromkeys([*prev_v, v]))
                elif isinstance(prev_v, str) and isinstance(v, list):
                    merged_constraints[k] = list(dict.fromkeys([prev_v, *v]))
                else:
                    merged_constraints[k] = list(v) if isinstance(v, list) else v
            concepts[op.concept_uri] = c.model_copy(
                update={
                    "governance_tags": merged_tags,
                    "governance_constraints": merged_constraints,
                }
            )
        else:
            concepts[op.concept_uri] = c.model_copy(
                update={
                    "governance_tags": list(op.governance_tags),
                    "governance_constraints": dict(op.governance_constraints),
                }
            )
    elif op.op_type == "UpdateAttributeMetadata":
        c = require_concept(concepts, op.concept_uri)
        updated_attrs = []
        found = False
        for attr in c.attributes:
            if attr.name == op.attribute_name:
                found = True
                patch: dict[str, Any] = {}
                for fld in (
                    "logical_type",
                    "sql_type_hint",
                    "nullable",
                    "is_business_key",
                    "is_pii",
                    "pii_category",
                    "enum_uri",
                    "standard_property_uri",
                ):
                    val = getattr(op, fld)
                    if val is not None:
                        patch[fld] = val
                updated_attrs.append(attr.model_copy(update=patch))
            else:
                updated_attrs.append(attr)
        if not found:
            raise OpenDVError(
                f"Attribute '{op.attribute_name}' not found on '{op.concept_uri}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        concepts[op.concept_uri] = c.model_copy(update={"attributes": updated_attrs})
    elif op.op_type == "AddTableBinding":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={"table_bindings": [*c.table_bindings, op.binding]}
        )
    elif op.op_type == "AddSourceCitation":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={"source_citations": [*c.source_citations, op.citation]}
        )
    elif op.op_type == "AddMcpEvidence":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={"mcp_evidence": [*c.mcp_evidence, op.evidence]}
        )
    elif op.op_type == "AddKeyAuthorityPattern":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={"key_authority_patterns": [*c.key_authority_patterns, op.pattern]}
        )


def compile_and_apply_deltas(
    base: GraphAOntology,
    operations: Sequence[OntologyDelta],
    *,
    actor: Literal["human", "agent"] = "human",
    locks: Sequence[EvolutionLockSpec] = (),
    matcher: FiboPackQueryAdapter | None = None,
    receipts_by_id: Mapping[str, GroundingReceipt] | None = None,
    profiles: Sequence[SourceProfile] = (),
) -> GraphAOntology:
    """Validate and apply 36-op OntologyDelta list server-side onto base GraphAOntology."""
    bm25 = matcher or FiboPackQueryAdapter()
    locked_hubs = locked_hub_keys(locks)
    locked_links = locked_link_keys(locks)
    concepts: dict[str, CanonicalBusinessConcept] = {c.concept_uri: c for c in base.concepts}
    rels: dict[str, OntologyRelationship] = {r.relationship_uri: r for r in base.relationships}
    enums: dict[str, OntologyEnumeration] = {e.enum_uri: e for e in base.enumerations}
    namespace = base.domain_namespace

    for op in operations:
        op_type = op.op_type
        if op_type in SERVER_ONLY_OPS:
            raise OpenDVError(
                f"Client-submitted '{op_type}' is server-only and forbidden.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if actor == "agent" and op_type in HUMAN_ONLY_OPS:
            raise OpenDVError(
                f"Agent actor cannot execute human-only delta '{op_type}'.",
                error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
            )
        if op_type in AGENT_ADDITIVE_OPS:
            _apply_additive_op(
                op,
                concepts=concepts,
                rels=rels,
                enums=enums,
                actor=actor,
                locked_hubs=locked_hubs,
                locked_links=locked_links,
                bm25=bm25,
            )
        elif op_type in HUMAN_ONLY_OPS:
            namespace = apply_human_delta_op(
                op,
                concepts=concepts,
                rels=rels,
                enums=enums,
                namespace=namespace,
                locks=locks,
                locked_hubs=locked_hubs,
                locked_links=locked_links,
                bm25=bm25,
                receipts_by_id=receipts_by_id,
            )
        else:
            raise OpenDVError(
                f"Unknown ontology delta op_type '{op_type}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

    candidate = GraphAOntology(
        ontology_version=base.ontology_version,
        domain_namespace=namespace,
        concepts=list(concepts.values()),
        relationships=list(rels.values()),
        enumerations=list(enums.values()),
    )
    table_fp_map = {t.table_id: p.schema_fingerprint for p in profiles for t in p.tables}
    base_by_uri = {c.concept_uri: c for c in base.concepts}
    for c in candidate.concepts:
        base_c = base_by_uri.get(c.concept_uri)
        base_maps = {m.standard_uri: m for m in base_c.external_mappings} if base_c else {}
        for m in c.external_mappings:
            prev_m = base_maps.get(m.standard_uri)
            if (prev_m is None or m != prev_m) and m.grounding_receipt_id is not None:
                verify_grounding_receipt_binding(
                    m,
                    c,
                    actor=actor,
                    receipts_by_id=receipts_by_id,
                    table_fp_map=table_fp_map,
                )
    check_evolution_locks_diff(base, candidate, locks)
    return candidate


def index_existing_ontology_for_projection(
    g: GraphAOntology, locks: Sequence[EvolutionLockSpec], slug_fn: Any,
) -> tuple[
    dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, set[str]],
    set[tuple[str, str, str, str]], dict[str, set[str]], dict[str, str], set[str],
    dict[str, list[str]], dict[str, dict[str, str | list[str]]], dict[str, set[str]], set[str],
]:
    """Pre-index existing GraphAOntology and evolution locks for 4-step projection."""
    cn, pby, s2u, n2u, ats, tgs, cns = {}, {}, {}, {}, {}, {}, {}
    bds: set[tuple[str, str, str, str]] = set()
    for c in g.concepts:
        u = c.concept_uri
        cn[u], n2u[slug_fn(c.concept_name)] = c.concept_name, u
        ats[u] = {a.name for a in c.attributes}
        tgs[u], cns[u] = list(c.governance_tags), dict(c.governance_constraints)
        if isinstance(pb := c.governance_constraints.get("played_by_uri"), str):
            pby[pb] = u
        bds.update((u, b.source_id, b.table_id, b.col_name) for b in c.table_bindings)
        for m in c.external_mappings:
            if (
                m.mapping_type in {"skos:exactMatch", "skos:closeMatch"}
                and m.confidence >= GROUNDING_MIN_CONFIDENCE
            ):
                s2u[m.standard_uri] = u
    ens = {e.enum_uri: set(e.codes) for e in g.enumerations}
    s2e = {e.standard_uri: e.enum_uri for e in g.enumerations if e.standard_uri}
    rus = {r.relationship_uri for r in g.relationships}
    lhb: dict[str, set[str]] = {}
    llk: set[str] = set()
    for lk in locks:
        llk.update(lk.locked_links)
        for k, bks in lk.locked_hub_bks.items():
            for c in g.concepts:
                cn_slug = slug_fn(c.concept_name)
                if k in (c.concept_uri, c.concept_name, cn_slug, f"hub_{cn_slug}"):
                    lhb.setdefault(c.concept_uri, set(bks)).update(
                        a.name for a in c.attributes if a.is_business_key
                    )
    return cn, pby, s2u, n2u, ats, bds, ens, s2e, rus, tgs, cns, lhb, llk
