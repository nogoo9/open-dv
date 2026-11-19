/**
 * Pre-Commit Structural & Additive Evolution Validation
 * (apps/opendv-web-workbench/src/ontology/store/graph-commit-validation.ts)
 */

import type { StudioStoreState } from "./store-types";
import type { ConceptNode, RelationshipEdge } from "./types";

export interface CommitValidationError {
	type: string;
	title: string;
	status: number;
	detail: string;
	code: string;
}

export function normalizeEdgeEndpoints(e: RelationshipEdge): string[] {
	return e.endpoints && e.endpoints.length >= 2
		? e.endpoints
		: [e.source_concept_uri, e.target_concept_uri];
}

export function edgeEndpointsEqual(a: string[], b: string[]): boolean {
	return a.length === b.length && a.every((ep, idx) => ep === b[idx]);
}

export function validateCommitPreconditions(
	state: StudioStoreState,
): CommitValidationError | null {
	const currentRole = state.userRole;
	if (currentRole === "viewer" || currentRole === "automated") {
		return {
			type: "urn:opendv:error:forbidden-role",
			title: "Human Steward Role Required",
			status: 403,
			detail: `Role '${currentRole}' cannot commit manual ontology deltas. Switch to an authenticated Editor or Admin steward session.`,
			code: "FORBIDDEN_ROLE",
		};
	}
	if (state.isHistoricalView) {
		return {
			type: "urn:opendv:error:historical-version-commit-blocked",
			title: "Historical Version Commit Blocked",
			status: 400,
			detail:
				"Cannot commit changes while viewing a historical ontology version. Please switch to the active head version.",
			code: "HISTORICAL_VERSION_COMMIT_BLOCKED",
		};
	}
	if (!state.activeSession?.head_sha256) {
		return {
			type: "urn:opendv:error:missing-head-sha",
			title: "Missing Head SHA",
			status: 400,
			detail: "Cannot commit delta without an active session head SHA.",
			code: "MISSING_HEAD_SHA",
		};
	}
	if (!/^[0-9a-f]{64}$/i.test(state.activeSession.head_sha256)) {
		return {
			type: "urn:opendv:error:invalid-sha256-format",
			title: "Invalid Head Digest",
			status: 400,
			detail:
				"Session head_sha256 must be a valid 64-character hexadecimal SHA-256 digest.",
			code: "INVALID_SHA256_DIGEST",
		};
	}
	const baseVersion = (state.activeSession.active_version || "").trim();
	if (!baseVersion || !/^(\d+)\.(\d+)\.(\d+)/.test(baseVersion)) {
		return {
			type: "urn:opendv:error:missing-active-version",
			title: "Missing Active Version",
			status: 400,
			detail:
				"Session active_version must be a valid SemVer string (e.g., 0.0.0 or 1.0.0) before committing.",
			code: "MISSING_ACTIVE_VERSION",
		};
	}
	return null;
}

export function validateCommitAdditiveMutations(params: {
	committedNodes: ConceptNode[];
	currentNodeMap: Map<string, ConceptNode>;
	committedEdges: RelationshipEdge[];
	currentEdgeMap: Map<string, RelationshipEdge>;
	allowDestructive: boolean;
}): {
	error: CommitValidationError | null;
	removedConcepts: ConceptNode[];
	removedEdges: RelationshipEdge[];
	modifiedEdgeUris: Set<string>;
	modifiedCommittedEdges: RelationshipEdge[];
} {
	const {
		committedNodes,
		currentNodeMap,
		committedEdges,
		currentEdgeMap,
		allowDestructive,
	} = params;

	const fail = (
		title: string,
		detail: string,
	): {
		error: CommitValidationError;
		removedConcepts: ConceptNode[];
		removedEdges: RelationshipEdge[];
		modifiedEdgeUris: Set<string>;
		modifiedCommittedEdges: RelationshipEdge[];
	} => ({
		error: {
			type: "urn:opendv:error:non-additive-change",
			title,
			status: 400,
			detail,
			code: "ONT_EVO_NON_ADDITIVE_CHANGE",
		},
		removedConcepts: [],
		removedEdges: [],
		modifiedEdgeUris: new Set(),
		modifiedCommittedEdges: [],
	});

	const removedEdges = committedEdges.filter(
		(e) => !currentEdgeMap.has(e.relationship_uri),
	);
	if (removedEdges.length > 0 && !allowDestructive) {
		return fail(
			"Non-Additive Change Blocked",
			"Deleting relationships violates ONT-EVO-001 additive evolution without admin authorization.",
		);
	}

	const removedConcepts = committedNodes.filter(
		(c) => !currentNodeMap.has(c.concept_uri),
	);
	if (removedConcepts.length > 0 && !allowDestructive) {
		return fail(
			"Non-Additive Change Blocked",
			"Deleting concepts violates ONT-EVO-001 strictly additive evolution without admin authorization.",
		);
	}

	for (const committed of committedNodes) {
		const current = currentNodeMap.get(committed.concept_uri);
		if (!current) continue;

		if (committed.concept_name !== current.concept_name) {
			return fail(
				"Concept Renaming Blocked",
				`Renaming concept '${committed.concept_name}' to '${current.concept_name}' cannot be committed via additive evolution deltas.`,
			);
		}
		if (
			committed.elm_archetype !== current.elm_archetype &&
			Boolean((committed.elm_archetype || "").trim()) &&
			!allowDestructive
		) {
			return fail(
				"Archetype Mutation Blocked",
				`Modifying archetype on '${committed.concept_name}' requires admin authorization under ONT-EVO-001.`,
			);
		}
		if ((committed.target_hub ?? null) !== (current.target_hub ?? null)) {
			return fail(
				"Target Hub Mutation Blocked",
				`Altering target hub on '${committed.concept_name}' is not supported in evolution deltas.`,
			);
		}
		if (
			(committed.parent_concept_uri ?? null) !==
				(current.parent_concept_uri ?? null) &&
			Boolean((committed.parent_concept_uri || "").trim()) &&
			!allowDestructive
		) {
			return fail(
				"Inheritance Hierarchy Mutation Blocked",
				`Overwriting or clearing parent concept on '${committed.concept_name}' requires admin authorization under ONT-EVO-001.`,
			);
		}

		const currentSynSet = new Set(current.synonyms || []);
		if ((committed.synonyms || []).some((s) => !currentSynSet.has(s))) {
			return fail(
				"Synonym Removal Blocked",
				`Removing existing synonyms on '${committed.concept_name}' cannot be expressed via delta operations.`,
			);
		}

		const currentAttrMap = new Map(
			(current.attributes || []).map((a) => [a.attribute_name, a]),
		);
		const committedAttrNameSet = new Set(
			(committed.attributes || []).map((a) => a.attribute_name),
		);
		if (
			(current.attributes || []).some(
				(a) =>
					!committedAttrNameSet.has(a.attribute_name) &&
					a.is_optional === false,
			) &&
			!allowDestructive
		) {
			return fail(
				"Non-Additive Change Blocked",
				"Adding mandatory (non-optional) attributes to committed concepts is a MAJOR breaking change under ONT-EVO-001 and requires admin authorization.",
			);
		}
		if (
			(committed.attributes || []).some(
				(a) => !currentAttrMap.has(a.attribute_name),
			) &&
			!allowDestructive
		) {
			return fail(
				"Non-Additive Change Blocked",
				"Removing attributes violates ONT-EVO-001 strictly additive evolution without admin authorization.",
			);
		}
		for (const a of committed.attributes || []) {
			const curr = currentAttrMap.get(a.attribute_name);
			if (!curr) continue;
			if (curr.data_type !== a.data_type) {
				return fail(
					"Non-Additive Change Blocked",
					"Altering attribute data types violates ONT-EVO-001 strictly additive evolution.",
				);
			}
			if ((curr.description || "") !== (a.description || "")) {
				return fail(
					"Attribute Description Mutation Blocked",
					"Modifying descriptions of existing attributes cannot be expressed via evolution deltas.",
				);
			}
			if ((curr.is_optional ?? true) !== (a.is_optional ?? true)) {
				return fail(
					"Attribute Optionality Mutation Blocked",
					"Modifying optionality of existing attributes cannot be expressed via evolution deltas.",
				);
			}
			if (
				JSON.stringify(curr.example_values || []) !==
				JSON.stringify(a.example_values || [])
			) {
				return fail(
					"Attribute Example Values Mutation Blocked",
					"Modifying example values of existing attributes cannot be expressed via evolution deltas.",
				);
			}
		}

		const currentMappingMap = new Map(
			(current.external_mappings || []).map((m) => [m.target_uri, m]),
		);
		if (
			(committed.external_mappings || []).some(
				(m) => !currentMappingMap.has(m.target_uri),
			) &&
			!allowDestructive
		) {
			return fail(
				"Removed External Mappings Blocked",
				"Removing reference external mappings violates regulatory stability under ONT-EVO-002 without admin authorization.",
			);
		}
		for (const m of committed.external_mappings || []) {
			const currM = currentMappingMap.get(m.target_uri);
			if (!currM) continue;
			if (
				currM.scheme !== m.scheme ||
				(currM.predicate ?? null) !== (m.predicate ?? null)
			) {
				return fail(
					"External Mapping Mutation Blocked",
					`Modifying scheme or predicate of existing external mapping '${m.target_uri}' requires removing and re-adding the mapping.`,
				);
			}
			const prevConf = m.mapping_confidence ?? m.confidence ?? null;
			const nextConf = currM.mapping_confidence ?? currM.confidence ?? null;
			if (nextConf !== prevConf) {
				if (typeof nextConf !== "number" || !Number.isFinite(nextConf)) {
					return fail(
						"External Mapping Confidence Invalid",
						`Rescoring mapping '${m.target_uri}' requires a valid numeric confidence in [0, 1].`,
					);
				}
				if (Boolean(m.pinned) && Boolean(currM.pinned ?? m.pinned)) {
					return fail(
						"Pinned External Mapping Mutation Blocked",
						`Cannot rescore pinned external mapping '${m.target_uri}' without unpinning it first.`,
					);
				}
			}
		}
	}

	const modifiedEdgeUris = new Set<string>();
	const modifiedCommittedEdges: RelationshipEdge[] = [];
	for (const committedEdge of committedEdges) {
		const currentEdge = currentEdgeMap.get(committedEdge.relationship_uri);
		if (!currentEdge) continue;

		const isEdgeMutated =
			committedEdge.target_link !== currentEdge.target_link ||
			(committedEdge.cardinality ?? null) !==
				(currentEdge.cardinality ?? null) ||
			committedEdge.source_concept_uri !== currentEdge.source_concept_uri ||
			committedEdge.target_concept_uri !== currentEdge.target_concept_uri ||
			(committedEdge.description ?? "") !== (currentEdge.description ?? "") ||
			(currentEdge.relationship_name ?? "") !==
				(committedEdge.relationship_name ?? "") ||
			(currentEdge.grain_semantics ?? "") !==
				(committedEdge.grain_semantics ?? "") ||
			!edgeEndpointsEqual(
				normalizeEdgeEndpoints(currentEdge),
				normalizeEdgeEndpoints(committedEdge),
			);

		if (isEdgeMutated) {
			if (!allowDestructive) {
				return fail(
					"Relationship Mutation Blocked",
					"Modifying properties or endpoints of existing relationships violates ONT-EVO-001 additive evolution without admin authorization.",
				);
			}
			modifiedEdgeUris.add(committedEdge.relationship_uri);
			modifiedCommittedEdges.push(committedEdge);
		}
	}

	return {
		error: null,
		removedConcepts,
		removedEdges,
		modifiedEdgeUris,
		modifiedCommittedEdges,
	};
}
