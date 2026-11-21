"""Run artifact resolution and normalization helpers for DV Schema Explorer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlglot
import yaml
from sqlglot import errors as sqlglot_errors
from sqlglot import exp

from opendv_contracts.errors import ErrorCode
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_service.errors import NotFoundError, RolePermissionError
from opendv_service.ontology.redaction import is_pii_key, redact_sample_value
from opendv_telemetry.rbac_redactor import RbacRole, resolve_cleared_tiers

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer


def resolve_existing_file(state: BaseStateContainer, raw_path: str | None) -> Path | None:
    """Resolve an existing file path either directly or relative to ``state.repo_root``."""
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p.resolve()
    cand = (state.repo_root / raw_path).resolve()
    if cand.is_file():
        return cand
    return None


def extract_four_zone_sql_text(four_zone_sql: Any) -> str:
    """Extract concatenated SQL text from a ``four_zone_sql`` report bundle."""
    if isinstance(four_zone_sql, str):
        return four_zone_sql
    if not isinstance(four_zone_sql, dict):
        return ""
    for dialect in ("duckdb", "databricks"):
        bundle = four_zone_sql.get(dialect)
        if isinstance(bundle, dict):
            if isinstance(bundle.get("all_zones"), str) and bundle["all_zones"].strip():
                return bundle["all_zones"]
            parts = [
                str(bundle[k])
                for k in (
                    "zone_0_staging",
                    "zone_1_raw_vault",
                    "zone_2_business_vault",
                    "zone_3_info_mart",
                )
                if isinstance(bundle.get(k), str) and str(bundle.get(k)).strip()
            ]
            if parts:
                return "\n\n".join(parts)
    if isinstance(four_zone_sql.get("all_zones"), str):
        return str(four_zone_sql["all_zones"])
    return ""


def resolve_run_artifact_sources(
    state: BaseStateContainer,
    run_id: str,
) -> dict[str, Any]:
    """Load persisted IR, ContractV5, SQL, and raw artifact texts for ``run_id``."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.", error_code=ErrorCode.RUN_NOT_FOUND)

    run_dto = state.runs[run_id]
    report = state.pipeline_reports.get(run_id)
    report_dict: dict[str, Any] = report if isinstance(report, dict) else {}

    ir_dict: dict[str, Any] = {}
    contract_dict: dict[str, Any] = {}
    contract_raw = (
        str(report_dict["contract_content"])
        if isinstance(report_dict.get("contract_content"), str)
        and report_dict["contract_content"].strip()
        else ""
    )
    ir_raw = ""
    if isinstance(report_dict.get("canonical_ir"), dict):
        ir_dict = dict(report_dict["canonical_ir"])
        ir_raw = json.dumps(ir_dict, indent=2)
    elif isinstance(report_dict.get("canonical_ir"), str) and report_dict["canonical_ir"].strip():
        ir_raw = str(report_dict["canonical_ir"])
        ir_dict = json.loads(ir_raw)

    if isinstance(report_dict.get("contract"), dict):
        contract_dict = dict(report_dict["contract"])

    candidate_dirs: list[Path] = []
    final_arts = report_dict.get("final_artifacts")
    if isinstance(final_arts, dict):
        ir_ref = final_arts.get("final_canonical_ir")
        if isinstance(ir_ref, dict):
            ir_p = resolve_existing_file(state, ir_ref.get("file_path") or ir_ref.get("path"))
            if ir_p is not None:
                candidate_dirs.append(ir_p.parent)
                ir_raw = ir_p.read_text(encoding="utf-8")
                ir_dict = json.loads(ir_raw)
        fc_ref = final_arts.get("final_contract")
        if isinstance(fc_ref, dict):
            fc_p = resolve_existing_file(state, fc_ref.get("file_path") or fc_ref.get("path"))
            if fc_p is not None:
                candidate_dirs.append(fc_p.parent)
                if not contract_raw:
                    contract_raw = fc_p.read_text(encoding="utf-8")
                if not contract_dict:
                    loaded = yaml.safe_load(fc_p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        contract_dict = loaded

    for art_ref in report_dict.get("artifacts") or []:
        if not isinstance(art_ref, dict):
            continue
        p = resolve_existing_file(state, art_ref.get("file_path") or art_ref.get("path"))
        if p is not None:
            candidate_dirs.append(p.parent)
            if p.name == "final_canonical_ir.json" and not ir_dict:
                ir_raw = p.read_text(encoding="utf-8")
                ir_dict = json.loads(ir_raw)
            elif p.name == "final_contract.yaml":
                if not contract_raw:
                    contract_raw = p.read_text(encoding="utf-8")
                if not contract_dict:
                    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        contract_dict = loaded

    proj = state.projects.get(run_dto.project_id)
    if proj is not None:
        if not ir_dict and proj.canonical_ir_path:
            p = resolve_existing_file(state, proj.canonical_ir_path)
            if p is not None:
                candidate_dirs.append(p.parent)
                ir_raw = p.read_text(encoding="utf-8")
                ir_dict = json.loads(ir_raw)
        if not contract_dict and proj.contract_path:
            p = resolve_existing_file(state, proj.contract_path)
            if p is not None:
                candidate_dirs.append(p.parent)
                if not contract_raw:
                    contract_raw = p.read_text(encoding="utf-8")
                loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    contract_dict = loaded

    sql_raw = ""
    draft_raw = (
        str(report_dict["blueprint_content"])
        if isinstance(report_dict.get("blueprint_content"), str)
        else ""
    )
    for c_dir in candidate_dirs:
        ir_cand = c_dir / "final_canonical_ir.json"
        if not ir_dict and ir_cand.is_file():
            ir_raw = ir_cand.read_text(encoding="utf-8")
            ir_dict = json.loads(ir_raw)
        fc_cand = c_dir / "final_contract.yaml"
        if fc_cand.is_file():
            if not contract_raw:
                contract_raw = fc_cand.read_text(encoding="utf-8")
            if not contract_dict:
                loaded = yaml.safe_load(fc_cand.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    contract_dict = loaded
        sql_cand = c_dir / "final_four_zone.sql"
        if not sql_raw and sql_cand.is_file():
            sql_raw = sql_cand.read_text(encoding="utf-8")
        if not draft_raw:
            for d_name in ("draft_contract.yaml", "vault_blueprint.yaml"):
                d_cand = c_dir / d_name
                if d_cand.is_file():
                    draft_raw = d_cand.read_text(encoding="utf-8")
                    break

    if not ir_dict and contract_dict:
        ir_obj = lower_contract_to_canonical_ir(contract_dict, validate_schema=False)
        ir_dict = ir_obj.model_dump(mode="json", exclude_none=True)
    if not ir_raw and ir_dict:
        ir_raw = json.dumps(ir_dict, indent=2)
    if not contract_raw and contract_dict:
        contract_raw = yaml.safe_dump(contract_dict, sort_keys=False)
    if not sql_raw:
        sql_raw = extract_four_zone_sql_text(report_dict.get("four_zone_sql"))

    scorecard = dict(report_dict.get("gate_scorecard") or {})
    if not scorecard:
        scorecard = run_dto.gate_scorecard.model_dump(mode="json")

    return {
        "ir_dict": ir_dict,
        "contract_dict": contract_dict,
        "scorecard": scorecard,
        "report_dict": report_dict,
        "contract_raw": contract_raw,
        "ir_raw": ir_raw,
        "sql_raw": sql_raw,
        "draft_raw": draft_raw,
        "candidate_dirs": candidate_dirs,
    }


def resolve_wave_label(report_dict: dict[str, Any], contract_dict: dict[str, Any]) -> str:
    """Resolve display wave label (e.g. W1/W2/W3) from report metadata or lifecycle stage."""
    raw_wave = report_dict.get("wave_id")
    if isinstance(raw_wave, str) and raw_wave.strip():
        return raw_wave.strip()
    wave_reports = report_dict.get("wave_reports")
    if isinstance(wave_reports, list) and wave_reports:
        last = wave_reports[-1]
        if isinstance(last, dict) and last.get("wave_index") is not None:
            idx = int(last["wave_index"])
            return f"W{max(1, idx)}" if idx > 0 else "W1"
    if contract_dict.get("lifecycle_stage") == "MULTI_SOURCE_EXPANSION":
        return "W2"
    return "W1"


def normalize_hub_source_mappings(raw_mappings: list[Any]) -> list[dict[str, Any]]:
    """Enrich hub source mappings with ``source_table`` and ``source_column`` aliases."""
    out: list[dict[str, Any]] = []
    for m in raw_mappings:
        if not isinstance(m, dict):
            continue
        item = dict(m)
        if not item.get("source_table"):
            item["source_table"] = item.get("bronze_table") or item.get("staging_table") or ""
        if not item.get("source_column"):
            bk_cols = [
                str(c) for c in (item.get("bk_source_columns") or []) if str(c) != "bkcc"
            ]
            item["source_column"] = ", ".join(bk_cols)
        out.append(item)
    return out


def normalize_triage_ledger(
    contract_dict: dict[str, Any],
    ir_dict: dict[str, Any],
    default_wave: str,
) -> list[dict[str, Any]]:
    """Normalize ``column_triage_ledger`` entries for View 1/2/CSV while preserving raw keys."""
    uri_by_entity: dict[str, str] = {}
    for coll_key in ("hubs", "links", "reference_tables"):
        for item in contract_dict.get(coll_key) or []:
            if isinstance(item, dict) and item.get("name") and item.get("ontology_concept_uri"):
                uri_by_entity[str(item["name"])] = str(item["ontology_concept_uri"])
    for h in ir_dict.get("hubs") or []:
        if isinstance(h, dict) and h.get("name"):
            hname = str(h["name"])
            uri_val = h.get("ontology_concept_uri") or h.get("business_concept")
            if hname not in uri_by_entity and uri_val:
                uri_by_entity[hname] = str(uri_val)

    aliases_by_entity: dict[str, dict[str, str]] = {}
    soft_dep_by_entity: dict[str, set[str]] = {}
    for sat in contract_dict.get("satellites") or []:
        if not isinstance(sat, dict) or not sat.get("name"):
            continue
        sname = str(sat["name"])
        parent = str(sat.get("parent_entity") or "")
        if sname not in uri_by_entity and parent in uri_by_entity:
            uri_by_entity[sname] = uri_by_entity[parent]
        if isinstance(sat.get("column_aliases"), dict):
            aliases_by_entity[sname] = {
                str(k): str(v) for k, v in sat["column_aliases"].items()
            }
        if isinstance(sat.get("soft_deprecated_columns"), list):
            soft_dep_by_entity[sname] = {str(c) for c in sat["soft_deprecated_columns"]}
    for sat in ir_dict.get("satellites") or []:
        if not isinstance(sat, dict) or not sat.get("name"):
            continue
        sname = str(sat["name"])
        parent = str(sat.get("parent_entity") or "")
        if sname not in uri_by_entity and parent in uri_by_entity:
            uri_by_entity[sname] = uri_by_entity[parent]
        sm = sat.get("source_mapping") or {}
        if isinstance(sm, dict):
            if isinstance(sm.get("column_aliases"), dict):
                aliases_by_entity.setdefault(sname, {}).update(
                    {str(k): str(v) for k, v in sm["column_aliases"].items()}
                )
            if isinstance(sm.get("soft_deprecated_columns"), list):
                soft_dep_by_entity.setdefault(sname, set()).update(
                    str(c) for c in sm["soft_deprecated_columns"]
                )

    bkcc_by_hub = {
        str(h["name"]): str(h.get("bkcc_column_expr") or h.get("bkcc_literal") or "")
        for h in (ir_dict.get("hubs") or [])
        if isinstance(h, dict) and h.get("name")
    }

    normalized: list[dict[str, Any]] = []
    for raw in contract_dict.get("column_triage_ledger") or []:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        src_table = str(entry.get("source_table") or entry.get("bronze_table") or "")
        src_col = str(entry.get("source_column") or entry.get("column_name") or "")
        role = str(entry.get("triage_role") or entry.get("elm_classification") or "")
        target = str(entry.get("target_entity") or entry.get("target_dv_entity") or "")
        wave = str(entry.get("wave") or default_wave)
        graph_a_uri = str(
            entry.get("graph_a_uri")
            or uri_by_entity.get(target)
            or ("quarantined:surrogate" if role == "SYSTEM_SURROGATE_DROPPED" else "")
        )
        staging_expr = str(entry.get("staging_expr") or "")
        if not staging_expr:
            sql_t = str(entry.get("source_sql_type") or "")
            if src_col in soft_dep_by_entity.get(target, set()):
                staging_expr = f"CAST(NULL AS {sql_t or 'VARCHAR'}) AS {src_col}"
            elif src_col in aliases_by_entity.get(target, {}):
                alias = aliases_by_entity[target][src_col]
                staging_expr = f"COALESCE({src_col}, {alias}) AS {src_col}"
            elif role == "SYSTEM_SURROGATE_DROPPED":
                staging_expr = "DROPPED (surrogate)"
            elif role in (
                "BUSINESS_KEY",
                "FOREIGN_KEY_REF",
                "OPTIONAL_FOREIGN_KEY_REF",
                "REFERENCE_CODE",
            ):
                bkcc = bkcc_by_hub.get(target, "")
                staging_expr = f"UPPER(TRIM(CAST({src_col} AS VARCHAR)))" + (
                    f" /* {bkcc} */" if bkcc else ""
                )
            else:
                staging_expr = f"CAST({src_col} AS {sql_t})" if sql_t else src_col

        entry.update(
            {
                "wave": wave,
                "source_table": src_table,
                "source_column": src_col,
                "triage_role": role,
                "graph_a_uri": graph_a_uri,
                "staging_expr": staging_expr,
                "target_entity": target,
            }
        )
        normalized.append(entry)
    return normalized


_PII_TABLE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_]*_pii\b", re.IGNORECASE)
_PII_CLASSIFICATIONS = frozenset({"PII_HIGH", "RESTRICTED_PII"})


def _collect_pii_satellite_names(state: BaseStateContainer, run_id: str) -> set[str]:
    report = state.pipeline_reports.get(run_id)
    if not isinstance(report, dict):
        return set()
    pii_names: set[str] = set()
    for key in ("contract", "canonical_ir"):
        section = report.get(key)
        if not isinstance(section, dict):
            continue
        for sat in section.get("satellites") or []:
            if not isinstance(sat, dict):
                continue
            name = str(sat.get("name") or "").strip().lower()
            sec_cls = str(sat.get("security_classification") or "").strip().upper()
            if name and (name.endswith("_pii") or sec_cls in _PII_CLASSIFICATIONS):
                pii_names.add(name)
    return pii_names


def assert_dv_schema_pii_access(
    state: BaseStateContainer,
    run_id: str,
    *,
    role: RbacRole | str,
    table_name: str | None = None,
    sql: str | None = None,
) -> None:
    """Block non-PII-cleared roles from reading `sat_*_pii` tables or PII columns (`FR-AUTH`)."""
    if "RESTRICTED_PII" in resolve_cleared_tiers(role):
        return
    role_str = str(role.value if hasattr(role, "value") else role)
    pii_sats = _collect_pii_satellite_names(state, run_id)

    if table_name is not None:
        clean_tbl = table_name.split(".")[-1].strip().strip('"').lower()
        if clean_tbl.endswith("_pii") or clean_tbl in pii_sats:
            raise RolePermissionError(
                f"Insufficient RBAC clearance: role '{role_str}' is not authorized "
                f"to access PII satellite '{table_name}'."
            )

    if sql is not None:
        if _PII_TABLE_TOKEN_RE.search(sql):
            raise RolePermissionError(
                f"Insufficient RBAC clearance: role '{role_str}' cannot query PII satellites."
            )
        try:
            stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
        except (sqlglot_errors.ParseError, ValueError):
            stmts = []
        for stmt in stmts:
            for node in stmt.walk():
                if isinstance(node, exp.Table):
                    tname = str(node.name or "").strip().lower()
                    if tname.endswith("_pii") or tname in pii_sats:
                        raise RolePermissionError(
                            f"Insufficient RBAC clearance: role '{role_str}' "
                            f"cannot query PII satellite '{tname}'."
                        )
                elif isinstance(node, exp.Column):
                    cname = str(node.name or "").strip()
                    if cname and is_pii_key(cname):
                        raise RolePermissionError(
                            f"Insufficient RBAC clearance: role '{role_str}' "
                            f"cannot query PII column '{cname}'."
                        )


def redact_dv_schema_rows(
    rows: list[dict[str, Any]],
    *,
    role: RbacRole | str,
) -> list[dict[str, Any]]:
    """Redact PII/MNPI cells in sandbox query rows for callers lacking `RESTRICTED_PII`."""
    if "RESTRICTED_PII" in resolve_cleared_tiers(role):
        return rows
    return [
        {
            col: redact_sample_value(val, key_name=col, clearance_level=role)
            for col, val in r.items()
        }
        for r in rows
    ]


__all__ = [
    "assert_dv_schema_pii_access",
    "extract_four_zone_sql_text",
    "normalize_hub_source_mappings",
    "normalize_triage_ledger",
    "redact_dv_schema_rows",
    "resolve_existing_file",
    "resolve_run_artifact_sources",
    "resolve_wave_label",
]
