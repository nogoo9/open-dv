/**
 * Behavioral & Integration Tests for `ExplorerDataSource` SPI,
 * Live REST/SSE Data Source, File Export Data Source, and Blocking Error UX.
 *
 * Requirements:
 * - REQ-CFG-01: Zero hardcoded ports or localhost; config-driven API base URL.
 * - REQ-MOCK-01 & REQ-FAIL-01: Zero demo fallback on offline backend; explicit blocking error UX.
 * - Decision D2: Async-only 202 pipeline run creation with live SSE event subscription.
 */

import { describe, expect, it } from "bun:test";
import type {
	CaptainHoldDecisionRequest,
	ExplorerBundleDTO,
	PipelineRunRequest,
	VaultObservabilityEventDTO,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	BlockingErrorBanner,
	type BlockingErrorState,
} from "../src/explorer/BlockingErrorBanner";
import {
	FileExplorerDataSource,
	RestSseExplorerDataSource,
	resolveApiBaseUrl,
} from "../src/explorer/data-source";
import { createExplorerStore } from "../src/explorer/explorer-store";
import {
	GLOBAL_COMMERCE_EXPLORER_BUNDLE,
	PE_DEMO_EVENTS,
} from "./fixtures/commerce-explorer-fixture";

describe("ExplorerDataSource SPI & Live REST/SSE Data Source", () => {
	it("resolves API base URL adhering to REQ-CFG-01 with zero hardcoded ports or localhost", () => {
		// Explicit custom URL takes priority
		expect(resolveApiBaseUrl("https://platform.internal.corp")).toBe(
			"https://platform.internal.corp",
		);
		expect(resolveApiBaseUrl("http://api-gateway:9000/api/v1")).toBe(
			"http://api-gateway:9000/api/v1",
		);

		// Default fallback is relative /api/v1 (zero hardcoded ports or localhost)
		const resolvedDefault = resolveApiBaseUrl();
		expect(resolvedDefault).toBe("/api/v1");
		expect(resolvedDefault).not.toContain("localhost");
		expect(resolvedDefault).not.toContain("127.0.0.1");
		expect(resolvedDefault).not.toContain(":8000");
		expect(resolvedDefault).not.toContain(":8420");
	});

	it("RestSseExplorerDataSource executes typed REST calls for runs, bundles, artifacts, and decisions", async () => {
		const calls: { url: string; method: string; body?: string }[] = [];

		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			const method = init?.method ?? "GET";
			const body = typeof init?.body === "string" ? init.body : undefined;
			calls.push(body !== undefined ? { url, method, body } : { url, method });

			if (url.endsWith("/api/v1/runs")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-test-101",
							project_id: "proj-commerce",
							harness_provider: "antigravity",
							status: "COMPLETED",
							started_at_utc: "2026-11-07T00:00:00Z",
							completed_at_utc: "2026-11-07T00:02:00Z",
							current_stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
							event_count: 5,
							active_hold_ids: [],
							events_sse_url: "/api/v1/runs/run-test-101/events",
							gates_url: "/api/v1/runs/run-test-101/gates",
							sandbox_url: "/api/v1/runs/run-test-101/sandbox",
							decisions_url: "/api/v1/runs/run-test-101/decisions",
							has_initial_contract: true,
							lifecycle_stage: "COLD_START_N1",
						},
					]),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}

			if (url.includes("/api/v1/runs/run-test-101/explorer")) {
				return new Response(
					JSON.stringify({
						...GLOBAL_COMMERCE_EXPLORER_BUNDLE,
						runId: "run-test-101",
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}

			if (url.includes("/api/v1/runs/run-test-101/artifacts/final_contract")) {
				return new Response(
					JSON.stringify({
						artifact_id: "final_contract",
						sha256: "b1".repeat(32),
						format: "yaml",
						content: "contract_version: '5.0'\nname: global_commerce\n",
						size_bytes: 42,
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}

			if (url.endsWith("/api/v1/pipelines/runs") && method === "POST") {
				return new Response(
					JSON.stringify({
						run_id: "run-async-202",
						status: "accepted",
						status_url: "/api/v1/runs/run-async-202",
						events_sse_url: "/api/v1/runs/run-async-202/events",
					}),
					{ status: 202, headers: { "Content-Type": "application/json" } },
				);
			}

			if (
				url.endsWith("/api/v1/runs/run-test-101/decisions") &&
				method === "POST"
			) {
				return new Response(
					JSON.stringify({
						decision_id: "dec-steward-test-01",
						status: "ACCEPTED",
						resumed_run_id: "run-test-101",
						next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
						next_event_id: "evt-test-005",
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}

			return new Response("Not Found", { status: 404 });
		}) as unknown as typeof fetch;

		const liveDs = new RestSseExplorerDataSource({
			baseUrl: "/api/v1",
			fetchImpl: mockFetch,
		});

		expect(liveDs.mode).toBe("live");
		expect(liveDs.sourceName).toContain("Live API");

		// 1. fetchRunsList
		const runs = await liveDs.fetchRunsList();
		expect(runs).toHaveLength(1);
		expect(runs[0]?.run_id).toBe("run-test-101");

		// 2. fetchExplorerBundle with wave filter
		const bundle = await liveDs.fetchExplorerBundle("run-test-101", "W1");
		expect(bundle.runId).toBe("run-test-101");
		expect(calls.some((c) => c.url.includes("wave_id=W1"))).toBe(true);

		// 3. fetchArtifactContent
		const content = await liveDs.fetchArtifactContent(
			"run-test-101",
			"final_contract",
		);
		expect(content).toContain("contract_version: '5.0'");

		// 4. createPipelineRun (Decision D2: 202 Accepted)
		const runRequest: PipelineRunRequest = {
			input_spec_path: "examples/pipeline_samples/input.yaml",
			steward_hold_policy: "pause",
			dry_run: false,
		};
		const handle = await liveDs.createPipelineRun(runRequest);
		expect(handle.run_id).toBe("run-async-202");
		expect(handle.status).toBe("accepted");

		// 5. submitDecision
		const decisionReq: CaptainHoldDecisionRequest = {
			event_id: "evt-001",
			hold_id: "hold-001",
			action: "APPROVE",
			selected_candidate_id: "CAND_01",
		};
		const decisionResp = await liveDs.submitDecision(
			"run-test-101",
			decisionReq,
		);
		expect(decisionResp.decision_id).toBe("dec-steward-test-01");
		expect(decisionResp.status).toBe("ACCEPTED");
	});

	it("RestSseExplorerDataSource subscribes to live SSE events and supports unsubscribe cleanup", () => {
		const createdSources: { url: string; closed: boolean }[] = [];
		const liveDs = new RestSseExplorerDataSource({
			baseUrl: "/api/v1",
			eventSourceFactory: (url: string) => {
				const src = {
					url,
					closed: false,
					addEventListener: () => {},
					close: () => {
						src.closed = true;
					},
					onerror: null,
				};
				createdSources.push(src);
				return src;
			},
		});

		const receivedEvents: VaultObservabilityEventDTO[] = [];
		const unsubscribe = liveDs.subscribeEvents(
			"run-live-123",
			(ev) => receivedEvents.push(ev),
			() => {},
		);

		expect(createdSources).toHaveLength(1);
		expect(createdSources[0]?.url).toContain(
			"/api/v1/runs/run-live-123/events",
		);
		expect(createdSources[0]?.closed).toBe(false);

		// Unsubscribe cleans up event source connection
		unsubscribe();
		expect(createdSources[0]?.closed).toBe(true);
	});
});

describe("FileExplorerDataSource & Exported Run Bundles", () => {
	it("loads exported explorer_bundle.json and marks UI source name", async () => {
		const jsonStr = JSON.stringify(GLOBAL_COMMERCE_EXPLORER_BUNDLE);
		const fileDs = FileExplorerDataSource.fromFileContent(
			"explorer_bundle.json",
			jsonStr,
		);

		expect(fileDs.mode).toBe("file");
		expect(fileDs.sourceName).toBe("Exported Run (explorer_bundle.json)");

		const runs = await fileDs.fetchRunsList();
		expect(runs).toHaveLength(1);
		expect(runs[0]?.run_id).toBe(GLOBAL_COMMERCE_EXPLORER_BUNDLE.runId);

		const bundle = await fileDs.fetchExplorerBundle(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.runId,
		);
		expect(bundle.displayName).toBe(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.displayName,
		);

		const artContent = await fileDs.fetchArtifactContent(
			bundle.runId,
			"final_contract",
		);
		expect(artContent).toBeDefined();

		// Pipeline creation is rejected in read-only file mode
		expect(
			fileDs.createPipelineRun({
				input_spec_path: "input.yaml",
				dry_run: false,
			}),
		).rejects.toThrow("read-only");
	});

	it("loads exported vault-ledger.jsonl and replays offline events", async () => {
		const jsonlLines = PE_DEMO_EVENTS.map((ev) => JSON.stringify(ev)).join(
			"\n",
		);
		const fileDs = FileExplorerDataSource.fromFileContent(
			"vault-ledger.jsonl",
			jsonlLines,
		);

		expect(fileDs.mode).toBe("file");
		expect(fileDs.sourceName).toBe("Exported Run (vault-ledger.jsonl)");

		const bundle = await fileDs.fetchExplorerBundle("any-run");
		expect(bundle.artifacts.ledger).toBeDefined();
		expect(bundle.artifacts.ledger?.name).toBe("vault-ledger.jsonl");

		// Test event replay
		const replayedEvents: VaultObservabilityEventDTO[] = [];
		const unsub = fileDs.subscribeEvents("any-run", (ev) => {
			replayedEvents.push(ev);
		});

		// Wait for microtask/timer event playback
		await new Promise((resolve) => setTimeout(resolve, 10));
		expect(replayedEvents.length).toBeGreaterThanOrEqual(1);
		unsub();
	});

	it("resolves Steward Holds offline in memory and updates artifacts", async () => {
		const fileDs = FileExplorerDataSource.fromBundle(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE,
			"Exported Bundle",
		);

		const decisionRes = await fileDs.submitDecision(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.runId,
			{
				hold_id: "hold-tier-u3-rheinland-precision",
				action: "APPROVE",
				selected_candidate_id: "TAX-DE-800202",
			},
		);

		expect(decisionRes.status).toBe("ACCEPTED");
		expect(decisionRes.decision_id).toContain("hold-tier-u3");

		const bundle = await fileDs.fetchExplorerBundle(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.runId,
		);
		const msaDoc = bundle.artifacts.msa?.doc;
		const extraction = msaDoc?.extractions.find(
			(e) => e.holdId === "hold-tier-u3-rheinland-precision",
		);
		expect(extraction?.resolved).toBe(true);
		expect(extraction?.resolutionAction).toBe("APPROVE");
	});
});

describe("Explicit Blocking Error UX & Zero Demo Fallback (REQ-MOCK-01, REQ-FAIL-01)", () => {
	it("renders structured BlockingErrorBanner with endpoint, cause, Retry button, and Open file action", () => {
		let retryClicked = false;
		let fileOpened: File | null = null;

		const errorState: BlockingErrorState = {
			hasError: true,
			title: "Platform Backend Offline",
			endpoint: "/api/v1/runs",
			status: 502,
			details: "HTTP 502 Bad Gateway: Upstream platform API unreachable.",
			retryAction: () => {
				retryClicked = true;
			},
			openFileAction: (f: File) => {
				fileOpened = f;
			},
		};

		const html = renderToStaticMarkup(
			createElement(BlockingErrorBanner, { error: errorState }),
		);

		// Assert zero demo elements
		expect(html).not.toContain("Global Commerce Vault");
		expect(html).not.toContain("Apex Horizon");

		// Assert structured error content
		expect(html).toContain('role="alert"');
		expect(html).toContain('data-testid="blocking-error-banner"');
		expect(html).toContain("Platform Backend Offline");
		expect(html).toContain("HTTP 502");
		expect(html).toContain("/api/v1/runs");
		expect(html).toContain("Upstream platform API unreachable");
		expect(html).toContain('data-testid="btn-retry"');
		expect(html).toContain("Retry Connection");
		expect(html).toContain('data-testid="btn-open-file"');
		expect(html).toContain("Open exported run...");
		expect(html).toContain("REQ-MOCK-01: Zero demo data fallback");
	});

	it("when backend is offline, store enters blocking error state with zero demo data fallback", async () => {
		// Mock fetch that always rejects (simulating server down / 502 gateway error)
		const failingFetch = (async () => {
			throw new Error("Failed to fetch: Connection refused (ECONNREFUSED)");
		}) as unknown as typeof fetch;

		const liveDs = new RestSseExplorerDataSource({
			baseUrl: "/api/v1",
			fetchImpl: failingFetch,
		});

		const store = createExplorerStore({
			dataSource: liveDs,
			syncBrowserUrl: false,
		});

		// Trigger loadRunsList which fails against offline backend
		const runs = await store.getState().loadRunsList();
		expect(runs).toHaveLength(0);

		const state = store.getState();
		expect(state.blockingError).not.toBeNull();
		expect(state.blockingError?.hasError).toBe(true);
		expect(state.blockingError?.title).toBe("Platform Backend Unreachable");
		expect(state.blockingError?.endpoint).toBe("/api/v1/runs");
		expect(state.blockingError?.details).toContain("ECONNREFUSED");

		// ZERO demo fallback invariant: bundle is empty and contains no commerce demo data
		expect(state.bundle.runId).toBe("");
		expect(state.bundle.displayName).toBe("No active run");
		expect(Object.keys(state.bundle.artifacts)).toHaveLength(0);

		// Now simulate opening an exported run file while in blocking error state
		const jsonStr = JSON.stringify(GLOBAL_COMMERCE_EXPLORER_BUNDLE);
		await store
			.getState()
			.loadFromBundleText("exported_commerce.json", jsonStr);

		// Blocking error is cleared and data is loaded from exported file
		const recoveredState = store.getState();
		expect(recoveredState.blockingError).toBeNull();
		expect(recoveredState.dataSource.mode).toBe("file");
		expect(recoveredState.sourceLabel).toContain("Exported Run");
		expect(recoveredState.bundle.runId).toBe(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.runId,
		);
		expect(recoveredState.bundle.displayName).toBe(
			GLOBAL_COMMERCE_EXPLORER_BUNDLE.displayName,
		);
		expect(Object.keys(recoveredState.bundle.artifacts).length).toBeGreaterThan(
			0,
		);
	});

	it("when single run fetch returns HTTP 504 Gateway Timeout, store sets blocking error", async () => {
		const timeoutFetch = (async () => {
			const err = new Error("Gateway Timeout") as Error & { status?: number };
			err.status = 504;
			throw err;
		}) as unknown as typeof fetch;

		const liveDs = new RestSseExplorerDataSource({
			baseUrl: "/api/v1",
			fetchImpl: timeoutFetch,
		});

		const store = createExplorerStore({
			dataSource: liveDs,
			syncBrowserUrl: false,
		});

		await store.getState().loadRun("run-timeout-999");

		const state = store.getState();
		expect(state.blockingError).not.toBeNull();
		expect(state.blockingError?.status).toBe(504);
		expect(state.blockingError?.endpoint).toContain(
			"/api/v1/runs/run-timeout-999/explorer",
		);
		expect(state.bundle.runId).toBe("");
	});
});
