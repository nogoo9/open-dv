"""Persistent DuckDB sandbox storage and read-only rehydration (`D11`).

Stores run-scoped sandbox snapshots under ``<state_dir>/sandboxes/<run_id>.duckdb``
and rehydrates them strictly read-only with ``enable_external_access=False``.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import duckdb

from opendv_core.paths import resolve_workspace_path
from opendv_duckdb_sandbox.compiler_4zone.bundle import Compiled4ZoneBundle
from opendv_duckdb_sandbox.sql_guard import (
    enforce_external_access_disabled,
    validate_safe_sql,
)

_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SEARCH_SCHEMA_ORDER: tuple[str, ...] = (
    "raw_vault",
    "business_vault",
    "staging",
    "bronze",
    "main",
)


def _validate_run_id(run_id: str) -> str:
    cleaned = str(run_id or "").strip()
    if not _SAFE_RUN_ID_RE.fullmatch(cleaned) or ".." in cleaned:
        raise ValueError(f"Invalid sandbox run_id: {run_id!r}")
    return cleaned


def _validate_ident(name: str, *, kind: str = "identifier") -> str:
    cleaned = str(name or "").strip()
    if not _SAFE_IDENT_RE.fullmatch(cleaned):
        raise ValueError(f"Unsafe SQL {kind}: {name!r}")
    return cleaned


def get_sandbox_db_path(state_dir: Path, run_id: str) -> Path:
    """Return the contained ``<state_dir>/sandboxes/<run_id>.duckdb`` path."""
    safe_run_id = _validate_run_id(run_id)
    sandboxes_dir = state_dir.resolve() / "sandboxes"
    return resolve_workspace_path(
        f"{safe_run_id}.duckdb",
        workspace_root=sandboxes_dir,
        base_dir=sandboxes_dir,
        allow_temp=False,
    )


def _clone_conn_tables(
    source_conn: duckdb.DuckDBPyConnection,
    target_conn: duckdb.DuckDBPyConnection,
) -> None:
    tables = source_conn.execute(
        """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema IN ('bronze', 'staging', 'raw_vault', 'business_vault', 'main')
        ORDER BY table_schema, table_name
        """
    ).fetchall()
    for schema, _, _ in tables:
        safe_schema = _validate_ident(schema, kind="schema")
        target_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{safe_schema}"')
    for schema, table_name, table_type in tables:
        if str(table_type).upper() != "BASE TABLE":
            continue
        safe_schema = _validate_ident(schema, kind="schema")
        safe_table = _validate_ident(table_name, kind="table")
        cols = source_conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
            """,
            [schema, table_name],
        ).fetchall()
        if not cols:
            continue
        col_defs = ", ".join(
            f'"{_validate_ident(col_name, kind="column")}" {col_type}'
            for col_name, col_type in cols
        )
        target_conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{safe_schema}"."{safe_table}" ({col_defs})'
        )
        rows = source_conn.execute(f'SELECT * FROM "{safe_schema}"."{safe_table}"').fetchall()
        if rows:
            placeholders = ", ".join(["?"] * len(cols))
            target_conn.executemany(
                f'INSERT INTO "{safe_schema}"."{safe_table}" VALUES ({placeholders})',
                rows,
            )


def persist_4zone_sandbox_db(
    db_path: Path,
    *,
    bundle: Compiled4ZoneBundle | None = None,
    source_conn: duckdb.DuckDBPyConnection | None = None,
) -> Path:
    """Materialize a 4-zone DuckDB sandbox database file atomically."""
    if bundle is None and source_conn is None:
        raise ValueError("persist_4zone_sandbox_db requires either bundle or source_conn")

    resolved_path = db_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = resolved_path.with_name(f".{resolved_path.stem}.{os.getpid()}.tmp.duckdb")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = duckdb.connect(str(tmp_path))
    try:
        if bundle is not None:
            for zone_sql in (
                bundle.zone_0_sql,
                bundle.zone_1_sql,
                bundle.zone_2_sql,
                bundle.zone_3_sql,
                bundle.zone_4_sql,
            ):
                if zone_sql and zone_sql.strip():
                    conn.execute(zone_sql)
        elif source_conn is not None:
            _clone_conn_tables(source_conn, conn)
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, resolved_path)
    return resolved_path


def open_readonly_sandbox(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a persisted sandbox `.duckdb` strictly read-only with external access disabled."""
    resolved = db_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Persisted sandbox database not found: {resolved}")
    conn = duckdb.connect(
        str(resolved),
        read_only=True,
        config={
            "max_memory": "512MB",
            "threads": "2",
            "enable_external_access": False,
        },
    )
    enforce_external_access_disabled(conn)
    existing_schemas = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_schema FROM information_schema.tables"
        ).fetchall()
    }
    search_list = [s for s in _SEARCH_SCHEMA_ORDER if s in existing_schemas]
    if search_list:
        conn.execute(f"SET search_path = '{','.join(search_list)}'")
    return conn


def _resolve_table_target(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
) -> tuple[str, str]:
    raw = str(table_name or "").strip()
    if "." in raw:
        parts = raw.split(".", 1)
        schema = _validate_ident(parts[0], kind="schema")
        table = _validate_ident(parts[1], kind="table")
        hit = conn.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            LIMIT 1
            """,
            [schema, table],
        ).fetchone()
        if hit is not None:
            return str(hit[0]), str(hit[1])
        raise KeyError(f"Sandbox table not found: {schema}.{table}")

    safe_table = _validate_ident(raw, kind="table")
    rows = conn.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [safe_table],
    ).fetchall()
    if not rows:
        raise KeyError(f"Sandbox table not found: {safe_table}")
    by_schema = {str(r[0]): str(r[1]) for r in rows}
    for preferred in _SEARCH_SCHEMA_ORDER:
        if preferred in by_schema:
            return preferred, by_schema[preferred]
    return str(rows[0][0]), str(rows[0][1])


def query_sandbox_table_rows(
    db_path: Path,
    table_name: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Read paginated rows from a persisted DuckDB sandbox table."""
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    conn = open_readonly_sandbox(db_path)
    try:
        schema, table = _resolve_table_target(conn, table_name)
        total_row = conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()
        total_count = int(total_row[0]) if total_row is not None else 0
        cursor = conn.execute(
            f'SELECT * FROM "{schema}"."{table}" LIMIT ? OFFSET ?',
            [safe_limit, safe_offset],
        )
        desc = cursor.description or []
        columns = [str(col[0]) for col in desc]
        raw_rows = cursor.fetchall()
        rows = [
            {col: _json_safe_cell(val) for col, val in zip(columns, row, strict=True)}
            for row in raw_rows
        ]
        return {
            "table_name": table,
            "schema_name": schema,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
            "truncated": (safe_offset + len(rows)) < total_count,
        }
    finally:
        conn.close()


def execute_readonly_sandbox_sql(
    db_path: Path,
    sql: str,
    *,
    max_rows: int = 500,
) -> dict[str, Any]:
    """Validate and execute a read-only SQL query against a persisted sandbox `.duckdb`."""
    validate_safe_sql(sql)
    safe_max_rows = max(1, min(int(max_rows), 2000))
    conn = open_readonly_sandbox(db_path)
    started = time.perf_counter()
    try:
        cursor = conn.execute(sql.strip().rstrip(";"))
        desc = cursor.description or []
        columns = [str(col[0]) for col in desc]
        fetched = cursor.fetchmany(safe_max_rows + 1)
        truncated = len(fetched) > safe_max_rows
        kept = fetched[:safe_max_rows]
        rows = [
            {col: _json_safe_cell(val) for col, val in zip(columns, row, strict=True)}
            for row in kept
        ]
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "duration_ms": duration_ms,
        }
    except duckdb.Error as exc:
        raise ValueError(f"Sandbox SQL query failed: {exc}") from exc
    finally:
        conn.close()


def _json_safe_cell(val: Any) -> Any:
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, bytes):
        return val.hex()
    return str(val)


__all__ = [
    "execute_readonly_sandbox_sql",
    "get_sandbox_db_path",
    "open_readonly_sandbox",
    "persist_4zone_sandbox_db",
    "query_sandbox_table_rows",
]
