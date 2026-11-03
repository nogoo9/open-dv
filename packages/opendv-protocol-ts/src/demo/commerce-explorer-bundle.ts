/**
 * Canonical 8-Stage Pipeline Explorer Metadata, Dynamic Bundle Builder, and
 * Multi-Wave Explorer Bundles (`GLOBAL_COMMERCE_EXPLORER_BUNDLE` & `PE_EXPLORER_BUNDLE`)
 * for `@opendv/protocol-ts` (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { ProjectDTO, ProjectRunDTO } from "../types/api-dtos";
import type { RbacRole } from "../types/common";
import type { VaultObservabilityEventDTO } from "../types/observability-event";
import type {
	Artifact,
	ExplorerBundleDTO,
	OntologyConceptSpec,
	PhaseSpec,
	Stage,
	StatusTag,
	WaveFilterId,
} from "../types/workbench-explorer";
import {
	PE_DEMO_EVENTS,
	PE_DEMO_PROJECT,
	PE_DEMO_RUN,
} from "./pe-demo-bundle";

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

export const COMMERCE_MSA_QUOTE_U1 =
	"Northwind Industrial Supply Corp. (US Federal Tax ID: TAX-US-900101)";
export const COMMERCE_MSA_QUOTE_U3 =
	"Rheinland Precision Holdings Europe";

export const COMMERCE_MSA_SOURCE_TEXT =
	"MASTER SERVICES AGREEMENT — ENTERPRISE ADDENDUM (2026-W3)\n\n" +
	"1. COUNTERPARTY IDENTIFICATION\n" +
	`This Master Services Agreement Addendum is entered into by and between ${COMMERCE_MSA_QUOTE_U1} ("Customer") and Global Commerce Vault Operations LLC, effective 2026-01-12.\n\n` +
	"2. SETTLEMENT & INVOICING GUARANTOR\n" +
	`Cross-border European settlement obligations under Invoice INV-EU-8801 shall be guaranteed by ${COMMERCE_MSA_QUOTE_U3}, subject to parent-entity verification by the Data Governance Steward.\n\n` +
	"3. DATA PROTECTION & RESTRICTED PII\n" +
	"Customer contact electronic mail addresses and phone numbers constitute RESTRICTED_PII and must be isolated in a dedicated GDPR satellite.";

export const COMMERCE_ONTOLOGY_CONCEPTS: OntologyConceptSpec[] = [
	{
		name: "CBC_CUSTOMER",
		arch: "WHO_PARTY",
		def: "B2B or B2C legal counterparty under a verified tax identifier. PII split required.",
		keys: "T1 customer_tax_id · T3 stripe_customer_code",
	},
	{
		name: "CBC_ORDER",
		arch: "WHAT_EVENT_ANCHOR",
		def: "Sales order committing line items and pricing for a customer.",
		keys: "T1 order_id",
	},
	{
		name: "CBC_INVOICE",
		arch: "WHAT_FINANCIAL_INSTRUMENT",
		def: "Settlement invoice issued by a payment processor against an order.",
		keys: "T3 invoice_number · STRIPE_EU",
	},
	{
		name: "CBC_PROMOTION",
		arch: "HOW_CONTRACTUAL_RULE",
		def: "Optional promotional campaign applied to qualifying orders.",
		keys: "T1 promo_campaign_code",
	},
	{
		name: "CBC_CONTRACT_DOCUMENT",
		arch: "WHAT_UNSTRUCTURED_ARTIFACT",
		def: "Signed MSA or addendum governing customer terms.",
		keys: "T1 doc_id",
	},
];

/**
 * Static canonical stage step & component definitions for the 8-stage Open-DV pipeline
 * (`packages/opendv-agents/` and `packages/opendv-core/`).
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

export const GLOBAL_COMMERCE_ARTIFACTS: Record<string, Artifact> = {
	harness: {
		id: "harness",
		name: "harness_config.yaml",
		short: "agy · pro + flash · 3 retries",
		group: "Setting",
		type: "AgentHarnessConfig",
		fmt: "YAML",
		viz: "kv",
		desc: "Selects the LLM provider, maps each stage persona to a model tier and temperature, and caps self-healing retries at K_retry = 3.",
		waves: ["W0", "W1", "W2", "W3"],
		kv: [
			["default_provider", "antigravity (agy)"],
			["@vault-first-mate", "pro · T=0.1 · STAGE_00"],
			["@scout-source", "flash · T=0.0 · STAGE_01"],
			["@scout-unstructured", "flash · T=0.0 · STAGE_02"],
			["@ship-vault", "pro · T=0.1 · STAGE_03, STAGE_04"],
			["@axi-dv", "deterministic (no LLM) · STAGE_05"],
			["@duckdb-sandbox", "deterministic (no LLM) · STAGE_06"],
			["@deterministic-compiler", "deterministic (no LLM) · STAGE_07"],
			["max_self_healing_retries", "3 (Algorithm A.8)"],
		],
		raw:
			"version: '1.0'\ndefault_provider: antigravity\nmax_self_healing_retries: 3\n" +
			"personas:\n" +
			"  vault-first-mate:       { model_tier: pro,   temperature: 0.1 }\n" +
			"  scout-source:           { model_tier: flash, temperature: 0.0 }\n" +
			"  scout-unstructured:     { model_tier: flash, temperature: 0.0 }\n" +
			"  ship-vault:             { model_tier: pro,   temperature: 0.1 }\n" +
			"  axi-dv:                 { mode: deterministic }\n" +
			"  duckdb-sandbox:         { mode: deterministic }\n" +
			"  deterministic-compiler: { mode: deterministic }",
	},
	runopts: {
		id: "runopts",
		name: "run options",
		short: "auto_approve_top · INTERNAL_MODELER",
		group: "Setting",
		type: "PipelineExecutionOptions",
		fmt: "Flags",
		viz: "kv",
		desc: "Runtime governance options: how Tier-U3 steward holds are resolved, which RBAC role shapes telemetry redaction, and the PIT assertion timestamp.",
		waves: ["W0", "W1", "W2", "W3"],
		kv: [
			["steward_hold_policy", "auto_approve_top_candidate"],
			["rbac_role", "INTERNAL_MODELER"],
			["hash_algorithm", "SHA-256 length-prefixed (Algorithm A.5)"],
			["target_dialect", "DuckDB 4-zone (Bronze / Staging / Raw / Business)"],
			["pit_as_of_utc", "2026-01-16T00:00:00Z"],
			["cas_sealing", "enabled (SHA-256 content-addressable)"],
		],
		raw:
			"--steward-hold-policy auto_approve_top_candidate\n" +
			"--rbac-role INTERNAL_MODELER\n" +
			"--target-dialect duckdb\n" +
			"--pit-as-of 2026-01-16T00:00:00Z\n" +
			"--seal-cas",
	},
	spec: {
		id: "spec",
		name: "pipeline_input.yaml",
		short: "4 waves · W0 cold → W3 MSA",
		group: "Input",
		type: "AgenticPipelineInputSpec",
		fmt: "YAML",
		viz: "kv",
		desc: "Master pipeline manifest declaring the project ID, the Graph A ontology path, and the 4 onboarding waves (Cold Start → Incremental Source → Schema Drift → Unstructured MSA).",
		waves: ["W0", "W1", "W2", "W3"],
		kv: [
			["project_id", "global_commerce_vault"],
			["ontology_path", "ontology/graph_a_ontology.yaml"],
			["initial_contract", "none (Wave 0 cold start)"],
			["Wave 0 · cold_start_crm_erp", "crm_customers.csv, erp_orders.csv"],
			["Wave 1 · incremental_stripe", "stripe_eu_invoices.csv (N=2 → N=3)"],
			[
				"Wave 2 · crm_schema_drift",
				"crm_customers_v2_drift.csv (S1(t) → S1(t+1))",
			],
			[
				"Wave 3 · unstructured_msa",
				"enterprise_msa_addendum_2026.txt (Algorithm A.9b)",
			],
		],
		raw:
			"project_id: global_commerce_vault\n" +
			"display_name: Global Commerce & Billing Vault\n" +
			"ontology_path: ontology/graph_a_ontology.yaml\n" +
			"waves:\n" +
			"  - wave_id: wave_0_cold_start\n" +
			"    relational_sources: [sources/wave0_crm_customers.csv, sources/wave0_erp_orders.csv]\n" +
			"  - wave_id: wave_1_incremental_stripe\n" +
			"    relational_sources: [sources/wave1_stripe_eu_invoices.csv]\n" +
			"  - wave_id: wave_2_crm_schema_drift\n" +
			"    relational_sources: [sources/wave2_crm_customers_v2_drift.csv]\n" +
			"  - wave_id: wave_3_unstructured_msa\n" +
			"    unstructured_sources: [sources/wave3_enterprise_msa_addendum_2026.txt]",
	},
	ontology: {
		id: "ontology",
		name: "graph_a_ontology.yaml",
		short: "5 concepts · Customer, Order, Invoice…",
		group: "Input",
		type: "GraphAOntology",
		fmt: "YAML",
		viz: "ontology",
		desc: "Enterprise domain ontology (Graph A) defining the 5 canonical business concepts, their key authority tiers, and the business relationships that anchor Hub and Link synthesis.",
		waves: ["W0", "W1", "W2", "W3"],
		ontology: {
			concepts: COMMERCE_ONTOLOGY_CONCEPTS,
			nodes: [
				{
					id: "CBC_CUSTOMER",
					label: "CBC_CUSTOMER",
					sub: "WHO_PARTY · Tier-1 tax_id",
					x: 16,
					y: 24,
					w: 168,
				},
				{
					id: "CBC_ORDER",
					label: "CBC_ORDER",
					sub: "WHAT_EVENT · Tier-1 order_id",
					x: 244,
					y: 24,
					w: 168,
				},
				{
					id: "CBC_INVOICE",
					label: "CBC_INVOICE",
					sub: "FINANCIAL · Tier-3 invoice_no",
					x: 468,
					y: 24,
					w: 156,
				},
				{
					id: "CBC_CONTRACT_DOCUMENT",
					label: "CBC_CONTRACT_DOCUMENT",
					sub: "UNSTRUCTURED · Tier-1 doc_id",
					x: 16,
					y: 160,
					w: 186,
				},
				{
					id: "CBC_PROMOTION",
					label: "CBC_PROMOTION",
					sub: "RULE · Tier-1 promo_code (opt)",
					x: 244,
					y: 160,
					w: 176,
				},
			],
			edges: [
				{
					path: "M184 52 L244 52",
					label: "places",
					lx: 214,
					ly: 44,
				},
				{
					path: "M412 52 L468 52",
					label: "settled_by",
					lx: 440,
					ly: 44,
				},
				{
					path: "M328 80 L328 160",
					label: "applies_promo (0..1)",
					lx: 334,
					ly: 124,
				},
				{
					path: "M100 80 L100 160",
					label: "governed_by",
					lx: 106,
					ly: 124,
				},
			],
		},
		raw:
			"ontology_version: '2.0'\ndomain: global_commerce\nconcepts:\n" +
			"  - id: CBC_CUSTOMER\n    archetype: WHO_PARTY\n    tier1_keys: [customer_tax_id]\n    tier3_keys: [stripe_customer_code]\n    pii_split_required: true\n" +
			"  - id: CBC_ORDER\n    archetype: WHAT_EVENT_ANCHOR\n    tier1_keys: [order_id]\n" +
			"  - id: CBC_INVOICE\n    archetype: WHAT_FINANCIAL_INSTRUMENT\n    tier3_keys: [invoice_number]\n" +
			"  - id: CBC_PROMOTION\n    archetype: HOW_CONTRACTUAL_RULE\n    tier1_keys: [promo_campaign_code]\n" +
			"  - id: CBC_CONTRACT_DOCUMENT\n    archetype: WHAT_UNSTRUCTURED_ARTIFACT\n    tier1_keys: [doc_id]",
	},
	crm: {
		id: "crm",
		name: "wave0_crm_customers.csv",
		short: "Wave 0 · 4 rows × 7 cols · has PII",
		group: "Input",
		type: "RelationalSource (CRM_GLOBAL)",
		fmt: "CSV",
		viz: "table",
		desc: "Wave 0 CRM customer master table. Contains monotonic surrogate row_seq_id (quarantined in Stage 01), verified tax IDs, and RESTRICTED_PII contact columns.",
		waves: ["W0"],
		cols: [
			"row_seq_id",
			"customer_tax_id",
			"legal_name",
			"country_iso",
			"credit_limit",
			"contact_email",
			"fax_number",
		],
		rows: [
			[
				"1",
				"TAX-US-900101",
				"Northwind Industrial Supply Corp.",
				"US",
				"250000.00",
				"‹PII›",
				"+1-206-555-0110",
			],
			[
				"2",
				"TAX-DE-800202",
				"Rheinland Precision GmbH",
				"DE",
				"180000.00",
				"‹PII›",
				"+49-211-555-0199",
			],
			[
				"3",
				"TAX-SG-700303",
				"Merlion Maritime Logistics Pte Ltd",
				"SG",
				"320000.00",
				"‹PII›",
				"NULL",
			],
			[
				"4",
				"TAX-GB-600404",
				"Albion Aerospace Components Ltd",
				"GB",
				"150000.00",
				"‹PII›",
				"+44-20-7946-0921",
			],
		],
		foot: "row_seq_id is quarantined as DROPPED_SURROGATE (U=1.00, Δ=1). contact_email is isolated into sat_customer_pii.",
		raw:
			"row_seq_id,customer_tax_id,legal_name,country_iso,credit_limit,contact_email,fax_number\n" +
			"1,TAX-US-900101,Northwind Industrial Supply Corp.,US,250000.00,[REDACTED_PII],+1-206-555-0110\n" +
			"2,TAX-DE-800202,Rheinland Precision GmbH,DE,180000.00,[REDACTED_PII],+49-211-555-0199\n" +
			"3,TAX-SG-700303,Merlion Maritime Logistics Pte Ltd,SG,320000.00,[REDACTED_PII],\n" +
			"4,TAX-GB-600404,Albion Aerospace Components Ltd,GB,150000.00,[REDACTED_PII],+44-20-7946-0921",
	},
	erp: {
		id: "erp",
		name: "wave0_erp_orders.csv",
		short: "Wave 0 · 6 rows × 6 cols · sparse promo FK",
		group: "Input",
		type: "RelationalSource (ERP_SAP)",
		fmt: "CSV",
		viz: "table",
		desc: "Wave 0 ERP sales order header table. Contains sparse foreign key promo_campaign_code (66.7% non-null), which triggers Algorithm A.4 Link splitting.",
		waves: ["W0"],
		cols: [
			"order_id",
			"customer_tax_id",
			"order_date",
			"order_status",
			"net_amount_usd",
			"promo_campaign_code",
		],
		rows: [
			[
				"ORD-2026-1001",
				"TAX-US-900101",
				"2026-01-03",
				"CONFIRMED",
				"48500.00",
				"PROMO-WINTER-26",
			],
			[
				"ORD-2026-1002",
				"TAX-DE-800202",
				"2026-01-04",
				"SHIPPED",
				"31200.00",
				"NULL",
			],
			[
				"ORD-2026-1003",
				"TAX-SG-700303",
				"2026-01-06",
				"CONFIRMED",
				"92400.00",
				"PROMO-APAC-VIP",
			],
			[
				"ORD-2026-1004",
				"TAX-US-900101",
				"2026-01-08",
				"INVOICED",
				"15750.00",
				"NULL",
			],
			[
				"ORD-2026-1005",
				"TAX-GB-600404",
				"2026-01-10",
				"CONFIRMED",
				"27800.00",
				"PROMO-WINTER-26",
			],
			[
				"ORD-2026-1006",
				"TAX-DE-800202",
				"2026-01-11",
				"INVOICED",
				"64100.00",
				"PROMO-EU-LAUNCH",
			],
		],
		foot: "promo_campaign_code has U_nn = 4/6 = 66.7% < 95% threshold → routed to isolating lnk_order_promotion.",
		raw:
			"order_id,customer_tax_id,order_date,order_status,net_amount_usd,promo_campaign_code\n" +
			"ORD-2026-1001,TAX-US-900101,2026-01-03,CONFIRMED,48500.00,PROMO-WINTER-26\n" +
			"ORD-2026-1002,TAX-DE-800202,2026-01-04,SHIPPED,31200.00,\n" +
			"ORD-2026-1003,TAX-SG-700303,2026-01-06,CONFIRMED,92400.00,PROMO-APAC-VIP\n" +
			"ORD-2026-1004,TAX-US-900101,2026-01-08,INVOICED,15750.00,\n" +
			"ORD-2026-1005,TAX-GB-600404,2026-01-10,CONFIRMED,27800.00,PROMO-WINTER-26\n" +
			"ORD-2026-1006,TAX-DE-800202,2026-01-11,INVOICED,64100.00,PROMO-EU-LAUNCH",
	},
	stripe: {
		id: "stripe",
		name: "wave1_stripe_eu_invoices.csv",
		short: "Wave 1 · 4 rows × 5 cols · Tier-3 key collision",
		group: "Input",
		type: "RelationalSource (STRIPE_EU)",
		fmt: "CSV",
		viz: "table",
		desc: "Wave 1 incremental payment processor feed. Uses local Tier-3 stripe_customer_code ('CUST-101') that would collide without a STRIPE_EU BKCC prefix.",
		waves: ["W1"],
		cols: [
			"invoice_number",
			"stripe_customer_code",
			"order_id",
			"invoice_amount_eur",
			"settlement_status",
		],
		rows: [
			["INV-EU-8801", "CUST-101", "ORD-2026-1002", "28900.00", "SETTLED"],
			["INV-EU-8802", "CUST-102", "ORD-2026-1005", "25400.00", "SETTLED"],
			["INV-EU-8803", "CUST-101", "ORD-2026-1006", "59300.00", "PENDING"],
			["INV-EU-8804", "CUST-103", "ORD-2026-1001", "44900.00", "SETTLED"],
		],
		foot: "stripe_customer_code is a local Tier-3 key; Gate 1 enforces bkcc_prefix: STRIPE_EU and a Same-As Link.",
		raw:
			"invoice_number,stripe_customer_code,order_id,invoice_amount_eur,settlement_status\n" +
			"INV-EU-8801,CUST-101,ORD-2026-1002,28900.00,SETTLED\n" +
			"INV-EU-8802,CUST-102,ORD-2026-1005,25400.00,SETTLED\n" +
			"INV-EU-8803,CUST-101,ORD-2026-1006,59300.00,PENDING\n" +
			"INV-EU-8804,CUST-103,ORD-2026-1001,44900.00,SETTLED",
	},
	crm2: {
		id: "crm2",
		name: "wave2_crm_customers_v2_drift.csv",
		short: "Wave 2 · 4 rows × 7 cols · +col, -col, rename",
		group: "Input",
		type: "RelationalSource (CRM_GLOBAL v2)",
		fmt: "CSV",
		viz: "table",
		desc: "Wave 2 evolved CRM feed: adds loyalty_tier_2026, drops fax_number, and renames credit_limit → credit_limit_usd.",
		waves: ["W2"],
		cols: [
			"row_seq_id",
			"customer_tax_id",
			"legal_name",
			"country_iso",
			"credit_limit_usd",
			"contact_email",
			"loyalty_tier_2026",
		],
		rows: [
			[
				"1",
				"TAX-US-900101",
				"Northwind Industrial Supply Corp.",
				"US",
				"250000.00",
				"‹PII›",
				"PLATINUM",
			],
			[
				"2",
				"TAX-DE-800202",
				"Rheinland Precision GmbH",
				"DE",
				"180000.00",
				"‹PII›",
				"GOLD",
			],
			[
				"3",
				"TAX-SG-700303",
				"Merlion Maritime Logistics Pte Ltd",
				"SG",
				"350000.00",
				"‹PII›",
				"PLATINUM",
			],
			[
				"4",
				"TAX-GB-600404",
				"Albion Aerospace Components Ltd",
				"GB",
				"150000.00",
				"‹PII›",
				"SILVER",
			],
		],
		foot: "Algorithm A.9a routes loyalty_tier_2026 to companion sat_customer_loyalty_2026 and aliases credit_limit_usd in Staging.",
		raw:
			"row_seq_id,customer_tax_id,legal_name,country_iso,credit_limit_usd,contact_email,loyalty_tier_2026\n" +
			"1,TAX-US-900101,Northwind Industrial Supply Corp.,US,250000.00,[REDACTED_PII],PLATINUM\n" +
			"2,TAX-DE-800202,Rheinland Precision GmbH,DE,180000.00,[REDACTED_PII],GOLD\n" +
			"3,TAX-SG-700303,Merlion Maritime Logistics Pte Ltd,SG,350000.00,[REDACTED_PII],PLATINUM\n" +
			"4,TAX-GB-600404,Albion Aerospace Components Ltd,GB,150000.00,[REDACTED_PII],SILVER",
	},
	msa: {
		id: "msa",
		name: "wave3_enterprise_msa_addendum_2026.txt",
		short: "Wave 3 · MSA text · 2 entity spans",
		group: "Input",
		type: "UnstructuredDocument",
		fmt: "TXT",
		viz: "doc",
		desc: "Wave 3 unstructured legal addendum. Grounded by Algorithm A.9b into 1 Tier-U1 exact tax-ID match and 1 Tier-U3 ambiguous guarantor mention.",
		waves: ["W3"],
		doc: {
			documentName: "enterprise_msa_addendum_2026.txt",
			sourceText: COMMERCE_MSA_SOURCE_TEXT,
			extractions: [
				{
					id: "ext_msa_counterparty_u1",
					quote: COMMERCE_MSA_QUOTE_U1,
					start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1),
					end:
						COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1) +
						COMMERCE_MSA_QUOTE_U1.length,
					tier: "Tier-U1",
					conf: "0.98",
					decision:
						"Exact tax-ID match → hub_customer TAX-US-900101. Linked automatically.",
					resolved: true,
				},
				{
					id: "ext_msa_guarantor_u3",
					quote: COMMERCE_MSA_QUOTE_U3,
					start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3),
					end:
						COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3) +
						COMMERCE_MSA_QUOTE_U3.length,
					tier: "Tier-U3",
					conf: "0.68",
					decision:
						"Ambiguous: Rheinland Precision GmbH (TAX-DE-800202) or an unaffiliated affiliate. STEWARD_HOLD → resolved by auto_approve_top_candidate.",
					holdId: "hold-tier-u3-rheinland-precision",
					eventId: "evt-commerce-004",
					resolved: false,
					candidates: [
						{
							id: "TAX-DE-800202",
							label: "Rheinland Precision GmbH (TAX-DE-800202)",
							score: 0.68,
						},
						{
							id: "NEW_AFFILIATE_ENTITY",
							label: "Rheinland Precision Holdings Europe (New Entity)",
							score: 0.32,
						},
					],
				},
			],
		},
		raw: COMMERCE_MSA_SOURCE_TEXT,
	},
	grounding: {
		id: "grounding",
		name: "bipartite_grounding_map.json",
		short: "5 tables → 5 concepts (100%)",
		group: "Stage output",
		type: "BipartiteGroundingMap",
		fmt: "JSON",
		viz: "table",
		desc: "Stage 00 output mapping every relational table and unstructured document onto its Graph A business concept.",
		waves: ["W0", "W1", "W2", "W3"],
		cols: ["source_artifact", "wave", "graph_a_concept", "target_hub", "key_tier"],
		rows: [
			[
				"wave0_crm_customers.csv",
				"W0",
				"CBC_CUSTOMER",
				"hub_customer",
				"Tier-1 (customer_tax_id)",
			],
			[
				"wave0_erp_orders.csv",
				"W0",
				"CBC_ORDER",
				"hub_order",
				"Tier-1 (order_id)",
			],
			[
				"wave1_stripe_eu_invoices.csv",
				"W1",
				"CBC_INVOICE",
				"hub_invoice",
				"Tier-3 (STRIPE_EU:invoice_number)",
			],
			[
				"wave2_crm_customers_v2_drift.csv",
				"W2",
				"CBC_CUSTOMER",
				"hub_customer",
				"Tier-1 (customer_tax_id)",
			],
			[
				"wave3_enterprise_msa_addendum_2026.txt",
				"W3",
				"CBC_CONTRACT_DOCUMENT",
				"hub_contract_document",
				"Tier-1 (doc_id)",
			],
		],
		foot: "Ontology coverage: 5/5 concepts active (100%). Zero unmapped sources.",
		raw: '{\n  "ontology_coverage_pct": 100.0,\n  "unmapped_tables": 0,\n  "mappings": [\n    { "source": "wave0_crm_customers.csv", "concept": "CBC_CUSTOMER", "target_hub": "hub_customer" },\n    { "source": "wave0_erp_orders.csv", "concept": "CBC_ORDER", "target_hub": "hub_order" },\n    { "source": "wave1_stripe_eu_invoices.csv", "concept": "CBC_INVOICE", "target_hub": "hub_invoice" },\n    { "source": "wave3_enterprise_msa_addendum_2026.txt", "concept": "CBC_CONTRACT_DOCUMENT", "target_hub": "hub_contract_document" }\n  ]\n}',
	},
	profile: {
		id: "profile",
		name: "graph_b_source_profile.json",
		short: "24 cols profiled · 1 surrogate · 1 sparse FK",
		group: "Stage output",
		type: "GraphBProfile",
		fmt: "JSON",
		viz: "table",
		desc: "Stage 01 information-theoretic profile of the relational columns: uniqueness U(C), non-null ratio U_nn(C), normalized entropy H_norm(C), and inferred role.",
		waves: ["W0", "W1"],
		cols: ["table.column", "U(C)", "U_nn(C)", "H_norm", "classification"],
		rows: [
			[
				"crm.row_seq_id",
				"1.000",
				"1.000",
				"1.000",
				"DROPPED_SURROGATE (Δ=1 monotonic)",
			],
			[
				"crm.customer_tax_id",
				"1.000",
				"1.000",
				"1.000",
				"BUSINESS_KEY (Tier-1)",
			],
			[
				"crm.contact_email",
				"1.000",
				"1.000",
				"1.000",
				"DESCRIPTIVE_PAYLOAD · RESTRICTED_PII",
			],
			["erp.order_id", "1.000", "1.000", "1.000", "BUSINESS_KEY (Tier-1)"],
			[
				"erp.promo_campaign_code",
				"0.750",
				"0.667",
				"0.792",
				"OPTIONAL_FK_REF (U_nn < 0.95 → split Link)",
			],
			[
				"stripe.invoice_number",
				"1.000",
				"1.000",
				"1.000",
				"BUSINESS_KEY (Tier-3 · STRIPE_EU)",
			],
			[
				"stripe.stripe_customer_code",
				"0.750",
				"1.000",
				"0.792",
				"FOREIGN_KEY_REF (Tier-3 · needs BKCC)",
			],
		],
		foot: "Showing 7 key/notable columns of 24 total profiled columns.",
		raw: '{\n  "columns_profiled": 24,\n  "surrogates_quarantined": ["crm.row_seq_id"],\n  "sparse_foreign_keys": [{ "column": "erp.promo_campaign_code", "non_null_ratio": 0.6667 }]\n}',
	},
	drift: {
		id: "drift",
		name: "schema_drift_report_w2.json",
		short: "3 drift events · 0 broken hash_diffs",
		group: "Stage output",
		type: "SchemaDriftReport",
		fmt: "JSON",
		viz: "table",
		desc: "Algorithm A.9a classification of Wave 2 schema changes between wave0_crm_customers.csv and wave2_crm_customers_v2_drift.csv.",
		waves: ["W2"],
		cols: ["column_transition", "category", "metric", "non_destructive_codemod"],
		rows: [
			[
				"+ loyalty_tier_2026",
				"ADDITIVE",
				"new column",
				"Route to companion sat_customer_loyalty_2026",
			],
			[
				"- fax_number",
				"SOFT_DEPRECATED",
				"missing in v2",
				"Inject CAST(NULL AS VARCHAR) AS fax_number in Staging",
			],
			[
				"credit_limit → credit_limit_usd",
				"RENAMED",
				"J_val = 0.996 >= 0.90",
				"Alias credit_limit_usd AS credit_limit in Staging",
			],
		],
		foot: "Historical sat_customer_core.hash_diff digests broken: 0.",
		raw: '{\n  "algorithm": "A.9a",\n  "historical_hash_diffs_broken": 0,\n  "mutations": [\n    { "column": "loyalty_tier_2026", "category": "ADDITIVE", "target": "sat_customer_loyalty_2026" },\n    { "column": "fax_number", "category": "SOFT_DEPRECATED", "staging_shim": "CAST(NULL AS VARCHAR)" },\n    { "from": "credit_limit", "to": "credit_limit_usd", "category": "RENAMED", "jaccard_value": 0.996 }\n  ]\n}',
	},
	extract: {
		id: "extract",
		name: "unstructured_extractions_w3.json",
		short: "2 spans · 1 Tier-U1 · 1 Tier-U3 hold",
		group: "Stage output",
		type: "UnstructuredExtractionBundle",
		fmt: "JSON",
		viz: "doc",
		desc: "Stage 02 output: character-offset verified entity spans extracted from the Wave 3 MSA addendum by Algorithm A.9b.",
		waves: ["W3"],
		doc: {
			documentName: "enterprise_msa_addendum_2026.txt",
			sourceText: COMMERCE_MSA_SOURCE_TEXT,
			extractions: [
				{
					id: "ext_msa_counterparty_u1",
					quote: COMMERCE_MSA_QUOTE_U1,
					start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1),
					end:
						COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1) +
						COMMERCE_MSA_QUOTE_U1.length,
					tier: "Tier-U1",
					conf: "0.98",
					decision:
						"Exact tax-ID match → hub_customer TAX-US-900101. Linked automatically.",
					resolved: true,
				},
				{
					id: "ext_msa_guarantor_u3",
					quote: COMMERCE_MSA_QUOTE_U3,
					start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3),
					end:
						COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3) +
						COMMERCE_MSA_QUOTE_U3.length,
					tier: "Tier-U3",
					conf: "0.68",
					decision:
						"Ambiguous: Rheinland Precision GmbH (TAX-DE-800202) or an unaffiliated affiliate. STEWARD_HOLD → resolved by auto_approve_top_candidate.",
					holdId: "hold-tier-u3-rheinland-precision",
					eventId: "evt-commerce-004",
					resolved: false,
					candidates: [
						{
							id: "TAX-DE-800202",
							label: "Rheinland Precision GmbH (TAX-DE-800202)",
							score: 0.68,
						},
						{
							id: "NEW_AFFILIATE_ENTITY",
							label: "Rheinland Precision Holdings Europe (New Entity)",
							score: 0.32,
						},
					],
				},
			],
		},
		raw: JSON.stringify(
			{
				algorithm: "A.9b",
				document: "enterprise_msa_addendum_2026.txt",
				extractions: [
					{
						extraction_id: "ext_msa_counterparty_u1",
						verbatim_quote: COMMERCE_MSA_QUOTE_U1,
						char_start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1),
						char_end:
							COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U1) +
							COMMERCE_MSA_QUOTE_U1.length,
						tier: "Tier-U1",
						confidence: 0.98,
					},
					{
						extraction_id: "ext_msa_guarantor_u3",
						verbatim_quote: COMMERCE_MSA_QUOTE_U3,
						char_start: COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3),
						char_end:
							COMMERCE_MSA_SOURCE_TEXT.indexOf(COMMERCE_MSA_QUOTE_U3) +
							COMMERCE_MSA_QUOTE_U3.length,
						tier: "Tier-U3",
						confidence: 0.68,
					},
				],
			},
			null,
			2,
		),
	},
	triage: {
		id: "triage",
		name: "column_triage_ledger.json",
		short: "24 / 24 columns classified (100%)",
		group: "Stage output",
		type: "ColumnTriageLedger",
		fmt: "JSON",
		viz: "table",
		desc: "Stage 03 Pass 1 exhaustive column accounting ledger. Every Bronze and extraction column is mapped to exactly one Data Vault role or explicitly tombstoned.",
		waves: ["W0", "W1", "W2", "W3"],
		cols: ["role_classification", "count", "example_columns", "target_construct"],
		rows: [
			[
				"BUSINESS_KEY",
				"4",
				"customer_tax_id, order_id, invoice_number, doc_id",
				"hub_customer, hub_order, hub_invoice, hub_contract_document",
			],
			[
				"FOREIGN_KEY_REF",
				"4",
				"erp.customer_tax_id, stripe.order_id, stripe.stripe_customer_code",
				"lnk_customer_order, lnk_order_invoice, sal_customer_alias",
			],
			[
				"OPTIONAL_FK_REF",
				"1",
				"erp.promo_campaign_code (66.7% non-null)",
				"hub_promotion + lnk_order_promotion",
			],
			[
				"DESCRIPTIVE_PAYLOAD",
				"12",
				"legal_name, credit_limit, net_amount_usd, invoice_amount_eur",
				"sat_customer_core, sat_order_details, sat_invoice_settlement",
			],
			[
				"DESCRIPTIVE_PAYLOAD (PII)",
				"1",
				"crm.contact_email",
				"sat_customer_pii (RESTRICTED_PII)",
			],
			[
				"ADDITIVE_DRIFT_PAYLOAD",
				"1",
				"crm2.loyalty_tier_2026",
				"sat_customer_loyalty_2026",
			],
			[
				"DROPPED_SURROGATE",
				"1",
				"crm.row_seq_id",
				"— (explicitly tombstoned)",
			],
		],
		foot: "Bijection check: 24/24 source columns accounted for (100.0%).",
		raw: '{\n  "total_source_columns": 24,\n  "triaged_columns": 24,\n  "coverage_pct": 100.0,\n  "dropped_surrogates": ["crm.row_seq_id"]\n}',
	},
	draft_contract: {
		id: "draft_contract",
		name: "draft_contract_attempt1.yaml",
		short: "Attempt 1 · missing STRIPE_EU BKCC",
		group: "Stage output",
		type: "DVModelContract (Attempt 1)",
		fmt: "YAML",
		viz: "dv",
		desc: "Stage 04 initial contract synthesis (Attempt 1). Contains an unscoped Tier-3 stripe_customer_code on hub_customer that Gate 1 catches and repairs.",
		waves: ["W0", "W1", "W2", "W3"],
		raw:
			"# Attempt 1 (pre-repair): missing bkcc_prefix on stripe_customer_code\n" +
			"contract_version: '6.0'\nhubs:\n" +
			"  - name: hub_customer\n" +
			"    sources:\n" +
			"      - table: wave1_stripe_eu_invoices\n" +
			"        key_column: stripe_customer_code\n" +
			"        require_src_qualifier: false   # <-- flagged by Gate 1 (DV-COL-012)\n" +
			"        bkcc_prefix: null",
	},
	toon: {
		id: "toon",
		name: "attempt1_diagnostic.toon",
		short: "44 tokens · DV-COL-012 → auto-repair",
		group: "Stage output",
		type: "ToonDiagnosticTrace",
		fmt: "@toon_v1",
		viz: "kv",
		desc: "Compact 44-token @toon_v1 diagnostic emitted by Gate 1 on Attempt 1 and fed back to @ship-vault to repair the missing STRIPE_EU BKCC prefix.",
		waves: ["W1"],
		kv: [
			["format", "@toon_v1 (Algorithm A.8)"],
			["attempt", "1 of 3"],
			["verdict", "REJECT-WITH-TRACE → AUTO_REPAIRED on Attempt 2"],
			["gate / rule", "G1 · DV-COL-012 (Tier-3 BKCC authority)"],
			["target", "hub_customer <- wave1_stripe_eu_invoices.stripe_customer_code"],
			[
				"prescribed_fix",
				"Set require_src_qualifier=true, bkcc_prefix='STRIPE_EU', and bind via sal_customer_alias",
			],
		],
		raw:
			"@toon_v1\nrun_id: run_2026_01_16_commerce_4w\nattempt: 1/3\nstatus: REJECT-WITH-TRACE\n" +
			"violations[1]{gate,rule,action,target,fix}:\n" +
			" G1,DV-COL-012,REJECT,hub_customer,\"Set require_src_qualifier=true & bkcc_prefix='STRIPE_EU' on stripe_customer_code\"",
	},
	gate_static: {
		id: "gate_static",
		name: "static_gates_0_to_4.json",
		short: "Gates 0–4 · 99/99 checks PASS",
		group: "Stage output",
		type: "StaticGatesScorecard",
		fmt: "JSON",
		viz: "checks",
		desc: "Stage 05 verification scorecard across Gates 0, 1, 2, 3 and 4 after Attempt 2 self-healing convergence.",
		waves: ["W0", "W1", "W2", "W3"],
		checks: [
			["G0 ·PRF-01", "All 24 profiled columns & Wave-2 drift rules aligned", "PASS"],
			[
				"G1 ·DV-COL-001",
				"Column triage ledger bijection: 24 / 24 columns accounted for",
				"PASS",
			],
			[
				"G1 ·DV-COL-012",
				"Tier-3 stripe_customer_code scoped under bkcc_prefix='STRIPE_EU' (Attempt 2)",
				"AUTO_REPAIRED",
			],
			[
				"G2 ·AST-A5",
				"SQLGlot AST & Algorithm A.5 length-prefixed SHA-256 hash expressions valid",
				"PASS",
			],
			[
				"G3 ·DV-STR-001..014",
				"NetworkX bipartite DAG: Hub outdeg=0, Link arity>=2, Sat outdeg=1, 0 cycles",
				"PASS",
			],
			[
				"G4 ·ALG-A1",
				"YAGNI write-amplification W_amp = 3.40 MB/day < 50.0 MB/day budget",
				"PASS",
			],
		],
		raw: '{\n  "attempt_converged": 2,\n  "checks_evaluated": 99,\n  "checks_passed": 99,\n  "gates": {\n    "gate_0_profile_drift": "PASS",\n    "gate_1_contract_grain": "AUTO_REPAIRED",\n    "gate_2_sqlglot_ast": "PASS",\n    "gate_3_networkx_dag": "PASS",\n    "gate_4_yagni_cost_guard": "PASS"\n  }\n}',
	},
	sandbox: {
		id: "sandbox",
		name: "gate5_duckdb_sandbox.json",
		short: "Gate 5 · 4 adversarial batches PASS",
		group: "Stage output",
		type: "SandboxExecutionReport",
		fmt: "JSON",
		viz: "checks",
		desc: "Stage 06 ephemeral DuckDB (:memory:) execution results across 4 adversarial test batches and bi-temporal PIT audit queries.",
		waves: ["W0", "W1", "W2", "W3"],
		checks: [
			[
				"Batch 1 · Baseline",
				"Initial 4-zone DDL/DML load: 5 Hubs, 4 Links, 7 Satellites populated; 0 PK collisions",
				"PASS",
			],
			[
				"Batch 2 · Replay",
				"Duplicate batch re-ingestion: delta_inserts = 0 across all 16 vault tables",
				"PASS",
			],
			[
				"Batch 3 · Late Hub",
				"Out-of-order child Link before parent Hub: 0 orphan foreign keys",
				"PASS",
			],
			[
				"Batch 4 · Null/Ghost",
				"All-NULL key -> ZERO_KEY (-1:^^) & Ghost record 0x00..00 verified",
				"PASS",
			],
			[
				"PIT Audit · D.10",
				"Point-in-time query at 2026-01-16T00:00:00Z returns 1 row per Hub key",
				"PASS",
			],
		],
		raw: '{\n  "engine": "DuckDB :memory:",\n  "batches_passed": 4,\n  "duplicate_replay_delta_inserts": 0,\n  "orphan_foreign_keys": 0,\n  "pit_audit_passed": true\n}',
	},
	cas: {
		id: "cas",
		name: "cas_manifest.json",
		short: "5 sealed SHA-256 digests",
		group: "Stage output",
		type: "CASManifest",
		fmt: "JSON",
		viz: "table",
		desc: "Content-Addressable Store (CAS) manifest mapping each sealed artifact to its immutable SHA-256 digest.",
		waves: ["W0", "W1", "W2", "W3"],
		cols: ["artifact", "media_type", "sha256_prefix", "status"],
		rows: [
			["final_contract.yaml", "application/x-yaml", "d4e5f607…a1b2c3", "SEALED"],
			["final_canonical_ir.json", "application/json", "e5f60718…b2c3d4", "SEALED"],
			["final_four_zone.sql", "application/sql", "f6071829…c3d4e5", "SEALED"],
			["pipeline_report.json", "application/json", "18293a4b…d4e5f6", "SEALED"],
			["vault-ledger.jsonl", "application/x-ndjson", "293a4b5c…e5f607", "SEALED"],
		],
		foot: "All 5 final artifacts verified in CAS with zero digest mismatches.",
		raw: '{\n  "cas_integrity_passed": true,\n  "sealed_artifacts": 5\n}',
	},
	final_contract: {
		id: "final_contract",
		name: "final_contract.yaml",
		short: "5 Hubs · 4 Links · 7 Satellites",
		group: "Final output",
		type: "DVModelContract v6.0",
		fmt: "YAML",
		viz: "dv",
		desc: "Verified Data Vault 2.0 model contract after all 4 waves and Gate 0–5 verification. Defines 5 Hubs, 4 Links and 7 Satellites.",
		waves: ["W0", "W1", "W2", "W3"],
		sha256: "d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3",
		dv: {
			nodes: [
				{
					id: "hub_customer",
					kind: "hub",
					label: "hub_customer",
					sub: "GLOBAL:tax_id · STRIPE_EU:cust_code",
					x: 16,
					y: 16,
					w: 184,
					h: 48,
				},
				{
					id: "hub_order",
					kind: "hub",
					label: "hub_order",
					sub: "GLOBAL:order_id",
					x: 248,
					y: 16,
					w: 164,
					h: 48,
				},
				{
					id: "hub_invoice",
					kind: "hub",
					label: "hub_invoice",
					sub: "STRIPE_EU:invoice_number",
					x: 460,
					y: 16,
					w: 184,
					h: 48,
				},
				{
					id: "hub_contract_document",
					kind: "hub",
					label: "hub_contract_document",
					sub: "GLOBAL:doc_id (Wave 3 MSA)",
					x: 16,
					y: 224,
					w: 184,
					h: 44,
				},
				{
					id: "hub_promotion",
					kind: "hub",
					label: "hub_promotion",
					sub: "GLOBAL:promo_campaign_code",
					x: 248,
					y: 224,
					w: 164,
					h: 44,
				},
				{
					id: "lnk_customer_order",
					kind: "link",
					label: "lnk_customer_order",
					sub: "customer <-> order",
					x: 148,
					y: 86,
					w: 148,
					h: 36,
				},
				{
					id: "lnk_order_invoice",
					kind: "link",
					label: "lnk_order_invoice",
					sub: "order <-> invoice",
					x: 374,
					y: 86,
					w: 148,
					h: 36,
				},
				{
					id: "lnk_customer_msa_doc",
					kind: "link",
					label: "lnk_customer_msa_doc",
					sub: "A.9b Tier-U1/U3",
					x: 16,
					y: 166,
					w: 156,
					h: 34,
				},
				{
					id: "lnk_order_promotion",
					kind: "link",
					label: "lnk_order_promotion",
					sub: "isolating (66.7% non-null)",
					x: 248,
					y: 166,
					w: 164,
					h: 34,
				},
				{
					id: "sat_customer_core",
					kind: "sat",
					label: "sat_customer_core",
					sub: "name, country, credit_limit",
					x: 16,
					y: 78,
					w: 120,
					h: 34,
				},
				{
					id: "sat_customer_pii",
					kind: "sat_pii",
					label: "sat_customer_pii",
					sub: "RESTRICTED_PII · email",
					x: 16,
					y: 118,
					w: 120,
					h: 34,
				},
				{
					id: "sat_customer_loyalty_2026",
					kind: "sat",
					label: "sat_customer_loyalty_2026",
					sub: "Wave 2 additive companion",
					x: 184,
					y: 128,
					w: 152,
					h: 32,
				},
				{
					id: "sat_order_details",
					kind: "sat",
					label: "sat_order_details",
					sub: "order_date, status, net_usd",
					x: 344,
					y: 128,
					w: 144,
					h: 32,
				},
				{
					id: "sat_invoice_settlement",
					kind: "sat",
					label: "sat_invoice_settlement",
					sub: "amount_eur, status",
					x: 500,
					y: 128,
					w: 144,
					h: 32,
				},
				{
					id: "sat_msa_extractions",
					kind: "sat",
					label: "sat_msa_extractions",
					sub: "quote, char_start..end, conf",
					x: 432,
					y: 224,
					w: 184,
					h: 44,
				},
			],
			edges: [
				{ x1: 180, y1: 64, x2: 205, y2: 86 },
				{ x1: 275, y1: 64, x2: 245, y2: 86 },
				{ x1: 380, y1: 64, x2: 420, y2: 86 },
				{ x1: 500, y1: 64, x2: 470, y2: 86 },
				{ x1: 330, y1: 64, x2: 330, y2: 166, dashed: true },
				{ x1: 330, y1: 200, x2: 330, y2: 224, dashed: true },
				{ x1: 64, y1: 64, x2: 64, y2: 166 },
				{ x1: 64, y1: 200, x2: 64, y2: 224 },
			],
		},
		raw:
			"contract_version: '6.0'\nproject_id: global_commerce_vault\n" +
			"hubs:\n" +
			"  - name: hub_customer\n    concept: CBC_CUSTOMER\n    business_keys: [GLOBAL:customer_tax_id, STRIPE_EU:stripe_customer_code]\n" +
			"  - name: hub_order\n    concept: CBC_ORDER\n    business_keys: [GLOBAL:order_id]\n" +
			"  - name: hub_invoice\n    concept: CBC_INVOICE\n    business_keys: [STRIPE_EU:invoice_number]\n" +
			"  - name: hub_promotion\n    concept: CBC_PROMOTION\n    business_keys: [GLOBAL:promo_campaign_code]\n" +
			"  - name: hub_contract_document\n    concept: CBC_CONTRACT_DOCUMENT\n    business_keys: [GLOBAL:doc_id]\n" +
			"links:\n" +
			"  - name: lnk_customer_order\n    hubs: [hub_customer, hub_order]\n" +
			"  - name: lnk_order_invoice\n    hubs: [hub_order, hub_invoice]\n" +
			"  - name: lnk_order_promotion\n    hubs: [hub_order, hub_promotion]\n    is_isolating_optional_link: true\n" +
			"  - name: lnk_customer_msa_doc\n    hubs: [hub_customer, hub_contract_document]\n" +
			"satellites:\n" +
			"  - name: sat_customer_core\n    parent: hub_customer\n" +
			"  - name: sat_customer_pii\n    parent: hub_customer\n    data_classification: RESTRICTED_PII\n" +
			"  - name: sat_customer_loyalty_2026\n    parent: hub_customer\n    drift_companion: true\n" +
			"  - name: sat_order_details\n    parent: hub_order\n" +
			"  - name: sat_invoice_settlement\n    parent: hub_invoice\n" +
			"  - name: sat_promotion_terms\n    parent: hub_promotion\n" +
			"  - name: sat_msa_extractions\n    parent: lnk_customer_msa_doc",
	},
	final_ir: {
		id: "final_ir",
		name: "final_canonical_ir.json",
		short: "CanonicalIR · 16 tables · 4 zones",
		group: "Final output",
		type: "CanonicalIR",
		fmt: "JSON",
		viz: "code",
		desc: "Platform-neutral Canonical Intermediate Representation lowered from final_contract.yaml by Stage 07 before SQL emission.",
		waves: ["W0", "W1", "W2", "W3"],
		sha256: "e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4",
		outline: [
			["ir_version", '"1.0.0" · dialect: duckdb'],
			["hash_recipe", "Algorithm A.5 · SHA256 length-prefixed"],
			["zone_1_bronze", "5 landing tables"],
			["zone_2_staging", "5 views (hash calc + Wave-2 drift shim)"],
			["zone_3_raw_vault", "5 Hubs · 4 Links · 7 Satellites"],
			["zone_4_business_vault", "pit_customer_as_of, bv_customer_360"],
		],
		raw:
			"{\n" +
			'  "ir_version": "1.0.0",\n' +
			'  "project_id": "global_commerce_vault",\n' +
			'  "hash_algorithm": "SHA256_LENGTH_PREFIXED_A5",\n' +
			'  "null_token": "-1:^^",\n' +
			'  "zero_key_hex": "0000000000000000000000000000000000000000000000000000000000000000",\n' +
			'  "zones": {\n' +
			'    "bronze_tables": 5,\n' +
			'    "staging_views": 5,\n' +
			'    "raw_vault_hubs": ["hub_customer", "hub_order", "hub_invoice", "hub_promotion", "hub_contract_document"],\n' +
			'    "raw_vault_links": ["lnk_customer_order", "lnk_order_invoice", "lnk_order_promotion", "lnk_customer_msa_doc"],\n' +
			'    "raw_vault_satellites": ["sat_customer_core", "sat_customer_pii", "sat_customer_loyalty_2026", "sat_order_details", "sat_invoice_settlement", "sat_promotion_terms", "sat_msa_extractions"],\n' +
			'    "business_vault_views": ["pit_customer_as_of", "bv_customer_order_invoice_360"]\n' +
			"  }\n" +
			"}",
	},
	final_sql: {
		id: "final_sql",
		name: "final_four_zone.sql",
		short: "DuckDB SQL · Bronze / Stg / RV / BV",
		group: "Final output",
		type: "CompiledSQLBundle",
		fmt: "SQL",
		viz: "code",
		desc: "Deterministic 4-zone DuckDB SQL bundle compiled from final_canonical_ir.json. Zero LLM tokens used in compilation.",
		waves: ["W0", "W1", "W2", "W3"],
		sha256: "f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5",
		outline: [
			["Zone 1 · Bronze", "CREATE SCHEMA bronze; raw landing tables"],
			["Zone 2 · Staging", "CREATE VIEW stg.v_crm_customers (A.5 hash + A.9a shim)"],
			["Zone 3 · Raw Vault", "hub_customer, lnk_customer_order, sat_customer_core…"],
			["Zone 4 · Business Vault", "pit_customer_as_of (D.10 PIT + ghost record)"],
		],
		raw:
			"-- Zone 2: Staging view with Algorithm A.5 length-prefixed SHA-256 & Wave-2 drift shim\n" +
			"CREATE OR REPLACE VIEW stg.v_crm_customers AS\n" +
			"SELECT\n" +
			"  lower(hex(sha256(\n" +
			"    '6:GLOBAL||' || length(trim(upper(customer_tax_id)))::VARCHAR || ':' || trim(upper(customer_tax_id))\n" +
			"  ))) AS hk_customer,\n" +
			"  lower(hex(sha256(\n" +
			"    coalesce(length(legal_name)::VARCHAR || ':' || legal_name, '-1:^^') || '||' ||\n" +
			"    coalesce(length(country_iso)::VARCHAR || ':' || country_iso, '-1:^^') || '||' ||\n" +
			"    coalesce(length(credit_limit_usd::VARCHAR)::VARCHAR || ':' || credit_limit_usd::VARCHAR, '-1:^^') || '||' ||\n" +
			"    '-1:^^' -- deprecated fax_number shim (Algorithm A.9a)\n" +
			"  ))) AS hash_diff,\n" +
			"  customer_tax_id,\n" +
			"  legal_name,\n" +
			"  country_iso,\n" +
			"  credit_limit_usd AS credit_limit,\n" +
			"  CAST(NULL AS VARCHAR) AS fax_number,\n" +
			"  CURRENT_TIMESTAMP AS load_dts,\n" +
			"  'CRM_GLOBAL' AS rec_src\n" +
			"FROM bronze.src_crm_customers;\n\n" +
			"-- Zone 3: Raw Vault Hub\n" +
			"CREATE TABLE IF NOT EXISTS rv.hub_customer (\n" +
			"  hk_customer CHAR(64) PRIMARY KEY,\n" +
			"  bk_customer VARCHAR NOT NULL,\n" +
			"  load_dts    TIMESTAMP NOT NULL,\n" +
			"  rec_src     VARCHAR NOT NULL\n" +
			");",
	},
	report: {
		id: "report",
		name: "pipeline_report.json",
		short: "OVERALL PASS · 4/4 waves · 6/6 gates",
		group: "Final output",
		type: "AgenticPipelineReport",
		fmt: "JSON",
		viz: "checks",
		desc: "End-to-end execution report covering pre-flight input validation, all 4 onboarding waves, all 6 verification gates, and post-flight PII/CAS checks.",
		waves: ["W0", "W1", "W2", "W3"],
		sha256: "18293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f607",
		checks: [
			[
				"Pre-flight",
				"Input spec, ontology & 5 source files validated before Stage 00",
				"PASS",
			],
			[
				"Wave 0 · Cold start",
				"CRM + ERP onboarded; row_seq_id tombstoned; promo FK split into isolating Link",
				"PASS",
			],
			[
				"Wave 1 · Incremental",
				"Stripe EU onboarded; Tier-3 BKCC collision repaired on Attempt 2/3",
				"AUTO_REPAIRED",
			],
			[
				"Wave 2 · Schema drift",
				"3 CRM drift mutations absorbed with 0 historical hash_diff breaks",
				"AUTO-FIX",
			],
			[
				"Wave 3 · Unstructured",
				"MSA addendum spans verified (1 Tier-U1 auto-linked, 1 Tier-U3 hold resolved)",
				"ASK-USER → resolved",
			],
			[
				"Post-flight",
				"JSON Schema conformance, zero unredacted RESTRICTED_PII, 5/5 CAS digests sealed",
				"PASS",
			],
		],
		raw: '{\n  "run_id": "run_2026_01_16_commerce_4w",\n  "project_id": "global_commerce_vault",\n  "status": "COMPLETED",\n  "overall_passed": true,\n  "waves_completed": 4,\n  "gates_passed": 6,\n  "self_healing_attempts_used": 2,\n  "pii_leak_check_passed": true,\n  "cas_integrity_passed": true\n}',
	},
	ledger: {
		id: "ledger",
		name: "vault-ledger.jsonl",
		short: "9 events · RBAC-redacted stream",
		group: "Final output",
		type: "VaultObservabilityEvent[]",
		fmt: "JSONL",
		viz: "events",
		desc: "Append-only observability event stream emitted across Stages 00–07, filtered through the 5-tier RBAC redactor for role INTERNAL_MODELER.",
		waves: ["W0", "W1", "W2", "W3"],
		sha256: "293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718",
		events: [
			[
				"STAGE_00",
				"Grounded 5 sources across 4 waves onto 5 Graph A concepts (100% coverage).",
				"PASS",
			],
			[
				"STAGE_01",
				"Profiled 24 columns; quarantined crm.row_seq_id; flagged sparse promo_campaign_code (66.7%).",
				"PASS",
			],
			[
				"STAGE_01",
				"Algorithm A.9a classified 3 Wave-2 CRM drift mutations (0 broken hash_diffs).",
				"AUTO-FIX",
			],
			[
				"STAGE_02",
				"Algorithm A.9b verified 2 MSA spans; Tier-U3 guarantor held and resolved.",
				"ASK-USER → resolved",
			],
			[
				"STAGE_03",
				"Pass 1 column triage ledger complete: 24 / 24 columns classified (100%).",
				"PASS",
			],
			[
				"STAGE_04",
				"Pass 2/3 drafted 5 Hubs, 4 Links and 7 Satellites (with sat_customer_pii split).",
				"PASS",
			],
			[
				"STAGE_05",
				"Gate 1 Attempt 1 rejected unscoped stripe_customer_code (DV-COL-012); emitted 44-token @toon_v1.",
				"REJECT-WITH-TRACE",
			],
			[
				"STAGE_05",
				"Attempt 2 applied bkcc_prefix='STRIPE_EU'; all 99 static checks across Gates 0–4 passed.",
				"AUTO_REPAIRED",
			],
			[
				"STAGE_06..07",
				"Gate 5 DuckDB 4-batch sandbox passed; compiled 4-zone SQL and sealed 5 artifacts to CAS.",
				"PASS",
			],
		],
		raw:
			'{"seq":1,"stage":"STAGE_00_ORCHESTRATION","status":"PASS","headline":"Grounded 5 sources onto 5 Graph A concepts"}\n' +
			'{"seq":2,"stage":"STAGE_01_SCOUT_SOURCE","status":"PASS","headline":"Profiled 24 columns; quarantined row_seq_id"}\n' +
			'{"seq":3,"stage":"STAGE_01_SCOUT_SOURCE","status":"AUTO-FIX","headline":"Classified 3 Wave-2 drift mutations"}\n' +
			'{"seq":4,"stage":"STAGE_02_SCOUT_UNSTRUCTURED","status":"ASK-USER","headline":"Extracted 2 MSA spans; 1 Tier-U3 hold resolved"}\n' +
			'{"seq":5,"stage":"STAGE_03_SHIP_VAULT_PASS1","status":"PASS","headline":"Triaged 24/24 columns (100%)"}\n' +
			'{"seq":6,"stage":"STAGE_04_SHIP_VAULT_PASS2","status":"PASS","headline":"Synthesised 5 Hubs, 4 Links, 7 Satellites"}\n' +
			'{"seq":7,"stage":"STAGE_05_AXI_DV_STATIC_GATES","status":"REJECT-WITH-TRACE","rule":"DV-COL-012","attempt":1}\n' +
			'{"seq":8,"stage":"STAGE_05_AXI_DV_STATIC_GATES","status":"AUTO_REPAIRED","rule":"DV-COL-012","attempt":2}\n' +
			'{"seq":9,"stage":"STAGE_07_DETERMINISTIC_COMPILER","status":"PASS","headline":"4-zone SQL compiled & sealed to CAS"}',
	},
};

export const GLOBAL_COMMERCE_EXPLORER_BUNDLE: ExplorerBundleDTO = {
	projectId: "global_commerce_vault",
	displayName: "global_commerce_vault",
	runId: "run_2026_01_16_commerce_4w",
	specFile: "pipeline_input.yaml",
	wavesCount: 4,
	waves: [
		{
			id: "W0",
			name: "wave_0_cold_start",
			description: "Cold Start N=2 (CRM + ERP)",
		},
		{
			id: "W1",
			name: "wave_1_incremental_stripe",
			description: "Incremental Source N=3 (Stripe EU Invoices)",
		},
		{
			id: "W2",
			name: "wave_2_crm_schema_drift",
			description: "Intra-Source Schema Drift (CRM v2)",
		},
		{
			id: "W3",
			name: "wave_3_unstructured_msa",
			description: "Unstructured MSA Addendum Extraction",
		},
	],
	statusLabel: "Sealed · 6/6 gates PASS",
	gatesPassed: 6,
	gatesTotal: 6,
	rbacRole: "INTERNAL_MODELER",
	hasInitialContract: false,
	phases: [...EXPLORER_PHASES],
	stages: CANONICAL_STAGE_DEFINITIONS.map((s) => ({
		...s,
		ins: [...s.ins],
		outs: [...s.outs],
		steps: s.steps.map((st) => ({
			...st,
			reads: [...st.reads],
			writes: [...st.writes],
		})),
		comps: s.comps.map((c) => [...c] as [string, string, string, string]),
	})),
	artifacts: GLOBAL_COMMERCE_ARTIFACTS,
	settingIds: ["harness", "runopts"],
	inputIds: ["spec", "ontology", "crm", "erp", "stripe", "crm2", "msa"],
	outputIds: ["final_contract", "final_ir", "final_sql", "report", "ledger"],
};

/**
 * Dynamically build or hydrate an `ExplorerBundleDTO` from `@opendv/protocol-ts`
 * `ProjectDTO`, `ProjectRunDTO`, and `VaultObservabilityEventDTO[]`.
 */
export function buildExplorerBundleFromObservabilityEvents(
	project: ProjectDTO = PE_DEMO_PROJECT,
	run: ProjectRunDTO = PE_DEMO_RUN,
	events: readonly VaultObservabilityEventDTO[] = PE_DEMO_EVENTS,
	rbacRole: RbacRole = "INTERNAL_MODELER",
): ExplorerBundleDTO {
	const clonedArtifacts: Record<string, Artifact> = {};
	for (const [k, v] of Object.entries(GLOBAL_COMMERCE_ARTIFACTS)) {
		clonedArtifacts[k] = {
			...v,
			waves: v.waves ? [...v.waves] : undefined,
			cols: v.cols ? [...v.cols] : undefined,
			rows: v.rows ? v.rows.map((r) => [...r]) : undefined,
			kv: v.kv ? v.kv.map((pair) => [...pair] as [string, string]) : undefined,
			checks: v.checks
				? v.checks.map((c) => [...c] as [string, string, StatusTag])
				: undefined,
			events: v.events
				? v.events.map((e) => [...e] as [string, string, StatusTag])
				: undefined,
		};
	}

	if (events.length > 0 && clonedArtifacts.ledger) {
		const mappedEvents: [string, string, StatusTag][] = events.map((ev) => {
			const shortStage = ev.stage_id.slice(0, 8);
			const statusTag: StatusTag =
				ev.status === "PASS" ||
				ev.status === "AUTO_REPAIRED" ||
				ev.status === "ASK-USER" ||
				ev.status === "REJECT-WITH-TRACE" ||
				ev.status === "AUTO-FIX"
					? ev.status
					: "PASS";
			return [shortStage, ev.layer1_story_banner.headline, statusTag];
		});
		clonedArtifacts.ledger = {
			...clonedArtifacts.ledger,
			short: `${events.length} events · RBAC-redacted stream`,
			events: mappedEvents,
			raw: events
				.map((ev, idx) =>
					JSON.stringify({
						seq: idx + 1,
						event_id: ev.event_id,
						stage: ev.stage_id,
						status: ev.status,
						headline: ev.layer1_story_banner.headline,
					}),
				)
				.join("\n"),
		};
	}

	return {
		...GLOBAL_COMMERCE_EXPLORER_BUNDLE,
		projectId: project.project_id,
		displayName: project.project_id,
		runId: run.run_id,
		rbacRole,
		artifacts: clonedArtifacts,
	};
}

export const PE_EXPLORER_BUNDLE: ExplorerBundleDTO =
	buildExplorerBundleFromObservabilityEvents(
		PE_DEMO_PROJECT,
		PE_DEMO_RUN,
		PE_DEMO_EVENTS,
	);

/**
 * Compute which stage(s) produced a given artifact ID.
 */
export function getArtifactProducedBy(
	stages: readonly Stage[],
	artifactId: string,
): string {
	const producers: string[] = [];
	for (const stage of stages) {
		if (stage.outs.includes(artifactId)) {
			producers.push(stage.key.slice(0, 8));
		}
	}
	return producers.join(", ") || "— (provided)";
}

/**
 * Compute which stage(s) consumed a given artifact ID.
 */
export function getArtifactConsumedBy(
	stages: readonly Stage[],
	artifactId: string,
): string {
	const consumers: string[] = [];
	for (const stage of stages) {
		if (stage.ins.includes(artifactId)) {
			consumers.push(stage.key.slice(0, 8));
		}
	}
	return consumers.join(", ") || "— (final)";
}

/**
 * Filter input artifact IDs by active wave (`all` | `W0` | `W1` | `W2` | `W3`).
 */
export function filterArtifactIdsByWave(
	artifactIds: readonly string[],
	artifacts: Readonly<Record<string, Artifact>>,
	wave: WaveFilterId,
): string[] {
	if (wave === "all") {
		return [...artifactIds];
	}
	return artifactIds.filter((id) => {
		const art = artifacts[id];
		if (!art || !art.waves || art.waves.length === 0) {
			return true;
		}
		return art.waves.includes(wave);
	});
}
