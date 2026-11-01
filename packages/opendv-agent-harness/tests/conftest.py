"""Pytest configuration for opendv-agent-harness test suite.

Ensures opendv-agent-harness, opendv-core, and opendv-telemetry are resolvable
in isolated CI runs without requiring editable installs.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_DIR.parent
PACKAGES_DIR = PACKAGE_ROOT.parent

for pkg_name in (
    "opendv-agent-harness",
    "opendv-telemetry",
    "opendv-core",
    "opendv-axi-verifier",
    "opendv-duckdb-sandbox",
):
    src_dir = (PACKAGES_DIR / pkg_name / "src").resolve()
    if src_dir.is_dir() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
