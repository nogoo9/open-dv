"""Open-DV CLI Common Package.

Provides spec-driven argument parser generation, RFC 8785 JCS printing,
output presentation, and command execution abstractions.
"""

from __future__ import annotations

from opendv_cli_common.executor import CommandExecutor, ExecutionResult
from opendv_cli_common.jcs_printer import format_jcs, print_jcs
from opendv_cli_common.parser_builder import build_argument_parser
from opendv_cli_common.presenter import format_presenter_output, present_result
from opendv_cli_common.tree import build_cli_tree, dump_cli_tree

__version__ = "0.1.0"

__all__ = [
    "CommandExecutor",
    "ExecutionResult",
    "__version__",
    "build_argument_parser",
    "build_cli_tree",
    "dump_cli_tree",
    "format_jcs",
    "format_presenter_output",
    "present_result",
    "print_jcs",
]
