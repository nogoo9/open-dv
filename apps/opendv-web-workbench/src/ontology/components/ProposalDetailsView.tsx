/**
 * Right-Panel Candidate Proposal Workspace Component
 * (apps/opendv-web-workbench/src/ontology/components/ProposalDetailsView.tsx)
 *
 * Manages the full end-to-end Candidate Proposal lifecycle inside the right panel:
 * - Explains what Candidate Proposals (`OntologyProposal`) are and their governance model
 * - Generates/refreshes Clarification Q&A and lets stewards resolve ambiguity questions inline
 * - Constructs or refreshes proposals from resolved clarifications and ingested sources
 * - Edits proposal rationale and staged delta operations (persisted across tab switches/refresh)
 *   and saves updated revisions as new `PENDING` proposals
 * - Author brand-new draft proposals via `+ New Draft Proposal`
 * - Ratifies (`Accept Proposal`), `Rebase`s, or `Reject`s proposals
 */

import type React from "react";
import { useEffect, useState } from "react";
import {
	CONCEPT_ARCHETYPES,
	CONCEPT_URN_REGEX,
	CONFIDENCE_DECIMAL_PLACES,
	DEFAULT_CONCEPT_ARCHETYPE,
	DEFAULT_MAPPING_CONFIDENCE_INPUT,
	DEFAULT_MAPPING_PREDICATE,
	DEFAULT_MAPPING_SCHEME,
	GENESIS_SEMVER_VERSION,
	MAPPING_CONFIDENCE_STEP,
	MAX_MAPPING_CONFIDENCE,
	MIN_MAPPING_CONFIDENCE,
	NEW_PROPOSAL_DRAFT_KEY,
	PERCENTAGE_MULTIPLIER,
	SHA256_SHORT_DISPLAY_CHARS,
	SKOS_PREDICATES,
	TARGET_HUB_REGEX,
} from "../constants";
import { useStudioStore } from "../store/studio-store";
import type { CandidateProposalSummary } from "../store/types";
import {
	buildUniqueConceptIdentifiers,
	createDraftOpId,
	extractIriTerminalLabel,
} from "../utils/identifiers";
import {
	ClarificationQuestionsList,
	ProposalRatificationControls,
} from "./ProposalWorkspaceShared";

export function summarizeProposalOperations(
	operations: ReadonlyArray<Record<string, unknown>>,
): {
	concepts: number;
	mappings: number;
	relationships: number;
	attributes: number;
	other: number;
} {
	const counts = {
		concepts: 0,
		mappings: 0,
		relationships: 0,
		attributes: 0,
		other: 0,
	};
	for (const op of operations) {
		const opType = String(op.op_type || "").toLowerCase();
		if (opType === "add_concept") counts.concepts += 1;
		else if (opType === "add_external_mapping") counts.mappings += 1;
		else if (opType === "add_relationship") counts.relationships += 1;
		else if (opType === "add_attribute") counts.attributes += 1;
		else counts.other += 1;
	}
	return counts;
}

export function getEditableOpPrimaryField(opType: string): {
	field: string;
	label: string;
	aliasKey?: string;
} {
	switch (opType) {
		case "add_relationship":
			return { field: "relationship_name", label: "Relationship Name" };
		case "add_attribute":
			return { field: "attribute_name", label: "Attribute Name" };
		case "add_synonym":
			return { field: "synonym", label: "Synonym" };
		case "refine_definition":
			return {
				field: "refined_description",
				label: "Refined Definition",
				aliasKey: "definition",
			};
		case "revise_description":
			return { field: "description", label: "Revised Description" };
		case "deprecate_concept":
		case "remove_concept":
		case "remove_attribute":
		case "deprecate_relationship":
		case "remove_relationship":
			return { field: "reason", label: "Removal / Deprecation Reason" };
		default:
			return { field: "rationale", label: "Operation Rationale" };
	}
}

export function validateProposalDraftOperations(
	operations: ReadonlyArray<Record<string, unknown>>,
): string | null {
	if (operations.length === 0) {
		return "Add at least one delta operation before saving a candidate proposal.";
	}
	for (let i = 0; i < operations.length; i += 1) {
		const op = operations[i];
		if (!op) continue;
		const opType = String(op.op_type || "").trim();
		const label = `Operation #${i + 1} (${opType || "unknown"})`;

		if (opType === "add_concept") {
			const conceptName =
				typeof op.concept_name === "string" ? op.concept_name.trim() : "";
			const conceptUri =
				typeof op.concept_uri === "string" ? op.concept_uri.trim() : "";
			const targetHub =
				typeof op.target_hub === "string" ? op.target_hub.trim() : "";
			if (!conceptName) {
				return `${label}: Concept Name is required.`;
			}
			if (!CONCEPT_URN_REGEX.test(conceptUri)) {
				return `${label}: Concept URN '${conceptUri}' must match pattern urn:ontology:entity:<slug>.`;
			}
			if (targetHub && !TARGET_HUB_REGEX.test(targetHub)) {
				return `${label}: Target Hub '${targetHub}' must match pattern hub_<slug>.`;
			}
		} else if (opType === "add_external_mapping") {
			const conceptUri =
				typeof op.concept_uri === "string" ? op.concept_uri.trim() : "";
			const targetUri =
				typeof op.target_uri === "string" ? op.target_uri.trim() : "";
			const conf =
				typeof op.mapping_confidence === "number"
					? op.mapping_confidence
					: typeof op.confidence === "number"
						? op.confidence
						: Number.NaN;
			if (!CONCEPT_URN_REGEX.test(conceptUri)) {
				return `${label}: Concept URN '${conceptUri}' must match pattern urn:ontology:entity:<slug>.`;
			}
			if (!targetUri) {
				return `${label}: External Target URI is required.`;
			}
			if (
				!Number.isFinite(conf) ||
				conf < MIN_MAPPING_CONFIDENCE ||
				conf > MAX_MAPPING_CONFIDENCE
			) {
				return `${label}: Mapping confidence must be between ${MIN_MAPPING_CONFIDENCE} and ${MAX_MAPPING_CONFIDENCE}.`;
			}
		} else if (opType === "add_synonym") {
			const syn = typeof op.synonym === "string" ? op.synonym.trim() : "";
			if (!syn) {
				return `${label}: Synonym must be non-empty.`;
			}
		} else if (opType === "add_attribute") {
			const attrName =
				typeof op.attribute_name === "string" ? op.attribute_name.trim() : "";
			if (!attrName) {
				return `${label}: Attribute Name must be non-empty.`;
			}
		} else if (opType === "add_relationship") {
			const relName =
				typeof op.relationship_name === "string"
					? op.relationship_name.trim()
					: "";
			if (!relName) {
				return `${label}: Relationship Name must be non-empty.`;
			}
		}
	}
	return null;
}

function cloneOperations(
	ops: ReadonlyArray<Record<string, unknown>> | undefined,
): Array<Record<string, unknown>> {
	if (!Array.isArray(ops)) return [];
	return ops.map((op) => ({ ...op }));
}

export interface ProposalDetailsViewProps {
	proposal?: CandidateProposalSummary | undefined;
	opFilter: string;
	onOpFilterChange: (value: string) => void;
	onSelectProposal: (proposalId: string) => void;
	onInspectConcept: (conceptUri: string) => void;
	onClose: () => void;
}

export const ProposalDetailsView: React.FC<ProposalDetailsViewProps> = ({
	proposal,
	opFilter,
	onOpFilterChange,
	onSelectProposal,
	onInspectConcept,
	onClose,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const userRole = useStudioStore((s) => s.userRole);
	const clarifications = useStudioStore((s) => s.clarifications);
	const proposals = useStudioStore((s) => s.proposals);
	const nodes = useStudioStore((s) => s.nodes);
	const committedNodes = useStudioStore((s) => s.committedNodes);
	const proposalDrafts = useStudioStore((s) => s.proposalDrafts);
	const isClarifying = useStudioStore((s) => s.isClarifying);
	const isConstructing = useStudioStore((s) => s.isConstructing);
	const isLoadingProposals = useStudioStore((s) => s.isLoadingProposals);
	const isLoadingClarifications = useStudioStore(
		(s) => s.isLoadingClarifications,
	);
	const startClarify = useStudioStore((s) => s.startClarify);
	const startConstruct = useStudioStore((s) => s.startConstruct);
	const loadProposals = useStudioStore((s) => s.loadProposals);
	const loadClarifications = useStudioStore((s) => s.loadClarifications);
	const acceptProposal = useStudioStore((s) => s.acceptProposal);
	const rejectProposal = useStudioStore((s) => s.rejectProposal);
	const rebaseProposal = useStudioStore((s) => s.rebaseProposal);
	const saveEditedProposal = useStudioStore((s) => s.saveEditedProposal);
	const setProposalDraft = useStudioStore((s) => s.setProposalDraft);
	const clearProposalDraft = useStudioStore((s) => s.clearProposalDraft);

	const canHumanRatify = userRole === "editor" || userRole === "admin";
	const canTriggerJobs = userRole !== "viewer";
	const isNonPending = Boolean(
		proposal?.status && proposal.status !== "PENDING",
	);
	const canEditDraft = canTriggerJobs && (!proposal || !isNonPending);

	const draftKey = proposal?.proposal_id ?? NEW_PROPOSAL_DRAFT_KEY;
	const storedDraft = proposalDrafts[draftKey];
	const initialOps = cloneOperations(proposal?.operations);
	const initialRationale = proposal?.rationale || "";
	const draftRationale = storedDraft ? storedDraft.rationale : initialRationale;
	const draftOps = storedDraft ? storedDraft.operations : initialOps;

	// Ratification state
	const [acceptRationale, setAcceptRationale] = useState("");
	const [selfAcceptOverride, setSelfAcceptOverride] = useState(false);
	const [lowConfidenceWaiver, setLowConfidenceWaiver] = useState(false);

	// Q&A section toggle (open by default so users can manage Q&A in-tab immediately)
	const [qaExpanded, setQaExpanded] = useState(true);

	const [editingOpIndex, setEditingOpIndex] = useState<number | null>(null);
	const [confidenceInputs, setConfidenceInputs] = useState<
		Record<number, string>
	>({});
	const [isSavingDraft, setIsSavingDraft] = useState(false);
	const [draftError, setDraftError] = useState<string | null>(null);

	// New operation builder state
	const [newOpMode, setNewOpMode] = useState<"concept" | "mapping" | null>(
		null,
	);
	const [newConceptName, setNewConceptName] = useState("");
	const [newConceptArchetype, setNewConceptArchetype] = useState<string>(
		DEFAULT_CONCEPT_ARCHETYPE,
	);
	const [newConceptDefinition, setNewConceptDefinition] = useState("");

	const [newMappingConceptUri, setNewMappingConceptUri] = useState("");
	const [newMappingScheme, setNewMappingScheme] = useState<string>(
		DEFAULT_MAPPING_SCHEME,
	);
	const [newMappingPredicate, setNewMappingPredicate] = useState<string>(
		DEFAULT_MAPPING_PREDICATE,
	);
	const [newMappingTargetUri, setNewMappingTargetUri] = useState("");
	const [newMappingConfidence, setNewMappingConfidence] = useState<string>(
		DEFAULT_MAPPING_CONFIDENCE_INPUT,
	);

	const proposalId = proposal?.proposal_id;
	useEffect(() => {
		setEditingOpIndex(null);
		setConfidenceInputs({});
		setDraftError(null);
		if (proposalId !== undefined) {
			setAcceptRationale("");
			setSelfAcceptOverride(false);
			setLowConfidenceWaiver(false);
		}
	}, [proposalId]);

	const commitDraftUpdate = (
		nextRationale: string,
		nextOps: Array<Record<string, unknown>>,
	) => {
		if (
			nextRationale === initialRationale &&
			JSON.stringify(nextOps) === JSON.stringify(initialOps)
		) {
			clearProposalDraft(draftKey);
		} else {
			setProposalDraft(draftKey, {
				rationale: nextRationale,
				operations: nextOps,
			});
		}
	};

	const hasDraftChanges =
		draftRationale !== initialRationale ||
		JSON.stringify(draftOps) !== JSON.stringify(initialOps);

	const opsCount =
		draftOps.length > 0 || proposal?.proposal_id
			? draftOps.length
			: (proposal?.operations_count ?? proposal?.ops_count ?? 0);
	const counts = summarizeProposalOperations(draftOps);

	const pendingQaCount = clarifications.filter(
		(q) => q.status === "PENDING",
	).length;
	const answeredQaCount = clarifications.filter(
		(q) => q.status === "ANSWERED",
	).length;

	const normalizedFilter = opFilter.trim().toLowerCase();
	const indexedDraftOps = draftOps.map((op, originalIdx) => ({
		op,
		originalIdx,
	}));
	const filteredOps = normalizedFilter
		? indexedDraftOps.filter(({ op }) => {
				const haystack = [
					op.op_type,
					op.concept_name,
					op.concept_uri,
					op.relationship_name,
					op.relationship_uri,
					op.target_hub,
					op.target_link,
					op.attribute_name,
					op.synonym,
					op.refined_description,
					op.description,
					op.predicate,
					op.target_uri,
					op.elm_archetype,
					op.scheme,
					op.definition,
					op.rationale,
				]
					.filter((v): v is string => typeof v === "string" && v.length > 0)
					.join(" ")
					.toLowerCase();
				return haystack.includes(normalizedFilter);
			})
		: indexedDraftOps;

	const nodeUriSet = new Set(nodes.map((n) => n.concept_uri));
	const availableConceptUris = Array.from(
		new Set([
			...nodes.map((n) => n.concept_uri),
			...draftOps
				.map((op) => (typeof op.concept_uri === "string" ? op.concept_uri : ""))
				.filter(Boolean),
		]),
	);

	const handleUpdateOpField = (
		index: number,
		field: string,
		value: unknown,
		aliasToRemove?: string,
	) => {
		const nextOps = draftOps.map((item, idx) => {
			if (idx !== index) return item;
			const updated = { ...item, [field]: value };
			if (aliasToRemove && aliasToRemove in updated) {
				delete updated[aliasToRemove];
			}
			return updated;
		});
		commitDraftUpdate(draftRationale, nextOps);
		setDraftError(null);
	};

	const handleUpdateMappingConfidence = (index: number, rawValue: string) => {
		setConfidenceInputs((prev) => ({ ...prev, [index]: rawValue }));
		if (!rawValue.trim()) {
			const nextOps = draftOps.map((item, idx) => {
				if (idx !== index) return item;
				const {
					mapping_confidence: _removedMappingConfidence,
					confidence: _removedConfidence,
					...rest
				} = item;
				return rest;
			});
			commitDraftUpdate(draftRationale, nextOps);
			setDraftError(null);
			return;
		}
		const parsed = Number.parseFloat(rawValue);
		if (!Number.isFinite(parsed)) {
			return;
		}
		const clamped = Number(
			Math.min(
				MAX_MAPPING_CONFIDENCE,
				Math.max(MIN_MAPPING_CONFIDENCE, parsed),
			).toFixed(CONFIDENCE_DECIMAL_PLACES),
		);
		const nextOps = draftOps.map((item, idx) =>
			idx === index
				? {
						...item,
						mapping_confidence: clamped,
						confidence: clamped,
					}
				: item,
		);
		commitDraftUpdate(draftRationale, nextOps);
		setDraftError(null);
	};

	const handleRemoveOp = (index: number) => {
		const nextOps = draftOps.filter((_, idx) => idx !== index);
		commitDraftUpdate(draftRationale, nextOps);
		if (editingOpIndex === index) {
			setEditingOpIndex(null);
		} else if (editingOpIndex !== null && editingOpIndex > index) {
			setEditingOpIndex(editingOpIndex - 1);
		}
		setConfidenceInputs({});
		setDraftError(null);
	};

	const handleAddConceptOp = (e: React.FormEvent) => {
		e.preventDefault();
		const cleanName = newConceptName.trim();
		if (!cleanName) return;

		const draftConceptNodes = draftOps
			.filter((op) => typeof op.concept_uri === "string")
			.map((op) => ({
				concept_uri: String(op.concept_uri),
				target_hub: typeof op.target_hub === "string" ? op.target_hub : null,
			}));
		const { conceptUri, targetHub } = buildUniqueConceptIdentifiers(
			cleanName,
			[...nodes, ...draftConceptNodes],
			committedNodes,
		);

		const nextOps = [
			...draftOps,
			{
				op_id: createDraftOpId(),
				op_type: "add_concept",
				concept_uri: conceptUri,
				concept_name: cleanName,
				elm_archetype: newConceptArchetype,
				definition: newConceptDefinition.trim(),
				target_hub: targetHub,
			},
		];
		commitDraftUpdate(draftRationale, nextOps);
		setNewConceptName("");
		setNewConceptDefinition("");
		setNewOpMode(null);
		setDraftError(null);
	};

	const handleAddMappingOp = (e: React.FormEvent) => {
		e.preventDefault();
		const targetConceptUri = (
			newMappingConceptUri.trim() ||
			availableConceptUris[0] ||
			""
		).trim();
		const cleanTargetUri = newMappingTargetUri.trim();
		if (!CONCEPT_URN_REGEX.test(targetConceptUri)) {
			setDraftError(
				"Concept URN must match pattern urn:ontology:entity:<slug> (pick from the concept suggestions or add a concept first).",
			);
			return;
		}
		if (!cleanTargetUri) {
			setDraftError("External Target URI is required for a mapping operation.");
			return;
		}
		const conf = Number.parseFloat(newMappingConfidence);
		if (
			!Number.isFinite(conf) ||
			conf < MIN_MAPPING_CONFIDENCE ||
			conf > MAX_MAPPING_CONFIDENCE
		) {
			setDraftError(
				`Mapping confidence must be between ${MIN_MAPPING_CONFIDENCE} and ${MAX_MAPPING_CONFIDENCE}.`,
			);
			return;
		}

		const roundedConf = Number(conf.toFixed(CONFIDENCE_DECIMAL_PLACES));
		const nextOps = [
			...draftOps,
			{
				op_id: createDraftOpId(),
				op_type: "add_external_mapping",
				concept_uri: targetConceptUri,
				scheme: newMappingScheme.trim() || DEFAULT_MAPPING_SCHEME,
				predicate: newMappingPredicate,
				target_uri: cleanTargetUri,
				mapping_confidence: roundedConf,
				confidence: roundedConf,
			},
		];
		commitDraftUpdate(draftRationale, nextOps);
		setNewMappingTargetUri("");
		setNewOpMode(null);
		setDraftError(null);
	};

	const handleResetDraft = () => {
		clearProposalDraft(draftKey);
		setEditingOpIndex(null);
		setConfidenceInputs({});
		setDraftError(null);
	};

	const handleSaveProposalRevision = async () => {
		if (!activeSession || !canEditDraft) return;
		const validationError = validateProposalDraftOperations(draftOps);
		if (validationError) {
			setDraftError(validationError);
			return;
		}
		setIsSavingDraft(true);
		setDraftError(null);
		try {
			await saveEditedProposal(
				proposal?.proposal_id ?? NEW_PROPOSAL_DRAFT_KEY,
				{
					rationale:
						draftRationale.trim() ||
						`Steward candidate proposal revision (${draftOps.length} ops)`,
					operations: draftOps,
				},
			);
		} finally {
			setIsSavingDraft(false);
		}
	};

	return (
		<div
			className="proposal-details-panel"
			data-testid="proposal-details-panel"
		>
			<div className="drawer-header">
				<div className="drawer-title-group">
					<span className="drawer-badge">CANDIDATE PROPOSAL</span>
					<h3
						className="drawer-title"
						title={proposal?.proposal_id || "New Candidate Proposal Draft"}
					>
						{proposal ? proposal.proposal_id : "New Candidate Proposal Draft"}
					</h3>
					{proposal && (
						<div className="panel-badges">
							<span className="badge" data-testid="proposal-detail-ops-badge">
								{opsCount} ops
							</span>
							<span
								className="badge"
								data-testid="proposal-detail-status-badge"
							>
								{proposal.status}
							</span>
						</div>
					)}
				</div>
				<button
					type="button"
					className="btn-close"
					onClick={onClose}
					aria-label="Return to Ontology Viewer"
					data-testid="close-drawer-btn"
				>
					✕
				</button>
			</div>

			{/* Workflow Toolbar */}
			<div
				className="proposal-workflow-toolbar"
				data-testid="proposal-workflow-toolbar"
			>
				<button
					type="button"
					className="btn-secondary btn-xs"
					onClick={() => {
						void loadClarifications();
						void loadProposals();
					}}
					disabled={
						isLoadingProposals || isLoadingClarifications || !activeSession
					}
					data-testid="right-refresh-proposals-btn"
				>
					{isLoadingProposals || isLoadingClarifications
						? "Refreshing..."
						: "Refresh"}
				</button>
				<button
					type="button"
					className="btn-secondary btn-xs"
					onClick={() => void startClarify()}
					disabled={isClarifying || !activeSession || !canTriggerJobs}
					data-testid="right-start-clarify-btn"
				>
					{isClarifying ? "Clarifying..." : "Generate Clarifications"}
				</button>
				<button
					type="button"
					className="btn-primary btn-xs"
					onClick={() => void startConstruct()}
					disabled={isConstructing || !activeSession || !canTriggerJobs}
					data-testid="right-start-construct-btn"
				>
					{isConstructing ? "Constructing..." : "Construct Proposal"}
				</button>
				{canTriggerJobs && (
					<button
						type="button"
						className="btn-secondary btn-xs"
						onClick={() => onSelectProposal(NEW_PROPOSAL_DRAFT_KEY)}
						disabled={!activeSession || !proposal}
						data-testid="new-draft-proposal-btn"
					>
						+ New Draft Proposal
					</button>
				)}
			</div>

			{proposals.length > 0 && (
				<div className="form-group">
					<label htmlFor="proposal-detail-select">Active Proposal:</label>
					<select
						id="proposal-detail-select"
						className="form-select"
						value={proposal?.proposal_id ?? NEW_PROPOSAL_DRAFT_KEY}
						onChange={(e) => onSelectProposal(e.target.value)}
						data-testid="proposal-detail-select"
					>
						{!proposal && (
							<option value={NEW_PROPOSAL_DRAFT_KEY}>
								New Candidate Proposal Draft (unsaved)
							</option>
						)}
						{proposals.map((item) => (
							<option key={item.proposal_id} value={item.proposal_id}>
								{item.proposal_id} ({item.status})
							</option>
						))}
					</select>
				</div>
			)}

			<section
				className="proposal-explanation-box"
				data-testid="proposal-explanation-box"
			>
				<h4 className="section-heading">What is a Candidate Proposal?</h4>
				<p className="proposal-explanation-lead">
					A <strong>Candidate Proposal</strong> (<code>OntologyProposal</code>)
					is a staged, reviewable batch of additive ontology changes generated
					from your parsed documents and Clarification Q&amp;A answers. Rather
					than mutating the model automatically, Open-DV saves the proposal in
					Content-Addressable Storage (CAS) for human stewardship review.
				</p>
				<ul className="proposal-explanation-list">
					<li>
						<strong>
							Concepts (<code>add_concept</code>):
						</strong>{" "}
						Proposes Data Vault 2.0 Conceptual Hubs and ELM archetypes anchored
						to source terminology.
					</li>
					<li>
						<strong>
							Mappings (<code>add_external_mapping</code>):
						</strong>{" "}
						Attaches BM25-verified FIBO SKOS alignments — Path A{" "}
						<code>ONT-EVO-002</code> grounding requires{" "}
						<code>skos:exactMatch</code> or <code>skos:closeMatch</code> (while
						weaker predicates such as <code>skos:broadMatch</code> do not
						satisfy Path A on their own).
					</li>
					<li>
						<strong>Stewardship Gate:</strong> <code>PENDING</code> proposals
						can be answered, edited &amp; saved as a new pending revision,{" "}
						<strong>Accepted</strong> (committing a validated SemVer snapshot),{" "}
						<strong>Rebased</strong>, or <strong>Rejected</strong>.
					</li>
				</ul>
			</section>

			{/* Inline Clarification Q&A Management */}
			<section
				className="editor-section proposal-qa-section"
				data-testid="right-clarifications-section"
			>
				<div className="panel-title-group">
					<h4 className="section-heading">
						1. Domain Clarifications ({clarifications.length})
					</h4>
					<div className="panel-badges">
						<span
							className="badge badge-pending"
							data-testid="right-pending-count-badge"
						>
							{pendingQaCount} Pending
						</span>
						<span
							className="badge badge-answered"
							data-testid="right-answered-count-badge"
						>
							{answeredQaCount} Answered
						</span>
						<button
							type="button"
							className="btn-secondary btn-xs"
							onClick={() => setQaExpanded((prev) => !prev)}
							data-testid="toggle-right-qa-btn"
						>
							{qaExpanded ? "Hide Q&A" : "Show Q&A"}
						</button>
					</div>
				</div>
				{qaExpanded && (
					<ClarificationQuestionsList
						testIdPrefix="right-"
						canHumanRatify={canHumanRatify}
						compact
					/>
				)}
			</section>

			{/* Lineage, Metadata & Ratification Controls */}
			{proposal && (
				<section
					className="editor-section proposal-meta-section"
					data-testid="right-proposal-resolution-section"
				>
					<h4 className="section-heading">2. Lineage &amp; Ratification</h4>
					<dl
						className="proposal-meta-grid"
						data-testid="proposal-metadata-section"
					>
						<div>
							<dt>Base Version</dt>
							<dd>v{proposal.base_version || GENESIS_SEMVER_VERSION}</dd>
						</div>
						<div>
							<dt>Proposer</dt>
							<dd>
								<code>{proposal.proposer || "deterministic-pipeline"}</code>
							</dd>
						</div>
						{proposal.created_at && (
							<div>
								<dt>Created</dt>
								<dd>{proposal.created_at}</dd>
							</div>
						)}
						{proposal.base_sha256 && (
							<div>
								<dt>Base SHA-256</dt>
								<dd title={proposal.base_sha256}>
									<code>
										{proposal.base_sha256.slice(0, SHA256_SHORT_DISPLAY_CHARS)}
										...
									</code>
								</dd>
							</div>
						)}
					</dl>
					<ProposalRatificationControls
						testIdPrefix="right-"
						proposal={proposal}
						userRole={userRole}
						acceptLabel="Accept Proposal"
						rationaleValue={acceptRationale}
						selfOverrideChecked={selfAcceptOverride}
						lowConfidenceWaiverChecked={lowConfidenceWaiver}
						hasUnsavedDraftEdits={hasDraftChanges}
						onRationaleChange={setAcceptRationale}
						onSelfOverrideChange={setSelfAcceptOverride}
						onLowConfidenceWaiverChange={setLowConfidenceWaiver}
						onAccept={() =>
							void acceptProposal(
								proposal.proposal_id,
								acceptRationale.trim() || undefined,
								undefined,
								userRole === "admin" &&
									(selfAcceptOverride || lowConfidenceWaiver)
									? {
											...(selfAcceptOverride
												? { selfAcceptanceOverride: true }
												: {}),
											...(lowConfidenceWaiver
												? { lowConfidenceWaiver: true }
												: {}),
										}
									: undefined,
							)
						}
						onRebase={() => void rebaseProposal(proposal.proposal_id)}
						onReject={() => void rejectProposal(proposal.proposal_id)}
					/>
				</section>
			)}

			{/* Proposal Editor & Staged Delta Operations */}
			<section
				className="editor-section"
				data-testid="proposal-operations-section"
			>
				<h4 className="section-heading">
					3. Edit Proposal &amp; Delta Operations ({draftOps.length})
				</h4>

				{canEditDraft && (
					<div className="proposal-editor-controls">
						<div className="form-group">
							<label htmlFor="edit-proposal-rationale-input">
								Proposal Rationale:
							</label>
							<textarea
								id="edit-proposal-rationale-input"
								className="form-textarea"
								rows={2}
								value={draftRationale}
								onChange={(e) => {
									commitDraftUpdate(e.target.value, draftOps);
									setDraftError(null);
								}}
								placeholder="Summarize why these ontology operations are proposed..."
								data-testid="edit-proposal-rationale-input"
							/>
						</div>

						<div className="proposal-workflow-toolbar">
							<button
								type="button"
								className="btn-secondary btn-xs"
								onClick={() =>
									setNewOpMode(newOpMode === "concept" ? null : "concept")
								}
								data-testid="toggle-add-concept-op-btn"
							>
								+ Add Concept Op
							</button>
							<button
								type="button"
								className="btn-secondary btn-xs"
								onClick={() => {
									if (!newMappingConceptUri && availableConceptUris[0]) {
										setNewMappingConceptUri(availableConceptUris[0]);
									}
									setNewOpMode(newOpMode === "mapping" ? null : "mapping");
								}}
								data-testid="toggle-add-mapping-op-btn"
							>
								+ Add Mapping Op
							</button>
							{hasDraftChanges && (
								<button
									type="button"
									className="btn-secondary btn-xs"
									onClick={handleResetDraft}
									data-testid="reset-proposal-edits-btn"
								>
									Reset Edits
								</button>
							)}
							<button
								type="button"
								className="btn-primary btn-xs"
								onClick={() => void handleSaveProposalRevision()}
								disabled={
									isSavingDraft ||
									!activeSession ||
									draftOps.length === 0 ||
									(Boolean(proposal) && !hasDraftChanges)
								}
								data-testid="save-edited-proposal-btn"
							>
								{isSavingDraft ? "Saving..." : "Save Edited Proposal"}
							</button>
						</div>

						{newOpMode === "concept" && (
							<form
								className="proposal-op-edit-form"
								onSubmit={handleAddConceptOp}
								data-testid="add-proposal-op-form"
							>
								<input
									type="text"
									className="form-input-sm"
									placeholder="Concept Name (e.g. Margin Call Notice)"
									value={newConceptName}
									onChange={(e) => setNewConceptName(e.target.value)}
									required
									data-testid="new-op-concept-name-input"
								/>
								<select
									className="form-select-sm"
									value={newConceptArchetype}
									onChange={(e) => setNewConceptArchetype(e.target.value)}
									data-testid="new-op-archetype-select"
								>
									{CONCEPT_ARCHETYPES.map((arch) => (
										<option key={arch} value={arch}>
											{arch}
										</option>
									))}
								</select>
								<input
									type="text"
									className="form-input-sm"
									placeholder="Normative definition..."
									value={newConceptDefinition}
									onChange={(e) => setNewConceptDefinition(e.target.value)}
									data-testid="new-op-definition-input"
								/>
								<button
									type="submit"
									className="btn-primary btn-xs"
									disabled={!newConceptName.trim()}
									data-testid="add-proposal-concept-op-btn"
								>
									Stage Concept Op
								</button>
							</form>
						)}

						{newOpMode === "mapping" && (
							<form
								className="proposal-op-edit-form"
								onSubmit={handleAddMappingOp}
								data-testid="add-proposal-mapping-form"
							>
								<input
									type="text"
									className="form-input-sm"
									list="proposal-concept-uris-list"
									placeholder="Concept URN (e.g. urn:ontology:entity:...)"
									value={newMappingConceptUri}
									onChange={(e) => setNewMappingConceptUri(e.target.value)}
									required
									data-testid="new-op-mapping-concept-input"
								/>
								<datalist id="proposal-concept-uris-list">
									{availableConceptUris.map((uri) => (
										<option key={uri} value={uri} />
									))}
								</datalist>
								<div className="proposal-op-edit-row">
									<input
										type="text"
										className="form-input-sm"
										placeholder="Scheme (e.g. FIBO)"
										value={newMappingScheme}
										onChange={(e) => setNewMappingScheme(e.target.value)}
										data-testid="new-op-mapping-scheme-input"
									/>
									<select
										className="form-select-sm"
										value={newMappingPredicate}
										onChange={(e) => setNewMappingPredicate(e.target.value)}
										data-testid="new-op-mapping-predicate-select"
									>
										{SKOS_PREDICATES.map((pred) => (
											<option key={pred} value={pred}>
												{pred}
											</option>
										))}
									</select>
								</div>
								<div className="proposal-op-edit-row">
									<input
										type="text"
										className="form-input-sm"
										placeholder="Target Standard URI (e.g. https://spec.edmcouncil.org/fibo/...)"
										value={newMappingTargetUri}
										onChange={(e) => setNewMappingTargetUri(e.target.value)}
										required
										data-testid="new-op-mapping-uri-input"
									/>
									<input
										type="number"
										step={MAPPING_CONFIDENCE_STEP}
										min={MIN_MAPPING_CONFIDENCE}
										max={MAX_MAPPING_CONFIDENCE}
										className="form-input-sm conf-input"
										value={newMappingConfidence}
										onChange={(e) => setNewMappingConfidence(e.target.value)}
										data-testid="new-op-mapping-conf-input"
									/>
								</div>
								<button
									type="submit"
									className="btn-primary btn-xs"
									disabled={!newMappingTargetUri.trim()}
									data-testid="add-proposal-mapping-op-btn"
								>
									Stage Mapping Op
								</button>
							</form>
						)}

						{draftError && (
							<p
								className="proposal-empty-notice"
								role="alert"
								data-testid="proposal-draft-error"
							>
								{draftError}
							</p>
						)}
					</div>
				)}

				<div className="proposal-ops-summary">
					{counts.concepts > 0 && (
						<span className="badge">Concepts (+{counts.concepts})</span>
					)}
					{counts.mappings > 0 && (
						<span className="badge">FIBO Mappings (+{counts.mappings})</span>
					)}
					{counts.relationships > 0 && (
						<span className="badge">
							Relationships (+{counts.relationships})
						</span>
					)}
					{counts.attributes > 0 && (
						<span className="badge">Attributes (+{counts.attributes})</span>
					)}
					{counts.other > 0 && (
						<span className="badge">Other ({counts.other})</span>
					)}
				</div>

				{draftOps.length > 0 && (
					<input
						type="search"
						className="form-input-sm"
						placeholder="Filter operations by concept, FIBO URI, hub..."
						value={opFilter}
						onChange={(e) => onOpFilterChange(e.target.value)}
						aria-label="Filter proposal operations"
						data-testid="proposal-ops-filter-input"
					/>
				)}

				{filteredOps.length === 0 ? (
					<p className="empty-subtext">
						{draftOps.length === 0
							? "No delta operations staged yet. Click Construct Proposal above or add operations manually."
							: `No operations match "${opFilter}".`}
					</p>
				) : (
					<div
						className="proposal-operations-list"
						data-testid="proposal-operations-list"
					>
						{filteredOps.map(({ op, originalIdx }) => {
							const opType = String(op.op_type || "operation");
							const conceptUri =
								typeof op.concept_uri === "string" ? op.concept_uri : "";
							const targetLabel = extractIriTerminalLabel(op.target_uri);
							const rawConf =
								typeof op.mapping_confidence === "number"
									? op.mapping_confidence
									: typeof op.confidence === "number"
										? op.confidence
										: null;
							const confidenceInputVal =
								confidenceInputs[originalIdx] !== undefined
									? confidenceInputs[originalIdx]
									: rawConf !== null
										? String(rawConf)
										: "";
							const canJumpToNode =
								opType === "add_concept" &&
								conceptUri.length > 0 &&
								nodeUriSet.has(conceptUri);
							const isEditingThisOp = editingOpIndex === originalIdx;
							const primaryFieldCfg = getEditableOpPrimaryField(opType);

							return (
								<div
									key={String(op.op_id || `${opType}-${originalIdx}`)}
									className="proposal-op-card"
									data-testid={`proposal-op-card-${originalIdx}`}
								>
									<div className="proposal-op-header">
										<span className="badge badge-type">{opType}</span>
										<div className="panel-badges">
											{rawConf !== null && (
												<span className="badge">
													conf: {(rawConf * PERCENTAGE_MULTIPLIER).toFixed(0)}%
												</span>
											)}
											{canEditDraft && (
												<>
													<button
														type="button"
														className="btn-secondary btn-xs"
														onClick={() =>
															setEditingOpIndex(
																isEditingThisOp ? null : originalIdx,
															)
														}
														data-testid={`edit-op-toggle-btn-${originalIdx}`}
													>
														{isEditingThisOp ? "Done" : "Edit"}
													</button>
													<button
														type="button"
														className="btn-danger btn-xs"
														onClick={() => handleRemoveOp(originalIdx)}
														data-testid={`remove-proposal-op-btn-${originalIdx}`}
													>
														Remove
													</button>
												</>
											)}
										</div>
									</div>

									{opType === "add_concept" && (
										<div className="proposal-op-body">
											<div className="proposal-op-title-row">
												<strong>{String(op.concept_name || conceptUri)}</strong>
												{canJumpToNode && (
													<button
														type="button"
														className="btn-secondary btn-xs"
														onClick={() => onInspectConcept(conceptUri)}
														data-testid={`inspect-op-concept-${originalIdx}`}
													>
														Edit Node
													</button>
												)}
											</div>
											{typeof op.target_hub === "string" &&
												op.target_hub.length > 0 && (
													<span className="proposal-op-meta">
														Hub: <code>{op.target_hub}</code> ·{" "}
														{typeof op.elm_archetype === "string" &&
														op.elm_archetype
															? op.elm_archetype
															: DEFAULT_CONCEPT_ARCHETYPE}
													</span>
												)}
											{conceptUri && (
												<code className="proposal-op-uri">{conceptUri}</code>
											)}
										</div>
									)}

									{opType === "add_external_mapping" && (
										<div className="proposal-op-body">
											<span className="proposal-op-title">
												{extractIriTerminalLabel(conceptUri)} →{" "}
												<strong>
													{typeof op.scheme === "string" && op.scheme
														? op.scheme
														: DEFAULT_MAPPING_SCHEME}
													:{targetLabel}
												</strong>
											</span>
											<span className="proposal-op-meta">
												Predicate:{" "}
												<code>
													{typeof op.predicate === "string" ? op.predicate : ""}
												</code>
											</span>
											{typeof op.target_uri === "string" && (
												<code className="proposal-op-uri" title={op.target_uri}>
													{op.target_uri}
												</code>
											)}
										</div>
									)}

									{opType !== "add_concept" &&
										opType !== "add_external_mapping" && (
											<div className="proposal-op-body">
												<strong>
													{String(
														op.relationship_name ||
															op.attribute_name ||
															op.synonym ||
															op.refined_description ||
															op.definition ||
															op.description ||
															op.concept_name ||
															conceptUri ||
															opType,
													)}
												</strong>
												{typeof op.target_link === "string" &&
													op.target_link.length > 0 && (
														<span className="proposal-op-meta">
															Link: <code>{op.target_link}</code>
														</span>
													)}
											</div>
										)}

									{isEditingThisOp && canEditDraft && (
										<div
											className="proposal-op-inline-editor"
											data-testid={`op-inline-editor-${originalIdx}`}
										>
											{opType === "add_concept" && (
												<>
													<input
														type="text"
														className="form-input-sm"
														aria-label="Concept Name"
														value={
															typeof op.concept_name === "string"
																? op.concept_name
																: ""
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"concept_name",
																e.target.value,
															)
														}
														data-testid={`edit-op-concept-name-${originalIdx}`}
													/>
													<select
														className="form-select-sm"
														aria-label="ELM Archetype"
														value={
															typeof op.elm_archetype === "string" &&
															op.elm_archetype
																? op.elm_archetype
																: DEFAULT_CONCEPT_ARCHETYPE
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"elm_archetype",
																e.target.value,
															)
														}
														data-testid={`edit-op-archetype-${originalIdx}`}
													>
														{CONCEPT_ARCHETYPES.map((arch) => (
															<option key={arch} value={arch}>
																{arch}
															</option>
														))}
													</select>
													<input
														type="text"
														className="form-input-sm"
														aria-label="Definition"
														placeholder="Concept definition..."
														value={
															typeof op.definition === "string"
																? op.definition
																: ""
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"definition",
																e.target.value,
															)
														}
														data-testid={`edit-op-definition-${originalIdx}`}
													/>
													<input
														type="text"
														className="form-input-sm"
														aria-label="Target Hub"
														placeholder="hub_..."
														value={
															typeof op.target_hub === "string"
																? op.target_hub
																: ""
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"target_hub",
																e.target.value,
															)
														}
														data-testid={`edit-op-target-hub-${originalIdx}`}
													/>
												</>
											)}
											{opType === "add_external_mapping" && (
												<>
													<input
														type="text"
														className="form-input-sm"
														aria-label="Concept URN"
														placeholder="urn:ontology:entity:..."
														value={conceptUri}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"concept_uri",
																e.target.value,
															)
														}
														data-testid={`edit-op-concept-uri-${originalIdx}`}
													/>
													<input
														type="text"
														className="form-input-sm"
														aria-label="External Mapping Scheme"
														placeholder="FIBO"
														value={
															typeof op.scheme === "string"
																? op.scheme
																: DEFAULT_MAPPING_SCHEME
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"scheme",
																e.target.value,
															)
														}
														data-testid={`edit-op-scheme-${originalIdx}`}
													/>
													<input
														type="text"
														className="form-input-sm"
														aria-label="Target Standard URI"
														value={
															typeof op.target_uri === "string"
																? op.target_uri
																: ""
														}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																"target_uri",
																e.target.value,
															)
														}
														data-testid={`edit-op-target-uri-${originalIdx}`}
													/>
													<div className="proposal-op-edit-row">
														<select
															className="form-select-sm"
															aria-label="SKOS Predicate"
															value={
																typeof op.predicate === "string"
																	? op.predicate
																	: DEFAULT_MAPPING_PREDICATE
															}
															onChange={(e) =>
																handleUpdateOpField(
																	originalIdx,
																	"predicate",
																	e.target.value,
																)
															}
															data-testid={`edit-op-predicate-${originalIdx}`}
														>
															{SKOS_PREDICATES.map((pred) => (
																<option key={pred} value={pred}>
																	{pred}
																</option>
															))}
														</select>
														<input
															type="number"
															step={MAPPING_CONFIDENCE_STEP}
															min={MIN_MAPPING_CONFIDENCE}
															max={MAX_MAPPING_CONFIDENCE}
															className="form-input-sm conf-input"
															aria-label="Confidence Score"
															value={confidenceInputVal}
															onChange={(e) =>
																handleUpdateMappingConfidence(
																	originalIdx,
																	e.target.value,
																)
															}
															data-testid={`edit-op-confidence-${originalIdx}`}
														/>
													</div>
												</>
											)}
											{opType !== "add_concept" &&
												opType !== "add_external_mapping" && (
													<input
														type="text"
														className="form-input-sm"
														aria-label={primaryFieldCfg.label}
														placeholder={primaryFieldCfg.label}
														value={String(
															op[primaryFieldCfg.field] ??
																(primaryFieldCfg.aliasKey
																	? op[primaryFieldCfg.aliasKey]
																	: undefined) ??
																"",
														)}
														onChange={(e) =>
															handleUpdateOpField(
																originalIdx,
																primaryFieldCfg.field,
																e.target.value,
																primaryFieldCfg.aliasKey,
															)
														}
														data-testid={`edit-op-name-${originalIdx}`}
													/>
												)}
										</div>
									)}
								</div>
							);
						})}
					</div>
				)}
			</section>
		</div>
	);
};
