"""Parquet format parser implementing FormatParser SPI."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import pyarrow
import pyarrow.parquet

from opendv_sources.exceptions import SourceLoadError
from opendv_sources.models.streams import StreamChunk
from opendv_sources.spi.parser import FormatParser


class ParquetFormatParser(FormatParser):
    """Format parser for Apache Parquet into PyArrow tables."""

    @property
    def format_name(self) -> str:
        return "parquet"

    @property
    def supported_extensions(self) -> Sequence[str]:
        return (".parquet", ".pq")

    @property
    def supported_mime_types(self) -> Sequence[str]:
        return ("application/vnd.apache.parquet", "application/x-parquet")

    def parse_to_arrow(
        self,
        data: bytes,
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> pyarrow.Table:
        try:
            table = pyarrow.parquet.read_table(pyarrow.BufferReader(data))
            if schema_hint is not None:
                table = table.cast(schema_hint)
            return table
        except Exception as exc:
            raise SourceLoadError(f"Failed to parse Parquet payload: {exc}") from exc

    def stream_arrow_batches(
        self,
        chunks: Iterator[StreamChunk],
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> Iterator[pyarrow.RecordBatch]:
        all_bytes = b"".join(chunk.data for chunk in chunks)
        table = self.parse_to_arrow(all_bytes, schema_hint=schema_hint, **options)
        yield from table.to_batches()


__all__ = ["ParquetFormatParser"]
