/**
 * Visual Editor Drawer Component (Task 5D.5, #226)
 * (apps/opendv-web-workbench/src/ontology/components/VisualEditorDrawer.tsx)
 */

import type React from "react";
import { useEffect, useRef, useState } from "react";
import {
	CONFIDENCE_DECIMAL_PLACES,
	DEFAULT_CONCEPT_ARCHETYPE,
	DEFAULT_MAPPING_CONFIDENCE_INPUT,
	DEFAULT_MAPPING_PREDICATE,
	DEFAULT_MAPPING_SCHEME,
	MAX_ATTRIBUTE_NAME_LENGTH,
	MAX_MAPPING_CONFIDENCE,
	MAX_SYNONYMS_PER_CONCEPT,
	MAX_SYNONYM_LENGTH,
	MIN_MAPPING_CONFIDENCE,
	SAVE_FEEDBACK_RESET_MS,
} from "../constants";
import { useStudioStore } from "../store/studio-store";
import type {
	ConceptAttribute,
	ConceptNode,
	ExternalMapping,
} from "../store/types";
import { ConceptEditorSections } from "./ConceptEditorSections";
import {
	type ConceptEditDraft,
	validateConceptEdit,
} from "./concept-edit-validation";

export { type ConceptEditDraft, validateConceptEdit };

export interface VisualEditorDrawerProps {
	className?: string;
	hideProposalTab?: boolean | undefined;
	onClose?: () => void;
	onSave?: (updatedConcept: ConceptNode) => void;
}

export const VisualEditorDrawer: React.FC<VisualEditorDrawerProps> = ({
	className = "",
	hideProposalTab: _hideProposalTab = false,
	onClose,
	onSave,
}) => {
	const selectedNodeId = useStudioStore((s) => s.selectedNodeId);
	const nodes = useStudioStore((s) => s.nodes);
	const committedNodes = useStudioStore((s) => s.committedNodes);
	const userRole = useStudioStore((s) => s.userRole);
	const isHistoricalView = useStudioStore((s) => s.isHistoricalView);
	const addConcept = useStudioStore((s) => s.addConcept);
	const updateConcept = useStudioStore((s) => s.updateConcept);
	const deleteConcept = useStudioStore((s) => s.deleteConcept);
	const selectNode = useStudioStore((s) => s.selectNode);

	const selectedConcept = nodes.find(
		(n) => n.id === selectedNodeId || n.concept_uri === selectedNodeId,
	);
	const isCreateMode = !selectedConcept;
	const committedConcept = committedNodes.find(
		(n) => n.concept_uri === selectedConcept?.concept_uri,
	);
	const isCommitted = Boolean(committedConcept);
	const isAdmin = userRole === "admin";
	const canMutateConcept = userRole === "editor" || userRole === "admin";
	const isConceptReadOnly = !canMutateConcept || isHistoricalView;

	const [conceptUri, setConceptUri] = useState(
		selectedConcept?.concept_uri || "",
	);
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

	const [newAttrName, setNewAttrName] = useState("");
	const [newAttrType, setNewAttrType] = useState<string>("string");
	const [newAttrOptional, setNewAttrOptional] = useState(true);
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
	const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		return () => {
			if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
		};
	}, []);

	const prevSelectedUriRef = useRef<string | undefined>(
		selectedConcept?.concept_uri,
	);
	const initialSnapshotRef = useRef<string>(
		selectedConcept ? JSON.stringify(selectedConcept) : "",
	);

	useEffect(() => {
		const resetCreateFields = (concept?: ConceptNode) => {
			setConceptUri(concept?.concept_uri || "");
			setConceptName(concept?.concept_name || "");
			setElmArchetype(concept?.elm_archetype || DEFAULT_CONCEPT_ARCHETYPE);
			setDefinition(concept?.definition || "");
			setDescription(concept?.description || "");
			setTargetHub(concept?.target_hub || "");
			setParentConceptUri(concept?.parent_concept_uri || "");
			setAttributes([...(concept?.attributes || [])]);
			setSynonyms([...(concept?.synonyms || [])]);
			setExternalMappings([...(concept?.external_mappings || [])]);
			setNewAttrOptional(true);
			setValidationErrors([]);
			setSaveSuccess(false);
		};
		if (
			selectedConcept &&
			prevSelectedUriRef.current !== selectedConcept.concept_uri
		) {
			prevSelectedUriRef.current = selectedConcept.concept_uri;
			initialSnapshotRef.current = JSON.stringify(selectedConcept);
			resetCreateFields(selectedConcept);
		} else if (!selectedConcept && prevSelectedUriRef.current !== undefined) {
			prevSelectedUriRef.current = undefined;
			initialSnapshotRef.current = "";
			resetCreateFields(undefined);
		}
	}, [selectedConcept]);

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
		setAttributes([
			...attributes,
			{
				attribute_name: cleanName,
				data_type: newAttrType,
				is_optional: newAttrOptional,
				description: null,
			},
		]);
		setNewAttrName("");
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
		setExternalMappings([
			...externalMappings,
			{
				scheme: newMappingScheme,
				target_uri: cleanUri,
				confidence: Number(conf.toFixed(CONFIDENCE_DECIMAL_PLACES)),
				predicate: newMappingPredicate,
				provenance: "user_asserted",
			},
		]);
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

	const handleRemoveMapping = (idx: number) => {
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

	const markSaved = (savedNode: ConceptNode) => {
		initialSnapshotRef.current = JSON.stringify(savedNode);
		setValidationErrors([]);
		setSaveSuccess(true);
		if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
		saveTimeoutRef.current = setTimeout(
			() => setSaveSuccess(false),
			SAVE_FEEDBACK_RESET_MS,
		);
		onSave?.(savedNode);
	};

	const handleSave = () => {
		const trimmedDef = definition.trim();
		const trimmedDesc = description.trim();
		const trimmedHub = targetHub.trim();
		const trimmedParent = parentConceptUri.trim();
		const isNew = isCreateMode || !selectedConcept;
		const targetUri = isNew
			? conceptUri.trim() ||
				`urn:opendv:concept:${
					conceptName
						.trim()
						.toLowerCase()
						.replace(/[^a-z0-9]+/g, "_")
						.replace(/^_+|_+$/g, "") || "new_concept"
				}`
			: selectedConcept.concept_uri;

		const currentStoreConcept = !isNew
			? nodes.find((n) => n.concept_uri === targetUri)
			: undefined;
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
		const draft: ConceptEditDraft = {
			concept_uri: targetUri,
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
			!isNew && isCommitted ? committedConcept : null,
			nodes,
			{ allowDestructive: isAdmin },
		);
		if (isNew && nodes.some((n) => n.concept_uri === targetUri)) {
			errors.push(`Concept URI '${targetUri}' already exists in ontology.`);
		}
		if (errors.length > 0) {
			setValidationErrors(errors);
			return;
		}
		if (isNew) {
			const newNode: ConceptNode = {
				id: targetUri,
				concept_uri: targetUri,
				concept_name: conceptName.trim(),
				elm_archetype: elmArchetype,
				definition: trimmedDef,
				...(trimmedDesc ? { description: trimmedDesc } : {}),
				...(trimmedHub ? { target_hub: trimmedHub } : {}),
				...(trimmedParent ? { parent_concept_uri: trimmedParent } : {}),
				grounding_status:
					externalMappings.length > 0 ? "GROUNDED" : "UNGROUNDED",
				attributes,
				synonyms,
				external_mappings: externalMappings,
			};
			addConcept(newNode);
			selectNode(newNode.concept_uri);
			prevSelectedUriRef.current = newNode.concept_uri;
			markSaved(newNode);
			return;
		}
		const updated: ConceptNode = {
			...selectedConcept,
			concept_name: conceptName.trim(),
			elm_archetype: elmArchetype,
			definition: (trimmedDef.length > 0
				? trimmedDef
				: undefined) as unknown as string,
			description: trimmedDesc.length > 0 ? trimmedDesc : undefined,
			target_hub: trimmedHub.length > 0 ? trimmedHub : undefined,
			parent_concept_uri: trimmedParent.length > 0 ? trimmedParent : undefined,
			attributes,
			synonyms,
			external_mappings: externalMappings,
		};
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
			markSaved(updated);
			return;
		}
		updateConcept(selectedConcept.concept_uri, updated);
		markSaved(updated);
	};

	const handleClose = () => {
		prevSelectedUriRef.current = undefined;
		initialSnapshotRef.current = "";
		setValidationErrors([]);
		setSaveSuccess(false);
		selectNode(null);
		onClose?.();
	};

	if (!selectedConcept && !onClose && !_hideProposalTab) {
		return null;
	}

	return (
		<aside
			className={`visual-editor-drawer ${className}`}
			aria-label="Concept Inspection and Visual Editor Drawer"
			data-testid="visual-editor-drawer"
		>
			<div className="drawer-header">
				<div className="drawer-title-group">
					<span className="drawer-badge">
						{isCreateMode ? "NEW CONCEPT" : "CONCEPT EDITOR"}
					</span>
					<h3
						className="drawer-title"
						title={
							isCreateMode
								? conceptUri || "New concept"
								: selectedConcept.concept_uri
						}
					>
						{isCreateMode
							? conceptName.trim() || "Create New Concept"
							: selectedConcept.concept_name}
					</h3>
					{isCreateMode ? (
						<input
							type="text"
							className="form-input"
							data-testid="concept-uri-input"
							aria-label="Concept URI"
							placeholder="urn:opendv:concept:new_concept"
							value={conceptUri}
							disabled={isConceptReadOnly}
							onChange={(e) => setConceptUri(e.target.value)}
						/>
					) : (
						<code className="drawer-uri">{selectedConcept.concept_uri}</code>
					)}
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
					✓ Changes saved to working draft (strictly additive, pending commit).
				</div>
			)}

			<ConceptEditorSections
				isConceptReadOnly={isConceptReadOnly}
				conceptName={conceptName}
				setConceptName={setConceptName}
				elmArchetype={elmArchetype}
				setElmArchetype={setElmArchetype}
				definition={definition}
				setDefinition={setDefinition}
				description={description}
				setDescription={setDescription}
				targetHub={targetHub}
				setTargetHub={setTargetHub}
				parentConceptUri={parentConceptUri}
				setParentConceptUri={setParentConceptUri}
				attributes={attributes}
				newAttrName={newAttrName}
				setNewAttrName={setNewAttrName}
				newAttrType={newAttrType}
				setNewAttrType={setNewAttrType}
				newAttrOptional={newAttrOptional}
				setNewAttrOptional={setNewAttrOptional}
				onAddAttribute={handleAddAttribute}
				onRemoveAttribute={handleRemoveAttribute}
				synonyms={synonyms}
				newSynonym={newSynonym}
				setNewSynonym={setNewSynonym}
				onAddSynonym={handleAddSynonym}
				onRemoveSynonym={handleRemoveSynonym}
				externalMappings={externalMappings}
				newMappingScheme={newMappingScheme}
				setNewMappingScheme={setNewMappingScheme}
				newMappingPredicate={newMappingPredicate}
				setNewMappingPredicate={setNewMappingPredicate}
				newMappingUri={newMappingUri}
				setNewMappingUri={setNewMappingUri}
				newMappingConf={newMappingConf}
				setNewMappingConf={setNewMappingConf}
				onAddMapping={handleAddMapping}
				onToggleMappingPin={handleToggleMappingPin}
				onRemoveMapping={handleRemoveMapping}
			/>

			<div className="drawer-footer">
				{!isCreateMode && selectedConcept && (
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
				)}
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
					{isCreateMode ? "Create Concept" : "Save Concept Changes"}
				</button>
			</div>
		</aside>
	);
};
