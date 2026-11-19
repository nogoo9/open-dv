/**
 * Concept Edit Draft & ONT-EVO-001 / ONT-DAG-001 Validation
 * (apps/opendv-web-workbench/src/ontology/components/concept-edit-validation.ts)
 */

import { PARENT_CONCEPT_URN_REGEX, TARGET_HUB_REGEX } from "../constants";
import type {
	ConceptAttribute,
	ConceptNode,
	ExternalMapping,
} from "../store/types";

export const DATA_TYPES = [
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
