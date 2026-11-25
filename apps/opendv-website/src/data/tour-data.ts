import type { TourChapter } from "./types";

export const TOUR_CHAPTERS: readonly TourChapter[] = [
	{
		id: "ch-1",
		number: 1,
		timestamp: "0:00",
		startSec: 0,
		endSec: 28,
		title: "Why LLM SQL Fails & V2 Architecture",
		callouts: ["INV-01..06", "Alg A.5 SHA-256", "L1 + V1–V8 CEGAR"],
		transcript:
			"Data Vault 2.0 delivers historical auditability and multi-source integration, yet asking an LLM to write Data Vault SQL directly risks delimiter hash collisions, leaked surrogate keys, and destructive UPDATEs. Open-DV V2 pairs an Agent-first 7-Step Guided ⇄ Observatory Workbench over 5-table project.sqlite with deterministic L1 codemods and V1–V8 verification before any 5-Zone (Z0–Z4) SQL compiles.",
	},
	{
		id: "ch-2",
		number: 2,
		timestamp: "0:28",
		startSec: 28,
		endSec: 62,
		title: "Loopback Open Access & Steps 1–3",
		callouts: ["opendv serve", "core_spine · 125-perm MinHash"],
		transcript:
			"Run uv run opendv serve for zero-auth loopback access at http://127.0.0.1:8420 (non-loopback --host fails closed with exit 40 unless --allow-remote-unauthenticated is set). In Step 1 choose Blank Genesis, Workspace Seed, core_spine Starter Pack, or .opendv.zip; in Step 2 attach 6 static connectors plus PDF/DOCX/Markdown with synchronous DocumentMap outlines; in Step 3 browse 125-permutation MinHash clusters, ConceptMemory Pin/Mute tags, and approve-scan OCR gates.",
	},
	{
		id: "ch-3",
		number: 3,
		timestamp: "1:02",
		startSec: 62,
		endSec: 98,
		title: "Brokered Step 4, Budget Meter & 4-Mode Step 5",
		callouts: ["JobMcpBroker /coord & /scout", "Budget Meter · 4-Mode Graph A"],
		transcript:
			"Step 4 enforces privilege separation via JobMcpBroker: @ontology-first-mate uses /coord tools only while isolated /scout subprocesses inspect masked tables and page spans, streaming into a live #181715 trace drawer alongside the Budget Meter (Turns · Tokens · Active Seconds) and Clarification Holds. Inspect 4-Path grounding (PATH_A1 exactMatch, PATH_A2 closeMatch, PATH_B1 empirical/1x doc, PATH_B2 quarantine) in the 716px Citation Drawer, then commit Graph A in the 4-Mode Step 5 Explorer with KeyTierEnum, comment-preserving YAML CST, and OCC base_revision_id.",
	},
	{
		id: "ch-4",
		number: 4,
		timestamp: "1:38",
		startSec: 98,
		endSec: 125,
		title: "Step 6 Guided Vault Synthesis & V8 Horizon Critic",
		callouts: ["Guided ⇄ Observatory", "L1 · V1–V6B · V7 · V8 Horizon"],
		transcript:
			"Step 6 defaults to plain-English Guided mode with Why Does This Table Exist? explainers and Steward Decision Cards—paired with 1-click Architect Observatory. Bounded CEGAR runs L1 codemods, V1–V5 symbolic checks, V6 15-invariant :memory: DuckDB proving, V6B anti-patterns, V7 Frontier Semantic Critic, and V8 Horizon Critic stress-testing BRK-01..08 future scenarios (Bucket A/B/C) before sealing VaultSchemaIR v2.0 and LOCK-W00N.",
	},
	{
		id: "ch-5",
		number: 5,
		timestamp: "2:05",
		startSec: 125,
		endSec: 152,
		title: "Step 7 11 Targets, DV Schema Explorer & Run Explorer",
		callouts: ["11 Storage Targets", "4-View Explorer · Run Explorer"],
		transcript:
			"Step 7 compiles VaultSchemaIR v2.0 into 8 SQL dialects plus Iceberg, Parquet, and dbt (automate_dv) with TargetEvalGate parity and a local read-only DuckDB query runner. Inspect Hub-Link-Sat-PIT lineage across 4 views and 5-Zone Z0–Z4 filters in DV Schema Explorer, and replay Last-Event-ID timelines, Attempt diffs, MCP/OCR receipts, and redaction telemetry in Run Explorer.",
	},
	{
		id: "ch-6",
		number: 6,
		timestamp: "2:32",
		startSec: 152,
		endSec: 165,
		title: "5-Zone SQL, .opendv.zip & Unified opendv CLI",
		callouts: [
			"schemas/v2/cli-spec.json",
			"source attach · fibo-qa · vault logs · storage query",
		],
		transcript:
			"Export portable .opendv.zip project bundles or run every Step 1–7 workflow headlessly with the unified Typer opendv CLI (in-process or --api-url) and 17-tool stdio MCP server: project init, source attach, ontology fibo-qa, vault build, vault logs, and storage query.",
	},
];
