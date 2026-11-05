"""Open-DV Pluggable Data Source & Format Adapter SPI, Registries, and Conformance Kit."""

from opendv_sources.config.loader import load_structured_document
from opendv_sources.conformance.document_suite import DocumentAdapterConformanceSuite
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import (
    AuthenticationError,
    DocumentExtractionError,
    FormatError,
    PathContainmentError,
    SourceEmptyError,
    SourceError,
    SourceLoadError,
    SourceNotFoundError,
    UnsupportedFormatError,
)
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
from opendv_sources.registry.format_registry import (
    FORMAT_PARSER_ENTRY_POINT_GROUP,
    FormatParserRegistry,
    get_format_registry,
)
from opendv_sources.registry.source_registry import (
    SOURCE_ADAPTER_ENTRY_POINT_GROUP,
    SourceAdapterRegistry,
    get_source_registry,
)
from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.parser import FormatParser
from opendv_sources.spi.tabular import TabularSourceAdapter

__all__ = [
    "FORMAT_PARSER_ENTRY_POINT_GROUP",
    "SOURCE_ADAPTER_ENTRY_POINT_GROUP",
    "AuthenticationError",
    "BatchRead",
    "ColumnDescriptor",
    "DocumentAdapterConformanceSuite",
    "DocumentExtractionError",
    "DocumentRef",
    "DocumentSourceAdapter",
    "ExtractedDocument",
    "FormatError",
    "FormatParser",
    "FormatParserRegistry",
    "FormatSpec",
    "PageMapEntry",
    "PathContainmentError",
    "SourceAdapter",
    "SourceAdapterRegistry",
    "SourceCapabilities",
    "SourceDescriptor",
    "SourceEmptyError",
    "SourceError",
    "SourceLoadError",
    "SourceMetadata",
    "SourceNotFoundError",
    "SourceSpec",
    "StreamChunk",
    "TabularAdapterConformanceSuite",
    "TabularSourceAdapter",
    "UnsupportedFormatError",
    "get_format_registry",
    "get_source_registry",
    "load_structured_document",
]
