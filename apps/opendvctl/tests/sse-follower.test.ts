import { describe, expect, it } from "bun:test";
import { followEventStream, formatShortStage } from "../src/sse-follower";

describe("sse-follower (Decision D2)", () => {
	it("formats short stage identifiers correctly", () => {
		expect(formatShortStage("STAGE_00_ORCHESTRATION")).toBe("STAGE 00");
		expect(formatShortStage("STAGE_03_DATA_VAULT_MODELLER")).toBe("STAGE 03");
		expect(formatShortStage("custom_stage")).toBe("custom_stage");
	});

	it("parses live SSE stage events and token deltas", async () => {
		const sseBody = [
			"event: message\n",
			'data: {"event_type":"STAGE_STARTED","payload":{"wave_index":1,"stage_id":"STAGE_01_PROFILING","persona":"BronzeProfiler","model":"gemini-2.5-pro"}}\n\n',
			"event: message\n",
			'data: {"event_type":"TOKEN_DELTA","payload":{"delta":"Analyzing CSV..."}}\n\n',
			"event: message\n",
			'data: {"event_type":"STAGE_COMPLETED","payload":{"wave_index":1,"stage_id":"STAGE_01_PROFILING","persona":"BronzeProfiler","model":"gemini-2.5-pro","status":"COMPLETED","summary":"Profile generated","duration_ms":1250}}\n\n',
			"event: run_complete\n",
			'data: {"status":"COMPLETED","run_id":"run-123","duration_ms":3500}\n\n',
		].join("");

		const lines: string[] = [];
		const tokens: string[] = [];

		const mockFetch = async () =>
			new Response(sseBody, {
				status: 200,
				headers: { "Content-Type": "text/event-stream" },
			});

		const result = await followEventStream({
			streamUrl: "http://mock-server/api/v1/runs/run-123/events",
			writer: (line) => lines.push(line),
			tokenWriter: (tok) => tokens.push(tok),
			tokensEnabled: true,
			fetchImpl: mockFetch as unknown as typeof fetch,
		});

		expect(result.status).toBe("COMPLETED");
		expect(result.run_id).toBe("run-123");

		expect(lines.length).toBe(2);
		expect(lines[0]).toBe(
			"[Wave 1][STAGE 01] Starting STAGE_01_PROFILING (BronzeProfiler, model=gemini-2.5-pro)...",
		);
		expect(lines[1]).toBe(
			"[Wave 1][STAGE 01] STAGE_01_PROFILING (BronzeProfiler, model=gemini-2.5-pro) -> COMPLETED: Profile generated (1250ms)",
		);

		expect(tokens.join("")).toBe("Analyzing CSV...");
	});
});
