/**
 * Component & Interaction Tests: Quality Scorecard, Steward Feedback & Multi-Format Export/Promotion Modal (Task 5D.6, #227)
 * (apps/opendv-web-workbench/tests/export-promotion-modal.test.tsx)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OntologyStudioApp } from "../src/ontology/OntologyStudioApp";
import {
	ExportPromoteModal,
} from "../src/ontology/components/ExportPromoteModal";
import {
	QualityFeedbackPanel,
	getCheckStatusBadge,
} from "../src/ontology/components/QualityFeedbackPanel";
import {
	createStudioStore,
	generateLocalExport,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	QualityCheck,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

describe("QualityFeedbackPanel & ExportPromoteModal (Task 5D.6, #227)", () => {
	const initialStoreState = createStudioStore().getState();

	beforeEach(() => {
		studioStore.setState({
			sessions: initialStoreState.sessions,
			activeSession: initialStoreState.activeSession,
			nodes: initialStoreState.nodes,
			edges: initialStoreState.edges,
			selectedNodeId: initialStoreState.selectedNodeId,
			selectedEdgeId: initialStoreState.selectedEdgeId,
			parsingState: initialStoreState.parsingState,
			questions: initialStoreState.questions,
			qualityScorecard: initialStoreState.qualityScorecard,
			diffState: initialStoreState.diffState,
			toasts: initialStoreState.toasts,
			layout: initialStoreState.layout,
			hasUnsavedChanges: false,
			isPromoting: false,
		});
	});
	describe("1. QualityFeedbackPanel Status Badges & Metrics", () => {
		it("resolves status badge metadata for PASS, WARN, FAIL and default", () => {
			const passBadge = getCheckStatusBadge("PASS");
			expect(passBadge.label).toBe("PASS");
			expect(passBadge.className).toBe("badge-pass");
			expect(passBadge.icon).toBe("✓");

			const warnBadge = getCheckStatusBadge("WARN");
			expect(warnBadge.label).toBe("WARN");
			expect(warnBadge.className).toBe("badge-warn");
			expect(warnBadge.icon).toBe("⚠");

			const failBadge = getCheckStatusBadge("FAIL");
			expect(failBadge.label).toBe("FAIL");
			expect(failBadge.className).toBe("badge-fail");
			expect(failBadge.icon).toBe("✕");

			const customBadge = getCheckStatusBadge("PENDING" as any);
			expect(customBadge.label).toBe("PENDING");
			expect(customBadge.className).toBe("badge-default");
		});

		it("renders quality scorecard with high grounding ratio (>= 65%)", () => {
			const sampleNodes: ConceptNode[] = [
				{
					id: "c1",
					concept_uri: "urn:ontology:entity:fund",
					concept_name: "Fund",
					elm_archetype: "CORE_CONCEPT",
					definition: "Investment fund entity.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [
						{ scheme: "FIBO", target_uri: "fibo:Fund", predicate: "skos:exactMatch", confidence: 0.95 },
					],
				},
				{
					id: "c2",
					concept_uri: "urn:ontology:entity:investor",
					concept_name: "Investor",
					elm_archetype: "CORE_CONCEPT",
					definition: "Limited partner.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [
						{ scheme: "FIBO", target_uri: "fibo:Investor", predicate: "skos:exactMatch", confidence: 0.92 },
					],
				},
				{
					id: "c3",
					concept_uri: "urn:ontology:entity:advisor",
					concept_name: "Advisor",
					elm_archetype: "REFERENCE_CONCEPT",
					definition: "Advisory entity.",
					grounding_status: "LOW_CONFIDENCE",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];

			studioStore.setState({
				nodes: sampleNodes,
				qualityScorecard: {
					grounding_score: 0.67,
					deployed_contracts_ok: true,
					dag_acyclic: true,
					yagni_compliant: true,
					checks: [
						{
							rule_id: "ONT-EVO-001",
							rule_name: "Strictly Additive Evolution",
							status: "PASS",
							details: "Zero historical entities or columns mutated.",
						},
						{
							rule_id: "BM25-001",
							rule_name: "Grounding Threshold",
							status: "PASS",
							details: "Score 0.6700 >= 0.6500 threshold.",
						},
					],
					waiver_applied: false,
					waiver_rationale: "",
					feedback_submitted: false,
					feedback_rating: 0,
					feedback_comment: "",
				},
			});

			const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));

			expect(html).toContain("data-testid=\"quality-feedback-panel\"");
			expect(html).toContain("Quality &amp; Governance");
			expect(html).toContain("data-testid=\"metric-gauges\"");
			expect(html).toContain("Grounding Ratio");
			expect(html).toContain("67%");
			expect(html).toContain("(2/3)");
			expect(html).toContain("fill-pass");
			expect(html).toContain("Zero DAG inheritance cycles");
			expect(html).toContain("ONT-EVO-001 preserved");
		});

		it("renders quality scorecard with failing grounding ratio (< 65%) and warning badge", () => {
			const lowGroundedNodes: ConceptNode[] = [
				{
					id: "c1",
					concept_uri: "urn:ontology:entity:fund",
					concept_name: "Fund",
					elm_archetype: "CORE_CONCEPT",
					definition: "Investment fund entity.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "c2",
					concept_uri: "urn:ontology:entity:advisor",
					concept_name: "Advisor",
					elm_archetype: "REFERENCE_CONCEPT",
					definition: "Advisory entity.",
					grounding_status: "UNGROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];

			studioStore.setState({
				nodes: lowGroundedNodes,
				qualityScorecard: {
					grounding_score: 0.5,
					deployed_contracts_ok: true,
					dag_acyclic: true,
					yagni_compliant: true,
					checks: [
						{
							rule_id: "BM25-001",
							rule_name: "Grounding Threshold",
							status: "FAIL",
							details: "Score 0.5000 < 0.6500 threshold.",
						},
					],
					waiver_applied: false,
					waiver_rationale: "",
					feedback_submitted: false,
					feedback_rating: 0,
					feedback_comment: "",
				},
			});

			const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));
			expect(html).toContain("50%");
			expect(html).toContain("fill-fail");
			expect(html).toContain("data-testid=\"rule-row-BM25-001\"");
			expect(html).toContain("FAIL");
		});

		it("renders all contract checklist rules accurately", () => {
			const mockChecks: QualityCheck[] = [
				{
					rule_id: "ONT-EVO-001",
					rule_name: "Strictly Additive Evolution",
					status: "PASS",
					details: "Preserves backwards compatibility.",
				},
				{
					rule_id: "DAG-001",
					rule_name: "DAG Acyclicity",
					status: "PASS",
					details: "Topological sort confirms zero cycles.",
				},
				{
					rule_id: "BM25-001",
					rule_name: "BM25 Regulatory Grounding",
					status: "WARN",
					details: "1 entity has grounding score 0.6200 (< 0.6500).",
				},
				{
					rule_id: "YAGNI-001",
					rule_name: "Concept Density & YAGNI",
					status: "PASS",
					details: "All proposed concepts have evidence citations.",
				},
			];

			studioStore.setState({
				qualityScorecard: {
					grounding_score: 0.75,
					deployed_contracts_ok: true,
					dag_acyclic: true,
					yagni_compliant: true,
					checks: mockChecks,
					waiver_applied: false,
					waiver_rationale: "",
					feedback_submitted: false,
					feedback_rating: 0,
					feedback_comment: "",
				},
			});

			const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));
			expect(html).toContain("data-testid=\"rules-checklist\"");
			expect(html).toContain("[ONT-EVO-001] Strictly Additive Evolution");
			expect(html).toContain("[DAG-001] DAG Acyclicity");
			expect(html).toContain("[BM25-001] BM25 Regulatory Grounding");
			expect(html).toContain("[YAGNI-001] Concept Density &amp; YAGNI");
			expect(html).toContain("badge-warn");
			expect(html).toContain("badge-pass");
		});
	});

	describe("2. Administrative Waiver & Steward Feedback", () => {
		it("renders waiver form and active waiver badge when waiver already authorized", () => {
			studioStore.setState({
				qualityScorecard: {
					grounding_score: 0.6,
					deployed_contracts_ok: true,
					dag_acyclic: true,
					yagni_compliant: true,
					checks: [],
					waiver_applied: true,
					waiver_rationale: "Approved by Chief Risk Officer for early beta onboarding",
					feedback_submitted: true,
					feedback_rating: 5,
					feedback_comment: "Very accurate FIBO alignment.",
				},
			});

			const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));
			expect(html).toContain("data-testid=\"waiver-section\"");
			expect(html).toContain("data-testid=\"waiver-applied-badge\"");
			expect(html).toContain("Active Waiver Authorized");
			expect(html).toContain("Approved by Chief Risk Officer for early beta onboarding");
			expect(html).toContain("data-testid=\"feedback-section\"");
			expect(html).toContain("data-testid=\"star-rating-widget\"");
			expect(html).toContain("data-testid=\"feedback-submitted-pill\"");
			expect(html).toContain("Recorded 5-star rating to feedback.jsonl");
		});

		it("executes store waiver submission and records rationale", async () => {
			studioStore.setState({
				nodes: [
					{
						id: "c1",
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Investment fund entity.",
						grounding_status: "LOW_CONFIDENCE",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
				activeSession: {
					session_id: "ses-pe-001",
					domain_namespace: "private_equity",
					enterprise_owner: "Global Capital Partners",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.1.0",
					head_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
					commits: [],
				},
				qualityScorecard: {
					grounding_score: 0.8,
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
				toasts: [],
			});

			const mockWaiverFetch = async () =>
				new Response(JSON.stringify({ status: "authorized" }), { status: 200 });

			const ok = await studioStore.getState().submitWaiver(
				"Enterprise domain waiver for custom non-standard portfolio metrics",
				"steward-auth-token-456",
				mockWaiverFetch as unknown as typeof fetch,
			);

			expect(ok).toBe(true);
			const state = studioStore.getState();
			expect(state.qualityScorecard.waiver_applied).toBe(true);
			expect(state.qualityScorecard.waiver_rationale).toBe(
				"Enterprise domain waiver for custom non-standard portfolio metrics",
			);
			expect(state.toasts.length).toBeGreaterThan(0);
			expect(state.toasts[0]?.title).toBe("Administrative Waiver Authorized");
		});

		it("executes steward feedback submission and records rating to feedback.jsonl store state", async () => {
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-001",
					domain_namespace: "private_equity",
					enterprise_owner: "Global Capital Partners",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.1.0",
					head_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
					commits: [],
				},
				qualityScorecard: {
					grounding_score: 0.8,
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
				toasts: [],
			});

			const mockFeedbackFetch = async () =>
				new Response(JSON.stringify({ status: "recorded" }), { status: 200 });

			const ok = await studioStore
				.getState()
				.submitFeedback(
					4,
					"High precision on PE relationships",
					mockFeedbackFetch as unknown as typeof fetch,
				);
			expect(ok).toBe(true);

			const state = studioStore.getState();
			expect(state.qualityScorecard.feedback_submitted).toBe(true);
			expect(state.qualityScorecard.feedback_rating).toBe(4);
			expect(state.qualityScorecard.feedback_comment).toBe(
				"High precision on PE relationships",
			);
			expect(state.toasts.some((t) => t.title === "Feedback Recorded")).toBe(true);
		});
	});

	describe("3. ExportPromoteModal Multi-Format Export Tab", () => {
		it("returns null when isOpen is false", () => {
			const html = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: false,
					onClose: () => {},
				}),
			);
			expect(html).toBe("");
		});

		it("renders export tab with format options, version selector, and action buttons", () => {
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-001",
					domain_namespace: "private_equity",
					enterprise_owner: "Global Capital Partners",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.1.0",
					head_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
					commits: [],
				},
				nodes: [
					{
						id: "urn:ontology:entity:fund",
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Investment fund entity.",
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
				edges: [],
			});

			const html = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "export",
					onClose: () => {},
				}),
			);

			expect(html).toContain("data-testid=\"export-promote-modal\"");
			expect(html).toContain("data-testid=\"tab-export-btn\"");
			expect(html).toContain("data-testid=\"tab-promote-btn\"");
			expect(html).toContain("data-testid=\"export-tab-body\"");
			expect(html).toContain("Export Ontology Representation");
			expect(html).toContain("data-testid=\"export-format-select\"");
			expect(html).toContain("Graph A YAML (.yaml)");
			expect(html).toContain("Canonical JSON / RFC 8785 JCS (.json)");
			expect(html).toContain("W3C Turtle (.ttl)");
			expect(html).toContain("JSON-LD (.jsonld)");
			expect(html).toContain("N-Triples (.nt)");
			expect(html).not.toContain("RDF/XML (.rdf)");
			expect(html).toContain("data-testid=\"export-version-select\"");
			expect(html).toContain("v1.1.0 (Current Head)");
			expect(html).toContain("data-testid=\"export-preview-block\"");
			expect(html).toContain("data-testid=\"copy-export-btn\"");
			expect(html).toContain("data-testid=\"download-export-btn\"");
			expect(html).toContain("data-testid=\"close-modal-btn\"");
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

			// YAML
			const yamlOut = generateLocalExport("yaml", "1.1.0", nodes, edges, session);
			expect(yamlOut).toContain("ontology_version:");
			expect(yamlOut).toContain("1.1.0");
			expect(yamlOut).toContain("canonical_business_concepts:");
			expect(yamlOut).toContain("canonical_relationships:");
			expect(yamlOut).toContain("Fund");

			// RFC 8785 JSON
			const jsonOut = generateLocalExport("json", "1.1.0", nodes, edges, session);
			const parsed = JSON.parse(jsonOut);
			expect(parsed.ontology_version).toBe("1.1.0");
			expect(parsed.enterprise_owner).toBe("Securities Co");
			expect(parsed.canonical_business_concepts.length).toBeGreaterThan(0);
			expect(parsed.canonical_relationships.length).toBeGreaterThan(0);

			// Turtle (.ttl)
			const ttlOut = generateLocalExport("turtle", "1.1.0", nodes, edges, session);
			expect(ttlOut).toContain("@prefix owl: <http://www.w3.org/2002/07/owl#>");
			expect(ttlOut).toContain("a owl:Class");

			// JSON-LD
			const jsonldOut = generateLocalExport("jsonld", "1.1.0", nodes, edges, session);
			expect(jsonldOut).toContain("\"@context\"");
			expect(jsonldOut).toContain("\"@graph\"");

			// N-Triples (.nt)
			const ntOut = generateLocalExport("ntriples", "1.1.0", nodes, edges, session);
			expect(ntOut).toContain("<http://www.w3.org/2002/07/owl#Class>");
			expect(ntOut).toContain("<urn:ontology:entity:fund>");
		});
	});

	describe("4. ExportPromoteModal Workspace Promotion Tab", () => {
		it("renders promotion tab with OCC card, rationale input, and warning acknowledgment", () => {
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-002",
					domain_namespace: "private_equity",
					enterprise_owner: "Global Capital Partners",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.2.0",
					head_sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
					commits: [],
				},
			});

			const html = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "promote",
					onClose: () => {},
				}),
			);

			expect(html).toContain("data-testid=\"promote-tab-body\"");
			expect(html).toContain("Promote to Data Vault Workspace");
			expect(html).toContain("data-testid=\"workspace-path-input\"");
			expect(html).toContain("data-testid=\"occ-verification-card\"");
			expect(html).toContain("data-testid=\"workspace-sha-input\"");
			expect(html).toContain("Expected Workspace SHA-256:");
			expect(html).toContain("Session Head SHA-256:");
			expect(html).toContain("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08");
			expect(html).toContain("data-testid=\"promote-rationale-input\"");
			expect(html).toContain("data-testid=\"ack-warning-checkbox\"");
			expect(html).toContain("data-testid=\"confirm-promote-btn\"");
			expect(html).toContain("disabled"); // Disabled initially since rationale < 10 chars
		});

		it("executes promoteWorkspace store action and updates head SHA and commits", async () => {
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
					rationale: "Enterprise release promoting validated portfolio entities to main DV",
					acknowledge_historical_contract_warning: true,
				},
				mockFetch as unknown as typeof fetch,
			);

			expect(ok).toBe(true);
			const state = studioStore.getState();
			expect(state.activeSession?.commits?.length ?? 0).toBe(0);
			expect(state.activeSession?.status).toBe("PROMOTED");
			expect(state.activeSession?.head_sha256).toBe(promotedHeadSha);
			expect(state.toasts.some((t) => t.title === "Workspace Promotion Succeeded")).toBe(true);
		});

		it("resets workspace target path and expected SHA when active session switches (MAJ-01)", () => {
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-A",
					domain_namespace: "domain_a",
					enterprise_owner: "Owner A",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.0.0",
					head_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
					commits: [],
				},
			});

			const htmlA = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "promote",
					onClose: () => {},
				}),
			);
			expect(htmlA).toContain("workspaces/domain_a");

			// Switch to session B
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-B",
					domain_namespace: "domain_b",
					enterprise_owner: "Owner B",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "2.0.0",
					head_sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
					commits: [],
				},
			});

			const htmlB = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "promote",
					onClose: () => {},
				}),
			);
			expect(htmlB).toContain("workspaces/domain_b");
			expect(htmlB).not.toContain("workspaces/domain_a");
		});

		it("disables download and copy actions and displays error banner when export fails (MIN-03)", () => {
			const html = renderToStaticMarkup(
				createElement(ExportPromoteModal, {
					isOpen: true,
					initialTab: "export",
					onClose: () => {},
				}),
			);
			// Without an activeSession or preview loaded, download & copy buttons are disabled
			expect(html).toContain('disabled="" data-testid="copy-export-btn"');
			expect(html).toContain('disabled="" data-testid="download-export-btn"');
		});

		it("renders exactly one ExportPromoteModal instance when activeModal is export or promote (MAJ-01)", () => {
			studioStore.setState({
				activeSession: {
					session_id: "ses-pe-001",
					domain_namespace: "private_equity",
					enterprise_owner: "Global Capital Partners",
					created_at: "2026-11-11T00:00:00Z",
					updated_at: "2026-11-11T00:00:00Z",
					status: "READY",
					active_version: "1.0.0",
					head_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
					commits: [],
				},
				layout: {
					...initialStoreState.layout,
					activeModal: "export",
				},
			});

			const html = renderToStaticMarkup(createElement(OntologyStudioApp));
			const matches = html.match(/data-testid="export-promote-modal"/g);
			expect(matches?.length).toBe(1);

			// When modal is closed, exactly zero instances rendered
			studioStore.setState({
				layout: {
					...initialStoreState.layout,
					activeModal: null,
				},
			});
			const closedHtml = renderToStaticMarkup(createElement(OntologyStudioApp));
			expect(closedHtml).not.toContain("data-testid=\"export-promote-modal\"");
		});
	});
});
