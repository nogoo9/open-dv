import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { runCli } from "../src/main";
import { loadCliSpec } from "../src/spec";

describe("opendvctl Phase 2 Track C wizard & dv-schema commands (D13)", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;
	let tempDir: string;
	let localCsvPath: string;
	let localCsvSha256: string;

	const recordedRequests: Array<{
		method: string;
		pathname: string;
		search: string;
		body: unknown;
		headers: Record<string, string>;
	}> = [];
	let casUploadBytes: Uint8Array | null = null;
	let casUploadShaHeader: string | null = null;
	let forceFiboQa422 = false;
	let forceBusy409 = false;
	let forceSql400 = false;

	beforeAll(() => {
		tempDir = mkdtempSync(path.join(tmpdir(), "opendvctl-wizard-test-"));
		localCsvPath = path.join(tempDir, "holdings.csv");
		const csvBytes = Buffer.from("account_id,isin,mv\nA1,US0378331005,12500\n");
		writeFileSync(localCsvPath, csvBytes);
		localCsvSha256 = createHash("sha256")
			.update(csvBytes)
			.digest("hex")
			.toLowerCase();

		server = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);
				const hdrs: Record<string, string> = {};
				req.headers.forEach((v, k) => {
					hdrs[k.toLowerCase()] = v;
				});

				if (url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				if (url.pathname === "/api/v1/cas/blobs" && req.method === "POST") {
					casUploadBytes = new Uint8Array(await req.arrayBuffer());
					casUploadShaHeader = hdrs["x-cas-sha256"] ?? null;
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body: { byteLength: casUploadBytes.byteLength },
						headers: hdrs,
					});
					return Response.json(
						{
							sha256: casUploadShaHeader,
							size_bytes: casUploadBytes.byteLength,
							uri: `cas://${casUploadShaHeader}`,
						},
						{ status: 201 },
					);
				}

				if (
					url.pathname === "/api/v1/runs/run-101/dv-schema" &&
					req.method === "GET"
				) {
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body: null,
						headers: hdrs,
					});
					return Response.json({
						run_id: "run-101",
						compare_run_id: url.searchParams.get("compare"),
						nodes: [{ id: "hub_account", kind: "HUB", zone: "Z2" }],
						edges: [],
					});
				}

				if (
					url.pathname ===
						"/api/v1/runs/run-101/dv-schema/tables/hub_account/rows" &&
					req.method === "GET"
				) {
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body: null,
						headers: hdrs,
					});
					return Response.json({
						run_id: "run-101",
						table: "hub_account",
						rows: [{ hk_account: "0".repeat(64), account_bk: "-1" }],
						total_rows: 1,
					});
				}

				if (
					url.pathname === "/api/v1/runs/run-101/dv-schema/query" &&
					req.method === "POST"
				) {
					const body = await req.json();
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body,
						headers: hdrs,
					});
					if (forceSql400) {
						return Response.json(
							{
								status: 400,
								error_code: "INVALID_REQUEST",
								detail: "Mutating SQL rejected by validate_safe_sql()",
							},
							{ status: 400 },
						);
					}
					return Response.json({
						run_id: "run-101",
						columns: ["account_bk"],
						rows: [{ account_bk: "ACC-100" }],
					});
				}

				if (
					url.pathname ===
						"/api/v1/ontology/studio/sessions/sess-1/sources/connectors" &&
					req.method === "POST"
				) {
					const body = await req.json();
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body,
						headers: hdrs,
					});
					if (forceBusy409) {
						return Response.json(
							{
								status: 409,
								error_code: "SESSION_JOB_ACTIVE",
								detail: "Another job is active on session sess-1",
							},
							{ status: 409 },
						);
					}
					return Response.json(
						{
							session_id: "sess-1",
							source_id: "src-conn-1",
							adapter: body.adapter ?? "filesystem",
							cas_hash: body.cas_hash ?? null,
						},
						{ status: 201 },
					);
				}

				if (
					url.pathname ===
						"/api/v1/ontology/studio/sessions/sess-1/fibo-qa" &&
					req.method === "POST"
				) {
					const body = await req.json();
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body,
						headers: hdrs,
					});
					if (forceFiboQa422) {
						return Response.json(
							{
								status: 422,
								error_code: "AGENT_NOT_CONFIGURED",
								detail: "Real agent harness is not configured",
							},
							{ status: 422 },
						);
					}
					if (forceBusy409) {
						return Response.json(
							{
								status: 409,
								error_code: "SESSION_JOB_ACTIVE",
								detail: "Session job active",
							},
							{ status: 409 },
						);
					}
					return Response.json({
						session_id: "sess-1",
						answer: "Positions link to FinancialInstrument via ISIN.",
						citations: [{ source_label: "holdings.csv", chunk_id: "c1" }],
						suggested_actions: [],
					});
				}

				if (
					url.pathname === "/api/v1/ontology/studio/sessions/sess-1/logs" &&
					req.method === "GET"
				) {
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body: null,
						headers: hdrs,
					});
					return Response.json({
						session_id: "sess-1",
						entries: [
							{
								seq: 1,
								stage_id: "STAGE_03_SHIP_VAULT_PASS1",
								persona: "@ship-vault",
								status: "COMPLETED",
							},
						],
					});
				}

				if (
					url.pathname ===
						"/api/v1/ontology/studio/sessions/sess-1/logs/stream" &&
					req.method === "GET"
				) {
					recordedRequests.push({
						method: req.method,
						pathname: url.pathname,
						search: url.search,
						body: null,
						headers: hdrs,
					});
					const encoder = new TextEncoder();
					const body = new ReadableStream<Uint8Array>({
						start(controller) {
							controller.enqueue(
								encoder.encode(
									'id: 1\nevent: log_entry\ndata: {"seq":1,"stage_id":"STAGE_03_SHIP_VAULT_PASS1","persona":"@ship-vault","status":"RUNNING"}\n\n',
								),
							);
							controller.enqueue(
								encoder.encode(
									'id: 2\nevent: log_entry\ndata: {"seq":2,"stage_id":"STAGE_07_DETERMINISTIC_COMPILER","persona":"@deterministic-compiler","status":"COMPLETED"}\n\n',
								),
							);
							controller.enqueue(
								encoder.encode(
									'id: 3\nevent: stream_end\ndata: {"status":"COMPLETED","session_id":"sess-1"}\n\n',
								),
							);
							controller.close();
						},
					});
					return new Response(body, {
						status: 200,
						headers: { "Content-Type": "text/event-stream" },
					});
				}

				return Response.json(
					{ detail: `Unexpected route ${req.method} ${url.pathname}` },
					{ status: 404 },
				);
			},
		});
		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server?.stop();
		if (tempDir) {
			rmSync(tempDir, { recursive: true, force: true });
		}
	});

	it("dispatches `run dv-schema` for topology, table rows, and safe SQL query", async () => {
		const out1: string[] = [];
		const err1: string[] = [];
		const rc1 = await runCli(
			[
				"run",
				"dv-schema",
				"--run-id",
				"run-101",
				"--compare",
				"run-100",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => out1.push(m),
			(m) => err1.push(m),
		);
		expect(rc1).toBe(0);
		expect(err1).toEqual([]);
		const parsed1 = JSON.parse(out1.join(""));
		expect(parsed1.run_id).toBe("run-101");
		expect(parsed1.compare_run_id).toBe("run-100");

		const out2: string[] = [];
		const rc2 = await runCli(
			[
				"run",
				"dv-schema",
				"--run-id",
				"run-101",
				"--table",
				"hub_account",
				"--limit",
				"25",
				"--offset",
				"5",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => out2.push(m),
			() => {},
		);
		expect(rc2).toBe(0);
		const lastRowReq = recordedRequests.at(-1);
		expect(lastRowReq?.pathname).toBe(
			"/api/v1/runs/run-101/dv-schema/tables/hub_account/rows",
		);
		expect(lastRowReq?.search).toContain("limit=25");
		expect(lastRowReq?.search).toContain("offset=5");

		const out3: string[] = [];
		const rc3 = await runCli(
			[
				"run",
				"dv-schema",
				"--run-id",
				"run-101",
				"--sql",
				"SELECT account_bk FROM raw_vault.hub_account",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => out3.push(m),
			() => {},
		);
		expect(rc3).toBe(0);
		expect(JSON.parse(out3.join("")).rows[0].account_bk).toBe("ACC-100");

		forceSql400 = true;
		const rcBad = await runCli(
			[
				"run",
				"dv-schema",
				"--run-id",
				"run-101",
				"--sql",
				"DROP TABLE raw_vault.hub_account",
				"--api-url",
				serverUrl,
				"--json",
			],
			() => {},
			() => {},
		);
		forceSql400 = false;
		expect(rcBad).not.toBe(0);
	});

	it("dispatches `ontology studio attach-source` with CAS staging for local files", async () => {
		casUploadBytes = null;
		casUploadShaHeader = null;
		const out: string[] = [];
		const err: string[] = [];
		const rc = await runCli(
			[
				"ontology",
				"studio",
				"attach-source",
				"--session",
				"sess-1",
				"--adapter",
				"filesystem",
				"--uri",
				localCsvPath,
				"--table",
				"holdings",
				"--namespace",
				"asset_management",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => out.push(m),
			(m) => err.push(m),
		);
		expect(rc).toBe(0);
		expect(err).toEqual([]);
		expect(casUploadShaHeader).toBe(localCsvSha256);
		expect(casUploadBytes).not.toBeNull();
		const connReq = recordedRequests.at(-1);
		expect(connReq?.pathname).toBe(
			"/api/v1/ontology/studio/sessions/sess-1/sources/connectors",
		);
		expect((connReq?.body as Record<string, unknown>)?.cas_hash).toBe(
			localCsvSha256,
		);
	});

	it("dispatches `ontology studio fibo-qa` and maps 422/409 errors", async () => {
		const out: string[] = [];
		const rc = await runCli(
			[
				"ontology",
				"studio",
				"fibo-qa",
				"--session",
				"sess-1",
				"--question",
				"How is ISIN modeled?",
				"--source-id",
				"src-conn-1",
				"--concept-uri",
				"urn:ontology:entity:instrument",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => out.push(m),
			() => {},
		);
		expect(rc).toBe(0);
		expect(JSON.parse(out.join("")).answer).toContain("FinancialInstrument");

		forceFiboQa422 = true;
		const errOut: string[] = [];
		const rc422 = await runCli(
			[
				"ontology",
				"studio",
				"fibo-qa",
				"--session",
				"sess-1",
				"--question",
				"How is ISIN modeled?",
				"--api-url",
				serverUrl,
				"--json",
			],
			() => {},
			(m) => errOut.push(m),
		);
		forceFiboQa422 = false;
		expect(rc422).not.toBe(0);
		expect(errOut.join("")).toContain("AGENT_NOT_CONFIGURED");

		forceBusy409 = true;
		const err409: string[] = [];
		const rc409 = await runCli(
			[
				"ontology",
				"studio",
				"fibo-qa",
				"--session",
				"sess-1",
				"--question",
				"Busy test",
				"--api-url",
				serverUrl,
				"--json",
			],
			() => {},
			(m) => err409.push(m),
		);
		forceBusy409 = false;
		expect(rc409).not.toBe(0);
		expect(err409.join("")).toContain("SESSION_JOB_ACTIVE");
	});

	it("dispatches `ontology studio logs` for query and `--follow` SSE streaming", async () => {
		const queryOut: string[] = [];
		const rcQuery = await runCli(
			[
				"ontology",
				"studio",
				"logs",
				"--session-id",
				"sess-1",
				"--stage",
				"03",
				"--step",
				"6",
				"--category",
				"agent",
				"--persona",
				"@ship-vault",
				"--api-url",
				serverUrl,
				"--json",
			],
			(m) => queryOut.push(m),
			() => {},
		);
		expect(rcQuery).toBe(0);
		expect(JSON.parse(queryOut.join("")).entries[0].stage_id).toBe(
			"STAGE_03_SHIP_VAULT_PASS1",
		);

		const streamOut: string[] = [];
		const streamErr: string[] = [];
		const rcFollow = await runCli(
			[
				"ontology",
				"studio",
				"logs",
				"--session",
				"sess-1",
				"--stage",
				"STAGE_03_SHIP_VAULT_PASS1",
				"--follow",
				"--api-url",
				serverUrl,
			],
			(m) => streamOut.push(m),
			(m) => streamErr.push(m),
		);
		expect(rcFollow).toBe(0);
		expect(streamErr).toEqual([]);
		const combined = streamOut.join("\n");
		expect(combined).toContain("STAGE_03_SHIP_VAULT_PASS1");
		expect(combined).toContain("STAGE_07_DETERMINISTIC_COMPILER");
	});
});
