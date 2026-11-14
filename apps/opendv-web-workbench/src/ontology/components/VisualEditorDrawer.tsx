/**
 * Visual Editor Drawer Component (Task 5D.5, #226)
 * (apps/opendv-web-workbench/src/ontology/components/VisualEditorDrawer.tsx)
 *
 * Implements:
 * - Sliding context drawer for inspecting and editing selected concept or relationship
 * - Attributes editor: add, edit, remove with data types, is_optional, descriptions
 * - Synonyms editor: tag chip list with duplicate and length guards
 * - External mappings editor: FIBO / reference standards with confidence scoring
 * - Real-time validation against Graph A schema and contract invariants
 */

import type React from "react";
import { useEffect, useRef, useState } from "react";
import {
	CONCEPT_ARCHETYPES,
	CONFIDENCE_DECIMAL_PLACES,
	DEFAULT_CONCEPT_ARCHETYPE,
	DEFAULT_MAPPING_CONFIDENCE_INPUT,
	DEFAULT_MAPPING_PREDICATE,
	DEFAULT_MAPPING_SCHEME,
	EXTERNAL_MAPPING_SCHEME_OPTIONS,
	GROUNDING_MIN_CONFIDENCE,
	MAPPING_CONFIDENCE_STEP,
	MAX_ATTRIBUTE_NAME_LENGTH,
	MAX_MAPPING_CONFIDENCE,
	MAX_SYNONYM_LENGTH,
	MAX_SYNONYMS_PER_CONCEPT,
	MIN_MAPPING_CONFIDENCE,
	NEW_PROPOSAL_DRAFT_KEY,
	PARENT_CONCEPT_URN_REGEX,
	PERCENTAGE_MULTIPLIER,
	SAVE_FEEDBACK_RESET_MS,
	SKOS_PREDICATES,
	TARGET_HUB_REGEX,
} from "../constants";
import { useStudioStore } from "../store/studio-store";
import type {
	ConceptAttribute,
	ConceptNode,
	ExternalMapping,
} from "../store/types";
import { ProposalDetailsView } from "./ProposalDetailsView";

export interface VisualEditorDrawerProps {
	className?: string;
	hideProposalTab?: boolean | undefined;
	onClose?: () => void;
	onSave?: (updatedConcept: ConceptNode) => void;
}

const DATA_TYPES = [
	"string",
	"integer",
	"decimal",
	"boolean",
	"date",
	"timestamp",
	"binary",
] as const;

export interface ConceptEditDraft {
	concept_uri: string;
	concept_name: string;
	elm_archetype: string;
	definition?: string | null;
	description?: string | null;
	target_hub?: string | null;
	parent_concept_uri?: string | null;
	attributes: ConceptAttribute[];
	synonyms: string[];
	external_mappings: ExternalMapping[];
}

export function validateConceptEdit(
	draft: ConceptEditDraft,
	committed?: ConceptNode | null,
	allNodes?: ConceptNode[],
	options?: { allowDestructive?: boolean },
): string[] {
	const errors: string[] = [];
	const allowDestructive = Boolean(options?.allowDestructive);

	if (!draft.concept_name.trim()) {
		errors.push("Concept Name is mandatory.");
	}

	if (
		draft.parent_concept_uri &&
		!PARENT_CONCEPT_URN_REGEX.test(draft.parent_concept_uri.trim())
	) {
		errors.push("Parent Concept URI must match URN pattern.");
	}

	if (
		draft.parent_concept_uri &&
		draft.parent_concept_uri.trim() === draft.concept_uri
	) {
		errors.push(
			"Concept cannot be its own parent (ONT-DAG-001 cycle prevention).",
		);
	}

	if (draft.parent_concept_uri && allNodes && allNodes.length > 0) {
		const visited = new Set<string>([draft.concept_uri]);
		let currParentUri: string | null | undefined =
			draft.parent_concept_uri.trim();
		while (currParentUri) {
			if (visited.has(currParentUri)) {
				errors.push(
					`CYCLE_DETECTED: Concept inheritance cycle detected traversing '${currParentUri}' (ONT-DAG-001).`,
				);
				break;
			}
			visited.add(currParentUri);
			const parentNode = allNodes.find(
				(n) => n.concept_uri === currParentUri || n.id === currParentUri,
			);
			currParentUri = parentNode?.parent_concept_uri;
		}
	}

	if (committed) {
		if (draft.concept_name.trim() !== committed.concept_name) {
			errors.push(
				"CANNOT_RENAME_COMMITTED_CONCEPT: Concept name is immutable under ONT-EVO-001.",
			);
		}
		if (!allowDestructive && draft.elm_archetype !== committed.elm_archetype) {
			errors.push(
				"CANNOT_CHANGE_ARCHETYPE: Archetype of committed concepts is immutable under ONT-EVO-001.",
			);
		}
		if (committed.definition && !draft.definition?.trim()) {
			errors.push(
				"CANNOT_REMOVE_COMMITTED_DEFINITION: Definition of committed concepts cannot be removed under ONT-EVO-001.",
			);
		}
		if (!allowDestructive) {
			if (committed.parent_concept_uri) {
				if (draft.parent_concept_uri?.trim() !== committed.parent_concept_uri) {
					errors.push(
						"CANNOT_REPARENT_COMMITTED_CONCEPT: Parent hierarchy of committed concepts is immutable under ONT-EVO-001.",
					);
				}
			} else if (draft.parent_concept_uri?.trim()) {
				errors.push(
					"CANNOT_REPARENT_COMMITTED_CONCEPT: Adding a parent to a committed root concept is prohibited under ONT-EVO-001.",
				);
			}
		}
		if (
			committed.target_hub &&
			(draft.target_hub?.trim() || null) !== committed.target_hub
		) {
			errors.push(
				"CANNOT_MODIFY_COMMITTED_TARGET_HUB: Target hub of committed concepts is immutable under ONT-EVO-001.",
			);
		}
		const missingCommittedSynonym = committed.synonyms?.some(
			(cs) => !draft.synonyms.some((s) => s.toLowerCase() === cs.toLowerCase()),
		);
		if (missingCommittedSynonym) {
			errors.push(
				"CANNOT_REMOVE_COMMITTED_SYNONYM: Committed synonyms cannot be removed under ONT-EVO-001.",
			);
		}

		for (const ca of committed.attributes || []) {
			const matching = draft.attributes.find(
				(a) =>
					a.attribute_name.toLowerCase() === ca.attribute_name.toLowerCase(),
			);
			if (!matching) {
				if (!allowDestructive) {
					errors.push(
						`CANNOT_REMOVE_COMMITTED_ATTRIBUTE: Committed attribute '${ca.attribute_name}' cannot be removed under ONT-EVO-001.`,
					);
				}
			} else if (matching.data_type !== ca.data_type) {
				errors.push(
					`CANNOT_MODIFY_COMMITTED_ATTRIBUTE_TYPE: Data type of committed attribute '${ca.attribute_name}' is immutable under ONT-EVO-001 (was '${ca.data_type}', cannot change to '${matching.data_type}').`,
				);
			} else if (ca.is_optional === true && matching.is_optional === false) {
				errors.push(
					`CANNOT_TIGHTEN_COMMITTED_ATTRIBUTE: Committed optional attribute '${ca.attribute_name}' cannot be made mandatory under ONT-EVO-001.`,
				);
			}
		}

		const committedAttrNames = new Set(
			(committed.attributes || []).map((a) => a.attribute_name.toLowerCase()),
		);
		for (const da of draft.attributes || []) {
			if (
				!committedAttrNames.has(da.attribute_name.toLowerCase()) &&
				da.is_optional === false &&
				!allowDestructive
			) {
				errors.push(
					`CANNOT_ADD_MANDATORY_ATTRIBUTE: Adding mandatory attribute '${da.attribute_name}' (is_optional=false) to a committed concept is a breaking MAJOR change under ONT-EVO-001 and requires admin authorization.`,
				);
			}
		}

		const missingCommittedMapping = committed.external_mappings?.some(
			(cm) =>
				!draft.external_mappings.some(
					(m) =>
						m.scheme.toLowerCase() === cm.scheme.toLowerCase() &&
						m.target_uri.toLowerCase() === cm.target_uri.toLowerCase(),
				),
		);
		if (missingCommittedMapping && !allowDestructive) {
			errors.push(
				"CANNOT_REMOVE_COMMITTED_MAPPING: Committed external mappings cannot be removed under ONT-EVO-001.",
			);
		}
	}

	if (draft.target_hub && !TARGET_HUB_REGEX.test(draft.target_hub.trim())) {
		errors.push("Target Hub name must match pattern 'hub_[a-z0-9_]+'.");
	}

	const attrNames = draft.attributes.map((a) => a.attribute_name.toLowerCase());
	if (new Set(attrNames).size !== attrNames.length) {
		errors.push("Duplicate attribute names detected.");
	}

	return errors;
}

export const VisualEditorDrawer: React.FC<VisualEditorDrawerProps> = ({
	className = "",
	hideProposalTab = false,
	onClose,
	onSave,
}) => {
	const selectedNodeId = useStudioStore((s) => s.selectedNodeId);
	const nodes = useStudioStore((s) => s.nodes);
	const committedNodes = useStudioStore((s) => s.committedNodes);
	const userRole = useStudioStore((s) => s.userRole);
	const isHistoricalView = useStudioStore((s) => s.isHistoricalView);
	const proposals = useStudioStore((s) => s.proposals);
	const selectedProposalId = useStudioStore((s) => s.selectedProposalId);
	const layout = useStudioStore((s) => s.layout);
	const updateConcept = useStudioStore((s) => s.updateConcept);
	const deleteConcept = useStudioStore((s) => s.deleteConcept);
	const selectNode = useStudioStore((s) => s.selectNode);
	const selectProposal = useStudioStore((s) => s.selectProposal);
	const setRightDrawerTab = useStudioStore((s) => s.setRightDrawerTab);

	const selectedConcept = nodes.find(
		(n) => n.id === selectedNodeId || n.concept_uri === selectedNodeId,
	);

	const committedConcept = committedNodes.find(
		(n) => n.concept_uri === selectedConcept?.concept_uri,
	);
	const isCommitted = Boolean(committedConcept);
	const isAdmin = userRole === "admin";
	const canMutateConcept = userRole === "editor" || userRole === "admin";
	const isConceptReadOnly = !canMutateConcept || isHistoricalView;

	// Form state
	const [conceptName, setConceptName] = useState(
		selectedConcept?.concept_name || "",
	);
	const [elmArchetype, setElmArchetype] = useState(
		selectedConcept?.elm_archetype || DEFAULT_CONCEPT_ARCHETYPE,
	);
	const [definition, setDefinition] = useState(
		selectedConcept?.definition || "",
	);
	const [description, setDescription] = useState(
		selectedConcept?.description || "",
	);
	const [targetHub, setTargetHub] = useState(selectedConcept?.target_hub || "");
	const [parentConceptUri, setParentConceptUri] = useState(
		selectedConcept?.parent_concept_uri || "",
	);
	const [attributes, setAttributes] = useState<ConceptAttribute[]>(
		selectedConcept?.attributes ? [...selectedConcept.attributes] : [],
	);
	const [synonyms, setSynonyms] = useState<string[]>(
		selectedConcept?.synonyms ? [...selectedConcept.synonyms] : [],
	);
	const [externalMappings, setExternalMappings] = useState<ExternalMapping[]>(
		selectedConcept?.external_mappings
			? [...selectedConcept.external_mappings]
			: [],
	);

	// Form input helpers
	const [newAttrName, setNewAttrName] = useState("");
	const [newAttrType, setNewAttrType] = useState<string>("string");
	const [newAttrOptional, setNewAttrOptional] = useState(true);
	const [newAttrDesc, setNewAttrDesc] = useState("");

	const [newSynonym, setNewSynonym] = useState("");

	const [newMappingScheme, setNewMappingScheme] = useState<string>(
		DEFAULT_MAPPING_SCHEME.toLowerCase(),
	);
	const [newMappingUri, setNewMappingUri] = useState("");
	const [newMappingConf, setNewMappingConf] = useState<string>(
		DEFAULT_MAPPING_CONFIDENCE_INPUT,
	);
	const [newMappingPredicate, setNewMappingPredicate] = useState<string>(
		DEFAULT_MAPPING_PREDICATE,
	);

	const [validationErrors, setValidationErrors] = useState<string[]>([]);
	const [saveSuccess, setSaveSuccess] = useState(false);
	const [proposalOpFilter, setProposalOpFilter] = useState("");
	const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		return () => {
			if (saveTimeoutRef.current) {
				clearTimeout(saveTimeoutRef.current);
			}
		};
	}, []);

	const selectedUri = selectedConcept?.concept_uri;
	const prevSelectedUriRef = useRef<string | undefined>(selectedUri);
	const initialSnapshotRef = useRef<string>(
		selectedConcept ? JSON.stringify(selectedConcept) : "",
	);

	// Load concept into local form state when selected concept URI changes (MIN-01)
	useEffect(() => {
		if (
			selectedConcept &&
			prevSelectedUriRef.current !== selectedConcept.concept_uri
		) {
			prevSelectedUriRef.current = selectedConcept.concept_uri;
			initialSnapshotRef.current = JSON.stringify(selectedConcept);
			setConceptName(selectedConcept.concept_name || "");
			setElmArchetype(
				selectedConcept.elm_archetype || DEFAULT_CONCEPT_ARCHETYPE,
			);
			setDefinition(selectedConcept.definition || "");
			setDescription(selectedConcept.description || "");
			setTargetHub(selectedConcept.target_hub || "");
			setParentConceptUri(selectedConcept.parent_concept_uri || "");
			setAttributes([...(selectedConcept.attributes || [])]);
			setSynonyms([...(selectedConcept.synonyms || [])]);
			setExternalMappings([...(selectedConcept.external_mappings || [])]);
			setNewAttrOptional(true);
			setValidationErrors([]);
			setSaveSuccess(false);
		}
	}, [selectedConcept?.concept_uri]);

	const isDrawerOpen = layout?.rightDrawerOpen !== false;
	const selectedProposal =
		selectedProposalId === NEW_PROPOSAL_DRAFT_KEY
			? undefined
			: (proposals.find((p) => p.proposal_id === selectedProposalId) ??
				proposals[0]);
	const wantsProposalTab =
		isDrawerOpen &&
		(layout?.rightDrawerTab === "proposal" ||
			(!selectedConcept && Boolean(selectedProposalId)));
	const activeTab: "concept" | "proposal" | null = wantsProposalTab
		? "proposal"
		: selectedConcept
			? "concept"
			: null;

	if (!activeTab || (hideProposalTab && activeTab === "proposal")) {
		return null;
	}

	const handleAddAttribute = () => {
		const cleanName = newAttrName.trim().toLowerCase();
		if (!cleanName) return;

		if (
			!/^[a-z0-9_]+$/.test(cleanName) ||
			cleanName.length > MAX_ATTRIBUTE_NAME_LENGTH
		) {
			setValidationErrors([
				`Attribute name must be lowercase alphanumeric and underscore (1-${MAX_ATTRIBUTE_NAME_LENGTH} characters).`,
			]);
			return;
		}

		if (attributes.some((a) => a.attribute_name.toLowerCase() === cleanName)) {
			setValidationErrors([
				`DUPLICATE_ATTRIBUTE_NAME: Attribute '${cleanName}' already exists.`,
			]);
			return;
		}

		const newAttr: ConceptAttribute = {
			attribute_name: cleanName,
			data_type: newAttrType,
			is_optional: newAttrOptional,
			description: newAttrDesc.trim() || null,
		};

		setAttributes([...attributes, newAttr]);
		setNewAttrName("");
		setNewAttrDesc("");
		setNewAttrOptional(true);
		setValidationErrors([]);
	};

	const handleRemoveAttribute = (attrName: string) => {
		const wasCommitted = committedConcept?.attributes?.some(
			(a) => a.attribute_name.toLowerCase() === attrName.toLowerCase(),
		);
		if (wasCommitted && !isAdmin) {
			setValidationErrors([
				`CANNOT_REMOVE_COMMITTED_ATTRIBUTE: Attribute '${attrName}' is committed. Admin role is required to remove committed attributes under ONT-EVO-001.`,
			]);
			return;
		}
		setAttributes(attributes.filter((a) => a.attribute_name !== attrName));
	};

	const handleAddSynonym = () => {
		const cleanSyn = newSynonym.trim();
		if (!cleanSyn) return;

		if (cleanSyn.length > MAX_SYNONYM_LENGTH) {
			setValidationErrors([
				`Synonym must be ${MAX_SYNONYM_LENGTH} characters or fewer.`,
			]);
			return;
		}

		if (synonyms.some((s) => s.toLowerCase() === cleanSyn.toLowerCase())) {
			setValidationErrors([`Synonym '${cleanSyn}' is already registered.`]);
			return;
		}

		if (synonyms.length >= MAX_SYNONYMS_PER_CONCEPT) {
			setValidationErrors([
				`Cannot add more than ${MAX_SYNONYMS_PER_CONCEPT} synonyms per concept.`,
			]);
			return;
		}

		setSynonyms([...synonyms, cleanSyn]);
		setNewSynonym("");
		setValidationErrors([]);
	};

	const handleRemoveSynonym = (syn: string) => {
		const wasCommitted = committedConcept?.synonyms?.some(
			(s) => s.toLowerCase() === syn.toLowerCase(),
		);
		if (wasCommitted) {
			setValidationErrors([
				`CANNOT_REMOVE_COMMITTED_SYNONYM: Synonym '${syn}' is committed. Under ONT-EVO-001, ontology evolution is strictly additive.`,
			]);
			return;
		}
		setSynonyms(synonyms.filter((s) => s !== syn));
	};

	const handleAddMapping = () => {
		const cleanUri = newMappingUri.trim();
		if (!cleanUri) return;

		const conf = Number.parseFloat(newMappingConf);
		if (
			Number.isNaN(conf) ||
			conf < MIN_MAPPING_CONFIDENCE ||
			conf > MAX_MAPPING_CONFIDENCE
		) {
			setValidationErrors([
				`Confidence score must be between ${MIN_MAPPING_CONFIDENCE}.0 and ${MAX_MAPPING_CONFIDENCE}.0.`,
			]);
			return;
		}

		const exists = externalMappings.some(
			(m) =>
				m.scheme.toLowerCase() === newMappingScheme.toLowerCase() &&
				m.target_uri.toLowerCase() === cleanUri.toLowerCase(),
		);
		if (exists) {
			setValidationErrors([
				`Mapping '${cleanUri}' under scheme '${newMappingScheme}' already exists.`,
			]);
			return;
		}

		const newMapping: ExternalMapping = {
			scheme: newMappingScheme,
			target_uri: cleanUri,
			confidence: Number(conf.toFixed(CONFIDENCE_DECIMAL_PLACES)),
			predicate: newMappingPredicate,
			provenance: "user_asserted",
		};

		setExternalMappings([...externalMappings, newMapping]);
		setNewMappingUri("");
		setValidationErrors([]);
	};

	const handleToggleMappingPin = (idx: number) => {
		setExternalMappings(
			externalMappings.map((m, i) =>
				i === idx ? { ...m, pinned: !m.pinned } : m,
			),
		);
	};

	const handleRemoveMapping = (targetUriOrIndex: string | number) => {
		const idx =
			typeof targetUriOrIndex === "number"
				? targetUriOrIndex
				: externalMappings.findIndex((m) => m.target_uri === targetUriOrIndex);
		if (idx < 0 || idx >= externalMappings.length) return;

		const target = externalMappings[idx];
		if (target) {
			const wasCommitted = committedConcept?.external_mappings?.some(
				(m) =>
					m.scheme.toLowerCase() === target.scheme.toLowerCase() &&
					m.target_uri.toLowerCase() === target.target_uri.toLowerCase(),
			);
			if (wasCommitted && !isAdmin) {
				setValidationErrors([
					`CANNOT_REMOVE_COMMITTED_MAPPING: External mapping '${target.target_uri}' is committed. Admin role is required to remove committed mappings.`,
				]);
				return;
			}
		}

		setExternalMappings(externalMappings.filter((_, i) => i !== idx));
	};

	const handleSave = () => {
		if (!selectedConcept) return;
		const currentStoreConcept = nodes.find(
			(n) => n.concept_uri === selectedConcept.concept_uri,
		);
		if (
			currentStoreConcept &&
			initialSnapshotRef.current &&
			JSON.stringify(currentStoreConcept) !== initialSnapshotRef.current
		) {
			setValidationErrors([
				"CONCURRENT_MODIFICATION_DETECTED: Concept was modified in store. Discard or re-open to merge changes (ONT-EVO-003).",
			]);
			return;
		}

		const trimmedDef = definition.trim();
		const trimmedDesc = description.trim();
		const trimmedHub = targetHub.trim();
		const trimmedParent = parentConceptUri.trim();

		const draft: ConceptEditDraft = {
			concept_uri: selectedConcept.concept_uri,
			concept_name: conceptName.trim(),
			elm_archetype: elmArchetype,
			definition: trimmedDef.length > 0 ? trimmedDef : null,
			description: trimmedDesc.length > 0 ? trimmedDesc : null,
			target_hub: trimmedHub.length > 0 ? trimmedHub : null,
			parent_concept_uri: trimmedParent.length > 0 ? trimmedParent : null,
			attributes,
			synonyms,
			external_mappings: externalMappings,
		};

		const errors = validateConceptEdit(
			draft,
			isCommitted ? committedConcept : null,
			nodes,
			{ allowDestructive: isAdmin },
		);

		if (errors.length > 0) {
			setValidationErrors(errors);
			return;
		}

		const updatedDef = trimmedDef.length > 0 ? trimmedDef : undefined;
		const updatedDesc = trimmedDesc.length > 0 ? trimmedDesc : undefined;
		const updatedHub = trimmedHub.length > 0 ? trimmedHub : undefined;
		const updatedParent = trimmedParent.length > 0 ? trimmedParent : undefined;

		const updated: ConceptNode = {
			...selectedConcept,
			concept_name: conceptName.trim(),
			elm_archetype: elmArchetype,
			definition: updatedDef as unknown as string,
			description: updatedDesc,
			target_hub: updatedHub,
			parent_concept_uri: updatedParent,
			attributes,
			synonyms,
			external_mappings: externalMappings,
		};

		// Skip mutation if nothing changed
		if (
			currentStoreConcept &&
			currentStoreConcept.concept_name === updated.concept_name &&
			currentStoreConcept.elm_archetype === updated.elm_archetype &&
			currentStoreConcept.definition === updated.definition &&
			(currentStoreConcept.description || undefined) === updated.description &&
			currentStoreConcept.target_hub === updated.target_hub &&
			currentStoreConcept.parent_concept_uri === updated.parent_concept_uri &&
			JSON.stringify(currentStoreConcept.attributes) ===
				JSON.stringify(updated.attributes) &&
			JSON.stringify(currentStoreConcept.synonyms) ===
				JSON.stringify(updated.synonyms) &&
			JSON.stringify(currentStoreConcept.external_mappings) ===
				JSON.stringify(updated.external_mappings)
		) {
			setValidationErrors([]);
			setSaveSuccess(true);
			if (saveTimeoutRef.current) {
				clearTimeout(saveTimeoutRef.current);
			}
			saveTimeoutRef.current = setTimeout(
				() => setSaveSuccess(false),
				SAVE_FEEDBACK_RESET_MS,
			);
			if (onSave) {
				onSave(updated);
			}
			return;
		}

		updateConcept(selectedConcept.concept_uri, updated);
		initialSnapshotRef.current = JSON.stringify(updated);
		setValidationErrors([]);
		setSaveSuccess(true);
		if (saveTimeoutRef.current) {
			clearTimeout(saveTimeoutRef.current);
		}
		saveTimeoutRef.current = setTimeout(
			() => setSaveSuccess(false),
			SAVE_FEEDBACK_RESET_MS,
		);

		if (onSave) {
			onSave(updated);
		}
	};

	const handleClose = () => {
		prevSelectedUriRef.current = undefined;
		initialSnapshotRef.current = "";
		setValidationErrors([]);
		setSaveSuccess(false);
		selectNode(null);
		if (onClose) {
			onClose();
		}
	};

	const handleCloseProposal = () => {
		selectProposal(null);
		if (selectedNodeId !== null) {
			selectNode(null);
		}
		if (onClose) {
			onClose();
		}
	};

	return (
		<aside
			className={`visual-editor-drawer ${className}`}
			aria-label="Concept Inspection and Visual Editor Drawer"
			data-testid="visual-editor-drawer"
		>
			{!hideProposalTab && (
				<div
					className="right-drawer-tabs"
					role="tablist"
					aria-label="Right Panel Inspector Tabs"
					data-testid="right-drawer-tabs"
				>
					<button
						type="button"
						role="tab"
						aria-selected={activeTab === "concept"}
						className={`right-drawer-tab-btn${activeTab === "concept" ? " active" : ""}`}
						onClick={() => {
							if (selectedConcept) {
								setRightDrawerTab("concept");
							} else {
								selectProposal(null);
							}
						}}
						data-testid="right-tab-concept"
					>
						Concept Editor
					</button>
					<button
						type="button"
						role="tab"
						aria-selected={activeTab === "proposal"}
						className={`right-drawer-tab-btn${activeTab === "proposal" ? " active" : ""}`}
						onClick={() => setRightDrawerTab("proposal")}
						data-testid="right-tab-proposal"
					>
						Proposal Details
						{proposals.length > 0 ? ` (${proposals.length})` : ""}
					</button>
				</div>
			)}

			{activeTab === "proposal" ? (
				<ProposalDetailsView
					proposal={selectedProposal}
					opFilter={proposalOpFilter}
					onOpFilterChange={setProposalOpFilter}
					onSelectProposal={(pid) => selectProposal(pid)}
					onInspectConcept={(uri) => selectNode(uri)}
					onClose={handleCloseProposal}
				/>
			) : (
				selectedConcept && (
					<>
						{/* Drawer Header */}
						<div className="drawer-header">
							<div className="drawer-title-group">
								<span className="drawer-badge">CONCEPT EDITOR</span>
								<h3
									className="drawer-title"
									title={selectedConcept.concept_uri}
								>
									{selectedConcept.concept_name}
								</h3>
								<code className="drawer-uri">
									{selectedConcept.concept_uri}
								</code>
							</div>
							<button
								type="button"
								className="btn-close"
								onClick={handleClose}
								aria-label="Close editor drawer"
								data-testid="close-drawer-btn"
							>
								✕
							</button>
						</div>

						{/* Validation Errors */}
						{validationErrors.length > 0 && (
							<div
								className="drawer-error-banner"
								role="alert"
								data-testid="drawer-validation-errors"
							>
								{validationErrors.map((err) => (
									<p key={err} className="error-item">
										⚠️ {err}
									</p>
								))}
							</div>
						)}

						{saveSuccess && (
							<div
								className="drawer-success-banner"
								data-testid="drawer-save-success"
							>
								✓ Changes saved to working draft (strictly additive, pending
								commit).
							</div>
						)}

						{/* Drawer Form Body */}
						<div className="drawer-body">
							{/* 1. Core Metadata */}
							<section className="form-section">
								<h4 className="section-heading">Core Metadata</h4>

								<div className="form-group">
									<label htmlFor="concept-name-input">Concept Name:</label>
									<input
										id="concept-name-input"
										type="text"
										className="form-input"
										value={conceptName}
										disabled={isConceptReadOnly}
										onChange={(e) => setConceptName(e.target.value)}
										data-testid="concept-name-input"
									/>
								</div>

								<div className="form-group">
									<label htmlFor="elm-archetype-select">ELM Archetype:</label>
									<select
										id="elm-archetype-select"
										className="form-select"
										value={elmArchetype}
										disabled={isConceptReadOnly}
										onChange={(e) => setElmArchetype(e.target.value)}
										data-testid="elm-archetype-select"
									>
										{CONCEPT_ARCHETYPES.map((arch) => (
											<option key={arch} value={arch}>
												{arch}
											</option>
										))}
									</select>
								</div>

								<div className="form-group">
									<label htmlFor="concept-def-input">
										Business Definition:
									</label>
									<textarea
										id="concept-def-input"
										className="form-textarea"
										rows={3}
										value={definition}
										disabled={isConceptReadOnly}
										onChange={(e) => setDefinition(e.target.value)}
										data-testid="concept-definition-input"
									/>
								</div>

								<div className="form-group">
									<label htmlFor="concept-desc-input">
										Concept Description:
									</label>
									<textarea
										id="concept-desc-input"
										className="form-textarea"
										rows={2}
										value={description}
										disabled={isConceptReadOnly}
										onChange={(e) => setDescription(e.target.value)}
										data-testid="concept-description-input"
									/>
								</div>

								<div className="form-group">
									<label htmlFor="target-hub-input">Target Hub:</label>
									<input
										id="target-hub-input"
										type="text"
										className="form-input"
										placeholder="hub_concept_name"
										value={targetHub}
										disabled={isConceptReadOnly}
										onChange={(e) => setTargetHub(e.target.value)}
										data-testid="target-hub-input"
									/>
								</div>

								<div className="form-group">
									<label htmlFor="parent-uri-input">
										Parent Concept URI (Inheritance):
									</label>
									<input
										id="parent-uri-input"
										type="text"
										className="form-input"
										placeholder="urn:ontology:entity:parent_concept"
										value={parentConceptUri}
										disabled={isConceptReadOnly}
										onChange={(e) => setParentConceptUri(e.target.value)}
										data-testid="parent-concept-uri-input"
									/>
								</div>
							</section>

							{/* 2. Conceptual Attributes */}
							<section
								className="form-section"
								data-testid="attributes-section"
							>
								<div className="section-title-row">
									<h4 className="section-heading">
										Attributes ({attributes.length})
									</h4>
								</div>

								{/* Attribute List */}
								<div className="attributes-table-container">
									{attributes.length === 0 ? (
										<p className="empty-subtext">No attributes defined yet.</p>
									) : (
										<ul className="attribute-items-list">
											{attributes.map((attr) => (
												<li
													key={attr.attribute_name}
													className="attribute-item-row"
													data-testid={`attr-row-${attr.attribute_name}`}
												>
													<div className="attr-item-info">
														<strong className="attr-name">
															{attr.attribute_name}
														</strong>
														<span className="attr-type">:{attr.data_type}</span>
														{attr.is_optional && (
															<span className="attr-opt-pill">optional</span>
														)}
													</div>
													<button
														type="button"
														className="btn-danger btn-xs"
														onClick={() =>
															handleRemoveAttribute(attr.attribute_name)
														}
														disabled={isConceptReadOnly}
														aria-label={`Remove attribute ${attr.attribute_name}`}
														data-testid={`remove-attr-${attr.attribute_name}`}
													>
														✕
													</button>
												</li>
											))}
										</ul>
									)}
								</div>

								{/* Add Attribute Row */}
								<div className="add-item-box">
									<div className="add-item-inputs">
										<input
											type="text"
											className="form-input-sm"
											placeholder="attribute_name"
											value={newAttrName}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewAttrName(e.target.value)}
											data-testid="new-attr-name-input"
										/>
										<select
											className="form-select-sm"
											value={newAttrType}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewAttrType(e.target.value)}
											data-testid="new-attr-type-select"
										>
											{DATA_TYPES.map((dt) => (
												<option key={dt} value={dt}>
													{dt}
												</option>
											))}
										</select>
										<label className="checkbox-sm">
											<input
												type="checkbox"
												checked={newAttrOptional}
												disabled={isConceptReadOnly}
												onChange={(e) => setNewAttrOptional(e.target.checked)}
												data-testid="new-attr-optional-checkbox"
											/>
											<span>opt</span>
										</label>
									</div>
									<button
										type="button"
										className="btn-secondary btn-sm"
										onClick={handleAddAttribute}
										disabled={isConceptReadOnly || !newAttrName.trim()}
										data-testid="add-attribute-btn"
									>
										Add Attribute
									</button>
								</div>
							</section>

							{/* 3. Discovered Synonyms */}
							<section className="form-section" data-testid="synonyms-section">
								<h4 className="section-heading">
									Synonyms ({synonyms.length})
								</h4>
								<div className="synonyms-chip-container">
									{synonyms.map((syn) => (
										<span
											key={syn}
											className="synonym-chip"
											data-testid={`synonym-chip-${syn}`}
										>
											{syn}
											<button
												type="button"
												className="chip-remove-btn"
												onClick={() => handleRemoveSynonym(syn)}
												disabled={isConceptReadOnly}
												aria-label={`Remove synonym ${syn}`}
											>
												✕
											</button>
										</span>
									))}
								</div>
								<div className="add-synonym-row">
									<input
										type="text"
										className="form-input-sm"
										placeholder="Add business synonym..."
										value={newSynonym}
										disabled={isConceptReadOnly}
										onChange={(e) => setNewSynonym(e.target.value)}
										data-testid="new-synonym-input"
									/>
									<button
										type="button"
										className="btn-secondary btn-sm"
										onClick={handleAddSynonym}
										disabled={isConceptReadOnly || !newSynonym.trim()}
										data-testid="add-synonym-btn"
									>
										Add
									</button>
								</div>
							</section>

							{/* 4. Reference External Mappings */}
							<section className="form-section" data-testid="mappings-section">
								<h4 className="section-heading">
									FIBO / External Mappings ({externalMappings.length})
								</h4>
								<p className="mapping-guidance-hint">
									<strong>ONT-EVO-002 Grounding:</strong> Path A requires at
									least one server-verified FIBO mapping using{" "}
									<code>skos:exactMatch</code> or <code>skos:closeMatch</code>{" "}
									with confidence &gt;= {GROUNDING_MIN_CONFIDENCE}. Path B lets
									an Admin authorize custom or <code>user_asserted</code>{" "}
									concepts via a Steward Waiver in the Quality tab.
								</p>
								<div className="mappings-list">
									{externalMappings.map((m, idx) => (
										<div
											key={`${m.scheme}-${m.target_uri}-${idx}`}
											className="mapping-item-card"
											data-testid={`mapping-card-${m.target_uri}`}
										>
											<div className="mapping-card-main">
												<span className="mapping-scheme">
													{m.scheme.toUpperCase()}
												</span>
												<span
													className="mapping-predicate-badge"
													data-testid="mapping-predicate-badge"
												>
													{m.predicate || "skos:relatedMatch"}
												</span>
												<span className="mapping-uri">{m.target_uri}</span>
												{m.provenance === "user_asserted" ? (
													<span
														className="mapping-provenance-badge badge-user-asserted"
														data-testid="mapping-user-asserted-badge"
														title="Manually asserted mapping — unverified until server FIBO validation"
													>
														USER-ASSERTED (Unverified)
													</span>
												) : (
													<span className="mapping-conf">
														{m.confidence != null
															? `${(m.confidence * PERCENTAGE_MULTIPLIER).toFixed(0)}%`
															: ""}
													</span>
												)}
											</div>
											<div className="mapping-card-actions">
												<button
													type="button"
													className={`btn-secondary btn-xs ${m.pinned ? "active" : ""}`}
													onClick={() => handleToggleMappingPin(idx)}
													disabled={isConceptReadOnly}
													aria-label={
														m.pinned
															? `Unpin mapping ${m.target_uri}`
															: `Pin mapping ${m.target_uri}`
													}
													data-testid={`toggle-pin-mapping-${idx}`}
												>
													{m.pinned ? "📌 Pinned" : "Pin"}
												</button>
												<button
													type="button"
													className="btn-danger btn-xs"
													onClick={() => handleRemoveMapping(idx)}
													disabled={isConceptReadOnly}
													aria-label={`Remove mapping ${m.target_uri}`}
												>
													✕
												</button>
											</div>
										</div>
									))}
								</div>

								<div className="add-mapping-box">
									<div className="add-mapping-inputs">
										<select
											className="form-select-sm"
											value={newMappingScheme}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewMappingScheme(e.target.value)}
											data-testid="new-mapping-scheme-select"
										>
											{EXTERNAL_MAPPING_SCHEME_OPTIONS.map((opt) => (
												<option key={opt.value} value={opt.value}>
													{opt.label}
												</option>
											))}
										</select>
										<select
											aria-label="SKOS Mapping Predicate"
											className="form-select-sm"
											value={newMappingPredicate}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewMappingPredicate(e.target.value)}
											data-testid="new-mapping-predicate-select"
										>
											{SKOS_PREDICATES.map((pred) => (
												<option key={pred} value={pred}>
													{pred}
												</option>
											))}
										</select>
										<input
											type="text"
											className="form-input-sm"
											placeholder="Target URI (e.g. fibo-fbc:Fund)"
											value={newMappingUri}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewMappingUri(e.target.value)}
											data-testid="new-mapping-uri-input"
										/>
										<input
											type="number"
											step={MAPPING_CONFIDENCE_STEP}
											min={MIN_MAPPING_CONFIDENCE}
											max={MAX_MAPPING_CONFIDENCE}
											className="form-input-sm conf-input"
											placeholder={DEFAULT_MAPPING_CONFIDENCE_INPUT}
											value={newMappingConf}
											disabled={isConceptReadOnly}
											onChange={(e) => setNewMappingConf(e.target.value)}
											data-testid="new-mapping-conf-input"
										/>
									</div>
									<button
										type="button"
										className="btn-secondary btn-sm"
										onClick={handleAddMapping}
										disabled={isConceptReadOnly || !newMappingUri.trim()}
										data-testid="add-mapping-btn"
									>
										Add Mapping (User-Asserted)
									</button>
								</div>
							</section>
						</div>

						{/* Drawer Footer Actions */}
						<div className="drawer-footer">
							<button
								type="button"
								className="btn-danger"
								onClick={() => deleteConcept(selectedConcept.concept_uri)}
								disabled={isConceptReadOnly || (isCommitted && !isAdmin)}
								data-testid="delete-concept-btn"
								title={
									isHistoricalView
										? "Switch to active head version before deleting concepts"
										: !canMutateConcept
											? "Editor or Admin human role required to modify concepts"
											: isCommitted && !isAdmin
												? "Admin role required to delete committed concepts"
												: `Delete concept ${selectedConcept.concept_name}`
								}
							>
								Delete Concept
							</button>
							<button
								type="button"
								className="btn-secondary"
								onClick={handleClose}
								data-testid="cancel-edit-btn"
							>
								Cancel
							</button>
							<button
								type="button"
								className="btn-primary"
								onClick={handleSave}
								disabled={isConceptReadOnly}
								data-testid="save-concept-btn"
							>
								Save Concept Changes
							</button>
						</div>
					</>
				)
			)}
		</aside>
	);
};
