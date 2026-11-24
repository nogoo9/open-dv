"""Workflow CLI subcommands: ontology (Step 4-5), vault (Step 6), and storage (Step 7)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import typer

from opendv.cli._client import CliClient
from opendv.cli._output import (
    coerce_str_dict,
    format_run_summary,
    parse_csv_or_json_list,
    parse_json_arg,
    read_text_arg,
)
from opendv.cli.commands_core import ProjectOpt, run_cli_action
from opendv.models import (
    ErrorCode,
    FiboQueryReq,
    HoldAnswersReq,
    HorizonActionReq,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyValidateReq,
    OpenDVError,
    RunControlReq,
    SqlQueryReq,
    StorageCompileReq,
    StorageTargetReq,
    VaultBuildReq,
    WaiverSubmitReq,
)

RunOpt = Annotated[str, typer.Option("--run", help="Run identifier.")]
WaitOpt = Annotated[bool, typer.Option("--wait", help="Poll until run finishes or pauses.")]
HintOpt = Annotated[str | None, typer.Option("--hint", help="Optional steering hint.")]
AnswersJsonOpt = Annotated[
    str, typer.Option("--answers-json", help="Clarification answers JSON string or file path.")
]
YamlOpt = Annotated[str, typer.Option("--yaml", help="Path to Graph A YAML file.")]
ActionOpt = Annotated[str, typer.Option("--action", help="Control action name.")]
ExtraTurnsOpt = Annotated[int | None, typer.Option("--extra-turns")]
ExtraTokensOpt = Annotated[int | None, typer.Option("--extra-tokens")]
ExtraTimeoutOpt = Annotated[int | None, typer.Option("--extra-timeout")]

# ---- ontology ----
ontology_app = typer.Typer(
    name="ontology",
    help="Construct, ask FIBO, validate, commit, and export Graph A ontologies.",
    no_args_is_help=True,
)


@ontology_app.command("construct")
def ontology_construct(
    ctx: typer.Context,
    project: ProjectOpt,
    discovery_mode: Annotated[
        str,
        typer.Option(
            "--discovery-mode",
            help="incremental_new, incremental_reverify, or full_rebuild.",
        ),
    ] = "incremental_new",
    hint: HintOpt = None,
    wait: WaitOpt = False,
) -> None:
    """Start Step 4 ontology construction discovery run."""
    run_cli_action(
        ctx,
        lambda c: c.start_ontology_construct(
            project,
            OntologyConstructReq(
                discovery_mode=discovery_mode,  # type: ignore[arg-type]
                hint=hint,
            ),
            wait=wait,
        ),
        plain_formatter=format_run_summary,
    )


@ontology_app.command("control")
def ontology_control(
    ctx: typer.Context,
    project: ProjectOpt,
    run: RunOpt,
    action: ActionOpt,
    hint: HintOpt = None,
    extra_turns: ExtraTurnsOpt = None,
    extra_tokens: ExtraTokensOpt = None,
    extra_timeout: ExtraTimeoutOpt = None,
) -> None:
    """Send live pause, hint, or raise-budget control signal to a Step 4 run."""
    run_cli_action(
        ctx,
        lambda c: c.control_ontology_run(
            project,
            run,
            RunControlReq(
                action=action,  # type: ignore[arg-type]
                hint=hint,
                extra_turns=extra_turns,
                extra_tokens=extra_tokens,
                extra_timeout_seconds=extra_timeout,
            ),
        ),
        plain_formatter=format_run_summary,
    )


@ontology_app.command("answer")
def ontology_answer(
    ctx: typer.Context,
    project: ProjectOpt,
    run: RunOpt,
    answers_json: AnswersJsonOpt,
    wait: WaitOpt = False,
) -> None:
    """Submit clarification answers for a Step 4 HOLD_CHECKPOINTED run."""

    def _run(client: CliClient) -> Any:
        raw = parse_json_arg(answers_json, label="--answers-json", expect_type=dict)
        answers = coerce_str_dict(raw, label="--answers-json")
        return client.answer_ontology_hold(
            project, run, HoldAnswersReq(answers=answers), wait=wait
        )

    run_cli_action(ctx, _run, plain_formatter=format_run_summary)


@ontology_app.command("fibo-qa")
def ontology_fibo_qa(
    ctx: typer.Context,
    query: Annotated[str, typer.Option("--query", help="Natural-language or concept query.")],
    project: Annotated[
        str | None, typer.Option("--project", help="Optional project context.")
    ] = None,
    namespace: Annotated[str | None, typer.Option("--namespace")] = None,
    top_k: Annotated[int, typer.Option("--top-k")] = 10,
) -> None:
    """Query vendored FIBO-core reference pack using deterministic `bm25_fibo_v1`."""
    run_cli_action(
        ctx,
        lambda c: c.fibo_qa(
            FiboQueryReq(query=query, domain_namespace=namespace, top_k=top_k),
            project_id=project,
        ),
    )


@ontology_app.command("validate")
def ontology_validate(
    ctx: typer.Context,
    project: ProjectOpt,
    yaml: YamlOpt,
    base_revision: Annotated[str | None, typer.Option("--base-revision")] = None,
) -> None:
    """Dry-run ontology YAML validation against invariants and vault evolution locks."""
    run_cli_action(
        ctx,
        lambda c: c.validate_ontology(
            project,
            OntologyValidateReq(
                yaml_text=read_text_arg(yaml, label="--yaml"),
                base_revision_id=base_revision,
            ),
        ),
    )


@ontology_app.command("commit")
def ontology_commit(
    ctx: typer.Context,
    project: ProjectOpt,
    yaml: YamlOpt,
    base_revision: Annotated[str, typer.Option("--base-revision")],
    rationale: Annotated[str, typer.Option("--rationale")],
    waive_quarantine: Annotated[bool, typer.Option("--waive-quarantine")] = False,
) -> None:
    """Commit validated Graph A YAML revision with optimistic concurrency guard."""
    run_cli_action(
        ctx,
        lambda c: c.commit_ontology(
            project,
            OntologyCommitReq(
                yaml_text=read_text_arg(yaml, label="--yaml"),
                base_revision_id=base_revision,
                rationale=rationale,
                waive_quarantine=waive_quarantine,
            ),
        ),
    )


@ontology_app.command("export")
def ontology_export(
    ctx: typer.Context,
    project: ProjectOpt,
    fmt: Annotated[str, typer.Option("--format", help="yaml, json, or ttl.")] = "yaml",
    include_staged: Annotated[bool, typer.Option("--include-staged")] = False,
) -> None:
    """Export active Graph A ontology (optionally including staged candidates)."""
    run_cli_action(
        ctx,
        lambda c: c.export_ontology(project, fmt=fmt, include_staged=include_staged),
        plain_formatter=lambda res: str(res["content"]),
    )


# ---- vault ----
vault_app = typer.Typer(
    name="vault",
    help="Synthesize, verify, waive, steer, and inspect Step 6 Data Vault 2.0 runs.",
    no_args_is_help=True,
)


@vault_app.command("build")
def vault_build(
    ctx: typer.Context,
    project: ProjectOpt,
    policy: Annotated[
        str, typer.Option("--policy", help="balanced, strict, fast, or symbolic_only.")
    ] = "balanced",
    wait: WaitOpt = False,
) -> None:
    """Launch Step 6 Data Vault 2.0 synthesis (`VaultBuildReq` carries only `policy`)."""
    run_cli_action(
        ctx,
        lambda c: c.build_vault(
            project,
            VaultBuildReq(policy=policy),  # type: ignore[arg-type]
            wait=wait,
        ),
        plain_formatter=format_run_summary,
    )


@vault_app.command("verify")
def vault_verify(ctx: typer.Context, project: ProjectOpt) -> None:
    """Verify Draft/Certified Vault IR deterministically (exits 2 on gate failure)."""
    run_cli_action(ctx, lambda c: c.verify_vault(project))


@vault_app.command("waive")
def vault_waive(
    ctx: typer.Context,
    project: ProjectOpt,
    rule: Annotated[str, typer.Option("--rule", help="Anti-pattern rule identifier.")],
    entity: Annotated[str, typer.Option("--entity", help="Target DV2 entity name.")],
    rationale: Annotated[str, typer.Option("--rationale", help="Audit justification.")],
    waiver_id: Annotated[str | None, typer.Option("--waiver-id")] = None,
) -> None:
    """Record a typed anti-pattern waiver rationale."""
    resolved_id = waiver_id or f"waiver-{uuid.uuid4().hex[:8]}"
    run_cli_action(
        ctx,
        lambda c: c.submit_vault_waiver(
            project,
            WaiverSubmitReq(
                waiver_id=resolved_id,
                rule_id=rule,
                entity_name=entity,
                rationale=rationale,
            ),
        ),
    )


@vault_app.command("hold")
def vault_hold(
    ctx: typer.Context,
    project: ProjectOpt,
    run: RunOpt,
    answers_json: AnswersJsonOpt,
    wait: WaitOpt = False,
) -> None:
    """Submit decision answers on a Step 6 HOLD_CHECKPOINTED run."""

    def _run(client: CliClient) -> Any:
        raw = parse_json_arg(answers_json, label="--answers-json", expect_type=dict)
        answers = coerce_str_dict(raw, label="--answers-json")
        return client.submit_vault_decisions(
            project, run, HoldAnswersReq(answers=answers), wait=wait
        )

    run_cli_action(ctx, _run, plain_formatter=format_run_summary)


@vault_app.command("control")
def vault_control(
    ctx: typer.Context,
    project: ProjectOpt,
    run: RunOpt,
    action: ActionOpt,
    hint: HintOpt = None,
    extra_turns: ExtraTurnsOpt = None,
    extra_tokens: ExtraTokensOpt = None,
    extra_timeout: ExtraTimeoutOpt = None,
) -> None:
    """Send live pause, hint, accept-best, raise-budget, or horizon-review control."""
    run_cli_action(
        ctx,
        lambda c: c.send_vault_control(
            project,
            run,
            RunControlReq(
                action=action,  # type: ignore[arg-type]
                hint=hint,
                extra_turns=extra_turns,
                extra_tokens=extra_tokens,
                extra_timeout_seconds=extra_timeout,
            ),
        ),
        plain_formatter=format_run_summary,
    )


@vault_app.command("horizon")
def vault_horizon(
    ctx: typer.Context,
    project: ProjectOpt,
    run: RunOpt,
    action: Annotated[str, typer.Option("--action", help="accept or steer.")],
    assumptions_json: Annotated[
        str | None,
        typer.Option(
            "--assumptions-json",
            help="JSON object (or file) with horizon assumption answers or steering payload.",
        ),
    ] = None,
    notes: Annotated[str, typer.Option("--notes")] = "",
    added_fields: Annotated[str | None, typer.Option("--added-fields")] = None,
    wait: WaitOpt = False,
) -> None:
    """Accept or steer provisional horizon assumptions for a Step 6 run."""

    def _run(client: CliClient) -> Any:
        payload_obj = parse_json_arg(assumptions_json, label="--assumptions-json", expect_type=dict)
        parsed_answers: dict[str, str] = {}
        parsed_notes = notes
        parsed_fields = parse_csv_or_json_list(added_fields, label="--added-fields")
        if payload_obj is not None:
            if any(k in payload_obj for k in ("answers", "notes", "added_fields")):
                raw_ans = payload_obj.get("answers")
                if raw_ans is not None:
                    if not isinstance(raw_ans, dict):
                        raise OpenDVError(
                            "'answers' inside --assumptions-json must be an object.",
                            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                        )
                    parsed_answers = coerce_str_dict(raw_ans, label="--assumptions-json.answers")
                if not parsed_notes and isinstance(payload_obj.get("notes"), str):
                    parsed_notes = payload_obj["notes"]
                if not parsed_fields and isinstance(payload_obj.get("added_fields"), list):
                    parsed_fields = [str(x) for x in payload_obj["added_fields"]]
            else:
                parsed_answers = coerce_str_dict(payload_obj, label="--assumptions-json")
        req = HorizonActionReq(
            action=action,  # type: ignore[arg-type]
            answers=parsed_answers,
            notes=parsed_notes,
            added_fields=parsed_fields,
        )
        return client.submit_horizon_steering(project, run, req, wait=wait)

    run_cli_action(ctx, _run, plain_formatter=format_run_summary)


@vault_app.command("logs")
def vault_logs(
    ctx: typer.Context,
    project: ProjectOpt,
    run: Annotated[str | None, typer.Option("--run", help="Run id (defaults to latest).")] = None,
    since_seq: Annotated[int, typer.Option("--since-seq")] = 0,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Fetch persisted JSONL run observability events."""

    def _run(client: CliClient) -> Any:
        run_id = run
        if run_id is None:
            runs = client.list_runs(project)
            if not runs:
                raise OpenDVError(
                    f"No runs recorded for project '{project}'.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
            run_id = runs[0].run_id
        return client.get_run_logs(project, run_id, since_seq=since_seq, limit=limit)

    run_cli_action(ctx, _run)


# ---- storage ----
storage_app = typer.Typer(
    name="storage",
    help="Compile targets, apply local DuckDB/Parquet migrations, regenerate, and query SQL.",
    no_args_is_help=True,
)


@storage_app.command("compile")
def storage_compile(
    ctx: typer.Context,
    project: ProjectOpt,
    target: Annotated[str, typer.Option("--target", help="Target id or 'all'.")],
    dbt_dialect: Annotated[str, typer.Option("--dbt-dialect")] = "duckdb",
    verify_runtime: Annotated[bool, typer.Option("--verify-runtime")] = False,
) -> None:
    """Compile CertifiedVaultIR into target SQL/dbt/Databricks/Iceberg bundles."""
    run_cli_action(
        ctx,
        lambda c: c.compile_storage(
            project,
            StorageCompileReq(
                target_id=target,
                dbt_sql_dialect=dbt_dialect,  # type: ignore[arg-type]
                verify_runtime=verify_runtime,
            ),
        ),
    )


@storage_app.command("migrate")
def storage_migrate(
    ctx: typer.Context,
    project: ProjectOpt,
    target: Annotated[str, typer.Option("--target", help="duckdb_local or parquet.")],
) -> None:
    """Apply certified storage schema DDL and bronze/DV2 load into local DuckDB or Parquet."""
    run_cli_action(
        ctx,
        lambda c: c.migrate_storage(
            project,
            StorageTargetReq(target_id=target),
        ),
    )


@storage_app.command("regenerate")
def storage_regenerate(
    ctx: typer.Context,
    project: ProjectOpt,
    target: Annotated[str, typer.Option("--target", help="Target id or 'all'.")],
) -> None:
    """Re-verify and regenerate local storage artifacts against CertifiedVaultIR."""
    run_cli_action(
        ctx,
        lambda c: c.regenerate_storage(
            project,
            StorageTargetReq(target_id=target),
        ),
    )


@storage_app.command("query")
def storage_query(
    ctx: typer.Context,
    project: ProjectOpt,
    sql: Annotated[str, typer.Option("--sql", help="Read-only SELECT/WITH SQL statement.")],
    limit: Annotated[int, typer.Option("--limit")] = 1000,
) -> None:
    """Execute a read-only SQL query against local DuckDB/Parquet views."""
    run_cli_action(ctx, lambda c: c.query_storage(project, SqlQueryReq(sql=sql, limit=limit)))
