"""Static analysis mock guards enforcing testkit and mock isolation (T14 / REQ-MOCK-01).

Guards:
1. AST scan asserting zero testkit, mock, Fake*, and pytest imports/symbols in `src/opendv/`.
2. Root `pyproject.toml` parse asserting runtime dependencies exclude test/mock libraries.
3. Frontend `web/src/` cleanliness asserting zero mock exports or demo test harnesses.
4. `OpenDVService` starts empty asserting zero projects and 404 `RESOURCE_NOT_FOUND` fail-closed.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from opendv.models import ErrorCode, OpenDVError
from opendv.service import OpenDVService
from tests.architecture.conftest import REPO_ROOT

PRODUCTION_PY_DIRS = ["src/opendv"]

BANNED_PRODUCTION_MODULES = {
    "opendv_" + "testkit",
    "tests.helpers",
    "unittest.mock",
    "mock",
    "pytest",
    "pytest_asyncio",
}

BANNED_PRODUCTION_IDENTIFIERS = {
    "deterministicmockharness",
    "fakeexternalmcpserver",
    "deterministic_adapter",
    "build_deterministic",
    "deterministic_seam",
    "_deterministic_shim",
    "sdk_shim",
}


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_mock_or_testkit_imports_in_production(rel_dir: str) -> None:
    """Guard 1: Assert `src/opendv/` does not import mock, testkit, or test double shims."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.is_dir(), f"Target directory does not exist: {target_dir}"

    py_files = sorted(target_dir.rglob("*.py"))
    assert py_files, f"No Python files found in {target_dir}"

    violations: list[str] = []
    for py_file in py_files:
        rel_path = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            mod_names: list[str] = []
            if isinstance(node, ast.Import):
                mod_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod_names.append(node.module)
                mod_names.extend(f"{node.module}.{alias.name}" for alias in node.names)
                mod_names.extend(alias.name for alias in node.names)

            for mod in mod_names:
                top_mod = mod.split(".")[0]
                if top_mod in BANNED_PRODUCTION_MODULES or mod in BANNED_PRODUCTION_MODULES:
                    violations.append(f"{rel_path}:{node.lineno}: banned import '{mod}'")
                if any(b in mod.lower() for b in BANNED_PRODUCTION_IDENTIFIERS):
                    violations.append(
                        f"{rel_path}:{node.lineno}: banned test-double module import '{mod}'"
                    )

            if isinstance(node, ast.Name) and any(
                b in node.id.lower() for b in BANNED_PRODUCTION_IDENTIFIERS
            ):
                violations.append(
                    f"{rel_path}:{node.lineno}: banned identifier reference '{node.id}'"
                )
            elif isinstance(node, ast.Attribute) and any(
                b in node.attr.lower() for b in BANNED_PRODUCTION_IDENTIFIERS
            ):
                violations.append(
                    f"{rel_path}:{node.lineno}: banned attribute reference '{node.attr}'"
                )

    assert not violations, (
        f"Production source directory '{rel_dir}' contains forbidden mock/testkit references:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_production_pyproject_dependencies_exclude_testkit() -> None:
    """Guard 2: Assert root `pyproject.toml` runtime dependencies exclude test/mock tools."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file(), f"Missing {pyproject_path}"

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    deps: list[str] = list(data.get("project", {}).get("dependencies", []))
    for extra_deps in data.get("project", {}).get("optional-dependencies", {}).values():
        deps.extend(extra_deps)

    banned = {"opendv-testkit", "pytest", "pytest-asyncio", "mock"}
    violations: list[str] = []
    for dep in deps:
        dep_clean = dep.split(">=")[0].split("==")[0].split("<")[0].split(";")[0].strip().lower()
        if dep_clean in banned:
            violations.append(f"pyproject.toml: runtime dependency '{dep_clean}'")

    assert not violations, (
        "Production runtime dependencies include testkit/pytest:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_typescript_sdk_cleanliness() -> None:
    """Guard 3: Assert `web/src/` has no Mock* exports or test-harness references."""
    web_src = REPO_ROOT / "web" / "src"
    assert web_src.is_dir(), f"Missing {web_src}"

    mock_export_re = re.compile(
        r"export\s+(?:(?:class|const|function|interface|type|enum)\s+Mock[A-Za-z0-9_]*|"
        r"\{[^}]*\bMock[A-Za-z0-9_]*[^}]*\}|\*\s+from\s+['\"][^'\"]*mock)"
    )
    banned_tokens = ("DeterministicMockHarness", "FakeExternalMcpServer", "MOCK_HARNESS")
    violations: list[str] = []
    for ts_file in sorted(web_src.rglob("*.ts*")):
        if (
            "tests" in ts_file.parts
            or "e2e" in ts_file.parts
            or ts_file.name == "types.generated.ts"
        ):
            continue
        content = ts_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if mock_export_re.search(line) or any(tok in line for tok in banned_tokens):
                rel = ts_file.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, "web/src contains Mock* exports or test doubles:\n" + "\n".join(
        violations
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_platform_starts_empty_invariant(tmp_path: Path) -> None:
    """Guard 4: Assert fresh OpenDVService starts empty with zero projects and 404 fail-closed."""
    service = OpenDVService(
        workspace_root=tmp_path / "ws",
        state_dir=tmp_path / "state",
    )
    assert service.list_projects() == []

    with pytest.raises(OpenDVError) as exc_proj:
        service.get_project("proj-nonexistent-demo")
    assert exc_proj.value.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert exc_proj.value.http_status == 404
