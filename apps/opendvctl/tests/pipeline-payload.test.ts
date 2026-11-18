import { describe, expect, it } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { buildSingleRunPayload } from "../src/remote/pipeline-payload";
import { handlePipeline, handleRun, handleSandbox } from "../src/remote/pipeline";
import { handleSource } from "../src/remote/source";
import type { RemoteHttpClient } from "../src/remote/http-client";

describe("buildSingleRunPayload (R3-C2)", () => {
	const mockClient = {
		baseUrl: "http://127.0.0.1:8000",
		getHeaders: () => ({}),
		postJson: async () => ({}),
		fetchImpl: async () =>
			new Response(JSON.stringify({ ok: true, sha256: "abcdef0123456789" }), {
				status: 201,
			}),
	} as unknown as RemoteHttpClient;

	it("omits all client *_path fields and undeclared keys, mapping hold policy into governance_policy", async () => {
		const options = {
			blueprint: "path/to/vault_blueprint.yaml",
			blueprint_content: "schema_version: '1.0.0'\ndomain_namespace: test\n",
			ontology: "path/to/ontology.yaml",
			ontology_content: "domain_namespace: test\nconcepts: {}\n",
			contract: "path/to/contract.yaml",
			contract_content: "contract_version: '1.0.0'\n",
			harness_config: "path/to/harness_config.yaml",
			harness_config_path: "path/to/harness_config.yaml",
			provider: "claude",
			hold_policy: "pause",
			unstructured_only: true,
			project_id: "test-project-123",
			dialect: "duckdb",
		};

		const payload = await buildSingleRunPayload(mockClient, options);

		// Assert all client *_path fields are omitted
		expect(payload.blueprint_path).toBeUndefined();
		expect(payload.initial_contract_path).toBeUndefined();
		expect(payload.harness_config_path).toBeUndefined();
		if (payload.ontology_source && typeof payload.ontology_source === "object") {
			expect((payload.ontology_source as Record<string, unknown>).path).toBeUndefined();
		}

		// Assert undeclared keys are omitted
		expect(payload.unstructured_only).toBeUndefined();
		expect(payload.project_id).toBeUndefined();
		expect(payload.steward_hold_policy).toBeUndefined();

		// Assert steward_hold_policy is mapped inside governance_policy
		expect(payload.governance_policy).toBeDefined();
		expect((payload.governance_policy as Record<string, unknown>).steward_hold_policy).toBe("pause");

		// Assert provider is mapped to harness_provider
		expect(payload.harness_provider).toBe("claude");
		expect(payload.target_dialect).toBe("duckdb");
	});

	it("R4-M5: infers source_format strictly from file extension ignoring global --format=toon", async () => {
		const tempDir = mkdtempSync(path.join(tmpdir(), "opendv-test-"));
		const files = [
			path.join(tempDir, "trades.csv"),
			path.join(tempDir, "ledger.tsv"),
			path.join(tempDir, "positions.parquet"),
			path.join(tempDir, "events.jsonl"),
			path.join(tempDir, "config.json"),
			path.join(tempDir, "store.sqlite"),
			path.join(tempDir, "cache.duckdb"),
		];
		for (const f of files) {
			writeFileSync(f, "test");
		}

		try {
			const options = {
				sources: files,
				format: "toon", // CLI global output format flag should be ignored for source_format
				dialect: "duckdb",
			};

			const payload = await buildSingleRunPayload(mockClient, options);
			const sources = payload.sources as Array<{
				feed_id: string;
				uri: string;
				adapter?: string;
				options?: { format?: string; source_format?: string };
			}>;

			expect(sources).toHaveLength(7);
			expect(sources[0].options?.source_format).toBe("csv");
			expect(sources[0].options?.format).toBe("csv");
			expect(sources[0].adapter).toBe("filesystem");

			expect(sources[1].options?.source_format).toBe("tsv");
			expect(sources[1].options?.format).toBe("tsv");
			expect(sources[1].adapter).toBe("filesystem");

			expect(sources[2].options?.source_format).toBe("parquet");
			expect(sources[2].options?.format).toBe("parquet");
			expect(sources[2].adapter).toBe("filesystem");

			expect(sources[3].options?.source_format).toBe("jsonl");
			expect(sources[3].options?.format).toBe("jsonl");
			expect(sources[3].adapter).toBe("filesystem");

			expect(sources[4].options?.source_format).toBe("json");
			expect(sources[4].options?.format).toBe("json");
			expect(sources[4].adapter).toBe("filesystem");

			expect(sources[5].options?.source_format).toBe("sqlite");
			expect(sources[5].options?.format).toBe("sqlite");
			expect(sources[5].adapter).toBe("sqlite");

			expect(sources[6].options?.source_format).toBe("duckdb");
			expect(sources[6].options?.format).toBe("duckdb");
			expect(sources[6].adapter).toBe("duckdb");
		} finally {
			rmSync(tempDir, { recursive: true, force: true });
		}
	});

	it("R4-M5: handleSource sets source_format from extension ignoring global format", async () => {
		const tempDir = mkdtempSync(path.join(tmpdir(), "opendv-src-test-"));
		const tempFile = path.join(tempDir, "transactions.parquet");
		writeFileSync(tempFile, "test");

		try {
			let capturedEndpoint = "";
			let capturedBody: any;
			const mockClientForSource = {
				baseUrl: "http://127.0.0.1:8000",
				getHeaders: () => ({ "X-OpenDV-Role": "INTERNAL_MODELER" }),
				fetchImpl: async () =>
					new Response(JSON.stringify({ ok: true, sha256: "abcdef0123456789" }), {
						status: 201,
					}),
				postJson: async (ep: string, body: any) => {
					capturedEndpoint = ep;
					capturedBody = body;
					return { ok: true };
				},
			} as unknown as RemoteHttpClient;

			await handleSource(mockClientForSource, "inspect", {
				uri: tempFile,
				format: "toon",
			});

			expect(capturedEndpoint).toBe("/api/v1/sources/inspect");
			expect(capturedBody.options.source_format).toBe("parquet");
			expect(capturedBody.options.format).toBe("parquet");
			expect(capturedBody.adapter).toBe("filesystem");
		} finally {
			rmSync(tempDir, { recursive: true, force: true });
		}
	});

	it("R4-M7: handleSandbox selects CUSTOM_SQL on non-empty --sql even when query_type is defaulted", async () => {
		let capturedBody: any;
		const mockClientForSandbox = {
			baseUrl: "http://127.0.0.1:8000",
			getHeaders: () => ({}),
			postJson: async (_ep: string, body: any) => {
				capturedBody = body;
				return { executed: true };
			},
		} as unknown as RemoteHttpClient;

		await handleSandbox(mockClientForSandbox, {
			run_id: "run-abc",
			sql: "SELECT * FROM hub_account LIMIT 10",
			query_type: "ADVERSARIAL_BATCHES_1_TO_4", // parser default
		});

		expect(capturedBody.query_type).toBe("CUSTOM_SQL");
		expect(capturedBody.sql).toBe("SELECT * FROM hub_account LIMIT 10");
	});

	it("R4-M7: handleRun run create maps contract_content -> initial_contract_content, harness -> harness_provider, strips *_path keys, and uses postJson", async () => {
		let capturedEndpoint = "";
		let capturedBody: any;
		const mockClientForPipeline = {
			baseUrl: "http://127.0.0.1:8000",
			getHeaders: () => ({ "X-OpenDV-Role": "INTERNAL_MODELER", "X-OpenDV-Workbench": "token" }),
			postJson: async (ep: string, body: any) => {
				capturedEndpoint = ep;
				capturedBody = body;
				return { run_id: "run-created-123" };
			},
		} as unknown as RemoteHttpClient;

		const result = await handleRun(mockClientForPipeline, "create", {
			project: "project-omega",
			harness: "agy-cli",
			contract_content: "hubs:\n  - table_name: hub_asset\n",
			contract_path: "/local/path/to/contract.yaml",
			initial_contract_path: "/local/path/to/initial.yaml",
			pipeline_input_path: "/local/path/to/input.json",
			blueprint_path: "/local/path/to/blueprint.yaml",
			max_repair_retries: 3,
			run_sandbox_batches: true,
		});

		expect(capturedEndpoint).toBe("/api/v1/runs");
		expect(result).toEqual({ run_id: "run-created-123" });
		expect(capturedBody.project_id).toBe("project-omega");
		expect(capturedBody.harness_provider).toBe("agy-cli");
		expect(capturedBody.initial_contract_content).toBe("hubs:\n  - table_name: hub_asset\n");
		// Assert all client *_path keys are stripped
		expect(capturedBody.contract_path).toBeUndefined();
		expect(capturedBody.initial_contract_path).toBeUndefined();
		expect(capturedBody.pipeline_input_path).toBeUndefined();
		expect(capturedBody.blueprint_path).toBeUndefined();
	});
});
