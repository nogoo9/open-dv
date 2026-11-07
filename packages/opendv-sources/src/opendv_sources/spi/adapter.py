"""Low-level byte-level transport SPI for raw source reading."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from opendv_sources.models.specs import SourceSpec
from opendv_sources.models.streams import SourceMetadata, StreamChunk


class SourceAdapter(ABC):
    """Low-level abstract base class for raw source I/O and byte streaming."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter identifier (e.g. 'file', 'inline', 's3')."""
        ...

    @property
    def client_uploadable(self) -> bool:
        """Whether this adapter can be sent inline from a remote client (opendvctl)."""
        return False

    @property
    def server_resolved(self) -> bool:
        """Whether this adapter is resolved against server-side resources."""
        return True

    @abstractmethod
    def read_bytes(self, spec: SourceSpec) -> bytes:
        """Read the full raw byte payload of the source."""
        ...

    @abstractmethod
    def stream_chunks(
        self,
        spec: SourceSpec,
        chunk_size: int = 65536,
    ) -> Iterator[StreamChunk]:
        """Stream the source byte payload in sequential chunks."""
        ...

    @abstractmethod
    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        """Retrieve size, modification timestamp, and raw headers."""
        ...


__all__ = ["SourceAdapter"]
