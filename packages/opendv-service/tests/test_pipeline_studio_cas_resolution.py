"""Tests for cross-layer Studio CAS resolution in pipeline preflight, state, and use cases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.pipeline.spec import (
    AgenticPipelineReport,
    DeterministicValidationReport,
    PipelineInputSpec,
)
from opendv_contracts.dtos.pipeline import PipelineInputDTO, PipelineRunRequest
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.vault_blueprint import create_vault_blueprint
from opendv_service.state import StateStore
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.use_cases.pipeline import execute_run_async
from opendv_service.use_cases.pipeline_preflight import (
    _extract_cas_hash,
    resolve_cas_sources_list,
    resolve_cas_unstructured_docs_list,
)


def _make_studio_session_with_files(
    studio_root: Path, session_id: str
) -> tuple[StudioSessionManager, str, str, str]:
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")
    cas = mgr.get_cas(session_id)

    # 1. SQLite source in Studio CAS
    db_file = studio_root / f"{session_id}_src.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE positions (position_id TEXT PRIMARY KEY, market_val REAL)")
    conn.execute("INSERT INTO positions VALUES ('POS-1', 12500.50)")
    conn.commit()
    conn.close()
    sqlite_sha = cas.put(db_file.read_bytes())

    # 2. CSV source in Studio CAS
    csv_sha = cas.put(b"account_id,currency\nACC-1,USD\n")

    # 3. Markdown policy document in Studio CAS
    md_sha = cas.put(b"# Custody SLA\n\nSettlement cutoff is T+1 at 16:00 EST.\n")
    return mgr, sqlite_sha, csv_sha, md_sha


def test_resolve_cas_sources_list_resolves_studio_cas_when_cas_store_is_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Studio session CAS blobs (sharded inputs/cas) resolve even when cas_store is None."""
    studio_root = tmp_path / "studio"
    session_id = "ses-cas-preflight-1"
    _, sqlite_sha, csv_sha, _ = _make_studio_session_with_files(studio_root, session_id)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    sources = [
        {"feed_id": "src-sqlite", "source_system": "holdings", "uri": f"cas://{sqlite_sha}"},
        {"feed_id": "src-csv", "source_system": "accounts", "uri": f"cas://{csv_sha}"},
    ]
    resolved = resolve_cas_sources_list(sources, None, session_id=session_id)
    assert len(resolved) == 2
    assert resolved[0]["uri"].startswith("sqlite:///")
    assert resolved[0]["adapter"] == "sqlite"
    assert Path(resolved[0]["uri"].removeprefix("sqlite:///")).is_file()
    assert not resolved[1]["uri"].startswith("cas://")
    assert Path(resolved[1]["uri"]).is_file()


def test_resolve_cas_unstructured_docs_uses_chunks_jsonl_and_ingestion_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstructured CAS docs resolve path and hydrate content from chunks.jsonl or ingestion."""
    studio_root = tmp_path / "studio"
    session_id = "ses-cas-unstr-1"
    mgr, _, _, md_sha = _make_studio_session_with_files(studio_root, session_id)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    # Case 1: without chunks.jsonl -> extracts via ingestion / UTF-8 fallback
    docs_no_chunks = [
        {
            "doc_id": "src-md-1",
            "document_path": f"cas://{md_sha}",
            "mime_type": "text/markdown",
            "metadata": {"title": "custody_sla.md", "source_sha256": md_sha},
        }
    ]
    resolved_direct = resolve_cas_unstructured_docs_list(
        docs_no_chunks, None, session_id=session_id
    )
    assert len(resolved_direct) == 1
    assert not str(resolved_direct[0]["document_path"]).startswith("cas://")
    assert Path(resolved_direct[0]["document_path"]).is_file()
    assert "Settlement cutoff is T+1" in resolved_direct[0]["content"]

    # Case 2: with evidence/chunks.jsonl -> prefers parsed chunks for that source_sha256
    sdir = mgr.get_session_dir(session_id)
    ev_dir = sdir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    chunk_record = {
        "chunk_id": "chk-parsed-1",
        "document_id": "src-pdf-doc",
        "source_sha256": md_sha,
        "text": "Parsed chunk override text from SLA table.",
        "metadata": {"source_id": "src-pdf-doc", "source_sha256": md_sha},
    }
    (ev_dir / "chunks.jsonl").write_text(json.dumps(chunk_record) + "\n", encoding="utf-8")

    docs_with_chunks = [
        {
            "doc_id": "src-pdf-doc",
            "document_path": f"cas://{md_sha}",
            "mime_type": "application/pdf",
            "metadata": {"title": "sla.pdf", "source_sha256": md_sha},
        }
    ]
    resolved_chunked = resolve_cas_unstructured_docs_list(
        docs_with_chunks, None, session_id=session_id
    )
    assert resolved_chunked[0]["content"] == "Parsed chunk override text from SLA table."


def test_state_resolve_pipeline_raw_inputs_supports_direct_pipeline_run_request(
    tmp_path: Path,
) -> None:
    """StateStore._resolve_pipeline_raw_inputs accepts PipelineRunRequest without input_spec."""
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    studio_root = ws / ".opendv" / "ontology_studio"
    session_id = "ses-ws-studio-1"
    _, sqlite_sha, _, md_sha = _make_studio_session_with_files(studio_root, session_id)

    state = StateStore(workspace_root=ws, state_dir=state_dir)
    ont = create_genesis_ontology("wealth_mgmt", "EA")
    bp = create_vault_blueprint(domain_namespace="wealth_mgmt", ontology=ont)

    run_req = PipelineRunRequest(
        session_id=session_id,
        blueprint=bp.model_dump(mode="json"),
        sources=[{"feed_id": "f1", "source_system": "holdings", "uri": f"cas://{sqlite_sha}"}],
        unstructured_documents=[
            {
                "doc_id": "d1",
                "document_path": f"cas://{md_sha}",
                "mime_type": "text/markdown",
                "metadata": {"title": "sla.md"},
            }
        ],
        unstructured_only=False,
    )
    spec_obj, _, _ = state._resolve_pipeline_inputs(run_req)
    assert isinstance(spec_obj, PipelineInputSpec)
    assert (spec_obj.sources[0].uri or "").startswith("sqlite:///")
    assert Path(spec_obj.unstructured_documents[0].document_path or "").is_file()
    assert "Settlement cutoff" in (spec_obj.unstructured_documents[0].content or "")


@pytest.mark.anyio
async def test_execute_run_async_resolves_studio_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_run_async resolves Studio cas:// refs for both sources and unstructured_documents."""
    import asyncio

    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    studio_root = state_dir / "ontology_studio"
    session_id = "ses-async-cas-1"
    _, sqlite_sha, _, md_sha = _make_studio_session_with_files(studio_root, session_id)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))

    ont = create_genesis_ontology("wealth_mgmt", "EA")
    bp = create_vault_blueprint(domain_namespace="wealth_mgmt", ontology=ont)
    captured_specs: list[PipelineInputSpec] = []

    def mock_runner(
        spec: PipelineInputSpec,
        *args: Any,
        **kwargs: Any,
    ) -> AgenticPipelineReport:
        captured_specs.append(spec)
        return AgenticPipelineReport(
            run_id=spec.run_id or "run-cas-1",
            pipeline_id="pipe-cas",
            project_id="proj-cas",
            domain_namespace="wealth_mgmt",
            status="COMPLETED",
            overall_passed=True,
            blueprint=bp,
            input_validation=DeterministicValidationReport(),
            output_validation=DeterministicValidationReport(),
        )

    state = StateStore(state_dir=state_dir, workspace_root=ws)
    dto = PipelineInputDTO(
        session_id=session_id,
        blueprint=bp.model_dump(mode="json"),
        sources=[{"feed_id": "f1", "source_system": "holdings", "uri": f"cas://{sqlite_sha}"}],
        unstructured_documents=[
            {
                "doc_id": "d1",
                "document_path": f"cas://{md_sha}",
                "mime_type": "text/markdown",
                "metadata": {"title": "sla.md"},
            }
        ],
    )
    handle = await execute_run_async(state, dto, pipeline_runner=mock_runner)
    assert handle.run_id
    for _ in range(40):
        if captured_specs:
            break
        await asyncio.sleep(0.05)
    assert any(
        s.sources
        and (s.sources[0].uri or "").startswith("sqlite:///")
        and s.unstructured_documents
        and "Settlement cutoff" in (s.unstructured_documents[0].content or "")
        for s in captured_specs
    )


def test_extract_cas_hash_and_resolve_reject_parent_traversal(tmp_path: Path) -> None:
    """Malformed or parent-traversal cas:// URIs are rejected and never probe cas_base.parent."""

    class _CasStoreStub:
        def __init__(self, base_dir: Path) -> None:
            self.base_dir = base_dir

    cas_base = tmp_path / "cas_root"
    cas_base.mkdir(parents=True, exist_ok=True)
    parent_secret = tmp_path / "somefile"
    parent_secret.write_text("SECRET_PARENT_DATA", encoding="utf-8")

    assert _extract_cas_hash("cas://..somefile") is None
    assert _extract_cas_hash("cas://../somefile") is None
    assert _extract_cas_hash("cas://not-a-valid-sha256") is None
    assert _extract_cas_hash("cas://" + ("A" * 64)) == "a" * 64

    stub = _CasStoreStub(cas_base)
    resolved_sources = resolve_cas_sources_list(
        [{"feed_id": "bad-1", "source_system": "holdings", "uri": "cas://..somefile"}],
        stub,
    )
    assert resolved_sources[0]["uri"] == "cas://..somefile"

    resolved_docs = resolve_cas_unstructured_docs_list(
        [{"doc_id": "bad-doc", "document_path": "cas://..somefile", "mime_type": "text/plain"}],
        stub,
    )
    assert resolved_docs[0]["document_path"] == "cas://..somefile"
    assert "content" not in resolved_docs[0]
