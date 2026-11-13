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
	SAVE_FEEDBACK_RESET_MS,
	STUDIO_MAX_UPLOAD_BYTES,
} from "../src/ontology/constants";
import {
	createStudioStore,
	isSupportedSourceFile,
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
});
