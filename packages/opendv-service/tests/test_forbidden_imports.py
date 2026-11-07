"""AST import analysis to enforce strict architectural boundaries (LAY-01 & Step 3.2).

Ensures opendv-service is a completely headless pure core domain library with:
- Zero imports of FastAPI / Starlette (web layer)
- Zero imports of argparse (CLI layer)
- Zero imports of opendv_cli (CLI layer)
- Zero imports of opendv_platform_api (API app layer)
And apps/opendv-cli:
- Zero imports of opendv_platform_api (LAY-01)
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_SERVICE_MODULES = {
    "fastapi",
    "starlette",
    "argparse",
    "opendv_cli",
    "opendv_platform_api",
}

FORBIDDEN_CLI_MODULES = {
    "opendv_platform_api",
}


def _collect_module_imports(py_file: Path) -> set[str]:
    """Parse a python file with AST and collect all root imported module names."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def test_opendv_service_forbidden_imports() -> None:
    """Verify that packages/opendv-service/src contains zero forbidden imports."""
    service_src = Path(__file__).resolve().parent.parent / "src"
    assert service_src.is_dir(), f"Service src directory not found at {service_src}"

    py_files = list(service_src.rglob("*.py"))
    assert len(py_files) > 0, "No python source files found in opendv-service/src"

    violations: list[str] = []
    for file_path in py_files:
        imports = _collect_module_imports(file_path)
        forbidden_found = imports.intersection(FORBIDDEN_SERVICE_MODULES)
        if forbidden_found:
            violations.append(
                f"{file_path.name} imported forbidden module(s): {sorted(forbidden_found)}"
            )

    assert not violations, "Forbidden imports detected in opendv-service:\n" + "\n".join(violations)


def test_opendv_cli_forbidden_imports() -> None:
    """Verify that apps/opendv-cli/src contains zero imports of opendv_platform_api (LAY-01)."""
    cli_src = Path(__file__).resolve().parent.parent.parent.parent / "apps" / "opendv-cli" / "src"
    assert cli_src.is_dir(), f"CLI src directory not found at {cli_src}"

    py_files = list(cli_src.rglob("*.py"))
    assert len(py_files) > 0, "No python source files found in opendv-cli/src"

    violations: list[str] = []
    for file_path in py_files:
        imports = _collect_module_imports(file_path)
        forbidden_found = imports.intersection(FORBIDDEN_CLI_MODULES)
        if forbidden_found:
            violations.append(
                f"{file_path.name} imported forbidden module(s): {sorted(forbidden_found)}"
            )

    assert not violations, "Forbidden imports detected in opendv-cli:\n" + "\n".join(violations)
