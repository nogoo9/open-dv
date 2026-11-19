/**
 * Graph Canvas Editing, Selection, Load & Commit Slice
 * (apps/opendv-web-workbench/src/ontology/store/graph-editor-slice.ts)
 */

import { validateCommitGroundingAndDag } from "./graph-commit-grounding";
import {
	buildDeltaOperationsAndVersion,
	submitDeltaCommit,
} from "./graph-commit-ops";
import {
	validateCommitAdditiveMutations,
	validateCommitPreconditions,
} from "./graph-commit-validation";
import { executeLoadGraph } from "./graph-loader";
import { hasGraphChangesFromBaseline } from "./graph-parser";
import {
	deriveGroundingStatus,
	hasPreservedCommittedUnscoredFiboPathAMapping,
	recomputeScorecard,
} from "./scorecard";
import type { StoreGet, StoreSet, StudioStoreState } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type { ConceptNode, RelationshipEdge } from "./types";

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

export function createGraphEditorSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
	bumpGraphReqId: () => number,
	getGraphReqId: () => number,
) {
	const blockHistorical = (): boolean => {
		if (!get().isHistoricalView) return false;
		get().addToast({
			type: "urn:opendv:error:cannot-edit-historical",
			title: "Cannot Edit Historical Version",
			status: 400,
			detail:
				"Editing historical ontology versions is not permitted. Please switch to the active head version first.",
			code: "CANNOT_EDIT_HISTORICAL_VERSION",
		});
		return true;
	};

	return {
		nodes: [] as ConceptNode[],
		edges: [] as RelationshipEdge[],
		committedNodes: [] as ConceptNode[],
		committedEdges: [] as RelationshipEdge[],
		selectedNodeId: null as string | null,
		selectedEdgeId: null as string | null,
		isLoadingGraph: false,
		hasUnsavedChanges: false,
		viewedVersion: null as string | null,
		isHistoricalView: false,
		isCommitting: false,

		loadGraph: (
			version?: string,
			fetchImpl = fetch,
			targetSessionId?: string,
		) =>
			executeLoadGraph(
				set,
				get,
				studioFetch,
				bumpGraphReqId,
				getGraphReqId,
				version,
				fetchImpl,
				targetSessionId,
			),

		selectNode: (nodeId: string | null) => {
			set((state) => {
				if (nodeId !== null) {
					return {
						selectedNodeId: nodeId,
						selectedEdgeId: null,
						layout: {
							...state.layout,
							rightDrawerOpen: true,
							rightDrawerTab: "concept",
						},
					};
				}
				const hasProposal =
					state.selectedProposalId !== null &&
					state.layout.rightDrawerTab === "proposal";
				const isBlueprint = state.layout.rightDrawerTab === "blueprint";
				return {
					selectedNodeId: null,
					selectedEdgeId: null,
					layout: {
						...state.layout,
						rightDrawerOpen: hasProposal || isBlueprint,
					},
				};
			});
		},

		selectEdge: (edgeId: string | null) => {
			set({ selectedEdgeId: edgeId, selectedNodeId: null });
		},

		updateConcept: (conceptId: string, updates: Partial<ConceptNode>) => {
			if (blockHistorical()) return;
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

					if (effectiveMappings.length === 0) {
						nextGroundingStatus = "UNGROUNDED";
					} else if (updates.grounding_status) {
						if (
							updates.grounding_status === "UNGROUNDED" ||
							updates.grounding_status === derived.grounding_status
						) {
							nextGroundingStatus = updates.grounding_status;
						}
					} else if (
						nextGroundingStatus !== "GROUNDED" &&
						n.grounding_status === "GROUNDED" &&
						hasPreservedCommittedUnscoredFiboPathAMapping(
							effectiveMappings,
							state.committedNodes.find((c) => c.concept_uri === n.concept_uri),
						)
					) {
						nextGroundingStatus = "GROUNDED";
					}

					return {
						...merged,
						confidence: nextConfidence,
						grounding_status: nextGroundingStatus,
						external_mappings: effectiveMappings,
					};
				});
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

		updateRelationship: (
			edgeId: string,
			updates: Partial<RelationshipEdge>,
		) => {
			if (blockHistorical()) return;
			set((state) => {
				let matched = false;
				const newEdges = state.edges.map((edge) => {
					if (edge.id !== edgeId && edge.relationship_uri !== edgeId) {
						return edge;
					}
					matched = true;
					const explicitEndpoints =
						Array.isArray(updates.endpoints) && updates.endpoints.length >= 2
							? updates.endpoints
							: undefined;
					const nextSrc =
						updates.source_concept_uri ??
						explicitEndpoints?.[0] ??
						edge.source_concept_uri;
					const nextTgt =
						updates.target_concept_uri ??
						explicitEndpoints?.[1] ??
						edge.target_concept_uri;
					const nextEndpoints = explicitEndpoints
						? [...explicitEndpoints]
						: edge.endpoints && edge.endpoints.length >= 2
							? [nextSrc, nextTgt, ...edge.endpoints.slice(2)]
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

		addConcept: (node: ConceptNode) => {
			if (blockHistorical()) return;
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

		addRelationship: (edge: RelationshipEdge) => {
			if (blockHistorical()) return;
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

		deleteConcept: (conceptUriOrId: string) => {
			if (blockHistorical()) return;
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

		deleteRelationship: (relationshipUriOrId: string) => {
			if (blockHistorical()) return;
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

		commitChanges: async (
			rationale?: string,
			maybeFetchImpl?: typeof fetch,
			commitOptions?: { allowDestructive?: boolean },
		) => {
			const fetchImpl = maybeFetchImpl ?? fetch;
			const state = get();
			if (state.isCommitting || !state.activeSession) return false;

			const preError = validateCommitPreconditions(state);
			if (preError) {
				state.addToast(preError);
				return false;
			}

			const allowDestructive = Boolean(
				commitOptions?.allowDestructive || state.userRole === "admin",
			);
			const committedNodes = state.committedNodes;
			const currentNodes = state.nodes;
			const committedEdges = state.committedEdges;
			const currentEdges = state.edges;

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

			const mutCheck = validateCommitAdditiveMutations({
				committedNodes,
				currentNodeMap,
				committedEdges,
				currentEdgeMap,
				allowDestructive,
			});
			if (mutCheck.error) {
				state.addToast(mutCheck.error);
				return false;
			}

			const groundingErr = validateCommitGroundingAndDag({
				state,
				currentNodes,
				currentEdges,
				committedNodeMap,
				removedEdges: mutCheck.removedEdges,
				modifiedCommittedEdges: mutCheck.modifiedCommittedEdges,
			});
			if (groundingErr) {
				state.addToast(groundingErr);
				return false;
			}

			const baseVersion = (state.activeSession.active_version || "").trim();
			const { operations, targetVersion } = buildDeltaOperationsAndVersion({
				baseVersion,
				rationale,
				committedNodes,
				currentNodes,
				committedEdges,
				currentEdges,
				committedNodeMap,
				committedEdgeMap,
				removedConcepts: mutCheck.removedConcepts,
				removedEdges: mutCheck.removedEdges,
				modifiedEdgeUris: mutCheck.modifiedEdgeUris,
				modifiedCommittedEdges: mutCheck.modifiedCommittedEdges,
			});

			if (operations.length === 0) {
				state.addToast({
					type: "urn:opendv:notice:no-changes",
					title: "No Changes to Commit",
					status: 200,
					detail: "There are no uncommitted changes in the workspace.",
					code: "NO_OPERATIONS",
				});
				return false;
			}

			return submitDeltaCommit({
				set,
				get,
				studioFetch,
				fetchImpl,
				activeSession: state.activeSession,
				baseVersion,
				targetVersion,
				operations,
				rationale,
				currentNodes,
				currentEdges,
				committedNodeMap,
			});
		},
	};
}
