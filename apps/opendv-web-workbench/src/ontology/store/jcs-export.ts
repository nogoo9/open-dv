/**
 * RFC 8785 JCS canonicalization and Graph A YAML serialization.
 * (apps/opendv-web-workbench/src/ontology/store/jcs-export.ts)
 */

import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "./types";

export function dumpsJcs(
	obj: unknown,
	options: { excludeNone?: boolean } = {},
): string {
	const excludeNone = Boolean(options.excludeNone);

	if (obj === null || obj === undefined) {
		return "null";
	}
	if (typeof obj === "boolean") {
		return obj ? "true" : "false";
	}
	if (typeof obj === "number") {
		if (!Number.isFinite(obj)) {
			throw new Error("Non-finite number is not representable in RFC 8785");
		}
		if (Object.is(obj, -0)) return "0";
		return obj.toString();
	}
	if (typeof obj === "string") {
		return JSON.stringify(obj);
	}
	if (Array.isArray(obj)) {
		const items = obj.map((elem) => dumpsJcs(elem, options));
		return `[${items.join(",")}]`;
	}
	if (typeof obj === "object") {
		const record = obj as Record<string, unknown>;
		const sortedKeys = Object.keys(record).sort((a, b) => {
			const len = Math.min(a.length, b.length);
			for (let i = 0; i < len; i++) {
				const ca = a.charCodeAt(i);
				const cb = b.charCodeAt(i);
				if (ca !== cb) return ca - cb;
			}
			return a.length - b.length;
		});

		const pairs: string[] = [];
		for (const k of sortedKeys) {
			const v = record[k];
			if (excludeNone && (v === null || v === undefined)) {
				continue;
			}
			pairs.push(`${JSON.stringify(k)}:${dumpsJcs(v, options)}`);
		}
		return `{${pairs.join(",")}}`;
	}
	throw new Error(`Unsupported type for JCS canonicalization: ${typeof obj}`);
}

export function canonicalizeGraphADict(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	session: StudioSessionSummary,
): Record<string, unknown> {
	const isEmptyExtValue = (v: unknown): boolean => {
		if (v === null || v === undefined) return true;
		if (typeof v === "string") return v.length === 0;
		if (Array.isArray(v)) return v.length === 0;
		if (typeof v === "object") {
			return Object.keys(v as Record<string, unknown>).length === 0;
		}
		return false;
	};

	const ENUM_EXT = new Set([
		"enumeration_name",
		"description",
		"code_descriptions",
		"deprecated",
		"metadata",
	]);

	const sortedConcepts = [...nodes]
		.sort((a, b) =>
			a.concept_uri < b.concept_uri
				? -1
				: a.concept_uri > b.concept_uri
					? 1
					: 0,
		)
		.map((n) => {
			const c: Record<string, unknown> = {
				concept_name: n.concept_name,
				concept_uri: n.concept_uri,
				definition: n.definition,
				elm_archetype: n.elm_archetype,
			};
			if (n.description !== null && n.description !== undefined) {
				c.description = n.description;
			}
			if (n.target_hub !== null && n.target_hub !== undefined) {
				c.target_hub = n.target_hub;
			}
			if (
				n.governance_constraints !== null &&
				n.governance_constraints !== undefined
			) {
				c.governance_constraints = n.governance_constraints;
			}
			if (
				n.key_authority_tiers !== null &&
				n.key_authority_tiers !== undefined
			) {
				c.key_authority_tiers = n.key_authority_tiers;
			}

			if (Array.isArray(n.attributes) && n.attributes.length > 0) {
				c.attributes = n.attributes.map((attr) => {
					const attrRecord: Record<string, unknown> = {
						attribute_name: attr.attribute_name,
						data_type: attr.data_type,
					};
					if (attr.is_optional !== null && attr.is_optional !== undefined) {
						attrRecord.is_optional = attr.is_optional;
					}
					if (
						typeof attr.description === "string" &&
						attr.description.length > 0
					) {
						attrRecord.description = attr.description;
					}
					if (
						Array.isArray(attr.example_values) &&
						attr.example_values.length > 0
					) {
						attrRecord.example_values = attr.example_values;
					}
					if (attr.deprecated !== null && attr.deprecated !== undefined) {
						attrRecord.deprecated = attr.deprecated;
					}
					if (attr.metadata && Object.keys(attr.metadata).length > 0) {
						attrRecord.metadata = attr.metadata;
					}
					if (
						Array.isArray(attr.source_hints) &&
						attr.source_hints.length > 0
					) {
						attrRecord.source_hints = attr.source_hints;
					}
					return attrRecord;
				});
			}
			if (
				Array.isArray(n.external_mappings) &&
				n.external_mappings.length > 0
			) {
				const sortedMappings = [...n.external_mappings].sort((ma, mb) => {
					const ta = ma.target_uri ?? "";
					const tb = mb.target_uri ?? "";
					if (ta !== tb) return ta < tb ? -1 : 1;
					const pa = ma.predicate ?? "";
					const pb = mb.predicate ?? "";
					if (pa !== pb) return pa < pb ? -1 : 1;
					const sa = ma.scheme ?? "";
					const sb = mb.scheme ?? "";
					return sa < sb ? -1 : sa > sb ? 1 : 0;
				});
				c.external_mappings = sortedMappings.map((m) => {
					const numericConf =
						typeof m.confidence === "number"
							? m.confidence
							: typeof m.mapping_confidence === "number"
								? m.mapping_confidence
								: undefined;
					return {
						scheme: m.scheme,
						target_uri: m.target_uri,
						...(numericConf !== undefined ? { confidence: numericConf } : {}),
						...(m.predicate !== null && m.predicate !== undefined
							? { predicate: m.predicate }
							: {}),
						...(m.rationale !== null && m.rationale !== undefined
							? { rationale: m.rationale }
							: {}),
						...(m.pinned !== null && m.pinned !== undefined
							? { pinned: m.pinned }
							: {}),
						...(m.provenance !== null && m.provenance !== undefined
							? { provenance: m.provenance }
							: {}),
						...(m.reference_pack_id !== null &&
						m.reference_pack_id !== undefined
							? { reference_pack_id: m.reference_pack_id }
							: {}),
						...(m.reference_pack_sha256 !== null &&
						m.reference_pack_sha256 !== undefined
							? { reference_pack_sha256: m.reference_pack_sha256 }
							: {}),
					};
				});
			}
			if (Array.isArray(n.synonyms) && n.synonyms.length > 0) {
				c.synonyms = n.synonyms;
			}
			if (
				typeof n.parent_concept_uri === "string" &&
				n.parent_concept_uri.length > 0
			) {
				c.parent_concept_uri = n.parent_concept_uri;
			}
			if (n.deprecated !== null && n.deprecated !== undefined) {
				c.deprecated = n.deprecated;
			}
			if (n.metadata && Object.keys(n.metadata).length > 0) {
				c.metadata = n.metadata;
			}
			if (Array.isArray(n.source_hints) && n.source_hints.length > 0) {
				c.source_hints = n.source_hints;
			}
			return c;
		});

	const sortedRelationships = [...edges]
		.sort((a, b) =>
			a.relationship_uri < b.relationship_uri
				? -1
				: a.relationship_uri > b.relationship_uri
					? 1
					: 0,
		)
		.map((e) => {
			const endpoints =
				e.endpoints && e.endpoints.length >= 2
					? e.endpoints
					: [e.source_concept_uri, e.target_concept_uri];
			const r: Record<string, unknown> = {
				endpoints,
				relationship_name: e.relationship_name,
				relationship_uri: e.relationship_uri,
				target_link: e.target_link,
			};
			if (e.grain_semantics !== null && e.grain_semantics !== undefined) {
				r.grain_semantics = e.grain_semantics;
			}
			if (
				e.yagni_cost_guard_policy !== null &&
				e.yagni_cost_guard_policy !== undefined
			) {
				r.yagni_cost_guard_policy = e.yagni_cost_guard_policy;
			}
			if (typeof e.cardinality === "string" && e.cardinality.length > 0) {
				r.cardinality = e.cardinality;
			}
			if (typeof e.description === "string" && e.description.length > 0) {
				r.description = e.description;
			}
			if (e.deprecated !== null && e.deprecated !== undefined) {
				r.deprecated = e.deprecated;
			}
			if (e.metadata && Object.keys(e.metadata).length > 0) {
				r.metadata = e.metadata;
			}
			if (Array.isArray(e.source_hints) && e.source_hints.length > 0) {
				r.source_hints = e.source_hints;
			}
			return r;
		});

	const normalizedReferenceEnums = [...(session.reference_enumerations ?? [])]
		.map((rawEnum) => {
			if (!rawEnum || typeof rawEnum !== "object") return rawEnum;
			const out: Record<string, unknown> = {};
			for (const [k, v] of Object.entries(rawEnum)) {
				if (v === null || v === undefined) continue;
				if (ENUM_EXT.has(k) && isEmptyExtValue(v)) continue;
				out[k] = v;
			}
			return out;
		})
		.sort((a, b) => {
			const ua =
				a && typeof a === "object"
					? String((a as Record<string, unknown>).enum_uri ?? "")
					: "";
			const ub =
				b && typeof b === "object"
					? String((b as Record<string, unknown>).enum_uri ?? "")
					: "";
			return ua < ub ? -1 : ua > ub ? 1 : 0;
		});

	const sortedDecisionRefs = [...(session.decision_refs ?? [])].sort((a, b) =>
		a < b ? -1 : a > b ? 1 : 0,
	);
	const sortedStandardsRefs = [...(session.standards_references ?? [])].sort(
		(a, b) => {
			const sa = typeof a === "string" ? a : JSON.stringify(a);
			const sb = typeof b === "string" ? b : JSON.stringify(b);
			return sa < sb ? -1 : sa > sb ? 1 : 0;
		},
	);

	const root: Record<string, unknown> = {
		canonical_business_concepts: sortedConcepts,
		canonical_relationships: sortedRelationships,
		decision_refs: sortedDecisionRefs,
		...(session.domain_namespace !== null &&
		session.domain_namespace !== undefined
			? { domain_namespace: session.domain_namespace }
			: {}),
		...(session.enterprise_owner !== null &&
		session.enterprise_owner !== undefined
			? { enterprise_owner: session.enterprise_owner }
			: {}),
		...(session.active_version !== null && session.active_version !== undefined
			? { ontology_version: session.active_version }
			: {}),
		reference_enumerations: normalizedReferenceEnums,
		standards_references: sortedStandardsRefs,
	};
	if (session.parent_sha256 && /^[a-f0-9]{64}$/i.test(session.parent_sha256)) {
		root.parent_sha256 = session.parent_sha256.toLowerCase();
	}
	if (
		typeof session.description === "string" &&
		session.description.length > 0
	) {
		root.description = session.description;
	}
	if (session.metadata && Object.keys(session.metadata).length > 0) {
		root.metadata = session.metadata;
	}
	return root;
}

export function serializeGraphAYaml(root: Record<string, unknown>): string {
	const formatKey = (key: string): string =>
		/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) ? key : JSON.stringify(key);

	const isInlineYamlValue = (val: unknown): boolean => {
		if (val === null || val === undefined) return true;
		if (
			typeof val === "string" ||
			typeof val === "number" ||
			typeof val === "boolean"
		) {
			return true;
		}
		if (Array.isArray(val)) return val.length === 0;
		if (typeof val === "object") {
			return (
				Object.entries(val as Record<string, unknown>).filter(
					([, v]) => v !== undefined,
				).length === 0
			);
		}
		return true;
	};

	const formatInlineYamlValue = (val: unknown): string => {
		if (val === null || val === undefined) return "null";
		if (typeof val === "string") return JSON.stringify(val);
		if (typeof val === "number") {
			return Number.isFinite(val) ? String(val) : "null";
		}
		if (typeof val === "boolean") return val ? "true" : "false";
		if (Array.isArray(val)) return "[]";
		if (typeof val === "object") return "{}";
		return JSON.stringify(String(val));
	};

	const emitNode = (val: unknown, indent: number, out: string[]): void => {
		const pad = " ".repeat(indent);
		if (Array.isArray(val)) {
			for (const item of val) {
				if (isInlineYamlValue(item)) {
					out.push(`${pad}- ${formatInlineYamlValue(item)}`);
				} else if (Array.isArray(item)) {
					out.push(`${pad}-`);
					emitNode(item, indent + 2, out);
				} else if (item && typeof item === "object") {
					const entries = Object.entries(
						item as Record<string, unknown>,
					).filter(([, v]) => v !== undefined);
					const [firstEntry, ...restEntries] = entries;
					if (!firstEntry) {
						out.push(`${pad}- {}`);
						continue;
					}
					const [firstKey, firstVal] = firstEntry;
					if (isInlineYamlValue(firstVal)) {
						out.push(
							`${pad}- ${formatKey(firstKey)}: ${formatInlineYamlValue(firstVal)}`,
						);
					} else {
						out.push(`${pad}- ${formatKey(firstKey)}:`);
						emitNode(firstVal, indent + 4, out);
					}
					for (const [k, v] of restEntries) {
						if (isInlineYamlValue(v)) {
							out.push(`${pad}  ${formatKey(k)}: ${formatInlineYamlValue(v)}`);
						} else {
							out.push(`${pad}  ${formatKey(k)}:`);
							emitNode(v, indent + 4, out);
						}
					}
				}
			}
			return;
		}
		if (val && typeof val === "object") {
			const entries = Object.entries(val as Record<string, unknown>).filter(
				([, v]) => v !== undefined,
			);
			for (const [k, v] of entries) {
				if (isInlineYamlValue(v)) {
					out.push(`${pad}${formatKey(k)}: ${formatInlineYamlValue(v)}`);
				} else {
					out.push(`${pad}${formatKey(k)}:`);
					emitNode(v, indent + 2, out);
				}
			}
		}
	};

	const preferredTopKeys = [
		"ontology_version",
		"domain_namespace",
		"enterprise_owner",
		"description",
		"parent_sha256",
		"decision_refs",
		"standards_references",
		"canonical_business_concepts",
		"canonical_relationships",
		"reference_enumerations",
		"metadata",
	];
	const orderedRoot: Record<string, unknown> = {};
	for (const k of preferredTopKeys) {
		if (k in root && root[k] !== undefined) {
			orderedRoot[k] = root[k];
		}
	}
	for (const [k, v] of Object.entries(root)) {
		if (!(k in orderedRoot) && v !== undefined) {
			orderedRoot[k] = v;
		}
	}
	const lines: string[] = [];
	emitNode(orderedRoot, 0, lines);
	return lines.join("\n");
}
