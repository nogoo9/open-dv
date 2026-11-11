"""Concrete source adapter implementations."""

from __future__ import annotations

from opendv_sources.adapters.duckdb_attach import DuckdbAttachSourceAdapter
from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter
from opendv_sources.adapters.inline import InlineSourceAdapter
from opendv_sources.adapters.libreoffice_adapter import (
    LibreOfficeDocumentAdapter,
    find_soffice_binary,
)
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    LiteParseDocumentAdapter,
)
from opendv_sources.adapters.multiformat_adapter import (
    DelimitedTabularAdapter,
    MarkdownDocumentAdapter,
    MarkupDocumentAdapter,
    PyArrowTabularAdapter,
    StructuredDataDocumentAdapter,
)
from opendv_sources.adapters.pdf_document import PdfDocumentSourceAdapter
from opendv_sources.adapters.text_document import TextDocumentSourceAdapter
from opendv_sources.exceptions import OfficeConversionUnavailableError
from opendv_sources.ingestion.adapter import DocumentAdapter
from opendv_sources.registry.source_registry import SourceAdapterRegistry, get_source_registry


def register_default_source_adapters(registry: SourceAdapterRegistry | None = None) -> None:
    """Register default source adapters into the given or default registry."""
    reg = registry or get_source_registry()
    reg.register_adapter("file", LocalFilesystemSourceAdapter)
    reg.register_adapter("filesystem", LocalFilesystemSourceAdapter)
    reg.register_adapter("inline", InlineSourceAdapter)
    reg.register_adapter("duckdb_attach", DuckdbAttachSourceAdapter)
    reg.register_adapter("sqlite", DuckdbAttachSourceAdapter)
    reg.register_adapter("duckdb", DuckdbAttachSourceAdapter)
    reg.register_adapter("text", TextDocumentSourceAdapter)
    reg.register_adapter("txt", TextDocumentSourceAdapter)
    reg.register_adapter("md", TextDocumentSourceAdapter)
    reg.register_adapter("markdown", TextDocumentSourceAdapter)
    reg.register_adapter("pdf", PdfDocumentSourceAdapter)


def get_default_document_adapters() -> list[DocumentAdapter]:
    """Return an ordered list of standard DocumentAdapter instances for ingestion."""
    return [
        MarkdownDocumentAdapter(),
        DelimitedTabularAdapter(),
        PyArrowTabularAdapter(),
        StructuredDataDocumentAdapter(),
        MarkupDocumentAdapter(),
        LibreOfficeDocumentAdapter(),
        LiteParseDocumentAdapter(),
    ]


__all__ = [
    "DelimitedTabularAdapter",
    "DuckdbAttachSourceAdapter",
    "InlineSourceAdapter",
    "InvalidDocumentError",
    "LibreOfficeDocumentAdapter",
    "LiteParseDocumentAdapter",
    "LocalFilesystemSourceAdapter",
    "MarkdownDocumentAdapter",
    "MarkupDocumentAdapter",
    "OfficeConversionUnavailableError",
    "PdfDocumentSourceAdapter",
    "PyArrowTabularAdapter",
    "StructuredDataDocumentAdapter",
    "TextDocumentSourceAdapter",
    "find_soffice_binary",
    "get_default_document_adapters",
    "register_default_source_adapters",
]
