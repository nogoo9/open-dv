"""`opendv pipeline` command handler (`validate`, `run`, `report`)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_pipeline_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute `opendv pipeline` actions in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("pipeline", args)


__all__ = ["run_pipeline_command"]
