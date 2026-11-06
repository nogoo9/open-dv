"""AST scan of module imports enforcing strict layered boundaries (T13)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

BANNED_IMPORTS: dict[str, set[str]] = {
    # apps/opendv-cli must never import web frameworks or platform API
    "apps/opendv-cli/src": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "httpx",
    },
    # apps/opendv-cli/tests must never import platform API or web frameworks
    "apps/opendv-cli/tests": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "httpx",
    },
    # apps/opendv-platform-api must never import direct engine packages or CLI
    "apps/opendv-platform-api/src": {
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_cli",
    },
    # packages/opendv-service must be headless (zero web framework or CLI imports)
    "packages/opendv-service/src": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "opendv_cli",
    },
    # packages/opendv-contracts must be language-neutral and dependency-free
    "packages/opendv-contracts/src": {
        "opendv_service",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_core",
    },
    # packages/opendv-cli-common must be clean of services, platforms, and engines
    "packages/opendv-cli-common/src": {
        "opendv_service",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_core",
        "fastapi",
        "starlette",
        "httpx",
    },
    # packages/opendv-core must not import from higher layers
    "packages/opendv-core/src": {
        "opendv_service",
        "opendv_contracts",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
    },
}


def get_imports(file_path: Path) -> list[tuple[int, str]]:
    """Extract all import module names with line numbers from a Python file via AST,
    including static imports and dynamic importlib / dunder calls.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Call) and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id in ("import_module", "__import__")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
        ):
            imports.append((node.lineno, node.args[0].value))
    return imports


@pytest.mark.architecture
@pytest.mark.parametrize("target_rel_dir,banned", BANNED_IMPORTS.items())
def test_import_contracts(target_rel_dir: str, banned: set[str]) -> None:
    """Verify that packages and apps observe strict dependency layering boundaries."""
    target_dir = REPO_ROOT / target_rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    py_files = list(target_dir.rglob("*.py"))
    assert py_files, f"No Python files found in {target_dir}"

    violations: list[str] = []
    for py_file in py_files:
        for lineno, mod in get_imports(py_file):
            top_mod = mod.split(".")[0]
            if top_mod in banned or mod in banned:
                rel_path = py_file.relative_to(REPO_ROOT)
                violations.append(f"{rel_path}:{lineno}: banned import '{mod}'")

    assert not violations, (
        f"Layering contract violations detected in '{target_rel_dir}':\n" + "\n".join(violations)
    )
