"""Layer-2a verification ports and decoupled DTOs for opendv.verifier.

Sole verifier module that opendv.storage is permitted to import.
Zero duckdb, sqlglot, opendv.sources, or opendv.storage imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from opendv.models.diagnostics import (
    AntiPatternJustification,
    HorizonQuestion,
    IRProverReport,
    RealDataRuleResult,
    StrictModel,
    VerifierDiagnostic,
)
from opendv.models.events import ExternalMcpReceipt, OcrPageReceipt
from opendv.models.modeling_preferences import ModelingPreferences, RemediationHint
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy
from opendv.models.project import EvolutionLockSpec, OntologyWaiverSpec
from opendv.models.sources import DocumentMap, PageTextView, SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.vault_ir_certified import CertifiedVaultIR


@runtime_checkable
class IRRuntimeProver(Protocol):
    """Protocol port implemented in opendv.storage (e.g. DuckDbProverAdapter) for Gate V6."""

    def prove(
        self,
        certified_ir: CertifiedVaultIR,
        profiles: Sequence[SourceProfile],
        *,
        policy: CegarPolicy = "balanced",
        source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        table_sample_flags: Mapping[str, bool] | None = None,
        real_row_proving: bool = True,
    ) -> IRProverReport:
        """Execute runtime dynamic invariants and AST checks on CertifiedVaultIR."""
        ...


class BucketAExecutionResult(StrictModel):
    """Result of executing BucketAOperatorCall overrides against DraftVaultIR."""

    draft_ir: DraftVaultIR
    applied_ops: list[str] = Field(default_factory=list)
    rerouted_entities: list[str] = Field(default_factory=list)
    bucket_b_escalations: list[HorizonQuestion] = Field(default_factory=list)
    requires_bucket_b: bool = False
    dedup_order_cols_by_sat: dict[str, list[str]] = Field(default_factory=dict)


class VerifierContext(StrictModel):
    """Typed context bundle supplied to V1-V6b verification gates."""

    profiles: list[SourceProfile] = Field(default_factory=list)
    ontology: GraphAOntology | None = None
    evolution_locks: EvolutionLockSpec | None = None
    prior_certified_ir: CertifiedVaultIR | None = None
    document_maps: list[DocumentMap] = Field(default_factory=list)
    page_text_views: list[PageTextView] = Field(default_factory=list)
    parsed_doc_pages: dict[str, dict[int, str]] = Field(default_factory=dict)
    ocr_receipts: list[OcrPageReceipt] = Field(default_factory=list)
    mcp_receipts: list[ExternalMcpReceipt] = Field(default_factory=list)
    ontology_waivers: list[OntologyWaiverSpec] = Field(default_factory=list)
    policy: CegarPolicy = "balanced"
    cold_start_requested: bool = False
    metadata_only_binary_absent: bool = False
    cdc_volatility_by_table: dict[str, dict[str, float]] = Field(default_factory=dict)
    fk_turnover_by_table: dict[str, dict[str, float]] = Field(default_factory=dict)
    entity_count_overrides: dict[str, int] = Field(default_factory=dict)
    source_rows_by_table: dict[str, list[dict[str, Any]]] | None = None
    table_sample_flags: dict[str, bool] | None = None
    real_row_proving: bool = True
    modeling_preferences: ModelingPreferences = Field(default_factory=ModelingPreferences)
    persisted_waivers: list[AntiPatternJustification] = Field(default_factory=list)


class VerifierReport(StrictModel):
    """Unified verification report across V1-V6 and V6b gates."""

    passed: bool
    v1_v6_diagnostics: list[VerifierDiagnostic] = Field(default_factory=list)
    v6b_diagnostics: list[VerifierDiagnostic] = Field(default_factory=list)
    all_diagnostics: list[VerifierDiagnostic] = Field(default_factory=list)
    v1_v6_fail_count: int = Field(default=0, ge=0)
    v6b_fail_count: int = Field(default=0, ge=0)
    waived_v6b_count: int = Field(default=0, ge=0)
    advisory_count: int = Field(default=0, ge=0)
    prover_report: IRProverReport | None = None
    energy_e: int = Field(default=0, ge=0)
    deterministic_energy: int = Field(default=0, ge=0)
    schema_proof: dict[str, bool] = Field(default_factory=dict)
    real_data_proof: dict[str, RealDataRuleResult] = Field(default_factory=dict)
    remediation_hints: list[RemediationHint] = Field(default_factory=list)


__all__ = [
    "BucketAExecutionResult",
    "IRRuntimeProver",
    "VerifierContext",
    "VerifierReport",
]
