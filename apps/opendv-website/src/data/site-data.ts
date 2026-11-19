import type { DocRef, QuickstartStep, SiteMeta } from "./types";

export type * from "./types";
export {
	AXI_GATES,
	FAULT_PRESETS,
	NAIVE_TRAPS,
	PIPELINE_STAGES,
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
	version: "v6.1",
	license: "Apache-2.0",
	repoUrl: "https://github.com/nogoo9/open-dv",
	docsBaseUrl: "https://github.com/nogoo9/open-dv/blob/main/docs",
	heroInstallCmd: "uv sync --all-packages && uv run opendv serve --open",
	tourDurationSec: 165,
};

export const QUICKSTART_STEPS: readonly QuickstartStep[] = [
	{
		step: 1,
		title: "Install Python & Bun Workspaces",
		command: "uv sync --all-packages && bun install",
		note: "Requires Python >= 3.12 and Bun; builds the standalone platform API, Dual CLIs, and 4-Tab Web Workbench.",
	},
	{
		step: 2,
		title: "Launch Loopback Open-Access Workbench",
		command: "uv run opendv serve --open",
		note: "Starts zero-auth loopback REST/SSE + 6-Step Wizard at http://127.0.0.1:8420 (non-loopback exits 40 without --allow-remote-unauthenticated).",
	},
	{
		step: 3,
		title: "Verify & Inspect Headlessly in CI",
		command: "uv run opendv verify -c final_contract.yaml --json",
		note: "Executes Axi-DV Gates G0-G5 with RFC 8785 deterministic JSON, or query outputs via run dv-schema and ontology studio logs.",
	},
];

export const AGENT_QUICKSTART_PROMPT = `Clone https://github.com/nogoo9/open-dv and run \`uv sync --all-packages && bun install\`.
Use \`uv run opendv serve --open\` for the Unified 6-Step Wizard & DV Schema Explorer at http://127.0.0.1:8420
(use \`--allow-remote-unauthenticated\` only for trusted non-loopback binds), or run \`uv run opendv verify -c final_contract.yaml --json\` and \`uv run opendv run dv-schema --run-id <id>\` in CI.`;

export const DOC_REFS: readonly DocRef[] = [
	{
		id: "docs/27",
		path: "27-unified-ontology-pipeline-requirements-and-uat.md",
		topic:
			"Normative FR/NFR specs, D1–D28 decisions, SCN-01..13 & UAT-01..30 matrix",
	},
	{
		id: "docs/28",
		path: "28-unified-ontology-pipeline-design-and-plan.md",
		topic:
			"Unified 6-Step Wizard, 12-screen design, DV Schema Explorer & phase plan",
	},
	{
		id: "docs/01",
		path: "01-core-modelling-spec.md",
		topic: "Data Vault 2.0 invariants, 3-Tier BKCC, PII isolation",
	},
	{
		id: "docs/02",
		path: "02-schema-stability-and-evolution.md",
		topic: "Additive evolution locks, drift, YAGNI write-amplification",
	},
	{
		id: "docs/03",
		path: "03-verification-and-agentic-architecture.md",
		topic: "6-Gate axi-dv static proofs & DuckDB 4-Batch shadow sandbox",
	},
	{
		id: "docs/05",
		path: "05-elm-calculus-and-reference-algorithms.md",
		topic: "Alg A.1-A.9b codemods & length-prefixed SHA-256 hashing",
	},
	{
		id: "docs/16",
		path: "16-human-and-agent-usage-guide.md",
		topic: "Contract-identical opendv & opendvctl dual-CLI specification",
	},
];
