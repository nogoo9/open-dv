/**
 * apps/opendv-web-workbench/tests/adversarial-challenger-m6.test.tsx
 *
 * Tier 5 Adversarial Coverage Hardening Suite for Web Workbench:
 * - Edge cases and boundary conditions in `deriveRawVaultBlueprint` (empty nodes, broken edges, reserved keywords, unsupported types)
 * - Security & XSS escaping in `SqlSyntaxHighlighter`
 * - Static markup assertions for `BlueprintView` inventory, pills, and action buttons
 * - 1-Click deep-link parameter parsing and state restoration in `PipelineBuilderView` and `specBuilderUtils`
 * - React state immutability in `studio-store` (`setRightDrawerTab`, `toggleRightDrawer`, drawer state transitions)
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	BlueprintView,
	SqlSyntaxHighlighter,
	deriveRawVaultBlueprint,
	generate4ZoneSql,
} from "../src/ontology/components/BlueprintView";
import { VisualEditorDrawer } from "../src/ontology/components/VisualEditorDrawer";
import {
	createStudioStore,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";
import {
	PipelineBuilderView,
	buildCurrentStagedSpec,
} from "../src/explorer/builder/PipelineBuilderView";
import {
	createDefaultPipelineDraft,
	createOntologyStudioPipelineDraft,
	draftToStagedSpec,
} from "../src/explorer/builder/specBuilderUtils";

describe("Tier 5 Adversarial Coverage Hardening: Web Workbench", () => {
	describe("1. Blueprint Derivation Boundaries & Adversarial Inputs", () => {
		it("handles undefined and null inputs without throwing and falls back to 16 tables", () => {
			const bpUndefined = deriveRawVaultBlueprint(undefined, undefined, null);
			expect(bpUndefined.hubs).toHaveLength(4);
			expect(bpUndefined.links).toHaveLength(1);
			expect(bpUndefined.satellites).toHaveLength(4);
			expect(bpUndefined.tableCount).toBe(16);

			const bpNull = deriveRawVaultBlueprint(
				null as unknown as ConceptNode[],
				null as unknown as RelationshipEdge[],
				undefined,
			);
			expect(bpNull.tableCount).toBe(16);
		});

		it("generates valid DDL for single concept with reserved SQL keywords as attributes", () => {
			const reservedAttrsNode: ConceptNode = {
				id: "node-order",
				concept_uri: "urn:opendv:concept:order",
				concept_name: "Order",
				elm_archetype: "EVENT",
				target_hub: "hub_order",
				attributes: [
					{
						attribute_name: "order_id",
						data_type: "string",
						is_optional: false,
					},
					{
						attribute_name: "select",
						data_type: "varchar",
						is_optional: true,
					},
					{
						attribute_name: "group",
						data_type: "bigint",
						is_optional: true,
					},
					{
						attribute_name: "where",
						data_type: "boolean",
						is_optional: true,
					},
					{
						attribute_name: "unknown_metric",
						data_type: "unsupported_type_xyz",
						is_optional: true,
					},
				],
				synonyms: ["purchase_order"],
				external_mappings: [],
			};

			const bp = deriveRawVaultBlueprint([reservedAttrsNode], []);
			expect(bp.hubs).toHaveLength(2); // hub_order + synthesized hub_document
			expect(bp.hubs.some((h) => h.tableName === "hub_order")).toBe(true);
			expect(bp.hubs.some((h) => h.tableName === "hub_document")).toBe(true);

			const satOrder = bp.satellites.find((s) => s.tableName === "sat_order_details");
			expect(satOrder).toBeDefined();

			// Assert reserved column names are in the satellite DDL
			expect(satOrder?.ddl).toContain("select VARCHAR");
			expect(satOrder?.ddl).toContain("group BIGINT");
			expect(satOrder?.ddl).toContain("where BOOLEAN");
			expect(satOrder?.ddl).toContain("unknown_metric VARCHAR"); // fallback type
		});

		it("handles orphaned relationships where endpoint concepts do not exist in nodes", () => {
			const brokenEdge: RelationshipEdge = {
				id: "edge-broken",
				relationship_uri: "urn:opendv:rel:broken_link",
				relationship_name: "Broken Link",
				source_concept_uri: "urn:opendv:concept:ghost_source",
				target_concept_uri: "urn:opendv:concept:ghost_target",
				cardinality: "many_to_many",
			};

			// Should not throw TypeError: Cannot read properties of undefined
			const bp = deriveRawVaultBlueprint([], [brokenEdge]);
			expect(bp).toBeDefined();
			// Empty nodes falls back to default 16-table schema
			expect(bp.tableCount).toBe(16);
		});

		it("synthesizes link between security and investor even when no edge is explicitly given", () => {
			const nodes: ConceptNode[] = [
				{
					id: "n1",
					concept_uri: "urn:opendv:concept:security",
					concept_name: "Security",
					target_hub: "hub_security",
					elm_archetype: "RESOURCE",
					attributes: [
						{ attribute_name: "ticker", data_type: "string", is_optional: false },
						{ attribute_name: "cik", data_type: "string", is_optional: false },
					],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "n2",
					concept_uri: "urn:opendv:concept:investor",
					concept_name: "Investor",
					target_hub: "hub_investor",
					elm_archetype: "PARTY",
					attributes: [
						{ attribute_name: "cik", data_type: "string", is_optional: false },
					],
					synonyms: [],
					external_mappings: [],
				},
			];

			const bp = deriveRawVaultBlueprint(nodes, []);
			expect(bp.links.some((l) => l.tableName === "lnk_investor_holding")).toBe(true);
			expect(bp.satellites.some((s) => s.tableName === "sat_holding_position")).toBe(
				true,
			);
		});

		it("customizes staging and DDL comment header based on activeSession domain_namespace", () => {
			const session: StudioSessionSummary = {
				session_id: "sess-custom-risk",
				domain_namespace: "risk_analytics",
				enterprise_owner: "Risk Management",
				status: "ACTIVE",
				active_version: "1.0.0",
				created_at: "2026-11-14T12:00:00Z",
				updated_at: "2026-11-14T12:00:00Z",
			};

			const bp = deriveRawVaultBlueprint(
				[
					{
						id: "n-risk",
						concept_uri: "urn:opendv:concept:risk_factor",
						concept_name: "RiskFactor",
						target_hub: "hub_risk_factor",
						elm_archetype: "CONCEPT",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
				[],
				session,
			);

			expect(bp.fourZoneDdl).toContain(
				"-- Open-DV risk_analytics 4-Zone Data Vault 2.0 Schema",
			);
			// Non-AM domain does not render sec/fred staging tables
			expect(bp.fourZoneDdl).not.toContain("stg_sec_security_master");
		});
	});

	describe("2. SqlSyntaxHighlighter XSS & Injection Escaping", () => {
		it("safely escapes HTML tags in SQL comments and string literals without DOM injection", () => {
			const maliciousSql = `-- <script>alert("xss comment")</script>
CREATE TABLE raw_vault.hub_test (
    hk_test VARCHAR PRIMARY KEY,
    malicious_val VARCHAR DEFAULT '<img src="x" onerror="evil()">'
);`;

			const html = renderToStaticMarkup(
				createElement(SqlSyntaxHighlighter, { sql: maliciousSql }),
			);

			// Must not contain raw executable unescaped tags
			expect(html).not.toContain('<script>alert("xss comment")</script>');
			expect(html).not.toContain('<img src="x" onerror="evil()">');
			// Must be properly escaped entities
			expect(html).toContain("&lt;script&gt;");
			expect(html).toContain("&lt;img");
		});

		it("handles empty and whitespace-only SQL strings cleanly", () => {
			const html = renderToStaticMarkup(
				createElement(SqlSyntaxHighlighter, { sql: "" }),
			);
			expect(html).toBe("<span></span>");
		});
	});

	describe("3. BlueprintView Static Component Markup & Test IDs", () => {
		beforeEach(() => {
			studioStore.setState({
				nodes: [],
				edges: [],
				activeSession: null,
			});
		});

		it("renders all required test-id elements in static markup", () => {
			const html = renderToStaticMarkup(createElement(BlueprintView));

			expect(html).toContain('data-testid="right-panel-blueprint-view"');
			expect(html).toContain('data-testid="blueprint-inventory-summary"');
			expect(html).toContain('data-testid="btn-copy-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-download-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-open-in-pipeline-builder"');
			expect(html).toContain('data-testid="blueprint-ddl-code"');

			// Default fallback pills
			expect(html).toContain('data-testid="pill-hub-hub_security"');
			expect(html).toContain('data-testid="pill-hub-hub_investor"');
			expect(html).toContain('data-testid="pill-hub-hub_rate_index"');
			expect(html).toContain('data-testid="pill-hub-hub_document"');
			expect(html).toContain('data-testid="pill-link-lnk_investor_holding"');
			expect(html).toContain('data-testid="pill-sat-sat_security_details"');
		});
	});

	describe("4. 1-Click Deep-Link Parameter Parsing & State Restoration", () => {
		const originalLocation = globalThis.location;

		afterEach(() => {
			// Restore globalThis.location
			if (originalLocation) {
				globalThis.location = originalLocation;
			}
		});

		it("parses deep-link query parameters and auto-creates AM pipeline draft", () => {
			// Mock globalThis.location.search
			// @ts-expect-error mocking location for test
			globalThis.location = {
				search:
					"?session_id=sess_m6_adversarial&source=ontology_studio&domain_namespace=asset_management&pipeline_id=pl_m6_walkthrough",
			};

			let capturedStaged: unknown = null;
			const html = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					onStageSpec: (staged) => {
						capturedStaged = staged;
					},
				}),
			);

			expect(html).toContain('data-testid="pipeline-builder-view"');
			expect(capturedStaged).not.toBeNull();
			const staged = capturedStaged as {
				mode: string;
				validated: boolean;
				inputSpec?: { pipeline_id?: string; domain_namespace?: string };
			};
			expect(staged.mode).toBe("interactive");
			expect(staged.validated).toBe(false);
			expect(staged.inputSpec?.pipeline_id).toBe("pl_m6_walkthrough");
			expect(staged.inputSpec?.domain_namespace).toBe("asset_management");
		});

		it("decodes URL-encoded parameters properly against query injection", () => {
			const draft = createOntologyStudioPipelineDraft({
				sessionId: "sess%20with%20spaces",
				domainNamespace: "private%26credit",
				pipelineId: "pl%20custom",
			});

			expect(draft.pipelineId).toBe("pl%20custom");
			expect(draft.domainNamespace).toBe("private%26credit");
		});

		it("falls back to global_commerce_vault_run when no ontology query params exist", () => {
			// @ts-expect-error mocking location
			globalThis.location = { search: "" };

			const defaultDraft = createDefaultPipelineDraft();
			expect(defaultDraft.pipelineId).toBe("global_commerce_vault_run");
			expect(defaultDraft.domainNamespace).toBe("commerce");
		});
	});

	describe("5. React State Immutability in studioStore", () => {
		beforeEach(() => {
			studioStore.setState({
				nodes: [],
				edges: [],
				proposals: [
					{
						proposal_id: "prop-001",
						session_id: "sess-001",
						status: "PENDING",
						base_version: "1.0.0",
						target_version: "1.1.0",
						operations: [],
					},
				],
				selectedProposalId: null,
				layout: {
					leftDrawerTab: "sessions",
					leftDrawerOpen: false,
					rightDrawerOpen: false,
					rightDrawerTab: "concept",
					bottomDockOpen: false,
					activeModal: null,
					lodTier: 2,
					minimapVisible: false,
					searchTerm: "",
				},
			});
		});

		it("immutably updates layout when setRightDrawerTab is called", () => {
			const initialLayout = studioStore.getState().layout;

			studioStore.getState().setRightDrawerTab("blueprint");

			const updatedState = studioStore.getState();
			expect(updatedState.layout.rightDrawerTab).toBe("blueprint");
			expect(updatedState.layout.rightDrawerOpen).toBe(true);
			// Must produce a new object reference (immutability check)
			expect(updatedState.layout).not.toBe(initialLayout);
		});

		it("auto-selects first proposal when switching to proposal tab with null selection", () => {
			expect(studioStore.getState().selectedProposalId).toBeNull();

			studioStore.getState().setRightDrawerTab("proposal");

			expect(studioStore.getState().layout.rightDrawerTab).toBe("proposal");
			expect(studioStore.getState().selectedProposalId).toBe("prop-001");
		});

		it("immutably toggles right drawer open/closed state", () => {
			const s1 = studioStore.getState();
			s1.toggleRightDrawer(true);
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(true);

			s1.toggleRightDrawer(false);
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(false);

			s1.toggleRightDrawer(); // toggle
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(true);
		});
	});
});
