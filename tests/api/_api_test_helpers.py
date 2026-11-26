"""Shared Wave 1 / Wave 2 public_am builders and HTTP helpers for tests/api/."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx

from opendv.models.diagnostics import AntiPatternJustification, ClarificationQuestion
from opendv.models.ontology import (
    ConceptAttribute,
    ConceptTableBinding,
    DocumentCitationRef,
    GraphAOntology,
)
from opendv.models.ontology_deltas import AddConcept
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
    PortableLogicalType,
)
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.service.app_service import OpenDVService
from tests.helpers.mock_harness import MockTurnScript

PUBLIC_AM = Path(__file__).resolve().parents[2] / "data" / "public_am"


def copy_public_am_wave1(ws: Path) -> Path:
    am_dir = ws / "public_am"
    am_dir.mkdir(parents=True, exist_ok=True)
    for fname in (
        "am_sec_treasury_ibor.sqlite",
        "am_esg_stewardship_proxy.parquet",
        "am_stewardship_domain_notes.md",
        "am_custody_and_voting_guide.pdf",
    ):
        shutil.copy2(PUBLIC_AM / fname, am_dir / fname)
    return am_dir


def copy_public_am_wave2(ws: Path) -> Path:
    am_dir = ws / "public_am"
    am_dir.mkdir(parents=True, exist_ok=True)
    for fname in (
        "wave2_eu_custody_lei_and_drift.parquet",
        "wave2_bitemporal_nav_restatements.csv",
        "wave2_ambiguous_otc_collateral.csv",
        "prime_allocations_w2.csv",
    ):
        shutil.copy2(PUBLIC_AM / fname, am_dir / fname)
    return am_dir


def _logical_type_from_sql(sql_type: str) -> PortableLogicalType:
    up = sql_type.upper()
    if "INT" in up:
        return "INT64"
    if "BOOL" in up:
        return "BOOLEAN"
    if "DATE" in up and "TIME" not in up:
        return "DATE32"
    if "TIME" in up:
        return "TIMESTAMP_UTC_US"
    if any(tok in up for tok in ("DEC", "NUM", "FLOAT", "DOUBLE", "REAL")):
        return DecimalType(precision=18, scale=4)
    return "UTF8_STRING"


def _add_esg_split(
    prof: SourceProfile,
    tbl: TableProfile,
    hubs: list[DraftHubSpec],
    sats: list[DraftSatelliteSpec],
    triage: list[ColumnTriageEntry],
) -> None:
    specs = [
        (
            "hub_esg_rating",
            "esg_rating_id",
            "ESG",
            "sat_esg_rating_core",
            [
                "cusip",
                "issuer_name",
                "esg_provider_code",
                "esg_overall_grade",
                "environmental_score",
                "social_score",
                "governance_score",
                "controversy_flag",
                "rating_effective_date",
            ],
            "STANDARD",
        ),
        (
            "hub_carbon_disclosure",
            "carbon_disclosure_id",
            "CARB",
            "sat_carbon_disclosure_core",
            [
                "reporting_fiscal_year",
                "scope1_tco2e",
                "scope2_tco2e",
                "scope3_tco2e",
                "carbon_intensity_tco2e_per_musd",
                "sbti_net_zero_status",
            ],
            "STANDARD",
        ),
        (
            "hub_proxy_ballot",
            "proxy_ballot_id",
            "PRX",
            "sat_proxy_ballot_core",
            [
                "agm_meeting_date",
                "proposal_category",
                "proposal_title",
                "management_recommendation",
                "vote_cast",
                "voted_shares",
                "stewardship_policy_code",
            ],
            "STANDARD",
        ),
        (
            "hub_mandate_rule",
            "mandate_rule_id",
            "MANDATE",
            "mas_mandate_rule_core",
            ["mandate_policy_name", "breach_severity"],
            "MAS",
        ),
        (
            None,
            None,
            None,
            "sat_proxy_ballot_pii",
            ["steward_review_required"],
            "PII",
        ),
    ]
    col_by_name = {c.col_name: c for c in tbl.columns}
    covered: set[str] = set()
    for hub_name, bk_col, bkcc, sat_name, sat_cols, sat_kind in specs:
        parent_hub = hub_name or "hub_proxy_ballot"
        if hub_name is not None and bk_col is not None and bkcc is not None:
            hubs.append(
                DraftHubSpec(
                    hub_name=hub_name,
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(
                            table_id=tbl.table_id,
                            col_name=bk_col,
                            source_id=prof.source_id,
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            bkcc_value="GLOBAL",
                        )
                    ],
                    source_bindings=[
                        DraftSourceBinding(
                            source_id=prof.source_id,
                            table_id=tbl.table_id,
                            col_mappings={bk_col: bk_col},
                        )
                    ],
                )
            )
            triage.append(
                ColumnTriageEntry(
                    table_id=tbl.table_id,
                    col_name=bk_col,
                    disposition="MODELED",
                    target_ref=hub_name,
                )
            )
            covered.add(bk_col)
        attrs: list[DraftField] = []
        mappings: dict[str, str] = {}
        for cname in sat_cols:
            col = col_by_name[cname]
            covered.add(cname)
            mappings[cname] = cname
            attrs.append(
                DraftField(
                    name=cname,
                    logical_type=_logical_type_from_sql(col.sql_type),
                    nullable=col.nullable,
                    is_pii=(sat_kind == "PII" or bool(col.pii_tags)),
                )
            )
            triage.append(
                ColumnTriageEntry(
                    table_id=tbl.table_id,
                    col_name=cname,
                    disposition="MODELED",
                    target_ref=sat_name,
                )
            )
        sats.append(
            DraftSatelliteSpec(
                sat_name=sat_name,
                sat_kind=sat_kind,  # type: ignore[arg-type]
                parent_name=parent_hub,
                child_dependent_keys=(
                    ["breach_severity"] if sat_kind == "MAS" and "breach_severity" in mappings else []
                ),
                source_bindings=[
                    DraftSourceBinding(
                        source_id=prof.source_id,
                        table_id=tbl.table_id,
                        col_mappings=mappings,
                    )
                ],
                attributes=attrs,
            )
        )
    for col in tbl.columns:
        if col.col_name not in covered:
            triage.append(
                ColumnTriageEntry(
                    table_id=tbl.table_id,
                    col_name=col.col_name,
                    disposition="OMITTED_INTENTIONALLY",
                    rationale="Supplemental column deferred to downstream mart view.",
                )
            )


def build_wave1_am_draft(
    project_id: str,
    ontology_version: str,
    profiles: list[SourceProfile],
) -> DraftVaultIR:
    hubs: list[DraftHubSpec] = []
    links: list[DraftLinkSpec] = []
    sats: list[DraftSatelliteSpec] = []
    triage: list[ColumnTriageEntry] = []
    bk_by_table = {
        "holdings_13f": ("hub_holding", "holding_id", "SEC_13F"),
        "institutional_filers": ("hub_filer", "filer_cik", "SEC_CIK"),
        "issuers": ("hub_issuer", "cik", "SEC_CIK"),
        "securities": ("hub_security", "ticker", "TICKER"),
        "treasury_rates": ("hub_treasury_series", "series_id", "FRED"),
        "yield_curves": ("hub_yield_curve", "curve_id", "YC"),
    }
    holding_tid, holding_sid = "", ""
    for prof in profiles:
        for tbl in prof.tables:
            if tbl.table_name == "am_esg_stewardship_proxy":
                _add_esg_split(prof, tbl, hubs, sats, triage)
                continue
            if tbl.table_name not in bk_by_table:
                continue
            hub_name, bk_col, _bkcc = bk_by_table[tbl.table_name]
            if tbl.table_name == "holdings_13f":
                holding_tid, holding_sid = tbl.table_id, prof.source_id
            bks = [
                BusinessKeyPart(
                    table_id=tbl.table_id,
                    col_name=bk_col,
                    source_id=prof.source_id,
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ]
            binds = [
                DraftSourceBinding(
                    source_id=prof.source_id,
                    table_id=tbl.table_id,
                    col_mappings={bk_col: bk_col},
                )
            ]
            if tbl.table_name == "institutional_filers":
                for p2 in profiles:
                    for t2 in p2.tables:
                        if t2.table_name == "holdings_13f":
                            bks.append(
                                BusinessKeyPart(
                                    table_id=t2.table_id,
                                    col_name="filer_cik",
                                    source_id=p2.source_id,
                                    key_tier="TIER_1_GLOBAL_STANDARD",
                                    bkcc_value="GLOBAL",
                                )
                            )
                            binds.append(
                                DraftSourceBinding(
                                    source_id=p2.source_id,
                                    table_id=t2.table_id,
                                    col_mappings={"filer_cik": "filer_cik"},
                                )
                            )
            hubs.append(
                DraftHubSpec(
                    hub_name=hub_name,
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=bks,
                    source_bindings=binds,
                )
            )
            triage.append(
                ColumnTriageEntry(
                    table_id=tbl.table_id,
                    col_name=bk_col,
                    disposition="MODELED",
                    target_ref=hub_name,
                )
            )
            excluded = {bk_col}
            if tbl.table_name == "holdings_13f":
                excluded.add("filer_cik")
                triage.append(
                    ColumnTriageEntry(
                        table_id=tbl.table_id,
                        col_name="filer_cik",
                        disposition="MODELED",
                        target_ref="hub_filer",
                    )
                )
            non_bk = [c for c in tbl.columns if c.col_name not in excluded]
            if non_bk:
                is_mas = tbl.table_name in ("treasury_rates", "yield_curves")
                sat_kind = "MAS" if is_mas else "STANDARD"
                cdks = ["observation_date"] if is_mas else []
                sat_name = f"sat_{tbl.table_name}_core"
                sats.append(
                    DraftSatelliteSpec(
                        sat_name=sat_name,
                        sat_kind=sat_kind,  # type: ignore[arg-type]
                        parent_name=hub_name,
                        child_dependent_keys=cdks,
                        source_bindings=[
                            DraftSourceBinding(
                                source_id=prof.source_id,
                                table_id=tbl.table_id,
                                col_mappings={c.col_name: c.col_name for c in non_bk},
                            )
                        ],
                        attributes=[
                            DraftField(
                                name=c.col_name,
                                logical_type=_logical_type_from_sql(c.sql_type),
                                nullable=c.nullable,
                                is_pii=bool(c.pii_tags),
                            )
                            for c in non_bk
                        ],
                    )
                )
                for c in non_bk:
                    triage.append(
                        ColumnTriageEntry(
                            table_id=tbl.table_id,
                            col_name=c.col_name,
                            disposition="MODELED",
                            target_ref=sat_name,
                        )
                    )
    if holding_tid:
        links.append(
            DraftLinkSpec(
                link_name="lnk_holding_filer",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_holding",
                        role_name="holding",
                        fk_col_name="hk_holding",
                        source_col_names=["holding_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_filer",
                        role_name="filer",
                        fk_col_name="hk_filer",
                        source_col_names=["filer_cik"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=holding_sid,
                        table_id=holding_tid,
                        col_mappings={"holding_id": "holding_id", "filer_cik": "filer_cik"},
                    )
                ],
            )
        )
    return DraftVaultIR(
        project_id=project_id,
        ontology_version=ontology_version,
        wave_number=1,
        hubs=hubs,
        links=links,
        satellites=sats,
        column_triage=triage,
        justifications=[
            AntiPatternJustification(
                waiver_id="WAIV-AP004",
                rule_id="DV-AP-004",
                entity_name="hub_filer",
                rationale="Institutional Filer CIK is an authoritative Form 13F primary entity.",
                actor="agent",
                reviewed_by_v7=True,
            )
        ],
    )


def build_wave2_am_draft(
    project_id: str,
    ontology_version: str,
    profiles: list[SourceProfile],
) -> DraftVaultIR:
    w1 = build_wave1_am_draft(project_id, ontology_version, profiles)
    hubs = list(w1.hubs)
    links = list(w1.links)
    sats = list(w1.satellites)
    triage = list(w1.column_triage)

    by_tname: dict[str, tuple[str, TableProfile]] = {}
    for prof in profiles:
        for tbl in prof.tables:
            by_tname[tbl.table_name] = (prof.source_id, tbl)

    eu_sid, eu_tbl = by_tname["wave2_eu_custody_lei_and_drift"]
    nav_sid, nav_tbl = by_tname["wave2_bitemporal_nav_restatements"]
    otc_sid, otc_tbl = by_tname["wave2_ambiguous_otc_collateral"]
    alloc_sid, alloc_tbl = by_tname["prime_allocations_w2"]

    hubs.extend(
        [
            DraftHubSpec(
                hub_name="hub_issuer_lei",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=eu_tbl.table_id,
                        col_name="lei_code",
                        source_id=eu_sid,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=eu_sid,
                        table_id=eu_tbl.table_id,
                        col_mappings={"lei_code": "lei_code"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_security_isin",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=eu_tbl.table_id,
                        col_name="isin",
                        source_id=eu_sid,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=eu_sid,
                        table_id=eu_tbl.table_id,
                        col_mappings={"isin": "isin"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_fund_nav",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=nav_tbl.table_id,
                        col_name="fund_code",
                        source_id=nav_sid,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="FUND_ADMIN",
                    ),
                    BusinessKeyPart(
                        table_id=nav_tbl.table_id,
                        col_name="share_class",
                        source_id=nav_sid,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="FUND_ADMIN",
                    ),
                    BusinessKeyPart(
                        table_id=nav_tbl.table_id,
                        col_name="as_of_date",
                        source_id=nav_sid,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="FUND_ADMIN",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=nav_sid,
                        table_id=nav_tbl.table_id,
                        col_mappings={
                            "fund_code": "fund_code",
                            "share_class": "share_class",
                            "as_of_date": "as_of_date",
                        },
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_otc_collateral",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=otc_tbl.table_id,
                        col_name="uti_id",
                        source_id=otc_sid,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=otc_sid,
                        table_id=otc_tbl.table_id,
                        col_mappings={"uti_id": "uti_id"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_prime_allocation",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=alloc_tbl.table_id,
                        col_name=alloc_tbl.columns[0].col_name,
                        source_id=alloc_sid,
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="PRIME",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=alloc_sid,
                        table_id=alloc_tbl.table_id,
                        col_mappings={alloc_tbl.columns[0].col_name: alloc_tbl.columns[0].col_name},
                    )
                ],
            ),
        ]
    )
    links.append(
        DraftLinkSpec(
            link_name="sal_issuer",
            link_kind="SAME_AS",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_security_isin",
                    role_name="PRIMARY",
                    fk_col_name="hk_security_isin",
                    source_col_names=["isin"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_issuer_lei",
                    role_name="ALIAS",
                    fk_col_name="hk_issuer_lei",
                    source_col_names=["lei_code"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id=eu_sid,
                    table_id=eu_tbl.table_id,
                    col_mappings={"isin": "isin", "lei_code": "lei_code"},
                )
            ],
        )
    )
    sats.extend(
        [
            DraftSatelliteSpec(
                sat_name="sat_nav_bitemp",
                sat_kind="STANDARD",
                parent_name="hub_fund_nav",
                is_bitemporal=True,
                applied_dts_col="applied_dts",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=nav_sid,
                        table_id=nav_tbl.table_id,
                        col_mappings={
                            "nav_per_share": "nav_per_share",
                            "aum_usd": "aum_usd",
                            "restatement_reason": "restatement_reason",
                        },
                    )
                ],
                attributes=[
                    DraftField(
                        name="nav_per_share",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="aum_usd",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="restatement_reason",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    ),
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_otc_collateral_core",
                sat_kind="STANDARD",
                parent_name="hub_otc_collateral",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=otc_sid,
                        table_id=otc_tbl.table_id,
                        col_mappings={
                            "trade_id": "trade_id",
                            "isda_master_id": "isda_master_id",
                            "pledge_amount": "pledge_amount",
                        },
                    )
                ],
                attributes=[
                    DraftField(
                        name="trade_id",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="isda_master_id",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="pledge_amount",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                ],
            ),
            DraftSatelliteSpec(
                sat_name="mas_allocations",
                sat_kind="MAS",
                parent_name="hub_prime_allocation",
                child_dependent_keys=["allocation_seq"],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=alloc_sid,
                        table_id=alloc_tbl.table_id,
                        col_mappings={
                            "allocation_seq": "allocation_seq",
                            "fund_account": "fund_account",
                            "shares_allocated": "shares_allocated",
                            "avg_price_dec": "avg_price_dec",
                        },
                    )
                ],
                attributes=[
                    DraftField(
                        name="allocation_seq",
                        logical_type="INT64",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="fund_account",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="shares_allocated",
                        logical_type="INT64",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="avg_price_dec",
                        logical_type=DecimalType(precision=18, scale=4),
                        nullable=False,
                        is_pii=False,
                    ),
                ],
            ),
        ]
    )
    for tbl, col_targets in (
        (eu_tbl, {"lei_code": "hub_issuer_lei", "isin": "hub_security_isin"}),
        (
            nav_tbl,
            {
                "fund_code": "hub_fund_nav",
                "share_class": "hub_fund_nav",
                "as_of_date": "hub_fund_nav",
                "applied_dts": "sat_nav_bitemp",
                "nav_per_share": "sat_nav_bitemp",
                "aum_usd": "sat_nav_bitemp",
                "restatement_reason": "sat_nav_bitemp",
            },
        ),
        (
            otc_tbl,
            {
                "uti_id": "hub_otc_collateral",
                "trade_id": "sat_otc_collateral_core",
                "isda_master_id": "sat_otc_collateral_core",
                "pledge_amount": "sat_otc_collateral_core",
            },
        ),
        (
            alloc_tbl,
            {
                "block_id": "hub_prime_allocation",
                "allocation_seq": "mas_allocations",
                "fund_account": "mas_allocations",
                "shares_allocated": "mas_allocations",
                "avg_price_dec": "mas_allocations",
            },
        ),
    ):
        for col in tbl.columns:
            tgt = col_targets.get(col.col_name)
            if tgt is not None:
                triage.append(
                    ColumnTriageEntry(
                        table_id=tbl.table_id,
                        col_name=col.col_name,
                        disposition="MODELED",
                        target_ref=tgt,
                    )
                )
            else:
                triage.append(
                    ColumnTriageEntry(
                        table_id=tbl.table_id,
                        col_name=col.col_name,
                        disposition="OMITTED_INTENTIONALLY",
                        rationale="Deferred Wave 2 auxiliary column not mapped in core vault.",
                    )
                )
    return DraftVaultIR(
        project_id=project_id,
        ontology_version=ontology_version,
        wave_number=2,
        hubs=hubs,
        links=links,
        satellites=sats,
        column_triage=triage,
        justifications=[
            *w1.justifications,
            AntiPatternJustification(
                waiver_id="WAIV-AP004-NAV",
                rule_id="DV-AP-004",
                entity_name="hub_fund_nav",
                rationale="Composite NAV valuation entity uses standardized fund/class/date keys.",
                actor="agent",
                reviewed_by_v7=True,
            ),
        ],
    )


def build_wave1_commit_ops(
    sqlite_sid: str,
    pq_sid: str,
    md_sid: str,
    md_quote: str,
) -> list[dict[str, Any]]:
    ops = [
        AddConcept(
            concept_uri="urn:asset_mgmt:InstitutionalFiler",
            concept_name="Institutional Filer",
            description="Registered institutional manager filing Form 13F.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="SEC_CIK",
            attributes=[
                ConceptAttribute(
                    name="filer_cik",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    nullable=False,
                    is_business_key=True,
                    is_pii=False,
                )
            ],
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:institutional_filers",
                    col_name="filer_cik",
                )
            ],
            source_citations=[
                DocumentCitationRef(doc_id=md_sid, page_number=1, verbatim_quote=md_quote)
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:Filer",
            concept_name="Filer",
            description="Filer hub alias grounded to institutional_filers.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="SEC_CIK",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:institutional_filers",
                    col_name="filer_cik",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:Holding",
            concept_name="Holding",
            description="Form 13F equity holding position.",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            bkcc_default="SEC_13F",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:holdings_13f",
                    col_name="holding_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:Issuer",
            concept_name="Issuer",
            description="Public corporate issuer identified by SEC CIK.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="SEC_CIK",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:issuers",
                    col_name="cik",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:Security",
            concept_name="Security",
            description="Listed tradable security identified by ticker.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="TICKER",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:securities",
                    col_name="ticker",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:TreasurySeries",
            concept_name="Treasury Series",
            description="US Treasury benchmark yield series.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="FRED",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:treasury_rates",
                    col_name="series_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:YieldCurve",
            concept_name="Yield Curve",
            description="US Treasury par yield curve observations.",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            bkcc_default="YC",
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sid,
                    table_id=f"{sqlite_sid}:yield_curves",
                    col_name="curve_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:EsgRating",
            concept_name="Esg Rating",
            description="ESG stewardship rating record.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="ESG",
            table_bindings=[
                ConceptTableBinding(
                    source_id=pq_sid,
                    table_id=f"{pq_sid}:am_esg_stewardship_proxy",
                    col_name="esg_rating_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:CarbonDisclosure",
            concept_name="Carbon Disclosure",
            description="Issuer Scope 1-3 emissions disclosure.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="CARB",
            table_bindings=[
                ConceptTableBinding(
                    source_id=pq_sid,
                    table_id=f"{pq_sid}:am_esg_stewardship_proxy",
                    col_name="carbon_disclosure_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:ProxyBallot",
            concept_name="Proxy Ballot",
            description="AGM stewardship proxy ballot vote.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="PRX",
            table_bindings=[
                ConceptTableBinding(
                    source_id=pq_sid,
                    table_id=f"{pq_sid}:am_esg_stewardship_proxy",
                    col_name="proxy_ballot_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:MandateRule",
            concept_name="Mandate Rule",
            description="Portfolio stewardship mandate compliance rule.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            bkcc_default="MANDATE",
            table_bindings=[
                ConceptTableBinding(
                    source_id=pq_sid,
                    table_id=f"{pq_sid}:am_esg_stewardship_proxy",
                    col_name="mandate_rule_id",
                )
            ],
        ),
    ]
    return [op.model_dump(mode="json") for op in ops]


async def wait_run_via_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    last_event_id: str | None = None,
) -> list[dict[str, Any]]:
    headers = {"Last-Event-ID": last_event_id} if last_event_id is not None else {}
    frames: list[dict[str, Any]] = []
    async with client.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        cur: dict[str, Any] = {}
        async for line in resp.aiter_lines():
            if line == "":
                if cur:
                    frames.append(cur)
                    cur = {}
                continue
            if line.startswith(":"):
                continue
            key, _, val = line.partition(":")
            cur[key.strip()] = val.lstrip()
        if cur:
            frames.append(cur)
    return frames


def load_project_profiles(svc: OpenDVService, project_id: str) -> list[SourceProfile]:
    profs: list[SourceProfile] = []
    for s in svc.list_sources(project_id):
        rec = svc.state_manager.get_source_record(project_id, s.source_id)
        prof = SourceProfile.model_validate(rec["profile"])
        if prof.tables:
            profs.append(prof)
    return profs


def build_wave2_commit_ops(sources: list[dict[str, Any]]) -> list[AddConcept]:
    w2_eu = next(s for s in sources if "wave2_eu" in s["uri_or_filename"])
    w2_nav = next(s for s in sources if "bitemporal_nav" in s["uri_or_filename"])
    w2_otc = next(s for s in sources if "otc_collateral" in s["uri_or_filename"])
    w2_alloc = next(s for s in sources if "prime_allocations" in s["uri_or_filename"])
    return [
        AddConcept(
            concept_uri="urn:asset_mgmt:CustodyAccount",
            concept_name="CustodyAccount",
            description="EU custody account",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            table_bindings=[
                ConceptTableBinding(
                    source_id=w2_eu["source_id"],
                    table_id=f"{w2_eu['source_id']}:wave2_eu_custody_lei_and_drift",
                    col_name="lei_code",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:SecurityIsin",
            concept_name="Security Isin",
            description="Global ISIN",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            table_bindings=[
                ConceptTableBinding(
                    source_id=w2_eu["source_id"],
                    table_id=f"{w2_eu['source_id']}:wave2_eu_custody_lei_and_drift",
                    col_name="isin",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:FundNav",
            concept_name="Fund Nav",
            description="Fund NAV",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            table_bindings=[
                ConceptTableBinding(
                    source_id=w2_nav["source_id"],
                    table_id=f"{w2_nav['source_id']}:wave2_bitemporal_nav_restatements",
                    col_name="fund_code",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:OtcCollateral",
            concept_name="Otc Collateral",
            description="OTC collateral",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            table_bindings=[
                ConceptTableBinding(
                    source_id=w2_otc["source_id"],
                    table_id=f"{w2_otc['source_id']}:wave2_ambiguous_otc_collateral",
                    col_name="uti_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:PrimeAllocation",
            concept_name="Prime Allocation",
            description="Prime allocation",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            table_bindings=[
                ConceptTableBinding(
                    source_id=w2_alloc["source_id"],
                    table_id=f"{w2_alloc['source_id']}:prime_allocations_w2",
                    col_name="block_id",
                )
            ],
        ),
    ]
