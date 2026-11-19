/**
 * Wave R3-C Hardening & Regression Suite
 * (apps/opendv-web-workbench/tests/wave-r3c-hardening.test.tsx)
 *
 * Covers:
 * 1. Confidential Source Upload (do_not_send_to_agent FormData & SourceDocument mapping)
 * 2. Strict Path A Grounding, Grandfather Mutation Revocation, Feedback & Lossless Export
 * 3. Genesis 0.0.0 Commits, Deterministic op_id, Mapping Payloads & Full Visual Editor / Admin Delta Diffing
 * 4. Parallel Clarification Q&A, SSE Job Progress + Polling Fallback, Candidate Proposals & 0.0.0 Diff Short-Circuit
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	VisualEditorDrawer,
	validateConceptEdit,
} from "../src/ontology/components/VisualEditorDrawer";
import {
	EMPTY_ANSWERS_SHA256,
	canonicalizeGraphADict,
	computeQualityScorecard,
	createStudioStore,
	deriveGroundingStatus,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

const GENESIS_SHA256 =
	"1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9";

function makeSession(
	overrides?: Partial<StudioSessionSummary>,
): StudioSessionSummary {
	return {
		session_id: "sess-r3c-1",
		domain_namespace: "private_equity",
		enterprise_owner: "Steward Team",
		status: "ACTIVE",
		active_version: "1.0.0",
		head_sha256: "a".repeat(64),
		created_at: "2026-11-12T00:00:00Z",
		updated_at: "2026-11-12T00:00:00Z",
		...overrides,
	};
}

function makeGroundedConcept(overrides?: Partial<ConceptNode>): ConceptNode {
	return {
		id: "urn:ontology:entity:fund",
		concept_uri: "urn:ontology:entity:fund",
		concept_name: "Fund",
		elm_archetype: "CORE_BUSINESS_CONCEPT",
		definition: "Pooled investment vehicle.",
		description: "Primary private equity fund vehicle.",
		target_hub: "hub_fund",
		parent_concept_uri: null,
		confidence: 0.92,
		grounding_status: "GROUNDED",
		attributes: [
			{
				attribute_name: "fund_id",
				data_type: "string",
				description: "Business key",
				is_optional: false,
			},
		],
		synonyms: ["Investment Vehicle"],
		external_mappings: [
			{
				scheme: "FIBO",
				target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
				predicate: "skos:exactMatch",
				confidence: 0.92,
				mapping_confidence: 0.92,
				pinned: true,
			},
		],
		...overrides,
	};
}

describe("Wave R3-C: Web Workbench Hardening & End-to-End Contract Alignment", () => {
	const originalEventSource = (globalThis as Record<string, unknown>)
		.EventSource;

	beforeEach(() => {
		studioStore.getState().reset();
	});

	afterEach(() => {
		studioStore.getState().reset();
		if (originalEventSource === undefined) {
			(globalThis as Record<string, unknown>).EventSource = undefined;
		} else {
			(globalThis as Record<string, unknown>).EventSource = originalEventSource;
		}
	});

	// =========================================================================
	// 2. CONFIDENTIAL SOURCE UPLOAD & INVENTORY MAPPING
	// =========================================================================
	describe("2. Confidential Source Upload & Drag Reset (CRIT-02, MINOR-03)", () => {
		it("sends do_not_send_to_agent in FormData (not 'confidential') and maps do_not_send_to_agent onto SourceDocument.confidential", async () => {
			const store = createStudioStore({ initialSession: makeSession() });
			let capturedFormData: FormData | null = null;

			const mockFetch: typeof fetch = (async (
				url: string | URL | Request,
				init?: RequestInit,
			) => {
				if (init?.method === "POST") {
					capturedFormData = init.body as FormData;
					return new Response(
						JSON.stringify({
							source_id: "src-conf-1",
							filename: "lpa_schedule.pdf",
							mime_type: "application/pdf",
							byte_size: 128,
							sha256: "b".repeat(64),
							do_not_send_to_agent: true,
							uploaded_at: "2026-11-12T00:00:00Z",
						}),
						{ status: 201 },
					);
				}
				if (String(url).endsWith("/sources")) {
					return new Response(
						JSON.stringify({
							sources: [
								{
									source_id: "src-conf-1",
									filename: "lpa_schedule.pdf",
									mime_type: "application/pdf",
									byte_size: 128,
									sha256: "b".repeat(64),
									do_not_send_to_agent: true,
									uploaded_at: "2026-11-12T00:00:00Z",
								},
							],
						}),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			const uploaded = await store.getState().uploadSource(
				{
					name: "lpa_schedule.pdf",
					type: "application/pdf",
					content: "confidential LPA text",
				},
				true,
				mockFetch,
			);

			expect(capturedFormData).not.toBeNull();
			expect(capturedFormData?.get("do_not_send_to_agent")).toBe("true");
			expect(capturedFormData?.get("confidential")).toBeNull();
			expect(uploaded?.confidential).toBe(true);
			expect(uploaded?.do_not_send_to_agent).toBe(true);

			await store.getState().loadSources(mockFetch);
			expect(store.getState().sources[0]?.confidential).toBe(true);
			expect(store.getState().sources[0]?.do_not_send_to_agent).toBe(true);

		});
	});

	// =========================================================================
	// 3. STRICT PATH A GROUNDING, WAIVERS, FEEDBACK & EXPORT
	// =========================================================================
	describe("3. Strict Path A Grounding, Per-Concept Waivers, Feedback & Export (P14b::MAJ-01..02, P14c::MAJ-02..03, CRIT-05, MIN-02)", () => {
		it("enforces FIBO scheme + skos:exactMatch/closeMatch + numeric confidence >= 0.65 and rejects broadMatch, missing confidence, and user_asserted", () => {
			// Valid exactMatch & closeMatch (including full SKOS IRI and lowercase fibo)
			expect(
				deriveGroundingStatus([
					{
						scheme: "fibo",
						target_uri: "fibo:Fund",
						predicate: "skos:closeMatch",
						confidence: 0.65,
					},
				]).grounding_status,
			).toBe("GROUNDED");
			expect(
				deriveGroundingStatus([
					{
						scheme: "FIBO",
						target_uri: "fibo:Fund",
						predicate: "http://www.w3.org/2004/02/skos/core#exactMatch",
						mapping_confidence: 0.88,
					},
				]).grounding_status,
			).toBe("GROUNDED");

			// skos:broadMatch MUST NOT satisfy Path A grounding
			expect(
				deriveGroundingStatus([
					{
						scheme: "FIBO",
						target_uri: "fibo:Asset",
						predicate: "skos:broadMatch",
						confidence: 0.99,
					},
				]).grounding_status,
			).toBe("LOW_CONFIDENCE");

			// Missing confidence MUST NOT default to 0.85
			expect(
				deriveGroundingStatus([
					{
						scheme: "FIBO",
						target_uri: "fibo:Fund",
						predicate: "skos:exactMatch",
					},
				]).grounding_status,
			).toBe("UNGROUNDED");

			// Non-FIBO scheme MUST NOT satisfy Path A grounding
			expect(
				deriveGroundingStatus([
					{
						scheme: "ISO20022",
						target_uri: "iso:Fund",
						predicate: "skos:exactMatch",
						confidence: 0.95,
					},
				]).grounding_status,
			).toBe("LOW_CONFIDENCE");

			// Unverified manual drawer mapping (user_asserted) MUST NOT satisfy Path A until verified by server
			expect(
				deriveGroundingStatus([
					{
						scheme: "FIBO",
						target_uri: "fibo:Fund",
						predicate: "skos:exactMatch",
						confidence: 0.95,
						provenance: "user_asserted",
					},
				]).grounding_status,
			).toBe("UNGROUNDED");
		});

		it("revokes grandfathered_concept_uris and per-concept waivers immediately when a concept is mutated from baseline", async () => {
			const grandfatheredConcept: ConceptNode = {
				id: "urn:ontology:entity:legacy_lp",
				concept_uri: "urn:ontology:entity:legacy_lp",
				concept_name: "Legacy LP",
				elm_archetype: "PARTY",
				definition: "Imported workspace concept without FIBO mapping.",
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			// Untouched grandfathered concept passes ONT-EVO-002
			const scUntouched = computeQualityScorecard([grandfatheredConcept], [], {
				committedNodes: [grandfatheredConcept],
				grandfatheredUris: [grandfatheredConcept.concept_uri],
			});
			expect(
				scUntouched.checks.find((c) => c.rule_id === "ONT-EVO-002")?.status,
			).toBe("PASS");

			// Mutating definition revokes grandfather exemption -> ONT-EVO-002 FAIL
			const mutatedGrandfathered: ConceptNode = {
				...grandfatheredConcept,
				definition: "Mutated definition requiring Path A FIBO grounding",
			};
			const scMutated = computeQualityScorecard([mutatedGrandfathered], [], {
				committedNodes: [grandfatheredConcept],
				grandfatheredUris: [grandfatheredConcept.concept_uri],
			});
			expect(
				scMutated.checks.find((c) => c.rule_id === "ONT-EVO-002")?.status,
			).toBe("FAIL");

		});

		it("renders SKOS predicate selector and USER-ASSERTED badge in VisualEditorDrawer, and per-concept feedback", async () => {
			const nodeWithManualMapping = makeGroundedConcept({
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri:
							"https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
						predicate: "skos:closeMatch",
						confidence: 0.85,
						provenance: "user_asserted",
						pinned: true,
					},
				],
			});
			studioStore.setState({
				activeSession: makeSession(),
				userRole: "admin",
				nodes: [nodeWithManualMapping],
				selectedNodeId: nodeWithManualMapping.id,
			});

			const drawerHtml = renderToStaticMarkup(
				createElement(VisualEditorDrawer),
			);
			expect(drawerHtml).toContain(
				'data-testid="new-mapping-predicate-select"',
			);
			expect(drawerHtml).toContain("skos:exactMatch");
			expect(drawerHtml).toContain("skos:closeMatch");
			expect(drawerHtml).toContain("skos:broadMatch");
			expect(drawerHtml).toContain("skos:narrowMatch");
			expect(drawerHtml).toContain("skos:relatedMatch");
			expect(drawerHtml).toContain("USER-ASSERTED (Unverified)");
			expect(drawerHtml).toContain('data-testid="concept-description-input"');
			expect(drawerHtml).toContain('data-testid="delete-concept-btn"');

			// Verify submitFeedback posts { rating, comment, concept_uri }
			let feedbackBody: unknown = null;
			const mockFeedbackFetch: typeof fetch = (async (_url, init) => {
				feedbackBody = JSON.parse(String(init?.body));
				return new Response(JSON.stringify({ status: "recorded" }), {
					status: 200,
				});
			}) as typeof fetch;

			const okFb = await studioStore
				.getState()
				.submitFeedback(
					4,
					"Accurate FIBO alignment",
					"urn:ontology:entity:fund",
					mockFeedbackFetch,
				);
			expect(okFb).toBe(true);
			expect(feedbackBody).toEqual({
				rating: 4,
				comment: "Accurate FIBO alignment",
				concept_uri: "urn:ontology:entity:fund",
			});
		});

		it("routes rfc8785 export to server export_format=json, surfaces toast without lossy fallback on 500, and preserves endpoints/metadata in canonicalizeGraphADict", async () => {
			const store = createStudioStore({ initialSession: makeSession() });
			let requestedUrl = "";
			const mockOkExport: typeof fetch = (async (url) => {
				requestedUrl = String(url);
				return new Response('{"canonical":true}', { status: 200 });
			}) as typeof fetch;

			const jcsPayload = await store
				.getState()
				.exportOntology("rfc8785", "1.0.0", mockOkExport);
			expect(jcsPayload).toBe('{"canonical":true}');
			expect(requestedUrl).toContain("export_format=json");

			// Server 500 MUST surface toast and throw (no silent lossy client fallback!)
			const mock500Export: typeof fetch = (async () => {
				return new Response(
					JSON.stringify({
						title: "Export Failed",
						status: 500,
						detail: "RDFLib serializer failure",
						code: "EXPORT_INTERNAL_ERROR",
					}),
					{
						status: 500,
						headers: { "Content-Type": "application/problem+json" },
					},
				);
			}) as typeof fetch;

			await expect(
				store.getState().exportOntology("turtle", "1.0.0", mock500Export),
			).rejects.toThrow("RDFLib serializer failure");
			expect(
				store.getState().toasts.some((t) => t.code === "EXPORT_INTERNAL_ERROR"),
			).toBe(true);

			// canonicalizeGraphADict preserves endpoints, grain_semantics, metadata, enumerations, and references
			const edge: RelationshipEdge = {
				id: "e1",
				relationship_uri: "urn:ontology:relationship:invests_in",
				relationship_name: "invests_in",
				target_link: "lnk_investor_fund",
				source_concept_uri: "urn:ontology:entity:investor",
				target_concept_uri: "urn:ontology:entity:fund",
				endpoints: ["urn:ontology:entity:investor", "urn:ontology:entity:fund"],
				grain_semantics: "One commitment per LP per fund",
				cardinality: "many_to_many",
			};
			const canonical = canonicalizeGraphADict(
				[makeGroundedConcept({ metadata: { custom_tag: "tier1" } })],
				[edge],
				makeSession({
					reference_enumerations: [
						{ enum_uri: "urn:ontology:enum:status", values: ["OPEN"] },
					],
					standards_references: [{ ref_id: "ref-1" }],
				}),
			);
			const rels = canonical.canonical_relationships as Array<
				Record<string, unknown>
			>;
			const concepts = canonical.canonical_business_concepts as Array<
				Record<string, unknown>
			>;
			expect(rels[0]?.endpoints).toEqual([
				"urn:ontology:entity:investor",
				"urn:ontology:entity:fund",
			]);
			expect(rels[0]?.grain_semantics).toBe("One commitment per LP per fund");
			expect(
				(concepts[0]?.metadata as Record<string, unknown>)?.custom_tag,
			).toBe("tier1");
			expect((canonical.reference_enumerations as unknown[]).length).toBe(1);
			expect((canonical.standards_references as unknown[]).length).toBe(1);

		});
	});

	// =========================================================================
	// 4. VISUAL EDITOR & commitChanges FULL DIFFING & GENESIS 0.0.0
	// =========================================================================
	describe("4. Visual Editor & commitChanges Full Diffing & Genesis 0.0.0 (P14d::MAJ-01, CRIT-03)", () => {
		it("handles genesis 0.0.0 -> 1.0.0 (with add_concept) and 0.0.0 -> 0.1.0 (without add_concept), blocking missing active_version", async () => {
			// Missing active_version must fail fast
			const badStore = createStudioStore({
				initialSession: makeSession({ active_version: "" }),
			});
			badStore.setState({ nodes: [makeGroundedConcept()] });
			const badOk = await badStore.getState().commitChanges("Should fail");
			expect(badOk).toBe(false);
			expect(badStore.getState().toasts[0]?.code).toBe(
				"MISSING_ACTIVE_VERSION",
			);

			// Genesis 0.0.0 + add_concept -> target_version = "1.0.0"
			const genesisStore = createStudioStore({
				initialSession: makeSession({
					active_version: "0.0.0",
					head_sha256: GENESIS_SHA256,
				}),
			});
			genesisStore.setState({
				nodes: [makeGroundedConcept()],
				committedNodes: [],
			});

			let capturedGenesisBody: Record<string, unknown> | null = null;
			let capturedGenesisHeaders: Record<string, string> = {};
			const mockGenesisFetch: typeof fetch = (async (url, init) => {
				if (String(url).includes("/export")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict(
								genesisStore.getState().nodes,
								genesisStore.getState().edges,
								{
									...(genesisStore.getState().activeSession ?? makeSession()),
									active_version: "1.0.0",
								},
							),
						),
						{ status: 200 },
					);
				}
				if (!String(url).endsWith("/delta")) {
					return new Response("{}", { status: 404 });
				}
				capturedGenesisHeaders =
					(init?.headers as Record<string, string>) || {};
				capturedGenesisBody = JSON.parse(String(init?.body)) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						target_version: "1.0.0",
						target_sha256: "c".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok1 = await genesisStore
				.getState()
				.commitChanges("Initial genesis concept", mockGenesisFetch);
			expect(ok1).toBe(true);
			expect(capturedGenesisHeaders["If-Match"]).toBe(`"${GENESIS_SHA256}"`);
			expect(capturedGenesisBody?.base_version).toBe("0.0.0");
			expect(capturedGenesisBody?.target_version).toBe("1.0.0");
			expect(capturedGenesisBody?.parent_sha256).toBe(GENESIS_SHA256);

			// Genesis 0.0.0 + relationship only (no add_concept) -> target_version = "0.1.0"
			const relOnlyStore = createStudioStore({
				initialSession: makeSession({
					active_version: "0.0.0",
					head_sha256: GENESIS_SHA256,
				}),
			});
			relOnlyStore.setState({
				nodes: [],
				committedNodes: [],
				edges: [
					{
						id: "urn:ontology:relationship:self_rel",
						relationship_uri: "urn:ontology:relationship:self_rel",
						relationship_name: "self_rel",
						target_link: "lnk_self_rel",
						source_concept_uri: "urn:ontology:entity:a",
						target_concept_uri: "urn:ontology:entity:b",
					},
				],
				committedEdges: [],
			});

			let capturedRelOnlyBody: Record<string, unknown> | null = null;
			const mockRelOnlyFetch: typeof fetch = (async (url, init) => {
				if (String(url).includes("/export")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict(
								relOnlyStore.getState().nodes,
								relOnlyStore.getState().edges,
								{
									...(relOnlyStore.getState().activeSession ?? makeSession()),
									active_version: "0.1.0",
								},
							),
						),
						{ status: 200 },
					);
				}
				if (!String(url).endsWith("/delta")) {
					return new Response("{}", { status: 404 });
				}
				capturedRelOnlyBody = JSON.parse(String(init?.body)) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						target_version: "0.1.0",
						target_sha256: "d".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok2 = await relOnlyStore
				.getState()
				.commitChanges("Relationship draft on 0.0.0", mockRelOnlyFetch);
			expect(ok2).toBe(true);
			expect(capturedRelOnlyBody?.target_version).toBe("0.1.0");
		});

		it("emits deterministic op_id, mapping_confidence without provenance, and diffs all drawer & admin destructive operations", async () => {
			const parentNode = makeGroundedConcept({
				id: "urn:ontology:entity:vehicle",
				concept_uri: "urn:ontology:entity:vehicle",
				concept_name: "Vehicle",
			});
			const baseFund = makeGroundedConcept({
				attributes: [
					{ attribute_name: "fund_id", data_type: "string" },
					{ attribute_name: "obsolete_attr", data_type: "string" },
				],
				external_mappings: [
					{
						scheme: "FIBO",
						target_uri: "fibo:FundPrimary",
						predicate: "skos:exactMatch",
						confidence: 0.85,
						pinned: false,
					},
					{
						scheme: "FIBO",
						target_uri: "fibo:OldMapping",
						predicate: "skos:closeMatch",
						confidence: 0.7,
						pinned: false,
					},
				],
			});
			const removedConcept = makeGroundedConcept({
				id: "urn:ontology:entity:deprecated_entity",
				concept_uri: "urn:ontology:entity:deprecated_entity",
				concept_name: "Deprecated Entity",
			});
			const removedEdge: RelationshipEdge = {
				id: "urn:ontology:relationship:old_rel",
				relationship_uri: "urn:ontology:relationship:old_rel",
				relationship_name: "old_rel",
				target_link: "lnk_old_rel",
				source_concept_uri: baseFund.concept_uri,
				target_concept_uri: removedConcept.concept_uri,
			};

			// Working draft mutates archetype, parent, description, definition, synonyms, attributes, mappings, and removes concept/edge
			const editedFund: ConceptNode = {
				...baseFund,
				elm_archetype: "AGREEMENT",
				parent_concept_uri: parentNode.concept_uri,
				description: "Updated extended stewardship description",
				definition: "Refined pooled fund definition",
				synonyms: ["Investment Vehicle", "PE Partnership"],
				attributes: [
					{ attribute_name: "fund_id", data_type: "string" },
					{ attribute_name: "vintage_year", data_type: "integer" },
				],
				external_mappings: [
					{
						// Rescored + repinned existing mapping
						scheme: "FIBO",
						target_uri: "fibo:FundPrimary",
						predicate: "skos:exactMatch",
						confidence: 0.96,
						pinned: true,
					},
					{
						// Newly added verified mapping -> sends mapping_confidence & confidence without provenance
						scheme: "FIBO",
						target_uri: "fibo:NewMapping",
						predicate: "skos:closeMatch",
						confidence: 0.88,
						pinned: true,
					},
					{
						// Newly added manual drawer mapping -> strips provenance & leaves out client confidence so server computes it (P14b::MAJ-01)
						scheme: "FIBO",
						target_uri: "fibo:ManualMapping",
						predicate: "skos:broadMatch",
						confidence: 0.85,
						provenance: "user_asserted",
						pinned: false,
					},
				],
			};

			// Admin validateConceptEdit allows destructive edits when allowDestructive: true
			expect(
				validateConceptEdit(editedFund, baseFund, [parentNode, editedFund], {
					allowDestructive: true,
				}),
			).toEqual([]);

			const store = createStudioStore({ initialSession: makeSession() });
			store.setState({
				userRole: "admin",
				committedNodes: [parentNode, baseFund, removedConcept],
				committedEdges: [removedEdge],
				nodes: [parentNode, editedFund],
				edges: [],
				hasUnsavedChanges: true,
			});

			let capturedPayload: {
				target_version: string;
				operations: Array<Record<string, unknown>>;
			} | null = null;
			const mockAdminCommit: typeof fetch = (async (url, init) => {
				if (String(url).includes("/export")) {
					return new Response(
						JSON.stringify(
							canonicalizeGraphADict([parentNode, editedFund], [], {
								...(store.getState().activeSession ?? makeSession()),
								active_version: "2.0.0",
							}),
						),
						{ status: 200 },
					);
				}
				if (!String(url).endsWith("/delta")) {
					return new Response("{}", { status: 404 });
				}
				capturedPayload = JSON.parse(String(init?.body));
				return new Response(
					JSON.stringify({
						target_version: "2.0.0",
						target_sha256: "e".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			const ok = await store
				.getState()
				.commitChanges("Admin comprehensive refactor", mockAdminCommit);
			expect(ok).toBe(true);
			expect(capturedPayload).not.toBeNull();
			expect(capturedPayload?.target_version).toBe("2.0.0");

			const ops = capturedPayload?.operations;
			expect(ops.length).toBeGreaterThanOrEqual(11);
			// Every op MUST have non-empty op_id
			for (const op of ops) {
				expect(typeof op.op_id).toBe("string");
				expect((op.op_id as string).length).toBeGreaterThan(0);
			}

			const emittedTypes = ops.map((o) => o.op_type);
			expect(emittedTypes).toContain("set_parent_concept");
			expect(emittedTypes).toContain("set_concept_elm_archetype");
			expect(emittedTypes).toContain("revise_description");
			expect(emittedTypes).toContain("refine_definition");
			expect(emittedTypes).toContain("add_synonym");
			expect(emittedTypes).toContain("add_attribute");
			expect(emittedTypes).toContain("remove_attribute");
			expect(emittedTypes).toContain("add_external_mapping");
			expect(emittedTypes).toContain("rescore_external_mapping");
			expect(emittedTypes).toContain("repin_external_mapping");
			expect(emittedTypes).toContain("remove_external_mapping");
			expect(emittedTypes).toContain("remove_concept");
			expect(emittedTypes).toContain("remove_relationship");

			const addMapOp = ops.find(
				(o) =>
					o.op_type === "add_external_mapping" &&
					o.target_uri === "fibo:NewMapping",
			);
			expect(addMapOp?.mapping_confidence).toBe(0.88);
			expect(addMapOp?.confidence).toBe(0.88);
			expect("provenance" in (addMapOp || {})).toBe(false);

			const manualMapOp = ops.find(
				(o) =>
					o.op_type === "add_external_mapping" &&
					o.target_uri === "fibo:ManualMapping",
			);
			expect(manualMapOp?.mapping_confidence).toBeUndefined();
			expect("provenance" in (manualMapOp || {})).toBe(false);
		});
	});

	// =========================================================================
	// 5. CLARIFICATIONS, SSE + POLLING JOBS, PROPOSALS & DIFF 0.0.0
	// =========================================================================
	describe("5. Clarifications, SSE Job Progress, Proposals & 0.0.0 Diff (P14a::MAJ-01, CRIT-04, MIN-04)", () => {
		it("fetches /questions and /answers in parallel, defaults EMPTY_ANSWERS_SHA256, and submits answers with selected_options, user_text, and If-Match", async () => {
			const store = createStudioStore({ initialSession: makeSession() });
			const fetchedPaths: string[] = [];
			let submittedAnswerBody: Record<string, unknown> | null = null;
			let submittedIfMatch = "";

			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				fetchedPaths.push(`${method} ${u}`);

				if (method === "GET" && u.endsWith("/questions")) {
					return new Response(
						JSON.stringify({
							questions: [
								{
									question_id: "q_advisor_1",
									focus_concept: "Advisor",
									prompt: "Should Advisor be a subtype of LegalEntity?",
									options: [
										{
											option_id: "opt_yes",
											label: "Yes, subtype",
											rationale: "Inherits LEI",
										},
									],
									evidence_citations: [
										{
											source_id: "src-1",
											chunk_id: "chk-1",
											excerpt: "Advisor is a registered entity",
										},
									],
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (method === "GET" && u.endsWith("/answers")) {
					// Server returns empty answers without answers_sha256 -> store defaults to EMPTY_ANSWERS_SHA256
					return new Response(JSON.stringify({ answers: [] }), { status: 200 });
				}
				if (method === "POST" && u.endsWith("/answers")) {
					submittedIfMatch =
						(init?.headers as Record<string, string>)?.["If-Match"] || "";
					submittedAnswerBody = JSON.parse(String(init?.body));
					return new Response(
						JSON.stringify({ answers_sha256: "f".repeat(64) }),
						{ status: 200 },
					);
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			await store.getState().loadClarifications(mockFetch);
			expect(
				fetchedPaths.some((p) =>
					p.endsWith(
						"GET /api/v1/ontology/studio/sessions/sess-r3c-1/questions",
					),
				),
			).toBe(true);
			expect(
				fetchedPaths.some((p) =>
					p.endsWith("GET /api/v1/ontology/studio/sessions/sess-r3c-1/answers"),
				),
			).toBe(true);
			expect(store.getState().answers_sha256).toBe(EMPTY_ANSWERS_SHA256);
			expect(store.getState().clarifications[0]?.concept_name).toBe("Advisor");
			expect(store.getState().clarifications[0]?.rationale_options[0]?.id).toBe(
				"opt_yes",
			);
			expect(store.getState().clarifications[0]?.status).toBe("PENDING");

			const okAns = await store.getState().submitAnswer(
				{
					question_id: "q_advisor_1",
					selected_option_ids: ["opt_yes"],
					custom_answer_text: "Confirmed by legal counsel",
				},
				undefined,
				mockFetch,
			);
			expect(okAns).toBe(true);
			expect(submittedIfMatch).toBe(`"${EMPTY_ANSWERS_SHA256}"`);
			expect(submittedAnswerBody).toEqual({
				answers: [
					{
						question_id: "q_advisor_1",
						selected_options: ["opt_yes"],
						user_text: "Confirmed by legal counsel",
					},
				],
				expected_answers_sha256: EMPTY_ANSWERS_SHA256,
			});
			expect(store.getState().clarifications[0]?.status).toBe("ANSWERED");
		});

		it("streams SSE job events via EventSource for startConstruct and manages proposals (load, rebase, accept, reject)", async () => {
			class MockEventSource {
				static instances: MockEventSource[] = [];
				url: string;
				withCredentials: boolean;
				onmessage: ((ev: { data: string }) => void) | null = null;
				onerror: (() => void) | null = null;
				listeners: Record<string, Array<(ev: { data: string }) => void>> = {};
				closed = false;

				constructor(url: string, opts?: { withCredentials?: boolean }) {
					this.url = url;
					this.withCredentials = Boolean(opts?.withCredentials);
					MockEventSource.instances.push(this);
				}
				addEventListener(
					type: string,
					cb: (ev: { data: string }) => void,
				): void {
					this.listeners[type] = this.listeners[type] || [];
					this.listeners[type]?.push(cb);
				}
				emit(type: string, payload: unknown): void {
					const data = JSON.stringify(payload);
					if (type === "message" && this.onmessage) {
						this.onmessage({ data });
					}
					for (const cb of this.listeners[type] || []) {
						cb({ data });
					}
				}
				close(): void {
					this.closed = true;
				}
			}

			(globalThis as Record<string, unknown>).EventSource = MockEventSource;

			const store = createStudioStore({ initialSession: makeSession() });
			const apiCalls: string[] = [];

			const mockFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				apiCalls.push(`${method} ${u}`);

				if (method === "POST" && u.endsWith("/construct")) {
					return new Response(
						JSON.stringify({ job_id: "job-construct-77", status: "QUEUED" }),
						{ status: 202 },
					);
				}
				if (method === "GET" && u.endsWith("/proposals")) {
					return new Response(
						JSON.stringify({
							proposals: [
								{
									proposal_id: "prop-001",
									status: "STALE_BASE",
									base_version: "1.0.0",
									target_version: "1.1.0",
									operations_count: 3,
									rationale: "Synthesized Limited Partner & Commitment",
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (method === "POST" && u.endsWith("/proposals/prop-001/rebase")) {
					return new Response(
						JSON.stringify({ proposal_id: "prop-001", status: "PENDING" }),
						{ status: 200 },
					);
				}
				if (method === "POST" && u.endsWith("/proposals/prop-001/accept")) {
					return new Response(
						JSON.stringify({
							target_version: "1.1.0",
							target_sha256: "9".repeat(64),
						}),
						{ status: 200 },
					);
				}
				if (method === "POST" && u.endsWith("/proposals/prop-002/reject")) {
					return new Response(
						JSON.stringify({ proposal_id: "prop-002", status: "REJECTED" }),
						{ status: 200 },
					);
				}
				if (u.includes("/export")) {
					return new Response(
						JSON.stringify({
							canonical_concepts: [],
							canonical_relationships: [],
						}),
						{ status: 200 },
					);
				}
				return new Response(JSON.stringify({ sessions: [makeSession()] }), {
					status: 200,
				});
			}) as typeof fetch;

			const jobId = await store
				.getState()
				.startConstruct({ mode: "deterministic" }, mockFetch);
			expect(jobId).toBe("job-construct-77");
			expect(store.getState().isConstructing).toBe(true);
			expect(MockEventSource.instances.length).toBe(1);
			expect(MockEventSource.instances[0]?.url).toBe(
				"/api/v1/ontology/studio/jobs/job-construct-77/events",
			);
			expect(MockEventSource.instances[0]?.withCredentials).toBe(true);

			// Emit progress and terminal SUCCEEDED SSE events
			MockEventSource.instances[0]?.emit("progress", {
				status: "RUNNING",
				stage: "synthesizing_delta",
				progress_pct: 60,
				message: "Synthesizing candidate concepts",
			});
			expect(store.getState().sseEvents.length).toBeGreaterThan(0);

			MockEventSource.instances[0]?.emit("completed", {
				status: "SUCCEEDED",
				stage: "completed",
				progress_pct: 100,
				message: "Proposal prop-001 ready",
			});
			// Allow async proposal refresh microtasks to settle
			await new Promise((r) => setTimeout(r, 25));

			expect(store.getState().isConstructing).toBe(false);
			expect(MockEventSource.instances[0]?.closed).toBe(true);
			expect(store.getState().proposals[0]?.proposal_id).toBe("prop-001");

			// Rebase, Accept, and Reject actions
			expect(await store.getState().rebaseProposal("prop-001", mockFetch)).toBe(
				true,
			);
			expect(
				await store
					.getState()
					.acceptProposal("prop-001", "Approved by steward", mockFetch),
			).toBe(true);
			expect(
				await store
					.getState()
					.rejectProposal("prop-002", "Out of scope", mockFetch),
			).toBe(true);
		});

		it("short-circuits computeDiff('0.0.0', ...) to empty baseline and fetches historical target version in loadAndComputeDiff", async () => {
			const store = createStudioStore({
				initialSession: makeSession({
					active_version: "1.1.0",
					commits: [
						{
							version: "1.0.0",
							sha256: "1".repeat(64),
							parent_sha256: GENESIS_SHA256,
							rationale: "v1.0.0",
							timestamp: "2026-11-12T00:00:00Z",
						},
						{
							version: "1.1.0",
							sha256: "2".repeat(64),
							parent_sha256: "1".repeat(64),
							rationale: "v1.1.0",
							timestamp: "2026-11-12T01:00:00Z",
						},
					],
				}),
			});

			const v1Concept = makeGroundedConcept();
			store.setState({ nodes: [v1Concept], edges: [] });

			// Direct computeDiff with "0.0.0" treats baseline as empty even if baseGraph omitted
			store.getState().computeDiff("0.0.0", "1.1.0");
			expect(store.getState().diffState.records.length).toBe(1);
			expect(store.getState().diffState.records[0]?.change_type).toBe("added");

			// loadAndComputeDiff("0.0.0", "1.0.0") must NOT fetch version=0.0.0, and MUST fetch historical target version=1.0.0
			const exportRequests: string[] = [];
			const mockDiffFetch: typeof fetch = (async (url) => {
				const u = String(url);
				exportRequests.push(u);
				return new Response(
					JSON.stringify({
						canonical_concepts: [
							{
								concept_uri: "urn:ontology:entity:historical_fund",
								concept_name: "Historical Fund",
								elm_archetype: "CORE_BUSINESS_CONCEPT",
								definition: "v1.0.0 snapshot",
							},
						],
						canonical_relationships: [],
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			await store
				.getState()
				.loadAndComputeDiff("0.0.0", "1.0.0", mockDiffFetch);
			expect(exportRequests.some((u) => u.includes("version=0.0.0"))).toBe(
				false,
			);
			expect(exportRequests.some((u) => u.includes("version=1.0.0"))).toBe(
				true,
			);
			expect(store.getState().diffState.records[0]?.identifier).toBe(
				"urn:ontology:entity:historical_fund",
			);

		});

		it("falls back to GET /jobs/{job_id} polling when EventSource is unavailable", async () => {
			(globalThis as Record<string, unknown>).EventSource = undefined;
			const store = createStudioStore({ initialSession: makeSession() });

			let polledJobUrl = "";
			const mockPollFetch: typeof fetch = (async (url, init) => {
				const u = String(url);
				const method = (init?.method || "GET").toUpperCase();
				if (method === "POST" && u.endsWith("/clarify")) {
					return new Response(
						JSON.stringify({ job_id: "job-clarify-poll-1", status: "QUEUED" }),
						{ status: 202 },
					);
				}
				if (method === "GET" && u.endsWith("/jobs/job-clarify-poll-1")) {
					polledJobUrl = u;
					return new Response(
						JSON.stringify({
							job_id: "job-clarify-poll-1",
							status: "SUCCEEDED",
							progress_pct: 100,
						}),
						{ status: 200 },
					);
				}
				if (u.endsWith("/questions")) {
					return new Response(
						JSON.stringify({
							questions: [
								{
									question_id: "q_polled",
									focus_concept: "Carry",
									prompt: "Is Carry an Event or Fee?",
									options: [{ option_id: "o1", label: "Event" }],
								},
							],
						}),
						{ status: 200 },
					);
				}
				if (u.endsWith("/answers")) {
					return new Response(JSON.stringify({ answers: [] }), { status: 200 });
				}
				return new Response("{}", { status: 200 });
			}) as typeof fetch;

			const clarifyJobId = await store
				.getState()
				.startClarify({ mode: "deterministic" }, mockPollFetch);
			expect(clarifyJobId).toBe("job-clarify-poll-1");
			expect(store.getState().isClarifying).toBe(true);

			// Wait for 50ms polling fallback timer + async loadClarifications
			await new Promise((r) => setTimeout(r, 110));
			expect(polledJobUrl).toBe(
				"/api/v1/ontology/studio/jobs/job-clarify-poll-1",
			);
			expect(store.getState().isClarifying).toBe(false);
			expect(store.getState().clarifications[0]?.question_id).toBe("q_polled");

		});
	});
});
