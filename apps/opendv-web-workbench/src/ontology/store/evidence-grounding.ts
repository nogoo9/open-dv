/**
 * Path B Empirical Evidence Verification & Wire-Contract Sanitization
 * (apps/opendv-web-workbench/src/ontology/store/evidence-grounding.ts)
 */

import type { ConceptNode, EvidenceRef } from "./types";

const PATH_B_GROUNDING_ORIGINS = new Set([
	"STRUCTURED_SCHEMA",
	"DOCUMENT_OBSERVED",
]);

const ALLOWED_EVIDENCE_REF_KEYS = new Set([
	"evidence_type",
	"reference_uri",
	"snippet",
	"confidence",
	"doc_sha256",
	"char_start",
	"char_end",
	"bronze_table",
	"column_name",
	"source_label",
	"chunk_id",
	"page_number",
	"table_id",
	"fibo_uri",
]);

function hasText(val: unknown): boolean {
	return typeof val === "string" && val.trim().length > 0;
}

function isVerifiableEvidenceRef(
	ref: EvidenceRef | Record<string, unknown>,
	origin: string,
): boolean {
	const evType = hasText(ref.evidence_type)
		? String(ref.evidence_type).trim()
		: "";
	if (!evType) return false;
	if (origin === "STRUCTURED_SCHEMA") {
		return (
			hasText(ref.bronze_table) ||
			hasText(ref.column_name) ||
			hasText(ref.table_id) ||
			hasText(ref.reference_uri) ||
			hasText(ref.snippet)
		);
	}
	return (
		hasText(ref.chunk_id) ||
		hasText(ref.doc_sha256) ||
		hasText(ref.source_label) ||
		hasText(ref.reference_uri) ||
		hasText(ref.snippet) ||
		(typeof ref.char_start === "number" && Number.isFinite(ref.char_start))
	);
}

export function hasVerifiableEmpiricalEvidence(node: ConceptNode): boolean {
	const refs = node.evidence_refs || [];
	if (refs.length === 0) return false;
	const origin = hasText(node.grounding_origin)
		? String(node.grounding_origin).trim()
		: null;
	if (origin !== null && PATH_B_GROUNDING_ORIGINS.has(origin)) {
		return refs.some((r) => isVerifiableEvidenceRef(r, origin));
	}
	if (origin !== null && origin !== "FIBO_GROUNDED") {
		return false;
	}
	return refs.some((r) => {
		const evType = hasText(r.evidence_type)
			? String(r.evidence_type).trim()
			: "";
		if (evType === "column_profile") {
			return isVerifiableEvidenceRef(r, "STRUCTURED_SCHEMA");
		}
		if (evType === "document_span") {
			return isVerifiableEvidenceRef(r, "DOCUMENT_OBSERVED");
		}
		return false;
	});
}

export function sanitizeEvidenceRefs(
	rawRefs: unknown,
): EvidenceRef[] | undefined {
	if (!Array.isArray(rawRefs) || rawRefs.length === 0) return undefined;
	const cleaned: EvidenceRef[] = [];
	for (const item of rawRefs) {
		if (!item || typeof item !== "object") continue;
		const src = item as Record<string, unknown>;
		if (!hasText(src.evidence_type)) continue;
		const out: Record<string, unknown> = {
			evidence_type: String(src.evidence_type).trim(),
		};
		for (const key of ALLOWED_EVIDENCE_REF_KEYS) {
			if (key === "evidence_type") continue;
			const val = src[key];
			if (val !== undefined && val !== null) {
				out[key] = val;
			}
		}
		if (!out.bronze_table && hasText(src.table_name)) {
			out.bronze_table = String(src.table_name).trim();
		}
		if (
			out.char_start === undefined &&
			typeof src.start_char === "number" &&
			Number.isFinite(src.start_char)
		) {
			out.char_start = src.start_char;
		}
		if (
			out.char_end === undefined &&
			typeof src.end_char === "number" &&
			Number.isFinite(src.end_char)
		) {
			out.char_end = src.end_char;
		}
		if (!out.snippet && hasText(src.text_snippet)) {
			out.snippet = String(src.text_snippet);
		} else if (!out.snippet && hasText(src.excerpt)) {
			out.snippet = String(src.excerpt);
		}
		cleaned.push(out as unknown as EvidenceRef);
	}
	return cleaned.length > 0 ? cleaned : undefined;
}
