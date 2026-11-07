"""Explicit opt-in fallback registry for OpenDV (REQ-FAIL-01, docs/18 §3B.4).

Re-exports canonical definitions from opendv_contracts.fallbacks for service-layer access.
"""

from __future__ import annotations

from opendv_contracts.fallbacks import (
    FALLBACK_REGISTRY,
    FallbackDefinition,
    FallbackKey,
    FallbackRecord,
    get_fallback_definition,
    is_fallback_enabled,
)

__all__ = [
    "FALLBACK_REGISTRY",
    "FallbackDefinition",
    "FallbackKey",
    "FallbackRecord",
    "get_fallback_definition",
    "is_fallback_enabled",
]
