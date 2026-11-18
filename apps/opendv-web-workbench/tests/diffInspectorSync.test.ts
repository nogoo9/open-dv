import { describe, expect, it } from "bun:test";
import { executeSyncOntologyDelta } from "../src/explorer/builder/diffInspectorSync";

describe("diffInspectorSync (R4-C1)", () => {
	it("deduplicates only add_concept and add_relationship against live session ontology, never dropping add_attribute", async () => {
		let capturedDeltaBody: any = null;
		let capturedDeltaHeaders: Record<string, string> = {};

		const mockFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.includes("/export?export_format=json")) {
				return new Response(
					JSON.stringify({
						canonical_business_concepts: [
							{
								concept_uri: "urn:ontology:entity:instrument",
								target_hub: "hub_instrument",
								concept_name: "Instrument",
							},
						],
						canonical_relationships: [
							{
								relationship_uri: "urn:ontology:relationship:traded_on",
								relationship_name: "traded_on",
							},
						],
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			if (url.includes("/delta") && init?.method === "POST") {
				capturedDeltaBody = JSON.parse(String(init.body));
				capturedDeltaHeaders = (init.headers as Record<string, string>) || {};
				return new Response(JSON.stringify({ ok: true }), { status: 200 });
			}
			if (url.includes("/ontology/studio/sessions/")) {
				return new Response(JSON.stringify({ head_sha256: "abc123sha" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			return new Response("Not found", { status: 404 });
		};

		const operations = [
			// add_concept that ALREADY exists -> should be filtered out
			{
				op_type: "add_concept",
				concept_uri: "urn:ontology:entity:instrument",
				target_hub: "hub_instrument",
			},
			// add_relationship that ALREADY exists -> should be filtered out
			{
				op_type: "add_relationship",
				relationship_uri: "urn:ontology:relationship:traded_on",
			},
			// add_attribute for the existing concept -> MUST NOT be filtered out!
			{
				op_type: "add_attribute",
				concept_uri: "urn:ontology:entity:instrument",
				attribute_name: "isin",
				data_type: "string",
			},
			// new add_concept -> should be kept
			{
				op_type: "add_concept",
				concept_uri: "urn:ontology:entity:exchange",
				target_hub: "hub_exchange",
			},
		];

		const res = await executeSyncOntologyDelta({
			sessionId: "session-101",
			apiUrl: "http://127.0.0.1:8000",
			fetchImpl: mockFetch as any,
			operations,
		});

		expect(res.success).toBe(true);
		expect(capturedDeltaBody).not.toBeNull();
		const ops = capturedDeltaBody.operations;
		expect(ops).toHaveLength(2);

		// First is the add_attribute for instrument
		expect(ops[0].op_type).toBe("add_attribute");
		expect(ops[0].concept_uri).toBe("urn:ontology:entity:instrument");
		expect(ops[0].attribute_name).toBe("isin");

		// Second is the new add_concept for exchange
		expect(ops[1].op_type).toBe("add_concept");
		expect(ops[1].concept_uri).toBe("urn:ontology:entity:exchange");
		expect(ops[1].target_hub).toBe("hub_exchange");
	});

	it("never reports synced when 0 ops were POSTed (fails closed when all operations already exist)", async () => {
		const mockFetch = async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes("/export?export_format=json")) {
				return new Response(
					JSON.stringify({
						canonical_business_concepts: [
							{
								concept_uri: "urn:ontology:entity:instrument",
								target_hub: "hub_instrument",
							},
						],
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			return new Response(JSON.stringify({ head_sha256: "abc123sha" }), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			});
		};

		const operations = [
			{
				op_type: "add_concept",
				concept_uri: "urn:ontology:entity:instrument",
				target_hub: "hub_instrument",
			},
		];

		const res = await executeSyncOntologyDelta({
			sessionId: "session-101",
			apiUrl: "http://127.0.0.1:8000",
			fetchImpl: mockFetch as any,
			operations,
		});

		expect(res.success).toBe(false);
		expect(res.error).toContain("No new operations to sync");
	});

	it("preserves >= 8-word grain_semantics on synced add_relationship operations", async () => {
		let capturedDeltaBody: any = null;
		const mockFetch = async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.includes("/export?export_format=json")) {
				return new Response(
					JSON.stringify({
						canonical_business_concepts: [],
						canonical_relationships: [],
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			if (url.includes("/delta") && init?.method === "POST") {
				capturedDeltaBody = JSON.parse(String(init.body));
				return new Response(JSON.stringify({ ok: true }), { status: 200 });
			}
			return new Response(JSON.stringify({ head_sha256: "abc123sha" }), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			});
		};

		const customGrain =
			"Each custody holding associates one account with one financial security.";
		const res = await executeSyncOntologyDelta({
			sessionId: "session-grain",
			apiUrl: "http://127.0.0.1:8000",
			fetchImpl: mockFetch as any,
			operations: [
				{
					op_type: "add_relationship",
					relationship_uri: "urn:ontology:relationship:custody_security",
					target_link: "lnk_custody_security",
					endpoints: ["urn:ontology:entity:custody_account", "urn:ontology:entity:security"],
					grain_semantics: customGrain,
				},
				{
					op_type: "add_relationship",
					relationship_uri: "urn:ontology:relationship:position_security",
					target_link: "lnk_position_security",
					endpoints: ["urn:ontology:entity:position", "urn:ontology:entity:security"],
				},
			],
		});

		expect(res.success).toBe(true);
		const ops = capturedDeltaBody.operations;
		expect(ops).toHaveLength(2);
		expect(ops[0].grain_semantics).toBe(customGrain);
		expect(ops[1].grain_semantics.split(/\s+/).length).toBeGreaterThanOrEqual(8);
	});
});
