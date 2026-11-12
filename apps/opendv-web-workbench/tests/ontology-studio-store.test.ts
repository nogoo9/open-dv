/**
 * Unit & Integration Tests: Web Workbench SPA Studio Store (Task 5D.1, #222)
 * (apps/opendv-web-workbench/tests/ontology-studio-store.test.ts)
 */

import { describe, expect, it } from "bun:test";
import goldenFixture from "../../../packages/opendv-contracts/schemas/v1/fixtures/graph_a_golden.json";
import { isOntologyRoute } from "../src/ontology/route-utils";
import {
	canonicalizeGraphADict,
	computeQualityScorecard,
	createDefaultDiffState,
	createDefaultLayoutState,
	createDefaultParsingState,
	createDefaultQualityScorecard,
	createStudioStore,
	dumpsJcs,
	generateLocalExport,
	isSupportedSourceFile,
	recomputeScorecard,
	selectFilteredNodes,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

describe("Task 5D.1: Web Workbench Studio Store (/ontology)", () => {
	describe("1. Initial State & Defaults", () => {
		it("initializes default state with local_standalone mode and editor role", () => {
			const store = createStudioStore();
			const state = store.getState();

			expect(state.activeMode).toBe("local_standalone");
			expect(state.userRole).toBe("editor");
			expect(state.activeSession).toBeNull();
			expect(state.sessions).toEqual([]);
			expect(state.sources).toEqual([]);
			expect(state.nodes).toEqual([]);
			expect(state.edges).toEqual([]);
			expect(state.toasts).toEqual([]);
			expect(state.layout.lodTier).toBe(2);
			expect(state.layout.leftDrawerTab).toBe("sessions");
		});

		it("creates default quality scorecard and computes real quality rules", () => {
			const scDefault = createDefaultQualityScorecard();
			expect(scDefault.grounding_score).toBe(0);
			expect(scDefault.deployed_contracts_ok).toBe(false);
			expect(scDefault.checks.length).toBe(0);

			const sampleNode: ConceptNode = {
				id: "c1",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_CONCEPT",
				definition: "Investment fund entity.",
				grounding_status: "GROUNDED",
				attributes: [
					{
						attribute_name: "fund_id",
						data_type: "string",
						is_optional: false,
					},
				],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:Fund",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
			};
			const scComputed = computeQualityScorecard(
				[sampleNode],
				[],
				["urn:ontology:entity:fund"],
			);
			expect(scComputed.grounding_score).toBe(1.0);
			expect(scComputed.deployed_contracts_ok).toBe(true);
			expect(scComputed.dag_acyclic).toBe(true);
			expect(scComputed.checks.length).toBe(4);
			expect(scComputed.checks.map((c) => c.rule_id)).toEqual([
				"ONT-EVO-001",
				"ONT-EVO-002",
				"ONT-DAG-001",
				"YAGNI-001",
			]);
		});

		it("evaluates grounding_score 0.0 and ONT-EVO-002 FAIL when a concept claims GROUNDED with empty external_mappings (MAJ-01)", () => {
			const ungroundedClaimant: ConceptNode = {
				id: "c-spoofed",
				concept_uri: "urn:ontology:entity:spoofed",
				concept_name: "Spoofed",
				elm_archetype: "CORE_CONCEPT",
				definition: "Entity claiming grounded without mappings.",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};
			const sc = computeQualityScorecard([ungroundedClaimant], []);
			expect(sc.grounding_score).toBe(0.0);
			const evoCheck = sc.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("FAIL");
			expect(evoCheck?.details).toContain("violates ONT-EVO-002");
		});

		it("validates supported source file MIME types and extensions", () => {
			expect(isSupportedSourceFile("report.pdf", "application/pdf")).toBe(true);
			expect(
				isSupportedSourceFile(
					"deal.docx",
					"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
				),
			).toBe(true);
			expect(isSupportedSourceFile("model.json", "application/json")).toBe(
				true,
			);
			expect(isSupportedSourceFile("data.csv", "text/csv")).toBe(true);
			expect(isSupportedSourceFile("notes.md", "text/markdown")).toBe(true);
			expect(isSupportedSourceFile("data.xlsx", "")).toBe(true);
			expect(
				isSupportedSourceFile("virus.exe", "application/x-msdownload"),
			).toBe(false);
			expect(isSupportedSourceFile("script.sh", "text/x-sh")).toBe(false);
		});
	});

	describe("2. Session Management", () => {
		it("loads sessions list via mock fetch", async () => {
			const mockSessions = [
				{
					session_id: "s1",
					domain_namespace: "wealth",
					enterprise_owner: "Global Wealth",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "abc123",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
				},
			];

			const mockFetch = async (input: RequestInfo | URL) => {
				const url = String(input);
				if (url.includes("/api/v1/ontology/studio/sessions")) {
					return new Response(JSON.stringify(mockSessions), { status: 200 });
				}
				return new Response("Not Found", { status: 404 });
			};

			const store = createStudioStore();
			await store.getState().loadSessions(mockFetch as unknown as typeof fetch);

			expect(store.getState().sessions.length).toBe(1);
			expect(store.getState().sessions[0]?.session_id).toBe("s1");
			expect(store.getState().activeSession?.session_id).toBe("s1");
		});

		it("creates a new session and prepends to sessions list", async () => {
			const mockCreated = {
				session_id: "pe-new",
				domain_namespace: "private_equity",
				enterprise_owner: "Apollo Capital",
				status: "ACTIVE",
				active_version: "0.0.0",
				head_sha256: "0000",
				created_at: "2026-11-11T00:00:00Z",
				updated_at: "2026-11-11T00:00:00Z",
			};

			const mockFetch = async () => {
				return new Response(JSON.stringify(mockCreated), { status: 201 });
			};

			const store = createStudioStore();
			const result = await store
				.getState()
				.createSession(
					{ session_id: "pe-new", domain_namespace: "private_equity" },
					mockFetch as unknown as typeof fetch,
				);

			expect(result).not.toBeNull();
			expect(result?.session_id).toBe("pe-new");
			expect(store.getState().activeSession?.session_id).toBe("pe-new");
			expect(store.getState().sessions.length).toBe(1);
		});
	});

	describe("3. Sources & Ingestion Actions", () => {
		it("rejects unsupported file formats and adds error toast", async () => {
			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			const res = await store.getState().uploadSource({
				name: "bad.exe",
				size: 1024,
				type: "application/x-dosexec",
			});

			expect(res).toBeNull();
			expect(store.getState().toasts.length).toBe(1);
			expect(store.getState().toasts[0]?.code).toBe("SYNTAX_INVALID_ARGUMENT");
		});

		it("uploads supported PDF source and updates sources state", async () => {
			const mockDoc = {
				source_id: "src-101",
				source_sha256: "feedbeef",
				file_name: "prospectus.pdf",
				size_bytes: 4096,
				mime_type: "application/pdf",
				do_not_send_to_agent: false,
			};

			const mockFetch = async () => {
				return new Response(JSON.stringify(mockDoc), { status: 201 });
			};

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			const uploaded = await store.getState().uploadSource(
				{
					name: "prospectus.pdf",
					size: 4096,
					type: "application/pdf",
					content: "%PDF-1.4",
				},
				false,
				mockFetch as unknown as typeof fetch,
			);

			expect(uploaded).not.toBeNull();
			expect(uploaded?.source_id).toBe("src-101");
			expect(store.getState().sources.length).toBe(1);
			expect(store.getState().sources[0]?.file_name).toBe("prospectus.pdf");
		});

		it("manages parse lifecycle and progress tracking", async () => {
			const mockFetch = async () => {
				return new Response(JSON.stringify({ job_id: "job-p1" }), {
					status: 202,
				});
			};

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			const jobId = await store
				.getState()
				.startParse("auto", mockFetch as unknown as typeof fetch);
			expect(jobId).toBe("job-p1");
			expect(store.getState().parsingState.status).toBe("parsing");
			expect(store.getState().parsingState.progress_pct).toBe(35);

			// update progress
			store.getState().updateParsingProgress({
				status: "completed",
				progress_pct: 100,
				evidence_chunks: [
					{
						chunk_id: "chk-01",
						source_id: "src-101",
						source_file: "prospectus.pdf",
						text_snippet: "LP Capital Commitment Agreement terms...",
						token_count: 320,
						layout_complexity: "TABULAR",
					},
				],
			});

			expect(store.getState().parsingState.status).toBe("completed");
			expect(store.getState().parsingState.evidence_chunks.length).toBe(1);
			expect(
				store.getState().parsingState.evidence_chunks[0]?.layout_complexity,
			).toBe("TABULAR");
		});
	});

	describe("4. Clarification Q&A & Answer Persistence", () => {
		it("submits clarification answer and updates answered status", async () => {
			const mockFetch = async () => {
				return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
			};

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			// Seed a pending question
			store.setState({
				clarifications: [
					{
						question_id: "q-101",
						concept_name: "Fund Advisor",
						question_text:
							"Does Fund Advisor map to InvestmentAdvisor or FundManager?",
						certainty: "HIGH",
						rationale_options: [
							{
								id: "opt-1",
								label: "InvestmentAdvisor",
								description: "SEC Registered RIA",
							},
							{
								id: "opt-2",
								label: "FundManager",
								description: "Operational Manager",
							},
						],
						evidence_citations: [
							{
								source_file: "prospectus.pdf",
								chunk_id: "chk-01",
								excerpt: "advisor operates...",
							},
						],
						status: "PENDING",
					},
				],
			});

			const ok = await store.getState().submitAnswer(
				{
					question_id: "q-101",
					selected_option_ids: ["opt-1"],
					custom_answer_text: "Registered under SEC Rule 203",
				},
				undefined,
				mockFetch as unknown as typeof fetch,
			);

			expect(ok).toBe(true);
			const updatedQ = store.getState().clarifications[0];
			expect(updatedQ?.status).toBe("ANSWERED");
			expect(updatedQ?.selected_option_ids).toEqual(["opt-1"]);
			expect(store.getState().answers["q-101"]?.custom_answer_text).toBe(
				"Registered under SEC Rule 203",
			);
		});
	});

	describe("5. Graph State & Manipulation", () => {
		it("loads graph concepts and relationships from ontology export", async () => {
			const mockOntology = {
				canonical_business_concepts: [
					{
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Investment fund entity.",
						attributes: [
							{
								attribute_name: "fund_id",
								data_type: "string",
								is_optional: false,
							},
						],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo-fbc:Fund",
								predicate: "skos:exactMatch",
								confidence: 0.92,
							},
						],
					},
					{
						concept_uri: "urn:ontology:entity:investor",
						concept_name: "Investor",
						elm_archetype: "CORE_CONCEPT",
						definition: "Limited partner investor.",
						attributes: [],
						external_mappings: [],
					},
				],
				canonical_relationships: [
					{
						relationship_uri: "urn:ontology:relationship:fund_investor",
						relationship_name: "FundInvestor",
						target_link: "lnk_fund_investor",
						endpoints: [
							"urn:ontology:entity:fund",
							"urn:ontology:entity:investor",
						],
					},
				],
			};

			const mockFetch = async () => {
				return new Response(JSON.stringify(mockOntology), { status: 200 });
			};

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			await store
				.getState()
				.loadGraph("1.0.0", mockFetch as unknown as typeof fetch);

			expect(store.getState().nodes.length).toBe(2);
			expect(store.getState().edges.length).toBe(1);
			expect(store.getState().nodes[0]?.concept_name).toBe("Fund");
			expect(store.getState().nodes[0]?.grounding_status).toBe("GROUNDED");
			expect(store.getState().nodes[1]?.concept_name).toBe("Investor");
			expect(store.getState().nodes[1]?.grounding_status).toBe("UNGROUNDED");
			expect(store.getState().edges[0]?.target_link).toBe("lnk_fund_investor");
		});

		it("selects node and updates concept attributes", () => {
			const store = createStudioStore();
			store.setState({
				nodes: [
					{
						id: "urn:ontology:entity:fund",
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Initial def",
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [
							{ scheme: "fibo", target_uri: "fibo:Fund", confidence: 0.95 },
						],
					},
				],
			});

			store.getState().selectNode("urn:ontology:entity:fund");
			expect(store.getState().selectedNodeId).toBe("urn:ontology:entity:fund");
			expect(store.getState().layout.rightDrawerOpen).toBe(true);

			store.getState().updateConcept("urn:ontology:entity:fund", {
				definition: "Updated def",
				synonyms: ["Private Equity Fund"],
				id: "should_be_ignored",
				concept_uri: "should_be_ignored",
			} as unknown as Partial<ConceptNode>);

			const updated = store.getState().nodes[0];
			expect(updated?.id).toBe("urn:ontology:entity:fund");
			expect(updated?.concept_uri).toBe("urn:ontology:entity:fund");
			expect(updated?.definition).toBe("Updated def");
			expect(updated?.synonyms).toEqual(["Private Equity Fund"]);
		});

		it("rejects duplicate concept and relationship URIs", () => {
			const store = createStudioStore();
			const node: ConceptNode = {
				id: "urn:ontology:entity:fund",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_CONCEPT",
				definition: "Initial def",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:Fund", confidence: 0.95 },
				],
			};
			store.getState().addConcept(node);
			expect(store.getState().nodes.length).toBe(1);

			// Attempt duplicate
			store.getState().addConcept(node);
			expect(store.getState().nodes.length).toBe(1);
			expect(store.getState().toasts[0]?.code).toBe("ONT_EVO_DUPLICATE_URI");
		});
	});

	describe("6. Visual Diff, Quality & Promotion Actions", () => {
		it("computes visual diff records and switches diff mode", () => {
			const store = createStudioStore();
			store.setState({
				nodes: [
					{
						id: "urn:ontology:entity:deal",
						concept_uri: "urn:ontology:entity:deal",
						concept_name: "Deal",
						elm_archetype: "CORE_CONCEPT",
						definition: "M&A deal.",
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [
							{ scheme: "fibo", target_uri: "fibo:Deal", confidence: 0.95 },
						],
					},
				],
			});

			store.getState().computeDiff("1.0.0", "1.1.0");
			const diff = store.getState().diffState;
			expect(diff.active).toBe(true);
			expect(diff.mode).toBe("unified");
			expect(diff.records.length).toBe(1);
			expect(diff.records[0]?.change_type).toBe("added");
			expect(diff.summary.added_count).toBe(1);
			expect(diff.summary.breaking_count).toBe(0);

			store.getState().setDiffMode("side-by-side");
			expect(store.getState().diffState.mode).toBe("side-by-side");
		});

		it("enforces waiver rationale >= 10 characters", async () => {
			const validHeadSha =
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81";
			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: validHeadSha,
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			store.setState({
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:provisional:entity",
						concept_name: "Provisional Entity",
						elm_archetype: "CORE_CONCEPT",
						definition: "Pending regulatory mapping",
						grounding_status: "UNGROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
			});

			const spyFetch = (() => {
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// 1. Short rationale rejected before fetch
			const failRes = await store
				.getState()
				.submitWaiver("too short", "wk-123", spyFetch);
			expect(failRes).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_GROUNDING_WAIVER");

			// 2. Empty credential rejected before fetch (MIN-02)
			const failKey = await store
				.getState()
				.submitWaiver("Legitimate rationale for audit", "", spyFetch);
			expect(failKey).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"MISSING_WAIVER_CREDENTIAL",
			);

			// 3. Server 403 authorization rejection (MIN-02)
			const rejectFetch = async () =>
				new Response(
					JSON.stringify({
						detail: "Invalid steward credential",
						code: "FORBIDDEN_ADMIN_REQUIRED",
					}),
					{ status: 403 },
				);
			const rejected = await store
				.getState()
				.submitWaiver(
					"Validated via corporate compliance audit report",
					"invalid-credential",
					rejectFetch as unknown as typeof fetch,
				);
			expect(rejected).toBe(false);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(false);

			// 4. Valid rationale and credential with authoritative server record (MIN-02)
			const mockFetch = async () =>
				new Response(
					JSON.stringify({
						status: "authorized",
						waiver_applied: true,
						waived_base_sha256: validHeadSha,
						waived_concept_uris: ["urn:provisional:entity"],
					}),
					{ status: 200 },
				);
			const okRes = await store
				.getState()
				.submitWaiver(
					"Validated via corporate compliance audit report",
					"steward-confidential-credential-secret-42",
					mockFetch as unknown as typeof fetch,
				);
			expect(okRes).toBe(true);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(true);
			expect(store.getState().sessionWaivers["s1"]?.waived_base_sha256).toBe(
				validHeadSha,
			);
			expect(
				store.getState().sessionWaivers["s1"]?.waived_concept_uris,
			).toEqual(["urn:provisional:entity"]);

			// 5. Security invariant: credential must NEVER leak into store state
			expect(JSON.stringify(store.getState().qualityScorecard)).not.toContain(
				"steward-confidential-credential-secret-42",
			);
			expect(JSON.stringify(store.getState().sessionWaivers)).not.toContain(
				"steward-confidential-credential-secret-42",
			);
		});

		it("rejects 200 response when server denies waiver with waiver_applied=false (MIN-02)", async () => {
			const validHeadSha =
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81";
			const store = createStudioStore({
				initialSession: {
					session_id: "s-denied",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: validHeadSha,
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});
			store.setState({
				nodes: [
					{
						id: "un1",
						concept_uri: "urn:concept:un1",
						concept_name: "Ungrounded Concept",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Needs waiver",
						grounding_status: "UNGROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
			});

			const deniedFetch = async () =>
				new Response(
					JSON.stringify({
						status: "denied",
						waiver_applied: false,
						detail: "Administrative waiver denied by enterprise policy.",
						code: "WAIVER_DENIED",
					}),
					{ status: 200 },
				);

			const ok = await store
				.getState()
				.submitWaiver(
					"Legitimate rationale but denied by policy",
					"steward-token",
					deniedFetch as unknown as typeof fetch,
				);
			expect(ok).toBe(false);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(false);
			expect(store.getState().sessionWaivers["s-denied"]).toBeUndefined();
			expect(store.getState().toasts[0]?.code).toBe("WAIVER_DENIED");
		});

		it("enforces promotion rationale >= 10 characters and OCC verification (MIN-06, MIN-07)", async () => {
			const validHeadSha =
				"1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81";
			const validWorkspaceSha =
				"1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9";
			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: validHeadSha,
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			const spyFetch = (() => {
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// 1. Short rationale rejected before fetch
			const failRes = await store.getState().promoteWorkspace(
				{
					workspace_sha256: validWorkspaceSha,
					head_sha256: validHeadSha,
					rationale: "short",
				},
				spyFetch,
			);
			expect(failRes).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"PROMOTION_RATIONALE_REQUIRED",
			);

			// 2. Malformed SHA rejected before fetch (MIN-07)
			const failSha = await store.getState().promoteWorkspace(
				{
					workspace_sha256: "not_a_hex_digest",
					head_sha256: validHeadSha,
					rationale: "Enterprise private equity ontology release v1.0.0",
				},
				spyFetch,
			);
			expect(failSha).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_SHA256_DIGEST");

			// 3. Valid rationale with OCC header capture (MIN-06)
			const captured: {
				url?: string;
				headers?: Record<string, string>;
				body?: { rationale: string; head_sha256: string };
			} = {};

			const mockFetch: typeof fetch = (async (
				url: string,
				init?: RequestInit,
			) => {
				captured.url = url;
				captured.headers = (init?.headers as Record<string, string>) || {};
				captured.body = JSON.parse(init?.body as string);
				return new Response(
					JSON.stringify({ status: "promoted", head_sha256: validHeadSha }),
					{ status: 200 },
				);
			}) as typeof fetch;

			const okRes = await store.getState().promoteWorkspace(
				{
					workspace_sha256: validWorkspaceSha,
					head_sha256: validHeadSha,
					rationale: "Enterprise private equity ontology release v1.0.0",
				},
				mockFetch,
			);
			expect(okRes).toBe(true);
			expect(captured.url).toContain(
				"/api/v1/ontology/studio/sessions/s1/promote",
			);
			expect(captured.headers?.["If-Match"]).toBe(`"${validHeadSha}"`);
			expect(captured.headers?.["X-OpenDV-Workspace-If-Match"]).toBe(
				`"${validWorkspaceSha}"`,
			);
			expect(captured.body?.rationale).toContain(
				"Enterprise private equity ontology release",
			);
		});

		it("manages toast notifications adding and dismissing by ID", () => {
			const store = createStudioStore();
			store.getState().addToast({
				type: "urn:opendv:notice:test",
				title: "Test Notice",
				status: 200,
				detail: "Detailed notification",
				code: "OK",
			});

			expect(store.getState().toasts.length).toBe(1);
			const toastId = store.getState().toasts[0]?.id;
			expect(toastId).toBeDefined();

			if (toastId) {
				store.getState().dismissToast(toastId);
				expect(store.getState().toasts.length).toBe(0);
			}
		});
	});

	describe("7. Edge Cases, Race Protection & Route Utilities", () => {
		it("evaluates isOntologyRoute matching /ontology paths correctly", () => {
			expect(isOntologyRoute()).toBe(false);
			const originalLocation = globalThis.location;
			try {
				(globalThis as unknown as { location: { pathname: string } }).location =
					{ pathname: "/ontology" };
				expect(isOntologyRoute()).toBe(true);
				(globalThis as unknown as { location: { pathname: string } }).location =
					{ pathname: "/ontology/pe-session" };
				expect(isOntologyRoute()).toBe(true);
				(globalThis as unknown as { location: { pathname: string } }).location =
					{ pathname: "/runs/run-123" };
				expect(isOntologyRoute()).toBe(false);
			} finally {
				(globalThis as unknown as { location: unknown }).location =
					originalLocation;
			}
		});

		it("prevents stale session data race conditions when switching sessions rapidly", async () => {
			const store = createStudioStore({
				initialSession: {
					session_id: "session-a",
					domain_namespace: "ns-a",
					enterprise_owner: "owner-a",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			// Start slow load for session-a
			let resolveSlow: (v: unknown) => void = () => {};
			const slowPromise = new Promise((resolve) => {
				resolveSlow = resolve;
			});

			const mockFetch = async (input: RequestInfo | URL) => {
				const url = String(input);
				if (url.includes("session-a")) {
					await slowPromise;
					return new Response(
						JSON.stringify({
							concepts: [{ concept_uri: "urn:a", concept_name: "A" }],
							canonical_relationships: [],
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				if (url.includes("session-b")) {
					if (url.includes("/export")) {
						return new Response(
							JSON.stringify({
								canonical_business_concepts: [
									{
										concept_uri: "urn:b",
										concept_name: "B",
										elm_archetype: "CORE_BUSINESS_CONCEPT",
										external_mappings: [
											{
												scheme: "fibo",
												target_uri: "fibo:B",
												confidence: 0.95,
											},
										],
									},
								],
								canonical_relationships: [],
							}),
							{ status: 200, headers: { "Content-Type": "application/json" } },
						);
					}
					if (url.includes("/sources") || url.includes("/questions")) {
						return new Response(JSON.stringify([]), {
							status: 200,
							headers: { "Content-Type": "application/json" },
						});
					}
					return new Response(
						JSON.stringify({
							session_id: "session-b",
							domain_namespace: "ns-b",
							enterprise_owner: "owner-b",
							status: "ACTIVE",
							active_version: "2.0.0",
							created_at: "2026-11-11",
							updated_at: "2026-11-11",
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response("Not Found", { status: 404 });
			};

			store.setState({
				sessions: [
					{
						session_id: "session-a",
						domain_namespace: "ns-a",
						enterprise_owner: "owner-a",
						status: "ACTIVE",
						active_version: "1.0.0",
						created_at: "2026-11-11",
						updated_at: "2026-11-11",
					},
					{
						session_id: "session-b",
						domain_namespace: "ns-b",
						enterprise_owner: "owner-b",
						status: "ACTIVE",
						active_version: "2.0.0",
						created_at: "2026-11-11",
						updated_at: "2026-11-11",
					},
				],
			});

			const p1 = store
				.getState()
				.loadGraph("1.0.0", mockFetch as unknown as typeof fetch, "session-a");
			await store
				.getState()
				.selectSession("session-b", mockFetch as unknown as typeof fetch);

			// Now let slow session-a finish
			resolveSlow(null);
			await p1;

			// Active session must remain session-b and nodes must not contain session-a data
			expect(store.getState().activeSession?.session_id).toBe("session-b");
			expect(
				store.getState().nodes.some((n) => n.concept_uri === "urn:a"),
			).toBe(false);
			expect(
				store.getState().nodes.some((n) => n.concept_uri === "urn:b"),
			).toBe(true);
		});

		it("reloads clarifications on HTTP 409/412 concurrent modification during submitAnswer", async () => {
			let reloadCalled = false;
			const mockFetch = async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				if (
					init?.method === "POST" &&
					(url.includes("/questions/q1/answer") || url.includes("/answers"))
				) {
					return new Response(JSON.stringify({ detail: "Concurrent edit" }), {
						status: 409,
					});
				}
				if (url.includes("/questions")) {
					reloadCalled = true;
					return new Response(
						JSON.stringify([{ question_id: "q1", status: "PENDING" }]),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			};

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			const success = await store
				.getState()
				.submitAnswer(
					{ question_id: "q1", selected_option_ids: ["opt1"] },
					undefined,
					mockFetch as unknown as typeof fetch,
				);
			expect(success).toBe(false);
			expect(reloadCalled).toBe(true);
		});

		it("throws error for unsupported export formats in generateLocalExport", () => {
			expect(() => {
				generateLocalExport("unknown_format", "1.0.0", [], [], {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				});
			}).toThrow("Unsupported export format");
		});

		it("blocks waiver when all concepts are already grounded", async () => {
			const spyFetch = (() => {
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "a".repeat(64),
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			store.setState({
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Def",
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo:Fund",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
					},
				],
			});

			const ok = await store
				.getState()
				.submitWaiver(
					"Validated via external compliance audit",
					"waiver-key-123",
					spyFetch,
				);
			expect(ok).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("NO_UNGROUNDED_CONCEPTS");
		});

		it("prevents stale session data race when body streaming resolves after newer session", async () => {
			const store = createStudioStore({
				initialSession: {
					session_id: "session-a",
					domain_namespace: "ns-a",
					enterprise_owner: "owner-a",
					status: "ACTIVE",
					active_version: "1.0.0",
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});

			let resolveBodyA: (v: unknown) => void = () => {};
			const bodyPromiseA = new Promise((resolve) => {
				resolveBodyA = resolve;
			});

			const mockFetch = async (input: RequestInfo | URL) => {
				const url = String(input);
				if (url.includes("session-a")) {
					return {
						ok: true,
						status: 200,
						json: async () => {
							await bodyPromiseA;
							return {
								session_id: "session-a",
								domain_namespace: "ns-a",
								enterprise_owner: "owner-a",
								status: "ACTIVE",
								active_version: "1.0.0",
							};
						},
					} as unknown as Response;
				}
				if (url.includes("session-b")) {
					return new Response(
						JSON.stringify({
							session_id: "session-b",
							domain_namespace: "ns-b",
							enterprise_owner: "owner-b",
							status: "ACTIVE",
							active_version: "2.0.0",
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			};

			const pA = store
				.getState()
				.selectSession("session-a", mockFetch as unknown as typeof fetch);
			await store
				.getState()
				.selectSession("session-b", mockFetch as unknown as typeof fetch);
			expect(store.getState().activeSession?.session_id).toBe("session-b");

			resolveBodyA(null);
			await pA;

			expect(store.getState().activeSession?.session_id).toBe("session-b");
		});

		it("blocks waiver when graph is empty", async () => {
			const spyFetch = (() => {
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			const store = createStudioStore({
				initialSession: {
					session_id: "s1",
					domain_namespace: "ns",
					enterprise_owner: "owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "a".repeat(64),
					created_at: "2026-11-11",
					updated_at: "2026-11-11",
				},
			});
			store.setState({ nodes: [] });
			const ok = await store
				.getState()
				.submitWaiver("Compliance audit waiver rationale", "key123", spyFetch);
			expect(ok).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("EMPTY_GRAPH");
		});

		it("executes structural diff comparing baseline and target graphs", () => {
			const store = createStudioStore();
			const baseGraph = {
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:fund",
						concept_name: "Fund",
						elm_archetype: "CORE",
						definition: "Old def",
						grounding_status: "GROUNDED" as const,
						attributes: [{ attribute_name: "a1", data_type: "str" }],
						synonyms: [],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo:Fund",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
					},
					{
						id: "c2",
						concept_uri: "urn:removed",
						concept_name: "Removed",
						elm_archetype: "CORE",
						definition: "Removed",
						grounding_status: "GROUNDED" as const,
						attributes: [],
						synonyms: [],
						external_mappings: [
							{ scheme: "fibo", target_uri: "fibo:Removed", confidence: 0.95 },
						],
					},
				],
				edges: [
					{
						id: "e1",
						relationship_uri: "urn:rel1",
						relationship_name: "rel1",
						target_link: "lnk",
						source_concept_uri: "urn:fund",
						target_concept_uri: "urn:removed",
					},
				],
			};

			store.setState({
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:fund",
						concept_name: "Fund",
						elm_archetype: "CORE",
						definition: "New def",
						grounding_status: "GROUNDED" as const,
						attributes: [
							{ attribute_name: "a1", data_type: "str" },
							{ attribute_name: "a2", data_type: "int" },
						],
						synonyms: [],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo:Fund",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
					},
					{
						id: "c3",
						concept_uri: "urn:added",
						concept_name: "Added",
						elm_archetype: "CORE",
						definition: "Added",
						grounding_status: "GROUNDED" as const,
						attributes: [],
						synonyms: [],
						external_mappings: [
							{ scheme: "fibo", target_uri: "fibo:Added", confidence: 0.95 },
						],
					},
				],
				edges: [
					{
						id: "e2",
						relationship_uri: "urn:rel2",
						relationship_name: "rel2",
						target_link: "lnk",
						source_concept_uri: "urn:fund",
						target_concept_uri: "urn:added",
					},
				],
			});

			store.getState().computeDiff("1.0.0", "1.1.0", baseGraph);
			const diff = store.getState().diffState;
			expect(diff.active).toBe(true);
			expect(diff.summary.added_count).toBe(2);
			expect(diff.summary.modified_count).toBe(1);
			expect(diff.summary.deleted_count).toBe(2);
			expect(diff.summary.breaking_count).toBe(2);
		});

		it("builds genuine delta operations and If-Match header in commitChanges", async () => {
			const store = createStudioStore();
			const activeSession: StudioSessionSummary = {
				session_id: "sess-commit-1",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const committedNode: ConceptNode = {
				id: "urn:ontology:entity:instrument",
				concept_uri: "urn:ontology:entity:instrument",
				concept_name: "Financial Instrument",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Original definition",
				target_hub: "hub_instrument",
				parent_concept_uri: null,
				confidence: 0.9,
				grounding_status: "GROUNDED",
				attributes: [{ attribute_name: "isin", data_type: "string" }],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:Instrument",
						predicate: "skos:exactMatch",
						confidence: 0.9,
					},
				],
			};

			store.setState({
				activeSession,
				committedNodes: [committedNode],
				committedEdges: [],
				nodes: [
					{
						...committedNode,
						definition: "Refined definition of instrument",
						attributes: [
							{ attribute_name: "isin", data_type: "string" },
							{ attribute_name: "currency", data_type: "string" },
						],
						external_mappings: [
							...committedNode.external_mappings,
							{
								scheme: "FIBO",
								target_uri:
									"https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/DebtInstruments/FinancialInstrument",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
					},
					{
						id: "urn:ontology:entity:party",
						concept_uri: "urn:ontology:entity:party",
						concept_name: "Legal Party",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Legal Entity",
						target_hub: "hub_party",
						parent_concept_uri: null,
						confidence: 0.95,
						grounding_status: "GROUNDED",
						attributes: [{ attribute_name: "lei", data_type: "string" }],
						synonyms: [],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo:Party",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
					},
				],
				edges: [
					{
						id: "urn:ontology:relationship:issued_by",
						relationship_uri: "urn:ontology:relationship:issued_by",
						relationship_name: "issued_by",
						target_link: "lnk_instrument_party",
						source_concept_uri: "urn:ontology:entity:instrument",
						target_concept_uri: "urn:ontology:entity:party",
					},
				],
				hasUnsavedChanges: true,
			});

			interface DeltaOp {
				op_type: string;
				concept_uri?: string;
				attribute_name?: string;
				data_type?: string;
			}
			interface CommitBody {
				delta_id?: string;
				parent_sha256?: string;
				base_version?: string;
				target_version?: string;
				operations: DeltaOp[];
			}
			const captured: {
				url?: string;
				headers?: Record<string, string>;
				body?: CommitBody;
			} = {};

			const mockFetch: typeof fetch = (async (
				url: string,
				init?: RequestInit,
			) => {
				captured.url = url;
				captured.headers = (init?.headers as Record<string, string>) || {};
				captured.body = JSON.parse(init?.body as string) as CommitBody;
				return {
					ok: true,
					status: 200,
					json: async () => ({
						session_id: "sess-commit-1",
						target_version: "1.1.0",
						target_sha256: "b".repeat(64),
						parent_sha256: "a".repeat(64),
						delta_id: captured.body?.delta_id,
						bump_type: "MINOR",
						applied_operations_count: captured.body?.operations.length ?? 0,
					}),
				} as Response;
			}) as typeof fetch;

			const success = await store
				.getState()
				.commitChanges("Visual studio refinement", mockFetch);
			expect(success).toBe(true);
			expect(captured.url).toContain(
				"/api/v1/ontology/studio/sessions/sess-commit-1/delta",
			);
			expect(captured.headers?.["If-Match"]).toBe(`"${"a".repeat(64)}"`);
			expect(captured.body?.parent_sha256).toBe("a".repeat(64));
			expect(captured.body?.base_version).toBe("1.0.0");
			expect(captured.body?.target_version).toBe("1.1.0");

			const opTypes = (captured.body?.operations || []).map((o) => o.op_type);
			expect(opTypes).toContain("add_concept");
			expect(opTypes).toContain("refine_definition");
			expect(opTypes).toContain("add_attribute");
			expect(opTypes).toContain("add_external_mapping");
			expect(opTypes).toContain("add_relationship");
			// Assert client-side grounding_status is not sent in delta operations (SUG-02)
			expect(
				(captured.body?.operations || []).every(
					(o) => !("grounding_status" in o),
				),
			).toBe(true);

			const state = store.getState();
			expect(state.hasUnsavedChanges).toBe(false);
			expect(state.activeSession?.head_sha256).toBe("b".repeat(64));
			expect(state.activeSession?.active_version).toBe("1.1.0");
			expect(state.committedNodes.length).toBe(2);
			expect(state.committedEdges.length).toBe(1);
		});

		it("guards commitChanges and promoteWorkspace against race conditions during session switch", async () => {
			const store = createStudioStore();
			const sessionA: StudioSessionSummary = {
				session_id: "sess-A",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};
			const sessionB: StudioSessionSummary = {
				session_id: "sess-B",
				domain_namespace: "risk",
				enterprise_owner: "cro@example.com",
				status: "ACTIVE",
				active_version: "2.0.0",
				head_sha256: "b".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: sessionA,
				sessions: [sessionA, sessionB],
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:opendv:test:c1",
						concept_name: "Test C1",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Test concept",
						target_hub: null,
						parent_concept_uri: null,
						confidence: 0.9,
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [
							{
								scheme: "fibo",
								target_uri: "fibo:TestC1",
								predicate: "skos:exactMatch",
								confidence: 0.95,
							},
						],
						x: 0,
						y: 0,
					},
				],
				committedNodes: [],
				edges: [],
				committedEdges: [],
			});

			// In-flight commit for session A
			let resolveCommit!: (r: Response) => void;
			const slowFetch: typeof fetch = (async () => {
				return new Promise<Response>((resolve) => {
					resolveCommit = resolve;
				});
			}) as typeof fetch;

			const commitPromise = store
				.getState()
				.commitChanges("Test race commit", slowFetch);

			// User rapidly switches to session B while commit is in-flight
			const nodeB: ConceptNode = {
				id: "node-b",
				concept_uri: "urn:concept:b",
				concept_name: "Concept B",
				elm_archetype: "CORE_CONCEPT",
				definition: "Session B node",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri: "https://example.com/b",
						predicate: "skos:exactMatch",
						confidence: 0.9,
					},
				],
			};
			store.setState({
				activeSession: sessionB,
				nodes: [nodeB],
				committedNodes: [nodeB],
				hasUnsavedChanges: true,
			});

			// Now resolve commit for session A
			resolveCommit({
				ok: true,
				status: 200,
				json: async () => ({
					target_version: "1.1.0",
					target_sha256: "c".repeat(64),
				}),
			} as Response);

			const result = await commitPromise;
			expect(result).toBe(false);
			// Session B must NOT have been mutated by Session A's commit
			expect(store.getState().activeSession?.session_id).toBe("sess-B");
			expect(store.getState().activeSession?.active_version).toBe("2.0.0");
			expect(store.getState().activeSession?.head_sha256).toBe("b".repeat(64));
			expect(store.getState().nodes).toEqual([nodeB]);
			expect(store.getState().committedNodes).toEqual([nodeB]);
			expect(store.getState().hasUnsavedChanges).toBe(true);

			// Session A's new head and version must be recorded in the sessions list (MIN-04)
			const sessionAInList = store
				.getState()
				.sessions.find((s) => s.session_id === "sess-A");
			expect(sessionAInList?.head_sha256).toBe("c".repeat(64));
			expect(sessionAInList?.active_version).toBe("1.1.0");

			// Matching promoteWorkspace race case (MIN-04):
			// Switch back to session A with no unsaved changes
			store.setState({
				activeSession: sessionAInList!,
				hasUnsavedChanges: false,
			});

			let resolvePromote!: (r: Response) => void;
			const slowPromoteFetch: typeof fetch = (async () => {
				return new Promise<Response>((resolve) => {
					resolvePromote = resolve;
				});
			}) as typeof fetch;

			const promotePromise = store.getState().promoteWorkspace(
				{
					head_sha256: "c".repeat(64),
					workspace_sha256: "0".repeat(64),
					rationale: "Promoting session A release candidate",
				},
				slowPromoteFetch,
			);

			// User switches to session B while promote is in-flight
			store.setState({
				activeSession: sessionB,
			});

			resolvePromote({
				ok: true,
				status: 200,
				json: async () => ({
					promoted_version: "2.0.0",
					head_sha256: "d".repeat(64),
				}),
			} as Response);

			const promoResult = await promotePromise;
			expect(promoResult).toBe(false);
			expect(store.getState().activeSession?.session_id).toBe("sess-B");
			const updatedSessionA = store
				.getState()
				.sessions.find((s) => s.session_id === "sess-A");
			expect(updatedSessionA?.head_sha256).toBe("d".repeat(64));
			expect(updatedSessionA?.active_version).toBe("2.0.0");
		});

		it("causes ONT-EVO-002 to fail when a new ungrounded concept is added after waiver authorization (MAJ-02)", () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-waiver-test",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const ungroundedNode1: ConceptNode = {
				id: "urn:ontology:entity:custom_alpha",
				concept_uri: "urn:ontology:entity:custom_alpha",
				concept_name: "Alpha Concept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Alpha definition",
				grounding_status: "UNGROUNDED",
				confidence: 0.1,
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			// Put fresh authorized waiver in sessionWaivers matching session.head_sha256
			const alphaWaiver = {
				waiver_applied: true,
				waiver_rationale: "Approved temporary exemption for alpha",
				waived_concept_uris: ["urn:ontology:entity:custom_alpha"],
				waived_base_sha256: session.head_sha256,
			};

			store.setState({
				activeSession: session,
				nodes: [ungroundedNode1],
				edges: [],
				sessionWaivers: {
					"sess-waiver-test": alphaWaiver,
				},
				qualityScorecard: recomputeScorecard(
					{
						...store.getState(),
						activeSession: session,
						sessionWaivers: {
							"sess-waiver-test": alphaWaiver,
						},
					},
					[ungroundedNode1],
					[],
				),
			});

			// Alpha alone is covered by the waiver: waiver is active and ONT-EVO-002 passes
			expect(store.getState().qualityScorecard.waiver_applied).toBe(true);
			const evoCheck1 = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck1?.status).toBe("PASS");

			// Now add a SECOND ungrounded concept without a waiver for it
			const ungroundedNode2: ConceptNode = {
				id: "urn:ontology:entity:custom_beta",
				concept_uri: "urn:ontology:entity:custom_beta",
				concept_name: "Beta Concept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Beta definition",
				grounding_status: "UNGROUNDED",
				confidence: 0.0,
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			store.getState().addConcept(ungroundedNode2);

			const currentScorecard = store.getState().qualityScorecard;
			// Waiver remains applied and fresh for the session
			expect(currentScorecard.waiver_applied).toBe(true);
			const evoCheck2 = currentScorecard.checks.find(
				(c) => c.rule_id === "ONT-EVO-002",
			);
			// Since custom_beta is ungrounded and NOT in waived_concept_uris, ONT-EVO-002 MUST FAIL!
			expect(evoCheck2?.status).toBe("FAIL");
			expect(evoCheck2?.details).toContain("violates ONT-EVO-002");

			// Direct pure function verification of concept-level scoping
			const scBoth = computeQualityScorecard(
				[ungroundedNode1, ungroundedNode2],
				[],
				{
					grandfatheredUris: [],
					waiverApplied: true,
					waivedUris: ["urn:ontology:entity:custom_alpha"],
				},
			);
			expect(
				scBoth.checks.find((c) => c.rule_id === "ONT-EVO-002")?.status,
			).toBe("FAIL");
		});

		it("blocks commitChanges when operations list is empty (MAJ-04)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-empty-ops",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "f".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const committedConcept: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.9,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:C1",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
				x: 0,
				y: 0,
			};

			store.setState({
				activeSession: session,
				nodes: [committedConcept],
				committedNodes: [committedConcept],
				edges: [],
				committedEdges: [],
			});

			let fetchCalled = false;
			const mockFetch: typeof fetch = (async () => {
				fetchCalled = true;
				return new Response(JSON.stringify({ ok: true }));
			}) as typeof fetch;

			const result = await store
				.getState()
				.commitChanges("Nothing changed", mockFetch);
			expect(result).toBe(false);
			expect(fetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("NO_OPERATIONS");
		});

		it("blocks commitChanges when concepts, attributes or data types are deleted or altered violating ONT-EVO-001 (MAJ-01)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-evo-guard",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "e".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const committedConcept: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.9,
				grounding_status: "GROUNDED",
				attributes: [
					{
						attribute_name: "amount",
						data_type: "decimal",
						description: "Amount in USD",
					},
				],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:C1", confidence: 0.95 },
				],
				x: 0,
				y: 0,
			};

			let spyFetchCalled = false;
			const spyFetch: typeof fetch = (async () => {
				spyFetchCalled = true;
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			// Case 1: Concept deleted
			store.setState({
				activeSession: session,
				nodes: [],
				committedNodes: [committedConcept],
			});
			const res1 = await store
				.getState()
				.commitChanges("Deleted concept", spyFetch);
			expect(res1).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Case 2: Attribute removed
			store.setState({
				activeSession: session,
				nodes: [{ ...committedConcept, attributes: [] }],
				committedNodes: [committedConcept],
				toasts: [],
			});
			const res2 = await store
				.getState()
				.commitChanges("Removed attribute", spyFetch);
			expect(res2).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Case 3: Attribute data type altered
			store.setState({
				activeSession: session,
				nodes: [
					{
						...committedConcept,
						attributes: [
							{
								attribute_name: "amount",
								data_type: "string", // altered from decimal
								description: "Amount in USD",
							},
						],
					},
				],
				committedNodes: [committedConcept],
				toasts: [],
			});
			const res3 = await store
				.getState()
				.commitChanges("Altered data type", spyFetch);
			expect(res3).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Case 4: Relationship removed (MIN-08)
			const committedRel: RelationshipEdge = {
				id: "rel-1",
				relationship_uri: "urn:rel:c1_target",
				relationship_name: "rel_name",
				target_link: "lnk_c1",
				source_concept_uri: "urn:c1",
				target_concept_uri: "urn:c1",
			};
			store.setState({
				activeSession: session,
				nodes: [committedConcept],
				committedNodes: [committedConcept],
				edges: [], // Removed committed relationship
				committedEdges: [committedRel],
				toasts: [],
			});
			const res4 = await store
				.getState()
				.commitChanges("Removed relationship", spyFetch);
			expect(res4).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Case 5: External mapping removed (MIN-08)
			store.setState({
				activeSession: session,
				nodes: [{ ...committedConcept, external_mappings: [] }], // Removed committed external mapping
				committedNodes: [committedConcept],
				edges: [],
				committedEdges: [],
				toasts: [],
			});
			const res5 = await store
				.getState()
				.commitChanges("Removed external mapping", spyFetch);
			expect(res5).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);
		});

		it("re-derives grounding status when updateConcept removes or adds external mappings (MAJ-03)", () => {
			const store = createStudioStore();
			const node: ConceptNode = {
				id: "c-ground",
				concept_uri: "urn:c-ground",
				concept_name: "Grounding Test",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Testing grounding derivation",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.0,
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
				x: 0,
				y: 0,
			};

			store.getState().addConcept(node);
			expect(store.getState().nodes[0]?.grounding_status).toBe("UNGROUNDED");

			// Add high-confidence FIBO mapping
			store.getState().updateConcept("urn:c-ground", {
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/Stock",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
			});
			expect(store.getState().nodes[0]?.grounding_status).toBe("GROUNDED");
			expect(store.getState().nodes[0]?.confidence).toBe(0.95);

			// Remove mappings -> must automatically flip back to UNGROUNDED even if caller passes GROUNDED
			store.getState().updateConcept("urn:c-ground", {
				external_mappings: [],
				grounding_status: "GROUNDED", // attempt to spoof
			});
			expect(store.getState().nodes[0]?.grounding_status).toBe("UNGROUNDED");
		});

		it("preserves hasUnsavedChanges when new edits occur while commitChanges is in-flight (MAJ-02, MIN-05)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-inflight",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const c1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def 1",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.95,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:c1",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
				x: 0,
				y: 0,
			};

			store.setState({
				activeSession: session,
				nodes: [],
				committedNodes: [],
				hasUnsavedChanges: false,
			});

			// User adds C1 via store action
			store.getState().addConcept(c1);
			expect(store.getState().hasUnsavedChanges).toBe(true);

			let resolveCommit!: (r: Response) => void;
			const commitDeferred = new Promise<Response>((resolve) => {
				resolveCommit = resolve;
			});

			interface DeltaPayload {
				operations: Array<{ op_type: string; concept_uri?: string }>;
			}
			const captured: { body?: DeltaPayload } = {};

			const slowFetch: typeof fetch = (async (
				_url: string,
				init?: RequestInit,
			) => {
				captured.body = JSON.parse(init?.body as string) as DeltaPayload;
				return commitDeferred;
			}) as typeof fetch;

			const commitPromise = store
				.getState()
				.commitChanges("Commit C1", slowFetch);

			// User adds C2 through store action while commit is in-flight (MIN-05)
			const c2: ConceptNode = {
				id: "c2",
				concept_uri: "urn:c2",
				concept_name: "C2",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def 2",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.95,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:c2",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
				x: 100,
				y: 100,
			};
			store.getState().addConcept(c2);

			// Now resolve commit for C1
			resolveCommit(
				new Response(
					JSON.stringify({
						target_version: "1.1.0",
						target_sha256: "b".repeat(64),
					}),
					{ status: 200 },
				),
			);

			const success = await commitPromise;
			expect(success).toBe(true);

			// Sent delta must contain only C1, proving in-flight C2 was not prematurely committed (MIN-05)
			expect(captured.body).toBeDefined();
			const committedUris = (captured.body?.operations || []).map(
				(o) => o.concept_uri,
			);
			expect(committedUris).toContain("urn:c1");
			expect(committedUris).not.toContain("urn:c2");

			// Invariant: hasUnsavedChanges MUST remain true because C2 was added during the commit!
			expect(store.getState().hasUnsavedChanges).toBe(true);
			expect(store.getState().committedNodes.length).toBe(1);
			expect(store.getState().committedNodes[0]?.concept_uri).toBe("urn:c1");
			expect(store.getState().nodes.length).toBe(2);
		});

		it("correctly computes SemVer 0.x patch and minor version bumps (MIN-01)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-semver-0x",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "0.1.2",
				head_sha256: "d".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const c0: ConceptNode = {
				id: "c0",
				concept_uri: "urn:c0",
				concept_name: "C0",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Existing concept on 0.1.2",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.95,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:c0",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
				x: 0,
				y: 0,
			};

			const c1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def 1",
				target_hub: null,
				parent_concept_uri: null,
				confidence: 0.95,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:c1",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
				x: 0,
				y: 0,
			};

			store.setState({
				activeSession: session,
				nodes: [c0, c1],
				committedNodes: [c0],
			});

			interface DeltaBodyPayload {
				target_version: string;
				parent_sha256?: string;
				operations?: unknown[];
			}
			const captured: { body?: DeltaBodyPayload } = {};
			const mockFetch: typeof fetch = (async (
				_url: string,
				init?: RequestInit,
			) => {
				captured.body = JSON.parse(init?.body as string) as DeltaBodyPayload;
				return new Response(
					JSON.stringify({
						target_version: captured.body?.target_version ?? "0.2.0",
						target_sha256: "e".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			await store.getState().commitChanges("Minor bump", mockFetch);
			// Adding concept bumps minor version on 0.x -> 0.2.0 (preserves major 0)
			expect(captured.body).toBeDefined();
			expect(captured.body?.target_version).toBe("0.2.0");
		});

		it("blocks promoteWorkspace when uncommitted changes are present or OCC precondition fails (MIN-10)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-promote-guard",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			let spyFetchCalled = false;
			const spyFetch: typeof fetch = (async () => {
				spyFetchCalled = true;
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// Case 1: Unsaved changes present
			store.setState({
				activeSession: session,
				hasUnsavedChanges: true,
			});
			const res1 = await store.getState().promoteWorkspace(
				{
					workspace_sha256: "0".repeat(64),
					head_sha256: "a".repeat(64),
					rationale: "Legitimate enterprise release candidate rationale",
				},
				spyFetch,
			);
			expect(res1).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"PROMOTION_UNSAVED_CHANGES",
			);

			// Case 2: Stale session head (OCC Precondition Failed)
			store.setState({
				activeSession: session,
				hasUnsavedChanges: false,
				toasts: [],
			});
			const res2 = await store.getState().promoteWorkspace(
				{
					workspace_sha256: "0".repeat(64),
					head_sha256: "b".repeat(64),
					rationale: "Legitimate enterprise release candidate rationale",
				},
				spyFetch,
			);
			expect(res2).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("OCC_PRECONDITION_FAILED");
		});

		it("strictly forces UNGROUNDED status in addConcept and updateConcept when external mappings are empty (MAJ-01)", () => {
			const store = createStudioStore();

			// 1. addConcept with asserted GROUNDED status but empty mappings
			store.getState().addConcept({
				id: "c-spoofed",
				concept_uri: "urn:c-spoofed",
				concept_name: "Spoofed Grounded Concept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Attempting to bypass grounding",
				confidence: 0.9,
				grounding_status: "GROUNDED", // Caller attempts to assert GROUNDED
				attributes: [],
				synonyms: [],
				external_mappings: [], // No mappings provided
			});

			const addedNode = store
				.getState()
				.nodes.find((n) => n.concept_uri === "urn:c-spoofed");
			expect(addedNode?.grounding_status).toBe("UNGROUNDED");

			// 2. updateConcept attempting to set GROUNDED on a concept with no mappings
			store.getState().updateConcept("urn:c-spoofed", {
				grounding_status: "GROUNDED",
			});

			const updatedNode = store
				.getState()
				.nodes.find((n) => n.concept_uri === "urn:c-spoofed");
			expect(updatedNode?.grounding_status).toBe("UNGROUNDED");

			// 3. Pre-commit gate ONT-EVO-002 must correctly catch this ungrounded concept
			const evoCheck = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("FAIL");
		});

		it("blocks selectSession, createSession, and loadGraph when unsaved changes exist without force (MAJ-02)", async () => {
			const store = createStudioStore();
			const sessionA: StudioSessionSummary = {
				session_id: "sess-a",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: sessionA,
				sessions: [sessionA],
				hasUnsavedChanges: true,
			});

			let spyFetchCalled = false;
			const spyFetch: typeof fetch = (async () => {
				spyFetchCalled = true;
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// selectSession blocks
			await store.getState().selectSession("sess-a", spyFetch);
			expect(store.getState().toasts[0]?.code).toBe("UNSAVED_CHANGES");
			expect(spyFetchCalled).toBe(false);

			// createSession blocks
			const created = await store.getState().createSession(
				{
					session_id: "sess-new",
					domain_namespace: "risk",
					enterprise_owner: "cro@example.com",
				},
				spyFetch,
			);
			expect(created).toBeNull();
			expect(store.getState().toasts[0]?.code).toBe("UNSAVED_CHANGES");
			expect(spyFetchCalled).toBe(false);

			// loadGraph blocks
			await store.getState().loadGraph("1.0.0", spyFetch);
			expect(store.getState().toasts[0]?.code).toBe("UNSAVED_CHANGES");
			expect(spyFetchCalled).toBe(false);
		});

		it("resets isHistoricalView and viewedVersion in discardChanges and updates sessions on commit (MIN-05)", () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-discard-hist",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "2.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: session,
				sessions: [session],
				isHistoricalView: true,
				viewedVersion: "1.0.0",
				committedNodes: [],
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:c1",
						concept_name: "C1",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Working edit",
						confidence: 0.9,
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [
							{ scheme: "fibo", target_uri: "fibo:c1", confidence: 0.95 },
						],
					},
				],
			});

			store.getState().discardChanges();
			expect(store.getState().isHistoricalView).toBe(false);
			expect(store.getState().viewedVersion).toBe("2.0.0");
		});

		it("blocks commitChanges when existing attribute optionality or mapping properties are mutated (MAJ-01)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-immut-test",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const baseNode: ConceptNode = {
				id: "c-immut",
				concept_uri: "urn:c-immut",
				concept_name: "ImmutableConcept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Stable concept",
				confidence: 0.95,
				grounding_status: "GROUNDED",
				attributes: [
					{
						attribute_name: "acc_id",
						data_type: "string",
						is_optional: false,
						example_values: ["ACC-123"],
					},
				],
				synonyms: [],
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri: "https://spec.edmcouncil.org/fibo/ontology/FBC/Account",
						confidence: 0.98,
						pinned: true,
					},
				],
			};

			let spyFetchCalled = false;
			const spyFetch: typeof fetch = (async () => {
				spyFetchCalled = true;
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// Case 1: mutating is_optional from false to true
			store.setState({
				activeSession: session,
				committedNodes: [baseNode],
				nodes: [
					{
						...baseNode,
						attributes: [
							{
								...baseNode.attributes[0]!,
								is_optional: true,
							},
						],
					},
				],
				committedEdges: [],
				edges: [],
			});

			let res = await store
				.getState()
				.commitChanges("Mutate is_optional", spyFetch);
			expect(res).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);

			// Case 2: mutating mapping confidence or pinned state
			store.setState({
				activeSession: session,
				toasts: [],
				committedNodes: [baseNode],
				nodes: [
					{
						...baseNode,
						external_mappings: [
							{
								...baseNode.external_mappings[0]!,
								confidence: 0.85,
							},
						],
					},
				],
				committedEdges: [],
				edges: [],
			});

			res = await store
				.getState()
				.commitChanges("Mutate mapping confidence", spyFetch);
			expect(res).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe(
				"ONT_EVO_NON_ADDITIVE_CHANGE",
			);
		});

		it("preserves waivers in sessionWaivers across commits/reloads and clears on reset (MAJ-02, MIN-07)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-waiver-persist",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: session,
				sessions: [session],
				nodes: [
					{
						id: "c-un",
						concept_uri: "urn:c-un",
						concept_name: "UngroundedConcept",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Needs waiver",
						confidence: 0.2,
						grounding_status: "UNGROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
			});

			// Submit waiver with realistic admin credential token (SUG-01, P14b::MAJ-02)
			let capturedWaiverUrl = "";
			let capturedWaiverHeaders: Record<string, string> = {};
			let capturedWaiverBody: Record<string, unknown> | null = null;
			const mockFetch: typeof fetch = (async (
				url: string,
				init?: RequestInit,
			) => {
				capturedWaiverUrl = url;
				capturedWaiverHeaders = (init?.headers as Record<string, string>) || {};
				capturedWaiverBody = JSON.parse(init?.body as string) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						waiver_applied: true,
						waiver_rationale: "Approved regulator delay",
						waived_concept_uris: ["urn:c-un"],
						waived_base_sha256: "a".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const waiverToken = "admin-waiver-secret-token-12345";
			const ok = await store
				.getState()
				.submitWaiver("Approved regulator delay", waiverToken, mockFetch);
			expect(ok).toBe(true);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(true);
			expect(
				store.getState().sessionWaivers["sess-waiver-persist"]?.waiver_applied,
			).toBe(true);
			expect(
				store.getState().sessionWaivers["sess-waiver-persist"]
					?.waived_base_sha256,
			).toBe("a".repeat(64));

			// Credential protection: verify token sent in X-OpenDV-Waiver-Key header and NEVER in URL query or JSON body (P14b::MAJ-02)
			expect(capturedWaiverUrl).not.toContain(waiverToken);
			expect(capturedWaiverHeaders["X-OpenDV-Waiver-Key"]).toBe(waiverToken);
			expect(capturedWaiverBody?.waiver_key).toBeUndefined();

			// Verify token credential is NEVER stored in store state or serialized state
			const serializedState = JSON.stringify(store.getState());
			expect(serializedState).not.toContain(waiverToken);

			// Calling reset clears sessionWaivers
			store.getState().reset();
			expect(store.getState().sessionWaivers).toEqual({});
		});

		it("handles HTTP 403 Forbidden on waiver submission rejecting invalid credentials (MIN-07)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-waiver-403",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: session,
				sessions: [session],
				nodes: [
					{
						id: "c-un",
						concept_uri: "urn:c-un",
						concept_name: "UngroundedConcept",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Needs waiver",
						confidence: 0.2,
						grounding_status: "UNGROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
			});

			const forbiddenFetch: typeof fetch = (async () => {
				return new Response(
					JSON.stringify({
						detail:
							"Provided administrative waiver token is invalid or unauthorized",
						code: "UNAUTHORIZED_WAIVER_KEY",
					}),
					{
						status: 403,
						headers: { "Content-Type": "application/problem+json" },
					},
				);
			}) as typeof fetch;

			const ok = await store
				.getState()
				.submitWaiver(
					"Approved regulator delay",
					"invalid-bogus-token",
					forbiddenFetch,
				);
			expect(ok).toBe(false);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(false);
			expect(
				store.getState().sessionWaivers["sess-waiver-403"],
			).toBeUndefined();
			expect(store.getState().toasts[0]?.status).toBe(403);
			expect(store.getState().toasts[0]?.code).toBe("UNAUTHORIZED_WAIVER_KEY");
		});

		it("loads sources in loadSources and during selectSession, ignoring stale responses (MAJ-01)", async () => {
			const store = createStudioStore();
			const sessionA: StudioSessionSummary = {
				session_id: "sess-src-a",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			store.setState({
				activeSession: sessionA,
				sessions: [sessionA],
				sources: [],
			});

			const mockFetch: typeof fetch = (async (url: string) => {
				if (url.includes("/sources")) {
					return new Response(
						JSON.stringify([
							{
								source_id: "src-1",
								source_sha256: "s1".padEnd(64, "0"),
								file_name: "risk_manual.pdf",
								size_bytes: 1024,
								mime_type: "application/pdf",
								do_not_send_to_agent: false,
							},
						]),
						{ status: 200 },
					);
				}
				if (url.includes("/questions")) {
					return new Response(JSON.stringify([]), { status: 200 });
				}
				if (url.includes("/export")) {
					return new Response(
						JSON.stringify({
							canonical_business_concepts: [],
							canonical_relationships: [],
						}),
						{
							status: 200,
						},
					);
				}
				return new Response(JSON.stringify(sessionA), { status: 200 });
			}) as typeof fetch;

			// Direct loadSources call
			await store.getState().loadSources(mockFetch, "sess-src-a");
			expect(store.getState().sources).toHaveLength(1);
			expect(store.getState().sources[0]?.file_name).toBe("risk_manual.pdf");

			// selectSession triggers loadSources
			store.setState({ sources: [] });
			await store.getState().selectSession("sess-src-a", mockFetch, true);
			expect(store.getState().sources).toHaveLength(1);
			expect(store.getState().sources[0]?.source_id).toBe("src-1");
		});

		it("restores server-recorded active waiver into sessionWaivers on selectSession (MAJ-02)", async () => {
			const store = createStudioStore();
			const sessionWithServerWaiver = {
				session_id: "sess-server-waiver",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "f".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
				active_waiver: {
					waiver_applied: true,
					waiver_rationale:
						"Enterprise board authorized grandfathered exemption",
					waived_concept_uris: ["urn:concept:grandfathered"],
					waived_base_sha256: "f".repeat(64),
				},
			};

			const mockFetch: typeof fetch = (async (url: string) => {
				if (url.includes("/sources"))
					return new Response(JSON.stringify([]), { status: 200 });
				if (url.includes("/questions"))
					return new Response(JSON.stringify([]), { status: 200 });
				if (url.includes("/export"))
					return new Response(
						JSON.stringify({
							canonical_business_concepts: [],
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				return new Response(JSON.stringify(sessionWithServerWaiver), {
					status: 200,
				});
			}) as typeof fetch;

			await store
				.getState()
				.selectSession("sess-server-waiver", mockFetch, true);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(true);
			expect(store.getState().qualityScorecard.waived_concept_uris).toContain(
				"urn:concept:grandfathered",
			);
			expect(
				store.getState().sessionWaivers["sess-server-waiver"]?.waiver_applied,
			).toBe(true);
		});

		it("rejects restored server waiver when waived_base_sha256 does not match session head (MAJ-02)", async () => {
			const store = createStudioStore();
			const staleServerWaiverSession = {
				session_id: "sess-stale-server-waiver",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64), // Head has advanced to 'a'
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
				active_waiver: {
					waiver_applied: true,
					waiver_rationale: "Approved for old head",
					waived_concept_uris: ["urn:concept:stale"],
					waived_base_sha256: "9".repeat(64), // Waived base is stale '9'
				},
			};

			const ungroundedConcept: ConceptNode = {
				id: "c-stale",
				concept_uri: "urn:concept:stale",
				concept_name: "Stale Concept",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Ungrounded entity with stale waiver",
				confidence: 0.1,
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			const mockFetch: typeof fetch = (async (url: string) => {
				if (url.includes("/sources"))
					return new Response(JSON.stringify([]), { status: 200 });
				if (url.includes("/questions"))
					return new Response(JSON.stringify([]), { status: 200 });
				if (url.includes("/export")) {
					return new Response(
						JSON.stringify({
							canonical_business_concepts: [
								{
									concept_uri: "urn:concept:stale",
									concept_name: "Stale Concept",
									elm_archetype: "CORE_BUSINESS_CONCEPT",
									definition: "Ungrounded entity with stale waiver",
									external_mappings: [],
									attributes: [],
									synonyms: [],
								},
							],
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				}
				return new Response(JSON.stringify(staleServerWaiverSession), {
					status: 200,
				});
			}) as typeof fetch;

			await store
				.getState()
				.selectSession("sess-stale-server-waiver", mockFetch, true);
			// Scorecard must reject the stale waiver because head_sha256 !== waived_base_sha256
			expect(store.getState().qualityScorecard.waiver_applied).toBe(false);
			const evoCheck = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("FAIL");
		});

		it("invalidates active waiver when a commit moves session head_sha256 causing subsequent ungrounded concepts to fail ONT-EVO-002 (MAJ-02)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-waiver-head-advance",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const ungroundedC1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Ungrounded at head A",
				confidence: 0.1,
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			store.setState({
				activeSession: session,
				sessions: [session],
				nodes: [ungroundedC1],
				committedNodes: [],
				sessionWaivers: {
					"sess-waiver-head-advance": {
						waiver_applied: true,
						waiver_rationale: "Exemption at head A",
						waived_concept_uris: ["urn:c1"],
						waived_base_sha256: "a".repeat(64),
					},
				},
			});

			// Recompute scorecard for head 'a' -> waiver applies
			store.setState({
				qualityScorecard: recomputeScorecard(
					store.getState(),
					store.getState().nodes,
					store.getState().edges,
				),
			});
			expect(store.getState().qualityScorecard.waiver_applied).toBe(true);
			let evoCheck = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("PASS");

			// Mock commit moving head to 'b'
			const commitFetch: typeof fetch = (async (
				_url: string,
				init?: RequestInit,
			) => {
				const body = JSON.parse(init?.body as string);
				return new Response(
					JSON.stringify({
						session_id: "sess-waiver-head-advance",
						target_version: "1.1.0",
						target_sha256: "b".repeat(64),
						parent_sha256: "a".repeat(64),
						applied_operations_count: body.operations.length,
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const success = await store
				.getState()
				.commitChanges("Commit ungrounded C1 under waiver", commitFetch);
			expect(success).toBe(true);
			expect(store.getState().activeSession?.head_sha256).toBe("b".repeat(64));

			// Now head is 'b', and waiver had waived_base_sha256: 'a'.
			// Adding a second ungrounded concept C2 at head 'b' must NOT be shielded by the stale waiver at head 'a'
			const ungroundedC2: ConceptNode = {
				id: "c2",
				concept_uri: "urn:c2",
				concept_name: "C2",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Ungrounded at head B",
				confidence: 0.1,
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			store.getState().addConcept(ungroundedC2);
			expect(store.getState().qualityScorecard.waiver_applied).toBe(false);
			evoCheck = store
				.getState()
				.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("FAIL");
		});
	});

	describe("9. Multi-Format Export Serialization & Byte-Stability (Task 5D.6, #227)", () => {
		const sessionFixture: StudioSessionSummary = {
			session_id: "sess-export-test",
			domain_namespace: "finance",
			enterprise_owner: "cdo@example.com",
			status: "ACTIVE",
			active_version: "1.0.0",
			head_sha256: "a".repeat(64),
			created_at: "2026-11-11T00:00:00Z",
			updated_at: "2026-11-11T00:00:00Z",
		};

		const nodeFixture1: ConceptNode = {
			id: "c1",
			concept_uri: "urn:ontology:entity:portfolio",
			concept_name: "Portfolio",
			elm_archetype: "CORE_BUSINESS_CONCEPT",
			definition: "Investment portfolio container",
			target_hub: "hub_portfolio",
			parent_concept_uri: null,
			confidence: 0.95,
			grounding_status: "GROUNDED",
			attributes: [
				{
					attribute_name: "portfolio_id",
					data_type: "string",
					description: "Identifier",
				},
				{
					attribute_name: "inception_date",
					data_type: "date",
					description: "Start date",
				},
			],
			synonyms: ["fund_portfolio"],
			external_mappings: [
				{
					scheme: "FIBO",
					target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
					confidence: 0.95,
				},
			],
			x: 50,
			y: 50,
		};

		const nodeFixture2: ConceptNode = {
			id: "c2",
			concept_uri: "urn:ontology:entity:asset",
			concept_name: "Asset",
			elm_archetype: "CORE_BUSINESS_CONCEPT",
			definition: "Financial asset",
			target_hub: "hub_asset",
			parent_concept_uri: null,
			confidence: 0.9,
			grounding_status: "GROUNDED",
			attributes: [{ attribute_name: "asset_id", data_type: "string" }],
			synonyms: [],
			external_mappings: [
				{
					scheme: "FIBO",
					target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Asset",
					confidence: 0.9,
				},
			],
			x: 390,
			y: 50,
		};

		const edgeFixture: RelationshipEdge = {
			id: "e1",
			relationship_uri: "urn:ontology:rel:holds_asset",
			relationship_name: "holds_asset",
			target_link: "lnk_portfolio_asset",
			source_concept_uri: "urn:ontology:entity:portfolio",
			target_concept_uri: "urn:ontology:entity:asset",
		};

		it("verifies RFC 8785 key sorting by UTF-16 code units matching opendv-core parity including astral and high-BMP keys (MAJ-01, MAJ-02)", () => {
			const rawDict = {
				z: 1,
				a: 2,
				"\u00e9": 3,
				A: 4,
				_: 5,
				"\uE000": 6, // High-BMP Private Use code point (0xE000 / 57344)
				"\uD83D\uDE00": 7, // Astral code point U+1F600 😀: surrogate pair \uD83D (55357) + \uDE00 (56832)
			};
			// In UTF-16 code unit order:
			// 'A' (0x41), '_' (0x5F), 'a' (0x61), 'z' (0x7A), '\u00e9' (0xE9),
			// '\uD83D\uDE00' (lead surrogate 0xD83D = 55357),
			// '\uE000' (0xE000 = 57344)
			const jcs = dumpsJcs(rawDict);
			expect(jcs).toBe(
				'{"A":4,"_":5,"a":2,"z":1,"\u00e9":3,"\uD83D\uDE00":7,"\uE000":6}',
			);
		});

		it("verifies RFC 8785 IEEE 754 number formatting, negative zero, and non-finite rejection (MAJ-01, MAJ-02)", () => {
			expect(dumpsJcs({ val: 42 })).toBe('{"val":42}');
			expect(dumpsJcs({ val: 1.5 })).toBe('{"val":1.5}');
			expect(dumpsJcs({ val: 0 })).toBe('{"val":0}');
			// RFC 8785 section 3.2.2.3: negative zero must serialize as "0"
			expect(dumpsJcs({ val: -0 })).toBe('{"val":0}');
			// Large and small numbers formatted with ECMAScript rules
			expect(dumpsJcs({ val: 1e21 })).toBe('{"val":1e+21}');
			expect(dumpsJcs({ val: 1e-7 })).toBe('{"val":1e-7}');
			// Exact IEEE 754 floating point representation
			expect(dumpsJcs({ val: 0.1 + 0.2 })).toBe('{"val":0.30000000000000004}');
			// Strings with control characters
			expect(dumpsJcs({ text: 'hello\nworld\t"escaped"\\' })).toBe(
				'{"text":"hello\\nworld\\t\\"escaped\\"\\\\"}',
			);
			// RFC 8785 rejects NaN and Infinity
			expect(() => dumpsJcs({ val: Number.NaN })).toThrow();
			expect(() => dumpsJcs({ val: Number.POSITIVE_INFINITY })).toThrow();
			expect(() => dumpsJcs({ val: Number.NEGATIVE_INFINITY })).toThrow();
		});

		it("produces identical byte-for-byte canonical JCS output regardless of key insertion order and strips explicit null fields (MAJ-01, MAJ-02)", () => {
			const nodeScrambled1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def",
				grounding_status: "GROUNDED",
				target_hub: null,
				parent_concept_uri: null,
				attributes: [
					{
						attribute_name: "a",
						data_type: "string",
						description: null as unknown as string,
					},
					{ attribute_name: "b", data_type: "int" },
				],
				synonyms: ["syn1"],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:c1", confidence: 0.9 },
				],
				x: 100,
				y: 200,
			};

			const nodeScrambled2: ConceptNode = {
				definition: "Def",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				concept_name: "C1",
				id: "different_layout_id",
				attributes: [
					{ data_type: "string", attribute_name: "a" },
					{ data_type: "int", attribute_name: "b" },
				],
				external_mappings: [
					{ target_uri: "fibo:c1", scheme: "fibo", confidence: 0.9 },
				],
				synonyms: ["syn1"],
				concept_uri: "urn:c1",
				grounding_status: "GROUNDED",
				x: 999,
				y: 888,
			};

			const jcs1 = generateLocalExport(
				"rfc8785",
				"1.0.0",
				[nodeScrambled1],
				[],
				sessionFixture,
			);
			const jcs2 = generateLocalExport(
				"rfc8785",
				"1.0.0",
				[nodeScrambled2],
				[],
				sessionFixture,
			);

			expect(jcs1).toBe(jcs2);
			// Pruned client-only layout coords and IDs
			expect(jcs1).not.toContain('"x"');
			expect(jcs1).not.toContain('"y"');
			expect(jcs1).not.toContain('"different_layout_id"');
			expect(jcs1).not.toContain("null");
		});

		it("matches exact Python hashlib.sha256 JCS golden digest parity for Graph A via production export (MAJ-01)", async () => {
			// Build genuine ConceptNodes from golden fixture inputs
			const concepts: ConceptNode[] =
				goldenFixture.raw_model.canonical_business_concepts.map((c: any) => ({
					id: c.concept_uri,
					concept_uri: c.concept_uri,
					concept_name: c.concept_name,
					elm_archetype: c.elm_archetype,
					definition: c.definition,
					target_hub: c.target_hub,
					attributes: c.attributes,
					synonyms: c.synonyms,
					external_mappings: c.external_mappings,
					grounding_status: "GROUNDED",
				}));

			const session: StudioSessionSummary = {
				session_id: "sess-golden",
				domain_namespace: goldenFixture.raw_model.domain_namespace,
				enterprise_owner: goldenFixture.raw_model.enterprise_owner,
				active_version: goldenFixture.raw_model.ontology_version,
				status: "ACTIVE",
				head_sha256: "a".repeat(64),
				created_at: "2026-11-11",
				updated_at: "2026-11-11",
			};

			// Run through production generateLocalExport
			const jcsOutput = generateLocalExport(
				"rfc8785",
				session.active_version,
				concepts,
				[],
				session,
			);
			expect(jcsOutput).toBe(goldenFixture.canonical_jcs);

			const digestBuf = await crypto.subtle.digest(
				"SHA-256",
				new TextEncoder().encode(jcsOutput),
			);
			const hex = Array.from(new Uint8Array(digestBuf))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");
			expect(hex).toBe(goldenFixture.sha256);
		});

		it("round-trips RFC 8785 export through JSON.parse and concept reconstruction preserving exact digest (MAJ-01)", async () => {
			const initialJcs = generateLocalExport(
				"rfc8785",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			const initialDigest = Array.from(
				new Uint8Array(
					await crypto.subtle.digest(
						"SHA-256",
						new TextEncoder().encode(initialJcs),
					),
				),
			)
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");

			// Parse serialized JCS back into JSON object
			const parsed = JSON.parse(initialJcs);

			// Map back to ConceptNode[] and RelationshipEdge[]
			const reconstructedNodes: ConceptNode[] =
				parsed.canonical_business_concepts.map((cbc: any) => ({
					id: cbc.concept_uri,
					concept_uri: cbc.concept_uri,
					concept_name: cbc.concept_name,
					elm_archetype: cbc.elm_archetype,
					definition: cbc.definition,
					target_hub: cbc.target_hub,
					attributes: cbc.attributes || [],
					synonyms: cbc.synonyms || [],
					external_mappings: cbc.external_mappings || [],
					grounding_status: "GROUNDED",
				}));

			const reconstructedEdges: RelationshipEdge[] =
				parsed.canonical_relationships.map((rel: any) => ({
					id: rel.relationship_uri,
					relationship_uri: rel.relationship_uri,
					relationship_name: rel.relationship_name,
					target_link: rel.target_link,
					source_concept_uri:
						rel.source_concept_uri || (rel.endpoints ? rel.endpoints[0] : ""),
					target_concept_uri:
						rel.target_concept_uri || (rel.endpoints ? rel.endpoints[1] : ""),
					cardinality: rel.cardinality,
					description: rel.description,
				}));

			const reconstructedSession: StudioSessionSummary = {
				...sessionFixture,
				active_version: parsed.ontology_version,
				domain_namespace: parsed.domain_namespace,
				enterprise_owner: parsed.enterprise_owner,
			};

			// Re-export and verify byte-for-byte and digest identity
			const reExportedJcs = generateLocalExport(
				"rfc8785",
				reconstructedSession.active_version,
				reconstructedNodes,
				reconstructedEdges,
				reconstructedSession,
			);
			expect(reExportedJcs).toBe(initialJcs);

			const reExportDigest = Array.from(
				new Uint8Array(
					await crypto.subtle.digest(
						"SHA-256",
						new TextEncoder().encode(reExportedJcs),
					),
				),
			)
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");
			expect(reExportDigest).toBe(initialDigest);
		});

		it("produces byte-stable deterministic JSON schema envelope output with shuffled input order (MIN-03)", () => {
			const json1 = generateLocalExport(
				"json",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			const json2 = generateLocalExport(
				"json",
				"1.0.0",
				[nodeFixture2, nodeFixture1],
				[edgeFixture],
				sessionFixture,
			);

			expect(json1).toBe(json2);
			const parsed = JSON.parse(json1);
			expect(parsed.$schema).toContain("ontology-export.schema.json");
			expect(parsed.domain_namespace).toBe("finance");
			expect(parsed.ontology_version).toBe("1.0.0");
			expect(parsed.concepts).toHaveLength(2);
			expect(parsed.relationships).toHaveLength(1);
		});

		it("produces byte-stable deterministic RFC 8785 JCS output with shuffled input order (MIN-03)", () => {
			const rfc1 = generateLocalExport(
				"rfc8785",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			const rfc2 = generateLocalExport(
				"rfc8785",
				"1.0.0",
				[nodeFixture2, nodeFixture1],
				[edgeFixture],
				sessionFixture,
			);
			expect(rfc1).toBe(rfc2);
		});

		it("generates valid Graph A YAML with schema version, concepts and relationships (MAJ-02)", () => {
			const yaml = generateLocalExport(
				"yaml",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			expect(yaml).toContain('ontology_version: "1.0.0"');
			expect(yaml).toContain('domain_namespace: "finance"');
			expect(yaml).toContain('concept_uri: "urn:ontology:entity:portfolio"');
			expect(yaml).toContain(
				'relationship_uri: "urn:ontology:rel:holds_asset"',
			);
		});

		it("generates valid W3C Turtle (.ttl) with OWL ontology and class declarations (MAJ-02)", () => {
			const turtle = generateLocalExport(
				"turtle",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			expect(turtle).toContain(
				"@prefix owl: <http://www.w3.org/2002/07/owl#> .",
			);
			expect(turtle).toContain(
				"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
			);
			expect(turtle).toContain("a owl:Ontology ;");
			expect(turtle).toContain("<urn:ontology:entity:portfolio> a owl:Class ;");
			expect(turtle).toContain(
				"<urn:ontology:rel:holds_asset> a owl:ObjectProperty ;",
			);
		});

		it("generates valid JSON-LD graph representation (MAJ-02)", () => {
			const jsonld = generateLocalExport(
				"jsonld",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			const parsed = JSON.parse(jsonld);
			expect(parsed["@context"]).toBeDefined();
			expect(Array.isArray(parsed["@graph"])).toBe(true);
			const portfolioClass = parsed["@graph"].find(
				(item: { "@id": string }) =>
					item["@id"] === "urn:ontology:entity:portfolio",
			);
			expect(portfolioClass).toBeDefined();
			expect(portfolioClass["@type"]).toBe("owl:Class");
		});

		it("generates valid RDF/XML serialization (MAJ-02)", () => {
			const rdfxml = generateLocalExport(
				"rdf",
				"1.0.0",
				[nodeFixture1, nodeFixture2],
				[edgeFixture],
				sessionFixture,
			);
			expect(rdfxml).toContain('<?xml version="1.0" encoding="utf-8"?>');
			expect(rdfxml).toContain("<rdf:RDF");
			expect(rdfxml).toContain(
				'<owl:Class rdf:about="urn:ontology:entity:portfolio">',
			);
			expect(rdfxml).toContain(
				'<owl:ObjectProperty rdf:about="urn:ontology:rel:holds_asset">',
			);
		});
	});

	describe("10. ONT-DAG-001 Inheritance Acyclicity & Graph Validation (Task 5D.5, #226)", () => {
		it("detects direct and indirect inheritance cycles violating ONT-DAG-001 (MAJ-02)", () => {
			const cyclicNodeA: ConceptNode = {
				id: "cA",
				concept_uri: "urn:cA",
				concept_name: "A",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Cycle node A",
				parent_concept_uri: "urn:cB",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:A", confidence: 0.95 },
				],
			};
			const cyclicNodeB: ConceptNode = {
				id: "cB",
				concept_uri: "urn:cB",
				concept_name: "B",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Cycle node B",
				parent_concept_uri: "urn:cA", // Cycle A -> B -> A
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:B", confidence: 0.95 },
				],
			};

			const scorecard = computeQualityScorecard([cyclicNodeA, cyclicNodeB], []);
			expect(scorecard.dag_acyclic).toBe(false);
			const dagCheck = scorecard.checks.find(
				(c) => c.rule_id === "ONT-DAG-001",
			);
			expect(dagCheck?.status).toBe("FAIL");
		});

		it("verifies acyclic DAG inheritance tree passes ONT-DAG-001 (MAJ-02)", () => {
			const rootNode: ConceptNode = {
				id: "cRoot",
				concept_uri: "urn:cRoot",
				concept_name: "Root",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Root entity",
				parent_concept_uri: null,
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:Root", confidence: 0.95 },
				],
			};
			const childNode: ConceptNode = {
				id: "cChild",
				concept_uri: "urn:cChild",
				concept_name: "Child",
				elm_archetype: "SUPPORTING_CONCEPT",
				definition: "Child entity",
				parent_concept_uri: "urn:cRoot",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{ scheme: "fibo", target_uri: "fibo:Child", confidence: 0.95 },
				],
			};

			const scorecard = computeQualityScorecard([rootNode, childNode], []);
			expect(scorecard.dag_acyclic).toBe(true);
			const dagCheck = scorecard.checks.find(
				(c) => c.rule_id === "ONT-DAG-001",
			);
			expect(dagCheck?.status).toBe("PASS");
		});
	});

	describe("11. Binary ArrayBuffer Source Upload & CAS Handling (Task 5D.2, #223)", () => {
		it("validates supported source extensions and rejects executable binaries", () => {
			expect(isSupportedSourceFile("doc.pdf", "application/pdf")).toBe(true);
			expect(isSupportedSourceFile("data.docx")).toBe(true);
			expect(isSupportedSourceFile("sheet.xlsx")).toBe(true);
			expect(isSupportedSourceFile("records.csv")).toBe(true);
			expect(isSupportedSourceFile("spec.json")).toBe(true);
			expect(isSupportedSourceFile("readme.md")).toBe(true);
			expect(isSupportedSourceFile("notes.txt")).toBe(true);

			expect(isSupportedSourceFile("malicious.exe")).toBe(false);
			expect(isSupportedSourceFile("lib.dll")).toBe(false);
			expect(isSupportedSourceFile("script.sh")).toBe(false);
		});

		it("handles binary ArrayBuffer source upload packaging FormData with CAS integrity (MAJ-02)", async () => {
			const store = createStudioStore();
			store.setState({
				activeSession: {
					session_id: "sess-cas-test",
					domain_namespace: "finance",
					enterprise_owner: "cdo@example.com",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "a".repeat(64),
					created_at: new Date().toISOString(),
					updated_at: new Date().toISOString(),
				},
			});

			const binaryPayload = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])
				.buffer; // %PDF-
			let capturedFormData: FormData | null = null;

			const mockFetch: typeof fetch = (async (
				_url: string,
				init?: RequestInit,
			) => {
				capturedFormData = init?.body as FormData;
				return new Response(
					JSON.stringify({
						source_id: "src-cas-001",
						file_name: "regulatory_policy.pdf",
						mime_type: "application/pdf",
						source_sha256:
							"38523c087796e5d5dd1cf9bad1fb026781a838dd9dd2cf8af58b9f6502a46778",
						size_bytes: 5,
						uploaded_at: new Date().toISOString(),
						status: "UPLOADED",
						do_not_send_to_agent: false,
					}),
					{ status: 201 },
				);
			}) as typeof fetch;

			const uploaded = await store.getState().uploadSource(
				{
					name: "regulatory_policy.pdf",
					type: "application/pdf",
					content: binaryPayload,
				},
				false,
				mockFetch,
			);

			expect(uploaded).not.toBeNull();
			expect(uploaded?.source_id).toBe("src-cas-001");
			expect(uploaded?.source_sha256).toBe(
				"38523c087796e5d5dd1cf9bad1fb026781a838dd9dd2cf8af58b9f6502a46778",
			);
			expect(uploaded?.size_bytes).toBe(5);
			expect(capturedFormData).not.toBeNull();
			expect(store.getState().sources).toHaveLength(1);
			expect(store.getState().sources[0]?.file_name).toBe(
				"regulatory_policy.pdf",
			);
			expect(store.getState().sources[0]?.source_sha256).toBe(
				"38523c087796e5d5dd1cf9bad1fb026781a838dd9dd2cf8af58b9f6502a46778",
			);
			expect(store.getState().sources[0]?.size_bytes).toBe(5);

			// Verify binary ArrayBuffer CAS integrity in FormData payload (MIN-01)
			const fileEntry = capturedFormData!.get("file") as Blob;
			expect(fileEntry).toBeDefined();
			expect(fileEntry.size).toBe(5);

			const fileBuffer = await fileEntry.arrayBuffer();
			const header = Array.from(new Uint8Array(fileBuffer.slice(0, 5)));
			expect(header).toEqual([0x25, 0x50, 0x44, 0x46, 0x2d]);

			const computedShaBuf = await crypto.subtle.digest("SHA-256", fileBuffer);
			const computedSha = Array.from(new Uint8Array(computedShaBuf))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");
			expect(computedSha).toBe(
				"38523c087796e5d5dd1cf9bad1fb026781a838dd9dd2cf8af58b9f6502a46778",
			);
		});
	});

	describe("12. Layout State Synchronization & Search Filtering (Task 5D.4, #225)", () => {
		it("updates layout LOD tier and triggers relayout counter", () => {
			const store = createStudioStore();
			expect(store.getState().layout.lodTier).toBe(2);
			expect(store.getState().layout.layoutTriggerCount ?? 0).toBe(0);

			store.getState().setLodTier(3);
			expect(store.getState().layout.lodTier).toBe(3);

			store.getState().triggerRelayout();
			expect(store.getState().layout.layoutTriggerCount).toBe(1);

			store.getState().triggerRelayout();
			expect(store.getState().layout.layoutTriggerCount).toBe(2);
		});

		it("filters matching concept nodes by search term in layout state using selectFilteredNodes (MIN-06)", () => {
			const store = createStudioStore();
			const c1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:concept:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Investment fund entity",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: ["mutual_fund"],
				external_mappings: [],
			};
			const c2: ConceptNode = {
				id: "c2",
				concept_uri: "urn:concept:security",
				concept_name: "Security",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Financial equity security",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			store.setState({ nodes: [c1, c2] });

			// 1. Filter by name
			store.getState().setSearchTerm("Fund");
			expect(store.getState().layout.searchTerm).toBe("Fund");
			const matchedByName = selectFilteredNodes(
				store.getState().nodes,
				store.getState().layout.searchTerm,
			);
			expect(matchedByName).toHaveLength(1);
			expect(matchedByName[0]?.concept_name).toBe("Fund");

			// 2. Filter by synonym
			const matchedBySyn = selectFilteredNodes(
				store.getState().nodes,
				"mutual",
			);
			expect(matchedBySyn).toHaveLength(1);
			expect(matchedBySyn[0]?.concept_name).toBe("Fund");

			// 3. Filter by URI
			const matchedByUri = selectFilteredNodes(
				store.getState().nodes,
				"urn:concept:security",
			);
			expect(matchedByUri).toHaveLength(1);
			expect(matchedByUri[0]?.concept_name).toBe("Security");

			// 4. Filter by definition
			const matchedByDef = selectFilteredNodes(
				store.getState().nodes,
				"equity",
			);
			expect(matchedByDef).toHaveLength(1);
			expect(matchedByDef[0]?.concept_name).toBe("Security");

			// 5. Empty or whitespace search term returns all nodes
			expect(selectFilteredNodes(store.getState().nodes, "")).toHaveLength(2);
			expect(selectFilteredNodes(store.getState().nodes, "   ")).toHaveLength(
				2,
			);

			// Clear query
			store.getState().setSearchTerm("");
			expect(store.getState().layout.searchTerm).toBe("");
		});
	});

	describe("13. Steward Feedback & OCC 412 Conflict Resolution (Task 5D.3 / 5D.6 / SUG-02)", () => {
		it("executes submitFeedback recording 5-star rating to feedback.jsonl store state (MAJ-02)", async () => {
			const store = createStudioStore();
			store.setState({
				activeSession: {
					session_id: "sess-feedback",
					domain_namespace: "finance",
					enterprise_owner: "cdo@example.com",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "a".repeat(64),
					created_at: new Date().toISOString(),
					updated_at: new Date().toISOString(),
				},
				qualityScorecard: {
					grounding_score: 0.9,
					deployed_contracts_ok: true,
					dag_acyclic: true,
					yagni_compliant: true,
					checks: [],
					waiver_applied: false,
					waiver_rationale: "",
					feedback_submitted: false,
					feedback_rating: 0,
					feedback_comment: "",
				},
			});

			let capturedUrl = "";
			let capturedMethod = "";
			let capturedBody: any = null;
			const mockFetch: typeof fetch = (async (
				url: string,
				init?: RequestInit,
			) => {
				capturedUrl = url;
				capturedMethod = init?.method || "GET";
				capturedBody = init?.body ? JSON.parse(init.body as string) : null;
				return new Response(
					JSON.stringify({ status: "recorded", written_to: "feedback.jsonl" }),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok = await store
				.getState()
				.submitFeedback(
					5,
					"Excellent enterprise domain coverage and grounding accuracy",
					mockFetch,
				);

			expect(ok).toBe(true);
			expect(capturedUrl).toContain(
				"/api/v1/ontology/studio/sessions/sess-feedback/feedback",
			);
			expect(capturedMethod).toBe("POST");
			expect(capturedBody.rating).toBe(5);
			expect(capturedBody.comment).toBe(
				"Excellent enterprise domain coverage and grounding accuracy",
			);
			expect(capturedBody.concept_uri).toBeNull();
			expect(store.getState().qualityScorecard.feedback_submitted).toBe(true);
			expect(store.getState().qualityScorecard.feedback_rating).toBe(5);
			expect(store.getState().qualityScorecard.feedback_comment).toContain(
				"Excellent enterprise",
			);
			expect(
				store.getState().toasts.some((t) => t.title === "Feedback Recorded"),
			).toBe(true);
		});

		it("rejects invalid feedback ratings (0, 6, 3.5, NaN) with code INVALID_FEEDBACK_RATING without calling fetch (SUG-02)", async () => {
			const store = createStudioStore();
			store.setState({
				activeSession: {
					session_id: "sess-rating-validation",
					domain_namespace: "finance",
					enterprise_owner: "cdo@example.com",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: "a".repeat(64),
					created_at: new Date().toISOString(),
					updated_at: new Date().toISOString(),
				},
			});

			let spyFetchCalled = false;
			const spyFetch: typeof fetch = (async () => {
				spyFetchCalled = true;
				throw new Error("Guard failed: network fetch was unexpectedly invoked");
			}) as typeof fetch;

			// Rating 0 (< 1)
			const res0 = await store
				.getState()
				.submitFeedback(0, "Rating 0 test", spyFetch);
			expect(res0).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_FEEDBACK_RATING");

			// Rating 6 (> 5)
			store.setState({ toasts: [] });
			const res6 = await store
				.getState()
				.submitFeedback(6, "Rating 6 test", spyFetch);
			expect(res6).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_FEEDBACK_RATING");

			// Non-integer rating 3.5
			store.setState({ toasts: [] });
			const resFloat = await store
				.getState()
				.submitFeedback(3.5, "Non-integer rating", spyFetch);
			expect(resFloat).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_FEEDBACK_RATING");

			// NaN rating
			store.setState({ toasts: [] });
			const resNan = await store
				.getState()
				.submitFeedback(Number.NaN, "NaN rating", spyFetch);
			expect(resNan).toBe(false);
			expect(spyFetchCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("INVALID_FEEDBACK_RATING");
		});

		it("handles HTTP 412 Precondition Failed during commitChanges preserving unsaved edits (ONT-EVO-003, SUG-02)", async () => {
			const store = createStudioStore();
			const session: StudioSessionSummary = {
				session_id: "sess-occ-412",
				domain_namespace: "finance",
				enterprise_owner: "cdo@example.com",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: new Date().toISOString(),
				updated_at: new Date().toISOString(),
			};

			const c1: ConceptNode = {
				id: "c1",
				concept_uri: "urn:c1",
				concept_name: "C1",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Def 1",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri: "fibo:c1",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				],
			};

			store.setState({
				activeSession: session,
				nodes: [c1],
				committedNodes: [],
				hasUnsavedChanges: true,
			});

			const conflictFetch: typeof fetch = (async () => {
				return new Response(
					JSON.stringify({
						detail:
							"Head SHA mismatch: server commit detected concurrent update",
						code: "CONCURRENT_MODIFICATION",
					}),
					{
						status: 412,
						headers: { "Content-Type": "application/problem+json" },
					},
				);
			}) as typeof fetch;

			const success = await store
				.getState()
				.commitChanges("Commit with stale head", conflictFetch);
			expect(success).toBe(false);
			// Invariants preserved under 412 conflict
			expect(store.getState().hasUnsavedChanges).toBe(true);
			expect(store.getState().activeSession?.head_sha256).toBe("a".repeat(64));
			expect(store.getState().committedNodes).toHaveLength(0);
			expect(
				store
					.getState()
					.toasts.some(
						(t) => t.status === 412 || t.code === "CONCURRENT_MODIFICATION",
					),
			).toBe(true);
		});
	});

	describe("Round-3 Audit Remediations (S6-CRIT-01..02, S6-MAJ-01, P14c..P14e)", () => {
		it("enforces exact canonicalizeGraphADict JCS hash parity without fabricating fields and sorts external_mappings by (target_uri, predicate ?? '', scheme) (P14c::MAJ-02)", async () => {
			const sha256Hex = async (text: string): Promise<string> => {
				const buf = await crypto.subtle.digest(
					"SHA-256",
					new TextEncoder().encode(text),
				);
				return Array.from(new Uint8Array(buf))
					.map((b) => b.toString(16).padStart(2, "0"))
					.join("");
			};

			// 1. Default genesis 0.0.0 ("general" / "Enterprise Architecture") matches 1916a5c6...
			const defaultGenesisJcs = generateLocalExport(
				"rfc8785",
				"0.0.0",
				[],
				[],
				{
					session_id: "sess-default",
					domain_namespace: "general",
					enterprise_owner: "Enterprise Architecture",
					status: "ACTIVE",
					active_version: "0.0.0",
					head_sha256:
						"1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9",
					created_at: "2026-11-12",
					updated_at: "2026-11-12",
				},
			);
			expect(await sha256Hex(defaultGenesisJcs)).toBe(
				"1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9",
			);

			// 2. Custom namespace ("wealth_mgmt" / "Global Wealth Advisory") matches Python ontology_hashing.py
			const customGenesisJcs = generateLocalExport("rfc8785", "0.0.0", [], [], {
				session_id: "sess-pe",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Global Wealth Advisory",
				status: "ACTIVE",
				active_version: "0.0.0",
				head_sha256:
					"9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142",
				created_at: "2026-11-12",
				updated_at: "2026-11-12",
			});
			expect(await sha256Hex(customGenesisJcs)).toBe(
				"9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142",
			);

			// 3. No fabricated grain_semantics, preserves empty concept description vs pruning _ATTR_OPT / _REL_EXT,
			//    preserves reference_pack_id/sha256, and sorts external_mappings by (target_uri, predicate ?? "", scheme)
			const node: ConceptNode = {
				id: "urn:ontology:entity:fund",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Fund entity",
				description: "",
				grounding_status: "GROUNDED",
				attributes: [
					{
						attribute_name: "fund_id",
						data_type: "string",
						is_optional: false,
						description: "",
						source_hints: ["lpa_sec_1"],
					},
				],
				synonyms: [],
				external_mappings: [
					{
						scheme: "ISO20022",
						target_uri: "urn:ext:a",
						predicate: "skos:exactMatch",
						confidence: 0.9,
					},
					{
						scheme: "FIBO",
						target_uri: "urn:ext:a",
						predicate: "skos:closeMatch",
						confidence: 0.85,
						reference_pack_id: "fibo-core-bebd6ae8",
						reference_pack_sha256: "bebd6ae8".repeat(8),
					},
					{
						scheme: "BIAN",
						target_uri: "urn:ext:a",
						confidence: 0.7,
					},
				],
			};
			const edgeWithoutGrain: RelationshipEdge = {
				id: "urn:ontology:relationship:fund_has_lp",
				relationship_uri: "urn:ontology:relationship:fund_has_lp",
				relationship_name: "fund_has_lp",
				target_link: "lnk_fund_has_lp",
				source_concept_uri: "urn:ontology:entity:fund",
				target_concept_uri: "urn:ontology:entity:fund",
				description: "",
				cardinality: "",
			};
			const canonical = canonicalizeGraphADict([node], [edgeWithoutGrain], {
				session_id: "s-canon",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Owner",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: "2026-11-12",
				updated_at: "2026-11-12",
			});
			const relOut = (
				canonical.canonical_relationships as Array<Record<string, unknown>>
			)[0]!;
			expect("grain_semantics" in relOut).toBe(false);
			expect("description" in relOut).toBe(false);
			expect("cardinality" in relOut).toBe(false);

			const cbcOut = (
				canonical.canonical_business_concepts as Array<Record<string, unknown>>
			)[0]!;
			expect(cbcOut.description).toBe("");
			const attrOut = (cbcOut.attributes as Array<Record<string, unknown>>)[0]!;
			expect("description" in attrOut).toBe(false);
			expect(attrOut.source_hints).toEqual(["lpa_sec_1"]);

			const mappingsOut = cbcOut.external_mappings as Array<
				Record<string, unknown>
			>;
			// Sorted by (target_uri="urn:ext:a", predicate ?? "" -> "", "skos:closeMatch", "skos:exactMatch")
			expect(mappingsOut.map((m) => m.scheme)).toEqual([
				"BIAN",
				"FIBO",
				"ISO20022",
			]);
			expect(mappingsOut[1]?.reference_pack_id).toBe("fibo-core-bebd6ae8");
		});

		it("handles all 9 named SSE events, bypasses EventSource when Bearer authToken is set, and resets flags on polling failure/timeout (S6-CRIT-01, P14c::MAJ-01)", async () => {
			// Part A: Named SSE events (job_started, stage, chunk_parsed, job_completed, JOB_ABANDONED_SERVER_CRASH)
			const prevEs = (globalThis as Record<string, unknown>).EventSource;
			const createdEsInstances: Array<{
				url: string;
				emitNamed: (name: string, data: unknown) => Promise<void>;
			}> = [];
			class MockNamedEventSource {
				url: string;
				listeners = new Map<string, Array<(ev: MessageEvent) => void>>();
				onmessage: ((ev: MessageEvent) => void) | null = null;
				onerror: (() => void) | null = null;
				constructor(url: string) {
					this.url = url;
					createdEsInstances.push(this);
				}
				addEventListener(name: string, cb: (ev: MessageEvent) => void) {
					const arr = this.listeners.get(name) || [];
					arr.push(cb);
					this.listeners.set(name, arr);
				}
				async emitNamed(name: string, data: unknown) {
					const str = typeof data === "string" ? data : JSON.stringify(data);
					for (const cb of this.listeners.get(name) || []) {
						cb({ data: str } as MessageEvent);
					}
					await new Promise((r) => setTimeout(r, 10));
				}
				close() {}
			}
			(globalThis as Record<string, unknown>).EventSource =
				MockNamedEventSource;
			try {
				const sseStore = createStudioStore({
					initialSession: {
						session_id: "s-sse",
						domain_namespace: "pe",
						enterprise_owner: "Owner",
						status: "ACTIVE",
						active_version: "0.0.0",
						head_sha256: "1".repeat(64),
						created_at: "2026-11-12",
						updated_at: "2026-11-12",
					},
				});
				const parseFetch: typeof fetch = (async () =>
					new Response(
						JSON.stringify({
							job_id: "job-sse-named",
							status: "RUNNING",
							pct_complete: 5,
						}),
						{ status: 202 },
					)) as typeof fetch;

				await sseStore.getState().startParse("auto", parseFetch);
				expect(createdEsInstances).toHaveLength(1);
				expect(sseStore.getState().parsingState.progress_pct).toBe(5);

				const es = createdEsInstances[0]!;
				await es.emitNamed("job_started", { pct_complete: 12 });
				await es.emitNamed("stage", { stage: "chunking", pct_complete: 40 });
				await es.emitNamed("chunk_parsed", { pct_complete: 65 });
				expect(sseStore.getState().parsingState.progress_pct).toBe(65);
				expect(sseStore.getState().parsingState.chunks_count).toBe(1);

				await es.emitNamed("job_completed", {
					pct_complete: 100,
					chunks_count: 7,
				});
				expect(sseStore.getState().parsingState.status).toBe("completed");
				expect(sseStore.getState().parsingState.chunks_count).toBe(7);

				// Named crash event resets isClarifying and emits toast
				const crashFetch: typeof fetch = (async () =>
					new Response(
						JSON.stringify({ job_id: "job-crash-1", status: "RUNNING" }),
						{ status: 202 },
					)) as typeof fetch;
				await sseStore.getState().startClarify({}, crashFetch);
				expect(sseStore.getState().isClarifying).toBe(true);
				const crashEs = createdEsInstances[1]!;
				await crashEs.emitNamed("JOB_ABANDONED_SERVER_CRASH", {
					detail: "Worker crashed during restart",
				});
				expect(sseStore.getState().isClarifying).toBe(false);
				expect(
					sseStore
						.getState()
						.toasts.some((t) => t.code === "JOB_ABANDONED_SERVER_CRASH"),
				).toBe(true);

				// Part B: Explicit Bearer authToken bypasses EventSource directly to polling
				createdEsInstances.length = 0;
				const bearerStore = createStudioStore({
					initialSession: {
						session_id: "s-bearer",
						domain_namespace: "pe",
						enterprise_owner: "Owner",
						status: "ACTIVE",
						active_version: "0.0.0",
						head_sha256: "2".repeat(64),
						created_at: "2026-11-12",
						updated_at: "2026-11-12",
					},
					initialPollIntervalMs: 15,
					maxPollAttempts: 2,
					maxPollErrorRetries: 1,
				});
				bearerStore.getState().setAuthToken("remote-jwt-token");
				let pollCalls = 0;
				const timeoutPollFetch: typeof fetch = (async (url) => {
					if (String(url).endsWith("/construct")) {
						return new Response(
							JSON.stringify({ job_id: "job-poll-timeout", status: "RUNNING" }),
							{ status: 202 },
						);
					}
					pollCalls += 1;
					return new Response(
						JSON.stringify({ status: "RUNNING", pct_complete: 50 }),
						{ status: 200 },
					);
				}) as typeof fetch;

				await bearerStore.getState().startConstruct({}, timeoutPollFetch);
				expect(createdEsInstances).toHaveLength(0); // Bypassed EventSource!
				await new Promise((r) => setTimeout(r, 120));
				expect(pollCalls).toBe(2);
				expect(bearerStore.getState().isConstructing).toBe(false);
				expect(
					bearerStore
						.getState()
						.toasts.some((t) => t.code === "JOB_POLL_TIMEOUT"),
				).toBe(true);
			} finally {
				(globalThis as Record<string, unknown>).EventSource = prevEs;
			}
		});

		it("loads proposals in selectSession and attaches If-Match + base_sha256 on startConstruct({ auto_commit: true }) (S6-CRIT-02)", async () => {
			const headA = "a".repeat(64);
			const headB = "b".repeat(64);
			const store = createStudioStore({
				initialSession: {
					session_id: "s-crit02",
					domain_namespace: "pe",
					enterprise_owner: "Owner",
					status: "ACTIVE",
					active_version: "0.0.0",
					head_sha256: headA,
					created_at: "2026-11-12",
					updated_at: "2026-11-12",
				},
			});

			let constructHeaders: Record<string, string> = {};
			let constructBody: Record<string, unknown> | null = null;
			let sessionReloadedAfterAutoCommit = false;

			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/construct")) {
					constructHeaders = (init?.headers || {}) as Record<string, string>;
					constructBody = JSON.parse(String(init?.body));
					return new Response(
						JSON.stringify({
							job_id: "job-autocommit",
							status: "COMPLETED",
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.endsWith("/sessions/s-crit02")) {
					if (constructBody) sessionReloadedAfterAutoCommit = true;
					return new Response(
						JSON.stringify({
							session_id: "s-crit02",
							domain_namespace: "pe",
							enterprise_owner: "Owner",
							status: "ACTIVE",
							active_version: constructBody ? "1.0.0" : "0.0.0",
							head_sha256: constructBody ? headB : headA,
							created_at: "2026-11-12",
							updated_at: "2026-11-12",
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.endsWith("/proposals")) {
					return new Response(
						JSON.stringify({
							proposals: [
								{
									proposal_id: "prop-pending-1",
									status: "PENDING",
									base_sha256: headA,
									target_version: "1.0.0",
									ops_count: 3,
									created_at: "2026-11-12",
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (u.includes("/graph") || u.includes("/export")) {
					return new Response(
						JSON.stringify({
							canonical_business_concepts: [],
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				}
				if (
					u.endsWith("/questions") ||
					u.endsWith("/answers") ||
					u.endsWith("/sources")
				) {
					return new Response(JSON.stringify([]), { status: 200 });
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().selectSession("s-crit02", mockFetch);
			expect(store.getState().proposals).toHaveLength(1);
			expect(store.getState().proposals[0]?.proposal_id).toBe("prop-pending-1");

			await store
				.getState()
				.startConstruct({ auto_commit: true }, mockFetch);
			expect(constructHeaders["If-Match"]).toBe(`"${headA}"`);
			expect(constructBody?.mode).toBe("deterministic");
			expect(constructBody?.base_sha256).toBe(headA);
			expect(sessionReloadedAfterAutoCommit).toBe(true);
			expect(store.getState().activeSession?.head_sha256).toBe(headB);
		});

		it("syncs OCC baseline in loadSessions when clean, pins head_sha256 when dirty, blocks acceptProposal when dirty, and detects structural edge changes in deleteConcept (P14d::MAJ-01..03)", async () => {
			const sha1 = "1".repeat(64);
			const sha2 = "2".repeat(64);
			const sha3 = "3".repeat(64);
			const store = createStudioStore({
				initialSession: {
					session_id: "s-occ",
					domain_namespace: "pe",
					enterprise_owner: "Owner",
					status: "ACTIVE",
					active_version: "1.0.0",
					head_sha256: sha1,
					created_at: "2026-11-12",
					updated_at: "2026-11-12",
				},
			});

			let serverHead = sha2;
			let serverConceptName = "Server Concept V2";
			const mockOccFetch: typeof fetch = (async (url) => {
				const u = String(url);
				if (u.endsWith("/sessions")) {
					return new Response(
						JSON.stringify([
							{
								session_id: "s-occ",
								domain_namespace: "pe",
								enterprise_owner: "Owner",
								status: "ACTIVE",
								active_version: serverHead === sha2 ? "1.1.0" : "1.2.0",
								head_sha256: serverHead,
								created_at: "2026-11-12",
								updated_at: "2026-11-12",
							},
						]),
						{ status: 200 },
					);
				}
				if (u.endsWith("/sessions/s-occ")) {
					return new Response(
						JSON.stringify({
							session_id: "s-occ",
							domain_namespace: "pe",
							enterprise_owner: "Owner",
							status: "ACTIVE",
							active_version: serverHead === sha2 ? "1.1.0" : "1.2.0",
							head_sha256: serverHead,
							created_at: "2026-11-12",
							updated_at: "2026-11-12",
						}),
						{ status: 200 },
					);
				}
				if (u.includes("/graph") || u.includes("/export")) {
					return new Response(
						JSON.stringify({
							canonical_business_concepts: [
								{
									concept_uri: "urn:ontology:entity:fund",
									concept_name: serverConceptName,
									elm_archetype: "CORE_BUSINESS_CONCEPT",
									definition: "Fund",
									external_mappings: [
										{
											scheme: "FIBO",
											target_uri: "fibo:Fund",
											predicate: "skos:exactMatch",
											confidence: 0.95,
										},
									],
								},
								{
									concept_uri: "urn:ontology:entity:lp",
									concept_name: "LP",
									elm_archetype: "PARTY",
									definition: "LP",
									external_mappings: [
										{
											scheme: "FIBO",
											target_uri: "fibo:LP",
											predicate: "skos:exactMatch",
											confidence: 0.95,
										},
									],
								},
							],
							canonical_relationships: [
								{
									relationship_uri: "urn:ontology:relationship:fund_has_lp_v1",
									relationship_name: "has",
									target_link: "lnk_fund_has_lp_v1",
									endpoints: [
										"urn:ontology:entity:fund",
										"urn:ontology:entity:lp",
									],
								},
							],
						}),
						{ status: 200 },
					);
				}
				return new Response(JSON.stringify([]), { status: 200 });
			}) as typeof fetch;

			// 1. Clean state: loadSessions detects sha1 -> sha2 and advances both head_sha256 and committedNodes!
			await store.getState().loadSessions(mockOccFetch);
			expect(store.getState().activeSession?.head_sha256).toBe(sha2);
			expect(store.getState().committedNodes[0]?.concept_name).toBe(
				"Server Concept V2",
			);
			expect(store.getState().committedEdges).toHaveLength(1);

			// 2. P14d::MAJ-03: Replace committed edge with a different edge (same count = 1), add temp concept, then delete temp concept
			store
				.getState()
				.deleteRelationship("urn:ontology:relationship:fund_has_lp_v1");
			store.getState().addRelationship({
				id: "urn:ontology:relationship:fund_owns_lp_v2",
				relationship_uri: "urn:ontology:relationship:fund_owns_lp_v2",
				relationship_name: "owns",
				target_link: "lnk_fund_owns_lp_v2",
				source_concept_uri: "urn:ontology:entity:fund",
				target_concept_uri: "urn:ontology:entity:lp",
			});
			store.getState().addConcept({
				id: "urn:ontology:entity:temp",
				concept_uri: "urn:ontology:entity:temp",
				concept_name: "Temp",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Temp",
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			});
			store.getState().deleteConcept("urn:ontology:entity:temp");
			// Edge count matches committedEdges.length (1 === 1), yet edge URI changed -> MUST remain dirty!
			expect(store.getState().edges).toHaveLength(1);
			expect(store.getState().committedEdges).toHaveLength(1);
			expect(store.getState().hasUnsavedChanges).toBe(true);

			// 3. P14d::MAJ-01 (dirty branch): Server advances to sha3 while canvas is dirty -> activeSession stays pinned to sha2!
			serverHead = sha3;
			await store.getState().loadSessions(mockOccFetch);
			expect(store.getState().activeSession?.head_sha256).toBe(sha2);
			expect(store.getState().sessions[0]?.head_sha256).toBe(sha3);

			// 4. P14d::MAJ-02: acceptProposal refuses to clobber dirty canvas edits unless { force: true }
			let acceptNetworkCalled = false;
			const spyAcceptFetch: typeof fetch = (async () => {
				acceptNetworkCalled = true;
				return new Response("{}", { status: 200 });
			}) as typeof fetch;
			const accepted = await store
				.getState()
				.acceptProposal("prop-1", "Try accept while dirty", spyAcceptFetch);
			expect(accepted).toBe(false);
			expect(acceptNetworkCalled).toBe(false);
			expect(store.getState().toasts[0]?.code).toBe("UNSAVED_CHANGES");
		});

		it("allows candidate user_asserted FIBO Path A mappings through commitChanges to server GroundingVerifier and classifies SemVer bumps accurately (S6-MAJ-01, P14e::MAJ-01)", async () => {
			const baseSession: StudioSessionSummary = {
				session_id: "s-semver",
				domain_namespace: "pe",
				enterprise_owner: "Owner",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				created_at: "2026-11-12",
				updated_at: "2026-11-12",
			};

			// Part A: S6-MAJ-01 — New concept with user_asserted FIBO skos:exactMatch proceeds to POST /delta with confidence stripped
			const s6Store = createStudioStore({ initialSession: baseSession });
			s6Store.getState().addConcept({
				id: "urn:ontology:entity:commitment",
				concept_uri: "urn:ontology:entity:commitment",
				concept_name: "Capital Commitment",
				elm_archetype: "AGREEMENT",
				definition: "LP commitment",
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "fibo",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractualCommitment",
						predicate: "skos:exactMatch",
						confidence: 0.85,
						provenance: "user_asserted",
					},
				],
			});
			let postedDelta: Record<string, unknown> | null = null;
			const mockDeltaFetch: typeof fetch = (async (_url, init) => {
				postedDelta = JSON.parse(String(init?.body));
				return new Response(
					JSON.stringify({
						target_version: "1.1.0",
						target_sha256: "b".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const committedS6 = await s6Store
				.getState()
				.commitChanges("Add user-asserted FIBO concept", mockDeltaFetch);
			expect(committedS6).toBe(true);
			expect(postedDelta).not.toBeNull();
			expect(postedDelta?.target_version).toBe("1.1.0");
			const addOp = (
				postedDelta?.operations as Array<Record<string, unknown>>
			)[0]!;
			const sentMapping = (
				addOp.external_mappings as Array<Record<string, unknown>>
			)[0]!;
			expect("confidence" in sentMapping).toBe(false);
			expect(s6Store.getState().nodes[0]?.grounding_status).toBe("GROUNDED");

			// Part B: P14e::MAJ-01 — refine_definition -> MINOR (1.1.0), remove_external_mapping -> MAJOR (2.0.0)
			const baseConcept: ConceptNode = {
				id: "urn:ontology:entity:fund",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				elm_archetype: "CORE_BUSINESS_CONCEPT",
				definition: "Initial definition",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri: "fibo:FundPrimary",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
					{
						scheme: "ISO20022",
						target_uri: "iso:Secondary",
						predicate: "skos:closeMatch",
						confidence: 0.8,
					},
				],
			};
			const semverStore = createStudioStore({
				initialSession: baseSession,
				userRole: "admin",
			});
			semverStore.setState({
				committedNodes: [baseConcept],
				nodes: [{ ...baseConcept, definition: "Refined steward definition" }],
				hasUnsavedChanges: true,
			});
			await semverStore
				.getState()
				.commitChanges("Refine definition", mockDeltaFetch);
			expect(postedDelta?.target_version).toBe("1.1.0");

			// Now remove iso:Secondary mapping from 1.1.0 -> remove_external_mapping MUST bump MAJOR -> 2.0.0!
			const v11Concept = semverStore.getState().committedNodes[0]!;
			semverStore.setState({
				nodes: [
					{
						...v11Concept,
						external_mappings: v11Concept.external_mappings.slice(0, 1),
					},
				],
				hasUnsavedChanges: true,
			});
			await semverStore
				.getState()
				.commitChanges("Remove secondary mapping", mockDeltaFetch);
			expect(postedDelta?.target_version).toBe("2.0.0");
		});
	});
});
