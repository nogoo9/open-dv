"""Pre-masking ColumnShapeStats profiler shared by Step 2 and Module 1A (Layer-2a)."""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Sequence
from typing import Any, Final

from opendv.grounding.kernel.pii_core import (
    MASKED_PII_TOKEN,
    detect_pii_in_column_name,
    detect_pii_in_value,
)
from opendv.grounding.structured.validators import compute_structural_patterns
from opendv.models.sources import CanonicalSentinel, ColumnShapeStats, SurrogateKind

_STRING_SENTINEL_TOKENS: Final[tuple[CanonicalSentinel, ...]] = (
    "",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "UNKNOWN",
    "TBD",
)
_STRING_SENTINEL_SET: Final[frozenset[str]] = frozenset(_STRING_SENTINEL_TOKENS)
_CANONICAL_SENTINEL_ORDER: Final[tuple[CanonicalSentinel, ...]] = (
    "",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "UNKNOWN",
    "TBD",
    "-1",
    "-2",
)
_NEG_SENTINEL_RATIO_CAP: Final[float] = 0.10
_ENUM_MAX_DISTINCT: Final[int] = 30
_ENUM_MAX_DISTINCT_RATIO: Final[float] = 0.20
_SURROGATE_MIN_COUNT: Final[int] = 10
_SURROGATE_MIN_RATIO: Final[float] = 0.90

_CODE_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9][A-Z0-9_\-./+]{0,15}$")
_NON_ZPAD_INT_RE: Final[re.Pattern[str]] = re.compile(r"^-?(?:0|[1-9]\d*)$")
_PREFIXED_OP_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[A-Za-z]{1,12}(?:[-_][A-Za-z0-9]{1,12})*[-_]\d{2,12}|[A-Za-z]{2,8}\d{4,12})$"
)
_SQL_BOOL_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:BOOL|BOOLEAN)\b")
_SQL_FLOAT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:DOUBLE|FLOAT|FLOAT4|FLOAT8|FLOAT32|FLOAT64|REAL|DECIMAL|NUMERIC)\b"
)
_SQL_NUMERIC_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:TINYINT|SMALLINT|MEDIUMINT|INT|INTEGER|BIGINT|"
    r"INT2|INT4|INT8|INT16|INT32|INT64|INT128|"
    r"UINT8|UINT16|UINT32|UINT64|UINT128|"
    r"FLOAT|FLOAT4|FLOAT8|FLOAT32|FLOAT64|DOUBLE|REAL|DECIMAL|NUMERIC|NUMBER)\b"
)
_STANDARD_FINANCIAL_KEYS: Final[tuple[str, ...]] = (
    "iso_6166_isin",
    "figi",
    "iban",
    "swift_bic",
    "sedol",
)


def _parse_finite_float(text: str) -> float | None:
    try:
        val = float(text.strip())
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def _strip_sentinels(
    unmasked_strs: Sequence[str],
    *,
    is_continuous_float: bool,
    sql_is_bool: bool,
) -> tuple[list[str], list[CanonicalSentinel]]:
    found_set: set[CanonicalSentinel] = set()
    after_str: list[str] = []
    for val in unmasked_strs:
        norm = val.strip().upper()
        if norm in _STRING_SENTINEL_SET:
            for cand in _STRING_SENTINEL_TOKENS:
                if cand == norm:
                    found_set.add(cand)
                    break
        else:
            after_str.append(val)

    clean_values = after_str
    if not is_continuous_float and not sql_is_bool and after_str:
        neg_hits = [s for s in after_str if s.strip() in ("-1", "-2")]
        if neg_hits and (len(neg_hits) / len(after_str)) <= _NEG_SENTINEL_RATIO_CAP:
            remaining = [s for s in after_str if s.strip() not in ("-1", "-2")]
            has_positive_domain = any(
                (parsed is None or parsed > 0.0)
                for parsed in (_parse_finite_float(r) for r in remaining)
            )
            if remaining and has_positive_domain:
                for tok in ("-1", "-2"):
                    if any(s.strip() == tok for s in neg_hits):
                        found_set.add(tok)
                clean_values = remaining

    ordered_sentinels = [s for s in _CANONICAL_SENTINEL_ORDER if s in found_set]
    return (clean_values, ordered_sentinels)


def _infer_surrogate_kind(
    clean_values: Sequence[str],
    *,
    distinct_ratio: float,
    is_continuous_float: bool,
    structural_patterns: dict[str, float],
) -> SurrogateKind:
    n = len(clean_values)
    if n < _SURROGATE_MIN_COUNT:
        return "none"
    if structural_patterns.get("uuid_v4", 0.0) >= _SURROGATE_MIN_RATIO:
        return "random_uuid_v4"

    if not is_continuous_float and distinct_ratio >= _SURROGATE_MIN_RATIO:
        parsed_ints = [
            int(v.strip())
            for v in clean_values
            if _NON_ZPAD_INT_RE.fullmatch(v.strip())
        ]
        if len(parsed_ints) / float(n) >= _SURROGATE_MIN_RATIO and len(parsed_ints) >= 2:
            inc_pairs = sum(1 for a, b in itertools.pairwise(parsed_ints) if b > a)
            if inc_pairs / float(len(parsed_ints) - 1) >= _SURROGATE_MIN_RATIO:
                return "monotonic_int"

    max_fin_pat = max(
        (structural_patterns.get(k, 0.0) for k in _STANDARD_FINANCIAL_KEYS),
        default=0.0,
    )
    if max_fin_pat < _SURROGATE_MIN_RATIO:
        prefixed_count = sum(
            1 for v in clean_values if _PREFIXED_OP_ID_RE.fullmatch(v.strip())
        )
        if prefixed_count / float(n) >= _SURROGATE_MIN_RATIO:
            return "prefixed_operational_id"

    return "none"


def _compute_numeric_bounds(
    clean_values: Sequence[str],
    *,
    sql_is_bool: bool,
    sql_is_numeric: bool,
) -> tuple[float | None, float | None]:
    if not clean_values or sql_is_bool:
        return (None, None)
    if all(v.strip().lower() in ("true", "false") for v in clean_values):
        return (None, None)
    if not sql_is_numeric:
        for v in clean_values:
            s = v.strip()
            if len(s) > 1 and s[0] == "0" and s.isdigit():
                return (None, None)
    nums: list[float] = []
    for v in clean_values:
        parsed = _parse_finite_float(v)
        if parsed is None:
            return (None, None)
        nums.append(parsed)
    return (min(nums), max(nums))


def compute_column_shape_stats(
    samples: Sequence[Any],
    *,
    col_name: str = "",
    sql_type: str = "VARCHAR",
    pii_tags: Sequence[str] = (),
    row_count_est: int | None = None,
    distinct_count_est: int | None = None,
    null_ratio: float | None = None,
) -> ColumnShapeStats:
    """Compute pre-masking ColumnShapeStats from up to 200 raw sample values."""
    sql_upper = sql_type.strip().upper()
    sql_is_bool = bool(_SQL_BOOL_RE.search(sql_upper))
    sql_is_float = bool(_SQL_FLOAT_RE.search(sql_upper))
    sql_is_numeric = sql_is_float or bool(_SQL_NUMERIC_RE.search(sql_upper))

    non_null_raw = [v for v in samples if v is not None and str(v) != MASKED_PII_TOKEN]
    has_masked_token = any(v is not None and str(v) == MASKED_PII_TOKEN for v in samples)
    unmasked_strs = [str(v) for v in non_null_raw]

    is_continuous_float = (
        sql_is_float
        or any(isinstance(v, float) for v in non_null_raw)
        or any("." in s and _parse_finite_float(s) is not None for s in unmasked_strs)
    )

    pii_suppressed = (
        bool(pii_tags)
        or has_masked_token
        or bool(detect_pii_in_column_name(col_name))
        or any(detect_pii_in_value(s, col_name=col_name) for s in unmasked_strs)
    )

    clean_values, detected_sentinels = _strip_sentinels(
        unmasked_strs,
        is_continuous_float=is_continuous_float,
        sql_is_bool=sql_is_bool,
    )
    n_clean = len(clean_values)
    if n_clean == 0:
        return ColumnShapeStats(
            profiled_non_null_count=0,
            distinct_ratio=0.0,
            detected_sentinels=detected_sentinels,
            pii_suppressed=pii_suppressed,
        )

    unique_clean = sorted(set(clean_values))
    if row_count_est is not None and row_count_est > 0 and distinct_count_est is not None:
        non_null_est = float(row_count_est) * (1.0 - float(null_ratio or 0.0))
        distinct_ratio = (
            min(1.0, max(0.0, float(distinct_count_est) / non_null_est))
            if non_null_est > 0.0
            else 0.0
        )
        eff_distinct = distinct_count_est
    else:
        distinct_ratio = min(1.0, max(0.0, float(len(unique_clean)) / float(n_clean)))
        eff_distinct = len(unique_clean)

    looks_like_enum = bool(
        not is_continuous_float
        and not sql_is_bool
        and eff_distinct <= _ENUM_MAX_DISTINCT
        and distinct_ratio <= _ENUM_MAX_DISTINCT_RATIO
    )

    observed_enum_values: list[str] = []
    if (
        not pii_suppressed
        and looks_like_enum
        and len(unique_clean) <= _ENUM_MAX_DISTINCT
        and all(
            _CODE_TOKEN_RE.fullmatch(v)
            and not (v.isdigit() and len(v) >= 6)
            and not detect_pii_in_value(v, col_name=col_name)
            for v in unique_clean
        )
    ):
        observed_enum_values = unique_clean

    structural_patterns = compute_structural_patterns(clean_values)
    surrogate_kind = _infer_surrogate_kind(
        clean_values,
        distinct_ratio=distinct_ratio,
        is_continuous_float=is_continuous_float,
        structural_patterns=structural_patterns,
    )

    if pii_suppressed:
        length_min, length_max = None, None
        numeric_min, numeric_max = None, None
    else:
        length_min = min(len(v) for v in clean_values)
        length_max = max(len(v) for v in clean_values)
        numeric_min, numeric_max = _compute_numeric_bounds(
            clean_values,
            sql_is_bool=sql_is_bool,
            sql_is_numeric=sql_is_numeric,
        )

    return ColumnShapeStats(
        profiled_non_null_count=n_clean,
        distinct_ratio=distinct_ratio,
        length_min=length_min,
        length_max=length_max,
        numeric_min=numeric_min,
        numeric_max=numeric_max,
        looks_like_enum=looks_like_enum,
        observed_enum_values=observed_enum_values,
        surrogate_kind=surrogate_kind,
        structural_patterns=structural_patterns,
        detected_sentinels=detected_sentinels,
        pii_suppressed=pii_suppressed,
    )


__all__ = ["compute_column_shape_stats"]
