"""PDF document source adapter using pypdf for real text extraction (T16 / DUP-04)."""

from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from opendv_sources._resolution import redact_credentials, resolve_adapter_path
from opendv_sources.exceptions import (
    DocumentExtractionError,
    SourceLoadError,
    SourceNotFoundError,
)
from opendv_sources.models.descriptors import SourceCapabilities, SourceDescriptor
from opendv_sources.models.documents import DocumentRef, ExtractedDocument, PageMapEntry
from opendv_sources.models.specs import SourceSpec
from opendv_sources.models.streams import SourceMetadata, StreamChunk
from opendv_sources.spi.adapter import SourceAdapter
from opendv_sources.spi.document import DocumentSourceAdapter


class PdfDocumentSourceAdapter(SourceAdapter, DocumentSourceAdapter):
    """Source adapter for PDF document text extraction and page span mapping."""

    schemes: tuple[str, ...] = ("pdf",)

    def __init__(
        self,
        *,
        adapter_name: str = "pdf",
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        allow_temp: bool | None = None,
    ) -> None:
        self._adapter_name = adapter_name
        self.workspace_root = workspace_root
        self.allowed_roots = allowed_roots
        self.allow_temp = (workspace_root is None) if allow_temp is None else allow_temp

    @property
    def adapter_name(self) -> str:
        return self._adapter_name

    @property
    def client_uploadable(self) -> bool:
        return True

    @property
    def server_resolved(self) -> bool:
        return True

    def _resolve_raw_bytes(self, spec: SourceSpec) -> tuple[bytes, str, Path | None]:
        """Resolve raw PDF bytes, document ID, and file path (if any)."""
        raw_inline = spec.inline_content
        if raw_inline is None:
            raw_inline = spec.options.get("file_content")
        if raw_inline is None:
            raw_inline = spec.options.get("data")
        if raw_inline is not None:
            doc_id = str(spec.options.get("doc_id") or "inline-pdf-doc")
            if isinstance(raw_inline, bytes):
                raw_bytes = raw_inline
            elif isinstance(raw_inline, str):
                is_b64 = spec.options.get("is_base64", False) or raw_inline.startswith("JVBERi0")
                try:
                    raw_bytes = (
                        base64.b64decode(raw_inline) if is_b64 else raw_inline.encode("latin1")
                    )
                except Exception as exc:
                    raise DocumentExtractionError(
                        f"Failed to decode base64 inline PDF: {exc}"
                    ) from exc
            else:
                raise DocumentExtractionError(
                    f"Unsupported inline content type for PDF: {type(raw_inline).__name__}"
                )
            if not raw_bytes:
                raise DocumentExtractionError("PDF source is empty (0 bytes)")
            return raw_bytes, doc_id, None

        if not spec.uri or not spec.uri.strip():
            raise SourceNotFoundError("PdfDocumentSourceAdapter requires 'uri' or 'inline_content'")

        resolved = resolve_adapter_path(spec.uri.strip(), self)
        if not resolved.exists():
            clean_uri = redact_credentials(spec.uri.strip())
            clean_res = redact_credentials(str(resolved))
            raise SourceNotFoundError(
                f"PDF document not found: '{clean_uri}' (resolved: '{clean_res}')"
            )
        if resolved.is_dir():
            raise SourceLoadError(f"PDF document target cannot be a directory: '{resolved}'")

        doc_id = str(spec.options.get("doc_id") or resolved.stem)
        raw_bytes = resolved.read_bytes()
        if not raw_bytes:
            raise DocumentExtractionError(f"PDF document is empty (0 bytes): '{resolved}'")
        return raw_bytes, doc_id, resolved

    def list_documents(self, spec: SourceSpec) -> list[DocumentRef]:
        """Enumerate document references matching the spec."""
        raw_inline = spec.inline_content
        if raw_inline is None:
            raw_inline = spec.options.get("file_content")
        if raw_inline is None:
            raw_inline = spec.options.get("data")
        if raw_inline is not None or not spec.uri or spec.uri == "inline":
            doc_id = str(spec.options.get("doc_id") or "inline-pdf-doc")
            return [
                DocumentRef(doc_id=doc_id, uri=spec.uri or "inline", media_type="application/pdf")
            ]

        _, doc_id, path = self._resolve_raw_bytes(spec)
        return [
            DocumentRef(
                doc_id=doc_id,
                uri=str(path) if path else (spec.uri or "inline"),
                media_type="application/pdf",
            )
        ]

    def extract(self, spec: SourceSpec, ref: DocumentRef) -> ExtractedDocument:
        """Extract normalized text and cumulative page map from PDF using pypdf."""
        try:
            import pypdf
        except ImportError as exc:
            raise DocumentExtractionError(
                "pypdf>=5.0.0 is required for PDF text extraction"
            ) from exc

        raw_bytes, default_doc_id, _ = self._resolve_raw_bytes(spec)
        doc_id = ref.doc_id if ref and ref.doc_id else default_doc_id
        media_type = ref.media_type if ref and ref.media_type else "application/pdf"

        if b"%PDF-" not in raw_bytes[:1024]:
            raise DocumentExtractionError("Invalid PDF document: missing '%PDF-' header marker")

        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            if reader.is_encrypted and not reader.decrypt("") and reader.is_encrypted:
                raise DocumentExtractionError(
                    "Password-protected PDF cannot be read without credentials"
                )
            pages = reader.pages
            if not pages:
                raise DocumentExtractionError("PDF document contains zero pages")
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError(
                f"PDF extraction failed on corrupt document: {exc}"
            ) from exc

        page_texts: list[str] = []
        page_map: list[PageMapEntry] = []
        current_offset = 0

        for idx, page in enumerate(pages):
            try:
                page_raw = page.extract_text() or ""
            except Exception as exc:
                raise DocumentExtractionError(
                    f"Failed to extract text from page {idx + 1} of PDF: {exc}"
                ) from exc

            normalized_page = page_raw.replace("\r\n", "\n").replace("\r", "\n")
            start_char = current_offset
            end_char = start_char + len(normalized_page)
            page_map.append(
                PageMapEntry(page_number=idx + 1, start_char=start_char, end_char=end_char)
            )
            page_texts.append(normalized_page)
            current_offset = end_char

        normalized_text = "".join(page_texts)
        return ExtractedDocument(
            doc_id=doc_id,
            normalized_text=normalized_text,
            page_map=page_map,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            media_type=media_type,
            char_count=len(normalized_text),
            byte_size=len(raw_bytes),
        )

    def extract_document(self, spec: SourceSpec) -> ExtractedDocument:
        """Convenience method to extract document directly from SourceSpec."""
        docs = self.list_documents(spec)
        if not docs:
            raise DocumentExtractionError("No documents found matching source spec")
        return self.extract(spec, docs[0])

    def read_bytes(self, spec: SourceSpec) -> bytes:
        raw_bytes, _, _ = self._resolve_raw_bytes(spec)
        return raw_bytes

    def stream_chunks(self, spec: SourceSpec, chunk_size: int = 65536) -> Iterator[StreamChunk]:
        data = self.read_bytes(spec)
        if not data:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return
        total = len(data)
        for idx, offset in enumerate(range(0, total, chunk_size)):
            end = min(offset + chunk_size, total)
            yield StreamChunk(
                chunk_index=idx, data=data[offset:end], is_last=(end >= total), total_bytes=total
            )

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        raw_bytes, _, path = self._resolve_raw_bytes(spec)
        mtime = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
            if path is not None
            else datetime.now(tz=UTC).isoformat()
        )
        return SourceMetadata(byte_size=len(raw_bytes), modified_at=mtime)

    def fingerprint(self, spec: SourceSpec) -> str:
        return hashlib.sha256(self.read_bytes(spec)).hexdigest()

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        _, _, path = self._resolve_raw_bytes(spec)
        return SourceDescriptor(
            adapter=self.adapter_name,
            format=spec.format or "pdf",
            redacted_uri=path.name if path else (spec.client_path or "inline:pdf"),
            columns=[],
            row_count_estimate=1,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            client_uploadable=True, server_resolved=True, supports_streaming=True
        )


__all__ = ["PdfDocumentSourceAdapter"]
