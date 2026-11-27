"""Optional bare SDK harness runner for claude (anthropic) and gemini (google-genai)."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from opendv.agents.cli_subprocess_base import (
    build_stateless_turn_prompt,
    count_cl100k_tokens,
    is_auth_error,
    redact_telemetry_text,
    validate_cli_structured_output,
)
from opendv.agents.cli_subprocess_base import (
    extract_tool_use_input as _extract_tool_use_input,
)
from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    RunEventSink,
    ScoutToolDispatcher,
    merge_model_overrides,
    resolve_model_for_agent,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStreamEvent
from opendv.models.project import HarnessProvider
from opendv.models.wire_dtos_discovery import ScoutDigest

SdkProvider = Literal["claude", "gemini"]
ModuleLoaderFn = Callable[[str], Any]
SdkClientFactory = Callable[[SdkProvider, Any, str], Any]

OPTIONAL_HTTP_ERROR_SPECS: tuple[tuple[str, str], ...] = (
    ("aiohttp", "ClientError"),
    ("httpx2", "HTTPError"),
)
_OPTIONAL_CLOSE_ERROR_SPECS: tuple[tuple[str, str], ...] = (
    *OPTIONAL_HTTP_ERROR_SPECS,
    ("anyio", "ClosedResourceError"),
)


def resolve_optional_exception_types(
    specs: Sequence[tuple[str, str]],
) -> tuple[type[BaseException], ...]:
    """Resolve optional exception classes from third-party modules when installed."""
    resolved: list[type[BaseException]] = []
    for mod_name, attr_name in specs:
        with contextlib.suppress(ImportError, AttributeError):
            mod = importlib.import_module(mod_name)
            cls_obj = getattr(mod, attr_name, None)
            if isinstance(cls_obj, type) and issubclass(cls_obj, BaseException):
                resolved.append(cls_obj)
    return tuple(dict.fromkeys(resolved))


def collect_sdk_error_types(sdk_mod: Any) -> tuple[type[BaseException], ...]:
    """Collect typed SDK error classes from an imported anthropic or google.genai module."""
    if sdk_mod is None:
        return ()
    found: list[type[BaseException]] = []
    for cls_name in ("APIError", "AuthenticationError", "RateLimitError", "GenAIError"):
        cls_obj = getattr(sdk_mod, cls_name, None)
        if isinstance(cls_obj, type) and issubclass(cls_obj, BaseException):
            found.append(cls_obj)
    errors_sub = getattr(sdk_mod, "errors", None)
    if errors_sub is not None:
        for cls_name in ("APIError", "ClientError", "ServerError"):
            cls_obj = getattr(errors_sub, cls_name, None)
            if isinstance(cls_obj, type) and issubclass(cls_obj, BaseException):
                found.append(cls_obj)
    return tuple(dict.fromkeys(found))


async def close_sdk_client(client: Any, *, used_aio: bool = False) -> None:
    """Best-effort close of sync/async SDK clients suppressing transport teardown errors."""
    suppress_types: tuple[type[BaseException], ...] = (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        httpx.HTTPError,
        httpx.InvalidURL,
        *resolve_optional_exception_types(_OPTIONAL_CLOSE_ERROR_SPECS),
    )
    if used_aio:
        aio_obj = getattr(client, "aio", None)
        aclose_fn = getattr(aio_obj, "aclose", None)
        if callable(aclose_fn):
            with contextlib.suppress(*suppress_types):
                res = aclose_fn()
                if inspect.isawaitable(res):
                    await res
    close_fn = getattr(client, "close", None)
    if callable(close_fn):
        with contextlib.suppress(*suppress_types):
            res = close_fn()
            if inspect.isawaitable(res):
                await res

_COORD_TOOL_NAMES: tuple[str, ...] = (
    "search_concepts",
    "search_fibo",
    "ground_columns",
    "propose_ontology_delta",
    "ask_domain_clarification",
    "dispatch_scout",
)
_SCOUT_TOOL_NAMES: tuple[str, ...] = (
    "inspect_table_cluster",
    "inspect_table_profile",
    "read_document_pages",
    "view_page_image",
    "call_external_mcp",
)


def _default_module_loader(module_name: str) -> Any:
    return importlib.import_module(module_name)


def load_optional_sdk_module(
    provider: SdkProvider,
    *,
    module_loader: ModuleLoaderFn | None = None,
) -> Any:
    """Import optional anthropic or google.genai SDK, failing closed with DRIVER_MISSING."""
    loader = _default_module_loader if module_loader is None else module_loader
    module_name = "anthropic" if provider == "claude" else "google.genai"
    try:
        return loader(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        extra_hint = "anthropic" if provider == "claude" else "google-genai"
        raise OpenDVError(
            f"Optional SDK package '{extra_hint}' for provider '{provider}' is not installed.",
            error_code=ErrorCode.DRIVER_MISSING,
        ) from exc


def resolve_sdk_api_key(
    provider: SdkProvider,
    *,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve required API key for bare SDK providers, failing closed if absent."""
    if api_key and api_key.strip():
        return api_key.strip()
    active_env = os.environ if env is None else env
    key_names = (
        ("ANTHROPIC_API_KEY",) if provider == "claude" else ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    for name in key_names:
        val = active_env.get(name, "").strip()
        if val:
            return val
    raise OpenDVError(
        f"Provider '{provider}' requires {' or '.join(key_names)} to be configured.",
        error_code=ErrorCode.AGENT_NOT_CONFIGURED,
    )


def select_sdk_tools(request: AgentTurnRequest) -> list[dict[str, str]]:
    """Return Step 4 role-scoped broker tool declarations or zero tools in Step 6."""
    if request.step != "step4" or request.role_kind == "none":
        return []
    base = _COORD_TOOL_NAMES if request.role_kind == "coord" else _SCOUT_TOOL_NAMES
    allowed = set(request.allowed_tools) if request.allowed_tools else set(base)
    names = [name for name in base if name in allowed]
    return [{"name": name, "scope": request.role_kind} for name in names]


async def dispatch_scouts_concurrently(
    dispatcher: ScoutToolDispatcher,
    requests: Sequence[tuple[Literal["table_cluster", "page_range"], Sequence[str]]],
    *,
    run_id: str,
) -> list[ScoutDigest]:
    """Dispatch multiple Step 4 scout batches concurrently via asyncio.gather."""
    if not requests:
        return []
    batches = await asyncio.gather(
        *(
            dispatcher.dispatch_scout(kind, targets, run_id=run_id)
            for kind, targets in requests
        )
    )
    merged: list[ScoutDigest] = []
    for batch in batches:
        merged.extend(batch)
    return merged


def build_sdk_client(
    provider: SdkProvider,
    sdk_mod: Any,
    api_key: str,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> Any:
    """Construct an Anthropic or Google GenAI client with bounded timeout and endpoint."""
    if provider == "claude":
        async_cls = getattr(sdk_mod, "AsyncAnthropic", None) or getattr(sdk_mod, "Anthropic", None)
        if async_cls is None:
            raise OpenDVError(
                "Installed anthropic SDK is missing Anthropic client class.",
                error_code=ErrorCode.DRIVER_MISSING,
            )
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": (base_url or "").strip() or "https://api.anthropic.com",
        }
        if timeout_seconds is not None:
            kwargs["timeout"] = timeout_seconds
        return async_cls(**kwargs)
    client_cls = getattr(sdk_mod, "Client", None)
    if client_cls is None:
        raise OpenDVError(
            "Installed google.genai SDK is missing Client class.",
            error_code=ErrorCode.DRIVER_MISSING,
        )
    http_options: dict[str, Any] = {"base_url": "https://generativelanguage.googleapis.com"}
    if timeout_seconds is not None:
        http_options["timeout"] = int(max(1, timeout_seconds) * 1000)
    return client_cls(api_key=api_key, vertexai=False, http_options=http_options)


_build_sdk_client = build_sdk_client


def _extract_sdk_text_and_usage(response: Any) -> tuple[str, int, int, int, list[dict[str, Any]]]:
    if isinstance(response, dict):
        text_val = str(response.get("text") or response.get("output_text") or "")
        if not text_val and isinstance(response.get("content"), list):
            text_val = "".join(
                str(b.get("text", ""))
                for b in response["content"]
                if isinstance(b, dict) and b.get("text")
            )
        if not text_val and (ti := _extract_tool_use_input(response)) is not None:
            text_val = ti if isinstance(ti, str) else json.dumps(ti, separators=(",", ":"))
        u = response["usage"] if isinstance(response.get("usage"), dict) else {}
        in_t = int(u.get("input_tokens") or u.get("prompt_token_count") or 0)
        out_t = int(u.get("output_tokens") or u.get("candidates_token_count") or 0)
        tot_t = int(u.get("total_tokens") or u.get("total_token_count") or (in_t + out_t))
        return text_val, in_t, out_t, tot_t, list(response.get("scout_calls") or [])

    raw_text = getattr(response, "text", None)
    if isinstance(raw_text, str):
        text_out = raw_text
    elif isinstance((content := getattr(response, "content", None)), list):
        text_out = "".join(str(getattr(b, "text", "")) for b in content if getattr(b, "text", None))
    else:
        text_out = ""
    if not text_out and (ti := _extract_tool_use_input(response)) is not None:
        text_out = ti if isinstance(ti, str) else json.dumps(ti, separators=(",", ":"))
    uobj = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    in_t = int(getattr(uobj, "input_tokens", 0) or getattr(uobj, "prompt_token_count", 0) or 0)
    out_t = int(
        getattr(uobj, "output_tokens", 0) or getattr(uobj, "candidates_token_count", 0) or 0
    )
    tot_t = int(
        getattr(uobj, "total_tokens", 0) or getattr(uobj, "total_token_count", 0) or (in_t + out_t)
    )
    return text_out, in_t, out_t, tot_t, []


class SdkHarnessRunner(AgentHarnessPort):
    """Bare optional-SDK implementation of AgentHarnessPort for claude and gemini."""

    def __init__(
        self,
        provider: SdkProvider = "claude",
        *,
        api_key: str | None = None,
        env: Mapping[str, str] | None = None,
        model_overrides: Mapping[str, str] | None = None,
        module_loader: ModuleLoaderFn | None = None,
        client_factory: SdkClientFactory | None = None,
    ) -> None:
        self._provider: SdkProvider = provider
        self._api_key = api_key
        self._env = dict(os.environ if env is None else env)
        self._model_overrides = dict(model_overrides or {})
        self._module_loader = module_loader
        self._client_factory = client_factory

    @property
    def provider_name(self) -> HarnessProvider:
        return self._provider

    async def _invoke_claude(
        self,
        client: Any,
        *,
        model_id: str,
        request: AgentTurnRequest,
        user_prompt: str,
        tools: list[dict[str, str]],
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": min(8192, request.budget.max_tokens),
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if request.system_instructions.strip():
            kwargs["system"] = request.system_instructions.strip()
        if tools:
            kwargs["tools"] = tools
        elif request.output_schema is not None:
            kwargs["tools"] = [
                {
                    "name": "emit_structured_output",
                    "description": "Emit structured JSON matching the required output schema.",
                    "input_schema": request.output_schema.model_json_schema(),
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "emit_structured_output"}

        messages_api = getattr(client, "messages", None)
        if messages_api is None:
            raise OpenDVError(
                "Anthropic client does not expose messages.create.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                run_id=request.run_id,
            )
        create_fn = messages_api.create
        res = create_fn(**kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res

    async def _invoke_gemini(
        self,
        client: Any,
        *,
        model_id: str,
        request: AgentTurnRequest,
        user_prompt: str,
        tools: list[dict[str, str]],
    ) -> Any:
        config: dict[str, Any] = {}
        if request.system_instructions.strip():
            config["system_instruction"] = request.system_instructions.strip()
        if request.output_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = request.output_schema.model_json_schema()
        if tools:
            config["tools"] = tools

        aio_obj = getattr(client, "aio", None)
        aio_models = getattr(aio_obj, "models", None)
        aio_gen = getattr(aio_models, "generate_content", None)
        if callable(aio_gen):
            res = aio_gen(model=model_id, contents=user_prompt, config=config)
            if inspect.isawaitable(res):
                return await res
            return res

        models_api = getattr(client, "models", None)
        if models_api is None:
            raise OpenDVError(
                "Google GenAI client does not expose models.generate_content.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                run_id=request.run_id,
            )
        gen_fn = models_api.generate_content
        if inspect.iscoroutinefunction(gen_fn):
            return await gen_fn(model=model_id, contents=user_prompt, config=config)
        res = await asyncio.to_thread(gen_fn, model=model_id, contents=user_prompt, config=config)
        if inspect.isawaitable(res):
            return await res
        return res

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        sdk_mod = load_optional_sdk_module(self._provider, module_loader=self._module_loader)
        resolved_key = resolve_sdk_api_key(
            self._provider,
            api_key=self._api_key,
            env=self._env,
        )
        resolved_model = resolve_model_for_agent(
            self._provider,
            request.model_tier,
            agent_id=request.agent_id,
            model_overrides=merge_model_overrides(self._model_overrides, request),
            cegar_policy=request.cegar_policy,
            env=self._env,
        )
        tools = select_sdk_tools(request)
        base_url = self._env.get("ANTHROPIC_BASE_URL") if self._provider == "claude" else None
        catch_types: tuple[type[BaseException], ...] = tuple(
            dict.fromkeys(
                [
                    TimeoutError,
                    OSError,
                    RuntimeError,
                    ValueError,
                    httpx.HTTPError,
                    httpx.InvalidURL,
                    *resolve_optional_exception_types(OPTIONAL_HTTP_ERROR_SPECS),
                    *collect_sdk_error_types(sdk_mod),
                ]
            )
        )
        client: Any = None
        used_aio = False
        try:
            try:
                client = (
                    self._client_factory(self._provider, sdk_mod, resolved_key)
                    if self._client_factory is not None
                    else build_sdk_client(
                        self._provider,
                        sdk_mod,
                        resolved_key,
                        base_url=base_url,
                        timeout_seconds=float(request.budget.timeout_seconds),
                    )
                )
            except catch_types as exc:
                raise self._sdk_turn_error(exc, request.run_id) from exc
            used_aio = self._provider == "gemini" and callable(
                getattr(
                    getattr(getattr(client, "aio", None), "models", None),
                    "generate_content",
                    None,
                )
            )
            user_prompt = build_stateless_turn_prompt(request, include_system=False)

            seq = int(request.checkpoint.last_seq) if request.checkpoint else 0
            current_seq_fn = getattr(event_sink, "current_seq", None)
            if event_sink is not None:
                if callable(current_seq_fn):
                    seq = max(seq, int(current_seq_fn(request.run_id)))
                seq += 1
                event_sink.emit(
                    RunStreamEvent(
                        seq=seq,
                        run_id=request.run_id,
                        ts=datetime.now(tz=UTC).isoformat(),
                        event_type="SESSION_START",
                        step=request.step,
                        payload={"provider": self._provider, "model_id": resolved_model.model_id},
                    )
                )

            try:
                async with asyncio.timeout(float(request.budget.timeout_seconds)):
                    if self._provider == "claude":
                        raw_resp = await self._invoke_claude(
                            client,
                            model_id=resolved_model.model_id,
                            request=request,
                            user_prompt=user_prompt,
                            tools=tools,
                        )
                    else:
                        raw_resp = await self._invoke_gemini(
                            client,
                            model_id=resolved_model.model_id,
                            request=request,
                            user_prompt=user_prompt,
                            tools=tools,
                        )
            except catch_types as exc:
                raise self._sdk_turn_error(exc, request.run_id) from exc

            text_out, in_tok, out_tok, tot_tok, scout_calls = _extract_sdk_text_and_usage(raw_resp)
            tool_events: list[dict[str, Any]] = []
            if scout_calls and request.step == "step4" and scout_dispatcher is not None:
                batch_reqs: list[tuple[Literal["table_cluster", "page_range"], Sequence[str]]] = [
                    (
                        (
                            "table_cluster"
                            if sc.get("scout_kind") == "table_cluster"
                            else "page_range"
                        ),
                        list(sc.get("target_ids") or ()),
                    )
                    for sc in scout_calls
                ]
                digests = await dispatch_scouts_concurrently(
                    scout_dispatcher,
                    batch_reqs,
                    run_id=request.run_id,
                )
                tool_events.append(
                    {
                        "tool_name": "dispatch_scout",
                        "tool_count": len(digests),
                        "digests": [d.model_dump(mode="json") for d in digests],
                    }
                )

            if tot_tok > 0:
                tok_source: Literal["reported", "estimated"] = "reported"
            else:
                in_tok = count_cl100k_tokens(user_prompt)
                out_tok = count_cl100k_tokens(text_out)
                tot_tok = in_tok + out_tok
                tok_source = "estimated"

            raw_structured: Any = None
            if isinstance(raw_resp, dict) and "structured_output" in raw_resp:
                raw_structured = raw_resp["structured_output"]
            elif request.output_schema is not None:
                raw_structured = _extract_tool_use_input(raw_resp)

            validated = validate_cli_structured_output(
                request.output_schema,
                raw_structured=raw_structured,
                raw_response_text=text_out,
                cli_label=f"{self._provider} SDK",
            )
            if not text_out and validated is not None:
                text_out = (
                    validated.model_dump_json()
                    if hasattr(validated, "model_dump_json")
                    else json.dumps(validated)
                )

            if event_sink is not None and text_out:
                if callable(current_seq_fn):
                    seq = max(seq, int(current_seq_fn(request.run_id)))
                seq += 1
                event_sink.emit(
                    RunStreamEvent(
                        seq=seq,
                        run_id=request.run_id,
                        ts=datetime.now(tz=UTC).isoformat(),
                        event_type="TEXT_DELTA",
                        step=request.step,
                        payload={"delta_text": redact_telemetry_text(text_out)},
                    )
                )

            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self._provider,
                model_id=resolved_model.model_id,
                text_output=text_out,
                structured_output=validated,
                turns_used=1,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                token_count_source=tok_source,
                text_deltas=[text_out] if text_out else [],
                tool_events=tool_events,
            )
        finally:
            if self._client_factory is None and client is not None:
                await close_sdk_client(client, used_aio=used_aio)

    def _sdk_turn_error(self, exc: BaseException, run_id: str) -> OpenDVError:
        err_code = (
            ErrorCode.AGENT_NOT_CONFIGURED
            if is_auth_error(exc, provider=self._provider)
            else ErrorCode.AGENT_EXECUTION_FAILED
        )
        err_text = redact_telemetry_text(str(exc))
        return OpenDVError(
            f"SDK turn failed for provider '{self._provider}': {err_text[:300]}",
            error_code=err_code,
            run_id=run_id,
        )


__all__ = [
    "OPTIONAL_HTTP_ERROR_SPECS",
    "ModuleLoaderFn",
    "SdkClientFactory",
    "SdkHarnessRunner",
    "SdkProvider",
    "build_sdk_client",
    "close_sdk_client",
    "collect_sdk_error_types",
    "dispatch_scouts_concurrently",
    "load_optional_sdk_module",
    "resolve_optional_exception_types",
    "resolve_sdk_api_key",
    "select_sdk_tools",
]
