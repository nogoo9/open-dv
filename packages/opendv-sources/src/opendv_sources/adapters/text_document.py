"""Text and Markdown document source adapter (T16 / DUP-04)."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from opendv_core.paths import resolve_workspace_path
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

_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_CREDENTIAL_PATTERN = re.compile(r"([^/:@\s]*):([^/:@\s]+)@")


class TextDocumentSourceAdapter(SourceAdapter, DocumentSourceAdapter):
    """Source adapter for plain text and Markdown document extraction."""

    schemes: tuple[str, ...] = ("text", "txt", "md", "markdown")

    def __init__(
        self,
        *,
        adapter_name: str = "text",
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

    def _resolve_raw_bytes(self, spec: SourceSpec) -> tuple[bytes, str, Path | None, str]:
        """Resolve raw document bytes, document ID, file path (if any), and media type."""
        raw_inline = spec.inline_content
        if raw_inline is None:
            raw_inline = spec.options.get("file_content")
        if raw_inline is None:
            raw_inline = spec.options.get("text")
        format_hint = (spec.format or "").lower().strip()
        is_markdown = format_hint in ("md", "markdown")

        if raw_inline is not None:
            doc_id = str(spec.options.get("doc_id") or "inline-text-doc")
            if isinstance(raw_inline, bytes):
                raw_bytes = raw_inline
            elif isinstance(raw_inline, str):
                if spec.options.get("is_base64", False):
                    try:
                        raw_bytes = base64.b64decode(raw_inline)
                    except Exception as exc:
                        raise SourceLoadError(
                            f"Failed to decode base64 inline text: {exc}"
                        ) from exc
                else:
                    raw_bytes = raw_inline.encode(spec.options.get("encoding", "utf-8"))
            else:
                msg = f"Unsupported inline content type for text: {type(raw_inline).__name__}"
                raise SourceLoadError(msg)
            if not raw_bytes:
                raise DocumentExtractionError("Text document source is empty (0 bytes)")
            return raw_bytes, doc_id, None, ("text/markdown" if is_markdown else "text/plain")

        if not spec.uri or not spec.uri.strip():
            raise SourceNotFoundError(
                "TextDocumentSourceAdapter requires 'uri' or 'inline_content'"
            )

        if self.workspace_root is not None:
            ws_root = self.workspace_root
        elif "workspace_root" in spec.options:
            ws_root = Path(spec.options["workspace_root"])
        else:
            ws_root = Path.cwd()
        resolved = resolve_workspace_path(
            spec.uri.strip(),
            workspace_root=ws_root,
            base_dir=Path(spec.options["base_dir"]) if "base_dir" in spec.options else None,
            allowed_roots=(
                self.allowed_roots
                if self.allowed_roots is not None
                else spec.options.get("allowed_roots")
            ),
            allow_temp=spec.options.get("allow_temp", self.allow_temp),
        )
        if not resolved.exists():
            clean_uri = _CREDENTIAL_PATTERN.sub(r"\1:***@", spec.uri.strip())
            clean_res = _CREDENTIAL_PATTERN.sub(r"\1:***@", str(resolved))
            raise SourceNotFoundError(
                f"Text document not found: '{clean_uri}' (resolved: '{clean_res}')"
            )
        if resolved.is_dir():
            raise SourceLoadError(f"Text document target cannot be a directory: '{resolved}'")

        doc_id = str(spec.options.get("doc_id") or resolved.stem)
        media_type = (
            "text/markdown"
            if (is_markdown or resolved.suffix.lower() in _MARKDOWN_EXTENSIONS)
            else "text/plain"
        )
        return resolved.read_bytes(), doc_id, resolved, media_type

    def list_documents(self, spec: SourceSpec) -> list[DocumentRef]:
        """Enumerate document references matching the spec."""
        raw_inline = spec.inline_content
        if raw_inline is None:
            raw_inline = spec.options.get("file_content")
        if raw_inline is None:
            raw_inline = spec.options.get("text")
        if raw_inline is not None or not spec.uri or spec.uri == "inline":
            media_type = (
                "text/markdown"
                if (spec.format or "").lower().strip() in ("md", "markdown")
                else "text/plain"
            )
            return [
                DocumentRef(
                    doc_id=str(spec.options.get("doc_id") or "inline-text-doc"),
                    uri=spec.uri or "inline",
                    media_type=media_type,
                )
            ]

        _, doc_id, path, media_type = self._resolve_raw_bytes(spec)
        return [
            DocumentRef(
                doc_id=doc_id,
                uri=str(path) if path else (spec.uri or "inline"),
                media_type=media_type,
            )
        ]

    def extract(self, spec: SourceSpec, ref: DocumentRef) -> ExtractedDocument:
        """Extract normalized text with uniform LF newlines and single-page page map."""
        raw_bytes, default_doc_id, _, default_media_type = self._resolve_raw_bytes(spec)
        doc_id = ref.doc_id if ref and ref.doc_id else default_doc_id
        media_type = ref.media_type if ref and ref.media_type else default_media_type

        encoding = spec.options.get("encoding", "utf-8")
        try:
            decoded = raw_bytes.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise DocumentExtractionError(
                f"Failed to decode text document with encoding '{encoding}': {exc}"
            ) from exc

        normalized_text = decoded.replace("\r\n", "\n").replace("\r", "\n")
        char_count = len(normalized_text)
        page_map = [PageMapEntry(page_number=1, start_char=0, end_char=char_count)]

        return ExtractedDocument(
            doc_id=doc_id,
            normalized_text=normalized_text,
            page_map=page_map,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            media_type=media_type,
            char_count=char_count,
            byte_size=len(raw_bytes),
        )

    def extract_document(self, spec: SourceSpec) -> ExtractedDocument:
        """Convenience method to extract document directly from SourceSpec."""
        docs = self.list_documents(spec)
        if not docs:
            raise DocumentExtractionError("No documents found matching source spec")
        return self.extract(spec, docs[0])

    def read_bytes(self, spec: SourceSpec) -> bytes:
        raw_bytes, _, _, _ = self._resolve_raw_bytes(spec)
        return raw_bytes

    def stream_chunks(self, spec: SourceSpec, chunk_size: int = 65536) -> Iterator[StreamChunk]:
        data = self.read_bytes(spec)
        total_bytes = len(data)
        if total_bytes == 0:
            yield StreamChunk(chunk_index=0, data=b"", is_last=True, total_bytes=0)
            return

        chunk_idx, offset = 0, 0
        while offset < total_bytes:
            end = min(offset + chunk_size, total_bytes)
            yield StreamChunk(
                chunk_index=chunk_idx,
                data=data[offset:end],
                is_last=(end >= total_bytes),
                total_bytes=total_bytes,
            )
            chunk_idx += 1
            offset = end

    def get_metadata(self, spec: SourceSpec) -> SourceMetadata:
        raw_bytes, _, path, _ = self._resolve_raw_bytes(spec)
        mtime = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
            if path is not None
            else datetime.now(tz=UTC).isoformat()
        )
        return SourceMetadata(byte_size=len(raw_bytes), modified_at=mtime)

    def fingerprint(self, spec: SourceSpec) -> str:
        return hashlib.sha256(self.read_bytes(spec)).hexdigest()

    def describe(self, spec: SourceSpec) -> SourceDescriptor:
        _, _, path, _ = self._resolve_raw_bytes(spec)
        redacted = path.name if path else (spec.client_path or "inline:text")
        return SourceDescriptor(
            adapter=self.adapter_name,
            format=spec.format or "text",
            redacted_uri=redacted,
            columns=[],
            row_count_estimate=1,
            fingerprint=self.fingerprint(spec),
            capabilities=self.capabilities(spec),
        )

    def capabilities(self, spec: SourceSpec) -> SourceCapabilities:
        return SourceCapabilities(
            client_uploadable=True, server_resolved=True, supports_streaming=True
        )


__all__ = ["TextDocumentSourceAdapter"]
