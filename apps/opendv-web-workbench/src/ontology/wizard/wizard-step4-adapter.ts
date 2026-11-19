/**
 * Step 4 Adapter: Derive Wizard Candidates from Store Proposals/Staged Candidates,
 * Sync Triage Decisions to Store Graph, & Apply Ask-FIBO Suggested Actions.
 * (apps/opendv-web-workbench/src/ontology/wizard/wizard-step4-adapter.ts)
 */

import type { StudioStoreState } from "../store/store-types";
import type {
	CandidateProposalSummary,
	ConceptAttribute,
	ConceptNode,
	ExternalMapping,
	StagedCandidateDTO,
} from "../store/types";
import type { AskFiboSuggestedAction } from "./AskFiboPanel";
import type {
	WizardBreakingCandidate,
	WizardCandidateAttribute,
	WizardCandidateLink,
	WizardConceptCandidate,
} from "./wizard-types";

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

function slugifyName(name: string): string {
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
		return {
			attribute_id: String(obj.attribute_id ?? `${opId}-${attrName}`),
			name: attrName,
			data_type: String(obj.data_type ?? "string"),
			checked: obj.checked !== false,
			is_optional: obj.is_optional !== false,
		};
	});
}

function deriveOriginAndBucket(
	explicitOrigin: unknown,
	evidenceRefs: Array<Record<string, unknown>>,
): { grounding_origin: string; bucket: "structured" | "unstructured" } {
	const rawOrigin = String(explicitOrigin || "").toUpperCase();
	if (rawOrigin === "DOCUMENT_OBSERVED") {
		return { grounding_origin: "DOCUMENT_OBSERVED", bucket: "unstructured" };
	}
	if (rawOrigin === "STRUCTURED_SCHEMA") {
		return { grounding_origin: "STRUCTURED_SCHEMA", bucket: "structured" };
	}
	const hasDocSpan = evidenceRefs.some(
		(e) => e.evidence_type === "document_span" || Boolean(e.chunk_id),
	);
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
				grounding_origin,
				bucket,
				...(typeof op.mention_count === "number"
					? { mention_count: op.mention_count }
					: bucket === "unstructured"
						? { mention_count: 1 }
						: {}),
				checked: true,
				...(fiboMatch ? { fibo_match: fiboMatch } : {}),
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
			const attr: WizardCandidateAttribute = {
				attribute_id: `${opId}-${attrName}`,
				name: attrName,
				data_type: String(op.data_type ?? "string"),
				checked: true,
				is_optional: op.is_optional !== false,
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
			const checkedAttrs: ConceptAttribute[] = c.attributes
				.filter((a) => a.checked)
				.map((a) => ({
					attribute_name: a.name,
					data_type: a.data_type || "string",
					is_optional: a.is_optional ?? true,
					description: null,
				}));
			const existing = store.nodes.find(
				(n) =>
					n.concept_uri === c.concept_uri ||
					n.concept_name.toLowerCase() === c.concept_name.toLowerCase(),
			);
			if (existing) {
				if (checkedAttrs.length > 0) {
					const byName = new Map(
						(existing.attributes ?? []).map((a) => [a.attribute_name, a]),
					);
					for (const ca of checkedAttrs) {
						byName.set(ca.attribute_name, ca);
					}
					store.updateConcept(existing.concept_uri, {
						attributes: Array.from(byName.values()),
					});
				}
			} else {
				const mappings: ExternalMapping[] = c.fibo_match
					? [
							{
								scheme: "fibo",
								target_uri: c.fibo_match.uri,
								predicate: c.fibo_match.predicate || "skos:broadMatch",
								confidence: c.fibo_match.score,
								provenance: "agent_grounded",
							},
						]
					: [];
				const newNode: ConceptNode = {
					id: c.concept_uri,
					concept_uri: c.concept_uri,
					concept_name: c.concept_name,
					elm_archetype: "CORE_BUSINESS_CONCEPT",
					definition: `${c.concept_name} business concept`,
					grounding_status:
						mappings.length > 0 && (c.fibo_match?.score ?? 0) >= 0.7
							? "GROUNDED"
							: "UNGROUNDED",
					attributes: checkedAttrs,
					synonyms: [],
					external_mappings: mappings,
					source_hints: [],
				};
				store.addConcept(newNode);
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
		}
	}

	if (promoteTasks.length > 0) {
		await Promise.all(promoteTasks);
	}
}

function findOrMaterializeConcept(
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
	const created: ConceptNode = {
		id: cand.concept_uri,
		concept_uri: cand.concept_uri,
		concept_name: cand.concept_name,
		elm_archetype: "CORE_BUSINESS_CONCEPT",
		definition: `${cand.concept_name} business concept`,
		grounding_status: cand.fibo_match ? "GROUNDED" : "UNGROUNDED",
		attributes: cand.attributes
			.filter((a) => a.checked)
			.map((a) => ({
				attribute_name: a.name,
				data_type: a.data_type || "string",
				is_optional: a.is_optional ?? true,
				description: null,
			})),
		synonyms: [],
		external_mappings: cand.fibo_match
			? [
					{
						scheme: "fibo",
						target_uri: cand.fibo_match.uri,
						predicate: cand.fibo_match.predicate || "skos:broadMatch",
						confidence: cand.fibo_match.score,
						provenance: "agent_grounded",
					},
				]
			: [],
		source_hints: [],
	};
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
		store.addConcept({
			id: uri,
			concept_uri: uri,
			concept_name: name,
			elm_archetype: String(p.elm_archetype ?? "CORE_BUSINESS_CONCEPT"),
			definition: String(p.definition ?? action.label),
			grounding_status: "UNGROUNDED",
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
