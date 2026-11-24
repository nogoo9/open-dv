"""Deterministic 7-step Unicode tokenization pipeline for ADR-008 BM25 FIBO matching."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

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

STOPWORDS: Final[set[str]] = {
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


def tokenize_concept_text(text: str) -> list[str]:
    """Execute the 7-step ADR-008 tokenization pipeline on input text."""
    if not text:
        return []

    normalized = unicodedata.normalize("NFC", text)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"[-_/.,\\]+", " ", s)
    raw_tokens = re.findall(r"\b[^\W_]+\b", s)
    lowered = [t.lower() for t in raw_tokens]

    token_entries: list[tuple[str, bool]] = []
    for t in lowered:
        if t in FINANCIAL_ACRONYMS:
            for exp_token in FINANCIAL_ACRONYMS[t]:
                token_entries.append((exp_token, True))
        else:
            token_entries.append((t, False))

    return [
        token
        for token, is_protected in token_entries
        if is_protected or token not in STOPWORDS
    ]


__all__ = [
    "FINANCIAL_ACRONYMS",
    "STOPWORDS",
    "tokenize_concept_text",
]
