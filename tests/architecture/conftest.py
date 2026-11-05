"""Architecture test fixtures and workspace repository root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendv_core import find_workspace_root

REPO_ROOT = find_workspace_root()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return workspace repository root path."""
    return REPO_ROOT
