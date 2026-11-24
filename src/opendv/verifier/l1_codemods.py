"""L1 deterministic IR codemod orchestrator (S6-03, Alg A.1-A.4, A.9a, Bucket A, Z0-Z4).

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import GraphAOntology
from opendv.models.project import EvolutionLockSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR, PredicateNode
from opendv.models.vault_ir_certified import CertifiedVaultIR, RefTableSpec
from opendv.models.wire_dtos_vault import BucketAOperatorCall
from opendv.verifier._l1_alg_a1 import L1CodemodOptions, apply_alg_a1_satellite_split
from opendv.verifier._l1_bucket_a import apply_bucket_a_operators
from opendv.verifier._l1_drift_and_bucket_a import apply_alg_a9a_schema_drift
from opendv.verifier._l1_promote import promote_draft_to_certified
from opendv.verifier._l1_rules_alg import (
    apply_alg_a2_ref_demotion,
    apply_alg_a3_esat_and_mas,
    apply_alg_a4_bkcc_and_sparse_fks,
)
from opendv.verifier._l1_shard_triage import inject_unresolved_triage, merge_draft_shards
from opendv.verifier.ports import BucketAExecutionResult


def _draft_digest(draft: DraftVaultIR) -> str:
    return sha256_jcs(draft.model_dump(mode="json"))


def run_l1_codemods(
    draft: DraftVaultIR,
    *,
    profiles: Sequence[SourceProfile] = (),
    ontology: GraphAOntology | None = None,
    locks: EvolutionLockSpec | None = None,
    prior_ir: CertifiedVaultIR | None = None,
    bucket_a_ops: Sequence[BucketAOperatorCall] = (),
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    entity_count_overrides: Mapping[str, int] | None = None,
    options: L1CodemodOptions | None = None,
    max_passes: int = 3,
) -> tuple[DraftVaultIR, CertifiedVaultIR, BucketAExecutionResult | None]:
    """Run S6-03 triage, optional Bucket A ops, 3-pass L1 fixpoint, and Z0-Z4 promotion.

    Pass order inside each bounded iteration (max_passes=3):
        A.9a -> A.4 -> A.2 -> A.3 -> A.1
    """
    opts = options or L1CodemodOptions()
    working = inject_unresolved_triage(draft, profiles)

    bucket_res: BucketAExecutionResult | None = None
    protected_split_sats: set[str] = set()
    if bucket_a_ops:
        bucket_res = apply_bucket_a_operators(working, bucket_a_ops, locks)
        working = bucket_res.draft_ir
        for call in bucket_a_ops:
            if call.operator == "SPLIT_HIGH_FREQ_OR_PII_SAT":
                if call.sat_name:
                    protected_split_sats.add(call.sat_name)
                new_sn = call.params.get("new_sat_name", "").strip()
                if new_sn:
                    protected_split_sats.add(new_sn)

    all_refs: dict[str, RefTableSpec] = {}
    link_filters: dict[str, PredicateNode] = {}
    z1_overrides: dict[str, dict[str, str]] = {}

    passes = max(1, min(max_passes, 3))
    for _ in range(passes):
        before_sha = _draft_digest(working)

        working, pass_z1 = apply_alg_a9a_schema_drift(
            working, locks, prior_ir=prior_ir, profiles=profiles
        )
        for tbl_id, mapping in pass_z1.items():
            z1_overrides.setdefault(tbl_id, {}).update(mapping)

        working, pass_filters = apply_alg_a4_bkcc_and_sparse_fks(
            working, profiles, locks, options=opts
        )
        link_filters.update(pass_filters)

        working, pass_refs = apply_alg_a2_ref_demotion(
            working,
            profiles,
            ontology,
            locks,
            max_enum_cardinality=opts.max_enum_cardinality,
        )
        for ref in pass_refs:
            all_refs[ref.ref_name] = ref

        working = apply_alg_a3_esat_and_mas(working, fk_turnover_by_table)

        working = apply_alg_a1_satellite_split(
            working,
            profiles,
            locks,
            options=opts,
            cdc_volatility_by_table=cdc_volatility_by_table,
            entity_count_overrides=entity_count_overrides,
            protected_sat_names=protected_split_sats,
        )

        if _draft_digest(working) == before_sha:
            break

    certified = promote_draft_to_certified(
        working,
        profiles=profiles,
        locks=locks,
        prior_ir=prior_ir,
        ref_tables=[all_refs[k] for k in sorted(all_refs.keys())],
        link_filters=link_filters,
        z1_lineage_overrides=z1_overrides,
        dedup_order_cols_by_sat=bucket_res.dedup_order_cols_by_sat if bucket_res else None,
    )
    return working, certified, bucket_res


__all__ = [
    "L1CodemodOptions",
    "apply_bucket_a_operators",
    "inject_unresolved_triage",
    "merge_draft_shards",
    "promote_draft_to_certified",
    "run_l1_codemods",
]
