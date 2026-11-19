import type { WorkbenchScreen } from "./types";

export const WIZARD_SCREENS: readonly WorkbenchScreen[] = [
	{
		id: "step1-baseline",
		tabLabel: "1 · Scope & Baseline",
		title: "Step 1 — Scope, Starter Packs & Baseline Digest",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-01-step1-baseline.webp",
		pngPath: "/screenshots/shot-01-step1-baseline.png",
		width: 2880,
		height: 1840,
		alt: "Step 1 Scope and Baseline screen showing Construct New with 6 starter packs, Evolve Existing sessions, and dark baseline digest bar.",
		summary:
			"Start Blank Genesis (v0.0.0), Workspace Auto-Seed, or 6 Asset-Management starter packs—or evolve an existing session or imported .yaml/.json/.ttl graph.",
		pins: [
			{
				id: "s01-p1",
				label: "Construct New & 6 Starter Packs",
				xPct: 24,
				yPct: 44,
				detail:
					"Create new-schema sessions (D25) from Blank Genesis, Workspace Auto-Seed, or 6 vertical asset-management packs.",
			},
			{
				id: "s01-p2",
				label: "Evolve Existing & External .ttl Import",
				xPct: 72,
				yPct: 36,
				detail:
					"Select any prior studio session or drop a Graph A YAML, JSON, or OWL/Turtle (.ttl) ontology baseline.",
			},
			{
				id: "s01-p3",
				label: "Dark Baseline Digest & Agent Pill",
				xPct: 72,
				yPct: 87,
				detail:
					"Displays base SHA digest, active Agent Ready pill, and loopback Open Access workspace readiness.",
			},
		],
	},
	{
		id: "step2-sources",
		tabLabel: "2 · Add Sources",
		title: "Step 2 — Multi-Connector Ingestion, OCR & Inline Notes",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-02-step2-sources.webp",
		pngPath: "/screenshots/shot-02-step2-sources.png",
		width: 2880,
		height: 1840,
		alt: "Step 2 Add Diff Data Sources displaying SQLite, Databricks UC, DuckDB, Parquet/CSV/JSON, Unstructured OCR upload, and Inline Domain Note cards.",
		summary:
			"Attach SQLite, Databricks Unity Catalog, DuckDB, Parquet/CSV/XLSX/JSON, PDF/DOCX/MD with OCR (auto|force|off) and do_not_send_to_agent privacy flags, plus inline notes.",
		pins: [
			{
				id: "s02-p1",
				label: "Structured Connectors & UC Tags",
				xPct: 36,
				yPct: 23,
				detail:
					"Read-only SQLite PRAGMA, DuckDB attach, Databricks Unity Catalog (PII/MNPI tags), and columnar file uploads.",
			},
			{
				id: "s02-p2",
				label: "Unstructured OCR & Inline Notes",
				xPct: 48,
				yPct: 33,
				detail:
					"Parse PDF/DOCX/MD with configurable OCR mode, optional LLM redaction, or quick-add business glossary markdown.",
			},
			{
				id: "s02-p3",
				label: "Unified Session Sources Ledger",
				xPct: 68,
				yPct: 55,
				detail:
					"Tracks profiled table counts, foreign keys, PII/MNPI markers, and parsed document chunk counts before Step 3.",
			},
		],
	},
	{
		id: "step3a-structured",
		tabLabel: "3a · Schema Preview",
		title: "Step 3a — Structured Schema, Stats & 50-Row Preview",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-03a-structured-preview.webp",
		pngPath: "/screenshots/shot-03a-structured-preview.png",
		width: 2880,
		height: 1840,
		alt: "Step 3a Structured Schema and Sample Rows inspecting Databricks UC alpha_factor_signals schema, MNPI tag, and dark 50-row sample preview.",
		summary:
			"Inspect inferred SQL types, PK/FK constraints, null%/distinct stats, Beyond-FIBO candidate badges, MNPI 5-row prompt caps, and Fast-Forward to Step 6 (D8).",
		pins: [
			{
				id: "s03a-p1",
				label: "Profiled Source & FIBO Coverage Rail",
				xPct: 12,
				yPct: 34,
				detail:
					"Lists attached Databricks UC and SQLite tables with Non-FIBO, PII/+Attrs, and FIBO alignment indicators.",
			},
			{
				id: "s03a-p2",
				label: "Column Stats & MNPI Prompt Cap",
				xPct: 58,
				yPct: 34,
				detail:
					"Surfaces SQL types, PK/FK constraints, null%/distinct counts, and sensitive MNPI tagging before LLM calls.",
			},
			{
				id: "s03a-p3",
				label: "50-Row Sample & Fast-Forward (D8)",
				xPct: 72,
				yPct: 96,
				detail:
					"Inspect up to 50 local sample rows or skip straight to Step 6 DV 2.0 build when your ontology is already complete.",
			},
		],
	},
	{
		id: "step3b-parsed-chunks",
		tabLabel: "3b · Parsed Chunks",
		title: "Step 3b — Parsed Document Chunks & Observed Terms",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-03b-parsed-chunks.webp",
		pngPath: "/screenshots/shot-03b-parsed-chunks.png",
		width: 2880,
		height: 1840,
		alt: "Step 3b Parsed Document Chunks showing Chunk 12 prose highlight, Chunk 19 embedded table, character offsets, and zero mention floor.",
		summary:
			"Verify LiteParse prose chunks, embedded tables, exact [char_start, char_end] offsets, OCR confidence, and informational term frequencies with zero mention floor (D4).",
		pins: [
			{
				id: "s03b-p1",
				label: "Zero Mention Floor (D4)",
				xPct: 14,
				yPct: 50,
				detail:
					"Even 1-mention inline SLA rules are surfaced as first-class candidates rather than filtered by frequency floors.",
			},
			{
				id: "s03b-p2",
				label: "Verbatim Char Spans & Confidence",
				xPct: 86,
				yPct: 28,
				detail:
					"Every prose chunk and inline note records exact character boundaries and OCR extraction confidence.",
			},
			{
				id: "s03b-p3",
				label: "Embedded Table Extraction",
				xPct: 52,
				yPct: 44,
				detail:
					"Extracts multi-column PDF tables into structured grids ready for semantic concept and attribute discovery.",
			},
		],
	},
	{
		id: "step4a-agent-setup-gate",
		tabLabel: "4a · Agent Gate",
		title: "Step 4a — Fail-Closed Agent Setup Gate (422)",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-04a-agent-setup-gate.webp",
		pngPath: "/screenshots/shot-04a-agent-setup-gate.png",
		width: 2880,
		height: 1840,
		alt: "Step 4a Mandatory Agent Setup Gate showing coral 422 AGENT_NOT_CONFIGURED banner and shared AgentHarnessConfigCard provider configurator.",
		summary:
			"Agent-only UI (D28) with zero silent deterministic fallback: surfaces HTTP 422 AGENT_NOT_CONFIGURED and shared <AgentHarnessConfigCard /> (P2) to verify providers live.",
		pins: [
			{
				id: "s04a-p1",
				label: "422 AGENT_NOT_CONFIGURED Callout",
				xPct: 52,
				yPct: 22,
				detail:
					"Explicit coral alert blocks execution until a real LLM harness provider passes readiness verification.",
			},
			{
				id: "s04a-p2",
				label: "Shared <AgentHarnessConfigCard /> (P2)",
				xPct: 36,
				yPct: 40,
				detail:
					"Single canonical provider/model/budget form mounted identically in Step 4a and Settings across 6 harness adapters.",
			},
			{
				id: "s04a-p3",
				label: "Live Verify & Save Bar",
				xPct: 84,
				yPct: 88,
				detail:
					"Tests provider credentials and flips the topbar status pill to green Agent Ready without leaving Step 4.",
			},
		],
	},
	{
		id: "step4b-agent-discovery",
		tabLabel: "4b · Cherry-Pick & Q&A",
		title: "Step 4b — Single-Pass Discovery, Cherry-Pick & Dual Q&A",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-04b-agent-discovery.webp",
		pngPath: "/screenshots/shot-04b-agent-discovery.png",
		width: 2880,
		height: 1840,
		alt: "Step 4b Agent Discovery displaying 3 columns for Structured Proposals, Unstructured Semantic Proposals, and FIBO Match plus Ask FIBO and Sources Q&A.",
		summary:
			"Single-pass @term-scout -> @domain-clarifier -> @ontology-architect synthesis: Beyond-FIBO entities, 1x Semantic rules, unchecked MAJOR Opt-In drops, and Ask FIBO Q&A.",
		pins: [
			{
				id: "s04b-p1",
				label: "Structured & MAJOR Opt-In",
				xPct: 18,
				yPct: 58,
				detail:
					"Groups Beyond-FIBO + Custom Entity proposals and leaves breaking MAJOR column drops unchecked by default.",
			},
			{
				id: "s04b-p2",
				label: "1x · Semantic Document Rules",
				xPct: 50,
				yPct: 68,
				detail:
					"Single-mention mandatory SLA rules from inline notes or PDFs appear checked alongside high-frequency entities.",
			},
			{
				id: "s04b-p3",
				label: "FIBO Match & Ask FIBO & Sources",
				xPct: 82,
				yPct: 54,
				detail:
					"Resolves ambiguous entity clarifications and answers ad-hoc FIBO + attached source questions with citations.",
			},
		],
	},
	{
		id: "step4c-citation-fibo",
		tabLabel: "4c · Citation Drawer",
		title: "Step 4c — 716px Citation & FIBO Reference Drawer",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-04c-citation-fibo.webp",
		pngPath: "/screenshots/shot-04c-citation-fibo.png",
		width: 2880,
		height: 1840,
		alt: "Step 4c slide-over Citation and FIBO Inspector drawer showing highlighted PDF char offsets, structured column stats, and side-by-side FIBO definitions.",
		summary:
			"716×920 slide-over drawer inspects table column profiles, amber-highlighted PDF/note spans with Step 3 jump links, and side-by-side EDM Council FIBO definitions.",
		pins: [
			{
				id: "s04c-p1",
				label: "Structured Column Provenance",
				xPct: 74,
				yPct: 22,
				detail:
					"Shows exact source URI, SQL type, null%/distinct stats, masked samples, and 1-click Open in Step 3a navigation.",
			},
			{
				id: "s04c-p2",
				label: "Highlighted PDF & Note Offsets",
				xPct: 74,
				yPct: 35,
				detail:
					"Highlights verbatim span text in warm amber with page number, chunk ID, char range, and OCR confidence.",
			},
			{
				id: "s04c-p3",
				label: "Side-by-Side FIBO Spec Comparison",
				xPct: 74,
				yPct: 64,
				detail:
					"Compares candidate FIBO URIs, class hierarchies, and official definitions before confirming or flagging custom.",
			},
		],
	},
	{
		id: "step5-diff-and-graph",
		tabLabel: "5 · Diff & Graph",
		title: "Step 5 — Visual Diff, Warm-Paper DAG & Commit",
		route: "127.0.0.1:8420 · Ontology & Pipeline",
		theme: "light",
		webpPath: "/screenshots/shot-05-diff-and-graph.webp",
		pngPath: "/screenshots/shot-05-diff-and-graph.png",
		width: 2880,
		height: 1840,
		alt: "Step 5 Diff Graph and Commit showing warm dot-grid elkjs ontology DAG, Concept Inspector with PII Split and MNPI badges, and Export Ontology Only button.",
		summary:
			"Review +FIBO, +Custom, and ~Enriched diffs on a warm dot-grid elkjs DAG, optionally open VisualEditorDrawer (D15), Export Ontology Only (D9), or commit to CAS.",
		pins: [
			{
				id: "s05-p1",
				label: "Warm Dot-Grid elkjs DAG",
				xPct: 38,
				yPct: 48,
				detail:
					"Color-codes Baseline, +FIBO, +Custom, DOC, and Selected entities with terminal IRI labels and FK/Doc edges.",
			},
			{
				id: "s05-p2",
				label: "Concept Inspector & Governance Tags",
				xPct: 84,
				yPct: 42,
				detail:
					"Displays archetype, FIBO alignment score, typed attributes, PII Split -> sat_pii badges, and grounded citations.",
			},
			{
				id: "s05-p3",
				label: "Export Ontology Only (D9) & Commit",
				xPct: 76,
				yPct: 96,
				detail:
					"Export Graph A YAML/Turtle (.ttl) without running DV 2.0, or commit atomically and advance to Step 6.",
			},
		],
	},
];
