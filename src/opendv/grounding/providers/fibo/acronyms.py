"""FIBO financial domain acronyms and conditional SEC context guard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Final

FINANCIAL_ACRONYMS: Final[dict[str, list[str]]] = {
    "nav": ["net", "asset", "value"],
    "lei": ["legal", "entity", "identifier"],
    "pe": ["private", "equity"],
    "acct": ["account"],
    "bk": ["business", "key"],
    "sec": ["securities", "and", "exchange", "commission"],
    "cusip": ["committee", "on", "uniform", "security", "identification", "procedures"],
    "isin": ["international", "securities", "identification", "number"],
    "sedol": ["stock", "exchange", "daily", "official", "list"],
    "figi": ["financial", "instrument", "global", "identifier"],
    "mic": ["market", "identifier", "code"],
    "bic": ["business", "identifier", "code"],
    "fbo": ["for", "benefit", "of"],
    "gp": ["general", "partner"],
    "lp": ["limited", "partner"],
    "aum": ["assets", "under", "management"],
    "irr": ["internal", "rate", "of", "return"],
    "ebitda": [
        "earnings",
        "before",
        "interest",
        "taxes",
        "depreciation",
        "and",
        "amortization",
    ],
}

SEC_CONTEXT_TRIGGERS: Final[frozenset[str]] = frozenset(
    {"filing", "cik", "13f", "10k", "edgar", "regulator"}
)

SEC_EXCLUSION_TOKENS: Final[frozenset[str]] = frozenset(
    {"second", "seconds", "sector", "security_id"}
)


def should_expand_sec(context_tokens: AbstractSet[str]) -> bool:
    """Return True iff regulatory SEC context is present and non-SEC exclusions are absent."""
    lowered = {t.lower() for t in context_tokens}
    return bool((lowered & SEC_CONTEXT_TRIGGERS) and not (lowered & SEC_EXCLUSION_TOKENS))


def expand_financial_tokens(
    tokens: Sequence[str],
    *,
    context_tokens: AbstractSet[str] | None = None,
) -> list[str]:
    """Expand FIBO financial acronyms across pre-tokenized terms with SEC context gating."""
    combined_ctx = {t.lower() for t in tokens}
    if context_tokens is not None:
        combined_ctx |= {t.lower() for t in context_tokens}
    allow_sec = should_expand_sec(combined_ctx)

    expanded: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low == "sec" and not allow_sec:
            expanded.append(low)
        elif low in FINANCIAL_ACRONYMS:
            expanded.extend(FINANCIAL_ACRONYMS[low])
        else:
            expanded.append(low)
    return expanded


def quantize_recall_score(ratio: float) -> Decimal:
    raw = Decimal("0.70") + Decimal("0.28") * Decimal(str(round(ratio, 4)))
    return min(Decimal("0.98"), raw).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def check_arch_type_veto(
    arch: str | None, is_bool_prop: bool, *,
    is_num: bool, is_temp: bool, is_bool: bool, is_float: bool,
) -> str | None:
    if is_num and (arch == "temporal" or is_bool_prop):
        return "num_vs_temp_bool"
    if is_temp and (arch in ("quantity_measure", "identifier") or is_bool_prop):
        return "temp_vs_num_id_bool"
    if is_bool and arch in ("temporal", "quantity_measure", "identifier", "code_classifier"):
        return "bool_vs_arch"
    if is_float and arch == "identifier":
        return "float_vs_id"
    return None


def score_candidate_shape_range(
    arch: str | None, is_bool_prop: bool, pat_key: Any, pats: Mapping[str, float],
    *, is_temp: bool, is_bool: bool, is_num: bool, is_float: bool,
    is_cat: bool, is_uniq: bool, decl: Any, look_enum: bool,
    dist_ratio: float, num_min: Any,
) -> float:
    if (
        (is_temp and arch == "temporal") or (is_bool and is_bool_prop)
        or (is_num and arch == "quantity_measure")
        or (arch in ("identifier", "code_classifier") and isinstance(pat_key, str)
            and pats.get(pat_key, 0.0) >= 0.50)
    ):
        return 1.0
    if arch == "identifier" and (is_cat or decl in ("INT32", "INT64")):
        return 0.7 if is_uniq else 0.4
    if arch == "code_classifier":
        if is_cat and (look_enum or dist_ratio <= 0.35):
            return 0.85
        return -0.9 if is_float else 0.2
    if arch == "quantity_measure" and decl == "UTF8_STRING" and num_min is None:
        return -0.9
    return 0.2


__all__ = [
    "FINANCIAL_ACRONYMS",
    "SEC_CONTEXT_TRIGGERS",
    "SEC_EXCLUSION_TOKENS",
    "check_arch_type_veto",
    "expand_financial_tokens",
    "quantize_recall_score",
    "score_candidate_shape_range",
    "should_expand_sec",
]
