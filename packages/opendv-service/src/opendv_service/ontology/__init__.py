"""Ontology services for Open-DV (Decision D9).

Provides deterministic coverage analysis, delta validation, application, diffing, and hashing.
"""

from __future__ import annotations

from opendv_service.ontology.applier import apply_ontology_delta
from opendv_service.ontology.coverage import compute_ontology_coverage
from opendv_service.ontology.diff import ontology_diff
from opendv_service.ontology.hashing import canonicalize_graph_a_dict, compute_ontology_sha256
from opendv_service.ontology.resolution import (
    OntologyResolutionResult,
    resolve_ontology_hold,
)
from opendv_service.ontology.validator import validate_ontology_delta

__all__ = [
    "OntologyResolutionResult",
    "apply_ontology_delta",
    "canonicalize_graph_a_dict",
    "compute_ontology_coverage",
    "compute_ontology_sha256",
    "ontology_diff",
    "resolve_ontology_hold",
    "validate_ontology_delta",
]


