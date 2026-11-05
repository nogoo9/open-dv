"""Architecture test fixtures and workspace repository root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return workspace repository root path."""
    return REPO_ROOT
