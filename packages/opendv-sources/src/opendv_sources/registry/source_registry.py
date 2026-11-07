"""Registry for discovery and dispatch of source adapters."""

from __future__ import annotations

import importlib.metadata
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.registry.format_registry import get_format_registry
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

logger = logging.getLogger(__name__)

SOURCE_ADAPTER_ENTRY_POINT_GROUP = "opendv.source_adapters"


class SourceAdapterRegistry:
    """Dynamic discovery and management of tabular and document source adapters."""

    def __init__(self, entry_point_group: str = SOURCE_ADAPTER_ENTRY_POINT_GROUP) -> None:
        self._group = entry_point_group
        self._tabular_adapters: dict[str, TabularSourceAdapter] = {}
        self._document_adapters: dict[str, DocumentSourceAdapter] = {}
        self._discovered = False
        self._lock = threading.RLock()

    def _discover(self) -> None:
        with self._lock:
            if self._discovered:
                return
            self._discovered = True
            eps: Any = ()
            try:
                eps = importlib.metadata.entry_points(group=self._group)
            except (KeyError, TypeError, AttributeError, ValueError):
                eps = ()

            for ep in eps:
                try:
                    loaded = ep.load()
                    inst = loaded() if isinstance(loaded, type) else loaded
                    self.register_adapter(ep.name, inst)
                except (ImportError, AttributeError, TypeError, ValueError) as exc:
                    logger.warning("Failed to load source adapter plugin '%s': %s", ep.name, exc)

    def register_tabular_adapter(
        self,
        name: str,
        adapter: TabularSourceAdapter | type[TabularSourceAdapter],
    ) -> None:
        """Register a tabular source adapter."""
        with self._lock:
            key = name.lower().strip()
            inst = adapter() if isinstance(adapter, type) else adapter
            if not isinstance(inst, TabularSourceAdapter):
                raise TypeError(
                    f"Expected TabularSourceAdapter instance or subclass, got {type(inst).__name__}"
                )
            if key in self._tabular_adapters and type(self._tabular_adapters[key]) is not type(
                inst
            ):
                logger.warning(
                    "Overwriting tabular adapter '%s' (%s) with %s",
                    key,
                    type(self._tabular_adapters[key]).__name__,
                    type(inst).__name__,
                )
            self._tabular_adapters[key] = inst

    def register_document_adapter(
        self,
        name: str,
        adapter: DocumentSourceAdapter | type[DocumentSourceAdapter],
    ) -> None:
        """Register a document source adapter."""
        with self._lock:
            key = name.lower().strip()
            inst = adapter() if isinstance(adapter, type) else adapter
            if not isinstance(inst, DocumentSourceAdapter):
                raise TypeError(f"Expected DocumentSourceAdapter, got {type(inst).__name__}")
            if key in self._document_adapters and type(self._document_adapters[key]) is not type(
                inst
            ):
                logger.warning(
                    "Overwriting document adapter '%s' (%s) with %s",
                    key,
                    type(self._document_adapters[key]).__name__,
                    type(inst).__name__,
                )
            self._document_adapters[key] = inst

    def register_adapter(
        self,
        name: str,
        adapter: Any,
    ) -> None:
        """Register an adapter, automatically detecting tabular vs document capability."""
        registered = False
        if isinstance(adapter, TabularSourceAdapter) or (
            isinstance(adapter, type) and issubclass(adapter, TabularSourceAdapter)
        ):
            self.register_tabular_adapter(name, adapter)
            registered = True
        if isinstance(adapter, DocumentSourceAdapter) or (
            isinstance(adapter, type) and issubclass(adapter, DocumentSourceAdapter)
        ):
            self.register_document_adapter(name, adapter)
            registered = True
        if not registered:
            raise TypeError(
                f"Adapter '{name}' of type {type(adapter).__name__} does not implement "
                "TabularSourceAdapter or DocumentSourceAdapter."
            )

    def get_tabular_adapter(
        self,
        name: str,
        *,
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        allow_temp: bool = False,
    ) -> TabularSourceAdapter:
        """Retrieve a registered TabularSourceAdapter, optionally configured with server jail."""
        self._discover()
        key = name.lower().strip()
        with self._lock:
            if key not in self._tabular_adapters:
                available = sorted(self._tabular_adapters.keys())
                msg = (
                    f"Unsupported source adapter '{name}'. "
                    f"Installed tabular adapters: {available}. "
                    f"Install plugin (e.g. uv add opendv-sources --extra {name})."
                )
                raise UnsupportedFormatError(
                    msg,
                    requested=name,
                    available=available,
                )
            base = self._tabular_adapters[key]
            if workspace_root is not None or allowed_roots is not None or allow_temp:
                try:
                    adapter_cls: Any = type(base)
                    return cast(
                        TabularSourceAdapter,
                        adapter_cls(
                            workspace_root=workspace_root,
                            allowed_roots=allowed_roots,
                            allow_temp=allow_temp,
                        ),
                    )
                except TypeError:
                    return base
            return base

    def get_document_adapter(
        self,
        name: str,
        *,
        workspace_root: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        allow_temp: bool = False,
    ) -> DocumentSourceAdapter:
        """Retrieve a registered DocumentSourceAdapter, optionally configured with server jail."""
        self._discover()
        key = name.lower().strip()
        with self._lock:
            if key not in self._document_adapters:
                available = sorted(self._document_adapters.keys())
                msg = (
                    f"Unsupported document adapter '{name}'. "
                    f"Installed document adapters: {available}. "
                    f"Install plugin (e.g. uv add opendv-sources --extra {name})."
                )
                raise UnsupportedFormatError(
                    msg,
                    requested=name,
                    available=available,
                )
            base = self._document_adapters[key]
            if workspace_root is not None or allowed_roots is not None or allow_temp:
                try:
                    doc_adapter_cls: Any = type(base)
                    return cast(
                        DocumentSourceAdapter,
                        doc_adapter_cls(
                            workspace_root=workspace_root,
                            allowed_roots=allowed_roots,
                            allow_temp=allow_temp,
                        ),
                    )
                except TypeError:
                    return base
            return base

    def list_tabular_adapters(self) -> list[str]:
        """List registered tabular adapter names."""
        self._discover()
        with self._lock:
            return sorted(self._tabular_adapters.keys())

    def list_document_adapters(self) -> list[str]:
        """List registered document adapter names."""
        self._discover()
        with self._lock:
            return sorted(self._document_adapters.keys())

    def list_adapters(self) -> list[str]:
        """List all registered adapter names."""
        self._discover()
        with self._lock:
            return sorted(set(self._tabular_adapters.keys()) | set(self._document_adapters.keys()))

    def is_document_path(self, target: Any) -> bool:
        """Return True if target path or URI corresponds to an unstructured document."""
        self._discover()
        raw = str(target).lower().strip()
        return any(raw.endswith(f".{ext}") for ext in ("txt", "text", "md", "markdown", "pdf"))

    def resolve_document_adapter_name(self, target: Any) -> str:
        """Resolve document adapter name for a target path or URI."""
        self._discover()
        raw = str(target).lower().strip()
        if raw.endswith(".pdf"):
            return "pdf"
        return "text"

    def is_tabular_path(self, target: Any) -> bool:
        """Return True if target path or URI corresponds to a supported tabular file."""
        self._discover()
        raw = str(target).lower().strip()
        if any(raw.endswith(f".{ext}") for ext in ("sqlite", "sqlite3", "duckdb", "db")):
            return True
        if "." in raw:
            suffix = f".{raw.rsplit('.', 1)[-1]}"
            if get_format_registry().is_extension_supported(suffix):
                return True
        return False

    def reset(self) -> None:
        """Reset the registry to an empty, undiscovered state (for testing)."""
        with self._lock:
            self._tabular_adapters.clear()
            self._document_adapters.clear()
            self._discovered = False


_DEFAULT_SOURCE_REGISTRY: SourceAdapterRegistry | None = None
_INIT_LOCK = threading.Lock()


def get_source_registry() -> SourceAdapterRegistry:
    """Obtain the global default SourceAdapterRegistry singleton."""
    global _DEFAULT_SOURCE_REGISTRY
    if _DEFAULT_SOURCE_REGISTRY is None:
        with _INIT_LOCK:
            if _DEFAULT_SOURCE_REGISTRY is None:
                _DEFAULT_SOURCE_REGISTRY = SourceAdapterRegistry()
    return _DEFAULT_SOURCE_REGISTRY


__all__ = [
    "SOURCE_ADAPTER_ENTRY_POINT_GROUP",
    "SourceAdapterRegistry",
    "get_source_registry",
]
