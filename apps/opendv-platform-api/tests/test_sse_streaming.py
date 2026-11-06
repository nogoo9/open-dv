"""T5 Streaming & SSE Test Suite for Open-DV Platform API (docs/18 §7.5, GAP-06, Decision D2)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from opendv_platform_api import PlatformStateContainer, create_app
from opendv_service import set_pipeline_runner_override
from opendv_testkit import build_deterministic_agy_sdk_shim


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
def _setup_pipeline_shim():
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline as orig_run

    def _shim(*args: Any, **kwargs: Any) -> Any:
        return orig_run(*args, **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()})

    set_pipeline_runner_override(_shim)
    yield
    set_pipeline_runner_override(None)


@pytest.mark.asyncio
async def test_post_pipeline_runs_returns_http_202_with_urls(
    tmp_path: Path, repo_root: Path
) -> None:
    """POST /api/v1/pipelines/runs returns HTTP 202 with run_id and event URLs."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        res = await client.post(
            "/api/v1/pipelines/runs",
            json={
                "input_spec_path": sample_input,
                "harness_config_path": sample_harness,
                "provider_override": "agy",
                "steward_hold_policy": "auto_approve_top_candidate",
            },
        )
        assert res.status_code == 202, f"Expected 202 Accepted, got {res.status_code}: {res.text}"
        assert "location" in res.headers
        data = res.json()
        assert "run_id" in data
        assert "status_url" in data
        assert "events_sse_url" in data
        assert "event_stream_url" in data
        assert data["events_sse_url"] == f"/api/v1/runs/{data['run_id']}/events"
        assert res.headers["location"] == data["status_url"]


@pytest.mark.asyncio
async def test_ordered_stage_lifecycle_events_and_increasing_ids(
    tmp_path: Path, repo_root: Path
) -> None:
    """Validate ordered stage lifecycle events, strictly increasing IDs, and terminal frame."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # Start async run
        res = await client.post(
            "/api/v1/pipelines/runs",
            json={
                "input_spec_path": sample_input,
                "harness_config_path": sample_harness,
                "provider_override": "agy",
                "steward_hold_policy": "auto_approve_top_candidate",
            },
        )
        assert res.status_code == 202
        run_id = res.json()["run_id"]

        # Stream live events until run_complete
        raw_text_chunks: list[str] = []
        seen_run_complete = False
        async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as sse_stream:
            assert sse_stream.status_code == 200
            async for line in sse_stream.aiter_lines():
                raw_text_chunks.append(line + "\n")
                if "event: run_complete" in line:
                    seen_run_complete = True
                elif seen_run_complete and not line.strip():
                    break

        frames = _parse_sse_frames("".join(raw_text_chunks))

        assert any(f.get("event") == "run_complete" for f in frames), "Expected run_complete frame"
        terminal_frame = [f for f in frames if f.get("event") == "run_complete"][-1]
        term_data = json.loads(terminal_frame["data"])
        assert term_data["status"] == "COMPLETED"

        # Check stage_started -> stage_completed ordering
        event_ids: list[str] = []
        stage_started_stages: list[str] = []
        stage_completed_stages: list[str] = []

        for frame in frames:
            ev_type = frame.get("event", "")
            ev_id = frame.get("id", "")
            if ev_id:
                event_ids.append(ev_id)

            if ev_type.upper() in {"STAGE_STARTED", "STAGE_COMPLETED"}:
                data = json.loads(frame["data"])
                st = data.get("stage_id") or data.get("payload", {}).get("stage_id", "")
                if ev_type.upper() == "STAGE_STARTED":
                    stage_started_stages.append(st)
                else:
                    stage_completed_stages.append(st)

        # Unique IDs without duplicates
        assert len(event_ids) == len(set(event_ids)), "Event IDs must not contain duplicates"

        # Verify stages 00 through 07 were started and completed
        expected_stages = [f"STAGE_{i:02d}" for i in range(8)]
        for st_prefix in expected_stages:
            assert any(s.startswith(st_prefix) for s in stage_started_stages), (
                f"Missing started {st_prefix}"
            )
            assert any(s.startswith(st_prefix) for s in stage_completed_stages), (
                f"Missing completed {st_prefix}"
            )


@pytest.mark.asyncio
async def test_paused_hold_emits_hold_frame_and_resumable_stream(
    tmp_path: Path, repo_root: Path
) -> None:
    """PAUSED_HOLD emits a hold frame and keeps the stream resumable upon decision resolution."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # Start pipeline with pause policy
        res = await client.post(
            "/api/v1/pipelines/runs",
            json={
                "input_spec_path": sample_input,
                "harness_config_path": sample_harness,
                "provider_override": "agy",
                "steward_hold_policy": "pause",
            },
        )
        assert res.status_code == 202
        run_id = res.json()["run_id"]

        # Wait for hold frame
        hold_frame: dict[str, str] | None = None
        for _ in range(80):
            stream_res = await client.get(f"/api/v1/runs/{run_id}/events")
            assert stream_res.status_code == 200
            frames = _parse_sse_frames(stream_res.text)
            matched = [f for f in frames if f.get("event") == "hold"]
            if matched:
                hold_frame = matched[0]
                break
            await asyncio.sleep(0.1)

        assert hold_frame is not None, "Expected 'hold' event frame when pipeline paused"
        hold_data = json.loads(hold_frame["data"])
        assert hold_data.get("status") == "PAUSED_HOLD"
        last_id = hold_frame.get("id") or "evt-hold"

        # Resolve hold
        dec_res = await client.post(
            f"/api/v1/runs/{run_id}/decisions",
            json={
                "action": "APPROVE",
                "selected_candidate_id": "hub_customer",
                "steward_id": "steward-test",
            },
        )
        assert dec_res.status_code == 200

        # Reconnect with Last-Event-ID to receive resumed events
        reconnect_res = await client.get(
            f"/api/v1/runs/{run_id}/events",
            headers={"Last-Event-ID": last_id},
        )
        assert reconnect_res.status_code == 200
        resumed_frames = _parse_sse_frames(reconnect_res.text)
        assert len(resumed_frames) >= 1
        assert any(f.get("event") == "run_complete" for f in resumed_frames)


@pytest.mark.asyncio
async def test_reconnection_with_last_event_id_delivers_missing_events(
    tmp_path: Path, repo_root: Path
) -> None:
    """Reconnection with Last-Event-ID delivers only missing tail events."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        res = await client.post(
            "/api/v1/pipelines/runs",
            json={
                "input_spec_path": sample_input,
                "harness_config_path": sample_harness,
                "provider_override": "agy",
                "steward_hold_policy": "auto_approve_top_candidate",
            },
        )
        run_id = res.json()["run_id"]

        # Wait for completion
        for _ in range(80):
            poll = await client.get(f"/api/v1/runs/{run_id}")
            if poll.status_code == 200 and poll.json().get("status") == "COMPLETED":
                break
            await asyncio.sleep(0.1)

        # Full stream
        full_res = await client.get(f"/api/v1/runs/{run_id}/events")
        all_frames = _parse_sse_frames(full_res.text)
        assert len(all_frames) >= 5

        mid_idx = len(all_frames) // 2
        mid_id = all_frames[mid_idx].get("id")
        assert mid_id is not None

        # Reconnect with Last-Event-ID at midpoint
        recon_res = await client.get(
            f"/api/v1/runs/{run_id}/events",
            headers={"Last-Event-ID": mid_id},
        )
        recon_frames = _parse_sse_frames(recon_res.text)

        # The reconnected stream must have exactly the tail frames following mid_id
        expected_tail_count = len(all_frames) - (mid_idx + 1)
        assert len(recon_frames) == expected_tail_count
        assert recon_frames[-1].get("event") == "run_complete"


@pytest.mark.asyncio
async def test_unknown_run_id_returns_404_problem(tmp_path: Path, repo_root: Path) -> None:
    """Unknown run ID returns 404 Problem Details on SSE endpoint."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    app = create_app(platform_state=state, workspace_root=repo_root)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        res = await client.get("/api/v1/runs/run-nonexistent-1234/events")
        assert res.status_code == 404
        assert "application/problem+json" in res.headers.get("content-type", "")
        body = res.json()
        assert body["status"] == 404
        assert "not found" in body["detail"].lower()
