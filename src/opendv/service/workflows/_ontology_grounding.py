"""SemVer bump calculation and Path A/B grounding checks for GraphAOntology."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.wire_dtos_discovery import OntologyCommitReq, ValidationBumpSpec

GROUNDING_MIN_CONFIDENCE: Decimal = Decimal("0.6500")


def _bump_semver(current: str, bump_kind: Literal["NONE", "PATCH", "MINOR", "MAJOR"]) -> str:
    clean = current.lstrip("v")
    parts = clean.split(".")
    nums = [int(p) if p.isdigit() else 0 for p in [*parts, "0", "0", "0"][:3]]
    major, minor, patch = nums
    if bump_kind == "NONE":
        return f"{major}.{minor}.{patch}"
    if bump_kind == "MAJOR":
        return f"{major + 1}.0.0"
    if bump_kind == "MINOR":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def compute_bump_spec(
    base: GraphAOntology,
    candidate: GraphAOntology,
) -> ValidationBumpSpec:
    """Compute SemVer bump kind and breaking reasons between two GraphAOntology states."""
    breaking: list[str] = []
    if base.domain_namespace != candidate.domain_namespace:
        breaking.append(
            f"domain_namespace changed: {base.domain_namespace} -> {candidate.domain_namespace}"
        )
    base_c = {c.concept_uri: c for c in base.concepts}
    cand_c = {c.concept_uri: c for c in candidate.concepts}
    for uri, old in base_c.items():
        new = cand_c.get(uri)
        if new is None:
            breaking.append(f"Removed concept {uri}")
            continue
        if new.concept_name != old.concept_name:
            breaking.append(f"Renamed concept {uri}: {old.concept_name} -> {new.concept_name}")
        old_bks = {a.name for a in old.attributes if a.is_business_key}
        new_bks = {a.name for a in new.attributes if a.is_business_key}
        if not old_bks.issubset(new_bks):
            removed = sorted(old_bks - new_bks)
            breaking.append(f"Removed business-key attribute(s) on {uri}: {removed}")
        if new.key_authority_tier != old.key_authority_tier:
            breaking.append(f"Changed key_authority_tier on {uri}")

    base_r = {r.relationship_uri for r in base.relationships}
    cand_r = {r.relationship_uri for r in candidate.relationships}
    for rel_uri in sorted(base_r - cand_r):
        breaking.append(f"Removed relationship {rel_uri}")

    base_e = {e.enum_uri for e in base.enumerations}
    cand_e = {e.enum_uri for e in candidate.enumerations}
    for enum_uri in sorted(base_e - cand_e):
        breaking.append(f"Removed enumeration {enum_uri}")

    bump_kind: Literal["NONE", "PATCH", "MINOR", "MAJOR"]
    if breaking:
        bump_kind = "MAJOR"
    elif (
        set(cand_c) != set(base_c)
        or set(cand_r) != set(base_r)
        or set(cand_e) != set(base_e)
    ):
        bump_kind = "MINOR"
    elif candidate.model_copy(update={"ontology_version": base.ontology_version}) != base:
        bump_kind = "PATCH"
    else:
        bump_kind = "NONE"

    next_ver = _bump_semver(base.ontology_version, bump_kind)
    return ValidationBumpSpec(
        current_version=base.ontology_version,
        next_version=next_ver,
        bump_kind=bump_kind,
        breaking_reasons=breaking,
    )


def _has_path_a(concept: CanonicalBusinessConcept) -> bool:
    return any(
        m.mapping_type in {"skos:exactMatch", "skos:closeMatch"}
        and m.confidence >= GROUNDING_MIN_CONFIDENCE
        for m in concept.external_mappings
    )


def _has_path_b1(
    concept: CanonicalBusinessConcept,
    explicit_uris: set[str],
) -> bool:
    return bool(
        concept.table_bindings
        or concept.source_citations
        or concept.mcp_evidence
        or concept.concept_uri in explicit_uris
    )


def collect_explicit_grounding_uris(req: OntologyCommitReq) -> set[str]:
    """Collect concept URIs carrying explicit Steward evidence grounding rationale."""
    uris: set[str] = set()
    items = list(req.evidence_grounding or [])
    if req.grounding_waiver and req.grounding_waiver.evidence_grounding:
        items.extend(req.grounding_waiver.evidence_grounding)
    for item in items:
        if item.source_refs and item.rationale.strip():
            uris.add(item.concept_uri)
    return uris


def collect_ungrounded_changed_concepts(
    base: GraphAOntology,
    candidate: GraphAOntology,
    explicit_uris: set[str],
) -> list[CanonicalBusinessConcept]:
    """Return modified or added concepts lacking Path A or Path B1 grounding."""
    base_by_uri = {c.concept_uri: c for c in base.concepts}
    ungrounded: list[CanonicalBusinessConcept] = []
    for concept in candidate.concepts:
        prior = base_by_uri.get(concept.concept_uri)
        if prior is not None and prior == concept:
            continue
        if _has_path_a(concept) or _has_path_b1(concept, explicit_uris):
            continue
        ungrounded.append(concept)
    return ungrounded
