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
			"Data Vault 2.0 gives enterprises complete historical auditability and resilient multi-source integration. Yet if you ask an LLM to write Data Vault SQL directly, subtle mistakes—delimiter hash collisions, leaked surrogate keys, or destructive satellite updates—silently corrupt your audit trail. Open-DV solves this with a neuro-symbolic architecture: agents discover semantics and propose a declarative VaultBlueprint, while a deterministic six-gate verifier and compiler prove every invariant before generating SQL.",
	},
	{
		id: "ch-2",
		number: 2,
		timestamp: "0:28",
		startSec: 28,
		endSec: 62,
		title: "Ontology Studio & FIBO",
		callouts: [
			"EDM Council FIBO Alignment (BM25 + 3-Persona Engine)",
			"1-Click Handoff -> VaultBlueprint v1",
		],
		transcript:
			"Every model begins with business meaning in the Ontology Construction Studio. Drop in regulatory filings, schemas, or domain notes—even in Confidential air-gapped mode. Open-DV grounds candidate concepts against EDM Council FIBO standards, surfaces targeted clarification questions, and stages a reviewable proposal in Content-Addressable Storage. Once accepted, you can inspect the interactive semantic graph, preview its seed Data Vault DDL, and send the signed VaultBlueprint straight into the Pipeline Builder.",
	},
	{
		id: "ch-3",
		number: 3,
		timestamp: "1:02",
		startSec: 62,
		endSec: 98,
		title: "Run 1: SQLite Greenfield",
		callouts: [
			"Zero Manual wave_type — Data-Driven Inference",
			"SQLite PRAGMA PK/FK Introspection",
		],
		transcript:
			"Inside the four-step Pipeline Builder, there are no manual wave types or baseline CSVs to configure. Because our blueprint has no prior contract yet, Open-DV automatically infers a Greenfield design. In Step 2, we connect a multi-table Asset Management SQLite database. Open-DV introspects every table, primary key, and foreign key in read-only mode. In Step 3, we set governance policies and select both DuckDB and Databricks Delta as output SQL dialects.",
	},
	{
		id: "ch-4",
		number: 4,
		timestamp: "1:38",
		startSec: 98,
		endSec: 125,
		title: "6-Gate axi-dv & Sandbox",
		callouts: [
			"STAGE_00 .. STAGE_07",
			"0 Orphans · 0 Hash Collisions · Replay delta_rows = 0",
		],
		transcript:
			"Clicking Run Pipeline launches the eight-stage neuro-symbolic loop. Scouts profile uniqueness and entropy; Ship-Vault synthesizes 3-Tier Business Key Hubs, Links, and PII-isolated Satellites; and Gate 0 through Gate 5 verify the contract with SQLGlot AST checks, NetworkX bipartite graph proofs, and four adversarial batches inside an in-memory DuckDB four-zone shadow sandbox—auto-healing any violation via compact TOON traces before sealing Revision 1.",
	},
	{
		id: "ch-5",
		number: 5,
		timestamp: "2:05",
		startSec: 125,
		endSec: 152,
		title: "Run 2: Databricks UC + Drift",
		callouts: [
			"Inferred Plan: HYBRID_PER_SOURCE",
			"Alg A.7 Evolution Lock + Alg A.9a Companion Sat + Alg A.9b Doc Spans",
		],
		transcript:
			"Now watch what happens on Day 2 when upstream data changes. We pass Revision 1 back in alongside new Databricks Unity Catalog tables carrying PII governance tags, a drifted SQLite holdings table with new ESG columns, and a regulatory filing—all in a single run. Open-DV computes a Hybrid Per-Source plan: pushing aggregate profiling down to Databricks, locking existing Hubs and Links under Algorithm A.7, routing drifted columns into an additive companion satellite without touching historical hash diffs, and grounding document spans.",
	},
	{
		id: "ch-6",
		number: 6,
		timestamp: "2:32",
		startSec: 152,
		endSec: 165,
		title: "Multi-Dialect SQL & CLIs",
		callouts: [
			"github.com/nogoo9/open-dv",
			"uv sync --all-packages && uv run opendv serve --open",
		],
		transcript:
			"Export your cryptographically chained VaultBlueprint, Canonical IR, and executable four-zone DuckDB and Databricks Delta SQL—or run the exact same workflow headlessly in CI with `opendv` and `opendvctl`. Clone Open-DV on GitHub and build your first verified vault in five minutes.",
	},
];
