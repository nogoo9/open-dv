"""`opendv run` command handler (manage runs and inspect observability events)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_run_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv run` actions in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("run", args)


__all__ = ["run_run_command"]
