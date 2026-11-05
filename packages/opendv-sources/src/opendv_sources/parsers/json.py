"""JSON and JSONL/NDJSON format parser implementing FormatParser SPI."""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterator, Sequence
from typing import Any

import pyarrow
import pyarrow.json

from opendv_sources.exceptions import SourceEmptyError, SourceLoadError
from opendv_sources.models.streams import StreamChunk
from opendv_sources.spi.parser import FormatParser


class JsonFormatParser(FormatParser):
    """Format parser for JSON and JSONL/NDJSON records into PyArrow tables."""

    @property
    def format_name(self) -> str:
        return "json"

    @property
    def supported_extensions(self) -> Sequence[str]:
        return (".json", ".jsonl", ".ndjson")

    @property
    def supported_mime_types(self) -> Sequence[str]:
        return ("application/json", "application/x-ndjson")

    def parse_to_arrow(
        self,
        data: bytes,
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> pyarrow.Table:
        try:
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, ValueError):
                parsed = json.loads(data.decode("utf-8"))
                if isinstance(parsed, list):
                    if not parsed:
                        raise SourceEmptyError("JSON array contains zero records")
                    return pyarrow.Table.from_pylist(parsed, schema=schema_hint)
                if isinstance(parsed, dict):
                    # Check if wrapped in standard data envelope
                    for candidate_key in ["records", "data", "rows", "items"]:
                        val = parsed.get(candidate_key)
                        if isinstance(val, list) and (len(parsed) == 1 or len(parsed) <= 2):
                            if not val:
                                raise SourceEmptyError(
                                    f"JSON key '{candidate_key}' contains zero records"
                                )
                            return pyarrow.Table.from_pylist(val, schema=schema_hint)
                    return pyarrow.Table.from_pylist([parsed], schema=schema_hint)

            return pyarrow.json.read_json(io.BytesIO(data))
        except (SourceEmptyError, SourceLoadError):
            raise
        except Exception as exc:
            raise SourceLoadError(f"Failed to parse JSON/JSONL payload: {exc}") from exc

    def stream_arrow_batches(
        self,
        chunks: Iterator[StreamChunk],
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> Iterator[pyarrow.RecordBatch]:
        all_bytes = b"".join(chunk.data for chunk in chunks)
        table = self.parse_to_arrow(all_bytes, schema_hint=schema_hint, **options)
        yield from table.to_batches()


__all__ = ["JsonFormatParser"]
