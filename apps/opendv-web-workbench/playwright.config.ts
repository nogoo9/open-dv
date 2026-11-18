import fs from "node:fs";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

function discoverChromium(): string {
	if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
		return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
	}
	const localAppData = process.env.LOCALAPPDATA;
	if (localAppData) {
		const pwDir = path.join(localAppData, "ms-playwright");
		if (fs.existsSync(pwDir)) {
			try {
				const entries = fs.readdirSync(pwDir, { withFileTypes: true });
				const chromiumDirs = entries
					.filter((e) => e.isDirectory() && e.name.startsWith("chromium-"))
					.map((e) => e.name)
					.sort();
				if (chromiumDirs.length > 0) {
					const latest = chromiumDirs[chromiumDirs.length - 1];
					const candidate = path.join(pwDir, latest, "chrome-win64", "chrome.exe");
					if (fs.existsSync(candidate)) {
						return candidate;
					}
					const candidateAlt = path.join(pwDir, latest, "chrome-win", "chrome.exe");
					if (fs.existsSync(candidateAlt)) {
						return candidateAlt;
					}
				}
			} catch {
				// ignore
			}
		}
	}
	const homeDir = process.env.HOME;
	if (homeDir) {
		const cacheDirs = [
			{ dir: path.join(homeDir, ".cache", "ms-playwright"), rels: ["chrome-linux64/chrome", "chrome-linux/chrome"] },
			{ dir: path.join(homeDir, "Library", "Caches", "ms-playwright"), rels: ["chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"] },
		];
		for (const { dir, rels } of cacheDirs) {
			if (fs.existsSync(dir)) {
				try {
					const chromiumDirs = fs
						.readdirSync(dir, { withFileTypes: true })
						.filter((e) => e.isDirectory() && e.name.startsWith("chromium-"))
						.map((e) => e.name)
						.sort();
					if (chromiumDirs.length > 0) {
						const latest = chromiumDirs[chromiumDirs.length - 1];
						for (const rel of rels) {
							const candidate = path.join(dir, latest, rel);
							if (fs.existsSync(candidate)) {
								return candidate;
							}
						}
					}
				} catch {
					// ignore
				}
			}
		}
	}
	for (const binName of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]) {
		if (typeof Bun !== "undefined" && typeof Bun.which === "function") {
			const resolved = Bun.which(binName);
			if (resolved) {
				return resolved;
			}
		}
	}
	for (const candidate of ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"]) {
		if (fs.existsSync(candidate)) {
			return candidate;
		}
	}
	return "chrome";
}

const CHROMIUM_PREINSTALLED_PATH = discoverChromium();

export default defineConfig({
	testDir: "./tests/e2e",
	testMatch: "**/*.spec.ts",
	timeout: 720_000,
	expect: {
		timeout: 15_000,
	},
	fullyParallel: false,
	workers: 1,
	retries: 0,
	reporter: [
		["list"],
		["html", { outputFolder: "playwright-report", open: "never" }],
	],
	use: {
		baseURL: process.env.OPENDV_BASE_URL || "http://127.0.0.1:5173",
		trace: "retain-on-failure",
		screenshot: "only-on-failure",
		video: "off",
	},
	projects: [
		{
			name: "local-chromium",
			use: {
				...devices["Desktop Chrome"],
				launchOptions: {
					executablePath: CHROMIUM_PREINSTALLED_PATH,
					headless: true,
					args: [
						"--no-sandbox",
						"--disable-setuid-sandbox",
						"--disable-gpu",
						"--disable-dev-shm-usage",
					],
				},
			},
		},
	],
	webServer: [
		{
			command: "uv run python tests/e2e/run_e2e_platform_server.py --port 8420 --host 127.0.0.1",
			url: "http://127.0.0.1:8420/api/v1/health",
			reuseExistingServer: true,
			timeout: 90_000,
			cwd: "../..",
		},
		{
			command: "bun run src/dev-server.ts",
			url: "http://127.0.0.1:5173",
			reuseExistingServer: true,
			timeout: 90_000,
		},
	],
});
