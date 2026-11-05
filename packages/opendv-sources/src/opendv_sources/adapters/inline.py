"""Zero-disk inline data source adapter (Decision D1 / T16)."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
from collections.abc import Iterator

import duckdb
import pyarrow
import pyarrow.csv
import pyarrow.json
import pyarrow.parquet

from opendv_sources.exceptions import (
    SourceEmptyError,
    SourceLoadError,
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


class InlineSourceAdapter(SourceAdapter, TabularSourceAdapter):
    """Source adapter for in-memory payloads passed inline via DTO or API uploads."""

    def __init__(self, *, adapter_name: str = "inline") -> None:
        self._adapter_name = adapter_name

    @property
    def adapter_name(self) -> str:
        return self._adapter_name

    @property
    def client_uploadable(self) -> bool:
        return True

    @property
    def server_resolved(self) -> bool:
        return False

    def _get_raw_bytes(self, spec: SourceSpec) -> bytes:
        raw = spec.inline_content
        if raw is None:
            raw = spec.options.get("file_content") or spec.options.get("data")
        if raw is None:
            msg = "InlineSourceAdapter requires 'inline_content' or options['file_content']"
            raise SourceLoadError(msg)

        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            if spec.options.get("is_base64", False):
                try:
                    return base64.b64decode(raw)
                except Exception as exc:
                    raise SourceLoadError(f"Failed to decode base64 inline content: {exc}") from exc
            encoding = spec.options.get("encoding", "utf-8")
            return raw.encode(encoding)
        raise SourceLoadError(f"Unsupported inline content type: {type(raw).__name__}")

    def read_bytes(self, spec: SourceSpec) -> bytes:
        return self._get_raw_bytes(spec)

    def stream_chunks(self, spec: SourceSpec, chunk_size: int = 65536) -> Iterator[StreamChunk]:
        data = self._get_raw_bytes(spec)
        total_bytes = len(data)
        if total_bytes == 0:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return

        stream = io.BytesIO(data)
        chunk_idx = 0
        bytes_read = 0
        while block := stream.read(chunk_size):
            bytes_read += len(block)
            yield StreamChunk(
                chunk_index=chunk_idx,
                data=block,
                is_last=(bytes_read == total_bytes),
                total_bytes=total_bytes,
            )
            chunk_idx += 1

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        data = self._get_raw_bytes(spec)
        return SourceMetadata(
            byte_size=len(data),
            content_type=spec.options.get("content_type"),
        )

    def fingerprint(self, spec: SourceSpec) -> str:
        data = self._get_raw_bytes(spec)
        return hashlib.sha256(data).hexdigest()

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            supports_streaming=True,
            supports_pushdown=False,
            supports_drift_detection=True,
            client_uploadable=True,
            server_resolved=False,
        )

    def _detect_format(self, spec: SourceSpec, data: bytes) -> str:
        if spec.format:
            fmt = spec.format.lower().strip(".")
        elif spec.client_path:
            raw_suf = spec.client_path.rsplit(".", 1)[-1].lower() if "." in spec.client_path else ""
            fmt = "parquet" if raw_suf == "pq" else raw_suf
        elif spec.uri:
            raw_suf = spec.uri.rsplit(".", 1)[-1].lower() if "." in spec.uri else ""
            fmt = "parquet" if raw_suf == "pq" else raw_suf
        else:
            stripped = data.strip()
            if stripped.startswith(b"PAR1"):
                fmt = "parquet"
            elif stripped.startswith((b"{", b"[")):
                fmt = "json"
            else:
                fmt = "csv"

        if fmt not in _SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"Unsupported inline format '{fmt}'",
                requested=fmt,
                available=sorted(_SUPPORTED_FORMATS),
            )
        return fmt

    def _parse_to_arrow(self, spec: SourceSpec, data: bytes) -> pyarrow.Table:
        if not data or not data.strip():
            raise SourceEmptyError("Inline source data is empty (0 bytes)")

        fmt = self._detect_format(spec, data)
        try:
            if fmt == "csv":
                table = pyarrow.csv.read_csv(io.BytesIO(data))
            elif fmt == "tsv":
                parse_opts = pyarrow.csv.ParseOptions(delimiter="\t")
                table = pyarrow.csv.read_csv(io.BytesIO(data), parse_options=parse_opts)
            elif fmt == "parquet":
                table = pyarrow.parquet.read_table(pyarrow.BufferReader(data))
            else:
                table = self._parse_json_to_arrow(data)
        except (SourceEmptyError, UnsupportedFormatError):
            raise
        except Exception as exc:
            raise SourceLoadError(f"Failed to parse inline {fmt} payload: {exc}") from exc

        if table.num_rows == 0:
            raise SourceEmptyError(f"Inline source table '{fmt}' contains zero rows")
        return table

    def _parse_json_to_arrow(self, data: bytes) -> pyarrow.Table:
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, ValueError):
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, list):
                if not parsed:
                    raise SourceEmptyError("JSON array contains zero records")
                return pyarrow.Table.from_pylist(parsed)
            if isinstance(parsed, dict):
                for candidate_key in ["records", "data", "rows", "items"]:
                    val = parsed.get(candidate_key)
                    if isinstance(val, list):
                        if not val:
                            msg = f"JSON key '{candidate_key}' contains zero records"
                            raise SourceEmptyError(msg)
                        return pyarrow.Table.from_pylist(val)
                return pyarrow.Table.from_pylist([parsed])

        # Fallback to streaming NDJSON / JSONL
        return pyarrow.json.read_json(io.BytesIO(data))

    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        data = self._get_raw_bytes(spec)
        arrow_table = self._parse_to_arrow(spec, data)
        try:
            conn.register(relation_name, arrow_table)
        except Exception as exc:
            msg = f"Failed to register Arrow table as '{relation_name}': {exc}"
            raise SourceLoadError(msg) from exc

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        data = self._get_raw_bytes(spec)
        fmt = self._detect_format(spec, data)
        arrow_table = self._parse_to_arrow(spec, data)

        columns = [
            ColumnDescriptor(
                name=field.name,
                physical_type=str(field.type),
                nullable=field.nullable,
            )
            for field in arrow_table.schema
        ]
        redacted = spec.client_path or spec.uri or "inline://payload"
        return SourceDescriptor(
            adapter=self.adapter_name,
            format=fmt,
            redacted_uri=redacted,
            columns=columns,
            row_count_estimate=arrow_table.num_rows,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )


__all__ = ["InlineSourceAdapter"]
