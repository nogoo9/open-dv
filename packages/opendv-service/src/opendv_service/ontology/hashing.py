"""Cryptographic hashing and canonical serialization for Graph A ontologies."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.models.graph_a import GraphAOntology


def canonicalize_graph_a_dict(ontology: GraphAOntology | dict[str, Any]) -> dict[str, Any]:
    """Convert a GraphAOntology or dict to a deterministically sorted dictionary representation."""
    if isinstance(ontology, GraphAOntology):
        doc = ontology.model_dump(mode="json", exclude_none=True)
    elif isinstance(ontology, dict):
        try:
            doc = GraphAOntology.model_validate(ontology).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError):
            doc = dict(ontology)
    else:
        doc = dict(ontology)

    # Deterministically sort list properties by canonical URNs
    if "canonical_business_concepts" in doc and isinstance(
        doc["canonical_business_concepts"], list
    ):
        doc["canonical_business_concepts"] = sorted(
            doc["canonical_business_concepts"],
            key=lambda c: c.get("concept_uri", ""),
        )
    if "canonical_relationships" in doc and isinstance(doc["canonical_relationships"], list):
        doc["canonical_relationships"] = sorted(
            doc["canonical_relationships"],
            key=lambda r: r.get("relationship_uri", ""),
        )
    if "reference_enumerations" in doc and isinstance(doc["reference_enumerations"], list):
        doc["reference_enumerations"] = sorted(
            doc["reference_enumerations"],
            key=lambda e: e.get("enum_uri", ""),
        )
    if "standards_references" in doc and isinstance(doc["standards_references"], list):
        doc["standards_references"] = sorted(doc["standards_references"])
    if "decision_refs" in doc and isinstance(doc["decision_refs"], list):
        doc["decision_refs"] = sorted(doc["decision_refs"])

    return doc


def compute_ontology_sha256(ontology: GraphAOntology) -> str:
    """Compute RFC 8785 JCS canonical SHA-256 hex digest of a GraphAOntology."""
    canonical_dict = canonicalize_graph_a_dict(ontology)
    jcs_bytes = dumps_jcs_bytes(canonical_dict)
    return hashlib.sha256(jcs_bytes).hexdigest()
