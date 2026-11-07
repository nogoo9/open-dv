"""3-Level Pipeline Explorer Workbench (Pipeline -> Stage -> Artifact) Data Contracts.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and T6 invariants.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from opendv_contracts.dtos.common import BaseContractModel

ArtifactGroup = Literal["Setting", "Input", "Stage output", "Final output"]
VizKind = Literal["ontology", "dv", "table", "kv", "checks", "events", "doc", "code"]
StatusTag = Literal[
    "PASS",
    "AUTO_REPAIRED",
    "ASK-USER",
    "ASK-USER → resolved",
    "REJECT-WITH-TRACE",
    "AUTO-FIX",
    "HOLD",
]


class OntologyConceptSpec(BaseContractModel):
    """Specification of a Graph A business ontology concept."""

    name: str = Field(..., description="Concept identifier")
    arch: str = Field(..., description="ELM archetype")
    def_: str = Field(..., alias="def", description="Business definition")
    keys: str = Field(..., description="Key authority and tiers")

    @property
    def definition(self) -> str:
        return self.def_


class OntologySvgNodeSpec(BaseContractModel):
    """SVG layout node for Graph A ontology visualizer."""

    id: str = Field(..., description="Node identifier")
    label: str = Field(..., description="Display label")
    sub: str = Field(..., description="Subtitle or archetype")
    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")
    w: float = Field(..., description="Width")
    h: float | None = Field(default=None, description="Height")


class OntologySvgEdgeSpec(BaseContractModel):
    """SVG layout edge for Graph A ontology visualizer."""

    path: str = Field(..., description="SVG path string")
    label: str | None = Field(default=None, description="Relationship label")
    lx: float | None = Field(default=None, description="Label X coordinate")
    ly: float | None = Field(default=None, description="Label Y coordinate")


class DvSvgNodeSpec(BaseContractModel):
    """SVG node for Data Vault 2.0 model visualizer."""

    id: str = Field(..., description="Node identifier")
    kind: Literal["hub", "link", "sat", "sat_pii"] = Field(..., description="Entity classification")
    label: str = Field(..., description="Display name")
    sub: str = Field(..., description="Subtitle or attribute summary")
    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")
    w: float = Field(..., description="Width")
    h: float = Field(..., description="Height")


class DvSvgEdgeSpec(BaseContractModel):
    """SVG edge for Data Vault 2.0 model visualizer."""

    x1: float = Field(..., description="Start X")
    y1: float = Field(..., description="Start Y")
    x2: float = Field(..., description="End X")
    y2: float = Field(..., description="End Y")
    dashed: bool = Field(default=False, description="Whether line is dashed")


class DocCandidateOption(BaseContractModel):
    """Entity resolution candidate option for steward hold."""

    id: str = Field(..., description="Candidate entity ID")
    label: str = Field(..., description="Candidate display label")
    score: float = Field(..., description="Confidence score")


class DocExtractionSpec(BaseContractModel):
    """Unstructured text entity extraction span and hold metadata."""

    id: str = Field(..., description="Extraction span identifier")
    quote: str = Field(..., description="Verbatim quote substring")
    start: int = Field(..., description="Character start offset")
    end: int = Field(..., description="Character end offset")
    tier: Literal["Tier-U1", "Tier-U2", "Tier-U3"] = Field(..., description="Authority tier")
    conf: str = Field(..., description="Confidence string")
    decision: str = Field(..., description="Resolution decision summary")
    hold_id: str | None = Field(
        default=None, alias="holdId", description="Active hold ID if pending"
    )
    event_id: str | None = Field(
        default=None, alias="eventId", description="Observability event ID"
    )
    resolved: bool = Field(default=False, description="Whether hold is resolved")
    resolution_action: Literal["APPROVE", "REJECT"] | None = Field(
        default=None, alias="resolutionAction", description="Steward decision action"
    )
    selected_candidate_id: str | None = Field(
        default=None, alias="selectedCandidateId", description="Chosen candidate identifier"
    )
    candidates: list[DocCandidateOption] | None = Field(
        default=None, description="Available candidate options"
    )


class OntologyVizDTO(BaseContractModel):
    """Ontology visualizer payload."""

    concepts: list[OntologyConceptSpec] = Field(default_factory=list)
    nodes: list[OntologySvgNodeSpec] = Field(default_factory=list)
    edges: list[OntologySvgEdgeSpec] = Field(default_factory=list)


class DvVizDTO(BaseContractModel):
    """Data Vault model visualizer payload."""

    nodes: list[DvSvgNodeSpec] = Field(default_factory=list)
    edges: list[DvSvgEdgeSpec] = Field(default_factory=list)


class DocVizDTO(BaseContractModel):
    """Unstructured document visualizer payload."""

    document_name: str = Field(..., alias="documentName")
    source_text: str = Field(..., alias="sourceText")
    extractions: list[DocExtractionSpec] = Field(default_factory=list)


class ArtifactDTO(BaseContractModel):
    """Comprehensive artifact descriptor for Explorer workbench."""

    id: str = Field(..., description="Unique artifact identifier")
    name: str = Field(..., description="Artifact display name or filename")
    short: str = Field(..., description="Concise one-line summary")
    group: ArtifactGroup = Field(..., description="Artifact category")
    type: str = Field(..., description="Semantic entity type")
    fmt: str = Field(..., description="Format (YAML, JSON, SQL, CSV, etc.)")
    viz: VizKind = Field(..., description="Visualizer renderer kind")
    desc: str = Field(..., description="Detailed description")
    raw: str = Field(..., description="RBAC-redacted raw artifact text")
    waves: list[str] | None = Field(default=None, description="Associated wave filter identifiers")
    wave_index: int | None = Field(default=None, description="Primary wave integer index")
    sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Lowercase 64-hex SHA-256 CAS digest",
    )
    cols: list[str] | None = Field(default=None, description="Table column headers")
    rows: list[list[str]] | None = Field(default=None, description="Table rows")
    foot: str | None = Field(default=None, description="Table footer text")
    kv: list[tuple[str, str]] | None = Field(default=None, description="Key-value pairs")
    checks: list[tuple[str, str, str]] | None = Field(default=None, description="Checks scorecard")
    events: list[tuple[str, str, str]] | None = Field(
        default=None, description="Event summary timeline"
    )
    outline: list[tuple[str, str]] | None = Field(default=None, description="Code sections outline")
    ontology: OntologyVizDTO | None = Field(default=None, description="Ontology visualizer model")
    dv: DvVizDTO | None = Field(default=None, description="DV model visualizer model")
    doc: DocVizDTO | None = Field(default=None, description="Document visualizer model")


class StepDTO(BaseContractModel):
    """Pipeline execution step definition inside a stage."""

    name: str = Field(..., description="Step name")
    kind: Literal["action", "decision", "loop"] | None = Field(
        default=None, description="Step kind"
    )
    branch: str | None = Field(default=None, description="Decision branch logic description")
    reads: list[str] = Field(default_factory=list, description="Artifact IDs read by step")
    writes: list[str] = Field(default_factory=list, description="Artifact IDs written by step")
    desc: str = Field(..., description="Step description")


class StageComponentDTO(BaseContractModel):
    """Stage component specification."""

    name: str = Field(..., description="Component name")
    type: str = Field(..., description="Component classification")
    desc: str = Field(..., description="Description")
    source_path: str = Field(..., alias="sourcePath", description="Source path")


class StageDTO(BaseContractModel):
    """Ordered pipeline stage definition (strictly STAGE_00..STAGE_07)."""

    key: str = Field(..., description="Stage key (e.g. STAGE_00_ORCHESTRATION)")
    phase: Literal[0, 1, 2, 3] = Field(..., description="Phase index (0..3)")
    name: str = Field(..., description="Stage display name")
    persona: str = Field(..., description="Assigned agent persona")
    tier: str = Field(..., description="Model tier or execution kind")
    tag: StatusTag = Field(..., description="Stage verdict / status pill")
    blurb: str = Field(..., description="One-line summary")
    desc: str = Field(..., description="Detailed description")
    note: str | None = Field(default=None, description="Optional amber note")
    ins: list[str] = Field(default_factory=list, description="Consumed artifact IDs")
    outs: list[str] = Field(default_factory=list, description="Produced artifact IDs")
    steps: list[StepDTO] = Field(default_factory=list, description="Ordered steps")
    comps: list[tuple[str, str, str, str]] = Field(
        default_factory=list,
        description="Assigned components [name, type, desc, source_path]",
    )


class PhaseSpecDTO(BaseContractModel):
    """Pipeline phase classification."""

    index: Literal[0, 1, 2, 3] = Field(..., description="Phase index")
    label: Literal["Discovery", "Modelling", "Verification", "Compilation"] = Field(
        ..., description="Phase display title"
    )
    sub: str = Field(..., description="Phase subtitle")


class WaveMetadataSpecDTO(BaseContractModel):
    """Wave metadata specification."""

    id: str = Field(..., description="Wave identifier (e.g. W0, W1)")
    name: str = Field(..., description="Wave name")
    description: str = Field(..., description="Wave description")


class ExplorerBundleDTO(BaseContractModel):
    """Top-level 3-Level Pipeline Explorer bundle response DTO."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=True,
    )

    project_id: str = Field(..., alias="projectId", description="Project identifier")
    display_name: str = Field(..., alias="displayName", description="Human-readable project name")
    run_id: str = Field(..., alias="runId", description="Run identifier")
    spec_file: str = Field(..., alias="specFile", description="Pipeline spec filename")
    waves_count: int = Field(..., alias="wavesCount", ge=0, description="Number of waves")
    waves: list[WaveMetadataSpecDTO] = Field(default_factory=list, description="Wave metadata list")
    status_label: str = Field(..., alias="statusLabel", description="Summary status label")
    gates_passed: int = Field(
        default=0, alias="gatesPassed", ge=0, description="Number of gates passed"
    )
    gates_total: int = Field(
        default=6, alias="gatesTotal", ge=0, description="Total number of gates"
    )
    rbac_role: str = Field(default="DATA_STEWARD", alias="rbacRole", description="Active RBAC role")
    has_initial_contract: bool = Field(
        default=False,
        alias="hasInitialContract",
        description="Whether cold-start contract was used",
    )
    phases: list[PhaseSpecDTO] = Field(default_factory=list, description="4 canonical phases")
    stages: list[StageDTO] = Field(default_factory=list, description="8 canonical stages")
    artifacts: dict[str, ArtifactDTO] = Field(default_factory=dict, description="Artifact map")
    setting_ids: list[str] = Field(
        default_factory=list, alias="settingIds", description="Setting artifact IDs"
    )
    input_ids: list[str] = Field(
        default_factory=list, alias="inputIds", description="Input artifact IDs"
    )
    output_ids: list[str] = Field(
        default_factory=list, alias="outputIds", description="Output artifact IDs"
    )

    @field_validator("stages")
    @classmethod
    def _validate_stages(cls, v: list[StageDTO]) -> list[StageDTO]:
        if len(v) != 8:
            raise ValueError(
                f"ExplorerBundleDTO stages must contain exactly 8 stages, got {len(v)}"
            )
        expected_prefixes = [f"STAGE_{i:02d}" for i in range(8)]
        actual_prefixes = [s.key[:8] for s in v]
        if actual_prefixes != expected_prefixes:
            msg = (
                f"ExplorerBundleDTO stages must strictly match {expected_prefixes}, "
                f"got {actual_prefixes}"
            )
            raise ValueError(msg)
        return v

    def __getattr__(self, name: str) -> Any:
        mapping = {
            "projectId": "project_id",
            "displayName": "display_name",
            "runId": "run_id",
            "specFile": "spec_file",
            "wavesCount": "waves_count",
            "statusLabel": "status_label",
            "gatesPassed": "gates_passed",
            "gatesTotal": "gates_total",
            "rbacRole": "rbac_role",
            "hasInitialContract": "has_initial_contract",
            "settingIds": "setting_ids",
            "inputIds": "input_ids",
            "outputIds": "output_ids",
        }
        if name in mapping:
            return getattr(self, mapping[name])
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class RunArtifactDTO(BaseContractModel):
    """Run-isolated artifact inspection response DTO (Decision D1, T6 invariants)."""

    artifact_id: str = Field(..., description="Unique artifact identifier within run")
    run_id: str = Field(..., description="Associated run identifier")
    sha256: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
        description="Lowercase 64-hex SHA-256 CAS digest",
    )
    content: str = Field(..., description="RBAC-redacted text or serialized content")
    artifact_type: str | None = Field(default=None, description="Artifact category")
    filename: str | None = Field(default=None, description="Artifact filename")
    byte_length: int | None = Field(default=None, ge=0, description="Artifact size in bytes")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary artifact metadata"
    )
