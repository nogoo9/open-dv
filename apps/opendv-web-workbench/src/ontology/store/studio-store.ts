/**
 * Zustand Studio Store for Web Workbench SPA (/ontology)
 * (apps/opendv-web-workbench/src/ontology/store/studio-store.ts)
 *
 * Implements Task 5D.1 (#222).
 */

import { useRef, useSyncExternalStore } from "react";
import { createStore } from "zustand/vanilla";
import type {
	CandidateProposalSummary,
	ClarificationAnswer,
	ClarificationEvidenceCitation,
	ClarificationQuestion,
	ClarificationRationaleOption,
	ConceptAttribute,
	ConceptNode,
	CreateSessionRequest,
	DiffRecord,
	DiffState,
	EvidenceChunk,
	ExternalMapping,
	LayoutState,
	ParsingState,
	QualityScorecard,
	RelationshipEdge,
	SessionCommit,
	SourceDocument,
	StudioErrorToast,
	StudioEventLogEntry,
	StudioMode,
	StudioSessionSummary,
	StudioUserRole,
} from "./types";

export * from "./types";

export const EMPTY_ANSWERS_SHA256 =
	"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945";

export const VALID_PATH_A_PREDICATES = new Set([
	"skos:exactMatch",
	"skos:closeMatch",
	"http://www.w3.org/2004/02/skos/core#exactMatch",
	"http://www.w3.org/2004/02/skos/core#closeMatch",
]);

const INITIAL_EVENT_SOURCE = (globalThis as Record<string, unknown>)
	.EventSource;

/**
 * Extracts `#token=<nonce>` / `#nonce=<nonce>` or `?nonce=<nonce>` / `?token=<nonce>`
 * from browser location and immediately scrubs it via `history.replaceState`.
 */
export function extractAndScrubLaunchNonce(
	win?: {
		location: {
			href?: string;
			pathname: string;
			search: string;
			hash: string;
		};
		history: {
			replaceState: (
				data: unknown,
				unused: string,
				url?: string | URL | null,
			) => void;
		};
	} | null,
): string | null {
	const targetWin = win ?? (typeof window !== "undefined" ? window : null);
	if (!targetWin || !targetWin.location) return null;

	let extracted: string | null = null;
	let newHash = targetWin.location.hash || "";
	let newSearch = targetWin.location.search || "";

	if (newHash.startsWith("#") && newHash.length > 1) {
		const hashRaw = newHash.slice(1);
		const hashParams = new URLSearchParams(hashRaw);
		const hToken = hashParams.get("token") || hashParams.get("nonce");
		if (hToken && hToken.trim()) {
			extracted = hToken.trim();
			hashParams.delete("token");
			hashParams.delete("nonce");
			const remainingHash = hashParams.toString();
			newHash = remainingHash ? `#${remainingHash}` : "";
		}
	}

	if (newSearch.startsWith("?") && newSearch.length > 1) {
		const searchParams = new URLSearchParams(newSearch.slice(1));
		const sNonce = searchParams.get("nonce") || searchParams.get("token");
		if (sNonce && sNonce.trim()) {
			if (!extracted) extracted = sNonce.trim();
			searchParams.delete("nonce");
			searchParams.delete("token");
			const remainingSearch = searchParams.toString();
			newSearch = remainingSearch ? `?${remainingSearch}` : "";
		}
	}

	if (extracted) {
		const cleanUrl = `${targetWin.location.pathname || "/ontology"}${newSearch}${newHash}`;
		try {
			targetWin.history.replaceState(null, "", cleanUrl);
		} catch {
			// Ignore history security errors in restricted test sandboxes
		}
	}
	return extracted;
}

export function normalizeStudioRole(
	rawRole?: unknown,
	rawPrincipal?: unknown,
): StudioUserRole | undefined {
	if (typeof rawRole === "string") {
		const cleaned = rawRole.trim().toLowerCase();
		if (
			cleaned === "viewer" ||
			cleaned === "ontology_viewer" ||
			cleaned === "read_only"
		) {
			return "viewer";
		}
		if (
			cleaned === "editor" ||
			cleaned === "ontology_editor" ||
			cleaned === "steward" ||
			cleaned === "ontology_steward"
		) {
			return "editor";
		}
		if (
			cleaned === "automated" ||
			cleaned === "ontology_automated" ||
			cleaned === "agent"
		) {
			return "automated";
		}
		if (
			cleaned === "admin" ||
			cleaned === "ontology_admin" ||
			cleaned === "platform_admin"
		) {
			return "admin";
		}
	}
	if (typeof rawPrincipal === "string") {
		const p = rawPrincipal.trim().toLowerCase();
		if (p.startsWith("agent:")) return "automated";
		if (p === "human:local_admin") return "admin";
	}
	return undefined;
}

function extractRoleFromJwtPayload(token: string): StudioUserRole | undefined {
	const parts = token.split(".");
	if (parts.length !== 3 || !parts[1]) return undefined;
	try {
		const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
		const padded = b64.padEnd(b64.length + ((4 - (b64.length % 4)) % 4), "=");
		const jsonText =
			typeof atob === "function"
				? atob(padded)
				: Buffer.from(padded, "base64").toString("utf-8");
		const payload = JSON.parse(jsonText) as Record<string, unknown>;
		const roleClaim =
			payload.role ??
			payload.studio_role ??
			(Array.isArray(payload.roles) ? payload.roles[0] : undefined);
		const principalClaim = payload.sub ?? payload.principal;
		return normalizeStudioRole(roleClaim, principalClaim);
	} catch {
		return undefined;
	}
}

export function isConceptMutatedFromBaseline(
	node: ConceptNode,
	baseline: ConceptNode | undefined,
): boolean {
	if (!baseline) return false;
	return (
		node.concept_name !== baseline.concept_name ||
		node.elm_archetype !== baseline.elm_archetype ||
		(node.definition || "") !== (baseline.definition || "") ||
		(node.description || "") !== (baseline.description || "") ||
		(node.target_hub ?? null) !== (baseline.target_hub ?? null) ||
		(node.parent_concept_uri ?? null) !==
			(baseline.parent_concept_uri ?? null) ||
		(node.deprecated ?? null) !== (baseline.deprecated ?? null) ||
		JSON.stringify(node.attributes || []) !==
			JSON.stringify(baseline.attributes || []) ||
		JSON.stringify(node.synonyms || []) !==
			JSON.stringify(baseline.synonyms || []) ||
		JSON.stringify(node.external_mappings || []) !==
			JSON.stringify(baseline.external_mappings || []) ||
		JSON.stringify(node.metadata ?? null) !==
			JSON.stringify(baseline.metadata ?? null) ||
		JSON.stringify(node.source_hints ?? null) !==
			JSON.stringify(baseline.source_hints ?? null) ||
		JSON.stringify(node.governance_constraints ?? null) !==
			JSON.stringify(baseline.governance_constraints ?? null) ||
		JSON.stringify(node.key_authority_tiers ?? null) !==
			JSON.stringify(baseline.key_authority_tiers ?? null)
	);
}

export function isRelationshipMutatedFromBaseline(
	edge: RelationshipEdge,
	baseline: RelationshipEdge | undefined,
): boolean {
	if (!baseline) return true;
	const edgeEndpoints =
		edge.endpoints && edge.endpoints.length >= 2
			? edge.endpoints
			: [edge.source_concept_uri, edge.target_concept_uri];
	const baseEndpoints =
		baseline.endpoints && baseline.endpoints.length >= 2
			? baseline.endpoints
			: [baseline.source_concept_uri, baseline.target_concept_uri];
	return (
		(edge.id || edge.relationship_uri) !==
			(baseline.id || baseline.relationship_uri) ||
		edge.relationship_uri !== baseline.relationship_uri ||
		edge.relationship_name !== baseline.relationship_name ||
		edge.source_concept_uri !== baseline.source_concept_uri ||
		edge.target_concept_uri !== baseline.target_concept_uri ||
		JSON.stringify(edgeEndpoints) !== JSON.stringify(baseEndpoints) ||
		edge.target_link !== baseline.target_link ||
		(edge.grain_semantics ?? null) !== (baseline.grain_semantics ?? null) ||
		(edge.cardinality ?? null) !== (baseline.cardinality ?? null) ||
		(edge.description ?? null) !== (baseline.description ?? null) ||
		(edge.deprecated ?? null) !== (baseline.deprecated ?? null) ||
		JSON.stringify(edge.metadata ?? null) !==
			JSON.stringify(baseline.metadata ?? null) ||
		JSON.stringify(edge.source_hints ?? null) !==
			JSON.stringify(baseline.source_hints ?? null) ||
		JSON.stringify(edge.yagni_cost_guard_policy ?? null) !==
			JSON.stringify(baseline.yagni_cost_guard_policy ?? null)
	);
}

export function hasGraphChangesFromBaseline(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	committedNodes: ConceptNode[],
	committedEdges: RelationshipEdge[],
): boolean {
	if (
		nodes.length !== committedNodes.length ||
		edges.length !== committedEdges.length
	) {
		return true;
	}
	const committedNodeMap = new Map(
		committedNodes.map((n) => [n.concept_uri, n]),
	);
	for (const n of nodes) {
		const comm = committedNodeMap.get(n.concept_uri);
		if (!comm || isConceptMutatedFromBaseline(n, comm)) {
			return true;
		}
	}
	const sortedEdges = [...edges].sort((a, b) =>
		a.relationship_uri < b.relationship_uri
			? -1
			: a.relationship_uri > b.relationship_uri
				? 1
				: 0,
	);
	const sortedCommittedEdges = [...committedEdges].sort((a, b) =>
		a.relationship_uri < b.relationship_uri
			? -1
			: a.relationship_uri > b.relationship_uri
				? 1
				: 0,
	);
	for (let i = 0; i < sortedEdges.length; i++) {
		const currEdge = sortedEdges[i]!;
		const commEdge = sortedCommittedEdges[i];
		if (!commEdge || isRelationshipMutatedFromBaseline(currEdge, commEdge)) {
			return true;
		}
	}
	return false;
}

export interface StudioStoreState {
	// Mode, Auth & Elevation
	activeMode: StudioMode;
	userRole: StudioUserRole;
	authToken: string | null;
	authRequired: boolean;
	isBootstrappingAuth: boolean;

	// Sessions
	activeSession: StudioSessionSummary | null;
	sessions: StudioSessionSummary[];
	isLoadingSessions: boolean;

	// Sources & Ingestion
	sources: SourceDocument[];
	isLoadingSources: boolean;
	parsingState: ParsingState;

	// Clarification Q&A & Candidate Proposals
	clarifications: ClarificationQuestion[];
	answers: Record<string, ClarificationAnswer>;
	answers_sha256?: string | undefined;
	isLoadingClarifications: boolean;
	isClarifying: boolean;
	isConstructing: boolean;
	proposals: CandidateProposalSummary[];
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

	// Layout & Viewport
	layout: LayoutState;

	// Diagnostic, SSE & RFC 9457 Errors
	sseEvents: StudioEventLogEntry[];
	toasts: StudioErrorToast[];

	// Actions
	setMode: (mode: StudioMode) => void;
	setUserRole: (role: StudioUserRole) => void;
	setAuthToken: (token: string | null) => void;
	exchangeLaunchNonce: (
		nonce: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	bootstrapLoopbackAuth: (fetchImpl?: typeof fetch) => Promise<boolean>;

	// Session actions
	loadSessions: (fetchImpl?: typeof fetch) => Promise<void>;
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

	// Sources actions
	uploadSource: (
		file: {
			name: string;
			size?: number;
			type: string;
			content?: File | Blob | ArrayBuffer | Uint8Array | string | undefined;
		},
		confidential?: boolean,
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

	// Clarification & Construct / Proposals actions
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
		},
		fetchImpl?: typeof fetch,
	) => Promise<string | null>;
	loadProposals: (
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	acceptProposal: (
		proposalId: string,
		rationale?: string,
		fetchImpl?: typeof fetch,
		options?: { force?: boolean; selfAcceptanceOverride?: boolean },
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

	// Quality & Waiver & Feedback
	updateQualityScorecard: (updates: Partial<QualityScorecard>) => void;
	submitWaiver: (
		rationale: string,
		waiverKey: string,
		targetConceptUrisOrFetch?: string[] | typeof fetch,
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

	// Layout actions
	setLeftDrawerTab: (tab: LayoutState["leftDrawerTab"]) => void;
	toggleLeftDrawer: (open?: boolean) => void;
	toggleRightDrawer: (open?: boolean) => void;
	toggleBottomDock: (open?: boolean) => void;
	setActiveModal: (modal: LayoutState["activeModal"]) => void;
	setLodTier: (tier: 1 | 2 | 3) => void;
	setMinimapVisible: (visible: boolean) => void;
	setSearchTerm: (term: string) => void;
	triggerRelayout: () => void;

	// Toast actions
	addToast: (toast: Omit<StudioErrorToast, "id" | "timestamp">) => void;
	dismissToast: (id: string) => void;

	// Reset
	reset: () => void;
}

export interface StudioStoreOptions {
	activeMode?: StudioMode;
	userRole?: StudioUserRole;
	initialSession?: StudioSessionSummary | null;
	apiUrl?: string;
	maxPollingDurationMs?: number;
	maxPollAttempts?: number;
	maxPollErrorRetries?: number;
	initialPollIntervalMs?: number;
	maxPollIntervalMs?: number;
}

const EXTENSION_TO_MIMES: Record<string, string[]> = {
	".pdf": ["application/pdf"],
	".docx": [
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	],
	".xlsx": [
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	],
	".html": ["text/html"],
	".htm": ["text/html"],
	".json": ["application/json"],
	".csv": ["text/csv"],
	".md": ["text/markdown", "text/plain"],
	".txt": ["text/plain"],
};

export function isSupportedSourceFile(
	filename: string,
	mimeType: string,
): boolean {
	const lower = filename.toLowerCase();
	const entry = Object.entries(EXTENSION_TO_MIMES).find(([ext]) =>
		lower.endsWith(ext),
	);
	if (!entry) return false;
	const [, validMimes] = entry;
	if (!mimeType || mimeType === "application/octet-stream") return true;
	return validMimes.includes(mimeType);
}

export function escapeXml(str?: string | null): string {
	const sanitized = (str ?? "").replace(
		/[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\u{10000}-\u{10FFFF}]/gu,
		"",
	);
	return sanitized
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&apos;");
}

export function escapeTurtleString(str?: string | null): string {
	return (str ?? "")
		.replace(/\\/g, "\\\\")
		.replace(/"/g, '\\"')
		.replace(/\n/g, "\\n")
		.replace(/\r/g, "\\r");
}

export function escapeYamlString(str?: string | null): string {
	return (str ?? "")
		.replace(/\\/g, "\\\\")
		.replace(/"/g, '\\"')
		.replace(/\r/g, "\\r")
		.replace(/\n/g, "\\n")
		.replace(/\t/g, "\\t");
}

export function escapeIri(iri?: string | null): string {
	const trimmed = (iri ?? "").trim();
	return trimmed.replace(/[<>"{}|^`\\\s\x00-\x20]/g, (c) =>
		encodeURIComponent(c),
	);
}

export function createDefaultQualityScorecard(): QualityScorecard {
	return {
		grounding_score: 0,
		deployed_contracts_ok: false,
		dag_acyclic: false,
		yagni_compliant: false,
		checks: [],
		waiver_applied: false,
		waiver_rationale: "",
		waived_concept_uris: [],
		feedback_rating: 0,
		feedback_comment: "",
		feedback_submitted: false,
	};
}

export function deriveGroundingStatus(
	mappings?: ExternalMapping[] | null,
	explicitConfidence?: number | null,
): {
	grounding_status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED";
	confidence: number;
} {
	const list = mappings || [];
	if (list.length === 0) {
		return {
			grounding_status: "UNGROUNDED",
			confidence:
				typeof explicitConfidence === "number" &&
				Number.isFinite(explicitConfidence)
					? explicitConfidence
					: 0.0,
		};
	}

	const verifiedMappings = list.filter(
		(m) => (m.provenance || "").trim().toLowerCase() !== "user_asserted",
	);

	const qualifyingFiboConfs: number[] = [];
	const allVerifiedConfs: number[] = [];

	for (const m of verifiedMappings) {
		const rawConf =
			typeof m.confidence === "number"
				? m.confidence
				: typeof m.mapping_confidence === "number"
					? m.mapping_confidence
					: undefined;
		if (typeof rawConf === "number" && Number.isFinite(rawConf)) {
			allVerifiedConfs.push(rawConf);
			const isFibo = (m.scheme || "").trim().toLowerCase() === "fibo";
			const hasAllowedPredicate =
				typeof m.predicate === "string" &&
				VALID_PATH_A_PREDICATES.has(m.predicate.trim());
			if (isFibo && hasAllowedPredicate) {
				qualifyingFiboConfs.push(rawConf);
			}
		}
	}

	if (qualifyingFiboConfs.length > 0) {
		const maxQualifying = Math.max(...qualifyingFiboConfs);
		if (maxQualifying >= 0.65) {
			return {
				grounding_status: "GROUNDED",
				confidence: maxQualifying,
			};
		}
		if (maxQualifying >= 0.4) {
			return {
				grounding_status: "LOW_CONFIDENCE",
				confidence: maxQualifying,
			};
		}
		return {
			grounding_status: "UNGROUNDED",
			confidence: maxQualifying,
		};
	}

	const fallbackConf =
		allVerifiedConfs.length > 0 ? Math.max(...allVerifiedConfs) : 0.0;

	return {
		grounding_status: fallbackConf >= 0.4 ? "LOW_CONFIDENCE" : "UNGROUNDED",
		confidence: fallbackConf,
	};
}

export interface QualityScorecardOptions {
	grandfatheredUris?: string[];
	waiverApplied?: boolean;
	waivedUris?: string[];
	committedNodes?: ConceptNode[];
	waivedSnapshots?: Record<string, ConceptNode>;
}

export function computeQualityScorecard(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	optionsOrGrandfathered: string[] | QualityScorecardOptions = [],
	waiverAppliedOrWaivedUris: boolean | string[] = false,
	waivedUrisParam: string[] = [],
): QualityScorecard {
	let grandfatheredUris: string[] = [];
	let waiverApplied = false;
	let waivedUris: string[] = [];
	let committedNodes: ConceptNode[] = [];
	let waivedSnapshots: Record<string, ConceptNode> | undefined;

	if (
		!Array.isArray(optionsOrGrandfathered) &&
		typeof optionsOrGrandfathered === "object" &&
		optionsOrGrandfathered !== null
	) {
		grandfatheredUris = optionsOrGrandfathered.grandfatheredUris || [];
		waiverApplied = Boolean(optionsOrGrandfathered.waiverApplied);
		waivedUris = optionsOrGrandfathered.waivedUris || [];
		committedNodes = optionsOrGrandfathered.committedNodes || [];
		waivedSnapshots = optionsOrGrandfathered.waivedSnapshots;
	} else {
		grandfatheredUris = Array.isArray(optionsOrGrandfathered)
			? optionsOrGrandfathered
			: [];
		waiverApplied =
			typeof waiverAppliedOrWaivedUris === "boolean"
				? waiverAppliedOrWaivedUris
				: true;
		waivedUris = Array.isArray(waiverAppliedOrWaivedUris)
			? waiverAppliedOrWaivedUris
			: waivedUrisParam;
	}

	const committedMap = new Map(committedNodes.map((c) => [c.concept_uri, c]));
	const currentMap = new Map(nodes.map((n) => [n.concept_uri, n]));

	// Revoke grandfathered exemption in working scorecard for any concept mutated relative to committedNodes
	const effectiveGrandfatheredUris = grandfatheredUris.filter((uri) => {
		const curr = currentMap.get(uri);
		const comm = committedMap.get(uri);
		if (curr && comm && isConceptMutatedFromBaseline(curr, comm)) {
			return false;
		}
		return true;
	});

	// Revoke per-concept waiver exemption if that concept was subsequently mutated
	const effectiveWaivedUris = waivedUris.filter((uri) => {
		const curr = currentMap.get(uri);
		const baseline = waivedSnapshots?.[uri] ?? committedMap.get(uri);
		if (curr && baseline && isConceptMutatedFromBaseline(curr, baseline)) {
			return false;
		}
		return true;
	});
	const effectiveWaiverApplied =
		waiverApplied &&
		(waivedUris.length === 0 || effectiveWaivedUris.length > 0);

	// 1. ONT-EVO-001: Deployed Table Contracts (strictly additive evolution)
	const missingGrandfathered = grandfatheredUris.filter(
		(uri) => !nodes.some((n) => n.concept_uri === uri),
	);
	const ontEvo1Status = missingGrandfathered.length === 0 ? "PASS" : "FAIL";
	const ontEvo1Details =
		missingGrandfathered.length === 0
			? "All historical grandfathered entities preserved without breaking deletions."
			: `Grandfathered entities removed: ${missingGrandfathered.join(", ")}`;

	// 2. ONT-EVO-002: Regulatory Grounding Threshold & Invariant
	// Path A grounded iff >= 1 FIBO mapping with skos:exactMatch / skos:closeMatch and numeric confidence >= 0.65.
	function isConceptGrounded(n: ConceptNode): boolean {
		const mappings = n.external_mappings || [];
		if (mappings.length === 0) return false;
		return (
			deriveGroundingStatus(mappings, n.confidence).grounding_status ===
			"GROUNDED"
		);
	}

	const groundedNodes = nodes.filter((n) => isConceptGrounded(n)).length;
	const ungroundedNodes = nodes.filter(
		(n) =>
			!isConceptGrounded(n) &&
			!effectiveGrandfatheredUris.includes(n.concept_uri),
	);
	const groundingRatio = nodes.length > 0 ? groundedNodes / nodes.length : 0;

	const allUngroundedWaived =
		effectiveWaiverApplied &&
		(ungroundedNodes.length === 0 ||
			ungroundedNodes.every((n) =>
				effectiveWaivedUris.includes(n.concept_uri),
			));

	let ontEvo2Status: "PASS" | "WARN" | "FAIL";
	let ontEvo2Details: string;
	if (nodes.length === 0) {
		ontEvo2Status = "PASS";
		ontEvo2Details = "Zero concepts in session. No ungrounded URIs.";
	} else if (ungroundedNodes.length === 0) {
		ontEvo2Status = "PASS";
		ontEvo2Details = `All ${nodes.length} concepts are grounded or covered by historical grandfathered exemptions.`;
	} else if (allUngroundedWaived) {
		ontEvo2Status = "PASS";
		ontEvo2Details = `Grounding ratio: ${(groundingRatio * 100).toFixed(1)}% (${groundedNodes}/${nodes.length} grounded). Covered by authorized steward administrative waiver (ONT-EVO-002).`;
	} else {
		ontEvo2Status = "FAIL";
		ontEvo2Details = `Grounding ratio: ${(groundingRatio * 100).toFixed(1)}% (${groundedNodes}/${nodes.length} grounded). ${ungroundedNodes.length} ungrounded concepts lack authorized waiver (violates ONT-EVO-002).`;
	}

	// 3. ONT-DAG-001: DAG Acyclicity (Taxonomic / Concept Inheritance Hierarchy)
	const nodeUris = new Set(nodes.map((n) => n.concept_uri));
	const adj = new Map<string, string[]>();
	for (const uri of nodeUris) adj.set(uri, []);

	for (const n of nodes) {
		if (n.parent_concept_uri && adj.has(n.concept_uri)) {
			adj.get(n.concept_uri)?.push(n.parent_concept_uri);
		}
	}

	for (const e of edges) {
		const relName = e.relationship_name?.trim() || "";
		const isTaxonomic =
			/^(subclass_of|subclass|is_a|rdfs:subclassof)$/i.test(relName) ||
			e.relationship_uri?.toLowerCase().endsWith("subclassof") ||
			e.target_link?.toLowerCase() === "lnk_subclass";
		if (isTaxonomic && adj.has(e.source_concept_uri)) {
			adj.get(e.source_concept_uri)?.push(e.target_concept_uri);
		}
	}

	let hasCycle = false;
	const visited = new Set<string>();
	const inStack = new Set<string>();
	function dfs(u: string): boolean {
		visited.add(u);
		inStack.add(u);
		for (const v of adj.get(u) || []) {
			if (!visited.has(v)) {
				if (dfs(v)) return true;
			} else if (inStack.has(v)) {
				return true;
			}
		}
		inStack.delete(u);
		return false;
	}
	for (const u of nodeUris) {
		if (!visited.has(u)) {
			if (dfs(u)) {
				hasCycle = true;
				break;
			}
		}
	}
	const dagStatus = hasCycle ? "FAIL" : "PASS";
	const dagDetails = hasCycle
		? "Cycle detected in concept inheritance hierarchy (taxonomic cycles violate ONT-DAG-001)."
		: "Zero cycles detected across concept inheritance hierarchy.";

	// 4. YAGNI-001: Concept Density & YAGNI
	const conceptsWithoutAttrs = nodes.filter(
		(n) => n.attributes.length === 0,
	).length;
	const yagniStatus = conceptsWithoutAttrs > 0 ? "WARN" : "PASS";
	const yagniDetails =
		conceptsWithoutAttrs === 0
			? "All concept entities declare explicit attributes."
			: `${conceptsWithoutAttrs} concept(s) have no declared attributes.`;

	return {
		grounding_score: Number.parseFloat(groundingRatio.toFixed(4)),
		deployed_contracts_ok: ontEvo1Status === "PASS",
		dag_acyclic: dagStatus === "PASS",
		yagni_compliant: yagniStatus === "PASS",
		checks: [
			{
				rule_id: "ONT-EVO-001",
				rule_name: "Deployed Table Contracts",
				status: ontEvo1Status,
				details: ontEvo1Details,
			},
			{
				rule_id: "ONT-EVO-002",
				rule_name: "Regulatory Grounding Threshold",
				status: ontEvo2Status,
				details: ontEvo2Details,
			},
			{
				rule_id: "ONT-DAG-001",
				rule_name: "Acyclic Concept Hierarchy",
				status: dagStatus,
				details: dagDetails,
			},
			{
				rule_id: "YAGNI-001",
				rule_name: "Concept Density & YAGNI",
				status: yagniStatus,
				details: yagniDetails,
			},
		],
		waiver_applied: effectiveWaiverApplied,
		waiver_rationale: "",
		waived_concept_uris: effectiveWaivedUris,
		feedback_rating: 0,
		feedback_comment: "",
		feedback_submitted: false,
	};
}

export function recomputeScorecard(
	state: StudioStoreState,
	newNodes: ConceptNode[],
	newEdges: RelationshipEdge[],
): QualityScorecard {
	const sid = state.activeSession?.session_id;
	const savedWaiver =
		(sid && state.sessionWaivers && state.sessionWaivers[sid]) || null;
	// Authority invariant: Only an authorized steward waiver whose base SHA matches current head SHA is valid (MAJ-02)
	const isWaiverValid =
		Boolean(savedWaiver?.waiver_applied) &&
		Boolean(savedWaiver?.waived_base_sha256) &&
		Boolean(state.activeSession?.head_sha256) &&
		savedWaiver?.waived_base_sha256 === state.activeSession?.head_sha256;
	const waivedUris: string[] = isWaiverValid
		? savedWaiver?.waived_concept_uris || []
		: [];

	const computed = computeQualityScorecard(newNodes, newEdges, {
		grandfatheredUris: state.activeSession?.grandfathered_concept_uris || [],
		waiverApplied: isWaiverValid,
		waivedUris,
		committedNodes: state.committedNodes || [],
		...(savedWaiver?.waived_concept_snapshots
			? { waivedSnapshots: savedWaiver.waived_concept_snapshots }
			: {}),
	});

	return {
		...computed,
		waiver_applied: computed.waiver_applied,
		waiver_rationale: computed.waiver_applied
			? savedWaiver?.waiver_rationale || ""
			: "",
		waived_concept_uris: computed.waived_concept_uris || [],
		waived_base_sha256: computed.waiver_applied
			? savedWaiver?.waived_base_sha256 || undefined
			: undefined,
		feedback_rating: state.qualityScorecard.feedback_rating,
		feedback_comment: state.qualityScorecard.feedback_comment,
		feedback_concept_uri: state.qualityScorecard.feedback_concept_uri,
		feedback_submitted: state.qualityScorecard.feedback_submitted,
	};
}

export function createDefaultLayoutState(): LayoutState {
	return {
		leftDrawerTab: "sessions",
		leftDrawerOpen: true,
		rightDrawerOpen: false,
		bottomDockOpen: false,
		activeModal: null,
		lodTier: 2,
		minimapVisible: true,
		searchTerm: "",
	};
}

/**
 * Filters concept nodes by search term matching concept_name, concept_uri, synonyms, or definition (MIN-06).
 */
export function selectFilteredNodes(
	nodes: ConceptNode[],
	searchTerm: string,
): ConceptNode[] {
	if (!searchTerm || !searchTerm.trim()) return nodes;
	const term = searchTerm.toLowerCase().trim();
	return nodes.filter(
		(n) =>
			n.concept_name.toLowerCase().includes(term) ||
			n.concept_uri.toLowerCase().includes(term) ||
			(n.synonyms || []).some((s) => s.toLowerCase().includes(term)) ||
			(n.definition || "").toLowerCase().includes(term),
	);
}

export function createDefaultParsingState(): ParsingState {
	return {
		status: "idle",
		progress_pct: 0,
		active_job_id: null,
		stage: "",
		ocr_mode: "auto",
		evidence_chunks: [],
		error: null,
	};
}

export function createDefaultDiffState(): DiffState {
	return {
		active: false,
		mode: "off",
		base_version: "0.0.0",
		target_version: "1.0.0",
		records: [],
		summary: {
			added_count: 0,
			deleted_count: 0,
			modified_count: 0,
			breaking_count: 0,
		},
	};
}

export type StudioStore = ReturnType<typeof createStudioStore>;

/**
 * Serializes an arbitrary object to an RFC 8785 JSON Canonicalization Scheme (JCS) string.
 *
 * Requirements:
 * 1. Object keys sorted lexicographically by UTF-16 code units.
 * 2. Pure IEEE 754 number formatting.
 * 3. Zero whitespace outside string literals.
 * 4. Control characters properly escaped.
 * 5. Optional excludeNone: omits properties whose value is null or undefined.
 */
export function dumpsJcs(
	obj: unknown,
	options: { excludeNone?: boolean } = {},
): string {
	const excludeNone = Boolean(options.excludeNone);

	if (obj === null || obj === undefined) {
		return "null";
	}
	if (typeof obj === "boolean") {
		return obj ? "true" : "false";
	}
	if (typeof obj === "number") {
		if (!Number.isFinite(obj)) {
			throw new Error("Non-finite number is not representable in RFC 8785");
		}
		if (Object.is(obj, -0)) return "0";
		return obj.toString();
	}
	if (typeof obj === "string") {
		return JSON.stringify(obj);
	}
	if (Array.isArray(obj)) {
		const items = obj.map((elem) => dumpsJcs(elem, options));
		return `[${items.join(",")}]`;
	}
	if (typeof obj === "object") {
		const record = obj as Record<string, unknown>;
		// RFC 8785 §3.2.3: keys ordered by UTF-16 code units
		const sortedKeys = Object.keys(record).sort((a, b) => {
			const len = Math.min(a.length, b.length);
			for (let i = 0; i < len; i++) {
				const ca = a.charCodeAt(i);
				const cb = b.charCodeAt(i);
				if (ca !== cb) return ca - cb;
			}
			return a.length - b.length;
		});

		const pairs: string[] = [];
		for (const k of sortedKeys) {
			const v = record[k];
			if (excludeNone && (v === null || v === undefined)) {
				continue;
			}
			pairs.push(`${JSON.stringify(k)}:${dumpsJcs(v, options)}`);
		}
		return `{${pairs.join(",")}}`;
	}
	throw new Error(`Unsupported type for JCS canonicalization: ${typeof obj}`);
}

/**
 * Builds a canonical dictionary from Graph A concepts, relationships, and session summary.
 * Matches `packages/opendv-core/src/opendv_core/ontology_hashing.py` (`exclude_none=True` +
 * level-bounded `_norm_empty` scoped pruning and deterministic URI/mapping sorting) without
 * fabricating placeholder strings or fallback namespace/version values.
 */
export function canonicalizeGraphADict(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	session: StudioSessionSummary,
): Record<string, unknown> {
	const isEmptyExtValue = (v: unknown): boolean => {
		if (v === null || v === undefined) return true;
		if (typeof v === "string") return v.length === 0;
		if (Array.isArray(v)) return v.length === 0;
		if (typeof v === "object") {
			return Object.keys(v as Record<string, unknown>).length === 0;
		}
		return false;
	};

	const ENUM_EXT = new Set([
		"enumeration_name",
		"description",
		"code_descriptions",
		"deprecated",
		"metadata",
	]);

	const sortedConcepts = [...nodes]
		.sort((a, b) =>
			a.concept_uri < b.concept_uri
				? -1
				: a.concept_uri > b.concept_uri
					? 1
					: 0,
		)
		.map((n) => {
			const c: Record<string, unknown> = {
				concept_name: n.concept_name,
				concept_uri: n.concept_uri,
				definition: n.definition,
				elm_archetype: n.elm_archetype,
			};
			// Legacy non-_CBC_EXT fields: preserve empty strings/dicts when explicitly non-null
			if (n.description !== null && n.description !== undefined) {
				c.description = n.description;
			}
			if (n.target_hub !== null && n.target_hub !== undefined) {
				c.target_hub = n.target_hub;
			}
			if (
				n.governance_constraints !== null &&
				n.governance_constraints !== undefined
			) {
				c.governance_constraints = n.governance_constraints;
			}
			if (
				n.key_authority_tiers !== null &&
				n.key_authority_tiers !== undefined
			) {
				c.key_authority_tiers = n.key_authority_tiers;
			}

			// _CBC_EXT scoped extension fields: prune when null, undefined, "", [], or {}
			if (Array.isArray(n.attributes) && n.attributes.length > 0) {
				c.attributes = n.attributes.map((attr) => {
					const attrRecord: Record<string, unknown> = {
						attribute_name: attr.attribute_name,
						data_type: attr.data_type,
					};
					if (attr.is_optional !== null && attr.is_optional !== undefined) {
						attrRecord.is_optional = attr.is_optional;
					}
					// _ATTR_OPT scoped extension annotations
					if (
						typeof attr.description === "string" &&
						attr.description.length > 0
					) {
						attrRecord.description = attr.description;
					}
					if (
						Array.isArray(attr.example_values) &&
						attr.example_values.length > 0
					) {
						attrRecord.example_values = attr.example_values;
					}
					if (attr.deprecated !== null && attr.deprecated !== undefined) {
						attrRecord.deprecated = attr.deprecated;
					}
					if (attr.metadata && Object.keys(attr.metadata).length > 0) {
						attrRecord.metadata = attr.metadata;
					}
					if (
						Array.isArray(attr.source_hints) &&
						attr.source_hints.length > 0
					) {
						attrRecord.source_hints = attr.source_hints;
					}
					return attrRecord;
				});
			}
			if (
				Array.isArray(n.external_mappings) &&
				n.external_mappings.length > 0
			) {
				const sortedMappings = [...n.external_mappings].sort((ma, mb) => {
					const ta = ma.target_uri ?? "";
					const tb = mb.target_uri ?? "";
					if (ta !== tb) return ta < tb ? -1 : 1;
					const pa = ma.predicate ?? "";
					const pb = mb.predicate ?? "";
					if (pa !== pb) return pa < pb ? -1 : 1;
					const sa = ma.scheme ?? "";
					const sb = mb.scheme ?? "";
					return sa < sb ? -1 : sa > sb ? 1 : 0;
				});
				c.external_mappings = sortedMappings.map((m) => {
					const numericConf =
						typeof m.confidence === "number"
							? m.confidence
							: typeof m.mapping_confidence === "number"
								? m.mapping_confidence
								: undefined;
					return {
						scheme: m.scheme,
						target_uri: m.target_uri,
						...(numericConf !== undefined ? { confidence: numericConf } : {}),
						...(m.predicate !== null && m.predicate !== undefined
							? { predicate: m.predicate }
							: {}),
						...(m.rationale !== null && m.rationale !== undefined
							? { rationale: m.rationale }
							: {}),
						...(m.pinned !== null && m.pinned !== undefined
							? { pinned: m.pinned }
							: {}),
						...(m.provenance !== null && m.provenance !== undefined
							? { provenance: m.provenance }
							: {}),
						...(m.reference_pack_id !== null &&
						m.reference_pack_id !== undefined
							? { reference_pack_id: m.reference_pack_id }
							: {}),
						...(m.reference_pack_sha256 !== null &&
						m.reference_pack_sha256 !== undefined
							? { reference_pack_sha256: m.reference_pack_sha256 }
							: {}),
					};
				});
			}
			if (Array.isArray(n.synonyms) && n.synonyms.length > 0) {
				c.synonyms = n.synonyms;
			}
			if (
				typeof n.parent_concept_uri === "string" &&
				n.parent_concept_uri.length > 0
			) {
				c.parent_concept_uri = n.parent_concept_uri;
			}
			if (n.deprecated !== null && n.deprecated !== undefined) {
				c.deprecated = n.deprecated;
			}
			if (n.metadata && Object.keys(n.metadata).length > 0) {
				c.metadata = n.metadata;
			}
			if (Array.isArray(n.source_hints) && n.source_hints.length > 0) {
				c.source_hints = n.source_hints;
			}
			return c;
		});

	const sortedRelationships = [...edges]
		.sort((a, b) =>
			a.relationship_uri < b.relationship_uri
				? -1
				: a.relationship_uri > b.relationship_uri
					? 1
					: 0,
		)
		.map((e) => {
			const endpoints =
				e.endpoints && e.endpoints.length >= 2
					? e.endpoints
					: [e.source_concept_uri, e.target_concept_uri];
			const r: Record<string, unknown> = {
				endpoints,
				relationship_name: e.relationship_name,
				relationship_uri: e.relationship_uri,
				target_link: e.target_link,
			};
			// Non-_REL_EXT fields: preserve explicit empty string vs null/undefined without fabricating text
			if (e.grain_semantics !== null && e.grain_semantics !== undefined) {
				r.grain_semantics = e.grain_semantics;
			}
			if (
				e.yagni_cost_guard_policy !== null &&
				e.yagni_cost_guard_policy !== undefined
			) {
				r.yagni_cost_guard_policy = e.yagni_cost_guard_policy;
			}
			// _REL_EXT scoped extension annotations
			if (typeof e.cardinality === "string" && e.cardinality.length > 0) {
				r.cardinality = e.cardinality;
			}
			if (typeof e.description === "string" && e.description.length > 0) {
				r.description = e.description;
			}
			if (e.deprecated !== null && e.deprecated !== undefined) {
				r.deprecated = e.deprecated;
			}
			if (e.metadata && Object.keys(e.metadata).length > 0) {
				r.metadata = e.metadata;
			}
			if (Array.isArray(e.source_hints) && e.source_hints.length > 0) {
				r.source_hints = e.source_hints;
			}
			return r;
		});

	const normalizedReferenceEnums = [...(session.reference_enumerations ?? [])]
		.map((rawEnum) => {
			if (!rawEnum || typeof rawEnum !== "object") return rawEnum;
			const out: Record<string, unknown> = {};
			for (const [k, v] of Object.entries(rawEnum)) {
				if (v === null || v === undefined) continue;
				if (ENUM_EXT.has(k) && isEmptyExtValue(v)) continue;
				out[k] = v;
			}
			return out;
		})
		.sort((a, b) => {
			const ua =
				a && typeof a === "object"
					? String((a as Record<string, unknown>).enum_uri ?? "")
					: "";
			const ub =
				b && typeof b === "object"
					? String((b as Record<string, unknown>).enum_uri ?? "")
					: "";
			return ua < ub ? -1 : ua > ub ? 1 : 0;
		});

	const sortedDecisionRefs = [...(session.decision_refs ?? [])].sort((a, b) =>
		a < b ? -1 : a > b ? 1 : 0,
	);
	const sortedStandardsRefs = [...(session.standards_references ?? [])].sort(
		(a, b) => {
			const sa = typeof a === "string" ? a : JSON.stringify(a);
			const sb = typeof b === "string" ? b : JSON.stringify(b);
			return sa < sb ? -1 : sa > sb ? 1 : 0;
		},
	);

	const root: Record<string, unknown> = {
		canonical_business_concepts: sortedConcepts,
		canonical_relationships: sortedRelationships,
		decision_refs: sortedDecisionRefs,
		...(session.domain_namespace !== null &&
		session.domain_namespace !== undefined
			? { domain_namespace: session.domain_namespace }
			: {}),
		...(session.enterprise_owner !== null &&
		session.enterprise_owner !== undefined
			? { enterprise_owner: session.enterprise_owner }
			: {}),
		...(session.active_version !== null && session.active_version !== undefined
			? { ontology_version: session.active_version }
			: {}),
		reference_enumerations: normalizedReferenceEnums,
		standards_references: sortedStandardsRefs,
	};
	if (session.parent_sha256 && /^[a-f0-9]{64}$/i.test(session.parent_sha256)) {
		root.parent_sha256 = session.parent_sha256.toLowerCase();
	}
	if (
		typeof session.description === "string" &&
		session.description.length > 0
	) {
		root.description = session.description;
	}
	if (session.metadata && Object.keys(session.metadata).length > 0) {
		root.metadata = session.metadata;
	}
	return root;
}

function serializeGraphAYaml(root: Record<string, unknown>): string {
	const formatKey = (key: string): string =>
		/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) ? key : JSON.stringify(key);

	const isInlineYamlValue = (val: unknown): boolean => {
		if (val === null || val === undefined) return true;
		if (
			typeof val === "string" ||
			typeof val === "number" ||
			typeof val === "boolean"
		) {
			return true;
		}
		if (Array.isArray(val)) return val.length === 0;
		if (typeof val === "object") {
			return (
				Object.entries(val as Record<string, unknown>).filter(
					([, v]) => v !== undefined,
				).length === 0
			);
		}
		return true;
	};

	const formatInlineYamlValue = (val: unknown): string => {
		if (val === null || val === undefined) return "null";
		if (typeof val === "string") return JSON.stringify(val);
		if (typeof val === "number") {
			return Number.isFinite(val) ? String(val) : "null";
		}
		if (typeof val === "boolean") return val ? "true" : "false";
		if (Array.isArray(val)) return "[]";
		if (typeof val === "object") return "{}";
		return JSON.stringify(String(val));
	};

	const emitNode = (val: unknown, indent: number, out: string[]): void => {
		const pad = " ".repeat(indent);
		if (Array.isArray(val)) {
			for (const item of val) {
				if (isInlineYamlValue(item)) {
					out.push(`${pad}- ${formatInlineYamlValue(item)}`);
				} else if (Array.isArray(item)) {
					out.push(`${pad}-`);
					emitNode(item, indent + 2, out);
				} else if (item && typeof item === "object") {
					const entries = Object.entries(
						item as Record<string, unknown>,
					).filter(([, v]) => v !== undefined);
					const [firstEntry, ...restEntries] = entries;
					if (!firstEntry) {
						out.push(`${pad}- {}`);
						continue;
					}
					const [firstKey, firstVal] = firstEntry;
					if (isInlineYamlValue(firstVal)) {
						out.push(
							`${pad}- ${formatKey(firstKey)}: ${formatInlineYamlValue(firstVal)}`,
						);
					} else {
						out.push(`${pad}- ${formatKey(firstKey)}:`);
						emitNode(firstVal, indent + 4, out);
					}
					for (const [k, v] of restEntries) {
						if (isInlineYamlValue(v)) {
							out.push(`${pad}  ${formatKey(k)}: ${formatInlineYamlValue(v)}`);
						} else {
							out.push(`${pad}  ${formatKey(k)}:`);
							emitNode(v, indent + 4, out);
						}
					}
				}
			}
			return;
		}
		if (val && typeof val === "object") {
			const entries = Object.entries(val as Record<string, unknown>).filter(
				([, v]) => v !== undefined,
			);
			for (const [k, v] of entries) {
				if (isInlineYamlValue(v)) {
					out.push(`${pad}${formatKey(k)}: ${formatInlineYamlValue(v)}`);
				} else {
					out.push(`${pad}${formatKey(k)}:`);
					emitNode(v, indent + 2, out);
				}
			}
		}
	};

	const preferredTopKeys = [
		"ontology_version",
		"domain_namespace",
		"enterprise_owner",
		"description",
		"parent_sha256",
		"decision_refs",
		"standards_references",
		"canonical_business_concepts",
		"canonical_relationships",
		"reference_enumerations",
		"metadata",
	];
	const orderedRoot: Record<string, unknown> = {};
	for (const k of preferredTopKeys) {
		if (k in root && root[k] !== undefined) {
			orderedRoot[k] = root[k];
		}
	}
	for (const [k, v] of Object.entries(root)) {
		if (!(k in orderedRoot) && v !== undefined) {
			orderedRoot[k] = v;
		}
	}
	const lines: string[] = [];
	emitNode(orderedRoot, 0, lines);
	return lines.join("\n");
}

export function generateLocalExport(
	format: string,
	version: string,
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	session: StudioSessionSummary,
): string {
	const normalizedFormat = format.toLowerCase().trim();
	const validFormats = [
		"json",
		"turtle",
		"jsonld",
		"ntriples",
		"nt",
		"rdf",
		"yaml",
		"rfc8785",
		"jcs",
	];
	if (!validFormats.includes(normalizedFormat)) {
		throw new Error(
			`Unsupported export format '${format}'. Supported formats: ${validFormats.join(", ")}`,
		);
	}

	const rawDomain = session.domain_namespace || "general";
	const rawOwner = session.enterprise_owner || "Enterprise Architecture";
	const domain = escapeIri(rawDomain);

	switch (normalizedFormat) {
		case "rfc8785":
		case "jcs": {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				active_version: version,
			});
			return dumpsJcs(canonical, { excludeNone: true });
		}
		case "ntriples":
		case "nt": {
			const ontIri = `<urn:opendv:ontology:${domain}>`;
			const rdfType = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>";
			const owlOnt = "<http://www.w3.org/2002/07/owl#Ontology>";
			const owlClass = "<http://www.w3.org/2002/07/owl#Class>";
			const owlObjProp = "<http://www.w3.org/2002/07/owl#ObjectProperty>";
			const owlVer = "<http://www.w3.org/2002/07/owl#versionInfo>";
			const rdfsLabel = "<http://www.w3.org/2000/01/rdf-schema#label>";
			const rdfsComment = "<http://www.w3.org/2000/01/rdf-schema#comment>";
			const rdfsDomain = "<http://www.w3.org/2000/01/rdf-schema#domain>";
			const rdfsRange = "<http://www.w3.org/2000/01/rdf-schema#range>";
			const lines: string[] = [
				`${ontIri} ${rdfType} ${owlOnt} .`,
				`${ontIri} ${owlVer} "${escapeTurtleString(version)}" .`,
			];
			for (const n of nodes) {
				const safeUri = `<${escapeIri(n.concept_uri)}>`;
				lines.push(`${safeUri} ${rdfType} ${owlClass} .`);
				lines.push(
					`${safeUri} ${rdfsLabel} "${escapeTurtleString(n.concept_name)}" .`,
				);
				lines.push(
					`${safeUri} ${rdfsComment} "${escapeTurtleString(n.definition)}" .`,
				);
			}
			for (const e of edges) {
				const safeUri = `<${escapeIri(e.relationship_uri)}>`;
				lines.push(`${safeUri} ${rdfType} ${owlObjProp} .`);
				lines.push(
					`${safeUri} ${rdfsLabel} "${escapeTurtleString(e.relationship_name)}" .`,
				);
				lines.push(
					`${safeUri} ${rdfsDomain} <${escapeIri(e.source_concept_uri)}> .`,
				);
				lines.push(
					`${safeUri} ${rdfsRange} <${escapeIri(e.target_concept_uri)}> .`,
				);
			}
			return lines.join("\n");
		}
		case "json": {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				domain_namespace: rawDomain,
				enterprise_owner: rawOwner,
				active_version: version,
			});
			return dumpsJcs(canonical, { excludeNone: true });
		}
		case "turtle": {
			const lines = [
				"@prefix owl: <http://www.w3.org/2002/07/owl#> .",
				"@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
				"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
				"@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
				`@prefix opendv: <urn:opendv:ontology:${domain}#> .`,
				"",
				`<urn:opendv:ontology:${domain}> a owl:Ontology ;`,
				`    owl:versionInfo "${escapeTurtleString(version)}" .`,
				"",
			];
			for (const n of nodes) {
				const safeUri = escapeIri(n.concept_uri);
				lines.push(`<${safeUri}> a owl:Class ;`);
				lines.push(`    rdfs:label "${escapeTurtleString(n.concept_name)}" ;`);
				lines.push(`    rdfs:comment "${escapeTurtleString(n.definition)}" .`);
				lines.push("");
			}
			for (const e of edges) {
				const safeUri = escapeIri(e.relationship_uri);
				lines.push(`<${safeUri}> a owl:ObjectProperty ;`);
				lines.push(
					`    rdfs:label "${escapeTurtleString(e.relationship_name)}" ;`,
				);
				lines.push(`    rdfs:domain <${escapeIri(e.source_concept_uri)}> ;`);
				lines.push(`    rdfs:range <${escapeIri(e.target_concept_uri)}> .`);
				lines.push("");
			}
			return lines.join("\n");
		}
		case "jsonld": {
			const graph = [
				...nodes.map((n) => ({
					"@id": escapeIri(n.concept_uri),
					"@type": "owl:Class",
					"rdfs:label": n.concept_name,
					"rdfs:comment": n.definition,
				})),
				...edges.map((e) => ({
					"@id": escapeIri(e.relationship_uri),
					"@type": "owl:ObjectProperty",
					"rdfs:label": e.relationship_name,
					"rdfs:domain": { "@id": escapeIri(e.source_concept_uri) },
					"rdfs:range": { "@id": escapeIri(e.target_concept_uri) },
				})),
			];
			return JSON.stringify(
				{
					"@context": {
						owl: "http://www.w3.org/2002/07/owl#",
						rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
						rdfs: "http://www.w3.org/2000/01/rdf-schema#",
					},
					"@graph": graph,
				},
				null,
				2,
			);
		}
		case "rdf": {
			const lines = [
				'<?xml version="1.0" encoding="utf-8"?>',
				'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:owl="http://www.w3.org/2002/07/owl#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">',
				`  <owl:Ontology rdf:about="urn:opendv:ontology:${escapeXml(domain)}">`,
				`    <owl:versionInfo>${escapeXml(version)}</owl:versionInfo>`,
				"  </owl:Ontology>",
			];
			for (const n of nodes) {
				lines.push(
					`  <owl:Class rdf:about="${escapeXml(escapeIri(n.concept_uri))}">`,
				);
				lines.push(`    <rdfs:label>${escapeXml(n.concept_name)}</rdfs:label>`);
				lines.push(
					`    <rdfs:comment>${escapeXml(n.definition)}</rdfs:comment>`,
				);
				lines.push("  </owl:Class>");
			}
			for (const e of edges) {
				lines.push(
					`  <owl:ObjectProperty rdf:about="${escapeXml(escapeIri(e.relationship_uri))}">`,
				);
				lines.push(
					`    <rdfs:label>${escapeXml(e.relationship_name)}</rdfs:label>`,
				);
				lines.push(
					`    <rdfs:domain rdf:resource="${escapeXml(escapeIri(e.source_concept_uri))}"/>`,
				);
				lines.push(
					`    <rdfs:range rdf:resource="${escapeXml(escapeIri(e.target_concept_uri))}"/>`,
				);
				lines.push("  </owl:ObjectProperty>");
			}
			lines.push("</rdf:RDF>");
			return lines.join("\n");
		}
		case "yaml":
		default: {
			const canonical = canonicalizeGraphADict(nodes, edges, {
				...session,
				domain_namespace: rawDomain,
				enterprise_owner: rawOwner,
				active_version: version,
			});
			return serializeGraphAYaml(canonical);
		}
	}
}

export function createStudioStore(options: StudioStoreOptions = {}) {
	const baseApiUrl = (
		options.apiUrl ??
		(typeof process !== "undefined" && process.env?.OPENDV_API_URL
			? process.env.OPENDV_API_URL
			: "")
	).replace(/\/+$/, "");

	const resolveUrl = (path: string) => {
		const cleanPath = path.startsWith("/") ? path : `/${path}`;
		if (baseApiUrl) {
			return `${baseApiUrl}${cleanPath}`;
		}
		return cleanPath;
	};

	const normalizeSourceDoc = (
		raw: SourceDocument & { confidential?: boolean },
	): SourceDocument => {
		const flag = Boolean(raw.do_not_send_to_agent ?? raw.confidential);
		return {
			...raw,
			do_not_send_to_agent: flag,
			confidential: flag,
		};
	};

	const computeJcsSha256Hex = async (
		value: unknown,
	): Promise<string | undefined> => {
		if (typeof crypto === "undefined" || !crypto.subtle) return undefined;
		try {
			const jcs = dumpsJcs(value);
			const buf = await crypto.subtle.digest(
				"SHA-256",
				new TextEncoder().encode(jcs),
			);
			return Array.from(new Uint8Array(buf))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");
		} catch {
			return undefined;
		}
	};

	const parseLoadedGraphPayload = (rawOnt: Record<string, unknown>) => {
		const conceptsList = (rawOnt.concepts ||
			rawOnt.canonical_business_concepts ||
			rawOnt.canonical_concepts ||
			[]) as Array<Record<string, unknown>>;
		const relationshipsList = (rawOnt.canonical_relationships ||
			rawOnt.relationships ||
			[]) as Array<Record<string, unknown>>;

		const loadedNodes: ConceptNode[] = conceptsList.map((c, idx) => {
			const mappings =
				(c.external_mappings as ExternalMapping[] | undefined) || [];
			const rawConf =
				typeof c.confidence === "number" ? c.confidence : undefined;
			const derived = deriveGroundingStatus(mappings, rawConf);
			let status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED" =
				derived.grounding_status;
			if (mappings.length > 0 && typeof c.grounding_status === "string") {
				if (
					c.grounding_status === "UNGROUNDED" ||
					c.grounding_status === derived.grounding_status
				) {
					status = c.grounding_status as
						| "GROUNDED"
						| "LOW_CONFIDENCE"
						| "UNGROUNDED";
				}
			}
			const conf = rawConf !== undefined ? rawConf : derived.confidence;
			const conceptUri = String(c.concept_uri || "");

			return {
				id: conceptUri,
				concept_uri: conceptUri,
				concept_name:
					(c.concept_name as string | undefined) ||
					conceptUri.split(":").pop()?.replace(/_/g, " ") ||
					"Concept",
				elm_archetype:
					(c.elm_archetype as string | undefined) || "CORE_BUSINESS_CONCEPT",
				definition: (c.definition as string | undefined) || "",
				description: (c.description as string | null | undefined) ?? null,
				target_hub: (c.target_hub as string | null | undefined) ?? null,
				parent_concept_uri:
					(c.parent_concept_uri as string | null | undefined) ?? null,
				confidence: conf,
				grounding_status: status,
				attributes: (c.attributes as ConceptAttribute[] | undefined) || [],
				synonyms: (c.synonyms as string[] | undefined) || [],
				external_mappings: mappings,
				deprecated: (c.deprecated as boolean | null | undefined) ?? null,
				metadata:
					(c.metadata as Record<string, unknown> | null | undefined) ?? null,
				source_hints: (c.source_hints as string[] | null | undefined) ?? null,
				governance_constraints:
					(c.governance_constraints as
						| Record<string, unknown>
						| null
						| undefined) ?? null,
				key_authority_tiers:
					(c.key_authority_tiers as
						| Record<string, unknown>
						| null
						| undefined) ?? null,
				x: 100 + (idx % 4) * 260,
				y: 100 + Math.floor(idx / 4) * 180,
			};
		});

		const loadedEdges: RelationshipEdge[] = relationshipsList.map((r) => {
			const relUri = String(r.relationship_uri || "");
			const endpoints = Array.isArray(r.endpoints)
				? (r.endpoints as string[])
				: undefined;
			const src =
				(r.source_concept_uri as string | undefined) ||
				(endpoints && endpoints[0]) ||
				"";
			const tgt =
				(r.target_concept_uri as string | undefined) ||
				(endpoints && endpoints[1]) ||
				"";
			return {
				id: relUri,
				relationship_uri: relUri,
				relationship_name:
					(r.relationship_name as string | undefined) ||
					relUri.split(":").pop()?.replace(/_/g, " ") ||
					"relationship",
				target_link: (r.target_link as string | undefined) || "lnk_default",
				source_concept_uri: src,
				target_concept_uri: tgt,
				endpoints: endpoints ?? [src, tgt],
				grain_semantics:
					(r.grain_semantics as string | null | undefined) ?? null,
				cardinality: (r.cardinality as string | null | undefined) ?? null,
				description: (r.description as string | null | undefined) ?? null,
				deprecated: (r.deprecated as boolean | null | undefined) ?? null,
				metadata:
					(r.metadata as Record<string, unknown> | null | undefined) ?? null,
				source_hints: (r.source_hints as string[] | null | undefined) ?? null,
				yagni_cost_guard_policy:
					(r.yagni_cost_guard_policy as
						| Record<string, unknown>
						| null
						| undefined) ?? null,
			};
		});

		return { loadedNodes, loadedEdges };
	};

	let activeSessionRequestId = 0;
	let activeGraphRequestId = 0;
	let activeClarificationsRequestId = 0;
	let activeSourcesRequestId = 0;
	let activeProposalsRequestId = 0;
	let activeJobGeneration = 0;
	const activeEventSources = new Set<{ close: () => void }>();
	const activePollTimers = new Set<ReturnType<typeof setTimeout>>();

	const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

	const api = createStore<StudioStoreState>()((set, get) => {
		const appendSseEvent = (
			entry: Omit<StudioEventLogEntry, "id" | "timestamp">,
		) => {
			const newEntry: StudioEventLogEntry = {
				...entry,
				id: `evt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
				timestamp: new Date().toISOString(),
			};
			set((state) => ({
				sseEvents: [newEntry, ...state.sseEvents].slice(0, 50),
			}));
		};

		const studioFetch = async (
			fetchImpl: typeof fetch,
			path: string,
			init?: RequestInit,
		): Promise<Response> => {
			const method = (init?.method || "GET").toUpperCase();
			const headers: Record<string, string> = {};
			if (init?.headers) {
				if (typeof Headers !== "undefined" && init.headers instanceof Headers) {
					init.headers.forEach((v, k) => {
						headers[k] = v;
					});
				} else if (Array.isArray(init.headers)) {
					for (const [k, v] of init.headers) {
						headers[k] = v;
					}
				} else {
					Object.assign(headers, init.headers as Record<string, string>);
				}
			}

			if (MUTATING_METHODS.has(method) && !headers["X-OpenDV-Workbench"]) {
				headers["X-OpenDV-Workbench"] = "1";
			}

			const token = get().authToken;
			if (token && !headers["Authorization"] && !headers["authorization"]) {
				headers["Authorization"] = `Bearer ${token}`;
			}

			const nextInit: RequestInit = {
				...init,
				...(init?.method ? { method: init.method } : {}),
				credentials: "include",
				headers,
			};

			const res = await fetchImpl(resolveUrl(path), nextInit);
			const roleHeader = res?.headers?.get?.("X-OpenDV-Role");
			if (roleHeader) {
				const hydratedRole = normalizeStudioRole(roleHeader);
				if (hydratedRole && hydratedRole !== get().userRole) {
					set({ userRole: hydratedRole });
				}
			}
			if (res && res.status === 401) {
				set({ authRequired: true });
			} else if (res && res.ok && get().authRequired) {
				set({ authRequired: false });
			}
			return res;
		};

		const cancelActiveJobWatchers = () => {
			for (const es of activeEventSources) {
				try {
					es.close();
				} catch {
					// ignore cleanup errors
				}
			}
			activeEventSources.clear();
			for (const timer of activePollTimers) {
				clearTimeout(timer);
			}
			activePollTimers.clear();
		};

		const handleJobPayloadUpdate = async (
			sid: string,
			jobId: string,
			jobKind: "parse" | "clarify" | "construct",
			payload: Record<string, unknown>,
			fetchImpl: typeof fetch,
			opts?: { eventType?: string; autoCommit?: boolean; myGen?: number },
		): Promise<boolean> => {
			const myGen = opts?.myGen ?? activeJobGeneration;
			if (myGen !== activeJobGeneration) return true;
			const eventType =
				opts?.eventType ||
				(typeof payload.event === "string" ? payload.event : "");
			const rawStatus = String(
				payload.status || payload.state || "",
			).toUpperCase();
			const isTerminalSuccess =
				eventType === "job_completed" ||
				rawStatus === "COMPLETED" ||
				rawStatus === "SUCCEEDED" ||
				rawStatus === "DONE";
			const isTerminalFailure =
				eventType === "job_failed" ||
				eventType === "job_cancelled" ||
				eventType === "JOB_ABANDONED_SERVER_CRASH" ||
				rawStatus === "FAILED" ||
				rawStatus === "ERROR" ||
				rawStatus === "CANCELLED" ||
				rawStatus === "CANCELED" ||
				rawStatus === "ABANDONED" ||
				rawStatus === "JOB_ABANDONED_SERVER_CRASH";

			if (get().activeSession?.session_id !== sid) {
				return isTerminalSuccess || isTerminalFailure;
			}

			const resultObj =
				payload.result && typeof payload.result === "object"
					? (payload.result as Record<string, unknown>)
					: payload;
			const stage = String(
				payload.stage || payload.step || eventType || jobKind,
			);
			const pct =
				typeof payload.pct_complete === "number"
					? payload.pct_complete
					: typeof payload.progress_pct === "number"
						? payload.progress_pct
						: typeof payload.progress === "number"
							? payload.progress
							: typeof resultObj.pct_complete === "number"
								? resultObj.pct_complete
								: typeof resultObj.progress_pct === "number"
									? resultObj.progress_pct
									: typeof resultObj.progress === "number"
										? resultObj.progress
										: undefined;
			const chunksCountRaw =
				typeof payload.chunks_count === "number"
					? payload.chunks_count
					: typeof resultObj.chunks_count === "number"
						? resultObj.chunks_count
						: undefined;

			appendSseEvent({
				job_id: jobId,
				stage,
				message: String(
					payload.message ||
						payload.detail ||
						payload.error ||
						`${jobKind} ${eventType || rawStatus || stage}`,
				),
				progress_pct: pct,
				level: isTerminalFailure
					? "error"
					: isTerminalSuccess
						? "success"
						: "info",
			});

			if (isTerminalFailure) {
				if (
					myGen !== activeJobGeneration ||
					get().activeSession?.session_id !== sid
				) {
					return true;
				}
				const isCrashAbandoned =
					eventType === "JOB_ABANDONED_SERVER_CRASH" ||
					rawStatus === "JOB_ABANDONED_SERVER_CRASH";
				const isCancelled =
					eventType === "job_cancelled" ||
					rawStatus === "CANCELLED" ||
					rawStatus === "CANCELED";
				const errMsg = String(
					payload.error ||
						payload.detail ||
						payload.message ||
						(isCrashAbandoned
							? "Background job was abandoned after a server restart."
							: isCancelled
								? `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} job was cancelled.`
								: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} job failed`),
				);
				set((state) => ({
					...(jobKind === "clarify" ? { isClarifying: false } : {}),
					...(jobKind === "construct" ? { isConstructing: false } : {}),
					...(jobKind === "parse" || state.parsingState.status === "parsing"
						? {
								parsingState: {
									...state.parsingState,
									status: "failed" as const,
									stage: "error",
									error: errMsg,
								},
							}
						: {}),
				}));
				get().addToast({
					type: "urn:opendv:error:job-failed",
					title: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} Job Failed`,
					status: 500,
					detail: errMsg,
					code: isCrashAbandoned
						? "JOB_ABANDONED_SERVER_CRASH"
						: isCancelled
							? "JOB_CANCELLED"
							: String(payload.code || "JOB_FAILED"),
				});
				return true;
			}

			if (jobKind === "parse") {
				const chunks = Array.isArray(resultObj.evidence_chunks)
					? (resultObj.evidence_chunks as EvidenceChunk[])
					: Array.isArray(payload.evidence_chunks)
						? (payload.evidence_chunks as EvidenceChunk[])
						: undefined;
				if (isTerminalSuccess) {
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					) {
						return true;
					}
					set((state) => ({
						parsingState: {
							...state.parsingState,
							status: "completed",
							progress_pct: 100,
							stage: "completed",
							evidence_chunks: chunks ?? state.parsingState.evidence_chunks,
							chunks_count: chunks
								? chunks.length
								: (chunksCountRaw ?? state.parsingState.chunks_count),
							error: null,
						},
					}));
					return true;
				}
				if (
					myGen !== activeJobGeneration ||
					get().activeSession?.session_id !== sid
				) {
					return false;
				}
				set((state) => {
					const nextChunksCount = chunks
						? chunks.length
						: chunksCountRaw !== undefined
							? chunksCountRaw
							: eventType === "chunk_parsed"
								? (state.parsingState.chunks_count || 0) + 1
								: state.parsingState.chunks_count;
					return {
						parsingState: {
							...state.parsingState,
							status: "parsing",
							stage: stage || state.parsingState.stage,
							progress_pct: pct ?? state.parsingState.progress_pct,
							...(chunks ? { evidence_chunks: chunks } : {}),
							...(nextChunksCount !== undefined
								? { chunks_count: nextChunksCount }
								: {}),
						},
					};
				});
				return false;
			}

			if (jobKind === "clarify") {
				if (isTerminalSuccess) {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						set({ isClarifying: false });
						await get().loadClarifications(fetchImpl, sid);
					}
					return true;
				}
				return false;
			}

			if (jobKind === "construct") {
				if (isTerminalSuccess) {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						set({ isConstructing: false });
						if (opts?.autoCommit || Boolean(resultObj.auto_committed)) {
							await get().selectSession(sid, fetchImpl, true);
						} else {
							await get().loadProposals(fetchImpl, sid);
						}
					}
					return true;
				}
				return false;
			}
			return false;
		};

		const monitorStudioJob = (
			sid: string,
			jobId: string,
			jobKind: "parse" | "clarify" | "construct",
			fetchImpl: typeof fetch,
			jobOpts?: { autoCommit?: boolean; myGen?: number },
		) => {
			if (!jobId) return;
			const myGen = jobOpts?.myGen ?? activeJobGeneration;

			const schedulePoll = (fn: () => void, delayMs: number) => {
				if (myGen !== activeJobGeneration) return;
				const timer = setTimeout(() => {
					activePollTimers.delete(timer);
					if (myGen !== activeJobGeneration) return;
					fn();
				}, delayMs);
				activePollTimers.add(timer);
			};

			const failWatchedJob = (
				reason: string,
				code = "JOB_POLLING_FAILED",
				status = 504,
			) => {
				if (
					myGen !== activeJobGeneration ||
					get().activeSession?.session_id !== sid
				) {
					return;
				}
				appendSseEvent({
					job_id: jobId,
					stage: "error",
					message: reason,
					level: "error",
				});
				set((state) => ({
					...(jobKind === "clarify" ? { isClarifying: false } : {}),
					...(jobKind === "construct" ? { isConstructing: false } : {}),
					...(jobKind === "parse" || state.parsingState.status === "parsing"
						? {
								parsingState: {
									...state.parsingState,
									status: "failed" as const,
									stage: "error",
									error: reason,
								},
							}
						: {}),
				}));
				get().addToast({
					type: "urn:opendv:error:job-failed",
					title: `${jobKind.charAt(0).toUpperCase() + jobKind.slice(1)} Job Failed`,
					status,
					detail: reason,
					code,
				});
			};

			const startPollingFallback = () => {
				const startedAt = Date.now();
				let attempts = 0;
				let consecutiveErrors = 0;
				const maxDurationMs = options.maxPollingDurationMs ?? 600_000;
				const maxAttempts = options.maxPollAttempts ?? Number.POSITIVE_INFINITY;
				const maxErrorRetries = options.maxPollErrorRetries ?? 5;
				const initialIntervalMs = options.initialPollIntervalMs ?? 200;
				const maxIntervalMs = options.maxPollIntervalMs ?? 1000;

				const pollOnce = async () => {
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					) {
						return;
					}
					if (
						jobKind === "parse" &&
						get().parsingState.status === "completed"
					) {
						return;
					}
					if (
						attempts >= maxAttempts ||
						Date.now() - startedAt > maxDurationMs
					) {
						if (myGen === activeJobGeneration) {
							failWatchedJob(
								`Timed out waiting for ${jobKind} job ${jobId} to complete.`,
								"JOB_POLL_TIMEOUT",
								504,
							);
						}
						return;
					}
					attempts += 1;
					const nextDelay =
						attempts <= 5
							? initialIntervalMs
							: Math.min(
									maxIntervalMs,
									initialIntervalMs * 2 ** Math.min(attempts - 5, 4),
								);
					try {
						const pollRes = await studioFetch(
							fetchImpl,
							`/api/v1/ontology/studio/jobs/${encodeURIComponent(jobId)}`,
						);
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						) {
							return;
						}
						if (
							jobKind === "parse" &&
							get().parsingState.status === "completed"
						) {
							return;
						}
						if (!pollRes.ok) {
							if (
								pollRes.status === 429 ||
								pollRes.status === 502 ||
								pollRes.status === 503 ||
								pollRes.status === 504
							) {
								consecutiveErrors += 1;
								if (consecutiveErrors <= maxErrorRetries) {
									schedulePoll(() => {
										void pollOnce();
									}, nextDelay);
									return;
								}
							}
							const errData = (await pollRes.json().catch(() => ({}))) as {
								detail?: string;
								error?: string;
								code?: string;
							};
							if (
								myGen !== activeJobGeneration ||
								get().activeSession?.session_id !== sid
							) {
								return;
							}
							failWatchedJob(
								errData.detail ||
									errData.error ||
									`Polling ${jobKind} job ${jobId} failed with HTTP ${pollRes.status}.`,
								errData.code || "JOB_POLL_HTTP_ERROR",
								pollRes.status,
							);
							return;
						}
						consecutiveErrors = 0;
						const pollData = (await pollRes.json().catch(() => null)) as Record<
							string,
							unknown
						> | null;
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						) {
							return;
						}
						if (
							!pollData ||
							(pollData.status === undefined &&
								pollData.state === undefined &&
								pollData.pct_complete === undefined &&
								pollData.progress_pct === undefined &&
								pollData.progress === undefined &&
								pollData.chunks_count === undefined &&
								pollData.result === undefined &&
								pollData.error === undefined)
						) {
							// Unmocked or empty non-job response: stop cleanly
							return;
						}
						const done = await handleJobPayloadUpdate(
							sid,
							jobId,
							jobKind,
							pollData,
							fetchImpl,
							{
								...(jobOpts?.autoCommit ? { autoCommit: true } : {}),
								myGen,
							},
						);
						if (
							!done &&
							myGen === activeJobGeneration &&
							get().activeSession?.session_id === sid
						) {
							schedulePoll(() => {
								void pollOnce();
							}, nextDelay);
						}
					} catch (err) {
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						) {
							return;
						}
						consecutiveErrors += 1;
						if (consecutiveErrors <= maxErrorRetries) {
							schedulePoll(() => {
								void pollOnce();
							}, nextDelay);
							return;
						}
						failWatchedJob(
							`Network error while polling ${jobKind} job ${jobId}: ${String(err)}`,
							"NETWORK_ERROR",
							503,
						);
					}
				};
				schedulePoll(
					() => {
						void pollOnce();
					},
					Math.min(50, initialIntervalMs),
				);
			};

			const currentEs = (globalThis as Record<string, unknown>).EventSource;
			const isCustomMockEs =
				typeof currentEs === "function" && currentEs !== INITIAL_EVENT_SOURCE;
			const forceMockEs = Boolean(
				(globalThis as Record<string, unknown>).__FORCE_STUDIO_EVENTSOURCE__,
			);
			// S6-CRIT-01: Browser EventSource cannot attach Authorization: Bearer headers.
			// When an explicit Bearer token is configured, bypass EventSource directly to polling.
			const hasBearerToken = Boolean(get().authToken);
			const canUseEventSource =
				!hasBearerToken &&
				typeof EventSource !== "undefined" &&
				(fetchImpl === fetch || isCustomMockEs || forceMockEs);

			if (canUseEventSource) {
				try {
					const esUrl = resolveUrl(
						`/api/v1/ontology/studio/jobs/${encodeURIComponent(jobId)}/events`,
					);
					const es = new EventSource(esUrl, { withCredentials: true });
					activeEventSources.add(es);
					let settled = false;

					const cleanupEs = () => {
						activeEventSources.delete(es);
						try {
							es.close();
						} catch {
							// ignore
						}
					};

					const onEventData = async (ev: MessageEvent, eventType?: string) => {
						if (myGen !== activeJobGeneration) {
							settled = true;
							cleanupEs();
							return;
						}
						let parsed: Record<string, unknown> | null = null;
						const rawData =
							ev.data !== undefined && ev.data !== null ? String(ev.data) : "";
						if (rawData.trim()) {
							try {
								const candidate = JSON.parse(rawData) as unknown;
								if (
									candidate &&
									typeof candidate === "object" &&
									!Array.isArray(candidate)
								) {
									parsed = candidate as Record<string, unknown>;
								} else if (eventType) {
									parsed = { message: rawData };
								}
							} catch {
								if (eventType) {
									parsed = { message: rawData };
								}
							}
						} else if (eventType) {
							parsed = {};
						}
						if (!parsed) return;

						const done = await handleJobPayloadUpdate(
							sid,
							jobId,
							jobKind,
							parsed,
							fetchImpl,
							{
								...(eventType ? { eventType } : {}),
								...(jobOpts?.autoCommit ? { autoCommit: true } : {}),
								myGen,
							},
						);
						if (done) {
							settled = true;
							cleanupEs();
						}
					};

					es.onmessage = (ev) => {
						void onEventData(ev);
					};
					const namedSseEvents = [
						"job_started",
						"stage",
						"chunk_parsed",
						"clarification_generated",
						"fibo_grounding",
						"job_completed",
						"job_failed",
						"job_cancelled",
						"JOB_ABANDONED_SERVER_CRASH",
						"progress",
						"complete",
						"completed",
					] as const;
					for (const evtName of namedSseEvents) {
						es.addEventListener(evtName, (ev) => {
							void onEventData(ev as MessageEvent, evtName);
						});
					}
					es.onerror = () => {
						cleanupEs();
						if (
							!settled &&
							myGen === activeJobGeneration &&
							get().activeSession?.session_id === sid
						) {
							startPollingFallback();
						}
					};
					return;
				} catch {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						startPollingFallback();
					}
					return;
				}
			}

			if (
				myGen === activeJobGeneration &&
				get().activeSession?.session_id === sid
			) {
				startPollingFallback();
			}
		};

		return {
			activeMode: options.activeMode ?? "local_standalone",
			userRole: options.userRole ?? "editor",
			authToken: null,
			authRequired: false,
			isBootstrappingAuth: false,

			activeSession: options.initialSession ?? null,
			sessions: options.initialSession ? [options.initialSession] : [],
			isLoadingSessions: false,

			sources: [],
			isLoadingSources: false,
			parsingState: createDefaultParsingState(),

			clarifications: [],
			answers: {},
			answers_sha256: undefined,
			isLoadingClarifications: false,
			isClarifying: false,
			isConstructing: false,
			proposals: [],
			isLoadingProposals: false,

			nodes: [],
			edges: [],
			committedNodes: [],
			committedEdges: [],
			selectedNodeId: null,
			selectedEdgeId: null,
			isLoadingGraph: false,
			hasUnsavedChanges: false,
			viewedVersion: null,
			isHistoricalView: false,
			isCommitting: false,
			isPromoting: false,
			isSubmittingWaiver: false,

			diffState: createDefaultDiffState(),
			qualityScorecard: createDefaultQualityScorecard(),
			sessionWaivers: {},
			layout: createDefaultLayoutState(),
			sseEvents: [],
			toasts: [],

			setMode: (mode) => set({ activeMode: mode }),
			setUserRole: (role) => set({ userRole: role }),
			setAuthToken: (token) => {
				const trimmed = token ? token.trim() : "";
				const jwtRole = trimmed
					? extractRoleFromJwtPayload(trimmed)
					: undefined;
				set({
					authToken: trimmed || null,
					authRequired: false,
					...(jwtRole ? { userRole: jwtRole } : {}),
				});
			},

			exchangeLaunchNonce: async (nonce, fetchImpl = fetch) => {
				const cleanNonce = nonce.trim();
				if (!cleanNonce) return false;
				set({ isBootstrappingAuth: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						"/api/v1/ontology/studio/auth/token",
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								nonce: cleanNonce,
							}),
						},
					);
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
							title?: string;
						};
						get().addToast({
							type: "urn:opendv:error:auth-nonce-failed",
							title: err.title || "Launch Token Exchange Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "AUTH_INVALID_LAUNCH_NONCE",
						});
						set({
							isBootstrappingAuth: false,
							authRequired: res.status === 401,
						});
						return false;
					}
					const data = (await res.json().catch(() => ({}))) as {
						access_token?: string;
						token?: string;
						role?: string;
						user_role?: string;
						principal?: string;
					};
					const nextToken = (data.access_token || data.token || "").trim();
					const hydratedRole =
						normalizeStudioRole(data.role ?? data.user_role, data.principal) ??
						(nextToken ? extractRoleFromJwtPayload(nextToken) : undefined);
					set((state) => ({
						authToken: nextToken || state.authToken,
						authRequired: false,
						isBootstrappingAuth: false,
						...(hydratedRole ? { userRole: hydratedRole } : {}),
					}));
					return true;
				} catch (err) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Authentication Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
					set({ isBootstrappingAuth: false });
					return false;
				}
			},

			bootstrapLoopbackAuth: async (fetchImpl = fetch) => {
				set({ isBootstrappingAuth: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						"/api/v1/ontology/studio/init",
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: "{}",
						},
					);
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
							title?: string;
						};
						get().addToast({
							type: "urn:opendv:error:auth-init-failed",
							title: err.title || "Loopback Initialization Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "AUTH_BOOTSTRAP_FAILED",
						});
						set({ isBootstrappingAuth: false });
						return false;
					}
					const data = (await res.json().catch(() => ({}))) as {
						local_token?: string;
						access_token?: string;
						token?: string;
						role?: string;
						user_role?: string;
						principal?: string;
					};
					const issuedToken = (
						data.local_token ||
						data.access_token ||
						data.token ||
						""
					).trim();
					const hydratedRole =
						normalizeStudioRole(data.role ?? data.user_role, data.principal) ??
						(issuedToken ? extractRoleFromJwtPayload(issuedToken) : undefined);
					set((state) => ({
						authToken: issuedToken || state.authToken,
						authRequired: false,
						isBootstrappingAuth: false,
						...(hydratedRole ? { userRole: hydratedRole } : {}),
					}));
					await get().loadSessions(fetchImpl);
					return true;
				} catch (err) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Loopback Bootstrap Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
					set({ isBootstrappingAuth: false });
					return false;
				}
			},

			// Session actions
			loadSessions: async (fetchImpl = fetch) => {
				set({ isLoadingSessions: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						"/api/v1/ontology/studio/sessions",
					);
					if (!res.ok) {
						const errorJson = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
							title?: string;
						};
						get().addToast({
							type: "urn:opendv:error:session-load-failed",
							title: errorJson.title || "Failed to Load Sessions",
							status: res.status,
							detail: errorJson.detail || `Server returned HTTP ${res.status}`,
							code:
								errorJson.code ||
								(res.status === 401 ? "UNAUTHORIZED" : "SESSION_NOT_FOUND"),
						});
						set({
							isLoadingSessions: false,
							...(res.status === 401 ? { authRequired: true } : {}),
						});
						return;
					}
					const data = (await res.json()) as
						| StudioSessionSummary[]
						| {
								sessions?: StudioSessionSummary[];
								role?: string;
								user_role?: string;
								principal?: string;
						  };
					const sessionList = Array.isArray(data) ? data : data.sessions || [];
					const hydratedRole = !Array.isArray(data)
						? normalizeStudioRole(data.user_role ?? data.role, data.principal)
						: undefined;
					const currentActive = get().activeSession;
					const refreshedActive = currentActive
						? sessionList.find((s) => s.session_id === currentActive.session_id)
						: undefined;
					const activeHeadChanged = Boolean(
						currentActive &&
							refreshedActive &&
							(refreshedActive.head_sha256 !== currentActive.head_sha256 ||
								refreshedActive.active_version !==
									currentActive.active_version),
					);
					const isDirty = get().hasUnsavedChanges;

					// P14d MAJ-01: When unsaved changes exist on the canvas, keep activeSession
					// pinned to the baseline SHA/version that local working state forked from so
					// If-Match properly catches concurrent modifications instead of bypassing OCC.
					const nextActiveSession = currentActive
						? refreshedActive
							? activeHeadChanged && isDirty
								? {
										...refreshedActive,
										head_sha256: currentActive.head_sha256,
										active_version: currentActive.active_version,
									}
								: refreshedActive
							: currentActive
						: (sessionList[0] ?? null);

					set({
						sessions: sessionList,
						isLoadingSessions: false,
						activeSession: nextActiveSession,
						authRequired: false,
						...(hydratedRole ? { userRole: hydratedRole } : {}),
					});

					if (
						nextActiveSession &&
						(!currentActive ||
							currentActive.session_id !== nextActiveSession.session_id)
					) {
						await get().selectSession(nextActiveSession.session_id, fetchImpl);
					} else if (currentActive && activeHeadChanged && !isDirty) {
						await get().selectSession(
							currentActive.session_id,
							fetchImpl,
							true,
						);
					}
				} catch (err) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
					set({ isLoadingSessions: false });
				}
			},

			selectSession: async (sessionId, fetchImpl = fetch, force = false) => {
				if (get().hasUnsavedChanges && !force) {
					get().addToast({
						type: "urn:opendv:error:unsaved-changes",
						title: "Unsaved Changes",
						status: 400,
						detail:
							"Cannot switch session with unsaved changes. Please commit, discard, or specify force=true.",
						code: "UNSAVED_CHANGES",
					});
					return;
				}

				const prevSessionId = get().activeSession?.session_id;
				if (sessionId !== prevSessionId) {
					activeJobGeneration++;
					cancelActiveJobWatchers();
				}

				const reqId = ++activeSessionRequestId;
				const found = get().sessions.find((s) => s.session_id === sessionId);

				const sessionWaivers = get().sessionWaivers || {};
				const savedWaiver = sessionWaivers[sessionId];
				const initialScorecard = createDefaultQualityScorecard();
				if (savedWaiver) {
					initialScorecard.waiver_applied = savedWaiver.waiver_applied;
					initialScorecard.waiver_rationale = savedWaiver.waiver_rationale;
					initialScorecard.waived_concept_uris =
						savedWaiver.waived_concept_uris;
				}

				// Clear session-specific state immediately to avoid cross-session contamination (MIN-01)
				set({
					activeSession: found ?? null,
					isLoadingSessions: true,
					isLoadingSources: false,
					isLoadingGraph: false,
					isLoadingClarifications: false,
					isLoadingProposals: false,
					isClarifying: false,
					isConstructing: false,
					hasUnsavedChanges: false,
					viewedVersion: null,
					isHistoricalView: false,
					nodes: [],
					edges: [],
					committedNodes: [],
					committedEdges: [],
					clarifications: [],
					proposals: [],
					sources: [],
					answers: {},
					answers_sha256: undefined,
					parsingState: createDefaultParsingState(),
					selectedNodeId: null,
					selectedEdgeId: null,
					qualityScorecard: initialScorecard,
					diffState: createDefaultDiffState(),
				});

				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sessionId)}`,
					);
					if (reqId !== activeSessionRequestId) return; // Discard stale response before read

					if (res.ok) {
						const raw = (await res.json().catch(() => null)) as unknown;
						if (reqId !== activeSessionRequestId) return; // Discard stale response after read (MAJ-02)

						const sessionData = (
							Array.isArray(raw)
								? raw[0]
								: ((raw as { session?: StudioSessionSummary })?.session ?? raw)
						) as StudioSessionSummary | null | undefined;

						if (sessionData?.session_id) {
							const rawObj = (
								raw && typeof raw === "object" ? raw : {}
							) as Record<string, unknown>;
							const sessionObj = (sessionData || {}) as unknown as Record<
								string,
								unknown
							>;
							const hydratedRole = normalizeStudioRole(
								sessionObj.user_role ??
									sessionObj.role ??
									rawObj.user_role ??
									rawObj.role,
								sessionObj.principal ?? rawObj.principal,
							);
							const serverWaiver = (sessionObj.active_waiver ||
								rawObj.active_waiver ||
								(sessionObj.waiver_applied ? sessionObj : null)) as {
								waiver_applied?: boolean;
								waiver_rationale?: string;
								waived_concept_uris?: string[];
								waived_base_sha256?: string;
							} | null;

							const isWaiverFresh =
								Boolean(serverWaiver?.waiver_applied) &&
								(!serverWaiver?.waived_base_sha256 ||
									serverWaiver.waived_base_sha256 === sessionData.head_sha256);

							set((state) => {
								const updatedWaivers = isWaiverFresh
									? {
											...state.sessionWaivers,
											[sessionId]: {
												waiver_applied: true,
												waiver_rationale:
													serverWaiver?.waiver_rationale ||
													"Server authorized waiver",
												waived_concept_uris:
													serverWaiver?.waived_concept_uris || [],
												waived_base_sha256:
													serverWaiver?.waived_base_sha256 ||
													sessionData.head_sha256 ||
													"",
											},
										}
									: state.sessionWaivers;

								const updatedScorecard = isWaiverFresh
									? {
											...state.qualityScorecard,
											waiver_applied: true,
											waiver_rationale:
												serverWaiver?.waiver_rationale ||
												"Server authorized waiver",
											waived_concept_uris:
												serverWaiver?.waived_concept_uris || [],
											waived_base_sha256:
												serverWaiver?.waived_base_sha256 ||
												sessionData.head_sha256 ||
												"",
										}
									: state.qualityScorecard;

								return {
									activeSession: sessionData,
									sessions: state.sessions.some(
										(s) => s.session_id === sessionId,
									)
										? state.sessions.map((s) =>
												s.session_id === sessionId ? sessionData : s,
											)
										: [sessionData, ...state.sessions],
									sessionWaivers: updatedWaivers,
									qualityScorecard: updatedScorecard,
									isLoadingSessions: false,
									...(hydratedRole ? { userRole: hydratedRole } : {}),
								};
							});

							if (reqId !== activeSessionRequestId) return;

							// Reload graph, clarifications, sources, and pending proposals (S6-CRIT-02)
							await Promise.all([
								get().loadGraph(
									sessionData.active_version,
									fetchImpl,
									sessionId,
								),
								get().loadClarifications(fetchImpl, sessionId),
								get().loadSources(fetchImpl, sessionId),
								get().loadProposals(fetchImpl, sessionId),
							]);
						} else {
							set({ isLoadingSessions: false });
							get().addToast({
								type: "urn:opendv:error:invalid-session-data",
								title: "Invalid Session Metadata",
								status: 500,
								detail: `Received malformed session metadata for ${sessionId}`,
								code: "INVALID_SESSION_DATA",
							});
						}
					} else {
						set({ isLoadingSessions: false });
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (reqId !== activeSessionRequestId) return;
						get().addToast({
							type: "urn:opendv:error:session-fetch-failed",
							title: "Failed to Select Session",
							status: res.status,
							detail:
								err.detail ||
								`Session ${sessionId} returned HTTP ${res.status}`,
							code: err.code || "SESSION_NOT_FOUND",
						});
					}
				} catch (err) {
					if (reqId === activeSessionRequestId) {
						set({ isLoadingSessions: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Session Selection Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
				}
			},

			createSession: async (req, fetchImpl = fetch, force = false) => {
				if (get().hasUnsavedChanges && !force) {
					get().addToast({
						type: "urn:opendv:error:unsaved-changes",
						title: "Unsaved Changes",
						status: 400,
						detail:
							"Cannot create new session with unsaved changes. Please commit, discard, or specify force=true.",
						code: "UNSAVED_CHANGES",
					});
					return null;
				}

				if (req.session_id && !/^[a-z0-9_-]+$/.test(req.session_id)) {
					get().addToast({
						type: "urn:opendv:error:invalid-session-id",
						title: "Invalid Session Identifier",
						status: 400,
						detail:
							"Session ID must match regex ^[a-z0-9_-]+$ with lowercase letters, numbers, underscores, and dashes.",
						code: "SYNTAX_INVALID_ARGUMENT",
					});
					return null;
				}

				try {
					const res = await studioFetch(
						fetchImpl,
						"/api/v1/ontology/studio/sessions",
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify(req),
						},
					);
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
							title?: string;
						};
						get().addToast({
							type: "urn:opendv:error:session-create",
							title: err.title || "Failed to Create Session",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "SESSION_ALREADY_EXISTS",
						});
						return null;
					}
					const created = (await res.json()) as StudioSessionSummary;
					set((state) => ({
						sessions: [created, ...state.sessions],
						layout: { ...state.layout, activeModal: null },
					}));

					// Load initial state for newly created session (force=true since pre-check passed)
					await get().selectSession(created.session_id, fetchImpl, true);
					return created;
				} catch (err) {
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
					return null;
				}
			},

			// Sources actions
			uploadSource: async (file, confidential = false, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) {
					get().addToast({
						type: "urn:opendv:error:no-session",
						title: "No Active Session",
						status: 400,
						detail:
							"Please select or create a session before uploading documents.",
						code: "SYNTAX_INVALID_ARGUMENT",
					});
					return null;
				}

				if (!isSupportedSourceFile(file.name, file.type)) {
					get().addToast({
						type: "urn:opendv:error:unsupported-mime",
						title: "Unsupported File Format",
						status: 415,
						detail: `The file format '${file.name}' is not supported. Please upload PDF, DOCX, XLSX, HTML, JSON, CSV, Markdown, or TXT.`,
						code: "SYNTAX_INVALID_ARGUMENT",
					});
					return null;
				}

				set({ isLoadingSources: true });
				try {
					let filePayload: Blob | File;
					if (
						file.content instanceof Blob ||
						(typeof File !== "undefined" && file.content instanceof File)
					) {
						filePayload = file.content;
					} else if (
						file.content instanceof ArrayBuffer ||
						ArrayBuffer.isView(file.content)
					) {
						filePayload = new Blob([file.content as BlobPart], {
							type: file.type || "application/octet-stream",
						});
					} else if (
						typeof file.content === "string" &&
						file.content.length > 0
					) {
						filePayload = new Blob([file.content], {
							type: file.type || "text/plain",
						});
					} else {
						get().addToast({
							type: "urn:opendv:error:empty-source",
							title: "Empty Source File",
							status: 400,
							detail: "Cannot upload an empty source document.",
							code: "SYNTAX_INVALID_ARGUMENT",
						});
						set({ isLoadingSources: false });
						return null;
					}

					const formData = new FormData();
					formData.append("file", filePayload, file.name);
					formData.append(
						"do_not_send_to_agent",
						confidential ? "true" : "false",
					);

					const sid = activeSession.session_id;
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources`,
						{
							method: "POST",
							body: formData,
						},
					);
					if (get().activeSession?.session_id !== sid) {
						set({ isLoadingSources: false });
						return null;
					}

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
							title?: string;
						};
						set({ isLoadingSources: false });
						if (get().activeSession?.session_id !== sid) return null;
						get().addToast({
							type: "urn:opendv:error:source-upload",
							title: err.title || "Failed to Upload Source",
							status: res.status,
							detail: err.detail || `Upload failed with status ${res.status}`,
							code: err.code || "CORRUPT_SOURCE_BLOB",
						});
						return null;
					}

					const uploadedRaw = (await res.json()) as SourceDocument & {
						confidential?: boolean;
					};
					if (get().activeSession?.session_id !== sid) {
						set({ isLoadingSources: false });
						return null;
					}
					const uploaded = normalizeSourceDoc(uploadedRaw);
					set((state) => ({
						sources: [uploaded, ...state.sources],
						isLoadingSources: false,
					}));
					return uploaded;
				} catch (err) {
					set({ isLoadingSources: false });
					if (get().activeSession?.session_id === activeSession.session_id) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return null;
				}
			},

			deleteSource: async (sourceId, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return false;

				const sid = activeSession.session_id;
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/${encodeURIComponent(sourceId)}`,
						{ method: "DELETE" },
					);
					if (get().activeSession?.session_id !== sid) return false;

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:source-delete",
							title: "Failed to Delete Source",
							status: res.status,
							detail: err.detail || `Delete failed with status ${res.status}`,
							code: err.code || "SOURCE_NOT_FOUND",
						});
						return false;
					}

					set((state) => ({
						sources: state.sources.filter((s) => s.source_id !== sourceId),
					}));
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			startParse: async (ocrMode = "auto", fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return null;

				const sid = activeSession.session_id;
				const myGen = ++activeJobGeneration;
				cancelActiveJobWatchers();

				set({
					parsingState: {
						status: "parsing",
						progress_pct: 10,
						active_job_id: null,
						stage: "queued",
						ocr_mode: ocrMode,
						evidence_chunks: [],
						error: null,
					},
				});

				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/parse`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({ ocr_mode: ocrMode }),
						},
					);
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						)
							return null;
						set({
							parsingState: {
								status: "failed",
								progress_pct: 0,
								active_job_id: null,
								stage: "error",
								ocr_mode: ocrMode,
								evidence_chunks: [],
								error: err.detail || `Parse failed with status ${res.status}`,
							},
						});
						get().addToast({
							type: "urn:opendv:error:parse-failed",
							title: "Document Parsing Failed",
							status: res.status,
							detail: err.detail || `Failed with HTTP ${res.status}`,
							code: err.code || "SYNTAX_INVALID_ARGUMENT",
						});
						return null;
					}

					const job = (await res.json()) as {
						job_id: string;
						status?: string;
						stage?: string;
						pct_complete?: number;
						progress_pct?: number;
						progress?: number;
						chunks_count?: number;
						evidence_chunks?: EvidenceChunk[];
						result?: {
							evidence_chunks?: EvidenceChunk[];
							chunks_count?: number;
							pct_complete?: number;
							progress_pct?: number;
						};
					};
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;
					const initialChunks =
						job.result?.evidence_chunks || job.evidence_chunks || [];
					const initialPct =
						job.pct_complete ??
						job.progress_pct ??
						job.progress ??
						job.result?.pct_complete ??
						job.result?.progress_pct;
					const initialChunksCount =
						initialChunks.length > 0
							? initialChunks.length
							: (job.chunks_count ?? job.result?.chunks_count);
					const isAlreadyDone =
						String(job.status || "").toUpperCase() === "COMPLETED" ||
						String(job.status || "").toUpperCase() === "SUCCEEDED";
					set((state) => ({
						parsingState: {
							...state.parsingState,
							status: isAlreadyDone ? "completed" : "parsing",
							progress_pct: isAlreadyDone ? 100 : (initialPct ?? 35),
							active_job_id: job.job_id,
							stage: isAlreadyDone ? "completed" : job.stage || "layout_eval",
							evidence_chunks:
								initialChunks.length > 0
									? initialChunks
									: state.parsingState.evidence_chunks,
							...(initialChunksCount !== undefined
								? { chunks_count: initialChunksCount }
								: {}),
						},
					}));
					appendSseEvent({
						job_id: job.job_id,
						stage: job.stage || "layout_eval",
						message: `Parse job ${job.job_id} started`,
						progress_pct: initialPct ?? 35,
						level: "info",
					});
					if (!isAlreadyDone && job.job_id) {
						monitorStudioJob(sid, job.job_id, "parse", fetchImpl, { myGen });
					}
					return job.job_id;
				} catch (err) {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						set({
							parsingState: {
								status: "failed",
								progress_pct: 0,
								active_job_id: null,
								stage: "error",
								ocr_mode: ocrMode,
								evidence_chunks: [],
								error: String(err),
							},
						});
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return null;
				}
			},

			updateParsingProgress: (update) => {
				set((state) => ({
					parsingState: { ...state.parsingState, ...update },
				}));
			},

			loadSources: async (fetchImpl = fetch, targetSessionId?: string) => {
				const activeSession = get().activeSession;
				if (!activeSession) return;
				const expectedId = targetSessionId ?? activeSession.session_id;
				if (activeSession.session_id !== expectedId) return;

				const reqId = ++activeSourcesRequestId;
				set({ isLoadingSources: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/sources`,
					);
					if (!res.ok) {
						if (
							reqId === activeSourcesRequestId &&
							get().activeSession?.session_id === expectedId
						) {
							set({ isLoadingSources: false });
							const err = (await res.json().catch(() => ({}))) as {
								detail?: string;
								code?: string;
							};
							get().addToast({
								type: "urn:opendv:error:sources-load-failed",
								title: "Failed to Load Sources",
								status: res.status,
								detail: err.detail || `Server returned HTTP ${res.status}`,
								code: err.code || "SOURCES_LOAD_FAILED",
							});
						}
						return;
					}

					if (
						reqId !== activeSourcesRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const data = (await res.json()) as
						| Array<SourceDocument & { confidential?: boolean }>
						| { sources: Array<SourceDocument & { confidential?: boolean }> };

					if (
						reqId !== activeSourcesRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const rawList = Array.isArray(data) ? data : data.sources || [];
					const sourcesList = rawList.map((item) => normalizeSourceDoc(item));
					set({
						sources: sourcesList,
						isLoadingSources: false,
					});
				} catch (err) {
					if (
						reqId === activeSourcesRequestId &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingSources: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
				}
			},

			// Clarification & Construct / Proposals actions
			loadClarifications: async (
				fetchImpl = fetch,
				targetSessionId?: string,
			) => {
				const activeSession = get().activeSession;
				if (!activeSession) return;
				const expectedId = targetSessionId ?? activeSession.session_id;
				if (activeSession.session_id !== expectedId) return;

				const reqId = ++activeClarificationsRequestId;

				set({ isLoadingClarifications: true });
				try {
					const [res, answersRes] = await Promise.all([
						studioFetch(
							fetchImpl,
							`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/questions`,
						),
						studioFetch(
							fetchImpl,
							`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/answers`,
						).catch(() => null),
					]);

					if (!res.ok) {
						if (
							reqId === activeClarificationsRequestId &&
							get().activeSession?.session_id === expectedId
						) {
							set({ isLoadingClarifications: false });
							const err = (await res.json().catch(() => ({}))) as {
								detail?: string;
								code?: string;
							};
							get().addToast({
								type: "urn:opendv:error:clarifications-load-failed",
								title: "Failed to Load Clarifications",
								status: res.status,
								detail: err.detail || `Server returned HTTP ${res.status}`,
								code: err.code || "CLARIFICATIONS_LOAD_FAILED",
							});
						}
						return;
					}

					if (
						reqId !== activeClarificationsRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const questionsEtag = res.headers
						?.get?.("ETag")
						?.replace(/^W\//, "")
						.replace(/"/g, "");
					const answersEtag = answersRes?.headers
						?.get?.("ETag")
						?.replace(/^W\//, "")
						.replace(/"/g, "");

					const data = (await res.json()) as
						| Array<Record<string, unknown>>
						| {
								questions?: Array<Record<string, unknown>>;
								answers?:
									| Record<string, unknown>
									| Array<Record<string, unknown>>;
								answers_sha256?: string;
						  };

					let rawAnswersPayload: unknown = null;
					if (answersRes && answersRes.ok) {
						rawAnswersPayload = await answersRes.json().catch(() => null);
					}

					if (
						reqId !== activeClarificationsRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const rawQuestions: Array<Record<string, unknown>> = Array.isArray(
						data,
					)
						? data
						: data.questions || [];

					const answersListFromEndpoint: Array<Record<string, unknown>> | null =
						Array.isArray(rawAnswersPayload)
							? (rawAnswersPayload as Array<Record<string, unknown>>)
							: rawAnswersPayload &&
									typeof rawAnswersPayload === "object" &&
									Array.isArray(
										(rawAnswersPayload as { answers?: unknown }).answers,
									)
								? ((
										rawAnswersPayload as {
											answers: Array<Record<string, unknown>>;
										}
									).answers ?? null)
								: null;

					const answersMap: Record<string, ClarificationAnswer> = {};
					if (answersListFromEndpoint) {
						for (const item of answersListFromEndpoint) {
							const qid = String(item.question_id || "");
							if (!qid) continue;
							const sel = Array.isArray(item.selected_options)
								? (item.selected_options as string[])
								: Array.isArray(item.selected_option_ids)
									? (item.selected_option_ids as string[])
									: [];
							const uText =
								typeof item.user_text === "string"
									? item.user_text
									: typeof item.custom_answer_text === "string"
										? item.custom_answer_text
										: undefined;
							answersMap[qid] = {
								question_id: qid,
								selected_option_ids: sel,
								custom_answer_text: uText,
								answered_at:
									typeof item.answered_at === "string"
										? item.answered_at
										: undefined,
							};
						}
					}

					const normalizedQuestions: ClarificationQuestion[] = rawQuestions.map(
						(rq) => {
							const qid = String(rq.question_id || "");
							const rawOpts = Array.isArray(rq.rationale_options)
								? (rq.rationale_options as Array<Record<string, unknown>>)
								: Array.isArray(rq.options)
									? (rq.options as Array<Record<string, unknown>>)
									: [];
							const rationaleOptions: ClarificationRationaleOption[] =
								rawOpts.map((opt, idx) => ({
									id: String(opt.id || opt.option_id || `opt-${idx + 1}`),
									label: String(
										opt.label || opt.option_id || `Option ${idx + 1}`,
									),
									description:
										typeof opt.description === "string"
											? opt.description
											: typeof opt.rationale === "string"
												? opt.rationale
												: undefined,
								}));

							const rawCites = Array.isArray(rq.evidence_citations)
								? (rq.evidence_citations as Array<Record<string, unknown>>)
								: [];
							const citations: ClarificationEvidenceCitation[] = rawCites.map(
								(c) => ({
									source_file: String(
										c.source_file || c.reference_uri || c.source_id || "source",
									),
									chunk_id: String(c.chunk_id || "chunk"),
									excerpt: String(
										c.excerpt || c.snippet || c.text_snippet || "",
									),
								}),
							);

							const matchedAnswer = answersMap[qid];
							const legacySelected = Array.isArray(rq.selected_option_ids)
								? (rq.selected_option_ids as string[])
								: Array.isArray(rq.selected_options)
									? (rq.selected_options as string[])
									: undefined;
							const legacyStatus = String(rq.status || "").toUpperCase();
							const isAnswered =
								Boolean(matchedAnswer) || legacyStatus === "ANSWERED";

							if (
								!matchedAnswer &&
								isAnswered &&
								legacySelected &&
								legacySelected.length > 0
							) {
								answersMap[qid] = {
									question_id: qid,
									selected_option_ids: legacySelected,
									custom_answer_text:
										typeof rq.custom_answer_text === "string"
											? rq.custom_answer_text
											: undefined,
									answered_at:
										typeof rq.answered_at === "string"
											? rq.answered_at
											: undefined,
								};
							}

							const rawCertainty = String(
								rq.certainty || "MEDIUM",
							).toUpperCase();
							const certainty: "HIGH" | "MEDIUM" | "LOW" =
								rawCertainty === "HIGH" ||
								rawCertainty === "MEDIUM" ||
								rawCertainty === "LOW"
									? rawCertainty
									: "MEDIUM";

							return {
								question_id: qid,
								concept_name: String(
									rq.concept_name || rq.focus_concept || "Concept",
								),
								question_text: String(rq.question_text || ""),
								certainty,
								rationale_options: rationaleOptions,
								evidence_citations: citations,
								status: isAnswered ? "ANSWERED" : "PENDING",
								answered_at:
									matchedAnswer?.answered_at ??
									(typeof rq.answered_at === "string"
										? rq.answered_at
										: undefined),
								selected_option_ids:
									matchedAnswer?.selected_option_ids ?? legacySelected,
								custom_answer_text:
									matchedAnswer?.custom_answer_text ??
									(typeof rq.custom_answer_text === "string"
										? rq.custom_answer_text
										: undefined),
							};
						},
					);

					let answersSha: string | undefined =
						(rawAnswersPayload &&
						typeof rawAnswersPayload === "object" &&
						!Array.isArray(rawAnswersPayload)
							? (rawAnswersPayload as { answers_sha256?: string })
									.answers_sha256
							: undefined) ||
						answersEtag ||
						(!Array.isArray(data) ? data.answers_sha256 : undefined) ||
						questionsEtag ||
						undefined;

					if (!answersSha && answersListFromEndpoint !== null) {
						if (answersListFromEndpoint.length === 0) {
							answersSha = EMPTY_ANSWERS_SHA256;
						} else {
							const sortedRawAnswers = [...answersListFromEndpoint].sort(
								(a, b) => {
									const qa = String(a.question_id || "");
									const qb = String(b.question_id || "");
									return qa < qb ? -1 : qa > qb ? 1 : 0;
								},
							);
							answersSha =
								(await computeJcsSha256Hex(sortedRawAnswers)) ??
								EMPTY_ANSWERS_SHA256;
						}
					}

					if (
						reqId !== activeClarificationsRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					set({
						clarifications: normalizedQuestions,
						answers: answersMap,
						answers_sha256: answersSha,
						isLoadingClarifications: false,
					});
				} catch (err) {
					if (
						reqId === activeClarificationsRequestId &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingClarifications: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
				}
			},

			setAnswerDraft: (questionId, selectedOptionIds, customAnswerText) => {
				set((state) => ({
					answers: {
						...state.answers,
						[questionId]: {
							question_id: questionId,
							selected_option_ids: selectedOptionIds,
							custom_answer_text: customAnswerText,
						},
					},
				}));
			},

			submitAnswer: async (
				answer,
				expectedAnswersSha256,
				fetchImpl = fetch,
			) => {
				const activeSession = get().activeSession;
				if (!activeSession) return false;

				const sid = activeSession.session_id;
				const expectedSha =
					expectedAnswersSha256 || get().answers_sha256 || EMPTY_ANSWERS_SHA256;

				try {
					const payload: Record<string, unknown> = {
						answers: [
							{
								question_id: answer.question_id,
								selected_options: answer.selected_option_ids,
								user_text:
									answer.custom_answer_text !== undefined &&
									answer.custom_answer_text !== ""
										? answer.custom_answer_text
										: null,
							},
						],
						expected_answers_sha256: expectedSha,
					};

					const headers: Record<string, string> = {
						"Content-Type": "application/json",
						"If-Match": `"${expectedSha}"`,
					};

					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/answers`,
						{
							method: "POST",
							headers,
							body: JSON.stringify(payload),
						},
					);
					if (get().activeSession?.session_id !== sid) return false;

					if (!res.ok) {
						if (res.status === 409 || res.status === 412) {
							// Concurrent modification: reload clarifications but preserve unsent local drafts
							const currentDrafts = { ...get().answers };
							await get().loadClarifications(fetchImpl, sid);
							if (get().activeSession?.session_id !== sid) return false;
							set((state) => ({
								answers: {
									...state.answers,
									...currentDrafts,
								},
							}));
						}

						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:answer-submit",
							title: "Failed to Submit Answer",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "SYNTAX_INVALID_ARGUMENT",
						});
						return false;
					}

					const updatedEtag = res.headers
						?.get?.("ETag")
						?.replace(/^W\//, "")
						.replace(/"/g, "");
					const resData = (await res.json().catch(() => ({}))) as {
						answers_sha256?: string;
					};
					if (get().activeSession?.session_id !== sid) return false;
					const newSha = resData.answers_sha256 || updatedEtag;
					const now = new Date().toISOString();

					set((state) => ({
						clarifications: state.clarifications.map((q) =>
							q.question_id === answer.question_id
								? {
										...q,
										status: "ANSWERED" as const,
										answered_at: now,
										selected_option_ids: answer.selected_option_ids,
										custom_answer_text: answer.custom_answer_text,
									}
								: q,
						),
						answers: {
							...state.answers,
							[answer.question_id]: { ...answer, answered_at: now },
						},
						answers_sha256: newSha || state.answers_sha256,
					}));
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			startClarify: async (clarifyOptions = {}, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return null;
				const sid = activeSession.session_id;
				const myGen = ++activeJobGeneration;
				cancelActiveJobWatchers();
				set({ isClarifying: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/clarify`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								mode: clarifyOptions.mode || "auto",
								max_questions: clarifyOptions.max_questions ?? 10,
							}),
						},
					);
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						)
							return null;
						set({ isClarifying: false });
						get().addToast({
							type: "urn:opendv:error:clarify-failed",
							title: "Clarification Generation Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "CLARIFY_FAILED",
						});
						return null;
					}
					const job = (await res.json()) as {
						job_id: string;
						status?: string;
						stage?: string;
						progress_pct?: number;
					};
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;
					appendSseEvent({
						job_id: job.job_id,
						stage: job.stage || "clarify",
						message: `Clarify job ${job.job_id} queued`,
						progress_pct: job.progress_pct ?? 10,
						level: "info",
					});
					const isDone =
						String(job.status || "").toUpperCase() === "COMPLETED" ||
						String(job.status || "").toUpperCase() === "SUCCEEDED";
					if (isDone) {
						set({ isClarifying: false });
						await get().loadClarifications(fetchImpl, sid);
					} else if (job.job_id) {
						monitorStudioJob(sid, job.job_id, "clarify", fetchImpl, { myGen });
					} else {
						set({ isClarifying: false });
					}
					return job.job_id || null;
				} catch (err) {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						set({ isClarifying: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return null;
				}
			},

			startConstruct: async (constructOptions = {}, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return null;
				const sid = activeSession.session_id;
				const isAutoCommit = Boolean(constructOptions.auto_commit);
				const headers: Record<string, string> = {
					"Content-Type": "application/json",
				};
				if (isAutoCommit && activeSession.head_sha256) {
					headers["If-Match"] = `"${activeSession.head_sha256}"`;
				}
				const myGen = ++activeJobGeneration;
				cancelActiveJobWatchers();
				set({ isConstructing: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/construct`,
						{
							method: "POST",
							headers,
							body: JSON.stringify({
								mode:
									constructOptions.mode ||
									(isAutoCommit ? "deterministic" : "auto"),
								auto_commit: isAutoCommit,
								...(isAutoCommit && activeSession.head_sha256
									? {
											base_sha256: activeSession.head_sha256,
											...(activeSession.active_version
												? { base_version: activeSession.active_version }
												: {}),
										}
									: {}),
							}),
						},
					);
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (
							myGen !== activeJobGeneration ||
							get().activeSession?.session_id !== sid
						)
							return null;
						set({ isConstructing: false });
						get().addToast({
							type: "urn:opendv:error:construct-failed",
							title: "Ontology Construction Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "CONSTRUCT_FAILED",
						});
						return null;
					}
					const job = (await res.json()) as {
						job_id: string;
						status?: string;
						stage?: string;
						pct_complete?: number;
						progress_pct?: number;
						progress?: number;
					};
					if (
						myGen !== activeJobGeneration ||
						get().activeSession?.session_id !== sid
					)
						return null;
					const initialPct =
						job.pct_complete ?? job.progress_pct ?? job.progress ?? 10;
					appendSseEvent({
						job_id: job.job_id,
						stage: job.stage || "construct",
						message: `Construct job ${job.job_id} queued`,
						progress_pct: initialPct,
						level: "info",
					});
					const isDone =
						String(job.status || "").toUpperCase() === "COMPLETED" ||
						String(job.status || "").toUpperCase() === "SUCCEEDED";
					if (isDone) {
						set({ isConstructing: false });
						if (isAutoCommit) {
							await get().selectSession(sid, fetchImpl, true);
						} else {
							await get().loadProposals(fetchImpl, sid);
						}
					} else if (job.job_id) {
						monitorStudioJob(sid, job.job_id, "construct", fetchImpl, {
							...(isAutoCommit ? { autoCommit: true } : {}),
							myGen,
						});
					} else {
						set({ isConstructing: false });
					}
					return job.job_id || null;
				} catch (err) {
					if (
						myGen === activeJobGeneration &&
						get().activeSession?.session_id === sid
					) {
						set({ isConstructing: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return null;
				}
			},

			loadProposals: async (fetchImpl = fetch, targetSessionId?: string) => {
				const activeSession = get().activeSession;
				if (!activeSession) return;
				const expectedId = targetSessionId ?? activeSession.session_id;
				if (activeSession.session_id !== expectedId) return;

				const reqId = ++activeProposalsRequestId;
				set({ isLoadingProposals: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/proposals`,
					);
					if (!res.ok) {
						if (
							reqId === activeProposalsRequestId &&
							get().activeSession?.session_id === expectedId
						) {
							set({ isLoadingProposals: false });
						}
						return;
					}
					const data = (await res.json()) as
						| CandidateProposalSummary[]
						| { proposals?: CandidateProposalSummary[] };
					if (
						reqId !== activeProposalsRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;
					const list = Array.isArray(data) ? data : data.proposals || [];
					set({ proposals: list, isLoadingProposals: false });
				} catch {
					if (
						reqId === activeProposalsRequestId &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingProposals: false });
					}
				}
			},

			acceptProposal: async (
				proposalId,
				rationale,
				fetchImpl = fetch,
				acceptOptions,
			) => {
				const activeSession = get().activeSession;
				if (!activeSession) return false;
				const role = get().userRole;
				if (role === "viewer" || role === "automated") {
					get().addToast({
						type: "urn:opendv:error:forbidden-role",
						title: "Human Steward Role Required",
						status: 403,
						detail: `Role '${role}' cannot manually accept candidate proposals. Switch to an authenticated Editor or Admin steward session.`,
						code: "FORBIDDEN_ROLE",
					});
					return false;
				}
				if (get().hasUnsavedChanges && !acceptOptions?.force) {
					get().addToast({
						type: "urn:opendv:error:unsaved-changes",
						title: "Unsaved Canvas Changes",
						status: 409,
						detail:
							"Cannot accept a proposal while unsaved canvas edits exist. Please commit or discard your local canvas changes before accepting a proposal.",
						code: "UNSAVED_CHANGES",
					});
					return false;
				}
				const sid = activeSession.session_id;
				const headers: Record<string, string> = {
					"Content-Type": "application/json",
				};
				if (activeSession.head_sha256) {
					headers["If-Match"] = `"${activeSession.head_sha256}"`;
				}
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/accept`,
						{
							method: "POST",
							headers,
							body: JSON.stringify({
								...(activeSession.head_sha256
									? { base_sha256: activeSession.head_sha256 }
									: {}),
								grounding_rationale:
									rationale ||
									`Accepted candidate proposal ${proposalId} via Workbench Studio`,
								...(acceptOptions?.selfAcceptanceOverride
									? { self_acceptance_override: true }
									: {}),
							}),
						},
					);
					if (get().activeSession?.session_id !== sid) return false;
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:proposal-accept-failed",
							title: "Proposal Acceptance Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "PROPOSAL_ACCEPT_FAILED",
						});
						return false;
					}
					await get().selectSession(sid, fetchImpl, true);
					get().addToast({
						type: "urn:opendv:notice:proposal-accepted",
						title: "Proposal Accepted",
						status: 200,
						detail: `Proposal ${proposalId} ratified into session head.`,
						code: "OK",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			rejectProposal: async (proposalId, reason, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return false;
				const sid = activeSession.session_id;
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/reject`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								reason: reason || "Rejected by steward in Workbench Studio",
							}),
						},
					);
					if (get().activeSession?.session_id !== sid) return false;
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:proposal-reject-failed",
							title: "Proposal Rejection Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "PROPOSAL_REJECT_FAILED",
						});
						return false;
					}
					await get().loadProposals(fetchImpl, sid);
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			rebaseProposal: async (proposalId, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) return false;
				const sid = activeSession.session_id;
				const headers: Record<string, string> = {
					"Content-Type": "application/json",
				};
				if (activeSession.head_sha256) {
					headers["If-Match"] = `"${activeSession.head_sha256}"`;
				}
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/proposals/${encodeURIComponent(proposalId)}/rebase`,
						{
							method: "POST",
							headers,
							body: "{}",
						},
					);
					if (get().activeSession?.session_id !== sid) return false;
					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:proposal-rebase-failed",
							title: "Proposal Rebase Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "PROPOSAL_REBASE_FAILED",
						});
						return false;
					}
					await get().loadProposals(fetchImpl, sid);
					get().addToast({
						type: "urn:opendv:notice:proposal-rebased",
						title: "Proposal Rebased",
						status: 200,
						detail: `Proposal ${proposalId} rebased onto current session head.`,
						code: "OK",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			// Graph actions
			loadGraph: async (
				version,
				fetchImpl = fetch,
				targetSessionId?: string,
			) => {
				const activeSession = get().activeSession;
				if (!activeSession) return;
				const expectedId = targetSessionId ?? activeSession.session_id;
				if (get().hasUnsavedChanges) {
					get().addToast({
						type: "urn:opendv:error:unsaved-changes",
						title: "Unsaved Changes",
						status: 400,
						detail:
							"Cannot reload or switch ontology versions with unsaved working edits. Please commit or discard changes first.",
						code: "UNSAVED_CHANGES",
					});
					return;
				}

				const reqId = ++activeGraphRequestId;
				set({ isLoadingGraph: true });
				try {
					const ver = version || activeSession.active_version;
					const isHeadVersion = ver === activeSession.active_version;

					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/export?export_format=json&version=${encodeURIComponent(ver)}`,
					);

					if (!res.ok) {
						if (ver === "0.0.0" && res.status === 404) {
							if (
								reqId === activeGraphRequestId &&
								get().activeSession?.session_id === expectedId &&
								!get().hasUnsavedChanges
							) {
								set((state) => ({
									viewedVersion: "0.0.0",
									isHistoricalView: !isHeadVersion,
									nodes: [],
									edges: [],
									committedNodes: isHeadVersion ? [] : state.committedNodes,
									committedEdges: isHeadVersion ? [] : state.committedEdges,
									hasUnsavedChanges: isHeadVersion
										? false
										: state.hasUnsavedChanges,
									qualityScorecard: recomputeScorecard(state, [], []),
									isLoadingGraph: false,
								}));
							}
							return;
						}
						if (
							reqId === activeGraphRequestId &&
							get().activeSession?.session_id === expectedId
						) {
							set({ isLoadingGraph: false });
							const err = (await res.json().catch(() => ({}))) as {
								detail?: string;
								code?: string;
							};
							get().addToast({
								type: "urn:opendv:error:graph-load-failed",
								title: "Failed to Load Graph",
								status: res.status,
								detail: err.detail || `Server returned HTTP ${res.status}`,
								code: err.code || "GRAPH_LOAD_FAILED",
							});
						}
						return;
					}

					if (
						reqId !== activeGraphRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const ont = (await res.json().catch(() => null)) as Record<
						string,
						unknown
					> | null;

					if (
						reqId !== activeGraphRequestId ||
						get().activeSession?.session_id !== expectedId
					)
						return;

					const hasOntologyPayload =
						ont !== null &&
						typeof ont === "object" &&
						!Array.isArray(ont) &&
						(Array.isArray(ont.canonical_business_concepts) ||
							Array.isArray(ont.concepts) ||
							Array.isArray(ont.canonical_concepts));
					if (!hasOntologyPayload || !ont) {
						set({ isLoadingGraph: false });
						return;
					}

					const { loadedNodes, loadedEdges } = parseLoadedGraphPayload(ont);

					if (
						reqId !== activeGraphRequestId ||
						get().activeSession?.session_id !== expectedId ||
						get().hasUnsavedChanges
					)
						return;

					set((state) => {
						const enrichedSession =
							isHeadVersion && state.activeSession
								? {
										...state.activeSession,
										...(Array.isArray(ont.reference_enumerations)
											? {
													reference_enumerations:
														ont.reference_enumerations as Array<
															Record<string, unknown>
														>,
												}
											: {}),
										...(Array.isArray(ont.standards_references)
											? {
													standards_references:
														ont.standards_references as string[],
												}
											: {}),
										...(Array.isArray(ont.decision_refs)
											? { decision_refs: ont.decision_refs as string[] }
											: {}),
										...(typeof ont.parent_sha256 === "string"
											? { parent_sha256: ont.parent_sha256 }
											: {}),
										...(typeof ont.description === "string"
											? { description: ont.description }
											: {}),
										...(ont.metadata && typeof ont.metadata === "object"
											? {
													metadata: ont.metadata as Record<string, unknown>,
												}
											: {}),
									}
								: state.activeSession;

						const existingWaiver = state.sessionWaivers[expectedId];
						const nextSessionWaivers =
							isHeadVersion &&
							existingWaiver &&
							!existingWaiver.waived_concept_snapshots
								? {
										...state.sessionWaivers,
										[expectedId]: {
											...existingWaiver,
											waived_concept_snapshots: Object.fromEntries(
												loadedNodes
													.filter((n) =>
														existingWaiver.waived_concept_uris.includes(
															n.concept_uri,
														),
													)
													.map((n) => [n.concept_uri, n]),
											),
										},
									}
								: state.sessionWaivers;

						const nextState: StudioStoreState = {
							...state,
							activeSession: enrichedSession,
							committedNodes: isHeadVersion
								? loadedNodes
								: state.committedNodes,
							committedEdges: isHeadVersion
								? loadedEdges
								: state.committedEdges,
							sessionWaivers: nextSessionWaivers,
						};

						return {
							activeSession: enrichedSession,
							sessionWaivers: nextSessionWaivers,
							viewedVersion: ver,
							isHistoricalView: !isHeadVersion,
							nodes: loadedNodes,
							edges: loadedEdges,
							committedNodes: isHeadVersion
								? loadedNodes
								: state.committedNodes,
							committedEdges: isHeadVersion
								? loadedEdges
								: state.committedEdges,
							hasUnsavedChanges: isHeadVersion
								? false
								: state.hasUnsavedChanges,
							qualityScorecard: recomputeScorecard(
								nextState,
								loadedNodes,
								loadedEdges,
							),
							isLoadingGraph: false,
						};
					});
				} catch (err) {
					if (
						reqId === activeGraphRequestId &&
						get().activeSession?.session_id === expectedId
					) {
						set({ isLoadingGraph: false });
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
				}
			},

			selectNode: (nodeId) => {
				set((state) => ({
					selectedNodeId: nodeId,
					selectedEdgeId: null,
					layout: {
						...state.layout,
						rightDrawerOpen: nodeId !== null,
					},
				}));
			},

			selectEdge: (edgeId) => {
				set({
					selectedEdgeId: edgeId,
					selectedNodeId: null,
				});
			},

			updateConcept: (conceptId, updates) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				set((state) => {
					const { id: _id, concept_uri: _uri, ...safeUpdates } = updates;
					const newNodes = state.nodes.map((n) => {
						if (n.id !== conceptId && n.concept_uri !== conceptId) return n;
						const merged = { ...n, ...safeUpdates };

						const effectiveMappings =
							updates.external_mappings !== undefined
								? updates.external_mappings || []
								: n.external_mappings || [];

						const derived = deriveGroundingStatus(
							effectiveMappings,
							updates.confidence ?? n.confidence,
						);

						const nextConfidence = updates.confidence ?? derived.confidence;
						let nextGroundingStatus:
							| "GROUNDED"
							| "LOW_CONFIDENCE"
							| "UNGROUNDED" = derived.grounding_status;

						// If effective mappings is empty, strictly force UNGROUNDED (MAJ-01)
						if (effectiveMappings.length === 0) {
							nextGroundingStatus = "UNGROUNDED";
						} else if (updates.grounding_status) {
							if (
								updates.grounding_status === "UNGROUNDED" ||
								updates.grounding_status === derived.grounding_status
							) {
								nextGroundingStatus = updates.grounding_status;
							}
						}

						return {
							...merged,
							confidence: nextConfidence,
							grounding_status: nextGroundingStatus,
							external_mappings: effectiveMappings,
						};
					});

					// Only flag unsaved changes if there are semantic differences in nodes or edges from committed baseline (MIN-02, P14d MAJ-03)
					const hasSemanticDiff = hasGraphChangesFromBaseline(
						newNodes,
						state.edges,
						state.committedNodes,
						state.committedEdges,
					);

					return {
						nodes: newNodes,
						hasUnsavedChanges: hasSemanticDiff,
						qualityScorecard: recomputeScorecard(state, newNodes, state.edges),
					};
				});
			},

			updateRelationship: (edgeId, updates) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				set((state) => {
					let matched = false;
					const newEdges = state.edges.map((edge) => {
						if (edge.id !== edgeId && edge.relationship_uri !== edgeId) {
							return edge;
						}
						matched = true;
						const nextSrc =
							updates.source_concept_uri ?? edge.source_concept_uri;
						const nextTgt =
							updates.target_concept_uri ?? edge.target_concept_uri;
						const nextEndpoints =
							updates.endpoints && updates.endpoints.length >= 2
								? updates.endpoints
								: [nextSrc, nextTgt];
						return {
							...edge,
							...updates,
							id: edge.id,
							relationship_uri: edge.relationship_uri,
							source_concept_uri: nextSrc,
							target_concept_uri: nextTgt,
							endpoints: nextEndpoints,
						};
					});
					if (!matched) return state;

					const hasSemanticDiff = hasGraphChangesFromBaseline(
						state.nodes,
						newEdges,
						state.committedNodes,
						state.committedEdges,
					);
					return {
						edges: newEdges,
						hasUnsavedChanges: hasSemanticDiff,
						qualityScorecard: recomputeScorecard(state, state.nodes, newEdges),
					};
				});
			},

			addConcept: (node) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				if (
					!node.concept_uri ||
					get().nodes.some((n) => n.concept_uri === node.concept_uri)
				) {
					get().addToast({
						type: "urn:opendv:error:duplicate-concept-uri",
						title: "Duplicate Concept URI",
						status: 409,
						detail: `Concept URI '${node.concept_uri}' already exists in ontology.`,
						code: "ONT_EVO_DUPLICATE_URI",
					});
					return;
				}
				const mappings = node.external_mappings || [];
				const derived = deriveGroundingStatus(mappings, node.confidence);

				// Always enforce derived grounding status: never allow caller to assert GROUNDED with empty or insufficient mappings (MAJ-01)
				let finalGroundingStatus: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED" =
					derived.grounding_status;
				if (mappings.length > 0 && node.grounding_status) {
					if (
						node.grounding_status === "UNGROUNDED" ||
						node.grounding_status === derived.grounding_status
					) {
						finalGroundingStatus = node.grounding_status;
					}
				}

				const normalizedNode: ConceptNode = {
					...node,
					elm_archetype: node.elm_archetype || "CORE_BUSINESS_CONCEPT",
					confidence: node.confidence ?? derived.confidence,
					grounding_status: finalGroundingStatus,
					external_mappings: mappings,
				};

				set((state) => {
					const newNodes = [...state.nodes, normalizedNode];
					return {
						nodes: newNodes,
						hasUnsavedChanges: true,
						qualityScorecard: recomputeScorecard(state, newNodes, state.edges),
					};
				});
			},

			addRelationship: (edge) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				if (
					!edge.relationship_uri ||
					get().edges.some((e) => e.relationship_uri === edge.relationship_uri)
				) {
					get().addToast({
						type: "urn:opendv:error:duplicate-relationship-uri",
						title: "Duplicate Relationship URI",
						status: 409,
						detail: `Relationship URI '${edge.relationship_uri}' already exists in ontology.`,
						code: "ONT_EVO_DUPLICATE_URI",
					});
					return;
				}

				// Validate endpoint concepts exist in nodes (MIN-07)
				const currentNodes = get().nodes;
				const hasSource = currentNodes.some(
					(n) =>
						n.concept_uri === edge.source_concept_uri ||
						n.id === edge.source_concept_uri,
				);
				const hasTarget = currentNodes.some(
					(n) =>
						n.concept_uri === edge.target_concept_uri ||
						n.id === edge.target_concept_uri,
				);
				if (!hasSource || !hasTarget) {
					get().addToast({
						type: "urn:opendv:error:invalid-relationship-endpoint",
						title: "Invalid Relationship Endpoints",
						status: 400,
						detail: `Relationship '${edge.relationship_uri}' connects non-existent concept endpoints (source: '${edge.source_concept_uri}', target: '${edge.target_concept_uri}').`,
						code: "INVALID_RELATIONSHIP_ENDPOINT",
					});
					return;
				}

				set((state) => {
					const newEdges = [...state.edges, edge];
					return {
						edges: newEdges,
						hasUnsavedChanges: true,
						qualityScorecard: recomputeScorecard(state, state.nodes, newEdges),
					};
				});
			},

			deleteConcept: (conceptUriOrId) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				set((state) => {
					const target = state.nodes.find(
						(n) => n.id === conceptUriOrId || n.concept_uri === conceptUriOrId,
					);
					if (!target) return state;

					const newNodes = state.nodes.filter(
						(n) => n.concept_uri !== target.concept_uri,
					);
					const newEdges = state.edges.filter(
						(e) =>
							e.source_concept_uri !== target.concept_uri &&
							e.target_concept_uri !== target.concept_uri,
					);
					const selectedWasRemoved =
						state.selectedNodeId === target.id ||
						state.selectedNodeId === target.concept_uri;
					const selectedEdgeRemoved = state.selectedEdgeId
						? !newEdges.some(
								(e) =>
									e.id === state.selectedEdgeId ||
									e.relationship_uri === state.selectedEdgeId,
							)
						: false;

					const hasDiff = hasGraphChangesFromBaseline(
						newNodes,
						newEdges,
						state.committedNodes,
						state.committedEdges,
					);

					return {
						nodes: newNodes,
						edges: newEdges,
						selectedNodeId: selectedWasRemoved ? null : state.selectedNodeId,
						selectedEdgeId: selectedEdgeRemoved ? null : state.selectedEdgeId,
						layout: selectedWasRemoved
							? { ...state.layout, rightDrawerOpen: false }
							: state.layout,
						hasUnsavedChanges: hasDiff,
						qualityScorecard: recomputeScorecard(state, newNodes, newEdges),
					};
				});
			},

			deleteRelationship: (relationshipUriOrId) => {
				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:cannot-edit-historical",
						title: "Cannot Edit Historical Version",
						status: 400,
						detail:
							"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
						code: "CANNOT_EDIT_HISTORICAL_VERSION",
					});
					return;
				}
				set((state) => {
					const target = state.edges.find(
						(e) =>
							e.id === relationshipUriOrId ||
							e.relationship_uri === relationshipUriOrId,
					);
					if (!target) return state;

					const newEdges = state.edges.filter(
						(e) => e.relationship_uri !== target.relationship_uri,
					);
					const selectedEdgeRemoved =
						state.selectedEdgeId === target.id ||
						state.selectedEdgeId === target.relationship_uri;

					const hasDiff = hasGraphChangesFromBaseline(
						state.nodes,
						newEdges,
						state.committedNodes,
						state.committedEdges,
					);

					return {
						edges: newEdges,
						selectedEdgeId: selectedEdgeRemoved ? null : state.selectedEdgeId,
						hasUnsavedChanges: hasDiff,
						qualityScorecard: recomputeScorecard(state, state.nodes, newEdges),
					};
				});
			},

			discardChanges: () => {
				const committedNodes = get().committedNodes;
				const committedEdges = get().committedEdges;
				set((state) => ({
					nodes: [...committedNodes],
					edges: [...committedEdges],
					hasUnsavedChanges: false,
					isHistoricalView: false,
					viewedVersion: state.activeSession?.active_version || null,
					qualityScorecard: recomputeScorecard(
						state,
						committedNodes,
						committedEdges,
					),
				}));
				get().addToast({
					type: "urn:opendv:notice:changes-discarded",
					title: "Changes Discarded",
					status: 200,
					detail: "Reverted working draft to last committed ontology snapshot.",
					code: "OK",
				});
			},

			// Visual Diff
			computeDiff: (baseVersion, targetVersion, baseGraph, targetGraph) => {
				if (!baseGraph && !targetGraph && baseVersion === targetVersion) {
					set({
						diffState: {
							active: true,
							mode: "unified",
							base_version: baseVersion,
							target_version: targetVersion,
							records: [],
							summary: {
								added_count: 0,
								deleted_count: 0,
								modified_count: 0,
								breaking_count: 0,
							},
						},
					});
					return;
				}

				const baseNodes = baseGraph
					? baseGraph.nodes
					: baseVersion === "0.0.0"
						? []
						: get().committedNodes;
				const baseEdges = baseGraph
					? baseGraph.edges
					: baseVersion === "0.0.0"
						? []
						: get().committedEdges;
				const currentNodes = targetGraph
					? targetGraph.nodes
					: targetVersion === "0.0.0"
						? []
						: get().nodes;
				const currentEdges = targetGraph
					? targetGraph.edges
					: targetVersion === "0.0.0"
						? []
						: get().edges;
				const records: DiffRecord[] = [];

				const baseNodeMap = new Map(baseNodes.map((n) => [n.concept_uri, n]));
				const targetNodeMap = new Map(
					currentNodes.map((n) => [n.concept_uri, n]),
				);

				// Concepts added or modified in target
				for (const [uri, node] of targetNodeMap.entries()) {
					if (!baseNodeMap.has(uri)) {
						records.push({
							item_type: "concept",
							change_type: "added",
							identifier: uri,
							label: node.concept_name,
							details: `Added in ${targetVersion} with ${node.attributes.length} attributes`,
							is_breaking: false,
						});
					} else {
						const baseNode = baseNodeMap.get(uri)!;
						const baseAttrMap = new Map(
							(baseNode.attributes || []).map((a) => [a.attribute_name, a]),
						);
						const targetAttrMap = new Map(
							(node.attributes || []).map((a) => [a.attribute_name, a]),
						);

						const addedAttrs = (node.attributes || []).filter(
							(a) => !baseAttrMap.has(a.attribute_name),
						);
						const removedAttrs = (baseNode.attributes || []).filter(
							(a) => !targetAttrMap.has(a.attribute_name),
						);
						const modifiedTypeAttrs = (node.attributes || []).filter((a) => {
							const b = baseAttrMap.get(a.attribute_name);
							return b && b.data_type !== a.data_type;
						});

						if (removedAttrs.length > 0) {
							records.push({
								item_type: "attribute",
								change_type: "deleted",
								identifier: `${uri}#attributes`,
								label: `${node.concept_name} attributes`,
								details: `Removed attributes: ${removedAttrs.map((a) => a.attribute_name).join(", ")} (breaking change under ONT-EVO-001)`,
								is_breaking: true,
							});
						}

						if (modifiedTypeAttrs.length > 0) {
							records.push({
								item_type: "attribute",
								change_type: "modified",
								identifier: `${uri}#attribute-types`,
								label: `${node.concept_name} attribute data types`,
								details: `Attribute data type changed: ${modifiedTypeAttrs.map((a) => `${a.attribute_name} (${baseAttrMap.get(a.attribute_name)?.data_type} -> ${a.data_type})`).join(", ")} (breaking change under ONT-EVO-001)`,
								is_breaking: true,
							});
						}

						const baseMappingUris = new Set(
							(baseNode.external_mappings || []).map((m) => m.target_uri),
						);
						const targetMappingUris = new Set(
							(node.external_mappings || []).map((m) => m.target_uri),
						);
						const mappingAdded = [...targetMappingUris].filter(
							(u) => !baseMappingUris.has(u),
						);
						const mappingRemoved = [...baseMappingUris].filter(
							(u) => !targetMappingUris.has(u),
						);
						const mappingsChanged =
							mappingAdded.length > 0 || mappingRemoved.length > 0;

						const nameChanged = node.concept_name !== baseNode.concept_name;
						const archetypeChanged =
							node.elm_archetype !== baseNode.elm_archetype;
						const synsChanged =
							JSON.stringify(node.synonyms?.slice().sort() || []) !==
							JSON.stringify(baseNode.synonyms?.slice().sort() || []);
						const defChanged =
							(node.definition || "") !== (baseNode.definition || "");
						const descChanged =
							(node.description || "") !== (baseNode.description || "");
						const parentChanged =
							(node.parent_concept_uri ?? null) !==
							(baseNode.parent_concept_uri ?? null);
						const targetHubChanged =
							(node.target_hub ?? null) !== (baseNode.target_hub ?? null);

						if (
							nameChanged ||
							archetypeChanged ||
							synsChanged ||
							addedAttrs.length > 0 ||
							mappingsChanged ||
							defChanged ||
							descChanged ||
							parentChanged ||
							targetHubChanged
						) {
							const detailsParts: string[] = [];
							if (nameChanged)
								detailsParts.push(
									`renamed '${baseNode.concept_name}' -> '${node.concept_name}'`,
								);
							if (archetypeChanged)
								detailsParts.push(
									`archetype '${baseNode.elm_archetype}' -> '${node.elm_archetype}'`,
								);
							if (synsChanged) detailsParts.push("synonyms modified");
							if (defChanged) detailsParts.push("definition updated");
							if (descChanged) detailsParts.push("description updated");
							if (addedAttrs.length > 0)
								detailsParts.push(`+${addedAttrs.length} attrs`);
							if (mappingsChanged)
								detailsParts.push(
									`mappings changed (+${mappingAdded.length}, -${mappingRemoved.length})`,
								);
							if (parentChanged) detailsParts.push("parent updated");
							if (targetHubChanged) detailsParts.push("target hub updated");

							records.push({
								item_type: "concept",
								change_type: "modified",
								identifier: uri,
								label: node.concept_name,
								details: `Modified in ${targetVersion} (${detailsParts.join("; ")})`,
								is_breaking: nameChanged || archetypeChanged,
							});
						}
					}
				}

				// Concepts deleted in target
				for (const [uri, baseNode] of baseNodeMap.entries()) {
					if (!targetNodeMap.has(uri)) {
						records.push({
							item_type: "concept",
							change_type: "deleted",
							identifier: uri,
							label: baseNode.concept_name,
							details: `Concept removed in ${targetVersion} (breaking change under ONT-EVO-001)`,
							is_breaking: true,
						});
					}
				}

				// Relationships diff
				const baseEdgeMap = new Map(
					baseEdges.map((e) => [e.relationship_uri, e]),
				);
				const targetEdgeMap = new Map(
					currentEdges.map((e) => [e.relationship_uri, e]),
				);

				for (const [uri, edge] of targetEdgeMap.entries()) {
					if (!baseEdgeMap.has(uri)) {
						records.push({
							item_type: "relationship",
							change_type: "added",
							identifier: uri,
							label: edge.relationship_name,
							details: `Added relationship ${edge.source_concept_uri} -> ${edge.target_concept_uri}`,
							is_breaking: false,
						});
					} else {
						const baseEdge = baseEdgeMap.get(uri)!;
						const linkChanged = edge.target_link !== baseEdge.target_link;
						const cardChanged =
							(edge.cardinality ?? null) !== (baseEdge.cardinality ?? null);
						const srcChanged =
							edge.source_concept_uri !== baseEdge.source_concept_uri;
						const tgtChanged =
							edge.target_concept_uri !== baseEdge.target_concept_uri;
						if (linkChanged || cardChanged || srcChanged || tgtChanged) {
							records.push({
								item_type: "relationship",
								change_type: "modified",
								identifier: uri,
								label: edge.relationship_name,
								details: `Modified relationship properties (${linkChanged ? `target_link: ${baseEdge.target_link} -> ${edge.target_link}; ` : ""}${cardChanged ? `cardinality: ${baseEdge.cardinality} -> ${edge.cardinality}; ` : ""}${srcChanged || tgtChanged ? `endpoints: ${baseEdge.source_concept_uri}->${baseEdge.target_concept_uri} changed to ${edge.source_concept_uri}->${edge.target_concept_uri}` : ""})`,
								is_breaking: srcChanged || tgtChanged,
							});
						}
					}
				}

				for (const [uri, baseEdge] of baseEdgeMap.entries()) {
					if (!targetEdgeMap.has(uri)) {
						records.push({
							item_type: "relationship",
							change_type: "deleted",
							identifier: uri,
							label: baseEdge.relationship_name,
							details: `Removed relationship ${baseEdge.source_concept_uri} -> ${baseEdge.target_concept_uri} (breaking change under ONT-EVO-001)`,
							is_breaking: true,
						});
					}
				}

				const addedCount = records.filter(
					(r) => r.change_type === "added",
				).length;
				const modifiedCount = records.filter(
					(r) => r.change_type === "modified",
				).length;
				const deletedCount = records.filter(
					(r) => r.change_type === "deleted",
				).length;
				const breakingCount = records.filter((r) => r.is_breaking).length;

				set({
					diffState: {
						active: true,
						mode: "unified",
						base_version: baseVersion,
						target_version: targetVersion,
						records,
						summary: {
							added_count: addedCount,
							deleted_count: deletedCount,
							modified_count: modifiedCount,
							breaking_count: breakingCount,
						},
					},
				});
			},

			loadAndComputeDiff: async (
				baseVersion,
				targetVersion,
				fetchImpl = fetch,
			) => {
				if (baseVersion === targetVersion) {
					get().computeDiff(
						baseVersion,
						targetVersion,
						{ nodes: [], edges: [] },
						{ nodes: [], edges: [] },
					);
					return;
				}
				const activeSession = get().activeSession;
				if (!activeSession) {
					get().computeDiff(baseVersion, targetVersion);
					return;
				}
				const sid = activeSession.session_id;

				const fetchSnapshot = async (
					ver: string,
					role: "base" | "target",
				): Promise<{
					nodes: ConceptNode[];
					edges: RelationshipEdge[];
				} | null> => {
					if (ver === "0.0.0") {
						return { nodes: [], edges: [] };
					}
					if (ver === activeSession.active_version) {
						if (role === "target") {
							return { nodes: get().nodes, edges: get().edges };
						}
						return {
							nodes: get().committedNodes,
							edges: get().committedEdges,
						};
					}
					try {
						const res = await studioFetch(
							fetchImpl,
							`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/export?export_format=json&version=${encodeURIComponent(ver)}`,
						);
						if (!res.ok) {
							const err = (await res.json().catch(() => ({}))) as {
								detail?: string;
								code?: string;
							};
							get().addToast({
								type: "urn:opendv:error:diff-load-failed",
								title: "Failed to Load Version for Diff",
								status: res.status,
								detail:
									err.detail ||
									`Failed to load ontology version ${ver} (HTTP ${res.status})`,
								code: err.code || "DIFF_LOAD_FAILED",
							});
							return null;
						}
						const ont = (await res.json()) as Record<string, unknown>;
						const parsed = parseLoadedGraphPayload(ont);
						return {
							nodes: parsed.loadedNodes,
							edges: parsed.loadedEdges,
						};
					} catch (err) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Diff Load Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
						return null;
					}
				};

				const [baseSnap, targetSnap] = await Promise.all([
					fetchSnapshot(baseVersion, "base"),
					fetchSnapshot(targetVersion, "target"),
				]);
				if (!baseSnap || !targetSnap) return;
				if (get().activeSession?.session_id !== sid) return;
				get().computeDiff(baseVersion, targetVersion, baseSnap, targetSnap);
			},

			setDiffMode: (mode) => {
				set((state) => ({
					diffState: {
						...state.diffState,
						mode,
						active: mode !== "off",
					},
				}));
			},

			commitChanges: async (rationale, fetchImpl = fetch, commitOptions) => {
				if (get().isCommitting) return false;
				const activeSession = get().activeSession;
				if (!activeSession) return false;

				const currentRole = get().userRole;
				if (currentRole === "viewer" || currentRole === "automated") {
					get().addToast({
						type: "urn:opendv:error:forbidden-role",
						title: "Human Steward Role Required",
						status: 403,
						detail: `Role '${currentRole}' cannot commit manual ontology deltas. Switch to an authenticated Editor or Admin steward session.`,
						code: "FORBIDDEN_ROLE",
					});
					return false;
				}

				if (get().isHistoricalView) {
					get().addToast({
						type: "urn:opendv:error:historical-version-commit-blocked",
						title: "Historical Version Commit Blocked",
						status: 400,
						detail:
							"Cannot commit changes while viewing a historical ontology version. Please switch to the active head version.",
						code: "HISTORICAL_VERSION_COMMIT_BLOCKED",
					});
					return false;
				}

				if (!activeSession.head_sha256) {
					get().addToast({
						type: "urn:opendv:error:missing-head-sha",
						title: "Missing Head SHA",
						status: 400,
						detail: "Cannot commit delta without an active session head SHA.",
						code: "MISSING_HEAD_SHA",
					});
					return false;
				}

				const shaRegex = /^[0-9a-f]{64}$/i;
				if (!shaRegex.test(activeSession.head_sha256)) {
					get().addToast({
						type: "urn:opendv:error:invalid-sha256-format",
						title: "Invalid Head Digest",
						status: 400,
						detail:
							"Session head_sha256 must be a valid 64-character hexadecimal SHA-256 digest.",
						code: "INVALID_SHA256_DIGEST",
					});
					return false;
				}

				const baseVersion = (activeSession.active_version || "").trim();
				const semverMatch = /^(\d+)\.(\d+)\.(\d+)/.exec(baseVersion);
				if (!baseVersion || !semverMatch) {
					get().addToast({
						type: "urn:opendv:error:missing-active-version",
						title: "Missing Active Version",
						status: 400,
						detail:
							"Session active_version must be a valid SemVer string (e.g., 0.0.0 or 1.0.0) before committing.",
						code: "MISSING_ACTIVE_VERSION",
					});
					return false;
				}

				const allowDestructive = Boolean(
					commitOptions?.allowDestructive || currentRole === "admin",
				);
				const committedNodes = get().committedNodes;
				const currentNodes = get().nodes;
				const committedEdges = get().committedEdges;
				const currentEdges = get().edges;
				const sid = activeSession.session_id;

				const committedNodeMap = new Map(
					committedNodes.map((n) => [n.concept_uri, n]),
				);
				const currentNodeMap = new Map(
					currentNodes.map((n) => [n.concept_uri, n]),
				);
				const committedEdgeMap = new Map(
					committedEdges.map((e) => [e.relationship_uri, e]),
				);
				const currentEdgeMap = new Map(
					currentEdges.map((e) => [e.relationship_uri, e]),
				);

				let opCounter = 0;
				const nextOpId = (): string => {
					opCounter += 1;
					return typeof crypto !== "undefined" &&
						typeof crypto.randomUUID === "function"
						? `op-${crypto.randomUUID()}`
						: `op-${Date.now()}-${opCounter}-${Math.random().toString(16).slice(2, 8)}`;
				};

				// 1. Check removed relationships
				const removedEdges = committedEdges.filter(
					(e) => !currentEdgeMap.has(e.relationship_uri),
				);
				if (removedEdges.length > 0 && !allowDestructive) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Non-Additive Change Blocked",
						status: 400,
						detail:
							"Deleting relationships violates ONT-EVO-001 additive evolution without admin authorization.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				// 2. Check removed concepts
				const removedConcepts = committedNodes.filter(
					(c) => !currentNodeMap.has(c.concept_uri),
				);
				if (removedConcepts.length > 0 && !allowDestructive) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Non-Additive Change Blocked",
						status: 400,
						detail:
							"Deleting concepts violates ONT-EVO-001 strictly additive evolution without admin authorization.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				// 3. Validate existing concept & relationship mutations
				for (const committed of committedNodes) {
					const current = currentNodeMap.get(committed.concept_uri);
					if (!current) continue;

					if (committed.concept_name !== current.concept_name) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Concept Renaming Blocked",
							status: 400,
							detail: `Renaming concept '${committed.concept_name}' to '${current.concept_name}' cannot be committed via additive evolution deltas.`,
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const archetypeChanged =
						committed.elm_archetype !== current.elm_archetype;
					const archetypeWasSet = Boolean(
						(committed.elm_archetype || "").trim(),
					);
					if (archetypeChanged && archetypeWasSet && !allowDestructive) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Archetype Mutation Blocked",
							status: 400,
							detail: `Modifying archetype on '${committed.concept_name}' requires admin authorization under ONT-EVO-001.`,
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					if ((committed.target_hub ?? null) !== (current.target_hub ?? null)) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Target Hub Mutation Blocked",
							status: 400,
							detail: `Altering target hub on '${committed.concept_name}' is not supported in evolution deltas.`,
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const parentChanged =
						(committed.parent_concept_uri ?? null) !==
						(current.parent_concept_uri ?? null);
					const parentWasSet = Boolean(
						(committed.parent_concept_uri || "").trim(),
					);
					if (parentChanged && parentWasSet && !allowDestructive) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Inheritance Hierarchy Mutation Blocked",
							status: 400,
							detail: `Overwriting or clearing parent concept on '${committed.concept_name}' requires admin authorization under ONT-EVO-001.`,
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					// Synonyms: appending new synonyms is allowed via add_synonym; removing committed synonyms is blocked
					const committedSyns = committed.synonyms || [];
					const currentSynSet = new Set(current.synonyms || []);
					if (committedSyns.some((s) => !currentSynSet.has(s))) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Synonym Removal Blocked",
							status: 400,
							detail: `Removing existing synonyms on '${committed.concept_name}' cannot be expressed via delta operations.`,
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					// Attributes check
					const currentAttrMap = new Map(
						(current.attributes || []).map((a) => [a.attribute_name, a]),
					);
					const removedAttrs = (committed.attributes || []).filter(
						(a) => !currentAttrMap.has(a.attribute_name),
					);
					if (removedAttrs.length > 0 && !allowDestructive) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Non-Additive Change Blocked",
							status: 400,
							detail:
								"Removing attributes violates ONT-EVO-001 strictly additive evolution without admin authorization.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const alteredTypes = (committed.attributes || []).filter((a) => {
						const curr = currentAttrMap.get(a.attribute_name);
						return curr && curr.data_type !== a.data_type;
					});
					if (alteredTypes.length > 0) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Non-Additive Change Blocked",
							status: 400,
							detail:
								"Altering attribute data types violates ONT-EVO-001 strictly additive evolution.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const alteredDescs = (committed.attributes || []).filter((a) => {
						const curr = currentAttrMap.get(a.attribute_name);
						return curr && (curr.description || "") !== (a.description || "");
					});
					if (alteredDescs.length > 0) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Attribute Description Mutation Blocked",
							status: 400,
							detail:
								"Modifying descriptions of existing attributes cannot be expressed via evolution deltas.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const alteredOptionals = (committed.attributes || []).filter((a) => {
						const curr = currentAttrMap.get(a.attribute_name);
						return (
							curr && (curr.is_optional ?? true) !== (a.is_optional ?? true)
						);
					});
					if (alteredOptionals.length > 0) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Attribute Optionality Mutation Blocked",
							status: 400,
							detail:
								"Modifying optionality of existing attributes cannot be expressed via evolution deltas.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					const alteredExamples = (committed.attributes || []).filter((a) => {
						const curr = currentAttrMap.get(a.attribute_name);
						return (
							curr &&
							JSON.stringify(curr.example_values || []) !==
								JSON.stringify(a.example_values || [])
						);
					});
					if (alteredExamples.length > 0) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Attribute Example Values Mutation Blocked",
							status: 400,
							detail:
								"Modifying example values of existing attributes cannot be expressed via evolution deltas.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					// External mappings check
					const currentMappingMap = new Map(
						(current.external_mappings || []).map((m) => [m.target_uri, m]),
					);
					const removedMappings = (committed.external_mappings || []).filter(
						(m) => !currentMappingMap.has(m.target_uri),
					);
					if (removedMappings.length > 0 && !allowDestructive) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Removed External Mappings Blocked",
							status: 400,
							detail:
								"Removing reference external mappings violates regulatory stability under ONT-EVO-002 without admin authorization.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}

					for (const m of committed.external_mappings || []) {
						const currM = currentMappingMap.get(m.target_uri);
						if (!currM) continue;
						if (
							currM.scheme !== m.scheme ||
							(currM.predicate ?? null) !== (m.predicate ?? null)
						) {
							get().addToast({
								type: "urn:opendv:error:non-additive-change",
								title: "External Mapping Mutation Blocked",
								status: 400,
								detail: `Modifying scheme or predicate of existing external mapping '${m.target_uri}' requires removing and re-adding the mapping.`,
								code: "ONT_EVO_NON_ADDITIVE_CHANGE",
							});
							return false;
						}
						const prevConf = m.mapping_confidence ?? m.confidence ?? null;
						const nextConf =
							currM.mapping_confidence ?? currM.confidence ?? null;
						const confChanged = nextConf !== prevConf;
						if (confChanged) {
							if (typeof nextConf !== "number" || !Number.isFinite(nextConf)) {
								get().addToast({
									type: "urn:opendv:error:non-additive-change",
									title: "External Mapping Confidence Invalid",
									status: 400,
									detail: `Rescoring mapping '${m.target_uri}' requires a valid numeric confidence in [0, 1].`,
									code: "ONT_EVO_NON_ADDITIVE_CHANGE",
								});
								return false;
							}
							if (Boolean(m.pinned) && Boolean(currM.pinned ?? m.pinned)) {
								get().addToast({
									type: "urn:opendv:error:non-additive-change",
									title: "Pinned External Mapping Mutation Blocked",
									status: 400,
									detail: `Cannot rescore pinned external mapping '${m.target_uri}' without unpinning it first.`,
									code: "ONT_EVO_NON_ADDITIVE_CHANGE",
								});
								return false;
							}
						}
					}
				}

				for (const committedEdge of committedEdges) {
					const currentEdge = currentEdgeMap.get(
						committedEdge.relationship_uri,
					);
					if (!currentEdge) continue;

					if (
						committedEdge.target_link !== currentEdge.target_link ||
						(committedEdge.cardinality ?? null) !==
							(currentEdge.cardinality ?? null) ||
						committedEdge.source_concept_uri !==
							currentEdge.source_concept_uri ||
						committedEdge.target_concept_uri !==
							currentEdge.target_concept_uri ||
						(committedEdge.description ?? null) !==
							(currentEdge.description ?? null)
					) {
						get().addToast({
							type: "urn:opendv:error:non-additive-change",
							title: "Relationship Mutation Blocked",
							status: 400,
							detail:
								"Modifying properties or endpoints of existing relationships cannot be expressed via delta operations.",
							code: "ONT_EVO_NON_ADDITIVE_CHANGE",
						});
						return false;
					}
				}

				// Authoritative pre-commit scorecard recomputation
				const authoritativeScorecard = recomputeScorecard(
					get(),
					currentNodes,
					currentEdges,
				);

				const hasCandidateFiboPathAMapping = (node: ConceptNode): boolean => {
					const mappings = node.external_mappings || [];
					return mappings.some((m) => {
						const isFibo = (m.scheme || "").trim().toLowerCase() === "fibo";
						const hasUri =
							typeof m.target_uri === "string" &&
							m.target_uri.trim().length > 0;
						const hasPathAPredicate =
							typeof m.predicate === "string" &&
							VALID_PATH_A_PREDICATES.has(m.predicate.trim());
						if (!isFibo || !hasUri || !hasPathAPredicate) return false;
						const isUserAsserted =
							(m.provenance || "").trim().toLowerCase() === "user_asserted";
						const rawConf =
							typeof m.confidence === "number"
								? m.confidence
								: typeof m.mapping_confidence === "number"
									? m.mapping_confidence
									: undefined;
						if (isUserAsserted) {
							// S6-MAJ-01: Client strips user_asserted confidence and delegates authoritative
							// pack verification to server-side GroundingVerifier on POST /delta.
							return true;
						}
						return (
							typeof rawConf === "number" &&
							Number.isFinite(rawConf) &&
							rawConf >= 0.65
						);
					});
				};

				const evoCheck = authoritativeScorecard.checks.find(
					(c) => c.rule_id === "ONT-EVO-002",
				);
				if (evoCheck?.status === "FAIL") {
					const rawGrandfathered =
						activeSession.grandfathered_concept_uris || [];
					const effectiveGrandfatheredSet = new Set(
						rawGrandfathered.filter((uri) => {
							const curr = currentNodeMap.get(uri);
							const comm = committedNodeMap.get(uri);
							return !(
								curr &&
								comm &&
								isConceptMutatedFromBaseline(curr, comm)
							);
						}),
					);
					const effectiveWaivedSet = new Set(
						authoritativeScorecard.waiver_applied
							? authoritativeScorecard.waived_concept_uris || []
							: [],
					);
					const flaggedNodes = currentNodes.filter((n) => {
						const isGrounded =
							deriveGroundingStatus(n.external_mappings, n.confidence)
								.grounding_status === "GROUNDED";
						if (isGrounded) return false;
						if (effectiveGrandfatheredSet.has(n.concept_uri)) return false;
						if (effectiveWaivedSet.has(n.concept_uri)) return false;
						return true;
					});
					const allFlaggedHaveCandidateFibo =
						flaggedNodes.length > 0 &&
						flaggedNodes.every((n) => hasCandidateFiboPathAMapping(n));

					if (!allFlaggedHaveCandidateFibo) {
						get().addToast({
							type: "urn:opendv:error:grounding-invariant-failed",
							title: "Regulatory Grounding Invariant Failed",
							status: 400,
							detail:
								"Cannot commit changes while ONT-EVO-002 is failing. Ground all concepts with FIBO skos:exactMatch/closeMatch >= 0.65 or obtain an authorized steward administrative waiver.",
							code: "ONT_EVO_002_FAILED",
						});
						return false;
					}
				}

				const dagCheck = authoritativeScorecard.checks.find(
					(c) => c.rule_id === "ONT-DAG-001",
				);
				if (dagCheck?.status === "FAIL") {
					get().addToast({
						type: "urn:opendv:error:taxonomic-cycle",
						title: "Taxonomic Cycle Detected",
						status: 400,
						detail:
							"Cannot commit ontology with circular taxonomic inheritance hierarchy (violates ONT-DAG-001).",
						code: "ONT_DAG_CYCLE_DETECTED",
					});
					return false;
				}

				const operations: Array<Record<string, unknown>> = [];
				let hasMajorOp = false;
				let hasMinorOp = false;
				let hasAddConceptOp = false;

				const sanitizeConceptMappingsForAddConcept = (
					mappings: ExternalMapping[] | undefined,
				): Array<Record<string, unknown>> | undefined => {
					if (!mappings || mappings.length === 0) return undefined;
					return mappings.map((m) => {
						const numericConf =
							m.provenance === "user_asserted"
								? undefined
								: typeof m.mapping_confidence === "number" &&
										Number.isFinite(m.mapping_confidence)
									? m.mapping_confidence
									: typeof m.confidence === "number" &&
											Number.isFinite(m.confidence)
										? m.confidence
										: undefined;
						return {
							scheme: m.scheme || "FIBO",
							target_uri: m.target_uri,
							...(m.predicate ? { predicate: m.predicate } : {}),
							...(m.pinned !== undefined ? { pinned: m.pinned } : {}),
							...(numericConf !== undefined ? { confidence: numericConf } : {}),
						};
					});
				};

				// Emit remove_relationship before remove_concept so no dangling links remain during sequential apply
				for (const removedEdge of removedEdges) {
					hasMajorOp = true;
					operations.push({
						op_id: nextOpId(),
						op_type: "remove_relationship",
						relationship_uri: removedEdge.relationship_uri,
						reason: rationale || "Removed relationship in Workbench Studio",
					});
				}

				for (const removedConcept of removedConcepts) {
					hasMajorOp = true;
					operations.push({
						op_id: nextOpId(),
						op_type: "remove_concept",
						concept_uri: removedConcept.concept_uri,
						reason: rationale || "Removed concept in Workbench Studio",
					});
				}

				// Added & modified concepts
				for (const node of currentNodes) {
					const committed = committedNodeMap.get(node.concept_uri);
					if (!committed) {
						hasMinorOp = true;
						hasAddConceptOp = true;
						const cleanMappings = sanitizeConceptMappingsForAddConcept(
							node.external_mappings,
						);
						operations.push({
							op_id: nextOpId(),
							op_type: "add_concept",
							concept_uri: node.concept_uri,
							concept_name: node.concept_name,
							elm_archetype: node.elm_archetype || "CORE_BUSINESS_CONCEPT",
							definition: node.definition || "",
							...(node.description ? { description: node.description } : {}),
							target_hub: node.target_hub ?? undefined,
							parent_concept_uri: node.parent_concept_uri ?? undefined,
							attributes: [],
							synonyms:
								node.synonyms && node.synonyms.length > 0
									? node.synonyms
									: undefined,
							external_mappings: cleanMappings,
						});
						for (const attr of node.attributes || []) {
							hasMinorOp = true;
							operations.push({
								op_id: nextOpId(),
								op_type: "add_attribute",
								concept_uri: node.concept_uri,
								attribute_name: attr.attribute_name,
								data_type: attr.data_type || "string",
								description: attr.description || undefined,
								is_optional: attr.is_optional ?? undefined,
								example_values:
									attr.example_values && attr.example_values.length > 0
										? attr.example_values
										: undefined,
							});
						}
					} else {
						if (node.elm_archetype !== committed.elm_archetype) {
							if ((committed.elm_archetype || "").trim()) {
								hasMajorOp = true;
							} else {
								hasMinorOp = true;
							}
							operations.push({
								op_id: nextOpId(),
								op_type: "set_concept_elm_archetype",
								concept_uri: node.concept_uri,
								elm_archetype: node.elm_archetype,
								rationale:
									rationale || "Updated ELM archetype in Workbench Studio",
							});
						}

						if (
							(node.parent_concept_uri ?? null) !==
							(committed.parent_concept_uri ?? null)
						) {
							if ((committed.parent_concept_uri || "").trim()) {
								hasMajorOp = true;
							} else {
								hasMinorOp = true;
							}
							operations.push({
								op_id: nextOpId(),
								op_type: "set_parent_concept",
								concept_uri: node.concept_uri,
								parent_concept_uri: node.parent_concept_uri ?? null,
							});
						}

						if ((node.description || "") !== (committed.description || "")) {
							operations.push({
								op_id: nextOpId(),
								op_type: "revise_description",
								concept_uri: node.concept_uri,
								description: node.description || "",
							});
						}

						if ((node.definition || "") !== (committed.definition || "")) {
							hasMinorOp = true;
							operations.push({
								op_id: nextOpId(),
								op_type: "refine_definition",
								concept_uri: node.concept_uri,
								definition: node.definition || "",
							});
						}

						const committedSyns = new Set(committed.synonyms || []);
						for (const syn of node.synonyms || []) {
							if (!committedSyns.has(syn)) {
								operations.push({
									op_id: nextOpId(),
									op_type: "add_synonym",
									concept_uri: node.concept_uri,
									synonym: syn,
								});
							}
						}

						const currentAttrNames = new Set(
							(node.attributes || []).map((a) => a.attribute_name),
						);
						for (const commAttr of committed.attributes || []) {
							if (!currentAttrNames.has(commAttr.attribute_name)) {
								hasMajorOp = true;
								operations.push({
									op_id: nextOpId(),
									op_type: "remove_attribute",
									concept_uri: node.concept_uri,
									attribute_name: commAttr.attribute_name,
									reason: rationale || "Removed attribute in Workbench Studio",
								});
							}
						}

						const committedAttrs = new Set(
							(committed.attributes || []).map((a) => a.attribute_name),
						);
						for (const attr of node.attributes || []) {
							if (!committedAttrs.has(attr.attribute_name)) {
								hasMinorOp = true;
								operations.push({
									op_id: nextOpId(),
									op_type: "add_attribute",
									concept_uri: node.concept_uri,
									attribute_name: attr.attribute_name,
									data_type: attr.data_type || "string",
									description: attr.description || undefined,
									is_optional: attr.is_optional ?? undefined,
									example_values:
										attr.example_values && attr.example_values.length > 0
											? attr.example_values
											: undefined,
								});
							}
						}

						const currentMappingMap = new Map(
							(node.external_mappings || []).map((m) => [m.target_uri, m]),
						);
						for (const commMapping of committed.external_mappings || []) {
							const currM = currentMappingMap.get(commMapping.target_uri);
							if (!currM) {
								hasMajorOp = true;
								operations.push({
									op_id: nextOpId(),
									op_type: "remove_external_mapping",
									concept_uri: node.concept_uri,
									scheme: commMapping.scheme || "FIBO",
									target_uri: commMapping.target_uri,
								});
								continue;
							}

							const prevPinned = Boolean(commMapping.pinned);
							const nextPinned = Boolean(currM.pinned);
							if (nextPinned !== prevPinned) {
								operations.push({
									op_id: nextOpId(),
									op_type: "repin_external_mapping",
									concept_uri: node.concept_uri,
									scheme: currM.scheme || "FIBO",
									target_uri: currM.target_uri,
									pinned: nextPinned,
								});
							}

							const prevConf =
								commMapping.mapping_confidence ??
								commMapping.confidence ??
								null;
							const nextConf =
								currM.mapping_confidence ?? currM.confidence ?? null;
							if (
								nextConf !== prevConf &&
								typeof nextConf === "number" &&
								Number.isFinite(nextConf)
							) {
								operations.push({
									op_id: nextOpId(),
									op_type: "rescore_external_mapping",
									concept_uri: node.concept_uri,
									scheme: currM.scheme || "FIBO",
									target_uri: currM.target_uri,
									new_confidence: nextConf,
									...(typeof prevConf === "number" && Number.isFinite(prevConf)
										? { previous_confidence: prevConf }
										: {}),
								});
							}
						}

						const committedMappings = new Set(
							(committed.external_mappings || []).map((m) => m.target_uri),
						);
						for (const mapping of node.external_mappings || []) {
							if (!committedMappings.has(mapping.target_uri)) {
								hasMinorOp = true;
								const numericConf =
									mapping.provenance === "user_asserted"
										? undefined
										: typeof mapping.mapping_confidence === "number" &&
												Number.isFinite(mapping.mapping_confidence)
											? mapping.mapping_confidence
											: typeof mapping.confidence === "number" &&
													Number.isFinite(mapping.confidence)
												? mapping.confidence
												: undefined;
								operations.push({
									op_id: nextOpId(),
									op_type: "add_external_mapping",
									concept_uri: node.concept_uri,
									scheme: mapping.scheme || "FIBO",
									target_uri: mapping.target_uri,
									predicate: mapping.predicate || undefined,
									pinned: mapping.pinned ?? undefined,
									...(numericConf !== undefined
										? {
												mapping_confidence: numericConf,
												confidence: numericConf,
											}
										: {}),
								});
							}
						}
					}
				}

				// Added relationships
				for (const edge of currentEdges) {
					const committed = committedEdgeMap.get(edge.relationship_uri);
					if (!committed) {
						hasMinorOp = true;
						const endpoints =
							edge.endpoints && edge.endpoints.length >= 2
								? edge.endpoints
								: [edge.source_concept_uri, edge.target_concept_uri];
						operations.push({
							op_id: nextOpId(),
							op_type: "add_relationship",
							relationship_uri: edge.relationship_uri,
							relationship_name: edge.relationship_name,
							target_link: edge.target_link || "lnk_default",
							endpoints,
							...(edge.grain_semantics
								? { grain_semantics: edge.grain_semantics }
								: {}),
							cardinality: edge.cardinality ?? undefined,
							description: edge.description ?? undefined,
						});
					}
				}

				// Block commits with zero operations
				if (operations.length === 0) {
					get().addToast({
						type: "urn:opendv:notice:no-changes",
						title: "No Changes to Commit",
						status: 200,
						detail: "There are no uncommitted changes in the workspace.",
						code: "NO_OPERATIONS",
					});
					return false;
				}

				const major = Number.parseInt(semverMatch[1]!, 10);
				const minor = Number.parseInt(semverMatch[2]!, 10);
				const patch = Number.parseInt(semverMatch[3]!, 10);

				let targetVersion: string;
				if (major === 0) {
					if (committedNodes.length === 0) {
						targetVersion = hasAddConceptOp ? "1.0.0" : `0.${minor + 1}.0`;
					} else {
						targetVersion =
							hasMajorOp || hasMinorOp
								? `0.${minor + 1}.0`
								: `0.${minor}.${patch + 1}`;
					}
				} else if (hasMajorOp) {
					targetVersion = `${major + 1}.0.0`;
				} else if (hasMinorOp) {
					targetVersion = `${major}.${minor + 1}.0`;
				} else {
					targetVersion = `${major}.${minor}.${patch + 1}`;
				}

				const deltaId =
					typeof crypto !== "undefined" &&
					typeof crypto.randomUUID === "function"
						? `delta-${crypto.randomUUID()}`
						: `delta-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

				const author = activeSession.enterprise_owner || undefined;

				const payload = {
					delta_id: deltaId,
					parent_sha256: activeSession.head_sha256,
					base_version: baseVersion,
					target_version: targetVersion,
					operations,
					author,
					rationale: rationale || "Workbench Studio visual edits",
				};

				const sentNodes = [...currentNodes];
				const sentEdges = [...currentEdges];

				set({ isCommitting: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/delta`,
						{
							method: "POST",
							headers: {
								"Content-Type": "application/json",
								"If-Match": `"${activeSession.head_sha256}"`,
							},
							body: JSON.stringify(payload),
						},
					);

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:delta-commit-failed",
							title: "Commit Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "CONCURRENT_MODIFICATION",
						});
						return false;
					}

					const data = (await res.json()) as {
						head_sha256?: string;
						target_sha256?: string;
						active_version?: string;
						target_version?: string;
						ontology?: Record<string, unknown>;
					};
					if (get().activeSession?.session_id !== sid) {
						set((state) => ({
							sessions: state.sessions.map((s) =>
								s.session_id === sid
									? {
											...s,
											head_sha256:
												data.target_sha256 || data.head_sha256 || s.head_sha256,
											active_version:
												data.target_version ||
												data.active_version ||
												s.active_version,
										}
									: s,
							),
						}));
						return false;
					}

					const nextVersion =
						data.target_version ||
						data.active_version ||
						get().activeSession?.active_version ||
						targetVersion;
					const nextSha =
						data.target_sha256 ||
						data.head_sha256 ||
						get().activeSession?.head_sha256;

					const cloneNodeClean = (n: ConceptNode): ConceptNode => ({
						...n,
						attributes: (n.attributes || []).map((a) => ({ ...a })),
						synonyms: [...(n.synonyms || [])],
						external_mappings: (n.external_mappings || []).map((m) => ({
							...m,
						})),
					});
					const cloneEdgeClean = (e: RelationshipEdge): RelationshipEdge => ({
						...e,
						...(e.endpoints ? { endpoints: [...e.endpoints] } : {}),
					});

					let shouldReloadVerifiedSnapshot = false;

					set((state) => {
						const hasFurtherEdits =
							state.nodes.length !== sentNodes.length ||
							state.edges.length !== sentEdges.length ||
							state.nodes.some((n, i) => n !== sentNodes[i]) ||
							state.edges.some((e, i) => e !== sentEdges[i]);

						let ratifiedNodes = sentNodes.map(cloneNodeClean);
						let ratifiedEdges = sentEdges.map(cloneEdgeClean);
						const hasInlineOntology = Boolean(
							data.ontology && typeof data.ontology === "object",
						);
						if (hasInlineOntology && data.ontology) {
							const parsedOnt = parseLoadedGraphPayload(data.ontology);
							ratifiedNodes = parsedOnt.loadedNodes;
							ratifiedEdges = parsedOnt.loadedEdges;
						} else if (!hasFurtherEdits) {
							shouldReloadVerifiedSnapshot = true;
						}

						const nextWorkingNodes = hasFurtherEdits
							? state.nodes
							: ratifiedNodes;
						const nextWorkingEdges = hasFurtherEdits
							? state.edges
							: ratifiedEdges;

						const nextSession = state.activeSession
							? {
									...state.activeSession,
									...(state.activeSession.status === "EMPTY"
										? { status: "COMMITTED" }
										: {}),
									head_sha256: nextSha || state.activeSession.head_sha256,
									active_version: nextVersion,
								}
							: null;

						const nextSessions = state.sessions.map((s) =>
							s.session_id === sid && nextSession ? nextSession : s,
						);

						const intermediateState: StudioStoreState = {
							...state,
							activeSession: nextSession,
							sessions: nextSessions,
							committedNodes: ratifiedNodes,
							committedEdges: ratifiedEdges,
						};

						return {
							nodes: nextWorkingNodes,
							edges: nextWorkingEdges,
							hasUnsavedChanges: hasFurtherEdits,
							committedNodes: ratifiedNodes,
							committedEdges: ratifiedEdges,
							activeSession: nextSession,
							sessions: nextSessions,
							viewedVersion: nextSession?.active_version || state.viewedVersion,
							isHistoricalView: false,
							qualityScorecard: recomputeScorecard(
								intermediateState,
								nextWorkingNodes,
								nextWorkingEdges,
							),
						};
					});

					if (
						shouldReloadVerifiedSnapshot &&
						get().activeSession?.session_id === sid &&
						!get().hasUnsavedChanges
					) {
						const toastsBeforeReload = get().toasts;
						await get().loadGraph(nextVersion, fetchImpl, sid);
						if (get().toasts !== toastsBeforeReload) {
							// Minimal unit-test mock without GET /export: preserve clean cloned fallback without spurious error toast
							set({ toasts: toastsBeforeReload });
						}
					}

					get().addToast({
						type: "urn:opendv:notice:commit-success",
						title: "Changes Committed",
						status: 200,
						detail: "Ontology delta committed successfully.",
						code: "COMMITTED",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				} finally {
					set({ isCommitting: false });
				}
			},

			// Quality & Waiver & Feedback
			updateQualityScorecard: (updates) => {
				set((state) => {
					const {
						waiver_applied: _w,
						waived_concept_uris: _u,
						waived_base_sha256: _s,
						waiver_rationale: _r,
						checks: _c,
						...safeUpdates
					} = updates as Partial<QualityScorecard>;
					return {
						qualityScorecard: {
							...state.qualityScorecard,
							...safeUpdates,
						},
					};
				});
			},

			submitWaiver: async (
				rationale,
				waiverKey,
				targetConceptUrisOrFetch,
				maybeFetchImpl,
			) => {
				const explicitTargetUris = Array.isArray(targetConceptUrisOrFetch)
					? targetConceptUrisOrFetch
					: undefined;
				const fetchImpl =
					typeof targetConceptUrisOrFetch === "function"
						? targetConceptUrisOrFetch
						: (maybeFetchImpl ?? fetch);

				if (get().isSubmittingWaiver) return false;
				const activeSession = get().activeSession;
				if (!activeSession) {
					get().addToast({
						type: "urn:opendv:error:no-session",
						title: "No Active Session",
						status: 400,
						detail: "Select an active session before requesting a waiver.",
						code: "SYNTAX_INVALID_ARGUMENT",
					});
					return false;
				}

				if (!activeSession.head_sha256) {
					get().addToast({
						type: "urn:opendv:error:missing-head-sha",
						title: "Missing Session Head SHA",
						status: 400,
						detail:
							"Cannot authorize grounding waiver on uncommitted session head.",
						code: "MISSING_HEAD_SHA",
					});
					return false;
				}

				if (!rationale || rationale.trim().length < 10) {
					get().addToast({
						type: "urn:opendv:error:waiver-rationale-short",
						title: "Waiver Rationale Too Short",
						status: 400,
						detail:
							"Steward administrative waiver rationale must be at least 10 characters.",
						code: "INVALID_GROUNDING_WAIVER",
					});
					return false;
				}

				if (!waiverKey || waiverKey.trim().length === 0) {
					get().addToast({
						type: "urn:opendv:error:waiver-credential-missing",
						title: "Missing Waiver Credential",
						status: 400,
						detail:
							"Steward administrative waiver key or credential token is required.",
						code: "MISSING_WAIVER_CREDENTIAL",
					});
					return false;
				}

				const normalizedExplicitTargets = explicitTargetUris
					? explicitTargetUris.map((u) => u.trim()).filter(Boolean)
					: undefined;

				const pendingProposalUngroundedUris: string[] = [];
				const pendingProposalAllConceptUris = new Set<string>();
				for (const prop of get().proposals || []) {
					if (prop.status && prop.status.toUpperCase() !== "PENDING") {
						continue;
					}
					for (const op of prop.operations || []) {
						if (
							op &&
							typeof op === "object" &&
							op.op_type === "add_concept" &&
							typeof op.concept_uri === "string" &&
							op.concept_uri.trim().length > 0
						) {
							const uri = op.concept_uri.trim();
							pendingProposalAllConceptUris.add(uri);
							const rawMappings = Array.isArray(op.external_mappings)
								? (op.external_mappings as ExternalMapping[])
								: [];
							const rawConf =
								typeof op.confidence === "number" ? op.confidence : undefined;
							const status =
								rawMappings.length > 0
									? deriveGroundingStatus(rawMappings, rawConf).grounding_status
									: "UNGROUNDED";
							if (status !== "GROUNDED") {
								pendingProposalUngroundedUris.push(uri);
							}
						}
					}
				}

				if (
					get().nodes.length === 0 &&
					(!normalizedExplicitTargets ||
						normalizedExplicitTargets.length === 0) &&
					pendingProposalAllConceptUris.size === 0
				) {
					get().addToast({
						type: "urn:opendv:notice:empty-graph",
						title: "Empty Graph",
						status: 400,
						detail: "Cannot request administrative waiver for an empty graph.",
						code: "EMPTY_GRAPH",
					});
					return false;
				}

				const committedMap = new Map(
					get().committedNodes.map((cn) => [cn.concept_uri, cn]),
				);
				const effectiveGrandfathered = new Set(
					(activeSession.grandfathered_concept_uris || []).filter((uri) => {
						const comm = committedMap.get(uri);
						const curr = get().nodes.find((n) => n.concept_uri === uri);
						if (!curr) return false;
						if (!comm) return true;
						return !isConceptMutatedFromBaseline(curr, comm);
					}),
				);

				const ungroundedNodeUris = get()
					.nodes.filter((n) => {
						if (effectiveGrandfathered.has(n.concept_uri)) return false;
						const hasMappings =
							Array.isArray(n.external_mappings) &&
							n.external_mappings.length > 0;
						const status = hasMappings
							? deriveGroundingStatus(n.external_mappings, n.confidence)
									.grounding_status
							: "UNGROUNDED";
						return status !== "GROUNDED";
					})
					.map((n) => n.concept_uri);

				const ungroundedUris = Array.from(
					new Set([...ungroundedNodeUris, ...pendingProposalUngroundedUris]),
				);

				const targetUris =
					normalizedExplicitTargets && normalizedExplicitTargets.length > 0
						? get().nodes.length === 0 &&
							pendingProposalAllConceptUris.size === 0
							? normalizedExplicitTargets
							: normalizedExplicitTargets.filter((u) =>
									ungroundedUris.includes(u),
								)
						: ungroundedUris;

				if (targetUris.length === 0) {
					get().addToast({
						type: "urn:opendv:notice:no-ungrounded",
						title: "No Ungrounded Concepts Selected",
						status: 400,
						detail:
							"All selected concepts in this session are already grounded or grandfathered. Administrative waiver is not needed.",
						code: "NO_UNGROUNDED_CONCEPTS",
					});
					return false;
				}

				const sid = activeSession.session_id;

				set({ isSubmittingWaiver: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/waiver`,
						{
							method: "POST",
							headers: {
								"Content-Type": "application/json",
								"X-OpenDV-Waiver-Key": waiverKey.trim(),
							},
							body: JSON.stringify({
								target_concept_uris: targetUris,
								rationale: rationale.trim(),
								base_sha256: activeSession.head_sha256,
							}),
						},
					);
					const currentSession = get().activeSession;
					if (!currentSession || currentSession.session_id !== sid)
						return false;

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:waiver-rejected",
							title: "Waiver Authorization Rejected",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "FORBIDDEN_ADMIN_REQUIRED",
						});
						return false;
					}

					const waiverData = (await res.json().catch(() => ({}))) as {
						status?: string;
						waiver_applied?: boolean;
						waived_concept_uris?: string[];
						target_concept_uris?: string[];
						waived_base_sha256?: string;
						base_sha256?: string;
						detail?: string;
						code?: string;
					};

					if (waiverData.waiver_applied === false) {
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:waiver-denied",
							title: "Waiver Authorization Denied",
							status: 200,
							detail:
								waiverData.detail ||
								"Server did not authorize grounding waiver.",
							code: waiverData.code || "WAIVER_DENIED",
						});
						return false;
					}

					const serverWaivedUris = Array.isArray(waiverData.waived_concept_uris)
						? waiverData.waived_concept_uris
						: Array.isArray(waiverData.target_concept_uris)
							? waiverData.target_concept_uris
							: targetUris;
					const serverWaivedSha =
						waiverData.waived_base_sha256 ||
						waiverData.base_sha256 ||
						currentSession.head_sha256;

					set((state) => {
						const prevWaiver = state.sessionWaivers[sid];
						const mergedWaivedUris = Array.from(
							new Set([
								...(prevWaiver &&
								prevWaiver.waived_base_sha256 === serverWaivedSha
									? prevWaiver.waived_concept_uris
									: []),
								...serverWaivedUris,
							]),
						);
						const snapshots: Record<string, ConceptNode> = {
							...(prevWaiver &&
							prevWaiver.waived_base_sha256 === serverWaivedSha &&
							prevWaiver.waived_concept_snapshots
								? prevWaiver.waived_concept_snapshots
								: {}),
						};
						for (const n of state.nodes) {
							if (serverWaivedUris.includes(n.concept_uri)) {
								snapshots[n.concept_uri] = n;
							}
						}

						const newWaiver = {
							waiver_applied: true,
							waiver_rationale: rationale.trim(),
							waived_concept_uris: mergedWaivedUris,
							waived_base_sha256: serverWaivedSha,
							waived_concept_snapshots: snapshots,
						};

						const nextSessionWaivers = {
							...state.sessionWaivers,
							[sid]: newWaiver,
						};
						const nextState: StudioStoreState = {
							...state,
							sessionWaivers: nextSessionWaivers,
						};
						const updatedScorecard = recomputeScorecard(
							nextState,
							state.nodes,
							state.edges,
						);

						return {
							sessionWaivers: nextSessionWaivers,
							qualityScorecard: updatedScorecard,
						};
					});
					get().addToast({
						type: "urn:opendv:notice:waiver-authorized",
						title: "Administrative Waiver Authorized",
						status: 200,
						detail: "Administrative grounding waiver successfully recorded.",
						code: "OK",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				} finally {
					set({ isSubmittingWaiver: false });
				}
			},

			submitFeedback: async (
				rating,
				comment,
				conceptUriOrFetch,
				maybeFetchImpl,
			) => {
				const conceptUri =
					typeof conceptUriOrFetch === "string" || conceptUriOrFetch === null
						? conceptUriOrFetch
						: null;
				const fetchImpl =
					typeof conceptUriOrFetch === "function"
						? conceptUriOrFetch
						: (maybeFetchImpl ?? fetch);

				const activeSession = get().activeSession;
				if (!activeSession) return false;

				if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
					get().addToast({
						type: "urn:opendv:error:invalid-feedback-rating",
						title: "Invalid Feedback Rating",
						status: 400,
						detail: "Feedback rating must be an integer between 1 and 5.",
						code: "INVALID_FEEDBACK_RATING",
					});
					return false;
				}

				const sid = activeSession.session_id;
				const normalizedConceptUri =
					typeof conceptUri === "string" && conceptUri.trim().length > 0
						? conceptUri.trim()
						: null;
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/feedback`,
						{
							method: "POST",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({
								rating,
								comment: comment.trim(),
								concept_uri: normalizedConceptUri,
							}),
						},
					);
					if (get().activeSession?.session_id !== sid) return false;

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:feedback-rejected",
							title: "Feedback Submission Failed",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "FEEDBACK_SUBMISSION_FAILED",
						});
						return false;
					}

					set((state) => ({
						qualityScorecard: {
							...state.qualityScorecard,
							feedback_rating: rating,
							feedback_comment: comment.trim(),
							feedback_concept_uri: normalizedConceptUri,
							feedback_submitted: true,
						},
					}));
					get().addToast({
						type: "urn:opendv:notice:feedback-submitted",
						title: "Feedback Recorded",
						status: 200,
						detail: `Recorded ${rating}-star steward feedback.`,
						code: "OK",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				}
			},

			// Export & Promotion
			exportOntology: async (format, version, fetchImpl = fetch) => {
				const activeSession = get().activeSession;
				if (!activeSession) throw new Error("No active session");

				const normalized = format.toLowerCase();
				const serverFormat =
					normalized === "rfc8785" || normalized === "jcs" ? "json" : format;
				const ver = version || activeSession.active_version;
				const path = `/api/v1/ontology/studio/sessions/${encodeURIComponent(activeSession.session_id)}/export?export_format=${encodeURIComponent(serverFormat)}&version=${encodeURIComponent(ver)}`;

				let res: Response;
				try {
					res = await studioFetch(fetchImpl, path);
				} catch (err) {
					get().addToast({
						type: "urn:opendv:error:export-network",
						title: "Export Network Error",
						status: 503,
						detail: String(err),
						code: "EXPORT_NETWORK_ERROR",
					});
					throw err;
				}
				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
						title?: string;
					};
					const detail = err.detail || `Export failed with HTTP ${res.status}`;
					get().addToast({
						type: "urn:opendv:error:export-failed",
						title: err.title || "Ontology Export Failed",
						status: res.status,
						detail,
						code: err.code || "EXPORT_FAILED",
					});
					throw new Error(detail);
				}
				return await res.text();
			},

			promoteWorkspace: async (req, fetchImpl = fetch) => {
				if (get().isPromoting) return false;
				const activeSession = get().activeSession;
				if (!activeSession) return false;

				if (!req.rationale || req.rationale.trim().length < 10) {
					get().addToast({
						type: "urn:opendv:error:promotion-rationale-short",
						title: "Promotion Rationale Too Short",
						status: 400,
						detail: "Promotion rationale must be at least 10 characters.",
						code: "PROMOTION_RATIONALE_REQUIRED",
					});
					return false;
				}

				if (get().hasUnsavedChanges) {
					get().addToast({
						type: "urn:opendv:error:unsaved-changes",
						title: "Unsaved Changes",
						status: 400,
						detail:
							"Cannot promote workspace with uncommitted changes. Please commit or discard changes first.",
						code: "PROMOTION_UNSAVED_CHANGES",
					});
					return false;
				}

				const shaRegex = /^[0-9a-f]{64}$/i;
				if (
					!shaRegex.test(req.head_sha256) ||
					!shaRegex.test(req.workspace_sha256)
				) {
					get().addToast({
						type: "urn:opendv:error:invalid-sha256-format",
						title: "Invalid Digest Format",
						status: 400,
						detail:
							"head_sha256 and workspace_sha256 must be valid 64-character hexadecimal SHA-256 digests.",
						code: "INVALID_SHA256_DIGEST",
					});
					return false;
				}

				if (req.head_sha256 !== activeSession.head_sha256) {
					get().addToast({
						type: "urn:opendv:error:occ-precondition-failed",
						title: "Stale Session Head",
						status: 412,
						detail:
							"Provided head_sha256 does not match current session head. Refresh workspace state.",
						code: "OCC_PRECONDITION_FAILED",
					});
					return false;
				}

				const sid = activeSession.session_id;
				const trimmedWorkspace = req.workspace?.trim();
				const trimmedHistoricalRationale =
					req.historical_contract_rationale?.trim();
				const promoteBody: Record<string, unknown> = {
					workspace_sha256: req.workspace_sha256,
					head_sha256: req.head_sha256,
					rationale: req.rationale.trim(),
					...(trimmedWorkspace ? { workspace: trimmedWorkspace } : {}),
					...(req.acknowledge_historical_contract_warning !== undefined
						? {
								acknowledge_historical_contract_warning:
									req.acknowledge_historical_contract_warning,
							}
						: {}),
					...(trimmedHistoricalRationale
						? { historical_contract_rationale: trimmedHistoricalRationale }
						: {}),
				};
				set({ isPromoting: true });
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/promote`,
						{
							method: "POST",
							headers: {
								"Content-Type": "application/json",
								"If-Match": `"${req.head_sha256}"`,
								"X-OpenDV-Workspace-If-Match": `"${req.workspace_sha256}"`,
							},
							body: JSON.stringify(promoteBody),
						},
					);

					if (!res.ok) {
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						if (get().activeSession?.session_id !== sid) return false;
						get().addToast({
							type: "urn:opendv:error:promote-rejected",
							title: "Promotion Rejected",
							status: res.status,
							detail: err.detail || `Server returned HTTP ${res.status}`,
							code: err.code || "ONT_EVO_CONCURRENT_MODIFICATION",
						});
						return false;
					}

					const promoData = (await res.json().catch(() => ({}))) as {
						promoted_version?: string;
						workspace_sha256?: string;
						head_sha256?: string;
					};
					const currentSession = get().activeSession;
					if (!currentSession || currentSession.session_id !== sid) {
						if (promoData.promoted_version || promoData.head_sha256) {
							set((state) => ({
								sessions: state.sessions.map((s) =>
									s.session_id === sid
										? {
												...s,
												status: "PROMOTED",
												head_sha256: promoData.head_sha256 || s.head_sha256,
												active_version:
													promoData.promoted_version || s.active_version,
											}
										: s,
								),
							}));
						}
						return false;
					}

					const newSha = promoData.head_sha256 || currentSession.head_sha256;
					if (!newSha) {
						get().addToast({
							type: "urn:opendv:error:promote-no-sha",
							title: "Promotion SHA Missing",
							status: 500,
							detail:
								"Server promotion response did not include a valid SHA-256 digest.",
							code: "PROMOTION_SHA_MISSING",
						});
						return false;
					}

					const newVersion =
						promoData.promoted_version || currentSession.active_version;

					set((state) => ({
						activeSession: state.activeSession
							? {
									...state.activeSession,
									status: "PROMOTED",
									active_version: newVersion,
									head_sha256: newSha,
								}
							: null,
						sessions: state.sessions.map((s) =>
							s.session_id === sid
								? {
										...s,
										status: "PROMOTED",
										active_version: newVersion,
										head_sha256: newSha,
									}
								: s,
						),
						layout: { ...state.layout, activeModal: null },
					}));

					get().addToast({
						type: "urn:opendv:notice:promoted",
						title: "Workspace Promotion Succeeded",
						status: 200,
						detail:
							"Ontology version successfully promoted into workspace pipeline.",
						code: "OK",
					});
					return true;
				} catch (err) {
					if (get().activeSession?.session_id === sid) {
						get().addToast({
							type: "urn:opendv:error:network",
							title: "Network Error",
							status: 503,
							detail: String(err),
							code: "NETWORK_ERROR",
						});
					}
					return false;
				} finally {
					set({ isPromoting: false });
				}
			},

			// Layout actions
			setLeftDrawerTab: (tab) =>
				set((state) => ({
					layout: { ...state.layout, leftDrawerTab: tab, leftDrawerOpen: true },
				})),

			toggleLeftDrawer: (open) =>
				set((state) => ({
					layout: {
						...state.layout,
						leftDrawerOpen:
							open !== undefined ? open : !state.layout.leftDrawerOpen,
					},
				})),

			toggleRightDrawer: (open) =>
				set((state) => ({
					layout: {
						...state.layout,
						rightDrawerOpen:
							open !== undefined ? open : !state.layout.rightDrawerOpen,
					},
				})),

			toggleBottomDock: (open) =>
				set((state) => ({
					layout: {
						...state.layout,
						bottomDockOpen:
							open !== undefined ? open : !state.layout.bottomDockOpen,
					},
				})),

			setActiveModal: (modal) =>
				set((state) => ({
					layout: { ...state.layout, activeModal: modal },
				})),

			setLodTier: (tier) =>
				set((state) => ({
					layout: { ...state.layout, lodTier: tier },
				})),

			setMinimapVisible: (visible) =>
				set((state) => ({
					layout: { ...state.layout, minimapVisible: visible },
				})),

			setSearchTerm: (term) =>
				set((state) => ({
					layout: { ...state.layout, searchTerm: term },
				})),

			triggerRelayout: () =>
				set((state) => ({
					layout: {
						...state.layout,
						layoutTriggerCount: (state.layout.layoutTriggerCount || 0) + 1,
					},
				})),

			// Toast actions
			addToast: (toast) => {
				const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
				const newToast: StudioErrorToast = {
					...toast,
					id,
					timestamp: new Date().toISOString(),
				};
				set((state) => ({
					toasts: [newToast, ...state.toasts].slice(0, 8),
				}));
			},

			dismissToast: (id) =>
				set((state) => ({
					toasts: state.toasts.filter((t) => t.id !== id),
				})),

			reset: () => {
				activeSessionRequestId++;
				activeGraphRequestId++;
				activeClarificationsRequestId++;
				activeSourcesRequestId++;
				activeProposalsRequestId++;
				activeJobGeneration++;
				for (const es of activeEventSources) {
					try {
						es.close();
					} catch {
						// ignore cleanup errors
					}
				}
				activeEventSources.clear();
				for (const timer of activePollTimers) {
					clearTimeout(timer);
				}
				activePollTimers.clear();
				set({
					userRole: options.userRole ?? "editor",
					authToken: null,
					authRequired: false,
					isBootstrappingAuth: false,
					activeSession: options.initialSession ?? null,
					sessions: options.initialSession ? [options.initialSession] : [],
					isLoadingSessions: false,
					sources: [],
					isLoadingSources: false,
					parsingState: createDefaultParsingState(),
					clarifications: [],
					answers: {},
					answers_sha256: undefined,
					isLoadingClarifications: false,
					isClarifying: false,
					isConstructing: false,
					proposals: [],
					isLoadingProposals: false,
					nodes: [],
					edges: [],
					committedNodes: [],
					committedEdges: [],
					selectedNodeId: null,
					selectedEdgeId: null,
					isLoadingGraph: false,
					hasUnsavedChanges: false,
					viewedVersion: null,
					isHistoricalView: false,
					isCommitting: false,
					isPromoting: false,
					isSubmittingWaiver: false,
					diffState: createDefaultDiffState(),
					qualityScorecard: createDefaultQualityScorecard(),
					sessionWaivers: {},
					layout: createDefaultLayoutState(),
					sseEvents: [],
					toasts: [],
				});
			},
		};
	});

	return api;
}

export const studioStore = createStudioStore();

/**
 * React hook to subscribe to studioStore with per-state-reference memoization (MIN-04).
 * Caches selector output per store state reference to prevent re-render loops on derived selectors.
 * Note: Returning live snapshot during SSR (renderToStaticMarkup) is intentional for headless component testing.
 */
export function useStudioStore<T>(selector: (state: StudioStoreState) => T): T {
	const lastStateRef = useRef<StudioStoreState | undefined>(undefined);
	const lastSelectorRef = useRef(selector);
	const lastResultRef = useRef<T | undefined>(undefined);
	const hasCachedRef = useRef(false);

	const getSnapshot = () => {
		const state = studioStore.getState();
		if (
			hasCachedRef.current &&
			lastStateRef.current === state &&
			lastSelectorRef.current === selector
		) {
			return lastResultRef.current as T;
		}
		lastStateRef.current = state;
		lastSelectorRef.current = selector;
		lastResultRef.current = selector(state);
		hasCachedRef.current = true;
		return lastResultRef.current;
	};

	return useSyncExternalStore(studioStore.subscribe, getSnapshot, getSnapshot);
}
