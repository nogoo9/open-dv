"""Project state, workspace settings, revision payloads, bundles, and target manifests."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from opendv.models.diagnostics import (
    DocumentCitationRef,
    ExternalMcpEvidenceRef,
    StrictModel,
)
from opendv.models.ontology import ConceptTableBinding
from opendv.models.ontology_deltas import OntologyDelta
from opendv.models.policy import CegarPolicy, ModelTier
from opendv.models.sources import ConnectorConfigPayload, SourceSummary

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DOMAIN_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

HarnessProvider = Literal["agy", "claude-cli", "claude", "gemini"]
DbtSqlDialect = Literal[
    "duckdb",
    "databricks",
    "snowflake",
    "postgres",
    "bigquery",
    "tsql",
    "redshift",
    "clickhouse",
]
SecretBackend = Literal["keyring", "user_state_file"]
SecretPrecedence = Literal["store_first", "env_first"]
DiscoveryMode = Literal["incremental_new", "incremental_reverify", "full_rebuild"]


def validate_project_id(value: str) -> str:
    """Validate project_id regex and reject 'import' and Windows reserved device names."""
    if not PROJECT_ID_RE.fullmatch(value):
        raise ValueError(
            "project_id must match ^[a-z0-9][a-z0-9_-]{0,63}$"
        )
    lowered = value.lower()
    if lowered == "import" or lowered in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"project_id {value!r} is reserved and forbidden")
    return value


def validate_domain_namespace(value: str) -> str:
    """Validate domain_namespace against ^[a-z][a-z0-9_]{0,63}$."""
    if not DOMAIN_NAMESPACE_RE.fullmatch(value):
        raise ValueError("domain_namespace must match ^[a-z][a-z0-9_]{0,63}$")
    return value


class TrustedWorkspaceSpec(StrictModel):
    """Approved workspace override pack digest recorded in OS user-state settings."""

    workspace_root: str
    agent_pack_sha256: str
    trusted_at: str


class WorkspaceSettings(StrictModel):
    """Global non-secret settings stored under <OS_USER_STATE>/opendv/settings.json."""

    schema_version: Literal["2.0"] = "2.0"
    secret_backend: SecretBackend = "keyring"
    secret_precedence: SecretPrecedence = "store_first"
    harness_provider: HarnessProvider = "agy"
    model_overrides: dict[str, str] = Field(default_factory=dict)
    v7_critic_tier: ModelTier = "FRONTIER_CRITIC"
    cegar_policy: CegarPolicy = "balanced"
    dbt_sql_dialect: DbtSqlDialect = "duckdb"
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    connection_profiles: dict[str, ConnectorConfigPayload] = Field(default_factory=dict)
    trusted_workspaces: list[TrustedWorkspaceSpec] = Field(default_factory=list)
    updated_at: str = ""

    @model_validator(mode="after")
    def _validate_strict_tier_lock(self) -> WorkspaceSettings:
        if self.cegar_policy == "strict" and self.v7_critic_tier != "FRONTIER_CRITIC":
            raise ValueError("v7_critic_tier must be FRONTIER_CRITIC when cegar_policy is strict")
        return self


class ProjectSettingsSnapshot(StrictModel):
    """Per-project settings snapshot persisted in projects.settings_snapshot_json."""

    harness_provider: HarnessProvider = "agy"
    v7_critic_tier: ModelTier = "FRONTIER_CRITIC"
    cegar_policy: CegarPolicy = "balanced"
    dbt_sql_dialect: DbtSqlDialect = "duckdb"
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    secret_precedence: SecretPrecedence = "store_first"
    discovery_mode: DiscoveryMode = "incremental_new"

    @model_validator(mode="after")
    def _validate_strict_tier_lock(self) -> ProjectSettingsSnapshot:
        if self.cegar_policy == "strict" and self.v7_critic_tier != "FRONTIER_CRITIC":
            raise ValueError("v7_critic_tier must be FRONTIER_CRITIC when cegar_policy is strict")
        return self


class ProjectState(StrictModel):
    """Full project state projection returned by GET /projects/{id}."""

    project_id: str
    domain_namespace: str
    active_ontology_rev_id: str | None = None
    active_wave_id: str | None = None
    settings_snapshot: ProjectSettingsSnapshot
    source_count: int = Field(default=0, ge=0)
    table_count: int = Field(default=0, ge=0)
    has_active_lease: bool = False
    created_at: str
    updated_at: str

    @field_validator("project_id")
    @classmethod
    def _check_pid(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("domain_namespace")
    @classmethod
    def _check_ns(cls, v: str) -> str:
        return validate_domain_namespace(v)


class ProjectSummary(StrictModel):
    """Compact project listing row returned by GET /projects."""

    project_id: str
    domain_namespace: str
    active_ontology_rev_id: str | None = None
    active_wave_id: str | None = None
    updated_at: str

    @field_validator("project_id")
    @classmethod
    def _check_pid(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("domain_namespace")
    @classmethod
    def _check_ns(cls, v: str) -> str:
        return validate_domain_namespace(v)


class RevisionMetadata(StrictModel):
    """Metadata envelope persisted alongside revisions.payload_json."""

    actor: Literal["human", "agent"]
    reason: str
    base_revision_id: str | None = None
    is_waiver_signed: bool = False
    waiver_rationale: str | None = None


class EvolutionLockSpec(StrictModel):
    """Sealed wave lock preventing destructive changes to deployed Hub BKs/Links/Sats."""

    wave_number: int = Field(ge=1)
    locked_hub_bks: dict[str, list[str]] = Field(default_factory=dict)
    locked_links: dict[str, list[str]] = Field(default_factory=dict)
    locked_source_columns: dict[str, list[str]] = Field(default_factory=dict)
    locked_sat_hash_diffs: dict[str, list[str]] = Field(default_factory=dict)
    physical_aliases: dict[str, str] = Field(default_factory=dict)
    iceberg_high_water_marks: dict[str, int] = Field(default_factory=dict)
    sat_epochs: dict[str, int] = Field(default_factory=dict)


class HorizonAssumptionSpec(StrictModel):
    """Persisted Steward acceptance/steering record for a Horizon BRK assumption."""

    assumption_id: str
    brk_code: str
    title: str
    notes: str
    steward_decision: Literal["ACCEPT", "STEER"]
    selected_option_ids: list[str] = Field(default_factory=list)
    added_fields: list[str] = Field(default_factory=list)


class ConceptMemorySpec(StrictModel):
    """User-pinned or muted term preference stored in revisions."""

    term: str
    state: Literal["PINNED", "MUTED", "UNSET"]
    concept_uri: str | None = None


class StagedCandidateSpec(StrictModel):
    """Quarantined, waived, or promoted discovery candidate stored in revisions."""

    candidate_id: str
    concept_name: str
    concept_uri: str
    grounding_path: Literal["PATH_A1", "PATH_A2", "PATH_B1", "PATH_B2"]
    bm25_score: str | None = None
    table_bindings: list[ConceptTableBinding] = Field(default_factory=list)
    source_citations: list[DocumentCitationRef] = Field(default_factory=list)
    mcp_evidence: list[ExternalMcpEvidenceRef] = Field(default_factory=list)
    status: Literal["QUARANTINED", "WAIVED", "PROMOTED"]
    rationale: str | None = None


class OntologyProposalSpec(StrictModel):
    """Uncommitted Step 6 proposal surfacing additive ontology updates in Step 5."""

    proposal_id: str
    wave_number: int = Field(ge=1)
    delta_ops: list[OntologyDelta]
    summary: str
    status: Literal["PENDING_STEWARD", "DISMISSED", "COMMITTED"]


class OntologyWaiverSpec(StrictModel):
    """Steward waiver record for waivable ONT-AP-001..005 rules."""

    waiver_id: str = ""
    rule_id: str
    concept_uri: str
    rationale: str
    created_at: str = ""


class BundleEntryDigest(StrictModel):
    """SHA-256 and uncompressed byte size for a single .opendv.zip member."""

    sha256: str
    uncompressed_bytes: int = Field(ge=0)


class BundleManifest(StrictModel):
    """Top-level manifest.json inside a portable .opendv.zip archive."""

    bundle_version: Literal["2.0"] = "2.0"
    mode: Literal["core_project_state", "full_demo"]
    project_id: str
    created_at: str
    source_os: Literal["posix", "nt"]
    entries: dict[str, BundleEntryDigest]

    @field_validator("project_id")
    @classmethod
    def _check_pid(cls, v: str) -> str:
        return validate_project_id(v)


class BundleProjectSnapshot(StrictModel):
    """Portable project.json snapshot inside an .opendv.zip archive."""

    project_id: str
    domain_namespace: str
    active_ontology_rev_id: str | None = None
    active_wave_id: str | None = None
    settings_snapshot: ProjectSettingsSnapshot
    sources: list[SourceSummary]
    revision_keys: list[str]

    @field_validator("project_id")
    @classmethod
    def _check_pid(cls, v: str) -> str:
        return validate_project_id(v)

    @field_validator("domain_namespace")
    @classmethod
    def _check_ns(cls, v: str) -> str:
        return validate_domain_namespace(v)


class ImportReadinessReport(StrictModel):
    """Post-import readiness and missing secret/binary report."""

    project_id: str
    resolved_conflict: Literal["none", "renamed", "replaced"]
    readiness_flags: list[
        Literal["READY_OFFLINE", "NEEDS_LOCAL_SECRET_BINDING", "METADATA_ONLY_BINARY_ABSENT"]
    ]
    missing_secret_refs: list[str]
    missing_source_ids: list[str]
    unverified_receipt_ids: list[str]
    audit_claim_notice: str


class TargetManifest(StrictModel):
    """Step 7 per-target compilation and materialization manifest."""

    target_id: str
    last_compiled_wave: int = Field(ge=1)
    last_materialized_wave: int | None = None
    verification_tier: Literal["RUNTIME_VERIFIED", "SYNTAX_AND_CONTRACT_CHECKED"]
    certification_status: Literal["CERTIFIED", "CERTIFIED_FAST"]
    physical_aliases: dict[str, str] = Field(default_factory=dict)
    eval_receipts: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)


__all__ = [
    "DOMAIN_NAMESPACE_RE",
    "PROJECT_ID_RE",
    "WINDOWS_RESERVED_NAMES",
    "BundleEntryDigest",
    "BundleManifest",
    "BundleProjectSnapshot",
    "ConceptMemorySpec",
    "DbtSqlDialect",
    "DiscoveryMode",
    "EvolutionLockSpec",
    "HarnessProvider",
    "HorizonAssumptionSpec",
    "ImportReadinessReport",
    "OntologyProposalSpec",
    "OntologyWaiverSpec",
    "ProjectSettingsSnapshot",
    "ProjectState",
    "ProjectSummary",
    "RevisionMetadata",
    "SecretBackend",
    "SecretPrecedence",
    "StagedCandidateSpec",
    "TargetManifest",
    "TrustedWorkspaceSpec",
    "WorkspaceSettings",
    "validate_domain_namespace",
    "validate_project_id",
]
