"""Pydantic metrics models and column role inference for Graph B DuckDB Profiler."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opendv_core.models.graph_b import CandidateRoleLiteral, ProfiledColumn


class ForeignKeyInclusionEdge(BaseModel):
    """Empirical Jaccard and asymmetric containment edge between child and parent columns."""

    model_config = ConfigDict(extra="allow")

    edge_id: str
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    asymmetric_inclusion_i_c_in_p: float = Field(ge=0.0, le=1.0)
    jaccard_similarity: float = Field(ge=0.0, le=1.0)
    jaccard_inclusion: float = Field(ge=0.0, le=1.0)
    null_fk_rate: float = Field(ge=0.0, le=1.0)
    orphan_value_count: int = Field(ge=0)
    verdict: str
    target_link: str
    zero_key_policy: str


class ColumnProfileMetrics(BaseModel):
    """Empirical Graph B metrics for a single relational/Parquet/CSV column."""

    model_config = ConfigDict(extra="allow")

    column_name: str
    physical_type: str
    row_count: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    distinct_non_null_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    uniqueness_ratio: float = Field(ge=0.0, le=1.0)
    non_null_ratio: float = Field(ge=0.0, le=1.0)
    u_total: float = Field(ge=0.0, le=1.0)
    u_non_null: float = Field(ge=0.0, le=1.0)
    null_rate: float = Field(ge=0.0, le=1.0)
    shannon_entropy_bits: float = Field(ge=0.0)
    shannon_entropy_norm: float = Field(ge=0.0, le=1.0)
    jaccard_inclusion: float = Field(default=0.0, ge=0.0, le=1.0)
    cdc_velocity_hz: float = Field(default=0.0, ge=0.0)
    write_amp_mb_per_day: float = Field(default=0.0, ge=0.0)
    update_frequency_lambda_per_day: float = Field(default=0.0, ge=0.0)
    avg_column_width_bytes: int = Field(default=8, ge=1)
    sample_values: list[Any] = Field(default_factory=list)
    bk_candidate_verdict: str | None = None
    recommended_dv2_role: str | None = None
    enum_demotion_verdict: str | None = None
    rationale: str | None = None

    def to_profiled_column(self) -> ProfiledColumn:
        """Convert into an `opendv_core.models.graph_b.ProfiledColumn` model."""
        role_map: dict[str, CandidateRoleLiteral] = {
            "HUB_BUSINESS_KEY": "TIER_1_BUSINESS_KEY",
            "LINK_FOREIGN_KEY": "FOREIGN_KEY_REF",
            "SYSTEM_METADATA": "TECHNICAL_METADATA",
            "SURROGATE_SEQUENCE_DROP": "SYSTEM_SURROGATE_DROPPED",
            "SATELLITE_DESCRIPTIVE_ATTRIBUTE": "DESCRIPTIVE_PAYLOAD",
        }
        cand_role: CandidateRoleLiteral = role_map.get(
            self.recommended_dv2_role or "",
            "DESCRIPTIVE_PAYLOAD",
        )
        return ProfiledColumn(
            column_name=self.column_name,
            data_type=self.physical_type,
            u_total=self.u_total,
            u_non_null=self.u_non_null,
            null_rate=self.null_rate,
            shannon_entropy_bits=self.shannon_entropy_bits,
            drift_rate_lambda=self.update_frequency_lambda_per_day,
            inclusion_in_parent=self.jaccard_inclusion if self.jaccard_inclusion > 0 else None,
            candidate_role=cand_role,
        )


def _infer_column_role(
    column_name: str,
    u_total: float,
    u_non_null: float,
    null_rate: float,
    shannon_entropy_norm: float,
    distinct_non_null_count: int,
    jaccard_inclusion: float,
) -> tuple[str, str, str | None]:
    """Infer a candidate DV 2.0 column role and BK verdict from empirical Graph B metrics."""
    lower_name = column_name.lower()
    if lower_name in {"load_dts", "rec_src", "record_source", "extract_dts"}:
        return ("REJECTED_SYSTEM_METADATA", "SYSTEM_METADATA", None)
    if u_total >= 0.99 and null_rate == 0.0 and shannon_entropy_norm >= 0.85:
        if lower_name.endswith("_seq_id") or lower_name == "row_id":
            return ("REJECTED_SURROGATE_SEQUENCE", "SURROGATE_SEQUENCE_DROP", None)
        return ("PROMOTED_HUB_BK", "HUB_BUSINESS_KEY", None)
    if jaccard_inclusion >= 0.85 or lower_name.endswith(("_id", "_code", "_lei")):
        if u_non_null >= 0.95 and null_rate == 0.0:
            return ("PROMOTED_HUB_BK", "HUB_BUSINESS_KEY", None)
        return ("REJECTED_FK_DEPENDENCY", "LINK_FOREIGN_KEY", None)
    if distinct_non_null_count <= 12 and shannon_entropy_norm < 0.55:
        return (
            "REJECTED_LOW_CARDINALITY_ENUM",
            "SATELLITE_DESCRIPTIVE_ATTRIBUTE",
            "DEMOTE_TO_SATELLITE_OR_REF",
        )
    return ("REJECTED_DESCRIPTIVE_ATTRIBUTE", "SATELLITE_DESCRIPTIVE_ATTRIBUTE", None)


__all__ = [
    "ColumnProfileMetrics",
    "ForeignKeyInclusionEdge",
    "_infer_column_role",
]
