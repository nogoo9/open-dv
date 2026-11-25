import type {
	CanonicalBusinessConcept,
	CertifiedVaultIR,
	EvolutionLockSpec,
	GraphAOntology,
	OntologyRelationship,
	StagedCandidateSpec,
} from "../api/openapi";
import type {
	BipartiteCanvasEdge,
	BipartiteCanvasNode,
	BipartiteNodeKind,
} from "./BipartiteGraphCanvas";

export type VaultFilterChip =
	| "all"
	| "z0"
	| "z1"
	| "z2_hubs"
	| "z2_links"
	| "z2_sats"
	| "z3"
	| "z4"
	| "pii"
	| "wave";

const CARD_SHORT: Record<OntologyRelationship["cardinality"], string> = {
	ONE_TO_ONE: "1:1",
	ONE_TO_MANY: "1:N",
	MANY_TO_ONE: "N:1",
	MANY_TO_MANY: "M:N",
};

export function isConceptLockedInEvolution(
	concept: CanonicalBusinessConcept,
	locks: EvolutionLockSpec | null | undefined,
): boolean {
	if (!locks?.locked_hub_bks) return false;
	const keys = new Set(Object.keys(locks.locked_hub_bks));
	const slug = concept.concept_name.trim().toLowerCase().replace(/\s+/g, "_");
	return (
		keys.has(concept.concept_uri) ||
		keys.has(concept.concept_name) ||
		keys.has(slug) ||
		keys.has(`hub_${slug}`)
	);
}

function resolveConceptKind(
	c: CanonicalBusinessConcept,
	locks: EvolutionLockSpec | null | undefined,
	stagedByUri: Map<string, StagedCandidateSpec>,
): { kind: BipartiteNodeKind; badge: string } {
	if (isConceptLockedInEvolution(c, locks)) {
		return {
			kind: "concept_locked",
			badge: `LOCK-W${String(locks?.wave_number ?? 1).padStart(3, "0")}`,
		};
	}
	const staged = stagedByUri.get(c.concept_uri);
	if (
		staged?.status === "QUARANTINED" ||
		staged?.grounding_path === "PATH_B2"
	) {
		return { kind: "concept_b2", badge: "PATH_B2 · Quarantined" };
	}
	const hasExact = c.external_mappings?.some(
		(m) => m.mapping_type === "skos:exactMatch" && Number(m.confidence) >= 0.65,
	);
	if (staged?.grounding_path === "PATH_A1" || hasExact) {
		return { kind: "concept_default", badge: `${c.key_authority_tier}` };
	}
	const hasClose = c.external_mappings?.some(
		(m) => m.mapping_type === "skos:closeMatch" && Number(m.confidence) >= 0.65,
	);
	if (staged?.grounding_path === "PATH_A2" || hasClose) {
		return { kind: "concept_a2", badge: "PATH_A2 · Review" };
	}
	const hasB1 =
		Boolean(c.table_bindings?.length) ||
		Boolean(c.source_citations?.length) ||
		Boolean(c.mcp_evidence?.length);
	if (staged?.grounding_path === "PATH_B1" || hasB1) {
		return { kind: "concept_b1", badge: "PATH_B1 · Empirical" };
	}
	return { kind: "concept_default", badge: c.key_authority_tier };
}

export interface CanvasGraphResult {
	nodes: BipartiteCanvasNode[];
	edges: BipartiteCanvasEdge[];
	isFiltered: boolean;
	totalEntities: number;
}

export function buildOntologyCanvasGraph(
	ontology: GraphAOntology | null | undefined,
	opts: {
		stagedCandidates?: StagedCandidateSpec[];
		evolutionLocks?: EvolutionLockSpec | null;
		focusConceptUri?: string | null;
		showAll?: boolean;
		sourceFilter?: string | null;
	} = {},
): CanvasGraphResult {
	const concepts = ontology?.concepts ?? [];
	const rels = ontology?.relationships ?? [];
	const stagedByUri = new Map<string, StagedCandidateSpec>();
	for (const sc of opts.stagedCandidates ?? []) {
		stagedByUri.set(sc.concept_uri, sc);
	}

	let visibleConcepts = concepts;
	let isFiltered = false;
	if (opts.sourceFilter) {
		const sf = opts.sourceFilter;
		visibleConcepts = concepts.filter(
			(c) =>
				c.table_bindings?.some((tb) => tb.source_id === sf) ||
				c.source_citations?.some((cit) => cit.doc_id === sf),
		);
		isFiltered = visibleConcepts.length < concepts.length;
	}
	if (visibleConcepts.length > 40 && !opts.showAll) {
		const seed =
			opts.focusConceptUri &&
			visibleConcepts.some((c) => c.concept_uri === opts.focusConceptUri)
				? opts.focusConceptUri
				: visibleConcepts[0]?.concept_uri;
		const keep = new Set<string>(seed ? [seed] : []);
		for (const r of rels) {
			if (r.source_concept_uri === seed) keep.add(r.target_concept_uri);
			if (r.target_concept_uri === seed) keep.add(r.source_concept_uri);
		}
		visibleConcepts = visibleConcepts.filter((c) => keep.has(c.concept_uri));
		isFiltered = true;
	}

	const keptUris = new Set(visibleConcepts.map((c) => c.concept_uri));
	const nodes: BipartiteCanvasNode[] = visibleConcepts.map((c) => {
		const meta = resolveConceptKind(c, opts.evolutionLocks, stagedByUri);
		const attrCount = c.attributes?.length ?? 0;
		return {
			id: `c:${c.concept_uri}`,
			type: "bipartite",
			position: { x: 0, y: 0 },
			data: {
				label: c.concept_name,
				iri: c.concept_uri,
				kind: meta.kind,
				badge: meta.badge,
				subtitle: `${attrCount} attr${attrCount === 1 ? "" : "s"}`,
			},
		};
	});

	const edges: BipartiteCanvasEdge[] = [];
	for (const r of rels) {
		if (
			!keptUris.has(r.source_concept_uri) ||
			!keptUris.has(r.target_concept_uri)
		) {
			continue;
		}
		const relNodeId = `r:${r.relationship_uri}`;
		nodes.push({
			id: relNodeId,
			type: "bipartite",
			position: { x: 0, y: 0 },
			data: {
				label: `${r.relationship_name} (${CARD_SHORT[r.cardinality]})`,
				iri: r.relationship_uri,
				kind: "link",
				subtitle: r.target_link,
			},
		});
		edges.push(
			{
				id: `e:${r.relationship_uri}:src`,
				source: `c:${r.source_concept_uri}`,
				target: relNodeId,
			},
			{
				id: `e:${r.relationship_uri}:tgt`,
				source: relNodeId,
				target: `c:${r.target_concept_uri}`,
			},
		);
	}

	return {
		nodes,
		edges,
		isFiltered,
		totalEntities: concepts.length,
	};
}

function getWaveIncludedSet(ir: CertifiedVaultIR): Set<string> {
	const set = new Set<string>();
	if (ir.wave_number <= 1 || !ir.wave_delta) {
		for (const h of ir.hubs ?? []) set.add(h.hub_name);
		for (const l of ir.links ?? []) set.add(l.link_name);
		for (const s of ir.satellites ?? []) set.add(s.sat_name);
		for (const p of ir.pit_bridges ?? []) set.add(p.object_name);
		for (const m of ir.information_marts ?? []) set.add(m.view_name);
		return set;
	}
	for (const id of ir.wave_delta.added_entities ?? []) set.add(id);
	for (const id of ir.wave_delta.added_sat_extensions ?? []) set.add(id);
	for (const s of ir.satellites ?? []) {
		if (set.has(s.sat_name)) set.add(s.parent_name);
	}
	return set;
}

function mkNode(
	id: string,
	label: string,
	kind: BipartiteNodeKind,
	extra: Partial<BipartiteCanvasNode["data"]> = {},
): BipartiteCanvasNode {
	return {
		id,
		type: "bipartite",
		position: { x: 0, y: 0 },
		data: { label, kind, ...extra },
	};
}

export function buildVaultCanvasGraph(
	ir: CertifiedVaultIR | null | undefined,
	opts: {
		chip?: VaultFilterChip;
		collapseOver40?: boolean;
		expandedParentIds?: Set<string>;
	} = {},
): CanvasGraphResult {
	if (!ir) return { nodes: [], edges: [], isFiltered: false, totalEntities: 0 };
	const bronze = ir.bronze_tables ?? [];
	const refs = ir.reference_tables ?? [];
	const stg = ir.staging_views ?? [];
	const hubs = ir.hubs ?? [];
	const links = ir.links ?? [];
	const sats = ir.satellites ?? [];
	const pits = ir.pit_bridges ?? [];
	const marts = ir.information_marts ?? [];

	const totalEntities =
		bronze.length +
		refs.length +
		stg.length +
		hubs.length +
		links.length +
		sats.length +
		pits.length +
		marts.length;

	const chip = opts.chip ?? "all";
	const waveSet = chip === "wave" ? getWaveIncludedSet(ir) : null;
	const piiParents = new Set(
		sats.filter((s) => s.sat_kind === "PII").map((s) => s.parent_name),
	);
	const hubNames = new Set(hubs.map((h) => h.hub_name));
	const linkNames = new Set(links.map((l) => l.link_name));
	const satNames = new Set(sats.map((s) => s.sat_name));
	const pitNames = new Set(pits.map((p) => p.object_name));

	const nodes: BipartiteCanvasNode[] = [];
	const addedNodeIds = new Set<string>();
	const pushNode = (n: BipartiteCanvasNode) => {
		if (!addedNodeIds.has(n.id)) {
			addedNodeIds.add(n.id);
			nodes.push(n);
		}
	};

	const edges: BipartiteCanvasEdge[] = [];
	const seenEdges = new Set<string>();
	const pushEdge = (src: string, tgt: string, label?: string) => {
		if (!addedNodeIds.has(src) || !addedNodeIds.has(tgt) || src === tgt) return;
		const id = `${src}->${tgt}`;
		if (seenEdges.has(id)) return;
		seenEdges.add(id);
		edges.push({ id, source: src, target: tgt, label });
	};

	const parentNodeId = (name: string) =>
		hubNames.has(name) ? `h:${name}` : linkNames.has(name) ? `l:${name}` : "";

	if (chip === "all" || chip === "z0") {
		for (const b of bronze) {
			pushNode(
				mkNode(`b:${b.table_name}`, b.table_name, "bronze", {
					zone: "Z0",
					badge: "Z0 Bronze",
				}),
			);
		}
		for (const r of refs) {
			pushNode(
				mkNode(`r:${r.ref_name}`, r.ref_name, "ref", {
					zone: "Z0",
					badge: "Z0 Ref",
				}),
			);
		}
	}
	if (chip === "all" || chip === "z1") {
		for (const s of stg) {
			pushNode(
				mkNode(`stg:${s.view_name}`, s.view_name, "staging", {
					zone: "Z1",
					badge: "Z1 Stg",
				}),
			);
		}
	}
	if (
		chip === "all" ||
		chip === "z2_hubs" ||
		chip === "pii" ||
		chip === "wave"
	) {
		for (const h of hubs) {
			if (
				(chip === "pii" && !piiParents.has(h.hub_name)) ||
				(waveSet && !waveSet.has(h.hub_name))
			)
				continue;
			pushNode(
				mkNode(
					`h:${h.hub_name}`,
					h.hub_name,
					h.is_planned_concept ? "planned" : "hub",
					{
						zone: "Z2",
						badge: h.is_planned_concept ? "Planned Stub (No DDL)" : h.key_tier,
					},
				),
			);
		}
	}
	if (
		chip === "all" ||
		chip === "z2_links" ||
		chip === "z3" ||
		chip === "pii" ||
		chip === "wave"
	) {
		for (const l of links) {
			if (
				(chip === "z3" && l.link_kind !== "SAME_AS") ||
				(chip === "pii" && !piiParents.has(l.link_name)) ||
				(waveSet && !waveSet.has(l.link_name))
			)
				continue;
			const isSal = l.link_kind === "SAME_AS";
			pushNode(
				mkNode(`l:${l.link_name}`, l.link_name, isSal ? "sal" : "link", {
					zone: isSal ? "Z3" : "Z2",
					badge: isSal ? "Z3 SAL" : l.link_kind,
				}),
			);
		}
	}

	const collapseSats =
		Boolean(opts.collapseOver40 ?? totalEntities > 40) &&
		chip === "all" &&
		sats.length > 0;
	const satsByParent = new Map<string, typeof sats>();
	for (const s of sats) {
		if (
			(chip === "pii" && s.sat_kind !== "PII") ||
			(waveSet && !waveSet.has(s.sat_name))
		)
			continue;
		const arr = satsByParent.get(s.parent_name) ?? [];
		arr.push(s);
		satsByParent.set(s.parent_name, arr);
	}

	if (
		chip === "all" ||
		chip === "z2_sats" ||
		chip === "pii" ||
		chip === "wave"
	) {
		for (const [parent, list] of satsByParent.entries()) {
			const expanded = opts.expandedParentIds?.has(parent);
			if (collapseSats && !expanded && list.length > 1) {
				const sumId = `sats:${parent}`;
				pushNode(
					mkNode(sumId, `+${list.length} Sats`, "sat", {
						zone: "Z2",
						parentHubId: parent,
						collapsedCount: list.length,
						subtitle: `Click to expand ${parent} satellites`,
					}),
				);
				const pId = parentNodeId(parent);
				if (pId) pushEdge(pId, sumId);
				continue;
			}
			for (const s of list) {
				const sKind: BipartiteNodeKind =
					s.sat_kind === "PII"
						? "sat_pii"
						: s.sat_kind === "AI_PROVENANCE_SATELLITE"
							? "sat_ai"
							: "sat";
				pushNode(
					mkNode(`s:${s.sat_name}`, s.sat_name, sKind, {
						zone: "Z2",
						badge:
							s.sat_kind === "AI_PROVENANCE_SATELLITE"
								? "AI_PROVENANCE_SATELLITE"
								: s.sat_kind === "PII"
									? "PII Split"
									: s.sat_kind,
					}),
				);
				const pId = parentNodeId(s.parent_name);
				if (pId) pushEdge(pId, `s:${s.sat_name}`);
			}
		}
	}

	if (chip === "all" || chip === "z3" || chip === "wave") {
		for (const p of pits) {
			if (waveSet && !waveSet.has(p.object_name) && ir.wave_number <= 1)
				continue;
			pushNode(
				mkNode(`p:${p.object_name}`, p.object_name, "pit", {
					zone: "Z3",
					badge: `Z3 ${p.object_kind}`,
				}),
			);
			pushEdge(`h:${p.root_hub}`, `p:${p.object_name}`);
			for (const t of p.target_satellites_or_links) {
				if (satNames.has(t)) pushEdge(`s:${t}`, `p:${p.object_name}`);
				else if (linkNames.has(t)) pushEdge(`l:${t}`, `p:${p.object_name}`);
			}
		}
	}

	if (chip === "all" || chip === "z4" || chip === "wave") {
		for (const m of marts) {
			pushNode(
				mkNode(`m:${m.view_name}`, m.view_name, "mart", {
					zone: "Z4",
					badge: "Z4 Mart",
				}),
			);
			const resolveId = (name: string) =>
				pitNames.has(name)
					? `p:${name}`
					: linkNames.has(name)
						? `l:${name}`
						: hubNames.has(name)
							? `h:${name}`
							: satNames.has(name)
								? `s:${name}`
								: "";
			const rootId = resolveId(m.root_entity);
			if (rootId) pushEdge(rootId, `m:${m.view_name}`);
			for (const je of m.joined_entities) {
				const jId = resolveId(je);
				if (jId) pushEdge(jId, `m:${m.view_name}`);
			}
		}
	}

	for (const s of stg) {
		for (const b of bronze) {
			if (
				b.source_id === s.source_id &&
				b.source_table_id === s.source_table_id
			)
				pushEdge(`b:${b.table_name}`, `stg:${s.view_name}`);
		}
		for (const h of hubs) {
			if (
				h.source_bindings?.some(
					(sb) =>
						sb.source_id === s.source_id && sb.table_id === s.source_table_id,
				)
			)
				pushEdge(`stg:${s.view_name}`, `h:${h.hub_name}`);
		}
	}

	for (const l of links) {
		for (const p of l.participants)
			pushEdge(`h:${p.hub_name}`, `l:${l.link_name}`, p.role_name);
	}

	return {
		nodes,
		edges,
		isFiltered: collapseSats || chip !== "all",
		totalEntities,
	};
}
