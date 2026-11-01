/**
 * Complete Private Equity Asset Management Demo Bundle (`Apex Horizon Capital Partners`)
 * covering all 12 Workbench Views (`WB-00` through `WB-11`) and 7 Scenes for offline replay
 * (`docs/06` §2.4, `docs/07` Scenes 1–7, `docs/10` §2).
 */

import { MockVaultEventStreamClient } from "../client/mock-client";
import type { ProjectDTO, ProjectRunDTO } from "../types/api-dtos";
import type {
	ContextRefsDTO,
	SemanticHashPillDTO,
	VaultObservabilityEventDTO,
} from "../types/observability-event";
import {
	ZERO_KEY_ERROR_HEX,
	ZERO_KEY_HEX,
	formatSemanticHashPill,
} from "../utils/hash-pill";

export const PE_DEMO_PROJECT_ID = "proj-apex-horizon-pe";
export const PE_DEMO_RUN_ID = "run-pe-apex-horizon-20261030";
export const PE_DEMO_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";

export const PE_DEMO_CONTEXT_REFS: ContextRefsDTO = {
	graph_a_sha256:
		"A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90",
	graph_b_sha256:
		"B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1",
	contract_yaml_pre_sha256:
		"C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2",
	contract_yaml_post_sha256:
		"D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3",
	canonical_ir_sha256:
		"E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4",
	compiled_sql_sha256:
		"F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5",
	toon_trace_sha256:
		"0718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F6",
};

export interface PeCanonicalHashPills {
	readonly CALPERS_LP_101: SemanticHashPillDTO;
	readonly FUND_IV_APEX_BO_IV: SemanticHashPillDTO;
	readonly FUND_I_CO_INVEST: SemanticHashPillDTO;
	readonly BAVARIAN_PENSION_LP_101: SemanticHashPillDTO;
	readonly NORDIC_SOVEREIGN_LP_104: SemanticHashPillDTO;
	readonly NORDISK_FOND_LP_102: SemanticHashPillDTO;
	readonly NORDIC_CLOUD_PORTCO: SemanticHashPillDTO;
	readonly CALPERS_FUND_IV_COMMITMENT_LHK: SemanticHashPillDTO;
	readonly CALPERS_SIDE_LETTER_PDF: SemanticHashPillDTO;
	readonly NORDIC_CLOUD_IC_MEMO_PDF: SemanticHashPillDTO;
	readonly SYSTEM_GHOST: SemanticHashPillDTO;
	readonly SYSTEM_ZERO_KEY_UNKNOWN: SemanticHashPillDTO;
	readonly SYSTEM_ZERO_KEY_ERROR: SemanticHashPillDTO;
}

/**
 * Canonical Private Equity Semantic Hash Pills matching `docs/06` §2.4 bit-for-bit.
 */
export const PE_CANONICAL_HASH_PILLS: PeCanonicalHashPills = {
	CALPERS_LP_101: formatSemanticHashPill(
		"HK",
		"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
		"GLOBAL",
		"LP-101",
		"CalPERS",
	),
	FUND_IV_APEX_BO_IV: formatSemanticHashPill(
		"HK",
		"94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
		"GLOBAL",
		"APEX_BO_IV",
		"Fund IV",
	),
	FUND_I_CO_INVEST: formatSemanticHashPill(
		"HK",
		"E77712A297CFF46051593303A3380820CA747A9A15C98254C518F7AF499E4C7E",
		"GLOBAL",
		"APEX_CO_I",
		"Co-Invest I",
	),
	BAVARIAN_PENSION_LP_101: formatSemanticHashPill(
		"HK",
		"6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
		"LEGACY_GP_ADMIN",
		"LP-101",
		"Bavarian Pension",
	),
	NORDIC_SOVEREIGN_LP_104: formatSemanticHashPill(
		"HK",
		"970AE134F322F208ADAFE424B8394FC039FB4FD77A3D11A4D2147130561E5106",
		"GLOBAL",
		"LP-104",
		"Nordic Sovereign",
	),
	NORDISK_FOND_LP_102: formatSemanticHashPill(
		"HK",
		"0B27D3AFD414BBAC781FC852588019FA77DAF121231396351646BAF93CBF9033",
		"LEGACY_GP_ADMIN",
		"LP-102",
		"Nordisk Fond",
	),
	NORDIC_CLOUD_PORTCO: formatSemanticHashPill(
		"HK",
		"0C1850CAC2E686A1F9233FA223F3986DC0949963DE1A32DFFDB25E1139B0454E",
		"GLOBAL",
		"529900T8BM49AURSDO55",
		"Nordic Cloud",
	),
	CALPERS_FUND_IV_COMMITMENT_LHK: formatSemanticHashPill(
		"LHK",
		"EB156109CC1A7C9A609169970B396D095950BD2309527D229017AD0D357A7C4E",
		"GLOBAL",
		"APEX_BO_IV:LP-101:COMM-2026-001",
		"Fund IV <-> CalPERS",
	),
	CALPERS_SIDE_LETTER_PDF: formatSemanticHashPill(
		"HK",
		"864D5083723B9FD7FE69085C2C31B646BD5E3DEAEF61B64C08D545EDE502EE6B",
		"GLOBAL",
		"s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf",
		"CalPERS Side Letter",
	),
	NORDIC_CLOUD_IC_MEMO_PDF: formatSemanticHashPill(
		"HK",
		"99D8178E34640A825C99AC0D343A43DA6D3F6EE5C9A413386801492D88F04158",
		"GLOBAL",
		"s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf",
		"Nordic Cloud Q3 IC Memo",
	),
	SYSTEM_GHOST: formatSemanticHashPill(
		"GHOST",
		ZERO_KEY_HEX,
		"SYSTEM",
		"00000000",
		"SYSTEM_GHOST (1900-01-01)",
	),
	SYSTEM_ZERO_KEY_UNKNOWN: formatSemanticHashPill(
		"ZERO-KEY",
		ZERO_KEY_HEX,
		"SYSTEM",
		"-1",
		"UNKNOWN (-1)",
	),
	SYSTEM_ZERO_KEY_ERROR: formatSemanticHashPill(
		"ERR-KEY",
		ZERO_KEY_ERROR_HEX,
		"SYSTEM",
		"-2",
		"ERROR (-2)",
	),
};

export const PE_DEMO_PROJECT: ProjectDTO = {
	project_id: PE_DEMO_PROJECT_ID,
	display_name: "Apex Horizon Capital Partners — Private Equity Vault",
	domain_namespace: "pe_asset_management",
	graph_a_sha256:
		"A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90",
	latest_run_id: PE_DEMO_RUN_ID,
	created_at_utc: "2026-10-30T08:55:00Z",
};

export const PE_DEMO_RUN: ProjectRunDTO = {
	run_id: PE_DEMO_RUN_ID,
	project_id: PE_DEMO_PROJECT_ID,
	harness_provider: "antigravity",
	status: "COMPLETED",
	started_at_utc: "2026-10-30T09:00:00Z",
	completed_at_utc: "2026-10-30T09:00:45Z",
	current_stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
	current_view_id: "WB-11",
	event_count: 13,
	gate_scorecard: {
		gate_0_profile_drift: "PASS",
		gate_1_contract_grain: "AUTO_REPAIRED",
		gate_2_sqlglot_ast: "PASS",
		gate_3_networkx_dag: "PASS",
		gate_4_yagni_cost_guard: "AUTO-FIX",
		gate_5_duckdb_sandbox: "PASS",
	},
	active_hold_ids: ["hold-tier-u3-horizon-sovereign-vehicle"],
	events_sse_url: `/api/v1/runs/${PE_DEMO_RUN_ID}/events`,
	gates_url: `/api/v1/runs/${PE_DEMO_RUN_ID}/gates`,
	sandbox_url: `/api/v1/runs/${PE_DEMO_RUN_ID}/sandbox`,
	decisions_url: `/api/v1/runs/${PE_DEMO_RUN_ID}/decisions`,
};

/**
 * Complete Private Equity `VaultObservabilityEventDTO` stream covering `WB-00` through `WB-11`.
 */
export const PE_DEMO_EVENTS: readonly VaultObservabilityEventDTO[] = [
	// 1. WB-00: Master Pipeline Command Center & 6-Gate Scoreboard
	{
		event_id: "evt-pe-000",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b0",
		parent_span_id: null,
		timestamp_utc: "2026-10-30T09:00:00Z",
		stage_id: "STAGE_00_ORCHESTRATION",
		phase_id: "P1_INGESTION",
		step_id: "VIZ-PIPE-00",
		contract_version: "6.0",
		workbench_view_id: "WB-00",
		viz_id: "VIZ-PIPE-00",
		agent_persona: "vault-first-mate",
		status: "PASS",
		rule_ids_evaluated: ["DV-STR-001", "KEY-01", "EVO-01"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Apex Horizon PE Vault: 5 Sources Onboarded, 67 Bronze Columns Audited (100%), 2 Self-Healed Exceptions, 1 Captain Hold.",
			business_impact:
				"All 4 Hubs, 5 Links, and 11 Satellites verified across 99 formal invariants; 4-Zone DuckDB SQL compiled with zero drift loss.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				sources_onboarded: 5,
				bronze_columns_audited: 67,
				column_coverage_pct: 100.0,
				hubs_count: 4,
				links_count: 5,
				satellites_count: 11,
				checks_passed: 99,
				self_healed_exceptions: 2,
				captain_holds_active: 1,
			},
			gate_scorecard: PE_DEMO_RUN.gate_scorecard,
		},
		layer3_formal_proof: {
			algorithm_ref: "Algorithm A.8 (Closed-Loop 6-Gate Orchestrator)",
			formula_latex:
				"\\forall c \\in C_{\\text{bronze}}: |\\text{triage}(c)| = 1 \\land K_{\\text{retry}} \\le 3",
		},
	},

	// 2. WB-01: Dual-Graph Grounding & Anti-Pattern Counterfactual Inspector
	{
		event_id: "evt-pe-001",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b1",
		parent_span_id: "00f067aa0ba902b0",
		timestamp_utc: "2026-10-30T09:00:02Z",
		stage_id: "STAGE_00_ORCHESTRATION",
		phase_id: "P1_INGESTION",
		step_id: "VIZ-STEP-01",
		contract_version: "6.0",
		confidence: 1.0,
		workbench_view_id: "WB-01",
		viz_id: "VIZ-STEP-01",
		agent_persona: "vault-first-mate",
		status: "PASS",
		rule_ids_evaluated: ["DV-STR-001", "KEY-04", "AP-11"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Grounded 9 Bronze tables across 5 PE systems onto 4 Enterprise Business Concepts (80.0% Ontology Coverage; 0 Unmapped Tables).",
			business_impact:
				"investran.src_lp_investors and legacy_gp.src_legacy_lp_roster converge on urn:ontology:entity:limited_partner, preventing siloed Hubs.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
			],
			summary_metrics: {
				ontology_concepts_active: 4,
				ontology_concepts_total: 5,
				ontology_coverage_pct: 80.0,
				unmapped_tables: 0,
				phantom_hubs_created: 0,
			},
			bipartite_mappings: [
				{
					cbc_uri: "urn:ontology:entity:fund",
					target_hub: "hub_fund",
					bronze_tables: ["investran.src_pe_funds"],
				},
				{
					cbc_uri: "urn:ontology:entity:limited_partner",
					target_hub: "hub_limited_partner",
					bronze_tables: [
						"investran.src_lp_investors",
						"legacy_gp.src_legacy_lp_roster",
					],
				},
				{
					cbc_uri: "urn:ontology:entity:portfolio_company",
					target_hub: "hub_portfolio_company",
					bronze_tables: ["dealcloud.src_portfolio_companies"],
				},
				{
					cbc_uri: "urn:ontology:entity:document",
					target_hub: "hub_document",
					bronze_tables: [
						"dms.documents",
						"dms.src_side_letter_extractions",
						"dms.src_ic_memo_extractions",
					],
				},
			],
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Dual-Graph Bipartite Concept Grounding (Graph A <-> Graph B)",
			formula_latex:
				"M \\subseteq V_A \\times V_B,\\quad |V_A^{\\text{active}}| / |V_A| = 4/5 = 0.80",
		},
	},

	// 3. WB-02: Empirical Column Profiler & Candidate Discovery Matrix
	{
		event_id: "evt-pe-002",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b2",
		parent_span_id: "00f067aa0ba902b0",
		timestamp_utc: "2026-10-30T09:00:04Z",
		stage_id: "STAGE_01_SCOUT_SOURCE",
		phase_id: "P2_PROFILING",
		step_id: "VIZ-STEP-02",
		contract_version: "6.0",
		confidence: 0.99,
		workbench_view_id: "WB-02",
		viz_id: "VIZ-STEP-02",
		agent_persona: "scout-source",
		status: "PASS",
		rule_ids_evaluated: ["KEY-01", "ALG-A4"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Profiled 44 relational columns: 8 Key candidates, 1 Dropped Surrogate (row_seq_id), 1 Optional FK (28.6% non-null), 2 Ref Codes.",
			business_impact:
				"Detects monotonic surrogate row_seq_id (Delta=1) and sparse co_invest_portco_lei (71.4% NULL) empirically before modelling begins.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				relational_columns_profiled: 44,
				business_key_candidates: 8,
				surrogates_quarantined: 1,
				optional_fks_detected: 1,
				optional_fk_non_null_ratio: 0.2857,
				ref_code_columns: 2,
			},
			quarantined_surrogate_column: "investran.src_lp_investors.row_seq_id",
			sparse_fk_column:
				"investran.src_lp_commitments_and_calls.co_invest_portco_lei",
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Graph B Information-Theoretic Profiler (U(C), U_nn(C), H_norm(C), I(A.x <= B.y))",
			formula_latex:
				"U_{nn}(\\text{co\\_invest\\_portco\\_lei}) = 1 - N_{\\text{null}} = 2/7 \\approx 0.2857 < 0.95",
		},
	},

	// 4. WB-09: Intra-Source Schema Drift Radar (S1(t) -> S1(t+1))
	{
		event_id: "evt-pe-003",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b3",
		parent_span_id: "00f067aa0ba902b2",
		timestamp_utc: "2026-10-30T09:00:06Z",
		stage_id: "STAGE_01_SCOUT_SOURCE",
		phase_id: "P2_PROFILING",
		step_id: "VIZ-STEP-03",
		contract_version: "6.0",
		confidence: 0.99,
		workbench_view_id: "WB-09",
		viz_id: "VIZ-STEP-03",
		agent_persona: "scout-source",
		status: "AUTO-FIX",
		rule_ids_evaluated: ["ALG-A9A", "EVO-01", "EVO-02", "EVO-03", "EVO-04"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Classified 4 Day-3 Schema Drift mutations across Investran & DealCloud without invalidating historical Satellite hash_diff digests.",
			business_impact:
				"Routes ilpa_esg_rating to companion sat_fund_investran_esg and absorbs deprecated_fax_no & committed_amt rename in Staging.",
			verdict_pill: "AUTO-FIX",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				additive_columns: 1,
				soft_deprecated_columns: 1,
				renamed_columns: 1,
				type_widened_columns: 1,
				key_drift_alerts: 0,
				historical_hash_diffs_broken: 0,
			},
			drift_items: [
				{
					table: "investran.src_pe_funds",
					column: "ilpa_esg_rating",
					category: "ADDITIVE",
					resolution:
						"Routed to sat_fund_investran_esg (ADDITIVE_DRIFT_COMPANION)",
				},
				{
					table: "investran.src_pe_funds",
					column: "deprecated_fax_no",
					category: "SOFT_DEPRECATED",
					resolution:
						"CAST(NULL AS VARCHAR(64)) AS deprecated_fax_no in Staging",
				},
				{
					table: "investran.src_lp_commitments_and_calls",
					column: "committed_amt -> commitment_amount_usd",
					category: "RENAMED",
					resolution: "Aliased in Staging (J_val = 0.994 >= 0.90)",
				},
				{
					table: "dealcloud.src_portfolio_companies",
					column: "company_name",
					category: "TYPE_WIDENED",
					resolution: "VARCHAR(128) -> VARCHAR(255) absorbed without reload",
				},
			],
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.9a (Intra-Source Schema Drift Classifier & Codemod)",
			formula_latex:
				"J_{\\text{val}}(\\text{committed\\_amt}, \\text{commitment\\_amount\\_usd}) = 0.994 \\ge 0.90",
		},
	},

	// 5. WB-03: Unstructured PDF Span-Grounding & 3-Tier ER Inspector (with Tier-U3 Captain Hold)
	{
		event_id: "evt-pe-004",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b4",
		parent_span_id: "00f067aa0ba902b0",
		timestamp_utc: "2026-10-30T09:00:08Z",
		stage_id: "STAGE_02_SCOUT_UNSTRUCTURED",
		phase_id: "P3_EXTRACTION",
		step_id: "VIZ-STEP-04",
		contract_version: "6.0",
		confidence: 0.98,
		workbench_view_id: "WB-03",
		viz_id: "VIZ-STEP-04",
		agent_persona: "scout-unstructured",
		status: "ASK-USER",
		rule_ids_evaluated: ["ALG-A9B", "U1", "U2", "U3", "TMP-01"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "CONFIDENTIAL",
		layer1_story_banner: {
			headline:
				"Extracted 17 spans from 2 PE PDFs: 2 LEIs bound (Tier-U1); 'Horizon Sovereign Co-Invest Vehicle' held for Captain (Tier-U3).",
			business_impact:
				"Prevents hallucinated Fund Hub keys from unverified PDF text (margin 0.02 < 0.15); awaits steward confirmation.",
			verdict_pill: "ASK-USER",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_SIDE_LETTER_PDF,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_IC_MEMO_PDF,
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				documents_processed: 2,
				spans_extracted: 17,
				tier_u1_bound: 3,
				tier_u2_bound: 3,
				tier_u3_held: 1,
				top2_margin_held: 0.02,
			},
			captain_hold: {
				hold_id: "hold-tier-u3-horizon-sovereign-vehicle",
				mention_text: "Horizon Sovereign Co-Invest Vehicle",
				span_start: 1112,
				span_end: 1147,
				candidates: [
					{ candidate_id: "APEX_CO_I", score: 0.81 },
					{ candidate_id: "APEX_BO_IV", score: 0.79 },
				],
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.9b (Substring Span Verifier & 3-Tier Unstructured ER)",
			formula_latex:
				"\\Delta S_{\\text{top2}} = |0.81 - 0.79| = 0.02 < 0.15 \\implies \\text{Tier-U3 Quarantine (ASK-USER)}",
		},
	},

	// 6. WB-04: Pass 1 Exhaustive Column Triage Sankey & Surrogate Sink
	{
		event_id: "evt-pe-005",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b5",
		parent_span_id: "00f067aa0ba902b0",
		timestamp_utc: "2026-10-30T09:00:12Z",
		stage_id: "STAGE_03_SHIP_VAULT_PASS1",
		phase_id: "P4_SYNTHESIS_PASS1",
		step_id: "VIZ-STEP-05",
		contract_version: "6.0",
		confidence: 1.0,
		workbench_view_id: "WB-04",
		viz_id: "VIZ-STEP-05",
		agent_persona: "ship-vault",
		status: "PASS",
		rule_ids_evaluated: ["DV-COL-001", "KEY-01"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Triaged 67/67 Bronze & Extraction columns (100% Accountability); tombstoned investran.src_lp_investors.row_seq_id as surrogate.",
			business_impact:
				"Guarantees zero silent column drops across 9 tables while blocking sequence surrogate pollution of hub_limited_partner.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
			],
			summary_metrics: {
				total_bronze_columns: 67,
				triaged_columns: 67,
				accountability_pct: 100.0,
				business_keys: 5,
				foreign_key_refs: 8,
				optional_fk_refs: 1,
				degenerate_keys: 1,
				descriptive_payload: 48,
				reference_codes: 2,
				technical_metadata: 1,
				dropped_surrogates: 1,
			},
		},
		layer3_formal_proof: {
			algorithm_ref: "Gate 1 Exhaustive Column Triage Ledger Bijection Check",
			formula_latex:
				"\\forall (T, c) \\in \\mathcal{B}: \\exists! e \\in \\text{column\\_triage\\_ledger}: (e.\\text{bronze\\_table}, e.\\text{column\\_name}) = (T, c)",
		},
	},

	// 7. WB-05: Pass 2/3 ELM Topology & 3-Tier BKCC Collision Shield
	{
		event_id: "evt-pe-006",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b6",
		parent_span_id: "00f067aa0ba902b5",
		timestamp_utc: "2026-10-30T09:00:16Z",
		stage_id: "STAGE_04_SHIP_VAULT_PASS2",
		phase_id: "P5_SYNTHESIS_PASS2",
		step_id: "VIZ-STEP-06",
		contract_version: "6.0",
		confidence: 1.0,
		workbench_view_id: "WB-05",
		viz_id: "VIZ-STEP-06",
		agent_persona: "ship-vault",
		status: "PASS",
		rule_ids_evaluated: ["KEY-04", "DV-COL-012", "EVO-05", "EVO-07"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "CONFIDENTIAL",
		layer1_story_banner: {
			headline:
				"Synthesized 4 Hubs, 5 Links, 11 Satellites; BKCC shield isolates Bavarian Pension (LEGACY_GP_ADMIN:LP-101) from CalPERS (GLOBAL:LP-101).",
			business_impact:
				"Prevents cross-source Hub key collision between $500M CalPERS and EUR 25M Bavarian Pension while unifying Nordic Sovereign via SAL.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101,
				PE_CANONICAL_HASH_PILLS.NORDIC_SOVEREIGN_LP_104,
				PE_CANONICAL_HASH_PILLS.NORDISK_FOND_LP_102,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				hubs_count: 4,
				links_count: 5,
				satellites_count: 11,
				pii_satellites_isolated: 1,
				mnpi_satellites_isolated: 1,
				cross_source_collisions_prevented: 2,
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.4 (3-Tier BK Authority & BKCC Collision Shield)",
			formula_latex:
				"\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101}) \\ne \\text{SHA256}(\\texttt{15:LEGACY\\_GP\\_ADMIN||6:LP-101})",
		},
	},

	// 8. WB-06: Gate 3 Topological DAG & Grain Matrix Inspector
	{
		event_id: "evt-pe-007",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b7",
		parent_span_id: "00f067aa0ba902b6",
		timestamp_utc: "2026-10-30T09:00:20Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		phase_id: "P6_VERIFICATION",
		gate_id: "G3",
		rule_code: "DV-STR-001",
		contract_version: "6.0",
		workbench_view_id: "WB-06",
		viz_id: "VIZ-GATE-03",
		agent_persona: "axi-dv",
		status: "PASS",
		rule_ids_evaluated: [
			"DV-STR-001",
			"DV-STR-002",
			"DV-STR-003",
			"DV-STR-005",
			"INV-01",
			"INV-02",
			"INV-03",
		],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Gate 3 NetworkX Bipartite DAG Proof: 14/14 structural invariants passed across 4 Hubs, 5 Links, and 11 Satellites.",
			business_impact:
				"Proves zero Hub-to-Hub edges, Link arity >= 2, single-parent Satellite attachment, and strict DAG acyclicity.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.CALPERS_FUND_IV_COMMITMENT_LHK,
			],
			summary_metrics: {
				structural_rules_evaluated: 14,
				structural_rules_passed: 14,
				hub_outdegree_max: 0,
				link_arity_min: 2,
				satellite_outdegree_exact: 1,
				dag_cycles_detected: 0,
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Gate 3 NetworkX Bipartite Topological Verifier (DV-STR-001..014)",
			formula_latex:
				"\\forall h \\in H: \\text{outdeg}(h)=0,\\quad \\forall l \\in L: \\text{outdeg}_H(l) \\ge 2,\\quad \\forall s \\in S: \\text{outdeg}(s)=1",
		},
	},

	// 9. WB-07: Nullable-FK Link Splitter & Gate 4 YAGNI Cost Guard
	{
		event_id: "evt-pe-008",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b8",
		parent_span_id: "00f067aa0ba902b6",
		timestamp_utc: "2026-10-30T09:00:24Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		phase_id: "P6_VERIFICATION",
		gate_id: "G4",
		rule_code: "ALG-A1",
		contract_version: "6.0",
		workbench_view_id: "WB-07",
		viz_id: "VIZ-GATE-05",
		agent_persona: "axi-dv",
		status: "AUTO-FIX",
		rule_ids_evaluated: ["ALG-A1", "ALG-A4"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Merged 3 FX/NAV ticker columns into sat_lp_commitment_investran (4.20 MB/day < 50 MB/day) and split sparse co_invest_portco_lei Link.",
			business_impact:
				"Eliminates an unnecessary PIT join (91.6% below 50 MB/day write-amplification budget) and prevents 71.4% Zero-Key pollution.",
			verdict_pill: "AUTO-FIX",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
			],
			summary_metrics: {
				general_column_count: 6,
				min_split_column_threshold: 10,
				write_amplification_mb_per_day: 4.2,
				write_amplification_threshold_mb_per_day: 50.0,
				pit_joins_saved: 1,
				sparse_fk_non_null_ratio: 0.2857,
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.1 (Gate 4 YAGNI Cost Guard) & Algorithm A.4 (Link Splitter)",
			formula_latex:
				"W_{\\text{amp}} = R_{\\text{daily}} \\times P(\\Delta C_{\\text{high}}) \\times B_{\\text{low}} = 157,000 \\times 0.85 \\times 33\\text{ B} = 4.20\\text{ MB/day} < 50\\text{ MB/day}",
			contract_yaml_diff:
				"- name: sat_lp_commitment_fx_high_freq\n+ # Merged into sat_lp_commitment_investran via Gate 4 auto-fix",
		},
	},

	// 10. WB-08 (Event A): Gate 1 REJECT-WITH-TRACE (Attempt 1)
	{
		event_id: "evt-pe-009",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902b9",
		parent_span_id: "00f067aa0ba902b6",
		timestamp_utc: "2026-10-30T09:00:26Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		phase_id: "P6_VERIFICATION",
		gate_id: "G1",
		rule_code: "DV-COL-012",
		contract_version: "6.0",
		workbench_view_id: "WB-08",
		viz_id: "VIZ-GATE-07",
		agent_persona: "axi-dv",
		status: "REJECT-WITH-TRACE",
		rule_ids_evaluated: ["DV-COL-012", "KEY-04", "ALG-A1"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Attempt 1 Blocked: legacy_gp.src_legacy_lp_roster.lp_num ('LP-101') lacks BKCC qualifier; emitted 48-token @toon_v1 trace.",
			business_impact:
				"Prevents unscoped Legacy GP local code 'LP-101' from colliding with CalPERS on hub_limited_partner.",
			verdict_pill: "REJECT-WITH-TRACE",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101,
			],
			summary_metrics: {
				attempt_number: 1,
				max_attempts: 3,
				violations_count: 2,
				toon_token_count: 48,
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.8 (@toon_v1 Closed-Loop Diagnostic Serializer)",
			toon_diagnostic_payload:
				'@toon_v1\nrun_id: run-pe-apex-horizon-20261030\nattempt: 1/3\nstatus: REJECT-WITH-TRACE\nviolations[2]{gate,rule,action,target,fix}:\n G1,DV-COL-012,REJECT,hub_limited_partner,"Set require_src_qualifier=true & bkcc_prefix=\'LEGACY_GP_ADMIN\' on lp_num"\n G4,ALG-A1,AUTO-FIX,sat_lp_comm_fx,"Merged 3 cols into sat_lp_commitment_inv (W_amp=4.20 MB/d < 50.0 MB/d)"',
		},
	},

	// 11. WB-08 (Event B): Closed-Loop Self-Healing Convergence (Attempt 2 -> AUTO_REPAIRED)
	{
		event_id: "evt-pe-010",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902ba",
		parent_span_id: "00f067aa0ba902b9",
		timestamp_utc: "2026-10-30T09:00:30Z",
		stage_id: "STAGE_05_AXI_DV_STATIC_GATES",
		phase_id: "P6_VERIFICATION",
		gate_id: "G1",
		rule_code: "DV-COL-012",
		contract_version: "6.0",
		workbench_view_id: "WB-08",
		viz_id: "VIZ-GATE-07",
		agent_persona: "ship-vault",
		status: "AUTO_REPAIRED",
		remediation: {
			codemod_type: "enforce_tier3_bkcc_prefix",
			target_entity: "hub_limited_partner",
			patch_diff:
				'- require_src_qualifier: false\n- bkcc_prefix: null\n+ require_src_qualifier: true\n+ bkcc_prefix: "LEGACY_GP_ADMIN"',
			action_description:
				"Scoped legacy_gp.src_legacy_lp_roster.lp_num under BKCC=LEGACY_GP_ADMIN on Attempt 2.",
		},
		rule_ids_evaluated: ["DV-COL-012", "KEY-04"],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "INTERNAL",
		layer1_story_banner: {
			headline:
				"Self-Healed in Attempt 2 (k=2 <= 3): ship-vault applied BKCC='LEGACY_GP_ADMIN' on lp_num; all static gates now pass.",
			business_impact:
				"Closed-loop @toon_v1 feedback repaired the Tier-3 key collision deterministically without human intervention.",
			verdict_pill: "AUTO_REPAIRED",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101,
			],
			summary_metrics: {
				attempt_number: 2,
				max_attempts: 3,
				remaining_violations: 0,
				converged: true,
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.4 + Algorithm A.8 Closed-Loop Repair (k=1 -> k=2)",
			contract_yaml_diff:
				'  - bronze_table: "legacy_gp.src_legacy_lp_roster"\n    key_columns: ["lp_num"]\n    key_authority_tier: "TIER_3_LOCAL_SCOPED"\n-   require_src_qualifier: false\n-   bkcc_prefix: null\n+   require_src_qualifier: true\n+   bkcc_prefix: "LEGACY_GP_ADMIN"',
			toon_diagnostic_payload:
				"@toon_v1\nrun_id: run-pe-apex-horizon-20261030\nattempt: 2/3\nstatus: PASS\nviolations[0]{gate,rule,action,target,fix}:",
		},
	},

	// 12. WB-10: Gate 5 Dynamic DuckDB Shadow Sandbox & Evolution Matrix
	{
		event_id: "evt-pe-011",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902bb",
		parent_span_id: "00f067aa0ba902ba",
		timestamp_utc: "2026-10-30T09:00:36Z",
		stage_id: "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
		phase_id: "P7_DYNAMIC_SANDBOX",
		contract_version: "6.0",
		workbench_view_id: "WB-10",
		viz_id: "VIZ-GATE-08",
		agent_persona: "axi-dv",
		status: "PASS",
		rule_ids_evaluated: [
			"DV-DYN-001",
			"DV-DYN-006",
			"DV-DYN-007",
			"DV-DYN-008",
			"DV-DYN-009",
			"DV-DYN-012",
			"EVO-01",
		],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "CONFIDENTIAL",
		layer1_story_banner: {
			headline:
				"Executed 4 Adversarial Batches in DuckDB Shadow Sandbox: 15/15 dynamic checks passed, 0 orphans, Batch 2 replay delta = 0.",
			business_impact:
				"Proves idempotency, Ghost Record INNER JOIN completeness, boundary-shift collision immunity, and bi-temporal cutoff integrity.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.SYSTEM_GHOST,
				PE_CANONICAL_HASH_PILLS.SYSTEM_ZERO_KEY_UNKNOWN,
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.BAVARIAN_PENSION_LP_101,
			],
			summary_metrics: {
				batches_executed: 4,
				batches_passed: 4,
				dynamic_checks_total: 15,
				dynamic_checks_passed: 15,
				orphan_fk_count: 0,
				batch2_replay_delta_rows: 0,
				ghost_records_per_satellite: 1,
			},
			batch_verdicts: {
				batch_1_initial_and_ghost_seed: "PASS",
				batch_2_idempotent_replay: "PASS",
				batch_3_single_delta_and_drift: "PASS",
				batch_4_multi_source_collision: "PASS",
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Gate 5 4-Batch Adversarial DuckDB Shadow Sandbox (DV-DYN-001..015)",
			formula_latex:
				"\\Delta\\text{rows}(t_1) = |\\text{Vault}(t_1)| - |\\text{Vault}(t_0)| = 0,\\quad |\\text{Orphans}| = 0",
		},
	},

	// 13. WB-11: Pre-Hash Stepper (Alg A.5) & 4-Zone SQL Rosetta Stone
	{
		event_id: "evt-pe-012",
		run_id: PE_DEMO_RUN_ID,
		trace_id: PE_DEMO_TRACE_ID,
		span_id: "00f067aa0ba902bc",
		parent_span_id: "00f067aa0ba902bb",
		timestamp_utc: "2026-10-30T09:00:42Z",
		stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
		phase_id: "P8_OPERATIONS",
		step_id: "VIZ-STEP-07",
		contract_version: "6.0",
		workbench_view_id: "WB-11",
		viz_id: "VIZ-STEP-07",
		agent_persona: "deterministic-compiler",
		status: "PASS",
		rule_ids_evaluated: [
			"ALG-A5",
			"ALG-A6",
			"DV-AST-001",
			"DV-AST-002",
			"DV-AST-003",
			"TMP-01",
			"TMP-02",
		],
		context_refs: PE_DEMO_CONTEXT_REFS,
		data_classification: "MNPI",
		layer1_story_banner: {
			headline:
				"Compiled 4-Zone DuckDB SQL & Bi-Temporal Queries D.10/D.10b: Nordic Cloud EV restated 12.80x -> 14.50x ($245M) with zero leakage.",
			business_impact:
				"Links CalPERS 125 bps side-letter fee cap ([38:1012]) and Q3 IC valuation memo ([108:1151]) with point-in-time audit reproducibility.",
			verdict_pill: "PASS",
		},
		layer2_visual_payload: {
			semantic_hash_pills: [
				PE_CANONICAL_HASH_PILLS.CALPERS_LP_101,
				PE_CANONICAL_HASH_PILLS.FUND_IV_APEX_BO_IV,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_PORTCO,
				PE_CANONICAL_HASH_PILLS.CALPERS_SIDE_LETTER_PDF,
				PE_CANONICAL_HASH_PILLS.NORDIC_CLOUD_IC_MEMO_PDF,
			],
			summary_metrics: {
				sql_zones_compiled: 4,
				ast_rules_passed: 6,
				calpers_fee_cap_bps: 125.0,
				nordic_cloud_oct15_multiple: 12.8,
				nordic_cloud_nov20_multiple: 14.5,
				nordic_cloud_nov20_ev_usd: 245000000.0,
			},
			prehash_stepper_example: {
				raw_tokens: ["GLOBAL", "LP-101"],
				canonical_prehash: "6:GLOBAL||6:LP-101",
				sha256_hex:
					"5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
				pill_label: "[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]",
			},
		},
		layer3_formal_proof: {
			algorithm_ref:
				"Algorithm A.5 (Length-Prefixed SHA-256) & Query D.10 / D.10b (Bi-Temporal PIT)",
			formula_latex:
				"\\text{HK} = \\text{UPPER}(\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101})) = \\texttt{5D15EB10...4B820AE4}",
			sqlglot_ast_summary:
				"Zone 0 Schemas -> Zone 1 Hashed Staging Views (Algorithm A.5) -> Zone 2 Raw Vault DDL/DML + Ghost Seed -> Zone 3 Business Vault AI Satellites & Logarithmic PIT -> Zone 4 Bi-Temporal Queries D.10 & D.10b",
		},
	},
];

/**
 * Pre-serialized `.jsonl` representation of `PE_DEMO_EVENTS` for `MockVaultEventStreamClient.loadFromJSONL`.
 */
export const PE_DEMO_EVENTS_JSONL: string = PE_DEMO_EVENTS.map((ev) =>
	JSON.stringify(ev),
).join("\n");

/**
 * Create a fresh `MockVaultEventStreamClient` preloaded with the complete Private Equity demo event stream.
 */
export function createPeDemoReplayClient(): MockVaultEventStreamClient {
	return new MockVaultEventStreamClient(PE_DEMO_EVENTS);
}
