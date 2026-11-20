/**
 * M6 Gen2 Failing-First TDD Tests — DV Schema Explorer (Views 1-4) & Step 6 Compare Link
 * (`apps/opendv-web-workbench/tests/m6-dv-explorer-fixes.test.tsx`)
 *
 * Tests against real `run-80ff425c8100`-shaped API payloads (raw contract triage rows,
 * CanonicalIR hub/link/pit/esat shapes, /report contract_content + /artifacts dummy stub,
 * empty Databricks Z1/Z3 tabs, and Step 6 selectedBaselineRunId compare forwarding).
 */

import { describe, expect, it } from "bun:test";
import type { DvSchemaProjectionDTO } from "@opendv/protocol-ts";
import { type ReactNode, createElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DvContractIrTab } from "../src/explorer/dv-schema/DvContractIrTab";
import { DvSchemaExplorerView } from "../src/explorer/dv-schema/DvSchemaExplorerView";
import { DvTableLineageInspector } from "../src/explorer/dv-schema/DvTableLineageInspector";
import { DvTriageAndWaveDiffTab } from "../src/explorer/dv-schema/DvTriageAndWaveDiffTab";
import {
	buildBipartiteFlowElements,
	fetchDvRunReportAndArtifacts,
} from "../src/explorer/dv-schema/dv-schema-layout";
import {
	buildDvEntities,
	filterDvEntities,
	formatTriageCsv,
} from "../src/explorer/dv-schema/dv-schema-utils";
import { Step6PipelineBuildAndRun } from "../src/ontology/wizard/Step6PipelineBuildAndRun";
import { REAL_RUN_80FF_PROJECTION } from "./fixtures/run-80ff-dv-schema-fixture";

function findElementByTestId(
	node: ReactNode,
	testId: string,
): Record<string, unknown> | null {
	if (!node || typeof node !== "object") return null;
	if (Array.isArray(node)) {
		for (const item of node) {
			const hit = findElementByTestId(item, testId);
			if (hit) return hit;
		}
		return null;
	}
	if (!isValidElement(node)) return null;
	const props = node.props as Record<string, unknown>;
	if (props["data-testid"] === testId) return props;
	return findElementByTestId(props.children as ReactNode, testId);
}

describe("M6 Gen2 DV Schema Explorer & Step 6 Regression Fixes", () => {
	describe("BUG-1: column_triage_ledger Raw Contract + Normalized Schema & Z1 Staging Nodes", () => {
		it("renders 52/52 (100%) triage, populated table cells, and non-empty CSV for raw contract column_triage_ledger", () => {
			const html = renderToStaticMarkup(
				createElement(DvTriageAndWaveDiffTab, {
					projection: REAL_RUN_80FF_PROJECTION,
					runId: "run-80ff425c8100",
					compareRunId: "run-4804789955aa",
				}),
			);

			expect(html).toContain("52/52 (100%)");
			expect(html).toContain("ds01_sec_treasury.positions");
			expect(html).toContain("position_id");
			expect(html).toContain("BUSINESS_KEY");
			expect(html).toContain("hub_position");
			expect(html).toContain("sat_positions_ext_v2");
			expect(html).not.toContain(">.<");

			const csv = formatTriageCsv(
				REAL_RUN_80FF_PROJECTION.column_triage_ledger,
			);
			const lines = csv.split("\n");
			expect(lines.length).toBe(53);
			expect(csv).not.toContain(",,,,,,");
			expect(csv).toContain(
				"ds01_sec_treasury.positions,position_id,BUSINESS_KEY",
			);
			expect(csv).toContain("hub_position");
		});

		it("continues supporting normalized column_triage_ledger entries alongside raw contract entries", () => {
			const mixedProjection: DvSchemaProjectionDTO = {
				...REAL_RUN_80FF_PROJECTION,
				column_triage_ledger: [
					{
						bronze_table: "ds01_sec_treasury.issuers",
						column_name: "cik",
						elm_classification: "BUSINESS_KEY",
						source_sql_type: "TEXT",
						target_dv_entity: "hub_issuer",
					},
					{
						wave: "W2",
						source_table: "daily_positions",
						source_column: "position_id",
						triage_role: "BUSINESS_KEY",
						graph_a_uri: "urn:ontology:entity:position",
						staging_expr: "TRIM(position_id)",
						target_entity: "hub_position",
					},
				],
			};
			const html = renderToStaticMarkup(
				createElement(DvTriageAndWaveDiffTab, {
					projection: mixedProjection,
					runId: "run-80ff425c8100",
				}),
			);
			expect(html).toContain("2/2 (100%)");
			expect(html).toContain("ds01_sec_treasury.issuers");
			expect(html).toContain("daily_positions");
		});

		it("derives Z1 Staging (stg_*) nodes from raw contract bronze_table, source_mappings, and staging_views", () => {
			const entities = buildDvEntities(REAL_RUN_80FF_PROJECTION);
			const z1Nodes = filterDvEntities(entities, "z1_staging", "");
			expect(z1Nodes.length).toBeGreaterThan(0);
			const stgNames = z1Nodes.map((n) => n.name);
			expect(stgNames).toContain("stg_ds01_sec_treasury_positions");
			expect(stgNames).toContain("stg_ds01_sec_treasury_issuers");

			// Also supports explicit staging_views when triage ledger is empty
			const stagingViewsProj = {
				...REAL_RUN_80FF_PROJECTION,
				hubs: [],
				column_triage_ledger: [],
				staging_views: ["stg_positions_from_views", "stg_issuers_from_views"],
			} as DvSchemaProjectionDTO & { staging_views: string[] };
			const fromViews = filterDvEntities(
				buildDvEntities(stagingViewsProj),
				"z1_staging",
				"",
			);
			expect(fromViews.map((n) => n.name)).toEqual([
				"stg_positions_from_views",
				"stg_issuers_from_views",
			]);

			// Also falls back to four_zone_sql Z1 CREATE VIEW stg_* when triage & source_mappings are empty
			const fromSql = filterDvEntities(
				buildDvEntities(
					{
						...REAL_RUN_80FF_PROJECTION,
						hubs: [],
						column_triage_ledger: [],
					},
					{
						Z1: "CREATE OR REPLACE VIEW staging.stg_hub_position AS SELECT 1;",
					},
				),
				"z1_staging",
				"",
			);
			expect(fromSql.map((n) => n.name)).toEqual(["stg_hub_position"]);
		});
	});

	describe("BUG-3: View 4 Real Contract & Canonical IR (Zero global_commerce_vault Stubs) + Databricks Empty Z1/Z3", () => {
		it("prefers real report.contract_content over static global_commerce_vault /artifacts stub and suppresses dummy IR stub", async () => {
			const realYaml =
				"version: '5.0'\nmodel_name: sec_treasury_vault\nhubs:\n  - name: hub_issuer\nsatellites:\n  - name: sat_positions_ext_v2\n";
			const mockFetch = (async (input: RequestInfo | URL) => {
				const url = String(input);
				if (url.endsWith("/report")) {
					return new Response(
						JSON.stringify({
							run_id: "run-80ff425c8100",
							contract_content: realYaml,
							four_zone_sql: {
								duckdb: {
									zone_0_staging: "CREATE SCHEMA IF NOT EXISTS bronze;",
									zone_1_raw_vault:
										"CREATE OR REPLACE VIEW staging.stg_hub_position AS SELECT 1;",
									zone_2_business_vault:
										"INSERT INTO raw_vault.hub_position SELECT 1;",
									zone_3_info_mart:
										"CREATE TABLE IF NOT EXISTS business_vault.pit_position (hk CHAR(64));",
									all_zones:
										"CREATE VIEW information_mart.vw_position_current AS SELECT 1;",
								},
								databricks: {
									zone_0_staging:
										"CREATE TABLE IF NOT EXISTS opendv_catalog.raw_vault.hub_issuer (hk STRING) USING DELTA;",
									zone_1_raw_vault: "",
									zone_2_business_vault:
										"MERGE INTO opendv_catalog.raw_vault.hub_issuer USING src ON 1=1;",
									zone_3_info_mart: "",
									all_zones: "-- FULL DATABRICKS BUNDLE",
								},
							},
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response(
					JSON.stringify([
						{
							artifact_id: "final_contract",
							filename: "final_contract.yaml",
							content:
								"version: '5.0'\nmodel_name: global_commerce_vault\nhubs:\n  - hub_customer: { business_key: customer_tax_id }",
						},
						{
							artifact_id: "final_ir",
							filename: "final_canonical_ir.json",
							content: JSON.stringify(
								{ hubs: ["hub_party", "hub_order"] },
								null,
								2,
							),
						},
					]),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}) as typeof fetch;

			const hydrated = await fetchDvRunReportAndArtifacts(
				"http://127.0.0.1:18420",
				"run-80ff425c8100",
				mockFetch,
			);

			expect(hydrated.contractYaml).toContain("sec_treasury_vault");
			expect(hydrated.contractYaml).toContain("sat_positions_ext_v2");
			expect(hydrated.contractYaml).not.toContain("global_commerce_vault");
			// Dummy ["hub_party", "hub_order"] stub must NOT survive as irJson
			expect(hydrated.irJson ?? "").not.toContain("hub_party");

			// Rendering View 4 reconstructs real IR from projection when report omits canonical_ir
			const view4Html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-80ff425c8100",
					initialTab: "sql",
					initialProjection: REAL_RUN_80FF_PROJECTION,
					zoneSqlDuckDb: hydrated.zoneSqlDuckDb,
					zoneSqlDatabricks: hydrated.zoneSqlDatabricks,
					finalContractYaml: hydrated.contractYaml,
					finalCanonicalIrJson: hydrated.irJson,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			expect(view4Html).toContain("sec_treasury_vault");
			expect(view4Html).not.toContain("global_commerce_vault");

			const irHtml = renderToStaticMarkup(
				createElement(DvContractIrTab, {
					projection: REAL_RUN_80FF_PROJECTION,
					finalContractYaml: hydrated.contractYaml,
					finalCanonicalIrJson: hydrated.irJson,
					initialArtifactTab: "ir",
				}),
			);
			expect(irHtml).toContain("hub_issuer");
			expect(irHtml).toContain("sat_positions_ext_v2");
			expect(irHtml).toContain("pit_position");
			expect(irHtml).not.toContain("hub_party");
		});

		it("shows a clear dialect note instead of a blank <pre> when Databricks Delta Z1 or Z3 is empty", () => {
			const htmlZ1 = renderToStaticMarkup(
				createElement(DvContractIrTab, {
					projection: REAL_RUN_80FF_PROJECTION,
					zoneSqlDatabricks: {
						Z0: "CREATE TABLE cat.raw_vault.hub_issuer (hk STRING) USING DELTA;",
						Z2: "MERGE INTO cat.raw_vault.hub_issuer USING src ON 1=1;",
						Z4: "-- FULL DATABRICKS BUNDLE",
					},
					initialDialect: "databricks",
					initialZone: "Z1",
				} as React.ComponentProps<typeof DvContractIrTab> & {
					initialDialect?: "duckdb" | "databricks";
					initialZone?: "Z0" | "Z1" | "Z2" | "Z3" | "Z4";
				}),
			);
			expect(htmlZ1).toContain("Databricks Delta");
			expect(htmlZ1).toContain("not emit");
			expect(htmlZ1).not.toMatch(/<pre[^>]*>\s*<\/pre>/);
		});
	});

	describe("BUG-4: Hub bkcc_literal/source_mappings, business_vault Schema Routing, PIT Edge, and Step 6 Compare Link", () => {
		it("renders Hub source_mappings (bronze_table/bk_source_columns) and BKCC badge from bkcc_literal + key_authority_tier", () => {
			const entities = buildDvEntities(REAL_RUN_80FF_PROJECTION);
			const hubIssuer = entities.find((e) => e.name === "hub_issuer");
			expect(hubIssuer).toBeDefined();

			const html = renderToStaticMarkup(
				createElement(DvTableLineageInspector, {
					entity: hubIssuer ?? null,
					scorecard: REAL_RUN_80FF_PROJECTION.scorecard,
					onOpenDdlDialog: () => {},
				}),
			);
			expect(html).toContain("ds01_sec_treasury.issuers.cik");
			expect(html).not.toContain("src.bk");
			expect(html).toContain("BKCC:");
			expect(html).toContain("TIER_3_LOCAL_SCOPED");
			expect(html).toContain("DS01_SEC_TREASURY_ISSUERS");
		});

		it("routes esat_sal_account, sal_account, hal_*, pit_*, brg_* to business_vault instead of raw_vault", () => {
			const extraProj: DvSchemaProjectionDTO = {
				...REAL_RUN_80FF_PROJECTION,
				satellites: [
					...REAL_RUN_80FF_PROJECTION.satellites,
					{
						name: "hal_party",
						parent_entity: "hub_issuer",
						satellite_type: "HIERARCHY",
						hash_diff_column: "hash_diff",
						hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
						payload_columns: [],
						soft_deprecated_columns: [],
					},
				],
				pit_tables: [
					...REAL_RUN_80FF_PROJECTION.pit_tables,
					{
						name: "brg_position_issuer",
						target_hub: "hub_position",
						tracked_satellites: ["sat_positions"],
					},
				],
			};
			const entities = buildDvEntities(extraProj);
			const byName = new Map(entities.map((e) => [e.name, e]));
			expect(byName.get("esat_sal_account")?.schemaName).toBe("business_vault");
			expect(byName.get("sal_account")?.schemaName).toBe("business_vault");
			expect(byName.get("hal_party")?.schemaName).toBe("business_vault");
			expect(byName.get("pit_position")?.schemaName).toBe("business_vault");
			expect(byName.get("brg_position_issuer")?.schemaName).toBe(
				"business_vault",
			);
			expect(byName.get("sat_positions_ext_v2")?.schemaName).toBe("raw_vault");

			const rowsViewHtml = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-80ff425c8100",
					initialTab: "rows",
					initialProjection: REAL_RUN_80FF_PROJECTION,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			expect(rowsViewHtml).toContain("business_vault.esat_sal_account");
			expect(rowsViewHtml).not.toContain("raw_vault.esat_sal_account");
		});

		it("resolves pit_tables target_hub || parent_entity for hk_* FK column and graph edge, plus Link target_hub edges", () => {
			const entities = buildDvEntities(REAL_RUN_80FF_PROJECTION);
			const pitPos = entities.find((e) => e.name === "pit_position");
			expect(pitPos).toBeDefined();
			expect(pitPos?.parentEntity).toBe("hub_position");
			expect(pitPos?.columns[0]?.name).toBe("hk_position");
			expect(pitPos?.columns[0]?.role).toBe(
				"PK/FK -&gt; hub_position".replace("&gt;", ">"),
			);

			const { edges } = buildBipartiteFlowElements(entities, "pit_position");
			const edgeIds = edges.map((e) => e.id);
			expect(edgeIds).toContain("edge:pit:pit_position->hub:hub_position");
			expect(edgeIds).toContain(
				"edge:hub:hub_position->lnk:lnk_position_exposure_to_issuer",
			);
			expect(edgeIds).toContain(
				"edge:hub:hub_issuer->lnk:lnk_position_exposure_to_issuer",
			);
		});

		it("deduplicates edge IDs for SAL self-links (hubReferences) and PIT trackedSatellites so React Flow never gets duplicate keys", () => {
			const salEntities = buildDvEntities(REAL_RUN_80FF_PROJECTION);
			const { edges: salEdges } = buildBipartiteFlowElements(
				salEntities,
				"sal_account",
			);
			const salEdgeIds = salEdges.map((e) => e.id);
			expect(
				salEdgeIds.filter(
					(id) => id === "edge:hub:hub_account->lnk:sal_account",
				),
			).toHaveLength(1);
			expect(new Set(salEdgeIds).size).toBe(salEdgeIds.length);

			const dupPitProj: DvSchemaProjectionDTO = {
				...REAL_RUN_80FF_PROJECTION,
				pit_tables: [
					{
						name: "pit_position",
						target_hub: "hub_position",
						tracked_satellites: ["sat_positions", "sat_positions"],
					},
				],
			};
			const { edges: pitEdges } = buildBipartiteFlowElements(
				buildDvEntities(dupPitProj),
				"pit_position",
			);
			const pitEdgeIds = pitEdges.map((e) => e.id);
			expect(
				pitEdgeIds.filter(
					(id) => id === "edge:pit:pit_position->sat:sat_positions",
				),
			).toHaveLength(1);
			expect(new Set(pitEdgeIds).size).toBe(pitEdgeIds.length);
		});

		it("forwards compareRunId from Step 6 selectedBaselineRunId when valid prior run ID, and omits it for __cold_start__", () => {
			const callsWithBaseline: Array<Record<string, unknown> | undefined> = [];
			const treeWithBaseline = Step6PipelineBuildAndRun({
				sessionId: "s-115ebe9242732b7f",
				activeRunId: "run-80ff425c8100",
				selectedBaselineRunId: "run-4804789955aa",
				onExploreDvSchema: (params) => callsWithBaseline.push(params),
			});
			const btnWithBaseline = findElementByTestId(
				treeWithBaseline,
				"btn-explore-dv-schema",
			);
			expect(btnWithBaseline).not.toBeNull();
			(btnWithBaseline?.onClick as () => void)();
			expect(callsWithBaseline[0]).toEqual({
				runId: "run-80ff425c8100",
				compareRunId: "run-4804789955aa",
				sessionId: "s-115ebe9242732b7f",
			});

			const callsCold: Array<Record<string, unknown> | undefined> = [];
			const treeCold = Step6PipelineBuildAndRun({
				sessionId: "s-115ebe9242732b7f",
				activeRunId: "run-80ff425c8100",
				selectedBaselineRunId: "__cold_start__",
				onExploreDvSchema: (params) => callsCold.push(params),
			});
			const btnCold = findElementByTestId(treeCold, "btn-explore-dv-schema");
			(btnCold?.onClick as () => void)();
			expect(callsCold[0]).toEqual({
				runId: "run-80ff425c8100",
				sessionId: "s-115ebe9242732b7f",
			});

			const callsUploaded: Array<Record<string, unknown> | undefined> = [];
			const treeUploaded = Step6PipelineBuildAndRun({
				sessionId: "s-115ebe9242732b7f",
				activeRunId: "run-80ff425c8100",
				selectedBaselineRunId: "__uploaded__",
				onExploreDvSchema: (params) => callsUploaded.push(params),
			});
			const btnUploaded = findElementByTestId(
				treeUploaded,
				"btn-explore-dv-schema",
			);
			(btnUploaded?.onClick as () => void)();
			expect(callsUploaded[0]).toEqual({
				runId: "run-80ff425c8100",
				sessionId: "s-115ebe9242732b7f",
			});
		});
	});
});
