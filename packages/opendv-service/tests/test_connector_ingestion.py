"""Tests for connector ingestion with real-world CSV headers (Bug C TDD)."""

from __future__ import annotations

from pathlib import Path

from opendv_service.studio.connector_ingestion import attach_connector_source
from opendv_service.studio.session_manager import StudioSessionManager


def test_attach_connector_source_profiles_csv_with_spaced_headers(tmp_path: Path) -> None:
    """Bug C TDD: attach_connector_source succeeds on a CSV with spaced headers."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    csv_path = ws_root / "ds05_clinical_trials.csv"
    csv_path.write_text(
        '"NCT Number","Study Title","Primary Outcome Measures"\n'
        '"NCT01234567","Phase II Study","Overall Survival"\n'
        '"NCT07654321","Phase III Study","Disease Free Survival"\n',
        encoding="utf-8",
    )

    mgr = StudioSessionManager(
        studio_root=ws_root / ".opendv" / "ontology_studio", workspace_root=ws_root
    )
    mgr.create_session("sess-bug-c")

    res = attach_connector_source(
        session_mgr=mgr,
        session_id="sess-bug-c",
        request_payload={
            "adapter": "file",
            "uri": str(csv_path),
            "source_name": "ds05_clinical_trials.csv",
        },
    )
    assert res["table_count"] == 1
    assert res["tables"]
