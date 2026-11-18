/**
 * End-to-End Walkthrough Remediation Tests (ISS-01 through ISS-10)
 * (apps/opendv-web-workbench/tests/ontology-e2e-walkthrough-fixes.test.tsx)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { ReactFlowProvider } from "@xyflow/react";
import type React from "react";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OntologyStudioApp } from "../src/ontology/OntologyStudioApp";
import { ClarificationPanel } from "../src/ontology/components/ClarificationPanel";
import { DiffOverlay } from "../src/ontology/components/DiffOverlay";
import { ExportPromoteModal } from "../src/ontology/components/ExportPromoteModal";
import { ConceptNodeComponent } from "../src/ontology/components/GraphCanvas";
import {
	ProposalDetailsView,
	getEditableOpPrimaryField,
	validateProposalDraftOperations,
} from "../src/ontology/components/ProposalDetailsView";
import { QualityFeedbackPanel } from "../src/ontology/components/QualityFeedbackPanel";
import { SourcesPanel } from "../src/ontology/components/SourcesPanel";
import { VisualEditorDrawer } from "../src/ontology/components/VisualEditorDrawer";
import {
	CLIPBOARD_FEEDBACK_RESET_MS,
	ELK_LAYOUT_DEFAULT_TIMEOUT_MS,
	GROUNDING_MIN_CONFIDENCE,
	LOCAL_TOKEN_PATH_HINT,
	MAX_SYNONYMS_PER_CONCEPT,
	MIN_RATIONALE_LENGTH,
	NEW_PROPOSAL_DRAFT_KEY,
	SAVE_FEEDBACK_RESET_MS,
	STUDIO_MAX_UPLOAD_BYTES,
} from "../src/ontology/constants";
import {
	createStudioStore,
	isSupportedSourceFile,
	normalizeProposalOperationsForSubmission,
	resolveProposalBaseVersionAndSha,
	sortProposalsForStewardship,
	studioStore,
} from "../src/ontology/store/studio-store";
import type { ConceptNode } from "../src/ontology/store/types";

describe("Ontology Studio E2E Walkthrough Fixes (ISS-01 through ISS-10)", () => {
	beforeEach(() => {
		studioStore.getState().reset();
	});

	it("ISS-01..ISS-04: index.css defines dark React Flow controls/minimap, stacked panel headers, mapping tag clipping, and wrap layouts", () => {
		const cssPath = path.resolve(import.meta.dir, "../src/index.css");
		const css = readFileSync(cssPath, "utf-8");

		// ISS-01: Dark-mode React Flow custom properties and explicit overrides
		expect(css).toContain("--xy-minimap-background-color-default: #0f172a");
		expect(css).toContain(
			"--xy-controls-button-background-color-default: #1e293b",
		);
		expect(css).toContain("--xy-controls-button-color-default: #f8fafc");
		expect(css).toContain(
			".canvas-flow-wrapper .react-flow__controls-button svg",
		);
		expect(css).toContain("fill: currentColor;");
		expect(css).toContain(".canvas-flow-wrapper .react-flow__minimap-node");

		// ISS-02: Vertical .panel-header stacking in left drawer while keeping sessions row header
		expect(css).toMatch(/\.panel-header\s*\{[^}]*flex-direction:\s*column/s);
		expect(css).toMatch(/\.panel-title-group\s*\{[^}]*display:\s*flex/s);
		expect(css).toMatch(
			/\.sessions-list-panel\s+\.panel-header\s*\{[^}]*flex-direction:\s*row/s,
		);

		// ISS-03: Concept node overflow and mapping tag ellipsis clipping
		expect(css).toMatch(/\.concept-node\s*\{[^}]*overflow:\s*hidden/s);
		expect(css).toMatch(/\.mapping-tag\s*\{[^}]*text-overflow:\s*ellipsis/s);

		// ISS-04: Quality gauge spacing and panel flexbox/wrap rules
		expect(css).toContain(".gauge-value-row");
		expect(css).toContain(".clarification-toolbar-actions");
		expect(css).toContain(".panel-badges");
		expect(css).toContain(".proposal-card-header");
		expect(css).toContain(".proposal-ratification-controls");
		expect(css).toContain(".proposal-card-actions");
		expect(css).toContain(".diff-controls-bar");
		expect(css).toContain(".diff-summary-badges");
		expect(css).toContain(".diff-mode-toggle");
		expect(css).toContain(".modal-tabs");
		expect(css).toContain(".format-pills");
	});

	it("ISS-03: GraphCanvas extracts terminal IRI fragment for full HTTP URIs and CURIEs with full IRI tooltip", () => {
		const fullUri =
			"https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/FinancialAgreement";
		const curieUri = "fibo-be-le-cb:CommercialBank";
		const concept: ConceptNode = {
			id: "urn:ontology:entity:agreement",
			concept_uri: "urn:ontology:entity:agreement",
			concept_name: "Advisory Agreement",
			elm_archetype: "CORE_CONCEPT",
			definition: "Advisory mandate.",
			grounding_status: "GROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri: fullUri,
					confidence: 0.91,
					predicate: "skos:exactMatch",
				},
				{
					scheme: "fibo",
					target_uri: curieUri,
					confidence: 0.88,
					predicate: "skos:closeMatch",
				},
			],
		};

		const html = renderToStaticMarkup(
			createElement(
				ReactFlowProvider,
				null,
				createElement(
					ConceptNodeComponent as unknown as React.ComponentType<
						Record<string, unknown>
					>,
					{
						data: {
							concept,
							lodTier: 3,
							isSelected: false,
							isDimmed: false,
						},
					},
				),
			),
		);

		expect(html).toContain("fibo: FinancialAgreement");
		expect(html).toContain("fibo: CommercialBank");
		expect(html).toContain(`title="fibo: ${fullUri}"`);
		expect(html).not.toContain(">fibo: //spec.edmcouncil.org");
	});

	it("ISS-05 & ISS-06: hydrates role from /init and /auth/token and surfaces actionable 409 hint on duplicate loopback init", async () => {
		const store = createStudioStore();

		// 1. /auth/token response role ('editor') hydrates userRole cleanly
		const mockNonceFetch: typeof fetch = async () =>
			new Response(
				JSON.stringify({
					status: "authenticated",
					principal: "human:alice",
					role: "editor",
				}),
				{ status: 200, headers: { "Content-Type": "application/json" } },
			);
		const okNonce = await store
			.getState()
			.exchangeLaunchNonce("nonce-123", mockNonceFetch);
		expect(okNonce).toBe(true);
		expect(store.getState().userRole).toBe("editor");

		// 2. /init 200 response hydrates role ('admin') even with opaque non-JWT token
		const mockInitOkFetch: typeof fetch = async (input) => {
			const url = String(input);
			if (url.endsWith("/init")) {
				return new Response(
					JSON.stringify({
						status: "initialized",
						token_path: LOCAL_TOKEN_PATH_HINT,
						principal: "human:local_admin",
						role: "admin",
						local_token: "opaque-local-token-abcdef",
					}),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			return new Response(JSON.stringify([]), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			});
		};
		const okInit = await store
			.getState()
			.bootstrapLoopbackAuth(mockInitOkFetch);
		expect(okInit).toBe(true);
		expect(store.getState().userRole).toBe("admin");
		expect(store.getState().authInitConflict).toBe(false);

		// 3. /init 409 conflict sets inline guidance hint and renders in OntologyStudioApp
		const mockInit409Fetch: typeof fetch = async () =>
			new Response(
				JSON.stringify({
					error_code: "STUDIO_ALREADY_INITIALIZED",
					detail: "Studio has already been initialized.",
				}),
				{ status: 409, headers: { "Content-Type": "application/json" } },
			);
		const conflictRes = await store
			.getState()
			.bootstrapLoopbackAuth(mockInit409Fetch);
		expect(conflictRes).toBe(false);
		expect(store.getState().authRequired).toBe(true);
		expect(store.getState().authInitConflict).toBe(true);
		expect(store.getState().authHint).toContain(LOCAL_TOKEN_PATH_HINT);
		expect(store.getState().authHint).toContain("uv run opendv serve --open");

		studioStore.setState({
			authRequired: store.getState().authRequired,
			authInitConflict: store.getState().authInitConflict,
			authHint: store.getState().authHint,
		});
		const appHtml = renderToStaticMarkup(createElement(OntologyStudioApp));
		expect(appHtml).toContain('data-testid="auth-conflict-hint"');
		expect(appHtml).toContain(LOCAL_TOKEN_PATH_HINT);
	});

	it("ISS-08: supports comparing committed baseline against uncommitted Working Draft ('draft') without hitting /export?version=draft", async () => {
		const store = createStudioStore();
		const committedConcept: ConceptNode = {
			id: "urn:ontology:entity:account",
			concept_uri: "urn:ontology:entity:account",
			concept_name: "Client Account",
			elm_archetype: "CORE_CONCEPT",
			definition: "Client custody account.",
			grounding_status: "GROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const draftAddedConcept: ConceptNode = {
			id: "urn:ontology:entity:advisory_fee",
			concept_uri: "urn:ontology:entity:advisory_fee",
			concept_name: "Advisory Fee",
			elm_archetype: "CORE_CONCEPT",
			definition: "Fee charged for advisory services.",
			grounding_status: "UNGROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};

		store.setState({
			activeSession: {
				session_id: "wealth-draft-diff",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				commits: [{ version: "1.0.0", sha256: "a".repeat(64) }],
			},
			committedNodes: [committedConcept],
			committedEdges: [],
			nodes: [committedConcept, draftAddedConcept],
			edges: [],
			hasUnsavedChanges: true,
		});

		const fetchedUrls: string[] = [];
		const spyFetch: typeof fetch = async (input) => {
			fetchedUrls.push(String(input));
			throw new Error(`Unexpected network fetch for ${String(input)}`);
		};

		await store.getState().loadAndComputeDiff("1.0.0", "draft", spyFetch);

		expect(fetchedUrls).toHaveLength(0);
		const diff = store.getState().diffState;
		expect(diff.active).toBe(true);
		expect(diff.base_version).toBe("1.0.0");
		expect(diff.target_version).toBe("draft");
		expect(diff.summary.added_count).toBe(1);
		expect(diff.records[0]?.label).toBe("Advisory Fee");

		studioStore.setState({
			activeSession: store.getState().activeSession,
			hasUnsavedChanges: true,
			diffState: diff,
		});
		const diffHtml = renderToStaticMarkup(createElement(DiffOverlay));
		expect(diffHtml).toContain("Ontology Version Diff: v1.0.0 → Working Draft");
		expect(diffHtml).toContain('<option value="draft"');
		expect(diffHtml).toContain("Working Draft (Uncommitted)");
	});

	it("ISS-09: ClarificationPanel disables Accept on 0-op proposals while keeping Reject enabled, and VisualEditorDrawer explains ONT-EVO-002 grounding", () => {
		studioStore.setState({
			userRole: "admin",
			activeSession: {
				session_id: "zero-op-session",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "0.0.0",
				head_sha256: "0".repeat(64),
			},
			proposals: [
				{
					proposal_id: "prop-empty",
					status: "PENDING",
					base_version: "0.0.0",
					rationale: "Empty run before source parsing.",
					operations: [],
				},
				{
					proposal_id: "prop-ready",
					status: "PENDING",
					base_version: "0.0.0",
					rationale: "Parsed policy concepts.",
					operations: [{ op: "add_concept" }],
				},
			],
		});

		const cpHtml = renderToStaticMarkup(createElement(ClarificationPanel));
		expect(cpHtml).toContain("0 ops");
		expect(cpHtml).toContain("1 ops");
		expect(cpHtml).toContain(
			"0 operations — upload and parse source documents first",
		);
		expect(cpHtml).toMatch(
			/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="accept-proposal-btn-prop-empty")[^>]*>/,
		);
		expect(cpHtml).not.toMatch(
			/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="reject-proposal-btn-prop-empty")[^>]*>/,
		);
		expect(cpHtml).not.toMatch(
			/<button(?=[^>]*\bdisabled="")(?=[^>]*data-testid="accept-proposal-btn-prop-ready")[^>]*>/,
		);

		studioStore.setState({
			selectedNodeId: "urn:ontology:entity:custom",
			nodes: [
				{
					id: "urn:ontology:entity:custom",
					concept_uri: "urn:ontology:entity:custom",
					concept_name: "Custom Client Tier",
					elm_archetype: "CORE_CONCEPT",
					definition: "Custom internal wealth tier.",
					grounding_status: "UNGROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			],
		});
		const drawerHtml = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(drawerHtml).toContain("ONT-EVO-002 Grounding:");
		expect(drawerHtml).toContain("skos:exactMatch");
		expect(drawerHtml).toContain("Steward Waiver");
	});

	it("ISS-10: centralizes Studio constants matching backend limits and contains zero hardcoded loopback ports in src/ontology", () => {
		expect(STUDIO_MAX_UPLOAD_BYTES).toBe(100 * 1024 * 1024);
		expect(MAX_SYNONYMS_PER_CONCEPT).toBe(50);
		expect(GROUNDING_MIN_CONFIDENCE).toBe(0.65);
		expect(CLIPBOARD_FEEDBACK_RESET_MS).toBe(2000);
		expect(SAVE_FEEDBACK_RESET_MS).toBe(2500);
		expect(ELK_LAYOUT_DEFAULT_TIMEOUT_MS).toBe(250);
		expect(MIN_RATIONALE_LENGTH).toBe(10);

		const constantsSrc = readFileSync(
			path.resolve(import.meta.dir, "../src/ontology/constants.ts"),
			"utf-8",
		);
		expect(constantsSrc).not.toMatch(/127\.0\.0\.1|:8000|:8420/);
	});

	it("AUDIT-MAJOR-01: loadAndComputeDiff resolves committed head cleanly when dirty, computeDiff detects attribute optionality/description and mapping changes, and empty draft diff shows Working Draft", async () => {
		const store = createStudioStore({ userRole: "admin" });
		const committedConcept: ConceptNode = {
			id: "urn:ontology:entity:account",
			concept_uri: "urn:ontology:entity:account",
			concept_name: "Wealth Account",
			elm_archetype: "HUB",
			definition: "Client custody account.",
			grounding_status: "GROUNDED",
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: true,
					description: "Initial risk bucket",
				},
			],
			synonyms: [],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri:
						"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
					predicate: "skos:broadMatch",
					confidence: 0.72,
				},
			],
		};
		const dirtyModifiedConcept: ConceptNode = {
			...committedConcept,
			concept_name: "Wealth Account (Dirty Uncommitted)",
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: false,
					description: "Strict KYC risk bucket",
				},
			],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri:
						"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
					predicate: "skos:exactMatch",
					confidence: 0.91,
				},
			],
		};

		store.setState({
			activeSession: {
				session_id: "diff-audit-test",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "b".repeat(64),
				created_at: "2026-11-14T00:00:00Z",
				updated_at: "2026-11-14T00:00:00Z",
				commits: [
					{
						commit_id: "c1",
						new_sha256: "b".repeat(64),
						version: "1.0.0",
						rationale: "Initial release",
						committed_at: "2026-11-14T00:00:00Z",
					},
				],
			},
			committedNodes: [committedConcept],
			committedEdges: [],
			nodes: [dirtyModifiedConcept],
			edges: [],
			hasUnsavedChanges: true,
		});

		const spyFetch: typeof fetch = async (input) => {
			throw new Error(`Unexpected network fetch for ${String(input)}`);
		};

		// Comparing 0.0.0 -> 1.0.0 while dirty must inspect committed 1.0.0 (not dirty draft)
		await store.getState().loadAndComputeDiff("0.0.0", "1.0.0", spyFetch);
		const committedDiff = store.getState().diffState;
		expect(committedDiff.summary.added_count).toBe(1);
		expect(committedDiff.records[0]?.label).toBe("Wealth Account");

		// Comparing draft -> 1.0.0 must compare dirty draft vs committed 1.0.0 (not dirty vs dirty)
		await store.getState().loadAndComputeDiff("draft", "1.0.0", spyFetch);
		const draftToHead = store.getState().diffState;
		expect(draftToHead.summary.modified_count).toBeGreaterThanOrEqual(1);
		expect(
			draftToHead.records.some(
				(r) =>
					r.item_type === "concept" &&
					r.label === "Wealth Account" &&
					r.details.includes("mappings changed"),
			),
		).toBe(true);

		// Forward committed -> draft detects name change, optional->mandatory tightening, and mapping change
		await store.getState().loadAndComputeDiff("1.0.0", "draft", spyFetch);
		const headToDraft = store.getState().diffState;
		expect(headToDraft.summary.breaking_count).toBe(2);
		expect(
			headToDraft.records.some(
				(r) =>
					r.identifier.endsWith("#attribute-optionality") &&
					r.is_breaking === true,
			),
		).toBe(true);
		expect(
			headToDraft.records.some(
				(r) =>
					r.item_type === "concept" && r.details.includes("mappings changed"),
			),
		).toBe(true);

		// Widening optionality (false -> true) + description update only produces non-breaking modified record
		const mandatoryBase: ConceptNode = {
			...committedConcept,
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: false,
					description: "Before",
				},
			],
		};
		const widenedTarget: ConceptNode = {
			...committedConcept,
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: true,
					description: "After",
				},
			],
		};
		store
			.getState()
			.computeDiff(
				"1.0.0",
				"1.1.0",
				{ nodes: [mandatoryBase], edges: [] },
				{ nodes: [widenedTarget], edges: [] },
			);
		expect(store.getState().diffState.summary.breaking_count).toBe(0);
		expect(store.getState().diffState.summary.modified_count).toBe(1);

		// Empty draft diff must display Working Draft rather than "vdraft"
		studioStore.setState({
			activeSession: store.getState().activeSession,
			hasUnsavedChanges: false,
			diffState: {
				active: true,
				mode: "unified",
				base_version: "1.0.0",
				target_version: "draft",
				records: [],
				summary: {
					added_count: 0,
					deleted_count: 0,
					modified_count: 0,
					breaking_count: 0,
				},
			},
		});
		const emptyDraftHtml = renderToStaticMarkup(createElement(DiffOverlay));
		expect(emptyDraftHtml).toContain("v1.0.0 and Working Draft");
		expect(emptyDraftHtml).not.toContain("vdraft");
	});

	it("AUDIT-MAJOR-02..03 & MINOR-01: theme CSS overrides, DiffOverlay grid class, centralized thresholds, and .markdown upload support", () => {
		const cssPath = path.resolve(import.meta.dir, "../src/index.css");
		const css = readFileSync(cssPath, "utf-8");
		expect(css).toContain(".diff-side-by-side-view");
		expect(css).not.toContain(".diff-side-by-side-grid");
		expect(css).toContain(
			'[data-theme="paper"] .canvas-flow-wrapper .react-flow__minimap-mask',
		);
		expect(css).toContain(
			'[data-theme="high-contrast"] .canvas-flow-wrapper .react-flow__controls-button',
		);
		expect(css).toContain("display: block;");

		const graphCanvasSrc = readFileSync(
			path.resolve(
				import.meta.dir,
				"../src/ontology/components/GraphCanvas.tsx",
			),
			"utf-8",
		);
		const viewportSrc = graphCanvasSrc.slice(
			graphCanvasSrc.indexOf("<ReactFlow"),
		);
		expect(viewportSrc).toContain("var(--dv-minimap-mask-bg)");
		expect(viewportSrc).toContain("var(--dv-border-strong)");
		expect(viewportSrc).not.toContain('"#374151"');
		expect(viewportSrc).not.toContain('"#1D4ED8"');
		expect(viewportSrc).not.toContain('"#1F2937"');

		studioStore.setState({
			userRole: "admin",
			activeSession: {
				session_id: "constants-render-test",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "c".repeat(64),
				created_at: "2026-11-14T00:00:00Z",
				updated_at: "2026-11-14T00:00:00Z",
			},
		});

		const qualityHtml = renderToStaticMarkup(
			createElement(QualityFeedbackPanel),
		);
		expect(qualityHtml).toContain(`${GROUNDING_MIN_CONFIDENCE * 100}%`);
		expect(qualityHtml).toContain(`≥ ${MIN_RATIONALE_LENGTH} chars`);

		const modalHtml = renderToStaticMarkup(
			createElement(ExportPromoteModal, {
				isOpen: true,
				initialTab: "promote",
				onClose: () => {},
			}),
		);
		expect(modalHtml).toContain(`minimum ${MIN_RATIONALE_LENGTH} characters`);

		expect(
			isSupportedSourceFile(
				"wealth_appendix.markdown",
				"application/octet-stream",
			),
		).toBe(true);
		const sourcesHtml = renderToStaticMarkup(createElement(SourcesPanel));
		expect(sourcesHtml).toContain(".markdown");
	});

	it("opens Candidate Proposal details tab with explanation and operations in right panel", () => {
		studioStore.getState().reset();
		studioStore.setState({
			userRole: "admin",
			activeSession: {
				session_id: "proposal-inspector-test",
				domain_namespace: "capital_markets",
				enterprise_owner: "Institutional Risk Architecture",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "d".repeat(64),
				created_at: "2026-11-14T00:00:00Z",
				updated_at: "2026-11-14T00:00:00Z",
			},
			proposals: [
				{
					proposal_id: "prop-bd4a8c90b20e62e5",
					session_id: "proposal-inspector-test",
					status: "ACCEPTED",
					base_version: "0.0.0",
					base_sha256: "a".repeat(64),
					proposer: "deterministic-pipeline",
					created_at: "2026-11-14T02:50:25Z",
					rationale: "Constructed from 4 regulatory PDFs.",
					operations: [
						{
							op_id: "op-hub-1",
							op_type: "add_concept",
							concept_uri: "urn:ontology:entity:collateral",
							concept_name: "Collateral",
							elm_archetype: "CORE_BUSINESS_CONCEPT",
							target_hub: "hub_collateral",
						},
						{
							op_id: "op-map-1",
							op_type: "add_external_mapping",
							concept_uri: "urn:ontology:entity:collateral",
							scheme: "fibo",
							target_uri:
								"https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/Collateral",
							predicate: "skos:exactMatch",
							mapping_confidence: 1.0,
						},
					],
				},
			],
		});

		const cpHtml = renderToStaticMarkup(createElement(ClarificationPanel));
		expect(cpHtml).toContain(
			'data-testid="inspect-proposal-btn-prop-bd4a8c90b20e62e5"',
		);
		expect(cpHtml).toContain("View Details");

		// Select proposal -> opens right panel directly on Proposal Details tab
		studioStore.getState().selectProposal("prop-bd4a8c90b20e62e5");
		let state = studioStore.getState();
		expect(state.selectedProposalId).toBe("prop-bd4a8c90b20e62e5");
		expect(state.layout.rightDrawerOpen).toBe(true);
		expect(state.layout.rightDrawerTab).toBe("proposal");

		const drawerHtml = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(drawerHtml).toContain('data-testid="right-drawer-tabs"');
		expect(drawerHtml).toContain('data-testid="right-tab-proposal"');
		expect(drawerHtml).toContain('data-testid="proposal-details-panel"');
		expect(drawerHtml).toContain("What is a Candidate Proposal?");
		expect(drawerHtml).toContain("OntologyProposal");
		expect(drawerHtml).toContain("Concepts (+1)");
		expect(drawerHtml).toContain("FIBO Mappings (+1)");
		expect(drawerHtml).toContain("hub_collateral");
		expect(drawerHtml).toContain("fibo:Collateral");

		// Switching back to Concept Editor when a concept is selected preserves tab semantics
		studioStore.setState({
			nodes: [
				{
					id: "urn:ontology:entity:collateral",
					concept_uri: "urn:ontology:entity:collateral",
					concept_name: "Collateral",
					elm_archetype: "CORE_BUSINESS_CONCEPT",
					definition: "Pledged assets securing an obligation.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			],
		});
		studioStore.getState().selectNode("urn:ontology:entity:collateral");
		state = studioStore.getState();
		expect(state.layout.rightDrawerTab).toBe("concept");
		const conceptHtml = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(conceptHtml).toContain("CONCEPT EDITOR");
		expect(conceptHtml).toContain("Proposal Details (1)");

		studioStore.getState().setRightDrawerTab("proposal");
		expect(studioStore.getState().layout.rightDrawerTab).toBe("proposal");

		studioStore.getState().selectProposal(null);
		expect(studioStore.getState().layout.rightDrawerTab).toBe("concept");
		expect(studioStore.getState().layout.rightDrawerOpen).toBe(true);
	});

	it("opens right-panel Proposal Workspace even with 0 proposals, renders top header button & full Q&A/edit/ratify flow", async () => {
		studioStore.setState({
			userRole: "admin",
			activeSession: {
				session_id: "proposal-full-flow-test",
				domain_namespace: "urn:opendv:domain:risk",
				enterprise_owner: "Risk Architecture",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "e".repeat(64),
				created_at: "2026-11-14T00:00:00Z",
				updated_at: "2026-11-14T00:00:00Z",
			},
			clarifications: [
				{
					question_id: "q-right-1",
					concept_uri: "urn:ontology:entity:margin_call",
					concept_name: "Margin Call",
					question_text:
						"Should Variation Margin Call share the same hub as Initial Margin Call?",
					certainty: "LOW",
					status: "PENDING",
					rationale_options: [
						{
							id: "opt-split",
							label: "Separate Hubs",
							description: "Distinct legal collateral agreements.",
						},
					],
				},
			],
			proposals: [],
		});

		// Switch left drawer to Q&A so ClarificationPanel and top header button are both rendered
		studioStore.getState().setLeftDrawerTab("questions");
		const appClosedHtml = renderToStaticMarkup(
			createElement(ReactFlowProvider, null, createElement(OntologyStudioApp)),
		);
		expect(appClosedHtml).toContain('data-testid="header-proposals-btn"');
		expect(appClosedHtml).toContain(
			'data-testid="open-proposal-workspace-btn"',
		);
		expect(appClosedHtml).toContain('data-testid="right-panel-tabs"');
		expect(appClosedHtml).toContain('data-testid="right-panel-tab-ontology"');
		expect(appClosedHtml).toContain('data-testid="right-panel-tab-proposal"');
		expect(appClosedHtml).toContain("Ontology Viewer (0)");
		expect(appClosedHtml).toContain('data-testid="graph-canvas-container"');
		expect(appClosedHtml).not.toContain(
			'data-testid="right-panel-proposal-view"',
		);
		expect(appClosedHtml).not.toContain('data-testid="proposal-details-panel"');

		// Clicking Proposals opens the right panel in Proposal Details mode even with 0 proposals
		studioStore.getState().setRightDrawerTab("proposal");
		const emptyProposalDrawer = renderToStaticMarkup(
			createElement(VisualEditorDrawer),
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="proposal-details-panel"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="proposal-explanation-box"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="right-start-clarify-btn"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="right-start-construct-btn"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="right-clarifications-section"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="right-submit-answer-btn-q-right-1"',
		);
		expect(emptyProposalDrawer).toContain(
			'data-testid="save-edited-proposal-btn"',
		);

		// Closing resets drawer cleanly when no concept is selected
		studioStore.getState().selectProposal(null);
		expect(studioStore.getState().layout.rightDrawerOpen).toBe(false);
		expect(studioStore.getState().layout.rightDrawerTab).toBe("concept");
		expect(renderToStaticMarkup(createElement(VisualEditorDrawer))).toBe("");

		// Now stage a PENDING proposal and verify zero testid collisions in full OntologyStudioApp
		const pendingProposal = {
			proposal_id: "prop-pending-edit-1",
			session_id: "proposal-full-flow-test",
			status: "PENDING",
			base_version: "1.0.0",
			base_sha256: "e".repeat(64),
			proposer: "deterministic-pipeline",
			created_at: "2026-11-14T03:00:00Z",
			rationale: "Initial draft proposal from parsed PDFs.",
			operations: [
				{
					op_id: "op-1",
					op_type: "add_concept",
					concept_uri: "urn:ontology:entity:margin_call",
					concept_name: "Margin Call",
					elm_archetype: "EVENT_CONCEPT",
					target_hub: "hub_margin_call",
				},
			],
		};
		studioStore.setState({ proposals: [pendingProposal] });
		studioStore.getState().selectProposal("prop-pending-edit-1");

		const fullAppHtml = renderToStaticMarkup(
			createElement(ReactFlowProvider, null, createElement(OntologyStudioApp)),
		);
		// Right panel switches from Ontology Graph Canvas to Proposal Details without duplicate ProposalDetailsView
		expect(fullAppHtml).toContain('data-testid="right-panel-proposal-view"');
		expect(fullAppHtml).not.toContain('data-testid="graph-canvas-container"');
		expect(
			fullAppHtml.match(/data-testid="proposal-details-panel"/g)?.length,
		).toBe(1);
		// Left Q&A panel and Right Proposal Workspace use distinct prefixed testids for Q&A & ratification
		expect(fullAppHtml).toContain('data-testid="submit-answer-btn-q-right-1"');
		expect(fullAppHtml).toContain(
			'data-testid="right-submit-answer-btn-q-right-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="accept-proposal-btn-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="right-accept-proposal-btn-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="right-rebase-proposal-btn-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="right-reject-proposal-btn-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="right-proposal-accept-rationale-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="right-proposal-self-accept-override-prop-pending-edit-1"',
		);
		expect(fullAppHtml).toContain(
			'data-testid="edit-proposal-rationale-input"',
		);
		expect(fullAppHtml).toContain('data-testid="toggle-add-concept-op-btn"');
		expect(fullAppHtml).toContain('data-testid="toggle-add-mapping-op-btn"');
		expect(fullAppHtml).toContain('data-testid="edit-op-toggle-btn-0"');
		expect(fullAppHtml).toContain('data-testid="remove-proposal-op-btn-0"');
		expect(fullAppHtml).toContain('data-testid="save-edited-proposal-btn"');

		// Answer Q&A inside the workflow
		const fetchCalls: Array<{ url: string; method: string; body?: string }> =
			[];
		const mockFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = typeof input === "string" ? input : input.toString();
			const method = init?.method || "GET";
			fetchCalls.push({
				url,
				method,
				...(typeof init?.body === "string" ? { body: init.body } : {}),
			});
			if (url.endsWith("/answers") && method === "POST") {
				return new Response(
					JSON.stringify({ answers_sha256: "f".repeat(64) }),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			if (url.endsWith("/clarifications") && method === "GET") {
				return new Response(JSON.stringify({ clarifications: [] }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			if (url.endsWith("/proposals") && method === "POST") {
				const parsed = JSON.parse(String(init?.body || "{}")) as {
					rationale?: string;
					operations?: Array<Record<string, unknown>>;
				};
				return new Response(
					JSON.stringify({
						proposal_id: "prop-edited-v2",
						session_id: "proposal-full-flow-test",
						base_version: "1.0.0",
						base_sha256: "e".repeat(64),
						status: "PENDING",
						proposer: "human:admin",
						created_at: "2026-11-14T03:05:00Z",
						rationale: parsed.rationale,
						operations: parsed.operations,
					}),
					{ status: 201, headers: { "Content-Type": "application/json" } },
				);
			}
			if (url.endsWith("/rebase") && method === "POST") {
				return new Response(JSON.stringify({ status: "PENDING" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			if (url.endsWith("/reject") && method === "POST") {
				return new Response(JSON.stringify({ status: "REJECTED" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			if (url.endsWith("/accept") && method === "POST") {
				return new Response(JSON.stringify({ status: "ACCEPTED" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}
			if (url.endsWith("/proposals") && method === "GET") {
				return new Response(
					JSON.stringify([{ ...pendingProposal, status: "PENDING" }]),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}
			return new Response(
				JSON.stringify({ detail: `Unexpected route ${method} ${url}` }),
				{
					status: 404,
					headers: { "Content-Type": "application/json" },
				},
			);
		}) as typeof fetch;

		const answeredOk = await studioStore.getState().submitAnswer(
			{
				question_id: "q-right-1",
				selected_option_ids: ["opt-split"],
				custom_answer_text: "Confirmed separate legal obligations.",
				answered_at: "2026-11-14T03:02:00Z",
			},
			undefined,
			mockFetch,
		);
		expect(answeredOk).toBe(true);
		expect(studioStore.getState().clarifications[0]?.status).toBe("ANSWERED");
		const answerCall = fetchCalls.find(
			(c) => c.url.endsWith("/answers") && c.method === "POST",
		);
		expect(answerCall).toBeDefined();

		// Save an edited proposal revision via POST /sessions/{id}/proposals
		const editedOps = [
			{
				...pendingProposal.operations[0],
				concept_name: "Initial Margin Call",
			},
			{
				op_type: "add_external_mapping",
				concept_uri: "urn:ontology:entity:margin_call",
				scheme: "FIBO",
				predicate: "skos:exactMatch",
				target_uri:
					"https://spec.edmcouncil.org/fibo/ontology/DER/DerivativesContracts/Margining/MarginCall",
				mapping_confidence: 0.92,
			},
		];
		const saved = await studioStore.getState().saveEditedProposal(
			"prop-pending-edit-1",
			{
				rationale: "Renamed concept and added FIBO MarginCall alignment.",
				operations: editedOps,
			},
			mockFetch,
		);
		expect(saved).not.toBeNull();
		expect(saved?.proposal_id).toBe("prop-edited-v2");
		expect(studioStore.getState().selectedProposalId).toBe("prop-edited-v2");
		expect(studioStore.getState().proposals[0]?.proposal_id).toBe(
			"prop-edited-v2",
		);
		expect(studioStore.getState().proposals[0]?.operations_count).toBe(2);

		const saveCall = fetchCalls.find(
			(c) => c.url.endsWith("/proposals") && c.method === "POST",
		);
		expect(saveCall).toBeDefined();
		const saveBody = JSON.parse(saveCall?.body || "{}") as {
			base_version?: string;
			base_sha256?: string;
			rationale?: string;
			operations?: Array<Record<string, unknown>>;
		};
		expect(saveBody.base_version).toBe("1.0.0");
		expect(saveBody.base_sha256).toBe("e".repeat(64));
		expect(saveBody.rationale).toBe(
			"Renamed concept and added FIBO MarginCall alignment.",
		);
		expect(saveBody.operations?.length).toBe(2);
		for (const op of saveBody.operations || []) {
			expect(typeof op.op_id).toBe("string");
			expect(String(op.op_id).trim().length).toBeGreaterThan(0);
		}
		expect(saveBody.operations?.[1]?.confidence).toBe(0.92);
		expect(saveBody.operations?.[1]?.mapping_confidence).toBe(0.92);

		// Viewer RBAC cannot edit/save, reject, or rebase proposals
		studioStore.setState({ userRole: "viewer" });
		const viewerSave = await studioStore
			.getState()
			.saveEditedProposal(
				"prop-edited-v2",
				{ rationale: "Blocked", operations: editedOps },
				mockFetch,
			);
		expect(viewerSave).toBeNull();
		expect(
			studioStore.getState().toasts.some((t) => t.code === "FORBIDDEN_ROLE"),
		).toBe(true);
		expect(
			await studioStore
				.getState()
				.rejectProposal("prop-edited-v2", "Blocked", mockFetch),
		).toBe(false);
		expect(
			await studioStore.getState().rebaseProposal("prop-edited-v2", mockFetch),
		).toBe(false);

		// Rebase and reject also dispatch cleanly for Admin
		studioStore.setState({ userRole: "admin" });
		expect(
			await studioStore.getState().rebaseProposal("prop-edited-v2", mockFetch),
		).toBe(true);
		expect(
			await studioStore
				.getState()
				.rejectProposal("prop-edited-v2", "Superseded by v3", mockFetch),
		).toBe(true);
	});

	it("Carbon review hardening: CSS tokens, base pairing, sorting, validation, draft persistence, and unsaved-edit ratification lock", async () => {
		const cssPath = path.resolve(import.meta.dir, "../src/index.css");
		const css = readFileSync(cssPath, "utf-8");
		expect(css).not.toContain("var(--dv-accent)");
		expect(css).not.toContain("var(--dv-warning)");
		expect(css).toContain(".option-btn.option-btn-selected");
		expect(css).toContain(".editor-section");
		expect(css).toContain(".conf-input");

		// MINOR-01: resolveProposalBaseVersionAndSha pairs PENDING baseline or activeSession head consistently
		const acceptedBaseline = resolveProposalBaseVersionAndSha(
			{ active_version: "2.0.0", head_sha256: "b".repeat(64) },
			{
				proposal_id: "prop-old",
				session_id: "s-1",
				status: "ACCEPTED",
				base_version: "1.0.0",
				base_sha256: "a".repeat(64),
				proposer: "human:admin",
			},
		);
		expect(acceptedBaseline.base_version).toBe("2.0.0");
		expect(acceptedBaseline.base_sha256).toBe("b".repeat(64));

		const pendingBaseline = resolveProposalBaseVersionAndSha(
			{ active_version: "2.0.0", head_sha256: "b".repeat(64) },
			{
				proposal_id: "prop-pend",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				base_sha256: "a".repeat(64),
				proposer: "human:admin",
			},
		);
		expect(pendingBaseline.base_version).toBe("1.0.0");
		expect(pendingBaseline.base_sha256).toBe("a".repeat(64));

		// MINOR-02: sortProposalsForStewardship places PENDING first, newest created_at first
		const sorted = sortProposalsForStewardship([
			{
				proposal_id: "prop-acc",
				session_id: "s-1",
				status: "ACCEPTED",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T05:00:00Z",
			},
			{
				proposal_id: "prop-pend-old",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T01:00:00Z",
			},
			{
				proposal_id: "prop-pend-new",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T04:00:00Z",
			},
		]);
		expect(sorted.map((p) => p.proposal_id)).toEqual([
			"prop-pend-new",
			"prop-pend-old",
			"prop-acc",
		]);

		// MAJOR-01 & MAJOR-03: normalizeProposalOperationsForSubmission & validation helpers
		const normalized = normalizeProposalOperationsForSubmission([
			{
				op_type: "add_external_mapping",
				concept_uri: "urn:ontology:entity:margin_call",
				scheme: "FIBO",
				predicate: "skos:exactMatch",
				target_uri: "https://spec.edmcouncil.org/fibo/MarginCall",
				mapping_confidence: 0.91,
			},
		]);
		expect(String(normalized[0]?.op_id || "").length).toBeGreaterThan(0);
		expect(normalized[0]?.confidence).toBe(0.91);

		expect(getEditableOpPrimaryField("refine_definition")).toEqual({
			field: "refined_description",
			label: "Refined Definition",
			aliasKey: "definition",
		});
		expect(getEditableOpPrimaryField("add_synonym").field).toBe("synonym");
		expect(getEditableOpPrimaryField("unknown_op").field).toBe("rationale");

		expect(
			validateProposalDraftOperations([
				{
					op_type: "add_external_mapping",
					concept_uri: "Not A URN",
					target_uri: "https://spec.edmcouncil.org/fibo/MarginCall",
					mapping_confidence: 0.9,
				},
			]),
		).toContain("Concept URN");
		expect(
			validateProposalDraftOperations([
				{
					op_type: "add_concept",
					concept_name: "Margin Call",
					concept_uri: "urn:ontology:entity:margin_call",
					target_hub: "bad_hub_prefix",
				},
			]),
		).toContain("Target Hub");

		// MAJOR-02, MAJOR-04, MINOR-03: draft persistence across tabs and Accept/Rebase lock on dirty edits
		const prop = {
			proposal_id: "prop-dirty-1",
			session_id: "s-dirty",
			status: "PENDING" as const,
			base_version: "1.0.0",
			base_sha256: "c".repeat(64),
			proposer: "human:admin",
			rationale: "Original rationale",
			operations: [
				{
					op_id: "op-1",
					op_type: "add_concept",
					concept_uri: "urn:ontology:entity:margin_call",
					concept_name: "Margin Call",
					elm_archetype: "EVENT_CONCEPT",
					target_hub: "hub_margin_call",
				},
			],
		};
		studioStore.setState({
			activeSession: {
				session_id: "s-dirty",
				domain_namespace: "clearing",
				enterprise_owner: "CRO",
				active_version: "1.0.0",
				head_sha256: "c".repeat(64),
				status: "ACTIVE",
			},
			userRole: "admin",
			proposals: [prop],
			selectedProposalId: "prop-dirty-1",
		});
		studioStore.getState().setProposalDraft("prop-dirty-1", {
			rationale: "Modified rationale in draft",
			operations: prop.operations,
		});
		// Switching back to concept/Ontology Viewer keeps selectedProposalId & proposalDrafts intact
		studioStore.getState().setRightDrawerTab("concept");
		expect(studioStore.getState().selectedProposalId).toBe("prop-dirty-1");
		expect(
			studioStore.getState().proposalDrafts["prop-dirty-1"]?.rationale,
		).toBe("Modified rationale in draft");
		studioStore.getState().setRightDrawerTab("proposal");

		const dirtyHtml = renderToStaticMarkup(
			createElement(ProposalDetailsView, {
				proposal: prop,
				opFilter: "",
				onOpFilterChange: () => {},
				onSelectProposal: () => {},
				onInspectConcept: () => {},
				onClose: () => {},
			}),
		);
		expect(dirtyHtml).toContain("Modified rationale in draft");
		expect(dirtyHtml).toContain(
			'data-testid="right-unsaved-draft-warning-prop-dirty-1"',
		);
		expect(dirtyHtml).toContain(
			'data-testid="right-proposal-low-conf-waiver-prop-dirty-1"',
		);
		expect(dirtyHtml).toContain('data-testid="new-draft-proposal-btn"');
		expect(dirtyHtml).toMatch(
			/data-testid="right-accept-proposal-btn-prop-dirty-1"[^>]*disabled=""|disabled=""[^>]*data-testid="right-accept-proposal-btn-prop-dirty-1"/,
		);
		expect(dirtyHtml).toMatch(
			/data-testid="right-rebase-proposal-btn-prop-dirty-1"[^>]*disabled=""|disabled=""[^>]*data-testid="right-rebase-proposal-btn-prop-dirty-1"/,
		);

		// New draft mode sentinel renders cleanly even when selectedProposalId === NEW_PROPOSAL_DRAFT_KEY
		studioStore.getState().selectProposal(NEW_PROPOSAL_DRAFT_KEY);
		const newDraftHtml = renderToStaticMarkup(
			createElement(ProposalDetailsView, {
				proposal: undefined,
				opFilter: "",
				onOpFilterChange: () => {},
				onSelectProposal: () => {},
				onInspectConcept: () => {},
				onClose: () => {},
			}),
		);
		expect(newDraftHtml).toContain("New Candidate Proposal Draft");
	});
});
