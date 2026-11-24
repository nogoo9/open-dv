"""Server-side 34-op OntologyDelta compiler, additive ops, and evolution lock checks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from opendv.models.errors import ErrorCode, OpenDVError
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
from opendv.service.bm25_matcher import Bm25ConceptMatcher
from opendv.service.workflows._ontology_human_ops import (
    apply_human_delta_op,
    is_concept_locked,
    locked_hub_keys,
    locked_link_keys,
    require_concept,
    require_enum,
    rescore_concept,
    rescore_mapping,
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


def _apply_additive_op(
    op: OntologyDelta,
    *,
    concepts: dict[str, CanonicalBusinessConcept],
    rels: dict[str, OntologyRelationship],
    enums: dict[str, OntologyEnumeration],
    actor: Literal["human", "agent"],
    locked_hubs: set[str],
    locked_links: set[str],
    bm25: Bm25ConceptMatcher,
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
        syns = c.synonyms if op.synonym in c.synonyms else [*c.synonyms, op.synonym]
        concepts[op.concept_uri] = rescore_concept(c.model_copy(update={"synonyms": syns}), bm25)
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
    matcher: Bm25ConceptMatcher | None = None,
) -> GraphAOntology:
    """Validate and apply 34-op OntologyDelta list server-side onto base GraphAOntology."""
    bm25 = matcher or Bm25ConceptMatcher()
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
    check_evolution_locks_diff(base, candidate, locks)
    return candidate
