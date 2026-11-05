"""AST assertion that exactly one path containment resolver definition exists (DUP-04 / T13)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

RESOLVER_FUNCTION_NAMES = {
    "resolve_workspace_path",
    "resolve_repo_path",
    "resolve_safe_repo_path",
    "_resolve_and_check_safe_path",
}


@pytest.mark.architecture
def test_single_path_resolver_definition() -> None:
    """Assert that exactly 1 definition of workspace path resolver exists in the codebase."""
    resolver_defs: list[tuple[Path, int, str]] = []

    scan_dirs = [REPO_ROOT / "packages", REPO_ROOT / "apps"]
    for scan_dir in scan_dirs:
        for py_file in scan_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except Exception as exc:
                pytest.fail(f"AST parsing failed for {py_file}: {exc}")

            for node in ast.walk(tree):
                # Search for top-level or method function definitions with candidate resolver names
                if isinstance(node, ast.FunctionDef) and node.name in RESOLVER_FUNCTION_NAMES:
                    rel_path = py_file.relative_to(REPO_ROOT)
                    resolver_defs.append((rel_path, node.lineno, node.name))

    assert len(resolver_defs) == 1, (
        f"Expected exactly 1 path resolver definition, found {len(resolver_defs)}:\n"
        + "\n".join(f"  {path}:{lineno} (def {name})" for path, lineno, name in resolver_defs)
    )

    file_rel, _lineno, func_name = resolver_defs[0]
    assert func_name == "resolve_workspace_path", (
        f"Expected canonical resolver 'resolve_workspace_path', found '{func_name}'"
    )

    # Invariant: Canonical definition must reside strictly in
    # packages/opendv-core/src/opendv_core/paths.py
    normalized_path = str(file_rel).replace("\\", "/")
    assert normalized_path == "packages/opendv-core/src/opendv_core/paths.py", (
        f"Canonical path resolver defined in unexpected location: {normalized_path}"
    )
