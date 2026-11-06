/**
 * Multi-Target Release Packaging Script for `opendvctl`.
 *
 * Implements:
 * 1. Cross-compilation across 5 release targets using `bun build --compile --minify`:
 *    - bun-linux-x64   -> dist/release/opendvctl-linux-x64
 *    - bun-linux-arm64 -> dist/release/opendvctl-linux-arm64
 *    - bun-darwin-x64  -> dist/release/opendvctl-darwin-x64
 *    - bun-darwin-arm64-> dist/release/opendvctl-darwin-arm64
 *    - bun-windows-x64 -> dist/release/opendvctl-windows-x64.exe
 * 2. Archive packaging (.tar.gz for Linux/macOS, .zip for Windows).
 * 3. SHA-256 checksum generation into dist/release/SHA256SUMS.txt.
 * 4. Host native binary smoke test (--help, --version, --dump-cli-tree).
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { computeSha256, createTarGz, createZip } from "./src/archive";

export interface ReleaseTarget {
	id: string;
	bunTarget: string;
	binName: string;
	archiveName: string;
	archiveFormat: "tar.gz" | "zip";
}

export const RELEASE_TARGETS: ReleaseTarget[] = [
	{
		id: "linux-x64",
		bunTarget: "bun-linux-x64",
		binName: "opendvctl-linux-x64",
		archiveName: "opendvctl-linux-x64.tar.gz",
		archiveFormat: "tar.gz",
	},
	{
		id: "linux-arm64",
		bunTarget: "bun-linux-arm64",
		binName: "opendvctl-linux-arm64",
		archiveName: "opendvctl-linux-arm64.tar.gz",
		archiveFormat: "tar.gz",
	},
	{
		id: "darwin-x64",
		bunTarget: "bun-darwin-x64",
		binName: "opendvctl-darwin-x64",
		archiveName: "opendvctl-darwin-x64.tar.gz",
		archiveFormat: "tar.gz",
	},
	{
		id: "darwin-arm64",
		bunTarget: "bun-darwin-arm64",
		binName: "opendvctl-darwin-arm64",
		archiveName: "opendvctl-darwin-arm64.tar.gz",
		archiveFormat: "tar.gz",
	},
	{
		id: "windows-x64",
		bunTarget: "bun-windows-x64",
		binName: "opendvctl-windows-x64.exe",
		archiveName: "opendvctl-windows-x64.zip",
		archiveFormat: "zip",
	},
];

export function getHostTarget(): ReleaseTarget {
	const platform = process.platform;
	const arch = process.arch;

	if (platform === "win32") {
		return RELEASE_TARGETS.find((t) => t.id === "windows-x64")!;
	}
	if (platform === "darwin") {
		return arch === "arm64"
			? RELEASE_TARGETS.find((t) => t.id === "darwin-arm64")!
			: RELEASE_TARGETS.find((t) => t.id === "darwin-x64")!;
	}
	// Default to linux
	return arch === "arm64"
		? RELEASE_TARGETS.find((t) => t.id === "linux-arm64")!
		: RELEASE_TARGETS.find((t) => t.id === "linux-x64")!;
}

export function compileTarget(
	target: ReleaseTarget,
	entrypoint: string,
	outDir: string,
): { binPath: string; durationMs: number; sizeBytes: number } {
	const binPath = path.join(outDir, target.binName);
	const start = Date.now();

	const proc = spawnSync(
		"bun",
		[
			"build",
			"--compile",
			"--minify",
			`--target=${target.bunTarget}`,
			entrypoint,
			`--outfile=${binPath}`,
		],
		{ stdio: "inherit", shell: true },
	);

	if (proc.status !== 0) {
		throw new Error(
			`Compilation failed for target ${target.id} (${target.bunTarget}): exit code ${proc.status}`,
		);
	}

	const durationMs = Date.now() - start;
	const stat = fs.statSync(binPath);
	return { binPath, durationMs, sizeBytes: stat.size };
}

export function packageTarget(
	target: ReleaseTarget,
	outDir: string,
): { archivePath: string; sizeBytes: number } {
	const binPath = path.join(outDir, target.binName);
	const archivePath = path.join(outDir, target.archiveName);

	if (target.archiveFormat === "tar.gz") {
		createTarGz(binPath, target.binName, archivePath);
	} else {
		createZip(binPath, target.binName, archivePath);
	}

	const stat = fs.statSync(archivePath);
	return { archivePath, sizeBytes: stat.size };
}

export function generateChecksums(outDir: string): string {
	const checksumsPath = path.join(outDir, "SHA256SUMS.txt");
	const files = fs
		.readdirSync(outDir)
		.filter((f) => f !== "SHA256SUMS.txt" && !f.endsWith(".tmp"));

	files.sort();
	const lines = files.map((file) => {
		const filePath = path.join(outDir, file);
		const hash = computeSha256(filePath);
		return `${hash}  ${file}`;
	});

	const content = lines.join("\n") + "\n";
	fs.writeFileSync(checksumsPath, content, "utf-8");
	return checksumsPath;
}

export function runHostSmokeTest(binPath: string): void {
	console.log(`\nRunning host native smoke test on: ${binPath}`);

	// 1. --help
	const helpProc = spawnSync(binPath, ["--help"], { encoding: "utf-8" });
	if (helpProc.status !== 0) {
		throw new Error(`Smoke test failed: --help exited with ${helpProc.status}: ${helpProc.stderr}`);
	}
	if (!helpProc.stdout.includes("Usage: opendvctl")) {
		throw new Error(`Smoke test failed: --help output missing "Usage: opendvctl"`);
	}
	console.log("  [PASS] --help executed cleanly (exit code 0)");

	// 2. --version
	const verProc = spawnSync(binPath, ["--version"], { encoding: "utf-8" });
	if (verProc.status !== 0) {
		throw new Error(`Smoke test failed: --version exited with ${verProc.status}`);
	}
	if (!verProc.stdout.includes("opendvctl")) {
		throw new Error(`Smoke test failed: --version output missing "opendvctl"`);
	}
	console.log("  [PASS] --version executed cleanly (exit code 0)");

	// 3. --dump-cli-tree
	const treeProc = spawnSync(binPath, ["--dump-cli-tree"], { encoding: "utf-8" });
	if (treeProc.status !== 0) {
		throw new Error(`Smoke test failed: --dump-cli-tree exited with ${treeProc.status}`);
	}
	const treeJson = JSON.parse(treeProc.stdout);
	if (!treeJson.commands || !treeJson.contract_version) {
		throw new Error(`Smoke test failed: --dump-cli-tree missing commands or contract_version`);
	}
	console.log("  [PASS] --dump-cli-tree emitted valid canonical tree (exit code 0)");
}

export async function main(): Promise<void> {
	const projectRoot = path.resolve(__dirname);
	const entrypoint = path.join(projectRoot, "src", "main.ts");
	const outDir = path.join(projectRoot, "dist", "release");

	fs.mkdirSync(outDir, { recursive: true });

	console.log("=================================================");
	console.log("  Open-DV Bun Platform Client Release Packager   ");
	console.log("=================================================");
	console.log(`Source Entrypoint: ${entrypoint}`);
	console.log(`Output Directory:  ${outDir}\n`);

	const hostTarget = getHostTarget();
	let hostBinPath = "";

	// 1. Compile all targets
	for (const target of RELEASE_TARGETS) {
		process.stdout.write(`Compiling ${target.id.padEnd(14)} (${target.bunTarget})... `);
		const { binPath, durationMs, sizeBytes } = compileTarget(
			target,
			entrypoint,
			outDir,
		);
		const mb = (sizeBytes / (1024 * 1024)).toFixed(1);
		console.log(`DONE (${durationMs}ms, ${mb} MB)`);

		if (target.id === hostTarget.id) {
			hostBinPath = binPath;
		}

		// 2. Package into archive (.tar.gz / .zip)
		process.stdout.write(`Packaging ${target.archiveName.padEnd(30)}... `);
		const { sizeBytes: archSize } = packageTarget(target, outDir);
		const archMb = (archSize / (1024 * 1024)).toFixed(1);
		console.log(`DONE (${archMb} MB)`);
	}

	// 3. Compute SHA256 checksums
	console.log("\nGenerating SHA256 checksums...");
	const checksumsPath = generateChecksums(outDir);
	console.log(`Written checksums to: ${checksumsPath}`);

	// 4. Run host smoke test
	if (hostBinPath && fs.existsSync(hostBinPath)) {
		runHostSmokeTest(hostBinPath);
	}

	console.log("\nAll 5 release targets compiled, packaged, and verified successfully!");
}

if (import.meta.main) {
	main().catch((err) => {
		console.error("Release build failed:", err);
		process.exit(1);
	});
}
