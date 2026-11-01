"""Pydantic v2 models for Unified Vault Observability Telemetry Events (`VaultObservabilityEvent`).

Strictly mirrors `schemas/vault-observability-event.schema.json` including all `allOf`
cross-field phase/gate/step/remediation invariants.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RULE_ID_RE = re.compile(
    r"^(DV-[A-Z]+-[0-9]{3}|ALG-A[0-9A-Za-z]+|INV-[0-9]{2}|KEY-[0-9]{2}|TMP-[0-9]{2}|EVO-[0-9]{2}|U[1-4]|AP-[0-9]{2})$"
)

type StageIdLiteral = Literal[
    "STAGE_00_ORCHESTRATION",
    "STAGE_01_SCOUT_SOURCE",
    "STAGE_02_SCOUT_UNSTRUCTURED",
    "STAGE_03_SHIP_VAULT_PASS1",
    "STAGE_04_SHIP_VAULT_PASS2",
    "STAGE_05_AXI_DV_STATIC_GATES",
    "STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
    "STAGE_07_DETERMINISTIC_COMPILER",
]

type PhaseIdLiteral = Literal[
    "P1_INGESTION",
    "P2_PROFILING",
    "P3_EXTRACTION",
    "P4_SYNTHESIS_PASS1",
    "P5_SYNTHESIS_PASS2",
    "P6_VERIFICATION",
    "P7_DYNAMIC_SANDBOX",
    "P8_OPERATIONS",
]

type StepIdLiteral = Literal[
    "VIZ-PIPE-00",
    "VIZ-STEP-01",
    "VIZ-STEP-02",
    "VIZ-STEP-03",
    "VIZ-STEP-04",
    "VIZ-STEP-05",
    "VIZ-STEP-06",
    "VIZ-STEP-07",
    "VIZ-STEP-08",
]

type GateIdLiteral = Literal[
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G_DYN",
    "G_AUD",
]

type WorkbenchViewIdLiteral = Literal[
    "WB-00",
    "WB-01",
    "WB-02",
    "WB-03",
    "WB-04",
    "WB-05",
    "WB-06",
    "WB-07",
    "WB-08",
    "WB-09",
    "WB-10",
    "WB-11",
]

type VizIdLiteral = Literal[
    "VIZ-PIPE-00",
    "VIZ-STEP-01",
    "VIZ-STEP-02",
    "VIZ-STEP-03",
    "VIZ-STEP-04",
    "VIZ-STEP-05",
    "VIZ-STEP-06",
    "VIZ-STEP-07",
    "VIZ-STEP-08",
    "VIZ-GATE-01",
    "VIZ-GATE-02",
    "VIZ-GATE-03",
    "VIZ-GATE-04",
    "VIZ-GATE-05",
    "VIZ-GATE-06",
    "VIZ-GATE-07",
    "VIZ-GATE-08",
]

type AgentPersonaLiteral = Literal[
    "vault-first-mate",
    "scout-source",
    "scout-unstructured",
    "ship-vault",
    "axi-dv",
    "deterministic-compiler",
]

type EventStatusLiteral = Literal[
    "PASS",
    "AUTO-FIX",
    "AUTO_REPAIRED",
    "REJECT-WITH-TRACE",
    "ASK-USER",
]

type DataClassificationLiteral = Literal[
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "MNPI",
    "RESTRICTED_PII",
]

type SummaryMetricValue = int | float | str | bool

PHASE_ALLOWED_STEPS: dict[str, set[str]] = {
    "P1_INGESTION": {"VIZ-PIPE-00", "VIZ-STEP-01"},
    "P2_PROFILING": {"VIZ-STEP-02", "VIZ-STEP-03"},
    "P3_EXTRACTION": {"VIZ-STEP-04"},
    "P4_SYNTHESIS_PASS1": {"VIZ-STEP-05"},
    "P5_SYNTHESIS_PASS2": {"VIZ-STEP-06"},
    "P8_OPERATIONS": {"VIZ-STEP-07", "VIZ-STEP-08"},
}


class StrictObservabilityModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EventRemediation(StrictObservabilityModel):
    """Mandatory structured codemod payload when `status == 'AUTO_REPAIRED'`."""

    codemod_type: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    patch_diff: str = Field(min_length=1)
    action_description: str | None = None


class EventContextRefs(StrictObservabilityModel):
    """Content-addressable SHA-256 artifact references under `state/artifacts/<sha256>`."""

    graph_a_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    graph_b_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    contract_yaml_pre_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    contract_yaml_post_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    canonical_ir_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    compiled_sql_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")
    toon_trace_sha256: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{64}$")


class Layer1StoryBanner(StrictObservabilityModel):
    """Layer 1 executive story banner for progressive-disclosure workbench cards."""

    headline: str = Field(max_length=140)
    business_impact: str = Field(max_length=200)
    verdict_pill: EventStatusLiteral


class SemanticHashPill(StrictObservabilityModel):
    """Semantic Hash Pill representation (`[HK: 5d15..0ae4 | GLOBAL:LP-101 CalPERS]`)."""

    pill_label: str
    full_sha256_hex: str = Field(pattern=r"^[0-9A-Fa-f]{64}$")
    bkcc_prefix: str
    business_key: str
    entity_label: str


class Layer2VisualPayload(BaseModel):
    """Layer 2 visual canvas payload (allows view-specific extra properties per schema)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    semantic_hash_pills: list[SemanticHashPill]
    summary_metrics: dict[str, SummaryMetricValue]


class Layer3FormalProof(StrictObservabilityModel):
    """Layer 3 on-demand formal proof drawer payload (collapsed by default in UI)."""

    algorithm_ref: str | None = None
    formula_latex: str | None = None
    toon_diagnostic_payload: str | None = None
    contract_yaml_diff: str | None = None
    sqlglot_ast_summary: str | None = None


class VaultObservabilityEvent(StrictObservabilityModel):
    """Root telemetry event model (`schemas/vault-observability-event.schema.json`)."""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    timestamp_utc: datetime
    stage_id: StageIdLiteral
    phase_id: PhaseIdLiteral | None = None
    step_id: StepIdLiteral | None = None
    gate_id: GateIdLiteral | None = None
    rule_code: str | None = Field(default=None, min_length=1)
    contract_version: Literal["5.0", "6.0"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    workbench_view_id: WorkbenchViewIdLiteral
    viz_id: VizIdLiteral
    agent_persona: AgentPersonaLiteral
    status: EventStatusLiteral
    remediation: EventRemediation | None = None
    rule_ids_evaluated: list[str] | None = None
    context_refs: EventContextRefs | None = None
    data_classification: DataClassificationLiteral | None = None
    layer1_story_banner: Layer1StoryBanner
    layer2_visual_payload: Layer2VisualPayload
    layer3_formal_proof: Layer3FormalProof | None = None

    @model_validator(mode="after")
    def _validate_all_of_invariants(self) -> VaultObservabilityEvent:
        if self.rule_ids_evaluated is not None:
            for rule_id in self.rule_ids_evaluated:
                if not RULE_ID_RE.match(rule_id):
                    msg = f"Invalid rule_id '{rule_id}' in rule_ids_evaluated"
                    raise ValueError(msg)

        if self.phase_id == "P6_VERIFICATION":
            if self.gate_id is None or self.rule_code is None:
                msg = "gate_id and rule_code are required when phase_id is P6_VERIFICATION"
                raise ValueError(msg)
        else:
            if self.gate_id is not None:
                msg = "gate_id is prohibited when phase_id is not P6_VERIFICATION"
                raise ValueError(msg)

        if self.phase_id in {"P6_VERIFICATION", "P7_DYNAMIC_SANDBOX"} and self.step_id is not None:
            msg = f"step_id is prohibited when phase_id is {self.phase_id}"
            raise ValueError(msg)

        if (
            self.phase_id is not None
            and self.step_id is not None
            and self.phase_id in PHASE_ALLOWED_STEPS
        ):
            allowed = PHASE_ALLOWED_STEPS[self.phase_id]
            if self.step_id not in allowed:
                msg = (
                    f"step_id '{self.step_id}' is not valid for phase_id '{self.phase_id}'; "
                    f"expected one of {sorted(allowed)}"
                )
                raise ValueError(msg)

        if self.status == "AUTO_REPAIRED" and self.remediation is None:
            msg = "remediation is required when status is AUTO_REPAIRED"
            raise ValueError(msg)

        return self


__all__ = [
    "AgentPersonaLiteral",
    "DataClassificationLiteral",
    "EventContextRefs",
    "EventRemediation",
    "EventStatusLiteral",
    "GateIdLiteral",
    "Layer1StoryBanner",
    "Layer2VisualPayload",
    "Layer3FormalProof",
    "PhaseIdLiteral",
    "SemanticHashPill",
    "StageIdLiteral",
    "StepIdLiteral",
    "SummaryMetricValue",
    "VaultObservabilityEvent",
    "VizIdLiteral",
    "WorkbenchViewIdLiteral",
]
