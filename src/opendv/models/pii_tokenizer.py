"""Layer-1 pure-stdlib PII/secret detection, masking, and HMAC-SHA256 tokenization."""

from __future__ import annotations

import collections
import hashlib
import hmac
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from opendv.models.modeling_preferences import ValueFreqItem

MASKED_PII_TOKEN: Final[str] = "<MASKED_PII>"

RE_SSN: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_EIN: Final[re.Pattern[str]] = re.compile(r"\b\d{2}-\d{7}\b")
RE_ABA_DIGITS: Final[re.Pattern[str]] = re.compile(r"\b\d{9}\b")
RE_ABA_CONTEXT: Final[re.Pattern[str]] = re.compile(r"(?i)\b(aba|routing|rtn)\b")
RE_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
RE_PHONE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
RE_IBAN: Final[re.Pattern[str]] = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
RE_CC_PAN: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
RE_HEX_64: Final[re.Pattern[str]] = re.compile(r"\b[0-9a-fA-F]{64}\b")

RE_PII_COLUMN: Final[re.Pattern[str]] = re.compile(
    r"("
    r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"
    r"(?i:ssn|tin|ein|dob|zip|aba|"
    r"tax_?id|acct_?num|account_?number|holder_?name|credit_?card|card_?number)"
    r"(?:(?![A-Za-z0-9])|(?=[A-Z]))"
    r"|(?i:passport|birth|email|phone|mobile|"
    r"address|street|postal|salary|wage|iban|"
    r"beneficiary|routing)"
    r")"
)

PII_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^<PII:[0-9a-f]{16}>$")

_SNAKE_CASE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_UPPER_SNAKE_CASE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_CAMEL_CASE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[A-Z][a-z]+){2,}$|^[a-z]+(?:[A-Z][a-z]+)+$"
)
_EXEMPT_HEX_COL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^(?:hk_|hash_diff)|(?:_hk|_sha256|_digest|_fingerprint|_hash_diff|_hashdiff)$"
)
_SYNTHETIC_ABA_PREFIXES: Final[tuple[str, ...]] = ("1900", "2026", "0000")
_MIN_TOKEN_ENTROPY: Final[float] = 4.2
_ENTROPY_MIN_LEN: Final[int] = 24


def compute_shannon_entropy(text: str) -> float:
    """Compute Shannon entropy in bits per character."""
    if not text:
        return 0.0
    n = len(text)
    counts = collections.Counter(text)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def is_valid_aba_routing(digits: str) -> bool:
    """Validate 9-digit US ABA routing number via [3,7,1] Mod-10 checksum."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    if digits.startswith(_SYNTHETIC_ABA_PREFIXES):
        return False
    d = [int(ch) for ch in digits]
    checksum = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    return checksum % 10 == 0


def is_valid_luhn(pan: str) -> bool:
    """Validate payment card PAN via Luhn Mod-10 checksum."""
    digits = [int(ch) for ch in pan if ch.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    for idx, val in enumerate(reversed(digits)):
        if idx % 2 == 1:
            doubled = val * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += val
    return total % 10 == 0


def is_non_credential_digest_column(col_name: str | None) -> bool:
    """Return True if column name represents a non-credential Open-DV HK/digest column."""
    if not col_name:
        return False
    return bool(_EXEMPT_HEX_COL_RE.search(col_name.strip()))


def _is_exempt_entropy_token(token: str) -> bool:
    if MASKED_PII_TOKEN in token or "[REDACTED" in token:
        return True
    lower = token.lower()
    if lower.startswith(("urn:", "http://", "https://")):
        return True
    if _SNAKE_CASE_RE.fullmatch(token) or _UPPER_SNAKE_CASE_RE.fullmatch(token):
        return True
    return bool(_CAMEL_CASE_RE.fullmatch(token))


def find_high_entropy_tokens(text: str) -> list[str]:
    """Return high-entropy secret-like tokens (>= 24 chars, H >= 4.2 bits/char)."""
    hits: list[str] = []
    for raw_tok in re.split(r"\s+", text):
        tok = raw_tok.strip(",;()[]{}'\"<>")
        if len(tok) < _ENTROPY_MIN_LEN:
            continue
        if _is_exempt_entropy_token(tok):
            continue
        if compute_shannon_entropy(tok) >= _MIN_TOKEN_ENTROPY:
            hits.append(tok)
    return hits


def _find_aba_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in RE_ABA_DIGITS.finditer(text):
        digits = match.group(0)
        if not is_valid_aba_routing(digits):
            continue
        window_start = max(0, match.start() - 32)
        window_end = min(len(text), match.end() + 32)
        context = text[window_start:window_end]
        if RE_ABA_CONTEXT.search(context):
            spans.append((match.start(), match.end()))
    return spans


def detect_pii_in_column_name(col_name: str) -> list[str]:
    """Return PII tags matched by column name."""
    if not col_name:
        return []
    match = RE_PII_COLUMN.search(col_name)
    if not match:
        return []
    return [f"PII_COLUMN:{match.group(1).upper()}"]


def detect_pii_in_value(value: str, *, col_name: str | None = None) -> list[str]:
    """Detect PII, routing numbers, PANs, raw 64-hex, or high-entropy secrets in text."""
    if not value:
        return []
    tags: list[str] = []
    if RE_SSN.search(value):
        tags.append("SSN")
    if RE_EIN.search(value):
        tags.append("EIN")
    if _find_aba_spans(value):
        tags.append("ABA_ROUTING")
    if RE_EMAIL.search(value):
        tags.append("EMAIL")
    if RE_PHONE.search(value):
        tags.append("PHONE")
    if RE_IBAN.search(value):
        tags.append("IBAN")
    if any(is_valid_luhn(m.group(0)) for m in RE_CC_PAN.finditer(value)):
        tags.append("CC_PAN")
    if not is_non_credential_digest_column(col_name) and RE_HEX_64.search(value):
        tags.append("HEX_64")
    if not is_non_credential_digest_column(col_name) and find_high_entropy_tokens(value):
        tags.append("HIGH_ENTROPY_SECRET")
    return tags


def mask_pii_text(text: str, *, col_name: str | None = None) -> str:
    """Replace detected PII/secret substrings in text with '<MASKED_PII>'."""
    if not text:
        return text

    masked = text
    for start, end in reversed(_find_aba_spans(masked)):
        masked = masked[:start] + MASKED_PII_TOKEN + masked[end:]

    masked = RE_SSN.sub(MASKED_PII_TOKEN, masked)
    masked = RE_EIN.sub(MASKED_PII_TOKEN, masked)
    masked = RE_EMAIL.sub(MASKED_PII_TOKEN, masked)
    masked = RE_PHONE.sub(MASKED_PII_TOKEN, masked)
    masked = RE_IBAN.sub(MASKED_PII_TOKEN, masked)
    masked = RE_CC_PAN.sub(
        lambda m: MASKED_PII_TOKEN if is_valid_luhn(m.group(0)) else m.group(0),
        masked,
    )
    if not is_non_credential_digest_column(col_name):
        masked = RE_HEX_64.sub(MASKED_PII_TOKEN, masked)
        for tok in find_high_entropy_tokens(masked):
            masked = masked.replace(tok, MASKED_PII_TOKEN)
    return masked


def mask_cell_value(
    value: str | None,
    *,
    col_name: str | None = None,
    column_pii_tags: Sequence[str] = (),
) -> str | None:
    """Mask a cell value to '<MASKED_PII>' if column or value triggers PII rules."""
    if value is None:
        return None
    if column_pii_tags:
        return MASKED_PII_TOKEN
    if detect_pii_in_value(value, col_name=col_name):
        return MASKED_PII_TOKEN
    return value


def _normalize_salt(salt: bytes | str) -> bytes:
    return salt.encode("utf-8") if isinstance(salt, str) else salt


def compute_salt_digest(salt: bytes | str) -> str:
    """Return 16-hex SHA-256 prefix identifying a PII tokenization salt."""
    return hashlib.sha256(_normalize_salt(salt)).hexdigest()[:16]


def tokenize_pii_scalar(value: Any, *, salt: bytes | str) -> str | None:
    """Deterministically and idempotently tokenize a non-null scalar via HMAC-SHA256."""
    if value is None:
        return None
    text = str(value)
    if PII_TOKEN_RE.fullmatch(text):
        return text
    digest = hmac.new(_normalize_salt(salt), text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"<PII:{digest}>"


def tokenize_row_pii(
    row: Mapping[str, Any],
    *,
    pii_columns: set[str] | frozenset[str],
    salt: bytes | str,
) -> dict[str, Any]:
    """Tokenize PII columns and inline PII-detected string cells across a row dict."""
    tokenized: dict[str, Any] = {}
    for key, val in row.items():
        if val is None:
            tokenized[key] = None
        elif key in pii_columns or (
            isinstance(val, str) and bool(detect_pii_in_value(val, col_name=key))
        ):
            tokenized[key] = tokenize_pii_scalar(val, salt=salt)
        else:
            tokenized[key] = val
    return tokenized


def redact_pii_value_frequencies(
    items: Sequence[ValueFreqItem],
    *,
    is_pii_col: bool,
    col_name: str | None = None,
) -> list[ValueFreqItem]:
    """Redact PII values in ColumnProfile top_value_frequencies while preserving counts."""
    redacted: list[ValueFreqItem] = []
    for idx, item in enumerate(items):
        if is_pii_col or detect_pii_in_value(item.value, col_name=col_name):
            redacted.append(ValueFreqItem(value=f"<MASKED_PII_{idx}>", count=item.count))
        else:
            redacted.append(item)
    return redacted


__all__ = [
    "MASKED_PII_TOKEN",
    "PII_TOKEN_RE",
    "RE_ABA_CONTEXT",
    "RE_ABA_DIGITS",
    "RE_CC_PAN",
    "RE_EIN",
    "RE_EMAIL",
    "RE_HEX_64",
    "RE_IBAN",
    "RE_PHONE",
    "RE_PII_COLUMN",
    "RE_SSN",
    "compute_salt_digest",
    "compute_shannon_entropy",
    "detect_pii_in_column_name",
    "detect_pii_in_value",
    "find_high_entropy_tokens",
    "is_non_credential_digest_column",
    "is_valid_aba_routing",
    "is_valid_luhn",
    "mask_cell_value",
    "mask_pii_text",
    "redact_pii_value_frequencies",
    "tokenize_pii_scalar",
    "tokenize_row_pii",
]
