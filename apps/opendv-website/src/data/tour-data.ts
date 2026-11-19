import type { TourChapter } from "./types";

export const TOUR_CHAPTERS: readonly TourChapter[] = [
	{
		id: "ch-1",
		number: 1,
		timestamp: "0:00",
		startSec: 0,
		endSec: 28,
		title: "Why LLM SQL Fails",
		callouts: ["INV-01..06", "Alg A.5 SHA-256", "6-Gate axi-dv"],
		transcript:
			"Data Vault 2.0 delivers historical auditability and multi-source integration, yet asking an LLM to write Data Vault SQL directly risks delimiter hash collisions, leaked surrogate keys, and destructive UPDATEs. Open-DV pairs Agent-first discovery in the 4-Tab Workbench with the deterministic 6-gate axi-dv verifier and DuckDB shadow sandbox before any 5-Zone (Z0–Z4) SQL compiles.",
	},
	{
		id: "ch-2",
		number: 2,
		timestamp: "0:28",
		startSec: 28,
		endSec: 62,
		title: "Loopback Open Access & Steps 1–3",
		callouts: ["opendv serve --open", "6 Starter Packs · Zero Mention Floor"],
		transcript:
			"Run uv run opendv serve --open for zero-auth loopback access at http://127.0.0.1:8420 (non-loopback --host fails closed with exit 40 unless --allow-remote-unauthenticated is set). In Step 1 choose Blank Genesis, 6 starter packs, or .ttl import; in Step 2 attach SQLite, Databricks UC, PDFs with OCR, do_not_send_to_agent flags, and inline notes; in Step 3 preview <= 50 rows and LiteParse char spans, or Fast-Forward to Step 6.",
	},
	{
		id: "ch-3",
		number: 3,
		timestamp: "1:02",
		startSec: 62,
		endSec: 98,
		title: "Agent Gate, Cost Meter & Diff Graph",
		callouts: [
			"422 AGENT_NOT_CONFIGURED",
			"716px Citation Drawer · Step 5 DAG",
		],
		transcript:
			"Step 4 enforces an explicit 422 AGENT_NOT_CONFIGURED gate with the shared <AgentHarnessConfigCard /> across 6 harness providers (agy, agy-cli, antigravity, claude, claude-cli, claude-code) and a live Cost Meter with budget holds. Single-pass synthesis surfaces Beyond-FIBO entities, 1x Semantic rules, unchecked MAJOR Opt-In drops, a 716px Citation & FIBO drawer, and the Step 5 elkjs diff graph with Export Ontology Only.",
	},
	{
		id: "ch-4",
		number: 4,
		timestamp: "1:38",
		startSec: 98,
		endSec: 125,
		title: "Step 6 Console & 6-Gate axi-dv",
		callouts: ["7-Pill 00..06 Console", "G0–G5 · @toon_v1 · Inline Hold"],
		transcript:
			"Step 6 auto-binds deployed_contract (D12) and launches the 7-pill 00..06 live pipeline console backed by STAGE_00..07. Gates G0–G5 run SQLGlot AST checks, NetworkX bipartite proofs, and 4 adversarial DuckDB batches with @toon_v1 self-healing—surfacing Steward and Budget Holds inline and syncing physical drift straight back to Step 4.",
	},
	{
		id: "ch-5",
		number: 5,
		timestamp: "2:05",
		startSec: 125,
		endSec: 152,
		title: "DV Schema Explorer & Session Logs",
		callouts: ["4-View Explorer (06b, 06c, D11)", "SessionLogsInspector (07)"],
		transcript:
			"Inspect the compiled vault across 4 views in DV Schema Explorer: interactive Hub-Link-Sat-PIT bipartite graph (06b), 100% Column Triage Ledger and W1->W2->W3 Wave Evolution Diff (06c), populated DuckDB rows + validate_safe_sql() Safe Read-Only SQL Runner (D11), and 5-Zone Z0–Z4 SQL including information_mart. Open Logs & Traces in SessionLogsInspector (07) for live EventSource streams, PII-masked prompts, LLM responses, and 50 MB session-lifetime bundle exports.",
	},
	{
		id: "ch-6",
		number: 6,
		timestamp: "2:32",
		startSec: 152,
		endSec: 165,
		title: "5-Zone SQL & Dual-CLI Parity",
		callouts: [
			"schemas/v1/cli-spec.json",
			"run dv-schema · attach-source · fibo-qa · logs",
		],
		transcript:
			"Compile deterministic DuckDB and Databricks Delta 5-Zone (Z0–Z4) SQL spanning bronze, staging, raw_vault, business_vault, and information_mart—or execute every workflow headlessly with opendv and opendvctl parity commands: run dv-schema, ontology studio attach-source, ontology studio fibo-qa, and ontology studio logs.",
	},
];
