import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
	testDir: "./e2e",
	testMatch: "**/*.pw.ts",
	timeout: 30_000,
	fullyParallel: false,
	workers: 1,
	use: {
		baseURL: "http://127.0.0.1:4173",
		trace: "retain-on-failure",
	},
	projects: [
		{
			name: "chromium",
			use: { ...devices["Desktop Chrome"] },
		},
	],
	webServer: {
		command: "bun run build && bun run preview",
		url: "http://127.0.0.1:4173/open-dv/",
		env: { OPENDV_SITE_BASE_PATH: "/open-dv" },
		reuseExistingServer: !process.env.CI,
		timeout: 60_000,
	},
});
