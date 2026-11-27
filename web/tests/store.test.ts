import { afterEach, describe, expect, test } from "bun:test";
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
const {
	ApiProblemError,
	SseStreamParser,
	apiRequest,
	buildSourceInspectReq,
	createProblemDetails,
	fetchAllRunLogs,
	parseProblemDetails,
	parseSseFrame,
	runFailedToProblem,
	streamRunEvents,
} = await import("../src/api/client");
const {
	FORBIDDEN_GUIDED_JARGON,
	STATIC_11_TARGETS,
	UPLOAD_EXT_TO_KIND,
	inferUploadKind,
	validateProjectId,
} = await import("../src/api/openapi");
const {
	SSE_RING_MAX,
	createInitialOntologyState,
	parseAppRoute,
	useProjectStore,
} = await import("../src/store/projectStore");
const { App } = await import("../src/App");
const { RemediationList } = await import("../src/components/RemediationList");
const { RunControlDialog } = await import("../src/components/RunControlDialog");

import type {
	ErrorCode,
	GraphAOntology,
	ProjectState,
	ReadinessResponse,
	RunStateResp,
	RunStreamEvent,
	WorkspaceSettings,
} from "../src/api/openapi";

const origFetch = globalThis.fetch;

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
	connection_profiles: {},
	trusted_workspaces: [
		{
			workspace_root: "/ws/trusted",
			agent_pack_sha256: "abc123",
			trusted_at: "2026-01-01T00:00:00Z",
		},
	],
	updated_at: "2026-01-01T00:00:00Z",
	...over,
});

const mkProject = (
	id: string,
	over: Partial<ProjectState> = {},
): ProjectState => ({
	project_id: id,
	domain_namespace: "asset_management",
	active_ontology_rev_id: "v0.1.0",
	active_wave_id: null,
	has_active_lease: false,
	source_count: 1,
	table_count: 2,
	created_at: "2026-01-01T00:00:00Z",
	updated_at: "2026-01-01T00:00:00Z",
	settings_snapshot: {
		harness_provider: "agy",
		cegar_policy: "balanced",
		v7_critic_tier: "FRONTIER_CRITIC",
		dbt_sql_dialect: "duckdb",
		discovery_mode: "incremental_new",
		secret_precedence: "store_first",
		proving_sample_rows: 200,
		real_row_proving: true,
		max_turns: null,
		max_tokens: null,
		timeout_seconds: null,
	},
	...over,
});

const mkOnt = (): GraphAOntology => ({
	ontology_version: "0.1.0",
	domain_namespace: "asset_management",
	concepts: [],
	relationships: [],
	enumerations: [],
});

const mkRun = (over: Partial<RunStateResp> = {}): RunStateResp => ({
	run_id: "run-1",
	project_id: "p1",
	step: "step6",
	status: "COMPLETED",
	phase: "done",
	attempt: 1,
	last_seq: 5,
	budget_turns_used: 3,
	budget_tokens_used: 12000,
	elapsed_seconds: 15,
	effective_max_turns: null,
	effective_max_tokens: null,
	effective_timeout_seconds: null,
	policy: "balanced",
	pending_questions: [],
	...over,
});

afterEach(() => {
	globalThis.fetch = origFetch;
	useProjectStore.getState().abortRunSse();
	useProjectStore.getState().applyDiscardReset();
	useProjectStore.setState({
		healthOk: true,
		bootstrapped: false,
		currentUrl: "/",
		activeProject: null,
		projectsSummary: [],
		sources: [],
		documentMaps: [],
		ontologyBundle: null,
		vaultIr: null,
		failedVerifyWaveId: null,
		missingSecretRefs: [],
		hiddenProposalIds: [],
		settings: null,
		settingsDraft: null,
		readiness: null,
		guidedView: null,
		lastProblem: null,
		liveAnnouncements: { polite: "", assertive: "" },
		activeStep4Run: null,
		activeStep6Run: null,
		activeRunPolicy: "balanced",
		runOrderIds: [],
		activeStep6OntologyRevId: null,
		supersededRunIds: [],
		sseEvents: [],
		lastReceivedSeq: 0,
		sseDisconnected: false,
		sseActiveRunId: null,
		sseBackfillActive: false,
		pendingNav: null,
		ontologyState: createInitialOntologyState(),
	});
	win.history.replaceState(null, "", "/");
});

function installMockFetch(urls: string[]) {
	const fn = (async (input: RequestInfo | URL) => {
		const url = typeof input === "string" ? input : input.toString();
		urls.push(url);
		if (url === "/api/v1/health")
			return new Response(JSON.stringify({ status: "ok", version: "2.0.0" }));
		if (url === "/api/v1/settings")
			return new Response(JSON.stringify(mkSettings()));
		if (url === "/api/v1/agents/readiness") {
			const body: ReadinessResponse = {
				providers: (["agy", "claude-cli", "claude", "gemini"] as const).map(
					(provider) => ({
						provider,
						status: "VERIFIED",
						credential_source:
							provider === "claude-cli" || provider === "agy"
								? "cli_login"
								: "store",
						missing_prereqs: [],
						available_models: [
							{
								id: `${provider}-frontier`,
								label: "Frontier",
								source: "default",
							},
							{
								id: `${provider}-standard`,
								label: "Standard",
								source: "default",
							},
						],
						active_run: false,
						remediation: null,
					}),
				),
			};
			return new Response(JSON.stringify(body));
		}
		if (url === "/api/v1/projects") {
			return new Response(
				JSON.stringify([
					{
						project_id: "p1",
						domain_namespace: "asset_management",
						active_ontology_rev_id: "v0.1.0",
						active_wave_id: null,
						updated_at: "2026-01-02T00:00:00Z",
					},
					{
						project_id: "p2",
						domain_namespace: "finance",
						active_ontology_rev_id: "v0.2.0",
						active_wave_id: "W001",
						updated_at: "2026-01-01T00:00:00Z",
					},
				]),
			);
		}
		if (/\/api\/v1\/projects\/([^/]+)$/.test(url)) {
			const pid = url.split("/").pop() ?? "p1";
			return new Response(JSON.stringify(mkProject(pid)));
		}
		if (url.endsWith("/sources")) return new Response("[]");
		if (url.endsWith("/evidence/documents/map")) return new Response("[]");
		if (url.endsWith("/ontology")) {
			return new Response(
				JSON.stringify({
					ontology: mkOnt(),
					ontology_proposals: [],
					staged_candidates: [],
					evolution_locks: null,
				}),
			);
		}
		if (url.endsWith("/runs")) return new Response("[]");
		if (url.endsWith("/vault/ir"))
			return new Response(
				JSON.stringify({ error_code: "RESOURCE_NOT_FOUND", detail: "none" }),
				{ status: 404 },
			);
		return new Response("{}", { status: 404 });
	}) as unknown as typeof fetch;
	globalThis.fetch = fn;
}

describe("Routing, Cold Start & Project Switcher", () => {
	test("parses canonical routes and falls back bare /explorer & /runs to /", () => {
		expect(parseAppRoute("/projects/p1/step/3", "?sub=3b").step).toBe(3);
		expect(
			parseAppRoute("/projects/p1/step/5", "?mode=3").query.get("mode"),
		).toBe("3");
		expect(parseAppRoute("/projects/p1/explorer", "?view=2").kind).toBe(
			"explorer",
		);
		expect(parseAppRoute("/projects/p1/runs/r-99").runId).toBe("r-99");
		expect(parseAppRoute("/explorer").normalizedUrl).toBe("/");
		expect(parseAppRoute("/runs").normalizedUrl).toBe("/");
		expect(parseAppRoute("/settings", "?tab=mcp").kind).toBe("settings");
	});

	test("cold boot on /, /settings, and Step 2 skips /vault/ir", async () => {
		const calls1: string[] = [];
		installMockFetch(calls1);
		await useProjectStore.getState().bootstrapRoute("/", "");
		expect(calls1.some((u) => u.includes("/vault/ir"))).toBe(false);

		const calls2: string[] = [];
		installMockFetch(calls2);
		await useProjectStore
			.getState()
			.bootstrapRoute("/settings", "?tab=secrets");
		expect(useProjectStore.getState().activeProject?.project_id).toBe("p1");
		expect(calls2.some((u) => u.includes("/vault/ir"))).toBe(false);

		const calls3: string[] = [];
		installMockFetch(calls3);
		useProjectStore.getState().navigate("/projects/p1/step/2");
		await Promise.resolve();
		expect(calls3.some((u) => u.includes("/ontology"))).toBe(false);
		expect(calls3.some((u) => u.includes("/vault/ir"))).toBe(false);
	});

	test("Project Switcher preserves routes, strips run_id, and stays on /settings", async () => {
		const calls: string[] = [];
		installMockFetch(calls);
		useProjectStore.setState({
			activeProject: mkProject("p1"),
			currentUrl: "/projects/p1/step/4?run_id=r1&sub=3b",
		});
		useProjectStore.getState().switchProject("p2");
		expect(useProjectStore.getState().currentUrl).toBe(
			"/projects/p2/step/4?sub=3b",
		);

		useProjectStore.setState({ currentUrl: "/projects/p1/explorer?view=3" });
		useProjectStore.getState().switchProject("p2");
		expect(useProjectStore.getState().currentUrl).toBe(
			"/projects/p2/explorer?view=3",
		);

		useProjectStore.setState({ currentUrl: "/projects/p1/runs/run-42" });
		useProjectStore.getState().switchProject("p2");
		expect(useProjectStore.getState().currentUrl).toBe("/projects/p2/runs");

		useProjectStore.setState({ currentUrl: "/settings?tab=mcp" });
		useProjectStore.getState().switchProject("p1");
		expect(useProjectStore.getState().currentUrl).toBe("/settings?tab=mcp");
	});
});

describe("SSE, Logs, Controls & Budget Meter", () => {
	test("SseStreamParser handles UTF-8 splits, CRLF, id tracking, and logs pagination", async () => {
		const frame = parseSseFrame(
			"id: 7\nevent: STAGE_UPDATE\ndata: hello\ndata: world",
		);
		expect(frame).toEqual({
			id: "7",
			event: "STAGE_UPDATE",
			data: "hello\nworld",
			retry: undefined,
		});

		const parser = new SseStreamParser();
		const out1 = parser.feed("id: 9\r\ndata: café");
		const out2 = parser.feed("\r\n\r\ndata: next\n\n");
		expect(out1).toEqual([]);
		expect(out2.map((x) => [x.id, x.data])).toEqual([
			["9", "café"],
			["9", "next"],
		]);

		const logCalls: string[] = [];
		const events = await fetchAllRunLogs("p1", "r1", 0, undefined, (async (
			u: RequestInfo | URL,
		) => {
			const s = String(u);
			logCalls.push(s);
			if (s.includes("since_seq=0")) {
				return new Response(
					JSON.stringify({
						events: [
							{
								run_id: "r1",
								seq: 1,
								step: "step6",
								event_type: "SESSION_START",
								ts: "t",
								payload: {},
							},
						],
						next_since_seq: 1,
					}),
				);
			}
			return new Response(
				JSON.stringify({
					events: [
						{
							run_id: "r1",
							seq: 1,
							step: "step6",
							event_type: "SESSION_START",
							ts: "t",
							payload: {},
						},
						{
							run_id: "r1",
							seq: 2,
							step: "step6",
							event_type: "RUN_COMPLETED",
							ts: "t",
							payload: {},
						},
					],
					next_since_seq: null,
				}),
			);
		}) as unknown as typeof fetch);
		expect(logCalls.length).toBe(2);
		expect(events.map((e) => e.seq)).toEqual([1, 2]);
	});

	test("streamRunEvents sends Last-Event-ID, reconciles EOF, and stops after 5 backoff retries", async () => {
		const headersSeen: string[] = [];
		let sseAttempts = 0;
		const origSetTimeout = globalThis.setTimeout;
		globalThis.setTimeout = ((fn: () => void) =>
			origSetTimeout(fn, 0)) as unknown as typeof setTimeout;
		let disconnected = false;
		const ac = new AbortController();
		let lastSeq = 12;
		try {
			await streamRunEvents({
				projectId: "p1",
				runId: "r1",
				step: "step6",
				getLastSeq: () => lastSeq,
				signal: ac.signal,
				onEvents: (evs) => {
					lastSeq = Math.max(lastSeq, ...evs.map((e) => e.seq));
				},
				onDisconnected: () => {
					disconnected = true;
				},
				fetchImpl: (async (u: RequestInfo | URL, init?: RequestInit) => {
					const s = String(u);
					if (s.endsWith("/events")) {
						sseAttempts++;
						headersSeen.push(
							(init?.headers as Record<string, string>)["Last-Event-ID"] ?? "",
						);
						return new Response(
							new ReadableStream({
								start(c) {
									c.close();
								},
							}),
							{ status: 200 },
						);
					}
					return new Response(
						JSON.stringify(mkRun({ run_id: "r1", status: "RUNNING" })),
					);
				}) as unknown as typeof fetch,
			});
		} finally {
			globalThis.setTimeout = origSetTimeout;
		}
		expect(headersSeen[0]).toBe("12");
		expect(sseAttempts).toBe(6);
		expect(disconnected).toBe(true);
	});

	test("SSE ring caps at 2,000, controls backfill on 200 halt, and active 1s tick updates elapsed", async () => {
		const bigBatch: RunStreamEvent[] = Array.from({ length: 2150 }, (_, i) => ({
			run_id: "r1",
			seq: i + 1,
			step: "step4",
			event_type: "TEXT_DELTA",
			ts: "t",
			payload: {},
		}));
		const { mergeSseEvents } = await import("../src/store/projectStore");
		const merged = mergeSseEvents([], bigBatch);
		expect(merged.events.length).toBe(SSE_RING_MAX);
		expect(merged.events[0]?.seq).toBe(151);

		const fetched: string[] = [];
		globalThis.fetch = (async (u: RequestInfo | URL) => {
			const s = String(u);
			fetched.push(s);
			if (s.endsWith("/controls")) {
				return new Response(
					JSON.stringify(
						mkRun({
							run_id: "r4",
							step: "step4",
							status: "HOLD_CHECKPOINTED",
							last_seq: 4,
						}),
					),
				);
			}
			if (s.includes("/logs")) {
				return new Response(
					JSON.stringify({
						events: [
							{
								run_id: "r4",
								seq: 4,
								step: "step4",
								event_type: "HOLD_CHECKPOINTED",
								ts: "t",
								payload: {},
							},
						],
						next_since_seq: null,
					}),
				);
			}
			return new Response(JSON.stringify(mkRun()));
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject("p1"),
			activeStep4Run: mkRun({
				run_id: "r4",
				step: "step4",
				status: "RUNNING",
				elapsed_seconds: 10,
				last_seq: 2,
			}),
			sseBackfillActive: true,
			lastReceivedSeq: 2,
		});
		const resp = await useProjectStore
			.getState()
			.runControlAction("step4", "r4", { action: "pause" });
		expect(resp.status).toBe("HOLD_CHECKPOINTED");
		expect(fetched.some((u) => u.includes("/logs?since_seq=2"))).toBe(true);

		const origSetInterval = globalThis.setInterval;
		const tickCbs: (() => void)[] = [];
		globalThis.setInterval = ((cb: () => void) => {
			tickCbs.push(cb);
			return 1 as unknown as ReturnType<typeof setInterval>;
		}) as unknown as typeof setInterval;
		globalThis.clearInterval = (() => {
			tickCbs.length = 0;
		}) as unknown as typeof clearInterval;
		try {
			useProjectStore.getState().setActiveStep6Run(
				mkRun({
					run_id: "r6",
					step: "step6",
					status: "RUNNING",
					elapsed_seconds: 25,
				}),
			);
			expect(tickCbs.length).toBe(1);
			tickCbs[0]?.();
			expect(useProjectStore.getState().activeStep6Run?.elapsed_seconds).toBe(
				26,
			);
		} finally {
			globalThis.setInterval = origSetInterval;
			useProjectStore.getState().abortRunSse();
		}
	});

	test("RunControlDialog locks controls on HOLD_REQUESTED", () => {
		const container = win.document.createElement("div");
		win.document.body.appendChild(container);
		const root = createRoot(container as unknown as Element);
		flushSync(() => {
			root.render(
				createElement(RunControlDialog, {
					open: true,
					onClose: () => undefined,
					run: mkRun({ status: "HOLD_REQUESTED" }),
				}),
			);
		});
		expect(container.textContent).toContain("Draining active work...");
		const submit = container.querySelector(
			'button[type="submit"]',
		) as HTMLButtonElement | null;
		expect(submit?.disabled).toBe(true);
		root.unmount();
		container.remove();
	});

	test("resolveEffectiveBudget obeys 8-step precedence, snapshot overrides, and + markers", () => {
		const s = useProjectStore.getState();
		useProjectStore.setState({
			settings: mkSettings({
				max_turns: 50,
				max_tokens: 900_000,
				timeout_seconds: 1500,
			}),
		});
		expect(s.resolveEffectiveBudget("/settings").mode).toBe("settings");
		expect(s.resolveEffectiveBudget("/settings").max_turns).toBe(50);
		expect(s.resolveEffectiveBudget("/").mode).toBe("idle");
		expect(s.resolveEffectiveBudget("/projects/p1/step/1").label).toBe(
			"Budget: Idle · 0 active runs",
		);

		const snapProj = mkProject("p1", {
			settings_snapshot: {
				...mkProject("p1").settings_snapshot,
				max_turns: 12,
			},
		});
		useProjectStore.setState({
			activeProject: snapProj,
			activeStep4Run: mkRun({
				run_id: "r4",
				step: "step4",
				status: "COMPLETED",
				budget_turns_used: 15,
				effective_max_turns: 12,
			}),
			activeStep6Run: mkRun({
				run_id: "r6",
				step: "step6",
				status: "COMPLETED",
				budget_turns_used: 4,
			}),
			runOrderIds: ["r4", "r6"],
		});
		const b2 = useProjectStore
			.getState()
			.resolveEffectiveBudget("/projects/p1/step/2");
		expect(b2.snapshotOverride).toBe(true);
		expect(b2.max_turns).toBe(12);
		const b7 = useProjectStore
			.getState()
			.resolveEffectiveBudget("/projects/p1/step/7");
		expect(b7.runId).toBe("r4");
		expect(b7.exceededTurns).toBe(true);

		useProjectStore.setState({
			activeStep6Run: mkRun({
				run_id: "r6-live",
				step: "step6",
				status: "HOLD_REQUESTED",
				budget_turns_used: 9,
			}),
		});
		expect(
			useProjectStore.getState().resolveEffectiveBudget("/projects/p1/step/1")
				.runId,
		).toBe("r6-live");
	});
});

describe("ProblemDetails, Readiness, Step 7 Gate & Thin-Client Guards", () => {
	test("covers all 34 ErrorCodes (30 errors + 200/202 + SSE RUN_FAILED + non-HTTP)", async () => {
		const codes: [ErrorCode, number | null][] = [
			["OK", 200],
			["HOLD_CHECKPOINTED", 202],
			["UNCERTIFIED_DRAFT", 202],
			["SYNTAX_INVALID_ARGUMENT", 400],
			["UNSAFE_WAL_FILESYSTEM", 400],
			["DATABRICKS_SSRF_BLOCKED", 400],
			["UNTRUSTED_WORKSPACE_OVERRIDE", 403],
			["LOOPBACK_ORIGIN_FORBIDDEN", 403],
			["EXTERNAL_MCP_DOC_OPT_IN_REQUIRED", 403],
			["RESOURCE_NOT_FOUND", 404],
			["PROJECT_JOB_ACTIVE", 409],
			["SOURCE_COMMITTED_IN_VAULT", 409],
			["PROJECT_ID_CONFLICT", 409],
			["REVISION_CONFLICT", 409],
			["SOURCE_BINARY_ABSENT", 409],
			["ARCHIVE_SIZE_LIMIT_EXCEEDED", 413],
			["UNSUPPORTED_MEDIA_TYPE", 415],
			["DRIVER_MISSING", 422],
			["NEEDS_LOCAL_SECRET_BINDING", 422],
			["AGENT_NOT_CONFIGURED", 422],
			["UNREDACTED_SCAN_BLOCKED", 422],
			["REDACTED_PLACEHOLDER_LEAK", 422],
			["UNGROUNDED_CANDIDATE_QUARANTINED", 422],
			["ONT_EVO_CONTRACT_VIOLATION", 422],
			["VERIFICATION_GATE_FAILED", 422],
			["ARCHIVE_SECURITY_VIOLATION", 422],
			["BUNDLE_HASH_MISMATCH", 422],
			["CITATION_SPAN_UNRESOLVED", 422],
			["REFERENCE_PACK_INTEGRITY_FAILURE", 500],
			["INTERNAL_SERVER_ERROR", 500],
			["EMITTER_VERIFICATION_FAILED", 500],
			["AGENT_EXECUTION_FAILED", 502],
			["EXTERNAL_MCP_GATEWAY_ERROR", 502],
			["REMOTE_BIND_UNAUTHENTICATED", null],
		];
		expect(codes.length).toBe(34);
		for (const [code, status] of codes) {
			const p = createProblemDetails(code, `detail:${code}`);
			expect(p.error_code).toBe(code);
			expect(p.status).toBe(status);
		}
		expect(
			runFailedToProblem("r1", {
				error_code: "AGENT_EXECUTION_FAILED",
				detail: "boom",
			}).run_id,
		).toBe("r1");
		expect(
			parseProblemDetails(
				new ApiProblemError(
					createProblemDetails("ARCHIVE_SIZE_LIMIT_EXCEEDED", "big"),
				),
			).status,
		).toBe(413);
		let caught: unknown;
		try {
			await apiRequest("/fail", {
				fetchImpl: (async () =>
					new Response(
						JSON.stringify({
							error_code: "VERIFICATION_GATE_FAILED",
							detail: "bad",
						}),
						{ status: 422 },
					)) as unknown as typeof fetch,
			});
		} catch (e) {
			caught = e;
		}
		expect(parseProblemDetails(caught).error_code).toBe(
			"VERIFICATION_GATE_FAILED",
		);
	});

	test("Readiness pill precedence, hiddenProposalIds, Step 7 gate, race guards, and dirty reset", async () => {
		useProjectStore.setState({
			settings: mkSettings(),
			activeProject: mkProject("p1", { active_wave_id: "W001" }),
			ontologyBundle: {
				ontology: mkOnt(),
				ontology_proposals: [
					{
						proposal_id: "prop-1",
						summary: "s",
						wave_number: 1,
						status: "PENDING_STEWARD",
						delta_ops: [],
					},
				],
				staged_candidates: [],
				evolution_locks: null,
			},
		});
		expect(useProjectStore.getState().deriveReadinessPill().label).toBe(
			"Steward Review",
		);
		expect(useProjectStore.getState().deriveReadinessPill().detailLabel).toBe(
			"Steward Review · agy",
		);
		useProjectStore.getState().hideProposalInSession("prop-1");
		expect(useProjectStore.getState().deriveReadinessPill().label).toBe(
			"Ready",
		);

		useProjectStore.setState({
			activeStep4Run: mkRun({ run_id: "r4", step: "step4", status: "FAILED" }),
			runOrderIds: ["r4"],
		});
		expect(useProjectStore.getState().deriveReadinessPill().label).toBe(
			"Action Needed",
		);
		useProjectStore.getState().markRunsSupersededOnCommit("v0.2.0");
		expect(useProjectStore.getState().deriveReadinessPill().label).toBe(
			"Ready",
		);

		useProjectStore.setState({
			vaultIr: {
				ir_version: "1",
				project_id: "p1",
				ontology_rev_id: "v0.1.0",
				wave_id: "W001",
				bronze_tables: [],
				staging_views: [],
				hubs: [],
				links: [],
				satellites: [],
				ref_tables: [],
				pit_bridges: [],
				information_marts: [],
			} as never,
		});
		expect(useProjectStore.getState().deriveStep7Gate().unlocked).toBe(true);
		useProjectStore.setState({ failedVerifyWaveId: "W001" });
		expect(useProjectStore.getState().deriveStep7Gate().unlocked).toBe(false);

		useProjectStore.setState({ missingSecretRefs: ["sec-1"] });
		installMockFetch([]);
		await useProjectStore
			.getState()
			.hydrateProjectShell("p1", { preserveMissingSecretRefs: true });
		expect(useProjectStore.getState().missingSecretRefs).toEqual(["sec-1"]);

		// Concurrent hydration abort guard
		let slowDone = false;
		globalThis.fetch = (async (u: RequestInfo | URL, init?: RequestInit) => {
			const s = String(u);
			if (s.includes("/p-slow")) {
				await new Promise((r) => setTimeout(r, 20));
				if (init?.signal?.aborted) throw new Error("aborted");
				slowDone = true;
			}
			if (s.endsWith("/sources") || s.endsWith("/map") || s.endsWith("/runs"))
				return new Response("[]");
			if (s.endsWith("/ontology"))
				return new Response(
					JSON.stringify({
						ontology: mkOnt(),
						ontology_proposals: [],
						staged_candidates: [],
						evolution_locks: null,
					}),
				);
			return new Response(
				JSON.stringify(mkProject(s.includes("p-fast") ? "p-fast" : "p-slow")),
			);
		}) as unknown as typeof fetch;
		const pSlow = useProjectStore.getState().hydrateProjectShell("p-slow");
		await useProjectStore.getState().hydrateProjectShell("p-fast");
		await pSlow;
		expect(slowDone).toBe(false);
		expect(useProjectStore.getState().activeProject?.project_id).toBe("p-fast");

		// Dirty flags & discard preserving trusted_workspaces
		useProjectStore.setState({
			settings: mkSettings(),
			settingsDraft: mkSettings({ max_turns: 99, trusted_workspaces: [] }),
			currentUrl: "/projects/p-fast/step/3",
		});
		useProjectStore.getState().setDirtyFlag("step2Secret", true);
		expect(
			useProjectStore.getState().navigate("/projects/p-fast/step/3?sub=3b"),
		).toBe(true);
		expect(useProjectStore.getState().navigate("/projects/p-fast/step/4")).toBe(
			false,
		);
		useProjectStore.getState().confirmPendingNav();
		expect(useProjectStore.getState().isDirty()).toBe(false);
		expect(
			useProjectStore.getState().settingsDraft?.trusted_workspaces?.length,
		).toBe(1);
	});

	test("validates IDs, 12 upload extensions, 11 targets, Databricks host, and guided jargon regex", () => {
		expect(validateProjectId("import")).not.toBeNull();
		expect(validateProjectId("con")).not.toBeNull();
		expect(validateProjectId("COM1")).not.toBeNull();
		expect(validateProjectId("am_wave-1")).toBeNull();
		expect(Object.keys(UPLOAD_EXT_TO_KIND).length).toBe(12);
		expect(new Set(Object.values(UPLOAD_EXT_TO_KIND)).size).toBe(8);
		expect(inferUploadKind("Fund.PARQUET")).toBe("parquet");
		expect(STATIC_11_TARGETS.length).toBe(11);
		expect(
			buildSourceInspectReq({
				kind: "databricks_uc",
				host: "https://adb-1.azuredatabricks.net/",
			}).uri_or_path,
		).toBe("https://adb-1.azuredatabricks.net");
		expect(FORBIDDEN_GUIDED_JARGON.test("Runs CEGAR check")).toBe(true);
		expect(FORBIDDEN_GUIDED_JARGON.test("Plain business summary")).toBe(false);
	});

	test("App shell renders landmarks, Revision pill, Readiness spans, live regions outside dialogs, and 0 fibo-qa searchbox", () => {
		useProjectStore.setState({
			bootstrapped: true,
			settings: mkSettings({ harness_provider: "agy" }),
			activeProject: mkProject("p1", {
				active_ontology_rev_id: null,
				active_wave_id: null,
			}),
			currentUrl: "/projects/p1/step/1",
		});
		const container = win.document.createElement("div");
		win.document.body.appendChild(container);
		const root = createRoot(container as unknown as Element);
		flushSync(() => {
			root.render(createElement(App));
		});
		expect(
			container.querySelector('nav[aria-label="Primary Workbench Navigation"]'),
		).not.toBeNull();
		expect(
			container.querySelector('nav[aria-label="Workflow Steps"]'),
		).not.toBeNull();
		expect(container.textContent).toContain("Genesis v0.0.0");
		expect(container.textContent).toContain("Ready · agy");
		expect(container.querySelectorAll('[aria-live="polite"]').length).toBe(1);
		expect(container.querySelectorAll('[aria-live="assertive"]').length).toBe(
			1,
		);
		expect(container.querySelectorAll("dialog [aria-live]").length).toBe(0);
		expect(container.innerHTML).not.toContain("fibo-qa");

		flushSync(() => {
			useProjectStore.setState({
				activeProject: mkProject("p1", {
					active_ontology_rev_id: "v0.3.0",
					active_wave_id: "W002",
				}),
			});
		});
		expect(container.textContent).toContain("v0.3.0 · W002");
		root.unmount();
		container.remove();
	});

	test("Phase 5: 4-tier readiness pill, snapshot budget isolation, non-throwing fetchReadiness, Databricks host fallback, RemediationList & RunControlDialog", async () => {
		const rem = {
			summary: "Bind GEMINI_API_KEY to verify gemini.",
			actions: [
				{
					actor: "human" as const,
					label: "Store key",
					command: "uv run opendv secrets set GEMINI_API_KEY --prompt",
					requires_tty: true,
				},
			],
		};
		const pWithRem = createProblemDetails("AGENT_NOT_CONFIGURED", "No key", {
			remediation: rem,
		});
		expect(pWithRem.remediation).toEqual(rem);
		expect(createProblemDetails("OK", "ok").remediation).toBeNull();

		expect(
			buildSourceInspectReq({
				kind: "databricks_uc",
				host: "",
				options: { host: "adb-fallback.azuredatabricks.net/" },
			}).uri_or_path,
		).toBe("https://adb-fallback.azuredatabricks.net");

		// Snapshot budget isolation: activeProject with null caps ignores global ws.max_turns
		useProjectStore.setState({
			settings: mkSettings({
				max_turns: 99,
				max_tokens: 999_000,
				timeout_seconds: 3600,
			}),
			activeProject: mkProject("p1"),
		});
		const b4 = useProjectStore
			.getState()
			.resolveEffectiveBudget("/projects/p1/step/4");
		expect(b4.max_turns).toBe(24);
		expect(b4.max_tokens).toBe(300_000);
		expect(b4.timeout_seconds).toBe(600);

		// 4-tier deriveReadinessPill precedence
		useProjectStore.setState({
			readiness: {
				providers: [
					{
						provider: "agy",
						status: "CONFIGURED_UNVERIFIED",
						credential_source: "cli_login",
						missing_prereqs: [],
						available_models: [],
						active_run: false,
						remediation: null,
					},
				],
			},
		});
		expect(useProjectStore.getState().deriveReadinessPill()).toMatchObject({
			label: "Unverified",
			tone: "warn",
			detailLabel: "Unverified · agy",
		});

		useProjectStore.setState({
			readiness: {
				providers: [
					{
						provider: "agy",
						status: "MISSING_PREREQ",
						credential_source: "none",
						missing_prereqs: ["agy binary not found"],
						available_models: [],
						active_run: false,
						remediation: rem,
					},
				],
			},
		});
		expect(useProjectStore.getState().deriveReadinessPill()).toMatchObject({
			label: "Action Needed",
			tone: "danger",
			detailLabel: "Action Needed · agy",
		});

		// fetchReadiness is non-throwing and preserves existing state on failure
		globalThis.fetch = (async () => {
			throw new Error("network down");
		}) as unknown as typeof fetch;
		await useProjectStore.getState().fetchReadiness();
		expect(useProjectStore.getState().readiness?.providers[0]?.status).toBe(
			"MISSING_PREREQ",
		);

		// bootstrapRoute populates readiness
		installMockFetch([]);
		await useProjectStore.getState().bootstrapRoute("/", "");
		expect(useProjectStore.getState().readiness?.providers.length).toBe(4);
		expect(useProjectStore.getState().deriveReadinessPill().label).toBe(
			"Ready",
		);

		// RemediationList inside <dialog>: zero [aria-live] & zero <output>, Copy announces
		const dlgWrap = win.document.createElement("div");
		win.document.body.appendChild(dlgWrap);
		const dlgRoot = createRoot(dlgWrap as unknown as Element);
		flushSync(() => {
			dlgRoot.render(
				createElement(
					"dialog",
					{ open: true },
					createElement(RemediationList, { remediation: rem }),
				),
			);
		});
		expect(dlgWrap.querySelectorAll("[aria-live], output").length).toBe(0);
		expect(dlgWrap.textContent).toContain("Interactive terminal");
		const copyBtn = dlgWrap.querySelector("button");
		flushSync(() => copyBtn?.click());
		await new Promise((r) => setTimeout(r, 10));
		expect(useProjectStore.getState().liveAnnouncements.polite).toContain(
			"Copied command:",
		);
		dlgRoot.unmount();
		dlgWrap.remove();

		// RunControlDialog: zero <output> even on HOLD_REQUESTED & omits <= 0 fields on raise-budget
		const postedBodies: Record<string, unknown>[] = [];
		globalThis.fetch = (async (_u: RequestInfo | URL, init?: RequestInit) => {
			if (init?.body)
				postedBodies.push(
					JSON.parse(String(init.body)) as Record<string, unknown>,
				);
			return new Response(JSON.stringify(mkRun({ status: "RUNNING" })));
		}) as unknown as typeof fetch;

		useProjectStore.setState({ activeProject: mkProject("p1") });
		const rcWrap = win.document.createElement("div");
		win.document.body.appendChild(rcWrap);
		const rcRoot = createRoot(rcWrap as unknown as Element);
		flushSync(() => {
			rcRoot.render(
				createElement(RunControlDialog, {
					open: true,
					onClose: () => undefined,
					run: mkRun({ status: "HOLD_REQUESTED" }),
				}),
			);
		});
		expect(
			rcWrap.querySelectorAll("dialog [aria-live], dialog output").length,
		).toBe(0);
		rcRoot.unmount();

		const rcRoot2 = createRoot(rcWrap as unknown as Element);
		flushSync(() => {
			rcRoot2.render(
				createElement(RunControlDialog, {
					open: true,
					onClose: () => undefined,
					run: mkRun({ status: "RUNNING" }),
					initialMode: "raise-budget",
				}),
			);
		});
		const numInputs = Array.from(
			rcWrap.querySelectorAll('input[type="number"]'),
		) as unknown as HTMLInputElement[];
		expect(numInputs.length).toBe(3);
		const proto = win.HTMLInputElement.prototype;
		const setVal = (el: HTMLInputElement, val: string) => {
			Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, val);
			el.dispatchEvent(
				new win.Event("input", { bubbles: true }) as unknown as Event,
			);
		};
		flushSync(() => {
			if (numInputs[1]) setVal(numInputs[1], "0");
			if (numInputs[2]) setVal(numInputs[2], "0");
		});
		const submitBtn = rcWrap.querySelector(
			'button[type="submit"]',
		) as HTMLButtonElement | null;
		expect(submitBtn?.disabled).toBe(false);
		flushSync(() => {
			const form = rcWrap.querySelector("form") as unknown as Element | null;
			form?.dispatchEvent(
				new win.Event("submit", {
					bubbles: true,
					cancelable: true,
				}) as unknown as Event,
			);
		});
		await new Promise((r) => setTimeout(r, 10));
		expect(postedBodies[0]).toEqual({ action: "raise-budget", extra_turns: 4 });
		flushSync(() => {
			if (numInputs[0]) setVal(numInputs[0], "0");
		});
		expect(submitBtn?.disabled).toBe(true);
		rcRoot2.unmount();
		rcWrap.remove();
	});
});
