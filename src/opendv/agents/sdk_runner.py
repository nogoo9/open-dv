"""Optional bare SDK harness runner for claude (anthropic) and gemini (google-genai)."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from opendv.agents.cli_subprocess_base import (
    build_stateless_turn_prompt,
    count_cl100k_tokens,
    redact_telemetry_text,
    validate_cli_structured_output,
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

_COORD_TOOL_NAMES: tuple[str, ...] = (
    "search_concepts",
    "search_fibo",
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


def _build_sdk_client(provider: SdkProvider, sdk_mod: Any, api_key: str) -> Any:
    if provider == "claude":
        async_cls = getattr(sdk_mod, "AsyncAnthropic", None) or getattr(sdk_mod, "Anthropic", None)
        if async_cls is None:
            raise OpenDVError(
                "Installed anthropic SDK is missing Anthropic client class.",
                error_code=ErrorCode.DRIVER_MISSING,
            )
        return async_cls(api_key=api_key)
    client_cls = getattr(sdk_mod, "Client", None)
    if client_cls is None:
        raise OpenDVError(
            "Installed google.genai SDK is missing Client class.",
            error_code=ErrorCode.DRIVER_MISSING,
        )
    return client_cls(api_key=api_key)


def _extract_sdk_text_and_usage(response: Any) -> tuple[str, int, int, int, list[dict[str, Any]]]:
    if isinstance(response, dict):
        text_val = str(response.get("text") or response.get("output_text") or "")
        raw_usage = response.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        in_t = int(usage.get("input_tokens") or usage.get("prompt_token_count") or 0)
        out_t = int(usage.get("output_tokens") or usage.get("candidates_token_count") or 0)
        tot_t = int(usage.get("total_tokens") or usage.get("total_token_count") or (in_t + out_t))
        scout_calls = list(response.get("scout_calls") or [])
        return text_val, in_t, out_t, tot_t, scout_calls

    text_out = ""
    raw_text = getattr(response, "text", None)
    if isinstance(raw_text, str):
        text_out = raw_text
    else:
        content = getattr(response, "content", None)
        if isinstance(content, list):
            parts = [str(getattr(blk, "text", "")) for blk in content if getattr(blk, "text", None)]
            text_out = "".join(parts)

    usage_obj = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    in_t = int(
        getattr(usage_obj, "input_tokens", 0)
        or getattr(usage_obj, "prompt_token_count", 0)
        or 0
    )
    out_t = int(
        getattr(usage_obj, "output_tokens", 0)
        or getattr(usage_obj, "candidates_token_count", 0)
        or 0
    )
    tot_t = int(
        getattr(usage_obj, "total_tokens", 0)
        or getattr(usage_obj, "total_token_count", 0)
        or (in_t + out_t)
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

        models_api = getattr(client, "models", None)
        if models_api is None:
            raise OpenDVError(
                "Google GenAI client does not expose models.generate_content.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                run_id=request.run_id,
            )
        gen_fn = models_api.generate_content
        res = gen_fn(model=model_id, contents=user_prompt, config=config)
        if asyncio.iscoroutine(res):
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
        client = (
            self._client_factory(self._provider, sdk_mod, resolved_key)
            if self._client_factory is not None
            else _build_sdk_client(self._provider, sdk_mod, resolved_key)
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

        catch_types: list[type[BaseException]] = [TimeoutError, OSError, RuntimeError, ValueError]
        for cls_name in ("APIError", "AuthenticationError", "RateLimitError", "GenAIError"):
            cls_obj = getattr(sdk_mod, cls_name, None)
            if isinstance(cls_obj, type) and issubclass(cls_obj, BaseException):
                catch_types.append(cls_obj)
        errors_sub = getattr(sdk_mod, "errors", None)
        if errors_sub is not None:
            for cls_name in ("APIError", "ClientError", "ServerError"):
                cls_obj = getattr(errors_sub, cls_name, None)
                if isinstance(cls_obj, type) and issubclass(cls_obj, BaseException):
                    catch_types.append(cls_obj)

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
        except tuple(catch_types) as exc:
            auth_cls = getattr(sdk_mod, "AuthenticationError", None)
            is_auth_exc = isinstance(auth_cls, type) and isinstance(exc, auth_cls)
            status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            err_text = redact_telemetry_text(str(exc))
            lowered = err_text.lower()
            is_auth_msg = status_code in (401, 403) or any(
                tok in lowered
                for tok in ("authentication", "unauthorized", "invalid api key", "api_key")
            )
            err_code = (
                ErrorCode.AGENT_NOT_CONFIGURED
                if (is_auth_exc or is_auth_msg)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            raise OpenDVError(
                f"SDK turn failed for provider '{self._provider}': {err_text[:300]}",
                error_code=err_code,
                run_id=request.run_id,
            ) from exc

        text_out, in_tok, out_tok, tot_tok, scout_calls = _extract_sdk_text_and_usage(raw_resp)
        tool_events: list[dict[str, Any]] = []
        if scout_calls and request.step == "step4" and scout_dispatcher is not None:
            batch_reqs: list[tuple[Literal["table_cluster", "page_range"], Sequence[str]]] = [
                (
                    "table_cluster" if sc.get("scout_kind") == "table_cluster" else "page_range",
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


__all__ = [
    "ModuleLoaderFn",
    "SdkClientFactory",
    "SdkHarnessRunner",
    "SdkProvider",
    "dispatch_scouts_concurrently",
    "load_optional_sdk_module",
    "resolve_sdk_api_key",
    "select_sdk_tools",
]
