"""Architecture Gate: CI workflow parity, wheel packaging config, and `.env.example` sync."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.architecture.conftest import REPO_ROOT

CI_YML_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PAGES_YML_PATH = REPO_ROOT / ".github" / "workflows" / "pages.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
SRC_OPENDV_DIR = REPO_ROOT / "src" / "opendv"

_ENV_VAR_RE = re.compile(r"\b(OPENDV_[A-Z0-9_]+)\b")

REQUIRED_ENV_VARS: frozenset[str] = frozenset(
    {
        "OPENDV_HOME",
        "OPENDV_SECRET_PRECEDENCE",
        "OPENDV_WORKSPACE_ROOTS",
        "OPENDV_API_URL",
        "OPENDV_STATIC_DIR",
        "OPENDV_BOUND_HOST",
        "OPENDV_ALLOW_REMOTE_UNAUTHENTICATED",
        "OPENDV_MCP_COORD_TOKEN",
        "OPENDV_MCP_SCOUT_TOKEN",
        "OPENDV_AGY_CLI_BIN",
        "OPENDV_CLAUDE_CLI_BIN",
        "OPENDV_CLAUDE_EFFORT",
        "OPENDV_AGY_HIGH_MODEL",
        "OPENDV_AGY_FAST_MODEL",
        "OPENDV_GEMINI_HIGH_MODEL",
        "OPENDV_GEMINI_FAST_MODEL",
        "OPENDV_CLAUDE_HIGH_MODEL",
        "OPENDV_CLAUDE_FAST_MODEL",
        "OPENDV_FIBO_PACK_PATH",
        "OPENDV_BASELINE_DIGESTS_PATH",
    }
)


@pytest.mark.architecture
def test_ci_workflow_dual_os_and_zero_skip_gates() -> None:
    """Assert `.github/workflows/ci.yml` enforces dual OS, lock check, wheel build, & 0 skips."""
    assert CI_YML_PATH.is_file(), f"Missing {CI_YML_PATH}"
    raw_ci = CI_YML_PATH.read_text(encoding="utf-8")
    parsed: dict[str, Any] = yaml.safe_load(raw_ci)

    job = parsed["jobs"]["validate-v2"]
    matrix_os = job["strategy"]["matrix"]["os"]
    assert set(matrix_os) == {"ubuntu-latest", "windows-latest"}

    steps = job["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    joined = "\n".join(runs)

    assert "uv lock --check" in joined
    assert "uv sync --locked --group dev" in joined
    assert "--extra dev" not in joined
    assert "bun x --cwd web" not in joined
    assert "bun run --cwd web playwright install" in joined
    assert "uv run mypy src tests/helpers" in joined

    web_build_idx = next(
        i for i, cmd in enumerate(runs) if "bun run --cwd web build" in cmd
    )
    wheel_idx = next(
        i for i, cmd in enumerate(runs) if "uv build --wheel" in cmd
    )
    assert web_build_idx < wheel_idx, (
        "`bun run --cwd web build` must execute strictly before `uv build --wheel`"
    )

    wheel_cmd = runs[wheel_idx]
    for required_asset in (
        "opendv/api/static/index.html",
        "opendv/sources/fibo/fibo-core-pack.json",
        "opendv/agents/packs/dv2-modeling-guide.md",
        "opendv/agents/packs/",
        "opendv/schemas/v2/",
        "opendv --help",
        "agents list",
        "model spec",
    ):
        assert required_asset in wheel_cmd, f"Wheel check missing '{required_asset}'"

    pytest_cmds = [
        cmd
        for cmd in runs
        if "pytest" in cmd and '-m "not live_provider"' in cmd
    ]
    assert len(pytest_cmds) == 1
    pytest_cmd = pytest_cmds[0]
    assert '-o addopts=""' in pytest_cmd
    assert "-rs" in pytest_cmd
    assert "skipped" in pytest_cmd, "CI pytest step must fail when any test is skipped"

    assert "bun run --cwd apps/opendv-website build" in joined
    assert "bun test apps/opendv-website/tests" in joined
    assert PAGES_YML_PATH.is_file(), f"Missing {PAGES_YML_PATH}"


@pytest.mark.architecture
def test_pyproject_wheel_packaging_configuration() -> None:
    """Assert `pyproject.toml` ships static UI, FIBO core pack, schemas/v2, and `opendv` CLI."""
    assert PYPROJECT_PATH.is_file(), f"Missing {PYPROJECT_PATH}"
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["opendv"] == "opendv.cli.main:app"
    assert "dev" in data["dependency-groups"]
    assert "dev" not in data["project"].get("optional-dependencies", {})

    wheel_cfg = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel_cfg["packages"] == ["src/opendv"]
    artifacts = set(wheel_cfg["artifacts"])
    assert "src/opendv/api/static/**" in artifacts
    assert "src/opendv/sources/fibo/fibo-core-pack.json" in artifacts
    assert wheel_cfg["force-include"]["schemas/v2"] == "opendv/schemas/v2"

    # Confirm tracked non-generated package inputs exist on disk
    assert (SRC_OPENDV_DIR / "sources" / "fibo" / "fibo-core-pack.json").is_file()
    assert (SRC_OPENDV_DIR / "agents" / "packs" / "dv2-modeling-guide.md").is_file()
    pack_skills = sorted((SRC_OPENDV_DIR / "agents" / "packs").glob("*/SKILL.md"))
    assert len(pack_skills) == 6
    assert (REPO_ROOT / "schemas" / "v2" / "openapi.json").is_file()


@pytest.mark.architecture
def test_env_example_parity_with_src_opendv() -> None:
    """Assert `.env.example` documents all active `OPENDV_*` runtime env vars (`UAT-S2-13`)."""
    assert ENV_EXAMPLE_PATH.is_file(), f"Missing {ENV_EXAMPLE_PATH}"
    env_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "OPENDV_SECRET_PRECEDENCE=store_first" in env_text
    assert "OPENDV_ALLOWED_HOSTS" not in env_text

    documented_vars = set(_ENV_VAR_RE.findall(env_text))
    missing_expected = sorted(REQUIRED_ENV_VARS - documented_vars)
    assert not missing_expected, (
        f".env.example missing expected OPENDV runtime variables: {missing_expected}"
    )

    discovered_src_vars: set[str] = set()
    for py_file in sorted(SRC_OPENDV_DIR.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/opendv/api/static/"):
            continue
        discovered_src_vars.update(_ENV_VAR_RE.findall(py_file.read_text(encoding="utf-8")))

    undocumented = sorted(discovered_src_vars - documented_vars)
    assert not undocumented, (
        f"OPENDV_* variables used in src/opendv/ missing from .env.example: {undocumented}"
    )
