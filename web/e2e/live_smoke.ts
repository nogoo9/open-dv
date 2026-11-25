import { type ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import {
	cpSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	readdirSync,
	rmSync,
	statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type Page, chromium, expect } from "@playwright/test";

const e2eDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(e2eDir, "../..");
const publicAmDir = resolve(repoRoot, "data/public_am");
const PORT = 8420;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const PROVIDER = process.env.OPENDV_LIVE_PROVIDER ?? "agy";

interface HostFileSnapshot {
	name: string;
	size: number;
	mtimeMs: number;
}

function snapshotDir(dir: string): HostFileSnapshot[] {
	if (!dir || !existsSync(dir)) return [];
	return readdirSync(dir)
		.sort()
		.map((name) => {
			const st = statSync(join(dir, name));
			return { name, size: st.size, mtimeMs: st.mtimeMs };
		});
}

async function waitForHealth(url: string, timeoutMs: number): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		try {
			const res = await fetch(url);
			if (res.ok) return;
		} catch {
			// Server still booting
		}
		await new Promise((r) => setTimeout(r, 300));
	}
	throw new Error(`Timed out waiting for ${url}`);
}

async function assertCleanLayout(page: Page, label: string): Promise<void> {
	const m = await page.evaluate(() => ({
		scrollWidth: document.documentElement.scrollWidth,
		innerWidth: window.innerWidth,
	}));
	expect(
		m.scrollWidth,
		`${label}: horizontal overflow ${m.scrollWidth} > ${m.innerWidth}`,
	).toBeLessThanOrEqual(m.innerWidth);
}

async function killChild(proc: ChildProcessWithoutNullStreams): Promise<void> {
	if (proc.exitCode !== null) return;
	if (process.platform === "win32" && proc.pid) {
		spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
			stdio: "ignore",
		});
	} else {
		proc.kill("SIGTERM");
	}
	await new Promise<void>((res) => {
		const timer = setTimeout(res, 3_000);
		proc.once("exit", () => {
			clearTimeout(timer);
			res();
		});
	});
}

async function resolveStep4IfHolding(page: Page): Promise<void> {
	const ansBtn = page.getByRole("button", {
		name: "Submit Answer & Resume Discovery →",
	});
	if (await ansBtn.isVisible().catch(() => false)) {
		const nextQ = page.getByRole("button", { name: "Next", exact: true });
		for (let q = 0; q < 10; q++) {
			const customBox = page.getByPlaceholder(
				"Enter plain-English Steward guidance...",
			);
			if (await customBox.isVisible().catch(() => false)) {
				const curVal = await customBox.inputValue();
				if (!curVal.trim()) {
					await customBox.fill(
						"Confirm primary natural business identifier per source schema.",
					);
				}
			}
			if (await nextQ.isEnabled().catch(() => false)) {
				await nextQ.click();
				await page.waitForTimeout(100);
			} else {
				break;
			}
		}
		await expect(ansBtn).toBeEnabled({ timeout: 10_000 });
		await Promise.all([
			page.waitForResponse(
				(res) =>
					res.url().includes("/answers") && res.request().method() === "POST",
			),
			ansBtn.click(),
		]);
		await page.waitForTimeout(500);
	}
	const resumeBtn = page.getByRole("button", { name: "▶ Resume Discovery →" });
	if (await resumeBtn.isVisible().catch(() => false)) {
		await resumeBtn.click();
		await page.waitForTimeout(500);
	}
}

async function resolveStep6IfHolding(page: Page): Promise<void> {
	const decTab = page.locator("#guided-tab-decisions");
	if (await decTab.isVisible().catch(() => false)) {
		await decTab.click();
	}
	const holdBtn = page
		.getByRole("button", { name: /Submit Choice & Resume/i })
		.first();
	if (await holdBtn.isVisible().catch(() => false)) {
		const firstRadio = page.locator("main input[type='radio']").first();
		if (await firstRadio.isVisible().catch(() => false)) {
			await firstRadio.check();
		}
		const customHold = page
			.getByPlaceholder("Or write a custom answer...")
			.first();
		if (await customHold.isVisible().catch(() => false)) {
			await customHold.fill(
				"Use holding_id for hub_holding_13f BK, non-null ticker for hub_security BK (never nullable cusip), and composite (series_id, observation_date) or MAS mode with child_dependent_keys observation_date for treasury_rates; NEVER put hk_* in Link source_bindings[*].col_mappings (put raw source column names in participants[*].source_col_names and empty or identical raw column mappings in link col_mappings); omit applied_dts_col, observation_date, and BKs from Satellite attributes; keep clarification_questions empty.",
			);
		}
		await expect(holdBtn).toBeEnabled({ timeout: 10_000 });
		await Promise.all([
			page.waitForResponse(
				(res) =>
					res.url().includes("/decisions") && res.request().method() === "POST",
			),
			holdBtn.click(),
		]);
		await page.waitForTimeout(500);
		return;
	}
	const horBtn = page
		.getByRole("button", { name: /Confirm Business Choice & Certify Wave/i })
		.first();
	if (await horBtn.isVisible().catch(() => false)) {
		const firstOpt = page.locator("main article input[type='radio']").first();
		if (await firstOpt.isVisible().catch(() => false)) {
			await firstOpt.check();
		}
		await expect(horBtn).toBeEnabled({ timeout: 10_000 });
		await Promise.all([
			page.waitForResponse(
				(res) =>
					res.url().includes("/horizon") && res.request().method() === "POST",
			),
			horBtn.click(),
		]);
		await page.waitForTimeout(500);
	}
}

async function runLiveSmoke(): Promise<void> {
	const hostLocalOpendv = join(process.env.LOCALAPPDATA ?? "", "opendv");
	const hostBefore = snapshotDir(hostLocalOpendv);

	try {
		const alreadyUp = await fetch(`${BASE_URL}/api/v1/health`);
		if (alreadyUp.ok) {
			throw new Error(`Port ${PORT} is already in use before live smoke`);
		}
	} catch (err) {
		if (err instanceof Error && err.message.includes("already in use")) {
			throw err;
		}
	}

	const liveTmp = mkdtempSync(join(tmpdir(), "opendv_live_"));
	const ws = join(liveTmp, "ws");
	const st = join(liveTmp, "state");
	const homeDir = join(liveTmp, "opendv_home");
	mkdirSync(join(ws, "public_am"), { recursive: true });
	mkdirSync(st, { recursive: true });
	mkdirSync(homeDir, { recursive: true });
	cpSync(
		join(publicAmDir, "am_sec_treasury_ibor.sqlite"),
		join(ws, "public_am", "am_sec_treasury_ibor.sqlite"),
	);
	cpSync(
		join(publicAmDir, "am_stewardship_domain_notes.md"),
		join(ws, "public_am", "am_stewardship_domain_notes.md"),
	);

	const serverPy = [
		"import os, pathlib, uvicorn",
		"from opendv.api import create_app",
		"from opendv.models import WorkspaceSettings",
		"from opendv.service.app_service import OpenDVService",
		'tmp = pathlib.Path(os.environ["OPENDV_LIVE_TMP"])',
		'svc = OpenDVService(tmp / "ws", state_dir=tmp / "state", harness=None)',
		`svc.update_settings(WorkspaceSettings(secret_backend="user_state_file", harness_provider=${JSON.stringify(PROVIDER)}, timeout_seconds=360))`,
		`uvicorn.run(create_app(svc), host="127.0.0.1", port=${PORT}, log_level="warning")`,
	].join("\n");

	const proc = spawn(
		"uv",
		["run", "--project", repoRoot, "python", "-c", serverPy],
		{
			cwd: repoRoot,
			env: {
				...process.env,
				OPENDV_LIVE_TMP: liveTmp,
				OPENDV_HOME: homeDir,
				XDG_STATE_HOME: st,
				OPENDV_WORKSPACE_ROOTS: [ws, repoRoot].join(delimiter),
			},
		},
	);
	const serverLogs: string[] = [];
	proc.stdout.on("data", (d: Buffer) => serverLogs.push(d.toString()));
	proc.stderr.on("data", (d: Buffer) => serverLogs.push(d.toString()));

	const browser = await chromium.launch({
		executablePath:
			process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ??
			"C:\\Users\\etern\\AppData\\Local\\ms-playwright\\chromium-1255\\chrome-win64\\chrome.exe",
		headless: true,
	});

	const consoleErrors: string[] = [];
	const pageErrors: string[] = [];
	const badResponses: { url: string; status: number }[] = [];

	try {
		await waitForHealth(`${BASE_URL}/api/v1/health`, 30_000);
		const context = await browser.newContext({
			viewport: { width: 1440, height: 920 },
			acceptDownloads: true,
		});
		const page = await context.newPage();
		page.on("console", (msg) => {
			if (msg.type() === "error") consoleErrors.push(msg.text());
		});
		page.on("pageerror", (err) => pageErrors.push(err.message));
		page.on("response", (res) => {
			if (res.status() >= 400) {
				badResponses.push({ url: res.url(), status: res.status() });
			}
		});

		try {
			await page.goto(BASE_URL);
			await expect(
				page.getByRole("heading", { name: /Step 1 · Scope & Seeds/i }),
			).toBeVisible();

			// Step 1
			await page.getByLabel("Project ID").fill("live-smoke-am");
			await page.getByLabel("Domain Namespace").fill("asset_management");
			await page.getByRole("radio", { name: /Blank Genesis/i }).check();
			await page.getByRole("button", { name: "Create Project" }).click();
			await expect(
				page.locator("main").getByRole("heading", { name: "live-smoke-am" }),
			).toBeVisible();

			// Step 2
			await page
				.getByRole("button", { name: "Proceed to Step 2 Sources →" })
				.click();
			await page.getByRole("button", { name: "SQLite Database" }).click();
			await page
				.getByLabel("Workspace File Path")
				.fill("public_am/am_sec_treasury_ibor.sqlite");
			await page.getByRole("button", { name: "Inspect Source" }).click();
			await expect(page.getByText(/Preview:\s+\d+\s+table\(s\)/)).toBeVisible({
				timeout: 15_000,
			});
			await page.getByRole("button", { name: "Attach Source" }).click();
			await expect(
				page
					.locator("main")
					.getByText(/am_sec_treasury_ibor\.sqlite/)
					.first(),
			).toBeVisible();

			await page
				.getByLabel("Choose source file to upload")
				.setInputFiles(join(ws, "public_am", "am_stewardship_domain_notes.md"));
			await expect(
				page
					.locator("main")
					.getByText(/am_stewardship_domain_notes\.md/)
					.first(),
			).toBeVisible({ timeout: 15_000 });

			// Step 4 Discovery + Live AgentIoDrawer SSE
			await page.getByRole("link", { name: /4 Discovery/i }).click();
			await page
				.getByLabel(/Initial Steward Hint/i)
				.fill(
					"Propose canonical concepts directly backed by the attached SQLite tables: holdings_13f (BK=holding_id), institutional_filers (BK=filer_cik), issuers (BK=cik), securities (BK=ticker; mark nullable cusip as non-BK is_business_key=False without SAL split in W1), treasury_rates (composite BK=(series_id, observation_date)), yield_curves (BK=curve_id). Record each BK grain and note am_stewardship_domain_notes.md inside concept descriptions without emitting AddSourceCitation ops, keep logical_type strictly within UTF8_STRING/INT64/DATE32/BOOLEAN (NEVER FLOAT64 or DOUBLE), and avoid holds or unbacked concepts.",
				);
			const [constructResp] = await Promise.all([
				page.waitForResponse(
					(res) =>
						/\/api\/v1\/projects\/live-smoke-am\/ontology\/construct(\?|$)/.test(
							res.url(),
						) && res.request().method() === "POST",
				),
				page.getByRole("button", { name: "Start Discovery" }).click(),
			]);
			const run4 = (await constructResp.json()) as { run_id: string };
			console.log(`[live_smoke] Step 4 started: ${run4.run_id}`);
			const drawer = page.getByLabel("Broker Agent I/O Trace Drawer");
			await expect(drawer).toBeVisible();
			const expandTrace = drawer.getByRole("button", {
				name: /Expand Trace/i,
			});
			if (await expandTrace.isVisible().catch(() => false)) {
				await expandTrace.click();
			}

			const deadline4 = Date.now() + 360_000;
			let step4Status = "";
			let lastLogged4 = "";
			while (Date.now() < deadline4) {
				const r = await page.request.get(
					`${BASE_URL}/api/v1/projects/live-smoke-am/ontology/runs/${run4.run_id}`,
				);
				const payload = (await r.json()) as { status?: string; error?: string };
				step4Status = payload.status ?? "";
				if (step4Status !== lastLogged4) {
					console.log(`[live_smoke] Step 4 status: ${step4Status}`);
					lastLogged4 = step4Status;
				}
				if (step4Status === "COMPLETED") break;
				if (step4Status === "FAILED") {
					const evs = await page.request
						.get(
							`${BASE_URL}/api/v1/projects/live-smoke-am/runs/${run4.run_id}/logs`,
						)
						.then((lr) => lr.text())
						.catch(() => "");
					throw new Error(
						`Step 4 live run failed: ${JSON.stringify(payload)} logs=${evs.slice(-1500)}`,
					);
				}
				if (step4Status === "HOLD_CHECKPOINTED") {
					await resolveStep4IfHolding(page);
				}
				await page.waitForTimeout(1_000);
			}
			expect(step4Status, "Step 4 live discovery should complete").toBe(
				"COMPLETED",
			);
			await expect(
				page.getByRole("button", { name: "Proceed to Step 5 Explorer →" }),
			).toBeEnabled({ timeout: 15_000 });
			await expect(drawer).toContainText(/Last-Event-ID:\s*[1-9]\d*/);

			// Step 5 Commit
			await page
				.getByRole("button", { name: "Proceed to Step 5 Explorer →" })
				.click();
			await page.locator("#step5-tab-4").click();
			await page.getByRole("button", { name: "Re-validate" }).click();
			await expect(
				page.getByRole("button", { name: "Re-validate" }),
			).toBeEnabled({ timeout: 15_000 });
			const apChecks = page.getByRole("checkbox", {
				name: /Sign ONT-AP Waiver/i,
			});
			for (let i = 0; i < (await apChecks.count()); i++) {
				await apChecks.nth(i).check();
			}
			const apInputs = page.locator(
				"main input[aria-label^='Waiver rationale for ']",
			);
			for (let i = 0; i < (await apInputs.count()); i++) {
				await apInputs
					.nth(i)
					.fill("Approved for live provider smoke baseline.");
			}
			const waiveQ = page.getByRole("checkbox", {
				name: /Explicitly waive Path B2 ungrounded quarantine/i,
			});
			if (
				(await waiveQ.isVisible()) &&
				(await waiveQ.isEnabled()) &&
				!(await waiveQ.isChecked())
			) {
				await waiveQ.check();
			}
			await page
				.getByPlaceholder(
					"Describe this ontology revision (min 20 chars if waiving Path B2 quarantine)...",
				)
				.fill(
					"Live provider smoke Graph A ontology baseline commit with steward approval.",
				);
			await page.getByRole("button", { name: /Validate & Commit v/i }).click();
			await expect(page.getByText(/Committed ont-/i).first()).toBeVisible({
				timeout: 15_000,
			});
			console.log("[live_smoke] Step 5 ontology committed");

			// Step 6 Live Synthesis
			await Promise.all([
				page.waitForResponse(
					(res) =>
						/\/api\/v1\/projects\/live-smoke-am\/runs(\?|$)/.test(res.url()) &&
						res.status() === 200,
				),
				page.waitForResponse(
					(res) =>
						/\/api\/v1\/projects\/live-smoke-am\/ontology(\?|$)/.test(
							res.url(),
						) && res.status() === 200,
				),
				page.getByRole("link", { name: /6 Vault Synthesis/i }).click(),
			]);
			const policyRadio = page.getByRole("radio", { name: "Quick" });
			await policyRadio.check();
			await expect(policyRadio).toBeChecked();
			let run6 = { run_id: "" };
			let step6Status = "";
			const deadline6 = Date.now() + 1_500_000;
			for (let step6Try = 0; step6Try < 2; step6Try++) {
				if (step6Try > 0) {
					const dismissBtn = page.getByRole("button", { name: "Dismiss" });
					if (await dismissBtn.isVisible().catch(() => false)) {
						await dismissBtn.click();
					}
				}
				const startOrRerunBtn = page.getByRole("button", {
					name: /Start Synthesis|Start \/ Re-run/i,
				});
				await expect(startOrRerunBtn).toBeEnabled({ timeout: 10_000 });
				const [run6Resp] = await Promise.all([
					page.waitForResponse(
						(res) =>
							/\/api\/v1\/projects\/live-smoke-am\/runs(\?|$)/.test(
								res.url(),
							) && res.request().method() === "POST",
					),
					startOrRerunBtn.click(),
				]);
				run6 = (await run6Resp.json()) as { run_id: string };
				console.log(`[live_smoke] Step 6 started: ${run6.run_id}`);
				const hintBtn = page.getByRole("button", { name: /💬 Hint/i });
				await hintBtn
					.waitFor({ state: "visible", timeout: 5_000 })
					.catch(() => {});
				if (await hintBtn.isVisible().catch(() => false)) {
					await hintBtn.click();
					const hintBox = page.getByLabel("Steward Hint");
					await hintBox
						.waitFor({ state: "visible", timeout: 3_000 })
						.catch(() => {});
					if (await hintBox.isVisible().catch(() => false)) {
						await hintBox.fill(
							"CRITICAL: On EVERY LinkParticipantSpec set fk_col_name to hk_<hub_slug> (NEVER raw column names like holding_id, filer_cik, cusip, or cik). Every Hub in a Link MUST also include a source_bindings entry on that Link table: hub_institutionalfiler binds institutional_filers and holdings_13f (filer_cik); hub_issuer binds issuers and securities (cik); hub_security uses non-null ticker as BK (never cusip) and binds securities and holdings_13f (ticker). Use composite BK (series_id, observation_date) or MAS mode for treasury_rates, curve_id for yield_curves, keep justifications empty, and return empty clarification_questions.",
						);
						await page
							.getByRole("button", { name: "Send Hint", exact: true })
							.click();
						await hintBox
							.waitFor({ state: "hidden", timeout: 5_000 })
							.catch(() => {});
						console.log("[live_smoke] Step 6 hint sent");
					}
				}

				const raiseBtn = page.getByRole("button", { name: /Raise Budget/i });
				if (await raiseBtn.isVisible().catch(() => false)) {
					await raiseBtn.click();
					const timeoutInput = page.getByLabel("+Timeout (s)");
					await timeoutInput
						.waitFor({ state: "visible", timeout: 3_000 })
						.catch(() => {});
					if (await timeoutInput.isVisible().catch(() => false)) {
						await timeoutInput.fill("600");
						await page
							.getByRole("button", {
								name: "Apply Budget Increase",
								exact: true,
							})
							.click();
						await timeoutInput
							.waitFor({ state: "hidden", timeout: 5_000 })
							.catch(() => {});
						console.log("[live_smoke] Step 6 budget raised (+600s)");
					}
				}

				step6Status = "";
				let lastLogged6 = "";
				while (Date.now() < deadline6) {
					const r = await page.request.get(
						`${BASE_URL}/api/v1/projects/live-smoke-am/runs/${run6.run_id}`,
					);
					const payload = (await r.json()) as {
						status?: string;
						phase?: string;
						attempt?: number;
						budget_turns_used?: number;
						error?: string;
					};
					step6Status = payload.status ?? "";
					const progressKey = `${step6Status}|${payload.phase ?? ""}|a${payload.attempt ?? 0}|t${payload.budget_turns_used ?? 0}`;
					if (progressKey !== lastLogged6) {
						console.log(`[live_smoke] Step 6: ${progressKey}`);
						lastLogged6 = progressKey;
					}
					if (step6Status === "COMPLETED") break;
					if (step6Status === "UNCERTIFIED_DRAFT") {
						const accBtn = page.getByRole("button", { name: /Accept Best/i });
						if (await accBtn.isEnabled().catch(() => false)) {
							await accBtn.click();
							await page.waitForTimeout(1_000);
							continue;
						}
					}
					if (step6Status === "FAILED" || step6Status === "UNCERTIFIED_DRAFT") {
						const mainTxt = await page
							.locator("main")
							.innerText()
							.catch(() => "");
						if (
							step6Status === "FAILED" &&
							step6Try === 0 &&
							/Invalid JSON|invalid structured output/i.test(mainTxt)
						) {
							console.warn(
								"[live_smoke] Retrying Step 6 once after transient LLM invalid JSON",
							);
							break;
						}
						const attSummary = await page.request
							.get(
								`${BASE_URL}/api/v1/projects/live-smoke-am/runs/${run6.run_id}/attempts`,
							)
							.then(async (ar) => {
								const list = (await ar.json()) as Array<{
									attempt?: number;
									energy_e?: number;
									diagnostics?: unknown[];
								}>;
								return JSON.stringify(
									list.map((a) => ({
										attempt: a.attempt,
										energy_e: a.energy_e,
										diagnostics: a.diagnostics,
									})),
								);
							})
							.catch(() => "");
						throw new Error(
							`Step 6 live run ended with ${step6Status}: ${JSON.stringify(payload)} attempts=${attSummary.slice(0, 6000)}`,
						);
					}
					if (step6Status === "HOLD_CHECKPOINTED") {
						await resolveStep6IfHolding(page);
					}
					await page.waitForTimeout(1_500);
				}
				if (step6Status === "COMPLETED") break;
			}
			expect(step6Status, "Step 6 live synthesis should complete").toBe(
				"COMPLETED",
			);
			await expect(
				page.getByRole("button", { name: "Proceed to Step 7 Target Build →" }),
			).toBeEnabled({ timeout: 20_000 });

			// Step 7 duckdb_local Compile -> Migrate -> Real SQL Query
			await page
				.getByRole("button", { name: "Proceed to Step 7 Target Build →" })
				.click();
			await page
				.getByRole("button", { name: /Compile Selected \(duckdb_local\)/i })
				.click();
			await expect(page.getByText(/Generated Files:/i)).toBeVisible({
				timeout: 20_000,
			});
			await page
				.getByRole("button", {
					name: /Migrate Selected Target \(duckdb_local\)/i,
				})
				.click();
			await expect(
				page.getByText(/Target duckdb_local wave \d+: rows_loaded=/i),
			).toBeVisible({ timeout: 30_000 });

			const sqlBox = page.getByLabel("Read-only DuckDB SQL Query");
			await sqlBox.fill(
				"SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault' AND table_name LIKE 'hub_%' ORDER BY table_name",
			);
			await page.getByRole("button", { name: "Run Query" }).click();
			const firstHubCell = page.locator("main table tbody tr td").first();
			await expect(firstHubCell).toHaveText(/^hub_/, { timeout: 15_000 });
			const firstHub = (await firstHubCell.innerText()).trim();
			expect(firstHub).toMatch(/^hub_/);

			await sqlBox.fill(
				`SELECT COUNT(*) AS row_count FROM raw_vault.${firstHub}`,
			);
			await page.getByRole("button", { name: "Run Query" }).click();
			const countCell = page.locator("main table tbody tr td").first();
			await expect(countCell).toHaveText(/^\d+$/, { timeout: 15_000 });
			const rowCount = Number.parseInt(
				(await countCell.innerText()).trim(),
				10,
			);
			expect(Number.isFinite(rowCount) && rowCount > 0).toBe(true);
			await assertCleanLayout(page, "Live Step 7");

			// RunExplorerPage verification
			await page.getByRole("link", { name: "Run Explorer" }).click();
			await expect(page.locator("#run-tab-payload")).toBeVisible();
			await expect
				.poll(
					async () =>
						page.locator("main select").first().locator("option").count(),
					{ timeout: 10_000 },
				)
				.toBeGreaterThanOrEqual(2);
			await assertCleanLayout(page, "Live RunExplorer");

			const shotDir = process.env.OPENDV_SCREENSHOT_DIR;
			if (shotDir) {
				mkdirSync(shotDir, { recursive: true });
				await page.screenshot({
					path: join(shotDir, "10_live_smoke_run_explorer.png"),
					fullPage: false,
				});
			}

			expect(
				consoleErrors,
				`console.error: ${consoleErrors.join("; ")}`,
			).toEqual([]);
			expect(pageErrors, `pageerror: ${pageErrors.join("; ")}`).toEqual([]);
			expect(
				badResponses,
				`HTTP >=400: ${JSON.stringify(badResponses)}`,
			).toEqual([]);
			console.log(
				`UAT-V27-LIVE-01 PASSED (provider=${PROVIDER}, step4=${run4.run_id}, step6=${run6.run_id}, table=raw_vault.${firstHub}, rows=${rowCount})`,
			);
		} catch (innerErr) {
			console.error(
				"Live smoke last <main> snapshot:\n",
				await page
					.locator("main")
					.innerText()
					.catch(() => ""),
			);
			throw innerErr;
		}
	} catch (err) {
		console.error("Live smoke server logs:\n", serverLogs.join(""));
		console.error("Live smoke consoleErrors:", consoleErrors);
		console.error("Live smoke badResponses:", badResponses);
		throw err;
	} finally {
		await browser.close();
		await killChild(proc);
		rmSync(liveTmp, { recursive: true, force: true });
		const hostAfter = snapshotDir(hostLocalOpendv);
		expect(
			hostAfter,
			"Host %LOCALAPPDATA%\\opendv must remain completely untouched",
		).toEqual(hostBefore);
	}
}

runLiveSmoke().catch((err) => {
	console.error(err);
	process.exit(1);
});
