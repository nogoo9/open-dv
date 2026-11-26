"""TargetEvalGate (`EVAL-AST`, `EVAL-RUN` vs `ir_kernel`, `EVAL-FMT`) & Defect Reporter.

Strictly Layer 2a: imports opendv.models, duckdb, pyarrow, yaml, jinja2,
and sibling storage modules.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NoReturn

import duckdb
import jinja2
import pyarrow.parquet as pq
import yaml

from opendv.models.diagnostics import EmitterDefectReport
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.ir_kernel import execute_hash_recipe
from opendv.models.project import DbtSqlDialect
from opendv.models.vault_ir import ArrayType, DecimalType, MapType, PortableLogicalType
from opendv.models.vault_ir_certified import (
    CertifiedVaultIR,
    HashRecipeSpec,
    LinkParticipantHashSpec,
)
from opendv.storage._eval_runtime import run_eval_runtime_checks
from opendv.storage.dialects import SQL_TARGET_IDS, get_dialect_profile
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler.ddl_builders import planned_exclusion_sets
from opendv.storage.sql_compiler.dialect_hashing import compile_hash_recipe_sql


class EmitterDefectReporter:
    """Fail-closed defect reporter raising ErrorCode.EMITTER_VERIFICATION_FAILED (exit 50)."""

    @staticmethod
    def raise_emitter_defect(
        target_id: str,
        stage: str,
        rule_id: str,
        message: str,
        snippet: str = "",
    ) -> NoReturn:
        """Raise OpenDVError with EmitterDefectReport and EMITTER_VERIFICATION_FAILED."""
        report = EmitterDefectReport(
            target_id=target_id,
            stage=stage,
            rule_id=rule_id,
            message=message,
            sql_or_artifact_snippet=snippet[:500],
        )
        raise OpenDVError(
            message,
            error_code=ErrorCode.EMITTER_VERIFICATION_FAILED,
            defect_report=report,
        )


def _check_iceberg_type_ids(ftype: Any, seen_ids: set[int]) -> None:
    if not isinstance(ftype, dict):
        return
    tname = ftype.get("type")
    if tname == "list":
        eid = int(ftype["element-id"])
        if eid in seen_ids:
            raise ValueError(f"Duplicate Iceberg list element-id={eid}")
        seen_ids.add(eid)
        _check_iceberg_type_ids(ftype.get("element"), seen_ids)
    elif tname == "map":
        kid, vid = int(ftype["key-id"]), int(ftype["value-id"])
        if kid in seen_ids or vid in seen_ids or kid == vid:
            raise ValueError(f"Duplicate Iceberg map key/value id={kid}/{vid}")
        seen_ids.update((kid, vid))
        _check_iceberg_type_ids(ftype.get("key"), seen_ids)
        _check_iceberg_type_ids(ftype.get("value"), seen_ids)
    elif tname == "struct":
        _check_iceberg_fields_monotonic(ftype.get("fields", []), seen_ids, [0])


def _check_iceberg_fields_monotonic(
    fields: list[dict[str, Any]],
    seen_ids: set[int],
    prev_top_id: list[int],
) -> None:
    for f in fields:
        fid = int(f["id"])
        if fid <= prev_top_id[0] or fid in seen_ids:
            raise ValueError(f"Non-monotonic or duplicate Iceberg field_id={fid}")
        seen_ids.add(fid)
        prev_top_id[0] = fid
        _check_iceberg_type_ids(f.get("type"), seen_ids)


class TargetEvalGate:
    """Deterministic EVAL-AST, EVAL-RUN (non-circular vs ir_kernel), and EVAL-FMT gate."""

    def __init__(self) -> None:
        self._ast_verifier = SqlAstVerifier()

    def verify_sql_ast(
        self,
        target_id: str,
        sql: str,
        ir: CertifiedVaultIR | None = None,
        *,
        raise_on_error: bool = True,
    ) -> list[str]:
        """Run EVAL-AST-01 & Alg A.6 checks; raise EMITTER_VERIFICATION_FAILED on failure."""
        diags = self._ast_verifier.verify_sql(sql, target_id, ir=ir)
        msgs = [f"[{d.rule_id}] {d.entity_name}: {d.message}" for d in diags]
        if diags and raise_on_error:
            EmitterDefectReporter.raise_emitter_defect(
                target_id, "EVAL-AST-01", diags[0].rule_id, msgs[0], snippet=sql[:300]
            )
        return msgs

    def verify_cross_dialect_vectors(self, *, raise_on_error: bool = True) -> list[str]:
        """EVAL-AST-02..03: verify Alg A.5 AST generation across 8 SQL dialects + DuckDB."""
        recipe = HashRecipeSpec(
            target_col="hk_vec",
            recipe_kind="HUB_HK",
            bkcc_literal="IBOR",
            source_columns=["bk_str", "bk_bool", "bk_date", "bk_ts", "bk_dec"],
            sort_alphabetically=False,
            key_columns=["bk_str", "bk_bool", "bk_date", "bk_ts", "bk_dec"],
        )
        col_types: dict[str, PortableLogicalType] = {
            "bk_str": "UTF8_STRING",
            "bk_bool": "BOOLEAN",
            "bk_date": "DATE32",
            "bk_ts": "TIMESTAMP_UTC_US",
            "bk_dec": DecimalType(precision=18, scale=4),
        }
        errors: list[str] = []
        for tid in SQL_TARGET_IDS:
            prof = get_dialect_profile(tid)
            dialect = prof.sqlglot_dialect or "duckdb"
            expr_sql = compile_hash_recipe_sql(
                recipe, dialect, key_tier="TIER_3_LOCAL_SCOPED", col_types=col_types
            )
            diags = self._ast_verifier.verify_sql(f"SELECT {expr_sql} AS hk_vec FROM src_vec", tid)
            if diags:
                errors.append(f"{tid}: {diags[0].message}")

        conn = duckdb.connect(
            ":memory:", config={"enable_external_access": False, "lock_configuration": True}
        )
        try:
            conn.execute(
                "CREATE TABLE src_vec (bk_str VARCHAR, bk_bool BOOLEAN, bk_date DATE, "
                "bk_ts TIMESTAMPTZ, bk_dec DECIMAL(18,4))"
            )
            conn.execute(
                "INSERT INTO src_vec VALUES "
                "('0000909100', TRUE, DATE '2026-10-20', "
                "TIMESTAMPTZ '2026-10-20 18:00:00+00', 120.5000), "
                "('Nordisk Suverän Fond', NULL, DATE '2026-10-21', "
                "TIMESTAMPTZ '2026-10-21 00:00:00+00', 0.0000), "
                "('line1\r\nline2', FALSE, NULL, NULL, 100.0000)"
            )
            duck_expr = compile_hash_recipe_sql(
                recipe, "duckdb", key_tier="TIER_3_LOCAL_SCOPED", col_types=col_types
            )
            rows = conn.execute(
                f"SELECT bk_str, bk_bool, bk_date, "
                f"STRFTIME(bk_ts AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ'), "
                f"bk_dec, LOWER(HEX({duck_expr})) FROM src_vec"
            ).fetchall()
            for r in rows:
                py_row = {
                    "bk_str": r[0],
                    "bk_bool": r[1],
                    "bk_date": r[2],
                    "bk_ts": r[3],
                    "bk_dec": r[4],
                }
                expected = execute_hash_recipe(
                    recipe, py_row, key_tier="TIER_3_LOCAL_SCOPED", column_types=col_types
                )
                actual = str(r[5]).lower()
                if actual != expected:
                    errors.append(
                        f"EVAL-AST-03 DuckDB mismatch for {py_row}: {actual} != {expected}"
                    )

            # Collection Alg A.5 parity (ArrayType, MapType, nested) across 8 dialects + DuckDB
            coll_recipe = HashRecipeSpec(
                target_col="hash_diff",
                recipe_kind="SAT_HASH_DIFF",
                source_columns=["tags_arr", "attrs_map", "nested_arr", "nested_map"],
                sort_alphabetically=True,
            )
            coll_types: dict[str, PortableLogicalType] = {
                "tags_arr": ArrayType(element_type="UTF8_STRING"),
                "attrs_map": MapType(key_type="UTF8_STRING", value_type="INT64"),
                "nested_arr": ArrayType(element_type=ArrayType(element_type="UTF8_STRING")),
                "nested_map": MapType(
                    key_type="UTF8_STRING", value_type=ArrayType(element_type="INT32")
                ),
            }
            for tid in SQL_TARGET_IDS:
                d = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
                c_sql = compile_hash_recipe_sql(coll_recipe, d, col_types=coll_types)
                c_diags = self._ast_verifier.verify_sql(
                    f"SELECT {c_sql} AS hash_diff FROM src_coll", tid
                )
                if c_diags:
                    errors.append(f"{tid} collection: {c_diags[0].message}")
            conn.execute(
                "CREATE TABLE src_coll (tags_arr VARCHAR[], attrs_map MAP(VARCHAR, BIGINT), "
                "nested_arr VARCHAR[][], nested_map MAP(VARCHAR, INTEGER[]))"
            )
            conn.execute(
                "INSERT INTO src_coll VALUES "
                "(['t1', 't2'], MAP {'b': 2, 'a': 1}, [['a'], []], MAP {'m': [1, 2]}), "
                "([], MAP {}, [], MAP {}), (NULL, NULL, NULL, NULL)"
            )
            duck_coll = compile_hash_recipe_sql(coll_recipe, "duckdb", col_types=coll_types)
            coll_q = (
                "SELECT tags_arr, attrs_map, nested_arr, nested_map, "
                f"LOWER(HEX({duck_coll})) FROM src_coll"
            )
            for cr in conn.execute(coll_q).fetchall():
                py_c = {
                    "tags_arr": cr[0],
                    "attrs_map": cr[1],
                    "nested_arr": cr[2],
                    "nested_map": cr[3],
                }
                exp_c = execute_hash_recipe(coll_recipe, py_c, column_types=coll_types)
                if str(cr[4]).lower() != exp_c:
                    errors.append(f"EVAL-AST-03 collection mismatch for {py_c}: {cr[4]} != {exp_c}")

            # Participant-aware LINK_HK parity (mixed-case, degenerate, partial-NULL -1 vs -2)
            lnk_recipe = HashRecipeSpec(
                target_col="hk_lnk_vec",
                recipe_kind="LINK_HK",
                source_columns=["acct_id", "issuer_cik", "sub_code", "seq_tag"],
                sort_alphabetically=False,
                key_columns=["acct_id", "issuer_cik", "sub_code", "seq_tag"],
                participant_specs=[
                    LinkParticipantHashSpec(
                        hub_name="hub_acct",
                        role_name="ACCT",
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_literal="GLOBAL",
                        source_columns=["acct_id"],
                        is_nullable=False,
                    ),
                    LinkParticipantHashSpec(
                        hub_name="hub_issuer",
                        role_name="ISSUER",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_literal="IBOR",
                        bkcc_col="bkcc_src",
                        source_columns=["issuer_cik", "sub_code"],
                        is_nullable=True,
                    ),
                    LinkParticipantHashSpec(
                        role_name="DEG_SEQ",
                        source_columns=["seq_tag"],
                        is_degenerate=True,
                    ),
                ],
            )
            lnk_types: dict[str, PortableLogicalType] = {
                "acct_id": "UTF8_STRING",
                "issuer_cik": "UTF8_STRING",
                "sub_code": "UTF8_STRING",
                "seq_tag": "UTF8_STRING",
                "bkcc_src": "UTF8_STRING",
            }
            for tid in SQL_TARGET_IDS:
                d = get_dialect_profile(tid).sqlglot_dialect or "duckdb"
                l_sql = compile_hash_recipe_sql(lnk_recipe, d, col_types=lnk_types)
                l_diags = self._ast_verifier.verify_sql(
                    f"SELECT {l_sql} AS hk_lnk_vec FROM src_lnk_vec", tid
                )
                if l_diags:
                    errors.append(f"{tid} link_vec: {l_diags[0].message}")
            conn.execute(
                "CREATE TABLE src_lnk_vec (acct_id VARCHAR, issuer_cik VARCHAR, "
                "sub_code VARCHAR, seq_tag VARCHAR, bkcc_src VARCHAR)"
            )
            conn.execute(
                "INSERT INTO src_lnk_vec VALUES "
                "('AbC-1', 'xYz', 'sUb', 'MixedCase', 'eu_cust'), "
                "('AbC-2', 'xYz', NULL, 'Case_2', NULL), "
                "('AbC-3', NULL, NULL, NULL, 'eu_cust'), "
                "('-1', NULL, NULL, 'KeepDeg', 'eu_cust'), "
                "('-2', NULL, NULL, 'KeepDeg', 'eu_cust'), "
                "(NULL, NULL, NULL, 'OnlyDeg', NULL)"
            )
            duck_lnk = compile_hash_recipe_sql(lnk_recipe, "duckdb", col_types=lnk_types)
            lnk_q = (
                "SELECT acct_id, issuer_cik, sub_code, seq_tag, bkcc_src, "
                f"LOWER(HEX({duck_lnk})) FROM src_lnk_vec"
            )
            for lr in conn.execute(lnk_q).fetchall():
                py_l = {
                    "acct_id": lr[0],
                    "issuer_cik": lr[1],
                    "sub_code": lr[2],
                    "seq_tag": lr[3],
                    "bkcc_src": lr[4],
                }
                exp_l = execute_hash_recipe(lnk_recipe, py_l, column_types=lnk_types)
                if str(lr[5]).lower() != exp_l:
                    errors.append(f"EVAL-AST-03 link_vec mismatch for {py_l}: {lr[5]} != {exp_l}")
        finally:
            conn.close()

        if errors and raise_on_error:
            EmitterDefectReporter.raise_emitter_defect(
                "duckdb_local", "EVAL-AST-03", "EVAL-AST-03", errors[0], snippet=errors[0]
            )
        return errors

    def verify_format_bundle(
        self,
        target_id: Literal["iceberg", "parquet", "dbt"],
        output_dir: Path,
        ir: CertifiedVaultIR,
        *,
        dbt_sql_dialect: DbtSqlDialect = "duckdb",
        raise_on_error: bool = True,
        run_dbt_cli_if_present: bool = False,
    ) -> list[str]:
        """Verify Iceberg (EVAL-FMT-01), Parquet (EVAL-FMT-02), or dbt (EVAL-FMT-03) bundles."""
        planned_hubs, _, _ = planned_exclusion_sets(ir)
        errors: list[str] = []

        if target_id == "iceberg":
            ops_file = output_dir / f"schema_ops/wave_{ir.wave_number}_update_schema.json"
            if not ops_file.exists():
                errors.append(f"Missing {ops_file.name}")
            else:
                json.loads(ops_file.read_text(encoding="utf-8"))
            meta_files = sorted(output_dir.rglob(f"v{ir.wave_number}.metadata.json"))
            if not meta_files:
                errors.append("No Iceberg vN.metadata.json files generated")
            for mf in meta_files:
                rel = mf.relative_to(output_dir).as_posix()
                for ph in planned_hubs:
                    if f"/{ph}/" in f"/{rel}":
                        errors.append(f"Planned hub {ph} emitted into Iceberg path {rel}")
                data = json.loads(mf.read_text(encoding="utf-8"))
                if data.get("format-version") != 2:
                    errors.append(f"{rel} format-version != 2")
                for schema in data.get("schemas", []):
                    try:
                        _check_iceberg_fields_monotonic(schema.get("fields", []), set(), [0])
                    except ValueError as exc:
                        errors.append(f"{rel}: {exc}")
            stage = "EVAL-FMT-01"

        elif target_id == "parquet":
            sm_path = output_dir / "schema_manifest.json"
            if not sm_path.exists():
                errors.append("Missing schema_manifest.json")
            else:
                json.loads(sm_path.read_text(encoding="utf-8"))
            pq_files = sorted(output_dir.rglob("*.zstd.parquet"))
            if not pq_files:
                errors.append("No .zstd.parquet files generated")
            for pf in pq_files:
                rel = pf.relative_to(output_dir).as_posix()
                for ph in planned_hubs:
                    if f"/{ph}/" in f"/{rel}":
                        errors.append(f"Planned hub {ph} emitted into Parquet path {rel}")
                meta = pq.ParquetFile(pf).metadata
                if (
                    meta.num_row_groups > 0
                    and str(meta.row_group(0).column(0).compression).upper() != "ZSTD"
                ):
                    errors.append(f"{rel} compression != ZSTD")
                tbl = pq.read_table(pf)
                if rel.startswith("raw_vault/") and tbl.num_rows < 1:
                    errors.append(f"{rel} missing Zero/Ghost seed rows")
            stage = "EVAL-FMT-02"

        else:
            pkg_path = output_dir / "packages.yml"
            if not pkg_path.exists():
                errors.append("Missing packages.yml")
            else:
                pkgs = yaml.safe_load(pkg_path.read_text(encoding="utf-8")) or {}
                adv = [
                    p
                    for p in pkgs.get("packages", [])
                    if p.get("package") == "datavault-uk/automate_dv"
                ]
                if not adv or "0.11" not in str(adv[0].get("version", "")):
                    errors.append("packages.yml does not pin datavault-uk/automate_dv 0.11.x")
            for yml_f in sorted(output_dir.rglob("*.yml")):
                try:
                    yaml.safe_load(yml_f.read_text(encoding="utf-8"))
                except yaml.YAMLError as exc:
                    errors.append(f"Invalid YAML {yml_f.name}: {exc}")
            env = jinja2.Environment()
            for sql_f in sorted(output_dir.rglob("*.sql")):
                rel = sql_f.relative_to(output_dir).as_posix()
                for ph in planned_hubs:
                    if ph in sql_f.stem:
                        errors.append(f"Planned hub {ph} emitted in dbt model {rel}")
                raw_sql = sql_f.read_text(encoding="utf-8")
                try:
                    env.parse(raw_sql)
                except jinja2.TemplateSyntaxError as exc:
                    errors.append(f"Invalid Jinja in {rel}: {exc}")
                if "models/Z1_staging/" in rel:
                    stripped = "\n".join(
                        ln for ln in raw_sql.splitlines() if "{{ config(" not in ln
                    )
                    stripped = stripped.replace("{{ source('bronze', '", "bronze.").replace(
                        "') }}", ""
                    )
                    diags = self._ast_verifier.verify_sql(stripped, dbt_sql_dialect, ir=ir)
                    errors.extend(f"{rel}: {d.message}" for d in diags)
            if run_dbt_cli_if_present and shutil.which("dbt"):
                proc = subprocess.run(
                    ["dbt", "parse"], cwd=output_dir, capture_output=True, text=True, check=False
                )
                if proc.returncode != 0:
                    errors.append(f"dbt parse failed: {proc.stderr or proc.stdout}")
            stage = "EVAL-FMT-03"

        if errors and raise_on_error:
            EmitterDefectReporter.raise_emitter_defect(
                target_id, stage, stage, errors[0], snippet="\n".join(errors[:5])
            )
        return errors

    def verify_duckdb_runtime(
        self,
        ir: CertifiedVaultIR,
        ddl_sql: str,
        dml_sql: str,
        *,
        migration_sqls: Sequence[str] = (),
        source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        physical_aliases: Mapping[str, str] | None = None,
        raise_on_error: bool = True,
    ) -> list[str]:
        """EVAL-RUN-01..04: non-circular DuckDB runtime verification vs pure-Python ir_kernel."""
        errors = run_eval_runtime_checks(
            ir,
            ddl_sql,
            dml_sql,
            migration_sqls=migration_sqls,
            source_rows_by_table=source_rows_by_table,
            physical_aliases=physical_aliases,
        )
        if errors and raise_on_error:
            EmitterDefectReporter.raise_emitter_defect(
                "duckdb_local", "EVAL-RUN", "EVAL-RUN", errors[0], snippet="\n".join(errors[:5])
            )
        return errors


__all__ = ["EmitterDefectReporter", "TargetEvalGate"]
