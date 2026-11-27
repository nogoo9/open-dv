"""ProjectGraphASource indexing committed GraphAOntology, memory & steward answers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from typing import Final

from opendv.grounding.kernel.lexical import STOPWORDS, LexicalIndex
from opendv.models.grounding import (
    ArchetypeKind,
    ColumnEvidence,
    InferredValueProfile,
    SemanticContext,
)
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    TableReasoningContext,
)
from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.project import ConceptMemorySpec

_PINNED_MIN_SCORE: Final[Decimal] = Decimal("0.9200")
_CODE_MATCH_SCORE: Final[Decimal] = Decimal("0.9500")
_ONE_DEC: Final[Decimal] = Decimal("1.0000")
_ZERO_DEC: Final[Decimal] = Decimal("0.0000")


def _infer_concept_archetype(concept: CanonicalBusinessConcept) -> ArchetypeKind:
    if isinstance(concept.governance_constraints.get("played_by_uri"), str):
        return "party_role"
    haystack = f"{concept.concept_name} {concept.description}".lower()
    if any(k in haystack for k in ("agreement", "contract", "facility", "obligation")):
        return "agreement"
    if any(k in haystack for k in ("event", "transaction", "trade", "occurrence", "filing")):
        return "event_occurrence"
    if any(k in haystack for k in ("role", "custodian", "issuer", "counterparty", "party")):
        return "party_role"
    return "entity"


def _normalize_steward_notes(
    steward_answers: Mapping[str, str] | Sequence[str] | None,
) -> tuple[str, ...]:
    if not steward_answers:
        return ()
    notes: list[str] = []
    if isinstance(steward_answers, Mapping):
        for key, val in steward_answers.items():
            v_str = str(val).strip()
            k_str = str(key).strip()
            if v_str:
                notes.append(f"{k_str}: {v_str}" if k_str else v_str)
    else:
        for item in steward_answers:
            text = str(item).strip()
            if text:
                notes.append(text)
    return tuple(notes)


def dedupe_str_list(values: Sequence[str]) -> list[str]:
    """Deduplicate non-empty strings preserving order."""
    return list(dict.fromkeys(v for v in values if v))


class ProjectGraphASource:
    """In-memory KnowledgeSource indexing a committed GraphAOntology, memory & steward notes."""

    def __init__(
        self,
        ontology: GraphAOntology | None = None,
        *,
        concept_memory: Sequence[ConceptMemorySpec] = (),
        steward_answers: Mapping[str, str] | Sequence[str] | None = None,
        source_id: str = "project-graph-a",
        version: str | None = None,
        sha256: str | None = None,
    ) -> None:
        self._ontology = ontology
        self._source_id = source_id
        self._version = version or (ontology.ontology_version if ontology else "0.0.0")
        empty_ont: dict[str, list[object]] = {
            "concepts": [],
            "relationships": [],
            "enumerations": [],
        }
        self._sha256 = sha256 or sha256_jcs(ontology if ontology is not None else empty_ont)
        self.steward_notes: tuple[str, ...] = _normalize_steward_notes(steward_answers)

        self._items: dict[str, KnowledgeItem] = {}
        self.standard_to_project_uri: dict[str, str] = {}
        self.project_to_standard_uris: dict[str, list[str]] = {}
        self._enum_code_index: dict[str, list[str]] = {}
        self._label_to_id: dict[str, str] = {}

        if ontology is not None:
            self._index_ontology(ontology)

        self.pinned_project_terms: dict[str, list[str]] = {}
        self.pinned_reference_uris: dict[str, list[str]] = {}
        muted_uris: set[str] = set()
        muted_terms: set[str] = set()

        for mem in concept_memory:
            term = mem.term.strip()
            if mem.state == "PINNED":
                self._register_pinned(mem.concept_uri, term)
            elif mem.state == "MUTED":
                if mem.concept_uri:
                    muted_uris.add(mem.concept_uri)
                    if mem.concept_uri in self.standard_to_project_uri:
                        proj_uri = self.standard_to_project_uri[mem.concept_uri]
                        muted_uris.add(proj_uri)
                        for std_uri in self.project_to_standard_uris.get(proj_uri, ()):
                            muted_uris.add(std_uri)
                    for std_uri in self.project_to_standard_uris.get(mem.concept_uri, ()):
                        muted_uris.add(std_uri)
                if term:
                    muted_terms.add(term.casefold())

        self.muted_uris: frozenset[str] = frozenset(muted_uris)
        self.muted_terms: frozenset[str] = frozenset(muted_terms)

        indexed_items: list[KnowledgeItem] = []
        for entry_id, item in self._items.items():
            extra_terms = self.pinned_project_terms.get(entry_id, [])
            if extra_terms:
                merged_aliases = dedupe_str_list([*item.aliases, *extra_terms])
                item = item.model_copy(update={"aliases": merged_aliases})
                self._items[entry_id] = item
            indexed_items.append(item)

        self._index = LexicalIndex()
        self._index.index_items(
            indexed_items,
            origin_source_id=self._source_id,
            origin_kind="project_graph_a",
        )
        self._steward_tokens: list[tuple[frozenset[str], str]] = [
            (frozenset(self._index.tokenize(note)), note) for note in self.steward_notes
        ]

    def _register_std_link(self, standard_uri: str | None, project_uri: str) -> None:
        if not standard_uri:
            return
        self.standard_to_project_uri.setdefault(standard_uri, project_uri)
        self.project_to_standard_uris.setdefault(project_uri, []).append(standard_uri)

    def _index_ontology(self, ontology: GraphAOntology) -> None:
        for concept in ontology.concepts:
            archetype = _infer_concept_archetype(concept)
            played_by = concept.governance_constraints.get("played_by_uri")
            std_uris = [m.standard_uri for m in concept.external_mappings]
            item = KnowledgeItem(
                entry_id=concept.concept_uri,
                label=concept.concept_name,
                entry_kind="class",
                definition=concept.description,
                aliases=list(concept.synonyms),
                parent_ids=[concept.parent_concept_uri] if concept.parent_concept_uri else [],
                neighborhood_links={},
                replaces_uris=std_uris,
                semantic_context=SemanticContext(
                    archetype=archetype,
                    domain_entity_uri=concept.concept_uri,
                    played_by_uri=played_by if isinstance(played_by, str) else None,
                ),
                metadata={
                    "table_bindings_count": len(concept.table_bindings),
                    "mapped_standard_uris": std_uris,
                    "is_planned": len(concept.table_bindings) == 0,
                },
            )
            self._items[concept.concept_uri] = item
            self._label_to_id[concept.concept_name.strip().casefold()] = concept.concept_uri
            for std_uri in std_uris:
                self._register_std_link(std_uri, concept.concept_uri)

            for attr in concept.attributes:
                attr_id = f"{concept.concept_uri}#{attr.name}"
                std_prop = [attr.standard_property_uri] if attr.standard_property_uri else []
                attr_item = KnowledgeItem(
                    entry_id=attr_id,
                    label=attr.name,
                    entry_kind="datatype_property",
                    definition=f"Attribute {attr.name} of {concept.concept_name}",
                    aliases=[],
                    parent_ids=[],
                    neighborhood_links={},
                    replaces_uris=std_prop,
                    semantic_context=SemanticContext(
                        archetype="identifier" if attr.is_business_key else None,
                        domain_entity_uri=concept.concept_uri,
                        identified_entity_uri=(
                            concept.concept_uri if attr.is_business_key else None
                        ),
                        pii_category_hint=attr.pii_category,
                    ),
                    metadata={
                        "standard_property_uri": attr.standard_property_uri,
                        "is_business_key": attr.is_business_key,
                        "is_pii": attr.is_pii,
                        "enum_uri": attr.enum_uri,
                        "owning_concept_uri": concept.concept_uri,
                    },
                )
                self._items[attr_id] = attr_item
                self._register_std_link(attr.standard_property_uri, attr_id)

        for enum in ontology.enumerations:
            enum_item = KnowledgeItem(
                entry_id=enum.enum_uri,
                label=enum.enumeration_name,
                entry_kind="enumeration",
                definition=enum.description,
                aliases=list(enum.codes.keys()),
                parent_ids=[],
                neighborhood_links={},
                replaces_uris=[enum.standard_uri] if enum.standard_uri else [],
                semantic_context=SemanticContext(archetype="code_classifier"),
                metadata={
                    "codes": dict(enum.codes),
                    "standard_uri": enum.standard_uri,
                    "target_reference_table": enum.target_reference_table,
                },
            )
            self._items[enum.enum_uri] = enum_item
            self._label_to_id[enum.enumeration_name.strip().casefold()] = enum.enum_uri
            self._register_std_link(enum.standard_uri, enum.enum_uri)
            for code_key in enum.codes:
                norm_code = code_key.strip().casefold()
                if norm_code:
                    self._enum_code_index.setdefault(norm_code, []).append(enum.enum_uri)

        for rel in ontology.relationships:
            rel_item = KnowledgeItem(
                entry_id=rel.relationship_uri,
                label=rel.relationship_name,
                entry_kind="object_property",
                definition=rel.description,
                aliases=[],
                parent_ids=[],
                neighborhood_links={},
                replaces_uris=[rel.standard_property_uri] if rel.standard_property_uri else [],
                semantic_context=SemanticContext(
                    archetype="party_role" if rel.role_uri else None,
                    domain_entity_uri=rel.source_concept_uri,
                    target_entity_uri=rel.target_concept_uri,
                    role_uri=rel.role_uri,
                    played_by_uri=rel.played_by_uri,
                ),
                metadata={
                    "standard_property_uri": rel.standard_property_uri,
                    "target_link": rel.target_link,
                    "cardinality": rel.cardinality,
                },
            )
            self._items[rel.relationship_uri] = rel_item
            self._label_to_id[rel.relationship_name.strip().casefold()] = rel.relationship_uri
            self._register_std_link(rel.standard_property_uri, rel.relationship_uri)

    def _register_pinned(self, concept_uri: str | None, term: str) -> None:
        if concept_uri:
            if concept_uri in self._items:
                if term:
                    self.pinned_project_terms.setdefault(concept_uri, []).append(term)
            elif concept_uri in self.standard_to_project_uri:
                proj_uri = self.standard_to_project_uri[concept_uri]
                if term:
                    self.pinned_project_terms.setdefault(proj_uri, []).append(term)
            else:
                self.pinned_reference_uris.setdefault(concept_uri, [])
                if term:
                    self.pinned_reference_uris[concept_uri].append(term)
        elif term:
            matched_id = self._label_to_id.get(term.casefold())
            if matched_id:
                self.pinned_project_terms.setdefault(matched_id, []).append(term)

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        """Return Graph A metadata with empty anchors when ontology has no concepts."""
        has_concepts = bool(self._ontology is not None and len(self._ontology.concepts) > 0)
        return KnowledgeSourceMetadata(
            source_id=self._source_id,
            version=self._version,
            sha256=self._sha256,
            anchor_entry_kinds=frozenset({"class"}) if has_concepts else frozenset(),
            origin_kind="project_graph_a",
        )

    @property
    def calibration_params(self) -> CalibrationParams | None:
        """Return None so caller falls back to base source or analytic defaults."""
        return None

    def expand_query_terms(
        self,
        tokens: Sequence[str],
        *,
        context_text: str | None = None,
    ) -> list[str]:
        """Expand query tokens using pinned memory terms and matching steward notes."""
        tok_set = {t.lower() for t in tokens if t}
        if context_text:
            tok_set |= set(self._index.tokenize(context_text))
        expanded: list[str] = []
        for note_toks, _ in self._steward_tokens:
            if note_toks & tok_set:
                for nt in sorted(note_toks - tok_set - STOPWORDS):
                    expanded.append(nt)
        for uri, terms in self.pinned_project_terms.items():
            item = self._items.get(uri)
            if item and any(t.casefold() in tok_set for t in terms):
                expanded.extend(self._index.tokenize(item.label))
        return dedupe_str_list(expanded)

    def is_muted(self, entry_id: str, label: str) -> bool:
        """Return True if entry_id, label, or mapped standard/project URI is muted."""
        if entry_id in self.muted_uris or label.strip().casefold() in self.muted_terms:
            return True
        if (
            entry_id in self.standard_to_project_uri
            and self.standard_to_project_uri[entry_id] in self.muted_uris
        ):
            return True
        std_uris = self.project_to_standard_uris.get(entry_id, ())
        return any(std in self.muted_uris for std in std_uris)

    def _pinned_reference_hits(
        self,
        query: str,
        *,
        entry_kinds: AbstractSet[str] | None = None,
    ) -> list[RecallHit]:
        q_norm = query.strip().casefold()
        q_toks = set(self._index.tokenize(query))
        hits: list[RecallHit] = []
        if entry_kinds is not None and "class" not in entry_kinds:
            return hits
        for ref_uri, terms in self.pinned_reference_uris.items():
            if ref_uri in self.muted_uris:
                continue
            matched = any(
                t.casefold() == q_norm or bool(set(self._index.tokenize(t)) & q_toks)
                for t in terms
            )
            if matched:
                label = terms[0] if terms else ref_uri.rsplit("/", 1)[-1]
                hits.append(
                    RecallHit(
                        entry_id=ref_uri,
                        label=label,
                        entry_kind="class",
                        raw_score=_PINNED_MIN_SCORE,
                        origin_source_id=self._source_id,
                        origin_kind="reference_standard",
                    )
                )
        return hits

    def recall_by_text(
        self,
        query: str,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        top_k: int = 25,
    ) -> list[RecallHit]:
        """Recall committed Graph A items plus uncommitted pinned reference hits."""
        hits_by_id: dict[str, RecallHit] = {}
        for hit in self._index.match(query, top_k=max(top_k * 2, 25), entry_kinds=entry_kinds):
            if self.is_muted(hit.entry_id, hit.label):
                continue
            score = hit.raw_score
            if hit.entry_id in self.pinned_project_terms:
                score = min(_ONE_DEC, max(score, _PINNED_MIN_SCORE))
            hits_by_id[hit.entry_id] = hit.model_copy(update={"raw_score": score})

        for ref_hit in self._pinned_reference_hits(query, entry_kinds=entry_kinds):
            existing = hits_by_id.get(ref_hit.entry_id)
            if existing is None or ref_hit.raw_score > existing.raw_score:
                hits_by_id[ref_hit.entry_id] = ref_hit

        ordered = sorted(hits_by_id.values(), key=lambda h: (-h.raw_score, h.entry_id))
        return ordered[:top_k]

    def match_values_or_mentions(
        self,
        surface_forms: Sequence[str],
        *,
        pii_tags: Sequence[str] = (),
        context_hint: str | None = None,
        structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        """Match surface codes against committed Graph A enumerations and pinned terms."""
        _ = (pii_tags, structural_patterns)
        hits_by_id: dict[str, RecallHit] = {}
        for form in surface_forms:
            norm = str(form).strip().casefold()
            if not norm:
                continue
            for enum_uri in self._enum_code_index.get(norm, ()):
                item = self._items.get(enum_uri)
                if item is None or self.is_muted(enum_uri, item.label):
                    continue
                hits_by_id[enum_uri] = RecallHit(
                    entry_id=enum_uri,
                    label=item.label,
                    entry_kind=item.entry_kind,
                    raw_score=_CODE_MATCH_SCORE,
                    origin_source_id=self._source_id,
                    origin_kind="project_graph_a",
                )
        if context_hint:
            for hit in self.recall_by_text(context_hint, top_k=10):
                existing = hits_by_id.get(hit.entry_id)
                if existing is None or hit.raw_score > existing.raw_score:
                    hits_by_id[hit.entry_id] = hit
        ordered = sorted(hits_by_id.values(), key=lambda h: (-h.raw_score, h.entry_id))
        return ordered[:25]

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        """Fetch normalized KnowledgeItem for a committed Graph A entry."""
        return self._items.get(entry_id)

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        """Return nearest-first parent concept URIs inside the committed ontology."""
        ancestors: list[str] = []
        visited: set[str] = {entry_id}
        frontier = list(self._items[entry_id].parent_ids) if entry_id in self._items else []
        while frontier and len(ancestors) < max_depth:
            next_frontier: list[str] = []
            for pid in frontier:
                if pid in visited:
                    continue
                visited.add(pid)
                ancestors.append(pid)
                if len(ancestors) >= max_depth:
                    break
                p_item = self._items.get(pid)
                if p_item is not None:
                    next_frontier.extend(p_item.parent_ids)
            frontier = next_frontier
        return ancestors

    def evaluate_structured_candidates(
        self,
        column: ColumnEvidence,
        profile: InferredValueProfile,
        candidates: Sequence[RecallHit],
        *,
        table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        """Evaluate shape compatibility for committed Graph A candidates."""
        _ = (column, table_context)
        results: list[BackendReasoningResult] = []
        for hit in candidates:
            item = self._items.get(hit.entry_id)
            if item is None:
                continue
            score = 0.2
            detail = "Committed project concept match"
            if item.entry_kind == "enumeration" and profile.shape.looks_like_enum:
                codes = item.metadata.get("codes")
                obs = set(profile.shape.observed_enum_values)
                if isinstance(codes, Mapping) and obs and obs.issubset(codes.keys()):
                    score = 0.9
                    detail = "Observed codes match committed enumeration"
                else:
                    score = 0.5
                    detail = "Committed enumeration compatible with categorical shape"
            elif item.entry_kind == "datatype_property":
                score = 0.4
                detail = "Committed concept attribute"
            results.append(
                BackendReasoningResult(
                    entry_id=hit.entry_id,
                    shape_range_score=score,
                    hard_veto=False,
                    semantic_context=item.semantic_context,
                    support_detail=detail,
                )
            )
        return results

    def score(self, query: str, entry_id: str) -> Decimal:
        """Compute normalized lexical score in [0, 1] for query vs committed entry_id."""
        item = self._items.get(entry_id)
        if item is None or self.is_muted(entry_id, item.label):
            return _ZERO_DEC
        base = self._index.score(query, entry_id)
        if entry_id in self.pinned_project_terms and base > _ZERO_DEC:
            return min(_ONE_DEC, max(base, _PINNED_MIN_SCORE))
        return base
