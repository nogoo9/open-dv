"""Phase 2 DV2.0 Link participant_specs, Alg A.4, Hub canonical/Z4, SAL, ESAT, Bridge, MAS, & V6/V6b tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from opendv.models.diagnostics import (
    AntiPatternJustification,
    IRProverReport,
    RealDataRuleResult,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptTableBinding,
    GraphAOntology,
)
from opendv.models.policy import CegarPolicy
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
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
    PredicateBranch,
    PredicateLeaf,
)
from opendv.models.vault_ir_certified import (
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    LoadSemanticsSpec,
    PhysicalLayoutHints,
    PitBridgeSpec,
    SourceBindingSpec,
)
from opendv.verifier import (
    VerifierContext,
    apply_alg_a9a_schema_drift,
    lint_vault_antipatterns,
    promote_draft_to_certified,
    resolve_effective_v6b_blocking_rules,
    run_l1_codemods,
    run_v1_to_v6_gates,
    verify_v1_structure,
    verify_v2_hash_and_bkcc,
)
from opendv.verifier._l1_link_promote_helpers import build_link_binding_hash_recipe
from opendv.verifier._l1_rules_alg import (
    apply_alg_a3_esat_and_mas,
    apply_alg_a4_bkcc_and_sparse_fks,
)
from opendv.verifier._v1_bridge_rules import (
    validate_draft_bridge,
    validate_link_participant_bindings,
    validate_pit_bridges,
    validate_survivorship_and_z4_types,
)
from opendv.verifier.l1_codemods import compile_l1_options_from_preferences


def _triage_profiles(profiles: Sequence[SourceProfile]) -> list[ColumnTriageEntry]:
    return [
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name=col.col_name,
            disposition="MODELED",
            target_ref="modeled",
            rationale="",
        )
        for prof in profiles
        for tbl in prof.tables
        for col in tbl.columns
    ]


def _col(
    name: str,
    sql_type: str = "VARCHAR",
    *,
    nullable: bool = False,
    is_pk: bool = False,
    fk_ref: str | None = None,
    null_ratio: float = 0.0,
    distinct_count_est: int = 100,
    surrogate_score: float = 0.0,
) -> ColumnProfile:
    return ColumnProfile(
        col_name=name,
        sql_type=sql_type,
        nullable=nullable,
        is_pk=is_pk,
        fk_ref=fk_ref,
        null_ratio=null_ratio,
        distinct_count_est=distinct_count_est,
        surrogate_score=surrogate_score,
    )


def _std_sat_cols(parent_hk: str, *attrs: DraftField) -> list[IRField]:
    fields = [
        IRField(field_id=1, name=parent_hk, logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
        IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
        IRField(field_id=3, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
        IRField(field_id=4, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
    ]
    for idx, attr in enumerate(attrs, start=5):
        fields.append(
            IRField(
                field_id=idx,
                name=attr.name,
                logical_type=attr.logical_type,
                nullable=attr.nullable,
                is_pii=attr.is_pii,
                classification_tag=attr.classification_tag,
            )
        )
    return fields


def test_link_participant_specs_mixed_tiers_disambiguation_degenerate_and_predicates() -> None:
    """Multi-source per-Link-binding participant_specs, disambiguation, DV-STR-003, & predicates."""
    hub_sec = CertifiedHubSpec(
        hub_name="hub_security",
        key_tier="TIER_1_GLOBAL_STANDARD",
        business_keys=[
            BusinessKeyPart(
                table_id="src_a:sec_master",
                col_name="isin",
                source_id="src_a",
                key_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_value="GLOBAL",
            )
        ],
        source_bindings=[
            SourceBindingSpec(
                source_id="src_a",
                table_id="src_a:sec_master",
                col_mappings={"isin": "isin"},
                key_tier="TIER_1_GLOBAL_STANDARD",
                hash_recipe=HashRecipeSpec(
                    target_col="hk_security",
                    recipe_kind="HUB_HK",
                    bkcc_literal="GLOBAL",
                    bkcc_col=None,
                    source_columns=["isin"],
                    sort_alphabetically=False,
                    key_columns=["isin"],
                ),
            )
        ],
        hash_recipe=HashRecipeSpec(
            target_col="hk_security",
            recipe_kind="HUB_HK",
            bkcc_literal="GLOBAL",
            source_columns=["isin"],
            sort_alphabetically=False,
            key_columns=["isin"],
        ),
        physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_security"]),
    )

    unk_pred = PredicateLeaf(op="EQ", column="acct_code", literal="UNK")
    err_pred = PredicateBranch(
        op="OR",
        children=[
            PredicateLeaf(op="EQ", column="acct_code", literal="ERR"),
            PredicateLeaf(op="EQ", column="acct_code", literal="INVALID"),
        ],
    )
    hub_acct = CertifiedHubSpec(
        hub_name="hub_account",
        key_tier="TIER_2_ENTERPRISE_SOR",
        business_keys=[
            BusinessKeyPart(
                table_id="src_a:acct_main",
                col_name="acct_code",
                source_id="src_a",
                key_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_value="SOR_MAIN",
            ),
            BusinessKeyPart(
                table_id="src_a:acct_shadow",
                col_name="shadow_acct_id",
                source_id="src_a",
                key_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_value="SOR_SHADOW",
            ),
        ],
        source_bindings=[
            SourceBindingSpec(
                source_id="src_a",
                table_id="src_a:acct_main",
                col_mappings={"acct_code": "acct_code"},
                key_tier="TIER_2_ENTERPRISE_SOR",
                hash_recipe=HashRecipeSpec(
                    target_col="hk_account",
                    recipe_kind="HUB_HK",
                    bkcc_literal="SOR_MAIN",
                    source_columns=["acct_code"],
                    sort_alphabetically=False,
                    key_columns=["acct_code"],
                    unknown_predicate=unk_pred,
                    error_predicate=err_pred,
                ),
            ),
            SourceBindingSpec(
                source_id="src_a",
                table_id="src_a:acct_shadow",
                col_mappings={"shadow_acct_id": "shadow_acct_id"},
                key_tier="TIER_2_ENTERPRISE_SOR",
                hash_recipe=HashRecipeSpec(
                    target_col="hk_account",
                    recipe_kind="HUB_HK",
                    bkcc_literal="SOR_SHADOW",
                    source_columns=["shadow_acct_id"],
                    sort_alphabetically=False,
                    key_columns=["shadow_acct_id"],
                ),
            ),
        ],
        hash_recipe=HashRecipeSpec(
            target_col="hk_account",
            recipe_kind="HUB_HK",
            bkcc_literal="SOR_MAIN",
            source_columns=["acct_code"],
            sort_alphabetically=False,
            key_columns=["acct_code"],
        ),
        physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_account"]),
    )

    hub_broker = CertifiedHubSpec(
        hub_name="hub_broker",
        key_tier="TIER_3_LOCAL_SCOPED",
        business_keys=[
            BusinessKeyPart(
                table_id="src_a:broker",
                col_name="broker_code",
                source_id="src_a",
                key_tier="TIER_3_LOCAL_SCOPED",
                bkcc_value="SRC_A",
                bkcc_col="broker_ns",
            )
        ],
        source_bindings=[
            SourceBindingSpec(
                source_id="src_a",
                table_id="src_a:broker",
                col_mappings={"broker_code": "broker_code"},
                key_tier="TIER_3_LOCAL_SCOPED",
                hash_recipe=HashRecipeSpec(
                    target_col="hk_broker",
                    recipe_kind="HUB_HK",
                    bkcc_literal="SRC_A",
                    bkcc_col="broker_ns",
                    source_columns=["broker_code"],
                    sort_alphabetically=False,
                    key_columns=["broker_code"],
                ),
            )
        ],
        hash_recipe=HashRecipeSpec(
            target_col="hk_broker",
            recipe_kind="HUB_HK",
            bkcc_literal="SRC_A",
            bkcc_col="broker_ns",
            source_columns=["broker_code"],
            sort_alphabetically=False,
            key_columns=["broker_code"],
        ),
        physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_broker"]),
    )

    draft_lnk = DraftLinkSpec(
        link_name="lnk_trade",
        link_kind="STANDARD",
        participants=[
            LinkParticipantSpec(
                hub_name="hub_security",
                role_name="SECURITY",
                fk_col_name="hk_security",
                source_col_names=["pos_isin"],
                key_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_col="spurious_bkcc_col",  # must be suppressed for TIER_1
            ),
            LinkParticipantSpec(
                hub_name="hub_account",
                role_name="ACCOUNT",
                fk_col_name="hk_account",
                source_col_names=["holding_acct_code"],
                target_hub_binding_table_id="src_a:acct_main",
            ),
            LinkParticipantSpec(
                hub_name="hub_broker",
                role_name="BROKER",
                fk_col_name="hk_broker",
                source_col_names=["exec_broker_code"],
                key_tier="TIER_3_LOCAL_SCOPED",
                bkcc_col="broker_ns",
            ),
        ],
        degenerate_keys=["trade_seq_no"],
        source_bindings=[
            DraftSourceBinding(
                source_id="src_a",
                table_id="src_a:trades",
                col_mappings={
                    "hk_security": "hk_security",
                    "hk_account": "hk_account",
                    "hk_broker": "hk_broker",
                },
            ),
            DraftSourceBinding(
                source_id="src_b",
                table_id="src_b:fills",
                col_mappings={"holding_acct_code": "fill_acct_code"},
            ),
        ],
    )

    hub_map = {h.hub_name: h for h in (hub_sec, hub_acct, hub_broker)}
    tbl_a_cols = {"pos_isin", "holding_acct_code", "exec_broker_code", "broker_ns", "trade_seq_no"}
    tbl_b_cols = {"pos_isin", "fill_acct_code", "exec_broker_code", "broker_ns", "trade_seq_no"}
    recipe_a = build_link_binding_hash_recipe(
        draft_lnk,
        draft_lnk.source_bindings[0],
        hk_col="hk_trade",
        hub_cert_by_name=hub_map,
        col_idx={},
        tbl_cols=tbl_a_cols,
    )
    recipe_b = build_link_binding_hash_recipe(
        draft_lnk,
        draft_lnk.source_bindings[1],
        hk_col="hk_trade",
        hub_cert_by_name=hub_map,
        col_idx={},
        tbl_cols=tbl_b_cols,
    )

    assert len(recipe_a.participant_specs) == 4
    ps_sec, ps_acct, ps_brk, ps_deg = recipe_a.participant_specs
    assert ps_sec.key_tier == "TIER_1_GLOBAL_STANDARD"
    assert ps_sec.bkcc_literal == "GLOBAL"
    assert ps_sec.bkcc_col is None
    assert ps_acct.key_tier == "TIER_2_ENTERPRISE_SOR"
    assert ps_acct.target_hub_binding_table_id == "src_a:acct_main"
    assert ps_acct.bkcc_literal == "SOR_MAIN"
    assert ps_acct.unknown_predicate == PredicateLeaf(op="EQ", column="holding_acct_code", literal="UNK")
    assert isinstance(ps_acct.error_predicate, PredicateBranch)
    assert [c.column for c in ps_acct.error_predicate.children] == [
        "holding_acct_code",
        "holding_acct_code",
    ]
    assert ps_brk.key_tier == "TIER_3_LOCAL_SCOPED"
    assert ps_brk.bkcc_col == "broker_ns"
    assert ps_deg.is_degenerate is True
    assert ps_deg.hub_name == ""
    assert ps_deg.source_columns == ["trade_seq_no"]
    assert ps_deg.is_nullable is None

    # Second source binding remaps column via col_mappings and still remaps Hub predicate
    ps_b_acct = recipe_b.participant_specs[1]
    assert ps_b_acct.source_columns == ["fill_acct_code"]
    assert ps_b_acct.unknown_predicate == PredicateLeaf(
        op="EQ", column="fill_acct_code", literal="UNK"
    )

    # Validate disambiguated CertifiedVaultIR passes validate_link_participant_bindings & V2
    prof_a = SourceProfile(
        source_id="src_a",
        schema_fingerprint="fp_a",
        table_count=1,
        tables=[
            TableProfile(
                table_id="src_a:trades",
                table_name="trades",
                row_count_est=10,
                columns=[_col(c) for c in sorted(tbl_a_cols)],
            )
        ],
    )
    cert_lnk = CertifiedLinkSpec(
        link_name="lnk_trade",
        link_kind="STANDARD",
        participants=draft_lnk.participants,
        degenerate_keys=["trade_seq_no"],
        source_bindings=[
            SourceBindingSpec(
                source_id="src_a",
                table_id="src_a:trades",
                col_mappings={},
                hash_recipe=recipe_a,
            )
        ],
        columns=[
            IRField(field_id=1, name="hk_trade", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=2, name="hk_security", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=3, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
            IRField(field_id=4, name="hk_broker", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
        ],
        hash_recipe=recipe_a,
        physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade"]),
    )
    cert_ir = CertifiedVaultIR(
        project_id="p_link",
        ontology_version="1.0",
        wave_number=1,
        hubs=[hub_sec, hub_acct, hub_broker],
        links=[cert_lnk],
    )
    assert validate_link_participant_bindings(cert_ir, [prof_a]) == []
    assert [d for d in verify_v2_hash_and_bkcc(cert_ir) if d.entity_name.startswith("lnk_trade")] == []

    # Unresolved target_hub_binding_table_id on ambiguous Hub (2 tables under src_a) -> DV-STR-003 BLOCKER
    ambig_part = draft_lnk.participants[1].model_copy(update={"target_hub_binding_table_id": None})
    ambig_ir = cert_ir.model_copy(
        update={
            "links": [
                cert_lnk.model_copy(
                    update={"participants": [draft_lnk.participants[0], ambig_part, draft_lnk.participants[2]]}
                )
            ]
        }
    )
    diags_ambig = validate_link_participant_bindings(ambig_ir, [prof_a])
    assert any(d.rule_id == "DV-STR-003" and d.severity == "BLOCKER" for d in diags_ambig)

    # Non-existent target_hub_binding_table_id -> DV-STR-003 BLOCKER
    bad_tgt = draft_lnk.participants[1].model_copy(
        update={"target_hub_binding_table_id": "src_a:does_not_exist"}
    )
    bad_ir = cert_ir.model_copy(
        update={
            "links": [
                cert_lnk.model_copy(
                    update={"participants": [draft_lnk.participants[0], bad_tgt, draft_lnk.participants[2]]}
                )
            ]
        }
    )
    diags_bad = validate_link_participant_bindings(bad_ir, [prof_a])
    assert any(d.rule_id == "DV-STR-003" and d.severity == "BLOCKER" for d in diags_bad)


def test_alg_a4_partial_null_link_preservation_tristate_and_optional_inapplicable() -> None:
    """BRK-02b: Alg A.4 preserves optional 3-way STANDARD Link, tri-state is_nullable, & optional HK."""
    prof = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_brk02b",
        table_count=4,
        tables=[
            TableProfile(
                table_id="erp:orders",
                table_name="orders",
                row_count_est=100,
                columns=[
                    _col("order_code", is_pk=True, null_ratio=0.40),
                    _col("cust_id", nullable=False, null_ratio=0.0),
                    _col("promo_code", nullable=True, null_ratio=0.35),
                ],
            ),
            TableProfile(
                table_id="erp:customers",
                table_name="customers",
                row_count_est=50,
                columns=[_col("cust_id", is_pk=True)],
            ),
            TableProfile(
                table_id="erp:promos",
                table_name="promos",
                row_count_est=10,
                columns=[_col("promo_code", is_pk=True)],
            ),
        ],
    )
    hubs = [
        DraftHubSpec(
            hub_name="hub_order",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="erp:orders",
                    col_name="order_code",
                    source_id="erp",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="ERP",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:orders", col_mappings={"order_code": "order_code"})],
        ),
        DraftHubSpec(
            hub_name="hub_customer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="erp:customers",
                    col_name="cust_id",
                    source_id="erp",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="ERP",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:customers", col_mappings={"cust_id": "cust_id"})],
        ),
        DraftHubSpec(
            hub_name="hub_promo",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="erp:promos",
                    col_name="promo_code",
                    source_id="erp",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="ERP",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:promos", col_mappings={"promo_code": "promo_code"})],
        ),
    ]
    lnk = DraftLinkSpec(
        link_name="lnk_order_cust_promo",
        link_kind="STANDARD",
        driving_hub="hub_order",
        participants=[
            # Explicit False overrides null_ratio=0.40
            LinkParticipantSpec(
                hub_name="hub_order",
                role_name="ORDER",
                fk_col_name="hk_order",
                source_col_names=["order_code"],
                is_nullable=False,
            ),
            # Explicit True overrides null_ratio=0.0
            LinkParticipantSpec(
                hub_name="hub_customer",
                role_name="CUSTOMER",
                fk_col_name="hk_customer",
                source_col_names=["cust_id"],
                is_nullable=True,
            ),
            # None infers from profile null_ratio=0.35 -> True
            LinkParticipantSpec(
                hub_name="hub_promo",
                role_name="PROMO",
                fk_col_name="hk_promo",
                source_col_names=["promo_code"],
                is_nullable=None,
            ),
        ],
        source_bindings=[
            DraftSourceBinding(
                source_id="erp",
                table_id="erp:orders",
                col_mappings={
                    "hk_order": "hk_order",
                    "hk_customer": "hk_customer",
                    "hk_promo": "hk_promo",
                },
            )
        ],
    )
    draft = DraftVaultIR(
        project_id="p_brk02b",
        ontology_version="1.0",
        wave_number=1,
        hubs=hubs,
        links=[lnk],
        column_triage=_triage_profiles([prof]),
    )

    a4_draft, link_filters = apply_alg_a4_bkcc_and_sparse_fks(draft, [prof])
    assert len(a4_draft.links) == 1
    assert len(a4_draft.links[0].participants) == 3
    assert link_filters == {}

    _, cert, _ = run_l1_codemods(draft, profiles=[prof])
    assert len(cert.links) == 1
    cert_lnk = cert.links[0]
    assert len(cert_lnk.participants) == 3
    assert cert_lnk.load_semantics is not None
    assert cert_lnk.load_semantics.filter_ast is None

    assert cert_lnk.hash_recipe is not None
    null_flags = [ps.is_nullable for ps in cert_lnk.hash_recipe.participant_specs]
    assert null_flags == [False, True, True]
    assert cert_lnk.hash_recipe.is_optional_inapplicable is False

    # Synthesized staging HUB_HK recipes on erp:orders propagate is_optional_inapplicable=True
    stg_orders = next(s for s in cert.staging_views if s.source_table_id == "erp:orders")
    by_tgt = {r.target_col: r for r in stg_orders.hash_recipes}
    assert by_tgt["hk_customer"].is_optional_inapplicable is True
    assert by_tgt["hk_promo"].is_optional_inapplicable is True

    # When ALL non-degenerate participants are nullable, LINK_HK sets is_optional_inapplicable=True
    all_opt_lnk = lnk.model_copy(
        update={
            "participants": [
                p.model_copy(update={"is_nullable": True}) for p in lnk.participants
            ]
        }
    )
    _, cert_opt, _ = run_l1_codemods(
        draft.model_copy(update={"links": [all_opt_lnk]}), profiles=[prof]
    )
    assert cert_opt.links[0].hash_recipe is not None
    assert cert_opt.links[0].hash_recipe.is_optional_inapplicable is True


def test_canonical_hub_bk_separation_survivorship_coalesce_and_dv_str_017() -> None:
    """Canonical Hub key separation, Z4 COALESCE widening/TRIM, ext suppression, & DV-STR-017."""
    prof = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_canon",
        table_count=2,
        tables=[
            TableProfile(
                table_id="erp:erp_cust",
                table_name="erp_cust",
                row_count_est=100,
                columns=[
                    _col("cust_num", is_pk=True),
                    _col("full_name"),
                    _col("credit_limit", sql_type="INTEGER"),
                    _col("tier_score", sql_type="INTEGER"),
                ],
            ),
            TableProfile(
                table_id="crm:crm_cust",
                table_name="crm_cust",
                row_count_est=100,
                columns=[
                    _col("crm_cust_code", is_pk=True),
                    _col("full_name"),
                    _col("credit_limit", sql_type="BIGINT"),
                    _col("tier_score", sql_type="INTEGER"),
                ],
            ),
        ],
    )
    hub_draft = DraftHubSpec(
        hub_name="hub_customer",
        key_tier="TIER_2_ENTERPRISE_SOR",
        survivorship_priority=["erp:erp:erp_cust", "crm:crm:crm_cust"],
        business_keys=[
            BusinessKeyPart(
                table_id="erp:erp_cust",
                col_name="cust_num",
                source_id="erp",
                key_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_value="ERP",
                canonical_col_name="customer_bk",
            ),
            BusinessKeyPart(
                table_id="crm:crm_cust",
                col_name="crm_cust_code",
                source_id="crm",
                key_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_value="CRM",
                canonical_col_name="customer_bk",
            ),
        ],
        source_bindings=[
            DraftSourceBinding(source_id="erp", table_id="erp:erp_cust", col_mappings={"cust_num": "cust_num"}),
            DraftSourceBinding(source_id="crm", table_id="crm:crm_cust", col_mappings={"crm_cust_code": "crm_cust_code"}),
        ],
    )
    f_name = DraftField(name="full_name", logical_type="UTF8_STRING", nullable=True, is_pii=False)
    f_limit_erp = DraftField(name="credit_limit", logical_type="INT32", nullable=True, is_pii=False)
    f_limit_crm = DraftField(name="credit_limit", logical_type="INT64", nullable=True, is_pii=False)
    f_score_base = DraftField(name="tier_score", logical_type="INT32", nullable=True, is_pii=False)
    f_score_ext = DraftField(name="tier_score", logical_type="UTF8_STRING", nullable=True, is_pii=False)

    sat_erp = DraftSatelliteSpec(
        sat_name="sat_cust_erp",
        sat_kind="STANDARD",
        parent_name="hub_customer",
        source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:erp_cust", col_mappings={})],
        attributes=[f_name, f_limit_erp, f_score_base],
    )
    sat_erp_ext = DraftSatelliteSpec(
        sat_name="sat_cust_erp_ext_v2",
        sat_kind="EXT",
        parent_name="hub_customer",
        extends_sat_name="sat_cust_erp",
        companion_epoch=2,
        source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:erp_cust", col_mappings={})],
        attributes=[f_score_ext],
    )
    sat_crm = DraftSatelliteSpec(
        sat_name="sat_cust_crm",
        sat_kind="STANDARD",
        parent_name="hub_customer",
        source_bindings=[DraftSourceBinding(source_id="crm", table_id="crm:crm_cust", col_mappings={})],
        attributes=[f_name, f_limit_crm],
    )

    draft = DraftVaultIR(
        project_id="p_canon",
        ontology_version="1.0",
        wave_number=2,
        hubs=[hub_draft],
        satellites=[sat_erp, sat_erp_ext, sat_crm],
        column_triage=_triage_profiles([prof]),
    )
    cert = promote_draft_to_certified(draft, profiles=[prof])
    hub_cert = cert.hubs[0]

    # Canonical Hub key separation: Hub physical + hub_recipe use customer_bk; bindings keep raw cols
    assert [c.name for c in hub_cert.columns if c.classification_tag == "BUSINESS_KEY"] == ["customer_bk"]
    assert hub_cert.hash_recipe is not None
    assert hub_cert.hash_recipe.source_columns == ["customer_bk"]
    assert hub_cert.source_bindings[0].hash_recipe is not None
    assert hub_cert.source_bindings[0].hash_recipe.source_columns == ["cust_num"]
    assert hub_cert.source_bindings[1].hash_recipe is not None
    assert hub_cert.source_bindings[1].hash_recipe.source_columns == ["crm_cust_code"]

    stg_erp = next(s for s in cert.staging_views if s.source_table_id == "erp:erp_cust")
    stg_crm = next(s for s in cert.staging_views if s.source_table_id == "crm:crm_cust")
    assert stg_erp.col_lineage_mappings["customer_bk"] == "cust_num"
    assert stg_crm.col_lineage_mappings["customer_bk"] == "crm_cust_code"

    mart = next(m for m in cert.information_marts if m.view_name == "information_mart.vw_customer_current")
    # UTF8_STRING wrapped in NULLIF(TRIM(...), '') in survivorship_priority order
    assert mart.select_exprs["full_name"] == (
        "COALESCE(NULLIF(TRIM(sat_cust_erp.full_name), ''), "
        "NULLIF(TRIM(sat_cust_crm.full_name), ''))"
    )
    # Numeric INT32 widened via CAST(... AS BIGINT), NOT wrapped in NULLIF(TRIM(...))
    assert mart.select_exprs["credit_limit"] == (
        "COALESCE(CAST(sat_cust_erp.credit_limit AS BIGINT), sat_cust_crm.credit_limit)"
    )
    # Incompatible extension suppresses base sat_cust_erp.tier_score and selects only ext_v2
    assert mart.select_exprs["tier_score"] == "sat_cust_erp_ext_v2.tier_score"
    assert validate_survivorship_and_z4_types(cert) == []

    # Distinct (source_id, table_id) pairs with incompatible Z4 types (even same source_id!) -> DV-STR-017
    incompat_ir = CertifiedVaultIR(
        project_id="p_incompat",
        ontology_version="1.0",
        wave_number=1,
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_item",
                key_tier="TIER_2_ENTERPRISE_SOR",
                survivorship_priority=["erp:erp:t1", "erp:erp:t2", "unknown_src"],
                source_bindings=[
                    SourceBindingSpec(source_id="erp", table_id="erp:t1", col_mappings={}),
                    SourceBindingSpec(source_id="erp", table_id="erp:t2", col_mappings={}),
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_item_t1",
                sat_kind="STANDARD",
                parent_name="hub_item",
                source_id="erp",
                source_table_id="erp:t1",
                attributes=[DraftField(name="weight", logical_type="INT32", nullable=True, is_pii=False)],
                columns=_std_sat_cols("hk_item"),
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_item_t2",
                sat_kind="STANDARD",
                parent_name="hub_item",
                source_id="erp",
                source_table_id="erp:t2",
                attributes=[DraftField(name="weight", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
                columns=_std_sat_cols("hk_item"),
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_item"]),
            ),
        ],
    )
    diags_17 = validate_survivorship_and_z4_types(incompat_ir)
    assert any(d.rule_id == "DV-STR-017" and d.severity == "BLOCKER" and "not owned" in d.message for d in diags_17)
    assert any(d.rule_id == "DV-STR-017" and d.severity == "BLOCKER" and "incompatible" in d.message for d in diags_17)


def test_inv05_rejects_hub_attributes_on_draft_and_certified() -> None:
    """INV-05 rejects non-empty attributes on both DraftHubSpec and CertifiedHubSpec."""
    bad_field = DraftField(name="cust_name", logical_type="UTF8_STRING", nullable=True, is_pii=False)

    with pytest.raises(ValidationError, match="INV-05"):
        DraftHubSpec(
            hub_name="hub_customer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            attributes=[bad_field],
        )

    with pytest.raises(ValidationError, match="INV-05"):
        CertifiedHubSpec(
            hub_name="hub_customer",
            key_tier="TIER_2_ENTERPRISE_SOR",
            attributes=[bad_field],
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_customer"]),
        )

    # Also verified via V1 symbolic gate if constructed via model_construct
    constructed_hub = CertifiedHubSpec.model_construct(
        hub_name="hub_customer",
        zone_id="Z2",
        business_keys=[],
        key_tier="TIER_2_ENTERPRISE_SOR",
        source_bindings=[],
        attributes=[bad_field],
        is_planned_concept=True,
        survivorship_priority=[],
        columns=[],
        hash_recipe=None,
        load_semantics=None,
        physical_hints=None,
    )
    ir = CertifiedVaultIR.model_construct(
        project_id="p_inv05",
        ontology_version="1.0",
        wave_number=1,
        bronze_tables=[],
        staging_views=[],
        hubs=[constructed_hub],
        links=[],
        satellites=[],
        reference_tables=[],
        pit_bridges=[],
        information_marts=[],
        column_triage=[],
        justifications=[],
    )
    v1_diags = verify_v1_structure(ir)
    assert any(d.rule_id == "INV-05" and d.severity == "BLOCKER" for d in v1_diags)


def test_sal_linked_companion_hub_exemption_from_anti_silo_violation() -> None:
    """sal_*-linked companion Hubs are exempt from ANTI_SILO_VIOLATION and get ADVISORY DV-AP-001."""
    prof = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_sal",
        table_count=2,
        tables=[
            TableProfile(
                table_id="erp:customers",
                table_name="customers",
                row_count_est=10,
                columns=[_col("erp_cust_id", is_pk=True), _col("name")],
            ),
            TableProfile(
                table_id="crm:customers",
                table_name="customers",
                row_count_est=10,
                columns=[_col("crm_cust_id", is_pk=True), _col("name")],
            ),
        ],
    )
    ont = GraphAOntology(
        ontology_version="1.0",
        domain_namespace="test",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:test:Customer",
                concept_name="Customer",
                description="Customer CBC",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                table_bindings=[
                    ConceptTableBinding(source_id="erp", table_id="erp:customers", col_name="erp_cust_id"),
                    ConceptTableBinding(source_id="crm", table_id="crm:customers", col_name="crm_cust_id"),
                ],
            )
        ],
    )
    draft = DraftVaultIR(
        project_id="p_sal",
        ontology_version="1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_erp_customer",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="erp:customers",
                        col_name="erp_cust_id",
                        source_id="erp",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="ERP",
                    )
                ],
                source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:customers", col_mappings={})],
            ),
            DraftHubSpec(
                hub_name="hub_crm_customer",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="crm:customers",
                        col_name="crm_cust_id",
                        source_id="crm",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[DraftSourceBinding(source_id="crm", table_id="crm:customers", col_mappings={})],
            ),
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_erp_customer",
                sat_kind="STANDARD",
                parent_name="hub_erp_customer",
                source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:customers", col_mappings={})],
                attributes=[DraftField(name="name", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
            DraftSatelliteSpec(
                sat_name="sat_crm_customer",
                sat_kind="STANDARD",
                parent_name="hub_crm_customer",
                source_bindings=[DraftSourceBinding(source_id="crm", table_id="crm:customers", col_mappings={})],
                attributes=[DraftField(name="name", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            ),
        ],
        column_triage=_triage_profiles([prof]),
    )
    _, cert_no_sal, _ = run_l1_codemods(draft, profiles=[prof])
    diags_no_sal = verify_v1_structure(cert_no_sal, profiles=[prof], ontology=ont)
    assert any(d.rule_id == "ANTI_SILO_VIOLATION" for d in diags_no_sal)

    sal_link = DraftLinkSpec(
        link_name="sal_customer",
        link_kind="SAME_AS",
        participants=[
            LinkParticipantSpec(
                hub_name="hub_erp_customer",
                role_name="MASTER",
                fk_col_name="hk_master",
                source_col_names=["erp_cust_id"],
            ),
            LinkParticipantSpec(
                hub_name="hub_crm_customer",
                role_name="DUPLICATE",
                fk_col_name="hk_duplicate",
                source_col_names=["crm_cust_id"],
            ),
        ],
        source_bindings=[
            DraftSourceBinding(
                source_id="erp",
                table_id="erp:customers",
                col_mappings={"hk_master": "hk_erp_customer", "hk_duplicate": "hk_crm_customer"},
            )
        ],
    )
    _, cert_with_sal, _ = run_l1_codemods(
        draft.model_copy(update={"links": [sal_link]}),
        profiles=[prof],
    )
    diags_with_sal = verify_v1_structure(cert_with_sal, profiles=[prof], ontology=ont)
    assert not any(d.rule_id == "ANTI_SILO_VIOLATION" for d in diags_with_sal)

    prefs = ModelingPreferences(multi_source_hub_strategy="companion_hub_with_sal")
    v6b_diags = lint_vault_antipatterns(
        cert_with_sal,
        [prof],
        ont,
        policy="balanced",
        modeling_preferences=prefs,
    )
    ap001 = [d for d in v6b_diags if d.rule_id == "DV-AP-001" and "source prefix" in d.message]
    assert ap001 and all(d.severity == "ADVISORY" for d in ap001)


def test_z2_append_only_esat_and_z3_esat_intervals_mart() -> None:
    """Z2 append-only raw esat_* avoids effective_from/to injection, enforces DV-STR-009/012, & builds Z3 ESAT_INTERVALS."""
    prof = SourceProfile(
        source_id="hr",
        schema_fingerprint="fp_esat",
        table_count=3,
        tables=[
            TableProfile(
                table_id="hr:emp",
                table_name="emp",
                row_count_est=10,
                columns=[_col("emp_id", is_pk=True)],
            ),
            TableProfile(
                table_id="hr:dept",
                table_name="dept",
                row_count_est=5,
                columns=[_col("dept_id", is_pk=True)],
            ),
            TableProfile(
                table_id="hr:emp_dept",
                table_name="emp_dept",
                row_count_est=20,
                columns=[
                    _col("emp_id", is_pk=True, fk_ref="emp.emp_id"),
                    _col("dept_id", fk_ref="dept.dept_id"),
                    _col("effective_from", sql_type="TIMESTAMP"),
                    _col("effective_to", sql_type="TIMESTAMP", nullable=True),
                ],
            ),
        ],
    )
    hubs = [
        DraftHubSpec(
            hub_name="hub_emp",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="hr:emp",
                    col_name="emp_id",
                    source_id="hr",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="HR",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="hr", table_id="hr:emp", col_mappings={})],
        ),
        DraftHubSpec(
            hub_name="hub_dept",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="hr:dept",
                    col_name="dept_id",
                    source_id="hr",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    bkcc_value="HR",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="hr", table_id="hr:dept", col_mappings={})],
        ),
    ]
    lnk = DraftLinkSpec(
        link_name="lnk_emp_dept",
        link_kind="STANDARD",
        driving_hub="hub_emp",
        participants=[
            LinkParticipantSpec(hub_name="hub_emp", role_name="EMP", fk_col_name="hk_emp", source_col_names=["emp_id"]),
            LinkParticipantSpec(hub_name="hub_dept", role_name="DEPT", fk_col_name="hk_dept", source_col_names=["dept_id"]),
        ],
        source_bindings=[DraftSourceBinding(source_id="hr", table_id="hr:emp_dept", col_mappings={})],
    )
    draft = DraftVaultIR(
        project_id="p_esat",
        ontology_version="1.0",
        wave_number=1,
        hubs=hubs,
        links=[lnk],
        column_triage=_triage_profiles([prof]),
    )

    _, cert, _ = run_l1_codemods(
        draft,
        profiles=[prof],
        fk_turnover_by_table={"hr:emp_dept": {"dept_id": 0.25}},
    )
    esat = next(s for s in cert.satellites if s.sat_kind == "ESAT")
    assert esat.zone_id == "Z2"
    assert esat.load_mode == "APPEND_WITH_EFFECTIVITY"
    assert esat.hash_recipe is None
    assert len(esat.source_bindings) == 1
    esat_col_names = {c.name for c in esat.columns}
    assert {"hk_emp_dept", "load_dts", "rec_src", "effective_from"}.issubset(esat_col_names)
    assert "effective_to" not in esat_col_names
    assert "is_current" not in esat_col_names
    assert "load_end_dts" not in esat_col_names

    intervals = next(m for m in cert.information_marts if m.view_kind == "ESAT_INTERVALS")
    assert intervals.zone_id == "Z3"
    assert intervals.root_entity == esat.sat_name
    assert {"effective_from", "effective_to", "is_current"}.issubset({c.name for c in intervals.columns})
    assert "LEAD(" in intervals.select_exprs["effective_to"]

    # When source table has no applied_dts_col, raw ESAT has neither effective_from nor effective_to
    prof_no_eff = prof.model_copy(
        update={
            "tables": [
                prof.tables[0],
                prof.tables[1],
                TableProfile(
                    table_id="hr:emp_dept",
                    table_name="emp_dept",
                    row_count_est=20,
                    columns=[
                        _col("emp_id", is_pk=True, fk_ref="emp.emp_id"),
                        _col("dept_id", fk_ref="dept.dept_id"),
                    ],
                ),
            ]
        }
    )
    _, cert_no_eff, _ = run_l1_codemods(
        draft.model_copy(update={"column_triage": _triage_profiles([prof_no_eff])}),
        profiles=[prof_no_eff],
        fk_turnover_by_table={"hr:emp_dept": {"dept_id": 0.25}},
    )
    esat_no_eff = next(s for s in cert_no_eff.satellites if s.sat_kind == "ESAT")
    assert "effective_from" not in {c.name for c in esat_no_eff.columns}
    assert "effective_to" not in {c.name for c in esat_no_eff.columns}

    # DV-STR-009: ESAT on TRANSACTIONAL Link is banned; DV-STR-012: multi-binding ESAT rejected
    bad_esat_ir = CertifiedVaultIR(
        project_id="p_bad_esat",
        ontology_version="1.0",
        wave_number=1,
        hubs=cert.hubs,
        links=[
            cert.links[0].model_copy(update={"link_kind": "TRANSACTIONAL"}),
        ],
        satellites=[
            esat.model_copy(
                update={
                    "source_bindings": [
                        SourceBindingSpec(source_id="hr", table_id="hr:emp_dept", col_mappings={}),
                        SourceBindingSpec(source_id="hr2", table_id="hr2:emp_dept", col_mappings={}),
                    ]
                }
            )
        ],
    )
    diags = verify_v1_structure(bad_esat_ir)
    assert any(d.rule_id == "DV-STR-009" and d.severity == "ERROR" for d in diags)
    assert any(d.rule_id == "DV-STR-012" and d.severity == "ERROR" for d in diags)


def test_bridge_lowering_cycle_rejection_and_mas_ext_cdk_inheritance() -> None:
    """Multi-hop DraftBridgeSpec -> Z3 BRIDGE PitBridgeSpec, DV-STR-016 cycle, & MAS _ext_vN CDK inheritance."""
    prof = SourceProfile(
        source_id="oms",
        schema_fingerprint="fp_brg_mas",
        table_count=3,
        tables=[
            TableProfile(
                table_id="oms:orders",
                table_name="orders",
                row_count_est=10,
                columns=[_col("order_id", is_pk=True), _col("item_id", fk_ref="items.item_id")],
            ),
            TableProfile(
                table_id="oms:items",
                table_name="items",
                row_count_est=10,
                columns=[
                    _col("item_id", is_pk=True),
                    _col("vendor_id", fk_ref="vendors.vendor_id"),
                    _col("line_no", sql_type="INTEGER"),
                    _col("qty", sql_type="INTEGER"),
                    _col("discount_amt", sql_type="DECIMAL(10,2)", nullable=True),
                ],
            ),
            TableProfile(
                table_id="oms:vendors",
                table_name="vendors",
                row_count_est=5,
                columns=[_col("vendor_id", is_pk=True)],
            ),
        ],
    )
    hubs = [
        DraftHubSpec(
            hub_name="hub_order",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="oms:orders", col_name="order_id", source_id="oms",
                    key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="OMS",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:orders", col_mappings={})],
        ),
        DraftHubSpec(
            hub_name="hub_item",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="oms:items", col_name="item_id", source_id="oms",
                    key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="OMS",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:items", col_mappings={})],
        ),
        DraftHubSpec(
            hub_name="hub_vendor",
            key_tier="TIER_2_ENTERPRISE_SOR",
            business_keys=[
                BusinessKeyPart(
                    table_id="oms:vendors", col_name="vendor_id", source_id="oms",
                    key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="OMS",
                )
            ],
            source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:vendors", col_mappings={})],
        ),
    ]
    links = [
        DraftLinkSpec(
            link_name="lnk_order_item",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(hub_name="hub_order", role_name="ORDER", fk_col_name="hk_order", source_col_names=["order_id"]),
                LinkParticipantSpec(hub_name="hub_item", role_name="ITEM", fk_col_name="hk_item", source_col_names=["item_id"]),
            ],
            source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:orders", col_mappings={})],
        ),
        DraftLinkSpec(
            link_name="lnk_item_vendor",
            link_kind="STANDARD",
            participants=[
                LinkParticipantSpec(hub_name="hub_item", role_name="ITEM", fk_col_name="hk_item", source_col_names=["item_id"]),
                LinkParticipantSpec(hub_name="hub_vendor", role_name="VENDOR", fk_col_name="hk_vendor", source_col_names=["vendor_id"]),
            ],
            source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:items", col_mappings={})],
        ),
    ]
    valid_bridge = DraftBridgeSpec(
        bridge_name="brg_order_vendor",
        root_hub="hub_order",
        hops=[
            BridgeHopSpec(link_name="lnk_order_item"),
            BridgeHopSpec(link_name="lnk_item_vendor"),
        ],
    )
    mas_base = DraftSatelliteSpec(
        sat_name="sat_item_lines_mas",
        sat_kind="MAS",
        parent_name="hub_item",
        child_dependent_keys=["line_no"],
        source_bindings=[DraftSourceBinding(source_id="oms", table_id="oms:items", col_mappings={})],
        attributes=[
            DraftField(name="line_no", logical_type="INT32", nullable=False, is_pii=False),
            DraftField(name="qty", logical_type="INT32", nullable=False, is_pii=False),
        ],
    )
    draft_w1 = DraftVaultIR(
        project_id="p_brg",
        ontology_version="1.0",
        wave_number=1,
        hubs=hubs,
        links=links,
        satellites=[mas_base],
        bridges=[valid_bridge],
        column_triage=_triage_profiles([prof]),
    )
    _, cert_w1, _ = run_l1_codemods(draft_w1, profiles=[prof])
    brg_cert = next(pb for pb in cert_w1.pit_bridges if pb.object_kind == "BRIDGE")
    assert brg_cert.object_name == "brg_order_vendor"
    assert brg_cert.zone_id == "Z3"
    assert brg_cert.grain_cols == ["hk_order", "hk_order_item", "hk_item_vendor"]
    assert brg_cert.traversed_hk_cols == ["hk_item", "hk_vendor"]
    assert validate_pit_bridges(cert_w1) == []

    # Cyclic BridgeHopSpec -> DV-STR-016 BLOCKER
    cyclic_brg = DraftBridgeSpec(
        bridge_name="brg_cyclic",
        root_hub="hub_order",
        hops=[
            BridgeHopSpec(link_name="lnk_order_item"),
            BridgeHopSpec(link_name="lnk_order_item"),
        ],
    )
    cyc_diags = validate_draft_bridge(
        cyclic_brg,
        {h.hub_name: h for h in hubs},
        {lnk.link_name: lnk for lnk in links},
    )
    assert any(d.rule_id == "DV-STR-016" and d.severity == "BLOCKER" for d in cyc_diags)

    # MAS _ext_vN inherits child_dependent_keys in apply_alg_a9a_schema_drift
    locks_w1 = EvolutionLockSpec(
        wave_number=1,
        locked_hub_bks={"hub_item": ["item_id"]},
        locked_sat_hash_diffs={"sat_item_lines_mas": ["line_no", "qty"]},
        sat_epochs={"sat_item_lines_mas": 1},
    )
    mas_w2 = mas_base.model_copy(
        update={
            "attributes": [
                DraftField(name="line_no", logical_type="INT32", nullable=False, is_pii=False),
                DraftField(name="qty", logical_type="INT32", nullable=False, is_pii=False),
                DraftField(
                    name="discount_amt",
                    logical_type=DecimalType(precision=10, scale=2),
                    nullable=True,
                    is_pii=False,
                ),
            ]
        }
    )
    draft_w2 = draft_w1.model_copy(update={"wave_number": 2, "satellites": [mas_w2]})
    drifted, _ = apply_alg_a9a_schema_drift(draft_w2, locks_w1, prior_ir=cert_w1, profiles=[prof])
    ext_mas = next(s for s in drifted.satellites if s.sat_name == "sat_item_lines_mas_ext_v2")
    assert ext_mas.sat_kind == "MAS"
    assert ext_mas.extends_sat_name == "sat_item_lines_mas"
    assert ext_mas.companion_epoch == 2
    assert ext_mas.child_dependent_keys == ["line_no"]
    ext_attrs = {a.name: a for a in ext_mas.attributes}
    assert ext_attrs["line_no"].nullable is False
    assert "discount_amt" in ext_attrs


def test_modeling_preferences_energy_v6_proof_wiring_and_legacy_id_ap003() -> None:
    """ModelingPreferences -> L1CodemodOptions, disabled_v6b_blocks, deterministic_energy, V6 proofs, & legacy id AP-003."""
    prefs = ModelingPreferences(
        multi_source_hub_strategy="companion_hub_with_sal",
        low_cardinality_strategy="keep_in_satellite",
        link_cardinality_strategy="allow_transactional_links",
        esat_on_mutable_links="off",
        surrogate_key_strategy="allow_with_bkcc_note",
        disabled_v6b_blocks=["DV-AP-001"],
    )
    justs = [
        AntiPatternJustification(
            waiver_id="W-007",
            rule_id="DV-AP-007",
            entity_name="lnk_evt",
            rationale="Immutable click event log.",
            actor="human",
        ),
        AntiPatternJustification(
            waiver_id="W-008",
            rule_id="DV-AP-008",
            entity_name="lnk_rel",
            rationale="Static relation without effectivity.",
            actor="human",
        ),
    ]
    opts = compile_l1_options_from_preferences(prefs, justs)
    assert opts.max_enum_cardinality == 0
    assert opts.allow_transactional_links is True
    assert opts.esat_mode == "off"
    assert opts.multi_source_hub_strategy == "companion_hub_with_sal"
    assert opts.waived_ap007_entities == frozenset({"lnk_evt"})
    assert opts.waived_ap008_entities == frozenset({"lnk_rel"})

    # disabled_v6b_blocks validation
    with pytest.raises(OpenDVError) as exc_unk:
        resolve_effective_v6b_blocking_rules(
            "balanced", ModelingPreferences(disabled_v6b_blocks=["DV-AP-999"])
        )
    assert exc_unk.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_strict:
        resolve_effective_v6b_blocking_rules(
            "strict", ModelingPreferences(disabled_v6b_blocks=["DV-AP-001"])
        )
    assert exc_strict.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Build a minimal certified IR with source-prefixed hub (DV-AP-001) + dummy V6 prover
    prof_clean = SourceProfile(
        source_id="erp",
        schema_fingerprint="fp_v6",
        table_count=1,
        tables=[
            TableProfile(
                table_id="erp:widget",
                table_name="widget",
                row_count_est=10,
                columns=[_col("widget_code", is_pk=True), _col("label")],
            )
        ],
    )
    _, cert_prefixed, _ = run_l1_codemods(
        DraftVaultIR(
            project_id="p_v6",
            ontology_version="1.0",
            wave_number=1,
            hubs=[
                DraftHubSpec(
                    hub_name="hub_erp_widget",
                    key_tier="TIER_2_ENTERPRISE_SOR",
                    business_keys=[
                        BusinessKeyPart(
                            table_id="erp:widget",
                            col_name="widget_code",
                            source_id="erp",
                            key_tier="TIER_2_ENTERPRISE_SOR",
                            bkcc_value="ERP",
                        )
                    ],
                    source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:widget", col_mappings={})],
                )
            ],
            satellites=[
                DraftSatelliteSpec(
                    sat_name="sat_erp_widget",
                    sat_kind="STANDARD",
                    parent_name="hub_erp_widget",
                    source_bindings=[DraftSourceBinding(source_id="erp", table_id="erp:widget", col_mappings={})],
                    attributes=[DraftField(name="label", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
                )
            ],
            column_triage=_triage_profiles([prof_clean]),
        ),
        profiles=[prof_clean],
    )

    class _StubProver:
        def prove(
            self,
            certified_ir: CertifiedVaultIR,
            profiles: Sequence[SourceProfile],
            *,
            policy: CegarPolicy = "balanced",
            source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
            table_sample_flags: Mapping[str, bool] | None = None,
            real_row_proving: bool = True,
        ) -> IRProverReport:
            _ = (certified_ir, profiles, policy, source_rows_by_table, table_sample_flags, real_row_proving)
            return IRProverReport(
                dyn_results={"DV-DYN-001": True},
                ast_violations=[],
                empirical_smells=[],
                counterexamples=[],
                schema_proof_results={"DV-DYN-001": True},
                real_data_proof_results={
                    "DV-DYN-001": RealDataRuleResult(
                        status="PASS_FULL",
                        detail="Verified against full table.",
                        table_row_counts={"erp:widget": 10},
                    )
                },
            )

    rep_blocking = run_v1_to_v6_gates(
        cert_prefixed,
        VerifierContext(profiles=[prof_clean], policy="balanced"),
        prover=_StubProver(),
    )
    assert rep_blocking.passed is False
    assert rep_blocking.deterministic_energy == 10
    assert rep_blocking.schema_proof == {"DV-DYN-001": True}
    assert rep_blocking.real_data_proof["DV-DYN-001"].status == "PASS_FULL"

    rep_disabled = run_v1_to_v6_gates(
        cert_prefixed,
        VerifierContext(
            profiles=[prof_clean],
            policy="balanced",
            modeling_preferences=ModelingPreferences(disabled_v6b_blocks=["DV-AP-001"]),
        ),
        prover=_StubProver(),
    )
    assert rep_disabled.passed is True
    assert rep_disabled.deterministic_energy == 0

    # Legacy id-only TIER_3_LOCAL_SCOPED DV-AP-003 downgrade ONLY when best_non_surrogate_bk_uniqueness_ratio < 0.99
    def _legacy_id_ir(ratio: float | None, tier: str = "TIER_3_LOCAL_SCOPED", bkcc: str = "LEGACY_APP") -> tuple[CertifiedVaultIR, SourceProfile]:
        p = SourceProfile(
            source_id="leg",
            schema_fingerprint="fp_leg",
            table_count=1,
            tables=[
                TableProfile(
                    table_id="leg:users",
                    table_name="users",
                    row_count_est=100,
                    columns=[_col("id", sql_type="INTEGER", is_pk=True), _col("email")],
                    best_non_surrogate_bk_candidate_col="email",
                    best_non_surrogate_bk_uniqueness_ratio=ratio,
                )
            ],
        )
        ir = CertifiedVaultIR(
            project_id="p_leg",
            ontology_version="1.0",
            wave_number=1,
            hubs=[
                CertifiedHubSpec(
                    hub_name="hub_user",
                    key_tier=tier,  # type: ignore[arg-type]
                    business_keys=[
                        BusinessKeyPart(
                            table_id="leg:users",
                            col_name="id",
                            source_id="leg",
                            key_tier=tier,  # type: ignore[arg-type]
                            bkcc_value=bkcc,
                        )
                    ],
                    source_bindings=[SourceBindingSpec(source_id="leg", table_id="leg:users", col_mappings={})],
                    physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_user"]),
                )
            ],
        )
        return ir, p

    ir_low, p_low = _legacy_id_ir(0.85)
    ap003_low = [
        d for d in lint_vault_antipatterns(ir_low, [p_low], policy="balanced") if d.rule_id == "DV-AP-003"
    ]
    assert len(ap003_low) == 1 and ap003_low[0].severity == "ADVISORY"

    # Ratio >= 0.99 must NOT downgrade
    ir_high, p_high = _legacy_id_ir(0.99)
    ap003_high = [
        d for d in lint_vault_antipatterns(ir_high, [p_high], policy="balanced") if d.rule_id == "DV-AP-003"
    ]
    assert len(ap003_high) == 1 and ap003_high[0].severity == "ERROR"

    # policy="strict" must NOT downgrade even when ratio < 0.99
    ap003_strict = [
        d for d in lint_vault_antipatterns(ir_low, [p_low], policy="strict") if d.rule_id == "DV-AP-003"
    ]
    assert len(ap003_strict) == 1 and ap003_strict[0].severity == "ERROR"
