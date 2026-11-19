"""Phase 2 Track B TDD Tests: EvidenceRef Metadata, Citation Resolution, FIBO Lookup & Ask FIBO Q&A.

Covers:
- FR-CIT-01: EvidenceRef optional fields (source_label, chunk_id, page_number, table_id, fibo_uri)
  and option-level evidence_citations on ClarificationOption.
- FR-CIT-02: POST /api/v1/ontology/studio/sessions/{id}/citations/resolve batch resolution.
- FR-CIT-03: GET /api/v1/ontology/studio/fibo/lookup returning fibo-core definitions & hierarchy.
- FR-CIT-04 / D2: POST /api/v1/ontology/studio/sessions/{id}/fibo-qa returning cited answer + SuggestedDeltaActions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness.models import (
    AgentTaskSpec,
    HarnessConfig,
    HarnessRunResult,
    HarnessStopReason,
    HarnessUsageMetrics,
    ModelTier,
)
from opendv_contracts.dtos.clarification import ClarificationOption
from opendv_contracts.dtos.ontology import EvidenceRef
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.studio.session_manager import StudioSessionManager


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    return studio_root


def _client(tmp_path: Path) -> TestClient:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


def test_evidence_ref_optional_fields_and_option_level_citations() -> None:
    """EvidenceRef supports source_label/chunk_id/page_number/table_id/fibo_uri and ClarificationOption carries evidence_citations."""
    ref = EvidenceRef(
        evidence_type="document_span",
        reference_uri="urn:doc:policy",
        snippet="All T+1 trades settle within 24 hours.",
        confidence=0.95,
        source_label="sla_policy.pdf",
        chunk_id="chk-101",
        page_number=3,
        table_id="tbl-sla",
        fibo_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/DebtInstrument",
    )
    assert ref.source_label == "sla_policy.pdf"
    assert ref.chunk_id == "chk-101"
    assert ref.page_number == 3
    assert ref.table_id == "tbl-sla"
    assert ref.fibo_uri is not None

    opt = ClarificationOption(
        option_id="opt-1",
        label="Separate Settlement Entity",
        rationale="Track SLA separately from Trade.",
        evidence_citations=[ref],
    )
    assert len(opt.evidence_citations) == 1
    assert opt.evidence_citations[0].chunk_id == "chk-101"


def test_citations_resolve_batch_endpoint(tmp_path: Path, isolated_env: Path) -> None:
    """POST /sessions/{id}/citations/resolve resolves document spans, table profiles, and fibo-core defs in one batch."""
    from opendv_service.studio.citation_resolver import resolve_session_citations

    client = _client(tmp_path)
    client.post("/api/v1/ontology/studio/sessions", json={"session_id": "cit-res-01", "domain_namespace": "custody_ops"})

    note_text = "Header line.\nSettlement SLA requires T+1 confirmation."
    client.post(
        "/api/v1/ontology/studio/sessions/cit-res-01/sources/text",
        json={"text": note_text, "source_name": "sla.md", "format": "markdown"},
    )
    client.post("/api/v1/ontology/studio/sessions/cit-res-01/parse", json={"ocr_mode": "off"})

    # Also create a small sqlite source
    sqlite_p = tmp_path / "workspace" / "cit.sqlite"
    conn = sqlite3.connect(str(sqlite_p))
    conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, qty INTEGER);")
    conn.execute("INSERT INTO trades VALUES ('T1', 100);")
    conn.commit()
    conn.close()
    client.post(
        "/api/v1/ontology/studio/sessions/cit-res-01/sources/connectors",
        json={"adapter": "sqlite", "uri": str(sqlite_p)},
    )

    fibo_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/Agents/AutomatedSystem"
    refs = [
        EvidenceRef(
            evidence_type="document_span",
            source_label="sla.md",
            char_start=13,
            char_end=53,
            snippet="Settlement SLA requires T+1 confirmation.",
        ),
        EvidenceRef(
            evidence_type="column_profile",
            bronze_table="trades",
            column_name="trade_id",
        ),
        EvidenceRef(
            evidence_type="standard_reference",
            fibo_uri=fibo_uri,
            reference_uri=fibo_uri,
        ),
    ]

    resp = client.post(
        "/api/v1/ontology/studio/sessions/cit-res-01/citations/resolve",
        json={"citations": [r.model_dump(mode="json", exclude_none=True) for r in refs]},
    )
    assert resp.status_code == 200, resp.text
    resolved = resp.json()["resolved"]
    assert len(resolved) == 3
    assert "Settlement SLA requires T+1 confirmation." in resolved[0]["highlighted_span"]
    assert resolved[1]["table_profile"] is not None
    assert resolved[2]["fibo_definition"] is not None
    assert callable(resolve_session_citations)


def test_fibo_lookup_endpoint(tmp_path: Path) -> None:
    """GET /api/v1/ontology/studio/fibo/lookup returns fibo-core definitions and parent hierarchy."""
    client = _client(tmp_path)
    resp = client.get("/api/v1/ontology/studio/fibo/lookup", params={"q": "FinancialInstrument"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["matches"]) >= 1
    first = body["matches"][0]
    assert "uri" in first and "label" in first and "definition" in first
    assert "parents" in first


def test_fibo_qa_service_and_endpoint_returns_cited_answer_and_suggested_actions(
    tmp_path: Path, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ask FIBO & Sources Q&A uses live harness, returns cited answer + SuggestedDeltaActions, and fails closed if unconfigured."""
    from opendv_service.studio.fibo_qa_service import FiboQARequest, FiboQAResponse, answer_fibo_qa

    class _SpyQaHarness:
        def __init__(self) -> None:
            self._cfg = HarnessConfig(
                provider="claude",
                workspace_dir=tmp_path,
                state_dir=tmp_path / "s",
                app_data_dir=tmp_path / "a",
                save_dir=tmp_path / "sv",
            )

        @property
        def provider_name(self) -> str:
            return "claude"

        @property
        def config(self) -> HarnessConfig:
            return self._cfg

        def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
            return "claude-sonnet-4-5"

        async def cancel_active_turn(self, task_id: str) -> None:
            return None

        async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
            if False:
                yield  # pragma: no cover

        async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
            qa_resp = FiboQAResponse(
                answer="Map settlement_sla to a custom concept with skos:broadMatch to FIBO Occurrence.",
                citations=[
                    EvidenceRef(
                        evidence_type="document_span",
                        source_label="sla.md",
                        snippet="T+1 confirmation",
                        confidence=0.92,
                    )
                ],
                suggested_actions=[
                    {
                        "action_id": "act-1",
                        "label": "Add Settlement SLA Concept",
                        "op_type": "add_concept",
                        "payload": {"concept_name": "Settlement SLA"},
                    }
                ],
            )
            return HarnessRunResult(
                task_id=spec.task_id,
                run_id=spec.run_id,
                session_id="qa-sess-01",
                trace_id=spec.trace_id,
                span_id="0123456789abcdef",
                provider="claude",
                model_used="claude-sonnet-4-5",
                stop_reason=HarnessStopReason.COMPLETED,
                text_output=qa_resp.model_dump_json(),
                usage=HarnessUsageMetrics(input_tokens=40, output_tokens=30, total_tokens=70, cost_usd=0.002),
                structured_output=qa_resp,
            )

    mgr = StudioSessionManager(studio_root=isolated_env, workspace_root=tmp_path / "workspace")
    mgr.create_session(session_id="qa-sess-01", domain_namespace="custody_ops")
    res = answer_fibo_qa(
        session_mgr=mgr,
        session_id="qa-sess-01",
        request=FiboQARequest(question="Should Settlement SLA map to FIBO?"),
        harness=_SpyQaHarness(),
    )
    assert len(res.citations) >= 1
    assert len(res.suggested_actions) >= 1

    # Unconfigured REST call fails closed with 422 AGENT_NOT_CONFIGURED
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENDV_AGENT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", "")
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ontology/studio/sessions/qa-sess-01/fibo-qa",
        json={"question": "Should Settlement SLA map to FIBO?"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error_code") == "AGENT_NOT_CONFIGURED"
