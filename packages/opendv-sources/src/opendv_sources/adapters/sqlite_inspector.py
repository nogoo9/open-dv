"""SQLite catalog inspector and multi-table introspector (WS2A.3).

Provides read-only multi-table enumeration, PRAGMA PK/FK extraction,
row counts, and CAS-uploadable multi-table feed expansion.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_sources._resolution import redact_credentials, resolve_adapter_path
from opendv_sources.config.defaults import SourceDefaultsConfig, get_source_defaults
from opendv_sources.exceptions import SourceLoadError, SourceNotFoundError, SourceTableEmptyError
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    ForeignKeyHint,
    NamespaceDescriptor,
    ResolvedTableSource,
    SourceCapabilities,
    TableSchemaDescriptor,
    TableSummary,
    TabularProfileSummary,
)
from opendv_sources.models.specs import (
    NamespaceRef,
    SamplingPolicy,
    SourceSpec,
    TableRef,
)
from opendv_sources.spi.catalog import CatalogSourceAdapter


class SqliteCatalogInspector(CatalogSourceAdapter):
    """Catalog inspector providing read-only multi-table introspection for SQLite databases."""

    def __init__(
        self,
        *,
        adapter_name: str = "sqlite",
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        allow_temp: bool | None = None,
        config: SourceDefaultsConfig | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self.workspace_root = workspace_root
        self.allowed_roots = allowed_roots
        self.allow_temp = (workspace_root is None) if allow_temp is None else allow_temp
        self._config = config or get_source_defaults()

    @property
    def adapter_name(self) -> str:
        return self._adapter_name

    def _resolve_db_path(self, spec: SourceSpec) -> Path:
        """Resolve database path from spec URI, verifying existence and jail boundaries."""
        raw_uri = spec.uri
        if not raw_uri or not raw_uri.strip():
            raise SourceNotFoundError("SqliteCatalogInspector requires a non-empty 'uri'")

        uri_str = raw_uri.strip()
        clean_uri, _ = uri_str.split("#", 1) if "#" in uri_str else (uri_str, None)

        for pfx in self._config.sqlite_schemes:
            for sep in (":///", "://", ":"):
                if clean_uri.lower().startswith(pfx + sep):
                    clean_uri = clean_uri[len(pfx + sep) :]
                    break

        if len(clean_uri) >= 3 and clean_uri[0] in "/\\" and clean_uri[2] == ":":
            clean_uri = clean_uri[1:]

        resolved = resolve_adapter_path(clean_uri, self)
        if not resolved.exists():
            clean_raw = redact_credentials(raw_uri)
            clean_res = redact_credentials(str(resolved))
            raise SourceNotFoundError(
                f"SQLite database file not found: '{clean_raw}' (resolved: '{clean_res}')"
            )
        if resolved.is_dir():
            raise SourceLoadError(f"Expected SQLite database file, found directory: '{resolved}'")
        if resolved.stat().st_size == 0:
            clean_raw = redact_credentials(raw_uri)
            raise SourceTableEmptyError(f"SQLite database file '{clean_raw}' is empty (0 bytes).")

        return resolved

    def _connect_ro(self, db_path: Path) -> sqlite3.Connection:
        """Connect to SQLite database in strict read-only mode."""
        norm_path = db_path.resolve().as_posix()
        uri_str = (
            f"file:///{norm_path}{self._config.sqlite_uri_query}"
            if not norm_path.startswith("/")
            else f"file:{norm_path}{self._config.sqlite_uri_query}"
        )
        try:
            return sqlite3.connect(uri_str, uri=True, check_same_thread=False)
        except sqlite3.Error as exc:
            raise SourceLoadError(
                f"Failed to open SQLite database '{db_path.name}': {exc}"
            ) from exc

    def list_namespaces(self, spec: SourceSpec) -> list[NamespaceDescriptor]:
        """Discover database namespaces for the SQLite file."""
        db_path = self._resolve_db_path(spec)
        return [
            NamespaceDescriptor(
                catalog=None,
                schema_name="main",
                description=f"Default schema for SQLite database {db_path.name}",
            )
        ]

    def list_tables(
        self,
        spec: SourceSpec,
        namespace: NamespaceRef | None = None,
    ) -> list[TableSummary]:
        """List user tables, skipping internal tables and extracting row estimates."""
        db_path = self._resolve_db_path(spec)
        conn = self._connect_ro(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
            )
            raw_entries = cursor.fetchall()
            summaries: list[TableSummary] = []

            for name, tbl_type in raw_entries:
                if self._is_internal_table(name):
                    continue

                table_type = "VIEW" if tbl_type.lower() == "view" else "BASE_TABLE"
                row_count: int | None = None
                try:
                    cursor.execute(f'SELECT count(*) FROM "{name}"')
                    cnt_res = cursor.fetchone()
                    if cnt_res and cnt_res[0] is not None:
                        row_count = int(cnt_res[0])
                except sqlite3.Error:
                    row_count = None

                col_count: int | None = None
                try:
                    cursor.execute(f'PRAGMA table_info("{name}")')
                    cols = cursor.fetchall()
                    col_count = len(cols)
                except sqlite3.Error:
                    col_count = None

                summaries.append(
                    TableSummary(
                        table_name=name,
                        namespace=namespace or NamespaceRef(schema_name="main"),
                        table_type=table_type,
                        row_count_estimate=row_count,
                        column_count=col_count,
                    )
                )
            return summaries
        finally:
            conn.close()

    def inspect_table(self, spec: SourceSpec, table: TableRef) -> TableSchemaDescriptor:
        """Inspect schema, primary keys, and foreign keys via PRAGMAs."""
        db_path = self._resolve_db_path(spec)
        conn = self._connect_ro(db_path)
        tbl_name = table.table_name
        try:
            cursor = conn.cursor()
            cursor.execute(f'PRAGMA table_info("{tbl_name}")')
            raw_cols = cursor.fetchall()
            if not raw_cols:
                raise SourceNotFoundError(
                    f"Table '{tbl_name}' not found in SQLite database '{db_path.name}'"
                )

            cursor.execute(f'PRAGMA foreign_key_list("{tbl_name}")')
            raw_fks = cursor.fetchall()
            from collections import defaultdict

            fk_groups: dict[int, list[tuple[int, str, str, str]]] = defaultdict(list)
            for fk_row in raw_fks:
                cid = int(fk_row[0])
                seq = int(fk_row[1])
                target_table = str(fk_row[2])
                from_col = str(fk_row[3])
                target_col = str(fk_row[4])
                fk_groups[cid].append((seq, target_table, from_col, target_col))

            fks_by_from_col: dict[str, ForeignKeyHint] = {}
            foreign_keys_list: list[ForeignKeyHint] = []

            for _cid, items in fk_groups.items():
                items.sort(key=lambda x: x[0])
                target_table = items[0][1]
                src_cols = [it[2] for it in items]
                tgt_cols = [it[3] for it in items]
                from_col = src_cols[0]
                target_col = tgt_cols[0]
                cname = f"fk_{tbl_name}_{'_'.join(src_cols)}_{target_table}"
                fk_hint = ForeignKeyHint(
                    target_table=target_table,
                    target_column=target_col,
                    source_column=from_col,
                    source_columns=src_cols,
                    target_columns=tgt_cols,
                    constraint_name=cname,
                )
                foreign_keys_list.append(fk_hint)
                for sc in src_cols:
                    fks_by_from_col[sc] = fk_hint

            columns: list[ColumnDescriptor] = []
            pk_columns_ordered: list[tuple[int, str]] = []

            for col_row in raw_cols:
                col_name = str(col_row[1])
                phys_type = str(col_row[2]) if col_row[2] else "TEXT"
                not_null = bool(col_row[3])
                pk_val = int(col_row[5]) if col_row[5] else 0

                is_pk = pk_val > 0
                pk_ord = pk_val if is_pk else None
                if is_pk:
                    pk_columns_ordered.append((pk_val, col_name))

                nullable = (not not_null) and (not is_pk)
                col_fk = fks_by_from_col.get(col_name)

                columns.append(
                    ColumnDescriptor(
                        name=col_name,
                        physical_type=phys_type.upper(),
                        nullable=nullable,
                        is_primary_key=is_pk,
                        primary_key_ordinal=pk_ord,
                        foreign_key_ref=col_fk,
                    )
                )

            pk_columns_ordered.sort(key=lambda x: x[0])
            pk_col_names = [col for _, col in pk_columns_ordered]

            row_count: int | None = None
            try:
                cursor.execute(f'SELECT count(*) FROM "{tbl_name}"')
                cnt_row = cursor.fetchone()
                if cnt_row and cnt_row[0] is not None:
                    row_count = int(cnt_row[0])
            except sqlite3.Error:
                row_count = None

            fingerprint = self._compute_table_fingerprint(tbl_name, columns, row_count)

            return TableSchemaDescriptor(
                table_name=tbl_name,
                namespace=table.namespace or NamespaceRef(schema_name="main"),
                columns=columns,
                primary_key_columns=pk_col_names,
                foreign_keys=foreign_keys_list,
                row_count_estimate=row_count,
                fingerprint=fingerprint,
            )
        finally:
            conn.close()

    def expand_feed(
        self,
        spec: SourceSpec,
        table: TableRef | None = None,
    ) -> list[ResolvedTableSource]:
        """Expand a multi-table SQLite feed into concrete per-table ResolvedTableSource items."""
        db_path = self._resolve_db_path(spec)
        source_sys = str(spec.options.get("source_system") or db_path.stem)

        if table is not None:
            schema = self.inspect_table(spec, table)
            sub_spec = spec.model_copy(
                update={"options": {**spec.options, "table": table.table_name}}
            )
            return [
                ResolvedTableSource(
                    source_system=source_sys,
                    table_name=table.table_name,
                    qualified_name=f"{source_sys}.{table.table_name}",
                    schema_descriptor=schema,
                    spec=sub_spec,
                    physical_location=str(db_path),
                    execution_mode="DIRECT_DUCKDB_ATTACH",
                )
            ]

        all_summaries = self.list_tables(spec)
        sel = spec.table_selection

        selected_names: list[str] = []
        if sel is None or sel.mode == "ALL_USER_TABLES":
            selected_names = [s.table_name for s in all_summaries]
        elif sel.mode == "EXPLICIT":
            allowed = set(sel.tables)
            selected_names = [s.table_name for s in all_summaries if s.table_name in allowed]
        elif sel.mode == "PATTERN":
            inc_re = re.compile(sel.include_pattern) if sel.include_pattern else None
            exc_re = re.compile(sel.exclude_pattern) if sel.exclude_pattern else None
            for s in all_summaries:
                if inc_re and not inc_re.search(s.table_name):
                    continue
                if exc_re and exc_re.search(s.table_name):
                    continue
                selected_names.append(s.table_name)

        resolved_sources: list[ResolvedTableSource] = []
        for name in selected_names:
            tbl_ref = TableRef(table_name=name)
            schema = self.inspect_table(spec, tbl_ref)
            sub_spec = spec.model_copy(update={"options": {**spec.options, "table": name}})
            resolved_sources.append(
                ResolvedTableSource(
                    source_system=source_sys,
                    table_name=name,
                    qualified_name=f"{source_sys}.{name}",
                    schema_descriptor=schema,
                    spec=sub_spec,
                    physical_location=str(db_path),
                    execution_mode="DIRECT_DUCKDB_ATTACH",
                )
            )

        return resolved_sources

    def profile_pushdown(
        self,
        spec: SourceSpec,
        table: TableRef,
        policy: SamplingPolicy,
    ) -> TabularProfileSummary:
        """Execute aggregate SQL profiling inside SQLite."""
        db_path = self._resolve_db_path(spec)
        conn = self._connect_ro(db_path)
        tbl_name = table.table_name
        try:
            cursor = conn.cursor()
            cursor.execute(f'SELECT count(*) FROM "{tbl_name}"')
            tot = cursor.fetchone()[0] or 0

            cursor.execute(f'PRAGMA table_info("{tbl_name}")')
            cols = [r[1] for r in cursor.fetchall()]

            stats: dict[str, dict[str, Any]] = {}
            for col in cols:
                try:
                    cursor.execute(
                        f'SELECT count(*) - count("{col}"), count(DISTINCT "{col}") '
                        f'FROM "{tbl_name}"'
                    )
                    null_cnt, dist_cnt = cursor.fetchone()
                    stats[col] = {
                        "null_count": int(null_cnt or 0),
                        "approx_distinct": int(dist_cnt or 0),
                    }
                except sqlite3.Error:
                    stats[col] = {"null_count": 0, "approx_distinct": 0}

            return TabularProfileSummary(
                table_name=tbl_name,
                namespace=table.namespace,
                total_row_count=int(tot),
                column_stats=stats,
            )
        finally:
            conn.close()

    def capabilities(self, spec: SourceSpec | None = None) -> SourceCapabilities:
        """Declare SQLite catalog capabilities."""
        return SourceCapabilities(
            execution_mode="DIRECT_DUCKDB_ATTACH",
            supports_multi_table=True,
            supports_catalog_hierarchy=False,
            supports_foreign_keys=True,
            supports_governance_tags=False,
            supports_time_travel=False,
            supports_streaming=True,
            supports_pushdown=True,
            supports_drift_detection=True,
            client_uploadable=True,
            server_resolved=True,
        )

    def _is_internal_table(self, name: str) -> bool:
        """Return True if table name is a configured internal system table."""
        lower = name.lower()
        if lower in self._config.sqlite_internal_tables:
            return True
        return any(lower.startswith(pfx) for pfx in self._config.sqlite_internal_prefixes)

    def _compute_table_fingerprint(
        self,
        table_name: str,
        columns: list[ColumnDescriptor],
        row_count: int | None,
    ) -> str:
        """Deterministic fingerprint of table schema and metadata for CAS provenance."""
        h = hashlib.sha256()
        h.update(table_name.encode())
        for col in columns:
            token = f":{col.name}:{col.physical_type}:{col.nullable}:{col.is_primary_key}"
            h.update(token.encode())
        h.update(f":rows={row_count or 0}".encode())
        return h.hexdigest()


__all__ = ["SqliteCatalogInspector"]
