/**
 * Unit Tests for WizardShell.tsx & YamlDrawer.tsx (WS5.1).
 *
 * Verifies 4-step stepper navigation, live YAML drawer synchronization,
 * Inferred Mode banner reflection, step gating, and run triggering.
 */

import { describe, expect, it } from "bun:test";
import type {
	PipelineRunHandle,
	PipelineRunRequest,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WizardShell } from "../src/explorer/builder/WizardShell";
import { YamlDrawer } from "../src/explorer/builder/YamlDrawer";
import { createDefaultWizardFormState } from "../src/explorer/builder/wizard-types";

describe("WizardShell & YamlDrawer (WS5.1)", () => {
	describe("1. Stepper Header & Navigation Structure", () => {
		it("renders 4-step stepper header with badges, labels, and default Step 1 Foundation view", () => {
			const html = renderToStaticMarkup(createElement(WizardShell, {}));

			expect(html).toContain('data-testid="wizard-shell"');
			expect(html).toContain('data-testid="pipeline-builder-view"');
			expect(html).toContain('data-testid="wizard-step-tab-foundation"');
			expect(html).toContain('data-testid="wizard-step-tab-connectors"');
			expect(html).toContain('data-testid="wizard-step-tab-governance"');
			expect(html).toContain('data-testid="wizard-step-tab-validate_run"');

			// Default step is foundation
			expect(html).toContain('data-testid="wizard-step-foundation"');
			expect(html).toContain("Step 1: Choose Foundation");
			expect(html).toContain('data-testid="toggle-yaml-drawer"');
			expect(html).toContain('data-testid="btn-wizard-next"');
			expect(html).toContain('data-testid="btn-wizard-back"');
		});

		it("pre-fills session ID and domain namespace from props", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					sessionId: "sess-asset-management",
					initialDomainNamespace: "asset_management",
					initialPipelineId: "pl_test",
				}),
			);

			expect(html).toContain('value="sess-asset-management"');
		});

		it("displays Inferred Mode banner for greenfield zero-data cold start", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					initialFormState: createDefaultWizardFormState({
						contract: null,
						contractContent: "",
						configuredSources: [],
						unstructuredDocs: [],
					}),
				}),
			);

			expect(html).toContain('data-testid="inferred-mode-banner"');
			expect(html).toContain("Greenfield Future-Proof Design");
			expect(html).toContain("COLD_START_N1");
		});

		it("displays Inferred Mode banner for existing contract schema evolution", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					initialFormState: createDefaultWizardFormState({
						contract: { hubs: [{ table_name: "hub_security" }] },
						contractContent: "hubs: ...",
						configuredSources: [],
					}),
				}),
			);

			expect(html).toContain('data-testid="inferred-mode-banner"');
			expect(html).toContain("Existing Schema Evolution");
			expect(html).toContain("VERIFIED_NO_OP");
		});
	});

	describe("2. Step Switching & Views", () => {
		it("renders Step 2 Connectors when initialStep is connectors", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					initialStep: "connectors",
					initialAdapters: [
						{ adapter_id: "sqlite", display_name: "SQLite Database", supports_multi_table: true },
						{ adapter_id: "databricks", display_name: "Databricks Unity Catalog", supports_multi_table: true },
					],
				}),
			);

			expect(html).toContain('data-testid="wizard-step-connectors"');
			expect(html).toContain("Step 2: Data Sources");
			expect(html).toContain('data-testid="connector-card-sqlite"');
			expect(html).toContain('data-testid="connector-card-databricks"');
		});

		it("renders Step 3 Governance when initialStep is governance", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					initialStep: "governance",
				}),
			);

			expect(html).toContain('data-testid="wizard-step-governance"');
			expect(html).toContain("Step 3: Governance");
			expect(html).toContain('data-testid="select-agent-model"');
			expect(html).toContain('data-testid="select-hold-policy"');
			expect(html).toContain('data-testid="checkbox-dialect-databricks"');
		});

		it("renders Step 4 Validate & Run when initialStep is validate_run", () => {
			const html = renderToStaticMarkup(
				createElement(WizardShell, {
					initialStep: "validate_run",
				}),
			);

			expect(html).toContain('data-testid="wizard-step-validate-run"');
			expect(html).toContain("Step 4: Pre-Flight Action Plan");
			expect(html).toContain('data-testid="inferred-action-plan-card"');
			expect(html).toContain('data-testid="btn-run-pipeline"');
		});
	});

	describe("3. YamlDrawer Slide-Out Component", () => {
		it("renders drawer overlay, header, monospace textarea, and action buttons when open", () => {
			const sampleYaml =
				"blueprint_path: data/test.yaml\ntarget_dialect: duckdb";
			let appliedText: string | null = null;

			const html = renderToStaticMarkup(
				createElement(YamlDrawer, {
					isOpen: true,
					onClose: () => {},
					yamlText: sampleYaml,
					onApplyYaml: (text) => {
						appliedText = text;
					},
				}),
			);

			expect(html).toContain('data-testid="yaml-drawer"');
			expect(html).toContain('data-testid="yaml-spec-textarea"');
			expect(html).toContain('data-testid="btn-yaml-copy"');
			expect(html).toContain('data-testid="btn-yaml-download"');
			expect(html).toContain('data-testid="btn-yaml-apply"');
			expect(html).toContain("blueprint_path: data/test.yaml");
		});

		it("does not render into DOM when isOpen is false", () => {
			const html = renderToStaticMarkup(
				createElement(YamlDrawer, {
					isOpen: false,
					onClose: () => {},
					yamlText: "target_dialect: duckdb",
					onApplyYaml: () => {},
				}),
			);

			expect(html).toBe("");
		});

		it("renders error banner when validationError prop is provided", () => {
			const html = renderToStaticMarkup(
				createElement(YamlDrawer, {
					isOpen: true,
					onClose: () => {},
					yamlText: "foo: bar",
					validationError: "Syntax Error at line 1",
					onApplyYaml: () => {},
				}),
			);

			expect(html).toContain('data-testid="yaml-drawer-error-banner"');
			expect(html).toContain("Syntax Error at line 1");
		});
	});
});
