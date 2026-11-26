"""Structured extraction dispatcher & Databricks UC extractor for opendv.sources."""

from __future__ import annotations

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
) -> list[RawTableData]:
    injected_conn = options.get("_connection")
    catalog = validate_ident_fn(str(options.get("catalog", "")))
    schema = validate_ident_fn(str(options.get("schema") or "default"))
    if schema.lower() in _SYSTEM_SCHEMAS:
        raise OpenDVError(
            f"System schema '{schema}' cannot be profiled",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

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
    cur.execute(
        f"SELECT table_name FROM `{catalog}`.information_schema.tables "
        f"WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' ORDER BY table_name"
    )
    tbl_names = filter_selected(
        [str(r[0]) for r in cur.fetchall() if not str(r[0]).startswith("_")],
        selected_tables,
    )

    tables: list[RawTableData] = []
    for tbl in tbl_names:
        safe_tbl = validate_ident_fn(tbl)
        cur.execute(
            f"SELECT column_name, data_type, is_nullable "
            f"FROM `{catalog}`.information_schema.columns "
            f"WHERE table_catalog = '{catalog}' AND table_schema = '{schema}' "
            f"AND table_name = '{safe_tbl}' ORDER BY ordinal_position"
        )
        col_defs = cur.fetchall()

        pii_tagged_cols: set[str] = set()
        try:
            cur.execute(
                f"SELECT column_name, tag_name, tag_value "
                f"FROM `{catalog}`.information_schema.column_tags "
                f"WHERE catalog_name = '{catalog}' AND schema_name = '{schema}' "
                f"AND table_name = '{safe_tbl}'"
            )
            for c_n, t_n, t_v in cur.fetchall():
                if str(t_n).lower() == "pii" and str(t_v).lower() == "true":
                    pii_tagged_cols.add(str(c_n))
        except Exception as exc:  # noqa: BLE001
            _ = exc

        cur.execute(f"SELECT count(*) FROM `{catalog}`.`{schema}`.`{safe_tbl}`")
        total_rows = int((cur.fetchone() or (0,))[0] or 0)

        fetch_limit = max(0, int(preview_offset)) + max(0, int(preview_limit))
        cur.execute(
            f"SELECT * FROM `{catalog}`.`{schema}`.`{safe_tbl}` LIMIT {fetch_limit}"
        )
        all_preview = cur.fetchall()
        sliced = all_preview[preview_offset : preview_offset + preview_limit]
        preview_rows = [[None if v is None else str(v) for v in row] for row in sliced]

        cols: list[RawColumnData] = []
        for idx, c_def in enumerate(col_defs):
            col_name = validate_ident_fn(str(c_def[0]))
            sql_type = str(c_def[1] or "STRING").upper()
            nullable = str(c_def[2]).upper() in ("YES", "TRUE", "1")
            cur.execute(
                f"SELECT count(*) - count(`{col_name}`), "
                f"APPROX_COUNT_DISTINCT(`{col_name}`) "
                f"FROM `{catalog}`.`{schema}`.`{safe_tbl}`"
            )
            null_cnt, dist_cnt = cur.fetchone() or (0, 0)
            null_ratio = float(null_cnt or 0) / float(total_rows) if total_rows > 0 else 0.0
            raw_vals = [
                str(row[idx])
                for row in all_preview
                if idx < len(row) and row[idx] is not None
            ][:sample_limit]
            samples: list[str | None] = list(raw_vals)
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
                    distinct_count_est=int(dist_cnt or 0),
                    raw_samples=samples,
                    explicit_pii_tags=explicit_tags,
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
) -> list[RawTableData]:
    if kind == "sqlite":
        return extract_sqlite(
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
        )
    if kind in ("duckdb", "parquet", "csv"):
        return extract_duckdb_or_tabular_file(
            kind,
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
        )
    if kind == "xlsx":
        return extract_xlsx(
            uri_or_path,
            selected_tables,
            sample_limit=sample_limit,
            preview_offset=preview_offset,
            preview_limit=preview_limit,
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
