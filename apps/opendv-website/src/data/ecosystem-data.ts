import type { ConnectorSpec, DialectSnippet, InferenceScenario } from "./types";

export const HARNESS_PROVIDERS: readonly string[] = [
	"agy",
	"claude-cli",
	"antigravity",
	"claude",
];

export const INFERENCE_SCENARIOS: readonly InferenceScenario[] = [
	{
		id: "day0-seed",
		tabLabel: "1. Day-0 Ontology Seed",
		mode: "COLD_START_N1",
		inputs: "contract: null · 0 physical tables",
		strategy:
			"Ontology Studio grounds domain notes against FIBO and synthesizes a greenfield seed.",
		algorithms: "FIBO BM25 Alignment · VaultBlueprint v1",
		outputs: "9 hubs, 10 links, 20 satellites + 4-Zone DDL preview",
	},
	{
		id: "sqlite-greenfield",
		tabLabel: "2. Greenfield SQLite + Files",
		mode: "COLD_START_N1",
		inputs: "contract: null · am_sec_treasury_ibor.sqlite (6 tables)",
		strategy:
			"DIRECT_DUCKDB_ATTACH reads PRAGMA PK/FK constraints before profiling Graph B.",
		algorithms: "Graph B Profiler · 3-Pass CoT · G0-G5 Sandbox",
		outputs: "Sealed Contract v1 + DuckDB & Databricks Delta SQL",
	},
	{
		id: "hybrid-drift",
		tabLabel: "3. Hybrid Databricks UC + Drift",
		mode: "HYBRID_PER_SOURCE",
		inputs:
			"contract: v1 · uc://am_prod.ibor.* + drifted holdings_13f + 10-K PDF",
		strategy:
			"Locks existing Hubs/Links, pushes UC stats down via Arrow, splits drifted cols & PDF spans.",
		algorithms: "Alg A.7 Evolution Lock · Alg A.9a Drift · Alg A.9b Doc Spans",
		outputs: "Revision 2 Blueprint + 15 synced Ontology Deltas",
	},
	{
		id: "verified-noop",
		tabLabel: "4. Zero-Data Audit Pass",
		mode: "VERIFIED_NO_OP",
		inputs: "contract: v2 · 0 new sources",
		strategy:
			"Re-proves Graph A alignment and invariants, then emits multi-dialect SQL bundles.",
		algorithms: "Static Proof Replay · Multi-Dialect Compiler",
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
			"Zero-copy local attachment for fast profiling and 4-Batch shadow verification.",
	},
	{
		id: "files",
		name: "Parquet / CSV / JSON",
		executionClass: "DIRECT_DUCKDB_ATTACH",
		introspection:
			"Streaming vectorized scans across partitioned tabular drops without baseline bloat.",
	},
	{
		id: "docs",
		name: "PDF & Regulatory Filings",
		executionClass: "DOCUMENT_SPAN_EXTRACTOR",
		introspection:
			"LiteParse extracts verbatim [char_start, char_end] offsets for Tier-U1..U3 resolution.",
	},
];

export const DIALECT_SNIPPETS: readonly DialectSnippet[] = [
	{
		id: "duckdb-sql",
		label: "DuckDB 4-Zone SQL",
		lang: "sql",
		filename: "dv_duckdb_schema.sql",
		code: `-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)
CREATE SCHEMA IF NOT EXISTS raw_vault;
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
		label: "Dual CLIs (opendv / opendvctl)",
		lang: "bash",
		filename: "ci_verify.sh",
		code: `# In-process standalone Python CLI (air-gapped CI)
uv run opendv verify -c final_contract.yaml -b graph_b.json --apply-codemods --json

# Contract-identical standalone Bun platform client
opendvctl pipeline run --api-url "$OPENDV_API_URL" -i pipeline_input.yaml --json`,
	},
];
