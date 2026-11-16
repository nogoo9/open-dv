"""Open-DV Pluggable Data Source & Format Adapter SPI, Registries, and Conformance Kit."""

from opendv_sources._resolution import redact_credentials, resolve_adapter_path
from opendv_sources.adapters.duckdb_attach import (
    DuckdbAttachSourceAdapter,
    qualify_bronze_view_name,
    quote_ident,
    validate_identifier,
)
from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter
from opendv_sources.adapters.inline import InlineSourceAdapter
from opendv_sources.adapters.pdf_document import PdfDocumentSourceAdapter
from opendv_sources.adapters.text_document import TextDocumentSourceAdapter
from opendv_sources.asset_management import (
    AssetManagementStarterPackRegistry,
    get_all_domains_pack,
    get_core_spine_pack,
)
from opendv_sources.config.graph_a_loader import LoadedGraphA, load_graph_a_source
from opendv_sources.config.loader import load_structured_document
from opendv_sources.conformance.document_suite import DocumentAdapterConformanceSuite
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import (
    ArchiveSecurityViolationError,
    AuthenticationError,
    DocumentExtractionError,
    FormatError,
    InvalidDocumentError,
    OCRExecutionError,
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    PathContainmentError,
    SourceEmptyError,
    SourceError,
    SourceLoadError,
    SourceNotFoundError,
    SourceTableEmptyError,
    UnsupportedFormatError,
)
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    SourceCapabilities,
    SourceDescriptor,
)
from opendv_sources.models.documents import (
    DocumentPage,
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
from opendv_sources.parsers import CsvFormatParser, JsonFormatParser, ParquetFormatParser
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
    "ArchiveSecurityViolationError",
    "AssetManagementStarterPackRegistry",
    "AuthenticationError",
    "BatchRead",
    "ColumnDescriptor",
    "CsvFormatParser",
    "DocumentAdapterConformanceSuite",
    "DocumentExtractionError",
    "DocumentPage",
    "DocumentRef",
    "DocumentSourceAdapter",
    "DuckdbAttachSourceAdapter",
    "ExtractedDocument",
    "FormatError",
    "FormatParser",
    "FormatParserRegistry",
    "FormatSpec",
    "InlineSourceAdapter",
    "InvalidDocumentError",
    "JsonFormatParser",
    "LoadedGraphA",
    "LocalFilesystemSourceAdapter",
    "OCRExecutionError",
    "OCRUnavailableError",
    "OfficeConversionUnavailableError",
    "PageMapEntry",
    "ParquetFormatParser",
    "PathContainmentError",
    "PdfDocumentSourceAdapter",
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
    "SourceTableEmptyError",
    "SourcesConfig",
    "StreamChunk",
    "TabularAdapterConformanceSuite",
    "TabularSourceAdapter",
    "TextDocumentSourceAdapter",
    "UnsupportedFormatError",
    "get_all_domains_pack",
    "get_core_spine_pack",
    "get_format_registry",
    "get_source_registry",
    "load_graph_a_source",
    "load_structured_document",
    "qualify_bronze_view_name",
    "quote_ident",
    "redact_credentials",
    "resolve_adapter_path",
    "validate_identifier",
]
