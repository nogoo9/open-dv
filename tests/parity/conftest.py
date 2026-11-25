"""Pytest fixtures and workspace isolation for Open-DV V2 parity tests (`tests/parity/`)."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv.api.server import create_app
from opendv.service import OpenDVService

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_parity_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Isolate `OPENDV_HOME`, workspace roots, and non-live CLI binary guards per parity test."""
    home_dir = tmp_path / ".opendv"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(home_dir))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", f"{tmp_path}{os.pathsep}{REPO_ROOT}")
    if (
        request.node.get_closest_marker("live_provider") is None
        and "OPENDV_AGY_CLI_BIN" not in os.environ
    ):
        monkeypatch.setenv("OPENDV_AGY_CLI_BIN", "disabled")


@pytest.fixture
def parity_service(tmp_path: Path) -> OpenDVService:
    """Create an isolated V2 OpenDVService bound to tmp_path state."""
    return OpenDVService(
        workspace_root=REPO_ROOT,
        state_dir=tmp_path / "parity_state",
    )


@pytest.fixture
def parity_api_client(parity_service: OpenDVService) -> Generator[TestClient, None, None]:
    """Provide an in-process FastAPI TestClient over V2 OpenDVService."""
    app = create_app(service=parity_service, workspace_root=REPO_ROOT)
    with TestClient(app) as client:
        yield client
