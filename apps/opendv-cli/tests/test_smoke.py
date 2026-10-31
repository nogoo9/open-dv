"""Smoke test for opendv-cli."""

from opendv_cli import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
