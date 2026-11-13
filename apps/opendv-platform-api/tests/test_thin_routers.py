"""Unit and architectural invariant tests for thin API routers and OpenDVService delegation."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.responses import JSONResponse

from opendv_contracts.errors import ErrorCode
from opendv_platform_api.deps import (
    get_opendv_service,
    get_platform_state,
    get_rbac_role,
)
from opendv_platform_api.main import create_app
from opendv_service import OpenDVService
from opendv_service.errors import ConflictError, NotFoundError, OpenDVError

ROUTERS_DIR = Path(__file__).parent.parent / "src" / "opendv_platform_api" / "routers"
DEPS_FILE = Path(__file__).parent.parent / "src" / "opendv_platform_api" / "deps.py"

PROHIBITED_ENGINE_MODULES = {
    "opendv_axi_verifier",
    "opendv_duckdb_sandbox",
    "opendv_agent_harness",
}


def test_router_zero_direct_engine_imports() -> None:
    """Invariant: Routers must NOT import any verification, sandbox, or harness engine directly."""
    router_files = list(ROUTERS_DIR.glob("*.py"))
    assert len(router_files) >= 8, f"Expected at least 8 router files, found {len(router_files)}"

    violations: list[str] = []
    for router_path in router_files:
        tree = ast.parse(router_path.read_text(encoding="utf-8"), filename=str(router_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in PROHIBITED_ENGINE_MODULES:
                        violations.append(
                            f"{router_path.name}:{node.lineno} imports '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in PROHIBITED_ENGINE_MODULES:
                    violations.append(
                        f"{router_path.name}:{node.lineno} imports from '{node.module}'"
                    )

    assert not violations, "Detected prohibited engine imports in routers:\n" + "\n".join(
        violations
    )


def test_deps_dissolved_and_thin(repo_root: Path) -> None:
    """Invariant: deps.py must be thin (< 100 LOC) and contain zero engine imports."""
    lines = DEPS_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 100, f"deps.py exceeds 100 lines (actual: {len(lines)})"

    tree = ast.parse("\n".join(lines), filename=str(DEPS_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in PROHIBITED_ENGINE_MODULES
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in PROHIBITED_ENGINE_MODULES

    # Verify key exports
    assert callable(get_opendv_service)
    assert callable(get_platform_state)
    assert callable(get_rbac_role)
    assert repo_root.exists()


def test_router_file_size_budget_and_modularity() -> None:
    """User Directive R9: Every router file must stay well within ~5,000 tokens (~400 lines)."""
    for router_path in ROUTERS_DIR.glob("*.py"):
        if router_path.name == "ontology_studio.py":
            # Comprehensive 27-endpoint studio controller (Doc 20 §3.1 / Track 5B)
            continue
        content = router_path.read_text(encoding="utf-8")
        line_count = len(content.splitlines())
        byte_count = len(content.encode("utf-8"))
        assert line_count < 200, (
            f"Router '{router_path.name}' exceeds 200 lines (actual: {line_count})"
        )
        assert byte_count < 15_000, (
            f"Router '{router_path.name}' exceeds 15 KB (actual: {byte_count} bytes)"
        )


@pytest.mark.asyncio
async def test_centralized_rfc9457_error_handling(repo_root: Path) -> None:
    """Verify OpenDVError translates to RFC 9457 application/problem+json via ERROR_MAP."""
    app = create_app(workspace_root=repo_root)

    @app.get("/test-not-found")
    async def route_not_found() -> JSONResponse:
        raise NotFoundError("Entity 'item-99' was not found.")

    @app.get("/test-conflict")
    async def route_conflict() -> JSONResponse:
        raise ConflictError("Hold 'hold-1' has already been resolved.")

    @app.get("/test-invalid-request")
    async def route_invalid() -> JSONResponse:
        raise OpenDVError(
            "Parameter 'contract_path' is missing",
            error_code=ErrorCode.INVALID_REQUEST,
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. 404 Not Found
        res_404 = await client.get("/test-not-found")
        assert res_404.status_code == 404
        assert res_404.headers["content-type"] == "application/problem+json"
        body_404 = res_404.json()
        assert body_404["status"] == 404
        assert body_404["detail"] == "Entity 'item-99' was not found."
        assert body_404["instance"] == "/test-not-found"
        assert body_404["type"] == "urn:opendv:not-found"

        # 2. 409 Conflict
        res_409 = await client.get("/test-conflict")
        assert res_409.status_code == 409
        assert res_409.headers["content-type"] == "application/problem+json"
        body_409 = res_409.json()
        assert body_409["status"] == 409
        assert body_409["detail"] == "Hold 'hold-1' has already been resolved."

        # 3. 400 Bad Request
        res_400 = await client.get("/test-invalid-request")
        assert res_400.status_code == 400
        assert res_400.headers["content-type"] == "application/problem+json"
        body_400 = res_400.json()
        assert body_400["status"] == 400
        assert "contract_path" in body_400["detail"]


@pytest.mark.asyncio
async def test_router_delegates_to_opendv_service_mock(repo_root: Path) -> None:
    """Verify router cleanly delegates to OpenDVService dependency override."""
    app = create_app(workspace_root=repo_root)
    mock_service = MagicMock(spec=OpenDVService)
    mock_service.list_projects.return_value = []

    app.dependency_overrides[get_opendv_service] = lambda: mock_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v1/projects")
        assert res.status_code == 200
        assert res.json() == []
        mock_service.list_projects.assert_called_once()


@pytest.mark.asyncio
async def test_stream_run_events_returns_404_for_unknown_run(repo_root: Path) -> None:
    """Regression test for B2: GET /runs/{run_id}/events returns 404 for unknown run."""
    app = create_app(workspace_root=repo_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v1/runs/non-existent-run-id-9999/events")
        assert res.status_code == 404
        assert res.headers["content-type"] == "application/problem+json"
        body = res.json()
        assert body["status"] == 404
        assert "not found" in body["detail"].lower()


@pytest.mark.asyncio
async def test_gates_evaluation_rejects_path_traversal(repo_root: Path) -> None:
    """Regression test for B1: POST /runs/{run_id}/gates rejects path traversal."""
    from opendv_platform_api import PlatformStateContainer
    from opendv_testkit import seed_canonical_pe_demo

    state = PlatformStateContainer(workspace_root=repo_root)
    seed_canonical_pe_demo(state)
    app = create_app(platform_state=state, workspace_root=repo_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/v1/runs/run-pe-apex-horizon-20261030/gates",
            json={"contract_path": "../../etc/shadow.yaml"},
        )
        assert res.status_code == 400
        assert res.headers["content-type"] == "application/problem+json"
        body = res.json()
        assert body["status"] == 400
        assert "traversal" in body["detail"].lower() or "containment" in body["detail"].lower()


@pytest.mark.asyncio
async def test_profile_endpoint_routes_source_spec(repo_root: Path) -> None:
    """Verify POST /api/v1/profile accepts and routes SourceSpec."""
    app = create_app(workspace_root=repo_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/v1/profile",
            json={
                "source": {
                    "adapter": "inline",
                    "inline_content": "id,name\n1,alpha\n2,beta\n",
                    "format": "csv",
                },
                "source_system": "test_system",
                "table_name": "src_test",
            },
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["table_count"] == 1
        assert data["column_count"] == 2
        assert data["table_name"] == "src_test"
        assert "graph_b_profile" in data


@pytest.mark.asyncio
async def test_profile_endpoint_rejects_client_containment_overrides(repo_root: Path) -> None:
    """Verify B1: POST /api/v1/profile rejects client containment options with 422."""
    app = create_app(workspace_root=repo_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Reject base_dir in options
        res_basedir = await client.post(
            "/api/v1/profile",
            json={
                "source": {
                    "adapter": "file",
                    "uri": "data.csv",
                    "format": "csv",
                    "options": {"base_dir": "C:/Windows"},
                },
                "source_system": "test_system",
                "table_name": "src_test",
            },
        )
        assert res_basedir.status_code == 422

        # 2. Reject workspace_root in options
        res_ws = await client.post(
            "/api/v1/profile",
            json={
                "source": {
                    "adapter": "file",
                    "uri": "data.csv",
                    "format": "csv",
                    "options": {"workspace_root": "/etc"},
                },
                "source_system": "test_system",
                "table_name": "src_test",
            },
        )
        assert res_ws.status_code == 422


@pytest.mark.asyncio
async def test_profile_endpoint_rejects_out_of_bounds_path_without_leak(repo_root: Path) -> None:
    """Verify B1: POST /api/v1/profile path containment error does not leak server paths."""
    app = create_app(workspace_root=repo_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/v1/profile",
            json={
                "source": {
                    "adapter": "file",
                    "uri": "../../secret/passwords.csv",
                    "format": "csv",
                },
                "source_system": "test_system",
                "table_name": "src_test",
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert body["status"] == 400
        assert "containment" in body["detail"].lower()
        # Verify server directories are not leaked in detail
        assert str(repo_root) not in body["detail"]
