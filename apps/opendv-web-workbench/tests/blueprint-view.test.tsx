/**
 * Unit & Component Tests: Feature 9 — 3rd Right-Panel DV 2.0 Blueprint Tab
 * (apps/opendv-web-workbench/tests/blueprint-view.test.tsx)
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	BlueprintView,
	SqlSyntaxHighlighter,
	deriveRawVaultBlueprint,
	generate4ZoneSql,
} from "../src/ontology/components/BlueprintView";
import {
	createStudioStore,
	studioStore,
} from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
} from "../src/ontology/store/types";

describe("Feature 9: DV 2.0 Blueprint & Schema Preview", () => {
	describe("1. Raw Vault Table Derivation (deriveRawVaultBlueprint)", () => {
		it("falls back to full 4-zone Asset Management reference schema when nodes are empty", () => {
			const blueprint = deriveRawVaultBlueprint([], []);
			expect(blueprint.hubs).toHaveLength(4);
			expect(blueprint.links).toHaveLength(1);
			expect(blueprint.satellites).toHaveLength(4);
			expect(blueprint.tableCount).toBe(16);

			const hubNames = blueprint.hubs.map((h) => h.tableName);
			expect(hubNames).toContain("hub_security");
			expect(hubNames).toContain("hub_investor");
			expect(hubNames).toContain("hub_rate_index");
			expect(hubNames).toContain("hub_document");

			const linkNames = blueprint.links.map((l) => l.tableName);
			expect(linkNames).toContain("lnk_investor_holding");

			const satNames = blueprint.satellites.map((s) => s.tableName);
			expect(satNames).toContain("sat_security_details");
			expect(satNames).toContain("sat_holding_position");
			expect(satNames).toContain("sat_rate_observation");
			expect(satNames).toContain("sat_document_metadata");

			expect(blueprint.fourZoneDdl).toContain(
				"CREATE SCHEMA IF NOT EXISTS staging;",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE SCHEMA IF NOT EXISTS raw_vault;",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE SCHEMA IF NOT EXISTS business_vault;",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE SCHEMA IF NOT EXISTS marts;",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE TABLE IF NOT EXISTS raw_vault.hub_security",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding",
			);
			expect(blueprint.fourZoneDdl).toContain(
				"CREATE TABLE IF NOT EXISTS raw_vault.sat_rate_observation",
			);
		});

		it("dynamically derives Hubs, Links, and Satellites from active ontology concepts and relationships", () => {
			const customNodes: ConceptNode[] = [
				{
					id: "concept-1",
					concept_uri: "urn:opendv:concept:portfolio",
					concept_name: "Portfolio",
					elm_archetype: "CONCEPT",
					definition: "An investment portfolio",
					grounding_status: "GROUNDED",
					attributes: [
						{ attribute_name: "portfolio_id", data_type: "string" },
						{ attribute_name: "portfolio_name", data_type: "string" },
						{ attribute_name: "inception_date", data_type: "date" },
						{ attribute_name: "nav_amount", data_type: "decimal" },
					],
					synonyms: ["Fund Portfolio"],
					external_mappings: [],
				},
				{
					id: "concept-2",
					concept_uri: "urn:opendv:concept:instrument",
					concept_name: "Instrument",
					elm_archetype: "CONCEPT",
					definition: "A financial instrument",
					grounding_status: "GROUNDED",
					attributes: [
						{ attribute_name: "instrument_id", data_type: "string" },
						{ attribute_name: "symbol", data_type: "string" },
						{ attribute_name: "market_price", data_type: "float" },
					],
					synonyms: ["Asset"],
					external_mappings: [],
				},
			];

			const customEdges: RelationshipEdge[] = [
				{
					id: "edge-1",
					relationship_uri: "urn:opendv:rel:portfolio_has_instrument",
					relationship_name: "Portfolio Position",
					target_link: "lnk_portfolio_position",
					source_concept_uri: "urn:opendv:concept:portfolio",
					target_concept_uri: "urn:opendv:concept:instrument",
				},
			];

			const blueprint = deriveRawVaultBlueprint(customNodes, customEdges);

			// Should have hub_portfolio, hub_instrument, plus synthesized hub_document
			const hubNames = blueprint.hubs.map((h) => h.tableName);
			expect(hubNames).toContain("hub_portfolio");
			expect(hubNames).toContain("hub_instrument");
			expect(hubNames).toContain("hub_document");

			const portfolioHub = blueprint.hubs.find(
				(h) => h.tableName === "hub_portfolio",
			);
			expect(portfolioHub).toBeDefined();
			expect(portfolioHub?.hkColumn).toBe("hk_portfolio");
			expect(portfolioHub?.businessKeys).toContain("bk_portfolio_id");

			const instrumentHub = blueprint.hubs.find(
				(h) => h.tableName === "hub_instrument",
			);
			expect(instrumentHub).toBeDefined();
			expect(instrumentHub?.hkColumn).toBe("hk_instrument");
			expect(instrumentHub?.businessKeys).toContain("bk_instrument_id");
			expect(instrumentHub?.businessKeys).toContain("bk_symbol");

			// Satellites
			const satNames = blueprint.satellites.map((s) => s.tableName);
			expect(satNames).toContain("sat_portfolio_details");
			expect(satNames).toContain("sat_instrument_details");
			expect(satNames).toContain("sat_document_metadata");

			// Link
			const linkNames = blueprint.links.map((l) => l.tableName);
			expect(linkNames).toContain("lnk_portfolio_position");
			const posLink = blueprint.links.find(
				(l) => l.tableName === "lnk_portfolio_position",
			);
			expect(posLink?.hkColumn).toBe("hk_portfolio_position");
			expect(posLink?.foreignKeys).toEqual([
				{ name: "hk_portfolio", targetHub: "hub_portfolio" },
				{ name: "hk_instrument", targetHub: "hub_instrument" },
			]);
		});

		it("synthesizes Wave 0 hub_document and sat_document_metadata when absent", () => {
			const nodes: ConceptNode[] = [
				{
					id: "concept-trade",
					concept_uri: "urn:opendv:concept:trade",
					concept_name: "Trade",
					elm_archetype: "EVENT",
					definition: "Executed trade event",
					grounding_status: "GROUNDED",
					attributes: [{ attribute_name: "trade_id", data_type: "string" }],
					synonyms: [],
					external_mappings: [],
				},
			];
			const blueprint = deriveRawVaultBlueprint(nodes, []);
			expect(blueprint.hubs.some((h) => h.tableName === "hub_document")).toBe(
				true,
			);
			expect(
				blueprint.satellites.some(
					(s) => s.tableName === "sat_document_metadata",
				),
			).toBe(true);
		});
	});

	describe("2. SqlSyntaxHighlighter Component & Fidelity", () => {
		it("renders syntax highlighted tokens with correct color styling", () => {
			const sampleSql =
				"CREATE TABLE IF NOT EXISTS raw_vault.hub_security (\n    hk_security VARCHAR PRIMARY KEY,\n    load_dts TIMESTAMP NOT NULL\n);";
			const html = renderToStaticMarkup(
				createElement(SqlSyntaxHighlighter, { sql: sampleSql }),
			);

			// Assert token classes and colors are present
			expect(html).toContain('class="sql-keyword"');
			expect(html).toContain("color:#93C5FD");
			expect(html).toContain('class="sql-schema"');
			expect(html).toContain("color:#F472B6");
			expect(html).toContain('class="sql-key"');
			expect(html).toContain("color:#A78BFA");
			expect(html).toContain('class="sql-type"');
			expect(html).toContain("color:#6EE7B7");
		});

		it("renders comments with italic styling", () => {
			const sampleSql =
				"-- Open-DV Comment\nCREATE SCHEMA IF NOT EXISTS raw_vault;";
			const html = renderToStaticMarkup(
				createElement(SqlSyntaxHighlighter, { sql: sampleSql }),
			);
			expect(html).toContain('class="sql-comment"');
			expect(html).toContain("color:#64748B");
			expect(html).toContain("font-style:italic");
		});

		it("preserves exact plain SQL text extraction fidelity", () => {
			const sampleSql = generate4ZoneSql();
			const element = createElement(SqlSyntaxHighlighter, { sql: sampleSql });
			const html = renderToStaticMarkup(element);

			// Strip all HTML tags to verify innerText equivalence
			let plainText = "";
			let inTag = false;
			for (const ch of html) {
				if (ch === "<") inTag = true;
				else if (ch === ">") inTag = false;
				else if (!inTag) plainText += ch;
			}
			// Verify key DDL constructs are present verbatim
			expect(plainText).toContain("CREATE SCHEMA IF NOT EXISTS raw_vault;");
			expect(plainText).toContain("hk_security VARCHAR PRIMARY KEY,");
			expect(plainText).toContain(
				"CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding",
			);
			expect(plainText).toContain("sat_rate_observation");
		});
	});

	describe("3. BlueprintView Component DOM & Inventory Summary", () => {
		it("renders container, pills, badges, action buttons, and DDL code block", () => {
			studioStore.setState({ nodes: [], edges: [], activeSession: null });
			const html = renderToStaticMarkup(createElement(BlueprintView));

			expect(html).toContain('data-testid="right-panel-blueprint-view"');
			expect(html).toContain('data-testid="blueprint-inventory-summary"');
			expect(html).toContain("Target Raw Vault Tables");

			// Pill badges
			expect(html).toContain('data-testid="pill-hub-hub_security"');
			expect(html).toContain('data-testid="pill-hub-hub_investor"');
			expect(html).toContain('data-testid="pill-hub-hub_rate_index"');
			expect(html).toContain('data-testid="pill-hub-hub_document"');
			expect(html).toContain('data-testid="pill-link-lnk_investor_holding"');
			expect(html).toContain('data-testid="pill-sat-sat_security_details"');
			expect(html).toContain('data-testid="pill-sat-sat_holding_position"');
			expect(html).toContain('data-testid="pill-sat-sat_rate_observation"');
			expect(html).toContain('data-testid="pill-sat-sat_document_metadata"');

			// Action buttons
			expect(html).toContain('data-testid="btn-copy-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-download-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-open-in-pipeline-builder"');

			// DDL code block
			expect(html).toContain('data-testid="blueprint-ddl-code"');
		});
	});

	describe("4. Studio Store Blueprint Tab Integration & Drawer Persistence", () => {
		it("setRightDrawerTab('blueprint') opens drawer and activates blueprint tab", () => {
			const store = createStudioStore();
			expect(store.getState().layout.rightDrawerTab).toBe("concept");

			store.getState().setRightDrawerTab("blueprint");
			expect(store.getState().layout.rightDrawerTab).toBe("blueprint");
			expect(store.getState().layout.rightDrawerOpen).toBe(true);
		});

		it("selectNode(null) preserves drawer open when rightDrawerTab is 'blueprint'", () => {
			const store = createStudioStore();
			store.getState().setRightDrawerTab("blueprint");
			expect(store.getState().layout.rightDrawerOpen).toBe(true);

			// Deselect node
			store.getState().selectNode(null);
			expect(store.getState().layout.rightDrawerOpen).toBe(true);
			expect(store.getState().layout.rightDrawerTab).toBe("blueprint");
		});

		it("selectProposal(null) preserves rightDrawerTab 'blueprint' when active", () => {
			const store = createStudioStore();
			store.getState().setRightDrawerTab("blueprint");

			// Deselect proposal
			store.getState().selectProposal(null);
			expect(store.getState().layout.rightDrawerTab).toBe("blueprint");
			expect(store.getState().layout.rightDrawerOpen).toBe(true);
		});
	});
});
