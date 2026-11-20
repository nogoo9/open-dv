/**
 * M6 Failing-First TDD Tests — Bug C: Step 6 Console Stage Pills Mount Persistence,
 * handleRunStarted -> loadRun cloneBundle Safety, & Inline Steward Hold (D14)
 * (`apps/opendv-web-workbench/tests/m6-step6-pill-mount.test.tsx`)
 */

import { describe, expect, it } from "bun:test";
import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import { createExplorerStore } from "../src/explorer/explorer-store";
import { studioStore } from "../src/ontology/store/studio-store";
import { OntologyWizardShell } from "../src/ontology/wizard/OntologyWizardShell";
import { Step6PipelineBuildAndRun } from "../src/ontology/wizard/Step6PipelineBuildAndRun";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

interface ReactElementNode {
	type?: unknown;
	props?: Record<string, unknown>;
}

function findElements(
	node: unknown,
	predicate: (el: ReactElementNode) => boolean,
): ReactElementNode[] {
	const matches: ReactElementNode[] = [];
	const walk = (cur: unknown) => {
		if (!cur || typeof cur !== "object") return;
		if (Array.isArray(cur)) {
			for (const item of cur) walk(item);
			return;
		}
		const el = cur as ReactElementNode;
		if (el.props) {
			if (predicate(el)) matches.push(el);
			if (el.props.children !== undefined) walk(el.props.children);
		}
	};
	walk(node);
	return matches;
}

function collectText(node: unknown): string {
	if (node === null || node === undefined || typeof node === "boolean") {
		return "";
	}
	if (typeof node === "string" || typeof node === "number") {
		return String(node);
	}
	if (Array.isArray(node)) {
		return node.map(collectText).join(" ");
	}
	if (typeof node === "object") {
		const el = node as ReactElementNode;
		return el.props?.children ? collectText(el.props.children) : "";
	}
	return "";
}

function mountWithReactHooks(renderFn: () => unknown) {
	const internals = (
		React as unknown as {
			__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE?: {
				H?: unknown;
			};
		}
	).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
	if (!internals) {
		throw new Error("React internals dispatcher unavailable");
	}
	const prevH = internals.H;

	interface FiberBucket {
		stateSlots: unknown[];
		refSlots: Array<{ current: unknown }>;
		effectSlots: Array<{
			deps: readonly unknown[] | undefined;
			cleanup: (() => void) | undefined;
		}>;
		storeSubs: Map<
			number,
			{ subscribe: (cb: () => void) => () => void; unsub?: () => void }
		>;
	}
	const fibers = new Map<string, FiberBucket>();
	let currentFiberKey = "root";
	let stateIdx = 0;
	let refIdx = 0;
	let effectIdx = 0;
	let subIdx = 0;
	let pendingEffects: Array<{
		fiberKey: string;
		slot: number;
		effect: React.EffectCallback;
		deps: readonly unknown[] | undefined;
	}> = [];
	let rerenderScheduled = false;

	const getBucket = (key: string): FiberBucket => {
		let b = fibers.get(key);
		if (!b) {
			b = {
				stateSlots: [],
				refSlots: [],
				effectSlots: [],
				storeSubs: new Map(),
			};
			fibers.set(key, b);
		}
		return b;
	};

	const depsEqual = (
		a: readonly unknown[] | undefined,
		b: readonly unknown[] | undefined,
	): boolean => {
		if (!a || !b || a.length !== b.length) return false;
		return a.every((val, i) => Object.is(val, b[i]));
	};

	const dispatcher = {
		useState<T>(initial: T | (() => T)) {
			const bucket = getBucket(currentFiberKey);
			const idx = stateIdx++;
			if (!(idx in bucket.stateSlots)) {
				bucket.stateSlots[idx] =
					typeof initial === "function" ? (initial as () => T)() : initial;
			}
			const setState = (val: T | ((prev: T) => T)) => {
				const next =
					typeof val === "function"
						? (val as (prev: T) => T)(bucket.stateSlots[idx] as T)
						: val;
				if (!Object.is(bucket.stateSlots[idx], next)) {
					bucket.stateSlots[idx] = next;
					rerenderScheduled = true;
				}
			};
			return [bucket.stateSlots[idx] as T, setState];
		},
		useEffect(effect: React.EffectCallback, deps?: readonly unknown[]) {
			const bucket = getBucket(currentFiberKey);
			const slot = effectIdx++;
			const prev = bucket.effectSlots[slot];
			if (!prev || !depsEqual(prev.deps, deps)) {
				pendingEffects.push({ fiberKey: currentFiberKey, slot, effect, deps });
			}
		},
		useRef<T>(initial: T) {
			const bucket = getBucket(currentFiberKey);
			const idx = refIdx++;
			if (!(idx in bucket.refSlots)) {
				bucket.refSlots[idx] = { current: initial };
			}
			return bucket.refSlots[idx] as { current: T };
		},
		useMemo<T>(factory: () => T) {
			return factory();
		},
		useCallback<T>(fn: T) {
			return fn;
		},
		useId() {
			return "test-id";
		},
		useSyncExternalStore<T>(
			subscribe: (cb: () => void) => () => void,
			getSnapshot: () => T,
		): T {
			const bucket = getBucket(currentFiberKey);
			const idx = subIdx++;
			const existing = bucket.storeSubs.get(idx);
			if (!existing || existing.subscribe !== subscribe) {
				existing?.unsub?.();
				const unsub = subscribe(() => {
					rerenderScheduled = true;
				});
				bucket.storeSubs.set(idx, { subscribe, unsub });
			}
			return getSnapshot();
		},
	};

	const expandWithFibers = (node: unknown, path = "0", depth = 0): unknown => {
		if (depth > 25 || !node || typeof node !== "object") return node;
		if (Array.isArray(node)) {
			return node.map((item, i) =>
				expandWithFibers(item, `${path}.${i}`, depth + 1),
			);
		}
		const el = node as ReactElementNode;
		if (typeof el.type === "function") {
			const compName =
				(el.type as { name?: string }).name || "AnonymousComponent";
			currentFiberKey = `${path}:${compName}`;
			stateIdx = 0;
			refIdx = 0;
			effectIdx = 0;
			subIdx = 0;
			const rendered = (el.type as (props: Record<string, unknown>) => unknown)(
				el.props ?? {},
			);
			return expandWithFibers(rendered, currentFiberKey, depth + 1);
		}
		if (el.props && el.props.children !== undefined) {
			return {
				...el,
				props: {
					...el.props,
					children: expandWithFibers(
						el.props.children,
						`${path}.${String(el.type ?? "el")}`,
						depth + 1,
					),
				},
			};
		}
		return el;
	};

	const render = (): unknown => {
		internals.H = dispatcher;
		let tree: unknown = null;
		for (let guard = 0; guard < 12; guard += 1) {
			pendingEffects = [];
			rerenderScheduled = false;
			tree = expandWithFibers(renderFn());
			const toRun = [...pendingEffects];
			pendingEffects = [];
			for (const item of toRun) {
				const bucket = getBucket(item.fiberKey);
				bucket.effectSlots[item.slot]?.cleanup?.();
				const cleanup = item.effect();
				bucket.effectSlots[item.slot] = {
					deps: item.deps,
					cleanup: typeof cleanup === "function" ? cleanup : undefined,
				};
			}
			if (!rerenderScheduled) break;
		}
		return tree;
	};

	const unmount = () => {
		for (const bucket of fibers.values()) {
			for (const slot of bucket.effectSlots) {
				slot?.cleanup?.();
			}
			for (const sub of bucket.storeSubs.values()) {
				sub.unsub?.();
			}
		}
		internals.H = prevH;
	};

	return { render, unmount };
}

describe("M6 Bug C: Step 6 Console Pills Mount Persistence, cloneBundle Safety & Inline Steward Hold", () => {
	it("keeps Step 6 [data-testid='console-stage-pill-00']..06 mounted and shows COMPLETED when session load resolves during Step 6 run -> handleRunStarted -> run_complete", async () => {
		studioStore.getState().reset();

		const listeners = new Map<
			string,
			Array<(ev: unknown) => Promise<void> | void>
		>();
		const origEs = (globalThis as Record<string, unknown>).EventSource;
		class MockEs {
			readonly url: string;
			constructor(url: string) {
				this.url = url;
			}
			addEventListener(
				type: string,
				cb: (ev: unknown) => Promise<void> | void,
			) {
				const arr = listeners.get(type) ?? [];
				arr.push(cb);
				listeners.set(type, arr);
			}
			close() {}
		}
		(globalThis as Record<string, unknown>).EventSource = MockEs;

		const chunkResolvers: Array<(res: Response) => void> = [];
		let sessionDetailCalls = 0;
		const sessionObj = {
			session_id: "s-w2-step6-bugc",
			domain_namespace: "wealth_mgmt",
			enterprise_owner: "Enterprise",
			status: "COMMITTED",
			active_version: "1.1.0",
			head_sha256:
				"4846b336e707249bdfe7bad1197c485809a6cb884156a072ca5c604841238f6c",
			created_at: "2026-11-19T00:00:00Z",
			updated_at: "2026-11-19T01:00:00Z",
			wizard_schema_version: 2,
		};

		const stepReports = [
			{ stage_id: "STAGE_00_ORCHESTRATION", status: "PASS", verdict: "PASS" },
			{ stage_id: "STAGE_01_SCOUT_SOURCE", status: "PASS", verdict: "PASS" },
			{
				stage_id: "STAGE_02_SCOUT_UNSTRUCTURED",
				status: "PASS",
				verdict: "PASS",
			},
			{
				stage_id: "STAGE_03_SHIP_VAULT_PASS1",
				status: "PASS",
				verdict: "PASS",
			},
			{
				stage_id: "STAGE_04_SHIP_VAULT_PASS2",
				status: "PASS",
				verdict: "PASS",
			},
			{
				stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
				status: "AUTO_REPAIRED",
				verdict: "PASS",
			},
			{
				stage_id: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
				status: "PASS",
				verdict: "PASS",
			},
			{
				stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
				status: "PASS",
				verdict: "PASS",
			},
		];

		const mockFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes("/api/v1/ontology/studio/sessions?wizard_only=true")) {
				return new Response(
					JSON.stringify({ sessions: [sessionObj], legacy_session_count: 0 }),
					{ status: 200 },
				);
			}
			if (
				url.endsWith(
					`/api/v1/ontology/studio/sessions/${sessionObj.session_id}`,
				)
			) {
				sessionDetailCalls += 1;
				return new Response(JSON.stringify(sessionObj), { status: 200 });
			}
			if (url.includes("/evidence/chunks")) {
				return new Promise<Response>((resolve) => {
					chunkResolvers.push(resolve);
				});
			}
			if (url.includes("/sources")) {
				return new Response(
					JSON.stringify({
						sources: [
							{
								source_id: "src-w2-custody",
								source_sha256: "a".repeat(64),
								file_name: "ds02_w2_custody.csv",
								size_bytes: 512,
								mime_type: "text/csv",
								do_not_send_to_agent: false,
							},
						],
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs") && !url.includes("run-w2-bugc")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-w1-baseline",
							session_id: sessionObj.session_id,
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T00:30:00Z",
						},
					]),
					{ status: 200 },
				);
			}
			if (url.includes("/export-blueprint")) {
				return new Response(
					JSON.stringify({ blueprint: { domain_namespace: "wealth_mgmt" } }),
					{ status: 200 },
				);
			}
			if (url.endsWith("/api/v1/pipelines/runs")) {
				return new Response(JSON.stringify({ run_id: "run-w2-bugc" }), {
					status: 202,
				});
			}
			if (url.includes("/api/v1/runs/run-w2-bugc/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: stepReports,
						four_zone_sql: {
							databricks: {
								zone_0_staging: "CREATE SCHEMA IF NOT EXISTS cat.staging;",
							},
						},
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-w2-bugc/explorer")) {
				const { waves: _omitted, ...bundleWithoutWaves } =
					GLOBAL_COMMERCE_EXPLORER_BUNDLE;
				return new Response(
					JSON.stringify({ ...bundleWithoutWaves, runId: "run-w2-bugc" }),
					{ status: 200 },
				);
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		const explorerStore = createExplorerStore({
			startView: "ontology",
			syncBrowserUrl: false,
			dataSource: {
				mode: "live",
				sourceName: "Mock Live",
				fetchRunsList: async () => [],
				fetchExplorerBundle: async (runId) => {
					const { waves: _omitted, ...bundleWithoutWaves } =
						GLOBAL_COMMERCE_EXPLORER_BUNDLE;
					return {
						...bundleWithoutWaves,
						runId,
					} as typeof GLOBAL_COMMERCE_EXPLORER_BUNDLE;
				},
				fetchArtifactContent: async () => "",
				createPipelineRun: async () => ({
					run_id: "run-w2-bugc",
					status: "RUNNING",
					created_at_utc: "2026-11-19T00:00:00Z",
					status_url: "/api/v1/runs/run-w2-bugc",
					events_sse_url: "/api/v1/runs/run-w2-bugc/events",
					event_stream_url: "/api/v1/runs/run-w2-bugc/events",
					has_initial_contract: false,
					lifecycle_stage: "COLD_START_N1",
				}),
				submitDecision: async () => ({
					decision_id: "dec-1",
					status: "ACCEPTED",
					resumed_run_id: "run-w2-bugc",
					next_stage_id: "STAGE_02",
					next_event_id: "evt-1",
				}),
				subscribeEvents: () => () => {},
			},
		});

		const mounted = mountWithReactHooks(() =>
			React.createElement(OntologyWizardShell, {
				sessionId: studioStore.getState().activeSession?.session_id ?? null,
				initialStep: 1,
				fetchImpl: mockFetch,
				onRunStarted: (handle) => {
					if (handle.run_id) {
						void explorerStore.getState().loadRun(handle.run_id);
					}
				},
			}),
		);

		try {
			// Seed Step 1 with the session button (simulating loaded sessions)
			studioStore.setState({ sessions: [sessionObj] });
			let tree = mounted.render();

			// 1. User clicks session in Step 1 (selectSession starts async fetches, including slow /evidence-chunks)
			const selectBtn = findElements(
				tree,
				(el) =>
					el.props?.["data-testid"] ===
					`btn-select-session-${sessionObj.session_id}`,
			)[0];
			expect(selectBtn).toBeDefined();
			void (selectBtn?.props?.onClick as () => void)();

			// 2. User/UAT immediately clicks Step 6 in the stepper while selectSession is still loading chunks
			const step6Btn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "wizard-stepper-step-6",
			)[0];
			expect(step6Btn).toBeDefined();
			(step6Btn?.props?.onClick as () => void)();
			tree = mounted.render();
			expect(
				findElements(
					tree,
					(el) => el.props?.["data-testid"] === "console-stage-pill-00",
				),
			).toHaveLength(1);

			// 3. User clicks Run 5-Zone Vault Pipeline in Step 6 (triggers startDvPipelineRun + handleRunStarted -> loadRun)
			const runBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-start-pipeline-run",
			)[0];
			expect(runBtn).toBeDefined();
			await (runBtn?.props?.onClick as () => Promise<void>)();
			await new Promise((r) => setTimeout(r, 15));
			tree = mounted.render();

			// 4. Slow /evidence/chunks resolves AFTER the Step 6 run has started!
			for (const resFn of chunkResolvers) {
				resFn(
					new Response(
						JSON.stringify({ chunks: [], observed_term_counts: {} }),
						{ status: 200 },
					),
				);
			}
			await new Promise((r) => setTimeout(r, 25));
			tree = mounted.render();

			// Assert Step 6 did NOT jump back to Step 2 and console pills remain mounted
			expect(
				findElements(
					tree,
					(el) => el.props?.["data-testid"] === "wizard-step-6",
				),
			).toHaveLength(1);
			expect(
				findElements(
					tree,
					(el) => el.props?.["data-testid"] === "wizard-step-2",
				),
			).toHaveLength(0);
			expect(sessionDetailCalls).toBe(1);

			// 5. Emit run_complete and assert all 7 console-stage-pill-00..06 stay mounted & show COMPLETED
			for (const cb of listeners.get("run_complete") ?? []) {
				await cb({ data: JSON.stringify({ status: "COMPLETED" }) });
			}
			tree = mounted.render();

			for (const pill of ["00", "01", "02", "03", "04", "05", "06"]) {
				const pillEl = findElements(
					tree,
					(el) => el.props?.["data-testid"] === `console-stage-pill-${pill}`,
				)[0];
				expect(pillEl).toBeDefined();
				expect(collectText(pillEl)).toContain("COMPLETED");
			}
		} finally {
			mounted.unmount();
			(globalThis as Record<string, unknown>).EventSource = origEs;
			studioStore.getState().reset();
		}
	});

	it("safely ingests live SSE events via handleRunStarted -> loadRun without TypeError when backend /explorer omits waves", async () => {
		const { waves: _omitted, ...bundleWithoutWaves } =
			GLOBAL_COMMERCE_EXPLORER_BUNDLE;
		const store = createExplorerStore({
			startView: "ontology",
			syncBrowserUrl: false,
			dataSource: {
				mode: "live",
				sourceName: "Live Backend",
				fetchRunsList: async () => [],
				fetchExplorerBundle: async (runId) =>
					({
						...bundleWithoutWaves,
						runId,
					}) as typeof GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				fetchArtifactContent: async () => "",
				createPipelineRun: async () => ({
					run_id: "run-no-waves",
					status: "RUNNING",
					created_at_utc: "2026-11-19T00:00:00Z",
					status_url: "/api/v1/runs/run-no-waves",
					events_sse_url: "/api/v1/runs/run-no-waves/events",
					event_stream_url: "/api/v1/runs/run-no-waves/events",
					has_initial_contract: false,
					lifecycle_stage: "COLD_START_N1",
				}),
				submitDecision: async () => ({
					decision_id: "dec-1",
					status: "ACCEPTED",
					resumed_run_id: "run-no-waves",
					next_stage_id: "STAGE_01",
					next_event_id: "evt-1",
				}),
				subscribeEvents: (_runId, onEvent) => {
					const evt: VaultObservabilityEventDTO = {
						event_id: "evt-nowaves-1",
						run_id: "run-no-waves",
						trace_id: "tr-1",
						span_id: "sp-1",
						parent_span_id: null,
						timestamp_utc: "2026-11-19T00:00:01Z",
						stage_id: "STAGE_01_SCOUT_SOURCE",
						agent_persona: "scout-source",
						status: "PASS",
						data_classification: "INTERNAL",
						rule_ids_evaluated: ["DV-STR-001"],
						layer1_story_banner: {
							headline: "Stage 01 PASS",
							business_impact: "Scouted feeds",
							verdict_pill: "PASS",
						},
						layer2_visual_payload: {
							semantic_hash_pills: [],
							summary_metrics: {},
						},
					};
					onEvent(evt);
					return () => {};
				},
			},
		});

		await store.getState().loadRun("run-no-waves");
		expect(store.getState().activeRunId).toBe("run-no-waves");
		expect(store.getState().bundle.stages[1]?.tag).toBe("PASS");

		const html = renderToStaticMarkup(
			React.createElement(OpenDVWorkbenchExplorer, { store }),
		);
		expect(html).toContain('data-testid="ontology-wizard-shell"');
	});

	it("hydrates inline StewardHoldModal in Step 6 with dec-* hold_id and reason from /decisions on event: hold (D14)", async () => {
		studioStore.getState().reset();
		studioStore.setState({
			activeSession: {
				session_id: "s-hold-step6",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Enterprise",
				status: "COMMITTED",
				active_version: "1.1.0",
				head_sha256: "b".repeat(64),
				created_at: "2026-11-19T00:00:00Z",
				updated_at: "2026-11-19T01:00:00Z",
				wizard_schema_version: 2,
			},
		});

		const listeners = new Map<
			string,
			Array<(ev: unknown) => Promise<void> | void>
		>();
		const origEs = (globalThis as Record<string, unknown>).EventSource;
		class MockEs {
			addEventListener(
				type: string,
				cb: (ev: unknown) => Promise<void> | void,
			) {
				const arr = listeners.get(type) ?? [];
				arr.push(cb);
				listeners.set(type, arr);
			}
			close() {}
		}
		(globalThis as Record<string, unknown>).EventSource = MockEs;

		try {
			const mockFetch = (async (input: RequestInfo | URL) => {
				const url = String(input);
				if (url.includes("/export-blueprint")) {
					return new Response(
						JSON.stringify({ blueprint: { domain: "wealth_mgmt" } }),
						{ status: 200 },
					);
				}
				if (url.endsWith("/api/v1/pipelines/runs")) {
					return new Response(JSON.stringify({ run_id: "run-hold-d14" }), {
						status: 202,
					});
				}
				if (url.includes("/api/v1/runs/run-hold-d14/decisions")) {
					return new Response(
						JSON.stringify({
							active_holds: [
								{
									hold_id: "dec-2f891af1b1",
									stage_id: "STAGE_01_SCOUT_SOURCE",
									reason: "DV-G4-001: Unmapped table 'ds02_w2_custody'",
									status: "PENDING",
								},
							],
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			await studioStore.getState().startDvPipelineRun(undefined, mockFetch);
			for (const cb of listeners.get("hold") ?? []) {
				await cb({
					data: JSON.stringify({
						status: "PAUSED_HOLD",
						run_id: "run-hold-d14",
					}),
				});
			}

			const state = studioStore.getState();
			expect(state.activeHold?.hold_id).toBe("dec-2f891af1b1");
			expect(state.activeHold?.stage_id).toBe("STAGE_01_SCOUT_SOURCE");
			expect(state.activeHold?.headline).toContain(
				"DV-G4-001: Unmapped table 'ds02_w2_custody'",
			);

			const html = renderToStaticMarkup(
				React.createElement(Step6PipelineBuildAndRun, {
					sessionId: "s-hold-step6",
					activeRunId: state.activeRunId,
					activeHold: state.activeHold,
					stageStatuses: state.stageStatuses,
				}),
			);
			expect(html).toContain('data-testid="steward-hold-modal"');
			expect(html).toContain("dec-2f891af1b1");
			expect(html).toContain("DV-G4-001: Unmapped table");
		} finally {
			(globalThis as Record<string, unknown>).EventSource = origEs;
			studioStore.getState().reset();
		}
	});
});
