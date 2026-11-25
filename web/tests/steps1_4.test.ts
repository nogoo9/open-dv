import { afterEach, describe, expect, test } from "bun:test";
import { Window } from "happy-dom";

const win = new Window({ url: "http://127.0.0.1:8420/" });
const g = globalThis as unknown as Record<string, unknown>;
for (const k of [
	"window",
	"document",
	"navigator",
	"location",
	"history",
	"HTMLElement",
	"HTMLDialogElement",
	"HTMLInputElement",
	"HTMLTextAreaElement",
	"HTMLSelectElement",
	"HTMLButtonElement",
	"HTMLFormElement",
	"Element",
	"Node",
	"Event",
	"KeyboardEvent",
	"MouseEvent",
	"FormData",
	"File",
	"Blob",
]) {
	g[k] = (win as unknown as Record<string, unknown>)[k];
}
g.IS_REACT_ACT_ENVIRONMENT = true;

let copiedText = "";
Object.defineProperty(win.navigator, "clipboard", {
	configurable: true,
	value: {
		writeText: async (text: string) => {
			copiedText = text;
		},
	},
});

const dlgProto = win.HTMLDialogElement.prototype as unknown as {
	showModal?: () => void;
	close?: () => void;
};
dlgProto.showModal = function showModal(this: {
	open: boolean;
	setAttribute: (k: string, v: string) => void;
}) {
	this.open = true;
	this.setAttribute("open", "");
};
dlgProto.close = function close(this: {
	open: boolean;
	removeAttribute: (k: string) => void;
}) {
	this.open = false;
	this.removeAttribute("open");
};

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const {
	FORBIDDEN_GUIDED_JARGON,
	MAX_UPLOAD_BYTES,
	UPLOAD_EXT_TO_KIND,
	inferUploadKind,
} = await import("../src/api/openapi");
const { createInitialDirtyFlags, useProjectStore } = await import(
	"../src/store/projectStore"
);
const { Step1Scope } = await import("../src/pages/Step1Scope");
const { Step2Sources } = await import("../src/pages/Step2Sources");
const { Step3Preview } = await import("../src/pages/Step3Preview");
const { Step4Discovery } = await import("../src/pages/Step4Discovery");
const { AskFiboDrawer } = await import("../src/components/AskFiboDrawer");
const { CitationInspectorDrawer, normalizeAlgA9bText } = await import(
	"../src/components/CitationInspectorDrawer"
);
const { AgentIoDrawer } = await import("../src/components/AgentIoDrawer");

const origFetch = globalThis.fetch;
let roots: ReturnType<typeof createRoot>[] = [];

const PROJ_SNAP = {
	cegar_policy: "balanced" as const,
	dbt_sql_dialect: "duckdb" as const,
	discovery_mode: "incremental_new" as const,
	harness_provider: "agy" as const,
	secret_precedence: "store_first" as const,
	v7_critic_tier: "FRONTIER_CRITIC" as const,
};

const WORKSPACE_SETTINGS = {
	...PROJ_SNAP,
	schema_version: "2.0" as const,
	secret_backend: "keyring" as const,
	updated_at: "2026-01-01T00:00:00Z",
	connection_profiles: {
		prod_uc: {
			adapter_id: "databricks_uc",
			display_name: "Prod UC",
			uri_or_path: "uc://prod.gold",
			profile_timeout_seconds: 30,
			options: {
				host: "https://saved.cloud",
				http_path: "/sql/2",
				catalog: "prod",
				schema: "gold",
			},
			secret_refs: ["SAVED_REF"],
		},
	},
};

const jsonResp = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});

const problemResp = (
	status: number,
	error_code: string,
	title: string,
	detail: string,
) =>
	jsonResp(
		{
			type: `https://opendv.local/errors/${error_code}`,
			title,
			status,
			detail,
			error_code,
			retryable: false,
			request_id: "req-1",
		},
		status,
	);

const tick = () => new Promise<void>((r) => setTimeout(r, 12));

function mount(el: React.ReactElement): HTMLElement {
	const host = win.document.createElement("div") as unknown as HTMLElement;
	win.document.body.appendChild(
		host as unknown as Parameters<typeof win.document.body.appendChild>[0],
	);
	const root = createRoot(host);
	roots.push(root);
	act(() => {
		root.render(el);
	});
	return host;
}

const settle = () => act(async () => void (await tick()));

const click = (el: Element) =>
	act(async () => {
		el.dispatchEvent(
			new win.MouseEvent("click", { bubbles: true }) as unknown as Event,
		);
		await tick();
	});

const setValue = (el: Element, value: string) => {
	const proto =
		el.tagName === "TEXTAREA"
			? win.HTMLTextAreaElement.prototype
			: el.tagName === "SELECT"
				? win.HTMLSelectElement.prototype
				: win.HTMLInputElement.prototype;
	const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
	return act(async () => {
		setter?.call(el, value);
		el.dispatchEvent(
			new win.Event("input", { bubbles: true }) as unknown as Event,
		);
		el.dispatchEvent(
			new win.Event("change", { bubbles: true }) as unknown as Event,
		);
		await tick();
	});
};

const submitForm = (form: Element) =>
	act(async () => {
		form.dispatchEvent(
			new win.Event("submit", {
				bubbles: true,
				cancelable: true,
			}) as unknown as Event,
		);
		await tick();
	});

const btn = (
	root: Pick<Element, "querySelectorAll">,
	text: string,
	exact = false,
) =>
	[...root.querySelectorAll("button")].find((b) =>
		exact ? b.textContent?.trim() === text : b.textContent?.includes(text),
	) as unknown as HTMLButtonElement | undefined;

const dropFile = (zone: Element, file: File) => {
	const ev = new win.Event("drop", { bubbles: true, cancelable: true });
	Object.defineProperty(ev, "dataTransfer", { value: { files: [file] } });
	return act(async () => {
		zone.dispatchEvent(ev as unknown as Event);
		await tick();
	});
};

afterEach(() => {
	useProjectStore.getState().abortRunSse();
	for (const r of roots) act(() => r.unmount());
	roots = [];
	win.document.body.innerHTML = "";
	globalThis.fetch = origFetch;
	copiedText = "";
	useProjectStore.setState({
		currentUrl: "/",
		activeProject: null,
		projectsSummary: [],
		sources: [],
		documentMaps: [],
		ontologyBundle: null,
		vaultIr: null,
		failedVerifyWaveId: null,
		missingSecretRefs: [],
		settings: null,
		step3ScanDraft: {},
		hiddenProposalIds: [],
		dirtyFlags: createInitialDirtyFlags(),
		activeStep4Run: null,
		activeStep6Run: null,
		sseEvents: [],
		lastReceivedSeq: 0,
		sseDisconnected: false,
		sseActiveRunId: null,
		exportImportOpen: false,
		exportImportFile: null,
		lastProblem: null,
	});
});

describe("Step 1 · Scope, Seeds & Baseline Dropzone", () => {
	test("UAT-V27-S1-01..06 validates project_id/domain, 4 modes, dropzone, and lease delete", async () => {
		const calls: string[] = [];
		const projA = {
			project_id: "am-ibor-2026",
			domain_namespace: "asset_management",
			active_ontology_rev_id: null,
			active_wave_id: null,
			has_active_lease: true,
			source_count: 1,
			table_count: 2,
			created_at: "2026-01-01T00:00:00Z",
			updated_at: "2026-01-02T00:00:00Z",
			settings_snapshot: PROJ_SNAP,
		};
		const projB = {
			...projA,
			project_id: "retail-core",
			has_active_lease: false,
			updated_at: "2026-01-01T00:00:00Z",
		};
		const cap: { init: Record<string, unknown> | null } = { init: null };

		globalThis.fetch = (async (input, init) => {
			const url = String(input);
			calls.push(`${init?.method ?? "GET"} ${url}`);
			if (url.endsWith("/api/v1/workspace-seeds")) {
				return jsonResp([
					{
						seed_id: "seed-am",
						relpath: "seeds/am.yaml",
						domain_namespace: "asset_management",
						concept_count: 4,
					},
				]);
			}
			if (url.endsWith("/api/v1/starter-packs")) {
				return jsonResp([
					{
						pack_id: "am_core_spine",
						title: "AM Core Spine",
						domain_namespace: "asset_management",
						hub_count: 3,
					},
				]);
			}
			if (url.endsWith("/api/v1/starter-packs/am_core_spine/preview")) {
				return jsonResp({
					pack_id: "am_core_spine",
					ontology: {
						ontology_version: "0.1.0",
						domain_namespace: "asset_management",
						concepts: [
							{
								concept_name: "InstitutionalFiler",
								concept_uri: "urn:opendv:am:InstitutionalFiler",
								description: "Filer",
								key_authority_tier: "TIER_1_GLOBAL_STANDARD",
								governance_tags: ["planned_stub"],
								table_bindings: [],
							},
						],
						relationships: [],
						enumerations: [],
					},
				});
			}
			if (url.endsWith("/api/v1/projects") && init?.method === "POST") {
				cap.init = JSON.parse(String(init.body));
				return jsonResp(projB);
			}
			if (url.endsWith("/api/v1/projects")) return jsonResp([projB]);
			if (
				url.endsWith("/api/v1/projects/retail-core") &&
				init?.method === "DELETE"
			) {
				return jsonResp({ deleted: true });
			}
			if (url.endsWith("/api/v1/projects/retail-core")) return jsonResp(projB);
			if (url.includes("/sources") || url.includes("/document-maps"))
				return jsonResp([]);
			if (url.includes("/ontology") || url.includes("/vault/ir"))
				return problemResp(404, "NOT_FOUND", "None", "None");
			return jsonResp({});
		}) as typeof fetch;

		useProjectStore.setState({
			currentUrl: "/",
			activeProject: projA,
			projectsSummary: [
				{
					project_id: projA.project_id,
					domain_namespace: projA.domain_namespace,
					updated_at: projA.updated_at,
				},
				{
					project_id: projB.project_id,
					domain_namespace: projB.domain_namespace,
					updated_at: projB.updated_at,
				},
			],
		});

		const host = mount(React.createElement(Step1Scope));
		await settle();

		expect(
			calls.filter(
				(c) =>
					c === "GET /api/v1/projects/am-ibor-2026" ||
					c === "GET /api/v1/projects/retail-core",
			).length,
		).toBe(0);

		const idInput = host.querySelector(
			'input[placeholder="e.g. am-ibor-2026"]',
		) as Element;
		const nsInput = [...host.querySelectorAll("input")][1] as Element;
		const form = host.querySelector("form") as Element;

		for (const badId of ["import", "con", "Bad Space"]) {
			await setValue(idInput, badId);
			await submitForm(form);
			expect(calls.some((c) => c === "POST /api/v1/projects")).toBe(false);
		}
		await setValue(idInput, "valid-proj-1");
		await setValue(nsInput, "Invalid NS!");
		await submitForm(form);
		expect(calls.some((c) => c === "POST /api/v1/projects")).toBe(false);
		await setValue(nsInput, "asset_management");

		const radios = [...host.querySelectorAll('input[type="radio"]')];
		await click(radios[2] as Element);
		expect(host.textContent).toContain("is_planned_concept=true");

		const dropZone = host.querySelector(
			'[aria-label="Baseline or Bundle Dropzone"]',
		) as Element;
		await dropFile(dropZone, new File(["zip"], "bundle.opendv.zip"));
		expect(useProjectStore.getState().exportImportOpen).toBe(true);
		expect(useProjectStore.getState().exportImportFile?.name).toBe(
			"bundle.opendv.zip",
		);

		await dropFile(
			dropZone,
			new File(["domain_namespace: test\n"], "base.yaml"),
		);
		await click(radios[3] as Element);
		await submitForm(form);
		expect(cap.init).toMatchObject({
			project_id: "valid-proj-1",
			baseline_format: "yaml",
		});

		expect(btn(host, "Delete Project")?.disabled).toBe(true);
		useProjectStore.setState({ activeProject: projB, projectsSummary: [] });
		await settle();
		const delBtn = btn(host, "Delete Project");
		expect(delBtn?.disabled).toBe(false);
		await click(delBtn as unknown as Element);
		await click(btn(host, "Confirm Delete") as unknown as Element);
		expect(
			calls.some((c) => c.startsWith("DELETE /api/v1/projects/retail-core")),
		).toBe(true);
	});
});

describe("Step 2 · Connectors, Uploads, Inline Domain Notes & Recovery", () => {
	test("UAT-V27-S2-01..07 enforces upload limits, 0 mount probes, LOCK-W00N & recovery dialogs", async () => {
		const calls: string[] = [];
		const srcLocked = {
			source_id: "src-locked",
			kind: "sqlite",
			uri_or_filename: "data/am.sqlite",
			status: "METADATA_ONLY_BINARY_ABSENT" as const,
			table_count: 1,
			page_count: 0,
			locked_in_wave: "W001",
			attached_at: "2026-01-01T00:00:00Z",
			connector_config: {
				adapter_id: "sqlite",
				display_name: "SQLite",
				options: { uri: "data/am.sqlite" },
				secret_refs: [],
			},
		};
		const srcDb = {
			source_id: "src-uc",
			kind: "databricks_uc",
			uri_or_filename: "uc://cat.sch",
			status: "NEEDS_LOCAL_SECRET_BINDING" as const,
			table_count: 0,
			page_count: 0,
			locked_in_wave: null,
			attached_at: "2026-01-01T00:00:00Z",
			connector_config: {
				adapter_id: "databricks_uc",
				display_name: "Databricks Unity Catalog",
				options: {
					host: "https://db.cloud",
					http_path: "/sql/1",
					catalog: "main",
					schema: "core",
				},
				secret_refs: ["DBX_PAT"],
			},
		};
		const cap: {
			patches: Record<string, unknown>[];
			sec: Record<string, unknown> | null;
			note: string;
		} = { patches: [], sec: null, note: "" };

		globalThis.fetch = (async (input, init) => {
			const url = String(input);
			calls.push(`${init?.method ?? "GET"} ${url}`);
			if (url.endsWith("/api/v1/sources/adapters")) {
				return jsonResp([
					{ adapter_id: "sqlite", display_name: "SQLite Database", fields: [] },
					{ adapter_id: "duckdb", display_name: "DuckDB Database", fields: [] },
					{
						adapter_id: "parquet",
						display_name: "Apache Parquet File",
						fields: [],
					},
					{ adapter_id: "csv", display_name: "CSV Table", fields: [] },
					{
						adapter_id: "xlsx",
						display_name: "Excel Workbook (XLSX)",
						fields: [],
					},
					{
						adapter_id: "databricks_uc",
						display_name: "Databricks Unity Catalog",
						fields: [],
					},
				]);
			}
			if (url.endsWith("/sources")) return jsonResp([srcLocked, srcDb]);
			if (url.endsWith("/evidence/documents/map")) return jsonResp([]);
			if (url.endsWith("/api/v1/secrets") && init?.method === "POST") {
				cap.sec = JSON.parse(String(init.body));
				return jsonResp({
					key: cap.sec?.key,
					backend: "user_state_file",
					present: true,
				});
			}
			if (url.endsWith("/api/v1/secrets"))
				return jsonResp([
					{ key: "SAVED_TOK", backend: "keyring", present: true },
				]);
			if (url.endsWith("/api/v1/projects/p2")) {
				return jsonResp({
					project_id: "p2",
					domain_namespace: "am",
					has_active_lease: false,
					source_count: 2,
					table_count: 1,
					created_at: "t",
					updated_at: "t",
					settings_snapshot: PROJ_SNAP,
				});
			}
			if (url.endsWith("/api/v1/sources/inspect") && init?.method === "POST") {
				return jsonResp({
					source_id: "insp",
					kind: "databricks_uc",
					table_count: 2,
					schema_fingerprint: "abc123def456789",
					tables: [
						{
							table_id: "t1",
							source_id: "insp",
							table_name: "positions",
							cluster_id: "c1",
							col_count: 4,
							pii_col_count: 0,
							badge: "NEW",
						},
					],
				});
			}
			if (url.endsWith("/sources/upload") && init?.method === "POST") {
				const fd = init.body as FormData;
				cap.note = (fd.get("file") as File).name;
				return jsonResp(srcLocked);
			}
			if (url.endsWith("/evidence/tables/index")) {
				return jsonResp([
					{
						table_id: "t1",
						source_id: "src-locked",
						table_name: "positions",
						cluster_id: "c1",
						col_count: 2,
						pii_col_count: 0,
						badge: "NEW",
					},
				]);
			}
			if (url.includes("/evidence/tables/t1")) {
				return jsonResp({
					table_id: "t1",
					source_id: "src-locked",
					columns: ["id", "acct"],
					rows: [["1", "A-100"]],
					total_rows: 1,
				});
			}
			if (url.endsWith("/sources/src-uc") && init?.method === "PATCH") {
				cap.patches.push(JSON.parse(String(init.body)));
				return jsonResp({ ...srcDb, status: "READY_OFFLINE" });
			}
			return jsonResp({});
		}) as typeof fetch;

		expect(Object.keys(UPLOAD_EXT_TO_KIND).length).toBe(12);
		expect(new Set(Object.values(UPLOAD_EXT_TO_KIND)).size).toBe(8);
		expect(inferUploadKind("notes.markdown")).toBe("markdown");

		useProjectStore.setState({
			currentUrl: "/projects/p2/step/2",
			settings: WORKSPACE_SETTINGS,
			activeProject: {
				project_id: "p2",
				domain_namespace: "am",
				has_active_lease: false,
				source_count: 2,
				table_count: 1,
				created_at: "t",
				updated_at: "t",
				settings_snapshot: PROJ_SNAP,
			},
		});

		const host = mount(React.createElement(Step2Sources));
		await settle();

		expect(
			calls.some(
				(c) =>
					c.includes("/evidence/tables/index") ||
					c.includes("/ontology") ||
					c.includes("/vault/ir"),
			),
		).toBe(false);

		await click(btn(host, "Databricks Unity Catalog") as unknown as Element);
		await click(btn(host, "Inspect Source") as unknown as Element);
		expect(calls.some((c) => c === "POST /api/v1/sources/inspect")).toBe(true);

		const upSection = [...host.querySelectorAll("section")].find((s) =>
			s.querySelector("h2")?.textContent?.includes("Upload File"),
		) as Element;
		await dropFile(upSection, new File(["@prefix : <urn:> ."], "export.ttl"));
		expect(host.textContent).toContain("export-only in Step 5");
		const huge = new File(["x"], "huge.csv");
		Object.defineProperty(huge, "size", { value: MAX_UPLOAD_BYTES + 1 });
		await dropFile(upSection, huge);
		expect(host.textContent).toContain("250 MB");

		const noteForm = [...host.querySelectorAll("form")].find((f) =>
			f.textContent?.includes("Inline Domain Note"),
		) as Element;
		await setValue(noteForm.querySelector("input") as Element, "Custody Rules");
		await setValue(
			noteForm.querySelector("textarea") as Element,
			"Positions settle T+1.",
		);
		await submitForm(noteForm);
		expect(cap.note).toBe("custody-rules.md");

		expect(host.textContent).toContain("LOCK-W001");
		expect(btn(host, "Detach")?.disabled).toBe(true);

		const inspBefore = calls.filter((c) =>
			c.includes("/sources/inspect"),
		).length;
		await click(btn(host, "Preview", true) as unknown as Element);
		expect(host.textContent).toContain("A-100");
		expect(calls.filter((c) => c.includes("/sources/inspect")).length).toBe(
			inspBefore,
		);
		await click(btn(host, "Close") as unknown as Element);

		await click(btn(host, "Bind Secret Token") as unknown as Element);
		const dlg = host.querySelector("dialog") as Element;
		const profileSelect = [...dlg.querySelectorAll("select")].find((s) =>
			s.textContent?.includes("prod_uc"),
		) as Element;
		await setValue(profileSelect, "prod_uc");
		await setValue(
			dlg.querySelector('input[type="password"]') as Element,
			"secret-token-xyz",
		);
		await submitForm(dlg.querySelector("form") as Element);
		expect(cap.sec).toEqual({ key: "SAVED_REF", value: "secret-token-xyz" });
		expect(cap.patches.length).toBe(1);

		await click(btn(host, "Edit", true) as unknown as Element);
		const dlg2 = host.querySelector("dialog") as Element;
		await click(btn(dlg2, "Upload Replacement File") as unknown as Element);
		expect(dlg2.querySelector('input[type="file"]')).not.toBeNull();
		await click(btn(dlg2, "Workspace Path / URI") as unknown as Element);
		await setValue(
			dlg2.querySelector("input.input.mono") as Element,
			"data/rebound.sqlite",
		);
		expect(useProjectStore.getState().dirtyFlags.step2Secret).toBe(true);
		await click(btn(dlg2, "Cancel") as unknown as Element);
		await click(btn(dlg2, "Discard & Close") as unknown as Element);
	});
});

describe("Step 3 · Preview, Clusters, ConceptMemory & Scan Gate", () => {
	test("UAT-V27-S3-01..08, A11Y-01, RESP-01 handle 400 tables <300ms, ConceptMemory & 3a<->3b draft persistence", async () => {
		const bigTables = Array.from({ length: 400 }, (_, i) => ({
			table_id: `tbl-${i}`,
			source_id: "src-off",
			table_name: i < 3 ? `shard_000${i + 1}` : `entity_${i}`,
			cluster_id: `cl-${Math.floor(i / 50)}`,
			col_count: 5,
			pii_col_count: i === 0 ? 2 : 0,
			badge: "NEW" as const,
			pk_cols: ["id"],
			fk_refs: ["acct_id"],
		}));
		const doc1 = {
			doc_id: "doc-1",
			filename: "policy.pdf",
			format: "pdf" as const,
			page_count: 2,
			approved_scan_pages: [1],
			outline: [{ page_start: 1, title: "Overview", level: 1 }],
			pages: [
				{
					page_number: 1,
					char_count: 240,
					has_images: false,
					is_scanned_or_low_density: false,
					ocr_box_count: 0,
					canonical_text_sha256: "deadbeef1234",
					heading_preview: "Intro",
				},
				{
					page_number: 2,
					char_count: 10,
					has_images: true,
					is_scanned_or_low_density: true,
					ocr_box_count: 3,
					canonical_text_sha256: "cafebabe5678",
					heading_preview: "Scan",
				},
			],
		};
		const docOff = {
			doc_id: "src-doc-off",
			filename: "absent.pdf",
			format: "pdf" as const,
			page_count: 1,
			approved_scan_pages: [],
			outline: [],
			pages: [
				{
					page_number: 1,
					char_count: 100,
					has_images: false,
					is_scanned_or_low_density: false,
					ocr_box_count: 0,
					canonical_text_sha256: "00001111",
					heading_preview: "Off",
				},
			],
		};

		const calls: string[] = [];
		const cap: { approve: Record<string, unknown> | null } = { approve: null };
		globalThis.fetch = (async (input, init) => {
			const url = String(input);
			calls.push(`${init?.method ?? "GET"} ${url}`);
			if (url.endsWith("/evidence/tables/index")) return jsonResp(bigTables);
			if (url.endsWith("/evidence/concept-memory") && init?.method === "POST") {
				const body = JSON.parse(String(init.body));
				return jsonResp({
					term: body.term,
					state: body.state,
					concept_uri: null,
				});
			}
			if (url.endsWith("/evidence/concept-memory")) return jsonResp([]);
			if (url.endsWith("/evidence/documents/map"))
				return jsonResp([doc1, docOff]);
			if (url.includes("/evidence/tables/tbl-0")) {
				const off = url.includes("offset=50") ? 50 : 0;
				return jsonResp({
					table_id: "tbl-0",
					source_id: "src-off",
					columns: ["id", "ssn"],
					rows: [[String(off + 1), "***"]],
					total_rows: 120,
				});
			}
			if (url.includes("/evidence/documents/doc-1/pages")) {
				return jsonResp([
					{
						doc_id: "doc-1",
						page_number: 1,
						masked_text: "Approved text p1",
						is_scanned: false,
						scan_approved: true,
					},
				]);
			}
			if (
				url.endsWith("/evidence/documents/doc-1/approve-scan") &&
				init?.method === "POST"
			) {
				cap.approve = JSON.parse(String(init.body));
				return jsonResp({ ...doc1, approved_scan_pages: [1, 2] });
			}
			return jsonResp({});
		}) as typeof fetch;

		useProjectStore.setState({
			currentUrl: "/projects/p3/step/3?sub=3a",
			activeProject: {
				project_id: "p3",
				domain_namespace: "am",
				has_active_lease: false,
				source_count: 2,
				table_count: 400,
				created_at: "t",
				updated_at: "t",
				settings_snapshot: PROJ_SNAP,
			},
			sources: [
				{
					source_id: "src-off",
					kind: "sqlite",
					uri_or_filename: "off.sqlite",
					status: "METADATA_ONLY_BINARY_ABSENT",
					table_count: 400,
					attached_at: "t",
				},
				{
					source_id: "src-doc-off",
					kind: "pdf",
					uri_or_filename: "absent.pdf",
					status: "METADATA_ONLY_BINARY_ABSENT",
					table_count: 0,
					page_count: 1,
					attached_at: "t",
				},
			],
		});

		const t0 = performance.now();
		const host = mount(React.createElement(Step3Preview));
		await settle();
		expect(performance.now() - t0).toBeLessThan(300);

		expect(host.textContent).toContain("shard_*");
		expect(host.textContent).toContain("3 shards");
		await click(btn(host, "shard_*") as unknown as Element);
		await click(btn(host, "shard_0001") as unknown as Element);

		expect(host.textContent).toContain("METADATA_ONLY_BINARY_ABSENT");
		expect(host.textContent).toContain("src-off");
		expect(
			host.querySelector('.table-scroll-region[tabindex="0"]'),
		).not.toBeNull();
		expect(
			host.querySelector(
				'table.responsive-table[role="table"] td[role="cell"][data-label="ssn"]',
			),
		).not.toBeNull();
		await click(btn(host, "Next 50") as unknown as Element);
		expect(calls.some((c) => c.includes("offset=50&limit=50"))).toBe(true);

		const previewCountBefore = calls.filter((c) =>
			c.includes("/evidence/tables/tbl-0"),
		).length;
		const termBtn = btn(host, "(UNSET)") as unknown as HTMLButtonElement;
		await click(termBtn as unknown as Element);
		expect(termBtn.getAttribute("data-memory-state")).toBe("PINNED");
		await click(termBtn as unknown as Element);
		expect(termBtn.getAttribute("data-memory-state")).toBe("MUTED");
		await click(termBtn as unknown as Element);
		expect(termBtn.getAttribute("data-memory-state")).toBe("UNSET");
		expect(
			calls.filter((c) => c.includes("/evidence/tables/tbl-0")).length,
		).toBe(previewCountBefore);

		await act(async () => {
			(host.querySelector("#step3-tab-3a") as Element).dispatchEvent(
				new win.KeyboardEvent("keydown", {
					key: "ArrowRight",
					bubbles: true,
				}) as unknown as Event,
			);
			await tick();
		});
		expect(useProjectStore.getState().currentUrl).toContain("sub=3b");

		expect(host.textContent).toContain("Approved ✓");
		expect(host.textContent).toContain("SCANNED · 3 boxes");
		await click(
			host.querySelector(
				'form.callout-coral input[type="checkbox"]',
			) as Element,
		);
		await setValue(
			host.querySelector("form.callout-coral textarea") as Element,
			"Verified OCR box mask",
		);

		await act(async () => {
			(host.querySelector("#step3-tab-3b") as Element).dispatchEvent(
				new win.KeyboardEvent("keydown", {
					key: "Home",
					bubbles: true,
				}) as unknown as Event,
			);
			await tick();
		});
		await click(host.querySelector("#step3-tab-3b") as Element);
		expect(
			(
				host.querySelector(
					"form.callout-coral textarea",
				) as unknown as HTMLTextAreaElement
			).value,
		).toBe("Verified OCR box mask");

		await click(btn(host, "absent.pdf") as unknown as Element);
		expect(host.textContent).toContain("METADATA_ONLY_BINARY_ABSENT");
		expect(calls.some((c) => c.includes("/documents/src-doc-off/pages"))).toBe(
			false,
		);

		await click(btn(host, "policy.pdf") as unknown as Element);
		await submitForm(host.querySelector("form.callout-coral") as Element);
		expect(cap.approve).toEqual({
			page_numbers: [2],
			rationale: "Verified OCR box mask",
			allow_unredacted_external_ocr: false,
		});
		expect(useProjectStore.getState().step3ScanDraft["doc-1"]).toBeUndefined();
	});
});

describe("Step 4 · Discovery, Holds, AgentIO, CitationInspector & AskFibo", () => {
	test("UAT-V27-S4-01..14, SSE-01, AskFiboProps, Alg A.9b, and FORBIDDEN_GUIDED_JARGON check", async () => {
		const candA = {
			candidate_id: "cand-1",
			concept_name: "InstitutionalFiler",
			concept_uri: "urn:opendv:am:InstitutionalFiler",
			grounding_path: "PATH_A1" as const,
			status: "PROMOTED" as const,
			bm25_score: "0.94",
			rationale: "Grounded in table + policy PDF",
			table_bindings: [
				{ source_id: "src-1", table_id: "tbl-1", col_name: "cik" },
			],
			source_citations: [
				{
					doc_id: "doc-canon",
					page_number: 1,
					verbatim_quote: "Registered Institutional Filer",
					occurrence: 2,
					content_sha256: "abc12345",
				},
				{
					doc_id: "doc-absent",
					page_number: 1,
					verbatim_quote: "Absent",
					occurrence: 1,
				},
				{
					doc_id: "doc-ocr",
					page_number: 2,
					verbatim_quote: "OCR text",
					occurrence: 1,
				},
			],
			mcp_evidence: [
				{
					receipt_id: "rc-1",
					server_id: "fibo-qa",
					tool_name: "search",
					masked_excerpt: "SEC Filer",
					content_sha256: "f00d",
				},
			],
		};
		const holdRun = {
			run_id: "run4-hold",
			project_id: "p4",
			step: "step4" as const,
			status: "HOLD_CHECKPOINTED" as const,
			phase: "HOLD",
			attempt: 1,
			budget_tokens_used: 100,
			budget_turns_used: 2,
			elapsed_seconds: 5,
			last_seq: 4,
			pending_questions: [
				{
					question_id: "q1",
					title: "Confirm Primary Identifier",
					concept_name: "InstitutionalFiler",
					prompt: "Use CIK as primary identifier for Filer?",
					impact_if_locked: "Locks Hub business key to CIK",
					recommended_answer: "Yes, use CIK",
					sample_rows: ["id | cik", "1 | 0000102909"],
				},
				{
					question_id: "q2",
					title: "Clarify Custodial Role",
					concept_name: "Custodian",
					prompt: "Specify custodial scope",
					impact_if_locked: "Scopes custodian links",
					recommended_answer: null,
					sample_rows: ["Custodian row A"],
				},
			],
		};

		const calls: string[] = [];
		const cap: { ans: Record<string, unknown> | null } = { ans: null };
		let serverRun: Record<string, unknown> = holdRun;
		globalThis.fetch = (async (input, init) => {
			const url = String(input);
			calls.push(`${init?.method ?? "GET"} ${url}`);
			if (url.includes("/runs/run4-hold/events")) {
				return new Response("", {
					status: 200,
					headers: { "Content-Type": "text/event-stream" },
				});
			}
			if (url.includes("/runs/run4-hold/logs")) {
				return jsonResp({
					events: [
						{
							run_id: "run4-hold",
							step: "step4",
							seq: 1,
							ts: "2026-01-01T00:00:00Z",
							event_type: "TOOL_CALL",
							payload: { tool: "search_fibo" },
						},
					],
					next_since_seq: null,
				});
			}
			if (
				url.endsWith("/ontology/runs/run4-hold/answers") &&
				init?.method === "POST"
			) {
				cap.ans = JSON.parse(String(init.body));
				serverRun = {
					...holdRun,
					status: "COMPLETED",
					pending_questions: [],
				};
				return jsonResp(serverRun);
			}
			if (url.endsWith("/runs/run4-hold")) return jsonResp(serverRun);
			if (url.includes("/runs?")) return jsonResp([holdRun]);
			if (url.endsWith("/ontology/construct") && init?.method === "POST") {
				return problemResp(
					409,
					"NEEDS_LOCAL_SECRET_BINDING",
					"Secret Missing",
					"Bind OPENAI_API_KEY",
				);
			}
			if (url.endsWith("/ontology")) {
				return jsonResp({
					ontology: {
						ontology_version: "0.1.0",
						domain_namespace: "asset_management",
						concepts: [],
						relationships: [],
						enumerations: [],
					},
					staged_candidates: [candA],
					ontology_proposals: [],
				});
			}
			if (url.endsWith("/receipts")) {
				return jsonResp([
					{
						receipt_id: "rc-1",
						server_id: "fibo-qa",
						tool_name: "search",
						args_sha256: "111",
						content_sha256: "222",
						masked_excerpt: "SEC Filer",
						doc_id: "doc-ocr",
						page_number: 2,
					},
				]);
			}
			if (
				url.includes("/ontology/citations/resolve") &&
				init?.method === "POST"
			) {
				const b = JSON.parse(String(init.body));
				if (b.doc_id === "doc-absent")
					return problemResp(
						409,
						"SOURCE_BINARY_ABSENT",
						"Absent",
						"Binary absent",
					);
				if (b.doc_id === "doc-ocr")
					return jsonResp({ matched: true, source_kind: "ocr_receipt" });
				return jsonResp({
					matched: true,
					source_kind: "canonical_text",
					char_start: null,
					char_end: null,
				});
			}
			if (url.includes("/evidence/documents/doc-canon/pages")) {
				return jsonResp([
					{
						doc_id: "doc-canon",
						page_number: 1,
						masked_text:
							"Registered   Institutional Filer once and Registered Institutional Filer twice.",
						is_scanned: false,
						scan_approved: true,
					},
				]);
			}
			if (url.endsWith("/ontology/fibo-qa") && init?.method === "POST") {
				return jsonResp({
					matches: [
						{
							concept_uri:
								"https://spec.edmcouncil.org/fibo/ontology/FND/Agents/Filer",
							label: "Filer",
							definition: "Entity filing reports",
							suggested_path: "PATH_A1",
							normalized_score: "0.95",
							raw_bm25: "4.20",
							parent_uri: null,
						},
					],
				});
			}
			if (url.endsWith("/api/v1/secrets") && init?.method === "POST") {
				return jsonResp({
					key: "OPENAI_API_KEY",
					backend: "keyring",
					present: true,
				});
			}
			return jsonResp({});
		}) as typeof fetch;

		useProjectStore.setState({
			currentUrl: "/projects/p4/step/4?sub=4a&run_id=run4-hold",
			activeProject: {
				project_id: "p4",
				domain_namespace: "asset_management",
				has_active_lease: false,
				source_count: 1,
				table_count: 1,
				created_at: "t",
				updated_at: "t",
				settings_snapshot: PROJ_SNAP,
			},
			documentMaps: [
				{
					doc_id: "doc-absent",
					filename: "absent.pdf",
					format: "pdf",
					page_count: 1,
					pages: [
						{
							page_number: 1,
							char_count: 88,
							has_images: false,
							is_scanned_or_low_density: false,
							ocr_box_count: 0,
							canonical_text_sha256: "dead1234",
							heading_preview: "H",
						},
					],
				},
				{
					doc_id: "doc-ocr",
					filename: "scan.pdf",
					format: "pdf",
					page_count: 2,
					pages: [
						{
							page_number: 2,
							char_count: 10,
							has_images: true,
							is_scanned_or_low_density: true,
							ocr_box_count: 5,
							canonical_text_sha256: "beef5678",
							heading_preview: "S",
						},
					],
				},
			],
		});

		const host = mount(React.createElement(Step4Discovery));
		await settle();

		expect(FORBIDDEN_GUIDED_JARGON.test(host.textContent ?? "")).toBe(false);
		expect(host.textContent).toContain("Last-Event-ID: 1");
		expect(host.textContent).toContain("TOOL_CALL");
		await click(btn(host, "Collapse Trace") as unknown as Element);
		expect(btn(host, "Expand Trace")).not.toBeUndefined();

		expect(host.querySelector("table.responsive-table")).not.toBeNull();
		expect(btn(host, "Submit Answer")?.disabled).toBe(true);
		await click(btn(host, "Next") as unknown as Element);
		expect(host.textContent).toContain("Question 2 of 2");
		await setValue(
			host.querySelector("textarea") as Element,
			"Custodial accounts only",
		);
		const subBtn = btn(host, "Submit Answer") as unknown as HTMLButtonElement;
		expect(subBtn.disabled).toBe(false);
		await click(subBtn as unknown as Element);
		expect(cap.ans).toEqual({
			answers: { q1: "Yes, use CIK", q2: "Custodial accounts only" },
		});

		serverRun = {
			...holdRun,
			status: "HOLD_REQUESTED",
			pending_questions: [],
		};
		await act(async () => {
			useProjectStore.setState({
				activeStep4Run: {
					...holdRun,
					status: "HOLD_REQUESTED",
					pending_questions: [],
				},
			});
			await tick();
		});
		expect(host.textContent).toContain("Draining active work");
		expect(btn(host, "Start Discovery")?.disabled).toBe(true);

		serverRun = {
			...holdRun,
			status: "UNCERTIFIED_DRAFT",
			pending_questions: [],
		};
		await act(async () => {
			useProjectStore.setState({
				activeStep4Run: {
					...holdRun,
					status: "UNCERTIFIED_DRAFT",
					pending_questions: [],
				},
			});
			await tick();
		});
		expect(host.textContent).toContain("UNCERTIFIED_DRAFT");

		await click(btn(host, "Re-run") as unknown as Element);
		expect(host.textContent).toContain("NEEDS_LOCAL_SECRET_BINDING");
		const secForm = host.querySelector(".banner-danger form") as Element;
		await setValue(
			secForm.querySelector('input[name="key"]') as Element,
			"OPENAI_API_KEY",
		);
		await setValue(
			secForm.querySelector('input[name="val"]') as Element,
			"sk-123",
		);
		await submitForm(secForm);
		expect(calls.some((c) => c === "POST /api/v1/secrets")).toBe(true);

		await click(host.querySelector("#step4-tab-4b") as Element);
		expect(host.textContent).toContain("PATH_A1: 1");
		await click(btn(host, "Inspect Citations") as unknown as Element);
		const citeDlg = [...host.querySelectorAll("dialog")].find(
			(d) => d.getAttribute("open") !== null,
		) as Element;
		expect(FORBIDDEN_GUIDED_JARGON.test(citeDlg.textContent ?? "")).toBe(false);
		expect(normalizeAlgA9bText("a\r\n b\t\tc")).toBe("a\n b c");
		expect(citeDlg.querySelector("mark")?.textContent).toBe(
			"Registered Institutional Filer",
		);

		await click(btn(citeDlg, "doc-absent p.1") as unknown as Element);
		expect(citeDlg.textContent).toContain("dead1234");
		await click(btn(citeDlg, "doc-ocr p.2") as unknown as Element);
		expect(citeDlg.textContent).toContain("OCR boxes: 5");
		expect(citeDlg.textContent).toContain("SEC Filer");
		await click(btn(citeDlg, "Close") as unknown as Element);

		await click(btn(host, "Ask FIBO") as unknown as Element);
		const fiboDlg = [...host.querySelectorAll("dialog")].find(
			(d) => d.getAttribute("open") !== null,
		) as Element;
		await setValue(
			fiboDlg.querySelector('[aria-label="FIBO search query"]') as Element,
			"Filer",
		);
		await submitForm(fiboDlg.querySelector("form") as Element);
		expect(fiboDlg.textContent).toContain("Filer");
		expect(FORBIDDEN_GUIDED_JARGON.test(fiboDlg.textContent ?? "")).toBe(false);
		expect(btn(fiboDlg, "Stage Concept")).toBeUndefined();
		await click(btn(fiboDlg, "Copy IRI") as unknown as Element);
		expect(copiedText).toContain("fibo/ontology/FND/Agents/Filer");

		let stagedUri = "";
		const fHost = mount(
			React.createElement(AskFiboDrawer, {
				open: true,
				onClose: () => {},
				projectId: "p4",
				initialQuery: "Filer",
				canStageToDraft: true,
				onStageConcept: (m) => {
					stagedUri = m.concept_uri;
				},
			}),
		);
		await settle();
		await click(btn(fHost, "Stage Concept") as unknown as Element);
		expect(stagedUri).toContain("FND/Agents/Filer");

		expect(typeof AgentIoDrawer).toBe("function");
		expect(typeof CitationInspectorDrawer).toBe("function");
	});
});
