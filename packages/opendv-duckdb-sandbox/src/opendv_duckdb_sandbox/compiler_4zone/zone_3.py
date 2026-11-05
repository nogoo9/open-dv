"""Zone 3: Business Vault PIT Tables and Logarithmic Snapshot Calendar."""

from __future__ import annotations

from opendv_core.constants import GHOST_DTS, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.models.canonical_ir import CanonicalIR, IRHub, IRPitTable, IRSatellite
from opendv_duckdb_sandbox.compiler_4zone.hashing import _validate_ident
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_pit_tables,
    _satellite_schema,
)


def _build_generic_pit_view_sql(
    pit: IRPitTable,
    hub_by_name: dict[str, IRHub],
    sat_by_name: dict[str, IRSatellite],
) -> str:
    """Build `CREATE OR REPLACE VIEW information_mart.vw_<pit_name>_current` SQL."""
    pit_tbl = _validate_ident(pit.name)
    parent_hub = hub_by_name[pit.parent_entity]
    hub_tbl = _validate_ident(parent_hub.name)
    hub_hk = _validate_ident(parent_hub.hk_column.name)
    as_of_col = _validate_ident(pit.as_of_dts_column)

    select_cols = [
        f"    pit.{as_of_col}",
        f"    h.{hub_hk}",
        *[f"    h.{_validate_ident(bk.name)}" for bk in parent_hub.business_keys],
    ]
    join_clauses = [
        f"FROM business_vault.{pit_tbl} pit",
        f"INNER JOIN raw_vault.{hub_tbl} h ON h.{hub_hk} = pit.{hub_hk}",
    ]
    seen_col_names = {as_of_col, hub_hk, *(bk.name for bk in parent_hub.business_keys)}

    for idx_s, sat_name in enumerate(pit.tracked_satellites):
        sat = sat_by_name[sat_name]
        sat_tbl = _validate_ident(sat.name)
        sat_schema = _satellite_schema(sat)
        sat_parent_hk = _validate_ident(sat.parent_hk_column.name)
        alias = f"s{idx_s}"
        join_clauses.append(
            f"INNER JOIN {sat_schema}.{sat_tbl} {alias}\n"
            f"    ON {alias}.{sat_parent_hk} = pit.{sat_tbl}_hk\n"
            f"   AND {alias}.load_dts = pit.{sat_tbl}_load_dts"
        )
        for col in sat.payload_columns:
            c_ident = _validate_ident(col.name)
            out_alias = f"{sat_tbl}__{c_ident}" if c_ident in seen_col_names else c_ident
            seen_col_names.add(out_alias)
            select_cols.append(f"    {alias}.{c_ident} AS {out_alias}")

    select_body = ",\n".join(select_cols)
    join_body = "\n".join(join_clauses)
    return (
        f"CREATE OR REPLACE VIEW information_mart.vw_{pit_tbl}_current AS\n"
        f"SELECT\n"
        f"{select_body}\n"
        f"{join_body};"
    )


def _compile_generic_zone_3_business_vault_sql(ir_obj: CanonicalIR) -> str:
    """Compile Zone 3 Business Vault PIT tables and logarithmic snapshot calendar."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    sat_by_name = {s.name: s for s in ir_obj.satellites}
    effective_pits = _effective_pit_tables(ir_obj)

    parts = [
        "-- =============================================================================",
        "-- ZONE 3: BUSINESS VAULT & POINT-IN-TIME (PIT) HELPER TABLES",
        "-- =============================================================================",
        "CREATE TABLE IF NOT EXISTS business_vault.snap_logarithmic_calendar (\n"
        "    as_of_dts TIMESTAMP NOT NULL PRIMARY KEY,\n"
        "    window_tier VARCHAR NOT NULL\n"
        ");",
        "INSERT INTO business_vault.snap_logarithmic_calendar\n"
        "SELECT * FROM (\n"
        "    VALUES\n"
        "        (TIMESTAMP '2026-10-01 12:00:00', 'DAILY_ROLLING_30D'),\n"
        "        (TIMESTAMP '2026-10-15 12:00:00', 'DAILY_ROLLING_30D'),\n"
        "        (TIMESTAMP '2026-11-15 23:59:59', 'QUARTER_END_PERMANENT')\n"
        ") v(as_of_dts, window_tier)\n"
        "WHERE NOT EXISTS (\n"
        "    SELECT 1 FROM business_vault.snap_logarithmic_calendar c\n"
        "    WHERE c.as_of_dts = v.as_of_dts\n"
        ");",
        "CREATE OR REPLACE VIEW information_mart.snap_logarithmic_calendar AS\n"
        "SELECT * FROM business_vault.snap_logarithmic_calendar;",
    ]

    for pit in effective_pits:
        if not pit.tracked_satellites:
            continue
        pit_tbl = _validate_ident(pit.name)
        pit_hk = f"hk_{pit_tbl}"
        parent_hub = hub_by_name[pit.parent_entity]
        hub_tbl = _validate_ident(parent_hub.name)
        hub_hk = _validate_ident(parent_hub.hk_column.name)
        as_of_col = _validate_ident(pit.as_of_dts_column)

        ddl_cols = [
            f"    {pit_hk} CHAR(64) NOT NULL PRIMARY KEY,",
            f"    {hub_hk} CHAR(64) NOT NULL,",
            f"    {as_of_col} TIMESTAMP NOT NULL,",
        ]
        ptr_col_names: list[str] = []
        subquery_selects: list[str] = [
            f"        LOWER(SHA256(\n"
            f"            '64:' || h.{hub_hk} || '||'\n"
            f"            || CAST(OCTET_LENGTH(ENCODE(CAST(cal.as_of_dts AS VARCHAR)))"
            f" AS VARCHAR)\n"
            f"            || ':' || CAST(cal.as_of_dts AS VARCHAR)\n"
            f"        )) AS {pit_hk}",
            f"        h.{hub_hk}",
            f"        cal.as_of_dts AS {as_of_col}",
        ]
        asof_joins: list[str] = []

        for idx_s, sat_name in enumerate(pit.tracked_satellites):
            sat = sat_by_name[sat_name]
            sat_tbl = _validate_ident(sat.name)
            sat_schema = _satellite_schema(sat)
            sat_parent_hk = _validate_ident(sat.parent_hk_column.name)
            alias = f"s{idx_s}"
            hk_ptr = f"{sat_tbl}_hk"
            dts_ptr = f"{sat_tbl}_load_dts"
            ptr_col_names.extend([hk_ptr, dts_ptr])
            comma = "," if idx_s < len(pit.tracked_satellites) - 1 else ""
            ddl_cols.append(f"    {hk_ptr} CHAR(64) NOT NULL,")
            ddl_cols.append(f"    {dts_ptr} TIMESTAMP NOT NULL{comma}")
            subquery_selects.append(
                f"        COALESCE({alias}.{sat_parent_hk}, '{ZERO_KEY_UNKNOWN}') AS {hk_ptr}"
            )
            subquery_selects.append(
                f"        COALESCE({alias}.load_dts, TIMESTAMP '{GHOST_DTS}') AS {dts_ptr}"
            )
            asof_joins.append(
                f"    ASOF LEFT JOIN {sat_schema}.{sat_tbl} {alias}\n"
                f"        ON {alias}.{sat_parent_hk} = h.{hub_hk}\n"
                f"       AND {alias}.load_dts <= cal.as_of_dts"
            )

        all_cols = [pit_hk, hub_hk, as_of_col, *ptr_col_names]
        cols_csv = ", ".join(all_cols)
        src_cols_csv = ", ".join(f"src.{c}" for c in all_cols)
        subquery_sel_str = ",\n".join(subquery_selects)
        asof_join_str = "\n".join(asof_joins)

        parts.append(
            f"CREATE TABLE IF NOT EXISTS business_vault.{pit_tbl} (\n"
            + "\n".join(ddl_cols)
            + "\n);"
        )
        parts.append(
            f"INSERT INTO business_vault.{pit_tbl} ({cols_csv})\n"
            f"SELECT {src_cols_csv}\n"
            f"FROM (\n"
            f"    SELECT\n"
            f"{subquery_sel_str}\n"
            f"    FROM raw_vault.{hub_tbl} h\n"
            f"    CROSS JOIN business_vault.snap_logarithmic_calendar cal\n"
            f"{asof_join_str}\n"
            f"    WHERE h.{hub_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f") src\n"
            f"WHERE NOT EXISTS (\n"
            f"    SELECT 1 FROM business_vault.{pit_tbl} p\n"
            f"    WHERE p.{pit_hk} = src.{pit_hk}\n"
            f");"
        )
        parts.append(
            f"CREATE OR REPLACE VIEW information_mart.{pit_tbl} AS\n"
            f"SELECT * FROM business_vault.{pit_tbl};"
        )
        parts.append(_build_generic_pit_view_sql(pit, hub_by_name, sat_by_name))

    return "\n\n".join(parts)


__all__ = [
    "_build_generic_pit_view_sql",
    "_compile_generic_zone_3_business_vault_sql",
]
