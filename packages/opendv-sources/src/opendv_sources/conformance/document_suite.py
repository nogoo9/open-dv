"""Reusable conformance test suite for DocumentSourceAdapter plugins (T16)."""

from __future__ import annotations

import hashlib
from typing import Any

from opendv_sources.exceptions import DocumentExtractionError, SourceError
from opendv_sources.models.documents import DocumentRef
from opendv_sources.models.specs import SourceSpec
from opendv_sources.spi.document import DocumentSourceAdapter


class DocumentAdapterConformanceSuite:
    """Base conformance test suite that all DocumentSourceAdapter implementations must satisfy.

    Plugin implementations inherit from this class in their test suites and provide
    the required fixtures (adapter, sample_spec, lf_spec, crlf_spec, multi_page_spec, corrupt_spec).
    """

    @staticmethod
    def _assert_raises(
        expected_type: type[BaseException] | tuple[type[BaseException], ...],
        fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> BaseException:
        """Assert that executing `fn(*args, **kwargs)` raises an exception of `expected_type`."""
        try:
            fn(*args, **kwargs)
        except expected_type as exc:
            return exc
        except BaseException as exc:
            type_name = (
                "/".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            raise AssertionError(
                f"Expected exception {type_name}, but {type(exc).__name__} was raised: {exc}"
            ) from exc
        type_name = (
            "/".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise AssertionError(f"Expected exception {type_name}, but no exception was raised.")

    def test_text_normalization_crlf_equality(
        self,
        adapter: DocumentSourceAdapter,
        lf_spec: SourceSpec,
        crlf_spec: SourceSpec,
    ) -> None:
        """Verify extracted normalized_text is byte-identical across LF and CRLF sources."""
        docs_lf = adapter.list_documents(lf_spec)
        docs_crlf = adapter.list_documents(crlf_spec)
        assert docs_lf, "LF spec produced zero documents"
        assert docs_crlf, "CRLF spec produced zero documents"

        extracted_lf = adapter.extract(lf_spec, docs_lf[0])
        extracted_crlf = adapter.extract(crlf_spec, docs_crlf[0])

        assert "\r" not in extracted_lf.normalized_text, "CR chars in LF text"
        assert "\r" not in extracted_crlf.normalized_text, "CR chars in CRLF text"
        assert extracted_lf.normalized_text == extracted_crlf.normalized_text, (
            "Normalized text differs between LF and CRLF inputs"
        )
        assert extracted_lf.char_count == len(extracted_lf.normalized_text)

    def test_page_map_invariants(
        self,
        adapter: DocumentSourceAdapter,
        multi_page_spec: SourceSpec,
    ) -> None:
        """Verify page maps are strictly monotonic, valid, and cover character offsets."""
        docs = adapter.list_documents(multi_page_spec)
        assert docs, "Multi-page spec produced zero documents"
        doc = adapter.extract(multi_page_spec, docs[0])

        if not doc.page_map:
            return

        last_end = 0
        last_page = 0
        for entry in doc.page_map:
            assert entry.page_number > last_page, (
                f"Page numbers must increase: {entry.page_number} <= {last_page}"
            )
            assert 0 <= entry.start_char <= entry.end_char <= doc.char_count, (
                f"Invalid span [{entry.start_char}, {entry.end_char}] for len {doc.char_count}"
            )
            assert entry.start_char >= last_end, (
                f"Overlapping page spans: start {entry.start_char} < previous end {last_end}"
            )
            last_page = entry.page_number
            last_end = entry.end_char

    def test_corrupt_document_fails_loudly(
        self,
        adapter: DocumentSourceAdapter,
        corrupt_spec: SourceSpec,
    ) -> None:
        """Verify corrupt documents raise DocumentExtractionError, not replacement chars."""
        docs = [DocumentRef(doc_id="corrupt-doc", uri="corrupt", media_type="text/plain")]
        try:
            listed = adapter.list_documents(corrupt_spec)
            if listed:
                docs = listed
        except (DocumentExtractionError, SourceError):
            # If list_documents itself raises, that is also an acceptable fail-fast behavior
            return

        self._assert_raises(
            (DocumentExtractionError, SourceError),
            adapter.extract,
            corrupt_spec,
            docs[0],
        )

    def test_raw_sha256_integrity(
        self,
        adapter: DocumentSourceAdapter,
        sample_spec: SourceSpec,
        raw_bytes: bytes,
    ) -> None:
        """Verify raw_sha256 in ExtractedDocument matches actual binary bytes SHA-256."""
        docs = adapter.list_documents(sample_spec)
        assert docs, "Sample spec produced zero documents"
        extracted = adapter.extract(sample_spec, docs[0])
        expected_sha = hashlib.sha256(raw_bytes).hexdigest()
        assert extracted.raw_sha256 == expected_sha, (
            f"raw_sha256 mismatch: expected {expected_sha}, got {extracted.raw_sha256}"
        )

    def test_extract_document_helper(
        self,
        adapter: DocumentSourceAdapter,
        sample_spec: SourceSpec,
    ) -> None:
        """Verify extract_document convenience helper extracts document."""
        if hasattr(adapter, "extract_document"):
            doc = adapter.extract_document(sample_spec)
            assert doc is not None
            assert len(doc.normalized_text) == doc.char_count


__all__ = ["DocumentAdapterConformanceSuite"]
