"""Layer-1 modeling preferences, remediation hints, fragility summary, and cache DTOs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import Field

from opendv.models.diagnostics import (
    AntiPatternJustification,
    GateId,
    RealDataCheckStatus,
    RealDataRuleResult,
    StrictModel,
)

SURROGATE_BK_RE_PATTERN: Final[str] = (
    r"(?i)^(?:id|pk|seq_id|row_id|uuid|surrogate_id|auto_id|.*_sk|.*_surrogate)$"
)
_SURROGATE_BK_RE: Final[re.Pattern[str]] = re.compile(SURROGATE_BK_RE_PATTERN)
SURROGATE_BK_RE: Final[re.Pattern[str]] = _SURROGATE_BK_RE

ColumnFormatShape = Literal[
    "ISO_DATE",
    "ISO_TIMESTAMP",
    "UUID",
    "EMAIL",
    "INT_SEQ",
    "DECIMAL",
    "CODE_UPPER",
    "FREE_TEXT",
    "MIXED",
]

NextActionEnum = Literal[
    "READY_FOR_DOWNSTREAM",
    "ASK_HUMAN_HOLD",
    "REPAIR_DRAFT_IR",
    "WAIVE_OR_ADJUST_PREFERENCES",
    "FAILED",
]

ModelingStatus = Literal[
    "CERTIFIED",
    "CERTIFIED_FAST",
    "HOLD_CHECKPOINTED",
    "UNCERTIFIED_DRAFT",
    "FAILED",
]


class ModelingPreferences(StrictModel):
    """Tier-1 deterministic DV2.0 modeling preferences governing L1 codemods and V6b."""

    multi_source_hub_strategy: Literal[
        "unified_canonical_when_same_arity",
        "companion_hub_with_sal",
    ] = Field(
        default="unified_canonical_when_same_arity",
        description="Merge compatible multi-source Hubs or keep separate companion Hubs via SAL.",
    )
    low_cardinality_strategy: Literal["auto_ref_table", "keep_in_satellite"] = Field(
        default="auto_ref_table",
        description="Promote low-cardinality columns to Reference Tables or keep in Satellites.",
    )
    link_cardinality_strategy: Literal[
        "prefer_standard_or_mas",
        "allow_transactional_links",
    ] = Field(
        default="prefer_standard_or_mas",
        description="Prefer standard/MAS links or permit non-historized transactional links.",
    )
    esat_on_mutable_links: Literal[
        "auto_when_turnover_known",
        "always_on_driving_hub",
        "off",
    ] = Field(
        default="auto_when_turnover_known",
        description="When to attach Effectivity Satellites to mutable relationship Links.",
    )
    surrogate_key_strategy: Literal[
        "reject_when_natural_exists",
        "allow_with_bkcc_note",
    ] = Field(
        default="reject_when_natural_exists",
        description="Reject surrogate Hub BKs when a natural key candidate exists, or allow.",
    )
    disabled_v6b_blocks: list[str] = Field(
        default_factory=list,
        description="Valid V6b rule IDs downgraded from blocking under non-strict policies.",
    )
    clarify_mode: Literal["auto", "ask_on_ambiguity"] = Field(
        default="auto",
        description="Auto-resolve ambiguities or pause for human Hold clarification.",
    )
    max_survey_questions: int | None = Field(
        default=5,
        ge=1,
        description="Maximum clarification survey questions returned per discovery batch.",
    )


class RemediationHint(StrictModel):
    """Actionable remediation guidance for a failed verification or prover check."""

    gate: GateId = Field(description="Gate identifier that emitted this remediation hint.")
    rule_id: str = Field(description="Rule identifier (e.g. DV-DYN-004, DV-AP-003).")
    entity_name: str = Field(description="Target Hub, Link, Satellite, or Mart entity name.")
    suggested_fix: str = Field(description="Concrete declarative fix or waiver action.")
    waivable: bool = Field(
        default=False,
        description="Whether this finding may be resolved via an audited V6b waiver.",
    )


class FragilitySurfaceSummary(StrictModel):
    """Zero-token deterministic summary of immutable wave-1 commitments for V8."""

    hub_bks: list[str] = Field(default_factory=list)
    link_arities: list[str] = Field(default_factory=list)
    single_active_sats: list[str] = Field(default_factory=list)
    narrow_numeric_cols: list[str] = Field(default_factory=list)
    bitemporal_gaps: list[str] = Field(default_factory=list)
    candidate_alt_bks: list[str] = Field(default_factory=list)


class ValueFreqItem(StrictModel):
    """PII-redacted value frequency bucket for a low-cardinality profiled column."""

    value: str = Field(description="Observed or PII-redacted scalar representation.")
    count: int = Field(ge=0, description="Observed occurrence count in sampled rows.")


class PkContainmentOverlap(StrictModel):
    """Asymmetric FK-to-PK containment overlap ratio computed over bounded local hashes."""

    left_source_id: str = Field(description="Source ID of the candidate FK column.")
    left_table_id: str = Field(description="Table ID of the candidate FK column.")
    left_col: str = Field(description="Candidate FK column name.")
    right_source_id: str = Field(description="Source ID of the target PK/BK column.")
    right_table_id: str = Field(description="Table ID of the target PK/BK column.")
    right_col: str = Field(description="Target PK or non-surrogate BK column name.")
    overlap_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of distinct left hashes contained in right hashes.",
    )
    compared_hashes: int = Field(
        ge=1,
        description="Number of distinct non-null left hashes compared.",
    )


class CachedTableSample(StrictModel):
    """PII-tokenized sample rows and completeness metadata cached for a single table."""

    table_id: str = Field(description="Canonical table identifier.")
    schema_fingerprint: str = Field(description="Canonical JCS SHA-256 table schema digest.")
    salt_digest: str = Field(description="16-hex SHA-256 prefix of the HMAC tokenization salt.")
    rows: list[dict[str, Any]] = Field(description="PII-tokenized sample row dictionaries.")
    row_count_est: int | None = Field(
        default=None,
        description="Estimated total row count in the physical source table.",
    )
    is_complete_table: bool = Field(
        description="True when cached rows represent the complete physical table.",
    )
    cached_sample_rows_limit: int = Field(
        ge=1,
        le=5000,
        description="Configured proving sample limit active when cached.",
    )


class ProvingSampleCacheFile(StrictModel):
    """Per-source proving sample sidecar JSON file stored outside .opendv.zip bundles."""

    source_id: str = Field(description="Source identifier.")
    project_id: str = Field(description="Owning project identifier.")
    created_at_utc: str = Field(description="UTC ISO-8601 creation timestamp.")
    salt_key_id: str = Field(description="Keyring/ephemeral identifier for the tokenization salt.")
    salt_digest: str = Field(description="16-hex SHA-256 prefix of the tokenization salt.")
    cached_sample_rows_limit: int = Field(
        ge=1,
        le=5000,
        description="Sample row ceiling used to build this cache.",
    )
    tables: dict[str, CachedTableSample] = Field(
        default_factory=dict,
        description="Cached table samples keyed by table_id.",
    )


class TableCluster(StrictModel):
    """Deterministic cluster of related source tables for shard and discovery grouping."""

    cluster_id: str = Field(description="Stable cluster identifier.")
    table_ids: list[str] = Field(
        default_factory=list,
        description="Member table_id values assigned to this cluster.",
    )


@dataclass(frozen=True)
class TableClusterResult:
    """Cluster assignments for a catalog of TableProfile objects."""

    clusters: list[list[str]]
    table_to_cluster: dict[str, str]
    shard_groups: dict[str, str]


__all__ = [
    "SURROGATE_BK_RE",
    "SURROGATE_BK_RE_PATTERN",
    "AntiPatternJustification",
    "CachedTableSample",
    "ColumnFormatShape",
    "FragilitySurfaceSummary",
    "ModelingPreferences",
    "ModelingStatus",
    "NextActionEnum",
    "PkContainmentOverlap",
    "ProvingSampleCacheFile",
    "RealDataCheckStatus",
    "RealDataRuleResult",
    "RemediationHint",
    "TableCluster",
    "TableClusterResult",
    "ValueFreqItem",
]
