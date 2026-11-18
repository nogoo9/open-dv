"""DuckDB Unity Catalog Emulator for Air-Gapped Local Testing."""

from __future__ import annotations

import os
import re
from typing import Any

import duckdb
import pyarrow as pa

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DESCRIBE_DETAIL_PATTERN = re.compile(r"^\s*DESCRIBE\s+DETAIL\s+([A-Za-z0-9_`.]+)", re.I)
_DESCRIBE_HISTORY_PATTERN = re.compile(r"^\s*DESCRIBE\s+HISTORY\s+([A-Za-z0-9_`.]+)", re.I)
_INFO_SCHEMA_RE = re.compile(r"(?:`?[A-Za-z0-9_]+`?\.)?`?information_schema`?\.`?(\w+)`?", re.I)
_SHOW_CATALOGS_RE = re.compile(r"^\s*SHOW\s+CATALOGS\b", re.I)
_SHOW_SCHEMAS_RE = re.compile(r"^\s*SHOW\s+SCHEMAS(?:\s+(?:IN|FROM)\s+([A-Za-z0-9_`]+))?", re.I)
_DETAIL_COLS = [
    "format",
    "id",
    "name",
    "description",
    "location",
    "createdAt",
    "lastModified",
    "partitionColumns",
    "numFiles",
    "sizeInBytes",
    "properties",
    "minReaderVersion",
    "minWriterVersion",
    "version",
    "numRecords",
]


def _validate_safe_name(name: str) -> str:
    clean = name.strip('`" ')
    if not _IDENTIFIER_PATTERN.match(clean):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return clean


class EmulatorCursor:
    """Cursor wrapper simulating Databricks SQL connector cursor over DuckDB."""

    def __init__(
        self, duck_conn: duckdb.DuckDBPyConnection, emulator: DuckDBUnityCatalogEmulator
    ) -> None:
        self._conn = duck_conn
        self._emulator = emulator
        self._last_relation: duckdb.DuckDBPyRelation | None = None
        self._last_sql: str | None = None
        self._last_params: list[Any] | dict[str, Any] | None = None
        self._static_results: list[tuple[Any, ...]] | None = None
        self._static_description: list[tuple[str, str]] | None = None

    def _clear_last(self) -> EmulatorCursor:
        self._last_relation = self._last_sql = self._last_params = None
        return self

    def execute(
        self, sql: str, parameters: list[Any] | dict[str, Any] | None = None
    ) -> EmulatorCursor:
        cleaned = sql.strip()
        if _SHOW_CATALOGS_RE.match(cleaned):
            self._static_description = [("catalog_name", "string")]
            self._static_results = [(c,) for c in self._emulator.list_catalogs()]
            return self._clear_last()

        schema_match = _SHOW_SCHEMAS_RE.match(cleaned)
        if schema_match:
            c_name = schema_match.group(1)
            t_cat = c_name.replace("`", "") if c_name else self._emulator.default_catalog
            self._static_description = [("databaseName", "string")]
            self._static_results = [(s,) for s in self._emulator.list_schemas(t_cat)]
            return self._clear_last()

        detail_match = _DESCRIBE_DETAIL_PATTERN.match(cleaned)
        if detail_match:
            t_ref = detail_match.group(1).replace("`", "")
            meta = self._emulator.get_delta_metadata(t_ref)
            self._static_description = [(c, "string") for c in _DETAIL_COLS]
            self._static_results = [
                (
                    meta.get("format", "delta"),
                    meta.get("id", "tbl-mock-001"),
                    t_ref,
                    meta.get("description", "Mock Delta Table"),
                    meta.get("location", f"s3://bucket/{t_ref}"),
                    "2026-01-01 00:00:00",
                    "2026-01-01 12:00:00",
                    "[]",
                    meta.get("numFiles", 4),
                    meta.get("sizeInBytes", 102400),
                    "{}",
                    1,
                    2,
                    meta.get("version", 1),
                    meta.get("numRecords", meta.get("numrecords", 100)),
                )
            ]
            return self._clear_last()

        history_match = _DESCRIBE_HISTORY_PATTERN.match(cleaned)
        if history_match:
            t_ref = history_match.group(1).replace("`", "")
            meta = self._emulator.get_delta_metadata(t_ref)
            self._static_description = [
                ("version", "bigint"),
                ("timestamp", "timestamp"),
                ("userId", "string"),
                ("userName", "string"),
                ("operation", "string"),
                ("operationParameters", "string"),
            ]
            self._static_results = [
                (
                    meta.get("version", 1),
                    "2026-01-01 12:00:00",
                    "u1",
                    "user@ex.com",
                    "WRITE",
                    '{"mode":"Append"}',
                )
            ]
            return self._clear_last()

        trans_sql = _INFO_SCHEMA_RE.sub(r"_uc_meta.\1", cleaned).replace("`", "")
        self._static_results = None
        self._static_description = None
        self._last_sql = trans_sql
        self._last_params = parameters

        try:
            if parameters:
                self._last_relation = self._conn.sql(trans_sql, params=parameters)
            else:
                self._last_relation = self._conn.sql(trans_sql)
        except (duckdb.Error, ValueError, TypeError):
            if parameters:
                self._last_relation = self._conn.execute(trans_sql, parameters)
            else:
                self._last_relation = self._conn.execute(trans_sql)
        return self

    @property
    def description(self) -> list[tuple[str, ...]] | None:
        if self._static_description is not None:
            return [(c[0], c[1], None, None, None, None, None) for c in self._static_description]
        if self._last_relation is not None and hasattr(self._last_relation, "description"):
            return self._last_relation.description
        return self._conn.description

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self._static_results is not None:
            return self._static_results
        return self._last_relation.fetchall() if self._last_relation is not None else []

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._static_results is not None:
            return self._static_results[0] if self._static_results else None
        if self._last_relation is None:
            return None
        if hasattr(self._last_relation, "fetchone"):
            return self._last_relation.fetchone()
        rows = self._last_relation.limit(1).fetchall()
        return rows[0] if rows else None

    def fetchall_arrow(self) -> pa.Table:
        if self._static_results is not None:
            if not self._static_description or not self._static_results:
                return pa.Table.from_arrays([], names=[])
            names = [col[0] for col in self._static_description]
            cols = [[row[i] for row in self._static_results] for i in range(len(names))]
            return pa.Table.from_arrays([pa.array(c) for c in cols], names=names)
        if self._last_relation is not None:
            return self._last_relation.to_arrow_table()
        if getattr(self, "_last_sql", None) is not None:
            if getattr(self, "_last_params", None):
                return self._conn.sql(self._last_sql, params=self._last_params).to_arrow_table()
            return self._conn.sql(self._last_sql).to_arrow_table()
        return pa.Table.from_arrays([], names=[])

    def fetchmany_arrow(self, size: int | None = None) -> pa.Table:
        tbl = self.fetchall_arrow()
        return tbl.slice(0, size) if size is not None and size < len(tbl) else tbl

    def close(self) -> None:
        pass


class EmulatorConnection:
    """Connection wrapper providing Databricks SQL connector compatible interface."""

    def __init__(
        self, duck_conn: duckdb.DuckDBPyConnection, emulator: DuckDBUnityCatalogEmulator
    ) -> None:
        self._conn = duck_conn
        self._emulator = emulator

    def cursor(self) -> EmulatorCursor:
        return EmulatorCursor(self._conn.cursor(), self._emulator)

    def close(self) -> None:
        pass


class DuckDBUnityCatalogEmulator:
    """Local DuckDB-backed Unity Catalog Emulator."""

    def __init__(
        self,
        default_catalog: str = "am_prod",
        db_path: str = ":memory:",
        include_walkthrough: bool | None = None,
    ) -> None:
        self.default_catalog = _validate_safe_name(default_catalog)
        self._db_path = db_path
        self._conn = duckdb.connect(db_path)
        self._catalogs: set[str] = {self.default_catalog}
        self._delta_metadata: dict[str, dict[str, Any]] = {}

        self._conn.execute(
            "CREATE MACRO IF NOT EXISTS regexp_like(val, pat) AS regexp_matches(val, pat)"
        )
        self._setup_meta_schema()
        self._setup_catalog(self.default_catalog)

        if include_walkthrough is None:
            include_walkthrough = os.environ.get("OPENDV_UC_WALKTHROUGH", "").lower() in (
                "1",
                "true",
                "yes",
            )
        self.seed_asset_management_ibor(include_walkthrough=include_walkthrough)

    def _setup_meta_schema(self) -> None:
        self._conn.execute(
            "CREATE SCHEMA IF NOT EXISTS _uc_meta;\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.tables (\n"
            "    table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,\n"
            "    table_type VARCHAR, comment VARCHAR\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.columns (\n"
            "    table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,\n"
            "    column_name VARCHAR, ordinal_position INT, data_type VARCHAR,\n"
            "    is_nullable VARCHAR, comment VARCHAR\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.table_constraints (\n"
            "    constraint_catalog VARCHAR, constraint_schema VARCHAR,\n"
            "    constraint_name VARCHAR, table_catalog VARCHAR,\n"
            "    table_schema VARCHAR, table_name VARCHAR,\n"
            "    constraint_type VARCHAR, enforced VARCHAR\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.referential_constraints (\n"
            "    constraint_catalog VARCHAR, constraint_schema VARCHAR,\n"
            "    constraint_name VARCHAR, unique_constraint_catalog VARCHAR,\n"
            "    unique_constraint_schema VARCHAR, unique_constraint_name VARCHAR\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.key_column_usage (\n"
            "    constraint_catalog VARCHAR, constraint_schema VARCHAR,\n"
            "    constraint_name VARCHAR, table_catalog VARCHAR,\n"
            "    table_schema VARCHAR, table_name VARCHAR,\n"
            "    column_name VARCHAR, ordinal_position INT\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.column_tags (\n"
            "    catalog_name VARCHAR, schema_name VARCHAR, table_name VARCHAR,\n"
            "    column_name VARCHAR, tag_name VARCHAR, tag_value VARCHAR\n"
            ");\n"
            "CREATE TABLE IF NOT EXISTS _uc_meta.table_tags (\n"
            "    catalog_name VARCHAR, schema_name VARCHAR, table_name VARCHAR,\n"
            "    tag_name VARCHAR, tag_value VARCHAR\n"
            ");"
        )

    def _setup_catalog(self, catalog: str) -> None:
        cat = _validate_safe_name(catalog)
        self._catalogs.add(cat)
        self._conn.execute(f"ATTACH IF NOT EXISTS ':memory:' AS {cat}")

    def list_catalogs(self) -> list[str]:
        return sorted(self._catalogs)

    def list_schemas(self, catalog: str) -> list[str]:
        cat = _validate_safe_name(catalog)
        res = self._conn.execute(
            "SELECT DISTINCT table_schema FROM _uc_meta.tables WHERE table_catalog = ?", [cat]
        ).fetchall()
        return sorted({r[0] for r in res})

    def set_delta_metadata(self, table_ref: str, metadata: dict[str, Any]) -> None:
        self._delta_metadata[table_ref.replace("`", "").lower()] = metadata

    def get_delta_metadata(self, table_ref: str) -> dict[str, Any]:
        return self._delta_metadata.get(
            table_ref.replace("`", "").lower(),
            {"format": "delta", "version": 1, "description": "Mock Delta Table"},
        )

    def register_table(
        self,
        catalog: str,
        schema: str,
        table: str,
        columns: list[tuple[str, str, bool, str | None]],
        rows: list[tuple[Any, ...]] | None = None,
        primary_key: list[str] | None = None,
        foreign_keys: list[dict[str, Any]] | None = None,
        column_tags: dict[str, dict[str, str]] | None = None,
        table_tags: dict[str, str] | None = None,
        comment: str | None = None,
        version: int = 1,
    ) -> None:
        cat = _validate_safe_name(catalog)
        sch = _validate_safe_name(schema)
        tbl = _validate_safe_name(table)
        self._setup_catalog(cat)
        self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {cat}.{sch}")

        col_defs = [
            f"{_validate_safe_name(n)} {t} {'NOT NULL' if not null else ''}".strip()
            for n, t, null, _ in columns
        ]
        self._conn.execute(f"CREATE TABLE IF NOT EXISTS {cat}.{sch}.{tbl} ({', '.join(col_defs)})")

        if rows:
            ph = ", ".join(["?"] * len(columns))
            for row in rows:
                self._conn.execute(f"INSERT INTO {cat}.{sch}.{tbl} VALUES ({ph})", list(row))

        del_q = (
            "DELETE FROM _uc_meta.{} "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?"
        )
        self._conn.execute(del_q.format("tables"), [cat, sch, tbl])
        self._conn.execute(
            "INSERT INTO _uc_meta.tables VALUES (?, ?, ?, 'MANAGED', ?)",
            [cat, sch, tbl, comment or f"Table {cat}.{sch}.{tbl}"],
        )

        self._conn.execute(del_q.format("columns"), [cat, sch, tbl])
        self._conn.execute(del_q.format("table_constraints"), [cat, sch, tbl])
        self._conn.execute(del_q.format("key_column_usage"), [cat, sch, tbl])
        del_tags = (
            "DELETE FROM _uc_meta.{} "
            "WHERE catalog_name = ? AND schema_name = ? AND table_name = ?"
        )
        self._conn.execute(del_tags.format("column_tags"), [cat, sch, tbl])
        self._conn.execute(del_tags.format("table_tags"), [cat, sch, tbl])
        for idx, (c_name, c_type, c_null, c_comment) in enumerate(columns, start=1):
            self._conn.execute(
                "INSERT INTO _uc_meta.columns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [cat, sch, tbl, c_name, idx, c_type, "YES" if c_null else "NO", c_comment or ""],
            )

        if primary_key:
            pk_name = f"pk_{tbl}"
            self._conn.execute(
                "INSERT INTO _uc_meta.table_constraints VALUES "
                "(?, ?, ?, ?, ?, ?, 'PRIMARY KEY', 'RELY')",
                [cat, sch, pk_name, cat, sch, tbl],
            )
            for idx, pk_col in enumerate(primary_key, start=1):
                self._conn.execute(
                    "INSERT INTO _uc_meta.key_column_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [cat, sch, pk_name, cat, sch, tbl, pk_col, idx],
                )

        if foreign_keys:
            for fk in foreign_keys:
                from_col = fk.get("from_column", "")
                fk_name = fk.get("constraint_name", f"fk_{tbl}_{from_col}")
                target_table = fk.get("target_table", "")
                target_cols = fk.get("target_columns") or (
                    [fk["target_column"]] if "target_column" in fk else []
                )
                target_uq_name = fk.get(
                    "target_constraint_name",
                    (
                        f"uq_{target_table}_{'_'.join(target_cols)}"
                        if target_cols
                        else f"pk_{target_table}"
                    ),
                )
                self._conn.execute(
                    "INSERT INTO _uc_meta.table_constraints VALUES "
                    "(?, ?, ?, ?, ?, ?, 'FOREIGN KEY', 'NORELY')",
                    [cat, sch, fk_name, cat, sch, tbl],
                )
                self._conn.execute(
                    "INSERT INTO _uc_meta.referential_constraints VALUES (?, ?, ?, ?, ?, ?)",
                    [cat, sch, fk_name, cat, sch, target_uq_name],
                )
                from_cols = fk.get("from_columns") or ([from_col] if from_col else [])
                for idx, col in enumerate(from_cols, start=1):
                    self._conn.execute(
                        "INSERT INTO _uc_meta.key_column_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [cat, sch, fk_name, cat, sch, tbl, col, idx],
                    )
                for idx, col in enumerate(target_cols, start=1):
                    self._conn.execute(
                        "INSERT INTO _uc_meta.key_column_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [cat, sch, target_uq_name, cat, sch, target_table, col, idx],
                    )

        if column_tags:
            for c_name, tags in column_tags.items():
                for tag_k, tag_v in tags.items():
                    self._conn.execute(
                        "INSERT INTO _uc_meta.column_tags VALUES (?, ?, ?, ?, ?, ?)",
                        [cat, sch, tbl, c_name, tag_k, tag_v],
                    )

        if table_tags:
            for tag_k, tag_v in table_tags.items():
                self._conn.execute(
                    "INSERT INTO _uc_meta.table_tags VALUES (?, ?, ?, ?, ?)",
                    [cat, sch, tbl, tag_k, tag_v],
                )

        self.set_delta_metadata(
            f"{cat}.{sch}.{tbl}",
            {
                "format": "delta",
                "version": version,
                "description": comment or f"Delta table {cat}.{sch}.{tbl}",
                "numFiles": 4,
                "sizeInBytes": 131072,
            },
        )

    def seed_walkthrough_tables(self, catalog: str = "am_prod", schema: str = "ibor") -> None:
        """Seed UI walkthrough tables (positions_snapshot and custody_holdings)."""
        from opendv_testkit.uc_fixtures import seed_walkthrough_tables

        seed_walkthrough_tables(self, catalog=catalog, schema=schema)

    def seed_asset_management_ibor(self, include_walkthrough: bool = False) -> None:
        """Seed canonical Asset Management IBOR fixtures into am_prod.ibor."""
        from opendv_testkit.uc_fixtures import seed_asset_management_ibor

        seed_asset_management_ibor(self, include_walkthrough=include_walkthrough)

    def connect(self) -> EmulatorConnection:
        return EmulatorConnection(self._conn, self)

    def close(self) -> None:
        self._conn.close()


__all__ = ["DuckDBUnityCatalogEmulator", "EmulatorConnection", "EmulatorCursor"]
