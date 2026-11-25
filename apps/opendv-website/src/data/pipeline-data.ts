import type {
	FaultPreset,
	PipelineStage,
	TrapItem,
	VerifierGate,
} from "./types";

export const NAIVE_TRAPS: readonly TrapItem[] = [
	{
		id: "Trap 1",
		title: "Source-System Hub Proliferation",
		summary:
			"Agents mint separate hubs per feed (crm_customer, erp_buyer) instead of integrating via shared BKCC and Same-As Links (sal_*).",
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
			"PDF and regulatory facts enter without normalized [char_start, char_end] spans, OCR receipts, or AI_PROVENANCE_SATELLITE lineage.",
	},
];

export const PIPELINE_STAGES: readonly PipelineStage[] = [
	{
		id: "STEP_1_3",
		code: "Steps 1–3 · project.sqlite",
		phase: "Scope & Evidence",
		actor: "Zero-LLM Profiler",
		temp: "Deterministic",
		summary:
			"Inits core_spine or .opendv.zip, attaches 6 static adapters, builds 125-perm (25×5) MinHash clusters, DocumentMaps, and approve-scan gates.",
	},
	{
		id: "STEP_4",
		code: "Step 4 · JobMcpBroker",
		phase: "Discovery & Ontology",
		actor: "@ontology-first-mate",
		temp: "Brokered",
		summary:
			"Coordinator (/coord) dispatches isolated /scout subprocesses to ground PATH_A1/A2 (FIBO BM25) & PATH_B1/B2 concepts with Clarification Holds.",
	},
	{
		id: "STEP_5",
		code: "Step 5 · 4-Mode Explorer",
		phase: "Discovery & Ontology",
		actor: "Human Steward",
		temp: "OCC Commit",
		summary:
			"Curates Graph A across Canvas, KeyTierEnum, comment-preserving YAML CST, and ONT-AP-001..005 / quarantine waivers with base_revision_id OCC.",
	},
	{
		id: "S6_02_03",
		code: "S6-02 / S6-03 · Loop L1",
		phase: "CEGAR & Critics",
		actor: "@ship-vault + L1",
		temp: "Draft -> IR",
		summary:
			"Shards DraftVaultIR by MinHash cluster, injects UNRESOLVED column triage, applies Alg A.1–A.4/A.9a codemods, and lowers VaultSchemaIR v2.0.",
	},
	{
		id: "S6_04_V1_V6",
		code: "S6-04 · V1–V6 / V6B",
		phase: "CEGAR & Critics",
		actor: "Verifier + Prover",
		temp: "Deterministic",
		summary:
			"Proves NetworkX Z0–Z4 DAG (V1), Alg A.5/A.9b/locks (V2–V5), 15/15 DV-DYN in :memory: DuckDB (V6), and DV-AP-001..012 smells (V6B).",
	},
	{
		id: "S6_04_V7_L3",
		code: "S6-04 / S6-05 · V7 + L3",
		phase: "CEGAR & Critics",
		actor: "@dv-semantic-critic",
		temp: "Frontier Tier",
		summary:
			"Scores VaultSchemaIR v2.0 (V7 >= 90/95), audits WAIV-<sha8> waivers, and surfaces plain-English Guided Decision Cards on L3 holds.",
	},
	{
		id: "S6_06_07",
		code: "S6-06 / S6-07 · V8 + Seal",
		phase: "CEGAR & Critics",
		actor: "@dv-horizon-critic",
		temp: "Resilience >= 85",
		summary:
			"Stress-tests BRK-01..08 future scenarios (Bucket A <= 1 warm steer, Bucket B Steward choice, Bucket C watchlist) before sealing LOCK-W00N.",
	},
	{
		id: "STEP_7",
		code: "Step 7 · TargetEvalGate",
		phase: "11-Target Storage",
		actor: "SqlGlot + Emitters",
		temp: "Deterministic",
		summary:
			"Compiles 8 SQL dialects + Iceberg/Parquet/dbt into targets/, proves EVAL-AST/RUN/FMT parity, and queries read-only DuckDB locally.",
	},
];

export const VERIFIER_GATES: readonly VerifierGate[] = [
	{
		id: "L1",
		name: "Deterministic IR Codemods",
		rules: "Alg A.1–A.4 · A.9a · S6-03",
		check:
			"Injects UNRESOLVED column_triage entries, splits sparse FKs (U_nn < 0.95), demotes ref_*, and routes Wave N+1 columns into sat_*_ext_vN.",
		repair:
			"Rewrites DraftVaultIR deterministically with zero LLM tokens before V1–V6b verification.",
	},
	{
		id: "V1_V5",
		name: "Symbolic DAG, Hashing & Locks",
		rules: "DV-STR-001..015 · Alg A.5 · A.9b",
		check:
			"Verifies Z0–Z4 bipartite DAG (0 Hub->Hub, 0 Z3->Z2), 100% column triage, <len>:<val> SHA-256 recipes, Zero/Ghost sentinels, citations, and LOCK-W00N parity.",
		repair:
			"Returns structured VerifierDiagnostic targets to L2 @ship-vault repair or L3 Steward Hold.",
	},
	{
		id: "V6",
		name: "DuckDB 15-Invariant Prover",
		rules: "DV-DYN-001..015 · Alg A.6",
		check:
			"Executes Ghost INNER JOIN, Replay delta=0, Boundary-Shift ('AB','C'!='A','BC'), and BKCC isolation inside :memory: DuckDB (enable_external_access=false).",
		repair:
			"Halts lock sealing on any AST or runtime invariant breach and captures concrete counterexamples.",
	},
	{
		id: "V6B",
		name: "Schema Anti-Pattern Smells",
		rules: "DV-AP-001..012 · WAIV-<sha8>",
		check:
			"Detects unsplit PII satellites, premature micro-satellites, soft-rule impurity (DV-AP-010), and empirical Batch-3 delta churn (DV-AP-011).",
		repair:
			"Triggers Ask AI to Refactor Instead or requires >= 20-char WAIV-<sha8> justification audited by V7.",
	},
	{
		id: "V7",
		name: "Frontier Semantic Critic",
		rules: "ModelTier FRONTIER_CRITIC · >= 90/95",
		check:
			"Audits domain semantics, naming clarity, and V6B waiver legitimacy over compact 7-tuples (required under Balanced >= 90 and Strict >= 95).",
		repair:
			"Drives convergence energy E(IR_k) to 0 via bounded K_max L2 repair iterations.",
	},
	{
		id: "V8",
		name: "Horizon Resilience Critic",
		rules: "BRK-01..08 · Bucket A/B/C · >= 85",
		check:
			"Stress-tests 8 future-wave breaking scenarios (mutable BKs, type widening, BK collisions, bi-temporal restatements) against evolution_locks.",
		repair:
			"Applies <= 1 Bucket A warm steer, escalates Bucket B choices in Guided mode, or records Bucket C watchlist.",
	},
];

export const FAULT_PRESETS: readonly FaultPreset[] = [
	{
		id: "fault-bk-collision",
		label: "Inject Sparse FK & BK Collision",
		gateId: "L1",
		symptom:
			"Position.account_id has U_nn = 0.62 NULL density alongside multi-source TIER_2_ENTERPRISE_SOR key overlap.",
		resolution:
			"L1 Alg A.4 extracts optional FK into companion lnk_position_account + Ghost 0*64 key and enforces BKCC + sal_* scoping.",
		traceSnippet:
			"L1|AUTO-FIX|ALG_A4|split_sparse_fk:lnk_position_account|U_nn=0.62->V6:PASS",
	},
	{
		id: "fault-pii-split",
		label: "Inject Unsplit PII Email (DV-AP-007)",
		gateId: "V6B",
		symptom:
			"stewardship_officer_email landed inside standard sat_proxy_ballot_core without PII satellite isolation.",
		resolution:
			"V6B raises DV-AP-007; L2 @ship-vault splits sat_proxy_ballot_pii + <MASKED_PII> mask while E(IR_k) converges to 0.",
		traceSnippet:
			"V6B|SMELL|DV-AP-007|sat_proxy_ballot_core->split:sat_proxy_ballot_pii|E=0:PASS",
	},
	{
		id: "fault-hub-edge",
		label: "Inject Hub-to-Hub Edge (DV-STR-004)",
		gateId: "V1_V5",
		symptom:
			"Candidate IR declares a direct hub_holding -> hub_security edge violating Raw Vault bipartite topology.",
		resolution:
			"V1 NetworkX DAG rejects edge; L2 @ship-vault rewires via lnk_holding_security (Z2) and pit_holding (Z3) -> V6..V8 PASS.",
		traceSnippet:
			"V1_V5|REJECT|DV-STR-004|outdeg(hub_holding)=1|L2_repair:lnk_holding_security->PASS",
	},
];
