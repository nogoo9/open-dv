"""Unified CLI entrypoints (`opendv` and `axi-dv`) for Open-DV v6.0."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from opendv_cli.api_client import OpenDVCliApiError
from opendv_cli.commands import (
    run_compile_command,
    run_hold_command,
    run_pipeline_command,
    run_profile_command,
    run_run_command,
    run_sandbox_command,
    run_verify_command,
)

SUBCOMMANDS = {"verify", "profile", "compile", "sandbox", "run", "hold", "pipeline"}
_BOOLEAN_FLAGS = frozenset(
    {
        "-h",
        "--help",
        "--local",
        "--json",
        "--toon",
        "--stream",
        "--no-stream",
        "--stream-tokens",
        "--no-stream-tokens",
    }
)


def _has_explicit_subcommand(raw_args: list[str]) -> bool:
    """Return True if raw_args contains an explicit positional subcommand token."""
    skip_next = False
    for tok in raw_args:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            break
        if tok.startswith("-"):
            if "=" not in tok and tok not in _BOOLEAN_FLAGS:
                skip_next = True
            continue
        return tok in SUBCOMMANDS
    return False


def _add_shared_flags(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """Attach shared `--local`, `--api-url`, `--role`, `--json`, `--run-id`, and `--project-id`."""
    default_bool: Any = argparse.SUPPRESS if suppress_defaults else False
    default_none: Any = argparse.SUPPRESS if suppress_defaults else None

    parser.add_argument(
        "--local",
        action="store_true",
        default=default_bool,
        help="Execute directly against local workspace packages without an HTTP server.",
    )
    parser.add_argument(
        "--api-url",
        dest="api_url",
        default=default_none,
        help="Execute against a remote Open-DV Platform API server (e.g. http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--role",
        default=argparse.SUPPRESS if suppress_defaults else "INTERNAL_MODELER",
        help="5-Tier RBAC role header (PUBLIC, INTERNAL_MODELER, DATA_STEWARD, etc.).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=default_bool,
        help="Format command output as deterministic JSON.",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=default_none,
        help="Target platform run_id.",
    )
    parser.add_argument(
        "--project-id",
        dest="project_id",
        default=default_none,
        help="Target platform project_id.",
    )


def build_parser(prog: str = "opendv") -> argparse.ArgumentParser:
    """Construct the top-level ArgumentParser for `opendv` or `axi-dv`."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Open-DV v6.0 Headless Platform & AXI-DV Verification CLI.",
    )
    _add_shared_flags(parser, suppress_defaults=False)

    subparsers = parser.add_subparsers(dest="subcommand")

    # 1. verify
    p_verify = subparsers.add_parser(
        "verify",
        help="Run 6-Gate AXI-DV verification pipeline on a Data Vault 2.0 contract.",
    )
    _add_shared_flags(p_verify, suppress_defaults=True)
    p_verify.add_argument(
        "--contract",
        default=None,
        help="Path to the DV 2.0 model contract YAML.",
    )
    p_verify.add_argument(
        "--graph-a",
        dest="graph_a",
        default=None,
        help="Path to the Graph A domain ontology YAML.",
    )
    p_verify.add_argument(
        "--graph-b",
        dest="graph_b",
        default=None,
        help="Path to the Graph B empirical profile JSON.",
    )
    p_verify.add_argument(
        "--toon",
        action="store_true",
        default=False,
        help="Print compact @toon_v1 trace output.",
    )

    # 2. profile
    p_profile = subparsers.add_parser(
        "profile",
        help="Inspect or validate Bronze Graph B empirical column profiles.",
    )
    _add_shared_flags(p_profile, suppress_defaults=True)
    p_profile.add_argument(
        "--graph-b",
        dest="graph_b",
        default=None,
        help="Path to the Graph B profile JSON.",
    )
    p_profile.add_argument(
        "--source-csv",
        dest="source_csv",
        default=None,
        help="Path to a raw Bronze CSV file to profile directly via GraphBProfiler.",
    )
    p_profile.add_argument(
        "--source-system",
        dest="source_system",
        default="bronze_source",
        help="Source system code when profiling a raw CSV file (default: bronze_source).",
    )
    p_profile.add_argument(
        "--table-name",
        dest="table_name",
        default=None,
        help="Optional table name override when profiling a raw CSV file.",
    )

    # 3. compile
    p_compile = subparsers.add_parser(
        "compile",
        help="Compile a verified DV 2.0 contract into Canonical IR & 4-Zone SQL.",
    )
    _add_shared_flags(p_compile, suppress_defaults=True)
    p_compile.add_argument(
        "--contract",
        default=None,
        help="Path to the DV 2.0 model contract YAML.",
    )
    p_compile.add_argument(
        "--canonical-ir",
        dest="canonical_ir",
        default=None,
        help="Path to the Canonical IR JSON.",
    )

    # 4. sandbox
    p_sandbox = subparsers.add_parser(
        "sandbox",
        help="Execute 4-Batch adversarial DuckDB sandbox or bi-temporal Queries D.10 / D.10b.",
    )
    _add_shared_flags(p_sandbox, suppress_defaults=True)
    p_sandbox.add_argument(
        "--contract",
        default=None,
        help="Path to the DV 2.0 model contract YAML for local sandbox execution.",
    )
    p_sandbox.add_argument(
        "--canonical-ir",
        dest="canonical_ir",
        default=None,
        help="Path to the Canonical IR JSON for local sandbox execution.",
    )
    p_sandbox.add_argument(
        "--query-type",
        dest="query_type",
        default="ADVERSARIAL_BATCHES_1_TO_4",
        help="Query type (ADVERSARIAL_BATCHES_1_TO_4, QUERY_D10_PIT, QUERY_D10B_VALUATION, etc.).",
    )
    p_sandbox.add_argument(
        "--query-id",
        dest="query_id",
        default=None,
        help="Alias for --query-type.",
    )
    p_sandbox.add_argument(
        "--cutoff",
        "--assertion-cutoff-ts",
        dest="assertion_cutoff_ts",
        default="2026-11-15 23:59:59",
        help="Bi-temporal assertion cutoff timestamp (UTC).",
    )

    # 5. run
    p_run = subparsers.add_parser(
        "run",
        help="Create, inspect, abort, or stream SSE events for a platform verification run.",
    )
    _add_shared_flags(p_run, suppress_defaults=True)
    p_run.add_argument(
        "run_action",
        nargs="?",
        default="get",
        choices=["list", "get", "create", "abort", "events"],
        help="Run subcommand action (default: get).",
    )
    p_run.add_argument(
        "--harness",
        default="antigravity",
        choices=[
            "agy",
            "agy-cli",
            "antigravity",
            "claude",
            "claude-cli",
            "claude-code",
        ],
        help="Agent harness provider when creating a run.",
    )
    p_run.add_argument(
        "--last-event-id",
        dest="last_event_id",
        default=None,
        help="Resume SSE stream after the specified Last-Event-ID.",
    )

    # 6. hold
    p_hold = subparsers.add_parser(
        "hold",
        help="List or resolve Steward Captain-Holds (Tier U3/G3/G4).",
    )
    _add_shared_flags(p_hold, suppress_defaults=True)
    p_hold.add_argument(
        "hold_action",
        nargs="?",
        default="list",
        choices=["list", "resolve", "approve", "reject", "answer"],
        help="Hold action (list, resolve, approve, reject, answer).",
    )
    p_hold.add_argument(
        "--hold-id",
        dest="hold_id",
        default=None,
        help="Identifier of the Captain-Hold to resolve.",
    )
    p_hold.add_argument(
        "--event-id",
        dest="event_id",
        default=None,
        help="Observability event ID that triggered the hold.",
    )
    p_hold.add_argument(
        "--decision",
        "--action",
        dest="decision",
        default=None,
        choices=["APPROVE", "REJECT", "ANSWER", "approve", "reject", "answer"],
        help="Steward resolution decision (APPROVE, REJECT, ANSWER).",
    )
    p_hold.add_argument(
        "--candidate",
        dest="selected_candidate_id",
        default=None,
        help="Selected candidate entity ID when approving an entity resolution hold.",
    )
    p_hold.add_argument(
        "--notes",
        default="Confirmed via opendv hold CLI command.",
        help="Steward audit rationale notes.",
    )
    p_hold.add_argument(
        "--steward",
        dest="steward_id",
        default="steward-cli",
        help="Steward identifier recorded in the observability ledger.",
    )

    # 7. pipeline
    p_pipeline = subparsers.add_parser(
        "pipeline",
        help="Validate, execute, or inspect reports for the 8-stage agentic pipeline.",
    )
    _add_shared_flags(p_pipeline, suppress_defaults=True)
    p_pipeline.add_argument(
        "pipeline_action",
        nargs="?",
        default="run",
        choices=["run", "validate", "report"],
        help="Pipeline action (run, validate, report).",
    )
    p_pipeline.add_argument(
        "--input",
        "--input-spec",
        dest="input_path",
        default=None,
        help="Path to the pipeline_input.yaml or .json specification.",
    )
    p_pipeline.add_argument(
        "--harness-config",
        dest="harness_config",
        default=None,
        help="Optional path to the harness_config.yaml or .json configuration.",
    )
    p_pipeline.add_argument(
        "--initial-contract",
        dest="initial_contract",
        default=None,
        help=(
            "Optional path to a previously deployed NoMistakeDataVault2ContractV5 "
            "(final_contract.yaml) for incremental schema evolution."
        ),
    )
    p_pipeline.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Optional output directory for generated pipeline artifacts and reports.",
    )
    p_pipeline.add_argument(
        "--provider",
        dest="provider",
        default=None,
        choices=[
            "agy",
            "agy-cli",
            "antigravity",
            "claude",
            "claude-cli",
            "claude-code",
        ],
        help=(
            "Optional agent harness provider override "
            "(agy, agy-cli, antigravity, claude, claude-cli, claude-code)."
        ),
    )
    p_pipeline.add_argument(
        "--hold-policy",
        dest="hold_policy",
        default=None,
        help=(
            "Optional steward hold policy override "
            "(auto_approve_top_candidate, pause, reject_on_hold)."
        ),
    )
    p_pipeline.add_argument(
        "--report-path",
        dest="report_path",
        default=None,
        help="Optional local path to a pipeline_report.json file for 'pipeline report'.",
    )
    p_pipeline.add_argument(
        "--stream",
        dest="stream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Enable or disable live stage progress streaming during 'pipeline run' (default: True)."
        ),
    )
    p_pipeline.add_argument(
        "--stream-tokens",
        dest="stream_tokens",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable or disable live LLM token/text delta streaming during 'pipeline run' "
            "(default: True when --stream is enabled and --json/--toon is not set)."
        ),
    )

    return parser


def _emit_output(
    args: argparse.Namespace,
    payload: dict[str, Any],
) -> None:
    """Print CLI command result as JSON, `@toon_v1`, or human-readable summary."""
    if getattr(args, "toon", False) and "toon_text" in payload:
        print(str(payload["toon_text"]).rstrip())
        return
    if getattr(args, "json_output", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    status_label = (
        payload.get("status")
        or payload.get("verdict")
        or ("PASS" if payload.get("overall_passed") else None)
        or ("PASS" if payload.get("all_batches_passed") else None)
        or "OK"
    )
    run_id = payload.get("run_id") or payload.get("resumed_run_id") or ""
    subcommand = getattr(args, "subcommand", "verify")
    print(f"[opendv {subcommand}] status={status_label} run_id={run_id}".strip())
    print(json.dumps(payload, indent=2, sort_keys=True))


def _dispatch(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Dispatch parsed CLI arguments to the corresponding command handler."""
    sub = getattr(args, "subcommand", None) or "verify"
    try:
        if sub == "verify":
            code, payload = run_verify_command(args, transport=transport)
        elif sub == "profile":
            code, payload = run_profile_command(args, transport=transport)
        elif sub == "compile":
            code, payload = run_compile_command(args, transport=transport)
        elif sub == "sandbox":
            code, payload = run_sandbox_command(args, transport=transport)
        elif sub == "run":
            code, payload = run_run_command(args, transport=transport)
        elif sub == "hold":
            code, payload = run_hold_command(args, transport=transport)
        elif sub == "pipeline":
            code, payload = run_pipeline_command(args, transport=transport)
        else:
            print(f"Unknown subcommand: {sub}", file=sys.stderr)
            return 2
    except OpenDVCliApiError as exc:
        err_payload = {
            "status": "ERROR",
            "status_code": exc.status_code,
            "detail": exc.message,
        }
        if getattr(args, "json_output", False):
            print(json.dumps(err_payload, indent=2, sort_keys=True))
        else:
            print(f"API error ({exc.status_code}): {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        err_payload = {"status": "ERROR", "detail": str(exc)}
        if getattr(args, "json_output", False):
            print(json.dumps(err_payload, indent=2, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    _emit_output(args, payload)
    return code


def app_main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Primary entrypoint for the `opendv` CLI binary."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(prog="opendv")
    if not raw_args:
        parser.print_help()
        return 0
    if not any(flag in raw_args for flag in ("-h", "--help")) and not _has_explicit_subcommand(
        raw_args
    ):
        raw_args = ["verify", *raw_args]
    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and "--json" in raw_args:
            print(
                json.dumps(
                    {"status": "ERROR", "detail": "Invalid CLI arguments."},
                    indent=2,
                    sort_keys=True,
                )
            )
        return code
    return _dispatch(args, transport=transport)


def axi_dv_main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Entrypoint for the `axi-dv` verification CLI binary.

    Defaults to the `verify` subcommand when invoked without an explicit subcommand name
    (e.g. `axi-dv --local --contract ... --json`).
    """
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if any(flag in raw_args for flag in ("-h", "--help")):
        parser = build_parser(prog="axi-dv")
        parser.parse_args(raw_args)
        return 0

    if not _has_explicit_subcommand(raw_args):
        raw_args = ["verify", *raw_args]

    parser = build_parser(prog="axi-dv")
    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
        if code != 0 and "--json" in raw_args:
            print(
                json.dumps(
                    {"status": "ERROR", "detail": "Invalid CLI arguments."},
                    indent=2,
                    sort_keys=True,
                )
            )
        return code
    return _dispatch(args, transport=transport)


main = app_main

if __name__ == "__main__":
    raise SystemExit(app_main())
