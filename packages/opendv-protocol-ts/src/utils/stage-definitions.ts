/**
 * Canonical 8-Stage Pipeline Explorer Metadata & Static Definitions
 * (`packages/opendv-agents/` and `packages/opendv-core/`).
 */

import type { PhaseSpec, Stage } from "../types/workbench-explorer";

export const EXPLORER_PHASES: readonly PhaseSpec[] = [
	{
		index: 0,
		label: "Discovery",
		sub: "LLM agents · read-only scouts",
	},
	{
		index: 1,
		label: "Modelling",
		sub: "LLM agent · declarative contract only",
	},
	{
		index: 2,
		label: "Verification",
		sub: "deterministic · symbolic gates",
	},
	{
		index: 3,
		label: "Compilation",
		sub: "deterministic · no LLM",
	},
] as const;

/**
 * Static canonical stage step & component definitions for the 8-stage Open-DV pipeline.
 */
export const CANONICAL_STAGE_DEFINITIONS: Stage[] = [
	{
		key: "STAGE_00_ORCHESTRATION",
		phase: 0,
		name: "Orchestration & Grounding",
		persona: "@vault-first-mate",
		tier: "pro · T=0.1",
		tag: "PASS",
		blurb:
			"Validates inputs, grounds source tables onto Graph A concepts, and schedules the 4 waves.",
		desc: "Checks input files and paths, maps every source table to a canonical business concept in Graph A, and plans the wave execution order.",
		ins: ["harness", "runopts", "spec", "ontology"],
		outs: ["grounding"],
		steps: [
			{
				name: "Pre-flight input validation",
				kind: "decision",
				branch: "invalid path or schema → abort run",
				reads: ["spec", "harness", "runopts"],
				writes: [],
				desc: "Checks YAML syntax, resolves relative file paths, and verifies that every CSV and document exists.",
			},
			{
				name: "Load Graph A ontology",
				reads: ["ontology"],
				writes: [],
				desc: "Parses the 5 canonical business concepts, their key tiers and relationships.",
			},
			{
				name: "Ground source tables to concepts",
				reads: ["spec", "ontology"],
				writes: ["grounding"],
				desc: "Maps each source table across Waves 0–3 to a Graph A concept.",
			},
			{
				name: "Plan 4-wave execution schedule",
				reads: ["spec", "grounding"],
				writes: ["grounding"],
				desc: "Orders Wave 0 → Wave 3 so each wave inherits the prior wave's verified contract.",
			},
		],
		comps: [
			[
				"vault-first-mate",
				"Agent",
				"Orchestrator (pro · T=0.1). Owns wave scheduling and steward holds.",
				"packages/opendv-agents/…/personas.py",
			],
			[
				"validate_pipeline_inputs",
				"Verifier",
				"Deterministic pre-flight validator.",
				"packages/opendv-agents/…/agentic_pipeline.py",
			],
			[
				"ContractLoader",
				"Parser",
				"Strict YAML/JSON schema loader.",
				"packages/opendv-core/…/contract_loader.py",
			],
		],
	},
	{
		key: "STAGE_01_SCOUT_SOURCE",
		phase: 0,
		name: "Relational Profiling & Drift",
		persona: "@scout-source",
		tier: "flash · T=0.0",
		tag: "AUTO-FIX",
		blurb:
			"Profiles 24 columns across 4 CSVs, quarantines row_seq_id, and classifies 3 Wave-2 drift changes.",
		desc: "Computes uniqueness and null ratios, flags surrogate sequences and sparse foreign keys, and runs Algorithm A.9a on Wave 2 schema drift.",
		note: "Wave 2 drift: loyalty_tier_2026 → companion sat · fax_number → NULL cast · credit_limit_usd → renamed",
		ins: ["crm", "erp", "stripe", "crm2"],
		outs: ["profile", "drift"],
		steps: [
			{
				name: "Scan headers & infer SQL types",
				reads: ["crm", "erp", "stripe", "crm2"],
				writes: [],
				desc: "Reads CSV headers and sample rows in read-only mode.",
			},
			{
				name: "Compute uniqueness U(C), null ratio & entropy",
				reads: ["crm", "erp", "stripe"],
				writes: ["profile"],
				desc: "Information-theoretic column metrics across all relational tables.",
			},
			{
				name: "Quarantine monotonic surrogate keys",
				kind: "decision",
				branch: "Δ=1 sequence → DROPPED_SURROGATE",
				reads: ["profile"],
				writes: ["profile"],
				desc: "Flags row_seq_id (U=1.00, step=1) so it cannot become a Hub business key.",
			},
			{
				name: "Flag sparse foreign keys (Algorithm A.4)",
				kind: "decision",
				branch: "U_nn < 0.95 → OPTIONAL_FK_REF",
				reads: ["erp", "profile"],
				writes: ["profile"],
				desc: "Flags promo_campaign_code (66.7% non-null) for isolating Link.",
			},
			{
				name: "Classify intra-source drift (Algorithm A.9a)",
				reads: ["crm", "crm2"],
				writes: ["drift"],
				desc: "Compares Wave 0 vs Wave 2 CRM: 1 additive, 1 deprecated, 1 renamed (J_val=0.996). Zero hash_diff breaks.",
			},
		],
		comps: [
			[
				"scout-source",
				"Agent",
				"Read-only relational profiler (flash · T=0.0).",
				"packages/opendv-agents/…/personas.py",
			],
			[
				"GraphBProfiler",
				"Algorithm",
				"Computes U(C), U_nn(C), H_norm(C) and inclusion dependencies.",
				"packages/opendv-core/…/profiler.py",
			],
			[
				"SchemaDriftClassifier",
				"Algorithm",
				"Algorithm A.9a intra-source drift classifier.",
				"packages/opendv-core/…/schema_drift.py",
			],
		],
	},
	{
		key: "STAGE_02_SCOUT_UNSTRUCTURED",
		phase: 0,
		name: "Unstructured Document Extraction",
		persona: "@scout-unstructured",
		tier: "flash · T=0.0",
		tag: "ASK-USER → resolved",
		blurb:
			"Extracts verbatim spans from the Wave-3 MSA; 1 exact match (Tier-U1) and 1 steward hold (Tier-U3).",
		desc: "Runs Algorithm A.9b over enterprise_msa_addendum_2026.txt, verifies character offsets, and routes low-confidence matches to a steward hold.",
		note: "Tier-U3 hold (0.68): 'Rheinland Precision Holdings Europe' — resolved by auto_approve_top_candidate",
		ins: ["msa", "ontology", "grounding"],
		outs: ["extract"],
		steps: [
			{
				name: "Read unstructured document text",
				reads: ["msa"],
				writes: [],
				desc: "Loads enterprise_msa_addendum_2026.txt as an immutable UTF-8 buffer.",
			},
			{
				name: "Extract candidate entity spans",
				reads: ["msa", "ontology"],
				writes: ["extract"],
				desc: "Proposes verbatim quotes for counterparties and guarantors.",
			},
			{
				name: "Verify exact substring offsets (Algorithm A.9b)",
				kind: "decision",
				branch: "doc[start:end] != quote → reject span",
				reads: ["msa", "extract"],
				writes: ["extract"],
				desc: "Checks character offsets against the source buffer to block hallucinated quotes.",
			},
			{
				name: "Score 3-tier entity resolution (U1 / U2 / U3)",
				reads: ["extract", "grounding"],
				writes: ["extract"],
				desc: "Tier-U1 (0.98) links TAX-US-900101; Tier-U3 (0.68) falls below auto-merge threshold.",
			},
			{
				name: "Route Tier-U3 ambiguity to steward hold",
				kind: "decision",
				branch: "policy: auto_approve_top_candidate",
				reads: ["runopts", "extract"],
				writes: ["extract"],
				desc: "Raises STEWARD_HOLD and resolves per the configured hold policy.",
			},
		],
		comps: [
			[
				"scout-unstructured",
				"Agent",
				"Unstructured span extractor (flash · T=0.0).",
				"packages/opendv-agents/…/personas.py",
			],
			[
				"SubstringSpanVerifier",
				"Algorithm",
				"Algorithm A.9b offset verifier & 3-tier ER scorer.",
				"packages/opendv-core/…/unstructured_er.py",
			],
		],
	},
	{
		key: "STAGE_03_SHIP_VAULT_PASS1",
		phase: 1,
		name: "Pass 1 · Column Triage",
		persona: "@ship-vault",
		tier: "pro · T=0.1",
		tag: "PASS",
		blurb:
			"Assigns every one of the 24 source columns to an explicit role (100% column coverage).",
		desc: "Produces the exhaustive column_triage_ledger so no source column is silently dropped and row_seq_id is explicitly tombstoned.",
		ins: ["ontology", "grounding", "profile", "drift", "extract"],
		outs: ["triage"],
		steps: [
			{
				name: "Enumerate all 24 columns across 5 sources",
				reads: ["profile", "drift", "extract"],
				writes: [],
				desc: "Builds the universe of columns that must be accounted for in Gate 1.",
			},
			{
				name: "Classify each column into 1 of 8 roles",
				reads: ["ontology", "profile"],
				writes: ["triage"],
				desc: "Assigns BUSINESS_KEY, FOREIGN_KEY_REF, OPTIONAL_FK_REF, DESCRIPTIVE_PAYLOAD, etc.",
			},
			{
				name: "Tombstone row_seq_id as DROPPED_SURROGATE",
				reads: ["profile"],
				writes: ["triage"],
				desc: "Records an explicit tombstone entry so Gate 1 bijection passes without polluting Hub keys.",
			},
		],
		comps: [
			[
				"ship-vault",
				"Agent",
				"Declarative DV 2.0 synthesiser (pro · T=0.1). Emits YAML/JSON only.",
				"packages/opendv-agents/…/personas.py",
			],
			[
				"ColumnTriageBijection",
				"Verifier",
				"Checks 1-to-1 column accounting.",
				"packages/opendv-core/…/gate1_schema_grain.py",
			],
		],
	},
	{
		key: "STAGE_04_SHIP_VAULT_PASS2",
		phase: 1,
		name: "Pass 2/3 · Vault Topology & Keys",
		persona: "@ship-vault",
		tier: "pro · T=0.1",
		tag: "PASS",
		blurb:
			"Synthesises 5 Hubs, 4 Links and 7 Satellites with PII isolation, companion drift sat and isolating Link.",
		desc: "Drafts the Data Vault 2.0 contract: Hubs anchored to Graph A concepts, unit-of-work Links, and Satellites split by PII classification and change rate.",
		ins: ["ontology", "triage", "drift", "extract"],
		outs: ["draft_contract"],
		steps: [
			{
				name: "Synthesise 5 Hubs from Graph A concepts",
				reads: ["ontology", "triage"],
				writes: ["draft_contract"],
				desc: "Creates hub_customer, hub_order, hub_invoice, hub_promotion and hub_contract_document.",
			},
			{
				name: "Synthesise 4 Links (including isolating promo Link)",
				reads: ["triage"],
				writes: ["draft_contract"],
				desc: "Routes sparse promo_campaign_code into lnk_order_promotion (Algorithm A.4).",
			},
			{
				name: "Split RESTRICTED_PII into sat_customer_pii",
				reads: ["triage"],
				writes: ["draft_contract"],
				desc: "Isolates email and phone from general customer attributes for GDPR erasure.",
			},
			{
				name: "Attach Wave-2 companion sat_customer_loyalty_2026",
				reads: ["drift"],
				writes: ["draft_contract"],
				desc: "Absorbs loyalty_tier_2026 without altering sat_customer_core.hash_diff.",
			},
		],
		comps: [
			[
				"ship-vault",
				"Agent",
				"Declarative DV 2.0 synthesiser (pro · T=0.1).",
				"packages/opendv-agents/…/personas.py",
			],
			[
				"BKCCAuthority",
				"Algorithm",
				"Algorithm A.4 3-tier business key authority & collision shield.",
				"packages/opendv-core/…/bkcc.py",
			],
		],
	},
	{
		key: "STAGE_05_AXI_DV_STATIC_GATES",
		phase: 2,
		name: "Gates 0–4 · Static Verification",
		persona: "@axi-dv",
		tier: "deterministic",
		tag: "AUTO_REPAIRED",
		blurb:
			"Runs 99 formal checks across Gates 0–4; catches missing BKCC on Attempt 1 and auto-repairs on Attempt 2.",
		desc: "Deterministic symbolic verifier (Pydantic, SQLGlot, NetworkX, YAGNI cost guard). Emits a 44-token @toon_v1 diagnostic on Attempt 1; converges on Attempt 2.",
		note: "Attempt 1 REJECT-WITH-TRACE (DV-COL-012 on stripe_customer_code) → Attempt 2 PASS",
		ins: ["draft_contract", "profile", "drift", "triage"],
		outs: ["toon", "gate_static", "final_contract"],
		steps: [
			{
				name: "Gate 0 · Profile & drift alignment",
				reads: ["draft_contract", "profile", "drift"],
				writes: ["gate_static"],
				desc: "Verifies that all profiled columns and Wave-2 drift rules match the contract.",
			},
			{
				name: "Gate 1 · Schema, grain & BKCC collision check",
				kind: "decision",
				branch: "Attempt 1: DV-COL-012 violation → @toon_v1 trace",
				reads: ["draft_contract", "triage"],
				writes: ["toon", "gate_static"],
				desc: "Detects unscoped Tier-3 stripe_customer_code on hub_customer during Attempt 1.",
			},
			{
				name: "Gate 2 · SQLGlot AST & Algorithm A.5 hash recipe",
				reads: ["draft_contract"],
				writes: ["gate_static"],
				desc: "Parses staging expressions and length-prefixed SHA-256 hash key formulas.",
			},
			{
				name: "Gate 3 · NetworkX bipartite DAG verification",
				reads: ["draft_contract"],
				writes: ["gate_static"],
				desc: "Proves Hub outdeg=0, Link arity>=2, Satellite outdeg=1, and zero cycles.",
			},
			{
				name: "Gate 4 · YAGNI write-amplification cost guard",
				reads: ["draft_contract", "profile"],
				writes: ["gate_static"],
				desc: "Checks W_amp = 3.40 MB/day < 50.0 MB/day budget.",
			},
			{
				name: "Closed-loop self-healing retry (Attempt 2/3)",
				kind: "loop",
				branch: "patches bkcc_prefix: STRIPE_EU → 99/99 PASS",
				reads: ["draft_contract", "toon"],
				writes: ["final_contract", "gate_static"],
				desc: "Feeds the 44-token @toon_v1 trace back to @ship-vault, which scopes stripe_customer_code under STRIPE_EU.",
			},
		],
		comps: [
			[
				"axi-dv",
				"Verifier",
				"Deterministic 6-gate symbolic verifier (zero LLM tokens).",
				"packages/opendv-core/…/verifier.py",
			],
			[
				"gate1_schema_grain",
				"Verifier",
				"Pydantic + BKCC collision verifier.",
				"packages/opendv-core/…/gate1_schema_grain.py",
			],
			[
				"gate2_sqlglot_ast",
				"Verifier",
				"SQLGlot AST & Algorithm A.5 verifier.",
				"packages/opendv-core/…/gate2_sqlglot_ast.py",
			],
			[
				"gate3_networkx_dag",
				"Verifier",
				"NetworkX bipartite DAG verifier.",
				"packages/opendv-core/…/gate3_networkx_dag.py",
			],
			[
				"gate4_yagni_sizer",
				"Verifier",
				"Algorithm A.1 write-amplification cost guard.",
				"packages/opendv-core/…/gate4_yagni_sizer.py",
			],
			[
				"toon_serializer",
				"Compiler",
				"Algorithm A.8 @toon_v1 diagnostic encoder.",
				"packages/opendv-core/…/toon_serializer.py",
			],
		],
	},
	{
		key: "STAGE_06_DUCKDB_SANDBOX",
		phase: 2,
		name: "Gate 5 · DuckDB Ephemeral Sandbox",
		persona: "@duckdb-sandbox",
		tier: "deterministic",
		tag: "PASS",
		blurb:
			"Executes the compiled DDL/DML in an in-memory DuckDB instance against 4 adversarial batches.",
		desc: "Runs duplicate replay, late-arriving hubs, null keys and bi-temporal PIT joins in DuckDB to prove runtime idempotency.",
		ins: ["final_contract", "crm", "erp", "stripe", "crm2"],
		outs: ["sandbox"],
		steps: [
			{
				name: "Boot ephemeral in-memory DuckDB (:memory:)",
				reads: ["final_contract"],
				writes: [],
				desc: "Creates an isolated DuckDB connection with 4-zone schemas.",
			},
			{
				name: "Batch 1 · Baseline load & PK uniqueness",
				reads: ["crm", "erp", "stripe"],
				writes: ["sandbox"],
				desc: "Loads initial CSVs and asserts zero PK collisions.",
			},
			{
				name: "Batch 2 · Duplicate replay idempotency",
				reads: ["crm", "erp", "stripe"],
				writes: ["sandbox"],
				desc: "Re-runs the exact batch and asserts delta_inserts = 0 across all tables.",
			},
			{
				name: "Batch 3 · Out-of-order late-arriving Hub synthesis",
				reads: ["stripe"],
				writes: ["sandbox"],
				desc: "Loads child Link rows before parent Hub rows; verifies zero orphan FKs.",
			},
			{
				name: "Batch 4 · NULL key & Zero-Key routing",
				reads: ["erp", "crm2"],
				writes: ["sandbox"],
				desc: "Verifies -1:^^ null tokens and ghost record 0x00..00.",
			},
		],
		comps: [
			[
				"duckdb-sandbox",
				"Sandbox",
				"Ephemeral in-memory DuckDB execution engine.",
				"packages/opendv-duckdb-sandbox/…/sandbox_runner.py",
			],
			[
				"adversarial_generator",
				"Verifier",
				"Synthesises duplicate, late-arriving and null-key batches.",
				"packages/opendv-duckdb-sandbox/…/adversarial_generator.py",
			],
		],
	},
	{
		key: "STAGE_07_DETERMINISTIC_COMPILER",
		phase: 3,
		name: "Compiler, PIT & CAS Seal",
		persona: "@deterministic-compiler",
		tier: "deterministic",
		tag: "PASS",
		blurb:
			"Lowers the contract to CanonicalIR, compiles 4-zone DuckDB SQL, runs post-flight PII checks and seals to CAS.",
		desc: "Produces final_canonical_ir.json, final_four_zone.sql, pipeline_report.json and vault-ledger.jsonl, and seals every artifact into the content-addressable store.",
		ins: ["final_contract", "sandbox", "runopts"],
		outs: ["final_ir", "final_sql", "report", "ledger", "cas"],
		steps: [
			{
				name: "Lower DVModelContract to CanonicalIR",
				reads: ["final_contract"],
				writes: ["final_ir"],
				desc: "Deterministic lowering pass into the platform-neutral intermediate representation.",
			},
			{
				name: "Compile 4-zone DuckDB SQL bundle",
				reads: ["final_ir"],
				writes: ["final_sql"],
				desc: "Generates Bronze, Staging, Raw Vault and Business Vault DDL/DML.",
			},
			{
				name: "Run PIT audit (D.10 / D.10b)",
				reads: [],
				writes: [],
				desc: "Bi-temporal point-in-time queries at the assertion cut-off.",
			},
			{
				name: "Post-flight validation",
				kind: "decision",
				branch: "schema or PII leak → fail",
				reads: ["runopts"],
				writes: ["report"],
				desc: "Every artifact against its JSON Schema; zero unredacted RESTRICTED_PII, MNPI or raw digests for the role.",
			},
			{
				name: "Seal into CAS",
				reads: [],
				writes: ["final_contract", "cas"],
				desc: "Stores each artifact under its SHA-256 digest.",
			},
		],
		comps: [
			[
				"ir_lowerer",
				"Compiler",
				"Contract → CanonicalIR.",
				"packages/opendv-duckdb-sandbox/…/ir_lowerer.py",
			],
			[
				"compiler_4zone",
				"Compiler",
				"CanonicalIR → 4-zone DuckDB SQL.",
				"packages/opendv-duckdb-sandbox/…/compiler_4zone.py",
			],
			[
				"pit_query_engine",
				"Query engine",
				"Bi-temporal PIT queries.",
				"packages/opendv-duckdb-sandbox/…/pit_query_engine.py",
			],
			[
				"cas_store",
				"Storage",
				"Content-addressable store.",
				"packages/opendv-telemetry/…/cas_store.py",
			],
			[
				"rbac_redactor",
				"Governance",
				"5-tier RBAC redaction and PII checks.",
				"packages/opendv-telemetry/…/rbac_redactor.py",
			],
		],
	},
];
