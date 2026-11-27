"""Layer-1 pure-stdlib PII/secret detection, masking, and HMAC-SHA256 tokenization."""

from __future__ import annotations

import collections
import hashlib
import hmac
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ValueFreqItem

MASKED_PII_TOKEN: Final[str] = "<MASKED_PII>"
REDACTED_LEAK_RE: Final[re.Pattern[str]] = re.compile(r"(?:<MASKED_PII>|\[REDACTED_)")
_MASKED_PII_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"<MASKED_PII>", re.IGNORECASE)
_REDACTED_PREFIX: Final[str] = "[redacted_"

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
_PIPE_SEP_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?|\|(?:\s*:?-{3,}:?\s*\|)+)$"
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


def table_has_aba_context(
    table_headers: Sequence[str] | None,
    table_rows: Sequence[Sequence[str]] | None,
) -> bool:
    """Return True if table headers (or row 0 when headerless) match RE_ABA_CONTEXT."""
    eff = (
        table_headers
        if (table_headers and any(h.strip() for h in table_headers))
        else (table_rows[0] if table_rows else None)
    )
    return bool(eff and RE_ABA_CONTEXT.search(" ".join(eff)))


def _split_pipe_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def find_pipe_table_aba_spans(text: str) -> list[tuple[int, int]]:
    """Find [start, end) spans of ABA digits in pipe tables with ABA context."""
    if not text or "|" not in text:
        return []
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)
    spans: list[tuple[int, int]] = []

    def _scan_row(row_idx: int, ln: str, *, force: bool = False) -> None:
        if force or RE_ABA_CONTEXT.search(" ".join(_split_pipe_cells(ln))):
            base = offsets[row_idx]
            for m in RE_ABA_DIGITS.finditer(ln):
                if is_valid_aba_routing(m.group(0)):
                    spans.append((base + m.start(), base + m.end()))

    idx, n = 0, len(lines)
    while idx < n:
        stripped = lines[idx].strip()
        if "|" in stripped and idx + 1 < n and _PIPE_SEP_RE.match(lines[idx + 1].strip()):
            end_idx = idx + 2
            while end_idx < n and "|" in lines[end_idx] and lines[end_idx].strip():
                end_idx += 1
            hdr_cells = _split_pipe_cells(lines[idx])
            eff_cells = (
                hdr_cells
                if any(hdr_cells)
                else (_split_pipe_cells(lines[idx + 2]) if idx + 2 < end_idx else [])
            )
            hdr_aba = bool(RE_ABA_CONTEXT.search(" ".join(eff_cells)))
            for r_idx in (idx, *range(idx + 2, end_idx)):
                _scan_row(r_idx, lines[r_idx], force=hdr_aba)
            idx = end_idx
            continue
        if "|" in stripped and (stripped.startswith("|") or stripped.count("|") >= 2):
            _scan_row(idx, lines[idx])
        idx += 1
    return spans


def mask_pipe_tables_in_text(text: str) -> str:
    """Mask ABA digits inside pipe tables when header or row has ABA context."""
    spans = find_pipe_table_aba_spans(text)
    if not spans:
        return text
    out: list[str] = []
    pos = 0
    for s, e in spans:
        if s >= pos:
            out.extend((text[pos:s], MASKED_PII_TOKEN))
            pos = e
    out.append(text[pos:])
    return "".join(out)


def detect_pii_in_column_name(col_name: str) -> list[str]:
    """Return PII tags matched by column name."""
    if not col_name:
        return []
    match = RE_PII_COLUMN.search(col_name)
    if not match:
        return []
    return [f"PII_COLUMN:{match.group(1).upper()}"]


def detect_pii_in_value(
    value: str,
    *,
    col_name: str | None = None,
    exempt_high_entropy: bool = False,
) -> list[str]:
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
    if (
        not exempt_high_entropy
        and not is_non_credential_digest_column(col_name)
        and find_high_entropy_tokens(value)
    ):
        tags.append("HIGH_ENTROPY_SECRET")
    return tags


def _replace_pii_spans(text: str, token: str, *, col_name: str | None = None) -> str:
    """Replace detected PII/secret spans in text with `token`."""
    if not text:
        return text

    result = text
    for start, end in reversed(_find_aba_spans(result)):
        result = result[:start] + token + result[end:]

    result = RE_SSN.sub(token, result)
    result = RE_EIN.sub(token, result)
    result = RE_EMAIL.sub(token, result)
    result = RE_PHONE.sub(token, result)
    result = RE_IBAN.sub(token, result)
    result = RE_CC_PAN.sub(
        lambda m: token if is_valid_luhn(m.group(0)) else m.group(0),
        result,
    )
    if not is_non_credential_digest_column(col_name):
        result = RE_HEX_64.sub(token, result)
        for tok in find_high_entropy_tokens(result):
            result = result.replace(tok, token)
    return result


def mask_pii_text(text: str, *, col_name: str | None = None) -> str:
    """Replace detected PII/secret substrings in text with '<MASKED_PII>'."""
    return _replace_pii_spans(text, MASKED_PII_TOKEN, col_name=col_name)


def truncate_preserving_masked_pii(text: str, limit: int) -> str:
    """Truncate text to <= limit chars without bisecting a <MASKED_PII> token."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    tok_len = len(MASKED_PII_TOKEN)
    idx = text.rfind(
        MASKED_PII_TOKEN, max(0, limit - tok_len + 1), min(len(text), limit + tok_len - 1)
    )
    if idx != -1 and idx < limit < idx + tok_len:
        return text[:idx].rstrip()
    return text[:limit]


def _strip_redacted_brackets_once(text: str) -> str:
    """Linear O(n) pass stripping closed [REDACTED_...] or unclosed [REDACTED_ tokens."""
    low = text.lower()
    if _REDACTED_PREFIX not in low:
        return text
    has_close = "]" in text
    out: list[str] = []
    pos = 0
    prefix_len = len(_REDACTED_PREFIX)
    n = len(text)
    while pos < n:
        start = low.find(_REDACTED_PREFIX, pos)
        if start == -1:
            out.append(text[pos:])
            break
        if has_close:
            close_idx = text.find("]", start + prefix_len)
            if close_idx != -1:
                next_open = low.find(_REDACTED_PREFIX, start + prefix_len, close_idx)
                if next_open != -1:
                    out.append(text[pos:next_open])
                    pos = next_open
                    continue
                out.append(text[pos:start])
                pos = close_idx + 1
                continue
        out.append(text[pos:start])
        end = start + prefix_len
        while end < n and not text[end].isspace() and text[end] != "]":
            end += 1
        pos = end
    return "".join(out)


def _strip_redaction_placeholders(text: str) -> str:
    cleaned = text
    for _ in range(len(text) + 1):
        next_val = _MASKED_PII_TOKEN_RE.sub("", cleaned)
        next_val = _strip_redacted_brackets_once(next_val)
        if next_val == cleaned:
            break
        cleaned = next_val
    return cleaned


def strip_pii_spans(
    text: str,
    *,
    col_name: str | None = None,
    strip_placeholders: bool = True,
) -> str:
    """Strip raw PII spans (and optional placeholders) without emitting [REDACTED_PII]."""
    if not text:
        return text
    cleaned = _strip_redaction_placeholders(text) if strip_placeholders else text
    cleaned = _replace_pii_spans(cleaned, "", col_name=col_name)
    if strip_placeholders:
        cleaned = _strip_redaction_placeholders(cleaned)
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in cleaned.splitlines()]
    if len(lines) > 1:
        return "\n".join(line for line in lines if line)
    return lines[0] if lines else ""


def has_redacted_placeholder(text: str) -> bool:
    """Return True if text contains '<MASKED_PII>' or '[REDACTED_*' placeholders."""
    return bool(REDACTED_LEAK_RE.search(text))


def ensure_no_redacted_placeholder(text: str, *, context: str = "Payload") -> None:
    """Raise REDACTED_PLACEHOLDER_LEAK if text contains forbidden redaction placeholders."""
    if has_redacted_placeholder(text):
        raise OpenDVError(
            f"{context} contains forbidden redaction placeholder (<MASKED_PII> or [REDACTED_*]).",
            error_code=ErrorCode.REDACTED_PLACEHOLDER_LEAK,
        )


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
    """Return 16-hex HMAC-SHA256 prefix identifying a PII tokenization salt."""
    return hmac.new(
        _normalize_salt(salt), b"opendv-proving-salt-id-v1", hashlib.sha256
    ).hexdigest()[:16]


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
    "REDACTED_LEAK_RE",
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
    "ensure_no_redacted_placeholder",
    "find_high_entropy_tokens",
    "find_pipe_table_aba_spans",
    "has_redacted_placeholder",
    "is_non_credential_digest_column",
    "is_valid_aba_routing",
    "is_valid_luhn",
    "mask_cell_value",
    "mask_pii_text",
    "mask_pipe_tables_in_text",
    "redact_pii_value_frequencies",
    "strip_pii_spans",
    "table_has_aba_context",
    "tokenize_pii_scalar",
    "tokenize_row_pii",
    "truncate_preserving_masked_pii",
]
