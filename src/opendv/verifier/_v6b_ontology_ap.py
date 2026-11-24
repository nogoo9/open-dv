"""Gate V6b upstream ontology anti-patterns (ONT-AP-001..005) & shared PII regexes.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile

SOURCE_PREFIX_RE = re.compile(r"(?i)^(?:hub_)?(?:sfdc|sap|erp|crm|csv|parquet|tbl)_")
PII_COL_RE = re.compile(
    r"("
    r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"
    r"(?i:ssn|tin|ein|dob|zip|aba|"
    r"tax_?id|acct_?num|account_?number|holder_?name|credit_?card|card_?number)"
    r"(?:(?![A-Za-z0-9])|(?=[A-Z]))"
    r"|(?i:passport|birth|email|phone|mobile|address|street|postal|"
    r"salary|wage|iban|beneficiary|routing)"
    r")"
)
SEQ_COL_RE = re.compile(r"(?i)(?:^line_no$|^seq_no$|^item_idx$|_seq$|_line_no$)")
METRIC_COL_RE = re.compile(r"(?i)(?:_amt$|_amount$|_pct$|_qty$|_rate$)")
FLOAT_SQL_RE = re.compile(r"(?i)^(?:DOUBLE|FLOAT|REAL|FLOAT4|FLOAT8)$")
ID_GROUP_RE = re.compile(r"(?i)(?:_id$|_cik$|_ticker$|_cusip$|_lei$|_code$)")


def waiver_id(prefix: str, rule_id: str, entity_name: str) -> str:
    """Compute deterministic 8-hex suggested waiver identifier."""
    digest = hashlib.sha256(f"{rule_id}:{entity_name}".encode()).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _ont_diag(
    rule_id: str,
    entity_name: str,
    concept_uri: str | None,
    message: str,
) -> VerifierDiagnostic:
    return VerifierDiagnostic(
        gate="V6b",
        rule_id=rule_id,
        severity="WARN",
        entity_name=entity_name,
        concept_uri=concept_uri,
        waivable=True,
        suggested_waiver_id=waiver_id("ONTW", rule_id, concept_uri or entity_name),
        message=message,
    )


def index_profiles(
    profiles: Sequence[SourceProfile],
) -> tuple[dict[tuple[str, str], ColumnProfile], dict[str, TableProfile]]:
    """Index ColumnProfile and TableProfile objects for V6b rules."""
    cols: dict[tuple[str, str], ColumnProfile] = {}
    tbls: dict[str, TableProfile] = {}
    for prof in profiles:
        for t in prof.tables:
            tbls[t.table_id] = t
            for c in t.columns:
                cols[(t.table_id, c.col_name)] = c
    return cols, tbls


def is_sensitive_col(
    name: str, col_prof: ColumnProfile | None, is_pii_flag: bool = False
) -> bool:
    """Return True if column is tagged PII or matches sensitive column regex."""
    if is_pii_flag:
        return True
    if col_prof is not None and col_prof.pii_tags:
        return True
    return bool(PII_COL_RE.search(name))


def lint_ontology_antipatterns(
    ontology: GraphAOntology,
    profiles: Sequence[SourceProfile] = (),
    *,
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
) -> list[VerifierDiagnostic]:
    """Detect upstream ONT-AP-001..005 smells on (GraphAOntology, SourceProfile)."""
    diags: list[VerifierDiagnostic] = []
    col_idx, tbl_idx = index_profiles(profiles)
    enum_names = {e.enumeration_name.lower() for e in ontology.enumerations}
    cdc = cdc_volatility_by_table or {}

    concept_tokens: list[tuple[CanonicalBusinessConcept, set[str]]] = []
    for c in ontology.concepts:
        if SOURCE_PREFIX_RE.search(c.concept_name):
            diags.append(
                _ont_diag(
                    "ONT-AP-001",
                    c.concept_name,
                    c.concept_uri,
                    f"Concept '{c.concept_name}' carries a source/format prefix token.",
                )
            )
        toks = {a.name.lower() for a in c.attributes} | {
            pat.lower() for kp in c.key_authority_patterns for pat in kp.column_patterns
        }
        concept_tokens.append((c, toks))

        bound_tables = {tb.table_id for tb in c.table_bindings}
        if len(c.attributes) >= 15 and len(bound_tables) == 1:
            t_id = next(iter(bound_tables))
            tbl = tbl_idx.get(t_id)
            id_cols = {
                col.col_name
                for col in (tbl.columns if tbl else [])
                if col.is_pk or ID_GROUP_RE.search(col.col_name)
            }
            if len(id_cols) >= 2:
                diags.append(
                    _ont_diag(
                        "ONT-AP-001",
                        c.concept_name,
                        c.concept_uri,
                        f"'{c.concept_name}' clones {len(c.attributes)} attrs from '{t_id}'.",
                    )
                )

    for idx, (c1, t1) in enumerate(concept_tokens):
        for c2, t2 in concept_tokens[idx + 1 :]:
            if len(t1) >= 2 and len(t2) >= 2:
                union = t1 | t2
                jaccard = len(t1 & t2) / len(union) if union else 0.0
                if jaccard >= 0.70:
                    diags.append(
                        _ont_diag(
                            "ONT-AP-001",
                            c1.concept_name,
                            c1.concept_uri,
                            f"'{c1.concept_name}' and '{c2.concept_name}' overlap {jaccard:.0%}.",
                        )
                    )

    for c in ontology.concepts:
        bk_attrs = [a for a in c.attributes if a.is_business_key]
        if len(bk_attrs) >= 4:
            diags.append(
                _ont_diag(
                    "ONT-AP-003",
                    c.concept_name,
                    c.concept_uri,
                    f"Kitchen-sink business key ({len(bk_attrs)} BKs) on '{c.concept_name}'.",
                )
            )
        for kp in c.key_authority_patterns:
            if kp.authority_name.upper() in ("MONOTONIC_INT", "RANDOM_UUID_V4"):
                diags.append(
                    _ont_diag(
                        "ONT-AP-003",
                        c.concept_name,
                        c.concept_uri,
                        f"Fragile key pattern '{kp.authority_name}' on '{c.concept_name}'.",
                    )
                )

        if c.concept_name.lower() not in enum_names:
            for tb in c.table_bindings:
                cp = col_idx.get((tb.table_id, tb.col_name))
                tp = tbl_idx.get(tb.table_id)
                is_seq_metric = bool(
                    SEQ_COL_RE.search(tb.col_name) or METRIC_COL_RE.search(tb.col_name)
                )
                is_small_lookup = (
                    cp is not None
                    and cp.distinct_count_est <= 50
                    and not cp.is_pk
                    and len(c.attributes) <= 2
                    and (
                        bool(
                            re.search(
                                r"(?i)(?:_code|_status|_type|_tier|_grade|_category)$",
                                tb.col_name,
                            )
                        )
                        or (tp is not None and tp.row_count_est > cp.distinct_count_est)
                    )
                )
                if is_seq_metric or is_small_lookup:
                    diags.append(
                        _ont_diag(
                            "ONT-AP-002",
                            c.concept_name,
                            c.concept_uri,
                            f"Concept '{c.concept_name}' wraps lookup/seq/metric '{tb.col_name}'.",
                        )
                    )
                    break

        for tb in c.table_bindings:
            cp = col_idx.get((tb.table_id, tb.col_name))
            is_bk_col = any(a.name == tb.col_name and a.is_business_key for a in c.attributes) or (
                cp is not None and cp.is_pk
            )
            if not is_bk_col or cp is None:
                continue
            avg_len = (
                sum(len(s) for s in cp.sample_values if s is not None)
                / max(1, sum(1 for s in cp.sample_values if s is not None))
                if cp.sample_values
                else 0.0
            )
            lam = cdc.get(tb.table_id, {}).get(tb.col_name, 0.0)
            if (
                FLOAT_SQL_RE.match(cp.sql_type.strip())
                or cp.null_ratio > 0.01
                or avg_len > 64.0
                or lam > 0.0
            ):
                diags.append(
                    _ont_diag(
                        "ONT-AP-003",
                        c.concept_name,
                        c.concept_uri,
                        f"Fragile BK '{tb.table_id}.{tb.col_name}' on '{c.concept_name}'.",
                    )
                )

        has_pii_gov = "PII_ISOLATION" in {g.upper() for g in c.governance_tags} or any(
            "PII_ISOLATION" in str(v).upper() for v in c.governance_constraints.values()
        )
        if not has_pii_gov:
            pii_hits: list[str] = []
            for attr in c.attributes:
                if is_sensitive_col(attr.name, None, attr.is_pii):
                    pii_hits.append(attr.name)
            for tb in c.table_bindings:
                cp = col_idx.get((tb.table_id, tb.col_name))
                if is_sensitive_col(tb.col_name, cp):
                    pii_hits.append(tb.col_name)
            if pii_hits:
                diags.append(
                    _ont_diag(
                        "ONT-AP-005",
                        c.concept_name,
                        c.concept_uri,
                        f"Concept '{c.concept_name}' has PII {sorted(set(pii_hits))} "
                        "without PII_ISOLATION.",
                    )
                )

    rel_deg: dict[str, int] = {c.concept_uri: 0 for c in ontology.concepts}
    for rel in ontology.relationships:
        if rel.source_concept_uri in rel_deg:
            rel_deg[rel.source_concept_uri] += 1
        if rel.target_concept_uri in rel_deg:
            rel_deg[rel.target_concept_uri] += 1

    concept_tables: dict[str, set[str]] = {
        c.concept_uri: {tb.table_id for tb in c.table_bindings} for c in ontology.concepts
    }
    table_fks: dict[str, set[str]] = {}
    for tbl_id, tbl in tbl_idx.items():
        table_fks[tbl_id] = {
            col.fk_ref.split(".", 1)[0] for col in tbl.columns if col.fk_ref
        }

    for c in ontology.concepts:
        deg = rel_deg.get(c.concept_uri, 0)
        if deg >= 8:
            diags.append(
                _ont_diag(
                    "ONT-AP-004",
                    c.concept_name,
                    c.concept_uri,
                    f"Radial starburst: '{c.concept_name}' has {deg} relationships.",
                )
            )
        elif deg == 0 and len(ontology.concepts) >= 2:
            c_tbls = concept_tables.get(c.concept_uri, set())
            c_cols = {
                col.col_name
                for tid in c_tbls
                if tid in tbl_idx
                for col in tbl_idx[tid].columns
            }
            for other in ontology.concepts:
                if other.concept_uri == c.concept_uri:
                    continue
                o_tbls = concept_tables.get(other.concept_uri, set())
                shared_tbl = bool(c_tbls & o_tbls)
                fk_linked = any(
                    o_tid.split(":", 1)[-1] in table_fks.get(c_tid, set())
                    or o_tid in table_fks.get(c_tid, set())
                    or c_tid.split(":", 1)[-1] in table_fks.get(o_tid, set())
                    or c_tid in table_fks.get(o_tid, set())
                    for c_tid in c_tbls
                    for o_tid in o_tbls
                )
                o_cols = {
                    col.col_name
                    for tid in o_tbls
                    if tid in tbl_idx
                    for col in tbl_idx[tid].columns
                }
                containment = (
                    len(c_cols & o_cols) / min(len(c_cols), len(o_cols))
                    if c_cols and o_cols
                    else 0.0
                )
                if shared_tbl or fk_linked or containment >= 0.80:
                    diags.append(
                        _ont_diag(
                            "ONT-AP-004",
                            c.concept_name,
                            c.concept_uri,
                            f"Orphan '{c.concept_name}' (deg=0) joins with '{other.concept_name}'.",
                        )
                    )
                    break

    return diags


__all__ = [
    "FLOAT_SQL_RE",
    "SEQ_COL_RE",
    "SOURCE_PREFIX_RE",
    "index_profiles",
    "is_sensitive_col",
    "lint_ontology_antipatterns",
    "waiver_id",
]
