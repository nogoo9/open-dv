"""Phase V2-6 M3 registry tests (`UAT-V26-E2E-03`): 20/20 scenarios & 12/12 real traps."""

from __future__ import annotations

import json
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from opendv.cli.main import app as cli_app
from opendv.cli.mcp_stdio import MCP_TOOL_NAMES
from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    DocumentCitationRef,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ir_kernel import execute_hash_recipe
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    GraphAOntology,
    KeyAuthorityPattern,
)
from opendv.models.ontology_deltas import (
    AddConcept,
    AddKeyAuthorityPattern,
    SetConceptKeyAuthority,
)
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
    PredicateLeaf,
)
from opendv.models.vault_ir_certified import (
    CertifiedVaultIR,
    HashRecipeSpec,
    MartViewSpec,
    StagingViewSpec,
)
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    ConceptMemoryUpdate,
    HoldAnswersReq,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyValidateReq,
    ProjectInitReq,
    ScanApprovalReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import (
    HorizonActionReq,
    StorageTargetReq,
    VaultBuildReq,
    WaiverSubmitReq,
)
from opendv.service.app_service import OpenDVService
from opendv.service.guided_translator import contains_forbidden_jargon
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256
from opendv.storage.prover import DuckDbProverAdapter, decollide_masked_pii
from opendv.storage.prover.edge_batches import run_adversarial_batches
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.storage.sql_compiler.dialect_hashing import compile_hash_recipe_sql, zero_key_hex_sql
from opendv.verifier import (
    VerifierContext,
    evaluate_v6b_accounting,
    lint_ontology_antipatterns,
    lint_vault_antipatterns,
    run_l1_codemods,
    run_v1_to_v6_gates,
    verify_v2_hash_and_bkcc,
)
from tests.api._api_test_helpers import (
    build_wave1_am_draft,
    build_wave2_am_draft,
    build_wave2_commit_ops,
    copy_public_am_wave1,
    copy_public_am_wave2,
    load_project_profiles,
    wait_run_via_sse,
)
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.helpers import DeterministicMockHarness
from tests.parity._v2_parity_helpers import (
    GOLDEN_BUNDLE_PATH,
    MD_STEWARDSHIP_QUOTE,
    PDF_EXCEPTION_QUOTE,
    TRAP_DUCKDB,
    IsolatedWorkspace,
    build_clean_trap_ir,
    build_wave2_hold_draft,
    commit_wave1_v1_ontology,
    load_trap_profiles_and_rows,
    setup_isolated_env,
    snapshot_dir_mtimes,
)


def _setup_w1_svc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_id: str = "scen-proj",
    selector: Callable[[Any], Any] | None = None,
) -> tuple[IsolatedWorkspace, OpenDVService, dict[str, str]]:
    env = setup_isolated_env(tmp_path, monkeypatch)
    pub_dir = copy_public_am_wave1(env.ws)
    copy_public_am_wave2(env.ws)
    harness = DeterministicMockHarness(selector=selector)
    svc = env.make_service(harness=harness)
    svc.init_project(ProjectInitReq(project_id=project_id, domain_namespace="asset_management"))
    s_sql = svc.attach_source(
        project_id,
        SourceAttachReq(kind="sqlite", uri_or_filename=str(pub_dir / "am_sec_treasury_ibor.sqlite")),
    )
    s_pq = svc.attach_source(
        project_id,
        SourceAttachReq(kind="parquet", uri_or_filename=str(pub_dir / "am_esg_stewardship_proxy.parquet")),
    )
    s_md = svc.attach_source(
        project_id,
        SourceAttachReq(kind="markdown", uri_or_filename=str(pub_dir / "am_stewardship_domain_notes.md")),
    )
    s_pdf = svc.attach_source(
        project_id,
        SourceAttachReq(kind="pdf", uri_or_filename=str(pub_dir / "am_custody_and_voting_guide.pdf")),
    )
    return env, svc, {
        "sqlite": s_sql.source_id,
        "parquet": s_pq.source_id,
        "markdown": s_md.source_id,
        "pdf": s_pdf.source_id,
        "pub_dir": str(pub_dir),
    }


def _w1_selector(svc_box: list[OpenDVService], pid: str) -> Callable[[Any], Any]:
    def _sel(req: Any) -> Any:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc_box[0], pid)
            return build_wave1_am_draft(pid, "1.0.0", profs).model_dump(mode="json")
        if req.agent_id == "dv-semantic-critic":
            return {"score": 96, "findings": [], "accepted_waiver_ids": []}
        if req.agent_id == "dv-horizon-critic":
            return {
                "score": 94,
                "classification": "REASONABLY_STABLE",
                "absorbed_natively": [],
                "bucket_a_ops": [],
                "bucket_b_questions": [],
                "bucket_c_watchlist": [],
            }
        return {}

    return _sel


async def _scen_01(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            qa = await client.post("/api/v1/ontology/fibo-qa", json={"query": "Issuer", "top_k": 3})
            assert qa.status_code == 200 and qa.json()["matches"]
            top = qa.json()["matches"][0]
            cli_qa = CliRunner().invoke(cli_app, ["--json", "--api-url", url, "ontology", "fibo-qa", "--query", "Holding"], env=env.subprocess_env())
            assert cli_qa.exit_code == 0 and json.loads(cli_qa.stdout)["data"]["matches"]
            rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
            tbl_id = f"{sids['sqlite']}:issuers"
            r = await client.post(
                "/api/v1/projects/scen-proj/ontology/commit",
                json={
                    "base_revision_id": rev0,
                    "rationale": "SCEN-01 exact FIBO match commit without waivers",
                    "operations": [
                        {
                            "op_id": "op_s01",
                            "op_type": "AddConcept",
                            "concept_uri": "urn:asset_mgmt:ExactIssuer",
                            "concept_name": "ExactIssuer",
                            "description": "Exact FIBO Issuer",
                            "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                            "external_mappings": [
                                {
                                    "standard_uri": top["concept_uri"],
                                    "reference_pack_id": "fibo_core",
                                    "reference_pack_sha256": CANONICAL_FIBO_CORE_SHA256,
                                    "mapping_type": "skos:exactMatch",
                                    "confidence": 0.92,
                                }
                            ],
                            "table_bindings": [{"source_id": sids["sqlite"], "table_id": tbl_id, "col_name": "cik"}],
                            "rationale": "Exact FIBO mapping",
                        }
                    ],
                },
            )
            assert r.status_code == 200, r.text


async def _scen_02(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            qa = (await client.post("/api/v1/ontology/fibo-qa", json={"query": "Treasury Yield Curve", "top_k": 3})).json()
            assert qa["matches"]
            match_uri = qa["matches"][0]["concept_uri"]
            rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
            ops = [
                {
                    "op_id": "op_s02",
                    "op_type": "AddConcept",
                    "concept_uri": "urn:asset_mgmt:TreasuryRateClose",
                    "concept_name": "TreasuryRateClose",
                    "description": "Domain specialized Treasury Rate close match.",
                    "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
                    "external_mappings": [
                        {
                            "standard_uri": match_uri,
                            "reference_pack_id": "fibo_core",
                            "reference_pack_sha256": CANONICAL_FIBO_CORE_SHA256,
                            "mapping_type": "skos:closeMatch",
                            "confidence": 0.72,
                        }
                    ],
                    "table_bindings": [{"source_id": sids["sqlite"], "table_id": f"{sids['sqlite']}:treasury_rates", "col_name": "series_id"}],
                    "rationale": "SCEN-02 closeMatch TreasuryRate mapping",
                }
            ]
            v = await client.post("/api/v1/projects/scen-proj/ontology/validate", json={"base_revision_id": rev0, "operations": ops})
            assert v.status_code == 200 and v.json()["valid"] is True
            c = await client.post(
                "/api/v1/projects/scen-proj/ontology/commit",
                json={"base_revision_id": rev0, "operations": ops, "rationale": "SCEN-02 closeMatch commit"},
            )
            assert c.status_code == 200, c.text


async def _scen_03(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    pub_dir = Path(sids["pub_dir"])
    w2_alloc = svc.attach_source("scen-proj", SourceAttachReq(kind="csv", uri_or_filename=str(pub_dir / "prime_allocations_w2.csv")))
    rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
    res = svc.commit_ontology(
        "scen-proj",
        OntologyCommitReq(
            base_revision_id=rev0,
            low_confidence_grounding_waiver=False,
            rationale="SCEN-03 Beyond-FIBO empirical concept commit",
            operations=[
                AddConcept(
                    op_id="op_s03",
                    concept_uri="urn:asset_mgmt:ClearingAllocationBucket",
                    concept_name="ClearingAllocationBucket",
                    description="Proprietary prime brokerage allocation block.",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    external_mappings=[],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id=w2_alloc.source_id,
                            table_id=f"{w2_alloc.source_id}:prime_allocations_w2",
                            col_name="block_id",
                        )
                    ],
                    rationale="SCEN-03 novel empirical concept",
                )
            ],
        ),
    )
    assert res.ontology_version == "0.1.0"


async def _scen_04(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    r_md = svc.resolve_citation_span(
        "scen-proj",
        CitationResolveReq(doc_id=sids["markdown"], page_number=1, verbatim_quote=MD_STEWARDSHIP_QUOTE, occurrence=1),
    )
    rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
    res = svc.commit_ontology(
        "scen-proj",
        OntologyCommitReq(
            base_revision_id=rev0,
            rationale="SCEN-04 document-only planned entity grounded by citation",
            operations=[
                AddConcept(
                    op_id="op_s04",
                    concept_uri="urn:asset_mgmt:StewardshipEngagementCase",
                    concept_name="StewardshipEngagementCase",
                    description="Document-only policy entity zero physical table.",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    table_bindings=[],
                    source_citations=[
                        DocumentCitationRef(
                            doc_id=sids["markdown"],
                            page_number=1,
                            verbatim_quote=MD_STEWARDSHIP_QUOTE,
                            occurrence=1,
                            char_start=r_md.char_start,
                            char_end=r_md.char_end,
                            content_sha256=r_md.resolved_sha256,
                        )
                    ],
                    rationale="SCEN-04 document-only entity",
                )
            ],
        ),
    )
    assert res.ontology_version == "0.1.0"


async def _scen_05(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="SCEN-05 scan approval"))
    r_pdf = svc.resolve_citation_span(
        "scen-proj",
        CitationResolveReq(doc_id=sids["pdf"], page_number=5, verbatim_quote=PDF_EXCEPTION_QUOTE, occurrence=2),
    )
    rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
    r1 = svc.commit_ontology(
        "scen-proj",
        OntologyCommitReq(
            base_revision_id=rev0,
            rationale="SCEN-05 initial local tier concept",
            operations=[
                AddConcept(
                    op_id="op_s05_a",
                    concept_uri="urn:asset_mgmt:CustodyAccount",
                    concept_name="CustodyAccount",
                    description="Hybrid custody account.",
                    key_authority_tier="TIER_3_LOCAL_SCOPED",
                    table_bindings=[ConceptTableBinding(source_id=sids["sqlite"], table_id=f"{sids['sqlite']}:issuers", col_name="cik")],
                    source_citations=[DocumentCitationRef(doc_id=sids["pdf"], page_number=5, verbatim_quote=PDF_EXCEPTION_QUOTE, occurrence=2, char_start=r_pdf.char_start, char_end=r_pdf.char_end, content_sha256=r_pdf.resolved_sha256)],
                    rationale="Initial hybrid concept",
                )
            ],
        ),
    )
    r2 = svc.commit_ontology(
        "scen-proj",
        OntologyCommitReq(
            base_revision_id=r1.revision_id,
            rationale="SCEN-05 upgrade BK authority to Tier 1 global LEI",
            operations=[
                SetConceptKeyAuthority(
                    op_id="op_s05_b",
                    concept_uri="urn:asset_mgmt:CustodyAccount",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_default="GLOBAL",
                    rationale="Upgrade to global LEI",
                ),
                AddKeyAuthorityPattern(
                    op_id="op_s05_c",
                    concept_uri="urn:asset_mgmt:CustodyAccount",
                    pattern=KeyAuthorityPattern(tier="TIER_1_GLOBAL_STANDARD", authority_name="GLEIF_LEI", column_patterns=["lei_code"], bkcc_policy="GLOBAL"),
                    rationale="Add GLEIF pattern",
                ),
            ],
        ),
    )
    assert r2.ontology_version != r1.ontology_version


async def _scen_06(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [0]

    def _s06_sel(req: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1 and "Clarify" not in req.prompt and "q_poly" not in req.prompt:
            return {
                "proposed_ops": [],
                "clarification_questions": [
                    ClarificationQuestion(
                        question_id="q_poly",
                        concept_name="Security",
                        title="Disambiguate ticker vs ISIN",
                        prompt="Use ISIN (TIER_1) or local ticker?",
                        sample_rows=["AAPL | US0378331005"],
                        impact_if_locked="Sets Security primary identifier.",
                        recommended_answer="ISIN",
                    ).model_dump(mode="json")
                ],
                "staged_candidate_ids": [],
                "summary": "Awaiting polysemic clarification.",
            }
        return {"proposed_ops": [], "clarification_questions": [], "staged_candidate_ids": [], "summary": "Resolved."}

    _, svc, _ = _setup_w1_svc(tmp_path, monkeypatch, selector=_s06_sel)
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            svc.upsert_concept_memory("scen-proj", ConceptMemoryUpdate(term="Security", state="PINNED", concept_uri="urn:asset_mgmt:Security"))
            run = await client.post("/api/v1/projects/scen-proj/ontology/construct", json={"discovery_mode": "incremental_new", "hint": "disambiguate"})
            assert run.status_code in (200, 202)
            rid = run.json()["run_id"]
            await wait_run_via_sse(client, f"/api/v1/projects/scen-proj/ontology/runs/{rid}/events")
            st1 = (await client.get(f"/api/v1/projects/scen-proj/ontology/runs/{rid}")).json()
            assert st1["status"] == "HOLD_CHECKPOINTED"
            ans = await client.post(f"/api/v1/projects/scen-proj/ontology/runs/{rid}/answers", json={"answers": {"q_poly": "ISIN"}})
            assert ans.status_code in (200, 202)
            await wait_run_via_sse(client, f"/api/v1/projects/scen-proj/ontology/runs/{rid}/events")
            assert (await client.get(f"/api/v1/projects/scen-proj/ontology/runs/{rid}")).json()["status"] == "COMPLETED"


async def _scen_07(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, _ = _setup_w1_svc(tmp_path, monkeypatch)
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            rev0 = svc.get_project("scen-proj").active_ontology_rev_id or ""
            ops = [
                {
                    "op_id": "op_s07",
                    "op_type": "AddConcept",
                    "concept_uri": "urn:asset_mgmt:UngroundedEntity",
                    "concept_name": "UngroundedEntity",
                    "description": "No bindings or FIBO mapping",
                    "key_authority_tier": "TIER_3_LOCAL_SCOPED",
                    "rationale": "Test fail-closed Path B2",
                }
            ]
            blocked = await client.post(
                "/api/v1/projects/scen-proj/ontology/commit",
                json={"base_revision_id": rev0, "operations": ops, "rationale": "Try without waiver"},
            )
            assert blocked.status_code == 422
            assert blocked.json()["error_code"] == ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED.value
            allowed = await client.post(
                "/api/v1/projects/scen-proj/ontology/commit",
                json={
                    "base_revision_id": rev0,
                    "operations": ops,
                    "rationale": "Admin signed waiver approving placeholder entity",
                    "waive_quarantine": True,
                },
            )
            assert allowed.status_code == 200, allowed.text


async def _scen_08(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch)
    pub_dir = Path(sids["pub_dir"])
    with pytest.raises(OpenDVError) as exc_info:
        svc.get_document_pages("scen-proj", sids["pdf"], page_start=4, page_end=4)
    assert exc_info.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="Local-only steward consent"))
    pages = svc.get_document_pages("scen-proj", sids["pdf"], page_start=4, page_end=4)
    assert pages[0].scan_approved is True

    w2_otc = svc.attach_source("scen-proj", SourceAttachReq(kind="csv", uri_or_filename=str(pub_dir / "wave2_ambiguous_otc_collateral.csv")))
    rec = svc.state_manager.get_source_record("scen-proj", w2_otc.source_id)
    prof_dump = json.dumps(rec["profile"].model_dump(mode="json"))
    assert "<MASKED_PII>" in prof_dump and "021000021" not in prof_dump


async def _scen_09(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_w1_selector(svc_box, "scen-proj"))
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="SCEN-09 approve scan"))
    rev1_id = commit_wave1_v1_ontology(
        svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"]
    )
    await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    with pytest.raises(OpenDVError) as exc_info:
        svc.commit_ontology(
            "scen-proj",
            OntologyCommitReq.model_validate(
                {
                    "base_revision_id": rev1_id,
                    "rationale": "Try breaking locked Issuer hub BK",
                    "operations": [{"op_id": "op_brk", "op_type": "DeleteConcept", "concept_uri": "urn:asset_mgmt:Issuer", "rationale": "Delete locked hub"}],
                }
            ),
        )
    assert exc_info.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION
    pub_dir = Path(sids["pub_dir"])
    for kind, fn in (("parquet", "wave2_eu_custody_lei_and_drift.parquet"), ("csv", "wave2_bitemporal_nav_restatements.csv"), ("csv", "wave2_ambiguous_otc_collateral.csv"), ("csv", "prime_allocations_w2.csv")):
        svc.attach_source("scen-proj", SourceAttachReq(kind=kind, uri_or_filename=str(pub_dir / fn)))
    srcs = [s.model_dump(mode="json") for s in svc.list_sources("scen-proj")]
    rev2 = svc.commit_ontology(
        "scen-proj",
        OntologyCommitReq(
            base_revision_id=rev1_id,
            operations=list(build_wave2_commit_ops(srcs)),
            rationale="SCEN-09 Wave 2 additive evolution v1.1.0",
            low_confidence_grounding_waiver=True,
            grounding_rationale="Wave 2 approved",
            waive_quarantine=True,
        ),
    )
    assert rev2.ontology_version == "1.1.0"


async def _mod_01(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_w1_selector(svc_box, "scen-proj"))
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="MOD-01 approve scan"))
    commit_wave1_v1_ontology(
        svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"]
    )
    run = await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    assert run.status == "COMPLETED"
    ver = svc.verify_vault("scen-proj")
    assert ver.passed is True


async def _mod_02(_tmp_path: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    profs, rows = load_trap_profiles_and_rows()
    ballot_tbl = "trap_src:trap_ballots_mas"
    mgr_tbl = "trap_src:trap_manager_eff"
    draft = DraftVaultIR(
        project_id="mod02",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[BusinessKeyPart(table_id=mgr_tbl, col_name="account_id", source_id="trap_src", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="CRM")],
                source_bindings=[DraftSourceBinding(source_id="trap_src", table_id=mgr_tbl, col_mappings={"account_id": "account_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_manager",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id=mgr_tbl, col_name="portfolio_manager_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[DraftSourceBinding(source_id="trap_src", table_id=mgr_tbl, col_mappings={"portfolio_manager_id": "portfolio_manager_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_ballot",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[BusinessKeyPart(table_id=ballot_tbl, col_name="ballot_id", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")],
                source_bindings=[DraftSourceBinding(source_id="trap_src", table_id=ballot_tbl, col_mappings={"ballot_id": "ballot_id"})],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_acct_mgr",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_account", role_name="ACCOUNT", fk_col_name="hk_account", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_manager", role_name="MANAGER", fk_col_name="hk_manager", source_col_names=["portfolio_manager_id"]),
                ],
                source_bindings=[DraftSourceBinding(source_id="trap_src", table_id=mgr_tbl, col_mappings={"account_id": "account_id", "portfolio_manager_id": "portfolio_manager_id"})],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="mas_ballots",
                sat_kind="MAS",
                parent_name="hub_ballot",
                child_dependent_keys=["proposal_seq"],
                source_bindings=[DraftSourceBinding(source_id="trap_src", table_id=ballot_tbl, col_mappings={"proposal_seq": "proposal_seq", "vote_cast_code": "vote_cast_code"})],
                attributes=[
                    DraftField(name="proposal_seq", logical_type="INT32", nullable=True, is_pii=False),
                    DraftField(name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                ],
            )
        ],
    )
    unpromoted = build_clean_trap_ir(profs, unpromote_ballot_mas_to_sat=True)
    assert DuckDbProverAdapter().prove(unpromoted, profs, source_rows_by_table=rows).dyn_results["DV-DYN-004"] is False
    _, fixed, _ = run_l1_codemods(draft, profiles=profs, fk_turnover_by_table={mgr_tbl: {"portfolio_manager_id": 0.67}})
    mas_sat = next(s for s in fixed.satellites if s.sat_kind == "MAS")
    assert any(a.name == "proposal_seq" and a.nullable is False for a in mas_sat.attributes)
    assert any(s.sat_kind == "ESAT" and s.driving_keys for s in fixed.satellites)
    clean_ir = build_clean_trap_ir(profs)
    report = DuckDbProverAdapter().prove(clean_ir, profs, source_rows_by_table=rows)
    assert len(report.dyn_results) == 15 and all(report.dyn_results.values())


async def _mod_03(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    env, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_w1_selector(svc_box, "scen-proj"))
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="MOD-03 approve scan"))
    commit_wave1_v1_ontology(svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))

    ir = build_clean_trap_ir()
    god_sat = ir.satellites[0].model_copy(
        update={
            "attributes": [DraftField(name=f"col_{i}", logical_type="UTF8_STRING", nullable=True, is_pii=False) for i in range(25)]
        }
    )
    smelly_ir = ir.model_copy(update={"satellites": [god_sat, *ir.satellites[1:]]})
    diags = lint_vault_antipatterns(smelly_ir, policy="balanced")
    ap5 = next(d for d in diags if d.rule_id == "DV-AP-005")
    self_dealing = AntiPatternJustification(
        waiver_id="W-AGENT", rule_id="DV-AP-005", entity_name=ap5.entity_name, rationale="Agent self-waive", actor="agent", reviewed_by_v7=False
    )
    f_unrev, w_unrev, _ = evaluate_v6b_accounting(diags, justifications=[self_dealing])
    assert w_unrev == 0
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            w_resp = await client.post(
                "/api/v1/projects/scen-proj/vault/waivers",
                json={"waiver_id": "W-HUMAN", "rule_id": "DV-AP-005", "entity_name": ap5.entity_name, "rationale": "Steward verified uniform volatility across columns"},
            )
            assert w_resp.status_code == 200
            waived = w_resp.json()["waivers"] if "waivers" in w_resp.json() else svc.state_manager.list_revisions("scen-proj", rev_kind="vault_waiver")
            assert len(waived) >= 1
    reviewed = self_dealing.model_copy(update={"reviewed_by_v7": True})
    f_rev, w_rev, _ = evaluate_v6b_accounting(diags, justifications=[reviewed])
    assert w_rev == 1 and f_rev < f_unrev


async def _mod_04(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []

    def _sel(req: Any) -> Any:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc_box[0], "scen-proj")
            if "q_otc_key" in req.prompt:
                return build_wave1_am_draft("scen-proj", "1.0.0", profs).model_dump(mode="json")
            return build_wave1_am_draft("scen-proj", "1.0.0", profs).model_copy(
                update={
                    "pending_clarifications": [
                        ClarificationQuestion(
                            question_id="q_otc_key",
                            concept_name="Collateral",
                            title="Confirm key for Collateral",
                            prompt="Use uti_id or isda_master_id?",
                            sample_rows=["UTI-1 | ISDA-1"],
                            impact_if_locked="Freezes Hub key.",
                            recommended_answer="uti_id",
                        )
                    ]
                }
            ).model_dump(mode="json")
        if req.agent_id == "dv-semantic-critic":
            return {"score": 96, "findings": [], "accepted_waiver_ids": []}
        return {"score": 94, "classification": "REASONABLY_STABLE", "absorbed_natively": [], "bucket_a_ops": [], "bucket_b_questions": [], "bucket_c_watchlist": []}

    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_sel)
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="MOD-04 approve scan"))
    commit_wave1_v1_ontology(svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    run = await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    assert run.status == "HOLD_CHECKPOINTED"
    gv = svc.get_guided_view("scen-proj", run.run_id)
    assert not contains_forbidden_jargon(json.dumps(gv.model_dump(mode="json")))
    resumed = await svc.submit_vault_decisions("scen-proj", run.run_id, HoldAnswersReq(answers={"q_otc_key": "uti_id"}))
    assert resumed.status == "COMPLETED"


async def _mod_05(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    h_calls = [0]

    def _sel(req: Any) -> Any:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc_box[0], "scen-proj")
            return build_wave1_am_draft("scen-proj", "1.0.0", profs).model_dump(mode="json")
        if req.agent_id == "dv-semantic-critic":
            return {"score": 96, "findings": [], "accepted_waiver_ids": []}
        h_calls[0] += 1
        if h_calls[0] == 1:
            return {
                "score": 74,
                "classification": "HUMAN_REVIEW_REQUIRED",
                "absorbed_natively": [],
                "bucket_a_ops": [],
                "bucket_b_questions": [
                    {
                        "question_id": "hq_brk_02",
                        "brk_code": "BRK-02",
                        "title": "Confirm CDK resilience",
                        "prompt": "Confirm proposal_seq CDK grain covers multi-asset future ballots.",
                        "options": [
                            {"option_id": "opt_keep", "label": "Confirm CDK", "description": "Keep proposal_seq CDK", "implications": "Low risk", "operator_hint": None}
                        ],
                        "impact_summary": "Protects future Wave ballots.",
                    }
                ],
                "bucket_c_watchlist": ["Monitor future ballot schemas."],
            }
        return {"score": 94, "classification": "REASONABLY_STABLE", "absorbed_natively": [], "bucket_a_ops": [], "bucket_b_questions": [], "bucket_c_watchlist": []}

    _, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_sel)
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="MOD-05 approve scan"))
    commit_wave1_v1_ontology(svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    run = await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    assert run.status == "HOLD_CHECKPOINTED"
    steered = await svc.submit_horizon_steering("scen-proj", run.run_id, HorizonActionReq(action="steer", answers={"hq_brk_02": "opt_keep"}))
    assert steered.status == "COMPLETED"


async def _mod_06(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    wave_box = [1]

    def _sel(req: Any) -> Any:
        if req.agent_id == "ship-vault":
            profs = load_project_profiles(svc_box[0], "scen-proj")
            return (build_wave1_am_draft("scen-proj", "1.0.0", profs) if wave_box[0] == 1 else build_wave2_am_draft("scen-proj", "1.1.0", profs)).model_dump(mode="json")
        if req.agent_id == "dv-semantic-critic":
            return {"score": 96, "findings": [], "accepted_waiver_ids": []}
        return {"score": 94, "classification": "REASONABLY_STABLE", "absorbed_natively": [], "bucket_a_ops": [], "bucket_b_questions": [], "bucket_c_watchlist": []}

    env, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, selector=_sel)
    svc_box.append(svc)
    svc.approve_scan_pages("scen-proj", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="MOD-06 approve scan"))
    rev1_id = commit_wave1_v1_ontology(svc, "scen-proj", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    svc.migrate_target("scen-proj", StorageTargetReq(target_id="duckdb_local"))

    pub_dir = Path(sids["pub_dir"])
    for kind, fn in (("parquet", "wave2_eu_custody_lei_and_drift.parquet"), ("csv", "wave2_bitemporal_nav_restatements.csv"), ("csv", "wave2_ambiguous_otc_collateral.csv"), ("csv", "prime_allocations_w2.csv")):
        svc.attach_source("scen-proj", SourceAttachReq(kind=kind, uri_or_filename=str(pub_dir / fn)))
    srcs = [s.model_dump(mode="json") for s in svc.list_sources("scen-proj")]
    svc.commit_ontology("scen-proj", OntologyCommitReq(base_revision_id=rev1_id, operations=list(build_wave2_commit_ops(srcs)), rationale="MOD-06 W2 additive commit", low_confidence_grounding_waiver=True, grounding_rationale="W2 approved", waive_quarantine=True))
    wave_box[0] = 2
    await svc.build_vault("scen-proj", VaultBuildReq(policy="balanced"))
    m2 = svc.migrate_target("scen-proj", StorageTargetReq(target_id="duckdb_local"))
    assert m2.wave_number == 2 and m2.verified is True
    v2_sql = (env.opendv_home / "projects" / "scen-proj" / "targets" / "duckdb_local" / "migrations" / "V002_migrate.sql").read_text(encoding="utf-8")
    assert "DROP TABLE raw_vault." not in v2_sql


async def _mod_07(_tmp_path: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    ir = build_clean_trap_ir()
    extra_sat = ir.satellites[0].model_copy(update={"sat_name": "sat_client_extra"})
    no_pit = ir.model_copy(
        update={
            "pit_bridges": [],
            "satellites": [*ir.satellites, extra_sat],
            "information_marts": [
                MartViewSpec(
                    view_name="information_mart.vw_client",
                    root_entity="hub_client_acct",
                    joined_entities=["sat_crm_core", "sat_crm_pii", "sat_client_extra"],
                    select_exprs={"hk": "hk"},
                )
            ],
        }
    )
    d_bal = lint_vault_antipatterns(no_pit, policy="balanced")
    d_str = lint_vault_antipatterns(no_pit, policy="strict")
    assert any(d.rule_id == "DV-AP-012" and d.severity == "ADVISORY" for d in d_bal)
    assert any(d.rule_id == "DV-AP-012" and d.severity == "WARN" for d in d_str)
    bad_v1 = ir.model_copy(
        update={
            "satellites": [
                ir.satellites[0].model_copy(update={"parent_name": "hub_missing_nonexistent"}),
                *ir.satellites[1:],
            ]
        }
    )
    fast_rep = run_v1_to_v6_gates(bad_v1, VerifierContext(policy="fast"))
    assert fast_rep.v1_v6_fail_count > 0 and fast_rep.passed is False


async def _ops_01(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = setup_isolated_env(tmp_path / "ops01", monkeypatch)
    svc = env.make_service()
    rep = svc.import_bundle(GOLDEN_BUNDLE_PATH, conflict="fail", enforce_workspace_containment=False)
    assert {"READY_OFFLINE", "METADATA_ONLY_BINARY_ABSENT", "NEEDS_LOCAL_SECRET_BINDING"}.issubset(set(rep.readiness_flags))


async def _ops_02(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    env, svc, sids = _setup_w1_svc(tmp_path / "src", monkeypatch, project_id="ops02-demo", selector=_w1_selector(svc_box, "ops02-demo"))
    svc_box.append(svc)
    svc.approve_scan_pages("ops02-demo", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="OPS-02 approve scan"))
    commit_wave1_v1_ontology(svc, "ops02-demo", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    await svc.build_vault("ops02-demo", VaultBuildReq(policy="balanced"))
    svc.migrate_target("ops02-demo", StorageTargetReq(target_id="duckdb_local"))
    out_zip = tmp_path / "ops02.full.opendv.zip"
    svc.export_bundle("ops02-demo", mode="full_demo", output_path=out_zip)
    with zipfile.ZipFile(out_zip) as zf:
        assert zf.getinfo("targets/duckdb_local/ops02-demo.duckdb").compress_type == zipfile.ZIP_STORED

    dst_env = setup_isolated_env(tmp_path / "dst", monkeypatch)
    dst_svc = dst_env.make_service()
    dst_svc.import_bundle(out_zip, conflict="fail", enforce_workspace_containment=False)
    with run_loopback_api_server(dst_svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            q = await client.post("/api/v1/projects/ops02-demo/storage/query", json={"sql": "SELECT COUNT(*) FROM raw_vault.hub_issuer"})
            assert q.status_code == 200 and q.json()["rows"][0][0] > 0


async def _ops_03(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = setup_isolated_env(tmp_path, monkeypatch)
    svc = env.make_service()
    rep = svc.import_bundle(GOLDEN_BUNDLE_PATH, conflict="fail", enforce_workspace_containment=False)
    assert "DATABRICKS_TOKEN" in rep.missing_secret_refs
    with run_loopback_api_server(svc) as url:
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            res = await client.post("/api/v1/secrets", json={"key": "DATABRICKS_TOKEN", "value": "dapi-recovery-token"})
            assert res.status_code == 200 and res.json()["backend"] == "user_state_file"
            lst = await client.get("/api/v1/secrets", params={"keys": "DATABRICKS_TOKEN"})
            assert lst.status_code == 200 and lst.json()[0]["present"] is True
            assert "dapi-recovery-token" not in lst.text


async def _ops_04(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc_box: list[OpenDVService] = []
    env, svc, sids = _setup_w1_svc(tmp_path, monkeypatch, project_id="ops04-ci", selector=_w1_selector(svc_box, "ops04-ci"))
    svc_box.append(svc)
    svc.approve_scan_pages("ops04-ci", sids["pdf"], ScanApprovalReq(page_numbers=[4], rationale="OPS-04 approve scan"))
    commit_wave1_v1_ontology(svc, "ops04-ci", sqlite_sid=sids["sqlite"], pq_sid=sids["parquet"], md_sid=sids["markdown"], pdf_sid=sids["pdf"])
    await svc.build_vault("ops04-ci", VaultBuildReq(policy="balanced"))
    with run_loopback_api_server(svc) as url:
        comp = CliRunner().invoke(
            cli_app,
            ["--json", "--api-url", url, "storage", "compile", "--project", "ops04-ci", "--target", "duckdb_local"],
            env=env.subprocess_env(),
        )
        assert comp.exit_code == 0, comp.output
        mcp_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "opendv.cli", "mcp", "serve", "--transport", "stdio", "--project", "ops04-ci"],
            env=env.subprocess_env(),
        )
        async with stdio_client(mcp_params) as (r, w), ClientSession(r, w) as mcp:
            await mcp.initialize()
            tools = await mcp.list_tools()
            assert {t.name for t in tools.tools} == set(MCP_TOOL_NAMES)
            res = await mcp.call_tool("vault_verify", {})
            payload_text = getattr(res.content[0], "text", "{}") if res.content else "{}"
            assert not res.is_error and json.loads(payload_text)["passed"] is True


SCENARIO_HANDLERS: dict[str, Callable[[Path, pytest.MonkeyPatch], Awaitable[None]]] = {
    "SCEN-01": _scen_01,
    "SCEN-02": _scen_02,
    "SCEN-03": _scen_03,
    "SCEN-04": _scen_04,
    "SCEN-05": _scen_05,
    "SCEN-06": _scen_06,
    "SCEN-07": _scen_07,
    "SCEN-08": _scen_08,
    "SCEN-09": _scen_09,
    "MOD-01": _mod_01,
    "MOD-02": _mod_02,
    "MOD-03": _mod_03,
    "MOD-04": _mod_04,
    "MOD-05": _mod_05,
    "MOD-06": _mod_06,
    "MOD-07": _mod_07,
    "OPS-01": _ops_01,
    "OPS-02": _ops_02,
    "OPS-03": _ops_03,
    "OPS-04": _ops_04,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scen_id", list(SCENARIO_HANDLERS.keys()))
async def test_uat_v26_e2e_03_all_20_scenarios(
    scen_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-E2E-03: Verify all 20 scenarios (SCEN-01..09, MOD-01..07, OPS-01..04) cleanly."""
    host_before = snapshot_dir_mtimes(setup_isolated_env(tmp_path / "probe").host_local_opendv)
    await SCENARIO_HANDLERS[scen_id](tmp_path / scen_id, monkeypatch)
    assert snapshot_dir_mtimes(setup_isolated_env(tmp_path / "probe").host_local_opendv) == host_before


def _attach_12trap_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[OpenDVService, list[Any], dict[str, list[dict[str, Any]]]]:
    env = setup_isolated_env(tmp_path, monkeypatch)
    svc = env.make_service()
    svc.init_project(ProjectInitReq(project_id="trap12", domain_namespace="asset_management"))
    svc.attach_source("trap12", SourceAttachReq(kind="duckdb", uri_or_filename=str(TRAP_DUCKDB)))
    profs, rows = load_trap_profiles_and_rows()
    return svc, profs, rows


def _verify_trap(trap_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, profs, rows = _attach_12trap_project(tmp_path, monkeypatch)
    prover = DuckDbProverAdapter()
    clean_ir = build_clean_trap_ir(profs)

    if trap_id == "TRAP-01":
        # Composite BK delimiter collision prevented by Alg A.5 length-prefix hashing (DV-DYN-002)
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-001"] is True and rep.dyn_results["DV-DYN-002"] is True
        crm_rows = rows["trap_src:trap_crm_accounts"]
        naive_0 = f"{crm_rows[0]['delimiter_part_a'] or ''}|{crm_rows[0]['delimiter_part_b'] or ''}"
        naive_1 = f"{crm_rows[1]['delimiter_part_a'] or ''}|{crm_rows[1]['delimiter_part_b'] or ''}"
        assert naive_0 == naive_1 == "A|B|C"
        comp_recipe = HashRecipeSpec(
            target_col="hk_crm_delim",
            recipe_kind="HUB_HK",
            bkcc_literal="CRM",
            source_columns=["delimiter_part_a", "delimiter_part_b"],
            sort_alphabetically=False,
            key_columns=["delimiter_part_a", "delimiter_part_b"],
        )
        col_types = {"delimiter_part_a": "UTF8_STRING", "delimiter_part_b": "UTF8_STRING"}
        py_hashes = [execute_hash_recipe(comp_recipe, r, key_tier="TIER_3_LOCAL_SCOPED", column_types=col_types) for r in crm_rows]
        assert len(set(py_hashes)) == 4 and py_hashes[0] != py_hashes[1]
        expr_sql = compile_hash_recipe_sql(comp_recipe, "duckdb", key_tier="TIER_3_LOCAL_SCOPED", col_types=col_types)
        conn = duckdb.connect(":memory:")
        try:
            sql_hashes = [
                str(conn.execute(f"SELECT LOWER(HEX({expr_sql})) FROM (SELECT CAST(? AS VARCHAR) AS delimiter_part_a, CAST(? AS VARCHAR) AS delimiter_part_b)", [r["delimiter_part_a"], r["delimiter_part_b"]]).fetchone()[0]).lower()  # type: ignore[index]
                for r in crm_rows
            ]
        finally:
            conn.close()
        assert sql_hashes == py_hashes
    elif trap_id == "TRAP-02":
        # Leading-zero CIK '0000909100' & Unicode NFC 'Nordisk Suverän Fond' stability (DV-DYN-002 & DV-DYN-011)
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-002"] is True and rep.dyn_results["DV-DYN-011"] is True
        ibor_0 = next(r for r in rows["trap_src:trap_ibor_accounts"] if r["account_id"] == "1001")
        assert ibor_0["issuer_cik"] == "0000909100" and ibor_0["issuer_legal_name"] == "Nordisk Suverän Fond"
        hd_ibor = next(s.hash_recipe for s in clean_ir.satellites if s.sat_name == "sat_ibor_core" and s.hash_recipe is not None)
        hd_types = {"issuer_cik": "UTF8_STRING", "issuer_legal_name": "UTF8_STRING"}
        h_orig = execute_hash_recipe(hd_ibor, ibor_0, column_types=hd_types)
        assert h_orig != execute_hash_recipe(hd_ibor, {**ibor_0, "issuer_cik": "909100"}, column_types=hd_types)
        assert h_orig != execute_hash_recipe(hd_ibor, {**ibor_0, "issuer_legal_name": "Nordisk Suveran Fond"}, column_types=hd_types)
        expr_sql = compile_hash_recipe_sql(hd_ibor, "duckdb", col_types=hd_types)
        conn = duckdb.connect(":memory:")
        try:
            sql_row = conn.execute(f"SELECT LOWER(HEX({expr_sql})) FROM (SELECT CAST(? AS VARCHAR) AS issuer_cik, CAST(? AS VARCHAR) AS issuer_legal_name)", [ibor_0["issuer_cik"], ibor_0["issuer_legal_name"]]).fetchone()
            assert sql_row is not None and str(sql_row[0]).lower() == h_orig
        finally:
            conn.close()
    elif trap_id == "TRAP-03":
        # Tenant BKCC separation ('CRM' vs 'IBOR' on identical account_id='1001')
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-001"] is True and rep.dyn_results["DV-DYN-002"] is True
        crm_1001 = next(r for r in rows["trap_src:trap_crm_accounts"] if r["account_id"] == "1001")
        ibor_1001 = next(r for r in rows["trap_src:trap_ibor_accounts"] if r["account_id"] == "1001")
        assert clean_ir.hubs[0].hash_recipe is not None and clean_ir.hubs[1].hash_recipe is not None
        hk_crm = execute_hash_recipe(clean_ir.hubs[0].hash_recipe, crm_1001, key_tier="TIER_3_LOCAL_SCOPED")
        hk_ibor = execute_hash_recipe(clean_ir.hubs[1].hash_recipe, ibor_1001, key_tier="TIER_3_LOCAL_SCOPED")
        assert hk_crm != hk_ibor
        coll_recipe = clean_ir.hubs[1].hash_recipe.model_copy(update={"bkcc_literal": "CRM"})
        assert execute_hash_recipe(coll_recipe, ibor_1001, key_tier="TIER_3_LOCAL_SCOPED") == hk_crm
        missing_bkcc_hub = clean_ir.hubs[1].model_copy(
            update={
                "business_keys": [BusinessKeyPart(table_id="trap_src:trap_ibor_accounts", col_name="account_id", source_id="trap_src", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="")],
                "hash_recipe": clean_ir.hubs[1].hash_recipe.model_copy(update={"bkcc_literal": ""}),
            }
        )
        missing_bkcc_ir = clean_ir.model_copy(update={"hubs": [clean_ir.hubs[0], missing_bkcc_hub, *clean_ir.hubs[2:]]})
        assert any(d.rule_id == "DV-COL-012" for d in verify_v2_hash_and_bkcc(missing_bkcc_ir))
    elif trap_id == "TRAP-04":
        # Multi-active proposal_seq: unpromoted fails DV-DYN-004, MAS passes 15/15
        bad_ir = build_clean_trap_ir(profs, unpromote_ballot_mas_to_sat=True)
        bad_rep = prover.prove(bad_ir, profs, source_rows_by_table=rows)
        assert bad_rep.dyn_results["DV-DYN-004"] is False
        ok_rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert ok_rep.dyn_results["DV-DYN-004"] is True
    elif trap_id == "TRAP-05":
        # Technical churn etl_snapshot_ts in hash_diff triggers DV-AP-011; SYSTEM_METADATA triage fixes
        churn_ir = build_clean_trap_ir(profs, include_tech_churn_in_hd=True)
        churn_rep = prover.prove(churn_ir, profs, source_rows_by_table=rows)
        assert any(s.rule_id == "DV-AP-011" for s in churn_rep.empirical_smells)
        assert all(s.rule_id != "DV-AP-011" for s in prover.prove(clean_ir, profs, source_rows_by_table=rows).empirical_smells)
    elif trap_id == "TRAP-06":
        # Disguised lookup iso_currency_code -> ONT-AP-002 / RefTableSpec demotion
        ballot_tbl = next(t.table_id for t in profs[0].tables if t.table_name == "trap_ballots_mas")
        ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:t:ccy",
                    concept_name="IsoCurrency",
                    description="Lookup smell",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    attributes=[ConceptAttribute(name="iso_currency_code", logical_type="UTF8_STRING", sql_type_hint="VARCHAR", nullable=False, is_business_key=True, is_pii=False)],
                    table_bindings=[ConceptTableBinding(source_id="trap_src", table_id=ballot_tbl, col_name="iso_currency_code")],
                )
            ],
        )
        assert any(d.rule_id == "ONT-AP-002" for d in lint_ontology_antipatterns(ont, profs))
    elif trap_id == "TRAP-07":
        # Sparse optional FK (executing_broker_lei 50% null, prime_custodian_bic 75% null) + Alg A.4 IS_NOT_NULL + DV-AP-002 + DV-DYN-007 + zero-key -1
        bronze_ibor = next(b for b in clean_ir.bronze_tables if b.table_name == "bronze_ibor")
        assert {"executing_broker_lei", "prime_custodian_bic"} <= {c.name for c in bronze_ibor.columns}
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-007"] is True and rep.dyn_results["DV-DYN-009"] is True
        bundle = SqlGlotVaultCompiler("duckdb_local").compile(clean_ir)
        z0, zf = zero_key_hex_sql("duckdb", is_error=False), zero_key_hex_sql("duckdb", is_error=True)
        conn = duckdb.connect(":memory:")
        try:
            run_adversarial_batches(conn, clean_ir, profs, bundle, source_rows_by_table=rows)
            lnk_tot = conn.execute("SELECT COUNT(*) FROM raw_vault.lnk_ibor_broker").fetchone()
            lnk_real = conn.execute(f"SELECT COUNT(*) FROM raw_vault.lnk_ibor_broker WHERE hk_ibor_broker NOT IN ({z0}, {zf})").fetchone()
            lnk_zero = conn.execute(f"SELECT COUNT(*) FROM raw_vault.lnk_ibor_broker WHERE hk_ibor_broker IN ({z0}, {zf})").fetchone()
            brk_tot = conn.execute("SELECT COUNT(*) FROM raw_vault.hub_broker").fetchone()
            assert lnk_tot is not None and int(lnk_tot[0]) == 4
            assert lnk_real is not None and int(lnk_real[0]) == 2
            assert lnk_zero is not None and int(lnk_zero[0]) == 2
            assert brk_tot is not None and int(brk_tot[0]) == 4
        finally:
            conn.close()
        sparse_draft = DraftVaultIR(
            project_id="trap07-alg-a4",
            ontology_version="1.0.0",
            wave_number=1,
            hubs=[
                DraftHubSpec(hub_name="hub_ibor_acct", key_tier="TIER_3_LOCAL_SCOPED", business_keys=[BusinessKeyPart(table_id="trap_src:trap_ibor_accounts", col_name="account_id", source_id="trap_src", key_tier="TIER_3_LOCAL_SCOPED", bkcc_value="IBOR")], source_bindings=[DraftSourceBinding(source_id="trap_src", table_id="trap_src:trap_ibor_accounts", col_mappings={})]),
                DraftHubSpec(hub_name="hub_broker", key_tier="TIER_1_GLOBAL_STANDARD", business_keys=[BusinessKeyPart(table_id="trap_src:trap_ibor_accounts", col_name="executing_broker_lei", source_id="trap_src", key_tier="TIER_1_GLOBAL_STANDARD", bkcc_value="GLOBAL")], source_bindings=[DraftSourceBinding(source_id="trap_src", table_id="trap_src:trap_ibor_accounts", col_mappings={})]),
            ],
            links=[
                DraftLinkSpec(
                    link_name="lnk_ibor_broker",
                    link_kind="STANDARD",
                    participants=[
                        LinkParticipantSpec(hub_name="hub_ibor_acct", role_name="IBOR_ACCOUNT", fk_col_name="hk_ibor_acct", source_col_names=["account_id"]),
                        LinkParticipantSpec(hub_name="hub_broker", role_name="EXECUTING_BROKER", fk_col_name="hk_broker", source_col_names=["executing_broker_lei"]),
                    ],
                    source_bindings=[DraftSourceBinding(source_id="trap_src", table_id="trap_src:trap_ibor_accounts", col_mappings={})],
                )
            ],
        )
        _, l1_cert, _ = run_l1_codemods(sparse_draft, profiles=profs)
        assert l1_cert.links[0].load_semantics is not None
        assert l1_cert.links[0].load_semantics.filter_ast == PredicateLeaf(op="IS_NOT_NULL", column="executing_broker_lei")
        mega_lnk = clean_ir.links[1].model_copy(
            update={
                "link_name": "lnk_mega_sparse",
                "participants": [
                    LinkParticipantSpec(hub_name="hub_ibor_acct", role_name="A", fk_col_name="hk_ibor_acct", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_broker", role_name="B", fk_col_name="hk_broker", source_col_names=["executing_broker_lei"]),
                    LinkParticipantSpec(hub_name="hub_custodian", role_name="C", fk_col_name="hk_custodian", source_col_names=["prime_custodian_bic"]),
                    LinkParticipantSpec(hub_name="hub_ballot", role_name="D", fk_col_name="hk_ballot", source_col_names=["issuer_cik"]),
                    LinkParticipantSpec(hub_name="hub_trade", role_name="E", fk_col_name="hk_trade", source_col_names=["issuer_legal_name"]),
                ],
            }
        )
        assert any(d.rule_id == "DV-AP-002" for d in lint_vault_antipatterns(clean_ir.model_copy(update={"links": [*clean_ir.links, mega_lnk]}), profs))
        assert all(d.rule_id != "DV-AP-002" for d in lint_vault_antipatterns(clean_ir, profs))
    elif trap_id == "TRAP-08":
        # Boomerang effectivity trap_manager_eff -> esat_acct_mgr passes DV-DYN-008 & DV-DYN-013; missing driving_keys fails DV-DYN-013
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-008"] is True and rep.dyn_results["DV-DYN-013"] is True
        bad_esat_ir = clean_ir.model_copy(
            update={"satellites": [s.model_copy(update={"driving_keys": []}) if s.sat_kind == "ESAT" else s for s in clean_ir.satellites]}
        )
        bad_rep = prover.prove(bad_esat_ir, profs, source_rows_by_table=rows)
        assert bad_rep.dyn_results["DV-DYN-013"] is False
        assert any(c["rule_id"] == "DV-DYN-013" for c in bad_rep.counterexamples)
    elif trap_id == "TRAP-09":
        # Unisolated PII triggers DV-AP-009; isolated sat_crm_pii passes + decollide_masked_pii
        unsplit = build_clean_trap_ir(profs, omit_pii_isolation=True)
        assert any(d.rule_id == "DV-AP-009" for d in lint_vault_antipatterns(unsplit, profs))
        masked_rows, next_idx = decollide_masked_pii([{"tax_id": "<MASKED_PII>"}, {"tax_id": "<MASKED_PII>"}])
        assert next_idx == 3 and masked_rows[0]["tax_id"] == "<MASKED_PII_#1>" and masked_rows[1]["tax_id"] == "<MASKED_PII_#2>"
        assert all(d.rule_id != "DV-AP-009" for d in lint_vault_antipatterns(clean_ir, profs))
    elif trap_id == "TRAP-10":
        # God Satellite (>=25 attrs on trap_crm_accounts) triggers DV-AP-005
        crm_tbl = next(t for t in profs[0].tables if t.table_name == "trap_crm_accounts")
        wide_sat = clean_ir.satellites[0].model_copy(
            update={"attributes": [DraftField(name=c.col_name, logical_type="UTF8_STRING", nullable=True, is_pii=False) for c in crm_tbl.columns[:25]]}
        )
        god_ir = clean_ir.model_copy(update={"satellites": [wide_sat, *clean_ir.satellites[1:]]})
        assert any(d.rule_id == "DV-AP-005" for d in lint_vault_antipatterns(god_ir, profs))
    elif trap_id == "TRAP-11":
        # Bi-temporal late-arriving restatement (trap_early_trades as_of_date vs applied_dts): DV-DYN-015 & DV-DYN-009
        rep = prover.prove(clean_ir, profs, source_rows_by_table=rows)
        assert rep.dyn_results["DV-DYN-015"] is True and rep.dyn_results["DV-DYN-009"] is True
        bad_pit_ir = clean_ir.model_copy(
            update={"pit_bridges": [p.model_copy(update={"target_satellites_or_links": []}) if p.object_name == "pit_trade" else p for p in clean_ir.pit_bridges]}
        )
        bad_rep = prover.prove(bad_pit_ir, profs, source_rows_by_table=rows)
        assert bad_rep.dyn_results["DV-DYN-015"] is False
        assert any(c["rule_id"] == "DV-DYN-015" for c in bad_rep.counterexamples)
    elif trap_id == "TRAP-12":
        # Planned ontology concept exemption + full V1..V6 gate check via REST verify
        gate_rep = run_v1_to_v6_gates(clean_ir, VerifierContext(policy="balanced"), prover=prover)
        assert gate_rep.passed is True
        assert any(h.is_planned_concept for h in clean_ir.hubs)
        # Also verify soft-rule AST leak detection (DV-AP-010) cleanly via V5
        bad_stg = clean_ir.model_copy(update={"staging_views": [StagingViewSpec(view_name="stg_bad", source_id="trap_src", source_table_id="trap_src:trap_crm_accounts", col_lineage_mappings={"x": "CASE WHEN 1=1 THEN 1 ELSE 0 END"})]})
        assert any(d.rule_id == "DV-AP-010" for d in lint_vault_antipatterns(bad_stg, profs))


TRAP_IDS = [f"TRAP-{i:02d}" for i in range(1, 13)]


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_uat_v26_e2e_03_all_12_traps_on_real_duckdb(
    trap_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-V26-E2E-03: Verify TRAP-01..12 on real data/public_am/ds_cegar_12trap.duckdb."""
    host_before = snapshot_dir_mtimes(setup_isolated_env(tmp_path / "probe").host_local_opendv)
    _verify_trap(trap_id, tmp_path / trap_id, monkeypatch)
    assert snapshot_dir_mtimes(setup_isolated_env(tmp_path / "probe").host_local_opendv) == host_before
