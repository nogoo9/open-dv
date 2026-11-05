"""Data transfer models and specifications for Open-DV sources."""

from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    SourceCapabilities,
    SourceDescriptor,
)
from opendv_sources.models.documents import (
    DocumentRef,
    ExtractedDocument,
    PageMapEntry,
)
from opendv_sources.models.specs import (
    FormatSpec,
    SourceSpec,
)
from opendv_sources.models.streams import (
    BatchRead,
    SourceMetadata,
    StreamChunk,
)

__all__ = [
    "BatchRead",
    "ColumnDescriptor",
    "DocumentRef",
    "ExtractedDocument",
    "FormatSpec",
    "PageMapEntry",
    "SourceCapabilities",
    "SourceDescriptor",
    "SourceMetadata",
    "SourceSpec",
    "StreamChunk",
]
