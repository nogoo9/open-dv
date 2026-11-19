"""Service use cases for Data Vault 2.0 Schema Explorer & persisted DuckDB queries (`D11`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import yaml

from opendv_contracts.dtos.dv_schema import (
    DvHubSchemaDTO,
    DvLinkSchemaDTO,
    DvPitTableSchemaDTO,
    DvReferenceTableSchemaDTO,
    DvSatelliteSchemaDTO,
    DvSchemaProjectionDTO,
    DvSchemaQueryResponseDTO,
    DvSchemaTableRowsDTO,
    DvSchemaWaveDiffDTO,
)
from opendv_contracts.errors import ErrorCode
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_duckdb_sandbox.sandbox_persistence import (
    execute_readonly_sandbox_sql,
    get_sandbox_db_path,
    query_sandbox_table_rows,
)
from opendv_duckdb_sandbox.sql_guard import validate_safe_sql
from opendv_service.errors import InputValidationError, NotFoundError

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer


def _resolve_existing_file(state: BaseStateContainer, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p.resolve()
    cand = (state.repo_root / raw_path).resolve()
    if cand.is_file():
        return cand
    return None


def _load_run_ir_and_contract(
    state: BaseStateContainer,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.", error_code=ErrorCode.RUN_NOT_FOUND)

    run_dto = state.runs[run_id]
    report = state.pipeline_reports.get(run_id)
    report_dict: dict[str, Any] = report if isinstance(report, dict) else {}

    ir_dict: dict[str, Any] = {}
    contract_dict: dict[str, Any] = {}
    if isinstance(report_dict.get("contract"), dict):
        contract_dict = dict(report_dict["contract"])

    candidate_dirs: list[Path] = []
    final_arts = report_dict.get("final_artifacts")
    if isinstance(final_arts, dict):
        ir_ref = final_arts.get("final_canonical_ir")
        if isinstance(ir_ref, dict):
            ir_p = _resolve_existing_file(state, ir_ref.get("file_path") or ir_ref.get("path"))
            if ir_p is not None:
                candidate_dirs.append(ir_p.parent)
                ir_dict = json.loads(ir_p.read_text(encoding="utf-8"))
        fc_ref = final_arts.get("final_contract")
        if isinstance(fc_ref, dict):
            fc_p = _resolve_existing_file(state, fc_ref.get("file_path") or fc_ref.get("path"))
            if fc_p is not None:
                candidate_dirs.append(fc_p.parent)
                if not contract_dict:
                    loaded = yaml.safe_load(fc_p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        contract_dict = loaded

    for art_ref in report_dict.get("artifacts") or []:
        if not isinstance(art_ref, dict):
            continue
        p = _resolve_existing_file(state, art_ref.get("file_path") or art_ref.get("path"))
        if p is not None:
            candidate_dirs.append(p.parent)
            if p.name == "final_canonical_ir.json" and not ir_dict:
                ir_dict = json.loads(p.read_text(encoding="utf-8"))
            elif p.name == "final_contract.yaml" and not contract_dict:
                loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    contract_dict = loaded

    proj = state.projects.get(run_dto.project_id)
    if proj is not None:
        if not ir_dict and proj.canonical_ir_path:
            p = _resolve_existing_file(state, proj.canonical_ir_path)
            if p is not None:
                candidate_dirs.append(p.parent)
                ir_dict = json.loads(p.read_text(encoding="utf-8"))
        if not contract_dict and proj.contract_path:
            p = _resolve_existing_file(state, proj.contract_path)
            if p is not None:
                candidate_dirs.append(p.parent)
                loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    contract_dict = loaded

    for c_dir in candidate_dirs:
        ir_cand = c_dir / "final_canonical_ir.json"
        if not ir_dict and ir_cand.is_file():
            ir_dict = json.loads(ir_cand.read_text(encoding="utf-8"))
        fc_cand = c_dir / "final_contract.yaml"
        if not contract_dict and fc_cand.is_file():
            loaded = yaml.safe_load(fc_cand.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                contract_dict = loaded

    if not ir_dict and contract_dict:
        ir_obj = lower_contract_to_canonical_ir(contract_dict, validate_schema=False)
        ir_dict = ir_obj.model_dump(mode="json", exclude_none=True)

    if not ir_dict:
        raise NotFoundError(
            f"Compiled DV schema is not available for run '{run_id}'.",
            error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        )

    scorecard = dict(report_dict.get("gate_scorecard") or {})
    if not scorecard:
        scorecard = run_dto.gate_scorecard.model_dump(mode="json")

    return ir_dict, contract_dict, scorecard


def _build_projection_from_ir(
    run_id: str,
    ir_dict: dict[str, Any],
    contract_dict: dict[str, Any],
    scorecard: dict[str, Any],
    *,
    compare_run_id: str | None = None,
    wave_diff: DvSchemaWaveDiffDTO | None = None,
) -> DvSchemaProjectionDTO:
    contract_sats_by_name = {
        str(s.get("name")): s
        for s in (contract_dict.get("satellites") or [])
        if isinstance(s, dict) and s.get("name")
    }
    contract_links_by_name = {
        str(lnk.get("name")): lnk
        for lnk in (contract_dict.get("links") or [])
        if isinstance(lnk, dict) and lnk.get("name")
    }

    hubs = [
        DvHubSchemaDTO(
            name=str(h.get("name", "")),
            hk_column=str((h.get("hk_column") or {}).get("name", "")),
            business_keys=[
                str(bk.get("name", ""))
                for bk in (h.get("business_keys") or [])
                if isinstance(bk, dict)
            ],
            bkcc_column_expr=h.get("bkcc_column_expr"),
            ontology_concept_uri=h.get("ontology_concept_uri"),
            source_mappings=list(h.get("source_mappings") or []),
        )
        for h in (ir_dict.get("hubs") or [])
        if isinstance(h, dict)
    ]

    links = []
    for lnk in ir_dict.get("links") or []:
        if not isinstance(lnk, dict):
            continue
        lname = str(lnk.get("name", ""))
        sm0 = (lnk.get("source_mappings") or [{}])[0]
        c_lnk = contract_links_by_name.get(lname, {})
        where_cond = (
            sm0.get("where_filter_condition") if isinstance(sm0, dict) else None
        ) or c_lnk.get("where_filter_condition")
        links.append(
            DvLinkSchemaDTO(
                name=lname,
                hk_column=str((lnk.get("hk_column") or {}).get("name", "")),
                link_type=str(lnk.get("link_type", "STANDARD")),
                hub_references=list(lnk.get("hub_references") or []),
                degenerate_columns=[
                    str(c.get("name", "")) if isinstance(c, dict) else str(c)
                    for c in (lnk.get("degenerate_columns") or [])
                ],
                where_filter_condition=where_cond,
            )
        )

    satellites = []
    for sat in ir_dict.get("satellites") or []:
        if not isinstance(sat, dict):
            continue
        sname = str(sat.get("name", ""))
        sm = sat.get("source_mapping") or {}
        c_sat = contract_sats_by_name.get(sname, {})
        soft_dep = list(
            (sm.get("soft_deprecated_columns") if isinstance(sm, dict) else None)
            or c_sat.get("soft_deprecated_columns")
            or []
        )
        hd_col = sat.get("hash_diff_column") or {}
        satellites.append(
            DvSatelliteSchemaDTO(
                name=sname,
                parent_entity=str(sat.get("parent_entity", "")),
                satellite_type=str(sat.get("satellite_type", "STANDARD")),
                hash_diff_column=str(hd_col.get("name", "hash_diff"))
                if isinstance(hd_col, dict)
                else str(hd_col),
                hash_diff_migration_policy=str(
                    sat.get("hash_diff_migration_policy")
                    or c_sat.get("hash_diff_migration_policy")
                    or "SPLIT_NEW_SATELLITE"
                ),
                payload_columns=list(sat.get("payload_columns") or []),
                soft_deprecated_columns=soft_dep,
            )
        )

    ref_tables = [
        DvReferenceTableSchemaDTO(
            name=str(r.get("name", "")),
            natural_code_column=str(r.get("natural_code_column", "")),
            description_columns=[
                str(c.get("name", "")) if isinstance(c, dict) else str(c)
                for c in (r.get("description_columns") or [])
            ],
        )
        for r in (ir_dict.get("reference_tables") or [])
        if isinstance(r, dict)
    ]

    pit_tables = [
        DvPitTableSchemaDTO(
            name=str(p.get("name", "")),
            target_hub=str(p.get("target_hub", "")),
            tracked_satellites=list(p.get("tracked_satellites") or []),
        )
        for p in (ir_dict.get("pit_tables") or [])
        if isinstance(p, dict)
    ]

    return DvSchemaProjectionDTO(
        run_id=run_id,
        compare_run_id=compare_run_id,
        model_name=str(ir_dict.get("model_name", "")),
        hubs=hubs,
        links=links,
        satellites=satellites,
        reference_tables=ref_tables,
        pit_tables=pit_tables,
        column_triage_ledger=list(contract_dict.get("column_triage_ledger") or []),
        scorecard=scorecard,
        wave_diff=wave_diff,
    )


def _compute_wave_diff(
    current: DvSchemaProjectionDTO,
    baseline: DvSchemaProjectionDTO,
    *,
    compare_run_id: str,
) -> DvSchemaWaveDiffDTO:
    base_hubs = {h.name for h in baseline.hubs}
    base_links = {lnk.name for lnk in baseline.links}
    base_sats = {s.name: s for s in baseline.satellites}

    added_hubs = sorted(h.name for h in current.hubs if h.name not in base_hubs)
    added_links = sorted(lnk.name for lnk in current.links if lnk.name not in base_links)
    added_satellites = sorted(s.name for s in current.satellites if s.name not in base_sats)

    dropped_count = 0
    hash_diff_mutations = 0
    added_columns: list[dict[str, str]] = []
    soft_deprecated: list[dict[str, str]] = []

    for sat in current.satellites:
        curr_cols = [
            str(c.get("name", "")) for c in sat.payload_columns if isinstance(c, dict)
        ]
        for sd in sat.soft_deprecated_columns:
            soft_deprecated.append({"satellite": sat.name, "column": sd})
        if sat.name not in base_sats:
            for col in curr_cols:
                added_columns.append({"satellite": sat.name, "column": col})
            continue
        base_sat = base_sats[sat.name]
        base_cols = [
            str(c.get("name", "")) for c in base_sat.payload_columns if isinstance(c, dict)
        ]
        curr_set = set(curr_cols) | set(sat.soft_deprecated_columns)
        for b_col in base_cols:
            if b_col not in curr_set:
                dropped_count += 1
        if curr_cols[: len(base_cols)] != base_cols:
            hash_diff_mutations += 1
        for c in curr_cols:
            if c not in set(base_cols):
                added_columns.append({"satellite": sat.name, "column": c})

    return DvSchemaWaveDiffDTO(
        compare_run_id=compare_run_id,
        dropped_columns_count=dropped_count,
        hash_diff_mutations_count=hash_diff_mutations,
        added_hubs=added_hubs,
        added_links=added_links,
        added_satellites=added_satellites,
        added_columns=added_columns,
        soft_deprecated_columns=soft_deprecated,
    )


def get_dv_schema_projection(
    state: BaseStateContainer,
    run_id: str,
    *,
    compare_run_id: str | None = None,
) -> DvSchemaProjectionDTO:
    """Project DV 2.0 schema metadata and optional cross-wave diff for ``run_id``."""
    ir_dict, contract_dict, scorecard = _load_run_ir_and_contract(state, run_id)
    curr_proj = _build_projection_from_ir(run_id, ir_dict, contract_dict, scorecard)

    if compare_run_id:
        b_ir, b_contract, b_score = _load_run_ir_and_contract(state, compare_run_id)
        base_proj = _build_projection_from_ir(compare_run_id, b_ir, b_contract, b_score)
        diff = _compute_wave_diff(curr_proj, base_proj, compare_run_id=compare_run_id)
        return curr_proj.model_copy(
            update={"compare_run_id": compare_run_id, "wave_diff": diff}
        )
    return curr_proj


def get_dv_schema_table_rows(
    state: BaseStateContainer,
    run_id: str,
    table_name: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> DvSchemaTableRowsDTO:
    """Read paginated rows from a persisted DuckDB sandbox table for ``run_id``."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.", error_code=ErrorCode.RUN_NOT_FOUND)
    try:
        db_path = get_sandbox_db_path(state.state_dir, run_id)
    except ValueError as exc:
        raise InputValidationError(str(exc), error_code=ErrorCode.INVALID_REQUEST) from exc
    if not db_path.is_file():
        raise NotFoundError(
            f"Persisted sandbox database not found for run '{run_id}'.",
            error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        )
    try:
        res = query_sandbox_table_rows(db_path, table_name, limit=limit, offset=offset)
    except KeyError as exc:
        raise NotFoundError(str(exc), error_code=ErrorCode.ARTIFACT_NOT_FOUND) from exc
    except ValueError as exc:
        raise InputValidationError(str(exc), error_code=ErrorCode.INVALID_REQUEST) from exc
    return DvSchemaTableRowsDTO(run_id=run_id, **res)


def execute_dv_schema_query(
    state: BaseStateContainer,
    run_id: str,
    *,
    sql: str,
    max_rows: int = 500,
) -> DvSchemaQueryResponseDTO:
    """Validate and execute read-only SQL against the persisted DuckDB sandbox."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.", error_code=ErrorCode.RUN_NOT_FOUND)
    try:
        validate_safe_sql(sql)
        db_path = get_sandbox_db_path(state.state_dir, run_id)
    except ValueError as exc:
        raise InputValidationError(str(exc), error_code=ErrorCode.INVALID_REQUEST) from exc
    if not db_path.is_file():
        raise NotFoundError(
            f"Persisted sandbox database not found for run '{run_id}'.",
            error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        )
    try:
        res = execute_readonly_sandbox_sql(db_path, sql, max_rows=max_rows)
    except (ValueError, duckdb.Error) as exc:
        raise InputValidationError(str(exc), error_code=ErrorCode.INVALID_REQUEST) from exc
    return DvSchemaQueryResponseDTO(run_id=run_id, **res)


__all__ = [
    "execute_dv_schema_query",
    "get_dv_schema_projection",
    "get_dv_schema_table_rows",
]
