"""Core base commands (profile, compile, verify, sandbox, ontology, source)."""

from __future__ import annotations

from opendv_service.cli_spec.schema import CommandDef, OptionDef
from opendv_service.cli_spec.studio_commands import STUDIO_COMMAND

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
            choices=[
                "ADVERSARIAL_BATCHES_1_TO_4",
                "QUERY_D10_PIT",
                "QUERY_D10B_VALUATION",
                "CUSTOM_SQL",
            ],
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
        STUDIO_COMMAND,
    ],
)

# --- 8b. source ---
SOURCE_COMMAND = CommandDef(
    name="source",
    description="Inspect and discover external source systems, catalogs, and schemas",
    help="Inspect source catalogs, tables, and constraints",
    availability="both",
    subcommands=[
        CommandDef(
            name="inspect",
            description="Inspect tables, columns, primary keys, and foreign keys in a source",
            help="Inspect source schema, tables, and constraints",
            availability="both",
            request_dto="SourceInspectRequestDTO",
            response_dto="SourceInspectResponseDTO",
            options=[
                OptionDef(
                    name="uri",
                    flag="--uri",
                    short="-u",
                    type="string",
                    required=False,
                    help="Source URI or file path to inspect (e.g. sqlite:///path/to/db.sqlite)",
                ),
                OptionDef(
                    name="adapter",
                    flag="--adapter",
                    short="-a",
                    type="string",
                    required=False,
                    help="Target source adapter identifier (e.g. sqlite, databricks, duckdb, file)",
                ),
                OptionDef(
                    name="namespace",
                    flag="--namespace",
                    short="-n",
                    type="string",
                    required=False,
                    help="Namespace, schema, or catalog name to scope inspection",
                ),
                OptionDef(
                    name="table",
                    flag="--table",
                    short="-t",
                    type="string",
                    required=False,
                    help="Target physical table name to inspect",
                ),
                OptionDef(
                    name="credentials_ref",
                    flag="--credentials-ref",
                    type="string",
                    required=False,
                    help="Credentials reference (e.g. env:VAR_NAME)",
                ),
            ],
        ),
    ],
)

__all__ = [
    "COMPILE_COMMAND",
    "ONTOLOGY_COMMAND",
    "PROFILE_COMMAND",
    "SANDBOX_COMMAND",
    "SOURCE_COMMAND",
    "VERIFY_COMMAND",
]
