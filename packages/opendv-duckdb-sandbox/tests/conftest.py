"""Pytest configuration for opendv-duckdb-sandbox test suite.

Ensures opendv-duckdb-sandbox, opendv-axi-verifier, opendv-telemetry, and opendv-core
are resolvable in isolated test runs without requiring editable installs.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_DIR.parent
PACKAGES_DIR = PACKAGE_ROOT.parent

SANDBOX_SRC = (PACKAGE_ROOT / "src").resolve()
VERIFIER_SRC = (PACKAGES_DIR / "opendv-axi-verifier" / "src").resolve()
TELEMETRY_SRC = (PACKAGES_DIR / "opendv-telemetry" / "src").resolve()
CORE_SRC = (PACKAGES_DIR / "opendv-core" / "src").resolve()

for src_path in (SANDBOX_SRC, VERIFIER_SRC, TELEMETRY_SRC, CORE_SRC):
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
