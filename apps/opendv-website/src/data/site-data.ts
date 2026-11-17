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
	version: "v6.0",
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
		note: "Requires Python >= 3.12 and Bun; builds the standalone platform API and web suite.",
	},
	{
		step: 2,
		title: "Launch Ontology Studio & Builder",
		command: "uv run opendv serve --open",
		note: "Starts unified REST/SSE server and embedded UI on http://127.0.0.1:8420.",
	},
	{
		step: 3,
		title: "Verify & Compile Headlessly in CI",
		command: "uv run opendv verify -c final_contract.yaml --json",
		note: "Executes Axi-DV Gates G0-G5 with RFC 8785 deterministic JSON exit codes.",
	},
];

export const AGENT_QUICKSTART_PROMPT = `Clone https://github.com/nogoo9/open-dv and run \`uv sync --all-packages && bun install\`.
Use \`uv run opendv serve --open\` for the Ontology Studio & Pipeline Builder at http://127.0.0.1:8420,
or run \`uv run opendv pipeline run -i pipeline_input.yaml\` and \`uv run opendv verify -c final_contract.yaml --json\` in CI.`;

export const DOC_REFS: readonly DocRef[] = [
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
		path: "16-cli-reference.md",
		topic: "Contract-identical opendv & opendvctl dual-CLI specification",
	},
	{
		id: "docs/20",
		path: "20-ontology-studio-spec.md",
		topic: "Ontology Construction Studio, FIBO grounding, CAS versions",
	},
];
