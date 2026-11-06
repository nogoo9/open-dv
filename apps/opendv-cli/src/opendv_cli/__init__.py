"""Open-DV Standalone In-Process CLI (`opendv` & `axi-dv`) package."""

from __future__ import annotations

from typing import Any

from opendv_cli.executor import LocalExecutor
from opendv_cli.main import app_main, axi_dv_main, build_parser, main

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    if name == "RemoteExecutor":
        from opendv_cli.remote_executor import RemoteExecutor

        return RemoteExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LocalExecutor",
    "RemoteExecutor",
    "__version__",
    "app_main",
    "axi_dv_main",
    "build_parser",
    "main",
]
