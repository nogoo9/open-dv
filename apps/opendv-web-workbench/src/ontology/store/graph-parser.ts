/**
 * Graph payload parsing and baseline mutation comparison helpers.
 * (apps/opendv-web-workbench/src/ontology/store/graph-parser.ts)
 */

import {
	GRID_LAYOUT_COLS,
	GRID_LAYOUT_ORIGIN,
	GRID_LAYOUT_X_STRIDE,
	GRID_LAYOUT_Y_STRIDE,
} from "../constants";
import {
	deriveGroundingStatus,
	hasPreservedCommittedUnscoredFiboPathAMapping,
	isUnscoredNonUserAssertedFiboPathAMapping,
} from "./scorecard";
import type {
	ConceptAttribute,
	ConceptNode,
	EvidenceRef,
	ExternalMapping,
	RelationshipEdge,
} from "./types";

export function isConceptMutatedFromBaseline(
	node: ConceptNode,
	baseline: ConceptNode | undefined,
): boolean {
	if (!baseline) return false;
	return (
		node.concept_name !== baseline.concept_name ||
		node.elm_archetype !== baseline.elm_archetype ||
		(node.definition || "") !== (baseline.definition || "") ||
		(node.description || "") !== (baseline.description || "") ||
		(node.target_hub ?? null) !== (baseline.target_hub ?? null) ||
		(node.parent_concept_uri ?? null) !==
			(baseline.parent_concept_uri ?? null) ||
		(node.grounding_origin ?? null) !== (baseline.grounding_origin ?? null) ||
		(node.deprecated ?? null) !== (baseline.deprecated ?? null) ||
		JSON.stringify(node.attributes || []) !==
			JSON.stringify(baseline.attributes || []) ||
		JSON.stringify(node.synonyms || []) !==
			JSON.stringify(baseline.synonyms || []) ||
		JSON.stringify(node.external_mappings || []) !==
			JSON.stringify(baseline.external_mappings || []) ||
		JSON.stringify(node.metadata ?? null) !==
			JSON.stringify(baseline.metadata ?? null) ||
		JSON.stringify(node.source_hints ?? null) !==
			JSON.stringify(baseline.source_hints ?? null) ||
		JSON.stringify(node.governance_constraints ?? null) !==
			JSON.stringify(baseline.governance_constraints ?? null) ||
		JSON.stringify(node.key_authority_tiers ?? null) !==
			JSON.stringify(baseline.key_authority_tiers ?? null)
	);
}

export function isRelationshipMutatedFromBaseline(
	edge: RelationshipEdge,
	baseline: RelationshipEdge | undefined,
): boolean {
	if (!baseline) return true;
	const edgeEndpoints =
		edge.endpoints && edge.endpoints.length >= 2
			? edge.endpoints
			: [edge.source_concept_uri, edge.target_concept_uri];
	const baseEndpoints =
		baseline.endpoints && baseline.endpoints.length >= 2
			? baseline.endpoints
			: [baseline.source_concept_uri, baseline.target_concept_uri];
	return (
		(edge.id || edge.relationship_uri) !==
			(baseline.id || baseline.relationship_uri) ||
		edge.relationship_uri !== baseline.relationship_uri ||
		edge.relationship_name !== baseline.relationship_name ||
		edge.source_concept_uri !== baseline.source_concept_uri ||
		edge.target_concept_uri !== baseline.target_concept_uri ||
		JSON.stringify(edgeEndpoints) !== JSON.stringify(baseEndpoints) ||
		edge.target_link !== baseline.target_link ||
		(edge.grain_semantics ?? null) !== (baseline.grain_semantics ?? null) ||
		(edge.cardinality ?? null) !== (baseline.cardinality ?? null) ||
		(edge.description ?? null) !== (baseline.description ?? null) ||
		(edge.deprecated ?? null) !== (baseline.deprecated ?? null) ||
		JSON.stringify(edge.metadata ?? null) !==
			JSON.stringify(baseline.metadata ?? null) ||
		JSON.stringify(edge.source_hints ?? null) !==
			JSON.stringify(baseline.source_hints ?? null) ||
		JSON.stringify(edge.yagni_cost_guard_policy ?? null) !==
			JSON.stringify(baseline.yagni_cost_guard_policy ?? null)
	);
}

export function hasGraphChangesFromBaseline(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	committedNodes: ConceptNode[],
	committedEdges: RelationshipEdge[],
): boolean {
	if (
		nodes.length !== committedNodes.length ||
		edges.length !== committedEdges.length
	) {
		return true;
	}
	const committedNodeMap = new Map(
		committedNodes.map((n) => [n.concept_uri, n]),
	);
	for (const n of nodes) {
		const comm = committedNodeMap.get(n.concept_uri);
		if (!comm || isConceptMutatedFromBaseline(n, comm)) {
			return true;
		}
	}
	const sortedEdges = [...edges].sort((a, b) =>
		a.relationship_uri < b.relationship_uri
			? -1
			: a.relationship_uri > b.relationship_uri
				? 1
				: 0,
	);
	const sortedCommittedEdges = [...committedEdges].sort((a, b) =>
		a.relationship_uri < b.relationship_uri
			? -1
			: a.relationship_uri > b.relationship_uri
				? 1
				: 0,
	);
	for (const [i, currEdge] of sortedEdges.entries()) {
		const commEdge = sortedCommittedEdges[i];
		if (!commEdge || isRelationshipMutatedFromBaseline(currEdge, commEdge)) {
			return true;
		}
	}
	return false;
}

export function resolveCleanHeadSnapshot(state: {
	hasUnsavedChanges: boolean;
	committedNodes: ConceptNode[];
	committedEdges: RelationshipEdge[];
	nodes: ConceptNode[];
	edges: RelationshipEdge[];
}): { nodes: ConceptNode[]; edges: RelationshipEdge[] } {
	if (
		state.hasUnsavedChanges ||
		state.committedNodes.length > 0 ||
		state.committedEdges.length > 0
	) {
		return {
			nodes: state.committedNodes,
			edges: state.committedEdges,
		};
	}
	return { nodes: state.nodes, edges: state.edges };
}

export function parseLoadedGraphPayload(
	rawOnt: Record<string, unknown>,
	loadOptions?: {
		isCommittedSnapshot?: boolean;
		grandfatheredUris?: Set<string>;
		waivedUris?: Set<string>;
	},
): { loadedNodes: ConceptNode[]; loadedEdges: RelationshipEdge[] } {
	const conceptsList = (rawOnt.concepts ||
		rawOnt.canonical_business_concepts ||
		rawOnt.canonical_concepts ||
		[]) as Array<Record<string, unknown>>;
	const relationshipsList = (rawOnt.canonical_relationships ||
		rawOnt.relationships ||
		[]) as Array<Record<string, unknown>>;

	const loadedNodes: ConceptNode[] = conceptsList.map((c, idx) => {
		const mappings =
			(c.external_mappings as ExternalMapping[] | undefined) || [];
		const rawConf = typeof c.confidence === "number" ? c.confidence : undefined;
		const derived = deriveGroundingStatus(mappings, rawConf);
		const conceptUri = String(c.concept_uri || "");
		let status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED" =
			derived.grounding_status;
		if (mappings.length > 0 && typeof c.grounding_status === "string") {
			if (
				c.grounding_status === "UNGROUNDED" ||
				c.grounding_status === derived.grounding_status
			) {
				status = c.grounding_status as
					| "GROUNDED"
					| "LOW_CONFIDENCE"
					| "UNGROUNDED";
			}
		}
		if (
			loadOptions?.isCommittedSnapshot &&
			status !== "GROUNDED" &&
			c.grounding_status !== "UNGROUNDED" &&
			!loadOptions.grandfatheredUris?.has(conceptUri) &&
			!loadOptions.waivedUris?.has(conceptUri) &&
			mappings.some((m) => isUnscoredNonUserAssertedFiboPathAMapping(m))
		) {
			status = "GROUNDED";
		}
		const conf = rawConf !== undefined ? rawConf : derived.confidence;

		return {
			id: conceptUri,
			concept_uri: conceptUri,
			concept_name:
				(c.concept_name as string | undefined) ||
				conceptUri.split(":").pop()?.replace(/_/g, " ") ||
				"Concept",
			elm_archetype:
				(c.elm_archetype as string | undefined) || "CORE_BUSINESS_CONCEPT",
			definition: (c.definition as string | undefined) || "",
			description: (c.description as string | null | undefined) ?? null,
			target_hub: (c.target_hub as string | null | undefined) ?? null,
			parent_concept_uri:
				(c.parent_concept_uri as string | null | undefined) ?? null,
			confidence: conf,
			grounding_status: status,
			grounding_origin:
				(c.grounding_origin as string | null | undefined) ?? null,
			...(Array.isArray(c.evidence_refs)
				? { evidence_refs: c.evidence_refs as EvidenceRef[] }
				: {}),
			attributes: (c.attributes as ConceptAttribute[] | undefined) || [],
			synonyms: (c.synonyms as string[] | undefined) || [],
			external_mappings: mappings,
			deprecated: (c.deprecated as boolean | null | undefined) ?? null,
			metadata:
				(c.metadata as Record<string, unknown> | null | undefined) ?? null,
			source_hints: (c.source_hints as string[] | null | undefined) ?? null,
			governance_constraints:
				(c.governance_constraints as
					| Record<string, unknown>
					| null
					| undefined) ?? null,
			key_authority_tiers:
				(c.key_authority_tiers as Record<string, unknown> | null | undefined) ??
				null,
			x: GRID_LAYOUT_ORIGIN + (idx % GRID_LAYOUT_COLS) * GRID_LAYOUT_X_STRIDE,
			y:
				GRID_LAYOUT_ORIGIN +
				Math.floor(idx / GRID_LAYOUT_COLS) * GRID_LAYOUT_Y_STRIDE,
		};
	});

	const loadedEdges: RelationshipEdge[] = relationshipsList.map((r) => {
		const relUri = String(r.relationship_uri || "");
		const endpoints = Array.isArray(r.endpoints)
			? (r.endpoints as string[])
			: undefined;
		const src =
			(r.source_concept_uri as string | undefined) || endpoints?.[0] || "";
		const tgt =
			(r.target_concept_uri as string | undefined) || endpoints?.[1] || "";
		return {
			id: relUri,
			relationship_uri: relUri,
			relationship_name:
				(r.relationship_name as string | undefined) ||
				relUri.split(":").pop()?.replace(/_/g, " ") ||
				"relationship",
			target_link: (r.target_link as string | undefined) || "lnk_default",
			source_concept_uri: src,
			target_concept_uri: tgt,
			endpoints: endpoints ?? [src, tgt],
			grain_semantics: (r.grain_semantics as string | null | undefined) ?? null,
			cardinality: (r.cardinality as string | null | undefined) ?? null,
			description: (r.description as string | null | undefined) ?? null,
			deprecated: (r.deprecated as boolean | null | undefined) ?? null,
			metadata:
				(r.metadata as Record<string, unknown> | null | undefined) ?? null,
			source_hints: (r.source_hints as string[] | null | undefined) ?? null,
			yagni_cost_guard_policy:
				(r.yagni_cost_guard_policy as
					| Record<string, unknown>
					| null
					| undefined) ?? null,
		};
	});

	return { loadedNodes, loadedEdges };
}

export function cloneNodeUnverifiedFallback(
	n: ConceptNode,
	committedNodeMap: Map<string, ConceptNode>,
): ConceptNode {
	const sanitizedMappings: ExternalMapping[] = (n.external_mappings || []).map(
		(m) => {
			const isUserAsserted =
				(m.provenance || "").trim().toLowerCase() === "user_asserted";
			if (isUserAsserted) {
				const {
					confidence: _strippedConf,
					mapping_confidence: _strippedMapConf,
					...rest
				} = m;
				return { ...rest };
			}
			return { ...m };
		},
	);
	const derived = deriveGroundingStatus(sanitizedMappings);
	const preservedStatus =
		derived.grounding_status !== "GROUNDED" &&
		n.grounding_status === "GROUNDED" &&
		hasPreservedCommittedUnscoredFiboPathAMapping(
			sanitizedMappings,
			committedNodeMap.get(n.concept_uri),
		)
			? "GROUNDED"
			: derived.grounding_status;
	return {
		...n,
		confidence: derived.confidence,
		grounding_status: preservedStatus,
		attributes: (n.attributes || []).map((a) => ({ ...a })),
		synonyms: [...(n.synonyms || [])],
		external_mappings: sanitizedMappings,
	};
}

export function cloneEdgeClean(e: RelationshipEdge): RelationshipEdge {
	return {
		...e,
		...(e.endpoints ? { endpoints: [...e.endpoints] } : {}),
	};
}
