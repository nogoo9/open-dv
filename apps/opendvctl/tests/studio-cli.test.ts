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
	const dummyBinaryPath = `temp_binary_${Date.now()}.pdf`;
	const dummyAnswersPath = `temp_answers_${Date.now()}.json`;
	const dummyWrappedAnswersPath = `temp_wrapped_answers_${Date.now()}.json`;
	const dummyDeltaPath = `temp_delta_${Date.now()}.json`;
	const dummyBundlePath = `temp_bundle_${Date.now()}.zip`;
	const rawBinaryBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0xff, 0xfe, 0x00, 0x89]);

	let savedEnv: Record<string, string | undefined> = {};
	let lastAnswerBody: Record<string, unknown> | null = null;
	let lastConstructHeaders: Record<string, string> = {};
	let lastPromoteHeaders: Record<string, string> = {};
	let lastExportFormatQuery = "";
	let lastUploadedBytes: Uint8Array | null = null;
	let pollAttemptCount = 0;

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
		writeFileSync(dummyBinaryPath, rawBinaryBytes);
		writeFileSync(
			dummyAnswersPath,
			JSON.stringify([{ question_id: "q1", selected_options: ["opt1"], user_text: "A loan facility" }]),
		);
		writeFileSync(
			dummyWrappedAnswersPath,
			JSON.stringify({
				answers: [{ question_id: "q2", selected_options: ["optA"], user_text: "Advisor mapping" }],
			}),
		);
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

				// Session detail & status (canonical GET /sessions/{id} + legacy /status)
				if (
					(url.pathname === "/api/v1/ontology/studio/sessions/s1" ||
						url.pathname === "/api/v1/ontology/studio/sessions/s1/status") &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "s1",
						status: "active",
						active_version: "0.1.0",
						head_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
						active_jobs: 0,
					});
				}

				if (
					(url.pathname === "/api/v1/ontology/studio/sessions/non-existent" ||
						url.pathname === "/api/v1/ontology/studio/sessions/non-existent/status") &&
					req.method === "GET"
				) {
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

				// Sources upload (captures binary bytes intact)
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/sources" && req.method === "POST") {
					const fd = await req.formData();
					const uploaded = fd.get("file");
					if (uploaded instanceof Blob) {
						lastUploadedBytes = new Uint8Array(await uploaded.arrayBuffer());
					}
					return Response.json({
						source_id: "src-01",
						source_sha256: "sha256-src-01",
						filename: "upload.txt",
						file_name: "upload.txt",
						size_bytes: lastUploadedBytes ? lastUploadedBytes.byteLength : 42,
						mime_type: "application/octet-stream",
						do_not_send_to_agent: fd.get("do_not_send_to_agent") === "true",
						indexed_at: "2026-11-10T00:00:00Z",
					});
				}

				// Parse & Studio Job polling / SSE streaming
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/parse" && req.method === "POST") {
					const body = (await req.json()) as Record<string, unknown>;
					if (body.ocr_mode === "force") {
						return Response.json({
							job_id: "job-fail-sse",
							session_id: "s1",
							status: "queued",
						});
					}
					return Response.json({
						job_id: "job-parse-01",
						session_id: "s1",
						status: "queued",
						parsed_documents_count: 1,
					});
				}

				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/jobs/job-parse-01" && req.method === "GET") {
					pollAttemptCount += 1;
					if (pollAttemptCount < 2) {
						return Response.json({
							job_id: "job-parse-01",
							session_id: "s1",
							status: "RUNNING",
							progress_pct: 50,
						});
					}
					return Response.json({
						job_id: "job-parse-01",
						session_id: "s1",
						status: "COMPLETED",
						progress_pct: 100,
						result: { parsed_sources_count: 1, chunks_count: 4 },
					});
				}

				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s1/jobs/job-parse-01/events" &&
					req.method === "GET"
				) {
					const ssePayload = [
						"id: 1\nevent: job_started\ndata: {\"job_id\":\"job-parse-01\",\"stage\":\"parsing\"}\n\n",
						"id: 2\nevent: progress_update\ndata: {\"job_id\":\"job-parse-01\",\"progress_pct\":65}\n\n",
						"id: 3\nevent: job_completed\ndata: {\"job_id\":\"job-parse-01\",\"status\":\"COMPLETED\",\"result\":{\"parsed_sources_count\":1}}\n\n",
					].join("");
					return new Response(ssePayload, {
						headers: { "Content-Type": "text/event-stream" },
					});
				}

				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s1/jobs/job-fail-sse/events" &&
					req.method === "GET"
				) {
					const sseFail = [
						"id: 1\nevent: job_started\ndata: {\"job_id\":\"job-fail-sse\"}\n\n",
						"id: 2\nevent: job_failed\ndata: {\"job_id\":\"job-fail-sse\",\"error_code\":\"CORRUPT_SOURCE_BLOB\",\"detail\":\"Corrupt PDF stream\"}\n\n",
					].join("");
					return new Response(sseFail, {
						headers: { "Content-Type": "text/event-stream" },
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

				// Answers GET (OCC answers_sha256) & POST
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/answers" && req.method === "GET") {
					return Response.json({
						session_id: "s1",
						answers: [],
						answers_sha256: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
					});
				}

				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/answers" && req.method === "POST") {
					const body = (await req.json()) as Record<string, unknown>;
					lastAnswerBody = body;
					if (!body.expected_answers_sha256 || !Array.isArray(body.answers)) {
						return new Response(
							JSON.stringify({
								status: 400,
								error_code: "MISSING_AUTO_COMMIT_PRECONDITION",
								detail: "expected_answers_sha256 and answers array required",
							}),
							{ status: 400, headers: { "Content-Type": "application/problem+json" } },
						);
					}
					return Response.json({
						session_id: "s1",
						recorded_answers_count: body.answers.length,
						answers_sha256: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
					});
				}

				// Construct
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/construct" && req.method === "POST") {
					lastConstructHeaders = {};
					for (const [k, v] of req.headers.entries()) {
						lastConstructHeaders[k.toLowerCase()] = v;
					}
					return Response.json({
						job_id: "job-construct-01",
						session_id: "s1",
						status: "completed",
						proposals_generated: 1,
					});
				}

				// Delta validate (both global /api/v1/ontology/validate-delta and session /sessions/s1/validate)
				if (
					(url.pathname === "/api/v1/ontology/validate-delta" ||
						url.pathname === "/api/v1/ontology/studio/sessions/s1/validate") &&
					req.method === "POST"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					const delta = (body.delta ?? body) as Record<string, unknown>;
					if (delta.delta_id === "delta-invalid") {
						return Response.json({
							valid: false,
							violations: ["ONT-EVO-002: Ungrounded concept"],
							diagnostics: [],
						});
					}
					return Response.json({
						valid: true,
						violations: [],
						diagnostics: ["Delta is valid"],
					});
				}

				// Delta apply (canonical POST /sessions/s1/delta)
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/delta" && req.method === "POST") {
					const body = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: "s1",
						new_version: "0.1.0",
						new_sha256: "new-sha256-abc",
						parent_sha256: body.parent_sha256 ?? null,
						applied_operations_count: Array.isArray(body.operations) ? body.operations.length : 1,
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

				// Export (returns raw YAML/Turtle text, never JSON, matching platform API!)
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/export" && req.method === "GET") {
					lastExportFormatQuery = url.searchParams.get("export_format") || "";
					return new Response("domain_namespace: logistics\nontology_version: 0.1.0\n", {
						headers: { "Content-Type": "application/x-yaml" },
					});
				}

				// Promote (captures If-Match and X-OpenDV-Workspace-If-Match)
				if (url.pathname === "/api/v1/ontology/studio/sessions/s1/promote" && req.method === "POST") {
					lastPromoteHeaders = {};
					for (const [k, v] of req.headers.entries()) {
						lastPromoteHeaders[k.toLowerCase()] = v;
					}
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
		for (const p of [
			dummyKeyPath,
			dummySourcePath,
			dummyBinaryPath,
			dummyAnswersPath,
			dummyWrappedAnswersPath,
			dummyDeltaPath,
			dummyBundlePath,
		]) {
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
			expect(waiverRefused).toBe(true);
		} finally {
			delete process.env.OPENDV_STUDIO_TOKEN;
			delete process.env.OPENDV_TOKEN;
			delete process.env.OPENDV_WAIVER_KEY;
		}
	});

	it("CRIT-01: studio answer supports -q/-o/-t flags, unwraps {answers:[...]}, and auto-fetches expected_answers_sha256", async () => {
		const out1: string[] = [];
		const code1 = await runCli(
			[
				"ontology",
				"studio",
				"answer",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"-q",
				"q-card-1",
				"-o",
				"opt-many",
				"-t",
				"Clarified 1:N cardinality",
				"--json",
			],
			(msg) => out1.push(msg),
		);
		expect(code1).toBe(0);
		expect(lastAnswerBody?.expected_answers_sha256).toBe(
			"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
		);
		const sentAnswers1 = lastAnswerBody?.answers as Array<Record<string, unknown>>;
		expect(sentAnswers1[0]).toEqual({
			question_id: "q-card-1",
			selected_options: ["opt-many"],
			user_text: "Clarified 1:N cardinality",
		});

		const out2: string[] = [];
		const code2 = await runCli(
			[
				"ontology",
				"studio",
				"answer",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"-f",
				dummyWrappedAnswersPath,
				"--json",
			],
			(msg) => out2.push(msg),
		);
		expect(code2).toBe(0);
		const sentAnswers2 = lastAnswerBody?.answers as Array<Record<string, unknown>>;
		expect(sentAnswers2[0]?.question_id).toBe("q2");
		expect(sentAnswers2[0]?.selected_options).toEqual(["optA"]);
	});

	it("CRIT-02: studio construct --auto-commit enforces deterministic preconditions (exit 2) and sends If-Match", async () => {
		const errLines: string[] = [];
		const badCode = await runCli(
			[
				"ontology",
				"studio",
				"construct",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"--auto-commit",
				"--json",
			],
			() => {},
			(msg) => errLines.push(msg),
		);
		expect(badCode).toBe(2);
		expect(JSON.parse(errLines.join("\n")).error_code).toBe("MISSING_AUTO_COMMIT_PRECONDITION");

		const outLines: string[] = [];
		const okCode = await runCli(
			[
				"ontology",
				"studio",
				"construct",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"--auto-commit",
				"--mode",
				"deterministic",
				"--base-version",
				"0.1.0",
				"--base-sha256",
				"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
				"--json",
			],
			(msg) => outLines.push(msg),
		);
		expect(okCode).toBe(0);
		expect(lastConstructHeaders["if-match"]).toBe(
			'"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"',
		);
	});

	it("CRIT-03: studio export defaults to yaml (never toon), reads raw text, and writes --out", async () => {
		const outYaml = `temp_export_${Date.now()}.yaml`;
		try {
			const outLines: string[] = [];
			const code = await runCli(
				[
					"ontology",
					"studio",
					"export",
					"--api-url",
					serverUrl,
					"-s",
					"s1",
					"--out",
					outYaml,
				],
				(msg) => outLines.push(msg),
			);
			expect(code).toBe(0);
			expect(lastExportFormatQuery).toBe("yaml");
			expect(outLines.join("\n")).toContain("domain_namespace: logistics");
			expect(existsSync(outYaml)).toBe(true);
		} finally {
			if (existsSync(outYaml)) unlinkSync(outYaml);
		}
	});

	it("CRIT-04: studio parse supports --wait polling and --stream SSE (propagating job_failed exit codes)", async () => {
		pollAttemptCount = 0;
		const waitOut: string[] = [];
		const waitCode = await runCli(
			["ontology", "studio", "parse", "--api-url", serverUrl, "-s", "s1", "--wait", "--json"],
			(msg) => waitOut.push(msg),
		);
		expect(waitCode).toBe(0);
		expect(pollAttemptCount).toBeGreaterThanOrEqual(2);
		expect(JSON.parse(waitOut.join("\n")).status).toBe("COMPLETED");

		const streamOut: string[] = [];
		const streamCode = await runCli(
			["ontology", "studio", "parse", "--api-url", serverUrl, "-s", "s1", "--stream", "--json"],
			(msg) => streamOut.push(msg),
		);
		expect(streamCode).toBe(0);
		expect(JSON.parse(streamOut.join("\n")).status).toBe("COMPLETED");

		const failErr: string[] = [];
		const failCode = await runCli(
			[
				"ontology",
				"studio",
				"parse",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"--ocr-mode",
				"force",
				"--stream",
				"--json",
			],
			() => {},
			(msg) => failErr.push(msg),
		);
		expect(failCode).toBe(4);
		const lastErrLine = failErr[failErr.length - 1] ?? "{}";
		expect(JSON.parse(lastErrLine).error_code).toBe("CORRUPT_SOURCE_BLOB");
	});

	it("CRIT-05 & MAJ-05: delta apply works without --base-version and delta validate exits 4 on invalid delta", async () => {
		const applyOut: string[] = [];
		const applyCode = await runCli(
			[
				"ontology",
				"studio",
				"delta",
				"apply",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"-f",
				dummyDeltaPath,
				"--base-sha256",
				"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
				"--json",
			],
			(msg) => applyOut.push(msg),
		);
		expect(applyCode).toBe(0);

		const badDeltaPath = `temp_bad_delta_${Date.now()}.json`;
		writeFileSync(
			badDeltaPath,
			JSON.stringify({
				delta_id: "delta-invalid",
				operations: [{ op: "AddConcept", concept_uri: "urn:ontology:entity:bad" }],
			}),
		);
		try {
			const valOut: string[] = [];
			const valCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"validate",
					"--api-url",
					serverUrl,
					"-s",
					"s1",
					"-f",
					badDeltaPath,
					"--json",
				],
				(msg) => valOut.push(msg),
			);
			expect(valCode).toBe(4);
			expect(JSON.parse(valOut.join("\n")).error_code).toBe("ONT_EVO_CONTRACT_VIOLATION");
		} finally {
			if (existsSync(badDeltaPath)) unlinkSync(badDeltaPath);
		}
	});

	it("MAJ-02 & MAJ-04: promote attaches If-Match + X-OpenDV-Workspace-If-Match, rejects remote server paths, and upload preserves binary bytes", async () => {
		// Verify last promote headers from previous loopback promote test
		const promOut: string[] = [];
		const promCode = await runCli(
			[
				"ontology",
				"studio",
				"promote",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"--workspace-sha256",
				"1111111111111111111111111111111111111111111111111111111111111111",
				"--head-sha256",
				"2222222222222222222222222222222222222222222222222222222222222222",
				"-r",
				"Valid promotion rationale for header check",
				"--json",
			],
			(msg) => promOut.push(msg),
		);
		expect(promCode).toBe(0);
		expect(lastPromoteHeaders["if-match"]).toBe(
			'"2222222222222222222222222222222222222222222222222222222222222222"',
		);
		expect(lastPromoteHeaders["x-opendv-workspace-if-match"]).toBe(
			'"1111111111111111111111111111111111111111111111111111111111111111"',
		);

		// Non-loopback remote path guard on create --from-workspace
		const remoteExec = new RemoteExecutor({
			baseUrl: "https://remote.opendv.internal:8443",
			fetchImpl: async (url) => {
				if (String(url).endsWith("/api/v1/health")) {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}
				return Response.json({});
			},
		});
		let remotePathBlocked = false;
		try {
			await remoteExec.execute("ontology", "studio create", {
				session: "s-rem",
				from_workspace: "/etc/passwd",
			});
		} catch (err: unknown) {
			remotePathBlocked = true;
			expect((err as { errorCode?: string }).errorCode).toBe("REMOTE_SERVER_PATH_FORBIDDEN");
		}
		expect(remotePathBlocked).toBe(true);

		// Binary upload preserves exact raw bytes (0xff 0xfe 0x00 0x89) without UTF-8 corruption
		lastUploadedBytes = null;
		const upOut: string[] = [];
		const upCode = await runCli(
			[
				"ontology",
				"studio",
				"upload",
				"--api-url",
				serverUrl,
				"-s",
				"s1",
				"-f",
				dummyBinaryPath,
				"--confidential",
				"--json",
			],
			(msg) => upOut.push(msg),
		);
		expect(upCode).toBe(0);
		expect(Array.from(lastUploadedBytes ?? [])).toEqual(Array.from(rawBinaryBytes));
		expect(JSON.parse(upOut.join("\n")).do_not_send_to_agent).toBe(true);
	});

	it("P13 MAJ-01..03 & S5-MAJ-03: preserves OCC preconditions on delta apply/validate & answer, rejects empty delta, and applies adaptive job poll backoff", async () => {
		const staleSha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
		const headSha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
		let capturedDeltaBody: Record<string, unknown> | null = null;
		let capturedDeltaIfMatch = "";
		let capturedValBody: Record<string, unknown> | null = null;
		let getAnswersCalls = 0;
		let existingAnswersList: unknown[] = [];
		let jobPollCount = 0;

		const occServer = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);
				if (url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ" &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "s-occ",
						active_version: "1.0.0",
						head_version: "1.0.0",
						head_sha256: headSha,
					});
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ/delta" &&
					req.method === "POST"
				) {
					capturedDeltaIfMatch = req.headers.get("if-match") ?? "";
					capturedDeltaBody = (await req.json()) as Record<string, unknown>;
					if (capturedDeltaIfMatch !== `"${headSha}"`) {
						return Response.json(
							{
								error_code: "ONT_EVO_CONCURRENT_MODIFICATION",
								detail: "Stale If-Match precondition",
							},
							{ status: 412 },
						);
					}
					return Response.json({ status: "COMMITTED", new_version: "1.1.0" });
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ/validate" &&
					req.method === "POST"
				) {
					capturedValBody = (await req.json()) as Record<string, unknown>;
					return Response.json({ valid: true, diagnostics: [], violations: [] });
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ/answers" &&
					req.method === "GET"
				) {
					getAnswersCalls++;
					return Response.json({
						session_id: "s-occ",
						answers: existingAnswersList,
						answers_sha256: headSha,
					});
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ/answers" &&
					req.method === "POST"
				) {
					return Response.json(await req.json());
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-occ/parse" &&
					req.method === "POST"
				) {
					return Response.json({ job_id: "job-backoff", status: "RUNNING" });
				}
				if (
					url.pathname ===
					"/api/v1/ontology/studio/sessions/s-occ/jobs/job-backoff"
				) {
					jobPollCount++;
					if (jobPollCount >= 12) {
						return Response.json({ job_id: "job-backoff", status: "COMPLETED" });
					}
					return Response.json({ job_id: "job-backoff", status: "RUNNING" });
				}
				return new Response("Not Found", { status: 404 });
			},
		});

		const occUrl = `http://127.0.0.1:${occServer.port}`;
		const staleDeltaFile = `temp_stale_delta_${Date.now()}.json`;
		const emptyDeltaFile = `temp_empty_delta_${Date.now()}.json`;
		await Bun.write(
			staleDeltaFile,
			JSON.stringify({
				base_version: "1.0.0",
				parent_sha256: staleSha,
				operations: [
					{
						op_type: "add_concept",
						concept_uri: "urn:ontology:entity:account",
						concept_name: "Account",
					},
				],
			}),
		);
		await Bun.write(emptyDeltaFile, JSON.stringify({ operations: [] }));

		try {
			// 1. Stale delta parent_sha256 is NEVER overwritten by fetchSessionDetail -> 412 exit 5!
			const staleErr: string[] = [];
			const staleCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-f",
					staleDeltaFile,
					"--json",
				],
				() => {},
				(msg) => staleErr.push(msg),
			);
			expect(staleCode).toBe(5);
			expect(capturedDeltaIfMatch).toBe(`"${staleSha}"`);
			expect(capturedDeltaBody?.parent_sha256).toBe(staleSha);
			expect(JSON.parse(staleErr.join("\n")).error_code).toBe(
				"ONT_EVO_CONCURRENT_MODIFICATION",
			);

			// 2. Conflicting --base-sha256 vs delta parent_sha256 -> exit 2 PRECONDITION_BASE_SHA_MISMATCH
			const mismatchErr: string[] = [];
			const mismatchCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-f",
					staleDeltaFile,
					"--base-sha256",
					headSha,
					"--json",
				],
				() => {},
				(msg) => mismatchErr.push(msg),
			);
			expect(mismatchCode).toBe(2);
			expect(JSON.parse(mismatchErr.join("\n")).error_code).toBe(
				"PRECONDITION_BASE_SHA_MISMATCH",
			);

			// 3. Non-genesis delta missing both --base-sha256 and parent_sha256 -> exit 2 MISSING_PRECONDITION_HEADER
			const noShaFile = `temp_no_sha_${Date.now()}.json`;
			await Bun.write(
				noShaFile,
				JSON.stringify({
					base_version: "1.0.0",
					operations: [
						{
							op_type: "add_concept",
							concept_uri: "urn:ontology:entity:account",
							concept_name: "Account",
						},
					],
				}),
			);
			try {
				const missErr: string[] = [];
				const missCode = await runCli(
					[
						"ontology",
						"studio",
						"delta",
						"apply",
						"--api-url",
						occUrl,
						"-s",
						"s-occ",
						"-f",
						noShaFile,
						"--json",
					],
					() => {},
					(msg) => missErr.push(msg),
				);
				expect(missCode).toBe(2);
				expect(JSON.parse(missErr.join("\n")).error_code).toBe(
					"MISSING_PRECONDITION_HEADER",
				);
			} finally {
				if (existsSync(noShaFile)) unlinkSync(noShaFile);
			}

			// 4. Empty delta apply -> exit 1 NO_OP_DELTA
			const noopErr: string[] = [];
			const noopCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-f",
					emptyDeltaFile,
					"--json",
				],
				() => {},
				(msg) => noopErr.push(msg),
			);
			expect(noopCode).toBe(1);
			expect(JSON.parse(noopErr.join("\n")).error_code).toBe("NO_OP_DELTA");

			// 5. delta validate at 0.0.0 preserves explicit --base-sha256 instead of forcing ""
			const valOut: string[] = [];
			const valCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"validate",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-f",
					staleDeltaFile,
					"--base-version",
					"0.0.0",
					"--base-sha256",
					staleSha,
					"--json",
				],
				(msg) => valOut.push(msg),
			);
			expect(valCode).toBe(0);
			expect(capturedValBody?.parent_sha256).toBe(staleSha);

			// 6. answer preserves explicit --expected-answers-sha256 without GET, and requires precondition when answers exist
			const ansOut: string[] = [];
			const ansCode = await runCli(
				[
					"ontology",
					"studio",
					"answer",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-q",
					"q1",
					"-o",
					"opt-a",
					"--expected-answers-sha256",
					staleSha,
					"--json",
				],
				(msg) => ansOut.push(msg),
			);
			expect(ansCode).toBe(0);
			expect(getAnswersCalls).toBe(0);
			expect(JSON.parse(ansOut.join("\n")).expected_answers_sha256).toBe(
				staleSha,
			);

			existingAnswersList = [{ question_id: "q1", selected_option_id: "opt-a" }];
			const ansConflictErr: string[] = [];
			const ansConflictCode = await runCli(
				[
					"ontology",
					"studio",
					"answer",
					"--api-url",
					occUrl,
					"-s",
					"s-occ",
					"-q",
					"q1",
					"-o",
					"opt-b",
					"--json",
				],
				() => {},
				(msg) => ansConflictErr.push(msg),
			);
			expect(ansConflictCode).toBe(2);
			expect(JSON.parse(ansConflictErr.join("\n")).error_code).toBe(
				"MISSING_AUTO_COMMIT_PRECONDITION",
			);

			// 7. Adaptive job polling backoff completes cleanly
			const execFast = new RemoteExecutor({
				baseUrl: occUrl,
				pollIntervalMs: 1,
			});
			const waitRes = await execFast.execute("ontology", "studio parse", {
				session: "s-occ",
				wait: true,
			});
			expect(waitRes.exitCode).toBe(0);
			expect(jobPollCount).toBe(12);
		} finally {
			occServer.stop();
			if (existsSync(staleDeltaFile)) unlinkSync(staleDeltaFile);
			if (existsSync(emptyDeltaFile)) unlinkSync(emptyDeltaFile);
		}
	});

	it("P13b::MAJ-01 & P13a::MAJ-01: genesis 0.0.0 delta validate and delta apply send genuine head_sha256 in parent_sha256 matching If-Match", async () => {
		const defaultGenesisSha =
			"1916a5c6be732b3867e8b123456789abcdef0123456789abcdef0123456789ab";
		const customGenesisSha =
			"c987654321fedcba0123456789abcdef0123456789abcdef0123456789abcdef";

		let lastValidateBody: Record<string, unknown> | null = null;
		let lastApplyBody: Record<string, unknown> | null = null;
		let lastApplyIfMatch: string | null = null;

		const genServer = Bun.serve({
			port: 0,
			async fetch(req) {
				const url = new URL(req.url);
				if (url.pathname === "/api/v1/health") {
					return Response.json({ status: "ok", contract_version: "1.0.0" });
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-gen-default" &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "s-gen-default",
						status: "ACTIVE",
						head_version: "0.0.0",
						active_version: "0.0.0",
						head_sha256: defaultGenesisSha,
					});
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-gen-custom" &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "s-gen-custom",
						status: "ACTIVE",
						domain_namespace: "supply_chain",
						head_version: "0.0.0",
						active_version: "0.0.0",
						head_sha256: customGenesisSha,
					});
				}
				if (
					url.pathname === "/api/v1/ontology/studio/sessions/s-gen-empty-sha" &&
					req.method === "GET"
				) {
					return Response.json({
						session_id: "s-gen-empty-sha",
						status: "ACTIVE",
						head_version: "0.0.0",
						active_version: "0.0.0",
						head_sha256: "",
					});
				}
				if (
					(url.pathname ===
						"/api/v1/ontology/studio/sessions/s-gen-default/validate" ||
						url.pathname ===
							"/api/v1/ontology/studio/sessions/s-gen-custom/validate") &&
					req.method === "POST"
				) {
					lastValidateBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						valid: true,
						violations: [],
						diagnostics: [],
					});
				}
				if (
					(url.pathname ===
						"/api/v1/ontology/studio/sessions/s-gen-default/delta" ||
						url.pathname ===
							"/api/v1/ontology/studio/sessions/s-gen-custom/delta") &&
					req.method === "POST"
				) {
					lastApplyIfMatch = req.headers.get("if-match");
					lastApplyBody = (await req.json()) as Record<string, unknown>;
					return Response.json({
						session_id: url.pathname.includes("s-gen-custom")
							? "s-gen-custom"
							: "s-gen-default",
						target_version: lastApplyBody.target_version,
						target_sha256: "a".repeat(64),
						parent_sha256: lastApplyBody.parent_sha256,
						applied_operations_count: 1,
					});
				}
				return new Response("Not Found", { status: 404 });
			},
		});

		const genUrl = `http://127.0.0.1:${genServer.port}`;
		const majorConceptDeltaFile = `temp_gen_major_${Date.now()}.json`;
		const minorEnumDeltaFile = `temp_gen_minor_${Date.now()}.json`;

		await Bun.write(
			majorConceptDeltaFile,
			JSON.stringify({
				operations: [
					{
						op_type: "add_concept",
						concept_uri: "urn:ontology:entity:shipment",
						concept_name: "Shipment",
					},
				],
			}),
		);
		await Bun.write(
			minorEnumDeltaFile,
			JSON.stringify({
				operations: [
					{
						op_type: "add_reference_enumeration",
						enum_uri: "urn:ontology:enum:shipment_status",
						enum_name: "ShipmentStatus",
						allowed_values: ["PENDING", "SHIPPED"],
					},
				],
			}),
		);

		const savedWaiverKey = process.env.OPENDV_WAIVER_KEY;
		try {
			// 1. 0.0.0 -> 1.0.0 (add_concept): delta validate & delta apply send genuine defaultGenesisSha
			const valMajorCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"validate",
					"--api-url",
					genUrl,
					"-s",
					"s-gen-default",
					"-f",
					majorConceptDeltaFile,
					"--json",
				],
				() => {},
			);
			expect(valMajorCode).toBe(0);
			expect(lastValidateBody?.base_version).toBe("0.0.0");
			expect(lastValidateBody?.target_version).toBe("1.0.0");
			expect(lastValidateBody?.parent_sha256).toBe(defaultGenesisSha);

			const applyMajorCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					genUrl,
					"-s",
					"s-gen-default",
					"-f",
					majorConceptDeltaFile,
					"--json",
				],
				() => {},
			);
			expect(applyMajorCode).toBe(0);
			expect(lastApplyBody?.base_version).toBe("0.0.0");
			expect(lastApplyBody?.target_version).toBe("1.0.0");
			expect(lastApplyBody?.parent_sha256).toBe(defaultGenesisSha);
			expect(lastApplyIfMatch).toBe(`"${defaultGenesisSha}"`);

			// 2. 0.0.0 -> 0.1.0 (non-CBC add_reference_enumeration on custom namespace):
			// delta validate & delta apply send genuine customGenesisSha + grounding rationale without key-copying
			process.env.OPENDV_WAIVER_KEY = "local-steward-secret";
			const valMinorCode = await runCli(
				[
					"--role",
					"DATA_STEWARD",
					"ontology",
					"studio",
					"delta",
					"validate",
					"--api-url",
					genUrl,
					"-s",
					"s-gen-custom",
					"-f",
					minorEnumDeltaFile,
					"--low-confidence-waiver",
					"--grounding-rationale",
					"Approved genesis reference enumeration waiver",
					"--json",
				],
				() => {},
			);
			expect(valMinorCode).toBe(0);
			expect(lastValidateBody?.base_version).toBe("0.0.0");
			expect(lastValidateBody?.target_version).toBe("0.1.0");
			expect(lastValidateBody?.parent_sha256).toBe(customGenesisSha);
			expect(lastValidateBody?.grounding_rationale).toBe(
				"Approved genesis reference enumeration waiver",
			);

			const applyMinorCode = await runCli(
				[
					"--role",
					"DATA_STEWARD",
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					genUrl,
					"-s",
					"s-gen-custom",
					"-f",
					minorEnumDeltaFile,
					"--low-confidence-waiver",
					"--waiver-rationale",
					"Approved genesis waiver via --waiver-rationale",
					"--json",
				],
				() => {},
			);
			expect(applyMinorCode).toBe(0);
			expect(lastApplyBody?.base_version).toBe("0.0.0");
			expect(lastApplyBody?.target_version).toBe("0.1.0");
			expect(lastApplyBody?.parent_sha256).toBe(customGenesisSha);
			expect(lastApplyIfMatch).toBe(`"${customGenesisSha}"`);
			expect(lastApplyBody?.grounding_rationale).toBe(
				"Approved genesis waiver via --waiver-rationale",
			);

			// 3. 0.0.0 session with empty head_sha256 and no explicit SHA raises MISSING_PRECONDITION_HEADER
			const emptyShaErr: string[] = [];
			const emptyShaCode = await runCli(
				[
					"ontology",
					"studio",
					"delta",
					"apply",
					"--api-url",
					genUrl,
					"-s",
					"s-gen-empty-sha",
					"-f",
					majorConceptDeltaFile,
					"--json",
				],
				() => {},
				(msg) => emptyShaErr.push(msg),
			);
			expect(emptyShaCode).toBe(2);
			expect(JSON.parse(emptyShaErr.join("\n")).error_code).toBe(
				"MISSING_PRECONDITION_HEADER",
			);
		} finally {
			if (savedWaiverKey === undefined) {
				delete process.env.OPENDV_WAIVER_KEY;
			} else {
				process.env.OPENDV_WAIVER_KEY = savedWaiverKey;
			}
			genServer.stop();
			if (existsSync(majorConceptDeltaFile)) unlinkSync(majorConceptDeltaFile);
			if (existsSync(minorEnumDeltaFile)) unlinkSync(minorEnumDeltaFile);
		}
	});
});
