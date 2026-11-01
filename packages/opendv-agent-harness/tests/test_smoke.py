"""Smoke test for opendv-agent-harness."""

from opendv_agent_harness import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
