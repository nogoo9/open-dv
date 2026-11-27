"""File-based structured extractors (SQLite, DuckDB/Parquet/CSV, XLSX) for opendv.sources."""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from zipfile import BadZipFile

import duckdb

from opendv.models.errors import ErrorCode, OpenDVError


@dataclass(frozen=True)
class RawColumnData:
    """Extracted column metadata and raw cell sample pool before PII masking."""

    col_name: str
    sql_type: str
    nullable: bool
    is_pk: bool
    pk_ord: int | None
    fk_ref: str | None
    null_ratio: float
    distinct_count_est: int
    raw_samples: list[str | None]
    explicit_pii_tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RawTableData:
    """Extracted table metadata and raw rows before PII masking."""

    table_name: str
    row_count_est: int
    columns: list[RawColumnData]
    preview_rows: list[list[str | None]]


def _ensure_existing_file(path_str: str) -> Path:
    if not path_str or not path_str.strip():
        raise OpenDVError(
            "Source path cannot be empty",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    path = Path(path_str.strip()).resolve()
    if not path.is_file():
        raise OpenDVError(
            f"Source binary file not found: {path}",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )
    return path


def filter_selected(names: Sequence[str], selected_tables: Sequence[str] | None) -> list[str]:
    if selected_tables is None:
        return list(names)
    allowed = set(selected_tables)
    return [n for n in names if n in allowed]


_WITHOUT_ROWID_RE = re.compile(r"\bWITHOUT\s+ROWID\b", re.IGNORECASE)


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _sqlite_execute_bounded(
    cur: sqlite3.Cursor,
    select_prefix: str,
    limit_clause: str,
    params: tuple[int, ...],
    *,
    order_by_rowid: bool,
) -> list[tuple[Any, ...]]:
    if order_by_rowid:
        try:
            cur.execute(f"{select_prefix} ORDER BY rowid {limit_clause}", params)
            return cur.fetchall()
        except sqlite3.OperationalError:
            pass
    cur.execute(f"{select_prefix} {limit_clause}", params)
    return cur.fetchall()


def _duckdb_execute_bounded(
    conn: duckdb.DuckDBPyConnection,
    select_prefix: str,
    limit_clause: str,
    params: list[int],
    *,
    order_by_rowid: bool,
) -> list[tuple[Any, ...]]:
    if order_by_rowid:
        try:
            return conn.execute(
                f"{select_prefix} ORDER BY rowid {limit_clause}", params
            ).fetchall()
        except duckdb.Error:
            pass
    return conn.execute(f"{select_prefix} {limit_clause}", params).fetchall()


def extract_sqlite(
    uri_or_path: str,
    selected_tables: Sequence[str] | None,
    *,
    sample_limit: int,
    preview_offset: int,
    preview_limit: int,
) -> list[RawTableData]:
    path = _ensure_existing_file(uri_or_path)
    posix_path = path.as_posix()
    db_uri = (
        f"file:{posix_path}?mode=ro"
        if posix_path.startswith("/")
        else f"file:///{posix_path}?mode=ro"
    )
    try:
        conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    except sqlite3.Error as err:
        raise OpenDVError(
            f"Failed to open SQLite file '{path.name}': {err}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from err

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, type, sql FROM sqlite_master "
            "WHERE type IN ('table', 'view') ORDER BY name"
        )
        meta_by_name: dict[str, tuple[str, str]] = {}
        all_names: list[str] = []
        for row in cur.fetchall():
            name = str(row[0])
            if name.lower().startswith("sqlite_"):
                continue
            all_names.append(name)
            meta_by_name[name] = (str(row[1] or "").lower(), str(row[2] or ""))

        tables: list[RawTableData] = []
        for tbl in filter_selected(all_names, selected_tables):
            q_tbl = _quote_ident(tbl)
            tbl_type, tbl_sql = meta_by_name[tbl]
            use_rowid = tbl_type == "table" and not bool(_WITHOUT_ROWID_RE.search(tbl_sql))
            cur.execute(f"SELECT count(*) FROM {q_tbl}")
            total_rows = int((cur.fetchone() or (0,))[0] or 0)

            cur.execute(f"PRAGMA foreign_key_list({q_tbl})")
            fk_by_col: dict[str, str] = {}
            for fk_row in cur.fetchall():
                from_col = str(fk_row[3])
                to_tbl = str(fk_row[2])
                to_col = str(fk_row[4]) if fk_row[4] else from_col
                fk_by_col[from_col] = f"{to_tbl}.{to_col}"

            cur.execute(f"PRAGMA table_info({q_tbl})")
            col_rows = cur.fetchall()

            fetched_preview = _sqlite_execute_bounded(
                cur,
                f"SELECT * FROM {q_tbl}",
                "LIMIT ? OFFSET ?",
                (preview_limit, preview_offset),
                order_by_rowid=use_rowid,
            )
            preview_rows = [[None if v is None else str(v) for v in row] for row in fetched_preview]

            cols: list[RawColumnData] = []
            for c_row in col_rows:
                col_name = str(c_row[1])
                q_col = _quote_ident(col_name)
                sql_type = str(c_row[2] or "TEXT").upper()
                not_null = bool(c_row[3])
                pk_val = int(c_row[5] or 0)
                is_pk = pk_val > 0
                pk_ord = pk_val if is_pk else None
                nullable = (not not_null) and (not is_pk)

                cur.execute(
                    f"SELECT count(*) - count({q_col}), count(DISTINCT {q_col}) FROM {q_tbl}"
                )
                null_cnt, dist_cnt = cur.fetchone() or (0, 0)
                null_ratio = float(null_cnt or 0) / float(total_rows) if total_rows > 0 else 0.0

                sample_rows = _sqlite_execute_bounded(
                    cur,
                    f"SELECT {q_col} FROM {q_tbl} WHERE {q_col} IS NOT NULL",
                    "LIMIT ?",
                    (sample_limit,),
                    order_by_rowid=use_rowid,
                )
                samples: list[str | None] = [str(r[0]) for r in sample_rows]
                if not samples and total_rows > 0:
                    fallback_rows = _sqlite_execute_bounded(
                        cur,
                        f"SELECT {q_col} FROM {q_tbl}",
                        "LIMIT ?",
                        (sample_limit,),
                        order_by_rowid=use_rowid,
                    )
                    samples = [None if r[0] is None else str(r[0]) for r in fallback_rows]

                cols.append(
                    RawColumnData(
                        col_name=col_name,
                        sql_type=sql_type,
                        nullable=nullable,
                        is_pk=is_pk,
                        pk_ord=pk_ord,
                        fk_ref=fk_by_col.get(col_name),
                        null_ratio=min(1.0, max(0.0, null_ratio)),
                        distinct_count_est=int(dist_cnt or 0),
                        raw_samples=samples,
                    )
                )
            tables.append(
                RawTableData(
                    table_name=tbl,
                    row_count_est=total_rows,
                    columns=cols,
                    preview_rows=preview_rows,
                )
            )
        return tables
    except sqlite3.Error as err:
        raise OpenDVError(
            f"Failed to read SQLite file '{path.name}': {err}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from err
    finally:
        conn.close()


def extract_duckdb_or_tabular_file(
    kind: Literal["duckdb", "parquet", "csv"],
    uri_or_path: str,
    selected_tables: Sequence[str] | None,
    *,
    sample_limit: int,
    preview_offset: int,
    preview_limit: int,
) -> list[RawTableData]:
    path = _ensure_existing_file(uri_or_path)
    if kind == "duckdb":
        try:
            conn = duckdb.connect(
                str(path),
                read_only=True,
                config={"enable_external_access": False},
            )
        except duckdb.Error as err:
            raise OpenDVError(
                f"Failed to open DuckDB file '{path.name}': {err}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from err
    else:
        conn = duckdb.connect(":memory:")

    try:
        table_types: dict[str, str] = {}
        if kind == "duckdb":
            rows = conn.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type IN ('BASE TABLE', 'VIEW') "
                "ORDER BY table_name"
            ).fetchall()
            table_types = {str(r[0]): str(r[1]).upper() for r in rows}
            table_names = filter_selected([str(r[0]) for r in rows], selected_tables)
        else:
            stem = re.sub(r"[^A-Za-z0-9_]", "_", path.stem) or "table"
            if stem[0].isdigit():
                stem = f"t_{stem}"
            if kind == "parquet":
                conn.read_parquet(str(path)).create_view(stem)
            else:
                conn.read_csv(str(path), sample_size=-1).create_view(stem)
            table_types = {stem: "VIEW"}
            table_names = filter_selected([stem], selected_tables)

        tables: list[RawTableData] = []
        for tbl in table_names:
            q_tbl = _quote_ident(tbl)
            use_rowid = kind == "duckdb" and table_types.get(tbl) == "BASE TABLE"
            total_rows = int((conn.execute(f"SELECT count(*) FROM {q_tbl}").fetchone() or (0,))[0])
            desc_rows = conn.execute(f"DESCRIBE {q_tbl}").fetchall()

            pk_ords: dict[str, int] = {}
            if kind == "duckdb":
                c_rows = conn.execute(
                    "SELECT constraint_type, constraint_column_names "
                    "FROM duckdb_constraints() WHERE schema_name = 'main' AND table_name = ?",
                    [tbl],
                ).fetchall()
                for c_type, c_cols in c_rows:
                    if str(c_type) == "PRIMARY KEY" and isinstance(c_cols, list):
                        for idx, col_n in enumerate(c_cols, start=1):
                            pk_ords[str(col_n)] = idx

            cast_select = ", ".join(
                f"CAST({_quote_ident(str(d[0]))} AS VARCHAR)" for d in desc_rows
            )
            preview_rows = (
                [
                    [None if v is None else str(v) for v in row]
                    for row in _duckdb_execute_bounded(
                        conn,
                        f"SELECT {cast_select} FROM {q_tbl}",
                        "LIMIT ? OFFSET ?",
                        [preview_limit, preview_offset],
                        order_by_rowid=use_rowid,
                    )
                ]
                if cast_select
                else []
            )

            cols: list[RawColumnData] = []
            for d_row in desc_rows:
                col_name = str(d_row[0])
                q_col = _quote_ident(col_name)
                sql_type = str(d_row[1] or "VARCHAR").upper()
                nullable = str(d_row[2]).upper() != "NO"
                is_pk = col_name in pk_ords or str(d_row[3] or "").upper() == "PRI"
                pk_ord = pk_ords.get(col_name) or (1 if is_pk else None)

                dist_expr = (
                    f"count(DISTINCT {q_col})"
                    if total_rows <= 100_000
                    else f"approx_count_distinct({q_col})"
                )
                null_cnt, dist_cnt = conn.execute(
                    f"SELECT count(*) - count({q_col}), {dist_expr} FROM {q_tbl}"
                ).fetchone() or (0, 0)
                null_ratio = float(null_cnt or 0) / float(total_rows) if total_rows > 0 else 0.0

                samples: list[str | None] = [
                    str(r[0])
                    for r in _duckdb_execute_bounded(
                        conn,
                        f"SELECT CAST({q_col} AS VARCHAR) FROM {q_tbl} "
                        f"WHERE {q_col} IS NOT NULL",
                        "LIMIT ?",
                        [sample_limit],
                        order_by_rowid=use_rowid,
                    )
                ]
                cols.append(
                    RawColumnData(
                        col_name=col_name,
                        sql_type=sql_type,
                        nullable=nullable and (not is_pk),
                        is_pk=is_pk,
                        pk_ord=pk_ord,
                        fk_ref=None,
                        null_ratio=min(1.0, max(0.0, null_ratio)),
                        distinct_count_est=int(dist_cnt or 0),
                        raw_samples=samples,
                    )
                )
            tables.append(
                RawTableData(
                    table_name=tbl,
                    row_count_est=total_rows,
                    columns=cols,
                    preview_rows=preview_rows,
                )
            )
        return tables
    except duckdb.Error as err:
        raise OpenDVError(
            f"Failed to read {kind} file '{path.name}': {err}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from err
    finally:
        conn.close()


def _infer_sql_type_from_values(values: Sequence[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "DOUBLE"
    return "VARCHAR"


def extract_xlsx(
    uri_or_path: str,
    selected_tables: Sequence[str] | None,
    *,
    sample_limit: int,
    preview_offset: int,
    preview_limit: int,
) -> list[RawTableData]:
    from importlib import import_module

    from openpyxl import load_workbook  # type: ignore[import-untyped]
    from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]

    xlsx_errors: list[type[BaseException]] = [
        InvalidFileException,
        BadZipFile,
        zlib.error,
        RuntimeError,
        OSError,
        EOFError,
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        ET.ParseError,
    ]
    try:
        lxml_etree = import_module("lxml.etree")
    except ImportError:
        lxml_etree = None
    if lxml_etree is not None:
        for attr in ("XMLSyntaxError", "LxmlError"):
            xml_err = getattr(lxml_etree, attr, None)
            if isinstance(xml_err, type) and issubclass(xml_err, BaseException):
                xlsx_errors.append(xml_err)

    path = _ensure_existing_file(uri_or_path)
    wb: Any = None
    try:
        with path.open("rb") as fh:
            wb = load_workbook(filename=fh, read_only=True, data_only=True)
            tables: list[RawTableData] = []
            for sheet_name in filter_selected(list(wb.sheetnames), selected_tables):
                ws = wb[sheet_name]
                row_iter = ws.iter_rows(values_only=True)
                header_raw = next(row_iter, None)
                if not header_raw:
                    continue
                headers = [
                    str(cell).strip() if cell is not None else f"col_{idx + 1}"
                    for idx, cell in enumerate(header_raw)
                ]
                while (
                    headers
                    and headers[-1].startswith("col_")
                    and header_raw[len(headers) - 1] is None
                ):
                    headers.pop()
                if not headers:
                    continue

                width = len(headers)
                data_rows = [
                    tuple(row[:width]) + (None,) * max(0, width - len(row))
                    for row in row_iter
                    if any(v is not None for v in row[:width])
                ]
                total_rows = len(data_rows)
                preview_slice = data_rows[preview_offset : preview_offset + preview_limit]
                preview_rows = [[None if v is None else str(v) for v in r] for r in preview_slice]

                cols: list[RawColumnData] = []
                for idx, col_name in enumerate(headers):
                    col_vals = [r[idx] for r in data_rows]
                    non_null = [v for v in col_vals if v is not None]
                    null_cnt = total_rows - len(non_null)
                    null_ratio = float(null_cnt) / float(total_rows) if total_rows > 0 else 0.0
                    samples: list[str | None] = [str(v) for v in non_null[:sample_limit]]
                    cols.append(
                        RawColumnData(
                            col_name=col_name,
                            sql_type=_infer_sql_type_from_values(col_vals),
                            nullable=null_cnt > 0 or total_rows == 0,
                            is_pk=False,
                            pk_ord=None,
                            fk_ref=None,
                            null_ratio=null_ratio,
                            distinct_count_est=len({str(v) for v in non_null}),
                            raw_samples=samples,
                        )
                    )
                tables.append(
                    RawTableData(
                        table_name=sheet_name,
                        row_count_est=total_rows,
                        columns=cols,
                        preview_rows=preview_rows,
                    )
                )
            return tables
    except tuple(xlsx_errors) as err:
        raise OpenDVError(
            f"Failed to read Excel file '{path.name}': {err}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from err
    finally:
        if wb is not None:
            wb.close()
