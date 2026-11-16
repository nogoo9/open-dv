/**
 * 4-Step Wizard Pipeline Builder Type Definitions (ADR-017, ADR-019, ADR-021).
 *
 * Defines the canonical state model, step identifiers, and component contracts
 * for the guided pipeline builder UI.
 */

import type {
	ConnectorUIManifestDTO,
	InferredExecutionPlanDTO,
	PipelineInputDTO,
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
	SamplingPolicyDTO,
	SamplingStrategy,
	SourceFeedDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import type { PipelineValidationReport } from "../modals/NewRunValidationSection";

/**
 * The 4 canonical guided wizard step identifiers.
 */
export type WizardStepId =
	| "foundation"
	| "connectors"
	| "governance"
	| "validate_run";

/**
 * Foundation source selection mode for Step 1.
 */
export type FoundationMode =
	| "studio_session"
	| "vault_blueprint"
	| "raw_ontology";

/**
 * Inferred architectural execution mode displayed on Step 1.
 */
export type InferredModeBanner =
	| "GREENFIELD_DESIGN"
	| "SCHEMA_EVOLUTION"
	| "ZERO_DATA_COLD_START"
	| "ZERO_DATA_VERIFIED_NO_OP";

/**
 * Canonical form state holding all configuration inputs for the 4-step wizard.
 */
export interface WizardFormState {
	// Step 1: Foundation
	foundationMode: FoundationMode;
	activeSessionId: string | null;
	blueprintPath: string;
	blueprintContent: string;
	blueprint: VaultBlueprintDTO | null;
	ontologyPath: string;
	ontologyContent: string;
	ontologySource: Record<string, unknown> | null;
	contractPath: string;
	contractContent: string;
	contract: Record<string, unknown> | null;

	// Step 2: Data Sources & Unstructured Documents
	configuredSources: SourceFeedDTO[];
	unstructuredDocs: Array<Record<string, unknown>>;

	// Step 3: Modeling Governance & Dialects
	agentModel: string;
	targetDialects: string[];
	samplingRows: number;
	samplingStrategy: SamplingStrategy;
	stewardHoldPolicy: "pause" | "auto_approve_top_candidate" | "reject_on_hold";
	ontologyEvolutionPolicy: "recommend" | "auto_accept" | "strict_lock";
	harnessProvider: string;

	// Metadata & Overrides
	domainNamespace: string;
	pipelineId: string;
	advancedOverrides?: Record<string, unknown>;

	// Step 4 Execution Plan & Validation Cache
	inferredPlan?: InferredExecutionPlanDTO | null;
	validationReport?: PipelineValidationReport | null;
}

/**
 * Configuration descriptor for each wizard step.
 */
export interface WizardStepConfig {
	id: WizardStepId;
	stepNumber: number;
	title: string;
	shortTitle: string;
	description: string;
}

export const WIZARD_STEPS: readonly WizardStepConfig[] = [
	{
		id: "foundation",
		stepNumber: 1,
		title: "Foundation & Inferred Mode",
		shortTitle: "Foundation",
		description:
			"Select active Studio session, previous VaultBlueprint, or raw ontology",
	},
	{
		id: "connectors",
		stepNumber: 2,
		title: "Data Sources & Connectors",
		shortTitle: "Connectors",
		description:
			"Configure tabular files, SQLite catalogs, Databricks UC, or docs",
	},
	{
		id: "governance",
		stepNumber: 3,
		title: "Governance & Dialect",
		shortTitle: "Governance",
		description:
			"Choose agent model, SQL dialect, sampling rows, and hold policy",
	},
	{
		id: "validate_run",
		stepNumber: 4,
		title: "Validate, Plan & Run",
		shortTitle: "Validate & Run",
		description:
			"Preview per-table action plan, stream SSE execution, and inspect diff",
	},
] as const;

/**
 * Common props passed to each step component.
 */
export interface WizardStepProps {
	formState: WizardFormState;
	onChange: (updates: Partial<WizardFormState>) => void;
	onNext?: (() => void) | undefined;
	onBack?: (() => void) | undefined;
	isFirstStep?: boolean | undefined;
	isLastStep?: boolean | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
}

/**
 * Props for Step 1: Foundation Selector.
 */
export interface Step1FoundationProps extends WizardStepProps {
	availableSessions?:
		| Array<{
				session_id: string;
				domain_namespace?: string | undefined;
				concepts_count?: number | undefined;
		  }>
		| undefined;
	isLoadingSessions?: boolean | undefined;
	onSelectSession?: ((sessionId: string) => void) | undefined;
}

/**
 * Props for Step 2: Connector Sources.
 */
export interface Step2ConnectorSourcesProps extends WizardStepProps {
	onOpenCatalogPicker?:
		| ((sourceType: "sqlite" | "databricks") => void)
		| undefined;
	initialAdapters?: ConnectorUIManifestDTO[] | undefined;
}

/**
 * Props for Step 3: Governance & Dialect.
 */
export interface Step3GovernanceProps extends WizardStepProps {
	availableModels?: string[] | undefined;
	initialAdvancedOpen?: boolean | undefined;
}

/**
 * Props for Step 4: Validate & Run.
 */
export interface Step4ValidateRunProps extends WizardStepProps {
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	validatePipelineFn?:
		| ((req: PipelineValidateRequest) => Promise<PipelineValidationReport>)
		| undefined;
	createRunFn?:
		| ((req: PipelineRunRequest) => Promise<PipelineRunHandle>)
		| undefined;
	onEvolveSchema?: ((newBlueprint: VaultBlueprintDTO) => void) | undefined;
	onSyncOntologyDelta?: ((sessionId: string) => Promise<void>) | undefined;
	initialAdapters?: ConnectorUIManifestDTO[] | undefined;
}

/**
 * Props for the toggleable live YAML Drawer.
 */
export interface YamlDrawerProps {
	isOpen: boolean;
	onClose: () => void;
	yamlText: string;
	onApplyYaml: (yamlText: string) => void;
	validationError?: string | null | undefined;
	readOnly?: boolean | undefined;
}

/**
 * Props for the top-level WizardShell component.
 */
export interface WizardShellProps {
	initialStep?: WizardStepId | undefined;
	initialFormState?: Partial<WizardFormState> | undefined;
	sessionId?: string | undefined;
	source?: string | undefined;
	initialDomainNamespace?: string | undefined;
	initialPipelineId?: string | undefined;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	onBackToExplorer?: (() => void) | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	validatePipelineFn?:
		| ((req: PipelineValidateRequest) => Promise<PipelineValidationReport>)
		| undefined;
	createRunFn?:
		| ((req: PipelineRunRequest) => Promise<PipelineRunHandle>)
		| undefined;
	onEvolveSchema?: ((newBlueprint: VaultBlueprintDTO) => void) | undefined;
	onSyncOntologyDelta?: ((sessionId: string) => Promise<void>) | undefined;
	initialAdapters?: ConnectorUIManifestDTO[] | undefined;
}

/**
 * Creates a clean initial WizardFormState with safe defaults.
 */
export function createDefaultWizardFormState(
	overrides?: Partial<WizardFormState>,
): WizardFormState {
	return {
		foundationMode: "studio_session",
		activeSessionId: null,
		blueprintPath: "",
		blueprintContent: "",
		blueprint: null,
		ontologyPath: "",
		ontologyContent: "",
		ontologySource: null,
		contractPath: "",
		contractContent: "",
		contract: null,
		configuredSources: [],
		unstructuredDocs: [],
		agentModel: "gemini-4-argon",
		targetDialects: ["duckdb"],
		samplingRows: 10000,
		samplingStrategy: "DETERMINISTIC_LIMIT",
		stewardHoldPolicy: "pause",
		ontologyEvolutionPolicy: "recommend",
		harnessProvider: "agy",
		domainNamespace: "default",
		pipelineId: "pipeline_main",
		...overrides,
	};
}
