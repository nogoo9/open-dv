"""Concrete SDK Adapters for the Open-DV Agnostic Agent Harness.

Provides:
- `AgyCliHarnessAdapter`: Antigravity Coding CLI (`agy` / `agy-cli`)
- `AntigravityHarnessAdapter`: Google Antigravity SDK (`google.antigravity`)
- `ClaudeAgentHarnessAdapter`: Anthropic Claude Agent SDK (`claude_agent_sdk`)
- `DeterministicHarnessAdapter`: Internal test-only deterministic mock adapter
"""

from __future__ import annotations

from opendv_agent_harness.adapters.agy_cli_adapter import (
    AgyCliHarnessAdapter,
    AntigravityCliAdapter,
)
from opendv_agent_harness.adapters.antigravity_adapter import (
    DEFAULT_AGY_MODEL_MAP,
    AntigravityHarnessAdapter,
)
from opendv_agent_harness.adapters.claude_adapter import (
    DEFAULT_CLAUDE_MODEL_MAP,
    ClaudeAgentHarnessAdapter,
)
from opendv_agent_harness.adapters.deterministic_adapter import (
    DEFAULT_DETERMINISTIC_MODEL_MAP,
    DeterministicHarnessAdapter,
)

__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "DEFAULT_CLAUDE_MODEL_MAP",
    "DEFAULT_DETERMINISTIC_MODEL_MAP",
    "AgyCliHarnessAdapter",
    "AntigravityCliAdapter",
    "AntigravityHarnessAdapter",
    "ClaudeAgentHarnessAdapter",
    "DeterministicHarnessAdapter",
]
