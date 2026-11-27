"""Public Layer-1 DTOs for column grounding, table anchors, and FIBO sync."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from opendv.models.diagnostics import StrictModel
from opendv.models.sources import ColumnShapeStats
from opendv.models.vault_ir import PortableLogicalType

InferredLogicalType = PortableLogicalType | Literal["unknown"]

ArchetypeKind = Literal[
    "identifier",
    "code_classifier",
    "temporal",
    "quantity_measure",
    "value_object",
    "party_role",
    "event_occurrence",
    "agreement",
    "entity",
]

GroundingExecutionMode = Literal[
    "symbolic_fast_path",
    "agentic_reranked",
    "symbolic_only",
    "deterministic_only",
]

SelectionStatus = Literal["MATCHED", "AMBIGUOUS", "NO_MATCH"]

ReviewReason = Literal[
    "competing_candidates",
    "wide_confidence_interval",
    "elevated_null_probability",
    "unverified_masked_values",
    "close_match_nuance",
]

NoMatchCategory = Literal[
    "out_of_vocabulary_domain",
    "technical_surrogate",
    "system_audit_metadata",
    "insufficient_evidence",
    "below_threshold",
]

CandidatePredicate = Literal["skos:exactMatch", "skos:closeMatch", "skos:relatedMatch"]

EntryKind = Literal[
    "class",
    "datatype_property",
    "object_property",
    "individual_lifted",
    "glossary_term",
    "enumeration",
    "other",
]

_QUANT_FOUR_PLACES = Decimal("0.0001")
_MAX_NOTE_CHARS = 4000


class ColumnEvidence(StrictModel):
    """Input physical/metadata evidence for a single table column."""

    column_name: str
    data_type: str
    sample_values: list[Any] = Field(default_factory=list, max_length=200)
    steward_note: str | None = Field(default=None, max_length=_MAX_NOTE_CHARS)
    nullable: bool = True
    is_pk: bool = False
    pk_ord: int | None = None
    fk_ref: str | None = None
    null_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    distinct_count_est: int | None = Field(default=None, ge=0)
    pii_tags: list[str] = Field(default_factory=list)
    precomputed_shape: ColumnShapeStats | None = None


class TableEvidence(StrictModel):
    """Input evidence bundle for a physical or logical table."""

    table_name: str
    columns: list[ColumnEvidence]
    table_id: str | None = None
    steward_notes: list[str] = Field(default_factory=list, max_length=20)
    row_count_est: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_steward_notes_length(self) -> TableEvidence:
        for idx, note in enumerate(self.steward_notes):
            if len(note) > _MAX_NOTE_CHARS:
                raise ValueError(
                    f"steward_notes[{idx}] exceeds {_MAX_NOTE_CHARS} chars ({len(note)})"
                )
        return self


class GroundingPolicy(StrictModel):
    """Execution policy controlling agentic reranking, calibration CI, and selection."""

    mode: Literal["agentic", "deterministic_only"] = "agentic"
    skip_reason: str | None = None
    max_choices: int = Field(default=3, ge=1)
    cumulative_mass_target: float = Field(default=0.90, gt=0.0, le=1.0)
    ci_level: float = Field(default=0.90, gt=0.0, lt=1.0)
    no_match_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    enable_table_context: bool = True

    @model_validator(mode="after")
    def _validate_skip_reason(self) -> GroundingPolicy:
        has_skip = self.skip_reason is not None
        non_blank = self.skip_reason is not None and bool(self.skip_reason.strip())
        if self.mode == "deterministic_only" and not non_blank:
            raise ValueError("skip_reason must be non-blank when mode == 'deterministic_only'")
        if self.mode == "agentic" and has_skip:
            raise ValueError("skip_reason must be None when mode == 'agentic'")
        return self


class GroundingRequest(StrictModel):
    """Bounded batch request for synchronous/orchestrated column grounding."""

    tables: list[TableEvidence]
    global_steward_notes: list[str] = Field(default_factory=list, max_length=20)
    policy: GroundingPolicy = Field(default_factory=GroundingPolicy)
    max_tables: int = Field(default=50, ge=1, le=50)
    max_columns: int = Field(default=500, ge=1, le=500)

    @model_validator(mode="after")
    def _validate_bounds_and_keys(self) -> GroundingRequest:
        for idx, note in enumerate(self.global_steward_notes):
            if len(note) > _MAX_NOTE_CHARS:
                raise ValueError(
                    f"global_steward_notes[{idx}] exceeds {_MAX_NOTE_CHARS} chars ({len(note)})"
                )
        if len(self.tables) > self.max_tables:
            raise ValueError(
                f"tables count ({len(self.tables)}) exceeds max_tables ({self.max_tables})"
            )
        total_cols = sum(len(t.columns) for t in self.tables)
        if total_cols > self.max_columns:
            raise ValueError(
                f"total column count ({total_cols}) exceeds max_columns ({self.max_columns})"
            )
        seen_keys: set[str] = set()
        for table in self.tables:
            key = table.table_id or table.table_name
            if key in seen_keys:
                raise ValueError(f"Duplicate table key '{key}' in GroundingRequest")
            seen_keys.add(key)
        return self


class OntologyGroundReq(StrictModel):
    """Top-level API/CLI request for standalone or project-backed ontology grounding."""

    request: GroundingRequest | None = None
    project_id: str | None = None
    knowledge_source: Literal["fibo", "glossary"] = "fibo"
    fibo_pack: str | None = None
    glossary_path: str | None = None
    policy: GroundingPolicy | None = None

    @model_validator(mode="after")
    def _validate_request_and_source(self) -> OntologyGroundReq:
        if (self.request is not None) == (self.project_id is not None):
            raise ValueError("Exactly one of request or project_id must be provided")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("project_id must be non-blank when provided")
        has_glossary = self.glossary_path is not None
        non_blank_glossary = self.glossary_path is not None and bool(self.glossary_path.strip())
        if self.knowledge_source == "glossary" and not non_blank_glossary:
            raise ValueError("glossary_path must be non-blank when knowledge_source == 'glossary'")
        if self.knowledge_source == "fibo" and has_glossary:
            raise ValueError("glossary_path must be None when knowledge_source == 'fibo'")
        if self.request is not None and self.policy is not None:
            if "policy" in self.request.model_fields_set and self.policy != self.request.policy:
                raise ValueError(
                    "Conflicting policy specified on OntologyGroundReq and request.policy"
                )
            self.request = self.request.model_copy(update={"policy": self.policy})
        return self


class InferredValueProfile(StrictModel):
    """Inferred logical type, counts, PII flags, and shape stats for a column."""

    observed_sample_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    masked_count: int = Field(ge=0)
    declared_logical_type: PortableLogicalType
    inferred_logical_type: InferredLogicalType
    type_confidence: float = Field(ge=0.0, le=1.0)
    pii_tags: list[str] = Field(default_factory=list)
    shape: ColumnShapeStats

    @model_validator(mode="after")
    def _validate_pii_shape_consistency(self) -> InferredValueProfile:
        if self.pii_tags and (not self.shape.pii_suppressed or self.shape.observed_enum_values):
            raise ValueError(
                "Non-empty pii_tags requires shape.pii_suppressed=True "
                "and empty shape.observed_enum_values"
            )
        return self


class EvidenceContribution(StrictModel):
    """Per-modality evidence contribution for candidate explainability."""

    channel: str
    score: float
    weight: float
    detail: str


class ConfidenceInterval(StrictModel):
    """Calibrated point probability and Logit-Normal confidence interval."""

    point: float
    lower: float
    upper: float
    level: float
    calibration_mode: Literal["analytic_logit_normal", "empirical_calibrated"]

    @model_validator(mode="after")
    def _validate_interval_bounds(self) -> ConfidenceInterval:
        vals = (self.point, self.lower, self.upper, self.level)
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("ConfidenceInterval values must be finite")
        if not (0.0 <= self.lower <= self.point <= self.upper <= 1.0):
            raise ValueError(
                f"Expected 0.0 <= lower <= point <= upper <= 1.0, "
                f"got ({self.lower}, {self.point}, {self.upper})"
            )
        if not (0.0 < self.level < 1.0):
            raise ValueError(f"Expected 0.0 < level < 1.0, got {self.level}")
        return self


class SemanticContext(StrictModel):
    """Semantic role, domain/target entity, identifier scope, and PII hints."""

    archetype: ArchetypeKind | None = None
    identified_entity_uri: str | None = None
    domain_entity_uri: str | None = None
    target_entity_uri: str | None = None
    role_uri: str | None = None
    played_by_uri: str | None = None
    identifier_scope: Literal["global_standard", "enterprise_sor", "local_source"] | None = None
    standard_authority: str | None = None
    pii_category_hint: str | None = None


class CandidateMapping(StrictModel):
    """Ranked candidate mapping emitted for a grounded column."""

    rank: int = Field(ge=1)
    entry_id: str
    label: str
    entry_kind: EntryKind
    origin_kind: Literal["project_graph_a", "reference_standard"]
    confidence: ConfidenceInterval
    suggested_predicate: CandidatePredicate
    semantic_context: SemanticContext
    replaces_uris: list[str] = Field(default_factory=list)
    evidence_contributions: list[EvidenceContribution] = Field(default_factory=list)

    def to_quantized_confidence(self) -> Decimal:
        """Quantize confidence.point to 4 decimal places via ROUND_HALF_UP."""
        return Decimal(str(self.confidence.point)).quantize(
            _QUANT_FOUR_PLACES,
            rounding=ROUND_HALF_UP,
        )


class ColumnGroundingResult(StrictModel):
    """Final grounding outcome and diagnostics for a single column."""

    table_name: str
    column_name: str
    status: SelectionStatus
    review_reason: ReviewReason | None = None
    choices: list[CandidateMapping] = Field(default_factory=list)
    no_match_probability: float = Field(ge=0.0, le=1.0)
    no_match_category: NoMatchCategory | None = None
    no_match_detail: str | None = None
    suggested_broad_parent_uri: str | None = None
    best_rejected_candidate: CandidateMapping | None = None
    steward_summary: str
    inferred_value_profile: InferredValueProfile
    execution_mode_used: GroundingExecutionMode
    selection_rule_applied: str
    grounding_receipt_id: str | None = None


class TableAnchorCandidate(StrictModel):
    """Inferred table-level anchor concept hypothesis."""

    entry_id: str
    label: str
    archetype: ArchetypeKind
    origin_kind: Literal["project_graph_a", "reference_standard"]
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_predicate: CandidatePredicate
    replaces_uris: list[str] = Field(default_factory=list)
    supporting_columns: list[str] = Field(default_factory=list)
    grounding_receipt_id: str | None = None

    def to_quantized_confidence(self) -> Decimal:
        """Quantize anchor confidence to 4 decimal places via ROUND_HALF_UP."""
        return Decimal(str(self.confidence)).quantize(
            _QUANT_FOUR_PLACES,
            rounding=ROUND_HALF_UP,
        )


class ColumnPair(StrictModel):
    """Detected multi-column semantic relationship within a table."""

    kind: Literal["amount_currency", "effective_interval", "composite_key", "alternative_keys"]
    columns: list[str]


class TableGroundingResult(StrictModel):
    """Grounding outputs for a single table."""

    table_name: str
    table_id: str | None = None
    inferred_table_anchors: list[TableAnchorCandidate] = Field(default_factory=list)
    paired_columns: list[ColumnPair] = Field(default_factory=list)
    columns: list[ColumnGroundingResult] = Field(default_factory=list)


class GroundingResponse(StrictModel):
    """Top-level response returned by the column grounding engine/service."""

    knowledge_source_id: str
    knowledge_source_version: str
    knowledge_source_sha256: str
    engine_version: str
    mode_used: GroundingExecutionMode
    skip_reason: str | None = None
    tables: list[TableGroundingResult] = Field(default_factory=list)


class FiboSyncReq(StrictModel):
    """Request DTO for installing or drift-checking a FIBO Pack v2 archive/file."""

    pack_file: str | None = None
    tag: str | None = None
    check_project_id: str | None = None
    apply_repin: bool = False


class FiboDriftItem(StrictModel):
    """Single 3-tier FIBO pack drift item for a committed ontology target."""

    tier: Literal["deprecated_with_replacement", "removed_or_split", "pack_sha256_mismatch"]
    target_kind: Literal["concept", "attribute", "relationship", "enumeration"]
    target_id: str
    old_standard_uri: str
    replacement_uri: str | None = None
    detail: str


class FiboProjectDriftReport(StrictModel):
    """Project-wide FIBO drift audit report emitted by `fibo-sync --check-project`."""

    project_id: str
    pack_sha256_orig: str | None
    pack_sha256_new: str
    items: list[FiboDriftItem]
    repin_deltas_applied: int = 0


class FiboSyncResp(StrictModel):
    """Response DTO for `opendv ontology fibo-sync`."""

    installed_pack_path: str | None = None
    pack_sha256: str | None = None
    upstream_tag: str | None = None
    drift_report: FiboProjectDriftReport | None = None


__all__ = [
    "ArchetypeKind",
    "CandidateMapping",
    "CandidatePredicate",
    "ColumnEvidence",
    "ColumnGroundingResult",
    "ColumnPair",
    "ConfidenceInterval",
    "EntryKind",
    "EvidenceContribution",
    "FiboDriftItem",
    "FiboProjectDriftReport",
    "FiboSyncReq",
    "FiboSyncResp",
    "GroundingExecutionMode",
    "GroundingPolicy",
    "GroundingRequest",
    "GroundingResponse",
    "InferredLogicalType",
    "InferredValueProfile",
    "NoMatchCategory",
    "OntologyGroundReq",
    "ReviewReason",
    "SelectionStatus",
    "SemanticContext",
    "TableAnchorCandidate",
    "TableEvidence",
    "TableGroundingResult",
]
