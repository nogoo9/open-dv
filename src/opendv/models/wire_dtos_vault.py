"""Wire DTOs for Step 6 Vault Synthesis, Horizon Critic, Guided UX, and Step 7 Storage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from opendv.models.diagnostics import (
    AntiPatternJustification,
    ClarificationQuestion,
    HorizonOptionSpec,
    HorizonQuestion,
    StrictModel,
    VerifierDiagnostic,
)
from opendv.models.modeling_preferences import (
    ModelingPreferences,
    NextActionEnum,
    PkContainmentOverlap,
)
from opendv.models.policy import CegarPolicy
from opendv.models.project import DbtSqlDialect, TargetManifest
from opendv.models.sources import SourceProfile
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
    cdc_volatility_by_table: dict[str, dict[str, float]] | None = None
    fk_turnover_by_table: dict[str, dict[str, float]] | None = None
    real_row_proving: bool | None = None
    business_context_hints: str | None = Field(default=None, max_length=12_000)


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

    action: Literal["accept", "steer", "revert"]
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


class ProfileBundle(StrictModel):
    """Stateless Step 2/3 profile and prioritized clarification survey bundle."""

    profiles: list[SourceProfile] = Field(default_factory=list)
    pk_containment_overlaps: list[PkContainmentOverlap] = Field(default_factory=list)
    source_rows_by_table: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    table_sample_flags: dict[str, bool] = Field(default_factory=dict)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    total_candidates: int = Field(default=0, ge=0)
    returned_count: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)
    remaining_count: int = Field(default=0, ge=0)
    by_priority: dict[str, int] = Field(default_factory=dict)


class ModelSpecResp(StrictModel):
    """Annotated schema, constraint, guide, and preference spec from `opendv model spec`."""

    part: Literal["all", "schema", "guide", "preferences"] = "all"
    schemas: dict[str, Any] = Field(default_factory=dict)
    examples: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    guide_markdown: str | None = None
    guide_sha256: str | None = None
    preferences: ModelingPreferences | None = None
    preferences_schema: dict[str, Any] = Field(default_factory=dict)


class VaultStatusResp(StrictModel):
    """Project vault governance, run, open-hold, and waiver status summary."""

    project_id: str
    active_wave_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    next_action: NextActionEnum | None = None
    open_holds: list[ClarificationQuestion | HorizonQuestion] = Field(default_factory=list)
    waived_rules: list[AntiPatternJustification] = Field(default_factory=list)


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
    "ModelSpecResp",
    "ProfileBundle",
    "SemanticCriticVerdict",
    "SqlQueryReq",
    "SqlQueryResult",
    "StorageCompileReq",
    "StorageTargetReq",
    "TargetBundleResp",
    "VaultBuildReq",
    "VaultStatusResp",
    "VaultVerifyResp",
    "WaiverSubmitReq",
]
