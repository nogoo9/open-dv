/**
 * Open-DV Ontology Construction Studio & Unified Wizard State Types
 * (apps/opendv-web-workbench/src/ontology/store/types.ts)
 */

import type { ForeignKeyHintLike } from "../../explorer/builder/CatalogTablePickerModal";

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
	wizard_schema_version?: number | undefined;
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

export interface ImportOntologyRequest {
	session_id?: string | undefined;
	domain_namespace?: string | undefined;
	enterprise_owner?: string | undefined;
	format: "yaml" | "json" | "ttl";
	content: string;
	expected_sha256?: string | undefined;
	description?: string | undefined;
}

export interface StarterPackMetadata {
	pack_id: string;
	display_name: string;
	concept_count: number;
	relationship_count: number;
	description?: string | undefined;
}

export interface SourceDocument {
	source_id: string;
	source_sha256: string;
	file_name: string;
	filename?: string | undefined;
	size_bytes: number;
	byte_count?: number | undefined;
	mime_type: string;
	do_not_send_to_agent: boolean;
	confidential?: boolean | undefined;
	tombstoned?: boolean | undefined;
	uploaded_at?: string | undefined;
	status?: string | undefined;
	status_badge?: string | undefined;
	ocr_mode?: string | undefined;
	scope_summary?: string | undefined;
}

export interface ConnectorSourceInput {
	adapter: string;
	uri?: string | undefined;
	table?: string | undefined;
	namespace?:
		| string
		| { catalog?: string | undefined; schema_name?: string | undefined }
		| undefined;
	credentials_ref?: string | undefined;
	options?: Record<string, unknown> | undefined;
	description?: string | undefined;
	cas_hash?: string | undefined;
	source_name?: string | undefined;
}

export interface EvidenceColumnProfile {
	column_name: string;
	sql_type: string;
	is_primary_key: boolean;
	surrogate_hint: boolean;
	foreign_key: string | ForeignKeyHintLike | null;
	null_rate: number;
	shannon_entropy: number;
}

export interface EvidenceTableProfile {
	table_id: string;
	table_name: string;
	headers: string[];
	sample_rows: string[][];
	columns: EvidenceColumnProfile[];
	fibo_gap_hints?: Array<Record<string, unknown>> | undefined;
}

export interface EvidenceChunk {
	chunk_id: string;
	source_id?: string | undefined;
	document_id?: string | undefined;
	source_file?: string | undefined;
	page_number?: number | undefined;
	text_snippet?: string | undefined;
	text?: string | undefined;
	token_count?: number | undefined;
	confidence?: number | undefined;
	is_table?: boolean | undefined;
	table_id?: string | null | undefined;
	table_data?: { headers: string[]; rows: string[][] } | null | undefined;
	layout_complexity?:
		| "SIMPLE"
		| "TABULAR"
		| "MULTI_COLUMN"
		| "COMPLEX"
		| undefined;
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
	fibo_uri?: string | undefined;
	evidence_citations?: Array<Record<string, unknown>> | undefined;
}

export interface ClarificationEvidenceCitation {
	source_file: string;
	chunk_id: string;
	excerpt: string;
	evidence_type?: string | undefined;
	source_label?: string | undefined;
}

export interface ClarificationQuestion {
	question_id: string;
	concept_name: string;
	focus_concept?: string | undefined;
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

export interface EvidenceRef {
	evidence_type: string;
	reference_uri?: string | null | undefined;
	snippet?: string | null | undefined;
	confidence?: number | null | undefined;
	doc_sha256?: string | null | undefined;
	char_start?: number | null | undefined;
	char_end?: number | null | undefined;
	bronze_table?: string | null | undefined;
	column_name?: string | null | undefined;
	source_label?: string | null | undefined;
	chunk_id?: string | null | undefined;
	page_number?: number | null | undefined;
	table_id?: string | null | undefined;
	fibo_uri?: string | null | undefined;
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
	evidence_refs?: EvidenceRef[] | null | undefined;
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
	grounding_origin?: string | null | undefined;
	evidence_refs?: EvidenceRef[] | null | undefined;
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
	evidence_refs?: EvidenceRef[] | null | undefined;
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
	feedback_rating: number;
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

export interface StagedCandidateDTO {
	candidate_id: string;
	concept_name: string;
	suggested_hub?: string | undefined;
	top_match_uri?: string | null | undefined;
	top_match_score?: number | undefined;
	reason_held_back?: string | undefined;
	status: "held_back" | "promoted" | "rejected";
	evidence_refs?: Array<Record<string, unknown>> | undefined;
	attributes?: Array<Record<string, unknown>> | undefined;
	dependent_links?: Array<Record<string, unknown>> | undefined;
}

export interface ResolvedCitationItem {
	evidence_type: string;
	citation: Record<string, unknown>;
	highlighted_span?: string | null | undefined;
	surrounding_text?: string | null | undefined;
	table_profile?:
		| {
				table_id: string;
				table_name: string;
				column_name: string;
				column_profile?: Record<string, unknown> | null | undefined;
				sample_rows?: unknown[][] | undefined;
		  }
		| null
		| undefined;
	fibo_definition?:
		| {
				uri: string;
				label: string;
				definition: string;
				module?: string | undefined;
				parents?: string[] | undefined;
				match_score?: number | undefined;
		  }
		| null
		| undefined;
}

export interface FiboQaResult {
	question: string;
	answer: string;
	citations: Array<Record<string, unknown>>;
	suggested_actions: Array<Record<string, unknown>>;
}

export interface BudgetHoldInfo {
	job_id: string;
	reason: string;
	total_tokens: number;
	cost_usd: number;
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
	rightDrawerTab?: "concept" | "proposal" | "blueprint" | undefined;
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

export interface ProposalDraftEdit {
	rationale: string;
	operations: Array<Record<string, unknown>>;
}

export interface QualityScorecardOptions {
	grandfatheredUris?: string[];
	waiverApplied?: boolean;
	waivedUris?: string[];
	committedNodes?: ConceptNode[];
	waivedSnapshots?: Record<string, ConceptNode>;
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

export type StudioMode = "local_standalone" | "remote_platform";

export type StudioUserRole = "viewer" | "editor" | "automated" | "admin";
