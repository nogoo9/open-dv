"""Open-DV Unified CLI (`opendv` & `axi-dv`) package."""

from __future__ import annotations

from opendv_cli.api_client import OpenDVCliApiClient, OpenDVCliApiError, validate_api_url
from opendv_cli.main import app_main, axi_dv_main, build_parser, main

__version__ = "0.1.0"

__all__ = [
    "OpenDVCliApiClient",
    "OpenDVCliApiError",
    "__version__",
    "app_main",
    "axi_dv_main",
    "build_parser",
    "main",
    "validate_api_url",
]
