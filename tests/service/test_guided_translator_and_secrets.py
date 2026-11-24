"""M2C tests for GuidedUxTranslator jargon guard, Databricks secret recovery, and bundle conflicts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opendv.models.diagnostics import HorizonOptionSpec, HorizonQuestion
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    GraphAOntology,
)
from opendv.models.ontology_deltas import AddConcept
from opendv.models.project import (
    HorizonAssumptionSpec,
    OntologyProposalSpec,
    ProjectSettingsSnapshot,
    RevisionMetadata,
)
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
)
from opendv.models.wire_dtos_discovery import (
    OntologyCommitReq,
    ProjectInitReq,
    SourceAttachReq,
    SourceInspectReq,
)
from opendv.models.wire_dtos_vault import (
    HorizonCriticVerdict,
    SemanticCriticVerdict,
    StorageTargetReq,
    VaultBuildReq,
)
from opendv.service import OpenDVService, ProjectStateManager
from opendv.service._state_ddl import migrate_per_project_primary_keys, open_wal_connection
from tests.helpers import (
    DeterministicMockHarness,
    FailingKeyringBackend,
    InMemoryKeyringBackend,
    MockFaultSpec,
    MockTurnScript,
)


def _create_tiny_sqlite(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE filers (filer_cik TEXT PRIMARY KEY, filer_name TEXT NOT NULL)")
        conn.execute("INSERT INTO filers VALUES ('0001', 'Alpha Capital')")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_guided_ux_strips_jargon_even_under_injection(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    src_db = ws / "tiny.sqlite"
    _create_tiny_sqlite(src_db)

    def _selector(req):
        if req.agent_id == "ship-vault":
            return MockTurnScript(
                structured_output=DraftVaultIR(
                    project_id="guided-proj",
                    ontology_version="0.1.0",
                    wave_number=1,
                    hubs=[
                        DraftHubSpec(
                            hub_name="hub_filer",
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            business_keys=[
                                BusinessKeyPart(
                                    table_id=f"{req.profiles[0].source_id}:filers"
                                    if hasattr(req, "profiles")
                                    else "src-1:filers",
                                    col_name="filer_cik",
                                    source_id="src-1",
                                    key_tier="TIER_1_GLOBAL_STANDARD",
                                    bkcc_value="SEC_CIK",
                                )
                            ],
                            source_bindings=[],
                        )
                    ],
                )
            )
        return None

    # Attach first to know exact source_id for valid DraftVaultIR binding
    ws_svc = OpenDVService(ws, state_dir=tmp_path / "state", keyring_backend=InMemoryKeyringBackend())
    proj = ws_svc.init_project(ProjectInitReq(project_id="guided-proj", domain_namespace="asset_mgmt"))
    src_sum = ws_svc.attach_source(
        "guided-proj", SourceAttachReq(kind="sqlite", uri_or_filename=str(src_db))
    )
    table_id = f"{src_sum.source_id}:filers"
    assert proj.active_ontology_rev_id is not None
    ws_svc.commit_ontology(
        "guided-proj",
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:asset_mgmt:Filer",
                    concept_name="Filer",
                    description="Institutional filer.",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
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
                            source_id=src_sum.source_id, table_id=table_id, col_name="filer_cik"
                        )
                    ],
                )
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit Filer concept.",
        ),
    )

    draft = DraftVaultIR(
        project_id="guided-proj",
        ontology_version="0.1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_filer",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=table_id,
                        col_name="filer_cik",
                        source_id=src_sum.source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="SEC_CIK",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_cik": "filer_cik"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_filer_core",
                sat_kind="STANDARD",
                parent_name="hub_filer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_name": "filer_name"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="filer_name",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    )
                ],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_cik",
                disposition="MODELED",
                target_ref="hub_filer",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_name",
                disposition="MODELED",
                target_ref="sat_filer_core",
            ),
        ],
    )
    harness = DeterministicMockHarness(
        scripts_by_agent={
            "ship-vault": [MockTurnScript(structured_output=draft)],
            "dv-semantic-critic": [
                MockTurnScript(
                    structured_output=SemanticCriticVerdict(
                        score=92,
                        findings=[
                            "Looks great",
                            "Check check_DV-DYN-004 and tier_u3_flag.",
                        ],
                    )
                )
            ],
            "dv-horizon-critic": [
                MockTurnScript(
                    structured_output=HorizonCriticVerdict(
                        score=90,
                        classification="REASONABLY_STABLE",
                        bucket_b_questions=[
                            HorizonQuestion(
                                question_id="hq-bkcc-1",
                                brk_code="BRK-02",
                                title="Confirm BKCC_value alignment",
                                prompt="Review my_CEGAR_loop and check_DV-DYN-004.",
                                options=[
                                    HorizonOptionSpec(
                                        option_id="accept",
                                        label="Keep layout",
                                        description="Proceed safely.",
                                        implications="Preserves current keys.",
                                        operator_hint="SET_BKCC",
                                    )
                                ],
                                impact_summary="Affects BKCC_value only.",
                            )
                        ],
                        bucket_c_watchlist=[
                            "Monitor BKCC alignment",
                            "Watch my_CEGAR_loop and BKCC_value",
                        ],
                    )
                )
            ],
        },
        fault=MockFaultSpec(jargon_injection=True),
    )
    svc = OpenDVService(
        ws,
        state_dir=tmp_path / "state",
        harness=harness,
        keyring_backend=InMemoryKeyringBackend(),
    )
    run = await svc.build_vault("guided-proj", VaultBuildReq(policy="balanced"))
    assert run.status == "HOLD_CHECKPOINTED"

    guided = svc.get_guided_view("guided-proj", run.run_id)
    assert guided.readiness_brief.certification_label == "UNCERTIFIED"
    assert guided.decision_cards
    combined_plain = " ".join(
        [
            guided.headline,
            guided.readiness_brief.confidence_summary,
            *guided.readiness_brief.plain_highlights,
            *guided.readiness_brief.watchlist,
            *(
                " ".join(
                    [
                        c.title,
                        c.plain_summary,
                        c.why_it_matters,
                        *(
                            f"{o.label} {o.description} {o.implications} {o.operator_hint or ''}"
                            for o in c.options
                        ),
                    ]
                )
                for c in guided.decision_cards
            ),
        ]
    )
    for banned in ("DV-DYN-", "HashDiff", "BKCC", "BRK-", "CEGAR", "TIER_U3", "SET_BKCC"):
        assert banned.lower() not in combined_plain.lower()
    for card in guided.decision_cards:
        for opt in card.options:
            assert opt.operator_hint is None
    # Technical drawer retains raw V7/V8 audit tokens separately
    assert "v7_findings" in guided.technical_drawer
    assert _selector is not None


@pytest.mark.asyncio
async def test_upload_source_sqlite_migrates_and_dbt_dialect_propagates(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    src_db = tmp_path / "external_filers.sqlite"
    _create_tiny_sqlite(src_db)

    svc0 = OpenDVService(ws, state_dir=tmp_path / "state", keyring_backend=InMemoryKeyringBackend())
    proj = svc0.init_project(ProjectInitReq(project_id="up-sql", domain_namespace="asset_mgmt"))
    svc0.state_manager.update_project_pointers(
        "up-sql",
        settings_snapshot=proj.settings_snapshot.model_copy(update={"dbt_sql_dialect": "snowflake"}),
    )
    up_sum = svc0.upload_source(
        "up-sql",
        filename="filers.sqlite",
        content=src_db.read_bytes(),
        kind="sqlite",
    )
    assert up_sum.connector_config is not None
    assert up_sum.connector_config.uploaded_relpath == f"sources/{up_sum.source_id}/filers.sqlite"
    table_id = f"{up_sum.source_id}:filers"
    assert proj.active_ontology_rev_id is not None
    svc0.commit_ontology(
        "up-sql",
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:asset_mgmt:Filer",
                    concept_name="Filer",
                    description="Institutional filer.",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
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
                            source_id=up_sum.source_id, table_id=table_id, col_name="filer_cik"
                        )
                    ],
                )
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit Filer for uploaded SQLite source.",
        ),
    )
    draft = DraftVaultIR(
        project_id="up-sql",
        ontology_version="0.1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_filer",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=table_id,
                        col_name="filer_cik",
                        source_id=up_sum.source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="SEC_CIK",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=up_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_cik": "filer_cik"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_filer_core",
                sat_kind="STANDARD",
                parent_name="hub_filer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=up_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_name": "filer_name"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="filer_name",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    )
                ],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_cik",
                disposition="MODELED",
                target_ref="hub_filer",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_name",
                disposition="MODELED",
                target_ref="sat_filer_core",
            ),
        ],
    )
    svc = OpenDVService(
        ws,
        state_dir=tmp_path / "state",
        harness=DeterministicMockHarness(
            scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=draft)]}
        ),
        keyring_backend=InMemoryKeyringBackend(),
    )
    run6 = await svc.build_vault("up-sql", VaultBuildReq(policy="fast"))
    assert run6.status == "COMPLETED"

    mig = svc.migrate_target("up-sql", StorageTargetReq(target_id="duckdb_local"))
    assert mig.verified and mig.rows_loaded > 0

    dbt_mig = svc.migrate_target("up-sql", StorageTargetReq(target_id="dbt"))
    assert dbt_mig.verified
    dbt_dir = svc.state_manager.targets_dir("up-sql") / "dbt"
    dbt_contents = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in dbt_dir.rglob("*") if p.is_file()
    )
    assert "snowflake" in dbt_contents.lower()


def test_databricks_missing_secret_recovery_and_failing_keyring(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = OpenDVService(
        ws,
        state_dir=tmp_path / "state",
        keyring_backend=FailingKeyringBackend(),
        allow_headless_fallback=True,
    )
    svc.init_project(ProjectInitReq(project_id="dbx-proj", domain_namespace="asset_mgmt"))

    req = SourceInspectReq(
        kind="databricks_uc",
        uri_or_path="adb-987654321.12.azuredatabricks.net",
        options={"http_path": "/sql/1.0/warehouses/abc123"},
        secret_refs=["DBX_TOKEN"],
    )
    with pytest.raises(OpenDVError) as exc_missing:
        svc.inspect_source(req)
    assert exc_missing.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING

    # Binding secret via KeyringSecretStore (falls back to owner-only user_state_file on FailingKeyringBackend)
    summary = svc.set_secret("DBX_TOKEN", "dapi-test-secret-value")
    assert summary.present is True
    assert summary.backend == "user_state_file"

    # Now secret check passes cleanly into connector validation / optional driver check
    with pytest.raises(OpenDVError) as exc_after:
        svc.inspect_source(req)
    assert exc_after.value.error_code != ErrorCode.NEEDS_LOCAL_SECRET_BINDING


@pytest.mark.asyncio
async def test_bundle_rename_replace_and_legacy_pk_migration(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    src_db = ws / "tiny.sqlite"
    _create_tiny_sqlite(src_db)

    svc0 = OpenDVService(ws, state_dir=tmp_path / "state", keyring_backend=InMemoryKeyringBackend())
    proj = svc0.init_project(ProjectInitReq(project_id="proj-a", domain_namespace="asset_mgmt"))
    src_sum = svc0.attach_source(
        "proj-a", SourceAttachReq(kind="sqlite", uri_or_filename=str(src_db))
    )
    table_id = f"{src_sum.source_id}:filers"
    assert proj.active_ontology_rev_id is not None
    svc0.commit_ontology(
        "proj-a",
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:asset_mgmt:Filer",
                    concept_name="Filer",
                    description="Institutional filer.",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
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
                            source_id=src_sum.source_id, table_id=table_id, col_name="filer_cik"
                        )
                    ],
                )
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit Filer.",
        ),
    )

    draft = DraftVaultIR(
        project_id="proj-a",
        ontology_version="0.1.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_filer",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=table_id,
                        col_name="filer_cik",
                        source_id=src_sum.source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="SEC_CIK",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_cik": "filer_cik"},
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_filer_core",
                sat_kind="STANDARD",
                parent_name="hub_filer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=table_id,
                        col_mappings={"filer_name": "filer_name"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="filer_name",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    )
                ],
            )
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_cik",
                disposition="MODELED",
                target_ref="hub_filer",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="filer_name",
                disposition="MODELED",
                target_ref="sat_filer_core",
            ),
        ],
    )
    harness = DeterministicMockHarness(
        scripts_by_agent={"ship-vault": [MockTurnScript(structured_output=draft)]}
    )
    svc = OpenDVService(
        ws,
        state_dir=tmp_path / "state",
        harness=harness,
        keyring_backend=InMemoryKeyringBackend(),
    )
    await svc.build_vault("proj-a", VaultBuildReq(policy="fast"))
    mig = svc.migrate_target("proj-a", StorageTargetReq(target_id="duckdb_local"))
    assert mig.rows_loaded > 0

    zip_path, _manifest = svc.export_bundle("proj-a", mode="full_demo")
    # conflict="rename" creates proj-a-copy sharing identical source_ids & revision_ids
    rename_report = svc.import_bundle(zip_path, conflict="rename")
    assert rename_report.project_id == "proj-a-copy"
    copy_sources = svc.list_sources("proj-a-copy")
    assert len(copy_sources) == 1
    assert copy_sources[0].source_id == src_sum.source_id
    copy_duck = svc.state_manager.targets_dir("proj-a-copy") / "duckdb_local" / "proj-a-copy.duckdb"
    assert copy_duck.is_file()

    # conflict="replace" deletes old proj-a row and preserves extracted .duckdb & binaries
    replace_report = svc.import_bundle(zip_path, conflict="replace")
    assert replace_report.project_id == "proj-a"
    orig_duck = svc.state_manager.targets_dir("proj-a") / "duckdb_local" / "proj-a.duckdb"
    assert orig_duck.is_file()

    # Verify legacy single-PK SQLite migration preserves rows cleanly
    legacy_ws = tmp_path / "legacy_ws"
    (legacy_ws / ".opendv").mkdir(parents=True)
    legacy_db = legacy_ws / ".opendv" / "project.sqlite"
    conn = open_wal_connection(legacy_db, ensure_wal=True)
    try:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                domain_namespace TEXT NOT NULL,
                active_ontology_rev_id TEXT,
                active_wave_id TEXT,
                settings_snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE sources_and_tables (
                source_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                uri_or_filename TEXT NOT NULL,
                binary_sha256 TEXT,
                connector_config_json TEXT NOT NULL,
                status TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                document_map_json TEXT,
                tables_index_json TEXT NOT NULL,
                approved_scan_pages_json TEXT NOT NULL,
                attached_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE revisions (
                revision_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                rev_kind TEXT NOT NULL,
                rev_key TEXT NOT NULL,
                parent_rev_id TEXT,
                content_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, rev_kind, rev_key)
            );
            """
        )
        snap = ProjectSettingsSnapshot().model_dump_json()
        conn.execute(
            "INSERT INTO projects VALUES ('p1', 'asset_mgmt', NULL, NULL, ?, '2026-01-01', '2026-01-01')",
            (snap,),
        )
        conn.execute(
            "INSERT INTO projects VALUES ('p2', 'asset_mgmt', NULL, NULL, ?, '2026-01-01', '2026-01-01')",
            (snap,),
        )
        conn.execute(
            "INSERT INTO sources_and_tables VALUES ('s1', 'p1', 'sqlite', 'a.db', NULL, '{}', 'READY_OFFLINE', '{}', NULL, '[]', '[]', '2026-01-01', '2026-01-01')"
        )
        ont_json = GraphAOntology(ontology_version="0.0.0", domain_namespace="asset_mgmt").model_dump_json()
        meta_json = RevisionMetadata(actor="human", reason="init").model_dump_json()
        conn.execute(
            "INSERT INTO revisions VALUES ('r1', 'p1', 'ontology_version', 'r1', NULL, '0000', ?, ?, '2026-01-01')",
            (ont_json, meta_json),
        )
        conn.commit()
    finally:
        conn.close()

    migrate_per_project_primary_keys(legacy_db)
    mgr = ProjectStateManager(legacy_ws)
    mgr.initialize()
    # Now p2 can insert identical source_id='s1' and revision_id='r1' without collision
    assert mgr.get_revision("p1", "r1")["revision_id"] == "r1"
    mgr.save_revision(
        "p2",
        revision_id="r1",
        rev_kind="ontology_version",
        rev_key="r1",
        parent_rev_id=None,
        payload=HorizonAssumptionSpec(
            assumption_id="a1",
            brk_code="BRK-01",
            title="t",
            notes="n",
            steward_decision="ACCEPT",
        ),
        metadata=RevisionMetadata(actor="human", reason="copy"),
    )
    assert OntologyProposalSpec is not None
