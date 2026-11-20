import { mkdirSync } from "node:fs";
import path from "node:path";
import { type Page, expect, test } from "@playwright/test";

const LOCAL_E2E_SCREENSHOTS = path.resolve(
	process.cwd(),
	"tests/e2e/screenshots",
);

async function captureStep(page: Page, filename: string) {
	await page.screenshot({
		path: path.join(LOCAL_E2E_SCREENSHOTS, filename),
		fullPage: true,
	});
}

test.beforeAll(async ({ request }) => {
	mkdirSync(LOCAL_E2E_SCREENSHOTS, { recursive: true });

	await request
		.delete("/api/v1/ontology/studio/sessions/am-walkthrough?force=true")
		.catch(() => {});

	const createRes = await request.post("/api/v1/ontology/studio/sessions", {
		headers: { "Content-Type": "application/json" },
		data: {
			session_id: "am-walkthrough",
			domain_namespace: "asset_management",
			enterprise_owner: "Enterprise Architecture",
			description: "AM Walkthrough Seed Session",
			from_workspace:
				"apps/opendv-web-workbench/tests/fixtures/am_walkthrough_seed/versions/v1.0.0/ontology.json",
		},
	});
	expect([200, 201]).toContain(createRes.status());
});

test.describe("Open-DV Unified 6-Step Wizard & 4-Tab Workbench Walkthrough (D19)", () => {
	test("End-to-end 6-step wizard + DV Schema Explorer + 4-tab navigation", async ({
		page,
	}) => {
		test.setTimeout(300_000);

		// 1. Verify /builder routes to unified OntologyWizardShell (D19)
		await page.goto("/builder");
		await expect(page.getByTestId("ontology-wizard-shell")).toBeVisible({
			timeout: 20_000,
		});

		// 2. Navigate to /ontology and verify 4-tab header + 6-step stepper
		await page.goto("/ontology");
		await expect(page.getByTestId("ontology-wizard-shell")).toBeVisible({
			timeout: 20_000,
		});
		await expect(page.getByTestId("nav-tab-ontology")).toBeVisible();
		await expect(page.getByTestId("nav-tab-dv-schema")).toBeVisible();
		await expect(page.getByTestId("nav-tab-explorer")).toBeVisible();
		await expect(page.getByTestId("nav-tab-settings")).toBeVisible();
		for (let s = 1; s <= 6; s += 1) {
			await expect(
				page.getByTestId(`wizard-stepper-step-${s}`),
			).toBeVisible();
		}

		// Step 1: Select seeded v2 session am-walkthrough
		await expect(page.getByTestId("wizard-step-1")).toBeVisible({
			timeout: 15_000,
		});
		await page.getByTestId("btn-mode-evolve-existing").click();
		const selectWalkthroughBtn = page.getByTestId(
			"btn-select-session-am-walkthrough",
		);
		await expect(selectWalkthroughBtn).toBeVisible({ timeout: 15_000 });
		await captureStep(page, "01_wizard_step1_baseline.png");
		await selectWalkthroughBtn.click();

		// Step 2: Attach SQLite & Databricks UC sources + inline domain note
		await expect(page.getByTestId("wizard-step-2")).toBeVisible({
			timeout: 15_000,
		});
		await page.getByTestId("connector-tile-sqlite").click();
		const pickerModal = page.getByTestId("catalog-table-picker-modal");
		await expect(pickerModal).toBeVisible({ timeout: 10_000 });
		await page
			.getByTestId("input-picker-uri")
			.fill("data/public_am/am_sec_treasury_ibor.sqlite");
		const expandSecBtn = page.getByTestId("btn-expand-table-securities");
		await expect(expandSecBtn).toBeVisible({ timeout: 15_000 });
		const selectAllCb = page.getByTestId("checkbox-select-all-tables");
		if (!(await selectAllCb.isChecked())) {
			await selectAllCb.check();
		}
		await expandSecBtn.click();
		await expect(page.getByTestId("columns-table-securities")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("pk-badge-ticker")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("fk-badge-cik")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("btn-add-selected-tables")).toBeEnabled();
		await page.getByTestId("btn-add-selected-tables").click();
		await expect(pickerModal).not.toBeVisible({ timeout: 10_000 });

		await page
			.getByTestId("inline-domain-note-textarea")
			.fill(
				"Regulatory policy: track beneficial ownership and treasury rate benchmarks.",
			);
		await page.getByTestId("btn-add-inline-note").click();
		await captureStep(page, "02_wizard_step2_sources.png");

		// Step 3: Preview Evidence (Structured Tables & Parsed Chunks)
		await page.getByTestId("wizard-stepper-step-3").click();
		await expect(page.getByTestId("wizard-step-3")).toBeVisible({
			timeout: 15_000,
		});
		await page.getByTestId("tab-step3-chunks").click();
		await page.getByTestId("tab-step3-structured").click();
		await captureStep(page, "03_wizard_step3_evidence.png");

		// Step 4: Agent Discovery (or fail-closed 422 setup gate in keyless CI)
		await page.getByTestId("wizard-stepper-step-4").click();
		await expect(
			page
				.getByTestId("wizard-step-4")
				.or(page.getByTestId("step4-agent-setup-gate")),
		).toBeVisible({ timeout: 15_000 });
		await captureStep(page, "04_wizard_step4_discovery.png");

		// Step 5: Ontology Diff, Graph Canvas & Export/Commit
		await page.getByTestId("wizard-stepper-step-5").click();
		await expect(page.getByTestId("wizard-step-5")).toBeVisible({
			timeout: 15_000,
		});
		await captureStep(page, "05_wizard_step5_graph.png");

		// Step 6: 5-Zone Data Vault 2.0 Build & Run
		await page.getByTestId("wizard-stepper-step-6").click();
		await expect(page.getByTestId("wizard-step-6")).toBeVisible({
			timeout: 15_000,
		});
		await expect(page.getByTestId("step6-pipeline-console")).toBeVisible();
		await expect(page.getByTestId("step6-gate-badges")).toBeVisible();
		await expect(page.getByTestId("step6-sql-preview-card")).toBeVisible();

		const startPipelineBtn = page.getByTestId("btn-start-pipeline-run");
		await expect(startPipelineBtn).toBeEnabled({ timeout: 15_000 });
		await startPipelineBtn.click();

		// Wait for run completion (handle Steward Hold modal if paused)
		const approveHoldBtn = page.getByRole("button", { name: /approve/i });
		for (let i = 0; i < 60; i += 1) {
			if (await approveHoldBtn.isVisible().catch(() => false)) {
				await approveHoldBtn.click();
			}
			const btnText = await startPipelineBtn.innerText().catch(() => "");
			if (btnText.includes("Run 5-Zone Vault Pipeline")) {
				const consoleText = await page
					.getByTestId("step6-pipeline-console")
					.innerText();
				if (!consoleText.includes("PENDING")) {
					break;
				}
			}
			await page.waitForTimeout(500);
		}
		await expect(startPipelineBtn).toBeEnabled({ timeout: 120_000 });
		await expect(page.getByTestId("step6-sql-preview-pre")).toBeVisible();
		await captureStep(page, "06_wizard_step6_pipeline.png");

		// Navigate to DV Schema Explorer via Step 6 CTA
		await page.getByTestId("btn-explore-dv-schema").click();
		await expect(page.getByTestId("dv-schema-explorer-view")).toBeVisible({
			timeout: 15_000,
		});
		await page.getByTestId("dv-schema-tab-triage").click();
		await page.getByTestId("dv-schema-tab-rows").click();
		await page.getByTestId("dv-schema-tab-sql").click();
		await page.getByTestId("dv-schema-tab-graph").click();
		await captureStep(page, "07_dv_schema_explorer.png");

		// Verify remaining 2 top-bar tabs (Run Explorer & Settings)
		await page.getByTestId("nav-tab-explorer").click();
		await expect(page.getByTestId("pipeline-overview-view")).toBeVisible({
			timeout: 15_000,
		});
		await page.getByTestId("nav-tab-settings").click();
		await expect(page.getByTestId("platform-settings-view")).toBeVisible({
			timeout: 15_000,
		});
		await captureStep(page, "08_settings_view.png");
	});
});
