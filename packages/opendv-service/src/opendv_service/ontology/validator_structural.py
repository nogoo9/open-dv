"""Phase 1 pure in-memory StructuralValidator (acyclicity, provisional leak, Hub bijection)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_contracts.errors import ErrorCode
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.validator_models import (
    ContractViolationError,
    HierarchicalCycleError,
    OntologyGovernanceError,
    ProvisionalTokenError,
    StructuralValidationResult,
)


class StructuralValidator:
    """Phase 1: Pure in-memory primitive structural validator (zero filesystem/state access)."""

    PROVISIONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"(?i)urn:opendv:provisional:"),
        re.compile(r"(?i)urn:ontology:provisional:"),
    )

    _LINK_PREFIXES: tuple[str, ...] = (
        "t_lnk_",
        "nh_lnk_",
        "x_lnk_",
        "lnk_",
        "sal_",
        "hal_",
    )

    def scan_provisional_tokens(self, val: Any) -> list[str]:
        """Recursively scan nested structures for forbidden provisional URI patterns."""
        found: list[str] = []
        if isinstance(val, str):
            for pat in self.PROVISIONAL_PATTERNS:
                if pat.search(val):
                    found.append(val)
                    break
        elif isinstance(val, Mapping):
            for k, v in val.items():
                found.extend(self.scan_provisional_tokens(k))
                found.extend(self.scan_provisional_tokens(v))
        elif isinstance(val, (list, tuple, set, frozenset)):
            for item in val:
                found.extend(self.scan_provisional_tokens(item))
        elif hasattr(val, "__dict__"):
            found.extend(self.scan_provisional_tokens(vars(val)))
        return found

    def validate_acyclicity(self, ontology: GraphAOntology) -> None:
        """Validate taxonomy DAG acyclicity via Tarjan's Strongly Connected Components (SCC)."""
        adj: dict[str, list[str]] = {}
        for c in ontology.canonical_business_concepts:
            if c.concept_uri not in adj:
                adj[c.concept_uri] = []
            if c.parent_concept_uri:
                if c.parent_concept_uri == c.concept_uri:
                    raise HierarchicalCycleError(
                        f"HIERARCHICAL_CYCLE_DETECTED: Concept '{c.concept_uri}' "
                        "cannot be its own parent."
                    )
                adj[c.concept_uri].append(c.parent_concept_uri)

        index = 0
        indices: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        sccs: list[list[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlink[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in adj.get(node, []):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlink[node] = min(lowlink[node], lowlink[neighbor])
                elif neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], indices[neighbor])

            if lowlink[node] == indices[node]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    scc.append(w)
                    if w == node:
                        break
                sccs.append(scc)

        for node in list(adj.keys()):
            if node not in indices:
                strongconnect(node)

        for scc in sccs:
            if len(scc) > 1:
                cycle_str = " -> ".join([*scc, scc[0]])
                raise HierarchicalCycleError(
                    f"HIERARCHICAL_CYCLE_DETECTED: Directed cycle detected in taxonomy DAG: "
                    f"{cycle_str}"
                )
            if len(scc) == 1:
                node = scc[0]
                if node in adj.get(node, []):
                    raise HierarchicalCycleError(
                        f"HIERARCHICAL_CYCLE_DETECTED: Concept '{node}' cannot be its own parent."
                    )

    def validate_hub_bijection(self, ontology: GraphAOntology) -> None:
        """Validate 1:1 Hub bijection invariant and physical table uniqueness/slug budgets."""
        hub_to_concept: dict[str, str] = {}
        seen_physical_tables: dict[str, str] = {}

        for c in ontology.canonical_business_concepts:
            if c.target_hub:
                hub_slug = c.target_hub.removeprefix("hub_")
                if not hub_slug or len(hub_slug) > 55 or len(c.target_hub) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Hub '{c.target_hub}' on concept "
                        f"'{c.concept_uri}' exceeds the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if c.target_hub in hub_to_concept:
                    prev = hub_to_concept[c.target_hub]
                    if prev != c.concept_uri:
                        raise ContractViolationError(
                            f"ONT-EVO-003: Anti-silo violation: Hub '{c.target_hub}' is already "
                            f"bound to concept '{prev}'; cannot bind distinct concept "
                            f"'{c.concept_uri}'.",
                            error_code=ErrorCode.HUB_BIJECTION_VIOLATION,
                        )
                hub_to_concept[c.target_hub] = c.concept_uri
                seen_physical_tables[c.target_hub] = c.concept_uri

        link_to_rel: dict[str, str] = {}
        for r in ontology.canonical_relationships:
            if r.target_link:
                link_slug = r.target_link
                for pfx in self._LINK_PREFIXES:
                    if link_slug.startswith(pfx):
                        link_slug = link_slug[len(pfx) :]
                        break
                if not link_slug or len(link_slug) > 55 or len(r.target_link) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Link '{r.target_link}' on relationship "
                        f"'{r.relationship_uri}' exceeds the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if r.target_link in link_to_rel and link_to_rel[r.target_link] != r.relationship_uri:
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Link table '{r.target_link}' is already "
                        f"bound to relationship '{link_to_rel[r.target_link]}'; cannot bind "
                        f"'{r.relationship_uri}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                if r.target_link in seen_physical_tables:
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Physical table '{r.target_link}' "
                        f"collides with '{seen_physical_tables[r.target_link]}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                link_to_rel[r.target_link] = r.relationship_uri
                seen_physical_tables[r.target_link] = r.relationship_uri

        ref_to_enum: dict[str, str] = {}
        for e in ontology.reference_enumerations:
            if e.target_reference_table:
                ref_slug = e.target_reference_table.removeprefix("ref_")
                if not ref_slug or len(ref_slug) > 55 or len(e.target_reference_table) > 63:
                    raise OntologyGovernanceError(
                        f"INVALID_PHYSICAL_TABLE_NAME: Reference table "
                        f"'{e.target_reference_table}' on enumeration '{e.enum_uri}' exceeds "
                        "the 55-character base slug budget.",
                        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
                    )
                if (
                    e.target_reference_table in ref_to_enum
                    and ref_to_enum[e.target_reference_table] != e.enum_uri
                ):
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Reference table "
                        f"'{e.target_reference_table}' is already bound to enumeration "
                        f"'{ref_to_enum[e.target_reference_table]}'; cannot bind '{e.enum_uri}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                if e.target_reference_table in seen_physical_tables:
                    raise ContractViolationError(
                        f"PHYSICAL_TABLE_NAME_COLLISION: Physical table "
                        f"'{e.target_reference_table}' collides with "
                        f"'{seen_physical_tables[e.target_reference_table]}'.",
                        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
                    )
                ref_to_enum[e.target_reference_table] = e.enum_uri
                seen_physical_tables[e.target_reference_table] = e.enum_uri

    def validate(
        self,
        ontology: GraphAOntology,
        delta: OntologyDelta | None = None,
    ) -> StructuralValidationResult:
        """Run Phase 1 structural validation."""
        ont_dict = ontology.model_dump(mode="json", exclude_none=True)
        prov_tokens = self.scan_provisional_tokens(ont_dict)
        if delta is not None:
            delta_dump = delta.model_dump(mode="json", exclude_none=True)
            if isinstance(delta_dump.get("metadata"), dict) and (
                "staged_candidates" in delta_dump["metadata"]
            ):
                meta_copy = dict(delta_dump["metadata"])
                meta_copy.pop("staged_candidates", None)
                delta_dump["metadata"] = meta_copy
            prov_tokens.extend(self.scan_provisional_tokens(delta_dump))

        if prov_tokens:
            unique_prov = sorted(set(prov_tokens))
            raise ProvisionalTokenError(
                f"ONT_EVO_GROUNDING_VIOLATION: Reserved provisional URI detected: {unique_prov}"
            )

        self.validate_acyclicity(ontology)
        self.validate_hub_bijection(ontology)

        diagnostics = [
            f"Structural validation passed: {len(ontology.canonical_business_concepts)} concepts, "
            f"{len(ontology.canonical_relationships)} relationships."
        ]
        return StructuralValidationResult(valid=True, diagnostics=diagnostics)


__all__ = ["StructuralValidator"]
