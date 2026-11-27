"""Integration tests for project settings, proving-sample cache, and Wave-1 pre-cert waivers."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from opendv.agents.ports import (
    AgentHarnessPort,
    AgentTurnRequest,
    AgentTurnResult,
    RunEventSink,
    ScoutToolDispatcher,
)
from opendv.agents.pack_loader import load_modeling_guide
from opendv.api import create_app
from opendv.modeling.models import compute_preferences_sha256
from opendv.modeling.token_utils import count_tokens
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences, ProvingSampleCacheFile
from opendv.models.ontology import ConceptAttribute, ConceptTableBinding
from opendv.models.ontology_deltas import AddConcept
from opendv.models.pii_tokenizer import PII_TOKEN_RE
from opendv.models.policy import HarnessBudget
from opendv.models.project import HarnessProvider, ProjectSettingsSnapshot
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
)
from opendv.models.wire_dtos_discovery import OntologyCommitReq, ProjectInitReq, SourceAttachReq
from opendv.models.wire_dtos_vault import (
    HorizonCriticVerdict,
    SemanticCriticVerdict,
    VaultBuildReq,
    WaiverSubmitReq,
)
from opendv.service.app_service import OpenDVService
from opendv.service.workflows._vault_helpers import load_cegar_result, load_verifier_inputs
from opendv.service.workflows._vault_proving import (
    load_cached_proving_samples,
    proving_sample_path,
)
from opendv.sources import compute_salt_digest


def _create_duckdb_customers(path: Path, row_count: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE customers (
                customer_code VARCHAR PRIMARY KEY,
                email VARCHAR NOT NULL,
                ssn VARCHAR NOT NULL,
                tier_code VARCHAR NOT NULL
            )
            """
        )
        rows = [
            (
                f"CUST-{idx:03d}",
                f"user{idx}@example.com",
                f"123-45-{6780 + idx:04d}",
                "GOLD" if idx % 2 == 0 else "SILVER",
            )
            for idx in range(1, row_count + 1)
        ]
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", rows)
    finally:
        conn.close()


def _create_sqlite_customers(path: Path, *, surrogate_pk: bool = False) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        if surrogate_pk:
            conn.execute(
                """
                CREATE TABLE customers (
                    id INTEGER PRIMARY KEY,
                    customer_email TEXT NOT NULL,
                    region_code TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                "INSERT INTO customers (id, customer_email, region_code) VALUES (?, ?, ?)",
                [
                    (101, "alice@retail.example", "US_EAST"),
                    (102, "bob@retail.example", "EU_WEST"),
                    (103, "carol@retail.example", "AP_SOUTH"),
                ],
            )
        else:
            conn.execute(
                """
                CREATE TABLE accounts (
                    acct_code TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    status_code TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                "INSERT INTO accounts (acct_code, email, status_code) VALUES (?, ?, ?)",
                [
                    ("ACC-001", "owner1@bank.example", "ACTIVE"),
                    ("ACC-002", "owner2@bank.example", "PENDING"),
                ],
            )
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


class _DeterministicVaultHarness(AgentHarnessPort):
    """Lightweight deterministic AgentHarnessPort returning DraftVaultIR + V7/V8 verdicts."""

    def __init__(
        self,
        *,
        draft_ir: DraftVaultIR,
        accepted_waiver_ids: list[str],
    ) -> None:
        self._draft_ir = draft_ir
        self._accepted_waiver_ids = list(accepted_waiver_ids)
        self.requests: list[AgentTurnRequest] = []

    @property
    def provider_name(self) -> HarnessProvider:
        return "agy"

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: RunEventSink | None = None,
        scout_dispatcher: ScoutToolDispatcher | None = None,
    ) -> AgentTurnResult:
        _ = event_sink, scout_dispatcher
        assert isinstance(request, AgentTurnRequest)
        self.requests.append(request)

        if request.agent_id == "ship-vault":
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="gemini-3.8-flash-high",
                structured_output=self._draft_ir,
                turns_used=1,
                input_tokens=180,
                output_tokens=120,
                total_tokens=300,
                token_count_source="reported",
            )
        if request.agent_id == "dv-semantic-critic":
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="gemini-4-argon-high",
                structured_output=SemanticCriticVerdict(
                    score=96,
                    findings=["Clean Wave-1 DV2.0 vault with audited V6b waiver."],
                    accepted_waiver_ids=list(self._accepted_waiver_ids),
                ),
                turns_used=1,
                input_tokens=120,
                output_tokens=60,
                total_tokens=180,
                token_count_source="reported",
            )
        if request.agent_id == "dv-horizon-critic":
            return AgentTurnResult(
                agent_id=request.agent_id,
                provider=self.provider_name,
                model_id="gemini-4-argon-high",
                structured_output=HorizonCriticVerdict(
                    score=93,
                    classification="REASONABLY_STABLE",
                    absorbed_natively=["sat_customer_pii isolates customer_email"],
                    bucket_c_watchlist=["Monitor regional code expansion"],
                ),
                turns_used=1,
                input_tokens=110,
                output_tokens=55,
                total_tokens=165,
                token_count_source="reported",
            )
        return AgentTurnResult(
            agent_id=request.agent_id,
            provider=self.provider_name,
            model_id="gemini-3.8-flash-high",
            text_output="{}",
            turns_used=1,
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            token_count_source="reported",
        )

    def check_budget(self, budget: HarnessBudget, *, turns: int, tokens: int) -> bool:
        return turns <= budget.max_turns and tokens <= budget.max_tokens


def test_patch_and_put_project_settings_validation_and_persistence(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    state_dir = tmp_path / "state"
    svc = OpenDVService(ws, state_dir=state_dir)
    app = create_app(svc)

    with TestClient(app) as tc:
        init_resp = tc.post(
            "/api/v1/projects",
            json={"project_id": "proj-settings", "domain_namespace": "finance"},
        )
        assert init_resp.status_code == 200
        initial = init_resp.json()["settings_snapshot"]
        assert initial["proving_sample_rows"] == 200
        assert initial["real_row_proving"] is True
        assert initial["business_context_hints"] is None

        # PATCH partial fields via model_fields_set
        patch_resp = tc.patch(
            "/api/v1/projects/proj-settings/settings",
            json={
                "modeling_preferences": {
                    "low_cardinality_strategy": "keep_in_satellite",
                    "surrogate_key_strategy": "allow_with_bkcc_note",
                    "disabled_v6b_blocks": ["DV-AP-003"],
                },
                "proving_sample_rows": 50,
                "real_row_proving": False,
                "business_context_hints": "Prefer corporate CIK over ticker.",
            },
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.json()["settings_snapshot"]
        assert patched["proving_sample_rows"] == 50
        assert patched["real_row_proving"] is False
        assert patched["business_context_hints"] == "Prefer corporate CIK over ticker."
        assert patched["modeling_preferences"]["low_cardinality_strategy"] == "keep_in_satellite"
        assert patched["modeling_preferences"]["surrogate_key_strategy"] == "allow_with_bkcc_note"
        assert patched["modeling_preferences"]["disabled_v6b_blocks"] == ["DV-AP-003"]
        assert patched["dbt_sql_dialect"] == "duckdb"
        assert patched["cegar_policy"] == "balanced"

        # Reload from a fresh OpenDVService to verify SQLite WAL persistence
        reloaded_svc = OpenDVService(ws, state_dir=state_dir)
        reloaded = reloaded_svc.get_project("proj-settings").settings_snapshot
        assert reloaded.proving_sample_rows == 50
        assert reloaded.real_row_proving is False
        assert reloaded.business_context_hints == "Prefer corporate CIK over ticker."
        assert reloaded.modeling_preferences.disabled_v6b_blocks == ["DV-AP-003"]

        # Explicit business_context_hints=null clears it while leaving omitted fields untouched
        clear_resp = tc.patch(
            "/api/v1/projects/proj-settings/settings",
            json={"business_context_hints": None},
        )
        assert clear_resp.status_code == 200
        cleared = clear_resp.json()["settings_snapshot"]
        assert cleared["business_context_hints"] is None
        assert cleared["proving_sample_rows"] == 50
        assert cleared["real_row_proving"] is False
        assert cleared["modeling_preferences"]["low_cardinality_strategy"] == "keep_in_satellite"
        assert cleared["modeling_preferences"]["disabled_v6b_blocks"] == ["DV-AP-003"]

        # > 1,500-token business_context_hints fails closed (HTTP + OpenDVError)
        over_hints = "x_y " * 1200  # 4,800 chars <= 12,000, > 1,500 cl100k_base tokens
        assert len(over_hints) <= 12_000
        assert count_tokens(over_hints) > 1_500
        r_hints = tc.patch(
            "/api/v1/projects/proj-settings/settings",
            json={"business_context_hints": over_hints},
        )
        assert r_hints.status_code in (400, 422)
        assert r_hints.json()["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value
        with pytest.raises(OpenDVError) as exc_hints:
            svc.projects.update_project_settings(
                "proj-settings",
                ProjectSettingsSnapshot(business_context_hints=over_hints),
            )
        assert exc_hints.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        # strict + non-empty disabled_v6b_blocks fails closed (HTTP + OpenDVError)
        r_strict = tc.patch(
            "/api/v1/projects/proj-settings/settings",
            json={
                "cegar_policy": "strict",
                "modeling_preferences": {"disabled_v6b_blocks": ["DV-AP-003"]},
            },
        )
        assert r_strict.status_code in (400, 422)
        assert r_strict.json()["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value
        with pytest.raises(OpenDVError) as exc_strict:
            svc.projects.update_project_settings(
                "proj-settings",
                ProjectSettingsSnapshot(
                    cegar_policy="strict",
                    v7_critic_tier="FRONTIER_CRITIC",
                    modeling_preferences=ModelingPreferences(disabled_v6b_blocks=["DV-AP-003"]),
                ),
            )
        assert exc_strict.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        # Invalid V6b rule ID fails closed (HTTP + OpenDVError)
        r_rule = tc.patch(
            "/api/v1/projects/proj-settings/settings",
            json={"modeling_preferences": {"disabled_v6b_blocks": ["DV-AP-999"]}},
        )
        assert r_rule.status_code in (400, 422)
        assert r_rule.json()["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value
        with pytest.raises(OpenDVError) as exc_rule:
            svc.projects.update_project_settings(
                "proj-settings",
                ProjectSettingsSnapshot(
                    modeling_preferences=ModelingPreferences(disabled_v6b_blocks=["DV-AP-999"])
                ),
            )
        assert exc_rule.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        # PUT replaces full ProjectSettingsSnapshot
        put_payload = ProjectSettingsSnapshot(
            harness_provider="claude",
            v7_critic_tier="FRONTIER_CRITIC",
            cegar_policy="strict",
            dbt_sql_dialect="postgres",
            proving_sample_rows=100,
            real_row_proving=True,
            business_context_hints="Strict full replacement snapshot.",
            modeling_preferences=ModelingPreferences(
                multi_source_hub_strategy="companion_hub_with_sal",
                low_cardinality_strategy="auto_ref_table",
                disabled_v6b_blocks=[],
            ),
        )
        put_resp = tc.put(
            "/api/v1/projects/proj-settings/settings",
            json=put_payload.model_dump(mode="json"),
        )
        assert put_resp.status_code == 200
        replaced = ProjectSettingsSnapshot.model_validate(put_resp.json()["settings_snapshot"])
        assert replaced == put_payload
        assert svc.get_project("proj-settings").settings_snapshot == put_payload


def test_proving_sample_cache_lifecycle_auto_invalidation_and_bundle_exclusion(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    state_dir = tmp_path / "state"
    svc = OpenDVService(ws, state_dir=state_dir)

    project_id = "proj-proving"
    svc.init_project(ProjectInitReq(project_id=project_id, domain_namespace="retail"))
    svc.projects.update_project_settings(
        project_id,
        ProjectSettingsSnapshot(proving_sample_rows=3, real_row_proving=True),
    )

    # Real DuckDB (5 rows > limit 3 -> incomplete) + uploaded SQLite (2 rows <= limit 3 -> complete)
    duck_path = ws / "data" / "crm.duckdb"
    _create_duckdb_customers(duck_path, row_count=5)
    sqlite_bytes = _create_sqlite_customers(tmp_path / "staging" / "accounts.sqlite")

    duck_sum = svc.attach_source(
        project_id,
        SourceAttachReq(kind="duckdb", uri_or_filename="data/crm.duckdb"),
    )
    sqlite_sum = svc.upload_source(
        project_id,
        filename="accounts.sqlite",
        content=sqlite_bytes,
    )

    # Sidecar files created under .opendv/projects/<id>/proving_samples/<source_id>.json
    duck_cache_path = proving_sample_path(svc.state_manager, project_id, duck_sum.source_id)
    sqlite_cache_path = proving_sample_path(svc.state_manager, project_id, sqlite_sum.source_id)
    assert duck_cache_path.is_file()
    assert sqlite_cache_path.is_file()

    duck_raw_json = duck_cache_path.read_text(encoding="utf-8")
    sqlite_raw_json = sqlite_cache_path.read_text(encoding="utf-8")
    for cleartext in ("user1@example.com", "123-45-6781", "owner1@bank.example"):
        assert cleartext not in duck_raw_json
        assert cleartext not in sqlite_raw_json

    duck_cache = ProvingSampleCacheFile.model_validate_json(duck_raw_json)
    sqlite_cache = ProvingSampleCacheFile.model_validate_json(sqlite_raw_json)
    duck_tid = f"{duck_sum.source_id}:customers"
    sqlite_tid = f"{sqlite_sum.source_id}:accounts"

    assert duck_cache.cached_sample_rows_limit == 3
    assert duck_cache.tables[duck_tid].cached_sample_rows_limit == 3
    assert len(duck_cache.tables[duck_tid].rows) == 3
    assert duck_cache.tables[duck_tid].is_complete_table is False
    for row in duck_cache.tables[duck_tid].rows:
        assert PII_TOKEN_RE.fullmatch(str(row["email"]))
        assert PII_TOKEN_RE.fullmatch(str(row["ssn"]))

    assert sqlite_cache.cached_sample_rows_limit == 3
    assert len(sqlite_cache.tables[sqlite_tid].rows) == 2
    assert sqlite_cache.tables[sqlite_tid].is_complete_table is True
    for row in sqlite_cache.tables[sqlite_tid].rows:
        assert PII_TOKEN_RE.fullmatch(str(row["email"]))

    profiles = [
        SourceProfile.model_validate(
            svc.state_manager.get_source_record(project_id, s.source_id)["profile"]
        )
        for s in svc.list_sources(project_id)
    ]

    # Matching load returns cached rows + completeness flags
    loaded_rows, loaded_flags = load_cached_proving_samples(
        svc.state_manager,
        svc.secret_store,
        project_id,
        profiles,
        proving_sample_rows=3,
        real_row_proving=True,
    )
    assert set(loaded_rows) == {duck_tid, sqlite_tid}
    assert len(loaded_rows[duck_tid]) == 3
    assert len(loaded_rows[sqlite_tid]) == 2
    assert loaded_flags == {duck_tid: False, sqlite_tid: True}

    # Auto-invalidation 1: proving_sample_rows change
    rows_lim, flags_lim = load_cached_proving_samples(
        svc.state_manager,
        svc.secret_store,
        project_id,
        profiles,
        proving_sample_rows=5,
        real_row_proving=True,
    )
    assert rows_lim == {}
    assert flags_lim == {}

    # Auto-invalidation 2: schema_fingerprint mismatch (mutated table column type)
    mutated_profiles = []
    for prof in profiles:
        mutated_tables = [
            tbl.model_copy(
                update={
                    "columns": [
                        c.model_copy(update={"sql_type": "BIGINT"}) if c.col_name == "email" else c
                        for c in tbl.columns
                    ]
                }
            )
            for tbl in prof.tables
        ]
        mutated_profiles.append(prof.model_copy(update={"tables": mutated_tables}))
    rows_fp, flags_fp = load_cached_proving_samples(
        svc.state_manager,
        svc.secret_store,
        project_id,
        mutated_profiles,
        proving_sample_rows=3,
        real_row_proving=True,
    )
    assert rows_fp == {}
    assert flags_fp == {}

    # Auto-invalidation 3: salt_digest mismatch
    salt_key = f"PROVING_SALT_{project_id}"
    orig_salt = svc.secret_store.get_secret(salt_key)
    assert orig_salt is not None
    rotated_salt = "b" * 64
    assert compute_salt_digest(rotated_salt) != compute_salt_digest(orig_salt)
    svc.secret_store.set_secret(salt_key, rotated_salt)
    rows_salt, flags_salt = load_cached_proving_samples(
        svc.state_manager,
        svc.secret_store,
        project_id,
        profiles,
        proving_sample_rows=3,
        real_row_proving=True,
    )
    assert rows_salt == {}
    assert flags_salt == {}

    # export_bundle(mode="full_demo") completely excludes proving_samples/
    bundle_path, manifest = svc.export_bundle(project_id, mode="full_demo")
    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
    assert any(n.startswith("sources/") for n in names)
    assert not any("proving_samples" in n for n in names)
    assert not any("proving_samples" in k for k in manifest.entries)

    # detach_source deletes the sidecar file
    svc.detach_source(project_id, duck_sum.source_id)
    assert not duck_cache_path.exists()
    assert sqlite_cache_path.is_file()


@pytest.mark.asyncio
async def test_wave1_pre_cert_waiver_and_vault_build_hydration(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    state_dir = tmp_path / "state"

    # Trusted workspace dv2-modeling-guide.md override
    guide_path = ws / ".opendv" / "agents" / "dv2-modeling-guide.md"
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(
        "# Trusted Retail DV2 Modeling Guide\n"
        "Marker: TRUSTED_GUIDE_MARKER_2026\n"
        "Always honor V1-V6 > ModelingPreferences > business_context_hints.\n",
        encoding="utf-8",
    )

    project_id = "wave1-proj"
    _create_sqlite_customers(ws / "data" / "retail_customers.sqlite", surrogate_pk=True)

    # Bootstrap service first to attach source, commit ontology, and record Wave-1 pre-cert waiver
    boot_svc = OpenDVService(ws, state_dir=state_dir)
    boot_svc.trust_workspace_path(ws)
    expected_guide_text, expected_guide_sha = load_modeling_guide(
        workspace_root=ws,
        trusted_workspaces=boot_svc.get_settings().trusted_workspaces,
    )
    assert "TRUSTED_GUIDE_MARKER_2026" in expected_guide_text

    proj = boot_svc.init_project(ProjectInitReq(project_id=project_id, domain_namespace="retail"))
    assert proj.active_wave_id is None
    assert proj.active_ontology_rev_id is not None

    prefs = ModelingPreferences(
        low_cardinality_strategy="keep_in_satellite",
        surrogate_key_strategy="allow_with_bkcc_note",
    )
    hints_text = "Customer retail operational feed uses integer id locally; keep region_code in sat."
    boot_svc.projects.update_project_settings(
        project_id,
        ProjectSettingsSnapshot(
            modeling_preferences=prefs,
            business_context_hints=hints_text,
            proving_sample_rows=50,
            real_row_proving=True,
        ),
    )

    src_sum = boot_svc.attach_source(
        project_id,
        SourceAttachReq(kind="sqlite", uri_or_filename="data/retail_customers.sqlite"),
    )
    tid = f"{src_sum.source_id}:customers"

    ont_rev = boot_svc.commit_ontology(
        project_id,
        OntologyCommitReq(
            operations=[
                AddConcept(
                    concept_uri="urn:retail:Customer",
                    concept_name="Customer",
                    description="Retail customer entity.",
                    key_authority_tier="TIER_3_LOCAL_SCOPED",
                    bkcc_default="RETAIL_APP",
                    attributes=[
                        ConceptAttribute(
                            name="id",
                            logical_type="INT64",
                            sql_type_hint="INTEGER",
                            nullable=False,
                            is_business_key=True,
                            is_pii=False,
                        )
                    ],
                    table_bindings=[
                        ConceptTableBinding(
                            source_id=src_sum.source_id,
                            table_id=tid,
                            col_name="id",
                        )
                    ],
                )
            ],
            base_revision_id=proj.active_ontology_rev_id,
            rationale="Commit retail Customer concept before Wave 1 vault build.",
        ),
    )

    # Wave-1 pre-certification waiver while active_wave_id is still None
    waiver = boot_svc.vault.submit_vault_waiver(
        project_id,
        WaiverSubmitReq(
            waiver_id="WAIV-CLIENT-HINT",
            rule_id="DV-AP-003",
            entity_name="hub_customer",
            rationale="Operational CRM table only exposes integer surrogate id in Wave 1.",
        ),
    )
    assert waiver.waiver_id.startswith("WAIV-")
    assert waiver.rule_id == "DV-AP-003"
    assert waiver.entity_name == "hub_customer"
    assert boot_svc.get_project(project_id).active_wave_id is None
    waiver_revs = boot_svc.state_manager.list_revisions(project_id, rev_kind="vault_waiver")
    assert len(waiver_revs) == 1

    # Build DraftVaultIR for deterministic AgentHarnessPort
    draft_ir = DraftVaultIR(
        project_id=project_id,
        ontology_version=ont_rev.ontology_version,
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_customer",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id=tid,
                        col_name="id",
                        source_id=src_sum.source_id,
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="RETAIL_APP",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=tid,
                        col_mappings={"id": "id"},
                    )
                ],
            )
        ],
        links=[],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_customer_core",
                sat_kind="STANDARD",
                parent_name="hub_customer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=tid,
                        col_mappings={"region_code": "region_code"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="region_code",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    )
                ],
            ),
            DraftSatelliteSpec(
                sat_name="sat_customer_pii",
                sat_kind="PII",
                parent_name="hub_customer",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=src_sum.source_id,
                        table_id=tid,
                        col_mappings={"customer_email": "customer_email"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="customer_email",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=True,
                    )
                ],
            ),
        ],
        column_triage=[
            ColumnTriageEntry(
                table_id=tid,
                col_name="id",
                disposition="MODELED",
                target_ref="hub_customer",
            ),
            ColumnTriageEntry(
                table_id=tid,
                col_name="region_code",
                disposition="MODELED",
                target_ref="sat_customer_core",
            ),
            ColumnTriageEntry(
                table_id=tid,
                col_name="customer_email",
                disposition="MODELED",
                target_ref="sat_customer_pii",
            ),
        ],
    )

    harness = _DeterministicVaultHarness(
        draft_ir=draft_ir,
        accepted_waiver_ids=[waiver.waiver_id],
    )
    svc = OpenDVService(ws, state_dir=state_dir, harness=harness)

    # Confirm verifier input hydration before build_vault
    _, _, _, _, _, _, hydrated_ctx = load_verifier_inputs(
        svc.state_manager,
        ws,
        project_id,
        secret_store=svc.secret_store,
    )
    assert hydrated_ctx.source_rows_by_table is not None
    assert tid in hydrated_ctx.source_rows_by_table
    assert len(hydrated_ctx.source_rows_by_table[tid]) == 3
    assert all(
        PII_TOKEN_RE.fullmatch(str(r["customer_email"]))
        for r in hydrated_ctx.source_rows_by_table[tid]
    )
    assert hydrated_ctx.table_sample_flags == {tid: True}
    assert hydrated_ctx.modeling_preferences == prefs
    assert any(w.waiver_id == waiver.waiver_id for w in hydrated_ctx.persisted_waivers)

    # Run Vault build through ServiceModelingHarnessAdapter
    run6 = await svc.vault.build_vault(project_id, VaultBuildReq(policy="balanced"))
    assert run6.status == "COMPLETED"

    agent_ids = [req.agent_id for req in harness.requests]
    assert "ship-vault" in agent_ids
    assert "dv-semantic-critic" in agent_ids
    assert "dv-horizon-critic" in agent_ids

    ship_req = next(req for req in harness.requests if req.agent_id == "ship-vault")
    assert "TRUSTED_GUIDE_MARKER_2026" in ship_req.prompt
    assert "keep_in_satellite" in ship_req.prompt
    assert "allow_with_bkcc_note" in ship_req.prompt
    assert hints_text in ship_req.prompt

    cegar_res = load_cegar_result(svc.state_manager, project_id, run6.run_id)
    assert cegar_res is not None
    assert cegar_res.status == "CERTIFIED"
    assert cegar_res.modeling_guide_sha256 == expected_guide_sha
    assert cegar_res.preferences_sha256 == compute_preferences_sha256(prefs)
    assert cegar_res.certified_ir is not None
    assert any(
        j.waiver_id == waiver.waiver_id and j.rule_id == "DV-AP-003"
        for j in cegar_res.certified_ir.justifications
    )
    assert cegar_res.verifier_report is not None
    assert cegar_res.verifier_report.prover_report is not None
    assert any(
        res.status == "PASS_FULL"
        for res in cegar_res.verifier_report.prover_report.real_data_proof_results.values()
    )

    vault_status = svc.vault.get_vault_status(project_id)
    assert vault_status.next_action == "READY_FOR_DOWNSTREAM"
    assert vault_status.status == "CERTIFIED"
    assert vault_status.active_wave_id is not None
    assert any(
        w.waiver_id == waiver.waiver_id
        and w.rule_id == "DV-AP-003"
        and w.entity_name == "hub_customer"
        for w in vault_status.waived_rules
    )

    verify_resp = svc.vault.verify_vault(project_id)
    assert verify_resp.passed
