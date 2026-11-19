import { type Page, expect, test } from "@playwright/test";

function trackHealth(page: Page): () => void {
	const errs: string[] = [];
	page.on("console", (m) => {
		if (m.type() === "error") errs.push(`console.error: ${m.text()}`);
	});
	page.on("pageerror", (e) => errs.push(`pageerror: ${e.message}`));
	page.on("response", (r) => {
		const { pathname } = new URL(r.url());
		if (pathname.startsWith("/open-dv/") && r.status() >= 400) {
			errs.push(`http ${r.status()}: ${pathname}`);
		}
	});
	return () => expect(errs).toEqual([]);
}

test.describe("Open-DV website E2E", () => {
	test("desktop, tablet, mobile layout, Claude DESIGN.md light theme, 64px header, and popover nav", async ({
		page,
	}) => {
		const assertHealthy = trackHealth(page);
		for (const [width, height] of [
			[1440, 900],
			[768, 1024],
			[390, 844],
		] as const) {
			await page.setViewportSize({ width, height });
			await page.goto("/open-dv/");
			await expect(page.locator('meta[name="color-scheme"]')).toHaveAttribute(
				"content",
				"light",
			);
			const state = await page.evaluate(() => ({
				scheme: getComputedStyle(document.documentElement).colorScheme,
				bg: getComputedStyle(document.body).backgroundColor,
				fit: document.documentElement.scrollWidth <= window.innerWidth,
			}));
			expect(state).toEqual({
				scheme: "light",
				bg: "rgb(250, 249, 245)",
				fit: true,
			});
			await expect(
				page.locator(
					"#dark-mode-toggle, [data-theme-toggle], .dark-mode-toggle",
				),
			).toHaveCount(0);

			if (width === 1440) {
				await expect(page.locator("h1")).toHaveCount(1);
				await expect(page.locator(".site-header")).toHaveClass(/is-wide/);
				const hdrBox = await page.locator("[data-site-header]").boundingBox();
				expect(hdrBox?.height ?? 0).toBeGreaterThanOrEqual(62);
				expect(hdrBox?.height ?? 0).toBeLessThanOrEqual(66);
				const links = page.locator("#site-menu a");
				await expect(links).toHaveCount(7);
				for (const link of await links.all()) await expect(link).toBeVisible();
			}
			if (width === 390) {
				const btn = page.locator('[popovertarget="site-menu"]');
				await expect(btn).toBeVisible();
				await btn.click();
				await expect(page.locator("#site-menu:popover-open")).toBeVisible();
				await page.locator('#site-menu a[href="#verifier"]').click();
				await expect(page.locator("#site-menu:popover-open")).toHaveCount(0);
				expect(page.url()).toContain("#verifier");
			}
		}
		assertHealthy();
	});

	test("all 3 SVG diagrams and prefers-reduced-motion", async ({
		browser,
		page,
	}) => {
		const assertHealthy = trackHealth(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto("/open-dv/");

		const fw = page.locator('[data-diagram="vault-firewall"]');
		await page.locator('[data-firewall-select="naive"]').click();
		await expect(fw).toHaveAttribute("data-firewall-mode", "naive");

		const pauseBtn = page.locator('[data-diagram-pause="vault-firewall"]');
		await expect(pauseBtn).toHaveText("Pause");
		expect(await pauseBtn.getAttribute("aria-pressed")).toBeNull();
		await pauseBtn.click();
		await expect(pauseBtn).toHaveText("Play");
		await expect(fw).toHaveAttribute("data-paused", "true");
		expect(await pauseBtn.getAttribute("aria-pressed")).toBeNull();

		await page.locator('[data-scenario-tab="hybrid-drift"]').click();
		await expect(
			page.locator('[data-diagram="inference-matrix"]'),
		).toHaveAttribute("data-active-scenario", "hybrid-drift");
		const hybrid = page.locator('[data-scenario-panel="hybrid-drift"]');
		await expect(hybrid).toBeVisible();
		await expect(hybrid).toContainText("HYBRID_PER_SOURCE");

		const sg = page.locator('[data-diagram="six-gate-verifier"]');
		await page.locator('[data-fault-btn="fault-nullable-fk"]').click();
		await expect(
			page.locator('[data-fault-detail="fault-nullable-fk"]'),
		).toContainText("G1|AUTO-FIX|ALG_A4");

		await page.locator('[data-gate-select="G3"]').click();
		await expect(sg).toHaveAttribute("data-active-gate", "G3");
		await expect(page.locator('[data-gate-detail="G3"]')).toContainText(
			"NetworkX Bipartite",
		);

		const ctx = await browser.newContext({ reducedMotion: "reduce" });
		const rPage = await ctx.newPage();
		const assertReducedHealthy = trackHealth(rPage);
		await rPage.setViewportSize({ width: 1440, height: 900 });
		await rPage.goto("/open-dv/");
		const opacity = await rPage
			.locator('[data-diagram="vault-firewall"] .dia-opendv-branch')
			.evaluate((el) => getComputedStyle(el).opacity);
		expect(opacity).toBe("1");
		const box = await rPage
			.locator('[data-diagram="vault-firewall"] svg')
			.boundingBox();
		expect(box?.width ?? 0).toBeGreaterThan(200);
		assertReducedHealthy();
		await ctx.close();
		assertHealthy();
	});

	test("12 workbench 2x screenshots, tabs, pins, 5 dialect tabs, copy, and lightbox", async ({
		context,
		page,
	}) => {
		const assertHealthy = trackHealth(page);
		await context.grantPermissions(["clipboard-read", "clipboard-write"]);
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto("/open-dv/");

		const wbTabs = page.locator('[data-tabs-group="workbench"] [role="tab"]');
		await expect(wbTabs).toHaveCount(12);

		const img1 = page.locator("#wb-panel-step1-baseline img");
		await img1.scrollIntoViewIfNeeded();
		await expect
			.poll(() =>
				img1.evaluate(
					(i: HTMLImageElement) => i.complete && i.naturalWidth > 0,
				),
			)
			.toBe(true);

		await page.locator("#wb-tab-step2-sources").click();
		await expect(page.locator("#wb-tab-step2-sources")).toHaveAttribute(
			"aria-selected",
			"true",
		);
		await expect(page.locator("#wb-panel-step1-baseline")).toHaveAttribute(
			"hidden",
			"until-found",
		);
		await page.locator("#wb-tab-step2-sources").press("ArrowRight");
		const panel3 = page.locator("#wb-panel-step3a-structured");
		await expect(panel3).toBeVisible();

		const pin2 = panel3.locator("[data-pin-btn]").nth(1);
		const pinId = await pin2.getAttribute("data-pin-btn");
		expect(pinId).toBeTruthy();
		await pin2.click();
		await expect(pin2).toHaveAttribute("aria-pressed", "true");
		await expect(
			panel3.locator(`[data-pin-detail="${pinId}"]`),
		).toHaveAttribute("data-active", "true");

		for (const tabId of [
			"#wb-tab-dv-schema-explorer",
			"#wb-tab-session-logs",
		]) {
			const tab = page.locator(tabId);
			await tab.click();
			await expect(tab).toHaveAttribute("aria-selected", "true");
			const targetId = await tab.getAttribute("data-tab-target");
			expect(targetId).toBeTruthy();
			const panelImg = page.locator(`#${targetId} img`);
			await panelImg.scrollIntoViewIfNeeded();
			await expect
				.poll(() =>
					panelImg.evaluate(
						(i: HTMLImageElement) => i.complete && i.naturalWidth > 0,
					),
				)
				.toBe(true);
		}

		for (const tab of await wbTabs.all()) {
			await tab.click();
			const targetId = await tab.getAttribute("data-tab-target");
			const panelImg = page.locator(`#${targetId} img`);
			await panelImg.scrollIntoViewIfNeeded();
			await expect
				.poll(() =>
					panelImg.evaluate(
						(i: HTMLImageElement) => i.complete && i.naturalWidth > 0,
					),
				)
				.toBe(true);
		}

		const dialectTabs = page.locator(
			'[data-tabs-group="dialects"] [role="tab"]',
		);
		await expect(dialectTabs).toHaveCount(5);
		await page.getByRole("tab", { name: "Databricks Delta SQL" }).click();
		const delta = page.locator("#dialect-panel-databricks-sql");
		await expect(delta).toBeVisible();
		await expect(delta).toContainText("USING DELTA");
		const copyBtn = delta.locator("[data-copy-text]");
		await copyBtn.click();
		await expect(copyBtn).toHaveText("Copied!");

		const dlg = page.locator("#media-lightbox");
		const isOpen = () => dlg.evaluate((d: HTMLDialogElement) => d.open);
		await page.locator('[data-lightbox-open="inference-matrix"]').click();
		await expect.poll(isOpen).toBe(true);
		await expect(dlg.locator("[data-lightbox-stage] svg")).toBeVisible();
		await page.keyboard.press("Escape");
		await expect.poll(isOpen).toBe(false);

		await page.locator("#wb-tab-step3a-structured").click();
		await panel3.locator("[data-lightbox-image]").click();
		await expect.poll(isOpen).toBe(true);
		await expect
			.poll(() =>
				dlg
					.locator("[data-lightbox-stage] img")
					.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0),
			)
			.toBe(true);
		await page.mouse.click(5, 5);
		await expect.poll(isOpen).toBe(false);
		assertHealthy();
	});

	test("video tour metadata, WebVTT cues, chapter seek, and transcript filter", async ({
		page,
	}) => {
		const assertHealthy = trackHealth(page);
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto("/open-dv/tour/");
		await expect(page.locator("h1")).toHaveCount(1);

		const video = page.locator("#tour-page-video");
		await expect
			.poll(() =>
				video.evaluate(
					(v: HTMLVideoElement) => v.readyState >= 1 && v.duration > 150,
				),
			)
			.toBe(true);

		await video.evaluate((v: HTMLVideoElement) => {
			if (v.textTracks[0]) v.textTracks[0].mode = "hidden";
		});
		await expect
			.poll(() =>
				video.evaluate(
					(v: HTMLVideoElement) => v.textTracks[0]?.cues?.length ?? 0,
				),
			)
			.toBeGreaterThanOrEqual(6);

		await page.locator('[data-video-seek="62"]').click();
		await expect
			.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime))
			.toBeGreaterThanOrEqual(60);
		await video.evaluate((v: HTMLVideoElement) => v.pause());

		const rows = page.locator("[data-chapter-row]");
		const total = await rows.count();
		expect(total).toBeGreaterThanOrEqual(6);

		const filter = page.locator("[data-transcript-filter]");
		await filter.fill("axi");
		const visibleRows = page.locator("[data-chapter-row]:visible");
		const matched = await visibleRows.count();
		expect(matched).toBeGreaterThanOrEqual(1);
		await expect(page.locator("[data-transcript-count]")).toContainText(
			`Showing ${matched} of ${total} chapters`,
		);

		await filter.fill("zzznomatch");
		await expect(visibleRows).toHaveCount(0);
		await expect(page.locator("[data-transcript-count]")).toContainText(
			`Showing 0 of ${total} chapters`,
		);

		await filter.fill("");
		await expect(visibleRows).toHaveCount(total);
		assertHealthy();
	});
});
