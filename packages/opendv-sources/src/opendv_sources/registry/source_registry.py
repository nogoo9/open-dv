"""Registry for discovery and dispatch of source adapters."""

from __future__ import annotations

import importlib.metadata
import threading
from typing import Any

from opendv_sources.exceptions import UnsupportedFormatError
from opendv_sources.spi.document import DocumentSourceAdapter
from opendv_sources.spi.tabular import TabularSourceAdapter

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
                except (ImportError, AttributeError, TypeError, ValueError):
                    # Gracefully skip unloadable broken third-party entry points
                    pass

    def register_tabular_adapter(
        self,
        name: str,
        adapter: TabularSourceAdapter | type[TabularSourceAdapter],
    ) -> None:
        """Register a tabular source adapter."""
        with self._lock:
            inst = adapter() if isinstance(adapter, type) else adapter
            if not isinstance(inst, TabularSourceAdapter):
                raise TypeError(
                    f"Expected TabularSourceAdapter instance or subclass, got {type(inst).__name__}"
                )
            self._tabular_adapters[name.lower().strip()] = inst

    def register_document_adapter(
        self,
        name: str,
        adapter: DocumentSourceAdapter | type[DocumentSourceAdapter],
    ) -> None:
        """Register a document source adapter."""
        with self._lock:
            inst = adapter() if isinstance(adapter, type) else adapter
            if not isinstance(inst, DocumentSourceAdapter):
                raise TypeError(f"Expected DocumentSourceAdapter, got {type(inst).__name__}")
            self._document_adapters[name.lower().strip()] = inst

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

    def get_tabular_adapter(self, name: str) -> TabularSourceAdapter:
        """Retrieve a registered TabularSourceAdapter by name."""
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
            return self._tabular_adapters[key]

    def get_document_adapter(self, name: str) -> DocumentSourceAdapter:
        """Retrieve a registered DocumentSourceAdapter by name."""
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
            return self._document_adapters[key]

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
        return any(
            raw.endswith(f".{ext}")
            for ext in ("csv", "tsv", "parquet", "pq", "json", "jsonl", "ndjson")
        )

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
