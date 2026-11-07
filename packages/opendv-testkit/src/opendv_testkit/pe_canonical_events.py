"""Canonical Private Equity Demo events and constants for test suites."""

from __future__ import annotations

from typing import Any

from opendv_core import VaultObservabilityEvent, validate_observability_event
from opendv_telemetry import (
    format_error_key_pill,
    format_ghost_pill,
    format_hub_pill,
    format_link_pill,
    format_zero_key_pill,
    validate_no_pii_leaks,
)

PE_DEMO_CONTEXT_REFS: dict[str, str] = {
    "graph_a_sha256": "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90",
    "graph_b_sha256": "B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1",
    "contract_yaml_pre_sha256": "C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2",
    "contract_yaml_post_sha256": "D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3",
    "canonical_ir_sha256": "E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4",
    "compiled_sql_sha256": "F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5",
    "toon_trace_sha256": "0718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F6",
}


def build_pe_canonical_events(
    run_id: str = "run-pe-apex-horizon-20261030",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Construct the 9 canonical PE VaultObservabilityEvent dicts (`evt-pe-001`..`evt-pe-009`)
    plus the full projection events.
    """
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"

    pill_calpers = format_hub_pill(
        "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
        "GLOBAL",
        "LP-101",
        "CalPERS",
    )
    pill_fund_iv = format_hub_pill(
        "94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
        "GLOBAL",
        "APEX_BO_IV",
        "Fund IV",
    )
    pill_bavarian = format_hub_pill(
        "6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
        "LEGACY_GP_ADMIN",
        "LP-101",
        "Bavarian Pension",
    )
    pill_nordic_cloud = format_hub_pill(
        "0C1850CAC2E686A1F9233FA223F3986DC0949963DE1A32DFFDB25E1139B0454E",
        "GLOBAL",
        "529900T8BM49AURSDO55",
        "Nordic Cloud",
    )
    pill_comm_lhk = format_link_pill(
        "EB156109CC1A7C9A609169970B396D095950BD2309527D229017AD0D357A7C4E",
        "Fund IV <-> CalPERS",
        "GLOBAL",
    )
    pill_side_letter = format_hub_pill(
        "864D5083723B9FD7FE69085C2C31B646BD5E3DEAEF61B64C08D545EDE502EE6B",
        "GLOBAL",
        "calpers_side_letter_fund_iv_2026.pdf",
        "CalPERS Side Letter",
    )
    pill_ic_memo = format_hub_pill(
        "99D8178E34640A825C99AC0D343A43DA6D3F6EE5C9A413386801492D88F04158",
        "GLOBAL",
        "ic_q3_valuation_nordic_cloud.pdf",
        "Nordic Cloud Q3 IC Memo",
    )
    pill_ghost = format_ghost_pill()
    pill_zero_uk = format_zero_key_pill()
    pill_zero_err = format_error_key_pill()

    evt_001: dict[str, Any] = {
        "event_id": "evt-pe-001",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b1",
        "parent_span_id": "00f067aa0ba902b0",
        "timestamp_utc": "2026-10-30T09:00:02Z",
        "stage_id": "STAGE_00_ORCHESTRATION",
        "phase_id": "P1_INGESTION",
        "step_id": "STEP-01",
        "contract_version": "6.0",
        "confidence": 1.0,
        "agent_persona": "vault-first-mate",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "KEY-04", "AP-11"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Grounded 9 Bronze tables across 5 PE systems onto 4 Enterprise "
                "Business Concepts (80.0% Ontology Coverage)."
            ),
            "business_impact": (
                "investran.src_lp_investors and legacy_gp.src_legacy_lp_roster "
                "converge on LimitedPartner, preventing siloed Hubs."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv],
            "summary_metrics": {
                "ontology_concepts_active": 4,
                "ontology_concepts_total": 5,
                "ontology_coverage_pct": 80.0,
                "unmapped_tables": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Dual-Graph Bipartite Concept Grounding (Graph A <-> Graph B)",
            "formula_latex": (
                "M \\subseteq V_A \\times V_B,\\quad |V_A^{\\text{active}}| / |V_A| = 4/5 = 0.80"
            ),
        },
    }

    evt_002: dict[str, Any] = {
        "event_id": "evt-pe-002",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b2",
        "parent_span_id": "00f067aa0ba902b1",
        "timestamp_utc": "2026-10-30T09:00:04Z",
        "stage_id": "STAGE_01_SCOUT_SOURCE",
        "phase_id": "P2_PROFILING",
        "step_id": "STEP-02",
        "contract_version": "6.0",
        "confidence": 0.99,
        "agent_persona": "scout-source",
        "status": "PASS",
        "rule_ids_evaluated": ["KEY-01", "ALG-A4"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Profiled 44 relational columns: 8 Key candidates, 1 Dropped "
                "Surrogate (row_seq_id), 1 Optional FK (28.6% non-null)."
            ),
            "business_impact": (
                "Detects monotonic surrogate row_seq_id (Delta=1) and sparse "
                "co_invest_portco_lei (71.4% NULL) before modelling."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "relational_columns_profiled": 44,
                "business_key_candidates": 8,
                "surrogates_quarantined": 1,
                "optional_fk_non_null_ratio": 0.2857,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Graph B Information-Theoretic Profiler (U(C), U_nn(C), H_norm(C))",
            "formula_latex": (
                "U_{nn}(\\text{co\\_invest\\_portco\\_lei}) = "
                "1 - N_{\\text{null}} = 2/7 \\approx 0.2857 < 0.95"
            ),
        },
    }

    evt_003: dict[str, Any] = {
        "event_id": "evt-pe-003",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b3",
        "parent_span_id": "00f067aa0ba902b2",
        "timestamp_utc": "2026-10-30T09:00:06Z",
        "stage_id": "STAGE_01_SCOUT_SOURCE",
        "phase_id": "P2_PROFILING",
        "step_id": "STEP-03",
        "contract_version": "6.0",
        "confidence": 0.99,
        "agent_persona": "scout-source",
        "status": "AUTO-FIX",
        "rule_ids_evaluated": ["ALG-A9A", "EVO-01", "EVO-02", "EVO-03", "EVO-04"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Classified 4 Day-3 Schema Drift mutations across Investran & "
                "DealCloud with zero historical hash_diff rewrites."
            ),
            "business_impact": (
                "Routes ilpa_esg_rating to sat_fund_investran_esg and absorbs "
                "deprecated_fax_no & committed_amt rename in Staging."
            ),
            "verdict_pill": "AUTO-FIX",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv, pill_nordic_cloud],
            "summary_metrics": {
                "additive_columns": 1,
                "soft_deprecated_columns": 1,
                "renamed_columns": 1,
                "type_widened_columns": 1,
                "historical_hash_diffs_broken": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.9a (Intra-Source Schema Drift Classifier & Codemod)",
            "formula_latex": (
                "J_{\\text{val}}(\\text{committed\\_amt}, "
                "\\text{commitment\\_amount\\_usd}) = 0.994 \\ge 0.90"
            ),
        },
    }

    evt_004: dict[str, Any] = {
        "event_id": "evt-pe-004",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b4",
        "parent_span_id": "00f067aa0ba902b3",
        "timestamp_utc": "2026-10-30T09:00:08Z",
        "stage_id": "STAGE_02_SCOUT_UNSTRUCTURED",
        "phase_id": "P3_EXTRACTION",
        "step_id": "STEP-04",
        "contract_version": "6.0",
        "confidence": 0.98,
        "agent_persona": "scout-unstructured",
        "status": "ASK-USER",
        "rule_ids_evaluated": ["ALG-A9B", "U1", "U2", "U3", "TMP-01"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Extracted 17 PDF spans: 2 LEIs bound (Tier-U1); 'Horizon Sovereign "
                "Co-Invest Vehicle' held for Captain (Tier-U3)."
            ),
            "business_impact": (
                "Prevents hallucinated Fund Hub keys from unverified PDF text "
                "(margin 0.02 < 0.15); awaits steward confirmation."
            ),
            "verdict_pill": "ASK-USER",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_side_letter, pill_ic_memo, pill_calpers],
            "summary_metrics": {
                "documents_processed": 2,
                "spans_extracted": 17,
                "tier_u1_bound": 3,
                "tier_u2_bound": 3,
                "tier_u3_held": 1,
                "top2_margin_held": 0.02,
                "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
            },
            "captain_hold": {
                "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
                "mention_text": "Horizon Sovereign Co-Invest Vehicle",
                "span_start": 1112,
                "span_end": 1147,
                "candidates": [
                    {"candidate_id": "APEX_CO_I", "score": 0.81},
                    {"candidate_id": "APEX_BO_IV", "score": 0.79},
                ],
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.9b (Substring Span Verifier & 3-Tier Unstructured ER)",
            "formula_latex": (
                "\\Delta S_{\\text{top2}} = |0.81 - 0.79| = 0.02 < 0.15 "
                "\\implies \\text{Tier-U3 Quarantine (ASK-USER)}"
            ),
        },
    }

    evt_005: dict[str, Any] = {
        "event_id": "evt-pe-005",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b5",
        "parent_span_id": "00f067aa0ba902b4",
        "timestamp_utc": "2026-10-30T09:00:12Z",
        "stage_id": "STAGE_03_SHIP_VAULT_PASS1",
        "phase_id": "P4_SYNTHESIS_PASS1",
        "step_id": "STEP-05",
        "contract_version": "6.0",
        "confidence": 1.0,
        "agent_persona": "ship-vault",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-COL-001", "KEY-01", "ALG-A2"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "RESTRICTED_PII",
        "layer1_story_banner": {
            "headline": (
                "Triaged 67/67 Bronze columns (100% Accountability) and isolated "
                "Restricted PII into sat_limited_partner_pii."
            ),
            "business_impact": (
                "Guarantees zero silent column drops and physically segregates "
                "tax_id_ein, wire_iban, and beneficial_owner_ssn."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian],
            "summary_metrics": {
                "total_bronze_columns": 67,
                "triaged_columns": 67,
                "accountability_pct": 100.0,
                "dropped_surrogates": 1,
                "pii_satellite": "sat_limited_partner_pii",
                "pii_columns_isolated": 3,
                "masked_iban_sample": "DE89****************00",
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.2 (3-Axis Satellite Split & PII Vault Isolation)",
            "formula_latex": (
                "\\forall c \\in C_{\\text{PII\\_HIGH}}: "
                "\\text{target}(c) = \\texttt{sat\\_limited\\_partner\\_pii}"
            ),
            "contract_yaml_diff": (
                "+ name: sat_limited_partner_pii\n+ security_classification: RESTRICTED_PII"
            ),
        },
    }

    evt_006: dict[str, Any] = {
        "event_id": "evt-pe-006",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b6",
        "parent_span_id": "00f067aa0ba902b5",
        "timestamp_utc": "2026-10-30T09:00:16Z",
        "stage_id": "STAGE_04_SHIP_VAULT_PASS2",
        "phase_id": "P5_SYNTHESIS_PASS2",
        "step_id": "STEP-06",
        "contract_version": "6.0",
        "confidence": 1.0,
        "agent_persona": "ship-vault",
        "status": "PASS",
        "rule_ids_evaluated": ["KEY-04", "DV-COL-012", "EVO-05", "EVO-07"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "MNPI",
        "layer1_story_banner": {
            "headline": (
                "Synthesized 4 Hubs, 5 Links, 11 Satellites; isolated Nordic Cloud "
                "14.5x EBITDA ($245M) MNPI valuation."
            ),
            "business_impact": (
                "BKCC shield isolates Bavarian Pension (LEGACY_GP_ADMIN:LP-101) "
                "from CalPERS while ring-fencing $245M MNPI."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian, pill_nordic_cloud],
            "summary_metrics": {
                "hubs_count": 4,
                "links_count": 5,
                "satellites_count": 11,
                "entry_ev_multiple": "14.5x",
                "fair_value_usd": 245000000.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.4 (3-Tier BK Authority & BKCC Collision Shield)",
            "formula_latex": (
                "\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101}) \\ne "
                "\\text{SHA256}(\\texttt{15:LEGACY\\_GP\\_ADMIN||6:LP-101})"
            ),
        },
    }

    evt_007: dict[str, Any] = {
        "event_id": "evt-pe-007",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": "00f067aa0ba902b6",
        "timestamp_utc": "2026-10-30T09:00:20Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G3",
        "rule_code": "DV-STR-001",
        "contract_version": "6.0",
        "agent_persona": "axi-dv",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "DV-STR-002", "DV-STR-003", "INV-01", "INV-02"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Gate 3 NetworkX Bipartite DAG Proof: 14/14 structural invariants "
                "passed across 4 Hubs, 5 Links, and 11 Satellites."
            ),
            "business_impact": (
                "Proves zero Hub-to-Hub edges, Link arity >= 2, single-parent "
                "Satellite attachment, and strict DAG acyclicity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_fund_iv, pill_calpers, pill_comm_lhk],
            "summary_metrics": {
                "structural_rules_evaluated": 14,
                "structural_rules_passed": 14,
                "hub_outdegree_max": 0,
                "link_arity_min": 2,
                "dag_cycles_detected": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Gate 3 NetworkX Bipartite Topological Verifier (DV-STR-001..014)",
            "formula_latex": (
                "\\forall h \\in H: \\text{outdeg}(h)=0,\\quad "
                "\\forall l \\in L: \\text{outdeg}_H(l) \\ge 2,\\quad "
                "\\forall s \\in S: \\text{outdeg}(s)=1"
            ),
        },
    }

    evt_008: dict[str, Any] = {
        "event_id": "evt-pe-008",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b8",
        "parent_span_id": "00f067aa0ba902b7",
        "timestamp_utc": "2026-10-30T09:00:30Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G1",
        "rule_code": "DV-COL-012",
        "contract_version": "6.0",
        "agent_persona": "ship-vault",
        "status": "AUTO_REPAIRED",
        "remediation": {
            "codemod_type": "enforce_tier3_bkcc_prefix",
            "target_entity": "hub_limited_partner",
            "patch_diff": (
                "- require_src_qualifier: false\n"
                "+ require_src_qualifier: true\n"
                '+ bkcc_prefix: "LEGACY_GP_ADMIN"'
            ),
            "action_description": (
                "Scoped legacy_gp.src_legacy_lp_roster.lp_num under "
                "BKCC=LEGACY_GP_ADMIN on Attempt 2."
            ),
        },
        "rule_ids_evaluated": ["DV-COL-012", "KEY-04", "ALG-A1"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "MNPI",
        "layer1_story_banner": {
            "headline": (
                "Self-Healed in Attempt 2 (k=2 <= 3): applied BKCC='LEGACY_GP_ADMIN' "
                "on lp_num and verified 14.5x ($245M) rules."
            ),
            "business_impact": (
                "Closed-loop @toon_v1 feedback repaired the Tier-3 key collision "
                "deterministically without human intervention."
            ),
            "verdict_pill": "AUTO_REPAIRED",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_bavarian],
            "summary_metrics": {
                "attempt_number": 2,
                "max_attempts": 3,
                "remaining_violations": 0,
                "converged": True,
                "entry_ev_multiple": "14.5x",
                "fair_value_usd": 245000000.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.4 + Algorithm A.8 Closed-Loop Repair (k=1 -> k=2)",
            "contract_yaml_diff": (
                '+ require_src_qualifier: true\n+ bkcc_prefix: "LEGACY_GP_ADMIN"'
            ),
            "toon_diagnostic_payload": (
                "@toon_v1\n"
                "run_id: run-pe-apex-horizon-20261030\n"
                "attempt: 2/3\n"
                "status: PASS\n"
                "violations[0]{gate,rule,action,target,fix}:"
            ),
        },
    }

    evt_009: dict[str, Any] = {
        "event_id": "evt-pe-009",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b9",
        "parent_span_id": "00f067aa0ba902b8",
        "timestamp_utc": "2026-10-30T09:00:42Z",
        "stage_id": "STAGE_07_DETERMINISTIC_COMPILER",
        "phase_id": "P8_OPERATIONS",
        "step_id": "STEP-07",
        "contract_version": "6.0",
        "agent_persona": "deterministic-compiler",
        "status": "PASS",
        "rule_ids_evaluated": ["ALG-A5", "ALG-A6", "DV-AST-001", "TMP-01", "TMP-02"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Compiled 4-Zone DuckDB SQL & Bi-Temporal Queries D.10/D.10b with "
                "zero drift loss and 100% AST compliance."
            ),
            "business_impact": (
                "Links CalPERS side-letter fee cap ([38:1012]) and Q3 IC valuation "
                "memo ([108:1151]) with point-in-time audit parity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_fund_iv, pill_nordic_cloud, pill_ghost],
            "summary_metrics": {
                "sql_zones_compiled": 4,
                "ast_rules_passed": 6,
                "calpers_fee_cap_bps": 125.0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": (
                "Algorithm A.5 (Length-Prefixed SHA-256) & Query D.10 / D.10b (Bi-Temporal PIT)"
            ),
            "formula_latex": (
                "\\text{HK} = \\text{UPPER}(\\text{SHA256}(\\texttt{6:GLOBAL||6:LP-101}))"
            ),
            "sqlglot_ast_summary": (
                "Zone 0 Schemas -> Zone 1 Hashed Staging Views -> Zone 2 Raw Vault "
                "-> Zone 3 Business Vault PIT -> Zone 4 Queries D.10 & D.10b"
            ),
        },
    }

    evt_orchestration: dict[str, Any] = {
        "event_id": "evt-pe-launch",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902b0",
        "parent_span_id": None,
        "timestamp_utc": "2026-10-30T09:00:00Z",
        "stage_id": "STAGE_00_ORCHESTRATION",
        "phase_id": "P1_INGESTION",
        "step_id": "PIPE-00",
        "contract_version": "6.0",
        "agent_persona": "vault-first-mate",
        "status": "PASS",
        "rule_ids_evaluated": ["DV-STR-001", "KEY-01", "EVO-01"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Apex Horizon PE Vault: 5 Sources Onboarded, 67 Bronze Columns "
                "Audited (100%), 2 Self-Healed Exceptions, 1 Captain Hold."
            ),
            "business_impact": (
                "All 4 Hubs, 5 Links, and 11 Satellites verified across 99 formal "
                "invariants; 4-Zone DuckDB SQL compiled."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_fund_iv, pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "sources_onboarded": 5,
                "bronze_columns_audited": 67,
                "checks_passed": 99,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.8 (Closed-Loop 6-Gate Orchestrator)",
            "formula_latex": (
                "\\forall c \\in C_{\\text{bronze}}: |\\text{triage}(c)| = 1 "
                "\\land K_{\\text{retry}} \\le 3"
            ),
        },
    }

    evt_gate4_fix: dict[str, Any] = {
        "event_id": "evt-pe-gate4-fix",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902ba",
        "parent_span_id": "00f067aa0ba902b6",
        "timestamp_utc": "2026-10-30T09:00:24Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "phase_id": "P6_VERIFICATION",
        "gate_id": "G4",
        "rule_code": "ALG-A1",
        "contract_version": "6.0",
        "agent_persona": "axi-dv",
        "status": "AUTO-FIX",
        "rule_ids_evaluated": ["ALG-A1", "ALG-A4"],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": (
                "Merged 3 FX/NAV ticker columns into sat_lp_commitment_investran "
                "(4.20 MB/day < 50 MB/day) and split sparse co_invest Link."
            ),
            "business_impact": (
                "Eliminates an unnecessary PIT join (91.6% below 50 MB/day budget) "
                "and prevents 71.4% Zero-Key pollution."
            ),
            "verdict_pill": "AUTO-FIX",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_calpers, pill_nordic_cloud],
            "summary_metrics": {
                "write_amplification_mb_per_day": 4.2,
                "write_amplification_threshold_mb_per_day": 50.0,
                "sparse_fk_non_null_ratio": 0.2857,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": (
                "Algorithm A.1 (Gate 4 YAGNI Cost Guard) & Algorithm A.4 (Link Splitter)"
            ),
            "formula_latex": "W_{\\text{amp}} = 4.20\\text{ MB/day} < 50\\text{ MB/day}",
        },
    }

    evt_sandbox_pass: dict[str, Any] = {
        "event_id": "evt-pe-sandbox-pass",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": "00f067aa0ba902bb",
        "parent_span_id": "00f067aa0ba902b8",
        "timestamp_utc": "2026-10-30T09:00:36Z",
        "stage_id": "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
        "phase_id": "P7_DYNAMIC_SANDBOX",
        "contract_version": "6.0",
        "agent_persona": "axi-dv",
        "status": "PASS",
        "rule_ids_evaluated": [
            "DV-DYN-001",
            "DV-DYN-006",
            "DV-DYN-009",
            "DV-DYN-012",
            "EVO-01",
        ],
        "context_refs": PE_DEMO_CONTEXT_REFS,
        "data_classification": "CONFIDENTIAL",
        "layer1_story_banner": {
            "headline": (
                "Executed 4 Adversarial Batches in DuckDB Shadow Sandbox: 15/15 "
                "dynamic checks passed, 0 orphans, Batch 2 replay delta = 0."
            ),
            "business_impact": (
                "Proves idempotency, Ghost Record INNER JOIN completeness, "
                "boundary-shift collision immunity, and bi-temporal cutoff integrity."
            ),
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [pill_ghost, pill_zero_uk, pill_zero_err, pill_calpers],
            "summary_metrics": {
                "batches_executed": 4,
                "batches_passed": 4,
                "dynamic_checks_total": 15,
                "dynamic_checks_passed": 15,
                "orphan_fk_count": 0,
            },
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Gate 5 4-Batch Adversarial DuckDB Shadow Sandbox (DV-DYN-001..015)",
            "formula_latex": "\\Delta\\text{rows}(t_1) = 0,\\quad |\\text{Orphans}| = 0",
        },
    }

    canonical_9 = [
        evt_001,
        evt_002,
        evt_003,
        evt_004,
        evt_005,
        evt_006,
        evt_007,
        evt_008,
        evt_009,
    ]
    all_events = [evt_orchestration, *canonical_9, evt_gate4_fix, evt_sandbox_pass]

    for ev in all_events:
        VaultObservabilityEvent.model_validate(ev)
        validate_observability_event(ev)
        validate_no_pii_leaks(ev)

    return canonical_9, all_events
