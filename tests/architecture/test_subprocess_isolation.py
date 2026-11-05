"""Subprocess standalone isolation test for opendv CLI (Decision D6 / T13)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.architecture.conftest import REPO_ROOT

BANNED_MODULES = {"fastapi", "starlette", "opendv_platform_api", "httpx"}


@pytest.mark.architecture
def test_subprocess_standalone_isolation() -> None:
    """Spawns an isolated Python subprocess running opendv verify to assert zero backend leakage."""
    code = (
        "import sys\n"
        "from opendv_cli.main import app_main\n"
        "rc = app_main([\n"
        "    'verify',\n"
        "    '--contract', 'examples/private_equity/dv_pe_model_contract.yaml',\n"
        "    '--json',\n"
        "])\n"
        "assert rc == 0, f'verify returned exit code {rc}'\n"
        "banned = {'fastapi', 'starlette', 'opendv_platform_api', 'httpx'}\n"
        "loaded_banned = banned.intersection(sys.modules.keys())\n"
        "assert not loaded_banned, f'Banned modules loaded into sys.modules: {loaded_banned}'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Subprocess standalone verification failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
