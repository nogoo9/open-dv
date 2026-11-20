import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createWorkbenchDevServer } from "../src/dev-server";
import {
	executeSubmitRun,
	executeValidate,
} from "../src/explorer/modals/useNewRunActions";
import { fetchPlatformConfig } from "../src/explorer/settings/usePlatformConfigActions";
import * as urlRouter from "../src/explorer/url-router";
import * as logsTypes from "../src/ontology/wizard/logs-types";

const WORKBENCH_ROOT = resolve(import.meta.dir, "..");

const FLAGGED_FILES = [
	"src/dev-server.ts",
	"src/explorer/OpenDVWorkbenchExplorer.tsx",
	"src/explorer/dv-schema/DvSchemaExplorerView.tsx",
	"src/explorer/dv-schema/DvVaultRowsAndSqlQueryTab.tsx",
	"src/explorer/modals/useNewRunActions.ts",
	"src/explorer/settings/usePlatformConfigActions.ts",
	"src/ontology/wizard/logs-types.ts",
] as const;

describe("CodeQL js/polynomial-redos URL normalization guard", () => {
	test("static guard: flagged TS/TSX files contain no trailing-slash regexes", () => {
		for (const relPath of FLAGGED_FILES) {
			const src = readFileSync(resolve(WORKBENCH_ROOT, relPath), "utf-8");
			expect(src).not.toContain("/\\/+$/");
			expect(src).not.toContain("/\\/api\\/v1\\/?$/");
		}
	});

	test("stripTrailingSlashes and normalizeApiBaseUrl handle edge cases identically", () => {
		const strip = (
			urlRouter as { stripTrailingSlashes?: (v: string) => string }
		).stripTrailingSlashes;
		const normRouter = (
			urlRouter as { normalizeApiBaseUrl?: (v?: string) => string }
		).normalizeApiBaseUrl;
		const normLogs = logsTypes.normalizeApiBaseUrl;

		expect(typeof strip).toBe("function");
		expect(typeof normRouter).toBe("function");
		expect(typeof normLogs).toBe("function");
		if (!strip || !normRouter) return;

		for (const fn of [normRouter, normLogs]) {
			expect(fn(undefined)).toBe("");
			expect(fn("")).toBe("");
			expect(fn("/")).toBe("");
			expect(fn("///")).toBe("");
			expect(fn("/api/v1")).toBe("");
			expect(fn("/api/v1/")).toBe("");
			expect(fn("http://127.0.0.1:8420")).toBe("http://127.0.0.1:8420");
			expect(fn("http://127.0.0.1:8420/")).toBe("http://127.0.0.1:8420");
			expect(fn("http://127.0.0.1:8420///")).toBe("http://127.0.0.1:8420");
			expect(fn("http://127.0.0.1:8420/api/v1")).toBe("http://127.0.0.1:8420");
			expect(fn("http://127.0.0.1:8420/api/v1/")).toBe("http://127.0.0.1:8420");
			expect(fn("http://127.0.0.1:8420//api/v1/")).toBe(
				"http://127.0.0.1:8420",
			);
			expect(fn("http://127.0.0.1:8420/api/v1///")).toBe(
				"http://127.0.0.1:8420/api/v1",
			);
		}
		expect(strip("/api/v1///")).toBe("/api/v1");
		expect(strip("")).toBe("");
		expect(strip("///")).toBe("");
	});

	test("adversarial 50k-slash ReDoS input finishes in < 50ms", () => {
		const strip = (
			urlRouter as { stripTrailingSlashes?: (v: string) => string }
		).stripTrailingSlashes;
		const normRouter = (
			urlRouter as { normalizeApiBaseUrl?: (v?: string) => string }
		).normalizeApiBaseUrl;
		expect(typeof strip).toBe("function");
		expect(typeof normRouter).toBe("function");
		if (!strip || !normRouter) return;

		const payload = `${"/".repeat(50_000)}a`;
		const t0 = performance.now();
		expect(strip(payload)).toBe(payload);
		expect(normRouter(payload)).toBe(payload);
		expect(performance.now() - t0).toBeLessThan(50);
	});

	test("call-site URL constructors emit normalized URLs", async () => {
		const requestedUrls: string[] = [];
		const mockFetch: typeof fetch = (async (input: RequestInfo | URL) => {
			requestedUrls.push(String(input));
			return new Response(
				JSON.stringify({
					passed: true,
					diagnostics: [],
					run_id: "r1",
					status_url: "/api/v1/runs/r1",
					events_sse_url: "/api/v1/runs/r1/events",
					harness: {
						provider: "agy",
						auth: { auth_mode: "cli" },
						model_overrides: {},
						agent_models: {},
					},
					providers: [],
					resolved_persona_models: {},
					web_ui_bundled: true,
				}),
				{ status: 200, headers: { "Content-Type": "application/json" } },
			);
		}) as typeof fetch;

		await executeValidate({
			specPath: "spec.yaml",
			contractPath: "",
			waveId: "w1",
			holdPolicy: "halt",
			apiUrl: "http://platform.local///",
			fetchImpl: mockFetch,
		});
		await executeSubmitRun({
			specPath: "spec.yaml",
			contractPath: "",
			waveId: "w1",
			holdPolicy: "halt",
			apiUrl: "http://platform.local///",
			fetchImpl: mockFetch,
		});
		await fetchPlatformConfig({
			apiUrl: "http://platform.local/api/v1/",
			fetchImpl: mockFetch,
		});

		expect(requestedUrls[0]).toBe(
			"http://platform.local/api/v1/pipelines/validate",
		);
		expect(requestedUrls[1]).toBe(
			"http://platform.local/api/v1/pipelines/runs",
		);
		expect(requestedUrls[2]).toBe(
			"http://platform.local/api/v1/platform/config",
		);
		expect(
			logsTypes.buildLogsSnapshotUrl("s1", {
				apiUrl: "http://platform.local/api/v1///",
			}),
		).toBe(
			"http://platform.local/api/v1/api/v1/ontology/studio/sessions/s1/logs?limit=500",
		);
		expect(
			logsTypes.buildLogsExportUrl("s1", "http://platform.local/api/v1/"),
		).toBe(
			"http://platform.local/api/v1/ontology/studio/sessions/s1/logs/export",
		);
	});

	test("dev-server normalizes proxy base URL and finishes 50k-slash adversarial apiUrl in < 50ms", async () => {
		const seenPaths: string[] = [];
		const upstream = Bun.serve({
			port: 0,
			hostname: "127.0.0.1",
			fetch(req) {
				const u = new URL(req.url);
				seenPaths.push(`${u.pathname}${u.search}`);
				return Response.json({ status: "ok" });
			},
		});

		const devNormalized = createWorkbenchDevServer({
			port: 0,
			apiUrl: `http://127.0.0.1:${upstream.port}///`,
			silent: true,
		});
		const devAdversarial = createWorkbenchDevServer({
			port: 0,
			apiUrl: `http://127.0.0.1:${upstream.port}/${"/".repeat(50_000)}a`,
			silent: true,
		});

		try {
			const resNorm = await fetch(
				`http://127.0.0.1:${devNormalized.port}/api/v1/health?probe=1`,
			);
			expect(resNorm.status).toBe(200);
			expect(seenPaths[0]).toBe("/api/v1/health?probe=1");

			const t0 = performance.now();
			const resAdv = await fetch(
				`http://127.0.0.1:${devAdversarial.port}/api/v1/health`,
			);
			const elapsedMs = performance.now() - t0;
			expect(resAdv.status).toBe(200);
			expect(elapsedMs).toBeLessThan(50);
		} finally {
			devNormalized.stop(true);
			devAdversarial.stop(true);
			upstream.stop(true);
		}
	});
});
