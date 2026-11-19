/**
 * Unit Tests: Studio Multi-Format Export & Workspace Promotion Actions
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
	generateLocalExport,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

describe("Studio Export & Promotion Actions", () => {
	beforeEach(() => {
		studioStore.getState().reset();
	});

	it("generates valid serialization across all 5 supported export formats", () => {
		const nodes: ConceptNode[] = [
			{
				id: "c1",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_CONCEPT",
				definition: "Investment fund entity.",
				grounding_status: "GROUNDED",
				attributes: [
					{ attribute_name: "fund_id", data_type: "string", is_optional: false },
				],
				synonyms: ["Portfolio Fund"],
				external_mappings: [],
				x: 0,
				y: 0,
				confidence: 0.9,
			},
		];
		const edges: RelationshipEdge[] = [
			{
				id: "e1",
				relationship_uri: "urn:ontology:relationship:fund_investor",
				relationship_name: "FundInvestor",
				target_link: "lnk_fund_investor",
				source_concept_uri: "urn:ontology:entity:fund",
				target_concept_uri: "urn:ontology:entity:investor",
			},
		];
		const session: StudioSessionSummary = {
			session_id: "s1",
			domain_namespace: "finance",
			enterprise_owner: "Securities Co",
			status: "ACTIVE",
			active_version: "1.1.0",
			created_at: "2026-11-11T00:00:00Z",
			updated_at: "2026-11-11T00:00:00Z",
		};

		const yamlOut = generateLocalExport("yaml", "1.1.0", nodes, edges, session);
		expect(yamlOut).toContain("ontology_version:");
		expect(yamlOut).toContain("1.1.0");
		expect(yamlOut).toContain("canonical_business_concepts:");
		expect(yamlOut).toContain("canonical_relationships:");
		expect(yamlOut).toContain("Fund");

		const jsonOut = generateLocalExport("json", "1.1.0", nodes, edges, session);
		const parsed = JSON.parse(jsonOut);
		expect(parsed.ontology_version).toBe("1.1.0");
		expect(parsed.enterprise_owner).toBe("Securities Co");
		expect(parsed.canonical_business_concepts.length).toBeGreaterThan(0);
		expect(parsed.canonical_relationships.length).toBeGreaterThan(0);

		const ttlOut = generateLocalExport("turtle", "1.1.0", nodes, edges, session);
		expect(ttlOut).toContain("@prefix owl: <http://www.w3.org/2002/07/owl#>");
		expect(ttlOut).toContain("a owl:Class");

		const jsonldOut = generateLocalExport("jsonld", "1.1.0", nodes, edges, session);
		expect(jsonldOut).toContain('"@context"');
		expect(jsonldOut).toContain('"@graph"');

		const ntOut = generateLocalExport("ntriples", "1.1.0", nodes, edges, session);
		expect(ntOut).toContain("<http://www.w3.org/2002/07/owl#Class>");
		expect(ntOut).toContain("<urn:ontology:entity:fund>");
	});

	it("executes promoteWorkspace store action and updates head SHA and status", async () => {
		const initialHeadSha =
			"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81";
		const expectedWorkSha =
			"9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
		const promotedHeadSha =
			"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
		const newWorkspaceSha =
			"ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb";

		studioStore.setState({
			activeSession: {
				session_id: "ses-pe-002",
				domain_namespace: "private_equity",
				enterprise_owner: "Global Capital Partners",
				created_at: "2026-11-11T00:00:00Z",
				updated_at: "2026-11-11T00:00:00Z",
				status: "READY",
				active_version: "1.2.0",
				head_sha256: initialHeadSha,
				commits: [],
			},
			toasts: [],
		});

		const mockFetch = async () =>
			new Response(
				JSON.stringify({
					promoted_version: "1.2.1",
					workspace_sha256: newWorkspaceSha,
					head_sha256: promotedHeadSha,
				}),
				{ status: 200 },
			);

		const ok = await studioStore.getState().promoteWorkspace(
			{
				workspace: "workspaces/private_equity",
				workspace_sha256: expectedWorkSha,
				head_sha256: initialHeadSha,
				rationale:
					"Enterprise release promoting validated portfolio entities to main DV",
				acknowledge_historical_contract_warning: true,
			},
			mockFetch as unknown as typeof fetch,
		);

		expect(ok).toBe(true);
		const state = studioStore.getState();
		expect(state.activeSession?.commits?.length ?? 0).toBe(0);
		expect(state.activeSession?.status).toBe("PROMOTED");
		expect(state.activeSession?.head_sha256).toBe(promotedHeadSha);
		expect(
			state.toasts.some((t) => t.title === "Workspace Promotion Succeeded"),
		).toBe(true);
	});
});
