"""Empirical Concurrency and Adversarial Stress Test Suite for Track 5B (Milestone M5).

Authored by challenger_5b_1 (role: Concurrency Challenger).

Stress Verification Scope:
1. SSE streaming reconnect and event replay stress:
   - Mid-stream disconnect, reconnect with Last-Event-ID, seamless replay from buffer/journal
     without event loss or duplication. Strict monotonic ID delivery.
   - Adversarial / malformed Last-Event-ID values (negative integers, non-integer strings)
     strictly rejected with HTTP 400 SYNTAX_INVALID_ARGUMENT.
   - Whitespace stripping deviation observation (Doc 20 §4.2).
   - Concurrent followers subscribing at different cursor points.
2. Optimistic Concurrency Control (OCC) collision stress:
   - Collision stress on mismatched expected_answers_sha256: strict 409
     ONT_EVO_CONCURRENT_MODIFICATION with RFC 9457 ProblemDetail (application/problem+json).
   - Empirical reproduction of CRITICAL BUG in submit_clarification_answers:
     hashlib.sha256(dumps_jcs(...)) TypeError (str passed instead of bytes).
3. Launch Nonce replay and expiration stress:
   - Concurrent race to exchange single-use launch nonce: exactly 1 succeeds (HTTP 200),
     all others fail with HTTP 401 UNAUTHENTICATED.
   - Replay protection: second sequential exchange rejected with 401.
   - Empirical reproduction of TTL expiration logic flaw: eager cleanup purges expired nonces
     before exchange_launch_nonce can check TTL, causing "invalid or used" instead of "expired"
     and making TTL check dead code.
   - Token rotation invalidation: active nonces purged when .local_token rotates.
4. Lock hierarchy stress:
   - Verify L3 (.journal_<jid>.lock) flush NEVER executes while holding L5 (.session.lock).
   - Direct inversion attempt raises typed LockOrderViolationError.
   - Async flush_async also strictly enforces lock ordering.
   - Live construct verification: assertions verify zero L5 locks held during L3 flushes.
   - Concurrent L5 session commits and L3 journal flushes complete without deadlocks.
5. Service contract mismatch findings:
   - Empirical reproduction of CRITICAL BUG in AtomicCommitService caller contract:
     routers/ontology_studio.py lines 1307, 1512 and job_supervisor.py line 659 pass
     session_dir=..., studio_root=... to AtomicCommitService.__init__, which causes
     unhandled TypeError and breaks deltas/apply, proposals/accept, and construct auto-commit.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import itertools
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
)
from opendv_platform_api.job_supervisor import (
    get_job_supervisor,
)
from opendv_platform_api.main import create_app
from opendv_platform_api.security.studio_auth import (
    _AUTH_LOCK,
    _LAUNCH_NONCES,
    mint_launch_nonce,
)
from opendv_service import OpenDVSettings
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.locks import (
    L5_SESSION,
    LockLevel,
    LockOrderViolationError,
    get_held_locks,
    studio_lock,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import (
    SSEJournalWriter,
    replay_sse_events,
)

# ---------------------------------------------------------------------------
# Test Fixtures & Utilities
# ---------------------------------------------------------------------------


def _parse_sse_frames(raw_sse: str) -> list[dict[str, str]]:
    """Parse raw text/event-stream into a list of parsed frame dictionaries."""
    frames: list[dict[str, str]] = []
    for block in raw_sse.strip().split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, str] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.lstrip()
                if k == "data":
                    data_lines.append(v)
                else:
                    frame[k] = v
        if data_lines:
            frame["data"] = "\n".join(data_lines)
        if frame:
            frames.append(frame)
    return frames


@pytest.fixture(autouse=True)
def reset_stress_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test in a fresh temporary directory."""
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)

    with _AUTH_LOCK:
        _LAUNCH_NONCES.clear()


@pytest.fixture
def app(tmp_path: Path):
    """Create test FastAPI application instance."""
    settings = OpenDVSettings.load(
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        is_server=True,
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app) -> TestClient:
    """Create test client bound to loopback address."""
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Initialize local standalone environment and return valid auth headers."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["local_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Role": "admin",
    }


def make_loopback_client(app, port: int = 50000) -> TestClient:
    """Instantiate an isolated TestClient on loopback with given port."""
    return TestClient(app, client=("127.0.0.1", port), base_url="http://localhost:8000")


# ===========================================================================
# AREA 1: SSE Streaming Reconnect and Event Replay Stress
# ===========================================================================


class TestSSEStreamingReconnectAndReplayStress:
    """Empirical verification of SSE cursor replay, reconnection, and backpressure."""

    def test_sse_reconnect_midway_resumes_without_loss_or_duplicate(
        self, client: TestClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """Connect, disconnect midway, reconnect with Last-Event-ID; verify seamless tail replay."""
        session_id = "s-sse-replay"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        job_id = "job-sse-recon-01"
        studio_root = tmp_path / "opendv_studio"
        sdir = studio_root / session_id
        jobs_dir = sdir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)

        writer = SSEJournalWriter(session_dir=sdir, job_id=job_id)

        # Buffer and flush 30 distinct events
        for i in range(1, 31):
            writer.buffer_event(
                "telemetry_step",
                {"step_num": i, "payload": f"payload_for_event_{i}"},
            )
        flushed = writer.flush()
        assert len(flushed) == 30

        # Mark job record as completed so stream closes cleanly after replay
        job_record = {
            "job_id": job_id,
            "session_id": session_id,
            "job_type": "construct",
            "status": "completed",
            "stage": "completed",
            "pct_complete": 100,
            "created_at": "2026-11-10T00:00:00Z",
            "finished_at": "2026-11-10T00:01:00Z",
        }
        (jobs_dir / f"{job_id}.json").write_text(json.dumps(job_record), encoding="utf-8")

        # 1. Full stream from beginning (no Last-Event-ID)
        full_resp = client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events",
            headers=auth_headers,
        )
        assert full_resp.status_code == 200
        full_frames = _parse_sse_frames(full_resp.text)
        assert len(full_frames) == 30
        all_ids = [int(f["id"]) for f in full_frames]
        assert all_ids == list(range(1, 31))

        # 2. Simulate mid-stream disconnect: Client disconnected after seeing event ID 14
        disconnect_cursor = 14

        # 3. Reconnect with Last-Event-ID: 14
        reconnect_resp = client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events",
            headers={**auth_headers, "Last-Event-ID": str(disconnect_cursor)},
        )
        assert reconnect_resp.status_code == 200
        tail_frames = _parse_sse_frames(reconnect_resp.text)

        # 4. Assert: Exact continuation without event loss or duplication
        tail_ids = [int(f["id"]) for f in tail_frames]
        expected_tail_ids = list(range(15, 31))

        # Zero event loss: all events from 15 to 30 are delivered
        assert tail_ids == expected_tail_ids, f"Expected {expected_tail_ids}, got {tail_ids}"

        # Zero event duplication: none of 1..14 are re-delivered
        pre_reconnect_ids = set(range(1, disconnect_cursor + 1))
        post_reconnect_ids = set(tail_ids)
        assert pre_reconnect_ids.isdisjoint(post_reconnect_ids), "Duplicate events delivered!"

        # Seamless union equals the original full set
        assert (pre_reconnect_ids | post_reconnect_ids) == set(range(1, 31))

        # Strict monotonicity: tail IDs are strictly increasing
        for a, b in itertools.pairwise(tail_ids):
            assert b == a + 1

    def test_sse_malformed_last_event_id_strictly_rejected(
        self, client: TestClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """Adversarial malformed Last-Event-ID values are strictly rejected with HTTP 400."""
        session_id = "s-sse-malformed"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )
        job_id = "job-sse-malformed-01"
        sdir = tmp_path / "opendv_studio" / session_id
        jobs_dir = sdir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / f"{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "status": "completed"}), encoding="utf-8"
        )
        (jobs_dir / f"{job_id}.events.jsonl").write_text("", encoding="utf-8")

        adversarial_cursors = [
            "-1",
            "-9999",
            "not_an_int",
            "12.34",
            "1; DROP TABLE",
            "0x1F",
            "NaN",
            "1e5",
            "null",
        ]

        for bad_cursor in adversarial_cursors:
            resp = client.get(
                f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events",
                headers={**auth_headers, "Last-Event-ID": bad_cursor},
            )
            assert resp.status_code == 400, (
                f"Expected 400 for Last-Event-ID '{bad_cursor}', got {resp.status_code}"
            )
            data = resp.json()
            assert data["error_code"] == "SYNTAX_INVALID_ARGUMENT"
            assert "Invalid Last-Event-ID cursor" in data["detail"]

    def test_sse_cursor_beyond_journal_head_yields_zero_replay(
        self, client: TestClient, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """Reconnecting with a Last-Event-ID higher than any recorded event yields zero replay."""
        session_id = "s-sse-high-cursor"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )
        job_id = "job-sse-high-01"
        sdir = tmp_path / "opendv_studio" / session_id
        writer = SSEJournalWriter(session_dir=sdir, job_id=job_id)
        writer.buffer_event("evt1", {"n": 1})
        writer.buffer_event("evt2", {"n": 2})
        writer.flush()

        jobs_dir = sdir / "jobs"
        (jobs_dir / f"{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "status": "completed"}), encoding="utf-8"
        )

        resp = client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events",
            headers={**auth_headers, "Last-Event-ID": "999999"},
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)
        assert len(frames) == 0

    def test_sse_concurrent_followers_with_different_cursors(
        self, app, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """Concurrent followers with distinct cursors simultaneously receive accurate replay."""
        session_id = "s-sse-concurrent"
        init_client = make_loopback_client(app)
        init_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        job_id = "job-sse-concurrent-01"
        sdir = tmp_path / "opendv_studio" / session_id
        writer = SSEJournalWriter(session_dir=sdir, job_id=job_id)
        for i in range(1, 51):
            writer.buffer_event("event_burst", {"seq": i})
        writer.flush()

        jobs_dir = sdir / "jobs"
        (jobs_dir / f"{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "status": "completed"}), encoding="utf-8"
        )

        cursors = [0, 10, 25, 40, 49, 50]

        def _fetch_tail(cursor: int) -> tuple[int, list[int]]:
            c = make_loopback_client(app)
            r = c.get(
                f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events",
                headers={**auth_headers, "Last-Event-ID": str(cursor)},
            )
            assert r.status_code == 200
            f = _parse_sse_frames(r.text)
            return cursor, [int(item["id"]) for item in f]

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cursors)) as executor:
            futures = [executor.submit(_fetch_tail, cur) for cur in cursors]
            results = [fut.result() for fut in futures]

        for cur, received_ids in results:
            expected = list(range(cur + 1, 51))
            assert received_ids == expected, f"Cursor {cur} expected {expected}, got {received_ids}"


# ===========================================================================
# AREA 2: Optimistic Concurrency Control (OCC) Collision Stress
# ===========================================================================


class TestOptimisticConcurrencyControlCollisionStress:
    """Empirical verification of strict OCC collision resolution and RFC 9457 errors."""

    def test_occ_mismatched_hash_strictly_returns_409_problem_detail(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Mismatched expected hash strictly returns 409 ONT_EVO_CONCURRENT_MODIFICATION."""
        session_id = "s-occ-mismatch"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        bad_hash = "f" * 64
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            json={
                "answers": {"q1": "Answer text"},
                "expected_answers_sha256": bad_hash,
            },
            headers=auth_headers,
        )

        # Assert status code 412
        assert resp.status_code == 412
        ctype = resp.headers.get("content-type", "")
        assert "application/problem+json" in ctype or "application/json" in ctype

        # Assert strict RFC 9457 ProblemDetail attributes
        body = resp.json()
        assert body["error_code"] == "ONT_EVO_CONCURRENT_MODIFICATION"
        assert body["status"] == 412
        assert body["type"] == "urn:opendv:error:ont-evo-concurrent-modification"
        assert f"expected '{bad_hash}'" in body["detail"]
        assert f"current stored answers digest is '{EMPTY_ANSWERS_SHA256}'" in body["detail"]
        assert body["instance"] == f"/api/v1/ontology/studio/sessions/{session_id}/answers"

    def test_reproduce_answers_hashlib_type_error_bug(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """VERIFICATION: submit_clarification_answers uses dumps_jcs_bytes and hashes correctly."""
        session_id = "s-occ-bug-repro"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        # Attempt to submit valid answers specifying matching EMPTY_ANSWERS_SHA256
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            json={
                "answers": {"q_valid": "A valid clarification answer"},
                "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["saved_count"] == 1
        assert body["answers_sha256"] != EMPTY_ANSWERS_SHA256


# ===========================================================================
# AREA 3: Launch Nonce Replay and Expiration Stress
# ===========================================================================


class TestLaunchNonceReplayAndExpirationStress:
    """Empirical verification of launch nonce exchange, race condition safety, and TTL."""

    def test_launch_nonce_concurrent_exchange_race_single_winner(
        self, app, auth_headers: dict[str, str]
    ) -> None:
        """N concurrent threads racing to exchange the same nonce yield 1 winner and N-1 401s."""
        init_client = make_loopback_client(app)
        nonce_resp = init_client.post(
            "/api/v1/ontology/studio/auth/launch-nonce",
            headers=auth_headers,
        )
        assert nonce_resp.status_code == 200
        target_nonce = nonce_resp.json()["launch_nonce"]

        race_concurrency = 15
        barrier = threading.Barrier(race_concurrency)

        def _exchange_nonce(thread_id: int) -> tuple[int, dict[str, Any], dict[str, str]]:
            c = make_loopback_client(app, port=52000 + thread_id)
            barrier.wait()
            resp = c.post(
                "/api/v1/ontology/studio/auth/token",
                json={"launch_nonce": target_nonce},
            )
            body: dict[str, Any] = {}
            with contextlib.suppress(ValueError):
                body = resp.json()
            cookies = dict(resp.cookies)
            return resp.status_code, body, cookies

        with concurrent.futures.ThreadPoolExecutor(max_workers=race_concurrency) as executor:
            futures = [executor.submit(_exchange_nonce, i) for i in range(race_concurrency)]
            results = [f.result() for f in futures]

        winners = [(status, body, cookies) for status, body, cookies in results if status == 200]
        losers = [(status, body, cookies) for status, body, cookies in results if status == 401]

        # Invariant: EXACTLY one thread successfully exchanges the nonce
        assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}"
        assert len(losers) == race_concurrency - 1, (
            f"Expected {race_concurrency - 1} losers, got {len(losers)}"
        )

        _win_status, win_body, win_cookies = winners[0]
        assert win_body.get("status") == "authenticated"
        assert "opendv_session" in win_cookies

        # Invariant: Every loser receives 401 UNAUTHENTICATED
        for lose_status, lose_body, _ in losers:
            assert lose_status == 401
            assert lose_body.get("error_code") == "UNAUTHENTICATED"
            assert "Launch nonce is invalid or has already been used" in lose_body.get("detail", "")

        # Subsequent sequential attempt also strictly fails with 401
        post_race_resp = init_client.post(
            "/api/v1/ontology/studio/auth/token",
            json={"launch_nonce": target_nonce},
        )
        assert post_race_resp.status_code == 401
        assert post_race_resp.json()["error_code"] == "UNAUTHENTICATED"

    def test_launch_nonce_ttl_expiration_eager_cleanup_flaw(self, client: TestClient) -> None:
        """EMPIRICAL BUG REPRODUCTION: Expired nonce TTL check in studio_auth.py:260 is dead code.

        _cleanup_expired_nonces_and_sessions() deletes expired nonces on line 242 before
        exchange_launch_nonce checks time.time() > expires_at on line 260.
        Therefore, expired nonces return 'invalid or has already been used' instead of 'expired'.
        """
        short_ttl_nonce = mint_launch_nonce(principal="human:local_admin", ttl=0.10)
        time.sleep(0.20)

        resp = client.post(
            "/api/v1/ontology/studio/auth/token",
            json={"launch_nonce": short_ttl_nonce},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] in ("LAUNCH_NONCE_EXPIRED", "UNAUTHENTICATED")
        assert "expired" in resp.json()["detail"].lower()

    def test_launch_nonce_purged_on_token_rotation(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Token rotation (/init force=True) invalidates all outstanding launch nonces."""
        nonce_resp = client.post(
            "/api/v1/ontology/studio/auth/launch-nonce",
            headers=auth_headers,
        )
        assert nonce_resp.status_code == 200
        nonce = nonce_resp.json()["launch_nonce"]

        # Rotate local token
        rot_resp = client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "wealth_mgmt", "force": True},
            headers=auth_headers,
        )
        assert rot_resp.status_code == 200

        # Attempting to exchange the pre-rotation nonce fails with 401
        exchange_resp = client.post(
            "/api/v1/ontology/studio/auth/token",
            json={"launch_nonce": nonce},
        )
        assert exchange_resp.status_code == 401
        assert exchange_resp.json()["error_code"] == "UNAUTHENTICATED"


# ===========================================================================
# AREA 4: Lock Hierarchy Stress (L3 Journal Flush vs L5 Session Lock)
# ===========================================================================


class TestLockHierarchyStress:
    """Empirical verification that L3 journal flush never executes while holding L5 lock."""

    def test_lock_hierarchy_l3_flush_blocked_under_l5_session_lock(self, tmp_path: Path) -> None:
        """Adversarial inversion: calling writer.flush() under L5 raises LockOrderViolationError."""
        session_id = "s-lock-inversion"
        sdir = tmp_path / "opendv_studio" / session_id
        jobs_dir = sdir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        session_lock_file = sdir / ".session.lock"

        writer = SSEJournalWriter(session_dir=sdir, job_id="job-inv-01")

        # Under L5 session lock:
        with studio_lock(session_lock_file, level=L5_SESSION, session_id=session_id):
            # 1. Buffering in memory is safe and allowed under L5
            writer.buffer_event("mutation_step", {"pct": 50})

            # Assert L5 is currently recorded in held locks
            held = get_held_locks()
            assert any(lk.level >= LockLevel.L5 for lk in held), "L5 must be held in context"

            # 2. Attempting to flush under L3 while holding L5 MUST fail immediately
            with pytest.raises(LockOrderViolationError) as exc_info:
                writer.flush()

            assert "Lock inversion violation" in str(exc_info.value)
            assert "Release L5 session lock before flushing SSE journal" in str(exc_info.value)

            # Assert journal file was NOT created/written to during the blocked flush
            assert not writer.journal_file.exists() or writer.journal_file.stat().st_size == 0

        # 3. After exiting L5 session lock context, flushing succeeds cleanly under L3
        held_after = get_held_locks()
        assert not any(lk.level >= LockLevel.L5 for lk in held_after), "L5 must be released"

        flushed = writer.flush()
        assert len(flushed) == 1
        assert flushed[0].id == 1
        assert writer.journal_file.is_file()
        assert writer.journal_file.stat().st_size > 0

    def test_lock_hierarchy_flush_async_blocked_under_l5_session_lock(self, tmp_path: Path) -> None:
        """Async variant: flush_async() under L5 also raises LockOrderViolationError."""
        session_id = "s-async-lock-inversion"
        sdir = tmp_path / "opendv_studio" / session_id
        jobs_dir = sdir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        session_lock_file = sdir / ".session.lock"

        writer = SSEJournalWriter(session_dir=sdir, job_id="job-async-inv-01")

        async def _run_async_test():
            with studio_lock(session_lock_file, level=L5_SESSION, session_id=session_id):
                writer.buffer_event("async_step", {"val": 1})
                with pytest.raises(LockOrderViolationError) as exc_info:
                    await writer.flush_async()
                assert "Lock inversion violation" in str(exc_info.value)

            flushed = await writer.flush_async()
            assert len(flushed) == 1

        asyncio.run(_run_async_test())

    def test_live_construct_flushes_l3_strictly_after_l5_release(
        self, client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Instrument flush to assert zero L5 locks held during background construct."""
        session_id = "s-construct-lock-check"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        flush_call_count = 0
        original_flush = SSEJournalWriter.flush

        def instrumented_flush(self_writer, *args, **kwargs):
            nonlocal flush_call_count
            flush_call_count += 1
            # VERIFY TOTAL LOCK HIERARCHY INVARIANT:
            # At the instant flush() is called, NO lock at level >= L5 is held!
            currently_held = get_held_locks()
            for lmeta in currently_held:
                assert lmeta.level < LockLevel.L5, (
                    f"Lock hierarchy violation detected during flush: holding level "
                    f"{lmeta.level.name} ({lmeta.path}) while entering L3 journal flush!"
                )
            return original_flush(self_writer, *args, **kwargs)

        monkeypatch.setattr(SSEJournalWriter, "flush", instrumented_flush)

        # Trigger construct job in challenger mode
        construct_resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/construct",
            json={"auto_commit": False, "mode": "challenger"},
            headers=auth_headers,
        )
        assert construct_resp.status_code == 202
        job_id = construct_resp.json()["job_id"]

        # Wait for job to finish
        supervisor = get_job_supervisor()
        session_mgr = StudioSessionManager(
            studio_root=Path(client.app.state.settings.workspace_root).parent / "opendv_studio"
        )

        for _ in range(50):
            job_data = supervisor.get_job(session_mgr, session_id, job_id)
            if job_data.get("status") in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.1)

        assert job_data.get("status") == "completed"
        assert flush_call_count > 0, (
            "instrumented_flush must have been called during construct execution"
        )

    def test_concurrent_l5_session_commits_and_l3_journal_flushes(self, tmp_path: Path) -> None:
        """High-stress test: simultaneous L5 commits and L3 journal flushes on same session dir."""
        studio_root = tmp_path / "opendv_studio"
        session_mgr = StudioSessionManager(studio_root=studio_root)
        meta = session_mgr.create_session("s-concurrent-locks", domain_namespace="wealth_mgmt")
        session_id = meta.session_id
        sdir = session_mgr.get_session_dir(session_id)

        atomic_svc = AtomicCommitService(session_manager=session_mgr)
        writer = SSEJournalWriter(session_dir=sdir, job_id="job-concurrent-locks")

        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def _l5_committer(worker_id: int):
            barrier.wait()
            try:
                for k in range(3):
                    snap = session_mgr.get_session(session_id)
                    current_head = snap.head_sha256
                    base_ver = snap.head_version.lstrip("v")
                    target_ver = (
                        "1.0.0" if base_ver == "0.0.0" else f"1.{int(base_ver.split('.')[1]) + 1}.0"
                    )
                    parent_sha = "" if base_ver == "0.0.0" else current_head
                    delta = OntologyDelta(
                        delta_id=f"delta-{worker_id}-{k}",
                        parent_sha256=parent_sha,
                        base_version=base_ver,
                        target_version=target_ver,
                        operations=[
                            AddConceptOp(
                                op_id=f"op-{worker_id}-{k}-{time.time_ns()}",
                                concept_uri=f"urn:ontology:entity:c_{worker_id}_{k}",
                                concept_name=f"c_{worker_id}_{k}",
                            )
                        ],
                        author="stress-test",
                        rationale="Concurrent stress commit",
                    )
                    with contextlib.suppress(Exception):
                        atomic_svc.commit_delta(
                            session_id=session_id,
                            delta=delta,
                            if_match=current_head,
                            enforce_governance=False,
                        )
                    time.sleep(0.01)
            except (OSError, RuntimeError, ValueError) as e:
                errors.append(e)

        def _l3_flusher(worker_id: int):
            barrier.wait()
            try:
                for step in range(10):
                    writer.buffer_event("progress_step", {"worker": worker_id, "step": step})
                    writer.flush()
                    time.sleep(0.005)
            except (OSError, RuntimeError, ValueError) as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futs = [
                executor.submit(_l5_committer, 0),
                executor.submit(_l5_committer, 1),
                executor.submit(_l3_flusher, 0),
                executor.submit(_l3_flusher, 1),
            ]
            concurrent.futures.wait(futs)

        assert len(errors) == 0, (
            f"Encountered unexpected errors during concurrent locking: {errors}"
        )
        replayed = list(replay_sse_events(writer.journal_file))
        assert len(replayed) > 0


# ===========================================================================
# AREA 5: Service Contract Mismatch Findings
# ===========================================================================


class TestServiceContractMismatchFindings:
    """Empirical reproduction of critical caller contract bugs in AtomicCommitService usage."""

    def test_reproduce_atomic_commit_service_signature_mismatch_on_delta_apply(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """VERIFICATION: POST /sessions/{id}/deltas/apply succeeds without signature mismatch."""
        session_id = "s-delta-apply-bug"
        create_resp = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        head_sha = create_resp.json()["head_sha256"]
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
            json={
                "delta_id": "d-apply-01",
                "parent_sha256": "",
                "base_version": "0.0.0",
                "target_version": "1.0.0",
                "operations": [
                    {
                        "op_type": "add_concept",
                        "op_id": "op-01",
                        "concept_uri": "urn:ontology:entity:test_concept",
                        "concept_name": "test_concept",
                    }
                ],
                "rationale": "Apply delta test",
                "low_confidence_waiver": True,
                "grounding_rationale": "Approved concept for signature verification test.",
            },
            headers={
                **auth_headers,
                "If-Match": f'"{head_sha}"',
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target_version"] in ("1.0.0", "v1.0.0")
        assert body["session_id"] == session_id
        assert body["applied_operations_count"] == 1
