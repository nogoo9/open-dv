import type { AxiGate, FaultPreset, PipelineStage, TrapItem } from "./types";

export const NAIVE_TRAPS: readonly TrapItem[] = [
	{
		id: "Trap 1",
		title: "Source-System Hub Proliferation",
		summary:
			"Agents mint separate hubs per feed (crm_customer, erp_buyer) instead of integrating via shared BKCC and Same-As Links.",
	},
	{
		id: "Trap 2",
		title: "Surrogate Key Leakage",
		summary:
			"Auto-increment integers pass uniqueness checks and enter business keys, shattering multi-source Hub joins on replay.",
	},
	{
		id: "Trap 3",
		title: "Silent Hash Collisions",
		summary:
			"Naive CONCAT_WS concatenations collide ('AB','C' == 'A','BC') and drop nullable FKs via NULL propagation.",
	},
	{
		id: "Trap 4",
		title: "Destructive Satellite UPDATEs",
		summary:
			"Schema drift triggers ALTER or UPDATE statements inside SQL templates, erasing insert-only raw vault audit lineage.",
	},
	{
		id: "Trap 5",
		title: "Ungrounded Unstructured Claims",
		summary:
			"PDF and regulatory facts enter without source char-span offsets or entity-resolution confidence thresholds.",
	},
];

export const PIPELINE_STAGES: readonly PipelineStage[] = [
	{
		id: "STAGE_00",
		code: "STAGE_00_ORCHESTRATION",
		phase: "Discovery",
		actor: "@vault-first-mate",
		temp: "T=0.1",
		summary:
			"Validates inputs, binds deployed_contract (D12), and grounds sources against Graph A.",
	},
	{
		id: "STAGE_01",
		code: "STAGE_01_SCOUT_SOURCE",
		phase: "Discovery",
		actor: "@scout-source",
		temp: "T=0.0",
		summary:
			"Profiles relational cardinality/entropy (Graph B) and quarantines surrogate IDs.",
	},
	{
		id: "STAGE_02",
		code: "STAGE_02_SCOUT_UNSTRUCTURED",
		phase: "Discovery",
		actor: "@scout-unstructured",
		temp: "T=0.0",
		summary:
			"Extracts LiteParse verbatim char-spans and evaluates 3-tier entity resolution.",
	},
	{
		id: "STAGE_03",
		code: "STAGE_03_SHIP_VAULT_PASS1",
		phase: "Modelling",
		actor: "@ship-vault",
		temp: "T=0.1",
		summary:
			"Pass 1 CoT assigns 100% of profiled columns to explicit Data Vault 2.0 roles.",
	},
	{
		id: "STAGE_04",
		code: "STAGE_04_SHIP_VAULT_PASS2",
		phase: "Modelling",
		actor: "@ship-vault",
		temp: "T=0.1",
		summary:
			"Pass 2/3 CoT proposes declarative YAML Hubs, 3-Tier BKCCs, Links, and Satellites.",
	},
	{
		id: "STAGE_05",
		code: "STAGE_05_AXI_DV_STATIC_GATES",
		phase: "Verification",
		actor: "axi-dv",
		temp: "Deterministic",
		summary:
			"Runs Gates G0-G4, applies Alg A.1-A.9b codemods, or emits @toon_v1 repair traces.",
	},
	{
		id: "STAGE_06",
		code: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		phase: "Verification",
		actor: "DuckDB Sandbox",
		temp: "Deterministic",
		summary:
			"Gate G5 tests 4 adversarial batches + Query D.10b PIT audit in :memory: DuckDB.",
	},
	{
		id: "STAGE_07",
		code: "STAGE_07_DETERMINISTIC_COMPILER",
		phase: "Compilation",
		actor: "Compiler",
		temp: "Deterministic",
		summary:
			"Seals Canonical IR, VaultBlueprint, and multi-dialect 5-Zone (Z0–Z4) + information_mart SQL.",
	},
];

export const AXI_GATES: readonly AxiGate[] = [
	{
		id: "G0",
		name: "Profile & Drift",
		rules: "INV-06 · Alg A.9a",
		check:
			"Verifies Graph B statistical profiles and computes contract-diff schema drift without baseline CSVs.",
		repair: "Spawns additive _ext companion Satellites; locks prior hash_diff.",
	},
	{
		id: "G1",
		name: "Schema & BKCC",
		rules: "Draft 2020-12 · KEY-01..05 · Alg A.4",
		check:
			"Enforces 100% column triage, 3-Tier Business Key grain, and nullable FK density U_nn >= 0.95.",
		repair: "Alg A.4 splits nullable FKs into dedicated Links; removes leaks.",
	},
	{
		id: "G2",
		name: "Hash & SQLGlot AST",
		rules: "DV-AST · Alg A.5 · Alg A.6",
		check:
			"Requires injective <len>:<val> SHA-256 hashing and bans UPDATE/DELETE on Raw Vault via sqlglot.",
		repair:
			"Alg A.5/A.6 injects length-prefixed hash projections deterministically.",
	},
	{
		id: "G3",
		name: "NetworkX Bipartite DAG",
		rules: "DV-STR · EVO-01..08 · Alg A.7",
		check:
			"Proves outdeg(hub)=0, Link arity >= 2, acyclic references, and additive evolution locks.",
		repair:
			"Emits compact @toon_v1 trace back to @ship-vault for structural fix.",
	},
	{
		id: "G4",
		name: "YAGNI Write-Amp Guard",
		rules: "DV-EVO-004 · Alg A.1",
		check:
			"Blocks premature satellite splits unless W_amp >= 50 MB/day, >=10 cols, delta log10(lambda)>=2.0, or PII.",
		repair: "Alg A.1 merges micro-satellites automatically before compilation.",
	},
	{
		id: "G5",
		name: "4-Batch DuckDB Sandbox",
		rules: "DV-DYN-001..015 · Query D.10b",
		check:
			"Executes Ghost Record INNER JOIN, Replay delta_rows=0, Boundary-Shift ('AB','C'!='A','BC'), and BKCC isolation.",
		repair: "Halts release or triggers inline Steward Hold on violation.",
	},
];

export const FAULT_PRESETS: readonly FaultPreset[] = [
	{
		id: "fault-nullable-fk",
		label: "Inject Nullable FK (U_nn = 0.62)",
		gateId: "G1",
		symptom:
			"Position.account_id has 38% NULLs inside a mandatory Link composite key.",
		resolution:
			"Alg A.4 auto-fix extracts optional FK into dedicated lnk_position_account + Ghost key.",
		traceSnippet:
			"G1|AUTO-FIX|ALG_A4|split_nullable_fk:lnk_position_account|U_nn=0.62->PASS",
	},
	{
		id: "fault-micro-sat",
		label: "Inject Premature Micro-Satellite (4 MB/d)",
		gateId: "G4",
		symptom:
			"sat_security_flags carries 2 low-churn columns at 4 MB/day (< 50 MB/day threshold).",
		resolution:
			"Alg A.1 auto-fix coalesces into sat_securities; PII columns stay isolated.",
		traceSnippet:
			"G4|AUTO-FIX|ALG_A1|merge_satellite:sat_security_flags->sat_securities|PASS",
	},
	{
		id: "fault-hub-grain",
		label: "Inject Hub Grain Violation",
		gateId: "G3",
		symptom:
			"Candidate hub_position declares outdeg(h)=1 direct FK edge instead of bipartite Link.",
		resolution:
			"G3 rejects with @toon_v1 trace; @ship-vault k=1->k=2 rewires via lnk_position_security.",
		traceSnippet:
			"@toon_v1|G3|REJECT|DV-STR-004|hub_position:outdeg=1|repair=k2->G0..G5:PASS",
	},
];
