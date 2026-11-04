"""tests/parity/conftest.py — Pytest fixtures for cross-surface parity testing."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_platform_api import PlatformStateContainer, create_app
from tests.parity.transports import DirectRestTransport, StandaloneCliTransport


@pytest.fixture
def platform_api_state(tmp_path: Path) -> PlatformStateContainer:
    """Create an isolated platform state container with a dedicated temp directory."""
    state_dir = tmp_path / "platform_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return PlatformStateContainer(state_dir=state_dir)


@pytest.fixture
def platform_api_client(
    platform_api_state: PlatformStateContainer,
) -> Generator[TestClient, None, None]:
    """Provide an in-process HTTP client connected to the platform REST/SSE API."""
    app = create_app(platform_state=platform_api_state)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def cli_transport(tmp_path: Path) -> StandaloneCliTransport:
    """Provide an isolated StandaloneCliTransport instance for local CLI execution."""
    return StandaloneCliTransport(workspace_dir=tmp_path / "cli_workspace")


@pytest.fixture
def rest_transport(platform_api_client: TestClient) -> DirectRestTransport:
    """Provide a DirectRestTransport instance bound to the active platform API client."""
    return DirectRestTransport(client=platform_api_client)
