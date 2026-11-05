"""Command execution abstractions for spec-driven Open-DV CLIs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ExecutionResult:
    """Represents the standardized result of a CLI command execution."""

    exit_code: int
    payload: Any
    status: str = "OK"

    @property
    def is_success(self) -> bool:
        """Return True if command finished with a zero exit code."""
        return self.exit_code == 0


@runtime_checkable
class CommandExecutor(Protocol):
    """Protocol for executing spec-driven CLI commands in-process or remotely."""

    def execute(self, command: str, args: argparse.Namespace) -> tuple[int, Any]:
        """Execute a parsed CLI command and return (exit_code, result_payload).

        Args:
            command: Top-level command name (e.g. 'verify', 'profile', 'compile').
            args: Parsed argparse namespace.

        Returns:
            Tuple of integer exit code and command result payload (DTO, dict, or string).
        """
        ...


__all__ = ["CommandExecutor", "ExecutionResult"]
