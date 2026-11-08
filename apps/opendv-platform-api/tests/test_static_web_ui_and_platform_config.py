"""Integration tests for static Web UI serving, SPA routes, platform config API, and CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_platform_api.cli import build_parser, main
from opendv_platform_api.main import create_app
from opendv_platform_api.routers.health import WORKBENCH_CSP


def test_fallback_web_ui_shell_and_spa_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify fallback HTML shell and CSP header when compiled static bundle is absent."""
    empty_static = tmp_path / "empty_static"
    empty_static.mkdir()
    monkeypatch.setenv("OPENDV_STATIC_DIR", str(empty_static))

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(workspace_root=ws_root, state_dir=tmp_path / "state")

    with TestClient(app) as client:
        for route in ("/", "/index.html", "/builder", "/settings", "/stages/1", "/stages/7"):
            res = client.get(route)
            assert res.status_code == 200
            assert "text/html" in res.headers["content-type"]
            assert res.headers.get("Content-Security-Policy") == WORKBENCH_CSP
            assert "Open-DV Platform Workbench" in res.text


def test_compiled_static_bundle_and_asset_path_containment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify serving compiled static bundle, MIME types, and strict path traversal defense."""
    static_dir = tmp_path / "dist"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)

    custom_html = (
        "<!doctype html><html><body><div id='app'>Compiled Workbench v2</div></body></html>"
    )
    (static_dir / "index.html").write_text(custom_html, encoding="utf-8")
    (assets_dir / "index-abc123.js").write_text("console.log('opendv');", encoding="utf-8")
    (assets_dir / "style-abc123.css").write_text("body { margin: 0; }", encoding="utf-8")
    (assets_dir / "icon.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets_dir / "index-abc123.js.map").write_text('{"version":3}', encoding="utf-8")
    (tmp_path / "outside_secret.txt").write_text("TOP_SECRET", encoding="utf-8")

    monkeypatch.setenv("OPENDV_STATIC_DIR", str(static_dir))

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(workspace_root=ws_root, state_dir=tmp_path / "state")

    with TestClient(app) as client:
        for route in ("/", "/index.html", "/builder", "/settings", "/stages/3"):
            res = client.get(route)
            assert res.status_code == 200
            assert "Compiled Workbench v2" in res.text
            assert res.headers.get("Content-Security-Policy") == WORKBENCH_CSP

        res_js = client.get("/assets/index-abc123.js")
        assert res_js.status_code == 200
        assert "application/javascript" in res_js.headers["content-type"]
        assert "console.log('opendv');" in res_js.text

        res_css = client.get("/assets/style-abc123.css")
        assert res_css.status_code == 200
        assert "text/css" in res_css.headers["content-type"]

        res_svg = client.get("/assets/icon.svg")
        assert res_svg.status_code == 200
        assert "image/svg+xml" in res_svg.headers["content-type"]

        res_map = client.get("/assets/index-abc123.js.map")
        assert res_map.status_code == 200
        assert "application/json" in res_map.headers["content-type"]

        # Missing asset returns 404
        res_missing = client.get("/assets/does-not-exist.js")
        assert res_missing.status_code == 404

        # Path traversal attempts are strictly rejected with 404
        res_trav = client.get("/assets/../../outside_secret.txt")
        assert res_trav.status_code == 404

        # Platform config reports web_ui_bundled = True
        res_cfg = client.get("/api/v1/platform/config")
        assert res_cfg.status_code == 200
        assert res_cfg.json()["web_ui_bundled"] is True


def test_platform_config_rest_endpoints_and_rbac(tmp_path: Path) -> None:
    """Verify GET/PUT /api/v1/platform/config and POST /api/v1/platform/config/validate."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    state_dir = tmp_path / "state"
    app = create_app(workspace_root=ws_root, state_dir=state_dir)

    with TestClient(app) as client:
        # 1. GET /api/v1/platform/config
        res_get = client.get("/api/v1/platform/config")
        assert res_get.status_code == 200
        body = res_get.json()
        assert body["harness"]["provider"] == "agy"
        assert len(body["providers"]) == 6
        assert "vault-first-mate" in body["resolved_persona_models"]

        # 2. POST /api/v1/platform/config/validate (valid & invalid)
        res_val_ok = client.post(
            "/api/v1/platform/config/validate",
            json={
                "harness": {
                    "provider": "claude-cli",
                    "steward_hold_policy": "pause",
                    "allow_cli_schema_omission_retry": True,
                }
            },
        )
        assert res_val_ok.status_code == 200
        assert res_val_ok.json()["passed"] is True
        assert res_val_ok.json()["harness"]["provider"] == "claude-cli"

        res_val_bad = client.post(
            "/api/v1/platform/config/validate",
            json={"harness": {"provider": "invalid-provider"}},
        )
        assert res_val_bad.status_code == 200
        assert res_val_bad.json()["passed"] is False
        assert len(res_val_bad.json()["diagnostics"]) >= 1

        # 3. PUT /api/v1/platform/config RBAC enforcement: PUBLIC & AUDITOR_FINANCE -> 403
        for forbidden_role in ("PUBLIC", "AUDITOR_FINANCE"):
            res_forbidden = client.put(
                "/api/v1/platform/config",
                headers={"X-OpenDV-Role": forbidden_role},
                json={"harness": {"provider": "claude-cli"}},
            )
            assert res_forbidden.status_code == 403
            assert "application/problem+json" in res_forbidden.headers["content-type"]

        # 4. PUT /api/v1/platform/config succeeds with DATA_STEWARD clearance
        res_put = client.put(
            "/api/v1/platform/config",
            headers={"X-OpenDV-Role": "DATA_STEWARD"},
            json={
                "harness": {
                    "provider": "claude-cli",
                    "steward_hold_policy": "pause",
                    "ontology_policy": "recommend",
                    "allow_cli_schema_omission_retry": True,
                },
                "persist": True,
            },
        )
        assert res_put.status_code == 200
        updated = res_put.json()
        assert updated["harness"]["provider"] == "claude-cli"
        assert updated["harness"]["steward_hold_policy"] == "pause"
        assert updated["harness"]["ontology_policy"] == "recommend"
        assert updated["harness"]["allow_cli_schema_omission_retry"] is True
        assert (state_dir / "platform_config.json").is_file()

        # 5. PUT /api/v1/platform/config with invalid harness returns 422 ProblemDetail
        res_bad_put = client.put(
            "/api/v1/platform/config",
            json={"harness": {"provider": "bad-provider"}},
        )
        assert res_bad_put.status_code == 422
        assert "application/problem+json" in res_bad_put.headers["content-type"]


def test_opendv_platform_cli_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `opendv-platform` CLI parser and uvicorn launch wiring."""
    captured: dict[str, Any] = {}

    def fake_uvicorn_run(app: Any, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("opendv_platform_api.cli.uvicorn.run", fake_uvicorn_run)

    parser = build_parser()
    parsed = parser.parse_args(["--host", "0.0.0.0", "--port", "9001", "--open"])
    assert parsed.host == "0.0.0.0"
    assert parsed.port == 9001
    assert parsed.open is True

    rc = main(["--workspace", str(tmp_path), "--host", "127.0.0.1", "--port", "8499"])
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8499
    assert captured["app"] is not None
