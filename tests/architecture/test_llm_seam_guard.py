"""LLM seam guard asserting V2 agent runners are tested via injected AgentHarnessPort seams (T13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

V2_RUNNER_PATTERNS = (
    "cli_subprocess_base",
    "subprocess_runner",
    "sdk_runner",
    "cegar_runner",
    "AgentHarnessPort",
)


@pytest.mark.architecture
def test_no_monkeypatched_llm_seams() -> None:
    """Assert zero monkeypatching of V2 agent runner seams across V2 tests and src/opendv."""
    violations: list[str] = []
    scan_dirs = [
        REPO_ROOT / "src" / "opendv",
        REPO_ROOT / "tests" / "agents",
        REPO_ROOT / "tests" / "grounding",
        REPO_ROOT / "tests" / "service",
        REPO_ROOT / "tests" / "api",
        REPO_ROOT / "tests" / "cli",
        REPO_ROOT / "tests" / "parity",
    ]
    current_file = Path(__file__).resolve()
    for base in scan_dirs:
        if not base.is_dir():
            continue
        for py_file in base.rglob("*.py"):
            if py_file.resolve() == current_file:
                continue
            text = py_file.read_text(encoding="utf-8")
            if "monkeypatch" in text and any(pat in text for pat in V2_RUNNER_PATTERNS):
                # Allowlist monkeypatching of environment variables only
                for lineno, line in enumerate(text.splitlines(), 1):
                    if "monkeypatch.setattr" in line and any(
                        pat in line for pat in V2_RUNNER_PATTERNS
                    ):
                        rel_path = py_file.relative_to(REPO_ROOT).as_posix()
                        violations.append(f"{rel_path}:{lineno}: {line.strip()}")

    assert not violations, (
        "Monkeypatched V2 LLM seams detected! Inject `harness=DeterministicMockHarness(...)` "
        "through AgentHarnessPort instead:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
