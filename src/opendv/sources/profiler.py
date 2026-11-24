"""Bounded-timeout threadpool profiler with deterministic schema fingerprints & PII masking."""

from __future__ import annotations

import concurrent.futures
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import (
    ColumnProfile,
    SourceKind,
    SourceProfile,
    TablePreviewPage,
    TableProfile,
)
from opendv.sources.adapters import RawColumnData, RawTableData, extract_structured_source
from opendv.sources.pii import (
    MASKED_PII_TOKEN,
    detect_pii_in_column_name,
    detect_pii_in_value,
    mask_cell_value,
)

MAX_SAMPLE_VALUES: int = 5


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


def _build_column_profile(raw: RawColumnData) -> ColumnProfile:
    tags: list[str] = list(raw.explicit_pii_tags)
    tags.extend(detect_pii_in_column_name(raw.col_name))
    for val in raw.raw_samples:
        if val is not None:
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
    )


def _to_table_profile(source_id: str, raw: RawTableData) -> TableProfile:
    cols = [_build_column_profile(c) for c in raw.columns]
    table_id = f"{source_id}:{raw.table_name}"
    return TableProfile(
        table_id=table_id,
        table_name=raw.table_name,
        row_count_est=raw.row_count_est,
        columns=cols,
    )


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
        sample_limit=MAX_SAMPLE_VALUES,
        preview_offset=0,
        preview_limit=MAX_SAMPLE_VALUES,
    )
    table_profiles = [_to_table_profile(source_id, rt) for rt in raw_tables]
    fingerprint = _compute_schema_fingerprint(table_profiles)
    return SourceProfile(
        source_id=source_id,
        schema_fingerprint=fingerprint,
        table_count=len(table_profiles),
        tables=table_profiles,
    )


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
        sample_limit=MAX_SAMPLE_VALUES,
        preview_offset=offset,
        preview_limit=limit,
    )
    if not raw_tables:
        raise OpenDVError(
            f"Table '{table_name}' not found in source '{source_id}'",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    raw = raw_tables[0]
    table_profile = _to_table_profile(source_id, raw)
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
    "preview_table",
    "profile_source",
]
