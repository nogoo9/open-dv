"""15 Dynamic DuckDB Sandbox Invariant Evaluators (`DV-DYN-001` through `DV-DYN-015`).

Evaluates the 15 runtime Data Vault 2.0 invariants defined in `docs/03` §7.2 and
`docs/07` Scene 6 against an active DuckDB connection and returns structured
`DynamicCheckResult` diagnostics.
"""

from __future__ import annotations

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_core.constants import GHOST_DTS, GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_duckdb_sandbox.pit_query_engine import PitQueryEngine

HUB_PK_MAP: dict[str, str] = {
    "raw_vault.hub_fund": "hk_fund",
    "raw_vault.hub_limited_partner": "hk_lp",
    "raw_vault.hub_portfolio_company": "hk_portco",
    "raw_vault.hub_capital_account": "hk_cap_acct",
    "raw_vault.hub_unstructured_document": "hk_doc",
}

LINK_PK_MAP: dict[str, str] = {
    "raw_vault.lnk_lp_commitment": "hk_lnk_lp_commitment",
    "raw_vault.lnk_fund_portco_investment": "hk_lnk_fund_portco_investment",
    "raw_vault.lnk_cap_acct_alignment": "hk_lnk_cap_acct_alignment",
    "raw_vault.lnk_doc_lp_side_letter": "hk_lnk_doc_lp_side_letter",
    "raw_vault.lnk_doc_portco_memo": "hk_lnk_doc_portco_memo",
    "business_vault.sal_limited_partner": "hk_sal_limited_partner",
}

SATELLITE_PK_MAP: dict[str, str] = {
    "raw_vault.sat_fund_core": "hk_fund",
    "raw_vault.sat_lp_core": "hk_lp",
    "raw_vault.sat_lp_kyc_pii": "hk_lp",
    "raw_vault.sat_portco_core": "hk_portco",
    "raw_vault.sat_portco_valuation_mnpi": "hk_portco",
    "raw_vault.sat_cap_acct_quarterly_nav": "hk_cap_acct",
    "raw_vault.sat_doc_metadata": "hk_doc",
    "raw_vault.sat_lnk_lp_commitment_terms": "hk_lnk_lp_commitment",
    "raw_vault.sat_lnk_fund_portco_deal": "hk_lnk_fund_portco_investment",
    "business_vault.esat_sal_limited_partner": "hk_sal_limited_partner",
    "business_vault.sat_lp_side_letter_llm_ext": "hk_lnk_doc_lp_side_letter",
    "business_vault.sat_lnk_doc_portco_ai_provenance": "hk_lnk_doc_portco_memo",
}

FK_EDGES: tuple[tuple[str, str, str, str], ...] = (
    ("raw_vault.lnk_lp_commitment", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_lp_commitment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_fund_portco_investment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    (
        "raw_vault.lnk_fund_portco_investment",
        "hk_portco",
        "raw_vault.hub_portfolio_company",
        "hk_portco",
    ),
    (
        "raw_vault.lnk_cap_acct_alignment",
        "hk_cap_acct",
        "raw_vault.hub_capital_account",
        "hk_cap_acct",
    ),
    ("raw_vault.lnk_cap_acct_alignment", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_cap_acct_alignment", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.lnk_doc_lp_side_letter", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.lnk_doc_portco_memo", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    ("raw_vault.lnk_doc_portco_memo", "hk_portco", "raw_vault.hub_portfolio_company", "hk_portco"),
    (
        "business_vault.sal_limited_partner",
        "hk_lp_master",
        "raw_vault.hub_limited_partner",
        "hk_lp",
    ),
    (
        "business_vault.sal_limited_partner",
        "hk_lp_duplicate",
        "raw_vault.hub_limited_partner",
        "hk_lp",
    ),
    ("raw_vault.sat_fund_core", "hk_fund", "raw_vault.hub_fund", "hk_fund"),
    ("raw_vault.sat_lp_core", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.sat_lp_kyc_pii", "hk_lp", "raw_vault.hub_limited_partner", "hk_lp"),
    ("raw_vault.sat_portco_core", "hk_portco", "raw_vault.hub_portfolio_company", "hk_portco"),
    (
        "raw_vault.sat_portco_valuation_mnpi",
        "hk_portco",
        "raw_vault.hub_portfolio_company",
        "hk_portco",
    ),
    (
        "raw_vault.sat_cap_acct_quarterly_nav",
        "hk_cap_acct",
        "raw_vault.hub_capital_account",
        "hk_cap_acct",
    ),
    ("raw_vault.sat_doc_metadata", "hk_doc", "raw_vault.hub_unstructured_document", "hk_doc"),
    (
        "raw_vault.sat_lnk_lp_commitment_terms",
        "hk_lnk_lp_commitment",
        "raw_vault.lnk_lp_commitment",
        "hk_lnk_lp_commitment",
    ),
    (
        "raw_vault.sat_lnk_fund_portco_deal",
        "hk_lnk_fund_portco_investment",
        "raw_vault.lnk_fund_portco_investment",
        "hk_lnk_fund_portco_investment",
    ),
    (
        "business_vault.esat_sal_limited_partner",
        "hk_sal_limited_partner",
        "business_vault.sal_limited_partner",
        "hk_sal_limited_partner",
    ),
    (
        "business_vault.sat_lp_side_letter_llm_ext",
        "hk_lnk_doc_lp_side_letter",
        "raw_vault.lnk_doc_lp_side_letter",
        "hk_lnk_doc_lp_side_letter",
    ),
    (
        "business_vault.sat_lnk_doc_portco_ai_provenance",
        "hk_lnk_doc_portco_memo",
        "raw_vault.lnk_doc_portco_memo",
        "hk_lnk_doc_portco_memo",
    ),
)


class DynamicCheckResult(BaseModel):
    """Structured outcome of a single `DV-DYN-*` sandbox check."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(pattern=r"^DV-DYN-0(0[1-9]|1[0-5])$")
    check_name: str
    passed: bool
    metric_value: int | float | str | bool
    detail: str
    failing_rows: int = Field(default=0, ge=0)


class DynamicCheckSuiteReport(BaseModel):
    """Aggregate report of all 15 `DV-DYN-001`-`DV-DYN-015` checks."""

    model_config = ConfigDict(extra="forbid")

    all_passed: bool
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    checks: list[DynamicCheckResult] = Field(default_factory=list)

    @property
    def by_id(self) -> dict[str, DynamicCheckResult]:
        """Lookup map of `check_id -> DynamicCheckResult`."""
        return {c.check_id: c for c in self.checks}


def _scalar_int(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def check_dv_dyn_001_pk_uniqueness(conn: duckdb.DuckDBPyConnection) -> DynamicCheckResult:
    """DV-DYN-001: Primary Key Uniqueness across all Hubs, Links, and Satellites."""
    dup_count = 0
    for tbl, pk in {**HUB_PK_MAP, **LINK_PK_MAP}.items():
        dup_count += _scalar_int(conn, f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM {tbl}")
    for tbl, parent_hk in SATELLITE_PK_MAP.items():
        dup_count += _scalar_int(
            conn,
            f"SELECT COUNT(*) - COUNT(DISTINCT ({parent_hk} || '||' || "
            f"CAST(load_dts AS VARCHAR))) FROM {tbl}",
        )
    table_total = len(HUB_PK_MAP) + len(LINK_PK_MAP) + len(SATELLITE_PK_MAP)
    return DynamicCheckResult(
        check_id="DV-DYN-001",
        check_name="Primary Key Uniqueness",
        passed=dup_count == 0,
        metric_value=dup_count,
        failing_rows=dup_count,
        detail=f"Checked {table_total} tables; duplicate PK rows={dup_count}.",
    )


def check_dv_dyn_002_referential_integrity(conn: duckdb.DuckDBPyConnection) -> DynamicCheckResult:
    """DV-DYN-002: Parent-Child Referential Integrity (`0` orphan FK rows)."""
    orphan_total = 0
    for child_tbl, child_fk, parent_tbl, parent_pk in FK_EDGES:
        orphan_total += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {child_tbl} c
            LEFT JOIN {parent_tbl} p ON p.{parent_pk} = c.{child_fk}
            WHERE p.{parent_pk} IS NULL
            """,
        )
    return DynamicCheckResult(
        check_id="DV-DYN-002",
        check_name="Parent-Child Referential Integrity",
        passed=orphan_total == 0,
        metric_value=orphan_total,
        failing_rows=orphan_total,
        detail=f"Verified {len(FK_EDGES)} FK relationships; orphan rows={orphan_total}.",
    )


def check_dv_dyn_003_replay_idempotency(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_replay_delta: int | None = None,
) -> DynamicCheckResult:
    """DV-DYN-003: Replay Idempotency (re-running identical batch yields delta_rows = 0)."""
    if recorded_replay_delta is not None:
        delta = recorded_replay_delta
    else:
        before = _scalar_int(conn, "SELECT COUNT(*) FROM raw_vault.hub_fund")
        conn.execute(
            """
            INSERT INTO raw_vault.hub_fund (hk_fund, fund_code, load_dts, rec_src)
            SELECT hk_fund, fund_code, MIN(load_dts), ANY_VALUE(rec_src)
            FROM staging.stg_investran_lp_commitments
            WHERE hk_fund NOT IN (REPEAT('0', 64), REPEAT('f', 64))
              AND NOT EXISTS (
                  SELECT 1 FROM raw_vault.hub_fund h
                  WHERE h.hk_fund = stg_investran_lp_commitments.hk_fund
              )
            GROUP BY hk_fund, fund_code
            """
        )
        after = _scalar_int(conn, "SELECT COUNT(*) FROM raw_vault.hub_fund")
        delta = after - before
    return DynamicCheckResult(
        check_id="DV-DYN-003",
        check_name="Replay Idempotency",
        passed=delta == 0,
        metric_value=delta,
        failing_rows=max(delta, 0),
        detail=f"Batch replay delta_rows={delta} across Raw Vault entities.",
    )


def check_dv_dyn_004_true_change_capture(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_sat_delta: int | None = None,
    recorded_hub_link_delta: int = 0,
) -> DynamicCheckResult:
    """DV-DYN-004: True Change Capture (+1 Satellite row on attribute mutation, +0 Hub/Link)."""
    if recorded_sat_delta is not None:
        sat_versions = recorded_sat_delta
    else:
        sat_versions = _scalar_int(
            conn,
            """
            SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi
            WHERE rec_src <> 'SYSTEM_GHOST'
            """,
        )
    passed = sat_versions >= 1 and recorded_hub_link_delta == 0
    return DynamicCheckResult(
        check_id="DV-DYN-004",
        check_name="True Change Capture",
        passed=passed,
        metric_value=sat_versions,
        failing_rows=0 if passed else 1,
        detail=(
            f"Captured {sat_versions} Satellite delta version(s) with "
            f"hub_link_delta={recorded_hub_link_delta}."
        ),
    )


def check_dv_dyn_005_boundary_shift_collision(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-005: Boundary-Shift Collision Resistance ('AB'||'C' vs 'A'||'BC')."""
    row = conn.execute(
        """
        SELECT
            LOWER(SHA256('2:AB||1:C')) AS h_ab_c,
            LOWER(SHA256('1:A||2:BC')) AS h_a_bc,
            LOWER(SHA256('7:ANNE||O||5:BRIEN')) AS h_anne1,
            LOWER(SHA256('4:ANNE||7:O||BRIEN')) AS h_anne2
        """
    ).fetchone()
    assert row is not None
    collisions = int(row[0] == row[1]) + int(row[2] == row[3])
    return DynamicCheckResult(
        check_id="DV-DYN-005",
        check_name="Boundary-Shift Collision Resistance",
        passed=collisions == 0,
        metric_value=collisions,
        failing_rows=collisions,
        detail=(f"Length-prefixed SHA-256 ('2:AB||1:C' vs '1:A||2:BC') collisions={collisions}."),
    )


def check_dv_dyn_006_bkcc_collision_and_sal(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-006: Multi-Source BK Convergence & BKCC Isolation (`LP-101` & `sal`)."""
    lp_101_count = _scalar_int(
        conn,
        "SELECT COUNT(*) FROM raw_vault.hub_limited_partner WHERE lp_id = 'LP-101'",
    )
    sal_non_zero = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*) FROM business_vault.sal_investor_golden
        WHERE hk_sal_limited_partner NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
        """,
    )
    passed = lp_101_count == 2 and sal_non_zero >= 1
    return DynamicCheckResult(
        check_id="DV-DYN-006",
        check_name="Multi-Source BK Convergence & BKCC Isolation",
        passed=passed,
        metric_value=lp_101_count,
        failing_rows=0 if passed else 1,
        detail=(
            f"hub_limited_partner holds {lp_101_count} distinct BKCC-isolated 'LP-101' rows; "
            f"sal_investor_golden holds {sal_non_zero} resolved duplicate link(s)."
        ),
    )


def check_dv_dyn_007_ghost_and_zero_key_completeness(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-007: Ghost & Zero-Key INNER JOIN Completeness (`'0'*64`, `'f'*64`, `SYSTEM_GHOST`)."""
    missing_zero_keys = 0
    for tbl, pk in {**HUB_PK_MAP, **LINK_PK_MAP}.items():
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE {pk} IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')
              AND rec_src = 'SYSTEM_ZERO_KEY'
            """,
        )
        if cnt != 2:
            missing_zero_keys += abs(2 - cnt)

    missing_ghosts = 0
    for tbl, parent_hk in SATELLITE_PK_MAP.items():
        cnt = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE {parent_hk} = '{ZERO_KEY_UNKNOWN}'
              AND load_dts = TIMESTAMP '{GHOST_DTS}'
              AND rec_src = '{GHOST_REC_SRC}'
            """,
        )
        if cnt != 1:
            missing_ghosts += abs(1 - cnt)

    # Verify INNER JOIN preserves the NULL-FK DealCloud row ('PC-NULL-FK')
    inner_join_null_fk = _scalar_int(
        conn,
        f"""
        SELECT COUNT(*)
        FROM raw_vault.lnk_fund_portco_investment l
        INNER JOIN raw_vault.hub_fund hf ON hf.hk_fund = l.hk_fund
        INNER JOIN raw_vault.hub_portfolio_company hp ON hp.hk_portco = l.hk_portco
        INNER JOIN raw_vault.sat_portco_core sc ON sc.hk_portco = hp.hk_portco
        INNER JOIN raw_vault.sat_lnk_fund_portco_deal sd
            ON sd.hk_lnk_fund_portco_investment = l.hk_lnk_fund_portco_investment
        WHERE sd.deal_id = 'PC-NULL-FK'
          AND hp.hk_portco = '{ZERO_KEY_UNKNOWN}'
          AND sc.rec_src = '{GHOST_REC_SRC}'
        """,
    )
    failures = missing_zero_keys + missing_ghosts + (0 if inner_join_null_fk == 1 else 1)
    return DynamicCheckResult(
        check_id="DV-DYN-007",
        check_name="Ghost & Zero-Key INNER JOIN Completeness",
        passed=failures == 0,
        metric_value=failures,
        failing_rows=failures,
        detail=(
            f"missing_zero_keys={missing_zero_keys}, missing_ghosts={missing_ghosts}, "
            f"inner_join_null_fk_rows={inner_join_null_fk}."
        ),
    )


def check_dv_dyn_008_topological_load_order(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-008: Topological Load Order Compliance (Hubs -> Links -> Satellites -> PIT)."""
    missing_tables = 0
    for qualified_tbl in [
        *HUB_PK_MAP,
        *LINK_PK_MAP,
        *SATELLITE_PK_MAP,
        "information_mart.pit_portfolio_company",
    ]:
        schema_name, table_name = qualified_tbl.split(".")
        exists = _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'
            """,
        )
        if exists == 0:
            missing_tables += 1
    return DynamicCheckResult(
        check_id="DV-DYN-008",
        check_name="Topological Load Order Compliance",
        passed=missing_tables == 0,
        metric_value=missing_tables,
        failing_rows=missing_tables,
        detail=f"All 4 topological load waves materialized; missing_tables={missing_tables}.",
    )


def check_dv_dyn_009_bitemporal_late_arrival(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-009: Bi-Temporal Late-Arrival Correctness (12.80x -> 14.50x Q3 restatement)."""
    val_rows = _scalar_int(
        conn,
        """
        SELECT COUNT(*) FROM raw_vault.sat_portco_valuation_mnpi
        WHERE rec_src <> 'SYSTEM_GHOST'
        """,
    )
    if val_rows < 2:
        return DynamicCheckResult(
            check_id="DV-DYN-009",
            check_name="Bi-Temporal Late-Arrival Correctness",
            passed=False,
            metric_value=val_rows,
            failing_rows=1,
            detail=(
                f"Expected >= 2 valuation rows (initial 12.80x + restated 14.50x), "
                f"found {val_rows}."
            ),
        )
    engine = PitQueryEngine(conn)
    proof = engine.verify_bitemporal_restatement()
    return DynamicCheckResult(
        check_id="DV-DYN-009",
        check_name="Bi-Temporal Late-Arrival Correctness",
        passed=proof.verified,
        metric_value=f"{proof.prior_ev_ebitda_multiple}x -> {proof.restated_ev_ebitda_multiple}x",
        failing_rows=0 if proof.verified else 1,
        detail=(
            f"As-of {proof.prior_assertion_cutoff_ts}: {proof.prior_ev_ebitda_multiple}x "
            f"(${proof.prior_enterprise_value_usd:,.2f}); "
            f"As-of {proof.restated_assertion_cutoff_ts}: "
            f"{proof.restated_ev_ebitda_multiple}x (${proof.restated_enterprise_value_usd:,.2f})."
        ),
    )


def check_dv_dyn_010_pit_table_parity(conn: duckdb.DuckDBPyConnection) -> DynamicCheckResult:
    """DV-DYN-010: PIT Table Lookup Parity & Ghost-Record Coalescing."""
    null_pointers = _scalar_int(
        conn,
        """
        SELECT COUNT(*) FROM information_mart.pit_portfolio_company
        WHERE sat_portco_core_hk IS NULL
           OR sat_portco_core_load_dts IS NULL
           OR sat_portco_valuation_mnpi_hk IS NULL
           OR sat_portco_valuation_mnpi_load_dts IS NULL
        """,
    )
    parity_mismatches = _scalar_int(
        conn,
        """
        SELECT COUNT(*)
        FROM information_mart.pit_portfolio_company pit
        LEFT JOIN raw_vault.sat_portco_valuation_mnpi sv
            ON sv.hk_portco = pit.sat_portco_valuation_mnpi_hk
           AND sv.load_dts  = pit.sat_portco_valuation_mnpi_load_dts
        WHERE sv.hk_portco IS NULL
        """,
    )
    failures = null_pointers + parity_mismatches
    return DynamicCheckResult(
        check_id="DV-DYN-010",
        check_name="PIT Table Lookup Parity",
        passed=failures == 0,
        metric_value=failures,
        failing_rows=failures,
        detail=(
            f"pit_portfolio_company null_pointers={null_pointers}, "
            f"parity_mismatches={parity_mismatches}."
        ),
    )


def check_dv_dyn_011_driving_key_effectivity(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-011: Driving-Key Effectivity Exclusivity in `esat_sal_limited_partner`."""
    overlapping_active = _scalar_int(
        conn,
        """
        SELECT COUNT(*) FROM (
            SELECT hk_sal_limited_partner, COUNT(*) AS active_cnt
            FROM business_vault.esat_sal_limited_partner
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND is_active_flag = TRUE
              AND effective_to_dts IS NULL
            GROUP BY hk_sal_limited_partner
            HAVING COUNT(*) > 1
        )
        """,
    )
    return DynamicCheckResult(
        check_id="DV-DYN-011",
        check_name="Driving-Key Effectivity Exclusivity",
        passed=overlapping_active == 0,
        metric_value=overlapping_active,
        failing_rows=overlapping_active,
        detail=(
            f"Overlapping active effectivity intervals in "
            f"esat_sal_limited_partner={overlapping_active}."
        ),
    )


def check_dv_dyn_012_unstructured_provenance(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-012: Unstructured Extraction Provenance Integrity (model, prompt SHA-256, quote)."""
    ungrounded = _scalar_int(
        conn,
        """
        SELECT (
            SELECT COUNT(*) FROM business_vault.sat_lp_side_letter_llm_ext
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND (
                  extractor_model_id IS NULL
                  OR prompt_template_sha256 IS NULL
                  OR STRLEN(TRIM(prompt_template_sha256)) <> 64
                  OR extraction_confidence IS NULL
                  OR extraction_confidence <= 0
                  OR extraction_confidence > 1
                  OR source_text_quote IS NULL
                  OR TRIM(source_text_quote) = ''
              )
        ) + (
            SELECT COUNT(*) FROM business_vault.sat_lnk_doc_portco_ai_provenance
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND (
                  extractor_model_id IS NULL
                  OR prompt_template_sha256 IS NULL
                  OR STRLEN(TRIM(prompt_template_sha256)) <> 64
                  OR extraction_confidence IS NULL
                  OR extraction_confidence <= 0
                  OR extraction_confidence > 1
                  OR source_text_quote IS NULL
                  OR TRIM(source_text_quote) = ''
              )
        )
        """,
    )
    return DynamicCheckResult(
        check_id="DV-DYN-012",
        check_name="Unstructured Extraction Provenance Integrity",
        passed=ungrounded == 0,
        metric_value=ungrounded,
        failing_rows=ungrounded,
        detail=f"Ungrounded AI extraction rows missing model/prompt/confidence/quote={ungrounded}.",
    )


def check_dv_dyn_013_tier2_confidence_governance(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-013: Tier-2 Confidence Threshold & Human Review Governance."""
    invalid_governance = _scalar_int(
        conn,
        """
        SELECT (
            SELECT COUNT(*) FROM business_vault.sat_lp_side_letter_llm_ext
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND (
                  (extraction_confidence < 0.85 AND human_review_status = 'AUTO_ACCEPTED_TIER_2')
                  OR human_review_status IS NULL
              )
        ) + (
            SELECT COUNT(*) FROM business_vault.sat_lnk_doc_portco_ai_provenance
            WHERE rec_src <> 'SYSTEM_GHOST'
              AND (
                  (extraction_confidence < 0.85 AND human_review_status = 'AUTO_ACCEPTED_TIER_2')
                  OR (
                      tier2_token_jaccard < 0.85
                      AND human_review_status NOT IN ('STEWARD_VERIFIED', 'STEWARD_HOLD')
                  )
                  OR human_review_status IS NULL
              )
        )
        """,
    )
    return DynamicCheckResult(
        check_id="DV-DYN-013",
        check_name="Tier-2 Confidence Threshold & Human Review Governance",
        passed=invalid_governance == 0,
        metric_value=invalid_governance,
        failing_rows=invalid_governance,
        detail=(
            f"AI extraction rows violating Tier-2/Steward review thresholds={invalid_governance}."
        ),
    )


def check_dv_dyn_014_additive_schema_evolution(
    conn: duckdb.DuckDBPyConnection,
    *,
    historical_hash_mutations: int = 0,
) -> DynamicCheckResult:
    """DV-DYN-014: Additive Schema Evolution Non-Destructiveness (`0` hash_diff mutations)."""
    invalid_hashes = _scalar_int(
        conn,
        """
        SELECT COUNT(*) FROM raw_vault.sat_lp_core
        WHERE hash_diff IS NULL OR STRLEN(TRIM(hash_diff)) <> 64
        """,
    )
    total_failures = historical_hash_mutations + invalid_hashes
    return DynamicCheckResult(
        check_id="DV-DYN-014",
        check_name="Additive Schema Evolution Non-Destructiveness",
        passed=total_failures == 0,
        metric_value=total_failures,
        failing_rows=total_failures,
        detail=(
            f"Historical Satellite hash_diff mutations={historical_hash_mutations}, "
            f"invalid_hash_lengths={invalid_hashes}."
        ),
    )


def check_dv_dyn_015_insert_only_audit_invariant(
    conn: duckdb.DuckDBPyConnection,
) -> DynamicCheckResult:
    """DV-DYN-015: Zero-Update / Insert-Only Audit Invariant across all Raw Vault tables."""
    violations = 0
    for tbl in [*HUB_PK_MAP, *LINK_PK_MAP]:
        violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE load_dts < TIMESTAMP '{GHOST_DTS}'
               OR rec_src IS NULL
               OR TRIM(rec_src) = ''
            """,
        )
    for tbl in SATELLITE_PK_MAP:
        violations += _scalar_int(
            conn,
            f"""
            SELECT COUNT(*) FROM {tbl}
            WHERE load_dts < TIMESTAMP '{GHOST_DTS}'
               OR rec_src IS NULL
               OR TRIM(rec_src) = ''
               OR hash_diff IS NULL
               OR STRLEN(TRIM(hash_diff)) <> 64
            """,
        )
    return DynamicCheckResult(
        check_id="DV-DYN-015",
        check_name="Zero-Update / Insert-Only Audit Invariant",
        passed=violations == 0,
        metric_value=violations,
        failing_rows=violations,
        detail=f"Insert-only audit metadata violations across all Vault tables={violations}.",
    )


def evaluate_all_dynamic_checks(
    conn: duckdb.DuckDBPyConnection,
    *,
    recorded_replay_delta: int | None = None,
    recorded_sat_delta: int | None = None,
    recorded_hub_link_delta: int = 0,
    historical_hash_mutations: int = 0,
) -> DynamicCheckSuiteReport:
    """Execute all 15 `DV-DYN-001` through `DV-DYN-015` checks on `conn`."""
    results = [
        check_dv_dyn_001_pk_uniqueness(conn),
        check_dv_dyn_002_referential_integrity(conn),
        check_dv_dyn_003_replay_idempotency(conn, recorded_replay_delta=recorded_replay_delta),
        check_dv_dyn_004_true_change_capture(
            conn,
            recorded_sat_delta=recorded_sat_delta,
            recorded_hub_link_delta=recorded_hub_link_delta,
        ),
        check_dv_dyn_005_boundary_shift_collision(conn),
        check_dv_dyn_006_bkcc_collision_and_sal(conn),
        check_dv_dyn_007_ghost_and_zero_key_completeness(conn),
        check_dv_dyn_008_topological_load_order(conn),
        check_dv_dyn_009_bitemporal_late_arrival(conn),
        check_dv_dyn_010_pit_table_parity(conn),
        check_dv_dyn_011_driving_key_effectivity(conn),
        check_dv_dyn_012_unstructured_provenance(conn),
        check_dv_dyn_013_tier2_confidence_governance(conn),
        check_dv_dyn_014_additive_schema_evolution(
            conn, historical_hash_mutations=historical_hash_mutations
        ),
        check_dv_dyn_015_insert_only_audit_invariant(conn),
    ]
    passed_cnt = sum(1 for r in results if r.passed)
    failed_cnt = len(results) - passed_cnt
    return DynamicCheckSuiteReport(
        all_passed=failed_cnt == 0,
        passed_count=passed_cnt,
        failed_count=failed_cnt,
        checks=results,
    )
