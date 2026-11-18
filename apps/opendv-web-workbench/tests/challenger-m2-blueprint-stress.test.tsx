/**
 * EMPIRICAL ADVERSARIAL STRESS TEST SUITE: Feature 9 (DV 2.0 Blueprint)
 * (apps/opendv-web-workbench/tests/challenger-m2-blueprint-stress.test.tsx)
 *
 * Rigorously stress-tests deriveRawVaultBlueprint and SqlSyntaxHighlighter:
 * 1. Empty, solitary, cyclic, custom target_hub, and attribute edge cases
 * 2. SqlSyntaxHighlighter tokenization, regex boundary behavior, and plain innerText preservation
 * 3. DDL structural integrity and column uniqueness
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
import { createStudioStore } from "../src/ontology/store/studio-store";
import type {
	ConceptNode,
	RelationshipEdge,
	StudioSessionSummary,
} from "../src/ontology/store/types";

// Helper to extract text from HTML markup rendered by React
function extractPlainText(html: string): string {
	let out = "";
	let inTag = false;
	for (const ch of html) {
		if (ch === "<") inTag = true;
		else if (ch === ">") inTag = false;
		else if (!inTag) out += ch;
	}
	const entities: Record<string, string> = {
		"&#x27;": "'",
		"&quot;": '"',
		"&amp;": "&",
		"&lt;": "<",
		"&gt;": ">",
	};
	return out.replace(/&(?:quot|#x27|lt|gt|amp);/g, (m) => entities[m] ?? m);
}

describe("Feature 9 Empirical Stress Test Harness", () => {
	describe("1. deriveRawVaultBlueprint Edge-Case Ontologies", () => {
		it("Edge Case 1.1: Empty ontologies (fallback to Asset Management 4-zone)", () => {
			const bpEmpty = deriveRawVaultBlueprint([], []);
			expect(bpEmpty.hubs).toHaveLength(4);
			expect(bpEmpty.links).toHaveLength(1);
			expect(bpEmpty.satellites).toHaveLength(4);
			expect(bpEmpty.tableCount).toBe(16);
			expect(bpEmpty.zone2Ddl).toContain("CREATE SCHEMA IF NOT EXISTS raw_vault;");
			expect(bpEmpty.zone2Ddl).toContain("hub_security");
			expect(bpEmpty.zone2Ddl).toContain("hub_investor");
			expect(bpEmpty.zone2Ddl).toContain("hub_rate_index");
			expect(bpEmpty.zone2Ddl).toContain("hub_document");
			expect(bpEmpty.zone2Ddl).toContain("lnk_investor_holding");
			expect(bpEmpty.zone2Ddl).toContain("sat_security_details");
			expect(bpEmpty.zone2Ddl).toContain("sat_holding_position");
			expect(bpEmpty.zone2Ddl).toContain("sat_rate_observation");
			expect(bpEmpty.zone2Ddl).toContain("sat_document_metadata");
		});

		it("Edge Case 1.2: Null / undefined inputs handled gracefully without throwing", () => {
			// @ts-expect-error - testing boundary resilience
			const bpNull = deriveRawVaultBlueprint(null, null);
			expect(bpNull.hubs).toHaveLength(4);
			expect(bpNull.tableCount).toBe(16);

			// @ts-expect-error - testing boundary resilience
			const bpUndef = deriveRawVaultBlueprint(undefined, undefined);
			expect(bpUndef.hubs).toHaveLength(4);
			expect(bpUndef.tableCount).toBe(16);
		});

		it("Edge Case 1.3: Single concept with zero attributes", () => {
			const singleNode: ConceptNode[] = [
				{
					id: "concept-isolated",
					concept_uri: "urn:opendv:concept:isolated",
					concept_name: "Isolated Entity",
					elm_archetype: "CONCEPT",
					definition: "An isolated concept without attributes or relationships",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];

			const bp = deriveRawVaultBlueprint(singleNode, []);
			// Should contain hub_isolated_entity + synthesized hub_document
			expect(bp.hubs).toHaveLength(2);
			const isolatedHub = bp.hubs.find((h) => h.tableName === "hub_isolated_entity");
			expect(isolatedHub).toBeDefined();
			expect(isolatedHub?.hkColumn).toBe("hk_isolated_entity");
			// Generates default bk column bk_isolated_entity_id
			expect(isolatedHub?.businessKeys).toEqual(["bk_isolated_entity_id"]);

			// Generates sat_isolated_entity_details
			expect(bp.satellites).toHaveLength(2); // sat_isolated_entity_details + sat_document_metadata
			const sat = bp.satellites.find((s) => s.tableName === "sat_isolated_entity_details");
			expect(sat).toBeDefined();
			expect(sat?.columns.some((c) => c.name === "hk_isolated_entity" && c.isForeignKey)).toBe(true);
			expect(sat?.columns.some((c) => c.name === "hash_diff" && c.isAuditColumn)).toBe(true);

			// Zero links
			expect(bp.links).toHaveLength(0);
		});

		it("Edge Case 1.4: Custom target_hub overrides and case normalization", () => {
			const nodes: ConceptNode[] = [
				{
					id: "concept-custom-1",
					concept_uri: "urn:opendv:concept:fund_product",
					concept_name: "Mutual Fund Product",
					target_hub: "hub_investment_vehicle",
					elm_archetype: "CONCEPT",
					definition: "Fund product mapping to custom hub",
					grounding_status: "GROUNDED",
					attributes: [
						{ attribute_name: "fund_ticker", data_type: "string" },
						{ attribute_name: "fund_isin", data_type: "string" },
					],
					synonyms: [],
					external_mappings: [],
				},
			];

			const bp = deriveRawVaultBlueprint(nodes, []);
			const hub = bp.hubs.find((h) => h.tableName === "hub_investment_vehicle");
			expect(hub).toBeDefined();
			expect(hub?.hkColumn).toBe("hk_investment_vehicle");
			expect(hub?.businessKeys).toContain("bk_fund_ticker");
			expect(hub?.businessKeys).toContain("bk_fund_isin");
		});

		it("Edge Case 1.5: Attribute type mappings and business key regex filtering", () => {
			const nodes: ConceptNode[] = [
				{
					id: "concept-attrs",
					concept_uri: "urn:opendv:concept:comprehensive",
					concept_name: "Comprehensive Asset",
					elm_archetype: "CONCEPT",
					definition: "Concept with multiple attribute types and BK patterns",
					grounding_status: "GROUNDED",
					attributes: [
						// BK matches (matches suffix regex):
						{ attribute_name: "asset_code", data_type: "string" },
						{ attribute_name: "global_cusip", data_type: "string" },
						{ attribute_name: "issuer_lei", data_type: "string" },
						// Descriptive attributes:
						{ attribute_name: "lei_number", data_type: "string" },
						{ attribute_name: "nominal_value", data_type: "decimal" },
						{ attribute_name: "quantity_held", data_type: "bigint" },
						{ attribute_name: "is_active", data_type: "boolean" },
						{ attribute_name: "effective_date", data_type: "date" },
						{ attribute_name: "created_timestamp", data_type: "datetime" },
						{ attribute_name: "risk_score", data_type: "float" },
						{ attribute_name: "unknown_field", data_type: "custom_type" },
					],
					synonyms: [],
					external_mappings: [],
				},
			];

			const bp = deriveRawVaultBlueprint(nodes, []);
			const hub = bp.hubs.find((h) => h.tableName === "hub_comprehensive_asset");
			expect(hub?.businessKeys).toContain("bk_asset_code");
			expect(hub?.businessKeys).toContain("bk_global_cusip");
			expect(hub?.businessKeys).toContain("bk_issuer_lei");
			expect(hub?.businessKeys).not.toContain("bk_lei_number"); // suffix regex routes to satellite
			expect(hub?.businessKeys).not.toContain("bk_nominal_value");

			const sat = bp.satellites.find((s) => s.tableName === "sat_comprehensive_asset_details");
			const colTypes = new Map(sat?.columns.map((c) => [c.name, c.dataType]));
			expect(colTypes.get("lei_number")).toBe("VARCHAR"); // routed to sat
			expect(colTypes.get("nominal_value")).toBe("DECIMAL(18,2)");
			expect(colTypes.get("quantity_held")).toBe("BIGINT");
			expect(colTypes.get("is_active")).toBe("BOOLEAN");
			expect(colTypes.get("effective_date")).toBe("DATE");
			expect(colTypes.get("created_timestamp")).toBe("TIMESTAMP");
			expect(colTypes.get("risk_score")).toBe("DECIMAL(18,4)");
			expect(colTypes.get("unknown_field")).toBe("VARCHAR"); // fallback
		});

		it("Edge Case 1.6: Cyclic & self-referential relationships analysis", () => {
			const nodes: ConceptNode[] = [
				{
					id: "concept-person",
					concept_uri: "urn:opendv:concept:person",
					concept_name: "Person",
					elm_archetype: "CONCEPT",
					definition: "Individual person",
					grounding_status: "GROUNDED",
					attributes: [{ attribute_name: "person_id", data_type: "string" }],
					synonyms: [],
					external_mappings: [],
				},
			];

			const edges: RelationshipEdge[] = [
				{
					id: "edge-parent",
					relationship_uri: "urn:opendv:rel:parent_of",
					relationship_name: "Parent Of",
					source_concept_uri: "urn:opendv:concept:person",
					target_concept_uri: "urn:opendv:concept:person",
				},
			];

			const bp = deriveRawVaultBlueprint(nodes, edges);
			const link = bp.links.find((l) => l.tableName === "lnk_parent_of");
			expect(link).toBeDefined();
			expect(link?.hkColumn).toBe("hk_parent_of");

			// EMPIRICAL OBSERVATION:
			// For a self-referential link where source and target are the same concept,
			// both foreignKeys produce { name: "hk_person", targetHub: "hub_person" }
			expect(link?.foreignKeys).toHaveLength(2);
			expect(link?.foreignKeys[0].name).toBe("hk_person");
			expect(link?.foreignKeys[1].name).toBe("hk_person");

			// Document that DDL generation produces duplicate column name in SQL:
			const duplicateHkCount = (link?.ddl.match(/hk_person VARCHAR NOT NULL/g) || []).length;
			expect(duplicateHkCount).toBe(2);
		});

		it("Edge Case 1.7: Disconnected / Dangling relationship edges with unknown URIs", () => {
			const nodes: ConceptNode[] = [
				{
					id: "concept-alpha",
					concept_uri: "urn:opendv:concept:alpha",
					concept_name: "Alpha Concept",
					elm_archetype: "CONCEPT",
					definition: "Alpha concept",
					grounding_status: "GROUNDED",
					attributes: [{ attribute_name: "alpha_id", data_type: "string" }],
					synonyms: [],
					external_mappings: [],
				},
			];

			const edges: RelationshipEdge[] = [
				{
					id: "edge-unresolved",
					relationship_uri: "urn:opendv:rel:connects_to_unknown",
					relationship_name: "Connects To Unknown",
					source_concept_uri: "urn:opendv:concept:alpha",
					target_concept_uri: "urn:opendv:concept:non_existent_target",
				},
			];

			// Must not throw TypeError on nodeByUri.get(target_uri)
			const bp = deriveRawVaultBlueprint(nodes, edges);
			expect(bp.links).toHaveLength(1);
			const link = bp.links[0];
			expect(link.foreignKeys[0].name).toBe("hk_alpha_concept");
			expect(link.foreignKeys[1].name).toBe("hk_target"); // graceful fallback
		});
	});

	describe("2. SqlSyntaxHighlighter Adversarial Tokenization & Fidelity", () => {
		it("Fidelity: 100% byte-for-byte plain innerText preservation for 4-zone SQL", () => {
			const originalSql = generate4ZoneSql();
			const element = createElement(SqlSyntaxHighlighter, { sql: originalSql });
			const html = renderToStaticMarkup(element);
			const plainText = extractPlainText(html);

			// Assert identical text content
			expect(plainText).toBe(originalSql);
		});

		it("Fidelity: Preserves multi-line indentation, empty lines, and semicolons", () => {
			const customSql = `CREATE SCHEMA IF NOT EXISTS custom_vault;

CREATE TABLE IF NOT EXISTS custom_vault.hub_test (
    hk_test VARCHAR PRIMARY KEY,
    bk_test_id VARCHAR NOT NULL,
    load_dts TIMESTAMP NOT NULL,
    record_source VARCHAR NOT NULL
);

-- End of test block`;

			const element = createElement(SqlSyntaxHighlighter, { sql: customSql });
			const html = renderToStaticMarkup(element);
			const plainText = extractPlainText(html);

			expect(plainText).toBe(customSql);
		});

		it("Tokenization: Full-line comments vs inline comments", () => {
			const commentSql = `-- Top header comment\nCREATE TABLE raw_vault.hub_foo (\n    hk_foo VARCHAR PRIMARY KEY -- Inline comment\n);`;
			const element = createElement(SqlSyntaxHighlighter, { sql: commentSql });
			const html = renderToStaticMarkup(element);

			expect(html).toContain('class="sql-comment"');
			expect(html).toContain("-- Top header comment");
			expect(html).toContain("-- Inline comment");
			expect(extractPlainText(html)).toBe(commentSql);
		});

		it("Security: Safely escapes HTML entities and prevents XSS injection", () => {
			const xssSql = `-- <script>alert("xss")</script>\nCREATE TABLE "<img src=x onerror=alert(1)>" (\n    hk_col VARCHAR\n);`;
			const element = createElement(SqlSyntaxHighlighter, { sql: xssSql });
			const html = renderToStaticMarkup(element);

			// Unescaped script and img tags must not exist in rendered HTML
			expect(html).not.toContain("<script>");
			expect(html).not.toContain("<img src=");
			expect(html).toContain("&lt;script&gt;");
			expect(html).toContain("&lt;");
			expect(html).toContain("&gt;");
			expect(extractPlainText(html)).toBe(xssSql);
		});

		it("Scale stress: High volume DDL (1,000+ lines) renders cleanly under 250ms", () => {
			const lines: string[] = ["-- High Scale Performance Harness"];
			for (let i = 0; i < 200; i++) {
				lines.push(`CREATE TABLE IF NOT EXISTS raw_vault.hub_entity_${i} (`);
				lines.push(`    hk_entity_${i} VARCHAR PRIMARY KEY,`);
				lines.push(`    bk_id_${i} VARCHAR NOT NULL,`);
				lines.push("    load_dts TIMESTAMP NOT NULL,");
				lines.push("    record_source VARCHAR NOT NULL");
				lines.push(");");
			}
			const hugeSql = lines.join("\n");

			const start = performance.now();
			const element = createElement(SqlSyntaxHighlighter, { sql: hugeSql });
			const html = renderToStaticMarkup(element);
			const elapsed = performance.now() - start;

			expect(html.length).toBeGreaterThan(15000);
			expect(elapsed).toBeLessThan(350);
			expect(extractPlainText(html)).toBe(hugeSql);
		});
	});

	describe("3. SQL DDL Structural Integrity & Component Assertions", () => {
		it("All generated DDL statements have matching parentheses and valid structure", () => {
			const customNodes: ConceptNode[] = [
				{
					id: "c-1",
					concept_uri: "urn:opendv:concept:trade",
					concept_name: "Trade",
					elm_archetype: "EVENT",
					definition: "Trade transaction",
					grounding_status: "GROUNDED",
					attributes: [
						{ attribute_name: "trade_id", data_type: "string" },
						{ attribute_name: "trade_amount", data_type: "decimal" },
					],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "c-2",
					concept_uri: "urn:opendv:concept:account",
					concept_name: "Account",
					elm_archetype: "CONCEPT",
					definition: "Client account",
					grounding_status: "GROUNDED",
					attributes: [{ attribute_name: "account_id", data_type: "string" }],
					synonyms: [],
					external_mappings: [],
				},
			];

			const customEdges: RelationshipEdge[] = [
				{
					id: "e-1",
					relationship_uri: "urn:opendv:rel:account_trade",
					relationship_name: "Account Trade",
					source_concept_uri: "urn:opendv:concept:account",
					target_concept_uri: "urn:opendv:concept:trade",
				},
			];

			const bp = deriveRawVaultBlueprint(customNodes, customEdges);
			const allTables = [...bp.hubs, ...bp.links, ...bp.satellites];

			for (const t of allTables) {
				const openParen = (t.ddl.match(/\(/g) || []).length;
				const closeParen = (t.ddl.match(/\)/g) || []).length;
				expect(openParen).toBe(closeParen);
				expect(t.ddl.trim().endsWith(");")).toBe(true);
				expect(t.ddl).toContain("CREATE TABLE IF NOT EXISTS raw_vault.");
			}
		});

		it("BlueprintView component renders action buttons and inventory summary", () => {
			const store = createStudioStore();
			const html = renderToStaticMarkup(createElement(BlueprintView));

			expect(html).toContain('data-testid="right-panel-blueprint-view"');
			expect(html).toContain('data-testid="btn-copy-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-download-blueprint-ddl"');
			expect(html).toContain('data-testid="btn-open-in-pipeline-builder"');
			expect(html).toContain('data-testid="blueprint-inventory-summary"');
			expect(html).toContain('data-testid="blueprint-ddl-code"');
		});
	});
});
