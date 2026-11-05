"""Self-verification unit tests for `opendv-testkit` package."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from opendv_service.state import StateStore
from opendv_testkit import (
    DeterministicHarnessAdapter,
    build_deterministic_agy_sdk_shim,
    build_deterministic_claude_sdk_shim,
    build_deterministic_pipeline_runner,
    build_pe_canonical_events,
    create_ephemeral_project,
    create_ephemeral_run,
    deterministic_pipeline_seam,
    seed_canonical_pe_demo,
    synthesize_minimal_schema_instance,
)


class SampleSchema(BaseModel):
    name: str
    count: int
    tags: list[str]


def test_testkit_exports_present() -> None:
    """Verify all expected public symbols are exported."""
    assert callable(DeterministicHarnessAdapter)
    assert callable(build_deterministic_agy_sdk_shim)
    assert callable(build_deterministic_claude_sdk_shim)
    assert callable(build_deterministic_pipeline_runner)
    assert callable(build_pe_canonical_events)
    assert callable(create_ephemeral_project)
    assert callable(create_ephemeral_run)
    assert callable(deterministic_pipeline_seam)
    assert callable(seed_canonical_pe_demo)
    assert callable(synthesize_minimal_schema_instance)


def test_schema_synthesis() -> None:
    """Verify minimal schema instance synthesis."""
    inst = synthesize_minimal_schema_instance(SampleSchema)
    assert isinstance(inst, SampleSchema)
    assert inst.name == "deterministic"
    assert inst.count == 1
    assert inst.tags == []


@pytest.mark.asyncio
async def test_agy_sdk_shim() -> None:
    """Verify Antigravity SDK shim in-memory chat and thoughts."""
    shim = build_deterministic_agy_sdk_shim()
    cfg = shim.LocalAgentConfig(model="gemini-4-argon-high", response_schema=SampleSchema)
    async with shim.Agent(cfg) as agent:
        resp = await agent.chat("test prompt")
        thoughts = [t async for t in resp.thoughts]
        assert len(thoughts) == 1
        assert "gemini-4-argon-high" in thoughts[0]
        struct = await resp.structured_output()
        assert isinstance(struct, SampleSchema)


@pytest.mark.asyncio
async def test_claude_sdk_shim() -> None:
    """Verify Claude SDK shim returns ResultMessage with structured output."""
    shim = build_deterministic_claude_sdk_shim()
    opt = shim.ClaudeAgentOptions(
        model="claude-opus-5-5-high",
        output_format={"type": "json_schema", "schema": SampleSchema.model_json_schema()},
    )
    async with shim.ClaudeSDKClient(options=opt) as client:
        await client.query("test query")
        messages = [m async for m in client.receive_response()]
        assert len(messages) == 2
        result_msg = messages[1]
        assert hasattr(result_msg, "structured_output")


def test_seed_canonical_pe_demo(tmp_path: Path) -> None:
    """Verify seed_canonical_pe_demo populates an empty StateStore."""
    store = StateStore(state_dir=tmp_path / "state")
    assert len(store.projects) == 0
    seed_canonical_pe_demo(store)
    assert "proj-apex-horizon-pe" in store.projects
    assert "run-pe-apex-horizon-20261030" in store.runs
    assert len(store.run_events["run-pe-apex-horizon-20261030"]) >= 9
