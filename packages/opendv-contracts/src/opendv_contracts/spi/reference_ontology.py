"""Reference ontology adapter SPI protocol for terminology lookup and matching."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ReferenceOntologyAdapter(Protocol):
    """SPI protocol for reference ontology lookup and candidate retrieval."""

    def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
        """Look up entity definition, label, and metadata by exact standard URI."""
        ...

    def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search reference ontology candidates matching search term or token."""
        ...
