"""Architecture & contract isolation tests.

Asserts zero coupling from packages/opendv-contracts to engine packages,
services, CLI, FastAPI, Starlette, or external execution layers.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_ROOT_MODULES = {
    "opendv_core",
    "opendv_telemetry",
    "opendv_axi_verifier",
    "opendv_duckdb_sandbox",
    "opendv_agent_harness",
    "opendv_service",
    "opendv_platform_api",
    "opendv_cli",
    "fastapi",
    "starlette",
    "click",
    "duckdb",
    "sqlglot",
    "networkx",
}


def test_zero_forbidden_imports_in_contracts() -> None:
    src_dir = Path(__file__).resolve().parent.parent / "src"
    assert src_dir.is_dir(), f"src directory not found at {src_dir}"

    violations: list[str] = []

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in FORBIDDEN_ROOT_MODULES:
                        violations.append(f"{py_file.name}:{node.lineno} imports '{alias.name}'")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in FORBIDDEN_ROOT_MODULES:
                    violations.append(f"{py_file.name}:{node.lineno} imports from '{node.module}'")

    assert not violations, (
        "Contract isolation violated! opendv-contracts must have zero "
        "engine/platform dependencies:\n" + "\n".join(violations)
    )


def test_dependencies_in_pyproject_are_minimal() -> None:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")

    # Only pydantic and jsonschema allowed in dependencies list
    assert "pydantic" in content
    assert "jsonschema" in content
    for forbidden in ["fastapi", "starlette", "duckdb", "click", "sqlglot", "networkx"]:
        assert forbidden not in content, (
            f"Forbidden dependency '{forbidden}' found in contracts pyproject.toml"
        )
