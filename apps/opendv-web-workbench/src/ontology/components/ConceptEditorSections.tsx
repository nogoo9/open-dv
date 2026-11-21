/**
 * Presentational Sections for Concept Visual Editor Drawer
 * (apps/opendv-web-workbench/src/ontology/components/ConceptEditorSections.tsx)
 */

import type React from "react";
import {
	CONCEPT_ARCHETYPES,
	DEFAULT_MAPPING_CONFIDENCE_INPUT,
	EXTERNAL_MAPPING_SCHEME_OPTIONS,
	GROUNDING_MIN_CONFIDENCE,
	MAPPING_CONFIDENCE_STEP,
	MAX_MAPPING_CONFIDENCE,
	MIN_MAPPING_CONFIDENCE,
	PERCENTAGE_MULTIPLIER,
	SKOS_PREDICATES,
} from "../constants";
import type { ConceptAttribute, ExternalMapping } from "../store/types";
import { DATA_TYPES } from "./concept-edit-validation";

export interface ConceptEditorSectionsProps {
	isConceptReadOnly: boolean;
	conceptName: string;
	setConceptName: (val: string) => void;
	elmArchetype: string;
	setElmArchetype: (val: string) => void;
	definition: string;
	setDefinition: (val: string) => void;
	description: string;
	setDescription: (val: string) => void;
	targetHub: string;
	setTargetHub: (val: string) => void;
	parentConceptUri: string;
	setParentConceptUri: (val: string) => void;
	attributes: ConceptAttribute[];
	newAttrName: string;
	setNewAttrName: (val: string) => void;
	newAttrType: string;
	setNewAttrType: (val: string) => void;
	newAttrOptional: boolean;
	setNewAttrOptional: (val: boolean) => void;
	onAddAttribute: () => void;
	onRemoveAttribute: (name: string) => void;
	synonyms: string[];
	newSynonym: string;
	setNewSynonym: (val: string) => void;
	onAddSynonym: () => void;
	onRemoveSynonym: (syn: string) => void;
	externalMappings: ExternalMapping[];
	newMappingScheme: string;
	setNewMappingScheme: (val: string) => void;
	newMappingPredicate: string;
	setNewMappingPredicate: (val: string) => void;
	newMappingUri: string;
	setNewMappingUri: (val: string) => void;
	newMappingConf: string;
	setNewMappingConf: (val: string) => void;
	onAddMapping: () => void;
	onToggleMappingPin: (idx: number) => void;
	onRemoveMapping: (idx: number) => void;
}

export const ConceptEditorSections: React.FC<ConceptEditorSectionsProps> = ({
	isConceptReadOnly,
	conceptName,
	setConceptName,
	elmArchetype,
	setElmArchetype,
	definition,
	setDefinition,
	description,
	setDescription,
	targetHub,
	setTargetHub,
	parentConceptUri,
	setParentConceptUri,
	attributes,
	newAttrName,
	setNewAttrName,
	newAttrType,
	setNewAttrType,
	newAttrOptional,
	setNewAttrOptional,
	onAddAttribute,
	onRemoveAttribute,
	synonyms,
	newSynonym,
	setNewSynonym,
	onAddSynonym,
	onRemoveSynonym,
	externalMappings,
	newMappingScheme,
	setNewMappingScheme,
	newMappingPredicate,
	setNewMappingPredicate,
	newMappingUri,
	setNewMappingUri,
	newMappingConf,
	setNewMappingConf,
	onAddMapping,
	onToggleMappingPin,
	onRemoveMapping,
}) => (
	<div className="drawer-body">
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
				<label htmlFor="concept-def-input">Business Definition:</label>
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
				<label htmlFor="concept-desc-input">Concept Description:</label>
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

		<section className="form-section" data-testid="attributes-section">
			<div className="section-title-row">
				<h4 className="section-heading">Attributes ({attributes.length})</h4>
			</div>
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
									<strong className="attr-name">{attr.attribute_name}</strong>
									<span className="attr-type">:{attr.data_type}</span>
									{attr.is_optional && (
										<span className="attr-opt-pill">optional</span>
									)}
								</div>
								<button
									type="button"
									className="btn-danger btn-xs"
									onClick={() => onRemoveAttribute(attr.attribute_name)}
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
					onClick={onAddAttribute}
					disabled={isConceptReadOnly || !newAttrName.trim()}
					data-testid="add-attribute-btn"
				>
					Add Attribute
				</button>
			</div>
		</section>

		<section className="form-section" data-testid="synonyms-section">
			<h4 className="section-heading">Synonyms ({synonyms.length})</h4>
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
							onClick={() => onRemoveSynonym(syn)}
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
					onClick={onAddSynonym}
					disabled={isConceptReadOnly || !newSynonym.trim()}
					data-testid="add-synonym-btn"
				>
					Add
				</button>
			</div>
		</section>

		<section className="form-section" data-testid="mappings-section">
			<h4 className="section-heading">
				FIBO / External Mappings ({externalMappings.length})
			</h4>
			<p className="mapping-guidance-hint">
				<strong>ONT-EVO-002 Grounding:</strong> Path A requires at least one
				server-verified FIBO mapping using <code>skos:exactMatch</code> or{" "}
				<code>skos:closeMatch</code> with confidence &gt;={" "}
				{GROUNDING_MIN_CONFIDENCE}. Path B lets an Admin authorize custom or{" "}
				<code>user_asserted</code> concepts via a Steward Waiver.
			</p>
			<div className="mappings-list">
				{externalMappings.map((m, idx) => (
					<div
						key={`${m.scheme}-${m.target_uri}-${idx}`}
						className="mapping-item-card"
						data-testid={`mapping-card-${m.target_uri}`}
					>
						<div className="mapping-card-main">
							<span className="mapping-scheme">{m.scheme.toUpperCase()}</span>
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
								onClick={() => onToggleMappingPin(idx)}
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
								onClick={() => onRemoveMapping(idx)}
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
					onClick={onAddMapping}
					disabled={isConceptReadOnly || !newMappingUri.trim()}
					data-testid="add-mapping-btn"
				>
					Add Mapping (User-Asserted)
				</button>
			</div>
		</section>
	</div>
);
