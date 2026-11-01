"""Smoke test for opendv-core."""

from opendv_core import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
