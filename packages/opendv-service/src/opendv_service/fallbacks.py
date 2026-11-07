"""Explicit opt-in fallback registry for OpenDV (REQ-FAIL-01, docs/18 §3B.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FallbackKey(StrEnum):
    """Canonical registry keys for permitted opt-in fallbacks."""

    STAGE_OUTPUT_SYMBOLIC = "stage_output_policy.symbolic_completion"
    HARNESS_RETRY_POLICY = "harness.retry_policy"
    HARNESS_TRANSPORT_REST = "harness.transport.rest"
    CLI_SCHEMA_OMISSION_RETRY = "harness.cli.schema_omission_retry"


@dataclass(frozen=True)
class FallbackDefinition:
    """Specification of an opt-in fallback mechanism."""

    key: FallbackKey
    default_enabled: bool
    description: str
    valid_use_case: str


FALLBACK_REGISTRY: dict[FallbackKey, FallbackDefinition] = {
    FallbackKey.STAGE_OUTPUT_SYMBOLIC: FallbackDefinition(
        key=FallbackKey.STAGE_OUTPUT_SYMBOLIC,
        default_enabled=False,
        description="Substitute deterministic stage output when LLM is unavailable",
        valid_use_case="Offline or degraded-LLM local development and testing",
    ),
    FallbackKey.HARNESS_RETRY_POLICY: FallbackDefinition(
        key=FallbackKey.HARNESS_RETRY_POLICY,
        default_enabled=False,
        description="Bounded prompt-variant retries on structured output syntax failure",
        valid_use_case="Transient API rate-limiting or minor JSON formatting repairs",
    ),
    FallbackKey.HARNESS_TRANSPORT_REST: FallbackDefinition(
        key=FallbackKey.HARNESS_TRANSPORT_REST,
        default_enabled=False,
        description="Explicit REST transport selection instead of native SDK",
        valid_use_case="Environments lacking native SDK bindings",
    ),
    FallbackKey.CLI_SCHEMA_OMISSION_RETRY: FallbackDefinition(
        key=FallbackKey.CLI_SCHEMA_OMISSION_RETRY,
        default_enabled=False,
        description="Retry CLI LLM invocation without --json-schema when CLI rejects schema flag",
        valid_use_case="Experimental or local CLI harnesses with models that reject schema flags",
    ),
}


@dataclass
class FallbackRecord:
    """Audit record capturing an application of an explicit opt-in fallback."""

    key: FallbackKey
    applied_at_utc: str
    stage_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation for logging and serialization."""
        return {
            "key": str(self.key),
            "applied_at_utc": self.applied_at_utc,
            "stage_id": self.stage_id,
            "details": self.details,
        }


def get_fallback_definition(key: FallbackKey | str) -> FallbackDefinition:
    """Retrieve fallback definition by key or raise KeyError if unregistered."""
    try:
        fb_key = FallbackKey(key) if isinstance(key, str) else key
    except ValueError as exc:
        raise KeyError(f"Fallback key '{key}' is not registered in FALLBACK_REGISTRY.") from exc
    if fb_key not in FALLBACK_REGISTRY:
        raise KeyError(f"Fallback key '{key}' is not registered in FALLBACK_REGISTRY.")
    return FALLBACK_REGISTRY[fb_key]


def is_fallback_enabled(key: FallbackKey | str, config: dict[str, Any] | None = None) -> bool:
    """Check if an explicit opt-in fallback is enabled via configuration."""
    defn = get_fallback_definition(key)
    if not config:
        return defn.default_enabled
    fb_cfg = config.get("fallbacks", {})
    return bool(fb_cfg.get(str(defn.key), defn.default_enabled))


__all__ = [
    "FALLBACK_REGISTRY",
    "FallbackDefinition",
    "FallbackKey",
    "FallbackRecord",
    "get_fallback_definition",
    "is_fallback_enabled",
]
