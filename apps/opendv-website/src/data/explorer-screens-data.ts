import type { WorkbenchScreen } from "./types";

export const EXPLORER_SCREENS: readonly WorkbenchScreen[] = [
	{
		id: "step6-guided-vault",
		tabLabel: "6 · Vault Synthesis",
		title: "Step 6 — Guided Decision Cards & Certified Vault Synthesis",
		route: "127.0.0.1:8420 · Project Workflow · Step 6",
		theme: "light",
		webpPath: "/screenshots/shot-06-pipeline-console.webp",
		pngPath: "/screenshots/shot-06-pipeline-console.png",
		width: 2880,
		height: 1840,
		alt: "Step 6 Vault Synthesis in Guided View showing Balanced strictness, Confirm OTC Collateral Key Scope decision card, and CERTIFIED Wave Readiness Brief.",
		summary:
			"Toggle between plain-English Guided Decision Cards ('Confirm OTC Collateral Key Scope' with lock-in warnings) and the Architect Observatory while certifying multi-wave CEGAR checkpoints.",
		pins: [
			{
				id: "s06-p1",
				label: "Guided ⇄ Observatory Toggle",
				xPct: 91,
				yPct: 13,
				detail:
					"Switch seamlessly between business-facing Guided Decision Cards and the technical Architect Observatory (IR tree, CEGAR V1–V8, Horizon, Critique).",
			},
			{
				id: "s06-p2",
				label: "Horizon Decision Cards",
				xPct: 26,
				yPct: 58,
				detail:
					"Resolves Bucket B semantic ambiguities (Keep source-scoped vs. High Lock-In Risk) and ONT-AP business waivers cleanly.",
			},
			{
				id: "s06-p3",
				label: "CERTIFIED Wave Readiness Brief",
				xPct: 74,
				yPct: 55,
				detail:
					"Confirms 15 core business entities, 2 relationships, 14 history tables, Future Watchlist items, and Verify Vault.",
			},
		],
	},
	{
		id: "dv-schema-explorer",
		tabLabel: "DV Schema Explorer",
		title: "DV Schema Explorer — 5-Zone Bipartite Lineage & Wave 2 Objects",
		route: "127.0.0.1:8420 · DV Schema Explorer · View 2",
		theme: "light",
		webpPath: "/screenshots/shot-06b-dv-schema-explorer.webp",
		pngPath: "/screenshots/shot-06b-dv-schema-explorer.png",
		width: 2880,
		height: 1840,
		alt: "DV Schema Explorer View 2 Bipartite Graph showing 69 Wave 2 objects across Z0 Bronze, Z1 Stg, Z2 Hubs/Links/Sats, Z3 SAL/PIT, and Z4 information_mart.",
		summary:
			"Inspect the 69-object CertifiedVaultIR across 4 dedicated views (Zone Flow, Bipartite Graph, IR & Target Manifests, Wave Diff & Locks) with wave evolution lock badges.",
		pins: [
			{
				id: "s06b-p1",
				label: "4 Views, 10 Zone Filters & Wave 2 Pill",
				xPct: 32,
				yPct: 23,
				detail:
					"Slice Wave 2 · 69 objects across Z0 Bronze, Z1 Stg, Z2 Hubs/Links/Sats, Z3 SAL & PIT, Z4 information_mart, PII Split, and +W002.",
			},
			{
				id: "s06b-p2",
				label: "Forward-Only 5-Zone Lineage",
				xPct: 50,
				yPct: 48,
				detail:
					"Traces bronze_* -> stg_* -> hub_issuer_lei / hub_security_isin (sal_issuer ALIAS/PRIMARY) -> information_mart.vw_*.",
			},
			{
				id: "s06b-p3",
				label: "SAL, PIT & Zero-Mutation Status",
				xPct: 57,
				yPct: 73,
				detail:
					"Displays pit_proxy_ballot, multi-active satellites (mas_allocations), and Loopback Ready · v2.0.0 workspace telemetry.",
			},
		],
	},
	{
		id: "step7-storage-targets",
		tabLabel: "7 · Storage & DuckDB",
		title: "Step 7 — 11-Target Storage Compilation & Read-Only DuckDB Runner",
		route: "127.0.0.1:8420 · Project Workflow · Step 7",
		theme: "light",
		webpPath: "/screenshots/shot-06c-triage-wave-diff.webp",
		pngPath: "/screenshots/shot-06c-triage-wave-diff.png",
		width: 2880,
		height: 1840,
		alt: "Step 7 Storage Compilation showing 11 Certified Wave 2 targets, TargetEvalGate receipts for duckdb_local, and Sandboxed Read-Only DuckDB Query Runner returning ROW_COUNT 22.",
		summary:
			"Compile 11 disposable warehouse & open-format targets (duckdb_local, databricks_uc, snowflake, postgres, iceberg, parquet, dbt), inspect TargetEvalGate receipts, and query DuckDB live.",
		pins: [
			{
				id: "s06c-p1",
				label: "11 Certified Warehouse & Format Targets",
				xPct: 24,
				yPct: 48,
				detail:
					"Compile single or all 11 dialects respecting identifier limits (63 B PostgreSQL, 128 B SQL Server), delta merges, and runtime parity.",
			},
			{
				id: "s06c-p2",
				label: "TargetEvalGate Receipts & Migrations",
				xPct: 74,
				yPct: 31,
				detail:
					"Emits EVAL-AST-01, EVAL-RUN-01..04, ddl/full_schema_w2.sql, dml/load_w2.sql, and additive V002_migrate.sql manifests.",
			},
			{
				id: "s06c-p3",
				label: "Sandboxed Read-Only DuckDB Runner",
				xPct: 74,
				yPct: 60,
				detail:
					"Executes read-only SELECT SQL (ROW_COUNT: 22) verifying -1/-2 Dual Zero Keys and 1900-01-01T00:00:00Z ghost rows.",
			},
		],
	},
	{
		id: "run-explorer",
		tabLabel: "Run Explorer",
		title:
			"Run Explorer — Last-Event-ID Timeline, Attempts & Payload Inspector",
		route: "127.0.0.1:8420 · Run Explorer",
		theme: "light",
		webpPath: "/screenshots/shot-07-session-logs.webp",
		pngPath: "/screenshots/shot-07-session-logs.png",
		width: 2880,
		height: 1840,
		alt: "Run Explorer displaying Last-Event-ID 13 timeline for Step 6 Wave 2 synthesis alongside dark 4-tab Payload Inspector and Download .jsonl export.",
		summary:
			"Audit any Step 4 Discovery or Step 6 Vault Synthesis ledger without mutating active workflow drafts—streaming Last-Event-ID frames, Attempt IR diffs, MCP/OCR receipts, and .jsonl logs.",
		pins: [
			{
				id: "s07-p1",
				label: "Run Selector, Live Tail & .jsonl Export",
				xPct: 72,
				yPct: 14,
				detail:
					"Switch across historical or active Step 4/Step 6 runs (run6-8bb5f57211 · COMPLETED), follow SSE tail, or download full .jsonl logs.",
			},
			{
				id: "s07-p2",
				label: "Last-Event-ID Sequence Timeline",
				xPct: 22,
				yPct: 48,
				detail:
					"Lists monotonic STAGE_UPDATE, GATE_CHECK_STREAM, ATTEMPT_SNAPSHOT, and HOLD_REQUESTED events for deterministic replay.",
			},
			{
				id: "s07-p3",
				label: "4-Tab Dark Trace Inspector",
				xPct: 68,
				yPct: 38,
				detail:
					"Inspect Event Payloads, Attempt IR & Gate Diffs, Broker/OCR/MCP Receipts, and Redaction & Budget Telemetry cleanly in dark ink.",
			},
		],
	},
];
