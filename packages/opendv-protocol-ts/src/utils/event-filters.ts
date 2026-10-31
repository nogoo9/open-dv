/**
 * Filtering, grouping, and RBAC redaction utilities for `VaultObservabilityEventDTO` streams.
 */

import {
	type DataClassification,
	ROLE_CLEARANCE_LEVELS,
	type RbacRole,
} from "../types/common";
import {
	ALL_WORKBENCH_VIEW_IDS,
	type AgentPersona,
	type StageId,
	type VaultObservabilityEventDTO,
	type VerdictStatus,
	type VizId,
	type WorkbenchViewId,
} from "../types/observability-event";

export interface EventFilterCriteria {
	workbenchViewId?: WorkbenchViewId | readonly WorkbenchViewId[] | undefined;
	stageId?: StageId | readonly StageId[] | undefined;
	status?: VerdictStatus | readonly VerdictStatus[] | undefined;
	agentPersona?: AgentPersona | readonly AgentPersona[] | undefined;
	ruleId?: string | readonly string[] | undefined;
	vizId?: VizId | readonly VizId[] | undefined;
	runId?: string | undefined;
}

function toSet<T>(value: T | readonly T[]): ReadonlySet<T> {
	if (Array.isArray(value)) {
		return new Set(value as readonly T[]);
	}
	return new Set([value as T]);
}

/** Filter events matching one or more `WorkbenchViewId` values (`WB-00`..`WB-11`). */
export function filterEventsByViewId(
	events: readonly VaultObservabilityEventDTO[],
	viewId: WorkbenchViewId | readonly WorkbenchViewId[],
): VaultObservabilityEventDTO[] {
	const allowed = toSet(viewId);
	return events.filter((ev) => allowed.has(ev.workbench_view_id));
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
	const viewSet =
		criteria.workbenchViewId !== undefined
			? toSet(criteria.workbenchViewId)
			: null;
	const stageSet =
		criteria.stageId !== undefined ? toSet(criteria.stageId) : null;
	const statusSet =
		criteria.status !== undefined ? toSet(criteria.status) : null;
	const personaSet =
		criteria.agentPersona !== undefined ? toSet(criteria.agentPersona) : null;
	const ruleSet = criteria.ruleId !== undefined ? toSet(criteria.ruleId) : null;
	const vizSet = criteria.vizId !== undefined ? toSet(criteria.vizId) : null;

	return events.filter((ev) => {
		if (criteria.runId !== undefined && ev.run_id !== criteria.runId) {
			return false;
		}
		if (viewSet !== null && !viewSet.has(ev.workbench_view_id)) {
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
		if (vizSet !== null && !vizSet.has(ev.viz_id)) {
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

/** Group a stream of events into buckets keyed by all 12 `WorkbenchViewId`s (`WB-00`..`WB-11`). */
export function groupEventsByViewId(
	events: readonly VaultObservabilityEventDTO[],
): Record<WorkbenchViewId, VaultObservabilityEventDTO[]> {
	const grouped = {} as Record<WorkbenchViewId, VaultObservabilityEventDTO[]>;
	for (const viewId of ALL_WORKBENCH_VIEW_IDS) {
		grouped[viewId] = [];
	}
	for (const ev of events) {
		grouped[ev.workbench_view_id]?.push(ev);
	}
	return grouped;
}

/** Return the most recent event for a specific `WorkbenchViewId`, or `null` if none exists. */
export function getLatestEventForView(
	events: readonly VaultObservabilityEventDTO[],
	viewId: WorkbenchViewId,
): VaultObservabilityEventDTO | null {
	for (let i = events.length - 1; i >= 0; i -= 1) {
		const ev = events[i];
		if (ev && ev.workbench_view_id === viewId) {
			return ev;
		}
	}
	return null;
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
