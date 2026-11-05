"""Low-level format parsing SPI for decoding byte streams into PyArrow tables."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from typing import Any

import pyarrow

from opendv_sources.models.streams import StreamChunk


class FormatParser(ABC):
    """Abstract base class for decoding raw byte streams into PyArrow tables/batches."""

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Format identifier (e.g. 'csv', 'parquet', 'jsonl')."""
        ...

    @property
    @abstractmethod
    def supported_extensions(self) -> Sequence[str]:
        """Associated file extensions (e.g. ('.csv', '.tsv'))."""
        ...

    @property
    @abstractmethod
    def supported_mime_types(self) -> Sequence[str]:
        """Associated MIME types (e.g. ('text/csv',))."""
        ...

    @abstractmethod
    def parse_to_arrow(
        self,
        data: bytes,
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> pyarrow.Table:
        """Parse raw bytes into a PyArrow Table."""
        ...

    @abstractmethod
    def stream_arrow_batches(
        self,
        chunks: Iterator[StreamChunk],
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> Iterator[pyarrow.RecordBatch]:
        """Stream parsed PyArrow RecordBatches from raw stream chunks."""
        ...


__all__ = ["FormatParser"]
