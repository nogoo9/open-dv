"""Transitive subClassOf, subPropertyOf, role-player, and Pack v2 derived index closure."""

from __future__ import annotations

import collections
import logging
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from opendv.grounding.providers.fibo.closure_rules import (
    BROAD_MATCH_PREDICATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_PROVENANCE,
    GROUNDING_THRESHOLD,
    SubclassClosureMatch,
    TaxonomyCycleError,
    UngroundedSeedMappingError,
    normalize_score,
)
from opendv.models.errors import ErrorCode, OpenDVError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PackDerivedIndexes:
    """Precomputed Pack v2 indexes for restrictions, inverses, disjointness, and deprecations."""

    restriction_domains: dict[str, set[str]] = field(default_factory=dict)
    restriction_ranges: dict[str, set[str]] = field(default_factory=dict)
    inverse_of_map: dict[str, set[str]] = field(default_factory=dict)
    direct_disjoint: dict[str, set[str]] = field(default_factory=dict)
    ancestors_with_self: dict[str, set[str]] = field(default_factory=dict)
    ancestor_disjoint_roots: dict[str, set[str]] = field(default_factory=dict)
    replaces_uris: dict[str, list[str]] = field(default_factory=dict)
    replaced_by_map: dict[str, str] = field(default_factory=dict)

    def are_disjoint(self, uri_a: str, uri_b: str) -> bool:
        anc_a = self.ancestors_with_self.get(uri_a, {uri_a})
        if anc_a & self.ancestor_disjoint_roots.get(uri_b, set()):
            return True
        anc_b = self.ancestors_with_self.get(uri_b, {uri_b})
        return bool(anc_b & self.ancestor_disjoint_roots.get(uri_a, set()))


def minimal_taxonomic_leaves(
    uris: Sequence[str],
    ancestors_fn: Callable[[str], AbstractSet[str]],
) -> list[str]:
    """Keep only URIs that are not strict taxonomic ancestors of another URI in uris."""
    deduped = list(dict.fromkeys(u for u in uris if u))
    if len(deduped) <= 1:
        return deduped
    strict_anc: set[str] = set()
    for u in deduped:
        anc = set(ancestors_fn(u))
        anc.discard(u)
        strict_anc.update(anc)
    return [u for u in deduped if u not in strict_anc]


def _compute_ancestors_with_self(classes: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    ancestors: dict[str, set[str]] = {}
    for start_uri in classes:
        visited: set[str] = {start_uri}
        queue: collections.deque[str] = collections.deque([start_uri])
        while queue:
            curr = queue.popleft()
            entry = classes.get(curr)
            if not entry:
                continue
            for parent in entry.get("sub_class_of", ()):
                if isinstance(parent, str) and parent and parent not in visited:
                    visited.add(parent)
                    queue.append(parent)
        ancestors[start_uri] = visited
    return ancestors


def build_pack_v2_derived_indexes(
    classes: Mapping[str, Mapping[str, Any]],
    properties: Mapping[str, Mapping[str, Any]],
    individuals: Mapping[str, Mapping[str, Any]] | None = None,
) -> PackDerivedIndexes:
    """Build restriction, inverse_of, ancestor-closed disjointness, and replacement indexes."""
    r_dom: dict[str, set[str]] = {}
    r_rng: dict[str, set[str]] = {}
    inv_map: dict[str, set[str]] = {}
    dir_disj: dict[str, set[str]] = {}
    rev_repl: dict[str, set[str]] = {}
    repl_by: dict[str, str] = {}

    for c_uri, c_val in classes.items():
        for assoc in c_val.get("associated_properties", ()) or ():
            prop_uri = assoc.get("property_uri") if isinstance(assoc, Mapping) else None
            if isinstance(prop_uri, str) and prop_uri:
                r_dom.setdefault(prop_uri, set()).add(c_uri)
                for tgt in assoc.get("target_uris", ()) or ():
                    if isinstance(tgt, str) and tgt:
                        r_rng.setdefault(prop_uri, set()).add(tgt)
        for disj in c_val.get("disjoint_with", ()) or ():
            if isinstance(disj, str) and disj and disj != c_uri:
                dir_disj.setdefault(c_uri, set()).add(disj)
                dir_disj.setdefault(disj, set()).add(c_uri)
        repl = c_val.get("replaced_by")
        if isinstance(repl, str) and repl:
            repl_by[c_uri] = repl
            rev_repl.setdefault(repl, set()).add(c_uri)

    for p_uri, p_val in properties.items():
        raw_inv = p_val.get("inverse_of", ())
        for inv_uri in [raw_inv] if isinstance(raw_inv, str) else (raw_inv or ()):
            if isinstance(inv_uri, str) and inv_uri:
                inv_map.setdefault(p_uri, set()).add(inv_uri)
                inv_map.setdefault(inv_uri, set()).add(p_uri)
        repl = p_val.get("replaced_by")
        if isinstance(repl, str) and repl:
            repl_by[p_uri] = repl
            rev_repl.setdefault(repl, set()).add(p_uri)

    for ind_uri, ind_val in (individuals or {}).items():
        repl = ind_val.get("replaced_by")
        if isinstance(repl, str) and repl:
            repl_by[ind_uri] = repl
            rev_repl.setdefault(repl, set()).add(ind_uri)

    anc_self = _compute_ancestors_with_self(classes)
    anc_disj: dict[str, set[str]] = {}
    for c_uri, anc_set in anc_self.items():
        roots = {d for anc in anc_set for d in dir_disj.get(anc, ())}
        if roots:
            anc_disj[c_uri] = roots
    for d_uri, d_set in dir_disj.items():
        anc_disj.setdefault(d_uri, set()).update(d_set)

    replaces_uris: dict[str, list[str]] = {}
    for target_uri, seeds in rev_repl.items():
        seen: set[str] = set()
        q: collections.deque[str] = collections.deque(seeds)
        while q:
            old = q.popleft()
            if old == target_uri or old in seen:
                continue
            seen.add(old)
            q.extend(o for o in rev_repl.get(old, ()) if o not in seen and o != target_uri)
        if seen:
            replaces_uris[target_uri] = sorted(seen)

    return PackDerivedIndexes(
        r_dom,
        r_rng,
        inv_map,
        dir_disj,
        anc_self,
        anc_disj,
        replaces_uris,
        repl_by,
    )


class SubclassClosureResolver:
    """Taxonomic subClassOf, subPropertyOf, and inherited role/identity closure resolver."""

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

    def _lookup_list_field(self, uri: str, method_name: str, field_name: str) -> list[str]:
        if hasattr(self._loader, method_name):
            try:
                res = getattr(self._loader, method_name)(uri)
                if isinstance(res, (list, tuple, set)):
                    return [str(x) for x in res]
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed %s: %s", method_name, err)
        if hasattr(self._loader, "lookup_by_uri"):
            try:
                entry = self._loader.lookup_by_uri(uri)
                if isinstance(entry, dict):
                    raw = entry.get(field_name, [])
                    if isinstance(raw, (list, tuple, set)):
                        return [str(x) for x in raw]
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                logger.debug("Failed lookup_by_uri %s: %s", field_name, err)
        return []

    def _get_superclasses(self, uri: str) -> list[str]:
        return self._lookup_list_field(uri, "get_direct_superclasses", "sub_class_of")

    def _get_superproperties(self, uri: str) -> list[str]:
        return self._lookup_list_field(uri, "get_direct_superproperties", "sub_property_of")

    def _taxonomic_ancestors_with_self(self, uri: str) -> set[str]:
        visited: set[str] = {uri}
        queue: collections.deque[str] = collections.deque([uri])
        while queue:
            curr = queue.popleft()
            for p in (x.strip() for x in self._get_superclasses(curr)):
                if p and p not in visited:
                    visited.add(p)
                    queue.append(p)
        return visited

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

        label = (
            str(entry["label"]).strip()
            if isinstance(entry, dict) and entry.get("label") is not None
            else None
        ) or (uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip() or None)
        definition = (
            str(entry["definition"]).strip()
            if isinstance(entry, dict) and entry.get("definition") is not None
            else None
        )
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
        """Traverse strictly taxonomic subClassOf ancestors for a grounded seed URI."""
        if not concept_uri or not isinstance(concept_uri, str) or not concept_uri.strip():
            raise ValueError("concept_uri must be a non-empty string")
        clean_uri = concept_uri.strip()
        limit = max_depth if max_depth is not None else self._default_max_depth
        if limit < 1:
            raise ValueError(f"max_depth must be at least 1, got {limit}")

        q_score = normalize_score(direct_seed_score)
        if q_score < self._grounding_threshold:
            if raise_on_ungrounded:
                raise UngroundedSeedMappingError(
                    f"Closure rejected for '{clean_uri}': "
                    f"score {q_score} < {self._grounding_threshold}",
                    concept_uri=clean_uri,
                    direct_seed_score=q_score,
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
        pack_sha = getattr(self._loader, "pack_sha256", None)
        float_conf = float(q_score)

        queue: collections.deque[tuple[str, int, tuple[str, ...]]] = collections.deque(
            [(clean_uri, 0, (clean_uri,))]
        )
        shortest_depth: dict[str, int] = {}
        shortest_path: dict[str, tuple[str, ...]] = {}
        matches_by_uri: dict[str, SubclassClosureMatch] = {}

        while queue:
            curr_uri, curr_depth, curr_path = queue.popleft()
            next_depth = curr_depth + 1
            if next_depth > limit:
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
                    m = self._build_match(
                        p_uri,
                        depth=next_depth,
                        path=new_path,
                        confidence=float_conf,
                        pack_id=pack_id,
                        pack_sha256=pack_sha,
                        only_pack_concepts=only_pack_concepts,
                    )
                    if m is not None:
                        matches_by_uri[p_uri] = m
                    if next_depth < limit:
                        queue.append((p_uri, next_depth, new_path))
                elif next_depth == shortest_depth[p_uri] and new_path < shortest_path[p_uri]:
                    shortest_path[p_uri] = new_path
                    existing = matches_by_uri.get(p_uri)
                    if existing is not None:
                        matches_by_uri[p_uri] = existing.model_copy(update={"path": new_path})

        return sorted(matches_by_uri.values(), key=lambda m: (m.depth, m.uri))

    def resolve_property_closure(
        self,
        property_uri: str,
        *,
        max_depth: int | None = None,
        raise_on_cycle: bool = False,
    ) -> list[str]:
        """Resolve transitive subPropertyOf superproperties ordered nearest-first."""
        clean_uri = property_uri.strip()
        if not clean_uri:
            raise ValueError("property_uri must be a non-empty string")
        limit = max_depth if max_depth is not None else self._default_max_depth
        if limit < 1:
            raise ValueError(f"max_depth must be at least 1, got {limit}")

        seen_depth: dict[str, int] = {}
        queue: collections.deque[tuple[str, int, tuple[str, ...]]] = collections.deque(
            [(clean_uri, 0, (clean_uri,))]
        )
        while queue:
            curr, depth, path = queue.popleft()
            if depth >= limit:
                continue
            for p_uri in (x.strip() for x in self._get_superproperties(curr) if x.strip()):
                if p_uri in path or p_uri == clean_uri:
                    if raise_on_cycle:
                        raise TaxonomyCycleError(
                            f"Property cycle detected: {' -> '.join([*path, p_uri])}",
                            cycle_path=[*path, p_uri],
                        )
                    continue
                if p_uri not in seen_depth:
                    seen_depth[p_uri] = depth + 1
                    queue.append((p_uri, depth + 1, (*path, p_uri)))
        return [u for u, _ in sorted(seen_depth.items(), key=lambda kv: (kv[1], kv[0]))]

    def _resolve_inherited_targets(
        self,
        concept_uri: str,
        field_name: str,
        *,
        max_depth: int | None = None,
        reduce_leaves: bool = True,
    ) -> list[str]:
        clean_uri = concept_uri.strip()
        if not clean_uri:
            return []
        limit = max_depth if max_depth is not None else self._default_max_depth
        visited: set[str] = {clean_uri}
        queue: collections.deque[tuple[str, int]] = collections.deque([(clean_uri, 0)])
        collected: list[str] = []

        while queue:
            curr, depth = queue.popleft()
            if hasattr(self._loader, "lookup_by_uri"):
                entry = self._loader.lookup_by_uri(curr)
                if isinstance(entry, Mapping):
                    collected.extend(
                        t for t in (entry.get(field_name, ()) or ()) if isinstance(t, str) and t
                    )
            if depth < limit:
                for p in (x.strip() for x in self._get_superclasses(curr)):
                    if p and p not in visited:
                        visited.add(p)
                        queue.append((p, depth + 1))

        deduped = list(dict.fromkeys(collected))
        if reduce_leaves and len(deduped) > 1:
            return minimal_taxonomic_leaves(deduped, self._taxonomic_ancestors_with_self)
        return deduped

    def resolve_role_players(
        self,
        concept_uri: str,
        *,
        max_depth: int | None = None,
        reduce_leaves: bool = True,
    ) -> list[str]:
        """Resolve is_played_by role-player URIs inherited across taxonomic superclasses."""
        return self._resolve_inherited_targets(
            concept_uri, "is_played_by", max_depth=max_depth, reduce_leaves=reduce_leaves
        )

    def resolve_identified_entities(
        self,
        concept_uri: str,
        *,
        max_depth: int | None = None,
        reduce_leaves: bool = True,
    ) -> list[str]:
        """Resolve identified_entity_uris inherited across taxonomic superclasses."""
        return self._resolve_inherited_targets(
            concept_uri, "identified_entity_uris", max_depth=max_depth, reduce_leaves=reduce_leaves
        )


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
    "PackDerivedIndexes",
    "SubclassClosureMatch",
    "SubclassClosureResolver",
    "TaxonomyCycleError",
    "UngroundedSeedMappingError",
    "build_pack_v2_derived_indexes",
    "minimal_taxonomic_leaves",
    "resolve_subclass_closure",
]
