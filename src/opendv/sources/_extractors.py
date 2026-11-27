"""Structured extraction dispatcher & Databricks UC extractor for opendv.sources."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import SourceKind
from opendv.sources._extractors_files import (
    RawColumnData,
    RawTableData,
    extract_duckdb_or_tabular_file,
    extract_sqlite,
    extract_xlsx,
    filter_selected,
)

_SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema", "sys", "system"})
_UC_AGG_CHUNK_SIZE: int = 100
_UC_COMPLEX_TYPES: frozenset[str] = frozenset({"MAP", "ARRAY", "STRUCT"})


def _is_complex_uc_type(sql_type: str) -> bool:
    base = sql_type.strip().upper().split("<", 1)[0].split("(", 1)[0].strip()
    return base in _UC_COMPLEX_TYPES


def _execute_uc_query(
    cur: Any,
    sql: str,
    *,
    deadline: float | None,
    fetch_mode: str,
) -> Any:
    def _run() -> Any:
        cur.execute(sql)
        if fetch_mode == "one":
            return cur.fetchone()
        if fetch_mode == "all":
            return cur.fetchall()
        return None

    if deadline is None:
        return _run()

    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise OpenDVError(
            "Databricks UC profiling exceeded query timeout",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        )
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_run)
    try:
        return future.result(timeout=remaining)
    except concurrent.futures.TimeoutError as err:
        future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        raise OpenDVError(
            "Databricks UC profiling exceeded query timeout",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from err
    finally:
        pool.shutdown(wait=False)


class DatabricksConnFactory(Protocol):
    """Callable contract for Databricks SQL connection factories."""

    def __call__(
        self,
        *,
        host: str,
        http_path: str,
        access_token: str | None = None,
        secret_refs: Sequence[str] = (),
        options: Mapping[str, Any] | None = None,
    ) -> Any: ...


def extract_databricks_uc(
    *,
    uri_or_path: str = "",
    options: Mapping[str, Any],
    access_token: str | None = None,
    secret_refs: Sequence[str],
    selected_tables: Sequence[str] | None,
    sample_limit: int,
    preview_offset: int,
    preview_limit: int,
    validate_ident_fn: Callable[[str], str],
    open_conn_fn: DatabricksConnFactory,
    timeout_seconds: float | None = 30.0,
    shape_sample_limit: int = 200,
) -> list[RawTableData]:
    injected_conn = options.get("_connection")
    catalog = validate_ident_fn(str(options.get("catalog", "")))
    schema = validate_ident_fn(str(options.get("schema") or "default"))
    if schema.lower() in _SYSTEM_SCHEMAS:
        raise OpenDVError(
            f"System schema '{schema}' cannot be profiled",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    raw_timeout = options.get(
        "query_timeout_seconds",
        options.get("profile_timeout_seconds", timeout_seconds),
    )
    deadline: float | None = None
    if raw_timeout is not None:
        deadline = time.monotonic() + max(0.001, float(raw_timeout))

    raw_opt = options.get("host")
    opt_host = raw_opt.strip() if isinstance(raw_opt, str) else ""
    host = opt_host or uri_or_path.strip()
    raw_http_path = options.get("http_path")
    http_path = raw_http_path if isinstance(raw_http_path, str) else str(raw_http_path or "")
    conn = (
        injected_conn
        if injected_conn is not None
        else open_conn_fn(
            host=host,
            http_path=http_path,
            access_token=access_token,
            secret_refs=secret_refs,
            options={k: v for k, v in options.items() if k != "_connection"},
        )
    )
    cur = conn.cursor()
    tbl_rows = _execute_uc_query(
        cur,
        f"SELECT table_name FROM `{catalog}`.information_schema.tables "
        f"WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' ORDER BY table_name",
        deadline=deadline,
        fetch_mode="all",
    )
    tbl_names = filter_selected(
        [str(r[0]) for r in tbl_rows if not str(r[0]).startswith("_")],
        selected_tables,
    )

    tables: list[RawTableData] = []
    for tbl in tbl_names:
        safe_tbl = validate_ident_fn(tbl)
        col_defs = _execute_uc_query(
            cur,
            f"SELECT column_name, data_type, is_nullable "
            f"FROM `{catalog}`.information_schema.columns "
            f"WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' "
            f"AND table_name = '{safe_tbl}' ORDER BY ordinal_position",
            deadline=deadline,
            fetch_mode="all",
        )

        pii_tagged_cols: set[str] = set()
        try:
            tag_rows = _execute_uc_query(
                cur,
                f"SELECT column_name, tag_name, tag_value "
                f"FROM `{catalog}`.information_schema.column_tags "
                f"WHERE catalog_name = '{catalog}' AND schema_name = '{schema}' "
                f"AND table_name = '{safe_tbl}'",
                deadline=deadline,
                fetch_mode="all",
            )
            for c_n, t_n, t_v in tag_rows:
                if str(t_n).lower() == "pii" and str(t_v).lower() == "true":
                    pii_tagged_cols.add(str(c_n))
        except OpenDVError:
            raise
        except Exception as exc:  # noqa: BLE001
            _ = exc

        cnt_row = _execute_uc_query(
            cur,
            f"SELECT count(*) FROM `{catalog}`.`{schema}`.`{safe_tbl}`",
            deadline=deadline,
            fetch_mode="one",
        )
        total_rows = int((cnt_row or (0,))[0] or 0)

        fetch_limit = max(
            max(0, int(preview_offset)) + max(0, int(preview_limit)),
            max(0, int(shape_sample_limit)),
        )
        all_preview = _execute_uc_query(
            cur,
            f"SELECT * FROM `{catalog}`.`{schema}`.`{safe_tbl}` LIMIT {fetch_limit}",
            deadline=deadline,
            fetch_mode="all",
        )
        sliced = all_preview[preview_offset : preview_offset + preview_limit]
        preview_rows = [[None if v is None else str(v) for v in row] for row in sliced]

        parsed_cols = [
            (
                validate_ident_fn(str(c_def[0])),
                str(c_def[1] or "STRING").upper(),
                str(c_def[2]).upper() in ("YES", "TRUE", "1"),
            )
            for c_def in col_defs
        ]

        agg_stats: list[tuple[int, int]] = []
        for start in range(0, len(parsed_cols), _UC_AGG_CHUNK_SIZE):
            chunk = parsed_cols[start : start + _UC_AGG_CHUNK_SIZE]
            select_exprs: list[str] = []
            for col_name, sql_type, _ in chunk:
                select_exprs.append(f"count(*) - count(`{col_name}`)")
                if _is_complex_uc_type(sql_type):
                    select_exprs.append("0")
                else:
                    select_exprs.append(f"APPROX_COUNT_DISTINCT(`{col_name}`)")
            agg_sql = f"SELECT {', '.join(select_exprs)} FROM `{catalog}`.`{schema}`.`{safe_tbl}`"
            agg_row = (
                _execute_uc_query(cur, agg_sql, deadline=deadline, fetch_mode="one") or ()
            )
            for c_idx in range(len(chunk)):
                pos = c_idx * 2
                null_cnt = int(agg_row[pos] or 0) if pos < len(agg_row) else 0
                dist_cnt = int(agg_row[pos + 1] or 0) if (pos + 1) < len(agg_row) else 0
                agg_stats.append((null_cnt, dist_cnt))

        cols: list[RawColumnData] = []
        for idx, (col_name, sql_type, nullable) in enumerate(parsed_cols):
            null_cnt, dist_cnt = agg_stats[idx]
            null_ratio = float(null_cnt) / float(total_rows) if total_rows > 0 else 0.0
            col_non_null: list[str | None] = [
                str(row[idx])
                for row in all_preview
                if idx < len(row) and row[idx] is not None
            ]
            samples = col_non_null[:sample_limit]
            shape_samples = col_non_null[:shape_sample_limit] if shape_sample_limit > 0 else []
            explicit_tags = ["UC_TAG:PII"] if col_name in pii_tagged_cols else []
            cols.append(
                RawColumnData(
                    col_name=col_name,
                    sql_type=sql_type,
                    nullable=nullable,
                    is_pk=False,
                    pk_ord=None,
                    fk_ref=None,
                    null_ratio=min(1.0, max(0.0, null_ratio)),
                    distinct_count_est=dist_cnt,
                    raw_samples=samples,
                    explicit_pii_tags=explicit_tags,
                    shape_samples=shape_samples,
                )
            )
        tables.append(
            RawTableData(
                table_name=safe_tbl,
                row_count_est=total_rows,
                columns=cols,
                preview_rows=preview_rows,
            )
        )
    return tables


def dispatch_structured_extract(
    kind: SourceKind,
    uri_or_path: str,
    *,
    options: Mapping[str, Any] | None = None,
    access_token: str | None = None,
    secret_refs: Sequence[str] = (),
    selected_tables: Sequence[str] | None = None,
    sample_limit: int = 5,
    preview_offset: int = 0,
    preview_limit: int = 50,
    validate_ident_fn: Callable[[str], str],
    open_conn_fn: DatabricksConnFactory,
    shape_sample_limit: int = 200,
) -> list[RawTableData]:
    if kind == "sqlite":
        return extract_sqlite(
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
            shape_sample_limit=shape_sample_limit,
        )
    if kind in ("duckdb", "parquet", "csv"):
        return extract_duckdb_or_tabular_file(
            kind,
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
            shape_sample_limit=shape_sample_limit,
        )
    if kind == "xlsx":
        return extract_xlsx(
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
            shape_sample_limit=shape_sample_limit,
        )
    if kind == "databricks_uc":
        return extract_databricks_uc(
            uri_or_path=uri_or_path,
            options=options or {},
            access_token=access_token,
            secret_refs=secret_refs,
            selected_tables=selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
            validate_ident_fn=validate_ident_fn,
            open_conn_fn=open_conn_fn,
            shape_sample_limit=shape_sample_limit,
        )
    raise OpenDVError(
        f"Source kind '{kind}' is not a structured tabular adapter",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


__all__ = [
    "DatabricksConnFactory",
    "RawColumnData",
    "RawTableData",
    "dispatch_structured_extract",
    "extract_databricks_uc",
]
