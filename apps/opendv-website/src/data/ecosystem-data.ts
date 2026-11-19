import type { ConnectorSpec, DialectSnippet, InferenceScenario } from "./types";

export const HARNESS_PROVIDERS: readonly string[] = [
	"agy",
	"agy-cli",
	"antigravity",
	"claude",
	"claude-cli",
	"claude-code",
];

export const INFERENCE_SCENARIOS: readonly InferenceScenario[] = [
	{
		id: "day0-seed",
		tabLabel: "1. Day-0 Ontology Seed",
		mode: "COLD_START_N1",
		inputs: "deployed_contract: null · 6 Starter Packs or Blank Genesis",
		strategy:
			"Unified Step 1–5 Wizard grounds notes against FIBO + Beyond-FIBO and commits a Graph A baseline.",
		algorithms: "FIBO BM25 Alignment · VaultBlueprint v1",
		outputs: "9 hubs, 10 links, 20 satellites + 5-Zone (Z0–Z4) DDL preview",
	},
	{
		id: "sqlite-greenfield",
		tabLabel: "2. Greenfield SQLite + Files",
		mode: "COLD_START_N1",
		inputs: "deployed_contract: null · am_sec_treasury_ibor.sqlite (6 tables)",
		strategy:
			"DIRECT_DUCKDB_ATTACH reads PRAGMA PK/FK constraints before profiling Graph B (or Fast-Forward to Step 6).",
		algorithms: "Graph B Profiler · 3-Pass CoT · G0-G5 Sandbox",
		outputs: "Sealed Contract v1 + DuckDB & Databricks 5-Zone SQL",
	},
	{
		id: "hybrid-drift",
		tabLabel: "3. Hybrid Databricks UC + Drift",
		mode: "HYBRID_PER_SOURCE",
		inputs:
			"auto-bound deployed_contract v1 · uc://am_prod.ibor.* + drifted holdings + PDF",
		strategy:
			"Locks Hubs/Links, pushes UC stats down via Arrow, splits drifted cols & PDF spans, syncs drift to Step 4.",
		algorithms: "Alg A.7 Evolution Lock · Alg A.9a Drift · Alg A.9b Doc Spans",
		outputs: "Revision 2 Blueprint + 5-Zone information_mart + W1->W2->W3 diff",
	},
	{
		id: "verified-noop",
		tabLabel: "4. Zero-Data Audit Pass",
		mode: "VERIFIED_NO_OP",
		inputs: "deployed_contract: v2 · 0 new sources",
		strategy:
			"Re-proves Graph A alignment and invariants, then emits multi-dialect 5-Zone SQL + DV Schema Explorer state.",
		algorithms: "Static Proof Replay · Multi-Dialect 5-Zone Compiler",
		outputs: "final_contract.yaml · Canonical IR · DuckDB + Delta SQL",
	},
];

export const CONNECTORS: readonly ConnectorSpec[] = [
	{
		id: "sqlite",
		name: "SQLite Catalog",
		executionClass: "DIRECT_DUCKDB_ATTACH",
		introspection:
			"Read-only PRAGMA table_info & foreign_key_list inspect PK/FK before compute.",
	},
	{
		id: "databricks-uc",
		name: "Databricks Unity Catalog",
		executionClass: "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
		introspection:
			"Pushes aggregate SQL to warehouse; reads UC governance column_tags (pii, mnpi).",
	},
	{
		id: "duckdb",
		name: "DuckDB Databases",
		executionClass: "DIRECT_DUCKDB_ATTACH",
		introspection:
			"Zero-copy local attachment for fast profiling, shadow verification, and D11 Safe SQL.",
	},
	{
		id: "files",
		name: "Parquet / CSV / XLSX / JSON",
		executionClass: "DIRECT_DUCKDB_ATTACH",
		introspection:
			"Streaming vectorized scans across tabular drops with <= 50-row Step 3a preview.",
	},
	{
		id: "docs",
		name: "PDF / DOCX / Markdown + OCR",
		executionClass: "DOCUMENT_SPAN_EXTRACTOR",
		introspection:
			"LiteParse + configurable OCR (auto|force|off) extracts verbatim [char_start, char_end] spans.",
	},
	{
		id: "inline-notes",
		name: "Inline Domain Notes & Privacy",
		executionClass: "DOCUMENT_SPAN_EXTRACTOR",
		introspection:
			"Captures 1x Semantic SLA rules directly in Step 2 with per-source do_not_send_to_agent flags.",
	},
];

export const DIALECT_SNIPPETS: readonly DialectSnippet[] = [
	{
		id: "duckdb-sql",
		label: "DuckDB 5-Zone SQL",
		lang: "sql",
		filename: "dv_duckdb_schema.sql",
		code: `-- Z0–Z4 5-Zone SQL: bronze, staging, raw_vault, business_vault, information_mart
CREATE SCHEMA IF NOT EXISTS information_mart;
SELECT LOWER(SHA256(
  '33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F||' ||
  CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR)
  || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '')
)) AS hk_holding FROM bronze.src_hub_holding;`,
	},
	{
		id: "databricks-sql",
		label: "Databricks Delta SQL",
		lang: "sql",
		filename: "dv_databricks_schema.sql",
		code: `CREATE TABLE IF NOT EXISTS raw_vault.hub_holding (
  hk_holding STRING NOT NULL, bkcc STRING NOT NULL,
  holding_id STRING NOT NULL, load_dts TIMESTAMP NOT NULL, rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_holding AS target
USING (SELECT REPEAT('0', 64) AS hk_holding, 'SYSTEM' AS bkcc, '-1' AS holding_id) AS src
ON target.hk_holding = src.hk_holding
WHEN NOT MATCHED THEN INSERT *;`,
	},
	{
		id: "blueprint-yaml",
		label: "VaultBlueprint YAML",
		lang: "yaml",
		filename: "vault_blueprint.yaml",
		code: `schema_version: 1.0.0
domain_namespace: asset_management
lineage:
  blueprint_revision: 2
  ontology_sha256: 580b67f62f66115cd1baebe6a36171060df3f4da1acf0f63ba06991d87f11f17
  contract_sha256: d38b75515ed9a7f0da8d2562ee619741861a5565a774d05efd48607dbea17e30
  parent_blueprint_sha256: 7158c4242bd1bab561f9addb77187e24cfd837c3fe929ba098721ab93e4738dc`,
	},
	{
		id: "dual-cli",
		label: "Dual CLIs & D13 Subcommands",
		lang: "bash",
		filename: "cli_parity.sh",
		code: `# Query DV Schema Explorer & Safe SQL headlessly (Python or opendvctl)
uv run opendv run dv-schema --run-id run_09f4c2 --sql "SELECT * FROM information_mart.vw_position_current LIMIT 20"
uv run opendv ontology studio attach-source --session <id> --adapter sqlite --uri ./ibor.sqlite
uv run opendv ontology studio fibo-qa --session <id> --question "How does FIBO model CustodyAccount?"
opendvctl ontology studio logs --session <id> --stage STAGE_04_SHIP_VAULT_PASS2 --follow`,
	},
	{
		id: "loopback-guard",
		label: "Loopback & Remote Guard",
		lang: "bash",
		filename: "serve_guard.sh",
		code: `# Default loopback zero-auth open access (http://127.0.0.1:8420)
uv run opendv serve --open

# Non-loopback bind fails closed (exit 40) unless explicitly waived:
uv run opendv serve --host 0.0.0.0 --allow-remote-unauthenticated`,
	},
];
