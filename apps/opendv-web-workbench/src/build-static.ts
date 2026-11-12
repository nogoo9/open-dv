/**
 * Static Asset Builder for Open-DV Web Workbench (`apps/opendv-web-workbench/src/build-static.ts`).
 *
 * Bundles the browser application into `workbench.js`, copies `workbench.css`,
 * and generates a production `index.html` emitted to:
 * 1. `apps/opendv-web-workbench/dist/static/`
 * 2. `apps/opendv-platform-api/src/opendv_platform_api/static/`
 */

import {
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
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
    <meta name="referrer" content="no-referrer" />
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
	let programmaticError: Error | null = null;

	try {
		const buildResult = await Bun.build({
			entrypoints: [join(workbenchRoot, "src/index.ts")],
			target: "browser",
			format: "esm",
			minify,
		});
		const entryOutput =
			buildResult.outputs.find(
				(o) => (o as unknown as { kind?: string }).kind === "entry-point",
			) || buildResult.outputs[0];
		if (buildResult.success && entryOutput) {
			workbenchJs = await entryOutput.text();
		} else if (!buildResult.success) {
			const logs = buildResult.logs
				? buildResult.logs.map((l) => String(l)).join("; ")
				: "no logs";
			programmaticError = new Error(`Bun.build programmatic failure: ${logs}`);
		}
	} catch (err) {
		programmaticError = err instanceof Error ? err : new Error(String(err));
	}

	if (!workbenchJs) {
		const tempOutDir = mkdtempSync(join(tmpdir(), "opendv_wb_build_"));
		try {
			const proc = Bun.spawnSync(
				[
					process.execPath,
					"build",
					join(workbenchRoot, "src/index.ts"),
					"--target=browser",
					"--format=esm",
					`--outdir=${tempOutDir}`,
					...(minify ? ["--minify"] : []),
				],
				{ cwd: workbenchRoot, stdout: "pipe", stderr: "pipe" },
			);
			if (proc.exitCode !== 0) {
				const errorDetail = proc.stderr.toString("utf-8");
				throw new Error(
					`Failed to bundle static workbench assets (CLI exit ${proc.exitCode}): ${errorDetail}${programmaticError ? ` (Programmatic attempt error: ${programmaticError.message})` : ""}`,
				);
			}
			const mainJsPath = join(tempOutDir, "index.js");
			if (existsSync(mainJsPath)) {
				workbenchJs = readFileSync(mainJsPath, "utf-8");
			} else {
				throw new Error(
					`Bun build CLI completed but expected bundle '${mainJsPath}' was not generated.`,
				);
			}
		} finally {
			rmSync(tempOutDir, { recursive: true, force: true });
		}
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
		const ontologyDir = join(outDir, "ontology");
		mkdirSync(assetsDir, { recursive: true });
		mkdirSync(ontologyDir, { recursive: true });
		writeFileSync(join(outDir, "index.html"), indexHtml, "utf-8");
		writeFileSync(join(ontologyDir, "index.html"), indexHtml, "utf-8");
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
