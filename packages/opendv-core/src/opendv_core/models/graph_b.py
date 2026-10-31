"""Pydantic v2 models for Statistical Topology & ELM Profile Graph B (`pe_graph_b_profile.json`).

Validates source system Bronze table profiles, column information-theoretic metrics,
YAGNI write amplification telemetry, BKCC homonym collision diagnostics, and
span-grounded unstructured document extractions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type CandidateRoleLiteral = Literal[
    "TIER_1_BUSINESS_KEY",
    "TIER_2_BUSINESS_KEY",
    "TIER_3_LOCAL_SCOPED_KEY",
    "DESCRIPTIVE_PAYLOAD",
    "REFERENCE_CODE",
    "SOFT_DEPRECATED_PAYLOAD",
    "ADDITIVE_DRIFT_COMPANION",
    "SYSTEM_SURROGATE_DROPPED",
    "PII_RESTRICTED_PAYLOAD",
    "MNPI_VALUATION_PAYLOAD",
    "DEGENERATE_KEY",
    "FOREIGN_KEY_REF",
    "OPTIONAL_FOREIGN_KEY_REF",
    "TECHNICAL_METADATA",
]

type ERTierLiteral = Literal[
    "TIER_U1_DETERMINISTIC",
    "TIER_U2_HYBRID_EXACT",
    "TIER_U3_CAPTAIN_HOLD",
    "EXTRACTED_ATTRIBUTE",
]


class StrictGraphBModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class YagniTelemetry(StrictGraphBModel):
    """Gate 4 YAGNI write amplification telemetry for a Bronze staging table."""

    general_column_count: int = Field(ge=0)
    high_freq_column_count: int = Field(ge=0)
    high_freq_row_width_bytes: int = Field(ge=0)
    high_freq_change_probability: float = Field(ge=0.0, le=1.0)
    write_amplification_mb_per_day: float = Field(ge=0.0)
    threshold_mb_per_day: float = Field(ge=0.0)
    gate4_verdict: str


class CollisionDiagnostics(StrictGraphBModel):
    """Cross-source business key homonym collision diagnostic record."""

    colliding_column: str
    colliding_value: str
    legacy_entity_name: str
    investran_entity_name: str | None = None
    primary_entity_name: str | None = None
    jaccard_value_overlap: float = Field(ge=0.0, le=1.0)
    semantic_entity_overlap: float = Field(ge=0.0, le=1.0)
    required_bkcc_prefix: str = Field(pattern=r"^[A-Z0-9_]+$")


class ProfiledColumn(StrictGraphBModel):
    """Information-theoretic, temporal, and relational inclusion metrics for a column."""

    column_name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    u_total: float = Field(ge=0.0, le=1.0)
    u_non_null: float = Field(ge=0.0, le=1.0)
    null_rate: float = Field(ge=0.0, le=1.0)
    shannon_entropy_bits: float | None = Field(default=None, ge=0.0)
    drift_rate_lambda: float | None = Field(default=None, ge=0.0)
    monotonic_step_delta_1: bool | None = None
    security_tag: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "PII_HIGH", "PCI"] | None = None
    inclusion_in_parent: float | None = Field(default=None, ge=0.0, le=1.0)
    target_parent: str | None = None
    candidate_role: CandidateRoleLiteral


class ExtractedSpan(StrictGraphBModel):
    """Character-offset grounded span extracted from an unstructured document."""

    field: str = Field(min_length=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    verbatim_quote: str = Field(min_length=1)
    resolved_bk: str | None = None
    er_tier: ERTierLiteral
    confidence: float = Field(ge=0.0, le=1.0)
    top2_margin: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_span_bounds(self) -> ExtractedSpan:
        if self.span_end <= self.span_start:
            msg = (
                f"ExtractedSpan '{self.field}' must have span_end ({self.span_end}) > "
                f"span_start ({self.span_start})"
            )
            raise ValueError(msg)
        return self


class RowChunkSpan(StrictGraphBModel):
    """Bounding character offset span `[span_start, span_end]` for a document chunk."""

    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_chunk_bounds(self) -> RowChunkSpan:
        if self.span_end <= self.span_start:
            msg = (
                f"RowChunkSpan must have span_end ({self.span_end}) > "
                f"span_start ({self.span_start})"
            )
            raise ValueError(msg)
        return self


class ProfiledUnstructuredDocument(StrictGraphBModel):
    """Unstructured document profile with OCR/LLM extracted spans and checksum."""

    doc_uri: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    char_length: int = Field(ge=1)
    extracted_spans: list[ExtractedSpan] = Field(min_length=1)
    row_chunk_span: RowChunkSpan


class ProfiledBronzeTable(StrictGraphBModel):
    """Statistical profile for a single Bronze source table."""

    bronze_table: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    daily_delta_rows: int = Field(ge=0)
    yagni_telemetry: YagniTelemetry | None = None
    collision_diagnostics: CollisionDiagnostics | None = None
    columns: list[ProfiledColumn] = Field(min_length=1)


class ProfiledSourceSystem(StrictGraphBModel):
    """Statistical profile for a source system and its Bronze tables / documents."""

    source_system_code: str = Field(pattern=r"^[A-Z0-9_]+$")
    description: str = Field(min_length=2)
    tables: list[ProfiledBronzeTable] = Field(min_length=1)
    unstructured_documents: list[ProfiledUnstructuredDocument] | None = None


class GraphBProfile(StrictGraphBModel):
    """Root Statistical Topology & ELM Profile Graph B model (`pe_graph_b_profile.json`)."""

    profile_version: str = "1.0.0"
    domain_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    profiled_at_utc: datetime
    source_systems: list[ProfiledSourceSystem] = Field(min_length=1)


StatisticalGraphB = GraphBProfile

__all__ = [
    "CandidateRoleLiteral",
    "CollisionDiagnostics",
    "ERTierLiteral",
    "ExtractedSpan",
    "GraphBProfile",
    "ProfiledBronzeTable",
    "ProfiledColumn",
    "ProfiledSourceSystem",
    "ProfiledUnstructuredDocument",
    "RowChunkSpan",
    "StatisticalGraphB",
    "YagniTelemetry",
]
