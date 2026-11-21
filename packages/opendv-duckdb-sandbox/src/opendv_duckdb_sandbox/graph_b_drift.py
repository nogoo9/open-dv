"""Schema Drift Detection ($S_k(t) \\to S_k(t+1)$) for Graph B DuckDB Profiler."""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.elm_calculus import compute_jaccard_similarity


class SchemaDriftKind(StrEnum):
    """Taxonomy of source schema drift transitions $S_k(t) \\to S_k(t+1)$ (`docs/05` §3.9)."""

    ADD_COLUMN = "ADD_COLUMN"
    DROP_COLUMN = "DROP_COLUMN"
    RENAME_COLUMN = "RENAME_COLUMN"
    TYPE_WIDENING = "TYPE_WIDENING"
    SEMANTIC_TYPE_CHANGE = "SEMANTIC_TYPE_CHANGE"


class SchemaDriftEvent(BaseModel):
    """Single detected schema drift transition between $S_k(t)$ and $S_k(t+1)$."""

    model_config = ConfigDict(extra="forbid")

    drift_kind: SchemaDriftKind
    source_table: str
    column_name: str
    previous_column_name: str | None = None
    previous_type: str | None = None
    new_type: str | None = None
    jaccard_value_overlap: float | None = Field(default=None, ge=0.0, le=1.0)
    recommended_action: str


class SchemaDriftReport(BaseModel):
    """Aggregate report of all $S_k(t) \\to S_k(t+1)$ schema drift events for a table."""

    model_config = ConfigDict(extra="forbid")

    source_table: str
    has_drift: bool
    events: list[SchemaDriftEvent] = Field(default_factory=list)


def _normalized_base_type(type_str: str) -> tuple[str, tuple[int, ...]]:
    """Parse a SQL type string into its base family and numeric precision/length tuple."""
    cleaned = type_str.strip().upper()
    match = re.match(r"^([A-Z0-9_ ]+?)(?:\(([\d,\s]+)\))?$", cleaned)
    if not match:
        return cleaned, ()
    base = match.group(1).strip()
    params_raw = match.group(2)
    params = (
        tuple(int(part.strip()) for part in params_raw.split(",") if part.strip().isdigit())
        if params_raw
        else ()
    )
    return base, params


_WIDENING_FAMILIES: dict[str, int] = {
    "TINYINT": 1,
    "SMALLINT": 2,
    "INT": 3,
    "INTEGER": 3,
    "INT4": 3,
    "BIGINT": 4,
    "INT8": 4,
    "HUGEINT": 5,
    "FLOAT": 6,
    "REAL": 6,
    "DOUBLE": 7,
}

_STRING_FAMILIES = {"CHAR", "CHARACTER", "VARCHAR", "TEXT", "STRING"}
_DECIMAL_FAMILIES = {"DECIMAL", "NUMERIC"}


def _classify_type_transition(old_type: str, new_type: str) -> SchemaDriftKind | None:
    """Classify a column type change as None (identical), TYPE_WIDENING, or SEMANTIC_TYPE_CHANGE."""
    if old_type.strip().upper() == new_type.strip().upper():
        return None
    old_base, old_params = _normalized_base_type(old_type)
    new_base, new_params = _normalized_base_type(new_type)

    if old_base in _STRING_FAMILIES and new_base in _STRING_FAMILIES:
        if not new_params:
            return SchemaDriftKind.TYPE_WIDENING
        if old_params and new_params and new_params[0] >= old_params[0]:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    if old_base in _DECIMAL_FAMILIES and new_base in _DECIMAL_FAMILIES:
        if len(old_params) >= 2 and len(new_params) >= 2:
            if new_params[0] >= old_params[0] and new_params[1] >= old_params[1]:
                return SchemaDriftKind.TYPE_WIDENING
        elif not old_params or not new_params:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    if old_base in _WIDENING_FAMILIES and new_base in _WIDENING_FAMILIES:
        if _WIDENING_FAMILIES[new_base] > _WIDENING_FAMILIES[old_base]:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    return SchemaDriftKind.SEMANTIC_TYPE_CHANGE


def detect_schema_drift(
    conn: duckdb.DuckDBPyConnection,
    table_t0: str,
    table_t1: str,
    *,
    quote_identifier: Callable[..., str],
    rename_jaccard_threshold: float = 0.90,
) -> SchemaDriftReport:
    """Detect $S_k(t) \\to S_k(t+1)$ schema drift between two DuckDB tables/views."""
    q_t0 = quote_identifier(table_t0)
    q_t1 = quote_identifier(table_t1)

    cols_t0 = {str(r[0]): str(r[1]) for r in conn.execute(f"DESCRIBE {q_t0}").fetchall()}
    cols_t1 = {str(r[0]): str(r[1]) for r in conn.execute(f"DESCRIBE {q_t1}").fetchall()}

    removed_candidates = [c for c in cols_t0 if c not in cols_t1]
    added_candidates = [c for c in cols_t1 if c not in cols_t0]

    events: list[SchemaDriftEvent] = []
    matched_removed: set[str] = set()
    matched_added: set[str] = set()

    for add_col in added_candidates:
        q_add = quote_identifier(add_col, allow_qualified=False)
        add_vals = {
            str(r[0])
            for r in conn.execute(
                f"SELECT {q_add} FROM {q_t1} WHERE {q_add} IS NOT NULL"
            ).fetchall()
        }
        if not add_vals:
            continue
        best_old: str | None = None
        best_jaccard = 0.0
        for rem_col in removed_candidates:
            if rem_col in matched_removed:
                continue
            q_rem = quote_identifier(rem_col, allow_qualified=False)
            rem_vals = {
                str(r[0])
                for r in conn.execute(
                    f"SELECT {q_rem} FROM {q_t0} WHERE {q_rem} IS NOT NULL"
                ).fetchall()
            }
            jaccard = compute_jaccard_similarity(rem_vals, add_vals)
            if jaccard >= rename_jaccard_threshold and jaccard > best_jaccard:
                best_jaccard = jaccard
                best_old = rem_col
        if best_old is not None:
            matched_removed.add(best_old)
            matched_added.add(add_col)
            events.append(
                SchemaDriftEvent(
                    drift_kind=SchemaDriftKind.RENAME_COLUMN,
                    source_table=table_t1,
                    column_name=add_col,
                    previous_column_name=best_old,
                    previous_type=cols_t0[best_old],
                    new_type=cols_t1[add_col],
                    jaccard_value_overlap=round(best_jaccard, 4),
                    recommended_action=(
                        f"Aliasstaging column {add_col!r} -> {best_old!r} in Zone 1 staging "
                        "view to preserve downstream Satellite hash_diff continuity."
                    ),
                )
            )

    for add_col in added_candidates:
        if add_col in matched_added:
            continue
        events.append(
            SchemaDriftEvent(
                drift_kind=SchemaDriftKind.ADD_COLUMN,
                source_table=table_t1,
                column_name=add_col,
                new_type=cols_t1[add_col],
                recommended_action=(
                    f"Isolate {add_col!r} ({cols_t1[add_col]}) into an additive extension "
                    "Satellite (Algorithm A.9a) so existing Satellite hash_diff history "
                    "remains immutable."
                ),
            )
        )

    for rem_col in removed_candidates:
        if rem_col in matched_removed:
            continue
        events.append(
            SchemaDriftEvent(
                drift_kind=SchemaDriftKind.DROP_COLUMN,
                source_table=table_t1,
                column_name=rem_col,
                previous_type=cols_t0[rem_col],
                recommended_action=(
                    f"Project CAST(NULL AS {cols_t0[rem_col]}) AS {rem_col} in Zone 1 "
                    "staging view; never DROP physical columns from historical Satellites."
                ),
            )
        )

    for shared_col in cols_t0:
        if shared_col not in cols_t1:
            continue
        transition = _classify_type_transition(cols_t0[shared_col], cols_t1[shared_col])
        if transition == SchemaDriftKind.TYPE_WIDENING:
            events.append(
                SchemaDriftEvent(
                    drift_kind=SchemaDriftKind.TYPE_WIDENING,
                    source_table=table_t1,
                    column_name=shared_col,
                    previous_type=cols_t0[shared_col],
                    new_type=cols_t1[shared_col],
                    recommended_action=(
                        f"Apply non-destructive ALTER COLUMN {shared_col} SET DATA TYPE "
                        f"{cols_t1[shared_col]} in Satellite; length-prefixed hash_diff "
                        "tokenization remains invariant."
                    ),
                )
            )
        elif transition == SchemaDriftKind.SEMANTIC_TYPE_CHANGE:
            events.append(
                SchemaDriftEvent(
                    drift_kind=SchemaDriftKind.SEMANTIC_TYPE_CHANGE,
                    source_table=table_t1,
                    column_name=shared_col,
                    previous_type=cols_t0[shared_col],
                    new_type=cols_t1[shared_col],
                    recommended_action=(
                        f"Escalate incompatible type change on {shared_col!r} "
                        f"({cols_t0[shared_col]} -> {cols_t1[shared_col]}) to a new versioned "
                        "Satellite column."
                    ),
                )
            )

    return SchemaDriftReport(
        source_table=table_t1,
        has_drift=len(events) > 0,
        events=events,
    )


__all__ = [
    "SchemaDriftEvent",
    "SchemaDriftKind",
    "SchemaDriftReport",
    "_classify_type_transition",
    "_normalized_base_type",
    "detect_schema_drift",
]
