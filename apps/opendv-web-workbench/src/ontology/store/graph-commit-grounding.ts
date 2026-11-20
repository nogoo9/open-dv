/**
 * Pre-Commit Grounding & DAG Acyclicity Validation
 * (apps/opendv-web-workbench/src/ontology/store/graph-commit-grounding.ts)
 */

import { GROUNDING_MIN_CONFIDENCE } from "../constants";
import { hasVerifiableEmpiricalEvidence } from "./evidence-grounding";
import {
	type CommitValidationError,
	normalizeEdgeEndpoints,
} from "./graph-commit-validation";
import { isConceptMutatedFromBaseline } from "./graph-parser";
import {
	VALID_PATH_A_PREDICATES,
	deriveGroundingStatus,
	hasPreservedCommittedUnscoredFiboPathAMapping,
	recomputeScorecard,
} from "./scorecard";
import type { StudioStoreState } from "./store-types";
import type { ConceptNode, RelationshipEdge } from "./types";

export { hasVerifiableEmpiricalEvidence };

function hasCandidateFiboPathAMapping(node: ConceptNode): boolean {
	const mappings = node.external_mappings || [];
	return mappings.some((m) => {
		const isFibo = (m.scheme || "").trim().toLowerCase() === "fibo";
		const hasUri =
			typeof m.target_uri === "string" && m.target_uri.trim().length > 0;
		const hasPathAPredicate =
			typeof m.predicate === "string" &&
			VALID_PATH_A_PREDICATES.has(m.predicate.trim());
		if (!isFibo || !hasUri || !hasPathAPredicate) return false;
		const isUserAsserted =
			(m.provenance || "").trim().toLowerCase() === "user_asserted";
		const rawConf =
			typeof m.confidence === "number"
				? m.confidence
				: typeof m.mapping_confidence === "number"
					? m.mapping_confidence
					: undefined;
		if (isUserAsserted || rawConf === undefined) {
			return true;
		}
		return (
			typeof rawConf === "number" &&
			Number.isFinite(rawConf) &&
			rawConf >= GROUNDING_MIN_CONFIDENCE
		);
	});
}

export function validateCommitGroundingAndDag(params: {
	state: StudioStoreState;
	currentNodes: ConceptNode[];
	currentEdges: RelationshipEdge[];
	committedNodeMap: Map<string, ConceptNode>;
	removedEdges: RelationshipEdge[];
	modifiedCommittedEdges: RelationshipEdge[];
}): CommitValidationError | null {
	const {
		state,
		currentNodes,
		currentEdges,
		committedNodeMap,
		removedEdges,
		modifiedCommittedEdges,
	} = params;
	const activeSession = state.activeSession;
	if (!activeSession) return null;

	const authoritativeScorecard = recomputeScorecard(
		state,
		currentNodes,
		currentEdges,
	);
	const evoCheck = authoritativeScorecard.checks.find(
		(c) => c.rule_id === "ONT-EVO-002",
	);
	if (evoCheck?.status === "FAIL") {
		const rawGrandfathered = activeSession.grandfathered_concept_uris || [];
		const currentNodeMap = new Map(currentNodes.map((n) => [n.concept_uri, n]));
		const effectiveGrandfatheredSet = new Set(
			rawGrandfathered.filter((uri) => {
				const curr = currentNodeMap.get(uri);
				const comm = committedNodeMap.get(uri);
				return !(curr && comm && isConceptMutatedFromBaseline(curr, comm));
			}),
		);
		const effectiveWaivedSet = new Set(
			authoritativeScorecard.waiver_applied
				? authoritativeScorecard.waived_concept_uris || []
				: [],
		);

		const touchedConceptUris = new Set<string>();
		for (const n of currentNodes) {
			const comm = committedNodeMap.get(n.concept_uri);
			if (!comm || isConceptMutatedFromBaseline(n, comm)) {
				touchedConceptUris.add(n.concept_uri);
			}
		}
		for (const edge of [...removedEdges, ...modifiedCommittedEdges]) {
			for (const ep of normalizeEdgeEndpoints(edge)) {
				if (ep) touchedConceptUris.add(ep);
			}
		}

		const flaggedNodes = currentNodes.filter((n) => {
			if (!touchedConceptUris.has(n.concept_uri)) return false;
			const isGrounded =
				deriveGroundingStatus(n.external_mappings, n.confidence)
					.grounding_status === "GROUNDED" ||
				(n.grounding_status === "GROUNDED" &&
					hasPreservedCommittedUnscoredFiboPathAMapping(
						n.external_mappings,
						committedNodeMap.get(n.concept_uri),
					)) ||
				hasVerifiableEmpiricalEvidence(n);
			if (isGrounded) return false;
			if (effectiveGrandfatheredSet.has(n.concept_uri)) return false;
			if (effectiveWaivedSet.has(n.concept_uri)) return false;
			return true;
		});
		const hasBlockingFlaggedNodes =
			flaggedNodes.length > 0 &&
			!flaggedNodes.every((n) => hasCandidateFiboPathAMapping(n));

		if (hasBlockingFlaggedNodes) {
			return {
				type: "urn:opendv:error:grounding-invariant-failed",
				title: "Regulatory Grounding Invariant Failed",
				status: 400,
				detail: `Cannot commit changes while ONT-EVO-002 is failing. Ground all concepts with FIBO skos:exactMatch/closeMatch >= ${GROUNDING_MIN_CONFIDENCE} or obtain an authorized steward administrative waiver.`,
				code: "ONT_EVO_002_FAILED",
			};
		}
	}

	const dagCheck = authoritativeScorecard.checks.find(
		(c) => c.rule_id === "ONT-DAG-001",
	);
	if (dagCheck?.status === "FAIL") {
		return {
			type: "urn:opendv:error:taxonomic-cycle",
			title: "Taxonomic Cycle Detected",
			status: 400,
			detail:
				"Cannot commit ontology with circular taxonomic inheritance hierarchy (violates ONT-DAG-001).",
			code: "ONT_DAG_CYCLE_DETECTED",
		};
	}

	return null;
}
