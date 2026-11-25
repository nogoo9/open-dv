import type { DocRef, QuickstartStep, SiteMeta } from "./types";

export type * from "./types";
export {
	FAULT_PRESETS,
	NAIVE_TRAPS,
	PIPELINE_STAGES,
	VERIFIER_GATES,
} from "./pipeline-data";
export {
	CONNECTORS,
	DIALECT_SNIPPETS,
	HARNESS_PROVIDERS,
	INFERENCE_SCENARIOS,
} from "./ecosystem-data";
export { WORKBENCH_SCREENS } from "./workbench-data";
export { TOUR_CHAPTERS } from "./tour-data";

export const YOUTUBE_VIDEO_ID = "";

export const SITE_META: SiteMeta = {
	name: "open-dv",
	version: "v2.0",
	license: "Apache-2.0",
	repoUrl: "https://github.com/nogoo9/open-dv",
	docsBaseUrl: "https://github.com/nogoo9/open-dv/blob/main/docs",
	heroInstallCmd: "uv sync && uv run opendv serve",
	tourDurationSec: 165,
};

export const QUICKSTART_STEPS: readonly QuickstartStep[] = [
	{
		step: 1,
		title: "Install Python Engine & React 19 SPA",
		command: "uv sync && bun install --cwd web",
		note: "Requires Python >= 3.12 and Bun; installs src/opendv/ (34 ErrorCodes, 11 storage targets) and builds the standalone 7-Step Workbench.",
	},
	{
		step: 2,
		title: "Launch Loopback Open-Access Workbench",
		command: "uv run opendv serve",
		note: "Starts zero-auth loopback REST/SSE + 7-Step Guided Workbench at http://127.0.0.1:8420 (non-loopback exits 40 without --allow-remote-unauthenticated).",
	},
	{
		step: 3,
		title: "Verify, Inspect & Query Headlessly in CI",
		command: "uv run opendv vault verify --project am-ibor-2026 --json",
		note: "Runs L1, V1-V5, V6 DuckDB, V6B, V7 & V8 Horizon gates with RFC 8785 JCS JSON, or execute source attach, ontology fibo-qa, vault logs, and storage query.",
	},
];

export const AGENT_QUICKSTART_PROMPT = `Clone https://github.com/nogoo9/open-dv and run \`uv sync && bun install --cwd web\`.
Launch \`uv run opendv serve\` for the 7-Step Guided ⇄ Observatory Workbench, 4-View DV Schema Explorer, and Run Explorer at http://127.0.0.1:8420 (pass \`--allow-remote-unauthenticated\` only for trusted non-loopback binds).
Or run headless CLI workflows with \`uv run opendv project init\`, \`uv run opendv source attach\`, \`uv run opendv ontology fibo-qa\`, \`uv run opendv vault build --wait\`, \`uv run opendv vault logs\`, and \`uv run opendv storage query\`.`;

export const DOC_REFS: readonly DocRef[] = [
	{
		id: "v2-arch",
		path: "designs/v2-clean-sheet-architecture.md",
		topic:
			"Clean-sheet V2 architecture: 5-table project.sqlite, VaultSchemaIR v2.0, 11 targets & 34 ErrorCodes",
	},
	{
		id: "v2-ui",
		path: "designs/v2-workbench-ui-design.md",
		topic:
			"7-Step Guided ⇄ Observatory Workbench, Budget Meter, DV Schema Explorer, Run Explorer & WCAG AA tokens",
	},
	{
		id: "docs/29",
		path: "29-discovery-grounding-and-evolution-scenarios.md",
		topic:
			"SCEN-01..09 Discovery, FIBO PATH_A1/A2 vs PATH_B1/B2 grounding, BKCC & multi-wave evolution matrix",
	},
	{
		id: "docs/30",
		path: "30-enterprise-dv2-modeling-and-loop-engineering.md",
		topic:
			"TRAP-01..12 benchmarks, 3-Loop CEGAR, V8 Horizon Critic (BRK-01..08) & .opendv.zip portability",
	},
	{
		id: "docs/31",
		path: "31-v2-e2e-requirements-gaps-and-uat-spec.md",
		topic:
			"Consolidated V2 E2E requirements and 89-item full-stack browser & CLI UAT matrix",
	},
	{
		id: "docs/32",
		path: "32-v2-user-experience-and-scenario-guide.md",
		topic:
			"Steps 1–7 Guided UX guide, 20-scenario playbook (SCEN/MOD/OPS) & offline readiness flags",
	},
	{
		id: "docs/16",
		path: "16-cli-reference.md",
		topic:
			"Unified Typer opendv CLI (in-process & --api-url) + 17-tool stdio MCP (schemas/v2/cli-spec.json)",
	},
];
