"""Domain StateStore, PlatformStateContainer, and CAS utilities for opendv-service."""

from __future__ import annotations

from typing import Any

from opendv_service.state.base import (
    BaseStateContainer,
    resolve_safe_repo_path,
)
from opendv_service.state.cas import store_cas_artifact
from opendv_service.state.container import (
    PlatformStateContainer,
    StateStore,
)


def get_platform_state(service_or_container: Any) -> PlatformStateContainer:
    """Extract or return PlatformStateContainer from a service or container instance."""
    if hasattr(service_or_container, "state") and isinstance(
        service_or_container.state, PlatformStateContainer
    ):
        return service_or_container.state
    if isinstance(service_or_container, PlatformStateContainer):
        return service_or_container
    raise TypeError(
        f"Expected PlatformStateContainer or object with .state, got {type(service_or_container)}"
    )


__all__ = [
    "BaseStateContainer",
    "PlatformStateContainer",
    "StateStore",
    "get_platform_state",
    "resolve_safe_repo_path",
    "store_cas_artifact",
]
