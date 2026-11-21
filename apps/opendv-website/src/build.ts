import {
	cpSync,
	existsSync,
	mkdirSync,
	readFileSync,
	readdirSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { join, relative, resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DocumentShell } from "./components/Layout";
import { getBasePath, withBase } from "./lib/base-path";
import { HomePage } from "./pages/HomePage";
import { TourPage } from "./pages/TourPage";

export interface BuildSiteOptions {
	readonly outDir?: string | undefined;
	readonly basePath?: string | undefined;
}

export interface BuildSiteResult {
	readonly outDir: string;
	readonly basePath: string;
	readonly files: string[];
}

const CANONICAL_SITE_URL = "https://nogoo9.github.io/open-dv/";

const FAVICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
  <rect x="1" y="1" width="22" height="22" rx="6" fill="#faf9f5" stroke="#e6dfd8" stroke-width="1.5"/>
  <g stroke="#141413" stroke-width="2.1" stroke-linecap="round">
    <line x1="12" y1="4.5" x2="12" y2="19.5"/>
    <line x1="4.5" y1="12" x2="19.5" y2="12"/>
    <line x1="6.7" y1="6.7" x2="17.3" y2="17.3"/>
    <line x1="17.3" y1="6.7" x2="6.7" y2="17.3"/>
  </g>
</svg>
`;

const ROBOTS_TXT = `User-agent: *
Allow: /

Sitemap: ${CANONICAL_SITE_URL}sitemap.xml
`;

const SITEMAP_XML = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.w3.org/2000/svg">
  <url>
    <loc>${CANONICAL_SITE_URL}</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>${CANONICAL_SITE_URL}tour/</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>
`;

const STYLE_FILES = [
	"tokens.css",
	"layout.css",
	"components.css",
	"diagrams.css",
] as const;

function collectFiles(dir: string, rootDir: string, out: string[]): void {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const fullPath = join(dir, entry.name);
		if (entry.isDirectory()) {
			collectFiles(fullPath, rootDir, out);
		} else if (entry.isFile()) {
			out.push(relative(rootDir, fullPath).replaceAll("\\", "/"));
		}
	}
}

function restoreUntilFound(html: string): string {
	return html
		.replace(
			/(<div\b[^>]*\brole="tabpanel"[^>]*?\s)hidden=""/g,
			'$1hidden="until-found"',
		)
		.replace(
			/(<div\b[^>]*?)\bhidden=""([^>]*\brole="tabpanel")/g,
			'$1hidden="until-found"$2',
		);
}

function buildNotFoundHtml(basePath: string): string {
	const body = createElement(
		"section",
		{ className: "band hero-band" },
		createElement(
			"div",
			{ className: "wrap" },
			createElement("p", { className: "eyebrow" }, "404 · Route Not Found"),
			createElement("h1", null, "This blueprint route does not exist."),
			createElement(
				"p",
				{ className: "lede" },
				"Return to the Open-DV monograph or jump straight to the narrated technical walkthrough.",
			),
			createElement(
				"div",
				{ className: "btn-row" },
				createElement(
					"a",
					{ className: "btn btn-primary", href: withBase("/", basePath) },
					"Return Home",
				),
				createElement(
					"a",
					{
						className: "btn btn-secondary",
						href: withBase("/tour/", basePath),
					},
					"Open Video Tour",
				),
			),
		),
	);

	const markup = renderToStaticMarkup(
		createElement(
			DocumentShell,
			{
				title: "404 — Page Not Found | open-dv",
				description: "Requested page was not found on open-dv.",
				canonicalPath: "/404.html",
				activeRoute: "home",
				basePath,
			},
			body,
		),
	);

	return `<!DOCTYPE html>${markup}`;
}

export async function buildSite(
	options: BuildSiteOptions = {},
): Promise<BuildSiteResult> {
	const srcDir = import.meta.dir;
	const appRoot = resolve(srcDir, "..");
	const outDir = resolve(options.outDir ?? join(appRoot, "dist"));
	const basePath = getBasePath(options.basePath);

	rmSync(outDir, { recursive: true, force: true });
	mkdirSync(outDir, { recursive: true });
	mkdirSync(join(outDir, "tour"), { recursive: true });
	mkdirSync(join(outDir, "assets"), { recursive: true });

	const homeHtml = restoreUntilFound(
		`<!DOCTYPE html>${renderToStaticMarkup(
			createElement(HomePage, { basePath }),
		)}`,
	);
	const tourHtml = restoreUntilFound(
		`<!DOCTYPE html>${renderToStaticMarkup(
			createElement(TourPage, { basePath }),
		)}`,
	);
	const notFoundHtml = buildNotFoundHtml(basePath);

	writeFileSync(join(outDir, "index.html"), homeHtml, "utf-8");
	writeFileSync(join(outDir, "tour", "index.html"), tourHtml, "utf-8");
	writeFileSync(join(outDir, "404.html"), notFoundHtml, "utf-8");
	writeFileSync(join(outDir, ".nojekyll"), "", "utf-8");
	writeFileSync(join(outDir, "favicon.svg"), FAVICON_SVG, "utf-8");
	writeFileSync(join(outDir, "robots.txt"), ROBOTS_TXT, "utf-8");
	writeFileSync(join(outDir, "sitemap.xml"), SITEMAP_XML, "utf-8");

	const cssBundle = STYLE_FILES.map((fileName) =>
		readFileSync(join(srcDir, "styles", fileName), "utf-8"),
	).join("\n\n");
	writeFileSync(join(outDir, "assets", "site.css"), cssBundle, "utf-8");

	const hydrateEntry = join(srcDir, "client", "hydrate.ts");
	if (existsSync(hydrateEntry)) {
		const buildResult = await Bun.build({
			entrypoints: [hydrateEntry],
			target: "browser",
			format: "esm",
			minify: true,
		});
		if (!buildResult.success) {
			const messages = buildResult.logs.map((log) => String(log)).join("; ");
			throw new Error(`Failed to bundle client/hydrate.ts: ${messages}`);
		}
		const entryOutput =
			buildResult.outputs.find((output) => output.kind === "entry-point") ??
			buildResult.outputs[0];
		if (!entryOutput) {
			throw new Error("Bun.build produced no output for client/hydrate.ts");
		}
		const jsCode = await entryOutput.text();
		writeFileSync(join(outDir, "assets", "hydrate.js"), jsCode, "utf-8");
	}

	const publicDir = join(appRoot, "public");
	if (existsSync(publicDir)) {
		cpSync(publicDir, outDir, { recursive: true, force: true });
	}

	const files: string[] = [];
	collectFiles(outDir, outDir, files);
	files.sort();

	return {
		outDir,
		basePath,
		files,
	};
}

if (import.meta.main) {
	const result = await buildSite();
	console.log(
		`Built ${result.files.length} files (basePath="${result.basePath || "/"}") -> ${result.outDir}`,
	);
}
