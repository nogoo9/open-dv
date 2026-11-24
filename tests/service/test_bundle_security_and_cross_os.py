"""UAT-V25-SVC-05 tests for .opendv.zip security guards, cross-OS import/export, and offline V4/Step 7."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from opendv.models import (
    AuditLogRecord,
    BusinessKeyPart,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    ColumnProfile,
    ConnectorConfigPayload,
    DecimalType,
    DocumentCitationRef,
    DocumentMap,
    ErrorCode,
    ExternalMcpReceipt,
    GraphAOntology,
    IRField,
    LinkParticipantSpec,
    LoadSemanticsSpec,
    OcrPageReceipt,
    OpenDVError,
    PageMapSpec,
    PhysicalLayoutHints,
    ProjectSettingsSnapshot,
    ProjectState,
    ProjectSummary,
    RevisionMetadata,
    SecretPrecedence,
    SeedRecordsSpec,
    SourceBindingSpec,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
    TableProfile,
    sha256_jcs,
)
from opendv.service.bundles import (
    BundleSecretStorePort,
    BundleStateStorePort,
    export_project_bundle,
    import_project_bundle,
)
from opendv.service.bundles import importer as bundle_importer
from opendv.storage.local_runner import execute_readonly_query, materialize_duckdb_local
from opendv.verifier._v4_unstructured import compute_canonical_text_sha256, verify_v4_unstructured
from opendv.verifier.ports import VerifierContext
from tests.helpers.archive_builder import ArchiveFixtureBuilder


class InMemoryBundleStateStore(BundleStateStorePort):
    """Lightweight in-memory BundleStateStorePort double matching M2A contracts."""

    def __init__(self) -> None:
        self.projects: dict[str, ProjectState] = {}
        self.sources: dict[str, dict[str, dict[str, Any]]] = {}
        self.revisions: dict[str, list[dict[str, Any]]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}

    def get_project(self, project_id: str) -> ProjectState:
        if project_id not in self.projects:
            raise OpenDVError(f"Missing project {project_id}", error_code=ErrorCode.RESOURCE_NOT_FOUND)
        return self.projects[project_id]

    def list_projects(self) -> list[ProjectSummary]:
        return [
            ProjectSummary(
                project_id=p.project_id,
                domain_namespace=p.domain_namespace,
                active_ontology_rev_id=p.active_ontology_rev_id,
                active_wave_id=p.active_wave_id,
                updated_at=p.updated_at,
            )
            for p in self.projects.values()
        ]

    def create_project(
        self,
        project_id: str,
        domain_namespace: str,
        settings_snapshot: ProjectSettingsSnapshot,
    ) -> ProjectState:
        now = datetime.now(tz=UTC).isoformat()
        state = ProjectState(
            project_id=project_id,
            domain_namespace=domain_namespace,
            settings_snapshot=settings_snapshot,
            created_at=now,
            updated_at=now,
        )
        self.projects[project_id] = state
        self.sources.setdefault(project_id, {})
        self.revisions.setdefault(project_id, [])
        self.events.setdefault(project_id, [])
        return state

    def delete_project(self, project_id: str) -> None:
        self.projects.pop(project_id, None)
        self.sources.pop(project_id, None)
        self.revisions.pop(project_id, None)
        self.events.pop(project_id, None)

    def update_project_pointers(
        self,
        project_id: str,
        *,
        active_ontology_rev_id: str | None = None,
        active_wave_id: str | None = None,
        settings_snapshot: ProjectSettingsSnapshot | None = None,
    ) -> ProjectState:
        cur = self.get_project(project_id)
        upd: dict[str, Any] = {"updated_at": datetime.now(tz=UTC).isoformat()}
        if active_ontology_rev_id is not None:
            upd["active_ontology_rev_id"] = active_ontology_rev_id
        if active_wave_id is not None:
            upd["active_wave_id"] = active_wave_id
        if settings_snapshot is not None:
            upd["settings_snapshot"] = settings_snapshot
        self.projects[project_id] = cur.model_copy(update=upd)
        return self.projects[project_id]

    def upsert_source(
        self,
        project_id: str,
        *,
        source_id: str,
        kind: str,
        uri_or_filename: str,
        binary_sha256: str | None,
        connector_config: ConnectorConfigPayload,
        status: SourceStatus,
        profile: SourceProfile,
        document_map: DocumentMap | None,
        tables_index: list[TableIndexEntry],
        approved_scan_pages: list[int] | None = None,
    ) -> SourceSummary:
        now = datetime.now(tz=UTC).isoformat()
        self.sources.setdefault(project_id, {})[source_id] = {
            "source_id": source_id,
            "kind": kind,
            "uri_or_filename": uri_or_filename,
            "binary_sha256": binary_sha256,
            "connector_config": connector_config,
            "status": status,
            "profile": profile,
            "document_map": document_map,
            "tables_index": list(tables_index),
            "approved_scan_pages": list(approved_scan_pages or []),
            "attached_at": now,
        }
        cur = self.get_project(project_id)
        self.projects[project_id] = cur.model_copy(
            update={"source_count": len(self.sources[project_id])}
        )
        return SourceSummary(
            source_id=source_id,
            kind=kind,
            uri_or_filename=uri_or_filename,
            status=status,
            table_count=profile.table_count,
            page_count=document_map.page_count if document_map else None,
            attached_at=now,
            connector_config=connector_config,
        )

    def get_source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        return self.sources[project_id][source_id]

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        out: list[SourceSummary] = []
        for rec in self.sources.get(project_id, {}).values():
            prof: SourceProfile = rec["profile"]
            dm: DocumentMap | None = rec["document_map"]
            out.append(
                SourceSummary(
                    source_id=rec["source_id"],
                    kind=rec["kind"],
                    uri_or_filename=rec["uri_or_filename"],
                    status=rec["status"],
                    table_count=prof.table_count,
                    page_count=dm.page_count if dm else None,
                    attached_at=rec["attached_at"],
                    connector_config=rec["connector_config"],
                )
            )
        return out

    def save_revision(
        self,
        project_id: str,
        *,
        revision_id: str,
        rev_kind: str,
        rev_key: str,
        parent_rev_id: str | None,
        payload: BaseModel,
        metadata: RevisionMetadata,
    ) -> str:
        digest = sha256_jcs(payload)
        self.revisions.setdefault(project_id, []).append(
            {
                "revision_id": revision_id,
                "rev_kind": rev_kind,
                "rev_key": rev_key,
                "parent_rev_id": parent_rev_id,
                "content_sha256": digest,
                "payload": payload.model_dump(mode="json"),
                "metadata": metadata.model_dump(mode="json"),
                "created_at": datetime.now(tz=UTC).isoformat(),
            }
        )
        return digest

    def list_revisions(
        self, project_id: str, *, rev_kind: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.revisions.get(project_id, [])
        if rev_kind is None:
            return list(rows)
        return [r for r in rows if r["rev_kind"] == rev_kind]

    def append_event_or_receipt(
        self,
        project_id: str,
        *,
        record_kind: str,
        run_id: str | None,
        payload: BaseModel,
    ) -> int:
        rows = self.events.setdefault(project_id, [])
        seq = len(rows) + 1
        rows.append(
            {
                "seq_id": seq,
                "record_kind": record_kind,
                "run_id": run_id,
                "payload": payload.model_dump(mode="json"),
            }
        )
        return seq

    def list_events_or_receipts(
        self,
        project_id: str,
        *,
        record_kind: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.events.get(project_id, [])
        return [
            r
            for r in rows
            if (record_kind is None or r["record_kind"] == record_kind)
            and (run_id is None or r["run_id"] == run_id)
        ]


class InMemoryBundleSecretStore(BundleSecretStorePort):
    """Lightweight in-memory secret store double."""

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        _ = (project_precedence, cli_env_first)
        return self._secrets.get(key)


def _make_sample_ir(project_id: str, doc_sha: str, quote: str) -> CertifiedVaultIR:
    hints = PhysicalLayoutHints()
    return CertifiedVaultIR(
        project_id=project_id,
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[],
        staging_views=[],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_account",
                key_tier="TIER_2_ENTERPRISE_SOR",
                business_keys=[
                    BusinessKeyPart(
                        table_id="src_db:accounts",
                        col_name="account_id",
                        source_id="src_db",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="IBOR",
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="bkcc", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=3, name="account_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                    IRField(field_id=4, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=5, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                physical_hints=hints,
            )
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_doc_claim",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_account",
                        role_name="account",
                        fk_col_name="hk_account",
                        source_col_names=["account_id"],
                    )
                ],
                load_semantics=LoadSemanticsSpec(
                    source_table_id="src_doc:doc",
                    require_review_status="APPROVED",
                ),
                source_bindings=[
                    SourceBindingSpec(
                        source_id="src_doc",
                        table_id="src_doc:doc",
                        col_mappings={"account_id": "account_id"},
                        load_semantics=LoadSemanticsSpec(
                            source_table_id="src_doc:doc",
                            require_review_status="APPROVED",
                        ),
                        doc_citations=[
                            DocumentCitationRef(
                                doc_id="src_doc",
                                page_number=1,
                                verbatim_quote=quote,
                                occurrence=1,
                                char_start=0,
                                char_end=len(quote),
                                content_sha256=doc_sha,
                            )
                        ],
                    )
                ],
                columns=[
                    IRField(field_id=1, name="hk_doc_claim", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="hk_account", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=3, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                physical_hints=hints,
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_doc_claim_prov",
                sat_kind="AI_PROVENANCE_SATELLITE",
                parent_name="lnk_doc_claim",
                source_id="src_doc",
                source_table_id="src_doc:doc",
                attributes=[
                    {"name": "extractor_version", "logical_type": "UTF8_STRING", "nullable": False, "is_pii": False},
                    {"name": "prompt_or_rule_id", "logical_type": "UTF8_STRING", "nullable": False, "is_pii": False},
                    {"name": "confidence_score", "logical_type": DecimalType(precision=5, scale=4), "nullable": False, "is_pii": False},
                    {"name": "review_status", "logical_type": "UTF8_STRING", "nullable": False, "is_pii": False},
                    {"name": "citation_span", "logical_type": "JSON_DOCUMENT", "nullable": False, "is_pii": False},
                    {"name": "source_doc_id", "logical_type": "UTF8_STRING", "nullable": False, "is_pii": False},
                ],
                columns=[
                    IRField(field_id=1, name="hk_doc_claim", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=2, name="load_dts", logical_type="TIMESTAMP_UTC_US", nullable=False, is_pii=False),
                    IRField(field_id=3, name="hash_diff", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False),
                    IRField(field_id=4, name="rec_src", logical_type="UTF8_STRING", nullable=False, is_pii=False),
                ],
                physical_hints=hints,
            )
        ],
        seed_records=SeedRecordsSpec(),
    )


def _seed_project(store: InMemoryBundleStateStore, project_dir: Path, pid: str = "am-ibor-2026") -> tuple[str, str]:
    store.create_project(pid, "asset_management", ProjectSettingsSnapshot())
    quote = "Custody account voting policy"
    doc_sha = compute_canonical_text_sha256(quote)

    db_bytes = b"SQLite format 3\x00dummy"
    db_sha = hashlib.sha256(db_bytes).hexdigest()
    src_db_file = project_dir / "sources" / "src_db" / "ibor.sqlite"
    src_db_file.parent.mkdir(parents=True, exist_ok=True)
    src_db_file.write_bytes(db_bytes)

    store.upsert_source(
        pid,
        source_id="src_db",
        kind="sqlite",
        uri_or_filename="ibor.sqlite",
        binary_sha256=db_sha,
        connector_config=ConnectorConfigPayload(adapter_id="sqlite", display_name="SQLite", uri_or_path="ibor.sqlite"),
        status="READY_OFFLINE",
        profile=SourceProfile(
            source_id="src_db",
            schema_fingerprint="fp1",
            table_count=1,
            tables=[
                TableProfile(
                    table_id="src_db:accounts",
                    table_name="accounts",
                    row_count_est=10,
                    columns=[
                        ColumnProfile(
                            col_name="account_id",
                            sql_type="VARCHAR",
                            nullable=False,
                            is_pk=True,
                            null_ratio=0.0,
                            distinct_count_est=10,
                            sample_values=["ACC-1"],
                        )
                    ],
                )
            ],
        ),
        document_map=None,
        tables_index=[
            TableIndexEntry(
                table_id="src_db:accounts",
                source_id="src_db",
                table_name="accounts",
                cluster_id="cluster_00",
                col_count=1,
                pii_col_count=0,
                pk_cols=["account_id"],
                badge="NEW",
            )
        ],
    )
    store.upsert_source(
        pid,
        source_id="src_doc",
        kind="pdf",
        uri_or_filename="guide.pdf",
        binary_sha256="deadbeef" * 8,
        connector_config=ConnectorConfigPayload(adapter_id="pdf", display_name="PDF", uri_or_path="guide.pdf"),
        status="READY_OFFLINE",
        profile=SourceProfile(source_id="src_doc", schema_fingerprint="fp2", table_count=0, tables=[]),
        document_map=DocumentMap(
            doc_id="src_doc",
            filename="guide.pdf",
            format="pdf",
            page_count=1,
            approved_scan_pages=[1],
            pages=[
                PageMapSpec(
                    page_number=1,
                    char_count=len(quote),
                    has_images=False,
                    is_scanned_or_low_density=False,
                    ocr_box_count=0,
                    canonical_text_sha256=doc_sha,
                )
            ],
        ),
        tables_index=[],
        approved_scan_pages=[1],
    )
    store.upsert_source(
        pid,
        source_id="src_dbx",
        kind="databricks_uc",
        uri_or_filename="dbc://host",
        binary_sha256=None,
        connector_config=ConnectorConfigPayload(
            adapter_id="databricks_uc",
            display_name="Databricks",
            uri_or_path="https://dbc.example.com",
            secret_refs=["DATABRICKS_TOKEN"],
        ),
        status="READY_OFFLINE",
        profile=SourceProfile(source_id="src_dbx", schema_fingerprint="fp3", table_count=0, tables=[]),
        document_map=None,
        tables_index=[],
    )
    ir = _make_sample_ir(pid, doc_sha, quote)
    ont = GraphAOntology(ontology_version="1.0.0", domain_namespace="asset_management")
    store.save_revision(
        pid,
        revision_id="rev-1",
        rev_kind="ontology_version",
        rev_key="v1.0.0",
        parent_rev_id=None,
        payload=ont,
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    store.save_revision(
        pid,
        revision_id="wave-1",
        rev_kind="wave_ir",
        rev_key="W001",
        parent_rev_id=None,
        payload=ir,
        metadata=RevisionMetadata(actor="agent", reason="certify"),
    )
    store.update_project_pointers(pid, active_ontology_rev_id="rev-1", active_wave_id="wave-1")
    store.append_event_or_receipt(
        pid,
        record_kind="mcp_receipt",
        run_id="r1",
        payload=ExternalMcpReceipt(
            receipt_id="mcp-r1",
            server_id="ext_doc",
            tool_name="read_clause",
            doc_id="src_doc",
            page_number=1,
            args_sha256="a" * 64,
            content_sha256="b" * 64,
            masked_excerpt="Clean excerpt",
        ),
    )
    store.append_event_or_receipt(
        pid,
        record_kind="ocr_receipt",
        run_id="r1",
        payload=OcrPageReceipt(
            receipt_id="ocr-r1",
            doc_id="src_doc",
            page_number=1,
            ocr_text_masked=quote,
            content_sha256=doc_sha,
            ocr_box_count=3,
        ),
    )
    store.append_event_or_receipt(
        pid,
        record_kind="audit_log",
        run_id=None,
        payload=AuditLogRecord(event_type="INIT", actor="human", step="step1", summary="Created"),
    )
    return doc_sha, quote


@pytest.mark.parametrize(
    ("attack_name", "expected_code"),
    [
        ("zip_slip", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("backslash", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("unc", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("drive_prefix", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("leading_slash", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("ads", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("control_char", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("symlink", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("reparse_linux_sys", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("reparse_win_sys", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("device_node", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("reserved_name", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("trailing_dot_space", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("short_name_8_3", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("nfc_casefold", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("entry_overflow", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("zip_bomb_ratio", ErrorCode.ARCHIVE_SECURITY_VIOLATION),
        ("size_overflow_250mb", ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED),
        ("hash_tamper", ErrorCode.BUNDLE_HASH_MISMATCH),
    ],
)
def test_malicious_archives_fail_with_zero_extracted_files(
    tmp_path: Path, attack_name: str, expected_code: ErrorCode
) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    store = InMemoryBundleStateStore()
    secrets = InMemoryBundleSecretStore()

    if attack_name == "zip_slip":
        zpath = builder.build_custom_zipinfo_attack("slip.zip", member_name="../evil.txt")
    elif attack_name == "backslash":
        zpath = builder.build_custom_zipinfo_attack("bs.zip", member_name="sub\\evil.txt")
    elif attack_name == "unc":
        zpath = builder.build_custom_zipinfo_attack("unc.zip", member_name="//srv/share/x.txt")
    elif attack_name == "drive_prefix":
        zpath = builder.build_custom_zipinfo_attack("drv.zip", member_name="C:evil.txt")
    elif attack_name == "leading_slash":
        zpath = builder.build_custom_zipinfo_attack("abs.zip", member_name="/etc/passwd")
    elif attack_name == "ads":
        zpath = builder.build_custom_zipinfo_attack("ads.zip", member_name="a.txt:Zone.Identifier")
    elif attack_name == "control_char":
        zpath = builder.build_custom_zipinfo_attack("ctrl.zip", member_name="bad\x07.txt")
    elif attack_name == "symlink":
        zpath = builder.build_symlink_attack()
    elif attack_name == "reparse_linux_sys":
        zpath = builder.build_reparse_attack("rep_linux.zip", create_system=3)
    elif attack_name == "reparse_win_sys":
        zpath = builder.build_reparse_attack("rep_win.zip", create_system=0)
    elif attack_name == "device_node":
        zpath = builder.build_device_node_attack()
    elif attack_name == "reserved_name":
        zpath = builder.build_custom_zipinfo_attack("res.zip", member_name="sources/con.txt")
    elif attack_name == "trailing_dot_space":
        zpath = builder.build_custom_zipinfo_attack("trail.zip", member_name="sources/bad. ")
    elif attack_name == "short_name_8_3":
        zpath = builder.build_custom_zipinfo_attack("short.zip", member_name="sources/PROGRA~1.txt")
    elif attack_name == "nfc_casefold":
        zpath = builder.build_nfc_casefold_collision()
    elif attack_name == "entry_overflow":
        zpath = builder.build_entry_count_overflow()
    elif attack_name == "zip_bomb_ratio":
        zpath = builder.build_zip_bomb_ratio()
    elif attack_name == "size_overflow_250mb":
        zpath = builder.build_size_overflow_250mb()
    else:
        zpath = builder.build_hash_tamper()

    with pytest.raises(OpenDVError) as exc_info:
        import_project_bundle(store, secrets, zpath, projects_root=projects_root)

    assert exc_info.value.error_code == expected_code
    assert list(projects_root.iterdir()) == []
    assert store.list_projects() == []


def test_core_bundle_roundtrip_offline_v4_and_step7_zero_row(tmp_path: Path) -> None:
    src_store = InMemoryBundleStateStore()
    src_proj_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    doc_sha, quote = _seed_project(src_store, src_proj_dir)

    out_zip = tmp_path / "exports" / "am-ibor-2026.core.opendv.zip"
    _, manifest = export_project_bundle(
        src_store,
        "am-ibor-2026",
        out_zip,
        mode="core_project_state",
        project_dir=src_proj_dir,
    )
    assert out_zip.stat().st_size < 50_000
    assert "external_mcp_receipts.jsonl" in manifest.entries
    assert "ocr_receipts.jsonl" in manifest.entries
    assert "vault_waivers.json" in manifest.entries
    assert "versions/v1.0.0.json" in manifest.entries
    assert "waves/W001.json" in manifest.entries
    assert all(not name.endswith((".sqlite", ".duckdb")) for name in manifest.entries)

    dst_store = InMemoryBundleStateStore()
    dst_secrets = InMemoryBundleSecretStore(secrets={})  # Missing DATABRICKS_TOKEN
    dst_projects_root = tmp_path / "dst_ws" / "projects"
    report = import_project_bundle(dst_store, dst_secrets, out_zip, projects_root=dst_projects_root)

    assert report.project_id == "am-ibor-2026"
    assert report.resolved_conflict == "none"
    assert set(report.readiness_flags) == {
        "READY_OFFLINE",
        "NEEDS_LOCAL_SECRET_BINDING",
        "METADATA_ONLY_BINARY_ABSENT",
    }
    assert report.missing_secret_refs == ["DATABRICKS_TOKEN"]
    assert "src_db" in report.missing_source_ids
    assert "mcp-r1" in report.unverified_receipt_ids
    assert "producer-asserted" in report.audit_claim_notice

    # Step 6 V4 stored-digest verification succeeds offline when metadata_only_binary_absent=True
    wave_revs = dst_store.list_revisions("am-ibor-2026", rev_kind="wave_ir")
    imported_ir = CertifiedVaultIR.model_validate(wave_revs[0]["payload"])
    src_doc_rec = dst_store.get_source_record("am-ibor-2026", "src_doc")
    dm: DocumentMap = src_doc_rec["document_map"]
    # Verify V4 stored-digest pass (citation has content_sha256 matching DocumentMap)
    v4_diags = verify_v4_unstructured(
        imported_ir,
        VerifierContext(
            ontology=GraphAOntology(ontology_version="1.0.0", domain_namespace="asset_management"),
            document_maps=[dm],
            metadata_only_binary_absent=True,
        ),
    )
    assert v4_diags == []

    # Step 7 zero-row DDL succeeds with metadata_only=True, while metadata_only=False fails absent binary
    step7_dir = dst_projects_root / "am-ibor-2026" / "targets" / "duckdb_local"
    mig_resp = materialize_duckdb_local(imported_ir, step7_dir, metadata_only=True)
    assert mig_resp.verified is True
    assert mig_resp.rows_loaded == 0
    assert (step7_dir / "am-ibor-2026.duckdb").is_file()

    from opendv.storage.local_runner import SourceAttachment

    with pytest.raises(OpenDVError) as exc_absent:
        materialize_duckdb_local(
            imported_ir,
            step7_dir,
            attachments=[
                SourceAttachment(
                    source_id="src_db",
                    kind="sqlite",
                    path=dst_projects_root / "am-ibor-2026" / "sources" / "src_db" / "ibor.sqlite",
                )
            ],
            metadata_only=False,
        )
    assert exc_absent.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT


def test_full_demo_zip_stored_conflict_fail_rename_replace(tmp_path: Path) -> None:
    store = InMemoryBundleStateStore()
    proj_dir = tmp_path / "ws1" / "projects" / "am-ibor-2026"
    _seed_project(store, proj_dir)

    wave_ir = CertifiedVaultIR.model_validate(
        store.list_revisions("am-ibor-2026", rev_kind="wave_ir")[0]["payload"]
    )
    duck_dir = proj_dir / "targets" / "duckdb_local"
    materialize_duckdb_local(wave_ir, duck_dir, metadata_only=True)

    full_zip = tmp_path / "am-ibor-2026.full_demo.opendv.zip"
    export_project_bundle(
        store,
        "am-ibor-2026",
        full_zip,
        mode="full_demo",
        project_dir=proj_dir,
    )
    with zipfile.ZipFile(full_zip, "r") as zf:
        db_info = zf.getinfo("targets/duckdb_local/am-ibor-2026.duckdb")
        assert db_info.compress_type == zipfile.ZIP_STORED

    dst_store = InMemoryBundleStateStore()
    dst_secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})
    dst_root = tmp_path / "ws2" / "projects"

    rep1 = import_project_bundle(dst_store, dst_secrets, full_zip, projects_root=dst_root)
    assert rep1.project_id == "am-ibor-2026"
    assert rep1.missing_secret_refs == []

    # conflict=fail raises PROJECT_ID_CONFLICT
    with pytest.raises(OpenDVError) as exc_conf:
        import_project_bundle(
            dst_store, dst_secrets, full_zip, projects_root=dst_root, conflict="fail"
        )
    assert exc_conf.value.error_code == ErrorCode.PROJECT_ID_CONFLICT

    # conflict=rename renames project_id, .duckdb filename, and CertifiedVaultIR.project_id
    rep_ren = import_project_bundle(
        dst_store, dst_secrets, full_zip, projects_root=dst_root, conflict="rename"
    )
    assert rep_ren.project_id == "am-ibor-2026-copy"
    assert rep_ren.resolved_conflict == "renamed"
    renamed_db = dst_root / "am-ibor-2026-copy" / "targets" / "duckdb_local" / "am-ibor-2026-copy.duckdb"
    assert renamed_db.is_file()
    q_res = execute_readonly_query(renamed_db, "SELECT COUNT(*) AS cnt FROM raw_vault.hub_account")
    assert q_res.row_count == 1
    renamed_ir = CertifiedVaultIR.model_validate(
        dst_store.list_revisions("am-ibor-2026-copy", rev_kind="wave_ir")[0]["payload"]
    )
    assert renamed_ir.project_id == "am-ibor-2026-copy"

    # conflict=replace checks active_job_lease -> PROJECT_JOB_ACTIVE
    dst_store.projects["am-ibor-2026"] = dst_store.projects["am-ibor-2026"].model_copy(
        update={"has_active_lease": True}
    )
    with pytest.raises(OpenDVError) as exc_active:
        import_project_bundle(
            dst_store, dst_secrets, full_zip, projects_root=dst_root, conflict="replace"
        )
    assert exc_active.value.error_code == ErrorCode.PROJECT_JOB_ACTIVE

    dst_store.projects["am-ibor-2026"] = dst_store.projects["am-ibor-2026"].model_copy(
        update={"has_active_lease": False}
    )
    rep_rep = import_project_bundle(
        dst_store, dst_secrets, full_zip, projects_root=dst_root, conflict="replace"
    )
    assert rep_rep.resolved_conflict == "replaced"


@pytest.mark.parametrize(
    "nul_member_name",
    [
        "sources/bad\x00.txt",
        "sources/bad\x00../../evil.txt",
    ],
)
def test_nul_byte_orig_filename_smuggling_rejected_with_zero_files(
    tmp_path: Path, nul_member_name: str
) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    entries = builder._base_core_entries()
    payload = b"smuggled\n"
    manifest_entries: dict[str, Any] = {}
    zpath = tmp_path / "fixtures" / "nul_smuggle.opendv.zip"

    from opendv.models.json_hasher import dumps_jcs_bytes
    from opendv.models.project import BundleEntryDigest, BundleManifest

    with zipfile.ZipFile(zpath, "w") as zf:
        for name, raw in sorted(entries.items()):
            zf.writestr(name, raw, compress_type=zipfile.ZIP_DEFLATED)
            manifest_entries[name] = BundleEntryDigest(
                sha256=hashlib.sha256(raw).hexdigest(),
                uncompressed_bytes=len(raw),
            )
        # Embed raw NUL in ZipInfo.filename so ZipFile("r") truncates filename to "sources/bad"
        # while retaining full orig_filename=nul_member_name
        nul_info = zipfile.ZipInfo("sources/bad")
        nul_info.filename = nul_member_name
        zf.writestr(nul_info, payload, compress_type=zipfile.ZIP_STORED)
        manifest_entries["sources/bad"] = BundleEntryDigest(
            sha256=hashlib.sha256(payload).hexdigest(),
            uncompressed_bytes=len(payload),
        )
        manifest = BundleManifest(
            bundle_version="2.0",
            mode="core_project_state",
            project_id="am-ibor-2026",
            created_at=datetime.now(tz=UTC).isoformat(),
            source_os="posix",
            entries=manifest_entries,
        )
        zf.writestr(
            "manifest.json",
            dumps_jcs_bytes(manifest.model_dump(mode="json")) + b"\n",
            compress_type=zipfile.ZIP_DEFLATED,
        )

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    store = InMemoryBundleStateStore()
    secrets = InMemoryBundleSecretStore()

    with pytest.raises(OpenDVError) as exc_nul:
        import_project_bundle(store, secrets, zpath, projects_root=projects_root)
    assert exc_nul.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert list(projects_root.iterdir()) == []
    assert store.list_projects() == []


def test_malformed_revision_prevalidation_and_rollback_leave_zero_orphans(tmp_path: Path) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    bad_entries = builder._base_core_entries(
        extra_entries={
            "versions/v1.0.0.json": json.dumps({"rev_key": "v1.0.0", "payload": {"bad": 1}}).encode(
                "utf-8"
            ),
            "sources/src1/data.bin": b"binary-data",
        }
    )
    bad_zip = builder.build_valid_bundle("bad_rev.opendv.zip", entries=bad_entries)

    # Fresh workspace: fails closed with ARCHIVE_SECURITY_VIOLATION and 0 extracted files
    fresh_root = tmp_path / "fresh_projects"
    store = InMemoryBundleStateStore()
    secrets = InMemoryBundleSecretStore()
    with pytest.raises(OpenDVError) as exc_bad:
        import_project_bundle(store, secrets, bad_zip, projects_root=fresh_root)
    assert exc_bad.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert not fresh_root.exists() or list(fresh_root.iterdir()) == []
    assert store.list_projects() == []

    # Existing project with conflict="replace": must NOT delete existing project on invalid revision
    existing_dir = fresh_root / "am-ibor-2026" / "sources" / "keep"
    existing_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = existing_dir / "keep.txt"
    sentinel_file.write_text("do-not-delete", encoding="utf-8")
    store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())

    with pytest.raises(OpenDVError) as exc_rep_bad:
        import_project_bundle(
            store, secrets, bad_zip, projects_root=fresh_root, conflict="replace"
        )
    assert exc_rep_bad.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert store.get_project("am-ibor-2026").project_id == "am-ibor-2026"
    assert sentinel_file.read_text(encoding="utf-8") == "do-not-delete"

    # Persistence failure rollback leaves zero orphan project or directory
    good_src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "good_ws" / "projects" / "am-ibor-2026"
    _seed_project(good_src_store, src_dir)
    good_zip = tmp_path / "good_full.opendv.zip"
    export_project_bundle(
        good_src_store, "am-ibor-2026", good_zip, mode="full_demo", project_dir=src_dir
    )

    class _FailingSaveRevStore(InMemoryBundleStateStore):
        def save_revision(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("injected DB write failure")

    failing_store = _FailingSaveRevStore()
    rollback_root = tmp_path / "rollback_projects"
    with pytest.raises(RuntimeError, match="injected DB write failure"):
        import_project_bundle(failing_store, secrets, good_zip, projects_root=rollback_root)
    assert failing_store.list_projects() == []
    assert not (rollback_root / "am-ibor-2026").exists()


def test_demo_vault_duckdb_fallback_rename_on_conflict_rename(tmp_path: Path) -> None:
    from opendv.models.project import TargetManifest

    store = InMemoryBundleStateStore()
    proj_dir = tmp_path / "ws_demo" / "projects" / "am-ibor-2026"
    _seed_project(store, proj_dir)
    duck_dir = proj_dir / "targets" / "duckdb_local"
    duck_dir.mkdir(parents=True, exist_ok=True)
    demo_db = duck_dir / "demo_vault.duckdb"
    demo_db.write_bytes(b"duckdb-demo-bytes")
    tm = TargetManifest(
        target_id="duckdb_local",
        last_compiled_wave=1,
        verification_tier="RUNTIME_VERIFIED",
        certification_status="CERTIFIED",
        generated_files=["demo_vault.duckdb"],
    )
    (duck_dir / "target_manifest.json").write_text(tm.model_dump_json(indent=2), encoding="utf-8")

    bundle_zip = tmp_path / "demo_fallback.full_demo.opendv.zip"
    _, manifest = export_project_bundle(
        store, "am-ibor-2026", bundle_zip, mode="full_demo", project_dir=proj_dir
    )
    assert "targets/duckdb_local/demo_vault.duckdb" in manifest.entries

    dst_store = InMemoryBundleStateStore()
    dst_store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    dst_secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})
    dst_root = tmp_path / "dst_demo" / "projects"
    report = import_project_bundle(
        dst_store, dst_secrets, bundle_zip, projects_root=dst_root, conflict="rename"
    )
    assert report.project_id == "am-ibor-2026-copy"
    renamed_duck_dir = dst_root / "am-ibor-2026-copy" / "targets" / "duckdb_local"
    assert (renamed_duck_dir / "am-ibor-2026-copy.duckdb").is_file()
    assert not (renamed_duck_dir / "demo_vault.duckdb").exists()
    updated_tm = TargetManifest.model_validate_json(
        (renamed_duck_dir / "target_manifest.json").read_text(encoding="utf-8")
    )
    assert "am-ibor-2026-copy.duckdb" in updated_tm.generated_files


@pytest.mark.parametrize(
    ("entry_name", "entry_payload"),
    [
        ("source_digests.json", b'["not-a-dict"]\n'),
        ("source_digests.json", b'{"src_db": ["not-a-dict"]}\n'),
        ("source_digests.json", b'{"src_db": {"approved_scan_pages": 123}}\n'),
        ("source_digests.json", b'{"src_db": {"approved_scan_pages": [true]}}\n'),
        ("source_digests.json", b'{"src_db": {"binary_sha256": 123}}\n'),
        ("profile.json", b"null\n"),
        ("profile.json", b"123\n"),
        ("tables_index.json", b"null\n"),
        ("tables_index.json", b"123\n"),
        ("document_map.json", b"null\n"),
        ("document_map.json", b"123\n"),
    ],
)
def test_malformed_source_digests_and_list_entries_preserve_existing_on_replace(
    tmp_path: Path, entry_name: str, entry_payload: bytes
) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    src = SourceSummary(
        source_id="src_db",
        kind="sqlite",
        uri_or_filename="ibor.sqlite",
        status="READY_OFFLINE",
        table_count=1,
        attached_at=datetime.now(tz=UTC).isoformat(),
    )
    bad_entries = builder._base_core_entries(
        sources=[src],
        extra_entries={
            entry_name: entry_payload,
            "sources/src_db/ibor.sqlite": b"SQLite format 3\x00dummy",
        },
    )
    bad_zip = builder.build_valid_bundle("malformed_meta.opendv.zip", entries=bad_entries)

    projects_root = tmp_path / "projects"
    existing_dir = projects_root / "am-ibor-2026" / "sources" / "keep"
    existing_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = existing_dir / "keep.txt"
    sentinel_file.write_text("do-not-delete", encoding="utf-8")

    store = InMemoryBundleStateStore()
    store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    secrets = InMemoryBundleSecretStore()

    with pytest.raises(OpenDVError) as exc_info:
        import_project_bundle(
            store, secrets, bad_zip, projects_root=projects_root, conflict="replace"
        )
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert store.get_project("am-ibor-2026").project_id == "am-ibor-2026"
    assert sentinel_file.read_text(encoding="utf-8") == "do-not-delete"
    assert [p.name for p in projects_root.iterdir()] == ["am-ibor-2026"]


def test_conflict_replace_mid_db_failure_restores_disk_sentinel(tmp_path: Path) -> None:
    good_src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    _seed_project(good_src_store, src_dir)
    good_zip = tmp_path / "good_replace.opendv.zip"
    export_project_bundle(
        good_src_store, "am-ibor-2026", good_zip, mode="full_demo", project_dir=src_dir
    )

    class _FailingSaveRevStore(InMemoryBundleStateStore):
        def save_revision(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("injected DB write failure on replace")

    failing_store = _FailingSaveRevStore()
    failing_store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    projects_root = tmp_path / "dst_ws" / "projects"
    existing_dir = projects_root / "am-ibor-2026" / "sources" / "keep"
    existing_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = existing_dir / "keep.txt"
    sentinel_file.write_text("do-not-delete", encoding="utf-8")

    secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})
    with pytest.raises(RuntimeError, match="injected DB write failure on replace"):
        import_project_bundle(
            failing_store, secrets, good_zip, projects_root=projects_root, conflict="replace"
        )

    assert sentinel_file.is_file()
    assert sentinel_file.read_text(encoding="utf-8") == "do-not-delete"
    assert not (projects_root / "am-ibor-2026" / "sources" / "src_db").exists()
    assert [p.name for p in projects_root.iterdir()] == ["am-ibor-2026"]
    assert failing_store.list_projects() == []


def _create_dir_link(link_path: Path, target_path: Path) -> None:
    """Create a directory symlink (POSIX) or unprivileged NTFS junction (Windows)."""
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        os.symlink(target_path, link_path, target_is_directory=True)


def _remove_dir_link(link_path: Path) -> None:
    if not os.path.lexists(link_path):
        return
    if sys.platform == "win32" and not link_path.is_symlink():
        os.rmdir(link_path)
    else:
        link_path.unlink()


@pytest.mark.parametrize(
    ("bundle_mode", "conflict", "broken_link"),
    [
        ("core_project_state", "fail", False),
        ("core_project_state", "fail", True),
        ("full_demo", "fail", False),
        ("full_demo", "fail", True),
        ("core_project_state", "replace", True),
        ("full_demo", "replace", True),
    ],
)
def test_preexisting_live_and_broken_dir_links_rejected_across_conflicts_and_modes(
    tmp_path: Path,
    bundle_mode: Literal["core_project_state", "full_demo"],
    conflict: Literal["fail", "replace"],
    broken_link: bool,
) -> None:
    src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    _seed_project(src_store, src_dir)
    bundle_zip = tmp_path / f"link_probe_{bundle_mode}.opendv.zip"
    export_project_bundle(
        src_store, "am-ibor-2026", bundle_zip, mode=bundle_mode, project_dir=src_dir
    )

    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_sentinel = outside_dir / "outside.txt"
    outside_sentinel.write_text("outside", encoding="utf-8")

    projects_root = tmp_path / "dst_ws" / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    target_link = projects_root / "am-ibor-2026"
    _create_dir_link(target_link, outside_dir)
    if broken_link:
        shutil.rmtree(outside_dir)
        assert not target_link.exists()
        assert os.path.lexists(target_link)

    dst_store = InMemoryBundleStateStore()
    if conflict == "replace":
        dst_store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})

    try:
        with pytest.raises(OpenDVError) as exc_link:
            import_project_bundle(
                dst_store, secrets, bundle_zip, projects_root=projects_root, conflict=conflict
            )
        assert exc_link.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        if not broken_link:
            assert outside_sentinel.read_text(encoding="utf-8") == "outside"
        if conflict == "fail":
            assert dst_store.list_projects() == []
        else:
            assert dst_store.get_project("am-ibor-2026").project_id == "am-ibor-2026"
        assert [p.name for p in projects_root.iterdir()] == ["am-ibor-2026"]
    finally:
        _remove_dir_link(target_link)


@pytest.mark.parametrize(
    ("jsonl_name", "jsonl_payload"),
    [
        ("audit.jsonl", b"123\n"),
        ("audit.jsonl", b"null\n"),
        ("audit.jsonl", b"[]\n"),
        ("audit.jsonl", b'"str"\n'),
        ("external_mcp_receipts.jsonl", b"123\n"),
        ("external_mcp_receipts.jsonl", b"null\n"),
        ("external_mcp_receipts.jsonl", b"[]\n"),
        ("ocr_receipts.jsonl", b"123\n"),
    ],
)
def test_non_dict_jsonl_lines_rejected_with_archive_security_violation(
    tmp_path: Path, jsonl_name: str, jsonl_payload: bytes
) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    bad_entries = builder._base_core_entries(extra_entries={jsonl_name: jsonl_payload})
    bad_zip = builder.build_valid_bundle("bad_jsonl.opendv.zip", entries=bad_entries)

    projects_root = tmp_path / "projects"
    existing_dir = projects_root / "am-ibor-2026" / "sources" / "keep"
    existing_dir.mkdir(parents=True, exist_ok=True)
    sentinel = existing_dir / "keep.txt"
    sentinel.write_text("do-not-delete", encoding="utf-8")

    store = InMemoryBundleStateStore()
    store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    secrets = InMemoryBundleSecretStore()

    with pytest.raises(OpenDVError) as exc_jsonl:
        import_project_bundle(store, secrets, bad_zip, projects_root=projects_root, conflict="replace")
    assert exc_jsonl.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert sentinel.read_text(encoding="utf-8") == "do-not-delete"
    assert [p.name for p in projects_root.iterdir()] == ["am-ibor-2026"]


@pytest.mark.parametrize("bundle_mode", ["core_project_state", "full_demo"])
def test_preexisting_live_backup_dir_junction_rejected_without_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bundle_mode: Literal["core_project_state", "full_demo"],
) -> None:
    src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    _seed_project(src_store, src_dir)
    bundle_zip = tmp_path / f"backup_link_{bundle_mode}.opendv.zip"
    export_project_bundle(
        src_store, "am-ibor-2026", bundle_zip, mode=bundle_mode, project_dir=src_dir
    )

    outside_dir = tmp_path / "outside_backup_target"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_sentinel = outside_dir / "outside.txt"
    outside_sentinel.write_text("outside", encoding="utf-8")

    projects_root = tmp_path / "dst_ws" / "projects"
    existing_dir = projects_root / "am-ibor-2026" / "sources" / "keep"
    existing_dir.mkdir(parents=True, exist_ok=True)
    sentinel = existing_dir / "keep.txt"
    sentinel.write_text("do-not-delete", encoding="utf-8")

    backup_link = projects_root / ".backup-replace-deadbeefcafe"
    _create_dir_link(backup_link, outside_dir)

    dst_store = InMemoryBundleStateStore()
    dst_store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})

    uuids = iter(
        [
            uuid.UUID("11111111-1111-4000-8000-000000000000"),
            uuid.UUID("deadbeef-cafe-4000-8000-000000000000"),
        ]
    )
    monkeypatch.setattr(bundle_importer.uuid, "uuid4", lambda: next(uuids))

    try:
        with pytest.raises(OpenDVError) as exc_backup:
            import_project_bundle(
                dst_store, secrets, bundle_zip, projects_root=projects_root, conflict="replace"
            )
        assert exc_backup.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert sentinel.read_text(encoding="utf-8") == "do-not-delete"
        assert outside_sentinel.read_text(encoding="utf-8") == "outside"
        assert dst_store.get_project("am-ibor-2026").project_id == "am-ibor-2026"
        assert sorted(p.name for p in projects_root.iterdir()) == [
            ".backup-replace-deadbeefcafe",
            "am-ibor-2026",
        ]
    finally:
        _remove_dir_link(backup_link)


@pytest.mark.parametrize("bundle_mode", ["core_project_state", "full_demo"])
def test_nonexistent_projects_root_under_link_fails_without_creating_dir(
    tmp_path: Path,
    bundle_mode: Literal["core_project_state", "full_demo"],
) -> None:
    src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    _seed_project(src_store, src_dir)
    bundle_zip = tmp_path / f"mkdir_link_{bundle_mode}.opendv.zip"
    export_project_bundle(
        src_store, "am-ibor-2026", bundle_zip, mode=bundle_mode, project_dir=src_dir
    )

    outside_dir = tmp_path / "outside_mkdir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    ws_link = tmp_path / "ws_link"
    _create_dir_link(ws_link, outside_dir)
    projects_root = ws_link / "projects"

    dst_store = InMemoryBundleStateStore()
    secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})

    try:
        with pytest.raises(OpenDVError) as exc_info:
            import_project_bundle(dst_store, secrets, bundle_zip, projects_root=projects_root)
        assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
        assert not (outside_dir / "projects").exists()
        assert dst_store.list_projects() == []
    finally:
        _remove_dir_link(ws_link)


@pytest.mark.parametrize(
    ("bundle_mode", "conflict"),
    [
        ("core_project_state", "fail"),
        ("full_demo", "fail"),
        ("core_project_state", "replace"),
        ("full_demo", "replace"),
    ],
)
def test_target_project_dir_preexisting_regular_file_rejected(
    tmp_path: Path,
    bundle_mode: Literal["core_project_state", "full_demo"],
    conflict: Literal["fail", "replace"],
) -> None:
    src_store = InMemoryBundleStateStore()
    src_dir = tmp_path / "src_ws" / "projects" / "am-ibor-2026"
    _seed_project(src_store, src_dir)
    bundle_zip = tmp_path / f"file_target_{bundle_mode}.opendv.zip"
    export_project_bundle(
        src_store, "am-ibor-2026", bundle_zip, mode=bundle_mode, project_dir=src_dir
    )

    projects_root = tmp_path / "dst_ws" / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    target_file = projects_root / "am-ibor-2026"
    target_file.write_text("keep-file", encoding="utf-8")

    dst_store = InMemoryBundleStateStore()
    if conflict == "replace":
        dst_store.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    secrets = InMemoryBundleSecretStore({"DATABRICKS_TOKEN": "tok-123"})

    with pytest.raises(OpenDVError) as exc_info:
        import_project_bundle(
            dst_store, secrets, bundle_zip, projects_root=projects_root, conflict=conflict
        )
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert target_file.read_text(encoding="utf-8") == "keep-file"
    assert [p.name for p in projects_root.iterdir()] == ["am-ibor-2026"]


@pytest.mark.parametrize(
    "bad_member",
    [
        "sources/src1/bad<name>.txt",
        "sources/src1/bad>name.txt",
        'sources/src1/bad"name.txt',
        "sources/src1/bad|name.txt",
        "sources/src1/bad?name.txt",
        "sources/src1/bad*name.txt",
    ],
)
def test_windows_illegal_filename_chars_rejected_with_zero_extracted_files(
    tmp_path: Path, bad_member: str
) -> None:
    builder = ArchiveFixtureBuilder(tmp_path / "fixtures")
    zpath = builder.build_custom_zipinfo_attack("win_bad_char.opendv.zip", member_name=bad_member)
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    store = InMemoryBundleStateStore()
    secrets = InMemoryBundleSecretStore()

    with pytest.raises(OpenDVError) as exc_info:
        import_project_bundle(store, secrets, zpath, projects_root=projects_root)
    assert exc_info.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert list(projects_root.iterdir()) == []
    assert store.list_projects() == []
