"""Declarative registry resolving URI schemes and file extensions to source adapters.

Prevents hardcoded adapter/extension branching across the codebase per ADR-020.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from opendv_sources.config.defaults import SourceDefaultsConfig, get_source_defaults
from opendv_sources.exceptions import UnsupportedFormatError


class SourceSchemeRegistry:
    """Declarative registry mapping URI schemes and file suffixes to adapter plugins."""

    def __init__(self, config: SourceDefaultsConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._config = config or get_source_defaults()
        self._scheme_map: dict[str, str] = dict(self._config.scheme_to_adapter)
        self._extension_map: dict[str, str] = dict(self._config.extension_to_adapter)
        self._format_map: dict[str, str] = dict(self._config.extension_to_format)

    def register_scheme(self, scheme: str, adapter_name: str) -> None:
        """Register or override a URI scheme mapping."""
        with self._lock:
            clean_scheme = scheme.lower().rstrip(":/")
            self._scheme_map[clean_scheme] = adapter_name

    def register_extension(
        self,
        extension: str,
        adapter_name: str,
        format_name: str | None = None,
    ) -> None:
        """Register or override a file extension mapping."""
        with self._lock:
            ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            self._extension_map[ext] = adapter_name
            if format_name:
                self._format_map[ext] = format_name

    def _extract_scheme(self, uri_str: str) -> tuple[str | None, str]:
        """Extract URI scheme if present."""
        clean = uri_str.strip()
        for sep in (":///", "://", ":"):
            idx = clean.find(sep)
            if idx > 0:
                potential_scheme = clean[:idx].lower()
                if (
                    potential_scheme in self._scheme_map
                    or potential_scheme in self._config.cas_schemes
                ):
                    remainder = clean[idx + len(sep) :]
                    return potential_scheme, remainder
        return None, clean

    def _extract_extension(self, path_or_uri: str | Path) -> str | None:
        """Extract normalized file extension."""
        if isinstance(path_or_uri, Path):
            return path_or_uri.suffix.lower() if path_or_uri.suffix else None

        raw = str(path_or_uri).strip()
        clean = raw.split("?")[0].split("#")[0]
        p = Path(clean)
        if p.suffix and p.suffix.lower() in self._extension_map:
            return p.suffix.lower()

        if "?" in raw:
            with contextlib.suppress(ValueError):
                qs = parse_qs(urlsplit(raw).query)
                if qs.get("format"):
                    fmt = qs["format"][0].lower().strip()
                    for ext, f in self._format_map.items():
                        if f == fmt:
                            return ext
                if qs.get("ext"):
                    ext_val = qs["ext"][0].lower().strip()
                    return ext_val if ext_val.startswith(".") else f".{ext_val}"

        return p.suffix.lower() if p.suffix else None

    def resolve_adapter(
        self,
        uri_or_path: str | Path,
        explicit_adapter: str | None = None,
    ) -> str:
        """Resolve adapter name from explicit override, URI scheme, or file suffix."""
        if explicit_adapter and explicit_adapter.strip():
            return explicit_adapter.strip()

        raw_str = str(uri_or_path).strip()
        if "?" in raw_str:
            with contextlib.suppress(ValueError):
                qs = parse_qs(urlsplit(raw_str).query)
                if qs.get("adapter"):
                    return qs["adapter"][0].strip()

        with self._lock:
            scheme, _ = self._extract_scheme(raw_str)
            if scheme and scheme in self._scheme_map:
                return self._scheme_map[scheme]

            ext = self._extract_extension(uri_or_path)
            if ext and ext in self._extension_map:
                return self._extension_map[ext]

        available = sorted(set(self._scheme_map.keys()) | set(self._extension_map.keys()))
        raise UnsupportedFormatError(
            f"Unable to resolve source adapter for location: '{uri_or_path}'. "
            f"Declare an explicit adapter or use a recognized scheme/extension.",
            requested=str(uri_or_path),
            available=available,
        )

    def resolve_format(
        self,
        uri_or_path: str | Path,
        explicit_format: str | None = None,
    ) -> str | None:
        """Resolve physical data format from explicit override, URI scheme, or file suffix."""
        if explicit_format and explicit_format.strip():
            return explicit_format.strip()

        raw_str = str(uri_or_path).strip()
        with self._lock:
            scheme, _ = self._extract_scheme(raw_str)
            if scheme:
                if scheme in self._config.sqlite_schemes:
                    return "sqlite"
                if scheme in self._config.duckdb_schemes:
                    return "duckdb"
                if scheme in self._config.databricks_schemes:
                    return "delta"

            ext = self._extract_extension(uri_or_path)
            if ext and ext in self._format_map:
                return self._format_map[ext]

        return None

    def is_sqlite(self, uri_or_path: str | Path) -> bool:
        """Return True if the URI or file path targets an SQLite database."""
        raw_str = str(uri_or_path).strip()
        with self._lock:
            scheme, _ = self._extract_scheme(raw_str)
            if scheme and scheme in self._config.sqlite_schemes:
                return True
            ext = self._extract_extension(uri_or_path)
            if ext and ext in self._config.sqlite_extensions:
                return True
        return False

    def is_multi_table_capable(self, uri_or_path: str | Path) -> bool:
        """Return True if the resolved source supports multi-table catalog discovery."""
        raw_str = str(uri_or_path).strip()
        with self._lock:
            scheme, _ = self._extract_scheme(raw_str)
            if scheme and (
                scheme in self._config.sqlite_schemes
                or scheme in self._config.duckdb_schemes
                or scheme in self._config.databricks_schemes
            ):
                return True
            ext = self._extract_extension(uri_or_path)
            if ext and (
                ext in self._config.sqlite_extensions or ext in self._config.duckdb_extensions
            ):
                return True
        return False


_GLOBAL_SCHEME_REGISTRY: SourceSchemeRegistry | None = None
_GLOBAL_SCHEME_LOCK = threading.Lock()


def get_scheme_registry() -> SourceSchemeRegistry:
    """Obtain global singleton instance of SourceSchemeRegistry."""
    global _GLOBAL_SCHEME_REGISTRY
    if _GLOBAL_SCHEME_REGISTRY is None:
        with _GLOBAL_SCHEME_LOCK:
            if _GLOBAL_SCHEME_REGISTRY is None:
                _GLOBAL_SCHEME_REGISTRY = SourceSchemeRegistry()
    return _GLOBAL_SCHEME_REGISTRY


def reset_scheme_registry() -> None:
    """Reset global scheme registry (for testing isolation)."""
    global _GLOBAL_SCHEME_REGISTRY
    with _GLOBAL_SCHEME_LOCK:
        _GLOBAL_SCHEME_REGISTRY = None


__all__ = [
    "SourceSchemeRegistry",
    "get_scheme_registry",
    "reset_scheme_registry",
]
