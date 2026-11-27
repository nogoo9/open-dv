import type { components } from "./types.generated";

type S = components["schemas"];

export type HealthResp = S["HealthResp"];
export type WorkspaceSettings = S["WorkspaceSettings"];
export type ProjectSettingsSnapshot = S["ProjectSettingsSnapshot"];
export type TrustedWorkspaceSpec = S["TrustedWorkspaceSpec"];
export type SecretWriteReq = S["SecretWriteReq"];
export type SecretSummary = S["SecretSummary"];
export type ExternalMcpServerSpec = S["ExternalMcpServerSpec"];
export type AgentPackSummary = S["AgentPackSummary"];
export type AgentPackDetail = S["AgentPackDetail"];

export type ProjectSummary = S["ProjectSummary"];
export type ProjectState = S["ProjectState"];
export type ProjectInitReq = S["ProjectInitReq"];
export type ProjectImportPathReq = S["ProjectImportPathReq"];
export type ImportReadinessReport = S["ImportReadinessReport"];
export type WorkspaceSeedSummary = S["WorkspaceSeedSummary"];
export type StarterPackSummary = S["StarterPackSummary"];
export type StarterPackPreview = S["StarterPackPreview"];

export type ConnectorFieldSpec = S["ConnectorFieldSpec"];
export type ConnectorAdapterSpec = S["ConnectorAdapterSpec"];
export type ConnectorConfigPayload = S["ConnectorConfigPayload"];
export type SourceSummary = S["SourceSummary"];
export type SourceInspectReq = S["SourceInspectReq"];
export type SourceAttachReq = S["SourceAttachReq"];
export type SourceReattachReq = S["SourceReattachReq"];
export type SourceProfile = S["SourceProfile"];
export type TableProfile = S["TableProfile"];
export type ColumnProfile = S["ColumnProfile"];
export type TableIndexEntry = S["TableIndexEntry"];
export type TablePreviewPage = S["TablePreviewPage"];
export type OutlineNode = S["OutlineNode"];
export type PageMapSpec = S["PageMapSpec"];
export type DocumentMap = S["DocumentMap"];
export type PageTextView = S["PageTextView"];
export type ScanApprovalReq = S["ScanApprovalReq"];
export type ConceptMemorySpec = S["ConceptMemorySpec"];
export type ConceptMemoryUpdate = S["ConceptMemoryUpdate"];

export type GraphAOntology = S["GraphAOntology"];
export type CanonicalBusinessConcept = S["CanonicalBusinessConcept"];
export type ConceptAttribute = S["ConceptAttribute-Output"];
export type ConceptAttributeInput = S["ConceptAttribute-Input"];
export type OntologyRelationship = S["OntologyRelationship-Output"];
export type OntologyRelationshipInput = S["OntologyRelationship-Input"];
export type OntologyEnumeration = S["OntologyEnumeration"];
export type ConceptTableBinding = S["ConceptTableBinding"];
export type KeyAuthorityPattern = S["KeyAuthorityPattern"];
export type ExternalMapping = S["ExternalMapping-Output"];
export type ExternalMappingInput = S["ExternalMapping-Input"];
export type DocumentCitationRef = S["DocumentCitationRef"];
export type ExternalMcpEvidenceRef = S["ExternalMcpEvidenceRef"];
export type KeyTierEnum = CanonicalBusinessConcept["key_authority_tier"];
export type PortableLogicalType = ConceptAttribute["logical_type"];

export type OntologyBundleResp = S["OntologyBundleResp"];
export type EvolutionLockSpec = S["EvolutionLockSpec"];
export type StagedCandidateSpec = S["StagedCandidateSpec"];
export type OntologyProposalSpec = S["OntologyProposalSpec"];
export type OntologyWaiverSpec = S["OntologyWaiverSpec"];
export type EvidenceGroundingApproval = S["EvidenceGroundingApproval"];
export type OntologyConstructReq = S["OntologyConstructReq"];
export type OntologyValidateReq = S["OntologyValidateReq"];
export type OntologyValidationResp = S["OntologyValidationResp"];
export type ValidationBumpSpec = S["ValidationBumpSpec"];
export type OntologyCommitReq = S["OntologyCommitReq"];
export type OntologyRevisionResp = S["OntologyRevisionResp"];
export type OntologyDelta = NonNullable<
	OntologyValidateReq["operations"]
>[number];
export type OntologyProposalDelta = OntologyProposalSpec["delta_ops"][number];

export type FiboQueryReq = S["FiboQueryReq"];
export type FiboMatchSpec = S["FiboMatchSpec"];
export type FiboQueryResp = S["FiboQueryResp"];
export type CitationResolveReq = S["CitationResolveReq"];
export type CitationSpanResp = S["CitationSpanResp"];

export type RunStateResp = S["RunStateResp"];
export type RunStatus = RunStateResp["status"];
export type RunControlReq = S["RunControlReq"];
export type RunControlAction = RunControlReq["action"];
export type HoldAnswersReq = S["HoldAnswersReq"];
export type RunStreamEvent = S["RunStreamEvent"];
export type RunEventLogPage = S["RunEventLogPage"];
export type AttemptSnapshotSpec = S["AttemptSnapshotSpec"];
export type ClarificationQuestion = S["ClarificationQuestion"];
export type HorizonQuestion = S["HorizonQuestion"];
export type HorizonOptionSpec = S["HorizonOptionSpec"];
export type HorizonActionReq = S["HorizonActionReq"];
export type HorizonCriticVerdict = S["HorizonCriticVerdict"];
export type BucketAOperatorCall = S["BucketAOperatorCall"];
export type ExternalMcpReceipt = S["ExternalMcpReceipt"];

export type VaultBuildReq = S["VaultBuildReq"];
export type VaultVerifyResp = S["VaultVerifyResp"];
export type WaiverSubmitReq = S["WaiverSubmitReq"];
export type AntiPatternJustification = S["AntiPatternJustification"];
export type VerifierDiagnostic = S["VerifierDiagnostic"];
export type EmitterDefectReport = S["EmitterDefectReport"];
export type GuidedRunView = S["GuidedRunView"];
export type GuidedDecisionCard = S["GuidedDecisionCard"];
export type GuidedReadinessBrief = S["GuidedReadinessBrief"];
export type GuidedEntityCardSpec = S["GuidedEntityCardSpec"];
export type GuidedTableExplainerSpec = S["GuidedTableExplainerSpec"];
export type GuidedWaiverTargetSpec = S["GuidedWaiverTargetSpec"];

export type DraftVaultIR = S["DraftVaultIR"];
export type CertifiedVaultIR = S["CertifiedVaultIR"];
export type SchemaWaveDelta = S["SchemaWaveDelta"];
export type BronzeTableSpec = S["BronzeTableSpec"];
export type StagingViewSpec = S["StagingViewSpec"];
export type CertifiedHubSpec = S["CertifiedHubSpec"];
export type CertifiedLinkSpec = S["CertifiedLinkSpec"];
export type CertifiedSatelliteSpec = S["CertifiedSatelliteSpec"];
export type RefTableSpec = S["RefTableSpec"];
export type PitBridgeSpec = S["PitBridgeSpec"];
export type MartViewSpec = S["MartViewSpec"];
export type IRField = S["IRField"];

export type TargetManifest = S["TargetManifest"];
export type ArtifactFileSpec = S["ArtifactFileSpec"];
export type TargetBundleResp = S["TargetBundleResp"];
export type StorageCompileReq = S["StorageCompileReq"];
export type StorageTargetReq = S["StorageTargetReq"];
export type MigrationApplyResp = S["MigrationApplyResp"];
export type SqlQueryReq = S["SqlQueryReq"];
export type SqlQueryResult = S["SqlQueryResult"];
export type ProblemDetails = S["ProblemDetails"];
export type ErrorCode = S["ErrorCode"];
export type RemediationAction = S["RemediationAction"];
export type ErrorRemediation = S["ErrorRemediation"];
export type ModelOption = S["ModelOption"];
export type ProviderReadinessRecord = S["ProviderReadinessRecord"];
export type ReadinessResponse = S["ReadinessResponse"];
export type ReadinessTestReq = S["ReadinessTestReq"];
export type RevokeTrustedWorkspaceReq = S["RevokeTrustedWorkspaceReq"];

export type CegarPolicy = NonNullable<RunStateResp["policy"]>;
export type DiscoveryMode = ProjectSettingsSnapshot["discovery_mode"];
export type HarnessProvider = WorkspaceSettings["harness_provider"];
export type DbtSqlDialect = WorkspaceSettings["dbt_sql_dialect"];

const P = "/api/v1/projects/{project_id}" as const;

export const API_ROUTES = {
	health: "/api/v1/health",
	settings: "/api/v1/settings",
	settingsRevokeTrustedWorkspace: "/api/v1/settings/trusted-workspaces/revoke",
	secrets: "/api/v1/secrets",
	secret: "/api/v1/secrets/{key}",
	mcpServers: "/api/v1/mcp/servers",
	mcpServer: "/api/v1/mcp/servers/{server_id}",
	agents: "/api/v1/agents",
	agentsReadiness: "/api/v1/agents/readiness",
	agentsReadinessTest: "/api/v1/agents/readiness/test",
	agent: "/api/v1/agents/{agent_id}",
	fiboQaGlobal: "/api/v1/ontology/fibo-qa",
	projects: "/api/v1/projects",
	projectImport: "/api/v1/projects/import",
	project: P,
	projectBundle: `${P}/bundle`,
	workspaceSeeds: "/api/v1/workspace-seeds",
	starterPacks: "/api/v1/starter-packs",
	starterPackPreview: "/api/v1/starter-packs/{pack_id}/preview",
	sourceAdapters: "/api/v1/sources/adapters",
	sourceInspect: "/api/v1/sources/inspect",
	projectSources: `${P}/sources`,
	projectSourceUpload: `${P}/sources/upload`,
	projectSource: `${P}/sources/{sid}`,
	tablesIndex: `${P}/evidence/tables/index`,
	tablePreview: `${P}/evidence/tables/{tid}`,
	documentsMap: `${P}/evidence/documents/map`,
	documentPages: `${P}/evidence/documents/{did}/pages`,
	approveScan: `${P}/evidence/documents/{did}/approve-scan`,
	conceptMemory: `${P}/evidence/concept-memory`,
	ontology: `${P}/ontology`,
	ontologyConstruct: `${P}/ontology/construct`,
	ontologyRun: `${P}/ontology/runs/{rid}`,
	ontologyRunEvents: `${P}/ontology/runs/{rid}/events`,
	ontologyRunControls: `${P}/ontology/runs/{rid}/controls`,
	ontologyRunAnswers: `${P}/ontology/runs/{rid}/answers`,
	ontologyFiboQa: `${P}/ontology/fibo-qa`,
	ontologyCitationsResolve: `${P}/ontology/citations/resolve`,
	ontologyValidate: `${P}/ontology/validate`,
	ontologyCommit: `${P}/ontology/commit`,
	ontologyExport: `${P}/ontology/export`,
	runs: `${P}/runs`,
	run: `${P}/runs/{rid}`,
	runLogs: `${P}/runs/{rid}/logs`,
	runAttempts: `${P}/runs/{rid}/attempts`,
	runEvents: `${P}/runs/{rid}/events`,
	runControls: `${P}/runs/{rid}/controls`,
	runDecisions: `${P}/runs/{rid}/decisions`,
	runHorizon: `${P}/runs/{rid}/horizon`,
	runGuidedView: `${P}/runs/{rid}/guided-view`,
	vaultWaivers: `${P}/vault/waivers`,
	vaultIr: `${P}/vault/ir`,
	vaultVerify: `${P}/vault/verify`,
	receipts: `${P}/receipts`,
	storageTargets: `${P}/storage/targets`,
	storageCompile: `${P}/storage/compile`,
	storageMigrate: `${P}/storage/migrate`,
	storageRegenerate: `${P}/storage/regenerate`,
	storageQuery: `${P}/storage/query`,
} as const;

export function buildApiRoute(
	tpl: string,
	params: Record<string, string> = {},
): string {
	return tpl.replace(/\{([a-z_]+)\}/g, (_, k: string) =>
		encodeURIComponent(params[k] ?? ""),
	);
}

export const PROJECT_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const DOMAIN_NAMESPACE_RE = /^[a-z][a-z0-9_]{0,63}$/;
export const SERVER_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const WINDOWS_RESERVED_NAMES = new Set([
	"con",
	"prn",
	"aux",
	"nul",
	"com1",
	"com2",
	"com3",
	"com4",
	"com5",
	"com6",
	"com7",
	"com8",
	"com9",
	"lpt1",
	"lpt2",
	"lpt3",
	"lpt4",
	"lpt5",
	"lpt6",
	"lpt7",
	"lpt8",
	"lpt9",
]);

export function validateProjectId(v: string): string | null {
	if (!PROJECT_ID_RE.test(v))
		return "Project ID must match ^[a-z0-9][a-z0-9_-]{0,63}$.";
	const lower = v.toLowerCase();
	return lower === "import" || WINDOWS_RESERVED_NAMES.has(lower)
		? `Project ID '${v}' is reserved and cannot be used.`
		: null;
}

export function validateDomainNamespace(v: string): string | null {
	return DOMAIN_NAMESPACE_RE.test(v)
		? null
		: "Domain namespace must match ^[a-z][a-z0-9_]{0,63}$.";
}

export function validateServerId(v: string): string | null {
	return SERVER_ID_RE.test(v)
		? null
		: "Server ID must match ^[a-z0-9][a-z0-9_-]{0,63}$.";
}

export const MAX_UPLOAD_BYTES = 262_144_000;
export const UPLOAD_EXT_TO_KIND = {
	".sqlite": "sqlite",
	".db": "sqlite",
	".sqlite3": "sqlite",
	".duckdb": "duckdb",
	".ddb": "duckdb",
	".parquet": "parquet",
	".csv": "csv",
	".xlsx": "xlsx",
	".pdf": "pdf",
	".md": "markdown",
	".markdown": "markdown",
	".docx": "docx",
} as const;

export type UploadSourceKind =
	(typeof UPLOAD_EXT_TO_KIND)[keyof typeof UPLOAD_EXT_TO_KIND];

export function inferUploadKind(filename: string): UploadSourceKind | null {
	const dot = filename.lastIndexOf(".");
	return dot < 0
		? null
		: ((UPLOAD_EXT_TO_KIND as Record<string, UploadSourceKind>)[
				filename.slice(dot).toLowerCase()
			] ?? null);
}

export interface StaticTargetMeta {
	readonly target_id:
		| "duckdb_local"
		| "databricks_uc"
		| "snowflake"
		| "postgres"
		| "bigquery"
		| "sqlserver_fabric"
		| "redshift"
		| "clickhouse"
		| "iceberg"
		| "parquet"
		| "dbt";
	readonly display_name: string;
	readonly verification_tier:
		| "RUNTIME_VERIFIED"
		| "SYNTAX_AND_CONTRACT_CHECKED";
	readonly max_ident_bytes: number;
	readonly supports_migrate: boolean;
	readonly supports_regen: boolean;
	readonly summary: string;
}

export const STATIC_11_TARGETS: readonly StaticTargetMeta[] = (
	[
		[
			"duckdb_local",
			"DuckDB Local",
			1,
			255,
			1,
			1,
			"Local embedded SQL + oracle parity",
		],
		[
			"databricks_uc",
			"Databricks Unity Catalog",
			0,
			255,
			0,
			0,
			"Delta Lake MERGE",
		],
		["snowflake", "Snowflake", 0, 255, 0, 0, "MERGE WHEN NOT MATCHED"],
		["postgres", "PostgreSQL", 0, 63, 0, 0, "max ident 63 B · ON CONFLICT"],
		["bigquery", "BigQuery", 0, 300, 0, 0, "max ident 300 B"],
		[
			"sqlserver_fabric",
			"SQL Server / Fabric",
			0,
			128,
			0,
			0,
			"max ident 128 B · Latin1_General_100_BIN2_UTF8",
		],
		[
			"redshift",
			"Amazon Redshift",
			0,
			127,
			0,
			0,
			"max ident 127 B · LEFT ANTI JOIN",
		],
		["clickhouse", "ClickHouse", 0, 255, 0, 0, "MergeTree · join_use_nulls=1"],
		["iceberg", "Apache Iceberg", 0, 255, 0, 0, "v2 table metadata"],
		["parquet", "Apache Parquet", 1, 255, 1, 0, "RUNTIME_VERIFIED ZSTD"],
		["dbt", "dbt (automate_dv)", 0, 255, 0, 0, "automate_dv 0.11.x"],
	] as const
).map(([target_id, display_name, rt, max_ident_bytes, mig, reg, summary]) => ({
	target_id,
	display_name,
	verification_tier: rt ? "RUNTIME_VERIFIED" : "SYNTAX_AND_CONTRACT_CHECKED",
	max_ident_bytes,
	supports_migrate: Boolean(mig),
	supports_regen: Boolean(reg),
	summary,
}));

export const FORBIDDEN_GUIDED_JARGON =
	/(?<![A-Za-z0-9])(DV-(?:STR|DYN|COL|AST|EVO|AP|G)-\d*|ONT-(?:AP|EVO)-\d+|ALG\.?\s*A\.[1-9][ab]?|EVAL-(?:AST|RUN|FMT)-\d+|(?:TRAP|BRK|KEY|TMP|INV|EVO)-\d+|BKCC|CDK|CEGAR|SQLGlot|Hash[\s_]*Diff|TIER_U3)(?![A-Za-z0-9])/i;

export interface BudgetCaps {
	readonly max_turns: number;
	readonly max_tokens: number;
	readonly timeout_seconds: number;
}
export const STEP4_DEFAULT_BUDGET: BudgetCaps = {
	max_turns: 24,
	max_tokens: 300_000,
	timeout_seconds: 600,
};
export const CEGAR_POLICY_DEFAULTS: Record<
	CegarPolicy,
	BudgetCaps & {
		readonly label: string;
		readonly k_max: number;
		readonly v7_floor: number | null;
		readonly v7_tier_locked: boolean;
		readonly v8_mode: "off" | "on_demand" | "auto";
	}
> = {
	symbolic_only: {
		label: "Checks Only",
		k_max: 0,
		v7_floor: null,
		v7_tier_locked: false,
		v8_mode: "off",
		max_turns: 1,
		max_tokens: 60_000,
		timeout_seconds: 120,
	},
	fast: {
		label: "Quick",
		k_max: 2,
		v7_floor: null,
		v7_tier_locked: false,
		v8_mode: "on_demand",
		max_turns: 24,
		max_tokens: 250_000,
		timeout_seconds: 600,
	},
	balanced: {
		label: "Balanced",
		k_max: 4,
		v7_floor: 90,
		v7_tier_locked: false,
		v8_mode: "auto",
		max_turns: 40,
		max_tokens: 500_000,
		timeout_seconds: 1_200,
	},
	strict: {
		label: "Strict",
		k_max: 6,
		v7_floor: 95,
		v7_tier_locked: true,
		v8_mode: "auto",
		max_turns: 60,
		max_tokens: 800_000,
		timeout_seconds: 1_800,
	},
};

export function isRunLiveStatus(s: RunStatus | undefined | null): boolean {
	return s === "QUEUED" || s === "RUNNING" || s === "HOLD_REQUESTED";
}
export function isRunHaltStatus(s: RunStatus | undefined | null): boolean {
	return (
		s === "COMPLETED" ||
		s === "FAILED" ||
		s === "HOLD_CHECKPOINTED" ||
		s === "UNCERTIFIED_DRAFT"
	);
}
