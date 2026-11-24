"""Role-scoped Step 4 JobMcpBroker (pure-ASGI + uvicorn) and ServiceScoutToolDispatcher."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import secrets
import socket
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any, Literal

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    RunEventSink,
    ScoutToolDispatcher,
)
from opendv.models.diagnostics import ClarificationQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.policy import STEP4_DEFAULT_BUDGET, HarnessBudget
from opendv.models.sources import PageTextView, TableProfile
from opendv.models.wire_dtos_discovery import (
    ClientAdditiveOp,
    ConceptSummary,
    DeltaStageResp,
    ExternalMcpToolResult,
    FiboQueryResp,
    HoldAck,
    ImageOcrResult,
    ScoutDigest,
)
from opendv.service._mcp_ports import (
    Bm25MatcherPort,
    ExternalMcpGatewayPort,
    OcrProviderFn,
    ProjectStatePort,
    default_loopback_host,
)
from opendv.service._mcp_tools import (
    BrokerBudgetTracker,
    BrokerHoldCoordinator,
    BrokerToolContext,
    coord_ask_domain_clarification,
    coord_dispatch_scout,
    coord_propose_ontology_delta,
    coord_search_concepts,
    coord_search_fibo,
    scout_call_external_mcp,
    scout_inspect_table_cluster,
    scout_inspect_table_profile,
    scout_read_document_pages,
    scout_view_page_image,
)

_SCOUT_INSTRUCTIONS: dict[Literal["table_cluster", "page_range"], str] = {
    "table_cluster": (
        "Inspect assigned structured table clusters using inspect_table_cluster and "
        "inspect_table_profile only, and return a ScoutDigest JSON object."
    ),
    "page_range": (
        "Inspect assigned document pages using read_document_pages and approved view_page_image "
        "only, and return a ScoutDigest JSON object."
    ),
}


class _NoSignalUvicornServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class ServiceScoutToolDispatcher(ScoutToolDispatcher):
    """Dispatches Step 4 @table-cluster-scout and @page-range-scout via asyncio.gather."""

    def __init__(
        self,
        *,
        harness: AgentHarnessPort,
        scout_url: str,
        scout_token: str,
        budget: HarnessBudget = STEP4_DEFAULT_BUDGET,
        event_sink: RunEventSink | None = None,
    ) -> None:
        self._harness = harness
        self._scout_url = scout_url
        self._scout_token = scout_token
        self._budget = budget
        self._event_sink = event_sink

    async def dispatch_scout(
        self,
        scout_kind: Literal["table_cluster", "page_range"],
        target_ids: Sequence[str],
        *,
        run_id: str,
    ) -> list[ScoutDigest]:
        """Execute isolated scout turns concurrently via asyncio.gather."""
        if not target_ids:
            return []
        agent_id = "table-cluster-scout" if scout_kind == "table_cluster" else "page-range-scout"
        sys_inst = _SCOUT_INSTRUCTIONS[scout_kind]
        allowed = (
            ["inspect_table_cluster", "inspect_table_profile", "call_external_mcp"]
            if scout_kind == "table_cluster"
            else ["read_document_pages", "view_page_image", "call_external_mcp"]
        )

        async def _run_one(tid: str) -> ScoutDigest:
            req = AgentTurnRequest(
                run_id=run_id,
                agent_id=agent_id,
                step="step4",
                prompt=f"Inspect {scout_kind} target '{tid}' and return ScoutDigest.",
                system_instructions=sys_inst,
                output_schema=ScoutDigest,
                model_tier="STANDARD_WORKER",
                role_kind="scout",
                role_token=self._scout_token,
                broker_url=self._scout_url,
                allowed_tools=allowed,
                budget=self._budget,
                cluster_id=tid if scout_kind == "table_cluster" else None,
            )
            res = await self._harness.run_turn(req, event_sink=self._event_sink)
            out = res.structured_output
            if isinstance(out, ScoutDigest):
                return out
            if isinstance(out, dict):
                return ScoutDigest.model_validate(out)
            if res.text_output:
                return ScoutDigest.model_validate_json(res.text_output)
            raise OpenDVError(
                f"Scout '{agent_id}' returned no ScoutDigest for '{tid}'.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
                run_id=run_id,
            )

        return list(await asyncio.gather(*(_run_one(tid) for tid in target_ids)))


def _build_coord_server(ctx: BrokerToolContext) -> MCPServer:
    srv = MCPServer(name="opendv-step4-coord")

    @srv.tool()
    def search_concepts(query: str, include_seeds: bool = True) -> list[ConceptSummary]:
        """Search committed Graph A, staged in-run candidates, memory, and workspace seeds."""
        return coord_search_concepts(ctx, query, include_seeds=include_seeds)

    @srv.tool()
    def search_fibo(
        query: str, domain_namespace: str | None = None, top_k: int = 10
    ) -> FiboQueryResp:
        """Query precompiled FIBO BM25 ontology terms."""
        return coord_search_fibo(ctx, query, domain_namespace=domain_namespace, top_k=top_k)

    @srv.tool()
    def propose_ontology_delta(ops: list[ClientAdditiveOp]) -> DeltaStageResp:
        """Stage additive ontology delta operations for Steward review."""
        return coord_propose_ontology_delta(ctx, ops)

    @srv.tool()
    async def ask_domain_clarification(questions: list[ClarificationQuestion]) -> HoldAck:
        """Drain active scouts, checkpoint HOLD_CHECKPOINTED, and release job lease."""
        return await coord_ask_domain_clarification(ctx, questions)

    @srv.tool()
    async def dispatch_scout(
        scout_kind: Literal["table_cluster", "page_range"], target_ids: list[str]
    ) -> list[ScoutDigest]:
        """Dispatch isolated read-only scouts for table clusters or document page ranges."""
        return await coord_dispatch_scout(ctx, scout_kind, target_ids)

    return srv


def _build_scout_server(ctx: BrokerToolContext) -> MCPServer:
    srv = MCPServer(name="opendv-step4-scout")

    @srv.tool()
    def inspect_table_cluster(cluster_id: str) -> list[TableProfile]:
        """Return masked table profiles for all tables in cluster_id."""
        return scout_inspect_table_cluster(ctx, cluster_id)

    @srv.tool()
    def inspect_table_profile(table_id: str) -> TableProfile:
        """Return a single masked table profile (<= 5 samples per column)."""
        return scout_inspect_table_profile(ctx, table_id)

    @srv.tool()
    def read_document_pages(doc_id: str, page_start: int, page_end: int) -> list[PageTextView]:
        """Read PII-masked normalized page text views."""
        return scout_read_document_pages(ctx, doc_id, page_start, page_end)

    @srv.tool()
    def view_page_image(doc_id: str, page_number: int) -> ImageOcrResult:
        """Inspect a Steward-approved scanned page after OCR box redaction."""
        return scout_view_page_image(ctx, doc_id, page_number)

    @srv.tool()
    async def call_external_mcp(
        server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ExternalMcpToolResult:
        """Invoke a registered external MCP server through ExternalMcpGateway."""
        return await scout_call_external_mcp(ctx, server_id, tool_name, arguments)

    return srv


async def _send_json_error(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class JobMcpBroker:
    """Role-scoped Step 4 MCP HTTP broker (/mcp/<run_nonce>/coord vs /mcp/<run_nonce>/scout)."""

    def __init__(
        self,
        *,
        project_id: str,
        run_id: str,
        state_store: ProjectStatePort,
        bm25_matcher: Bm25MatcherPort | None = None,
        external_gateway: ExternalMcpGatewayPort | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
        budget: HarnessBudget = STEP4_DEFAULT_BUDGET,
        event_sink: RunEventSink | None = None,
        workspace_seeds: list[ConceptSummary] | None = None,
        parsed_pages: dict[str, list[str]] | None = None,
        source_path_resolver: Callable[[str, str], Any] | None = None,
        ocr_provider: OcrProviderFn | None = None,
        ontology_rev_id: str | None = None,
        sources_fingerprint: str = "fp-step4",
        bind_host: str | None = None,
        port: int = 0,
    ) -> None:
        self.bind_host = bind_host if bind_host is not None else default_loopback_host()
        self.requested_port = port
        self.port: int = port
        self.run_nonce = secrets.token_hex(16)
        self.coord_token = secrets.token_urlsafe(32)
        self.scout_token = secrets.token_urlsafe(32)
        self.coord_path = f"/mcp/{self.run_nonce}/coord"
        self.scout_path = f"/mcp/{self.run_nonce}/scout"

        self.hold_coord = BrokerHoldCoordinator(
            project_id=project_id,
            run_id=run_id,
            state_store=state_store,
            event_sink=event_sink,
        )
        self.budget_tracker = BrokerBudgetTracker(budget=budget)
        self.context = BrokerToolContext(
            project_id=project_id,
            run_id=run_id,
            state_store=state_store,
            bm25_matcher=bm25_matcher,
            external_gateway=external_gateway,
            scout_dispatcher=scout_dispatcher,
            budget_tracker=self.budget_tracker,
            hold_coord=self.hold_coord,
            workspace_seeds=list(workspace_seeds or []),
            parsed_pages=dict(parsed_pages or {}),
            source_path_resolver=source_path_resolver,
            ocr_provider=ocr_provider,
            ontology_rev_id=ontology_rev_id,
            sources_fingerprint=sources_fingerprint,
        )
        self.coord_server = _build_coord_server(self.context)
        self.scout_server = _build_scout_server(self.context)
        self._coord_mgr: StreamableHTTPSessionManager | None = None
        self._scout_mgr: StreamableHTTPSessionManager | None = None
        self._socket: socket.socket | None = None
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._stack: contextlib.AsyncExitStack | None = None

    @property
    def _url_host(self) -> str:
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(self.bind_host).version == 6:
                return f"[{self.bind_host}]"
        return self.bind_host

    @property
    def coord_url(self) -> str:
        return f"http://{self._url_host}:{self.port}{self.coord_path}"

    @property
    def scout_url(self) -> str:
        return f"http://{self._url_host}:{self.port}{self.scout_path}"

    def set_scout_dispatcher(self, dispatcher: ScoutToolDispatcher) -> None:
        """Bind or replace ScoutToolDispatcher after broker URLs are resolved."""
        self.context.scout_dispatcher = dispatcher

    def build_scout_dispatcher(
        self, harness: AgentHarnessPort, *, event_sink: RunEventSink | None = None
    ) -> ServiceScoutToolDispatcher:
        """Create and bind a ServiceScoutToolDispatcher wired to this broker's scout_url."""
        dispatcher = ServiceScoutToolDispatcher(
            harness=harness,
            scout_url=self.scout_url,
            scout_token=self.scout_token,
            budget=self.budget_tracker.budget,
            event_sink=event_sink,
        )
        self.context.scout_dispatcher = dispatcher
        return dispatcher

    def _authenticate(self, scope: dict[str, Any], expected_token: str) -> tuple[int, str] | None:
        raw_headers = scope.get("headers") or ()
        auth_val: str | None = None
        for k, v in raw_headers:
            if k.lower() == b"authorization":
                auth_val = v.decode("latin-1")
                break
        if not auth_val or not auth_val.startswith("Bearer "):
            return (401, "Missing Bearer authorization token.")
        presented = auth_val[7:]
        if not hmac.compare_digest(presented.encode("utf-8"), expected_token.encode("utf-8")):
            return (403, "Forbidden role token for this MCP endpoint.")
        return None

    async def _asgi_app(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return
        path = str(scope.get("path") or "")
        if path == self.coord_path:
            err = self._authenticate(scope, self.coord_token)
            if err is not None:
                await _send_json_error(send, err[0], err[1])
                return
            assert self._coord_mgr is not None
            await self._coord_mgr.handle_request(scope, receive, send)
            return
        if path == self.scout_path:
            err = self._authenticate(scope, self.scout_token)
            if err is not None:
                await _send_json_error(send, err[0], err[1])
                return
            assert self._scout_mgr is not None
            await self._scout_mgr.handle_request(scope, receive, send)
            return
        await _send_json_error(send, 404, "Unknown MCP broker endpoint.")

    async def start(self) -> None:
        """Bind loopback socket and start pure-ASGI uvicorn broker server."""
        if self._server is not None:
            return
        family = socket.AF_INET
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(self.bind_host).version == 6:
                family = socket.AF_INET6
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind_host, self.requested_port))
        sock.listen(64)
        sock.setblocking(False)
        self._socket = sock
        self.port = int(sock.getsockname()[1])

        sec = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{self._url_host}:{self.port}", self._url_host],
        )
        self._coord_mgr = StreamableHTTPSessionManager(
            self.coord_server._lowlevel_server,
            json_response=True,
            stateless=True,
            security_settings=sec,
        )
        self._scout_mgr = StreamableHTTPSessionManager(
            self.scout_server._lowlevel_server,
            json_response=True,
            stateless=True,
            security_settings=sec,
        )
        stack = contextlib.AsyncExitStack()
        await stack.enter_async_context(self._coord_mgr.run())
        await stack.enter_async_context(self._scout_mgr.run())
        self._stack = stack

        cfg = uvicorn.Config(
            app=self._asgi_app,
            host=self.bind_host,
            port=self.port,
            log_level="warning",
            access_log=False,
            interface="asgi3",
        )
        server = _NoSignalUvicornServer(cfg)
        self._server = server
        self._serve_task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if self._serve_task.done():
                await self._serve_task
                break
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        """Shut down uvicorn server, session managers, and socket cleanly."""
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            if not self._serve_task.done():
                self._serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._serve_task
            self._serve_task = None
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
            self._socket = None
        self._server = None

    async def __aenter__(self) -> JobMcpBroker:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()


__all__ = [
    "JobMcpBroker",
    "ServiceScoutToolDispatcher",
]
