"""Shared deterministic fixtures and loopback server helpers for `tests/cli/`."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml

from opendv.agents._cegar_models import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
from opendv.api.server import create_app
from opendv.models import (
    BusinessKeyPart,
    CanonicalBusinessConcept,
    ClarificationQuestion,
    ConceptAttribute,
    DraftField,
    DraftHubSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    ErrorCode,
    GraphAOntology,
    OpenDVError,
    ColumnTriageEntry,
)
from opendv.service import OpenDVService
from opendv.service.workflows._discovery_run import Step4CoordinatorOutput


def build_account_ontology(*, version: str = "1.0.0", extra_concept: str | None = None) -> GraphAOntology:
    """Create a valid minimal GraphAOntology matching `accounts.csv`."""
    concepts = [
        CanonicalBusinessConcept(
            concept_uri="urn:ibor:Account",
            concept_name="Account",
            description="Client investment account.",
            key_authority_tier="TIER_1_GLOBAL_STANDARD",
            attributes=[
                ConceptAttribute(
                    name="account_id",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    is_business_key=True,
                    is_pii=False,
                    nullable=False,
                ),
                ConceptAttribute(
                    name="account_name",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    is_business_key=False,
                    is_pii=False,
                    nullable=False,
                ),
                ConceptAttribute(
                    name="status",
                    logical_type="UTF8_STRING",
                    sql_type_hint="VARCHAR",
                    is_business_key=False,
                    is_pii=False,
                    nullable=False,
                ),
            ],
        )
    ]
    if extra_concept:
        concepts.append(
            CanonicalBusinessConcept(
                concept_uri=f"urn:ibor:{extra_concept}",
                concept_name=extra_concept,
                description=f"Added concept {extra_concept}.",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                attributes=[
                    ConceptAttribute(
                        name=f"{extra_concept.lower()}_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        is_business_key=True,
                        is_pii=False,
                        nullable=False,
                    )
                ],
            )
        )
    return GraphAOntology(
        ontology_version=version,
        domain_namespace="ibor",
        concepts=concepts,
    )


def build_account_ontology_yaml(*, version: str = "1.0.0", extra_concept: str | None = None) -> str:
    """Serialize `build_account_ontology` cleanly as YAML."""
    ont = build_account_ontology(version=version, extra_concept=extra_concept)
    return yaml.safe_dump(ont.model_dump(mode="json"), sort_keys=False)


def build_account_draft_ir(
    project_id: str,
    source_id: str,
    table_id: str,
    *,
    ontology_version: str = "1.0.0",
    unresolved: bool = False,
    hold_question: ClarificationQuestion | None = None,
) -> DraftVaultIR:
    """Construct a minimal `DraftVaultIR` covering all columns of `accounts.csv`."""
    triage = (
        []
        if unresolved
        else [
            ColumnTriageEntry(
                table_id=table_id,
                col_name="account_id",
                disposition="MODELED",
                target_ref="hub_account",
                rationale="Business key",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="account_name",
                disposition="MODELED",
                target_ref="sat_account_core",
                rationale="Descriptive attribute",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="status",
                disposition="MODELED",
                target_ref="sat_account_core",
                rationale="Status attribute",
            ),
        ]
    )
    return DraftVaultIR(
        project_id=project_id,
        ontology_version=ontology_version,
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=table_id,
                        col_name="account_id",
                        source_id=source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="IBOR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=table_id,
                        col_mappings={"account_id": "account_id"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="account_id",
                        logical_type="UTF8_STRING",
                        is_pii=False,
                        nullable=False,
                    )
                ],
            )
        ],
        satellites=[
            DraftSatelliteSpec(
                sat_name="sat_account_core",
                sat_kind="STANDARD",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=table_id,
                        col_mappings={"account_name": "account_name", "status": "status"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="account_name",
                        logical_type="UTF8_STRING",
                        is_pii=False,
                        nullable=False,
                    ),
                    DraftField(
                        name="status",
                        logical_type="UTF8_STRING",
                        is_pii=False,
                        nullable=False,
                    ),
                ],
            )
        ],
        column_triage=triage,
        pending_clarifications=[hold_question] if hold_question is not None else [],
    )


def sample_question(qid: str = "q_acc") -> ClarificationQuestion:
    """Create a deterministic ClarificationQuestion for hold tests."""
    return ClarificationQuestion(
        question_id=qid,
        concept_name="Account",
        title="Confirm Account Key",
        prompt="Confirm whether account_id is canonical business key.",
        sample_rows=["ACC1", "ACC2"],
        impact_if_locked="Fixes hub_account business key.",
        recommended_answer="account_id",
    )


class ScriptedCliHarness:
    """Deterministic `AgentHarnessPort` implementation for CLI/HTTP/MCP tests."""

    provider_name = "agy"

    def __init__(
        self,
        *,
        project_id: str = "p1",
        unresolved_mode: bool = False,
        hold_step6_once: bool = False,
        fail_execution: bool = False,
    ) -> None:
        self.project_id = project_id
        self.source_id = "src"
        self.table_id = "tbl"
        self.unresolved_mode = unresolved_mode
        self.hold_step6_once = hold_step6_once
        self.fail_execution = fail_execution
        self._held_runs: set[str] = set()

    def bind_source(self, source_id: str, table_id: str) -> None:
        """Bind profiled source and table IDs produced by `attach_source`."""
        self.source_id = source_id
        self.table_id = table_id

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: Any = None,
        scout_dispatcher: Any = None,
    ) -> AgentTurnResult:
        """Produce deterministic structured JSON outputs matching each phase schema."""
        _ = (event_sink, scout_dispatcher)
        if self.fail_execution:
            raise OpenDVError(
                "Simulated harness execution error.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )

        out: Any
        if request.step == "step4":
            out = Step4CoordinatorOutput(
                proposed_ops=[],
                clarification_questions=[],
                staged_candidate_ids=[],
                summary="Constructed baseline ontology concepts cleanly.",
            )
        elif request.output_schema is SemanticCriticVerdict:
            out = SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])
        elif request.output_schema is HorizonCriticVerdict:
            out = HorizonCriticVerdict(
                score=95,
                classification="REASONABLY_STABLE",
                absorbed_natively=[],
                bucket_a_ops=[],
                bucket_b_questions=[],
                bucket_c_watchlist=[],
            )
        else:
            q: ClarificationQuestion | None = None
            if self.hold_step6_once and request.run_id not in self._held_runs:
                self._held_runs.add(request.run_id)
                q = sample_question("q1")
            out = build_account_draft_ir(
                self.project_id,
                self.source_id,
                self.table_id,
                unresolved=self.unresolved_mode,
                hold_question=q,
            )
        return AgentTurnResult(
            agent_id=request.agent_id,
            provider="agy",
            model_id="scripted-model",
            text_output=out.model_dump_json(),
            structured_output=out,
        )


def create_fake_agy_binary(
    directory: Path,
    *,
    source_id: str,
    table_id: str,
    project_id: str,
    unresolved: bool = False,
) -> Path:
    """Create a cross-platform fake `agy` CLI wrapper for subprocess CLI & MCP tests."""
    import sys

    py_script = directory / "fake_agy_impl.py"
    ir_template = build_account_draft_ir(
        project_id, source_id, table_id, unresolved=unresolved
    ).model_dump(mode="json")
    script_body = f'''import json, os, sys

args = sys.argv[1:]
schema = {{}}
for i, a in enumerate(args):
    if a == "--json-schema" and i + 1 < len(args):
        raw_arg = args[i + 1]
        if os.path.isfile(raw_arg):
            with open(raw_arg, encoding="utf-8") as fh:
                schema = json.load(fh)
        else:
            schema = json.loads(raw_arg)
title = schema.get("title", "")
if "SemanticCriticVerdict" in title:
    payload = {{"score": 95, "findings": [], "accepted_waiver_ids": []}}
elif "HorizonCriticVerdict" in title:
    payload = {{
        "score": 95,
        "classification": "REASONABLY_STABLE",
        "absorbed_natively": [],
        "bucket_a_ops": [],
        "bucket_b_questions": [],
        "bucket_c_watchlist": [],
    }}
elif "Step4CoordinatorOutput" in title:
    payload = {{
        "proposed_ops": [],
        "clarification_questions": [],
        "staged_candidate_ids": [],
        "summary": "Constructed via fake agy",
    }}
else:
    payload = json.loads({json.dumps(json.dumps(ir_template))})
print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": json.dumps(payload)}}}}))
'''
    py_script.write_text(script_body, encoding="utf-8")
    if os.name == "nt":
        wrapper = directory / "fake_agy.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{py_script}" %*\r\n', encoding="utf-8")
        return wrapper
    wrapper = directory / "fake_agy.sh"
    wrapper.write_text(f'#!/usr/bin/env sh\n"{sys.executable}" "{py_script}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def free_loopback_port() -> int:
    """Reserve an available ephemeral loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def run_loopback_api_server(service: OpenDVService) -> Iterator[str]:
    """Run a real local `uvicorn` HTTP FastAPI server bound to `127.0.0.1:<port>`."""
    port = free_loopback_port()
    app = create_app(service=service)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{base_url}/api/v1/health", timeout=0.5)
                if resp.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Timed out waiting for test loopback uvicorn server to listen.")
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
