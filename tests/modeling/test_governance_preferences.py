"""Phase 4 tests for Tier-1 ModelingPreferences, V6b governance, and guide trust."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opendv.agents.pack_loader import (
    compute_workspace_agents_sha256 as pack_compute_workspace_agents_sha256,
    load_modeling_guide,
    verify_workspace_override_trust,
)
from opendv.modeling import (
    DVModelingRequest,
    ModelingPreferences,
    ModelingTurnResult,
    VerifyDraftRequest,
    model_vault,
    verify_draft_vault,
)
from opendv.modeling.prompts import resolve_modeling_guide
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.project import HarnessProvider, TrustedWorkspaceSpec
from opendv.models.project import (
    compute_workspace_agents_sha256 as models_compute_workspace_agents_sha256,
)
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    LinkParticipantSpec,
)
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.storage import DuckDbProverAdapter
from opendv.verifier.l1_codemods import run_l1_codemods
from tests.agents.test_cegar_v7_v8_horizon import _triage_all
from tests.helpers import DeterministicMockHarness


class _ModelingMockHarness:
    """Adapt DeterministicMockHarness to Layer-2c ModelingHarnessPort."""

    def __init__(self, inner: DeterministicMockHarness) -> None:
        self._inner = inner

    @property
    def provider_name(self) -> HarnessProvider:
        return self._inner.provider_name

    @property
    def requests(self) -> list[Any]:
        return self._inner.requests

    async def run_turn(
        self,
        request: Any,
        *,
        event_sink: Any = None,
        scout_dispatcher: Any = None,
    ) -> ModelingTurnResult:
        res = await self._inner.run_turn(
            request,
            event_sink=event_sink,
            scout_dispatcher=scout_dispatcher,
        )
        return ModelingTurnResult.model_validate(res.model_dump())


def _col(
    col_name: str,
    sql_type: str = "VARCHAR",
    *,
    is_pk: bool = False,
    pk_ord: int | None = None,
    fk_ref: str | None = None,
    surrogate_score: float = 0.0,
    distinct_count_est: int = 10,
    format_shape: Any = "MIXED",
    sample_values: list[str | None] | None = None,
) -> ColumnProfile:
    return ColumnProfile(
        col_name=col_name,
        sql_type=sql_type,
        nullable=False,
        is_pk=is_pk,
        pk_ord=pk_ord,
        fk_ref=fk_ref,
        null_ratio=0.0,
        surrogate_score=surrogate_score,
        distinct_count_est=distinct_count_est,
        format_shape=format_shape,
        sample_values=sample_values or [],
    )


def _companion_sal_fixture() -> tuple[list[SourceProfile], GraphAOntology, DraftVaultIR]:
    crm_tid = "src_crm:public.crm_acct"
    erp_tid = "src_erp:public.erp_acct"
    profiles = [
        SourceProfile(
            source_id="src_crm",
            schema_fingerprint="fp_crm_sal",
            table_count=1,
            tables=[
                TableProfile(
                    table_id=crm_tid,
                    table_name="crm_acct",
                    row_count_est=50,
                    columns=[
                        _col("crm_acct_id", is_pk=True, pk_ord=1, distinct_count_est=50),
                        _col(
                            "erp_acct_ref",
                            fk_ref="erp_acct.erp_acct_id",
                            distinct_count_est=50,
                            sample_values=["E-1", "E-2", "E-3"],
                        ),
                        _col("acct_name", distinct_count_est=50),
                    ],
                )
            ],
        ),
        SourceProfile(
            source_id="src_erp",
            schema_fingerprint="fp_erp_sal",
            table_count=1,
            tables=[
                TableProfile(
                    table_id=erp_tid,
                    table_name="erp_acct",
                    row_count_est=50,
                    columns=[
                        _col(
                            "erp_acct_id",
                            is_pk=True,
                            pk_ord=1,
                            distinct_count_est=50,
                            sample_values=["E-1", "E-2", "E-3"],
                        ),
                        _col("legal_name", distinct_count_est=50),
                    ],
                )
            ],
        ),
    ]
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:CrmAccount",
                concept_name="CrmAccount",
                description="CRM Account concept",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="CRM",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:ErpAccount",
                concept_name="ErpAccount",
                description="ERP Account concept",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="ERP",
            ),
        ],
    )
    draft = DraftVaultIR(
        project_id="proj_sal",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_crm_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=crm_tid,
                        col_name="crm_acct_id",
                        source_id="src_crm",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=crm_tid,
                        col_mappings={"crm_acct_id": "crm_acct_id"},
                    )
                ],
            ),
            DraftHubSpec(
                hub_name="hub_erp_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=erp_tid,
                        col_name="erp_acct_id",
                        source_id="src_erp",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ERP",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_erp",
                        table_id=erp_tid,
                        col_mappings={"erp_acct_id": "erp_acct_id"},
                    )
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="sal_account",
                link_kind="SAME_AS",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_crm_account",
                        role_name="MASTER",
                        fk_col_name="hk_crm_account",
                        source_col_names=["crm_acct_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_erp_account",
                        role_name="DUPLICATE",
                        fk_col_name="hk_erp_account",
                        source_col_names=["erp_acct_ref"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=crm_tid,
                        col_mappings={"crm_acct_id": "crm_acct_id", "erp_acct_ref": "erp_acct_ref"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_crm_acct",
                sat_kind="STANDARD",
                parent_name="hub_crm_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=crm_tid,
                        col_mappings={"acct_name": "acct_name"},
                    )
                ],
                attributes=[
                    DraftField(name="acct_name", logical_type="UTF8_STRING", nullable=False, is_pii=False)
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_erp_acct",
                sat_kind="STANDARD",
                parent_name="hub_erp_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_erp",
                        table_id=erp_tid,
                        col_mappings={"legal_name": "legal_name"},
                    )
                ],
                attributes=[
                    DraftField(name="legal_name", logical_type="UTF8_STRING", nullable=False, is_pii=False)
                ],
            ),
        ],
        column_triage=_triage_all(profiles),
    )
    return profiles, ont, draft


@pytest.mark.asyncio
async def test_pref_multi_source_hub_strategy_companion_sal_vs_unified() -> None:
    """companion_hub_with_sal downgrades DV-AP-001 on SAL companion hubs and skips BK alignment."""
    profiles, ont, draft = _companion_sal_fixture()
    prover = DuckDbProverAdapter()

    # Default unified_canonical_when_same_arity blocks source-prefixed companion hubs (DV-AP-001)
    res_unified = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="balanced",
            modeling_preferences=ModelingPreferences(
                multi_source_hub_strategy="unified_canonical_when_same_arity"
            ),
            prover=prover,
        )
    )
    assert res_unified.status == "UNCERTIFIED_DRAFT"
    assert res_unified.next_action == "WAIVE_OR_ADJUST_PREFERENCES"
    assert res_unified.verifier_report.v6b_fail_count == 2
    assert {d.rule_id for d in res_unified.verifier_report.v6b_diagnostics if d.severity == "ERROR"} == {
        "DV-AP-001"
    }

    # companion_hub_with_sal downgrades DV-AP-001 prefix findings on SAL hubs to ADVISORY
    prefs_sal = ModelingPreferences(multi_source_hub_strategy="companion_hub_with_sal")
    res_sal = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="balanced",
            modeling_preferences=prefs_sal,
            prover=prover,
        )
    )
    assert res_sal.status == "CERTIFIED_FAST"
    assert res_sal.next_action == "READY_FOR_DOWNSTREAM"
    assert res_sal.verifier_report.v6b_fail_count == 0
    assert res_sal.verifier_report.advisory_count >= 2
    sal_links = [lnk for lnk in res_sal.certified_ir.links if lnk.link_kind == "SAME_AS"]
    assert len(sal_links) == 1
    assert sal_links[0].zone_id == "Z3"

    # L1 canonical BK alignment: unified aligns same-arity multi-source BKs; companion_hub_with_sal does not
    crm_tid = "src_crm:public.crm_acct"
    erp_tid = "src_erp:public.erp_acct"
    merged_hub_draft = DraftVaultIR(
        project_id="proj_align",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=crm_tid,
                        col_name="crm_acct_id",
                        source_id="src_crm",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    ),
                    BusinessKeyPart(
                        table_id=erp_tid,
                        col_name="erp_acct_id",
                        source_id="src_erp",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ERP",
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_crm", table_id=crm_tid, col_mappings={"crm_acct_id": "crm_acct_id"}),
                    DraftSourceBinding(source_id="src_erp", table_id=erp_tid, col_mappings={"erp_acct_id": "erp_acct_id"}),
                ],
            )
        ],
        column_triage=_triage_all(profiles),
    )
    l1_uni, _ = run_l1_codemods(
        merged_hub_draft,
        profiles=profiles,
        modeling_preferences=ModelingPreferences(
            multi_source_hub_strategy="unified_canonical_when_same_arity"
        ),
    )[:2]
    l1_sal, _ = run_l1_codemods(
        merged_hub_draft,
        profiles=profiles,
        modeling_preferences=prefs_sal,
    )[:2]
    assert l1_uni.hubs[0].business_keys[1].canonical_col_name == "crm_acct_id"
    assert l1_sal.hubs[0].business_keys[1].canonical_col_name is None

    # Mode-2 model_vault parity with companion_hub_with_sal
    m2_res = await model_vault(
        DVModelingRequest(
            run_id="run-pref-sal",
            project_id="proj_sal",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=prefs_sal,
            prover=prover,
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [draft],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=95)],
                        "dv-horizon-critic": [HorizonCriticVerdict(score=91, classification="REASONABLY_STABLE")],
                    }
                )
            ),
        )
    )
    assert m2_res.status == "CERTIFIED"


def test_pref_low_cardinality_strategy_auto_ref_vs_keep_in_sat() -> None:
    """auto_ref_table promotes low-card Hub to Z0 ref_*; keep_in_satellite skips demotion & exempts DV-AP-004."""
    tbl_id = "src_ref:public.acct"
    profiles = [
        SourceProfile(
            source_id="src_ref",
            schema_fingerprint="fp_ref",
            table_count=1,
            tables=[
                TableProfile(
                    table_id=tbl_id,
                    table_name="acct",
                    row_count_est=100,
                    columns=[
                        _col("acct_id", is_pk=True, pk_ord=1, distinct_count_est=100),
                        _col(
                            "status_code",
                            distinct_count_est=4,
                            format_shape="CODE_UPPER",
                            sample_values=["OPEN", "CLOSED"],
                        ),
                    ],
                )
            ],
        )
    ]
    draft = DraftVaultIR(
        project_id="proj_ref",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_acct",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="acct_id",
                        source_id="src_ref",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="CORE",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_ref", table_id=tbl_id, col_mappings={"acct_id": "acct_id"})
                ],
            ),
            DraftHubSpec(
                hub_name="hub_status_code",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="status_code",
                        source_id="src_ref",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CORE",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_ref", table_id=tbl_id, col_mappings={"status_code": "status_code"})
                ],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_acct_status",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_acct",
                        role_name="ACCT",
                        fk_col_name="hk_acct",
                        source_col_names=["acct_id"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_status_code",
                        role_name="STATUS",
                        fk_col_name="hk_status_code",
                        source_col_names=["status_code"],
                    ),
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_ref",
                        table_id=tbl_id,
                        col_mappings={"acct_id": "acct_id", "status_code": "status_code"},
                    )
                ],
            )
        ],
        column_triage=_triage_all(profiles),
    )
    prover = DuckDbProverAdapter()

    res_auto = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=ModelingPreferences(low_cardinality_strategy="auto_ref_table"),
            prover=prover,
        )
    )
    assert res_auto.status == "CERTIFIED_FAST"
    assert [r.ref_name for r in res_auto.certified_ir.reference_tables] == ["ref_status_code"]
    assert "hub_status_code" not in {h.hub_name for h in res_auto.certified_ir.hubs}

    res_keep = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=ModelingPreferences(low_cardinality_strategy="keep_in_satellite"),
            prover=prover,
        )
    )
    assert res_keep.status == "CERTIFIED_FAST"
    assert res_keep.certified_ir.reference_tables == []
    assert "hub_status_code" in {h.hub_name for h in res_keep.certified_ir.hubs}
    dv004 = [d for d in res_keep.verifier_report.v6b_diagnostics if d.rule_id == "DV-AP-004"]
    assert dv004 and all(d.severity == "ADVISORY" for d in dv004)


def test_pref_esat_on_mutable_links_always_vs_off() -> None:
    """always_on_driving_hub emits Z2 esat_* + Z3 intervals view; off suppresses ESAT & exempts DV-AP-008."""
    acct_tid = "src_am:public.acct"
    mgr_tid = "src_am:public.mgr"
    rel_tid = "src_am:public.acct_mgr"
    profiles = [
        SourceProfile(
            source_id="src_am",
            schema_fingerprint="fp_esat",
            table_count=3,
            tables=[
                TableProfile(
                    table_id=acct_tid,
                    table_name="acct",
                    row_count_est=20,
                    columns=[
                        _col("acct_id", is_pk=True, pk_ord=1, distinct_count_est=20),
                    ],
                ),
                TableProfile(
                    table_id=mgr_tid,
                    table_name="mgr",
                    row_count_est=5,
                    columns=[
                        _col("mgr_id", is_pk=True, pk_ord=1, distinct_count_est=5),
                    ],
                ),
                TableProfile(
                    table_id=rel_tid,
                    table_name="acct_mgr",
                    row_count_est=30,
                    columns=[
                        _col("acct_id", is_pk=True, pk_ord=1, fk_ref="acct.acct_id", distinct_count_est=20),
                        _col("mgr_id", fk_ref="mgr.mgr_id", distinct_count_est=5),
                        _col("effective_from", sql_type="TIMESTAMP", format_shape="ISO_TIMESTAMP", distinct_count_est=30),
                    ],
                ),
            ],
        )
    ]
    draft = DraftVaultIR(
        project_id="proj_esat",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_acct",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id=acct_tid, col_name="acct_id", source_id="src_am", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="AM")],
                source_bindings=[DraftSourceBinding(source_id="src_am", table_id=acct_tid, col_mappings={"acct_id": "acct_id"})],
            ),
            DraftHubSpec(
                hub_name="hub_mgr",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[BusinessKeyPart(table_id=mgr_tid, col_name="mgr_id", source_id="src_am", key_tier="TIER_2_ENTERPRISE_SOR", bkcc_value="AM")],
                source_bindings=[DraftSourceBinding(source_id="src_am", table_id=mgr_tid, col_mappings={"mgr_id": "mgr_id"})],
            ),
        ],
        links=[
            DraftLinkSpec(
                link_name="lnk_acct_mgr",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_acct", role_name="ACCT", fk_col_name="hk_acct", source_col_names=["acct_id"]),
                    LinkParticipantSpec(hub_name="hub_mgr", role_name="MGR", fk_col_name="hk_mgr", source_col_names=["mgr_id"]),
                ],
                source_bindings=[
                    DraftSourceBinding(source_id="src_am", table_id=rel_tid, col_mappings={"acct_id": "acct_id", "mgr_id": "mgr_id"})
                ],
            )
        ],
        column_triage=_triage_all(profiles, system_cols={"effective_from"}),
    )
    prover = DuckDbProverAdapter()

    res_on = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=ModelingPreferences(esat_on_mutable_links="always_on_driving_hub"),
            prover=prover,
        )
    )
    assert res_on.status == "CERTIFIED_FAST"
    esats = [s for s in res_on.certified_ir.satellites if s.sat_kind == "ESAT"]
    assert len(esats) == 1
    assert esats[0].zone_id == "Z2"
    assert esats[0].sat_name.startswith("esat_")
    intervals = [
        m for m in res_on.certified_ir.information_marts if m.view_kind == "ESAT_INTERVALS"
    ]
    assert len(intervals) == 1
    assert intervals[0].zone_id == "Z3"

    res_off = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=ModelingPreferences(esat_on_mutable_links="off"),
            prover=prover,
        )
    )
    assert res_off.status == "CERTIFIED_FAST"
    assert not any(s.sat_kind == "ESAT" for s in res_off.certified_ir.satellites)
    assert not any(m.view_kind == "ESAT_INTERVALS" for m in res_off.certified_ir.information_marts)
    dv008 = [d for d in res_off.verifier_report.v6b_diagnostics if d.rule_id == "DV-AP-008"]
    assert dv008 and all(d.severity == "ADVISORY" for d in dv008)


def _surrogate_fixture() -> tuple[list[SourceProfile], GraphAOntology, DraftVaultIR]:
    tbl_id = "src_crm:public.client"
    profiles = [
        SourceProfile(
            source_id="src_crm",
            schema_fingerprint="fp_surr",
            table_count=1,
            tables=[
                TableProfile(
                    table_id=tbl_id,
                    table_name="client",
                    row_count_est=100,
                    columns=[
                        _col(
                            "surrogate_id",
                            sql_type="BIGINT",
                            is_pk=True,
                            pk_ord=1,
                            surrogate_score=0.95,
                            distinct_count_est=100,
                        ),
                        _col("client_code", distinct_count_est=100),
                    ],
                    best_non_surrogate_bk_candidate_col="client_code",
                    best_non_surrogate_bk_uniqueness_ratio=1.0,
                )
            ],
        )
    ]
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:Client",
                concept_name="Client",
                description="Client entity",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="CRM",
            )
        ],
    )
    draft = DraftVaultIR(
        project_id="proj_surr",
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_client",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tbl_id,
                        col_name="surrogate_id",
                        source_id="src_crm",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="CRM",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=tbl_id,
                        col_mappings={"surrogate_id": "surrogate_id"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_client",
                sat_kind="STANDARD",
                parent_name="hub_client",
                source_bindings=[
                    DraftSourceBinding(
                        source_id="src_crm",
                        table_id=tbl_id,
                        col_mappings={"client_code": "client_code"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="client_code",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    )
                ],
            )
        ],
        column_triage=_triage_all(profiles),
    )
    return profiles, ont, draft


def test_pref_surrogate_key_strategy_reject_vs_allow() -> None:
    """reject_when_natural_exists blocks DV-AP-003; allow_with_bkcc_note downgrades under balanced."""
    profiles, ont, draft = _surrogate_fixture()
    prover = DuckDbProverAdapter()

    res_reject = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="balanced",
            modeling_preferences=ModelingPreferences(
                surrogate_key_strategy="reject_when_natural_exists"
            ),
            prover=prover,
        )
    )
    assert res_reject.status == "UNCERTIFIED_DRAFT"
    assert res_reject.next_action == "WAIVE_OR_ADJUST_PREFERENCES"
    assert res_reject.verifier_report.v6b_fail_count == 1
    assert res_reject.verifier_report.v6b_diagnostics[0].rule_id == "DV-AP-003"

    res_allow = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="balanced",
            modeling_preferences=ModelingPreferences(
                surrogate_key_strategy="allow_with_bkcc_note"
            ),
            prover=prover,
        )
    )
    assert res_allow.status == "CERTIFIED_FAST"
    assert res_allow.next_action == "READY_FOR_DOWNSTREAM"
    assert res_allow.verifier_report.v6b_fail_count == 0
    assert res_allow.verifier_report.v6b_diagnostics[0].severity == "ADVISORY"

    # Under strict, allow_preference_exemptions is False so DV-AP-003 still blocks
    res_strict = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="strict",
            modeling_preferences=ModelingPreferences(
                surrogate_key_strategy="allow_with_bkcc_note"
            ),
            prover=prover,
        )
    )
    assert res_strict.status == "UNCERTIFIED_DRAFT"
    assert res_strict.verifier_report.v6b_fail_count == 1


@pytest.mark.asyncio
async def test_pref_disabled_v6b_blocks_balanced_vs_strict_and_unknown() -> None:
    """disabled_v6b_blocks works under balanced; strict or unknown rule ID raises SYNTAX_INVALID_ARGUMENT."""
    profiles, ont, draft = _surrogate_fixture()
    prover = DuckDbProverAdapter()

    prefs_dis = ModelingPreferences(disabled_v6b_blocks=["DV-AP-003"])
    res_bal = verify_draft_vault(
        VerifyDraftRequest(
            draft_ir=draft,
            profiles=profiles,
            ontology=ont,
            policy="balanced",
            modeling_preferences=prefs_dis,
            prover=prover,
        )
    )
    assert res_bal.status == "CERTIFIED_FAST"
    assert res_bal.verifier_report.v6b_fail_count == 0

    m2_bal = await model_vault(
        DVModelingRequest(
            run_id="run-dis-bal",
            project_id="proj_surr",
            ontology=ont,
            profiles=profiles,
            policy="balanced",
            modeling_preferences=prefs_dis,
            prover=prover,
            harness=_ModelingMockHarness(
                DeterministicMockHarness(
                    scripts_by_agent={
                        "ship-vault": [draft],
                        "dv-semantic-critic": [SemanticCriticVerdict(score=94)],
                        "dv-horizon-critic": [HorizonCriticVerdict(score=90, classification="REASONABLY_STABLE")],
                    }
                )
            ),
        )
    )
    assert m2_bal.status == "CERTIFIED"

    # strict policy forbids disabled_v6b_blocks
    with pytest.raises(OpenDVError) as exc_strict_v:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ont,
                policy="strict",
                modeling_preferences=prefs_dis,
                prover=prover,
            )
        )
    assert exc_strict_v.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_strict_m2:
        await model_vault(
            DVModelingRequest(
                run_id="run-dis-strict",
                project_id="proj_surr",
                ontology=ont,
                profiles=profiles,
                policy="strict",
                modeling_preferences=prefs_dis,
                prover=prover,
                harness=_ModelingMockHarness(DeterministicMockHarness()),
            )
        )
    assert exc_strict_m2.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # unknown rule ID raises SYNTAX_INVALID_ARGUMENT
    prefs_unk = ModelingPreferences(disabled_v6b_blocks=["DV-AP-999"])
    with pytest.raises(OpenDVError) as exc_unk_v:
        verify_draft_vault(
            VerifyDraftRequest(
                draft_ir=draft,
                profiles=profiles,
                ontology=ont,
                policy="balanced",
                modeling_preferences=prefs_unk,
                prover=prover,
            )
        )
    assert exc_unk_v.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_unk_m2:
        await model_vault(
            DVModelingRequest(
                run_id="run-dis-unk",
                project_id="proj_surr",
                ontology=ont,
                profiles=profiles,
                policy="balanced",
                modeling_preferences=prefs_unk,
                prover=prover,
                harness=_ModelingMockHarness(DeterministicMockHarness()),
            )
        )
    assert exc_unk_m2.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_load_modeling_guide_trust_token_limit_and_sha256_parity(tmp_path: Path) -> None:
    """Trusted workspace override loads <=2,500 tokens, untrusted/oversize fail closed, sha256 matches."""
    agents_dir = tmp_path / ".opendv" / "agents"
    agents_dir.mkdir(parents=True)
    guide_path = agents_dir / "dv2-modeling-guide.md"
    custom_guide = "# Custom Trusted Guide\n\nFollow V1-V6 and ModelingPreferences strictly.\n"
    guide_path.write_text(custom_guide, encoding="utf-8")

    # Parity between pack_loader.compute_workspace_agents_sha256 and models.project.compute_workspace_agents_sha256
    digest_pack = pack_compute_workspace_agents_sha256(tmp_path)
    digest_models = models_compute_workspace_agents_sha256(tmp_path)
    assert digest_pack is not None
    assert digest_pack == digest_models
    assert len(digest_pack) == 64

    trusted_spec = TrustedWorkspaceSpec(
        workspace_root=str(tmp_path),
        agent_pack_sha256=digest_pack,
        trusted_at="2026-11-26T00:00:00Z",
    )
    assert verify_workspace_override_trust(tmp_path, [trusted_spec]) == digest_pack

    loaded_text, guide_sha = load_modeling_guide(
        workspace_root=tmp_path,
        trusted_workspaces=[trusted_spec],
    )
    assert "Custom Trusted Guide" in loaded_text
    assert len(guide_sha) == 64

    # Untrusted workspace override fails closed with UNTRUSTED_WORKSPACE_OVERRIDE
    with pytest.raises(OpenDVError) as exc_untrusted:
        load_modeling_guide(workspace_root=tmp_path, trusted_workspaces=[])
    assert exc_untrusted.value.error_code == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE

    # Over-2,500-token guide fails closed even when trusted
    oversize_guide = " ".join(f"tok{i}" for i in range(2600))
    guide_path.write_text(oversize_guide, encoding="utf-8")
    oversize_digest = pack_compute_workspace_agents_sha256(tmp_path)
    assert oversize_digest is not None
    oversize_spec = TrustedWorkspaceSpec(
        workspace_root=str(tmp_path),
        agent_pack_sha256=oversize_digest,
        trusted_at="2026-11-26T00:00:00Z",
    )
    with pytest.raises(OpenDVError) as exc_tok:
        load_modeling_guide(workspace_root=tmp_path, trusted_workspaces=[oversize_spec])
    assert exc_tok.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as exc_prompt_tok:
        resolve_modeling_guide(guide_markdown=oversize_guide)
    assert exc_prompt_tok.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
