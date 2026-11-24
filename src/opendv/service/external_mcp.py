"""Authenticated PII-scrubbing ExternalMcpGateway with handle substitution and receipts."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

from opendv.agents.cli_subprocess_base import IsolatedTempCwd
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ExternalMcpReceipt
from opendv.models.json_hasher import sha256_jcs
from opendv.models.sources import ExternalMcpServerSpec
from opendv.models.wire_dtos_discovery import ExternalMcpToolResult
from opendv.service._ext_mcp_guards import (
    DnsResolverFn,
    build_external_mcp_stdio_env,
    reject_redirect_response_hook,
    resolve_doc_page_handles,
    scrub_inbound_text,
    scrub_outbound_arguments,
    validate_external_mcp_url,
)
from opendv.service._mcp_ports import (
    ExternalMcpGatewayPort,
    OcrProviderFn,
    ProjectStatePort,
    SecretStorePort,
    SettingsStorePort,
)


def _extract_call_result_text(result: Any) -> str:
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in content:
        txt = getattr(item, "text", None)
        if isinstance(txt, str) and txt:
            parts.append(txt)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return str(structured)
    return ""


def _extract_opendv_error(exc: BaseException) -> OpenDVError | None:
    if isinstance(exc, OpenDVError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            found = _extract_opendv_error(sub)
            if found is not None:
                return found
    return None


class ExternalMcpGateway(ExternalMcpGatewayPort):
    """Step 4 external MCP proxy enforcing stage/opt-in/SSRF/PII/secret/receipt invariants."""

    def __init__(
        self,
        *,
        state_store: ProjectStatePort,
        secret_store: SecretStorePort,
        settings_store: SettingsStorePort,
        source_path_resolver: Callable[[str, str], Path | None] | None = None,
        dns_resolver: DnsResolverFn | None = None,
        ocr_provider: OcrProviderFn | None = None,
        http_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._state = state_store
        self._secrets = secret_store
        self._settings = settings_store
        self._source_path_resolver = source_path_resolver
        self._dns_resolver = dns_resolver
        self._ocr_provider = ocr_provider
        self._http_transport = http_transport

    def _get_server_spec(self, server_id: str) -> ExternalMcpServerSpec:
        for spec in self._settings.load_mcp_servers():
            if spec.server_id == server_id:
                if not spec.enabled:
                    raise OpenDVError(
                        f"External MCP server '{server_id}' is disabled.",
                        error_code=ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED,
                    )
                return spec
        raise OpenDVError(
            f"External MCP server '{server_id}' is not registered.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    def _resolve_secrets(
        self, project_id: str, server: ExternalMcpServerSpec
    ) -> dict[str, str]:
        project = self._state.get_project(project_id)
        precedence = project.settings_snapshot.secret_precedence
        resolved: dict[str, str] = {}
        for ref in server.secret_refs:
            val = self._secrets.get_secret(ref, project_precedence=precedence)
            if not val:
                raise OpenDVError(
                    f"External MCP server '{server.server_id}' requires secret '{ref}'.",
                    error_code=ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
                )
            resolved[ref] = val
        return resolved

    async def _call_http(
        self,
        server: ExternalMcpServerSpec,
        tool_name: str,
        arguments: dict[str, Any],
        secret_values: dict[str, str],
        extra_headers: dict[str, str] | None,
    ) -> Any:
        if not server.url:
            raise OpenDVError(
                f"External MCP server '{server.server_id}' has no URL configured.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        safe_url = validate_external_mcp_url(server.url, dns_resolver=self._dns_resolver)
        headers: dict[str, str] = dict(extra_headers or {})
        if server.secret_refs and "Authorization" not in headers:
            first_secret = secret_values[server.secret_refs[0]]
            headers["Authorization"] = f"Bearer {first_secret}"

        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with (
            httpx2.AsyncClient(
                headers=headers,
                follow_redirects=False,
                timeout=float(server.timeout_seconds),
                transport=self._http_transport,
                event_hooks={"response": [reject_redirect_response_hook]},
            ) as hc,
            streamable_http_client(safe_url, http_client=hc) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            return await session.call_tool(tool_name, arguments)

    async def _call_stdio(
        self,
        server: ExternalMcpServerSpec,
        tool_name: str,
        arguments: dict[str, Any],
        secret_values: dict[str, str],
    ) -> Any:
        if not server.command:
            raise OpenDVError(
                f"External MCP stdio server '{server.server_id}' has no command configured.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        child_env = build_external_mcp_stdio_env(secret_values)
        with IsolatedTempCwd(prefix="opendv_ext_mcp_") as temp_cwd:
            params = StdioServerParameters(
                command=server.command[0],
                args=list(server.command[1:]),
                env=child_env,
                cwd=temp_cwd,
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    async def invoke_tool(
        self,
        *,
        project_id: str,
        run_id: str,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        is_scout_call: bool = True,
        stage: str = "step4_discovery",
        headers: dict[str, str] | None = None,
    ) -> ExternalMcpToolResult:
        """Validate gates, substitute handles, scrub PII, call external MCP, persist receipt."""
        server = self._get_server_spec(server_id)
        if stage not in server.allowed_stages:
            raise OpenDVError(
                f"External MCP server '{server_id}' is not allowed in stage '{stage}'.",
                error_code=ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED,
                run_id=run_id,
            )
        if is_scout_call and not server.allow_scout_calls:
            raise OpenDVError(
                f"External MCP server '{server_id}' requires allow_scout_calls=True.",
                error_code=ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED,
                run_id=run_id,
            )

        resolved_args, (doc_id, page_number), resolved_handle_ids = resolve_doc_page_handles(
            arguments,
            project_id=project_id,
            server=server,
            state_store=self._state,
            source_path_resolver=self._source_path_resolver,
            ocr_provider=self._ocr_provider,
        )
        scrubbed_args = scrub_outbound_arguments(
            resolved_args, resolved_handle_ids=resolved_handle_ids
        )
        secret_values = self._resolve_secrets(project_id, server)
        args_sha = sha256_jcs(scrubbed_args)

        try:
            async with asyncio.timeout(float(server.timeout_seconds)):
                if server.transport == "streamable_http":
                    raw_result = await self._call_http(
                        server, tool_name, scrubbed_args, secret_values, headers
                    )
                else:
                    raw_result = await self._call_stdio(
                        server, tool_name, scrubbed_args, secret_values
                    )
        except OpenDVError:
            raise
        except BaseExceptionGroup as eg:
            odv = _extract_opendv_error(eg)
            if odv is not None:
                raise odv from eg
            raise OpenDVError(
                f"External MCP call '{server_id}:{tool_name}' failed: {eg}",
                error_code=ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
                run_id=run_id,
            ) from eg
        except (TimeoutError, httpx2.HTTPError, OSError, RuntimeError, ValueError) as exc:
            raise OpenDVError(
                f"External MCP call '{server_id}:{tool_name}' failed: {exc}",
                error_code=ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
                run_id=run_id,
            ) from exc

        if getattr(raw_result, "isError", False) or getattr(raw_result, "is_error", False):
            err_txt = scrub_inbound_text(_extract_call_result_text(raw_result))
            raise OpenDVError(
                f"External MCP tool '{server_id}:{tool_name}' returned error: {err_txt}",
                error_code=ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
                run_id=run_id,
            )

        raw_text = _extract_call_result_text(raw_result)
        masked_excerpt = scrub_inbound_text(raw_text)
        content_sha = hashlib.sha256(masked_excerpt.encode("utf-8")).hexdigest()
        receipt_id = f"mcpr-{uuid.uuid4().hex[:12]}"

        receipt = ExternalMcpReceipt(
            receipt_id=receipt_id,
            server_id=server_id,
            tool_name=tool_name,
            doc_id=doc_id,
            page_number=page_number,
            args_sha256=args_sha,
            content_sha256=content_sha,
            masked_excerpt=masked_excerpt,
        )
        self._state.append_event_or_receipt(
            project_id,
            record_kind="mcp_receipt",
            run_id=run_id,
            payload=receipt,
        )
        return ExternalMcpToolResult(
            receipt_id=receipt_id,
            content_sha256=content_sha,
            masked_excerpt=masked_excerpt,
        )


__all__ = ["ExternalMcpGateway"]
