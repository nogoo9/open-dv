"""V2 RecordsSourceAdapter and minimal schema instance synthesis helpers."""

from __future__ import annotations

import contextlib
import enum
import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal, get_args, get_origin

import duckdb
import pyarrow
from pydantic import BaseModel, ValidationError

from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.sources.pii import (
    detect_pii_in_column_name,
    detect_pii_in_value,
    mask_cell_value,
)


def validate_candidate_with_schema(schema_cls: type[BaseModel], candidate: Any) -> Any:
    """Validate candidate (model instance, dict, or JSON string) against schema_cls."""
    if isinstance(candidate, schema_cls):
        return candidate
    if isinstance(candidate, BaseModel):
        return schema_cls.model_validate(candidate.model_dump(mode="json"))
    if isinstance(candidate, dict):
        return schema_cls.model_validate(candidate)
    if isinstance(candidate, str):
        return schema_cls.model_validate_json(candidate)
    return candidate


def default_for_annotation(annotation: Any, *, non_empty_collections: bool = False) -> Any:
    """Construct a minimal valid default value for a Python/Pydantic type annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal and args:
        return args[0]
    if origin in (list, tuple, set, frozenset):
        if non_empty_collections and args:
            elem = default_for_annotation(args[0], non_empty_collections=True)
            return [elem] if elem is not None else []
        return []
    if origin is dict:
        return {}
    if origin is not None and args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) < len(args) and not non_empty_collections:
            return None
        if non_none:
            return default_for_annotation(
                non_none[0],
                non_empty_collections=non_empty_collections,
            )

    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[0].value if members else "default"
        if issubclass(annotation, BaseModel):
            inst = synthesize_minimal_schema_instance(annotation)
            return inst.model_dump(mode="json") if inst is not None else {}
        if issubclass(annotation, Decimal):
            return Decimal("1.0")
        if issubclass(annotation, bool):
            return True
        if issubclass(annotation, int):
            return 1
        if issubclass(annotation, float):
            return 1.0
        if issubclass(annotation, str):
            return "deterministic"
    return "deterministic"


def synthesize_minimal_schema_instance(
    schema_cls: type[BaseModel],
    prompt: str = "",
) -> BaseModel | None:
    """Synthesize a valid minimal instance of any V2 StrictModel / BaseModel subclass."""
    _ = prompt
    with contextlib.suppress(ValidationError, ValueError, TypeError):
        return schema_cls.model_validate({})

    for non_empty in (False, True):
        payload: dict[str, Any] = {}
        for field_name, field_info in schema_cls.model_fields.items():
            if not field_info.is_required() and not non_empty:
                continue
            payload[field_name] = default_for_annotation(
                field_info.annotation,
                non_empty_collections=non_empty,
            )
        with contextlib.suppress(ValidationError, ValueError, TypeError):
            return schema_cls.model_validate(payload)
    return None


def _infer_sql_type(values: Sequence[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "DOUBLE"
    return "VARCHAR"


class RecordsSourceAdapter:
    """V2 test helper projecting in-memory records or JSON payloads into SourceProfile / Arrow."""

    def extract_records(
        self,
        records_or_json: Sequence[Mapping[str, Any]] | str | bytes,
    ) -> list[dict[str, Any]]:
        """Normalize records sequence or JSON payload into a non-empty list of dicts."""
        if isinstance(records_or_json, (str, bytes)):
            raw_text = (
                records_or_json.decode("utf-8", errors="replace")
                if isinstance(records_or_json, bytes)
                else records_or_json
            )
            parsed: Any = json.loads(raw_text)
            if isinstance(parsed, dict):
                for key in ("records", "data", "rows", "items"):
                    if isinstance(parsed.get(key), list):
                        parsed = parsed[key]
                        break
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("JSON payload must contain a non-empty list of objects")
            return [dict(item) for item in parsed]

        rows = [dict(r) for r in records_or_json]
        if not rows:
            raise ValueError("Records sequence must not be empty")
        return rows

    def to_arrow_table(
        self,
        records_or_json: Sequence[Mapping[str, Any]] | str | bytes,
    ) -> pyarrow.Table:
        """Convert normalized records into a PyArrow Table."""
        return pyarrow.Table.from_pylist(self.extract_records(records_or_json))

    def register(
        self,
        conn: duckdb.DuckDBPyConnection,
        relation_name: str,
        records_or_json: Sequence[Mapping[str, Any]] | str | bytes,
    ) -> None:
        """Register an in-memory Arrow view inside a DuckDB connection."""
        conn.register(relation_name, self.to_arrow_table(records_or_json))

    def to_source_profile(
        self,
        source_id: str,
        table_name: str,
        records_or_json: Sequence[Mapping[str, Any]] | str | bytes,
        *,
        pk_cols: Sequence[str] = (),
    ) -> SourceProfile:
        """Project records into a PII-masked V2 SourceProfile."""
        rows = self.extract_records(records_or_json)
        col_names = list(dict.fromkeys(k for row in rows for k in row))
        pk_set = set(pk_cols)
        col_profiles: list[ColumnProfile] = []
        for col_name in col_names:
            vals = [row.get(col_name) for row in rows]
            null_count = sum(1 for v in vals if v is None)
            distinct_cnt = len({str(v) for v in vals if v is not None})
            tags = detect_pii_in_column_name(col_name)
            for v in vals[:5]:
                if v is not None:
                    tags.extend(detect_pii_in_value(str(v), col_name=col_name))
            dedup_tags = list(dict.fromkeys(tags))
            samples = [
                mask_cell_value(
                    None if v is None else str(v),
                    col_name=col_name,
                    column_pii_tags=dedup_tags,
                )
                for v in vals[:5]
            ]
            pk_ord = pk_cols.index(col_name) + 1 if col_name in pk_set else None
            col_profiles.append(
                ColumnProfile(
                    col_name=col_name,
                    sql_type=_infer_sql_type(vals),
                    nullable=null_count > 0,
                    is_pk=col_name in pk_set,
                    pk_ord=pk_ord,
                    null_ratio=round(null_count / len(rows), 4),
                    distinct_count_est=distinct_cnt,
                    pii_tags=dedup_tags,
                    sample_values=samples,
                )
            )
        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        table_prof = TableProfile(
            table_id=f"{source_id}:{table_name}",
            table_name=table_name,
            row_count_est=len(rows),
            columns=col_profiles,
        )
        return SourceProfile(
            source_id=source_id,
            schema_fingerprint=digest,
            table_count=1,
            tables=[table_prof],
        )


__all__ = [
    "RecordsSourceAdapter",
    "default_for_annotation",
    "synthesize_minimal_schema_instance",
    "validate_candidate_with_schema",
]
