/**
 * Unit & Integration Tests: DiffInspector & Evolution / Delta Sync Callbacks.
 */

import { describe, expect, it } from "bun:test";
import type {
	BlueprintEvolutionDiffDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DiffInspector } from "../src/explorer/builder/DiffInspector";

describe("DiffInspector Visual Diff & Artifact Downloads", () => {
	it("renders summary scorecard with Gate G0-G5 and DuckDB shadow verification badges", () => {
		const html = renderToStaticMarkup(
			createElement(DiffInspector, {
				runId: "run-e2e-001",
				gateScorecard: { passed: true, shadow_passed: true },
			}),
		);

		expect(html).toContain('data-testid="diff-inspector"');
		expect(html).toContain('data-testid="scorecard-container"');
		expect(html).toContain('data-testid="scorecard-gates-badge"');
		expect(html).toContain("✓ Gate G0-G5 Passed");
		expect(html).toContain('data-testid="scorecard-shadow-badge"');
		expect(html).toContain("✓ 5-Zone Verification Passed");
	});

	it("renders added Hubs with TIER_1 and TIER_2 BKCC badges", () => {
		const html = renderToStaticMarkup(
			createElement(DiffInspector, {
				runId: "run-001",
				diff: {
					added_hubs: ["hub_instrument", "hub_portfolio", "hub_party"],
				},
			}),
		);

		expect(html).toContain('data-testid="diff-added-hubs"');
		expect(html).toContain("hub_instrument");
		expect(html).toContain("hub_portfolio");
		expect(html).toContain("hub_party");
		expect(html).toContain("TIER_1");
		expect(html).toContain("TIER_2");
	});

	it("renders added Links and Satellites including companion sat_*_ext", () => {
		const html = renderToStaticMarkup(
			createElement(DiffInspector, {
				diff: {
					added_links: ["lnk_portfolio_instrument"],
					added_satellites: ["sat_instrument_core", "sat_instrument_ext"],
					companion_satellites: ["sat_instrument_ext"],
				},
			}),
		);

		expect(html).toContain('data-testid="diff-added-links"');
		expect(html).toContain("lnk_portfolio_instrument");
		expect(html).toContain('data-testid="diff-added-satellites"');
		expect(html).toContain("sat_instrument_core");
		expect(html).toContain("sat_instrument_ext");
		expect(html).toContain("sat_*_ext");
	});

	it("renders detected schema drift events and applied ontology deltas", () => {
		const customDiff: BlueprintEvolutionDiffDTO = {
			widened_columns: [
				{
					table: "sat_positions",
					column: "nav_per_share",
					from: "DEC(10,2)",
					to: "DEC(18,6)",
				},
			],
			companion_satellites: ["sat_instrument_ext"],
			ontology_deltas_applied: [
				{
					op: "AddAttribute",
					concept: "Instrument",
					attribute: "isin_checksum",
					confidence: 0.95,
				},
			],
		};

		const html = renderToStaticMarkup(
			createElement(DiffInspector, {
				diff: customDiff,
			}),
		);

		expect(html).toContain('data-testid="diff-drift-repairs"');
		expect(html).toContain("WIDENED");
		expect(html).toContain("sat_positions.nav_per_share");
		expect(html).toContain("COMPANION EXT");
		expect(html).toContain('data-testid="diff-ontology-deltas"');
		expect(html).toContain("AddAttribute");
		expect(html).toContain("Instrument");
		expect(html).toContain("isin_checksum");
	});

	it("renders artifact download buttons for contract, blueprint, duckdb, and databricks SQL", () => {
		const html = renderToStaticMarkup(createElement(DiffInspector, {}));

		expect(html).toContain('data-testid="btn-download-contract"');
		expect(html).toContain("final_contract.yaml");
		expect(html).toContain('data-testid="btn-download-blueprint"');
		expect(html).toContain("vault_blueprint.yaml");
		expect(html).toContain('data-testid="btn-download-duckdb-sql"');
		expect(html).toContain("dv_duckdb_schema.sql");
		expect(html).toContain('data-testid="btn-download-databricks-sql"');
		expect(html).toContain("dv_databricks_schema.sql");
	});

	it("renders 1-Click 'Evolve Schema with New Data ➔' and 'Sync Ontology Delta' buttons", () => {
		const html = renderToStaticMarkup(
			createElement(DiffInspector, {
				diff: {
					ontology_deltas_applied: [
						{ op: "AddAttribute", concept: "Instrument" },
					],
				},
			}),
		);

		expect(html).toContain('data-testid="btn-evolve-schema"');
		expect(html).toContain("Evolve Schema with New Data ➔");
		expect(html).toContain('data-testid="btn-sync-ontology-delta"');
		expect(html).toContain("Sync Ontology Delta to Studio Session");
	});

	it("invokes onEvolveSchema callback when 1-click evolve schema is triggered", () => {
		let evolvedBlueprint: VaultBlueprintDTO | null = null;

		const testBp: VaultBlueprintDTO = {
			schema_version: "1.0.0",
			domain_namespace: "asset_management",
			lineage: {
				blueprint_revision: 2,
				domain_namespace: "asset_management",
				ontology_sha256: "test-hash",
			},
			ontology: {},
			contract: { hubs: [{ hub_name: "hub_test" }] },
		};

		const inspector = createElement(DiffInspector, {
			blueprint: testBp,
			onEvolveSchema: (bp) => {
				evolvedBlueprint = bp;
			},
		});

		inspector.props.onEvolveSchema?.(testBp);

		expect(evolvedBlueprint).not.toBeNull();
		expect(evolvedBlueprint?.lineage.blueprint_revision).toBe(2);
		expect(evolvedBlueprint?.domain_namespace).toBe("asset_management");
	});

	it("invokes onSyncOntologyDelta callback when sync delta button is triggered", async () => {
		let syncedSessionId: string | null = null;

		const inspector = createElement(DiffInspector, {
			activeSessionId: "session_asset_mgmt_ibor",
			onSyncOntologyDelta: async (sessionId: string) => {
				syncedSessionId = sessionId;
			},
		});

		await inspector.props.onSyncOntologyDelta?.("session_asset_mgmt_ibor");

		expect(syncedSessionId).toBe("session_asset_mgmt_ibor");
	});
});
