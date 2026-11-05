"""CSV and TSV format parser implementing FormatParser SPI."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from typing import Any

import pyarrow
import pyarrow.csv

from opendv_sources.exceptions import SourceLoadError
from opendv_sources.models.streams import StreamChunk
from opendv_sources.spi.parser import FormatParser

_DEFAULT_NULL_VALUES = ["", "NA", "null", "NULL", "\\N"]


class CsvFormatParser(FormatParser):
    """Format parser for CSV and TSV tabular data into PyArrow tables."""

    @property
    def format_name(self) -> str:
        return "csv"

    @property
    def supported_extensions(self) -> Sequence[str]:
        return (".csv", ".tsv")

    @property
    def supported_mime_types(self) -> Sequence[str]:
        return ("text/csv", "text/tab-separated-values")

    def parse_to_arrow(
        self,
        data: bytes,
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> pyarrow.Table:
        delimiter = options.get("delimiter")
        if not delimiter and options.get("format") == "tsv":
            delimiter = "\t"

        parse_opts = pyarrow.csv.ParseOptions(delimiter=delimiter) if delimiter else None
        conv_opts = pyarrow.csv.ConvertOptions(
            strings_can_be_null=True,
            null_values=_DEFAULT_NULL_VALUES,
            column_types=schema_hint,
        )
        try:
            return pyarrow.csv.read_csv(
                io.BytesIO(data),
                parse_options=parse_opts,
                convert_options=conv_opts,
            )
        except Exception as exc:
            raise SourceLoadError(f"Failed to parse CSV/TSV payload: {exc}") from exc

    def stream_arrow_batches(
        self,
        chunks: Iterator[StreamChunk],
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> Iterator[pyarrow.RecordBatch]:
        all_bytes = b"".join(chunk.data for chunk in chunks)
        table = self.parse_to_arrow(all_bytes, schema_hint=schema_hint, **options)
        yield from table.to_batches()


__all__ = ["CsvFormatParser"]
