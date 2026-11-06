import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { loadsJcs } from "../src/jcs";
import { runCli } from "../src/main";
import { loadCliSpec } from "../src/spec";

describe("opendvctl smoke & CLI execution", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;

	beforeAll(() => {
		// Mock Platform API server on dynamic ephemeral port
		server = Bun.serve({
			port: 0,
			fetch(req) {
				const url = new URL(req.url);

				if (url.pathname === "/api/v1/health") {
					return Response.json({
						status: "HEALTHY",
						version: "0.1.0",
						contract_version: spec.contract_version,
						mode: "PLATFORM_API",
					});
				}

				if (url.pathname === "/api/v1/compile" && req.method === "POST") {
					return Response.json({
						status: "COMPLETED",
						overall_passed: true,
						error_code: "OK",
						summary: "Compilation complete",
						rendered: {
							toon: "@toon_v1\nCOMPILE PASS\n4 zones generated",
							text: "=== COMPILE STATUS ===\n4 physical zones compiled successfully",
						},
					});
				}

				if (url.pathname === "/api/v1/verify" && req.method === "POST") {
					return Response.json({
						status: "COMPLETED",
						overall_passed: true,
						verdict: "PASS",
						error_code: "OK",
						rendered: {
							toon: "@toon_v1\nG0..G5 PASS",
							text: "Scorecard: 6 Gates PASS",
						},
					});
				}

				return new Response("Not found", { status: 404 });
			},
		});

		serverUrl = `http://127.0.0.1:${server.port}`;
	});

	afterAll(() => {
		server.stop();
	});

	it("executes without arguments and prints usage with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli([], (msg) => outLines.push(msg));
		expect(code).toBe(0);
		expect(outLines.join("\n")).toContain("Usage: opendvctl");
		expect(outLines.join("\n")).toContain("Commands:");
	});

	it("executes --help and prints usage with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(["--help"], (msg) => outLines.push(msg));
		expect(code).toBe(0);
		expect(outLines.join("\n")).toContain("Usage: opendvctl");
	});

	it("executes --version and prints version with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(["--version"], (msg) => outLines.push(msg));
		expect(code).toBe(0);
		expect(outLines.join("\n")).toContain(`opendvctl ${spec.spec_version}`);
		expect(outLines.join("\n")).toContain(`contract ${spec.contract_version}`);
	});

	it("executes --dump-cli-tree and prints canonical JCS tree with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(["--dump-cli-tree"], (msg) => outLines.push(msg));
		expect(code).toBe(0);
		const treeJson = outLines.join("\n");
		expect(treeJson.startsWith("{")).toBe(true);
		const parsed = loadsJcs<{ contract_version: string }>(treeJson);
		expect(parsed.contract_version).toBe(spec.contract_version);
	});

	it("exits with code 2 and error message when --api-url is missing", async () => {
		const errLines: string[] = [];
		const prevEnv = process.env.OPENDV_API_URL;
		process.env.OPENDV_API_URL = "";

		try {
			const code = await runCli(
				["compile", "-c", "contract.yaml"],
				undefined,
				(msg) => errLines.push(msg),
			);
			expect(code).toBe(2);
			expect(errLines.join("\n")).toContain("Missing required --api-url");
		} finally {
			if (prevEnv) process.env.OPENDV_API_URL = prevEnv;
		}
	});

	it("exits with code 2 and canonical JCS in --json mode when --api-url is missing", async () => {
		const errLines: string[] = [];
		const prevEnv = process.env.OPENDV_API_URL;
		process.env.OPENDV_API_URL = "";

		try {
			const code = await runCli(
				["compile", "-c", "contract.yaml", "--json"],
				undefined,
				(msg) => errLines.push(msg),
			);
			expect(code).toBe(2);
			const errOut = errLines.join("\n");
			const parsed = loadsJcs<{ status: string; error_code: string }>(errOut);
			expect(parsed.status).toBe("ERROR");
			expect(parsed.error_code).toBe("INVALID_REQUEST");
		} finally {
			if (prevEnv) process.env.OPENDV_API_URL = prevEnv;
		}
	});

	it("executes compile against server and prints pre-rendered text", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"compile",
				"-c",
				"contract.yaml",
				"--api-url",
				serverUrl,
				"--format",
				"text",
			],
			(msg) => outLines.push(msg),
		);
		expect(code).toBe(0);
		expect(outLines.join("\n")).toContain("=== COMPILE STATUS ===");
		expect(outLines.join("\n")).toContain("4 physical zones compiled");
	});

	it("executes compile with --toon and prints pre-rendered diagnostic trace", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			["compile", "-c", "contract.yaml", "--api-url", serverUrl, "--toon"],
			(msg) => outLines.push(msg),
		);
		expect(code).toBe(0);
		expect(outLines.join("\n")).toContain("@toon_v1");
		expect(outLines.join("\n")).toContain("COMPILE PASS");
	});

	it("executes compile with --json and prints canonical JCS", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			["compile", "-c", "contract.yaml", "--api-url", serverUrl, "--json"],
			(msg) => outLines.push(msg),
		);
		expect(code).toBe(0);
		const jsonOut = outLines.join("\n");
		const parsed = loadsJcs<{ status: string; overall_passed: boolean }>(
			jsonOut,
		);
		expect(parsed.status).toBe("COMPLETED");
		expect(parsed.overall_passed).toBe(true);
	});
});
