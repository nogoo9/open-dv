"""M4 Live-provider UAT suite (UAT-GND-LIVE-01..04) using real agy & claude-cli."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import yaml

from opendv.agents import resolve_cli_binary
from opendv.models import (
    AddConcept,
    AddExternalMapping,
    ConceptAttribute,
    ConceptTableBinding,
    ErrorCode,
    ExternalMapping,
    FiboQueryReq,
    GroundingResponse,
    HarnessProvider,
    OntologyCommitReq,
    OpenDVError,
    ProjectInitReq,
    SourceAttachReq,
    SourceProfile,
    WorkspaceSettings,
)
from opendv.models.events import GroundingReceipt
from opendv.models.ontology import GraphAOntology
from opendv.models.project import EvolutionLockSpec
from opendv.service.app_service import OpenDVService
from opendv.service.bundles.exporter import export_project_bundle
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._grounding_projection import project_grounding_to_deltas
from opendv.service.workflows._ontology_deltas import compile_and_apply_deltas
from tests.api._api_test_helpers import wait_run_via_sse
from tests.cli._cli_test_helpers import run_loopback_api_server
from tests.fixtures.public_am.fetch_public_datasets import PublicDataFetcher
from tests.parity._v2_parity_helpers import setup_isolated_env, snapshot_dir_mtimes

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_AM = _REPO_ROOT / "data" / "public_am"
_FIBO_BASE = "https://spec.edmcouncil.org/fibo/ontology"
_CANARY = "CANARY-LIVE-GND-DO-NOT-LEAK-9A7C"
_RAW_PII_SENTINELS = (
    "ABA 021000021",
    "021000021",
    "123-45-6789",
    "987-65-4321",
    "steward.one@am-example.com",
    "steward.two@am-example.com",
    "212-555-0101",
    "212-555-0102",
)


def _copy_public_am(ws: Path, filenames: tuple[str, ...]) -> Path:
    am_dir = ws / "public_am"
    am_dir.mkdir(parents=True, exist_ok=True)
    for fname in filenames:
        shutil.copy2(_PUBLIC_AM / fname, am_dir / fname)
    return am_dir


def _live_cli_env(ws: Path, opendv_home: Path, tmp_path: Path) -> dict[str, str]:
    """Preserve host HOME/APPDATA/LOCALAPPDATA auth for live agy/claude while isolating OpenDV."""
    return {
        **os.environ,
        "OPENDV_HOME": str(opendv_home),
        "OPENDV_WORKSPACE_ROOTS": f"{ws}{os.pathsep}{tmp_path}",
        "OPENDV_TEST_CANARY": _CANARY,
    }


def _assert_no_pii_or_canary_leaks(*texts: str) -> None:
    for txt in texts:
        assert _CANARY not in txt
        assert "[REDACTED_PII]" not in txt
        for pii in _RAW_PII_SENTINELS:
            assert pii not in txt, f"Raw PII leaked: {pii}"


def _northwind_glossary_yaml() -> str:
    return (
        "version: 1\n"
        "terms:\n"
        "  customer:\n"
        "    label: Customer\n"
        "    definition: Commercial retail customer purchasing products via sales orders.\n"
        "    aliases: [Customers, Buyer, Account]\n"
        "    kind: entity\n"
        "    expected_type: string\n"
        "  order:\n"
        "    label: Sales Order\n"
        "    definition: Commercial purchase order placed by a customer.\n"
        "    aliases: [Orders, Purchase Order]\n"
        "    kind: event\n"
        "    expected_type: string\n"
        "  product:\n"
        "    label: Product\n"
        "    definition: Retail product SKU stocked and sold to customers.\n"
        "    aliases: [Products, Merchandise]\n"
        "    kind: entity\n"
        "    expected_type: string\n"
        "  customer_id:\n"
        "    label: Customer ID\n"
        "    definition: Unique alphanumeric customer identifier code.\n"
        "    aliases: [CustomerID, CustID]\n"
        "    kind: identifier\n"
        "    domain_entity: customer\n"
        "    identifies: customer\n"
        "    identifier_scope: enterprise_sor\n"
        "    expected_type: string\n"
        "  company_name:\n"
        "    label: Company Name\n"
        "    definition: Registered trade name of the customer company.\n"
        "    aliases: [CompanyName]\n"
        "    kind: attribute\n"
        "    domain_entity: customer\n"
        "    expected_type: string\n"
        "  contact_name:\n"
        "    label: Contact Name\n"
        "    definition: Primary customer contact person full name.\n"
        "    aliases: [ContactName]\n"
        "    kind: attribute\n"
        "    domain_entity: customer\n"
        "    expected_type: string\n"
        "  country:\n"
        "    label: Country\n"
        "    definition: Country where the customer organization operates.\n"
        "    aliases: [Country]\n"
        "    kind: attribute\n"
        "    domain_entity: customer\n"
        "    expected_type: string\n"
        "  order_id:\n"
        "    label: Order ID\n"
        "    definition: Unique integer sales order identifier.\n"
        "    aliases: [OrderID]\n"
        "    kind: identifier\n"
        "    domain_entity: order\n"
        "    identifies: order\n"
        "    identifier_scope: enterprise_sor\n"
        "    expected_type: integer\n"
        "  order_date:\n"
        "    label: Order Date\n"
        "    definition: Calendar date on which the sales order was placed.\n"
        "    aliases: [OrderDate]\n"
        "    kind: attribute\n"
        "    domain_entity: order\n"
        "    expected_type: date\n"
        "  employee_id:\n"
        "    label: Employee ID\n"
        "    definition: Internal sales representative identifier.\n"
        "    aliases: [EmployeeID]\n"
        "    kind: identifier\n"
        "    domain_entity: order\n"
        "    expected_type: integer\n"
        "  product_id:\n"
        "    label: Product ID\n"
        "    definition: Unique integer catalog SKU identifier.\n"
        "    aliases: [ProductID]\n"
        "    kind: identifier\n"
        "    domain_entity: product\n"
        "    identifies: product\n"
        "    identifier_scope: enterprise_sor\n"
        "    expected_type: integer\n"
        "  product_name:\n"
        "    label: Product Name\n"
        "    definition: Commercial name of the retail product.\n"
        "    aliases: [ProductName]\n"
        "    kind: attribute\n"
        "    domain_entity: product\n"
        "    expected_type: string\n"
        "  supplier_id:\n"
        "    label: Supplier ID\n"
        "    definition: External vendor identifier supplying the product.\n"
        "    aliases: [SupplierID]\n"
        "    kind: identifier\n"
        "    domain_entity: product\n"
        "    expected_type: integer\n"
        "  category_id:\n"
        "    label: Category ID\n"
        "    definition: Product merchandising category identifier.\n"
        "    aliases: [CategoryID]\n"
        "    kind: identifier\n"
        "    domain_entity: product\n"
        "    expected_type: integer\n"
        "  unit_price:\n"
        "    label: Unit Price\n"
        "    definition: Standard unit selling price of a product.\n"
        "    aliases: [UnitPrice]\n"
        "    kind: attribute\n"
        "    domain_entity: product\n"
        "    expected_type: decimal\n"
    )


def _sync_pack_dict(*, deprecated_bond: bool, keep_yield: bool) -> dict[str, Any]:
    bond_uri = f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond"
    acct_uri = f"{_FIBO_BASE}/FBC/Accounts/Account"
    yield_uri = f"{_FIBO_BASE}/IND/InterestRates/InterestRates/InterestRate"
    classes: dict[str, Any] = {
        bond_uri: {
            "uri": bond_uri,
            "label": "Bond",
            "definition": "A negotiable debt security obligation.",
            "module": "SEC",
            "synonyms": ["bond", "bond_id"],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": deprecated_bond,
            "replaced_by": acct_uri if deprecated_bond else None,
        },
        acct_uri: {
            "uri": acct_uri,
            "label": "Account",
            "definition": "Financial account entity.",
            "module": "FBC",
            "synonyms": ["account"],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": False,
            "replaced_by": None,
        },
    }
    if keep_yield:
        classes[yield_uri] = {
            "uri": yield_uri,
            "label": "Interest Rate",
            "definition": "Rate of return or yield percentage.",
            "module": "IND",
            "synonyms": ["yield", "interest_rate"],
            "sub_class_of": [],
            "archetype": "attribute",
            "deprecated": False,
            "replaced_by": None,
        }
    return {
        "metadata": {
            "pack_id": "fibo-live-sync-v2",
            "pack_schema_version": "2.0.0",
            "version": "2.0.0-uat",
            "modules": ["BE", "FBC", "FND", "IND", "SEC"],
        },
        "classes": classes,
        "properties": {
            f"{_FIBO_BASE}/SEC/Debt/Bonds/hasMaturityDate": {
                "uri": f"{_FIBO_BASE}/SEC/Debt/Bonds/hasMaturityDate",
                "label": "maturity date",
                "definition": "Contractual date on which principal repayment is due.",
                "module": "SEC",
                "synonyms": ["maturity_date", "mat_dt"],
                "property_type": "DatatypeProperty",
                "domain": [bond_uri],
                "range": ["xsd:date"],
                "sub_property_of": [],
                "inverse_of": [],
                "is_functional": True,
                "is_inverse_functional": False,
                "deprecated": False,
            }
        },
        "relations": [],
        "individuals": {},
    }


@pytest.mark.live_provider
@pytest.mark.parametrize("provider", ["agy", "claude-cli"])
def test_uat_gnd_live_01_public_am_cli_and_rest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["agy", "claude-cli"],
) -> None:
    """UAT-GND-LIVE-01: Live `opendv ontology ground` on Asset Management + 12trap via CLI+REST."""
    resolve_cli_binary(provider)
    env = setup_isolated_env(tmp_path, monkeypatch, preserve_live_auth=True)
    pre_mtimes = snapshot_dir_mtimes(env.host_local_opendv)

    _copy_public_am(
        env.ws,
        (
            "am_sec_treasury_ibor.sqlite",
            "sec_13f_holdings.csv",
            "wave2_ambiguous_otc_collateral.csv",
            "wave2_eu_custody_lei_and_drift.parquet",
            "ds_cegar_12trap.duckdb",
        ),
    )

    svc = env.make_service()
    typed_provider: HarnessProvider = provider
    svc.update_settings(WorkspaceSettings(harness_provider=typed_provider, timeout_seconds=240))
    proj = svc.init_project(
        ProjectInitReq(project_id=f"live-gnd-01-{provider}", domain_namespace="asset_management")
    )

    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename="public_am/am_sec_treasury_ibor.sqlite",
            selected_tables=["securities", "holdings_13f"],
        ),
    )
    svc.attach_source(
        proj.project_id,
        SourceAttachReq(kind="csv", uri_or_filename="public_am/sec_13f_holdings.csv"),
    )
    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="csv", uri_or_filename="public_am/wave2_ambiguous_otc_collateral.csv"
        ),
    )
    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="parquet", uri_or_filename="public_am/wave2_eu_custody_lei_and_drift.parquet"
        ),
    )
    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="duckdb",
            uri_or_filename="public_am/ds_cegar_12trap.duckdb",
            selected_tables=["trap_crm_accounts", "trap_ibor_accounts"],
        ),
    )

    cli_env = _live_cli_env(env.ws, env.opendv_home, tmp_path)
    with run_loopback_api_server(svc) as base_url:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from opendv.cli.main import main; raise SystemExit(main())",
                "--json",
                "--api-url",
                base_url,
                "ontology",
                "ground",
                "--project",
                proj.project_id,
                "--mode",
                "agentic",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=cli_env,
            timeout=900,
            check=False,
        )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    envelope = json.loads(proc.stdout)
    assert envelope["ok"] is True
    resp = GroundingResponse.model_validate(envelope["data"])
    _assert_no_pii_or_canary_leaks(proc.stdout, proc.stderr, resp.model_dump_json())

    assert resp.mode_used == "agentic_reranked"
    assert len(resp.tables) >= 7
    all_cols = [col for tbl in resp.tables for col in tbl.columns]
    assert any(
        c.column_name == "cusip"
        and c.status == "MATCHED"
        and c.execution_mode_used == "symbolic_fast_path"
        for c in all_cols
    )
    assert any(
        c.column_name == "isin"
        and c.status == "MATCHED"
        and c.execution_mode_used == "symbolic_fast_path"
        for c in all_cols
    )
    reranked_cols = [
        c for c in all_cols if c.execution_mode_used in {"agentic_reranked", "agentic_rescue"}
    ]
    assert len(reranked_cols) >= 3
    matched_cols = [c for c in all_cols if c.status == "MATCHED" and c.choices]
    assert len(matched_cols) >= 4
    assert all(0.0 <= c.choices[0].confidence.point <= 1.0 for c in matched_cols)
    assert any(
        c.choices[0].origin_kind == "reference_standard"
        and "edmcouncil.org/fibo" in c.choices[0].entry_id
        for c in matched_cols
    )
    assert snapshot_dir_mtimes(env.host_local_opendv) == pre_mtimes


@pytest.mark.live_provider
@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agy", "claude-cli"])
async def test_uat_gnd_live_02_step4_step6_wave2_and_bundle_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["agy", "claude-cli"],
) -> None:
    """UAT-GND-LIVE-02: REST Step 4 -> commit -> Step 6 fast -> Wave 2 -> .opendv.zip SCEN-07."""
    resolve_cli_binary(provider)
    env = setup_isolated_env(tmp_path, monkeypatch, preserve_live_auth=True)
    pre_mtimes = snapshot_dir_mtimes(env.host_local_opendv)

    _copy_public_am(
        env.ws,
        (
            "am_sec_treasury_ibor.sqlite",
            "am_stewardship_domain_notes.md",
            "wave2_eu_custody_lei_and_drift.parquet",
            "wave2_ambiguous_otc_collateral.csv",
            "prime_allocations_w2.csv",
        ),
    )

    svc = env.make_service()
    typed_provider: HarnessProvider = provider
    svc.update_settings(WorkspaceSettings(harness_provider=typed_provider, timeout_seconds=240))
    proj = svc.init_project(
        ProjectInitReq(project_id=f"live-gnd-02-{provider}", domain_namespace="asset_management")
    )
    initial_rev = proj.active_ontology_rev_id
    assert initial_rev is not None

    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename="public_am/am_sec_treasury_ibor.sqlite",
            selected_tables=["issuers", "securities", "institutional_filers", "holdings_13f"],
        ),
    )
    svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"
        ),
    )

    state_mgr = ProjectStateManager(svc.workspace_root)
    with run_loopback_api_server(svc) as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=900.0) as client:
            r4 = await client.post(
                f"/api/v1/projects/{proj.project_id}/ontology/construct",
                json={
                    "discovery_mode": "incremental_new",
                    "hint": (
                        "Call ground_columns first to obtain server-minted grounding_receipt_id "
                        "values, then propose additive AddConcept deltas with exact "
                        "table_bindings covering issuers, securities, institutional_filers, and "
                        "holdings_13f without asking clarification questions. Do NOT include "
                        "source_citations on structured SQLite concepts unless copied verbatim "
                        "character-for-character from read_document_pages (omit source_citations otherwise)."
                    ),
                },
            )
            assert r4.status_code == 202, r4.text
            rid4 = r4.json()["run_id"]
            await wait_run_via_sse(
                client, f"/api/v1/projects/{proj.project_id}/ontology/runs/{rid4}/events"
            )
            run4_state = (
                await client.get(f"/api/v1/projects/{proj.project_id}/ontology/runs/{rid4}")
            ).json()
            for _ in range(3):
                if run4_state["status"] != "HOLD_CHECKPOINTED":
                    break
                answers = {
                    q["question_id"]: (
                        q.get("recommended_answer")
                        or "Use canonical Tier-1/Tier-2 natural keys (cik, ticker, filer_cik, holding_id)."
                    )
                    for q in run4_state.get("pending_questions", [])
                }
                r_ans = await client.post(
                    f"/api/v1/projects/{proj.project_id}/ontology/runs/{rid4}/answers",
                    json={"answers": answers},
                )
                assert r_ans.status_code == 202, r_ans.text
                await wait_run_via_sse(
                    client, f"/api/v1/projects/{proj.project_id}/ontology/runs/{rid4}/events"
                )
                run4_state = (
                    await client.get(f"/api/v1/projects/{proj.project_id}/ontology/runs/{rid4}")
                ).json()
            assert run4_state["status"] == "COMPLETED", run4_state
            assert svc.get_project(proj.project_id).active_ontology_rev_id == initial_rev

            staged = state_mgr.list_revisions(proj.project_id, rev_kind="staged_candidate")
            assert len(staged) >= 1
            for row in staged:
                assert row["payload"]["grounding_path"] in {
                    "PATH_A1",
                    "PATH_A2",
                    "PATH_B1",
                    "PATH_B2",
                }

            receipts: list[dict[str, Any]] = []
            if provider == "claude-cli":
                page = state_mgr.tail_run_stream_events(proj.project_id, rid4, since_seq=0)
                tool_calls = [
                    ev.payload.get("tool_name")
                    for ev in page.events
                    if ev.event_type == "TOOL_CALL"
                ]
                assert "ground_columns" in tool_calls
                receipts = state_mgr.list_events_or_receipts(
                    proj.project_id, record_kind="grounding_receipt", run_id=rid4
                )
                assert len(receipts) >= 1
                assert all(r["payload"]["provenance"] == "server_verified" for r in receipts)

            # Commit the real agent-staged proposals (no hardcoded AddConcept bypass)
            exp_r = await client.get(
                f"/api/v1/projects/{proj.project_id}/ontology/export?format=yaml&include_staged=true"
            )
            assert exp_r.status_code == 200, exp_r.text
            staged_ids = [str(r["rev_key"]) for r in staged]
            commit_r = await client.post(
                f"/api/v1/projects/{proj.project_id}/ontology/commit",
                json={
                    "base_revision_id": initial_rev,
                    "yaml_text": exp_r.text,
                    "rationale": "Steward commits real Step 4 agent-staged proposals.",
                    "committed_proposal_ids": staged_ids,
                    "waive_quarantine": True,
                },
            )
            assert commit_r.status_code == 200, commit_r.text
            w1_rev_id = commit_r.json()["revision_id"]

            # Bind server-verified Step 4 receipt (or FIBO QA hit on agy) onto a committed concept
            # so Wave-2 OverlayKnowledgeSource and .opendv.zip SCEN-07 test real ExternalMappings.
            w1_ont = svc.get_ontology_bundle(proj.project_id).ontology
            map_ops: list[AddExternalMapping] = []
            if receipts:
                rcpt_objs = [GroundingReceipt.model_validate(r["payload"]) for r in receipts]
                for concept in w1_ont.concepts:
                    match_rcpt = next(
                        (
                            rc
                            for rc in rcpt_objs
                            if rc.mapping_type in {"skos:exactMatch", "skos:closeMatch"}
                            and any(
                                b.table_id == rc.table_id
                                and (rc.target_kind == "table_anchor" or b.col_name == rc.source_column)
                                for b in concept.table_bindings
                            )
                        ),
                        None,
                    )
                    if match_rcpt is not None:
                        map_ops.append(
                            AddExternalMapping(
                                concept_uri=concept.concept_uri,
                                mapping=ExternalMapping(
                                    standard_uri=match_rcpt.standard_uri,
                                    reference_pack_id=match_rcpt.reference_pack_id,
                                    reference_pack_sha256=match_rcpt.reference_pack_sha256,
                                    mapping_type=match_rcpt.mapping_type,
                                    confidence=match_rcpt.confidence,
                                    grounding_receipt_id=match_rcpt.receipt_id,
                                ),
                            )
                        )
                        break
            if not map_ops and w1_ont.concepts:
                first_c = w1_ont.concepts[0]
                qa_hits = svc.fibo_qa(FiboQueryReq(query=first_c.concept_name, top_k=1)).matches
                if qa_hits:
                    map_ops.append(
                        AddExternalMapping(
                            concept_uri=first_c.concept_uri,
                            mapping=ExternalMapping(
                                standard_uri=qa_hits[0].concept_uri,
                                reference_pack_id="fibo-core",
                                reference_pack_sha256="",
                                mapping_type="skos:closeMatch",
                                confidence=Decimal("0.7000"),
                            ),
                        )
                    )
            if map_ops:
                map_rev = svc.commit_ontology(
                    proj.project_id,
                    OntologyCommitReq(
                        operations=map_ops,
                        base_revision_id=w1_rev_id,
                        rationale="Attach verified Step-4 external mapping onto staged concept.",
                        waive_quarantine=True,
                    ),
                )
                w1_rev_id = map_rev.revision_id
                w1_ont = svc.get_ontology_bundle(proj.project_id).ontology

            # Step 6 (policy="fast") certifies Wave-1 VaultSchemaIR + evolution_locks
            r6 = await client.post(
                f"/api/v1/projects/{proj.project_id}/runs",
                json={"policy": "fast"},
            )
            assert r6.status_code == 202, r6.text
            rid6 = r6.json()["run_id"]
            await wait_run_via_sse(
                client, f"/api/v1/projects/{proj.project_id}/runs/{rid6}/events"
            )
            run6_state = (
                await client.get(f"/api/v1/projects/{proj.project_id}/runs/{rid6}")
            ).json()
            for _ in range(4):
                if run6_state["status"] != "HOLD_CHECKPOINTED":
                    break
                answers6 = {
                    q["question_id"]: (
                        q.get("recommended_answer")
                        or "Proceed with standard Data Vault 2.0 Hub/Link/Satellite decomposition."
                    )
                    for q in run6_state.get("pending_questions", [])
                }
                r6_dec = await client.post(
                    f"/api/v1/projects/{proj.project_id}/runs/{rid6}/decisions",
                    json={"answers": answers6},
                )
                assert r6_dec.status_code == 202, r6_dec.text
                await wait_run_via_sse(
                    client, f"/api/v1/projects/{proj.project_id}/runs/{rid6}/events"
                )
                run6_state = (
                    await client.get(f"/api/v1/projects/{proj.project_id}/runs/{rid6}")
                ).json()
            assert run6_state["status"] == "COMPLETED", run6_state
            vault_ir = svc.get_vault_ir(proj.project_id)
            assert len(vault_ir.hubs) >= 1
            lock_revs = state_mgr.list_revisions(proj.project_id, rev_kind="evolution_lock")
            assert len(lock_revs) >= 1
            w1_lock = EvolutionLockSpec.model_validate(lock_revs[-1]["payload"])

            # Attach Wave-2 datasets & run incremental_new grounding
            for kind, fname in (
                ("parquet", "public_am/wave2_eu_custody_lei_and_drift.parquet"),
                ("csv", "public_am/wave2_ambiguous_otc_collateral.csv"),
                ("csv", "public_am/prime_allocations_w2.csv"),
            ):
                svc.attach_source(
                    proj.project_id,
                    SourceAttachReq(kind=kind, uri_or_filename=fname),
                )

            w2_gnd_r = await client.post(
                "/api/v1/ontology/ground",
                json={"project_id": proj.project_id, "policy": {"mode": "agentic"}},
            )
            assert w2_gnd_r.status_code == 200, w2_gnd_r.text
            w2_resp = GroundingResponse.model_validate(w2_gnd_r.json())
            _assert_no_pii_or_canary_leaks(w2_gnd_r.text, w2_resp.model_dump_json())

            # Assert OverlayKnowledgeSource reuse + locked_hub_bks/locked_links preservation + PII BK exclusion
            assert any(
                ch.origin_kind == "project_graph_a"
                for t in w2_resp.tables
                for c in t.columns
                for ch in c.choices
            ) or any(
                a.origin_kind == "project_graph_a"
                for t in w2_resp.tables
                for a in t.inferred_table_anchors
            )

            t_profiles = []
            t_id_map: dict[str, str] = {}
            for s in svc.list_sources(proj.project_id):
                prof_raw = state_mgr.get_source_record(proj.project_id, s.source_id).get("profile")
                if prof_raw is not None:
                    prof = SourceProfile.model_validate(prof_raw)
                    t_profiles.extend(prof.tables)
                    for tp in prof.tables:
                        t_id_map[tp.table_id] = tp.table_id
                        t_id_map.setdefault(tp.table_name, tp.table_id)
            rcpt_map = {
                rc.receipt_id: rc
                for row in state_mgr.list_events_or_receipts(
                    proj.project_id, record_kind="grounding_receipt"
                )
                if (rc := GroundingReceipt.model_validate(row["payload"])).provenance
                == "server_verified"
            }
            w2_proj = project_grounding_to_deltas(
                w2_resp,
                table_profiles=t_profiles,
                existing_ontology=w1_ont,
                table_id_map=t_id_map,
                receipts_by_id=rcpt_map,
                evolution_locks=[w1_lock],
            )
            existing_uris = {c.concept_uri for c in w1_ont.concepts}
            for op in w2_proj.suggested_deltas:
                if op.op_type == "AddConcept":
                    assert op.concept_uri not in existing_uris
                    for attr in op.attributes:
                        if "aba" in attr.name.lower() or "tax" in attr.name.lower():
                            assert attr.is_business_key is False
                elif op.op_type == "AddAttribute" and (
                    "aba" in op.attribute.name.lower() or "tax" in op.attribute.name.lower()
                ):
                    assert op.attribute.is_business_key is False
            compile_and_apply_deltas(
                w1_ont,
                w2_proj.suggested_deltas,
                actor="agent",
                locks=[w1_lock],
                matcher=svc.matcher,
                receipts_by_id=rcpt_map,
            )

    # .opendv.zip export/import imported_unverified isolation preserving unchanged base mappings (SCEN-07)
    bundle_path = tmp_path / f"{proj.project_id}.opendv.zip"
    export_project_bundle(
        state_mgr,
        project_id=proj.project_id,
        output_path=bundle_path,
        mode="core_project_state",
    )
    imp_ws = tmp_path / "imp_ws"
    imp_ws.mkdir(parents=True, exist_ok=True)
    imp_svc = OpenDVService(imp_ws, state_dir=tmp_path / "imp_state")
    imp_report = imp_svc.import_bundle(
        bundle_path, conflict="fail", enforce_workspace_containment=False
    )
    imp_rows = imp_svc.state_manager.list_events_or_receipts(
        imp_report.project_id, record_kind="grounding_receipt"
    )
    if provider == "claude-cli":
        assert len(imp_rows) >= 1
    for row in imp_rows:
        assert GroundingReceipt.model_validate(row["payload"]).provenance == "imported_unverified"

    imp_yaml, _ = imp_svc.export_ontology(imp_report.project_id, fmt="yaml")
    imp_dict = yaml.safe_load(imp_yaml)
    imp_dict["concepts"].append(
        {
            "concept_uri": "urn:asset_management:AuditPlaceholder",
            "concept_name": "Audit Placeholder",
            "description": "Unrelated concept added via YAML edit post-import.",
            "key_authority_tier": "TIER_3_LOCAL_SCOPED",
        }
    )
    imp_base_rev = imp_svc.get_project(imp_report.project_id).active_ontology_rev_id
    imp_svc.commit_ontology(
        imp_report.project_id,
        OntologyCommitReq(
            yaml_text=yaml.safe_dump(imp_dict, sort_keys=False),
            base_revision_id=imp_base_rev,
            rationale="Unrelated YAML commit preserving unchanged base mappings.",
            waive_quarantine=True,
        ),
    )
    after_ont = imp_svc.get_ontology_bundle(imp_report.project_id).ontology
    before_maps = {c.concept_uri: c.external_mappings for c in w1_ont.concepts}
    after_maps = {c.concept_uri: c.external_mappings for c in after_ont.concepts}
    for curi, expected_maps in before_maps.items():
        assert after_maps[curi] == expected_maps

    # SCEN-07: ungrounded concept without waiver fails closed
    imp_cur_rev = imp_svc.get_project(imp_report.project_id).active_ontology_rev_id
    with pytest.raises(OpenDVError) as q_exc:
        imp_svc.commit_ontology(
            imp_report.project_id,
            OntologyCommitReq(
                operations=[
                    AddConcept(
                        concept_uri="urn:asset_management:QuarantinedCheck",
                        concept_name="QuarantinedCheck",
                        description="Must fail closed without evidence or waiver.",
                        key_authority_tier="TIER_3_LOCAL_SCOPED",
                    )
                ],
                base_revision_id=imp_cur_rev,
                rationale="Verify SCEN-07 fail-closed quarantine.",
            ),
        )
    assert q_exc.value.error_code == ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED

    if imp_rows:
        unv_rcpt = GroundingReceipt.model_validate(imp_rows[0]["payload"])
        with pytest.raises(OpenDVError) as rc_exc:
            imp_svc.commit_ontology(
                imp_report.project_id,
                OntologyCommitReq(
                    operations=[
                        AddConcept(
                            concept_uri="urn:asset_management:LaunderedImport",
                            concept_name="LaunderedImport",
                            description="Must reject imported_unverified receipt.",
                            key_authority_tier="TIER_1_GLOBAL_STANDARD",
                            table_bindings=[
                                ConceptTableBinding(
                                    source_id=unv_rcpt.table_id.split(":", 1)[0],
                                    table_id=unv_rcpt.table_id,
                                    col_name=unv_rcpt.source_column or "cik",
                                )
                            ],
                            external_mappings=[
                                ExternalMapping(
                                    standard_uri=unv_rcpt.standard_uri,
                                    reference_pack_id=unv_rcpt.reference_pack_id,
                                    reference_pack_sha256=unv_rcpt.reference_pack_sha256,
                                    mapping_type=unv_rcpt.mapping_type,
                                    confidence=unv_rcpt.confidence,
                                    grounding_receipt_id=unv_rcpt.receipt_id,
                                )
                            ],
                        )
                    ],
                    base_revision_id=imp_cur_rev,
                    rationale="Verify imported_unverified receipt cannot pass as server_verified.",
                ),
            )
        assert rc_exc.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    assert snapshot_dir_mtimes(env.host_local_opendv) == pre_mtimes


@pytest.mark.live_provider
@pytest.mark.parametrize("provider", ["agy", "claude-cli"])
def test_uat_gnd_live_03_northwind_cross_domain_generalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["agy", "claude-cli"],
) -> None:
    """UAT-GND-LIVE-03: Northwind SQLite + structured YAML glossary with zero FIBO URI leakage."""
    resolve_cli_binary(provider)
    env = setup_isolated_env(tmp_path, monkeypatch, preserve_live_auth=True)
    pre_mtimes = snapshot_dir_mtimes(env.host_local_opendv)

    northwind_dir = env.ws / "public_sqlite"
    PublicDataFetcher(output_dir=tmp_path, mode="fixture").fetch_northwind(
        target_dir=northwind_dir
    )
    assert (northwind_dir / "northwind.sqlite").is_file()

    glossary_path = env.ws / "northwind_glossary.yaml"
    glossary_path.write_text(_northwind_glossary_yaml(), encoding="utf-8")

    svc = env.make_service()
    typed_provider: HarnessProvider = provider
    svc.update_settings(WorkspaceSettings(harness_provider=typed_provider, timeout_seconds=240))
    proj = svc.init_project(
        ProjectInitReq(project_id=f"live-nw-{provider}", domain_namespace="commercial_retail")
    )
    attach_resp = svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename="public_sqlite/northwind.sqlite",
            selected_tables=["Customers", "Orders", "Products"],
        ),
    )
    assert attach_resp.table_count == 3

    cli_env = _live_cli_env(env.ws, env.opendv_home, tmp_path)
    with run_loopback_api_server(svc) as base_url:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from opendv.cli.main import main; raise SystemExit(main())",
                "--json",
                "--api-url",
                base_url,
                "ontology",
                "ground",
                "--project",
                proj.project_id,
                "--knowledge-source",
                "glossary",
                "--glossary",
                str(glossary_path),
                "--mode",
                "agentic",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=cli_env,
            timeout=300,
            check=False,
        )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    _assert_no_pii_or_canary_leaks(proc.stdout, proc.stderr)
    assert "edmcouncil.org/fibo" not in proc.stdout
    assert "edmcouncil.org/fibo" not in proc.stderr

    envelope = json.loads(proc.stdout)
    assert envelope["ok"] is True
    resp = GroundingResponse.model_validate(envelope["data"])
    assert "edmcouncil.org/fibo" not in resp.model_dump_json()
    assert resp.mode_used == "agentic_reranked"
    assert resp.knowledge_source_id == "glossary"
    assert len(resp.tables) == 3
    all_cols = [c for t in resp.tables for c in t.columns]
    assert len(all_cols) >= 6
    assert any(
        ch.entry_id.startswith("urn:opendv:glossary:")
        for c in all_cols
        for ch in c.choices
    )
    assert any(c.status == "MATCHED" for c in all_cols)
    assert snapshot_dir_mtimes(env.host_local_opendv) == pre_mtimes


@pytest.mark.live_provider
def test_uat_gnd_live_04_fibo_sync_drift_and_repin_non_agentic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-GND-LIVE-04: Pure-JSON `fibo-sync` repins Wave-1 project (0 rdflib) + validates."""
    env = setup_isolated_env(tmp_path, monkeypatch, preserve_live_auth=True)
    pre_mtimes = snapshot_dir_mtimes(env.host_local_opendv)
    _copy_public_am(env.ws, ("am_sec_treasury_ibor.sqlite",))
    svc = env.make_service()

    pack_v1 = env.ws / "pack_v1.json"
    pack_v1.write_bytes(
        json.dumps(
            _sync_pack_dict(deprecated_bond=False, keep_yield=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    sub_env = env.subprocess_env()
    install_v1 = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from opendv.cli.main import main; "
                "rc = main(); assert 'rdflib' not in sys.modules; raise SystemExit(rc)"
            ),
            "--json",
            "ontology",
            "fibo-sync",
            "--pack-file",
            str(pack_v1),
            "--tag",
            "v2026q1",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(env.ws),
        env=sub_env,
        check=False,
    )
    assert install_v1.returncode == 0, install_v1.stderr or install_v1.stdout
    sha_v1 = json.loads(install_v1.stdout)["data"]["pack_sha256"]

    proj = svc.init_project(
        ProjectInitReq(project_id="live-fibo-sync", domain_namespace="asset_management")
    )
    src_sum = svc.attach_source(
        proj.project_id,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename="public_am/am_sec_treasury_ibor.sqlite",
            selected_tables=["securities", "yield_curves"],
        ),
    )
    bond_uri = f"{_FIBO_BASE}/SEC/Debt/Bonds/Bond"
    acct_uri = f"{_FIBO_BASE}/FBC/Accounts/Account"
    yield_uri = f"{_FIBO_BASE}/IND/InterestRates/InterestRates/InterestRate"

    svc.commit_ontology(
        proj.project_id,
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:opendv:asset_management:bond",
                    concept_name="Bond",
                    description="Debt security",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                    external_mappings=[
                        ExternalMapping(
                            standard_uri=bond_uri,
                            reference_pack_id="fibo-live-sync-v2",
                            reference_pack_sha256=sha_v1,
                            mapping_type="skos:exactMatch",
                            confidence=Decimal("0.9100"),
                        )
                    ],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id=src_sum.source_id,
                            table_id=f"{src_sum.source_id}:securities",
                            col_name="cusip",
                        )
                    ],
                    attributes=[
                        ConceptAttribute(
                            name="cusip",
                            logical_type="UTF8_STRING",
                            sql_type_hint="VARCHAR",
                            nullable=False,
                            is_business_key=True,
                            is_pii=False,
                        )
                    ],
                ),
                AddConcept(
                    concept_uri="urn:opendv:asset_management:yield",
                    concept_name="Yield",
                    description="Interest rate return",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    external_mappings=[
                        ExternalMapping(
                            standard_uri=yield_uri,
                            reference_pack_id="fibo-live-sync-v2",
                            reference_pack_sha256=sha_v1,
                            mapping_type="skos:closeMatch",
                            confidence=Decimal("0.8500"),
                        )
                    ],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id=src_sum.source_id,
                            table_id=f"{src_sum.source_id}:yield_curves",
                            col_name="curve_id",
                        )
                    ],
                    attributes=[
                        ConceptAttribute(
                            name="curve_id",
                            logical_type="UTF8_STRING",
                            sql_type_hint="VARCHAR",
                            nullable=False,
                            is_business_key=True,
                            is_pii=False,
                        )
                    ],
                ),
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Seed committed Wave-1 Bond + Yield concepts for FIBO drift audit.",
        ),
    )

    pack_v2 = env.ws / "pack_v2.json"
    pack_v2.write_bytes(
        json.dumps(
            _sync_pack_dict(deprecated_bond=True, keep_yield=False),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    repin_proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from opendv.cli.main import main; "
                "rc = main(); assert 'rdflib' not in sys.modules; raise SystemExit(rc)"
            ),
            "--json",
            "ontology",
            "fibo-sync",
            "--pack-file",
            str(pack_v2),
            "--tag",
            "v2026q2",
            "--check-project",
            proj.project_id,
            "--apply-repin",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(env.ws),
        env=sub_env,
        check=False,
    )
    assert repin_proc.returncode == 0, repin_proc.stderr or repin_proc.stdout
    data = json.loads(repin_proc.stdout)["data"]
    report = data["drift_report"]
    tiers = {item["tier"] for item in report["items"]}
    assert "deprecated_with_replacement" in tiers
    assert "removed_or_split" in tiers
    assert report["repin_deltas_applied"] >= 1

    bundle = svc.get_ontology_bundle(proj.project_id)
    by_name = {c.concept_name: c for c in bundle.ontology.concepts}
    assert by_name["Bond"].external_mappings[0].standard_uri == acct_uri
    assert by_name["Yield"].external_mappings[0].standard_uri == yield_uri

    # Run `opendv ontology validate` after --apply-repin (asserting zero rdflib usage)
    repinned_yaml_path = env.ws / "repinned_ont.yaml"
    yaml_text, _ = svc.export_ontology(proj.project_id, fmt="yaml")
    repinned_yaml_path.write_text(yaml_text, encoding="utf-8")
    with run_loopback_api_server(svc) as base_url:
        val_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; from opendv.cli.main import main; "
                    "rc = main(); assert 'rdflib' not in sys.modules; raise SystemExit(rc)"
                ),
                "--json",
                "--api-url",
                base_url,
                "ontology",
                "validate",
                "--project",
                proj.project_id,
                "--yaml",
                str(repinned_yaml_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(env.ws),
            env={**sub_env, "OPENDV_WORKSPACE_ROOTS": str(env.ws)},
            check=False,
        )
    assert val_proc.returncode == 0, val_proc.stderr or val_proc.stdout
    val_env = json.loads(val_proc.stdout)
    assert val_env["ok"] is True
    assert val_env["data"]["valid"] is True
    assert snapshot_dir_mtimes(env.host_local_opendv) == pre_mtimes
