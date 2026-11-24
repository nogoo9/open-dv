"""Shared isolation, fixture, golden-bundle, and parity helpers for Phase V2-6 M3 tests."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest

from opendv.agents.ports import AgentHarnessPort, AgentTurnRequest
from opendv.models import WorkspaceSettings
from opendv.models.diagnostics import ClarificationQuestion, DocumentCitationRef, KeyTierEnum
from opendv.models.events import AuditLogRecord, OcrPageReceipt
from opendv.models.project import BundleManifest
from opendv.models.sources import (
    ColumnProfile,
    ConnectorConfigPayload,
    SourceProfile,
    TableIndexEntry,
    TableProfile,
)
from opendv.models.vault_ir import (
    BusinessKeyPart,
    ColumnTriageEntry,
    DecimalType,
    DraftField,
    DraftVaultIR,
    LinkParticipantSpec,
    PredicateLeaf,
)
from opendv.models.vault_ir_certified import (
    BronzeTableSpec,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    HashRecipeSpec,
    IRField,
    LoadSemanticsSpec,
    MartViewSpec,
    PhysicalLayoutHints,
    PitBridgeSpec,
    SourceBindingSpec,
    StagingViewSpec,
)
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    OntologyCommitReq,
    ProjectInitReq,
    ScanApprovalReq,
    SourceAttachReq,
)
from opendv.models.wire_dtos_vault import StorageTargetReq, VaultBuildReq
from opendv.service.app_service import OpenDVService
from opendv.service.bundles.exporter import export_project_bundle
from opendv.service.os_platform import resolve_user_state_dir
from opendv.service.settings_store import SettingsStore
from tests.api._api_test_helpers import (
    PUBLIC_AM,
    build_wave1_am_draft,
    build_wave1_commit_ops,
    build_wave2_am_draft,
    copy_public_am_wave1,
    load_project_profiles,
)
from tests.helpers import DeterministicMockHarness

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_BUNDLE_PATH = REPO_ROOT / "tests" / "golden_v2" / "am_core_bundle.core.opendv.zip"
TRAP_DUCKDB = PUBLIC_AM / "ds_cegar_12trap.duckdb"
FIXTURE_TIMESTAMP = "2026-11-24T00:00:00+00:00"
_HOST_STATE_DIR = resolve_user_state_dir()
MD_STEWARDSHIP_QUOTE = "Stewardship Desk must open a formal"
PDF_EXCEPTION_QUOTE = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"


@dataclass(frozen=True)
class IsolatedWorkspace:
    """Isolated per-test filesystem and env contract protecting host user state."""

    ws: Path
    opendv_home: Path
    user_state: Path
    state_dir: Path
    host_local_opendv: Path

    def subprocess_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return subprocess environment cleanly isolated from host OpenDV state."""
        env = dict(os.environ)
        env.update(
            {
                "OPENDV_HOME": str(self.opendv_home),
                "LOCALAPPDATA": str(self.user_state),
                "XDG_STATE_HOME": str(self.user_state),
                "HOME": str(self.user_state),
                "USERPROFILE": str(self.user_state),
                "APPDATA": str(self.user_state / "Roaming"),
                "OPENDV_WORKSPACE_ROOTS": f"{self.ws}{os.pathsep}{self.ws.parent}{os.pathsep}{REPO_ROOT}",
            }
        )
        if extra:
            env.update(extra)
        return env

    def make_service(self, harness: AgentHarnessPort | None = None) -> OpenDVService:
        """Instantiate OpenDVService bound exclusively to this isolated workspace and state_dir."""
        svc = OpenDVService(self.ws, state_dir=self.state_dir, harness=harness)
        svc.update_settings(
            WorkspaceSettings(secret_backend="user_state_file", updated_at=FIXTURE_TIMESTAMP)
        )
        return svc


def snapshot_dir_mtimes(directory: Path) -> dict[str, float]:
    """Capture relative file mtimes under a host directory to verify zero mutation."""
    if not directory.exists():
        return {}
    return {
        str(p.relative_to(directory)): p.stat().st_mtime
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def setup_isolated_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    preserve_live_auth: bool = False,
) -> IsolatedWorkspace:
    """Create strict tmp_path isolation for OPENDV_HOME, user_state, and user_state_file."""
    host_state = _HOST_STATE_DIR
    ws = tmp_path / "ws"
    opendv_home = ws / ".opendv"
    user_state = tmp_path / "user_state"
    state_dir = user_state / "opendv"
    for folder in (ws, opendv_home, state_dir, user_state / "Roaming"):
        folder.mkdir(parents=True, exist_ok=True)

    SettingsStore(user_state_dir=state_dir).save_settings(
        WorkspaceSettings(secret_backend="user_state_file", updated_at=FIXTURE_TIMESTAMP)
    )

    if monkeypatch is not None:
        monkeypatch.setenv("OPENDV_HOME", str(opendv_home))
        monkeypatch.setenv(
            "OPENDV_WORKSPACE_ROOTS", f"{ws}{os.pathsep}{tmp_path}{os.pathsep}{REPO_ROOT}"
        )
        if not preserve_live_auth:
            monkeypatch.setenv("LOCALAPPDATA", str(user_state))
            monkeypatch.setenv("XDG_STATE_HOME", str(user_state))
            monkeypatch.setenv("HOME", str(user_state))
            monkeypatch.setenv("USERPROFILE", str(user_state))
            monkeypatch.setenv("APPDATA", str(user_state / "Roaming"))
        elif os.environ.get("OPENDV_AGY_CLI_BIN") == "disabled":
            # Undo tests/parity/conftest.py import-time guard when explicitly running live providers.
            monkeypatch.delenv("OPENDV_AGY_CLI_BIN", raising=False)

    return IsolatedWorkspace(
        ws=ws,
        opendv_home=opendv_home,
        user_state=user_state,
        state_dir=state_dir,
        host_local_opendv=host_state,
    )


def build_wave1_v1_ops_with_pdf_occ2(
    *,
    sqlite_sid: str,
    pq_sid: str,
    md_sid: str,
    pdf_sid: str,
    pdf_sha256: str,
    pdf_char_start: int,
    pdf_char_end: int,
    include_seed_delete: bool = True,
) -> list[dict[str, Any]]:
    """Build Wave 1 commit ops promoted to v1.0.0 with PDF occurrence=2 grounding."""
    ops: list[dict[str, Any]] = []
    if include_seed_delete:
        ops.append(
            {
                "op_id": "op_del_seed",
                "op_type": "DeleteConcept",
                "concept_uri": "urn:asset_mgmt:TempSeed",
                "rationale": "Promote clean Wave 1 asset management ontology to v1.0.0",
            }
        )
    base_ops = build_wave1_commit_ops(sqlite_sid, pq_sid, md_sid, MD_STEWARDSHIP_QUOTE)
    occ2_ref = DocumentCitationRef(
        doc_id=pdf_sid,
        page_number=5,
        verbatim_quote=PDF_EXCEPTION_QUOTE,
        occurrence=2,
        char_start=pdf_char_start,
        char_end=pdf_char_end,
        content_sha256=pdf_sha256,
    ).model_dump(mode="json")
    for op in base_ops:
        if op.get("concept_name") == "Proxy Ballot":
            cites = list(op.get("source_citations") or [])
            cites.append(occ2_ref)
            op = {**op, "source_citations": cites}
        ops.append(op)
    return ops


def commit_wave1_v1_ontology(
    svc: OpenDVService,
    project_id: str,
    *,
    sqlite_sid: str,
    pq_sid: str,
    md_sid: str,
    pdf_sid: str,
) -> str:
    """Commit seed 0.1.0 then Wave 1 MAJOR release v1.0.0 and return revision_id."""
    proj = svc.get_project(project_id)
    r_md = svc.resolve_citation_span(
        project_id,
        CitationResolveReq(
            doc_id=md_sid,
            page_number=1,
            verbatim_quote=MD_STEWARDSHIP_QUOTE,
            occurrence=1,
        ),
    )
    r_pdf = svc.resolve_citation_span(
        project_id,
        CitationResolveReq(
            doc_id=pdf_sid,
            page_number=5,
            verbatim_quote=PDF_EXCEPTION_QUOTE,
            occurrence=2,
        ),
    )
    seed_ops = [
        {
            "op_id": "op_seed",
            "op_type": "AddConcept",
            "concept_uri": "urn:asset_mgmt:TempSeed",
            "concept_name": "TempSeed",
            "description": "Initial grounded seed concept prior to Wave 1 v1.0.0 promotion.",
            "key_authority_tier": "TIER_2_ENTERPRISE_SOR",
            "source_citations": [
                {
                    "doc_id": md_sid,
                    "page_number": 1,
                    "verbatim_quote": MD_STEWARDSHIP_QUOTE,
                    "occurrence": 1,
                    "char_start": r_md.char_start,
                    "char_end": r_md.char_end,
                    "content_sha256": r_md.resolved_sha256,
                }
            ],
            "rationale": "Commit initial v0.1.0 baseline seed",
        }
    ]
    rev0 = svc.commit_ontology(
        project_id,
        OntologyCommitReq.model_validate(
            {
                "base_revision_id": proj.active_ontology_rev_id or "",
                "operations": seed_ops,
                "rationale": "Commit temporary seed ontology v0.1.0",
            }
        ),
    )
    w1_ops = build_wave1_v1_ops_with_pdf_occ2(
        sqlite_sid=sqlite_sid,
        pq_sid=pq_sid,
        md_sid=md_sid,
        pdf_sid=pdf_sid,
        pdf_sha256=r_pdf.resolved_sha256 or "",
        pdf_char_start=r_pdf.char_start or 0,
        pdf_char_end=r_pdf.char_end or 0,
    )
    rev1 = svc.commit_ontology(
        project_id,
        OntologyCommitReq.model_validate(
            {
                "base_revision_id": rev0.revision_id,
                "operations": w1_ops,
                "rationale": "Promote grounded Wave 1 asset management ontology to v1.0.0",
                "low_confidence_grounding_waiver": True,
                "grounding_rationale": "Wave 1 public_am concepts grounded via steward approval",
                "waive_quarantine": True,
            }
        ),
    )
    return rev1.revision_id


def build_wave2_hold_draft(
    project_id: str, ontology_version: str, profiles: list[SourceProfile]
) -> DraftVaultIR:
    """Wrap Wave 2 AM draft with an L3 ambiguity question on OTC collateral BK choice."""
    draft = build_wave2_am_draft(project_id, ontology_version, profiles)
    q = ClarificationQuestion(
        question_id="q_otc_key",
        concept_name="OtcCollateral",
        title="Confirm OTC Collateral canonical business key",
        prompt=(
            "Should hub_otc_collateral lock on global uti_id (TIER_1) or bilateral "
            "isda_master_id (TIER_2)?"
        ),
        sample_rows=["OTC-TRD-00001 | uti_id=UTI-LEI5493-000001 | isda_master_id=ISDA-2026-00001"],
        impact_if_locked="Freezes hub_otc_collateral business key across future waves.",
        recommended_answer="uti_id",
    )
    return draft.model_copy(update={"pending_clarifications": [q]})


def build_v2_golden_bundle(output_path: Path, project_id: str = "am-golden-w1") -> BundleManifest:
    """Deterministically build tests/golden_v2/am_core_bundle.core.opendv.zip from data/public_am/."""

    async def _run() -> BundleManifest:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            ws = td / "ws"
            pub_dir = copy_public_am_wave1(ws)
            state_dir = td / "state"
            profs_box: list[list[SourceProfile]] = [[]]

            def _selector(req: AgentTurnRequest) -> dict[str, Any]:
                if req.agent_id == "ship-vault":
                    return build_wave1_am_draft(project_id, "1.0.0", profs_box[0]).model_dump(
                        mode="json"
                    )
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

            svc = OpenDVService(
                ws, state_dir=state_dir, harness=DeterministicMockHarness(selector=_selector)
            )
            svc.update_settings(
                WorkspaceSettings(secret_backend="user_state_file", updated_at=FIXTURE_TIMESTAMP)
            )
            svc.init_project(
                ProjectInitReq(project_id=project_id, domain_namespace="asset_management")
            )

            s_sql = svc.attach_source(
                project_id,
                SourceAttachReq(
                    kind="sqlite", uri_or_filename=str(pub_dir / "am_sec_treasury_ibor.sqlite")
                ),
            )
            s_pq = svc.attach_source(
                project_id,
                SourceAttachReq(
                    kind="parquet", uri_or_filename=str(pub_dir / "am_esg_stewardship_proxy.parquet")
                ),
            )
            s_md = svc.attach_source(
                project_id,
                SourceAttachReq(
                    kind="markdown",
                    uri_or_filename=str(pub_dir / "am_stewardship_domain_notes.md"),
                ),
            )
            s_pdf = svc.attach_source(
                project_id,
                SourceAttachReq(
                    kind="pdf", uri_or_filename=str(pub_dir / "am_custody_and_voting_guide.pdf")
                ),
            )
            dm_pdf = svc.approve_scan_pages(
                project_id,
                s_pdf.source_id,
                ScanApprovalReq(
                    page_numbers=[4],
                    rationale="Steward approved OCR extraction of custody scan page 4",
                ),
            )
            commit_wave1_v1_ontology(
                svc,
                project_id,
                sqlite_sid=s_sql.source_id,
                pq_sid=s_pq.source_id,
                md_sid=s_md.source_id,
                pdf_sid=s_pdf.source_id,
            )

            profs_box[0] = load_project_profiles(svc, project_id)
            await svc.build_vault(project_id, VaultBuildReq(policy="balanced"))
            svc.migrate_target(project_id, StorageTargetReq(target_id="duckdb_local"))

            # Register metadata-only databricks_uc source referencing a secret (0 secret values stored).
            svc.state_manager.upsert_source(
                project_id,
                source_id="src_dbx_meta",
                kind="databricks_uc",
                uri_or_filename="https://adb-golden.example.azuredatabricks.net",
                binary_sha256=None,
                connector_config=ConnectorConfigPayload(
                    adapter_id="databricks_uc",
                    display_name="Unity Catalog IBOR Gold",
                    uri_or_path="https://adb-golden.example.azuredatabricks.net",
                    options={"catalog": "am_gold", "schema": "ibor"},
                    secret_refs=["DATABRICKS_TOKEN"],
                ),
                status="READY_OFFLINE",
                profile=SourceProfile(
                    source_id="src_dbx_meta",
                    schema_fingerprint="dbx_meta_fp",
                    table_count=0,
                    tables=[],
                ),
                document_map=None,
                tables_index=[],
                approved_scan_pages=[],
            )

            # Normalize local file URIs to portable POSIX paths before exporting core bundle.
            for src in svc.list_sources(project_id):
                if src.kind == "databricks_uc":
                    continue
                rec = svc.state_manager.get_source_record(project_id, src.source_id)
                rel_name = Path(src.uri_or_filename).name
                portable_rel = f"data/public_am/{rel_name}"
                cfg = (src.connector_config or ConnectorConfigPayload(adapter_id=src.kind, display_name=src.kind)).model_copy(
                    update={"uri_or_path": portable_rel}
                )
                prof_raw = rec.get("profile")
                prof = (
                    prof_raw
                    if isinstance(prof_raw, SourceProfile)
                    else SourceProfile.model_validate(prof_raw)
                )
                svc.state_manager.upsert_source(
                    project_id,
                    source_id=src.source_id,
                    kind=src.kind,
                    uri_or_filename=portable_rel,
                    binary_sha256=rec.get("binary_sha256"),
                    connector_config=cfg,
                    status=src.status,
                    profile=prof,
                    document_map=rec.get("document_map"),
                    tables_index=[
                        t if isinstance(t, TableIndexEntry) else TableIndexEntry.model_validate(t)
                        for t in (rec.get("tables_index") or [])
                    ],
                    approved_scan_pages=list(rec.get("approved_scan_pages") or []),
                )

            p4_sha = dm_pdf.pages[3].canonical_text_sha256 or ""
            svc.state_manager.append_event_or_receipt(
                project_id,
                record_kind="ocr_receipt",
                run_id=None,
                payload=OcrPageReceipt(
                    receipt_id="ocr-p4-golden",
                    doc_id=s_pdf.source_id,
                    page_number=4,
                    ocr_text_masked="Custody fee schedule table extracted cleanly.",
                    content_sha256=p4_sha,
                    ocr_box_count=1,
                ),
            )
            svc.state_manager.append_event_or_receipt(
                project_id,
                record_kind="audit_log",
                run_id=None,
                payload=AuditLogRecord(
                    event_type="GOLDEN_BUNDLE_BUILT",
                    actor="system",
                    step="step7",
                    summary="Golden Wave 1 core project bundle built from public_am.",
                    details={"ontology_version": "1.0.0", "wave": "W001"},
                ),
            )

            _, manifest = export_project_bundle(
                svc.state_manager,
                project_id,
                output_path,
                mode="core_project_state",
                created_at=FIXTURE_TIMESTAMP,
            )
            return manifest

    return asyncio.run(_run())


def create_v2_parity_fake_agy_binary(
    directory: Path,
    *,
    w1_draft: dict[str, Any],
    w2_hold_draft: dict[str, Any],
    w2_draft: dict[str, Any],
    v8_w2_first_hold: bool = True,
) -> Path:
    """Create cross-platform fake agy binary supporting W1/W2, Step 4/L3 holds, and V8 Horizon."""
    import sys

    spec_path = directory / "fake_agy_spec.json"
    state_path = directory / "fake_agy_horizon_state.json"
    spec_path.write_text(
        json.dumps(
            {
                "active_wave": 1,
                "w1_draft": w1_draft,
                "w2_hold_draft": w2_hold_draft,
                "w2_draft": w2_draft,
                "v8_w2_first_hold": v8_w2_first_hold,
            }
        ),
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps({"step4_calls": 0, "w2_ship_calls": 0, "w2_horizon_calls": 0}),
        encoding="utf-8",
    )

    py_script = directory / "fake_agy_v2_parity.py"
    py_script.write_text(
        f"""import json, os, sys
from pathlib import Path

args = sys.argv[1:]
schema = {{}}
prompt = ""
for i, a in enumerate(args):
    if a == "--json-schema" and i + 1 < len(args):
        raw_arg = args[i + 1]
        schema = json.loads(Path(raw_arg).read_text(encoding="utf-8")) if os.path.isfile(raw_arg) else json.loads(raw_arg)
    elif a.startswith("--print="):
        prompt = a[len("--print="):]

if not prompt and not sys.stdin.isatty():
    prompt = sys.stdin.read()

spec = json.loads(Path({json.dumps(str(spec_path))}).read_text(encoding="utf-8"))
state_file = Path({json.dumps(str(state_path))})
st = json.loads(state_file.read_text(encoding="utf-8"))
title = schema.get("title", "")
active_wave = int(spec.get("active_wave", 1))

if "SemanticCriticVerdict" in title or '"title": "SemanticCriticVerdict"' in prompt:
    payload = {{"score": 95, "findings": [], "accepted_waiver_ids": []}}
elif "HorizonCriticVerdict" in title or '"title": "HorizonCriticVerdict"' in prompt:
    if active_wave >= 2 and spec.get("v8_w2_first_hold") and st.get("w2_horizon_calls", 0) == 0:
        st["w2_horizon_calls"] = 1
        state_file.write_text(json.dumps(st), encoding="utf-8")
        payload = {{
            "score": 76,
            "classification": "HUMAN_REVIEW_REQUIRED",
            "absorbed_natively": [],
            "bucket_a_ops": [],
            "bucket_b_questions": [
                {{
                    "question_id": "hq_w2_horizon",
                    "brk_code": "BRK-02",
                    "title": "Confirm OTC BK longevity under EU MiFID reporting wave",
                    "prompt": "Confirm whether uti_id suffices for future EU reporting waves or needs additional qualifier.",
                    "options": [
                        {{
                            "option_id": "keep_uti_id",
                            "label": "Confirm uti_id canonical key",
                            "description": "Proceed using global UTI identifier as locked hub key.",
                            "implications": "Low risk; companion extensions absorb extra OTC attributes.",
                            "operator_hint": None
                        }},
                        {{
                            "option_id": "defer_watchlist",
                            "label": "Track on watchlist",
                            "description": "Keep current structure and note future reporting rule.",
                            "implications": "No breaking change in Wave 2.",
                            "operator_hint": None
                        }}
                    ],
                    "impact_summary": "Protects future OTC reporting evolution without raw table rewrites."
                }}
            ],
            "bucket_c_watchlist": ["Monitor ESMA UTI reporting rule updates in Wave 3."]
        }}
    else:
        payload = {{
            "score": 94,
            "classification": "REASONABLY_STABLE",
            "absorbed_natively": [],
            "bucket_a_ops": [],
            "bucket_b_questions": [],
            "bucket_c_watchlist": ["Track regulatory OTC derivative reporting."]
        }}
elif "Step4CoordinatorOutput" in title or "Step4CoordinatorOutput" in prompt:
    if st.get("step4_calls", 0) == 0:
        st["step4_calls"] = 1
        state_file.write_text(json.dumps(st), encoding="utf-8")
        payload = {{
            "proposed_ops": [],
            "clarification_questions": [
                {{
                    "question_id": "q_step4_bk",
                    "concept_name": "ProxyBallot",
                    "title": "Clarify ProxyBallot canonical key",
                    "prompt": "Confirm ballot_id as canonical ProxyBallot business key?",
                    "sample_rows": ["PRX_NVDA_2024 | proposal_seq=1"],
                    "impact_if_locked": "Binds ProxyBallot to ballot_id.",
                    "recommended_answer": "ballot_id"
                }}
            ],
            "staged_candidate_ids": [],
            "summary": "Awaiting steward confirmation on ProxyBallot key."
        }}
    else:
        payload = {{
            "proposed_ops": [],
            "clarification_questions": [],
            "staged_candidate_ids": [],
            "summary": "Constructed grounded asset management ontology concepts."
        }}
else:
    if active_wave >= 2:
        if st.get("w2_ship_calls", 0) == 0:
            st["w2_ship_calls"] = 1
            state_file.write_text(json.dumps(st), encoding="utf-8")
            payload = spec["w2_hold_draft"]
        else:
            payload = spec["w2_draft"]
    else:
        payload = spec["w1_draft"]

print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": json.dumps(payload)}}}}))
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = directory / "fake_agy.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_script}" %*\r\n', encoding="utf-8"
        )
        return wrapper
    wrapper = directory / "fake_agy.sh"
    wrapper.write_text(f'#!/usr/bin/env sh\n"{sys.executable}" "{py_script}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def load_trap_profiles_and_rows() -> tuple[list[SourceProfile], dict[str, list[dict[str, Any]]]]:
    """Load read-only SourceProfiles and row maps from real data/public_am/ds_cegar_12trap.duckdb."""
    assert TRAP_DUCKDB.exists(), f"Missing 12-trap fixture {TRAP_DUCKDB}"
    conn = duckdb.connect(str(TRAP_DUCKDB), read_only=True)
    try:
        tables: list[TableProfile] = []
        rows_map: dict[str, list[dict[str, Any]]] = {}
        t_names = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        for tname in t_names:
            cur = conn.execute(f'SELECT * FROM "{tname}"')
            col_names = [d[0] for d in (cur.description or ())]
            raw_rows = cur.fetchall()
            cols_meta = conn.execute(f"PRAGMA table_info('{tname}')").fetchall()
            tid = f"trap_src:{tname}"
            rows_map[tid] = [dict(zip(col_names, r, strict=True)) for r in raw_rows]
            col_profiles: list[ColumnProfile] = []
            for idx, meta in enumerate(cols_meta):
                _cid, cname, ctype, notnull, _, is_pk = meta
                is_pii = any(tok in str(cname) for tok in ("email", "tax_id", "phone"))
                col_profiles.append(
                    ColumnProfile(
                        col_name=str(cname),
                        sql_type=str(ctype),
                        nullable=not bool(notnull),
                        is_pk=bool(is_pk),
                        null_ratio=sum(1 for r in raw_rows if r[idx] is None)
                        / max(len(raw_rows), 1),
                        distinct_count_est=len({r[idx] for r in raw_rows}),
                        pii_tags=["PII_COLUMN"] if is_pii else [],
                        sample_values=[None if r[idx] is None else str(r[idx]) for r in raw_rows[:4]],
                    )
                )
            tables.append(
                TableProfile(
                    table_id=tid,
                    table_name=tname,
                    row_count_est=len(raw_rows),
                    columns=col_profiles,
                )
            )
        return (
            [
                SourceProfile(
                    source_id="trap_src",
                    schema_fingerprint="trap12",
                    table_count=len(tables),
                    tables=tables,
                )
            ],
            rows_map,
        )
    finally:
        conn.close()


def _ir_col(field_id: int, name: str, ltype: Any = "UTF8_STRING", *, nullable: bool = True, is_pii: bool = False) -> IRField:
    return IRField(field_id=field_id, name=name, logical_type=ltype, nullable=nullable, is_pii=is_pii)


def _std_hub(
    hub_name: str,
    hk_col: str,
    bk_col: str,
    table_id: str,
    bkcc: str,
    *,
    is_planned: bool = False,
) -> CertifiedHubSpec:
    tier: KeyTierEnum = (
        "TIER_3_LOCAL_SCOPED" if bkcc in {"CRM", "IBOR"} else "TIER_1_GLOBAL_STANDARD"
    )
    recipe = HashRecipeSpec(
        target_col=hk_col,
        recipe_kind="HUB_HK",
        bkcc_literal=bkcc,
        source_columns=[bk_col],
        sort_alphabetically=False,
        key_columns=[bk_col],
    )
    return CertifiedHubSpec(
        hub_name=hub_name,
        key_tier=tier,
        is_planned_concept=is_planned,
        business_keys=[
            BusinessKeyPart(
                table_id=table_id, col_name=bk_col, source_id="trap_src", key_tier=tier, bkcc_value=bkcc
            )
        ],
        source_bindings=[] if is_planned else [SourceBindingSpec(source_id="trap_src", table_id=table_id, col_mappings={}, hash_recipe=recipe)],
        columns=[
            _ir_col(1, hk_col, "FIXED_BINARY_32", nullable=False),
            _ir_col(2, "bkcc", nullable=False),
            _ir_col(3, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
            _ir_col(4, "rec_src", nullable=False),
            _ir_col(5, bk_col, nullable=False),
        ],
        hash_recipe=None if is_planned else recipe,
        physical_hints=None if is_planned else PhysicalLayoutHints(cluster_by_cols=[hk_col]),
    )


def build_clean_trap_ir(
    profiles: Sequence[SourceProfile] = (),
    *,
    include_tech_churn_in_hd: bool = False,
    unpromote_ballot_mas_to_sat: bool = False,
    omit_pii_isolation: bool = False,
    include_planned_hub: bool = True,
) -> CertifiedVaultIR:
    """Build clean or targeted-trap CertifiedVaultIR over ds_cegar_12trap.duckdb."""
    dec4 = DecimalType(precision=18, scale=4)
    dec2 = DecimalType(precision=18, scale=2)
    b_crm = [_ir_col(1, "account_id", nullable=False), _ir_col(2, "delimiter_part_a"), _ir_col(3, "delimiter_part_b"), _ir_col(4, "beneficial_owner_tax_id", is_pii=True), _ir_col(5, "daily_nav_usd", dec2)]
    b_ibor = [_ir_col(1, "account_id", nullable=False), _ir_col(2, "issuer_cik"), _ir_col(3, "issuer_legal_name"), _ir_col(4, "executing_broker_lei"), _ir_col(5, "prime_custodian_bic")]
    b_bal = [_ir_col(1, "ballot_id", nullable=False), _ir_col(2, "proposal_seq", "INT32", nullable=False), _ir_col(3, "vote_cast_code")]
    b_snap = [_ir_col(1, "position_id", nullable=False), _ir_col(2, "quantity", dec4), _ir_col(3, "etl_snapshot_ts", "TIMESTAMP_UTC_US")]
    b_eff = [_ir_col(1, "assignment_id", nullable=False), _ir_col(2, "account_id", nullable=False), _ir_col(3, "portfolio_manager_id", nullable=False), _ir_col(4, "effective_from", "TIMESTAMP_UTC_US", nullable=False)]
    b_trd = [_ir_col(1, "trade_id", nullable=False), _ir_col(2, "as_of_date", "DATE32", nullable=False), _ir_col(3, "applied_dts", "TIMESTAMP_UTC_US", nullable=False), _ir_col(4, "nav_per_share", dec4, nullable=False)]

    r_crm = HashRecipeSpec(target_col="hk_client_acct", recipe_kind="HUB_HK", bkcc_literal="CRM", source_columns=["account_id"], sort_alphabetically=False, key_columns=["account_id"])
    r_ibor = HashRecipeSpec(target_col="hk_ibor_acct", recipe_kind="HUB_HK", bkcc_literal="IBOR", source_columns=["account_id"], sort_alphabetically=False, key_columns=["account_id"])
    r_brk = HashRecipeSpec(target_col="hk_broker", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["executing_broker_lei"], sort_alphabetically=False, key_columns=["executing_broker_lei"])
    r_bal = HashRecipeSpec(target_col="hk_ballot", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["ballot_id"], sort_alphabetically=False, key_columns=["ballot_id"])
    r_pos = HashRecipeSpec(target_col="hk_position", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["position_id"], sort_alphabetically=False, key_columns=["position_id"])
    r_mgr = HashRecipeSpec(target_col="hk_manager", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["portfolio_manager_id"], sort_alphabetically=False, key_columns=["portfolio_manager_id"])
    r_trd = HashRecipeSpec(target_col="hk_trade", recipe_kind="HUB_HK", bkcc_literal="GLOBAL", source_columns=["trade_id"], sort_alphabetically=False, key_columns=["trade_id"])
    r_lnk = HashRecipeSpec(target_col="hk_acct_mgr", recipe_kind="LINK_HK", bkcc_literal="GLOBAL", source_columns=["account_id", "portfolio_manager_id"], sort_alphabetically=False, key_columns=["account_id", "portfolio_manager_id"])
    r_lnk_brk = HashRecipeSpec(target_col="hk_ibor_broker", recipe_kind="LINK_HK", bkcc_literal="GLOBAL", source_columns=["account_id", "executing_broker_lei"], sort_alphabetically=False, key_columns=["account_id", "executing_broker_lei"])

    hd_crm = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["daily_nav_usd", "delimiter_part_a", "delimiter_part_b"] + (["beneficial_owner_tax_id"] if omit_pii_isolation else []), sort_alphabetically=True, key_columns=["hk_client_acct"])
    hd_pii = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["beneficial_owner_tax_id"], sort_alphabetically=True, key_columns=["hk_client_acct"])
    hd_ibor = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["issuer_cik", "issuer_legal_name"], sort_alphabetically=True, key_columns=["hk_ibor_acct"])
    hd_bal = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["vote_cast_code"] if unpromote_ballot_mas_to_sat else ["proposal_seq", "vote_cast_code"], sort_alphabetically=True, key_columns=["hk_ballot"])
    hd_snap = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["etl_snapshot_ts", "quantity"] if include_tech_churn_in_hd else ["quantity"], sort_alphabetically=True, key_columns=["hk_position"])
    hd_trd = HashRecipeSpec(target_col="hash_diff", recipe_kind="SAT_HASH_DIFF", source_columns=["as_of_date", "nav_per_share"], sort_alphabetically=True, key_columns=["hk_trade"])

    hub_crm = _std_hub("hub_client_acct", "hk_client_acct", "account_id", "trap_src:trap_crm_accounts", "CRM")
    hub_crm = hub_crm.model_copy(
        update={
            "source_bindings": [
                SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_crm_accounts", col_mappings={}, hash_recipe=r_crm),
                SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_manager_eff", col_mappings={}, hash_recipe=r_crm),
            ]
        }
    )
    brk_sem = LoadSemanticsSpec(
        source_table_id="trap_src:trap_ibor_accounts",
        filter_ast=PredicateLeaf(op="IS_NOT_NULL", column="executing_broker_lei"),
    )
    hub_broker = _std_hub("hub_broker", "hk_broker", "executing_broker_lei", "trap_src:trap_ibor_accounts", "GLOBAL")
    hub_broker = hub_broker.model_copy(
        update={
            "source_bindings": [
                SourceBindingSpec(
                    source_id="trap_src",
                    table_id="trap_src:trap_ibor_accounts",
                    col_mappings={},
                    hash_recipe=r_brk,
                    load_semantics=brk_sem,
                )
            ]
        }
    )
    hubs = [
        hub_crm,
        _std_hub("hub_ibor_acct", "hk_ibor_acct", "account_id", "trap_src:trap_ibor_accounts", "IBOR"),
        _std_hub("hub_ballot", "hk_ballot", "ballot_id", "trap_src:trap_ballots_mas", "GLOBAL"),
        _std_hub("hub_position", "hk_position", "position_id", "trap_src:trap_snapshots_churn", "GLOBAL"),
        _std_hub("hub_manager", "hk_manager", "portfolio_manager_id", "trap_src:trap_manager_eff", "GLOBAL"),
        _std_hub("hub_trade", "hk_trade", "trade_id", "trap_src:trap_early_trades", "GLOBAL"),
        hub_broker,
    ]
    if include_planned_hub:
        hubs.append(_std_hub("hub_planned_clearing_house", "hk_planned_clearing_house", "clearing_code", "trap_src:trap_ibor_accounts", "GLOBAL", is_planned=True))

    sats = [
        CertifiedSatelliteSpec(
            sat_name="sat_crm_core",
            sat_kind="STANDARD",
            parent_name="hub_client_acct",
            source_id="trap_src",
            source_table_id="trap_src:trap_crm_accounts",
            attributes=[
                DraftField(name="delimiter_part_a", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="delimiter_part_b", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="daily_nav_usd", logical_type=dec2, nullable=True, is_pii=False),
                *([DraftField(name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=False)] if omit_pii_isolation else []),
            ],
            columns=[
                _ir_col(1, "hk_client_acct", "FIXED_BINARY_32", nullable=False),
                _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(3, "rec_src", nullable=False),
                _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                _ir_col(5, "delimiter_part_a"),
                _ir_col(6, "delimiter_part_b"),
                _ir_col(7, "daily_nav_usd", dec2),
                *([_ir_col(8, "beneficial_owner_tax_id")] if omit_pii_isolation else []),
            ],
            hash_recipe=hd_crm,
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_client_acct", "load_dts"]),
        ),
        *(
            []
            if omit_pii_isolation
            else [
                CertifiedSatelliteSpec(
                    sat_name="sat_crm_pii",
                    sat_kind="PII",
                    parent_name="hub_client_acct",
                    source_id="trap_src",
                    source_table_id="trap_src:trap_crm_accounts",
                    attributes=[DraftField(name="beneficial_owner_tax_id", logical_type="UTF8_STRING", nullable=True, is_pii=True)],
                    columns=[
                        _ir_col(1, "hk_client_acct", "FIXED_BINARY_32", nullable=False),
                        _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                        _ir_col(3, "rec_src", nullable=False),
                        _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                        _ir_col(5, "beneficial_owner_tax_id", is_pii=True),
                    ],
                    hash_recipe=hd_pii,
                    physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_client_acct", "load_dts"]),
                )
            ]
        ),
        CertifiedSatelliteSpec(
            sat_name="sat_ibor_core",
            sat_kind="STANDARD",
            parent_name="hub_ibor_acct",
            source_id="trap_src",
            source_table_id="trap_src:trap_ibor_accounts",
            attributes=[
                DraftField(name="issuer_cik", logical_type="UTF8_STRING", nullable=True, is_pii=False),
                DraftField(name="issuer_legal_name", logical_type="UTF8_STRING", nullable=True, is_pii=False),
            ],
            columns=[
                _ir_col(1, "hk_ibor_acct", "FIXED_BINARY_32", nullable=False),
                _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(3, "rec_src", nullable=False),
                _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                _ir_col(5, "issuer_cik"),
                _ir_col(6, "issuer_legal_name"),
            ],
            hash_recipe=hd_ibor,
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ibor_acct", "load_dts"]),
        ),
        CertifiedSatelliteSpec(
            sat_name="sat_ballot_core" if unpromote_ballot_mas_to_sat else "mas_ballots",
            sat_kind="STANDARD" if unpromote_ballot_mas_to_sat else "MAS",
            parent_name="hub_ballot",
            source_id="trap_src",
            source_table_id="trap_src:trap_ballots_mas",
            child_dependent_keys=[] if unpromote_ballot_mas_to_sat else ["proposal_seq"],
            attributes=[DraftField(name="vote_cast_code", logical_type="UTF8_STRING", nullable=True, is_pii=False)],
            columns=(
                [
                    _ir_col(1, "hk_ballot", "FIXED_BINARY_32", nullable=False),
                    _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                    _ir_col(3, "rec_src", nullable=False),
                    _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                    _ir_col(5, "vote_cast_code"),
                ]
                if unpromote_ballot_mas_to_sat
                else [
                    _ir_col(1, "hk_ballot", "FIXED_BINARY_32", nullable=False),
                    _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                    _ir_col(3, "rec_src", nullable=False),
                    _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                    _ir_col(5, "proposal_seq", "INT32", nullable=False),
                    _ir_col(6, "vote_cast_code"),
                ]
            ),
            hash_recipe=hd_bal,
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ballot", "load_dts"]),
        ),
        CertifiedSatelliteSpec(
            sat_name="sat_snap",
            sat_kind="STANDARD",
            parent_name="hub_position",
            source_id="trap_src",
            source_table_id="trap_src:trap_snapshots_churn",
            attributes=[
                DraftField(name="quantity", logical_type=dec4, nullable=True, is_pii=False),
                *([DraftField(name="etl_snapshot_ts", logical_type="TIMESTAMP_UTC_US", nullable=True, is_pii=False)] if include_tech_churn_in_hd else []),
            ],
            columns=[
                _ir_col(1, "hk_position", "FIXED_BINARY_32", nullable=False),
                _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(3, "rec_src", nullable=False),
                _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                _ir_col(5, "quantity", dec4),
                *([_ir_col(6, "etl_snapshot_ts", "TIMESTAMP_UTC_US")] if include_tech_churn_in_hd else []),
            ],
            hash_recipe=hd_snap,
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_position", "load_dts"]),
        ),
        CertifiedSatelliteSpec(
            sat_name="esat_acct_mgr",
            sat_kind="ESAT",
            parent_name="lnk_acct_mgr",
            source_id="trap_src",
            source_table_id="trap_src:trap_manager_eff",
            driving_keys=["hk_client_acct"],
            attributes=[DraftField(name="effective_from", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False)],
            columns=[
                _ir_col(1, "hk_acct_mgr", "FIXED_BINARY_32", nullable=False),
                _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(3, "rec_src", nullable=False),
                _ir_col(4, "hk_client_acct", "FIXED_BINARY_32", nullable=False),
                _ir_col(5, "effective_from", "TIMESTAMP_UTC_US", nullable=False),
            ],
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct_mgr", "load_dts"]),
        ),
        CertifiedSatelliteSpec(
            sat_name="sat_trade_bitemp",
            sat_kind="STANDARD",
            parent_name="hub_trade",
            source_id="trap_src",
            source_table_id="trap_src:trap_early_trades",
            is_bitemporal=True,
            applied_dts_col="applied_dts",
            attributes=[
                DraftField(name="as_of_date", logical_type="DATE32", nullable=False, is_pii=False),
                DraftField(name="nav_per_share", logical_type=dec4, nullable=False, is_pii=False),
            ],
            columns=[
                _ir_col(1, "hk_trade", "FIXED_BINARY_32", nullable=False),
                _ir_col(2, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(3, "rec_src", nullable=False),
                _ir_col(4, "hash_diff", "FIXED_BINARY_32", nullable=False),
                _ir_col(5, "applied_dts", "TIMESTAMP_UTC_US", nullable=False),
                _ir_col(6, "as_of_date", "DATE32", nullable=False),
                _ir_col(7, "nav_per_share", dec4, nullable=False),
            ],
            hash_recipe=hd_trd,
            physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_trade", "load_dts"]),
        ),
    ]

    triage = [
        ColumnTriageEntry(
            table_id=tbl.table_id,
            col_name=col.col_name,
            disposition="SYSTEM_METADATA" if col.col_name == "etl_snapshot_ts" else "MODELED",
            target_ref="hub_client_acct",
        )
        for p in profiles
        for tbl in p.tables
        for col in tbl.columns
    ]

    return CertifiedVaultIR(
        project_id="proj_12trap",
        ontology_version="1.0.0",
        wave_number=1,
        column_triage=triage,
        bronze_tables=[
            BronzeTableSpec(table_name="bronze_crm", source_id="trap_src", source_table_id="trap_src:trap_crm_accounts", columns=b_crm),
            BronzeTableSpec(table_name="bronze_ibor", source_id="trap_src", source_table_id="trap_src:trap_ibor_accounts", columns=b_ibor),
            BronzeTableSpec(table_name="bronze_ballots", source_id="trap_src", source_table_id="trap_src:trap_ballots_mas", columns=b_bal),
            BronzeTableSpec(table_name="bronze_snap", source_id="trap_src", source_table_id="trap_src:trap_snapshots_churn", columns=b_snap),
            BronzeTableSpec(table_name="bronze_eff", source_id="trap_src", source_table_id="trap_src:trap_manager_eff", columns=b_eff),
            BronzeTableSpec(table_name="bronze_trd", source_id="trap_src", source_table_id="trap_src:trap_early_trades", columns=b_trd),
        ],
        staging_views=[
            StagingViewSpec(view_name="stg_crm", source_id="trap_src", source_table_id="trap_src:trap_crm_accounts", col_lineage_mappings={c.name: c.name for c in b_crm}, hash_recipes=[r_crm, hd_crm, *([] if omit_pii_isolation else [hd_pii])], projected_fields=b_crm),
            StagingViewSpec(view_name="stg_ibor", source_id="trap_src", source_table_id="trap_src:trap_ibor_accounts", col_lineage_mappings={c.name: c.name for c in b_ibor}, hash_recipes=[r_ibor, r_brk, r_lnk_brk, hd_ibor], projected_fields=b_ibor),
            StagingViewSpec(view_name="stg_ballots", source_id="trap_src", source_table_id="trap_src:trap_ballots_mas", col_lineage_mappings={c.name: c.name for c in b_bal}, hash_recipes=[r_bal, hd_bal], projected_fields=b_bal),
            StagingViewSpec(view_name="stg_snap", source_id="trap_src", source_table_id="trap_src:trap_snapshots_churn", col_lineage_mappings={c.name: c.name for c in b_snap}, hash_recipes=[r_pos, hd_snap], projected_fields=b_snap),
            StagingViewSpec(view_name="stg_eff", source_id="trap_src", source_table_id="trap_src:trap_manager_eff", col_lineage_mappings={**{c.name: c.name for c in b_eff}, "load_dts": "effective_from"}, hash_recipes=[r_crm, r_mgr, r_lnk], projected_fields=b_eff),
            StagingViewSpec(view_name="stg_trd", source_id="trap_src", source_table_id="trap_src:trap_early_trades", col_lineage_mappings={c.name: c.name for c in b_trd}, hash_recipes=[r_trd, hd_trd], projected_fields=b_trd),
        ],
        hubs=hubs,
        links=[
            CertifiedLinkSpec(
                link_name="lnk_acct_mgr",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_client_acct", role_name="ACCOUNT", fk_col_name="hk_client_acct", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_manager", role_name="MANAGER", fk_col_name="hk_manager", source_col_names=["portfolio_manager_id"]),
                ],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_manager_eff", col_mappings={}, hash_recipe=r_lnk)],
                columns=[
                    _ir_col(1, "hk_acct_mgr", "FIXED_BINARY_32", nullable=False),
                    _ir_col(2, "hk_client_acct", "FIXED_BINARY_32", nullable=False),
                    _ir_col(3, "hk_manager", "FIXED_BINARY_32", nullable=False),
                    _ir_col(4, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                    _ir_col(5, "rec_src", nullable=False),
                ],
                hash_recipe=r_lnk,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_acct_mgr"]),
            ),
            CertifiedLinkSpec(
                link_name="lnk_ibor_broker",
                link_kind="STANDARD",
                participants=[
                    LinkParticipantSpec(hub_name="hub_ibor_acct", role_name="IBOR_ACCOUNT", fk_col_name="hk_ibor_acct", source_col_names=["account_id"]),
                    LinkParticipantSpec(hub_name="hub_broker", role_name="EXECUTING_BROKER", fk_col_name="hk_broker", source_col_names=["executing_broker_lei"]),
                ],
                source_bindings=[SourceBindingSpec(source_id="trap_src", table_id="trap_src:trap_ibor_accounts", col_mappings={}, hash_recipe=r_lnk_brk, load_semantics=brk_sem)],
                load_semantics=brk_sem,
                columns=[
                    _ir_col(1, "hk_ibor_broker", "FIXED_BINARY_32", nullable=False),
                    _ir_col(2, "hk_ibor_acct", "FIXED_BINARY_32", nullable=False),
                    _ir_col(3, "hk_broker", "FIXED_BINARY_32", nullable=False),
                    _ir_col(4, "load_dts", "TIMESTAMP_UTC_US", nullable=False),
                    _ir_col(5, "rec_src", nullable=False),
                ],
                hash_recipe=r_lnk_brk,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_ibor_broker"]),
            ),
        ],
        satellites=sats,
        pit_bridges=[
            PitBridgeSpec(object_name="pit_client_acct", object_kind="PIT", root_hub="hub_client_acct", target_satellites_or_links=["sat_crm_core", *([] if omit_pii_isolation else ["sat_crm_pii"])], grain_cols=["hk_client_acct", "snapshot_dts"]),
            PitBridgeSpec(object_name="pit_trade", object_kind="PIT", root_hub="hub_trade", target_satellites_or_links=["sat_trade_bitemp"], grain_cols=["hk_trade", "snapshot_dts"]),
        ],
        information_marts=[
            MartViewSpec(
                view_name="information_mart.vw_client_acct_current",
                root_entity="hub_client_acct",
                joined_entities=["sat_crm_core", *([] if omit_pii_isolation else ["sat_crm_pii"])],
                columns=[_ir_col(1, "hk_client_acct", "FIXED_BINARY_32", nullable=False), _ir_col(2, "account_id", nullable=False), _ir_col(3, "daily_nav_usd", dec2)],
                select_exprs={"hk_client_acct": "hub_client_acct.hk_client_acct", "account_id": "hub_client_acct.account_id", "daily_nav_usd": "sat_crm_core.daily_nav_usd"},
            )
        ],
    )
