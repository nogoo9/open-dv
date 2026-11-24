"""UAT-V25-SVC-04 tests for JobMcpBroker (/coord vs /scout) and ExternalMcpGateway."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from opendv.agents.ports import AgentTurnRequest
from opendv.models import (
    AddAttribute,
    AddConcept,
    AddMcpEvidence,
    AddSourceCitation,
    AuditLogRecord,
    CanonicalBusinessConcept,
    ClarificationQuestion,
    ClientAdditiveOp,
    ColumnProfile,
    ConceptAttribute,
    ConceptMemorySpec,
    ConceptSummary,
    ConnectorConfigPayload,
    DeleteConcept,
    DocumentCitationRef,
    DocumentMap,
    ErrorCode,
    EvolutionLockSpec,
    ExternalMcpEvidenceRef,
    ExternalMcpReceipt,
    ExternalMcpServerSpec,
    FiboMatchSpec,
    FiboQueryReq,
    FiboQueryResp,
    GraphAOntology,
    HarnessBudget,
    OcrPageReceipt,
    OpenDVError,
    PageMapSpec,
    ProjectSettingsSnapshot,
    ProjectState,
    RescoreExternalMapping,
    RevisionMetadata,
    RunStreamEvent,
    ScoutDigest,
    SecretPrecedence,
    SetConceptKeyAuthority,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
    TableProfile,
    UpdateAttributeMetadata,
    sha256_jcs,
)
from opendv.service._ext_mcp_guards import scrub_outbound_arguments
from opendv.service._mcp_ports import (
    Bm25MatcherPort,
    OcrBox,
    ProjectStatePort,
    SecretStorePort,
    SettingsStorePort,
)
from opendv.service._mcp_tools import (
    coord_ask_domain_clarification,
    coord_dispatch_scout,
    coord_propose_ontology_delta,
    coord_search_concepts,
    scout_call_external_mcp,
    scout_inspect_table_cluster,
    scout_inspect_table_profile,
    scout_read_document_pages,
    scout_view_page_image,
)
from opendv.service.external_mcp import ExternalMcpGateway
from opendv.service.mcp_broker import JobMcpBroker
from opendv.service.state_store import ProjectStateManager, ServiceRunEventSink
from opendv.sources.document_map import DocumentMapBuilder
from tests.helpers.fake_external_mcp import LoopbackFakeExternalMcpServer
from tests.helpers.mock_harness import (
    DeterministicMockHarness,
    InMemoryRunEventSink,
    InMemoryScoutToolDispatcher,
    MockTurnScript,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CUSTODY_PDF = _REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"


class InMemoryBrokerStateStore(ProjectStatePort):
    """Lightweight in-memory ProjectStatePort double for M2B broker/gateway tests."""

    def __init__(self, project_id: str = "am-ibor-2026") -> None:
        now = datetime.now(tz=UTC).isoformat()
        self.project_id = project_id
        self.projects: dict[str, ProjectState] = {
            project_id: ProjectState(
                project_id=project_id,
                domain_namespace="asset_management",
                settings_snapshot=ProjectSettingsSnapshot(),
                has_active_lease=True,
                created_at=now,
                updated_at=now,
            )
        }
        self.sources: dict[str, dict[str, dict[str, Any]]] = {project_id: {}}
        self.revisions: dict[str, list[dict[str, Any]]] = {project_id: []}
        self.events: dict[str, list[dict[str, Any]]] = {project_id: []}
        self.stream_events: dict[str, list[RunStreamEvent]] = {project_id: []}
        self.released_leases: list[tuple[str, str, str | None]] = []

    def get_project(self, project_id: str) -> ProjectState:
        if project_id not in self.projects:
            raise OpenDVError(f"Missing {project_id}", error_code=ErrorCode.RESOURCE_NOT_FOUND)
        return self.projects[project_id]

    def add_source(
        self,
        *,
        source_id: str,
        kind: str,
        uri: str,
        status: SourceStatus,
        profile: SourceProfile,
        document_map: DocumentMap | None = None,
        tables_index: list[TableIndexEntry] = (),  # type: ignore[assignment]
        approved_scan_pages: list[int] = (),  # type: ignore[assignment]
        allow_unredacted_external_ocr: bool = False,
    ) -> None:
        self.sources[self.project_id][source_id] = {
            "source_id": source_id,
            "kind": kind,
            "uri_or_filename": uri,
            "binary_sha256": "a" * 64,
            "connector_config": ConnectorConfigPayload(
                adapter_id=kind,
                display_name=kind,
                uri_or_path=uri,
                options={"allow_unredacted_external_ocr": allow_unredacted_external_ocr},
            ),
            "status": status,
            "profile": profile,
            "document_map": document_map,
            "tables_index": list(tables_index),
            "approved_scan_pages": list(approved_scan_pages),
            "allow_unredacted_external_ocr": allow_unredacted_external_ocr,
            "attached_at": datetime.now(tz=UTC).isoformat(),
        }

    def get_source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        rec = self.sources.get(project_id, {}).get(source_id)
        if rec is None:
            raise OpenDVError(f"Missing source {source_id}", error_code=ErrorCode.RESOURCE_NOT_FOUND)
        return rec

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        out: list[SourceSummary] = []
        for rec in self.sources.get(project_id, {}).values():
            prof: SourceProfile = rec["profile"]
            dm: DocumentMap | None = rec["document_map"]
            out.append(
                SourceSummary(
                    source_id=rec["source_id"],
                    kind=rec["kind"],
                    uri_or_filename=rec["uri_or_filename"],
                    status=rec["status"],
                    table_count=prof.table_count,
                    page_count=dm.page_count if dm else None,
                    attached_at=rec["attached_at"],
                    connector_config=rec["connector_config"],
                )
            )
        return out

    def save_revision(
        self,
        project_id: str,
        *,
        revision_id: str,
        rev_kind: str,
        rev_key: str,
        parent_rev_id: str | None,
        payload: BaseModel,
        metadata: RevisionMetadata,
    ) -> str:
        digest = sha256_jcs(payload)
        self.revisions.setdefault(project_id, []).append(
            {
                "revision_id": revision_id,
                "rev_kind": rev_kind,
                "rev_key": rev_key,
                "parent_rev_id": parent_rev_id,
                "content_sha256": digest,
                "payload": payload.model_dump(mode="json"),
                "metadata": metadata.model_dump(mode="json"),
            }
        )
        return digest

    def list_revisions(
        self, project_id: str, *, rev_kind: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.revisions.get(project_id, [])
        return [r for r in rows if rev_kind is None or r["rev_kind"] == rev_kind]

    def append_event_or_receipt(
        self,
        project_id: str,
        *,
        record_kind: str,
        run_id: str | None,
        payload: BaseModel,
    ) -> int:
        rows = self.events.setdefault(project_id, [])
        seq = len(rows) + 1
        rows.append(
            {
                "seq_id": seq,
                "record_kind": record_kind,
                "run_id": run_id,
                "payload": payload.model_dump(mode="json"),
            }
        )
        return seq

    def list_events_or_receipts(
        self,
        project_id: str,
        *,
        record_kind: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.events.get(project_id, [])
        return [
            r
            for r in rows
            if (record_kind is None or r["record_kind"] == record_kind)
            and (run_id is None or r["run_id"] == run_id)
        ]

    def release_lease(
        self, project_id: str, *, lease_key: str, holder_run_id: str | None = None
    ) -> None:
        self.released_leases.append((project_id, lease_key, holder_run_id))
        self.projects[project_id] = self.projects[project_id].model_copy(
            update={"has_active_lease": False}
        )

    def append_run_stream_event(self, project_id: str, event: RunStreamEvent) -> None:
        self.stream_events.setdefault(project_id, []).append(event)


class InMemorySecretStore(SecretStorePort):
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        _ = (project_precedence, cli_env_first)
        return self.values.get(key)


class InMemorySettingsStore(SettingsStorePort):
    def __init__(self, servers: list[ExternalMcpServerSpec] | None = None) -> None:
        self.servers = list(servers or [])

    def load_mcp_servers(self) -> list[ExternalMcpServerSpec]:
        return list(self.servers)


class FakeBm25Matcher(Bm25MatcherPort):
    def query(self, req: FiboQueryReq) -> FiboQueryResp:
        return FiboQueryResp(
            matches=[
                FiboMatchSpec(
                    concept_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/Accounts/Account",
                    label="Account",
                    normalized_score="0.9200",
                    raw_bm25="4.5000",
                    suggested_path="PATH_A1",
                    definition="Custody or investment account.",
                )
            ]
        )


def _setup_pdf_and_table_state(state: InMemoryBrokerStateStore) -> DocumentMap:
    prof = SourceProfile(
        source_id="src_tbl",
        schema_fingerprint="fp-tbl",
        table_count=1,
        tables=[
            TableProfile(
                table_id="src_tbl:holders",
                table_name="holders",
                row_count_est=10,
                columns=[
                    ColumnProfile(
                        col_name="holder_id",
                        sql_type="VARCHAR",
                        nullable=False,
                        is_pk=True,
                        null_ratio=0.0,
                        distinct_count_est=10,
                        sample_values=["H-001", "H-002", "H-003", "H-004", "H-005"],
                    ),
                    ColumnProfile(
                        col_name="ssn",
                        sql_type="VARCHAR",
                        nullable=True,
                        is_pk=False,
                        null_ratio=0.0,
                        distinct_count_est=10,
                        pii_tags=["SSN"],
                        sample_values=["123-45-6789"],
                    ),
                ],
            )
        ],
    )
    state.add_source(
        source_id="src_tbl",
        kind="sqlite",
        uri="ibor.sqlite",
        status="READY_OFFLINE",
        profile=prof,
        tables_index=[
            TableIndexEntry(
                table_id="src_tbl:holders",
                source_id="src_tbl",
                table_name="holders",
                cluster_id="cluster_00",
                col_count=2,
                pii_col_count=1,
                pk_cols=["holder_id"],
                badge="NEW",
            )
        ],
    )
    dm, _ = DocumentMapBuilder().build_from_path(
        source_id="custody_pdf",
        path=_CUSTODY_PDF,
        doc_format="pdf",
        approved_scan_pages=[],
        run_ocr=False,
    )
    state.add_source(
        source_id="custody_pdf",
        kind="pdf",
        uri=str(_CUSTODY_PDF),
        status="READY_OFFLINE",
        profile=SourceProfile(source_id="custody_pdf", schema_fingerprint="fp-doc", table_count=0, tables=[]),
        document_map=dm,
    )
    return dm


@pytest.mark.asyncio
async def test_broker_role_isolation_bearer_auth_and_http_tool_calls() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    sink = InMemoryRunEventSink()
    dispatcher = InMemoryScoutToolDispatcher(
        digests_by_target={
            "cluster_00": ScoutDigest(
                scout_kind="table_cluster",
                target_id="cluster_00",
                candidate_concepts=["AccountHolder"],
                key_columns=["holder_id"],
                pii_columns=["ssn"],
            )
        }
    )

    async with JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-s4-01",
        state_store=state,
        bm25_matcher=FakeBm25Matcher(),
        scout_dispatcher=dispatcher,
        event_sink=sink,
    ) as broker:
        # 1. Missing bearer token -> 401; cross-role token -> 403; bad path -> 404
        async with httpx.AsyncClient() as raw_hc:
            r_unauth = await raw_hc.post(broker.coord_url, json={})
            assert r_unauth.status_code == 401
            r_cross = await raw_hc.post(
                broker.coord_url,
                headers={"Authorization": f"Bearer {broker.scout_token}"},
                json={},
            )
            assert r_cross.status_code == 403
            r_cross_2 = await raw_hc.post(
                broker.scout_url,
                headers={"Authorization": f"Bearer {broker.coord_token}"},
                json={},
            )
            assert r_cross_2.status_code == 403
            r_404 = await raw_hc.get(f"http://{broker.bind_host}:{broker.port}/mcp/wrong/coord")
            assert r_404.status_code == 404

        # 2. /coord exposes strictly the 5 coordinator tools (no raw evidence tools)
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {broker.coord_token}"}) as hc,
            streamable_http_client(broker.coord_url, http_client=hc) as (r_s, w_s),
            ClientSession(r_s, w_s) as coord_sess,
        ):
            await coord_sess.initialize()
            tools_resp = await coord_sess.list_tools()
            coord_names = {t.name for t in tools_resp.tools}
            assert coord_names == {
                "search_concepts",
                "search_fibo",
                "propose_ontology_delta",
                "ask_domain_clarification",
                "dispatch_scout",
            }
            fibo_res = await coord_sess.call_tool("search_fibo", {"query": "account"})
            assert not (getattr(fibo_res, "is_error", False) or getattr(fibo_res, "isError", False))
            disp_res = await coord_sess.call_tool(
                "dispatch_scout",
                {"scout_kind": "table_cluster", "target_ids": ["cluster_00"]},
            )
            assert not (getattr(disp_res, "is_error", False) or getattr(disp_res, "isError", False))

        # 3. /scout exposes strictly the 5 read-only scout tools
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {broker.scout_token}"}) as hc,
            streamable_http_client(broker.scout_url, http_client=hc) as (r_s, w_s),
            ClientSession(r_s, w_s) as scout_sess,
        ):
            await scout_sess.initialize()
            tools_resp = await scout_sess.list_tools()
            scout_names = {t.name for t in tools_resp.tools}
            assert scout_names == {
                "inspect_table_cluster",
                "inspect_table_profile",
                "read_document_pages",
                "view_page_image",
                "call_external_mcp",
            }
            prof_res = await scout_sess.call_tool(
                "inspect_table_profile", {"table_id": "src_tbl:holders"}
            )
            assert not (getattr(prof_res, "is_error", False) or getattr(prof_res, "isError", False))
            assert "<MASKED_PII>" in str(prof_res.content)


@pytest.mark.asyncio
async def test_delta_guards_budget_caps_and_drain_on_hold() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    state.save_revision(
        "am-ibor-2026",
        revision_id="lock-1",
        rev_kind="evolution_lock",
        rev_key="LOCK-W001",
        parent_rev_id=None,
        payload=EvolutionLockSpec(wave_number=1, locked_hub_bks={"hub_account": ["account_id"]}),
        metadata=RevisionMetadata(actor="human", reason="lock"),
    )

    started = asyncio.Event()
    release = asyncio.Event()
    dispatcher = InMemoryScoutToolDispatcher(started_event=started, release_event=release)
    sink = InMemoryRunEventSink()
    broker = JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-hold-01",
        state_store=state,
        scout_dispatcher=dispatcher,
        budget=HarnessBudget(
            max_turns=10,
            max_tokens=10000,
            timeout_seconds=60,
            max_broker_calls=6,
            max_tool_payload_bytes=4096,
        ),
        event_sink=sink,
    )

    # _DOps rejected with ONT_EVO_CONTRACT_VIOLATION
    with pytest.raises(OpenDVError) as exc_d:
        coord_propose_ontology_delta(
            broker.context,
            [ClientAdditiveOp(op=DeleteConcept(concept_uri="urn:x", rationale="no"))],
        )
    assert exc_d.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    # SERVER_ONLY_OPS rejected with SYNTAX_INVALID_ARGUMENT
    with pytest.raises(OpenDVError) as exc_s:
        coord_propose_ontology_delta(
            broker.context,
            [
                ClientAdditiveOp(
                    op=RescoreExternalMapping(
                        concept_uri="urn:x", standard_uri="urn:fibo", confidence="0.8000"
                    )
                )
            ],
        )
    assert exc_s.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # <MASKED_PII> in citation quote rejected with REDACTED_PLACEHOLDER_LEAK
    with pytest.raises(OpenDVError) as exc_leak:
        coord_propose_ontology_delta(
            broker.context,
            [
                ClientAdditiveOp(
                    op=AddSourceCitation(
                        concept_uri="urn:x",
                        citation=DocumentCitationRef(
                            doc_id="custody_pdf",
                            page_number=1,
                            verbatim_quote="Owner <MASKED_PII> signed",
                        ),
                    )
                )
            ],
        )
    assert exc_leak.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

    # Drain-on-hold: start scout, call ask_domain_clarification, verify HOLD_REQUESTED before drain
    scout_task = asyncio.create_task(
        coord_dispatch_scout(broker.context, "table_cluster", ["cluster_00"])
    )
    await started.wait()

    q = ClarificationQuestion(
        question_id="q-1",
        concept_name="Account",
        title="Confirm key",
        prompt="Which key?",
        sample_rows=["ACC-1"],
        impact_if_locked="Locks grain",
    )
    hold_task = asyncio.create_task(coord_ask_domain_clarification(broker.context, [q]))
    await asyncio.sleep(0.02)
    assert not hold_task.done()
    assert any(e.event_type == "HOLD_REQUESTED" for e in sink.events)

    # Unblock scout -> hold finishes, persists RunCheckpointRecord, releases lease, emits HOLD_CHECKPOINTED
    release.set()
    await scout_task
    ack = await hold_task
    assert ack.status == "HOLD_CHECKPOINTED"
    assert ack.pending_question_ids == ["q-1"]
    assert state.released_leases == [
        ("am-ibor-2026", "active_job_lease:am-ibor-2026", "run-hold-01")
    ]
    assert any(e.event_type == "HOLD_CHECKPOINTED" for e in sink.events)
    cps = state.list_events_or_receipts("am-ibor-2026", record_kind="run_checkpoint")
    assert len(cps) == 1
    assert cps[0]["payload"]["status"] == "HOLD_CHECKPOINTED"

    # Budget exhaustion check
    with pytest.raises(OpenDVError) as exc_bud:
        for _ in range(10):
            scout_inspect_table_profile(broker.context, "src_tbl:holders")
    assert exc_bud.value.error_code == ErrorCode.UNCERTIFIED_DRAFT


@pytest.mark.asyncio
async def test_service_scout_dispatcher_concurrent_gather() -> None:
    state = InMemoryBrokerStateStore()
    harness = DeterministicMockHarness(
        scripts_by_agent={
            "table-cluster-scout": [
                MockTurnScript(
                    structured_output=ScoutDigest(
                        scout_kind="table_cluster",
                        target_id="c1",
                        candidate_concepts=["Account"],
                        key_columns=["account_id"],
                    )
                )
            ]
        }
    )
    broker = JobMcpBroker(project_id="am-ibor-2026", run_id="run-gather", state_store=state)
    dispatcher = broker.build_scout_dispatcher(harness)
    digests = await dispatcher.dispatch_scout("table_cluster", ["c1", "c2"], run_id="run-gather")
    assert len(digests) == 2
    assert len(harness.requests) == 2
    assert all(r.role_kind == "scout" and r.role_token == broker.scout_token for r in harness.requests)


def test_view_page_image_and_read_document_pages_on_real_pdf() -> None:
    state = InMemoryBrokerStateStore()
    dm = _setup_pdf_and_table_state(state)
    assert dm.pages[3].is_scanned_or_low_density is True  # 1-based page 4

    broker = JobMcpBroker(project_id="am-ibor-2026", run_id="run-ocr", state_store=state)

    # Text-dense page 2 works in read_document_pages, rejected in view_page_image
    views = scout_read_document_pages(broker.context, "custody_pdf", 1, 2)
    assert len(views) == 2
    with pytest.raises(OpenDVError) as exc_dense:
        scout_view_page_image(broker.context, "custody_pdf", 2)
    assert exc_dense.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Unapproved scanned page 4 -> UNREDACTED_SCAN_BLOCKED
    with pytest.raises(OpenDVError) as exc_unapp:
        scout_view_page_image(broker.context, "custody_pdf", 4)
    assert exc_unapp.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # Approved p4 with ocr_box_count=0 -> UNREDACTED_SCAN_BLOCKED
    state.sources["am-ibor-2026"]["custody_pdf"]["approved_scan_pages"] = [4]
    with pytest.raises(OpenDVError) as exc_nobox:
        scout_view_page_image(broker.context, "custody_pdf", 4)
    assert exc_nobox.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

    # Approved p4 with injected OCR provider -> redacted PNG + OcrPageReceipt persisted
    def _fake_ocr(_pdf: bytes, _page: int) -> tuple[list[OcrBox], str]:
        return (
            [OcrBox(10, 10, 120, 40, "SSN 123-45-6789")],
            "Proxy Holder SSN 123-45-6789 Ballot",
        )

    broker.context.ocr_provider = _fake_ocr
    ocr_res = scout_view_page_image(broker.context, "custody_pdf", 4)
    assert ocr_res.redaction_applied is True
    assert ocr_res.ocr_box_count == 1
    assert "123-45-6789" not in ocr_res.ocr_text
    assert "<MASKED_PII>" in ocr_res.ocr_text
    assert ocr_res.redacted_png_b64 is not None
    png_raw = base64.b64decode(ocr_res.redacted_png_b64)
    assert png_raw.startswith(b"\x89PNG\r\n\x1a\n")

    ocr_recs = state.list_events_or_receipts("am-ibor-2026", record_kind="ocr_receipt")
    assert len(ocr_recs) == 1
    parsed_rec = OcrPageReceipt.model_validate(ocr_recs[0]["payload"])
    assert parsed_rec.content_sha256 == hashlib.sha256(ocr_res.ocr_text.encode("utf-8")).hexdigest()

    # METADATA_ONLY_BINARY_ABSENT -> SOURCE_BINARY_ABSENT
    state.sources["am-ibor-2026"]["custody_pdf"]["status"] = "METADATA_ONLY_BINARY_ABSENT"
    with pytest.raises(OpenDVError) as exc_abs:
        scout_read_document_pages(broker.context, "custody_pdf", 1, 1)
    assert exc_abs.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT


@pytest.mark.asyncio
async def test_external_mcp_gateway_gates_ssrf_pii_and_receipts() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    secrets = InMemorySecretStore({"EXT_MCP_KEY": "super-secret-ext-token-value-12345"})

    with LoopbackFakeExternalMcpServer(
        server_id="ext_doc",
        expected_token="super-secret-ext-token-value-12345",
        echo_pii_in_response="Contact alice@example.com or 123-45-6789",
    ) as fake_srv:
        settings = InMemorySettingsStore(
            [
                fake_srv.server_spec(
                    allow_scout_calls=False,
                    allow_document_pages=False,
                    secret_refs=["EXT_MCP_KEY"],
                )
            ]
        )
        gw = ExternalMcpGateway(
            state_store=state, secret_store=secrets, settings_store=settings
        )

        # 1. allow_scout_calls=False -> EXTERNAL_MCP_DOC_OPT_IN_REQUIRED
        with pytest.raises(OpenDVError) as exc_sc:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="ext_doc",
                tool_name="lookup_entity",
                arguments={"symbol": "AAPL"},
                is_scout_call=True,
            )
        assert exc_sc.value.error_code == ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED

        # 2. allow_document_pages=False with doc_page handle -> EXTERNAL_MCP_DOC_OPT_IN_REQUIRED
        settings.servers = [
            fake_srv.server_spec(
                allow_scout_calls=True,
                allow_document_pages=False,
                secret_refs=["EXT_MCP_KEY"],
            )
        ]
        with pytest.raises(OpenDVError) as exc_doc:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="ext_doc",
                tool_name="extract_clause",
                arguments={
                    "query": "voting",
                    "doc_page": {"$opendv_handle": "doc_page", "doc_id": "custody_pdf", "page_number": 4},
                },
            )
        assert exc_doc.value.error_code == ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED

        # 3. allow_document_pages=True but page 4 not in approved_scan_pages -> UNREDACTED_SCAN_BLOCKED
        settings.servers = [
            fake_srv.server_spec(
                allow_scout_calls=True,
                allow_document_pages=True,
                secret_refs=["EXT_MCP_KEY"],
            )
        ]
        with pytest.raises(OpenDVError) as exc_scan:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="ext_doc",
                tool_name="extract_clause",
                arguments={
                    "query": "voting",
                    "doc_page": {"$opendv_handle": "doc_page", "doc_id": "custody_pdf", "page_number": 4},
                },
            )
        assert exc_scan.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        # 4. Approved p4 without OCR boxes & without allow_unredacted_external_ocr -> UNREDACTED_SCAN_BLOCKED
        state.sources["am-ibor-2026"]["custody_pdf"]["approved_scan_pages"] = [4]
        with pytest.raises(OpenDVError) as exc_ocr_flag:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="ext_doc",
                tool_name="extract_clause",
                arguments={
                    "query": "voting",
                    "doc_page": {"$opendv_handle": "doc_page", "doc_id": "custody_pdf", "page_number": 4},
                },
            )
        assert exc_ocr_flag.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        # 5. allow_unredacted_external_ocr=True succeeds, preserves redacted_png_b64, scrubs PII, persists receipt
        state.sources["am-ibor-2026"]["custody_pdf"]["allow_unredacted_external_ocr"] = True
        res = await gw.invoke_tool(
            project_id="am-ibor-2026",
            run_id="r1",
            server_id="ext_doc",
            tool_name="extract_clause",
            arguments={
                "query": "check SSN 123-45-6789",
                "doc_page": {"$opendv_handle": "doc_page", "doc_id": "custody_pdf", "page_number": 4},
            },
        )
        assert "alice@example.com" not in res.masked_excerpt
        assert "123-45-6789" not in res.masked_excerpt
        assert "<MASKED_PII>" in res.masked_excerpt
        assert fake_srv.received_calls[-1]["query"] == "check SSN <MASKED_PII>"
        assert fake_srv.received_calls[-1]["doc_page"]["redacted_png_b64"]
        assert "has_png=True" in res.masked_excerpt

        receipts = state.list_events_or_receipts("am-ibor-2026", record_kind="mcp_receipt")
        assert len(receipts) == 1
        rec = ExternalMcpReceipt.model_validate(receipts[0]["payload"])
        assert rec.receipt_id == res.receipt_id
        assert rec.content_sha256 == res.content_sha256
        assert rec.doc_id == "custody_pdf"
        assert rec.page_number == 4

        # 6. Missing secret -> NEEDS_LOCAL_SECRET_BINDING
        secrets.values.clear()
        with pytest.raises(OpenDVError) as exc_sec:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="ext_doc",
                tool_name="lookup_entity",
                arguments={"symbol": "AAPL"},
            )
        assert exc_sec.value.error_code == ErrorCode.NEEDS_LOCAL_SECRET_BINDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://10.0.0.1:8080/mcp",
        "http://172.16.5.4:8080/mcp",
        "http://192.168.1.1:8080/mcp",
        "http://100.64.0.1:8080/mcp",
        "http://169.254.169.254/latest/meta-data",
        "http://[fc00::1]:8080/mcp",
        "http://[fe80::1]:8080/mcp",
        "http://2130706433:8080/mcp",
        "http://evil-rebind.example:8080/mcp",
        "http://\uff10\uff11\uff17\uff17\uff0e\uff10\uff0e\uff10\uff0e\uff11/mcp",
        "http://\uff10\uff11\uff12\uff0e\uff10\uff0e\uff10\uff0e\uff11/mcp",
        "http://\uff10\uff58\uff10\uff41\uff10\uff10\uff10\uff10\uff10\uff11/mcp",
        "http://\uff12\uff11\uff13\uff10\uff17\uff10\uff16\uff14\uff13\uff13/mcp",
        "http://\uff11\uff10\uff0e\uff10\uff0e\uff10\uff0e\uff11/mcp",
    ],
)
async def test_external_mcp_ssrf_and_redirect_blocking(bad_url: str) -> None:
    state = InMemoryBrokerStateStore()
    secrets = InMemorySecretStore()
    settings = InMemorySettingsStore(
        [
            ExternalMcpServerSpec(
                server_id="ssrf_srv",
                transport="streamable_http",
                url=bad_url,
                allow_scout_calls=True,
            )
        ]
    )
    gw = ExternalMcpGateway(
        state_store=state,
        secret_store=secrets,
        settings_store=settings,
        dns_resolver=lambda host, _port: ["10.20.30.40"] if "rebind" in host else [],
    )
    with pytest.raises(OpenDVError) as exc_ssrf:
        await gw.invoke_tool(
            project_id="am-ibor-2026",
            run_id="r1",
            server_id="ssrf_srv",
            tool_name="lookup_entity",
            arguments={"symbol": "AAPL"},
        )
    assert exc_ssrf.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED


@pytest.mark.asyncio
async def test_external_mcp_redirect_302_blocked() -> None:
    state = InMemoryBrokerStateStore()
    secrets = InMemorySecretStore()
    with LoopbackFakeExternalMcpServer(
        server_id="redir_srv",
        redirect_location="http://169.254.169.254/latest/meta-data",
    ) as redir_srv:
        settings = InMemorySettingsStore(
            [redir_srv.server_spec(allow_scout_calls=True)]
        )
        gw = ExternalMcpGateway(
            state_store=state, secret_store=secrets, settings_store=settings
        )
        with pytest.raises(OpenDVError) as exc_redir:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r1",
                server_id="redir_srv",
                tool_name="lookup_entity",
                arguments={"symbol": "AAPL"},
            )
        assert exc_redir.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED


@pytest.mark.asyncio
async def test_external_mcp_stdio_allowlisted_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked-anthropic-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "leaked-dbx-token")
    monkeypatch.setenv("OPENDV_MCP_COORD_TOKEN", "leaked-coord-token")

    child_script = """
import os, json
from mcp.server.mcpserver import MCPServer
srv = MCPServer('stdio-test')
@srv.tool()
def env_check(arg: str) -> str:
    keys = sorted(k for k in os.environ if k in ('ANTHROPIC_API_KEY','DATABRICKS_TOKEN','OPENDV_MCP_COORD_TOKEN','ALLOWED_EXT_SECRET'))
    return json.dumps({'keys': keys, 'arg': arg})
srv.run(transport='stdio')
"""
    state = InMemoryBrokerStateStore()
    secrets = InMemorySecretStore({"ALLOWED_EXT_SECRET": "ext-secret-ok"})
    settings = InMemorySettingsStore(
        [
            ExternalMcpServerSpec(
                server_id="stdio_srv",
                transport="stdio",
                command=[sys.executable, "-c", child_script],
                secret_refs=["ALLOWED_EXT_SECRET"],
                allow_scout_calls=True,
                timeout_seconds=15,
            )
        ]
    )
    gw = ExternalMcpGateway(
        state_store=state, secret_store=secrets, settings_store=settings
    )
    res = await gw.invoke_tool(
        project_id="am-ibor-2026",
        run_id="r1",
        server_id="stdio_srv",
        tool_name="env_check",
        arguments={"arg": "ok"},
    )
    assert '"keys": ["ALLOWED_EXT_SECRET"]' in res.masked_excerpt
    assert "ANTHROPIC_API_KEY" not in res.masked_excerpt
    assert "DATABRICKS_TOKEN" not in res.masked_excerpt
    assert "OPENDV_MCP_COORD_TOKEN" not in res.masked_excerpt


@pytest.mark.asyncio
async def test_external_mcp_security_regressions_ocr_forged_b64_error_pii_bool_idna_ipv6() -> None:
    state = InMemoryBrokerStateStore()
    dm = _setup_pdf_and_table_state(state)
    # Force page 4 to have static ocr_box_count > 0 while runtime ocr_provider=None returns []
    patched_pages = [
        (p.model_copy(update={"ocr_box_count": 5}) if p.page_number == 4 else p) for p in dm.pages
    ]
    state.sources["am-ibor-2026"]["custody_pdf"]["document_map"] = dm.model_copy(
        update={"pages": patched_pages}
    )
    state.sources["am-ibor-2026"]["custody_pdf"]["approved_scan_pages"] = [4]

    # Direct scrub_outbound_arguments must NOT exempt forged dict matching handle key shape
    forged = {
        "doc_id": "x",
        "page_number": 1,
        "redacted_png_b64": "SSN 123-45-6789 email alice@example.com",
    }
    scrubbed_direct = scrub_outbound_arguments(forged)
    assert "123-45-6789" not in scrubbed_direct["redacted_png_b64"]
    assert "alice@example.com" not in scrubbed_direct["redacted_png_b64"]
    assert "<MASKED_PII>" in scrubbed_direct["redacted_png_b64"]

    secrets = InMemorySecretStore()
    with LoopbackFakeExternalMcpServer(
        server_id="ipv6_srv",
        bind_host="::1",
        echo_pii_in_response="SSN 123-45-6789 email alice@example.com",
    ) as ipv6_srv:
        settings = InMemorySettingsStore(
            [
                ipv6_srv.server_spec(allow_scout_calls=True, allow_document_pages=True),
                ExternalMcpServerSpec(
                    server_id="bad_idna",
                    transport="streamable_http",
                    url="http://xn--invalidpunycode-99999999:8080/mcp",
                    allow_scout_calls=True,
                ),
            ]
        )
        gw = ExternalMcpGateway(
            state_store=state, secret_store=secrets, settings_store=settings
        )

        # 1. Runtime boxes==0 must fail closed even when page_spec.ocr_box_count > 0
        with pytest.raises(OpenDVError) as exc_unredacted:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-reg",
                server_id="ipv6_srv",
                tool_name="extract_clause",
                arguments={
                    "query": "check",
                    "doc_page": {
                        "$opendv_handle": "doc_page",
                        "doc_id": "custody_pdf",
                        "page_number": 4,
                    },
                },
            )
        assert exc_unredacted.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED

        # 2. Boolean page_number=True rejected with SYNTAX_INVALID_ARGUMENT
        with pytest.raises(OpenDVError) as exc_bool:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-reg",
                server_id="ipv6_srv",
                tool_name="extract_clause",
                arguments={
                    "query": "check",
                    "doc_page": {
                        "$opendv_handle": "doc_page",
                        "doc_id": "custody_pdf",
                        "page_number": True,
                    },
                },
            )
        assert exc_bool.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        # 3. Malformed xn-- IDNA rejected with DATABRICKS_SSRF_BLOCKED
        with pytest.raises(OpenDVError) as exc_idna:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-reg",
                server_id="bad_idna",
                tool_name="lookup_entity",
                arguments={"symbol": "AAPL"},
            )
        assert exc_idna.value.error_code == ErrorCode.DATABRICKS_SSRF_BLOCKED

        # 4. SCAN_APPROVED CSV with whitespace ("1, 4") unlocks page 4; forged b64 is scrubbed
        state.append_event_or_receipt(
            "am-ibor-2026",
            record_kind="audit_log",
            run_id="r-reg",
            payload=AuditLogRecord(
                event_type="SCAN_APPROVED",
                actor="human",
                step="step3",
                summary="Approved scan pages",
                details={
                    "doc_id": "custody_pdf",
                    "pages": "1, 4",
                    "allow_unredacted_external_ocr": True,
                },
            ),
        )
        ok_res = await gw.invoke_tool(
            project_id="am-ibor-2026",
            run_id="r-reg",
            server_id="ipv6_srv",
            tool_name="extract_clause",
            arguments={
                "query": "forged_check",
                "forged": forged,
                "doc_page": {
                    "$opendv_handle": "doc_page",
                    "doc_id": "custody_pdf",
                    "page_number": 4,
                },
            },
        )
        assert "has_png=True" in ok_res.masked_excerpt
        assert ipv6_srv.url.startswith("http://[::1]:")

        # 5. isError=True / is_error=True response scrubs inbound PII before raising OpenDVError
        with pytest.raises(OpenDVError) as exc_err_pii:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-reg",
                server_id="ipv6_srv",
                tool_name="fail_lookup",
                arguments={"symbol": "AAPL"},
            )
        assert exc_err_pii.value.error_code == ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR
        assert "123-45-6789" not in str(exc_err_pii.value)
        assert "alice@example.com" not in str(exc_err_pii.value)
        assert "<MASKED_PII>" in str(exc_err_pii.value)

        # 6. doc_page handle referencing a structured source (document_map=None) -> RESOURCE_NOT_FOUND
        with pytest.raises(OpenDVError) as exc_no_dm:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-reg",
                server_id="ipv6_srv",
                tool_name="extract_clause",
                arguments={
                    "doc_page": {
                        "$opendv_handle": "doc_page",
                        "doc_id": "src_tbl",
                        "page_number": 1,
                    }
                },
            )
        assert exc_no_dm.value.error_code == ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_broker_ipv6_delta_leaks_locks_search_and_checkpoint_seq() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    state.save_revision(
        "am-ibor-2026",
        revision_id="ont-1",
        rev_kind="ontology_version",
        rev_key="v1.0.0",
        parent_rev_id=None,
        payload=GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:acct",
                    concept_name="Account",
                    description="Customer account",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    synonyms=["ClientAccount"],
                )
            ],
        ),
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    state.save_revision(
        "am-ibor-2026",
        revision_id="lock-1",
        rev_kind="evolution_lock",
        rev_key="LOCK-W001",
        parent_rev_id=None,
        payload=EvolutionLockSpec(wave_number=1, locked_hub_bks={"hub_account": ["account_id"]}),
        metadata=RevisionMetadata(actor="human", reason="lock"),
    )
    state.save_revision(
        "am-ibor-2026",
        revision_id="mem-1",
        rev_kind="concept_memory",
        rev_key="MEM-1",
        parent_rev_id=None,
        payload=ConceptMemorySpec(
            term="AccountHolder",
            state="PINNED",
            concept_uri="urn:holder",
        ),
        metadata=RevisionMetadata(actor="human", reason="mem"),
    )

    with LoopbackFakeExternalMcpServer(server_id="ext_scout", bind_host="::1") as ext_srv:
        settings = InMemorySettingsStore([ext_srv.server_spec(allow_scout_calls=True)])
        gw = ExternalMcpGateway(
            state_store=state,
            secret_store=InMemorySecretStore(),
            settings_store=settings,
        )
        sink = InMemoryRunEventSink()
        async with JobMcpBroker(
            project_id="am-ibor-2026",
            run_id="run-cov-01",
            state_store=state,
            external_gateway=gw,
            event_sink=sink,
            workspace_seeds=[
                ConceptSummary(
                    concept_uri="urn:seed",
                    concept_name="AccountSeed",
                    source_layer="workspace_seed",
                    locked_in_vault=False,
                )
            ],
            bind_host="::1",
        ) as broker:
            assert broker.coord_url.startswith("http://[::1]:")

            # AddConcept nested source_citations leak -> REDACTED_PLACEHOLDER_LEAK
            with pytest.raises(OpenDVError) as exc_ac_cit:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=AddConcept(
                                concept_uri="urn:c1",
                                concept_name="LeakyCit",
                                source_citations=[
                                    DocumentCitationRef(
                                        doc_id="custody_pdf",
                                        page_number=1,
                                        verbatim_quote="Holder <MASKED_PII> policy",
                                    )
                                ],
                            )
                        )
                    ],
                )
            assert exc_ac_cit.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

            # AddConcept nested mcp_evidence leak -> REDACTED_PLACEHOLDER_LEAK
            with pytest.raises(OpenDVError) as exc_ac_mcp:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=AddConcept(
                                concept_uri="urn:c2",
                                concept_name="LeakyMcp",
                                mcp_evidence=[
                                    ExternalMcpEvidenceRef(
                                        receipt_id="mcpr-leak-1",
                                        server_id="ext_scout",
                                        tool_name="lookup_entity",
                                        content_sha256="b" * 64,
                                        masked_excerpt="Secret [REDACTED_TOKEN] leaked",
                                    )
                                ],
                            )
                        )
                    ],
                )
            assert exc_ac_mcp.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

            # AddMcpEvidence leak -> REDACTED_PLACEHOLDER_LEAK
            with pytest.raises(OpenDVError) as exc_me:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=AddMcpEvidence(
                                concept_uri="urn:acct",
                                evidence=ExternalMcpEvidenceRef(
                                    receipt_id="mcpr-leak-2",
                                    server_id="ext_scout",
                                    tool_name="lookup_entity",
                                    content_sha256="b" * 64,
                                    masked_excerpt="Excerpt <MASKED_PII>",
                                ),
                            )
                        )
                    ],
                )
            assert exc_me.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

            # Locked-BK ONT_EVO_CONTRACT_VIOLATION (SetConceptKeyAuthority & AddAttribute BK)
            with pytest.raises(OpenDVError) as exc_lock_1:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=SetConceptKeyAuthority(
                                concept_uri="urn:acct",
                                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                            )
                        )
                    ],
                )
            assert exc_lock_1.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

            with pytest.raises(OpenDVError) as exc_lock_2:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=AddAttribute(
                                concept_uri="urn:acct",
                                attribute=ConceptAttribute(
                                    name="alt_bk",
                                    logical_type="UTF8_STRING",
                                    sql_type_hint="VARCHAR",
                                    nullable=False,
                                    is_business_key=True,
                                    is_pii=False,
                                ),
                            )
                        )
                    ],
                )
            assert exc_lock_2.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

            with pytest.raises(OpenDVError) as exc_lock_3:
                coord_propose_ontology_delta(
                    broker.context,
                    [
                        ClientAdditiveOp(
                            op=UpdateAttributeMetadata(
                                concept_uri="urn:acct",
                                attribute_name="account_id",
                                is_business_key=False,
                            )
                        )
                    ],
                )
            assert exc_lock_3.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

            # coord_search_concepts covers Graph A (locked), memory, and seeds
            hits = coord_search_concepts(broker.context, "account")
            by_layer = {h.source_layer: h for h in hits}
            assert by_layer["committed_graph_a"].locked_in_vault is True
            assert by_layer["concept_memory"].concept_name == "AccountHolder"
            assert by_layer["workspace_seed"].concept_name == "AccountSeed"

            # scout_inspect_table_cluster + scout_call_external_mcp over /scout HTTP MCP
            cluster_profiles = scout_inspect_table_cluster(broker.context, "cluster_00")
            assert len(cluster_profiles) == 1
            assert "<MASKED_PII>" in cluster_profiles[0].columns[1].sample_values[0]

            async with (
                httpx2.AsyncClient(headers={"Authorization": f"Bearer {broker.scout_token}"}) as hc,
                streamable_http_client(broker.scout_url, http_client=hc) as (r_s, w_s),
                ClientSession(r_s, w_s) as scout_sess,
            ):
                await scout_sess.initialize()
                ext_res = await scout_sess.call_tool(
                    "call_external_mcp",
                    {
                        "server_id": "ext_scout",
                        "tool_name": "lookup_entity",
                        "arguments": {"symbol": "MSFT"},
                    },
                )
                assert not (
                    getattr(ext_res, "is_error", False) or getattr(ext_res, "isError", False)
                )
                assert "symbol=MSFT" in str(ext_res.content)

            # RunCheckpointRecord.last_seq aligns with HOLD_CHECKPOINTED seq and payload["last_seq"]
            ack = await coord_ask_domain_clarification(
                broker.context,
                [
                    ClarificationQuestion(
                        question_id="q-seq",
                        concept_name="Account",
                        title="Confirm",
                        prompt="Confirm grain?",
                        sample_rows=["ACC-1"],
                        impact_if_locked="Locks grain",
                    )
                ],
            )
            cps = state.list_events_or_receipts("am-ibor-2026", record_kind="run_checkpoint")
            hold_evs = [e for e in sink.events if e.event_type == "HOLD_CHECKPOINTED"]
            assert len(hold_evs) == 1
            assert cps[-1]["payload"]["last_seq"] == ack.checkpoint_seq
            assert hold_evs[0].seq == ack.checkpoint_seq
            assert hold_evs[0].payload["last_seq"] == ack.checkpoint_seq

            # Duplicate ask_domain_clarification fails closed with HOLD_CHECKPOINTED
            with pytest.raises(OpenDVError) as exc_dup_hold:
                await coord_ask_domain_clarification(
                    broker.context,
                    [
                        ClarificationQuestion(
                            question_id="q-dup",
                            concept_name="Account",
                            title="Duplicate",
                            prompt="Duplicate hold?",
                            sample_rows=["ACC-2"],
                            impact_if_locked="Should not emit",
                        )
                    ],
                )
            assert exc_dup_hold.value.error_code == ErrorCode.HOLD_CHECKPOINTED
            assert len(state.list_events_or_receipts("am-ibor-2026", record_kind="run_checkpoint")) == 1
            assert len([e for e in sink.events if e.event_type == "HOLD_CHECKPOINTED"]) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_ask_domain_clarification_fails_closed() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    sink = InMemoryRunEventSink()
    started = asyncio.Event()
    release = asyncio.Event()
    dispatcher = InMemoryScoutToolDispatcher(started_event=started, release_event=release)

    async with JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-concurrent-hold",
        state_store=state,
        scout_dispatcher=dispatcher,
        event_sink=sink,
    ) as broker:
        scout_task = asyncio.create_task(
            coord_dispatch_scout(broker.context, "table_cluster", ["cluster_00"])
        )
        await started.wait()

        q1 = ClarificationQuestion(
            question_id="q-c1",
            concept_name="Account",
            title="Hold 1",
            prompt="Confirm grain?",
            sample_rows=["ACC-1"],
            impact_if_locked="Locks grain",
        )
        q2 = ClarificationQuestion(
            question_id="q-c2",
            concept_name="Account",
            title="Hold 2",
            prompt="Duplicate concurrent hold?",
            sample_rows=["ACC-2"],
            impact_if_locked="Should fail",
        )
        hold1_task = asyncio.create_task(coord_ask_domain_clarification(broker.context, [q1]))
        await asyncio.sleep(0.02)

        with pytest.raises(OpenDVError) as exc_dup:
            await coord_ask_domain_clarification(broker.context, [q2])
        assert exc_dup.value.error_code == ErrorCode.HOLD_CHECKPOINTED

        release.set()
        await scout_task
        ack = await hold1_task
        assert ack.status == "HOLD_CHECKPOINTED"
        assert len(state.list_events_or_receipts("am-ibor-2026", record_kind="run_checkpoint")) == 1
        assert len([e for e in sink.events if e.event_type == "HOLD_CHECKPOINTED"]) == 1


@pytest.mark.asyncio
async def test_camelcase_hub_lock_and_distinct_candidate_ids() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    state.save_revision(
        "am-ibor-2026",
        revision_id="ont-camel",
        rev_kind="ontology_version",
        rev_key="v1.0.0",
        parent_rev_id=None,
        payload=GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:acct",
                    concept_name="Account",
                    description="Customer account",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                ),
                CanonicalBusinessConcept(
                    concept_uri="urn:holder",
                    concept_name="AccountHolder",
                    description="Account holder",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                ),
            ],
        ),
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    state.save_revision(
        "am-ibor-2026",
        revision_id="lock-camel",
        rev_kind="evolution_lock",
        rev_key="LOCK-W001",
        parent_rev_id=None,
        payload=EvolutionLockSpec(
            wave_number=1,
            locked_hub_bks={"hub_account_holder": ["holder_id"]},
        ),
        metadata=RevisionMetadata(actor="human", reason="lock"),
    )

    async with JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-camel-lock",
        state_store=state,
    ) as broker:
        hits = coord_search_concepts(broker.context, "holder")
        assert len(hits) == 1
        assert hits[0].concept_name == "AccountHolder"
        assert hits[0].locked_in_vault is True

        with pytest.raises(OpenDVError) as exc_bk:
            coord_propose_ontology_delta(
                broker.context,
                [
                    ClientAdditiveOp(
                        op=UpdateAttributeMetadata(
                            concept_uri="urn:holder",
                            attribute_name="holder_id",
                            is_business_key=False,
                        )
                    )
                ],
            )
        assert exc_bk.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

        # Non-BK metadata update on locked CamelCase concept is permitted
        ok_resp = coord_propose_ontology_delta(
            broker.context,
            [
                ClientAdditiveOp(
                    op=UpdateAttributeMetadata(
                        concept_uri="urn:holder",
                        attribute_name="holder_id",
                        sql_type_hint="VARCHAR(64)",
                        is_pii=False,
                    )
                )
            ],
        )
        assert ok_resp.staged_count == 1

        # Multiple UpdateAttributeMetadata ops on same concept_uri yield distinct candidate_ids
        multi_resp = coord_propose_ontology_delta(
            broker.context,
            [
                ClientAdditiveOp(
                    op=UpdateAttributeMetadata(
                        concept_uri="urn:acct",
                        attribute_name="col_a",
                        sql_type_hint="VARCHAR(32)",
                    )
                ),
                ClientAdditiveOp(
                    op=UpdateAttributeMetadata(
                        concept_uri="urn:acct",
                        attribute_name="col_b",
                        is_pii=True,
                    )
                ),
            ],
        )
        assert multi_resp.staged_count == 2
        assert len(set(multi_resp.candidate_ids)) == 2
        staged_revs = state.list_revisions("am-ibor-2026", rev_kind="staged_candidate")
        staged_keys = {r["rev_key"] for r in staged_revs}
        assert set(multi_resp.candidate_ids).issubset(staged_keys)
        assert len(staged_revs) == 3


@pytest.mark.asyncio
async def test_service_run_event_sink_writes_single_jsonl_event_per_emit(tmp_path: Path) -> None:
    mgr = ProjectStateManager(tmp_path / "ws")
    mgr.initialize()
    mgr.create_project("am-ibor-2026", "asset_management", ProjectSettingsSnapshot())
    sink = ServiceRunEventSink(mgr, "am-ibor-2026")
    assert getattr(sink, "_state_manager", None) is mgr

    async with JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-single-jsonl",
        state_store=mgr,
        event_sink=sink,
    ) as broker:
        ack = await coord_ask_domain_clarification(
            broker.context,
            [
                ClarificationQuestion(
                    question_id="q-single-1",
                    concept_name="Account",
                    title="Confirm grain",
                    prompt="Confirm account grain?",
                    sample_rows=["ACC-1"],
                    impact_if_locked="Locks grain",
                )
            ],
        )
        assert ack.checkpoint_seq == 2

    page = mgr.tail_run_stream_events("am-ibor-2026", "run-single-jsonl")
    assert [ev.seq for ev in page.events] == [1, 2]
    assert [ev.event_type for ev in page.events] == ["HOLD_REQUESTED", "HOLD_CHECKPOINTED"]
    page1 = mgr.tail_run_stream_events("am-ibor-2026", "run-single-jsonl", since_seq=0, limit=1)
    assert [ev.event_type for ev in page1.events] == ["HOLD_REQUESTED"]
    page2 = mgr.tail_run_stream_events(
        "am-ibor-2026", "run-single-jsonl", since_seq=page1.next_since_seq
    )
    assert [ev.event_type for ev in page2.events] == ["HOLD_CHECKPOINTED"]


@pytest.mark.asyncio
async def test_mixed_batch_propose_ontology_delta_is_atomic() -> None:
    state = InMemoryBrokerStateStore()
    _setup_pdf_and_table_state(state)
    state.save_revision(
        "am-ibor-2026",
        revision_id="ont-atom",
        rev_kind="ontology_version",
        rev_key="v1.0.0",
        parent_rev_id=None,
        payload=GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:holder",
                    concept_name="AccountHolder",
                    description="Account holder",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                )
            ],
        ),
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    state.save_revision(
        "am-ibor-2026",
        revision_id="lock-atom",
        rev_kind="evolution_lock",
        rev_key="LOCK-W001",
        parent_rev_id=None,
        payload=EvolutionLockSpec(
            wave_number=1,
            locked_hub_bks={"hub_account_holder": ["holder_id"]},
        ),
        metadata=RevisionMetadata(actor="human", reason="lock"),
    )

    valid_add = ClientAdditiveOp(
        op=AddConcept(
            concept_uri="urn:new_portfolio",
            concept_name="Portfolio",
        )
    )
    locked_update = ClientAdditiveOp(
        op=UpdateAttributeMetadata(
            concept_uri="urn:holder",
            attribute_name="holder_id",
            is_business_key=False,
        )
    )
    leaked_ev = ClientAdditiveOp(
        op=AddMcpEvidence(
            concept_uri="urn:holder",
            evidence=ExternalMcpEvidenceRef(
                receipt_id="mcpr-atom",
                server_id="ext_scout",
                tool_name="lookup_entity",
                content_sha256="b" * 64,
                masked_excerpt="Leaked <MASKED_PII> token",
            ),
        )
    )

    async with JobMcpBroker(
        project_id="am-ibor-2026",
        run_id="run-atomic-delta",
        state_store=state,
    ) as broker:
        with pytest.raises(OpenDVError) as exc_lock:
            coord_propose_ontology_delta(broker.context, [valid_add, locked_update])
        assert exc_lock.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION
        assert state.list_revisions("am-ibor-2026", rev_kind="staged_candidate") == []

        with pytest.raises(OpenDVError) as exc_leak:
            coord_propose_ontology_delta(broker.context, [valid_add, leaked_ev])
        assert exc_leak.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK
        assert state.list_revisions("am-ibor-2026", rev_kind="staged_candidate") == []


@pytest.mark.asyncio
async def test_cross_project_propose_ontology_delta_and_punctuation_lock_regression(
    tmp_path: Path,
) -> None:
    mgr = ProjectStateManager(tmp_path / "ws_cross")
    mgr.initialize()
    mgr.create_project("proj-alpha", "asset_management", ProjectSettingsSnapshot())
    mgr.create_project("proj-beta", "asset_management", ProjectSettingsSnapshot())

    # Cross-project staging of identical concept_name must NOT collide on revisions.revision_id
    async with JobMcpBroker(project_id="proj-alpha", run_id="r-a", state_store=mgr) as broker_a:
        resp_a = coord_propose_ontology_delta(
            broker_a.context,
            [
                ClientAdditiveOp(
                    op=AddConcept(concept_uri="urn:a:portfolio", concept_name="GlobalPortfolio")
                )
            ],
        )
        assert resp_a.staged_count == 1

    async with JobMcpBroker(project_id="proj-beta", run_id="r-b", state_store=mgr) as broker_b:
        resp_b = coord_propose_ontology_delta(
            broker_b.context,
            [
                ClientAdditiveOp(
                    op=AddConcept(concept_uri="urn:b:custodian", concept_name="Custodian")
                ),
                ClientAdditiveOp(
                    op=AddConcept(concept_uri="urn:b:portfolio", concept_name="GlobalPortfolio")
                ),
            ],
        )
        assert resp_b.staged_count == 2

    revs_a = mgr.list_revisions("proj-alpha", rev_kind="staged_candidate")
    revs_b = mgr.list_revisions("proj-beta", rev_kind="staged_candidate")
    assert len(revs_a) == 1
    assert len(revs_b) == 2
    assert revs_a[0]["revision_id"] != revs_b[1]["revision_id"]

    # Punctuation variants ("Account/Holder", "Account.Holder") must match hub_account_holder lock
    mgr.save_revision(
        "proj-alpha",
        revision_id="ont-punct",
        rev_kind="ontology_version",
        rev_key="v1.0.0",
        parent_rev_id=None,
        payload=GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:slash",
                    concept_name="Account/Holder",
                    description="Slash holder",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                ),
                CanonicalBusinessConcept(
                    concept_uri="urn:dot",
                    concept_name="Account.Holder",
                    description="Dot holder",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                ),
            ],
        ),
        metadata=RevisionMetadata(actor="human", reason="init"),
    )
    mgr.save_revision(
        "proj-alpha",
        revision_id="lock-punct",
        rev_kind="evolution_lock",
        rev_key="LOCK-W001",
        parent_rev_id=None,
        payload=EvolutionLockSpec(
            wave_number=1,
            locked_hub_bks={"hub_account_holder": ["holder_id"]},
        ),
        metadata=RevisionMetadata(actor="human", reason="lock"),
    )

    async with JobMcpBroker(project_id="proj-alpha", run_id="r-punct", state_store=mgr) as brk:
        hits = coord_search_concepts(brk.context, "holder")
        assert len(hits) == 2
        assert all(h.locked_in_vault for h in hits)

        for uri in ("urn:slash", "urn:dot"):
            with pytest.raises(OpenDVError) as exc_lock:
                coord_propose_ontology_delta(
                    brk.context,
                    [
                        ClientAdditiveOp(
                            op=AddConcept(
                                concept_uri="urn:a:extra", concept_name="UnstagedConcept"
                            )
                        ),
                        ClientAdditiveOp(
                            op=UpdateAttributeMetadata(
                                concept_uri=uri,
                                attribute_name="holder_id",
                                is_business_key=False,
                            )
                        ),
                    ],
                )
            assert exc_lock.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION
        assert len(mgr.list_revisions("proj-alpha", rev_kind="staged_candidate")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_pages", ["", " , "])
async def test_scan_approved_empty_pages_does_not_act_as_wildcard(empty_pages: str) -> None:
    state = InMemoryBrokerStateStore()
    dm = _setup_pdf_and_table_state(state)
    patched_pages = [
        (p.model_copy(update={"ocr_box_count": 5}) if p.page_number == 4 else p) for p in dm.pages
    ]
    state.sources["am-ibor-2026"]["custody_pdf"]["document_map"] = dm.model_copy(
        update={"pages": patched_pages}
    )
    state.sources["am-ibor-2026"]["custody_pdf"]["approved_scan_pages"] = [4]
    state.append_event_or_receipt(
        "am-ibor-2026",
        record_kind="audit_log",
        run_id="r-empty",
        payload=AuditLogRecord(
            event_type="SCAN_APPROVED",
            actor="human",
            step="step3",
            summary="Empty pages approval",
            details={
                "doc_id": "custody_pdf",
                "pages": empty_pages,
                "allow_unredacted_external_ocr": True,
            },
        ),
    )

    secrets = InMemorySecretStore()
    with LoopbackFakeExternalMcpServer(server_id="ext_doc") as fake_srv:
        settings = InMemorySettingsStore(
            [fake_srv.server_spec(allow_scout_calls=True, allow_document_pages=True)]
        )
        gw = ExternalMcpGateway(state_store=state, secret_store=secrets, settings_store=settings)
        with pytest.raises(OpenDVError) as exc_scan:
            await gw.invoke_tool(
                project_id="am-ibor-2026",
                run_id="r-empty",
                server_id="ext_doc",
                tool_name="extract_clause",
                arguments={
                    "doc_page": {
                        "$opendv_handle": "doc_page",
                        "doc_id": "custody_pdf",
                        "page_number": 4,
                    }
                },
            )
        assert exc_scan.value.error_code == ErrorCode.UNREDACTED_SCAN_BLOCKED
