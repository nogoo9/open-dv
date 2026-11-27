"""Internal Layer-1 SPI and rerank DTOs for KnowledgeSource and Layer-4 orchestrator.

Intentionally excluded from `opendv.models.__init__` to respect the D16 token budget.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic.json_schema import DEFAULT_REF_TEMPLATE, GenerateJsonSchema, JsonSchemaMode

from opendv.models.diagnostics import StrictModel
from opendv.models.grounding import (
    ColumnPair,
    EntryKind,
    InferredValueProfile,
    SemanticContext,
    TableAnchorCandidate,
)

_MAX_RERANK_CANDIDATES = 15
_MAX_RESCUE_TERM_LEN = 64


class CalibrationParams(StrictModel):
    """Calibrated log-odds fusion weights, CI hyperparameters, and fast-path gates."""

    calibration_mode: Literal["analytic_logit_normal", "empirical_calibrated"] = (
        "analytic_logit_normal"
    )
    channel_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "lex": 2.2,
            "surface": 2.4,
            "shape_range": 1.8,
            "anchor": 1.1,
            "agent": 2.0,
        }
    )
    beta_0: float = -1.2
    T: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    s_null_base: float = Field(default=0.0, allow_inf_nan=False)
    gamma_null: float = Field(default=0.15, ge=0.0, allow_inf_nan=False)
    sigma_base: float = 0.25
    alpha_n: float = 0.85
    alpha_mod: float = 0.75
    alpha_disagree: float = 1.10
    c_cal: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    fast_path_min_prob: float = 0.95
    fast_path_max_ci_width: float = 0.12
    fast_path_max_eff_sigma: float = 0.65
    fast_path_max_disagree: float = 0.25
    min_top1_probability: float = 0.20
    matched_min_top1_probability: float = 0.65
    matched_min_lower_bound: float = 0.45
    ambiguous_null_threshold: float = 0.25
    min_tail_probability: float = 0.03
    max_effective_sample_count: int = 200

    @classmethod
    def analytic_defaults(cls) -> CalibrationParams:
        """Return default analytic Logit-Normal calibration hyperparameters."""
        return cls()


class KnowledgeSourceMetadata(StrictModel):
    """Static identity and capabilities descriptor for a KnowledgeSource."""

    source_id: str
    version: str
    sha256: str
    anchor_entry_kinds: frozenset[str]
    origin_kind: Literal["project_graph_a", "reference_standard"]


class KnowledgeItem(StrictModel):
    """Normalized knowledge entry exposed by a KnowledgeSource."""

    entry_id: str
    label: str
    entry_kind: EntryKind
    definition: str = ""
    aliases: list[str] = Field(default_factory=list)
    parent_ids: list[str] = Field(default_factory=list)
    neighborhood_links: dict[str, list[str]] = Field(default_factory=dict)
    replaces_uris: list[str] = Field(default_factory=list)
    semantic_context: SemanticContext = Field(default_factory=SemanticContext)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecallHit(StrictModel):
    """Lexical or value/mention recall hit returned by KnowledgeSource."""

    entry_id: str
    label: str
    entry_kind: EntryKind
    raw_score: Decimal
    origin_source_id: str
    origin_kind: Literal["project_graph_a", "reference_standard"]


class TableReasoningContext(StrictModel):
    """Table-level structural context passed to structured candidate evaluation."""

    table_name: str
    table_id: str | None = None
    anchor_hypotheses: list[TableAnchorCandidate] = Field(default_factory=list)
    sibling_summaries: list[str] = Field(default_factory=list)
    fk_targets: dict[str, str] = Field(default_factory=dict)
    paired_columns: list[ColumnPair] = Field(default_factory=list)
    steward_notes: list[str] = Field(default_factory=list)


class BackendReasoningResult(StrictModel):
    """Domain-backend evaluation of a RecallHit against column/table evidence."""

    entry_id: str
    shape_range_score: float
    hard_veto: bool
    veto_reason: str | None = None
    inverse_direction_score: float = 0.0
    semantic_context: SemanticContext | None = None
    suggested_predicate_hint: Literal["exact", "close", "related"] | None = None
    support_detail: str = ""


class RerankCandidateSummary(StrictModel):
    """URI-free candidate summary sent inside RerankColumnPacket."""

    index: int = Field(ge=0)
    label: str
    entry_kind: EntryKind
    definition: str
    semantic_hint: str | None = None
    prior_score: float


class RerankColumnPacket(StrictModel):
    """PII-safe, sample-free column packet passed to Layer-4 agentic reranking."""

    column_name: str
    data_type: str
    steward_note: str | None = None
    inferred_value_profile: InferredValueProfile
    pii_tags: list[str] = Field(default_factory=list)
    candidates: list[RerankCandidateSummary]

    @model_validator(mode="after")
    def _validate_rerank_packet(self) -> RerankColumnPacket:
        if len(self.candidates) > _MAX_RERANK_CANDIDATES:
            raise ValueError(
                f"RerankColumnPacket candidates ({len(self.candidates)}) "
                f"exceeds maximum {_MAX_RERANK_CANDIDATES}"
            )
        if self.inferred_value_profile.shape.observed_enum_values:
            raise ValueError(
                "RerankColumnPacket requires inferred_value_profile.shape.observed_enum_values "
                "to be empty"
            )
        return self


class TableRerankAssignment(StrictModel):
    """Single-column structured rerank selection emitted by Layer-3/4 RerankFn."""

    column_name: str
    selected_index: int | None = Field(default=None, ge=0)
    abstain: bool = False
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    suggested_predicate_hint: Literal["exact", "close", "related"] | None = None
    suggested_search_terms: list[str] = Field(default_factory=list, max_length=3)
    rationale: str = ""

    @model_validator(mode="after")
    def _validate_assignment(self) -> TableRerankAssignment:
        if self.abstain and self.selected_index is not None:
            raise ValueError("abstain=True requires selected_index=None")
        if not self.abstain and self.selected_index is None:
            raise ValueError("abstain=False requires non-None selected_index")
        for idx, term in enumerate(self.suggested_search_terms):
            if len(term) > _MAX_RESCUE_TERM_LEN:
                raise ValueError(
                    f"suggested_search_terms[{idx}] exceeds {_MAX_RESCUE_TERM_LEN} chars"
                )
        return self


class TableRerankResult(StrictModel):
    """Table-batched rerank assignments returned by RerankFn."""

    assignments: list[TableRerankAssignment]

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool | None = None,
        ref_template: str = DEFAULT_REF_TEMPLATE,
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        """Emit two-branch anyOf item schema enforcing (abstain, selected_index) invariant."""
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        defs = schema.get("$defs")
        base = defs.pop("TableRerankAssignment", None) if isinstance(defs, dict) else None
        props = schema.get("properties")
        if isinstance(defs, dict) and isinstance(base, dict) and isinstance(props, dict):
            req = ["column_name", "selected_index", "abstain", "confidence"]
            selected = deepcopy(base)
            selected["title"] = "SelectedTableRerankAssignment"
            selected["additionalProperties"] = False
            selected["required"] = list(req)
            sel_props = selected.get("properties")
            if isinstance(sel_props, dict):
                sel_props["abstain"] = {"type": "boolean", "const": False, "title": "Abstain"}
                sel_props["selected_index"] = {
                    "type": "integer",
                    "minimum": 0,
                    "title": "Selected Index",
                }
            abstained = deepcopy(base)
            abstained["title"] = "AbstainedTableRerankAssignment"
            abstained["additionalProperties"] = False
            abstained["required"] = list(req)
            abs_props = abstained.get("properties")
            if isinstance(abs_props, dict):
                abs_props["abstain"] = {"type": "boolean", "const": True, "title": "Abstain"}
                abs_props["selected_index"] = {"type": "null", "title": "Selected Index"}
            defs["SelectedTableRerankAssignment"] = selected
            defs["AbstainedTableRerankAssignment"] = abstained
            asgn_prop = props.get("assignments")
            if isinstance(asgn_prop, dict):
                asgn_prop["items"] = {
                    "anyOf": [
                        {"$ref": ref_template.format(model="SelectedTableRerankAssignment")},
                        {"$ref": ref_template.format(model="AbstainedTableRerankAssignment")},
                    ]
                }
        return schema


__all__ = [
    "BackendReasoningResult",
    "CalibrationParams",
    "KnowledgeItem",
    "KnowledgeSourceMetadata",
    "RecallHit",
    "RerankCandidateSummary",
    "RerankColumnPacket",
    "TableReasoningContext",
    "TableRerankAssignment",
    "TableRerankResult",
]
