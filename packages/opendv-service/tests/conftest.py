"""Test fixtures for opendv-service test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("OPENDV_WORKSPACE_ROOT", str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return workspace repository root path."""
    return REPO_ROOT
