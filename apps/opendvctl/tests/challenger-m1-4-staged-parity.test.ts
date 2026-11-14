/**
 * apps/opendvctl/tests/challenger-m1-4-staged-parity.test.ts
 *
 * Empirical verification of opendvctl studio subcommands:
 * - staged list
 * - staged promote (Path A and Path B)
 * - text add (inline text and --file)
 * - starter-pack apply
 *
 * Authored by challenger_m1_4 for Milestone M1 Iteration 2 challenge.
 */

import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { existsSync, unlinkSync, writeFileSync } from "node:fs";
import { runCli } from "../src/main";
import { RemoteExecutor } from "../src/remote-executor";
import { loadCliSpec } from "../src/spec";

describe("Challenger M1.4 Empirical: opendvctl Staged, Text, and Starter-Pack Parity", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;

	const dummyTextFilePath = `temp_challenger_text_${Date.now()}.txt`;
	let lastPromoteUrl = "";
	let lastPromoteBody: Record<string, unknown> | null = null;
	let lastTextAddBody: Record<string, unknown> | null = null;
	let lastStarterPackBody: Record<string, unknown> | null = null;

	beforeAll(() => {
		writeFileSync(dummyTextFilePath, "Empirical challenger text notes for domain modeling.\n");

		server = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);

				// Healthcheck
				if (url.pathname === "/health" || url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				// Staged candidates list: GET /api/v1/ontology/studio/sessions/{sid}/staged-candidates
				if (
					url.pathname.startsWith("/api/v1/ontology/studio/sessions/") &&
					url.pathname.endsWith("/staged-candidates") &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "sess-test-4",
						staged_candidates: [
							{
								candidate_id: "cand-fibo-001",
								concept_name: "InvestmentFund",
								suggested_hub: "hub_investment_fund",
								score: 0.612,
								matched_fibo_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/InvestmentFund",
							},
						],
						total_count: 1,
					});
				}

				// Staged promote: POST /api/v1/ontology/studio/sessions/{sid}/staged-candidates/{candId}/promote
				if (
					url.pathname.includes("/staged-candidates/") &&
					url.pathname.endsWith("/promote") &&
					req.method === "POST"
				) {
					lastPromoteUrl = url.pathname;
					lastPromoteBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: "sess-test-4",
						candidate_id: "cand-fibo-001",
						promoted: true,
						status: "PROMOTED",
						path: lastPromoteBody.path || "path_a",
						delta: {
							target_ontology_version: "1.0.0",
							operations: [
								{
									op_type: "AddConcept",
									op_id: "op-concept-investmentfund-123456",
									concept_name: "InvestmentFund",
									concept_uri: "urn:opendv:concept:investment_fund",
									target_hub: "hub_investment_fund",
								},
							],
						},
					});
				}

				// Text add: POST /api/v1/ontology/studio/sessions/{sid}/sources/text
				if (
					url.pathname.startsWith("/api/v1/ontology/studio/sessions/") &&
					url.pathname.endsWith("/sources/text") &&
					req.method === "POST"
				) {
					lastTextAddBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						source_id: "src-cas-001",
						source_name: lastTextAddBody.source_name || "domain_notes.txt",
						source_sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
						byte_count: String(lastTextAddBody.text || "").length,
						created_at: "2026-11-14T09:00:00Z",
					});
				}

				// Starter pack apply: POST /api/v1/ontology/studio/sessions/{sid}/starter-packs/apply
				if (
					url.pathname.startsWith("/api/v1/ontology/studio/sessions/") &&
					url.pathname.endsWith("/starter-packs/apply") &&
					req.method === "POST"
				) {
					lastStarterPackBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: "sess-test-4",
						applied: true,
						pack_ids: lastStarterPackBody.pack_ids || ["core_spine"],
						head_version: "1.0.0",
						concept_count: 6,
						relationship_count: 6,
					});
				}

				return new Response("Not Found", { status: 404 });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop();
		if (existsSync(dummyTextFilePath)) {
			unlinkSync(dummyTextFilePath);
		}
	});

	it("executes 'ontology studio staged list' and parses candidate list", async () => {
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"staged",
			"list",
			"-s",
			"sess-test-4",
		]);
		expect(exitCode).toBe(0);
	});

	it("fails 'ontology studio staged list' when --session flag is missing", async () => {
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"staged",
			"list",
		]);
		expect(exitCode).toBe(2);
	});

	it("executes 'ontology studio staged promote' Path A with synonyms", async () => {
		lastPromoteUrl = "";
		lastPromoteBody = null;
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"staged",
			"promote",
			"-s",
			"sess-test-4",
			"--candidate-id",
			"cand-fibo-001",
			"--synonyms",
			"Mutual Fund, Hedge Fund",
		]);
		expect(exitCode).toBe(0);
		expect(lastPromoteUrl).toContain("/staged-candidates/cand-fibo-001/promote");
		expect(lastPromoteBody).not.toBeNull();
		expect(lastPromoteBody?.path).toBe("path_a");
		expect(lastPromoteBody?.synonyms).toEqual(["Mutual Fund", "Hedge Fund"]);
		expect(lastPromoteBody?.waiver_rationale).toBeNull();
		// Assert candidate_id is NOT in the JSON request body
		expect(lastPromoteBody?.candidate_id).toBeUndefined();
	});

	it("executes 'ontology studio staged promote' Path B with admin waiver and rationale", async () => {
		lastPromoteUrl = "";
		lastPromoteBody = null;
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"staged",
			"promote",
			"-s",
			"sess-test-4",
			"--candidate-id",
			"cand-fibo-001",
			"--waiver",
			"--rationale",
			"Architectural justification for Asset Management candidate exceeding ten characters",
		]);
		expect(exitCode).toBe(0);
		expect(lastPromoteUrl).toContain("/staged-candidates/cand-fibo-001/promote");
		expect(lastPromoteBody).not.toBeNull();
		expect(lastPromoteBody?.path).toBe("path_b");
		expect(lastPromoteBody?.synonyms).toBeNull();
		expect(lastPromoteBody?.waiver_rationale).toBe(
			"Architectural justification for Asset Management candidate exceeding ten characters",
		);
		// Assert candidate_id is NOT in the JSON request body
		expect(lastPromoteBody?.candidate_id).toBeUndefined();
	});

	it("fails 'ontology studio staged promote' when --candidate-id is missing", async () => {
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"staged",
			"promote",
			"-s",
			"sess-test-4",
		]);
		expect(exitCode).toBe(2);
	});

	it("executes 'ontology studio text add' with inline text", async () => {
		lastTextAddBody = null;
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"text",
			"add",
			"-s",
			"sess-test-4",
			"-t",
			"Regulatory disclosure text for private fund investments",
		]);
		expect(exitCode).toBe(0);
		expect(lastTextAddBody).not.toBeNull();
		expect(lastTextAddBody?.text).toBe(
			"Regulatory disclosure text for private fund investments",
		);
	});

	it("executes 'ontology studio text add' reading from file", async () => {
		lastTextAddBody = null;
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"text",
			"add",
			"-s",
			"sess-test-4",
			"--file",
			dummyTextFilePath,
		]);
		expect(exitCode).toBe(0);
		expect(lastTextAddBody).not.toBeNull();
		expect(lastTextAddBody?.text).toContain("Empirical challenger text notes");
	});

	it("fails 'ontology studio text add' when file does not exist with exit 3", async () => {
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"text",
			"add",
			"-s",
			"sess-test-4",
			"--file",
			"non_existent_file_challenger.txt",
		]);
		expect(exitCode).toBe(3);
	});

	it("executes 'ontology studio starter-pack apply'", async () => {
		lastStarterPackBody = null;
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"starter-pack",
			"apply",
			"-s",
			"sess-test-4",
			"--pack",
			"core_spine",
		]);
		expect(exitCode).toBe(0);
		expect(lastStarterPackBody).not.toBeNull();
		// In opendvctl remote-executor.ts line 2786, payload sends pack_id and pack_ids
		expect(lastStarterPackBody?.pack_id).toBe("core_spine");
		expect(lastStarterPackBody?.pack_ids).toEqual(["core_spine"]);
	});

	it("fails 'ontology studio starter-pack apply' when --pack is missing with exit 2", async () => {
		const exitCode = await runCli([
			"--api-url",
			serverUrl,
			"--json",
			"ontology",
			"studio",
			"starter-pack",
			"apply",
			"-s",
			"sess-test-4",
		]);
		expect(exitCode).toBe(2);
	});
});
