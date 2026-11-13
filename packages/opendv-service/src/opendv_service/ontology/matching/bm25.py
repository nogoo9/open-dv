"""Okapi BM25 semantic concept matcher (Task 5A.1 / Issue #211).

Conforms to docs/19 §4.4, docs/21 Task 5A.1, and ADR-008:
- Pinned Robertson BM25 formulation with Decimal(28) arithmetic.
- Pinned parameters: k1=1.5, b=0.75, w_name=2.5, w_syn=2.0, w_def=1.0.
- Symmetric normalization with pseudo-document self-scoring.
- ROUND_HALF_EVEN quantization to 4 decimal places.
- Definition-only match hit gate capping scores at 0.6400.
- Deterministic sorting by (-normalized_score, -raw_score, concept_uri).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from opendv_contracts.dtos.matching import ReferenceConcept, ScoredConceptMatch
from opendv_service.ontology.matching.tokenization import STOPWORDS, tokenize_concept_text

# Normative BM25 Hyperparameters & Field Weights (docs/19 §4.4.2)
K1: Final[Decimal] = Decimal("1.5")
B: Final[Decimal] = Decimal("0.75")
W_NAME: Final[Decimal] = Decimal("2.5")
W_SYN: Final[Decimal] = Decimal("2.0")
W_DEF: Final[Decimal] = Decimal("1.0")

ONE: Final[Decimal] = Decimal("1.0")
HALF: Final[Decimal] = Decimal("0.5")
ZERO: Final[Decimal] = Decimal("0.0")
FOUR_DECIMALS: Final[Decimal] = Decimal("0.0001")
HIT_GATE_CAP: Final[Decimal] = Decimal("0.6400")
GROUNDING_THRESHOLD: Final[Decimal] = Decimal("0.6500")


class ConceptMatcher(ABC):
    """Abstract SPI for semantic reference concept matchers."""

    @abstractmethod
    def index_concepts(self, concepts: list[ReferenceConcept]) -> None:
        """Index a collection of reference concepts into the matcher."""
        ...

    @abstractmethod
    def lookup_concept(self, uri: str) -> ReferenceConcept | None:
        """Retrieve indexed reference concept by its URI, or None if not found."""
        ...

    @abstractmethod
    def match(
        self,
        query: str,
        top_k: int = 5,
        module_filter: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[ScoredConceptMatch]:
        """Match a query string against the indexed reference concepts."""
        ...

    @abstractmethod
    def score(self, query: str, concept_uri: str) -> ScoredConceptMatch | None:
        """Compute the match score of a query against a specific concept URI."""
        ...


class _IndexedConcept:
    """Internal indexed representation of a single ReferenceConcept."""

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
    )

    def __init__(self, concept: ReferenceConcept) -> None:
        self.concept = concept
        self.name_tokens = tokenize_concept_text(concept.concept_name)

        syn_toks: list[str] = []
        for s in concept.synonyms:
            syn_toks.extend(tokenize_concept_text(s))
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
        self.self_name_raw_score = ZERO


class Bm25ConceptMatcher(ConceptMatcher):
    """Normative multi-field Okapi BM25 concept matcher (`bm25_fibo_v1` v1.0.0)."""

    def __init__(self, concepts: list[ReferenceConcept] | None = None) -> None:
        self._concepts_by_uri: dict[str, _IndexedConcept] = {}
        self._concepts_list: list[_IndexedConcept] = []
        self._num_docs = Decimal("0")
        self._avgdl_name = ZERO
        self._avgdl_syn = ZERO
        self._avgdl_def = ZERO
        self._df: Counter[str] = Counter()
        self._idf_cache: dict[str, Decimal] = {}

        if concepts:
            self.index_concepts(concepts)

    def lookup_concept(self, uri: str) -> ReferenceConcept | None:
        """Retrieve indexed reference concept by its URI, or None if not found."""
        idx_c = self._concepts_by_uri.get(uri)
        return idx_c.concept if idx_c is not None else None

    def index_concepts(self, concepts: list[ReferenceConcept]) -> None:
        """Index or re-index the collection of reference concepts atomically."""
        with localcontext() as ctx:
            ctx.prec = 28

            # Deduplicate concepts by URI deterministically (last entry wins)
            unique_concepts: dict[str, ReferenceConcept] = {}
            for c in concepts:
                unique_concepts[c.concept_uri] = c
            deduped = list(unique_concepts.values())

            n = len(deduped)
            num_docs = Decimal(n)
            if n == 0:
                self._concepts_by_uri = {}
                self._concepts_list = []
                self._df = Counter()
                self._idf_cache = {}
                self._num_docs = ZERO
                self._avgdl_name = ZERO
                self._avgdl_syn = ZERO
                self._avgdl_def = ZERO
                return

            new_concepts_by_uri: dict[str, _IndexedConcept] = {}
            new_concepts_list: list[_IndexedConcept] = []
            new_df: Counter[str] = Counter()

            total_name_len = ZERO
            total_syn_len = ZERO
            total_def_len = ZERO

            for c in deduped:
                idx_c = _IndexedConcept(c)
                new_concepts_by_uri[c.concept_uri] = idx_c
                new_concepts_list.append(idx_c)

                total_name_len += idx_c.name_len
                total_syn_len += idx_c.syn_len
                total_def_len += idx_c.def_len

                for t in idx_c.all_tokens_set:
                    new_df[t] += 1

            new_avgdl_name = total_name_len / num_docs
            new_avgdl_syn = total_syn_len / num_docs
            new_avgdl_def = total_def_len / num_docs

            self._num_docs = num_docs
            self._avgdl_name = new_avgdl_name
            self._avgdl_syn = new_avgdl_syn
            self._avgdl_def = new_avgdl_def
            self._df = new_df
            self._idf_cache = {}

            # Precompute self-score Score_raw(C, C_name) for each indexed concept
            for idx_c in new_concepts_list:
                if idx_c.name_tokens:
                    idx_c.self_name_raw_score = self._compute_raw_score(idx_c, idx_c.name_tokens)
                else:
                    idx_c.self_name_raw_score = ZERO

            # Atomic swap
            self._concepts_by_uri = new_concepts_by_uri
            self._concepts_list = new_concepts_list

    def _get_idf(self, token: str) -> Decimal:
        """Compute or retrieve cached Robertson IDF for a token."""
        if token in self._idf_cache:
            return self._idf_cache[token]

        df = Decimal(self._df.get(token, 0))
        # Robertson IDF: ln(1 + (N - df + 0.5) / (df + 0.5))
        numerator = self._num_docs - df + HALF
        denominator = df + HALF
        ratio = numerator / denominator
        val = (ONE + ratio).ln()
        self._idf_cache[token] = val
        return val

    def _compute_tf(
        self,
        count: int,
        doc_len: Decimal,
        avgdl: Decimal,
    ) -> Decimal:
        """Compute Robertson BM25 term frequency contribution for a field."""
        if count <= 0 or avgdl <= ZERO:
            return ZERO

        f = Decimal(count)
        # avgdl is clamped to at least 1.0 per docs/19 §4.4.2
        effective_avgdl = max(avgdl, ONE)
        len_ratio = doc_len / effective_avgdl
        denom = f + K1 * (ONE - B + B * len_ratio)
        if denom <= ZERO:
            return ZERO
        return (f * (K1 + ONE)) / denom

    def _compute_raw_score(
        self,
        concept: _IndexedConcept,
        query_tokens: list[str],
    ) -> Decimal:
        """Compute unnormalized multi-field raw BM25 score Score_raw(C, Q)."""
        raw_score = ZERO
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

            field_sum = (W_NAME * tf_name) + (W_SYN * tf_syn) + (W_DEF * tf_def)
            raw_score += idf * field_sum

        return raw_score

    def _compute_query_self_score(self, query_tokens: list[str]) -> Decimal:
        """Evaluate query Q as pseudo-document on name field Score_raw(Q, Q)."""
        if not query_tokens or self._avgdl_name <= ZERO:
            return ZERO

        q_counts = Counter(query_tokens)
        q_len = Decimal(len(query_tokens))
        self_score = ZERO

        for t, cnt in q_counts.items():
            idf = self._get_idf(t)
            tf = self._compute_tf(cnt, q_len, self._avgdl_name)
            self_score += idf * W_NAME * tf

        return self_score

    def _score_concept(
        self,
        idx_c: _IndexedConcept,
        query_tokens: list[str],
        query_self_score: Decimal,
        query: str,
    ) -> ScoredConceptMatch:
        """Compute complete ScoredConceptMatch for a concept and tokenized query."""
        if not query_tokens:
            return ScoredConceptMatch(
                concept_uri=idx_c.concept.concept_uri,
                concept_name=idx_c.concept.concept_name,
                module=idx_c.concept.module,
                raw_score=0.0,
                normalized_score=0.0,
                matched_term=query,
                definition=idx_c.concept.definition,
            )

        raw_score = self._compute_raw_score(idx_c, query_tokens)
        if raw_score <= ZERO:
            return ScoredConceptMatch(
                concept_uri=idx_c.concept.concept_uri,
                concept_name=idx_c.concept.concept_name,
                module=idx_c.concept.module,
                raw_score=0.0,
                normalized_score=0.0,
                matched_term=query,
                definition=idx_c.concept.definition,
            )

        # Symmetric Normalization
        denom = max(idx_c.self_name_raw_score, query_self_score)
        norm_score = ZERO if denom <= ZERO else min(ONE, raw_score / denom)

        # 4-decimal Quantization via ROUND_HALF_EVEN coerced to positive zero
        quantized = norm_score.quantize(FOUR_DECIMALS, rounding=ROUND_HALF_EVEN) + ZERO

        # Name/Synonym Hit Gate Enforcement (docs/19 §4.4.3):
        # If score >= 0.6500 but no non-stopword token in query matches concept_name or
        # synonyms, cap at 0.6400.
        if quantized >= GROUNDING_THRESHOLD:
            has_name_or_syn_hit = any(
                t in idx_c.name_or_syn_tokens_set and t not in STOPWORDS for t in query_tokens
            )
            if not has_name_or_syn_hit:
                quantized = min(quantized, HIT_GATE_CAP)

        return ScoredConceptMatch(
            concept_uri=idx_c.concept.concept_uri,
            concept_name=idx_c.concept.concept_name,
            module=idx_c.concept.module,
            raw_score=float(raw_score),
            normalized_score=float(quantized),
            matched_term=query,
            definition=idx_c.concept.definition,
        )

    def match(
        self,
        query: str,
        top_k: int = 5,
        module_filter: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[ScoredConceptMatch]:
        """Rank and return top-k scored reference concepts matching the query."""
        if not math.isfinite(min_score) or min_score < 0.0 or min_score > 1.0:
            raise ValueError(f"min_score must be a finite float in [0.0, 1.0], got {min_score}")

        if not self._concepts_list or top_k <= 0:
            return []

        query_tokens = tokenize_concept_text(query)
        if not query_tokens:
            return []

        with localcontext() as ctx:
            ctx.prec = 28

            query_self_score = self._compute_query_self_score(query_tokens)
            matches: list[ScoredConceptMatch] = []

            for idx_c in self._concepts_list:
                if module_filter is not None and idx_c.concept.module not in module_filter:
                    continue

                if not idx_c.all_tokens_set.intersection(query_tokens):
                    continue

                m = self._score_concept(idx_c, query_tokens, query_self_score, query)
                if m.normalized_score >= min_score and m.raw_score > 0.0:
                    matches.append(m)

            matches.sort(key=lambda m: (-m.normalized_score, -m.raw_score, m.concept_uri))
            return matches[:top_k]

    def score(self, query: str, concept_uri: str) -> ScoredConceptMatch | None:
        """Compute the match score of a query against a specific concept URI."""
        idx_c = self._concepts_by_uri.get(concept_uri)
        if idx_c is None:
            return None

        with localcontext() as ctx:
            ctx.prec = 28
            query_tokens = tokenize_concept_text(query)
            query_self_score = self._compute_query_self_score(query_tokens)
            return self._score_concept(idx_c, query_tokens, query_self_score, query)
