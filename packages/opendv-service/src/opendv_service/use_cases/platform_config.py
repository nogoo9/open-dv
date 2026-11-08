"""Platform & Agent Harness Configuration Use Cases."""

from __future__ import annotations

from typing import Any

from opendv_contracts.dtos.platform_config import (
    PlatformConfigResponse,
    PlatformConfigUpdateRequest,
    PlatformConfigValidateRequest,
    PlatformConfigValidationResponse,
)
from opendv_service.state import StateStore


def execute_get_platform_config(state: StateStore) -> PlatformConfigResponse:
    """Retrieve active Platform & Agent Harness configuration and provider readiness."""
    raw = state.get_platform_config()
    return PlatformConfigResponse.model_validate(raw)


def execute_update_platform_config(
    state: StateStore,
    request: PlatformConfigUpdateRequest | dict[str, Any],
) -> PlatformConfigResponse:
    """Update and optionally persist Platform & Agent Harness configuration."""
    raw = state.update_platform_config(request)
    return PlatformConfigResponse.model_validate(raw)


def execute_validate_platform_config(
    state: StateStore,
    request: PlatformConfigValidateRequest | dict[str, Any],
) -> PlatformConfigValidationResponse:
    """Deterministically validate candidate Platform & Agent Harness configuration."""
    raw = state.validate_platform_config(request)
    return PlatformConfigValidationResponse.model_validate(raw)


__all__ = [
    "execute_get_platform_config",
    "execute_update_platform_config",
    "execute_validate_platform_config",
]
