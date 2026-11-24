"""Live-provider M2C E2E tests (UAT-V25-LIVE-01 agy + UAT-V25-LIVE-02 claude-cli)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

import pytest

from opendv.agents import resolve_cli_binary
from opendv.models.ontology import ConceptAttribute, ConceptTableBinding
from opendv.models.ontology_deltas import AddConcept
from opendv.models.project import HarnessProvider, WorkspaceSettings
from opendv.models.wire_dtos_discovery import (
    OntologyCommitReq,
    OntologyConstructReq,
    ProjectInitReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import (
    SqlQueryReq,
    StorageCompileReq,
    StorageTargetReq,
    VaultBuildReq,
)
from opendv.service import OpenDVService

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_AM = REPO_ROOT / "data" / "public_am"


async def _run_live_e2e(
    tmp_path: Path,
    *,
    provider: Literal["agy", "claude-cli"],
    project_id: str,
) -> None:
    ws = tmp_path / f"ws_{provider}"
    am_dir = ws / "public_am"
    am_dir.mkdir(parents=True)
    for fname in ("am_sec_treasury_ibor.sqlite", "am_stewardship_domain_notes.md"):
        shutil.copy2(PUBLIC_AM / fname, am_dir / fname)

    svc = OpenDVService(ws, state_dir=tmp_path / f"state_{provider}", harness=None)
    typed_provider: HarnessProvider = provider
    settings = WorkspaceSettings(harness_provider=typed_provider, timeout_seconds=180)
    svc.update_settings(settings)

    proj = svc.init_project(ProjectInitReq(project_id=project_id, domain_namespace="asset_mgmt"))
    initial_rev = proj.active_ontology_rev_id
    assert initial_rev is not None

    sqlite_sum = svc.attach_source(
        project_id,
        SourceAttachReq(
            kind="sqlite",
            uri_or_filename="public_am/am_sec_treasury_ibor.sqlite",
            selected_tables=["institutional_filers"],
        ),
    )
    svc.attach_source(
        project_id,
        SourceAttachReq(kind="markdown", uri_or_filename="public_am/am_stewardship_domain_notes.md"),
    )

    # Step 4 live coordinator turn (must NOT autonomously commit Graph A)
    run4 = await svc.start_ontology_construction(
        project_id,
        OntologyConstructReq(hint="Focus on institutional_filers only; keep output concise."),
    )
    assert run4.status in ("COMPLETED", "HOLD_CHECKPOINTED")
    assert svc.get_project(project_id).active_ontology_rev_id == initial_rev

    # Step 5 deterministic Steward commit grounded to institutional_filers
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
                ),
                ConceptAttribute(
                    name="filer_name",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    nullable=False,
                    is_business_key=False,
                    is_pii=False,
                ),
            ],
            table_bindings=[
                ConceptTableBinding(
                    source_id=sqlite_sum.source_id,
                    table_id=f"{sqlite_sum.source_id}:institutional_filers",
                    col_name="filer_cik",
                )
            ],
        )
    ]
    svc.commit_ontology(
        project_id,
        OntologyCommitReq(
            operations=commit_ops,
            base_revision_id=initial_rev,
            rationale="Steward commits grounded InstitutionalFiler ontology for live test.",
        ),
    )

    # Step 6 live CEGAR synthesis + DuckDbProverAdapter
    run6 = await svc.build_vault(project_id, VaultBuildReq(policy="fast"))
    assert run6.status == "COMPLETED"
    vault_ir = svc.get_vault_ir(project_id)
    assert len(vault_ir.hubs) >= 1
    verify_resp = svc.verify_vault(project_id)
    assert verify_resp.passed
    bundle = svc.compile_target(
        project_id, StorageCompileReq(target_id="duckdb_local", verify_runtime=True)
    )
    assert bundle.artifacts
    mig = svc.migrate_target(project_id, StorageTargetReq(target_id="duckdb_local"))
    assert mig.verified and mig.rows_loaded >= 1
    hub_name = vault_ir.hubs[0].hub_name
    res = svc.query_target(project_id, SqlQueryReq(sql=f"SELECT COUNT(*) AS cnt FROM raw_vault.{hub_name}"))
    assert int(res.rows[0][0] or 0) >= 1


@pytest.mark.live_provider
@pytest.mark.asyncio
async def test_uat_v25_live_01_agy_public_am(tmp_path: Path) -> None:
    resolve_cli_binary("agy")
    await _run_live_e2e(tmp_path, provider="agy", project_id="live-agy-am")


@pytest.mark.live_provider
@pytest.mark.asyncio
async def test_uat_v25_live_02_claude_public_am(tmp_path: Path) -> None:
    resolve_cli_binary("claude-cli")
    await _run_live_e2e(tmp_path, provider="claude-cli", project_id="live-claude-am")
