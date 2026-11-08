"""Declarative CLI definitions for `config` and `serve` platform commands."""

from __future__ import annotations

from opendv_service.cli_spec.definitions import CommandDef, OptionDef
from opendv_service.settings import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

CONFIG_COMMAND = CommandDef(
    name="config",
    description=(
        "Inspect, update, and validate Open-DV platform and agnostic agent harness configuration"
    ),
    help="Manage platform and agent harness configuration",
    availability="both",
    subcommands=[
        CommandDef(
            name="show",
            description=(
                "Display active platform settings, default agent harness configuration, "
                "resolved persona models, and provider readiness"
            ),
            help="Show active platform and agent harness configuration",
            availability="both",
            response_dto="PlatformConfigResponse",
            options=[],
        ),
        CommandDef(
            name="set",
            description=(
                "Update and persist platform default agent harness provider, model routing, "
                "budgets, and governance policies"
            ),
            help="Update platform and agent harness configuration",
            availability="both",
            request_dto="PlatformConfigUpdateRequest",
            response_dto="PlatformConfigResponse",
            options=[
                OptionDef(
                    name="provider",
                    flag="--provider",
                    short="-p",
                    type="string",
                    choices=[
                        "agy",
                        "agy-cli",
                        "antigravity",
                        "claude",
                        "claude-cli",
                        "claude-code",
                    ],
                    help="Default agent harness provider",
                ),
                OptionDef(
                    name="reasoning_model",
                    flag="--reasoning-model",
                    type="string",
                    help="Model override for HIGH_REASONING tier ('reasoning')",
                ),
                OptionDef(
                    name="fast_model",
                    flag="--fast-model",
                    type="string",
                    help="Model override for FAST_TIER ('fast')",
                ),
                OptionDef(
                    name="auth_mode",
                    flag="--auth-mode",
                    type="string",
                    choices=["none", "cli_session", "api_key", "vertex_adc", "aws_bedrock"],
                    help="Authentication mode for the harness provider",
                ),
                OptionDef(
                    name="api_key_env_var",
                    flag="--api-key-env-var",
                    type="string",
                    help="Environment variable name storing the provider API key",
                ),
                OptionDef(
                    name="hold_policy",
                    flag="--hold-policy",
                    type="string",
                    choices=["pause", "auto_approve_top_candidate", "reject_on_hold"],
                    help="Default Steward Hold policy",
                ),
                OptionDef(
                    name="ontology_policy",
                    flag="--ontology-policy",
                    type="string",
                    choices=["off", "recommend", "auto_accept"],
                    help="Default ontology evolution policy (off, recommend, auto_accept)",
                ),
                OptionDef(
                    name="stage_output_policy",
                    flag="--stage-output-policy",
                    type="string",
                    choices=["llm_only", "symbolic_completion"],
                    help="Default stage output synthesis policy",
                ),
                OptionDef(
                    name="max_turns",
                    flag="--max-turns",
                    type="integer",
                    help="Maximum agent turns per task",
                ),
                OptionDef(
                    name="timeout_seconds",
                    flag="--timeout-seconds",
                    type="integer",
                    help="Task execution timeout in seconds",
                ),
                OptionDef(
                    name="harness_config",
                    flag="--harness-config",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Optional path to a full harness_config.yaml file to import",
                ),
            ],
        ),
        CommandDef(
            name="validate",
            description=(
                "Deterministically validate platform and agent harness configuration and "
                "probe provider readiness"
            ),
            help="Validate platform and agent harness configuration",
            availability="both",
            request_dto="PlatformConfigValidateRequest",
            response_dto="PlatformConfigValidationResponse",
            options=[
                OptionDef(
                    name="provider",
                    flag="--provider",
                    short="-p",
                    type="string",
                    choices=[
                        "agy",
                        "agy-cli",
                        "antigravity",
                        "claude",
                        "claude-cli",
                        "claude-code",
                    ],
                    help="Candidate harness provider to validate",
                ),
                OptionDef(
                    name="harness_config",
                    flag="--harness-config",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Optional path to a candidate harness_config.yaml file to validate",
                ),
            ],
        ),
    ],
)

SERVE_COMMAND = CommandDef(
    name="serve",
    description=(
        "Launch the unified Open-DV Platform Server serving both the REST/SSE API "
        "and the embedded static Web Explorer UI"
    ),
    help="Start the unified Open-DV Platform & Web Explorer server",
    availability="standalone_only",
    options=[
        OptionDef(
            name="host",
            flag="--host",
            type="string",
            default=DEFAULT_SERVER_HOST,
            help="Bind host address for the platform server",
        ),
        OptionDef(
            name="port",
            flag="--port",
            short="-p",
            type="integer",
            default=DEFAULT_SERVER_PORT,
            help="Bind port for the platform server and Web Explorer UI",
        ),
        OptionDef(
            name="config",
            flag="--config",
            short="-c",
            type="path",
            env_var="OPENDV_CONFIG",
            help="Path to opendv.config.yaml configuration file",
        ),
        OptionDef(
            name="workspace",
            flag="--workspace",
            short="-w",
            type="path",
            env_var="OPENDV_WORKSPACE_ROOT",
            help="Workspace root directory for path containment and state storage",
        ),
        OptionDef(
            name="open_browser",
            flag="--open",
            type="boolean",
            default=False,
            help="Automatically open the Web Explorer UI in the default browser on startup",
        ),
    ],
)

__all__ = ["CONFIG_COMMAND", "SERVE_COMMAND"]
