"""Document Ingestion Service and Pipeline Framework (Task 4B.1 / Issue #201).

Provides the centralized IngestionService responsible for:
1. DocumentAdapter registry and resolution by MIME type and file extension.
2. Unicode NFC and LF newline normalization pipeline with exact O(N) offset remapping.
3. Accurate Unicode character and UTF-16 code unit offset tracking.
4. Sentence-boundary aware text chunking producing grounded EvidenceChunk instances.
5. Strict taint classification propagation preserving caller security posture.
"""

from __future__ import annotations

import bisect
import contextlib
import copy
import hashlib
import mimetypes
import os
import re
import stat
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from opendv_sources.ingestion.layout_evaluator import LayoutComplexityResult

from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TaintClassification,
)
from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.ingestion.adapter import DocumentAdapter, PlainTextDocumentAdapter
from opendv_sources.ingestion.normalization import (
    build_normalization_offset_map,
    compute_utf16_offsets_for_points,
    normalize_text,
    normalize_unicode_nfc,
)

TAINT_ORDER: dict[TaintClassification, int] = {
    TaintClassification.PUBLIC: 0,
    TaintClassification.INTERNAL: 1,
    TaintClassification.CONFIDENTIAL: 2,
    TaintClassification.RESTRICTED: 3,
}


def max_taint(*taints: TaintClassification | str | None) -> TaintClassification:
    """Compute the high-water mark taint level among non-None values.

    Enforces strict taint monotonicity (PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED)
    so that security classifications are never silently downgraded.
    """
    highest = TaintClassification.INTERNAL
    max_rank = -1
    for t in taints:
        if t is not None:
            tc = TaintClassification(t)
            rank = TAINT_ORDER[tc]
            if rank > max_rank:
                max_rank = rank
                highest = tc
    return highest


class IngestionService:
    """Orchestrator for document parsing, normalization, and evidence chunking."""

    DEFAULT_MAX_BYTES: int = 100 * 1024 * 1024  # 100 MB max document input size
    DEFAULT_MAX_CHUNKS: int = 250_000  # Accommodates 100 MB text with default chunking

    def __init__(
        self,
        adapters: list[DocumentAdapter] | None = None,
        default_chunk_size: int = 800,
        default_chunk_overlap: int = 100,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        default_ocr_mode: str = "auto",
    ) -> None:
        """Initialize IngestionService with adapter registry and chunking configuration.

        Args:
            adapters: Optional initial list of document adapters. If None,
                registers PlainTextDocumentAdapter by default.
            default_chunk_size: Default maximum character length per evidence chunk.
            default_chunk_overlap: Default character overlap between adjacent chunks.
            max_bytes: Maximum allowed document input size in bytes.
            max_chunks: Maximum allowable chunks before raising ValueError.
            default_ocr_mode: Default OCR strategy ('auto', 'force', 'off').
        """
        if default_chunk_size < 1:
            raise ValueError(f"default_chunk_size must be >= 1, got {default_chunk_size}")
        if default_chunk_overlap < 0:
            raise ValueError(f"default_chunk_overlap must be >= 0, got {default_chunk_overlap}")
        if default_chunk_overlap > default_chunk_size // 2:
            raise ValueError(
                f"default_chunk_overlap ({default_chunk_overlap}) must not exceed "
                f"default_chunk_size // 2 ({default_chunk_size // 2})"
            )
        if max_bytes < 1:
            raise ValueError(f"max_bytes must be >= 1, got {max_bytes}")
        if max_chunks < 1:
            raise ValueError(f"max_chunks must be >= 1, got {max_chunks}")

        self._adapters: list[DocumentAdapter] = []
        self.default_chunk_size = default_chunk_size
        self.default_chunk_overlap = default_chunk_overlap
        self.max_bytes = max_bytes
        self.max_chunks = max_chunks
        self.default_ocr_mode = default_ocr_mode

        if adapters is not None:
            for adapter in adapters:
                self.register_adapter(adapter)
        else:
            self.register_adapter(PlainTextDocumentAdapter())

    # -------------------------------------------------------------------------
    # Adapter Registry
    # -------------------------------------------------------------------------

    def register_adapter(self, adapter: DocumentAdapter, priority: bool = False) -> None:
        """Register a DocumentAdapter with the service.

        Args:
            adapter: DocumentAdapter instance.
            priority: If True, registers adapter at the front of the resolution chain.
        """
        if priority:
            self._adapters.insert(0, adapter)
        else:
            self._adapters.append(adapter)

    def unregister_adapter(self, adapter: DocumentAdapter) -> bool:
        """Remove a DocumentAdapter from the registry.

        Returns:
            True if adapter was found and removed, False otherwise.
        """
        if adapter in self._adapters:
            self._adapters.remove(adapter)
            return True
        return False

    def list_adapters(self) -> list[DocumentAdapter]:
        """Return a list of currently registered DocumentAdapter instances."""
        return list(self._adapters)

    def get_adapter_for_mime(self, mime_type: str) -> DocumentAdapter | None:
        """Find the first registered adapter that supports the given MIME type."""
        if not mime_type:
            return None
        for adapter in self._adapters:
            if adapter.supports_mime_type(mime_type):
                return adapter
        return None

    def get_adapter_for_extension(self, extension: str) -> DocumentAdapter | None:
        """Find the first registered adapter that supports the given file extension."""
        if not extension:
            return None
        norm_ext = extension.lower().strip()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"
        for adapter in self._adapters:
            if adapter.supports_extension(norm_ext):
                return adapter
        return None

    def get_adapter(
        self,
        filename: str | None = None,
        mime_type: str | None = None,
    ) -> DocumentAdapter:
        """Resolve a suitable adapter based on MIME type or filename extension.

        Args:
            filename: Document filename.
            mime_type: Explicit MIME media type.

        Returns:
            Matched DocumentAdapter.

        Raises:
            UnsupportedFormatError: If no registered adapter supports the format.
        """
        # 1. Try explicit MIME type (if specific)
        if mime_type and mime_type != "application/octet-stream":
            adapter = self.get_adapter_for_mime(mime_type)
            if adapter:
                return adapter
            available = [a.adapter_name for a in self._adapters]
            raise UnsupportedFormatError(
                f"No document adapter available for explicit mime_type='{mime_type}'",
                requested=mime_type,
                available=available,
            )

        # 2. Try filename extension
        if filename:
            ext = Path(filename).suffix
            if ext:
                adapter = self.get_adapter_for_extension(ext)
                if adapter:
                    return adapter

            # 3. Try guessing MIME type from filename
            guessed_mime, _ = mimetypes.guess_type(filename)
            if guessed_mime:
                adapter = self.get_adapter_for_mime(guessed_mime)
                if adapter:
                    return adapter

        # 4. Fallback for application/octet-stream if an adapter handles it
        if mime_type:
            adapter = self.get_adapter_for_mime(mime_type)
            if adapter:
                return adapter

        available = [a.adapter_name for a in self._adapters]
        raise UnsupportedFormatError(
            f"No document adapter available for filename='{filename}', mime_type='{mime_type}'",
            requested=mime_type or filename,
            available=available,
        )

    # -------------------------------------------------------------------------
    # Chunking Pipeline
    # -------------------------------------------------------------------------

    def chunk_text(
        self,
        text: str,
        document_id: str,
        page_map: list[PageMapEntry] | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        taint: TaintClassification | str = TaintClassification.INTERNAL,
        source_sha256: str | None = None,
        max_chunks: int | None = None,
    ) -> list[EvidenceChunk]:
        """Split normalized document text into sentence-aware EvidenceChunks.

        Maintains exact character code point offsets such that:
            text[chunk.start_char : chunk.end_char] == chunk.text

        Args:
            text: Normalized document text.
            document_id: Source document ID.
            page_map: Optional list of PageMapEntry instances for page resolution.
            chunk_size: Maximum chunk length in characters (default: self.default_chunk_size).
            chunk_overlap: Overlap in characters between adjacent chunks
                (must be <= chunk_size // 2).
            taint: TaintClassification level for created chunks.
            source_sha256: Optional SHA-256 digest of source bytes for provenance.
            max_chunks: Optional maximum allowable chunk count before raising ValueError.

        Returns:
            List of EvidenceChunk instances.

        Raises:
            ValueError: If chunk_size or chunk_overlap configuration is invalid or limits exceeded.
        """
        if not text:
            return []

        if chunk_size is not None and chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        if chunk_overlap is not None and chunk_overlap < 0:
            raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")

        size = chunk_size if chunk_size is not None else self.default_chunk_size
        if chunk_overlap is not None:
            if chunk_overlap > size // 2:
                raise ValueError(
                    f"chunk_overlap ({chunk_overlap}) must not exceed chunk_size // 2 ({size // 2})"
                )
            overlap = chunk_overlap
        else:
            overlap = min(self.default_chunk_overlap, size // 2)

        limit = max_chunks if max_chunks is not None else self.max_chunks

        total_len = len(text)
        raw_chunk_specs: list[tuple[str, str, int | None, int, int]] = []
        boundary_points: set[int] = set()
        curr_pos = 0
        chunk_idx = 1
        page_starts = [p.start_offset for p in page_map] if page_map else []

        while curr_pos < total_len:
            # Advance past leading whitespace
            while curr_pos < total_len and text[curr_pos].isspace():
                curr_pos += 1
            if curr_pos >= total_len:
                break

            target_end = min(curr_pos + size, total_len)

            if target_end == total_len:
                split_pos = total_len
            else:
                min_split = curr_pos + max(1, size // 3)
                window = text[min_split:target_end]

                split_pos = target_end
                # Paragraph break
                para_match = [m.end() for m in re.finditer(r"\n\s*\n+", window)]
                if para_match:
                    split_pos = min_split + para_match[-1]
                else:
                    # Sentence ending punctuation
                    sent_match = [m.end() for m in re.finditer(r"(?<=[.!?])\s+", window)]
                    if sent_match:
                        split_pos = min_split + sent_match[-1]
                    else:
                        # Line break
                        line_match = [m.end() for m in re.finditer(r"\n", window)]
                        if line_match:
                            split_pos = min_split + line_match[-1]
                        else:
                            # Word boundary
                            space_match = [m.end() for m in re.finditer(r"\s+", window)]
                            if space_match:
                                split_pos = min_split + space_match[-1]

            # Trim trailing whitespace from split_pos to preserve exact slice equality
            trimmed_end = split_pos
            while trimmed_end > curr_pos and text[trimmed_end - 1].isspace():
                trimmed_end -= 1

            if trimmed_end <= curr_pos:
                trimmed_end = split_pos

            chunk_text_slice = text[curr_pos:trimmed_end]
            if chunk_text_slice:
                if len(raw_chunk_specs) >= limit:
                    raise ValueError(
                        f"Document chunk count exceeded safety limit of {limit} chunks"
                    )

                page_num = self._resolve_page_number(
                    curr_pos, trimmed_end, page_map, page_starts=page_starts
                )
                chunk_id = f"{document_id}_chk_{chunk_idx:04d}"
                raw_chunk_specs.append(
                    (chunk_id, chunk_text_slice, page_num, curr_pos, trimmed_end)
                )
                boundary_points.add(curr_pos)
                boundary_points.add(trimmed_end)
                chunk_idx += 1

            if split_pos >= total_len:
                break

            # Calculate next start position with guaranteed minimum advance
            min_advance = max(1, (trimmed_end - curr_pos) // 2)
            raw_next = max(curr_pos + min_advance, trimmed_end - overlap)

            # Align raw_next forward to word boundary only if we landed inside a word
            next_start = raw_next
            if (
                next_start < trimmed_end
                and not text[next_start - 1].isspace()
                and not text[next_start].isspace()
            ):
                while next_start < trimmed_end and not text[next_start].isspace():
                    next_start += 1
                while next_start < trimmed_end and text[next_start].isspace():
                    next_start += 1

            if next_start >= trimmed_end:
                next_start = raw_next

            curr_pos = next_start

        if not raw_chunk_specs:
            return []

        u_map = compute_utf16_offsets_for_points(text, list(boundary_points))
        return [
            EvidenceChunk(
                chunk_id=c_id,
                document_id=document_id,
                text=c_slice,
                page_number=p_num,
                start_char=c_start,
                end_char=c_end,
                utf16_start=u_map[c_start],
                utf16_end=u_map[c_end],
                confidence=1.0,
                taint=taint,
                source_sha256=source_sha256,
            )
            for c_id, c_slice, p_num, c_start, c_end in raw_chunk_specs
        ]

    def _resolve_page_number(
        self,
        start_char: int,
        end_char: int,
        page_map: list[PageMapEntry] | None,
        page_starts: list[int] | None = None,
    ) -> int | None:
        """Determine 1-indexed page number with the largest text overlap for the chunk."""
        if not page_map:
            return None

        # Binary search for pages overlapping [start_char, end_char]
        starts = page_starts if page_starts is not None else [p.start_offset for p in page_map]
        idx = bisect.bisect_right(starts, start_char)
        search_start = max(0, idx - 1)

        best_page: int | None = None
        max_overlap = -1

        for page in page_map[search_start:]:
            if page.start_offset >= end_char:
                break
            overlap_start = max(start_char, page.start_offset)
            overlap_end = min(end_char, page.end_offset)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > max_overlap:
                max_overlap = overlap
                best_page = page.page_number

        if best_page is not None and max_overlap > 0:
            return best_page

        return None

    # -------------------------------------------------------------------------
    # Ingestion Pipeline
    # -------------------------------------------------------------------------

    def ingest_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        taint: TaintClassification | str | None = None,
        max_chunks: int | None = None,
        ocr_mode: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse raw document bytes into an IngestionResult with chunked evidence.

        Args:
            data: Raw document file content.
            filename: Document filename.
            mime_type: Optional explicit MIME media type.
            chunk_size: Optional chunk size override.
            chunk_overlap: Optional chunk overlap override.
            taint: Optional explicit taint level (prevents adapter downgrading).
            max_chunks: Optional maximum allowable chunks limit.
            ocr_mode: Optional OCR strategy override ('auto', 'force', 'off').
            **kwargs: Extra parameters passed to the adapter.

        Returns:
            Normalized IngestionResult with EvidenceChunks and PageMapEntries.
        """
        if len(data) > self.max_bytes:
            raise ValueError(
                f"Document size ({len(data)} bytes) exceeds maximum limit ({self.max_bytes} bytes)"
            )

        source_sha256 = hashlib.sha256(data).hexdigest()
        adapter = self.get_adapter(filename=filename, mime_type=mime_type)

        # Pass taint and precomputed source digest to adapter kwargs
        adapter_kwargs = dict(kwargs)
        adapter_kwargs["source_sha256"] = source_sha256
        if taint is not None:
            adapter_kwargs["taint"] = TaintClassification(taint)
        effective_ocr_mode = ocr_mode or getattr(self, "default_ocr_mode", "auto")
        if "ocr_mode" not in adapter_kwargs:
            adapter_kwargs["ocr_mode"] = effective_ocr_mode

        raw_result = adapter.parse_bytes(
            data,
            filename=filename,
            mime_type=mime_type,
            **adapter_kwargs,
        )

        # High-water mark taint resolution from caller, doc, and chunks (MAJ-02)
        all_taints: list[TaintClassification | str | None] = [taint, raw_result.taint]
        if raw_result.chunks:
            for c in raw_result.chunks:
                all_taints.append(c.taint)
        resolved_taint = max_taint(*all_taints)

        effective_max_chunks = max_chunks if max_chunks is not None else self.max_chunks

        # Check normalization necessity: fast path skips offset map allocation
        raw_adapter_text = raw_result.normalized_text or ""
        if unicodedata.is_normalized("NFC", raw_adapter_text) and "\r" not in raw_adapter_text:
            norm_text = raw_adapter_text
            text_was_remapped = False
            offset_map = None
        else:
            norm_text, offset_map = build_normalization_offset_map(raw_adapter_text)
            text_was_remapped = True
        char_count = len(norm_text)

        # 1. Remap page map spans first (MAJ-01)
        remapped_page_specs: list[tuple[PageMapEntry, int, int]] = []
        if raw_result.page_map:
            prev_end = -1
            for page in raw_result.page_map:
                if page.start_offset < 0 or page.end_offset < page.start_offset:
                    raise ValueError(
                        f"Adapter '{adapter.adapter_name}' produced page '{page.page_number}' "
                        f"with invalid span [{page.start_offset}, {page.end_offset}]"
                    )
                if text_was_remapped:
                    assert offset_map is not None
                    if page.end_offset >= len(offset_map):
                        raise ValueError(
                            f"Adapter '{adapter.adapter_name}' produced page '{page.page_number}' "
                            f"with end_offset {page.end_offset} exceeding "
                            f"raw text length {len(offset_map) - 1}"
                        )
                    p_start = offset_map[page.start_offset]
                    p_end = offset_map[page.end_offset]
                else:
                    if page.end_offset > char_count:
                        raise ValueError(
                            f"Adapter '{adapter.adapter_name}' produced page '{page.page_number}' "
                            f"with end_offset {page.end_offset} exceeding "
                            f"document length {char_count}"
                        )
                    p_start = page.start_offset
                    p_end = page.end_offset

                if p_start < prev_end:
                    raise ValueError(
                        f"Adapter '{adapter.adapter_name}' produced overlapping page map entry "
                        f"at page '{page.page_number}': start {p_start} < previous end {prev_end}"
                    )
                prev_end = p_end
                remapped_page_specs.append((page, p_start, p_end))

        # 2. Remap chunk spans first (MAJ-01, MIN-04)
        remapped_chunk_specs: list[
            tuple[EvidenceChunk, int | None, int | None, str, TaintClassification]
        ] = []
        dropped_chunk_ids: list[str] = []
        if raw_result.chunks:
            if len(raw_result.chunks) > effective_max_chunks:
                raise ValueError(
                    f"Adapter '{adapter.adapter_name}' returned {len(raw_result.chunks)} chunks, "
                    f"exceeding max_chunks limit of {effective_max_chunks}"
                )
            for chk in raw_result.chunks:
                chunk_taint = max_taint(resolved_taint, chk.taint)
                if chk.start_char is not None and chk.end_char is not None:
                    if chk.start_char < 0 or chk.end_char < chk.start_char:
                        raise ValueError(
                            f"Adapter '{adapter.adapter_name}' produced chunk "
                            f"'{chk.chunk_id}' with invalid span [{chk.start_char}, {chk.end_char}]"
                        )
                    if text_was_remapped:
                        assert offset_map is not None
                        if chk.end_char >= len(offset_map):
                            raise ValueError(
                                f"Adapter '{adapter.adapter_name}' produced chunk "
                                f"'{chk.chunk_id}' with end_char {chk.end_char} exceeding "
                                f"raw text length {len(offset_map) - 1}"
                            )
                        c_start = offset_map[chk.start_char]
                        c_end = offset_map[chk.end_char]
                    else:
                        if chk.end_char > char_count:
                            raise ValueError(
                                f"Adapter '{adapter.adapter_name}' produced chunk "
                                f"'{chk.chunk_id}' with end_char {chk.end_char} exceeding "
                                f"document length {char_count}"
                            )
                        c_start = chk.start_char
                        c_end = chk.end_char

                    c_text = norm_text[c_start:c_end]
                    expected_text = normalize_text(chk.text) if text_was_remapped else chk.text
                    # MIN-04: If NFC normalization decomposed or recombined on cluster boundaries
                    if c_text != expected_text and (
                        normalize_unicode_nfc(c_text) != normalize_unicode_nfc(expected_text)
                    ):
                        raise ValueError(
                            f"Adapter '{adapter.adapter_name}' produced chunk '{chk.chunk_id}' "
                            f"with text mismatch against document span [{c_start}, {c_end}]"
                        )
                else:
                    c_start = None
                    c_end = None
                    c_text = normalize_text(chk.text)

                if c_text:
                    remapped_chunk_specs.append((chk, c_start, c_end, c_text, chunk_taint))
                else:
                    dropped_chunk_ids.append(chk.chunk_id)

        # 3. Collect remapped boundary points across norm_text for exact O(K) UTF-16 lookup
        boundary_points: set[int] = {0, char_count}
        for _, p_start, p_end in remapped_page_specs:
            boundary_points.add(p_start)
            boundary_points.add(p_end)
        for _, c_start, c_end, _, _ in remapped_chunk_specs:
            if c_start is not None:
                boundary_points.add(c_start)
            if c_end is not None:
                boundary_points.add(c_end)
        utf16_points = compute_utf16_offsets_for_points(norm_text, list(boundary_points))

        # 4. Construct PageMapEntry instances with verified UTF-16 offsets
        updated_page_map: list[PageMapEntry] = []
        if remapped_page_specs:
            for page, p_start, p_end in remapped_page_specs:
                p_text = norm_text[p_start:p_end]
                u_start = utf16_points[p_start]
                u_end = utf16_points[p_end]
                page_remap = PageMapEntry(
                    page_number=page.page_number,
                    text=p_text,
                    start_offset=p_start,
                    end_offset=p_end,
                    utf16_start=u_start,
                    utf16_end=u_end,
                    is_scanned=page.is_scanned,
                    ocr_skipped=page.ocr_skipped,
                    confidence=page.confidence,
                    bounding_box=page.bounding_box,
                    has_tables=page.has_tables,
                )
                updated_page_map.append(page_remap)
        else:
            updated_page_map.append(
                PageMapEntry(
                    page_number=1,
                    text=norm_text,
                    start_offset=0,
                    end_offset=char_count,
                    utf16_start=0,
                    utf16_end=utf16_points[char_count],
                    confidence=1.0,
                )
            )

        # 5. Construct EvidenceChunks and handle dropped_chunks / metadata safely (MIN-06)
        result_metadata = copy.deepcopy(raw_result.metadata) if raw_result.metadata else {}
        if remapped_chunk_specs or dropped_chunk_ids:
            remapped_chunks: list[EvidenceChunk] = []
            for chk, c_start, c_end, c_text, chunk_taint in remapped_chunk_specs:
                u_c_start = utf16_points[c_start] if c_start is not None else None
                u_c_end = utf16_points[c_end] if c_end is not None else None
                # Grounding verification: recompute page number when offsets present (MIN-04)
                if c_start is not None and c_end is not None and updated_page_map:
                    resolved_page = self._resolve_page_number(c_start, c_end, updated_page_map)
                    c_page = resolved_page if resolved_page is not None else chk.page_number
                else:
                    c_page = chk.page_number

                c_meta = copy.deepcopy(chk.metadata) if chk.metadata is not None else {}
                if c_start is None and c_end is None:
                    c_meta.setdefault("ungrounded", True)

                remapped_chunks.append(
                    EvidenceChunk(
                        chunk_id=chk.chunk_id,
                        document_id=raw_result.document_id,
                        text=c_text,
                        page_number=c_page,
                        start_char=c_start,
                        end_char=c_end,
                        utf16_start=u_c_start,
                        utf16_end=u_c_end,
                        confidence=chk.confidence,
                        metadata=c_meta,
                        bounding_box=chk.bounding_box,
                        is_table=chk.is_table,
                        table_id=chk.table_id,
                        taint=chunk_taint,
                        source_sha256=source_sha256,
                    )
                )
            if dropped_chunk_ids:
                existing_dropped = result_metadata.get("dropped_chunks")
                if isinstance(existing_dropped, (list, tuple)):
                    result_metadata["dropped_chunks"] = list(existing_dropped) + dropped_chunk_ids
                else:
                    result_metadata["dropped_chunks"] = list(dropped_chunk_ids)
            chunks = remapped_chunks
        elif norm_text:
            chunks = self.chunk_text(
                text=norm_text,
                document_id=raw_result.document_id,
                page_map=updated_page_map,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                taint=resolved_taint,
                source_sha256=source_sha256,
                max_chunks=effective_max_chunks,
            )
        else:
            chunks = []

        total_pages = max(
            raw_result.total_pages,
            len(updated_page_map),
            1 if norm_text else 0,
        )

        return IngestionResult(
            document_id=raw_result.document_id,
            filename=raw_result.filename or filename,
            mime_type=raw_result.mime_type or mime_type or "application/octet-stream",
            total_pages=total_pages,
            total_characters=char_count,
            chunks=chunks,
            page_map=updated_page_map,
            tables=raw_result.tables,
            taint=resolved_taint,
            metadata=result_metadata,
            source_sha256=source_sha256,
            normalized_text=norm_text,
        )

    def ingest_stream(
        self,
        stream: BinaryIO,
        filename: str,
        mime_type: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        taint: TaintClassification | str | None = None,
        max_chunks: int | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Parse a binary stream into an IngestionResult with bounded read."""
        data = stream.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise ValueError(
                f"Stream content exceeds maximum allowed size ({self.max_bytes} bytes)"
            )
        return self.ingest_bytes(
            data,
            filename=filename,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            taint=taint,
            max_chunks=max_chunks,
            **kwargs,
        )

    def ingest_file(
        self,
        file_path: str | Path,
        mime_type: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        taint: TaintClassification | str | None = None,
        allowed_roots: Sequence[str | Path] | None = None,
        max_chunks: int | None = None,
        ocr_mode: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Read and parse a regular file from disk with bounded size validation.

        Args:
            file_path: File system path to the document.
            mime_type: Optional explicit MIME media type.
            chunk_size: Optional chunk size override.
            chunk_overlap: Optional chunk overlap override.
            taint: Optional taint classification override.
            allowed_roots: Optional sequence of directory roots for path confinement.
                If provided, file_path must resolve within one of these roots.
            max_chunks: Optional maximum chunk limit.
            ocr_mode: Optional OCR strategy override ('auto', 'force', 'off').
            **kwargs: Extra parameters passed to the underlying DocumentAdapter.

        Raises:
            ValueError: If path is not allowed, is a symlink, not a file, or exceeds max_bytes.
        """
        path = Path(file_path)
        resolved_path = path.resolve()

        if allowed_roots is not None:
            roots = [Path(r).resolve() for r in allowed_roots]
            if not any(resolved_path.is_relative_to(r) for r in roots):
                raise ValueError(f"File path '{path}' does not resolve within any allowed roots")

        if path.is_symlink():
            raise ValueError(f"Symlinks are prohibited for document ingestion: {path}")

        if not path.is_file():
            raise ValueError(f"Path is not a regular file: {path}")

        # Securely open resolved path in binary mode and verify regular file (MAJ-01, MIN-01)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        fd = os.open(resolved_path, flags)
        try:
            stat_info = os.fstat(fd)
            if not stat.S_ISREG(stat_info.st_mode):
                raise ValueError(f"Path is not a regular file: {path}")
            f = os.fdopen(fd, "rb")
            fd = -1  # Ownership transferred to f
            with f:
                data = f.read(self.max_bytes + 1)
        except Exception:
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise

        if len(data) > self.max_bytes:
            raise ValueError(f"File exceeds maximum allowed size ({self.max_bytes} bytes)")
        return self.ingest_bytes(
            data,
            filename=path.name,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            taint=taint,
            max_chunks=max_chunks,
            ocr_mode=ocr_mode,
            **kwargs,
        )

    def ingest_text(
        self,
        text: str,
        filename: str = "document.txt",
        mime_type: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        taint: TaintClassification | str | None = None,
        max_chunks: int | None = None,
        ocr_mode: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Convenience method to ingest a text string directly."""
        data = text.encode("utf-8")
        return self.ingest_bytes(
            data,
            filename=filename,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            taint=taint,
            max_chunks=max_chunks,
            ocr_mode=ocr_mode,
            **kwargs,
        )

    def evaluate_layout_complexity(
        self,
        result: IngestionResult,
    ) -> list[LayoutComplexityResult]:
        """Evaluate layout complexity across all pages in an IngestionResult.

        Args:
            result: IngestionResult containing PageMapEntry instances.

        Returns:
            List of LayoutComplexityResult instances corresponding to each page.
        """
        from opendv_sources.ingestion.layout_evaluator import evaluate_page_complexity

        results: list[LayoutComplexityResult] = []
        for p in result.page_map:
            res = evaluate_page_complexity(
                text=p.text,
                raster_ratio=1.0 if p.is_scanned else 0.0,
            )
            results.append(res)
        return results


__all__ = [
    "IngestionService",
    "max_taint",
]
