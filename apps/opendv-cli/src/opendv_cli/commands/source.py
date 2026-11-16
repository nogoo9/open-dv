"""`opendv source` command handler (`inspect`)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_source_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv source` actions in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("source", args)


__all__ = ["run_source_command"]
