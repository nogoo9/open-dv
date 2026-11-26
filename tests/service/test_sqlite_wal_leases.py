"""UAT-V25-SVC-01: 5-table SQLite WAL state store, leases, dead-PID, mount guard & ports."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opendv.modeling._engine_helpers import poll_control_signals
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import (
    AuditLogRecord,
    ControlSignalRecord,
    RunCheckpointRecord,
    RunStreamEvent,
)
from opendv.models.policy import STEP4_DEFAULT_BUDGET
from opendv.models.project import (
    ConceptMemorySpec,
    ProjectSettingsSnapshot,
    RevisionMetadata,
)
from opendv.models.sources import (
    ColumnProfile,
    ConnectorConfigPayload,
    SourceProfile,
    TableIndexEntry,
    TableProfile,
)
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec
from opendv.service._os_proc_fs import parse_linux_proc_stat
from opendv.service.os_platform import (
    check_wal_filesystem_safe,
    get_process_start_ts,
    is_process_instance_alive,
    resolve_user_state_dir,
    validate_workspace_containment,
)
from opendv.service.state_store import (
    ProjectStateManager,
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
)


def _sample_profile(source_id: str) -> tuple[SourceProfile, list[TableIndexEntry]]:
    prof = SourceProfile(
        source_id=source_id,
        schema_fingerprint="a" * 64,
        table_count=1,
        tables=[
            TableProfile(
                table_id=f"{source_id}::tbl_holdings",
                table_name="tbl_holdings",
                row_count_est=12,
                columns=[
                    ColumnProfile(
                        col_name="holding_id",
                        sql_type="VARCHAR",
                        nullable=False,
                        is_pk=True,
                        pk_ord=1,
                        null_ratio=0.0,
                        distinct_count_est=12,
                    )
                ],
            )
        ],
    )
    idx = [
        TableIndexEntry(
            table_id=f"{source_id}::tbl_holdings",
            source_id=source_id,
            table_name="tbl_holdings",
            cluster_id="cluster_00",
            col_count=1,
            pii_col_count=0,
            pk_cols=["holding_id"],
            fk_refs=[],
            badge="NEW",
        )
    ]
    return prof, idx


def test_sqlite_wal_schema_and_on_delete_cascade(tmp_path: Path) -> None:
    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()

    assert mgr.db_path.is_file()
    conn = sqlite3.connect(str(mgr.db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "projects",
            "sources_and_tables",
            "revisions",
            "events_and_receipts",
            "leases",
        }.issubset(tables)
    finally:
        conn.close()

    proj = mgr.create_project("proj_alpha", "asset_management", ProjectSettingsSnapshot())
    assert proj.project_id == "proj_alpha"
    assert proj.source_count == 0
    assert proj.table_count == 0
    assert len(mgr.list_projects()) == 1

    prof, idx = _sample_profile("src_1")
    mgr.upsert_source(
        "proj_alpha",
        source_id="src_1",
        kind="sqlite",
        uri_or_filename="am.sqlite",
        binary_sha256="b" * 64,
        connector_config=ConnectorConfigPayload(adapter_id="sqlite", display_name="SQLite"),
        status="READY_OFFLINE",
        profile=prof,
        document_map=None,
        tables_index=idx,
    )
    assert mgr.get_source_record("proj_alpha", "src_1")["kind"] == "sqlite"
    assert len(mgr.list_sources("proj_alpha")) == 1

    mem = ConceptMemorySpec(term="NAV", state="PINNED", concept_uri="urn:fibo:nav")
    digest = mgr.save_revision(
        "proj_alpha",
        revision_id="rev-mem-1",
        rev_kind="concept_memory",
        rev_key="MEM-nav-12345678",
        parent_rev_id=None,
        payload=mem,
        metadata=RevisionMetadata(actor="human", reason="Pin NAV concept"),
    )
    assert len(digest) == 64
    assert mgr.get_revision("proj_alpha", "rev-mem-1")["content_sha256"] == digest
    assert mgr.get_revision_by_key("proj_alpha", "concept_memory", "MEM-nav-12345678") is not None

    updated_proj = mgr.update_project_pointers(
        "proj_alpha",
        active_ontology_rev_id="rev-mem-1",
        active_wave_id="W001",
    )
    assert updated_proj.active_ontology_rev_id == "rev-mem-1"
    assert updated_proj.active_wave_id == "W001"
    assert updated_proj.source_count == 1
    assert updated_proj.table_count == 1

    mgr.append_event_or_receipt(
        "proj_alpha",
        record_kind="audit_log",
        run_id="run-1",
        payload=AuditLogRecord(
            event_type="PROJECT_CREATED",
            actor="system",
            step="step1",
            summary="Created project",
        ),
    )
    mgr.claim_lease(
        "proj_alpha",
        lease_key="active_job_lease:proj_alpha",
        holder_run_id="run-1",
        ttl_seconds=45,
    )
    assert mgr.get_project("proj_alpha").has_active_lease is True

    # Deleting project must cascade cleanly across all 4 child tables
    mgr.delete_project("proj_alpha")
    assert mgr.list_projects() == []

    conn2 = sqlite3.connect(str(mgr.db_path))
    try:
        for tbl in ("sources_and_tables", "revisions", "events_and_receipts", "leases"):
            cnt = conn2.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            assert cnt == 0, f"Expected ON DELETE CASCADE to clear {tbl}, found {cnt}"
    finally:
        conn2.close()


def test_leases_conflict_ttl_expiry_and_dead_pid_reclamation(tmp_path: Path) -> None:
    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()
    mgr.create_project("proj_lease", "asset_management", ProjectSettingsSnapshot())

    lease_key = "active_job_lease:proj_lease"
    mgr.claim_lease("proj_lease", lease_key=lease_key, holder_run_id="run-a", ttl_seconds=45)

    with pytest.raises(OpenDVError) as exc_info:
        mgr.claim_lease("proj_lease", lease_key=lease_key, holder_run_id="run-b", ttl_seconds=45)
    assert exc_info.value.error_code == ErrorCode.PROJECT_JOB_ACTIVE
    assert exc_info.value.http_status == 409
    assert int(exc_info.value.exit_code) == 4

    # DuckDB write lease operates independently with 30s TTL
    duck_key = "duckdb_write:proj_lease:duckdb_local"
    mgr.claim_lease("proj_lease", lease_key=duck_key, holder_run_id="run-a", ttl_seconds=30)
    mgr.renew_lease("proj_lease", lease_key=duck_key, holder_run_id="run-a", ttl_seconds=30)
    mgr.release_lease("proj_lease", lease_key=duck_key, holder_run_id="run-a")

    # Simulate dead PID with future expiry -> must be reclaimed cleanly (0 zombie locks)
    future_iso = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(str(mgr.db_path))
    try:
        conn.execute(
            "UPDATE leases SET holder_pid = ?, process_start_ts = ?, expires_at = ? WHERE lease_key = ?",
            (999_999_999, 123456789, future_iso, lease_key),
        )
        conn.commit()
    finally:
        conn.close()

    mgr.claim_lease("proj_lease", lease_key=lease_key, holder_run_id="run-b", ttl_seconds=45)

    # Simulate PID reuse (current PID but mismatched process_start_ts) -> reclaimed cleanly
    cur_ts = get_process_start_ts(os.getpid())
    assert cur_ts > 0
    assert is_process_instance_alive(os.getpid(), cur_ts) is True
    assert is_process_instance_alive(os.getpid(), cur_ts + 9999) is False

    conn = sqlite3.connect(str(mgr.db_path))
    try:
        conn.execute(
            "UPDATE leases SET holder_pid = ?, process_start_ts = ?, expires_at = ? WHERE lease_key = ?",
            (os.getpid(), 1 if cur_ts != 1 else 2, future_iso, lease_key),
        )
        conn.commit()
    finally:
        conn.close()

    mgr.claim_lease("proj_lease", lease_key=lease_key, holder_run_id="run-c", ttl_seconds=45)

    # Simulate expired TTL on same live PID -> reclaimed cleanly
    past_iso = (datetime.now(tz=UTC) - timedelta(seconds=5)).isoformat()
    conn = sqlite3.connect(str(mgr.db_path))
    try:
        conn.execute(
            "UPDATE leases SET expires_at = ? WHERE lease_key = ?",
            (past_iso, lease_key),
        )
        conn.commit()
    finally:
        conn.close()

    mgr.claim_lease("proj_lease", lease_key=lease_key, holder_run_id="run-d", ttl_seconds=45)
    mgr.release_lease("proj_lease", lease_key=lease_key, holder_run_id="run-d")


def test_wal_mount_guard_and_proc_stat_parsing(tmp_path: Path) -> None:
    linux_mounts = "\n".join(
        [
            "/dev/nvme0n1p2 / ext4 rw,relatime 0 0",
            "tmpfs /tmp tmpfs rw,nosuid,nodev 0 0",
            "/dev/nvme1n1 /mnt/local_ssd ext4 rw,relatime 0 0",
            "nas.corp:/exports/dv /mnt/nas_share nfs4 rw 0 0",
            "//fileserver/team /media/smb cifs rw 0 0",
            "C:\\ /mnt/c drvfs rw,noatime 0 0",
            "drvfs /mnt/d 9p rw 0 0",
        ]
    )
    # Safe local paths (no false positives on /tmp or local /mnt/local_ssd)
    check_wal_filesystem_safe("/tmp/opendv_ws", mounts_text=linux_mounts, platform_name="linux")
    check_wal_filesystem_safe(
        "/mnt/local_ssd/opendv_ws", mounts_text=linux_mounts, platform_name="linux"
    )

    for unsafe in ("/mnt/nas_share/proj", "/media/smb/proj", "/mnt/c/Users/dev/proj", "/mnt/d/work"):
        with pytest.raises(OpenDVError) as exc_info:
            check_wal_filesystem_safe(unsafe, mounts_text=linux_mounts, platform_name="linux")
        assert exc_info.value.error_code == ErrorCode.UNSAFE_WAL_FILESYSTEM

    # Windows paths: UNC & DRIVE_REMOTE/DRIVE_CDROM blocked; \\?\C:\... allowed
    check_wal_filesystem_safe(
        "C:\\Users\\dev\\proj",
        drive_type_fn=lambda _root: 3,
        platform_name="win32",
    )
    check_wal_filesystem_safe(
        "\\\\?\\C:\\Users\\dev\\proj",
        drive_type_fn=lambda _root: 3,
        platform_name="win32",
    )
    for unc in (
        "\\\\fileserver\\share\\proj",
        "//fileserver/share/proj",
        "\\\\?\\UNC\\srv\\s",
        "\\\\?\\unc\\server\\share\\ws",
        "\\\\?\\Unc\\server\\share\\ws",
        "//?/unc/server/share/ws",
        "//?/Unc/server/share/ws",
    ):
        with pytest.raises(OpenDVError) as exc_info:
            check_wal_filesystem_safe(unc, drive_type_fn=lambda _r: 3, platform_name="win32")
        assert exc_info.value.error_code == ErrorCode.UNSAFE_WAL_FILESYSTEM

    for bad_drive_type in (4, 5):
        with pytest.raises(OpenDVError) as exc_info:
            check_wal_filesystem_safe(
                "Z:\\proj",
                drive_type_fn=lambda _r, dt=bad_drive_type: dt,
                platform_name="win32",
            )
        assert exc_info.value.error_code == ErrorCode.UNSAFE_WAL_FILESYSTEM

    # Verify /proc/<pid>/stat parsing handles process names with spaces & parens via rfind(")")
    stat_line = (
        "4242 (my (weird) proc) S 1 4242 4242 0 -1 4194304 100 0 0 0 1 0 0 0 20 0 1 0 "
        "987654321 1024 10 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
    )
    assert parse_linux_proc_stat(stat_line) == ("S", 987654321)


def test_jsonl_stream_incomplete_frame_tolerance_and_agent_ports(tmp_path: Path) -> None:
    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()
    mgr.create_project("proj_ports", "asset_management", ProjectSettingsSnapshot())

    sink = ServiceRunEventSink(mgr, "proj_ports")
    reader = ServiceControlSignalReader(mgr, "proj_ports")
    ckpt_store = ServiceRunCheckpointStore(mgr, "proj_ports")

    for seq in (1, 2, 3):
        sink.emit(
            RunStreamEvent(
                seq=seq,
                run_id="run-x",
                ts=datetime.now(tz=UTC).isoformat(),
                event_type="STAGE_UPDATE",
                step="step6",
                payload={"attempt": seq},
            )
        )

    # Append partial trailing frame (simulating mid-write crash/tail race)
    log_path = mgr.run_log_path("proj_ports", "run-x")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq":4,"run_id":"run-x","ts":"2026-01-01T00:00:00Z"')

    page1 = mgr.tail_run_stream_events("proj_ports", "run-x", since_seq=0, limit=2)
    assert [e.seq for e in page1.events] == [1, 2]
    assert page1.next_since_seq == 2

    page2 = mgr.tail_run_stream_events("proj_ports", "run-x", since_seq=2, limit=2)
    assert [e.seq for e in page2.events] == [3]
    assert page2.next_since_seq is None

    # ControlSignalReader must use 0-based per-run offset even when prior SQLite rows exist
    mgr.append_event_or_receipt(
        "proj_ports",
        record_kind="audit_log",
        run_id="run-x",
        payload=AuditLogRecord(
            event_type="STEP6_START", actor="system", step="step6", summary="Start"
        ),
    )
    sig1 = ControlSignalRecord(
        signal_id="sig-1",
        run_id="run-x",
        action="hint",
        hint="Use ISIN",
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    sig2 = ControlSignalRecord(
        signal_id="sig-2",
        run_id="run-x",
        action="pause",
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    mgr.append_event_or_receipt(
        "proj_ports", record_kind="control_signal", run_id="run-x", payload=sig1
    )
    mgr.append_event_or_receipt(
        "proj_ports", record_kind="control_signal", run_id="run-x", payload=sig2
    )

    last_sig_seq = [0]
    hints: list[str] = []
    paused, _, _, _, _ = poll_control_signals(
        reader,
        run_id="run-x",
        last_signal_seq=last_sig_seq,
        budget=STEP4_DEFAULT_BUDGET,
        hints=hints,
    )
    assert paused is True
    assert hints == ["Use ISIN"]
    assert last_sig_seq[0] == 2

    # Second poll MUST return empty (no replay!)
    paused2, _, _, _, _ = poll_control_signals(
        reader,
        run_id="run-x",
        last_signal_seq=last_sig_seq,
        budget=STEP4_DEFAULT_BUDGET,
        hints=hints,
    )
    assert paused2 is False
    assert hints == ["Use ISIN"]

    # RunCheckpointStore + lease release
    mgr.claim_lease(
        "proj_ports", lease_key="active_job_lease:proj_ports", holder_run_id="run-x"
    )
    ckpt = RunCheckpointRecord(
        run_id="run-x",
        step="step6",
        phase="S6-02",
        status="HOLD_CHECKPOINTED",
        last_seq=3,
        ontology_rev_id="v1.0.0",
        sources_fingerprint="c" * 64,
        pending_questions=[],
        budget_turns_used=1,
        budget_tokens_used=100,
        token_count_source="estimated",
    )
    ckpt_store.save_checkpoint(ckpt)
    loaded = ckpt_store.load_checkpoint("run-x")
    assert loaded is not None and loaded.status == "HOLD_CHECKPOINTED"

    snap = AttemptSnapshotSpec(
        attempt=1,
        ir_sha256="d" * 64,
        energy_e=0,
        draft_ir=DraftVaultIR(project_id="proj_ports", ontology_version="1.0.0", wave_number=1),
    )
    ckpt_store.save_attempt_snapshot("run-x", snap)
    ckpt_store.release_active_lease("run-x")
    assert mgr.get_project("proj_ports").has_active_lease is False


def test_user_state_env_and_workspace_containment(tmp_path: Path) -> None:
    win_dir = resolve_user_state_dir(
        env={"LOCALAPPDATA": "C:\\Users\\alice\\AppData\\Local"},
        platform_name="nt",
    )
    assert win_dir == Path("C:\\Users\\alice\\AppData\\Local\\opendv")

    linux_dir = resolve_user_state_dir(
        env={"XDG_STATE_HOME": "/var/lib/alice-state"},
        platform_name="posix",
    )
    assert linux_dir == Path("/var/lib/alice-state/opendv")

    ws_root = tmp_path / "allowed"
    ws_root.mkdir()
    target = ws_root / "sub"
    target.mkdir()
    assert validate_workspace_containment(target, allowed_roots=[ws_root]) == Path(
        os.path.realpath(target)
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(OpenDVError) as exc_info:
        validate_workspace_containment(outside, allowed_roots=[ws_root])
    assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    with pytest.raises(OpenDVError) as remote_exc:
        validate_workspace_containment(target, allowed_roots=[ws_root], is_remote_request=True)
    assert remote_exc.value.error_code == ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN


def test_jsonl_stream_truncated_utf8_trailing_frame_and_corrupt_lines(tmp_path: Path) -> None:
    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()
    mgr.create_project("proj_utf8", "asset_management", ProjectSettingsSnapshot())

    mgr.append_run_stream_event(
        "proj_utf8",
        RunStreamEvent(
            seq=1,
            run_id="run-utf8",
            ts="2026-01-01T00:00:00Z",
            event_type="STAGE_UPDATE",
            step="step6",
            payload={"m": "\u20ac"},
        ),
    )
    log_path = mgr.run_log_path("proj_utf8", "run-utf8")
    # Truncated multi-byte UTF-8 trailing frame (first 2 bytes of U+20AC '€') without newline
    with log_path.open("ab") as fh:
        fh.write(b'{"seq":2,"run_id":"run-utf8","ts":"t","payload":{"m":"\xe2\x82')

    page = mgr.tail_run_stream_events("proj_utf8", "run-utf8", since_seq=0, limit=10)
    assert [e.seq for e in page.events] == [1]
    assert page.events[0].payload == {"m": "\u20ac"}

    # A newline-terminated corrupt last line must fail closed rather than be silently skipped
    with log_path.open("ab") as fh:
        fh.write(b'\n')
    with pytest.raises(OpenDVError) as corrupt_exc:
        mgr.tail_run_stream_events("proj_utf8", "run-utf8", since_seq=0, limit=10)
    assert corrupt_exc.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR

    # Corrupt middle UTF-8 line followed by a valid line must also fail closed
    mgr.append_run_stream_event(
        "proj_utf8",
        RunStreamEvent(
            seq=3,
            run_id="run-utf8",
            ts="2026-01-01T00:00:02Z",
            event_type="STAGE_UPDATE",
            step="step6",
            payload={"ok": True},
        ),
    )
    with pytest.raises(OpenDVError) as mid_exc:
        mgr.tail_run_stream_events("proj_utf8", "run-utf8", since_seq=0, limit=10)
    assert mid_exc.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
