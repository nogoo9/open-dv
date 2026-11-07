import { describe, expect, it } from "bun:test";
import packageJson from "../package.json";
import {
	type EventSourceLike,
	OpenDVPlatformClient,
	VaultEventStreamClient,
	type VaultObservabilityEventDTO,
} from "../src/index";
const MOCK_PROJECT_ID = "proj-test-client";
const MOCK_RUN_ID = "run-test-client-20261107";

const MOCK_PROJECT = {
	project_id: MOCK_PROJECT_ID,
	display_name: "Test Project",
	domain_namespace: "test_domain",
	graph_a_sha256: "A1".repeat(32),
	latest_run_id: MOCK_RUN_ID,
	created_at_utc: "2026-11-07T00:00:00Z",
};

const MOCK_RUN = {
	run_id: MOCK_RUN_ID,
	project_id: MOCK_PROJECT_ID,
	harness_provider: "antigravity",
	status: "COMPLETED",
	started_at_utc: "2026-11-07T00:00:00Z",
	completed_at_utc: "2026-11-07T00:01:00Z",
	current_stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
	event_count: 2,
	gate_scorecard: {
		gate_1_contract_grain: "PASS",
		gate_4_yagni_cost_guard: "AUTO-FIX",
	},
	active_hold_ids: [],
	events_sse_url: `/api/v1/runs/${MOCK_RUN_ID}/events`,
	gates_url: `/api/v1/runs/${MOCK_RUN_ID}/gates`,
	sandbox_url: `/api/v1/runs/${MOCK_RUN_ID}/sandbox`,
	decisions_url: `/api/v1/runs/${MOCK_RUN_ID}/decisions`,
};

const MOCK_EVENTS: VaultObservabilityEventDTO[] = [
	{
		event_id: "evt-test-000",
		run_id: MOCK_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:00Z",
		stage_id: "STAGE_00_ORCHESTRATION",
		agent_persona: "vault-first-mate",
		status: "PASS",
		layer1_story_banner: {
			headline: "Orchestration initialized",
			business_impact: "Ready to process",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: {},
		},
	},
	{
		event_id: "evt-test-001",
		run_id: MOCK_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:05Z",
		stage_id: "STAGE_01_SCOUT_SOURCE",
		agent_persona: "scout-source",
		status: "PASS",
		layer1_story_banner: {
			headline: "Source discovered",
			business_impact: "Source ingested",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: {},
		},
	},
	{
		event_id: "evt-test-002",
		run_id: MOCK_RUN_ID,
		timestamp_utc: "2026-11-07T00:00:10Z",
		stage_id: "STAGE_02_SCOUT_UNSTRUCTURED",
		agent_persona: "scout-unstructured",
		status: "PASS",
		layer1_story_banner: {
			headline: "Unstructured documents scanned",
			business_impact: "Entities extracted",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [],
			summary_metrics: {},
		},
	},
];

class FakeEventSource implements EventSourceLike {
	readonly url: string;
	readonly headers: Record<string, string> | undefined;
	readonly listeners = new Map<
		string,
		((ev: MessageEvent<string>) => void)[]
	>();
	onerror: ((err: unknown) => void) | null = null;
	closed = false;

	constructor(url: string, headers?: Record<string, string>) {
		this.url = url;
		this.headers = headers;
	}

	addEventListener(
		type: string,
		listener: (ev: MessageEvent<string>) => void,
	): void {
		const list = this.listeners.get(type) ?? [];
		list.push(listener);
		this.listeners.set(type, list);
	}

	emit(type: string, data: string, lastEventId = ""): void {
		const list = this.listeners.get(type) ?? [];
		const event = { data, lastEventId } as MessageEvent<string>;
		for (const listener of list) {
			listener(event);
		}
	}

	triggerError(err: unknown): void {
		this.onerror?.(err);
	}

	close(): void {
		this.closed = true;
	}
}

describe("package.json zero-dependency invariant & REST/SSE clients", () => {
	it("enforces zero runtime dependencies in packages/opendv-protocol-ts/package.json", () => {
		const deps = Object.keys(packageJson.dependencies ?? {});
		expect(deps).toEqual([]);
	});

	it("OpenDVPlatformClient executes typed REST calls with RBAC headers and error handling", async () => {
		const requests: {
			url: string;
			method: string;
			headers: Record<string, string>;
			body?: string;
		}[] = [];

		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			const method = init?.method ?? "GET";
			const headers = (init?.headers ?? {}) as Record<string, string>;
			const body = typeof init?.body === "string" ? init.body : undefined;
			requests.push(
				body !== undefined
					? { url, method, headers, body }
					: { url, method, headers },
			);

			if (url.endsWith("/api/v1/health")) {
				return new Response(
					JSON.stringify({ status: "ok", version: "6.0.0" }),
					{
						status: 200,
					},
				);
			}
			if (url.endsWith("/api/v1/projects") && method === "GET") {
				return new Response(JSON.stringify([MOCK_PROJECT]), { status: 200 });
			}
			if (url.endsWith("/api/v1/runs") && method === "GET") {
				return new Response(JSON.stringify([MOCK_RUN]), { status: 200 });
			}
			if (url.endsWith(`/api/v1/runs/${MOCK_RUN_ID}`)) {
				return new Response(JSON.stringify(MOCK_RUN), { status: 200 });
			}
			if (url.endsWith(`/api/v1/runs/${MOCK_RUN_ID}/gates`)) {
				return new Response(
					JSON.stringify({ gate_1: "PASS", gate_4: "AUTO-FIX" }),
					{
						status: 200,
					},
				);
			}
			if (url.endsWith(`/api/v1/runs/${MOCK_RUN_ID}/sandbox`)) {
				return new Response(JSON.stringify({ all_batches_passed: true }), {
					status: 200,
				});
			}
			if (
				url.endsWith(`/api/v1/runs/${MOCK_RUN_ID}/decisions`) &&
				method === "POST"
			) {
				return new Response(
					JSON.stringify({
						decision_id: "dec-001",
						status: "ACCEPTED",
						resumed_run_id: MOCK_RUN_ID,
						next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
						next_event_id: "evt-pe-005",
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/artifacts/")) {
				return new Response("contract_version: '5.0'\n", { status: 200 });
			}
			return new Response("Not Found", { status: 404 });
		}) as unknown as typeof fetch;

		const client = new OpenDVPlatformClient({
			baseUrl: "http://localhost:8420///",
			rbacRole: "AUDITOR_FINANCE",
			fetchImpl: mockFetch,
		});

		expect(client.getBaseUrl()).toBe("http://localhost:8420");
		expect(client.getHeaders()["X-OpenDV-Role"]).toBe("AUDITOR_FINANCE");

		const health = await client.getHealth();
		expect(health).toEqual({ status: "ok", version: "6.0.0" });

		const projects = await client.listProjects();
		expect(projects).toHaveLength(1);
		expect(projects[0]?.project_id).toBe(MOCK_PROJECT.project_id);

		const runs = await client.listRuns();
		expect(runs).toHaveLength(1);

		const run = await client.getRun(MOCK_RUN_ID);
		expect(run.run_id).toBe(MOCK_RUN_ID);

		const gates = await client.getRunGates(MOCK_RUN_ID);
		expect(gates.gate_4).toBe("AUTO-FIX");

		const sandbox = await client.getRunSandbox(MOCK_RUN_ID);
		expect(sandbox.all_batches_passed).toBe(true);

		const decisionRes = await client.submitDecision(MOCK_RUN_ID, {
			event_id: "evt-pe-004",
			hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			action: "APPROVE",
			selected_candidate_id: "APEX_CO_I",
			resolution_notes: "Confirmed with LPAC counsel.",
		});
		expect(decisionRes.status).toBe("ACCEPTED");

		const artifactText = await client.getArtifact("A1".repeat(32));
		expect(artifactText).toContain("contract_version");

		expect(client.getRun("missing-run")).rejects.toThrow("HTTP 404");
		expect(requests.length).toBeGreaterThanOrEqual(8);
	});

	it("VaultEventStreamClient deduplicates seenEventIds, replays Last-Event-ID, and reconnects with jittered backoff", async () => {
		const createdSources: FakeEventSource[] = [];
		const receivedEvents: VaultObservabilityEventDTO[] = [];
		const completedSummaries: { status: string }[] = [];
		const errors: unknown[] = [];

		const streamClient = new VaultEventStreamClient({
			streamUrl: "http://localhost:8420/api/v1/runs/run-1/events",
			lastEventId: "evt-test-000",
			reconnectBaseDelayMs: 20,
			maxReconnectDelayMs: 100,
			randomFn: () => 0.5,
			eventSourceFactory: (url, init) => {
				const fake = new FakeEventSource(url, init?.headers);
				createdSources.push(fake);
				return fake;
			},
			onEvent: (ev) => receivedEvents.push(ev),
			onRunComplete: (summary) => completedSummaries.push(summary),
			onError: (err) => errors.push(err),
		});

		expect(streamClient.getLastEventId()).toBe("evt-test-000");
		expect(streamClient.computeNextReconnectDelayMs()).toBe(22);

		streamClient.connect();
		expect(streamClient.isConnected()).toBe(true);
		expect(createdSources).toHaveLength(1);
		expect(createdSources[0]?.url).toContain("last_event_id=evt-test-000");
		expect(createdSources[0]?.headers?.["Last-Event-ID"]).toBe("evt-test-000");

		// Duplicate of initial lastEventId should be ignored
		createdSources[0]?.emit(
			"message",
			JSON.stringify(MOCK_EVENTS[0]),
			"evt-test-000",
		);
		expect(receivedEvents).toHaveLength(0);

		// New event evt-test-001 should be delivered once
		createdSources[0]?.emit(
			"message",
			JSON.stringify(MOCK_EVENTS[1]),
			"evt-test-001",
		);
		createdSources[0]?.emit(
			"message",
			JSON.stringify(MOCK_EVENTS[1]),
			"evt-test-001",
		);
		expect(receivedEvents).toHaveLength(1);
		expect(streamClient.getLastEventId()).toBe("evt-test-001");

		// Simulate connection drop and automatic reconnect with updated last_event_id
		createdSources[0]?.triggerError(new Error("SSE dropped"));
		expect(errors).toHaveLength(1);
		expect(streamClient.getReconnectAttempts()).toBe(1);

		await new Promise((resolve) => setTimeout(resolve, 40));
		expect(createdSources).toHaveLength(2);
		expect(createdSources[1]?.url).toContain("last_event_id=evt-test-001");

		// Emit run_complete and verify client disconnects cleanly
		createdSources[1]?.emit(
			"run_complete",
			JSON.stringify({ status: "COMPLETED" }),
		);
		expect(completedSummaries).toEqual([{ status: "COMPLETED" }]);
		expect(streamClient.isConnected()).toBe(false);
	});

	it("VaultEventStreamClient.ingestSseChunk parses raw SSE wire frames directly", () => {
		const events: VaultObservabilityEventDTO[] = [];
		let doneStatus = "";
		const client = new VaultEventStreamClient({
			streamUrl: "/api/v1/runs/run-1/events",
			onEvent: (ev) => events.push(ev),
			onRunComplete: (s) => {
				doneStatus = s.status;
			},
		});

		client.ingestSseChunk("event: run_complete\r");
		client.ingestSseChunk(
			`\ndata: {"status":"COMPLETED"}\r\n\r\nid: evt-test-002\ndata: ${JSON.stringify(MOCK_EVENTS[2])}\r\n\r\n`,
		);
		expect(doneStatus).toBe("COMPLETED");
		expect(events).toHaveLength(0);

		const secondClient = new VaultEventStreamClient({
			streamUrl: "/api/v1/runs/run-1/events",
			onEvent: (ev) => events.push(ev),
			onRunComplete: (s) => {
				doneStatus = s.status;
			},
		});
		secondClient.ingestSseChunk(
			`id: evt-test-002\ndata: ${JSON.stringify(MOCK_EVENTS[2])}\n\nevent: run_complete\ndata: {"status":"COMPLETED"}\n\n`,
		);
		expect(events).toHaveLength(1);
		expect(events[0]?.event_id).toBe("evt-test-002");
	});

	it("VaultEventStreamClient cleans up pending reconnect timers and existing EventSources across connect and disconnect", async () => {
		const createdSources: FakeEventSource[] = [];
		const streamClient = new VaultEventStreamClient({
			streamUrl: "http://localhost:8420/api/v1/runs/run-1/events",
			reconnectBaseDelayMs: 25,
			maxReconnectDelayMs: 100,
			randomFn: () => 0,
			eventSourceFactory: (url, init) => {
				const fake = new FakeEventSource(url, init?.headers);
				createdSources.push(fake);
				return fake;
			},
			onEvent: () => {},
		});

		streamClient.connect();
		streamClient.connect();
		expect(createdSources).toHaveLength(2);
		expect(createdSources[0]?.closed).toBe(true);
		expect(createdSources[1]?.closed).toBe(false);

		createdSources[1]?.triggerError(new Error("drop 1"));
		createdSources[1]?.triggerError(new Error("drop 2"));
		expect(streamClient.getReconnectAttempts()).toBe(1);

		streamClient.disconnect();
		await new Promise((resolve) => setTimeout(resolve, 45));
		expect(createdSources).toHaveLength(2);
		expect(streamClient.isConnected()).toBe(false);
	});
});
