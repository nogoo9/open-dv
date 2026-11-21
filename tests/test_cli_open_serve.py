"""Phase 1 TDD Tests: Loopback-Only Server Guard (D18 Exit 40), Clean `--open` URL,
`--allow-remote-unauthenticated` Metadata, and Zero-Token `opendvctl` Compatibility.

Maps to requirements:
- FR-AUTH-03: Clean `opendv serve --open` / `opendv-platform --open` URL (no `#token=` fragment)
- FR-AUTH-04 / D18: Refuse non-loopback `--host` with exit code 40 unless `--allow-remote-unauthenticated`
- FR-CLI-04: `serve` supports `--allow-remote-unauthenticated`, loud warning, metadata exposure
- FR-CLI-06: `opendvctl` zero-token operation, legacy `--token`/`OPENDV_TOKEN` no-op, waiver without key
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from opendv_cli import app_main
from opendv_cli.executor import LocalExecutor
from opendv_platform_api import create_app
from opendv_platform_api.cli import main as platform_cli_main
from opendv_service import OpenDVSettings


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def ephemeral_open_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[str, Path], None, None]:
    """Start an isolated open-access FastAPI server on an ephemeral 127.0.0.1 port."""
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    for env_key in ("OPENDV_TOKEN", "OPENDV_STUDIO_TOKEN", "OPENDV_WAIVER_KEY"):
        monkeypatch.delenv(env_key, raising=False)

    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=state_dir, is_server=True)
    app = create_app(settings=settings, workspace_root=ws_root, storage_root=state_dir)
    port = _find_free_port()

    class _UvicornServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = _UvicornServer(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except OSError:
            time.sleep(0.05)

    try:
        yield url, studio_root
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)


def _run_opendvctl(
    repo_root: Path,
    argv: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for env_key in ("OPENDV_TOKEN", "OPENDV_STUDIO_TOKEN", "OPENDV_WAIVER_KEY", "OPENDV_STUDIO_DIR"):
        env.pop(env_key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bun", "run", "src/main.ts", *argv],
        cwd=repo_root / "apps" / "opendvctl",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


# ===========================================================================
# 1. Non-Loopback Refusal (Exit Code 40) — FR-AUTH-04 / D18
# ===========================================================================


@pytest.mark.parametrize("non_loopback_host", ["0.0.0.0", "::", "192.168.1.50"])
def test_opendv_platform_refuses_non_loopback_without_allow_flag_exit_40(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    non_loopback_host: str,
) -> None:
    """`opendv-platform --host <non-loopback>` without `--allow-remote-unauthenticated` exits 40."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    uvicorn_calls: list[tuple[str, int]] = []

    def fail_if_uvicorn_called(app: Any, host: str, port: int) -> None:
        uvicorn_calls.append((host, port))

    monkeypatch.setattr("opendv_platform_api.cli.uvicorn.run", fail_if_uvicorn_called)

    try:
        rc = platform_cli_main(["--workspace", str(ws), "--host", non_loopback_host, "--port", "0"])
    except SystemExit as exc:
        rc = int(exc.code) if isinstance(exc.code, int) else 1

    assert rc == 40
    assert uvicorn_calls == []


@pytest.mark.parametrize("non_loopback_host", ["0.0.0.0", "::", "192.168.1.50"])
def test_opendv_serve_refuses_non_loopback_without_allow_flag_exit_40(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    non_loopback_host: str,
) -> None:
    """`opendv serve --host <non-loopback>` without `--allow-remote-unauthenticated` exits 40."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    uvicorn_calls: list[tuple[str, int]] = []

    def fail_if_uvicorn_called(app: Any, host: str, port: int) -> None:
        uvicorn_calls.append((host, port))

    import opendv_cli.executor as cli_executor

    monkeypatch.setattr(cli_executor, "_load_platform_server_main", lambda: None)
    monkeypatch.setattr("uvicorn.run", fail_if_uvicorn_called)

    executor = LocalExecutor(workspace_root=ws)
    try:
        rc = app_main(
            ["serve", "--host", non_loopback_host, "--port", "0", "--workspace", str(ws)],
            executor=executor,
        )
    except SystemExit as exc:
        rc = int(exc.code) if isinstance(exc.code, int) else 1

    assert rc == 40
    assert uvicorn_calls == []


# ===========================================================================
# 2. `--allow-remote-unauthenticated` Startup, Warning & Metadata
# ===========================================================================


def test_opendv_platform_allow_remote_unauthenticated_starts_warns_and_exposes_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`opendv-platform --host 0.0.0.0 --allow-remote-unauthenticated` starts, warns, exposes flag."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {}

    def fake_uvicorn_run(app: Any, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("opendv_platform_api.cli.uvicorn.run", fake_uvicorn_run)

    rc = platform_cli_main(
        [
            "--workspace",
            str(ws),
            "--host",
            "0.0.0.0",
            "--port",
            "9420",
            "--allow-remote-unauthenticated",
        ]
    )
    assert rc == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9420

    io_out = capsys.readouterr()
    combined = f"{io_out.out}\n{io_out.err}".lower()
    assert "unauthenticated" in combined
    assert "warning" in combined or "remote" in combined

    app = captured["app"]
    redacted_settings = app.state.settings.to_redacted_dict()
    assert redacted_settings["remote_unauthenticated_mode"] is True

    with TestClient(app) as client:
        health_body = client.get("/api/v1/health").json()
        assert (
            health_body.get("remote_unauthenticated_mode") is True
            or health_body.get("settings", {}).get("remote_unauthenticated_mode") is True
        )
        cfg_body = client.get("/api/v1/platform/config").json()
        assert cfg_body.get("remote_unauthenticated_mode") is True

    # Default loopback startup exposes remote_unauthenticated_mode=False
    captured_local: dict[str, Any] = {}

    def fake_local_uvicorn(app: Any, host: str, port: int) -> None:
        captured_local["app"] = app

    monkeypatch.setattr("opendv_platform_api.cli.uvicorn.run", fake_local_uvicorn)
    rc_local = platform_cli_main(["--workspace", str(ws), "--host", "127.0.0.1", "--port", "9421"])
    assert rc_local == 0
    assert captured_local["app"].state.settings.to_redacted_dict()["remote_unauthenticated_mode"] is False


def test_opendv_serve_allow_remote_unauthenticated_starts_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`opendv serve --host 0.0.0.0 --allow-remote-unauthenticated` starts and emits warning."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    captured_argv: list[list[str]] = []

    def fake_platform_main(argv: list[str] | None = None) -> int:
        captured_argv.append(list(argv or []))
        return 0

    import opendv_cli.executor as cli_executor

    monkeypatch.setattr(cli_executor, "_load_platform_server_main", lambda: fake_platform_main)
    executor = LocalExecutor(workspace_root=ws)

    rc = app_main(
        [
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9420",
            "--workspace",
            str(ws),
            "--allow-remote-unauthenticated",
        ],
        executor=executor,
    )
    assert rc == 0
    assert len(captured_argv) == 1
    assert "--allow-remote-unauthenticated" in captured_argv[0]
    io_out = capsys.readouterr()
    combined = f"{io_out.out}\n{io_out.err}".lower()
    assert "unauthenticated" in combined


# ===========================================================================
# 3. Clean `--open` URL (Zero `#token=` Fragment) — FR-AUTH-03
# ===========================================================================


def test_opendv_platform_and_serve_open_clean_url_no_token_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`opendv-platform --open` and `opendv serve --open` emit clean /ontology URL without #token=."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    opened_urls: list[str] = []

    class _FakeTimer:
        def __init__(self, interval: float, function: Any, args: Any = None, kwargs: Any = None) -> None:
            self.daemon = False
            self._fn = function
            self._args = args or ()
            self._kwargs = kwargs or {}

        def start(self) -> None:
            self._fn(*self._args, **self._kwargs)

        def cancel(self) -> None:
            pass

    monkeypatch.setattr("webbrowser.open", lambda url, *a, **kw: opened_urls.append(str(url)) or True)
    monkeypatch.setattr("threading.Timer", _FakeTimer)
    monkeypatch.setattr("opendv_platform_api.cli.uvicorn.run", lambda *a, **kw: None)

    rc_plat = platform_cli_main(["--workspace", str(ws), "--host", "127.0.0.1", "--port", "9421", "--open"])
    assert rc_plat == 0
    assert opened_urls[-1] == "http://127.0.0.1:9421/ontology"
    assert "#token=" not in opened_urls[-1]
    capsys.readouterr()

    import opendv_cli.executor as cli_executor

    monkeypatch.setattr(cli_executor, "_load_platform_server_main", lambda: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)

    executor = LocalExecutor(workspace_root=ws)
    rc_serve = app_main(
        ["serve", "--host", "127.0.0.1", "--port", "9422", "--workspace", str(ws), "--open", "--json"],
        executor=executor,
    )
    assert rc_serve == 0
    serve_io = capsys.readouterr()
    assert "#token=" not in serve_io.out
    assert "#token=" not in serve_io.err
    assert opened_urls[-1] == "http://127.0.0.1:9422/ontology"
    payload = json.loads(serve_io.out)
    if "studio_launch_url" in payload:
        assert payload["studio_launch_url"] == "http://127.0.0.1:9422/ontology"


# ===========================================================================
# 4. `opendvctl` Zero-Token, `--token` No-Op, and Waiver Without Key — FR-CLI-06
# ===========================================================================


def test_opendvctl_zero_token_legacy_token_noop_and_waiver_without_key(
    repo_root: Path,
    ephemeral_open_server: tuple[str, Path],
) -> None:
    """`opendvctl` runs zero-token, treats --token/OPENDV_TOKEN as no-ops, waives without K_waiver."""
    server_url, studio_root = ephemeral_open_server
    sid = "opendvctl-open-01"

    # 1. Zero-token session create (no OPENDV_TOKEN, no .local_token)
    res_create = _run_opendvctl(
        repo_root,
        [
            "ontology",
            "studio",
            "create",
            "-s",
            sid,
            "--domain-namespace",
            "wealth_mgmt",
            "--api-url",
            server_url,
            "--json",
        ],
    )
    assert res_create.returncode == 0, f"stdout={res_create.stdout}\nstderr={res_create.stderr}"
    created = json.loads(res_create.stdout)
    assert created["session_id"] == sid
    assert not (studio_root / ".local_token").exists()

    # 2. Legacy `--token legacy-tok` and `OPENDV_TOKEN=legacy-tok` tolerated as harmless no-ops (not exit 2)
    res_list = _run_opendvctl(
        repo_root,
        [
            "ontology",
            "studio",
            "list",
            "--token",
            "legacy-tok",
            "--api-url",
            server_url,
            "--json",
        ],
        extra_env={"OPENDV_TOKEN": "legacy-tok"},
    )
    assert res_list.returncode == 0, f"stdout={res_list.stdout}\nstderr={res_list.stderr}"
    sessions = json.loads(res_list.stdout)
    assert any(item.get("session_id") == sid for item in sessions)

    # 3. `--low-confidence-waiver` succeeds without OPENDV_WAIVER_KEY or --waiver-key-file
    head_sha = created["head_sha256"]
    prop_resp = httpx.post(
        f"{server_url}/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-ctl-open-1",
            "base_sha256": head_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ctl-1",
                    "concept_uri": "urn:ontology:entity:custom_ctl_fund",
                    "concept_name": "Custom CTL Fund Z88",
                    "definition": "Custom fund concept accepted via opendvctl without waiver key.",
                }
            ],
            "rationale": "Proposal for opendvctl zero-waiver-key verification.",
        },
        timeout=10.0,
    )
    assert prop_resp.status_code == 201, prop_resp.text

    res_accept = _run_opendvctl(
        repo_root,
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-ctl-open-1",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Custodian approved local custom concept without K_waiver key.",
            "--api-url",
            server_url,
            "--json",
        ],
    )
    assert res_accept.returncode == 0, f"stdout={res_accept.stdout}\nstderr={res_accept.stderr}"
    accepted = json.loads(res_accept.stdout)
    assert accepted["applied_operations_count"] == 1
