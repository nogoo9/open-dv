"""Tier-2 authentication probes and offline default model catalogs for Open-DV harnesses."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from opendv.agents._cli_ndjson import extract_json_from_stdout
from opendv.agents.cli_subprocess_base import (
    IsolatedTempCwd,
    SubprocessExecFn,
    build_isolated_child_env,
    is_auth_error,
    redact_telemetry_text,
    run_cli_subprocess_capture,
)
from opendv.agents.ports import MODEL_TIER_DEFAULTS, resolve_model_for_agent
from opendv.agents.sdk_runner import (
    OPTIONAL_HTTP_ERROR_SPECS,
    ModuleLoaderFn,
    SdkClientFactory,
    SdkProvider,
    build_sdk_client,
    close_sdk_client,
    collect_sdk_error_types,
    load_optional_sdk_module,
    resolve_optional_exception_types,
)
from opendv.models.errors import OpenDVError
from opendv.models.project import HarnessProvider
from opendv.models.wire_dtos_readiness import ModelOption


@dataclass(frozen=True, slots=True)
class Tier2ProbeOutcome:
    """Result returned by Tier-2 auth verification probes."""

    kind: Literal["verified", "auth_failed", "transient_error"]
    error_summary: str | None = None


ProbeFn = Callable[
    [HarnessProvider, str | None, str | None, Mapping[str, str]],
    Awaitable[Tier2ProbeOutcome],
]


def default_model_options(provider: HarnessProvider) -> list[ModelOption]:
    """Return offline default ModelOption suggestions for a harness provider."""
    tier_map = MODEL_TIER_DEFAULTS[provider]
    frontier_id = tier_map["FRONTIER_CRITIC"]
    worker_id = tier_map["STANDARD_WORKER"]
    return [
        ModelOption(
            id=frontier_id,
            label=f"{frontier_id} (Frontier Critic Default)",
            source="default",
        ),
        ModelOption(
            id=worker_id,
            label=f"{worker_id} (Standard Worker Default)",
            source="default",
        ),
    ]


def _format_probe_error_summary(exc: BaseException) -> str:
    redacted = redact_telemetry_text(str(exc))
    if isinstance(exc, TimeoutError) and not redacted.strip():
        return "Probe timed out."
    return redacted[:300]


class _SyncToAsyncTransport(httpx.AsyncBaseTransport):
    """Adapt a synchronous httpx.BaseTransport for use with httpx.AsyncClient."""

    def __init__(self, sync_transport: httpx.BaseTransport) -> None:
        self._sync = sync_transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()

        def _run_sync() -> httpx.Response:
            resp = self._sync.handle_request(request)
            try:
                if not resp.is_stream_consumed:
                    body = b"".join(resp.iter_raw())
                    headers = resp.headers
                else:
                    body = resp.content
                    headers = resp.headers.copy()
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)
            finally:
                resp.close()
            return httpx.Response(
                status_code=resp.status_code,
                headers=headers,
                content=body,
                extensions=resp.extensions,
            )

        return await asyncio.to_thread(_run_sync)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._sync.close)


def _parse_probe_json(stdout: str, *, cli_label: str) -> dict[str, Any] | None:
    raw = stdout.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    try:
        return extract_json_from_stdout(raw, cli_label=cli_label)
    except (OpenDVError, ValueError):
        return None


async def _run_cli_probe_capture(
    provider: Literal["agy", "claude-cli"],
    argv: Sequence[str],
    *,
    parent_env: Mapping[str, str],
    timeout_seconds: float,
    subprocess_exec_fn: SubprocessExecFn | None,
) -> tuple[int, str, str] | Tier2ProbeOutcome:
    child_env = build_isolated_child_env(
        provider=provider,
        step="step6",
        parent_env=parent_env,
    )
    try:
        with IsolatedTempCwd() as cwd:
            return await run_cli_subprocess_capture(
                argv,
                env=child_env,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                subprocess_exec_fn=subprocess_exec_fn,
            )
    except (OSError, ValueError) as exc:
        return Tier2ProbeOutcome(
            kind="transient_error",
            error_summary=_format_probe_error_summary(exc),
        )


async def _run_sdk_probe(
    provider: SdkProvider,
    *,
    api_key: str,
    base_url: str | None = None,
    timeout_seconds: float = 15.0,
    sdk_client_factory: SdkClientFactory | None = None,
    module_loader: ModuleLoaderFn | None = None,
) -> Tier2ProbeOutcome:
    sdk_mod = (
        load_optional_sdk_module(provider, module_loader=module_loader)
        if sdk_client_factory is None or module_loader is not None
        else None
    )
    used_aio = provider == "gemini"
    catch_types: tuple[type[BaseException], ...] = tuple(
        dict.fromkeys(
            [
                httpx.HTTPError,
                httpx.InvalidURL,
                OSError,
                RuntimeError,
                ValueError,
                *resolve_optional_exception_types(OPTIONAL_HTTP_ERROR_SPECS),
                *collect_sdk_error_types(sdk_mod),
            ]
        )
    )
    client: Any = None
    try:
        client = (
            sdk_client_factory(provider, sdk_mod, api_key)
            if sdk_client_factory is not None
            else build_sdk_client(
                provider,
                sdk_mod,
                api_key,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
            )
        )
        async with asyncio.timeout(timeout_seconds):
            res = (
                client.models.list(limit=1)
                if provider == "claude"
                else client.aio.models.list(config={"page_size": 1})
            )
            if inspect.isawaitable(res):
                await res
        return Tier2ProbeOutcome(kind="verified")
    except catch_types as exc:
        kind: Literal["auth_failed", "transient_error"] = (
            "auth_failed" if is_auth_error(exc, provider=provider) else "transient_error"
        )
        return Tier2ProbeOutcome(
            kind=kind,
            error_summary=_format_probe_error_summary(exc),
        )
    finally:
        if sdk_client_factory is None and client is not None:
            await close_sdk_client(client, used_aio=used_aio)


async def probe_claude_sdk(
    *,
    api_key: str,
    parent_env: Mapping[str, str],
    timeout_seconds: float = 15.0,
    sdk_client_factory: SdkClientFactory | None = None,
    module_loader: ModuleLoaderFn | None = None,
) -> Tier2ProbeOutcome:
    """Verify Anthropic API key via zero-token models.list(limit=1)."""
    return await _run_sdk_probe(
        "claude",
        api_key=api_key,
        base_url=parent_env.get("ANTHROPIC_BASE_URL"),
        timeout_seconds=timeout_seconds,
        sdk_client_factory=sdk_client_factory,
        module_loader=module_loader,
    )


async def probe_gemini_sdk(
    *,
    api_key: str,
    timeout_seconds: float = 15.0,
    sdk_client_factory: SdkClientFactory | None = None,
    module_loader: ModuleLoaderFn | None = None,
) -> Tier2ProbeOutcome:
    """Verify Gemini API key via zero-token client.aio.models.list(page_size=1)."""
    return await _run_sdk_probe(
        "gemini",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        sdk_client_factory=sdk_client_factory,
        module_loader=module_loader,
    )


async def probe_claude_cli(
    *,
    binary_path: str,
    api_key: str | None,
    parent_env: Mapping[str, str],
    timeout_seconds: float = 15.0,
    transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    subprocess_exec_fn: SubprocessExecFn | None = None,
) -> Tier2ProbeOutcome:
    """Verify claude-cli auth via GET /v1/models?limit=1 when api_key set, else auth status."""
    if api_key is not None and api_key.strip():
        raw_base = (parent_env.get("ANTHROPIC_BASE_URL") or "").strip()
        base = (raw_base or "https://api.anthropic.com").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        url = f"{base}/v1/models?limit=1"
        headers = {
            "x-api-key": api_key.strip(),
            "anthropic-version": "2023-06-01",
        }
        async_transport: httpx.AsyncBaseTransport | None = None
        if transport is not None:
            async_transport = (
                transport
                if isinstance(transport, httpx.AsyncBaseTransport)
                else _SyncToAsyncTransport(transport)
            )
        catch_types: tuple[type[BaseException], ...] = tuple(
            dict.fromkeys(
                [
                    httpx.HTTPError,
                    httpx.InvalidURL,
                    OSError,
                    ValueError,
                    *resolve_optional_exception_types(OPTIONAL_HTTP_ERROR_SPECS),
                ]
            )
        )
        try:
            async with (
                asyncio.timeout(timeout_seconds),
                httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=timeout_seconds,
                    transport=async_transport,
                ) as client,
            ):
                resp = await client.get(url, headers=headers)
        except catch_types as exc:
            return Tier2ProbeOutcome(
                kind="transient_error",
                error_summary=_format_probe_error_summary(exc),
            )
        if resp.status_code == 200:
            return Tier2ProbeOutcome(kind="verified")
        summary = redact_telemetry_text(f"HTTP {resp.status_code}: {resp.text}")[:300]
        if resp.status_code == 401:
            return Tier2ProbeOutcome(kind="auth_failed", error_summary=summary)
        return Tier2ProbeOutcome(kind="transient_error", error_summary=summary)

    captured = await _run_cli_probe_capture(
        "claude-cli",
        [binary_path, "auth", "status", "--json"],
        parent_env=parent_env,
        timeout_seconds=timeout_seconds,
        subprocess_exec_fn=subprocess_exec_fn,
    )
    if isinstance(captured, Tier2ProbeOutcome):
        return captured
    rc, stdout, stderr = captured
    parsed = _parse_probe_json(stdout, cli_label="claude-cli")
    if isinstance(parsed, dict):
        logged_in = parsed.get("loggedIn")
        if logged_in is True:
            return Tier2ProbeOutcome(kind="verified")
        if logged_in is False:
            raw_msg = stderr.strip() or stdout.strip() or "claude auth status loggedIn=false"
            return Tier2ProbeOutcome(
                kind="auth_failed",
                error_summary=redact_telemetry_text(raw_msg)[:300],
            )
    combined = " | ".join(p for p in (stderr.strip(), stdout.strip()) if p) or f"exit {rc}"
    kind: Literal["auth_failed", "transient_error"] = (
        "auth_failed" if is_auth_error(combined, provider="claude-cli") else "transient_error"
    )
    return Tier2ProbeOutcome(kind=kind, error_summary=redact_telemetry_text(combined)[:300])


async def probe_agy(
    *,
    binary_path: str,
    parent_env: Mapping[str, str],
    timeout_seconds: float = 30.0,
    subprocess_exec_fn: SubprocessExecFn | None = None,
) -> Tier2ProbeOutcome:
    """Verify agy auth via a single bounded --print=Reply OK. inference ping."""
    ping_model = resolve_model_for_agent("agy", "STANDARD_WORKER", env=parent_env).model_id
    argv = [
        binary_path,
        "--sandbox",
        "--disable-slash-commands",
        "--output-format",
        "json",
        "--model",
        ping_model,
        "--print-timeout",
        f"{max(1, int(timeout_seconds))}s",
        "--print=Reply OK.",
    ]
    captured = await _run_cli_probe_capture(
        "agy",
        argv,
        parent_env=parent_env,
        timeout_seconds=timeout_seconds,
        subprocess_exec_fn=subprocess_exec_fn,
    )
    if isinstance(captured, Tier2ProbeOutcome):
        return captured
    rc, stdout, stderr = captured
    parsed = _parse_probe_json(stdout, cli_label="agy")
    if rc == 0 and isinstance(parsed, dict) and parsed.get("is_error") is not True:
        return Tier2ProbeOutcome(kind="verified")
    combined = " | ".join(p for p in (stderr.strip(), stdout.strip()) if p) or f"exit {rc}"
    kind: Literal["auth_failed", "transient_error"] = (
        "auth_failed" if is_auth_error(combined, provider="agy") else "transient_error"
    )
    return Tier2ProbeOutcome(kind=kind, error_summary=redact_telemetry_text(combined)[:300])


__all__ = [
    "ProbeFn",
    "Tier2ProbeOutcome",
    "default_model_options",
    "is_auth_error",
    "probe_agy",
    "probe_claude_cli",
    "probe_claude_sdk",
    "probe_gemini_sdk",
]
