"""Structural difference computation between Graph A ontology instances."""

from __future__ import annotations

from opendv_contracts.dtos.ontology import OntologyDiff
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256


def ontology_diff(base: GraphAOntology, target: GraphAOntology) -> OntologyDiff:
    """Compute structural delta, additions, refinements, and breaking changes."""
    base_sha256 = compute_ontology_sha256(base)
    target_sha256 = compute_ontology_sha256(target)

    # 1. Compare Concepts
    base_concepts = {c.concept_uri: c for c in base.canonical_business_concepts}
    target_concepts = {c.concept_uri: c for c in target.canonical_business_concepts}

    added_concepts = [uri for uri in target_concepts if uri not in base_concepts]
    dropped_concepts = [uri for uri in base_concepts if uri not in target_concepts]

    refined_concepts: list[str] = []
    breaking_changes: list[str] = []

    for uri in dropped_concepts:
        breaking_changes.append(f"BREAKING: Concept '{uri}' was removed in target ontology.")

    for uri, b_concept in base_concepts.items():
        if uri in target_concepts:
            t_concept = target_concepts[uri]
            # Check target hub alteration
            if b_concept.target_hub != t_concept.target_hub:
                breaking_changes.append(
                    f"BREAKING: Concept '{uri}' target_hub rebound from '{b_concept.target_hub}' "
                    f"to '{t_concept.target_hub}'."
                )
                refined_concepts.append(uri)
            else:
                # Check key authority tier breaking changes
                b_kat = b_concept.key_authority_tiers
                t_kat = t_concept.key_authority_tiers
                if (
                    b_kat
                    and t_kat
                    and b_kat.tier_1_global_standard
                    and t_kat.tier_1_global_standard
                    and (
                        b_kat.tier_1_global_standard.authority_name
                        != t_kat.tier_1_global_standard.authority_name
                    )
                ):
                    breaking_changes.append(
                        f"BREAKING: Concept '{uri}' Tier 1 authority changed from "
                        f"'{b_kat.tier_1_global_standard.authority_name}' to "
                        f"'{t_kat.tier_1_global_standard.authority_name}'."
                    )
                if (
                    b_concept.definition != t_concept.definition
                    or b_concept.elm_archetype != t_concept.elm_archetype
                    or b_concept.governance_constraints != t_concept.governance_constraints
                    or b_concept.key_authority_tiers != t_concept.key_authority_tiers
                ):
                    refined_concepts.append(uri)

    # 2. Compare Relationships
    base_rels = {r.relationship_uri: r for r in base.canonical_relationships}
    target_rels = {r.relationship_uri: r for r in target.canonical_relationships}

    added_relationships = [uri for uri in target_rels if uri not in base_rels]
    dropped_relationships = [uri for uri in base_rels if uri not in target_rels]

    for uri in dropped_relationships:
        breaking_changes.append(f"BREAKING: Relationship '{uri}' was removed in target ontology.")

    for uri, b_rel in base_rels.items():
        if uri in target_rels:
            t_rel = target_rels[uri]
            if b_rel.target_link != t_rel.target_link:
                breaking_changes.append(
                    f"BREAKING: Relationship '{uri}' target_link rebound from "
                    f"'{b_rel.target_link}' to '{t_rel.target_link}'."
                )
            if set(b_rel.endpoints) != set(t_rel.endpoints):
                breaking_changes.append(
                    f"BREAKING: Relationship '{uri}' endpoints changed from "
                    f"{b_rel.endpoints} to {t_rel.endpoints}."
                )

    # 3. Compare Reference Enumerations
    base_enums = {e.enum_uri: e for e in base.reference_enumerations}
    target_enums = {e.enum_uri: e for e in target.reference_enumerations}

    added_enumerations = [uri for uri in target_enums if uri not in base_enums]
    dropped_enumerations = [uri for uri in base_enums if uri not in target_enums]

    for uri in dropped_enumerations:
        breaking_changes.append(
            f"BREAKING: Reference enumeration '{uri}' was removed in target ontology."
        )

    for uri, b_enum in base_enums.items():
        if uri in target_enums:
            t_enum = target_enums[uri]
            if b_enum.target_reference_table != t_enum.target_reference_table:
                breaking_changes.append(
                    f"BREAKING: Reference enumeration '{uri}' table changed from "
                    f"'{b_enum.target_reference_table}' to '{t_enum.target_reference_table}'."
                )
            b_codes = set(b_enum.allowed_codes)
            t_codes = set(t_enum.allowed_codes)
            if not b_codes.issubset(t_codes):
                dropped_codes = b_codes - t_codes
                breaking_changes.append(
                    f"BREAKING: Reference enumeration '{uri}' dropped allowed codes: "
                    f"{sorted(dropped_codes)}."
                )

    diagnostics = [
        f"Ontology diff computed from v{base.ontology_version} ({base_sha256[:8]}) to "
        f"v{target.ontology_version} ({target_sha256[:8]}): +{len(added_concepts)} concepts, "
        f"+{len(added_relationships)} relationships, +{len(added_enumerations)} enums, "
        f"{len(refined_concepts)} refined concepts, {len(breaking_changes)} breaking changes."
    ]

    return OntologyDiff(
        base_version=base.ontology_version,
        target_version=target.ontology_version,
        base_sha256=base_sha256,
        target_sha256=target_sha256,
        added_concepts=sorted(added_concepts),
        added_relationships=sorted(added_relationships),
        added_enumerations=sorted(added_enumerations),
        refined_concepts=sorted(refined_concepts),
        breaking_changes=breaking_changes,
        diagnostics=diagnostics,
    )
