"""Declarative taxonomy and definitions of OpenDV CLI commands, subcommands, and options.

This module provides the single source of truth for the language-neutral CLI specification
(REQ-CLI-01, REQ-CLI-02, docs/18 §3C).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OptionType = Literal["string", "integer", "boolean", "path", "array"]
Availability = Literal["both", "standalone_only", "client_only"]


@dataclass(frozen=True)
class OptionDef:
    """Declarative definition of a command option/flag."""

    name: str
    flag: str
    type: OptionType
    help: str
    short: str | None = None
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    env_var: str | None = None
    inline_upload: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert option to dictionary conforming to cli-spec.schema.json."""
        data: dict[str, Any] = {
            "name": self.name,
            "flag": self.flag,
            "short": self.short,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": self.choices,
            "env_var": self.env_var,
            "inline_upload": self.inline_upload,
            "help": self.help,
        }
        return data


@dataclass(frozen=True)
class CommandDef:
    """Declarative definition of a command or subcommand."""

    name: str
    description: str
    help: str
    availability: Availability = "both"
    aliases: list[str] = field(default_factory=list)
    request_dto: str | None = None
    response_dto: str | None = None
    options: list[OptionDef] = field(default_factory=list)
    subcommands: list[CommandDef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary conforming to cli-spec.schema.json."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "help": self.help,
            "aliases": list(self.aliases),
            "availability": self.availability,
        }
        if self.subcommands:
            data["subcommands"] = [sub.to_dict() for sub in self.subcommands]
        else:
            data["request_dto"] = self.request_dto
            data["response_dto"] = self.response_dto
            data["options"] = [opt.to_dict() for opt in self.options]
        return data


# --- Global CLI Options ---
GLOBAL_OPTIONS: list[OptionDef] = [
    OptionDef(
        name="format",
        flag="--format",
        short="-f",
        type="string",
        required=False,
        default="toon",
        choices=["toon", "json", "text"],
        help="Output formatting style (diagnostic TOON, machine RFC 8785 JSON, or human table)",
    ),
    OptionDef(
        name="verbose",
        flag="--verbose",
        short="-v",
        type="boolean",
        required=False,
        default=False,
        help="Enable verbose diagnostic logs",
    ),
    OptionDef(
        name="version",
        flag="--version",
        type="boolean",
        required=False,
        default=False,
        help="Show OpenDV CLI version",
    ),
    OptionDef(
        name="json",
        flag="--json",
        type="boolean",
        required=False,
        default=False,
        help="Format command output as deterministic RFC 8785 canonical JSON",
    ),
    OptionDef(
        name="toon",
        flag="--toon",
        type="boolean",
        required=False,
        default=False,
        help="Print compact diagnostic @toon_v1 trace output",
    ),
    OptionDef(
        name="role",
        flag="--role",
        type="string",
        required=False,
        default="INTERNAL_MODELER",
        choices=[
            "PUBLIC",
            "INTERNAL_MODELER",
            "DATA_STEWARD",
            "AUDITOR_FINANCE",
            "RESTRICTED_PII_CUSTODIAN",
        ],
        env_var="OPENDV_ROLE",
        help="5-Tier RBAC role clearance context",
    ),
]


# --- 1. profile ---
PROFILE_COMMAND = CommandDef(
    name="profile",
    description=(
        "Profile raw CSV or data files to discover schema, types, candidate keys, and "
        "empirical distributions (Graph B)"
    ),
    help="Profile Bronze tabular sources to extract empirical Graph B schema",
    availability="both",
    request_dto="ProfileRequest",
    response_dto="ProfileResponse",
    options=[
        OptionDef(
            name="source",
            flag="--source",
            type="string",
            help="Source URI or adapter spec string to profile",
        ),
        OptionDef(
            name="file",
            flag="--file",
            type="path",
            inline_upload=True,
            help="Path to data file to profile (backwards compatibility alias for --source)",
        ),
        OptionDef(
            name="source_csv",
            flag="--source-csv",
            short="-s",
            type="path",
            inline_upload=True,
            help="Path to source CSV file",
        ),
        OptionDef(
            name="graph_b",
            flag="--graph-b",
            short="-g",
            type="path",
            inline_upload=True,
            help="Path to existing Graph B profile to validate",
        ),
        OptionDef(
            name="project_id",
            flag="--project-id",
            short="-p",
            type="string",
            help="Associated project identifier",
        ),
        OptionDef(
            name="source_system",
            flag="--source-system",
            type="string",
            help="Origin source system identifier",
        ),
        OptionDef(
            name="table_name",
            flag="--table-name",
            short="-t",
            type="string",
            help="Target physical table name",
        ),
    ],
)


# --- 2. compile ---
COMPILE_COMMAND = CommandDef(
    name="compile",
    description=(
        "Compile Data Vault 2.0 contract into Canonical Intermediate Representation (IR) "
        "and 4-Zone SQL"
    ),
    help="Compile Data Vault contract YAML to 4-Zone SQL bundle",
    availability="both",
    request_dto="CompileRequest",
    response_dto="CompileResponse",
    options=[
        OptionDef(
            name="contract",
            flag="--contract",
            short="-c",
            type="path",
            required=True,
            inline_upload=True,
            help="Path to Data Vault 2.0 YAML contract",
        ),
        OptionDef(
            name="canonical_ir",
            flag="--canonical-ir",
            short="-i",
            type="path",
            inline_upload=True,
            help="Optional pre-existing Canonical IR JSON path",
        ),
        OptionDef(
            name="run_id",
            flag="--run-id",
            short="-r",
            type="string",
            help="Associated pipeline run ID",
        ),
    ],
)


# --- 3. verify ---
VERIFY_COMMAND = CommandDef(
    name="verify",
    description=(
        "Execute Axi-DV verification gates against Data Vault 2.0 contracts, profiles, "
        "and compiled bundles"
    ),
    help="Verify Data Vault 2.0 contract against Axi-DV gates",
    aliases=["axi-dv"],
    availability="both",
    request_dto="VerifyRequest",
    response_dto="VerifyResponse",
    options=[
        OptionDef(
            name="contract",
            flag="--contract",
            short="-c",
            type="path",
            required=True,
            inline_upload=True,
            help="Path to Data Vault 2.0 contract YAML",
        ),
        OptionDef(
            name="graph_a",
            flag="--graph-a",
            short="-a",
            type="path",
            inline_upload=True,
            help="Path to Graph A business ontology YAML",
        ),
        OptionDef(
            name="graph_b",
            flag="--graph-b",
            short="-b",
            type="path",
            inline_upload=True,
            help="Path to Graph B statistical profile JSON",
        ),
        OptionDef(
            name="prior_contract",
            flag="--prior-contract",
            type="path",
            inline_upload=True,
            help="Path to baseline contract YAML for grain/nullability drift checks",
        ),
        OptionDef(
            name="compiled_sql",
            flag="--compiled-sql",
            short="-s",
            type="path",
            inline_upload=True,
            help="Path to compiled 4-Zone SQL bundle for AST validation",
        ),
        OptionDef(
            name="run_id",
            flag="--run-id",
            short="-r",
            type="string",
            help="Associated pipeline run ID",
        ),
        OptionDef(
            name="apply_codemods",
            flag="--apply-codemods",
            type="boolean",
            default=True,
            help="Automatically synthesize and apply rule-governed codemod repairs",
        ),
    ],
)


# --- 4. sandbox ---
SANDBOX_COMMAND = CommandDef(
    name="sandbox",
    description=(
        "Execute DuckDB sandbox assertions, point-in-time joins, and dynamic adversarial "
        "batch stress-testing"
    ),
    help="Execute DuckDB dynamic validation sandbox queries",
    availability="both",
    request_dto="SandboxQueryRequest",
    response_dto="SandboxQueryResponse",
    options=[
        OptionDef(
            name="run_id",
            flag="--run-id",
            short="-r",
            type="string",
            help="Target pipeline run ID containing compiled sandbox assets",
        ),
        OptionDef(
            name="contract",
            flag="--contract",
            short="-c",
            type="path",
            inline_upload=True,
            help="Path to Data Vault 2.0 contract YAML",
        ),
        OptionDef(
            name="canonical_ir",
            flag="--canonical-ir",
            short="-i",
            type="path",
            inline_upload=True,
            help="Path to Canonical IR JSON",
        ),
        OptionDef(
            name="query_type",
            flag="--query-type",
            short="-q",
            type="string",
            default="ADVERSARIAL_BATCHES_1_TO_4",
            choices=["ADVERSARIAL_BATCHES_1_TO_4", "QUERY_D10_PIT", "QUERY_D10B_VALUATION"],
            help="Sandbox verification scenario type",
        ),
        OptionDef(
            name="cutoff",
            flag="--cutoff",
            type="string",
            default="2026-11-15 23:59:59",
            help="Point-in-time assertion timestamp cutoff (YYYY-MM-DD HH:MM:SS)",
        ),
        OptionDef(
            name="sql",
            flag="--sql",
            short="-s",
            type="string",
            help="Custom ad-hoc SQL query to execute against active sandbox zones",
        ),
    ],
)


# --- 5. pipeline ---
PIPELINE_COMMAND = CommandDef(
    name="pipeline",
    description="Manage and execute end-to-end multi-agent Data Vault 2.0 synthesis pipelines",
    help="Execute or validate end-to-end Data Vault synthesis pipelines",
    availability="both",
    subcommands=[
        CommandDef(
            name="run",
            description="Execute end-to-end 8-stage synthesis pipeline run",
            help="Execute end-to-end pipeline run",
            availability="both",
            request_dto="PipelineRunRequest",
            response_dto="PipelineRunHandle",
            options=[
                OptionDef(
                    name="input",
                    flag="--input",
                    short="-i",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to pipeline input specification YAML",
                ),
                OptionDef(
                    name="harness_config",
                    flag="--harness-config",
                    short="-c",
                    type="path",
                    help="Path to agent harness configuration YAML",
                ),
                OptionDef(
                    name="initial_contract",
                    flag="--initial-contract",
                    type="path",
                    inline_upload=True,
                    help="Path to starting contract YAML",
                ),
                OptionDef(
                    name="provider",
                    flag="--provider",
                    short="-p",
                    type="string",
                    help="Target harness provider override",
                ),
                OptionDef(
                    name="hold_policy",
                    flag="--hold-policy",
                    type="string",
                    default=None,
                    help="Captain hold intervention policy",
                ),
                OptionDef(
                    name="output_dir",
                    flag="--output-dir",
                    short="-o",
                    type="path",
                    help="Target directory for generated artifacts and reports",
                ),
                OptionDef(
                    name="stream",
                    flag="--stream",
                    short="-s",
                    type="boolean",
                    default=True,
                    help="Stream live stage execution progress",
                ),
                OptionDef(
                    name="stream_tokens",
                    flag="--stream-tokens",
                    type="boolean",
                    default=False,
                    help="Stream live LLM tokens and thinking deltas",
                ),
                OptionDef(
                    name="wave_id",
                    flag="--wave-id",
                    short="-w",
                    type="string",
                    help="Target wave identifier (e.g. W0, W1, W2)",
                ),
            ],
        ),
        CommandDef(
            name="validate",
            description=(
                "Deterministically validate pipeline input specifications, sources, "
                "and harness configuration"
            ),
            help="Validate pipeline inputs and pre-flight configuration",
            availability="both",
            request_dto="PipelineValidateRequest",
            options=[
                OptionDef(
                    name="input",
                    flag="--input",
                    short="-i",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to input spec file",
                ),
                OptionDef(
                    name="harness_config",
                    flag="--harness-config",
                    short="-c",
                    type="path",
                    help="Path to harness config file",
                ),
                OptionDef(
                    name="initial_contract",
                    flag="--initial-contract",
                    type="path",
                    inline_upload=True,
                    help="Path to initial contract YAML",
                ),
                OptionDef(
                    name="provider",
                    flag="--provider",
                    short="-p",
                    type="string",
                    help="Target harness provider override",
                ),
                OptionDef(
                    name="hold_policy",
                    flag="--hold-policy",
                    type="string",
                    help="Steward hold policy",
                ),
            ],
        ),
        CommandDef(
            name="report",
            description="Inspect or dump a completed pipeline run report",
            help="Inspect pipeline execution report",
            availability="standalone_only",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    help="Pipeline run ID",
                ),
                OptionDef(
                    name="report_path",
                    flag="--report-path",
                    short="-o",
                    type="path",
                    help="Export report path",
                ),
            ],
        ),
    ],
)


# --- 6. run ---
RUN_COMMAND = CommandDef(
    name="run",
    description=(
        "Manage platform runs: list, query status, trigger, abort, or inspect event streams"
    ),
    help="Manage pipeline execution runs",
    availability="both",
    subcommands=[
        CommandDef(
            name="list",
            description="List runs for a project",
            help="List runs",
            availability="both",
            options=[
                OptionDef(
                    name="project_id",
                    flag="--project-id",
                    short="-p",
                    type="string",
                    help="Filter by project ID",
                ),
            ],
        ),
        CommandDef(
            name="get",
            description="Get run details and current lifecycle status",
            help="Get run details",
            availability="both",
            response_dto="ProjectRunDTO",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
            ],
        ),
        CommandDef(
            name="create",
            description="Create and initialize a run",
            help="Create a run",
            availability="both",
            request_dto="CreateRunRequest",
            response_dto="PipelineRunHandle",
            options=[
                OptionDef(
                    name="project_id",
                    flag="--project-id",
                    short="-p",
                    type="string",
                    required=True,
                    help="Project ID",
                ),
                OptionDef(
                    name="harness",
                    flag="--harness",
                    type="string",
                    help="Harness configuration name",
                ),
                OptionDef(
                    name="contract",
                    flag="--contract",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Contract YAML path",
                ),
                OptionDef(
                    name="initial_contract_path",
                    flag="--initial-contract-path",
                    type="path",
                    inline_upload=True,
                    help="Path to initial contract YAML file",
                ),
                OptionDef(
                    name="initial_contract",
                    flag="--initial-contract",
                    type="string",
                    help="Inline starting contract YAML or JSON string",
                ),
                OptionDef(
                    name="wave_id",
                    flag="--wave-id",
                    short="-w",
                    type="string",
                    help="Target wave identifier",
                ),
            ],
        ),
        CommandDef(
            name="abort",
            description="Abort an in-flight pipeline run",
            help="Abort run",
            availability="both",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
            ],
        ),
        CommandDef(
            name="events",
            description="Stream or query events for a run",
            help="Stream run events",
            availability="both",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="last_event_id",
                    flag="--last-event-id",
                    type="string",
                    help="Replay starting after this event ID",
                ),
                OptionDef(
                    name="role",
                    flag="--role",
                    type="string",
                    default="PUBLIC",
                    help="RBAC role clearance for event redaction",
                ),
            ],
        ),
        CommandDef(
            name="explorer",
            description="Fetch 3-Level Explorer bundle for a completed or active run",
            help="Fetch 3-Level Explorer bundle",
            availability="both",
            response_dto="ExplorerBundleDTO",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="wave_id",
                    flag="--wave-id",
                    short="-w",
                    type="string",
                    help="Target wave identifier",
                ),
            ],
        ),
        CommandDef(
            name="artifact",
            description="Fetch specific run artifact content and metadata",
            help="Fetch run artifact",
            availability="both",
            response_dto="RunArtifactDTO",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="artifact_id",
                    flag="--artifact-id",
                    short="-a",
                    type="string",
                    required=True,
                    help="Artifact ID to retrieve",
                ),
            ],
        ),
    ],
)


# --- 7. hold ---
HOLD_COMMAND = CommandDef(
    name="hold",
    description="Inspect and resolve Captain-Hold human-in-the-loop escalations",
    help="Manage Captain-Hold escalations",
    availability="both",
    subcommands=[
        CommandDef(
            name="list",
            description="List holds for a run",
            help="List active holds",
            availability="both",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
            ],
        ),
        CommandDef(
            name="decide",
            description="Resolve a Captain-Hold escalation with APPROVE, REJECT, or ANSWER",
            help="Resolve Captain-Hold escalation",
            availability="both",
            request_dto="CaptainHoldDecisionRequest",
            response_dto="CaptainHoldDecisionResponse",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="action",
                    flag="--action",
                    short="-a",
                    type="string",
                    required=True,
                    choices=["APPROVE", "REJECT", "ANSWER"],
                    help="Decision action to take",
                ),
                OptionDef(
                    name="hold_id",
                    flag="--hold-id",
                    type="string",
                    help="Specific hold ID",
                ),
                OptionDef(
                    name="event_id",
                    flag="--event-id",
                    short="-e",
                    type="string",
                    help="Triggering event ID",
                ),
                OptionDef(
                    name="candidate_id",
                    flag="--candidate-id",
                    type="string",
                    help="Selected candidate option ID",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    help="Steward justification notes",
                ),
                OptionDef(
                    name="steward_id",
                    flag="--steward-id",
                    short="-s",
                    type="string",
                    default="steward-cli",
                    help="Acting steward identity",
                ),
            ],
        ),
        CommandDef(
            name="approve",
            description="Approve a Captain-Hold escalation to proceed",
            help="Approve hold",
            aliases=["resolve"],
            availability="both",
            request_dto="CaptainHoldDecisionRequest",
            response_dto="CaptainHoldDecisionResponse",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="hold_id",
                    flag="--hold-id",
                    type="string",
                    help="Specific hold ID",
                ),
                OptionDef(
                    name="event_id",
                    flag="--event-id",
                    short="-e",
                    type="string",
                    help="Triggering event ID",
                ),
                OptionDef(
                    name="candidate_id",
                    flag="--candidate-id",
                    type="string",
                    help="Selected candidate option ID",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    help="Steward justification notes",
                ),
                OptionDef(
                    name="steward_id",
                    flag="--steward-id",
                    short="-s",
                    type="string",
                    default="steward-cli",
                    help="Acting steward identity",
                ),
            ],
        ),
        CommandDef(
            name="reject",
            description="Reject a Captain-Hold escalation",
            help="Reject hold",
            availability="both",
            request_dto="CaptainHoldDecisionRequest",
            response_dto="CaptainHoldDecisionResponse",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="hold_id",
                    flag="--hold-id",
                    type="string",
                    help="Specific hold ID",
                ),
                OptionDef(
                    name="event_id",
                    flag="--event-id",
                    short="-e",
                    type="string",
                    help="Triggering event ID",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    help="Steward rejection notes",
                ),
                OptionDef(
                    name="steward_id",
                    flag="--steward-id",
                    short="-s",
                    type="string",
                    default="steward-cli",
                    help="Acting steward identity",
                ),
            ],
        ),
        CommandDef(
            name="answer",
            description="Answer a Captain-Hold prompt by choosing a candidate option",
            help="Answer hold",
            availability="both",
            request_dto="CaptainHoldDecisionRequest",
            response_dto="CaptainHoldDecisionResponse",
            options=[
                OptionDef(
                    name="run_id",
                    flag="--run-id",
                    short="-r",
                    type="string",
                    required=True,
                    help="Target run ID",
                ),
                OptionDef(
                    name="hold_id",
                    flag="--hold-id",
                    type="string",
                    help="Specific hold ID",
                ),
                OptionDef(
                    name="event_id",
                    flag="--event-id",
                    short="-e",
                    type="string",
                    help="Triggering event ID",
                ),
                OptionDef(
                    name="candidate_id",
                    flag="--candidate-id",
                    type="string",
                    help="Selected candidate option ID",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    help="Steward response notes",
                ),
                OptionDef(
                    name="steward_id",
                    flag="--steward-id",
                    short="-s",
                    type="string",
                    default="steward-cli",
                    help="Acting steward identity",
                ),
            ],
        ),
    ],
)


# --- 8. ontology (Phase P7) ---
ONTOLOGY_COMMAND = CommandDef(
    name="ontology",
    description=(
        "Manage and evolve enterprise Business Concept ontologies (Graph A), proposals, and deltas"
    ),
    help="Manage Graph A ontology suggestions, deltas, diffs, and decisions",
    availability="both",
    subcommands=[
        CommandDef(
            name="suggest",
            description=(
                "Generate an additive ontology evolution proposal for Graph A "
                "from source data profiles (IP-1)"
            ),
            help="Suggest ontology proposal from source profiles",
            availability="both",
            request_dto="OntologySuggestionRequest",
            response_dto="OntologyProposal",
            options=[
                OptionDef(
                    name="base",
                    flag="--base",
                    short="-b",
                    type="path",
                    inline_upload=True,
                    help="Path to base Graph A ontology YAML/JSON file",
                ),
                OptionDef(
                    name="profile",
                    flag="--profile",
                    short="-p",
                    type="path",
                    inline_upload=True,
                    help="Path to empirical Graph B data profile JSON file",
                ),
                OptionDef(
                    name="source",
                    flag="--source",
                    short="-s",
                    type="path",
                    inline_upload=True,
                    help="Path to bronze source dataset file",
                ),
                OptionDef(
                    name="context_doc",
                    flag="--context-doc",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Path to business context document (e.g. glossary, dictionary, policy)",
                ),
                OptionDef(
                    name="mode",
                    flag="--mode",
                    short="-m",
                    type="string",
                    default="DELTA",
                    choices=["BOOTSTRAP", "DELTA", "HOLD_RESOLUTION"],
                    help="Recommender operation mode (BOOTSTRAP without base ontology, or DELTA)",
                ),
                OptionDef(
                    name="domain_namespace",
                    flag="--domain-namespace",
                    type="string",
                    help="Target domain namespace",
                ),
                OptionDef(
                    name="recommender_id",
                    flag="--recommender-id",
                    type="string",
                    help="Identifier of specific ontology recommender plugin to use",
                ),
                OptionDef(
                    name="output",
                    flag="--output",
                    short="-o",
                    type="path",
                    help="Path to write output proposal JSON",
                ),
            ],
        ),
        CommandDef(
            name="validate-delta",
            description=(
                "Deterministically validate an additive ontology delta against deployed "
                "contract and base Graph A (ONT-EVO-001..003)"
            ),
            help="Validate an ontology delta",
            availability="both",
            request_dto="OntologyValidateDeltaRequest",
            response_dto="OntologyValidationResult",
            options=[
                OptionDef(
                    name="delta",
                    flag="--delta",
                    short="-d",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to OntologyDelta JSON/YAML file to validate",
                ),
                OptionDef(
                    name="base",
                    flag="--base",
                    short="-b",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to base Graph A ontology YAML/JSON file",
                ),
                OptionDef(
                    name="contract",
                    flag="--contract",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Path to deployed Data Vault contract YAML file",
                ),
            ],
        ),
        CommandDef(
            name="diff",
            description=(
                "Compute structural diff between two Graph A ontologies "
                "(additions, refinements, breaking changes)"
            ),
            help="Compute diff between two ontologies",
            availability="both",
            request_dto="OntologyDiffRequest",
            response_dto="OntologyDiff",
            options=[
                OptionDef(
                    name="base",
                    flag="--base",
                    short="-b",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to base Graph A ontology YAML/JSON file",
                ),
                OptionDef(
                    name="target",
                    flag="--target",
                    short="-t",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to target Graph A ontology YAML/JSON file",
                ),
            ],
        ),
        CommandDef(
            name="accept",
            description=(
                "Apply accepted delta operations to a base ontology, generate Graph A vN+1 "
                "with CAS lineage, and record decision"
            ),
            help="Accept and apply an ontology delta",
            availability="both",
            request_dto="OntologyDecisionRequest",
            response_dto="OntologyDecisionResponse",
            options=[
                OptionDef(
                    name="delta",
                    flag="--delta",
                    short="-d",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to OntologyDelta JSON/YAML file",
                ),
                OptionDef(
                    name="base",
                    flag="--base",
                    short="-b",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to base Graph A ontology YAML/JSON file",
                ),
                OptionDef(
                    name="contract",
                    flag="--contract",
                    short="-c",
                    type="path",
                    inline_upload=True,
                    help="Path to deployed Data Vault contract YAML file",
                ),
                OptionDef(
                    name="op_id",
                    flag="--op-id",
                    type="string",
                    help="Specific operation ID to accept (can be repeated or comma-separated)",
                ),
                OptionDef(
                    name="decided_by",
                    flag="--decided-by",
                    type="string",
                    default="steward",
                    help="Identifier of the decision maker (e.g. 'steward' or policy name)",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    default="",
                    help="Steward decision rationale notes",
                ),
                OptionDef(
                    name="output",
                    flag="--output",
                    short="-o",
                    type="path",
                    help="Path to write resulting Graph A vN+1 YAML file",
                ),
            ],
        ),
        CommandDef(
            name="reject",
            description="Record steward rejection of an ontology delta",
            help="Reject an ontology delta",
            availability="both",
            request_dto="OntologyDecisionRequest",
            response_dto="OntologyDecisionResponse",
            options=[
                OptionDef(
                    name="delta",
                    flag="--delta",
                    short="-d",
                    type="path",
                    inline_upload=True,
                    help="Path to OntologyDelta JSON/YAML file",
                ),
                OptionDef(
                    name="proposal_id",
                    flag="--proposal-id",
                    short="-p",
                    type="string",
                    help="Proposal identifier being rejected",
                ),
                OptionDef(
                    name="base",
                    flag="--base",
                    short="-b",
                    type="path",
                    inline_upload=True,
                    help="Path to base Graph A ontology YAML/JSON file",
                ),
                OptionDef(
                    name="decided_by",
                    flag="--decided-by",
                    type="string",
                    default="steward",
                    help="Identifier of the decision maker",
                ),
                OptionDef(
                    name="notes",
                    flag="--notes",
                    short="-n",
                    type="string",
                    default="",
                    help="Steward rejection rationale notes",
                ),
            ],
        ),
    ],
)

# Canonical active command list for Phase P7
from opendv_service.cli_spec.platform_commands import (  # noqa: E402
    CONFIG_COMMAND,
    SERVE_COMMAND,
)

CORE_COMMANDS: list[CommandDef] = [
    PROFILE_COMMAND,
    COMPILE_COMMAND,
    VERIFY_COMMAND,
    SANDBOX_COMMAND,
    PIPELINE_COMMAND,
    RUN_COMMAND,
    HOLD_COMMAND,
    ONTOLOGY_COMMAND,
    CONFIG_COMMAND,
    SERVE_COMMAND,
]

ALL_COMMANDS: list[CommandDef] = [
    *CORE_COMMANDS,
]
