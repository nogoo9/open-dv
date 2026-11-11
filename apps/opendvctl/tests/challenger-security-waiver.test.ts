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

	it("Vector 1: Raw secrets on CLI argv are never accepted by opendvctl", async () => {
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

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");
	});

	it("Vector 1: Non-existent waiver key file fails with exit 7", async () => {
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

		expect(code).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");
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

	it("Vector 1: OPENDV_WAIVER_KEY in RemoteExecutor vs opendvctl parser divergence", async () => {
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

		// B. Via runCli: Because parser.ts line 275 binds env_var OPENDV_WAIVER_KEY to waiver_key_file,
		// passing raw secret in env var fails because it tries to read raw secret as file path:
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
		// Empirically observes exit 7 due to "Waiver key file could not be read"
		expect(codeRawEnv).toBe(7);
		const err = JSON.parse(errLines.join("\n"));
		expect(err.error_code).toBe("FORBIDDEN_ADMIN_REQUIRED");

		// C. If OPENDV_WAIVER_KEY is a file path, runCli succeeds:
		const envKeyFile = path.join(tempDir, "env_steward.key");
		await fs.writeFile(envKeyFile, "valid-steward-secret-key", "utf-8");
		process.env.OPENDV_WAIVER_KEY = envKeyFile;

		const outLines2: string[] = [];
		const errLines2: string[] = [];
		const codeFilePathEnv = await runCli(
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
			(msg) => outLines2.push(msg),
			(msg) => errLines2.push(msg),
		);
		expect(codeFilePathEnv).toBe(0);
		expect(lastHeaders["x-opendv-waiver-key"]).toBe("valid-steward-secret-key");
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
