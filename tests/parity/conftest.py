"""tests/parity/conftest.py — Pytest fixtures for cross-surface parity testing."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from starlette.testclient import TestClient

from opendv_platform_api import PlatformStateContainer, create_app
from tests.parity.transports import (
    ClientCliTransport,
    DirectRestTransport,
    StandaloneCliTransport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def platform_api_state(tmp_path: Path) -> PlatformStateContainer:
    """Create an isolated platform state container with repository workspace
    and isolated state directories.
    """
    state_dir = tmp_path / "platform_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return PlatformStateContainer(workspace_root=REPO_ROOT, storage_root=state_dir)


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


@pytest.fixture
def live_server_url(platform_api_state: PlatformStateContainer) -> Generator[str, None, None]:
    """Run an ephemeral live Uvicorn server in a background thread for subprocess client testing."""
    app = create_app(platform_state=platform_api_state)
    port = _find_free_port()

    class _UvicornServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = _UvicornServer(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.05)

    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)


@pytest.fixture
def client_cli_transport(tmp_path: Path, live_server_url: str) -> ClientCliTransport:
    """Provide an isolated ClientCliTransport executing against live Uvicorn server."""
    return ClientCliTransport(
        workspace_dir=tmp_path / "client_cli_ws",
        server_url=live_server_url,
    )
