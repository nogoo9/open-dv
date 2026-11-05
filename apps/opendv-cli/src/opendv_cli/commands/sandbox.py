"""`opendv sandbox` command handler (4-Batch DuckDB Shadow Sandbox & Queries D.10 / D.10b)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_sandbox_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute 4-Batch DuckDB sandbox or bi-temporal queries via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("sandbox", args)


__all__ = ["run_sandbox_command"]
