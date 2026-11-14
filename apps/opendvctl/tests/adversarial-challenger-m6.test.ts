/**
 * apps/opendvctl/tests/adversarial-challenger-m6.test.ts
 *
 * Tier 5 Adversarial Coverage Hardening Suite for opendvctl:
 * - Branch coverage & boundary conditions for newly added M1-M6 commands
 * - Missing flags, invalid argument values, whitespace-only inputs
 * - Non-existent sessions and candidate IDs (HTTP 404 -> exit 3)
 * - Server error status mapping (401/403 -> exit 7, 409 -> exit 6, 412 -> exit 5, 422 -> exit 4, 500 -> exit 2)
 * - URL path parameter encoding against injection
 * - RFC 9457 JSON problem details output discipline
 */

import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { runCli } from "../src/main";
import { loadCliSpec } from "../src/spec";

describe("Tier 5 Adversarial Coverage Hardening: opendvctl CLI", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;

	let lastRequestUrl = "";
	let lastRequestMethod = "";
	let lastRequestBody: Record<string, unknown> | null = null;
	let lastRequestHeaders: Headers | null = null;

	const dummyFilePath = path.join(
		process.cwd(),
		`temp_adversarial_note_${Date.now()}.txt`,
	);

	beforeAll(() => {
		writeFileSync(
			dummyFilePath,
			"Adversarial domain notes with Unicode 📊 and quotes \"' &\n",
			"utf-8",
		);

		server = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);
				lastRequestUrl = url.pathname + url.search;
				lastRequestMethod = req.method;
				lastRequestHeaders = req.headers;

				// Healthcheck
				if (url.pathname === "/health" || url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				// Simulated error endpoints
				if (url.pathname.includes("/sessions/err-401/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/UNAUTHENTICATED",
							title: "Unauthenticated",
							status: 401,
							detail: "Missing authentication bearer token",
						}),
						{ status: 401, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-403/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/FORBIDDEN_ADMIN_REQUIRED",
							title: "Forbidden",
							status: 403,
							detail: "Admin role required for this action",
						}),
						{ status: 403, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-404/")) {
					return new Response(
						JSON.stringify({
							type: "urn:opendv:error:session-not-found",
							title: "Session Not Found",
							status: 404,
							detail: "The requested session does not exist",
						}),
						{ status: 404, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-409/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/CONFLICT",
							title: "Conflict",
							status: 409,
							detail: "Session state conflict detected",
						}),
						{ status: 409, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-412/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/ONT_EVO_CONCURRENT_MODIFICATION",
							title: "Precondition Failed",
							status: 412,
							detail: "Optimistic concurrency precondition mismatch",
						}),
						{ status: 412, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-422/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/ONT_EVO_CONTRACT_VIOLATION",
							title: "Unprocessable Entity",
							status: 422,
							detail: "Candidate promotion violates governance invariants",
						}),
						{ status: 422, headers: { "Content-Type": "application/problem+json" } },
					);
				}
				if (url.pathname.includes("/sessions/err-500/")) {
					return new Response(
						JSON.stringify({
							type: "https://opendv.org/errors/INTERNAL_ERROR",
							title: "Internal Server Error",
							status: 500,
							detail: "Internal server fault encountered",
						}),
						{ status: 500, headers: { "Content-Type": "application/problem+json" } },
					);
				}

				// Normal staged candidates list
				if (
					url.pathname.startsWith("/api/v1/ontology/studio/sessions/") &&
					url.pathname.endsWith("/staged-candidates") &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "sess-adv-1",
						staged_candidates: [
							{
								candidate_id: "cand-fibo-999",
								concept_name: "AssetHolding",
								suggested_hub: "hub_asset_holding",
								score: 0.58,
								matched_fibo_uri:
									"https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/AssetHolding",
							},
						],
						total_count: 1,
					});
				}

				// Normal staged promote
				if (
					url.pathname.includes("/staged-candidates/") &&
					url.pathname.endsWith("/promote") &&
					req.method === "POST"
				) {
					lastRequestBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: "sess-adv-1",
						candidate_id: "cand-fibo-999",
						promoted: true,
						status: "PROMOTED",
						path: lastRequestBody.path || "path_a",
						delta: {
							target_ontology_version: "1.0.0",
							operations: [
								{
									op_type: "AddConcept",
									op_id: "op-concept-999",
									concept_name: "AssetHolding",
									concept_uri: "urn:opendv:concept:asset_holding",
									target_hub: "hub_asset_holding",
								},
							],
						},
					});
				}

				// Normal text add
				if (
					url.pathname.startsWith("/api/v1/ontology/studio/sessions/") &&
					url.pathname.endsWith("/sources/text") &&
					req.method === "POST"
				) {
					lastRequestBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						source_id: "src-adv-cas-01",
						source_name: lastRequestBody.source_name || "domain_notes.txt",
						source_sha256:
							"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
						byte_count: String(lastRequestBody.text || "").length,
						created_at: "2026-11-14T12:00:00Z",
					});
				}

				// Normal proposal refine
				if (
					url.pathname.includes("/proposals/") &&
					url.pathname.endsWith("/refine") &&
					req.method === "POST"
				) {
					lastRequestBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: "sess-adv-1",
						proposal_id: "prop-am-001",
						status: "PENDING",
						base_version: "1.0.0",
						target_version: "1.1.0",
						proposer: "StudioAgentRunner",
						rationale: `Refined via prompt: ${lastRequestBody.steering_prompt}`,
						operations: [
							{
								op_type: "AddAttribute",
								concept_uri: "urn:opendv:concept:security",
								attribute_name: "isin",
								data_type: "string",
								is_optional: true,
							},
						],
					});
				}

				return new Response("Not Found", { status: 404 });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop(true);
		try {
			unlinkSync(dummyFilePath);
		} catch {
			// cleanup
		}
	});

	// Helper to run CLI capturing stdout and stderr
	async function execCli(
		args: string[],
	): Promise<{ exitCode: number; stdout: string[]; stderr: string[] }> {
		const stdout: string[] = [];
		const stderr: string[] = [];
		const baseArgs = ["--api-url", serverUrl, ...args];
		const exitCode = await runCli(
			baseArgs,
			(msg) => stdout.push(msg),
			(msg) => stderr.push(msg),
		);
		return { exitCode, stdout, stderr };
	}

	describe("1. ontology studio staged list: Error Boundaries & HTTP Code Mapping", () => {
		it("fails with exit 2 when --session flag is missing", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
			expect(errObj.detail).toContain("--session");
		});

		it("fails with exit 2 when --session is an empty string", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
		});

		it("fails with exit 2 when unknown flag is provided", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"sess-1",
				"--bogus-flag-adv",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
		});

		it("maps HTTP 404 to exit 3 (SESSION_NOT_FOUND)", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-404",
				"--json",
			]);
			expect(exitCode).toBe(3);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SESSION_NOT_FOUND");
		});

		it("maps HTTP 401 and 403 to exit 7 (AUTH / FORBIDDEN)", async () => {
			const res401 = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-401",
				"--json",
			]);
			expect(res401.exitCode).toBe(7);

			const res403 = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-403",
				"--json",
			]);
			expect(res403.exitCode).toBe(7);
		});

		it("maps HTTP 409 to exit 6 (CONFLICT)", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-409",
				"--json",
			]);
			expect(exitCode).toBe(6);
		});

		it("maps HTTP 412 to exit 5 (PRECONDITION / OCC)", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-412",
				"--json",
			]);
			expect(exitCode).toBe(5);
		});

		it("maps HTTP 422 to exit 4 (GOVERNANCE / CONTRACT)", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-422",
				"--json",
			]);
			expect(exitCode).toBe(4);
		});

		it("maps HTTP 500 to exit 2 (INTERNAL_ERROR)", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"err-500",
				"--json",
			]);
			expect(exitCode).toBe(2);
		});

		it("properly URL-encodes special characters in session id", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"list",
				"--session",
				"session/with slashes&symbols",
				"--json",
			]);
			// Expect URL path to contain encoded components
			expect(lastRequestUrl).toContain("session%2Fwith%20slashes%26symbols");
		});
	});

	describe("2. ontology studio staged promote: Boundary & Path A/B Validation", () => {
		it("fails with exit 2 when --candidate-id is missing", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"staged",
				"promote",
				"--session",
				"sess-adv-1",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
			expect(errObj.detail).toContain("--candidate-id");
		});

		it("fails with exit 2 when --candidate-id is empty", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"promote",
				"--session",
				"sess-adv-1",
				"--candidate-id",
				"",
				"--json",
			]);
			expect(exitCode).toBe(2);
		});

		it("executes Path A promotion with trimmed comma-separated synonyms", async () => {
			const { exitCode, stdout } = await execCli([
				"ontology",
				"studio",
				"staged",
				"promote",
				"--session",
				"sess-adv-1",
				"--candidate-id",
				"cand-fibo-999",
				"--synonyms",
				" fund , collective investment , pooled vehicle ",
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody).not.toBeNull();
			expect(lastRequestBody?.path).toBe("path_a");
			expect(lastRequestBody?.synonyms).toEqual([
				"fund",
				"collective investment",
				"pooled vehicle",
			]);
			expect(lastRequestBody?.waiver_rationale).toBeNull();
		});

		it("executes Path B promotion with --waiver and rationale", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"staged",
				"promote",
				"--session",
				"sess-adv-1",
				"--candidate-id",
				"cand-fibo-999",
				"--waiver",
				"--rationale",
				"Approved administrative waiver for non-grounded concept promotion",
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody?.path).toBe("path_b");
			expect(lastRequestBody?.synonyms).toBeNull();
			expect(lastRequestBody?.waiver_rationale).toBe(
				"Approved administrative waiver for non-grounded concept promotion",
			);
		});

		it("encodes candidate ID containing URI slashes or colons", async () => {
			await execCli([
				"ontology",
				"studio",
				"staged",
				"promote",
				"--session",
				"sess-adv-1",
				"--candidate-id",
				"urn:fibo:cand/001#item",
				"--json",
			]);
			expect(lastRequestUrl).toContain("urn%3Afibo%3Acand%2F001%23item");
		});
	});

	describe("3. ontology studio text add: Ingestion Boundaries", () => {
		it("fails with exit 2 when neither --text nor --file is provided", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"text",
				"add",
				"--session",
				"sess-adv-1",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
			expect(errObj.detail).toContain("Either --text or --file must be provided");
		});

		it("fails with exit 2 when --text is empty string and --file is absent", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"text",
				"add",
				"--session",
				"sess-adv-1",
				"--text",
				"",
				"--json",
			]);
			expect(exitCode).toBe(2);
		});

		it("fails with exit 3 (FILE_NOT_FOUND) when file does not exist", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"text",
				"add",
				"--session",
				"sess-adv-1",
				"--file",
				"non_existent_challenger_file_987654.txt",
				"--json",
			]);
			expect(exitCode).toBe(3);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("FILE_NOT_FOUND");
		});

		it("preserves multi-line unicode, quotes, and symbols byte-for-byte from file", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"text",
				"add",
				"--session",
				"sess-adv-1",
				"--file",
				dummyFilePath,
				"--source-name",
				"custom_adversarial.txt",
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody?.source_name).toBe("custom_adversarial.txt");
			expect(lastRequestBody?.text).toContain("Unicode 📊 and quotes \"' &");
		});

		it("prefers inline --text over --file if both are supplied", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"text",
				"add",
				"--session",
				"sess-adv-1",
				"--text",
				"Explicit inline text override",
				"--file",
				dummyFilePath,
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody?.text).toBe("Explicit inline text override");
		});
	});

	describe("4. ontology studio proposal refine: Steering Prompts & Mode Flags", () => {
		it("fails with exit 2 when --proposal-id is missing", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"proposal",
				"refine",
				"--session",
				"sess-adv-1",
				"--steering-prompt",
				"Add ISIN and CUSIP to Security",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
			expect(errObj.detail).toContain("--proposal-id");
		});

		it("fails with exit 2 when --steering-prompt is missing", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"proposal",
				"refine",
				"--session",
				"sess-adv-1",
				"--proposal-id",
				"prop-am-001",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
			expect(errObj.detail).toContain("--steering-prompt");
		});

		it("fails with exit 2 when invalid --mode choice is passed", async () => {
			const { exitCode, stderr } = await execCli([
				"ontology",
				"studio",
				"proposal",
				"refine",
				"--session",
				"sess-adv-1",
				"--proposal-id",
				"prop-am-001",
				"--steering-prompt",
				"Add ISIN",
				"--mode",
				"non_existent_mode_choice",
				"--json",
			]);
			expect(exitCode).toBe(2);
			const errObj = JSON.parse(stderr.join("\n"));
			expect(errObj.detail).toContain(
				"Allowed choices: llm, deterministic, agentic",
			);
		});

		it("executes proposal refine with deterministic mode and steering prompt", async () => {
			const { exitCode, stdout } = await execCli([
				"ontology",
				"studio",
				"proposal",
				"refine",
				"--session",
				"sess-adv-1",
				"--proposal-id",
				"prop-am-001",
				"--steering-prompt",
				"Refine Security to include LEI and CFI codes",
				"--mode",
				"deterministic",
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody?.steering_prompt).toBe(
				"Refine Security to include LEI and CFI codes",
			);
			expect(lastRequestBody?.mode).toBe("deterministic");
			expect(lastRequestBody?.stream).toBe(false);
		});

		it("executes proposal refine with agentic mode and --stream", async () => {
			const { exitCode } = await execCli([
				"ontology",
				"studio",
				"proposal",
				"refine",
				"--session",
				"sess-adv-1",
				"--proposal-id",
				"prop-am-001",
				"--steering-prompt",
				"Refine Security via @ontology-architect",
				"--mode",
				"agentic",
				"--stream",
				"--json",
			]);
			expect(exitCode).toBe(0);
			expect(lastRequestBody?.mode).toBe("agentic");
			expect(lastRequestBody?.stream).toBe(true);
		});
	});
});
