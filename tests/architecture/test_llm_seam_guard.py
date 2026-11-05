"""LLM seam guard test asserting zero monkeypatching of
run_agentic_vault_pipeline (LAY-02 / T13).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT


@pytest.mark.architecture
def test_no_monkeypatched_llm_seams() -> None:
    """Assert zero monkeypatching of run_agentic_vault_pipeline across tests and apps."""
    violations: list[str] = []
    test_dirs = [REPO_ROOT / "tests", REPO_ROOT / "apps"]

    current_file = Path(__file__).resolve()
    for d in test_dirs:
        for py_file in d.rglob("*.py"):
            if py_file.resolve() == current_file:
                continue
            text = py_file.read_text(encoding="utf-8")
            if "run_agentic_vault_pipeline" in text and "monkeypatch" in text:
                rel_path = py_file.relative_to(REPO_ROOT)
                violations.append(str(rel_path))

    assert not violations, (
        "Monkeypatched LLM seams detected! Tests must use explicit seam "
        "injection ('set_pipeline_runner_override') rather than monkeypatching:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
