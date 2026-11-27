"""Phase 4 unit & integration tests for Mode-1 zero-LLM deterministic verification."""

from __future__ import annotations

import pytest

from opendv.modeling.models import VerifyDraftRequest
from opendv.modeling.verify_mode import validate_source_rows_pii_tokenized, verify_draft_vault
from opendv.models.diagnostics import AntiPatternJustification
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptTableBinding,
    GraphAOntology,
)
from opendv.models.pii_tokenizer import MASKED_PII_TOKEN, tokenize_row_pii
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
)
from opendv.storage import DuckDbProverAdapter

_TEST_SALT = b"opendv-mode1-test-salt"


def _build_multi_source_fixture(
    *,
    include_mas_pii: bool = False,
) -> tuple[
    GraphAOntology,
    list[SourceProfile],
    DraftVaultIR,
    dict[str, list[dict[str, object]]],
]:
    """Build a realistic 2-source CRM+ERP Vault fixture with PII-tokenized rows."""
    crm_tbl_id = "src_crm:crm_customers"
    erp_tbl_id = "src_erp:erp_orders"
    mas_tbl_id = "src_crm:crm_contacts"

    crm_tbl = TableProfile(
        table_id=crm_tbl_id,
        table_name="crm_customers",
        row_count_est=2,
        pk_uniqueness_ratio=1.0,
        best_non_surrogate_bk_candidate_col="customer_code",
        best_non_surrogate_bk_uniqueness_ratio=1.0,
        columns=[
            ColumnProfile(
                col_name="customer_code",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=True,
                pk_ord=1,
                null_ratio=0.0,
                distinct_count_est=2,
                sample_values=["CUST-001", "CUST-002"],
            ),
            ColumnProfile(
                col_name="legal_name",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=False,
                null_ratio=0.0,
                distinct_count_est=2,
                sample_values=["Acme Corp", "Beta LLC"],
            ),
            ColumnProfile(
                col_name="primary_email",
                sql_type="VARCHAR",
                nullable=True,
                is_pk=False,
                null_ratio=0.0,
                distinct_count_est=2,
                pii_tags=["EMAIL"],
                sample_values=[MASKED_PII_TOKEN],
            ),
        ],
    )
    erp_tbl = TableProfile(
        table_id=erp_tbl_id,
        table_name="erp_orders",
        row_count_est=2,
        pk_uniqueness_ratio=1.0,
        best_non_surrogate_bk_candidate_col="order_number",
        best_non_surrogate_bk_uniqueness_ratio=1.0,
        columns=[
            ColumnProfile(
                col_name="order_number",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=True,
                pk_ord=1,
                null_ratio=0.0,
                distinct_count_est=2,
                sample_values=["ORD-100", "ORD-101"],
            ),
            ColumnProfile(
                col_name="customer_code",
                sql_type="VARCHAR",
                nullable=False,
                is_pk=False,
                fk_ref="crm_customers.customer_code",
                null_ratio=0.0,
                distinct_count_est=2,
                sample_values=["CUST-001", "CUST-002"],
            ),
            ColumnProfile(
                col_name="order_amount",
                sql_type="DECIMAL(18,2)",
                nullable=False,
                is_pk=False,
                null_ratio=0.0,
                distinct_count_est=2,
                sample_values=["1250.00", "450.50"],
            ),
        ],
    )

    crm_tables = [crm_tbl]
    if include_mas_pii:
        crm_tables.append(
            TableProfile(
                table_id=mas_tbl_id,
                table_name="crm_contacts",
                row_count_est=2,
                pk_uniqueness_ratio=1.0,
                best_non_surrogate_bk_candidate_col="customer_code",
                best_non_surrogate_bk_uniqueness_ratio=1.0,
                columns=[
                    ColumnProfile(
                        col_name="customer_code",
                        sql_type="VARCHAR",
                        nullable=False,
                        is_pk=True,
                        pk_ord=1,
                        fk_ref="crm_customers.customer_code",
                        null_ratio=0.0,
                        distinct_count_est=2,
                        sample_values=["CUST-001", "CUST-002"],
                    ),
                    ColumnProfile(
                        col_name="contact_seq",
                        sql_type="INTEGER",
                        nullable=False,
                        is_pk=True,
                        pk_ord=2,
                        null_ratio=0.0,
                        distinct_count_est=2,
                        sample_values=["1", "2"],
                    ),
                    ColumnProfile(
                        col_name="contact_email",
                        sql_type="VARCHAR",
                        nullable=True,
                        is_pk=False,
                        null_ratio=0.0,
                        distinct_count_est=2,
                        pii_tags=["EMAIL"],
                        sample_values=[MASKED_PII_TOKEN],
                    ),
                ],
            )
        )

    profiles = [
        SourceProfile(
            source_id="src_crm",
            schema_fingerprint="crm_fp_v1",
            table_count=len(crm_tables),
            tables=crm_tables,
        ),
        SourceProfile(
            source_id="src_erp",
            schema_fingerprint="erp_fp_v1",
            table_count=1,
            tables=[erp_tbl],
        ),
    ]

    ontology = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="commercial",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:comm:Customer",
                concept_name="Customer",
                description="Enterprise customer entity across CRM and ERP.",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="CORP_CUST",
                table_bindings=[
                    ConceptTableBinding(
                        source_id="src_crm", table_id=crm_tbl_id, col_name="customer_code"
                    ),
                    ConceptTableBinding(
                        source_id="src_erp", table_id=erp_tbl_id, col_name="customer_code"
                    ),
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:comm:Order",
                concept_name="Order",
                description="Customer sales order.",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="ERP_ORD",
                table_bindings=[
                    ConceptTableBinding(
                        source_id="src_erp", table_id=erp_tbl_id, col_name="order_number"
                    ),
                ],
            ),
        ],
    )

    hubs = [
        DraftHubSpec(
            hub_name="hub_customer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=crm_tbl_id,
                    col_name="customer_code",
                    source_id="src_crm",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="CORP_CUST",
                ),
                BusinessKeyPart(
                    table_id=erp_tbl_id,
                    col_name="customer_code",
                    source_id="src_erp",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="CORP_CUST",
                ),
                *(
                    [
                        BusinessKeyPart(
                            table_id=mas_tbl_id,
                            col_name="customer_code",
                            source_id="src_crm",
                            key_tier="TIER_2_ENTERPRISE_SOR",
                            bkcc_value="CORP_CUST",
                        )
                    ]
                    if include_mas_pii
                    else []
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_crm",
                    table_id=crm_tbl_id,
                    col_mappings={"customer_code": "customer_code"},
                ),
                DraftSourceBinding(
                    source_id="src_erp",
                    table_id=erp_tbl_id,
                    col_mappings={"customer_code": "customer_code"},
                ),
                *(
                    [
                        DraftSourceBinding(
                            source_id="src_crm",
                            table_id=mas_tbl_id,
                            col_mappings={"customer_code": "customer_code"},
                        )
                    ]
                    if include_mas_pii
                    else []
                ),
            ],
            survivorship_priority=["src_crm", "src_erp"],
        ),
        DraftHubSpec(
            hub_name="hub_order",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id=erp_tbl_id,
                    col_name="order_number",
                    source_id="src_erp",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="ERP_ORD",
                )
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_erp",
                    table_id=erp_tbl_id,
                    col_mappings={"order_number": "order_number"},
                )
            ],
        ),
    ]

    links = [
        DraftLinkSpec(
            link_name="lnk_customer_order",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(
                    hub_name="hub_customer",
                    role_name="customer",
                    fk_col_name="hk_customer",
                    source_col_names=["customer_code"],
                    is_nullable=False,
                ),
                LinkParticipantSpec(
                    hub_name="hub_order",
                    role_name="order",
                    fk_col_name="hk_order",
                    source_col_names=["order_number"],
                    is_nullable=False,
                ),
            ],
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_erp",
                    table_id=erp_tbl_id,
                    col_mappings={
                        "customer_code": "customer_code",
                        "order_number": "order_number",
                    },
                )
            ],
        )
    ]

    satellites = [
        DraftSatelliteSpec(
            sat_name="sat_customer_crm",
            sat_kind="STANDARD",
            parent_name="hub_customer",
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_crm",
                    table_id=crm_tbl_id,
                    col_mappings={"legal_name": "legal_name"},
                )
            ],
            attributes=[
                DraftField(
                    name="legal_name",
                    logical_type="UTF8_STRING",
                    nullable=False,
                    is_pii=False,
                )
            ],
        ),
        DraftSatelliteSpec(
            sat_name="sat_customer_crm_pii",
            sat_kind="PII",
            parent_name="hub_customer",
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_crm",
                    table_id=crm_tbl_id,
                    col_mappings={"primary_email": "primary_email"},
                )
            ],
            attributes=[
                DraftField(
                    name="primary_email",
                    logical_type="UTF8_STRING",
                    nullable=True,
                    is_pii=True,
                )
            ],
        ),
        DraftSatelliteSpec(
            sat_name="sat_order_erp",
            sat_kind="STANDARD",
            parent_name="hub_order",
            source_bindings=[
                DraftSourceBinding(
                    source_id="src_erp",
                    table_id=erp_tbl_id,
                    col_mappings={"order_amount": "order_amount"},
                )
            ],
            attributes=[
                DraftField(
                    name="order_amount",
                    logical_type=DecimalType(precision=18, scale=2),
                    nullable=False,
                    is_pii=False,
                )
            ],
        ),
    ]

    triage = [
        ColumnTriageEntry(
            table_id=crm_tbl_id,
            col_name="customer_code",
            disposition="MODELED",
            target_ref="hub_customer",
        ),
        ColumnTriageEntry(
            table_id=crm_tbl_id,
            col_name="legal_name",
            disposition="MODELED",
            target_ref="sat_customer_crm",
        ),
        ColumnTriageEntry(
            table_id=crm_tbl_id,
            col_name="primary_email",
            disposition="MODELED",
            target_ref="sat_customer_crm_pii",
        ),
        ColumnTriageEntry(
            table_id=erp_tbl_id,
            col_name="order_number",
            disposition="MODELED",
            target_ref="hub_order",
        ),
        ColumnTriageEntry(
            table_id=erp_tbl_id,
            col_name="customer_code",
            disposition="MODELED",
            target_ref="lnk_customer_order",
        ),
        ColumnTriageEntry(
            table_id=erp_tbl_id,
            col_name="order_amount",
            disposition="MODELED",
            target_ref="sat_order_erp",
        ),
    ]

    rows: dict[str, list[dict[str, object]]] = {
        crm_tbl_id: [
            tokenize_row_pii(
                {
                    "customer_code": "CUST-001",
                    "legal_name": "Acme Corp",
                    "primary_email": "alice@acme.example",
                },
                pii_columns={"primary_email"},
                salt=_TEST_SALT,
            ),
            tokenize_row_pii(
                {
                    "customer_code": "CUST-002",
                    "legal_name": "Beta LLC",
                    "primary_email": "bob@beta.example",
                },
                pii_columns={"primary_email"},
                salt=_TEST_SALT,
            ),
        ],
        erp_tbl_id: [
            {
                "order_number": "ORD-100",
                "customer_code": "CUST-001",
                "order_amount": "1250.00",
            },
            {
                "order_number": "ORD-101",
                "customer_code": "CUST-002",
                "order_amount": "450.50",
            },
        ],
    }

    if include_mas_pii:
        satellites.append(
            DraftSatelliteSpec(
                sat_name="mas_customer_contact",
                sat_kind="MAS",
                parent_name="hub_customer",
                child_dependent_keys=["contact_seq"],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=mas_tbl_id,
                        col_mappings={
                            "contact_seq": "contact_seq",
                            "contact_email": "contact_email",
                        },
                    )
                ],
                attributes=[
                    DraftField(
                        name="contact_seq",
                        logical_type="INT32",
                        nullable=False,
                        is_pii=False,
                    ),
                    DraftField(
                        name="contact_email",
                        logical_type="UTF8_STRING",
                        nullable=True,
                        is_pii=True,
                    ),
                ],
            )
        )
        triage.extend(
            [
                ColumnTriageEntry(
                    table_id=mas_tbl_id,
                    col_name="customer_code",
                    disposition="MODELED",
                    target_ref="hub_customer",
                ),
                ColumnTriageEntry(
                    table_id=mas_tbl_id,
                    col_name="contact_seq",
                    disposition="MODELED",
                    target_ref="mas_customer_contact",
                ),
                ColumnTriageEntry(
                    table_id=mas_tbl_id,
                    col_name="contact_email",
                    disposition="MODELED",
                    target_ref="mas_customer_contact",
                ),
            ]
        )
        rows[mas_tbl_id] = [
            tokenize_row_pii(
                {
                    "customer_code": "CUST-001",
                    "contact_seq": 1,
                    "contact_email": "ops@acme.example",
                },
                pii_columns={"contact_email"},
                salt=_TEST_SALT,
            ),
            tokenize_row_pii(
                {
                    "customer_code": "CUST-002",
                    "contact_seq": 2,
                    "contact_email": "desk@beta.example",
                },
                pii_columns={"contact_email"},
                salt=_TEST_SALT,
            ),
        ]

    draft = DraftVaultIR(
        project_id="proj-mode1-comm",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=hubs,
        links=links,
        satellites=satellites,
        column_triage=triage,
    )
    return ontology, profiles, draft, rows


def test_mode1_happy_path_multi_source_real_duckdb_proving() -> None:
    """Mode-1 verify_draft_vault certifies clean multi-source DraftVaultIR as CERTIFIED_FAST."""
    ontology, profiles, draft, rows = _build_multi_source_fixture()
    req = VerifyDraftRequest(
        draft_ir=draft,
        profiles=profiles,
        ontology=ontology,
        policy="balanced",
        prover=DuckDbProverAdapter(),
        source_rows_by_table=rows,
        real_row_proving=True,
    )
    res = verify_draft_vault(req)

    assert res.status == "CERTIFIED_FAST"
    assert res.status != "CERTIFIED"
    assert res.next_action == "READY_FOR_DOWNSTREAM"
    assert res.deterministic_energy == 0
    assert res.final_energy_e == 0
    assert res.verifier_report.passed is True
    assert res.evolution_lock is not None
    assert "hub_customer" in res.evolution_lock.locked_hub_bks
    assert res.fragility_summary is not None
    assert len(res.preferences_sha256) == 64
    assert res.verifier_report.prover_report is not None
    assert all(res.verifier_report.schema_proof.values())
    assert res.verifier_report.real_data_proof


def test_mode1_fail_closed_guards() -> None:
    """Mode-1 rejects prompts/guides, missing prover under non-symbolic policy, and untokenized PII."""
    ontology, profiles, draft, rows = _build_multi_source_fixture()

    with pytest.raises(OpenDVError) as hint_exc:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ontology,
                policy="symbolic_only",
                business_context_hints="Prefer raw tables.",
            )
        )
    assert hint_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as guide_exc:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ontology,
                policy="symbolic_only",
                guide_markdown="# Custom guide",
            )
        )
    assert guide_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as prover_exc:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ontology,
                policy="balanced",
                prover=None,
            )
        )
    assert prover_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    sym_res = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ontology,
            policy="symbolic_only",
            prover=None,
        )
    )
    assert sym_res.status == "CERTIFIED_FAST"
    assert sym_res.next_action == "READY_FOR_DOWNSTREAM"

    raw_rows = {
        **rows,
        "src_crm:crm_customers": [
            {
                "customer_code": "CUST-001",
                "legal_name": "Acme Corp",
                "primary_email": "plain@acme.example",
            }
        ],
    }
    with pytest.raises(OpenDVError) as raw_pii_exc:
        validate_source_rows_pii_tokenized(profiles, raw_rows)
    assert raw_pii_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as raw_req_exc:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ontology,
                policy="balanced",
                prover=DuckDbProverAdapter(),
                source_rows_by_table=raw_rows,
                real_row_proving=True,
            )
        )
    assert raw_req_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    masked_rows = {
        **rows,
        "src_crm:crm_customers": [
            {
                "customer_code": "CUST-001",
                "legal_name": "Acme Corp",
                "primary_email": MASKED_PII_TOKEN,
            }
        ],
    }
    with pytest.raises(OpenDVError) as masked_exc:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ontology,
                policy="balanced",
                prover=DuckDbProverAdapter(),
                source_rows_by_table=masked_rows,
                real_row_proving=True,
            )
        )
    assert masked_exc.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK


def test_mode1_inv05_hub_attributes_violation() -> None:
    """Populated DraftHubSpec.attributes surfaces INV-05 BLOCKER and REPAIR_DRAFT_IR."""
    ontology, profiles, draft, _rows = _build_multi_source_fixture()
    bad_hub = draft.hubs[0].model_copy(
        update={
            "attributes": [
                DraftField(
                    name="illegal_hub_attr",
                    logical_type="UTF8_STRING",
                    nullable=True,
                    is_pii=False,
                )
            ]
        }
    )
    bad_draft = draft.model_copy(update={"hubs": [bad_hub, *draft.hubs[1:]]})

    res = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=bad_draft,
            profiles=profiles,
            ontology=ontology,
            policy="balanced",
            prover=DuckDbProverAdapter(),
        )
    )

    assert res.status == "UNCERTIFIED_DRAFT"
    assert res.next_action == "REPAIR_DRAFT_IR"
    assert res.evolution_lock is None
    assert res.deterministic_energy >= 100
    assert res.remediation_hints
    inv05 = [d for d in res.verifier_report.v1_v6_diagnostics if d.rule_id == "INV-05"]
    assert inv05
    assert inv05[0].severity == "BLOCKER"
    assert inv05[0].entity_name == "hub_customer"
    assert any(h.rule_id == "INV-05" for h in res.remediation_hints)


def test_mode1_blocking_v6b_pii_mixed_waiver_and_preference_bypass() -> None:
    """Blocking V6b DV-AP-009 returns WAIVE_OR_ADJUST_PREFERENCES, then passes via waiver or pref."""
    ontology, profiles, draft, rows = _build_multi_source_fixture(include_mas_pii=True)
    prover = DuckDbProverAdapter()

    blocked = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ontology,
            policy="balanced",
            prover=prover,
            source_rows_by_table=rows,
            real_row_proving=True,
        )
    )
    assert blocked.status == "UNCERTIFIED_DRAFT"
    assert blocked.next_action == "WAIVE_OR_ADJUST_PREFERENCES"
    assert blocked.evolution_lock is None
    assert blocked.verifier_report.v1_v6_fail_count == 0
    assert blocked.verifier_report.v6b_fail_count >= 1
    ap009 = [
        d
        for d in blocked.verifier_report.v6b_diagnostics
        if d.rule_id == "DV-AP-009" and d.entity_name == "mas_customer_contact"
    ]
    assert ap009
    assert ap009[0].severity != "ADVISORY"

    waived = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ontology,
            policy="balanced",
            prover=prover,
            source_rows_by_table=rows,
            real_row_proving=True,
            persisted_waivers=[
                AntiPatternJustification(
                    waiver_id="WAIV-TEST-AP009",
                    rule_id="DV-AP-009",
                    entity_name="mas_customer_contact",
                    rationale="Tokenized multi-active contact channel kept together by Steward.",
                    actor="human",
                )
            ],
        )
    )
    assert waived.status == "CERTIFIED_FAST"
    assert waived.next_action == "READY_FOR_DOWNSTREAM"
    assert waived.deterministic_energy == 0
    assert waived.evolution_lock is not None

    pref_disabled = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ontology,
            policy="balanced",
            modeling_preferences=ModelingPreferences(disabled_v6b_blocks=["DV-AP-009"]),
            prover=prover,
            source_rows_by_table=rows,
            real_row_proving=True,
        )
    )
    assert pref_disabled.status == "CERTIFIED_FAST"
    assert pref_disabled.next_action == "READY_FOR_DOWNSTREAM"
    assert pref_disabled.deterministic_energy == 0
    assert pref_disabled.evolution_lock is not None
