"""Local filesystem and directory traversal source adapter (DUP-04 / T16)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from opendv_core.paths import PathContainmentError
from opendv_sources._resolution import redact_credentials, resolve_adapter_path
from opendv_sources.exceptions import (
    SourceEmptyError,
    SourceLoadError,
    SourceNotFoundError,
    UnsupportedFormatError,
)
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    SourceCapabilities,
    SourceDescriptor,
)
from opendv_sources.models.specs import SourceSpec
from opendv_sources.models.streams import SourceMetadata, StreamChunk
from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

_SUPPORTED_FORMATS = {"csv", "tsv", "parquet", "json", "jsonl", "ndjson"}
_GLOB_CHARS = {"*", "?", "["}


class LocalFilesystemSourceAdapter(SourceAdapter, TabularSourceAdapter):
    """Source adapter for local files, directory walks, and glob patterns."""

    def __init__(
        self,
        *,
        adapter_name: str = "file",
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

    @property
    def client_uploadable(self) -> bool:
        return False

    @property
    def server_resolved(self) -> bool:
        return True

    def _resolve_target_paths(self, spec: SourceSpec) -> list[Path]:
        raw_uri = spec.uri
        if not raw_uri or not raw_uri.strip():
            raise SourceNotFoundError("LocalFilesystemSourceAdapter requires a non-empty 'uri'")

        raw_str = raw_uri.strip()
        if any(ch in raw_str for ch in _GLOB_CHARS):
            return self._resolve_glob_pattern(raw_str)

        resolved = resolve_adapter_path(raw_str, self)
        if not resolved.exists():
            clean_raw = redact_credentials(raw_str)
            clean_res = redact_credentials(str(resolved))
            msg = f"Source path not found: '{clean_raw}' (resolved: '{clean_res}')"
            raise SourceNotFoundError(msg)

        if resolved.is_dir():
            files = sorted(
                p for p in resolved.rglob("*") if p.is_file() and not p.name.startswith(".")
            )
            if not files:
                clean_dir = redact_credentials(str(resolved))
                raise SourceEmptyError(f"Source directory contains zero files: '{clean_dir}'")
            return files
        return [resolved]

    def _resolve_glob_pattern(self, pattern: str) -> list[Path]:
        prefix_parts: list[str] = []
        pattern_parts: list[str] = []
        found_wildcard = False
        for part in Path(pattern).parts:
            if not found_wildcard and any(ch in part for ch in _GLOB_CHARS):
                found_wildcard = True
            (pattern_parts if found_wildcard else prefix_parts).append(part)

        prefix_str = str(Path(*prefix_parts)) if prefix_parts else "."
        base_path = resolve_adapter_path(prefix_str, self)
        if not base_path.is_dir():
            clean_base = redact_credentials(str(base_path))
            raise SourceNotFoundError(f"Glob base dir missing: '{clean_base}'")

        sub_pattern = "/".join(pattern_parts)
        matched = list(base_path.glob(sub_pattern))

        ws_root = self.workspace_root if self.workspace_root is not None else Path.cwd()
        permitted = {ws_root.resolve(), base_path.resolve()}
        if self.allowed_roots:
            permitted.update(r.resolve() for r in self.allowed_roots)

        files: list[Path] = []
        for p in matched:
            resolved_p = p.resolve()
            if not any(resolved_p.is_relative_to(root) for root in permitted):
                raise PathContainmentError(f"Matched file escapes permitted roots: '{resolved_p}'")
            if resolved_p.is_file() and not resolved_p.name.startswith("."):
                files.append(resolved_p)

        files = sorted(set(files))
        if not files:
            clean_pat = redact_credentials(pattern)
            raise SourceNotFoundError(f"No files matched glob pattern: '{clean_pat}'")
        return files

    def read_bytes(self, spec: SourceSpec) -> bytes:
        paths = self._resolve_target_paths(spec)
        return paths[0].read_bytes() if len(paths) == 1 else b"".join(p.read_bytes() for p in paths)

    def stream_chunks(
        self,
        spec: SourceSpec,
        chunk_size: int = 65536,
    ) -> Iterator[StreamChunk]:
        paths = self._resolve_target_paths(spec)
        total_bytes = sum(p.stat().st_size for p in paths)
        if total_bytes == 0:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return

        chunk_idx, bytes_read = 0, 0
        for p in paths:
            with open(p, "rb") as f:
                while block := f.read(chunk_size):
                    bytes_read += len(block)
                    yield StreamChunk(
                        chunk_index=chunk_idx,
                        data=block,
                        is_last=(bytes_read == total_bytes),
                        total_bytes=total_bytes,
                    )
                    chunk_idx += 1

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        paths = self._resolve_target_paths(spec)
        total_size = sum(p.stat().st_size for p in paths)
        dt = datetime.fromtimestamp(max(p.stat().st_mtime for p in paths), tz=UTC)
        return SourceMetadata(byte_size=total_size, modified_at=dt.isoformat())

    def fingerprint(self, spec: SourceSpec) -> str:
        paths = self._resolve_target_paths(spec)
        hasher = hashlib.sha256()
        for p in paths:
            with open(p, "rb") as f:
                while block := f.read(65536):
                    hasher.update(block)
        return hasher.hexdigest()

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            supports_streaming=True,
            supports_pushdown=True,
            supports_drift_detection=True,
            client_uploadable=False,
            server_resolved=True,
        )

    def _detect_format(self, spec: SourceSpec, first_path: Path) -> str:
        if spec.format:
            fmt = spec.format.lower().strip(".")
        else:
            suffix = first_path.suffix.lower().lstrip(".")
            fmt = "parquet" if suffix == "pq" else suffix

        if fmt not in _SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"Unsupported format '{fmt}' for '{first_path.name}'",
                requested=fmt,
                available=sorted(_SUPPORTED_FORMATS),
            )
        return fmt

    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        paths = self._resolve_target_paths(spec)
        if all(p.stat().st_size == 0 for p in paths):
            raise SourceEmptyError(f"Source file '{spec.uri}' is empty (0 bytes).")

        fmt = self._detect_format(spec, paths[0])
        escaped_paths = ", ".join(
            f"'{str(p).replace(chr(92), '/').replace("'", "''")}'" for p in paths
        )
        if fmt == "parquet":
            reader = "read_parquet"
        elif fmt in {"csv", "tsv"}:
            reader = "read_csv_auto"
        else:
            reader = "read_json_auto"

        r_esc = relation_name.replace('"', '""')
        sql = (
            f'CREATE OR REPLACE VIEW "{r_esc}" AS '
            f"SELECT * FROM {reader}([{escaped_paths}], union_by_name=true)"
        )
        try:
            conn.execute(sql)
            res = conn.execute(f'SELECT count(*) FROM "{r_esc}"').fetchall()
            count = int(res[0][0]) if res and res[0] else 0
        except duckdb.Error as exc:
            clean_uri = redact_credentials(str(spec.uri or ""))
            msg = f"Failed to register relation '{relation_name}' from '{clean_uri}': {exc}"
            raise SourceLoadError(msg) from exc

        if count == 0:
            clean_uri = redact_credentials(str(spec.uri or ""))
            raise SourceEmptyError(f"Source '{clean_uri}' contains zero data rows.")

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        paths = self._resolve_target_paths(spec)
        fmt = self._detect_format(spec, paths[0])
        tmp_conn = duckdb.connect(":memory:")
        try:
            self.register(spec, tmp_conn, "_tmp_describe")
            cols_data: list[Any] = tmp_conn.execute(
                'DESCRIBE SELECT * FROM "_tmp_describe"'
            ).fetchall()
            columns = [
                ColumnDescriptor(
                    name=str(r[0]),
                    physical_type=str(r[1]),
                    nullable=(str(r[2]).upper() != "NO"),
                )
                for r in cols_data
            ]
            res = tmp_conn.execute('SELECT count(*) FROM "_tmp_describe"').fetchall()
            count = int(res[0][0]) if res and res[0] else 0
        finally:
            tmp_conn.close()

        raw_target = spec.uri or str(paths[0])
        clean_uri = redact_credentials(raw_target)
        return SourceDescriptor(
            adapter=self.adapter_name,
            format=fmt,
            redacted_uri=clean_uri,
            columns=columns,
            row_count_estimate=count,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )


__all__ = ["LocalFilesystemSourceAdapter"]
