"""ADR-008 Okapi BM25 semantic concept matcher with Decimal(28) arithmetic and FIBO integrity."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any, Final, Literal

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.wire_dtos_discovery import FiboMatchSpec, FiboQueryReq, FiboQueryResp
from opendv.service._bm25_config import (
    Bm25ConceptInput,
    Bm25MatcherConfig,
    Bm25ScoredHit,
    Bm25WeightsConfig,
    coerce_concept,
    concepts_from_fibo_loader,
    load_bm25_matcher_config,
)
from opendv.service.bm25_tokenizer import STOPWORDS, tokenize_concept_text
from opendv.sources.fibo.loader import FiboReferenceOntologyLoader, get_default_fibo_loader

_ONE: Final[Decimal] = Decimal("1.0")
_HALF: Final[Decimal] = Decimal("0.5")
_ZERO: Final[Decimal] = Decimal("0.0")
_FOUR_DECIMALS: Final[Decimal] = Decimal("0.0001")


class _IndexedConcept:
    __slots__ = (
        "all_tokens_set",
        "concept",
        "def_counts",
        "def_len",
        "def_tokens",
        "name_counts",
        "name_len",
        "name_or_syn_tokens_set",
        "name_tokens",
        "self_name_raw_score",
        "syn_counts",
        "syn_len",
        "syn_tokens",
        "syn_tokens_list",
    )

    def __init__(self, concept: Bm25ConceptInput) -> None:
        self.concept = concept
        self.name_tokens = tokenize_concept_text(concept.concept_name)
        self.syn_tokens_list = [tokenize_concept_text(s) for s in concept.synonyms]
        syn_toks: list[str] = []
        for toks in self.syn_tokens_list:
            syn_toks.extend(toks)
        self.syn_tokens = syn_toks
        self.def_tokens = tokenize_concept_text(concept.definition)

        self.name_counts = Counter(self.name_tokens)
        self.syn_counts = Counter(self.syn_tokens)
        self.def_counts = Counter(self.def_tokens)

        self.name_len = Decimal(len(self.name_tokens))
        self.syn_len = Decimal(len(self.syn_tokens))
        self.def_len = Decimal(len(self.def_tokens))

        self.name_or_syn_tokens_set = set(self.name_tokens) | set(self.syn_tokens)
        self.all_tokens_set = self.name_or_syn_tokens_set | set(self.def_tokens)
        self.self_name_raw_score = _ZERO


class Bm25ConceptMatcher:
    """Normative multi-field Okapi BM25 concept matcher (`bm25_fibo_v1`)."""

    def __init__(
        self,
        concepts: Sequence[Bm25ConceptInput | Mapping[str, Any]] | None = None,
        *,
        loader: FiboReferenceOntologyLoader | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self.config: Bm25MatcherConfig = load_bm25_matcher_config(config_path)
        self._k1: Decimal = Decimal(self.config.k1)
        self._b: Decimal = Decimal(self.config.b)
        self._w_name: Decimal = Decimal(self.config.weights.w_name)
        self._w_syn: Decimal = Decimal(self.config.weights.w_syn)
        self._w_def: Decimal = Decimal(self.config.weights.w_def)
        self._hit_gate_cap: Decimal = Decimal(self.config.hit_gate_cap)
        self._grounding_cutoff: Decimal = Decimal(self.config.grounding_threshold)

        self._concepts_by_uri: dict[str, _IndexedConcept] = {}
        self._concepts_list: list[_IndexedConcept] = []
        self._num_docs = _ZERO
        self._avgdl_name = _ZERO
        self._avgdl_syn = _ZERO
        self._avgdl_def = _ZERO
        self._df: Counter[str] = Counter()
        self._idf_cache: dict[str, Decimal] = {}

        if concepts is not None:
            self.index_concepts(concepts)
        else:
            active_loader = loader if loader is not None else get_default_fibo_loader()
            self.index_concepts(concepts_from_fibo_loader(active_loader))

    def quantize_score(self, norm_score: Decimal | str) -> Decimal:
        """Quantize a normalized Decimal score to 4 decimal places via ROUND_HALF_EVEN."""
        dec = norm_score if isinstance(norm_score, Decimal) else Decimal(norm_score)
        return dec.quantize(_FOUR_DECIMALS, rounding=ROUND_HALF_EVEN) + _ZERO

    def apply_hit_gate(self, score_val: Decimal | str, *, has_name_or_syn_hit: bool) -> Decimal:
        """Cap definition-only scores >= grounding cutoff at hit_gate_cap (0.6400)."""
        quantized = self.quantize_score(score_val)
        if quantized >= self._grounding_cutoff and not has_name_or_syn_hit:
            return min(quantized, self._hit_gate_cap)
        return quantized

    def is_grounded_score(self, quantized: Decimal | float) -> bool:
        """Return True if quantized score meets or exceeds grounding cutoff."""
        dec = quantized if isinstance(quantized, Decimal) else Decimal(str(quantized))
        return dec >= self._grounding_cutoff

    def lookup_concept(self, uri: str) -> Bm25ConceptInput | None:
        idx_c = self._concepts_by_uri.get(uri)
        return idx_c.concept if idx_c is not None else None

    def index_concepts(self, concepts: Sequence[Bm25ConceptInput | Mapping[str, Any]]) -> None:
        """Index or re-index reference concepts atomically under Decimal(28) precision."""
        with localcontext() as ctx:
            ctx.prec = 28
            unique_concepts: dict[str, Bm25ConceptInput] = {}
            for raw in concepts:
                c = coerce_concept(raw)
                unique_concepts[c.concept_uri] = c
            deduped = list(unique_concepts.values())

            n = len(deduped)
            num_docs = Decimal(n)
            if n == 0:
                self._concepts_by_uri = {}
                self._concepts_list = []
                self._df = Counter()
                self._idf_cache = {}
                self._num_docs = _ZERO
                self._avgdl_name = _ZERO
                self._avgdl_syn = _ZERO
                self._avgdl_def = _ZERO
                return

            new_by_uri: dict[str, _IndexedConcept] = {}
            new_list: list[_IndexedConcept] = []
            new_df: Counter[str] = Counter()
            total_name_len = _ZERO
            total_syn_len = _ZERO
            total_def_len = _ZERO

            for c in deduped:
                idx_c = _IndexedConcept(c)
                new_by_uri[c.concept_uri] = idx_c
                new_list.append(idx_c)
                total_name_len += idx_c.name_len
                total_syn_len += idx_c.syn_len
                total_def_len += idx_c.def_len
                for t in idx_c.all_tokens_set:
                    new_df[t] += 1

            self._num_docs = num_docs
            self._avgdl_name = total_name_len / num_docs
            self._avgdl_syn = total_syn_len / num_docs
            self._avgdl_def = total_def_len / num_docs
            self._df = new_df
            self._idf_cache = {}

            for idx_c in new_list:
                idx_c.self_name_raw_score = (
                    self._compute_raw_score(idx_c, idx_c.name_tokens)
                    if idx_c.name_tokens
                    else _ZERO
                )

            self._concepts_by_uri = new_by_uri
            self._concepts_list = new_list

    def _get_idf(self, token: str) -> Decimal:
        cached = self._idf_cache.get(token)
        if cached is not None:
            return cached
        df = Decimal(self._df.get(token, 0))
        ratio = (self._num_docs - df + _HALF) / (df + _HALF)
        val = (_ONE + ratio).ln()
        self._idf_cache[token] = val
        return val

    def _compute_tf(self, count: int, doc_len: Decimal, avgdl: Decimal) -> Decimal:
        if count <= 0 or avgdl <= _ZERO:
            return _ZERO
        f = Decimal(count)
        effective_avgdl = max(avgdl, _ONE)
        len_ratio = doc_len / effective_avgdl
        denom = f + self._k1 * (_ONE - self._b + self._b * len_ratio)
        if denom <= _ZERO:
            return _ZERO
        return (f * (self._k1 + _ONE)) / denom

    def _compute_raw_score(self, concept: _IndexedConcept, query_tokens: list[str]) -> Decimal:
        raw_score = _ZERO
        for t in dict.fromkeys(query_tokens):
            if t not in self._df:
                continue
            idf = self._get_idf(t)
            tf_name = self._compute_tf(
                concept.name_counts.get(t, 0), concept.name_len, self._avgdl_name
            )
            tf_syn = self._compute_tf(
                concept.syn_counts.get(t, 0), concept.syn_len, self._avgdl_syn
            )
            tf_def = self._compute_tf(
                concept.def_counts.get(t, 0), concept.def_len, self._avgdl_def
            )
            field_sum = (self._w_name * tf_name) + (self._w_syn * tf_syn) + (self._w_def * tf_def)
            raw_score += idf * field_sum
        return raw_score

    def _compute_query_self_score(self, query_tokens: list[str]) -> Decimal:
        if not query_tokens or self._avgdl_name <= _ZERO:
            return _ZERO
        q_counts = Counter(query_tokens)
        q_len = Decimal(len(query_tokens))
        self_score = _ZERO
        for t, cnt in q_counts.items():
            idf = self._get_idf(t)
            tf = self._compute_tf(cnt, q_len, self._avgdl_name)
            self_score += idf * self._w_name * tf
        return self_score

    def _score_concept(
        self,
        idx_c: _IndexedConcept,
        query_tokens: list[str],
        query_self_score: Decimal,
        query: str,
    ) -> Bm25ScoredHit:
        c = idx_c.concept
        if not query_tokens:
            return Bm25ScoredHit(
                concept_uri=c.concept_uri,
                concept_name=c.concept_name,
                module=c.module,
                raw_score=0.0,
                normalized_score=0.0,
                matched_term=query,
                definition=c.definition,
                parent_uri=c.parent_uri,
            )

        raw_score = self._compute_raw_score(idx_c, query_tokens)
        if raw_score <= _ZERO:
            return Bm25ScoredHit(
                concept_uri=c.concept_uri,
                concept_name=c.concept_name,
                module=c.module,
                raw_score=0.0,
                normalized_score=0.0,
                matched_term=query,
                definition=c.definition,
                parent_uri=c.parent_uri,
            )

        denom = max(idx_c.self_name_raw_score, query_self_score)
        norm_score = _ZERO if denom <= _ZERO else min(_ONE, raw_score / denom)
        has_name_or_syn_hit = any(
            t in idx_c.name_or_syn_tokens_set and t not in STOPWORDS for t in query_tokens
        )
        quantized = self.apply_hit_gate(norm_score, has_name_or_syn_hit=has_name_or_syn_hit)

        return Bm25ScoredHit(
            concept_uri=c.concept_uri,
            concept_name=c.concept_name,
            module=c.module,
            raw_score=float(raw_score),
            normalized_score=float(quantized),
            matched_term=query,
            definition=c.definition,
            parent_uri=c.parent_uri,
            raw_decimal=raw_score,
            quantized_decimal=quantized,
        )

    def match(
        self,
        query: str,
        top_k: int = 5,
        module_filter: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[Bm25ScoredHit]:
        """Rank and return top-k scored reference concepts matching query."""
        if not math.isfinite(min_score) or min_score < 0.0 or min_score > 1.0:
            raise OpenDVError(
                f"min_score must be a finite float in [0.0, 1.0], got {min_score}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if not self._concepts_list or top_k <= 0:
            return []
        query_tokens = tokenize_concept_text(query)
        if not query_tokens:
            return []

        with localcontext() as ctx:
            ctx.prec = 28
            query_self_score = self._compute_query_self_score(query_tokens)
            matches: list[Bm25ScoredHit] = []
            for idx_c in self._concepts_list:
                if module_filter is not None and idx_c.concept.module not in module_filter:
                    continue
                if not idx_c.all_tokens_set.intersection(query_tokens):
                    continue
                hit = self._score_concept(idx_c, query_tokens, query_self_score, query)
                if hit.normalized_score >= min_score and hit.raw_score > 0.0:
                    matches.append(hit)

            matches.sort(key=lambda m: (-m.normalized_score, -m.raw_score, m.concept_uri))
            return matches[:top_k]

    def score(self, query: str, concept_uri: str) -> Bm25ScoredHit | None:
        """Compute match score of query against a specific indexed concept_uri."""
        idx_c = self._concepts_by_uri.get(concept_uri)
        if idx_c is None:
            return None
        with localcontext() as ctx:
            ctx.prec = 28
            query_tokens = tokenize_concept_text(query)
            query_self_score = self._compute_query_self_score(query_tokens)
            return self._score_concept(idx_c, query_tokens, query_self_score, query)

    def query(self, req: FiboQueryReq) -> FiboQueryResp:
        """Execute Ask FIBO BM25 query and map matches to FiboQueryResp DTO."""
        hits = self.match(req.query, top_k=req.top_k)
        q_norm = req.query.strip().casefold()
        q_tokens = tokenize_concept_text(req.query)

        specs: list[FiboMatchSpec] = []
        for hit in hits:
            idx_c = self._concepts_by_uri[hit.concept_uri]
            label_norm = idx_c.concept.concept_name.strip().casefold()
            syn_norms = {s.strip().casefold() for s in idx_c.concept.synonyms}
            tok_match = bool(q_tokens) and (
                q_tokens == idx_c.name_tokens or q_tokens in idx_c.syn_tokens_list
            )
            exact_label_or_syn = q_norm == label_norm or q_norm in syn_norms or tok_match
            suggested: Literal["PATH_A1", "PATH_A2"] = (
                "PATH_A1"
                if hit.quantized_decimal >= self._grounding_cutoff and exact_label_or_syn
                else "PATH_A2"
            )
            raw_fmt = str(hit.raw_decimal.quantize(_FOUR_DECIMALS, rounding=ROUND_HALF_EVEN))
            specs.append(
                FiboMatchSpec(
                    concept_uri=hit.concept_uri,
                    label=hit.concept_name,
                    parent_uri=hit.parent_uri,
                    normalized_score=f"{hit.quantized_decimal:.4f}",
                    raw_bm25=raw_fmt,
                    suggested_path=suggested,
                    definition=hit.definition,
                )
            )
        return FiboQueryResp(matches=specs)


__all__ = [
    "Bm25ConceptInput",
    "Bm25ConceptMatcher",
    "Bm25MatcherConfig",
    "Bm25ScoredHit",
    "Bm25WeightsConfig",
    "load_bm25_matcher_config",
]
