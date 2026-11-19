/**
 * Composite Store State Interface & Slice Context Types
 * (apps/opendv-web-workbench/src/ontology/store/store-types.ts)
 */

import type {
	BudgetHoldInfo,
	CandidateProposalSummary,
	ClarificationAnswer,
	ClarificationQuestion,
	ConceptNode,
	ConnectorSourceInput,
	CreateSessionRequest,
	DiffState,
	EvidenceChunk,
	EvidenceTableProfile,
	FiboQaResult,
	ImportOntologyRequest,
	LayoutState,
	ParsingState,
	ProposalDraftEdit,
	QualityScorecard,
	RelationshipEdge,
	ResolvedCitationItem,
	SourceDocument,
	StagedCandidateDTO,
	StarterPackMetadata,
	StudioErrorToast,
	StudioEventLogEntry,
	StudioMode,
	StudioSessionSummary,
	StudioUserRole,
} from "./types";

export interface PipelineTopologyRow {
	entity_name: string;
	entity_kind: string;
	zone: string;
	source_mapping: string;
}

export interface PipelineBaselineOption {
	run_id: string;
	label: string;
	is_latest_certified: boolean;
	contract_content?: string | undefined;
}

export interface PipelineHoldSummary {
	hold_id: string;
	run_id: string;
	stage_id: string;
	headline: string;
	status: string;
}

export interface SessionLogEntryDTO {
	seq: number;
	raw_id?: number | undefined;
	timestamp: string;
	origin?: string | undefined;
	event: string;
	wizard_step?: number | null | undefined;
	stage_id?: string | null | undefined;
	persona?: string | null | undefined;
	source_id?: string | null | undefined;
	job_id?: string | null | undefined;
	category?: string | null | undefined;
	provider?: string | null | undefined;
	model_id?: string | null | undefined;
	duration_ms?: number | null | undefined;
	token_usage?:
		| {
				input_tokens?: number | undefined;
				output_tokens?: number | undefined;
				total_tokens?: number | undefined;
				cost_usd?: number | undefined;
		  }
		| null
		| undefined;
	masked_prompt?: string | null | undefined;
	structured_or_raw_response?: string | null | undefined;
	tool_calls?: Array<Record<string, unknown>> | undefined;
	toon_diagnostics?: string[] | undefined;
	status?: string | null | undefined;
	headline?: string | null | undefined;
	data?: Record<string, unknown> | undefined;
}

export interface StudioStoreState {
	// Mode & Role
	activeMode: StudioMode;
	userRole: StudioUserRole;

	// Sessions
	activeSession: StudioSessionSummary | null;
	sessions: StudioSessionSummary[];
	legacySessionCount: number;
	starterPacks: StarterPackMetadata[];
	isLoadingSessions: boolean;

	// Sources & Previews
	sources: SourceDocument[];
	isLoadingSources: boolean;
	parsingState: ParsingState;
	evidenceTables: EvidenceTableProfile[];
	evidenceChunks: EvidenceChunk[];
	observedTermCounts: Record<string, number>;

	// Clarification Q&A, Agent Discovery & Citations
	clarifications: ClarificationQuestion[];
	answers: Record<string, ClarificationAnswer>;
	answers_sha256?: string | undefined;
	isLoadingClarifications: boolean;
	isClarifying: boolean;
	isConstructing: boolean;
	activeSessionJobId: string | null;
	agentErrorCode: string | null;
	agentErrorDetail: string | null;
	budgetHold: BudgetHoldInfo | null;
	stagedCandidates: StagedCandidateDTO[];
	fiboQaResult: FiboQaResult | null;
	resolvedCitations: ResolvedCitationItem[];
	proposals: CandidateProposalSummary[];
	selectedProposalId: string | null;
	proposalDrafts: Record<string, ProposalDraftEdit>;
	isLoadingProposals: boolean;

	// Graph Canvas
	nodes: ConceptNode[];
	edges: RelationshipEdge[];
	committedNodes: ConceptNode[];
	committedEdges: RelationshipEdge[];
	selectedNodeId: string | null;
	selectedEdgeId: string | null;
	isLoadingGraph: boolean;
	hasUnsavedChanges: boolean;
	viewedVersion: string | null;
	isHistoricalView: boolean;
	isCommitting: boolean;
	isPromoting: boolean;
	isSubmittingWaiver: boolean;

	// Visual Diff
	diffState: DiffState;

	// Quality & Governance
	qualityScorecard: QualityScorecard;
	sessionWaivers: Record<
		string,
		{
			waiver_applied: boolean;
			waiver_rationale: string;
			waived_concept_uris: string[];
			waived_base_sha256?: string | undefined;
			waived_concept_snapshots?: Record<string, ConceptNode> | undefined;
		}
	>;

	// Step 6 DV Pipeline Execution
	selectedDialects: Array<"duckdb" | "databricks">;
	baselineOptions: PipelineBaselineOption[];
	selectedBaselineRunId: string;
	customBaselineContractContent: string | null;
	topologyRows: PipelineTopologyRow[];
	stageStatuses: Record<
		string,
		"PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "HOLD"
	>;
	zoneSqlByZone: Record<string, string>;
	activeRunId: string | null;
	isRunningPipeline: boolean;
	activeHold: PipelineHoldSummary | null;
	driftColumns: string[];

	// Session Logs
	sessionLogEntries: SessionLogEntryDTO[];
	sessionLogsTotal: number;
	sessionCostTokens: number;
	sessionCostUsd: number;
	isLogsDrawerOpen: boolean;
	logsStageFilter: string | null;
	logsStepFilter: number | null;
	followLiveTail: boolean;

	// Layout & Diagnostics
	layout: LayoutState;
	sseEvents: StudioEventLogEntry[];
	toasts: StudioErrorToast[];

	// Session actions
	setMode: (mode: StudioMode) => void;
	setUserRole: (role: StudioUserRole) => void;
	loadSessions: (
		fetchImpl?: typeof fetch,
		opts?: { wizardOnly?: boolean },
	) => Promise<void>;
	selectSession: (
		sessionId: string,
		fetchImpl?: typeof fetch,
		force?: boolean,
	) => Promise<void>;
	createSession: (
		req: CreateSessionRequest,
		fetchImpl?: typeof fetch,
		force?: boolean,
	) => Promise<StudioSessionSummary | null>;
	importOntologySession: (
		req: ImportOntologyRequest,
		fetchImpl?: typeof fetch,
	) => Promise<StudioSessionSummary | null>;
	loadStarterPacks: (fetchImpl?: typeof fetch) => Promise<void>;
	applyStarterPacks: (
		packIds: string[],
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;

	// Sources & Preview actions
	uploadSource: (
		file:
			| File
			| {
					name: string;
					size?: number;
					type: string;
					content?: File | Blob | ArrayBuffer | Uint8Array | string | undefined;
			  },
		confidential?: boolean,
		fetchImpl?: typeof fetch,
		ocrMode?: "auto" | "force" | "off",
	) => Promise<SourceDocument | null>;
	attachConnectorSource: (
		req: ConnectorSourceInput,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	addInlineNote: (
		text: string,
		sourceName?: string,
		doNotSendToAgent?: boolean,
		fetchImpl?: typeof fetch,
	) => Promise<SourceDocument | null>;
	deleteSource: (
		sourceId: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	startParse: (
		ocrMode?: "auto" | "force" | "off",
		fetchImpl?: typeof fetch,
	) => Promise<string | null>;
	updateParsingProgress: (update: Partial<ParsingState>) => void;
	loadSources: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	loadEvidenceTables: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	loadEvidenceChunks: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;

	// Clarification, Agent Discovery & Citations actions
	loadClarifications: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	submitAnswer: (
		answer: ClarificationAnswer,
		expectedAnswersSha256?: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	setAnswerDraft: (
		questionId: string,
		selectedOptionIds: string[],
		customAnswerText?: string | undefined,
	) => void;
	startClarify: (
		options?: {
			mode?: "auto" | "agent" | "deterministic";
			max_questions?: number;
		},
		fetchImpl?: typeof fetch,
	) => Promise<string | null>;
	startConstruct: (
		options?: {
			mode?: "auto" | "agent" | "deterministic";
			auto_commit?: boolean;
			rationale?: string;
			steering_prompt?: string;
			force?: boolean;
		},
		fetchImpl?: typeof fetch,
	) => Promise<string | null>;
	cancelActiveJob: (fetchImpl?: typeof fetch) => Promise<boolean>;
	loadStagedCandidates: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	promoteStagedCandidate: (
		candidateId: string,
		path: "path_a" | "path_b",
		rationale?: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	resolveCitations: (
		citations: Array<Record<string, unknown>>,
		fetchImpl?: typeof fetch,
	) => Promise<ResolvedCitationItem[]>;
	lookupFibo: (
		query: string,
		fetchImpl?: typeof fetch,
	) => Promise<Array<Record<string, unknown>>>;
	askFiboQa: (
		question: string,
		options?: { sourceIds?: string[]; conceptUri?: string },
		fetchImpl?: typeof fetch,
	) => Promise<FiboQaResult | null>;
	clearAgentError: () => void;
	clearBudgetHold: () => void;

	// Proposal stewardship actions
	loadProposals: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
		loadOptions?: { selectNewestPending?: boolean },
	) => Promise<void>;
	acceptProposal: (
		proposalId: string,
		rationale?: string,
		fetchImpl?: typeof fetch,
		options?: {
			force?: boolean;
			selfAcceptanceOverride?: boolean;
			lowConfidenceWaiver?: boolean;
		},
	) => Promise<boolean>;
	rejectProposal: (
		proposalId: string,
		reason?: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	rebaseProposal: (
		proposalId: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	saveEditedProposal: (
		proposalId: string,
		updates: {
			rationale?: string | undefined;
			operations: Array<Record<string, unknown>>;
		},
		fetchImpl?: typeof fetch,
	) => Promise<CandidateProposalSummary | null>;
	selectProposal: (proposalId: string | null) => void;
	setProposalDraft: (proposalKey: string, draft: ProposalDraftEdit) => void;
	clearProposalDraft: (proposalKey: string) => void;

	// Graph actions
	loadGraph: (
		version?: string,
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	selectNode: (nodeId: string | null) => void;
	selectEdge: (edgeId: string | null) => void;
	updateConcept: (nodeId: string, updates: Partial<ConceptNode>) => void;
	updateRelationship: (
		edgeId: string,
		updates: Partial<RelationshipEdge>,
	) => void;
	addConcept: (node: ConceptNode) => void;
	addRelationship: (edge: RelationshipEdge) => void;
	deleteConcept: (conceptUriOrId: string) => void;
	deleteRelationship: (relationshipUriOrId: string) => void;
	discardChanges: () => void;

	// Visual Diff actions
	computeDiff: (
		baseVersion: string,
		targetVersion: string,
		baseGraph?: { nodes: ConceptNode[]; edges: RelationshipEdge[] },
		targetGraph?: { nodes: ConceptNode[]; edges: RelationshipEdge[] },
	) => void;
	loadAndComputeDiff: (
		baseVersion: string,
		targetVersion: string,
		fetchImpl?: typeof fetch,
	) => Promise<void>;
	setDiffMode: (mode: "unified" | "side-by-side" | "off") => void;
	commitChanges: (
		rationale?: string,
		fetchImpl?: typeof fetch,
		options?: { allowDestructive?: boolean },
	) => Promise<boolean>;

	// Quality, Waiver & Feedback
	updateQualityScorecard: (updates: Partial<QualityScorecard>) => void;
	submitWaiver: (
		rationale: string,
		targetOrLegacy?: string | string[] | typeof fetch,
		maybeTargetsOrFetch?: string[] | typeof fetch,
		maybeFetchImpl?: typeof fetch,
	) => Promise<boolean>;
	submitFeedback: (
		rating: number,
		comment: string,
		conceptUriOrFetch?: string | null | typeof fetch,
		maybeFetchImpl?: typeof fetch,
	) => Promise<boolean>;

	// Export & Promotion
	exportOntology: (
		format: string,
		version?: string,
		fetchImpl?: typeof fetch,
	) => Promise<string>;
	promoteWorkspace: (
		req: {
			workspace?: string;
			workspace_sha256: string;
			head_sha256: string;
			rationale: string;
			acknowledge_historical_contract_warning?: boolean;
			historical_contract_rationale?: string;
		},
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	exportBlueprint: (
		versionTag?: string,
		fetchImpl?: typeof fetch,
	) => Promise<Record<string, unknown> | null>;

	// Step 6 DV Pipeline actions
	setSelectedDialects: (dialects: Array<"duckdb" | "databricks">) => void;
	setSelectedBaselineRunId: (
		runId: string,
		customContractContent?: string | null,
	) => void;
	loadPipelineBaselines: (fetchImpl?: typeof fetch) => Promise<void>;
	startDvPipelineRun: (
		opts?: { sourceUris?: string[] },
		fetchImpl?: typeof fetch,
	) => Promise<string | null>;
	resolveActiveHold: (
		params: {
			holdId: string;
			action: "APPROVE" | "REJECT" | "ANSWER";
			selectedCandidateId?: string;
			resolutionNotes?: string;
		},
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	clearActiveHold: () => void;
	abortPipelineRun: (fetchImpl?: typeof fetch) => Promise<boolean>;
	syncDriftToStep4: (fetchImpl?: typeof fetch) => Promise<boolean>;

	// Session Logs & Budget actions
	loadSessionLogs: (
		opts?: {
			stageId?: string | null;
			wizardStep?: number | null;
			persona?: string | null;
		},
		fetchImpl?: typeof fetch,
	) => Promise<void>;
	openLogsDrawer: (opts?: {
		stageFilter?: string | null;
		stepFilter?: number | null;
	}) => void;
	closeLogsDrawer: () => void;
	setFollowLiveTail: (follow: boolean) => void;
	raiseBudgetAndResume: (
		extraCostUsd?: number,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	abortBudgetHold: (fetchImpl?: typeof fetch) => Promise<boolean>;

	// Layout & Toast actions
	setLeftDrawerTab: (tab: LayoutState["leftDrawerTab"]) => void;
	setRightDrawerTab: (tab: NonNullable<LayoutState["rightDrawerTab"]>) => void;
	toggleLeftDrawer: (open?: boolean) => void;
	toggleRightDrawer: (open?: boolean) => void;
	toggleBottomDock: (open?: boolean) => void;
	setActiveModal: (modal: LayoutState["activeModal"]) => void;
	setLodTier: (tier: 1 | 2 | 3) => void;
	setMinimapVisible: (visible: boolean) => void;
	setSearchTerm: (term: string) => void;
	triggerRelayout: () => void;
	addToast: (toast: Omit<StudioErrorToast, "id" | "timestamp">) => void;
	dismissToast: (id: string) => void;
	reset: () => void;
}

export type StoreSet = (
	partial:
		| Partial<StudioStoreState>
		| ((state: StudioStoreState) => Partial<StudioStoreState>),
) => void;
export type StoreGet = () => StudioStoreState;
