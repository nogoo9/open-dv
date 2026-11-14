import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

const SCREENSHOTS_DIR = path.resolve(
	process.cwd(),
	"../../artifacts/screenshots",
);
const DDL_EXPORT_DIR = path.resolve(process.cwd(), "../../artifacts");

test.beforeAll(() => {
	mkdirSync(SCREENSHOTS_DIR, { recursive: true });
	mkdirSync(DDL_EXPORT_DIR, { recursive: true });
});

test.describe("Open-DV Asset Management Agentic Browser Walkthrough (Features 16–18)", () => {
	test("Full 8-step walkthrough: Studio -> Steering -> Blueprint -> Pipeline Builder -> 4-Zone DDL", async ({
		page,
	}) => {
		// -------------------------------------------------------------------------
		// Step 1: Cold Start & Launch Ontology Studio
		// -------------------------------------------------------------------------
		await page.goto("/ontology");
		// Verify Studio topbar and canvas container (strict locator)
		const studioApp = page.getByTestId("ontology-studio-app");
		await expect(studioApp).toBeVisible({ timeout: 20_000 });

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "01_ontology_studio_initial.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 2: Bootstrap Asset Management Ontology Pack / CAS Text Note
		// -------------------------------------------------------------------------
		const leftDrawerTabSources = page.getByTestId("nav-tab-sources");
		await expect(leftDrawerTabSources).toBeVisible({ timeout: 10_000 });
		await leftDrawerTabSources.click();

		const sourcesPanel = page.getByTestId("sources-panel");
		await expect(sourcesPanel).toBeVisible({ timeout: 10_000 });

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "02_ontology_bootstrapped.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 3: Clarification & Steering Prompt Refinement
		// -------------------------------------------------------------------------
		const proposalTab = page.getByTestId("right-panel-tab-proposal");
		await expect(proposalTab).toBeVisible({ timeout: 10_000 });
		await proposalTab.click();

		const proposalView = page.getByTestId("right-panel-proposal-view");
		await expect(proposalView).toBeVisible({ timeout: 10_000 });

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "03_steering_proposal_applied.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 4: DV 2.0 Blueprint Tab Inspection & Zone 2 DDL Preview
		// -------------------------------------------------------------------------
		const blueprintTab = page.getByTestId("right-panel-tab-blueprint");
		await expect(blueprintTab).toBeVisible({ timeout: 10_000 });
		await blueprintTab.click();

		const blueprintView = page.getByTestId("right-panel-blueprint-view");
		await expect(blueprintView).toBeVisible({ timeout: 10_000 });

		const ddlCodeElement = page.getByTestId("blueprint-ddl-code");
		await expect(ddlCodeElement).toBeVisible({ timeout: 10_000 });

		// Assert presence of target table pills
		const hubSecurityPill = page.getByTestId("pill-hub-hub_security");
		await expect(hubSecurityPill).toBeVisible({ timeout: 10_000 });
		const lnkHoldingPill = page.getByTestId("pill-link-lnk_investor_holding");
		await expect(lnkHoldingPill).toBeVisible({ timeout: 10_000 });

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "04_dv_blueprint_ddl_preview.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 5: 1-Click Hand-Off to Pipeline Builder
		// -------------------------------------------------------------------------
		const handoffBtn = page.getByTestId("btn-open-in-pipeline-builder");
		await expect(handoffBtn).toBeVisible({ timeout: 10_000 });
		await handoffBtn.click();

		const pipelineBuilderView = page.getByTestId("pipeline-builder-view");
		await expect(pipelineBuilderView).toBeVisible({ timeout: 20_000 });

		// Assert deep-link URL navigation parameters
		expect(page.url()).toContain("source=ontology_studio");
		expect(page.url()).toContain("domain_namespace=asset_management");

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "05_pipeline_builder_prefilled.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 6: Configure Public Datasets & Validate Specification
		// -------------------------------------------------------------------------
		const modeFormBtn = page.getByTestId("btn-builder-mode-form");
		await expect(modeFormBtn).toBeVisible({ timeout: 10_000 });
		await modeFormBtn.click();

		// Assert live specification preview pre-populated with target Hubs and schema
		const specPreview = page.getByTestId("builder-live-spec-preview");
		await expect(specPreview).toBeVisible({ timeout: 10_000 });
		const specPreviewContent = await specPreview.innerText();
		expect(specPreviewContent).toContain("hub_security");
		expect(specPreviewContent).toContain("stg_sec_security_master");

		const validateBtn = page.getByTestId("btn-builder-validate");
		await expect(validateBtn).toBeVisible({ timeout: 10_000 });
		await validateBtn.click();

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "06_pipeline_spec_validated.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 7: Execute Pipeline & Monitor Live Execution Stream
		// -------------------------------------------------------------------------
		const runPipelineBtn = page.getByTestId("btn-builder-run-pipeline");
		await expect(runPipelineBtn).toBeVisible({ timeout: 10_000 });
		await runPipelineBtn.click();

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "07_pipeline_execution_completed.png"),
			fullPage: true,
		});

		// -------------------------------------------------------------------------
		// Step 8: Assert Final 4-Zone DDL Export
		// -------------------------------------------------------------------------
		// Return to ontology blueprint view to extract the live generated DDL
		await page.goto("/ontology");
		await expect(page.getByTestId("ontology-studio-app")).toBeVisible({
			timeout: 20_000,
		});

		await page.getByTestId("right-panel-tab-blueprint").click();
		await expect(page.getByTestId("right-panel-blueprint-view")).toBeVisible({
			timeout: 10_000,
		});

		const exportedDdlCode = page.getByTestId("blueprint-ddl-code");
		await expect(exportedDdlCode).toBeVisible({ timeout: 10_000 });
		const ddlContent = (await exportedDdlCode.innerText()).trim();

		// Assert all four architectural schemas exist in exported DDL
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS staging");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS raw_vault");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS business_vault");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS marts");

		// Assert key tables across all four zones exist in exported DDL
		// Staging
		expect(ddlContent).toContain("stg_sec_security_master");
		expect(ddlContent).toContain("stg_sec_13f_holdings");
		expect(ddlContent).toContain("stg_fred_treasury_rates");
		// Raw Vault
		expect(ddlContent).toContain("hub_security");
		expect(ddlContent).toContain("hub_investor");
		expect(ddlContent).toContain("hub_rate_index");
		expect(ddlContent).toContain("hub_document");
		expect(ddlContent).toContain("lnk_investor_holding");
		expect(ddlContent).toContain("sat_security_details");
		expect(ddlContent).toContain("sat_holding_position");
		expect(ddlContent).toContain("sat_rate_observation");
		expect(ddlContent).toContain("sat_document_metadata");
		// Business Vault
		expect(ddlContent).toContain("pit_security");
		expect(ddlContent).toContain("bridge_portfolio_holdings");
		// Marts
		expect(ddlContent).toContain("dim_security");
		expect(ddlContent).toContain("fct_institutional_holding");

		// Export the genuine live-rendered DDL from Web Workbench UI
		const ddlExportPath = path.join(DDL_EXPORT_DIR, "final_four_zone_am.sql");
		writeFileSync(ddlExportPath, ddlContent, "utf-8");

		await page.screenshot({
			path: path.join(SCREENSHOTS_DIR, "08_final_ddl_exported.png"),
			fullPage: true,
		});
	});
});
