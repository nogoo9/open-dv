"""Concrete SDK Adapters for the Open-DV Agnostic Agent Harness.

Provides:
- `AgyCliHarnessAdapter`: Antigravity Coding CLI (`agy` / `agy-cli`)
- `ClaudeCliHarnessAdapter`: Claude Code CLI (`claude-cli` / `claude-code`)
- `AntigravityHarnessAdapter`: Google Antigravity SDK (`google.antigravity`)
- `ClaudeAgentHarnessAdapter`: Anthropic Claude Agent SDK (`claude_agent_sdk`)
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
from opendv_agent_harness.adapters.claude_cli_adapter import (
    ClaudeCliHarnessAdapter,
    ClaudeCodeCliAdapter,
)

__all__ = [
    "DEFAULT_AGY_MODEL_MAP",
    "DEFAULT_CLAUDE_MODEL_MAP",
    "AgyCliHarnessAdapter",
    "AntigravityCliAdapter",
    "AntigravityHarnessAdapter",
    "ClaudeAgentHarnessAdapter",
    "ClaudeCliHarnessAdapter",
    "ClaudeCodeCliAdapter",
]
