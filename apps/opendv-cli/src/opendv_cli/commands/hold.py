"""`opendv hold` command handler (list and resolve Steward Captain-Holds)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_hold_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """List or resolve Captain-Holds in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("hold", args)


__all__ = ["run_hold_command"]
