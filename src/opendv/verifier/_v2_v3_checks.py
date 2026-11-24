"""Gates V2 (Alg A.5 & BKCC) and V3 (Zero/Ghost sentinels & temporal/bitemporal).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.ir_kernel import resolve_bkcc
from opendv.models.vault_ir import PredicateBranch, PredicateLeaf, PredicateNode
from opendv.models.vault_ir_certified import CertifiedVaultIR, HashRecipeSpec

_FORBIDDEN_HASH_DIFF_COLS: frozenset[str] = frozenset({"load_dts", "rec_src", "hash_diff"})
_FORBIDDEN_RAW_SAT_TEMPORAL_COLS: frozenset[str] = frozenset(
    {"load_end_dts", "end_dts", "is_current", "is_current_flag"}
)


def _diag(
    gate: str,
    rule_id: str,
    severity: str,
    entity_name: str,
    message: str,
) -> VerifierDiagnostic:
    return VerifierDiagnostic(
        gate=gate,  # type: ignore[arg-type]
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        entity_name=entity_name,
        waivable=False,
        message=message,
    )


def _predicate_cols(node: PredicateNode | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, PredicateLeaf):
        return {node.column}
    if isinstance(node, PredicateBranch):
        cols: set[str] = set()
        for child in node.children:
            cols.update(_predicate_cols(child))
        return cols
    return set()


def _check_recipe_bkcc(
    recipe: HashRecipeSpec,
    key_tier: str,
    entity_name: str,
    diags: list[VerifierDiagnostic],
) -> None:
    if key_tier == "TIER_1_GLOBAL_STANDARD":
        if recipe.bkcc_literal != "GLOBAL":
            diags.append(
                _diag(
                    "V2",
                    "DV-COL-013",
                    "BLOCKER",
                    entity_name,
                    f"TIER_1 on {entity_name} requires bkcc_literal='GLOBAL'.",
                )
            )
    elif key_tier == "TIER_3_LOCAL_SCOPED":
        has_lit = bool(recipe.bkcc_literal and recipe.bkcc_literal.strip())
        has_col = bool(recipe.bkcc_col and recipe.bkcc_col.strip())
        if not (has_lit or has_col):
            diags.append(
                _diag(
                    "V2",
                    "DV-COL-012",
                    "BLOCKER",
                    entity_name,
                    f"TIER_3 on {entity_name} requires bkcc_literal or bkcc_col.",
                )
            )


def verify_v2_hash_and_bkcc(ir: CertifiedVaultIR) -> list[VerifierDiagnostic]:
    """Gate V2: Alg A.5 HashRecipeSpec, tuple-scoped 3-tier BKCC, and IRField checks."""
    diags: list[VerifierDiagnostic] = []
    seen_tier3_scopes: dict[str, set[tuple[str, str | None, str | None]]] = {}

    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        if not h.business_keys:
            diags.append(
                _diag(
                    "V2",
                    "DV-COL-002",
                    "BLOCKER",
                    h.hub_name,
                    f"Non-planned Hub {h.hub_name} has zero BusinessKeyParts.",
                )
            )
        if h.hash_recipe is None or h.hash_recipe.recipe_kind != "HUB_HK":
            diags.append(
                _diag(
                    "V2",
                    "DV-COL-003",
                    "BLOCKER",
                    h.hub_name,
                    f"Hub {h.hub_name} missing HUB_HK HashRecipeSpec.",
                )
            )
        else:
            bk_cols = {bk.col_name for bk in h.business_keys}
            recipe_cols = set(h.hash_recipe.key_columns or h.hash_recipe.source_columns)
            if not bk_cols.issubset(recipe_cols):
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-003",
                        "BLOCKER",
                        h.hub_name,
                        f"Hub {h.hub_name} HashRecipeSpec omits BKs "
                        f"{sorted(bk_cols - recipe_cols)}.",
                    )
                )
            _check_recipe_bkcc(h.hash_recipe, h.key_tier, h.hub_name, diags)

        # BUG-08: Tuple-scoped BKCC validation per (table_id, col_name, hub_name)
        for bk in h.business_keys:
            _ = (bk.table_id, bk.col_name, h.hub_name)
            if (
                bk.key_tier == "TIER_1_GLOBAL_STANDARD"
                and bk.bkcc_value.strip().upper() != "GLOBAL"
            ):
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-013",
                        "BLOCKER",
                        h.hub_name,
                        f"Hub {h.hub_name} BK {bk.col_name} is TIER_1 with bkcc={bk.bkcc_value!r}.",
                    )
                )
            elif bk.key_tier == "TIER_3_LOCAL_SCOPED":
                has_val = bool(bk.bkcc_value.strip())
                has_col = bool(bk.bkcc_col and bk.bkcc_col.strip())
                if not (has_val or has_col):
                    diags.append(
                        _diag(
                            "V2",
                            "DV-COL-012",
                            "BLOCKER",
                            h.hub_name,
                            f"Hub {h.hub_name} TIER_3 BK {bk.col_name} lacks bkcc_value/col.",
                        )
                    )
                else:
                    try:
                        if has_val:
                            resolve_bkcc("TIER_3_LOCAL_SCOPED", bk.bkcc_value)
                    except ValueError as exc:
                        diags.append(_diag("V2", "DV-COL-012", "BLOCKER", h.hub_name, str(exc)))
                    scope_key = (
                        bk.source_id,
                        bk.bkcc_value.strip().upper() if has_val else None,
                        bk.bkcc_col,
                    )
                    seen_tier3_scopes.setdefault(h.hub_name, set()).add(scope_key)

        for sb in h.source_bindings:
            if sb.hash_recipe is not None:
                tbl_bks = [bk for bk in h.business_keys if bk.table_id == sb.table_id]
                eff_tier = tbl_bks[0].key_tier if tbl_bks else h.key_tier
                _check_recipe_bkcc(
                    sb.hash_recipe, eff_tier, f"{h.hub_name}:{sb.table_id}", diags
                )

        col_map = {c.name: c for c in h.columns}
        for audit_col, audit_type in (("load_dts", "TIMESTAMP_UTC_US"), ("rec_src", "UTF8_STRING")):
            fld = col_map.get(audit_col)
            if fld is None or fld.nullable or fld.logical_type != audit_type:
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-005",
                        "BLOCKER",
                        h.hub_name,
                        f"Hub {h.hub_name} requires non-null {audit_col}: {audit_type}.",
                    )
                )

    for lnk in ir.links:
        if lnk.hash_recipe is None or lnk.hash_recipe.recipe_kind != "LINK_HK":
            diags.append(
                _diag(
                    "V2",
                    "DV-COL-003",
                    "BLOCKER",
                    lnk.link_name,
                    f"Link {lnk.link_name} missing LINK_HK HashRecipeSpec.",
                )
            )
        col_map = {c.name: c for c in lnk.columns}
        for part in lnk.participants:
            fk_f = col_map.get(part.fk_col_name)
            if fk_f is None or fk_f.nullable or fk_f.logical_type != "FIXED_BINARY_32":
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-004",
                        "BLOCKER",
                        lnk.link_name,
                        f"Link {lnk.link_name} FK '{part.fk_col_name}' must be FIXED_BINARY_32.",
                    )
                )

    for sat in ir.satellites:
        col_map = {c.name: c for c in sat.columns}
        if sat.sat_kind != "ESAT":
            if sat.hash_recipe is None or sat.hash_recipe.recipe_kind != "SAT_HASH_DIFF":
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-006",
                        "BLOCKER",
                        sat.sat_name,
                        f"Satellite {sat.sat_name} missing SAT_HASH_DIFF HashRecipeSpec.",
                    )
                )
            else:
                r = sat.hash_recipe
                expected_cols = (
                    {a.name for a in sat.attributes}
                    | (set(sat.child_dependent_keys) if sat.sat_kind == "MAS" else set())
                ) - _FORBIDDEN_HASH_DIFF_COLS - (
                    {sat.applied_dts_col} if sat.applied_dts_col else set()
                )
                missing_cols = expected_cols - set(r.source_columns)
                if missing_cols:
                    diags.append(
                        _diag(
                            "V2",
                            "DV-COL-006",
                            "BLOCKER",
                            sat.sat_name,
                            f"{sat.sat_name} SAT_HASH_DIFF omits {sorted(missing_cols)}.",
                        )
                    )
                if not r.sort_alphabetically or r.source_columns != sorted(r.source_columns):
                    diags.append(
                        _diag(
                            "V2",
                            "DV-COL-008",
                            "BLOCKER",
                            sat.sat_name,
                            f"{sat.sat_name} SAT_HASH_DIFF source_columns must be sorted.",
                        )
                    )
                bad_sys = [
                    c
                    for c in r.source_columns
                    if c in _FORBIDDEN_HASH_DIFF_COLS
                    or c.startswith("hk_")
                    or (sat.applied_dts_col and c == sat.applied_dts_col)
                ]
                if bad_sys:
                    diags.append(
                        _diag(
                            "V2",
                            "DV-COL-008",
                            "BLOCKER",
                            sat.sat_name,
                            f"{sat.sat_name} hash_diff includes forbidden cols {bad_sys}.",
                        )
                    )
            hd_col = col_map.get("hash_diff")
            if hd_col is None or hd_col.nullable or hd_col.logical_type != "FIXED_BINARY_32":
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-009",
                        "BLOCKER",
                        sat.sat_name,
                        f"Satellite {sat.sat_name} requires non-null hash_diff: FIXED_BINARY_32.",
                    )
                )

        for audit_col, audit_type in (("load_dts", "TIMESTAMP_UTC_US"), ("rec_src", "UTF8_STRING")):
            fld = col_map.get(audit_col)
            if fld is None or fld.nullable or fld.logical_type != audit_type:
                diags.append(
                    _diag(
                        "V2",
                        "DV-COL-009",
                        "BLOCKER",
                        sat.sat_name,
                        f"Satellite {sat.sat_name} requires non-null {audit_col}: {audit_type}.",
                    )
                )

    return diags


def verify_v3_sentinels_and_temporal(ir: CertifiedVaultIR) -> list[VerifierDiagnostic]:
    """Gate V3: Dual Zero (-1/-2), Ghost record (1900-01-01T00:00:00Z), and temporal rules."""
    diags: list[VerifierDiagnostic] = []
    sr = ir.seed_records

    if (
        sr.unknown_hk_hex != "0" * 64
        or sr.error_hk_hex != "f" * 64
        or sr.ghost_hash_diff_hex != "0" * 64
        or sr.ghost_load_dts != "1900-01-01T00:00:00Z"
        or sr.default_num != -1
    ):
        diags.append(
            _diag(
                "V3",
                "DV-COL-010",
                "BLOCKER",
                "seed_records",
                "SeedRecordsSpec must keep Dual Zero ('0'*64,'f'*64,-1) and Ghost timestamp.",
            )
        )

    stg_by_table: dict[str, set[str]] = {}
    for stg in ir.staging_views:
        cols = {f.name for f in stg.projected_fields} | set(stg.col_lineage_mappings.keys())
        stg_by_table[stg.source_table_id] = cols

    for stg in ir.staging_views:
        known_cols = stg_by_table.get(stg.source_table_id, set())
        for recipe in stg.hash_recipes:
            pred_cols = _predicate_cols(recipe.unknown_predicate) | _predicate_cols(
                recipe.error_predicate
            )
            missing = pred_cols - known_cols
            if missing:
                diags.append(
                    _diag(
                        "V3",
                        "DV-COL-010",
                        "ERROR",
                        stg.view_name,
                        f"Sentinel predicate references unknown columns {sorted(missing)}.",
                    )
                )

    for sat in ir.satellites:
        col_names = {c.name for c in sat.columns} | {a.name for a in sat.attributes}
        if sat.sat_kind in ("STANDARD", "MAS", "PII", "EXT"):
            bad_temporal = col_names & _FORBIDDEN_RAW_SAT_TEMPORAL_COLS
            if bad_temporal:
                diags.append(
                    _diag(
                        "V3",
                        "DV-COL-011",
                        "BLOCKER",
                        sat.sat_name,
                        f"Raw Satellite {sat.sat_name} forbids {sorted(bad_temporal)}.",
                    )
                )
        if sat.is_bitemporal:
            if not sat.applied_dts_col or not sat.applied_dts_col.strip():
                diags.append(
                    _diag(
                        "V3",
                        "TMP-01",
                        "BLOCKER",
                        sat.sat_name,
                        f"Bi-temporal Satellite {sat.sat_name} requires applied_dts_col.",
                    )
                )
            elif sat.applied_dts_col == "load_dts":
                diags.append(
                    _diag(
                        "V3",
                        "TMP-02",
                        "BLOCKER",
                        sat.sat_name,
                        f"Bi-temporal {sat.sat_name} applied_dts_col cannot alias load_dts.",
                    )
                )
            elif sat.applied_dts_col not in col_names:
                diags.append(
                    _diag(
                        "V3",
                        "TMP-04",
                        "BLOCKER",
                        sat.sat_name,
                        f"Bi-temporal {sat.sat_name} missing '{sat.applied_dts_col}'.",
                    )
                )

    return diags


__all__ = [
    "verify_v2_hash_and_bkcc",
    "verify_v3_sentinels_and_temporal",
]
