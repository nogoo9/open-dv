/**
 * Types, constants, and normalization utilities for remote execution.
 */

import { createHash, randomUUID } from "node:crypto";
import { dumpsJcs } from "../jcs";

export const MIN_RATIONALE_LENGTH = 10;

export interface RemoteExecutorConfig {
	baseUrl?: string | undefined;
	role?: string | undefined;
	explicitRole?: boolean | undefined;
	fetchImpl?: typeof fetch | undefined;
	writer?: ((msg: string) => void) | undefined;
	errWriter?: ((msg: string) => void) | undefined;
	pollIntervalMs?: number | undefined;
}

export const ADMIN_ROLE_NAMES = new Set([
	"ADMIN",
	"DATA_STEWARD",
	"RESTRICTED_PII_CUSTODIAN",
]);

export const DESTRUCTIVE_OP_TYPES = new Set([
	"remove_concept",
	"remove_relationship",
	"remove_attribute",
	"update_concept_urn",
	"change_relationship_cardinality",
	"reparent_concept",
	"retype_attribute",
	"split_concept",
	"merge_concepts",
]);

export function isDestructiveDeltaOperation(
	op: Record<string, unknown>,
	newlyAddedConceptUris?: ReadonlySet<string>,
	baseConceptsByUri?: ReadonlyMap<string, Record<string, unknown>>,
): boolean {
	const opType = normalizeOpType(op.op_type ?? op.op);
	if (DESTRUCTIVE_OP_TYPES.has(opType)) {
		return true;
	}
	if (opType === "add_attribute" && op.is_optional === false) {
		const conceptUri = String(op.concept_uri ?? "").trim();
		if (!conceptUri || !newlyAddedConceptUris?.has(conceptUri)) {
			return true;
		}
	}
	if (opType === "set_parent_concept") {
		const conceptUri = String(op.concept_uri ?? "").trim();
		const existing = conceptUri
			? baseConceptsByUri?.get(conceptUri)
			: undefined;
		if (op.parent_concept_uri == null) {
			if (!baseConceptsByUri || existing?.parent_concept_uri != null) {
				return true;
			}
		} else if (
			existing?.parent_concept_uri != null &&
			existing.parent_concept_uri !== op.parent_concept_uri
		) {
			return true;
		}
	}
	if (opType === "set_concept_elm_archetype") {
		const conceptUri = String(op.concept_uri ?? "").trim();
		const existing = conceptUri
			? baseConceptsByUri?.get(conceptUri)
			: undefined;
		if (
			existing?.elm_archetype != null &&
			existing.elm_archetype !== op.elm_archetype
		) {
			return true;
		}
	}
	return false;
}

export function normalizeOpType(rawType: unknown): string {
	const str = String(rawType ?? "").trim();
	if (!str) return "";
	if (str === "AddConceptOp") return "add_concept";
	if (str.endsWith("Op") && str.length > 2) {
		const base = str.slice(0, -2);
		return base.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
	}
	return str;
}

export function normalizeDeltaOperations(
	rawOps: unknown,
): Array<Record<string, unknown>> {
	if (!Array.isArray(rawOps)) {
		return [];
	}
	return rawOps.map((item, idx) => {
		if (typeof item !== "object" || item === null || Array.isArray(item)) {
			return {} as Record<string, unknown>;
		}
		const opDict: Record<string, unknown> = {
			...(item as Record<string, unknown>),
		};
		const resolvedType = normalizeOpType(opDict.op_type ?? opDict.op);
		delete opDict.op;
		if (resolvedType) {
			opDict.op_type = resolvedType;
		}
		if (!opDict.op_id) {
			opDict.op_id = `op-${idx + 1}-${randomUUID().replace(/-/g, "").slice(0, 8)}`;
		}
		if (
			opDict.op_type === "add_concept" &&
			typeof opDict.concept === "object" &&
			opDict.concept !== null &&
			!Array.isArray(opDict.concept)
		) {
			const conceptFields = {
				...(opDict.concept as Record<string, unknown>),
			};
			delete opDict.concept;
			return {
				...conceptFields,
				...opDict,
				op_type: "add_concept",
			};
		}
		return opDict;
	});
}

export function computeCanonicalAnswersSha256(items: unknown[]): string {
	if (!items || items.length === 0) {
		return createHash("sha256").update(dumpsJcs([]), "utf-8").digest("hex");
	}
	const cleaned = items
		.filter(
			(it): it is Record<string, unknown> =>
				typeof it === "object" && it !== null && !Array.isArray(it),
		)
		.map((it) => {
			const row: Record<string, unknown> = {};
			for (const [k, v] of Object.entries(it)) {
				if (v !== null && v !== undefined) {
					row[k] = v;
				}
			}
			return row;
		})
		.sort((a, b) =>
			String(a.question_id ?? "").localeCompare(String(b.question_id ?? "")),
		);
	return createHash("sha256").update(dumpsJcs(cleaned), "utf-8").digest("hex");
}

export function normalizeStringArrayOption(val: unknown): string[] {
	if (val === undefined || val === null) {
		return [];
	}
	const rawItems = Array.isArray(val) ? val : [val];
	const out: string[] = [];
	for (const item of rawItems) {
		for (const part of String(item).split(",")) {
			const trimmed = part.trim();
			if (trimmed) out.push(trimmed);
		}
	}
	return out;
}
