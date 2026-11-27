"""Domain-neutral YAML GlossaryKnowledgeSource implementing Layer-2a SPI."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Final

from opendv.grounding.kernel.lexical import LexicalIndex
from opendv.grounding.kernel.pii_core import MASKED_PII_TOKEN, has_redacted_placeholder
from opendv.grounding.providers.glossary.loader import (
    GlossaryKind,
    GlossaryTermSpec,
    LoadedGlossary,
    load_glossary,
)
from opendv.models.grounding import (
    ArchetypeKind,
    ColumnEvidence,
    EntryKind,
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
from opendv.models.vault_ir import DecimalType

_ZERO_DEC: Final[Decimal] = Decimal("0.0000")
_FOUR_PLACES: Final[Decimal] = Decimal("0.0001")
_MAX_ALLOWED_DEC: Final[Decimal] = Decimal("0.9800")
_BASE_ALLOWED_DEC: Final[Decimal] = Decimal("0.7200")
_ALLOWED_SPAN_DEC: Final[Decimal] = Decimal("0.2600")
_BASE_PAT_DEC: Final[Decimal] = Decimal("0.6800")
_PAT_SPAN_DEC: Final[Decimal] = Decimal("0.2000")
_MATCH_RATIO_CUTOFF: Final[float] = 0.50

_KIND_MAP: Final[dict[GlossaryKind | None, tuple[EntryKind, ArchetypeKind | None]]] = {
    None: ("glossary_term", None),
    "entity": ("glossary_term", "entity"),
    "event": ("glossary_term", "event_occurrence"),
    "identifier": ("glossary_term", "identifier"),
    "attribute": ("glossary_term", "value_object"),
    "code": ("glossary_term", "code_classifier"),
    "relationship": ("object_property", None),
}


def _term_uri(term_id: str) -> str:
    if "://" in term_id or term_id.startswith("urn:"):
        return term_id
    return f"urn:opendv:glossary:{term_id}"


def _resolve_ref(ref: str | None, all_terms: Mapping[str, GlossaryTermSpec]) -> str | None:
    if ref is None:
        return None
    if ref in all_terms:
        return _term_uri(ref)
    return ref


class GlossaryKnowledgeSource:
    """Synchronous non-FIBO YAML Glossary KnowledgeSource."""

    def __init__(
        self,
        glossary_path: Path | str | None = None,
        *,
        glossary_bytes: bytes | None = None,
        glossary_text: str | None = None,
        source_id: str = "glossary",
        version: str = "1.0.0",
        config_path: Path | str | None = None,
    ) -> None:
        loaded: LoadedGlossary = load_glossary(
            glossary_path,
            glossary_bytes=glossary_bytes,
            glossary_text=glossary_text,
        )
        self._loaded = loaded
        self._source_id = source_id
        self._version = version
        self._items: dict[str, KnowledgeItem] = {}
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._allowed_sets: dict[str, frozenset[str]] = {}
        self._has_anchors = False

        terms = loaded.document.terms
        for term_id in sorted(terms):
            spec = terms[term_id]
            if spec.kind in ("entity", "event"):
                self._has_anchors = True
            entry_kind, archetype = _KIND_MAP[spec.kind]
            uri = _term_uri(term_id)
            parent_uri = _resolve_ref(spec.parent, terms)
            domain_uri = _resolve_ref(spec.domain_entity, terms)
            target_uri = _resolve_ref(spec.target_entity, terms)
            ident_uri = _resolve_ref(spec.identifies, terms)
            if spec.kind in ("entity", "event") and domain_uri is None:
                domain_uri = uri

            if term_id in loaded.compiled_patterns:
                self._patterns[uri] = loaded.compiled_patterns[term_id]
            if spec.allowed_values:
                self._allowed_sets[uri] = frozenset(
                    v.strip().casefold() for v in spec.allowed_values if v.strip()
                )

            self._items[uri] = KnowledgeItem(
                entry_id=uri,
                label=spec.label,
                entry_kind=entry_kind,
                definition=spec.definition,
                aliases=list(spec.aliases),
                parent_ids=[parent_uri] if parent_uri else [],
                neighborhood_links={
                    "domain": [domain_uri] if domain_uri else [],
                    "target": [target_uri] if target_uri else [],
                },
                semantic_context=SemanticContext(
                    archetype=archetype,
                    identified_entity_uri=ident_uri,
                    domain_entity_uri=domain_uri,
                    target_entity_uri=target_uri,
                    identifier_scope=spec.identifier_scope,
                    pii_category_hint=spec.pii_category_hint,
                ),
                metadata={
                    "term_id": term_id,
                    "kind": spec.kind,
                    "expected_type": spec.expected_type,
                    "allowed_values": list(spec.allowed_values),
                    "pattern": spec.pattern,
                    "has_surface_rule": bool(spec.allowed_values or spec.pattern),
                },
            )

        self._lexical = LexicalIndex(config_path=config_path, acronyms=None)
        self._lexical.index_items(
            list(self._items.values()),
            origin_source_id=self._source_id,
            origin_kind="reference_standard",
        )

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        return KnowledgeSourceMetadata(
            source_id=self._source_id,
            version=self._version,
            sha256=self._loaded.sha256,
            anchor_entry_kinds=frozenset({"glossary_term"}) if self._has_anchors else frozenset(),
            origin_kind="reference_standard",
        )

    @property
    def calibration_params(self) -> CalibrationParams | None:
        return None

    def expand_query_terms(
        self,
        tokens: Sequence[str],
        *,
        context_text: str | None = None,
    ) -> list[str]:
        _ = context_text
        return list(dict.fromkeys(t.lower() for t in tokens if t))

    def recall_by_text(
        self,
        query: str,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        top_k: int = 25,
    ) -> list[RecallHit]:
        return self._lexical.match(query, top_k=top_k, entry_kinds=entry_kinds)

    def match_values_or_mentions(
        self,
        surface_forms: Sequence[str],
        *,
        pii_tags: Sequence[str] = (),
        context_hint: str | None = None,
        structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        _ = (context_hint, structural_patterns)
        if pii_tags:
            return []
        cleaned = [
            str(v).strip()
            for v in surface_forms
            if v is not None
            and str(v).strip()
            and str(v).strip() != MASKED_PII_TOKEN
            and not has_redacted_placeholder(str(v))
        ]
        if not cleaned:
            return []

        n_clean = float(len(cleaned))
        hits_by_id: dict[str, RecallHit] = {}

        def _record(uri: str, score: Decimal) -> None:
            item = self._items[uri]
            prev = hits_by_id.get(uri)
            if prev is None or score > prev.raw_score:
                hits_by_id[uri] = RecallHit(
                    entry_id=uri,
                    label=item.label,
                    entry_kind=item.entry_kind,
                    raw_score=score,
                    origin_source_id=self._source_id,
                    origin_kind="reference_standard",
                )

        for uri, allowed in self._allowed_sets.items():
            matched = sum(1 for v in cleaned if v.casefold() in allowed)
            ratio = matched / n_clean
            if ratio >= _MATCH_RATIO_CUTOFF:
                sc = min(
                    _MAX_ALLOWED_DEC,
                    _BASE_ALLOWED_DEC + _ALLOWED_SPAN_DEC * Decimal(str(round(ratio, 4))),
                ).quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN)
                _record(uri, sc)

        for uri, pat in self._patterns.items():
            matched = sum(1 for v in cleaned if pat.fullmatch(v) is not None)
            ratio = matched / n_clean
            if ratio >= _MATCH_RATIO_CUTOFF:
                sc = (_BASE_PAT_DEC + _PAT_SPAN_DEC * Decimal(str(round(ratio, 4)))).quantize(
                    _FOUR_PLACES, rounding=ROUND_HALF_EVEN
                )
                _record(uri, sc)

        return sorted(hits_by_id.values(), key=lambda h: (-h.raw_score, h.entry_id))[:25]

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        return self._items.get(entry_id)

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        if max_depth <= 0 or entry_id not in self._items:
            return []
        visited: set[str] = {entry_id}
        ancestors: list[str] = []
        queue: deque[tuple[str, int]] = deque([(entry_id, 0)])
        while queue and len(ancestors) < max_depth:
            curr, depth = queue.popleft()
            item = self._items.get(curr)
            if item is None:
                continue
            for pid in item.parent_ids:
                if pid in visited:
                    continue
                visited.add(pid)
                ancestors.append(pid)
                if len(ancestors) >= max_depth:
                    return ancestors
                if depth + 1 < max_depth:
                    queue.append((pid, depth + 1))
        return ancestors

    def evaluate_structured_candidates(
        self,
        column: ColumnEvidence,
        profile: InferredValueProfile,
        candidates: Sequence[RecallHit],
        *,
        table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        _ = table_context
        decl = profile.declared_logical_type
        inf = profile.inferred_logical_type
        is_temporal = decl in ("DATE32", "TIMESTAMP_UTC_US") or inf in (
            "DATE32", "TIMESTAMP_UTC_US",
        )
        is_bool = decl == "BOOLEAN" or inf == "BOOLEAN"
        is_num = (not is_temporal and not is_bool) and (
            decl in ("INT32", "INT64")
            or isinstance(decl, DecimalType)
            or inf in ("INT32", "INT64")
            or isinstance(inf, DecimalType)
        )
        is_float = isinstance(decl, DecimalType) or isinstance(inf, DecimalType)

        samples = (
            list(profile.shape.observed_enum_values)
            if profile.shape.observed_enum_values
            else [
                str(v).strip()
                for v in column.sample_values
                if v is not None
                and str(v).strip()
                and str(v).strip() != MASKED_PII_TOKEN
                and not has_redacted_placeholder(str(v))
            ]
        )

        results: list[BackendReasoningResult] = []
        for hit in candidates:
            item = self._items.get(hit.entry_id)
            if item is None:
                continue
            exp_type = item.metadata.get("expected_type")
            veto_reason: str | None = None
            if exp_type in ("date", "timestamp") and (is_num or is_bool):
                veto_reason = "expected_temporal_contradicts_numeric_or_boolean"
            elif exp_type == "boolean" and (is_num or is_temporal):
                veto_reason = "expected_boolean_contradicts_non_boolean"
            elif exp_type in ("integer", "number", "decimal") and (is_temporal or is_bool):
                veto_reason = "expected_numeric_contradicts_temporal_or_boolean"
            elif exp_type == "integer" and is_float:
                veto_reason = "expected_integer_contradicts_decimal"

            if veto_reason is not None:
                results.append(
                    BackendReasoningResult(
                        entry_id=hit.entry_id,
                        shape_range_score=-1.0,
                        hard_veto=True,
                        veto_reason=veto_reason,
                        semantic_context=item.semantic_context,
                    )
                )
                continue

            sr_score = 0.2
            notes: list[str] = []
            if exp_type in ("date", "timestamp") and is_temporal:
                sr_score = 0.9
                notes.append("expected_temporal_match")
            elif exp_type == "boolean" and is_bool:
                sr_score = 0.9
                notes.append("expected_boolean_match")
            elif exp_type in ("integer", "number", "decimal") and is_num:
                sr_score = 0.9
                notes.append("expected_numeric_match")
            elif (
                exp_type in ("number", "decimal", "integer")
                and decl == "UTF8_STRING"
                and profile.shape.numeric_min is None
            ):
                sr_score = -0.8
                notes.append("expected_numeric_string_mismatch")

            if samples and not profile.pii_tags:
                n_s = float(len(samples))
                allowed = self._allowed_sets.get(hit.entry_id)
                pat = self._patterns.get(hit.entry_id)
                if allowed is not None:
                    ratio = sum(1 for v in samples if v.casefold() in allowed) / n_s
                    if ratio >= _MATCH_RATIO_CUTOFF:
                        sr_score = max(sr_score, 0.95)
                        notes.append("allowed_values_match")
                    else:
                        sr_score = min(sr_score, -0.8)
                        notes.append("allowed_values_mismatch")
                elif pat is not None:
                    ratio = sum(1 for v in samples if pat.fullmatch(v) is not None) / n_s
                    if ratio >= _MATCH_RATIO_CUTOFF:
                        sr_score = max(sr_score, 0.85)
                        notes.append("pattern_match")
                    else:
                        sr_score = min(sr_score, -0.8)
                        notes.append("pattern_mismatch")

            results.append(
                BackendReasoningResult(
                    entry_id=hit.entry_id,
                    shape_range_score=sr_score,
                    hard_veto=False,
                    semantic_context=item.semantic_context,
                    support_detail=",".join(notes) or "glossary_term_match",
                )
            )
        return results

    def score(self, query: str, entry_id: str) -> Decimal:
        if entry_id not in self._items:
            return _ZERO_DEC
        return self._lexical.score(query, entry_id)


__all__ = ["GlossaryKnowledgeSource"]
