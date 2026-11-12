import { describe, expect, it, beforeAll, afterAll, beforeEach, afterEach } from "bun:test";
import { runCli } from "../src/main";
import { RemoteExecutor } from "../src/remote-executor";
import { loadCliSpec } from "../src/spec";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as os from "node:os";

describe("Challenger 5C Security & Waiver Empirical Tests (opendvctl)", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;
	let tempDir: string;
	const savedEnv = { ...process.env };
	const lastHeaders: Record<string, string> = {};

	beforeAll(() => {
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

				for (const [k, v] of req.headers.entries()) {
					lastHeaders[k.toLowerCase()] = v;
				}

				if (url.pathname.includes("/accept")) {
					const waiverKey = req.headers.get("x-opendv-waiver-key");
					if (waiverKey === "valid-steward-secret-key") {
						return Response.json({
							status: "ACCEPTED",
							proposal_id: "prop-01",
							new_head_sha256: "0000000000000000000000000000000000000000000000000000000000000000",
						});
					} else {
						return new Response(
							JSON.stringify({
								type: "urn:opendv:error:forbidden-admin-required",
								title: "Forbidden Admin Required",
								status: 403,
								detail: "Invalid steward waiver credentials.",
								error_code: "FORBIDDEN_ADMIN_REQUIRED",
							}),
							{
								status: 403,
								headers: { "Content-Type": "application/problem+json" },
							},
						);
					}
				}

				if (url.pathname.includes("/status")) {
					return Response.json({
						session_id: "sec-sess-01",
						status: "ACTIVE",
					});
				}

				return Response.json({ status: "OK" });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop(true);
	});

	beforeEach(async () => {
		tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "challenger-5c-"));
		delete process.env.OPENDV_WAIVER_KEY;
		delete process.env.OPENDV_STUDIO_TOKEN;
		delete process.env.OPENDV_TOKEN;
		for (const k of Object.keys(lastHeaders)) delete lastHeaders[k];
	});

	afterEach(async () => {
		process.env = { ...savedEnv };
		try {
			await fs.rm(tempDir, { recursive: true, force: true });
		} catch {
			// ignore cleanup error
		}
	});

	it("Vector 1: Raw secrets on CLI argv (--waiver-key) are strictly rejected by parser with exit 2", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"--waiver-key",
				"raw-secret-key-that-must-be-rejected",
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(2);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
	});

	it("Vector 1: Non-existent waiver key file fails with exit 3 (FILE_NOT_FOUND), not FORBIDDEN_ADMIN_REQUIRED", async () => {
		const nonExistentPath = path.join(tempDir, "does_not_exist.key");
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"--waiver-key-file",
				nonExistentPath,
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(3);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FILE_NOT_FOUND");
	});

	it("Vector 1: Empty waiver key file fails with exit 7", async () => {
		const emptyKeyFile = path.join(tempDir, "empty.key");
		await fs.writeFile(emptyKeyFile, "   \n\t  \r\n", "utf-8");

		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"--waiver-key-file",
				emptyKeyFile,
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");
	});

	it("Vector 1: OPENDV_WAIVER_KEY works identically as raw secret in both RemoteExecutor and runCli (P13::MAJ-01)", async () => {
		// A. Direct RemoteExecutor: OPENDV_WAIVER_KEY as raw secret succeeds
		process.env.OPENDV_WAIVER_KEY = "valid-steward-secret-key";
		const directExec = new RemoteExecutor({
			baseUrl: serverUrl,
			role: "DATA_STEWARD",
		});
		const directRes = await directExec.execute("ontology", "studio proposal accept", {
			session: "sec-sess-01",
			proposal_id: "prop-01",
			base_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
			low_confidence_waiver: true,
			waiver_rationale: "Adequate rationale for security compliance test.",
		});
		expect(directRes.exitCode).toBe(0);
		expect(lastHeaders["x-opendv-waiver-key"]).toBe("valid-steward-secret-key");

		// B. Via runCli: OPENDV_WAIVER_KEY is NOT bound as env_var on --waiver-key-file,
		// so passing raw secret in OPENDV_WAIVER_KEY succeeds end-to-end!
		const outLines: string[] = [];
		const errLines: string[] = [];
		const codeRawEnv = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);
		expect(codeRawEnv).toBe(0);
		expect(lastHeaders["x-opendv-waiver-key"]).toBe("valid-steward-secret-key");

		// C. Explicit --waiver-key-file (-k) overrides OPENDV_WAIVER_KEY cleanly:
		process.env.OPENDV_WAIVER_KEY = "unused-fallback-secret";
		const envKeyFile = path.join(tempDir, "env_steward.key");
		await fs.writeFile(envKeyFile, "valid-steward-secret-key\n", "utf-8");

		const outLines2: string[] = [];
		const errLines2: string[] = [];
		const codeFilePath = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"-k",
				envKeyFile,
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines2.push(msg),
			(msg) => errLines2.push(msg),
		);
		expect(codeFilePath).toBe(0);
		expect(lastHeaders["x-opendv-waiver-key"]).toBe("valid-steward-secret-key");
	});

	it("Vector 1: Waiver checks admin role (exit 7) BEFORE rationale length (exit 4) and allows Bearer token without local key file (MAJ-03)", async () => {
		// 1. Non-admin role (PUBLIC) with short rationale fails with exit 7 (NOT exit 4!)
		const outLines: string[] = [];
		const errLines: string[] = [];
		const codeRoleFirst = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"--role",
				"PUBLIC",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"short",
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);
		expect(codeRoleFirst).toBe(7);
		expect(JSON.parse(errLines.join("\n")).error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");

		// 2a. Loopback workstation call WITH OPENDV_STUDIO_TOKEN still requires explicit waiver key (exit 7 parity)
		process.env.OPENDV_STUDIO_TOKEN = "valid-admin-bearer";
		const outLinesLoopback: string[] = [];
		const errLinesLoopback: string[] = [];
		const codeLoopbackBlocked = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for loopback admin test.",
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLinesLoopback.push(msg),
			(msg) => errLinesLoopback.push(msg),
		);
		expect(codeLoopbackBlocked).toBe(7);
		expect(JSON.parse(errLinesLoopback.join("\n")).error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");

		// 2b. Non-loopback remote admin with RS256 Bearer token (OPENDV_TOKEN) is not blocked client-side when lacking local waiver key file
		process.env.OPENDV_TOKEN = "valid-remote-admin-jwt";
		let remoteHeaders: Record<string, string> = {};
		const remoteExec = new RemoteExecutor({
			baseUrl: "https://remote.opendv.example",
			role: "DATA_STEWARD",
			fetchImpl: (async (input: RequestInfo | URL, init?: RequestInit) => {
				const urlStr = typeof input === "string" ? input : input.toString();
				if (urlStr.endsWith("/api/v1/health")) {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}
				const hdrs = new Headers(init?.headers);
				remoteHeaders = Object.fromEntries(hdrs.entries());
				return Response.json({
					status: "ACCEPTED",
					proposal_id: "prop-01",
					target_sha256: "0000000000000000000000000000000000000000000000000000000000000000",
				});
			}) as typeof fetch,
		});
		const remoteRes = await remoteExec.execute("ontology", "studio proposal accept", {
			session: "sec-sess-01",
			proposal_id: "prop-01",
			base_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
			low_confidence_waiver: true,
			waiver_rationale: "Adequate rationale for bearer-authenticated remote admin.",
		});
		expect(remoteRes.exitCode).toBe(0);
		expect(remoteHeaders["authorization"]).toBe("Bearer valid-remote-admin-jwt");
	});

	it("Vector 1: Mismatched waiver key sent to server returns exit 7", async () => {
		process.env.OPENDV_WAIVER_KEY = "wrong-steward-secret-key";

		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"studio",
				"proposal",
				"accept",
				"-s",
				"sec-sess-01",
				"-p",
				"prop-01",
				"--base-sha256",
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
				"--low-confidence-waiver",
				"--waiver-rationale",
				"Adequate rationale for security compliance test.",
				"--api-url",
				serverUrl,
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");
	});

	it("Vector 2: Remote init unconditionally throws exit 7 (LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE)", async () => {
		// 1. standard init
		const outLines1: string[] = [];
		const errLines1: string[] = [];
		const code1 = await runCli(
			["ontology", "studio", "init", "--api-url", serverUrl, "--json"],
			(msg) => outLines1.push(msg),
			(msg) => errLines1.push(msg),
		);

		expect(code1).toBe(7);
		const err1 = JSON.parse(errLines1.join("\n"));
		expect(err1.error_code).toBe("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE");

		// 2. init with force
		const outLines2: string[] = [];
		const errLines2: string[] = [];
		const code2 = await runCli(
			["ontology", "studio", "init", "--force", "--api-url", serverUrl, "--json"],
			(msg) => outLines2.push(msg),
			(msg) => errLines2.push(msg),
		);

		expect(code2).toBe(7);
		const err2 = JSON.parse(errLines2.join("\n"));
		expect(err2.error_code).toBe("LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE");
	});

	it("Vector 3: Session ID path traversal characters are safely URI-encoded", async () => {
		const traversalSid = "../../escape/session";
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			["ontology", "studio", "status", "-s", traversalSid, "--api-url", serverUrl, "--json"],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(0);
	});
});
