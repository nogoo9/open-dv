import { describe, expect, it } from "bun:test";
import {
	ALL_WORKBENCH_VIEW_IDS,
	MockVaultEventStreamClient,
	PE_DEMO_EVENTS,
	PE_DEMO_EVENTS_JSONL,
	type VaultObservabilityEventDTO,
	WORKBENCH_VIEW_STAGE_MAP,
	createPeDemoReplayClient,
	filterEventsByAgentPersona,
	filterEventsByRuleId,
	filterEventsByStageId,
	filterEventsByVerdictStatus,
	filterEventsByViewId,
	filterObservabilityEvents,
	getLatestEventForView,
	groupEventsByViewId,
	redactEventForRole,
} from "../src/index";

describe("MockVaultEventStreamClient & Private Equity Demo Replay Bundle", () => {
	it("covers all 12 Workbench Views (WB-00 through WB-11) in PE_DEMO_EVENTS", () => {
		const grouped = groupEventsByViewId(PE_DEMO_EVENTS);
		for (const viewId of ALL_WORKBENCH_VIEW_IDS) {
			expect(grouped[viewId].length).toBeGreaterThanOrEqual(1);
			const latest = getLatestEventForView(PE_DEMO_EVENTS, viewId);
			expect(latest).not.toBeNull();
			expect(latest?.workbench_view_id).toBe(viewId);
			expect(latest?.layer1_story_banner.headline.length).toBeLessThanOrEqual(
				140,
			);
			expect(
				latest?.layer1_story_banner.business_impact.length,
			).toBeLessThanOrEqual(200);
			expect(WORKBENCH_VIEW_STAGE_MAP[viewId].viewId).toBe(viewId);
		}
	});

	it("loads from JSONL and supports step, seek, setSpeed, reset, and AbortSignal replay", async () => {
		const client = MockVaultEventStreamClient.loadFromJSONL(
			`\n${PE_DEMO_EVENTS_JSONL}\n`,
		);
		expect(client.getEvents()).toHaveLength(PE_DEMO_EVENTS.length);
		expect(client.getCursor()).toBe(0);
		expect(client.toJSONL().split("\n")).toHaveLength(PE_DEMO_EVENTS.length);

		expect(() => client.setSpeed(0)).toThrow(RangeError);
		expect(() => client.setSpeed(-2)).toThrow(RangeError);
		expect(() => client.setSpeed(Number.NaN)).toThrow(RangeError);
		client.setSpeed(4);
		expect(client.getSpeed()).toBe(4);

		// Single step
		const stepped: number[] = [];
		const first = client.step((_ev, idx) => stepped.push(idx));
		expect(first?.event_id).toBe("evt-pe-000");
		expect(stepped).toEqual([0]);
		expect(client.getCursor()).toBe(1);

		// Seek
		expect(() => client.seek(-1)).toThrow(RangeError);
		expect(() => client.seek(1.5)).toThrow(RangeError);
		expect(() => client.seek(Number.NaN)).toThrow(RangeError);
		expect(() => client.seek(PE_DEMO_EVENTS.length + 1)).toThrow(RangeError);
		const sought = client.seek(4);
		expect(sought?.workbench_view_id).toBe("WB-03");
		expect(client.getCursor()).toBe(4);

		// Seek to end
		expect(client.seek(PE_DEMO_EVENTS.length)).toBeNull();
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
		expect(count).toBe(PE_DEMO_EVENTS.length);
		expect(replayed).toHaveLength(PE_DEMO_EVENTS.length);

		// AbortSignal support during replay
		const demoClient = createPeDemoReplayClient();
		const abortController = new AbortController();
		const partial: VaultObservabilityEventDTO[] = [];
		const emittedCount = await demoClient.replay(
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

	it("filters events by WorkbenchViewId, StageId, VerdictStatus, AgentPersona, and rule_ids_evaluated", () => {
		expect(filterEventsByViewId(PE_DEMO_EVENTS, "WB-08")).toHaveLength(2);
		expect(
			filterEventsByStageId(PE_DEMO_EVENTS, "STAGE_05_AXI_DV_STATIC_GATES"),
		).toHaveLength(4);
		expect(
			filterEventsByVerdictStatus(PE_DEMO_EVENTS, "ASK-USER"),
		).toHaveLength(1);
		expect(
			filterEventsByAgentPersona(PE_DEMO_EVENTS, "deterministic-compiler"),
		).toHaveLength(1);
		expect(filterEventsByRuleId(PE_DEMO_EVENTS, "DV-COL-012")).toHaveLength(3);

		const composite = filterObservabilityEvents(PE_DEMO_EVENTS, {
			workbenchViewId: ["WB-07", "WB-08"],
			status: ["AUTO-FIX", "AUTO_REPAIRED"],
			ruleId: ["ALG-A1", "DV-COL-012"],
		});
		expect(composite.map((e) => e.event_id)).toEqual([
			"evt-pe-008",
			"evt-pe-010",
		]);
	});

	it("enforces 5-Tier RBAC redaction across PUBLIC, INTERNAL_MODELER, and AUDITOR_FINANCE roles", () => {
		const mnpiEvent = getLatestEventForView(PE_DEMO_EVENTS, "WB-11");
		if (!mnpiEvent) {
			throw new Error("Expected WB-11 event in PE_DEMO_EVENTS");
		}
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
		).toBe("CalPERS");
	});
});
