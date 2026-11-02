"""Harness Provider Registry and Factory for the Open-DV Agnostic Agent Harness.

Conforms to docs/09-agnostic-agent-harness-and-sdk-spec.md (§4).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from opendv_agent_harness.adapters.agy_cli_adapter import AgyCliHarnessAdapter
from opendv_agent_harness.adapters.antigravity_adapter import AntigravityHarnessAdapter
from opendv_agent_harness.adapters.claude_adapter import ClaudeAgentHarnessAdapter
from opendv_agent_harness.adapters.deterministic_adapter import DeterministicHarnessAdapter
from opendv_agent_harness.models import HarnessConfig
from opendv_agent_harness.protocol import VaultAgentHarness

HarnessFactory = Callable[..., VaultAgentHarness]

_DEFAULT_REGISTRY: dict[str, HarnessFactory] = {
    "agy": AgyCliHarnessAdapter,
    "agy-cli": AgyCliHarnessAdapter,
    "antigravity": AntigravityHarnessAdapter,
    "claude": ClaudeAgentHarnessAdapter,
    "deterministic": DeterministicHarnessAdapter,
    "deterministic-stub": DeterministicHarnessAdapter,
}

_HARNESS_REGISTRY = _DEFAULT_REGISTRY


def register_harness(provider_name: str, factory: HarnessFactory) -> None:
    """Register a custom `VaultAgentHarness` provider factory by name."""
    normalized = provider_name.strip().lower()
    if not normalized:
        raise ValueError("Harness provider_name must be a non-empty string.")
    _HARNESS_REGISTRY[normalized] = factory


def list_available_harnesses() -> list[str]:
    """Return a sorted list of all registered harness provider identifiers."""
    return sorted(_HARNESS_REGISTRY.keys())


def create_agent_harness(
    config: HarnessConfig,
    *,
    sdk_module: Any | None = None,
) -> VaultAgentHarness:
    """Instantiate a `VaultAgentHarness` adapter matching `config.provider`.

    Dispatches:
    - `'antigravity'` -> `AntigravityHarnessAdapter`
    - `'claude'` -> `ClaudeAgentHarnessAdapter`
    - `'deterministic'` / `'deterministic-stub'` -> `DeterministicHarnessAdapter`
    - Any custom provider registered via `register_harness(...)`
    """
    provider_key = str(config.provider).strip().lower()
    factory = _HARNESS_REGISTRY.get(provider_key)
    if factory is None:
        available = ", ".join(list_available_harnesses())
        raise ValueError(
            f"Unsupported harness provider '{config.provider}'. Available providers: [{available}]."
        )

    sig = inspect.signature(factory)
    accepts_sdk_module = "sdk_module" in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_sdk_module:
        return factory(config, sdk_module=sdk_module)
    return factory(config)


def get_harness_adapter(
    config: HarnessConfig,
    *,
    sdk_module: Any | None = None,
) -> VaultAgentHarness:
    """Alias for `create_agent_harness(config, sdk_module=sdk_module)`."""
    return create_agent_harness(config, sdk_module=sdk_module)


__all__ = [
    "_DEFAULT_REGISTRY",
    "_HARNESS_REGISTRY",
    "HarnessFactory",
    "create_agent_harness",
    "get_harness_adapter",
    "list_available_harnesses",
    "register_harness",
]
