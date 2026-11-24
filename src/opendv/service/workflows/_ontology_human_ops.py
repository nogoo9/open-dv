"""Human-only destructive/steward OntologyDelta handlers and lock checks."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ExternalMapping,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.ontology_deltas import OntologyDelta
from opendv.models.project import EvolutionLockSpec, validate_domain_namespace
from opendv.service.bm25_matcher import Bm25ConceptMatcher
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256


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
    matcher: Bm25ConceptMatcher,
) -> ExternalMapping:
    if mapping.mapping_type not in {"skos:exactMatch", "skos:closeMatch"}:
        return mapping
    terms = [concept.concept_name, *concept.synonyms]
    best: Decimal | None = None
    for term in terms:
        if not term.strip():
            continue
        hit = matcher.score(term, mapping.standard_uri)
        if hit is not None and (best is None or hit.quantized_decimal > best):
            best = hit.quantized_decimal
    if best is None:
        return mapping.model_copy(update={"confidence": Decimal("0.0000")})
    return mapping.model_copy(
        update={
            "confidence": best,
            "reference_pack_sha256": mapping.reference_pack_sha256 or CANONICAL_FIBO_CORE_SHA256,
        }
    )


def rescore_concept(
    concept: CanonicalBusinessConcept,
    matcher: Bm25ConceptMatcher,
) -> CanonicalBusinessConcept:
    if not concept.external_mappings:
        return concept
    return concept.model_copy(
        update={
            "external_mappings": [
                rescore_mapping(concept, m, matcher) for m in concept.external_mappings
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
    bm25: Bm25ConceptMatcher,
) -> str:
    """Execute one of the 17 HUMAN_ONLY_OPS onto mutable dicts and return updated namespace."""
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
            c.model_copy(update={"concept_name": op.new_concept_name}), bm25
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
            c.model_copy(update={"synonyms": [s for s in c.synonyms if s != op.synonym]}), bm25
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
        updated = ExternalMapping(
            standard_uri=op.standard_uri,
            reference_pack_id=op.reference_pack_id,
            reference_pack_sha256=op.reference_pack_sha256,
            mapping_type=op.mapping_type,
            confidence=Decimal("0.0000"),
        )
        rescored_map = rescore_mapping(c, updated, bm25)
        rest = [m for m in c.external_mappings if m.standard_uri != op.standard_uri]
        concepts[op.concept_uri] = c.model_copy(update={"external_mappings": [*rest, rescored_map]})
    elif op.op_type == "RemoveTableBinding":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={
                "table_bindings": [
                    b
                    for b in c.table_bindings
                    if not (
                        b.source_id == op.source_id
                        and b.table_id == op.table_id
                        and b.col_name == op.col_name
                    )
                ]
            }
        )
    elif op.op_type == "RemoveSourceCitation":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={
                "source_citations": [
                    cit
                    for cit in c.source_citations
                    if not (
                        cit.doc_id == op.doc_id
                        and cit.page_number == op.page_number
                        and cit.verbatim_quote == op.verbatim_quote
                        and cit.occurrence == op.occurrence
                    )
                ]
            }
        )
    elif op.op_type == "RemoveMcpEvidence":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={
                "mcp_evidence": [ev for ev in c.mcp_evidence if ev.receipt_id != op.receipt_id]
            }
        )
    elif op.op_type == "RemoveKeyAuthorityPattern":
        c = require_concept(concepts, op.concept_uri)
        concepts[op.concept_uri] = c.model_copy(
            update={
                "key_authority_patterns": [
                    p for p in c.key_authority_patterns if p.authority_name != op.authority_name
                ]
            }
        )
    return namespace
