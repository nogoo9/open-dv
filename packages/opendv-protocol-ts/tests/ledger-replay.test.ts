import { describe, expect, it } from "bun:test";
import {
	LedgerReplayClient,
	type VaultObservabilityEventDTO,
	filterEventsByAgentPersona,
	filterEventsByRuleId,
	filterEventsByStageId,
	filterEventsByVerdictStatus,
	filterObservabilityEvents,
	redactEventForRole,
} from "../src/index";

const TEST_RUN_ID = "run-ledger-replay-clean-001";

const SYNTHETIC_EVENTS: readonly VaultObservabilityEventDTO[] = [
	{
		event_id: "evt-replay-001",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:00Z",
		stage_id: "STAGE_00_ORCHESTRATION",
		agent_persona: "vault-first-mate",
		status: "PASS",
		rule_code: "DV-ORCH-001",
		rule_ids_evaluated: ["DV-ORCH-001"],
		data_classification: "PUBLIC",
		layer1_story_banner: {
			headline: "Orchestration pipeline initialized",
			business_impact: "Ready for task execution",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: { initialized: true },
		},
	},
	{
		event_id: "evt-replay-002",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:10Z",
		stage_id: "STAGE_01_SCOUT_SOURCE",
		agent_persona: "scout-source",
		status: "PASS",
		rule_code: "DV-SRC-001",
		rule_ids_evaluated: ["DV-SRC-001", "DV-COL-010"],
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline: "Tabular source schemas discovered",
			business_impact: "Source ingested with high confidence",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: { tables_discovered: 4 },
		},
	},
	{
		event_id: "evt-replay-003",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:20Z",
		stage_id: "STAGE_03_SHIP_VAULT_PASS1",
		agent_persona: "ship-vault",
		status: "AUTO-FIX",
		rule_code: "DV-COL-012",
		rule_ids_evaluated: ["DV-COL-012", "DV-SURR-001"],
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline: "Column triage completed with automated repairs",
			business_impact: "Surrogates allocated for unmapped columns",
			verdict_pill: "AUTO-FIX",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: { repaired_columns: 2 },
		},
	},
	{
		event_id: "evt-replay-004",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:30Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		agent_persona: "axi-dv",
		status: "AUTO_REPAIRED",
		rule_code: "DV-COL-012",
		rule_ids_evaluated: ["DV-GATE-003", "DV-COL-012"],
		data_classification: "CONFIDENTIAL",
		remediation: {
			codemod_type: "enforce_tier3_bkcc_prefix",
			target_entity: "hub_limited_partner",
			patch_diff:
				'- require_src_qualifier: false\n+ require_src_qualifier: true\n+ bkcc_prefix: "LEGACY_GP_ADMIN"',
			action_description:
				"Scoped legacy_gp.src_legacy_lp_roster.lp_num under BKCC=LEGACY_GP_ADMIN on Attempt 2.",
		},
		layer1_story_banner: {
			headline: "Static verification gates passed with topology adjustments",
			business_impact: "Grain dependencies normalized",
			verdict_pill: "AUTO_REPAIRED",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: { gates_passed: 6 },
		},
	},
	{
		event_id: "evt-replay-005",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:40Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		agent_persona: "axi-dv",
		status: "ASK-USER",
		rule_code: "DV-GATE-007",
		rule_ids_evaluated: ["DV-GATE-007"],
		data_classification: "CONFIDENTIAL",
		layer1_story_banner: {
			headline: "Ambiguous entity resolution requires steward approval",
			business_impact: "Pipeline held pending steward decision",
			verdict_pill: "ASK-USER",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: { holds_active: 1 },
		},
	},
	{
		event_id: "evt-replay-006",
		run_id: TEST_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:50Z",
		stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
		agent_persona: "deterministic-compiler",
		status: "PASS",
		rule_code: "ALG-A5",
		rule_ids_evaluated: ["ALG-A5", "DV-SQL-001"],
		data_classification: "MNPI",
		layer1_story_banner: {
			headline: "Deterministic DDL/DML compilation complete",
			business_impact: "Vault models ready for materialization",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				{
					pill_label: "[HK: 5D15..0AE4 | GLOBAL:LP-101 Alpha Fund]",
					full_sha256_hex:
						"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
					bkcc_prefix: "GLOBAL",
					business_key: "LP-101",
					entity_label: "Alpha Fund",
				},
			],
			summary_metrics: { models_compiled: 8 },
		},
		layer3_formal_proof: {
			algorithm_ref: "Algorithm A.5 (Length-Prefixed SHA-256)",
			formula_latex: "\\mathcal{H}_{hk}(K) = \\mathrm{SHA256}(\\dots)",
		},
	},
];

const SYNTHETIC_JSONL = SYNTHETIC_EVENTS.map((e) => JSON.stringify(e)).join(
	"\n",
);

describe("LedgerReplayClient (Offline Event Ledger Replay Engine)", () => {
	it("loads from JSONL and supports step, seek, setSpeed, reset, and AbortSignal replay", async () => {
		const client = LedgerReplayClient.loadFromJSONL(`\n${SYNTHETIC_JSONL}\n`);
		expect(client.getEvents()).toHaveLength(SYNTHETIC_EVENTS.length);
		expect(client.getCursor()).toBe(0);
		expect(client.toJSONL().split("\n")).toHaveLength(SYNTHETIC_EVENTS.length);

		expect(() => client.setSpeed(0)).toThrow(RangeError);
		expect(() => client.setSpeed(-2)).toThrow(RangeError);
		expect(() => client.setSpeed(Number.NaN)).toThrow(RangeError);
		client.setSpeed(4);
		expect(client.getSpeed()).toBe(4);

		// Single step
		const stepped: number[] = [];
		const first = client.step((_ev, idx) => stepped.push(idx));
		expect(first?.event_id).toBe("evt-replay-001");
		expect(stepped).toEqual([0]);
		expect(client.getCursor()).toBe(1);

		// Seek
		expect(() => client.seek(-1)).toThrow(RangeError);
		expect(() => client.seek(1.5)).toThrow(RangeError);
		expect(() => client.seek(Number.NaN)).toThrow(RangeError);
		expect(() => client.seek(SYNTHETIC_EVENTS.length + 1)).toThrow(RangeError);
		const sought = client.seek(3);
		expect(sought?.event_id).toBe("evt-replay-004");
		expect(client.getCursor()).toBe(3);

		// Seek to end
		expect(client.seek(SYNTHETIC_EVENTS.length)).toBeNull();
		expect(client.step()).toBeNull();

		// Reset and full replay
		client.reset();
		expect(client.getCursor()).toBe(0);
		await expect(
			client.replay(() => {}, { speedMultiplier: 0 }),
		).rejects.toThrow(RangeError);
		await expect(
			client.replay(() => {}, { speedMultiplier: Number.NaN }),
		).rejects.toThrow(RangeError);
		await expect(client.replay(() => {}, { stepDelayMs: -1 })).rejects.toThrow(
			RangeError,
		);
		const replayed: VaultObservabilityEventDTO[] = [];
		const count = await client.replay((ev) => replayed.push(ev), {
			stepDelayMs: 4,
			speedMultiplier: 4,
		});
		expect(count).toBe(SYNTHETIC_EVENTS.length);
		expect(replayed).toHaveLength(SYNTHETIC_EVENTS.length);

		// AbortSignal support during replay
		const replayClient = new LedgerReplayClient(SYNTHETIC_EVENTS);
		const abortController = new AbortController();
		const partial: VaultObservabilityEventDTO[] = [];
		const emittedCount = await replayClient.replay(
			(ev, idx) => {
				partial.push(ev);
				if (idx === 2) {
					abortController.abort();
				}
			},
			{ signal: abortController.signal },
		);
		expect(emittedCount).toBe(3);
		expect(partial).toHaveLength(3);
	});

	it("filters events by StageId, VerdictStatus, AgentPersona, and rule_ids_evaluated", () => {
		expect(
			filterEventsByStageId(SYNTHETIC_EVENTS, "STAGE_05_AXI_DV_STATIC_GATES"),
		).toHaveLength(2);
		expect(
			filterEventsByVerdictStatus(SYNTHETIC_EVENTS, "ASK-USER"),
		).toHaveLength(1);
		expect(
			filterEventsByAgentPersona(SYNTHETIC_EVENTS, "deterministic-compiler"),
		).toHaveLength(1);
		expect(filterEventsByRuleId(SYNTHETIC_EVENTS, "DV-COL-012")).toHaveLength(
			2,
		);

		const composite = filterObservabilityEvents(SYNTHETIC_EVENTS, {
			stageId: ["STAGE_03_SHIP_VAULT_PASS1", "STAGE_05_AXI_DV_STATIC_GATES"],
			status: ["AUTO-FIX", "AUTO_REPAIRED"],
			ruleId: ["DV-COL-012"],
		});
		expect(composite.map((e) => e.event_id)).toEqual([
			"evt-replay-003",
			"evt-replay-004",
		]);
	});

	it("enforces 5-Tier RBAC redaction across PUBLIC, INTERNAL_MODELER, and AUDITOR_FINANCE roles", () => {
		const mnpiEvent = SYNTHETIC_EVENTS[5];
		expect(mnpiEvent.data_classification).toBe("MNPI");

		// INTERNAL_MODELER lacks MNPI clearance -> masked
		const redactedForModeler = redactEventForRole(
			mnpiEvent,
			"INTERNAL_MODELER",
		);
		expect(redactedForModeler.layer1_story_banner.headline).toContain(
			"[REDACTED MNPI]",
		);
		expect(
			redactedForModeler.layer2_visual_payload.summary_metrics.redacted,
		).toBe(true);
		expect(
			redactedForModeler.layer2_visual_payload.semantic_hash_pills[0]
				?.business_key,
		).toBe("[REDACTED]");
		expect(
			redactedForModeler.layer2_visual_payload.semantic_hash_pills[0]
				?.pill_label,
		).toBe("[HK: 5D15..0AE4 | GLOBAL:[REDACTED]]");
		expect(redactedForModeler.layer3_formal_proof?.formula_latex).toBe(
			"[REDACTED MNPI]",
		);
		expect(redactedForModeler.layer3_formal_proof?.algorithm_ref).toContain(
			"Algorithm A.5",
		);

		// AUDITOR_FINANCE holds MNPI clearance -> unredacted
		const clearedForAuditor = redactEventForRole(mnpiEvent, "AUDITOR_FINANCE");
		expect(clearedForAuditor.layer1_story_banner.headline).toBe(
			mnpiEvent.layer1_story_banner.headline,
		);
		expect(
			clearedForAuditor.layer2_visual_payload.semantic_hash_pills[0]
				?.entity_label,
		).toBe("Alpha Fund");
	});
});
