import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { existsSync, unlinkSync, writeFileSync } from "node:fs";
import { runCli } from "../src/main";
import { RemoteExecutor } from "../src/remote-executor";
import { loadCliSpec } from "../src/spec";

describe("opendvctl ontology studio subcommands (Issue #220)", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;

	const dummyKeyPath = `temp_waiver_key_${Date.now()}.key`;
	const dummySourcePath = `temp_source_${Date.now()}.txt`;
	const dummyAnswersPath = `temp_answers_${Date.now()}.json`;
	const dummyDeltaPath = `temp_delta_${Date.now()}.json`;
	const dummyBundlePath = `temp_bundle_${Date.now()}.zip`;

	let savedEnv: Record<string, string | undefined> = {};

	beforeAll(() => {
		savedEnv = {
			OPENDV_WAIVER_KEY: process.env.OPENDV_WAIVER_KEY,
			OPENDV_ROLE: process.env.OPENDV_ROLE,
			OPENDV_STUDIO_TOKEN: process.env.OPENDV_STUDIO_TOKEN,
			OPENDV_TOKEN: process.env.OPENDV_TOKEN,
			OPENDV_STUDIO_DIR: process.env.OPENDV_STUDIO_DIR,
		};
		delete process.env.OPENDV_WAIVER_KEY;
		delete process.env.OPENDV_ROLE;
		delete process.env.OPENDV_STUDIO_TOKEN;
		delete process.env.OPENDV_TOKEN;
		delete process.env.OPENDV_STUDIO_DIR;

		writeFileSync(dummyKeyPath, "steward-secret-key-12345\n");
		writeFileSync(dummySourcePath, "Sample regulatory financial standard text.\n");
		writeFileSync(dummyAnswersPath, JSON.stringify([{ question_id: "q1", answer: "A loan facility" }]));
		writeFileSync(
			dummyDeltaPath,
			JSON.stringify({
				delta_id: "delta-1",
				operations: [
					{
						op_id: "op-1",
						op_type: "add_concept",
						concept_uri: "urn:ontology:entity:loan_facility",
						concept_name: "LoanFacility",
					},
				],
			}),
		);
		writeFileSync(dummyBundlePath, "PK\x03\x04mock-zip-content");

		server = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);

				// Health contract version check
				if (url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				// Sessions: create & list
				if (url.pathname === "/api/v1/ontology/studio/sessions" && req.method === "POST") {
					const body = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: body.session_id || "s-test-01",
						domain_namespace: body.domain_namespace || "logistics",
						enterprise_owner: "test_owner",
						status: "active",
						active_version: "0.0.0",
						head_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
						created_at: "2026-11-10T00:00:00Z",
						updated_at: "2026-11-10T00:00:00Z",
					});
				}

				if (url.pathname === "/api/v1/ontology/studio/sessions" && req.method === "GET") {
					return Response.json([
						{
							session_id: "s-test-01",
							domain_namespace: "logistics",
							status: "active",
							active_version: "0.0.0",
							head_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
							created_at: "2026-11-10T00:00:00Z",
							updated_at: "2026-11-10T00:00:00Z",
						},
					]);
				}

				// Status
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/status" && req.method === "GET") {
					return Response.json({
						session_id: "s1",
						status: "active",
						active_version: "0.1.0",
						head_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
						active_jobs: 0,
					});
				}

				if (url.pathname === "/api/v1/ontology/studio/sessions/non-existent/status" && req.method === "GET") {
					return new Response(
						JSON.stringify({
							type: "urn:opendv:error:session-not-found",
							title: "Session Not Found",
							status: 404,
							error_code: "SESSION_NOT_FOUND",
							detail: "Session 'non-existent' was not found.",
						}),
						{ status: 404, headers: { "Content-Type": "application/problem+json" } },
					);
				}

				// Sources upload
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/sources" && req.method === "POST") {
					return Response.json({
						source_id: "src-01",
						source_sha256: "sha256-src-01",
						filename: "upload.txt",
						file_name: "upload.txt",
						size_bytes: 42,
						mime_type: "text/plain",
						indexed_at: "2026-11-10T00:00:00Z",
					});
				}

				// Parse
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/parse" && req.method === "POST") {
					return Response.json({
						job_id: "job-parse-01",
						session_id: "s1",
						status: "completed",
						parsed_documents_count: 1,
					});
				}

				// Clarify
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/clarify" && req.method === "POST") {
					return Response.json({
						job_id: "job-clarify-01",
						session_id: "s1",
						questions: [{ question_id: "q1", question_text: "What is a loan facility?" }],
					});
				}

				// Answers
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/answers" && req.method === "POST") {
					return Response.json({
						session_id: "s1",
						recorded_answers_count: 1,
					});
				}

				// Construct
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/construct" && req.method === "POST") {
					return Response.json({
						job_id: "job-construct-01",
						session_id: "s1",
						status: "completed",
						proposals_generated: 1,
					});
				}

				// Delta validate
				if (url.pathname === "/api/v1/ontology/validate-delta" && req.method === "POST") {
					return Response.json({
						valid: true,
						violations: [],
						diagnostics: ["Delta is valid"],
					});
				}

				// Delta apply
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/deltas/apply" && req.method === "POST") {
					return Response.json({
						session_id: "s1",
						new_version: "0.1.0",
						new_sha256: "new-sha256-abc",
						applied_operations_count: 1,
					});
				}

				// Proposals: list & show
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/proposals" && req.method === "GET") {
					return Response.json([
						{
							proposal_id: "p1",
							session_id: "s1",
							rationale: "Initial domain concept",
							confidence_score: 0.95,
							status: "PENDING",
						},
					]);
				}

				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/proposals/p1" && req.method === "GET") {
					return Response.json({
						proposal_id: "p1",
						session_id: "s1",
						rationale: "Initial domain concept",
						confidence_score: 0.95,
						status: "PENDING",
					});
				}

				// Proposal accept
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/proposals/p1/accept" && req.method === "POST") {
					const ifMatch = req.headers.get("if-match");
					if (ifMatch && ifMatch.includes("conflict-sha")) {
						return new Response(
							JSON.stringify({
								type: "urn:opendv:error:ont-evo-concurrent-modification",
								title: "Concurrent Modification",
								status: 412,
								error_code: "ONT_EVO_CONCURRENT_MODIFICATION",
								detail: "Head SHA has advanced.",
							}),
							{ status: 412, headers: { "Content-Type": "application/problem+json" } },
						);
					}

					const auth = req.headers.get("authorization") || "";
					const waiverKey = req.headers.get("x-opendv-waiver-key") || "";
					const body = (await req.json()) as Record<string, unknown>;
					if (
						body.low_confidence_waiver &&
						!auth.includes("steward-secret-key") &&
						!waiverKey.includes("steward-secret-key")
					) {
						return new Response(
							JSON.stringify({
								type: "urn:opendv:error:forbidden-admin-required",
								title: "Forbidden Admin Required",
								status: 403,
								error_code: "FORBIDDEN_ADMIN_REQUIRED",
								detail: "Admin/steward waiver key required.",
							}),
							{ status: 403, headers: { "Content-Type": "application/problem+json" } },
						);
					}

					return Response.json({
						session_id: "s1",
						proposal_id: "p1",
						status: "ACCEPTED",
						new_version: "0.2.0",
						new_sha256: "accepted-sha-456",
					});
				}

				// Proposal reject
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/proposals/p1/reject" && req.method === "POST") {
					return Response.json({
						session_id: "s1",
						proposal_id: "p1",
						status: "REJECTED",
					});
				}

				// Proposal rebase
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/proposals/p1/rebase" && req.method === "POST") {
					return Response.json({
						session_id: "s1",
						proposal_id: "p1",
						status: "REBASED",
						new_base_sha256: "rebased-sha-789",
					});
				}

				// Export
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/export" && req.method === "GET") {
					return Response.json({
						session_id: "s1",
						ontology_yaml: "domain_namespace: logistics\nversion: 0.1.0\n",
					});
				}

				// Promote
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/promote" && req.method === "POST") {
					return Response.json({
						session_id: "s1",
						promoted_version: "1.0.0",
						promoted_head_sha256: "promoted-sha-abc",
						promoted_at: "2026-11-10T00:00:00Z",
					});
				}

				// Bundle export
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/bundle" && req.method === "POST") {
					return new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00]), {
						headers: { "Content-Type": "application/zip" },
					});
				}

				// Bundle import
				if (url.pathname === "/api/v1/ontology/studio/sessions/bundle/import" && req.method === "POST") {
					return Response.json({
						session_id: "s-imported-01",
						domain_namespace: "logistics",
						status: "active",
						active_version: "0.1.0",
						head_sha256: "imported-head-sha",
						created_at: "2026-11-10T00:00:00Z",
						updated_at: "2026-11-10T00:00:00Z",
					});
				}

				// Bundle clone
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/bundle/clone" && req.method === "POST") {
					return Response.json({
						session_id: "s-cloned-01",
						domain_namespace: "logistics",
						status: "active",
						active_version: "0.1.0",
						head_sha256: "cloned-head-sha",
						created_at: "2026-11-10T00:00:00Z",
						updated_at: "2026-11-10T00:00:00Z",
					});
				}

				return new Response("Not found", { status: 404 });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop();
		for (const [k, v] of Object.entries(savedEnv)) {
			if (v !== undefined) {
				process.env[k] = v;
			} else {
				delete process.env[k];
			}
		}
		for (const p of [dummyKeyPath, dummySourcePath, dummyAnswersPath, dummyDeltaPath, dummyBundlePath]) {
			if (existsSync(p)) {
				unlinkSync(p);
			}
		}
	});

	it("prohibits ontology studio init in remote mode with exit 7 (LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE)", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			["ontology", "studio", "init", "--api-url", serverUrl, "--json"],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE");
	});

	it("executes ontology studio create with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"create",
				"--api-url",
				serverUrl,
				"--session",
				"s-test-01",
				"--domain-namespace",
				"logistics",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.session_id).toBe("s-test-01");
	});

	it("executes ontology studio list with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			["ontology", "studio", "list", "--api-url", serverUrl, "--json"],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(Array.isArray(out)).toBe(true);
		expect(out[0].session_id).toBe("s-test-01");
	});

	it("executes ontology studio status with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"status",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.session_id).toBe("s1");
		expect(out.status).toBe("active");
	});

	it("returns exit code 3 on ontology studio status for non-existent session", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"status",
				"--api-url",
				serverUrl,
				"--session",
				"non-existent",
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(3);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("SESSION_NOT_FOUND");
	});

	it("executes ontology studio upload with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"upload",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--file",
				dummySourcePath,
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.source_id).toBe("src-01");
	});

	it("executes ontology studio parse with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"parse",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.job_id).toBe("job-parse-01");
	});

	it("executes ontology studio clarify with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"clarify",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.job_id).toBe("job-clarify-01");
	});

	it("executes ontology studio answer with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"answer",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--file",
				dummyAnswersPath,
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.recorded_answers_count).toBe(1);
	});

	it("executes ontology studio construct with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"construct",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.job_id).toBe("job-construct-01");
	});

	it("executes ontology studio delta validate with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"delta",
				"validate",
				"--api-url",
				serverUrl,
				"--file",
				dummyDeltaPath,
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.valid).toBe(true);
	});

	it("executes ontology studio delta apply with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"delta",
				"apply",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--file",
				dummyDeltaPath,
				"--base-version",
				"0.0.0",
				"--base-sha256",
				"genesis-sha-000",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.new_version).toBe("0.1.0");
	});

	it("executes ontology studio proposal list with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"list",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(Array.isArray(out)).toBe(true);
		expect(out[0].proposal_id).toBe("p1");
	});

	it("executes ontology studio proposal show with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"show",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.proposal_id).toBe("p1");
	});

	it("proposal accept returns exit code 5 on OCC conflict", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--base-sha256",
				"conflict-sha",
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(5);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("ONT_EVO_CONCURRENT_MODIFICATION");
	});

	it("proposal accept returns exit code 4 on short waiver rationale", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--base-sha256",
				"valid-sha",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"too short",
				"--waiver-key-file",
				dummyKeyPath,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(4);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("INVALID_GROUNDING_WAIVER");
	});

	it("proposal accept returns exit code 7 on missing waiver credentials", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--base-sha256",
				"valid-sha",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"A valid rationale of more than ten characters",
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");
	});

	it("proposal accept succeeds with valid waiver key and rationale", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--base-sha256",
				"valid-sha",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Approved low confidence concept due to domain precedent",
				"--waiver-key-file",
				dummyKeyPath,
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.status).toBe("ACCEPTED");
		expect(out.new_version).toBe("0.2.0");
	});

	it("executes ontology studio proposal reject with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"reject",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.status).toBe("REJECTED");
	});

	it("executes ontology studio proposal rebase with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"rebase",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--proposal-id",
				"p1",
				"--base-sha256",
				"base-sha-123",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.status).toBe("REBASED");
	});

	it("executes ontology studio export with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"export",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--export-format",
				"yaml",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.session_id).toBe("s1");
	});

	it("promote returns exit code 1 on missing/short promotion rationale", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"promote",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--workspace-sha256",
				"ws-sha-12345",
				"--head-sha256",
				"head-sha-67890",
				"--rationale",
				"short",
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(1);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("PROMOTION_RATIONALE_REQUIRED");
	});

	it("executes ontology studio promote with exit code 0 when rationale is valid", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"promote",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--workspace-sha256",
				"ws-sha-12345",
				"--head-sha256",
				"head-sha-67890",
				"--rationale",
				"Promotion of validated v1.0.0 ontology to workspace",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.promoted_version).toBe("1.0.0");
	});

	it("executes ontology studio bundle export with exit code 0", async () => {
		const outLines: string[] = [];
		const tmpExportZip = `temp_bundle_export_${Date.now()}.zip`;
		try {
			const code = await runCli(
				[
					"ontology",
					"studio",
					"bundle",
					"export",
					"--api-url",
					serverUrl,
					"--session",
					"s1",
					"--out",
					tmpExportZip,
					"--export-rationale",
					"Auditing session export",
					"--json",
				],
				(msg) => outLines.push(msg),
			);

			expect(code).toBe(0);
			const out = JSON.parse(outLines.join("\n"));
			expect(out.session_id).toBe("s1");
			expect(existsSync(tmpExportZip)).toBe(true);
		} finally {
			if (existsSync(tmpExportZip)) {
				unlinkSync(tmpExportZip);
			}
		}
	});

	it("executes ontology studio bundle clone with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"bundle",
				"clone",
				"--api-url",
				serverUrl,
				"--session",
				"s1",
				"--new-session-id",
				"s-cloned-01",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.session_id).toBe("s-cloned-01");
	});

	it("executes ontology studio bundle import with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"bundle",
				"import",
				"--api-url",
				serverUrl,
				"--file",
				dummyBundlePath,
				"--acknowledge-unverified-import-attribution",
				"--acknowledgement-rationale",
				"Legitimate external bundle for audit test",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.session_id).toBe("s-imported-01");
	});

	it("RemoteExecutor enforces token precedence and transport security", async () => {
		try {
			// 1. Loopback receives env token
			process.env.OPENDV_STUDIO_TOKEN = "loopback-token-123";
			const loopbackExec = new RemoteExecutor({ baseUrl: "http://127.0.0.1:8000" });
			const loopbackHeaders = loopbackExec.getHeaders();
			expect(loopbackHeaders.Authorization).toBe("Bearer loopback-token-123");

			// 2. Extra headers override env token
			const overridden = loopbackExec.getHeaders({ Authorization: "Bearer override-token-456" });
			expect(overridden.Authorization).toBe("Bearer override-token-456");

			// 3. Plaintext http to non-loopback refuses to attach bearer credentials without opt-in
			const mockFetch: typeof fetch = async (url) => {
				if (String(url).endsWith("/api/v1/health")) {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}
				return new Response("OK");
			};
			const remoteHttpExec = new RemoteExecutor({
				baseUrl: "http://remote.opendv.internal:8000",
				fetchImpl: mockFetch,
			});
			const remoteHeaders = remoteHttpExec.getHeaders();
			expect(remoteHeaders.Authorization).toBeUndefined();

			// 4. Role validation throws on unsupported role
			expect(() => new RemoteExecutor({ baseUrl: "http://127.0.0.1:8000", role: "SUPERUSER_ROOT" }).getHeaders()).toThrow();

			// 5. Steward waiver key is refused over plaintext remote http
			process.env.OPENDV_WAIVER_KEY = "steward-secret-key-12345";
			let waiverRefused = false;
			try {
				await remoteHttpExec.execute("ontology", "studio proposal accept", {
					session: "s1",
					proposal_id: "p1",
					low_confidence_waiver: true,
					waiver_rationale: "Legitimate waiver reason with domain precedent",
				});
			} catch (err: unknown) {
				waiverRefused = true;
				expect((err as { errorCode?: string }).errorCode).toBe("INSECURE_TRANSPORT_REFUSED");
			}
			// 6. Remote HTTPS refuses to attach OPENDV_STUDIO_TOKEN (does not leak local secret)
			process.env.OPENDV_STUDIO_TOKEN = "loopback-token-123";
			delete process.env.OPENDV_TOKEN;
			const remoteHttpsExec = new RemoteExecutor({ baseUrl: "https://remote.opendv.internal:8000" });
			expect(remoteHttpsExec.getHeaders().Authorization).toBeUndefined();

			// 7. Remote HTTPS attaches OPENDV_TOKEN
			process.env.OPENDV_TOKEN = "remote-token-789";
			expect(remoteHttpsExec.getHeaders().Authorization).toBe("Bearer remote-token-789");

			// 8. When both are set, remote HTTPS uses OPENDV_TOKEN (not OPENDV_STUDIO_TOKEN)
			process.env.OPENDV_STUDIO_TOKEN = "loopback-token-123";
			expect(remoteHttpsExec.getHeaders().Authorization).toBe("Bearer remote-token-789");
		} finally {
			delete process.env.OPENDV_STUDIO_TOKEN;
			delete process.env.OPENDV_TOKEN;
			delete process.env.OPENDV_WAIVER_KEY;
		}
	});
});
