"""Phase 2 Track C Failing-First Tests: Audit #13 Streaming CAS Blob Upload.

Verifies `POST /api/v1/cas/blobs`:
1. Streams `request.stream()` in 64 KiB (65,536-byte) chunks without buffering via `request.body()`.
2. Enforces `MAX_CAS_UPLOAD_BYTES` mid-stream with HTTP 413 before reading remaining chunks.
3. Rejects SHA-256 mismatch with HTTP 400 and leaves zero temp files or CAS entries.
4. Persists validated uploads via `cas_store.put_file()` rather than `cas_store.put()`.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException, Request

from opendv_contracts import RbacRole
from opendv_platform_api.main import create_app
from opendv_platform_api.routers import cas as cas_router
from opendv_service import OpenDVSettings

CHUNK_64K = 65_536


def _build_app(tmp_path: Path):
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    return create_app(settings=settings, workspace_root=ws, storage_root=state_dir)


def _temp_files_in_cas(cas_dir: Path) -> list[Path]:
    return [
        p
        for p in cas_dir.iterdir()
        if p.is_file() and not cas_router.HEX_64_RE.match(p.name)
    ]


def test_cas_router_ast_avoids_request_body_and_uses_64kib_stream() -> None:
    """Audit #13 static guard: upload_cas_blob uses request.stream(), not request.body()."""
    cas_path = Path(cas_router.__file__)
    source = cas_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(cas_path))

    upload_fn = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "upload_cas_blob"
        ),
        None,
    )
    assert upload_fn is not None, "upload_cas_blob function missing from routers/cas.py"

    attr_calls: list[str] = []
    for node in ast.walk(upload_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            attr_calls.append(f"{node.func.value.id}.{node.func.attr}")

    assert "request.body" not in attr_calls, (
        "Audit #13 violation: upload_cas_blob must not call request.body()"
    )
    assert "request.stream" in attr_calls, (
        "Audit #13 violation: upload_cas_blob must stream via request.stream()"
    )
    assert "65536" in source or "64 * 1024" in source or "CHUNK" in source, (
        "Audit #13 violation: routers/cas.py should define/use 64 KiB streaming chunks"
    )


@pytest.mark.asyncio
async def test_cas_upload_streams_without_request_body_and_calls_put_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/v1/cas/blobs streams chunks, never calls request.body(), and calls put_file()."""
    app = _build_app(tmp_path)
    cas_store = app.state.service.state.cas_store

    async def fail_request_body(self: Request) -> bytes:
        raise AssertionError("upload_cas_blob buffered payload via request.body()")

    monkeypatch.setattr(Request, "body", fail_request_body)

    put_calls: list[int] = []
    put_file_calls: list[Path] = []
    orig_put = cas_store.put
    orig_put_file = cas_store.put_file

    def spy_put(content: Any) -> str:
        put_calls.append(len(content))
        return orig_put(content)

    def spy_put_file(file_path: Path | str) -> str:
        resolved = Path(file_path)
        assert resolved.is_file(), f"put_file called with missing file: {resolved}"
        put_file_calls.append(resolved)
        return orig_put_file(resolved)

    monkeypatch.setattr(cas_store, "put", spy_put)
    monkeypatch.setattr(cas_store, "put_file", spy_put_file)

    payload_chunks = [bytes([0x41 + i]) * CHUNK_64K for i in range(3)] + [b"TAIL_BYTES_1234"]
    full_payload = b"".join(payload_chunks)
    expected_sha = hashlib.sha256(full_payload).hexdigest().lower()

    async def stream_gen() -> AsyncIterator[bytes]:
        for ch in payload_chunks:
            yield ch

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post(
            "/api/v1/cas/blobs",
            content=stream_gen(),
            headers={
                "Content-Type": "application/octet-stream",
                "X-CAS-SHA256": expected_sha,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data == {
            "sha256": expected_sha,
            "size_bytes": len(full_payload),
            "uri": f"cas://{expected_sha}",
        }
        assert len(put_file_calls) == 1, (
            f"Expected cas_store.put_file() to be called once, got {len(put_file_calls)}"
        )
        assert put_calls == [], "cas_store.put() must not be called when put_file() is available"
        assert _temp_files_in_cas(cas_store.base_dir) == []

        get_resp = await client.get(f"/api/v1/cas/blobs/{expected_sha}")
        assert get_resp.status_code == 200
        assert get_resp.content == full_payload


@pytest.mark.asyncio
async def test_cas_upload_enforces_max_bytes_413_mid_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exceeding MAX_CAS_UPLOAD_BYTES raises 413 mid-stream without consuming trailing chunks."""
    app = _build_app(tmp_path)
    cas_store = app.state.service.state.cas_store
    monkeypatch.setattr(cas_router, "MAX_CAS_UPLOAD_BYTES", 2 * CHUNK_64K)

    put_file_calls: list[Path] = []
    monkeypatch.setattr(cas_store, "put_file", lambda p: put_file_calls.append(Path(p)) or "")

    # 1. HTTP end-to-end chunked upload (no Content-Length header) returns 413
    async def oversized_stream() -> AsyncIterator[bytes]:
        for i in range(4):
            yield bytes([0x50 + i]) * CHUNK_64K

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post(
            "/api/v1/cas/blobs",
            content=oversized_stream(),
            headers={"Content-Type": "application/octet-stream"},
        )
    assert resp.status_code == 413, resp.text
    assert put_file_calls == []
    assert _temp_files_in_cas(cas_store.base_dir) == []

    # 2. Direct handler invocation proves mid-stream abort BEFORE reading chunks 4 and 5
    consumed_chunks = 0

    class _FakeStreamingRequest:
        async def body(self) -> bytes:
            raise AssertionError("upload_cas_blob must not call request.body()")

        async def stream(self) -> AsyncIterator[bytes]:
            nonlocal consumed_chunks
            for i in range(5):
                consumed_chunks += 1
                yield bytes([0x60 + i]) * CHUNK_64K

    with pytest.raises(HTTPException) as exc_info:
        await cas_router.upload_cas_blob(
            request=_FakeStreamingRequest(),  # type: ignore[arg-type]
            service=app.state.service,
            _role=RbacRole.INTERNAL_MODELER,
            x_cas_sha256=None,
        )
    assert exc_info.value.status_code == 413
    assert consumed_chunks == 3, (
        f"Expected mid-stream abort on chunk 3 (> 128 KiB), consumed {consumed_chunks} chunks"
    )
    assert _temp_files_in_cas(cas_store.base_dir) == []


@pytest.mark.asyncio
async def test_cas_upload_sha256_mismatch_returns_400_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mismatched X-CAS-SHA256 returns 400 and never commits to CAS or leaks temp files."""
    app = _build_app(tmp_path)
    cas_store = app.state.service.state.cas_store

    async def fail_request_body(self: Request) -> bytes:
        raise AssertionError("upload_cas_blob must not call request.body()")

    monkeypatch.setattr(Request, "body", fail_request_body)

    committed: list[str] = []
    monkeypatch.setattr(cas_store, "put", lambda b: committed.append("put") or "")
    monkeypatch.setattr(cas_store, "put_file", lambda p: committed.append("put_file") or "")

    async def mismatched_stream() -> AsyncIterator[bytes]:
        yield b"M" * CHUNK_64K
        yield b"N" * 1024

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        resp = await client.post(
            "/api/v1/cas/blobs",
            content=mismatched_stream(),
            headers={
                "Content-Type": "application/octet-stream",
                "X-CAS-SHA256": "0" * 64,
            },
        )
    assert resp.status_code == 400, resp.text
    assert committed == []
    assert _temp_files_in_cas(cas_store.base_dir) == []
