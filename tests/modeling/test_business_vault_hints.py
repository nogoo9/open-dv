"""End-to-end Business Vault Hints & Survey tests (Stage A zero-hints survey & Stage B DuckDB)."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from typer.testing import CliRunner

from opendv.cli.main import app as cli_app
from opendv.modeling.models import VerifyDraftRequest
from opendv.modeling.verify_mode import verify_draft_vault
from opendv.models.modeling_preferences import ModelingPreferences
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    GraphAOntology,
)
from opendv.models.vault_ir import (
    BridgeHopSpec,
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftBridgeSpec,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_vault import ProfileBundle
from opendv.service.workflows.modeling_stateless import profile_sources_stateless
from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter
from opendv.storage.prover.edge_batches import _insert_rows
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.storage.sql_compiler.ddl_builders import qualify_name
from opendv.storage.sql_compiler.dialect_hashing import zero_key_hex_sql

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "business_vault_hints"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
RAW_PII_SENTINELS = ("ops@beacon.io", "billing@acme.io", "old@beacon.io")


def _invoke_profile_cli(
    max_questions: int,
    questions_offset: int,
) -> tuple[str, ProfileBundle]:
    runner = CliRunner()
    res = runner.invoke(
        cli_app,
        [
            "--json",
            "model",
            "profile",
            "--sources-file",
            str(MANIFEST_PATH),
            "--max-questions",
            str(max_questions),
            "--questions-offset",
            str(questions_offset),
        ],
    )
    assert res.exit_code == 0, f"CLI failed (exit={res.exit_code}): {res.output}"
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    return res.stdout, ProfileBundle.model_validate(payload["data"])


def test_stage_a_zero_hints_survey_and_pii_masking() -> None:
    """Stage A: stateless `opendv model profile` surfaces P1, P2, P3 with masked PII."""
    raw_p1, page1 = _invoke_profile_cli(max_questions=3, questions_offset=0)
    assert page1.returned_count == 3
    assert page1.remaining_count >= 2
    assert page1.total_candidates >= 5
    for secret_email in RAW_PII_SENTINELS:
        assert secret_email not in raw_p1

    _, page2 = _invoke_profile_cli(max_questions=3, questions_offset=3)
    assert page2.offset == 3
    assert page2.returned_count >= 1
    ids1 = {q.question_id for q in page1.questions}
    ids2 = {q.question_id for q in page2.questions}
    assert ids1.isdisjoint(ids2)

    raw_all, all_bundle = _invoke_profile_cli(max_questions=0, questions_offset=0)
    assert all_bundle.returned_count == all_bundle.total_candidates
    assert all_bundle.remaining_count == 0
    for secret_email in RAW_PII_SENTINELS:
        assert secret_email not in raw_all

    p1_q = next((q for q in all_bundle.questions if q.question_id.startswith("Q-P1-customer")), None)
    assert p1_q is not None, f"Missing Q-P1-customer in {[q.question_id for q in all_bundle.questions]}"
    samples_blob = "\n".join(p1_q.sample_rows)
    assert "<NON_NULL>" in samples_blob and "<NULL>" in samples_blob

    p2_bridge = next(
        (
            q
            for q in all_bundle.questions
            if q.question_id.startswith("Q-P2-trade-fund")
            or "trade -> account -> fund" in " ".join(q.sample_rows)
        ),
        None,
    )
    assert p2_bridge is not None, f"Missing P2 trade->account->fund in {[q.question_id for q in all_bundle.questions]}"

    p3_mgr = next(
        (
            q
            for q in all_bundle.questions
            if q.question_id.startswith("Q-P3-") and "crm_account_mgr" in q.title
        ),
        None,
    )
    assert p3_mgr is not None, f"Missing P3 crm_account_mgr in {[q.question_id for q in all_bundle.questions]}"


def _build_stage_b_ontology(profiles_by_sid: dict[str, str]) -> GraphAOntology:
    erp_tid = profiles_by_sid["erp"]
    crm_tid = profiles_by_sid["crm"]
    mgr_tid = profiles_by_sid["crm_mgr"]
    fund_tid = profiles_by_sid["funds"]
    acct_tid = profiles_by_sid["accounts"]
    trd_tid = profiles_by_sid["trades"]

    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="financial_operations",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:finops:Customer",
                concept_name="Customer",
                description="Master customer entity across ERP and CRM.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="cust_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="erp", table_id=erp_tid, col_name="cust_id"),
                    ConceptTableBinding(source_id="crm", table_id=crm_tid, col_name="cust_id"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:finops:SalesRep",
                concept_name="SalesRep",
                description="Account manager representative.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="rep_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="crm_mgr", table_id=mgr_tid, col_name="rep_id"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:finops:Fund",
                concept_name="Fund",
                description="Investment fund product.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="fund_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="funds", table_id=fund_tid, col_name="fund_id"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:finops:Account",
                concept_name="Account",
                description="Customer investment account.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="account_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="accounts", table_id=acct_tid, col_name="account_id"),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:finops:Trade",
                concept_name="Trade",
                description="Immutable execution trade event.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="trade_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="trades", table_id=trd_tid, col_name="trade_id"),
                ],
            ),
        ],
    )


def _build_stage_b_draft(bundle: ProfileBundle) -> tuple[GraphAOntology, DraftVaultIR]:
    tids = {p.source_id: p.tables[0].table_id for p in bundle.profiles}
    ontology = _build_stage_b_ontology(tids)

    erp_tid, crm_tid, mgr_tid = tids["erp"], tids["crm"], tids["crm_mgr"]
    fund_tid, acct_tid, trd_tid = tids["funds"], tids["accounts"], tids["trades"]

    hubs = [
        DraftHubSpec(
            hub_name="hub_customer",
            key_tier="TIER_1_GLOBAL_STANDARD",
            survivorship_priority=["erp", "crm"],
            business_keys=[
                BusinessKeyPart(
                    table_id=erp_tid,
                    col_name="cust_id",
                    source_id="erp",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                ),
                BusinessKeyPart(
                    table_id=crm_tid,
                    col_name="cust_id",
                    source_id="crm",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="erp", table_id=erp_tid, col_mappings={"cust_id": "cust_id"}),
                DraftSourceBinding(source_id="crm", table_id=crm_tid, col_mappings={"cust_id": "cust_id"}),
            ],
        ),
        DraftHubSpec(
            hub_name="hub_rep",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=mgr_tid,
                    col_name="rep_id",
                    source_id="crm_mgr",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="crm_mgr", table_id=mgr_tid, col_mappings={"rep_id": "rep_id"}),
            ],
        ),
        DraftHubSpec(
            hub_name="hub_fund",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=fund_tid,
                    col_name="fund_id",
                    source_id="funds",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="funds", table_id=fund_tid, col_mappings={"fund_id": "fund_id"}),
            ],
        ),
        DraftHubSpec(
            hub_name="hub_account",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=acct_tid,
                    col_name="account_id",
                    source_id="accounts",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="accounts", table_id=acct_tid, col_mappings={"account_id": "account_id"}),
            ],
        ),
        DraftHubSpec(
            hub_name="hub_trade",
            key_tier="TIER_1_GLOBAL_STANDARD",
            business_keys=[
                BusinessKeyPart(
                    table_id=trd_tid,
                    col_name="trade_id",
                    source_id="trades",
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    bkcc_value="GLOBAL",
                )
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trades", table_id=trd_tid, col_mappings={"trade_id": "trade_id"}),
            ],
        ),
    ]

    links = [
        DraftLinkSpec(
            link_name="t_lnk_trade_account",
            link_kind="TRANSACTIONAL",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_trade",
                    role_name="TRADE",
                    fk_col_name="hk_trade",
                    source_col_names=["trade_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_account",
                    role_name="ACCOUNT",
                    fk_col_name="hk_account",
                    source_col_names=["account_id"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="trades", table_id=trd_tid, col_mappings={}),
            ],
            attributes=[
                DraftField(name="trade_ts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                DraftField(
                    name="notional_usd",
                    logical_type=DecimalType(precision=18, scale=2),
                    nullable=False,
                    is_pii=False,
                ),
                DraftField(name="side", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
        ),
        DraftLinkSpec(
            link_name="lnk_account_fund",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_account",
                    role_name="ACCOUNT",
                    fk_col_name="hk_account",
                    source_col_names=["account_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_fund",
                    role_name="FUND",
                    fk_col_name="hk_fund",
                    source_col_names=["fund_id"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="accounts", table_id=acct_tid, col_mappings={}),
            ],
        ),
        DraftLinkSpec(
            link_name="lnk_account_customer",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_account",
                    role_name="ACCOUNT",
                    fk_col_name="hk_account",
                    source_col_names=["account_id"],
                ),
                LinkParticipantSpec(
                    hub_name="hub_customer",
                    role_name="CUSTOMER",
                    fk_col_name="hk_customer",
                    source_col_names=["cust_id"],
                    target_hub_binding_table_id=erp_tid,
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="accounts", table_id=acct_tid, col_mappings={}),
            ],
        ),
        DraftLinkSpec(
            link_name="lnk_customer_rep_crm",
            link_kind="STANDARD",
            driving_hub="hub_customer",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_customer",
                    role_name="CUSTOMER",
                    fk_col_name="hk_customer",
                    source_col_names=["cust_id"],
                    target_hub_binding_table_id=erp_tid,
                ),
                LinkParticipantSpec(
                    hub_name="hub_rep",
                    role_name="REP",
                    fk_col_name="hk_rep",
                    source_col_names=["rep_id"],
                ),
            ],
            source_bindings=[
                DraftSourceBinding(source_id="crm_mgr", table_id=mgr_tid, col_mappings={}),
            ],
        ),
    ]

    cust_core_attrs = [
        DraftField(name="legal_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="credit_tier", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        DraftField(name="billing_region", logical_type="UTF8_STRING", nullable=True, is_pii=False),
    ]
    cust_pii_attrs = [
        DraftField(name="email", logical_type="UTF8_STRING", nullable=True, is_pii=True),
    ]

    satellites = [
        DraftSatelliteSpec(
            sat_name="sat_customer_erp",
            sat_kind="STANDARD",
            parent_name="hub_customer",
            source_bindings=[DraftSourceBinding(source_id="erp", table_id=erp_tid, col_mappings={})],
            attributes=cust_core_attrs,
        ),
        DraftSatelliteSpec(
            sat_name="sat_customer_erp_pii",
            sat_kind="PII",
            parent_name="hub_customer",
            source_bindings=[DraftSourceBinding(source_id="erp", table_id=erp_tid, col_mappings={})],
            attributes=cust_pii_attrs,
        ),
        DraftSatelliteSpec(
            sat_name="sat_customer_crm",
            sat_kind="STANDARD",
            parent_name="hub_customer",
            source_bindings=[DraftSourceBinding(source_id="crm", table_id=crm_tid, col_mappings={})],
            attributes=cust_core_attrs,
        ),
        DraftSatelliteSpec(
            sat_name="sat_customer_crm_pii",
            sat_kind="PII",
            parent_name="hub_customer",
            source_bindings=[DraftSourceBinding(source_id="crm", table_id=crm_tid, col_mappings={})],
            attributes=cust_pii_attrs,
        ),
        DraftSatelliteSpec(
            sat_name="sat_fund_core",
            sat_kind="STANDARD",
            parent_name="hub_fund",
            source_bindings=[DraftSourceBinding(source_id="funds", table_id=fund_tid, col_mappings={})],
            attributes=[
                DraftField(name="fund_name", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                DraftField(name="base_currency", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
        ),
        DraftSatelliteSpec(
            sat_name="sat_account_core",
            sat_kind="STANDARD",
            parent_name="hub_account",
            source_bindings=[DraftSourceBinding(source_id="accounts", table_id=acct_tid, col_mappings={})],
            attributes=[
                DraftField(name="account_type", logical_type="UTF8_STRING", nullable=False, is_pii=False),
            ],
        ),
        DraftSatelliteSpec(
            sat_name="esat_customer_rep_crm",
            sat_kind="ESAT",
            parent_name="lnk_customer_rep_crm",
            source_bindings=[
                DraftSourceBinding(
                    source_id="crm_mgr",
                    table_id=mgr_tid,
                    col_mappings={"assigned_at": "assigned_at"},
                )
            ],
            is_bitemporal=True,
            applied_dts_col="assigned_at",
            load_mode="APPEND_WITH_EFFECTIVITY",
            driving_keys=["hk_customer"],
            attributes=[],
        ),
    ]

    bridges = [
        DraftBridgeSpec(
            bridge_name="brg_trade_account_fund",
            root_hub="hub_trade",
            hops=[
                BridgeHopSpec(
                    link_name="t_lnk_trade_account",
                    from_fk_col="hk_trade",
                    to_fk_col="hk_account",
                ),
                BridgeHopSpec(
                    link_name="lnk_account_fund",
                    from_fk_col="hk_account",
                    to_fk_col="hk_fund",
                ),
            ],
        )
    ]

    triage = [
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name=col.col_name,
            disposition="MODELED",
            target_ref=tbl.table_name,
        )
        for prof in bundle.profiles
        for tbl in prof.tables
        for col in tbl.columns
    ]

    draft = DraftVaultIR(
        project_id="bv-hints-demo",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=hubs,
        links=links,
        satellites=satellites,
        bridges=bridges,
        column_triage=triage,
    )
    return ontology, draft


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [{k: (None if v == "" else v) for k, v in row.items()} for row in csv.DictReader(fh)]


def test_stage_b_authoritative_precedence_verify_compile_and_duckdb(tmp_path: Path) -> None:
    """Stage B: certify DraftVaultIR via API + CLI, compile DuckDB SQL, and verify query semantics."""
    bundle = profile_sources_stateless(sources_file=MANIFEST_PATH, max_questions=0)
    ontology, draft = _build_stage_b_draft(bundle)
    prefs = ModelingPreferences(
        low_cardinality_strategy="keep_in_satellite",
        link_cardinality_strategy="allow_transactional_links",
    )

    req = VerifyDraftRequest(
        draft_ir=draft,
        profiles=bundle.profiles,
        ontology=ontology,
        policy="balanced",
        modeling_preferences=prefs,
        prover=DuckDbProverAdapter(),
        source_rows_by_table=bundle.source_rows_by_table,
        table_sample_flags=bundle.table_sample_flags,
        real_row_proving=True,
    )
    verify_res = verify_draft_vault(req)
    assert verify_res.status == "CERTIFIED_FAST", (
        f"Unexpected status={verify_res.status}; diags={verify_res.verifier_report.all_diagnostics}"
    )
    assert verify_res.final_energy_e == 0

    # CLI `opendv --json model verify` parity check
    req_path = tmp_path / "verify_req.json"
    draft_path = tmp_path / "draft_ir.json"
    prof_path = tmp_path / "profile_bundle.json"
    req_path.write_text(
        json.dumps(
            {
                "ontology": ontology.model_dump(mode="json"),
                "policy": "balanced",
                "modeling_preferences": prefs.model_dump(mode="json"),
                "real_row_proving": True,
            }
        ),
        encoding="utf-8",
    )
    draft_path.write_text(draft.model_dump_json(), encoding="utf-8")
    prof_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    cli_res = CliRunner().invoke(
        cli_app,
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(draft_path),
            "--profile",
            str(prof_path),
        ],
    )
    assert cli_res.exit_code == 0, f"CLI verify failed: {cli_res.output}"
    cli_payload = json.loads(cli_res.stdout)
    assert cli_payload["ok"] is True
    assert cli_payload["data"]["status"] == "CERTIFIED_FAST"
    assert cli_payload["data"]["final_energy_e"] == 0

    # Compile CertifiedVaultIR to duckdb_local and execute against actual CSV files
    cert_ir = verify_res.certified_ir
    compiled = SqlGlotVaultCompiler("duckdb_local").compile(cert_ir)
    csv_by_table_name = {
        "erp_customers": _load_csv_rows(FIXTURE_DIR / "erp_customers.csv"),
        "crm_customers": _load_csv_rows(FIXTURE_DIR / "crm_customers.csv"),
        "crm_account_mgr": _load_csv_rows(FIXTURE_DIR / "crm_account_mgr.csv"),
        "funds": _load_csv_rows(FIXTURE_DIR / "funds.csv"),
        "accounts": _load_csv_rows(FIXTURE_DIR / "accounts.csv"),
        "trades": _load_csv_rows(FIXTURE_DIR / "trades.csv"),
    }

    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for stmt in compiled.ddl_statements:
            conn.execute(stmt)
        for bronze in cert_ir.bronze_tables:
            tname = bronze.source_table_id.split(":", 1)[-1]
            b_full = qualify_name("Z0", bronze.table_name, compiled.physical_aliases)
            _insert_rows(conn, b_full, [c.name for c in bronze.columns], csv_by_table_name[tname])
        for stmt in compiled.dml_statements:
            conn.execute(stmt)

        # 1. Survivorship + NULL fallback + PII isolation in information_mart.vw_customer_current
        mart_cols = [
            row[0]
            for row in conn.execute("DESCRIBE information_mart.vw_customer_current").fetchall()
        ]
        assert "email" not in mart_cols
        pii_tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'raw_vault' AND table_name LIKE 'sat_customer_%_pii'"
            ).fetchall()
        }
        assert {"sat_customer_erp_pii", "sat_customer_crm_pii"} <= pii_tables
        cust_rows = conn.execute(
            "SELECT cust_id, legal_name, credit_tier, billing_region "
            "FROM information_mart.vw_customer_current ORDER BY cust_id ASC"
        ).fetchall()
        assert cust_rows == [
            ("C-100", "Acme Capital", "AAA", "US-EAST"),
            ("C-200", "Beacon Holdings", "AA", "EU-WEST"),
        ]

        # 2. Multi-hop Bridge resolution (T-900 -> A-10 -> F-01)
        brg_rows = conn.execute(
            "SELECT ht.trade_id, ha.account_id, hf.fund_id "
            "FROM business_vault.brg_trade_account_fund AS b "
            "INNER JOIN raw_vault.hub_trade AS ht ON ht.hk_trade = b.hk_trade "
            "INNER JOIN raw_vault.hub_account AS ha ON ha.hk_account = b.hk_account "
            "INNER JOIN raw_vault.hub_fund AS hf ON hf.hk_fund = b.hk_fund "
            "ORDER BY ht.trade_id ASC"
        ).fetchall()
        assert brg_rows == [("T-900", "A-10", "F-01"), ("T-901", "A-20", "F-02")]

        # 3. ESAT intervals for C-100: REP-1 closed at 2026-02-01, REP-2 open/active
        iv_rows = conn.execute(
            "SELECT hr.rep_id, "
            "STRFTIME(iv.effective_from AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "STRFTIME(iv.effective_to AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ'), "
            "iv.is_current "
            "FROM business_vault.vw_esat_customer_rep_crm_intervals AS iv "
            "INNER JOIN raw_vault.lnk_customer_rep_crm AS lnk "
            "ON lnk.hk_customer_rep_crm = iv.hk_customer_rep_crm "
            "INNER JOIN raw_vault.hub_customer AS hc ON hc.hk_customer = iv.hk_customer "
            "INNER JOIN raw_vault.hub_rep AS hr ON hr.hk_rep = lnk.hk_rep "
            "WHERE hc.cust_id = 'C-100' "
            "ORDER BY iv.effective_from ASC"
        ).fetchall()
        assert iv_rows == [
            ("REP-1", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", False),
            ("REP-2", "2026-02-01T00:00:00Z", None, True),
        ]

        # 4. Transactional Link raw_vault.t_lnk_trade_account contains immutable trade rows
        z0 = zero_key_hex_sql("duckdb", is_error=False)
        zf = zero_key_hex_sql("duckdb", is_error=True)
        trd_rows = conn.execute(
            "SELECT ht.trade_id, ha.account_id, t.notional_usd, t.side "
            "FROM raw_vault.t_lnk_trade_account AS t "
            "INNER JOIN raw_vault.hub_trade AS ht ON ht.hk_trade = t.hk_trade "
            "INNER JOIN raw_vault.hub_account AS ha ON ha.hk_account = t.hk_account "
            f"WHERE t.hk_trade_account NOT IN ({z0}, {zf}) "
            "ORDER BY ht.trade_id ASC"
        ).fetchall()
        assert trd_rows == [
            ("T-900", "A-10", Decimal("125000.00"), "BUY"),
            ("T-901", "A-20", Decimal("50000.00"), "SELL"),
        ]
    finally:
        conn.close()


def test_dv_str_009_suggest_fix_mentions_link_attributes_and_owning_hub() -> None:
    """_suggest_fix gives concrete DraftLinkSpec.attributes / Hub remediation for DV-STR-009."""
    from opendv.modeling.verify_mode import _suggest_fix
    from opendv.models import VerifierDiagnostic

    hint = _suggest_fix(
        VerifierDiagnostic(
            gate="V1",
            rule_id="DV-STR-009",
            severity="ERROR",
            entity_name="sat_trade_trades",
            message="TRANSACTIONAL Link t_lnk_trade_account forbids STANDARD Sat.",
        )
    )
    assert "DraftLinkSpec.attributes" in hint
    assert "t_lnk_*" in hint and "owning Hub" in hint
