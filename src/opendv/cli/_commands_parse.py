"""Stateless local document parsing CLI subcommands: `opendv parse ...`."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from opendv.cli._output import (
    CLICK_EXCEPTION_TYPES,
    CliGlobalOptions,
    CliHandledError,
    coerce_cli_error,
    normalize_json_data,
    read_text_arg,
    render_error,
    render_success,
)
from opendv.models import ErrorCode, OpenDVError
from opendv.models.document_ir import (
    ParsedDocumentIR,
    ParseOptions,
    RedactionMode,
    SinkClass,
)
from opendv.service.os_platform import atomic_write_bytes, atomic_write_text

parse_app = typer.Typer(
    name="parse",
    help="Stateless local document inspection, parsing, rendering, validation, and export.",
    no_args_is_help=True,
)

_PARSE_HANDLED_EXC_TYPES: tuple[type[BaseException], ...] = (
    CliHandledError,
    OpenDVError,
    ValidationError,
    ValueError,
    OSError,
    RuntimeError,
    TypeError,
    *CLICK_EXCEPTION_TYPES,
)


class TargetProjectionOpt(StrEnum):
    """Supported IR projection targets."""

    DOCUMENT_MAP = "document_map"
    MARKDOWN = "markdown"
    CHUNKS = "chunks"


class OutputFormatOpt(StrEnum):
    """Supported export serialization formats."""

    JSON = "json"
    JSONL = "jsonl"


def _run_parse_action(
    ctx: typer.Context,
    action: Callable[[CliGlobalOptions], Any],
) -> None:
    obj = ctx.obj
    opts = obj if isinstance(obj, CliGlobalOptions) else CliGlobalOptions()
    if opts.api_url:
        err = OpenDVError(
            "`opendv parse` is local-only and does not support --api-url or OPENDV_API_URL.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
        raise typer.Exit(code=render_error(err, json_mode=opts.json_mode))

    try:
        result = action(opts)
    except _PARSE_HANDLED_EXC_TYPES as exc:
        err, data, instance = coerce_cli_error(exc)
        exit_code = render_error(err, json_mode=opts.json_mode, instance=instance, data=data)
        raise typer.Exit(code=exit_code) from exc

    render_success(result, json_mode=opts.json_mode)


def _load_ir_file(ir_path: Path) -> ParsedDocumentIR:
    raw_text = read_text_arg(str(ir_path), label="ir")
    return ParsedDocumentIR.model_validate_json(raw_text)


@parse_app.command("spec")
def parse_spec(ctx: typer.Context) -> None:
    """Emit machine-readable IR schemas, formats, projectors, and Tier-1 capabilities."""

    def _run(_opts: CliGlobalOptions) -> Any:
        from opendv.service.parsing_svc import ParsingService

        return ParsingService().get_spec()

    _run_parse_action(ctx, _run)


@parse_app.command("inspect")
def parse_inspect(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Local file path or file URI to inspect.")],
    allow_root: Annotated[
        list[Path] | None,
        typer.Option("--allow-root", help="Repeatable allowed workspace root directory."),
    ] = None,
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", help="Maximum page budget hint."),
    ] = 1000,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", help="Parser timeout in milliseconds."),
    ] = 30000,
) -> None:
    """Inspect document page count and digital-vs-scanned classification."""

    def _run(_opts: CliGlobalOptions) -> Any:
        from opendv.service.parsing_svc import ParsingService

        return ParsingService(allowed_roots=allow_root).inspect_document(
            path,
            options=ParseOptions(max_pages=max_pages, timeout_ms=timeout_ms),
        )

    _run_parse_action(ctx, _run)


@parse_app.command("extract")
def parse_extract(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Local file path or file URI to parse.")],
    pages: Annotated[
        str | None,
        typer.Option("--pages", help="1-based page range specification (e.g. 1-5,8)."),
    ] = None,
    redact: Annotated[
        bool,
        typer.Option("--redact", help="Apply MODEL_EGRESS PII masking to extracted IR."),
    ] = False,
    ocr: Annotated[
        bool,
        typer.Option("--ocr", help="Enable optional OCR extraction on scanned pages."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write ParsedDocumentIR JSON atomically to file."),
    ] = None,
    allow_root: Annotated[
        list[Path] | None,
        typer.Option("--allow-root", help="Repeatable allowed workspace root directory."),
    ] = None,
    max_pages: Annotated[
        int,
        typer.Option("--max-pages", help="Maximum extracted pages allowed."),
    ] = 1000,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", help="Parser timeout in milliseconds."),
    ] = 30000,
) -> None:
    """Parse a local document into canonical ParsedDocumentIR."""

    def _run(_opts: CliGlobalOptions) -> Any:
        from opendv.service.parsing_svc import ParsingService

        page_list = ParsingService.parse_page_range_spec(pages) if pages is not None else None
        sink_class: SinkClass = "MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO"
        redaction: RedactionMode = "on" if redact else "off"
        ir = ParsingService(allowed_roots=allow_root).parse_document(
            path,
            options=ParseOptions(
                pages=page_list,
                max_pages=max_pages,
                timeout_ms=timeout_ms,
                ocr=ocr,
            ),
            sink_class=sink_class,
            redaction=redaction,
        )
        if out is not None:
            target = out.resolve()
            serialized = ir.model_dump_json(indent=2) + "\n"
            atomic_write_text(target, serialized)
            raw_bytes = serialized.encode("utf-8")
            return {
                "output_path": str(target),
                "byte_size": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "format_id": ir.provenance.format_id,
                "page_count": len(ir.pages),
                "block_count": len(ir.blocks),
                "redacted": ir.provenance.redacted,
            }
        return ir

    _run_parse_action(ctx, _run)


@parse_app.command("render-page")
def parse_render_page(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Local document path or file URI to render.")],
    page: Annotated[
        int,
        typer.Option("--page", help="1-based physical page number to render."),
    ],
    dpi: Annotated[
        int,
        typer.Option("--dpi", help="Render resolution in DPI (max 300)."),
    ] = 150,
    redact: Annotated[
        bool,
        typer.Option("--redact", help="Apply MODEL_EGRESS PII blackout rectangles."),
    ] = False,
    ocr: Annotated[
        bool,
        typer.Option("--ocr", help="Enable optional OCR on scanned pages."),
    ] = False,
    approve_scan: Annotated[
        bool,
        typer.Option("--approve-scan", help="Allow unredacted scanned page viewing."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write rendered PNG atomically to file."),
    ] = None,
    allow_root: Annotated[
        list[Path] | None,
        typer.Option("--allow-root", help="Repeatable allowed workspace root directory."),
    ] = None,
    timeout_ms: Annotated[
        int,
        typer.Option("--timeout-ms", help="Parser timeout in milliseconds."),
    ] = 30000,
) -> None:
    """Render a document page PNG with scan gating and optional PII blackout."""

    def _run(_opts: CliGlobalOptions) -> Any:
        from opendv.service.parsing_svc import ParsingService

        sink_class: SinkClass = "MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO"
        redaction: RedactionMode = "on" if redact else "off"
        png_bytes, res = ParsingService(allowed_roots=allow_root).render_document_page(
            path,
            page,
            sink_class=sink_class,
            redaction=redaction,
            approve_scan=approve_scan,
            options=ParseOptions(dpi=dpi, ocr=ocr, timeout_ms=timeout_ms),
        )
        if out is not None:
            target = out.resolve()
            atomic_write_bytes(target, png_bytes)
            return res.model_copy(update={"output_path": str(target), "png_b64": None})
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return res.model_copy(update={"output_path": None, "png_b64": b64})

    _run_parse_action(ctx, _run)


@parse_app.command("validate")
def parse_validate(
    ctx: typer.Context,
    ir_path: Annotated[Path, typer.Argument(help="Path to ParsedDocumentIR JSON file.")],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write ValidationDiagnostic list JSON to file."),
    ] = None,
) -> None:
    """Validate ParsedDocumentIR canonical hashes, span bounds, and outline structure."""

    def _run(_opts: CliGlobalOptions) -> Any:
        from opendv.service.parsing_svc import ParsingService

        ir = _load_ir_file(ir_path)
        diags = ParsingService().validate_ir(ir)
        if out is not None:
            serialized = json.dumps([d.model_dump(mode="json") for d in diags], indent=2) + "\n"
            atomic_write_text(out.resolve(), serialized)
        if any(d.severity == "error" for d in diags):
            raise CliHandledError(
                OpenDVError(
                    f"ParsedDocumentIR failed validation with {len(diags)} diagnostic(s).",
                    error_code=ErrorCode.VERIFICATION_GATE_FAILED,
                ),
                data=diags,
            )
        return diags

    _run_parse_action(ctx, _run)


@parse_app.command("export")
def parse_export(
    ctx: typer.Context,
    ir_path: Annotated[Path, typer.Argument(help="Path to ParsedDocumentIR JSON file.")],
    target: Annotated[
        TargetProjectionOpt,
        typer.Option("--target", help="Target projection: document_map, markdown, or chunks."),
    ],
    fmt: Annotated[
        OutputFormatOpt,
        typer.Option("--format", help="Output format: json or jsonl."),
    ] = OutputFormatOpt.JSON,
    redact: Annotated[
        bool,
        typer.Option("--redact", help="Apply MODEL_EGRESS PII masking during projection."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write projected output atomically to file."),
    ] = None,
) -> None:
    """Project ParsedDocumentIR into document_map, markdown, or streaming chunks JSONL."""

    def _run(opts: CliGlobalOptions) -> Any:
        from opendv.service._mcp_parse_tools import stream_jsonl_atomic
        from opendv.service.parsing_svc import ParsingService

        if opts.json_mode and fmt == OutputFormatOpt.JSONL and out is None:
            raise OpenDVError(
                "`--json --format jsonl` requires `--out FILE`.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        ir = _load_ir_file(ir_path)
        svc = ParsingService()
        sink_class: SinkClass = "MODEL_EGRESS" if redact else "LOCAL_HUMAN_IO"
        redaction: RedactionMode = "on" if redact else "off"

        if fmt == OutputFormatOpt.JSONL:
            lines = svc.stream_export(
                ir,
                target=target.value,
                sink_class=sink_class,
                redaction=redaction,
            )
            if out is not None:
                written_path, line_count, byte_size, sha256_hex = stream_jsonl_atomic(out, lines)
                return {
                    "output_path": str(written_path),
                    "target": target.value,
                    "format": OutputFormatOpt.JSONL.value,
                    "line_count": line_count,
                    "byte_size": byte_size,
                    "sha256": sha256_hex,
                    "redacted": redact or ir.provenance.redacted,
                }
            for line in lines:
                typer.echo(line.rstrip("\n"))
            return None

        projected = svc.export_ir(
            ir,
            target=target.value,
            sink_class=sink_class,
            redaction=redaction,
        )
        if out is not None:
            out_target = out.resolve()
            text = (
                projected
                if isinstance(projected, str)
                else json.dumps(normalize_json_data(projected), indent=2) + "\n"
            )
            atomic_write_text(out_target, text)
            raw_bytes = text.encode("utf-8")
            return {
                "output_path": str(out_target),
                "target": target.value,
                "format": OutputFormatOpt.JSON.value,
                "byte_size": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "redacted": redact or ir.provenance.redacted,
            }
        return projected

    _run_parse_action(ctx, _run)


__all__ = [
    "OutputFormatOpt",
    "TargetProjectionOpt",
    "parse_app",
]
