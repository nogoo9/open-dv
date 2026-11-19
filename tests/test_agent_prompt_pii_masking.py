"""Phase 2 Track B TDD Tests: Prompt Privacy, <= 5 PII-Masked Samples/Column & do_not_send_to_agent Exclusion.

Covers:
- FR-AGT-04 / D6: Agent prompts include <= 5 PII-masked sample values per column (<MASKED_PII>
  via (?<![A-Za-z0-9]) boundaries) and strictly exclude all chunks/metadata from do_not_send_to_agent=true sources.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from opendv_agent_harness.models import (
    AgentTaskSpec,
    HarnessConfig,
    HarnessRunResult,
    HarnessStopReason,
    HarnessUsageMetrics,
    ModelTier,
)
from opendv_contracts.dtos.ingestion import EvidenceChunk, TaintClassification, TableStructure
from opendv_contracts.dtos.ontology import AddConceptOp, EvidenceRef, OntologyDelta, OntologyProposal
from opendv_core.genesis import create_genesis_ontology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.job_runners import run_construct_session
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter


class _PromptCapturingHarness:
    """Spy VaultAgentHarness capturing all prompts passed to run_task()."""

    def __init__(self, tmp_path: Path) -> None:
        self.captured_prompts: list[str] = []
        self._config = HarnessConfig(
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
        return self._config

    def resolve_model(self, tier: ModelTier, persona: Any = None) -> str:
        return "claude-sonnet-4-5"

    async def cancel_active_turn(self, task_id: str) -> None:
        return None

    async def stream_task(self, spec: AgentTaskSpec[Any]) -> Any:
        if False:
            yield  # pragma: no cover

    async def run_task(self, spec: AgentTaskSpec[Any], *, on_event: Any = None) -> HarnessRunResult[Any]:
        self.captured_prompts.append(spec.prompt)
        base = create_genesis_ontology("custody_ops")
        delta = OntologyDelta(
            delta_id="d-cap-01",
            parent_sha256=compute_ontology_sha256(base),
            base_version="0.0.0",
            target_version="1.0.0",
            domain_namespace="custody_ops",
            operations=[
                AddConceptOp(
                    op_id="op-cap-1",
                    concept_uri="urn:ontology:entity:beneficial_owner",
                    concept_name="Beneficial Owner",
                    target_hub="hub_beneficial_owner",
                    definition="Beneficial owner record.",
                    rationale="Observed in source table.",
                    evidence_refs=[
                        EvidenceRef(evidence_type="column_profile", bronze_table="owners", column_name="tax_id", confidence=0.9)
                    ],
                )
            ],
        )
        prop = OntologyProposal(
            proposal_id="prop-cap-01",
            recommender_id="agent:cap",
            base_ontology_sha256=compute_ontology_sha256(base),
            delta=delta,
            confidence=0.9,
            rationale="Captured prompt test proposal",
        )
        return HarnessRunResult(
            task_id=spec.task_id,
            run_id=spec.run_id,
            session_id="cap-session",
            trace_id=spec.trace_id,
            span_id=uuid.uuid4().hex[:16],
            provider="claude",
            model_used="claude-sonnet-4-5",
            stop_reason=HarnessStopReason.COMPLETED,
            text_output=prop.model_dump_json(),
            usage=HarnessUsageMetrics(input_tokens=50, output_tokens=50, total_tokens=100, cost_usd=0.005),
            structured_output=prop,
        )


def test_agent_prompt_caps_sample_values_at_five_and_masks_pii(tmp_path: Path) -> None:
    """Agent prompt includes <= 5 sample values per column and masks PII with <MASKED_PII>."""
    harness = _PromptCapturingHarness(tmp_path)
    runner = StudioAgentRunner(harness=harness)

    rows = [[f"OWN-{i:02d}", f"taxpayer_{i:02d}@private-example.org"] for i in range(15)]
    tbl = TableStructure(
        table_id="tbl-owners",
        headers=["owner_code", "beneficial_owner_email"],
        rows=rows,
        caption="Owners Table",
        taint=TaintClassification.CONFIDENTIAL,
        metadata={"pii_columns": ["beneficial_owner_email"]},
    )

    runner(
        base_ontology=create_genesis_ontology("custody_ops"),
        tables=[tbl],
        mode="agent",
    )
    assert len(harness.captured_prompts) >= 1
    combined_prompt = "\n".join(harness.captured_prompts)

    # At most 5 sample rows/values per column appear in prompt (rows 05..14 excluded)
    included_codes = [f"OWN-{i:02d}" for i in range(15) if f"OWN-{i:02d}" in combined_prompt]
    assert len(included_codes) <= 5
    for i in range(5, 15):
        assert f"OWN-{i:02d}" not in combined_prompt

    # PII email values are masked as <MASKED_PII> and raw emails never appear
    assert "<MASKED_PII>" in combined_prompt
    for i in range(15):
        assert f"taxpayer_{i:02d}@private-example.org" not in combined_prompt


def test_do_not_send_to_agent_strictly_excluded_from_prompts_and_logs(tmp_path: Path) -> None:
    """Sources flagged do_not_send_to_agent=True never appear in agent prompts or session logs."""
    studio_root = tmp_path / "studio"
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=ws_root)
    mgr.create_session(session_id="priv-excl-01", domain_namespace="custody_ops")

    secret_marker = "RESTRICTED_MNPI_SENTINEL_9941"
    mgr.add_text_source(
        "priv-excl-01",
        text=f"Internal deal memo: {secret_marker} valuation pending.",
        source_name="restricted_memo.md",
        do_not_send_to_agent=True,
    )
    mgr.add_text_source(
        "priv-excl-01",
        text="Public settlement SLA: every trade settles T+1.",
        source_name="public_sla.md",
        do_not_send_to_agent=False,
    )

    from opendv_service.studio.job_runners import run_parse_session_sources

    run_parse_session_sources(mgr, "priv-excl-01", ocr_mode="off")

    harness = _PromptCapturingHarness(tmp_path)
    runner = StudioAgentRunner(harness=harness)
    sdir = mgr.get_session_dir("priv-excl-01")
    writer = SSEJournalWriter(session_dir=sdir, job_id="job-priv-01")

    run_construct_session(
        mgr,
        "priv-excl-01",
        mode="agent",
        job_id="job-priv-01",
        agent_runner=runner,
        writer=writer,
    )

    assert len(harness.captured_prompts) >= 1
    combined_prompt = "\n".join(harness.captured_prompts)
    assert secret_marker not in combined_prompt

    journal_text = (sdir / "jobs" / "job-priv-01.events.jsonl").read_text(encoding="utf-8")
    assert secret_marker not in journal_text
