"""Pure Layer-2 document format adapters."""

from __future__ import annotations

from opendv.parsing.parsers._zip_guard import BoundedZipReader
from opendv.parsing.parsers.docx_parser import DocxParser
from opendv.parsing.parsers.liteparse_pdf import LiteParsePdfParser
from opendv.parsing.parsers.text_parsers import MarkdownTextParser

__all__ = [
    "BoundedZipReader",
    "DocxParser",
    "LiteParsePdfParser",
    "MarkdownTextParser",
]
