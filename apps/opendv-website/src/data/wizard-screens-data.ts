import type { WorkbenchScreen } from "./types";

export const WIZARD_SCREENS: readonly WorkbenchScreen[] = [
	{
		id: "step1-baseline",
		tabLabel: "1 · Scope & Seeds",
		title: "Step 1 — Scope, Starter Packs & Existing Projects",
		route: "127.0.0.1:8420 · Project Workflow · Step 1",
		theme: "light",
		webpPath: "/screenshots/shot-01-step1-baseline.webp",
		pngPath: "/screenshots/shot-01-step1-baseline.png",
		width: 2880,
		height: 1840,
		alt: "Step 1 Scope and Seeds screen showing Baseline Mode radios, core_spine planned stubs, dark Budget Meter pill, and Existing Projects cards.",
		summary:
			"Initialize projects from Blank Genesis (v0.0.0), Workspace Seed, Starter Pack planned stubs (6 core_spine concepts), or an uploaded .yaml/.json/.ttl ontology baseline.",
		pins: [
			{
				id: "s01-p1",
				label: "Dark Budget Meter & Active Revision",
				xPct: 16,
				yPct: 7,
				detail:
					"Header status bar shows Ready · agy, live Budget Meter telemetry (turns, tokens, seconds), and active Graph A revision digest.",
			},
			{
				id: "s01-p2",
				label: "Baseline Mode & Core Spine Stubs",
				xPct: 26,
				yPct: 58,
				detail:
					"Select Blank Genesis, Workspace Seed, or Starter Pack presets displaying is_planned_concept=true spine stubs before source profiling.",
			},
			{
				id: "s01-p3",
				label: "Archive Import & Existing Projects",
				xPct: 74,
				yPct: 60,
				detail:
					"Drop .opendv.zip archives or resume prior sessions across Step 1–7 with deterministic SQLite workspace persistence.",
			},
		],
	},
	{
		id: "step2-sources",
		tabLabel: "2 · Sources & Secrets",
		title: "Step 2 — Sources, Zero-LLM Profiling & Domain Notes",
		route: "127.0.0.1:8420 · Project Workflow · Step 2",
		theme: "light",
		webpPath: "/screenshots/shot-02-step2-sources.webp",
		pngPath: "/screenshots/shot-02-step2-sources.png",
		width: 2880,
		height: 1840,
		alt: "Step 2 Sources and Secrets screen inspecting data/public_am/am_sec_treasury_ibor.sqlite with 6 tables, file upload, MNPI compliance banner, and 5 attached sources.",
		summary:
			"Zero-LLM profiling across 6 structured connectors, 250 MB uploads, and UTF-8 domain notes—emitting READY_OFFLINE source handles and DocumentMap outlines without background queues.",
		pins: [
			{
				id: "s02-p1",
				label: "Structured Connectors & Live Preview",
				xPct: 25,
				yPct: 45,
				detail:
					"Inspect SQLite, DuckDB, Parquet, CSV, Excel, or Unity Catalog metadata cleanly via repo-relative paths and table previews.",
			},
			{
				id: "s02-p2",
				label: "File Upload & MNPI Domain Notes",
				xPct: 25,
				yPct: 82,
				detail:
					"Attach local files up to 250 MB and capture steward Markdown notes behind an explicit MNPI compliance warning banner.",
			},
			{
				id: "s02-p3",
				label: "Attached Sources Registry (READY_OFFLINE)",
				xPct: 75,
				yPct: 48,
				detail:
					"Tracks structured tables, PDFs, and notes with content digests (src-*), Preview, Re-attach, and wave-locked Detach controls.",
			},
		],
	},
	{
		id: "step3a-structured",
		tabLabel: "3a · Clusters & Memory",
		title: "Step 3a — MinHash Clusters, ConceptMemory & Masked Preview",
		route: "127.0.0.1:8420 · Project Workflow · Step 3a",
		theme: "light",
		webpPath: "/screenshots/shot-03a-structured-preview.webp",
		pngPath: "/screenshots/shot-03a-structured-preview.png",
		width: 2880,
		height: 1840,
		alt: "Step 3a Catalog and Clusters displaying MinHash Cluster Tree with holdings_13f, ConceptMemory Pinned and Muted term pills, and PK/FK-annotated 50-row Table Preview.",
		summary:
			"Group structured tables by deterministic MinHash similarity, toggle ConceptMemory tokens (PINNED / MUTED) for discovery bias, and inspect PII-masked 50-row previews.",
		pins: [
			{
				id: "s03a-p1",
				label: "MinHash Cluster Tree",
				xPct: 25,
				yPct: 45,
				detail:
					"Deterministic signature clustering groups related IBOR SQLite tables and Parquet stewardship feeds without LLM inference.",
			},
			{
				id: "s03a-p2",
				label: "ConceptMemory Term Cloud",
				xPct: 64,
				yPct: 37,
				detail:
					"Stewards cycle terms across Unset -> PINNED -> MUTED so scout prompts prioritize holdings keys and ignore noisy suffixes.",
			},
			{
				id: "s03a-p3",
				label: "PII-Safe PK/FK Table Preview",
				xPct: 74,
				yPct: 66,
				detail:
					"Renders 50 deterministic rows with detected PK/FK metadata (holding_id, filer_cik, ticker) and redaction counters.",
			},
		],
	},
	{
		id: "step3b-parsed-chunks",
		tabLabel: "3b · DocumentMap & Scan Gate",
		title: "Step 3b — Synchronous DocumentMap Outlines & Scan Consent Gate",
		route: "127.0.0.1:8420 · Project Workflow · Step 3b",
		theme: "light",
		webpPath: "/screenshots/shot-03b-parsed-chunks.webp",
		pngPath: "/screenshots/shot-03b-parsed-chunks.png",
		width: 2880,
		height: 1840,
		alt: "Step 3b DocumentMap Outlines listing Markdown and 6-page PDF digests alongside Page 4 Approved scan badge and verified text windows.",
		summary:
			"Browse synchronous DocumentMap headings with character hashes and enforce explicit Steward Scan Consent per scanned PDF page before OCR or VLM calls.",
		pins: [
			{
				id: "s03b-p1",
				label: "DocumentMap Outlines & Digests",
				xPct: 34,
				yPct: 68,
				detail:
					"Every Markdown and PDF document is indexed synchronously with page character counts, SHA hashes, and SCANNED markers.",
			},
			{
				id: "s03b-p2",
				label: "Multi-Page Evidence Window",
				xPct: 74,
				yPct: 55,
				detail:
					"Prev/Next 5-page windows inspect embedded vote schedules and custody prose prior to agent citation.",
			},
			{
				id: "s03b-p3",
				label: "Steward Scan Consent Receipt",
				xPct: 93,
				yPct: 79,
				detail:
					"Scanned Page 4 shows Approved check receipt—preventing unauthorized OCR or VLM spend across multi-hundred-page filings.",
			},
		],
	},
	{
		id: "step4a-discovery-hold",
		tabLabel: "4a · Clarification Hold",
		title: "Step 4a — Agent Discovery Hold Card & Dark Broker Trace",
		route: "127.0.0.1:8420 · Project Workflow · Step 4a",
		theme: "light",
		webpPath: "/screenshots/shot-04a-agent-setup-gate.webp",
		pngPath: "/screenshots/shot-04a-agent-setup-gate.png",
		width: 2880,
		height: 1840,
		alt: "Step 4a Discovery Hold screen showing Incremental New mode, HOLD_CHECKPOINTED clarification card for filer_cik, and dark Broker Trace drawer.",
		summary:
			"Checkpoint autonomous discovery cleanly when key ambiguity arises (HOLD_CHECKPOINTED), presenting sample rows and live Last-Event-ID broker trace telemetry.",
		pins: [
			{
				id: "s04a-p1",
				label: "Incremental Mode & Live Run Controls",
				xPct: 26,
				yPct: 38,
				detail:
					"Switch Incremental New, Reverify, or Full Rebuild, attach a Steward Hint, Pause, or Raise Budget mid-run.",
			},
			{
				id: "s04a-p2",
				label: "Steward Clarification Hold Card",
				xPct: 28,
				yPct: 70,
				detail:
					"Displays impact analysis (Impact if locked), sample source rows (0001166559), Recommended choice, or custom guidance.",
			},
			{
				id: "s04a-p3",
				label: "Dark Broker Trace Drawer",
				xPct: 74,
				yPct: 45,
				detail:
					"Streams resumable Last-Event-ID JSON frames for MCP calls, hold requests, and discovery heartbeats in dark ink.",
			},
		],
	},
	{
		id: "step4b-agent-discovery",
		tabLabel: "4b · 4-Path Matrix",
		title: "Step 4b — Staged Candidates & 4-Path Grounding Matrix",
		route: "127.0.0.1:8420 · Project Workflow · Step 4b",
		theme: "light",
		webpPath: "/screenshots/shot-04b-agent-discovery.webp",
		pngPath: "/screenshots/shot-04b-agent-discovery.png",
		width: 2880,
		height: 1840,
		alt: "Step 4b 4-Path Grounding Matrix listing 12 staged ontology candidates, PATH_A1/A2/B1/B2 counters, quarantine badges, and Inspect Citations buttons.",
		summary:
			"Track every staged concept across PATH_A1 (Exact FIBO), PATH_A2 (BM25 + Steward), PATH_B1 (Empirical DB), and PATH_B2 (Unverified Quarantine).",
		pins: [
			{
				id: "s04b-p1",
				label: "4-Path Counters & Ask FIBO Bar",
				xPct: 82,
				yPct: 29,
				detail:
					"Summarizes live counts across PATH_A1, PATH_A2, PATH_B1, and PATH_B2 alongside MCP call receipts.",
			},
			{
				id: "s04b-p2",
				label: "Grounding Path & Quarantine Gate",
				xPct: 52,
				yPct: 52,
				detail:
					"Surfaces empirical vs. ungrounded evidence status so unverified candidates remain quarantined until reviewed in Step 5.",
			},
			{
				id: "s04b-p3",
				label: "Evidence Breakdown & Citation Drawer",
				xPct: 86,
				yPct: 48,
				detail:
					"Shows linked tables, citations, and MCP tool calls (1t · 1c · 1 MCP) with one-click Citation Inspector inspection.",
			},
		],
	},
	{
		id: "step4c-citation-fibo",
		tabLabel: "4c · Citations & FIBO",
		title: "Step 4c — Citation & Evidence Inspector Drawer + Ask FIBO",
		route: "127.0.0.1:8420 · Project Workflow · Step 4c",
		theme: "light",
		webpPath: "/screenshots/shot-04c-citation-fibo.webp",
		pngPath: "/screenshots/shot-04c-citation-fibo.png",
		width: 2880,
		height: 1840,
		alt: "Slide-over Citation and Evidence Inspector showing FIBO Alignment Summary PATH_A2 scores and Verified Quote Span [0, 66] over Markdown source text.",
		summary:
			"Verify BM25/Exact FIBO alignments alongside server-indexed character spans ([start, end]) anchored verbatim inside attached Markdown and PDF artifacts.",
		pins: [
			{
				id: "s04c-p1",
				label: "FIBO Alignment Candidates & Ask FIBO",
				xPct: 75,
				yPct: 19,
				detail:
					"Displays ranked FIBO class candidates (PATH_A2 · 0.3892) fetched via the read-only Ask FIBO MCP resource.",
			},
			{
				id: "s04c-p2",
				label: "Server-Indexed Quote Span Badge",
				xPct: 60,
				yPct: 40,
				detail:
					"Confirms exact character offset [0, 66] inside source digest src-b0c81933b4 p.1 rather than unverified LLM paraphrases.",
			},
			{
				id: "s04c-p3",
				label: "Verbatim Grounded Source Excerpt",
				xPct: 74,
				yPct: 68,
				detail:
					"Highlights Stewardship Escalation Cases, ISO 17442 LEI rules, and ProxyBallot attributes in context.",
			},
		],
	},
	{
		id: "step5-ontology-explorer",
		tabLabel: "5 · Ontology Explorer",
		title: "Step 5 — Ontology Explorer (Graph A) & OCC Revision Commit",
		route: "127.0.0.1:8420 · Project Workflow · Step 5",
		theme: "light",
		webpPath: "/screenshots/shot-05-diff-and-graph.webp",
		pngPath: "/screenshots/shot-05-diff-and-graph.png",
		width: 2880,
		height: 1840,
		alt: "Step 5 Ontology Explorer showing 4 editor tabs, React Flow Graph A canvas with 12 concepts, and Graph B Evidence attribute editor.",
		summary:
			"Curate Graph A across 4 modes (Canvas & Inspector, Catalog & Keys, YAML CST Editor, Validation & Waivers) while committing OCC-protected revisions (ont-0.1.0-*).",
		pins: [
			{
				id: "s05-p1",
				label: "4-Mode Editor & OCC Digest",
				xPct: 26,
				yPct: 23,
				detail:
					"Switch freely between Canvas, Key Tier Catalog, YAML CST Editor, and ONT-AP Validation/Waivers before committing ont-*.",
			},
			{
				id: "s05-p2",
				label: "Graph A Concept Canvas",
				xPct: 30,
				yPct: 54,
				detail:
					"Inspect empirical concepts, filter by attached source, stage AddConcept or AddRelationship deltas, and fit the topology.",
			},
			{
				id: "s05-p3",
				label: "Graph B Evidence & BK/PII Authoring",
				xPct: 78,
				yPct: 42,
				detail:
					"View bound tables/citations/receipts for the selected concept, append BK or PII attributes, and stage Step 6 alignment proposals.",
			},
		],
	},
];
