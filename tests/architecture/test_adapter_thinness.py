"""Verification that API routers and CLI commands are thin adapters over OpenDVService (T13)."""

from __future__ import annotations

import ast

import pytest

from tests.architecture.conftest import REPO_ROOT

ROUTERS_DIR = REPO_ROOT / "apps" / "opendv-platform-api" / "src" / "opendv_platform_api" / "routers"

# Engine symbols that must never be called directly in API router adapter modules
BANNED_ENGINE_CALLS = {
    "run_axi_dv_verification_pipeline",
    "run_agentic_vault_pipeline",
    "build_gate_scorecard_from_axi_result",
}


@pytest.mark.architecture
def test_router_adapter_thinness() -> None:
    """Assert that API router files contain zero direct engine calls
    and delegate directly to OpenDVService.
    """
    assert ROUTERS_DIR.is_dir(), f"Routers directory not found: {ROUTERS_DIR}"

    router_files = [f for f in ROUTERS_DIR.glob("*.py") if f.name != "__init__.py"]
    assert len(router_files) >= 7, f"Expected at least 7 router modules, found {len(router_files)}"

    violations: list[str] = []
    for r_file in router_files:
        tree = ast.parse(r_file.read_text(encoding="utf-8"), filename=str(r_file))

        # 1. Assert no direct engine calls
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

        # 2. Assert router depends on OpenDVService
        text = r_file.read_text(encoding="utf-8")
        assert "OpenDVService" in text or "get_opendv_service" in text, (
            f"Router '{r_file.name}' does not inject or reference OpenDVService"
        )

    assert not violations, (
        "Adapter thinness violations detected:\n" + "\n".join(violations)
    )
