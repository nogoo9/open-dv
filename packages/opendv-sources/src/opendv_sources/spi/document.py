"""High-level domain port for unstructured document sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from opendv_sources.models.documents import DocumentRef, ExtractedDocument
from opendv_sources.models.specs import SourceSpec


class DocumentSourceAdapter(ABC):
    """High-level domain port for unstructured document extraction and span grounding."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter identifier (e.g. 'text', 'pdf')."""
        ...

    @abstractmethod
    def list_documents(self, spec: SourceSpec) -> list[DocumentRef]:
        """Enumerate document references matching the spec."""
        ...

    @abstractmethod
    def extract(self, spec: SourceSpec, ref: DocumentRef) -> ExtractedDocument:
        """Extract normalized text (CRLF -> LF), page map, media type, and raw SHA-256."""
        ...


__all__ = ["DocumentSourceAdapter"]
