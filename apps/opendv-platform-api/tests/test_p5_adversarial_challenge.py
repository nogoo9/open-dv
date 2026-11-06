"""Adversarial security and concurrency challenge suite for Phase P5 (GAP-05, GAP-06, GAP-07).

Challenge Scope:
1. Concurrency isolation across concurrent async runs (zero SSE/ledger interleaving).
2. SSE follower resumption with Last-Event-ID and slow consumer backpressure.
3. Hostile initial_contract_path traversal and YAML injection resistance.
4. 3-Level Explorer API validation and cross-run artifact access isolation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from opendv_platform_api import PlatformStateContainer, create_app
from opendv_service import (
    set_pipeline_runner_override,
)
from opendv_testkit import build_deterministic_agy_sdk_shim, seed_canonical_pe_demo


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
    """Ensure fast deterministic execution for pipeline runs."""
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline as orig_run

    def _shim(*args: Any, **kwargs: Any) -> Any:
        return orig_run(*args, **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()})

    set_pipeline_runner_override(_shim)
    yield
    set_pipeline_runner_override(None)


@pytest.fixture
def challenge_app(tmp_path: Path, repo_root: Path):
    """Construct an isolated Platform API application with state container."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = PlatformStateContainer(state_dir=state_dir, workspace_root=repo_root)
    seed_canonical_pe_demo(state)
    app = create_app(platform_state=state, workspace_root=repo_root)
    return app, state, repo_root


# ---------------------------------------------------------------------------
# Challenge 1: Async-only Pipeline Runs & SSE Concurrency (GAP-06, D2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_1a_async_only_all_shapes_return_202(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Verify POST /api/v1/pipelines/runs returns HTTP 202 with non-blocking handles."""
    app, _, _ = challenge_app
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    shapes = [
        {"input_spec_path": sample_input},
        {
            "input_spec_path": sample_input,
            "harness_config_path": sample_harness,
            "provider_override": "agy",
        },
        {
            "input_spec_path": sample_input,
            "wave_id": "W1",
            "steward_hold_policy": "auto_approve_top_candidate",
        },
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for idx, shape in enumerate(shapes):
            res = await client.post("/api/v1/pipelines/runs", json=shape)
            assert res.status_code == 202, f"Shape {idx} failed with {res.status_code}: {res.text}"
            body = res.json()
            assert "run_id" in body and body["run_id"].startswith("run-")
            assert "status_url" in body and body["status_url"].startswith("/api/v1/runs/")
            assert "events_sse_url" in body and body["events_sse_url"].endswith("/events")
            assert "event_stream_url" in body
            assert res.headers.get("location") == body["status_url"]


@pytest.mark.asyncio
async def test_challenge_1b_concurrent_runs_zero_interleaving(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Run 2 concurrent pipeline runs and verify zero SSE or ledger cross-talk."""
    app, state, _ = challenge_app
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60.0
    ) as client:
        # 1. Start Run A and Run B concurrently
        res_a, res_b = await asyncio.gather(
            client.post(
                "/api/v1/pipelines/runs",
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "agy",
                    "steward_hold_policy": "auto_approve_top_candidate",
                },
            ),
            client.post(
                "/api/v1/pipelines/runs",
                json={
                    "input_spec_path": sample_input,
                    "harness_config_path": sample_harness,
                    "provider_override": "agy",
                    "steward_hold_policy": "auto_approve_top_candidate",
                },
            ),
        )
        assert res_a.status_code == 202
        assert res_b.status_code == 202
        run_a_id = res_a.json()["run_id"]
        run_b_id = res_b.json()["run_id"]
        assert run_a_id != run_b_id

        # 2. Concurrently consume SSE streams for both runs
        async def _consume_stream(run_id: str) -> list[dict[str, str]]:
            frames: list[dict[str, str]] = []
            async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as s_res:
                assert s_res.status_code == 200
                async for chunk in s_res.aiter_text():
                    for f in _parse_sse_frames(chunk):
                        frames.append(f)
                        if f.get("event") == "run_complete":
                            return frames
            return frames

        frames_a, frames_b = await asyncio.gather(
            _consume_stream(run_a_id),
            _consume_stream(run_b_id),
        )

        assert len(frames_a) >= 8, f"Stream A too short: {len(frames_a)}"
        assert len(frames_b) >= 8, f"Stream B too short: {len(frames_b)}"

        # 3. Assert zero interleaving in SSE streams
        for f in frames_a:
            if "data" in f:
                import json

                try:
                    payload = json.loads(f["data"])
                    if isinstance(payload, dict) and "run_id" in payload:
                        assert payload["run_id"] == run_a_id, (
                            f"LEAK: Stream A contains event from {payload['run_id']}"
                        )
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

        for f in frames_b:
            if "data" in f:
                import json

                try:
                    payload = json.loads(f["data"])
                    if isinstance(payload, dict) and "run_id" in payload:
                        assert payload["run_id"] == run_b_id, (
                            f"LEAK: Stream B contains event from {payload['run_id']}"
                        )
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

        # 4. Assert zero interleaving in persisted run ledgers / state
        events_a = state.run_events.get(run_a_id, [])
        events_b = state.run_events.get(run_b_id, [])
        assert all(ev.get("run_id") == run_a_id for ev in events_a)
        assert all(ev.get("run_id") == run_b_id for ev in events_b)


@pytest.mark.asyncio
async def test_challenge_1c_sse_follower_resume_last_event_id(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Disconnect follower and resume with Last-Event-ID; verify exact resumption."""
    app, _, _ = challenge_app
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60.0
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
        assert res.status_code == 202
        run_id = res.json()["run_id"]

        # Read first 3 frames then disconnect
        first_batch: list[dict[str, str]] = []
        async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream1:
            assert stream1.status_code == 200
            async for chunk in stream1.aiter_text():
                for f in _parse_sse_frames(chunk):
                    first_batch.append(f)
                    if len(first_batch) == 3:
                        break
                if len(first_batch) >= 3:
                    break

        assert len(first_batch) == 3
        last_seen_id = first_batch[-1].get("id")
        assert last_seen_id is not None, f"Frame 3 missing id: {first_batch[-1]}"

        # Resume stream using Last-Event-ID header
        resumed_batch: list[dict[str, str]] = []
        async with client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/events",
            headers={"Last-Event-ID": last_seen_id},
        ) as stream2:
            assert stream2.status_code == 200
            async for chunk in stream2.aiter_text():
                for f in _parse_sse_frames(chunk):
                    resumed_batch.append(f)
                    if f.get("event") == "run_complete":
                        break
                if resumed_batch and resumed_batch[-1].get("event") == "run_complete":
                    break

        # Verification: none of the initial 3 IDs should appear in resumed stream
        initial_ids = {f.get("id") for f in first_batch if f.get("id")}
        resumed_ids = [f.get("id") for f in resumed_batch if f.get("id")]

        assert not initial_ids.intersection(resumed_ids), (
            f"Duplicated events on resume! Overlap: {initial_ids.intersection(resumed_ids)}"
        )
        assert resumed_batch[-1].get("event") == "run_complete"

        # Also test resume via query parameter ?last_event_id=
        query_resumed_batch: list[dict[str, str]] = []
        async with client.stream(
            "GET",
            f"/api/v1/runs/{run_id}/events?last_event_id={last_seen_id}",
        ) as stream3:
            assert stream3.status_code == 200
            async for chunk in stream3.aiter_text():
                for f in _parse_sse_frames(chunk):
                    query_resumed_batch.append(f)
                    if f.get("event") == "run_complete":
                        break
                if query_resumed_batch and query_resumed_batch[-1].get("event") == "run_complete":
                    break

        query_resumed_ids = [f.get("id") for f in query_resumed_batch if f.get("id")]
        assert query_resumed_ids == resumed_ids


@pytest.mark.asyncio
async def test_challenge_1d_slow_consumer_backpressure_and_abrupt_disconnect(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Verify slow consumer delays do not deadlock, and abrupt close cleans up."""
    app, _, _ = challenge_app
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60.0
    ) as client:
        # Start a pipeline run
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

        # Abrupt disconnect after 1 frame
        async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream:
            assert stream.status_code == 200
            async for chunk in stream.aiter_text():
                frames = _parse_sse_frames(chunk)
                if frames:
                    break

        # Slow consumer reading remaining frames with artificial sleep
        slow_frames: list[dict[str, str]] = []
        async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream:
            assert stream.status_code == 200
            async for chunk in stream.aiter_text():
                await asyncio.sleep(0.02)  # slow consumer delay
                for f in _parse_sse_frames(chunk):
                    slow_frames.append(f)
                    if f.get("event") == "run_complete":
                        break
                if slow_frames and slow_frames[-1].get("event") == "run_complete":
                    break

        assert any(f.get("event") == "run_complete" for f in slow_frames)


# ---------------------------------------------------------------------------
# Challenge 2: Run Creation & Path Traversal Injection (GAP-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_2a_hostile_initial_contract_path_traversal(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Hostile path traversal, null bytes, and out-of-bounds contract paths."""
    app, _, _ = challenge_app

    hostile_paths = [
        "../../../../etc/shadow",
        "../../../../windows/system32/cmd.exe",
        "foo\x00bar.yaml",
        "C:/Windows/System32/drivers/etc/hosts",
        "/etc/passwd",
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Test on POST /api/v1/runs
        for hostile_path in hostile_paths:
            res = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": "default-project",
                    "initial_contract_path": hostile_path,
                },
            )
            assert res.status_code == 400, (
                f"Path '{hostile_path}' on /runs was not rejected with HTTP 400"
            )
            body = res.json()
            assert body.get("status") == 400
            assert "detail" in body

        sample_spec = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
        for hostile_path in hostile_paths:
            res = await client.post(
                "/api/v1/pipelines/runs",
                json={
                    "input_spec_path": sample_spec,
                    "initial_contract_path": hostile_path,
                },
            )
            assert res.status_code == 400, (
                f"Path '{hostile_path}' on /pipelines/runs was not rejected with HTTP 400"
            )
            body = res.json()
            assert body.get("status") == 400


@pytest.mark.asyncio
async def test_challenge_2b_unreadable_and_missing_contract_path_behavior(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Evaluate unreadable and missing initial_contract_path parameters.

    Verify non-existent files and directories return HTTP 400 Problem Details.
    """
    app, _, _ = challenge_app

    unreadable_paths = [
        "nonexistent/contract.yaml",
        "examples",  # directory passed as file
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        for p in unreadable_paths:
            res = await client.post(
                "/api/v1/runs",
                json={"project_id": "default-project", "initial_contract_path": p},
            )
            assert res.status_code == 400, f"Expected 400, got {res.status_code}"


@pytest.mark.asyncio
async def test_challenge_2c_inline_yaml_code_execution_prevention(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Verify inline contract with constructor injection does not execute code.

    Verify PyYAML SafeLoader blocks execution and returns HTTP 400 Problem Details.
    """
    app, _, _ = challenge_app

    payloads = [
        "!!python/object/apply:os.system ['echo PWNED']",
        "!!python/module:subprocess ()",
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        for payload in payloads:
            res = await client.post(
                "/api/v1/runs",
                json={
                    "project_id": "default-project",
                    "initial_contract": payload,
                },
            )
            assert res.status_code == 400


@pytest.mark.asyncio
async def test_challenge_2d_inline_yaml_syntax_and_size_limits(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Verify handling of malformed YAML syntax and oversized inline payloads."""
    app, _, _ = challenge_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        # Malformed YAML returns 400
        res_malformed = await client.post(
            "/api/v1/runs",
            json={"project_id": "default-project", "initial_contract": "key: [unclosed list"},
        )
        assert res_malformed.status_code == 400

        # Large inline string (5 MB payload) returns 422
        large_yaml = "domain: TestLarge\ncomments: " + ("x" * (5 * 1024 * 1024)) + "\n"
        res_large = await client.post(
            "/api/v1/runs",
            json={"project_id": "default-project", "initial_contract": large_yaml},
        )
        assert res_large.status_code == 422


# ---------------------------------------------------------------------------
# Challenge 3: Explorer API & Cross-Run Isolation (GAP-05, T6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_3a_explorer_api_unknown_runs_and_invalid_waves(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: GET /runs/{id}/explorer with unknown ID or invalid waves returns RFC 9457 404."""
    app, _, _ = challenge_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Unknown run ID must 404
        res_unknown = await client.get("/api/v1/runs/unknown-run-9999/explorer")
        assert res_unknown.status_code == 404
        body = res_unknown.json()
        assert body["status"] == 404
        assert "not found" in body["detail"].lower()

        # Start a valid run to test wave parameter validation
        res_create = await client.post(
            "/api/v1/runs",
            json={"project_id": "default-project", "harness_provider": "antigravity"},
        )
        assert res_create.status_code == 202
        valid_run_id = res_create.json()["run_id"]

        # Valid wave
        res_w1 = await client.get(f"/api/v1/runs/{valid_run_id}/explorer?wave_id=W1")
        assert res_w1.status_code == 200

        # Invalid waves must return 404
        for invalid_wave in ["W99", "invalid", "../../traversal", "-1", "W4"]:
            res_inv = await client.get(
                f"/api/v1/runs/{valid_run_id}/explorer?wave_id={invalid_wave}"
            )
            assert res_inv.status_code == 404, (
                f"Expected 404 for invalid wave '{invalid_wave}', got {res_inv.status_code}"
            )


@pytest.mark.asyncio
async def test_challenge_3b_cross_run_artifact_isolation(
    challenge_app: tuple[Any, PlatformStateContainer, Path],
) -> None:
    """Challenge: Verify strict run-isolation: non-existent artifact or unknown run 404s."""
    app, _, _ = challenge_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Create Run A and Run B
        res_a = await client.post(
            "/api/v1/runs",
            json={"project_id": "default-project", "harness_provider": "antigravity"},
        )
        res_b = await client.post(
            "/api/v1/runs",
            json={"project_id": "default-project", "harness_provider": "antigravity"},
        )
        assert res_a.status_code == 202
        assert res_b.status_code == 202
        run_a = res_a.json()["run_id"]
        run_b = res_b.json()["run_id"]

        # Valid artifact under Run A
        res_art_a = await client.get(f"/api/v1/runs/{run_a}/artifacts/harness")
        assert res_art_a.status_code == 200
        art_a = res_art_a.json()
        assert art_a["run_id"] == run_a

        # Requesting an artifact ID that does not exist under Run B must 404
        res_missing = await client.get(f"/api/v1/runs/{run_b}/artifacts/custom_run_a_only")
        assert res_missing.status_code == 404

        # Requesting Run A's artifact under a completely non-existent run ID must 404
        res_ghost_run = await client.get("/api/v1/runs/ghost-run-xyz/artifacts/harness")
        assert res_ghost_run.status_code == 404
        assert res_ghost_run.json()["status"] == 404
