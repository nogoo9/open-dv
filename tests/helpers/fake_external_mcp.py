"""LoopbackFakeExternalMcpServer helper for UAT-V25-SVC-04 and Step 4 integration tests."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import socket
import threading
from collections.abc import Iterator
from types import TracebackType
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent

from opendv.models.sources import ExternalMcpServerSpec


class _NoSignalServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class LoopbackFakeExternalMcpServer:
    """Thread-hosted loopback Streamable-HTTP MCP server for deterministic gateway tests."""

    def __init__(
        self,
        *,
        server_id: str = "ext_doc",
        expected_token: str | None = None,
        response_prefix: str = "Clause summary",
        echo_pii_in_response: str | None = None,
        redirect_location: str | None = None,
        bind_host: str | None = None,
    ) -> None:
        self.server_id = server_id
        self.expected_token = expected_token
        self.response_prefix = response_prefix
        self.echo_pii_in_response = echo_pii_in_response
        self.redirect_location = redirect_location
        self.bind_host = bind_host or ipaddress.IPv4Address(0x7F000001).compressed
        self.port: int = 0
        self.received_calls: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready_event = threading.Event()
        self._startup_error: BaseException | None = None

    @property
    def _url_host(self) -> str:
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(self.bind_host).version == 6:
                return f"[{self.bind_host}]"
        return self.bind_host

    @property
    def url(self) -> str:
        return f"http://{self._url_host}:{self.port}/mcp"

    def server_spec(
        self,
        *,
        allow_scout_calls: bool = True,
        allow_document_pages: bool = True,
        secret_refs: list[str] | None = None,
        allowed_stages: list[str] | None = None,
        timeout_seconds: int = 10,
        enabled: bool = True,
    ) -> ExternalMcpServerSpec:
        """Return an ExternalMcpServerSpec targeting this loopback test server."""
        return ExternalMcpServerSpec(
            server_id=self.server_id,
            transport="streamable_http",
            url=self.url,
            secret_refs=list(secret_refs or []),
            allowed_stages=list(allowed_stages or ["step4_discovery"]),  # type: ignore[arg-type]
            allow_scout_calls=allow_scout_calls,
            allow_document_pages=allow_document_pages,
            timeout_seconds=timeout_seconds,
            enabled=enabled,
        )

    def _build_mcp(self) -> MCPServer:
        srv = MCPServer(name=f"fake-{self.server_id}")

        @srv.tool()
        def extract_clause(query: str, doc_page: dict[str, Any] | None = None) -> str:
            """Echo scrubbed query and doc_page metadata while recording received payload."""
            self.received_calls.append(
                {"tool_name": "extract_clause", "query": query, "doc_page": doc_page}
            )
            extra = f" | {self.echo_pii_in_response}" if self.echo_pii_in_response else ""
            has_png = bool(doc_page and doc_page.get("redacted_png_b64"))
            return f"{self.response_prefix}: query={query} has_png={has_png}{extra}"

        @srv.tool()
        def lookup_entity(symbol: str) -> str:
            """Simple metadata lookup tool."""
            self.received_calls.append({"tool_name": "lookup_entity", "symbol": symbol})
            extra = f" | {self.echo_pii_in_response}" if self.echo_pii_in_response else ""
            return f"{self.response_prefix}: symbol={symbol}{extra}"

        @srv.tool()
        def fail_lookup(symbol: str) -> CallToolResult:
            """Return an error CallToolResult optionally carrying PII for inbound scrub tests."""
            self.received_calls.append({"tool_name": "fail_lookup", "symbol": symbol})
            extra = f" | {self.echo_pii_in_response}" if self.echo_pii_in_response else ""
            return CallToolResult(
                content=[TextContent(type="text", text=f"Lookup failed for {symbol}{extra}")],
                is_error=True,
            )

        return srv

    async def _serve_async(self, sock: socket.socket) -> None:
        assert self._loop is not None
        self._stop_event = asyncio.Event()
        mcp_srv = self._build_mcp()
        sec = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{self._url_host}:{self.port}", self._url_host],
        )
        mgr = StreamableHTTPSessionManager(
            mcp_srv._lowlevel_server,
            json_response=True,
            stateless=True,
            security_settings=sec,
        )

        async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                return
            if self.redirect_location is not None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 302,
                        "headers": [(b"location", self.redirect_location.encode("ascii"))],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            if self.expected_token is not None:
                auth_hdr = ""
                for k, v in scope.get("headers") or ():
                    if k.lower() == b"authorization":
                        auth_hdr = v.decode("latin-1")
                        break
                expected = f"Bearer {self.expected_token}"
                if not hmac.compare_digest(auth_hdr.encode("utf-8"), expected.encode("utf-8")):
                    body = json.dumps({"detail": "Unauthorized"}).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
            await mgr.handle_request(scope, receive, send)

        async with mgr.run():
            cfg = uvicorn.Config(
                app=_app,
                host=self.bind_host,
                port=self.port,
                log_level="warning",
                access_log=False,
                interface="asgi3",
            )
            server = _NoSignalServer(cfg)
            serve_task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                if serve_task.done():
                    break
                await asyncio.sleep(0.01)
            self._ready_event.set()
            await self._stop_event.wait()
            server.should_exit = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(serve_task, timeout=5.0)

    def _thread_main(self, sock: socket.socket) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_async(sock))
        except BaseException as exc:
            self._startup_error = exc
            self._ready_event.set()
        finally:
            loop.close()
            with contextlib.suppress(OSError):
                sock.close()

    def start(self) -> str:
        """Start the loopback server in a daemon thread and return its MCP URL."""
        if self._thread is not None:
            return self.url
        family = socket.AF_INET
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(self.bind_host).version == 6:
                family = socket.AF_INET6
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind_host, 0))
        sock.listen(32)
        sock.setblocking(False)
        self.port = int(sock.getsockname()[1])
        self._ready_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._thread_main, args=(sock,), daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=10.0)
        if self._startup_error is not None:
            raise self._startup_error
        return self.url

    def stop(self) -> None:
        """Stop the background server thread cleanly."""
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._loop = None
        self._stop_event = None

    def __enter__(self) -> LoopbackFakeExternalMcpServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


__all__ = ["LoopbackFakeExternalMcpServer"]
