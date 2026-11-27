import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type Page, type TestInfo, expect, test } from "@playwright/test";
import { FORBIDDEN_GUIDED_JARGON } from "../src/api/openapi";

const publicAmDir = resolve(
	dirname(fileURLToPath(import.meta.url)),
	"../../data/public_am",
);

const STRICT_DV_JARGON =
	/\b(Hub|Link|Satellite|PIT|SAL|HK|BKCC|CEGAR|AP-\d+|Graph\s+A|Graph\s+B|Vault\s+IR)\b/i;

const EXPECTED_FONTS = [
	"dm-sans-latin.woff2",
	"instrument-serif-latin.woff2",
	"instrument-serif-italic-latin.woff2",
	"jetbrains-mono-latin.woff2",
];

type AllowedFailure = {
	urlPattern: RegExp;
	statuses: number[];
};

interface TelemetryGuard {
	requests: { url: string; method: string }[];
	fontStatuses: Record<string, number>;
	consoleErrors: string[];
	pageErrors: string[];
	unexpectedResponses: { url: string; status: number }[];
	allowFailure<T>(
		urlPattern: RegExp,
		statuses: number[],
		fn: () => Promise<T>,
	): Promise<T>;
	assertClean(requireAllFonts?: boolean): void;
}

function attachGuards(page: Page): TelemetryGuard {
	const requests: { url: string; method: string }[] = [];
	const fontStatuses: Record<string, number> = {};
	const consoleErrors: string[] = [];
	const pageErrors: string[] = [];
	const unexpectedResponses: { url: string; status: number }[] = [];
	const activeAllowlist: AllowedFailure[] = [];

	page.on("console", (msg) => {
		if (msg.type() === "error") {
			const text = msg.text();
			const expectedResourceErr =
				text.startsWith("Failed to load resource:") &&
				activeAllowlist.some((rule) =>
					rule.statuses.some((s) => text.includes(String(s))),
				);
			if (!expectedResourceErr) {
				consoleErrors.push(text);
			}
		}
	});
	page.on("pageerror", (err) => {
		pageErrors.push(err.message);
	});
	page.on("request", (req) => {
		const url = req.url();
		requests.push({ url, method: req.method() });
		const isAllowedScheme =
			url.startsWith("http://127.0.0.1:8420/") ||
			url.startsWith("data:") ||
			url.startsWith("blob:");
		if (!isAllowedScheme) {
			unexpectedResponses.push({ url: `EXTERNAL_REQUEST:${url}`, status: 0 });
		}
	});
	page.on("response", (res) => {
		const url = res.url();
		const status = res.status();
		if (url.includes("/assets/fonts/") && url.endsWith(".woff2")) {
			const name = url.split("/").pop() ?? url;
			fontStatuses[name] = status;
		}
		if (status >= 400) {
			const allowed = activeAllowlist.some(
				(rule) => rule.urlPattern.test(url) && rule.statuses.includes(status),
			);
			if (!allowed) {
				unexpectedResponses.push({ url, status });
			}
		}
	});

	return {
		requests,
		fontStatuses,
		consoleErrors,
		pageErrors,
		unexpectedResponses,
		async allowFailure<T>(
			urlPattern: RegExp,
			statuses: number[],
			fn: () => Promise<T>,
		): Promise<T> {
			const rule: AllowedFailure = { urlPattern, statuses };
			activeAllowlist.push(rule);
			try {
				return await fn();
			} finally {
				const idx = activeAllowlist.indexOf(rule);
				if (idx >= 0) {
					activeAllowlist.splice(idx, 1);
				}
			}
		},
		assertClean(requireAllFonts = true) {
			expect(
				unexpectedResponses,
				"zero unexpected >=400 / 404 / external requests",
			).toEqual([]);
			expect(consoleErrors, "zero console.error").toEqual([]);
			expect(pageErrors, "zero uncaught pageerror").toEqual([]);
			if (requireAllFonts) {
				for (const font of [
					"dm-sans-latin.woff2",
					"instrument-serif-latin.woff2",
					"jetbrains-mono-latin.woff2",
				]) {
					expect(fontStatuses[font], `local font ${font} loaded HTTP 200`).toBe(
						200,
					);
				}
			}
		},
	};
}

async function assertLayoutAndA11y(page: Page) {
	const metrics = await page.evaluate(() => {
		const overflow = document.documentElement.scrollWidth - window.innerWidth;
		const hasHeader = Boolean(document.querySelector("header"));
		const hasMain = Boolean(document.querySelector("main"));
		const hasNav = Boolean(document.querySelector("nav"));
		const liveInDialog = document.querySelectorAll(
			"dialog [aria-live], dialog output",
		).length;
		return {
			overflow,
			scrollWidth: document.documentElement.scrollWidth,
			innerWidth: window.innerWidth,
			hasHeader,
			hasMain,
			hasNav,
			liveInDialog,
		};
	});
	expect(
		metrics.scrollWidth,
		`no horizontal overflow (${metrics.scrollWidth} <= ${metrics.innerWidth})`,
	).toBeLessThanOrEqual(metrics.innerWidth);
	expect(metrics.hasHeader).toBe(true);
	expect(metrics.hasMain).toBe(true);
	expect(metrics.hasNav).toBe(true);
	expect(metrics.liveInDialog).toBe(0);
}

async function shot(page: Page, testInfo: TestInfo, filename: string) {
	const dir = process.env.OPENDV_SCREENSHOT_DIR;
	if (dir) {
		mkdirSync(dir, { recursive: true });
		await page.screenshot({
			path: join(dir, filename),
			fullPage: false,
		});
		return;
	}
	await page.screenshot({
		path: testInfo.outputPath(filename),
		fullPage: false,
	});
}

async function attachWorkspaceSource(
	page: Page,
	adapterButtonName: string,
	relPath: string,
) {
	await page.getByRole("button", { name: adapterButtonName }).click();
	await page.getByLabel("Workspace File Path").fill(relPath);
	await page.getByRole("button", { name: "Inspect Source" }).click();
	await expect(page.getByText(/Preview:\s+\d+\s+table\(s\)/)).toBeVisible();
	await page.getByRole("button", { name: "Attach Source" }).click();
	const baseName = relPath.split("/").pop() ?? relPath;
	await expect(page.locator("main").getByText(baseName).first()).toBeVisible();
}

async function waitForStep4HoldOrDone(page: Page) {
	const answerBtn = page.getByRole("button", {
		name: "Submit Answer & Resume Discovery →",
	});
	const resumeBtn = page.getByRole("button", {
		name: "▶ Resume Discovery →",
	});
	const nextBtn = page.getByRole("button", {
		name: "Proceed to Step 5 Explorer →",
	});
	await expect(answerBtn.or(resumeBtn).or(nextBtn).first()).toBeVisible({
		timeout: 30_000,
	});
}

test.describe("Open-DV V2-7 Batched 3-Viewport Real-Browser E2E", () => {
	test("1440x920 complete lifecycle: Steps 1-7, Wave 2, Explorer, RunExplorer, Settings, Export/Import & Pause/Resume", async ({
		page,
	}, testInfo) => {
		test.setTimeout(180_000);
		await page.setViewportSize({ width: 1440, height: 920 });
		const guard = attachGuards(page);

		await page.goto("/");
		await expect(
			page.getByRole("heading", { name: /Step 1 · Scope & Seeds/i }),
		).toBeVisible();
		await expect(
			page.getByRole("banner").getByText(/Budget:\s*Idle\s*·\s*0 active runs/),
		).toBeVisible();
		await expect(
			page.getByRole("banner").getByText("Genesis v0.0.0"),
		).toBeVisible();
		await assertLayoutAndA11y(page);

		// 1. Step 1 Validation, Starter Pack, Workspace Seed, Delete Disposable, Create Main Project
		await page.getByLabel("Project ID").fill("INVALID ID!");
		await page.getByRole("button", { name: "Create Project" }).click();
		await expect(
			page
				.locator("main")
				.getByText(/lowercase|pattern|a-z|invalid/i)
				.first(),
		).toBeVisible();

		await page.getByLabel("Project ID").fill("am-starter-disp");
		await page.getByLabel("Domain Namespace").fill("asset_management");
		await page.getByRole("radio", { name: /Starter Pack/i }).check();
		await expect(page.getByText(/core_spine \+ planned stubs/i)).toBeVisible();
		await page.getByRole("button", { name: "Create Project" }).click();
		await expect(
			page.locator("main").getByRole("heading", { name: "am-starter-disp" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Delete Project" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Delete Project" }).click();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "Confirm Delete" })
			.click();
		await expect(page.getByRole("dialog")).not.toBeVisible();
		await expect(
			page.getByRole("button", { name: "Delete Project" }),
		).not.toBeVisible();

		await page.getByLabel("Project ID").fill("am-seed-disp");
		await page.getByRole("radio", { name: /Workspace Seed/i }).check();
		const seedSelect = page.getByLabel("Workspace Seeds");
		await expect(seedSelect).toBeVisible();
		await expect(seedSelect.locator("option").first()).not.toHaveText(
			/No seeds/i,
		);
		await page.getByRole("button", { name: "Create Project" }).click();
		await expect(
			page.locator("main").getByRole("heading", { name: "am-seed-disp" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Delete Project" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Delete Project" }).click();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: "Confirm Delete" })
			.click();
		await expect(page.getByRole("dialog")).not.toBeVisible();
		await expect(
			page.getByRole("button", { name: "Delete Project" }),
		).not.toBeVisible();

		await page.getByLabel("Project ID").fill("am-ibor-w1");
		await page.getByLabel("Domain Namespace").fill("asset_management");
		await page.getByRole("radio", { name: /Blank Genesis/i }).check();
		await page.getByRole("button", { name: "Create Project" }).click();
		await expect(
			page.locator("main").getByRole("heading", { name: "am-ibor-w1" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Proceed to Step 2 Sources →" }),
		).toBeVisible();
		await expect(page.getByRole("banner").getByText(/v0\.0\.0/i)).toBeVisible();
		await assertLayoutAndA11y(page);

		// 2. Step 2: Eager probe check (zero /ontology, /vault/ir, /evidence/tables/index before Wave 1 / Step 3)
		const step2StartIdx = guard.requests.length;
		await page
			.getByRole("button", { name: "Proceed to Step 2 Sources →" })
			.click();
		await expect(
			page.getByRole("heading", { name: /Step 2 · Sources & Secrets/i }),
		).toBeVisible();
		await page.waitForTimeout(300);
		const step2Probes = guard.requests
			.slice(step2StartIdx)
			.filter((r) =>
				/\/(ontology|vault\/ir|evidence\/tables\/index)(\?|$)/.test(r.url),
			);
		expect(
			step2Probes,
			"Step 2 must not emit eager /ontology, /vault/ir, or /evidence/tables/index probes",
		).toEqual([]);

		for (const adapterLabel of [
			"SQLite Database",
			"DuckDB Database",
			"Apache Parquet File",
			"CSV Table",
			"Excel Workbook (XLSX)",
			"Databricks Unity Catalog",
		]) {
			await expect(
				page.getByRole("button", { name: adapterLabel }),
			).toBeVisible();
		}

		await attachWorkspaceSource(
			page,
			"SQLite Database",
			"public_am/am_sec_treasury_ibor.sqlite",
		);
		await attachWorkspaceSource(
			page,
			"Apache Parquet File",
			"public_am/am_esg_stewardship_proxy.parquet",
		);
		await page
			.getByLabel("Choose source file to upload")
			.setInputFiles(join(publicAmDir, "am_stewardship_domain_notes.md"));
		await expect(
			page
				.locator("main")
				.getByText(/am_stewardship_domain_notes\.md/)
				.first(),
		).toBeVisible({ timeout: 15_000 });
		await page
			.getByLabel("Choose source file to upload")
			.setInputFiles(join(publicAmDir, "am_custody_and_voting_guide.pdf"));
		await expect(
			page
				.locator("main")
				.getByText(/am_custody_and_voting_guide\.pdf/)
				.first(),
		).toBeVisible({ timeout: 15_000 });

		await page.getByLabel(/Note Slug/i).fill("steward-key-policy");
		await page
			.getByLabel(/Markdown Note Body/i)
			.fill(
				"# IBOR Key Policy\nInstitutional Filer CIK uses SEC EDGAR global key authority.",
			);
		await page.getByRole("button", { name: "Save Domain Note" }).click();
		await expect(
			page
				.locator("main")
				.getByText(/steward-key-policy\.md/)
				.first(),
		).toBeVisible();

		await page
			.getByRole("button", { name: "Preview", exact: true })
			.first()
			.click();
		await expect(page.getByRole("dialog")).toBeVisible();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: /Close/i })
			.click();
		await assertLayoutAndA11y(page);
		await shot(page, testInfo, "01_step2_sources.png");

		// 3. Step 3a Cluster Tree + ConceptMemory + Step 3b 5-Page DocumentMap + Page 4 Scan Approval
		await page
			.getByRole("button", { name: "Proceed to Step 3 Preview →" })
			.click();
		await expect(
			page.getByRole("heading", { name: /Step 3 · Preview & Clusters/i }),
		).toBeVisible();

		const clusterBtn = page
			.locator("main button")
			.filter({ hasText: /institutional_filers|holdings_13f/i })
			.first();
		await expect(clusterBtn).toBeVisible();
		await clusterBtn.click();
		await expect(page.locator("main table tbody tr").first()).toBeVisible();

		const conceptBtn = page
			.locator("button[data-memory-state='UNSET']")
			.first();
		await expect(conceptBtn).toBeVisible();
		await conceptBtn.click();
		const pinnedBtn = page
			.locator("button[data-memory-state='PINNED']")
			.first();
		await expect(pinnedBtn).toBeVisible();
		const pinnedBorder = await pinnedBtn.evaluate(
			(el) => getComputedStyle(el).borderColor,
		);
		expect(pinnedBorder).toMatch(/169,\s*88,\s*62|#a9583e/i);

		await page.locator("#step3-tab-3b").click();
		await page
			.locator("main button")
			.filter({ hasText: /am_custody_and_voting_guide\.pdf/i })
			.first()
			.click();
		await expect(page.getByText("Steward Scan Consent Gate")).toBeVisible();
		const p4Check = page.getByRole("checkbox", { name: /Page 4\s*·/i }).first();
		await expect(p4Check).toBeVisible();
		await p4Check.check();
		await page
			.getByLabel("Approval Rationale")
			.fill(
				"Page 4 contains board custody authority matrix verified by steward.",
			);
		await page.getByRole("button", { name: "Approve Page 4 Scan" }).click();
		await expect(page.getByText(/Prior approved:\s*4/)).toBeVisible();
		await assertLayoutAndA11y(page);

		// 4. Step 4 Discovery, Live SSE Broker Trace, Hold Card, MCP Receipts, Grounding & Ask FIBO
		await page
			.getByRole("button", { name: "Proceed to Step 4 Discovery →" })
			.click();
		await expect(
			page.getByRole("heading", { name: /Step 4 · Agent Discovery/i }),
		).toBeVisible();
		await page
			.getByLabel(/Initial Steward Hint/i)
			.fill("Verify Institutional Filer SEC CIK key and Proxy Ballot binding.");
		await page.getByRole("button", { name: "Start Discovery" }).click();

		// Expand live Broker Trace SSE drawer during/after run
		await expect(
			page.getByLabel("Broker Agent I/O Trace Drawer"),
		).toBeVisible();
		const expandBtn = page.getByRole("button", { name: /Expand Trace/i });
		if (await expandBtn.isVisible()) {
			await expandBtn.click();
		}
		await expect(page.getByText(/Last-Event-ID:\s*[1-9]\d*/)).toBeVisible({
			timeout: 20_000,
		});
		const collapseBtn = page.getByRole("button", { name: /Collapse Trace/i });
		if (await collapseBtn.isVisible()) {
			await collapseBtn.click();
		}

		await waitForStep4HoldOrDone(page);
		const holdSubmit = page.getByRole("button", {
			name: "Submit Answer & Resume Discovery →",
		});
		await expect(holdSubmit).toBeVisible();
		await page.getByRole("radio", { name: "Custom Steward Answer" }).check();
		await page
			.getByPlaceholder("Enter plain-English Steward guidance...")
			.fill("Confirmed filer_cik uniquely identifies an Institutional Filer.");
		await holdSubmit.click();

		await expect(
			page.getByRole("button", { name: "Proceed to Step 5 Explorer →" }),
		).toBeEnabled({ timeout: 30_000 });
		await expect(
			page
				.locator("main")
				.getByText(/1 MCP Receipts/)
				.first(),
		).toBeVisible();

		await page.locator("#step4-tab-4b").click();
		await expect(page.getByText("PATH_A1").first()).toBeVisible();
		await expect(page.getByText("PATH_B1").first()).toBeVisible();
		await expect(page.getByText("PATH_B2").first()).toBeVisible();

		await page
			.getByRole("button", { name: "Inspect Citations" })
			.first()
			.click();
		await expect(page.getByRole("dialog")).toBeVisible();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: /Close/i })
			.click();

		await page.getByRole("button", { name: "Ask FIBO" }).click();
		await page.getByLabel("FIBO search query").fill("Financial Security");
		await page.getByRole("button", { name: "Search FIBO" }).click();
		await expect(
			page.getByRole("dialog").getByText(/fibo/i).first(),
		).toBeVisible();
		await page
			.getByRole("dialog")
			.getByRole("button", { name: /Close/i })
			.click();
		await assertLayoutAndA11y(page);
		await shot(page, testInfo, "02_step4_discovery_grounding.png");

		// 5. Step 5 Ontology Explorer 4 Modes, React Flow + ELK, Dirty Nav Guard, YAML CST & Wave 1 Commit
		await page
			.getByRole("button", { name: "Proceed to Step 5 Explorer →" })
			.click();
		await expect(
			page.getByRole("heading", {
				name: /Step 5 · Ontology Explorer \(Graph A\)/i,
			}),
		).toBeVisible();
		await expect(page.locator(".react-flow__node").first()).toBeVisible({
			timeout: 15_000,
		});
		expect(
			await page.locator(".react-flow__node").count(),
		).toBeGreaterThanOrEqual(3);

		// Verify Mode 3 YAML CST editor while clean, then test Mode 1-2 staging & mutual exclusion
		await page.locator("#step5-tab-3").click();
		const yamlEditor = page.getByLabel("Graph A Ontology YAML Editor");
		await expect(yamlEditor).toBeVisible();
		const validYamlBefore = await yamlEditor.inputValue();
		expect(validYamlBefore).toContain("Institutional Filer");
		await yamlEditor.fill(`${validYamlBefore}\n  bad: [unclosed`);
		await expect(page.getByTestId("yaml-validity-pill")).toContainText(
			/Invalid/i,
		);
		await expect(
			page.getByRole("button", { name: "Validate YAML" }),
		).toBeDisabled();
		await yamlEditor.fill(`# preserved steward comment\n${validYamlBefore}`);
		await expect(
			page.getByRole("button", { name: "Validate YAML" }),
		).toBeEnabled();
		await page.getByRole("button", { name: "Validate YAML" }).click();
		await expect(page.getByTestId("yaml-validity-pill")).toContainText(
			/Valid/i,
		);
		expect(await yamlEditor.inputValue()).toContain(
			"# preserved steward comment",
		);
		await page.getByRole("button", { name: "Discard Draft" }).click();

		// Stage AddConcept + attribute in Mode 1 & test Dirty Navigation Guard dialog
		await page.locator("#step5-tab-1").click();
		await page
			.getByPlaceholder("Concept name or URN/URI...")
			.fill("Benchmark Index");
		await page
			.getByPlaceholder("Business description...")
			.fill("Reference market performance index.");
		await page.getByRole("button", { name: "Stage AddConcept" }).click();
		await page.getByPlaceholder("Attribute name...").fill("benchmark_id");
		await page.getByRole("checkbox", { name: "BK" }).check();
		await page.getByRole("button", { name: "+ Add Attr" }).click();

		await page.getByRole("link", { name: /6 Vault Synthesis/i }).click();
		const dirtyDialog = page.getByRole("dialog", { name: /Unsaved Changes/i });
		await expect(dirtyDialog).toBeVisible();
		await dirtyDialog.getByRole("button", { name: "Stay Here" }).click();
		await expect(dirtyDialog).not.toBeVisible();

		await page.locator("#step5-tab-2").click();
		await expect(
			page.getByRole("heading", {
				name: /Concept Catalog & Key Authority Tiers/i,
			}),
		).toBeVisible();
		await expect(
			page.getByLabel("Key tier for Institutional Filer"),
		).toBeVisible();

		await page.locator("#step5-tab-3").click();
		await expect(page.getByText(/Mode 3 Read-Only:/i)).toBeVisible();

		await page.locator("#step5-tab-4").click();
		await expect(
			page.getByRole("checkbox", {
				name: /Explicitly waive Path B2 ungrounded quarantine/i,
			}),
		).toBeVisible();
		const apWaivers = page.getByRole("checkbox", {
			name: /Sign ONT-AP Waiver/i,
		});
		for (let i = 0; i < (await apWaivers.count()); i++) {
			await apWaivers.nth(i).check();
		}
		const apRationales = page.locator(
			"main input[aria-label^='Waiver rationale for ']",
		);
		for (let i = 0; i < (await apRationales.count()); i++) {
			await apRationales
				.nth(i)
				.fill(
					"Intentional Wave 1 business baseline design choice approved by Steward.",
				);
		}
		await page
			.getByRole("checkbox", {
				name: /Explicitly waive Path B2 ungrounded quarantine/i,
			})
			.check();
		await page
			.getByPlaceholder(
				"Describe this ontology revision (min 20 chars if waiving Path B2 quarantine)...",
			)
			.fill(
				"Wave 1 asset management baseline approved with quarantined custody signal waiver.",
			);
		await page.getByRole("button", { name: /Validate & Commit v/i }).click();
		await expect(
			page
				.getByRole("banner")
				.getByText(/ont-(?!0\.0\.0)\d+\.\d+\.\d+/i)
				.first(),
		).toBeVisible({
			timeout: 15_000,
		});
		for (const font of EXPECTED_FONTS) {
			const res = await page.request.get(`/assets/fonts/${font}`);
			expect(res.status(), `font asset ${font} status 200`).toBe(200);
		}
		await assertLayoutAndA11y(page);
		await shot(page, testInfo, "03_step5_ontology_explorer.png");

		// 6. Step 6 Guided View, 4-Field Explainer, Jargon Lint, Horizon Review & Observatory
		await page
			.getByRole("button", { name: "Proceed to Step 6 Vault →" })
			.click();
		await expect(
			page.getByRole("heading", {
				name: /Step 6 · Vault Synthesis/i,
			}),
		).toBeVisible();
		await page.getByRole("radio", { name: "Quick" }).check();
		await page.getByRole("button", { name: "▶ Start Synthesis" }).click();
		await expect(
			page.getByRole("button", { name: "Proceed to Step 7 Target Build →" }),
		).toBeEnabled({ timeout: 30_000 });

		// Verify Guided View zero forbidden jargon + 4-field explainer
		await page.locator("#guided-tab-blueprint").click();
		const guidedMainText = await page.locator("main").innerText();
		expect(
			FORBIDDEN_GUIDED_JARGON.test(guidedMainText),
			`Guided View must not expose forbidden jargon; saw match in: ${guidedMainText.match(FORBIDDEN_GUIDED_JARGON)?.[0]}`,
		).toBe(false);
		expect(
			STRICT_DV_JARGON.test(guidedMainText),
			`Guided Blueprint tab must use plain English; saw match in: ${guidedMainText.match(STRICT_DV_JARGON)?.[0]}`,
		).toBe(false);
		await expect(
			page.getByRole("heading", {
				name: "Why Does This Table Exist?",
				exact: true,
			}),
		).toBeVisible();
		await expect(
			page.locator("main").getByText("1. Business Purpose").first(),
		).toBeVisible();
		await expect(
			page.locator("main").getByText("2. How History Is Protected").first(),
		).toBeVisible();
		await expect(
			page.locator("main").getByText("3. Future Change Impact").first(),
		).toBeVisible();
		await shot(page, testInfo, "04_step6_guided_blueprint.png");

		await page.getByRole("button", { name: "Run Horizon Review" }).click();
		await page.locator("#guided-tab-decisions").click();
		await expect(
			page.getByText(/Monitor custodian fee column additions/i),
		).toBeVisible({ timeout: 15_000 });

		await page.getByRole("button", { name: "Architect Observatory" }).click();
		await expect(
			page.getByRole("button", { name: "✓ Verify Vault" }),
		).toBeVisible();
		await page.getByRole("button", { name: "✓ Verify Vault" }).click();
		await page.locator("#obs-tab-horizon").click();
		await expect(page.getByText(/LOCK-W001 immutable/i)).toBeVisible();
		await page.getByRole("button", { name: "Guided View (Default)" }).click();

		// 7. Wave 2 Incremental Attach -> Lock Verification -> Horizon Bucket B Escalation -> Wave 2 Proposals
		await page.getByRole("link", { name: /2 Sources & Secrets/i }).click();
		await expect(
			page.getByRole("button", { name: /Detach 🔒/ }).first(),
		).toBeDisabled();
		await attachWorkspaceSource(
			page,
			"Apache Parquet File",
			"public_am/wave2_eu_custody_lei_and_drift.parquet",
		);
		await attachWorkspaceSource(
			page,
			"CSV Table",
			"public_am/wave2_bitemporal_nav_restatements.csv",
		);
		await attachWorkspaceSource(
			page,
			"CSV Table",
			"public_am/wave2_ambiguous_otc_collateral.csv",
		);
		await attachWorkspaceSource(
			page,
			"CSV Table",
			"public_am/prime_allocations_w2.csv",
		);

		// Wave 2 incremental discovery -> ontology commit before Wave 2 vault synthesis
		await page.getByRole("link", { name: /4 Discovery/i }).click();
		const [w2ConstructResp] = await Promise.all([
			page.waitForResponse(
				(res) =>
					/\/api\/v1\/projects\/[^/]+\/ontology\/construct(\?|$)/.test(
						res.url(),
					) &&
					res.request().method() === "POST" &&
					res.status() < 300,
			),
			page.getByRole("button", { name: /Start Discovery/i }).click(),
		]);
		const w2Run = (await w2ConstructResp.json()) as { run_id: string };
		await expect
			.poll(
				async () => {
					const r = await page.request.get(
						`/api/v1/projects/am-ibor-w1/ontology/runs/${w2Run.run_id}`,
					);
					const data = (await r.json()) as { status?: string };
					return data.status ?? "";
				},
				{ timeout: 30_000 },
			)
			.toBe("COMPLETED");
		await expect(page.getByText(w2Run.run_id).first()).toBeVisible({
			timeout: 15_000,
		});
		await expect(
			page.getByRole("button", { name: "Proceed to Step 5 Explorer →" }),
		).toBeEnabled({ timeout: 30_000 });
		await page
			.getByRole("button", { name: "Proceed to Step 5 Explorer →" })
			.click();
		await page.locator("#step5-tab-4").click();
		await expect(
			page.locator("main option", { hasText: "Prime Allocation" }),
		).toBeAttached({ timeout: 10_000 });
		await page.getByRole("button", { name: "Re-validate" }).click();
		await expect(
			page.getByRole("button", { name: "Re-validate" }),
		).toBeEnabled();
		const w2Waivers = page.getByRole("checkbox", {
			name: /Sign ONT-AP Waiver/i,
		});
		for (let i = 0; i < (await w2Waivers.count()); i++) {
			await w2Waivers.nth(i).check();
		}
		const w2Rats = page.locator(
			"main input[aria-label^='Waiver rationale for ']",
		);
		for (let i = 0; i < (await w2Rats.count()); i++) {
			await w2Rats
				.nth(i)
				.fill("Wave 2 custody and NAV governance rationale approved.");
		}
		await page
			.getByPlaceholder(
				"Describe this ontology revision (min 20 chars if waiving Path B2 quarantine)...",
			)
			.fill(
				"Wave 2 incremental ontology commit for custody, NAV, OTC, and prime feeds.",
			);
		const w2WaiveQ = page.getByRole("checkbox", {
			name: /Explicitly waive Path B2 ungrounded quarantine/i,
		});
		if ((await w2WaiveQ.isVisible()) && !(await w2WaiveQ.isChecked())) {
			await w2WaiveQ.check();
		}
		await page.getByRole("button", { name: /Validate & Commit v/i }).click();
		await expect(page.getByText(/Committed ont-/i).first()).toBeVisible({
			timeout: 15_000,
		});

		await Promise.all([
			page.waitForResponse(
				(res) =>
					/\/api\/v1\/projects\/[^/]+\/runs(\?|$)/.test(res.url()) &&
					res.status() === 200,
			),
			page.waitForResponse(
				(res) =>
					/\/api\/v1\/projects\/[^/]+\/ontology(\?|$)/.test(res.url()) &&
					res.status() === 200,
			),
			page.getByRole("link", { name: /6 Vault Synthesis/i }).click(),
		]);
		const balancedRadio = page.getByRole("radio", { name: "Balanced" });
		await expect(balancedRadio).toBeVisible({ timeout: 10_000 });
		await balancedRadio.check();
		await expect(balancedRadio).toBeChecked();
		// Legacy Wave-2 test fixture wave2_bitemporal_nav_restatements.csv has duplicate
		// (fund_code, share_class, as_of_date, applied_dts) rows tested as STANDARD sat_nav_bitemp.
		const w2SettingsPatch = await page.request.patch(
			"/api/v1/projects/am-ibor-w1/settings",
			{ data: { real_row_proving: false } },
		);
		expect(w2SettingsPatch.ok()).toBe(true);
		const [w2RunResp] = await Promise.all([
			page.waitForResponse(
				(res) =>
					/\/api\/v1\/projects\/[^/]+\/runs(\?|$)/.test(res.url()) &&
					res.request().method() === "POST" &&
					res.status() < 300,
			),
			page
				.getByRole("button", { name: "▶ Start / Re-run", exact: true })
				.click(),
		]);
		const w2VaultRun = (await w2RunResp.json()) as { run_id: string };
		await expect
			.poll(
				async () => {
					const r = await page.request.get(
						`/api/v1/projects/am-ibor-w1/runs/${w2VaultRun.run_id}`,
					);
					const data = (await r.json()) as { status?: string };
					return data.status ?? "";
				},
				{ timeout: 30_000 },
			)
			.toBe("HOLD_CHECKPOINTED");
		const decTab = page.locator("#guided-tab-decisions");
		if (await decTab.isVisible()) {
			await decTab.click();
		}
		await expect(
			page.getByText(/Confirm OTC Collateral Key Scope/i).first(),
		).toBeVisible({ timeout: 15_000 });
		const w2ConfirmBtn = page.getByRole("button", {
			name: /Confirm Business Choice & Certify Wave/i,
		});
		await expect(w2ConfirmBtn).toBeVisible({ timeout: 30_000 });
		const w2EscalationText = await page.locator("main").innerText();
		expect(
			FORBIDDEN_GUIDED_JARGON.test(w2EscalationText),
			`Wave 2 Guided escalation must avoid forbidden jargon; saw match in: ${w2EscalationText.match(FORBIDDEN_GUIDED_JARGON)?.[0]}`,
		).toBe(false);
		const horizonCardText = await page
			.locator("main article", { hasText: "Confirm OTC Collateral Key Scope" })
			.innerText();
		expect(
			STRICT_DV_JARGON.test(horizonCardText),
			`Wave 2 Horizon Decision Card must use plain English; saw match in: ${horizonCardText.match(STRICT_DV_JARGON)?.[0]}`,
		).toBe(false);
		await page.getByRole("radio", { name: /Keep source-scoped/i }).check();
		await page
			.getByRole("button", { name: "Optional Business Guidance Note" })
			.click();
		await page
			.getByPlaceholder("Optional plain-English note for future waves...")
			.fill(
				"Keep prime broker collateral keys source-scoped per Wave 2 guidance.",
			);
		await w2ConfirmBtn.click();
		await expect(
			page.getByRole("heading", { name: /Wave 2 blueprint certified/i }),
		).toBeVisible({
			timeout: 30_000,
		});
		await expect(page.getByText(/wave-2-/i).first()).toBeVisible();
		await shot(page, testInfo, "05_step6_wave2_certified.png");

		// Verify Wave 2 OntologyProposalSpec arrives on Step 5 Mode 1 -> stage -> discard clean draft
		await page.getByRole("link", { name: /5 Ontology Explorer/i }).click();
		const stagePropBtn = page
			.getByRole("button", { name: "Stage into Draft Ontology" })
			.first();
		await expect(stagePropBtn).toBeVisible({ timeout: 10_000 });
		await stagePropBtn.click();
		await page.getByRole("button", { name: "Discard Draft" }).click();

		// 8. Step 7 Target Build, All 11 Targets, Migration, Full Regen & Read-Only DuckDB SQL
		await page.getByRole("link", { name: /7 Storage & Targets/i }).click();
		await expect(
			page.getByRole("heading", {
				name: /Step 7 · 11-Target Storage Compilation/i,
			}),
		).toBeVisible();
		await page
			.getByRole("button", { name: /Compile Selected \(duckdb_local\)/i })
			.click();
		await expect(page.getByText(/Generated Files:/i)).toBeVisible({
			timeout: 20_000,
		});

		await page.getByRole("button", { name: "Compile All 11 Targets" }).click();
		await expect(page.getByText(/TargetEvalGate Receipts/i)).toBeVisible({
			timeout: 30_000,
		});

		await page
			.getByRole("button", {
				name: /Migrate Selected Target \(duckdb_local\)/i,
			})
			.click();
		await expect(
			page.getByText(/Target duckdb_local wave \d+: rows_loaded=/i),
		).toBeVisible({ timeout: 30_000 });

		await page
			.getByRole("button", {
				name: /Reset & Test Full Regen \(duckdb_local\)/i,
			})
			.click();
		await expect(
			page.getByText(
				/Target duckdb_local wave \d+: rows_loaded=.*verified=true/i,
			),
		).toBeVisible({ timeout: 30_000 });

		const sqlBox = page.getByLabel("Read-only DuckDB SQL Query");
		await sqlBox.fill(
			"SELECT table_name FROM information_schema.tables ORDER BY table_name",
		);
		await page.getByRole("button", { name: "Run Query" }).click();
		await expect(page.locator("main table tbody tr").first()).toBeVisible({
			timeout: 15_000,
		});

		await guard.allowFailure(/\/storage\/query$/, [400, 422], async () => {
			await sqlBox.fill("DROP TABLE hub_holding");
			await page.getByRole("button", { name: "Run Query" }).click();
			await expect(
				page
					.locator("main")
					.getByText(/read-only|SELECT|forbidden|rejected/i)
					.first(),
			).toBeVisible();
		});

		const dismissAlert = page.locator("main [role='alert'] button", {
			hasText: "Dismiss",
		});
		if (await dismissAlert.isVisible()) {
			await dismissAlert.click();
		}
		await sqlBox.fill(
			"SELECT COUNT(*) AS row_count FROM raw_vault.hub_holding",
		);
		await page.getByRole("button", { name: "Run Query" }).click();
		await expect(
			page.locator("main table th", { hasText: /row_count/i }),
		).toBeVisible({ timeout: 15_000 });
		await expect(page.locator("main table tbody tr").first()).toBeVisible();
		await shot(page, testInfo, "06_step7_storage_duckdb.png");

		// 9. DV Schema Explorer (Views 1-4 + 10 chips + ELK/React Flow)
		await page
			.getByRole("button", {
				name: /Inspect IR & Target Manifests \(Explorer View 3\)/i,
			})
			.click();
		await expect(page.locator("#exp-tab-3")).toHaveAttribute(
			"aria-selected",
			"true",
		);
		await page.locator("#exp-tab-1").click();
		await expect(
			page.getByRole("heading", { name: /Z2 Raw Vault/i }),
		).toBeVisible();
		await page.getByRole("button", { name: "Z2 Hubs" }).click();
		await page.getByRole("button", { name: "PII Split" }).click();
		await page.getByRole("button", { name: "All Zones" }).click();
		await page.locator("#exp-tab-2").click();
		await expect(page.locator(".react-flow__node").first()).toBeVisible({
			timeout: 15_000,
		});
		await shot(page, testInfo, "07_dv_schema_explorer.png");
		await page.locator("#exp-tab-4").click();
		await assertLayoutAndA11y(page);

		// 10. Run Explorer (Step 6 vs Step 4 + /attempts guard + .jsonl download)
		await page.getByRole("link", { name: "Run Explorer" }).click();
		await expect(page.locator("#run-tab-payload")).toBeVisible();
		const runSelect = page.locator("main select").first();
		await expect(runSelect).toBeVisible();
		const options = await runSelect.locator("option").allTextContents();
		const step4Opt = options.find((o) => /step4|discovery|first-mate/i.test(o));
		const step6Opt = options.find((o) => /step6|vault|ship-vault/i.test(o));
		expect(step4Opt).toBeTruthy();
		expect(step6Opt).toBeTruthy();

		if (step6Opt) {
			await runSelect.selectOption({ label: step6Opt });
			await page.locator("#run-tab-attempts").click();
			await page.locator("#run-tab-receipts").click();
			await page.locator("#run-tab-redaction").click();
			await page.locator("#run-tab-payload").click();
		}
		if (step4Opt) {
			const beforeStep4RunIdx = guard.requests.length;
			await runSelect.selectOption({ label: step4Opt });
			await page.waitForTimeout(250);
			const attemptFetches = guard.requests
				.slice(beforeStep4RunIdx)
				.filter((r) => /\/attempts(\?|$)/.test(r.url));
			expect(
				attemptFetches,
				"RunExplorer must not request /attempts for Step 4 run",
			).toEqual([]);
		}
		const jsonlDlPromise = page.waitForEvent("download");
		await page.getByRole("button", { name: "Download .jsonl" }).click();
		const jsonlDl = await jsonlDlPromise;
		expect(jsonlDl.suggestedFilename()).toMatch(/\.jsonl$/);
		await shot(page, testInfo, "08_run_explorer.png");

		// 11. Settings Hub (provider / secrets / mcp)
		await page.getByRole("link", { name: "Settings" }).click();
		await expect(page.locator("#set-tab-provider")).toBeVisible();
		await expect(
			page.getByRole("heading", { name: /Harness Readiness Diagnostics/i }),
		).toBeVisible();
		await expect(page.locator("datalist option").first()).toBeAttached();
		const providerSelect = page
			.locator("label", { hasText: "Harness Provider" })
			.locator("select");
		await providerSelect.selectOption("gemini");
		await expect(page.getByLabel("API key for GEMINI_API_KEY")).toBeVisible();
		await page.getByRole("button", { name: "Test", exact: true }).click();
		await expect(
			page.getByRole("button", { name: "Copy", exact: true }).first(),
		).toBeVisible({ timeout: 10_000 });
		await providerSelect.selectOption("agy");
		await expect(
			page.getByLabel("API key for GEMINI_API_KEY"),
		).not.toBeVisible();
		await page.locator("#set-tab-secrets").click();
		await expect(page.getByText(/User-Global/i).first()).toBeVisible();
		await expect(
			page.locator("option", { hasText: "store_first (Store > Env)" }),
		).toBeAttached();
		await page
			.getByPlaceholder("Secret key (e.g. DATABRICKS_TOKEN)")
			.fill("UAT_MASKED_TOKEN");
		await page
			.getByPlaceholder("Write-only secret value")
			.fill("super-secret-value");
		await page.getByRole("button", { name: "Store Secret" }).click();
		await expect(page.getByText("UAT_MASKED_TOKEN")).toBeVisible();
		await page.getByPlaceholder("Profile name").fill("uat-sqlite-profile");
		await page.locator("main select").last().selectOption("sqlite");
		await page
			.getByPlaceholder("URI, Databricks host, or workspace path")
			.fill("public_am/am_sec_treasury_ibor.sqlite");
		await page
			.getByRole("button", { name: "Stage Connection Profile" })
			.click();
		await page.getByRole("button", { name: "Test Connection" }).first().click();

		await page.locator("#set-tab-mcp").click();
		await expect(page.getByText(/User-Global/i).first()).toBeVisible();
		await expect(page.getByText("ext_doc")).toBeVisible();
		await page.getByRole("button", { name: "Save Settings" }).click();
		await expect(
			page.getByRole("button", { name: "Save Settings" }),
		).toBeDisabled({ timeout: 10_000 });

		// 12. Export / Import Modal (full_demo + core_project_state + upload rename + ImportReadinessReport)
		await page.getByRole("link", { name: "Project Workflow" }).click();
		await page.getByRole("button", { name: "Export / Import" }).click();
		const modal = page.getByRole("dialog");
		await expect(modal).toBeVisible();

		await modal.getByRole("radio", { name: /core_project_state/i }).check();
		const coreDlPromise = page.waitForEvent("download");
		await modal.getByRole("button", { name: /Export am-ibor-w1/i }).click();
		const coreDl = await coreDlPromise;
		expect(coreDl.suggestedFilename()).toContain("core.opendv.zip");

		await modal.getByRole("radio", { name: /full_demo/i }).check();
		const fullDlPromise = page.waitForEvent("download");
		await modal.getByRole("button", { name: /Export am-ibor-w1/i }).click();
		const fullDl = await fullDlPromise;
		const fullZipPath = await fullDl.path();
		expect(fullZipPath).toBeTruthy();

		if (fullZipPath) {
			const buf = readFileSync(fullZipPath);
			expect(buf.byteLength).toBeGreaterThan(200);
			await modal.locator("input[type='file']").setInputFiles(fullZipPath);
			await modal.getByRole("radio", { name: "rename" }).check();
			await modal.getByRole("button", { name: "Import Bundle" }).click();
			await expect(
				modal.getByRole("button", { name: "Open Hydrated Project →" }),
			).toBeVisible({ timeout: 20_000 });
			await expect(modal.getByText(/resolved_conflict:/i)).toBeVisible();
			await expect(modal.getByText(/readiness_flags:/i)).toBeVisible();
			await expect(modal.getByText(/missing_secret_refs:/i)).toBeVisible();
			await expect(modal.getByText(/missing_source_ids:/i)).toBeVisible();
			await expect(modal.getByText(/unverified_receipt_ids:/i)).toBeVisible();
			await expect(modal.getByText(/audit_claim_notice:/i)).toBeVisible();
			await shot(page, testInfo, "09_export_import_report.png");
			await modal
				.getByRole("button", { name: "Open Hydrated Project →" })
				.click();
		}

		// 13. Pause/Resume drain verification on dedicated -ctrl project
		await page.getByRole("link", { name: /1 Scope & Seeds/i }).click();
		await page.getByLabel("Project ID").fill("am-ctrl");
		await page.getByLabel("Domain Namespace").fill("asset_management");
		await page.getByRole("radio", { name: /Starter Pack/i }).check();
		await page.getByRole("button", { name: "Create Project" }).click();
		await expect(
			page.locator("main").getByRole("heading", { name: "am-ctrl" }),
		).toBeVisible();
		await page.getByRole("link", { name: /2 Sources & Secrets/i }).click();
		await attachWorkspaceSource(
			page,
			"SQLite Database",
			"public_am/am_sec_treasury_ibor.sqlite",
		);
		await page.getByRole("link", { name: /4 Discovery/i }).click();
		await page.getByLabel(/Initial Steward Hint/i).fill("pause-probe");
		const [ctrl4Resp] = await Promise.all([
			page.waitForResponse(
				(res) =>
					/\/api\/v1\/projects\/am-ctrl\/ontology\/construct(\?|$)/.test(
						res.url(),
					) && res.request().method() === "POST",
			),
			page.getByRole("button", { name: "Start Discovery" }).click(),
		]);
		const ctrl4 = (await ctrl4Resp.json()) as { run_id: string };
		await page.getByRole("button", { name: "Pause" }).click();
		await expect(
			page.getByRole("button", { name: "▶ Resume Discovery →" }),
		).toBeVisible({ timeout: 15_000 });
		await page.getByRole("button", { name: "▶ Resume Discovery →" }).click();
		await expect
			.poll(
				async () => {
					const r = await page.request.get(
						`/api/v1/projects/am-ctrl/ontology/runs/${ctrl4.run_id}`,
					);
					const data = (await r.json()) as { status?: string };
					return data.status ?? "";
				},
				{ timeout: 20_000 },
			)
			.toBe("COMPLETED");
		await expect(
			page
				.getByText(/(Ready|Steward Review|Unverified|Action Needed) · agy/i)
				.first(),
		).toBeVisible({
			timeout: 10_000,
		});
		await page.waitForTimeout(250);

		await page.getByRole("link", { name: /6 Vault Synthesis/i }).click();
		await page.getByRole("radio", { name: "Quick" }).check();
		await page.getByRole("button", { name: "▶ Start Synthesis" }).click();
		await page.getByRole("button", { name: "⏸ Pause" }).click();
		await expect(
			page.getByRole("button", { name: "▶ Resume Synthesis →" }),
		).toBeVisible({ timeout: 15_000 });
		await page.getByRole("button", { name: "▶ Resume Synthesis →" }).click();
		await expect(
			page.getByRole("button", { name: "▶ Start / Re-run", exact: true }),
		).toBeEnabled({ timeout: 30_000 });

		guard.assertClean(true);
	});

	test("1024x768 compact desktop viewport preserves navigation & zero overflow", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1024, height: 768 });
		const guard = attachGuards(page);

		await page.goto("/");
		await expect(
			page.getByRole("heading", { name: /Step 1 · Scope & Seeds/i }),
		).toBeVisible();
		await assertLayoutAndA11y(page);

		await page
			.getByRole("banner")
			.getByLabel("Project Switcher")
			.selectOption("am-ibor-w1");
		for (const stepLink of [
			/2 Sources & Secrets/i,
			/3 Preview & Clusters/i,
			/4 Discovery/i,
			/5 Ontology Explorer/i,
			/6 Vault Synthesis/i,
			/7 Storage & Targets/i,
		]) {
			await page.getByRole("link", { name: stepLink }).click();
			await assertLayoutAndA11y(page);
		}
		await page.getByRole("link", { name: "DV Schema Explorer" }).click();
		await assertLayoutAndA11y(page);
		await page.getByRole("link", { name: "Run Explorer" }).click();
		await assertLayoutAndA11y(page);
		await page.getByRole("link", { name: "Settings" }).click();
		await assertLayoutAndA11y(page);

		guard.assertClean(false);
	});

	test("390x844 mobile drawers, >= 40px touch targets, view-only downgrade & resize draft retention", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		const guard = attachGuards(page);

		await page.goto("/");
		await expect(
			page.getByRole("button", { name: "Open navigation menu" }),
		).toBeVisible();
		await assertLayoutAndA11y(page);

		// Verify topbar navigation & project switcher live cleanly inside mobile <dialog>
		await page.getByRole("button", { name: "Open navigation menu" }).click();
		const mobileNav = page.locator("#mobile-nav-dialog");
		await expect(mobileNav).toBeVisible();
		await expect(mobileNav.getByLabel("Project Switcher")).toBeVisible();
		await expect(
			mobileNav.getByRole("button", { name: "Export / Import" }),
		).toBeVisible();
		await mobileNav.getByLabel("Project Switcher").selectOption("am-ibor-w1");
		await expect(mobileNav).not.toBeVisible();

		// Check >= 40px interactive touch targets on mobile
		const smallTargets = await page.evaluate(() => {
			const bad: string[] = [];
			for (const el of document.querySelectorAll(
				"header button, main button, main [role='tab'], main select, main input[type='radio']",
			)) {
				const rect = el.getBoundingClientRect();
				if (rect.width === 0 || rect.height === 0) continue;
				const cs = getComputedStyle(el);
				if (cs.display === "none" || cs.visibility === "hidden") continue;
				if (el.getAttribute("type") === "radio") continue;
				if (rect.height < 40) {
					bad.push(
						`${el.tagName}:${(el.textContent ?? "").trim().slice(0, 24)}(${Math.round(rect.height)}px)`,
					);
				}
			}
			return bad;
		});
		expect(
			smallTargets,
			"interactive mobile controls should meet >= 40px height target",
		).toEqual([]);

		// Check Settings mobile layout & >= 40px touch targets, then return to workflow
		await page.getByRole("button", { name: "Open navigation menu" }).click();
		await mobileNav.getByRole("link", { name: "Settings" }).click();
		await expect(
			page.getByRole("heading", { name: /Harness Readiness Diagnostics/i }),
		).toBeVisible();
		await assertLayoutAndA11y(page);
		const smallSettingsTargets = await page.evaluate(() => {
			const bad: string[] = [];
			for (const el of document.querySelectorAll(
				"header button, main button, main [role='tab'], main select, main input[type='radio']",
			)) {
				const rect = el.getBoundingClientRect();
				if (rect.width === 0 || rect.height === 0) continue;
				const cs = getComputedStyle(el);
				if (cs.display === "none" || cs.visibility === "hidden") continue;
				if (el.getAttribute("type") === "radio") continue;
				if (rect.height < 40) {
					bad.push(
						`${el.tagName}:${(el.textContent ?? "").trim().slice(0, 24)}(${Math.round(rect.height)}px)`,
					);
				}
			}
			return bad;
		});
		expect(
			smallSettingsTargets,
			"interactive mobile controls on Settings should meet >= 40px height target",
		).toEqual([]);
		await page.getByRole("button", { name: "Open navigation menu" }).click();
		await mobileNav.getByRole("link", { name: "Project Workflow" }).click();

		// Switch steps via mobile stepper sheet <dialog> -> Step 5 -> verify view-only downgrade + resize draft preservation
		await page.getByRole("button", { name: /Step \d+ of 7/i }).click();
		const stepperDlg = page.locator("#mobile-stepper-dialog");
		await expect(stepperDlg).toBeVisible();
		await stepperDlg.getByRole("link", { name: /5 Ontology/i }).click();
		await expect(
			page.getByRole("heading", {
				name: /Step 5 · Ontology Explorer/i,
			}),
		).toBeVisible();
		await assertLayoutAndA11y(page);

		// Resize up to 1280px, type draft edit, resize back to 390px and back to 1280px -> draft must survive!
		await page.setViewportSize({ width: 1280, height: 800 });
		await page.locator("#step5-tab-3").click();
		const yamlArea = page.getByLabel("Graph A Ontology YAML Editor");
		const origYaml = await yamlArea.inputValue();
		await yamlArea.fill(`${origYaml}\n# mobile-resize-preserved`);
		await page.setViewportSize({ width: 390, height: 844 });
		await assertLayoutAndA11y(page);
		await page.setViewportSize({ width: 1280, height: 800 });
		expect(await yamlArea.inputValue()).toContain("# mobile-resize-preserved");
		await page.getByRole("button", { name: "Discard Draft" }).click();

		// Switch back to mobile 390x844 and check Step 7 view-only SQL readout (no overflow)
		await page.setViewportSize({ width: 390, height: 844 });
		await page.getByRole("button", { name: /Step \d+ of 7/i }).click();
		await page
			.locator("#mobile-stepper-dialog")
			.getByRole("link", { name: /7 Storage/i })
			.click();
		await assertLayoutAndA11y(page);

		guard.assertClean(false);
	});
});
