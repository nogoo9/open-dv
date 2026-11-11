"""Declarative CLI definitions for `ontology studio` commands."""

from __future__ import annotations

from opendv_service.cli_spec.definitions import CommandDef, OptionDef

STUDIO_COMMAND = CommandDef(
    name="studio",
    description=(
        "Interactive Ontology Construction Studio (FIBO, LiteParse, dual-"
        "mode engine, CAS sessions)"
    ),
    help="Manage interactive Ontology Construction Studio sessions, ingestion, and promotion",
    subcommands=[
        CommandDef(
            name="init",
            description="Initialize local workbench loopback token and DACL permissions",
            help="Initialize local workbench loopback token",
            options=[
                OptionDef(
                    name="force",
                    flag="--force",
                    type="boolean",
                    default=False,
                    help="Force overwrite existing local token if authorized",
                ),
            ],
        ),
        CommandDef(
            name="create",
            description="Create a new ontology construction studio session",
            help="Create a new studio session",
            request_dto="CreateSessionRequest",
            response_dto="StudioSessionDTO",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    help="Optional explicit studio session identifier",
                ),
                OptionDef(
                    name="domain_namespace",
                    flag="--domain-namespace",
                    short="-n",
                    type="string",
                    default="default",
                    help="Domain namespace matching base Graph A",
                ),
                OptionDef(
                    name="enterprise_owner",
                    flag="--enterprise-owner",
                    short="-o",
                    type="string",
                    default="Enterprise",
                    help="Enterprise owner organization or domain",
                ),
                OptionDef(
                    name="ontology_id",
                    flag="--ontology-id",
                    type="string",
                    help="Custom ontology identifier",
                ),
                OptionDef(
                    name="description",
                    flag="--description",
                    short="-d",
                    type="string",
                    help="Session description or business purpose",
                ),
                OptionDef(
                    name="from_workspace",
                    flag="--from-workspace",
                    short="-w",
                    type="path",
                    help="Path to workspace Data Vault project to seed from",
                ),
                OptionDef(
                    name="session_label",
                    flag="--session-label",
                    short="-l",
                    type="string",
                    help="Human-readable label for session identification",
                ),
            ],
        ),
        CommandDef(
            name="list",
            description="List active ontology construction studio sessions",
            help="List active studio sessions",
            options=[
                OptionDef(
                    name="limit",
                    flag="--limit",
                    short="-l",
                    type="integer",
                    default=50,
                    help="Maximum number of sessions to display",
                ),
                OptionDef(
                    name="offset",
                    flag="--offset",
                    type="integer",
                    default=0,
                    help="Pagination offset",
                ),
                OptionDef(
                    name="status",
                    flag="--status",
                    short="-s",
                    type="string",
                    help="Filter sessions by status",
                ),
            ],
        ),
        CommandDef(
            name="status",
            description=(
                "Show status, active version, head SHA-256, sources, and jobs for a studio session"
            ),
            help="Show studio session details",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
            ],
        ),
        CommandDef(
            name="upload",
            description=(
                "Upload candidate source documents (PDF, CSV, TSV, Parquet, MD, "
                "etc.) to session CAS"
            ),
            help="Upload source documents to session",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="file",
                    flag="--file",
                    short="-f",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to document file to upload",
                ),
                OptionDef(
                    name="do_not_send_to_agent",
                    flag="--do-not-send-to-agent",
                    type="boolean",
                    default=False,
                    help=(
                        "Mark source confidential to suppress raw text transmission to "
                        "external LLMs"
                    ),
                ),
                OptionDef(
                    name="description",
                    flag="--description",
                    short="-d",
                    type="string",
                    help="Optional description of source dataset",
                ),
            ],
        ),
        CommandDef(
            name="parse",
            description="Trigger document ingestion and layout parsing job",
            help="Trigger document ingestion and parsing",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="source_id",
                    flag="--source-id",
                    type="string",
                    help="Specific source document ID to parse (defaults to all pending)",
                ),
                OptionDef(
                    name="ocr_mode",
                    flag="--ocr-mode",
                    type="string",
                    default="auto",
                    choices=["auto", "force", "off"],
                    help="OCR routing mode for scanned pages",
                ),
                OptionDef(
                    name="stream",
                    flag="--stream",
                    type="boolean",
                    default=False,
                    help="Follow live SSE event stream until job completes",
                ),
                OptionDef(
                    name="wait",
                    flag="--wait",
                    type="boolean",
                    default=False,
                    help="Block until job completes and return terminal status",
                ),
                OptionDef(
                    name="source_ids",
                    flag="--source-ids",
                    type="string",
                    help="Comma-separated list of source document IDs to parse",
                ),
            ],
        ),
        CommandDef(
            name="clarify",
            description="Generate domain clarification questions from candidate terms",
            help="Generate domain clarification questions",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="focus_concept",
                    flag="--focus-concept",
                    type="string",
                    help="Optional focus concept URI to target clarification",
                ),
                OptionDef(
                    name="max_questions",
                    flag="--max-questions",
                    type="integer",
                    default=5,
                    help="Maximum number of questions to generate",
                ),
                OptionDef(
                    name="stream",
                    flag="--stream",
                    type="boolean",
                    default=False,
                    help="Follow live SSE event stream",
                ),
                OptionDef(
                    name="wait",
                    flag="--wait",
                    type="boolean",
                    default=False,
                    help="Block until clarify job completes",
                ),
            ],
        ),
        CommandDef(
            name="answer",
            description="Submit human answers to clarification questions",
            help="Submit clarification answers",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="file",
                    flag="--file",
                    short="-f",
                    type="path",
                    required=True,
                    inline_upload=True,
                    help="Path to JSON file containing clarification answers",
                ),
            ],
        ),
        CommandDef(
            name="construct",
            description=(
                "Trigger dual-mode ontology construction pipeline (deterministic or agent)"
            ),
            help="Construct Graph A ontology",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="base_version",
                    flag="--base-version",
                    type="string",
                    help="Base SemVer version string",
                ),
                OptionDef(
                    name="base_sha256",
                    flag="--base-sha256",
                    type="string",
                    help="64-character hex SHA-256 digest of base ontology",
                ),
                OptionDef(
                    name="mode",
                    flag="--mode",
                    short="-m",
                    type="string",
                    default="auto",
                    choices=["auto", "agent", "deterministic"],
                    help="Construction execution mode",
                ),
                OptionDef(
                    name="auto_commit",
                    flag="--auto-commit",
                    type="boolean",
                    default=False,
                    help=(
                        "Automatically commit constructed delta without proposal staging "
                        "(requires pinned base)"
                    ),
                ),
                OptionDef(
                    name="stream",
                    flag="--stream",
                    type="boolean",
                    default=False,
                    help="Follow live SSE event stream",
                ),
                OptionDef(
                    name="wait",
                    flag="--wait",
                    type="boolean",
                    default=False,
                    help="Block until construct completes",
                ),
            ],
        ),
        CommandDef(
            name="delta",
            description="Apply human additive delta operations or steward revisions",
            help="Apply ontology delta operations",
            subcommands=[
                CommandDef(
                    name="apply",
                    description="Apply delta operations from file to active session",
                    help="Apply delta operations",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="file",
                            flag="--file",
                            short="-f",
                            type="path",
                            required=True,
                            inline_upload=True,
                            help="Path to JSON file containing delta operations",
                        ),
                        OptionDef(
                            name="base_version",
                            flag="--base-version",
                            type="string",
                            required=True,
                            help="Base SemVer version",
                        ),
                        OptionDef(
                            name="base_sha256",
                            flag="--base-sha256",
                            type="string",
                            required=True,
                            help="Base SHA-256 hash",
                        ),
                        OptionDef(
                            name="rationale",
                            flag="--rationale",
                            short="-r",
                            type="string",
                            default="",
                            help="Business rationale for mutation",
                        ),
                        OptionDef(
                            name="low_confidence_waiver",
                            flag="--low-confidence-waiver",
                            type="boolean",
                            default=False,
                            help="Admin waiver for concepts with low confidence grounding",
                        ),
                        OptionDef(
                            name="grounding_rationale",
                            flag="--grounding-rationale",
                            type="string",
                            help="Admin rationale for grounding waiver (min 10 chars)",
                        ),
                        OptionDef(
                            name="ungrounded_concept",
                            flag="--ungrounded-concept",
                            type="array",
                            help="Explicit list of concept URIs covered by grounding waiver",
                        ),
                        OptionDef(
                            name="evidence_grounding_file",
                            flag="--evidence-grounding-file",
                            type="path",
                            inline_upload=True,
                            help="Path to admin evidence grounding approval JSON",
                        ),
                    ],
                ),
            ],
        ),
        CommandDef(
            name="proposal",
            description=(
                "Manage candidate ontology proposals (list, show, accept, reject, rebase)"
            ),
            help="Manage ontology proposals",
            subcommands=[
                CommandDef(
                    name="list",
                    description="List proposals for a studio session",
                    help="List proposals",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                    ],
                ),
                CommandDef(
                    name="show",
                    description="Display detailed proposal operations and grounding",
                    help="Show proposal details",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="proposal_id",
                            flag="--proposal-id",
                            short="-p",
                            type="string",
                            required=True,
                            help="Proposal identifier",
                        ),
                    ],
                ),
                CommandDef(
                    name="accept",
                    description=(
                        "Accept pending proposal and apply its delta to roll head forward"
                    ),
                    help="Accept proposal",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="proposal_id",
                            flag="--proposal-id",
                            short="-p",
                            type="string",
                            required=True,
                            help="Proposal identifier",
                        ),
                        OptionDef(
                            name="base_sha256",
                            flag="--base-sha256",
                            type="string",
                            required=True,
                            help="Expected base SHA-256 for optimistic concurrency check",
                        ),
                        OptionDef(
                            name="allow_self_accepted_proposals",
                            flag="--allow-self-accepted-proposals",
                            type="boolean",
                            default=False,
                            help=(
                                "Admin override allowing acceptance of own proposal (requires "
                                "local admin)"
                            ),
                        ),
                        OptionDef(
                            name="low_confidence_waiver",
                            flag="--low-confidence-waiver",
                            type="boolean",
                            default=False,
                            help="Admin waiver for low-confidence external mappings",
                        ),
                        OptionDef(
                            name="grounding_rationale",
                            flag="--grounding-rationale",
                            type="string",
                            help="Rationale for grounding waiver (min 10 chars)",
                        ),
                        OptionDef(
                            name="ungrounded_concept",
                            flag="--ungrounded-concept",
                            type="array",
                            help="Concept URIs covered by waiver",
                        ),
                        OptionDef(
                            name="evidence_grounding_file",
                            flag="--evidence-grounding-file",
                            type="path",
                            inline_upload=True,
                            help="Path to admin evidence grounding approval JSON",
                        ),
                    ],
                ),
                CommandDef(
                    name="reject",
                    description="Reject a pending proposal",
                    help="Reject proposal",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="proposal_id",
                            flag="--proposal-id",
                            short="-p",
                            type="string",
                            required=True,
                            help="Proposal identifier",
                        ),
                        OptionDef(
                            name="reason",
                            flag="--reason",
                            short="-r",
                            type="string",
                            help="Rejection rationale",
                        ),
                    ],
                ),
                CommandDef(
                    name="rebase",
                    description="Rebase proposal operations onto latest active session head",
                    help="Rebase proposal",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="proposal_id",
                            flag="--proposal-id",
                            short="-p",
                            type="string",
                            required=True,
                            help="Proposal identifier",
                        ),
                        OptionDef(
                            name="base_sha256",
                            flag="--base-sha256",
                            type="string",
                            required=True,
                            help="Target base SHA-256 for rebase",
                        ),
                    ],
                ),
            ],
        ),
        CommandDef(
            name="export",
            description=(
                "Export constructed Graph A ontology to YAML, JSON, OWL 2/Turtle, or JSON-LD"
            ),
            help="Export constructed ontology",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="export_format",
                    flag="--export-format",
                    short="-e",
                    type="string",
                    default="yaml",
                    choices=["yaml", "json", "turtle", "jsonld"],
                    help="Serialization format",
                ),
                OptionDef(
                    name="version",
                    flag="--version",
                    short="-v",
                    type="string",
                    help="Target ontology version (defaults to active head)",
                ),
                OptionDef(
                    name="out",
                    flag="--out",
                    short="-o",
                    type="path",
                    help="Output file path (client_file_download)",
                ),
            ],
        ),
        CommandDef(
            name="promote",
            description=(
                "Promote active session ontology to production workspace pipeline "
                "under graph_a.lock"
            ),
            help="Promote ontology to workspace",
            options=[
                OptionDef(
                    name="session",
                    flag="--session",
                    short="-s",
                    type="string",
                    required=True,
                    help="Studio session identifier",
                ),
                OptionDef(
                    name="workspace",
                    flag="--workspace",
                    short="-w",
                    type="path",
                    help="Destination workspace Data Vault project path",
                ),
                OptionDef(
                    name="workspace_sha256",
                    flag="--workspace-sha256",
                    type="string",
                    required=True,
                    help="Expected current workspace SHA-256 (64 zeros for greenfield)",
                ),
                OptionDef(
                    name="head_sha256",
                    flag="--head-sha256",
                    type="string",
                    required=True,
                    help="Expected session head SHA-256 digest",
                ),
                OptionDef(
                    name="rationale",
                    flag="--rationale",
                    short="-r",
                    type="string",
                    required=True,
                    help="Required promotion rationale (min 10 characters)",
                ),
                OptionDef(
                    name="acknowledge_historical_contract_warning",
                    flag="--acknowledge-historical-contract-warning",
                    type="boolean",
                    default=False,
                    help="Acknowledge historical deployed contract warning",
                ),
                OptionDef(
                    name="historical_contract_rationale",
                    flag="--historical-contract-rationale",
                    type="string",
                    help="Rationale acknowledging historical contract warning",
                ),
            ],
        ),
        CommandDef(
            name="bundle",
            description=("Manage portable session archives (.odvstudio.zip export, import, clone)"),
            help="Manage session bundles",
            subcommands=[
                CommandDef(
                    name="export",
                    description="Export session into portable .odvstudio.zip archive",
                    help="Export session bundle",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Studio session identifier",
                        ),
                        OptionDef(
                            name="out",
                            flag="--out",
                            short="-o",
                            type="path",
                            required=True,
                            help="Destination file path (.odvstudio.zip)",
                        ),
                        OptionDef(
                            name="acknowledge_confidential_source_export",
                            flag="--acknowledge-confidential-source-export",
                            type="boolean",
                            default=False,
                            help="Acknowledge export of confidential/tainted sources",
                        ),
                        OptionDef(
                            name="export_rationale",
                            flag="--export-rationale",
                            type="string",
                            help="Rationale for exporting confidential session archive",
                        ),
                    ],
                ),
                CommandDef(
                    name="import",
                    description="Import and re-validate portable .odvstudio.zip archive",
                    help="Import session bundle",
                    options=[
                        OptionDef(
                            name="file",
                            flag="--file",
                            short="-f",
                            type="path",
                            required=True,
                            inline_upload=True,
                            help="Path to .odvstudio.zip archive to import",
                        ),
                        OptionDef(
                            name="acknowledge_unverified_import_attribution",
                            flag="--acknowledge-unverified-import-attribution",
                            type="boolean",
                            default=False,
                            help=(
                                "Admin acknowledgement for unverified imported attribution or "
                                "waivers"
                            ),
                        ),
                        OptionDef(
                            name="acknowledgement_rationale",
                            flag="--acknowledgement-rationale",
                            type="string",
                            help="Rationale acknowledging unverified import attribution",
                        ),
                        OptionDef(
                            name="acknowledge_historical_contract_warning",
                            flag="--acknowledge-historical-contract-warning",
                            type="boolean",
                            default=False,
                            help="Acknowledge historical contract warning in imported session",
                        ),
                        OptionDef(
                            name="historical_contract_rationale",
                            flag="--historical-contract-rationale",
                            type="string",
                            help="Rationale acknowledging historical contract warning",
                        ),
                    ],
                ),
                CommandDef(
                    name="clone",
                    description=(
                        "Clone an existing studio session into a new isolated session identifier"
                    ),
                    help="Clone studio session",
                    options=[
                        OptionDef(
                            name="session",
                            flag="--session",
                            short="-s",
                            type="string",
                            required=True,
                            help="Source studio session identifier to clone",
                        ),
                        OptionDef(
                            name="session_label",
                            flag="--session-label",
                            short="-l",
                            type="string",
                            help="Label for the newly cloned session",
                        ),
                        OptionDef(
                            name="new_session_id",
                            flag="--new-session-id",
                            type="string",
                            help=(
                                "Explicit target session identifier for the newly cloned session"
                            ),
                        ),
                    ],
                ),
            ],
        ),
    ],
)

__all__ = ["STUDIO_COMMAND"]
