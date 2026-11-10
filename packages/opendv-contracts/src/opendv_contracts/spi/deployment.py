"""Deployment state provider SPI protocol for Data Vault contract protection (ONT-EVO-001)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DeploymentStateProvider(Protocol):
    """SPI protocol providing deployed entity and table state for contract governance."""

    def get_deployed_concepts(self) -> frozenset[str]:
        """Return set of deployed Canonical Business Concept URIs."""
        ...

    def get_deployed_tables(self) -> frozenset[str]:
        """Return set of deployed physical table names (Hubs, Links, Sats, Refs)."""
        ...

    def get_deployed_entities(self) -> frozenset[str]:
        """Return unified set of deployed concept URIs and table names."""
        ...
