"""Smoke test for opendv-service package."""

from __future__ import annotations

import opendv_service
from opendv_service import OpenDVService, __version__


def test_smoke_imports() -> None:
    """Verify package metadata and clean importability."""
    assert __version__ == "0.1.0"
    assert hasattr(opendv_service, "OpenDVService")
    service = OpenDVService()
    assert service is not None
    assert service.state is not None
