"""Transitive subClassOf Closure Resolver & Grounding Guard for reference ontologies.

Adheres strictly to ADR-003, ADR-004, and docs/19 §4.3 & §7.3 (ONT-EVO-002):
- Implements transitive taxonomic closure resolution over rdfs:subClassOf hierarchies
  using pre-compiled RFC 8785 reference packs (e.g. FiboReferenceOntologyLoader).
- Grounding Guard Invariants:
  * Computes transitive closure strictly iff direct seed mapping is grounded (score >= 0.6500).
  * Rejects ungrounded seed mappings (< 0.6500) with UngroundedSeedMappingError.
  * Classifies all inferred superclasses strictly as skos:broadMatch (never exact or close).
  * Inferred superclasses inherit the direct seed mapping's confidence score.
  * Inferred superclasses are strictly excluded from satisfying standalone Path A positive
    grounding for ungrounded concepts (exempt from standalone grounding / ONT-EVO-002).
- Robust Graph Traversal:
  * Safe breadth-first search (BFS) tracking visited nodes.
  * Cycle detection handling self-loops and circular inheritance without infinite recursion.
  * Configurable max_depth (default 32) truncation.
  * Deduplication across multiple inheritance paths, retaining shortest depth.
- Resource Invariants:
  * Zero runtime rdflib dependency.
  * Sub-millisecond closure resolution over compiled in-memory packs.
  * Low memory footprint.
"""

from __future__ import annotations

import collections
import logging
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from opendv_contracts.errors import ErrorCode
from opendv_sources.exceptions import SourceError, SourceNotFoundError

logger = logging.getLogger(__name__)

# Authoritative Constants (docs/19 §4.3 & §7.3, ADR-003)
GROUNDING_THRESHOLD: Final[Decimal] = Decimal("0.6500")
DEFAULT_MAX_DEPTH: Final[int] = 32
BROAD_MATCH_PREDICATE: Final[str] = "skos:broadMatch"
DEFAULT_PROVENANCE: Final[str] = "deterministic"
DEFAULT_SCHEME: Final[str] = "fibo"

_DECIMAL_FOUR_PLACES: Final[Decimal] = Decimal("0.0001")


class UngroundedSeedMappingError(SourceError, ValueError):
    """Raised when transitive closure is attempted on an ungrounded seed mapping (< 0.6500).

    Adheres to ONT-EVO-002 and docs/19 §4.3 / §7.3:
    Transitive closure is computed strictly iff direct seed mapping is grounded (>= 0.6500).
    """

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    exit_code: int = 4
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        concept_uri: str | None = None,
        direct_seed_score: float | Decimal | None = None,
        threshold: float | Decimal = GROUNDING_THRESHOLD,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if concept_uri is not None:
            merged_details["concept_uri"] = concept_uri
        if direct_seed_score is not None:
            merged_details["direct_seed_score"] = float(direct_seed_score)
        merged_details["grounding_threshold"] = float(threshold)
        merged_details["error_code_symbol"] = "ONT_EVO_UNGROUNDED_CONCEPT"
        super().__init__(message, details=merged_details)
        self.exit_code = 4
        self.http_status = 422
        self.error_code_symbol = "ONT_EVO_UNGROUNDED_CONCEPT"


class TaxonomyCycleError(SourceError, ValueError):
    """Raised when a circular inheritance relationship is detected in taxonomic hierarchy."""

    error_code: ErrorCode = ErrorCode.SOURCE_LOAD_ERROR
    exit_code: int = 4
    http_status: int = 422

    def __init__(
        self,
        message: str,
        *,
        cycle_path: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged_details = dict(details or {})
        if cycle_path is not None:
            merged_details["cycle_path"] = list(cycle_path)
        merged_details["error_code_symbol"] = "HIERARCHICAL_CYCLE_DETECTED"
        super().__init__(message, details=merged_details)
        self.exit_code = 4
        self.http_status = 422
        self.error_code_symbol = "HIERARCHICAL_CYCLE_DETECTED"


class SubclassClosureMatch(BaseModel):
    """Represents an inferred taxonomic superclass resolved via transitive subClassOf closure.

    Adheres strictly to ADR-003 and docs/19 §4.3:
    - Inferred superclasses are categorized strictly as skos:broadMatch (never exact or close).
    - Confidence is strictly inherited from the direct seed mapping score.
    - Excluded from satisfying standalone Path A positive grounding for ungrounded concepts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    uri: str = Field(..., description="Target reference ontology superclass URI")
    label: str | None = Field(default=None, description="Concept human-readable label")
    depth: int = Field(..., ge=1, description="Taxonomic distance (hops) from direct seed concept")
    predicate: str = Field(
        default=BROAD_MATCH_PREDICATE,
        description="Taxonomic relationship predicate, strictly 'skos:broadMatch'",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Inherited seed mapping confidence score (quantized to 4 decimal places)",
    )
    provenance: str = Field(
        default=DEFAULT_PROVENANCE,
        description="Attribution provenance, normative 'deterministic'",
    )
    reference_pack_id: str | None = Field(
        default=None,
        description="Registered reference ontology pack ID (e.g. 'fibo-core')",
    )
    reference_pack_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Authoritative SHA-256 digest of reference ontology pack",
    )
    path: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Sequence of URIs from direct seed to this superclass",
    )
    definition: str | None = Field(
        default=None,
        description="Concept definition or description from reference pack",
    )

    @property
    def satisfies_path_a(self) -> bool:
        """Transitive skos:broadMatch superclasses cannot satisfy Path A alone (ONT-EVO-002)."""
        return False

    @property
    def is_standalone_grounding_eligible(self) -> bool:
        """Transitive skos:broadMatch cannot satisfy standalone positive grounding."""
        return False

    def to_external_mapping(self, scheme: str = DEFAULT_SCHEME) -> Any:
        """Convert this closure match into an opendv_core ExternalMapping domain model instance."""
        from opendv_core.models.graph_a import ExternalMapping

        return ExternalMapping(
            scheme=scheme,
            target_uri=self.uri,
            confidence=self.confidence,
            predicate=self.predicate,
            pinned=False,
            provenance=self.provenance,
            reference_pack_id=self.reference_pack_id,
            reference_pack_sha256=self.reference_pack_sha256,
        )


def _normalize_score(score: float | Decimal | str | int) -> Decimal:
    """Normalize and quantize confidence score to 4 decimal places with bounds checking."""
    if isinstance(score, Decimal):
        dec = score
    elif isinstance(score, (float, int, str)):
        try:
            dec = Decimal(str(score))
        except Exception as err:
            msg = f"Invalid score representation '{score}': {err}"
            raise ValueError(msg) from err
    else:
        msg = f"Unsupported score type: {type(score).__name__}"
        raise TypeError(msg)

    if dec < Decimal("0.0") or dec > Decimal("1.0"):
        msg = f"Confidence score must be between 0.0 and 1.0, got {dec}"
        raise ValueError(msg)

    return dec.quantize(_DECIMAL_FOUR_PLACES, rounding=ROUND_HALF_UP)


def can_satisfy_path_a_grounding(predicate: str | None) -> bool:
    """Determine if a mapping predicate satisfies Path A reference pack grounding.

    Per docs/19 §4.3 and §7.3 (ONT-EVO-002):
    Only direct 'skos:exactMatch' and 'skos:closeMatch' mappings satisfy Path A.
    Transitive 'skos:broadMatch' superclasses are strictly excluded.
    """
    if predicate is None:
        return False
    norm = predicate.strip().lower()
    return norm in ("skos:exactmatch", "exactmatch", "skos:closematch", "closematch")


def validate_closure_grounding_eligibility(matches: Sequence[SubclassClosureMatch]) -> None:
    """Assert that transitive subclass closure matches cannot satisfy standalone Path A."""
    for match in matches:
        if match.predicate != BROAD_MATCH_PREDICATE:
            msg = (
                f"Invalid closure predicate '{match.predicate}': must be "
                f"'{BROAD_MATCH_PREDICATE}'"
            )
            raise ValueError(msg)
        if match.satisfies_path_a:
            msg = "Transitive closure matches cannot satisfy Path A grounding alone"
            raise ValueError(msg)
        if match.is_standalone_grounding_eligible:
            msg = "Transitive closure matches cannot serve as standalone grounding"
            raise ValueError(msg)


class SubclassClosureResolver:
    """Taxonomic subClassOf transitive closure resolver with grounding guard enforcement.

    Traverses rdfs:subClassOf hierarchies from a direct seed concept using breadth-first
    search (BFS). Enforces strict grounding guards, cycle protection, shortest-path
    deduplication, and depth truncation.
    """

    def __init__(
        self,
        loader: Any,
        *,
        default_max_depth: int = DEFAULT_MAX_DEPTH,
        grounding_threshold: Decimal | float = GROUNDING_THRESHOLD,
    ) -> None:
        """Initialize the closure resolver with an underlying reference ontology loader.

        Args:
            loader: Reference ontology loader or adapter (e.g. FiboReferenceOntologyLoader).
            default_max_depth: Default maximum traversal depth (hops) from seed concept.
            grounding_threshold: Minimum direct seed confidence required for closure resolution.
        """
        if default_max_depth < 1:
            msg = f"default_max_depth must be at least 1, got {default_max_depth}"
            raise ValueError(msg)

        self._loader = loader
        self._default_max_depth = default_max_depth
        self._grounding_threshold = _normalize_score(grounding_threshold)

    @property
    def loader(self) -> Any:
        """The underlying reference ontology loader."""
        return self._loader

    @property
    def default_max_depth(self) -> int:
        """Default maximum traversal depth."""
        return self._default_max_depth

    @property
    def grounding_threshold(self) -> Decimal:
        """Authoritative grounding threshold score."""
        return self._grounding_threshold

    def _get_superclasses(self, uri: str) -> list[str]:
        """Retrieve direct superclasses for a URI from loader via SPI or duck typing."""
        if hasattr(self._loader, "get_direct_superclasses"):
            try:
                res = self._loader.get_direct_superclasses(uri)
                if isinstance(res, (list, tuple, set)):
                    return list(res)
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed to get direct superclasses from loader: %s", err)

        if hasattr(self._loader, "lookup_by_uri"):
            try:
                entry = self._loader.lookup_by_uri(uri)
                if isinstance(entry, dict):
                    raw_sub = entry.get("sub_class_of", [])
                    if isinstance(raw_sub, (list, tuple, set)):
                        return list(raw_sub)
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed lookup_by_uri on superclass retrieval: %s", err)

        return []

    def _build_match(
        self,
        uri: str,
        *,
        depth: int,
        path: tuple[str, ...],
        confidence: float,
        pack_id: str | None,
        pack_sha256: str | None,
        only_pack_concepts: bool,
    ) -> SubclassClosureMatch | None:
        """Construct a SubclassClosureMatch for a resolved superclass URI."""
        entry: dict[str, Any] | None = None
        if hasattr(self._loader, "lookup_by_uri"):
            try:
                entry = self._loader.lookup_by_uri(uri)
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Lookup failed for URI %s: %s", uri, err)
                entry = None

        if entry is None and only_pack_concepts:
            return None

        label: str | None = None
        definition: str | None = None
        if isinstance(entry, dict):
            raw_label = entry.get("label")
            label = str(raw_label).strip() if raw_label is not None else None
            raw_def = entry.get("definition")
            definition = str(raw_def).strip() if raw_def is not None else None

        if not label:
            # Fallback to local name from URI fragment/path
            local_name = uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip()
            label = local_name if local_name else None

        # Clean pack sha256 to exactly 64-char hex if valid, else None
        valid_pack_sha = (
            pack_sha256
            if isinstance(pack_sha256, str) and len(pack_sha256) == 64
            else None
        )

        return SubclassClosureMatch(
            uri=uri,
            label=label,
            depth=depth,
            predicate=BROAD_MATCH_PREDICATE,
            confidence=confidence,
            provenance=DEFAULT_PROVENANCE,
            reference_pack_id=str(pack_id) if pack_id is not None else None,
            reference_pack_sha256=valid_pack_sha,
            path=path,
            definition=definition,
        )

    def resolve(
        self,
        concept_uri: str,
        direct_seed_score: float | Decimal | str | int,
        *,
        max_depth: int | None = None,
        raise_on_ungrounded: bool = True,
        raise_on_cycle: bool = False,
        only_pack_concepts: bool = False,
        require_known_seed: bool = False,
    ) -> list[SubclassClosureMatch]:
        """Resolve transitive subClassOf closure for a concept URI with grounding guard check.

        Args:
            concept_uri: Fully-qualified seed concept standard URI.
            direct_seed_score: Verified confidence score of direct mapping to seed concept.
            max_depth: Optional maximum traversal depth (hops). Defaults to instance default (32).
            raise_on_ungrounded: If True, raises UngroundedSeedMappingError when score < 0.6500.
                                 If False, returns empty list.
            raise_on_cycle: If True, raises TaxonomyCycleError on cyclic inheritance.
                            If False (default), detects cycles, logs warning, and avoids recursion.
            only_pack_concepts: If True, excludes external superclasses not present in loader pack.
            require_known_seed: If True, raises SourceNotFoundError if seed concept is unknown.

        Returns:
            Deduplicated, deterministically sorted list of SubclassClosureMatch instances
            tagged strictly as 'skos:broadMatch' with inherited confidence.

        Raises:
            UngroundedSeedMappingError: If direct_seed_score < 0.6500 and raise_on_ungrounded=True.
            TaxonomyCycleError: If cyclic hierarchy detected and raise_on_cycle=True.
            SourceNotFoundError: If require_known_seed=True and concept_uri not in loader.
            ValueError: On invalid URI, invalid score, or depth < 1.
        """
        if not concept_uri or not isinstance(concept_uri, str) or not concept_uri.strip():
            msg = "concept_uri must be a non-empty string"
            raise ValueError(msg)
        clean_uri = concept_uri.strip()

        effective_max_depth = (
            max_depth if max_depth is not None else self._default_max_depth
        )
        if effective_max_depth < 1:
            msg = f"max_depth must be at least 1, got {effective_max_depth}"
            raise ValueError(msg)

        quantized_score = _normalize_score(direct_seed_score)
        float_confidence = float(quantized_score)

        # Grounding Guard Enforcement (ONT-EVO-002, docs/19 §4.3)
        if quantized_score < self._grounding_threshold:
            if raise_on_ungrounded:
                msg = (
                    f"Transitive subClassOf closure rejected for concept '{clean_uri}': "
                    f"direct seed mapping confidence {quantized_score} is below authoritative "
                    f"grounding threshold {self._grounding_threshold} (ONT-EVO-002)"
                )
                raise UngroundedSeedMappingError(
                    msg,
                    concept_uri=clean_uri,
                    direct_seed_score=quantized_score,
                    threshold=self._grounding_threshold,
                )
            logger.debug(
                "Direct seed score %s < threshold %s; returning empty closure",
                quantized_score,
                self._grounding_threshold,
            )
            return []

        # Check seed existence if require_known_seed asserted
        if (
            require_known_seed
            and hasattr(self._loader, "lookup_by_uri")
            and self._loader.lookup_by_uri(clean_uri) is None
        ):
            msg = f"Seed concept URI '{clean_uri}' not found in reference ontology pack"
            raise SourceNotFoundError(msg)

        pack_id = getattr(self._loader, "pack_id", None)
        pack_sha256 = getattr(self._loader, "pack_sha256", None)

        # Breadth-first search traversal structures
        queue: collections.deque[tuple[str, int, tuple[str, ...]]] = collections.deque()
        shortest_depth: dict[str, int] = {}
        shortest_path: dict[str, tuple[str, ...]] = {}
        matches_by_uri: dict[str, SubclassClosureMatch] = {}
        detected_cycles: list[tuple[str, str]] = []

        # Enqueue direct superclasses (depth = 1)
        direct_parents = self._get_superclasses(clean_uri)
        for parent_uri in direct_parents:
            if not parent_uri or not isinstance(parent_uri, str):
                continue
            p_uri = parent_uri.strip()
            if p_uri == clean_uri:
                detected_cycles.append((clean_uri, p_uri))
                logger.warning("Self-loop cycle detected on seed concept: %s", clean_uri)
                if raise_on_cycle:
                    msg = f"Self-loop cycle detected on concept '{clean_uri}'"
                    raise TaxonomyCycleError(msg, cycle_path=[clean_uri, p_uri])
                continue

            init_path = (clean_uri, p_uri)
            match = self._build_match(
                p_uri,
                depth=1,
                path=init_path,
                confidence=float_confidence,
                pack_id=pack_id,
                pack_sha256=pack_sha256,
                only_pack_concepts=only_pack_concepts,
            )
            if match is not None:
                matches_by_uri[p_uri] = match
                shortest_depth[p_uri] = 1
                shortest_path[p_uri] = init_path
                if effective_max_depth > 1:
                    queue.append((p_uri, 1, init_path))

        # BFS expansion
        while queue:
            curr_uri, curr_depth, curr_path = queue.popleft()
            next_depth = curr_depth + 1
            if next_depth > effective_max_depth:
                continue

            parents = self._get_superclasses(curr_uri)
            for p_uri in parents:
                if not p_uri or not isinstance(p_uri, str):
                    continue
                p_uri = p_uri.strip()

                # Cycle detection: check if p_uri is in current traversal path or equals seed
                if p_uri in curr_path or p_uri == clean_uri:
                    detected_cycles.append((curr_uri, p_uri))
                    logger.warning(
                        "Taxonomic cycle detected: %s -> %s (path: %s)",
                        curr_uri,
                        p_uri,
                        curr_path,
                    )
                    if raise_on_cycle:
                        cycle_trace = [*curr_path, p_uri]
                        msg = f"Taxonomic cycle detected: {' -> '.join(cycle_trace)}"
                        raise TaxonomyCycleError(msg, cycle_path=cycle_trace)
                    continue

                new_path = (*curr_path, p_uri)

                if p_uri not in shortest_depth:
                    shortest_depth[p_uri] = next_depth
                    shortest_path[p_uri] = new_path
                    match = self._build_match(
                        p_uri,
                        depth=next_depth,
                        path=new_path,
                        confidence=float_confidence,
                        pack_id=pack_id,
                        pack_sha256=pack_sha256,
                        only_pack_concepts=only_pack_concepts,
                    )
                    if match is not None:
                        matches_by_uri[p_uri] = match
                    if next_depth < effective_max_depth:
                        queue.append((p_uri, next_depth, new_path))
                elif next_depth < shortest_depth[p_uri]:
                    # Found strictly shorter path to an already discovered node
                    shortest_depth[p_uri] = next_depth
                    shortest_path[p_uri] = new_path
                    match = self._build_match(
                        p_uri,
                        depth=next_depth,
                        path=new_path,
                        confidence=float_confidence,
                        pack_id=pack_id,
                        pack_sha256=pack_sha256,
                        only_pack_concepts=only_pack_concepts,
                    )
                    if match is not None:
                        matches_by_uri[p_uri] = match
                    if next_depth < effective_max_depth:
                        queue.append((p_uri, next_depth, new_path))
                elif next_depth == shortest_depth[p_uri]:
                    # Equal depth: tie-break deterministically by lexicographical path
                    if new_path < shortest_path[p_uri]:
                        shortest_path[p_uri] = new_path
                        existing = matches_by_uri.get(p_uri)
                        if existing is not None:
                            matches_by_uri[p_uri] = existing.model_copy(
                                update={"path": new_path}
                            )

        # Deterministic sorting: by depth ascending, then by URI ascending
        results = list(matches_by_uri.values())
        results.sort(key=lambda m: (m.depth, m.uri))
        return results


def resolve_subclass_closure(
    loader: Any,
    concept_uri: str,
    direct_seed_score: float | Decimal | str | int,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    raise_on_ungrounded: bool = True,
    raise_on_cycle: bool = False,
    only_pack_concepts: bool = False,
    require_known_seed: bool = False,
) -> list[SubclassClosureMatch]:
    """Resolve transitive subClassOf closure over an ontology reference pack.

    Convenience wrapper around SubclassClosureResolver.

    Args:
        loader: Reference ontology loader or adapter (e.g. FiboReferenceOntologyLoader).
        concept_uri: Fully-qualified seed concept standard URI.
        direct_seed_score: Verified confidence score of direct mapping to seed concept.
        max_depth: Maximum traversal depth (hops) from direct seed concept (default 32).
        raise_on_ungrounded: If True, raises UngroundedSeedMappingError when score < 0.6500.
                             If False, returns empty list.
        raise_on_cycle: If True, raises TaxonomyCycleError on cyclic inheritance.
                        If False (default), handles cycles gracefully.
        only_pack_concepts: If True, excludes superclasses not present in loader pack.
        require_known_seed: If True, raises SourceNotFoundError if seed concept is unknown.

    Returns:
        List of SubclassClosureMatch instances tagged strictly as 'skos:broadMatch'.
    """
    resolver = SubclassClosureResolver(loader, default_max_depth=max_depth)
    return resolver.resolve(
        concept_uri,
        direct_seed_score,
        max_depth=max_depth,
        raise_on_ungrounded=raise_on_ungrounded,
        raise_on_cycle=raise_on_cycle,
        only_pack_concepts=only_pack_concepts,
        require_known_seed=require_known_seed,
    )


__all__ = [
    "BROAD_MATCH_PREDICATE",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_PROVENANCE",
    "DEFAULT_SCHEME",
    "GROUNDING_THRESHOLD",
    "SubclassClosureMatch",
    "SubclassClosureResolver",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "can_satisfy_path_a_grounding",
    "resolve_subclass_closure",
    "validate_closure_grounding_eligibility",
]
