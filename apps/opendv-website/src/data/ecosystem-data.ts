import type { ConnectorSpec, DialectSnippet, InferenceScenario } from "./types";

export const HARNESS_PROVIDERS: readonly string[] = [
	"agy",
	"claude-cli",
	"claude",
	"gemini",
];

export const INFERENCE_SCENARIOS: readonly InferenceScenario[] = [
	{
		id: "cold-start-w1",
		tabLabel: "1. Cold-Start Wave 1 (SCEN-01..04)",
		mode: "COLD_START_W1",
		inputs:
			"Blank Genesis or core_spine · SQLite IBOR + ESG Parquet + 1x Domain Notes + PDF",
		strategy:
			"Step 4 /coord + isolated /scout classify FIBO PATH_A1 (exactMatch >= 0.65), PATH_A2 (closeMatch), and PATH_B1 Beyond-FIBO empirical & 1x doc concepts.",
		algorithms: "FIBO BM25 (Decimal 28) · 125-perm MinHash · L1/V1–V8 CEGAR",
		outputs: "CertifiedVaultIR W001 + LOCK-W001 + 11 target DDL/format bundles",
	},
	{
		id: "multi-source-bkcc",
		tabLabel: "2. Cross-Modal BKCC & PII (SCEN-05/06/08)",
		mode: "MULTI_SOURCE_BKCC",
		inputs:
			"SQLite CIK + Doc Tier-1 LEI/ISIN + polysemic terms + scanned PDF p4 + PII email",
		strategy:
			"Resolves Clarification Holds (L3/Step 4), enforces approve-scan & <MASKED_PII>, splits sat_*_pii, and merges Tier-1 BK > Tier-2 BKCC across feeds.",
		algorithms:
			"KeyTierEnum 1–3 · Alg A.9b Spans · sal_* Same-As Links · PII Split",
		outputs:
			"Guided Decision Cards + AI_PROVENANCE_SATELLITE + sat_proxy_ballot_pii",
	},
	{
		id: "additive-ext-w2",
		tabLabel: "3. Wave 2 Drift & Quarantine (SCEN-07/09)",
		mode: "ADDITIVE_EXT_W2",
		inputs:
			"LOCK-W001 · wave2_eu_custody_lei_and_drift.parquet + PATH_B2 candidate",
		strategy:
			"Quarantines ungrounded PATH_B2 until >= 20-char waive_quarantine; preserves immutable W001 hash_diff while routing W002 drift into sat_*_ext_v2.",
		algorithms:
			"Alg A.7 Evolution Locks · Alg A.9a Drift · OCC base_revision_id",
		outputs:
			"CertifiedVaultIR W002 + LOCK-W002 + additive EVO-04 V002_migrate.sql",
	},
	{
		id: "horizon-steer",
		tabLabel: "4. V8 Horizon Steer & EvalGate",
		mode: "HORIZON_STEER",
		inputs:
			"Certified Wave 2 IR · BRK-01..08 future traps + 11 storage targets",
		strategy:
			"V8 runs Bucket A (<= 1 warm steer), Bucket B Steward Guided choice, and Bucket C watchlist; Step 7 verifies EVAL-AST-01, EVAL-RUN-01..04 & EVAL-FMT.",
		algorithms:
			"V8 Horizon Critic · SqlGlotVaultCompiler · Pure-IR Oracle Parity",
		outputs:
			"targets/{duckdb_local,databricks_uc,snowflake,iceberg,parquet,dbt}/",
	},
];

export const CONNECTORS: readonly ConnectorSpec[] = [
	{
		id: "sqlite",
		name: "SQLite Catalog",
		executionClass: "LOCAL_FILE_INSPECT_AND_ATTACH",
		introspection:
			"Zero-LLM PRAGMA table_info & foreign_key_list inspect PK/FK constraints and <= 5 masked samples.",
	},
	{
		id: "duckdb",
		name: "DuckDB Databases",
		executionClass: "LOCAL_FILE_INSPECT_AND_ATTACH",
		introspection:
			"Local attachment for fast profiling, V6 15-invariant proving, and AST-checked read-only SQL.",
	},
	{
		id: "parquet-csv-xlsx",
		name: "Parquet / CSV / XLSX",
		executionClass: "COLUMNAR_VECTOR_PROFILE",
		introspection:
			"Vectorized columnar and openpyxl scans grouped via 125-permutation (25×5) MinHash clustering.",
	},
	{
		id: "databricks-uc",
		name: "Databricks Unity Catalog",
		executionClass: "SSRF_GUARDED_KEYRING_PUSHDOWN",
		introspection:
			"CIDR SSRF-blocked HTTPS pushdown using out-of-repo KeyringSecretStore refs and UC PII/MNPI tags.",
	},
	{
		id: "pdf-docx-md",
		name: "PDF / DOCX / Markdown",
		executionClass: "SYNCHRONOUS_DOCUMENT_MAP",
		introspection:
			"Synchronous pypdf/pypdfium2/docx DocumentMap outlines with Steward approve-scan consent for OCR pages.",
	},
	{
		id: "domain-notes-mcp",
		name: "Domain Notes & External MCP",
		executionClass: "SYNCHRONOUS_DOCUMENT_MAP",
		introspection:
			"UTF-8 domain_note rules (1x Semantic) plus Step 4 ExternalMcpGateway with <MASKED_PII> & SHA-256 receipts.",
	},
];

export const DIALECT_SNIPPETS: readonly DialectSnippet[] = [
	{
		id: "duckdb-sql",
		label: "DuckDB 5-Zone SQL",
		lang: "sql",
		filename: "targets/duckdb_local/ddl/full_schema_w2.sql",
		code: `-- Z0–Z4 5-Zone SQL: bronze, staging, raw_vault, business_vault, information_mart
CREATE SCHEMA IF NOT EXISTS information_mart;
SELECT UNHEX(SHA256(
  '33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F||' ||
  COALESCE(CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR)
  || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''), '-1:^^')
)) AS hk_holding FROM bronze.stg_src_holdings_13f;`,
	},
	{
		id: "databricks-sql",
		label: "Databricks Delta SQL",
		lang: "sql",
		filename: "targets/databricks_uc/ddl/full_schema_w2.sql",
		code: `CREATE TABLE IF NOT EXISTS raw_vault.hub_holding (
  hk_holding BINARY NOT NULL, bkcc STRING NOT NULL,
  holding_id STRING NOT NULL, load_dts TIMESTAMP_NTZ NOT NULL, rec_src STRING NOT NULL
) USING DELTA;

MERGE INTO raw_vault.hub_holding AS target
USING (SELECT UNHEX(REPEAT('0', 64)) AS hk_holding, 'SYSTEM' AS bkcc, '-1' AS holding_id) AS src
ON target.hk_holding = src.hk_holding
WHEN NOT MATCHED THEN INSERT *;`,
	},
	{
		id: "vault-ir-json",
		label: "VaultSchemaIR v2.0",
		lang: "json",
		filename: "waves/W002.json",
		code: `{
  "schema_version": "2.0",
  "project_id": "am-ibor-2026",
  "ontology_version": "0.2.0",
  "wave_number": 2,
  "certification_status": "CERTIFIED",
  "v7_score": 95,
  "v8_resilience_score": 92,
  "evolution_lock_id": "LOCK-W002"
}`,
	},
	{
		id: "opendv-cli",
		label: "Unified opendv CLI",
		lang: "bash",
		filename: "opendv_workflow.sh",
		code: `# Unified Python CLI (in-process or --api-url http://127.0.0.1:8420) & 17-tool stdio MCP
uv run opendv project init --project am-ibor-2026 --namespace asset_management --starter-pack core_spine
uv run opendv source attach --project am-ibor-2026 --kind sqlite --uri ./data/public_am/am_sec_treasury_ibor.sqlite
uv run opendv ontology fibo-qa --query "How does FIBO model CustodyAccount?"
uv run opendv vault build --project am-ibor-2026 --policy balanced --wait
uv run opendv vault logs --project am-ibor-2026 --since-seq 0
uv run opendv storage query --project am-ibor-2026 --sql "SELECT COUNT(*) FROM raw_vault.hub_holding"`,
	},
	{
		id: "loopback-guard",
		label: "Loopback & Remote Guard",
		lang: "bash",
		filename: "serve_guard.sh",
		code: `# Default loopback zero-auth open access (http://127.0.0.1:8420)
uv run opendv serve

# Non-loopback bind fails closed (CliExitCode 40) unless explicitly waived:
uv run opendv serve --host 0.0.0.0 --allow-remote-unauthenticated`,
	},
];
