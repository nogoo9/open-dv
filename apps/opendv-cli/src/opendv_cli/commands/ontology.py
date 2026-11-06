"""`opendv ontology` and `axi-dv ontology` command handler.

Manages Graph A evolution, proposals, and deltas.
"""

from __future__ import annotations

import argparse
from typing import Any

from opendv_cli.executor import LocalExecutor


def run_ontology_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute ontology commands in-process via LocalExecutor."""
    exec_inst = executor or LocalExecutor()
    return exec_inst.execute("ontology", args)


__all__ = ["run_ontology_command"]
