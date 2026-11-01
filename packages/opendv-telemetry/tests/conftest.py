"""Pytest configuration for opendv-telemetry test suite.

Ensures both opendv-telemetry and opendv-core are resolvable in isolated CI runs
without requiring editable installs.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_DIR.parent
WORKSPACE_ROOT = PACKAGE_ROOT.parent.parent

TELEMETRY_SRC = (PACKAGE_ROOT / "src").resolve()
CORE_SRC = (PACKAGE_ROOT.parent / "opendv-core" / "src").resolve()

if str(TELEMETRY_SRC) not in sys.path:
    sys.path.insert(0, str(TELEMETRY_SRC))

if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))
