"""DuckDB attach source adapter for SQLite and DuckDB database tables (T16 / DUP-04)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_sources.exceptions import (
    SourceLoadError,
    SourceNotFoundError,
    SourceTableEmptyError,
    UnsupportedFormatError,
)
from opendv_sources.models.descriptors import ColumnDescriptor, SourceCapabilities, SourceDescriptor
from opendv_sources.models.specs import SourceSpec
from opendv_sources.models.streams import SourceMetadata, StreamChunk
from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

_KNOWN_SCHEMES = ("duckdb_attach", "sqlite", "duckdb")
_CREDENTIAL_PATTERN = re.compile(r"([^/:@\s]*):([^/:@\s]+)@")


class DuckdbAttachSourceAdapter(SourceAdapter, TabularSourceAdapter):
    """Source adapter exposing tables from local SQLite or DuckDB database files via ATTACH."""

    schemes: tuple[str, ...] = ("duckdb_attach", "sqlite", "duckdb")

    def __init__(
        self,
        *,
        adapter_name: str = "duckdb_attach",
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self.workspace_root = workspace_root
        self.allowed_roots = allowed_roots

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

        if self.workspace_root is not None:
            ws_root = self.workspace_root
        elif "workspace_root" in spec.options:
            ws_root = Path(spec.options["workspace_root"])
        else:
            ws_root = Path.cwd()
        resolved = resolve_workspace_path(
            raw_p,
            workspace_root=ws_root,
            base_dir=Path(spec.options["base_dir"]) if "base_dir" in spec.options else None,
            allowed_roots=(
                self.allowed_roots
                if self.allowed_roots is not None
                else spec.options.get("allowed_roots")
            ),
            allow_temp=spec.options.get("allow_temp", False),
        )
        if not resolved.exists():
            clean_raw = _CREDENTIAL_PATTERN.sub(r"\1:***@", raw_p)
            clean_res = _CREDENTIAL_PATTERN.sub(r"\1:***@", str(resolved))
            msg = f"Database file not found: '{clean_raw}' (resolved: '{clean_res}')"
            raise SourceNotFoundError(msg)
        if resolved.is_dir():
            raise SourceLoadError(f"Expected database file, found directory: '{resolved}'")
        if resolved.stat().st_size == 0:
            raise SourceTableEmptyError(f"Database file '{raw_p}' is empty (0 bytes).")

        tbl = spec.options.get("table") or spec.options.get("table_name") or frag_tbl
        return resolved, tbl, scheme

    def _detect_db_type(self, spec: SourceSpec, scheme: str | None, path: Path) -> str:
        fmt = (spec.format or "").lower().strip(".")
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
            conn.execute(f"ATTACH IF NOT EXISTS '{esc}' AS \"{alias}\" {att}")
        except duckdb.Error as exc:
            raise SourceLoadError(f"Failed to attach {db_type} db '{path.name}': {exc}") from exc
        return alias

    def register(
        self, spec: SourceSpec, conn: duckdb.DuckDBPyConnection, relation_name: str
    ) -> None:
        db_path, tbl, scheme = self._parse_spec(spec)
        alias = self._get_or_attach_db(conn, db_path, self._detect_db_type(spec, scheme, db_path))
        try:
            tables = [r[0] for r in conn.execute(f'SHOW TABLES FROM "{alias}"').fetchall()]
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

        t_esc, r_esc = tbl.replace('"', '""'), relation_name.replace('"', '""')
        try:
            conn.execute(f'CREATE OR REPLACE VIEW "{r_esc}" AS SELECT * FROM "{alias}"."{t_esc}"')
            res = conn.execute(f'SELECT count(*) FROM "{r_esc}"').fetchone()
            count = res[0] if res else 0
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
            res = tmp.execute('SELECT count(*) FROM "_tmp_describe"').fetchone()
            count = res[0] if res else 0
        finally:
            tmp.close()

        clean = _CREDENTIAL_PATTERN.sub(r"\1:***@", spec.uri or str(db_path))
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

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            supports_streaming=True,
            supports_pushdown=True,
            supports_drift_detection=True,
            client_uploadable=False,
            server_resolved=True,
        )


__all__ = ["DuckdbAttachSourceAdapter", "PathContainmentError"]
