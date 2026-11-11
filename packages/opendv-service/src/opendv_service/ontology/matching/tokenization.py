"""Deterministic 7-step Unicode 14.0 tokenization pipeline (Task 5A.1 / Issue #211).

Conforms strictly to docs/19 §4.4.1 and ADR-008:
1. Unicode NFC normalization.
2. CamelCase decomposition.
3. Delimiter replacement.
4. Alphanumeric word extraction.
5. Unicode lowercasing.
6. 18 financial acronyms expansion (expanded tokens protected from stopword pruning).
7. 33 English and modeling stopwords filtration.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# 18 Pinned Financial Acronyms (docs/19 §4.4.1, ADR-008 §2)
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
    "ebitda": ["earnings", "before", "interest", "taxes", "depreciation", "and", "amortization"],
}

# 33 Pinned English & Modeling Stopwords (docs/19 §4.4.1, ADR-008 §2)
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
    """Execute the canonical 7-step tokenization pipeline on input text.

    Args:
        text: Arbitrary query, name, synonym, or definition text.

    Returns:
        Ordered list of normalized tokens.
    """
    if not text:
        return []

    # Step 1: Unicode NFC Normalization
    normalized = unicodedata.normalize("NFC", text)

    # Step 2: CamelCase Decomposition
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)

    # Step 3: Delimiter Replacement
    s = re.sub(r"[-_/.,\\]+", " ", s)

    # Step 4: Alphanumeric Word Extraction
    raw_tokens = re.findall(r"\b[^\W_]+\b", s)

    # Step 5: Unicode Lowercasing
    lowered = [t.lower() for t in raw_tokens]

    # Step 6: Domain Acronym Expansion with Protected Markings
    # Tokens resulting from acronym expansion are marked as protected so that
    # stopwords inside expansions (e.g. 'and' in 'securities and exchange commission')
    # are preserved during Step 7.
    token_entries: list[tuple[str, bool]] = []
    for t in lowered:
        if t in FINANCIAL_ACRONYMS:
            for exp_token in FINANCIAL_ACRONYMS[t]:
                token_entries.append((exp_token, True))
        else:
            token_entries.append((t, False))

    # Step 7: Stopword Filtration
    filtered: list[str] = []
    for token, is_protected in token_entries:
        if is_protected or token not in STOPWORDS:
            filtered.append(token)

    return filtered
