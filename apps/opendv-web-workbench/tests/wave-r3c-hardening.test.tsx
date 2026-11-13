/**
 * Wave R3-C Hardening & Regression Suite
 * (apps/opendv-web-workbench/tests/wave-r3c-hardening.test.tsx)
 *
 * Covers:
 * 1. Unified studioFetch (CSRF X-OpenDV-Workbench, credentials: include, Bearer token, 401 -> authRequired) & Auth Bootstrap
 * 2. Confidential Source Upload (do_not_send_to_agent FormData & SourceDocument mapping)
 * 3. Strict Path A Grounding, Grandfather/Waiver Mutation Revocation, Per-Concept Waivers, Feedback & Lossless Export
 * 4. Genesis 0.0.0 Commits, Deterministic op_id, Mapping Payloads & Full Visual Editor / Admin Delta Diffing
 * 5. Parallel Clarification Q&A, SSE Job Progress + Polling Fallback, Candidate Proposals & 0.0.0 Diff Short-Circuit
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	OntologyStudioApp,
	buildUniqueConceptIdentifiers,
	buildUniqueRelationshipIdentifiers,
} from "../src/ontology/OntologyStudioApp";
import { ClarificationPanel } from "../src/ontology/components/ClarificationPanel";
import { DiffOverlay } from "../src/ontology/components/DiffOverlay";
import { ExportPromoteModal } from "../src/ontology/components/ExportPromoteModal";
import { QualityFeedbackPanel } from "../src/ontology/components/QualityFeedbackPanel";
import { SourcesPanel } from "../src/ontology/components/SourcesPanel";
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
	extractAndScrubLaunchNonce,
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
	// 1. UNIFIED studioFetch & AUTH BOOTSTRAP
	// =========================================================================
	describe("1. Unified studioFetch & Auth Bootstrap (P14c::MAJ-01, P14d::MAJ-02, CRIT-01, MAJOR-01)", () => {
		it("attaches credentials: include, X-OpenDV-Workbench: 1 on mutations, Authorization Bearer when set, and flags 401 authRequired", async () => {
			const store = createStudioStore();
			store.getState().setAuthToken("secret-studio-bearer-xyz");

			const calls: Array<{
				url: string;
				method: string;
				credentials?: RequestCredentials;
				headers: Record<string, string>;
			}> = [];

			const mockFetch: typeof fetch = (async (
				url: string | URL | Request,
				init?: RequestInit,
			) => {
				const method = (init?.method || "GET").toUpperCase();
				const headers = (init?.headers as Record<string, string>) || {};
				calls.push({
					url: String(url),
					method,
					...(init?.credentials !== undefined
						? { credentials: init.credentials }
						: {}),
					headers,
				});
				if (String(url).endsWith("/sessions") && method === "GET") {
					return new Response(JSON.stringify({ detail: "Unauthorized" }), {
						status: 401,
					});
				}
				return new Response(
					JSON.stringify({
						session_id: "new-sess",
						domain_namespace: "pe",
						enterprise_owner: "owner",
						status: "ACTIVE",
						active_version: "0.0.0",
						head_sha256: GENESIS_SHA256,
						created_at: "2026-11-12T00:00:00Z",
						updated_at: "2026-11-12T00:00:00Z",
					}),
					{ status: 201 },
				);
			}) as typeof fetch;

			// GET /sessions -> 401 sets authRequired = true
			await store.getState().loadSessions(mockFetch);
			expect(store.getState().authRequired).toBe(true);
			expect(calls[0]?.credentials).toBe("include");
			expect(calls[0]?.headers.Authorization).toBe(
				"Bearer secret-studio-bearer-xyz",
			);
			expect(calls[0]?.headers["X-OpenDV-Workbench"]).toBeUndefined();

			// POST /sessions -> includes X-OpenDV-Workbench: 1 + credentials: include + Bearer
			const created = await store.getState().createSession(
				{
					session_id: "new-sess",
					domain_namespace: "pe",
					enterprise_owner: "owner",
				},
				mockFetch,
			);
			expect(created?.active_version).toBe("0.0.0");
			expect(store.getState().authRequired).toBe(false);
			expect(calls[1]?.method).toBe("POST");
			expect(calls[1]?.credentials).toBe("include");
			expect(calls[1]?.headers["X-OpenDV-Workbench"]).toBe("1");
			expect(calls[1]?.headers.Authorization).toBe(
				"Bearer secret-studio-bearer-xyz",
			);
		});

		it("extracts and immediately scrubs #token=<nonce> and ?nonce=<nonce> via history.replaceState", () => {
			let replacedUrl = "";
			const mockWinHash = {
				location: {
					pathname: "/ontology",
					search: "?tab=sources",
					hash: "#token=launch-nonce-abc123&view=graph",
				},
				history: {
					replaceState: (
						_data: unknown,
						_unused: string,
						url?: string | URL | null,
					) => {
						replacedUrl = String(url ?? "");
					},
				},
			};

			const extracted1 = extractAndScrubLaunchNonce(mockWinHash);
			expect(extracted1).toBe("launch-nonce-abc123");
			expect(replacedUrl).toBe("/ontology?tab=sources#view=graph");
			expect(replacedUrl).not.toContain("launch-nonce-abc123");

			let replacedUrlQuery = "";
			const mockWinQuery = {
				location: {
					pathname: "/ontology",
					search: "?nonce=query-nonce-999",
					hash: "",
				},
				history: {
					replaceState: (
						_data: unknown,
						_unused: string,
						url?: string | URL | null,
					) => {
						replacedUrlQuery = String(url ?? "");
					},
				},
			};
			const extracted2 = extractAndScrubLaunchNonce(mockWinQuery);
			expect(extracted2).toBe("query-nonce-999");
			expect(replacedUrlQuery).toBe("/ontology");
		});

		it("exchanges launch nonce via POST /auth/token and supports loopback session bootstrap", async () => {
			const store = createStudioStore();
			store.setState({ authRequired: true });

			const requests: Array<{
				url: string;
				body: unknown;
				headers: Record<string, string>;
			}> = [];
			const mockFetch: typeof fetch = (async (
				url: string | URL | Request,
				init?: RequestInit,
			) => {
				requests.push({
					url: String(url),
					body: init?.body ? JSON.parse(String(init.body)) : null,
					headers: (init?.headers as Record<string, string>) || {},
				});
				if (String(url).endsWith("/sessions")) {
					return new Response(JSON.stringify({ sessions: [] }), {
						status: 200,
					});
				}
				return new Response(JSON.stringify({ authenticated: true }), {
					status: 200,
				});
			}) as typeof fetch;

			const okNonce = await store
				.getState()
				.exchangeLaunchNonce("one-time-nonce-42", mockFetch);
			expect(okNonce).toBe(true);
			expect(store.getState().authRequired).toBe(false);
			expect(requests[0]?.url).toBe("/api/v1/ontology/studio/auth/token");
			expect(requests[0]?.body).toEqual({ nonce: "one-time-nonce-42" });
			expect(requests[0]?.headers["X-OpenDV-Workbench"]).toBe("1");

			store.setState({ authRequired: true });
			const okLoopback = await store
				.getState()
				.bootstrapLoopbackAuth(mockFetch);
			expect(okLoopback).toBe(true);
			expect(store.getState().authRequired).toBe(false);
			expect(
				requests.some((r) => r.url.endsWith("/api/v1/ontology/studio/init")),
			).toBe(true);
		});

		it("renders auth bootstrap UI on 401, CAS standalone banner (never 'stored in-memory'), and header visual edit controls", () => {
			studioStore.setState({
				activeMode: "local_standalone",
				authRequired: true,
				activeSession: makeSession(),
				sessions: [makeSession()],
				nodes: [makeGroundedConcept()],
				hasUnsavedChanges: true,
			});

			const html = renderToStaticMarkup(createElement(OntologyStudioApp));
			expect(html).toContain('data-testid="studio-auth-bootstrap"');
			expect(html).toContain('data-testid="auth-token-input"');
			expect(html).toContain('data-testid="loopback-init-btn"');
			expect(html).toContain('data-testid="standalone-mode-banner"');
			expect(html).toContain("persisted in local CAS");
			expect(html).not.toContain("stored in-memory");
			expect(html).toContain('data-testid="add-concept-btn"');
			expect(html).toContain('data-testid="add-relationship-btn"');
			expect(html).toContain('data-testid="commit-changes-btn"');
			expect(html).toContain('data-testid="discard-changes-btn"');
			expect(html).toContain('data-testid="header-relayout-btn"');
			expect(html).toContain('data-testid="studio-role-badge"');
			expect(html).not.toContain('data-testid="studio-role-select"');

			// Verify triggerRelayout increments layoutTriggerCount for GraphCanvas subscription (P14a::MAJ-01)
			const prevTrigger = studioStore.getState().layout.layoutTriggerCount || 0;
			studioStore.getState().triggerRelayout();
			expect(studioStore.getState().layout.layoutTriggerCount).toBe(
				prevTrigger + 1,
			);
		});
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

			studioStore.setState({
				activeSession: makeSession(),
				sources: store.getState().sources,
			});
			const panelHtml = renderToStaticMarkup(createElement(SourcesPanel));
			expect(panelHtml).toContain("CONFIDENTIAL");
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

			// Per-concept waiver revocation upon concept mutation
			const store = createStudioStore({ initialSession: makeSession() });
			const ungroundedA: ConceptNode = {
				...grandfatheredConcept,
				id: "urn:ontology:entity:ungrounded_a",
				concept_uri: "urn:ontology:entity:ungrounded_a",
				concept_name: "Ungrounded A",
			};
			const ungroundedB: ConceptNode = {
				...grandfatheredConcept,
				id: "urn:ontology:entity:ungrounded_b",
				concept_uri: "urn:ontology:entity:ungrounded_b",
				concept_name: "Ungrounded B",
			};
			store.setState({ nodes: [ungroundedA, ungroundedB] });

			let capturedWaiverBody: Record<string, unknown> | null = null;
			let capturedWaiverHeaders: Record<string, string> = {};
			const mockWaiverFetch: typeof fetch = (async (_url, init) => {
				capturedWaiverHeaders = (init?.headers as Record<string, string>) || {};
				capturedWaiverBody = JSON.parse(String(init?.body)) as Record<
					string,
					unknown
				>;
				return new Response(
					JSON.stringify({
						waiver_applied: true,
						waived_concept_uris: [ungroundedA.concept_uri],
						waived_base_sha256: "a".repeat(64),
					}),
					{ status: 200 },
				);
			}) as typeof fetch;

			// Scope waiver ONLY to ungroundedA
			const okWaiver = await store
				.getState()
				.submitWaiver(
					"Proprietary internal concept A approved",
					"waiver-admin-secret",
					[ungroundedA.concept_uri],
					mockWaiverFetch,
				);
			expect(okWaiver).toBe(true);
			expect(capturedWaiverBody?.target_concept_uris).toEqual([
				ungroundedA.concept_uri,
			]);
			expect(capturedWaiverHeaders["X-OpenDV-Waiver-Key"]).toBe(
				"waiver-admin-secret",
			);
			expect(capturedWaiverBody?.waiver_key).toBeUndefined();

			// Because ungroundedB was NOT waived, ONT-EVO-002 still fails until B is removed or grounded
			expect(
				store
					.getState()
					.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002")
					?.status,
			).toBe("FAIL");

			// Remove ungroundedB -> now only waived ungroundedA remains -> ONT-EVO-002 PASS
			store.getState().deleteConcept(ungroundedB.concept_uri);
			expect(
				store
					.getState()
					.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002")
					?.status,
			).toBe("PASS");

			// Mutate ungroundedA after waiver -> waiver for ungroundedA is invalidated -> ONT-EVO-002 FAIL!
			store.getState().updateConcept(ungroundedA.concept_uri, {
				synonyms: ["Mutated After Waiver"],
			});
			expect(
				store
					.getState()
					.qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002")
					?.status,
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

			// ExportPromoteModal omits rdf and offers ntriples
			studioStore.setState({ activeSession: makeSession() });
			const modalHtml = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "export",
					onClose: () => {},
				}),
			);
			expect(modalHtml).toContain("N-Triples (.nt)");
			expect(modalHtml).not.toContain("RDF/XML (.rdf)");
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

			// Render ClarificationPanel and verify Clarify, Construct, and Proposal controls
			studioStore.setState({
				activeSession: makeSession(),
				proposals: store.getState().proposals,
			});
			const cpHtml = renderToStaticMarkup(createElement(ClarificationPanel));
			expect(cpHtml).toContain('data-testid="start-clarify-btn"');
			expect(cpHtml).toContain('data-testid="start-construct-btn"');
			expect(cpHtml).toContain('data-testid="proposals-section"');
			expect(cpHtml).toContain('data-testid="rebase-proposal-btn-prop-001"');
			expect(cpHtml).toContain('data-testid="accept-proposal-btn-prop-001"');

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

			// Render DiffOverlay and verify version selectors
			studioStore.setState({
				activeSession: store.getState().activeSession,
				diffState: store.getState().diffState,
			});
			const diffHtml = renderToStaticMarkup(createElement(DiffOverlay));
			expect(diffHtml).toContain('data-testid="diff-base-version-select"');
			expect(diffHtml).toContain('data-testid="diff-target-version-select"');
		});

		it("falls back to GET /jobs/{job_id} polling when EventSource is unavailable and renders QualityFeedbackPanel per-concept waiver checkboxes", async () => {
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

			// Render QualityFeedbackPanel with active waiver + ungrounded concept -> verify per-concept checkbox & feedback concept select
			studioStore.setState({
				activeSession: makeSession(),
				nodes: [
					{
						id: "urn:ontology:entity:ungrounded_carry",
						concept_uri: "urn:ontology:entity:ungrounded_carry",
						concept_name: "Ungrounded Carry",
						elm_archetype: "CORE_BUSINESS_CONCEPT",
						definition: "Carry waterfall rule",
						grounding_status: "UNGROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
				qualityScorecard: {
					...studioStore.getState().qualityScorecard,
					waiver_applied: true,
					waiver_rationale: "Approved proprietary carry model",
					waived_concept_uris: ["urn:ontology:entity:ungrounded_carry"],
				},
			});
			const qfpHtml = renderToStaticMarkup(createElement(QualityFeedbackPanel));
			expect(qfpHtml).toContain('data-testid="waiver-concepts-list"');
			expect(qfpHtml).toContain(
				'data-testid="waiver-concept-checkbox-urn:ontology:entity:ungrounded_carry"',
			);
			expect(qfpHtml).toContain('data-testid="feedback-concept-select"');
		});
	});

	// =========================================================================
	// 6. ROUND-3 AUDIT REMEDIATIONS: RBAC BADGE & QUICK-ADD COLLISION GUARDS
	// =========================================================================
	describe("6. Round-3 Audit Remediations: Read-Only Role Badge & Collision-Free Quick-Add (P14a::MAJ-01, MAJ-02)", () => {
		it("renders read-only studio-role-badge across all 4 RBAC tiers and blocks manual mutations for viewer and automated roles", async () => {
			for (const [role, expectedLabel] of [
				["viewer", "Role: Viewer"],
				["editor", "Role: Editor"],
				["automated", "Role: Automated"],
				["admin", "Role: Admin"],
			] as const) {
				studioStore.getState().setUserRole(role);
				studioStore.setState({
					activeSession: makeSession(),
					nodes: [makeGroundedConcept()],
					hasUnsavedChanges: true,
				});
				const html = renderToStaticMarkup(createElement(OntologyStudioApp));
				expect(html).toContain('data-testid="studio-role-badge"');
				expect(html).toContain(expectedLabel);
				expect(html).not.toContain('data-testid="studio-role-select"');

				if (role === "viewer" || role === "automated") {
					expect(html).toMatch(
						/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="add-concept-btn")[^>]*>/,
					);
					expect(html).toMatch(
						/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="commit-changes-btn")[^>]*>/,
					);
				} else {
					expect(html).not.toMatch(
						/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="add-concept-btn")[^>]*>/,
					);
					expect(html).not.toMatch(
						/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="commit-changes-btn")[^>]*>/,
					);
				}
			}

			// ClarificationPanel gates human ratification buttons for automated role while allowing job triggers
			studioStore.setState({
				activeSession: makeSession(),
				userRole: "automated",
				clarifications: [
					{
						question_id: "q-rbac-1",
						concept_name: "Fund",
						question_text: "Clarify grain?",
						certainty: "MEDIUM",
						rationale_options: [{ id: "opt-1", label: "Fund ID" }],
						evidence_citations: [],
						status: "PENDING",
					},
				],
				proposals: [
					{
						proposal_id: "prop-rbac-1",
						status: "PENDING",
						base_sha256: "a".repeat(64),
						target_version: "1.1.0",
						ops_count: 1,
						created_at: "2026-11-12T00:00:00Z",
					},
				],
			});
			const cpHtml = renderToStaticMarkup(createElement(ClarificationPanel));
			expect(cpHtml).not.toMatch(
				/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="start-clarify-btn")[^>]*>/,
			);
			expect(cpHtml).toMatch(
				/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="accept-proposal-btn-prop-rbac-1")[^>]*>/,
			);
			expect(cpHtml).toMatch(
				/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="submit-answer-btn-q-rbac-1")[^>]*>/,
			);

			// Store-level guards reject commitChanges and acceptProposal under automated role
			const blockedCommit = await studioStore
				.getState()
				.commitChanges("Agent manual commit attempt");
			expect(blockedCommit).toBe(false);
			expect(studioStore.getState().toasts[0]?.code).toBe("FORBIDDEN_ROLE");

			const blockedAccept = await studioStore
				.getState()
				.acceptProposal("prop-rbac-1", "Agent manual accept attempt");
			expect(blockedAccept).toBe(false);
		});

		it("generates collision-free concept and relationship identifiers incorporating source+verb+target and deduplicating against committed history (P14a::MAJ-02)", () => {
			const committedConceptA = makeGroundedConcept({
				id: "urn:ontology:entity:concept_1",
				concept_uri: "urn:ontology:entity:concept_1",
				concept_name: "Concept 1",
				target_hub: "hub_concept_1",
			});
			const workingConceptB = makeGroundedConcept({
				id: "urn:ontology:entity:fund",
				concept_uri: "urn:ontology:entity:fund",
				concept_name: "Fund",
				target_hub: "hub_fund",
			});

			// Even if nodes only has 1 item ("fund"), committedNodes has "concept_1", so default Concept 2 or colliding "Fund" increments cleanly
			const dupFundIds = buildUniqueConceptIdentifiers(
				"Fund",
				[workingConceptB],
				[committedConceptA],
			);
			expect(dupFundIds.conceptUri).toBe("urn:ontology:entity:fund_2");
			expect(dupFundIds.targetHub).toBe("hub_fund_2");

			const dupCommittedIds = buildUniqueConceptIdentifiers(
				"Concept 1",
				[workingConceptB],
				[committedConceptA],
			);
			expect(dupCommittedIds.conceptUri).toBe(
				"urn:ontology:entity:concept_1_2",
			);
			expect(dupCommittedIds.targetHub).toBe("hub_concept_1_2");

			// Repeated verb "has" across distinct concept pairs produces distinct URIs and lnk_* physical names
			const relFundLp = buildUniqueRelationshipIdentifiers(
				"has",
				"urn:ontology:entity:fund",
				"urn:ontology:entity:lp",
				[],
				[],
			);
			const relPortfolioDeal = buildUniqueRelationshipIdentifiers(
				"has",
				"urn:ontology:entity:portfolio",
				"urn:ontology:entity:deal",
				[],
				[],
			);
			expect(relFundLp.relationshipUri).toBe(
				"urn:ontology:relationship:fund_has_lp",
			);
			expect(relFundLp.targetLink).toBe("lnk_fund_has_lp");
			expect(relPortfolioDeal.relationshipUri).toBe(
				"urn:ontology:relationship:portfolio_has_deal",
			);
			expect(relPortfolioDeal.targetLink).toBe("lnk_portfolio_has_deal");

			// Repeated verb between the SAME concept pair or colliding with committedEdges appends _2
			const committedEdge: RelationshipEdge = {
				id: "urn:ontology:relationship:fund_has_lp",
				relationship_uri: "urn:ontology:relationship:fund_has_lp",
				relationship_name: "has",
				target_link: "lnk_fund_has_lp",
				source_concept_uri: "urn:ontology:entity:fund",
				target_concept_uri: "urn:ontology:entity:lp",
			};
			const relFundLpSecond = buildUniqueRelationshipIdentifiers(
				"has",
				"urn:ontology:entity:fund",
				"urn:ontology:entity:lp",
				[],
				[committedEdge],
			);
			expect(relFundLpSecond.relationshipUri).toBe(
				"urn:ontology:relationship:fund_has_lp_2",
			);
			expect(relFundLpSecond.targetLink).toBe("lnk_fund_has_lp_2");
		});
	});
});
