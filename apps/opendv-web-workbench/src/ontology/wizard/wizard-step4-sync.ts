/**
 * Step 4 Store Sync, Candidate Materialization & Ask-FIBO Action Helpers
 * (apps/opendv-web-workbench/src/ontology/wizard/wizard-step4-sync.ts)
 */

import { sanitizeEvidenceRefs } from "../store/evidence-grounding";
import type { StudioStoreState } from "../store/store-types";
import type {
	ConceptAttribute,
	ConceptNode,
	ExternalMapping,
} from "../store/types";
import type { AskFiboSuggestedAction } from "./AskFiboPanel";
import { deriveStep4State, slugifyName } from "./wizard-step4-adapter";
import type {
	WizardBreakingCandidate,
	WizardConceptCandidate,
} from "./wizard-types";

function buildCandidateExternalMappings(
	c: WizardConceptCandidate,
): ExternalMapping[] {
	if (Array.isArray(c.external_mappings) && c.external_mappings.length > 0) {
		const out: ExternalMapping[] = [];
		for (const raw of c.external_mappings) {
			const targetUri = String(raw.target_uri ?? "").trim();
			if (!targetUri) continue;
			const rawConf = raw.confidence ?? raw.mapping_confidence;
			out.push({
				scheme: String(raw.scheme ?? "fibo"),
				target_uri: targetUri,
				predicate: String(raw.predicate ?? "skos:broadMatch"),
				...(typeof rawConf === "number" && Number.isFinite(rawConf)
					? { confidence: rawConf }
					: {}),
				provenance: String(raw.provenance ?? "agent_grounded"),
			});
		}
		if (out.length > 0) return out;
	}
	if (c.fibo_match) {
		return [
			{
				scheme: "fibo",
				target_uri: c.fibo_match.uri,
				predicate: c.fibo_match.predicate || "skos:broadMatch",
				confidence: c.fibo_match.score,
				provenance: "agent_grounded",
			},
		];
	}
	return [];
}

function buildConceptNodeFromCandidate(
	c: WizardConceptCandidate,
	checkedAttrs: ConceptAttribute[],
): ConceptNode {
	const mappings = buildCandidateExternalMappings(c);
	const evidenceRefs = sanitizeEvidenceRefs(c.evidence_refs);
	return {
		id: c.concept_uri,
		concept_uri: c.concept_uri,
		concept_name: c.concept_name,
		elm_archetype: "CORE_BUSINESS_CONCEPT",
		definition: c.definition || `${c.concept_name} business concept`,
		...(c.description ? { description: c.description } : {}),
		...(c.target_hub ? { target_hub: c.target_hub } : {}),
		grounding_status:
			mappings.length > 0 && (c.fibo_match?.score ?? 0) >= 0.7
				? "GROUNDED"
				: "UNGROUNDED",
		...(c.grounding_origin ? { grounding_origin: c.grounding_origin } : {}),
		...(evidenceRefs ? { evidence_refs: evidenceRefs } : {}),
		attributes: checkedAttrs,
		synonyms: [],
		external_mappings: mappings,
		source_hints: [],
	};
}

export async function syncStep4DecisionsToStore(
	store: StudioStoreState,
	concepts: WizardConceptCandidate[],
	breakingList: WizardBreakingCandidate[],
	fetchImpl = fetch,
): Promise<void> {
	const stagedById = new Map(
		store.stagedCandidates.map((sc) => [sc.candidate_id, sc]),
	);
	const promoteTasks: Array<Promise<unknown>> = [];

	for (const c of concepts) {
		if (c.checked) {
			const fallbackRefs = sanitizeEvidenceRefs(c.evidence_refs);
			const checkedAttrs: ConceptAttribute[] = c.attributes
				.filter((a) => a.checked)
				.map((a) => {
					const attrRefs =
						sanitizeEvidenceRefs(a.evidence_refs) ?? fallbackRefs;
					return {
						attribute_name: a.name,
						data_type: a.data_type || "string",
						is_optional: a.is_optional ?? true,
						description: null,
						...(attrRefs && attrRefs.length > 0
							? { evidence_refs: attrRefs }
							: {}),
					};
				});
			const existing = store.nodes.find(
				(n) =>
					n.concept_uri === c.concept_uri ||
					n.concept_name.toLowerCase() === c.concept_name.toLowerCase(),
			);
			if (existing) {
				const updates: Partial<ConceptNode> = {};
				if (checkedAttrs.length > 0) {
					const byName = new Map(
						(existing.attributes ?? []).map((a) => [a.attribute_name, a]),
					);
					for (const ca of checkedAttrs) {
						byName.set(ca.attribute_name, ca);
					}
					updates.attributes = Array.from(byName.values());
				}
				if (c.grounding_origin && !existing.grounding_origin) {
					updates.grounding_origin = c.grounding_origin;
				}
				const sanitizedRefs = sanitizeEvidenceRefs(c.evidence_refs);
				if (
					sanitizedRefs &&
					sanitizedRefs.length > 0 &&
					(!existing.evidence_refs || existing.evidence_refs.length === 0)
				) {
					updates.evidence_refs = sanitizedRefs;
				}
				const candMappings = buildCandidateExternalMappings(c);
				if (candMappings.length > 0) {
					const existingUris = new Set(
						(existing.external_mappings ?? []).map((m) => m.target_uri),
					);
					const newMaps = candMappings.filter(
						(m) => !existingUris.has(m.target_uri),
					);
					if (newMaps.length > 0) {
						updates.external_mappings = [
							...(existing.external_mappings ?? []),
							...newMaps,
						];
					}
				}
				if (Object.keys(updates).length > 0) {
					store.updateConcept(existing.concept_uri, updates);
				}
			} else {
				store.addConcept(buildConceptNodeFromCandidate(c, checkedAttrs));
			}
			const staged = stagedById.get(c.candidate_id);
			if (staged && staged.status === "held_back") {
				const path =
					c.fibo_match && c.fibo_match.score >= 0.65 ? "path_a" : "path_b";
				promoteTasks.push(
					store
						.promoteStagedCandidate(
							c.candidate_id,
							path,
							"Steward selected in Step 4",
							fetchImpl,
						)
						.catch(() => false),
				);
			}
		} else {
			const isCommitted = store.committedNodes.some(
				(n) => n.concept_uri === c.concept_uri,
			);
			if (
				!isCommitted &&
				store.nodes.some((n) => n.concept_uri === c.concept_uri)
			) {
				store.deleteConcept(c.concept_uri);
			}
		}
	}

	const activeNodeUris = new Set(store.nodes.map((n) => n.concept_uri));
	for (const c of concepts) {
		if (!c.checked) continue;
		activeNodeUris.add(c.concept_uri);
	}
	for (const c of concepts) {
		if (!c.checked) continue;
		for (const lnk of c.dependent_links) {
			if (!lnk.checked || !lnk.target_concept) continue;
			if (
				!activeNodeUris.has(c.concept_uri) ||
				!activeNodeUris.has(lnk.target_concept)
			) {
				continue;
			}
			const relSlug = slugifyName(lnk.name);
			const relUri = `urn:ontology:relationship:${relSlug}`;
			if (
				!store.edges.some(
					(e) =>
						e.relationship_uri === relUri ||
						(e.source_concept_uri === c.concept_uri &&
							e.target_concept_uri === lnk.target_concept &&
							e.relationship_name === lnk.name),
				)
			) {
				store.addRelationship({
					id: relUri,
					relationship_uri: relUri,
					relationship_name: lnk.name,
					target_link: `lnk_${relSlug}`,
					source_concept_uri: c.concept_uri,
					target_concept_uri: lnk.target_concept,
				});
			}
		}
	}

	for (const bk of breakingList) {
		if (!bk.checked) continue;
		if (bk.op_type === "remove_concept") {
			const hit = store.nodes.find(
				(n) =>
					n.concept_uri === bk.label ||
					n.concept_name.toLowerCase() === bk.label.toLowerCase(),
			);
			if (hit) store.deleteConcept(hit.concept_uri);
		} else if (bk.op_type === "remove_relationship") {
			const hit = store.edges.find(
				(e) =>
					e.relationship_uri === bk.label ||
					e.relationship_name.toLowerCase() === bk.label.toLowerCase(),
			);
			if (hit) store.deleteRelationship(hit.relationship_uri);
		} else if (bk.op_type === "deprecate_concept") {
			const hit = store.nodes.find(
				(n) =>
					n.concept_uri === bk.label ||
					n.concept_name.toLowerCase() === bk.label.toLowerCase(),
			);
			if (hit) store.updateConcept(hit.concept_uri, { deprecated: true });
		} else if (bk.op_type === "remove_attribute") {
			const targetAttr = (bk.attribute_name || bk.label).trim();
			const hit = store.nodes.find(
				(n) =>
					(bk.concept_uri ? n.concept_uri === bk.concept_uri : true) &&
					(n.attributes ?? []).some((a) => a.attribute_name === targetAttr),
			);
			if (hit) {
				store.updateConcept(hit.concept_uri, {
					attributes: (hit.attributes ?? []).filter(
						(a) => a.attribute_name !== targetAttr,
					),
				});
			}
		}
	}

	if (promoteTasks.length > 0) {
		await Promise.all(promoteTasks);
	}
}

export function findOrMaterializeConcept(
	store: StudioStoreState,
	conceptUri: string,
	conceptName: string,
): ConceptNode | undefined {
	const existing = store.nodes.find(
		(n) =>
			(conceptUri && n.concept_uri === conceptUri) ||
			(conceptName &&
				n.concept_name.toLowerCase() === conceptName.toLowerCase()),
	);
	if (existing) return existing;
	const { concepts } = deriveStep4State(
		store.proposals,
		store.stagedCandidates,
	);
	const cand = concepts.find(
		(c) =>
			(conceptUri && c.concept_uri === conceptUri) ||
			(conceptName &&
				c.concept_name.toLowerCase() === conceptName.toLowerCase()),
	);
	if (!cand) return undefined;
	const checkedAttrs: ConceptAttribute[] = cand.attributes
		.filter((a) => a.checked)
		.map((a) => ({
			attribute_name: a.name,
			data_type: a.data_type || "string",
			is_optional: a.is_optional ?? true,
			description: null,
		}));
	const created = buildConceptNodeFromCandidate(cand, checkedAttrs);
	store.addConcept(created);
	return created;
}

export function applyAskFiboActionToStore(
	store: StudioStoreState,
	action: AskFiboSuggestedAction,
): void {
	const p = (action.payload ?? {}) as Record<string, unknown>;
	const opType = action.op_type;

	if (
		opType === "add_external_mapping" ||
		opType === "map_fibo" ||
		opType === "link_fibo"
	) {
		const target = findOrMaterializeConcept(
			store,
			String(p.concept_uri ?? ""),
			String(p.concept_name ?? ""),
		);
		const targetUri = String(p.target_uri ?? p.fibo_uri ?? "");
		if (target && targetUri) {
			const nextMap: ExternalMapping = {
				scheme: String(p.scheme ?? "fibo"),
				target_uri: targetUri,
				predicate: String(p.predicate ?? "skos:broadMatch"),
				confidence: Number(p.confidence ?? 0.85),
				provenance: "user_asserted",
			};
			store.updateConcept(target.concept_uri, {
				external_mappings: [...(target.external_mappings ?? []), nextMap],
			});
		}
	} else if (opType === "add_concept") {
		const name = String(p.concept_name ?? action.label ?? "NewConcept");
		const uri = String(
			p.concept_uri ?? `urn:opendv:concept:${slugifyName(name)}`,
		);
		const evidenceRefs = sanitizeEvidenceRefs(p.evidence_refs);
		store.addConcept({
			id: uri,
			concept_uri: uri,
			concept_name: name,
			elm_archetype: String(p.elm_archetype ?? "CORE_BUSINESS_CONCEPT"),
			definition: String(p.definition ?? action.label),
			grounding_status: "UNGROUNDED",
			...(typeof p.grounding_origin === "string"
				? { grounding_origin: p.grounding_origin }
				: {}),
			...(evidenceRefs ? { evidence_refs: evidenceRefs } : {}),
			attributes: [],
			synonyms: [],
			external_mappings: [],
		});
	} else if (opType === "add_attribute") {
		const target = findOrMaterializeConcept(
			store,
			String(p.concept_uri ?? ""),
			String(p.concept_name ?? ""),
		);
		const attrName = String(p.attribute_name ?? "");
		if (target && attrName) {
			store.updateConcept(target.concept_uri, {
				attributes: [
					...(target.attributes ?? []),
					{
						attribute_name: attrName,
						data_type: String(p.data_type ?? "string"),
						is_optional: p.is_optional !== false,
						description: null,
					},
				],
			});
		}
	} else if (opType === "refine_definition" && p.definition) {
		const target = findOrMaterializeConcept(
			store,
			String(p.concept_uri ?? ""),
			String(p.concept_name ?? ""),
		);
		if (target) {
			store.updateConcept(target.concept_uri, {
				definition: String(p.definition),
			});
		}
	}

	store.addToast({
		type: "urn:opendv:notice:fibo-action-applied",
		title: "FIBO Action Applied",
		status: 200,
		detail: action.label,
		code: "OK",
	});
}
