"""Declarative CLI definitions for Phase 2 Studio wizard commands."""

from __future__ import annotations

from opendv_service.cli_spec.schema import CommandDef, OptionDef

_SESSION_OPTION = OptionDef(
    name="session",
    flag="--session",
    short="-s",
    type="string",
    required=True,
    help="Target studio session identifier",
)

STUDIO_WIZARD_SUBCOMMANDS: list[CommandDef] = [
    CommandDef(
        name="attach-source",
        description=(
            "Attach and profile a tabular connector source "
            "(filesystem/SQLite/DuckDB/CSV/Parquet/XLSX/JSONL)"
        ),
        help="Attach connector source",
        availability="both",
        options=[
            _SESSION_OPTION,
            OptionDef(
                name="adapter",
                flag="--adapter",
                type="string",
                required=True,
                help="Connector adapter type (e.g. filesystem, sqlite, duckdb, csv)",
            ),
            OptionDef(
                name="uri",
                flag="--uri",
                type="string",
                required=True,
                help="Source URI, file path, or connection identifier",
            ),
            OptionDef(
                name="table", flag="--table", type="string", help="Target table name or alias"
            ),
            OptionDef(
                name="namespace",
                flag="--namespace",
                type="string",
                help="Optional domain namespace override",
            ),
            OptionDef(
                name="credentials_ref",
                flag="--credentials-ref",
                type="string",
                help=(
                    "Secret reference (env:... or secret:...) — "
                    "plaintext credentials are rejected"
                ),
            ),
            OptionDef(
                name="description",
                flag="--description",
                type="string",
                help="Optional human source description",
            ),
            OptionDef(
                name="do_not_send_to_agent",
                flag="--do-not-send-to-agent",
                type="boolean",
                default=False,
                help="Mark connector source confidential (taint perimeter)",
            ),
        ],
    ),
    CommandDef(
        name="fibo-qa",
        description="Ask read-only FIBO & evidence alignment questions against a Studio session",
        help="Query FIBO Q&A",
        availability="both",
        options=[
            _SESSION_OPTION,
            OptionDef(
                name="question",
                flag="--question",
                short="-q",
                type="string",
                required=True,
                help="Natural-language FIBO/evidence question",
            ),
            OptionDef(
                name="source_id", flag="--source-id", type="string", help="Optional source ID scope"
            ),
            OptionDef(
                name="concept_uri",
                flag="--concept-uri",
                type="string",
                help="Optional concept URI scope",
            ),
        ],
    ),
    CommandDef(
        name="logs",
        description="Inspect or stream Studio session AI & compiler execution logs",
        help="Inspect session logs",
        availability="both",
        options=[
            OptionDef(
                name="session",
                flag="--session",
                short="-s",
                type="string",
                help="Target studio session identifier",
            ),
            OptionDef(
                name="session_id",
                flag="--session-id",
                type="string",
                help="Target studio session identifier alias",
            ),
            OptionDef(
                name="stage",
                flag="--stage",
                type="string",
                help="Filter by stage ID or stage pill alias (STAGE_00..07 or 00..06)",
            ),
            OptionDef(
                name="step", flag="--step", type="integer", help="Filter by wizard step number"
            ),
            OptionDef(
                name="category",
                flag="--category",
                type="string",
                help="Filter by log entry category (e.g. agent, compiler)",
            ),
            OptionDef(
                name="persona",
                flag="--persona",
                type="string",
                help="Filter by persona (e.g. @ship-vault)",
            ),
            OptionDef(
                name="follow",
                flag="--follow",
                type="boolean",
                default=False,
                help="Stream logs live via SSE until stage completes",
            ),
        ],
    ),
]

__all__ = ["STUDIO_WIZARD_SUBCOMMANDS"]
