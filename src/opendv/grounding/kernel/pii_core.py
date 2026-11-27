"""Grounding kernel PII scanner/masker re-exports + raw-PII placeholder stripping."""

from __future__ import annotations

import re
from typing import Final

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import (
    MASKED_PII_TOKEN,
    PII_TOKEN_RE,
    RE_ABA_CONTEXT,
    RE_ABA_DIGITS,
    RE_CC_PAN,
    RE_EIN,
    RE_EMAIL,
    RE_HEX_64,
    RE_IBAN,
    RE_PHONE,
    RE_PII_COLUMN,
    RE_SSN,
    _find_aba_spans,
    compute_salt_digest,
    compute_shannon_entropy,
    detect_pii_in_column_name,
    detect_pii_in_value,
    find_high_entropy_tokens,
    is_non_credential_digest_column,
    is_valid_aba_routing,
    is_valid_luhn,
    mask_cell_value,
    mask_pii_text,
    redact_pii_value_frequencies,
    tokenize_pii_scalar,
    tokenize_row_pii,
)

REDACTED_LEAK_RE: Final[re.Pattern[str]] = re.compile(r"(?:<MASKED_PII>|\[REDACTED_)")
_MASKED_PII_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"<MASKED_PII>", re.IGNORECASE)
_REDACTED_PREFIX: Final[str] = "[redacted_"


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


__all__ = [
    "MASKED_PII_TOKEN", "PII_TOKEN_RE", "REDACTED_LEAK_RE",
    "RE_ABA_CONTEXT", "RE_ABA_DIGITS", "RE_CC_PAN", "RE_EIN", "RE_EMAIL",
    "RE_HEX_64", "RE_IBAN", "RE_PHONE", "RE_PII_COLUMN", "RE_SSN",
    "compute_salt_digest", "compute_shannon_entropy", "detect_pii_in_column_name",
    "detect_pii_in_value", "ensure_no_redacted_placeholder", "find_high_entropy_tokens",
    "has_redacted_placeholder", "is_non_credential_digest_column", "is_valid_aba_routing",
    "is_valid_luhn", "mask_cell_value", "mask_pii_text", "redact_pii_value_frequencies",
    "strip_pii_spans", "tokenize_pii_scalar", "tokenize_row_pii",
]
