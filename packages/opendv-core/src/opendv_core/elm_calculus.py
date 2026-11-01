"""ELM (Ensemble Logical Modeling) Information-Theoretic & Temporal Calculus.

Implements the formal mathematical profiling metrics from
docs/05-elm-calculus-and-reference-algorithms.md (§1, §3.1-§3.3):
- Uniqueness ratio U(C) and non-null uniqueness ratio U_nn(C)
- Null rate N_null(C) and non-null completeness ratio (1 - N_null(C))
- Shannon entropy H(C) in bits and normalized Shannon entropy H_norm(C) in [0, 1]
- Jaccard similarity J(A, B), inclusion I(A.x <= B.y), and containment C(A, B)
- CDC temporal change velocity lambda(c) and log-volatility v(c)
- Daily storage write amplification W_amp (bytes/day and MB/day) and Gate 4 YAGNI check
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

BYTES_PER_MIB: float = float(1024 * 1024)
BYTES_PER_MB_DECIMAL: float = 1_000_000.0


def _is_null(value: Any) -> bool:
    """Return True if value is None or NaN float."""
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def compute_null_rate(values: Sequence[Any]) -> float:
    """Compute column null rate N_null(C) = |{r | pi_C(r) is NULL}| / N.

    Returns 1.0 for an empty sequence.
    """
    total = len(values)
    if total == 0:
        return 1.0
    null_count = sum(1 for v in values if _is_null(v))
    return null_count / total


def compute_non_null_ratio(
    values: Sequence[Any] | None = None,
    *,
    null_rate: float | None = None,
) -> float:
    """Compute non-null completeness ratio 1.0 - N_null(C) = N_non_null / N.

    Can be called with either a sequence of `values` or a pre-computed `null_rate`.
    """
    if null_rate is not None:
        return max(0.0, min(1.0, 1.0 - null_rate))
    if values is None:
        msg = "Either values or null_rate must be provided"
        raise ValueError(msg)
    if len(values) == 0:
        return 0.0
    return 1.0 - compute_null_rate(values)


def compute_uniqueness_ratio(
    values: Sequence[Any],
    *,
    over_non_null: bool = True,
) -> float:
    """Compute column uniqueness ratio U(C) or U_nn(C).

    Args:
        values: Column values from sample or table scan.
        over_non_null: If True (default), computes U_nn(C) = |DISTINCT non-null| / N_non_null
            (docs/05 §1.1 Eq. 2 & §3.1). If False, computes U(C) = |DISTINCT non-null| / N_total
            (docs/05 §1.1 Eq. 1).
    """
    total = len(values)
    if total == 0:
        return 0.0
    non_null_vals = [v for v in values if not _is_null(v)]
    if len(non_null_vals) == 0:
        return 0.0
    distinct_count = len(set(non_null_vals))
    denominator = len(non_null_vals) if over_non_null else total
    return distinct_count / denominator


def compute_u_c(values: Sequence[Any]) -> float:
    """Compute total uniqueness ratio U(C) = |DISTINCT non-null| / N_total (Eq. 1)."""
    return compute_uniqueness_ratio(values, over_non_null=False)


def compute_u_nn(values: Sequence[Any]) -> float:
    """Compute non-null uniqueness ratio U_nn(C) = |DISTINCT non-null| / N_non_null (Eq. 2)."""
    return compute_uniqueness_ratio(values, over_non_null=True)


def compute_shannon_entropy(values: Sequence[Any]) -> float:
    """Compute Shannon entropy H(C) = -sum p(v) * log2(p(v)) in bits over non-null values."""
    non_null_vals = [v for v in values if not _is_null(v)]
    n = len(non_null_vals)
    if n <= 1:
        return 0.0
    counts = Counter(non_null_vals)
    entropy = -sum((count / n) * math.log2(count / n) for count in counts.values())
    return max(0.0, entropy)


def compute_normalized_shannon_entropy(
    values: Sequence[Any],
    *,
    normalize_by_distinct: bool = False,
) -> float:
    """Compute normalized Shannon entropy H_norm(C) in [0.0, 1.0].

    Args:
        values: Column values from sample or table scan.
        normalize_by_distinct: If False (default, matching docs/05 §3.1), normalizes by
            log2(N_non_null) so columns with duplicate values score < 1.0 and candidate
            business keys with all unique values score 1.0. If True (docs/05 §1.1 Eq. 4),
            normalizes by log2(|DISTINCT non-null|).
    """
    non_null_vals = [v for v in values if not _is_null(v)]
    n = len(non_null_vals)
    if n <= 1:
        return 0.0
    distinct_count = len(set(non_null_vals))
    if distinct_count <= 1:
        return 0.0
    h_bits = compute_shannon_entropy(non_null_vals)
    denom_count = distinct_count if normalize_by_distinct else n
    max_entropy = math.log2(denom_count)
    if max_entropy <= 0.0:
        return 0.0
    return max(0.0, min(1.0, h_bits / max_entropy))


def compute_jaccard_similarity(
    col_a: Iterable[Any],
    col_b: Iterable[Any],
) -> float:
    """Compute symmetric Jaccard similarity J(A, B) = |V_a & V_b| / |V_a | V_b|."""
    set_a = {v for v in col_a if not _is_null(v)}
    set_b = {v for v in col_b if not _is_null(v)}
    union_size = len(set_a | set_b)
    if union_size == 0:
        return 0.0
    return len(set_a & set_b) / union_size


def compute_jaccard_inclusion(
    child_values: Iterable[Any],
    parent_values: Iterable[Any],
) -> float:
    """Compute directional FK inclusion I(A.x <= B.y) = |V(A.x) & V(B.y)| / |V(A.x)|.

    Measures the fraction of distinct non-null child column values that exist in the
    parent candidate key domain (docs/05 §1.2 Eq. 5).
    """
    child_set = {v for v in child_values if not _is_null(v)}
    if len(child_set) == 0:
        return 0.0
    parent_set = {v for v in parent_values if not _is_null(v)}
    return len(child_set & parent_set) / len(child_set)


def compute_asymmetric_containment(
    col_a: Iterable[Any],
    col_b: Iterable[Any],
) -> float:
    """Compute asymmetric containment C(A, B) = |V_a & V_b| / min(|V_a|, |V_b|) (docs/05 §3.1)."""
    set_a = {v for v in col_a if not _is_null(v)}
    set_b = {v for v in col_b if not _is_null(v)}
    min_size = min(len(set_a), len(set_b))
    if min_size == 0:
        return 0.0
    return len(set_a & set_b) / min_size


def compute_cdc_velocity(
    cdc_update_count: int | float,
    entity_count: int | float = 1,
    delta_t_days: float = 1.0,
) -> float:
    """Compute CDC temporal change velocity lambda(c) in changes/entity/day (docs/05 §1.3 Eq. 6)."""
    if entity_count <= 0 or delta_t_days <= 0:
        msg = "entity_count and delta_t_days must be positive"
        raise ValueError(msg)
    if cdc_update_count < 0:
        msg = "cdc_update_count must be non-negative"
        raise ValueError(msg)
    return float(cdc_update_count) / (float(entity_count) * float(delta_t_days))


def compute_log_volatility(
    lambda_per_day: float,
    epsilon: float = 1e-6,
) -> float:
    """Compute log-scaled volatility v(c) = log10(lambda(c) + epsilon) (docs/05 §1.3 Eq. 7)."""
    if lambda_per_day < 0:
        msg = "lambda_per_day must be non-negative"
        raise ValueError(msg)
    return math.log10(lambda_per_day + epsilon)


def compute_write_amplification_bytes_per_day(
    daily_rows_changed: int | float,
    slow_payload_width_bytes: int | float,
    high_freq_change_probability: float = 1.0,
) -> float:
    """Compute daily storage write amplification W_amp in bytes/day (docs/05 §1.3 Eq. 8).

    Formula:
        W_amp (bytes/day) = daily_rows_changed * P(Delta c_fast != 0) * sum(width_bytes(c_slow))

    In the Private Equity benchmark (`pe_graph_b_profile.json`):
        157,000 rows/day * 0.85 * (18 + 15) bytes = 4,403,850 bytes/day.
    """
    if daily_rows_changed < 0 or slow_payload_width_bytes < 0:
        msg = "daily_rows_changed and slow_payload_width_bytes must be non-negative"
        raise ValueError(msg)
    if not (0.0 <= high_freq_change_probability <= 1.0):
        msg = "high_freq_change_probability must be in [0.0, 1.0]"
        raise ValueError(msg)
    return (
        float(daily_rows_changed)
        * float(high_freq_change_probability)
        * float(slow_payload_width_bytes)
    )


def compute_write_amplification_mb_per_day(
    daily_rows_changed: int | float,
    slow_payload_width_bytes: int | float,
    high_freq_change_probability: float = 1.0,
    *,
    base_1024: bool = True,
    precision: int | None = None,
) -> float:
    """Compute daily storage write amplification W_amp in MB/day (docs/05 §1.3 Eq. 8).

    By default uses binary MiB (`1024 * 1024 = 1,048,576` bytes), yielding `4.20 MB/day`
    for the Private Equity demo (`157_000 * 0.85 * 33 = 4,403,850 bytes / 1,048,576 = 4.20`).
    """
    bytes_per_day = compute_write_amplification_bytes_per_day(
        daily_rows_changed=daily_rows_changed,
        slow_payload_width_bytes=slow_payload_width_bytes,
        high_freq_change_probability=high_freq_change_probability,
    )
    divisor = BYTES_PER_MIB if base_1024 else BYTES_PER_MB_DECIMAL
    mb_per_day = bytes_per_day / divisor
    if precision is not None:
        return round(mb_per_day, precision)
    return mb_per_day


def evaluate_yagni_satellite_split(
    w_amp_mb_per_day: float,
    *,
    velocity_ratio: float | None = None,
    min_w_amp_mb_per_day: float = 1.0,
    min_velocity_ratio: float = 100.0,
) -> bool:
    """Evaluate Gate 4 (`G4_GRAIN_YAGNI`) Satellite split justification.

    Returns True if a multi-satellite velocity split is mathematically justified:
    - `w_amp_mb_per_day >= min_w_amp_mb_per_day` (default 1.0 MB/day), AND
    - if `velocity_ratio` (lambda_fast / lambda_slow) is provided,
      `velocity_ratio >= min_velocity_ratio` (default 100.0).
    """
    if w_amp_mb_per_day < min_w_amp_mb_per_day:
        return False
    return not (velocity_ratio is not None and velocity_ratio < min_velocity_ratio)


__all__ = [
    "BYTES_PER_MB_DECIMAL",
    "BYTES_PER_MIB",
    "compute_asymmetric_containment",
    "compute_cdc_velocity",
    "compute_jaccard_inclusion",
    "compute_jaccard_similarity",
    "compute_log_volatility",
    "compute_non_null_ratio",
    "compute_normalized_shannon_entropy",
    "compute_null_rate",
    "compute_shannon_entropy",
    "compute_u_c",
    "compute_u_nn",
    "compute_uniqueness_ratio",
    "compute_write_amplification_bytes_per_day",
    "compute_write_amplification_mb_per_day",
    "evaluate_yagni_satellite_split",
]
