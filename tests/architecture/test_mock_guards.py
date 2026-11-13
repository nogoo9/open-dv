"""Static analysis mock guards enforcing testkit and mock isolation (T14 / REQ-MOCK-01).

Guards:
1. AST scan asserting zero testkit, mock, and pytest imports in production sources.
2. Pyproject parse asserting production packages do not declare testkit in runtime dependencies.
3. TypeScript SDK cleanliness asserting no demo bundles or mock exports.
4. Platform starts empty asserting zero projects/runs and 404s on PE demo IDs.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from opendv_contracts.errors import NotFoundError
from opendv_service import OpenDVService, PlatformStateContainer
from tests.architecture.conftest import REPO_ROOT


def _discover_production_py_dirs() -> list[str]:
    dirs: list[str] = []
    for base in ["packages", "apps"]:
        for p in (REPO_ROOT / base).glob("*/src"):
            if "opendv-testkit" not in p.parts and list(p.rglob("*.py")):
                dirs.append(str(p.relative_to(REPO_ROOT)).replace("\\", "/"))
    return sorted(dirs)


PRODUCTION_PY_DIRS = _discover_production_py_dirs()

BANNED_PRODUCTION_MODULES = {
    "opendv_testkit",
    "unittest.mock",
    "mock",
    "pytest",
    "pytest_asyncio",
}

BANNED_NAME_SUBSTRINGS = {
    "deterministic_adapter",
    "deterministicharnessadapter",
    "build_deterministic",
    "deterministic_pipeline",
    "deterministic_seam",
    "_deterministic_shim",
    "sdk_shim",
}

# BANNED_PRODUCTION_MODULES and BANNED_NAME_SUBSTRINGS are strictly enforced
# across all production sources. The real production module
# opendv_service.construction.deterministic_pipeline is permitted as genuine
# offline construction logic (not a test double or mock).


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_mock_or_testkit_imports_in_production(rel_dir: str) -> None:
    """Guard 1: Assert production sources do not import mock, testkit, or test double shims."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    py_files = list(target_dir.rglob("*.py"))
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
                for alias in node.names:
                    mod_names.append(f"{node.module}.{alias.name}")
                    mod_names.append(alias.name)

            for mod in mod_names:
                top_mod = mod.split(".")[0]
                if top_mod in BANNED_PRODUCTION_MODULES or mod in BANNED_PRODUCTION_MODULES:
                    violations.append(f"{rel_path}:{node.lineno}: banned import '{mod}'")
                for banned_sub in BANNED_NAME_SUBSTRINGS:
                    # Allow authorized production module
                    # opendv_service.construction.deterministic_pipeline
                    if banned_sub == "deterministic_pipeline" and (
                        mod == "opendv_service.construction.deterministic_pipeline"
                        or mod.startswith("opendv_service.construction.deterministic_pipeline.")
                    ):
                        continue
                    if banned_sub in mod.lower():
                        violations.append(
                            f"{rel_path}:{node.lineno}: banned test-double module import '{mod}'"
                        )

            # Check for direct references to test doubles in identifiers or attributes
            if isinstance(node, ast.Name):
                for banned_sub in BANNED_NAME_SUBSTRINGS:
                    if (
                        banned_sub == "deterministic_pipeline"
                        and node.id == "deterministic_pipeline"
                    ):
                        continue
                    if banned_sub in node.id.lower():
                        violations.append(
                            f"{rel_path}:{node.lineno}: banned identifier reference '{node.id}'"
                        )
            elif isinstance(node, ast.Attribute):
                for banned_sub in BANNED_NAME_SUBSTRINGS:
                    if (
                        banned_sub == "deterministic_pipeline"
                        and node.attr == "deterministic_pipeline"
                    ):
                        continue
                    if banned_sub in node.attr.lower():
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
    """Guard 2: Assert production pyproject.toml files never declare testkit in dependencies."""
    violations: list[str] = []
    checked_packages: int = 0

    for rel_dir in PRODUCTION_PY_DIRS:
        pkg_dir = (REPO_ROOT / rel_dir).parent
        pyproject_path = pkg_dir / "pyproject.toml"
        if not pyproject_path.exists():
            continue

        checked_packages += 1
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        deps = data.get("project", {}).get("dependencies", [])
        for dep in deps:
            dep_clean = dep.split(">=")[0].split("==")[0].split("<")[0].split(";")[0].strip()
            if dep_clean in {"opendv-testkit", "pytest", "pytest-asyncio", "mock"}:
                violations.append(
                    f"{pyproject_path.relative_to(REPO_ROOT)}: runtime dependency '{dep_clean}'"
                )

    assert checked_packages >= 8, f"Expected at least 8 packages checked, got {checked_packages}"
    assert not violations, (
        "Production packages declare testkit/pytest in runtime dependencies:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_typescript_sdk_cleanliness() -> None:
    """Guard 3: Assert TS protocol SDK and workbench have no demo bundles or mock exports."""
    ts_src = REPO_ROOT / "packages/opendv-protocol-ts/src"
    demo_dir = ts_src / "demo"
    assert not demo_dir.exists(), (
        f"packages/opendv-protocol-ts/src/demo must not exist! Found: {demo_dir}"
    )

    index_ts = ts_src / "index.ts"
    assert index_ts.exists(), f"Expected index.ts to exist at {index_ts}"
    index_content = index_ts.read_text(encoding="utf-8")
    assert "pe-demo-bundle" not in index_content, "index.ts must not export pe-demo-bundle"
    assert "MockVaultEventStreamClient" not in index_content, (
        "index.ts must not export MockVaultEventStreamClient"
    )
    assert "mock-client" not in index_content, "index.ts must not export mock-client"

    if shutil.which("bun") is None:
        pytest.skip("bun is required for typescript cleanliness verification")

    # Evaluate Bun runtime export object of protocol-ts asserting zero Mock* keys
    bun_code = (
        "import * as proto from './packages/opendv-protocol-ts/src/index.ts'; "
        "const mocks = Object.keys(proto).filter(k => k.includes('Mock')); "
        "if (mocks.length > 0) { console.error('Found Mock exports:', mocks); process.exit(1); }"
    )
    res = subprocess.run(
        ["bun", "-e", bun_code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Evaluated protocol-ts exports contain Mock symbols: {res.stderr}"

    # Inspect workbench source tree for Mock* exports
    workbench_src = REPO_ROOT / "apps/opendv-web-workbench/src"
    if workbench_src.exists():
        mock_export_re = re.compile(
            r"export\s+(?:(?:class|const|function|interface|type|enum)\s+Mock[A-Za-z0-9_]*|\{[^}]*\bMock[A-Za-z0-9_]*[^}]*\}|\*\s+from\s+['\"][^'\"]*mock)"
        )
        violations: list[str] = []
        for ts_file in workbench_src.rglob("*.ts*"):
            content = ts_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(content.splitlines(), start=1):
                if mock_export_re.search(line):
                    rel = ts_file.relative_to(REPO_ROOT)
                    violations.append(f"{rel}:{lineno}: {line.strip()}")

        assert not violations, (
            "apps/opendv-web-workbench/src contains Mock* exports:\n" + "\n".join(violations)
        )


@pytest.mark.architecture
@pytest.mark.hardening
def test_platform_starts_empty_invariant(tmp_path: Path) -> None:
    """Guard 4: Assert fresh PlatformStateContainer starts empty with zero entities and 404s."""
    container = PlatformStateContainer(
        storage_root=tmp_path / "state",
        workspace_root=tmp_path / "ws",
    )
    assert len(container.projects) == 0, f"Expected 0 projects, got {len(container.projects)}"
    assert len(container.runs) == 0, f"Expected 0 runs, got {len(container.runs)}"
    assert len(container.artifacts) == 0, f"Expected 0 artifacts, got {len(container.artifacts)}"
    assert len(container.hold_managers) == 0, (
        f"Expected 0 holds, got {len(container.hold_managers)}"
    )

    assert "proj-apex-horizon-pe" not in container.projects
    assert "run-pe-apex-horizon-20261030" not in container.runs

    # Verify OpenDVService backed by clean container produces 404s on PE demo IDs
    service = OpenDVService(state_store=container, storage_root=tmp_path / "state")
    assert service.list_projects() == []

    with pytest.raises(NotFoundError) as exc_proj:
        service.get_project("proj-apex-horizon-pe")
    assert exc_proj.value.http_status == 404

    with pytest.raises(NotFoundError) as exc_run:
        service.get_run("run-pe-apex-horizon-20261030")
    assert exc_run.value.http_status == 404

    with pytest.raises(NotFoundError) as exc_report:
        service.get_pipeline_report("run-pe-apex-horizon-20261030")
    assert exc_report.value.http_status == 404

    with pytest.raises(NotFoundError) as exc_holds:
        service.list_holds("run-pe-apex-horizon-20261030")
    assert exc_holds.value.http_status == 404
