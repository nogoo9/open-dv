/**
 * M6 Gen2 Failing-First TDD Tests — Step 6 Cold-Reload Run Hydration,
 * Dark-Chrome #181715 5-Zone SQL Preview + Dialect Toggle, & Gate 0–6 Badges
 * (`apps/opendv-web-workbench/tests/m6-step6-cold-reload.test.tsx`)
 */

import { describe, expect, it } from "bun:test";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CLAUDE_TOKENS } from "../src/explorer/design-tokens";
import { studioStore } from "../src/ontology/store/studio-store";
import { OntologyWizardShell } from "../src/ontology/wizard/OntologyWizardShell";
import { Step6PipelineBuildAndRun } from "../src/ontology/wizard/Step6PipelineBuildAndRun";

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

const SESSION_W2 = {
	session_id: "s-115ebe9242732b7f",
	domain_namespace: "wealth_mgmt",
	enterprise_owner: "Enterprise",
	status: "COMMITTED" as const,
	active_version: "1.1.0",
	head_sha256:
		"4846b336e707249bdfe7bad1197c485809a6cb884156a072ca5c604841238f6c",
	created_at: "2026-11-19T18:00:00Z",
	updated_at: "2026-11-19T23:04:12Z",
	wizard_schema_version: 2,
};

const EIGHT_STAGE_REPORTS = [
	{ stage_id: "STAGE_00_ORCHESTRATION", status: "PASS", verdict: "PASS" },
	{ stage_id: "STAGE_01_SCOUT_SOURCE", status: "PASS", verdict: "PASS" },
	{ stage_id: "STAGE_02_SCOUT_UNSTRUCTURED", status: "PASS", verdict: "PASS" },
	{ stage_id: "STAGE_03_SHIP_VAULT_PASS1", status: "PASS", verdict: "PASS" },
	{ stage_id: "STAGE_04_SHIP_VAULT_PASS2", status: "PASS", verdict: "PASS" },
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

const SAMPLE_GATE_SCORECARD = {
	gate_0_profile_drift: "PASS",
	gate_1_contract_grain: "PASS",
	gate_2_sqlglot_ast: "PASS",
	gate_3_networkx_dag: "PASS",
	gate_4_yagni_cost_guard: "AUTO-FIX",
	gate_5_duckdb_sandbox: "PASS",
	gates_passed: true,
	shadow_passed: true,
	sandbox_passed: true,
	four_zone_passed: true,
	sandbox_summary: {
		passed: true,
		dynamic_checks_passed: 15,
		dynamic_checks_total: 15,
		dynamic_rule_ids: ["DV-DYN-001", "DV-DYN-015"],
	},
};

const SAMPLE_FOUR_ZONE_SQL = {
	duckdb: {
		zone_0_staging:
			"CREATE SCHEMA IF NOT EXISTS raw_staging;\nCREATE OR REPLACE VIEW raw_staging.stg_custody AS SELECT 1;",
		zone_1_raw_vault:
			"CREATE SCHEMA IF NOT EXISTS raw_vault;\nCREATE TABLE IF NOT EXISTS raw_vault.hub_portfolio (hk_portfolio VARCHAR);",
		zone_2_business_vault:
			"CREATE SCHEMA IF NOT EXISTS business_vault;\nCREATE TABLE IF NOT EXISTS business_vault.pit_portfolio (hk_portfolio VARCHAR);",
		zone_3_info_mart:
			"CREATE SCHEMA IF NOT EXISTS information_mart;\nCREATE OR REPLACE VIEW information_mart.vw_portfolio AS SELECT 1;",
		all_zones:
			"-- DuckDB 5-Zone Bundle\nCREATE SCHEMA IF NOT EXISTS information_mart;",
	},
	databricks: {
		zone_0_staging:
			"CREATE SCHEMA IF NOT EXISTS main.raw_staging;\nCREATE OR REPLACE VIEW main.raw_staging.stg_custody AS SELECT 1;",
		zone_1_raw_vault: "",
		zone_2_business_vault:
			"CREATE TABLE IF NOT EXISTS main.business_vault.pit_portfolio (hk_portfolio STRING) USING DELTA;",
		zone_3_info_mart: "",
		all_zones:
			"-- Databricks Delta 5-Zone Bundle\nCREATE SCHEMA IF NOT EXISTS main.information_mart;",
	},
};

describe("M6 Gen2: Step 6 Cold-Reload Hydration, Dark-Chrome 5-Zone SQL & Gate 0–6 Badges", () => {
	it("1. Cold-reload hydration (selectSession & hydrateSessionLatestRun) populates activeRunId, 7 COMPLETED pills, topologyRows, dual-dialect zoneSqlByDialect, gateScorecard, and independent D12 baseline", async () => {
		studioStore.getState().reset();
		const mockFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (
				url.endsWith(
					`/api/v1/ontology/studio/sessions/${SESSION_W2.session_id}`,
				)
			) {
				return new Response(JSON.stringify(SESSION_W2), { status: 200 });
			}
			if (url.endsWith("/api/v1/runs")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-other-session-newest",
							session_id: "s-other-999",
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T23:10:00Z",
						},
						{
							run_id: "run-4804789955aa",
							session_id: SESSION_W2.session_id,
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T23:04:12Z",
						},
						{
							run_id: "run-9a9fac4b576e",
							session_id: SESSION_W2.session_id,
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T19:07:32Z",
						},
					]),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-4804789955aa/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: EIGHT_STAGE_REPORTS,
						four_zone_sql: SAMPLE_FOUR_ZONE_SQL,
						gate_scorecard: SAMPLE_GATE_SCORECARD,
						blueprint: {
							contract: {
								hubs: [
									{
										name: "hub_portfolio",
										source_mappings: ["ds02_w1_holdings.portfolio_id"],
									},
								],
								links: [
									{
										name: "lnk_portfolio_custodian",
										source_mappings: ["ds02_w2_custody.custodian_id"],
									},
								],
								satellites: [
									{
										name: "sat_portfolio_nav",
										parent_hub: "hub_portfolio",
									},
								],
								reference_tables: [
									{
										name: "ref_currency_iso",
										source_mappings: ["ds02_w1_holdings.ccy"],
									},
								],
							},
						},
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/sources")) {
				return new Response(JSON.stringify({ sources: [] }), { status: 200 });
			}
			if (url.includes("/clarifications")) {
				return new Response(JSON.stringify({ clarifications: [] }), {
					status: 200,
				});
			}
			if (url.includes("/proposals")) {
				return new Response(JSON.stringify({ proposals: [] }), { status: 200 });
			}
			if (url.includes("/evidence/tables")) {
				return new Response(JSON.stringify({ tables: [] }), { status: 200 });
			}
			if (url.includes("/evidence/chunks")) {
				return new Response(
					JSON.stringify({ chunks: [], observed_term_counts: {} }),
					{ status: 200 },
				);
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		try {
			await studioStore
				.getState()
				.selectSession(SESSION_W2.session_id, mockFetch);
			await studioStore.getState().loadPipelineBaselines(mockFetch);

			const st = studioStore.getState() as unknown as Record<string, unknown>;
			expect(st.activeRunId).toBe("run-4804789955aa");
			expect(st.selectedBaselineRunId).toBe("run-4804789955aa");
			expect(Array.isArray(st.topologyRows)).toBe(true);
			expect((st.topologyRows as unknown[]).length).toBe(4);
			const dialSql = st.zoneSqlByDialect as
				| {
						duckdb?: Record<string, string>;
						databricks?: Record<string, string>;
				  }
				| undefined;
			expect(dialSql?.duckdb?.Z0).toContain("raw_staging.stg_custody");
			expect(dialSql?.duckdb?.Z3).toContain("information_mart.vw_portfolio");
			expect(dialSql?.databricks?.Z2).toContain("USING DELTA");
			expect(st.gateScorecard).toMatchObject({
				gate_0_profile_drift: "PASS",
				gate_4_yagni_cost_guard: "AUTO-FIX",
			});

			const html = renderToStaticMarkup(
				React.createElement(OntologyWizardShell, {
					sessionId: SESSION_W2.session_id,
					initialStep: 6,
					fetchImpl: mockFetch,
				}),
			);
			expect(html).toContain("run-4804789955aa");
			expect(html).toContain("Derived Data Vault 2.0 Topology (4)");
			expect(html).toContain("hub_portfolio");
			for (const pill of ["00", "01", "02", "03", "04", "05", "06"]) {
				expect(html).toContain(`data-testid="console-stage-pill-${pill}"`);
			}
			expect(html).not.toContain("00 · PENDING");
			expect(html).toContain('data-testid="step6-gate-badges"');
		} finally {
			studioStore.getState().reset();
		}
	});

	it("2. Isolates Step 6 state when switching to a session with 0 runs and rehydrates activeHold when latest run is PAUSED_HOLD", async () => {
		studioStore.getState().reset();
		const sessionEmpty = {
			...SESSION_W2,
			session_id: "s-empty-0001",
		};
		const sessionHold = {
			...SESSION_W2,
			session_id: "s-hold-rehydrate",
		};

		const mockFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.endsWith(`/sessions/${SESSION_W2.session_id}`)) {
				return new Response(JSON.stringify(SESSION_W2), { status: 200 });
			}
			if (url.endsWith(`/sessions/${sessionEmpty.session_id}`)) {
				return new Response(JSON.stringify(sessionEmpty), { status: 200 });
			}
			if (url.endsWith(`/sessions/${sessionHold.session_id}`)) {
				return new Response(JSON.stringify(sessionHold), { status: 200 });
			}
			if (url.endsWith("/api/v1/runs")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-4804789955aa",
							session_id: SESSION_W2.session_id,
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T23:04:12Z",
						},
						{
							run_id: "run-hold-rehydrate",
							session_id: sessionHold.session_id,
							status: "PAUSED_HOLD",
							started_at_utc: "2026-11-19T23:08:00Z",
						},
					]),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-4804789955aa/report")) {
				return new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: EIGHT_STAGE_REPORTS,
						four_zone_sql: SAMPLE_FOUR_ZONE_SQL,
						gate_scorecard: SAMPLE_GATE_SCORECARD,
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-hold-rehydrate/report")) {
				return new Response(
					JSON.stringify({
						status: "PAUSED_HOLD",
						step_reports: [
							{
								stage_id: "STAGE_00_ORCHESTRATION",
								status: "PASS",
								verdict: "PASS",
							},
							{
								stage_id: "STAGE_04_SHIP_VAULT_PASS2",
								status: "PAUSED_HOLD",
								verdict: "HOLD",
							},
						],
					}),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-hold-rehydrate/decisions")) {
				return new Response(
					JSON.stringify({
						active_holds: [
							{
								hold_id: "dec-cold-hold-1",
								stage_id: "STAGE_04_SHIP_VAULT_PASS2",
								reason: "DV-G4-002: Ambiguous custody link",
								status: "PENDING",
							},
						],
					}),
					{ status: 200 },
				);
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		try {
			await studioStore
				.getState()
				.selectSession(SESSION_W2.session_id, mockFetch);
			expect(studioStore.getState().activeRunId).toBe("run-4804789955aa");

			// Switching to session with 0 runs must clear Step 6 state
			await studioStore
				.getState()
				.selectSession(sessionEmpty.session_id, mockFetch);
			const emptySt = studioStore.getState() as unknown as Record<
				string,
				unknown
			>;
			expect(emptySt.activeRunId).toBeNull();
			expect(emptySt.topologyRows).toEqual([]);
			expect(emptySt.stageStatuses).toEqual({});
			expect(emptySt.zoneSqlByZone).toEqual({});
			expect(emptySt.gateScorecard).toBeNull();
			expect(emptySt.activeHold).toBeNull();

			// Switching to session whose latest run is PAUSED_HOLD must hydrate activeHold
			await studioStore
				.getState()
				.selectSession(sessionHold.session_id, mockFetch);
			const holdSt = studioStore.getState();
			expect(holdSt.activeRunId).toBe("run-hold-rehydrate");
			expect(holdSt.activeHold?.hold_id).toBe("dec-cold-hold-1");
			expect(holdSt.activeHold?.headline).toContain(
				"DV-G4-002: Ambiguous custody link",
			);

			const holdHtml = renderToStaticMarkup(
				React.createElement(Step6PipelineBuildAndRun, {
					sessionId: sessionHold.session_id,
					activeRunId: holdSt.activeRunId,
					activeHold: holdSt.activeHold,
					stageStatuses: holdSt.stageStatuses,
				}),
			);
			expect(holdHtml).toContain('data-testid="steward-hold-modal"');
			expect(holdHtml).toContain("dec-cold-hold-1");
		} finally {
			studioStore.getState().reset();
		}
	});

	it("3. Race guard prevents slow in-flight cold hydration from overwriting active startDvPipelineRun", async () => {
		studioStore.getState().reset();
		const sessionRace = { ...SESSION_W2, session_id: "s-race-1" };
		studioStore.setState({ activeSession: sessionRace });

		let resolveSlowReport: ((res: Response) => void) | null = null;
		const slowHydrateFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.endsWith("/api/v1/runs")) {
				return new Response(
					JSON.stringify([
						{
							run_id: "run-old-slow",
							session_id: sessionRace.session_id,
							status: "COMPLETED",
							completed_at_utc: "2026-11-19T18:00:00Z",
						},
					]),
					{ status: 200 },
				);
			}
			if (url.includes("/api/v1/runs/run-old-slow/report")) {
				return new Promise<Response>((resolve) => {
					resolveSlowReport = resolve;
				});
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		const fastRunFetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes("/export-blueprint")) {
				return new Response(JSON.stringify({ blueprint: { domain: "w" } }), {
					status: 200,
				});
			}
			if (url.endsWith("/api/v1/pipelines/runs")) {
				return new Response(JSON.stringify({ run_id: "run-new-fast" }), {
					status: 202,
				});
			}
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		const origEs = (globalThis as Record<string, unknown>).EventSource;
		class MockEs {
			addEventListener() {}
			close() {}
		}
		(globalThis as Record<string, unknown>).EventSource = MockEs;

		try {
			const hydrateFn = (
				studioStore.getState() as unknown as {
					hydrateSessionLatestRun?: (
						fetchImpl?: typeof fetch,
						targetSessionId?: string,
					) => Promise<void>;
				}
			).hydrateSessionLatestRun;
			expect(typeof hydrateFn).toBe("function");

			const hydratePromise = hydrateFn?.(
				slowHydrateFetch,
				sessionRace.session_id,
			);
			await new Promise((r) => setTimeout(r, 10));

			const newRunId = await studioStore
				.getState()
				.startDvPipelineRun(undefined, fastRunFetch);
			expect(newRunId).toBe("run-new-fast");

			// Now resolve the slow cold-reload report
			resolveSlowReport?.(
				new Response(
					JSON.stringify({
						status: "COMPLETED",
						step_reports: EIGHT_STAGE_REPORTS,
						four_zone_sql: SAMPLE_FOUR_ZONE_SQL,
					}),
					{ status: 200 },
				),
			);
			await hydratePromise;

			expect(studioStore.getState().activeRunId).toBe("run-new-fast");
			expect(studioStore.getState().isRunningPipeline).toBe(true);
			expect(studioStore.getState().stageStatuses.STAGE_00_ORCHESTRATION).toBe(
				"RUNNING",
			);
		} finally {
			(globalThis as Record<string, unknown>).EventSource = origEs;
			studioStore.getState().reset();
		}
	});

	it("4. Renders dark-chrome #181715 / #1f1e1b 5-Zone SQL preview card, information_mart (never 4-Zone/marts.), and toggles DuckDB / Databricks Delta skipping empty zones", () => {
		const zoneSqlByDialect = {
			duckdb: {
				Z0: "CREATE SCHEMA IF NOT EXISTS raw_staging;",
				Z1: "CREATE TABLE IF NOT EXISTS raw_vault.hub_portfolio (hk VARCHAR);",
				Z2: "CREATE TABLE IF NOT EXISTS business_vault.pit_portfolio (hk VARCHAR);",
				Z3: "CREATE OR REPLACE VIEW information_mart.vw_portfolio AS SELECT 1;",
				Z4: "-- DuckDB all zones",
			},
			databricks: {
				Z0: "CREATE SCHEMA IF NOT EXISTS main.raw_staging;",
				Z2: "CREATE TABLE IF NOT EXISTS main.business_vault.pit_portfolio (hk STRING) USING DELTA;",
				Z4: "-- Databricks all zones",
			},
		};

		const mounted = mountWithReactHooks(() =>
			React.createElement(Step6PipelineBuildAndRun, {
				sessionId: SESSION_W2.session_id,
				activeRunId: "run-4804789955aa",
				selectedDialects: ["duckdb", "databricks"],
				zoneSqlByZone: zoneSqlByDialect.duckdb,
				...({ zoneSqlByDialect } as Record<string, unknown>),
			}),
		);

		try {
			let tree = mounted.render();
			const cardEl = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "step6-sql-preview-card",
			)[0];
			expect(cardEl).toBeDefined();
			const cardStyle = cardEl?.props?.style as
				| Record<string, unknown>
				| undefined;
			expect(
				String(
					cardStyle?.backgroundColor ?? cardStyle?.background ?? "",
				).toLowerCase(),
			).toContain(CLAUDE_TOKENS.colors.surfaceDark);

			const preEl = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "step6-sql-preview-pre",
			)[0];
			expect(preEl).toBeDefined();
			const preStyle = preEl?.props?.style as
				| Record<string, unknown>
				| undefined;
			expect(
				String(
					preStyle?.backgroundColor ?? preStyle?.background ?? "",
				).toLowerCase(),
			).toContain(CLAUDE_TOKENS.colors.surfaceDarkSoft);
			expect(String(preStyle?.color ?? "").toLowerCase()).toContain(
				CLAUDE_TOKENS.colors.onDark,
			);
			expect(JSON.stringify(preStyle ?? {})).not.toContain("#f5f0e8");

			const cardText = collectText(cardEl);
			expect(cardText).toContain("5-Zone");
			expect(cardText).toContain("information_mart");
			expect(cardText).not.toContain("4-Zone");
			expect(cardText).not.toMatch(/\bmarts\./i);

			// Click Z1 on DuckDB
			const z1Btn = findElements(
				tree,
				(el) => el.type === "button" && collectText(el).includes("Z1"),
			)[0];
			expect(z1Btn).toBeDefined();
			(z1Btn?.props?.onClick as () => void)();
			tree = mounted.render();
			expect(
				collectText(
					findElements(
						tree,
						(el) => el.props?.["data-testid"] === "step6-sql-preview-pre",
					)[0],
				),
			).toContain("raw_vault.hub_portfolio");

			// Switch preview dialect to Databricks Delta -> Z1 is empty, so it must auto-skip to non-empty zone (Z0/Z2/Z4)
			const dbBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-sql-dialect-databricks",
			)[0];
			expect(dbBtn).toBeDefined();
			(dbBtn?.props?.onClick as () => void)();
			tree = mounted.render();
			const dbPreText = collectText(
				findElements(
					tree,
					(el) => el.props?.["data-testid"] === "step6-sql-preview-pre",
				)[0],
			);
			expect(dbPreText).toContain("main.raw_staging");
			expect(dbPreText).not.toContain("raw_vault.hub_portfolio");

			// Switch back to DuckDB
			const duckBtn = findElements(
				tree,
				(el) => el.props?.["data-testid"] === "btn-sql-dialect-duckdb",
			)[0];
			expect(duckBtn).toBeDefined();
			(duckBtn?.props?.onClick as () => void)();
			tree = mounted.render();
			expect(
				collectText(
					findElements(
						tree,
						(el) => el.props?.["data-testid"] === "step6-sql-preview-pre",
					)[0],
				),
			).toContain("raw_vault.hub_portfolio");
		} finally {
			mounted.unmount();
		}
	});

	it("5. Renders dynamic Gate 0–6 badges (#5db872 green PASS/AUTO-FIX + 15/15 DV-DYN, #c64545 REJECT-WITH-TRACE, and neutral PENDING on null)", () => {
		const passHtml = renderToStaticMarkup(
			React.createElement(Step6PipelineBuildAndRun, {
				sessionId: SESSION_W2.session_id,
				activeRunId: "run-4804789955aa",
				...({ gateScorecard: SAMPLE_GATE_SCORECARD } as Record<
					string,
					unknown
				>),
			}),
		);
		expect(passHtml).toContain('data-testid="step6-gate-badges"');
		for (let i = 0; i <= 6; i += 1) {
			expect(passHtml).toContain(`Gate ${i}`);
		}
		expect(passHtml).toContain("15/15 DV-DYN");
		expect(passHtml.toLowerCase()).toContain(CLAUDE_TOKENS.colors.success);

		const failHtml = renderToStaticMarkup(
			React.createElement(Step6PipelineBuildAndRun, {
				sessionId: SESSION_W2.session_id,
				activeRunId: "run-fail-1",
				...({
					gateScorecard: {
						gate_0_profile_drift: "PASS",
						gate_1_contract_grain: "PASS",
						gate_2_sqlglot_ast: "REJECT-WITH-TRACE",
						gates_passed: false,
						sandbox_summary: {
							passed: false,
							dynamic_checks_passed: 12,
							dynamic_checks_total: 15,
						},
					},
				} as Record<string, unknown>),
			}),
		);
		expect(failHtml).toContain("REJECT-WITH-TRACE");
		expect(failHtml).toContain("12/15 DV-DYN");
		expect(failHtml.toLowerCase()).toContain(CLAUDE_TOKENS.colors.error);

		const nullHtml = renderToStaticMarkup(
			React.createElement(Step6PipelineBuildAndRun, {
				sessionId: SESSION_W2.session_id,
				...({ gateScorecard: null } as Record<string, unknown>),
			}),
		);
		expect(nullHtml).toContain('data-testid="step6-gate-badges"');
		expect(nullHtml).toContain("Gate 0");
		expect(nullHtml).toContain("Gate 6");
		expect(nullHtml).toContain("PENDING");
	});
});
