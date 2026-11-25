"""Subprocess standalone isolation test for V2 `opendv` CLI (Decision D6 / T13)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.architecture.conftest import REPO_ROOT


@pytest.mark.architecture
def test_subprocess_standalone_isolation() -> None:
    """Spawn an isolated Python subprocess running `opendv --json doctor` with zero API imports."""
    code = (
        "import sys\n"
        "from opendv.cli.main import main\n"
        "rc = main(['--json', 'doctor'])\n"
        "assert rc == 0, f'doctor returned exit code {rc}'\n"
        "banned = {'fastapi', 'starlette', 'uvicorn', 'opendv.api'}\n"
        "loaded_banned = banned.intersection(sys.modules.keys())\n"
        "assert not loaded_banned, f'Banned API modules loaded in CLI process: {loaded_banned}'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Subprocess standalone isolation failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
