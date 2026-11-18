import { describe, expect, it } from "bun:test";
import type {
	BlueprintExportResponseDTO,
	BlueprintLineageDTO,
	ColumnTriageLedgerEntryDTO,
	ConnectorUIManifestDTO,
	InferredExecutionPlanDTO,
	PipelineInputDTO,
	SourceFeedDTO,
	SourceInspectRequestDTO,
	SourceInspectResponseDTO,
	VaultBlueprintDTO,
} from "../src";

describe("Single-Run & VaultBlueprint Protocol Types (WS1.4)", () => {
	it("validates ColumnTriageLedgerEntryDTO with source_sql_type", () => {
		const entry: ColumnTriageLedgerEntryDTO = {
			bronze_table: "stg_funds",
			column_name: "fund_code",
			elm_classification: "BUSINESS_KEY",
			target_dv_entity: "hub_fund",
			source_sql_type: "VARCHAR(64)",
		};
		expect(entry.bronze_table).toBe("stg_funds");
		expect(entry.source_sql_type).toBe("VARCHAR(64)");
	});

	it("validates VaultBlueprintDTO and BlueprintLineageDTO structure", () => {
		const lineage: BlueprintLineageDTO = {
			schema_version: "1.0.0",
			blueprint_revision: 1,
			domain_namespace: "asset_management",
			ontology_sha256: "0".repeat(64),
			contract_sha256: null,
			parent_blueprint_sha256: null,
			lineage_notes: "Day-0 Genesis Export",
		};

		const blueprint: VaultBlueprintDTO = {
			schema_version: "1.0.0",
			domain_namespace: "asset_management",
			lineage,
			ontology: { ontology_version: "1.0.0" },
			contract: null,
		};

		expect(blueprint.domain_namespace).toBe("asset_management");
		expect(blueprint.lineage.blueprint_revision).toBe(1);
		expect(blueprint.contract).toBeNull();
	});

	it("validates SourceFeedDTO with multi-table SQLite and Databricks UC", () => {
		const sqliteFeed: SourceFeedDTO = {
			feed_id: "sec_treasury",
			source_system: "sec_edgar",
			uri: "sqlite:///data/am_sec_treasury_ibor.sqlite",
			adapter: "sqlite",
			table_selection: {
				mode: "EXPLICIT",
				tables: ["issuers", "securities", "holdings_13f"],
			},
			sampling_policy: {
				max_sandbox_rows: 5000,
				strategy: "DETERMINISTIC_LIMIT",
			},
			target_dialect: "duckdb",
		};
		expect(sqliteFeed.table_selection?.tables?.length).toBe(3);

		const ucFeed: SourceFeedDTO = {
			feed_id: "uc_ibor",
			source_system: "databricks_ibor",
			uri: "databricks://dbc-01/am_prod/ibor",
			adapter: "databricks",
			namespace: {
				catalog: "am_prod",
				schema_name: "ibor",
			},
			credentials_ref: "env:DATABRICKS_TOKEN",
			sampling_policy: {
				max_sandbox_rows: 10000,
				strategy: "TABLESAMPLE_BERNOULLI",
				sample_ratio: 0.1,
			},
			target_dialect: "databricks",
		};
		expect(ucFeed.namespace?.catalog).toBe("am_prod");
		expect(ucFeed.credentials_ref).toBe("env:DATABRICKS_TOKEN");
	});

	it("validates ConnectorUIManifestDTO capabilities", () => {
		const manifest: ConnectorUIManifestDTO = {
			adapter_id: "databricks",
			display_name: "Databricks Unity Catalog",
			description: "3-level catalog introspection with column_tags",
			schemes: ["databricks", "uc"],
			execution_mode: "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
			client_uploadable: false,
			supports_multi_table: true,
			supports_catalog_hierarchy: true,
			supports_foreign_keys: true,
			supports_governance_tags: true,
			supports_time_travel: true,
		};
		expect(manifest.execution_mode).toBe("REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE");
		expect(manifest.supports_catalog_hierarchy).toBe(true);
	});

	it("validates SourceInspectRequestDTO and SourceInspectResponseDTO", () => {
		const req: SourceInspectRequestDTO = {
			uri: "sqlite:///data.sqlite",
			adapter: "sqlite",
		};
		expect(req.adapter).toBe("sqlite");

		const resp: SourceInspectResponseDTO = {
			adapter_id: "sqlite",
			tables: [{ table_name: "securities", row_count_estimate: 100 }],
			inspected_at_utc: "2026-11-15T04:00:00Z",
		};
		expect(resp.tables?.length).toBe(1);
	});

	it("validates InferredExecutionPlanDTO across 7-row matrix", () => {
		const plan: InferredExecutionPlanDTO = {
			plan_id: "plan_001",
			primary_action: "HYBRID_PER_SOURCE",
			table_actions: [
				{
					source_system: "ibor",
					table_name: "positions",
					action: "INCREMENTAL_ONBOARDING",
					matched_existing_entity: "hub_position",
				},
				{
					source_system: "sqlite",
					table_name: "drifted",
					action: "INTRA_SOURCE_DRIFT",
					matched_existing_entity: "sat_drifted",
				},
			],
			doc_actions: [
				{
					doc_id: "doc1",
					action: "EXTRACT_SPANS_AND_LINKS",
					target_hub: "hub_document",
				},
			],
			inferred_at_utc: "2026-11-15T04:00:00Z",
			explanation: "Hybrid execution plan",
		};
		expect(plan.primary_action).toBe("HYBRID_PER_SOURCE");
		expect(plan.table_actions?.length).toBe(2);
		expect(plan.doc_actions?.length).toBe(1);
	});

	it("validates PipelineInputDTO and BlueprintExportResponseDTO", () => {
		const inputDto: PipelineInputDTO = {
			blueprint_path: "vault_blueprint.yaml",
			sources: [
				{
					feed_id: "f1",
					source_system: "s1",
					uri: "file:///test.parquet",
				},
			],
			target_dialect: "duckdb",
		};
		expect(inputDto.blueprint_path).toBe("vault_blueprint.yaml");
		expect(inputDto.sources?.length).toBe(1);

		const exportDto: BlueprintExportResponseDTO = {
			blueprint: {
				domain_namespace: "finance",
				lineage: {
					domain_namespace: "finance",
					ontology_sha256: "f".repeat(64),
				},
				ontology: {},
			},
			raw_vault_ddl: "CREATE TABLE raw_vault.hub_test ();",
			target_tables: [{ name: "hub_test", type: "HUB" }],
			exported_at_utc: "2026-11-15T04:00:00Z",
		};
		expect(exportDto.target_tables?.length).toBe(1);
	});
});
