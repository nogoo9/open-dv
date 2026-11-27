"""Bounded-timeout threadpool profiler with schema fingerprints, metrics, & PII tokenization."""

from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import itertools
import math
import re
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from opendv.grounding.structured.profiler import compute_column_shape_stats
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import (
    SURROGATE_BK_RE,
    ColumnFormatShape,
    ValueFreqItem,
)
from opendv.models.sources import (
    ColumnProfile,
    SourceKind,
    SourceProfile,
    TablePreviewPage,
    TableProfile,
)
from opendv.sources.adapters import RawColumnData, RawTableData, extract_structured_source
from opendv.sources.clustering import cluster_tables
from opendv.sources.containment import compute_pk_containment_overlaps
from opendv.sources.pii import (
    MASKED_PII_TOKEN,
    RE_EMAIL,
    detect_pii_in_column_name,
    detect_pii_in_value,
    mask_cell_value,
    redact_pii_value_frequencies,
    tokenize_row_pii,
)

MAX_SAMPLE_VALUES: int = 5
SHAPE_SAMPLE_LIMIT: int = 200
_MAX_PROVING_SAMPLE_ROWS: int = 5000

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_CODE_UPPER_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9][A-Z0-9_/-]{0,31}$")
_FLOAT_SQL_BASES = frozenset({"DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "NUMBER"})
_SEMANTIC_DELIMITERS = ("-", "_", "/", ".")
_NON_BK_SHAPES = frozenset({"ISO_DATE", "ISO_TIMESTAMP", "DECIMAL", "FREE_TEXT"})


def _compute_schema_fingerprint(tables: Sequence[TableProfile]) -> str:
    h = hashlib.sha256()
    for tbl in sorted(tables, key=lambda t: t.table_name):
        h.update(f"T:{tbl.table_name}:{tbl.row_count_est}\n".encode())
        for col in tbl.columns:
            h.update(
                f"C:{col.col_name}:{col.sql_type}:{col.nullable}:{col.is_pk}:"
                f"{col.pk_ord}:{col.fk_ref}:{','.join(sorted(col.pii_tags))}\n".encode()
            )
    return h.hexdigest()


def _classify_format_shape(values: Sequence[str], sql_type: str) -> ColumnFormatShape:
    non_empty = [v.strip() for v in values if v and v.strip()]
    if not non_empty:
        return "MIXED"
    n = len(non_empty)
    min_match = math.ceil(0.8 * n)

    if sum(1 for v in non_empty if _UUID_RE.fullmatch(v)) >= min_match:
        return "UUID"
    if sum(1 for v in non_empty if _ISO_TS_RE.fullmatch(v)) >= min_match:
        return "ISO_TIMESTAMP"
    if sum(1 for v in non_empty if _ISO_DATE_RE.fullmatch(v)) >= min_match:
        return "ISO_DATE"
    if sum(1 for v in non_empty if RE_EMAIL.fullmatch(v)) >= min_match:
        return "EMAIL"
    if sum(1 for v in non_empty if _NUM_RE.fullmatch(v)) >= min_match:
        base_type = sql_type.strip().upper().split("(", 1)[0].strip()
        if any(_DECIMAL_RE.fullmatch(v) for v in non_empty) or base_type in _FLOAT_SQL_BASES:
            return "DECIMAL"
        if sum(1 for v in non_empty if _INT_RE.fullmatch(v)) >= min_match:
            return "INT_SEQ"
    if sum(1 for v in non_empty if _CODE_UPPER_RE.fullmatch(v)) >= min_match:
        return "CODE_UPPER"
    if sum(1 for v in non_empty if any(ch.isspace() for ch in v) or len(v) > 32) >= min_match:
        return "FREE_TEXT"
    return "MIXED"


def _compute_surrogate_score(
    col_name: str,
    format_shape: ColumnFormatShape,
    non_null_vals: Sequence[str],
) -> float:
    score = 0.0
    if SURROGATE_BK_RE.fullmatch(col_name.strip()):
        score += 0.50

    if format_shape == "UUID":
        score += 0.30
    elif format_shape == "INT_SEQ":
        ints = [int(v.strip()) for v in non_null_vals if _INT_RE.fullmatch(v.strip())]
        if len(ints) >= 2 and (
            all(a < b for a, b in itertools.pairwise(ints))
            or all(a > b for a, b in itertools.pairwise(ints))
        ):
            score += 0.30

    if non_null_vals and not any(
        delim in v for v in non_null_vals for delim in _SEMANTIC_DELIMITERS
    ):
        score += 0.20

    return round(min(1.0, score), 4)


def _build_column_profile(
    raw: RawColumnData,
    *,
    observed_values: Sequence[str] | None = None,
    row_count_est: int | None = None,
    include_shape_stats: bool = True,
) -> ColumnProfile:
    non_null_samples = [v for v in raw.raw_samples if v is not None]
    shape_pool = raw.shape_samples if raw.shape_samples else raw.raw_samples
    stat_vals = (
        list(observed_values)
        if observed_values
        else ([v for v in raw.shape_samples if v is not None] or non_null_samples)
    )
    tags: list[str] = list(raw.explicit_pii_tags)
    tags.extend(detect_pii_in_column_name(raw.col_name))
    for val in dict.fromkeys((*non_null_samples, *stat_vals)):
        tags.extend(detect_pii_in_value(val, col_name=raw.col_name))

    dedup_tags = sorted(set(tags))
    capped = raw.raw_samples[:MAX_SAMPLE_VALUES]
    if dedup_tags:
        masked_samples: list[str | None] = [
            None if v is None else MASKED_PII_TOKEN for v in capped
        ]
    else:
        masked_samples = [
            mask_cell_value(v, col_name=raw.col_name, column_pii_tags=dedup_tags)
            for v in capped
        ]

    format_shape = _classify_format_shape(stat_vals, raw.sql_type)
    surrogate_score = _compute_surrogate_score(raw.col_name, format_shape, stat_vals)

    top_freqs: list[ValueFreqItem] = []
    if raw.distinct_count_est <= 20 and stat_vals:
        counts = collections.Counter(stat_vals)
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        raw_items = [ValueFreqItem(value=v, count=c) for v, c in ordered]
        top_freqs = redact_pii_value_frequencies(
            raw_items,
            is_pii_col=bool(dedup_tags),
            col_name=raw.col_name,
        )
    shape_stats = (
        compute_column_shape_stats(
            shape_pool,
            col_name=raw.col_name,
            sql_type=raw.sql_type,
            pii_tags=dedup_tags,
            row_count_est=row_count_est,
            distinct_count_est=raw.distinct_count_est,
            null_ratio=raw.null_ratio,
        )
        if include_shape_stats
        else None
    )

    return ColumnProfile(
        col_name=raw.col_name,
        sql_type=raw.sql_type,
        nullable=raw.nullable,
        is_pk=raw.is_pk,
        pk_ord=raw.pk_ord,
        fk_ref=raw.fk_ref,
        null_ratio=raw.null_ratio,
        distinct_count_est=raw.distinct_count_est,
        pii_tags=dedup_tags,
        sample_values=masked_samples,
        format_shape=format_shape,
        surrogate_score=surrogate_score,
        top_value_frequencies=top_freqs,
        shape_stats=shape_stats,
    )


def _to_table_profile(
    source_id: str,
    raw: RawTableData,
    *,
    row_slice: Sequence[Sequence[str | None]] | None = None,
    include_shape_stats: bool = True,
) -> TableProfile:
    rows_for_stats = row_slice if row_slice is not None else raw.preview_rows
    cols: list[ColumnProfile] = []
    for idx, raw_col in enumerate(raw.columns):
        col_vals = [
            cell
            for r in rows_for_stats
            if idx < len(r) and (cell := r[idx]) is not None
        ]
        cols.append(
            _build_column_profile(
                raw_col,
                observed_values=col_vals,
                row_count_est=raw.row_count_est,
                include_shape_stats=include_shape_stats,
            )
        )

    pk_cols = [c for c in sorted(cols, key=lambda c: c.pk_ord or 999) if c.is_pk]
    pk_ratio: float | None = None
    if pk_cols and raw.row_count_est > 0:
        if len(pk_cols) == 1:
            pk_ratio = round(min(1.0, pk_cols[0].distinct_count_est / raw.row_count_est), 6)
        elif rows_for_stats:
            pk_indices = [idx for idx, c in enumerate(cols) if c.is_pk]
            distinct_tuples = len(
                {
                    tuple(r[i] if i < len(r) else None for i in pk_indices)
                    for r in rows_for_stats
                }
            )
            pk_ratio = round(min(1.0, distinct_tuples / len(rows_for_stats)), 6)
        else:
            pk_ratio = round(
                min(1.0, max(c.distinct_count_est for c in pk_cols) / raw.row_count_est), 6
            )

    is_composite_pk = len(pk_cols) > 1
    best_bk_col: str | None = None
    best_bk_ratio: float | None = None
    if raw.row_count_est > 0:
        candidates: list[tuple[tuple[float, int, int, int, int, float, int], ColumnProfile]] = []
        for idx, c in enumerate(cols):
            if c.distinct_count_est <= 0:
                continue
            is_surrogate = (
                bool(SURROGATE_BK_RE.fullmatch(c.col_name.strip())) or c.surrogate_score >= 0.75
            )
            if is_surrogate and not (is_composite_pk and c.is_pk):
                continue
            u_ratio = min(1.0, c.distinct_count_est / raw.row_count_est)
            rank_key = (
                u_ratio,
                1 if c.null_ratio == 0.0 else 0,
                1 if not c.pii_tags else 0,
                1 if c.format_shape not in _NON_BK_SHAPES else 0,
                1 if c.is_pk else 0,
                -c.surrogate_score,
                -idx,
            )
            candidates.append((rank_key, c))
        if candidates:
            best_key, best_col = max(candidates, key=lambda item: item[0])
            best_bk_col = best_col.col_name
            best_bk_ratio = round(best_key[0], 6)
    table_id = f"{source_id}:{raw.table_name}"
    return TableProfile(
        table_id=table_id,
        table_name=raw.table_name,
        row_count_est=raw.row_count_est,
        columns=cols,
        pk_uniqueness_ratio=pk_ratio,
        best_non_surrogate_bk_candidate_col=best_bk_col,
        best_non_surrogate_bk_uniqueness_ratio=best_bk_ratio,
    )


def _assign_clusters(tables: list[TableProfile]) -> list[TableProfile]:
    if not tables:
        return tables
    clustering = cluster_tables(tables)
    return [
        tbl.model_copy(update={"cluster_id": clustering.table_to_cluster.get(tbl.table_id)})
        for tbl in tables
    ]


def _run_with_timeout(timeout_seconds: int, fn: Any, *args: Any, **kwargs: Any) -> Any:
    if timeout_seconds < 1:
        raise OpenDVError(
            f"profile_timeout_seconds must be >= 1, got {timeout_seconds}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=float(timeout_seconds))
    except concurrent.futures.TimeoutError as err:
        future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        raise OpenDVError(
            f"Source profiling exceeded timeout of {timeout_seconds}s",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from err
    finally:
        pool.shutdown(wait=False)


def profile_source(
    source_id: str,
    kind: SourceKind,
    uri_or_path: str,
    *,
    options: Mapping[str, Any] | None = None,
    secret_refs: Sequence[str] = (),
    selected_tables: Sequence[str] | None = None,
    profile_timeout_seconds: int = 30,
    access_token: str | None = None,
) -> SourceProfile:
    """Profile a structured source in a bounded threadpool (zero LLM calls)."""
    raw_tables: list[RawTableData] = _run_with_timeout(
        profile_timeout_seconds,
        extract_structured_source,
        kind,
        uri_or_path,
        options=options,
        secret_refs=secret_refs,
        selected_tables=selected_tables,
        access_token=access_token,
        sample_limit=MAX_SAMPLE_VALUES,
        preview_offset=0,
        preview_limit=MAX_SAMPLE_VALUES,
        shape_sample_limit=SHAPE_SAMPLE_LIMIT,
    )
    table_profiles = _assign_clusters(
        [_to_table_profile(source_id, rt, include_shape_stats=True) for rt in raw_tables]
    )
    fingerprint = _compute_schema_fingerprint(table_profiles)
    return SourceProfile(
        source_id=source_id,
        schema_fingerprint=fingerprint,
        table_count=len(table_profiles),
        tables=table_profiles,
    )


def profile_source_with_samples(
    source_id: str,
    kind: SourceKind,
    uri_or_path: str,
    *,
    options: Mapping[str, Any] | None = None,
    secret_refs: Sequence[str] = (),
    selected_tables: Sequence[str] | None = None,
    profile_timeout_seconds: int = 30,
    proving_sample_rows: int = 200,
    real_row_proving: bool = True,
    pii_salt: bytes | str | None = None,
    access_token: str | None = None,
) -> tuple[SourceProfile, dict[str, list[dict[str, Any]]], dict[str, bool]]:
    """Profile a structured source and return bounded PII-tokenized proving sample rows."""
    if (
        isinstance(proving_sample_rows, bool)
        or proving_sample_rows < 1
        or proving_sample_rows > _MAX_PROVING_SAMPLE_ROWS
    ):
        raise OpenDVError(
            f"proving_sample_rows must be in [1, {_MAX_PROVING_SAMPLE_ROWS}], "
            f"got {proving_sample_rows}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if not real_row_proving:
        profile = profile_source(
            source_id,
            kind,
            uri_or_path,
            options=options,
            secret_refs=secret_refs,
            selected_tables=selected_tables,
            profile_timeout_seconds=profile_timeout_seconds,
            access_token=access_token,
        )
        return profile, {}, {}

    raw_tables: list[RawTableData] = _run_with_timeout(
        profile_timeout_seconds,
        extract_structured_source,
        kind,
        uri_or_path,
        options=options,
        secret_refs=secret_refs,
        selected_tables=selected_tables,
        access_token=access_token,
        sample_limit=MAX_SAMPLE_VALUES,
        preview_offset=0,
        preview_limit=proving_sample_rows + 1,
        shape_sample_limit=SHAPE_SAMPLE_LIMIT,
    )

    effective_salt: bytes | str = pii_salt if pii_salt is not None else secrets.token_bytes(32)
    table_profiles: list[TableProfile] = []
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    sample_flags: dict[str, bool] = {}

    for rt in raw_tables:
        is_complete = len(rt.preview_rows) <= proving_sample_rows
        sliced_rows = rt.preview_rows[:proving_sample_rows]
        tbl_prof = _to_table_profile(
            source_id, rt, row_slice=sliced_rows, include_shape_stats=True
        )
        table_profiles.append(tbl_prof)

        col_names = [c.col_name for c in tbl_prof.columns]
        pii_cols = {c.col_name for c in tbl_prof.columns if c.pii_tags}
        tokenized_rows = [
            tokenize_row_pii(
                {
                    col_names[idx]: (row[idx] if idx < len(row) else None)
                    for idx in range(len(col_names))
                },
                pii_columns=pii_cols,
                salt=effective_salt,
            )
            for row in sliced_rows
        ]
        rows_by_table[tbl_prof.table_id] = tokenized_rows
        sample_flags[tbl_prof.table_id] = is_complete

    clustered_tables = _assign_clusters(table_profiles)
    fingerprint = _compute_schema_fingerprint(clustered_tables)
    profile = SourceProfile(
        source_id=source_id,
        schema_fingerprint=fingerprint,
        table_count=len(clustered_tables),
        tables=clustered_tables,
    )
    if len(clustered_tables) >= 2:
        overlaps = compute_pk_containment_overlaps(
            [profile], rows_by_table, table_sample_flags=sample_flags
        )
        if overlaps:
            profile = profile.model_copy(update={"pk_containment_overlaps": overlaps})

    return profile, rows_by_table, sample_flags


def preview_table(
    source_id: str,
    kind: SourceKind,
    uri_or_path: str,
    table_name: str,
    *,
    options: Mapping[str, Any] | None = None,
    secret_refs: Sequence[str] = (),
    offset: int = 0,
    limit: int = 50,
    profile_timeout_seconds: int = 30,
    access_token: str | None = None,
) -> TablePreviewPage:
    """Fetch a paged, PII-masked row preview for a single profiled table."""
    if offset < 0 or limit < 1:
        raise OpenDVError(
            f"Invalid pagination parameters offset={offset}, limit={limit}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    raw_tables: list[RawTableData] = _run_with_timeout(
        profile_timeout_seconds,
        extract_structured_source,
        kind,
        uri_or_path,
        options=options,
        secret_refs=secret_refs,
        selected_tables=[table_name],
        access_token=access_token,
        sample_limit=MAX_SAMPLE_VALUES,
        preview_offset=offset,
        preview_limit=limit,
        shape_sample_limit=0,
    )
    if not raw_tables:
        raise OpenDVError(
            f"Table '{table_name}' not found in source '{source_id}'",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    raw = raw_tables[0]
    table_profile = _to_table_profile(source_id, raw, include_shape_stats=False)
    col_names = [c.col_name for c in table_profile.columns]
    col_tags = [c.pii_tags for c in table_profile.columns]

    masked_rows: list[list[str | None]] = []
    for row in raw.preview_rows:
        masked_row = [
            mask_cell_value(cell, col_name=col_names[idx], column_pii_tags=col_tags[idx])
            for idx, cell in enumerate(row[: len(col_names)])
        ]
        masked_rows.append(masked_row)

    return TablePreviewPage(
        table_id=table_profile.table_id,
        source_id=source_id,
        columns=col_names,
        rows=masked_rows,
        total_rows=raw.row_count_est,
    )


__all__ = [
    "MAX_SAMPLE_VALUES",
    "SHAPE_SAMPLE_LIMIT",
    "preview_table",
    "profile_source",
    "profile_source_with_samples",
]
