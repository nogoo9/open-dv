"""Algorithm A.5: 6-Stage Length-Prefixed Canonical SHA-256 Hashing for Data Vault 2.0.

Implements collision-resistant business key hashing (`_hk`) and descriptive payload
change detection (`hash_diff`), plus Zero Key foreign-key resolution, in accordance
with docs/01-core-modelling-spec.md (§6.1), docs/05-elm-calculus-and-reference-algorithms.md
(§3.5), and docs/06-explainability-and-observability-spec.md (§1.4, §2.4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from opendv_core.constants import (
    DEFAULT_DELIMITER,
    GLOBAL_BKCC,
    NULL_TOKEN,
    ZERO_KEY_ERROR,
    ZERO_KEY_ERROR_TOKEN,
    ZERO_KEY_UNKNOWN,
    ZERO_KEY_UNKNOWN_TOKEN,
)

type TokenValue = str | int | float | bool | None


def encode_dv2_token(
    value: TokenValue,
    *,
    is_business_key: bool = True,
    null_token: str = NULL_TOKEN,
) -> str:
    """Encode a single scalar value using Algorithm A.5 Stages 1-3 (`<utf8_byte_len>:<val>`).

    - Stage 1 (Null & Whitespace Normalization): `None` or whitespace-only strings map
      to the reserved `null_token` (`-1:^^`), whose `-1` byte length can never be
      produced by any valid UTF-8 string.
    - Stage 2 (Case Fold Policy): Business keys (`is_business_key=True`) apply
      `.upper()`; Satellite payload `hash_diff` attributes (`is_business_key=False`)
      preserve case sensitivity.
    - Stage 3 (Byte-Length Prefixing): Prefix with UTF-8 byte length `<byte_len>:<val>`.
    """
    if value is None:
        return null_token
    normalized = str(value).strip()
    if normalized == "":
        return null_token
    if is_business_key:
        normalized = normalized.upper()
    byte_len = len(normalized.encode("utf-8"))
    return f"{byte_len}:{normalized}"


def compute_dv2_prehash_string(
    tokens: Sequence[TokenValue] | Mapping[str, TokenValue],
    *,
    is_business_key: bool = True,
    bkcc_prefix: str | None = None,
    delimiter: str = DEFAULT_DELIMITER,
    null_token: str = NULL_TOKEN,
    sort_tokens: bool = False,
) -> str:
    """Compute the Algorithm A.5 Stage 4 pre-hash serialization string.

    Args:
        tokens: Positional sequence of key/attribute values, or a column-name mapping.
            When a mapping is passed with `is_business_key=False` or `sort_tokens=True`,
            values are ordered alphabetically by column name.
        is_business_key: True for Hub/Link `_hk` (applies UPPER); False for Satellite
            `hash_diff` (preserves case).
        bkcc_prefix: Optional Business Key Collision Code (e.g., `'GLOBAL'`) to prepend
            as the first token when `is_business_key=True`.
        delimiter: Token separator (default `'||'`).
        null_token: Sentinel representation for NULL or empty strings (default `'-1:^^'`).
        sort_tokens: Whether to sort tokens alphabetically (used for `hash_diff` when
            passing an unordered collection).

    Returns:
        Deterministic length-prefixed pre-hash string (e.g., `'6:GLOBAL||6:LP-101'`).
    """
    if isinstance(tokens, (str, bytes)):
        msg = "tokens must be a Sequence or Mapping of column values, not a bare str/bytes"
        raise TypeError(msg)

    if isinstance(tokens, Mapping):
        if not is_business_key or sort_tokens:
            ordered_values: list[TokenValue] = [tokens[k] for k in sorted(tokens.keys())]
        else:
            ordered_values = list(tokens.values())
    else:
        ordered_values = list(tokens)
        if sort_tokens:
            ordered_values = sorted(
                ordered_values,
                key=lambda v: "" if v is None else str(v).strip(),
            )

    if bkcc_prefix is not None and bkcc_prefix.strip() != "":
        ordered_values = [bkcc_prefix.strip(), *ordered_values]

    encoded_parts = [
        encode_dv2_token(tok, is_business_key=is_business_key, null_token=null_token)
        for tok in ordered_values
    ]
    return delimiter.join(encoded_parts)


def compute_dv2_length_prefixed_sha256(
    tokens: Sequence[TokenValue] | Mapping[str, TokenValue],
    *,
    is_business_key: bool = True,
    bkcc_prefix: str | None = None,
    delimiter: str = DEFAULT_DELIMITER,
    null_token: str = NULL_TOKEN,
    sort_tokens: bool = False,
) -> str:
    """Compute the Algorithm A.5 64-character uppercase hex SHA-256 digest.

    If `is_business_key=True` and all business key values (excluding `bkcc_prefix`)
    are `None` or empty whitespace, returns `ZERO_KEY_UNKNOWN` (`'0' * 64`).
    """
    if isinstance(tokens, (str, bytes)):
        msg = "tokens must be a Sequence or Mapping of column values, not a bare str/bytes"
        raise TypeError(msg)

    raw_values = list(tokens.values()) if isinstance(tokens, Mapping) else list(tokens)
    all_null = len(raw_values) == 0 or all(v is None or str(v).strip() == "" for v in raw_values)
    if is_business_key and all_null:
        return ZERO_KEY_UNKNOWN

    prehash = compute_dv2_prehash_string(
        tokens,
        is_business_key=is_business_key,
        bkcc_prefix=bkcc_prefix,
        delimiter=delimiter,
        null_token=null_token,
        sort_tokens=sort_tokens,
    )
    return hashlib.sha256(prehash.encode("utf-8")).hexdigest().upper()


def dv2_canonical_hash(
    values: Sequence[TokenValue],
    is_business_key: bool = True,
    bkcc_prefix: str | None = None,
    delimiter: str = DEFAULT_DELIMITER,
    null_token: str = NULL_TOKEN,
) -> str:
    """Reference alias for `compute_dv2_length_prefixed_sha256` (docs/05 §3.5)."""
    return compute_dv2_length_prefixed_sha256(
        values,
        is_business_key=is_business_key,
        bkcc_prefix=bkcc_prefix,
        delimiter=delimiter,
        null_token=null_token,
    )


def resolve_fk_hash_key_with_zero_keys(
    fk_tokens: Sequence[TokenValue] | TokenValue,
    *,
    bkcc_prefix: str | None = GLOBAL_BKCC,
    is_optional_inapplicable: bool = False,
) -> str:
    """Resolve foreign key column value(s) to a Hub Hash Key or Zero Key sentinel.

    - All NULL/empty on mandatory FK -> `ZERO_KEY_UNKNOWN` (`'0' * 64`)
    - All NULL/empty on optional inapplicable FK (`is_optional_inapplicable=True`)
      or explicit `'-2'` token -> `ZERO_KEY_ERROR` (`'f' * 64`)
    - Explicit `'-1'` token -> `ZERO_KEY_UNKNOWN` (`'0' * 64`)
    - Valid non-empty token(s) -> `compute_dv2_length_prefixed_sha256` with `bkcc_prefix`.
    """
    if isinstance(fk_tokens, str) or not isinstance(fk_tokens, Sequence):
        token_list: list[TokenValue] = [fk_tokens]
    else:
        token_list = list(fk_tokens)

    normalized = [str(t).strip() if t is not None else "" for t in token_list]

    # Support callers that pass (bkcc_prefix, fk_val, ...) directly in fk_tokens
    effective_bkcc = bkcc_prefix.strip() if bkcc_prefix is not None else ""
    if effective_bkcc and len(normalized) >= 2 and normalized[0].upper() == effective_bkcc.upper():
        key_parts = normalized[1:]
    else:
        key_parts = normalized

    if len(key_parts) == 0 or all(t == "" for t in key_parts):
        return ZERO_KEY_ERROR if is_optional_inapplicable else ZERO_KEY_UNKNOWN

    if key_parts == [ZERO_KEY_UNKNOWN_TOKEN]:
        return ZERO_KEY_UNKNOWN

    if key_parts == [ZERO_KEY_ERROR_TOKEN]:
        return ZERO_KEY_ERROR

    if effective_bkcc:
        full_tokens: list[TokenValue] = [effective_bkcc, *key_parts]
    else:
        full_tokens = [*key_parts]

    return compute_dv2_length_prefixed_sha256(full_tokens, is_business_key=True)


__all__ = [
    "TokenValue",
    "compute_dv2_length_prefixed_sha256",
    "compute_dv2_prehash_string",
    "dv2_canonical_hash",
    "encode_dv2_token",
    "resolve_fk_hash_key_with_zero_keys",
]
