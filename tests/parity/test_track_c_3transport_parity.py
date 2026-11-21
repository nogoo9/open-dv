"""Phase 2 Track C Failing-First Parity Suite: 4 New 3-Transport Subcommands (D13 / FR-CLI-09).

Verifies RFC 8785 JCS parity and exit codes (`0`, `10`, `20`, `40`) across:
- `opendv` (`StandaloneCliTransport`)
- `opendvctl` (`ClientCliTransport`)
- `DIRECT_REST` (`httpx` against live platform API)

Covers `run dv-schema`, `ontology studio attach-source`, `ontology studio fibo-qa`, and
`ontology studio logs` (incl. `--follow` SSE and `STAGE_00..07` + `00..06` stage filtering).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
import uvicorn

from opendv_contracts.jcs import dumps_jcs
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_platform_api.job_supervisor import get_job_supervisor
from opendv_service import OpenDVSettings
from opendv_service.state.persistence import save_platform_state
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_testkit import seed_canonical_pe_demo
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

_VOLATILE_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "timestamp",
        "duration_ms",
        "source_id",
        "job_id",
        "answer_id",
        "cas_hash",
        "uri",
    }
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _normalize_jcs(payload: Any) -> Any:
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k in sorted(payload.keys()):
            if k == "mode":
                continue
            v = payload[k]
            if k in _VOLATILE_KEYS:
                out[k] = f"<{k.upper()}>"
            elif isinstance(v, (dict, list)):
                out[k] = _normalize_jcs(v)
            elif isinstance(v, str) and ("\\" in v or "/" in v) and not v.startswith("urn:"):
                out[k] = v.replace("\\", "/")
            else:
                out[k] = v
        return out
    if isinstance(payload, list):
        return [_normalize_jcs(item) for item in payload]
    return payload


def _parse_json_output(stdout: str, stderr: str) -> dict[str, Any]:
    for text in (stdout.strip(), stderr.strip()):
        if not text:
            continue
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"items": parsed}
        except json.JSONDecodeError:
            pass
    return {"raw_stdout": stdout.strip(), "raw_stderr": stderr.strip()}


def _assert_3transport_jcs_equal(
    out_py: tuple[str, str], out_ts: tuple[str, str], rest_json: Any
) -> None:
    jcs_py = dumps_jcs(_normalize_jcs(_parse_json_output(*out_py)))
    jcs_ts = dumps_jcs(_normalize_jcs(_parse_json_output(*out_ts)))
    jcs_rest = dumps_jcs(_normalize_jcs(rest_json))
    assert jcs_py == jcs_ts == jcs_rest


def _normalize_error_envelope(
    payload: dict[str, Any], *, require_error_code: bool = True
) -> dict[str, Any]:
    error_code = str(payload.get("error_code") or payload.get("code") or "")
    detail = str(payload.get("detail") or payload.get("error") or "")
    norm: dict[str, Any] = {"detail": detail}
    if require_error_code or error_code:
        norm["error_code"] = error_code
    return _normalize_jcs(norm)


def _assert_3transport_error_parity(
    out_py: tuple[str, str],
    out_ts: tuple[str, str],
    rest_json: dict[str, Any],
    *,
    expected_error_code: str | None = None,
) -> None:
    py_obj = _parse_json_output(*out_py)
    ts_obj = _parse_json_output(*out_ts)
    assert py_obj.get("status") == "ERROR", py_obj
    assert ts_obj.get("status") == "ERROR", ts_obj
    assert "detail" in rest_json and "status" in rest_json, rest_json
    if expected_error_code is not None:
        assert py_obj.get("error_code") == expected_error_code
        assert ts_obj.get("error_code") == expected_error_code
        assert (rest_json.get("error_code") or rest_json.get("code")) == expected_error_code
    has_rest_code = bool(rest_json.get("error_code") or rest_json.get("code"))
    jcs_py = dumps_jcs(_normalize_error_envelope(py_obj, require_error_code=has_rest_code))
    jcs_ts = dumps_jcs(_normalize_error_envelope(ts_obj, require_error_code=has_rest_code))
    jcs_rest = dumps_jcs(_normalize_error_envelope(rest_json, require_error_code=has_rest_code))
    if not has_rest_code:
        # Non-studio RFC 9457 responses omit error_code; verify CLI codes still match each other
        assert py_obj.get("error_code") == ts_obj.get("error_code")
        jcs_py_code = dumps_jcs({"detail": py_obj.get("detail", "")})
        jcs_ts_code = dumps_jcs({"detail": ts_obj.get("detail", "")})
        jcs_rest_code = dumps_jcs({"detail": rest_json.get("detail", "")})
        assert jcs_py_code == jcs_ts_code == jcs_rest_code
    else:
        assert jcs_py == jcs_ts == jcs_rest


@dataclass
class TrackCParityContext:
    workspace_root: Path
    state_dir: Path
    studio_root: Path
    run_id: str
    compare_run_id: str
    session_id: str
    csv_path: Path
    cli: StandaloneCliTransport
    ctl: ClientCliTransport
    rest: httpx.Client


@pytest.fixture
def track_c_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TrackCParityContext, None, None]:
    """Isolated workspace, persisted DuckDB sandbox, seeded Studio session, and ephemeral API."""
    ws_root = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    studio_root = tmp_path / "studio"
    for p in (ws_root, state_dir, studio_root, state_dir / "sandboxes"):
        p.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_STATE_DIR", str(state_dir))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_AGY_CLI_BIN", "disabled")

    csv_path = ws_root / "positions_sample.csv"
    csv_path.write_text("account_id,symbol,quantity\nACC-01,AAPL,100\n", encoding="utf-8")

    state = PlatformStateContainer(workspace_root=ws_root, storage_root=state_dir)
    compare_run_id, run_id = "run-trackc-w1", "run-trackc-w2"
    seed_canonical_pe_demo(state, run_id=compare_run_id)
    seed_canonical_pe_demo(state, run_id=run_id)
    save_platform_state(state)

    for rid in (compare_run_id, run_id):
        con = duckdb.connect(str(state_dir / "sandboxes" / f"{rid}.duckdb"))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
            con.execute(
                "CREATE TABLE IF NOT EXISTS raw_vault.hub_fund "
                "(hk_fund VARCHAR, fund_code VARCHAR, load_dts TIMESTAMP);"
            )
            con.execute(
                "INSERT INTO raw_vault.hub_fund VALUES "
                "('0000000000000000000000000000000000000000000000000000000000000000', '-1', "
                "TIMESTAMP '1900-01-01 00:00:00'), "
                "('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'FUND_A', "
                "TIMESTAMP '2026-11-19 00:00:00');"
            )
        finally:
            con.close()

    mgr = StudioSessionManager(studio_root=studio_root)
    session_id = "sess-trackc-parity"
    mgr.create_session(
        session_id=session_id,
        domain_namespace="asset_management",
        enterprise_owner="Parity QA",
    )

    from opendv_agent_harness import build_observability_event_dict
    from opendv_service.studio.sse_journal import SSEJournalWriter
    from opendv_telemetry import VaultLedgerEmitter

    writer = SSEJournalWriter(mgr.get_session_dir(session_id), "job-trackc-1", session_id=session_id)
    writer.buffer_event(
        "log_entry",
        {
            "seq": 1,
            "timestamp": "2026-11-19T03:00:00Z",
            "wizard_step": 6,
            "job_id": "job-trackc-1",
            "stage_id": "STAGE_00_ORCHESTRATION",
            "persona": "@vault-first-mate",
            "category": "pipeline",
            "status": "COMPLETED",
            "masked_prompt": "Preflight check",
            "structured_or_raw_response": "OK",
        },
    )
    writer.buffer_event(
        "log_entry",
        {
            "seq": 2,
            "timestamp": "2026-11-19T03:00:05Z",
            "wizard_step": 6,
            "job_id": "job-trackc-1",
            "stage_id": "STAGE_03_SHIP_VAULT_PASS1",
            "persona": "@ship-vault",
            "category": "agent",
            "status": "COMPLETED",
            "masked_prompt": "Column triage",
            "structured_or_raw_response": "100% triaged",
        },
    )
    writer.flush()
    emitter = VaultLedgerEmitter(ledger_path=state.ledger_dir / "run-trackc-1.jsonl", cas_store=state.cas)
    emitter.emit(
        build_observability_event_dict(
            event_id="evt-trackc-07",
            event_type="STAGE_COMPLETED",
            run_id="run-trackc-1",
            trace_id="a" * 32,
            span_id="b" * 16,
            timestamp_utc="2026-11-19T03:00:10Z",
            stage_id="STAGE_07_DETERMINISTIC_COMPILER",
            agent_persona="deterministic-compiler",
            status="PASS",
            headline="Lower CanonicalIR",
            business_impact="5-Zone SQL emitted",
            summary_metrics={"session_id": session_id, "wizard_step": 6},
        )
    )

    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=state_dir, is_server=True)
    app = create_app(
        settings=settings,
        platform_state=state,
        workspace_root=ws_root,
        storage_root=state_dir,
    )
    port = _find_free_port()

    class _UvicornServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    server = _UvicornServer(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    server_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except OSError:
            time.sleep(0.05)

    cli = StandaloneCliTransport(workspace_dir=ws_root)
    ctl = ClientCliTransport(workspace_dir=ws_root, server_url=server_url)
    rest = httpx.Client(base_url=server_url, timeout=15.0)
    try:
        yield TrackCParityContext(
            workspace_root=ws_root,
            state_dir=state_dir,
            studio_root=studio_root,
            run_id=run_id,
            compare_run_id=compare_run_id,
            session_id=session_id,
            csv_path=csv_path,
            cli=cli,
            ctl=ctl,
            rest=rest,
        )
    finally:
        rest.close()
        server.should_exit = True
        thread.join(timeout=2.0)


@pytest.mark.parity
def test_run_dv_schema_3transport_jcs_parity_and_exit_codes(
    track_c_ctx: TrackCParityContext,
) -> None:
    """3-transport JCS parity + exit codes 0 and 40 for `run dv-schema`."""
    sql_q = "SELECT fund_code FROM raw_vault.hub_fund ORDER BY fund_code LIMIT 5"
    cases = [
        (
            ["--compare", track_c_ctx.compare_run_id],
            lambda: track_c_ctx.rest.get(
                f"/api/v1/runs/{track_c_ctx.run_id}/dv-schema",
                params={"compare": track_c_ctx.compare_run_id},
            ),
        ),
        (
            ["--table", "hub_fund", "--limit", "10", "--offset", "0"],
            lambda: track_c_ctx.rest.get(
                f"/api/v1/runs/{track_c_ctx.run_id}/dv-schema/tables/hub_fund/rows",
                params={"limit": 10, "offset": 0},
            ),
        ),
        (
            ["--sql", sql_q],
            lambda: track_c_ctx.rest.post(
                f"/api/v1/runs/{track_c_ctx.run_id}/dv-schema/query", json={"sql": sql_q}
            ),
        ),
    ]
    for extra_args, rest_call in cases:
        argv = ["run", "dv-schema", "--run-id", track_c_ctx.run_id, *extra_args, "--json"]
        c_py, out_py, err_py = track_c_ctx.cli.invoke_raw(argv)
        c_ts, out_ts, err_ts = track_c_ctx.ctl.invoke_raw(argv)
        r_resp = rest_call()
        assert c_py == 0, f"opendv failed ({c_py}): {err_py or out_py}"
        assert c_ts == 0, f"opendvctl failed ({c_ts}): {err_ts or out_ts}"
        assert r_resp.status_code == 200, r_resp.text
        _assert_3transport_jcs_equal((out_py, err_py), (out_ts, err_ts), r_resp.json())

    bad_argv = [
        "run",
        "dv-schema",
        "--run-id",
        track_c_ctx.run_id,
        "--sql",
        "DROP TABLE raw_vault.hub_fund",
        "--json",
    ]
    c_py_b, o_py_b, e_py_b = track_c_ctx.cli.invoke_raw(bad_argv)
    c_ts_b, o_ts_b, e_ts_b = track_c_ctx.ctl.invoke_raw(bad_argv)
    r_bad = track_c_ctx.rest.post(
        f"/api/v1/runs/{track_c_ctx.run_id}/dv-schema/query",
        json={"sql": "DROP TABLE raw_vault.hub_fund"},
    )
    assert r_bad.status_code == 400
    assert c_py_b == 40, f"Expected exit 40 on unsafe SQL, got {c_py_b}: {e_py_b}"
    assert c_ts_b == 40, f"Expected exit 40 on unsafe SQL, got {c_ts_b}: {e_ts_b}"
    _assert_3transport_error_parity((o_py_b, e_py_b), (o_ts_b, e_ts_b), r_bad.json())


@pytest.mark.parity
def test_attach_source_3transport_jcs_parity_and_exit_codes(
    track_c_ctx: TrackCParityContext,
) -> None:
    """3-transport JCS parity + exit codes 0 and 40 for `ontology studio attach-source`."""
    argv = [
        "ontology",
        "studio",
        "attach-source",
        "--session",
        track_c_ctx.session_id,
        "--adapter",
        "filesystem",
        "--uri",
        str(track_c_ctx.csv_path),
        "--table",
        "positions_sample",
        "--namespace",
        "asset_management",
        "--json",
    ]
    c_py, o_py, e_py = track_c_ctx.cli.invoke_raw(argv)
    c_ts, o_ts, e_ts = track_c_ctx.ctl.invoke_raw(argv)
    r_ok = track_c_ctx.rest.post(
        f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/sources/connectors",
        json={
            "adapter": "filesystem",
            "uri": str(track_c_ctx.csv_path),
            "table": "positions_sample",
            "namespace": "asset_management",
        },
    )
    assert c_py == 0, f"opendv attach-source failed ({c_py}): {e_py or o_py}"
    assert c_ts == 0, f"opendvctl attach-source failed ({c_ts}): {e_ts or o_ts}"
    assert r_ok.status_code in (200, 201), r_ok.text
    _assert_3transport_jcs_equal((o_py, e_py), (o_ts, e_ts), r_ok.json())

    bad_argv = [*argv[:-1], "--credentials-ref", "plaintext://secret", "--json"]
    c_py_b, _, e_py_b = track_c_ctx.cli.invoke_raw(bad_argv)
    c_ts_b, _, e_ts_b = track_c_ctx.ctl.invoke_raw(bad_argv)
    r_bad = track_c_ctx.rest.post(
        f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/sources/connectors",
        json={
            "adapter": "filesystem",
            "uri": str(track_c_ctx.csv_path),
            "credentials_ref": "plaintext://secret",
        },
    )
    assert r_bad.status_code == 400
    assert c_py_b == 40, f"Expected exit 40, got {c_py_b}: {e_py_b}"
    assert c_ts_b == 40, f"Expected exit 40, got {c_ts_b}: {e_ts_b}"


@pytest.mark.parity
def test_fibo_qa_3transport_jcs_parity_and_exit_codes_10_20(
    track_c_ctx: TrackCParityContext,
) -> None:
    """3-transport parity for `ontology studio fibo-qa` incl. exit 10 (422) and exit 20 (409)."""
    argv = [
        "ontology",
        "studio",
        "fibo-qa",
        "--session",
        track_c_ctx.session_id,
        "--question",
        "Which FIBO class aligns with institutional positions?",
        "--json",
    ]
    c_py, o_py, e_py = track_c_ctx.cli.invoke_raw(argv)
    c_ts, o_ts, e_ts = track_c_ctx.ctl.invoke_raw(argv)
    r_422 = track_c_ctx.rest.post(
        f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/fibo-qa",
        json={"question": "Which FIBO class aligns with institutional positions?"},
    )
    assert r_422.status_code == 422, r_422.text
    assert r_422.json().get("error_code") == "AGENT_NOT_CONFIGURED"
    assert c_py == 10, f"Expected exit 10 for 422 AGENT_NOT_CONFIGURED, got {c_py}: {e_py}"
    assert c_ts == 10, f"Expected exit 10 for 422 AGENT_NOT_CONFIGURED, got {c_ts}: {e_ts}"
    _assert_3transport_error_parity(
        (o_py, e_py), (o_ts, e_ts), r_422.json(), expected_error_code="AGENT_NOT_CONFIGURED"
    )

    import contextlib

    mgr = StudioSessionManager(studio_root=track_c_ctx.studio_root)
    supervisor = get_job_supervisor()
    stop_evt = threading.Event()
    active_job = supervisor.submit_job(
        mgr,
        track_c_ctx.session_id,
        "construct",
        lambda *_a, **_kw: stop_evt.wait(timeout=30.0),
    )
    try:
        c_py_20, o_py_20, e_py_20 = track_c_ctx.cli.invoke_raw(argv)
        c_ts_20, o_ts_20, e_ts_20 = track_c_ctx.ctl.invoke_raw(argv)
        r_409 = track_c_ctx.rest.post(
            f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/fibo-qa",
            json={"question": "Which FIBO class aligns with institutional positions?"},
        )
        assert r_409.status_code == 409, r_409.text
        assert r_409.json().get("error_code") == "SESSION_JOB_ACTIVE"
        assert c_py_20 == 20, f"Expected exit 20 for 409 SESSION_JOB_ACTIVE, got {c_py_20}"
        assert c_ts_20 == 20, f"Expected exit 20 for 409 SESSION_JOB_ACTIVE, got {c_ts_20}"
        _assert_3transport_error_parity(
            (o_py_20, e_py_20),
            (o_ts_20, e_ts_20),
            r_409.json(),
            expected_error_code="SESSION_JOB_ACTIVE",
        )
    finally:
        stop_evt.set()
        with contextlib.suppress(Exception):
            supervisor.cancel_job(mgr, track_c_ctx.session_id, active_job["job_id"])


@pytest.mark.parity
def test_studio_logs_stage_filtering_and_follow_sse_parity(
    track_c_ctx: TrackCParityContext,
) -> None:
    """3-transport parity for `ontology studio logs` (STAGE_00..07, 00..06 aliases, --follow)."""
    stage_pairs = [
        ("STAGE_00_ORCHESTRATION", "00"),
        ("STAGE_03_SHIP_VAULT_PASS1", "03"),
        ("STAGE_07_DETERMINISTIC_COMPILER", "05"),
    ]
    for canonical_stage, pill_alias in stage_pairs:
        for stage_arg in (canonical_stage, pill_alias):
            argv = [
                "ontology",
                "studio",
                "logs",
                "--session-id",
                track_c_ctx.session_id,
                "--stage",
                stage_arg,
                "--json",
            ]
            c_py, o_py, e_py = track_c_ctx.cli.invoke_raw(argv)
            c_ts, o_ts, e_ts = track_c_ctx.ctl.invoke_raw(argv)
            r_resp = track_c_ctx.rest.get(
                f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/logs",
                params={"stage_id": stage_arg},
            )
            assert c_py == 0, f"opendv logs ({stage_arg}) failed: {e_py or o_py}"
            assert c_ts == 0, f"opendvctl logs ({stage_arg}) failed: {e_ts or o_ts}"
            assert r_resp.status_code == 200, r_resp.text
            assert r_resp.json()["entries"], f"Expected non-empty logs for stage={stage_arg}"
            _assert_3transport_jcs_equal((o_py, e_py), (o_ts, e_ts), r_resp.json())

    with track_c_ctx.rest.stream(
        "GET",
        f"/api/v1/ontology/studio/sessions/{track_c_ctx.session_id}/logs/stream",
        params={"once": "true"},
        headers={"Accept": "text/event-stream"},
    ) as sse_resp:
        assert sse_resp.status_code == 200
        assert "text/event-stream" in sse_resp.headers.get("content-type", "")
        raw_sse = "".join(sse_resp.iter_text())
        assert "event: log_entry" in raw_sse
        assert "STAGE_00_ORCHESTRATION" in raw_sse

    follow_argv = [
        "ontology",
        "studio",
        "logs",
        "--session",
        track_c_ctx.session_id,
        "--stage",
        "03",
        "--follow",
        "--json",
    ]
    c_py_f, o_py_f, e_py_f = track_c_ctx.cli.invoke_raw(follow_argv)
    c_ts_f, o_ts_f, e_ts_f = track_c_ctx.ctl.invoke_raw(follow_argv)
    assert c_py_f == 0, f"opendv logs --follow failed: {e_py_f or o_py_f}"
    assert c_ts_f == 0, f"opendvctl logs --follow failed: {e_ts_f or o_ts_f}"
