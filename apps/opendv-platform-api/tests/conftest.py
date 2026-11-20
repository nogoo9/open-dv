"""Test fixtures for opendv-platform-api test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("OPENDV_WORKSPACE_ROOT", str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return workspace repository root path."""
    return REPO_ROOT


@pytest.fixture
def deterministic_pipeline_runner() -> Iterator[None]:
    """Scoped deterministic SDK shim runner for offline pipeline creation tests."""
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline as orig_run_pipeline
    from opendv_service.seams import set_pipeline_runner_override
    from opendv_testkit import build_deterministic_agy_sdk_shim

    def _shim(*r_args: Any, **r_kwargs: Any) -> Any:
        return orig_run_pipeline(
            *r_args,
            **{**r_kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(_shim)
    try:
        yield
    finally:
        set_pipeline_runner_override(None)
