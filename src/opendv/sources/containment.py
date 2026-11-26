"""Asymmetric FK-to-PK local containment overlap detection using ephemeral HMAC salts."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

from opendv.models.modeling_preferences import PkContainmentOverlap
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile

_COL_SUFFIX_RE = re.compile(r"(?:_id|_key|_code|_pk|_num|_no|id)$")


def _matches_table_stem(table_name: str, col_name: str) -> bool:
    col_low = col_name.strip().lower()
    col_stem = _COL_SUFFIX_RE.sub("", col_low)
    if len(col_stem) < 2:
        return False
    stems: set[str] = set()
    for tok in re.split(r"[^a-z0-9]+", table_name.lower()):
        if not tok:
            continue
        stems.add(tok)
        if tok.endswith("ies") and len(tok) > 3:
            stems.add(tok[:-3] + "y")
        elif tok.endswith("s") and len(tok) > 2:
            stems.add(tok[:-1])
    return any(s == col_stem or (len(col_stem) >= 3 and s.startswith(col_stem)) for s in stems)


def _is_target_col(tbl: TableProfile, col: ColumnProfile) -> bool:
    uniqueness_ratio = col.distinct_count_est / max(1, tbl.row_count_est)
    return (
        col.is_pk
        or uniqueness_ratio >= 0.95
        or col.col_name == tbl.best_non_surrogate_bk_candidate_col
    )


def _right_rank(
    tbl: TableProfile,
    col: ColumnProfile,
    hashes: set[bytes],
    overlap_into: float,
) -> tuple[int, int, int, float, int, int, str, str]:
    return (
        1 if col.is_pk else 0,
        1 if col.col_name == tbl.best_non_surrogate_bk_candidate_col else 0,
        1 if _matches_table_stem(tbl.table_name, col.col_name) else 0,
        round(overlap_into, 6),
        len(hashes),
        0 if col.surrogate_score >= 0.75 else 1,
        tbl.table_id,
        col.col_name,
    )


def compute_pk_containment_overlaps(
    profiles: Sequence[SourceProfile],
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    table_sample_flags: Mapping[str, bool] | None = None,
) -> list[PkContainmentOverlap]:
    """Compute asymmetric FK->PK containment overlaps over local sampled rows."""
    _ = table_sample_flags
    salt = secrets.token_bytes(16)
    entries: list[tuple[str, TableProfile, ColumnProfile, set[bytes], int, bool]] = []
    for prof in profiles:
        for tbl in prof.tables:
            rows = source_rows_by_table.get(tbl.table_id, ())
            if not rows:
                continue
            row_cnt = tbl.row_count_est if tbl.row_count_est > 0 else len(rows)
            min_hashes = 2 if row_cnt <= 20 else 10
            for col in tbl.columns:
                hashes: set[bytes] = set()
                for row in rows:
                    val = row.get(col.col_name)
                    if val is None:
                        continue
                    text = str(val).strip()
                    if text:
                        hashes.add(hmac.new(salt, text.encode("utf-8"), hashlib.sha256).digest())
                if hashes:
                    entries.append(
                        (prof.source_id, tbl, col, hashes, min_hashes, _is_target_col(tbl, col))
                    )

    overlaps: list[PkContainmentOverlap] = []
    for i, (s_a, t_a, c_a, h_a, min_a, tgt_a) in enumerate(entries):
        for s_b, t_b, c_b, h_b, min_b, tgt_b in entries[i + 1 :]:
            if t_a.table_id == t_b.table_id:
                continue
            shared = len(h_a & h_b)
            if not shared:
                continue
            ratio_ab = shared / len(h_a)
            ratio_ba = shared / len(h_b)
            cand_ab = tgt_b and len(h_a) >= min_a and ratio_ab >= 0.50
            cand_ba = tgt_a and len(h_b) >= min_b and ratio_ba >= 0.50
            if not cand_ab and not cand_ba:
                continue
            pick_ab = cand_ab and (
                not cand_ba
                or _right_rank(t_b, c_b, h_b, ratio_ab) >= _right_rank(t_a, c_a, h_a, ratio_ba)
            )
            if pick_ab:
                overlaps.append(
                    PkContainmentOverlap(
                        left_source_id=s_a,
                        left_table_id=t_a.table_id,
                        left_col=c_a.col_name,
                        right_source_id=s_b,
                        right_table_id=t_b.table_id,
                        right_col=c_b.col_name,
                        overlap_ratio=round(min(1.0, ratio_ab), 6),
                        compared_hashes=len(h_a),
                    )
                )
            else:
                overlaps.append(
                    PkContainmentOverlap(
                        left_source_id=s_b,
                        left_table_id=t_b.table_id,
                        left_col=c_b.col_name,
                        right_source_id=s_a,
                        right_table_id=t_a.table_id,
                        right_col=c_a.col_name,
                        overlap_ratio=round(min(1.0, ratio_ba), 6),
                        compared_hashes=len(h_b),
                    )
                )

    overlaps.sort(
        key=lambda o: (o.left_table_id, o.left_col, o.right_table_id, o.right_col)
    )
    return overlaps


__all__ = ["compute_pk_containment_overlaps"]
