"""Automatic Dual-SDK Tool Bridge for Google Antigravity and Claude Agent SDKs.

Conforms to docs/09-agnostic-agent-harness-and-sdk-spec.md (§3.2).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from opendv_agent_harness.models import AgentToolDefinition, ToolExecutionContext


def build_google_docstring(description: str, model_fields: dict[str, Any]) -> str:
    """Generate a Google-style docstring from Pydantic model fields for AGY tool introspection."""
    lines = [description.strip(), "", "Args:"]
    for field_name, field_info in model_fields.items():
        desc = getattr(field_info, "description", None) or f"Parameter '{field_name}'."
        lines.append(f"    {field_name}: {desc}")
    return "\n".join(lines)


def to_antigravity_tool_callable(
    tool_def: AgentToolDefinition[Any, Any],
    exec_ctx: ToolExecutionContext,
) -> Callable[..., Awaitable[str]]:
    """Synthesize a strongly-typed Python async function for `google.antigravity`.

    Produces an async callable with exact `inspect.Signature` (keyword-only parameters),
    `__annotations__`, Google-style docstring, and `ToolContext` state synchronization.
    """
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for field_name, field_info in tool_def.input_model.model_fields.items():
        annotation = field_info.annotation if field_info.annotation is not None else Any
        default = inspect.Parameter.empty if field_info.is_required() else field_info.default
        parameters.append(
            inspect.Parameter(
                name=field_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
        annotations[field_name] = annotation

    annotations["return"] = str

    async def _agy_tool_wrapper(**kwargs: Any) -> str:
        agy_ctx = kwargs.pop("ctx", None)
        if agy_ctx is not None and hasattr(agy_ctx, "get_state") and hasattr(agy_ctx, "set_state"):
            saved = agy_ctx.get_state("opendv_state", {})
            if isinstance(saved, dict):
                for k, v in saved.items():
                    exec_ctx.set_state(str(k), v)

        output_model = await tool_def.invoke(kwargs, exec_ctx)

        if agy_ctx is not None and hasattr(agy_ctx, "set_state"):
            agy_ctx.set_state("opendv_state", dict(exec_ctx._session_state))

        return str(output_model.model_dump_json())

    _agy_tool_wrapper.__name__ = tool_def.name
    _agy_tool_wrapper.__qualname__ = tool_def.name
    _agy_tool_wrapper.__doc__ = build_google_docstring(
        tool_def.description,
        tool_def.input_model.model_fields,
    )
    _agy_tool_wrapper.__annotations__ = annotations
    _agy_tool_wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=parameters,
        return_annotation=str,
    )
    return _agy_tool_wrapper


def build_claude_sdk_mcp_bundle(
    tools: Sequence[AgentToolDefinition[Any, Any]],
    exec_ctx: ToolExecutionContext,
    server_key: str = "opendv",
) -> tuple[dict[str, Any], list[str], dict[str, dict[str, Any]]]:
    """Package AgentToolDefinitions into an in-process Claude Agent SDK MCP bundle.

    Returns:
      1. `mcp_servers` mapping for `ClaudeAgentOptions(mcp_servers=...)`
      2. `qualified_tool_names` (`["mcp__opendv__<name>", ...]`) for `allowed_tools`
      3. `raw_specs` dictionary for unit/contract testing without spawning a subprocess.
    """
    raw_specs: dict[str, dict[str, Any]] = {}
    qualified_names: list[str] = []
    sdk_tools: list[Any] = []
    handlers_by_name: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {}

    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            create_sdk_mcp_server,
        )
        from claude_agent_sdk import (
            tool as claude_tool_decorator,
        )

        has_claude_sdk = True
    except ImportError:
        has_claude_sdk = False

    for t_def in tools:

        async def _bound_handler(
            args: dict[str, Any],
            td: AgentToolDefinition[Any, Any] = t_def,
        ) -> dict[str, Any]:
            try:
                result = await td.invoke(args, exec_ctx)
                return {
                    "content": [{"type": "text", "text": result.model_dump_json()}],
                    "is_error": False,
                }
            except Exception as exc:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"error": type(exc).__name__, "detail": str(exc)}),
                        }
                    ],
                    "is_error": True,
                }

        qual_name = f"mcp__{server_key}__{t_def.name}"
        qualified_names.append(qual_name)
        handlers_by_name[qual_name] = _bound_handler
        handlers_by_name[t_def.name] = _bound_handler
        raw_specs[qual_name] = {
            "name": t_def.name,
            "qualified_name": qual_name,
            "description": t_def.description,
            "input_schema": t_def.json_schema(),
            "handler": _bound_handler,
        }

        if has_claude_sdk:
            decorated = claude_tool_decorator(
                t_def.name,
                t_def.description,
                t_def.json_schema(),
            )(_bound_handler)
            sdk_tools.append(decorated)

    if has_claude_sdk:
        mcp_server: Any = create_sdk_mcp_server(
            name=server_key,
            version="1.0.0",
            tools=sdk_tools,
        )
        if isinstance(mcp_server, dict):
            mcp_server.setdefault("handlers", handlers_by_name)
            mcp_server.setdefault("tools", qualified_names)
    else:
        mcp_server = {
            "type": "sdk_in_process_stub",
            "name": server_key,
            "tools": qualified_names,
            "handlers": handlers_by_name,
        }

    return {server_key: mcp_server}, qualified_names, raw_specs


__all__ = [
    "build_claude_sdk_mcp_bundle",
    "build_google_docstring",
    "to_antigravity_tool_callable",
]
