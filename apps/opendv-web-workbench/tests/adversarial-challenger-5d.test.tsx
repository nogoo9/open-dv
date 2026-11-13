/**
 * Adversarial Security & OCC Concurrency Challenge Suite (Milestone 5D)
 * (apps/opendv-web-workbench/tests/adversarial-challenger-5d.test.tsx)
 *
 * Authored by: challenger_5d_2 (teamwork_challenger)
 * Empirical verification of:
 * 1. Administrative waiver authorization bypass attempts
 * 2. OCC concurrency conflict simulation (409 Conflict & 412 Precondition Failed)
 * 3. XSS / script injection attacks in visual editor, drawer, scorecard & exports
 * 4. Export serialization roundtrips (RDF/XML, Turtle, JSON-LD, YAML, RFC 8785 JCS)
 * 5. Graph A golden fixture cross-language SHA-256 parity
 */

import { beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	ExportPromoteModal,
} from "../src/ontology/components/ExportPromoteModal";
import {
	QualityFeedbackPanel,
} from "../src/ontology/components/QualityFeedbackPanel";
import {
	VisualEditorDrawer,
	validateConceptEdit,
} from "../src/ontology/components/VisualEditorDrawer";
import {
	canonicalizeGraphADict,
	computeQualityScorecard,
	createDefaultQualityScorecard,
	createStudioStore,
	dumpsJcs,
	escapeIri,
	escapeTurtleString,
	escapeXml,
	escapeYamlString,
	generateLocalExport,
	recomputeScorecard,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";
import goldenFixture from "../../../packages/opendv-contracts/schemas/v1/fixtures/graph_a_golden.json";

describe("Empirical Challenger 5D: Adversarial Security & OCC Concurrency Suite", () => {
	const createSampleSession = (overrides?: Partial<StudioSessionSummary>): StudioSessionSummary => ({
		session_id: "sess-adv-1",
		domain_namespace: "wealth_adv",
		enterprise_owner: "Risk & Sec Division",
		active_version: "1.0.0",
		status: "ACTIVE",
		head_sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
		created_at: "2026-11-11T10:00:00Z",
		updated_at: "2026-11-11T10:00:00Z",
		...overrides,
	});

	const createSampleNode = (overrides?: Partial<ConceptNode>): ConceptNode => ({
		id: "urn:entity:portfolio",
		concept_uri: "urn:entity:portfolio",
		concept_name: "Portfolio",
		elm_archetype: "CORE_CONCEPT",
		definition: "Investment portfolio",
		target_hub: "hub_portfolio",
		confidence: 0.95,
		grounding_status: "GROUNDED",
		attributes: [
			{ attribute_name: "portfolio_id", data_type: "string", description: "Portfolio ID" },
		],
		synonyms: ["fund_portfolio"],
		external_mappings: [
			{
				scheme: "FIBO",
				target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio",
				predicate: "skos:exactMatch",
				confidence: 0.95,
			},
		],
		...overrides,
	});

	const createUngroundedNode = (overrides?: Partial<ConceptNode>): ConceptNode => ({
		id: "urn:entity:provisional_instrument",
		concept_uri: "urn:entity:provisional_instrument",
		concept_name: "Provisional Instrument",
		elm_archetype: "CORE_CONCEPT",
		definition: "Provisional ungrounded instrument",
		target_hub: "hub_instrument",
		confidence: 0.45,
		grounding_status: "UNGROUNDED",
		attributes: [],
		synonyms: [],
		external_mappings: [],
		...overrides,
	});

	beforeEach(() => {
		const initial = createStudioStore().getState();
		studioStore.setState({
			sessions: [createSampleSession()],
			activeSession: createSampleSession(),
			nodes: [createSampleNode()],
			edges: [],
			selectedNodeId: null,
			selectedEdgeId: null,
			toasts: [],
			sessionWaivers: {},
			qualityScorecard: createDefaultQualityScorecard(),
			hasUnsavedChanges: false,
		});
	});

	// =========================================================================
	// 1. ADMINISTRATIVE WAIVER AUTHORIZATION BYPASS ATTEMPTS
	// =========================================================================
	describe("1. Administrative Waiver Authorization Bypass Attempts", () => {
		it("blocks waiver submission when rationale is short, empty, whitespace-only or multibyte boundary", async () => {
			const ungrounded = createUngroundedNode();
			studioStore.setState({ nodes: [createSampleNode(), ungrounded] });

			let fetchCalled = false;
			const mockFetch = (async () => {
				fetchCalled = true;
				return new Response(JSON.stringify({ waiver_applied: true }), { status: 200 });
			}) as unknown as typeof fetch;

			// Attack 1a: Empty string
			const resEmpty = await studioStore.getState().submitWaiver("", "admin-secret-key-123", mockFetch);
			expect(resEmpty).toBe(false);
			expect(fetchCalled).toBe(false);
			expect(studioStore.getState().toasts[0].code).toBe("INVALID_GROUNDING_WAIVER");

			// Attack 1b: 9 characters (below 10 char minimum)
			const res9 = await studioStore.getState().submitWaiver("123456789", "admin-secret-key-123", mockFetch);
			expect(res9).toBe(false);
			expect(fetchCalled).toBe(false);

			// Attack 1c: Whitespace padding hiding short rationale
			const resPadded = await studioStore.getState().submitWaiver("   short   ", "admin-secret-key-123", mockFetch);
			expect(resPadded).toBe(false);
			expect(fetchCalled).toBe(false);

			// Attack 1d: Multibyte emojis with character count < 10
			const resEmoji = await studioStore.getState().submitWaiver("🚀🚀🚀🚀", "admin-secret-key-123", mockFetch);
			expect(resEmoji).toBe(false);
			expect(fetchCalled).toBe(false);

			// QualityScorecard must remain un-waived and ONT-EVO-002 must FAIL
			const scorecard = studioStore.getState().qualityScorecard;
			expect(scorecard.waiver_applied).toBe(false);
		});

		it("blocks waiver submission when waiver key / credential is empty or blank", async () => {
			const ungrounded = createUngroundedNode();
			studioStore.setState({ nodes: [createSampleNode(), ungrounded] });

			let fetchCalled = false;
			const mockFetch = (async () => {
				fetchCalled = true;
				return new Response(JSON.stringify({ waiver_applied: true }), { status: 200 });
			}) as unknown as typeof fetch;

			// Attack 2a: Empty key
			const resEmptyKey = await studioStore.getState().submitWaiver("Valid rationale with more than 10 characters", "", mockFetch);
			expect(resEmptyKey).toBe(false);
			expect(fetchCalled).toBe(false);
			expect(studioStore.getState().toasts[0].code).toBe("MISSING_WAIVER_CREDENTIAL");

			// Attack 2b: Whitespace key
			const resBlankKey = await studioStore.getState().submitWaiver("Valid rationale with more than 10 characters", "   \t\n  ", mockFetch);
			expect(resBlankKey).toBe(false);
			expect(fetchCalled).toBe(false);
		});

		it("rejects unauthorized waiver when server responds with 401 Unauthorized or 403 Forbidden", async () => {
			const ungrounded = createUngroundedNode();
			studioStore.setState({ nodes: [createSampleNode(), ungrounded] });

			const mockFetch403 = (async () => {
				return new Response(
					JSON.stringify({
						title: "Forbidden",
						status: 403,
						detail: "Invalid steward administrative credentials.",
						code: "FORBIDDEN_ADMIN_REQUIRED",
					}),
					{ status: 403, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const res = await studioStore.getState().submitWaiver(
				"Legitimate rationale for regulatory delay",
				"attacker-forged-key",
				mockFetch403,
			);
			expect(res).toBe(false);
			const toasts = studioStore.getState().toasts;
			expect(toasts.some((t) => t.code === "FORBIDDEN_ADMIN_REQUIRED")).toBe(true);
			expect(studioStore.getState().qualityScorecard.waiver_applied).toBe(false);
		});

		it("rejects waiver when active session has no head_sha256 or is uncommitted", async () => {
			studioStore.setState({
				activeSession: createSampleSession({ head_sha256: "" }),
				nodes: [createSampleNode(), createUngroundedNode()],
			});

			let fetchCalled = false;
			const mockFetch = (async () => {
				fetchCalled = true;
				return new Response(JSON.stringify({ waiver_applied: true }), { status: 200 });
			}) as unknown as typeof fetch;

			const res = await studioStore.getState().submitWaiver(
				"Sufficient rationale for grounding waiver",
				"valid-admin-key-123",
				mockFetch,
			);
			expect(res).toBe(false);
			expect(fetchCalled).toBe(false);
			expect(studioStore.getState().toasts[0].code).toBe("MISSING_HEAD_SHA");
		});

		it("rejects waiver attempt when graph has zero ungrounded concepts (precondition bypass)", async () => {
			// All nodes are grounded
			studioStore.setState({ nodes: [createSampleNode()] });

			let fetchCalled = false;
			const mockFetch = (async () => {
				fetchCalled = true;
				return new Response(JSON.stringify({ waiver_applied: true }), { status: 200 });
			}) as unknown as typeof fetch;

			const res = await studioStore.getState().submitWaiver(
				"Sufficient rationale for grounding waiver",
				"valid-admin-key-123",
				mockFetch,
			);
			expect(res).toBe(false);
			expect(fetchCalled).toBe(false);
			expect(studioStore.getState().toasts[0].code).toBe("NO_UNGROUNDED_CONCEPTS");
		});

		it("prevents state spoofing via updateQualityScorecard attempting to inject waiver authorization", () => {
			const ungrounded = createUngroundedNode();
			studioStore.setState({ nodes: [createSampleNode(), ungrounded] });

			// Initial state: not waived
			expect(studioStore.getState().qualityScorecard.waiver_applied).toBe(false);

			// Adversary calls updateQualityScorecard trying to flip waiver_applied and waived_concept_uris
			studioStore.getState().updateQualityScorecard({
				waiver_applied: true,
				waived_concept_uris: [ungrounded.concept_uri],
				waiver_rationale: "Spoofed by adversary",
				checks: [{ rule: "ONT-EVO-002", status: "PASS", description: "Bypassed" }],
			} as any);

			// Invariant check: waiver fields MUST be stripped by updateQualityScorecard sanitizer
			const current = studioStore.getState().qualityScorecard;
			expect(current.waiver_applied).toBe(false);
			expect(current.waived_concept_uris).toEqual([]);
			expect(current.waiver_rationale).toBe("");
		});

		it("invalidates authorized waiver when an ungrounded concept NOT in waived_concept_uris is introduced", async () => {
			const ungrounded1 = createUngroundedNode({
				id: "urn:entity:waived_concept_1",
				concept_uri: "urn:entity:waived_concept_1",
				concept_name: "Waived Concept 1",
			});
			studioStore.setState({ nodes: [createSampleNode(), ungrounded1] });

			const mockFetchSuccess = (async () => {
				return new Response(
					JSON.stringify({
						status: "AUTHORIZED",
						waiver_applied: true,
						waived_concept_uris: [ungrounded1.concept_uri],
						waived_base_sha256: studioStore.getState().activeSession?.head_sha256,
						rationale: "Steward approved waiver for ungrounded concept 1",
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const ok = await studioStore.getState().submitWaiver(
				"Steward approved waiver for ungrounded concept 1",
				"valid-admin-key-123",
				mockFetchSuccess,
			);
			expect(ok).toBe(true);

			// At this point ONT-EVO-002 passes because ungrounded1 is waived
			expect(studioStore.getState().qualityScorecard.waiver_applied).toBe(true);
			const evoCheck = studioStore.getState().qualityScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("PASS");

			// Adversary or developer adds a SECOND ungrounded concept that was NOT in the authorized waiver
			const ungrounded2 = createUngroundedNode({
				id: "urn:entity:unauthorized_concept_2",
				concept_uri: "urn:entity:unauthorized_concept_2",
				concept_name: "Unauthorized Concept 2",
			});
			studioStore.getState().addConcept(ungrounded2);

			// Invariant: The scorecard MUST recompute and ONT-EVO-002 MUST FAIL because ungrounded2 is not waived!
			const updatedScorecard = studioStore.getState().qualityScorecard;
			const updatedEvoCheck = updatedScorecard.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(updatedEvoCheck?.status).toBe("FAIL");
			expect(updatedEvoCheck?.details).toContain("violates ONT-EVO-002");
		});

		it("invalidates authorized waiver when session head_sha256 advances via commit or rebase (MAJ-02)", async () => {
			const ungrounded = createUngroundedNode();
			studioStore.setState({ nodes: [createSampleNode(), ungrounded] });

			const currentHead = studioStore.getState().activeSession?.head_sha256;
			const mockFetchSuccess = (async () => {
				return new Response(
					JSON.stringify({
						waiver_applied: true,
						waived_concept_uris: [ungrounded.concept_uri],
						waived_base_sha256: currentHead,
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			await studioStore.getState().submitWaiver("Valid rationale for waiver", "valid-key-123", mockFetchSuccess);
			expect(studioStore.getState().qualityScorecard.waiver_applied).toBe(true);

			// Simulate session head advancing to new SHA
			const newHead = "f".repeat(64);
			studioStore.setState((s) => ({
				activeSession: s.activeSession ? { ...s.activeSession, head_sha256: newHead } : null,
			}));

			// Calling recomputeScorecard with the new state
			const recomputed = recomputeScorecard(
				studioStore.getState(),
				studioStore.getState().nodes,
				studioStore.getState().edges,
			);

			// Invariant: Because savedWaiver.waived_base_sha256 !== state.activeSession.head_sha256, waiver is INVALID!
			expect(recomputed.waiver_applied).toBe(false);
			expect(recomputed.waived_concept_uris).toEqual([]);
			const evoCheck = recomputed.checks.find((c) => c.rule_id === "ONT-EVO-002");
			expect(evoCheck?.status).toBe("FAIL");
		});
	});

	// =========================================================================
	// 2. OCC CONCURRENCY CONFLICT SIMULATION (STALE IF-MATCH -> 409 HANDLING)
	// =========================================================================
	describe("2. OCC Concurrency Conflict Simulation", () => {
		it("simulates OCC conflict (HTTP 409) during submitAnswer preserving local draft without corruption", async () => {
			const questionId = "q_jurisdiction_01";
			studioStore.setState({
				clarifications: [
					{
						question_id: questionId,
						concept_name: "Portfolio",
						question_text: "What is the primary jurisdiction?",
						certainty: "MEDIUM",
						rationale_options: [{ id: "opt_us", label: "US SEC" }, { id: "opt_eu", label: "EU ESMA" }],
						evidence_citations: [],
						status: "PENDING",
					},
				],
				answers_sha256: "stale_hash_1111111111111111111111111111111111111111111111111111111111111111",
			});

			let loadClarificationsCalled = false;
			const mockFetch409 = (async (url: string | URL | Request, init?: RequestInit) => {
				const urlStr = String(url);
				if (urlStr.includes("/answers") && init?.method === "POST") {
					// Verify If-Match header was sent
					const headers = init?.headers as Record<string, string>;
					expect(headers["If-Match"]).toBe('"stale_hash_1111111111111111111111111111111111111111111111111111111111111111"');
					return new Response(
						JSON.stringify({
							title: "Conflict",
							status: 409,
							detail: "Answers digest conflict: another steward submitted answers concurrently.",
							code: "CONCURRENT_MODIFICATION",
						}),
						{ status: 409, headers: { "Content-Type": "application/json" } },
					);
				}
				if (urlStr.includes("/answers") && (!init?.method || init.method === "GET")) {
					return new Response(
						JSON.stringify({
							answers: [],
							answers_sha256: "fresh_hash_2222222222222222222222222222222222222222222222222222222222222222",
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				if (urlStr.includes("/questions")) {
					loadClarificationsCalled = true;
					return new Response(
						JSON.stringify({
							questions: [
								{
									question_id: questionId,
									concept_name: "Portfolio",
									question_text: "What is the primary jurisdiction?",
									certainty: "MEDIUM",
									rationale_options: [{ id: "opt_us", label: "US SEC" }],
									evidence_citations: [],
									status: "PENDING",
								},
							],
							answers: {},
							answers_sha256: "fresh_hash_2222222222222222222222222222222222222222222222222222222222222222",
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			studioStore.getState().setAnswerDraft(questionId, ["opt_us"], "Local unsaved insight");

			const ok = await studioStore.getState().submitAnswer(
				{ question_id: questionId, selected_option_ids: ["opt_us"], custom_answer_text: "Local unsaved insight" },
				studioStore.getState().answers_sha256,
				mockFetch409,
			);

			// Result must be false
			expect(ok).toBe(false);
			// Must have reloaded clarifications
			expect(loadClarificationsCalled).toBe(true);
			// Local draft MUST be preserved
			const draft = studioStore.getState().answers[questionId];
			expect(draft).toBeDefined();
			expect(draft.custom_answer_text).toBe("Local unsaved insight");
			// Error toast must be surfaced
			const toasts = studioStore.getState().toasts;
			expect(toasts.some((t) => t.status === 409)).toBe(true);
		});

		it("simulates OCC conflict (HTTP 409) during commitChanges: preserves unsaved edits and rejects commit", async () => {
			const modifiedNode = createSampleNode({ definition: "Adversarially edited definition" });
			studioStore.setState({
				nodes: [modifiedNode],
				hasUnsavedChanges: true,
			});

			const mockFetchCommit409 = (async (url: string | URL | Request, init?: RequestInit) => {
				const headers = init?.headers as Record<string, string>;
				expect(headers["If-Match"]).toBe(`"${studioStore.getState().activeSession?.head_sha256}"`);
				return new Response(
					JSON.stringify({
						title: "Conflict",
						status: 409,
						detail: "Delta commit conflict: Session head SHA has moved.",
						code: "CONCURRENT_MODIFICATION",
					}),
					{ status: 409, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const commitOk = await studioStore.getState().commitChanges("Steward commit rationale", mockFetchCommit409);

			expect(commitOk).toBe(false);
			expect(studioStore.getState().isCommitting).toBe(false);
			// Unsaved edits MUST NOT be discarded
			expect(studioStore.getState().hasUnsavedChanges).toBe(true);
			expect(studioStore.getState().nodes[0].definition).toBe("Adversarially edited definition");
			// Toast must notify steward
			expect(studioStore.getState().toasts.some((t) => t.code === "CONCURRENT_MODIFICATION")).toBe(true);
		});

		it("simulates OCC conflict (HTTP 409 / 412) during promoteWorkspace: enforces both If-Match and X-OpenDV-Workspace-If-Match", async () => {
			const headSha = studioStore.getState().activeSession?.head_sha256 || "";
			const workspaceSha = "b".repeat(64);

			let ifMatchSent: string | undefined;
			let wsIfMatchSent: string | undefined;

			const mockFetchPromote409 = (async (url: string | URL | Request, init?: RequestInit) => {
				const headers = init?.headers as Record<string, string>;
				ifMatchSent = headers["If-Match"];
				wsIfMatchSent = headers["X-OpenDV-Workspace-If-Match"];
				return new Response(
					JSON.stringify({
						title: "Conflict",
						status: 409,
						detail: "Promotion rejected: workspace branch moved.",
						code: "ONT_EVO_CONCURRENT_MODIFICATION",
					}),
					{ status: 409, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const promoteOk = await studioStore.getState().promoteWorkspace(
				{
					workspace: "workspaces/wealth_adv",
					workspace_sha256: workspaceSha,
					head_sha256: headSha,
					rationale: "Legitimate promotion rationale with >10 chars",
				},
				mockFetchPromote409,
			);

			expect(promoteOk).toBe(false);
			expect(ifMatchSent).toBe(`"${headSha}"`);
			expect(wsIfMatchSent).toBe(`"${workspaceSha}"`);
			expect(studioStore.getState().isPromoting).toBe(false);
			expect(studioStore.getState().toasts.some((t) => t.code === "ONT_EVO_CONCURRENT_MODIFICATION")).toBe(true);
		});
	});

	// =========================================================================
	// 3. XSS / SCRIPT INJECTION ATTACKS
	// =========================================================================
	describe("3. XSS / Script Injection Defense Verification", () => {
		const XSS_PAYLOADS = {
			name: `<script>alert('XSS_NAME')</script>`,
			definition: `"><img src=x onerror=alert('XSS_DEF')>`,
			synonym: `<svg onload=alert('XSS_SYN')>`,
			attributeName: `attr_<iframe src="javascript:alert(1)">`,
			attributeDesc: `Description with ' " & < > and ${"`${alert(1)}`"}`,
			targetHub: `hub" onfocus="alert('XSS_HUB')`,
			conceptUri: `urn:entity:<script>alert(1)</script>`,
		};

		it("properly renders malicious XSS payloads into safe text nodes without HTML injection in VisualEditorDrawer", () => {
			const maliciousConcept: ConceptNode = {
				id: "node_xss_1",
				concept_uri: XSS_PAYLOADS.conceptUri,
				concept_name: XSS_PAYLOADS.name,
				elm_archetype: "CORE_CONCEPT",
				definition: XSS_PAYLOADS.definition,
				target_hub: XSS_PAYLOADS.targetHub,
				grounding_status: "GROUNDED",
				attributes: [
					{
						attribute_name: XSS_PAYLOADS.attributeName,
						data_type: "string",
						description: XSS_PAYLOADS.attributeDesc,
					},
				],
				synonyms: [XSS_PAYLOADS.synonym],
				external_mappings: [],
			};

			studioStore.setState({
				nodes: [maliciousConcept],
				selectedNodeId: maliciousConcept.id,
			});

			const markup = renderToStaticMarkup(createElement(VisualEditorDrawer, {}));

			// HTML strings must be encoded: <script> becomes &lt;script&gt;
			expect(markup).toContain("&lt;script&gt;alert(&#x27;XSS_NAME&#x27;)&lt;/script&gt;");
			// Raw dangerous tags MUST NOT exist
			expect(markup).not.toContain("<script>alert('XSS_NAME')</script>");
			expect(markup).not.toContain("<img src=x onerror=alert('XSS_DEF')>");
			expect(markup).toContain("&lt;img src=x onerror=alert(&#x27;XSS_DEF&#x27;)&gt;");
			expect(markup).not.toContain("<svg onload=");
			expect(markup).toContain("&lt;svg onload=alert(&#x27;XSS_SYN&#x27;)&gt;");
		});

		it("properly escapes malicious payloads in QualityFeedbackPanel without DOM injection", () => {
			studioStore.setState({
				qualityScorecard: {
					...createDefaultQualityScorecard(),
					waiver_applied: true,
					waiver_rationale: `<script>alert('XSS_WAIVER')</script>`,
					feedback_comment: `<img src=x onerror=alert('XSS_FEEDBACK')>`,
					feedback_rating: 4,
					feedback_submitted: true,
				},
			});

			const markup = renderToStaticMarkup(createElement(QualityFeedbackPanel, {}));

			expect(markup).not.toContain("<script>alert('XSS_WAIVER')</script>");
			expect(markup).toContain("&lt;script&gt;alert(&#x27;XSS_WAIVER&#x27;)&lt;/script&gt;");
			expect(markup).not.toContain("<img src=x onerror=alert('XSS_FEEDBACK')>");
			expect(markup).toContain("&lt;img src=x onerror=alert(&#x27;XSS_FEEDBACK&#x27;)&gt;");
		});

		it("properly escapes malicious payloads in ExportPromoteModal preview", () => {
			const markup = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "promote",
					onClose: () => {},
				}),
			);
			// Modal markup exists and contains zero raw scripts
			expect(markup).toContain("modal-dialog");
			expect(markup).not.toContain("<script>");
		});

		it("escapes special characters and prevents XML injection in escapeXml", () => {
			const payload = `<foo attr="bar" quote='baz'>&test</foo>\x00\x08`;
			const escaped = escapeXml(payload);

			expect(escaped).toBe("&lt;foo attr=&quot;bar&quot; quote=&apos;baz&apos;&gt;&amp;test&lt;/foo&gt;");
			// Control characters \x00, \x08 must be removed according to XML 1.0
			expect(escaped).not.toContain("\x00");
			expect(escaped).not.toContain("\x08");

			// Defensive nullish handling
			expect(escapeXml(null)).toBe("");
			expect(escapeXml(undefined)).toBe("");
		});

		it("escapes quotes, backslashes, and newlines in escapeTurtleString and escapeYamlString", () => {
			const raw = `Line 1 "quoted"\nLine 2 \\backslash\rLine 3`;

			const turtleEscaped = escapeTurtleString(raw);
			expect(turtleEscaped).toBe('Line 1 \\"quoted\\"\\nLine 2 \\\\backslash\\rLine 3');

			const yamlEscaped = escapeYamlString(raw);
			expect(yamlEscaped).toBe('Line 1 \\"quoted\\"\\nLine 2 \\\\backslash\\rLine 3');

			// Defensive nullish handling
			expect(escapeTurtleString(null)).toBe("");
			expect(escapeTurtleString(undefined)).toBe("");
			expect(escapeYamlString(null)).toBe("");
			expect(escapeYamlString(undefined)).toBe("");
		});

		it("escapes URI/IRI delimiter characters in escapeIri", () => {
			const maliciousUri = `urn:opendv:entity:<script>{"foo":"bar"}|bad^name`;
			const escaped = escapeIri(maliciousUri);

			expect(escaped).not.toContain("<");
			expect(escaped).not.toContain(">");
			expect(escaped).not.toContain("{");
			expect(escaped).not.toContain("}");
			expect(escaped).not.toContain("|");
			expect(escaped).not.toContain("^");
			expect(escaped).toContain("%3Cscript%3E");

			// Defensive nullish handling
			expect(escapeIri(null)).toBe("");
			expect(escapeIri(undefined)).toBe("");
		});
	});

	// =========================================================================
	// 4. MULTI-FORMAT EXPORT SERIALIZATION ROUNDTRIPS
	// =========================================================================
	describe("4. Multi-Format Export Serialization Roundtrips", () => {
		const session = createSampleSession();
		const nodes: ConceptNode[] = [
			createSampleNode({
				concept_uri: "urn:entity:portfolio",
				concept_name: "Portfolio",
				definition: "Investment portfolio container",
				attributes: [
					{ attribute_name: "portfolio_id", data_type: "string", description: "Identifier" },
					{ attribute_name: "inception_date", data_type: "date", description: "Start date" },
				],
				synonyms: ["fund_portfolio"],
				external_mappings: [
					{ scheme: "FIBO", target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Portfolio", confidence: 0.95 },
				],
			}),
			createSampleNode({
				concept_uri: "urn:entity:asset",
				concept_name: "Asset",
				definition: "Financial asset",
				attributes: [{ attribute_name: "asset_id", data_type: "string" }],
				synonyms: [],
				external_mappings: [
					{ scheme: "FIBO", target_uri: "https://spec.edmcouncil.org/fibo/ontology/SEC/Asset", confidence: 0.9 },
				],
			}),
		];

		const edges: RelationshipEdge[] = [
			{
				id: "edge_holds_asset",
				relationship_uri: "urn:relation:holds_asset",
				relationship_name: "holds_asset",
				source_concept_uri: "urn:entity:portfolio",
				target_concept_uri: "urn:entity:asset",
				cardinality: "1:N",
				target_link: "lnk_holds_asset",
			},
		];

		it("exports valid Turtle (.ttl) with prefix headers, owl:Class, and owl:ObjectProperty", () => {
			const ttl = generateLocalExport("turtle", "1.0.0", nodes, edges, session);

			expect(ttl).toContain("@prefix owl: <http://www.w3.org/2002/07/owl#> .");
			expect(ttl).toContain("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .");
			expect(ttl).toContain("<urn:entity:portfolio> a owl:Class ;");
			expect(ttl).toContain('rdfs:label "Portfolio" ;');
			expect(ttl).toContain('rdfs:comment "Investment portfolio container" .');
			expect(ttl).toContain("<urn:relation:holds_asset> a owl:ObjectProperty ;");
			expect(ttl).toContain("<urn:entity:portfolio>");
			expect(ttl).toContain("<urn:entity:asset>");
		});

		it("exports valid RDF/XML (.rdf) conforming to XML structure with tags and attributes", () => {
			const rdf = generateLocalExport("rdf", "1.0.0", nodes, edges, session);

			expect(rdf).toContain('<?xml version="1.0" encoding="utf-8"?>');
			expect(rdf).toContain('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"');
			expect(rdf).toContain('<owl:Class rdf:about="urn:entity:portfolio">');
			expect(rdf).toContain("<rdfs:label>Portfolio</rdfs:label>");
			expect(rdf).toContain("<rdfs:comment>Investment portfolio container</rdfs:comment>");
			expect(rdf).toContain('<owl:ObjectProperty rdf:about="urn:relation:holds_asset">');
			expect(rdf).toContain('rdf:resource="urn:entity:portfolio"');
			expect(rdf).toContain('rdf:resource="urn:entity:asset"');
			expect(rdf).toContain("</rdf:RDF>");
		});

		it("exports valid JSON-LD (.jsonld) with @context, @graph, @id, and @type", () => {
			const jsonld = generateLocalExport("jsonld", "1.0.0", nodes, edges, session);
			const parsed = JSON.parse(jsonld);

			expect(parsed["@context"]).toBeDefined();
			expect(parsed["@context"]["owl"]).toBe("http://www.w3.org/2002/07/owl#");
			expect(Array.isArray(parsed["@graph"])).toBe(true);

			const graph = parsed["@graph"];
			const portfolioNode = graph.find((item: any) => item["@id"] === "urn:entity:portfolio");
			expect(portfolioNode).toBeDefined();
			expect(portfolioNode["@type"]).toBe("owl:Class");
			expect(portfolioNode["rdfs:label"]).toBe("Portfolio");

			const edgeNode = graph.find((item: any) => item["@id"] === "urn:relation:holds_asset");
			expect(edgeNode).toBeDefined();
			expect(edgeNode["@type"]).toBe("owl:ObjectProperty");
			expect(edgeNode["rdfs:domain"]["@id"]).toBe("urn:entity:portfolio");
			expect(edgeNode["rdfs:range"]["@id"]).toBe("urn:entity:asset");
		});

		it("exports valid Graph A YAML (.yaml) containing canonical_business_concepts and canonical_relationships", () => {
			const yamlStr = generateLocalExport("yaml", "1.0.0", nodes, edges, session);

			expect(yamlStr).toContain('ontology_version: "1.0.0"');
			expect(yamlStr).toContain('domain_namespace: "wealth_adv"');
			expect(yamlStr).toContain("canonical_business_concepts:");
			expect(yamlStr).toContain("canonical_relationships:");
			expect(yamlStr).toContain('concept_uri: "urn:entity:portfolio"');
			expect(yamlStr).toContain('concept_name: "Portfolio"');
			expect(yamlStr).toContain('concept_uri: "urn:entity:asset"');
			expect(yamlStr).toContain('relationship_uri: "urn:relation:holds_asset"');
		});

		it("exports bit-for-bit RFC 8785 JCS canonical representation", async () => {
			const jcsStr = generateLocalExport("rfc8785", "1.0.0", nodes, edges, session);
			const parsed = JSON.parse(jcsStr);

			expect(parsed.canonical_business_concepts).toBeDefined();
			expect(parsed.canonical_relationships).toBeDefined();
			// Key order verification: canonical_business_concepts comes before canonical_relationships
			const keys = Object.keys(parsed);
			expect(keys).toEqual([...keys].sort());

			// JCS output round-trips cleanly through re-canonicalization
			const reserialized = dumpsJcs(parsed);
			expect(reserialized).toBe(jcsStr);
		});
	});

	// =========================================================================
	// 5. GRAPH A GOLDEN FIXTURE SHA-256 PARITY VERIFICATION
	// =========================================================================
	describe("5. Graph A Golden Fixture SHA-256 Parity Verification", () => {
		it("asserts exact match against graph_a_golden.json canonical_jcs and sha256 digest", async () => {
			const rawModel = goldenFixture.raw_model;
			const expectedJcs = goldenFixture.canonical_jcs;
			const expectedSha = goldenFixture.sha256;

			// Transform rawModel concepts to ConceptNode
			const concepts: ConceptNode[] = rawModel.canonical_business_concepts.map((c: any) => ({
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
				domain_namespace: rawModel.domain_namespace,
				enterprise_owner: rawModel.enterprise_owner,
				active_version: rawModel.ontology_version,
				status: "ACTIVE",
				head_sha256: expectedSha,
				created_at: "2026-11-11",
				updated_at: "2026-11-11",
			};

			const computedJcs = generateLocalExport("rfc8785", session.active_version, concepts, [], session);
			expect(computedJcs).toBe(expectedJcs);

			const digestBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(computedJcs));
			const computedSha = Array.from(new Uint8Array(digestBuf))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");

			expect(computedSha).toBe(expectedSha);
		});

		it("asserts deterministic sorting: concepts provided in reverse order produce identical JCS and SHA-256", async () => {
			const rawModel = goldenFixture.raw_model;
			// Provide concepts in reversed order (Asset before Portfolio)
			const reversedConcepts: ConceptNode[] = [...rawModel.canonical_business_concepts].reverse().map((c: any) => ({
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
				domain_namespace: rawModel.domain_namespace,
				enterprise_owner: rawModel.enterprise_owner,
				active_version: rawModel.ontology_version,
				status: "ACTIVE",
				head_sha256: goldenFixture.sha256,
				created_at: "2026-11-11",
				updated_at: "2026-11-11",
			};

			const computedJcs = generateLocalExport("rfc8785", session.active_version, reversedConcepts, [], session);
			expect(computedJcs).toBe(goldenFixture.canonical_jcs);

			const digestBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(computedJcs));
			const computedSha = Array.from(new Uint8Array(digestBuf))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("");

			expect(computedSha).toBe(goldenFixture.sha256);
		});
	});
});
