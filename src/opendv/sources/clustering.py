"""Deterministic MinHash (125 perms: 25 bands x 5 rows) + PK/FK table clusterer."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Final, Literal

from opendv.models.modeling_preferences import TableClusterResult
from opendv.models.sources import TableIndexEntry, TableProfile

NUM_PERMS: Final[int] = 125
NUM_BANDS: Final[int] = 25
ROWS_PER_BAND: Final[int] = 5
JACCARD_THRESHOLD: Final[float] = 0.40
MIN_CLUSTER_SIZE: Final[int] = 10
MAX_CLUSTER_SIZE: Final[int] = 25

_SHARD_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"_[0-9]{4,8}$")
_PRIME: Final[int] = 4294967311
_HASH_COEFFS: Final[tuple[tuple[int, int], ...]] = tuple(
    (
        int.from_bytes(hashlib.sha256(f"a:{i}".encode()).digest()[:4], "big") | 1,
        int.from_bytes(hashlib.sha256(f"b:{i}".encode()).digest()[:4], "big"),
    )
    for i in range(NUM_PERMS)
)


def collapse_shard_name(table_name: str) -> str:
    """Collapse sibling date/shard suffixes (`_[0-9]{4,8}$`) to a canonical base name."""
    return _SHARD_SUFFIX_RE.sub("", table_name.strip())


def _normalize_sql_type(sql_type: str) -> str:
    base = sql_type.strip().upper().split("(")[0].strip()
    if base in ("VARCHAR", "TEXT", "STRING", "CHAR", "NVARCHAR", "CLOB"):
        return "STR"
    if base in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "INT32", "INT64"):
        return "INT"
    if base in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "NUMBER"):
        return "NUM"
    if base in ("TIMESTAMP", "TIMESTAMPTZ", "DATETIME"):
        return "TS"
    return base or "STR"


def _table_feature_bag(table: TableProfile) -> frozenset[int]:
    tokens: set[str] = set()
    for col in table.columns:
        c_norm = col.col_name.strip().lower()
        t_norm = _normalize_sql_type(col.sql_type)
        tokens.add(f"{c_norm}:{t_norm}")
    if not tokens:
        tokens.add(f"__empty__:{collapse_shard_name(table.table_name).lower()}")
    return frozenset(
        int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=4).digest(), "big")
        for tok in tokens
    )


def _minhash_signature(bag: frozenset[int]) -> list[int]:
    sig = [_PRIME] * NUM_PERMS
    for h in bag:
        for i, (a, b) in enumerate(_HASH_COEFFS):
            val = (a * h + b) % _PRIME
            if val < sig[i]:
                sig[i] = val
    return sig


def _sig_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    matches = sum(1 for x, y in zip(sig_a, sig_b, strict=True) if x == y)
    return matches / float(NUM_PERMS)


def _split_oversized_component(
    comp: list[str],
    adj: dict[str, dict[str, float]],
    group_weights: dict[str, int],
) -> list[list[str]]:
    """Split components > MAX_CLUSTER_SIZE on lowest-weight bridges."""
    total_w = sum(group_weights[g] for g in comp)
    if total_w <= MAX_CLUSTER_SIZE or len(comp) <= 1:
        return [comp]

    # Greedy seeded bipartition minimizing cut weight while aiming near balanced sizes <= 25
    ordered = sorted(comp)
    best_cut_pair: tuple[list[str], list[str]] | None = None
    best_cut_score = float("inf")

    seeds = list(ordered[: min(len(ordered), 6)])
    heaviest = max(ordered, key=lambda g: (group_weights[g], g))
    if heaviest not in seeds:
        seeds.append(heaviest)

    for seed in seeds:
        part_a: list[str] = [seed]
        set_a = {seed}
        weight_a = group_weights[seed]
        target_a = min(MAX_CLUSTER_SIZE, max(MIN_CLUSTER_SIZE, total_w // 2))

        while weight_a < target_a and len(set_a) < len(ordered):
            candidates = [n for n in ordered if n not in set_a]
            if not candidates:
                break
            next_node = max(
                candidates,
                key=lambda n: (
                    sum(adj[n].get(a, 0.0) for a in set_a)
                    - 0.25 * sum(adj[n].get(b, 0.0) for b in candidates if b != n),
                    -ord(n[0]) if n else 0,
                ),
            )
            if (
                weight_a + group_weights[next_node] > MAX_CLUSTER_SIZE
                and weight_a >= MIN_CLUSTER_SIZE
            ):
                break
            part_a.append(next_node)
            set_a.add(next_node)
            weight_a += group_weights[next_node]

        part_b = [n for n in ordered if n not in set_a]
        if not part_b:
            continue
        weight_b = sum(group_weights[x] for x in part_b)
        cut_weight = sum(adj[a].get(b, 0.0) for a in part_a for b in part_b)
        diff_w = abs(weight_a - weight_b)
        undersize_penalty = (
            1000.0
            if min(weight_a, weight_b) < MIN_CLUSTER_SIZE
            and total_w >= 2 * MIN_CLUSTER_SIZE
            else 0.0
        )
        oversize_penalty = 1000.0 if weight_a > MAX_CLUSTER_SIZE else 0.0
        balance_penalty = 0.01 * diff_w
        score = cut_weight + undersize_penalty + oversize_penalty + balance_penalty
        if score < best_cut_score:
            best_cut_score = score
            best_cut_pair = (sorted(part_a), sorted(part_b))

    if best_cut_pair is None:
        mid = max(1, len(ordered) // 2)
        left, right = ordered[:mid], ordered[mid:]
    else:
        left, right = best_cut_pair

    return _split_oversized_component(left, adj, group_weights) + _split_oversized_component(
        right, adj, group_weights
    )


def cluster_tables(tables: Sequence[TableProfile]) -> TableClusterResult:
    """Cluster TableProfiles deterministically using 125-perm MinHash + PK/FK edges."""
    if not tables:
        return TableClusterResult(clusters=[], table_to_cluster={}, shard_groups={})

    sorted_tables = sorted(tables, key=lambda t: (t.table_name, t.table_id))
    shard_groups = {t.table_id: collapse_shard_name(t.table_name) for t in sorted_tables}

    if len(sorted_tables) < MIN_CLUSTER_SIZE:
        ids = [t.table_id for t in sorted_tables]
        return TableClusterResult(
            clusters=[ids],
            table_to_cluster=dict.fromkeys(ids, "cluster_001"),
            shard_groups=shard_groups,
        )

    # Collapse sibling shards into canonical schema groups
    group_tables: dict[str, list[TableProfile]] = defaultdict(list)
    for t in sorted_tables:
        group_tables[collapse_shard_name(t.table_name)].append(t)

    group_names = sorted(group_tables.keys())
    group_weights = {g: len(group_tables[g]) for g in group_names}
    group_bags: dict[str, frozenset[int]] = {}
    group_sigs: dict[str, list[int]] = {}
    for g in group_names:
        union_bag: set[int] = set()
        for t in group_tables[g]:
            union_bag.update(_table_feature_bag(t))
        f_bag = frozenset(union_bag)
        group_bags[g] = f_bag
        group_sigs[g] = _minhash_signature(f_bag)

    # LSH banding (25 bands x 5 rows)
    buckets: dict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)
    for g in group_names:
        sig = group_sigs[g]
        for b in range(NUM_BANDS):
            start = b * ROWS_PER_BAND
            band_key = (b, tuple(sig[start : start + ROWS_PER_BAND]))
            buckets[band_key].append(g)

    adj: dict[str, dict[str, float]] = {g: {} for g in group_names}
    candidate_pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i, ga in enumerate(members):
            for gb in members[i + 1 :]:
                pair = (ga, gb) if ga < gb else (gb, ga)
                candidate_pairs.add(pair)

    for ga, gb in candidate_pairs:
        sim = _sig_jaccard(group_sigs[ga], group_sigs[gb])
        if sim >= JACCARD_THRESHOLD:
            adj[ga][gb] = sim
            adj[gb][ga] = sim

    # Union with PK/FK edges (weight 2.0 to prefer keeping FK-linked tables together)
    name_to_group = {t.table_name: collapse_shard_name(t.table_name) for t in sorted_tables}
    for t in sorted_tables:
        src_g = collapse_shard_name(t.table_name)
        for col in t.columns:
            if not col.fk_ref:
                continue
            ref_parts = col.fk_ref.split(".")
            target_tbl = ref_parts[-2] if len(ref_parts) >= 2 else ref_parts[0]
            dst_g = name_to_group.get(target_tbl)
            if dst_g and dst_g != src_g:
                w = max(adj[src_g].get(dst_g, 0.0), 2.0)
                adj[src_g][dst_g] = w
                adj[dst_g][src_g] = w

    # Connected components
    visited: set[str] = set()
    raw_components: list[list[str]] = []
    for g in group_names:
        if g in visited:
            continue
        stack = [g]
        visited.add(g)
        comp: list[str] = []
        while stack:
            curr = stack.pop()
            comp.append(curr)
            for nb in sorted(adj[curr]):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        raw_components.append(sorted(comp))

    # Split components > MAX_CLUSTER_SIZE on lowest-weight bridges
    sized_chunks: list[list[str]] = []
    for comp in raw_components:
        sized_chunks.extend(_split_oversized_component(comp, adj, group_weights))

    # Merge small components (< MIN_CLUSTER_SIZE) into compatible neighbors (<= MAX_CLUSTER_SIZE)
    def _chunk_size(ch: list[str]) -> int:
        return sum(group_weights[g] for g in ch)

    sized_chunks.sort(key=lambda ch: (_chunk_size(ch), ch[0]))
    merged: list[list[str]] = []
    pending: list[str] = []
    for ch in sized_chunks:
        if _chunk_size(ch) >= MIN_CLUSTER_SIZE:
            merged.append(ch)
            continue
        if _chunk_size(pending) + _chunk_size(ch) <= MAX_CLUSTER_SIZE:
            pending.extend(ch)
            if _chunk_size(pending) >= MIN_CLUSTER_SIZE:
                merged.append(sorted(pending))
                pending = []
        else:
            merged.append(sorted(pending))
            pending = list(ch)

    if pending:
        pending_sz = _chunk_size(pending)
        best_idx = min(
            (
                i
                for i, m in enumerate(merged)
                if _chunk_size(m) + pending_sz <= MAX_CLUSTER_SIZE
            ),
            key=lambda i: (_chunk_size(merged[i]), merged[i][0]),
            default=None,
        )
        if best_idx is not None:
            merged[best_idx] = sorted(merged[best_idx] + pending)
        else:
            merged.append(sorted(pending))

    # Expand groups back to physical table_ids, ordered deterministically
    physical_clusters: list[list[str]] = []
    for ch in merged:
        tids = sorted(t.table_id for g in ch for t in group_tables[g])
        if tids:
            physical_clusters.append(tids)

    physical_clusters.sort(key=lambda c: c[0])
    table_to_cluster: dict[str, str] = {}
    for idx, tids in enumerate(physical_clusters, start=1):
        cid = f"cluster_{idx:03d}"
        for tid in tids:
            table_to_cluster[tid] = cid

    return TableClusterResult(
        clusters=physical_clusters,
        table_to_cluster=table_to_cluster,
        shard_groups=shard_groups,
    )


def build_table_index_entries(
    tables: Sequence[TableProfile],
    source_id: str,
    *,
    badge_map: dict[str, Literal["NEW", "UPDATED", "UNCHANGED"]] | None = None,
    modeled_version: str | None = None,
) -> list[TableIndexEntry]:
    """Produce deterministic MinHash-clustered TableIndexEntry DTOs for Step 3a."""
    clustering = cluster_tables(tables)
    entries: list[TableIndexEntry] = []
    for tbl in sorted(tables, key=lambda t: t.table_name):
        pk_cols = [
            c.col_name
            for c in sorted(
                (c for c in tbl.columns if c.is_pk),
                key=lambda c: c.pk_ord or 999,
            )
        ]
        fk_refs = [c.fk_ref for c in tbl.columns if c.fk_ref is not None]
        pii_cnt = sum(1 for c in tbl.columns if c.pii_tags)
        badge: Literal["NEW", "UPDATED", "UNCHANGED"] = (
            badge_map.get(tbl.table_id, "NEW") if badge_map else "NEW"
        )
        entries.append(
            TableIndexEntry(
                table_id=tbl.table_id,
                source_id=source_id,
                table_name=tbl.table_name,
                cluster_id=clustering.table_to_cluster[tbl.table_id],
                col_count=len(tbl.columns),
                pii_col_count=pii_cnt,
                pk_cols=pk_cols,
                fk_refs=fk_refs,
                badge=badge,
                modeled_version=modeled_version,
            )
        )
    return entries


__all__ = [
    "JACCARD_THRESHOLD",
    "MAX_CLUSTER_SIZE",
    "MIN_CLUSTER_SIZE",
    "NUM_BANDS",
    "NUM_PERMS",
    "ROWS_PER_BAND",
    "TableClusterResult",
    "build_table_index_entries",
    "cluster_tables",
    "collapse_shard_name",
]
