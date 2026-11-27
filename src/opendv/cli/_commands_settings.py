"""Workspace settings, connection profiles, and project settings CLI subcommands."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

import typer

from opendv.cli._client import CliClient
from opendv.cli._output import coerce_option_dict, parse_json_arg
from opendv.cli.commands_core import ProjectOpt, SecretRefsOpt, run_cli_action
from opendv.models import (
    VALID_MODEL_OVERRIDE_TARGETS,
    ConnectorConfigPayload,
    ErrorCode,
    OpenDVError,
    ProjectSettingsPatchReq,
    SourceInspectReq,
    WorkspaceSettings,
)


class HarnessProviderOpt(StrEnum):
    """Supported agent harness provider identifiers."""

    AGY = "agy"
    CLAUDE_CLI = "claude-cli"
    CLAUDE = "claude"
    GEMINI = "gemini"


class CegarPolicyOpt(StrEnum):
    """Supported CEGAR verification policies."""

    SYMBOLIC_ONLY = "symbolic_only"
    FAST = "fast"
    BALANCED = "balanced"
    STRICT = "strict"


class ModelTierOpt(StrEnum):
    """Supported V7 semantic critic model tiers."""

    FRONTIER_CRITIC = "FRONTIER_CRITIC"
    STANDARD_WORKER = "STANDARD_WORKER"


class DbtSqlDialectOpt(StrEnum):
    """Supported dbt SQL dialects."""

    DUCKDB = "duckdb"
    DATABRICKS = "databricks"
    SNOWFLAKE = "snowflake"
    POSTGRES = "postgres"
    BIGQUERY = "bigquery"
    TSQL = "tsql"
    REDSHIFT = "redshift"
    CLICKHOUSE = "clickhouse"


class SecretBackendOpt(StrEnum):
    """Supported secret persistence backends."""

    KEYRING = "keyring"
    USER_STATE_FILE = "user_state_file"


class SecretPrecedenceOpt(StrEnum):
    """Supported secret resolution precedence modes."""

    STORE_FIRST = "store_first"
    ENV_FIRST = "env_first"


class DiscoveryModeOpt(StrEnum):
    """Supported Step 4 ontology discovery modes."""

    INCREMENTAL_NEW = "incremental_new"
    INCREMENTAL_REVERIFY = "incremental_reverify"
    FULL_REBUILD = "full_rebuild"


class ClearFieldOpt(StrEnum):
    """Nullable setting fields clearable via `--clear`."""

    MAX_TURNS = "max-turns"
    MAX_TOKENS = "max-tokens"
    TIMEOUT_SECONDS = "timeout-seconds"
    MODEL_OVERRIDES = "model-overrides"
    ALL = "all"


class StructuredAdapterOpt(StrEnum):
    """Supported structured connector adapter IDs for connection profiles."""

    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    PARQUET = "parquet"
    CSV = "csv"
    XLSX = "xlsx"
    DATABRICKS_UC = "databricks_uc"


HarnessProviderFlag = Annotated[
    HarnessProviderOpt | None,
    typer.Option("--harness-provider", help="Default agent harness provider."),
]
CegarPolicyFlag = Annotated[
    CegarPolicyOpt | None,
    typer.Option("--cegar-policy", help="Default CEGAR verification policy."),
]
CriticTierFlag = Annotated[
    ModelTierOpt | None,
    typer.Option("--v7-critic-tier", help="Default V7 semantic critic tier."),
]
DbtDialectFlag = Annotated[
    DbtSqlDialectOpt | None,
    typer.Option("--dbt-sql-dialect", help="Default dbt SQL dialect."),
]
SecretBackendFlag = Annotated[
    SecretBackendOpt | None,
    typer.Option("--secret-backend", help="Secret backend (keyring or user_state_file)."),
]
SecretPrecedenceFlag = Annotated[
    SecretPrecedenceOpt | None,
    typer.Option("--secret-precedence", help="Secret resolution order."),
]
DiscoveryModeFlag = Annotated[
    DiscoveryModeOpt | None,
    typer.Option("--discovery-mode", help="Default Step 4 ontology discovery mode."),
]
MaxTurnsFlag = Annotated[
    int | None,
    typer.Option("--max-turns", help="Max agent turns per run (1..500)."),
]
MaxTokensFlag = Annotated[
    int | None,
    typer.Option("--max-tokens", help="Max token budget per run (>= 1)."),
]
TimeoutSecondsFlag = Annotated[
    int | None,
    typer.Option("--timeout-seconds", help="Per-turn timeout in seconds (1..7200)."),
]
ModelOverrideFlag = Annotated[
    list[str] | None,
    typer.Option("--model-override", help="Set TARGET=MODEL override (repeatable)."),
]
ClearModelOverrideFlag = Annotated[
    list[str] | None,
    typer.Option("--clear-model-override", help="Clear TARGET model override (repeatable)."),
]
ClearFieldFlag = Annotated[
    list[ClearFieldOpt] | None,
    typer.Option("--clear", help="Clear nullable fields (repeatable)."),
]

_CLEAR_SCALAR_MAP: dict[str, str] = {
    "max-turns": "max_turns",
    "max-tokens": "max_tokens",
    "timeout-seconds": "timeout_seconds",
}

settings_app = typer.Typer(
    name="settings",
    help="Inspect and update workspace settings.",
    no_args_is_help=True,
)
profile_app = typer.Typer(
    name="profile",
    help="Manage user-global saved connection profiles.",
    no_args_is_help=True,
)
settings_app.add_typer(profile_app, name="profile")

project_settings_app = typer.Typer(
    name="settings",
    help="Inspect and update per-project execution settings.",
    no_args_is_help=True,
)


def _parse_override_flags(
    model_override: list[str] | None,
    clear_model_override: list[str] | None,
) -> tuple[dict[str, str], list[str]]:
    set_map: dict[str, str] = {}
    for raw in model_override or ():
        target, sep, model_id = raw.partition("=")
        if not sep:
            raise OpenDVError(
                f"Invalid --model-override '{raw}'; expected TARGET=MODEL.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        clean_target = target.strip()
        clean_model = model_id.strip()
        if clean_target not in VALID_MODEL_OVERRIDE_TARGETS:
            raise OpenDVError(
                f"Unknown model override target '{clean_target}'; "
                f"expected one of {sorted(VALID_MODEL_OVERRIDE_TARGETS)}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if not clean_model:
            raise OpenDVError(
                f"Model override for '{clean_target}' must not be empty.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        set_map[clean_target] = clean_model

    clear_targets: list[str] = []
    for raw_target in clear_model_override or ():
        clean_target = raw_target.strip()
        if clean_target not in VALID_MODEL_OVERRIDE_TARGETS:
            raise OpenDVError(
                f"Unknown model override target '{clean_target}'; "
                f"expected one of {sorted(VALID_MODEL_OVERRIDE_TARGETS)}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if clean_target in set_map:
            raise OpenDVError(
                f"Conflicting --model-override and --clear-model-override for '{clean_target}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if clean_target not in clear_targets:
            clear_targets.append(clean_target)
    return set_map, clear_targets


def _validate_clear_flags(
    clear: list[ClearFieldOpt] | None,
    *,
    max_turns: int | None,
    max_tokens: int | None,
    timeout_seconds: int | None,
    model_override: list[str] | None,
    clear_model_override: list[str] | None,
) -> set[str]:
    raw_clears = {item.value for item in (clear or ())}
    expanded = (
        {"max-turns", "max-tokens", "timeout-seconds", "model-overrides"}
        if "all" in raw_clears
        else raw_clears
    )
    scalar_vals: dict[str, int | None] = {
        "max-turns": max_turns,
        "max-tokens": max_tokens,
        "timeout-seconds": timeout_seconds,
    }
    for flag_key, val in scalar_vals.items():
        if flag_key in expanded and val is not None:
            raise OpenDVError(
                f"Cannot combine --{flag_key} with --clear {flag_key} or --clear all.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
    if "model-overrides" in expanded and (model_override or clear_model_override):
        raise OpenDVError(
            "Cannot combine --model-override/--clear-model-override with "
            "--clear model-overrides or --clear all.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return expanded


def _collect_updates(raw_fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in raw_fields.items():
        if val is None:
            continue
        out[key] = val.value if isinstance(val, StrEnum) else val
    return out


def _prepare_settings_mutation(
    raw_fields: dict[str, Any],
    *,
    max_turns: int | None,
    max_tokens: int | None,
    timeout_seconds: int | None,
    model_override: list[str] | None,
    clear_model_override: list[str] | None,
    clear: list[ClearFieldOpt] | None,
) -> tuple[dict[str, Any], dict[str, str], list[str], set[str]]:
    updates = _collect_updates(
        {
            **raw_fields,
            "max_turns": max_turns,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
        }
    )
    set_map, clear_targets = _parse_override_flags(model_override, clear_model_override)
    cleared = _validate_clear_flags(
        clear,
        max_turns=max_turns,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        model_override=model_override,
        clear_model_override=clear_model_override,
    )
    if not updates and not set_map and not clear_targets and not cleared:
        raise OpenDVError(
            "At least one setting flag must be provided.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return updates, set_map, clear_targets, cleared


@settings_app.command("show")
def settings_show(ctx: typer.Context) -> None:
    """Show persisted workspace settings."""
    run_cli_action(ctx, lambda c: c.get_settings())


@settings_app.command("set")
def settings_set(
    ctx: typer.Context,
    harness_provider: HarnessProviderFlag = None,
    cegar_policy: CegarPolicyFlag = None,
    v7_critic_tier: CriticTierFlag = None,
    dbt_sql_dialect: DbtDialectFlag = None,
    secret_backend: SecretBackendFlag = None,
    secret_precedence: SecretPrecedenceFlag = None,
    max_turns: MaxTurnsFlag = None,
    max_tokens: MaxTokensFlag = None,
    timeout_seconds: TimeoutSecondsFlag = None,
    model_override: ModelOverrideFlag = None,
    clear_model_override: ClearModelOverrideFlag = None,
    clear: ClearFieldFlag = None,
) -> None:
    """Update workspace-level defaults with strict schema validation."""

    def _run(client: CliClient) -> WorkspaceSettings:
        updates, set_map, clear_targets, cleared = _prepare_settings_mutation(
            {
                "harness_provider": harness_provider,
                "cegar_policy": cegar_policy,
                "v7_critic_tier": v7_critic_tier,
                "dbt_sql_dialect": dbt_sql_dialect,
                "secret_backend": secret_backend,
                "secret_precedence": secret_precedence,
            },
            max_turns=max_turns,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            model_override=model_override,
            clear_model_override=clear_model_override,
            clear=clear,
        )
        current = client.get_settings()
        merged = current.model_dump(mode="python")
        for flag_key, field_name in _CLEAR_SCALAR_MAP.items():
            if flag_key in cleared:
                merged[field_name] = None
        if "model-overrides" in cleared:
            merged["model_overrides"] = {}
        elif set_map or clear_targets:
            overrides = dict(merged.get("model_overrides") or {})
            for target in clear_targets:
                overrides.pop(target, None)
            overrides.update(set_map)
            merged["model_overrides"] = overrides
        merged.update(updates)
        if updates.get("cegar_policy") == "strict" and "v7_critic_tier" not in updates:
            merged["v7_critic_tier"] = "FRONTIER_CRITIC"
        updated = WorkspaceSettings.model_validate(merged)
        return client.update_settings(updated)

    run_cli_action(ctx, _run)


@settings_app.command("models")
def settings_models(
    ctx: typer.Context,
    provider: Annotated[
        HarnessProviderOpt | None,
        typer.Option("--provider", help="Provider id (defaults to active harness_provider)."),
    ] = None,
) -> None:
    """List offline default model suggestions for the active or specified provider."""

    def _run(client: CliClient) -> dict[str, Any]:
        target_provider = (
            provider.value if provider is not None else client.get_settings().harness_provider
        )
        readiness = client.get_agent_readiness()
        rec = next((p for p in readiness.providers if p.provider == target_provider), None)
        return {
            "provider": target_provider,
            "models": list(rec.available_models) if rec is not None else [],
        }

    run_cli_action(ctx, _run)


@settings_app.command("trust-workspace")
def settings_trust_workspace(
    ctx: typer.Context,
    path: Annotated[
        str, typer.Option("--path", help="Workspace directory path to trust or revoke.")
    ],
    revoke: Annotated[
        bool,
        typer.Option(
            "--revoke",
            help="Revoke workspace trust via POST /api/v1/settings/trusted-workspaces/revoke.",
        ),
    ] = False,
) -> None:
    """Approve or revoke SHA-256 hashes for `.opendv/agents/*/SKILL.md` overrides."""

    def _run(client: CliClient) -> WorkspaceSettings:
        if revoke:
            return client.revoke_trusted_workspace(path)
        return client.trust_workspace(path)

    run_cli_action(ctx, _run)


@profile_app.command("list")
def settings_profile_list(ctx: typer.Context) -> None:
    """List user-global saved connection profiles."""
    run_cli_action(ctx, lambda c: c.get_settings().connection_profiles)


@profile_app.command("set")
def settings_profile_set(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Unique connection profile name.")],
    adapter: Annotated[
        StructuredAdapterOpt,
        typer.Option("--adapter", help="Structured connector adapter identifier."),
    ],
    display_name: Annotated[
        str | None,
        typer.Option("--display-name", help="Human-readable label (defaults to --name)."),
    ] = None,
    uri: Annotated[
        str | None,
        typer.Option(
            "--uri",
            help="Source path or host URI (optional for databricks_uc when options.host is set).",
        ),
    ] = None,
    options_json: Annotated[
        str | None,
        typer.Option("--options-json", help="Connector options JSON object or file path."),
    ] = None,
    secret_ref: SecretRefsOpt = None,
    selected_tables: Annotated[
        list[str] | None,
        typer.Option("--selected-tables", help="Selected table filter (repeatable)."),
    ] = None,
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", help="Profile inspection timeout in seconds."),
    ] = 30,
) -> None:
    """Create or replace a named user-global connection profile."""

    def _run(client: CliClient) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise OpenDVError(
                "Profile --name must not be blank.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        opts_raw = parse_json_arg(options_json, label="--options-json", expect_type=dict)
        opts = coerce_option_dict(opts_raw, label="--options-json")
        clean_uri = (uri or "").strip()
        adapter_id = adapter.value
        if adapter_id == "databricks_uc":
            host_val = opts.get("host")
            if not clean_uri and not (isinstance(host_val, str) and host_val.strip()):
                raise OpenDVError(
                    "databricks_uc profile requires --uri or a non-empty string options.host.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
        elif not clean_uri:
            raise OpenDVError(
                f"Adapter '{adapter_id}' requires a non-empty --uri.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        clean_label = display_name.strip() if display_name and display_name.strip() else clean_name
        payload = ConnectorConfigPayload(
            adapter_id=adapter_id,
            display_name=clean_label,
            uri_or_path=clean_uri,
            options=opts,
            secret_refs=list(secret_ref or ()),
            selected_tables=list(selected_tables) if selected_tables is not None else None,
            profile_timeout_seconds=timeout_seconds,
        )
        settings = client.get_settings()
        profiles = dict(settings.connection_profiles)
        profiles[clean_name] = payload
        updated = settings.model_copy(update={"connection_profiles": profiles})
        saved = client.update_settings(updated)
        return {"name": clean_name, "profile": saved.connection_profiles[clean_name]}

    run_cli_action(ctx, _run)


@profile_app.command("delete")
def settings_profile_delete(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Connection profile name to delete.")],
) -> None:
    """Delete a saved user-global connection profile."""

    def _run(client: CliClient) -> dict[str, Any]:
        clean_name = name.strip()
        settings = client.get_settings()
        if clean_name not in settings.connection_profiles:
            raise OpenDVError(
                f"Connection profile '{clean_name}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        profiles = dict(settings.connection_profiles)
        profiles.pop(clean_name)
        client.update_settings(settings.model_copy(update={"connection_profiles": profiles}))
        return {"deleted": True, "name": clean_name}

    run_cli_action(ctx, _run)


@profile_app.command("test")
def settings_profile_test(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Connection profile name to test.")],
) -> None:
    """Inspect and validate a saved connection profile via `sources/inspect`."""

    def _run(client: CliClient) -> Any:
        clean_name = name.strip()
        settings = client.get_settings()
        profile = settings.connection_profiles.get(clean_name)
        if profile is None:
            raise OpenDVError(
                f"Connection profile '{clean_name}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        opts = dict(profile.options)
        uri_or_path = profile.uri_or_path.strip()
        if profile.adapter_id == "databricks_uc":
            raw_host = opts.get("host")
            host_str = raw_host.strip() if isinstance(raw_host, str) else ""
            target = uri_or_path or host_str
            if target and "://" not in target:
                target = f"https://{target}"
            if not host_str and target:
                opts["host"] = target
            uri_or_path = target
        req = SourceInspectReq(
            kind=profile.adapter_id,
            uri_or_path=uri_or_path,
            options=opts,
            secret_refs=list(profile.secret_refs),
        )
        return client.inspect_source(req)

    run_cli_action(ctx, _run)


@project_settings_app.command("show")
def project_settings_show(ctx: typer.Context, project: ProjectOpt) -> None:
    """Show persisted execution settings snapshot for a project."""
    run_cli_action(ctx, lambda c: c.get_project(project).settings_snapshot)


@project_settings_app.command("set")
def project_settings_set(
    ctx: typer.Context,
    project: ProjectOpt,
    harness_provider: HarnessProviderFlag = None,
    cegar_policy: CegarPolicyFlag = None,
    v7_critic_tier: CriticTierFlag = None,
    dbt_sql_dialect: DbtDialectFlag = None,
    secret_precedence: SecretPrecedenceFlag = None,
    max_turns: MaxTurnsFlag = None,
    max_tokens: MaxTokensFlag = None,
    timeout_seconds: TimeoutSecondsFlag = None,
    discovery_mode: DiscoveryModeFlag = None,
    model_override: ModelOverrideFlag = None,
    clear_model_override: ClearModelOverrideFlag = None,
    clear: ClearFieldFlag = None,
) -> None:
    """Atomically patch per-project execution settings for future runs."""

    def _run(client: CliClient) -> Any:
        patch_dict, set_map, clear_targets, cleared = _prepare_settings_mutation(
            {
                "harness_provider": harness_provider,
                "cegar_policy": cegar_policy,
                "v7_critic_tier": v7_critic_tier,
                "dbt_sql_dialect": dbt_sql_dialect,
                "secret_precedence": secret_precedence,
                "discovery_mode": discovery_mode,
            },
            max_turns=max_turns,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            model_override=model_override,
            clear_model_override=clear_model_override,
            clear=clear,
        )
        for flag_key, field_name in _CLEAR_SCALAR_MAP.items():
            if flag_key in cleared:
                patch_dict[field_name] = None
        if "model-overrides" in cleared:
            patch_dict["model_overrides"] = None
        elif set_map or clear_targets:
            ovr_patch: dict[str, str | None] = {target: None for target in clear_targets}
            ovr_patch.update(set_map)
            patch_dict["model_overrides"] = ovr_patch
        patch = ProjectSettingsPatchReq.model_validate(patch_dict)
        return client.update_project_settings(project, patch)

    run_cli_action(ctx, _run)
