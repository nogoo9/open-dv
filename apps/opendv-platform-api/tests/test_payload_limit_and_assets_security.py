"""Security tests for 413 PayloadTooLargeError non-exposure and /assets/ containment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from opendv_contracts.errors import PayloadTooLargeError
from opendv_platform_api import create_app


def test_payload_too_large_does_not_leak_arbitrary_exception_message(tmp_path: Path) -> None:
    """exception_handlers must never expose raw PayloadTooLargeError text in HTTP 413 responses."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(workspace_root=ws_root, state_dir=tmp_path / "state")

    secret_msg = "SECRET_INTERNAL_TRACE /srv/app/secret.py"

    @app.get("/api/v1/test-ptl")
    async def _raise_ptl_api() -> None:
        raise PayloadTooLargeError(secret_msg)

    @app.get("/api/v1/ontology/studio/test-ptl")
    async def _raise_ptl_studio() -> None:
        raise PayloadTooLargeError(secret_msg)

    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000") as client:
        for path in ("/api/v1/test-ptl", "/api/v1/ontology/studio/test-ptl"):
            resp = client.get(path)
            assert resp.status_code == 413, resp.text
            assert "exceeds limit" in resp.json().get("detail", "")
            assert "SECRET_INTERNAL_TRACE" not in resp.text
            assert "/srv/app/secret.py" not in resp.text


def test_static_assets_rejects_symlinks_and_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /assets/* must return 404 for symlinks escaping assets/ and absolute paths."""
    static_dir = tmp_path / "static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    (assets_dir / "legit.js").write_text("console.log('ok');", encoding="utf-8")

    outside_secret = tmp_path / "outside_secret.js"
    outside_secret.write_text("secret_key = 'xyz';", encoding="utf-8")

    evil_link = assets_dir / "evil.js"
    try:
        os.symlink(outside_secret, evil_link)
        has_symlink = True
    except OSError:
        has_symlink = False
        evil_link.write_text("secret_key = 'xyz';", encoding="utf-8")

    monkeypatch.setenv("OPENDV_STATIC_DIR", str(static_dir))
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(workspace_root=ws_root, state_dir=tmp_path / "state")

    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000") as client:
        assert client.get("/assets/legit.js").status_code == 200
        if not has_symlink:
            orig_realpath = os.path.realpath

            def _fake_realpath(p: str | os.PathLike[str], *, strict: bool = False) -> str:
                s = str(p)
                if "evil.js" in s:
                    return str(outside_secret)
                return orig_realpath(p, strict=strict)

            monkeypatch.setattr(os.path, "realpath", _fake_realpath)
        assert client.get("/assets/evil.js").status_code == 404
        assert client.get("/assets//etc/passwd").status_code == 404


def test_payload_too_large_via_http_exception_context_without_from_does_not_leak(
    tmp_path: Path,
) -> None:
    """HTTPException raised in except PayloadTooLargeError without `from exc` must not leak."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(workspace_root=ws_root, state_dir=tmp_path / "state")

    secret_msg = "SECRET_INTERNAL_TRACE /srv/app/secret.py"

    @app.get("/api/v1/test-ptl-ctx")
    async def _raise_ptl_ctx_api() -> None:
        try:
            raise PayloadTooLargeError(secret_msg)
        except PayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc))  # noqa: B904

    @app.get("/api/v1/ontology/studio/test-ptl-ctx")
    async def _raise_ptl_ctx_studio() -> None:
        try:
            raise PayloadTooLargeError(secret_msg)
        except PayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc))  # noqa: B904

    with TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000") as client:
        for path in ("/api/v1/test-ptl-ctx", "/api/v1/ontology/studio/test-ptl-ctx"):
            resp = client.get(path)
            assert resp.status_code == 413, resp.text
            assert "exceeds limit" in resp.json().get("detail", "")
            assert "SECRET_INTERNAL_TRACE" not in resp.text
            assert "/srv/app/secret.py" not in resp.text

