/**
 * Algorithm A.5 6-Stage Length-Prefixed Uppercase SHA-256 Hashing & Semantic Hash Pill Formatting
 * (`docs/01` §4.2, `docs/05` §3.5, `docs/06` §2.2, `docs/10` §2.3).
 */

import type { HashPillKind } from "../types/common";
import type { SemanticHashPillDTO } from "../types/observability-event";

export interface Dv2HashOptions {
	isBusinessKey?: boolean | undefined;
	bkccPrefix?: string | null | undefined;
	sortAlphabetically?: boolean | undefined;
}

/** 64-character uppercase hex Zero Key for unknown/missing business keys (`-1`). */
export const ZERO_KEY_HEX = "0".repeat(64);

/** 64-character uppercase hex Zero Key for unresolved error business keys (`-2`). */
export const ZERO_KEY_ERROR_HEX = "F".repeat(64);

/** Canonical positional NULL sentinel token per Algorithm A.5 (`docs/01` §4.2 & `docs/05` §3.5). */
export const NULL_TOKEN = "-1:^^";

const UTF8_ENCODER = new TextEncoder();

/**
 * Construct the canonical Algorithm A.5 length-prefixed pre-hash string (`<byte_len>:<val>` joined by `||`).
 * - Uses `'-1:^^'` as the canonical positional null sentinel.
 * - Uppercases Business Key tokens (`isBusinessKey: true`, default) and preserves case for
 *   Satellite `hash_diff` payload digests (`isBusinessKey: false`).
 * - Prepends `<byte_len>:<BKCC>` when `bkccPrefix` is supplied on a Business Key.
 */
export function computeDv2PrehashString(
	tokens: readonly (string | null | undefined)[],
	options: Dv2HashOptions = {},
): string {
	const isBusinessKey = options.isBusinessKey ?? true;
	const sourceTokens = options.sortAlphabetically
		? [...tokens].sort((a, b) => (a ?? "").localeCompare(b ?? ""))
		: tokens;

	const normalized = sourceTokens.map((token) => {
		if (token === null || token === undefined) {
			return "";
		}
		const trimmed = token.trim();
		return isBusinessKey ? trimmed.toUpperCase() : trimmed;
	});

	const parts: string[] = [];
	if (isBusinessKey && options.bkccPrefix) {
		const bkcc = options.bkccPrefix.trim().toUpperCase();
		if (bkcc.length > 0) {
			const bkccBytes = UTF8_ENCODER.encode(bkcc).length;
			parts.push(`${bkccBytes}:${bkcc}`);
		}
	}

	for (const cleaned of normalized) {
		if (cleaned.length === 0) {
			parts.push(NULL_TOKEN);
		} else {
			const byteLen = UTF8_ENCODER.encode(cleaned).length;
			parts.push(`${byteLen}:${cleaned}`);
		}
	}

	return parts.join("||");
}

/**
 * Compute Algorithm A.5 6-Stage Length-Prefixed Uppercase SHA-256 Digest using Web Crypto API.
 * - Uses `'-1:^^'` as the canonical null sentinel per `docs/01` §4.2 and `docs/05` Algorithm A.5.
 * - Preserves case for Satellite `hash_diff` digests (`isBusinessKey: false`) and uppercases
 *   Business Key tokens (`isBusinessKey: true`, default).
 * - Resolves all-NULL business keys to the 64-zero Zero-Key sentinel (`ZERO_KEY_HEX`, `KEY-02b`).
 */
export async function computeDv2LengthPrefixedSha256(
	tokens: readonly (string | null | undefined)[],
	options: Dv2HashOptions = {},
): Promise<string> {
	const isBusinessKey = options.isBusinessKey ?? true;
	const normalized = tokens.map((token) => {
		if (token === null || token === undefined) {
			return "";
		}
		const trimmed = token.trim();
		return isBusinessKey ? trimmed.toUpperCase() : trimmed;
	});

	if (isBusinessKey && normalized.every((val) => val.length === 0)) {
		return ZERO_KEY_HEX;
	}

	const payload = computeDv2PrehashString(tokens, options);
	const buffer = UTF8_ENCODER.encode(payload);
	const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
	const hashArray = Array.from(new Uint8Array(hashBuffer));
	return hashArray
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("")
		.toUpperCase();
}

/**
 * Format a Layer-2 Semantic Hash Pill (`[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]`)
 * that prevents 64-hex cryptographic string fatigue (`docs/06` §2.2 & `docs/10` §2.3).
 */
export function formatSemanticHashPill(
	kind: HashPillKind,
	fullSha256Hex: string,
	bkccPrefix: string,
	businessKey: string,
	entityLabel: string,
): SemanticHashPillDTO {
	const digest = fullSha256Hex.trim().toUpperCase();
	const shortHex = `${digest.slice(0, 4)}..${digest.slice(-4)}`;
	const suffix = entityLabel ? ` ${entityLabel}` : "";
	const pill_label = `[${kind}: ${shortHex} | ${bkccPrefix}:${businessKey}${suffix}]`;
	return {
		pill_label,
		full_sha256_hex: digest,
		bkcc_prefix: bkccPrefix,
		business_key: businessKey,
		entity_label: entityLabel,
	};
}
