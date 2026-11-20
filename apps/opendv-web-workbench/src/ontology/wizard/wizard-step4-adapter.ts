/**
 * Step 4 Adapter: Derive Wizard Candidates from Store Proposals/Staged Candidates
 * and re-export Store Sync / Ask-FIBO helpers.
 * (apps/opendv-web-workbench/src/ontology/wizard/wizard-step4-adapter.ts)
 */

import type {
	CandidateProposalSummary,
	StagedCandidateDTO,
} from "../store/types";
import type {
	WizardBreakingCandidate,
	WizardCandidateAttribute,
	WizardCandidateLink,
	WizardConceptCandidate,
} from "./wizard-types";

export {
	applyAskFiboActionToStore,
	findOrMaterializeConcept,
	syncStep4DecisionsToStore,
} from "./wizard-step4-sync";

export interface ConceptOverrideMap {
	[candidateId: string]: {
		checked?: boolean | undefined;
		attrTypes?: Record<string, string> | undefined;
		attrChecked?: Record<string, boolean> | undefined;
		linkChecked?: Record<string, boolean> | undefined;
	};
}

const BREAKING_OP_TYPES = new Set([
	"remove_concept",
	"remove_relationship",
	"remove_attribute",
	"deprecate_concept",
	"deprecate_relationship",
	"update_concept_urn",
]);

export function slugifyName(name: string): string {
	return (
		name
			.trim()
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "_")
			.replace(/^_+|_+$/g, "") || "concept"
	);
}

function normalizeOpAttributes(
	rawAttrs: unknown,
	opId: string,
): WizardCandidateAttribute[] {
	if (!Array.isArray(rawAttrs)) return [];
	return rawAttrs.map((a, idx) => {
		if (typeof a === "string") {
			return {
				attribute_id: `${opId}-attr-${idx}`,
				name: a,
				data_type: "string",
				checked: true,
				is_optional: true,
			};
		}
		const obj = (a && typeof a === "object" ? a : {}) as Record<
			string,
			unknown
		>;
		const attrName = String(
			obj.attribute_name ?? obj.name ?? `attr_${idx + 1}`,
		);
		const attrRefs = Array.isArray(obj.evidence_refs)
			? (obj.evidence_refs as Array<Record<string, unknown>>)
			: undefined;
		return {
			attribute_id: String(obj.attribute_id ?? `${opId}-${attrName}`),
			name: attrName,
			data_type: String(obj.data_type ?? "string"),
			checked: obj.checked !== false,
			is_optional: obj.is_optional !== false,
			...(attrRefs && attrRefs.length > 0 ? { evidence_refs: attrRefs } : {}),
		};
	});
}

function deriveOriginAndBucket(
	explicitOrigin: unknown,
	evidenceRefs: Array<Record<string, unknown>>,
): { grounding_origin: string; bucket: "structured" | "unstructured" } {
	const rawOrigin = String(explicitOrigin || "").toUpperCase();
	const hasDocSpan = evidenceRefs.some(
		(e) => e.evidence_type === "document_span" || Boolean(e.chunk_id),
	);
	if (rawOrigin === "DOCUMENT_OBSERVED") {
		return { grounding_origin: "DOCUMENT_OBSERVED", bucket: "unstructured" };
	}
	if (rawOrigin === "STRUCTURED_SCHEMA") {
		return { grounding_origin: "STRUCTURED_SCHEMA", bucket: "structured" };
	}
	if (rawOrigin === "FIBO_GROUNDED") {
		return {
			grounding_origin: "FIBO_GROUNDED",
			bucket: hasDocSpan ? "unstructured" : "structured",
		};
	}
	if (hasDocSpan) {
		return { grounding_origin: "DOCUMENT_OBSERVED", bucket: "unstructured" };
	}
	return {
		grounding_origin: rawOrigin || "STRUCTURED_SCHEMA",
		bucket: "structured",
	};
}

export function deriveStep4State(
	proposals: CandidateProposalSummary[],
	stagedCandidates: StagedCandidateDTO[],
	conceptOverrides: ConceptOverrideMap = {},
	breakingOverrides: Record<string, boolean> = {},
): {
	concepts: WizardConceptCandidate[];
	breakingCandidates: WizardBreakingCandidate[];
} {
	const conceptMap = new Map<string, WizardConceptCandidate>();
	const breakingCandidates: WizardBreakingCandidate[] = [];

	const newestPending = proposals.find(
		(p) => !p.status || p.status.toUpperCase() === "PENDING",
	);
	const ops = newestPending?.operations ?? [];

	for (let i = 0; i < ops.length; i += 1) {
		const op = ops[i];
		if (!op) continue;
		const opType = String(op.op_type ?? "");
		const opId = String(op.op_id ?? `op-${i}`);

		if (BREAKING_OP_TYPES.has(opType)) {
			const label = String(
				op.concept_name ||
					op.attribute_name ||
					op.relationship_name ||
					op.concept_uri ||
					op.relationship_uri ||
					opType,
			);
			breakingCandidates.push({
				candidate_id: opId,
				op_type: opType,
				label,
				impact: "MAJOR",
				checked: breakingOverrides[opId] ?? false,
				...(typeof op.concept_uri === "string" && op.concept_uri
					? { concept_uri: op.concept_uri }
					: {}),
				...(typeof op.attribute_name === "string" && op.attribute_name
					? { attribute_name: op.attribute_name }
					: {}),
			});
			continue;
		}

		if (opType === "add_concept") {
			const conceptUri = String(
				op.concept_uri ||
					`urn:ontology:entity:${slugifyName(String(op.concept_name || opId))}`,
			);
			const conceptName = String(
				op.concept_name || conceptUri.split(":").pop() || opId,
			);
			const refs = Array.isArray(op.evidence_refs)
				? (op.evidence_refs as Array<Record<string, unknown>>)
				: [];
			const { grounding_origin, bucket } = deriveOriginAndBucket(
				op.grounding_origin,
				refs,
			);
			const rawMappings = Array.isArray(op.external_mappings)
				? (op.external_mappings as Array<Record<string, unknown>>)
				: [];
			const firstMap = rawMappings[0];
			const fiboMatch = firstMap?.target_uri
				? {
						uri: String(firstMap.target_uri),
						predicate: String(firstMap.predicate || "skos:broadMatch"),
						score: Number(
							firstMap.confidence ??
								firstMap.mapping_confidence ??
								op.confidence ??
								0.85,
						),
					}
				: undefined;
			conceptMap.set(conceptUri, {
				candidate_id: opId,
				concept_uri: conceptUri,
				concept_name: conceptName,
				...(typeof op.definition === "string" && op.definition
					? { definition: op.definition }
					: {}),
				...(typeof op.description === "string" && op.description
					? { description: op.description }
					: {}),
				...(typeof op.target_hub === "string" && op.target_hub
					? { target_hub: op.target_hub }
					: {}),
				grounding_origin,
				bucket,
				...(typeof op.mention_count === "number"
					? { mention_count: op.mention_count }
					: bucket === "unstructured"
						? { mention_count: 1 }
						: {}),
				checked: true,
				...(fiboMatch ? { fibo_match: fiboMatch } : {}),
				...(rawMappings.length > 0 ? { external_mappings: rawMappings } : {}),
				attributes: normalizeOpAttributes(op.attributes, opId),
				dependent_links: [],
				evidence_refs: refs,
			});
		}
	}

	for (let i = 0; i < ops.length; i += 1) {
		const op = ops[i];
		if (!op) continue;
		const opType = String(op.op_type ?? "");
		const opId = String(op.op_id ?? `op-${i}`);
		if (opType === "add_attribute") {
			const uri = String(op.concept_uri ?? "");
			const target = conceptMap.get(uri);
			const attrName = String(op.attribute_name ?? `attr_${i}`);
			const attrRefs = Array.isArray(op.evidence_refs)
				? (op.evidence_refs as Array<Record<string, unknown>>)
				: undefined;
			const attr: WizardCandidateAttribute = {
				attribute_id: `${opId}-${attrName}`,
				name: attrName,
				data_type: String(op.data_type ?? "string"),
				checked: true,
				is_optional: op.is_optional !== false,
				...(attrRefs && attrRefs.length > 0 ? { evidence_refs: attrRefs } : {}),
			};
			if (target) {
				if (!target.attributes.some((a) => a.name === attrName)) {
					target.attributes.push(attr);
				}
			} else if (uri) {
				conceptMap.set(uri, {
					candidate_id: opId,
					concept_uri: uri,
					concept_name: String(op.concept_name || uri.split(":").pop() || uri),
					grounding_origin: "STRUCTURED_SCHEMA",
					bucket: "structured",
					checked: true,
					attributes: [attr],
					dependent_links: [],
					evidence_refs: Array.isArray(op.evidence_refs)
						? (op.evidence_refs as Array<Record<string, unknown>>)
						: [],
				});
			}
		} else if (opType === "add_relationship") {
			const srcUri = String(
				op.source_concept_uri ??
					(Array.isArray(op.endpoints) ? op.endpoints[0] : "") ??
					"",
			);
			const tgtUri = String(
				op.target_concept_uri ??
					(Array.isArray(op.endpoints) ? op.endpoints[1] : "") ??
					"",
			);
			const target = conceptMap.get(srcUri);
			if (target) {
				target.dependent_links.push({
					link_id: opId,
					name: String(op.relationship_name || op.target_link || opId),
					checked: true,
					...(tgtUri ? { target_concept: tgtUri } : {}),
				});
			}
		} else if (opType === "add_external_mapping") {
			const uri = String(op.concept_uri ?? "");
			const target = conceptMap.get(uri);
			const targetUri = String(op.target_uri ?? "");
			if (target && targetUri) {
				if (!target.fibo_match) {
					target.fibo_match = {
						uri: targetUri,
						predicate: String(op.predicate || "skos:broadMatch"),
						score: Number(op.mapping_confidence ?? op.confidence ?? 0.85),
					};
				}
				const existingMaps = target.external_mappings ?? [];
				if (!existingMaps.some((m) => m.target_uri === targetUri)) {
					target.external_mappings = [
						...existingMaps,
						op as Record<string, unknown>,
					];
				}
			}
		}
	}

	for (const cand of stagedCandidates) {
		if (cand.status === "rejected") continue;
		const slug = slugifyName(cand.concept_name);
		const uri = cand.top_match_uri?.startsWith("urn:")
			? cand.top_match_uri
			: `urn:ontology:entity:${slug}`;
		const alreadyPresent = Array.from(conceptMap.values()).some(
			(c) =>
				c.concept_uri === uri ||
				c.concept_name.toLowerCase() === cand.concept_name.toLowerCase(),
		);
		if (alreadyPresent) continue;
		const refs = Array.isArray(cand.evidence_refs) ? cand.evidence_refs : [];
		const { grounding_origin, bucket } = deriveOriginAndBucket(undefined, refs);
		const attrs = normalizeOpAttributes(cand.attributes, cand.candidate_id);
		const links: WizardCandidateLink[] = Array.isArray(cand.dependent_links)
			? cand.dependent_links.map((l, idx) => ({
					link_id: String(l.link_id ?? `${cand.candidate_id}-lnk-${idx}`),
					name: String(l.name ?? l.relationship_name ?? `link_${idx + 1}`),
					checked: l.checked !== false,
					...(l.target_concept
						? { target_concept: String(l.target_concept) }
						: {}),
				}))
			: [];
		conceptMap.set(uri, {
			candidate_id: cand.candidate_id,
			concept_uri: uri,
			concept_name: cand.concept_name,
			grounding_origin,
			bucket,
			...(bucket === "unstructured" ? { mention_count: 1 } : {}),
			checked: true,
			...(cand.top_match_uri
				? {
						fibo_match: {
							uri: cand.top_match_uri,
							predicate: "skos:broadMatch",
							score: Number(cand.top_match_score ?? 0.5),
						},
					}
				: {}),
			attributes: attrs,
			dependent_links: links,
			evidence_refs: refs,
		});
	}

	const concepts = Array.from(conceptMap.values()).map((c) => {
		const ov = conceptOverrides[c.candidate_id];
		if (!ov) return c;
		const parentChecked = ov.checked ?? c.checked;
		return {
			...c,
			checked: parentChecked,
			attributes: c.attributes.map((a) => ({
				...a,
				data_type: ov.attrTypes?.[a.attribute_id] ?? a.data_type,
				checked: parentChecked
					? (ov.attrChecked?.[a.attribute_id] ?? a.checked)
					: false,
			})),
			dependent_links: c.dependent_links.map((l) => ({
				...l,
				checked: parentChecked
					? (ov.linkChecked?.[l.link_id] ?? l.checked)
					: false,
			})),
		};
	});

	return { concepts, breakingCandidates };
}
