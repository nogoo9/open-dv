"""Phase 1 Layer 3 tests: probes, is_auth_error, credential scrubbing, capture, and DI runners."""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from opendv.agents import (
    AgentTurnRequest,
    SdkHarnessRunner,
    SubprocessHarnessRunner,
    Tier2ProbeOutcome,
    build_isolated_child_env,
    close_sdk_client,
    default_model_options,
    is_auth_error,
    probe_agy,
    probe_claude_cli,
    probe_claude_sdk,
    probe_gemini_sdk,
    resolve_cli_binary,
    run_cli_subprocess_capture,
)
from opendv.agents._pii_redact import redact_telemetry_text
from opendv.models.errors import ErrorCode, OpenDVError


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int | None = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        pid: int = 0,
        hang_seconds: float = 0.0,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.pid = pid
        self._hang_seconds = hang_seconds
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang_seconds > 0:
            await asyncio.sleep(self._hang_seconds)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


def test_default_model_options_offline_catalog() -> None:
    """default_model_options returns deterministic Frontier + Worker ModelOption lists."""
    for provider in ("agy", "claude-cli", "claude", "gemini"):
        opts = default_model_options(provider)  # type: ignore[arg-type]
        assert len(opts) == 2
        assert all(opt.source == "default" for opt in opts)
        assert "Frontier Critic Default" in opts[0].label
        assert "Standard Worker Default" in opts[1].label


def test_is_auth_error_exhaustive_status_and_markers() -> None:
    """is_auth_error enforces terminal HTTP status rules, AuthenticationError, and 16 markers."""
    class _HttpError(Exception):
        def __init__(self, msg: str, *, status_code: int | None = None, code: int | None = None) -> None:
            super().__init__(msg)
            self.status_code = status_code
            self.code = code

    class AuthenticationError(Exception):
        pass

    class _ResponseHolderError(Exception):
        def __init__(self, msg: str, code: int) -> None:
            super().__init__(msg)
            self.response = type("_R", (), {"status_code": code})()

    # Terminal HTTP status codes override text markers
    assert is_auth_error(_HttpError("rate limit", status_code=401), provider="claude") is True
    assert is_auth_error(_ResponseHolderError("bad", 401), provider="agy") is True
    assert is_auth_error(_HttpError("not logged in", status_code=429), provider="claude") is False
    assert is_auth_error(_HttpError("not logged in", status_code=403), provider="claude") is False
    assert is_auth_error(_HttpError("forbidden", status_code=403), provider="gemini") is True
    assert is_auth_error(_HttpError("API_KEY_INVALID", code=400), provider="gemini") is True
    assert is_auth_error(_HttpError("API key not valid", code=400), provider="gemini") is True
    assert is_auth_error(_HttpError("bad schema", code=400), provider="gemini") is False
    assert is_auth_error(_HttpError("API_KEY_INVALID", code=400), provider="claude") is False
    assert is_auth_error("unauthorized", provider="claude-cli", status_code=503) is False
    assert is_auth_error("any msg", provider="claude-cli", status_code=401) is True

    # Non-HTTP errno ints are ignored -> fall through to class / markers
    assert is_auth_error(_HttpError("please run /login", code=2), provider="claude-cli") is True
    assert is_auth_error(AuthenticationError("bad creds"), provider="claude") is True

    for marker in (
        "not logged in",
        "not authenticated",
        "unauthenticated",
        "please run /login",
        "invalid x-api-key",
        "invalid api key",
        "api_key_invalid",
        "api key not valid",
        "authentication_error",
        "authentication failed",
        "unauthorized",
        "login required",
        "credentials expired",
        "token expired",
        "invalid_grant",
        "session expired",
    ):
        assert is_auth_error(f"Error: {marker}!", provider="agy") is True

    assert is_auth_error("bare auth mention without marker", provider="agy") is False
    assert is_auth_error("bare api key prose", provider="claude") is False


def test_credential_redaction_and_prose_preservation() -> None:
    """Credential scrubbing redacts tokens/headers/query/JSON secrets without clobbering prose."""
    dapi_tok = "dapi" + "0123456789abcdef" * 2
    secret_samples = [
        ("token sk-ant-api03-1234567890abcdef", "sk-ant-api03-1234567890abcdef"),
        ("token sk-1234567890abcdef1234", "sk-1234567890abcdef1234"),
        (f"db {dapi_tok}", dapi_tok),
        ("gem AIzaSyD1234567890abcdefghijklm", "AIzaSyD1234567890abcdefghijklm"),
        ("oauth ya29.a0AfH6SMC1234567890_abc", "ya29.a0AfH6SMC1234567890_abc"),
        ("Authorization: Bearer tok_12345678.abc", "tok_12345678.abc"),
        (r'{\"Authorization\": \"Bearer my.jwt.token_99\"}', "my.jwt.token_99"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("x-api-key: supersecretkey123", "supersecretkey123"),
        (r'\"x-goog-api-key\":\"googsecret12345\"', "googsecret12345"),
        ("https://example.com/v1/models?key=querysecret1234&foo=1", "querysecret1234"),
        ("https://example.com/v1?a=1&key=querysecret5678#frag", "querysecret5678"),
        ('{"api_key":"jsonsecretvalue123"}', "jsonsecretvalue123"),
    ]
    for raw, leaked in secret_samples:
        scrubbed = redact_telemetry_text(raw)
        assert leaked not in scrubbed, f"Secret leaked in {raw!r} -> {scrubbed!r}"
        assert "[REDACTED]" in scrubbed
        assert "_REDACTED_" not in scrubbed

    prose_cases = (
        "Invalid Bearer credential.",
        "Missing Bearer authorization token.",
        "Bearer authentication failed",
        "Bearer non-empty token required",
        "not_api_key=1234567890",
    )
    for prose in prose_cases:
        assert redact_telemetry_text(prose) == prose


@pytest.mark.asyncio
async def test_run_cli_subprocess_capture_success_timeout_and_oserror(tmp_path: Path) -> None:
    """run_cli_subprocess_capture captures stdio, reaps hung processes, and propagates raw TimeoutError/OSError."""
    rc, out, err = await run_cli_subprocess_capture(
        [sys.executable, "-c", "import sys; print('ok'); sys.stderr.write('warn')"],
        env=build_isolated_child_env(provider="claude-cli", step="step6"),
        cwd=tmp_path / "cap_ok",
        timeout_seconds=5.0,
    )
    assert rc == 0
    assert out.strip() == "ok"
    assert err.strip() == "warn"

    hung_proc = _FakeProcess(returncode=None, hang_seconds=5.0, pid=0)

    async def fake_exec_hang(*_args: Any, **_kwargs: Any) -> Any:
        return hung_proc

    with pytest.raises(TimeoutError):
        await run_cli_subprocess_capture(
            ["claude", "auth", "status"],
            env={},
            cwd=tmp_path / "cap_hang",
            timeout_seconds=0.05,
            subprocess_exec_fn=fake_exec_hang,
        )
    assert hung_proc.killed is True

    async def fake_exec_missing(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("no such binary")

    with pytest.raises(FileNotFoundError):
        await run_cli_subprocess_capture(
            ["missing-bin"],
            env={},
            cwd=tmp_path / "cap_err",
            timeout_seconds=1.0,
            subprocess_exec_fn=fake_exec_missing,
        )


@pytest.mark.asyncio
async def test_sdk_probes_and_close_sdk_client() -> None:
    """probe_claude_sdk and probe_gemini_sdk classify auth/transient/driver errors and close safely."""
    closed_events: list[str] = []

    class _FakeSdkMod:
        class AuthenticationError(Exception):
            pass

        class APIError(Exception):
            def __init__(self, msg: str, status_code: int | None = None) -> None:
                super().__init__(msg)
                self.status_code = status_code

        class AsyncAnthropic:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.models = type(
                    "_M",
                    (),
                    {"list": self._list},
                )()

            async def _list(self, **_kw: Any) -> list[str]:
                return ["claude-sonnet-4-6"]

            async def close(self) -> None:
                closed_events.append("claude_close")
                raise httpx.ConnectError("close failed")

        class Client:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.aio = type(
                    "_Aio",
                    (),
                    {
                        "models": type("_M", (), {"list": self._list})(),
                        "aclose": self._aclose,
                    },
                )()

            async def _list(self, **_kw: Any) -> list[str]:
                return ["gemini-2.5-flash"]

            async def _aclose(self) -> None:
                closed_events.append("gemini_aclose")
                raise RuntimeError("aio close error")

            def close(self) -> None:
                closed_events.append("gemini_close")

    # Verified via real module_loader path (exercises build_sdk_client & close_sdk_client)
    res_c = await probe_claude_sdk(
        api_key="sk-ant-valid-1234567890",
        parent_env={"ANTHROPIC_BASE_URL": "https://proxy.anthropic.example"},
        module_loader=lambda _n: _FakeSdkMod,
    )
    assert res_c == Tier2ProbeOutcome(kind="verified")
    assert "claude_close" in closed_events

    res_g = await probe_gemini_sdk(
        api_key="AIzaValidKey1234567890abcdefghij",
        module_loader=lambda _n: _FakeSdkMod,
    )
    assert res_g == Tier2ProbeOutcome(kind="verified")
    assert "gemini_aclose" in closed_events
    assert "gemini_close" in closed_events

    # DRIVER_MISSING propagates directly from module_loader
    with pytest.raises(OpenDVError) as exc_drv:
        await probe_claude_sdk(
            api_key="sk-ant-1234567890abcdef",
            parent_env={},
            module_loader=lambda n: (_ for _ in ()).throw(ModuleNotFoundError(n)),
        )
    assert exc_drv.value.error_code == ErrorCode.DRIVER_MISSING

    # Auth failed & transient error classification via sdk_client_factory
    def auth_fail_factory(prov: str, _mod: Any, _k: str) -> Any:
        err = (
            _FakeSdkMod.AuthenticationError("invalid sk-ant-secret1234567890")
            if prov == "claude"
            else _FakeSdkMod.APIError("API_KEY_INVALID AIzaSecretKey1234567890abc", status_code=400)
        )

        async def _raise(*_a: Any, **_kw: Any) -> Any:
            raise err

        return (
            type("_C", (), {"models": type("_M", (), {"list": _raise})()})()
            if prov == "claude"
            else type("_G", (), {"aio": type("_A", (), {"models": type("_M", (), {"list": _raise})()})()})()
        )

    c_auth = await probe_claude_sdk(
        api_key="sk-ant-bad",
        parent_env={},
        sdk_client_factory=auth_fail_factory,
        module_loader=lambda _n: _FakeSdkMod,
    )
    assert c_auth.kind == "auth_failed"
    assert "sk-ant-secret" not in (c_auth.error_summary or "")

    g_auth = await probe_gemini_sdk(
        api_key="AIzaBad",
        sdk_client_factory=auth_fail_factory,
        module_loader=lambda _n: _FakeSdkMod,
    )
    assert g_auth.kind == "auth_failed"
    assert "AIzaSecret" not in (g_auth.error_summary or "")

    def transient_factory(_prov: str, _mod: Any, _k: str) -> Any:
        async def _raise(*_a: Any, **_kw: Any) -> Any:
            raise httpx.ReadTimeout("timed out")

        return type("_G", (), {"aio": type("_A", (), {"models": type("_M", (), {"list": _raise})()})()})()

    g_trans = await probe_gemini_sdk(
        api_key="AIzaOk",
        sdk_client_factory=transient_factory,
    )
    assert g_trans.kind == "transient_error"

    def runtime_err_factory(prov: str, _mod: Any, _k: str) -> Any:
        async def _raise(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("sdk runtime failure sk-ant-secret1234567890")

        return (
            type("_C", (), {"models": type("_M", (), {"list": _raise})()})()
            if prov == "claude"
            else type("_G", (), {"aio": type("_A", (), {"models": type("_M", (), {"list": _raise})()})()})()
        )

    c_rt = await probe_claude_sdk(
        api_key="sk-ant-ok",
        parent_env={},
        sdk_client_factory=runtime_err_factory,
    )
    assert c_rt.kind == "transient_error"
    assert "sk-ant-secret" not in (c_rt.error_summary or "")

    g_rt = await probe_gemini_sdk(
        api_key="AIzaOk",
        sdk_client_factory=runtime_err_factory,
    )
    assert g_rt.kind == "transient_error"
    assert "sk-ant-secret" not in (g_rt.error_summary or "")

    def timeout_factory(prov: str, _mod: Any, _k: str) -> Any:
        async def _hang(*_a: Any, **_kw: Any) -> Any:
            await asyncio.sleep(2.0)

        return (
            type("_C", (), {"models": type("_M", (), {"list": _hang})()})()
            if prov == "claude"
            else type("_G", (), {"aio": type("_A", (), {"models": type("_M", (), {"list": _hang})()})()})()
        )

    c_to = await probe_claude_sdk(
        api_key="sk-ant-ok",
        parent_env={},
        timeout_seconds=0.02,
        sdk_client_factory=timeout_factory,
    )
    assert c_to == Tier2ProbeOutcome(kind="transient_error", error_summary="Probe timed out.")

    g_to = await probe_gemini_sdk(
        api_key="AIzaOk",
        timeout_seconds=0.02,
        sdk_client_factory=timeout_factory,
    )
    assert g_to == Tier2ProbeOutcome(kind="transient_error", error_summary="Probe timed out.")

    # Explicit close_sdk_client suppression check
    await close_sdk_client(_FakeSdkMod.Client(), used_aio=True)


@pytest.mark.asyncio
async def test_probe_claude_cli_api_key_and_oauth_paths() -> None:
    """probe_claude_cli covers /v1/models?limit=1 HTTP responses and claude auth status --json."""
    seen_urls: list[str] = []

    def make_transport(status: int, body: str = "") -> httpx.MockTransport:
        def _handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            return httpx.Response(status, text=body)

        return httpx.MockTransport(_handler)

    ok_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-1234567890abcdef",
        parent_env={"ANTHROPIC_BASE_URL": "https://proxy.example/v1/"},
        transport=make_transport(200, '{"data":[]}'),
    )
    assert ok_res == Tier2ProbeOutcome(kind="verified")
    assert seen_urls[-1] == "https://proxy.example/v1/models?limit=1"

    auth_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-bad1234567890abc",
        parent_env={},
        transport=make_transport(401, "invalid key sk-ant-bad1234567890abc"),
    )
    assert auth_res.kind == "auth_failed"
    assert "sk-ant-bad" not in (auth_res.error_summary or "")

    for code in (302, 403, 429, 500):
        tr_res = await probe_claude_cli(
            binary_path="claude",
            api_key="sk-ant-1234567890abcdef",
            parent_env={},
            transport=make_transport(code, "upstream"),
        )
        assert tr_res.kind == "transient_error"

    def err_transport(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    net_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-1234567890abcdef",
        parent_env={},
        transport=httpx.MockTransport(err_transport),
    )
    assert net_res.kind == "transient_error"

    # Pure synchronous httpx.BaseTransport subclass (not MockTransport)
    class _SyncOnlyStream(httpx.SyncByteStream):
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __iter__(self) -> Any:
            yield self._payload

    class _SyncOnlyTransport(httpx.BaseTransport):
        def __init__(
            self,
            status: int,
            body: bytes,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            self._status = status
            self._body = body
            self._headers = headers
            self.closed = False

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            _ = request.read()
            return httpx.Response(
                self._status,
                headers=self._headers,
                stream=_SyncOnlyStream(self._body),
            )

        def close(self) -> None:
            self.closed = True

    sync_ok = _SyncOnlyTransport(200, b'{"data":[]}')
    assert not isinstance(sync_ok, httpx.AsyncBaseTransport)
    sync_ok_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-1234567890abcdef",
        parent_env={},
        transport=sync_ok,
    )
    assert sync_ok_res == Tier2ProbeOutcome(kind="verified")
    assert sync_ok.closed is True

    sync_401 = _SyncOnlyTransport(401, b"invalid sk-ant-secret1234567890")
    sync_401_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-bad1234567890abc",
        parent_env={},
        transport=sync_401,
    )
    assert sync_401_res.kind == "auth_failed"
    assert "sk-ant-secret" not in (sync_401_res.error_summary or "")
    assert sync_401.closed is True

    sync_gz_ok = _SyncOnlyTransport(
        200,
        gzip.compress(b'{"data":[]}'),
        headers={"Content-Encoding": "gzip"},
    )
    sync_gz_ok_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-1234567890abcdef",
        parent_env={},
        transport=sync_gz_ok,
    )
    assert sync_gz_ok_res == Tier2ProbeOutcome(kind="verified")
    assert sync_gz_ok.closed is True

    sync_gz_401 = _SyncOnlyTransport(
        401,
        gzip.compress(b"invalid sk-ant-secret1234567890"),
        headers={"Content-Encoding": "gzip"},
    )
    sync_gz_401_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-bad1234567890abc",
        parent_env={},
        transport=sync_gz_401,
    )
    assert sync_gz_401_res.kind == "auth_failed"
    assert "invalid [REDACTED]" in (sync_gz_401_res.error_summary or "")
    assert "sk-ant-secret" not in (sync_gz_401_res.error_summary or "")
    assert sync_gz_401.closed is True

    class _SyncPreReadGzipTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            _ = request.read()
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(b'{"data":[]}'),
            )

    assert (
        await probe_claude_cli(
            binary_path="claude",
            api_key="sk-ant-1234567890abcdef",
            parent_env={},
            transport=_SyncPreReadGzipTransport(),
        )
    ) == Tier2ProbeOutcome(kind="verified")

    def timeout_transport(_req: httpx.Request) -> httpx.Response:
        raise TimeoutError

    http_to_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-1234567890abcdef",
        parent_env={},
        transport=httpx.MockTransport(timeout_transport),
    )
    assert http_to_res == Tier2ProbeOutcome(kind="transient_error", error_summary="Probe timed out.")

    # OAuth / CLI login path (api_key=None)
    async def exec_logged_in(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(returncode=0, stdout=b'{"loggedIn": true}')

    assert (
        await probe_claude_cli(
            binary_path="claude",
            api_key=None,
            parent_env={},
            subprocess_exec_fn=exec_logged_in,
        )
    ) == Tier2ProbeOutcome(kind="verified")

    async def exec_logged_out(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(returncode=1, stdout=b'{"loggedIn": false}')

    out_res = await probe_claude_cli(
        binary_path="claude",
        api_key=None,
        parent_env={},
        subprocess_exec_fn=exec_logged_out,
    )
    assert out_res.kind == "auth_failed"

    async def exec_timeout(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(returncode=None, hang_seconds=2.0)

    to_res = await probe_claude_cli(
        binary_path="claude",
        api_key=None,
        parent_env={},
        timeout_seconds=0.05,
        subprocess_exec_fn=exec_timeout,
    )
    assert to_res == Tier2ProbeOutcome(kind="transient_error", error_summary="Probe timed out.")


@pytest.mark.asyncio
async def test_probe_agy_classification_and_env_isolation() -> None:
    """probe_agy handles verified, auth_failed, transient_error, malformed JSON, and strips GEMINI_API_KEY."""
    captured_envs: list[dict[str, str]] = []
    captured_argvs: list[list[str]] = []

    async def exec_ok(*args: Any, **kw: Any) -> Any:
        captured_argvs.append([str(a) for a in args])
        captured_envs.append(dict(kw["env"]))
        return _FakeProcess(returncode=0, stdout=b'{"response": "OK"}')

    res_ok = await probe_agy(
        binary_path="agy",
        parent_env={"GEMINI_API_KEY": "should-not-leak", "AGY_ACCOUNT": "ops@example.com"},
        subprocess_exec_fn=exec_ok,
    )
    assert res_ok == Tier2ProbeOutcome(kind="verified")
    assert captured_argvs[-1][captured_argvs[-1].index("--print-timeout") + 1] == "30s"
    assert "GEMINI_API_KEY" not in captured_envs[-1]
    assert captured_envs[-1]["AGY_ACCOUNT"] == "ops@example.com"

    async def exec_is_err_auth(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(
            returncode=0,
            stdout=json.dumps({"is_error": True, "result": "Not logged in sk-ant-1234567890abcdef"}).encode(),
        )

    res_auth = await probe_agy(binary_path="agy", parent_env={}, subprocess_exec_fn=exec_is_err_auth)
    assert res_auth.kind == "auth_failed"
    assert "sk-ant-" not in (res_auth.error_summary or "")

    async def exec_bad_json(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(returncode=0, stdout=b"NOT_JSON")

    res_bad = await probe_agy(binary_path="agy", parent_env={}, subprocess_exec_fn=exec_bad_json)
    assert res_bad.kind == "transient_error"

    async def exec_agy_timeout(*_a: Any, **_kw: Any) -> Any:
        return _FakeProcess(returncode=None, hang_seconds=2.0)

    res_agy_to = await probe_agy(
        binary_path="agy",
        parent_env={},
        timeout_seconds=0.05,
        subprocess_exec_fn=exec_agy_timeout,
    )
    assert res_agy_to == Tier2ProbeOutcome(kind="transient_error", error_summary="Probe timed out.")


@pytest.mark.asyncio
async def test_subprocess_runner_path_isolation_di_and_classify_before_redact(tmp_path: Path) -> None:
    """resolve_cli_binary isolates PATH when env={}, and SubprocessHarnessRunner uses DI seams & classify-before-redact."""
    with pytest.raises(OpenDVError) as exc_bin:
        resolve_cli_binary("claude-cli", env={})
    assert exc_bin.value.error_code == ErrorCode.DRIVER_MISSING

    fake_bin = tmp_path / "claude.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    captured_env: dict[str, str] = {}

    async def err_payload_stream(
        _cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = input_text, cwd, timeout_seconds, cli_label
        captured_env.update(env)
        line = json.dumps(
            {"type": "result", "is_error": True, "result": "authentication_error for sk-ant-secret1234567890"}
        )
        state_out["returncode"] = 0
        state_out["stdout"] = line
        state_out["stderr"] = ""
        yield line

    runner = SubprocessHarnessRunner(
        "claude-cli",
        api_key="sk-ant-di-key1234567890",
        env={},
        which_fn=lambda name: str(fake_bin) if name.startswith("claude") else None,
        stream_fn=err_payload_stream,
    )
    with pytest.raises(OpenDVError) as exc_err_payload:
        await runner.run_turn(
            AgentTurnRequest(run_id="r-sub-di", agent_id="ship-vault", step="step6", prompt="hi")
        )
    assert captured_env["ANTHROPIC_API_KEY"] == "sk-ant-di-key1234567890"
    assert exc_err_payload.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert "sk-ant-secret" not in str(exc_err_payload.value)

    # Non-zero exit with empty stderr falls back to stdout and classifies auth before redaction
    async def rc_stdout_stream(
        _cmd: list[str],
        *,
        input_text: str,
        env: dict[str, str],
        cwd: Path,
        timeout_seconds: float,
        state_out: dict[str, Any],
        cli_label: str,
    ) -> AsyncIterator[str]:
        _ = input_text, env, cwd, timeout_seconds, cli_label
        state_out["returncode"] = 1
        state_out["stdout"] = "Invalid x-api-key: sk-ant-leaked1234567890"
        state_out["stderr"] = ""
        if False:
            yield ""

    runner_rc = SubprocessHarnessRunner(
        "claude-cli",
        binary_path=str(fake_bin),
        env={},
        stream_fn=rc_stdout_stream,
    )
    with pytest.raises(OpenDVError) as exc_rc:
        await runner_rc.run_turn(
            AgentTurnRequest(run_id="r-sub-rc", agent_id="ship-vault", step="step6", prompt="hi")
        )
    assert exc_rc.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert "sk-ant-leaked" not in str(exc_rc.value)


@pytest.mark.asyncio
async def test_sdk_runner_aio_gemini_client_options_close_and_auth_classification() -> None:
    """SdkHarnessRunner prefers client.aio, passes base_url/vertexai=False, closes safely, and classifies raw errors."""
    created_clients: list[dict[str, Any]] = []
    closed_log: list[str] = []

    class _FakeSdk:
        class APIError(Exception):
            def __init__(self, msg: str, status_code: int | None = None) -> None:
                super().__init__(msg)
                self.status_code = status_code

        class AsyncAnthropic:
            def __init__(self, **kwargs: Any) -> None:
                created_clients.append({"kind": "claude", **kwargs})
                self.messages = type("_M", (), {"create": self._create})()

            async def _create(self, **_kw: Any) -> dict[str, Any]:
                raise httpx.ReadTimeout("anthropic transport timeout")

            async def close(self) -> None:
                closed_log.append("claude_closed")

        class Client:
            def __init__(self, **kwargs: Any) -> None:
                created_clients.append({"kind": "gemini", **kwargs})
                self.aio = type(
                    "_Aio",
                    (),
                    {
                        "models": type("_M", (), {"generate_content": self._aio_gen})(),
                        "aclose": self._aclose,
                    },
                )()

            async def _aio_gen(self, **_kw: Any) -> dict[str, Any]:
                return {"text": "ok", "usage": {"prompt_token_count": 3, "candidates_token_count": 2}}

            async def _aclose(self) -> None:
                closed_log.append("gemini_aclosed")
                raise httpx.ConnectError("teardown error")

            def close(self) -> None:
                closed_log.append("gemini_closed")

    gemini_runner = SdkHarnessRunner(
        "gemini",
        env={"GEMINI_API_KEY": "AIzaGoodKey1234567890abcdefghij"},
        module_loader=lambda _n: _FakeSdk,
    )
    res_g = await gemini_runner.run_turn(
        AgentTurnRequest(run_id="r-g-aio", agent_id="ship-vault", step="step6", prompt="hello")
    )
    assert res_g.text_output == "ok"
    assert created_clients[-1]["vertexai"] is False
    assert created_clients[-1]["http_options"]["base_url"] == "https://generativelanguage.googleapis.com"
    assert "gemini_aclosed" in closed_log and "gemini_closed" in closed_log

    claude_runner = SdkHarnessRunner(
        "claude",
        env={"ANTHROPIC_API_KEY": "sk-ant-ok", "ANTHROPIC_BASE_URL": "https://custom.anthropic.local"},
        module_loader=lambda _n: _FakeSdk,
    )
    with pytest.raises(OpenDVError) as exc_http:
        await claude_runner.run_turn(
            AgentTurnRequest(run_id="r-c-http", agent_id="ship-vault", step="step6", prompt="hello")
        )
    assert exc_http.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
    assert created_clients[-1]["base_url"] == "https://custom.anthropic.local"
    assert "claude_closed" in closed_log

    # Classify HTTP 400 API_KEY_INVALID before redacting raw secret
    class _AuthFailClient:
        class _Models:
            async def generate_content(self, **_kw: Any) -> Any:
                raise _FakeSdk.APIError(
                    "API_KEY_INVALID key=AIzaSecretLeak1234567890abcdef",
                    status_code=400,
                )

        aio = type("_Aio", (), {"models": _Models()})()

    auth_gem_runner = SdkHarnessRunner(
        "gemini",
        env={"GEMINI_API_KEY": "AIzaBad"},
        module_loader=lambda _n: _FakeSdk,
        client_factory=lambda _p, _m, _k: _AuthFailClient(),
    )
    with pytest.raises(OpenDVError) as exc_gem_auth:
        await auth_gem_runner.run_turn(
            AgentTurnRequest(run_id="r-g-auth", agent_id="ship-vault", step="step6", prompt="hello")
        )
    assert exc_gem_auth.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert "AIzaSecretLeak" not in str(exc_gem_auth.value)


@pytest.mark.asyncio
async def test_httpx_invalid_url_handled_non_blocking_across_probes_and_sdk_runner() -> None:
    """Malformed ANTHROPIC_BASE_URL raising httpx.InvalidURL is caught in CLI/SDK probes and runner."""
    bad_env = {"ANTHROPIC_API_KEY": "sk-ant-ok-1234567890", "ANTHROPIC_BASE_URL": "http://localhost:notaport"}

    cli_res = await probe_claude_cli(
        binary_path="claude",
        api_key="sk-ant-ok-1234567890",
        parent_env=bad_env,
        transport=httpx.MockTransport(lambda _req: httpx.Response(200)),
    )
    assert cli_res.kind == "transient_error"

    class _EagerInvalidUrlSdk:
        class AsyncAnthropic:
            def __init__(self, **kwargs: Any) -> None:
                _ = httpx.URL(kwargs["base_url"])

    sdk_probe_res = await probe_claude_sdk(
        api_key="sk-ant-ok-1234567890",
        parent_env=bad_env,
        module_loader=lambda _n: _EagerInvalidUrlSdk,
    )
    assert sdk_probe_res.kind == "transient_error"

    runner = SdkHarnessRunner(
        "claude",
        env=bad_env,
        module_loader=lambda _n: _EagerInvalidUrlSdk,
    )
    with pytest.raises(OpenDVError) as exc_runner:
        await runner.run_turn(
            AgentTurnRequest(run_id="r-invalid-url", agent_id="ship-vault", step="step6", prompt="hi")
        )
    assert exc_runner.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED

    class _BadCloseClient:
        def close(self) -> None:
            raise httpx.InvalidURL("bad url on close")

    await close_sdk_client(_BadCloseClient(), used_aio=False)
