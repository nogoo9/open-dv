"""Transitive subClassOf Closure Resolver for reference ontologies (V2)."""

from __future__ import annotations

import collections
import logging
from decimal import Decimal
from typing import Any

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.sources.fibo.closure_rules import (
    BROAD_MATCH_PREDICATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_PROVENANCE,
    GROUNDING_THRESHOLD,
    SubclassClosureMatch,
    TaxonomyCycleError,
    UngroundedSeedMappingError,
    normalize_score,
)

logger = logging.getLogger(__name__)


class SubclassClosureResolver:
    """Taxonomic subClassOf transitive closure resolver with grounding guard."""

    def __init__(
        self,
        loader: Any,
        *,
        default_max_depth: int = DEFAULT_MAX_DEPTH,
        grounding_threshold: Decimal | float = GROUNDING_THRESHOLD,
    ) -> None:
        if default_max_depth < 1:
            raise ValueError(f"default_max_depth must be at least 1, got {default_max_depth}")
        self._loader = loader
        self._default_max_depth = default_max_depth
        self._grounding_threshold = normalize_score(grounding_threshold)

    @property
    def loader(self) -> Any:
        return self._loader

    @property
    def default_max_depth(self) -> int:
        return self._default_max_depth

    @property
    def grounding_threshold(self) -> Decimal:
        return self._grounding_threshold

    def _get_superclasses(self, uri: str) -> list[str]:
        if hasattr(self._loader, "get_direct_superclasses"):
            try:
                res = self._loader.get_direct_superclasses(uri)
                if isinstance(res, (list, tuple, set)):
                    return [str(x) for x in res]
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed get_direct_superclasses: %s", err)

        if hasattr(self._loader, "lookup_by_uri"):
            try:
                entry = self._loader.lookup_by_uri(uri)
                if isinstance(entry, dict):
                    raw_sub = entry.get("sub_class_of", [])
                    if isinstance(raw_sub, (list, tuple, set)):
                        return [str(x) for x in raw_sub]
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed lookup_by_uri superclass lookup: %s", err)
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
        entry: dict[str, Any] | None = None
        if hasattr(self._loader, "lookup_by_uri"):
            try:
                entry = self._loader.lookup_by_uri(uri)
            except (AttributeError, KeyError, TypeError, ValueError):
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
            local_name = uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip()
            label = local_name or None

        valid_sha = pack_sha256 if isinstance(pack_sha256, str) and len(pack_sha256) == 64 else None
        return SubclassClosureMatch(
            uri=uri,
            label=label,
            depth=depth,
            predicate=BROAD_MATCH_PREDICATE,
            confidence=confidence,
            provenance=DEFAULT_PROVENANCE,
            reference_pack_id=str(pack_id) if pack_id is not None else None,
            reference_pack_sha256=valid_sha,
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
        if not concept_uri or not isinstance(concept_uri, str) or not concept_uri.strip():
            raise ValueError("concept_uri must be a non-empty string")
        clean_uri = concept_uri.strip()

        effective_max_depth = max_depth if max_depth is not None else self._default_max_depth
        if effective_max_depth < 1:
            raise ValueError(f"max_depth must be at least 1, got {effective_max_depth}")

        quantized_score = normalize_score(direct_seed_score)
        float_confidence = float(quantized_score)

        if quantized_score < self._grounding_threshold:
            if raise_on_ungrounded:
                raise UngroundedSeedMappingError(
                    f"Closure rejected for '{clean_uri}': score {quantized_score} < "
                    f"threshold {self._grounding_threshold}",
                    concept_uri=clean_uri,
                    direct_seed_score=quantized_score,
                    threshold=self._grounding_threshold,
                )
            return []

        if (
            require_known_seed
            and hasattr(self._loader, "lookup_by_uri")
            and self._loader.lookup_by_uri(clean_uri) is None
        ):
            raise OpenDVError(
                f"Seed concept URI '{clean_uri}' not found in reference ontology pack",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )

        pack_id = getattr(self._loader, "pack_id", None)
        pack_sha256 = getattr(self._loader, "pack_sha256", None)

        queue: collections.deque[tuple[str, int, tuple[str, ...]]] = collections.deque()
        shortest_depth: dict[str, int] = {}
        shortest_path: dict[str, tuple[str, ...]] = {}
        matches_by_uri: dict[str, SubclassClosureMatch] = {}

        for parent_uri in self._get_superclasses(clean_uri):
            if not parent_uri or not isinstance(parent_uri, str):
                continue
            p_uri = parent_uri.strip()
            if p_uri == clean_uri:
                if raise_on_cycle:
                    raise TaxonomyCycleError(
                        f"Self-loop cycle detected on concept '{clean_uri}'",
                        cycle_path=[clean_uri, p_uri],
                    )
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

        while queue:
            curr_uri, curr_depth, curr_path = queue.popleft()
            next_depth = curr_depth + 1
            if next_depth > effective_max_depth:
                continue

            for raw_parent in self._get_superclasses(curr_uri):
                if not raw_parent or not isinstance(raw_parent, str):
                    continue
                p_uri = raw_parent.strip()

                if p_uri in curr_path or p_uri == clean_uri:
                    cycle_trace = [*curr_path, p_uri]
                    logger.warning("Taxonomic cycle detected: %s", " -> ".join(cycle_trace))
                    if raise_on_cycle:
                        raise TaxonomyCycleError(
                            f"Taxonomic cycle detected: {' -> '.join(cycle_trace)}",
                            cycle_path=cycle_trace,
                        )
                    continue

                new_path = (*curr_path, p_uri)
                if p_uri not in shortest_depth or next_depth < shortest_depth[p_uri]:
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
                elif next_depth == shortest_depth[p_uri] and new_path < shortest_path[p_uri]:
                    shortest_path[p_uri] = new_path
                    existing = matches_by_uri.get(p_uri)
                    if existing is not None:
                        matches_by_uri[p_uri] = existing.model_copy(update={"path": new_path})

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
    """Convenience wrapper around SubclassClosureResolver."""
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
    "GROUNDING_THRESHOLD",
    "SubclassClosureMatch",
    "SubclassClosureResolver",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "resolve_subclass_closure",
]
