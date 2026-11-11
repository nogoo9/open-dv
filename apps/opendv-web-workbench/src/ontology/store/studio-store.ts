/**
 * Zustand Studio Store for Web Workbench SPA (/ontology)
 * (apps/opendv-web-workbench/src/ontology/store/studio-store.ts)
 *
 * Implements Task 5D.1 (#222).
 */

import { useRef, useSyncExternalStore } from "react";
import { createStore } from "zustand/vanilla";
import type {
	ClarificationAnswer,
	ClarificationQuestion,
	ConceptAttribute,
	ConceptNode,
	CreateSessionRequest,
	DiffRecord,
	DiffState,
	ExternalMapping,
	LayoutState,
	ParsingState,
	QualityScorecard,
	RelationshipEdge,
	SessionCommit,
	SourceDocument,
	StudioErrorToast,
	StudioMode,
	StudioSessionSummary,
} from "./types";

export * from "./types";

export interface StudioStoreState {
	// Mode & Elevation
	activeMode: StudioMode;
	userRole: "viewer" | "editor" | "admin";

	// Sessions
	activeSession: StudioSessionSummary | null;
	sessions: StudioSessionSummary[];
	isLoadingSessions: boolean;

	// Sources & Ingestion
	sources: SourceDocument[];
	isLoadingSources: boolean;
	parsingState: ParsingState;

	// Clarification Q&A
	clarifications: ClarificationQuestion[];
	answers: Record<string, ClarificationAnswer>;
	answers_sha256?: string | undefined;
	isLoadingClarifications: boolean;

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
		}
	>;

	// Layout & Viewport
	layout: LayoutState;

	// Diagnostic & RFC 9457 Errors
	toasts: StudioErrorToast[];

	// Actions
	setMode: (mode: StudioMode) => void;
	setUserRole: (role: "viewer" | "editor" | "admin") => void;

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
			size: number;
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

	// Clarification actions
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

	// Graph actions
	loadGraph: (
		version?: string,
		fetchImpl?: typeof fetch,
		targetSessionId?: string,
	) => Promise<void>;
	selectNode: (nodeId: string | null) => void;
	selectEdge: (edgeId: string | null) => void;
	updateConcept: (nodeId: string, updates: Partial<ConceptNode>) => void;
	addConcept: (node: ConceptNode) => void;
	addRelationship: (edge: RelationshipEdge) => void;
	discardChanges: () => void;

	// Visual Diff actions
	computeDiff: (
		baseVersion: string,
		targetVersion: string,
		baseGraph?: { nodes: ConceptNode[]; edges: RelationshipEdge[] },
	) => void;
	setDiffMode: (mode: "unified" | "side-by-side" | "off") => void;
	commitChanges: (
		rationale?: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;

	// Quality & Waiver & Feedback
	updateQualityScorecard: (updates: Partial<QualityScorecard>) => void;
	submitWaiver: (
		rationale: string,
		waiverKey: string,
		fetchImpl?: typeof fetch,
	) => Promise<boolean>;
	submitFeedback: (
		rating: number,
		comment: string,
		fetchImpl?: typeof fetch,
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
	userRole?: "viewer" | "editor" | "admin";
	initialSession?: StudioSessionSummary | null;
	apiUrl?: string;
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
	return trimmed.replace(/[<>"{}|^`\\\s\x00-\x20]/g, (c) => encodeURIComponent(c));
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
			confidence: explicitConfidence ?? 0.0,
		};
	}
	const scoredConfs = list
		.map((m) => m.confidence)
		.filter((c): c is number => typeof c === "number" && !Number.isNaN(c));
	const avgConf =
		scoredConfs.length > 0
			? scoredConfs.reduce((acc, m) => acc + m, 0) / scoredConfs.length
			: (explicitConfidence ?? 0.85);
	const status =
		avgConf >= 0.65
			? "GROUNDED"
			: avgConf >= 0.4
				? "LOW_CONFIDENCE"
				: "UNGROUNDED";
	return {
		grounding_status: status,
		confidence: avgConf,
	};
}

export interface QualityScorecardOptions {
	grandfatheredUris?: string[];
	waiverApplied?: boolean;
	waivedUris?: string[];
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

	if (
		!Array.isArray(optionsOrGrandfathered) &&
		typeof optionsOrGrandfathered === "object" &&
		optionsOrGrandfathered !== null
	) {
		grandfatheredUris = optionsOrGrandfathered.grandfatheredUris || [];
		waiverApplied = Boolean(optionsOrGrandfathered.waiverApplied);
		waivedUris = optionsOrGrandfathered.waivedUris || [];
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
	// Invariant ONT-EVO-002 strictly forbids ungrounded or provisional URIs without an authorized waiver or ratified amendment.
	// A concept is genuinely grounded iff its status is GROUNDED and it possesses valid external mappings (MAJ-01).
	function isConceptGrounded(n: ConceptNode): boolean {
		if (n.grounding_status !== "GROUNDED") return false;
		const mappings = n.external_mappings || [];
		if (mappings.length === 0) return false;
		return deriveGroundingStatus(mappings, n.confidence).grounding_status === "GROUNDED";
	}

	const groundedNodes = nodes.filter((n) => isConceptGrounded(n)).length;
	const ungroundedNodes = nodes.filter(
		(n) =>
			!isConceptGrounded(n) &&
			!grandfatheredUris.includes(n.concept_uri),
	);
	const groundingRatio = nodes.length > 0 ? groundedNodes / nodes.length : 0;

	const allUngroundedWaived =
		waiverApplied &&
		(ungroundedNodes.length === 0 ||
			ungroundedNodes.every((n) => waivedUris.includes(n.concept_uri)));

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
	// Association relationships can be bidirectional or self-referential;
	// acyclicity strictly guards taxonomy (parent_concept_uri and is_a/subClassOf links).
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
		waiver_applied: waiverApplied,
		waiver_rationale: "",
		waived_concept_uris: waivedUris,
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
	});

	return {
		...computed,
		waiver_applied: isWaiverValid,
		waiver_rationale: isWaiverValid ? savedWaiver?.waiver_rationale || "" : "",
		waived_concept_uris: isWaiverValid ? waivedUris : [],
		waived_base_sha256: isWaiverValid
			? savedWaiver?.waived_base_sha256 || undefined
			: undefined,
		feedback_rating: state.qualityScorecard.feedback_rating,
		feedback_comment: state.qualityScorecard.feedback_comment,
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
 * Prunes layout-only properties (id, x, y) and sorts concepts and relationships deterministically by URI.
 */
export function canonicalizeGraphADict(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	session: StudioSessionSummary,
): Record<string, unknown> {
	const sortedConcepts = [...nodes]
		.sort((a, b) =>
			a.concept_uri < b.concept_uri ? -1 : a.concept_uri > b.concept_uri ? 1 : 0,
		)
		.map((n) => {
			const c: Record<string, unknown> = {
				concept_name: n.concept_name,
				concept_uri: n.concept_uri,
				definition: n.definition,
				elm_archetype: n.elm_archetype,
			};
			if (n.attributes && n.attributes.length > 0) {
				c.attributes = n.attributes.map((attr) => ({
					attribute_name: attr.attribute_name,
					data_type: attr.data_type,
					...(attr.description ? { description: attr.description } : {}),
					...(attr.is_optional !== null && attr.is_optional !== undefined
						? { is_optional: attr.is_optional }
						: {}),
				}));
			}
			if (n.external_mappings && n.external_mappings.length > 0) {
				c.external_mappings = n.external_mappings.map((m) => ({
					scheme: m.scheme,
					target_uri: m.target_uri,
					...(typeof m.confidence === "number" ? { confidence: m.confidence } : {}),
					...(m.predicate ? { predicate: m.predicate } : {}),
				}));
			}
			if (n.synonyms && n.synonyms.length > 0) {
				c.synonyms = n.synonyms;
			}
			if (n.parent_concept_uri) c.parent_concept_uri = n.parent_concept_uri;
			if (n.target_hub) c.target_hub = n.target_hub;
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
			const r: Record<string, unknown> = {
				relationship_name: e.relationship_name,
				relationship_uri: e.relationship_uri,
				source_concept_uri: e.source_concept_uri,
				target_concept_uri: e.target_concept_uri,
				target_link: e.target_link,
			};
			if (e.cardinality) r.cardinality = e.cardinality;
			if (e.description) r.description = e.description;
			return r;
		});

	return {
		canonical_business_concepts: sortedConcepts,
		canonical_relationships: sortedRelationships,
		decision_refs: [],
		domain_namespace: session.domain_namespace || "general",
		enterprise_owner: session.enterprise_owner || "Enterprise Architecture",
		ontology_version: session.active_version || "1.0.0",
		reference_enumerations: [],
		standards_references: [],
	};
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
		case "json": {
			const sortedConcepts = [...nodes]
				.sort((a, b) =>
					a.concept_uri < b.concept_uri ? -1 : a.concept_uri > b.concept_uri ? 1 : 0,
				)
				.map((n) => ({
					concept_uri: n.concept_uri,
					concept_name: n.concept_name,
					elm_archetype: n.elm_archetype,
					definition: n.definition,
					parent_concept_uri: n.parent_concept_uri ?? null,
					target_hub: n.target_hub ?? null,
					grounding_status: n.grounding_status,
					attributes: n.attributes,
					synonyms: n.synonyms,
					external_mappings: n.external_mappings,
				}));
			const sortedEdges = [...edges]
				.sort((a, b) =>
					a.relationship_uri < b.relationship_uri
						? -1
						: a.relationship_uri > b.relationship_uri
							? 1
							: 0,
				)
				.map((e) => ({
					relationship_uri: e.relationship_uri,
					relationship_name: e.relationship_name,
					source_concept_uri: e.source_concept_uri,
					target_concept_uri: e.target_concept_uri,
					cardinality: e.cardinality ?? null,
					target_link: e.target_link,
				}));
			const obj = {
				$schema: "https://open-dv.io/schemas/v1/ontology-export.schema.json",
				schema_version: "v1",
				ontology_version: version,
				domain_namespace: session.domain_namespace || "general",
				concepts: sortedConcepts,
				relationships: sortedEdges,
			};
			return JSON.stringify(obj, null, 2);
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
				lines.push(`    rdfs:label "${escapeTurtleString(e.relationship_name)}" ;`);
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
				lines.push(`  <owl:Class rdf:about="${escapeXml(escapeIri(n.concept_uri))}">`);
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
				lines.push(`    <rdfs:label>${escapeXml(e.relationship_name)}</rdfs:label>`);
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
			const lines = [
				`ontology_version: "${escapeYamlString(version)}"`,
				`domain_namespace: "${escapeYamlString(rawDomain)}"`,
				"concepts:",
			];
			for (const n of nodes) {
				lines.push(`  - concept_uri: "${escapeYamlString(n.concept_uri)}"`);
				lines.push(`    concept_name: "${escapeYamlString(n.concept_name)}"`);
				lines.push(`    elm_archetype: "${escapeYamlString(n.elm_archetype)}"`);
				lines.push(`    definition: "${escapeYamlString(n.definition)}"`);
			}
			lines.push("relationships:");
			for (const e of edges) {
				lines.push(
					`  - relationship_uri: "${escapeYamlString(e.relationship_uri)}"`,
				);
				lines.push(
					`    relationship_name: "${escapeYamlString(e.relationship_name)}"`,
				);
				lines.push(
					`    source_concept_uri: "${escapeYamlString(e.source_concept_uri)}"`,
				);
				lines.push(
					`    target_concept_uri: "${escapeYamlString(e.target_concept_uri)}"`,
				);
			}
			return lines.join("\n");
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

	let activeSessionRequestId = 0;
	let activeGraphRequestId = 0;
	let activeClarificationsRequestId = 0;
	let activeSourcesRequestId = 0;

	const api = createStore<StudioStoreState>()((set, get) => ({
		activeMode: options.activeMode ?? "local_standalone",
		userRole: options.userRole ?? "editor",

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
		toasts: [],

		setMode: (mode) => set({ activeMode: mode }),
		setUserRole: (role) => set({ userRole: role }),

		// Session actions
		loadSessions: async (fetchImpl = fetch) => {
			set({ isLoadingSessions: true });
			try {
				const res = await fetchImpl(
					resolveUrl("/api/v1/ontology/studio/sessions"),
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
						code: errorJson.code || "SESSION_NOT_FOUND",
					});
					set({ isLoadingSessions: false });
					return;
				}
				const data = (await res.json()) as
					| StudioSessionSummary[]
					| { sessions: StudioSessionSummary[] };
				const sessionList = Array.isArray(data) ? data : data.sessions || [];
				const currentActive = get().activeSession;
				const initialSession = currentActive
					? sessionList.find((s) => s.session_id === currentActive.session_id) ?? currentActive
					: sessionList[0] ?? null;
				set({
					sessions: sessionList,
					isLoadingSessions: false,
					activeSession: initialSession,
				});

				if (
					initialSession &&
					(!currentActive || currentActive.session_id !== initialSession.session_id)
				) {
					await get().selectSession(initialSession.session_id, fetchImpl);
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

			const reqId = ++activeSessionRequestId;
			const found = get().sessions.find((s) => s.session_id === sessionId);

			const sessionWaivers = get().sessionWaivers || {};
			const savedWaiver = sessionWaivers[sessionId];
			const initialScorecard = createDefaultQualityScorecard();
			if (savedWaiver) {
				initialScorecard.waiver_applied = savedWaiver.waiver_applied;
				initialScorecard.waiver_rationale = savedWaiver.waiver_rationale;
				initialScorecard.waived_concept_uris = savedWaiver.waived_concept_uris;
			}

			// Clear session-specific state immediately to avoid cross-session contamination (MIN-01)
			set({
				activeSession: found ?? null,
				isLoadingSessions: true,
				isLoadingSources: false,
				isLoadingGraph: false,
				isLoadingClarifications: false,
				hasUnsavedChanges: false,
				viewedVersion: null,
				isHistoricalView: false,
				nodes: [],
				edges: [],
				committedNodes: [],
				committedEdges: [],
				clarifications: [],
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
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sessionId)}`,
					),
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
						const rawObj = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
						const sessionObj = (sessionData || {}) as unknown as Record<string, unknown>;
						const serverWaiver = (sessionObj.active_waiver || rawObj.active_waiver || (sessionObj.waiver_applied ? sessionObj : null)) as {
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
												serverWaiver?.waiver_rationale || "Server authorized waiver",
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
											serverWaiver?.waiver_rationale || "Server authorized waiver",
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
								sessions: state.sessions.some((s) => s.session_id === sessionId)
									? state.sessions.map((s) =>
											s.session_id === sessionId ? sessionData : s,
										)
									: [sessionData, ...state.sessions],
								sessionWaivers: updatedWaivers,
								qualityScorecard: updatedScorecard,
								isLoadingSessions: false,
							};
						});

						if (reqId !== activeSessionRequestId) return;

						// Reload graph, clarifications, and sources for the selected session
						await Promise.all([
							get().loadGraph(
								sessionData.active_version || "1.0.0",
								fetchImpl,
								sessionId,
							),
							get().loadClarifications(fetchImpl, sessionId),
							get().loadSources(fetchImpl, sessionId),
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
							err.detail || `Session ${sessionId} returned HTTP ${res.status}`,
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
				const res = await fetchImpl(
					resolveUrl("/api/v1/ontology/studio/sessions"),
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
				if (confidential) {
					formData.append("confidential", "true");
				}

				const sid = activeSession.session_id;
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources`,
					),
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

				const uploaded = (await res.json()) as SourceDocument;
				if (get().activeSession?.session_id !== sid) {
					set({ isLoadingSources: false });
					return null;
				}
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
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/sources/${encodeURIComponent(sourceId)}`,
					),
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
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/parse`,
					),
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ ocr_mode: ocrMode }),
					},
				);
				if (get().activeSession?.session_id !== sid) return null;

				if (!res.ok) {
					const err = (await res.json().catch(() => ({}))) as {
						detail?: string;
						code?: string;
					};
					if (get().activeSession?.session_id !== sid) return null;
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
					stage?: string;
					progress_pct?: number;
				};
				if (get().activeSession?.session_id !== sid) return null;
				set((state) => ({
					parsingState: {
						...state.parsingState,
						status: "parsing",
						progress_pct: job.progress_pct || 35,
						active_job_id: job.job_id,
						stage: job.stage || "layout_eval",
					},
				}));
				return job.job_id;
			} catch (err) {
				if (get().activeSession?.session_id === sid) {
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
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/sources`,
					),
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
					| SourceDocument[]
					| { sources: SourceDocument[] };

				if (
					reqId !== activeSourcesRequestId ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const sourcesList = Array.isArray(data) ? data : data.sources || [];
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

		// Clarification actions
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
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/questions`,
					),
				);
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

				const etag = res.headers
					.get("ETag")
					?.replace(/^W\//, "")
					.replace(/"/g, "");
				const data = (await res.json()) as
					| ClarificationQuestion[]
					| { questions: ClarificationQuestion[]; answers_sha256?: string };

				if (
					reqId !== activeClarificationsRequestId ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const questions = Array.isArray(data) ? data : data.questions || [];
				const answersSha =
					(!Array.isArray(data) && data.answers_sha256) || etag || undefined;

				const answersMap: Record<string, ClarificationAnswer> = {};
				for (const q of questions) {
					if (
						q.status === "ANSWERED" &&
						q.selected_option_ids &&
						q.selected_option_ids.length > 0
					) {
						answersMap[q.question_id] = {
							question_id: q.question_id,
							selected_option_ids: q.selected_option_ids,
							custom_answer_text: q.custom_answer_text,
							answered_at: q.answered_at,
						};
					}
				}

				if (
					reqId !== activeClarificationsRequestId ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				set({
					clarifications: questions,
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

		submitAnswer: async (answer, expectedAnswersSha256, fetchImpl = fetch) => {
			const activeSession = get().activeSession;
			if (!activeSession) return false;

			const sid = activeSession.session_id;
			const expectedSha = expectedAnswersSha256 || get().answers_sha256;

			try {
				const payload: Record<string, unknown> = {
					session_id: sid,
					answers: [
						{
							question_id: answer.question_id,
							selected_option_ids: answer.selected_option_ids,
							custom_answer_text: answer.custom_answer_text,
						},
					],
				};
				if (expectedSha) {
					payload.expected_answers_sha256 = expectedSha;
				}

				const headers: Record<string, string> = {
					"Content-Type": "application/json",
				};
				if (expectedSha) {
					headers["If-Match"] = `"${expectedSha}"`;
				}

				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/answers`,
					),
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
						await get().loadClarifications(fetchImpl);
						if (get().activeSession?.session_id !== sid) return false;
						set((state) => ({
							answers: {
								...currentDrafts,
								...state.answers,
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
					.get("ETag")
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

				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(expectedId)}/export?export_format=json&version=${encodeURIComponent(ver)}`,
					),
				);

				if (!res.ok) {
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

				const ont = (await res.json()) as {
					concepts?: Array<{
						concept_uri: string;
						concept_name?: string;
						elm_archetype?: string;
						definition?: string;
						target_hub?: string | null;
						parent_concept_uri?: string | null;
						confidence?: number;
						grounding_status?: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED";
						attributes?: ConceptAttribute[];
						synonyms?: string[];
						external_mappings?: ExternalMapping[];
					}>;
					canonical_business_concepts?: Array<{
						concept_uri: string;
						concept_name?: string;
						elm_archetype?: string;
						definition?: string;
						target_hub?: string | null;
						parent_concept_uri?: string | null;
						confidence?: number;
						grounding_status?: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED";
						attributes?: ConceptAttribute[];
						synonyms?: string[];
						external_mappings?: ExternalMapping[];
					}>;
					canonical_relationships?: Array<{
						relationship_uri: string;
						relationship_name?: string;
						target_link?: string;
						endpoints: string[];
						cardinality?: string | null;
						description?: string | null;
					}>;
					relationships?: Array<{
						relationship_uri: string;
						relationship_name?: string;
						target_link?: string;
						endpoints: string[];
						cardinality?: string | null;
						description?: string | null;
					}>;
				};

				if (
					reqId !== activeGraphRequestId ||
					get().activeSession?.session_id !== expectedId
				)
					return;

				const conceptsList =
					ont.concepts || ont.canonical_business_concepts || [];
				const relationshipsList =
					ont.canonical_relationships || ont.relationships || [];

				const loadedNodes: ConceptNode[] = conceptsList.map((c, idx) => {
					const mappings = c.external_mappings || [];
					const derived = deriveGroundingStatus(mappings, c.confidence);
					let status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED" =
						derived.grounding_status;
					if (mappings.length > 0 && c.grounding_status) {
						if (
							c.grounding_status === "UNGROUNDED" ||
							c.grounding_status === derived.grounding_status
						) {
							status = c.grounding_status;
						}
					}
					const conf =
						c.confidence !== undefined && c.confidence !== null
							? c.confidence
							: derived.confidence;

					return {
						id: c.concept_uri,
						concept_uri: c.concept_uri,
						concept_name:
							c.concept_name ||
							c.concept_uri.split(":").pop()?.replace(/_/g, " ") ||
							"Concept",
						elm_archetype: c.elm_archetype || "CORE_BUSINESS_CONCEPT",
						definition: c.definition || "",
						target_hub: c.target_hub ?? null,
						parent_concept_uri: c.parent_concept_uri ?? null,
						confidence: conf,
						grounding_status: status,
						attributes: c.attributes || [],
						synonyms: c.synonyms || [],
						external_mappings: mappings,
						x: 100 + (idx % 4) * 260,
						y: 100 + Math.floor(idx / 4) * 180,
					};
				});

				const loadedEdges: RelationshipEdge[] = relationshipsList.map((r) => ({
					id: r.relationship_uri,
					relationship_uri: r.relationship_uri,
					relationship_name:
						r.relationship_name ||
						r.relationship_uri.split(":").pop()?.replace(/_/g, " ") ||
						"relationship",
					target_link: r.target_link || "lnk_default",
					source_concept_uri: (r.endpoints && r.endpoints[0]) || "",
					target_concept_uri: (r.endpoints && r.endpoints[1]) || "",
					cardinality: r.cardinality ?? null,
					description: r.description ?? null,
				}));

				if (
					reqId !== activeGraphRequestId ||
					get().activeSession?.session_id !== expectedId ||
					get().hasUnsavedChanges
				)
					return;

				set((state) => ({
					viewedVersion: ver,
					isHistoricalView: !isHeadVersion,
					nodes: loadedNodes,
					edges: loadedEdges,
					committedNodes: isHeadVersion ? loadedNodes : state.committedNodes,
					committedEdges: isHeadVersion ? loadedEdges : state.committedEdges,
					hasUnsavedChanges: isHeadVersion ? false : state.hasUnsavedChanges,
					qualityScorecard: recomputeScorecard(state, loadedNodes, loadedEdges),
					isLoadingGraph: false,
				}));
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
				const {
					id: _id,
					concept_uri: _uri,
					...safeUpdates
				} = updates;
				const newNodes = state.nodes.map((n) => {
					if (n.id !== conceptId && n.concept_uri !== conceptId) return n;
					const merged = { ...n, ...safeUpdates };

					const effectiveMappings =
						updates.external_mappings !== undefined
							? (updates.external_mappings || [])
							: (n.external_mappings || []);

					const derived = deriveGroundingStatus(
						effectiveMappings,
						updates.confidence ?? n.confidence,
					);

					let nextConfidence = updates.confidence ?? derived.confidence;
					let nextGroundingStatus: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED" =
						derived.grounding_status;

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

				// Only flag unsaved changes if there are semantic differences from committed state (MIN-02)
				const committedNodeMap = new Map(
					state.committedNodes.map((cn) => [cn.concept_uri, cn]),
				);
				const hasSemanticDiff =
					state.hasUnsavedChanges ||
					newNodes.length !== state.committedNodes.length ||
					newNodes.some((cn) => {
						const comm = committedNodeMap.get(cn.concept_uri);
						if (!comm) return true;
						return (
							cn.concept_name !== comm.concept_name ||
							cn.elm_archetype !== comm.elm_archetype ||
							(cn.definition || "") !== (comm.definition || "") ||
							(cn.target_hub ?? null) !== (comm.target_hub ?? null) ||
							(cn.parent_concept_uri ?? null) !==
								(comm.parent_concept_uri ?? null) ||
							JSON.stringify(cn.attributes || []) !==
								JSON.stringify(comm.attributes || []) ||
							JSON.stringify(cn.synonyms || []) !==
								JSON.stringify(comm.synonyms || []) ||
							JSON.stringify(cn.external_mappings || []) !==
								JSON.stringify(comm.external_mappings || [])
						);
					});

				return {
					nodes: newNodes,
					hasUnsavedChanges: hasSemanticDiff,
					qualityScorecard: recomputeScorecard(state, newNodes, state.edges),
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

		discardChanges: () => {
			const committedNodes = get().committedNodes;
			const committedEdges = get().committedEdges;
			set((state) => ({
				nodes: [...committedNodes],
				edges: [...committedEdges],
				hasUnsavedChanges: false,
				isHistoricalView: false,
				viewedVersion: state.activeSession?.active_version || null,
				qualityScorecard: recomputeScorecard(state, committedNodes, committedEdges),
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
		computeDiff: (baseVersion, targetVersion, baseGraph) => {
			if (!baseGraph && baseVersion === targetVersion) {
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

			const currentNodes = get().nodes;
			const currentEdges = get().edges;
			const records: DiffRecord[] = [];

			const baseNodes = baseGraph ? baseGraph.nodes : get().committedNodes;
			const baseEdges = baseGraph ? baseGraph.edges : get().committedEdges;
			const baseNodeMap = new Map(baseNodes.map((n) => [n.concept_uri, n]));
			const targetNodeMap = new Map(
				currentNodes.map((n) => [n.concept_uri, n]),
			);

				// Concepts added in target
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

		setDiffMode: (mode) => {
			set((state) => ({
				diffState: {
					...state.diffState,
					mode,
					active: mode !== "off",
				},
			}));
		},

		commitChanges: async (rationale, fetchImpl = fetch) => {
			if (get().isCommitting) return false;
			const activeSession = get().activeSession;
			if (!activeSession) return false;

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

			const committedNodes = get().committedNodes;
			const currentNodes = get().nodes;
			const committedEdges = get().committedEdges;
			const currentEdges = get().edges;
			const sid = activeSession.session_id;

			// O(1) indexed maps for high performance and LOD graph scaling (SUG-01)
			const committedNodeMap = new Map(
				committedNodes.map((n) => [n.concept_uri, n]),
			);
			const currentNodeMap = new Map(currentNodes.map((n) => [n.concept_uri, n]));
			const committedEdgeMap = new Map(
				committedEdges.map((e) => [e.relationship_uri, e]),
			);
			const currentEdgeMap = new Map(currentEdges.map((e) => [e.relationship_uri, e]));

			// Enforce ONT-EVO-001 strictly additive evolution and block unexpressible edits (MAJ-01)
			// 1. Check for removed concepts
			const removedConcepts = committedNodes.filter(
				(c) => !currentNodeMap.has(c.concept_uri),
			);
			if (removedConcepts.length > 0) {
				get().addToast({
					type: "urn:opendv:error:non-additive-change",
					title: "Non-Additive Change Blocked",
					status: 400,
					detail:
						"Deleting concepts violates ONT-EVO-001 strictly additive evolution. Use discardChanges() or a breaking migration workflow instead.",
					code: "ONT_EVO_NON_ADDITIVE_CHANGE",
				});
				return false;
			}

			// 2. Check for concept property mutations, removed attributes, altered attribute types/descriptions, or removed mappings
			for (const committed of committedNodes) {
				const current = currentNodeMap.get(committed.concept_uri);
				if (!current) continue;

				// Concept-level immutability for existing concepts under additive evolution
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

				if (committed.elm_archetype !== current.elm_archetype) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Archetype Mutation Blocked",
						status: 400,
						detail: `Modifying archetype on '${committed.concept_name}' violates ONT-EVO-001 strictly additive evolution.`,
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				if ((committed.target_hub ?? null) !== (current.target_hub ?? null)) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Target Hub Mutation Blocked",
						status: 400,
						detail: `Altering target hub on '${committed.concept_name}' is not supported in additive evolution deltas.`,
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				if (
					(committed.parent_concept_uri ?? null) !==
					(current.parent_concept_uri ?? null)
				) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Inheritance Hierarchy Mutation Blocked",
						status: 400,
						detail: `Modifying parent concept on '${committed.concept_name}' is not supported in additive evolution deltas.`,
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				// Synonyms check on existing concepts
				const committedSyns = new Set(committed.synonyms || []);
				const currentSyns = new Set(current.synonyms || []);
				if (
					committedSyns.size !== currentSyns.size ||
					[...committedSyns].some((s) => !currentSyns.has(s))
				) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Synonym Mutation Blocked",
						status: 400,
						detail: `Modifying synonyms on existing concept '${committed.concept_name}' cannot be expressed via additive delta operations.`,
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
				if (removedAttrs.length > 0) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Non-Additive Change Blocked",
						status: 400,
						detail:
							"Removing attributes violates ONT-EVO-001 strictly additive evolution. Use a breaking migration workflow instead.",
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
							"Modifying descriptions of existing attributes cannot be expressed via additive evolution deltas.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				const alteredOptionals = (committed.attributes || []).filter((a) => {
					const curr = currentAttrMap.get(a.attribute_name);
					return curr && (curr.is_optional ?? true) !== (a.is_optional ?? true);
				});
				if (alteredOptionals.length > 0) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Attribute Optionality Mutation Blocked",
						status: 400,
						detail:
							"Modifying optionality of existing attributes cannot be expressed via additive evolution deltas.",
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
							"Modifying example values of existing attributes cannot be expressed via additive evolution deltas.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				// External mappings check: cannot remove existing external mappings or alter their properties
				const currentMappingMap = new Map(
					(current.external_mappings || []).map((m) => [m.target_uri, m]),
				);
				const removedMappings = (committed.external_mappings || []).filter(
					(m) => !currentMappingMap.has(m.target_uri),
				);
				if (removedMappings.length > 0) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Removed External Mappings Blocked",
						status: 400,
						detail:
							"Removing reference external mappings violates regulatory stability under ONT-EVO-002.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}

				for (const m of committed.external_mappings || []) {
					const currM = currentMappingMap.get(m.target_uri);
					if (currM) {
						if (
							currM.scheme !== m.scheme ||
							(currM.predicate ?? null) !== (m.predicate ?? null) ||
							(currM.confidence ?? null) !== (m.confidence ?? null) ||
							(currM.pinned ?? false) !== (m.pinned ?? false) ||
							(currM.provenance ?? null) !== (m.provenance ?? null)
						) {
							get().addToast({
								type: "urn:opendv:error:non-additive-change",
								title: "External Mapping Mutation Blocked",
								status: 400,
								detail: `Modifying properties of existing external mapping '${m.target_uri}' cannot be expressed via additive evolution deltas.`,
								code: "ONT_EVO_NON_ADDITIVE_CHANGE",
							});
							return false;
						}
					}
				}
			}

			// 3. Check for removed relationships or modified existing relationships
			const removedEdges = committedEdges.filter(
				(e) => !currentEdgeMap.has(e.relationship_uri),
			);
			if (removedEdges.length > 0) {
				get().addToast({
					type: "urn:opendv:error:non-additive-change",
					title: "Non-Additive Change Blocked",
					status: 400,
					detail:
						"Deleting relationships violates ONT-EVO-001 strictly additive evolution.",
					code: "ONT_EVO_NON_ADDITIVE_CHANGE",
				});
				return false;
			}

			for (const committedEdge of committedEdges) {
				const currentEdge = currentEdgeMap.get(committedEdge.relationship_uri);
				if (!currentEdge) continue;

				if (
					committedEdge.target_link !== currentEdge.target_link ||
					(committedEdge.cardinality ?? null) !==
						(currentEdge.cardinality ?? null) ||
					committedEdge.source_concept_uri !== currentEdge.source_concept_uri ||
					committedEdge.target_concept_uri !== currentEdge.target_concept_uri ||
					(committedEdge.description ?? null) !==
						(currentEdge.description ?? null)
				) {
					get().addToast({
						type: "urn:opendv:error:non-additive-change",
						title: "Relationship Mutation Blocked",
						status: 400,
						detail:
							"Modifying properties or endpoints of existing relationships cannot be expressed via additive evolution deltas.",
						code: "ONT_EVO_NON_ADDITIVE_CHANGE",
					});
					return false;
				}
			}

			// Authoritative pre-commit scorecard recomputation (MIN-01):
			// Do not trust cached or caller-manipulated scorecard; compute directly from current nodes, edges, and valid session waiver
			const authoritativeScorecard = recomputeScorecard(
				get(),
				currentNodes,
				currentEdges,
			);

			// Pre-commit gate on regulatory grounding invariant ONT-EVO-002 (MIN-03)
			const evoCheck = authoritativeScorecard.checks.find(
				(c) => c.rule_id === "ONT-EVO-002",
			);
			if (evoCheck?.status === "FAIL") {
				get().addToast({
					type: "urn:opendv:error:grounding-invariant-failed",
					title: "Regulatory Grounding Invariant Failed",
					status: 400,
					detail:
						"Cannot commit changes while ONT-EVO-002 is failing. Ground all concepts or obtain an authorized steward administrative waiver.",
					code: "ONT_EVO_002_FAILED",
				});
				return false;
			}

			// Pre-commit gate on acyclicity invariant ONT-DAG-001 (MIN-04)
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

			// Added concepts
			for (const node of currentNodes) {
				const committed = committedNodeMap.get(node.concept_uri);
				if (!committed) {
					operations.push({
						op_type: "add_concept",
						concept_uri: node.concept_uri,
						concept_name: node.concept_name,
						elm_archetype: node.elm_archetype || "CORE_BUSINESS_CONCEPT",
						definition: node.definition || "",
						target_hub: node.target_hub ?? undefined,
						parent_concept_uri: node.parent_concept_uri ?? undefined,
						attributes: [], // Emit typed add_attribute operations below instead of string names (MIN-01)
						synonyms:
							node.synonyms && node.synonyms.length > 0
								? node.synonyms
								: undefined,
						external_mappings:
							node.external_mappings && node.external_mappings.length > 0
								? node.external_mappings
								: undefined,
					});
					// Emit typed attributes for new concept per MIN-04
					for (const attr of node.attributes || []) {
						operations.push({
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
					// Refined definition (even if emptied or changed)
					if (node.definition !== committed.definition) {
						operations.push({
							op_type: "refine_definition",
							concept_uri: node.concept_uri,
							definition: node.definition || "",
						});
					}
					// Added attributes on existing concept
					const committedAttrs = new Set(
						(committed.attributes || []).map((a) => a.attribute_name),
					);
					for (const attr of node.attributes || []) {
						if (!committedAttrs.has(attr.attribute_name)) {
							operations.push({
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
					// Added external mappings on existing concept
					const committedMappings = new Set(
						(committed.external_mappings || []).map((m) => m.target_uri),
					);
					for (const mapping of node.external_mappings || []) {
						if (!committedMappings.has(mapping.target_uri)) {
							operations.push({
								op_type: "add_external_mapping",
								concept_uri: node.concept_uri,
								scheme: mapping.scheme || "FIBO",
								target_uri: mapping.target_uri,
								predicate: mapping.predicate || undefined,
								confidence: mapping.confidence ?? undefined,
								pinned: mapping.pinned ?? undefined,
								provenance: mapping.provenance || undefined,
							});
						}
					}
				}
			}

			// Added relationships
			for (const edge of currentEdges) {
				const committed = committedEdgeMap.get(edge.relationship_uri);
				if (!committed) {
					operations.push({
						op_type: "add_relationship",
						relationship_uri: edge.relationship_uri,
						relationship_name: edge.relationship_name,
						target_link: edge.target_link || "lnk_default",
						endpoints: [edge.source_concept_uri, edge.target_concept_uri],
						cardinality: edge.cardinality ?? undefined,
						description: edge.description ?? undefined,
					});
				}
			}

			// Block commits with zero operations (MAJ-04)
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

			const baseVersion = activeSession.active_version || "1.0.0";
			const match = /^(\d+)\.(\d+)\.(\d+)/.exec(baseVersion);
			const major =
				match && match[1] !== undefined ? Number.parseInt(match[1], 10) : 1;
			const minor =
				match && match[2] !== undefined ? Number.parseInt(match[2], 10) : 0;
			const patch =
				match && match[3] !== undefined ? Number.parseInt(match[3], 10) : 0;
			const hasMinor = operations.some(
				(o) => o.op_type === "add_concept" || o.op_type === "add_relationship",
			);
			const targetVersion = hasMinor
				? `${major}.${minor + 1}.0`
				: `${major}.${minor}.${patch + 1}`;

			const deltaId =
				typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
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

			// Snapshot current nodes and edges before fetch (MAJ-02)
			const sentNodes = [...currentNodes];
			const sentEdges = [...currentEdges];

			set({ isCommitting: true });
			try {
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/delta`,
					),
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
				};
				if (get().activeSession?.session_id !== sid) {
					// Session switched while commit was in-flight (MIN-04): record session's new head and version in sessions list
					set((state) => ({
						sessions: state.sessions.map((s) =>
							s.session_id === sid
								? {
										...s,
										head_sha256:
											data.target_sha256 ||
											data.head_sha256 ||
											s.head_sha256,
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

				set((state) => {
					const hasFurtherEdits =
						state.nodes.length !== sentNodes.length ||
						state.edges.length !== sentEdges.length ||
						state.nodes.some((n, i) => n !== sentNodes[i]) ||
						state.edges.some((e, i) => e !== sentEdges[i]);

					const nextSession = state.activeSession
						? {
								...state.activeSession,
								head_sha256:
									data.target_sha256 ||
									data.head_sha256 ||
									state.activeSession.head_sha256,
								active_version:
									data.target_version ||
									data.active_version ||
									state.activeSession.active_version,
							}
						: null;

					const nextSessions = state.sessions.map((s) =>
						s.session_id === sid && nextSession ? nextSession : s,
					);

					const intermediateState: StudioStoreState = {
						...state,
						activeSession: nextSession,
						sessions: nextSessions,
					};

					return {
						hasUnsavedChanges: hasFurtherEdits,
						committedNodes: sentNodes,
						committedEdges: sentEdges,
						activeSession: nextSession,
						sessions: nextSessions,
						viewedVersion: nextSession?.active_version || state.viewedVersion,
						isHistoricalView: false,
						qualityScorecard: recomputeScorecard(
							intermediateState,
							state.nodes,
							state.edges,
						),
					};
				});
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

		submitWaiver: async (rationale, waiverKey, fetchImpl = fetch) => {
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

			if (get().nodes.length === 0) {
				get().addToast({
					type: "urn:opendv:notice:empty-graph",
					title: "Empty Graph",
					status: 400,
					detail: "Cannot request administrative waiver for an empty graph.",
					code: "EMPTY_GRAPH",
				});
				return false;
			}

			const ungroundedUris = get()
				.nodes.filter((n) => n.grounding_status !== "GROUNDED")
				.map((n) => n.concept_uri);

			if (ungroundedUris.length === 0) {
				get().addToast({
					type: "urn:opendv:notice:no-ungrounded",
					title: "No Ungrounded Concepts",
					status: 400,
					detail:
						"All concepts in this session are already grounded. Administrative waiver is not needed.",
					code: "NO_UNGROUNDED_CONCEPTS",
				});
				return false;
			}

			const targetUris = ungroundedUris;
			const sid = activeSession.session_id;

			set({ isSubmittingWaiver: true });
			try {
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/waiver`,
					),
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							session_id: sid,
							base_sha256: activeSession.head_sha256,
							target_concept_uris: targetUris,
							rationale: rationale.trim(),
							waiver_key: waiverKey.trim(),
						}),
					},
				);
				const currentSession = get().activeSession;
				if (!currentSession || currentSession.session_id !== sid) return false;

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
						detail: waiverData.detail || "Server did not authorize grounding waiver.",
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

				const newWaiver = {
					waiver_applied: true,
					waiver_rationale: rationale.trim(),
					waived_concept_uris: serverWaivedUris,
					waived_base_sha256: serverWaivedSha,
				};
				set((state) => {
					const updatedScorecard = computeQualityScorecard(
						state.nodes,
						state.edges,
						{
							grandfatheredUris:
								state.activeSession?.grandfathered_concept_uris || [],
							waiverApplied: true,
							waivedUris: serverWaivedUris,
						},
					);
					return {
						sessionWaivers: {
							...state.sessionWaivers,
							[sid]: newWaiver,
						},
						qualityScorecard: {
							...updatedScorecard,
							waiver_applied: true,
							waiver_rationale: rationale.trim(),
							waived_concept_uris: serverWaivedUris,
							waived_base_sha256: serverWaivedSha,
							feedback_rating: state.qualityScorecard.feedback_rating,
							feedback_comment: state.qualityScorecard.feedback_comment,
							feedback_submitted: state.qualityScorecard.feedback_submitted,
						},
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

		submitFeedback: async (rating, comment, fetchImpl = fetch) => {
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
			try {
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/feedback`,
					),
					{
						method: "POST",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							session_id: sid,
							rating,
							comment: comment.trim(),
							submitted_at: new Date().toISOString(),
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

			const ver = version || activeSession.active_version;
			const url = resolveUrl(
				`/api/v1/ontology/studio/sessions/${encodeURIComponent(activeSession.session_id)}/export?export_format=${encodeURIComponent(format)}&version=${encodeURIComponent(ver)}`,
			);
			const res = await fetchImpl(url);
			if (!res.ok) {
				const err = (await res.json().catch(() => ({}))) as { detail?: string };
				throw new Error(err.detail || `Export failed with HTTP ${res.status}`);
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
			if (!shaRegex.test(req.head_sha256) || !shaRegex.test(req.workspace_sha256)) {
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
			set({ isPromoting: true });
			try {
				const res = await fetchImpl(
					resolveUrl(
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/promote`,
					),
					{
						method: "POST",
						headers: {
							"Content-Type": "application/json",
							"If-Match": `"${req.head_sha256}"`,
							"X-OpenDV-Workspace-If-Match": `"${req.workspace_sha256}"`,
						},
						body: JSON.stringify(req),
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
					// Session switched while promote was in-flight (MIN-04): record session's new head and version in sessions list
					if (promoData.promoted_version || promoData.head_sha256) {
						set((state) => ({
							sessions: state.sessions.map((s) =>
								s.session_id === sid
									? {
											...s,
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

				const newCommit: SessionCommit = {
					commit_id: `commit-${Date.now()}`,
					parent_sha256: req.head_sha256,
					new_sha256: newSha,
					version: newVersion,
					rationale: req.rationale,
					committed_at: new Date().toISOString(),
				};

				set((state) => ({
					activeSession: state.activeSession
						? {
								...state.activeSession,
								active_version: newVersion,
								head_sha256: newSha,
								commits: [...(state.activeSession.commits || []), newCommit],
							}
						: null,
					sessions: state.sessions.map((s) =>
						s.session_id === sid
							? {
									...s,
									active_version: newVersion,
									head_sha256: newSha,
									commits: [...(s.commits || []), newCommit],
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
			set({
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
				toasts: [],
			});
		},
	}));

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

	return useSyncExternalStore(
		studioStore.subscribe,
		getSnapshot,
		getSnapshot,
	);
}
