"""Zero code clone detection test wrapping pylint duplicate-code (T13)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.architecture.conftest import REPO_ROOT

TARGET_DIRS = [
    "apps/opendv-cli/src",
    "apps/opendv-platform-api/src",
    "packages/opendv-service/src",
    "packages/opendv-contracts/src",
]


@pytest.mark.architecture
@pytest.mark.slow
def test_zero_code_clones() -> None:
    """Assert zero duplicate code clones (>= 8 lines) across core packages and apps."""
    cmd = [
        sys.executable,
        "-m",
        "pylint",
        "--disable=all",
        "--enable=duplicate-code",
        "--min-similarity-lines=8",
        *[str(REPO_ROOT / d) for d in TARGET_DIRS],
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert res.returncode == 0, (
        f"Duplicate code clones detected by pylint (threshold: 8 lines):\n"
        f"stdout:\n{res.stdout}\n"
        f"stderr:\n{res.stderr}"
    )
