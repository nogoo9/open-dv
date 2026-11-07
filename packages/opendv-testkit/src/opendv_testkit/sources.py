"""In-memory records source adapter for testkit and extensibility verification (T16)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

import duckdb
import pyarrow

from opendv_sources.exceptions import (
    SourceEmptyError,
    SourceLoadError,
    SourceTableEmptyError,
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

_SECRET_KEYS = frozenset({"password", "secret", "token", "credentials", "api_key"})


class RecordsSourceAdapter(SourceAdapter, TabularSourceAdapter):
    """Test-only in-memory source adapter ingesting list-of-dict records."""

    def __init__(self, *, adapter_name: str = "records") -> None:
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

    def _extract_records(self, spec: SourceSpec) -> list[dict[str, Any]]:
        raw_records = spec.options.get("records")
        if raw_records is not None:
            if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
                raise SourceLoadError(
                    f"Expected list/sequence of dict records, got {type(raw_records).__name__}"
                )
            if len(raw_records) == 0:
                raise SourceTableEmptyError("Records source contains zero records")
            for idx, rec in enumerate(raw_records):
                if not isinstance(rec, dict):
                    raise SourceLoadError(
                        f"Record at index {idx} is not a dict: {type(rec).__name__}"
                    )
            return list(raw_records)

        raw_payload = (
            spec.inline_content or spec.options.get("file_content") or spec.options.get("data")
        )
        if raw_payload is not None:
            if isinstance(raw_payload, bytes):
                text_content = raw_payload.decode("utf-8", errors="replace")
            elif isinstance(raw_payload, str):
                text_content = raw_payload
            else:
                raise SourceLoadError(f"Unsupported raw payload type: {type(raw_payload).__name__}")

            if not text_content.strip():
                raise SourceEmptyError("Records source payload is empty (0 bytes)")

            try:
                parsed = json.loads(text_content)
            except Exception as exc:
                raise SourceLoadError(f"Failed to parse records JSON payload: {exc}") from exc

            if isinstance(parsed, list):
                if len(parsed) == 0:
                    raise SourceTableEmptyError("Records array contains zero entries")
                for idx, item in enumerate(parsed):
                    if not isinstance(item, dict):
                        raise SourceLoadError(f"Record at index {idx} is not an object/dict")
                return list(parsed)

            if isinstance(parsed, dict):
                for candidate_key in ["records", "data", "rows", "items"]:
                    if candidate_key in parsed:
                        sub = parsed[candidate_key]
                        if isinstance(sub, list):
                            if len(sub) == 0:
                                raise SourceTableEmptyError(
                                    f"Key '{candidate_key}' contains zero records"
                                )
                            for idx, item in enumerate(sub):
                                if not isinstance(item, dict):
                                    raise SourceLoadError(f"Item {idx} is not an object/dict")
                            return list(sub)
                raise SourceLoadError(
                    "JSON dictionary must contain a list under 'records', 'data', or 'rows'"
                )

            raise SourceLoadError(
                f"Expected JSON list or dict with records, got {type(parsed).__name__}"
            )

        raise SourceLoadError(
            "RecordsSourceAdapter requires options['records'] or 'inline_content'"
        )

    def _to_arrow_table(self, spec: SourceSpec) -> pyarrow.Table:
        records = self._extract_records(spec)
        try:
            table = pyarrow.Table.from_pylist(records)
        except Exception as exc:
            raise SourceLoadError(f"Failed to convert records to PyArrow table: {exc}") from exc

        if table.num_rows == 0:
            raise SourceTableEmptyError("Converted Arrow table contains zero rows")
        return table

    def read_bytes(self, spec: SourceSpec) -> bytes:
        records = self._extract_records(spec)
        return json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def stream_chunks(
        self,
        spec: SourceSpec,
        chunk_size: int = 65536,
    ) -> Iterator[StreamChunk]:
        data = self.read_bytes(spec)
        total = len(data)
        if total == 0:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return

        for idx, offset in enumerate(range(0, total, chunk_size)):
            end = min(offset + chunk_size, total)
            yield StreamChunk(
                chunk_index=idx,
                data=data[offset:end],
                is_last=(end >= total),
                total_bytes=total,
            )

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        data = self.read_bytes(spec)
        return SourceMetadata(
            byte_size=len(data),
            modified_at=datetime.now(tz=UTC).isoformat(),
        )

    def fingerprint(self, spec: SourceSpec) -> str:
        return hashlib.sha256(self.read_bytes(spec)).hexdigest()

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        table = self._to_arrow_table(spec)
        columns = [
            ColumnDescriptor(
                name=field.name,
                physical_type=str(field.type),
                nullable=field.nullable,
            )
            for field in table.schema
        ]
        raw_uri = spec.uri or spec.client_path or "memory://records"
        for secret_token in _SECRET_KEYS:
            if secret_token in raw_uri.lower():
                raw_uri = "memory://records/[REDACTED]"
                break

        return SourceDescriptor(
            adapter=self.adapter_name,
            format=spec.format or "records",
            redacted_uri=raw_uri,
            columns=columns,
            row_count_estimate=table.num_rows,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )

    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        table = self._to_arrow_table(spec)
        conn.register(relation_name, table)

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            client_uploadable=True,
            server_resolved=False,
            supports_streaming=False,
        )


__all__ = ["RecordsSourceAdapter"]
