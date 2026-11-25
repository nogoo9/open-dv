import { beforeAll, describe, expect, test } from "bun:test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { type BuildSiteResult, buildSite } from "../src/build";

const APP_ROOT = resolve(import.meta.dir, "..");
const DIST_DIR = join(APP_ROOT, "dist");
const BASE_PATH = "/open-dv";
const SKIP_DIRS = new Set(["node_modules", "dist"]);

const REQUIRED_FILES = [
	".nojekyll",
	"index.html",
	"tour/index.html",
	"404.html",
	"sitemap.xml",
	"robots.txt",
	"favicon.svg",
	"assets/site.css",
	"assets/hydrate.js",
	"video/opendv-tour-en.vtt",
	"video/opendv-tour-en.srt",
	"video/opendv-tour-poster.webp",
	"video/youtube-thumbnail-1280x720.png",
	"video/youtube-chapters.txt",
] as const;

const SHOT_SLUGS = [
	"shot-01-step1-baseline",
	"shot-02-step2-sources",
	"shot-03a-structured-preview",
	"shot-03b-parsed-chunks",
	"shot-04a-agent-setup-gate",
	"shot-04b-agent-discovery",
	"shot-04c-citation-fibo",
	"shot-05-diff-and-graph",
	"shot-06-pipeline-console",
	"shot-06b-dv-schema-explorer",
	"shot-06c-triage-wave-diff",
	"shot-07-session-logs",
] as const;

const wb = ["W", "B-"].join("");
const vz = ["VI", "Z-"].join("");
const BANNED_PATTERNS: readonly RegExp[] = [
	new RegExp(`${wb}[01][0-9]`),
	new RegExp(`${vz}[01][0-9]`),
	new RegExp(`${vz}(?:PIPE|STEP|GATE)-`),
	new RegExp(`\\b${["work", "bench_view_id"].join("")}\\b`),
	new RegExp(`\\b${["vi", "z_id"].join("")}\\b`),
	new RegExp(`\\b${["use", "LegacyShell"].join("")}\\b`),
	new RegExp(`\\b${["execute_", "pe_sandbox_query"].join("")}\\b`),
	new RegExp(["docs/10-", "decoupled"].join("")),
	new RegExp(`\\b${["87", "87"].join("")}\\b`),
	new RegExp(`\\b${["4\\.2", "k"].join("")}\\b`, "i"),
	new RegExp(`\\b${["Ru", "st"].join("")}\\b`),
	new RegExp(`\\b${["opendv", "ctl"].join("")}\\b`, "i"),
	new RegExp(`\\b${["Cost", " Meter"].join("")}\\b`, "i"),
	new RegExp(`\\b${["Session", " Logs"].join("")}\\b`, "i"),
	new RegExp(`\\b${["6-", "Step"].join("")}\\b`, "i"),
	new RegExp(`\\b${["Vault", "Blueprint"].join("")}\\b`, "i"),
	new RegExp(`\\b${["STAGE_", "0[0-7]"].join("")}\\b`),
	new RegExp(`@${["toon", "_v1"].join("")}\\b`),
	new RegExp(`\\b${["G", "[0-5]"].join("")}\\b`),
];

let buildResult: BuildSiteResult;

const readDist = (...parts: string[]): string =>
	readFileSync(join(DIST_DIR, ...parts), "utf-8");

function walkFiles(dir: string): string[] {
	if (!existsSync(dir)) return [];
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = join(dir, entry.name);
		if (entry.isDirectory() && !SKIP_DIRS.has(entry.name)) {
			out.push(...walkFiles(full));
		} else if (entry.isFile()) {
			out.push(full);
		}
	}
	return out.sort();
}

function collectHtmlUrls(html: string): string[] {
	const urls: string[] = [];
	for (const m of html.matchAll(/\b(?:href|src|poster)="([^"]*)"/gi)) {
		if (m[1] !== undefined) urls.push(m[1]);
	}
	for (const m of html.matchAll(/\bsrcset="([^"]*)"/gi)) {
		for (const item of (m[1] ?? "").split(",")) {
			const u = item.trim().split(/\s+/)[0];
			if (u) urls.push(u);
		}
	}
	return urls;
}

function collectIds(html: string): Set<string> {
	return new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1] ?? ""));
}

function assertDistUrl(
	url: string,
	pageIds: ReadonlySet<string>,
	indexIds: ReadonlySet<string>,
): void {
	if (/^(?:https?:|mailto:|data:|\/\/)/i.test(url)) return;
	if (url.startsWith("#")) {
		expect(pageIds.has(url.slice(1))).toBe(true);
		return;
	}
	const [pathOnly = "", hashPart] = url.split("#");
	expect(pathOnly.startsWith(`${BASE_PATH}/`)).toBe(true);
	const rel = pathOnly.slice(BASE_PATH.length + 1);
	const target =
		rel === "" || rel.endsWith("/")
			? join(DIST_DIR, rel, "index.html")
			: join(DIST_DIR, rel);
	expect(existsSync(target)).toBe(true);
	if (rel === "" && hashPart) expect(indexIds.has(hashPart)).toBe(true);
}

beforeAll(async () => {
	buildResult = await buildSite({ basePath: BASE_PATH });
});

describe("@opendv/website static build", () => {
	test("dist/ contains all static HTML, assets, fonts, 12 canonical 2x screenshots, and video", () => {
		expect(buildResult.basePath).toBe(BASE_PATH);
		expect(buildResult.outDir).toBe(DIST_DIR);

		for (const rel of REQUIRED_FILES) {
			const p = join(DIST_DIR, rel);
			expect(existsSync(p)).toBe(true);
			if (rel !== ".nojekyll") expect(statSync(p).size).toBeGreaterThan(0);
		}

		const fonts = readdirSync(join(DIST_DIR, "fonts")).filter((f) =>
			f.endsWith(".woff2"),
		);
		expect(fonts).toHaveLength(4);
		for (const f of fonts) {
			expect(statSync(join(DIST_DIR, "fonts", f)).size).toBeGreaterThan(0);
		}

		const indexHtml = readDist("index.html");
		for (const slug of SHOT_SLUGS) {
			for (const ext of ["webp", "png"] as const) {
				const p = join(DIST_DIR, "screenshots", `${slug}.${ext}`);
				expect(existsSync(p)).toBe(true);
				expect(statSync(p).size).toBeGreaterThan(0);
				expect(indexHtml).toContain(`/screenshots/${slug}.${ext}`);
			}
		}

		const mp4 = join(DIST_DIR, "video", "opendv-walkthrough-1080p60.mp4");
		expect(existsSync(mp4)).toBe(true);
		const bytes = statSync(mp4).size;
		expect(bytes).toBeGreaterThan(0);
		expect(bytes).toBeLessThan(25 * 1024 * 1024);
	});

	test('light-theme-only metadata and hidden="until-found" tabpanels (12 tour + 5 dialect)', () => {
		const indexHtml = readDist("index.html");
		const tourHtml = readDist("tour", "index.html");
		const siteCss = readDist("assets", "site.css");
		const hydrateJs = readDist("assets", "hydrate.js");

		for (const html of [indexHtml, tourHtml]) {
			expect(html).toContain('<meta name="color-scheme" content="light"');
		}
		for (const text of [indexHtml, tourHtml, siteCss, hydrateJs]) {
			expect(text).not.toContain("dark-mode-toggle");
			expect(text).not.toContain("light-dark(");
			expect(text.toLowerCase()).not.toContain("#0b1118");
			expect(text).not.toMatch(/prefers-color-scheme\s*:\s*dark/i);
		}

		const panels = indexHtml.match(/<div\b[^>]*\brole="tabpanel"[^>]*>/g) ?? [];
		expect(panels.length).toBeGreaterThanOrEqual(17);
		expect(
			panels.filter((t) => t.includes('hidden="until-found"')),
		).toHaveLength(15);
		for (const tag of panels) {
			expect(tag).not.toMatch(/\bhidden=""/);
			expect(tag).not.toMatch(/\bhidden(?!=)/);
		}
	});

	test("Claude DESIGN.md + WCAG AA tokens, 4-spoke SPIKE_MARK_SVG, and V2 7-Step 5-Zone content", () => {
		const indexHtml = readDist("index.html");
		const tourHtml = readDist("tour", "index.html");
		const siteCssLower = readDist("assets", "site.css").toLowerCase();
		const faviconSvg = readDist("favicon.svg");

		expect(siteCssLower).toContain("#faf9f5");
		expect(siteCssLower).toContain("#cc785c");
		expect(siteCssLower).toContain("#181715");
		expect(siteCssLower).toContain("#57554f");
		expect(siteCssLower).toContain("#8f442d");
		expect(siteCssLower).toMatch(/--header-h:\s*(?:4rem|64px)\b/i);
		expect(siteCssLower).not.toContain("#f6f4ee");
		expect(siteCssLower).not.toContain("#1d4ed8");

		expect(indexHtml).toMatch(
			/viewBox="0 0 24 24"[^>]*>[\s\S]*?<line\b[^>]*x1="12"[^>]*y1="2\.5"/,
		);
		expect(indexHtml).not.toContain("M14 6.5L21 14L14 21.5L7 14L14 6.5Z");
		expect(indexHtml.toLowerCase()).not.toContain("#9a680c");
		expect(faviconSvg).not.toContain("M16 7L25 16L16 25L7 16L16 7Z");

		const docTexts = [
			indexHtml,
			tourHtml,
			readDist("video", "opendv-tour-en.vtt"),
			readDist("video", "opendv-tour-en.srt"),
			readDist("video", "youtube-chapters.txt"),
		];
		for (const text of docTexts) {
			expect(text).not.toMatch(/\b(?:4-Zone|four-zone)\b/i);
			expect(text).toMatch(/7-[Ss]tep/);
			expect(text).toContain("Run Explorer");
		}

		for (const html of [indexHtml, tourHtml]) {
			expect(html).toMatch(/5-Zone/i);
			expect(html).toMatch(/Z0\s*[–-]\s*Z4/i);
			expect(html).toContain("information_mart");
			expect(html).toMatch(/7-[Ss]tep/);
			expect(html).toContain("DV Schema Explorer");
			expect(html).toContain("Run Explorer");
		}

		expect(indexHtml).toMatch(/Budget Meter/i);
		expect(indexHtml).toContain("VaultSchemaIR v2.0");
		expect(indexHtml).toContain("--allow-remote-unauthenticated");
		expect(indexHtml).toContain("source attach");
		expect(indexHtml).toContain("ontology fibo-qa");
		expect(indexHtml).toContain("storage query");
		expect(indexHtml).toContain("vault logs");
		for (const gateId of ["L1", "V1_V5", "V6", "V6B", "V7", "V8"]) {
			expect(indexHtml).toContain(gateId);
		}
	});

	test("zero broken internal links, media paths, or CSS font URLs", () => {
		const indexHtml = readDist("index.html");
		const tourHtml = readDist("tour", "index.html");
		const siteCss = readDist("assets", "site.css");
		const indexIds = collectIds(indexHtml);
		const tourIds = collectIds(tourHtml);

		const indexUrls = collectHtmlUrls(indexHtml);
		expect(indexUrls.length).toBeGreaterThan(20);
		for (const url of indexUrls) assertDistUrl(url, indexIds, indexIds);

		const tourUrls = collectHtmlUrls(tourHtml);
		expect(tourUrls.length).toBeGreaterThan(10);
		for (const url of tourUrls) assertDistUrl(url, tourIds, indexIds);

		const cssUrls = [...siteCss.matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g)]
			.map((m) => m[2] ?? "")
			.filter((u) => u !== "" && !u.startsWith("data:") && !u.startsWith("#"));
		expect(cssUrls.length).toBeGreaterThanOrEqual(4);
		for (const u of cssUrls) {
			expect(existsSync(resolve(join(DIST_DIR, "assets"), u))).toBe(true);
		}
	});

	test("token budget, T9 guard, zero .py files, and zero legacy V1 claims", () => {
		const pyFiles = walkFiles(APP_ROOT).filter((f) => f.endsWith(".py"));
		expect(pyFiles).toEqual([]);

		const tsFiles = [
			...["src", "tests", "e2e"].flatMap((d) => walkFiles(join(APP_ROOT, d))),
			join(APP_ROOT, "playwright.config.ts"),
		]
			.filter((f) => existsSync(f) && (f.endsWith(".ts") || f.endsWith(".tsx")))
			.sort();
		expect(tsFiles.length).toBeGreaterThan(10);

		for (const f of tsFiles) {
			const content = readFileSync(f, "utf-8");
			expect(content.length).toBeLessThan(20_000);
			expect(Math.ceil(content.length / 3)).toBeLessThan(5_000);
			if (
				f.endsWith("tests/build.test.ts") ||
				f.endsWith("tests\\build.test.ts")
			)
				continue;
			for (const re of BANNED_PATTERNS) expect(re.test(content)).toBe(false);
		}

		for (const html of [
			readDist("index.html"),
			readDist("tour", "index.html"),
		]) {
			for (const re of BANNED_PATTERNS) expect(re.test(html)).toBe(false);
		}
	});
});
