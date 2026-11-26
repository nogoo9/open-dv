import { afterEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Window } from "happy-dom";

const win = new Window({ url: "http://127.0.0.1:8420/" });
Object.assign(globalThis, {
	window: win,
	document: win.document,
	navigator: win.navigator,
	location: win.location,
	history: win.history,
	HTMLElement: win.HTMLElement,
	Element: win.Element,
	Node: win.Node,
	Event: win.Event,
	CustomEvent: win.CustomEvent,
	MouseEvent: win.MouseEvent,
	KeyboardEvent: win.KeyboardEvent,
	HTMLDialogElement: win.HTMLDialogElement,
	PopStateEvent: win.PopStateEvent,
});

if (typeof win.HTMLDialogElement !== "undefined") {
	const proto = win.HTMLDialogElement.prototype as unknown as {
		open: boolean;
		returnValue: string;
		showModal?: () => void;
		close?: () => void;
	};
	proto.showModal ??= function (this: {
		setAttribute: (k: string, v: string) => void;
		open: boolean;
	}) {
		this.open = true;
		this.setAttribute("open", "");
	};
	proto.close ??= function (this: {
		removeAttribute: (k: string) => void;
		open: boolean;
	}) {
		this.open = false;
		this.removeAttribute("open");
	};
}

const { createElement } = await import("react");
const { flushSync } = await import("react-dom");
const { createRoot } = await import("react-dom/client");
const { FORBIDDEN_GUIDED_JARGON } = await import("../src/api/openapi");
const { createInitialOntologyState, useProjectStore } = await import(
	"../src/store/projectStore"
);
const { Step6Vault } = await import("../src/pages/Step6Vault");
const { Step7Storage } = await import("../src/pages/Step7Storage");
const { DVSchemaExplorerPage } = await import(
	"../src/pages/DVSchemaExplorerPage"
);
const { RunExplorerPage } = await import("../src/pages/RunExplorerPage");
const { SettingsHub } = await import("../src/pages/SettingsHub");
const { SettingsSecretsTab } = await import(
	"../src/components/SettingsSecretsTab"
);
const { RunControlDialog } = await import("../src/components/RunControlDialog");

import type {
	CertifiedVaultIR,
	GuidedRunView,
	ProjectState,
	ReadinessResponse,
	RunStateResp,
	WorkspaceSettings,
} from "../src/api/openapi";

const origFetch = globalThis.fetch;

const mkProject = (over: Partial<ProjectState> = {}): ProjectState => ({
	project_id: "am-ibor-2026",
	domain_namespace: "asset_management",
	active_ontology_rev_id: "ont-1.1.0-abc12345",
	active_wave_id: "wave-2",
	has_active_lease: false,
	source_count: 2,
	table_count: 4,
	created_at: "2026-11-24T00:00:00Z",
	updated_at: "2026-11-24T00:00:00Z",
	settings_snapshot: {
		harness_provider: "agy",
		cegar_policy: "balanced",
		v7_critic_tier: "FRONTIER_CRITIC",
		dbt_sql_dialect: "duckdb",
		discovery_mode: "incremental_new",
		secret_precedence: "store_first",
		max_turns: null,
		max_tokens: null,
		timeout_seconds: null,
	},
	...over,
});

const mkRun = (over: Partial<RunStateResp> = {}): RunStateResp => ({
	run_id: "run6-001",
	project_id: "am-ibor-2026",
	step: "step6",
	status: "HOLD_CHECKPOINTED",
	phase: "S6-06",
	attempt: 2,
	last_seq: 12,
	budget_turns_used: 8,
	budget_tokens_used: 44_000,
	elapsed_seconds: 30,
	effective_max_turns: 40,
	effective_max_tokens: 500_000,
	effective_timeout_seconds: 1200,
	policy: "balanced",
	pending_questions: [],
	...over,
});

const mkGuidedView = (over: Partial<GuidedRunView> = {}): GuidedRunView => ({
	headline: "Wave 2 requires your review before target build",
	milestone_index: 2,
	steps_summary: [
		"1. Scope & Sources: Completed",
		"2. Business Blueprint: Completed",
		"3. Steward Decisions: Active",
		"4. Target Build: Pending",
	],
	ontology_rev_id: "ont-1.1.0-abc12345",
	policy: "balanced",
	step4_run_id: "run4-999",
	entities: [
		{
			entity_id: "hub_custody_account",
			title: "Custody Account",
			badge_label: "Core Business Entity",
			summary: "Tracks unique custody accounts across source feeds.",
			change_impact: "LOCK_IN",
		},
	],
	explainers: {
		hub_custody_account: {
			business_purpose: "Anchors unique custody account keys across systems.",
			history_protection:
				"Append-only key ledger never overwrites prior records.",
			flexible_here: "New descriptive attributes go into extension tables.",
			lock_in_contrast: "Core business identifier stays locked once certified.",
		},
	},
	decision_cards: [
		{
			card_id: "hq-brk-01",
			source_step: "step6_horizon",
			title: "Account Identifier Scope",
			plain_summary: "Choose whether account IDs stay scoped per source feed.",
			why_it_matters: "Prevents future collisions if custodian codes overlap.",
			lock_in_option_label: "Keep source-scoped",
			flexible_option_label: "Unify globally",
			recommended_option_id: "opt-scoped",
			sample_evidence: ["ACCT_001 | Custody Bank A"],
			options: [
				{
					option_id: "opt-scoped",
					label: "Keep source-scoped",
					description: "Isolate account keys by source system.",
					implications: "Safe against cross-source collisions.",
				},
				{
					option_id: "opt-global",
					label: "Unify globally",
					description: "Merge identical codes across feeds.",
					implications: "High lock-in risk if systems collide.",
				},
			],
		},
		{
			card_id: "v6b-WAIV-1234abcd",
			source_step: "step6_v6b",
			title: "Review satellite split on Portfolio",
			plain_summary: "High-frequency fields may benefit from splitting.",
			why_it_matters: "Improves delta storage efficiency.",
			lock_in_option_label: "Sign business waiver",
			flexible_option_label: "Ask AI to refactor instead",
			recommended_option_id: "refactor",
			sample_evidence: [],
			options: [],
		},
		{
			card_id: "step4-q1",
			source_step: "step4",
			title: "Clarify Ballot Concept",
			plain_summary: "Confirm whether proxy ballots are separate events.",
			why_it_matters: "Needs resolution in Step 4 discovery.",
			lock_in_option_label: "Resolve in Step 4",
			flexible_option_label: "Defer",
			recommended_option_id: "lock_shared",
			sample_evidence: [],
			options: [],
		},
	],
	v6b_waiver_targets: {
		"v6b-WAIV-1234abcd": {
			waiver_id: "WAIV-1234abcd",
			rule_id: "DV-AP-004",
			entity_name: "sat_portfolio_details",
		},
	},
	readiness_brief: {
		wave_number: 2,
		certification_label: "UNCERTIFIED",
		confidence_summary: "Waiting for steward choices on Wave 2.",
		plain_highlights: ["Modeled 3 business entities and 2 relationships."],
		watchlist: ["Monitor custodian feed column growth."],
		blocked_reasons: ["Open decision cards require your review."],
	},
	technical_drawer: {
		v7_score: 92,
		diagnostic_codes: [],
	},
	...over,
});

const mkIr = (): CertifiedVaultIR => ({
	ir_version: "2.0",
	project_id: "am-ibor-2026",
	ontology_version: "1.1.0",
	wave_number: 2,
	bronze_tables: [
		{
			zone_id: "Z0",
			source_id: "s1",
			source_table_id: "t1",
			table_name: "bronze_acct",
			columns: [],
		},
	],
	staging_views: [
		{
			zone_id: "Z1",
			source_id: "s1",
			source_table_id: "t1",
			view_name: "stg_acct",
			col_lineage_mappings: {},
		},
	],
	hubs: [
		{
			zone_id: "Z2",
			hub_name: "hub_custody_account",
			key_tier: "TIER_2_ENTERPRISE_SOR",
			is_planned_concept: false,
			source_bindings: [
				{ source_id: "s1", table_id: "t1", col_mappings: { acct: "acct" } },
			],
		},
		{
			zone_id: "Z2",
			hub_name: "hub_planned_ballot",
			key_tier: "TIER_2_ENTERPRISE_SOR",
			is_planned_concept: true,
			source_bindings: [],
		},
	],
	links: [
		{
			zone_id: "Z2",
			link_name: "sal_acct_crosswalk",
			link_kind: "SAME_AS",
			participants: [
				{
					hub_name: "hub_custody_account",
					role_name: "acct",
					fk_col_name: "hk_acct",
					source_col_names: ["acct"],
				},
			],
			physical_hints: {},
		},
	],
	satellites: [
		{
			zone_id: "Z2",
			sat_name: "sat_custody_pii",
			sat_kind: "PII",
			parent_name: "hub_custody_account",
			load_mode: "INSERT_ONLY_DELTA",
			is_bitemporal: false,
			source_id: "s1",
			source_table_id: "t1",
			physical_hints: {},
		},
		{
			zone_id: "Z2",
			sat_name: "sat_ai_voting_provenance",
			sat_kind: "AI_PROVENANCE_SATELLITE",
			parent_name: "hub_custody_account",
			load_mode: "INSERT_ONLY_DELTA",
			is_bitemporal: false,
			source_id: "s1",
			source_table_id: "t1",
			physical_hints: {},
		},
	],
	pit_bridges: [
		{
			zone_id: "Z3",
			object_kind: "PIT",
			object_name: "pit_custody_acct_daily",
			root_hub: "hub_custody_account",
			target_satellites_or_links: ["sat_custody_pii"],
			grain_cols: ["hk_acct", "snapshot_dts"],
		},
	],
	information_marts: [
		{
			zone_id: "Z4",
			view_name: "vw_custody_position_current",
			root_entity: "pit_custody_acct_daily",
			joined_entities: ["sal_acct_crosswalk"],
			select_exprs: { acct: "acct" },
		},
	],
	wave_delta: {
		from_wave: 1,
		to_wave: 2,
		added_entities: ["sat_ai_voting_provenance"],
		added_sat_extensions: [],
		locked_entities: ["hub_custody_account"],
	},
});

const mkSettings = (
	over: Partial<WorkspaceSettings> = {},
): WorkspaceSettings => ({
	schema_version: "2.0",
	harness_provider: "agy",
	cegar_policy: "balanced",
	v7_critic_tier: "FRONTIER_CRITIC",
	dbt_sql_dialect: "duckdb",
	secret_backend: "keyring",
	secret_precedence: "store_first",
	max_turns: null,
	max_tokens: null,
	timeout_seconds: null,
	model_overrides: {},
	connection_profiles: {
		prod_dbx: {
			adapter_id: "databricks_uc",
			display_name: "prod_dbx",
			uri_or_path: "adb-1.azuredatabricks.net",
			options: { http_path: "/sql/1" },
			secret_refs: ["DBX_TOKEN"],
			profile_timeout_seconds: 30,
		},
	},
	trusted_workspaces: [
		{
			workspace_root: "/ws/trusted",
			agent_pack_sha256: "abc123",
			trusted_at: "2026-11-24T00:00:00Z",
		},
	],
	updated_at: "2026-11-24T00:00:00Z",
	...over,
});

function mount(node: React.ReactElement) {
	const container = win.document.createElement("div");
	win.document.body.appendChild(container);
	const root = createRoot(container as unknown as Element);
	flushSync(() => root.render(node));
	return {
		container,
		unmount: () => {
			root.unmount();
			container.remove();
		},
	};
}

const flush = () => new Promise((r) => setTimeout(r, 15));
const flushUntil = async (pred: () => boolean, timeoutMs = 1500) => {
	const deadline = Date.now() + timeoutMs;
	while (!pred() && Date.now() < deadline) {
		await flush();
	}
};
type HappyEl = ReturnType<typeof win.document.createElement>;
const qEl = <T>(root: HappyEl, sel: string) =>
	root.querySelector(sel) as unknown as T;
const findBtn = (root: HappyEl, text: string, exact = false) =>
	Array.from(root.querySelectorAll("button")).find((b) =>
		exact ? b.textContent === text : b.textContent?.includes(text),
	) as unknown as HTMLButtonElement;
const setCtrlVal = (
	el: HTMLInputElement | HTMLTextAreaElement,
	val: string,
) => {
	const isTa =
		"rows" in el ||
		(el as unknown as { tagName: string }).tagName === "TEXTAREA";
	const proto = isTa
		? win.HTMLTextAreaElement.prototype
		: win.HTMLInputElement.prototype;
	Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, val);
	el.dispatchEvent(
		new win.Event("input", { bubbles: true }) as unknown as Event,
	);
};

afterEach(() => {
	globalThis.fetch = origFetch;
	win.innerWidth = 1440;
	useProjectStore.getState().abortRunSse();
	useProjectStore.getState().applyDiscardReset();
	useProjectStore.setState({
		activeProject: null,
		activeStep4Run: null,
		activeStep6Run: null,
		activeRunPolicy: "balanced",
		guidedView: null,
		vaultIr: null,
		failedVerifyWaveId: null,
		settings: null,
		settingsDraft: null,
		readiness: null,
		sqlQuery: "",
		lastProblem: null,
		ontologyBundle: null,
		ontologyState: createInitialOntologyState(),
	});
});

describe("Track C Guided UX, Observatory, Step 7, Explorers & Settings", () => {
	test("Step 6 Guided cards carry 0 FORBIDDEN_GUIDED_JARGON, render 4-field explainer, and handle horizon accept/steer & v6b waiver/refactor", async () => {
		const posted: { url: string; body: unknown }[] = [];
		const run = mkRun();
		const gv = mkGuidedView();
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (init?.method === "POST") {
				posted.push({
					url,
					body: init.body ? JSON.parse(String(init.body)) : null,
				});
			}
			if (url.endsWith("/runs?step=step6"))
				return new Response(JSON.stringify([run]));
			if (url.endsWith("/guided-view")) return new Response(JSON.stringify(gv));
			if (url.endsWith("/vault/ir"))
				return new Response(JSON.stringify(mkIr()));
			if (url.endsWith("/vault/waivers")) {
				return new Response(
					JSON.stringify({
						waiver_id: "WAIV-1234abcd",
						rule_id: "DV-AP-004",
						entity_name: "sat_portfolio_details",
						rationale: "Intentional business choice over 20 chars",
						actor: "human",
						reviewed_by_v7: true,
					}),
				);
			}
			if (url.includes("/controls")) {
				return new Response(
					JSON.stringify(mkRun({ status: "HOLD_CHECKPOINTED" })),
				);
			}
			if (url.includes("/horizon") || url.includes("/decisions")) {
				return new Response(JSON.stringify(mkRun({ status: "COMPLETED" })), {
					status: 202,
				});
			}
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			currentUrl: "/projects/am-ibor-2026/step/6",
			activeStep6Run: run,
			guidedView: gv,
			vaultIr: mkIr(),
		});

		const { container, unmount } = mount(createElement(Step6Vault));
		await flush();

		// Guided headline, explainer, and cards must contain zero forbidden compiler jargon
		expect(FORBIDDEN_GUIDED_JARGON.test(gv.headline)).toBe(false);
		for (const c of gv.decision_cards) {
			expect(FORBIDDEN_GUIDED_JARGON.test(c.title)).toBe(false);
			expect(FORBIDDEN_GUIDED_JARGON.test(c.plain_summary)).toBe(false);
			expect(FORBIDDEN_GUIDED_JARGON.test(c.why_it_matters)).toBe(false);
		}
		expect(container.textContent).toContain("1. Business Purpose");
		expect(container.textContent).toContain("2. How History Is Protected");
		expect(container.textContent).toContain("3. Future Change Impact");

		// Switch to Decisions & Readiness sub-tab
		const decTab = qEl<HTMLButtonElement>(container, "#guided-tab-decisions");
		flushSync(() => decTab.click());
		await flush();

		expect(container.textContent).toContain("Return to Step 4 (run4-999)");
		expect(container.textContent).toContain(
			"Confirm Business Choice & Certify Wave 2 →",
		);

		// Submit horizon accept (recommended selected, empty guidance/fields)
		const confirmBtn = findBtn(
			container,
			"Confirm Business Choice & Certify Wave 2",
		);
		flushSync(() => confirmBtn.click());
		await flush();
		const horizonPost = posted.find((p) => p.url.endsWith("/horizon"));
		expect(horizonPost?.body).toEqual({
			action: "accept",
			answers: { "hq-brk-01": "opt-scoped" },
			notes: "",
			added_fields: [],
		});

		// Ask AI to Refactor Instead on HOLD_CHECKPOINTED invokes hint + empty decisions
		const refactorBtn = findBtn(container, "Ask AI to Refactor Instead");
		flushSync(() => refactorBtn.click());
		await flush();
		expect(posted.some((p) => p.url.endsWith("/controls"))).toBe(true);
		expect(
			posted.some(
				(p) =>
					p.url.endsWith("/decisions") &&
					JSON.stringify(p.body) === JSON.stringify({ answers: {} }),
			),
		).toBe(true);

		// Open v6b Waiver <dialog> and submit >= 20 char rationale
		const waiveBtn = findBtn(container, "Review & Sign Business Waiver");
		flushSync(() => waiveBtn.click());
		await flush();
		const dlg = qEl<HTMLDialogElement>(container, "dialog");
		expect(dlg.open).toBe(true);
		const ta = dlg.querySelector("textarea") as unknown as HTMLTextAreaElement;
		flushSync(() => {
			setCtrlVal(
				ta,
				"Business requires single satellite for regulatory audit.",
			);
		});
		const submitWaiverBtn = dlg.querySelector(
			'button[type="submit"]',
		) as unknown as HTMLButtonElement;
		expect(submitWaiverBtn.disabled).toBe(false);
		flushSync(() => {
			dlg.querySelector("form")?.dispatchEvent(
				new win.Event("submit", {
					bubbles: true,
					cancelable: true,
				}) as unknown as Event,
			);
		});
		await flush();
		expect(posted.some((p) => p.url.endsWith("/vault/waivers"))).toBe(true);

		unmount();
	});

	test("Step 6 supports Pause/Resume empty answers, HOLD_REQUESTED lock, Quick Horizon Review, Strict Accept Best gate, and Observatory V7 N/A", async () => {
		const posted: { url: string; body: unknown }[] = [];
		const fastCompleted = mkRun({
			run_id: "run6-fast",
			status: "COMPLETED",
			policy: "fast",
		});
		const gvFast = mkGuidedView({
			policy: "fast",
			decision_cards: [],
			readiness_brief: {
				wave_number: 2,
				certification_label: "CERTIFIED_FAST",
				confidence_summary: "Quick certification passed.",
				plain_highlights: [],
				watchlist: [],
				blocked_reasons: [],
			},
		});
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (init?.method === "POST") {
				posted.push({
					url,
					body: init.body ? JSON.parse(String(init.body)) : null,
				});
			}
			if (url.endsWith("/runs?step=step6"))
				return new Response(JSON.stringify([fastCompleted]));
			if (url.endsWith("/guided-view"))
				return new Response(JSON.stringify(gvFast));
			if (url.endsWith("/vault/ir"))
				return new Response(JSON.stringify(mkIr()));
			if (url.endsWith("/attempts")) {
				return new Response(
					JSON.stringify([
						{
							attempt: 1,
							ir_sha256: "abc",
							energy_e: 10,
							v7_score: null,
							draft_ir: {
								ir_version: "2.0",
								project_id: "am-ibor-2026",
								ontology_version: "1.1.0",
								wave_number: 2,
							},
							diagnostics: [
								{
									gate: "V6",
									rule_id: "DV-DYN-002",
									severity: "ERROR",
									entity_name: "hub_custody_account",
									message: "Unique collision",
									waivable: false,
									counterexample_rows: [
										{ a: "<MASKED_PII_#1>", b: "<MASKED_PII_#2>" },
									],
								},
							],
							horizon_verdict: {
								score: 92,
								classification: "REASONABLY_STABLE",
								absorbed_natively: ["BRK-01"],
								bucket_a_ops: [],
								bucket_b_questions: [],
								bucket_c_watchlist: ["Watch feed"],
							},
						},
					]),
				);
			}
			if (url.includes("/logs")) {
				return new Response(
					JSON.stringify({ events: [], next_since_seq: null }),
				);
			}
			if (url.includes("/controls")) {
				return new Response(JSON.stringify(fastCompleted));
			}
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			currentUrl: "/projects/am-ibor-2026/step/6",
			activeStep6Run: fastCompleted,
			activeRunPolicy: "fast",
			guidedView: gvFast,
			vaultIr: mkIr(),
		});

		const { container, unmount } = mount(createElement(Step6Vault));
		await flush();

		const horizonBtn = findBtn(container, "Run Horizon Review");
		expect(horizonBtn).toBeDefined();
		flushSync(() => horizonBtn.click());
		await flush();
		expect(
			posted.some(
				(p) =>
					p.url.endsWith("/controls") &&
					(p.body as { action?: string })?.action === "horizon-review",
			),
		).toBe(true);

		// Switch to Architect Observatory and check V7="N/A" and <MASKED_PII_#1>
		const obsBtn = findBtn(container, "Architect Observatory");
		flushSync(() => obsBtn.click());
		await flush();
		expect(container.textContent).toContain('V7="N/A"');
		expect(container.textContent).toContain("<MASKED_PII_#1>");

		// RunControlDialog locks controls on HOLD_REQUESTED
		const dlgMount = mount(
			createElement(RunControlDialog, {
				open: true,
				onClose: () => undefined,
				run: mkRun({ status: "HOLD_REQUESTED" }),
			}),
		);
		expect(dlgMount.container.textContent).toContain("Draining active work...");
		dlgMount.unmount();

		unmount();
	});

	test("Step 7 enforces wave-keyed verify gate, targetDefectsById continue-on-error, duckdb_local regen, and mobile view-only SQL preserving sqlQuery", async () => {
		const posted: { url: string; body: unknown }[] = [];
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (init?.method === "POST") {
				posted.push({
					url,
					body: init.body ? JSON.parse(String(init.body)) : null,
				});
			}
			if (url.endsWith("/storage/targets")) {
				return new Response(
					JSON.stringify([
						{
							target_id: "duckdb_local",
							certification_status: "CERTIFIED",
							last_compiled_wave: 2,
							last_materialized_wave: 2,
							verification_tier: "RUNTIME_VERIFIED",
							eval_receipts: ["EVAL-1234"],
							generated_files: ["ddl.sql"],
						},
					]),
				);
			}
			if (url.endsWith("/storage/compile")) {
				const body = JSON.parse(String(init?.body)) as { target_id: string };
				if (body.target_id === "all" || body.target_id === "redshift") {
					return new Response(
						JSON.stringify({
							error_code: "EMITTER_VERIFICATION_FAILED",
							detail: "Redshift alias failed",
							defect_report: {
								target_id: "redshift",
								stage: "EVAL-AST-01",
								rule_id: "IDENT_LEN",
								message: "Identifier exceeds 127 bytes",
								sql_or_artifact_snippet: "CREATE TABLE ...",
							},
						}),
						{ status: 500 },
					);
				}
				return new Response(
					JSON.stringify({
						target_id: body.target_id,
						artifacts: [],
						manifest: {},
					}),
				);
			}
			if (url.endsWith("/storage/regenerate")) {
				return new Response(
					JSON.stringify({
						target_id: "duckdb_local",
						wave_number: 2,
						rows_loaded: 12,
						verified: true,
					}),
				);
			}
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			currentUrl: "/projects/am-ibor-2026/step/7",
			vaultIr: mkIr(),
			sqlQuery: "SELECT * FROM hub_custody_account;",
		});

		const { container, unmount } = mount(createElement(Step7Storage));
		try {
			await flush();

			// Compile All hits 500 EMITTER_VERIFICATION_FAILED -> displays targetDefectsById inline
			const compileAllBtn = findBtn(container, "Compile All 11 Targets");
			flushSync(() => compileAllBtn.click());
			await flushUntil(
				() =>
					container.textContent?.includes("Identifier exceeds 127 bytes") ??
					false,
			);
			expect(container.textContent).toContain("Identifier exceeds 127 bytes");

			// Select Snowflake (compile-only -> Migrate disabled) and verify Regen always targets duckdb_local
			const sfBtn = findBtn(container, "Snowflake");
			flushSync(() => sfBtn.click());
			const migrateBtn = findBtn(container, "Migrate Selected Target");
			expect(migrateBtn.disabled).toBe(true);
			const regenBtn = findBtn(container, "Reset & Test Full Regen");
			flushSync(() => regenBtn.click());
			await flush();
			expect(
				posted.some(
					(p) =>
						p.url.endsWith("/storage/regenerate") &&
						(p.body as { target_id?: string })?.target_id === "duckdb_local",
				),
			).toBe(true);

			// Resize < 768px downgrades SQL runner to view-only while preserving sqlQuery
			flushSync(() => {
				win.innerWidth = 390;
				win.dispatchEvent(new win.Event("resize"));
			});
			expect(container.querySelector("textarea")).toBeNull();
			expect(container.textContent).toContain(
				"SELECT * FROM hub_custody_account;",
			);
			expect(useProjectStore.getState().sqlQuery).toBe(
				"SELECT * FROM hub_custody_account;",
			);
		} finally {
			unmount();
		}
	});

	test("DVSchemaExplorerPage renders 10 chips & Wave delta, RunExplorerPage gates /attempts on step6, SettingsHub isolates trusted_workspaces & WCAG tokens", async () => {
		const fetchedUrls: string[] = [];
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			fetchedUrls.push(url);
			if (url.endsWith("/storage/targets")) return new Response("[]");
			if (url.endsWith("/runs")) {
				return new Response(
					JSON.stringify([
						mkRun({ run_id: "run4-1", step: "step4", status: "COMPLETED" }),
					]),
				);
			}
			if (url.includes("/logs"))
				return new Response(
					JSON.stringify({ events: [], next_since_seq: null }),
				);
			if (url.endsWith("/receipts")) return new Response("[]");
			if (url.endsWith("/settings")) {
				if (init?.method === "PUT") return new Response(String(init.body));
				return new Response(JSON.stringify(mkSettings()));
			}
			if (url.endsWith("/settings/trusted-workspaces/revoke")) {
				return new Response(
					JSON.stringify(mkSettings({ trusted_workspaces: [] })),
				);
			}
			if (url.endsWith("/agents/readiness")) {
				const rr: ReadinessResponse = {
					providers: [
						{
							provider: "agy",
							status: "MISSING_PREREQ",
							credential_source: "none",
							missing_prereqs: ["agy binary not found"],
							available_models: [
								{ id: "agy-pro", label: "Agy Pro", source: "default" },
								{ id: "agy-std", label: "Agy Std", source: "default" },
							],
							active_run: false,
							remediation: {
								summary: "Install agy CLI or switch provider.",
								actions: [
									{
										actor: "human",
										label: "Install agy",
										command: null,
										requires_tty: true,
									},
								],
							},
						},
						{
							provider: "claude-cli",
							status: "CONFIGURED_UNVERIFIED",
							credential_source: "cli_login",
							missing_prereqs: [],
							available_models: [],
							active_run: false,
							remediation: null,
						},
						{
							provider: "claude",
							status: "VERIFIED",
							credential_source: "store",
							missing_prereqs: [],
							available_models: [],
							active_run: false,
							remediation: null,
						},
						{
							provider: "gemini",
							status: "VERIFIED",
							credential_source: "store",
							missing_prereqs: [],
							available_models: [
								{
									id: "gemini-2.5-pro",
									label: "Gemini 2.5 Pro",
									source: "default",
								},
								{
									id: "gemini-2.5-flash",
									label: "Gemini 2.5 Flash",
									source: "default",
								},
							],
							active_run: false,
							remediation: null,
						},
					],
				};
				return new Response(JSON.stringify(rr));
			}
			if (url.endsWith("/sources/adapters")) return new Response("[]");
			if (url.endsWith("/agents")) {
				return new Response(
					JSON.stringify([
						{
							agent_id: "dv-horizon-critic",
							name: "Horizon Critic",
							role: "V8 Horizon Critic",
							sha256: "abc",
							source: "builtin",
							stage: "step6",
						},
					]),
				);
			}
			if (url.endsWith("/secrets")) return new Response("[]");
			if (url.endsWith("/mcp/servers")) return new Response("[]");
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			vaultIr: mkIr(),
			currentUrl: "/projects/am-ibor-2026/explorer?view=1",
		});
		const exp = mount(createElement(DVSchemaExplorerPage));
		await flush();
		expect(exp.container.textContent).toContain("+W002");
		expect(exp.container.textContent).toContain("Planned Stub (No DDL)");
		expect(exp.container.textContent).toContain("AI_PROVENANCE_SATELLITE");
		exp.unmount();

		useProjectStore.setState({ currentUrl: "/projects/am-ibor-2026/runs" });
		const rexp = mount(createElement(RunExplorerPage));
		await flush();
		// Step 4 runs MUST NEVER probe /attempts
		expect(fetchedUrls.some((u) => u.includes("/attempts"))).toBe(false);
		rexp.unmount();

		useProjectStore.setState({
			currentUrl: "/settings?tab=provider",
			settings: mkSettings(),
			settingsDraft: mkSettings(),
		});
		const setHub = mount(createElement(SettingsHub));
		await flush();
		expect(setHub.container.textContent).toContain("FRONTIER_CRITIC (locked)");
		expect(setHub.container.textContent).not.toContain("deterministic");
		// Revoke trusted_workspaces keeps settingsDraft dirty state isolated
		const revokeBtn = findBtn(setHub.container, "Revoke", true);
		flushSync(() => revokeBtn.click());
		await flush();
		expect(
			useProjectStore.getState().settings?.trusted_workspaces?.length,
		).toBe(0);
		expect(useProjectStore.getState().dirtyFlags.settings).toBe(false);
		setHub.unmount();

		// WCAG 2.1 AA tokens contract check
		const css = readFileSync(
			resolve(import.meta.dir, "../src/styles/tokens.css"),
			"utf-8",
		);
		expect(css).toContain("--primary-active: #a9583e");
		expect(css).toContain("--primary-ink: #8f442d");
		expect(css).toContain("--xy-controls-button-background-color: #181715");
	});

	test("Phase 5 Settings readiness card, <datalist> model matrix, inline bind, Switch to gemini, Tab 2/3 User-Global & Step 6 policy toggle preservation", async () => {
		const posted: { url: string; body: unknown }[] = [];
		let readinessCalls = 0;
		let probeCalls = 0;
		let listSecretsCalls = 0;
		const mkReadiness = (
			agyActiveRun = false,
			geminiStatus: "MISSING_PREREQ" | "VERIFIED" = "MISSING_PREREQ",
		): ReadinessResponse => ({
			providers: [
				{
					provider: "agy",
					status: "MISSING_PREREQ",
					credential_source: "none",
					missing_prereqs: ["agy missing"],
					available_models: [
						{ id: "agy-frontier", label: "Agy Frontier", source: "default" },
					],
					active_run: agyActiveRun,
					active_run_id: agyActiveRun ? "run4-lock" : null,
					remediation: {
						summary: "Install agy or switch to gemini.",
						actions: [
							{
								actor: "agent",
								label: "Switch to gemini",
								command: "uv run opendv settings set --harness-provider gemini",
								requires_tty: false,
							},
						],
					},
				},
				{
					provider: "claude-cli",
					status: "CONFIGURED_UNVERIFIED",
					credential_source: "cli_login",
					missing_prereqs: [],
					available_models: [],
					active_run: false,
					remediation: null,
				},
				{
					provider: "claude",
					status: "VERIFIED",
					credential_source: "store",
					missing_prereqs: [],
					available_models: [],
					active_run: false,
					remediation: null,
				},
				{
					provider: "gemini",
					status: geminiStatus,
					credential_source: geminiStatus === "VERIFIED" ? "store" : "none",
					missing_prereqs:
						geminiStatus === "VERIFIED" ? [] : ["GEMINI_API_KEY"],
					available_models: [
						{
							id: "gemini-2.5-pro",
							label: "Gemini 2.5 Pro",
							source: "default",
						},
					],
					active_run: false,
					remediation: null,
				},
			],
		});

		let currentGemini: "MISSING_PREREQ" | "VERIFIED" = "MISSING_PREREQ";
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (init?.method === "POST" || init?.method === "PUT") {
				posted.push({
					url,
					body: init.body ? JSON.parse(String(init.body)) : null,
				});
			}
			if (url.endsWith("/settings")) {
				if (init?.method === "PUT") return new Response(String(init.body));
				return new Response(
					JSON.stringify(
						mkSettings({
							model_overrides: { FRONTIER_CRITIC: "agy-frontier" },
						}),
					),
				);
			}
			if (url.endsWith("/agents/readiness")) {
				readinessCalls += 1;
				return new Response(JSON.stringify(mkReadiness(false, currentGemini)));
			}
			if (url.endsWith("/agents/readiness/test")) {
				probeCalls += 1;
				return new Response(
					JSON.stringify({
						type: "urn:opendv:error:project-job-active",
						title: "Project Job Active",
						status: 409,
						detail: "Run active",
						error_code: "PROJECT_JOB_ACTIVE",
						category: "Resolution",
						waivable: false,
						rationale_required: false,
						instance: null,
						run_id: null,
						diagnostics: [],
						defect_report: null,
						remediation: { summary: "Wait for active run.", actions: [] },
					}),
					{ status: 409 },
				);
			}
			if (url.endsWith("/secrets") && init?.method === "POST") {
				currentGemini = "VERIFIED";
				return new Response(
					JSON.stringify({
						key: "GEMINI_API_KEY",
						backend: "keyring",
						present: true,
					}),
				);
			}
			if (url.includes("/secrets")) {
				listSecretsCalls += 1;
				return new Response("[]");
			}
			if (url.endsWith("/sources/adapters")) return new Response("[]");
			if (url.endsWith("/agents")) {
				return new Response(
					JSON.stringify([
						{
							agent_id: "dv-horizon-critic",
							name: "Horizon Critic",
							role: "V8 Horizon Critic",
							sha256: "abc",
							source: "builtin",
							stage: "step6",
						},
					]),
				);
			}
			if (url.endsWith("/mcp/servers")) return new Response("[]");
			if (url.endsWith("/sources/inspect")) {
				return new Response(
					JSON.stringify({
						type: "urn:opendv:error:needs-local-secret-binding",
						title: "Needs Local Secret Binding",
						status: 422,
						detail: "Missing DATABRICKS_TOKEN",
						error_code: "NEEDS_LOCAL_SECRET_BINDING",
						category: "Preconditions",
						waivable: false,
						rationale_required: false,
						instance: null,
						run_id: null,
						diagnostics: [],
						defect_report: null,
						remediation: {
							summary: "Bind DATABRICKS_TOKEN locally.",
							actions: [
								{
									actor: "human",
									label: "Bind token",
									command:
										"uv run opendv secrets set DATABRICKS_TOKEN --prompt",
									requires_tty: true,
								},
							],
						},
					}),
					{ status: 422 },
				);
			}
			if (url.endsWith("/runs?step=step6"))
				return new Response(JSON.stringify([mkRun({ policy: "balanced" })]));
			if (url.endsWith("/guided-view"))
				return new Response(JSON.stringify(mkGuidedView()));
			if (url.endsWith("/vault/ir"))
				return new Response(JSON.stringify(mkIr()));
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			currentUrl: "/settings?tab=provider",
		});
		const hub = mount(createElement(SettingsHub));
		await flush();
		expect(readinessCalls).toBe(1);
		expect(hub.container.textContent).toContain(
			"Harness Readiness Diagnostics",
		);
		expect(hub.container.textContent).not.toContain("deterministic");
		// agy MUST NOT render inline API key binder
		expect(
			hub.container.querySelector('input[aria-label^="API key for"]'),
		).toBeNull();
		// datalist + effectiveTier placeholder
		expect(hub.container.querySelectorAll("datalist option").length).toBe(1);
		const modelInput = hub.container.querySelector(
			'input[aria-label="Model override for dv-horizon-critic"]',
		) as HTMLInputElement | null;
		expect(modelInput?.placeholder).toContain(
			"Inherited (FRONTIER_CRITIC): agy-frontier",
		);
		// Test button calls testAgentReadiness AND settles with fetchReadiness even on 409
		const testBtn = findBtn(hub.container, "Test", true);
		flushSync(() => testBtn.click());
		await flush();
		expect(probeCalls).toBe(1);
		expect(readinessCalls).toBe(2);
		expect(hub.container.textContent).toContain("PROJECT_JOB_ACTIVE");
		// Switch to gemini button appears only for agy when MISSING_PREREQ/AUTH_FAILED
		const switchBtn = findBtn(hub.container, "Switch to gemini");
		flushSync(() => switchBtn.click());
		await flush();
		const keepBtn = findBtn(hub.container, "Keep Overrides");
		flushSync(() => keepBtn.click());
		await flush();
		// Now gemini shows inline GEMINI_API_KEY password binder
		const keyInput = qEl<HTMLInputElement>(
			hub.container,
			'input[aria-label="API key for GEMINI_API_KEY"]',
		);
		expect(keyInput.type).toBe("password");
		flushSync(() => setCtrlVal(keyInput, "gem-secret-val"));
		const bindForm = keyInput.closest("form") as unknown as HTMLFormElement;
		flushSync(() => {
			bindForm.dispatchEvent(
				new win.Event("submit", {
					bubbles: true,
					cancelable: true,
				}) as unknown as Event,
			);
		});
		await flush();
		expect(
			posted.some(
				(p) =>
					p.url.endsWith("/secrets") &&
					(p.body as Record<string, unknown>).key === "GEMINI_API_KEY",
			),
		).toBe(true);
		expect(readinessCalls).toBe(3);

		// Save Settings triggers fetchReadiness()
		const saveBtn = findBtn(hub.container, "Save Settings");
		flushSync(() => saveBtn.click());
		await flush();
		expect(readinessCalls).toBe(4);
		hub.unmount();

		// Tab 2 User-Global copy, host fallback, databricks_uc skips client listSecrets & renders RemediationList
		const secCallsBefore = listSecretsCalls;
		const secTab = mount(
			createElement(SettingsSecretsTab, {
				draft: mkSettings({
					connection_profiles: {
						db_prof: {
							adapter_id: "databricks_uc",
							display_name: "DB",
							uri_or_path: "",
							options: { host: "adb-host.example.com" },
							secret_refs: ["DATABRICKS_TOKEN"],
							profile_timeout_seconds: 30,
						},
					},
				}),
				secrets: [],
				onRefreshSecrets: async () => undefined,
				onChangeDraft: () => undefined,
			}),
		);
		expect(secTab.container.textContent).toContain("User-Global");
		expect(secTab.container.textContent).toContain("store_first (Store > Env)");
		expect(secTab.container.textContent).toContain("adb-host.example.com");
		const testConnBtn = findBtn(secTab.container, "Test Connection");
		flushSync(() => testConnBtn.click());
		await flush();
		expect(listSecretsCalls).toBe(secCallsBefore);
		expect(
			secTab.container.querySelector(".banner-danger")?.textContent,
		).toContain("Bind DATABRICKS_TOKEN locally.");
		secTab.unmount();

		// Step 6 Guided <-> Observatory toggle preserves user-selected policy radio
		useProjectStore.setState({
			activeProject: mkProject({
				settings_snapshot: {
					...mkProject().settings_snapshot,
					cegar_policy: "balanced",
				},
			}),
			currentUrl: "/projects/am-ibor-2026/step/6",
		});
		const s6 = mount(createElement(Step6Vault));
		await flush();
		const strictRadio = qEl<HTMLInputElement>(
			s6.container,
			'input[type="radio"][value="strict"]',
		);
		flushSync(() => strictRadio.click());
		expect(useProjectStore.getState().activeRunPolicy).toBe("strict");
		const obsBtn = findBtn(s6.container, "Architect Observatory");
		flushSync(() => obsBtn.click());
		await flush();
		expect(useProjectStore.getState().activeRunPolicy).toBe("strict");
		s6.unmount();
	});
});
