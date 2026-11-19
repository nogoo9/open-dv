/**
 * Ontology Graph Structural Visual Diff Calculation & Version Loading Slice
 * (apps/opendv-web-workbench/src/ontology/store/graph-diff.ts)
 */

import {
	parseLoadedGraphPayload,
	resolveCleanHeadSnapshot,
} from "./graph-parser";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	ConceptNode,
	DiffRecord,
	DiffState,
	RelationshipEdge,
} from "./types";

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

function diffConceptPair(
	uri: string,
	baseNode: ConceptNode,
	node: ConceptNode,
	targetVersion: string,
	records: DiffRecord[],
): void {
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
	const tightenedOptionalityAttrs = (node.attributes || []).filter((a) => {
		const b = baseAttrMap.get(a.attribute_name);
		return b && Boolean(b.is_optional) && !a.is_optional;
	});
	const widenedOptionalityAttrs = (node.attributes || []).filter((a) => {
		const b = baseAttrMap.get(a.attribute_name);
		return b && !b.is_optional && Boolean(a.is_optional);
	});
	const modifiedDescAttrs = (node.attributes || []).filter((a) => {
		const b = baseAttrMap.get(a.attribute_name);
		return b && (b.description || "") !== (a.description || "");
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

	if (tightenedOptionalityAttrs.length > 0) {
		records.push({
			item_type: "attribute",
			change_type: "modified",
			identifier: `${uri}#attribute-optionality`,
			label: `${node.concept_name} attribute optionality`,
			details: `Attribute made mandatory: ${tightenedOptionalityAttrs.map((a) => `${a.attribute_name} (optional -> mandatory)`).join(", ")} (breaking change under ONT-EVO-001)`,
			is_breaking: true,
		});
	}

	const baseMappingMap = new Map(
		(baseNode.external_mappings || []).map((m) => [m.target_uri, m]),
	);
	const targetMappingMap = new Map(
		(node.external_mappings || []).map((m) => [m.target_uri, m]),
	);
	const mappingAdded = [...targetMappingMap.keys()].filter(
		(u) => !baseMappingMap.has(u),
	);
	const mappingRemoved = [...baseMappingMap.keys()].filter(
		(u) => !targetMappingMap.has(u),
	);
	const mappingModified = [...targetMappingMap.entries()].filter(([u, m]) => {
		const b = baseMappingMap.get(u);
		if (!b) return false;
		return (
			(b.scheme || "") !== (m.scheme || "") ||
			(b.predicate || "") !== (m.predicate || "") ||
			(b.confidence ?? null) !== (m.confidence ?? null)
		);
	});
	const mappingsChanged =
		mappingAdded.length > 0 ||
		mappingRemoved.length > 0 ||
		mappingModified.length > 0;

	const nameChanged = node.concept_name !== baseNode.concept_name;
	const archetypeChanged = node.elm_archetype !== baseNode.elm_archetype;
	const synsChanged =
		JSON.stringify(node.synonyms?.slice().sort() || []) !==
		JSON.stringify(baseNode.synonyms?.slice().sort() || []);
	const defChanged = (node.definition || "") !== (baseNode.definition || "");
	const descChanged = (node.description || "") !== (baseNode.description || "");
	const parentChanged =
		(node.parent_concept_uri ?? null) !== (baseNode.parent_concept_uri ?? null);
	const targetHubChanged =
		(node.target_hub ?? null) !== (baseNode.target_hub ?? null);
	const attrMetaChanged =
		widenedOptionalityAttrs.length > 0 || modifiedDescAttrs.length > 0;

	if (
		nameChanged ||
		archetypeChanged ||
		synsChanged ||
		addedAttrs.length > 0 ||
		attrMetaChanged ||
		mappingsChanged ||
		defChanged ||
		descChanged ||
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
		if (descChanged) detailsParts.push("description updated");
		if (addedAttrs.length > 0) detailsParts.push(`+${addedAttrs.length} attrs`);
		if (widenedOptionalityAttrs.length > 0)
			detailsParts.push(
				`attrs made optional: ${widenedOptionalityAttrs.map((a) => a.attribute_name).join(", ")}`,
			);
		if (modifiedDescAttrs.length > 0)
			detailsParts.push(
				`attr descriptions updated: ${modifiedDescAttrs.map((a) => a.attribute_name).join(", ")}`,
			);
		if (mappingsChanged)
			detailsParts.push(
				`mappings changed (+${mappingAdded.length}, -${mappingRemoved.length}${mappingModified.length > 0 ? `, ~${mappingModified.length}` : ""})`,
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

export function createGraphDiffSlice(
	set: StoreSet,
	get: StoreGet,
	studioFetch: StudioFetcher,
) {
	return {
		diffState: createDefaultDiffState(),

		computeDiff: (
			baseVersion: string,
			targetVersion: string,
			baseGraph?: { nodes: ConceptNode[]; edges: RelationshipEdge[] },
			targetGraph?: { nodes: ConceptNode[]; edges: RelationshipEdge[] },
		) => {
			if (!baseGraph && !targetGraph && baseVersion === targetVersion) {
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

			const resolveDiffFallbackSnapshot = (
				ver: string,
				role: "base" | "target",
			): { nodes: ConceptNode[]; edges: RelationshipEdge[] } => {
				if (ver === "0.0.0") return { nodes: [], edges: [] };
				const state = get();
				if (ver === "draft") {
					return { nodes: state.nodes, edges: state.edges };
				}
				const headVersion = state.activeSession?.active_version;
				if (!state.isHistoricalView && headVersion && ver === headVersion) {
					return resolveCleanHeadSnapshot(state);
				}
				if (state.isHistoricalView) {
					if (
						state.viewedVersion &&
						ver === state.viewedVersion &&
						ver !== headVersion
					) {
						return { nodes: state.nodes, edges: state.edges };
					}
					return { nodes: state.committedNodes, edges: state.committedEdges };
				}
				if (role === "base") {
					return { nodes: state.committedNodes, edges: state.committedEdges };
				}
				return { nodes: state.nodes, edges: state.edges };
			};

			const fallbackBase =
				baseGraph ?? resolveDiffFallbackSnapshot(baseVersion, "base");
			const fallbackTarget =
				targetGraph ?? resolveDiffFallbackSnapshot(targetVersion, "target");
			const baseNodes = fallbackBase.nodes;
			const baseEdges = fallbackBase.edges;
			const currentNodes = fallbackTarget.nodes;
			const currentEdges = fallbackTarget.edges;
			const records: DiffRecord[] = [];

			const baseNodeMap = new Map(baseNodes.map((n) => [n.concept_uri, n]));
			const targetNodeMap = new Map(
				currentNodes.map((n) => [n.concept_uri, n]),
			);

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
					const baseNode = baseNodeMap.get(uri);
					if (baseNode) {
						diffConceptPair(uri, baseNode, node, targetVersion, records);
					}
				}
			}

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
					const baseEdge = baseEdgeMap.get(uri);
					if (!baseEdge) continue;
					const baseEps =
						baseEdge.endpoints && baseEdge.endpoints.length >= 2
							? baseEdge.endpoints
							: [baseEdge.source_concept_uri, baseEdge.target_concept_uri];
					const targetEps =
						edge.endpoints && edge.endpoints.length >= 2
							? edge.endpoints
							: [edge.source_concept_uri, edge.target_concept_uri];
					const epsChanged =
						baseEps.length !== targetEps.length ||
						baseEps.some((ep, idx) => ep !== targetEps[idx]);
					const linkChanged = edge.target_link !== baseEdge.target_link;
					const cardChanged =
						(edge.cardinality ?? null) !== (baseEdge.cardinality ?? null);
					const srcChanged =
						edge.source_concept_uri !== baseEdge.source_concept_uri;
					const tgtChanged =
						edge.target_concept_uri !== baseEdge.target_concept_uri;
					const relNameChanged =
						(edge.relationship_name ?? "") !==
						(baseEdge.relationship_name ?? "");
					const grainChanged =
						(edge.grain_semantics ?? "") !== (baseEdge.grain_semantics ?? "");
					const relDescChanged =
						(edge.description ?? "") !== (baseEdge.description ?? "");
					if (
						linkChanged ||
						cardChanged ||
						srcChanged ||
						tgtChanged ||
						epsChanged ||
						relNameChanged ||
						grainChanged ||
						relDescChanged
					) {
						const relDetails: string[] = [];
						if (relNameChanged) {
							relDetails.push(
								`name: '${baseEdge.relationship_name}' -> '${edge.relationship_name}'`,
							);
						}
						if (linkChanged) {
							relDetails.push(
								`target_link: ${baseEdge.target_link} -> ${edge.target_link}`,
							);
						}
						if (cardChanged) {
							relDetails.push(
								`cardinality: ${baseEdge.cardinality} -> ${edge.cardinality}`,
							);
						}
						if (grainChanged) relDetails.push("grain_semantics updated");
						if (relDescChanged) relDetails.push("description updated");
						if (srcChanged || tgtChanged || epsChanged) {
							relDetails.push(
								`endpoints: [${baseEps.join(", ")}] -> [${targetEps.join(", ")}]`,
							);
						}
						records.push({
							item_type: "relationship",
							change_type: "modified",
							identifier: uri,
							label: edge.relationship_name,
							details: `Modified relationship properties (${relDetails.join("; ")})`,
							is_breaking: srcChanged || tgtChanged || epsChanged,
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

			set({
				diffState: {
					active: true,
					mode: "unified",
					base_version: baseVersion,
					target_version: targetVersion,
					records,
					summary: {
						added_count: records.filter((r) => r.change_type === "added")
							.length,
						deleted_count: records.filter((r) => r.change_type === "deleted")
							.length,
						modified_count: records.filter((r) => r.change_type === "modified")
							.length,
						breaking_count: records.filter((r) => r.is_breaking).length,
					},
				},
			});
		},

		loadAndComputeDiff: async (
			baseVersion: string,
			targetVersion: string,
			fetchImpl = fetch,
		) => {
			if (baseVersion === targetVersion) {
				get().computeDiff(
					baseVersion,
					targetVersion,
					{ nodes: [], edges: [] },
					{ nodes: [], edges: [] },
				);
				return;
			}
			const activeSession = get().activeSession;
			if (!activeSession) {
				get().computeDiff(baseVersion, targetVersion);
				return;
			}
			const sid = activeSession.session_id;

			const fetchSnapshot = async (
				ver: string,
			): Promise<{
				nodes: ConceptNode[];
				edges: RelationshipEdge[];
			} | null> => {
				if (ver === "0.0.0") return { nodes: [], edges: [] };
				if (ver === "draft") return { nodes: get().nodes, edges: get().edges };
				if (!get().isHistoricalView && ver === activeSession.active_version) {
					return resolveCleanHeadSnapshot(get());
				}
				const isHeadInHistoricalMode =
					get().isHistoricalView && ver === activeSession.active_version;
				const isViewedInHistoricalMode =
					get().isHistoricalView &&
					Boolean(get().viewedVersion) &&
					ver === get().viewedVersion &&
					ver !== activeSession.active_version;
				const resolveOfflineHistoricalFallback = (): {
					nodes: ConceptNode[];
					edges: RelationshipEdge[];
				} | null => {
					if (isHeadInHistoricalMode) {
						return { nodes: get().committedNodes, edges: get().committedEdges };
					}
					if (isViewedInHistoricalMode) {
						return { nodes: get().nodes, edges: get().edges };
					}
					return null;
				};
				try {
					const res = await studioFetch(
						fetchImpl,
						`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/export?export_format=json&version=${encodeURIComponent(ver)}`,
					);
					if (!res.ok) {
						const fallback = resolveOfflineHistoricalFallback();
						if (fallback) return fallback;
						const err = (await res.json().catch(() => ({}))) as {
							detail?: string;
							code?: string;
						};
						get().addToast({
							type: "urn:opendv:error:diff-load-failed",
							title: "Failed to Load Version for Diff",
							status: res.status,
							detail:
								err.detail ||
								`Failed to load ontology version ${ver} (HTTP ${res.status})`,
							code: err.code || "DIFF_LOAD_FAILED",
						});
						return null;
					}
					const ont = (await res.json()) as Record<string, unknown>;
					const hasOntologyPayload =
						ont !== null &&
						typeof ont === "object" &&
						!Array.isArray(ont) &&
						(Array.isArray(ont.canonical_business_concepts) ||
							Array.isArray(ont.concepts) ||
							Array.isArray(ont.canonical_concepts));
					if (!hasOntologyPayload) {
						const fallback = resolveOfflineHistoricalFallback();
						if (fallback) return fallback;
					}
					const parsed = parseLoadedGraphPayload(ont, {
						isCommittedSnapshot: ver.replace(/^v/i, "") !== "0.0.0",
						grandfatheredUris: new Set(
							get().activeSession?.grandfathered_concept_uris || [],
						),
						waivedUris: new Set(
							get().sessionWaivers[sid]?.waived_concept_uris || [],
						),
					});
					return { nodes: parsed.loadedNodes, edges: parsed.loadedEdges };
				} catch (err) {
					const fallback = resolveOfflineHistoricalFallback();
					if (fallback) return fallback;
					get().addToast({
						type: "urn:opendv:error:network",
						title: "Diff Load Network Error",
						status: 503,
						detail: String(err),
						code: "NETWORK_ERROR",
					});
					return null;
				}
			};

			const [baseSnap, targetSnap] = await Promise.all([
				fetchSnapshot(baseVersion),
				fetchSnapshot(targetVersion),
			]);
			if (!baseSnap || !targetSnap) return;
			if (get().activeSession?.session_id !== sid) return;
			get().computeDiff(baseVersion, targetVersion, baseSnap, targetSnap);
		},

		setDiffMode: (mode: "unified" | "side-by-side" | "off") => {
			set((state) => ({
				diffState: { ...state.diffState, mode, active: mode !== "off" },
			}));
		},
	};
}
