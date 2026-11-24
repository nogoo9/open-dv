"""Wire DTOs for Step 6 Vault Synthesis, Horizon Critic, Guided UX, and Step 7 Storage."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from opendv.models.diagnostics import (
    HorizonOptionSpec,
    HorizonQuestion,
    StrictModel,
    VerifierDiagnostic,
)
from opendv.models.policy import CegarPolicy
from opendv.models.project import DbtSqlDialect, TargetManifest
from opendv.models.vault_ir import DraftVaultIR

BucketAOperator = Literal[
    "SET_BKCC",
    "WIDEN_TYPE_TO_DECIMAL",
    "SPLIT_HIGH_FREQ_OR_PII_SAT",
    "PROMOTE_COLUMN_TO_HUB",
    "PROMOTE_HUB_BK_TO_IMMUTABLE",
    "INJECT_MAS_CDK",
]


class VaultBuildReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/runs."""

    policy: CegarPolicy = "balanced"


class WaiverSubmitReq(StrictModel):
    """Steward V6b waiver submission payload."""

    waiver_id: str
    rule_id: str
    entity_name: str
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _validate_rationale_len(cls, v: str) -> str:
        if len(v.strip()) < 20:
            raise ValueError("Waiver rationale must be at least 20 non-whitespace characters")
        return v


class VaultVerifyResp(StrictModel):
    """Response for POST /api/v1/projects/{id}/vault/verify."""

    passed: bool
    diagnostics: list[VerifierDiagnostic]


class HorizonActionReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/runs/{rid}/horizon."""

    action: Literal["accept", "steer"]
    answers: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    added_fields: list[str] = Field(default_factory=list)


class BucketAOperatorCall(StrictModel):
    """Deterministic structural steer operator applied before L1."""

    operator: BucketAOperator
    target_hub: str | None = None
    sat_name: str | None = None
    table_id: str | None = None
    source_column: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


class SemanticCriticVerdict(StrictModel):
    """V7 Semantic Critic scoring verdict."""

    score: int = Field(ge=0, le=100)
    findings: list[str] = Field(default_factory=list)
    accepted_waiver_ids: list[str] = Field(default_factory=list)


class HorizonCriticVerdict(StrictModel):
    """V8 Horizon Critic architectural resilience verdict."""

    score: int = Field(ge=0, le=100)
    classification: Literal["REASONABLY_STABLE", "NEEDS_STEER", "HUMAN_REVIEW_REQUIRED"]
    absorbed_natively: list[str] = Field(default_factory=list)
    bucket_a_ops: list[BucketAOperatorCall] = Field(default_factory=list)
    bucket_b_questions: list[HorizonQuestion] = Field(default_factory=list)
    bucket_c_watchlist: list[str] = Field(default_factory=list)


class AttemptSnapshotSpec(StrictModel):
    """Per-attempt CEGAR snapshot persisted in events_and_receipts."""

    attempt: int = Field(ge=0)
    ir_sha256: str
    energy_e: int | None = None
    v7_score: int | None = None
    diagnostics: list[VerifierDiagnostic] = Field(default_factory=list)
    draft_ir: DraftVaultIR
    horizon_verdict: HorizonCriticVerdict | None = None


class GuidedEntityCardSpec(StrictModel):
    """Plain-English business entity card for Step 6 Guided view."""

    entity_id: str
    title: str
    badge_label: str
    summary: str
    change_impact: Literal[
        "FLEXIBLE",
        "LOCK_IN",
        "PRIVACY_ISOLATED",
        "COMPANION_TABLE",
        "BUSINESS_READY",
    ]


class GuidedTableExplainerSpec(StrictModel):
    """Plain-English table existence and flexibility explainer."""

    business_purpose: str
    history_protection: str
    flexible_here: str
    lock_in_contrast: str


class GuidedWaiverTargetSpec(StrictModel):
    """Target binding for a plain-English V6b waiver card."""

    waiver_id: str
    rule_id: str
    entity_name: str


class GuidedDecisionCard(StrictModel):
    """Plain-English Steward decision card across Step 4, Step 6 Hold, V6b, and Horizon."""

    card_id: str
    source_step: Literal["step4", "step6_hold", "step6_v6b", "step6_horizon"]
    title: str
    plain_summary: str
    why_it_matters: str
    lock_in_option_label: str
    flexible_option_label: str
    recommended_option_id: str
    options: list[HorizonOptionSpec]
    sample_evidence: list[str] = Field(default_factory=list)


class GuidedReadinessBrief(StrictModel):
    """Plain-English wave readiness summary."""

    wave_number: int = Field(ge=1)
    certification_label: Literal["UNCERTIFIED", "CERTIFIED_FAST", "CERTIFIED"]
    confidence_summary: str
    plain_highlights: list[str] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class GuidedRunView(StrictModel):
    """Complete plain-English Step 6 Guided Workbench projection."""

    headline: str
    milestone_index: int = Field(ge=0)
    steps_summary: list[str]
    decision_cards: list[GuidedDecisionCard]
    readiness_brief: GuidedReadinessBrief
    ontology_rev_id: str | None = None
    policy: CegarPolicy = "balanced"
    step4_run_id: str | None = None
    entities: list[GuidedEntityCardSpec] = Field(default_factory=list)
    explainers: dict[str, GuidedTableExplainerSpec] = Field(default_factory=dict)
    v6b_waiver_targets: dict[str, GuidedWaiverTargetSpec] = Field(default_factory=dict)
    technical_drawer: dict[str, str | int | list[str]] = Field(default_factory=dict)


class StorageCompileReq(StrictModel):
    """Request payload for POST /api/v1/projects/{id}/storage/compile."""

    target_id: str
    dbt_sql_dialect: DbtSqlDialect = "duckdb"
    verify_runtime: bool = False


class StorageTargetReq(StrictModel):
    """Request payload for migrate and regenerate endpoints."""

    target_id: str


class ArtifactFileSpec(StrictModel):
    """Single generated DDL/DML/manifest/metadata/Parquet/dbt artifact descriptor."""

    relpath: str
    kind: Literal["ddl", "dml", "manifest", "metadata", "parquet", "dbt"]
    size_bytes: int = Field(ge=0)
    sha256: str


class TargetBundleResp(StrictModel):
    """Response payload for Step 7 compilation of a single target."""

    target_id: str
    manifest: TargetManifest
    artifacts: list[ArtifactFileSpec]


class MigrationApplyResp(StrictModel):
    """Response payload for storage migrate and regenerate."""

    target_id: str
    wave_number: int = Field(ge=1)
    rows_loaded: int = Field(ge=0)
    verified: bool


class SqlQueryReq(StrictModel):
    """Read-only SQL query payload for Step 7 DuckDB runner."""

    sql: str = Field(min_length=1)
    limit: int = Field(default=1000, ge=1, le=10000)


class SqlQueryResult(StrictModel):
    """Tabular result returned by sandboxed read-only DuckDB query runner."""

    columns: list[str]
    rows: list[list[str | int | float | bool | None]]
    row_count: int = Field(ge=0)


__all__ = [
    "ArtifactFileSpec",
    "AttemptSnapshotSpec",
    "BucketAOperator",
    "BucketAOperatorCall",
    "GuidedDecisionCard",
    "GuidedEntityCardSpec",
    "GuidedReadinessBrief",
    "GuidedRunView",
    "GuidedTableExplainerSpec",
    "GuidedWaiverTargetSpec",
    "HorizonActionReq",
    "HorizonCriticVerdict",
    "HorizonOptionSpec",
    "HorizonQuestion",
    "MigrationApplyResp",
    "SemanticCriticVerdict",
    "SqlQueryReq",
    "SqlQueryResult",
    "StorageCompileReq",
    "StorageTargetReq",
    "TargetBundleResp",
    "VaultBuildReq",
    "VaultVerifyResp",
    "WaiverSubmitReq",
]
