"""`opendv compile` command handler (Canonical IR & 4-Zone DuckDB SQL compiler)."""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_compile_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Compile DV 2.0 contract into Canonical IR & 4-Zone SQL via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("compile", args)


__all__ = ["run_compile_command"]
