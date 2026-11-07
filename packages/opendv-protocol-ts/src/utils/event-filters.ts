/**
 * Filtering, grouping, and RBAC redaction utilities for `VaultObservabilityEventDTO` streams.
 */

import {
	type DataClassification,
	ROLE_CLEARANCE_LEVELS,
	type RbacRole,
} from "../types/common";
import type {
	AgentPersona,
	StageId,
	VaultObservabilityEventDTO,
	VerdictStatus,
} from "../types/observability-event";

export interface EventFilterCriteria {
	stageId?: StageId | readonly StageId[] | undefined;
	status?: VerdictStatus | readonly VerdictStatus[] | undefined;
	agentPersona?: AgentPersona | readonly AgentPersona[] | undefined;
	ruleId?: string | readonly string[] | undefined;
	runId?: string | undefined;
}

function toSet<T>(value: T | readonly T[]): ReadonlySet<T> {
	if (Array.isArray(value)) {
		return new Set(value as readonly T[]);
	}
	return new Set([value as T]);
}

/** Filter events matching one or more `StageId` values (`STAGE_00`..`STAGE_07`). */
export function filterEventsByStageId(
	events: readonly VaultObservabilityEventDTO[],
	stageId: StageId | readonly StageId[],
): VaultObservabilityEventDTO[] {
	const allowed = toSet(stageId);
	return events.filter((ev) => allowed.has(ev.stage_id));
}

/** Filter events matching one or more `VerdictStatus` values (`PASS`, `AUTO-FIX`, etc.). */
export function filterEventsByVerdictStatus(
	events: readonly VaultObservabilityEventDTO[],
	status: VerdictStatus | readonly VerdictStatus[],
): VaultObservabilityEventDTO[] {
	const allowed = toSet(status);
	return events.filter((ev) => allowed.has(ev.status));
}

/** Filter events matching one or more `AgentPersona` values. */
export function filterEventsByAgentPersona(
	events: readonly VaultObservabilityEventDTO[],
	persona: AgentPersona | readonly AgentPersona[],
): VaultObservabilityEventDTO[] {
	const allowed = toSet(persona);
	return events.filter((ev) => allowed.has(ev.agent_persona));
}

/** Filter events where `rule_ids_evaluated` (or `rule_code`) intersects the requested rule ID(s). */
export function filterEventsByRuleId(
	events: readonly VaultObservabilityEventDTO[],
	ruleId: string | readonly string[],
): VaultObservabilityEventDTO[] {
	const allowed = toSet(ruleId);
	return events.filter((ev) => {
		if (ev.rule_code && allowed.has(ev.rule_code)) {
			return true;
		}
		if (!ev.rule_ids_evaluated || ev.rule_ids_evaluated.length === 0) {
			return false;
		}
		return ev.rule_ids_evaluated.some((id) => allowed.has(id));
	});
}

/** Filter events by composite `EventFilterCriteria`. */
export function filterObservabilityEvents(
	events: readonly VaultObservabilityEventDTO[],
	criteria: EventFilterCriteria,
): VaultObservabilityEventDTO[] {
	const stageSet =
		criteria.stageId !== undefined ? toSet(criteria.stageId) : null;
	const statusSet =
		criteria.status !== undefined ? toSet(criteria.status) : null;
	const personaSet =
		criteria.agentPersona !== undefined ? toSet(criteria.agentPersona) : null;
	const ruleSet = criteria.ruleId !== undefined ? toSet(criteria.ruleId) : null;

	return events.filter((ev) => {
		if (criteria.runId !== undefined && ev.run_id !== criteria.runId) {
			return false;
		}
		if (stageSet !== null && !stageSet.has(ev.stage_id)) {
			return false;
		}
		if (statusSet !== null && !statusSet.has(ev.status)) {
			return false;
		}
		if (personaSet !== null && !personaSet.has(ev.agent_persona)) {
			return false;
		}
		if (ruleSet !== null) {
			const matchesRuleCode =
				ev.rule_code !== undefined && ruleSet.has(ev.rule_code);
			const matchesEvaluated =
				ev.rule_ids_evaluated?.some((r) => ruleSet.has(r)) ?? false;
			if (!matchesRuleCode && !matchesEvaluated) {
				return false;
			}
		}
		return true;
	});
}

/**
 * Client-side 5-Tier RBAC redaction helper mirroring `redact_event_for_role` (`docs/08` §5.2)
 * for offline `.jsonl` replay under different simulated RBAC roles.
 */
export function redactEventForRole(
	event: VaultObservabilityEventDTO,
	role: RbacRole,
): VaultObservabilityEventDTO {
	const classification: DataClassification =
		event.data_classification ?? "INTERNAL";
	const clearedTiers = ROLE_CLEARANCE_LEVELS[role] ?? ["PUBLIC"];
	if (clearedTiers.includes(classification)) {
		return structuredClone(event);
	}

	const cloned = structuredClone(event);
	cloned.layer1_story_banner = {
		headline: `[REDACTED ${classification}] Stage ${cloned.stage_id} telemetry masked for role '${role}'.`,
		business_impact: `Sensitive ${classification} payload masked for role '${role}'.`,
		verdict_pill: cloned.layer1_story_banner.verdict_pill,
	};

	const redactedPills = (
		cloned.layer2_visual_payload.semantic_hash_pills ?? []
	).map((pill) => {
		const digest = pill.full_sha256_hex;
		const shortHex =
			digest.length >= 8
				? `${digest.slice(0, 4)}..${digest.slice(-4)}`
				: "REDACTED";
		const bkcc = pill.bkcc_prefix || "GLOBAL";
		return {
			...pill,
			business_key: "[REDACTED]",
			entity_label: "[REDACTED]",
			pill_label: `[HK: ${shortHex} | ${bkcc}:[REDACTED]]`,
		};
	});

	cloned.layer2_visual_payload = {
		semantic_hash_pills: redactedPills,
		summary_metrics: {
			redacted: true,
			required_classification: classification,
		},
	};

	if (cloned.layer3_formal_proof) {
		const redactedProof: Record<string, string> = {};
		for (const [k, v] of Object.entries(cloned.layer3_formal_proof)) {
			if (v !== undefined) {
				redactedProof[k] =
					k === "algorithm_ref" ? v : `[REDACTED ${classification}]`;
			}
		}
		cloned.layer3_formal_proof = redactedProof;
	}

	return cloned;
}
