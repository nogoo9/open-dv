"""Phase 2 Track B TDD Tests: Strict Agent-First Gate, Real Harness Invocation & CLI-Only Deterministic Tag.

Covers:
- FR-AGT-01: Default mode='agent' + stage_output_policy='strict' fails closed with 422 AGENT_NOT_CONFIGURED.
- FR-AGT-03 / Audit #1/#11: StudioAgentRunner invokes VaultAgentHarness.run_task(..., on_event=...)
  across @term-scout -> @domain-clarifier -> @ontology-architect and reads active provider from PlatformStateContainer per run.
- FR-AGT-11 / D28: mode='deterministic' is CLI/API-only and tagged mode=deterministic in logs/run metadata.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness.models import (
    AgentPersona,
    AgentTaskSpec,
    HarnessConfig,
    HarnessEventKind,
    HarnessRunResult,
    HarnessStopReason,
    HarnessStreamEvent,
    HarnessUsageMetrics,
    ModelTier,
    PipelineStageId,
)
from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_contracts.dtos.ingestion import EvidenceChunk
from opendv_contracts.dtos.ontology import AddConceptOp, EvidenceRef, OntologyDelta, OntologyProposal
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.genesis import create_genesis_ontology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings, PlatformStateContainer
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.job_runners import run_construct_session
from opendv_service.studio.session_manager import StudioSessionManager


class _SpyHarness:
    """In-memory spy implementing VaultAgentHarness protocol to verify real run_task calls."""

    def __init__(self, tmp_path: Path, provider: str = "claude-cli") -> None:
        self._provider = provider
        self.run_task_calls: list[dict[str, Any]] = []
        self._config = HarnessConfig(
            provider=provider,  # type: ignore[arg-type]
            workspace_dir=tmp_path,
            state_dir=tmp_path / "s",
            app_data_dir=tmp_path / "a",
            save_dir=tmp_path / "sv",
        )

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def config(self) -> HarnessConfig:
        return self._config

    def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
        return "claude-sonnet-4-5"

    async def cancel_active_turn(self, task_id: str) -> None:
        return None

    async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
        if False:
            yield  # pragma: no cover

    async def run_task(
        self,
        spec: AgentTaskSpec[Any],
        *,
        on_event: Any = None,
    ) -> HarnessRunResult[Any]:
        self.run_task_calls.append({"task_id": spec.task_id, "prompt": spec.prompt, "has_on_event": on_event is not None})
        if on_event is not None:
            on_event(
                HarnessStreamEvent(
                    kind=HarnessEventKind.TEXT_DELTA,
                    task_id=spec.task_id,
                    run_id=spec.run_id,
                    trace_id=spec.trace_id,
                    span_id=uuid.uuid4().hex[:16],
                    provider=self._provider,
                    persona=AgentPersona.ONTOLOGY_ARCHITECT,
                    stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
                    delta_text="Synthesizing custom concept",
                )
            )
        base = create_genesis_ontology("custody_ops")
        delta = OntologyDelta(
            delta_id="d-spy-01",
            parent_sha256=compute_ontology_sha256(base),
            base_version="0.0.0",
            target_version="1.0.0",
            domain_namespace="custody_ops",
            operations=[
                AddConceptOp(
                    op_id="op-spy-1",
                    concept_uri="urn:ontology:entity:settlement_instruction",
                    concept_name="Settlement Instruction",
                    target_hub="hub_settlement_instruction",
                    definition="Instruction to settle a securities trade.",
                    rationale="Observed in source evidence.",
                    evidence_refs=[
                        EvidenceRef(evidence_type="document_span", snippet="Settlement SLA 24h", confidence=0.9)
                    ],
                )
            ],
        )
        prop = OntologyProposal(
            proposal_id="prop-spy-01",
            recommender_id="agent:spy",
            base_ontology_sha256=compute_ontology_sha256(base),
            delta=delta,
            confidence=0.9,
            rationale="Spy harness proposal",
        )
        return HarnessRunResult(
            task_id=spec.task_id,
            run_id=spec.run_id,
            session_id="spy-session",
            trace_id=spec.trace_id,
            span_id=uuid.uuid4().hex[:16],
            provider=self._provider,
            model_used="claude-sonnet-4-5",
            stop_reason=HarnessStopReason.COMPLETED,
            text_output=prop.model_dump_json(),
            usage=HarnessUsageMetrics(input_tokens=120, output_tokens=80, total_tokens=200, cost_usd=0.01),
            structured_output=prop,
        )


def test_harness_setup_config_defaults_to_strict_and_error_code_exists() -> None:
    """HarnessSetupConfig.stage_output_policy defaults to 'strict' and ErrorCode.AGENT_NOT_CONFIGURED is 422."""
    cfg = HarnessSetupConfig()
    assert cfg.stage_output_policy == "strict"
    assert hasattr(ErrorCode, "AGENT_NOT_CONFIGURED")
    err = OpenDVError("No agent configured", error_code=ErrorCode.AGENT_NOT_CONFIGURED)
    assert err.http_status == 422


def test_studio_agent_runner_fails_closed_when_unconfigured_zero_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unconfigured StudioAgentRunner and POST /construct mode='agent' fail closed with 422 AGENT_NOT_CONFIGURED."""
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENDV_AGENT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", "")

    runner = StudioAgentRunner(harness=None, harness_config=None)
    with pytest.raises(OpenDVError) as exc_info:
        runner(
            base_ontology=create_genesis_ontology("custody_ops"),
            evidence_chunks=[
                EvidenceChunk(chunk_id="c1", document_id="d1", text="Settlement instruction within 24h.")
            ],
            mode="agent",
        )
    assert exc_info.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED
    assert exc_info.value.http_status == 422

    # Also verify REST /construct rejects unconfigured agent mode with 422 AGENT_NOT_CONFIGURED
    studio_root = tmp_path / "studio"
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    settings = OpenDVSettings.load(workspace_root=ws_root, state_dir=tmp_path / "state", is_server=True)
    app = create_app(settings=settings, workspace_root=ws_root, storage_root=tmp_path / "state")
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")
    client.post("/api/v1/ontology/studio/sessions", json={"session_id": "gate-422-01", "domain_namespace": "custody_ops"})

    resp = client.post(
        "/api/v1/ontology/studio/sessions/gate-422-01/construct",
        json={"mode": "agent", "base_version": "0.0.0"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error_code") == "AGENT_NOT_CONFIGURED"


def test_studio_agent_runner_invokes_vault_agent_harness_run_task_with_on_event(tmp_path: Path) -> None:
    """StudioAgentRunner actually invokes VaultAgentHarness.run_task(..., on_event=...) rather than local stubs."""
    spy = _SpyHarness(tmp_path)
    received_events: list[HarnessStreamEvent] = []

    runner = StudioAgentRunner(harness=spy)
    proposal = runner(
        base_ontology=create_genesis_ontology("custody_ops"),
        evidence_chunks=[
            EvidenceChunk(chunk_id="c1", document_id="d1", text="Settlement instruction SLA is T+1.")
        ],
        mode="agent",
        on_event=received_events.append,
    )
    assert len(spy.run_task_calls) >= 1, "StudioAgentRunner must invoke harness.run_task()"
    assert spy.run_task_calls[0]["has_on_event"] is True
    assert len(received_events) >= 1
    assert len(proposal.delta.operations) >= 1


def test_studio_agent_runner_reads_active_provider_from_platform_state_container_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """StudioAgentRunner resolves active provider config dynamically from PlatformStateContainer per run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")
    state = PlatformStateContainer(workspace_root=tmp_path, storage_root=tmp_path / "state")
    state.update_platform_config({"harness": {"provider": "claude"}})

    observed_providers: list[str] = []

    def _spy_factory(cfg: HarnessConfig) -> _SpyHarness:
        observed_providers.append(cfg.provider)
        return _SpyHarness(tmp_path, provider=cfg.provider)

    runner = StudioAgentRunner(state_container=state, harness_factory=_spy_factory)  # type: ignore[call-arg]
    runner(
        base_ontology=create_genesis_ontology("custody_ops"),
        evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Custody account balance.")],
        mode="agent",
    )
    assert observed_providers[-1] == "claude"

    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-test-key-456")
    state.update_platform_config({"harness": {"provider": "antigravity"}})
    runner(
        base_ontology=create_genesis_ontology("custody_ops"),
        evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Custody account balance.")],
        mode="agent",
    )
    assert observed_providers[-1] == "antigravity"


def test_deterministic_mode_cli_only_tag_in_logs_and_metadata(tmp_path: Path) -> None:
    """Explicit mode='deterministic' tags run metadata and session journal with mode=deterministic."""
    studio_root = tmp_path / "studio"
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=ws_root)
    mgr.create_session(session_id="det-tag-01", domain_namespace="custody_ops")
    mgr.add_text_source("det-tag-01", text="Trade order entity with trade_id.", source_name="note.md")

    from opendv_service.studio.sse_journal import SSEJournalWriter

    sdir = mgr.get_session_dir("det-tag-01")
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-det-01")
    res = run_construct_session(mgr, "det-tag-01", mode="deterministic", job_id="job-det-01", writer=writer)
    assert res.get("mode") == "deterministic"

    journal_lines = (sdir / "jobs" / "job-det-01.events.jsonl").read_text(encoding="utf-8")
    assert "mode=deterministic" in journal_lines or '"mode": "deterministic"' in journal_lines


def test_build_safe_agent_prompt_includes_base_ontology_version_and_sha256() -> None:
    """Fix 4 TDD: build_safe_agent_prompt emits base_ontology_version and base_ontology_sha256 in JSON payload."""
    from opendv_service.studio.agent_runner_helpers import build_safe_agent_prompt

    genesis = create_genesis_ontology("custody_ops")
    expected_sha = compute_ontology_sha256(genesis)
    prompt = build_safe_agent_prompt(
        base_ontology=genesis,
        evidence_chunks=[EvidenceChunk(chunk_id="c1", document_id="d1", text="Settlement SLA 24h.")],
        tables=[],
        clarification_answers={},
        fibo_modules=[],
        steering_prompt=None,
    )
    json_part = prompt[prompt.index("{") :]
    payload = json.loads(json_part)
    assert payload["base_ontology_version"] == "0.0.0"
    assert payload["base_ontology_sha256"] == expected_sha
    assert expected_sha in prompt


def test_run_construct_session_handles_dict_external_mappings_with_writer(
    tmp_path: Path,
) -> None:
    """Fix 5 TDD: run_construct_session buffers fibo_grounding when external_mappings are dicts."""
    from opendv_service.studio.sse_journal import SSEJournalWriter

    mgr = StudioSessionManager(studio_root=tmp_path / "studio", workspace_root=tmp_path / "ws")
    mgr.create_session(session_id="ext-map-dict-01", domain_namespace="custody_ops")
    mgr.add_text_source("ext-map-dict-01", text="Custody Account entity.", source_name="note.md")
    base = create_genesis_ontology("custody_ops")
    base_sha = compute_ontology_sha256(base)
    fibo_acct = (
        "https://spec.edmcouncil.org/fibo/ontology/FBC/"
        "ProductsAndServices/ClientsAndAccounts/Account"
    )

    def _dict_mapping_runner(**_kwargs: Any) -> OntologyProposal:
        return OntologyProposal(
            proposal_id="prop-dict-map-01",
            recommender_id="agent:job-dict-01",
            base_ontology_sha256=base_sha,
            delta=OntologyDelta(
                delta_id="d-dict-01",
                parent_sha256=base_sha,
                base_version="0.0.0",
                target_version="0.1.0",
                domain_namespace="custody_ops",
                operations=[
                    AddConceptOp(
                        op_id="op-dict-1",
                        concept_uri="urn:ontology:entity:custody_account",
                        concept_name="Custody Account",
                        target_hub="hub_custody_account",
                        definition="Account holding securities.",
                        external_mappings=[
                            {
                                "scheme": "fibo",
                                "target_uri": fibo_acct,
                                "confidence": 0.91,
                            }
                        ],
                        evidence_refs=[
                            EvidenceRef(
                                evidence_type="document_span",
                                snippet="Custody Account",
                                confidence=0.9,
                            )
                        ],
                    )
                ],
            ),
            confidence=0.9,
            rationale="Dict external mapping test",
        )

    sdir = mgr.get_session_dir("ext-map-dict-01")
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-dict-01")
    res = run_construct_session(
        mgr,
        "ext-map-dict-01",
        mode="agent",
        job_id="job-dict-01",
        writer=writer,
        agent_runner=_dict_mapping_runner,
    )
    assert res["proposal_id"] == "prop-dict-map-01"
    journal_text = (sdir / "jobs" / "job-dict-01.events.jsonl").read_text(encoding="utf-8")
    assert "fibo_grounding" in journal_text
    assert "ClientsAndAccounts/Account" in journal_text


def test_build_safe_agent_prompt_budgets_350_plus_chunks_and_preserves_inline_notes_tables_and_masked_samples() -> None:
    """Prompt budgeting keeps <= CoreModelingConfig limits while preserving inline notes, tables, and <= 5 masked samples."""
    from opendv_agent_harness.adapters.cli_subprocess_base import build_cli_prompt
    from opendv_contracts.dtos.ingestion import TableStructure
    from opendv_core.config import CoreModelingConfig
    from opendv_service.studio.agent_runner_helpers import build_safe_agent_prompt

    cfg = CoreModelingConfig()
    assert cfg.max_agent_prompt_chars <= 40_000
    assert cfg.max_agent_prompt_chunks >= 10
    assert cfg.max_agent_prompt_chunk_chars >= 200
    assert cfg.small_inline_note_max_chars >= 200

    genesis = create_genesis_ontology("custody_ops")
    chunks: list[EvidenceChunk] = [
        EvidenceChunk(
            chunk_id=f"doc_pdf_chk_{i:04d}",
            document_id="src-pdf-01",
            page_number=(i // 5) + 1,
            text=(
                f"Section {i}: General regulatory governance policy clause describing operational "
                f"procedures, escalation matrices, and archival controls across business units. "
                + ("Detail text padding for realistic PDF chunk density. " * 14)
            ),
            metadata={"filename": "regulatory_policy.pdf", "source_id": "src-pdf-01"},
        )
        for i in range(352)
    ]
    chunks.append(
        EvidenceChunk(
            chunk_id="doc_pdf_tbl_01",
            document_id="src-pdf-01",
            page_number=15,
            is_table=True,
            table_id="tbl_pdf_1",
            text="FEE_CODE | FEE_BASIS | SETTLEMENT_CYCLE\nCUST_01 | BPS | T+1",
            metadata={
                "filename": "regulatory_policy.pdf",
                "source_id": "src-pdf-01",
                "layout_complexity": "TABULAR",
            },
        )
    )
    chunks.append(
        EvidenceChunk(
            chunk_id="doc_pdf_tbl_02",
            document_id="src-pdf-01",
            page_number=30,
            text="RATE_TIER | THRESHOLD_USD | APPROVAL_ROLE",
            metadata={
                "filename": "regulatory_policy.pdf",
                "source_id": "src-pdf-01",
                "table_data": [["T1", "1000000", "STEWARD"]],
            },
        )
    )
    chunks.append(
        EvidenceChunk(
            chunk_id="doc_inline_note_01",
            document_id="src-note-01",
            text="Settlement instructions arriving after 16:30 EST incur T+2 exception escalation.",
            metadata={"filename": "domain-note.md", "source_id": "src-note-01"},
        )
    )
    chunks.append(
        EvidenceChunk(
            chunk_id="doc_secret_01",
            document_id="src-secret-01",
            text="RESTRICTED_SECRET_MUST_NOT_APPEAR",
            metadata={
                "filename": "secret.txt",
                "source_id": "src-secret-01",
                "do_not_send_to_agent": True,
            },
        )
    )

    tbl = TableStructure(
        table_id="tbl-accounts",
        headers=["account_id", "beneficial_owner_email"],
        rows=[[f"ACC-{i:02d}", f"owner{i}@example.org"] for i in range(10)],
        caption="Custody accounts",
        metadata={"pii_columns": ["beneficial_owner_email"]},
    )

    prompt = build_safe_agent_prompt(
        base_ontology=genesis,
        evidence_chunks=chunks,
        tables=[tbl],
        clarification_answers={"cutoff_rule": "16:30 EST"},
        fibo_modules=["FBC"],
        steering_prompt="Prioritize settlement exception escalation concepts.",
    )
    assert len(prompt) <= cfg.max_agent_prompt_chars
    assert "RESTRICTED_SECRET_MUST_NOT_APPEAR" not in prompt
    assert "Settlement instructions arriving after 16:30 EST incur T+2 exception escalation." in prompt
    assert "FEE_CODE | FEE_BASIS | SETTLEMENT_CYCLE" in prompt
    assert "RATE_TIER | THRESHOLD_USD | APPROVAL_ROLE" in prompt
    assert "Prioritize settlement exception escalation concepts." in prompt

    payload = json.loads(prompt[prompt.index("{") :])
    assert len(payload["chunks"]) <= cfg.max_agent_prompt_chunks
    assert len(payload["tables"]) == 1
    assert len(payload["tables"][0]["sample_rows"]) <= 5
    assert "<MASKED_PII>" in json.dumps(payload["tables"][0])
    assert "owner0@example.org" not in prompt
    assert "ACC-05" not in prompt

    spec = AgentTaskSpec(
        run_id="run-budget-check",
        trace_id="a" * 32,
        persona=AgentPersona.ONTOLOGY_ARCHITECT,
        stage_id=PipelineStageId.STAGE_04_SHIP_VAULT_PASS2,
        prompt=prompt,
        system_instructions="Propose OntologyProposal.",
        output_schema=OntologyProposal,
    )
    cli_prompt = build_cli_prompt(spec, subagent_models={}, include_inline_schema=True)
    assert "Return ONLY a valid JSON object conforming to this JSON Schema" in cli_prompt
    assert len(cli_prompt.encode("utf-8")) <= 95_000



