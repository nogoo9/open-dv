/**
 * Shared identifier normalization helpers for Ontology Studio concepts and relationships.
 */

import {
	CONCEPT_URN_PREFIX,
	RELATIONSHIP_URN_PREFIX,
	TARGET_HUB_PREFIX,
	TARGET_LINK_PREFIX,
} from "../constants";

let draftOpSequence = 0;

export function createDraftOpId(prefix = "op-draft"): string {
	const rand =
		typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
			? crypto.randomUUID().replace(/-/g, "").slice(0, 8)
			: `${Date.now().toString(36)}${(draftOpSequence++).toString(36)}`;
	return `${prefix}-${rand}`;
}

export function extractIriTerminalLabel(raw: unknown): string {
	const text = typeof raw === "string" ? raw.trim() : "";
	if (!text) return "";
	return text.split(/[:/#]/).filter(Boolean).pop() || text;
}

export function slugifyIdentifier(raw: string, fallback: string): string {
	const cleaned = raw
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "_")
		.replace(/^_+|_+$/g, "");
	return cleaned || fallback;
}

export function extractConceptTerminalSlug(
	conceptUri: string,
	fallback: string,
): string {
	const tail = conceptUri.split(/[:/#]/).filter(Boolean).pop() || "";
	return slugifyIdentifier(tail, fallback);
}

export function buildUniqueConceptIdentifiers(
	rawName: string,
	nodes: ReadonlyArray<{
		concept_uri: string;
		target_hub?: string | null | undefined;
	}>,
	committedNodes: ReadonlyArray<{
		concept_uri: string;
		target_hub?: string | null | undefined;
	}>,
): { slug: string; conceptUri: string; targetHub: string } {
	const baseSlug = slugifyIdentifier(rawName, "concept");
	const existingUris = new Set<string>();
	const existingHubs = new Set<string>();
	for (const n of [...nodes, ...committedNodes]) {
		if (n.concept_uri) existingUris.add(n.concept_uri.toLowerCase());
		if (n.target_hub) existingHubs.add(n.target_hub.toLowerCase());
	}

	let candidate = baseSlug;
	let suffix = 2;
	while (
		existingUris.has(`${CONCEPT_URN_PREFIX}${candidate}`.toLowerCase()) ||
		existingHubs.has(`${TARGET_HUB_PREFIX}${candidate}`.toLowerCase())
	) {
		candidate = `${baseSlug}_${suffix}`;
		suffix += 1;
	}
	return {
		slug: candidate,
		conceptUri: `${CONCEPT_URN_PREFIX}${candidate}`,
		targetHub: `${TARGET_HUB_PREFIX}${candidate}`,
	};
}

export function buildUniqueRelationshipIdentifiers(
	rawVerb: string,
	sourceConceptUri: string,
	targetConceptUri: string,
	edges: ReadonlyArray<{
		relationship_uri: string;
		target_link?: string | null | undefined;
	}>,
	committedEdges: ReadonlyArray<{
		relationship_uri: string;
		target_link?: string | null | undefined;
	}>,
): { slug: string; relationshipUri: string; targetLink: string } {
	const srcSlug = extractConceptTerminalSlug(sourceConceptUri, "src");
	const verbSlug = slugifyIdentifier(rawVerb, "relates_to");
	const tgtSlug = extractConceptTerminalSlug(targetConceptUri, "tgt");
	const baseSlug = `${srcSlug}_${verbSlug}_${tgtSlug}`;

	const existingUris = new Set<string>();
	const existingLinks = new Set<string>();
	for (const e of [...edges, ...committedEdges]) {
		if (e.relationship_uri) existingUris.add(e.relationship_uri.toLowerCase());
		if (e.target_link) existingLinks.add(e.target_link.toLowerCase());
	}

	let candidate = baseSlug;
	let suffix = 2;
	while (
		existingUris.has(`${RELATIONSHIP_URN_PREFIX}${candidate}`.toLowerCase()) ||
		existingLinks.has(`${TARGET_LINK_PREFIX}${candidate}`.toLowerCase())
	) {
		candidate = `${baseSlug}_${suffix}`;
		suffix += 1;
	}
	return {
		slug: candidate,
		relationshipUri: `${RELATIONSHIP_URN_PREFIX}${candidate}`,
		targetLink: `${TARGET_LINK_PREFIX}${candidate}`,
	};
}
