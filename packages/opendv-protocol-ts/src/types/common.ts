/**
 * Common foundational types and constants for `@opendv/protocol-ts`.
 */

export const PROTOCOL_VERSION = "1.0.0";

/** Uppercase 64-character hexadecimal SHA-256 digest string. */
export type Sha256Hex = string;

/** ISO-8601 UTC timestamp string (e.g. `2026-10-30T09:00:01Z`). */
export type IsoUtcTimestamp = string;

/** 5-Tier RBAC Data Classification levels (docs/06 §1.1 & docs/08 §5.2). */
export type DataClassification =
	| "PUBLIC"
	| "INTERNAL"
	| "CONFIDENTIAL"
	| "MNPI"
	| "RESTRICTED_PII";

/** 5-Tier RBAC User Roles (docs/08 §5.2). */
export type RbacRole =
	| "PUBLIC"
	| "INTERNAL_MODELER"
	| "DATA_STEWARD"
	| "AUDITOR_FINANCE"
	| "RESTRICTED_PII_CUSTODIAN";

/** Agnostic Agent Harness Provider identifiers (docs/08 & docs/09). */
export type HarnessProvider = "antigravity" | "claude";

/** Reasoning / Execution Model Tiers (docs/09 & docs/11). */
export type ModelTier = "argon" | "flash";

/** Pipeline Run Execution Lifecycle Status (docs/08 §5.2). */
export type RunStatus =
	| "PENDING"
	| "RUNNING"
	| "PAUSED_HOLD"
	| "COMPLETED"
	| "FAILED"
	| "ABORTED";

/** Semantic Hash Pill Role Kind (docs/06 §2.2 & docs/10 §2.3). */
export type HashPillKind =
	| "HK"
	| "LHK"
	| "HD"
	| "GHOST"
	| "ZERO-KEY"
	| "ERR-KEY";

/**
 * Canonical 5-Tier RBAC clearance matrix mapping each role to its authorized
 * `DataClassification` tiers (docs/08 §5.2).
 */
export const ROLE_CLEARANCE_LEVELS: Record<
	RbacRole,
	readonly DataClassification[]
> = {
	PUBLIC: ["PUBLIC"],
	INTERNAL_MODELER: ["PUBLIC", "INTERNAL"],
	DATA_STEWARD: ["PUBLIC", "INTERNAL", "CONFIDENTIAL"],
	AUDITOR_FINANCE: ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI"],
	RESTRICTED_PII_CUSTODIAN: [
		"PUBLIC",
		"INTERNAL",
		"CONFIDENTIAL",
		"MNPI",
		"RESTRICTED_PII",
	],
};

/** RFC 9457 Problem Details (`application/problem+json`) error payload. */
export interface ProblemDetailsDTO {
	type: string;
	title: string;
	status: number;
	detail?: string | undefined;
	instance?: string | undefined;
	[key: string]: unknown;
}
