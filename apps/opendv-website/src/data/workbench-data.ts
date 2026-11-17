import type { WorkbenchScreen } from "./types";

export const WORKBENCH_SCREENS: readonly WorkbenchScreen[] = [
	{
		id: "ontology-studio",
		tabLabel: "1. Ontology Studio",
		title: "Ontology Construction Studio (/ontology)",
		route: "localhost:8420/ontology",
		theme: "dark",
		webpPath: "/screenshots/shot-01-ontology-studio-dag.webp",
		pngPath: "/screenshots/shot-01-ontology-studio-dag.png",
		width: 2880,
		height: 1800,
		alt: "Open-DV Ontology Construction Studio displaying a 9-concept Asset Management elkjs DAG and FinancialInstrument Concept Editor.",
		summary:
			"Curate enterprise concepts grounded in EDM Council FIBO, edit ELM archetypes in the side inspector, and commit immutable CAS versions.",
		pins: [
			{
				id: "s1-p1",
				label: "FIBO-Grounded Semantic DAG",
				xPct: 58,
				yPct: 48,
				detail:
					"9 concepts laid out in 25.0 ms via elkjs (FinancialInstrument, Position, Bond, Valuation, RateBenchmark, LegalEntity, Account, Portfolio, Transaction).",
			},
			{
				id: "s1-p2",
				label: "Concept Editor & Target Hub",
				xPct: 86,
				yPct: 35,
				detail:
					"Inspects ELM archetype (CORE_BUSINESS_CONCEPT), business definition, target_hub binding, and mapped attributes.",
			},
			{
				id: "s1-p3",
				label: "CAS Version & RBAC Strip",
				xPct: 77,
				yPct: 4,
				detail:
					"Displays session asset-management-v1 at immutable tag v1.0.0 under Admin clearance in local CAS mode.",
			},
		],
	},
	{
		id: "blueprint-ddl",
		tabLabel: "2. DV 2.0 Blueprint",
		title: "Ontology-to-Vault Blueprint Projection (/ontology)",
		route: "localhost:8420/ontology#blueprint",
		theme: "dark",
		webpPath: "/screenshots/shot-02-blueprint-ddl.webp",
		pngPath: "/screenshots/shot-02-blueprint-ddl.png",
		width: 2880,
		height: 1800,
		alt: "DV 2.0 Blueprint tab showing 40 target Raw Vault tables and generated 4-zone DuckDB DDL with Open in Pipeline Builder button.",
		summary:
			"Projects any accepted ontology revision deterministically into Hub, Link, and Satellite tables with live 4-Zone SQL preview and 1-click Builder handoff.",
		pins: [
			{
				id: "s2-p1",
				label: "Deterministic Projection Count",
				xPct: 38,
				yPct: 17,
				detail:
					"Projects 9 concepts and 10 relationships into 40 target Raw Vault tables without writing custom prompts.",
			},
			{
				id: "s2-p2",
				label: "Dual-Encoded Vault Pills",
				xPct: 46,
				yPct: 38,
				detail:
					"Separates Hubs (10), Links (10), and Satellites (20) using dual text and semantic color encoding.",
			},
			{
				id: "s2-p3",
				label: "1-Click Builder Handoff",
				xPct: 91,
				yPct: 24,
				detail:
					"Copies or downloads SQL DDL directly, or forwards the signed VaultBlueprint straight into Pipeline Builder.",
			},
		],
	},
	{
		id: "catalog-picker",
		tabLabel: "3. Multi-Connector Picker",
		title: "4-Step Builder & SQLite Catalog Picker (/builder)",
		route: "localhost:8420/builder",
		theme: "light",
		webpPath: "/screenshots/shot-03-catalog-picker-modal.webp",
		pngPath: "/screenshots/shot-03-catalog-picker-modal.png",
		width: 2880,
		height: 1800,
		alt: "Pipeline Builder Step 2 CatalogTablePickerModal inspecting 6 tables and PK/FK columns in am_sec_treasury_ibor.sqlite.",
		summary:
			"Auto-detects Greenfield mode without manual waves and introspects SQLite, DuckDB, files, or Databricks Unity Catalog tables read-only.",
		pins: [
			{
				id: "s3-p1",
				label: "Inferred Mode Badge",
				xPct: 80,
				yPct: 10,
				detail:
					"mode_detector.py automatically infers Greenfield Future-Proof Design (COLD_START_N1).",
			},
			{
				id: "s3-p2",
				label: "Read-Only Catalog Table Picker",
				xPct: 45,
				yPct: 25,
				detail:
					"Inspects all 6 tables inside am_sec_treasury_ibor.sqlite under namespace main.",
			},
			{
				id: "s3-p3",
				label: "Extracted PK / FK Badges",
				xPct: 48,
				yPct: 58,
				detail:
					"Surfaces PRAGMA primary key ticker [PK (#1)] and cik [FK] bindings prior to agent synthesis.",
			},
		],
	},
	{
		id: "hybrid-diff",
		tabLabel: "4. Hybrid Diff & SQL",
		title: "Hybrid Evolution Diff & Artifacts (/builder)",
		route: "localhost:8420/builder#step-4",
		theme: "light",
		webpPath: "/screenshots/shot-04-hybrid-evolution-diff.webp",
		pngPath: "/screenshots/shot-04-hybrid-evolution-diff.png",
		width: 2880,
		height: 1800,
		alt: "Pipeline Builder Step 4 Evolution Diff showing STAGE_00-07 green, Gate G0-G5 Passed, Added Hubs/Links/Satellites, Ontology Deltas, and 4 download buttons.",
		summary:
			"Proves Gate G0-G5 and 4-Zone DuckDB verification across SQLite + Databricks feeds, lists every structural diff and Ontology Delta, and exports sealed YAML/SQL.",
		pins: [
			{
				id: "s4-p1",
				label: "Gates & 4-Zone Proofs",
				xPct: 46,
				yPct: 8,
				detail:
					"STAGE_00..07 finish green with both Gate G0-G5 Passed and 4-Zone Verification Passed banners.",
			},
			{
				id: "s4-p2",
				label: "Hub/Link/Sat & Ontology Deltas",
				xPct: 46,
				yPct: 38,
				detail:
					"Lists 9 Added Hubs, 5 Links, 10 Satellites (including sat_positions_pii), and 15 synced Ontology Deltas.",
			},
			{
				id: "s4-p3",
				label: "Sealed Bundle Downloads",
				xPct: 35,
				yPct: 96,
				detail:
					"Direct downloads for final_contract.yaml, vault_blueprint.yaml, dv_duckdb_schema.sql, and dv_databricks_schema.sql.",
			},
		],
	},
	{
		id: "explorer-3layer",
		tabLabel: "5. 3-Level Explorer",
		title: "3-Level Explainability Explorer (/)",
		route: "localhost:8420/",
		theme: "light",
		webpPath: "/screenshots/shot-05-explorer-3layer.webp",
		pngPath: "/screenshots/shot-05-explorer-3layer.png",
		width: 2880,
		height: 1800,
		alt: "Open-DV 3-Level Explainability Explorer showing 8/8 Stages Complete, 6/6 gates PASS, Inputs, Pipeline Stages, and CAS-sealed Outputs.",
		summary:
			"Inspect sealed pipeline telemetry end-to-end: settings and inputs on the left, L0-L2 stage cards in the center, and CAS-sealed deliverables on the right.",
		pins: [
			{
				id: "s5-p1",
				label: "6/6 Gates Scorecard",
				xPct: 50,
				yPct: 11,
				detail:
					"Top progress strip displays SEALED 8 / 8 Stages Complete (100%) and 6/6 gates PASS.",
			},
			{
				id: "s5-p2",
				label: "Semantic Stage Cards",
				xPct: 48,
				yPct: 41,
				detail:
					"Shows PASS, AUTO-FIX, and resolved ASK-USER outcomes per stage with actor persona and temperature.",
			},
			{
				id: "s5-p3",
				label: "CAS-Sealed Outputs Rail",
				xPct: 86,
				yPct: 44,
				detail:
					"Inspects final_contract.yaml, final_canonical_ir.json, final_four_zone.sql, pipeline_report.json, and vault-ledger.jsonl.",
			},
		],
	},
];
