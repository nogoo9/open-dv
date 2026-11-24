"""dbt + AutomateDV 0.11.x bundle emitter (`EVAL-FMT-03`).

Strictly Layer 2a: imports only opendv.models, pyyaml, dialects, sql_compiler, stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from opendv.models.project import DbtSqlDialect, EvolutionLockSpec, TargetManifest
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.dialects import assign_physical_aliases, get_dialect_profile
from opendv.storage.sql_compiler.ddl_builders import (
    build_mart_view_ddl,
    build_staging_view_ddl,
    planned_exclusion_sets,
    resolve_staging_hd_col_map,
)


@dataclass(frozen=True, slots=True)
class DbtBundle:
    """Generated dbt + AutomateDV 0.11.x files and TargetManifest."""

    dbt_sql_dialect: DbtSqlDialect
    files: dict[str, str]
    z1_plain_sqls: dict[str, str]
    manifest: TargetManifest


class DbtEmitter:
    """Emit dbt project pinning datavault-uk/automate_dv 0.11.x with pre-hashed Alg A.5 Z1 SQL."""

    def emit(
        self,
        ir: CertifiedVaultIR,
        *,
        dbt_sql_dialect: DbtSqlDialect = "duckdb",
        locks: EvolutionLockSpec | None = None,
        output_dir: Path | None = None,
    ) -> DbtBundle:
        """Emit dbt_project.yml, packages.yml, models/<zone>/*.sql, and schema.yml."""
        profile = get_dialect_profile(dbt_sql_dialect)
        aliases = assign_physical_aliases(ir, "dbt", locks=locks)
        planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)
        hd_col_map = resolve_staging_hd_col_map(ir, max_ident_bytes=profile.max_ident_bytes)
        files: dict[str, str] = {}
        z1_plain_sqls: dict[str, str] = {}

        proj_slug = ir.project_id.replace("-", "_").lower()
        dbt_project = {
            "name": f"opendv_{proj_slug}",
            "version": f"{ir.wave_number}.0.0",
            "config-version": 2,
            "profile": f"opendv_{dbt_sql_dialect}",
            "model-paths": ["models"],
            "models": {
                f"opendv_{proj_slug}": {
                    "Z1_staging": {"+materialized": "view"},
                    "Z2_raw_vault": {"+materialized": "incremental"},
                    "Z3_business_vault": {"+materialized": "table"},
                    "Z4_information_mart": {"+materialized": "view"},
                }
            },
        }
        files["dbt_project.yml"] = yaml.safe_dump(dbt_project, sort_keys=False)

        packages = {
            "packages": [
                {"package": "datavault-uk/automate_dv", "version": [">=0.11.0", "<0.12.0"]}
            ]
        }
        files["packages.yml"] = yaml.safe_dump(packages, sort_keys=False)

        # Z0 sources schema
        z0_sources = {
            "version": 2,
            "sources": [
                {
                    "name": "bronze",
                    "tables": [
                        {
                            "name": b.table_name,
                            "columns": [{"name": c.name} for c in b.columns],
                        }
                        for b in ir.bronze_tables
                    ],
                }
            ],
        }
        files["models/Z0_bronze/schema.yml"] = yaml.safe_dump(z0_sources, sort_keys=False)

        # Z1 Staging (pre-hashed Alg A.5 SQL replacing automate_dv hash macros)
        z1_models: list[dict[str, Any]] = []
        for stg in ir.staging_views:
            ddl_list = build_staging_view_ddl(stg, ir, profile, aliases, hd_col_map)
            if not ddl_list:
                continue
            create_stmt = ddl_list[-1]
            z1_plain_sqls[stg.view_name] = create_stmt
            select_body = (
                create_stmt.split(" AS\n", 1)[1] if " AS\n" in create_stmt else create_stmt
            )
            bronze_tbl = next(
                (
                    b.table_name
                    for b in ir.bronze_tables
                    if b.source_table_id == stg.source_table_id
                ),
                stg.view_name,
            )
            select_jinja = select_body.replace(
                f"FROM bronze.{aliases.get(bronze_tbl, bronze_tbl)} AS src",
                f"FROM {{{{ source('bronze', '{bronze_tbl}') }}}} AS src",
            )
            files[f"models/Z1_staging/{stg.view_name}.sql"] = (
                "{{ config(materialized='view') }}\n\n" + select_jinja + "\n"
            )
            z1_models.append(
                {
                    "name": stg.view_name,
                    "columns": [{"name": f.name} for f in stg.projected_fields],
                }
            )
        files["models/Z1_staging/schema.yml"] = yaml.safe_dump(
            {"version": 2, "models": z1_models}, sort_keys=False
        )

        # Z2 Raw Vault & Z3 Business Vault AutomateDV models
        stg_by_tbl = {s.source_table_id: s.view_name for s in ir.staging_views}
        z2_models: list[dict[str, Any]] = []
        for h in ir.hubs:
            if h.hub_name in planned_hubs:
                continue
            hk = h.columns[0].name
            bks = [bk.col_name for bk in h.business_keys]
            src_models = [
                stg_by_tbl[sb.table_id] for sb in h.source_bindings if sb.table_id in stg_by_tbl
            ]
            if not src_models:
                src_models = [next(iter(stg_by_tbl.values()), h.hub_name)]
            files[f"models/Z2_raw_vault/{h.hub_name}.sql"] = (
                "{{ config(materialized='incremental') }}\n"
                f"{{{{ automate_dv.hub(src_pk='{hk}', src_nk={bks!r}, src_ldts='load_dts', "
                f"src_source='rec_src', source_model={src_models!r}) }}}}\n"
            )
            z2_models.append({"name": h.hub_name, "columns": [{"name": c.name} for c in h.columns]})

        for lk in ir.links:
            if lk.link_name in excluded_links:
                continue
            hk = lk.columns[0].name
            fks = [p.fk_col_name for p in lk.participants]
            src_models = [
                stg_by_tbl[sb.table_id] for sb in lk.source_bindings if sb.table_id in stg_by_tbl
            ]
            src_model = src_models[0] if src_models else lk.link_name
            macro = "t_link" if lk.link_kind == "TRANSACTIONAL" else "link"
            zone_dir = "Z3_business_vault" if lk.zone_id == "Z3" else "Z2_raw_vault"
            files[f"models/{zone_dir}/{lk.link_name}.sql"] = (
                "{{ config(materialized='incremental') }}\n"
                f"{{{{ automate_dv.{macro}(src_pk='{hk}', src_fk={fks!r}, src_ldts='load_dts', "
                f"src_source='rec_src', source_model='{src_model}') }}}}\n"
            )
            if zone_dir == "Z2_raw_vault":
                z2_models.append(
                    {"name": lk.link_name, "columns": [{"name": c.name} for c in lk.columns]}
                )

        for sat in ir.satellites:
            if sat.sat_name in excluded_sats:
                continue
            parent_hk = sat.columns[0].name
            src_model = stg_by_tbl.get(sat.source_table_id, sat.sat_name)
            payload = [a.name for a in sat.attributes]
            zone_dir = "Z3_business_vault" if sat.zone_id == "Z3" else "Z2_raw_vault"
            src_hd = hd_col_map.get((src_model, sat.sat_name), "hash_diff")
            if sat.sat_kind == "MAS":
                macro_call = (
                    f"automate_dv.ma_sat(src_pk='{parent_hk}', "
                    f"src_cdk={sat.child_dependent_keys!r}, "
                    f"src_hashdiff={src_hd!r}, src_payload={payload!r}, "
                    f"src_ldts='load_dts', src_source='rec_src', source_model='{src_model}')"
                )
            elif sat.sat_kind == "ESAT":
                macro_call = (
                    f"automate_dv.eff_sat(src_pk='{parent_hk}', src_dfk={sat.driving_keys!r}, "
                    "src_sfk=[], src_start_date='load_dts', src_end_date='load_dts', "
                    f"src_eff='load_dts', src_ldts='load_dts', src_source='rec_src', "
                    f"source_model='{src_model}')"
                )
            else:
                macro_call = (
                    f"automate_dv.sat(src_pk='{parent_hk}', src_hashdiff={src_hd!r}, "
                    f"src_payload={payload!r}, src_ldts='load_dts', src_source='rec_src', "
                    f"source_model='{src_model}')"
                )
            files[f"models/{zone_dir}/{sat.sat_name}.sql"] = (
                "{{ config(materialized='incremental') }}\n{{ " + macro_call + " }}\n"
            )
            if zone_dir == "Z2_raw_vault":
                z2_models.append(
                    {"name": sat.sat_name, "columns": [{"name": c.name} for c in sat.columns]}
                )

        files["models/Z2_raw_vault/schema.yml"] = yaml.safe_dump(
            {"version": 2, "models": z2_models}, sort_keys=False
        )

        # Z3 PITs / Bridges
        z3_models: list[dict[str, Any]] = []
        for pb in ir.pit_bridges:
            if pb.root_hub in planned_hubs:
                continue
            macro = "pit" if pb.object_kind == "PIT" else "bridge"
            hk_n = f"hk_{pb.root_hub.removeprefix('hub_')}"
            files[f"models/Z3_business_vault/{pb.object_name}.sql"] = (
                "{{ config(materialized='table') }}\n"
                f"{{{{ automate_dv.{macro}(source_model='{pb.root_hub}', src_pk='{hk_n}') }}}}\n"
            )
            z3_models.append({"name": pb.object_name})
        files["models/Z3_business_vault/schema.yml"] = yaml.safe_dump(
            {"version": 2, "models": z3_models}, sort_keys=False
        )

        # Z4 Information Marts
        z4_models: list[dict[str, Any]] = []
        for mart in ir.information_marts:
            if mart.root_entity in planned_hubs:
                continue
            ddl_list = build_mart_view_ddl(mart, ir, profile, aliases)
            if not ddl_list:
                continue
            obj_name = mart.view_name.split(".")[-1]
            select_body = (
                ddl_list[-1].split(" AS\n", 1)[1] if " AS\n" in ddl_list[-1] else ddl_list[-1]
            )
            files[f"models/Z4_information_mart/{obj_name}.sql"] = (
                "{{ config(materialized='view') }}\n\n" + select_body + "\n"
            )
            z4_models.append(
                {"name": obj_name, "columns": [{"name": c.name} for c in mart.columns]}
            )
        files["models/Z4_information_mart/schema.yml"] = yaml.safe_dump(
            {"version": 2, "models": z4_models}, sort_keys=False
        )

        manifest = TargetManifest(
            target_id="dbt",
            last_compiled_wave=ir.wave_number,
            last_materialized_wave=None,
            verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
            certification_status="CERTIFIED",
            physical_aliases=aliases,
            eval_receipts=["EVAL-FMT-03"],
            generated_files=sorted([*files.keys(), "target_manifest.json"]),
        )
        files["target_manifest.json"] = manifest.model_dump_json(indent=2) + "\n"

        if output_dir is not None:
            for rel, content in sorted(files.items()):
                dest = output_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", newline="\n")

        return DbtBundle(
            dbt_sql_dialect=dbt_sql_dialect,
            files=files,
            z1_plain_sqls=z1_plain_sqls,
            manifest=manifest,
        )


__all__ = ["DbtBundle", "DbtEmitter"]
