import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { existsSync, unlinkSync } from "node:fs";
import { runCli } from "../src/main";
import { loadCliSpec } from "../src/spec";

describe("opendvctl ontology command group", () => {
	const spec = loadCliSpec();
	let server: ReturnType<typeof Bun.serve>;
	let serverUrl: string;

	beforeAll(() => {
		server = Bun.serve({
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
					url.pathname === "/api/v1/ontology/suggestions" &&
					req.method === "POST"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					if (body.recommender_id === "unavailable") {
						return new Response(
							JSON.stringify({
								type: "urn:opendv:ontology-recommender-unavailable",
								title: "Ontology Recommender Unavailable",
								status: 422,
								detail: "No ontology recommender module is installed.",
							}),
							{
								status: 422,
								headers: { "Content-Type": "application/problem+json" },
							},
						);
					}
					return Response.json({
						proposal_id: "prop-ctl-01",
						rationale: "Suggested driver concept",
						confidence_score: 0.95,
						delta: {
							delta_id: "delta-ctl-01",
							operations: [
								{
									op_id: "op-1",
									op_type: "add_concept",
									concept_uri: "urn:ontology:entity:driver",
									concept_name: "Driver",
								},
							],
						},
					});
				}

				if (
					url.pathname === "/api/v1/ontology/validate-delta" &&
					req.method === "POST"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					const content = String(body.delta_content || "");
					const delta = (body.delta || {}) as Record<string, unknown>;
					if (
						delta.delta_id === "invalid-delta" ||
						content.includes("invalid-delta")
					) {
						return Response.json({
							valid: false,
							violations: ["ONT-EVO-002: Provisional URI forbidden."],
							diagnostics: [],
						});
					}
					return Response.json({
						valid: true,
						violations: [],
						diagnostics: ["Validation passed."],
					});
				}

				if (url.pathname === "/api/v1/ontology/diff" && req.method === "POST") {
					return Response.json({
						base_version: "1.0.0",
						target_version: "1.1.0",
						added_concepts: ["urn:ontology:entity:trailer"],
						added_relationships: [],
						added_enumerations: [],
						refined_concepts: [],
						breaking_changes: [],
					});
				}

				if (
					url.pathname === "/api/v1/ontology/decisions" &&
					req.method === "POST"
				) {
					const body = (await req.json()) as Record<string, unknown>;
					const action = String(body.action || "ACCEPT");
					return Response.json({
						status: "COMPLETED",
						decision: {
							decision_id: "dec-ctl-01",
							action,
							decided_by: body.decided_by || "steward",
						},
						resulting_ontology_yaml:
							action === "ACCEPT"
								? "ontology_version: 1.1.0\ndomain_namespace: logistics\n"
								: null,
						resulting_ontology_version: action === "ACCEPT" ? "1.1.0" : null,
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

	it("executes ontology suggest successfully and writes output", async () => {
		const outLines: string[] = [];
		const outputFile = `temp_suggest_output_${Date.now()}.json`;

		const code = await runCli(
			[
				"ontology",
				"suggest",
				"--api-url",
				serverUrl,
				"--output",
				outputFile,
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.proposal_id).toBe("prop-ctl-01");

		const fileContent = await Bun.file(outputFile).text();
		expect(JSON.parse(fileContent).proposal_id).toBe("prop-ctl-01");
		await Bun.file(outputFile).delete();
	});

	it("returns exit code 2 when recommender is unavailable", async () => {
		const outLines: string[] = [];
		const errLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"suggest",
				"--api-url",
				serverUrl,
				"--recommender-id",
				"unavailable",
				"--json",
			],
			(msg) => outLines.push(msg),
			(msg) => errLines.push(msg),
		);

		expect(code).toBe(2);
		const out = JSON.parse(errLines.join("\n"));
		expect(out.error_code).toBe("ONTOLOGY_RECOMMENDER_UNAVAILABLE");
	});

	it("executes ontology validate-delta with exit code 0 when valid", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"validate-delta",
				"--api-url",
				serverUrl,
				"--base",
				"examples/private_equity/pe_graph_a_ontology.yaml",
				"--delta",
				"examples/private_equity/pe_graph_a_ontology.yaml",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.valid).toBe(true);
	});

	it("executes ontology validate-delta with exit code 1 when delta is invalid", async () => {
		const outLines: string[] = [];
		// Write a temporary invalid delta
		const tmpDelta = `temp_invalid_${Date.now()}.json`;
		await Bun.write(
			tmpDelta,
			JSON.stringify({ delta_id: "invalid-delta", operations: [] }),
		);

		try {
			const code = await runCli(
				[
					"ontology",
					"validate-delta",
					"--api-url",
					serverUrl,
					"--base",
					"examples/private_equity/pe_graph_a_ontology.yaml",
					"--delta",
					tmpDelta,
					"--json",
				],
				(msg) => outLines.push(msg),
			);

			expect(code).toBe(1);
			const out = JSON.parse(outLines.join("\n"));
			expect(out.valid).toBe(false);
		} finally {
			if (existsSync(tmpDelta)) {
				unlinkSync(tmpDelta);
			}
		}
	});

	it("executes ontology diff with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"diff",
				"--api-url",
				serverUrl,
				"--base",
				"examples/private_equity/pe_graph_a_ontology.yaml",
				"--target",
				"examples/private_equity/pe_graph_a_ontology.yaml",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.added_concepts).toContain("urn:ontology:entity:trailer");
	});

	it("executes ontology accept with exit code 0 and outputs resulting yaml", async () => {
		const outLines: string[] = [];
		const outYaml = `temp_result_${Date.now()}.yaml`;

		try {
			const code = await runCli(
				[
					"ontology",
					"accept",
					"--api-url",
					serverUrl,
					"--base",
					"examples/private_equity/pe_graph_a_ontology.yaml",
					"--delta",
					"examples/private_equity/pe_graph_a_ontology.yaml",
					"--output",
					outYaml,
					"--json",
				],
				(msg) => outLines.push(msg),
			);

			expect(code).toBe(0);
			const out = JSON.parse(outLines.join("\n"));
			expect(out.decision.action).toBe("ACCEPT");

			const yamlContent = await Bun.file(outYaml).text();
			expect(yamlContent).toContain("ontology_version: 1.1.0");
		} finally {
			if (existsSync(outYaml)) {
				unlinkSync(outYaml);
			}
		}
	});

	it("executes ontology reject with exit code 0", async () => {
		const outLines: string[] = [];
		const code = await runCli(
			[
				"ontology",
				"reject",
				"--api-url",
				serverUrl,
				"--proposal-id",
				"prop-to-reject",
				"--json",
			],
			(msg) => outLines.push(msg),
		);

		expect(code).toBe(0);
		const out = JSON.parse(outLines.join("\n"));
		expect(out.decision.action).toBe("REJECT");
	});
});
