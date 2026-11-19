"""Structured Stage 00-04 LLM / symbolic output schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Stage00OrchestrationPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_summary: str = ""
    inferred_action: str = "COLD_START_N1"
    active_stages: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    per_table_plan: dict[str, str] = Field(default_factory=dict)


class Stage01FeedDiscoveryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_system: str
    table_name: str
    column_count: int = 0
    row_count: int = 0
    primary_keys: list[str] = Field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = Field(default_factory=list)
    column_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    record_source_code: str = ""
    target_hub_name: str = ""
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    key_authority_tier: str = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)


class Stage01SourceDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    discovered_feeds: list[Stage01FeedDiscoveryItem] = Field(default_factory=list)
    profile_summaries: dict[str, Any] = Field(default_factory=dict)


class Stage02UnstructuredDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str = ""
    target_hub_name: str = ""
    link_name: str = ""
    satellite_name: str = ""
    extracted_candidates: list[Any] = Field(default_factory=list)
    discovered_entities: list[str] = Field(default_factory=list)
    spans_extracted: int = 0
    profiled_docs: list[dict[str, Any]] = Field(default_factory=list)


class Stage03ColumnTriageOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    column_triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    ontology_deltas_proposed: list[dict[str, Any]] = Field(default_factory=list)


class Stage04ContractSynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contract: dict[str, Any] = Field(default_factory=dict)
    evolved_contract: dict[str, Any] | None = None
    drift_findings: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "Stage00OrchestrationPlanOutput",
    "Stage01FeedDiscoveryItem",
    "Stage01SourceDiscoveryOutput",
    "Stage02UnstructuredDiscoveryOutput",
    "Stage03ColumnTriageOutput",
    "Stage04ContractSynthesisOutput",
]
