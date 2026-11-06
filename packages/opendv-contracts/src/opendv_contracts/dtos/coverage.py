from __future__ import annotations

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class MappedConceptCoverage(BaseContractModel):
    """Detailed mapping breakdown for a Canonical Business Concept."""

    concept_uri: str = Field(..., description="Canonical concept URI in Graph A")
    concept_name: str | None = Field(default=None, description="Concept name")
    target_hub: str | None = Field(default=None, description="Target Data Vault Hub")
    mapped_tables: list[str] = Field(
        default_factory=list, description="Bronze tables mapped to this concept"
    )
    mapped_columns: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of mapped column references ({'table': ..., 'column': ...})",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Mapping confidence score")


class UnmappedTableGap(BaseContractModel):
    """Diagnostic detail for an unmapped bronze table gap."""

    table_name: str = Field(..., description="Qualified or base table name")
    columns: list[str] = Field(
        default_factory=list, description="All columns in this unmapped table"
    )
    row_count: int | None = Field(default=None, description="Observed row count if profiled")
    candidate_hints: list[str] = Field(
        default_factory=list, description="Potential concept candidate hints"
    )
    reason: str = Field(default="ABSENT_FROM_ONTOLOGY", description="Reason for unmapped status")


class UnmappedColumnGap(BaseContractModel):
    """Diagnostic detail for an unmapped bronze column gap."""

    table_name: str = Field(..., description="Table containing the unmapped column")
    column_name: str = Field(..., description="Unmapped column name")
    inferred_type: str | None = Field(default=None, description="Inferred or physical data type")
    sample_values: list[str] = Field(default_factory=list, description="Sample values if available")
    candidate_concept_uris: list[str] = Field(
        default_factory=list, description="Candidate concept URIs for recommender"
    )


class UnmappedMentionGap(BaseContractModel):
    """Diagnostic detail for an unmapped unstructured mention gap."""

    doc_sha256: str = Field(..., description="Document content SHA-256")
    char_start: int = Field(ge=0, description="Character start offset")
    char_end: int = Field(ge=0, description="Character end offset")
    text_snippet: str = Field(..., description="Verbatim text span quote")
    inferred_entity_type: str | None = Field(default=None, description="Inferred entity type")


class OntologyCoverageArtifact(BaseContractModel):
    """Explicit deterministic coverage artifact reporting ontology mapping metrics and gaps.

    Replaces silent provisional-URI synthesis per Decision D9 and ONT-03.
    """

    artifact_id: str = Field(
        ..., description="Unique artifact identifier, e.g. art-w0-stage01-ontology-coverage"
    )
    run_id: str | None = Field(default=None, description="Pipeline run identifier")
    wave_id: str = Field(default="wave_0", description="Pipeline wave identifier or index")
    domain_namespace: str = Field(default="", description="Domain namespace of the ontology")
    graph_a_version: str = Field(default="1.0.0", description="Graph A ontology version")
    graph_a_sha256: str = Field(
        default="", description="Cryptographic SHA-256 hash of Graph A ontology"
    )
    total_tables: int = Field(default=0, ge=0, description="Total number of Bronze tables surveyed")
    mapped_tables_count: int = Field(default=0, ge=0, description="Count of mapped tables")
    unmapped_tables_count: int = Field(default=0, ge=0, description="Count of unmapped tables")
    total_columns: int = Field(
        default=0, ge=0, description="Total number of Bronze columns surveyed"
    )
    mapped_columns_count: int = Field(default=0, ge=0, description="Count of mapped columns")
    unmapped_columns_count: int = Field(default=0, ge=0, description="Count of unmapped columns")
    coverage_ratio: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Exact coverage ratio: mapped_columns / max(1, total_columns)",
    )
    mapped_concepts: list[str] = Field(
        default_factory=list, description="List of mapped canonical concept URIs"
    )
    mapped_tables: list[str] = Field(
        default_factory=list, description="List of mapped bronze table names"
    )
    unmapped_tables: list[str] = Field(
        default_factory=list, description="List of unmapped bronze table names"
    )
    unmapped_columns: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of unmapped column entries: [{'table': ..., 'column': ...}]",
    )
    unmapped_mentions: list[str] = Field(
        default_factory=list,
        description="List of unmapped document mentions / entity snippets",
    )
    provisional_uris_found: list[str] = Field(
        default_factory=list,
        description="List of deprecated provisional URIs detected in inputs",
    )
    diagnostics: list[str] = Field(
        default_factory=list,
        description="Diagnostic messages and gap notices for steward/recommender",
    )
    detailed_mapped_concepts: list[MappedConceptCoverage] = Field(
        default_factory=list, description="Detailed concept mapping breakdowns"
    )
    detailed_unmapped_tables: list[UnmappedTableGap] = Field(
        default_factory=list, description="Detailed unmapped table gap descriptions"
    )
    detailed_unmapped_columns: list[UnmappedColumnGap] = Field(
        default_factory=list, description="Detailed unmapped column gap descriptions"
    )
    detailed_unmapped_mentions: list[UnmappedMentionGap] = Field(
        default_factory=list, description="Detailed unmapped mention gap descriptions"
    )
    generated_at_utc: str | None = Field(
        default=None, description="ISO 8601 UTC timestamp of coverage calculation"
    )
