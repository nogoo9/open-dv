"""Algorithm A.6: SQLGlot AST Static Verifier (`DV-AST-001`-`006` / `DV-G6-001`-`005`).

Performs static SQLGlot AST inspection on staging and Raw Vault SQL models:
  - `DV-AST-001` (`DV-G6-002`): No `UPDATE`, `DELETE`, `TRUNCATE`, `DROP`, destructive `ALTER`
    (only additive `ALTER TABLE ... ADD COLUMN` is permitted), or destructive `MERGE` on Raw Vault
    tables (`hub_*`, `lnk_*`, `sat_*`, `esat_*`, `mas_*`).
  - `DV-AST-002` (`DV-G6-001`): Canonical length-prefixed `||` (`DPipe`) hash recipe
    (`OCTET_LENGTH` / `BYTE_LENGTH` + `||` delimiter).
  - `DV-AST-003` (`DV-G6-001`): Positional null token `'-1:^^'` in every `hk_*`,
    `hk_lnk_*`, and `hash_diff` hash expression.
  - `DV-AST-004` (`DV-G6-001` / `DV-G6-003`): Deterministic projection (forbids `NOW()`,
    `CURRENT_TIMESTAMP`, `RANDOM()`, `UUID()` in hashes and forbids `SELECT *`).
  - `DV-AST-005` (`DV-G6-004`): Satellite `INSERT` load `hash_diff` delta guard in `WHERE`
    clause and `esat_*` windowing partitioned by `driving_hk_*` (never `hk_lnk_*`).
  - `DV-AST-006` (`DV-G6-005`): Raw Vault structural column purity (`load_dts`, `rec_src`,
    `hk_*`, `hk_lnk_*`, `hash_diff`) and ban on `GROUP BY` / `HAVING` aggregations
    (except idempotent Hub/Link micro-batch key deduplication).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

FORBIDDEN_NON_DETERMINISTIC_FUNCS: frozenset[str] = frozenset(
    {
        "NOW",
        "CURRENT_TIMESTAMP",
        "SYSDATE",
        "GETDATE",
        "RANDOM",
        "RAND",
        "UUID",
        "GEN_RANDOM_UUID",
        "NEWID",
    }
)


HUB_PREFIXES: tuple[str, ...] = ("hub_", "bhub_")
LINK_PREFIXES: tuple[str, ...] = ("lnk_", "t_lnk_", "nh_lnk_", "sal_", "hal_", "x_lnk_")
LINK_KEY_PREFIXES: tuple[str, ...] = tuple(f"hk_{p}" for p in LINK_PREFIXES)


def _infer_entity_type(entity_name: str, explicit_type: str | None = None) -> str:
    if explicit_type:
        return explicit_type.upper()
    lower = entity_name.lower().rsplit(".", 1)[-1]
    if lower.startswith(HUB_PREFIXES):
        return "HUB"
    if lower.startswith(LINK_PREFIXES):
        return "LINK"
    if lower.startswith("esat_"):
        return "EFFECTIVITY_SATELLITE"
    if lower.startswith(("mas_", "ma_sat_")):
        return "MULTI_ACTIVE_SATELLITE"
    if lower.startswith(("sat_", "bsat_", "sts_", "rts_")):
        return "STANDARD_SATELLITE"
    if lower.startswith("stg_"):
        return "STAGE"
    return "RAW_VAULT"


def _is_idempotent_microbatch_dedup_group_by(
    stmt: exp.Expr,
    resolved_type: str,
) -> bool:
    """Return True if `stmt` uses `GROUP BY` strictly for idempotent Hub/Link
    micro-batch key deduplication (`MIN(load_dts)` and optional `ANY_VALUE(rec_src)` /
    `MIN(rec_src)` grouped by primary hash key and business/foreign keys, with no
    `HAVING` or business metric aggregations).
    """
    if resolved_type not in {"HUB", "LINK"}:
        return False
    if stmt.find(exp.Having) is not None:
        return False

    if isinstance(stmt, exp.Insert) and isinstance(stmt.expression, exp.Select):
        select_node = stmt.expression
    elif isinstance(stmt, exp.Select):
        select_node = stmt
    else:
        return False

    group_nodes = list(stmt.find_all(exp.Group))
    group_node = select_node.args.get("group")
    if group_node is None or len(group_nodes) != 1 or group_nodes[0] is not group_node:
        return False

    group_exprs = list(group_node.expressions or [])
    if not group_exprs:
        return False

    group_col_names: list[str] = []
    for g_expr in group_exprs:
        if not isinstance(g_expr, exp.Column) or not g_expr.name:
            return False
        group_col_names.append(g_expr.name.lower())

    if "load_dts" in group_col_names or "rec_src" in group_col_names:
        return False

    if resolved_type == "HUB" and not any(c.startswith("hk_") for c in group_col_names):
        return False
    if resolved_type == "LINK" and not any(
        c.startswith(LINK_KEY_PREFIXES) for c in group_col_names
    ):
        return False

    insert_cols: list[str] = []
    if isinstance(stmt, exp.Insert) and isinstance(stmt.this, exp.Schema):
        insert_cols = [c.alias_or_name.lower() for c in stmt.this.expressions]

    has_min_load_dts = False
    group_col_set = set(group_col_names)

    for idx, proj in enumerate(select_node.expressions):
        target_col = proj.alias_or_name.lower()
        if not target_col and idx < len(insert_cols):
            target_col = insert_cols[idx]

        aggs = list(proj.find_all(exp.AggFunc))
        if aggs:
            if len(aggs) != 1:
                return False
            agg = aggs[0]
            unwrapped = proj.unalias()
            if isinstance(unwrapped, exp.IgnoreNulls):
                unwrapped = unwrapped.this
            if unwrapped is not agg:
                return False
            if not isinstance(agg.this, exp.Column) or not agg.this.name:
                return False
            inner_col = agg.this.name.lower()
            effective_col = target_col or inner_col

            if inner_col == "load_dts" and effective_col == "load_dts":
                if not isinstance(agg, exp.Min):
                    return False
                has_min_load_dts = True
            elif inner_col == "rec_src" and effective_col == "rec_src":
                if not isinstance(agg, (exp.AnyValue, exp.Min)):
                    return False
            else:
                return False
        else:
            unwrapped = proj.unalias()
            if not isinstance(unwrapped, exp.Column) or not unwrapped.name:
                return False
            if unwrapped.name.lower() not in group_col_set:
                return False

    return has_min_load_dts


def verify_physical_dv2_sql_ast(
    sql_text: str,
    *,
    entity_name: str = "raw_vault_model",
    entity_type: str | None = None,
    dialect: str = "duckdb",
) -> list[str]:
    """Algorithm A.6: Static SQLGlot AST Verifier enforcing `DV-AST-001`-`006`
    and `DV-G6-001`-`005`.
    """
    violations: list[str] = []
    resolved_type = _infer_entity_type(entity_name, entity_type)

    # Raw text check for TRUNCATE if dialect parses it as Command
    upper_stripped = sql_text.strip().upper()
    if upper_stripped.startswith("TRUNCATE "):
        violations.append(
            f"[DV-AST-001 / DV-G6-002] Destructive DML 'TRUNCATE' is strictly forbidden on "
            f"Raw Vault entity '{entity_name}'."
        )
        return violations

    try:
        statements = sqlglot.parse(sql_text, read=dialect)
    except Exception as exc:
        return [f"[DV-AST-PARSE-ERR] SQLGlot failed to parse '{entity_name}': {exc}"]

    for stmt in statements:
        if stmt is None:
            continue

        # 1. DV-AST-001 / DV-G6-002: Insert-Only Raw Vault DML Enforcement
        if isinstance(stmt, exp.Alter):
            actions = stmt.args.get("actions") or []
            is_additive_col_only = bool(actions) and all(
                isinstance(act, exp.ColumnDef) for act in actions
            )
            if not is_additive_col_only:
                violations.append(
                    f"[DV-AST-001 / DV-G6-002] Destructive DML 'ALTER' is strictly "
                    f"forbidden on Raw Vault entity '{entity_name}'."
                )
        elif isinstance(stmt, (exp.Update, exp.Delete, exp.Drop, exp.Command)):
            stmt_kind = stmt.key.upper()
            raw_sql_u = stmt.sql().upper()
            if stmt_kind in {"UPDATE", "DELETE", "DROP"} or any(
                kw in raw_sql_u for kw in ("TRUNCATE", "UPDATE ", "DELETE ")
            ):
                violations.append(
                    f"[DV-AST-001 / DV-G6-002] Destructive DML '{stmt_kind}' is strictly "
                    f"forbidden on Raw Vault entity '{entity_name}'."
                )

        if isinstance(stmt, exp.Merge):
            merge_sql_u = stmt.sql(dialect=dialect).upper()
            if "WHEN MATCHED" in merge_sql_u and (
                "UPDATE" in merge_sql_u or "DELETE" in merge_sql_u
            ):
                violations.append(
                    f"[DV-AST-001 / DV-G6-002] MERGE with WHEN MATCHED UPDATE/DELETE is "
                    f"forbidden on Raw Vault entity '{entity_name}'."
                )

        # 2. DV-AST-004 / DV-G6-003: No SELECT * Wildcards
        for star in stmt.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                violations.append(
                    f"[DV-AST-004 / DV-G6-003] Wildcard 'SELECT *' is forbidden in "
                    f"'{entity_name}'. Enumerate columns explicitly."
                )
                break

        stmt_type = resolved_type
        if stmt_type == "RAW_VAULT" and isinstance(stmt, exp.Insert):
            target_tbl = stmt.this.find(exp.Table) if stmt.this is not None else None
            if target_tbl is not None and target_tbl.name:
                stmt_type = _infer_entity_type(target_tbl.name)

        # 3. DV-AST-006 / DV-G6-005: No Aggregations (GROUP BY / HAVING) in Raw Vault
        if (
            stmt_type != "STAGE"
            and (stmt.find(exp.Group) is not None or stmt.find(exp.Having) is not None)
            and not _is_idempotent_microbatch_dedup_group_by(stmt, stmt_type)
        ):
            violations.append(
                f"[DV-AST-006 / DV-G6-005] Raw Vault entity '{entity_name}' contains "
                f"GROUP BY or HAVING aggregation; aggregations belong in Business Vault."
            )

        # 4. Inspect Projections for Hash Discipline & Archetype Signatures
        select_node = stmt.find(exp.Select)
        if select_node is not None:
            insert_cols: list[str] = []
            if isinstance(stmt, exp.Insert) and isinstance(stmt.this, exp.Schema):
                insert_cols = [c.alias_or_name.lower() for c in stmt.this.expressions]

            projected_cols: dict[str, exp.Expression] = {}
            for idx, proj in enumerate(select_node.expressions):
                col_alias = proj.alias_or_name.lower()
                if not col_alias and idx < len(insert_cols):
                    col_alias = insert_cols[idx]
                if not col_alias:
                    unwrapped = proj.unalias()
                    if isinstance(unwrapped, exp.IgnoreNulls):
                        unwrapped = unwrapped.this
                    if isinstance(unwrapped, (exp.Min, exp.AnyValue)) and isinstance(
                        unwrapped.this, exp.Column
                    ):
                        col_alias = unwrapped.this.name.lower()
                if col_alias:
                    projected_cols[col_alias] = proj

            # DV-AST-002, DV-AST-003, DV-AST-004 / DV-G6-001: Hash expression checks
            for col_name, proj_node in projected_cols.items():
                if col_name.startswith(("hk_", "hash_diff")):
                    proj_sql_u = proj_node.sql(dialect=dialect).upper()

                    # Check non-deterministic functions inside hash expression (DV-AST-004)
                    for func_node in proj_node.find_all(exp.Func):
                        f_name = func_node.sql_name().upper()
                        if f_name in FORBIDDEN_NON_DETERMINISTIC_FUNCS or isinstance(
                            func_node, exp.CurrentTimestamp
                        ):
                            violations.append(
                                f"[DV-AST-004 / DV-G6-001] Hash column '{col_name}' in "
                                f"'{entity_name}' invokes non-deterministic function '{f_name}'."
                            )

                    is_macro_call = "DV_BK(" in proj_sql_u or "DV_PL(" in proj_sql_u
                    is_inline_sha = (
                        "SHA2" in proj_sql_u or "SHA256" in proj_sql_u or "MD5" in proj_sql_u
                    )
                    has_dpipe = proj_node.find(exp.DPipe) is not None or "||" in proj_sql_u
                    has_concat = (
                        proj_node.find(exp.Concat) is not None
                        or "CONCAT(" in proj_sql_u
                        or "CONCAT_WS(" in proj_sql_u
                    )

                    # DV-AST-002: Length-prefixed `||` hashing check
                    if (is_inline_sha or has_dpipe or has_concat) and not is_macro_call:
                        has_len_prefix = any(
                            token in proj_sql_u for token in ("OCTET_LENGTH(", "BYTE_LENGTH(")
                        )
                        if not has_len_prefix or not has_dpipe:
                            violations.append(
                                f"[DV-AST-002 / DV-G6-001] Hash column '{col_name}' in "
                                f"'{entity_name}' lacks canonical length-prefixed '||' "
                                f"(OCTET_LENGTH + '||') concatenation."
                            )
                        # DV-AST-003: Null token `-1:^^` check
                        if "-1:^^" not in proj_sql_u:
                            violations.append(
                                f"[DV-AST-003 / DV-G6-001] Hash column '{col_name}' in "
                                f"'{entity_name}' is missing positional null token '-1:^^'."
                            )

            # DV-AST-006 / DV-G6-005: Archetype Column Signature & Audit Columns
            if stmt_type != "STAGE" and projected_cols:
                for mandatory_audit in ("load_dts", "rec_src"):
                    if mandatory_audit not in projected_cols:
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] '{entity_name}' is missing mandatory "
                            f"audit column '{mandatory_audit}'."
                        )

                if stmt_type == "HUB":
                    has_hk = any(
                        c.startswith("hk_hub_") or c.startswith("hk_") for c in projected_cols
                    )
                    if not has_hk:
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] Hub '{entity_name}' is missing a "
                            f"primary hash key column ('hk_*')."
                        )
                    if "hash_diff" in projected_cols:
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] Hub '{entity_name}' must not contain "
                            f"a 'hash_diff' column."
                        )
                elif stmt_type == "LINK":
                    if not any(c.startswith(LINK_KEY_PREFIXES) for c in projected_cols):
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] Link '{entity_name}' is missing a "
                            f"link hash key column ('hk_lnk_*')."
                        )
                    if "hash_diff" in projected_cols:
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] Link '{entity_name}' must not contain "
                            f"a 'hash_diff' column."
                        )
                elif stmt_type in {"STANDARD_SATELLITE", "MULTI_ACTIVE_SATELLITE"}:
                    if "hash_diff" not in projected_cols:
                        violations.append(
                            f"[DV-AST-006 / DV-G6-005] Satellite '{entity_name}' is missing "
                            f"a 'hash_diff' column."
                        )

            # 5. DV-AST-005 / DV-G6-004: Satellite INSERT hash_diff Delta Check & ESAT Windowing
            if stmt_type in {"STANDARD_SATELLITE", "MULTI_ACTIVE_SATELLITE"} and isinstance(
                stmt, exp.Insert
            ):
                where_node = stmt.find(exp.Where)
                if where_node is None or "hash_diff" not in where_node.sql(dialect=dialect).lower():
                    violations.append(
                        f"[DV-AST-005 / DV-G6-004] Satellite INSERT on '{entity_name}' lacks "
                        f"a 'hash_diff' delta comparison in its WHERE clause."
                    )

            if stmt_type == "EFFECTIVITY_SATELLITE":
                for win in stmt.find_all(exp.Window):
                    win_sql = win.sql(dialect=dialect).lower()
                    if (
                        "partition by" in win_sql
                        and "hk_lnk_" in win_sql
                        and "driving_hk_" not in win_sql
                    ):
                        violations.append(
                            f"[DV-AST-005 / DV-G6-004] Effectivity Satellite '{entity_name}' "
                            f"window function partitions by 'hk_lnk_*' instead of 'driving_hk_*'."
                        )

    return violations


def verify_sql_models_batch(
    compiled_sql_models: dict[str, str],
    dialect: str = "duckdb",
) -> list[str]:
    """Run Algorithm A.6 across a dictionary of `{entity_name: sql_text}`."""
    all_violations: list[str] = []
    for entity_name, sql_text in compiled_sql_models.items():
        all_violations.extend(
            verify_physical_dv2_sql_ast(
                sql_text,
                entity_name=entity_name,
                dialect=dialect,
            )
        )
    return all_violations
