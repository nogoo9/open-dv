/**
 * Pure ontology quality scorecard, grounding status, and cycle detection helpers.
 * (apps/opendv-web-workbench/src/ontology/store/scorecard.ts)
 */

import { GROUNDING_MIN_CONFIDENCE } from "../constants";
import { hasVerifiableEmpiricalEvidence } from "./evidence-grounding";
import { isConceptMutatedFromBaseline } from "./graph-parser";
import type {
	ConceptNode,
	ExternalMapping,
	QualityScorecard,
	QualityScorecardOptions,
	RelationshipEdge,
	StudioSessionSummary,
} from "./types";

export const EMPTY_ANSWERS_SHA256 =
	"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945";

export const VALID_PATH_A_PREDICATES = new Set([
	"skos:exactMatch",
	"skos:closeMatch",
	"http://www.w3.org/2004/02/skos/core#exactMatch",
	"http://www.w3.org/2004/02/skos/core#closeMatch",
]);

export function createDefaultQualityScorecard(): QualityScorecard {
	return {
		grounding_score: 0,
		deployed_contracts_ok: false,
		dag_acyclic: false,
		yagni_compliant: false,
		checks: [],
		waiver_applied: false,
		waiver_rationale: "",
		waived_concept_uris: [],
		feedback_rating: 0,
		feedback_comment: "",
		feedback_submitted: false,
	};
}

export function deriveGroundingStatus(
	mappings?: ExternalMapping[] | null,
	explicitConfidence?: number | null,
): {
	grounding_status: "GROUNDED" | "LOW_CONFIDENCE" | "UNGROUNDED";
	confidence: number;
} {
	const list = mappings || [];
	if (list.length === 0) {
		return {
			grounding_status: "UNGROUNDED",
			confidence:
				typeof explicitConfidence === "number" &&
				Number.isFinite(explicitConfidence)
					? explicitConfidence
					: 0.0,
		};
	}

	const verifiedMappings = list.filter(
		(m) => (m.provenance || "").trim().toLowerCase() !== "user_asserted",
	);

	const qualifyingFiboConfs: number[] = [];
	const allVerifiedConfs: number[] = [];

	for (const m of verifiedMappings) {
		const rawConf =
			typeof m.confidence === "number"
				? m.confidence
				: typeof m.mapping_confidence === "number"
					? m.mapping_confidence
					: undefined;
		if (typeof rawConf === "number" && Number.isFinite(rawConf)) {
			allVerifiedConfs.push(rawConf);
			const isFibo = (m.scheme || "").trim().toLowerCase() === "fibo";
			const hasAllowedPredicate =
				typeof m.predicate === "string" &&
				VALID_PATH_A_PREDICATES.has(m.predicate.trim());
			if (isFibo && hasAllowedPredicate) {
				qualifyingFiboConfs.push(rawConf);
			}
		}
	}

	if (qualifyingFiboConfs.length > 0) {
		const maxQualifying = Math.max(...qualifyingFiboConfs);
		if (maxQualifying >= GROUNDING_MIN_CONFIDENCE) {
			return { grounding_status: "GROUNDED", confidence: maxQualifying };
		}
		if (maxQualifying >= 0.4) {
			return { grounding_status: "LOW_CONFIDENCE", confidence: maxQualifying };
		}
		return { grounding_status: "UNGROUNDED", confidence: maxQualifying };
	}

	const fallbackConf =
		allVerifiedConfs.length > 0 ? Math.max(...allVerifiedConfs) : 0.0;

	return {
		grounding_status: fallbackConf >= 0.4 ? "LOW_CONFIDENCE" : "UNGROUNDED",
		confidence: fallbackConf,
	};
}

export function isUnscoredNonUserAssertedFiboPathAMapping(
	m: ExternalMapping | null | undefined,
): boolean {
	if (!m) return false;
	if ((m.provenance || "").trim().toLowerCase() === "user_asserted") {
		return false;
	}
	const isFibo = (m.scheme || "").trim().toLowerCase() === "fibo";
	const hasUri =
		typeof m.target_uri === "string" && m.target_uri.trim().length > 0;
	const hasPathAPredicate =
		typeof m.predicate === "string" &&
		VALID_PATH_A_PREDICATES.has(m.predicate.trim());
	return (
		isFibo &&
		hasUri &&
		hasPathAPredicate &&
		m.confidence === undefined &&
		m.mapping_confidence === undefined
	);
}

export function hasPreservedCommittedUnscoredFiboPathAMapping(
	mappings: ExternalMapping[] | undefined | null,
	committedConcept?: ConceptNode | null,
): boolean {
	const list = mappings || [];
	if (list.length === 0) return false;
	if (!committedConcept || committedConcept.grounding_status !== "GROUNDED") {
		return false;
	}
	const committedUnscoredUris = new Set(
		(committedConcept.external_mappings || [])
			.filter((cm) => isUnscoredNonUserAssertedFiboPathAMapping(cm))
			.map((cm) => cm.target_uri.trim()),
	);
	if (committedUnscoredUris.size === 0) return false;
	return list.some(
		(m) =>
			isUnscoredNonUserAssertedFiboPathAMapping(m) &&
			committedUnscoredUris.has(m.target_uri.trim()),
	);
}

export function computeQualityScorecard(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	optionsOrGrandfathered: string[] | QualityScorecardOptions = [],
	waiverAppliedOrWaivedUris: boolean | string[] = false,
	waivedUrisParam: string[] = [],
): QualityScorecard {
	let grandfatheredUris: string[] = [];
	let waiverApplied = false;
	let waivedUris: string[] = [];
	let committedNodes: ConceptNode[] = [];
	let waivedSnapshots: Record<string, ConceptNode> | undefined;

	if (
		!Array.isArray(optionsOrGrandfathered) &&
		typeof optionsOrGrandfathered === "object" &&
		optionsOrGrandfathered !== null
	) {
		grandfatheredUris = optionsOrGrandfathered.grandfatheredUris || [];
		waiverApplied = Boolean(optionsOrGrandfathered.waiverApplied);
		waivedUris = optionsOrGrandfathered.waivedUris || [];
		committedNodes = optionsOrGrandfathered.committedNodes || [];
		waivedSnapshots = optionsOrGrandfathered.waivedSnapshots;
	} else {
		grandfatheredUris = Array.isArray(optionsOrGrandfathered)
			? optionsOrGrandfathered
			: [];
		waiverApplied =
			typeof waiverAppliedOrWaivedUris === "boolean"
				? waiverAppliedOrWaivedUris
				: true;
		waivedUris = Array.isArray(waiverAppliedOrWaivedUris)
			? waiverAppliedOrWaivedUris
			: waivedUrisParam;
	}

	const committedMap = new Map(committedNodes.map((c) => [c.concept_uri, c]));
	const currentMap = new Map(nodes.map((n) => [n.concept_uri, n]));

	const effectiveGrandfatheredUris = grandfatheredUris.filter((uri) => {
		const curr = currentMap.get(uri);
		const comm = committedMap.get(uri);
		if (curr && comm && isConceptMutatedFromBaseline(curr, comm)) {
			return false;
		}
		return true;
	});

	const effectiveWaivedUris = waivedUris.filter((uri) => {
		const curr = currentMap.get(uri);
		const baseline = waivedSnapshots?.[uri] ?? committedMap.get(uri);
		if (curr && baseline && isConceptMutatedFromBaseline(curr, baseline)) {
			return false;
		}
		return true;
	});
	const effectiveWaiverApplied =
		waiverApplied &&
		(waivedUris.length === 0 || effectiveWaivedUris.length > 0);

	const missingGrandfathered = grandfatheredUris.filter(
		(uri) => !nodes.some((n) => n.concept_uri === uri),
	);
	const ontEvo1Status = missingGrandfathered.length === 0 ? "PASS" : "FAIL";
	const ontEvo1Details =
		missingGrandfathered.length === 0
			? "All historical grandfathered entities preserved without breaking deletions."
			: `Grandfathered entities removed: ${missingGrandfathered.join(", ")}`;

	function isConceptGrounded(n: ConceptNode): boolean {
		const mappings = n.external_mappings || [];
		if (
			mappings.length > 0 &&
			deriveGroundingStatus(mappings, n.confidence).grounding_status ===
				"GROUNDED"
		) {
			return true;
		}
		if (
			mappings.length > 0 &&
			n.grounding_status === "GROUNDED" &&
			hasPreservedCommittedUnscoredFiboPathAMapping(
				mappings,
				committedMap.get(n.concept_uri),
			)
		) {
			return true;
		}
		return hasVerifiableEmpiricalEvidence(n);
	}

	const groundedNodes = nodes.filter((n) => isConceptGrounded(n)).length;
	const ungroundedNodes = nodes.filter(
		(n) =>
			!isConceptGrounded(n) &&
			!effectiveGrandfatheredUris.includes(n.concept_uri),
	);
	const groundingRatio = nodes.length > 0 ? groundedNodes / nodes.length : 0;

	const allUngroundedWaived =
		effectiveWaiverApplied &&
		(ungroundedNodes.length === 0 ||
			ungroundedNodes.every((n) =>
				effectiveWaivedUris.includes(n.concept_uri),
			));

	let ontEvo2Status: "PASS" | "WARN" | "FAIL";
	let ontEvo2Details: string;
	if (nodes.length === 0) {
		ontEvo2Status = "PASS";
		ontEvo2Details = "Zero concepts in session. No ungrounded URIs.";
	} else if (ungroundedNodes.length === 0) {
		ontEvo2Status = "PASS";
		ontEvo2Details = `All ${nodes.length} concepts are grounded or covered by historical grandfathered exemptions.`;
	} else if (allUngroundedWaived) {
		ontEvo2Status = "PASS";
		ontEvo2Details = `Grounding ratio: ${(groundingRatio * 100).toFixed(1)}% (${groundedNodes}/${nodes.length} grounded). Covered by authorized steward administrative waiver (ONT-EVO-002).`;
	} else {
		ontEvo2Status = "FAIL";
		ontEvo2Details = `Grounding ratio: ${(groundingRatio * 100).toFixed(1)}% (${groundedNodes}/${nodes.length} grounded). ${ungroundedNodes.length} ungrounded concepts lack authorized waiver (violates ONT-EVO-002).`;
	}

	const nodeUris = new Set(nodes.map((n) => n.concept_uri));
	const adj = new Map<string, string[]>();
	for (const uri of nodeUris) adj.set(uri, []);

	for (const n of nodes) {
		if (n.parent_concept_uri && adj.has(n.concept_uri)) {
			adj.get(n.concept_uri)?.push(n.parent_concept_uri);
		}
	}

	for (const e of edges) {
		const relName = e.relationship_name?.trim() || "";
		const isTaxonomic =
			/^(subclass_of|subclass|is_a|rdfs:subclassof)$/i.test(relName) ||
			e.relationship_uri?.toLowerCase().endsWith("subclassof") ||
			e.target_link?.toLowerCase() === "lnk_subclass";
		if (isTaxonomic && adj.has(e.source_concept_uri)) {
			adj.get(e.source_concept_uri)?.push(e.target_concept_uri);
		}
	}

	let hasCycle = false;
	const visited = new Set<string>();
	const inStack = new Set<string>();
	function dfs(u: string): boolean {
		visited.add(u);
		inStack.add(u);
		for (const v of adj.get(u) || []) {
			if (!visited.has(v)) {
				if (dfs(v)) return true;
			} else if (inStack.has(v)) {
				return true;
			}
		}
		inStack.delete(u);
		return false;
	}
	for (const u of nodeUris) {
		if (!visited.has(u)) {
			if (dfs(u)) {
				hasCycle = true;
				break;
			}
		}
	}
	const dagStatus = hasCycle ? "FAIL" : "PASS";
	const dagDetails = hasCycle
		? "Cycle detected in concept inheritance hierarchy (taxonomic cycles violate ONT-DAG-001)."
		: "Zero cycles detected across concept inheritance hierarchy.";

	const conceptsWithoutAttrs = nodes.filter(
		(n) => n.attributes.length === 0,
	).length;
	const yagniStatus = conceptsWithoutAttrs > 0 ? "WARN" : "PASS";
	const yagniDetails =
		conceptsWithoutAttrs === 0
			? "All concept entities declare explicit attributes."
			: `${conceptsWithoutAttrs} concept(s) have no declared attributes.`;

	return {
		grounding_score: Number.parseFloat(groundingRatio.toFixed(4)),
		deployed_contracts_ok: ontEvo1Status === "PASS",
		dag_acyclic: dagStatus === "PASS",
		yagni_compliant: yagniStatus === "PASS",
		checks: [
			{
				rule_id: "ONT-EVO-001",
				rule_name: "Deployed Table Contracts",
				status: ontEvo1Status,
				details: ontEvo1Details,
			},
			{
				rule_id: "ONT-EVO-002",
				rule_name: "Regulatory Grounding Threshold",
				status: ontEvo2Status,
				details: ontEvo2Details,
			},
			{
				rule_id: "ONT-DAG-001",
				rule_name: "Acyclic Concept Hierarchy",
				status: dagStatus,
				details: dagDetails,
			},
			{
				rule_id: "YAGNI-001",
				rule_name: "Concept Density & YAGNI",
				status: yagniStatus,
				details: yagniDetails,
			},
		],
		waiver_applied: effectiveWaiverApplied,
		waiver_rationale: "",
		waived_concept_uris: effectiveWaivedUris,
		feedback_rating: 0,
		feedback_comment: "",
		feedback_submitted: false,
	};
}

export interface RecomputeScorecardInput {
	activeSession: StudioSessionSummary | null;
	sessionWaivers: Record<
		string,
		{
			waiver_applied: boolean;
			waiver_rationale: string;
			waived_concept_uris: string[];
			waived_base_sha256?: string | undefined;
			waived_concept_snapshots?: Record<string, ConceptNode> | undefined;
		}
	>;
	committedNodes: ConceptNode[];
	qualityScorecard: QualityScorecard;
}

export function recomputeScorecard(
	state: RecomputeScorecardInput,
	newNodes: ConceptNode[],
	newEdges: RelationshipEdge[],
): QualityScorecard {
	const sid = state.activeSession?.session_id;
	const savedWaiver =
		(sid && state.sessionWaivers && state.sessionWaivers[sid]) || null;
	const isWaiverValid =
		Boolean(savedWaiver?.waiver_applied) &&
		Boolean(savedWaiver?.waived_base_sha256) &&
		Boolean(state.activeSession?.head_sha256) &&
		savedWaiver?.waived_base_sha256 === state.activeSession?.head_sha256;
	const waivedUris: string[] = isWaiverValid
		? savedWaiver?.waived_concept_uris || []
		: [];

	const computed = computeQualityScorecard(newNodes, newEdges, {
		grandfatheredUris: state.activeSession?.grandfathered_concept_uris || [],
		waiverApplied: isWaiverValid,
		waivedUris,
		committedNodes: state.committedNodes || [],
		...(savedWaiver?.waived_concept_snapshots
			? { waivedSnapshots: savedWaiver.waived_concept_snapshots }
			: {}),
	});

	return {
		...computed,
		waiver_applied: computed.waiver_applied,
		waiver_rationale: computed.waiver_applied
			? savedWaiver?.waiver_rationale || ""
			: "",
		waived_concept_uris: computed.waived_concept_uris || [],
		waived_base_sha256: computed.waiver_applied
			? savedWaiver?.waived_base_sha256 || undefined
			: undefined,
		feedback_rating: state.qualityScorecard.feedback_rating,
		feedback_comment: state.qualityScorecard.feedback_comment,
		feedback_concept_uri: state.qualityScorecard.feedback_concept_uri,
		feedback_submitted: state.qualityScorecard.feedback_submitted,
	};
}
