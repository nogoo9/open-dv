"""Unit tests verifying the conformance test kit itself operates correctly."""

from __future__ import annotations

import hashlib

import duckdb
import pytest

from opendv_sources.conformance.document_suite import DocumentAdapterConformanceSuite
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import (
    DocumentExtractionError,
    SourceEmptyError,
    SourceLoadError,
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
from opendv_sources.models.specs import SourceSpec
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter


class _CompliantTabularAdapter(TabularSourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "compliant_tabular"

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        if spec.options.get("is_empty"):
            raise SourceEmptyError("Empty table")
        if spec.options.get("is_corrupt"):
            raise SourceLoadError("Corrupt data")

        return SourceDescriptor(
            adapter=self.adapter_name,
            format="mock",
            redacted_uri="mock://redacted_clean_path",
            columns=[
                ColumnDescriptor(name="id", physical_type="INTEGER"),
                ColumnDescriptor(name="val", physical_type="VARCHAR"),
            ],
            row_count_estimate=2,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )

    def register(
        self,
        spec: SourceSpec,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
    ) -> None:
        if spec.options.get("is_empty"):
            raise SourceEmptyError("Table is empty")
        if spec.options.get("is_corrupt"):
            raise SourceLoadError("Corrupted file header")

        conn.execute(f'CREATE OR REPLACE TABLE "{relation_name}" (id INTEGER, val VARCHAR)')
        conn.execute(f"INSERT INTO \"{relation_name}\" VALUES (1, 'a'), (2, 'b')")

    def fingerprint(self, spec: SourceSpec) -> str:
        content = spec.options.get("version", "v1")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(supports_streaming=True)


class TestTabularConformanceSuite(TabularAdapterConformanceSuite):
    """Test executing the TabularAdapterConformanceSuite against a compliant adapter."""

    @pytest.fixture
    def adapter(self) -> TabularSourceAdapter:
        return _CompliantTabularAdapter()

    @pytest.fixture
    def sample_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_tabular", options={"version": "v1"})

    @pytest.fixture
    def mutated_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_tabular", options={"version": "v2_mutated"})

    @pytest.fixture
    def empty_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_tabular", options={"is_empty": True})

    @pytest.fixture
    def corrupt_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_tabular", options={"is_corrupt": True})

    @pytest.fixture
    def secure_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="compliant_tabular",
            credentials_ref="secret:database_secret",
            options={"version": "secure_v1"},
        )

    @pytest.fixture
    def conn(self) -> duckdb.DuckDBPyConnection:
        c = duckdb.connect(":memory:")
        yield c
        c.close()


class _CompliantDocumentAdapter(DocumentSourceAdapter):
    @property
    def adapter_name(self) -> str:
        return "compliant_document"

    def list_documents(self, spec: SourceSpec) -> list[DocumentRef]:
        if spec.options.get("is_corrupt"):
            raise DocumentExtractionError("Corrupt document archive")
        return [DocumentRef(doc_id="doc-1", uri="sample.txt", media_type="text/plain")]

    def extract(self, spec: SourceSpec, ref: DocumentRef) -> ExtractedDocument:
        if spec.options.get("is_corrupt"):
            raise DocumentExtractionError("Unparseable corrupt document")

        # CRLF vs LF test input
        raw_text = spec.options.get("text", "Page 1 Content\nPage 2 Content\n")
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        raw_bytes = raw_text.encode("utf-8")

        page_map = []
        if spec.options.get("multi_page"):
            p1_len = len("Page 1 Content\n")
            page_map = [
                PageMapEntry(page_number=1, start_char=0, end_char=p1_len),
                PageMapEntry(page_number=2, start_char=p1_len, end_char=len(normalized)),
            ]

        return ExtractedDocument(
            doc_id=ref.doc_id,
            normalized_text=normalized,
            page_map=page_map,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            media_type="text/plain",
            char_count=len(normalized),
        )


class TestDocumentConformanceSuite(DocumentAdapterConformanceSuite):
    """Test executing the DocumentAdapterConformanceSuite against a compliant adapter."""

    @pytest.fixture
    def adapter(self) -> DocumentSourceAdapter:
        return _CompliantDocumentAdapter()

    @pytest.fixture
    def sample_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_document", options={"text": "Uniform text\n"})

    @pytest.fixture
    def raw_bytes(self) -> bytes:
        return b"Uniform text\n"

    @pytest.fixture
    def lf_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_document", options={"text": "Line 1\nLine 2\n"})

    @pytest.fixture
    def crlf_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_document", options={"text": "Line 1\r\nLine 2\r\n"})

    @pytest.fixture
    def multi_page_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="compliant_document",
            options={"multi_page": True, "text": "Page 1 Content\nPage 2 Content\n"},
        )

    @pytest.fixture
    def corrupt_spec(self) -> SourceSpec:
        return SourceSpec(adapter="compliant_document", options={"is_corrupt": True})
