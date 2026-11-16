/**
 * Unit & Integration Tests: Step 1 (Foundation) & Step 2 (Connectors) (WS5.2).
 *
 * Verifies:
 * - Step 1: Active Studio session picker, VaultBlueprint mode, Raw Ontology mode
 * - Step 1: Inferred Mode Banner (Greenfield vs Evolution) and live inferred execution plan (COLD_START_N1, VERIFIED_NO_OP, UNSTRUCTURED_FIRST, INCREMENTAL_SOURCE)
 * - Step 2: Dynamic connector cards (SQLite, Databricks, Files, DuckDB)
 * - Step 2: Direct file feed and unstructured documents (PDF, Markdown, Notes)
 * - Step 2: Configured sources feed table and removal actions
 */

import { describe, expect, it } from "bun:test";
import type { SourceFeedDTO, VaultBlueprintDTO } from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Step1Foundation } from "../src/explorer/builder/Step1Foundation";
import { Step2ConnectorSources } from "../src/explorer/builder/Step2ConnectorSources";
import { createDefaultWizardFormState } from "../src/explorer/builder/wizard-types";

const TEST_CONNECTOR_ADAPTERS = [
	{ adapter_id: "sqlite", display_name: "SQLite Database", supports_multi_table: true },
	{ adapter_id: "databricks", display_name: "Databricks Unity Catalog", supports_multi_table: true },
	{ adapter_id: "file", display_name: "Local Tabular Files" },
	{ adapter_id: "duckdb", display_name: "DuckDB / Delta Lake" },
];

describe("Step 1 (Foundation) & Step 2 (Connectors) (WS5.2)", () => {
	describe("1. Step 1 Foundation: Modes & Inferred Execution Plan Banners", () => {
		it("renders Step 1 with heading, radio options, and greenfield zero-data cold start plan", () => {
			const state = createDefaultWizardFormState({
				foundationMode: "studio_session",
				contract: null,
				contractPath: "",
				configuredSources: [],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-foundation"');
			expect(html).toContain("Step 1: Choose Foundation");
			expect(html).toContain('data-testid="inferred-mode-status-card"');
			expect(html).toContain("Greenfield Future-Proof Design");
			expect(html).toContain('data-testid="inferred-execution-plan-card"');
			expect(html).toContain("COLD_START_N1");
			expect(html).toContain("Zero-Data: Ontology-Only Seed Synthesis");
		});

		it("displays Existing Schema Evolution and VERIFIED_NO_OP when contract is present without data", () => {
			const state = createDefaultWizardFormState({
				contract: { hubs: [{ table_name: "hub_fund" }] },
				contractPath: "schemas/dv_contract.yaml",
				configuredSources: [],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain("Existing Schema Update");
			expect(html).toContain("Evolution");
			expect(html).toContain("VERIFIED_NO_OP");
			expect(html).toContain("Zero-Data: Deterministic Verification Pass");
		});

		it("displays UNSTRUCTURED_FIRST plan when unstructured documents exist and sources are empty", () => {
			const state = createDefaultWizardFormState({
				contract: null,
				configuredSources: [],
				unstructuredDocs: [{ doc_id: "doc1", title: "fibo_spec.md" }],
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain("UNSTRUCTURED_FIRST");
			expect(html).toContain("Unstructured Materials First");
		});

		it("displays INCREMENTAL_SOURCE plan when tabular sources are configured", () => {
			const state = createDefaultWizardFormState({
				contract: null,
				configuredSources: [
					{
						feed_id: "f1",
						source_system: "sqlite",
						uri: "data/fund.sqlite",
					},
				],
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain("INCREMENTAL_SOURCE");
			expect(html).toContain("Incremental Tabular Onboarding");
		});

		it("renders Active Studio Session dropdown with provided availableSessions", () => {
			const state = createDefaultWizardFormState({
				foundationMode: "studio_session",
				activeSessionId: "session-am-core",
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
					availableSessions: [
						{
							session_id: "session-am-core",
							domain_namespace: "asset_management",
							concepts_count: 24,
						},
						{
							session_id: "session-fibo",
							domain_namespace: "fibo",
							concepts_count: 42,
						},
					],
				}),
			);

			expect(html).toContain('data-testid="select-studio-session"');
			expect(html).toContain("session-am-core");
			expect(html).toContain("asset_management");
			expect(html).toContain("24 concepts");
			expect(html).toContain("session-fibo");
		});

		it("renders VaultBlueprint mode with lineage card when blueprint is set", () => {
			const mockBlueprint: VaultBlueprintDTO = {
				blueprint_version: "1.0.0",
				domain_namespace: "asset_management",
				generated_at_utc: "2026-11-15T00:00:00Z",
				lineage: {
					blueprint_revision: "v2.1.0",
					parent_blueprint_sha256: "parent_sha_12345678",
					ontology_sha256: "ont_sha_87654321",
					contract_sha256: "contract_sha_11223344",
					generator: "open-dv",
				},
				ontology: {},
			};

			const state = createDefaultWizardFormState({
				foundationMode: "vault_blueprint",
				blueprintPath: "data/blueprints/vault_blueprint.yaml",
				blueprint: mockBlueprint,
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="input-blueprint-path"');
			expect(html).toContain('data-testid="input-blueprint-file"');
			expect(html).toContain('data-testid="blueprint-lineage-card"');
			expect(html).toContain('data-testid="blueprint-revision-badge"');
			expect(html).toContain("v2.1.0");
			expect(html).toContain("asset_management");
		});

		it("renders Raw Ontology mode with ontology and contract path inputs and clear contract button", () => {
			const state = createDefaultWizardFormState({
				foundationMode: "raw_ontology",
				ontologyPath: "schemas/ontology.yaml",
				contractPath: "schemas/contract.yaml",
				contract: { hubs: [] },
			});

			const html = renderToStaticMarkup(
				createElement(Step1Foundation, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="input-ontology-path"');
			expect(html).toContain('data-testid="input-contract-path"');
			expect(html).toContain('data-testid="btn-clear-contract"');
		});
	});

	describe("2. Step 2 Connector Sources: Cards, Feeds & Documents", () => {
		it("renders connector cards for SQLite, Databricks, Files, and DuckDB", () => {
			const state = createDefaultWizardFormState({
				configuredSources: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step2ConnectorSources, {
					formState: state,
					initialAdapters: TEST_CONNECTOR_ADAPTERS,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-connectors"');
			expect(html).toContain('data-testid="connector-card-sqlite"');
			expect(html).toContain('data-testid="connector-card-databricks"');
			expect(html).toContain('data-testid="connector-card-file"');
			expect(html).toContain('data-testid="connector-card-duckdb"');
			expect(html).toContain("Inspect Tables ➔");
		});

		it("renders empty sources banner when no sources are configured", () => {
			const state = createDefaultWizardFormState({
				configuredSources: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step2ConnectorSources, {
					formState: state,
					initialAdapters: TEST_CONNECTOR_ADAPTERS,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="empty-sources-banner"');
			expect(html).toContain("No tabular data sources configured yet");
			expect(html).toContain("0 sources configured");
		});

		it("renders configured sources table when sources exist", () => {
			const mockFeeds: SourceFeedDTO[] = [
				{
					feed_id: "feed_sqlite_1",
					source_system: "sqlite_prod",
					uri: "data/am_sec_treasury_ibor.sqlite",
					adapter: "sqlite",
					table_selection: {
						mode: "EXPLICIT",
						tables: ["security_master", "fund_portfolio", "daily_positions"],
					},
				},
				{
					feed_id: "feed_file_2",
					source_system: "local_filesystem",
					uri: "data/trades.parquet",
					adapter: "file",
					table_selection: {
						mode: "EXPLICIT",
						tables: ["trades"],
					},
				},
			];

			const state = createDefaultWizardFormState({
				configuredSources: mockFeeds,
			});

			const html = renderToStaticMarkup(
				createElement(Step2ConnectorSources, {
					formState: state,
					initialAdapters: TEST_CONNECTOR_ADAPTERS,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="configured-sources-table"');
			expect(html).toContain('data-testid="feed-row-feed_sqlite_1"');
			expect(html).toContain("SQLITE");
			expect(html).toContain("data/am_sec_treasury_ibor.sqlite");
			expect(html).toContain("3 tables");
			expect(html).toContain('data-testid="btn-remove-feed-feed_sqlite_1"');

			expect(html).toContain('data-testid="feed-row-feed_file_2"');
			expect(html).toContain("FILE");
			expect(html).toContain("data/trades.parquet");
			expect(html).toContain("2 sources configured");
		});

		it("renders direct file feed input and button", () => {
			const state = createDefaultWizardFormState();

			const html = renderToStaticMarkup(
				createElement(Step2ConnectorSources, {
					formState: state,
					initialAdapters: TEST_CONNECTOR_ADAPTERS,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="input-file-feed-path"');
			expect(html).toContain('data-testid="btn-add-file-feed"');
		});

		it("renders unstructured document section with input, type selector, and document list", () => {
			const state = createDefaultWizardFormState({
				unstructuredDocs: [
					{
						doc_id: "doc_1",
						title: "form_10k_filing.pdf",
						doc_type: "pdf",
					},
					{
						doc_id: "doc_2",
						title: "regulatory_rules.md",
						doc_type: "markdown",
					},
				],
			});

			const html = renderToStaticMarkup(
				createElement(Step2ConnectorSources, {
					formState: state,
					initialAdapters: TEST_CONNECTOR_ADAPTERS,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="input-doc-title"');
			expect(html).toContain('data-testid="select-doc-type"');
			expect(html).toContain('data-testid="btn-add-document"');
			expect(html).toContain('data-testid="unstructured-docs-list"');
			expect(html).toContain('data-testid="doc-row-0"');
			expect(html).toContain("PDF");
			expect(html).toContain("form_10k_filing.pdf");
			expect(html).toContain('data-testid="doc-row-1"');
			expect(html).toContain("MARKDOWN");
			expect(html).toContain("regulatory_rules.md");
		});
	});
});
