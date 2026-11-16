"""DuckDB attach source adapter for SQLite and DuckDB database tables (T16 / DUP-04)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from opendv_core.paths import PathContainmentError
from opendv_sources._resolution import redact_credentials, resolve_adapter_path
from opendv_sources.exceptions import (
    SourceLoadError,
    SourceNotFoundError,
    SourceTableEmptyError,
    UnsupportedFormatError,
)
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    NamespaceDescriptor,
    ResolvedTableSource,
    SourceCapabilities,
    SourceDescriptor,
    TableSchemaDescriptor,
    TableSummary,
    TabularProfileSummary,
)
from opendv_sources.models.specs import NamespaceRef, SamplingPolicy, SourceSpec, TableRef
from opendv_sources.models.streams import SourceMetadata, StreamChunk
from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.catalog import CatalogSourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

_KNOWN_SCHEMES = ("duckdb_attach", "sqlite", "duckdb")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def validate_identifier(name: str) -> str:
    """Validate that a table/view identifier contains only safe alphanumeric characters."""
    clean = name.strip('`" ')
    if not _IDENTIFIER_PATTERN.match(clean):
        raise ValueError(f"Unsafe SQL identifier rejected: {name!r}")
    return clean


def quote_ident(ident: str) -> str:
    """Safely quote a SQL identifier, escaping internal double quotes by doubling them."""
    clean = ident.strip()
    if clean.startswith('"') and clean.endswith('"') and len(clean) >= 2:
        inner = clean[1:-1]
        if re.fullmatch(r'(?:[^"]|"")*', inner):
            return clean
        clean = inner
    escaped = clean.replace('"', '""')
    return f'"{escaped}"'


def qualify_bronze_view_name(source: str, table: str) -> str:
    """Construct collision-safe qualified bronze view name: bronze_{source}_{table}."""
    clean_src = re.sub(r"[^A-Za-z0-9_]", "_", source).strip("_") or "src"
    clean_tbl = re.sub(r"[^A-Za-z0-9_]", "_", table).strip("_") or "tbl"
    return f"bronze_{clean_src}_{clean_tbl}"


class DuckdbAttachSourceAdapter(SourceAdapter, TabularSourceAdapter, CatalogSourceAdapter):
    """Source adapter exposing tables from local SQLite or DuckDB database files via ATTACH."""

    schemes: tuple[str, ...] = ("duckdb_attach", "sqlite", "duckdb")

    def __init__(
        self,
        *,
        adapter_name: str = "duckdb_attach",
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        allow_temp: bool | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self.workspace_root = workspace_root
        self.allowed_roots = allowed_roots
        self.allow_temp = (workspace_root is None) if allow_temp is None else allow_temp

    @property
    def adapter_name(self) -> str:
        return self._adapter_name

    def _parse_spec(self, spec: SourceSpec) -> tuple[Path, str | None, str | None]:
        raw_uri = spec.uri
        if not raw_uri or not raw_uri.strip():
            raise SourceNotFoundError("DuckdbAttachSourceAdapter requires a non-empty 'uri'")

        uri_str = raw_uri.strip()
        uri_no_frag, frag_tbl = uri_str.split("#", 1) if "#" in uri_str else (uri_str, None)
        scheme, raw_p = None, uri_no_frag
        for pfx in _KNOWN_SCHEMES:
            for sep in (":///", "://", ":"):
                if uri_no_frag.lower().startswith(pfx + sep):
                    scheme, raw_p = pfx, uri_no_frag[len(pfx + sep) :]
                    break
            if scheme:
                break

        if len(raw_p) >= 3 and raw_p[0] in "/\\" and raw_p[2] == ":":
            raw_p = raw_p[1:]

        resolved = resolve_adapter_path(raw_p, self)
        if not resolved.exists():
            clean_raw = redact_credentials(raw_p)
            clean_res = redact_credentials(str(resolved))
            msg = f"Database file not found: '{clean_raw}' (resolved: '{clean_res}')"
            raise SourceNotFoundError(msg)
        if resolved.is_dir():
            raise SourceLoadError(f"Expected database file, found directory: '{resolved}'")
        if resolved.stat().st_size == 0:
            clean_raw = redact_credentials(raw_p)
            raise SourceTableEmptyError(f"Database file '{clean_raw}' is empty (0 bytes).")

        tbl = spec.options.get("table") or spec.options.get("table_name") or frag_tbl
        return resolved, tbl, scheme

    def _detect_db_type(self, spec: SourceSpec, scheme: str | None, path: Path) -> str:
        fmt = (getattr(spec, "format", None) or "").lower().strip(".")
        cand = (scheme or spec.adapter or self.adapter_name).lower()
        if fmt in {"sqlite", "sqlite3"} or cand in {"sqlite", "sqlite3"}:
            return "sqlite"
        if fmt in {"duckdb", "duck", "ddb"} or cand == "duckdb":
            return "duckdb"

        suf = path.suffix.lower()
        if suf in {".sqlite", ".sqlite3", ".db"}:
            return "sqlite"
        if suf in {".duckdb", ".ddb"}:
            return "duckdb"

        try:
            with open(path, "rb") as f:
                hdr = f.read(32)
            if hdr.startswith(b"SQLite format 3\x00"):
                return "sqlite"
            if len(hdr) >= 12 and hdr[8:12] == b"DUCK":
                return "duckdb"
        except OSError as exc:
            raise SourceLoadError(f"Failed to read header from '{path}': {exc}") from exc

        raise UnsupportedFormatError(
            f"Cannot determine db format for '{path.name}'.",
            requested=suf,
            available=["sqlite", "duckdb"],
        )

    def _get_or_attach_db(self, conn: duckdb.DuckDBPyConnection, path: Path, db_type: str) -> str:
        resolved = path.resolve()
        try:
            for row in conn.execute(
                "SELECT database_name, path FROM duckdb_databases() WHERE path IS NOT NULL"
            ).fetchall():
                if row[1] and Path(row[1]).resolve() == resolved:
                    return str(row[0])
        except duckdb.Error:
            pass

        esc = str(resolved).replace("\\", "/").replace("'", "''")
        alias = f"attach_{db_type}_{hashlib.md5(esc.encode()).hexdigest()[:8]}"
        att = "(TYPE SQLITE, READ_ONLY)" if db_type == "sqlite" else "(READ_ONLY)"
        try:
            conn.execute(f"ATTACH IF NOT EXISTS '{esc}' AS {quote_ident(alias)} {att}")
        except duckdb.Error as exc:
            raise SourceLoadError(f"Failed to attach {db_type} db '{path.name}': {exc}") from exc
        return alias

    def register(
        self, spec: SourceSpec, conn: duckdb.DuckDBPyConnection, relation_name: str
    ) -> None:
        validate_identifier(relation_name)
        db_path, tbl, scheme = self._parse_spec(spec)
        alias = self._get_or_attach_db(conn, db_path, self._detect_db_type(spec, scheme, db_path))
        try:
            q_alias = quote_ident(alias)
            tables = [r[0] for r in conn.execute(f"SHOW TABLES FROM {q_alias}").fetchall()]
        except duckdb.Error as exc:
            raise SourceLoadError(f"Failed to inspect tables in '{db_path.name}': {exc}") from exc

        if not tables:
            raise SourceTableEmptyError(f"Database '{db_path.name}' contains zero tables.")
        if tbl is None:
            if len(tables) == 1:
                tbl = tables[0]
            else:
                raise SourceLoadError(
                    f"Multiple tables in '{db_path.name}': {tables}. Specify options['table']."
                )
        elif tbl not in tables:
            raise SourceTableEmptyError(
                f"Table '{tbl}' not found in '{db_path.name}'. Available: {tables}"
            )

        validate_identifier(tbl)
        q_rel = quote_ident(relation_name)
        q_target = f"{quote_ident(alias)}.{quote_ident(tbl)}"
        try:
            conn.execute(f"CREATE OR REPLACE VIEW {q_rel} AS SELECT * FROM {q_target}")
            res = conn.execute(f"SELECT count(*) FROM {q_rel}").fetchall()
            count = int(res[0][0]) if res and res[0] else 0
        except duckdb.Error as exc:
            raise SourceLoadError(f"Failed to register '{relation_name}': {exc}") from exc

        if count == 0:
            msg = f"Requested table '{tbl}' in '{db_path.name}' is empty (0 rows)."
            raise SourceTableEmptyError(msg)

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        tmp = duckdb.connect(":memory:")
        try:
            self.register(spec, tmp, "_tmp_describe")
            cols = [
                ColumnDescriptor(
                    name=str(r[0]), physical_type=str(r[1]), nullable=(str(r[2]).upper() != "NO")
                )
                for r in tmp.execute('DESCRIBE SELECT * FROM "_tmp_describe"').fetchall()
            ]
            res = tmp.execute('SELECT count(*) FROM "_tmp_describe"').fetchall()
            count = int(res[0][0]) if res and res[0] else 0
        finally:
            tmp.close()

        clean = redact_credentials(spec.uri or str(db_path))
        return SourceDescriptor(
            adapter=self.adapter_name,
            format=db_type,
            redacted_uri=clean,
            columns=cols,
            row_count_estimate=count,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )

    def read_bytes(self, spec: SourceSpec) -> bytes:
        return self._parse_spec(spec)[0].read_bytes()

    def stream_chunks(self, spec: SourceSpec, chunk_size: int = 65536) -> Iterator[StreamChunk]:
        p, _, _ = self._parse_spec(spec)
        tot = p.stat().st_size
        if tot == 0:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return
        idx, cur = 0, 0
        with open(p, "rb") as f:
            while b := f.read(chunk_size):
                cur += len(b)
                yield StreamChunk(chunk_index=idx, data=b, is_last=(cur == tot), total_bytes=tot)
                idx += 1

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        st = self._parse_spec(spec)[0].stat()
        dt = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
        return SourceMetadata(byte_size=st.st_size, modified_at=dt)

    def fingerprint(self, spec: SourceSpec) -> str:
        h = hashlib.sha256()
        with open(self._parse_spec(spec)[0], "rb") as f:
            while b := f.read(65536):
                h.update(b)
        return h.hexdigest()

    def _get_sqlite_inspector(self) -> Any:
        from opendv_sources.adapters.sqlite_inspector import SqliteCatalogInspector

        return SqliteCatalogInspector(
            adapter_name=self.adapter_name,
            workspace_root=self.workspace_root,
            allowed_roots=self.allowed_roots,
            allow_temp=self.allow_temp or (self.workspace_root is None),
        )

    def list_namespaces(self, spec: SourceSpec) -> list[NamespaceDescriptor]:
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        if db_type == "sqlite":
            return self._get_sqlite_inspector().list_namespaces(spec)
        return [
            NamespaceDescriptor(
                catalog=None,
                schema_name="main",
                description=f"Default schema for DuckDB database {db_path.name}",
            )
        ]

    def list_tables(
        self,
        spec: SourceSpec,
        namespace: NamespaceRef | None = None,
    ) -> list[TableSummary]:
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        if db_type == "sqlite":
            return self._get_sqlite_inspector().list_tables(spec, namespace)

        tmp = duckdb.connect(":memory:")
        try:
            alias = self._get_or_attach_db(tmp, db_path, db_type)
            q_alias = quote_ident(alias)
            tables = [r[0] for r in tmp.execute(f"SHOW TABLES FROM {q_alias}").fetchall()]
            summaries: list[TableSummary] = []
            for t in tables:
                cnt_row = tmp.execute(
                    f"SELECT count(*) FROM {quote_ident(alias)}.{quote_ident(t)}"
                ).fetchone()
                row_cnt = int(cnt_row[0]) if cnt_row else None
                summaries.append(
                    TableSummary(
                        table_name=t,
                        namespace=namespace or NamespaceRef(schema_name="main"),
                        table_type="BASE_TABLE",
                        row_count_estimate=row_cnt,
                    )
                )
            return summaries
        finally:
            tmp.close()

    def inspect_table(self, spec: SourceSpec, table: TableRef) -> TableSchemaDescriptor:
        validate_identifier(table.table_name)
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        if db_type == "sqlite":
            return self._get_sqlite_inspector().inspect_table(spec, table)

        sub_spec = spec.model_copy(update={"options": {**spec.options, "table": table.table_name}})
        desc = self.describe(sub_spec)
        return TableSchemaDescriptor(
            table_name=table.table_name,
            namespace=table.namespace or NamespaceRef(schema_name="main"),
            columns=desc.columns,
            row_count_estimate=desc.row_count_estimate,
            fingerprint=desc.fingerprint,
        )

    def expand_feed(
        self,
        spec: SourceSpec,
        table: TableRef | None = None,
    ) -> list[ResolvedTableSource]:
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        if db_type == "sqlite":
            return self._get_sqlite_inspector().expand_feed(spec, table)

        source_sys = str(spec.options.get("source_system") or db_path.stem)
        tbl_names = [table.table_name] if table else [s.table_name for s in self.list_tables(spec)]
        expanded: list[ResolvedTableSource] = []
        for name in tbl_names:
            t_ref = TableRef(table_name=name)
            schema = self.inspect_table(spec, t_ref)
            sub_spec = spec.model_copy(update={"options": {**spec.options, "table": name}})
            expanded.append(
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
        return expanded

    def profile_pushdown(
        self,
        spec: SourceSpec,
        table: TableRef,
        policy: SamplingPolicy,
    ) -> TabularProfileSummary:
        db_path, _, scheme = self._parse_spec(spec)
        db_type = self._detect_db_type(spec, scheme, db_path)
        if db_type == "sqlite":
            return self._get_sqlite_inspector().profile_pushdown(spec, table, policy)

        sub_spec = spec.model_copy(update={"options": {**spec.options, "table": table.table_name}})
        desc = self.describe(sub_spec)
        return TabularProfileSummary(
            table_name=table.table_name,
            namespace=table.namespace,
            total_row_count=desc.row_count_estimate or 0,
        )

    def capabilities(self, spec: SourceSpec | None = None) -> SourceCapabilities:
        is_sqlite = False
        if spec and spec.uri:
            try:
                db_path, _, scheme = self._parse_spec(spec)
                is_sqlite = self._detect_db_type(spec, scheme, db_path) == "sqlite"
            except (
                SourceLoadError,
                SourceNotFoundError,
                SourceTableEmptyError,
                UnsupportedFormatError,
                OSError,
                ValueError,
            ):
                is_sqlite = False

        return SourceCapabilities(
            execution_mode="DIRECT_DUCKDB_ATTACH",
            supports_multi_table=True,
            supports_catalog_hierarchy=False,
            supports_foreign_keys=is_sqlite,
            supports_governance_tags=False,
            supports_time_travel=False,
            supports_streaming=True,
            supports_pushdown=True,
            supports_drift_detection=True,
            client_uploadable=is_sqlite,
            server_resolved=True,
        )


__all__ = [
    "DuckdbAttachSourceAdapter",
    "PathContainmentError",
    "qualify_bronze_view_name",
    "quote_ident",
    "validate_identifier",
]
