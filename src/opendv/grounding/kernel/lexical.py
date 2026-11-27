"""Domain-neutral Decimal(28) Okapi BM25 LexicalIndex with class-scoped IDF statistics."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding_spi import KnowledgeItem, RecallHit

_ONE: Final[Decimal] = Decimal("1.0")
_HALF: Final[Decimal] = Decimal("0.5")
_ZERO: Final[Decimal] = Decimal("0.0")
_FOUR_DECIMALS: Final[Decimal] = Decimal("0.0001")

_SEC_CONTEXT_TRIGGERS: Final[frozenset[str]] = frozenset(
    {"filing", "cik", "13f", "10k", "edgar", "regulator"}
)
_SEC_EXCLUSION_TOKENS: Final[frozenset[str]] = frozenset(
    {"second", "seconds", "sector", "security_id"}
)

STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the",
        "is",
        "at",
        "which",
        "on",
        "of",
        "and",
        "or",
        "in",
        "to",
        "a",
        "an",
        "for",
        "with",
        "as",
        "by",
        "from",
        "that",
        "this",
        "it",
        "be",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "class",
        "concept",
        "entity",
        "type",
        "term",
        "model",
    }
)


class LexicalWeightsConfig(StrictModel):
    """Per-field BM25 weights matching schemas/v2/bm25_fibo_v1.schema.json."""

    w_name: str
    w_syn: str
    w_def: str


class LexicalMatcherConfig(StrictModel):
    """Pinned BM25 matcher configuration loaded from schemas/v2/matchers/bm25_fibo_v1.json."""

    schema_url: str | None = Field(default=None, alias="$schema")
    matcher_id: str
    version: str
    k1: str
    b: str
    weights: LexicalWeightsConfig
    hit_gate_cap: str
    grounding_threshold: str


def _find_default_bm25_config_path() -> Path | None:
    current: Path | None = Path(__file__).resolve().parent
    for _ in range(8):
        if current is None:
            break
        candidate = current / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_lexical_matcher_config(config_path: Path | str | None = None) -> LexicalMatcherConfig:
    """Load and validate LexicalMatcherConfig from schemas/v2/matchers/bm25_fibo_v1.json."""
    resolved = Path(config_path) if config_path is not None else _find_default_bm25_config_path()
    if resolved is not None:
        if not resolved.is_file():
            raise OpenDVError(
                f"BM25 matcher config not found: {resolved}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
            return LexicalMatcherConfig.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise OpenDVError(
                f"Invalid BM25 matcher config at '{resolved}': {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc

    return LexicalMatcherConfig(
        matcher_id="bm25_fibo_v1",
        version="1.0.0",
        k1="1.5",
        b="0.75",
        weights=LexicalWeightsConfig(w_name="2.5", w_syn="2.0", w_def="1.0"),
        hit_gate_cap="0.6400",
        grounding_threshold="0.6500",
    )


class _IndexedItem:
    __slots__ = (
        "all_tokens_set",
        "def_counts",
        "def_len",
        "def_tokens",
        "item",
        "label_norm",
        "name_counts",
        "name_len",
        "name_or_syn_tokens_set",
        "name_tokens",
        "origin_kind",
        "origin_source_id",
        "self_name_raw_score",
        "syn_counts",
        "syn_len",
        "syn_norms",
        "syn_tokens",
        "syn_tokens_list",
    )

    def __init__(
        self,
        item: KnowledgeItem,
        *,
        name_tokens: list[str],
        syn_tokens_list: list[list[str]],
        def_tokens: list[str],
        origin_source_id: str,
        origin_kind: Literal["project_graph_a", "reference_standard"],
    ) -> None:
        self.item = item
        self.origin_source_id = origin_source_id
        self.origin_kind = origin_kind
        self.name_tokens = name_tokens
        self.syn_tokens_list = syn_tokens_list
        syn_toks: list[str] = []
        for toks in syn_tokens_list:
            syn_toks.extend(toks)
        self.syn_tokens = syn_toks
        self.def_tokens = def_tokens

        self.label_norm = item.label.strip().casefold()
        self.syn_norms = frozenset(a.strip().casefold() for a in item.aliases)

        self.name_counts = Counter(self.name_tokens)
        self.syn_counts = Counter(self.syn_tokens)
        self.def_counts = Counter(self.def_tokens)

        self.name_len = Decimal(len(self.name_tokens))
        self.syn_len = Decimal(len(self.syn_tokens))
        self.def_len = Decimal(len(self.def_tokens))

        self.name_or_syn_tokens_set = set(self.name_tokens) | set(self.syn_tokens)
        self.all_tokens_set = self.name_or_syn_tokens_set | set(self.def_tokens)
        self.self_name_raw_score = _ZERO


class _CorpusScope:
    __slots__ = (
        "avgdl_def",
        "avgdl_name",
        "avgdl_syn",
        "df",
        "idf_cache",
        "items_list",
        "num_docs",
    )

    def __init__(self) -> None:
        self.items_list: list[_IndexedItem] = []
        self.num_docs = _ZERO
        self.avgdl_name = _ZERO
        self.avgdl_syn = _ZERO
        self.avgdl_def = _ZERO
        self.df: Counter[str] = Counter()
        self.idf_cache: dict[str, Decimal] = {}

    def get_idf(self, token: str) -> Decimal:
        cached = self.idf_cache.get(token)
        if cached is not None:
            return cached
        df = Decimal(self.df.get(token, 0))
        ratio = (self.num_docs - df + _HALF) / (df + _HALF)
        val = (_ONE + ratio).ln()
        self.idf_cache[token] = val
        return val


class LexicalIndex:
    """Domain-neutral multi-field Okapi BM25 index with Decimal(28) precision."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        acronyms: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.config: LexicalMatcherConfig = load_lexical_matcher_config(config_path)
        self._k1: Decimal = Decimal(self.config.k1)
        self._b: Decimal = Decimal(self.config.b)
        self._w_name: Decimal = Decimal(self.config.weights.w_name)
        self._w_syn: Decimal = Decimal(self.config.weights.w_syn)
        self._w_def: Decimal = Decimal(self.config.weights.w_def)
        self._hit_gate_cap: Decimal = Decimal(self.config.hit_gate_cap)
        self._grounding_cutoff: Decimal = Decimal(self.config.grounding_threshold)

        self._acronyms: dict[str, tuple[str, ...]] = {
            k.lower(): tuple(str(tok) for tok in v) for k, v in (acronyms or {}).items()
        }
        self._items_by_id: dict[str, tuple[str, _IndexedItem]] = {}
        self._scopes: dict[str, _CorpusScope] = {}

    def tokenize(
        self,
        text: str,
        *,
        acronyms: Mapping[str, Sequence[str]] | None = None,
        context_tokens: AbstractSet[str] | None = None,
    ) -> list[str]:
        """Execute deterministic 7-step Unicode tokenization with optional acronym expansion."""
        if not text:
            return []

        normalized = unicodedata.normalize("NFC", text)
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
        s = re.sub(r"[-_/.,\\]+", " ", s)
        raw_tokens = re.findall(r"\b[^\W_]+\b", s)
        lowered = [t.lower() for t in raw_tokens]

        active_acronyms: Mapping[str, Sequence[str]] = (
            {k.lower(): tuple(str(tok) for tok in v) for k, v in acronyms.items()}
            if acronyms is not None
            else self._acronyms
        )

        effective_ctx = (
            {t.lower() for t in (context_tokens or ())}
            | set(lowered)
            | {w.lower() for w in re.findall(r"[A-Za-z0-9_]+", normalized)}
        )
        allow_sec_expansion = bool(
            "sec" in active_acronyms
            and (effective_ctx & _SEC_CONTEXT_TRIGGERS)
            and not (effective_ctx & _SEC_EXCLUSION_TOKENS)
        )

        token_entries: list[tuple[str, bool]] = []
        for t in lowered:
            if t == "sec" and not allow_sec_expansion:
                token_entries.append((t, False))
            elif t in active_acronyms:
                for exp_token in active_acronyms[t]:
                    token_entries.append((exp_token, True))
            else:
                token_entries.append((t, False))

        return [
            token
            for token, is_protected in token_entries
            if is_protected or token not in STOPWORDS
        ]

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

    def index_items(
        self,
        items: Sequence[KnowledgeItem],
        *,
        origin_source_id: str = "",
        origin_kind: Literal["project_graph_a", "reference_standard"] = "reference_standard",
    ) -> None:
        """Atomically index KnowledgeItems partitioned by entry_kind (class-scoped IDF)."""
        with localcontext() as ctx:
            ctx.prec = 28
            unique_items: dict[str, KnowledgeItem] = {}
            for item in items:
                unique_items[item.entry_id] = item
            deduped = list(unique_items.values())

            if not deduped:
                self._items_by_id = {}
                self._scopes = {}
                return

            new_by_id: dict[str, tuple[str, _IndexedItem]] = {}
            new_scopes: dict[str, _CorpusScope] = {}
            scope_totals: dict[str, list[Decimal]] = {}

            for item in deduped:
                idx_item = _IndexedItem(
                    item,
                    name_tokens=self.tokenize(item.label),
                    syn_tokens_list=[self.tokenize(a) for a in item.aliases],
                    def_tokens=self.tokenize(item.definition),
                    origin_source_id=origin_source_id,
                    origin_kind=origin_kind,
                )
                scope = new_scopes.get(item.entry_kind)
                if scope is None:
                    scope = _CorpusScope()
                    new_scopes[item.entry_kind] = scope
                    scope_totals[item.entry_kind] = [_ZERO, _ZERO, _ZERO]

                new_by_id[item.entry_id] = (item.entry_kind, idx_item)
                scope.items_list.append(idx_item)
                totals = scope_totals[item.entry_kind]
                totals[0] += idx_item.name_len
                totals[1] += idx_item.syn_len
                totals[2] += idx_item.def_len
                for t in idx_item.all_tokens_set:
                    scope.df[t] += 1

            for entry_kind, scope in new_scopes.items():
                n = len(scope.items_list)
                num_docs = Decimal(n)
                totals = scope_totals[entry_kind]
                scope.num_docs = num_docs
                scope.avgdl_name = totals[0] / num_docs
                scope.avgdl_syn = totals[1] / num_docs
                scope.avgdl_def = totals[2] / num_docs
                for idx_item in scope.items_list:
                    idx_item.self_name_raw_score = (
                        self._compute_raw_score(scope, idx_item, idx_item.name_tokens)
                        if idx_item.name_tokens
                        else _ZERO
                    )

            self._items_by_id = new_by_id
            self._scopes = new_scopes

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

    def _compute_raw_score(
        self,
        scope: _CorpusScope,
        item: _IndexedItem,
        query_tokens: list[str],
    ) -> Decimal:
        raw_score = _ZERO
        for t in dict.fromkeys(query_tokens):
            if t not in scope.df:
                continue
            idf = scope.get_idf(t)
            tf_name = self._compute_tf(item.name_counts.get(t, 0), item.name_len, scope.avgdl_name)
            tf_syn = self._compute_tf(item.syn_counts.get(t, 0), item.syn_len, scope.avgdl_syn)
            tf_def = self._compute_tf(item.def_counts.get(t, 0), item.def_len, scope.avgdl_def)
            field_sum = (self._w_name * tf_name) + (self._w_syn * tf_syn) + (self._w_def * tf_def)
            raw_score += idf * field_sum
        return raw_score

    def _compute_query_self_score(self, scope: _CorpusScope, query_tokens: list[str]) -> Decimal:
        if not query_tokens or scope.avgdl_name <= _ZERO:
            return _ZERO
        q_counts = Counter(query_tokens)
        q_len = Decimal(len(query_tokens))
        self_score = _ZERO
        for t, cnt in q_counts.items():
            idf = scope.get_idf(t)
            tf = self._compute_tf(cnt, q_len, scope.avgdl_name)
            self_score += idf * self._w_name * tf
        return self_score

    def _score_indexed_item(
        self,
        scope: _CorpusScope,
        idx_item: _IndexedItem,
        query_tokens: list[str],
        query_self_score: Decimal,
        q_norm: str,
    ) -> tuple[Decimal, Decimal, bool]:
        if not query_tokens:
            return _FOUR_DECIMALS * 0, _ZERO, False
        raw_score = self._compute_raw_score(scope, idx_item, query_tokens)
        if raw_score <= _ZERO:
            return _FOUR_DECIMALS * 0, _ZERO, False

        denom = max(idx_item.self_name_raw_score, query_self_score)
        norm_score = _ZERO if denom <= _ZERO else min(_ONE, raw_score / denom)
        has_name_or_syn_hit = any(
            t in idx_item.name_or_syn_tokens_set and t not in STOPWORDS for t in query_tokens
        )
        quantized = self.apply_hit_gate(norm_score, has_name_or_syn_hit=has_name_or_syn_hit)

        tok_match = query_tokens == idx_item.name_tokens or query_tokens in idx_item.syn_tokens_list
        exact_label_or_syn = (
            q_norm == idx_item.label_norm or q_norm in idx_item.syn_norms or tok_match
        )
        return quantized, raw_score, exact_label_or_syn

    def match_detailed(
        self,
        query: str,
        top_k: int = 25,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        min_score: float = 0.0,
        context_tokens: AbstractSet[str] | None = None,
        exclude_entry_ids: AbstractSet[str] | None = None,
    ) -> list[tuple[RecallHit, Decimal, bool]]:
        """Rank and return top-k RecallHits paired with (raw_bm25_decimal, exact_label_or_syn)."""
        if not math.isfinite(min_score) or min_score < 0.0 or min_score > 1.0:
            raise OpenDVError(
                f"min_score must be a finite float in [0.0, 1.0], got {min_score}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if not self._scopes or top_k <= 0:
            return []
        query_tokens = self.tokenize(query, context_tokens=context_tokens)
        if not query_tokens:
            return []

        q_norm = query.strip().casefold()
        q_tok_set = set(query_tokens)
        results: list[tuple[RecallHit, Decimal, bool]] = []

        with localcontext() as ctx:
            ctx.prec = 28
            for kind, scope in self._scopes.items():
                if entry_kinds is not None and kind not in entry_kinds:
                    continue
                query_self_score = self._compute_query_self_score(scope, query_tokens)
                for idx_item in scope.items_list:
                    if exclude_entry_ids and idx_item.item.entry_id in exclude_entry_ids:
                        continue
                    if not idx_item.all_tokens_set.intersection(q_tok_set):
                        continue
                    quantized, raw_bm25, exact = self._score_indexed_item(
                        scope, idx_item, query_tokens, query_self_score, q_norm
                    )
                    if float(quantized) >= min_score and raw_bm25 > _ZERO:
                        hit = RecallHit(
                            entry_id=idx_item.item.entry_id,
                            label=idx_item.item.label,
                            entry_kind=idx_item.item.entry_kind,
                            raw_score=quantized,
                            origin_source_id=idx_item.origin_source_id,
                            origin_kind=idx_item.origin_kind,
                        )
                        results.append((hit, raw_bm25, exact))

            results.sort(key=lambda row: (-row[0].raw_score, -row[1], row[0].entry_id))
            return results[:top_k]

    def match(
        self,
        query: str,
        top_k: int = 25,
        *,
        entry_kinds: AbstractSet[str] | None = None,
        min_score: float = 0.0,
        context_tokens: AbstractSet[str] | None = None,
    ) -> list[RecallHit]:
        """Rank and return top-k RecallHits matching query."""
        return [
            hit
            for hit, _, _ in self.match_detailed(
                query,
                top_k=top_k,
                entry_kinds=entry_kinds,
                min_score=min_score,
                context_tokens=context_tokens,
            )
        ]

    def score_detail(
        self,
        query: str,
        entry_id: str,
        *,
        context_tokens: AbstractSet[str] | None = None,
    ) -> tuple[Decimal, Decimal, bool]:
        """Compute (quantized_score, raw_bm25_decimal, exact_label_or_syn) for entry_id."""
        pair = self._items_by_id.get(entry_id)
        if pair is None:
            return self.quantize_score(_ZERO), _ZERO, False
        entry_kind, idx_item = pair
        scope = self._scopes[entry_kind]
        with localcontext() as ctx:
            ctx.prec = 28
            query_tokens = self.tokenize(query, context_tokens=context_tokens)
            if not query_tokens:
                return self.quantize_score(_ZERO), _ZERO, False
            query_self_score = self._compute_query_self_score(scope, query_tokens)
            return self._score_indexed_item(
                scope,
                idx_item,
                query_tokens,
                query_self_score,
                query.strip().casefold(),
            )

    def score(
        self,
        query: str,
        entry_id: str,
        *,
        context_tokens: AbstractSet[str] | None = None,
    ) -> Decimal:
        """Compute normalized quantized score in [0, 1] for query vs entry_id."""
        return self.score_detail(query, entry_id, context_tokens=context_tokens)[0]


__all__ = [
    "STOPWORDS",
    "LexicalIndex",
    "LexicalMatcherConfig",
    "LexicalWeightsConfig",
    "load_lexical_matcher_config",
]
