"""Open-DV Standalone In-Process CLI (`opendv` & `axi-dv`) package (Decision D6)."""

from __future__ import annotations

from opendv_cli.executor import LocalExecutor
from opendv_cli.main import app_main, axi_dv_main, build_parser, main

__version__ = "0.1.0"

__all__ = [
    "LocalExecutor",
    "__version__",
    "app_main",
    "axi_dv_main",
    "build_parser",
    "main",
]
