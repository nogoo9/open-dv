/**
 * Delta Operation Builder & SemVer Commit Execution
 * (apps/opendv-web-workbench/src/ontology/store/graph-commit-ops.ts)
 */

import { sanitizeEvidenceRefs } from "./evidence-grounding";
import { normalizeEdgeEndpoints } from "./graph-commit-validation";
import {
	cloneEdgeClean,
	cloneNodeUnverifiedFallback,
	parseLoadedGraphPayload,
} from "./graph-parser";
import { recomputeScorecard } from "./scorecard";
import type { StoreGet, StoreSet } from "./store-types";
import type { StudioFetcher } from "./studio-fetch";
import type {
	ConceptNode,
	ExternalMapping,
	RelationshipEdge,
	StudioSessionSummary,
} from "./types";

function sanitizeMappings(
	mappings: ExternalMapping[] | undefined,
): Array<Record<string, unknown>> | undefined {
	if (!mappings || mappings.length === 0) return undefined;
	return mappings.map((m) => {
		const numericConf =
			m.provenance === "user_asserted"
				? undefined
				: typeof m.mapping_confidence === "number" &&
						Number.isFinite(m.mapping_confidence)
					? m.mapping_confidence
					: typeof m.confidence === "number" && Number.isFinite(m.confidence)
						? m.confidence
						: undefined;
		return {
			scheme: m.scheme || "FIBO",
			target_uri: m.target_uri,
			...(m.predicate ? { predicate: m.predicate } : {}),
			...(m.pinned !== undefined ? { pinned: m.pinned } : {}),
			...(numericConf !== undefined ? { confidence: numericConf } : {}),
		};
	});
}

export function buildDeltaOperationsAndVersion(params: {
	baseVersion: string;
	rationale?: string | undefined;
	committedNodes: ConceptNode[];
	currentNodes: ConceptNode[];
	committedEdges: RelationshipEdge[];
	currentEdges: RelationshipEdge[];
	committedNodeMap: Map<string, ConceptNode>;
	committedEdgeMap: Map<string, RelationshipEdge>;
	removedConcepts: ConceptNode[];
	removedEdges: RelationshipEdge[];
	modifiedEdgeUris: Set<string>;
	modifiedCommittedEdges: RelationshipEdge[];
}): { operations: Array<Record<string, unknown>>; targetVersion: string } {
	const {
		baseVersion,
		rationale,
		committedNodes,
		currentNodes,
		currentEdges,
		committedNodeMap,
		committedEdgeMap,
		removedConcepts,
		removedEdges,
		modifiedEdgeUris,
		modifiedCommittedEdges,
	} = params;

	let opCounter = 0;
	const nextOpId = (): string => {
		opCounter += 1;
		return typeof crypto !== "undefined" &&
			typeof crypto.randomUUID === "function"
			? `op-${crypto.randomUUID()}`
			: `op-${Date.now()}-${opCounter}-${Math.random().toString(16).slice(2, 8)}`;
	};

	const operations: Array<Record<string, unknown>> = [];
	let hasMajorOp = false;
	let hasMinorOp = false;
	let hasAddConceptOp = false;

	for (const removedEdge of [...removedEdges, ...modifiedCommittedEdges]) {
		hasMajorOp = true;
		operations.push({
			op_id: nextOpId(),
			op_type: "remove_relationship",
			relationship_uri: removedEdge.relationship_uri,
			reason: rationale || "Removed relationship in Workbench Studio",
		});
	}

	for (const removedConcept of removedConcepts) {
		hasMajorOp = true;
		operations.push({
			op_id: nextOpId(),
			op_type: "remove_concept",
			concept_uri: removedConcept.concept_uri,
			reason: rationale || "Removed concept in Workbench Studio",
		});
	}

	for (const node of currentNodes) {
		const committed = committedNodeMap.get(node.concept_uri);
		if (!committed) {
			hasMinorOp = true;
			hasAddConceptOp = true;
			const conceptRefs = sanitizeEvidenceRefs(node.evidence_refs);
			operations.push({
				op_id: nextOpId(),
				op_type: "add_concept",
				concept_uri: node.concept_uri,
				concept_name: node.concept_name,
				elm_archetype: node.elm_archetype || "CORE_BUSINESS_CONCEPT",
				definition: node.definition || "",
				...(node.description ? { description: node.description } : {}),
				target_hub: node.target_hub ?? undefined,
				parent_concept_uri: node.parent_concept_uri ?? undefined,
				...(node.grounding_origin
					? { grounding_origin: node.grounding_origin }
					: {}),
				...(conceptRefs ? { evidence_refs: conceptRefs } : {}),
				attributes: [],
				synonyms:
					node.synonyms && node.synonyms.length > 0 ? node.synonyms : undefined,
				external_mappings: sanitizeMappings(node.external_mappings),
			});
			for (const attr of node.attributes || []) {
				hasMinorOp = true;
				const attrRefs = sanitizeEvidenceRefs(attr.evidence_refs);
				operations.push({
					op_id: nextOpId(),
					op_type: "add_attribute",
					concept_uri: node.concept_uri,
					attribute_name: attr.attribute_name,
					data_type: attr.data_type || "string",
					description: attr.description || undefined,
					is_optional: attr.is_optional ?? undefined,
					...(attrRefs ? { evidence_refs: attrRefs } : {}),
					example_values:
						attr.example_values && attr.example_values.length > 0
							? attr.example_values
							: undefined,
				});
			}
			continue;
		}

		if (node.elm_archetype !== committed.elm_archetype) {
			if ((committed.elm_archetype || "").trim()) hasMajorOp = true;
			else hasMinorOp = true;
			operations.push({
				op_id: nextOpId(),
				op_type: "set_concept_elm_archetype",
				concept_uri: node.concept_uri,
				elm_archetype: node.elm_archetype,
				rationale: rationale || "Updated ELM archetype in Workbench Studio",
			});
		}
		if (
			(node.parent_concept_uri ?? null) !==
			(committed.parent_concept_uri ?? null)
		) {
			if ((committed.parent_concept_uri || "").trim()) hasMajorOp = true;
			else hasMinorOp = true;
			operations.push({
				op_id: nextOpId(),
				op_type: "set_parent_concept",
				concept_uri: node.concept_uri,
				parent_concept_uri: node.parent_concept_uri ?? null,
			});
		}
		if ((node.description || "") !== (committed.description || "")) {
			operations.push({
				op_id: nextOpId(),
				op_type: "revise_description",
				concept_uri: node.concept_uri,
				description: node.description || "",
			});
		}
		if ((node.definition || "") !== (committed.definition || "")) {
			operations.push({
				op_id: nextOpId(),
				op_type: "refine_definition",
				concept_uri: node.concept_uri,
				definition: node.definition || "",
			});
		}
		const committedSyns = new Set(committed.synonyms || []);
		for (const syn of node.synonyms || []) {
			if (!committedSyns.has(syn)) {
				operations.push({
					op_id: nextOpId(),
					op_type: "add_synonym",
					concept_uri: node.concept_uri,
					synonym: syn,
				});
			}
		}
		const currentAttrNames = new Set(
			(node.attributes || []).map((a) => a.attribute_name),
		);
		for (const commAttr of committed.attributes || []) {
			if (!currentAttrNames.has(commAttr.attribute_name)) {
				hasMajorOp = true;
				operations.push({
					op_id: nextOpId(),
					op_type: "remove_attribute",
					concept_uri: node.concept_uri,
					attribute_name: commAttr.attribute_name,
					reason: rationale || "Removed attribute in Workbench Studio",
				});
			}
		}
		const committedAttrs = new Set(
			(committed.attributes || []).map((a) => a.attribute_name),
		);
		for (const attr of node.attributes || []) {
			if (!committedAttrs.has(attr.attribute_name)) {
				if (attr.is_optional === false) hasMajorOp = true;
				else hasMinorOp = true;
				const attrRefs = sanitizeEvidenceRefs(attr.evidence_refs);
				operations.push({
					op_id: nextOpId(),
					op_type: "add_attribute",
					concept_uri: node.concept_uri,
					attribute_name: attr.attribute_name,
					data_type: attr.data_type || "string",
					description: attr.description || undefined,
					is_optional: attr.is_optional ?? undefined,
					...(attrRefs ? { evidence_refs: attrRefs } : {}),
					example_values:
						attr.example_values && attr.example_values.length > 0
							? attr.example_values
							: undefined,
				});
			}
		}
		const currentMappingMap = new Map(
			(node.external_mappings || []).map((m) => [m.target_uri, m]),
		);
		for (const commMapping of committed.external_mappings || []) {
			const currM = currentMappingMap.get(commMapping.target_uri);
			if (!currM) {
				operations.push({
					op_id: nextOpId(),
					op_type: "remove_external_mapping",
					concept_uri: node.concept_uri,
					scheme: commMapping.scheme || "FIBO",
					target_uri: commMapping.target_uri,
				});
				continue;
			}
			if (Boolean(currM.pinned) !== Boolean(commMapping.pinned)) {
				operations.push({
					op_id: nextOpId(),
					op_type: "repin_external_mapping",
					concept_uri: node.concept_uri,
					scheme: currM.scheme || "FIBO",
					target_uri: currM.target_uri,
					pinned: Boolean(currM.pinned),
				});
			}
			const prevConf =
				commMapping.mapping_confidence ?? commMapping.confidence ?? null;
			const nextConf = currM.mapping_confidence ?? currM.confidence ?? null;
			if (
				nextConf !== prevConf &&
				typeof nextConf === "number" &&
				Number.isFinite(nextConf)
			) {
				operations.push({
					op_id: nextOpId(),
					op_type: "rescore_external_mapping",
					concept_uri: node.concept_uri,
					scheme: currM.scheme || "FIBO",
					target_uri: currM.target_uri,
					new_confidence: nextConf,
					...(typeof prevConf === "number" && Number.isFinite(prevConf)
						? { previous_confidence: prevConf }
						: {}),
				});
			}
		}
		const committedMappings = new Set(
			(committed.external_mappings || []).map((m) => m.target_uri),
		);
		for (const mapping of node.external_mappings || []) {
			if (!committedMappings.has(mapping.target_uri)) {
				const numericConf =
					mapping.provenance === "user_asserted"
						? undefined
						: typeof mapping.mapping_confidence === "number" &&
								Number.isFinite(mapping.mapping_confidence)
							? mapping.mapping_confidence
							: typeof mapping.confidence === "number" &&
									Number.isFinite(mapping.confidence)
								? mapping.confidence
								: undefined;
				operations.push({
					op_id: nextOpId(),
					op_type: "add_external_mapping",
					concept_uri: node.concept_uri,
					scheme: mapping.scheme || "FIBO",
					target_uri: mapping.target_uri,
					predicate: mapping.predicate || undefined,
					pinned: mapping.pinned ?? undefined,
					...(numericConf !== undefined
						? { mapping_confidence: numericConf, confidence: numericConf }
						: {}),
				});
			}
		}
	}

	for (const edge of currentEdges) {
		const committed = committedEdgeMap.get(edge.relationship_uri);
		if (!committed || modifiedEdgeUris.has(edge.relationship_uri)) {
			hasMinorOp = true;
			const relRefs = sanitizeEvidenceRefs(edge.evidence_refs);
			operations.push({
				op_id: nextOpId(),
				op_type: "add_relationship",
				relationship_uri: edge.relationship_uri,
				relationship_name: edge.relationship_name,
				target_link: edge.target_link || "lnk_default",
				endpoints: [...normalizeEdgeEndpoints(edge)],
				...(edge.grain_semantics
					? { grain_semantics: edge.grain_semantics }
					: {}),
				cardinality: edge.cardinality ?? undefined,
				description: edge.description ?? undefined,
				...(relRefs ? { evidence_refs: relRefs } : {}),
			});
		}
	}

	const semverMatch = /^(\d+)\.(\d+)\.(\d+)/.exec(baseVersion);
	const major = Number.parseInt(semverMatch?.[1] ?? "0", 10);
	const minor = Number.parseInt(semverMatch?.[2] ?? "0", 10);
	const patch = Number.parseInt(semverMatch?.[3] ?? "0", 10);

	let targetVersion: string;
	if (major === 0) {
		if (committedNodes.length === 0) {
			targetVersion = hasAddConceptOp ? "1.0.0" : `0.${minor + 1}.0`;
		} else {
			targetVersion =
				hasMajorOp || hasMinorOp
					? `0.${minor + 1}.0`
					: `0.${minor}.${patch + 1}`;
		}
	} else if (hasMajorOp) {
		targetVersion = `${major + 1}.0.0`;
	} else if (hasMinorOp) {
		targetVersion = `${major}.${minor + 1}.0`;
	} else {
		targetVersion = `${major}.${minor}.${patch + 1}`;
	}

	return { operations, targetVersion };
}

export async function submitDeltaCommit(params: {
	set: StoreSet;
	get: StoreGet;
	studioFetch: StudioFetcher;
	fetchImpl: typeof fetch;
	activeSession: StudioSessionSummary;
	baseVersion: string;
	targetVersion: string;
	operations: Array<Record<string, unknown>>;
	rationale?: string | undefined;
	currentNodes: ConceptNode[];
	currentEdges: RelationshipEdge[];
	committedNodeMap: Map<string, ConceptNode>;
}): Promise<boolean> {
	const {
		set,
		get,
		studioFetch,
		fetchImpl,
		activeSession,
		baseVersion,
		targetVersion,
		operations,
		rationale,
		currentNodes,
		currentEdges,
		committedNodeMap,
	} = params;
	const sid = activeSession.session_id;
	const deltaId =
		typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
			? `delta-${crypto.randomUUID()}`
			: `delta-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

	const payload = {
		delta_id: deltaId,
		parent_sha256: activeSession.head_sha256,
		base_version: baseVersion,
		target_version: targetVersion,
		operations,
		author: activeSession.enterprise_owner || undefined,
		rationale: rationale || "Workbench Studio visual edits",
	};

	const sentNodes = [...currentNodes];
	const sentEdges = [...currentEdges];

	set({ isCommitting: true });
	try {
		const res = await studioFetch(
			fetchImpl,
			`/api/v1/ontology/studio/sessions/${encodeURIComponent(sid)}/delta`,
			{
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					"If-Match": `"${activeSession.head_sha256}"`,
				},
				body: JSON.stringify(payload),
			},
		);

		if (!res.ok) {
			const err = (await res.json().catch(() => ({}))) as {
				detail?: string;
				code?: string;
			};
			if (get().activeSession?.session_id !== sid) return false;
			get().addToast({
				type: "urn:opendv:error:delta-commit-failed",
				title: "Commit Failed",
				status: res.status,
				detail: err.detail || `Server returned HTTP ${res.status}`,
				code: err.code || "CONCURRENT_MODIFICATION",
			});
			return false;
		}

		const data = (await res.json()) as {
			head_sha256?: string;
			target_sha256?: string;
			active_version?: string;
			target_version?: string;
			ontology?: Record<string, unknown>;
		};
		if (get().activeSession?.session_id !== sid) {
			set((state) => ({
				sessions: state.sessions.map((s) =>
					s.session_id === sid
						? {
								...s,
								head_sha256:
									data.target_sha256 || data.head_sha256 || s.head_sha256,
								active_version:
									data.target_version ||
									data.active_version ||
									s.active_version,
							}
						: s,
				),
			}));
			return false;
		}

		const nextVersion =
			data.target_version ||
			data.active_version ||
			get().activeSession?.active_version ||
			targetVersion;
		const nextSha =
			data.target_sha256 ||
			data.head_sha256 ||
			get().activeSession?.head_sha256;

		let shouldReloadVerifiedSnapshot = false;

		set((state) => {
			const hasFurtherEdits =
				state.nodes.length !== sentNodes.length ||
				state.edges.length !== sentEdges.length ||
				state.nodes.some((n, i) => n !== sentNodes[i]) ||
				state.edges.some((e, i) => e !== sentEdges[i]);

			let ratifiedNodes = sentNodes.map((n) =>
				cloneNodeUnverifiedFallback(n, committedNodeMap),
			);
			let ratifiedEdges = sentEdges.map(cloneEdgeClean);
			if (data.ontology && typeof data.ontology === "object") {
				const parsedOnt = parseLoadedGraphPayload(data.ontology, {
					isCommittedSnapshot: nextVersion.replace(/^v/i, "") !== "0.0.0",
					grandfatheredUris: new Set(
						state.activeSession?.grandfathered_concept_uris || [],
					),
					waivedUris: new Set(
						state.sessionWaivers[sid]?.waived_concept_uris || [],
					),
				});
				ratifiedNodes = parsedOnt.loadedNodes;
				ratifiedEdges = parsedOnt.loadedEdges;
			} else if (!hasFurtherEdits) {
				shouldReloadVerifiedSnapshot = true;
			}

			const nextWorkingNodes = hasFurtherEdits ? state.nodes : ratifiedNodes;
			const nextWorkingEdges = hasFurtherEdits ? state.edges : ratifiedEdges;
			const nextSession = state.activeSession
				? {
						...state.activeSession,
						...(state.activeSession.status === "EMPTY"
							? { status: "COMMITTED" }
							: {}),
						head_sha256: nextSha || state.activeSession.head_sha256,
						active_version: nextVersion,
					}
				: null;

			const nextSessions = state.sessions.map((s) =>
				s.session_id === sid && nextSession ? nextSession : s,
			);
			return {
				nodes: nextWorkingNodes,
				edges: nextWorkingEdges,
				hasUnsavedChanges: hasFurtherEdits,
				committedNodes: ratifiedNodes,
				committedEdges: ratifiedEdges,
				activeSession: nextSession,
				sessions: nextSessions,
				viewedVersion: nextSession?.active_version || state.viewedVersion,
				isHistoricalView: false,
				qualityScorecard: recomputeScorecard(
					{
						...state,
						activeSession: nextSession,
						committedNodes: ratifiedNodes,
					},
					nextWorkingNodes,
					nextWorkingEdges,
				),
			};
		});

		if (
			shouldReloadVerifiedSnapshot &&
			get().activeSession?.session_id === sid &&
			!get().hasUnsavedChanges
		) {
			const fallbackCommittedRef = get().committedNodes;
			const toastIdsBeforeReload = new Set(get().toasts.map((t) => t.id));
			await get().loadGraph(nextVersion, fetchImpl, sid);
			const hydrated = get().committedNodes !== fallbackCommittedRef;
			if (!hydrated && get().activeSession?.session_id === sid) {
				const hasReloadFailureToast = get().toasts.some(
					(t) => !toastIdsBeforeReload.has(t.id) && t.status >= 400,
				);
				if (!hasReloadFailureToast) {
					get().addToast({
						type: "urn:opendv:warning:post-commit-reload-failed",
						title: "Post-Commit Snapshot Reload Failed",
						status: 409,
						detail: `Delta committed as v${nextVersion}, but reloading the server-verified ontology snapshot failed.`,
						code: "GRAPH_LOAD_FAILED",
					});
				}
			}
		}

		get().addToast({
			type: "urn:opendv:notice:commit-success",
			title: "Changes Committed",
			status: 200,
			detail: "Ontology delta committed successfully.",
			code: "COMMITTED",
		});
		return true;
	} catch (err) {
		if (get().activeSession?.session_id === sid) {
			get().addToast({
				type: "urn:opendv:error:network",
				title: "Network Error",
				status: 503,
				detail: String(err),
				code: "NETWORK_ERROR",
			});
		}
		return false;
	} finally {
		set({ isCommitting: false });
	}
}
