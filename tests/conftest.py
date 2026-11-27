"""Shared pytest fixtures for Open-DV V2 verification suites."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import duckdb
import keyring
import pytest

from tests.helpers.secret_backends import InMemoryKeyringBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCHEMAS_V2_DIR = REPO_ROOT / "schemas" / "v2"
DATA_PUBLIC_AM_DIR = REPO_ROOT / "data" / "public_am"
DOCS_DIR = REPO_ROOT / "docs"
VECTORS_DIR = REPO_ROOT / "vectors"

if SRC_DIR.is_dir() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the Open-DV repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def schemas_v2_dir() -> Path:
    """Return the canonical `schemas/v2/` directory."""
    return SCHEMAS_V2_DIR


@pytest.fixture(scope="session")
def public_am_dir() -> Path:
    """Return the checked-in `data/public_am/` asset management fixtures directory."""
    return DATA_PUBLIC_AM_DIR


@pytest.fixture
def isolated_opendv_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Bind isolated temporary OPENDV_HOME and workspace trust state for a test."""
    home_dir = tmp_path / ".opendv"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(home_dir))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", f"{tmp_path}{os.pathsep}{REPO_ROOT}")
    return home_dir


@pytest.fixture
def duckdb_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Provide an isolated ephemeral in-memory DuckDB connection per test."""
    con = duckdb.connect(":memory:")
    try:
        yield con
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _isolate_host_keyring(monkeypatch: pytest.MonkeyPatch) -> InMemoryKeyringBackend:
    """Isolate every test from real OS keychains and prevent child subprocess keyring writes."""
    mem_kr = InMemoryKeyringBackend()
    monkeypatch.setattr(keyring, "get_keyring", lambda: mem_kr)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    return mem_kr
