"""Ontology services for Open-DV (Decision D9).

Provides deterministic coverage analysis, delta validation, application, and diffing.
"""

from __future__ import annotations

from opendv_service.ontology.coverage import compute_ontology_coverage

__all__ = ["compute_ontology_coverage"]
