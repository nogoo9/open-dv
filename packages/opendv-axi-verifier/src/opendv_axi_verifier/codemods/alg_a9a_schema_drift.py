"""Algorithm A.9a: Intra-Source Schema Drift Detector & Additive Satellite Evolution Codemod.

Detects and handles intra-source schema drift across 5 canonical categories without
breaking historical `hash_diff` chains or executing destructive Raw Vault DDL:
  1. `ADDITIVE` (`ADD_COLUMN`): Isolates newly added columns into an additive companion
     Satellite (`sat_<base>_ext_<epoch>` or domain-tagged companion such as
     `sat_fund_investran_esg`) with `volatility_tier = "ADDITIVE_DRIFT_COMPANION"`.
  2. `SOFT_DEPRECATED` (`DROP_COLUMN`): Retains missing source columns in the deployed
     Satellite's `payload_columns`, records them in `soft_deprecated_columns`, and
     rewrites the staging projection to `CAST(NULL AS <type>) AS <col>`.
  3. `RENAMED` (`RENAME_COLUMN`): Detects renamed columns via normalized Levenshtein
     similarity `>= 0.85` (or value sample Jaccard overlap `>= 0.90`), emitting a
     staging alias `new_col AS old_col` with zero Vault DDL mutation.
  4. `TYPE_WIDENED` (`TYPE_WIDENING`): Detects lossless data-type widening (e.g.,
     `VARCHAR(64) -> VARCHAR(255)`, `INT -> BIGINT`, `NUMERIC(12,2) -> NUMERIC(18,2)`),
     absorbing it transparently via canonical `TRIM(CAST(col AS VARCHAR))` hashing.
  5. `KEY_DRIFT` (`SEMANTIC_TYPE_CHANGE` / `KEY_DRIFT`): Detects Business Key column
     mutation or narrowing/incompatible type changes, halting for `ASK-USER`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any

_VARCHAR_RE = re.compile(r"^(?:VAR)?CHAR\s*\(\s*(\d+)\s*\)$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^(?:NUMERIC|DECIMAL)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)
_INT_RANK: dict[str, int] = {
    "TINYINT": 1,
    "SMALLINT": 2,
    "INT": 3,
    "INTEGER": 3,
    "BIGINT": 4,
    "HUGEINT": 5,
}


@dataclass(frozen=True)
class DriftFinding:
    """Structured diagnostic record for a single intra-source schema drift event."""

    category: str  # ADDITIVE | SOFT_DEPRECATED | RENAMED | TYPE_WIDENED | KEY_DRIFT
    source_table: str
    target_entity: str
    column_name: str
    new_column_name: str | None = None
    old_type: str | None = None
    new_type: str | None = None
    similarity: float | None = None
    action: str = "AUTO-FIX"
    staging_sql_rewrite: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the exact Levenshtein edit distance between two strings."""
    a = s1.lower()
    b = s2.lower()
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev_row = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        curr_row = [i]
        for j, ch_b in enumerate(b, start=1):
            insert_cost = curr_row[j - 1] + 1
            delete_cost = prev_row[j] + 1
            replace_cost = prev_row[j - 1] + (0 if ch_a == ch_b else 1)
            curr_row.append(min(insert_cost, delete_cost, replace_cost))
        prev_row = curr_row
    return prev_row[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Compute normalized Levenshtein similarity in `[0.0, 1.0]`."""
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    dist = levenshtein_distance(s1, s2)
    return 1.0 - (dist / max_len)


def is_lossless_type_widening(old_type: str, new_type: str) -> bool:
    """Return True if `old_type -> new_type` is a lossless SQL type widening."""
    old_u = old_type.strip().upper()
    new_u = new_type.strip().upper()
    if old_u == new_u:
        return False

    # VARCHAR(n) -> VARCHAR(m) with m > n, or VARCHAR(n) -> VARCHAR / TEXT
    m_old_vc = _VARCHAR_RE.match(old_u)
    m_new_vc = _VARCHAR_RE.match(new_u)
    if m_old_vc and m_new_vc:
        return int(m_new_vc.group(1)) > int(m_old_vc.group(1))
    if m_old_vc and new_u in {"VARCHAR", "TEXT", "STRING"}:
        return True

    # Integer widening: SMALLINT -> INT -> BIGINT
    if old_u in _INT_RANK and new_u in _INT_RANK:
        return _INT_RANK[new_u] > _INT_RANK[old_u]

    # NUMERIC(p1, s1) -> NUMERIC(p2, s2) where s2 >= s1 and (p2 - s2) >= (p1 - s1)
    m_old_num = _NUMERIC_RE.match(old_u)
    m_new_num = _NUMERIC_RE.match(new_u)
    if m_old_num and m_new_num:
        p1, s1 = int(m_old_num.group(1)), int(m_old_num.group(2))
        p2, s2 = int(m_new_num.group(1)), int(m_new_num.group(2))
        return (p2 > p1 or s2 > s1) and s2 >= s1 and (p2 - s2) >= (p1 - s1)

    return old_u == "DATE" and new_u in {"TIMESTAMP", "TIMESTAMPTZ"}


def are_types_compatible(old_type: str | None, new_type: str | None) -> bool:
    """Return True if `old_type` and `new_type` are identical or losslessly widened."""
    if not old_type or not new_type:
        return True
    if old_type.strip().upper() == new_type.strip().upper():
        return True
    return is_lossless_type_widening(old_type, new_type)


def detect_intra_source_schema_drift(
    deployed_columns: list[str] | set[str],
    candidate_columns: list[str] | set[str],
    *,
    source_table: str = "unknown",
    target_entity: str = "sat_unknown",
    key_columns: set[str] | None = None,
    old_types: dict[str, str] | None = None,
    new_types: dict[str, str] | None = None,
    value_jaccard: dict[tuple[str, str], float] | None = None,
    similarity_threshold: float = 0.85,
) -> list[DriftFinding]:
    """Classify column-level schema drift across the 5 canonical categories:
    `ADDITIVE`, `SOFT_DEPRECATED`, `RENAMED`, `TYPE_WIDENED`, and `KEY_DRIFT`.
    """
    dep_set = set(deployed_columns)
    cand_set = set(candidate_columns)
    keys = set(key_columns or set())
    old_t = old_types or {}
    new_t = new_types or {}
    jaccard_map = value_jaccard or {}

    findings: list[DriftFinding] = []

    # 1. Check for KEY_DRIFT on business/foreign key columns first
    for k_col in sorted(keys):
        if k_col in dep_set and k_col not in cand_set:
            findings.append(
                DriftFinding(
                    category="KEY_DRIFT",
                    source_table=source_table,
                    target_entity=target_entity,
                    column_name=k_col,
                    old_type=old_t.get(k_col),
                    action="ASK-USER",
                    message=(
                        f"Business/Foreign key column '{k_col}' on '{target_entity}' "
                        f"is missing from source table '{source_table}'."
                    ),
                )
            )
        elif (
            k_col in dep_set
            and k_col in cand_set
            and k_col in old_t
            and k_col in new_t
            and not are_types_compatible(old_t[k_col], new_t[k_col])
        ):
            findings.append(
                DriftFinding(
                    category="KEY_DRIFT",
                    source_table=source_table,
                    target_entity=target_entity,
                    column_name=k_col,
                    old_type=old_t[k_col],
                    new_type=new_t[k_col],
                    action="ASK-USER",
                    message=(
                        f"Key column '{k_col}' on '{target_entity}' underwent "
                        f"incompatible type drift: {old_t[k_col]} -> {new_t[k_col]}."
                    ),
                )
            )

    # 2. Check retained columns for TYPE_WIDENED vs incompatible KEY_DRIFT
    retained_cols = sorted((dep_set & cand_set) - keys)
    for col in retained_cols:
        if col in old_t and col in new_t and old_t[col].upper() != new_t[col].upper():
            if is_lossless_type_widening(old_t[col], new_t[col]):
                findings.append(
                    DriftFinding(
                        category="TYPE_WIDENED",
                        source_table=source_table,
                        target_entity=target_entity,
                        column_name=col,
                        old_type=old_t[col],
                        new_type=new_t[col],
                        action="AUTO-FIX",
                        staging_sql_rewrite=f"TRIM(CAST({col} AS VARCHAR))",
                        message=(
                            f"Lossless type widening on '{col}': {old_t[col]} -> {new_t[col]}."
                        ),
                    )
                )
            else:
                findings.append(
                    DriftFinding(
                        category="KEY_DRIFT",
                        source_table=source_table,
                        target_entity=target_entity,
                        column_name=col,
                        old_type=old_t[col],
                        new_type=new_t[col],
                        action="ASK-USER",
                        message=(
                            f"Incompatible semantic type change on '{col}': "
                            f"{old_t[col]} -> {new_t[col]}."
                        ),
                    )
                )

    # 3. Match removed and added non-key columns for RENAMED (similarity >= 0.85 or Jaccard >= 0.90)
    raw_removed = sorted((dep_set - cand_set) - keys)
    raw_added = sorted((cand_set - dep_set) - keys)

    matched_removed: set[str] = set()
    matched_added: set[str] = set()

    for old_col in raw_removed:
        best_new: str | None = None
        best_score = 0.0
        for new_col in raw_added:
            if new_col in matched_added:
                continue
            if not are_types_compatible(old_t.get(old_col), new_t.get(new_col)):
                continue
            lev_sim = levenshtein_similarity(old_col, new_col)
            jac_sim = jaccard_map.get((old_col, new_col), jaccard_map.get((new_col, old_col), 0.0))
            effective_sim = max(lev_sim, jac_sim)
            if (lev_sim >= similarity_threshold or jac_sim >= 0.90) and effective_sim > best_score:
                best_score = effective_sim
                best_new = new_col

        if best_new is not None:
            matched_removed.add(old_col)
            matched_added.add(best_new)
            findings.append(
                DriftFinding(
                    category="RENAMED",
                    source_table=source_table,
                    target_entity=target_entity,
                    column_name=old_col,
                    new_column_name=best_new,
                    old_type=old_t.get(old_col),
                    new_type=new_t.get(best_new),
                    similarity=round(best_score, 4),
                    action="AUTO-FIX",
                    staging_sql_rewrite=f"{best_new} AS {old_col}",
                    message=(
                        f"Column '{old_col}' renamed to '{best_new}' "
                        f"(similarity={best_score:.2f} >= {similarity_threshold:.2f}); "
                        f"aliased in staging as '{best_new} AS {old_col}'."
                    ),
                )
            )

    # 4. Remaining removed columns -> SOFT_DEPRECATED
    for old_col in raw_removed:
        if old_col in matched_removed:
            continue
        col_dtype = old_t.get(old_col, "VARCHAR")
        findings.append(
            DriftFinding(
                category="SOFT_DEPRECATED",
                source_table=source_table,
                target_entity=target_entity,
                column_name=old_col,
                old_type=col_dtype,
                action="AUTO-FIX",
                staging_sql_rewrite=f"CAST(NULL AS {col_dtype}) AS {old_col}",
                message=(
                    f"Column '{old_col}' missing from '{source_table}'; "
                    f"soft-deprecated in '{target_entity}' via NULL projection."
                ),
            )
        )

    # 5. Remaining added columns -> ADDITIVE
    for new_col in raw_added:
        if new_col in matched_added:
            continue
        findings.append(
            DriftFinding(
                category="ADDITIVE",
                source_table=source_table,
                target_entity=target_entity,
                column_name=new_col,
                new_type=new_t.get(new_col),
                action="AUTO-FIX",
                message=(
                    f"Additive column '{new_col}' detected on '{source_table}'; "
                    f"routed to companion satellite."
                ),
            )
        )

    return findings


def codemod_intra_source_schema_drift(
    deployed_contract: dict[str, Any],
    candidate_contract: dict[str, Any],
    schema_epoch_tag: str = "v2",
    *,
    similarity_threshold: float = 0.85,
    companion_name_override: str | None = None,
) -> dict[str, Any]:
    """Algorithm A.9a: Zero-Rehash Intra-Source Schema Drift Codemod.

    When an already-deployed Satellite gains new descriptive columns, experiences column
    renames (`similarity >= 0.85`), or loses columns in a subsequent schema epoch:
      - Keeps the deployed Satellite's `payload_columns` 100% immutable so historical
        `hash_diff` values never invalidate.
      - Maps renamed columns (`new_col -> old_col`) back to the deployed Satellite in
        `column_triage_ledger` with zero Vault DDL mutation.
      - Records dropped columns in `soft_deprecated_columns`.
      - Spins off genuinely new columns into an additive companion Satellite
        (`sat_<entity>_ext_<schema_epoch_tag>` or `companion_name_override`).
      - Escalates `KEY_DRIFT` (`action == "ASK-USER"`) findings into `unmapped_escalations`.
    """
    ir = copy.deepcopy(candidate_contract)
    deployed_sats = {
        str(s["name"]): s for s in deployed_contract.get("satellites", []) if "name" in s
    }
    updated_satellites: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = ir.get("column_triage_ledger", [])
    use_v5_ledger = str(ir.get("contract_version", "")) in {"5.0", "5.0.0"} or any(
        "bronze_table" in e or "target_dv_entity" in e for e in ledger if isinstance(e, dict)
    )
    escalations: list[dict[str, Any]] = list(ir.get("unmapped_escalations") or [])

    # Check deployed Hubs for business key column drift (KEY_DRIFT -> ASK-USER)
    deployed_hubs = {str(h["name"]): h for h in deployed_contract.get("hubs", []) if "name" in h}
    for hub in ir.get("hubs", []):
        h_name = str(hub.get("name", ""))
        if h_name not in deployed_hubs:
            continue
        dep_hub = deployed_hubs[h_name]
        dep_sources = {
            (str(s.get("bronze_table", "")), str(s.get("record_source_code", ""))): s
            for s in dep_hub.get("business_key_sources", [])
        }
        for src in hub.get("business_key_sources", []):
            s_key = (str(src.get("bronze_table", "")), str(src.get("record_source_code", "")))
            if s_key not in dep_sources:
                continue
            dep_src = dep_sources[s_key]
            dep_kcols = list(dep_src.get("key_columns", []))
            cand_kcols = list(src.get("key_columns", []))
            if dep_kcols != cand_kcols:
                b_tbl = s_key[0]
                drifted_cols = sorted(set(dep_kcols) ^ set(cand_kcols)) or cand_kcols
                escalations.append(
                    {
                        "bronze_table": b_tbl,
                        "columns": [str(c) for c in drifted_cols],
                        "escalation_reason": "COMPETING_NATURAL_KEYS",
                        "architect_question": (
                            f"Business key columns on '{h_name}' ({b_tbl}) drifted from "
                            f"{dep_kcols} to {cand_kcols}."
                        ),
                    }
                )

    for sat in ir.get("satellites", []):
        s_name = str(sat.get("name", ""))
        if s_name not in deployed_sats:
            updated_satellites.append(sat)
            continue

        dep_sat = deployed_sats[s_name]
        dep_cols = set(dep_sat.get("payload_columns", []))
        cand_cols = set(sat.get("payload_columns", []))
        b_table = str(sat.get("source_bronze_table", ""))
        old_types = dep_sat.get("column_types")
        new_types = sat.pop("column_types", None)
        val_jaccard = sat.pop("value_jaccard", None)
        sat.pop("staging_column_aliases", None)

        findings = detect_intra_source_schema_drift(
            deployed_columns=dep_cols,
            candidate_columns=cand_cols,
            source_table=b_table,
            target_entity=s_name,
            old_types=old_types,
            new_types=new_types,
            value_jaccard=val_jaccard,
            similarity_threshold=similarity_threshold,
        )

        added_cols = sorted(f.column_name for f in findings if f.category == "ADDITIVE")
        removed_cols = sorted(f.column_name for f in findings if f.category == "SOFT_DEPRECATED")
        renamed_pairs = {
            f.new_column_name: f.column_name
            for f in findings
            if f.category == "RENAMED" and f.new_column_name is not None
        }
        key_drift_findings = [
            f for f in findings if f.category == "KEY_DRIFT" or f.action == "ASK-USER"
        ]

        for kd in key_drift_findings:
            escalations.append(
                {
                    "bronze_table": b_table,
                    "columns": [kd.column_name],
                    "escalation_reason": "COMPETING_NATURAL_KEYS",
                    "architect_question": kd.message,
                }
            )
            for entry in ledger:
                e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                if e_table == b_table and entry.get("column_name") == kd.column_name:
                    if "target_dv_entity" in entry or "target_entity" not in entry:
                        entry["target_dv_entity"] = "ESCALATED"
                    if "target_entity" in entry:
                        entry["target_entity"] = "ESCALATED"
                    if "elm_classification" in entry or "dv_target_role" not in entry:
                        entry["elm_classification"] = "ESCALATED_AMBIGUOUS"
                    if "dv_target_role" in entry:
                        entry["dv_target_role"] = "ESCALATED_AMBIGUOUS"

        # Preserve original payload columns on the deployed Satellite
        sat["payload_columns"] = sorted(dep_cols)
        if removed_cols:
            existing_soft = list(sat.get("soft_deprecated_columns") or [])
            sat["soft_deprecated_columns"] = sorted(set(existing_soft + removed_cols))
        if renamed_pairs:
            for entry in ledger:
                e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                e_col = str(entry.get("column_name", ""))
                if e_table == b_table and (
                    e_col in renamed_pairs or e_col in renamed_pairs.values()
                ):
                    if "target_dv_entity" in entry or "target_entity" not in entry:
                        entry["target_dv_entity"] = s_name
                    if "target_entity" in entry:
                        entry["target_entity"] = s_name
                    if "elm_classification" in entry or "dv_target_role" not in entry:
                        entry["elm_classification"] = "DESCRIPTIVE_PAYLOAD"
                    if "dv_target_role" in entry:
                        entry["dv_target_role"] = "SATELLITE_PAYLOAD"

        updated_satellites.append(sat)

        # Spin off newly added columns into an additive companion Satellite
        if added_cols:
            companion_name = (
                companion_name_override
                if companion_name_override
                else f"{s_name}_ext_{schema_epoch_tag}"
            )
            companion_sat: dict[str, Any] = {
                "name": companion_name,
                "satellite_type": sat.get("satellite_type", "STANDARD_SATELLITE"),
                "parent_entity": sat["parent_entity"],
                "source_bronze_table": b_table,
                "security_classification": sat.get("security_classification", "CONFIDENTIAL"),
                "volatility_tier": "ADDITIVE_DRIFT_COMPANION",
                "payload_columns": added_cols,
                "soft_deprecated_columns": [],
            }
            updated_satellites.append(companion_sat)

            existing_ledger_cols = {
                (
                    str(e.get("bronze_table") or e.get("source_table") or ""),
                    str(e.get("column_name", "")),
                )
                for e in ledger
            }
            for col in added_cols:
                updated_in_place = False
                for entry in ledger:
                    e_table = str(entry.get("bronze_table") or entry.get("source_table") or "")
                    if e_table == b_table and entry.get("column_name") == col:
                        if "target_dv_entity" in entry or "target_entity" not in entry:
                            entry["target_dv_entity"] = companion_name
                        if "target_entity" in entry:
                            entry["target_entity"] = companion_name
                        if "elm_classification" in entry or "dv_target_role" not in entry:
                            entry["elm_classification"] = "DESCRIPTIVE_PAYLOAD"
                        if "dv_target_role" in entry:
                            entry["dv_target_role"] = "SATELLITE_PAYLOAD"
                        updated_in_place = True
                if not updated_in_place and (b_table, col) not in existing_ledger_cols:
                    if use_v5_ledger:
                        ledger.append(
                            {
                                "bronze_table": b_table,
                                "column_name": col,
                                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                                "target_dv_entity": companion_name,
                            }
                        )
                    else:
                        ledger.append(
                            {
                                "source_table": b_table,
                                "column_name": col,
                                "dv_target_role": "SATELLITE_PAYLOAD",
                                "target_entity": companion_name,
                                "disposition_rationale": (
                                    f"Auto-routed additive schema drift column to {companion_name}."
                                ),
                            }
                        )

    if escalations or "unmapped_escalations" in ir:
        ir["unmapped_escalations"] = escalations
    ir["satellites"] = updated_satellites
    return ir


codemod_6_additive_schema_drift_evolution = codemod_intra_source_schema_drift
