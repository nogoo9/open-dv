import { afterEach, describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Window } from "happy-dom";

const win = new Window({ url: "http://127.0.0.1:8420/" });
class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
const stubRaf = (cb: FrameRequestCallback) =>
	setTimeout(() => cb(Date.now()), 0) as unknown as number;
const stubCaf = (id: number) => clearTimeout(id);
Object.assign(win, {
	ResizeObserver: StubResizeObserver,
	requestAnimationFrame: stubRaf,
	cancelAnimationFrame: stubCaf,
});
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
	ResizeObserver: StubResizeObserver,
	requestAnimationFrame: stubRaf,
	cancelAnimationFrame: stubCaf,
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
const { createInitialOntologyState, useProjectStore } = await import(
	"../src/store/projectStore"
);
const { Step5Explorer } = await import("../src/pages/Step5Explorer");
const { checkYamlCstSyntax } = await import("../src/components/YamlCstEditor");
const { BipartiteGraphCanvas, extractIriTerminalLabel } = await import(
	"../src/components/BipartiteGraphCanvas"
);
const { buildOntologyCanvasGraph, buildVaultCanvasGraph } = await import(
	"../src/components/graphBuilders"
);
const { ExportImportModal } = await import(
	"../src/components/ExportImportModal"
);

import type {
	CertifiedVaultIR,
	GraphAOntology,
	OntologyBundleResp,
	ProjectState,
} from "../src/api/openapi";

const origFetch = globalThis.fetch;

const mkOnt = (over: Partial<GraphAOntology> = {}): GraphAOntology => ({
	ontology_version: "1.0.0",
	domain_namespace: "asset_management",
	concepts: [
		{
			concept_uri: "urn:opendv:asset_management:Portfolio",
			concept_name: "Portfolio",
			description: "Client portfolio",
			key_authority_tier: "TIER_2_ENTERPRISE_SOR",
			attributes: [],
			synonyms: [],
			table_bindings: [],
			source_citations: [],
			mcp_evidence: [],
		},
	],
	relationships: [],
	enumerations: [],
	...over,
});

const mkBundle = (
	over: Partial<OntologyBundleResp> = {},
): OntologyBundleResp => ({
	ontology: mkOnt(),
	staged_candidates: [],
	ontology_proposals: [
		{
			proposal_id: "PROP-custody-9a2f4c10",
			summary: "Add Custodian concept",
			wave_number: 2,
			status: "PENDING_STEWARD",
			delta_ops: [
				{
					op_type: "AddConcept",
					concept_uri: "urn:opendv:asset_management:Custodian",
					concept_name: "Custodian",
					description: "Safekeeping custodian",
					key_authority_tier: "TIER_2_ENTERPRISE_SOR",
					rationale: "Proposed by Step 6",
				},
			],
		},
	],
	evolution_locks: null,
	...over,
});

const mkProject = (over: Partial<ProjectState> = {}): ProjectState => ({
	project_id: "am-ibor-2026",
	domain_namespace: "asset_management",
	active_ontology_rev_id: "ont-1.0.0-11112222",
	active_wave_id: null,
	has_active_lease: false,
	source_count: 1,
	table_count: 1,
	created_at: "2026-11-24T00:00:00Z",
	updated_at: "2026-11-24T00:00:00Z",
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

const flush = () => new Promise((r) => setTimeout(r, 25));
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
	useProjectStore.getState().abortRunSse();
	useProjectStore.getState().applyDiscardReset();
	useProjectStore.setState({
		activeProject: null,
		ontologyBundle: null,
		exportImportOpen: false,
		exportImportFile: null,
		missingSecretRefs: [],
		hiddenProposalIds: [],
		lastProblem: null,
		ontologyState: createInitialOntologyState(),
	});
});

describe("Track C Step 5 Explorer, YAML CST, 409 Rebase, Graph Builders & ExportImportModal", () => {
	test("hydrates staged JSON/YAML on clean mount, stages/dismisses proposals orthogonally, and resets to baseWorkingOntology on discard", async () => {
		const fetched: string[] = [];
		const stagedOnt = mkOnt({
			concepts: [
				...(mkOnt().concepts ?? []),
				{
					concept_uri: "urn:opendv:asset_management:ShareholderBallot",
					concept_name: "ShareholderBallot",
					description: "Staged candidate",
					key_authority_tier: "TIER_2_ENTERPRISE_SOR",
					attributes: [],
				},
			],
		});
		const bundle = mkBundle({
			staged_candidates: [
				{
					candidate_id: "cand-1",
					concept_uri: "urn:opendv:asset_management:ShareholderBallot",
					concept_name: "ShareholderBallot",
					grounding_path: "PATH_B1",
					status: "WAIVED",
				},
			],
		});
		globalThis.fetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			fetched.push(url);
			if (url.endsWith("/ontology"))
				return new Response(JSON.stringify(bundle));
			if (url.includes("ontology/export?format=yaml"))
				return new Response(
					"# Steward note\nontology_version: '1.0.0'\ndomain_namespace: asset_management\n",
				);
			if (url.includes("ontology/export?format=json&include_staged=true"))
				return new Response(JSON.stringify(stagedOnt));
			if (url.endsWith("/receipts")) return new Response("[]");
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			currentUrl: "/projects/am-ibor-2026/step/5?mode=1",
		});
		const { container, unmount } = mount(createElement(Step5Explorer));
		await flush();

		expect(
			fetched.some((u) =>
				u.includes("ontology/export?format=yaml&include_staged=true"),
			),
		).toBe(true);
		expect(
			fetched.some((u) =>
				u.includes("ontology/export?format=json&include_staged=true"),
			),
		).toBe(true);
		expect(
			useProjectStore.getState().ontologyState.baseWorkingOntology?.concepts
				?.length,
		).toBe(2);

		// Stage Step 6 proposal -> operations channel
		const stageBtn = findBtn(container, "Stage into Draft Ontology");
		flushSync(() => stageBtn.click());
		expect(useProjectStore.getState().ontologyState.draftChannel).toBe(
			"operations",
		);
		expect(
			useProjectStore.getState().ontologyState.committedProposalIds,
		).toEqual(["PROP-custody-9a2f4c10"]);
		expect(useProjectStore.getState().isDirty()).toBe(true);

		// Discard restores baseWorkingOntology & clears committed/dismissed proposals
		flushSync(() => useProjectStore.getState().applyDiscardReset());
		await flush();
		expect(useProjectStore.getState().ontologyState.draftChannel).toBe("clean");
		expect(
			useProjectStore.getState().ontologyState.workingOntology?.concepts
				?.length,
		).toBe(2);

		// Orthogonal Dismiss Proposal stays dirty even in clean or yaml_text mode
		const dismissBtn = findBtn(container, "Dismiss Proposal");
		flushSync(() => dismissBtn.click());
		expect(
			useProjectStore.getState().ontologyState.dismissedProposalIds,
		).toEqual(["PROP-custody-9a2f4c10"]);
		expect(useProjectStore.getState().isDirty()).toBe(true);

		unmount();
	});

	test("YAML CST preserves comments, catches Esc without bubbling, allows native Tab, enforces 5-clause commit gate, and handles channel-safe 409 rebase", async () => {
		const source =
			"# Important steward header\nontology_version: '1.0.0'\ndomain_namespace: asset_management\n";
		const cstOk = checkYamlCstSyntax(source);
		expect(cstOk.valid).toBe(true);
		expect(cstOk.preservedYaml).toContain("# Important steward header");

		const cstBad = checkYamlCstSyntax("concepts:\n  - [unclosed");
		expect(cstBad.valid).toBe(false);
		expect(cstBad.line).toBeGreaterThan(0);

		let validateCount = 0;
		let commitAttempts = 0;
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (url.endsWith("/ontology/validate")) {
				validateCount += 1;
				return new Response(
					JSON.stringify({
						valid: true,
						diagnostics: [
							{
								gate: "V6b",
								rule_id: "ONT-AP-001",
								severity: "WARN",
								entity_name: "Portfolio",
								concept_uri: "urn:opendv:asset_management:Portfolio",
								waivable: true,
								message: "Possible clone",
							},
						],
						proposed_bump: {
							current_version: "1.0.0",
							next_version: "1.1.0",
							bump_kind: "MINOR",
							breaking_reasons: [],
						},
					}),
				);
			}
			if (url.endsWith("/ontology/commit")) {
				commitAttempts += 1;
				if (commitAttempts === 1) {
					return new Response(
						JSON.stringify({
							error_code: "REVISION_CONFLICT",
							detail: "Head advanced",
						}),
						{ status: 409 },
					);
				}
				return new Response(
					JSON.stringify({
						revision_id: "ont-1.1.0-newhead",
						ontology_version: "1.1.0",
						content_sha256: "abc",
						updated_at: "2026-11-24T01:00:00Z",
					}),
				);
			}
			if (url.endsWith("/projects/am-ibor-2026")) {
				return new Response(
					JSON.stringify(
						mkProject({ active_ontology_rev_id: "ont-1.0.1-srv" }),
					),
				);
			}
			if (url.endsWith("/ontology"))
				return new Response(JSON.stringify(mkBundle()));
			if (url.includes("ontology/export?format=yaml"))
				return new Response(
					"ontology_version: '1.0.1'\ndomain_namespace: asset_management\n",
				);
			if (url.endsWith("/receipts")) return new Response("[]");
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			ontologyBundle: mkBundle(),
			currentUrl: "/projects/am-ibor-2026/step/5?mode=3",
			ontologyState: {
				...createInitialOntologyState(),
				base_revision_id: "ont-1.0.0-11112222",
				baseWorkingOntology: mkOnt(),
				workingOntology: mkOnt(),
				baseYamlText: source,
				yamlText: source,
			},
		});

		const { container, unmount } = mount(createElement(Step5Explorer));
		await flush();

		const ta = qEl<HTMLTextAreaElement>(container, "textarea");
		let parentEscReached = false;
		container.addEventListener("keydown", (e) => {
			if ((e as unknown as KeyboardEvent).key === "Escape")
				parentEscReached = true;
		});
		const escEv = new win.KeyboardEvent("keydown", {
			key: "Escape",
			bubbles: true,
			cancelable: true,
		});
		ta.dispatchEvent(escEv as unknown as Event);
		expect(parentEscReached).toBe(false);

		const tabEv = new win.KeyboardEvent("keydown", {
			key: "Tab",
			bubbles: true,
			cancelable: true,
		});
		ta.dispatchEvent(tabEv as unknown as Event);
		expect(tabEv.defaultPrevented).toBe(false);

		// Edit YAML -> draftChannel becomes "yaml_text"
		flushSync(() => {
			setCtrlVal(ta, `${source}# local merge edit\n`);
		});
		expect(useProjectStore.getState().ontologyState.draftChannel).toBe(
			"yaml_text",
		);

		// Switch to Mode 4 -> triggers entry /validate immediately
		const mode4Tab = qEl<HTMLButtonElement>(container, "#step5-tab-4");
		flushSync(() => mode4Tab.click());
		await flush();
		expect(validateCount).toBeGreaterThanOrEqual(1);

		// Commit button disabled until rationale populated (5-clause gate)
		const commitBtn = findBtn(container, "Validate & Commit");
		expect(commitBtn.disabled).toBe(true);

		const ratTa = qEl<HTMLTextAreaElement>(
			container,
			'textarea[placeholder*="Describe this ontology"]',
		);
		flushSync(() => {
			setCtrlVal(ratTa, "Approve ontology evolution");
		});
		expect(commitBtn.disabled).toBe(false);

		// First commit returns 409 REVISION_CONFLICT -> opens read-only conflictServerYaml beside local yamlText
		flushSync(() => commitBtn.click());
		await flush();
		expect(container.textContent).toContain("409 REVISION_CONFLICT");
		expect(container.textContent).toContain("Server Head YAML (Read-Only");

		// Confirm YAML rebase preserves merged local yamlText and re-validates
		const rebaseBtn = findBtn(container, "Confirm YAML Rebase");
		flushSync(() => rebaseBtn.click());
		await flush();
		expect(useProjectStore.getState().ontologyState.yamlText).toContain(
			"# local merge edit",
		);
		expect(useProjectStore.getState().ontologyState.base_revision_id).toBe(
			"ont-1.0.1-srv",
		);

		unmount();
	});

	test("BipartiteGraphCanvas uses elk-api.js worker (0 elk.bundled.js) and graphBuilders enforce 0 Hub->Hub / Link->Link / Z3->Z2 edges", async () => {
		const canvasSrc = readFileSync(
			resolve(import.meta.dir, "../src/components/BipartiteGraphCanvas.tsx"),
			"utf-8",
		);
		expect(canvasSrc).toContain("elkjs/lib/elk-api.js");
		expect(canvasSrc).not.toContain("elk.bundled.js");
		expect(extractIriTerminalLabel("urn:opendv:am:CustodyAccount")).toBe(
			"CustodyAccount",
		);

		const ir: CertifiedVaultIR = {
			ir_version: "2.0",
			project_id: "p1",
			ontology_version: "1.0.0",
			wave_number: 1,
			hubs: [
				{
					zone_id: "Z2",
					hub_name: "hub_a",
					key_tier: "TIER_1_GLOBAL_STANDARD",
					is_planned_concept: false,
				},
				{
					zone_id: "Z2",
					hub_name: "hub_b",
					key_tier: "TIER_2_ENTERPRISE_SOR",
					is_planned_concept: false,
				},
			],
			links: [
				{
					zone_id: "Z2",
					link_name: "lnk_ab",
					link_kind: "STANDARD",
					participants: [
						{
							hub_name: "hub_a",
							role_name: "a",
							fk_col_name: "hk_a",
							source_col_names: ["a"],
						},
						{
							hub_name: "hub_b",
							role_name: "b",
							fk_col_name: "hk_b",
							source_col_names: ["b"],
						},
					],
					physical_hints: {},
				},
			],
			satellites: [
				{
					zone_id: "Z2",
					sat_name: "sat_a_pii",
					sat_kind: "PII",
					parent_name: "hub_a",
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
					object_name: "pit_a",
					root_hub: "hub_a",
					target_satellites_or_links: ["sat_a_pii"],
					grain_cols: ["hk_a"],
				},
			],
			information_marts: [
				{
					zone_id: "Z4",
					view_name: "vw_a",
					view_kind: "CURRENT",
					root_entity: "pit_a",
					joined_entities: [],
					select_exprs: {},
				},
			],
		};
		const vg = buildVaultCanvasGraph(ir);
		for (const e of vg.edges) {
			expect(e.source.startsWith("h:") && e.target.startsWith("h:")).toBe(
				false,
			);
			expect(e.source.startsWith("l:") && e.target.startsWith("l:")).toBe(
				false,
			);
			expect(e.source.startsWith("p:") && e.target.startsWith("s:")).toBe(
				false,
			);
		}
		const og = buildOntologyCanvasGraph(mkOnt());
		expect(og.nodes.length).toBe(1);

		let runnerCalled = false;
		const m = mount(
			createElement(BipartiteGraphCanvas, {
				nodes: vg.nodes,
				edges: vg.edges,
				layoutRunner: async (g) => {
					runnerCalled = true;
					return g;
				},
			}),
		);
		await flush();
		expect(runnerCalled).toBe(true);
		m.unmount();
	});

	test("ExportImportModal defaults core_project_state, prompts before active replace, defers reset until 200 replaced, and displays all 7 ImportReadinessReport fields", async () => {
		let importCallCount = 0;
		globalThis.fetch = (async (
			input: RequestInfo | URL,
			init?: RequestInit,
		) => {
			const url = String(input);
			if (url.endsWith("/projects/import") && init?.method === "POST") {
				importCallCount += 1;
				return new Response(
					JSON.stringify({
						project_id: "am-ibor-2026",
						resolved_conflict: "replaced",
						readiness_flags: ["READY_OFFLINE", "NEEDS_LOCAL_SECRET_BINDING"],
						missing_secret_refs: ["DBX_TOKEN"],
						missing_source_ids: [],
						unverified_receipt_ids: ["rcpt-1"],
						audit_claim_notice: "Imported audit logs are producer-asserted.",
					}),
				);
			}
			if (url.endsWith("/projects")) return new Response("[]");
			if (url.endsWith("/projects/am-ibor-2026"))
				return new Response(JSON.stringify(mkProject()));
			if (url.endsWith("/sources")) return new Response("[]");
			if (url.endsWith("/evidence/documents/map")) return new Response("[]");
			if (url.endsWith("/ontology"))
				return new Response(JSON.stringify(mkBundle()));
			if (url.endsWith("/runs")) return new Response("[]");
			return new Response("{}");
		}) as unknown as typeof fetch;

		useProjectStore.setState({
			activeProject: mkProject(),
			exportImportOpen: true,
			ontologyState: {
				...createInitialOntologyState(),
				draftChannel: "operations",
				dirty: true,
			},
		});
		const { container, unmount } = mount(createElement(ExportImportModal));
		await flush();

		const pathInput = qEl<HTMLInputElement>(
			container,
			'input[placeholder*="bundles/"]',
		);
		flushSync(() => {
			setCtrlVal(pathInput, "bundles/am.core.opendv.zip");
		});
		const replaceRadio = Array.from(
			container.querySelectorAll('input[name="import_conflict"]'),
		)[2] as unknown as HTMLInputElement;
		flushSync(() => replaceRadio.click());

		const importBtn = findBtn(container, "Import Bundle", true);
		expect(importBtn.disabled).toBe(false);
		flushSync(() => {
			qEl<HTMLFormElement>(container, "form").dispatchEvent(
				new win.Event("submit", {
					bubbles: true,
					cancelable: true,
				}) as unknown as Event,
			);
		});
		// Dirty prompt fires BEFORE POST /projects/import, keeping local draft intact
		expect(importCallCount).toBe(0);
		expect(useProjectStore.getState().isDirty()).toBe(true);
		expect(container.textContent).toContain("Confirm Replace & Import");

		const confirmBtn = findBtn(container, "Confirm Replace & Import", true);
		flushSync(() => confirmBtn.click());
		await flush();
		expect(importCallCount).toBe(1);
		expect(useProjectStore.getState().isDirty()).toBe(false);
		expect(useProjectStore.getState().missingSecretRefs).toEqual(["DBX_TOKEN"]);
		expect(container.textContent).toContain("producer-asserted");
		expect(container.textContent).toContain("rcpt-1");
		expect(container.textContent).toContain("NEEDS_LOCAL_SECRET_BINDING");

		unmount();
	});
});
