"""Gate V1: Storage-neutral Z0..Z4 bipartite DAG topology and column triage checks.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.ontology import GraphAOntology
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.verifier._v1_bridge_rules import (
    are_hubs_sal_connected,
    source_cols_by_table,
    validate_link_participant_bindings,
    validate_pit_bridges,
    validate_survivorship_and_z4_types,
)
from opendv.verifier._v1_dag_rules import check_v1_dag_rules, v1_diag


def _validate_hub_inv05_and_raw_bks(
    ir: CertifiedVaultIR,
    profiles: Sequence[SourceProfile],
) -> list[VerifierDiagnostic]:
    diags: list[VerifierDiagnostic] = []
    src_cols_by_tbl = source_cols_by_table(ir, profiles)
    stg_by_tbl = {stg.source_table_id: stg for stg in ir.staging_views}
    canon_to_raws: dict[str, dict[str, set[str]]] = {}
    raw_to_canons: dict[str, dict[str, set[str]]] = {}

    for h in ir.hubs:
        if h.attributes:
            diags.append(
                v1_diag(
                    "INV-05",
                    "BLOCKER",
                    h.hub_name,
                    f"Hub {h.hub_name} violates INV-05: Hub attributes must be empty.",
                )
            )
        if h.is_planned_concept:
            continue
        for bk in h.business_keys:
            tbl_cols = src_cols_by_tbl.get(bk.table_id)
            if tbl_cols is not None and bk.col_name not in tbl_cols:
                diags.append(
                    v1_diag(
                        "DV-COL-002",
                        "BLOCKER",
                        h.hub_name,
                        f"Hub {h.hub_name} raw BK '{bk.col_name}' missing from '{bk.table_id}'.",
                    )
                )
            canon = bk.canonical_col_name or bk.col_name
            canon_to_raws.setdefault(bk.table_id, {}).setdefault(canon, set()).add(bk.col_name)
            raw_to_canons.setdefault(bk.table_id, {}).setdefault(bk.col_name, set()).add(canon)
            stg = stg_by_tbl.get(bk.table_id)
            if stg is not None and bk.canonical_col_name and bk.canonical_col_name != bk.col_name:
                mapped = stg.col_lineage_mappings.get(bk.canonical_col_name)
                if mapped is not None and mapped != bk.col_name:
                    diags.append(
                        v1_diag(
                            "DV-COL-002",
                            "BLOCKER",
                            stg.view_name,
                            f"StagingViewSpec {stg.view_name} canonical BK "
                            f"'{bk.canonical_col_name}' <- '{bk.col_name}' collides with "
                            f"'{mapped}'.",
                        )
                    )
                elif tbl_cols is not None and bk.canonical_col_name in tbl_cols:
                    diags.append(
                        v1_diag(
                            "DV-COL-002",
                            "BLOCKER",
                            stg.view_name,
                            f"StagingViewSpec {stg.view_name} canonical BK "
                            f"'{bk.canonical_col_name}' collides with source column.",
                        )
                    )

    for tbl_id, c_map in canon_to_raws.items():
        entity = stg_by_tbl[tbl_id].view_name if tbl_id in stg_by_tbl else tbl_id
        for canon, raws in c_map.items():
            if len(raws) > 1:
                diags.append(
                    v1_diag(
                        "DV-COL-002",
                        "BLOCKER",
                        entity,
                        f"Staging {entity} ({tbl_id}) maps multiple raw BKs {sorted(raws)} "
                        f"to '{canon}'.",
                    )
                )
        for raw, canons in raw_to_canons.get(tbl_id, {}).items():
            if len(canons) > 1:
                diags.append(
                    v1_diag(
                        "DV-COL-002",
                        "BLOCKER",
                        entity,
                        f"Staging {entity} ({tbl_id}) maps raw BK '{raw}' to conflicting "
                        f"canonical columns {sorted(canons)}.",
                    )
                )
    return diags


def _norm_slug(name: str) -> str:
    low = name.strip().lower()
    for prefix in ("hub_", "lnk_", "t_lnk_", "sal_", "sat_", "mas_", "esat_", "ref_"):
        if low.startswith(prefix):
            low = low[len(prefix) :]
            break
    if low.endswith("_canonical"):
        low = low[: -len("_canonical")]
    return low.replace("_", "").replace("-", "").replace(" ", "")


def verify_v1_structure(
    ir: CertifiedVaultIR,
    *,
    profiles: Sequence[SourceProfile] = (),
    ontology: GraphAOntology | None = None,
) -> list[VerifierDiagnostic]:
    """Run Gate V1 symbolic DAG topology and column triage checks."""
    diags = check_v1_dag_rules(ir)
    diags.extend(_validate_hub_inv05_and_raw_bks(ir, profiles))
    diags.extend(validate_survivorship_and_z4_types(ir))
    diags.extend(validate_pit_bridges(ir))
    diags.extend(validate_link_participant_bindings(ir, profiles))

    # DV-STR-DUP: reject duplicate entity names in CertifiedVaultIR
    for kind, names in (
        ("Hub", [h.hub_name for h in ir.hubs]),
        ("Link", [lnk.link_name for lnk in ir.links]),
        ("Satellite", [s.sat_name for s in ir.satellites]),
    ):
        seen_names: set[str] = set()
        reported_dups: set[str] = set()
        for name in names:
            if name in seen_names and name not in reported_dups:
                reported_dups.add(name)
                diags.append(
                    v1_diag(
                        "DV-STR-DUP",
                        "BLOCKER",
                        name,
                        f"Duplicate {kind} name '{name}' in CertifiedVaultIR.",
                    )
                )
            seen_names.add(name)

    # PHANTOM_HUB_REJECTED & DV-COL-002 (non-planned hubs)
    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        if h.physical_hints is None:
            diags.append(
                v1_diag(
                    "PHANTOM_HUB_REJECTED",
                    "BLOCKER",
                    h.hub_name,
                    f"Hub {h.hub_name} lacks physical_hints.",
                )
            )
        if not h.business_keys:
            diags.append(
                v1_diag(
                    "DV-COL-002",
                    "BLOCKER",
                    h.hub_name,
                    f"Non-planned Hub {h.hub_name} has zero BusinessKeyParts.",
                )
            )
        has_triage_ref = any(e.target_ref == h.hub_name for e in ir.column_triage)
        if not h.source_bindings and not has_triage_ref:
            diags.append(
                v1_diag(
                    "PHANTOM_HUB_REJECTED",
                    "BLOCKER",
                    h.hub_name,
                    f"Hub {h.hub_name} has no physical source_bindings or triage mappings.",
                )
            )

    # DV-COL-001: 100% column triage, 0 UNRESOLVED, hk_* FIXED_BINARY_32 not null
    triage_keys = {(e.table_id, e.col_name): e for e in ir.column_triage}
    for prof in profiles:
        for tbl in prof.tables:
            for col in tbl.columns:
                if (tbl.table_id, col.col_name) not in triage_keys:
                    diags.append(
                        v1_diag(
                            "DV-COL-001",
                            "BLOCKER",
                            tbl.table_id,
                            f"Unaccounted Graph B column '{tbl.table_id}.{col.col_name}'.",
                        )
                    )
    for entry in ir.column_triage:
        if entry.disposition == "UNRESOLVED":
            diags.append(
                v1_diag(
                    "DV-COL-001",
                    "BLOCKER",
                    entry.table_id,
                    f"Column '{entry.table_id}.{entry.col_name}' remains UNRESOLVED.",
                )
            )
        elif entry.disposition == "OMITTED_INTENTIONALLY" and len(entry.rationale.strip()) < 15:
            diags.append(
                v1_diag(
                    "DV-COL-001",
                    "BLOCKER",
                    entry.table_id,
                    f"OMITTED_INTENTIONALLY on '{entry.table_id}.{entry.col_name}' "
                    "needs >= 15 chars.",
                )
            )

    for h in ir.hubs:
        if h.is_planned_concept:
            continue
        for hk in (c for c in h.columns if c.name.startswith("hk_")):
            if hk.nullable or hk.logical_type != "FIXED_BINARY_32":
                diags.append(
                    v1_diag(
                        "DV-COL-001",
                        "BLOCKER",
                        h.hub_name,
                        f"Primary key {h.hub_name}.{hk.name} must be non-null FIXED_BINARY_32.",
                    )
                )

    # ANTI_SILO_VIOLATION & DV-G4-001 (ontology grounding check)
    if ontology is not None:
        concept_by_slug = {_norm_slug(c.concept_name): c for c in ontology.concepts}
        concept_by_binding = {
            (tb.source_id, tb.table_id, tb.col_name): c
            for c in ontology.concepts
            for tb in c.table_bindings
        }
        uri_to_hubs: dict[str, list[str]] = {}
        for h in ir.hubs:
            matched_c = concept_by_slug.get(_norm_slug(h.hub_name))
            if matched_c is None:
                for bk in h.business_keys:
                    matched_c = concept_by_binding.get((bk.source_id, bk.table_id, bk.col_name))
                    if matched_c is not None:
                        break
            if matched_c is None:
                if not h.is_planned_concept and not h.hub_name.endswith("_canonical"):
                    diags.append(
                        v1_diag(
                            "DV-G4-001",
                            "ERROR",
                            h.hub_name,
                            f"Hub {h.hub_name} has no matching concept in GraphAOntology.",
                        )
                    )
            else:
                uri_to_hubs.setdefault(matched_c.concept_uri, []).append(h.hub_name)

        for uri, mapped_hubs in uri_to_hubs.items():
            non_canon = [hn for hn in mapped_hubs if not hn.endswith("_canonical")]
            if len(non_canon) > 1 and not are_hubs_sal_connected(ir, non_canon):
                diags.append(
                    v1_diag(
                        "ANTI_SILO_VIOLATION",
                        "ERROR",
                        non_canon[0],
                        f"Multiple Hubs ({', '.join(non_canon)}) bind same concept_uri '{uri}'.",
                        concept_uri=uri,
                    )
                )

    return diags


__all__ = ["verify_v1_structure"]
