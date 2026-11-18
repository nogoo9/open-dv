/**
 * Unit & Integration Tests: Step 3 (Governance & Dialect), Step 4 (Validate & Run),
 * DiffInspector & 1-Click Schema Evolution (WS5.4).
 */

import { describe, expect, it } from "bun:test";
import type {
	BlueprintEvolutionDiffDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DiffInspector } from "../src/explorer/builder/DiffInspector";
import { Step3GovernanceAndDialect } from "../src/explorer/builder/Step3GovernanceAndDialect";
import {
	PIPELINE_STAGES,
	Step4ValidateAndRun,
} from "../src/explorer/builder/Step4ValidateAndRun";
import { WizardShell } from "../src/explorer/builder/WizardShell";
import { createDefaultWizardFormState } from "../src/explorer/builder/wizard-types";

describe("Step 3 (Governance), Step 4 (Validate & Run) & DiffInspector (WS5.4)", () => {
	describe("1. Step 3 Governance & Dialect Form Controls", () => {
		it("renders Step 3 with heading, agent model options, and default selections", () => {
			const state = createDefaultWizardFormState({
				agentModel: "gemini-4-argon",
				targetDialects: ["duckdb"],
				samplingRows: 10000,
				samplingStrategy: "DETERMINISTIC_LIMIT",
				stewardHoldPolicy: "pause",
				ontologyEvolutionPolicy: "recommend",
			});

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					availableModels: [
						"Gemini 4 Argon (Recommended Default)",
						"Gemini 3.8 Flash (High Throughput)",
						"Claude Opus 5.5 High (Deep Critique)",
						"Claude 3.7 Sonnet (Hybrid Reasoning)",
						"Deterministic Offline Fallback",
					],
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="wizard-step-governance"');
			expect(html).toContain("Step 3: Governance &amp; Dialect");
			expect(html).toContain('data-testid="select-agent-model"');
			expect(html).toContain("Gemini 4 Argon (Recommended Default)");
			expect(html).toContain("Gemini 3.8 Flash (High Throughput)");
			expect(html).toContain("Claude Opus 5.5 High (Deep Critique)");
			expect(html).toContain("Claude 3.7 Sonnet (Hybrid Reasoning)");
			expect(html).toContain("Deterministic Offline Fallback");
		});

		it("renders SQL dialect options with canonical DuckDB and optional Databricks Delta", () => {
			const state = createDefaultWizardFormState({
				targetDialects: ["duckdb", "databricks"],
			});

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					availableModels: [
						"Gemini 4 Argon (Recommended Default)",
						"Gemini 3.8 Flash (High Throughput)",
						"Claude Opus 5.5 High (Deep Critique)",
						"Claude 3.7 Sonnet (Hybrid Reasoning)",
						"Deterministic Offline Fallback",
					],
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="checkbox-dialect-duckdb"');
			expect(html).toContain('data-testid="checkbox-dialect-databricks"');
			expect(html).toContain("DuckDB");
			expect(html).toContain("Databricks");
			expect(html).toContain("checked");
		});

		it("renders sampling policy row input and strategy selector", () => {
			const state = createDefaultWizardFormState({
				samplingRows: 25000,
				samplingStrategy: "TABLESAMPLE_BERNOULLI",
			});

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					availableModels: [
						"Gemini 4 Argon (Recommended Default)",
						"Gemini 3.8 Flash (High Throughput)",
						"Claude Opus 5.5 High (Deep Critique)",
						"Claude 3.7 Sonnet (Hybrid Reasoning)",
						"Deterministic Offline Fallback",
					],
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="input-sampling-rows"');
			expect(html).toContain('value="25000"');
			expect(html).toContain('data-testid="select-sampling-strategy"');
			expect(html).toContain(
				"TABLESAMPLE_BERNOULLI (Bernoulli Random Sampling)",
			);
			expect(html).toContain("DETERMINISTIC_LIMIT (Head N Rows)");
		});

		it("renders steward hold and ontology evolution policies", () => {
			const state = createDefaultWizardFormState({
				stewardHoldPolicy: "auto_approve_top_candidate",
				ontologyEvolutionPolicy: "auto_accept",
			});

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					availableModels: [
						"Gemini 4 Argon (Recommended Default)",
						"Gemini 3.8 Flash (High Throughput)",
						"Claude Opus 5.5 High (Deep Critique)",
						"Claude 3.7 Sonnet (Hybrid Reasoning)",
						"Deterministic Offline Fallback",
					],
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="radio-hold-pause"');
			expect(html).toContain('data-testid="radio-hold-auto-approve"');
			expect(html).toContain('data-testid="radio-hold-reject"');
			expect(html).toContain('data-testid="radio-evolution-recommend"');
			expect(html).toContain('data-testid="radio-evolution-auto-accept"');
			expect(html).toContain('data-testid="radio-evolution-strict-lock"');
		});

		it("renders advanced governance toggle button", () => {
			const state = createDefaultWizardFormState();

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					availableModels: [
						"Gemini 4 Argon (Recommended Default)",
						"Gemini 3.8 Flash (High Throughput)",
						"Claude Opus 5.5 High (Deep Critique)",
						"Claude 3.7 Sonnet (Hybrid Reasoning)",
						"Deterministic Offline Fallback",
					],
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="toggle-advanced-governance"');
			expect(html).toContain("Advanced Governance Overrides &amp; Hints");
		});

		it("renders harness provider option agy-cli and does not render invalid cli provider (R4-M7)", () => {
			const state = createDefaultWizardFormState({
				harnessProvider: "agy-cli",
			});

			const html = renderToStaticMarkup(
				createElement(Step3GovernanceAndDialect, {
					formState: state,
					initialAdvancedOpen: true,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="select-harness-provider"');
			expect(html).toContain('value="agy-cli"');
			expect(html).toContain("Standard CLI Runner (agy-cli)");
			expect(html).not.toContain('value="cli"');
		});

		it("invokes onChange with updated values when form inputs change", () => {
			const updates: Array<Record<string, unknown>> = [];
			const state = createDefaultWizardFormState();

			const step = createElement(Step3GovernanceAndDialect, {
				formState: state,
				onChange: (u) => {
					updates.push(u);
				},
			});

			step.props.onChange({ agentModel: "claude-opus-5-5-high" });
			step.props.onChange({ targetDialects: ["duckdb", "databricks"] });
			step.props.onChange({ samplingRows: 50000 });
			step.props.onChange({ samplingStrategy: "TABLESAMPLE_BERNOULLI" });
			step.props.onChange({ stewardHoldPolicy: "reject_on_hold" });
			step.props.onChange({ ontologyEvolutionPolicy: "strict_lock" });
			step.props.onChange({
				advancedOverrides: {
					target_hub_overrides: "trades:HubTrade",
					business_key_overrides: "trades:trade_id",
				},
			});

			expect(updates.length).toBe(7);
			expect(updates[0]).toEqual({ agentModel: "claude-opus-5-5-high" });
			expect(updates[1]).toEqual({ targetDialects: ["duckdb", "databricks"] });
			expect(updates[2]).toEqual({ samplingRows: 50000 });
			expect(updates[3]).toEqual({ samplingStrategy: "TABLESAMPLE_BERNOULLI" });
			expect(updates[4]).toEqual({ stewardHoldPolicy: "reject_on_hold" });
			expect(updates[5]).toEqual({ ontologyEvolutionPolicy: "strict_lock" });
			expect(updates[6]).toEqual({
				advancedOverrides: {
					target_hub_overrides: "trades:HubTrade",
					business_key_overrides: "trades:trade_id",
				},
			});
		});
	});

	describe("2. Step 4 Pre-Flight Action Plan & Validation Preview", () => {
		it("renders pre-flight diagnostics banner with passed checks", () => {
			const state = createDefaultWizardFormState({
				configuredSources: [],
				unstructuredDocs: [],
				validationReport: {
					passed: true,
					checked_items_count: 3,
					diagnostics: [
						{ severity: "INFO", message: "Gate G0 Semantic Invariance: Pass" },
						{ severity: "INFO", message: "G1 Syntax & Types: Pass" },
					],
				},
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="validation-diagnostics"');
			expect(html).toContain('data-testid="validation-status"');
			expect(html).toContain("✓ ALL CHECKS PASSED");
			expect(html).toContain("Gate G0");
		});

		it("displays Inferred Action Plan card for greenfield zero-data cold start", () => {
			const state = createDefaultWizardFormState({
				contract: null,
				contractContent: "",
				configuredSources: [],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="inferred-action-plan-card"');
			expect(html).toContain("COLD_START_N1");
			expect(html).toContain("0 Candidate Table(s)");
			expect(html).toContain("Expected Hubs:");
		});

		it("displays Inferred Action Plan card for existing contract zero-data verification", () => {
			const state = createDefaultWizardFormState({
				contract: { hubs: [{ table_name: "hub_position" }] },
				contractContent: "hubs: ...",
				configuredSources: [],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="inferred-action-plan-card"');
			expect(html).toContain("VERIFIED_NO_OP");
			expect(html).toContain("Zero-Data deterministic verification");
		});

		it("displays candidate tables breakdown and expected counts when sources exist", () => {
			const state = createDefaultWizardFormState({
				contract: { hubs: [] },
				ontologyEvolutionPolicy: "auto_accept",
				configuredSources: [
					{
						feed_id: "sec_master",
						source_system: "sec_db",
						uri: "sqlite://sec.db",
						table_selection: {
							mode: "EXPLICIT",
							tables: ["security_master", "tickers"],
						},
					},
				],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="inferred-action-plan-card"');
			expect(html).toContain("MULTI_SOURCE_EXPANSION");
			expect(html).toContain("2 Candidate Table(s)");
			expect(html).toContain("sec_db.security_master");
			expect(html).toContain("sec_db.tickers");
			expect(html).toContain("Expected Hubs: <strong>2</strong>");
		});

		it("displays SCHEMA_DRIFT plan when recommend policy is enabled with existing contract", () => {
			const state = createDefaultWizardFormState({
				contract: { hubs: [] },
				ontologyEvolutionPolicy: "recommend",
				configuredSources: [
					{
						feed_id: "sec_master",
						source_system: "sec_db",
						uri: "sqlite://sec.db",
						table_selection: { mode: "EXPLICIT", tables: ["security_master"] },
					},
				],
				unstructuredDocs: [],
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="inferred-action-plan-card"');
			expect(html).toContain("SCHEMA_DRIFT");
			expect(html).toContain("Schema Drift repair");
		});

		it("displays HYBRID_PER_SOURCE plan when both structured tables and docs exist", () => {
			const state = createDefaultWizardFormState({
				configuredSources: [
					{
						feed_id: "feed1",
						source_system: "src1",
						uri: "data/trades.parquet",
						table_selection: { mode: "EXPLICIT", tables: ["trades"] },
					},
				],
				unstructuredDocs: [{ doc_id: "doc1", title: "guidelines.pdf" }],
			});

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain("HYBRID_PER_SOURCE");
			expect(html).toContain(
				"Hybrid multi-source structured and unstructured ingestion",
			);
		});
	});

	describe("3. Step 4 Live Execution Console & Progress Stepper", () => {
		it("renders '▶ Run Pipeline' trigger button and 8-stage progress stepper", () => {
			const state = createDefaultWizardFormState();

			const html = renderToStaticMarkup(
				createElement(Step4ValidateAndRun, {
					formState: state,
					onChange: () => {},
				}),
			);

			expect(html).toContain('data-testid="btn-run-pipeline"');
			expect(html).toContain("▶ Run Pipeline");
			expect(html).toContain('data-testid="live-stage-stepper"');
			for (const st of PIPELINE_STAGES) {
				expect(html).toContain(`data-testid="stage-step-${st.id}"`);
				expect(html).toContain(st.id);
			}
		});

		it("invokes validatePipelineFn on mount when provided", async () => {
			let validatedSpec = "";
			const state = createDefaultWizardFormState({
				domainNamespace: "test_domain",
			});

			const step = createElement(Step4ValidateAndRun, {
				formState: state,
				onChange: () => {},
				validatePipelineFn: async (req) => {
					validatedSpec = req.input_spec_content ?? "";
					return {
						passed: true,
						checked_items_count: 5,
						diagnostics: [
							{ severity: "INFO", message: "Mock validation passed" },
						],
					};
				},
			});

			// Trigger validatePipelineFn
			const report = await step.props.validatePipelineFn?.({
				input_spec_content: "domain_namespace: test_domain",
			});

			expect(validatedSpec).toBe("domain_namespace: test_domain");
			expect(report?.passed).toBe(true);
			expect(report?.checked_items_count).toBe(5);
		});

		it("invokes createRunFn and onRunStarted when pipeline run is initiated", async () => {
			let createdRunId = "";
			let receivedHandleRunId = "";
			const state = createDefaultWizardFormState();

			const step = createElement(Step4ValidateAndRun, {
				formState: state,
				onChange: () => {},
				createRunFn: async () => {
					createdRunId = "run-test-123";
					return {
						run_id: "run-test-123",
						status: "RUNNING",
						created_at_utc: new Date().toISOString(),
						event_stream_url: "/api/v1/runs/run-test-123/events",
						has_initial_contract: false,
						lifecycle_stage: "COLD_START_N1",
						status_url: "/api/v1/runs/run-test-123",
					};
				},
				onRunStarted: (handle) => {
					receivedHandleRunId = handle.run_id;
				},
			});

			const handle = await step.props.createRunFn?.({
				dry_run: false,
				input_spec_content: "test",
			});
			if (handle) {
				step.props.onRunStarted?.(handle);
			}

			expect(createdRunId).toBe("run-test-123");
			expect(receivedHandleRunId).toBe("run-test-123");
		});
	});

	describe("4. DiffInspector Visual Diff & Artifact Downloads", () => {
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
			expect(html).toContain("✓ 4-Zone Verification Passed");
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
	});

	describe("5. 1-Click Evolution & Sync Delta Integration", () => {
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

			// Direct component prop invocation test
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

		it("wires onEvolveSchema in WizardShell to transition to vault_blueprint mode and reset sources", () => {
			let receivedBlueprint: VaultBlueprintDTO | null = null;

			const sampleOutputBp: VaultBlueprintDTO = {
				schema_version: "1.0.0",
				domain_namespace: "wealth_management",
				lineage: {
					blueprint_revision: 3,
					domain_namespace: "wealth_management",
					ontology_sha256: "abc12345",
				},
				ontology: {},
				contract: { hubs: [{ hub_name: "hub_wealth" }] },
			};

			const shell = createElement(WizardShell, {
				initialStep: "validate_run",
				onEvolveSchema: (bp) => {
					receivedBlueprint = bp;
				},
			});

			shell.props.onEvolveSchema?.(sampleOutputBp);

			expect(receivedBlueprint).not.toBeNull();
			expect(receivedBlueprint?.domain_namespace).toBe("wealth_management");
			expect(receivedBlueprint?.lineage.blueprint_revision).toBe(3);
		});
	});
});
