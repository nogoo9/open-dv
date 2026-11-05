"""Unit tests for SourceAdapterRegistry and FormatParserRegistry."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import duckdb
import pyarrow
import pytest

from opendv_contracts.errors import ErrorCode
from opendv_sources.adapters import (
    DuckdbAttachSourceAdapter,
    InlineSourceAdapter,
    LocalFilesystemSourceAdapter,
    PdfDocumentSourceAdapter,
    TextDocumentSourceAdapter,
)
from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    SourceCapabilities,
    SourceDescriptor,
)
from opendv_sources.models.documents import DocumentRef, ExtractedDocument
from opendv_sources.models.specs import SourceSpec
from opendv_sources.models.streams import StreamChunk
from opendv_sources.registry.format_registry import (
    FormatParserRegistry,
    get_format_registry,
)
from opendv_sources.registry.source_registry import (
    SourceAdapterRegistry,
    get_source_registry,
)
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.parser import FormatParser
from opendv_sources.spi.tabular import TabularSourceAdapter


class _DummyTabularAdapter(TabularSourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "mock_tab"

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        return SourceDescriptor(
            adapter=self.adapter_name,
            format="csv",
            redacted_uri="mock://test",
            columns=[ColumnDescriptor(name="c1", physical_type="VARCHAR")],
            fingerprint="abc",
        )

    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        conn.execute(f'CREATE TABLE "{relation_name}" (c1 VARCHAR)')

    def fingerprint(self, spec: SourceSpec) -> str:
        return "abc"

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities()


class _DummyDocumentAdapter(DocumentSourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "mock_doc"

    def list_documents(self, spec: SourceSpec) -> list[DocumentRef]:
        return [DocumentRef(doc_id="d1", uri="doc1.txt", media_type="text/plain")]

    def extract(self, spec: SourceSpec, ref: DocumentRef) -> ExtractedDocument:
        return ExtractedDocument(
            doc_id="d1",
            normalized_text="Hello world\n",
            raw_sha256="123",
            media_type="text/plain",
            char_count=12,
        )


class _DummyFormatParser(FormatParser):
    @property
    def format_name(self) -> str:
        return "mock_fmt"

    @property
    def supported_extensions(self) -> Sequence[str]:
        return (".mfmt",)

    @property
    def supported_mime_types(self) -> Sequence[str]:
        return ("application/x-mock",)

    def parse_to_arrow(
        self,
        data: bytes,
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> pyarrow.Table:
        return pyarrow.table({"col1": [1, 2, 3]})

    def stream_arrow_batches(
        self,
        chunks: Iterator[StreamChunk],
        schema_hint: pyarrow.Schema | None = None,
        **options: Any,
    ) -> Iterator[pyarrow.RecordBatch]:
        yield pyarrow.record_batch([pyarrow.array([1, 2, 3])], names=["col1"])


def test_empty_source_registry_fails_loudly_decision_d8() -> None:
    """Verify core with no plugins fails loudly with UNSUPPORTED_FORMAT (Decision D8)."""
    registry = SourceAdapterRegistry(entry_point_group="opendv.empty_test_group")
    assert registry.list_adapters() == []
    assert registry.list_tabular_adapters() == []
    assert registry.list_document_adapters() == []

    with pytest.raises(UnsupportedFormatError) as exc_info:
        registry.get_tabular_adapter("file")
    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert "Unsupported source adapter 'file'" in str(exc_info.value)
    assert exc_info.value.details["available"] == []

    with pytest.raises(UnsupportedFormatError) as exc_doc:
        registry.get_document_adapter("pdf")
    assert exc_doc.value.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert "Unsupported document adapter 'pdf'" in str(exc_doc.value)


def test_source_registry_manual_registration_and_retrieval() -> None:
    """Verify manual registration, type detection, and retrieval."""
    registry = SourceAdapterRegistry(entry_point_group="opendv.empty_test_group")
    tab_adapter = _DummyTabularAdapter()
    doc_adapter = _DummyDocumentAdapter()

    registry.register_adapter("mock_tab", tab_adapter)
    registry.register_adapter("mock_doc", doc_adapter)

    assert "mock_tab" in registry.list_tabular_adapters()
    assert "mock_doc" in registry.list_document_adapters()
    assert registry.list_adapters() == ["mock_doc", "mock_tab"]

    retrieved_tab = registry.get_tabular_adapter("MOCK_TAB")
    assert retrieved_tab is tab_adapter

    retrieved_doc = registry.get_document_adapter("MOCK_DOC")
    assert retrieved_doc is doc_adapter

    # Reset
    registry.reset()
    assert registry.list_adapters() == []


def test_empty_format_registry_fails_loudly() -> None:
    """Verify format registry with no parsers fails loudly with UNSUPPORTED_FORMAT."""
    registry = FormatParserRegistry(entry_point_group="opendv.empty_test_fmt_group")
    assert registry.list_formats() == []
    assert registry.list_extensions() == []

    with pytest.raises(UnsupportedFormatError) as exc_info:
        registry.get_parser("csv")
    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert "Unsupported data format 'csv'" in str(exc_info.value)


def test_format_registry_resolution_paths() -> None:
    """Verify format parser resolves by format name, extension, and mime type."""
    registry = FormatParserRegistry(entry_point_group="opendv.empty_test_fmt_group")
    parser = _DummyFormatParser()
    registry.register_parser("mock_fmt", parser)

    assert registry.get_parser("mock_fmt") is parser
    assert registry.get_parser(".mfmt") is parser
    assert registry.get_parser("mfmt") is parser
    assert registry.get_parser("application/x-mock") is parser

    registry.reset()
    assert registry.list_formats() == []


def test_registry_singletons() -> None:
    """Verify default global singletons exist and are persistent."""
    r1 = get_source_registry()
    r2 = get_source_registry()
    assert r1 is r2

    f1 = get_format_registry()
    f2 = get_format_registry()
    assert f1 is f2


def test_entry_points_discovery_default_adapters() -> None:
    """Verify built-in 'file' and 'inline' adapters are discovered from entry points."""
    registry = get_source_registry()
    file_adapter = registry.get_tabular_adapter("file")
    assert isinstance(file_adapter, LocalFilesystemSourceAdapter)

    inline_adapter = registry.get_tabular_adapter("inline")
    assert isinstance(inline_adapter, InlineSourceAdapter)

    attach_adapter = registry.get_tabular_adapter("duckdb_attach")
    assert isinstance(attach_adapter, DuckdbAttachSourceAdapter)

    text_adapter = registry.get_document_adapter("text")
    assert isinstance(text_adapter, TextDocumentSourceAdapter)

    pdf_adapter = registry.get_document_adapter("pdf")
    assert isinstance(pdf_adapter, PdfDocumentSourceAdapter)
