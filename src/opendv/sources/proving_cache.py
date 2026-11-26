"""JSON proving-sample cache builders, JCS fingerprinting, and safe source_id validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.modeling_preferences import CachedTableSample, ProvingSampleCacheFile
from opendv.models.sources import TableProfile

SAFE_SOURCE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_source_id_slug(source_id: str) -> str:
    """Validate that source_id is a safe filename slug (no path traversal)."""
    if not isinstance(source_id, str) or not SAFE_SOURCE_ID_RE.fullmatch(source_id):
        raise OpenDVError(
            f"Invalid source_id slug '{source_id}': must match {SAFE_SOURCE_ID_RE.pattern}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return source_id


def compute_proving_table_fingerprint(table: TableProfile) -> str:
    """Compute canonical RFC 8785 JCS SHA-256 fingerprint for a profiled table schema."""
    col_tuples = sorted(
        (c.col_name, c.sql_type, c.nullable, bool(c.pii_tags), c.is_pk)
        for c in table.columns
    )
    return sha256_jcs((table.table_name, col_tuples))


def build_proving_cache_file(
    source_id: str,
    project_id: str,
    tables: Sequence[TableProfile],
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    sample_flags: Mapping[str, bool],
    *,
    salt_key_id: str,
    salt_digest: str,
    cached_sample_rows_limit: int,
) -> ProvingSampleCacheFile:
    """Build a ProvingSampleCacheFile DTO from profiled tables and PII-tokenized rows."""
    safe_source_id = validate_source_id_slug(source_id)
    if (
        isinstance(cached_sample_rows_limit, bool)
        or cached_sample_rows_limit < 1
        or cached_sample_rows_limit > 5000
    ):
        raise OpenDVError(
            f"cached_sample_rows_limit must be in [1, 5000], got {cached_sample_rows_limit}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    cached_tables: dict[str, CachedTableSample] = {}
    for tbl in tables:
        raw_rows = rows_by_table.get(tbl.table_id)
        if raw_rows is None:
            raw_rows = rows_by_table.get(tbl.table_name, ())
        bounded_rows = [dict(row) for row in raw_rows[:cached_sample_rows_limit]]
        is_complete = bool(
            sample_flags.get(
                tbl.table_id,
                tbl.row_count_est <= cached_sample_rows_limit
                and len(bounded_rows) == tbl.row_count_est,
            )
        )
        cached_tables[tbl.table_id] = CachedTableSample(
            table_id=tbl.table_id,
            schema_fingerprint=compute_proving_table_fingerprint(tbl),
            salt_digest=salt_digest,
            rows=bounded_rows,
            row_count_est=tbl.row_count_est,
            is_complete_table=is_complete,
            cached_sample_rows_limit=cached_sample_rows_limit,
        )

    return ProvingSampleCacheFile(
        source_id=safe_source_id,
        project_id=project_id,
        created_at_utc=datetime.now(UTC).isoformat(),
        salt_key_id=salt_key_id,
        salt_digest=salt_digest,
        cached_sample_rows_limit=cached_sample_rows_limit,
        tables=cached_tables,
    )


def is_cached_table_valid(
    cached: CachedTableSample,
    table: TableProfile,
    *,
    expected_salt_digest: str,
    expected_limit: int,
) -> bool:
    """Validate cached sample against schema fingerprint, salt digest, limit, and completeness."""
    if cached.table_id != table.table_id:
        return False
    if cached.salt_digest != expected_salt_digest:
        return False
    if cached.cached_sample_rows_limit != expected_limit:
        return False
    if cached.row_count_est != table.row_count_est:
        return False
    expected_complete = table.row_count_est <= expected_limit
    if cached.is_complete_table != expected_complete:
        return False
    return cached.schema_fingerprint == compute_proving_table_fingerprint(table)


__all__ = [
    "SAFE_SOURCE_ID_RE",
    "build_proving_cache_file",
    "compute_proving_table_fingerprint",
    "is_cached_table_valid",
    "validate_source_id_slug",
]
