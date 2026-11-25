"""Verification that V2 API routers are thin adapters delegating to OpenDVService (T13)."""

from __future__ import annotations

import ast

import pytest

from tests.architecture.conftest import REPO_ROOT

ROUTERS_DIR = REPO_ROOT / "src" / "opendv" / "api"

# Engine / domain symbols that must never be called directly inside API route handlers
BANNED_ENGINE_CALLS = {
    "DuckDbProverAdapter",
    "SqlGlotVaultCompiler",
    "run_cegar_vault_build",
}


@pytest.mark.architecture
def test_router_adapter_thinness() -> None:
    """Assert V2 API route modules contain zero direct engine calls and use OpenDVService."""
    assert ROUTERS_DIR.is_dir(), f"API directory not found: {ROUTERS_DIR}"

    router_files = sorted(ROUTERS_DIR.glob("*routes*.py"))
    assert len(router_files) >= 5, f"Expected at least 5 route modules, found {len(router_files)}"

    violations: list[str] = []
    for r_file in router_files:
        text = r_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(r_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name in BANNED_ENGINE_CALLS:
                    rel_path = r_file.relative_to(REPO_ROOT)
                    violations.append(f"{rel_path}:{node.lineno}: direct engine call '{func_name}'")

        assert "get_service" in text or "OpenDVService" in text, (
            f"Router '{r_file.name}' does not inject or reference get_service / OpenDVService"
        )

    assert not violations, "Adapter thinness violations detected:\n" + "\n".join(violations)
