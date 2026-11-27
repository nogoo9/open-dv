"""Gate V6b upstream ontology anti-patterns (ONT-AP-001..005) & shared PII regexes.

Zero duckdb, sqlglot, or opendv.sources imports.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from opendv.models.diagnostics import AntiPatternJustification, VerifierDiagnostic
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences
from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.pii_tokenizer import RE_PII_COLUMN
from opendv.models.policy import CEGAR_POLICY_SPECS, VALID_V6B_RULE_IDS, CegarPolicy
from opendv.models.project import OntologyWaiverSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile

SOURCE_PREFIX_RE = re.compile(r"(?i)^(?:hub_)?(?:sfdc|sap|erp|crm|csv|parquet|tbl)_")
SEQ_COL_RE = re.compile(r"(?i)(?:^line_no$|^seq_no$|^item_idx$|_seq$|_line_no$)")
METRIC_COL_RE = re.compile(r"(?i)(?:_amt$|_amount$|_pct$|_qty$|_rate$)")
FLOAT_SQL_RE = re.compile(r"(?i)^(?:DOUBLE|FLOAT|REAL|FLOAT4|FLOAT8)$")
ID_GROUP_RE = re.compile(r"(?i)(?:_id$|_cik$|_ticker$|_cusip$|_lei$|_code$)")


def is_diagnostic_waived(
    diag: VerifierDiagnostic,
    *,
    justifications: Sequence[AntiPatternJustification] = (),
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
) -> bool:
    """Return True if a waivable V6b diagnostic has an accepted human/V7 or ontology waiver."""
    if not diag.waivable:
        return False
    for just in justifications:
        if (just.actor == "human" or just.reviewed_by_v7) and (
            (just.rule_id == diag.rule_id and just.entity_name == diag.entity_name)
            or (diag.suggested_waiver_id is not None and just.waiver_id == diag.suggested_waiver_id)
        ):
            return True
    if diag.rule_id.startswith("ONT-AP-"):
        for ow in ontology_waivers:
            if ow.rule_id == diag.rule_id and (
                (diag.concept_uri is not None and ow.concept_uri == diag.concept_uri)
                or (
                    diag.suggested_waiver_id is not None
                    and ow.waiver_id == diag.suggested_waiver_id
                )
            ):
                return True
    return False


def resolve_effective_v6b_blocking_rules(
    policy: CegarPolicy,
    modeling_preferences: ModelingPreferences | None = None,
) -> frozenset[str]:
    """Validate disabled_v6b_blocks and return effective blocking V6b rule IDs."""
    prefs = modeling_preferences or ModelingPreferences()
    disabled = list(prefs.disabled_v6b_blocks)
    unknown = sorted(set(disabled) - VALID_V6B_RULE_IDS)
    if unknown:
        raise OpenDVError(
            f"Unknown V6b rule IDs in disabled_v6b_blocks: {', '.join(unknown)}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    spec = CEGAR_POLICY_SPECS[policy]
    if policy == "strict" and disabled:
        raise OpenDVError(
            "disabled_v6b_blocks must be empty when policy='strict'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if spec.allow_preference_exemptions:
        return spec.blocking_v6b_rules - set(disabled)
    return spec.blocking_v6b_rules


def _is_preference_exempt(diag: VerifierDiagnostic, prefs: ModelingPreferences) -> bool:
    if (
        prefs.surrogate_key_strategy == "allow_with_bkcc_note"
        and diag.rule_id == "DV-AP-003"
        and "surrogate" in diag.message.lower()
        and "float" not in diag.message.lower()
    ):
        return True
    if prefs.low_cardinality_strategy == "keep_in_satellite" and diag.rule_id in (
        "DV-AP-004",
        "ONT-AP-002",
    ):
        return True
    if (
        prefs.link_cardinality_strategy == "allow_transactional_links"
        and diag.rule_id == "DV-AP-007"
    ):
        return True
    return bool(prefs.esat_on_mutable_links == "off" and diag.rule_id == "DV-AP-008")


def apply_v6b_governance(
    diagnostics: Sequence[VerifierDiagnostic],
    *,
    policy: CegarPolicy = "balanced",
    modeling_preferences: ModelingPreferences | None = None,
    justifications: Sequence[AntiPatternJustification] = (),
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
) -> list[VerifierDiagnostic]:
    """Apply policy blocking rules, preference auto-exemptions, and waiver status."""
    prefs = modeling_preferences or ModelingPreferences()
    effective_blocking = resolve_effective_v6b_blocking_rules(policy, prefs)
    spec = CEGAR_POLICY_SPECS[policy]

    governed: list[VerifierDiagnostic] = []
    for diag in diagnostics:
        if diag.severity == "ADVISORY" and not spec.require_zero_unwaived_v6b:
            governed.append(diag)
            continue
        if spec.allow_preference_exemptions and _is_preference_exempt(diag, prefs):
            governed.append(diag.model_copy(update={"severity": "ADVISORY"}))
            continue
        if is_diagnostic_waived(
            diag,
            justifications=justifications,
            ontology_waivers=ontology_waivers,
        ):
            sev = "WARN" if diag.severity == "ADVISORY" else diag.severity
            governed.append(diag.model_copy(update={"severity": sev}))
            continue
        if diag.rule_id in effective_blocking or spec.require_zero_unwaived_v6b:
            governed.append(diag.model_copy(update={"severity": "ERROR"}))
        else:
            governed.append(diag.model_copy(update={"severity": "ADVISORY"}))
    return governed


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
    return bool(RE_PII_COLUMN.search(name))


def lint_ontology_antipatterns(
    ontology: GraphAOntology,
    profiles: Sequence[SourceProfile] = (),
    *,
    cdc_volatility_by_table: Mapping[str, Mapping[str, float]] | None = None,
    policy: CegarPolicy | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    justifications: Sequence[AntiPatternJustification] = (),
    ontology_waivers: Sequence[OntologyWaiverSpec] = (),
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

    if policy is not None:
        return apply_v6b_governance(
            diags,
            policy=policy,
            modeling_preferences=modeling_preferences,
            justifications=justifications,
            ontology_waivers=ontology_waivers,
        )
    if modeling_preferences is not None:
        prefs = modeling_preferences
        return [
            d.model_copy(update={"severity": "ADVISORY"})
            if _is_preference_exempt(d, prefs)
            else d
            for d in diags
        ]
    return diags


__all__ = [
    "FLOAT_SQL_RE",
    "SEQ_COL_RE",
    "SOURCE_PREFIX_RE",
    "apply_v6b_governance",
    "index_profiles",
    "is_diagnostic_waived",
    "is_sensitive_col",
    "lint_ontology_antipatterns",
    "resolve_effective_v6b_blocking_rules",
    "waiver_id",
]
