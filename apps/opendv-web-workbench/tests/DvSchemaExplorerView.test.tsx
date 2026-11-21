/**
 * M4 Failing-First TDD Tests — Multi-View Interactive Data Vault 2.0 Schema Explorer
 * (`apps/opendv-web-workbench/tests/DvSchemaExplorerView.test.tsx`)
 *
 * Expected implementation module (`< 5,000` cl100k_base tokens):
 * - `../src/explorer/dv-schema/DvSchemaExplorerView` (replacing `DvSchemaExplorerShell` placeholder while preserving `runId`, `compareRunId`, `sessionId`, `onGoPipeline`, `onGoOntology` props)
 *
 * Covers (`FR-DVE-01..07`, `D11`, `D26`, `DESIGN.md`):
 * - View 1 (`06b`): Interactive Bipartite `Hub–Link–Sat–PIT–Bridge` graph (`INV-05 ✓`), Zone/PII/Wave-Delta filter pills + search, Physical Table & Lineage Inspector (`BKCC`, `Alg A.5` SQL, Gate `0–6` badges), native `<dialog>` Table DDL/DML modal
 * - View 2 (`06c`): `100% Triage & Wave Diff` (`W1 ➔ W2 ➔ W3` diff cards `EVO-01/02/03/07/08`, `column_triage_ledger` table + CSV export)
 * - View 3 (`D11`): Populated Vault Row Preview (`/dv-schema/tables/{table}/rows`) + Safe Read-Only SQL Runner (`/dv-schema/query`), downgrading SQL runner to view-only below `768px` (`D26`)
 * - View 4: Per-Zone `Z0..Z4` DuckDB & Databricks Delta SQL + `final_contract.yaml` & `final_canonical_ir.json` inspector/download (`information_mart`, never `4-Zone` / `marts`)
 */

import { describe, expect, it } from "bun:test";
import type { DvSchemaProjectionDTO } from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import {
	DvSchemaExplorerView,
	formatTriageCsv,
} from "../src/explorer/dv-schema/DvSchemaExplorerView";
import { deriveGateBadge } from "../src/explorer/dv-schema/DvTableLineageInspector";
import {
	fetchDvRunReportAndArtifacts,
	normalizeFourZoneSql,
} from "../src/explorer/dv-schema/dv-schema-layout";
import { createExplorerStore } from "../src/explorer/explorer-store";
import { GLOBAL_COMMERCE_EXPLORER_BUNDLE } from "./fixtures/commerce-explorer-fixture";

const SAMPLE_PROJECTION: DvSchemaProjectionDTO = {
	run_id: "run-w3-003",
	compare_run_id: "run-w2-002",
	model_name: "InstitutionalAssetVault",
	hubs: [
		{
			name: "hub_portfolio_position",
			hk_column: "hk_portfolio_position",
			business_keys: ["position_id"],
			bkcc_column_expr: "TIER_2_ENTERPRISE_SOR:'GLOBAL'",
			ontology_concept_uri: "urn:ontology:entity:portfolio_position",
			source_mappings: [
				{ source_table: "daily_positions", source_column: "position_id" },
			],
		},
		{
			name: "hub_custody_account",
			hk_column: "hk_custody_account",
			business_keys: ["account_code"],
			bkcc_column_expr: "TIER_3_LOCAL_SCOPED:tenant_id",
			ontology_concept_uri: "urn:ontology:entity:custody_account",
			source_mappings: [
				{ source_table: "custody_holdings", source_column: "account_code" },
			],
		},
	],
	links: [
		{
			name: "lnk_position_custody_account",
			hk_column: "hk_lnk_position_custody_account",
			link_type: "STANDARD",
			hub_references: [
				{
					hub_name: "hub_portfolio_position",
					hk_column: "hk_portfolio_position",
				},
				{ hub_name: "hub_custody_account", hk_column: "hk_custody_account" },
			],
			degenerate_columns: [],
			where_filter_condition: "sub_custodian_id IS NOT NULL",
		},
	],
	satellites: [
		{
			name: "sat_position_ext_v2",
			parent_entity: "hub_portfolio_position",
			satellite_type: "STANDARD",
			hash_diff_column: "hash_diff",
			hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
			payload_columns: [
				{ name: "intraday_margin_buffer_bps", sql_type: "DECIMAL(10,2)" },
				{ name: "custodian_sla_tier", sql_type: "VARCHAR(32)" },
			],
			soft_deprecated_columns: ["legacy_desk_memo"],
		},
		{
			name: "sat_custody_account_pii",
			parent_entity: "hub_custody_account",
			satellite_type: "PII",
			hash_diff_column: "hash_diff",
			hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
			payload_columns: [
				{ name: "beneficial_owner_tax_id", sql_type: "VARCHAR(64)", pii: true },
			],
			soft_deprecated_columns: [],
		},
	],
	reference_tables: [
		{
			name: "ref_currency",
			natural_code_column: "currency_code",
			description_columns: ["currency_name"],
		},
	],
	pit_tables: [
		{
			name: "pit_portfolio_position",
			target_hub: "hub_portfolio_position",
			tracked_satellites: ["sat_position_ext_v2"],
		},
	],
	column_triage_ledger: [
		{
			wave: "W1",
			source_table: "daily_positions",
			source_column: "position_id",
			triage_role: "BUSINESS_KEY",
			graph_a_uri: "urn:ontology:entity:portfolio_position",
			staging_expr: "TRIM(CAST(position_id AS VARCHAR))",
			target_entity: "hub_portfolio_position.position_id",
		},
		{
			wave: "W2",
			source_table: "custody_holdings",
			source_column: "beneficial_owner_tax_id",
			triage_role: "PII_SENSITIVE",
			graph_a_uri: "urn:ontology:entity:custody_account",
			staging_expr: "beneficial_owner_tax_id",
			target_entity: "sat_custody_account_pii.beneficial_owner_tax_id",
		},
	],
	scorecard: {
		gate_0: "PASS",
		gate_1: "PASS",
		gate_2: "PASS",
		gate_3: "PASS",
		gate_4: "PASS",
		gate_5: "PASS",
		gate_6: "PASS",
		dv_dyn_passed: 15,
		dv_dyn_total: 15,
	},
	wave_diff: {
		compare_run_id: "run-w2-002",
		dropped_columns_count: 0,
		hash_diff_mutations_count: 0,
		added_hubs: [],
		added_links: ["lnk_position_custody_account"],
		added_satellites: ["sat_position_ext_v2"],
		added_columns: [
			{
				satellite: "sat_position_ext_v2",
				column: "intraday_margin_buffer_bps",
			},
		],
		soft_deprecated_columns: [
			{ satellite: "sat_position_ext_v2", column: "legacy_desk_memo" },
		],
	},
};

describe("M4: Multi-View Interactive DV Schema Explorer (FR-DVE-01..07)", () => {
	describe("0. Shell Mounting & Props Compatibility", () => {
		it("mounts DvSchemaExplorerView inside OpenDVWorkbenchExplorer on /dv-schema without placeholder text", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				startView: "dv_schema",
				syncBrowserUrl: false,
			});
			store
				.getState()
				.syncFromUrl("/dv-schema", "?run=run-w3-003&compare=run-w2-002");

			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(html).toContain('data-testid="dv-schema-explorer-view"');
			expect(html).not.toContain(
				'data-testid="dv-schema-explorer-placeholder"',
			);
		});
	});

	describe("1. View 1 (06b): Bipartite Topology, Zone/PII/Delta Filters, Inspector & Native <dialog> DDL/DML", () => {
		it("renders INV-05 bipartite badge, all 9 filter pills, search input, Physical Table Inspector (BKCC, Alg A.5, Gate 0-6), and open <dialog> DDL/DML modal", () => {
			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					compareRunId: "run-w2-002",
					sessionId: "ses-w1-portfolio-001",
					initialTab: "graph",
					initialProjection: SAMPLE_PROJECTION,
					selectedEntityName: "hub_portfolio_position",
					isDdlDialogOpen: true,
					entityDdlSql: {
						duckdb:
							"CREATE TABLE raw_vault.hub_portfolio_position (hk_portfolio_position CHAR(64) PRIMARY KEY);",
						databricks:
							"CREATE TABLE cat.raw_vault.hub_portfolio_position (hk_portfolio_position STRING) USING DELTA;",
					},
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);

			expect(html).toContain('data-testid="dv-schema-explorer-view"');
			expect(html).toContain("INV-05");
			for (const pill of [
				"All Zones",
				"Z0 Refs",
				"Z1 Staging",
				"Z2 Hubs",
				"Z2 Links &amp; SAL",
				"Z2 Satellites",
				"Z3 PIT &amp; Bridges",
				"PII Split Only",
				"+ Wave Delta",
			]) {
				expect(html).toContain(pill);
			}
			expect(html).toContain('data-testid="dv-schema-search-input"');
			expect(html).toContain("hub_portfolio_position");
			expect(html).toContain("TIER_2_ENTERPRISE_SOR");
			expect(html).toContain("OCTET_LENGTH");
			expect(html).toContain("15/15");
			expect(html).toContain("<dialog");
			expect(html).toContain("CREATE TABLE raw_vault.hub_portfolio_position");
		});

		it("never fabricates 15/15 for empty or failing scorecards and derives canonical Gate 0-5 + Sandbox verdicts", () => {
			const emptyHtml = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-empty-001",
					initialTab: "graph",
					initialProjection: { ...SAMPLE_PROJECTION, scorecard: {} },
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			expect(emptyHtml).toContain("Gates 0–6 · Pending / Unverified");
			expect(emptyHtml).not.toContain("15/15");
			expect(emptyHtml).not.toContain("#5db872");

			const failingHtml = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-fail-001",
					initialTab: "graph",
					initialProjection: {
						...SAMPLE_PROJECTION,
						scorecard: {
							gate_0_profile_drift: "PASS",
							gate_1_contract_grain: "AUTO_REPAIRED",
							gate_2_sqlglot_ast: "PASS",
							gate_3_networkx_dag: "PASS",
							gate_4_yagni_cost_guard: "PASS",
							gate_5_duckdb_sandbox: "FAIL",
							shadow_passed: false,
						},
					},
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			expect(failingHtml).toContain("Gates 0–5 · 5/6 PASS · Sandbox FAIL");
			expect(failingHtml).not.toContain("15/15");
			expect(failingHtml).toContain("#c64545");

			expect(
				deriveGateBadge({
					gate_0_profile_drift: "PASS",
					gate_1_contract_grain: "AUTO-FIX",
					gate_2_sqlglot_ast: "PASS",
					gate_3_networkx_dag: "PASS",
					gate_4_yagni_cost_guard: "PASS",
					gate_5_duckdb_sandbox: "PASS",
					shadow_passed: true,
				}),
			).toEqual({
				label: "Gates 0–5 · 6/6 PASS · Sandbox PASS",
				tone: "success",
			});
		});
	});

	describe("2. View 2 (06c): 100% Triage & Wave Diff (W1 -> W2 -> W3) + CSV Export", () => {
		it("renders 100% Triage Ledger, EVO-01/02/03/07/08 wave diff cards, 0 DROP / 0 hash_diff Mutation guarantee, and CSV export", () => {
			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					compareRunId: "run-w2-002",
					initialTab: "triage",
					initialProjection: SAMPLE_PROJECTION,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);

			expect(html).toContain("100%");
			expect(html).toContain("2/2");
			expect(html).toContain("sat_position_ext_v2");
			expect(html).toContain("legacy_desk_memo");
			expect(html).toContain("0 DROP");
			expect(html).toContain("0 hash_diff");
			expect(html).toContain('data-testid="btn-export-triage-csv"');

			const csv = formatTriageCsv(SAMPLE_PROJECTION.column_triage_ledger);
			expect(csv).toContain("wave,source_table,source_column,triage_role");
			expect(csv).toContain("daily_positions,position_id,BUSINESS_KEY");
		});

		it("applies minWidth: 0 to both DvTriageAndWaveDiffTab grid children and declares .claude-responsive-grid > * { min-width: 0; } globally outside mobile media queries (C11)", async () => {
			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					compareRunId: "run-w2-002",
					initialTab: "triage",
					initialProjection: SAMPLE_PROJECTION,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			const gridMarker = 'class="claude-responsive-grid"';
			const gridIdx = html.indexOf(gridMarker);
			expect(gridIdx).toBeGreaterThan(-1);
			const rightCardIdx = html.indexOf('class="claude-card-cream"', gridIdx);
			expect(rightCardIdx).toBeGreaterThan(gridIdx);
			const leftSlice = html.slice(gridIdx, rightCardIdx);
			const rightSlice = html.slice(rightCardIdx, rightCardIdx + 400);
			expect(leftSlice).toContain("min-width:0");
			expect(rightSlice).toContain("min-width:0");

			const { readFileSync } = await import("node:fs");
			const { resolve } = await import("node:path");
			const cssPath = resolve(import.meta.dir, "../src/index.css");
			const css = readFileSync(cssPath, "utf8");
			const mobileMediaIdx = css.indexOf("@media (max-width: 767px)");
			expect(mobileMediaIdx).toBeGreaterThan(-1);
			const globalCss = css.slice(0, mobileMediaIdx);
			expect(globalCss).toMatch(
				/\.claude-responsive-grid\s*>\s*\*\s*\{[^}]*min-width:\s*0\s*;/,
			);
		});
	});

	describe("3. View 3 (D11): Populated Vault Row Preview & Safe Read-Only SQL Runner + Mobile <768px View-Only (D26)", () => {
		it("renders populated Vault rows (Zero Keys & Ghost Record) and Safe Read-Only SQL Runner results on desktop", () => {
			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					initialTab: "rows",
					initialProjection: SAMPLE_PROJECTION,
					selectedTableName: "raw_vault.hub_portfolio_position",
					tableRowsData: {
						run_id: "run-w3-003",
						table_name: "raw_vault.hub_portfolio_position",
						schema_name: "raw_vault",
						columns: ["hk_portfolio_position", "position_id", "bkcc"],
						rows: [
							{
								hk_portfolio_position: "0".repeat(64),
								position_id: "-1",
								bkcc: "SYSTEM_ZERO_KEY",
							},
							{
								hk_portfolio_position: "f".repeat(64),
								position_id: "-2",
								bkcc: "SYSTEM_NA_KEY",
							},
							{
								hk_portfolio_position: "a".repeat(64),
								position_id: "POS-100",
								bkcc: "GLOBAL",
							},
						],
						row_count: 3,
						total_count: 3,
						limit: 50,
						offset: 0,
						truncated: false,
					},
					sqlQueryText:
						"SELECT * FROM information_mart.vw_portfolio_position_current LIMIT 10;",
					sqlQueryResult: {
						run_id: "run-w3-003",
						columns: ["position_id", "market_value"],
						rows: [{ position_id: "POS-100", market_value: 152400.5 }],
						row_count: 1,
						truncated: false,
						duration_ms: 4.2,
					},
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);

			expect(html).toContain("SYSTEM_ZERO_KEY");
			expect(html).toContain("POS-100");
			expect(html).toContain('data-testid="dv-sql-runner-textarea"');
			expect(html).toContain('data-testid="btn-run-safe-sql"');
			expect(html).toContain("152400.5");
		});

		it("downgrades Safe SQL Runner to view-only mode below 768px viewport (D26)", () => {
			const mobileHtml = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					initialTab: "rows",
					viewportWidth: 390,
					initialProjection: SAMPLE_PROJECTION,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);

			expect(mobileHtml).toContain("View-only");
			expect(mobileHtml).toMatch(
				/<button[^>]*data-testid="btn-run-safe-sql"[^>]*disabled/i,
			);
		});
	});

	describe("4. View 4: Per-Zone Z0..Z4 SQL & Contract/IR Inspector (FR-DVE-07)", () => {
		it("renders Z0..Z4 DuckDB & Databricks Delta SQL switcher (using information_mart, never 4-Zone/marts) plus final_contract.yaml & final_canonical_ir.json inspection", () => {
			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-w3-003",
					initialTab: "sql",
					initialProjection: SAMPLE_PROJECTION,
					zoneSqlDuckDb: {
						Z0: "CREATE SCHEMA IF NOT EXISTS information_mart;",
						Z1: "CREATE VIEW staging.stg_positions AS SELECT 1;",
						Z2: "INSERT INTO raw_vault.hub_portfolio_position SELECT 1;",
						Z3: "INSERT INTO business_vault.pit_portfolio_position SELECT 1;",
						Z4: "CREATE VIEW information_mart.vw_portfolio_position_current AS SELECT 1;",
					},
					zoneSqlDatabricks: {
						Z0: "CREATE SCHEMA IF NOT EXISTS cat.information_mart;",
						Z1: "CREATE OR REPLACE VIEW cat.staging.stg_positions AS SELECT 1;",
						Z2: "INSERT INTO cat.raw_vault.hub_portfolio_position SELECT 1;",
						Z3: "INSERT INTO cat.business_vault.pit_portfolio_position SELECT 1;",
						Z4: "CREATE OR REPLACE VIEW cat.information_mart.vw_portfolio_position_current AS SELECT 1;",
					},
					finalContractYaml:
						"contract_version: v5\nmodel_name: InstitutionalAssetVault",
					finalCanonicalIrJson: '{"model_name":"InstitutionalAssetVault"}',
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);

			for (const zone of ["Z0", "Z1", "Z2", "Z3", "Z4"]) {
				expect(html).toContain(zone);
			}
			expect(html).toContain("information_mart");
			expect(html).not.toContain("4-Zone");
			expect(html).not.toMatch(/\bmarts\./i);
			expect(html).toContain("final_contract.yaml");
			expect(html).toContain("final_canonical_ir.json");
		});

		it("hydrates nested four_zone_sql (duckdb & databricks) from /report and final_contract/final_ir from /artifacts without synthetic SQL fallback", async () => {
			const nested = normalizeFourZoneSql({
				duckdb: {
					zone_0_staging: "CREATE SCHEMA bronze_real;",
					zone_1_raw_vault: "CREATE TABLE raw_vault.hub_real (hk CHAR(64));",
					zone_2_business_vault:
						"CREATE TABLE business_vault.pit_real (hk CHAR(64));",
					zone_3_info_mart: "CREATE VIEW information_mart.vw_real AS SELECT 1;",
					all_zones: "-- REAL_ALL_ZONES_DUCKDB\nSELECT 'real_duck_sql';",
				},
				databricks: {
					zone_0_staging: "CREATE SCHEMA cat.bronze_real;",
					zone_1_raw_vault:
						"CREATE TABLE cat.raw_vault.hub_real (hk STRING) USING DELTA;",
					zone_2_business_vault:
						"CREATE TABLE cat.business_vault.pit_real (hk STRING) USING DELTA;",
					zone_3_info_mart:
						"CREATE VIEW cat.information_mart.vw_real AS SELECT 1;",
					all_zones: "-- REAL_ALL_ZONES_DELTA\nSELECT 'real_delta_sql';",
				},
			});
			expect(nested.zoneSqlDuckDb?.Z0).toBe("CREATE SCHEMA bronze_real;");
			expect(nested.zoneSqlDuckDb?.Z4).toContain("REAL_ALL_ZONES_DUCKDB");
			expect(nested.zoneSqlDatabricks?.Z1).toContain("USING DELTA");
			expect(nested.zoneSqlDatabricks?.Z4).toContain("REAL_ALL_ZONES_DELTA");

			const calledUrls: string[] = [];
			const mockFetch = (async (input: RequestInfo | URL) => {
				const url = String(input);
				calledUrls.push(url);
				if (url.endsWith("/report")) {
					return new Response(
						JSON.stringify({
							four_zone_sql: {
								duckdb: {
									zone_0_staging: "CREATE SCHEMA real_z0;",
									zone_1_raw_vault: "CREATE TABLE raw_vault.real_z1 (id INT);",
									zone_2_business_vault:
										"CREATE TABLE business_vault.real_z2 (id INT);",
									zone_3_info_mart:
										"CREATE VIEW information_mart.real_z3 AS SELECT 1;",
									all_zones: "SELECT 'hyd_duck_z4';",
								},
								databricks: {
									zone_0_staging: "CREATE SCHEMA cat.real_z0;",
									all_zones: "SELECT 'hyd_delta_z4';",
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
							content: "model_name: RealHydratedVault\nversion: 5",
						},
						{
							artifact_id: "final_ir",
							filename: "final_canonical_ir.json",
							content: '{"ir":"real_hydrated_ir"}',
						},
					]),
					{ status: 200, headers: { "Content-Type": "application/json" } },
				);
			}) as typeof fetch;

			const hydrated = await fetchDvRunReportAndArtifacts(
				"http://localhost:8420",
				"run-real-123",
				mockFetch,
			);
			expect(calledUrls).toContain(
				"http://localhost:8420/api/v1/runs/run-real-123/report",
			);
			expect(calledUrls).toContain(
				"http://localhost:8420/api/v1/runs/run-real-123/artifacts",
			);
			expect(hydrated.contractYaml).toContain("RealHydratedVault");
			expect(hydrated.irJson).toContain("real_hydrated_ir");

			const html = renderToStaticMarkup(
				createElement(DvSchemaExplorerView, {
					runId: "run-real-123",
					initialTab: "sql",
					initialProjection: SAMPLE_PROJECTION,
					zoneSqlDuckDb: hydrated.zoneSqlDuckDb,
					zoneSqlDatabricks: hydrated.zoneSqlDatabricks,
					finalContractYaml: hydrated.contractYaml,
					finalCanonicalIrJson: hydrated.irJson,
					onGoPipeline: () => {},
					onGoOntology: () => {},
				}),
			);
			expect(html).toContain("hyd_duck_z4");
			expect(html).toContain("RealHydratedVault");
			expect(html).not.toContain(
				"-- Z4: Information Mart Bi-Temporal Current Views",
			);
		});
	});
});
