import type { WorkbenchScreen } from "./types";

export const EXPLORER_SCREENS: readonly WorkbenchScreen[] = [
	{
		id: "step6-pipeline-console",
		tabLabel: "6 · DV 2.0 Console",
		title: "Step 6 — DV 2.0 Build, 7-Pill Console & 5-Zone SQL",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "dark",
		webpPath: "/screenshots/shot-06-pipeline-console.webp",
		pngPath: "/screenshots/shot-06-pipeline-console.png",
		width: 2880,
		height: 1840,
		alt: "Step 6 DV 2.0 Build and Run displaying derived topology, 7-pill 00-06 pipeline console, dark SQL preview, and Sync Drift to Step 4.",
		summary:
			"Auto-binds deployed_contract (D12), runs the 7-pill 00..06 live console, compiles DuckDB & Databricks 5-Zone (Z0–Z4) SQL, surfaces inline Steward/Budget Holds (D14/D21), and syncs drift back to Step 4.",
		pins: [
			{
				id: "s06-p1",
				label: "Derived Hub/Link/Sat Topology",
				xPct: 28,
				yPct: 33,
				detail:
					"Projects FIBO, Custom, and Doc hubs plus auto-split sat_position_pii alongside auto-bound deployed_contract (D12).",
			},
			{
				id: "s06-p2",
				label: "Live 7-Pill 00..06 Console & Drift Sync",
				xPct: 26,
				yPct: 68,
				detail:
					"Click any stage pill 00–06 for live logs, resolve Steward or Budget Holds inline (D14/D21), or Sync Drift to Step 4.",
			},
			{
				id: "s06-p3",
				label: "5-Zone (Z0–Z4) SQL & Bundle Export",
				xPct: 74,
				yPct: 55,
				detail:
					"Inspect multi-dialect bronze, staging, raw_vault, business_vault, and information_mart SQL and export artifacts.",
			},
		],
	},
	{
		id: "dv-schema-explorer",
		tabLabel: "6b · DV Schema Graph",
		title: "DV Schema Explorer — Bipartite Graph, Alg A.5 & DDL",
		route: "127.0.0.1:8420 · DV Schema Explorer",
		theme: "light",
		webpPath: "/screenshots/shot-06b-dv-schema-explorer.webp",
		pngPath: "/screenshots/shot-06b-dv-schema-explorer.png",
		width: 2880,
		height: 1840,
		alt: "DV Schema Explorer View 1 showing dark obsidian Hub nodes, warm amber Link pills, teal PIT nodes, coral PII Satellites, and Physical Table Inspector.",
		summary:
			"Explore Hub-Link-Sat-PIT bipartite topology with Zone Z0–Z4 & PII filters, Alg A.5 hash expressions, 6-gate proof readout, and table DDL/DML modal.",
		pins: [
			{
				id: "s06b-p1",
				label: "Bipartite Hub-Link-Sat-PIT Topology",
				xPct: 34,
				yPct: 48,
				detail:
					"Obsidian Hubs, amber Link/SAL pills, teal PITs, and coral-bordered sat_position_pii with zero Link-on-Link edges.",
			},
			{
				id: "s06b-p2",
				label: "Zone Z0–Z4 & + Wave Delta Filters",
				xPct: 36,
				yPct: 14,
				detail:
					"Filter 100+ tables by Z0 Refs, Z1 Staging, Z2 Hubs/Links/Sats, Z3 PIT & Bridges, PII Split Only, or Wave Delta.",
			},
			{
				id: "s06b-p3",
				label: "Table Lineage & Alg A.5 Proof Rail",
				xPct: 82,
				yPct: 58,
				detail:
					"Inspect physical columns, wave drift provenance, exact Algorithm A.5 length-prefixed SHA-256 SQL, and DDL dialog.",
			},
		],
	},
	{
		id: "triage-wave-diff",
		tabLabel: "6c · Triage & Safe SQL",
		title: "DV Schema Explorer — 100% Triage, Wave Diff & Safe SQL",
		route: "127.0.0.1:8420 · DV Schema Explorer",
		theme: "light",
		webpPath: "/screenshots/shot-06c-triage-wave-diff.webp",
		pngPath: "/screenshots/shot-06c-triage-wave-diff.png",
		width: 2880,
		height: 1840,
		alt: "DV Schema Explorer View 2 showing 100% Column Triage Ledger across Waves W1-W3, Wave Evolution Diff, and populated DuckDB PIT D.10b output rows.",
		summary:
			"Audit 100% column triage (84/84), W1 -> W2 -> W3 evolution diffs (EVO-01/02/03/07), live DuckDB row previews, and D11 Safe Read-Only SQL Runner (PIT D.10b).",
		pins: [
			{
				id: "s06c-p1",
				label: "100% Column Triage Ledger",
				xPct: 30,
				yPct: 46,
				detail:
					"Maps every source column across W1–W3 to BK Tier, Sparse FK, PII, Doc Span, Drift Add, Alias, Soft Drop, or Surrogate Quarantine.",
			},
			{
				id: "s06c-p2",
				label: "W1 -> W2 -> W3 Structural Diff",
				xPct: 78,
				yPct: 29,
				detail:
					"Verifies additive EVO-01 companion satellites, EVO-02/03 safe aliases, and 100% bit-identical prior hash_diff.",
			},
			{
				id: "s06c-p3",
				label: "Live DuckDB Rows & Safe SQL (D11)",
				xPct: 78,
				yPct: 64,
				detail:
					"Inspects populated PIT Query D.10b rows and executes allowlist-hardened SELECT/UNION queries via validate_safe_sql().",
			},
		],
	},
	{
		id: "session-logs",
		tabLabel: "7 · Session Logs",
		title: "SessionLogsInspector — Live SSE, Cost Meter & Traces",
		route: "127.0.0.1:8420 · Logs & Traces",
		theme: "dark",
		webpPath: "/screenshots/shot-07-session-logs.webp",
		pngPath: "/screenshots/shot-07-session-logs.png",
		width: 2880,
		height: 1840,
		alt: "SessionLogsInspector displaying live W3C EventSource timeline, stage scope filters, PII-masked prompt payload, and raw LLM output trace.",
		summary:
			"Stream live W3C EventSource agent logs with ● Follow Live Tail, inspect PII-masked prompts, LLM outputs, @toon_v1 traces, live Cost Meter (D21), and 50 MB retention.",
		pins: [
			{
				id: "s07-p1",
				label: "Stage & Persona Scope Filters",
				xPct: 36,
				yPct: 9,
				detail:
					"Filter session events by All Session, Step 2 Ingest, Step 4 Agent, or mid-run stages with live search.",
			},
			{
				id: "s07-p2",
				label: "W3C EventSource Session Timeline",
				xPct: 26,
				yPct: 48,
				detail:
					"Streams incremental stage flushes, token usage, latency, and @toon_v1 repairs across the full session lifetime.",
			},
			{
				id: "s07-p3",
				label: "4-Tab Trace Inspector & Bundle Export",
				xPct: 72,
				yPct: 48,
				detail:
					"Switch across Masked Prompt & Context, Structured LLM Response, Tool Calls & @toon_v1, and Raw JSON + 50 MB .gz export.",
			},
		],
	},
];
