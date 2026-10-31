"""Smoke test for opendv-platform-api."""

from opendv_platform_api import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
