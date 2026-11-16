import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { type Page, expect, test } from "@playwright/test";

const REPO_ARTIFACTS_SCREENSHOTS = path.resolve(
	process.cwd(),
	"../../artifacts/screenshots",
);
const LOCAL_E2E_SCREENSHOTS = path.resolve(
	process.cwd(),
	"tests/e2e/screenshots",
);
const DDL_EXPORT_DIR = path.resolve(process.cwd(), "../../artifacts");

async function captureStep(page: Page, filename: string) {
	await page.screenshot({
		path: path.join(REPO_ARTIFACTS_SCREENSHOTS, filename),
		fullPage: true,
	});
	await page.screenshot({
		path: path.join(LOCAL_E2E_SCREENSHOTS, filename),
		fullPage: true,
	});
}

function resolveLocalToken(): string {
	if (process.env.OPENDV_STUDIO_LOCAL_TOKEN) {
		return process.env.OPENDV_STUDIO_LOCAL_TOKEN.trim();
	}
	if (process.env.OPENDV_TOKEN) {
		return process.env.OPENDV_TOKEN.trim();
	}
	const candidates = [
		path.resolve(process.cwd(), ".opendv/ontology_studio/.local_token"),
		path.resolve(process.cwd(), "../../.opendv/ontology_studio/.local_token"),
		path.resolve(process.cwd(), "../.opendv/ontology_studio/.local_token"),
	];
	for (const p of candidates) {
		if (existsSync(p)) {
			try {
				const tok = readFileSync(p, "utf-8").trim();
				if (tok) return tok;
			} catch {}
		}
	}
	return "";
}

let launchNonce: string | null = null;

test.beforeAll(async ({ request }) => {
	mkdirSync(REPO_ARTIFACTS_SCREENSHOTS, { recursive: true });
	mkdirSync(LOCAL_E2E_SCREENSHOTS, { recursive: true });
	mkdirSync(DDL_EXPORT_DIR, { recursive: true });

	let localToken = resolveLocalToken();
	if (!localToken) {
		try {
			const initRes = await request.post("/api/v1/ontology/studio/init", {
				headers: { "X-OpenDV-Workbench": "1" },
			});
			if (initRes.ok()) {
				const initData = (await initRes.json()) as {
					local_token?: string;
					token?: string;
				};
				localToken = (initData.local_token || initData.token || "").trim();
			}
			if (!localToken) {
				localToken = resolveLocalToken();
			}
		} catch {}
	}
	const baseHeaders: Record<string, string> = {
		"X-OpenDV-Role": "INTERNAL_MODELER",
		"X-OpenDV-Workbench": "1",
	};
	if (localToken) {
		baseHeaders.Authorization = `Bearer ${localToken}`;
	}

	try {
		const nonceHeaders: Record<string, string> = {
			...baseHeaders,
			"X-OpenDV-Role": "admin",
			"X-OpenDV-Principal": "human:local_admin",
		};
		const nonceRes = await request.post("/api/v1/ontology/studio/auth/launch-nonce", {
			headers: nonceHeaders,
		});
		console.log("DEBUG mint nonce status:", nonceRes.status());
		if (nonceRes.ok()) {
			const nonceData = (await nonceRes.json()) as { launch_nonce?: string; nonce?: string };
			launchNonce = nonceData.launch_nonce || nonceData.nonce || null;
			console.log("DEBUG minted launch nonce:", launchNonce ? "SUCCESS" : "EMPTY");
		} else {
			console.log("DEBUG mint nonce failed:", await nonceRes.text());
		}
	} catch (e) {
		console.warn("Could not mint launch nonce in beforeAll:", e);
	}

	// M28: Seed Studio session via API in beforeAll on clean checkout
	try {
		const adminHeaders = {
			...baseHeaders,
			"X-OpenDV-Role": "admin",
		};
		await request.delete("/api/v1/ontology/studio/sessions/am-walkthrough?force=true", {
			headers: adminHeaders,
		}).catch(() => {});

		const createRes = await request.post("/api/v1/ontology/studio/sessions", {
			headers: { ...adminHeaders, "Content-Type": "application/json" },
			data: {
				session_id: "am-walkthrough",
				domain_namespace: "asset_management",
				enterprise_owner: "Enterprise Architecture",
				description: "AM Walkthrough Seed Session",
				from_workspace: "apps/opendv-web-workbench/tests/fixtures/am_walkthrough_seed/versions/v1.0.0/ontology.json",
			},
		});
		console.log("DEBUG create session status:", createRes.status());
	} catch (err) {
		console.warn("Studio session seeding via API in beforeAll notice:", err);
	}
});

test.describe("Open-DV 2-Run Progressive Walkthrough (WS6.4: Greenfield + Hybrid Evolution)", () => {
	test("Complete 2-run lifecycle: Studio -> Greenfield SQLite -> Evolve Schema -> Hybrid UC + Docs -> 4-Zone SQL", async ({
		page,
	}) => {
		test.setTimeout(720_000);

		// Fully unmocked inspection against live Platform API with DuckDB Unity Catalog emulator


		// =========================================================================
		// PHASE 1: Cold Start & Launch Ontology Studio
		// =========================================================================
		const targetUrl = launchNonce ? `/ontology#token=${launchNonce}` : "/ontology";
		await page.goto(targetUrl);
		const studioApp = page.getByTestId("ontology-studio-app");
		await expect(studioApp).toBeVisible({ timeout: 20_000 });

		// Ensure am-walkthrough session is selected
		const sessionSelector = page.getByTestId("session-selector");
		await expect(sessionSelector).toBeVisible({ timeout: 15_000 });
		await expect(sessionSelector.locator("option[value='am-walkthrough']")).toHaveCount(1, { timeout: 15_000 });
		if ((await sessionSelector.inputValue()) !== "am-walkthrough") {
			await sessionSelector.selectOption("am-walkthrough");
		}

		await captureStep(page, "01_ontology_studio_initial.png");

		// Step 2: Inspect Sources Panel
		const leftDrawerTabSources = page.getByTestId("nav-tab-sources");
		await expect(leftDrawerTabSources).toBeVisible({ timeout: 10_000 });
		await leftDrawerTabSources.click();
		await expect(page.getByTestId("sources-panel")).toBeVisible({
			timeout: 10_000,
		});
		await captureStep(page, "02_ontology_bootstrapped.png");

		// Step 3: Clarification & Steering Proposal
		const proposalTab = page.getByTestId("right-panel-tab-proposal");
		await expect(proposalTab).toBeVisible({ timeout: 10_000 });
		await proposalTab.click();
		await expect(page.getByTestId("right-panel-proposal-view")).toBeVisible({
			timeout: 10_000,
		});
		await captureStep(page, "03_steering_proposal_applied.png");

		// Step 4: DV 2.0 Blueprint Tab Inspection & Zone 2 DDL Preview
		const blueprintTab = page.getByTestId("right-panel-tab-blueprint");
		await expect(blueprintTab).toBeVisible({ timeout: 10_000 });
		await blueprintTab.click();
		await expect(page.getByTestId("right-panel-blueprint-view")).toBeVisible({
			timeout: 10_000,
		});

		await expect(page.getByTestId("blueprint-ddl-code")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("pill-hub-hub_security")).toBeVisible({
			timeout: 10_000,
		});
		await expect(
			page.getByTestId("pill-link-lnk_investor_holding"),
		).toBeVisible({ timeout: 10_000 });
		await captureStep(page, "04_dv_blueprint_ddl_preview.png");

		// Step 5: 1-Click Hand-Off to Pipeline Builder
		const handoffBtn = page.getByTestId("btn-open-in-pipeline-builder");
		await expect(handoffBtn).toBeVisible({ timeout: 10_000 });
		await handoffBtn.click();

		const wizardShell = page.getByTestId("wizard-shell");
		await expect(wizardShell).toBeVisible({ timeout: 20_000 });
		const pipelineBuilderView = page.getByTestId("pipeline-builder-view");
		await expect(pipelineBuilderView).toBeAttached();

		expect(page.url()).toContain("source=ontology_studio");
		expect(page.url()).toContain("domain_namespace=asset_management");
		await captureStep(page, "05_pipeline_builder_prefilled.png");

		// =========================================================================
		// PHASE 2: RUN 1 — Greenfield Pipeline Build (Public AM SQLite)
		// =========================================================================
		// Step 1: Foundation
		await expect(page.getByTestId("wizard-step-foundation")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("inferred-mode-banner")).toContainText(
			"Greenfield Future-Proof Design",
		);
		await expect(page.getByTestId("inferred-plan-badge")).toContainText(
			"COLD_START_N1",
		);

		await page.getByTestId("btn-wizard-next").click();

		// Step 2: Connectors & SQLite Picker
		await expect(page.getByTestId("wizard-step-connectors")).toBeVisible({
			timeout: 10_000,
		});
		const sqliteCard = page.getByTestId("connector-card-sqlite");
		await expect(sqliteCard).toBeVisible({ timeout: 10_000 });
		await sqliteCard.click();

		// CatalogTablePickerModal
		const pickerModal = page.getByTestId("catalog-table-picker-modal");
		await expect(pickerModal).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId("picker-adapter-badge")).toContainText(
			"SQLITE",
		);

		// Enter URI for public SQLite dataset if not already pre-filled
		const uriInput = page.getByTestId("input-picker-uri");
		if ((await uriInput.inputValue()) !== "data/public_am/am_sec_treasury_ibor.sqlite") {
			await uriInput.fill("data/public_am/am_sec_treasury_ibor.sqlite");
		}

		// Wait for inspection to finish and tables to be rendered
		const expandSecBtn = page.getByTestId("btn-expand-table-securities");
		await expect(expandSecBtn).toBeVisible({ timeout: 15_000 });

		// Select all tables and inspect PK/FK
		const selectAllCb = page.getByTestId("checkbox-select-all-tables");
		await expect(selectAllCb).toBeVisible({ timeout: 10_000 });
		if (!(await selectAllCb.isChecked())) {
			await selectAllCb.check();
		}

		// Expand securities table to assert columns and PK/FK badges
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

		// Add selected tables
		const applyBtn = page.getByTestId("btn-add-selected-tables");
		await expect(applyBtn).toBeEnabled({ timeout: 10_000 });
		await applyBtn.click();
		await expect(pickerModal).not.toBeVisible({ timeout: 10_000 });

		await expect(page.getByTestId("configured-sources-count")).toContainText(
			"1 sources configured",
		);
		await captureStep(page, "06_run1_step2_sqlite_configured.png");

		await page.getByTestId("btn-wizard-next").click();

		// Step 3: Governance & Dialect
		await expect(page.getByTestId("wizard-step-governance")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("select-agent-model")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("checkbox-dialect-duckdb")).toBeChecked();

		// Enable Databricks dialect & auto-approve hold policy
		const dbxDialectCb = page.getByTestId("checkbox-dialect-databricks");
		await expect(dbxDialectCb).toBeVisible({ timeout: 10_000 });
		await dbxDialectCb.check();
		const autoApproveHoldRadio1 = page.getByTestId("radio-hold-auto-approve");
		await expect(autoApproveHoldRadio1).toBeVisible({ timeout: 10_000 });
		await autoApproveHoldRadio1.check();
		await captureStep(page, "07_run1_step3_governance.png");

		await page.getByTestId("btn-wizard-next").click();

		// Step 4: Validate & Run 1
		await expect(page.getByTestId("wizard-step-validate-run")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("validation-status")).toContainText(
			"ALL CHECKS PASSED",
		);

		const runPipelineBtn = page.getByTestId("btn-run-pipeline");
		await expect(runPipelineBtn).toBeVisible({ timeout: 10_000 });
		await runPipelineBtn.click();

		// Wait for Run 1 completion
		const runStartedBadge = page.getByTestId("run-started-badge");
		await expect(runStartedBadge).toBeVisible({ timeout: 10_000 });
		await expect(runStartedBadge).toContainText("Completed", {
			timeout: 360_000,
		});

		// DiffInspector assertions
		const diffInspector = page.getByTestId("diff-inspector");
		await expect(diffInspector).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId("scorecard-gates-badge")).toContainText(
			"Gate G0-G5 Passed",
		);
		await expect(page.getByTestId("scorecard-shadow-badge")).toContainText(
			"4-Zone Verification Passed",
		);
		const addedHubsBox = page.getByTestId("diff-added-hubs");
		await expect(addedHubsBox).toBeVisible({ timeout: 10_000 });
		const addedHubsText = await addedHubsBox.innerText();
		expect(addedHubsText).toMatch(/[1-9]\d*/);

		const addedLinksBox = page.getByTestId("diff-added-links");
		await expect(addedLinksBox).toBeVisible({ timeout: 10_000 });
		const addedLinksText = await addedLinksBox.innerText();
		expect(addedLinksText).toMatch(/[1-9]\d*/);

		const addedSatsBox = page.getByTestId("diff-added-satellites");
		await expect(addedSatsBox).toBeVisible({ timeout: 10_000 });
		const addedSatsText = await addedSatsBox.innerText();
		expect(addedSatsText).toMatch(/[1-9]\d*/);
		await captureStep(page, "08_run1_execution_diff_complete.png");

		// =========================================================================
		// PHASE 3: RUN 2 — Hybrid Evolution (Drifted SQLite + Databricks UC + Docs)
		// =========================================================================
		// 1-Click "Evolve Schema with New Data ->"
		const evolveBtn = page.getByTestId("btn-evolve-schema");
		await expect(evolveBtn).toBeVisible({ timeout: 10_000 });
		await expect(evolveBtn).toBeEnabled({ timeout: 10_000 });
		await evolveBtn.click();

		// Navigated back to Step 1 in Evolution Mode
		await expect(page.getByTestId("wizard-step-foundation")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("inferred-mode-banner")).toContainText(
			"Existing Schema Evolution",
		);
		await expect(page.getByTestId("inferred-plan-badge")).toContainText(
			"VERIFIED_NO_OP",
		);
		await captureStep(page, "09_run2_step1_evolution_mode.png");

		await page.getByTestId("btn-wizard-next").click();

		// Step 2: Add Hybrid Multi-Source Feeds
		await expect(page.getByTestId("wizard-step-connectors")).toBeVisible({
			timeout: 10_000,
		});

		// Source 1: SQLite feed
		await page.getByTestId("connector-card-sqlite").click();
		await expect(page.getByTestId("catalog-table-picker-modal")).toBeVisible({
			timeout: 10_000,
		});
		const sqliteUriInput = page.getByTestId("input-picker-uri");
		await sqliteUriInput.fill("data/public_am/am_sec_treasury_ibor.sqlite");
		const expandSecBtnRun2 = page.getByTestId("btn-expand-table-securities");
		await expect(expandSecBtnRun2).toBeVisible({ timeout: 15_000 });
		const selectAllCbRun2Sqlite = page.getByTestId("checkbox-select-all-tables");
		await expect(selectAllCbRun2Sqlite).toBeVisible({ timeout: 15_000 });
		if (!(await selectAllCbRun2Sqlite.isChecked())) {
			await selectAllCbRun2Sqlite.check();
		}
		await expect(page.getByTestId("btn-add-selected-tables")).toBeEnabled({
			timeout: 10_000,
		});
		await page.getByTestId("btn-add-selected-tables").click();

		// Source 2: Databricks Unity Catalog Emulator
		await page.getByTestId("connector-card-databricks").click();
		await expect(page.getByTestId("catalog-table-picker-modal")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("picker-adapter-badge")).toContainText(
			"DATABRICKS UC",
		);
		const dbxUriInput = page.getByTestId("input-picker-uri");
		await dbxUriInput.fill("uc://am_prod");
		await page.getByTestId("input-picker-catalog").fill("am_prod");
		await page.getByTestId("input-picker-schema").fill("ibor");
		const expandPositionsBtn = page.getByTestId(
			"btn-expand-table-positions_snapshot",
		);
		await expect(expandPositionsBtn).toBeVisible({ timeout: 15_000 });
		await expandPositionsBtn.click();
		await expect(page.getByTestId("tag-mnpi")).toBeVisible({ timeout: 10_000 });
		const selectAllCbRun2Dbx = page.getByTestId("checkbox-select-all-tables");
		if (!(await selectAllCbRun2Dbx.isChecked())) {
			await selectAllCbRun2Dbx.check();
		}
		const tradesRowCb = page.getByTestId("checkbox-table-trades");
		if ((await tradesRowCb.count()) > 0 && (await tradesRowCb.isChecked())) {
			await tradesRowCb.uncheck();
		}
		await expect(page.getByTestId("btn-add-selected-tables")).toBeEnabled({
			timeout: 10_000,
		});
		await page.getByTestId("btn-add-selected-tables").click();

		// Source 3: Regulatory Text / Unstructured Material
		await page
			.getByTestId("input-doc-title")
			.fill("SEC Form 13F & BCBS-239 Risk Framework");
		await page.getByTestId("select-doc-type").selectOption("inline_note");
		await page
			.getByTestId("textarea-doc-content")
			.fill(
				"Regulatory requirements mandate tracking beneficial ownership (>5%) and portfolio exposure concentration.",
			);
		await page.getByTestId("btn-add-document").click();

		await expect(page.getByTestId("configured-sources-count")).toContainText(
			"2 sources configured",
		);
		await captureStep(page, "10_run2_step2_hybrid_sources_configured.png");

		await page.getByTestId("btn-wizard-next").click();

		// Step 3: Governance (Ensure Databricks Delta enabled)
		await expect(page.getByTestId("wizard-step-governance")).toBeVisible({
			timeout: 10_000,
		});
		if (!(await page.getByTestId("checkbox-dialect-databricks").isChecked())) {
			await page.getByTestId("checkbox-dialect-databricks").check();
		}
		const autoApproveHoldRadio2 = page.getByTestId("radio-hold-auto-approve");
		await expect(autoApproveHoldRadio2).toBeVisible({ timeout: 10_000 });
		await autoApproveHoldRadio2.check();
		await captureStep(page, "11_run2_step3_governance.png");

		await page.getByTestId("btn-wizard-next").click();

		// Step 4: Validate & Run 2 (Hybrid Per-Source Plan)
		await expect(page.getByTestId("wizard-step-validate-run")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("inferred-action-plan-card")).toContainText(
			"HYBRID_PER_SOURCE",
		);

		await page.getByTestId("btn-run-pipeline").click();

		// Wait for Run 2 completion
		await expect(page.getByTestId("run-started-badge")).toContainText(
			"Completed",
			{ timeout: 360_000 },
		);

		// Verify Evolution Diff, Drift Repairs, and Deltas
		const diffInspectorRun2 = page.getByTestId("diff-inspector");
		await expect(diffInspectorRun2).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId("diff-drift-repairs")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("diff-ontology-deltas")).toBeVisible({
			timeout: 10_000,
		});

		// Sync Ontology Delta to Studio Session
		const syncDeltaBtn = page.getByTestId("btn-sync-ontology-delta");
		await expect(syncDeltaBtn).toBeVisible({ timeout: 10_000 });
		await syncDeltaBtn.click();
		await expect(page.getByTestId("sync-delta-toast")).toBeVisible({
			timeout: 10_000,
		});

		// Verify Artifact Download Triggers & Content (M28)
		const [duckDownload] = await Promise.all([
			page.waitForEvent("download"),
			page.getByTestId("btn-download-duckdb-sql").click(),
		]);
		const duckStream = await duckDownload.createReadStream();
		const duckChunks: Buffer[] = [];
		if (duckStream) {
			for await (const chunk of duckStream) {
				duckChunks.push(Buffer.from(chunk));
			}
		}
		const duckSql = Buffer.concat(duckChunks).toString("utf-8");
		expect(duckSql).toContain("raw_vault");

		// Export downloaded DuckDB 4-Zone SQL directly to final_four_zone_am.sql
		writeFileSync(
			path.join(DDL_EXPORT_DIR, "final_four_zone_am.sql"),
			duckSql,
			"utf-8",
		);

		const [dbxDownload] = await Promise.all([
			page.waitForEvent("download"),
			page.getByTestId("btn-download-databricks-sql").click(),
		]);
		const dbxStream = await dbxDownload.createReadStream();
		const dbxChunks: Buffer[] = [];
		if (dbxStream) {
			for await (const chunk of dbxStream) {
				dbxChunks.push(Buffer.from(chunk));
			}
		}
		const dbxSql = Buffer.concat(dbxChunks).toString("utf-8");
		expect(dbxSql).toContain("USING DELTA");
		expect(dbxSql).toContain("MERGE INTO");

		// Export downloaded Databricks Delta SQL
		writeFileSync(
			path.join(DDL_EXPORT_DIR, "final_databricks_delta_am.sql"),
			dbxSql,
			"utf-8",
		);

		await expect(page.getByTestId("btn-download-blueprint")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByTestId("btn-download-contract")).toBeVisible({
			timeout: 10_000,
		});
		await captureStep(page, "12_run2_hybrid_evolution_diff_complete.png");

		// =========================================================================
		// PHASE 4: Export Genuine 4-Zone DDL Artifact
		// =========================================================================
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

		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS staging");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS raw_vault");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS business_vault");
		expect(ddlContent).toContain("CREATE SCHEMA IF NOT EXISTS marts");

		// Assert key tables
		expect(ddlContent).toContain("hub_security");
		expect(ddlContent).toContain("lnk_investor_holding");
		expect(ddlContent).toContain("sat_rate_observation");

		// Do not overwrite final_four_zone_am.sql: retain genuine downloaded DuckDB SQL from Run 2 (ORIGINAL_REQUEST lines 375 & 452)
		await captureStep(page, "13_final_multi_dialect_ddl_exported.png");

		// Maintain legacy screenshot filenames in artifacts/screenshots for backward compatibility
		copyFileSync(
			path.join(
				REPO_ARTIFACTS_SCREENSHOTS,
				"06_run1_step2_sqlite_configured.png",
			),
			path.join(REPO_ARTIFACTS_SCREENSHOTS, "06_pipeline_spec_validated.png"),
		);
		copyFileSync(
			path.join(
				REPO_ARTIFACTS_SCREENSHOTS,
				"08_run1_execution_diff_complete.png",
			),
			path.join(
				REPO_ARTIFACTS_SCREENSHOTS,
				"07_pipeline_execution_completed.png",
			),
		);
		copyFileSync(
			path.join(
				REPO_ARTIFACTS_SCREENSHOTS,
				"13_final_multi_dialect_ddl_exported.png",
			),
			path.join(REPO_ARTIFACTS_SCREENSHOTS, "08_final_ddl_exported.png"),
		);
	});
});
