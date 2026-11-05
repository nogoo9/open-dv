"""Registry for discovery and dispatch of format parsers."""

from __future__ import annotations

import importlib.metadata
import threading
from collections.abc import Sequence
from typing import Any

from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.spi.parser import FormatParser

FORMAT_PARSER_ENTRY_POINT_GROUP = "opendv.format_parsers"


class FormatParserRegistry:
    """Dynamic discovery and management of data format parsers."""

    def __init__(
        self,
        entry_point_group: str = FORMAT_PARSER_ENTRY_POINT_GROUP,
        register_defaults: bool | None = None,
    ) -> None:
        self._group = entry_point_group
        if register_defaults is None:
            self._register_defaults = entry_point_group == FORMAT_PARSER_ENTRY_POINT_GROUP
        else:
            self._register_defaults = register_defaults
        self._parsers: dict[str, FormatParser] = {}
        self._extension_map: dict[str, str] = {}
        self._mime_map: dict[str, str] = {}
        self._discovered = False
        self._lock = threading.RLock()

    def _discover(self) -> None:
        with self._lock:
            if self._discovered:
                return
            self._discovered = True
            if self._register_defaults:
                from opendv_sources.parsers import (
                    CsvFormatParser,
                    JsonFormatParser,
                    ParquetFormatParser,
                )

                self.register_parser("csv", CsvFormatParser())
                self.register_parser("tsv", CsvFormatParser())
                self.register_parser("parquet", ParquetFormatParser())
                self.register_parser("json", JsonFormatParser())
                self.register_parser("jsonl", JsonFormatParser())
                self.register_parser("ndjson", JsonFormatParser())

            eps: Any = ()
            try:
                eps = importlib.metadata.entry_points(group=self._group)
            except (KeyError, TypeError, AttributeError, ValueError):
                eps = ()

            for ep in eps:
                try:
                    loaded = ep.load()
                    inst = loaded() if isinstance(loaded, type) else loaded
                    self.register_parser(ep.name, inst)
                except (ImportError, AttributeError, TypeError, ValueError):
                    # Gracefully skip unloadable broken third-party entry points
                    pass

    def is_extension_supported(self, ext: str) -> bool:
        """Return True if the specified file extension has a registered parser."""
        self._discover()
        norm = ext.lower().strip()
        if not norm.startswith("."):
            norm = f".{norm}"
        with self._lock:
            return norm in self._extension_map

    def register_parser(
        self,
        format_name: str,
        parser: FormatParser | type[FormatParser],
        extensions: Sequence[str] | None = None,
        mime_types: Sequence[str] | None = None,
    ) -> None:
        """Register a format parser."""
        with self._lock:
            inst = parser() if isinstance(parser, type) else parser
            if not isinstance(inst, FormatParser):
                raise TypeError(
                    f"Expected FormatParser instance or subclass, got {type(inst).__name__}"
                )
            canon_name = format_name.lower().strip()
            self._parsers[canon_name] = inst

            exts = extensions if extensions is not None else inst.supported_extensions
            for ext in exts:
                norm_ext = ext.lower().strip()
                if not norm_ext.startswith("."):
                    norm_ext = f".{norm_ext}"
                self._extension_map[norm_ext] = canon_name

            mimes = mime_types if mime_types is not None else inst.supported_mime_types
            for mime in mimes:
                norm_mime = mime.lower().strip()
                self._mime_map[norm_mime] = canon_name

    def get_parser(self, format_or_extension_or_mime: str) -> FormatParser:
        """Resolve a format parser by format name, file extension, or MIME type."""
        self._discover()
        key = format_or_extension_or_mime.lower().strip()
        with self._lock:
            # 1. Exact format match
            if key in self._parsers:
                return self._parsers[key]

            # 2. Extension match
            ext_key = key if key.startswith(".") else f".{key}"
            if ext_key in self._extension_map:
                fmt = self._extension_map[ext_key]
                return self._parsers[fmt]

            # 3. MIME type match
            if key in self._mime_map:
                fmt = self._mime_map[key]
                return self._parsers[fmt]

            available = sorted(self._parsers.keys())
            raise UnsupportedFormatError(
                f"Unsupported data format '{format_or_extension_or_mime}'. "
                f"Installed formats: {available}.",
                requested=format_or_extension_or_mime,
                available=available,
            )

    def list_formats(self) -> list[str]:
        """List registered format identifiers."""
        self._discover()
        with self._lock:
            return sorted(self._parsers.keys())

    def list_extensions(self) -> list[str]:
        """List registered file extensions."""
        self._discover()
        with self._lock:
            return sorted(self._extension_map.keys())

    def reset(self) -> None:
        """Reset the registry to an empty, undiscovered state (for testing)."""
        with self._lock:
            self._parsers.clear()
            self._extension_map.clear()
            self._mime_map.clear()
            self._discovered = False


_DEFAULT_FORMAT_REGISTRY: FormatParserRegistry | None = None
_INIT_LOCK = threading.Lock()


def get_format_registry() -> FormatParserRegistry:
    """Obtain the global default FormatParserRegistry singleton."""
    global _DEFAULT_FORMAT_REGISTRY
    if _DEFAULT_FORMAT_REGISTRY is None:
        with _INIT_LOCK:
            if _DEFAULT_FORMAT_REGISTRY is None:
                _DEFAULT_FORMAT_REGISTRY = FormatParserRegistry()
    return _DEFAULT_FORMAT_REGISTRY


__all__ = [
    "FORMAT_PARSER_ENTRY_POINT_GROUP",
    "FormatParserRegistry",
    "get_format_registry",
]
