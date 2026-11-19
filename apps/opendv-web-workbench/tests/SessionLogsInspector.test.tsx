/**
 * M4 Failing-First TDD Tests — Session Agent & Execution Logs Inspector (`07`)
 * (`apps/opendv-web-workbench/tests/SessionLogsInspector.test.tsx`)
 *
 * Expected implementation module (`< 5,000` cl100k_base tokens):
 * - `../src/ontology/wizard/SessionLogsInspector`
 * - Header button `Logs & Traces (<count>)` in `OpenDVWorkbenchExplorer` / `TopBarStatusPills`
 *
 * Covers (`FR-LOG-01..07`, `docs/28 §4.4`, `D21`, `D24`):
 * - Global `Logs & Traces (<count>)` header button opens `SessionLogsInspector`
 * - Contextual `View Stage Logs ↗` pre-filters by `wizard_step` or `stage_id` (`00..06` mapped to `STAGE_00..07`)
 * - Live `EventSource` listening for named `log_entry` and `stream_end` frames (`Last-Event-ID` / `Follow Live Tail` / `Pause Auto-Scroll`)
 * - Search, `stage_id`, `persona`, and `wizard_step` filter controls
 * - Live Cost Meter (`tokens` + `$USD`) + `0 Mocks` + `<MASKED_PII>` status bar
 * - 4-tab Dark Deep Inspector (`Masked Prompt & Context`, `Streaming / Structured LLM Response`, `Tool Calls & @toon_v1 Traces`, `Raw JSON Event`)
 * - `.jsonl` download trigger (`/api/v1/ontology/studio/sessions/{id}/logs/export`)
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import { createExplorerStore } from "../src/explorer/explorer-store";
import {
	SessionLogsInspector,
	connectSessionLogsStream,
	resolvePillToCanonicalStages,
} from "../src/ontology/wizard/SessionLogsInspector";
import {
	PILL_TO_CANONICAL_STAGES,
	buildLogsSnapshotUrl,
	normalizeStageFilterToPill,
} from "../src/ontology/wizard/logs-types";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

const SAMPLE_ENTRIES = [
	{
		seq: 1,
		raw_id: 1,
		timestamp: "2026-11-19T10:00:00Z",
		origin: "studio_journal",
		event: "agent_telemetry",
		wizard_step: 4,
		stage_id: "STAGE_03_SHIP_VAULT_PASS1",
		persona: "@term-scout",
		source_id: "src-sqlite-1",
		job_id: "job-construct-1",
		category: "agent",
		provider: "agy",
		model_id: "gemini-3.8-flash-high",
		duration_ms: 842,
		token_usage: {
			input_tokens: 410,
			output_tokens: 190,
			total_tokens: 600,
			cost_usd: 0.0124,
		},
		masked_prompt: "Analyze holdings schema. Tax ID sample: <MASKED_PII>.",
		structured_or_raw_response: '{"entities": ["PortfolioPosition"]}',
		tool_calls: [{ tool: "fibo_lookup", args: { q: "Position" } }],
		toon_diagnostics: ["@toon_v1: 0 repair attempts required"],
		status: "COMPLETED",
		headline: "@term-scout synthesized 2 entity candidates",
		data: { mode: "agent", mock: false },
	},
	{
		seq: 2,
		raw_id: 2,
		timestamp: "2026-11-19T10:01:00Z",
		origin: "pipeline_ledger",
		event: "STAGE_COMPLETED",
		wizard_step: 6,
		stage_id: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		persona: "@axi-dv",
		source_id: null,
		job_id: "run-w2-002",
		category: "execution",
		provider: "agy",
		model_id: "gemini-4-pro-high",
		duration_ms: 1520,
		token_usage: {
			input_tokens: 600,
			output_tokens: 300,
			total_tokens: 900,
			cost_usd: 0.025,
		},
		masked_prompt: "Verify 4-batch DuckDB invariants DV-DYN-001..015",
		structured_or_raw_response: "15/15 DV-DYN checks PASS",
		tool_calls: [{ tool: "duckdb_sandbox_exec", result: "PASS" }],
		toon_diagnostics: ["@toon_v1: DV-AST-001 clean"],
		status: "COMPLETED",
		headline: "06 Prover completed 15/15 checks",
		data: { stage_pill: "06" },
	},
];

describe("M4: Session Agent & Execution Logs Inspector (FR-LOG-01..07)", () => {
	describe("1. Header Button Integration & Stage Pill Mapping (docs/28 §4.4)", () => {
		it("renders Logs & Traces button in OpenDVWorkbenchExplorer and resolves Step 6 pills 00..06 to STAGE_00..07", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				startView: "ontology",
				syncBrowserUrl: false,
			});

			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(html).toContain('data-testid="btn-logs-traces"');
			expect(html).toContain("Logs &amp; Traces");

			expect(resolvePillToCanonicalStages("00")).toEqual([
				"STAGE_00_ORCHESTRATION",
			]);
			expect(resolvePillToCanonicalStages("03")).toEqual([
				"STAGE_03_SHIP_VAULT_PASS1",
				"STAGE_04_SHIP_VAULT_PASS2",
			]);
			expect(resolvePillToCanonicalStages("05")).toEqual([
				"STAGE_07_DETERMINISTIC_COMPILER",
			]);
			expect(resolvePillToCanonicalStages("06")).toEqual([
				"STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
			]);
			expect(PILL_TO_CANONICAL_STAGES["04"]).toEqual([
				"STAGE_05_AXI_DV_STATIC_GATES",
			]);
			expect(
				normalizeStageFilterToPill("STAGE_06_AXI_DV_DYNAMIC_SANDBOX"),
			).toBe("06");
			expect(normalizeStageFilterToPill("STAGE_03_SHIP_VAULT_PASS1")).toBe(
				"03",
			);
			expect(normalizeStageFilterToPill("STAGE_04_SHIP_VAULT_PASS2")).toBe(
				"03",
			);
			expect(normalizeStageFilterToPill("05")).toBe("05");
			expect(normalizeStageFilterToPill(null)).toBe("all");
			expect(
				buildLogsSnapshotUrl("ses-123", {
					apiUrl: "http://localhost:8420",
					stageFilter: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
					wizardStep: 6,
				}),
			).toBe(
				"http://localhost:8420/api/v1/ontology/studio/sessions/ses-123/logs?limit=500&stage_id=06&wizard_step=6",
			);
		});
	});

	describe("2. Filters, Follow Live Tail, Cost Meter, 0 Mocks & <MASKED_PII> Bar", () => {
		it("normalizes canonical initialStageFilter (e.g. STAGE_06_AXI_DV_DYNAMIC_SANDBOX) to pill 06 in stage select", () => {
			const html = renderToStaticMarkup(
				createElement(SessionLogsInspector, {
					isOpen: true,
					sessionId: "ses-w1-portfolio-001",
					initialStageFilter: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
					entries: SAMPLE_ENTRIES,
					onClose: () => {},
				}),
			);
			expect(html).toMatch(
				/<option[^>]*selected=""[^>]*value="06"|<option[^>]*value="06"[^>]*selected=""/,
			);
		});
		it("renders search, stage/pill, persona, and step filters, Follow Live Tail toggle, Cost Meter, 0 Mocks & <MASKED_PII> badges, and .jsonl export", () => {
			const html = renderToStaticMarkup(
				createElement(SessionLogsInspector, {
					isOpen: true,
					sessionId: "ses-w1-portfolio-001",
					initialStageFilter: "06",
					initialStepFilter: 6,
					entries: SAMPLE_ENTRIES,
					selectedSeq: 1,
					activeDeepTab: "prompt",
					followLiveTail: true,
					onToggleFollowLiveTail: () => {},
					onClose: () => {},
				}),
			);

			expect(html).toContain('data-testid="session-logs-inspector"');
			expect(html).toContain('data-testid="logs-filter-search"');
			expect(html).toContain('data-testid="logs-filter-stage"');
			expect(html).toContain('data-testid="logs-filter-persona"');
			expect(html).toContain('data-testid="logs-filter-step"');
			expect(html).toContain('data-testid="btn-toggle-live-tail"');
			expect(html).toContain("Follow Live Tail");
			expect(html).toContain("0 Mocks");
			expect(html).toContain("&lt;MASKED_PII&gt;");
			expect(html).toContain('data-testid="logs-cost-meter"');
			expect(html).toContain("1,500");
			expect(html).toContain("$0.0374");
			expect(html).toContain('data-testid="btn-download-logs-jsonl"');
			expect(html).toContain(".jsonl");
		});

		it("renders 'Pause Auto-Scroll' when followLiveTail is false", () => {
			const html = renderToStaticMarkup(
				createElement(SessionLogsInspector, {
					isOpen: true,
					sessionId: "ses-w1-portfolio-001",
					entries: SAMPLE_ENTRIES,
					followLiveTail: false,
					onClose: () => {},
				}),
			);
			expect(html).toContain("Pause Auto-Scroll");
		});
	});

	describe("3. 4-Tab Dark Deep Inspector (#181715 / #1f1e1b)", () => {
		it("renders all 4 deep inspector tabs and displays active tab content in dark #181715 chrome", () => {
			const tabs = [
				{
					tab: "prompt" as const,
					expected: "Tax ID sample: &lt;MASKED_PII&gt;",
				},
				{ tab: "response" as const, expected: "PortfolioPosition" },
				{
					tab: "tools" as const,
					expected: "@toon_v1: 0 repair attempts required",
				},
				{ tab: "raw" as const, expected: "job-construct-1" },
			];

			for (const { tab, expected } of tabs) {
				const html = renderToStaticMarkup(
					createElement(SessionLogsInspector, {
						isOpen: true,
						sessionId: "ses-w1-portfolio-001",
						entries: SAMPLE_ENTRIES,
						selectedSeq: 1,
						activeDeepTab: tab,
						onClose: () => {},
					}),
				);
				expect(html).toContain("Masked Prompt &amp; Context");
				expect(html).toContain("Streaming / Structured LLM Response");
				expect(html).toContain("Tool Calls &amp; @toon_v1 Traces");
				expect(html).toContain("Raw JSON Event");
				expect(html.toLowerCase()).toContain("#181715");
				expect(html).toContain(expected);
			}
		});
	});

	describe("4. Live EventSource Named Frames (log_entry & stream_end)", () => {
		it("subscribes to /logs/stream with named log_entry and stream_end EventSource listeners and closes cleanly on stream_end", () => {
			const listeners = new Map<
				string,
				Array<(ev: { data: string; lastEventId?: string }) => void>
			>();
			let closed = false;
			let openedUrl = "";

			class MockEventSource {
				constructor(url: string) {
					openedUrl = url;
				}
				addEventListener(
					eventName: string,
					cb: (ev: { data: string; lastEventId?: string }) => void,
				) {
					const list = listeners.get(eventName) ?? [];
					list.push(cb);
					listeners.set(eventName, list);
				}
				close() {
					closed = true;
				}
			}

			const received: number[] = [];
			let endedStatus = "";
			const origEs = (globalThis as Record<string, unknown>).EventSource;
			(globalThis as Record<string, unknown>).EventSource = MockEventSource;
			try {
				connectSessionLogsStream({
					sessionId: "ses-w1-portfolio-001",
					apiUrl: "http://platform.local",
					stageFilter: "03",
					wizardStep: 4,
					onLogEntry: (entry) => {
						received.push(entry.seq);
					},
					onStreamEnd: (payload) => {
						endedStatus = payload.status;
					},
				});

				expect(openedUrl).toContain(
					"/api/v1/ontology/studio/sessions/ses-w1-portfolio-001/logs/stream",
				);
				expect(openedUrl).toContain("stage_id=03");
				expect(openedUrl).toContain("wizard_step=4");
				expect(listeners.has("log_entry")).toBe(true);
				expect(listeners.has("stream_end")).toBe(true);

				for (const cb of listeners.get("log_entry") ?? []) {
					cb({ data: JSON.stringify(SAMPLE_ENTRIES[0]), lastEventId: "1" });
				}
				expect(received).toEqual([1]);

				for (const cb of listeners.get("stream_end") ?? []) {
					cb({
						data: JSON.stringify({
							status: "COMPLETED",
							session_id: "ses-w1-portfolio-001",
						}),
					});
				}
				expect(endedStatus).toBe("COMPLETED");
				expect(closed).toBe(true);
			} finally {
				(globalThis as Record<string, unknown>).EventSource = origEs;
			}
		});
	});

	describe("5. Reopen Stage Filter Sync Without 1-Frame Stale Render (C9)", () => {
		it("synchronizes stageFilter immediately during render on reopen with a new initialStageFilter before effects flush", async () => {
			const React = await import("react");
			const internals = (
				React as unknown as {
					__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE?: {
						H?: unknown;
					};
				}
			).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
			expect(internals).toBeDefined();
			if (!internals) return;

			const stateSlots: unknown[] = [];
			const refSlots: Array<{ current: unknown }> = [];
			let stateIdx = 0;
			let refIdx = 0;
			let rerenderScheduled = false;

			const dispatcher = {
				useState<T>(initial: T | (() => T)) {
					const idx = stateIdx++;
					if (!(idx in stateSlots)) {
						stateSlots[idx] =
							typeof initial === "function" ? (initial as () => T)() : initial;
					}
					const setState = (val: T | ((prev: T) => T)) => {
						const next =
							typeof val === "function"
								? (val as (prev: T) => T)(stateSlots[idx] as T)
								: val;
						if (!Object.is(stateSlots[idx], next)) {
							stateSlots[idx] = next;
							rerenderScheduled = true;
						}
					};
					return [stateSlots[idx] as T, setState];
				},
				useEffect() {
					// Deferred until after commit (intentionally NOT executed before first frame)
				},
				useRef<T>(initial: T) {
					const idx = refIdx++;
					if (!(idx in refSlots)) {
						refSlots[idx] = { current: initial };
					}
					return refSlots[idx] as { current: T };
				},
				useMemo<T>(factory: () => T) {
					return factory();
				},
			};

			const renderPass = (isOpen: boolean, initialStageFilter: string) => {
				let tree: unknown = null;
				for (let guard = 0; guard < 10; guard += 1) {
					stateIdx = 0;
					refIdx = 0;
					rerenderScheduled = false;
					tree = SessionLogsInspector({
						isOpen,
						sessionId: "ses-w1-portfolio-001",
						initialStageFilter,
						entries: SAMPLE_ENTRIES,
						onClose: () => {},
					});
					if (!rerenderScheduled) break;
				}
				return tree;
			};

			const findTestIdValue = (
				node: unknown,
				testId: string,
			): unknown | undefined => {
				if (!node || typeof node !== "object") return undefined;
				if (Array.isArray(node)) {
					for (const child of node) {
						const found = findTestIdValue(child, testId);
						if (found !== undefined) return found;
					}
					return undefined;
				}
				const el = node as { props?: Record<string, unknown> };
				if (el.props?.["data-testid"] === testId) {
					return el.props.value;
				}
				return el.props?.children
					? findTestIdValue(el.props.children, testId)
					: undefined;
			};

			const prevH = internals.H;
			internals.H = dispatcher;
			try {
				// 1. Open on stage 06
				const treeOpen06 = renderPass(true, "06");
				expect(findTestIdValue(treeOpen06, "logs-filter-stage")).toBe("06");

				// 2. Close while mounted
				const treeClosed = renderPass(false, "06");
				expect(treeClosed).toBeNull();

				// 3. Reopen via stage pill 03 -> first committed frame must already be "03"
				const treeReopen03 = renderPass(true, "03");
				expect(findTestIdValue(treeReopen03, "logs-filter-stage")).toBe("03");
			} finally {
				internals.H = prevH;
			}
		});
	});
});
