"""Similarity, Value-Jaccard, and Type-Compatibility Helpers for Algorithm A.9a."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from opendv_core.config import get_core_modeling_config

_VARCHAR_RE = re.compile(r"^(?:VAR)?CHAR\s*\(\s*(\d+)\s*\)$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^(?:NUMERIC|DECIMAL)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_INT_RANK: dict[str, int] = {
    "TINYINT": 1,
    "SMALLINT": 2,
    "INT": 3,
    "INTEGER": 3,
    "BIGINT": 4,
    "HUGEINT": 5,
}
_TYPE_FAMILIES: dict[str, str] = {
    "TINYINT": "NUMERIC",
    "SMALLINT": "NUMERIC",
    "INT": "NUMERIC",
    "INTEGER": "NUMERIC",
    "BIGINT": "NUMERIC",
    "HUGEINT": "NUMERIC",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "DOUBLE": "NUMERIC",
    "FLOAT": "NUMERIC",
    "REAL": "NUMERIC",
    "VARCHAR": "STRING",
    "CHAR": "STRING",
    "TEXT": "STRING",
    "STRING": "STRING",
    "DATE": "TEMPORAL",
    "TIMESTAMP": "TEMPORAL",
    "TIMESTAMPTZ": "TEMPORAL",
    "DATETIME": "TEMPORAL",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
}


def levenshtein_distance(s1: str, s2: str) -> int:
    a, b = s1.lower(), s2.lower()
    if a == b:
        return 0
    if not a or not b:
        return len(b) or len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    m = max(len(s1), len(s2))
    return 1.0 if m == 0 else 1.0 - (levenshtein_distance(s1, s2) / m)


def is_lossless_type_widening(old_type: str, new_type: str) -> bool:
    ou, nu = old_type.strip().upper(), new_type.strip().upper()
    if ou == nu:
        return False
    mo, mn = _VARCHAR_RE.match(ou), _VARCHAR_RE.match(nu)
    if mo and mn:
        return int(mn.group(1)) > int(mo.group(1))
    if mo and nu in {"VARCHAR", "TEXT", "STRING"}:
        return True
    if ou in _INT_RANK and nu in _INT_RANK:
        return _INT_RANK[nu] > _INT_RANK[ou]
    mno, mnn = _NUMERIC_RE.match(ou), _NUMERIC_RE.match(nu)
    if mno and mnn:
        p1, s1 = int(mno.group(1)), int(mno.group(2))
        p2, s2 = int(mnn.group(1)), int(mnn.group(2))
        return (p2 > p1 or s2 > s1) and s2 >= s1 and (p2 - s2) >= (p1 - s1)
    return ou == "DATE" and nu in {"TIMESTAMP", "TIMESTAMPTZ"}


def are_types_compatible(old_type: str | None, new_type: str | None) -> bool:
    if not old_type or not new_type:
        return True
    if old_type.strip().upper() == new_type.strip().upper():
        return True
    return is_lossless_type_widening(old_type, new_type)


def _type_family(sql_type: str | None) -> str | None:
    if not sql_type:
        return None
    base = sql_type.strip().upper().split("(", 1)[0].strip()
    return _TYPE_FAMILIES.get(base)


def _tokenize_col(name: str) -> list[str]:
    snake = _CAMEL_RE.sub("_", name).lower()
    return [tok for tok in re.split(r"[^a-z0-9]+", snake) if tok]


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _token_similarity(t1: str, t2: str) -> float:
    if t1 == t2:
        return 1.0
    shorter, longer = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    if len(shorter) >= 3 and longer.startswith(shorter):
        return 0.92
    if len(shorter) >= 2 and shorter[0] == longer[0] and _is_subsequence(shorter, longer):
        return 0.90
    return levenshtein_similarity(t1, t2)


def structural_column_similarity(
    old_col: str,
    new_col: str,
    old_type: str | None = None,
    new_type: str | None = None,
) -> float:
    """Compute token/abbreviation + SQL-type-family similarity for rename fallback."""
    if not are_types_compatible(old_type, new_type):
        return 0.0
    old_tokens = _tokenize_col(old_col)
    new_tokens = _tokenize_col(new_col)
    if not old_tokens or not new_tokens:
        return 0.0
    pairs: list[tuple[float, int, int]] = [
        (_token_similarity(ot, nt), i, j)
        for i, ot in enumerate(old_tokens)
        for j, nt in enumerate(new_tokens)
    ]
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_old: set[int] = set()
    used_new: set[int] = set()
    matched_sum = 0.0
    for score, i, j in pairs:
        if i not in used_old and j not in used_new:
            used_old.add(i)
            used_new.add(j)
            matched_sum += score
    dice = (2.0 * matched_sum) / (len(old_tokens) + len(new_tokens))
    fam_old, fam_new = _type_family(old_type), _type_family(new_type)
    if fam_old is not None and fam_old == fam_new and dice > 0.4:
        return min(1.0, dice + 0.05)
    return dice


def _normalize_value_set(values: Iterable[Any], max_values: int) -> set[str]:
    out: set[str] = set()
    for val in values:
        if val is None:
            continue
        cleaned = str(val).strip()
        if cleaned:
            out.add(cleaned)
            if len(out) >= max_values:
                break
    return out


def compute_value_jaccard(
    old_values: Mapping[str, Iterable[Any]],
    new_values: Mapping[str, Iterable[Any]],
    *,
    max_values: int | None = None,
) -> dict[tuple[str, str], float]:
    """Compute bounded per-column-pair value Jaccard overlaps keyed by `(old_col, new_col)`."""
    cap = max_values or get_core_modeling_config().max_jaccard_profile_values_per_col
    old_sets = {str(k): _normalize_value_set(v, cap) for k, v in old_values.items()}
    new_sets = {str(k): _normalize_value_set(v, cap) for k, v in new_values.items()}
    result: dict[tuple[str, str], float] = {}
    for oc, oset in old_sets.items():
        for nc, nset in new_sets.items():
            if not oset or not nset:
                result[(oc, nc)] = 0.0
            else:
                union_sz = len(oset | nset)
                result[(oc, nc)] = (len(oset & nset) / union_sz) if union_sz else 0.0
    return result


def best_rename_match(
    old_col: str,
    candidates: Iterable[str],
    *,
    matched_new: set[str],
    old_types: Mapping[str, str],
    new_types: Mapping[str, str],
    value_jaccard: Mapping[tuple[str, str], float],
    lev_threshold: float,
    jaccard_threshold: float,
    structural_threshold: float,
) -> tuple[str | None, float]:
    """Return `(best_new_col, best_similarity)` for `old_col` across candidate added columns."""
    best_new: str | None = None
    best_sc = 0.0
    ot = old_types.get(old_col)
    for nc in candidates:
        nt = new_types.get(nc)
        if nc in matched_new or not are_types_compatible(ot, nt):
            continue
        lev_sc = levenshtein_similarity(old_col, nc)
        has_jac = (old_col, nc) in value_jaccard or (nc, old_col) in value_jaccard
        jac_sc = value_jaccard.get((old_col, nc), value_jaccard.get((nc, old_col), 0.0))
        struct_sc = structural_column_similarity(old_col, nc, ot, nt)
        allow_struct = (not has_jac) or (jac_sc > 0.0)
        matched = (
            lev_sc >= lev_threshold
            or jac_sc >= jaccard_threshold
            or (allow_struct and struct_sc >= structural_threshold)
        )
        sc = max(lev_sc, jac_sc, struct_sc if allow_struct else 0.0)
        if matched and sc > best_sc:
            best_sc, best_new = sc, nc
    return best_new, best_sc


def merge_satellite_column_aliases(
    dep_sat: Mapping[str, Any],
    sat: Mapping[str, Any],
    staging_aliases: Any,
    findings: Iterable[Any],
) -> dict[str, str]:
    """Merge existing, staging, and newly detected `{old_col: new_col}` aliases."""
    merged: dict[str, str] = {}
    for source in (dep_sat.get("column_aliases"), sat.get("column_aliases"), staging_aliases):
        if isinstance(source, Mapping):
            merged.update({str(k): str(v) for k, v in source.items() if k and v})
    for f in findings:
        if getattr(f, "category", None) == "RENAMED" and getattr(f, "new_column_name", None):
            merged[str(f.column_name)] = str(f.new_column_name)
    return merged


__all__ = [
    "are_types_compatible",
    "best_rename_match",
    "compute_value_jaccard",
    "is_lossless_type_widening",
    "levenshtein_distance",
    "levenshtein_similarity",
    "merge_satellite_column_aliases",
    "structural_column_similarity",
]
