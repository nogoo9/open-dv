"""DocumentAdapter SPI and Base Implementations (Task 4B.1 / Issue #201).

Defines the DocumentAdapter protocol for pluggable document parsers, converting raw
binary streams and bytes into standardized IngestionResult instances with character
offsets, page mappings, and taint classification.
"""

from __future__ import annotations

import hashlib
import mimetypes
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    PageMapEntry,
    TaintClassification,
)
from opendv_sources.exceptions import InvalidDocumentError, UnsupportedFormatError
from opendv_sources.ingestion.normalization import normalize_text

DEFAULT_MAX_TEXT_BYTES: int = 50 * 1024 * 1024


class DocumentAdapter(ABC):
    """Abstract Service Provider Interface (SPI) for document parsers."""

    @property
    def adapter_name(self) -> str:
        """Human-readable identifier for this adapter."""
        return self.__class__.__name__

    @abstractmethod
    def supports_mime_type(self, mime_type: str) -> bool:
        """Check if this adapter supports the given MIME media type.

        Args:
            mime_type: MIME type string (e.g. 'application/pdf', 'text/plain').

        Returns:
            True if supported, False otherwise.
        """
        ...

    @abstractmethod
    def supports_extension(self, extension: str) -> bool:
        """Check if this adapter supports the given file extension.

        Args:
            extension: File extension with or without leading dot (e.g. '.pdf', 'txt').

        Returns:
            True if supported, False otherwise.
        """
        ...

    @abstractmethod
    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw document bytes into an IngestionResult.

        Args:
            data: Raw document file content.
            filename: Original document filename.
            mime_type: Optional explicit MIME media type.
            **kwargs: Adapter-specific parsing options.

        Returns:
            Structured IngestionResult with text, offsets, and metadata.
        """
        ...

    def parse_stream(
        self,
        stream: BinaryIO,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse a binary stream into an IngestionResult.

        Default implementation reads all bytes and delegates to parse_bytes.

        Args:
            stream: Readable binary stream.
            filename: Original document filename.
            mime_type: Optional explicit MIME media type.
            **kwargs: Adapter-specific parsing options.

        Returns:
            Structured IngestionResult.
        """
        data = stream.read()
        return self.parse_bytes(data, filename=filename, mime_type=mime_type, **kwargs)


class BaseDocumentAdapter(DocumentAdapter):
    """Convenience base class with declarable MIME types and file extensions."""

    supported_mime_types: Sequence[str] = ()
    supported_extensions: Sequence[str] = ()

    def supports_mime_type(self, mime_type: str) -> bool:
        """Match against declared supported_mime_types case-insensitively."""
        if not mime_type:
            return False
        normalized = mime_type.lower().strip()
        # Handle parameters like 'text/plain; charset=utf-8'
        base_mime = normalized.split(";")[0].strip()
        return any(base_mime == decl.lower().strip() for decl in self.supported_mime_types)

    def supports_extension(self, extension: str) -> bool:
        """Match against declared supported_extensions case-insensitively."""
        if not extension:
            return False
        normalized = extension.lower().strip()
        if not normalized.startswith("."):
            normalized = f".{normalized}"
        for decl in self.supported_extensions:
            clean = decl.lower().strip()
            if not clean.startswith("."):
                clean = f".{clean}"
            if normalized == clean:
                return True
        return False


class PlainTextDocumentAdapter(BaseDocumentAdapter):
    """Native parser for plain text, markdown, json, yaml, and csv files."""

    @property
    def adapter_name(self) -> str:
        return "plain_text"

    supported_mime_types: Sequence[str] = (
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/x-rst",
        "application/json",
        "application/yaml",
        "application/x-yaml",
        "text/yaml",
        "text/csv",
        "text/tab-separated-values",
        "application/xml",
        "text/html",
    )
    supported_extensions: Sequence[str] = (
        ".txt",
        ".text",
        ".md",
        ".markdown",
        ".rst",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".tsv",
        ".xml",
        ".html",
        ".htm",
    )

    _EXTENSION_MIME_MAP: ClassVar[dict[str, str]] = {
        ".txt": "text/plain",
        ".text": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".rst": "text/x-rst",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".xml": "application/xml",
        ".html": "text/html",
        ".htm": "text/html",
    }

    def __init__(self, *, max_bytes: int | None = DEFAULT_MAX_TEXT_BYTES) -> None:
        self.max_bytes = max_bytes

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Decode and normalize plain text bytes."""
        if self.max_bytes is not None and len(data) > self.max_bytes:
            raise InvalidDocumentError(
                f"Text document '{filename}' exceeds maximum allowed size "
                f"({len(data)} > {self.max_bytes} bytes)"
            )
        # BOM detection and robust decoding (UTF-32 checked before UTF-16)
        has_explicit_bom = True
        if data.startswith(b"\xef\xbb\xbf"):
            clean_data = data[3:]
            encoding = "utf-8"
        elif data.startswith(b"\xff\xfe\x00\x00"):
            clean_data = data[4:]
            encoding = "utf-32-le"
        elif data.startswith(b"\x00\x00\xfe\xff"):
            clean_data = data[4:]
            encoding = "utf-32-be"
        elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
            clean_data = data
            encoding = "utf-16"
        else:
            has_explicit_bom = False
            clean_data = data
            encoding = "utf-8"

        # Binary check: if no explicit BOM, reject files containing NUL bytes
        if not has_explicit_bom and b"\x00" in clean_data[:4096]:
            raise UnsupportedFormatError(
                f"File '{filename}' appears to contain binary data (NUL bytes detected) "
                f"and cannot be parsed by PlainTextDocumentAdapter",
                requested=mime_type or filename,
                available=[self.adapter_name],
            )

        try:
            raw_text = clean_data.decode(encoding)
        except UnicodeDecodeError as err:
            if has_explicit_bom:
                raise ValueError(
                    f"Corrupt text file with explicit BOM {encoding} failed to decode: {err}"
                ) from err
            # Binary check: reject binary byte streams before non-UTF8 decoding fallbacks (MIN-05)
            sample = clean_data[:4096]
            control_count = sum(1 for b in sample if b < 32 and b not in (9, 10, 13))
            if control_count > len(sample) * 0.15:
                raise UnsupportedFormatError(
                    f"File '{filename}' contains binary data and cannot be parsed as text",
                    requested=mime_type or filename,
                    available=[self.adapter_name],
                ) from None
            try:
                raw_text = clean_data.decode("cp1252")
                encoding = "cp1252"
            except UnicodeDecodeError:
                raw_text = clean_data.decode("latin-1")
                encoding = "latin-1"

        # Shared normalization (LF and NFC)
        normalized_text = normalize_text(raw_text)

        source_sha256 = kwargs.get("source_sha256") or hashlib.sha256(data).hexdigest()
        doc_id = str(kwargs.get("document_id") or f"doc_{source_sha256[:16]}")
        ext = Path(filename).suffix.lower()
        deterministic_mime = self._EXTENSION_MIME_MAP.get(ext)
        if mime_type:
            resolved_mime = mime_type
        elif deterministic_mime:
            resolved_mime = deterministic_mime
        else:
            guessed_mime, _ = mimetypes.guess_type(filename)
            resolved_mime = guessed_mime or "text/plain"

        taint = kwargs.get("taint", TaintClassification.INTERNAL)

        metadata = dict(kwargs.get("metadata") or {})
        metadata["encoding"] = encoding

        char_count = len(normalized_text)
        page_entry = PageMapEntry(
            page_number=1,
            text=normalized_text,
            start_offset=0,
            end_offset=char_count,
            confidence=1.0,
        )

        return IngestionResult(
            document_id=doc_id,
            filename=filename,
            mime_type=resolved_mime,
            total_pages=1,
            total_characters=char_count,
            chunks=[],
            page_map=[page_entry],
            tables=[],
            taint=taint,
            source_sha256=source_sha256,
            normalized_text=normalized_text,
            metadata=metadata,
        )


__all__ = [
    "BaseDocumentAdapter",
    "DocumentAdapter",
    "PlainTextDocumentAdapter",
]
