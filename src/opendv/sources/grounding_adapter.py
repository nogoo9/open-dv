"""Adapter converting Layer-2b SourceProfile/ColumnShapeStats to Layer-1 GroundingRequest DTOs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    TableEvidence,
)
from opendv.models.project import ProjectState
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile


def column_profile_to_evidence(
    col: ColumnProfile,
    *,
    steward_note: str | None = None,
) -> ColumnEvidence:
    """Convert a Layer-2b ColumnProfile (with precomputed shape_stats) into ColumnEvidence."""
    return ColumnEvidence(
        column_name=col.col_name,
        data_type=col.sql_type,
        sample_values=list(col.sample_values),
        steward_note=steward_note,
        nullable=col.nullable,
        is_pk=col.is_pk,
        pk_ord=col.pk_ord,
        fk_ref=col.fk_ref,
        null_ratio=col.null_ratio,
        distinct_count_est=col.distinct_count_est,
        pii_tags=list(col.pii_tags),
        precomputed_shape=col.shape_stats,
    )


def table_profile_to_evidence(
    table: TableProfile,
    *,
    steward_notes: Sequence[str] = (),
    column_notes: Mapping[str, str] | None = None,
) -> TableEvidence:
    """Convert a TableProfile into a TableEvidence DTO."""
    notes_map = column_notes or {}
    columns = [
        column_profile_to_evidence(col, steward_note=notes_map.get(col.col_name))
        for col in table.columns
    ]
    return TableEvidence(
        table_name=table.table_name,
        table_id=table.table_id,
        columns=columns,
        steward_notes=list(steward_notes),
        row_count_est=table.row_count_est,
    )


def source_profiles_to_grounding_request(
    profiles: Sequence[SourceProfile],
    *,
    project_state: ProjectState | None = None,
    table_ids: AbstractSet[str] | None = None,
    table_steward_notes: Mapping[str, Sequence[str]] | None = None,
    column_steward_notes: Mapping[tuple[str, str], str] | None = None,
    global_steward_notes: Sequence[str] = (),
    policy: GroundingPolicy | None = None,
    max_tables: int = 50,
    max_columns: int = 500,
) -> GroundingRequest:
    """Convert one or more SourceProfiles into a bounded GroundingRequest."""
    _ = project_state
    t_notes_map = table_steward_notes or {}
    c_notes_map = column_steward_notes or {}

    tables: list[TableEvidence] = []
    for prof in profiles:
        for tbl in prof.tables:
            if (
                table_ids is not None
                and tbl.table_id not in table_ids
                and tbl.table_name not in table_ids
            ):
                continue
            t_notes = t_notes_map.get(tbl.table_id) or t_notes_map.get(tbl.table_name) or ()
            col_notes: dict[str, str] = {}
            for col in tbl.columns:
                note = c_notes_map.get((tbl.table_id, col.col_name)) or c_notes_map.get(
                    (tbl.table_name, col.col_name)
                )
                if note is not None:
                    col_notes[col.col_name] = note
            tables.append(
                table_profile_to_evidence(
                    tbl,
                    steward_notes=t_notes,
                    column_notes=col_notes,
                )
            )

    return GroundingRequest(
        tables=tables,
        global_steward_notes=list(global_steward_notes),
        policy=policy or GroundingPolicy(),
        max_tables=max_tables,
        max_columns=max_columns,
    )


def source_profile_to_grounding_request(
    profile: SourceProfile,
    *,
    project_state: ProjectState | None = None,
    table_ids: AbstractSet[str] | None = None,
    table_steward_notes: Mapping[str, Sequence[str]] | None = None,
    column_steward_notes: Mapping[tuple[str, str], str] | None = None,
    global_steward_notes: Sequence[str] = (),
    policy: GroundingPolicy | None = None,
    max_tables: int = 50,
    max_columns: int = 500,
) -> GroundingRequest:
    """Convenience wrapper converting a single SourceProfile into a GroundingRequest."""
    return source_profiles_to_grounding_request(
        [profile],
        project_state=project_state,
        table_ids=table_ids,
        table_steward_notes=table_steward_notes,
        column_steward_notes=column_steward_notes,
        global_steward_notes=global_steward_notes,
        policy=policy,
        max_tables=max_tables,
        max_columns=max_columns,
    )


__all__ = [
    "column_profile_to_evidence",
    "source_profile_to_grounding_request",
    "source_profiles_to_grounding_request",
    "table_profile_to_evidence",
]
