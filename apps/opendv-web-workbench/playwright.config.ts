import { defineConfig, devices } from "@playwright/test";

const CHROMIUM_PREINSTALLED_PATH =
	process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ||
	"C:\\Users\\etern\\AppData\\Local\\ms-playwright\\chromium-1243\\chrome-win64\\chrome.exe";

export default defineConfig({
	testDir: "./tests/e2e",
	testMatch: "**/*.spec.ts",
	timeout: 90_000,
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
	webServer: {
		command: "bun run src/dev-server.ts",
		url: "http://127.0.0.1:5173",
		reuseExistingServer: !process.env.CI,
		timeout: 30_000,
	},
});
