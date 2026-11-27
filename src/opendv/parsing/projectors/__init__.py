"""Layer-2 deterministic IR projectors for DocumentMap, Markdown, and ChunkRecord JSONL."""

from __future__ import annotations

from opendv.parsing.projectors.chunks_projector import ChunksProjector
from opendv.parsing.projectors.doc_map_projector import DocumentMapProjector
from opendv.parsing.projectors.markdown_projector import MarkdownProjector

__all__ = [
    "ChunksProjector",
    "DocumentMapProjector",
    "MarkdownProjector",
]
