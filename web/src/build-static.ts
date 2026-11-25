import {
	copyFileSync,
	existsSync,
	mkdirSync,
	readFileSync,
	readdirSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { resolve } from "node:path";

const FONT_FILES = [
	"dm-sans-latin.woff2",
	"instrument-serif-latin.woff2",
	"instrument-serif-italic-latin.woff2",
	"jetbrains-mono-latin.woff2",
] as const;

export function cleanStaticDirPreserveGitkeep(staticDir: string): void {
	if (!existsSync(staticDir)) {
		mkdirSync(staticDir, { recursive: true });
		return;
	}
	for (const entry of readdirSync(staticDir, { withFileTypes: true })) {
		if (entry.name === ".gitkeep") {
			continue;
		}
		rmSync(resolve(staticDir, entry.name), { recursive: true, force: true });
	}
}

export function buildStaticAssets(): void {
	const webRoot = resolve(import.meta.dir, "..");
	const repoRoot = resolve(webRoot, "..");
	const staticDir = resolve(repoRoot, "src", "opendv", "api", "static");
	const assetsDir = resolve(staticDir, "assets");
	const fontsDir = resolve(assetsDir, "fonts");

	cleanStaticDirPreserveGitkeep(staticDir);
	mkdirSync(fontsDir, { recursive: true });

	const entrypoint = resolve(webRoot, "src", "main.tsx");
	const proc = Bun.spawnSync(
		[
			process.execPath,
			"build",
			entrypoint,
			"--target=browser",
			"--format=esm",
			"--splitting",
			"--minify",
			"--public-path=/assets/",
			`--outdir=${assetsDir}`,
		],
		{ cwd: webRoot, stdout: "pipe", stderr: "pipe" },
	);

	if (proc.exitCode !== 0) {
		const errText = proc.stderr.toString("utf-8").trim();
		const outText = proc.stdout.toString("utf-8").trim();
		throw new Error(
			`Bun build failed (exit ${proc.exitCode}): ${errText || outText}`,
		);
	}

	copyFileSync(
		resolve(webRoot, "index.html"),
		resolve(staticDir, "index.html"),
	);

	const xyflowCssPath = resolve(
		webRoot,
		"node_modules",
		"@xyflow",
		"react",
		"dist",
		"style.css",
	);
	const tokensCssPath = resolve(webRoot, "src", "styles", "tokens.css");
	const combinedCss = `${readFileSync(xyflowCssPath, "utf-8")}\n${readFileSync(tokensCssPath, "utf-8")}`;
	writeFileSync(resolve(assetsDir, "tokens.css"), combinedCss, "utf-8");

	const elkWorkerPath = resolve(
		webRoot,
		"node_modules",
		"elkjs",
		"lib",
		"elk-worker.min.js",
	);
	copyFileSync(elkWorkerPath, resolve(assetsDir, "elk-worker.js"));

	for (const fontFile of FONT_FILES) {
		const srcFont = resolve(webRoot, "public", "fonts", fontFile);
		if (!existsSync(srcFont)) {
			throw new Error(`Missing bundled font file: ${srcFont}`);
		}
		copyFileSync(srcFont, resolve(fontsDir, fontFile));
	}

	console.log(`Built Open-DV V2 static SPA -> ${staticDir}`);
}

if (import.meta.main) {
	buildStaticAssets();
}
