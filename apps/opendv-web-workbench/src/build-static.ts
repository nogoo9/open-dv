/**
 * Static Asset Builder for Open-DV Web Workbench (`apps/opendv-web-workbench/src/build-static.ts`).
 *
 * Bundles the browser application into `workbench.js`, copies `workbench.css`,
 * and generates a production `index.html` emitted to:
 * 1. `apps/opendv-web-workbench/dist/static/`
 * 2. `apps/opendv-platform-api/src/opendv_platform_api/static/`
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

export interface StaticBuildOptions {
	workbenchRoot?: string | undefined;
	outDirs?: readonly string[] | undefined;
	minify?: boolean | undefined;
	silent?: boolean | undefined;
}

export interface StaticBuildResult {
	indexHtml: string;
	jsBytes: number;
	cssBytes: number;
	outDirs: string[];
}

export function generateStaticIndexHtml(): string {
	return `<!doctype html>
<html lang="en" data-theme="obsidian">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="referrer" content="strict-origin-when-cross-origin" />
    <!-- TODO(security): Enforce strict Content-Security-Policy header with script-src nonce and frame-ancestors 'self' at reverse proxy / FastAPI BFF layer -->
    <title>Open-DV Explorer</title>
    <link rel="stylesheet" href="/assets/workbench.css" />
  </head>
  <body class="dv-canvas-body">
    <div id="root"></div>
    <script type="module" src="/assets/workbench.js"></script>
  </body>
</html>
`;
}

export async function buildStaticWorkbenchAssets(
	options: StaticBuildOptions = {},
): Promise<StaticBuildResult> {
	const workbenchRoot = options.workbenchRoot ?? resolve(import.meta.dir, "..");
	const repoRoot = resolve(workbenchRoot, "../..");
	const minify = options.minify ?? true;

	const defaultOutDirs = [
		join(workbenchRoot, "dist", "static"),
		join(
			repoRoot,
			"apps",
			"opendv-platform-api",
			"src",
			"opendv_platform_api",
			"static",
		),
	];
	const outDirs = options.outDirs ? [...options.outDirs] : defaultOutDirs;

	let workbenchJs = "";
	const isTestEnv =
		typeof process !== "undefined" && process.env?.NODE_ENV === "test";

	if (!isTestEnv) {
		const buildResult = await Bun.build({
			entrypoints: [join(workbenchRoot, "src/index.ts")],
			target: "browser",
			format: "esm",
			minify,
		});
		if (buildResult.success && buildResult.outputs[0]) {
			workbenchJs = await buildResult.outputs[0].text();
		}
	}

	if (!workbenchJs) {
		const proc = Bun.spawnSync(
			[
				process.execPath,
				"build",
				join(workbenchRoot, "src/index.ts"),
				"--target=browser",
				"--format=esm",
				...(minify ? ["--minify"] : []),
			],
			{ cwd: workbenchRoot, stdout: "pipe", stderr: "pipe" },
		);
		if (proc.exitCode !== 0) {
			throw new Error(
				`Failed to bundle static workbench assets: ${proc.stderr.toString()}`,
			);
		}
		workbenchJs = proc.stdout.toString("utf-8");
	}

	if (!workbenchJs) {
		throw new Error("Bun.build produced no outputs for workbench.js");
	}

	const workbenchCss = readFileSync(
		join(workbenchRoot, "src/index.css"),
		"utf-8",
	);
	const indexHtml = generateStaticIndexHtml();

	for (const outDir of outDirs) {
		const assetsDir = join(outDir, "assets");
		mkdirSync(assetsDir, { recursive: true });
		writeFileSync(join(outDir, "index.html"), indexHtml, "utf-8");
		writeFileSync(join(assetsDir, "workbench.js"), workbenchJs, "utf-8");
		writeFileSync(join(assetsDir, "workbench.css"), workbenchCss, "utf-8");
	}

	const jsBytes = Buffer.byteLength(workbenchJs, "utf-8");
	const cssBytes = Buffer.byteLength(workbenchCss, "utf-8");

	if (!options.silent) {
		console.log(
			`Built Open-DV static workbench (${jsBytes} B JS, ${cssBytes} B CSS) -> ${outDirs.join(", ")}`,
		);
	}

	return {
		indexHtml,
		jsBytes,
		cssBytes,
		outDirs,
	};
}

if (import.meta.main) {
	await buildStaticWorkbenchAssets();
}
