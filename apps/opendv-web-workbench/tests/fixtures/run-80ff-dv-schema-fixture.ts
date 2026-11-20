/**
 * Real `run-80ff425c8100`-shaped DV Schema Projection fixture for M6 Gen2 regression tests.
 */

import type { DvSchemaProjectionDTO } from "@opendv/protocol-ts";

export function make52RawTriageRows(): Array<Record<string, unknown>> {
	const baseRows: Array<Record<string, unknown>> = [
		{
			bronze_table: "ds01_sec_treasury.currency_code",
			column_name: "currency_code",
			elm_classification: "REFERENCE_CODE",
			source_sql_type: "TEXT",
			target_dv_entity: "ref_currency_code",
		},
		{
			bronze_table: "ds01_sec_treasury.issuers",
			column_name: "cik",
			elm_classification: "BUSINESS_KEY",
			source_sql_type: "TEXT",
			target_dv_entity: "hub_issuer",
		},
		{
			bronze_table: "ds01_sec_treasury.positions",
			column_name: "position_id",
			elm_classification: "BUSINESS_KEY",
			source_sql_type: "TEXT",
			target_dv_entity: "hub_position",
		},
		{
			bronze_table: "ds01_sec_treasury.positions",
			column_name: "row_seq_sk",
			elm_classification: "SYSTEM_SURROGATE_DROPPED",
			source_sql_type: "INTEGER",
			target_dv_entity: "DROPPED_SURROGATE",
		},
		{
			bronze_table: "ds01_sec_treasury.positions",
			column_name: "intraday_margin_buffer_bps",
			elm_classification: "DESCRIPTIVE_PAYLOAD",
			source_sql_type: "INTEGER",
			target_dv_entity: "sat_positions_ext_v2",
		},
		{
			bronze_table: "ds02_w2_custody_csv.ds02_w2_custody",
			column_name: "beneficial_owner_tax_id",
			elm_classification: "PII_SENSITIVE",
			source_sql_type: "TEXT",
			target_dv_entity: "sat_ds02_w2_custody_pii",
		},
		{
			bronze_table: "ds02_account_crosswalk_csv.ds02_account_crosswalk",
			column_name: "master_account_id",
			elm_classification: "SAME_AS_LINK_KEY",
			source_sql_type: "TEXT",
			target_dv_entity: "sal_account",
		},
	];
	const out: Array<Record<string, unknown>> = [...baseRows];
	while (out.length < 52) {
		const idx = out.length + 1;
		out.push({
			bronze_table: "ds01_sec_treasury.securities",
			column_name: `attr_${idx}`,
			elm_classification: "DESCRIPTIVE_PAYLOAD",
			source_sql_type: "TEXT",
			target_dv_entity: "sat_securities",
		});
	}
	return out;
}

export const REAL_RUN_80FF_PROJECTION: DvSchemaProjectionDTO = {
	run_id: "run-80ff425c8100",
	compare_run_id: "run-4804789955aa",
	model_name: "default_vault",
	hubs: [
		{
			name: "hub_issuer",
			hk_column: "hk_issuer",
			business_keys: ["bkcc", "cik"],
			bkcc_column_expr: null,
			ontology_concept_uri: null,
			source_mappings: [
				{
					staging_table: "stg_ds01_sec_treasury_issuers",
					bk_source_columns: ["bkcc", "cik"],
					rec_src_value: "DS01_SEC_TREASURY_ISSUERS",
					bronze_table: "ds01_sec_treasury.issuers",
					key_authority_tier: "TIER_3_LOCAL_SCOPED",
					bkcc_prefix: "DS01_SEC_TREASURY_ISSUERS",
					bkcc_literal: "DS01_SEC_TREASURY_ISSUERS",
				},
			],
		},
		{
			name: "hub_position",
			hk_column: "hk_position",
			business_keys: ["bkcc", "position_id"],
			bkcc_column_expr: null,
			ontology_concept_uri: null,
			source_mappings: [
				{
					staging_table: "stg_ds01_sec_treasury_positions",
					bk_source_columns: ["bkcc", "position_id"],
					rec_src_value: "DS01_SEC_TREASURY_POSITIONS",
					bronze_table: "ds01_sec_treasury.positions",
					key_authority_tier: "TIER_3_LOCAL_SCOPED",
					bkcc_prefix: "DS01_SEC_TREASURY_POSITIONS",
					bkcc_literal: "DS01_SEC_TREASURY_POSITIONS",
				},
			],
		},
		{
			name: "hub_account",
			hk_column: "hk_account",
			business_keys: ["bkcc", "account_code"],
			bkcc_column_expr: null,
			ontology_concept_uri: null,
			source_mappings: [
				{
					staging_table:
						"stg_ds02_account_crosswalk_csv_ds02_account_crosswalk",
					bk_source_columns: ["bkcc", "account_code"],
					bronze_table: "ds02_account_crosswalk_csv.ds02_account_crosswalk",
					key_authority_tier: "TIER_3_LOCAL_SCOPED",
					bkcc_prefix: "DS02_ACCOUNT_CROSSWALK",
				},
			],
		},
	],
	links: [
		{
			name: "lnk_position_exposure_to_issuer",
			hk_column: "hk_lnk_position_exposure_to_issuer",
			link_type: "STANDARD",
			hub_references: [
				{
					fk_column_name: "hk_position",
					target_hub: "hub_position",
					target_hk_column: "hk_position",
				},
				{
					fk_column_name: "hk_issuer",
					target_hub: "hub_issuer",
					target_hk_column: "hk_issuer",
				},
			],
			degenerate_columns: [],
			where_filter_condition: null,
		},
		{
			name: "sal_account",
			hk_column: "hk_sal_account",
			link_type: "SAME_AS",
			hub_references: [
				{
					fk_column_name: "hk_account_master",
					target_hub: "hub_account",
					target_hk_column: "hk_account",
				},
				{
					fk_column_name: "hk_account_duplicate",
					target_hub: "hub_account",
					target_hk_column: "hk_account",
				},
			],
			degenerate_columns: [],
			where_filter_condition: null,
		},
	],
	satellites: [
		{
			name: "sat_positions",
			parent_entity: "hub_position",
			satellite_type: "STANDARD",
			hash_diff_column: "hash_diff",
			hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
			payload_columns: [{ name: "market_value", data_type: "VARCHAR(255)" }],
			soft_deprecated_columns: ["legacy_desk_memo"],
		},
		{
			name: "sat_positions_ext_v2",
			parent_entity: "hub_position",
			satellite_type: "STANDARD",
			hash_diff_column: "hash_diff",
			hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
			payload_columns: [
				{ name: "custodian_sla_tier", data_type: "VARCHAR(255)" },
				{ name: "intraday_margin_buffer_bps", data_type: "INTEGER" },
			],
			soft_deprecated_columns: [],
		},
		{
			name: "esat_sal_account",
			parent_entity: "sal_account",
			satellite_type: "EFFECTIVITY",
			hash_diff_column: "hash_diff",
			hash_diff_migration_policy: "SPLIT_NEW_SATELLITE",
			payload_columns: [
				{ name: "crosswalk_id", data_type: "VARCHAR(255)" },
				{ name: "effective_date", data_type: "DATE" },
			],
			soft_deprecated_columns: [],
		},
	],
	reference_tables: [
		{
			name: "ref_currency_code",
			natural_code_column: "currency_code",
			description_columns: [],
		},
	],
	pit_tables: [
		{
			name: "pit_position",
			target_hub: "",
			parent_entity: "hub_position",
			tracked_satellites: ["sat_positions", "sat_positions_ext_v2"],
		} as DvSchemaProjectionDTO["pit_tables"][number] & {
			parent_entity?: string;
		},
	],
	column_triage_ledger: make52RawTriageRows(),
	scorecard: {
		gate_0_profile_drift: "PASS",
		gate_1_contract_grain: "PASS",
		gate_2_sqlglot_ast: "PASS",
		gate_3_networkx_dag: "PASS",
		gate_4_yagni_cost_guard: "PASS",
		gate_5_duckdb_sandbox: "PASS",
		shadow_passed: true,
	},
	wave_diff: {
		compare_run_id: "run-4804789955aa",
		dropped_columns_count: 0,
		hash_diff_mutations_count: 0,
		added_hubs: [],
		added_links: [],
		added_satellites: ["sat_positions_ext_v2"],
		added_columns: [
			{
				satellite: "sat_positions_ext_v2",
				column: "intraday_margin_buffer_bps",
			},
		],
		soft_deprecated_columns: [
			{ satellite: "sat_positions", column: "legacy_desk_memo" },
		],
	},
};
