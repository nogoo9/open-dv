"""`opendv verify` and `axi-dv verify` command handler (6-Gate verification pipeline)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_verify_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute 6-Gate verification in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("verify", args)


__all__ = ["run_verify_command"]
