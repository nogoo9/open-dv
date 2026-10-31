"""Smoke test for opendv-axi-verifier."""

from opendv_axi_verifier import __version__


def test_smoke() -> None:
    assert __version__ == "0.1.0"
