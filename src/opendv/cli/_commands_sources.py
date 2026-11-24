"""Source inspection, attachment, note upload, and OCR approval CLI subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from opendv.cli._client import CliClient
from opendv.cli._output import coerce_option_dict, parse_csv_list, parse_int_csv, parse_json_arg
from opendv.cli.commands_core import ProjectOpt, SecretRefsOpt, run_cli_action
from opendv.models import (
    ErrorCode,
    OpenDVError,
    ScanApprovalReq,
    SourceAttachReq,
    SourceInspectReq,
    SourceReattachReq,
)

UriOpt = Annotated[str, typer.Option("--uri", help="Source URI or local path.")]
KindOpt = Annotated[str, typer.Option("--kind", help="Connector adapter kind.")]
OptionsJsonOpt = Annotated[
    str | None,
    typer.Option("--options-json", help="Connector options JSON object or file path."),
]

source_app = typer.Typer(
    name="source", help="Inspect, attach, manage, and detach sources.", no_args_is_help=True
)


@source_app.command("adapters")
def source_adapters(ctx: typer.Context) -> None:
    """List supported data and document source adapters."""
    run_cli_action(ctx, lambda c: c.list_source_adapters())


@source_app.command("inspect")
def source_inspect(
    ctx: typer.Context,
    kind: KindOpt,
    uri: UriOpt,
    options_json: OptionsJsonOpt = None,
    secret_ref: SecretRefsOpt = None,
) -> None:
    """Preview tables and redaction profile for a candidate source."""

    def _run(client: CliClient) -> Any:
        opts_raw = parse_json_arg(options_json, label="--options-json", expect_type=dict)
        req = SourceInspectReq(
            kind=kind,
            uri_or_path=uri,
            options=coerce_option_dict(opts_raw, label="--options-json"),
            secret_refs=list(secret_ref or []),
        )
        return client.inspect_source(req)

    run_cli_action(ctx, _run)


@source_app.command("list")
def source_list(ctx: typer.Context, project: ProjectOpt) -> None:
    """List sources attached to a project."""
    run_cli_action(ctx, lambda c: c.list_sources(project))


@source_app.command("attach")
def source_attach(
    ctx: typer.Context,
    project: ProjectOpt,
    kind: KindOpt,
    uri: UriOpt,
    selected_tables: Annotated[
        str | None, typer.Option("--selected-tables", help="Comma-separated table subset.")
    ] = None,
    profile_timeout: Annotated[int, typer.Option("--profile-timeout")] = 30,
    options_json: OptionsJsonOpt = None,
    secret_ref: SecretRefsOpt = None,
) -> None:
    """Attach and profile a data/document source for a project."""

    def _run(client: CliClient) -> Any:
        opts_raw = parse_json_arg(options_json, label="--options-json", expect_type=dict)
        req = SourceAttachReq(
            kind=kind,
            uri_or_filename=uri,
            options=coerce_option_dict(opts_raw, label="--options-json"),
            secret_refs=list(secret_ref or []),
            selected_tables=parse_csv_list(selected_tables),
            profile_timeout_seconds=profile_timeout,
        )
        return client.attach_source(project, req)

    run_cli_action(ctx, _run)


@source_app.command("reattach")
def source_reattach(
    ctx: typer.Context,
    project: ProjectOpt,
    source: Annotated[str, typer.Option("--source", help="Source identifier.")],
    uri: Annotated[str | None, typer.Option("--uri")] = None,
    selected_tables: Annotated[str | None, typer.Option("--selected-tables")] = None,
    options_json: OptionsJsonOpt = None,
    secret_ref: SecretRefsOpt = None,
) -> None:
    """Reattach or narrow selected tables on an existing source."""

    def _run(client: CliClient) -> Any:
        opts_raw = parse_json_arg(options_json, label="--options-json", expect_type=dict)
        req = SourceReattachReq(
            uri_or_filename=uri,
            options=(
                coerce_option_dict(opts_raw, label="--options-json")
                if opts_raw is not None
                else None
            ),
            secret_refs=list(secret_ref) if secret_ref is not None else None,
            selected_tables=parse_csv_list(selected_tables),
        )
        return client.reattach_source(project, source, req)

    run_cli_action(ctx, _run)


@source_app.command("note")
def source_note(
    ctx: typer.Context,
    project: ProjectOpt,
    path: Annotated[Path | None, typer.Option("--path", help="Markdown/text note file.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="Inline domain note text.")] = None,
) -> None:
    """Attach a domain note document from a local file or inline `--text`."""

    def _run(client: CliClient) -> Any:
        if (path is None and text is None) or (path is not None and text is not None):
            raise OpenDVError(
                "Specify exactly one of --path or --text.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if path is not None:
            resolved = path.expanduser()
            if not resolved.is_file():
                raise OpenDVError(
                    f"Domain note file not found: '{resolved}'.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
            return client.upload_domain_note(
                project, filename=resolved.name, content=resolved.read_bytes()
            )
        assert text is not None
        return client.upload_domain_note(
            project, filename="domain-note.md", content=text.encode("utf-8")
        )

    run_cli_action(ctx, _run)


@source_app.command("approve-scan")
def source_approve_scan(
    ctx: typer.Context,
    project: ProjectOpt,
    doc: Annotated[str, typer.Option("--doc", help="Document source identifier.")],
    pages: Annotated[str, typer.Option("--pages", help="Comma-separated 1-based page numbers.")],
    rationale: Annotated[str, typer.Option("--rationale", help="Audit rationale for approval.")],
    allow_unredacted_external_ocr: Annotated[
        bool,
        typer.Option(
            "--allow-unredacted-external-ocr",
            help="Explicit opt-in if external vision/OCR is enabled on redacted scans.",
        ),
    ] = False,
) -> None:
    """Approve scanned PDF pages for vision/OCR extraction."""

    def _run(client: CliClient) -> Any:
        req = ScanApprovalReq(
            page_numbers=parse_int_csv(pages, label="--pages"),
            rationale=rationale,
            allow_unredacted_external_ocr=allow_unredacted_external_ocr,
        )
        return client.approve_scan_pages(project, doc, req)

    run_cli_action(ctx, _run)


@source_app.command("detach")
def source_detach(
    ctx: typer.Context,
    project: ProjectOpt,
    source: Annotated[str, typer.Option("--source", help="Source identifier.")],
) -> None:
    """Detach an uncommitted source from a project."""
    run_cli_action(ctx, lambda c: c.detach_source(project, source))
