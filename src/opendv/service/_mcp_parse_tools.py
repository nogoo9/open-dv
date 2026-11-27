"""Layer-4 zero-LLM parsing MCP tools, spillover helpers, and shared MCP error/exec wrappers."""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from opendv.models.document_ir import (
    InspectResult,
    OutputFormat,
    ParsedDocumentIR,
    ParseOptions,
    ParseSpecResponse,
    RedactionMode,
    RenderPageResult,
    SinkClass,
    SpilloverRef,
    TargetProjection,
    ValidationDiagnostic,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.service.os_platform import (
    atomic_write_bytes,
    replace_with_retry,
    set_owner_only_permissions,
    verify_owner_only_permissions,
)
from opendv.service.parsing_svc import ParsingService, resolve_local_source_blob

PARSING_MCP_TOOL_NAMES: tuple[str, ...] = (
    "parse_spec",
    "parse_inspect",
    "parse_extract",
    "parse_render_page",
    "parse_validate_ir",
    "parse_export_ir",
)


def raise_mcp_tool_error(tool_name: str, exc: BaseException) -> ToolError:
    """Convert any handled exception into an MCP ToolError carrying ProblemDetails JSON."""
    if isinstance(exc, OpenDVError):
        mapped = exc
    elif isinstance(exc, ValidationError):
        mapped = OpenDVError(
            f"Invalid tool arguments for '{tool_name}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    else:
        mapped = OpenDVError(
            f"Tool '{tool_name}' failed: {exc}",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        )
    problem_json = mapped.to_problem_details(instance=f"mcp://tool/{tool_name}").model_dump_json()
    return ToolError(problem_json)


def run_mcp_sync[RetT](tool_name: str, fn: Callable[[], RetT]) -> RetT:
    """Execute a synchronous MCP tool callable with unified ProblemDetails mapping."""
    try:
        return fn()
    except (OpenDVError, ValidationError, ValueError, OSError, RuntimeError) as exc:
        raise raise_mcp_tool_error(tool_name, exc) from exc


async def run_mcp_async[RetT](tool_name: str, coro: Awaitable[RetT]) -> RetT:
    """Execute an asynchronous MCP tool coroutine with unified ProblemDetails mapping."""
    try:
        return await coro
    except (OpenDVError, ValidationError, ValueError, OSError, RuntimeError) as exc:
        raise raise_mcp_tool_error(tool_name, exc) from exc


def stream_jsonl_atomic(
    out_path: Path,
    lines: Iterable[str],
    *,
    owner_only: bool = True,
) -> tuple[Path, int, int, str]:
    """Stream UTF-8 JSONL lines atomically to `out_path` with owner-only ACLs."""
    target = out_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if owner_only and not verify_owner_only_permissions(target.parent):
        set_owner_only_permissions(target.parent)

    tmp_path: Path | None = None
    replaced = False
    hasher = hashlib.sha256()
    line_count = 0
    byte_size = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_path = Path(fh.name)
            for line in lines:
                normalized = line if line.endswith("\n") else f"{line}\n"
                raw = normalized.encode("utf-8")
                fh.write(raw)
                hasher.update(raw)
                line_count += 1
                byte_size += len(raw)
            fh.flush()
            os.fsync(fh.fileno())
        if owner_only:
            set_owner_only_permissions(tmp_path)
        replace_with_retry(tmp_path, target)
        replaced = True
        return target, line_count, byte_size, hasher.hexdigest()
    except OSError as exc:
        raise OpenDVError(
            f"Failed to stream JSONL to '{target.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        if not replaced and tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _write_spill_bytes(
    spill_dir: Path,
    ext: str,
    raw_bytes: bytes,
    fmt: OutputFormat | Literal["png"],
) -> SpilloverRef:
    target = (spill_dir / f"{uuid.uuid4().hex}.{ext}").resolve()
    atomic_write_bytes(target, raw_bytes, owner_only=True)
    return SpilloverRef(
        spill_path=str(target),
        byte_size=len(raw_bytes),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        format=fmt,
    )


def _emit_or_spill_jsonl(
    lines: Iterator[str],
    max_inline_bytes: int,
    spill_dir: Path,
) -> str | SpilloverRef:
    buffered: list[str] = []
    total_bytes = 0
    for line in lines:
        normalized = line if line.endswith("\n") else f"{line}\n"
        buffered.append(normalized)
        total_bytes += len(normalized.encode("utf-8"))
        if total_bytes > max_inline_bytes:
            spill_path = spill_dir / f"{uuid.uuid4().hex}.jsonl"
            target, _lc, bsize, sha = stream_jsonl_atomic(
                spill_path,
                itertools.chain(buffered, lines),
                owner_only=True,
            )
            return SpilloverRef(
                spill_path=str(target),
                byte_size=bsize,
                sha256=sha,
                format="jsonl",
            )
    return "".join(buffered)


def _to_json_native(projected: Any) -> Any:
    if isinstance(projected, str):
        return projected
    if isinstance(projected, BaseModel):
        return projected.model_dump(mode="json")
    if isinstance(projected, list):
        return [_to_json_native(item) for item in projected]
    if isinstance(projected, dict):
        return {str(k): _to_json_native(v) for k, v in projected.items()}
    return projected


def register_parsing_mcp_tools(
    mcp: MCPServer,
    *,
    parsing_service: ParsingService,
    allow_roots: Sequence[Path],
    allow_unredacted: bool,
    max_inline_bytes: int,
    spill_dir: Path,
    workspace_root: Path | None = None,
) -> None:
    """Register the 6 zero-LLM parsing MCP tools onto `mcp` with containment and spillover."""
    resolved_spill_dir = spill_dir.resolve()
    ir_allowed_roots = [*allow_roots, resolved_spill_dir]

    def _resolve_ir_arg(
        ir: dict[str, Any] | ParsedDocumentIR | None,
        ir_path: str | None,
    ) -> ParsedDocumentIR:
        if (ir is None) == (ir_path is None):
            raise OpenDVError(
                "Specify exactly one of 'ir' or 'ir_path'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if ir_path is not None:
            with resolve_local_source_blob(
                ir_path,
                workspace_root=workspace_root,
                allowed_roots=ir_allowed_roots,
            ) as blob:
                try:
                    raw_text = blob.path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise OpenDVError(
                        f"IR file '{blob.uri_sanitized}' is not valid UTF-8: {exc}",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    ) from exc
            return ParsedDocumentIR.model_validate_json(raw_text)
        assert ir is not None
        return ir if isinstance(ir, ParsedDocumentIR) else ParsedDocumentIR.model_validate(ir)

    @mcp.tool(
        name="parse_spec",
        description="Inspect document IR schemas, supported formats, projectors, and drivers.",
    )
    def parse_spec() -> ParseSpecResponse:
        return run_mcp_sync("parse_spec", parsing_service.get_spec)

    @mcp.tool(
        name="parse_inspect",
        description="Inspect local document page counts and scanned-page classification.",
    )
    def parse_inspect(
        uri: str,
        max_pages: int = 1000,
        timeout_ms: int = 30000,
    ) -> InspectResult:
        return run_mcp_sync(
            "parse_inspect",
            lambda: parsing_service.inspect_document(
                uri,
                options=ParseOptions(max_pages=max_pages, timeout_ms=timeout_ms),
            ),
        )

    @mcp.tool(
        name="parse_extract",
        description="Extract a local document into ParsedDocumentIR (MODEL_EGRESS redacted).",
    )
    def parse_extract(
        uri: str,
        pages: str | None = None,
        redact: bool = True,
        ocr: bool = False,
        max_pages: int = 1000,
        timeout_ms: int = 30000,
    ) -> ParsedDocumentIR | SpilloverRef:
        def _call() -> ParsedDocumentIR | SpilloverRef:
            if not redact and not allow_unredacted:
                raise OpenDVError(
                    "Unredacted parse_extract requires --allow-unredacted.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            page_list = (
                ParsingService.parse_page_range_spec(pages) if pages is not None else None
            )
            sink_class: SinkClass = "MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO"
            redaction: RedactionMode = "on" if redact else "off"
            ir = parsing_service.parse_document(
                uri,
                options=ParseOptions(
                    pages=page_list,
                    max_pages=max_pages,
                    timeout_ms=timeout_ms,
                    ocr=ocr,
                ),
                sink_class=sink_class,
                redaction=redaction,
            )
            raw_bytes = ir.model_dump_json().encode("utf-8")
            if len(raw_bytes) > max_inline_bytes:
                return _write_spill_bytes(resolved_spill_dir, "json", raw_bytes, "json")
            return ir

        return run_mcp_sync("parse_extract", _call)

    @mcp.tool(
        name="parse_render_page",
        description="Render a visual page PNG with fail-closed scan and PII blackout guards.",
    )
    def parse_render_page(
        uri: str,
        page_number: int,
        dpi: int = 150,
        ocr: bool = False,
        approve_scan: bool = False,
        redact: bool | None = None,
        timeout_ms: int = 30000,
    ) -> RenderPageResult:
        def _call() -> RenderPageResult:
            if not allow_unredacted:
                if redact is False:
                    raise OpenDVError(
                        "Unredacted parse_render_page requires --allow-unredacted.",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    )
                sink_class: SinkClass = "MODEL_EGRESS"
                redaction: RedactionMode = "on"
            else:
                use_unredacted = (redact is False) or (redact is None and approve_scan)
                sink_class = "LOCAL_HUMAN_IO" if use_unredacted else "MODEL_EGRESS"
                redaction = "off" if use_unredacted else "on"

            png_bytes, res = parsing_service.render_document_page(
                uri,
                page_number,
                sink_class=sink_class,
                redaction=redaction,
                approve_scan=approve_scan,
                options=ParseOptions(dpi=dpi, ocr=ocr, timeout_ms=timeout_ms),
            )
            b64 = base64.b64encode(png_bytes).decode("ascii")
            inline_res = res.model_copy(
                update={"png_b64": b64, "output_path": None, "spillover": None}
            )
            if len(inline_res.model_dump_json().encode("utf-8")) > max_inline_bytes:
                spill = _write_spill_bytes(resolved_spill_dir, "png", png_bytes, "png")
                return res.model_copy(
                    update={"png_b64": None, "output_path": None, "spillover": spill}
                )
            return inline_res

        return run_mcp_sync("parse_render_page", _call)

    @mcp.tool(
        name="parse_validate_ir",
        description="Validate canonical hashes, span bounds, and outline integrity of an IR.",
    )
    def parse_validate_ir(
        ir: dict[str, Any] | ParsedDocumentIR | None = None,
        ir_path: str | None = None,
    ) -> list[ValidationDiagnostic]:
        def _call() -> list[ValidationDiagnostic]:
            ir_obj = _resolve_ir_arg(ir, ir_path)
            return parsing_service.validate_ir(ir_obj)

        return run_mcp_sync("parse_validate_ir", _call)

    @mcp.tool(
        name="parse_export_ir",
        description="Project ParsedDocumentIR to document_map, markdown, or chunks (json/jsonl).",
    )
    def parse_export_ir(
        target: TargetProjection,
        ir: dict[str, Any] | ParsedDocumentIR | None = None,
        ir_path: str | None = None,
        format: OutputFormat = "json",
        redact: bool = True,
    ) -> dict[str, Any] | list[dict[str, Any]] | str | SpilloverRef:
        def _call() -> dict[str, Any] | list[dict[str, Any]] | str | SpilloverRef:
            if not redact and not allow_unredacted:
                raise OpenDVError(
                    "Unredacted parse_export_ir requires --allow-unredacted.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            ir_obj = _resolve_ir_arg(ir, ir_path)
            sink_class: SinkClass = "MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO"
            redaction: RedactionMode = "on" if redact else "off"

            if format == "jsonl":
                lines = parsing_service.stream_export(
                    ir_obj,
                    target=target,
                    sink_class=sink_class,
                    redaction=redaction,
                )
                return _emit_or_spill_jsonl(lines, max_inline_bytes, resolved_spill_dir)

            projected = parsing_service.export_ir(
                ir_obj,
                target=target,
                sink_class=sink_class,
                redaction=redaction,
            )
            json_val = _to_json_native(projected)
            payload_bytes = (
                json_val.encode("utf-8")
                if isinstance(json_val, str)
                else json.dumps(json_val).encode("utf-8")
            )
            if len(payload_bytes) > max_inline_bytes:
                return _write_spill_bytes(resolved_spill_dir, "json", payload_bytes, "json")
            return json_val  # type: ignore[no-any-return]

        return run_mcp_sync("parse_export_ir", _call)


__all__ = [
    "PARSING_MCP_TOOL_NAMES",
    "raise_mcp_tool_error",
    "register_parsing_mcp_tools",
    "run_mcp_async",
    "run_mcp_sync",
    "stream_jsonl_atomic",
]
