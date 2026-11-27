"""CLI command implementations for `opendv ontology ground` and `opendv ontology fibo-sync`."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

import typer

from opendv.cli._client import CliClient
from opendv.cli._output import read_text_arg
from opendv.cli.commands_core import run_cli_action
from opendv.models import (
    ErrorCode,
    FiboSyncReq,
    FiboSyncResp,
    GroundingRequest,
    GroundingResponse,
    OntologyGroundReq,
    OpenDVError,
)
from opendv.models.grounding import GroundingPolicy


def format_grounding_response(resp: GroundingResponse) -> str:
    """Render GroundingResponse as a clean plain-text table (zero ANSI codes)."""
    lines = [
        f"source={resp.knowledge_source_id} version={resp.knowledge_source_version} "
        f"mode={resp.mode_used}",
    ]
    rows: list[tuple[str, str, str, str]] = [
        ("Column", "Status", "Top Match", "Confidence / CI"),
    ]
    for tbl in resp.tables:
        for col in tbl.columns:
            col_label = f"{tbl.table_name}.{col.column_name}"
            if col.choices:
                top = col.choices[0]
                ci = top.confidence
                match_txt = f"{top.label} ({top.suggested_predicate})"
                ci_txt = f"{ci.point:.3f} [{ci.lower:.3f}, {ci.upper:.3f}]"
            else:
                match_txt = col.no_match_category or "-"
                ci_txt = f"null={col.no_match_probability:.3f}"
            rows.append((col_label, col.status, match_txt, ci_txt))

    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    header = " | ".join(rows[0][i].ljust(widths[i]) for i in range(4))
    sep = "-+-".join("-" * widths[i] for i in range(4))
    lines.append(header)
    lines.append(sep)
    for row in rows[1:]:
        lines.append(" | ".join(row[i].ljust(widths[i]) for i in range(4)))
    return "\n".join(lines)


def format_fibo_sync_response(resp: FiboSyncResp) -> str:
    """Render FiboSyncResp as a concise plain-text summary."""
    parts: list[str] = []
    if resp.installed_pack_path:
        parts.append(
            f"installed_pack={resp.installed_pack_path} tag={resp.upstream_tag or '-'} "
            f"sha256={resp.pack_sha256 or '-'}"
        )
    elif resp.pack_sha256:
        parts.append(f"pack_sha256={resp.pack_sha256}")

    if resp.drift_report is not None:
        rep = resp.drift_report
        counts = Counter(item.tier for item in rep.items)
        parts.append(
            f"project={rep.project_id} "
            f"sha256={rep.pack_sha256_orig or 'none'}->{rep.pack_sha256_new} "
            f"items={len(rep.items)} "
            f"(deprecated_with_replacement={counts['deprecated_with_replacement']}, "
            f"removed_or_split={counts['removed_or_split']}, "
            f"pack_sha256_mismatch={counts['pack_sha256_mismatch']}) "
            f"repin_deltas_applied={rep.repin_deltas_applied}"
        )
        for item in rep.items:
            repl = f" -> {item.replacement_uri}" if item.replacement_uri else ""
            parts.append(
                f"  - [{item.tier}] {item.target_kind}:{item.target_id} "
                f"{item.old_standard_uri}{repl} ({item.detail})"
            )
    return "\n".join(parts) if parts else "fibo-sync completed."


def ontology_ground(
    ctx: typer.Context,
    project: Annotated[
        str | None,
        typer.Option("--project", help="Project identifier (mutually exclusive with --input)."),
    ] = None,
    input_path: Annotated[
        str | None,
        typer.Option(
            "--input",
            help="Path to GroundingRequest JSON file (mutually exclusive with --project).",
        ),
    ] = None,
    knowledge_source: Annotated[
        str,
        typer.Option("--knowledge-source", help="Knowledge source: fibo or glossary."),
    ] = "fibo",
    fibo_pack: Annotated[
        str | None,
        typer.Option("--fibo-pack", help="Path to custom FIBO Pack v2 JSON file."),
    ] = None,
    glossary: Annotated[
        str | None,
        typer.Option(
            "--glossary",
            help="Path to YAML glossary file (required with --knowledge-source glossary).",
        ),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Execution mode: agentic or deterministic_only."),
    ] = None,
    skip_reason: Annotated[
        str | None,
        typer.Option("--skip-reason", help="Required rationale when --mode deterministic_only."),
    ] = None,
) -> None:
    """Ground table columns against FIBO Pack v2 or a YAML glossary."""

    def _run(client: CliClient) -> Any:
        if (project is not None) == (input_path is not None):
            raise OpenDVError(
                "Specify exactly one of --project or --input.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if project is not None and not project.strip():
            raise OpenDVError(
                "--project cannot be blank.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        ground_req: GroundingRequest | None = None
        if input_path is not None:
            raw_json = read_text_arg(input_path, label="--input")
            ground_req = GroundingRequest.model_validate_json(raw_json)

        policy: GroundingPolicy | None = None
        if mode is not None or skip_reason is not None:
            policy = GroundingPolicy(
                mode=mode or "agentic",  # type: ignore[arg-type]
                skip_reason=skip_reason,
            )

        req = OntologyGroundReq(
            request=ground_req,
            project_id=project,
            knowledge_source=knowledge_source,  # type: ignore[arg-type]
            fibo_pack=fibo_pack,
            glossary_path=glossary,
            policy=policy,
        )
        return client.ground_ontology(req)

    run_cli_action(ctx, _run, plain_formatter=format_grounding_response)


def ontology_fibo_sync(
    ctx: typer.Context,
    pack_file: Annotated[
        str | None,
        typer.Option("--pack-file", help="Path to Pack v2 JSON file to verify and install."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option(
            "--tag", help="Optional pack tag label for install or --check-project."
        ),
    ] = None,
    check_project: Annotated[
        str | None,
        typer.Option("--check-project", help="Audit committed FIBO mappings in project_id."),
    ] = None,
    apply_repin: Annotated[
        bool,
        typer.Option(
            "--apply-repin",
            help="Apply non-breaking replacement repins (requires --check-project).",
        ),
    ] = False,
) -> None:
    """Install/verify a FIBO Pack v2 file locally and audit/repin project drift."""

    def _run(client: CliClient) -> Any:
        if client.is_remote:
            raise OpenDVError(
                "`opendv ontology fibo-sync` is local-only and does not support "
                "--api-url / OPENDV_API_URL.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if pack_file is not None and not pack_file.strip():
            raise OpenDVError(
                "--pack-file cannot be blank.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if check_project is not None and not check_project.strip():
            raise OpenDVError(
                "--check-project cannot be blank.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if not pack_file and not check_project:
            raise OpenDVError(
                "Specify at least one of --pack-file or --check-project.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if apply_repin and not check_project:
            raise OpenDVError(
                "--apply-repin requires --check-project.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return client.fibo_sync(
            FiboSyncReq(
                pack_file=pack_file,
                tag=tag,
                check_project_id=check_project,
                apply_repin=apply_repin,
            )
        )

    run_cli_action(ctx, _run, plain_formatter=format_fibo_sync_response)


__all__ = [
    "format_fibo_sync_response",
    "format_grounding_response",
    "ontology_fibo_sync",
    "ontology_ground",
]
