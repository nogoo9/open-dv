/**
 * Open-DV Ontology Construction Studio Workbench State Types
 * (apps/opendv-web-workbench/src/ontology/store/types.ts)
 *
 * Conforms to:
 * - docs/20-ontology-studio-spec.md (§6 Web Workbench Specifications, §6.1-§6.4)
 * - schemas/v1/openapi.json
 * - schemas/v1/error-codes.json
 */

export interface SessionCommit {
	commit_id: string;
	parent_sha256?: string | undefined;
	new_sha256: string;
	version: string;
	rationale: string;
	committed_at: string;
	author?: string | undefined;
}

export interface StudioSessionSummary {
	session_id: string;
	domain_namespace: string;
	enterprise_owner: string;
	status: string;
	active_version: string;
	head_sha256?: string | undefined;
	created_at: string;
	updated_at: string;
	sources_count?: number | undefined;
	proposals_count?: number | undefined;
	versions_count?: number | undefined;
	grandfathered_concept_uris?: string[] | undefined;
	commits?: SessionCommit[] | undefined;
	from_workspace?: string | undefined;
	parent_sha256?: string | null | undefined;
	description?: string | null | undefined;
	metadata?: Record<string, unknown> | null | undefined;
	reference_enumerations?: Array<Record<string, unknown>> | undefined;
	standards_references?: string[] | undefined;
	decision_refs?: string[] | undefined;
}

export interface CreateSessionRequest {
	session_id?: string | undefined;
	domain_namespace?: string | undefined;
	enterprise_owner?: string | undefined;
	description?: string | undefined;
	from_workspace?: string | undefined;
}

export interface SourceDocument {
	source_id: string;
	source_sha256: string;
	file_name: string;
	size_bytes: number;
	mime_type: string;
	do_not_send_to_agent: boolean;
	confidential?: boolean | undefined;
	tombstoned?: boolean | undefined;
	uploaded_at?: string | undefined;
	status?: string | undefined;
}

export interface EvidenceChunk {
	chunk_id: string;
	source_id: string;
	source_file: string;
	text_snippet: string;
	token_count: number;
	layout_complexity: "SIMPLE" | "TABULAR" | "MULTI_COLUMN" | "COMPLEX";
	start_char?: number | undefined;
	end_char?: number | undefined;
}

export interface ParsingState {
	status: "idle" | "parsing" | "completed" | "failed";
	progress_pct: number;
	active_job_id: string | null;
	stage: string;
	ocr_mode: "auto" | "force" | "off";
	evidence_chunks: EvidenceChunk[];
	chunks_count?: number | undefined;
	error: string | null;
}

export interface ClarificationRationaleOption {
	id: string;
	label: string;
	description?: string | undefined;
}

export interface ClarificationEvidenceCitation {
	source_file: string;
	chunk_id: string;
	excerpt: string;
}

export interface ClarificationQuestion {
	question_id: string;
	concept_name: string;
	question_text: string;
	certainty: "HIGH" | "MEDIUM" | "LOW";
	rationale_options: ClarificationRationaleOption[];
	evidence_citations: ClarificationEvidenceCitation[];
	status: "PENDING" | "ANSWERED";
	answered_at?: string | undefined;
	selected_option_ids?: string[] | undefined;
	custom_answer_text?: string | undefined;
}

export interface ClarificationAnswer {
	question_id: string;
	selected_option_ids: string[];
	custom_answer_text?: string | undefined;
	answered_at?: string | undefined;
}

export interface ConceptAttribute {
	attribute_name: string;
	data_type: string;
	description?: string | null | undefined;
	is_optional?: boolean | null | undefined;
	example_values?: string[] | null | undefined;
	deprecated?: boolean | null | undefined;
	metadata?: Record<string, unknown> | null | undefined;
	source_hints?: string[] | null | undefined;
}

export interface ExternalMapping {
	scheme: string;
	target_uri: string;
	confidence?: number | null | undefined;
	mapping_confidence?: number | null | undefined;
	predicate?: string | null | undefined;
	rationale?: string | null | undefined;
	pinned?: boolean | null | undefined;
	provenance?: string | null | undefined;
	reference_pack_id?: string | null | undefined;
	reference_pack_sha256?: string | null | undefined;
}

export interface ConceptNode {
	id: string;
	concept_uri: string;
	concept_name: string;
	elm_archetype: string;
	definition: string;
	description?: string | null | undefined;
	target_hub?: string | null | undefined;
	parent_concept_uri?: string | null | undefined;
	confidence?: number | undefined;
	grounding_status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED";
	attributes: ConceptAttribute[];
	synonyms: string[];
	external_mappings: ExternalMapping[];
	deprecated?: boolean | null | undefined;
	metadata?: Record<string, unknown> | null | undefined;
	source_hints?: string[] | null | undefined;
	governance_constraints?: Record<string, unknown> | null | undefined;
	key_authority_tiers?: Record<string, unknown> | null | undefined;
	x?: number | undefined;
	y?: number | undefined;
}

export interface RelationshipEdge {
	id: string;
	relationship_uri: string;
	relationship_name: string;
	target_link: string;
	source_concept_uri: string;
	target_concept_uri: string;
	endpoints?: string[] | undefined;
	grain_semantics?: string | null | undefined;
	cardinality?: string | null | undefined;
	description?: string | null | undefined;
	deprecated?: boolean | null | undefined;
	metadata?: Record<string, unknown> | null | undefined;
	source_hints?: string[] | null | undefined;
	yagni_cost_guard_policy?: Record<string, unknown> | null | undefined;
}

export interface DiffRecord {
	item_type: "concept" | "relationship" | "attribute" | "mapping";
	change_type: "added" | "deleted" | "modified";
	identifier: string;
	label: string;
	details: string;
	is_breaking: boolean;
}

export interface DiffSummary {
	added_count: number;
	deleted_count: number;
	modified_count: number;
	breaking_count: number;
}

export interface DiffState {
	active: boolean;
	mode: "unified" | "side-by-side" | "off";
	base_version: string;
	target_version: string;
	records: DiffRecord[];
	summary: DiffSummary;
}

export interface QualityCheck {
	rule_id: string;
	rule_name: string;
	status: "PASS" | "WARN" | "FAIL";
	details: string;
}

export interface QualityScorecard {
	grounding_score: number;
	deployed_contracts_ok: boolean;
	dag_acyclic: boolean;
	yagni_compliant: boolean;
	checks: QualityCheck[];
	waiver_applied: boolean;
	waiver_rationale: string;
	waived_concept_uris?: string[] | undefined;
	waived_base_sha256?: string | undefined;
	feedback_rating: number; // 1-5
	feedback_comment: string;
	feedback_concept_uri?: string | null | undefined;
	feedback_submitted?: boolean | undefined;
}

export interface CandidateProposalSummary {
	proposal_id: string;
	session_id?: string | undefined;
	status: string;
	base_version: string;
	base_sha256?: string | undefined;
	target_version?: string | undefined;
	proposer?: string | undefined;
	rationale?: string | undefined;
	operations_count?: number | undefined;
	ops_count?: number | undefined;
	operations?: Array<Record<string, unknown>> | undefined;
	created_at?: string | undefined;
}

export interface StudioEventLogEntry {
	id: string;
	timestamp: string;
	job_id?: string | undefined;
	stage: string;
	message: string;
	progress_pct?: number | undefined;
	level?: "info" | "success" | "warn" | "error" | undefined;
}

export interface LayoutState {
	leftDrawerTab:
		| "sessions"
		| "sources"
		| "questions"
		| "history"
		| "settings"
		| "quality";
	leftDrawerOpen: boolean;
	rightDrawerOpen: boolean;
	rightDrawerTab?: "concept" | "proposal" | undefined;
	bottomDockOpen: boolean;
	activeModal: "export" | "promote" | "diff" | "new_session" | null;
	lodTier: 1 | 2 | 3;
	minimapVisible: boolean;
	searchTerm: string;
	layoutTriggerCount?: number | undefined;
}

export interface StudioErrorToast {
	id: string;
	type: string;
	title: string;
	status: number;
	detail: string;
	code: string;
	timestamp: string;
}

export type StudioMode = "local_standalone" | "remote_platform";

export type StudioUserRole = "viewer" | "editor" | "automated" | "admin";
