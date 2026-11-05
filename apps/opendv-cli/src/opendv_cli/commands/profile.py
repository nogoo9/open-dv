"""`opendv profile` command handler (Bronze Graph B empirical profiling & discovery)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_profile_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run Bronze source profiling in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("profile", args)


__all__ = ["run_profile_command"]
