"""UAT-V25-SVC-07: Mid-Build Checkpoint headless E2E over data/public_am/."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    DocumentCitationRef,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import ConceptAttribute, ConceptTableBinding
from opendv.models.ontology_deltas import AddConcept
from opendv.models.sources import SourceProfile
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
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    FiboQueryReq,
    HoldAnswersReq,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyValidateReq,
    ProjectInitReq,
    ScanApprovalReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import (
    HorizonCriticVerdict,
    SemanticCriticVerdict,
    SqlQueryReq,
    StorageCompileReq,
    StorageTargetReq,
    VaultBuildReq,
)
from opendv.service import OpenDVService
from tests.helpers import DeterministicMockHarness, MockTurnScript

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


def _logical_type_from_sql(sql_type: str) -> PortableLogicalType:
    up = sql_type.upper()
    if "BOOL" in up:
        return "BOOLEAN"
    if "INT" in up:
        return "INT64"
    if "REAL" in up or "DOUBLE" in up or "FLOAT" in up or "DEC" in up:
        return DecimalType(precision=18, scale=4)
    if "DATE" in up and "TIME" not in up:
        return "DATE32"
    return "UTF8_STRING"


def _build_parquet_esg_split(
    prof: SourceProfile,
    tbl: Any,
    hubs: list[DraftHubSpec],
    sats: list[DraftSatelliteSpec],
    triage: list[ColumnTriageEntry],
) -> None:
    col_by_name = {c.col_name: c for c in tbl.columns}
    specs: list[tuple[str, str, str, str, list[str], bool]] = [
        (
            "hub_esg_rating",
            "esg_rating_id",
            "ESG",
            "sat_esg_rating_core",
            [
                "filer_cik",
                "ticker",
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
            False,
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
            False,
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
            False,
        ),
        (
            "hub_mandate_rule",
            "mandate_rule_id",
            "MANDATE",
            "mas_mandate_rule_core",
            ["mandate_policy_name", "breach_severity"],
            True,
        ),
    ]
    for hub_name, bk_col, bkcc, sat_name, sat_cols, is_mas in specs:
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
                        bkcc_value=bkcc,
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
        sats.append(
            DraftSatelliteSpec(
                sat_name=sat_name,
                sat_kind="MAS" if is_mas else "STANDARD",
                parent_name=hub_name,
                child_dependent_keys=["breach_severity"] if is_mas else [],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=prof.source_id,
                        table_id=tbl.table_id,
                        col_mappings={c: c for c in sat_cols},
                    )
                ],
                attributes=[
                    DraftField(
                        name=c,
                        logical_type=_logical_type_from_sql(col_by_name[c].sql_type),
                        nullable=col_by_name[c].nullable,
                        is_pii=False,
                    )
                    for c in sat_cols
                ],
            )
        )
        for c in sat_cols:
            triage.append(
                ColumnTriageEntry(
                    table_id=tbl.table_id,
                    col_name=c,
                    disposition="MODELED",
                    target_ref=sat_name,
                )
            )
    # Isolated PII satellite on hub_proxy_ballot
    pii_col = col_by_name["steward_review_required"]
    sats.append(
        DraftSatelliteSpec(
            sat_name="sat_proxy_ballot_pii",
            sat_kind="PII",
            parent_name="hub_proxy_ballot",
            source_bindings=[
                DraftSourceBinding(
                    source_id=prof.source_id,
                    table_id=tbl.table_id,
                    col_mappings={"steward_review_required": "steward_review_required"},
                )
            ],
            attributes=[
                DraftField(
                    name="steward_review_required",
                    logical_type=_logical_type_from_sql(pii_col.sql_type),
                    nullable=pii_col.nullable,
                    is_pii=True,
                )
            ],
        )
    )
    triage.append(
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name="steward_review_required",
            disposition="MODELED",
            target_ref="sat_proxy_ballot_pii",
        )
    )


def _build_public_am_draft(
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

    holding_table_id = ""
    holding_src_id = ""
    filer_table_id = ""
    filer_src_id = ""

    for prof in profiles:
        for tbl in prof.tables:
            if tbl.table_name == "am_esg_stewardship_proxy":
                _build_parquet_esg_split(prof, tbl, hubs, sats, triage)
                continue
            hub_name, bk_col, bkcc = bk_by_table[tbl.table_name]
            if tbl.table_name == "holdings_13f":
                holding_table_id = tbl.table_id
                holding_src_id = prof.source_id
            elif tbl.table_name == "institutional_filers":
                filer_table_id = tbl.table_id
                filer_src_id = prof.source_id

            bks = [
                BusinessKeyPart(
                    table_id=tbl.table_id,
                    col_name=bk_col,
                    source_id=prof.source_id,
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value=bkcc,
                )
            ]
            s_binds = [
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
                                    bkcc_value=bkcc,
                                )
                            )
                            s_binds.append(
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
                    source_bindings=s_binds,
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

            excluded_from_sat = {bk_col}
            if tbl.table_name == "holdings_13f":
                excluded_from_sat.add("filer_cik")
                triage.append(
                    ColumnTriageEntry(
                        table_id=tbl.table_id,
                        col_name="filer_cik",
                        disposition="MODELED",
                        target_ref="hub_filer",
                    )
                )
            non_bk_cols = [c for c in tbl.columns if c.col_name not in excluded_from_sat]
            if non_bk_cols:
                is_mas = tbl.table_name in ("treasury_rates", "yield_curves")
                sat_kind = "MAS" if is_mas else "STANDARD"
                cdks = ["observation_date"] if is_mas else []
                chunk_size = 12 if len(non_bk_cols) > 12 and not is_mas else len(non_bk_cols)
                chunks = [
                    non_bk_cols[i : i + chunk_size] for i in range(0, len(non_bk_cols), chunk_size)
                ]
                for idx, chunk in enumerate(chunks):
                    suffix = f"_{idx + 1}" if len(chunks) > 1 else ""
                    sat_name = f"sat_{tbl.table_name}_core{suffix}"
                    sats.append(
                        DraftSatelliteSpec(
                            sat_name=sat_name,
                            sat_kind=sat_kind,
                            parent_name=hub_name,
                            child_dependent_keys=cdks,
                            source_bindings=[
                                DraftSourceBinding(
                                    source_id=prof.source_id,
                                    table_id=tbl.table_id,
                                    col_mappings={c.col_name: c.col_name for c in chunk},
                                )
                            ],
                            attributes=[
                                DraftField(
                                    name=c.col_name,
                                    logical_type=_logical_type_from_sql(c.sql_type),
                                    nullable=c.nullable,
                                    is_pii=bool(c.pii_tags),
                                )
                                for c in chunk
                            ],
                        )
                    )
                    for c in chunk:
                        triage.append(
                            ColumnTriageEntry(
                                table_id=tbl.table_id,
                                col_name=c.col_name,
                                disposition="MODELED",
                                target_ref=sat_name,
                            )
                        )

    if holding_table_id and filer_table_id:
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
                        source_id=holding_src_id,
                        table_id=holding_table_id,
                        col_mappings={"holding_id": "holding_id", "filer_cik": "filer_cik"},
                    )
                ],
            )
        )
        assert filer_src_id

    for planned_hub in (
        "hub_stewardship_engagement_case",
        "hub_macro_regime_signal",
        "hub_intraday_margin_haircut_schedule",
    ):
        hubs.append(
            DraftHubSpec(
                hub_name=planned_hub,
                key_tier="TIER_2_ENTERPRISE_SOR",
                is_planned_concept=True,
                business_keys=[],
                source_bindings=[],
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


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.asyncio
async def test_midbuild_e2e_public_am_checkpoint(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    am_dir = ws / "public_am"
    am_dir.mkdir(parents=True)
    for fname in (
        "am_sec_treasury_ibor.sqlite",
        "am_esg_stewardship_proxy.parquet",
        "am_stewardship_domain_notes.md",
        "am_custody_and_voting_guide.pdf",
    ):
        shutil.copy2(PUBLIC_AM / fname, am_dir / fname)

    state_holder: dict[str, Any] = {}

    def _script_selector(req: Any) -> MockTurnScript:
        if req.agent_id == "ontology-first-mate":
            if req.checkpoint is None:
                return MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [
                            ClarificationQuestion(
                                question_id="q-filer-bk",
                                concept_name="Institutional Filer",
                                title="Confirm SEC CIK authority",
                                prompt="Confirm SEC CIK is the global key for institutional filers.",
                                sample_rows=["filer_cik=0001166559"],
                                impact_if_locked="Fixes hub_filer key tier.",
                                recommended_answer="Confirm SEC_CIK Tier 1",
                            ).model_dump(mode="json")
                        ],
                        "summary": "Need steward confirmation for InstitutionalFiler BK.",
                    }
                )
            svc_ref: OpenDVService = state_holder["svc"]
            srcs = svc_ref.list_sources("public-am-e2e")
            sqlite_src = next(s for s in srcs if s.kind == "sqlite")
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [
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
                                    source_id=sqlite_src.source_id,
                                    table_id=f"{sqlite_src.source_id}:institutional_filers",
                                    col_name="filer_cik",
                                )
                            ],
                        ).model_dump(mode="json")
                    ],
                    "clarification_questions": [],
                    "summary": "Staged InstitutionalFiler proposal after Steward Hold.",
                }
            )
        if req.agent_id == "ship-vault":
            svc_ref = state_holder["svc"]
            profs = [
                SourceProfile.model_validate(
                    svc_ref.state_manager.get_source_record("public-am-e2e", s.source_id)["profile"]
                )
                for s in svc_ref.list_sources("public-am-e2e")
            ]
            profs = [p for p in profs if p.tables]
            bundle = svc_ref.get_ontology_bundle("public-am-e2e")
            draft = _build_public_am_draft(
                "public-am-e2e", bundle.ontology.ontology_version, profs
            )
            return MockTurnScript(structured_output=draft)
        if req.agent_id == "dv-semantic-critic":
            return MockTurnScript(
                structured_output=SemanticCriticVerdict(
                    score=94,
                    findings=["Clean DV2.0 structure"],
                    accepted_waiver_ids=["WAIV-AP004"],
                )
            )
        if req.agent_id == "dv-horizon-critic":
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(
                    score=91,
                    classification="REASONABLY_STABLE",
                    absorbed_natively=["Nullable CUSIP in securities satellite"],
                    bucket_c_watchlist=["Track MAS observation dates"],
                )
            )
        return MockTurnScript(text_output="{}")

    harness = DeterministicMockHarness(selector=_script_selector)
    svc = OpenDVService(ws, state_dir=tmp_path / "state", harness=harness)
    state_holder["svc"] = svc

    # Steps 1-3: Project init, 4 sources attached, clustering, PDF scan consent
    proj = svc.init_project(ProjectInitReq(project_id="public-am-e2e", domain_namespace="asset_mgmt"))
    initial_rev = proj.active_ontology_rev_id
    assert initial_rev is not None

    sqlite_sum = svc.attach_source(
        "public-am-e2e",
        SourceAttachReq(kind="sqlite", uri_or_filename="public_am/am_sec_treasury_ibor.sqlite"),
    )
    pq_sum = svc.attach_source(
        "public-am-e2e",
        SourceAttachReq(kind="parquet", uri_or_filename="public_am/am_esg_stewardship_proxy.parquet"),
    )
    md_sum = svc.attach_source(
        "public-am-e2e",
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )
    pdf_sum = svc.attach_source(
        "public-am-e2e",
        SourceAttachReq(kind="pdf", uri_or_filename="public_am/am_custody_and_voting_guide.pdf"),
    )
    assert pq_sum.table_count == 1
    tables_idx = svc.list_tables_index("public-am-e2e")
    assert len(tables_idx) == 7

    preview = svc.preview_table(
        "public-am-e2e", sqlite_sum.source_id, f"{sqlite_sum.source_id}:institutional_filers"
    )
    assert preview.total_rows > 0

    pdf_map = svc.get_document_map("public-am-e2e", pdf_sum.source_id)
    assert pdf_map.page_count == 6
    try:
        svc.get_document_pages("public-am-e2e", pdf_sum.source_id, page_start=1, page_end=6)
        raise AssertionError("Expected UNREDACTED_SCAN_BLOCKED on PDF page 4")
    except OpenDVError as err:
        assert err.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    svc.approve_scan_pages(
        "public-am-e2e",
        pdf_sum.source_id,
        ScanApprovalReq(page_numbers=[4], rationale="Approved page 4 table scan for extraction."),
    )
    pages = svc.get_document_pages("public-am-e2e", pdf_sum.source_id, page_start=1, page_end=6)
    assert len(pages) == 6

    # Step 4: Discovery Hold -> Steward Answer -> Staged Proposal (never autonomous commit)
    run4 = await svc.start_ontology_construction("public-am-e2e", OntologyConstructReq())
    assert run4.status == "HOLD_CHECKPOINTED"
    assert len(run4.pending_questions) == 1
    resumed4 = await svc.answer_ontology_hold(
        "public-am-e2e",
        run4.run_id,
        HoldAnswersReq(answers={"q-filer-bk": "Confirm SEC_CIK Tier 1"}),
    )
    assert resumed4.status == "COMPLETED"
    assert svc.get_project("public-am-e2e").active_ontology_rev_id == initial_rev

    # Step 5: FIBO QA, citation resolution, validate, commit, export TTL
    fibo_resp = svc.fibo_qa(FiboQueryReq(query="Institutional Investor", top_k=3))
    assert fibo_resp.matches

    md_pages = svc.get_document_pages("public-am-e2e", md_sum.source_id, page_start=1, page_end=1)
    quote = md_pages[0].masked_text.splitlines()[0].strip()
    cit_resp = svc.resolve_citation_span(
        "public-am-e2e",
        CitationResolveReq(doc_id=md_sum.source_id, page_number=1, verbatim_quote=quote),
    )
    assert cit_resp.matched

    staged_ids = [
        c.candidate_id for c in svc.get_ontology_bundle("public-am-e2e").staged_candidates
    ]
    assert staged_ids
    commit_ops = [
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:institutional_filers",
                    col_name="filer_cik",
                )
            ],
            source_citations=[
                DocumentCitationRef(
                    doc_id=md_sum.source_id,
                    page_number=1,
                    verbatim_quote=quote,
                )
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:institutional_filers",
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:holdings_13f",
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:issuers",
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:securities",
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:treasury_rates",
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
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:yield_curves",
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
                    source_id=pq_sum.source_id,
                    table_id=f"{pq_sum.source_id}:am_esg_stewardship_proxy",
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
                    source_id=pq_sum.source_id,
                    table_id=f"{pq_sum.source_id}:am_esg_stewardship_proxy",
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
                    source_id=pq_sum.source_id,
                    table_id=f"{pq_sum.source_id}:am_esg_stewardship_proxy",
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
                    source_id=pq_sum.source_id,
                    table_id=f"{pq_sum.source_id}:am_esg_stewardship_proxy",
                    col_name="mandate_rule_id",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:StewardshipEngagementCase",
            concept_name="Stewardship Engagement Case",
            description="Planned engagement case triggered by mandate breach.",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            source_citations=[
                DocumentCitationRef(
                    doc_id=md_sum.source_id,
                    page_number=1,
                    verbatim_quote="StewardshipEngagementCase",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:MacroRegimeSignal",
            concept_name="Macro Regime Signal",
            description="Planned macro curve inversion signal.",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            source_citations=[
                DocumentCitationRef(
                    doc_id=md_sum.source_id,
                    page_number=1,
                    verbatim_quote="MacroRegimeSignal",
                )
            ],
        ),
        AddConcept(
            concept_uri="urn:asset_mgmt:IntradayMarginHaircutSchedule",
            concept_name="Intraday Margin Haircut Schedule",
            description="Planned collateral haircut schedule during stress regimes.",
            key_authority_tier="TIER_2_ENTERPRISE_SOR",
            source_citations=[
                DocumentCitationRef(
                    doc_id=md_sum.source_id,
                    page_number=1,
                    verbatim_quote="IntradayMarginHaircutSchedule",
                )
            ],
        ),
    ]
    val_resp = svc.validate_ontology("public-am-e2e", OntologyValidateReq(operations=commit_ops))
    assert val_resp.valid
    rev5 = svc.commit_ontology(
        "public-am-e2e",
        OntologyCommitReq(
            operations=commit_ops,
            base_revision_id=initial_rev,
            rationale="Steward commits grounded InstitutionalFiler ontology.",
            committed_proposal_ids=staged_ids,
        ),
    )
    assert rev5.ontology_version == "0.1.0"
    ttl_text, media = svc.export_ontology("public-am-e2e", fmt="ttl")
    assert media == "text/turtle"
    assert "Institutional Filer" in ttl_text

    # Step 6: Build Vault with DuckDbProverAdapter, verify, Guided UX
    run6 = await svc.build_vault("public-am-e2e", VaultBuildReq(policy="balanced"))
    assert run6.status == "COMPLETED"
    assert len(svc.list_vault_attempts("public-am-e2e", run6.run_id)) >= 1
    verify_resp = svc.verify_vault("public-am-e2e")
    assert verify_resp.passed
    vault_ir = svc.get_vault_ir("public-am-e2e")
    hub_by_name = {h.hub_name: h for h in vault_ir.hubs}
    assert {
        "hub_esg_rating",
        "hub_carbon_disclosure",
        "hub_proxy_ballot",
        "hub_mandate_rule",
    }.issubset(hub_by_name)
    for planned_hub in (
        "hub_stewardship_engagement_case",
        "hub_macro_regime_signal",
        "hub_intraday_margin_haircut_schedule",
    ):
        assert planned_hub in hub_by_name
        assert hub_by_name[planned_hub].is_planned_concept is True
    assert any(
        s.sat_name == "sat_proxy_ballot_pii" and s.sat_kind == "PII" for s in vault_ir.satellites
    )
    guided = svc.get_guided_view("public-am-e2e", run6.run_id)
    assert guided.readiness_brief.certification_label == "CERTIFIED"
    assert len(guided.entities) >= 13

    # Step 7: Compile targets, materialize DuckDB & Parquet, query read-only, delete & regenerate byte-identical
    for tid in ("duckdb_local", "postgres", "iceberg", "parquet", "dbt"):
        compiled = svc.compile_target(
            "public-am-e2e",
            StorageCompileReq(target_id=tid, verify_runtime=(tid == "duckdb_local")),
        )
        assert compiled.artifacts
        assert compiled.manifest.eval_receipts

    duck_mig = svc.migrate_target("public-am-e2e", StorageTargetReq(target_id="duckdb_local"))
    assert duck_mig.verified and duck_mig.rows_loaded > 0
    pq_mig = svc.migrate_target("public-am-e2e", StorageTargetReq(target_id="parquet"))
    assert pq_mig.verified

    q_res = svc.query_target(
        "public-am-e2e",
        SqlQueryReq(sql="SELECT COUNT(*) AS cnt FROM raw_vault.hub_filer"),
    )
    assert q_res.row_count == 1
    assert int(q_res.rows[0][0] or 0) > 0

    try:
        svc.query_target("public-am-e2e", SqlQueryReq(sql="DROP TABLE raw_vault.hub_filer"))
        raise AssertionError("Expected read-only query guard rejection")
    except OpenDVError as err:
        assert err.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Byte-identical regenerate check on postgres & iceberg targets
    for det_tid in ("postgres", "iceberg"):
        tdir = svc.state_manager.targets_dir("public-am-e2e") / det_tid
        before_hashes = _tree_digests(tdir)
        shutil.rmtree(tdir)
        svc.regenerate_target("public-am-e2e", StorageTargetReq(target_id=det_tid))
        after_hashes = _tree_digests(tdir)
        assert before_hashes == after_hashes

    # Portable full_demo export + rename import roundtrip preserves queryability
    bundle_path, _ = svc.export_bundle("public-am-e2e", mode="full_demo")
    imp = svc.import_bundle(bundle_path, conflict="rename")
    assert imp.project_id == "public-am-e2e-copy"
    copy_q = svc.query_target(
        "public-am-e2e-copy",
        SqlQueryReq(sql="SELECT COUNT(*) AS cnt FROM raw_vault.hub_filer"),
    )
    assert copy_q.rows == q_res.rows
