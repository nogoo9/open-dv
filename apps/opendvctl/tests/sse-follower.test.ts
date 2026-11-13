import { describe, expect, it } from "bun:test";
import { CliDomainError } from "../src/error-handler";
import {
	followEventStream,
	followStudioJobEvents,
	formatShortStage,
} from "../src/sse-follower";

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

	it("S5-R10-MIN-02: aborts half-open stalled Studio SSE stream on timeoutMs and resets timer on progress chunks", async () => {
		let capturedSignal: AbortSignal | undefined;
		const stalledFetch = async (
			_input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			capturedSignal = init?.signal ?? undefined;
			const body = new ReadableStream<Uint8Array>({
				start(controller) {
					controller.enqueue(
						new TextEncoder().encode(
							'id: 1\nevent: job_started\ndata: {"job_id":"job-stall"}\n\n',
						),
					);
					// Leave stream half-open indefinitely without closing or emitting terminal event
				},
			});
			return new Response(body, {
				status: 200,
				headers: { "Content-Type": "text/event-stream" },
			});
		};

		let caughtErr: unknown;
		try {
			await followStudioJobEvents({
				streamUrl:
					"http://mock-server/api/v1/ontology/studio/sessions/s1/jobs/job-stall/events",
				timeoutMs: 35,
				streamEnabled: false,
				fetchImpl: stalledFetch as unknown as typeof fetch,
			});
		} catch (err) {
			caughtErr = err;
		}

		expect(caughtErr).toBeInstanceOf(CliDomainError);
		expect((caughtErr as CliDomainError).errorCode).toBe("API_UNREACHABLE");
		expect((caughtErr as CliDomainError).httpStatus).toBe(503);
		expect(capturedSignal?.aborted).toBe(true);

		// Verify active chunks reset the idle timer across multiple intervals > timeoutMs
		const activeFetch = async () => {
			const encoder = new TextEncoder();
			const body = new ReadableStream<Uint8Array>({
				async start(controller) {
					controller.enqueue(
						encoder.encode(
							'id: 1\nevent: job_started\ndata: {"job_id":"job-ok","stage":"parse"}\n\n',
						),
					);
					await new Promise((r) => setTimeout(r, 25));
					controller.enqueue(
						encoder.encode(
							'id: 2\nevent: progress_update\ndata: {"job_id":"job-ok","pct_complete":50}\n\n',
						),
					);
					await new Promise((r) => setTimeout(r, 25));
					controller.enqueue(
						encoder.encode(
							'id: 3\nevent: job_completed\ndata: {"job_id":"job-ok","stage":"done"}\n\n',
						),
					);
					controller.close();
				},
			});
			return new Response(body, {
				status: 200,
				headers: { "Content-Type": "text/event-stream" },
			});
		};

		const summary = await followStudioJobEvents({
			streamUrl:
				"http://mock-server/api/v1/ontology/studio/sessions/s1/jobs/job-ok/events",
			timeoutMs: 45,
			streamEnabled: false,
			fetchImpl: activeFetch as unknown as typeof fetch,
		});
		expect(summary.status).toBe("COMPLETED");
		expect(summary.last_event_id).toBe("3");
	});
});
