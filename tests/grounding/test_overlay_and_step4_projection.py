"""M2.1 tests for calibration, iso_codes.json, ProjectGraphASource & OverlayKnowledgeSource."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal

import pytest

from opendv.grounding import KnowledgeSource
from opendv.grounding.kernel import (
    CalibrationOutcome,
    CandidateChannelInputs,
    ColumnCalibrationContext,
    OverlayKnowledgeSource,
    ProjectGraphASource,
    compute_active_channels,
    compute_logit_normal_ci,
    compute_observed_channels,
    compute_phi_agent,
    compute_phi_anchor,
    compute_phi_lex,
    compute_sigma_disagree,
    compute_sigma_eff,
    determine_suggested_predicate,
    load_iso_codes,
    normalize_column_tokens,
    normalize_property_label,
    select_and_calibrate,
    softmax_with_null,
)
from opendv.grounding.kernel.lexical import LexicalIndex
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    InferredValueProfile,
    SemanticContext,
    TableAnchorCandidate,
)
from opendv.models.grounding_spi import (
    BackendReasoningResult,
    CalibrationParams,
    KnowledgeItem,
    KnowledgeSourceMetadata,
    RecallHit,
    TableReasoningContext,
    TableRerankAssignment,
)
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    ExternalMapping,
    GraphAOntology,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.project import ConceptMemorySpec
from opendv.models.sources import ColumnShapeStats
from opendv.service._guided_errors import contains_forbidden_jargon

_FIBO_LE = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
_FIBO_LEI = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LEIEntities/LegalEntityIdentifier"
_FIBO_CCY = "https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/CurrencyAmount/Currency"


class _StubBaseSource:
    """Minimal in-memory KnowledgeSource test double for OverlayKnowledgeSource tests."""

    def __init__(
        self,
        items: Sequence[KnowledgeItem],
        *,
        anchor_entry_kinds: frozenset[str] = frozenset({"class"}),
    ) -> None:
        self._items = {it.entry_id: it for it in items}
        self._index = LexicalIndex()
        self._index.index_items(
            items, origin_source_id="stub-fibo", origin_kind="reference_standard"
        )
        self._anchor_kinds = anchor_entry_kinds
        self.seen_table_contexts: list[TableReasoningContext | None] = []
        self.seen_candidate_ids: list[list[str]] = []

    @property
    def metadata(self) -> KnowledgeSourceMetadata:
        return KnowledgeSourceMetadata(
            source_id="stub-fibo",
            version="2.0.0",
            sha256="a" * 64,
            anchor_entry_kinds=self._anchor_kinds,
            origin_kind="reference_standard",
        )

    @property
    def calibration_params(self) -> CalibrationParams | None:
        return None

    def expand_query_terms(
        self, tokens: Sequence[str], *, context_text: str | None = None
    ) -> list[str]:
        _ = (tokens, context_text)
        return ["base_expanded"]

    def recall_by_text(
        self, query: str, *, entry_kinds: AbstractSet[str] | None = None, top_k: int = 25
    ) -> list[RecallHit]:
        return self._index.match(query, top_k=top_k, entry_kinds=entry_kinds)

    def match_values_or_mentions(
        self,
        surface_forms: Sequence[str],
        *,
        pii_tags: Sequence[str] = (),
        context_hint: str | None = None,
        structural_patterns: Mapping[str, float] | None = None,
    ) -> list[RecallHit]:
        _ = (surface_forms, pii_tags, context_hint, structural_patterns)
        return []

    def get_item(self, entry_id: str) -> KnowledgeItem | None:
        return self._items.get(entry_id)

    def get_ancestors(self, entry_id: str, *, max_depth: int = 8) -> list[str]:
        item = self._items.get(entry_id)
        if item is None:
            return []
        return list(item.parent_ids)[:max_depth]

    def evaluate_structured_candidates(
        self,
        column: ColumnEvidence,
        profile: InferredValueProfile,
        candidates: Sequence[RecallHit],
        *,
        table_context: TableReasoningContext | None = None,
    ) -> list[BackendReasoningResult]:
        _ = (column, profile)
        self.seen_table_contexts.append(table_context)
        self.seen_candidate_ids.append([c.entry_id for c in candidates])
        return [
            BackendReasoningResult(
                entry_id=c.entry_id,
                shape_range_score=0.85,
                hard_veto=False,
                inverse_direction_score=0.3 if table_context and table_context.fk_targets else 0.0,
                semantic_context=self._items[c.entry_id].semantic_context
                if c.entry_id in self._items
                else None,
                support_detail="stub evaluated",
            )
            for c in candidates
        ]

    def score(self, query: str, entry_id: str) -> Decimal:
        return self._index.score(query, entry_id)


def test_iso_codes_table_completeness() -> None:
    """iso_codes.json loads full 249 ISO 3166-1 countries and major ISO 4217 currencies."""
    iso = load_iso_codes()
    assert len(iso.country_alpha2) == 249
    assert len(iso.country_alpha3) == 249
    assert {"US", "GB", "DE", "JP", "SG", "CH", "FR"}.issubset(iso.country_alpha2)
    assert iso.alpha3_to_alpha2["USA"] == "US"
    assert {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "XAU", "XXX"}.issubset(
        iso.currency_codes
    )
    assert len(iso.currency_codes) >= 175
    assert iso.fibo_currency_uri.endswith("/Currency")
    assert iso.fibo_country_uri.endswith("/Country")


def test_calibration_channels_r5_m4_m6_disagree_ci_and_k1_null() -> None:
    """Verify R5-M4 phi_agent monotonicity, R5-M6 M_active, sigma_disagree, CI, and K=1 softmax."""
    params = CalibrationParams.analytic_defaults()

    # R5-M4: monotonic phi_agent across [0, 1], including confidence=0.25 < 1/3
    for conf in (0.0, 0.25, 1.0):
        asgn = TableRerankAssignment(column_name="c", selected_index=0, abstain=False, confidence=conf)
        phi_sel, pen_sel = compute_phi_agent(0, asgn, w_agent=2.0)
        phi_oth, pen_oth = compute_phi_agent(1, asgn, w_agent=2.0)
        assert phi_sel == pytest.approx(conf)
        assert phi_oth == pytest.approx(-conf)
        assert phi_sel >= phi_oth
        assert pen_sel == 0.0 and pen_oth == 0.0

    abstain_asgn = TableRerankAssignment(column_name="c", abstain=True, confidence=0.8)
    phi_abs, pen_abs = compute_phi_agent(0, abstain_asgn, w_agent=2.0)
    assert phi_abs == pytest.approx(-0.4)
    assert pen_abs == pytest.approx(2.0 * 1.3)

    # R5-M6: "agent" in M_active iff rerank_expected and not is_fast_pathed
    cand = CandidateChannelInputs(
        entry_id="e1",
        label="Currency",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="code_classifier"),
        has_surface_rule=True,
    )
    ctx_no_rerank = ColumnCalibrationContext(
        column_name="ccy", n_eff=50, anchor_enabled=True, rerank_expected=False
    )
    assert "agent" not in compute_active_channels(ctx_no_rerank, cand)

    ctx_fast_pathed = ColumnCalibrationContext(
        column_name="ccy", n_eff=50, anchor_enabled=True, rerank_expected=True, is_fast_pathed=True
    )
    assert "agent" not in compute_active_channels(ctx_fast_pathed, cand)

    ctx_rerank = ColumnCalibrationContext(
        column_name="ccy", n_eff=50, anchor_enabled=True, rerank_expected=True, is_fast_pathed=False
    )
    assert "agent" in compute_active_channels(ctx_rerank, cand)

    # List-based sigma_disagree excludes neutral phi_anchor==0.0, preserves equal distinct values
    phi_vals = {"lex": 0.8, "surface": 0.8, "shape_range": -0.2, "anchor": 0.0}
    m_obs = frozenset({"lex", "surface", "shape_range", "anchor"})
    m_act = frozenset({"lex", "surface", "shape_range", "anchor"})
    sigma_d = compute_sigma_disagree(phi_vals, m_obs, m_act)
    assert sigma_d == pytest.approx(0.47140452079, rel=1e-5)

    # n_eff cap at 200
    assert compute_sigma_eff(params, 200, 1.0, 0.0) == pytest.approx(
        compute_sigma_eff(params, 10000, 1.0, 0.0)
    )

    # Logit-Normal CI bounds invariant 0 <= lower <= point <= upper <= 1 even at extremes
    for p in (0.0, 1e-9, 0.5, 1.0 - 1e-9, 1.0):
        ci = compute_logit_normal_ci(p, 0.4, params, ci_level=0.90)
        assert 0.0 <= ci.lower <= ci.point <= ci.upper <= 1.0

    # K=1 softmax preserves P(null) in denominator; hard_veto excluded before softmax
    c_ok = CandidateChannelInputs(
        entry_id="ok",
        label="Issuer LEI",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=0.5,
        shape_range_score=0.2,
    )
    c_veto = CandidateChannelInputs(
        entry_id="veto",
        label="Wrong",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=0.99,
        hard_veto=True,
    )
    scored, p_null, any_veto = softmax_with_null(
        ColumnCalibrationContext(column_name="col", n_eff=10),
        [c_ok, c_veto],
        params,
    )
    assert any_veto is True
    assert len(scored) == 1
    assert scored[0].entry_id == "ok"
    assert 0.0 < p_null < 1.0
    assert scored[0].probability + p_null == pytest.approx(1.0)
    assert compute_phi_lex(1.5) == 1.0
    assert compute_phi_anchor(-0.8, -0.4) == -1.0


def test_calibration_selection_fast_path_predicates_and_jargon() -> None:
    """Verify fast-path corroboration, pruning, predicate stripping, categories & jargon."""
    params = CalibrationParams.analytic_defaults()
    policy = GroundingPolicy(mode="deterministic_only", skip_reason="unit test")

    # Checksum-less pattern uncorroborated blocks fast-path; corroborated allows it
    bic_cand = CandidateChannelInputs(
        entry_id="bic",
        label="swift_bic",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=1.0,
        surface_score=0.6,
        has_surface_rule=True,
        shape_range_score=1.0,
        base_anchor_score=1.0,
        corroborated=False,
    )
    ctx_bic = ColumnCalibrationContext(
        column_name="swift_bic",
        n_eff=200,
        has_column_surface_pattern=True,
        has_checksumless_pattern=True,
        has_checksum_pattern=False,
        anchor_enabled=True,
        has_top_table_anchor=True,
    )
    res_uncorr = select_and_calibrate(ctx_bic, [bic_cand], params, policy)
    assert res_uncorr.fast_path_eligible is False

    bic_corr = CandidateChannelInputs(
        entry_id="bic",
        label="swift_bic",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=1.0,
        surface_score=1.0,
        has_surface_rule=True,
        shape_range_score=1.0,
        base_anchor_score=1.0,
        corroborated=True,
    )
    res_corr = select_and_calibrate(ctx_bic, [bic_corr], params, policy)
    assert res_corr.status == "MATCHED"
    assert res_corr.fast_path_eligible is True
    assert not contains_forbidden_jargon(res_corr.steward_summary)

    # Verb & archetype-gated suffix stripping and predicate selection
    assert normalize_property_label("hasLegalName") == ["legal", "name"]
    assert normalize_property_label("isActive") == ["active"]
    assert normalize_column_tokens("trade_dt", archetype="temporal") == ["trade"]
    assert normalize_column_tokens("currency_cd", archetype="code_classifier") == ["currency"]
    assert normalize_column_tokens("issuer_id", archetype="identifier") == ["issuer"]
    assert normalize_column_tokens("active_flg", archetype=None, is_boolean_datatype=True) == [
        "active"
    ]

    rel_cand = CandidateChannelInputs(
        entry_id="r",
        label="hasLegalName",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        suggested_predicate_hint="related",
    )
    assert determine_suggested_predicate("legal_name", rel_cand, 0.99, "MATCHED") == "skos:relatedMatch"

    exact_cand = CandidateChannelInputs(
        entry_id="e",
        label="hasTrade",
        entry_kind="datatype_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="temporal"),
    )
    assert determine_suggested_predicate("trade_dt", exact_cand, 0.95, "MATCHED") == "skos:exactMatch"

    # Opaque header -> insufficient_evidence
    opaque_res: CalibrationOutcome = select_and_calibrate(
        ColumnCalibrationContext(column_name="COL_01", n_eff=5),
        [],
        params,
        policy,
    )
    assert opaque_res.status == "NO_MATCH"
    assert opaque_res.no_match_category == "insufficient_evidence"
    assert not contains_forbidden_jargon(opaque_res.steward_summary)


def test_overlay_and_project_graph_a_source_wave2_features() -> None:
    """Verify SCEN-04 planned concepts, Wave-1 folding, PINNED reference URI, MUTED & full_rebuild."""
    ontology = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="am_ibor",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am_ibor:LegalEntity",
                concept_name="LegalEntity",
                description="Committed Wave-1 legal entity concept",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri=_FIBO_LE,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256="a" * 64,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9200"),
                    )
                ],
                attributes=[
                    ConceptAttribute(
                        name="legal_entity_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                        standard_property_uri=_FIBO_LEI,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="s1", table_id="s1:issuers", col_name="lei")
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am_ibor:PlannedMandate",
                concept_name="PlannedMandate",
                description="SCEN-04 planned concept with zero table bindings",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                table_bindings=[],
            ),
        ],
        enumerations=[
            OntologyEnumeration(
                enum_uri="urn:opendv:am_ibor:enum:Ccy",
                enumeration_name="CurrencyCode",
                target_reference_table="ref_currency",
                codes={"USD": "US Dollar", "EUR": "Euro"},
                description="Committed Wave-1 currency enum",
                standard_uri=_FIBO_CCY,
            )
        ],
        relationships=[
            OntologyRelationship(
                relationship_uri="urn:opendv:am_ibor:rel:IssuedBy",
                relationship_name="IssuedBy",
                source_concept_uri="urn:opendv:am_ibor:PlannedMandate",
                target_concept_uri="urn:opendv:am_ibor:LegalEntity",
                cardinality="MANY_TO_ONE",
                target_link="lnk_mandate_issuer",
                grain_semantics="One issuer per mandate",
                description="Issuer relationship",
            )
        ],
    )

    uncommitted_fibo_uri = "https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/Security"
    muted_fibo_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Noise/NoiseClass"
    base_items = [
        KnowledgeItem(
            entry_id=_FIBO_LE,
            label="LegalEntity",
            entry_kind="class",
            definition="FIBO legal entity",
            aliases=["LE"],
            parent_ids=["https://www.omg.org/spec/Commons/PartiesAndSituations/Agent"],
            semantic_context=SemanticContext(archetype="entity"),
        ),
        KnowledgeItem(
            entry_id=uncommitted_fibo_uri,
            label="Security",
            entry_kind="class",
            definition="Financial instrument",
            semantic_context=SemanticContext(archetype="entity"),
        ),
        KnowledgeItem(
            entry_id=muted_fibo_uri,
            label="NoiseClass",
            entry_kind="class",
            definition="Muted noisy class",
            semantic_context=SemanticContext(archetype="entity"),
        ),
    ]
    base_src = _StubBaseSource(base_items)
    assert isinstance(base_src, KnowledgeSource)

    memory = [
        ConceptMemorySpec(term="Security", state="PINNED", concept_uri=uncommitted_fibo_uri),
        ConceptMemorySpec(term="NoiseClass", state="MUTED", concept_uri=muted_fibo_uri),
    ]
    overlay = OverlayKnowledgeSource(
        base_src,
        ontology=ontology,
        concept_memory=memory,
        steward_answers={"q1": "Mandate tracks client asset allocation rules"},
        discovery_mode="incremental_new",
    )
    assert isinstance(overlay, KnowledgeSource)
    assert overlay.is_bypassed is False

    # SCEN-04 planned concept is indexed & recalled
    planned_hits = overlay.recall_by_text("PlannedMandate")
    assert any(h.entry_id == "urn:opendv:am_ibor:PlannedMandate" for h in planned_hits)

    # Wave-1 FIBO URI folds to project_graph_a concept URI and combines ancestors
    le_hits = overlay.recall_by_text("LegalEntity")
    folded = next(h for h in le_hits if h.entry_id == "urn:opendv:am_ibor:LegalEntity")
    assert folded.origin_kind == "project_graph_a"
    ancestors = overlay.get_ancestors("urn:opendv:am_ibor:LegalEntity")
    assert _FIBO_LE in ancestors
    assert "https://www.omg.org/spec/Commons/PartiesAndSituations/Agent" in ancestors

    # PINNED uncommitted FIBO URI preserves origin_kind="reference_standard"
    sec_hits = overlay.recall_by_text("Security")
    sec_hit = next(h for h in sec_hits if h.entry_id == uncommitted_fibo_uri)
    assert sec_hit.origin_kind == "reference_standard"

    # MUTED entry is filtered out
    assert all(h.entry_id != muted_fibo_uri for h in overlay.recall_by_text("NoiseClass"))
    assert overlay.score("NoiseClass", muted_fibo_uri) == Decimal("0.0000")

    # Free-text steward answer expands terms
    expanded = overlay.expand_query_terms(["mandate"])
    assert "allocation" in expanded and "base_expanded" in expanded

    # TableReasoningContext forwards Wave-1 mapped FIBO URIs to base_source
    col_ev = ColumnEvidence(column_name="issuer_lei", data_type="VARCHAR")
    prof = InferredValueProfile(
        observed_sample_count=1,
        non_null_count=1,
        masked_count=0,
        declared_logical_type="UTF8_STRING",
        inferred_logical_type="UTF8_STRING",
        type_confidence=1.0,
        shape=ColumnShapeStats(profiled_non_null_count=1, distinct_ratio=1.0),
    )
    tbl_ctx = TableReasoningContext(
        table_name="holdings",
        anchor_hypotheses=[
            TableAnchorCandidate(
                entry_id="urn:opendv:am_ibor:LegalEntity",
                label="LegalEntity",
                archetype="entity",
                origin_kind="project_graph_a",
                confidence=0.90,
                suggested_predicate="skos:exactMatch",
            )
        ],
        fk_targets={"issuer_lei": "urn:opendv:am_ibor:LegalEntity"},
    )
    eval_res = overlay.evaluate_structured_candidates(col_ev, prof, [folded], table_context=tbl_ctx)
    assert eval_res and eval_res[0].entry_id == "urn:opendv:am_ibor:LegalEntity"
    forwarded_ctx = base_src.seen_table_contexts[-1]
    assert forwarded_ctx is not None
    assert any(a.entry_id == _FIBO_LE for a in forwarded_ctx.anchor_hypotheses)
    assert forwarded_ctx.fk_targets["issuer_lei"] == _FIBO_LE
    assert _FIBO_LE in base_src.seen_candidate_ids[-1]

    # full_rebuild bypasses project_source completely
    bypassed = OverlayKnowledgeSource(
        base_src,
        ontology=ontology,
        concept_memory=memory,
        discovery_mode="full_rebuild",
    )
    assert bypassed.is_bypassed is True
    assert all(h.origin_kind == "reference_standard" for h in bypassed.recall_by_text("LegalEntity"))
    assert any(h.entry_id == muted_fibo_uri for h in bypassed.recall_by_text("NoiseClass"))

    # Empty ontology + flat glossary base keeps anchor_entry_kinds == frozenset()
    flat_base = _StubBaseSource([], anchor_entry_kinds=frozenset())
    empty_proj = ProjectGraphASource(ontology=None)
    assert empty_proj.metadata.anchor_entry_kinds == frozenset()
    flat_overlay = OverlayKnowledgeSource(flat_base, empty_proj)
    assert flat_overlay.metadata.anchor_entry_kinds == frozenset()


def test_muted_project_uri_blocks_post_fold_resurrection() -> None:
    """BUG-1: Muting a Wave-1 project_graph_a concept URI blocks post-fold recall, get_item, score."""
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="am_ibor",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am_ibor:LegalEntity",
                concept_name="LegalEntity",
                description="Committed Wave-1 legal entity concept",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri=_FIBO_LE,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256="a" * 64,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9200"),
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="s1", table_id="s1:issuers", col_name="lei")
                ],
            )
        ],
    )
    base_src = _StubBaseSource(
        [
            KnowledgeItem(
                entry_id=_FIBO_LE,
                label="Legal Entity",
                entry_kind="class",
                definition="FIBO legal entity",
                semantic_context=SemanticContext(archetype="entity"),
            )
        ]
    )
    overlay = OverlayKnowledgeSource(
        base_src,
        ontology=ont,
        concept_memory=[
            ConceptMemorySpec(
                term="",
                state="MUTED",
                concept_uri="urn:opendv:am_ibor:LegalEntity",
            )
        ],
    )
    assert overlay.recall_by_text("Legal Entity") == []
    assert overlay.recall_by_text("LegalEntity") == []
    assert overlay.score("Legal Entity", _FIBO_LE) == Decimal("0.0000")
    assert overlay.score("LegalEntity", "urn:opendv:am_ibor:LegalEntity") == Decimal("0.0000")
    assert overlay.get_item(_FIBO_LE) is None
    assert overlay.get_item("urn:opendv:am_ibor:LegalEntity") is None

    bypassed = OverlayKnowledgeSource(
        base_src,
        ontology=ont,
        concept_memory=[
            ConceptMemorySpec(term="", state="MUTED", concept_uri="urn:opendv:am_ibor:LegalEntity")
        ],
        discovery_mode="full_rebuild",
    )
    assert bypassed.get_item(_FIBO_LE) is not None
    assert any(h.entry_id == _FIBO_LE for h in bypassed.recall_by_text("Legal Entity"))


def test_steward_summary_sanitizes_jargon_and_veto_drops_surface_obs() -> None:
    """BUG-3 & Challenge 2: Sanitize FORBIDDEN_GUIDED_JARGON and ignore vetoed surface rules."""
    params = CalibrationParams.analytic_defaults()
    policy = GroundingPolicy(mode="deterministic_only", skip_reason="unit test")
    jargon_tokens = [
        "hash_diff",
        "cdk_id",
        "TRAP-01",
        "CEGAR_flag",
        "DV-STR-01",
        "ONT-AP-2",
        "ALG.A.1a",
        "EVAL-AST-01",
        "BKCC",
        "SQLGlot",
        "TIER_U3",
    ]
    for tok in jargon_tokens:
        c_matched = CandidateChannelInputs(
            entry_id="e1",
            label=tok,
            entry_kind="datatype_property",
            origin_kind="reference_standard",
            semantic_context=SemanticContext(),
            lex_score=1.0,
            shape_range_score=1.0,
            base_anchor_score=1.0,
        )
        res_matched = select_and_calibrate(
            ColumnCalibrationContext(
                column_name=tok, n_eff=50, anchor_enabled=True, has_top_table_anchor=True
            ),
            [c_matched],
            params,
            policy,
        )
        assert not contains_forbidden_jargon(res_matched.steward_summary), res_matched.steward_summary

        res_nomatch = select_and_calibrate(
            ColumnCalibrationContext(column_name=tok, n_eff=10),
            [],
            params,
            policy,
        )
        assert not contains_forbidden_jargon(res_nomatch.steward_summary), res_nomatch.steward_summary

    # Challenge 2: hard_veto candidate with has_surface_rule=True must not activate "surface" in m_obs
    c_keep = CandidateChannelInputs(
        entry_id="keep",
        label="Issuer",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        lex_score=0.8,
        has_surface_rule=False,
    )
    c_vetoed = CandidateChannelInputs(
        entry_id="vetoed",
        label="Vetoed",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        lex_score=0.9,
        has_surface_rule=True,
        hard_veto=True,
    )
    scored, _, any_veto = softmax_with_null(
        ColumnCalibrationContext(column_name="issuer", n_eff=20, has_column_surface_pattern=False),
        [c_keep, c_vetoed],
        params,
    )
    assert any_veto is True
    assert len(scored) == 1
    assert "surface" not in scored[0].m_obs


def test_step4_projection_all_4_steps_structural_guards_and_commit() -> None:
    """Verify Step 1 triage, Step 2 structural guards, Step 3 Phase B, Step 4 tags, & compile."""
    from opendv.grounding.providers.fibo.loader import get_bundled_fibo_pack_sha256
    from opendv.models.events import GroundingReceipt
    from opendv.models.grounding import (
        CandidateMapping,
        ColumnGroundingResult,
        ColumnPair,
        ConfidenceInterval,
        GroundingResponse,
        TableGroundingResult,
    )
    from opendv.models.ontology import (
        CanonicalBusinessConcept,
        ConceptAttribute,
        ConceptTableBinding,
        ExternalMapping,
        GraphAOntology,
    )
    from opendv.models.project import EvolutionLockSpec
    from opendv.models.sources import ColumnProfile, ColumnShapeStats, SourceProfile, TableProfile
    from opendv.service._guided_errors import contains_forbidden_jargon
    from opendv.service.workflows._grounding_projection import project_grounding_to_deltas
    from opendv.service.workflows._ontology_deltas import compile_and_apply_deltas

    pack_sha = get_bundled_fibo_pack_sha256()
    fibo_le = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    fibo_bond = "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond"
    fibo_role = "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Roles/PartyInRole"
    fp = "d" * 64

    def _ci(lo: str, mid: str, hi: str) -> ConfidenceInterval:
        return ConfidenceInterval(
            point=float(mid),
            lower=float(lo),
            upper=float(hi),
            level=0.90,
            calibration_mode="analytic_logit_normal",
        )

    def _cand(
        eid: str,
        label: str,
        ek: str,
        pred: str,
        *,
        origin: str = "reference_standard",
        arch: str = "value_object",
        ident_uri: str | None = None,
        tgt_uri: str | None = None,
        played_uri: str | None = None,
        pii_hint: str | None = None,
        mid: str = "0.9100",
    ) -> CandidateMapping:
        return CandidateMapping(
            rank=1,
            entry_id=eid,
            label=label,
            entry_kind=ek,  # type: ignore[arg-type]
            origin_kind=origin,  # type: ignore[arg-type]
            suggested_predicate=pred,  # type: ignore[arg-type]
            confidence=_ci("0.8500", mid, "0.9600"),
            semantic_context=SemanticContext(
                archetype=arch,  # type: ignore[arg-type]
                identified_entity_uri=ident_uri,
                target_entity_uri=tgt_uri,
                played_by_uri=played_uri,
                pii_category_hint=pii_hint,
            ),
        )

    def _col(
        table_name: str,
        name: str,
        status: str,
        choices: list[CandidateMapping] | None = None,
        *,
        no_match_cat: str | None = None,
        broad: str | None = None,
        enum_vals: list[str] | None = None,
        distinct_r: float = 0.5,
    ) -> ColumnGroundingResult:
        return ColumnGroundingResult(
            table_name=table_name,
            column_name=name,
            status=status,  # type: ignore[arg-type]
            no_match_probability=0.05 if status == "MATCHED" else 0.70,
            no_match_category=no_match_cat,  # type: ignore[arg-type]
            steward_summary=f"Summary for {name}",
            suggested_broad_parent_uri=broad,
            execution_mode_used="agentic_reranked",
            selection_rule_applied="calibrator_top1",
            inferred_value_profile=InferredValueProfile(
                observed_sample_count=50,
                non_null_count=50,
                masked_count=0,
                declared_logical_type="UTF8_STRING",
                inferred_logical_type="UTF8_STRING",
                type_confidence=0.95,
                shape=ColumnShapeStats(
                    profiled_non_null_count=50,
                    distinct_ratio=distinct_r,
                    observed_enum_values=enum_vals or [],
                ),
            ),
            choices=choices or [],
        )

    def _cp(
        col_name: str,
        sql_type: str = "VARCHAR",
        *,
        is_pk: bool = False,
        distinct_count_est: int = 10,
        null_ratio: float = 0.0,
        fk_ref: str | None = None,
    ) -> ColumnProfile:
        return ColumnProfile(
            col_name=col_name,
            sql_type=sql_type,
            nullable=not is_pk,
            is_pk=is_pk,
            null_ratio=null_ratio,
            distinct_count_est=distinct_count_est,
            fk_ref=fk_ref,
        )

    bond_anchor_rcpt = GroundingReceipt(
        target_kind="table_anchor",
        table_id="s1:src_bonds",
        source_column=None,
        schema_fingerprint=fp,
        standard_uri=fibo_bond,
        reference_pack_id="fibo-core",
        reference_pack_sha256=pack_sha,
        confidence=Decimal("0.9200"),
        mapping_type="skos:exactMatch",
        provenance="server_verified",
    )
    imported_bad_rcpt = bond_anchor_rcpt.model_copy(
        update={
            "receipt_id": "grnd_imported_anchor",
            "table_id": "s1:src_unverified",
            "provenance": "imported_unverified",
        }
    )

    existing_ont = GraphAOntology(
        ontology_version="0.1.0",
        domain_namespace="am",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:LegalEntity",
                concept_name="LegalEntity",
                description="Existing legal entity hub with locked BK.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="lei_code",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                external_mappings=[
                    ExternalMapping(
                        standard_uri=fibo_le,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=pack_sha,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9300"),
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(
                        source_id="s1",
                        table_id="s1:seed_entities",
                        col_name="lei_code",
                    )
                ],
            )
        ],
    )
    locks = [
        EvolutionLockSpec(
            wave_number=1,
            locked_hub_bks={"urn:opendv:am:LegalEntity": ["lei_code"]},
            locked_links={"lnk_src_bonds": []},
        )
    ]

    t_profiles = [
        # 1. Main bonds table + AP-12 + Role + MAS + Junction + Unverified anchor + Held table
        TableProfile(
            table_id="s1:src_bonds",
            table_name="src_bonds",
            row_count_est=100,
            columns=[
                _cp("surrogate_id", "BIGINT", distinct_count_est=100),
                _cp("updated_at", "TIMESTAMP", distinct_count_est=10),
                _cp("isin_code", "VARCHAR", is_pk=True, distinct_count_est=100),
                _cp("cusip_alt", "VARCHAR", distinct_count_est=98),
                _cp("ambig_col", "VARCHAR", distinct_count_est=20),
                _cp("custom_rating", "VARCHAR", distinct_count_est=5),
                _cp("bond_status", "VARCHAR", distinct_count_est=3),
                _cp("maturity_dt", "DATE", distinct_count_est=40),
                _cp("ssn_tax_id", "VARCHAR", distinct_count_est=50, null_ratio=0.1),
                _cp("issuer_lei", "VARCHAR", distinct_count_est=15, fk_ref="src_entities(lei_code)"),
                _cp(
                    "parent_bond_id", "VARCHAR", distinct_count_est=10,
                    null_ratio=0.8, fk_ref="src_bonds(isin_code)",
                ),
            ],
        ),
        TableProfile(
            table_id="s1:src_ref_ccy",
            table_name="src_ref_ccy",
            row_count_est=3,
            columns=[
                _cp("ccy_cd", "CHAR(3)", is_pk=True, distinct_count_est=3),
                _cp("ccy_name", "VARCHAR", distinct_count_est=3),
            ],
        ),
        TableProfile(
            table_id="s1:src_issuer_role",
            table_name="src_issuer_role",
            row_count_est=20,
            columns=[_cp("party_lei", "VARCHAR", is_pk=True, distinct_count_est=20)],
        ),
        TableProfile(
            table_id="s1:src_bond_schedule",
            table_name="src_bond_schedule",
            row_count_est=200,
            columns=[
                _cp(
                    "bond_fk", "VARCHAR", is_pk=True, distinct_count_est=100,
                    fk_ref="src_bonds(isin_code)",
                ),
                _cp("seq_no", "INTEGER", is_pk=True, distinct_count_est=5),
            ],
        ),
        TableProfile(
            table_id="s1:src_trade_junc",
            table_name="src_trade_junc",
            row_count_est=50,
            columns=[
                _cp(
                    "bond_id_fk", "VARCHAR", is_pk=True, distinct_count_est=50,
                    fk_ref="src_bonds(isin_code)",
                ),
                _cp(
                    "issuer_id_fk", "VARCHAR", is_pk=True, distinct_count_est=15,
                    fk_ref="src_issuer_role(party_lei)",
                ),
                _cp("occurred_at", "TIMESTAMP", distinct_count_est=45),
                _cp("alloc_pct", "DECIMAL", distinct_count_est=20),
            ],
        ),
        TableProfile(
            table_id="s1:src_held",
            table_name="src_held",
            row_count_est=10,
            columns=[_cp("held_pk", "VARCHAR", is_pk=True, distinct_count_est=10)],
        ),
        TableProfile(
            table_id="s1:src_unverified",
            table_name="src_unverified",
            row_count_est=10,
            columns=[_cp("unv_code", "VARCHAR", is_pk=True, distinct_count_est=10)],
        ),
    ]

    resp = GroundingResponse(
        knowledge_source_id="fibo-core",
        knowledge_source_version="2026Q3",
        knowledge_source_sha256=pack_sha,
        engine_version="2.0",
        mode_used="agentic_reranked",
        tables=[
            TableGroundingResult(
                table_name="src_bonds",
                table_id="s1:src_bonds",
                inferred_table_anchors=[
                    TableAnchorCandidate(
                        entry_id=fibo_bond,
                        label="Bond",
                        origin_kind="reference_standard",
                        archetype="entity",
                        suggested_predicate="skos:exactMatch",
                        confidence=0.92,
                        grounding_receipt_id=bond_anchor_rcpt.receipt_id,
                    )
                ],
                paired_columns=[
                    ColumnPair(kind="alternative_keys", columns=["isin_code", "cusip_alt"]),
                    ColumnPair(kind="amount_currency", columns=["notional_amt", "ccy_cd"]),
                ],
                columns=[
                    _col("src_bonds", "surrogate_id", "NO_MATCH", no_match_cat="technical_surrogate"),
                    _col("src_bonds", "updated_at", "NO_MATCH", no_match_cat="system_audit_metadata"),
                    _col("src_bonds", "isin_code", "MATCHED", [
                        _cand(fibo_bond + "/isin", "isin code", "datatype_property",
                              "skos:exactMatch", arch="identifier", ident_uri=fibo_bond)
                    ], distinct_r=1.0),
                    _col("src_bonds", "cusip_alt", "MATCHED", [
                        _cand(fibo_bond + "/cusip", "cusip alt", "datatype_property",
                              "skos:exactMatch", arch="identifier", ident_uri=fibo_bond)
                    ], distinct_r=0.98),
                    _col("src_bonds", "ambig_col", "AMBIGUOUS", [
                        _cand("urn:cand:a", "Candidate A", "datatype_property", "skos:closeMatch")
                    ]),
                    _col(
                        "src_bonds", "custom_rating", "NO_MATCH",
                        no_match_cat="out_of_vocabulary_domain", broad=fibo_bond,
                    ),
                    _col("src_bonds", "bond_status", "MATCHED", [
                        _cand("urn:fibo:BondStatus", "BondStatus", "enumeration",
                              "skos:exactMatch", arch="code_classifier")
                    ], enum_vals=["ACTIVE", "MATURED"]),
                    _col("src_bonds", "maturity_dt", "MATCHED", [
                        _cand(fibo_bond + "/hasMaturityDate", "has maturity date",
                              "datatype_property", "skos:exactMatch", arch="temporal")
                    ]),
                    _col("src_bonds", "ssn_tax_id", "MATCHED", [
                        _cand("urn:fibo:SSN", "SSN", "datatype_property",
                              "skos:exactMatch", arch="identifier", pii_hint="GOV_ID")
                    ]),
                    _col("src_bonds", "issuer_lei", "MATCHED", [
                        _cand("urn:fibo:issuedBy", "issuedBy", "object_property",
                              "skos:exactMatch", arch="entity", tgt_uri=fibo_le)
                    ]),
                    _col("src_bonds", "parent_bond_id", "MATCHED", [
                        _cand("urn:fibo:hasParent", "hasParent", "object_property",
                              "skos:exactMatch", arch="entity", tgt_uri=fibo_bond)
                    ]),
                ],
            ),
            TableGroundingResult(
                table_name="src_ref_ccy",
                table_id="s1:src_ref_ccy",
                columns=[
                    _col("src_ref_ccy", "ccy_cd", "MATCHED", [
                        _cand("urn:fibo:CurrencyCode", "CurrencyCode", "enumeration",
                              "skos:exactMatch", arch="code_classifier")
                    ], enum_vals=["USD", "EUR"]),
                    _col("src_ref_ccy", "ccy_name", "MATCHED", [
                        _cand("urn:fibo:name", "name", "datatype_property", "skos:exactMatch")
                    ]),
                ],
            ),
            TableGroundingResult(
                table_name="src_issuer_role",
                table_id="s1:src_issuer_role",
                inferred_table_anchors=[
                    TableAnchorCandidate(
                        entry_id=fibo_role,
                        label="IssuerRole",
                        origin_kind="reference_standard",
                        archetype="party_role",
                        suggested_predicate="skos:exactMatch",
                        confidence=0.91,
                    )
                ],
                columns=[
                    _col("src_issuer_role", "party_lei", "MATCHED", [
                        _cand(fibo_le + "/lei", "party lei", "datatype_property",
                              "skos:exactMatch", arch="identifier",
                              ident_uri=fibo_le, played_uri=fibo_le)
                    ], distinct_r=1.0),
                ],
            ),
            TableGroundingResult(
                table_name="src_bond_schedule",
                table_id="s1:src_bond_schedule",
                columns=[
                    _col("src_bond_schedule", "bond_fk", "MATCHED", [
                        _cand(fibo_bond + "/bond", "bond ref", "object_property",
                              "skos:exactMatch", arch="entity", tgt_uri=fibo_bond)
                    ]),
                    _col("src_bond_schedule", "seq_no", "MATCHED", [
                        _cand("urn:fibo:seq", "sequence number", "datatype_property",
                              "skos:exactMatch")
                    ]),
                ],
            ),
            TableGroundingResult(
                table_name="src_trade_junc",
                table_id="s1:src_trade_junc",
                columns=[
                    _col("src_trade_junc", "bond_id_fk", "MATCHED", [
                        _cand("urn:fibo:bondRef", "bond ref", "object_property",
                              "skos:exactMatch", arch="entity", tgt_uri=fibo_bond)
                    ]),
                    _col("src_trade_junc", "issuer_id_fk", "MATCHED", [
                        _cand("urn:fibo:issuerRef", "issuer ref", "object_property",
                              "skos:exactMatch", arch="entity", tgt_uri=fibo_le)
                    ]),
                    _col("src_trade_junc", "occurred_at", "MATCHED", [
                        _cand("urn:fibo:occurredAt", "occurred at", "datatype_property",
                              "skos:exactMatch", arch="temporal")
                    ]),
                    _col("src_trade_junc", "alloc_pct", "MATCHED", [
                        _cand("urn:fibo:allocPct", "allocation pct", "datatype_property",
                              "skos:exactMatch", arch="quantity_measure")
                    ]),
                ],
            ),
            TableGroundingResult(
                table_name="src_held",
                table_id="s1:src_held",
                columns=[
                    _col("src_held", "held_pk", "AMBIGUOUS", [
                        _cand("urn:cand:h1", "Held Cand", "datatype_property", "skos:closeMatch")
                    ]),
                ],
            ),
            TableGroundingResult(
                table_name="src_unverified",
                table_id="s1:src_unverified",
                inferred_table_anchors=[
                    TableAnchorCandidate(
                        entry_id="urn:fibo:UnverifiedConcept",
                        label="UnverifiedConcept",
                        origin_kind="reference_standard",
                        archetype="entity",
                        suggested_predicate="skos:exactMatch",
                        confidence=0.92,
                        grounding_receipt_id=imported_bad_rcpt.receipt_id,
                    )
                ],
                columns=[
                    _col("src_unverified", "unv_code", "MATCHED", [
                        _cand("urn:fibo:unvCode", "unv code", "datatype_property",
                              "skos:exactMatch", arch="identifier")
                    ], distinct_r=1.0),
                ],
            ),
        ],
    )

    proj = project_grounding_to_deltas(
        resp,
        table_profiles=t_profiles,
        existing_ontology=existing_ont,
        table_id_map={},
        receipts_by_id={
            bond_anchor_rcpt.receipt_id: bond_anchor_rcpt,
            imported_bad_rcpt.receipt_id: imported_bad_rcpt,
        },
        evolution_locks=locks,
    )

    # Step 1 checks
    assert proj.system_surrogate_cols == ["s1:src_bonds:surrogate_id"]
    assert proj.system_metadata_cols == ["s1:src_bonds:updated_at"]
    assert len(proj.clarification_questions) == 2
    assert all(not contains_forbidden_jargon(q.prompt) for q in proj.clarification_questions)
    assert "s1:src_held" in proj.held_table_ids

    # Step 2 checks: AP-12 ref table emitted enum only (no AddConcept for RefCcy)
    added_concepts = [d for d in proj.suggested_deltas if d.op_type == "AddConcept"]
    assert all("RefCcy" not in d.concept_name for d in added_concepts)
    # Role table folded into existing LegalEntity (played_by_uri match) with AddTableBinding
    assert any(
        d.op_type == "AddTableBinding"
        and d.concept_uri == "urn:opendv:am:LegalEntity"
        and d.binding.table_id == "s1:src_issuer_role"
        for d in proj.suggested_deltas
    )
    # Unverified imported_unverified table anchor drops ExternalMapping (folds without external map)
    unv_concepts = [d for d in added_concepts if d.concept_name == "SrcUnverified"]
    assert len(unv_concepts) == 1
    assert unv_concepts[0].external_mappings == []

    # Step 3 checks: PII isolation tag + non-locked link suffix + self-ref hal_ + t_lnk junction
    bond_uri = "urn:opendv:am:Bond"
    assert any(
        d.op_type == "SetConceptGovernanceTags"
        and d.concept_uri == bond_uri
        and "PII_ISOLATION" in d.governance_tags
        and "s1:src_bonds:surrogate_id" in d.governance_constraints.get("system_surrogate_cols", [])
        and "s1:src_bond_schedule:seq_no" in d.governance_constraints.get("mas_cdks", [])
        for d in proj.suggested_deltas
    )
    rels = [d.relationship for d in proj.suggested_deltas if d.op_type == "AddRelationship"]
    assert any(r.target_link == "hal_parent_bond_id" for r in rels)
    # locked_links + low-non-null disambiguates Bond FK relationship target_link cleanly
    issuer_rel = next(r for r in rels if r.source_col_name == "issuer_lei")
    assert issuer_rel.target_link == "lnk_src_bonds_issuer_lei"
    # Temporal junction edge emits t_lnk_src_trade_junc with 2 link_attributes
    t_junc = next(r for r in rels if r.source_table_id == "s1:src_trade_junc")
    assert t_junc.cardinality == "MANY_TO_MANY"
    assert t_junc.target_link == "t_lnk_src_trade_junc"
    assert {a.name for a in t_junc.link_attributes} == {"occurred_at", "alloc_pct"}

    # Step 4 commit: full list of projected deltas applies cleanly via compile_and_apply_deltas
    src_profile = SourceProfile(
        source_id="s1", schema_fingerprint=fp, table_count=len(t_profiles), tables=t_profiles,
    )
    committed = compile_and_apply_deltas(
        existing_ont,
        proj.suggested_deltas,
        actor="agent",
        locks=locks,
        receipts_by_id={bond_anchor_rcpt.receipt_id: bond_anchor_rcpt},
        profiles=[src_profile],
    )
    bond_concept = next(c for c in committed.concepts if c.concept_uri == bond_uri)
    assert bond_concept.external_mappings[0].grounding_receipt_id == bond_anchor_rcpt.receipt_id
    mat_attr = next(a for a in bond_concept.attributes if a.name == "maturity_dt")
    assert mat_attr.logical_type == "DATE32"
    assert mat_attr.standard_property_uri == fibo_bond + "/hasMaturityDate"

    # MAS Child Guard checks on src_bond_schedule: 1 AddTableBinding(bond_fk), 0 self-rel, 0 CDK BK
    mas_bindings = [
        d for d in proj.suggested_deltas
        if d.op_type == "AddTableBinding" and d.binding.table_id == "s1:src_bond_schedule"
    ]
    assert len(mas_bindings) == 1
    assert mas_bindings[0].concept_uri == bond_uri
    assert mas_bindings[0].binding.col_name == "bond_fk"
    assert not any(
        r.source_concept_uri == bond_uri
        and r.target_concept_uri == bond_uri
        and r.target_link == "lnk_src_bond_schedule"
        for r in rels
    )
    seq_attr = next(a for a in bond_concept.attributes if a.name == "seq_no")
    assert seq_attr.is_business_key is False


@pytest.mark.parametrize(
    "seq_mode",
    ["datatype_property", "no_match_row9", "identifier_row3"],
)
def test_mas_child_guard_step3_excludes_fk_and_protects_cdk(seq_mode: str) -> None:
    """Verify MAS Child Guard skips fk_col and never promotes cdk_col to BK/binding/auth."""
    from opendv.grounding.providers.fibo.loader import get_bundled_fibo_pack_sha256
    from opendv.models.grounding import (
        CandidateMapping,
        ColumnGroundingResult,
        ConfidenceInterval,
        GroundingResponse,
        TableGroundingResult,
    )
    from opendv.models.sources import ColumnProfile, TableProfile
    from opendv.service.workflows._grounding_projection import project_grounding_to_deltas
    from opendv.service.workflows._ontology_deltas import compile_and_apply_deltas

    pack_sha = get_bundled_fibo_pack_sha256()
    fibo_bond = "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond"
    bond_uri = "urn:opendv:am:Bond"
    existing_ont = GraphAOntology(
        ontology_version="0.1.0",
        domain_namespace="am",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri=bond_uri,
                concept_name="Bond",
                description="Existing bond concept.",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="isin_code",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                external_mappings=[
                    ExternalMapping(
                        standard_uri=fibo_bond,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=pack_sha,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9300"),
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(
                        source_id="s1",
                        table_id="s1:src_bonds",
                        col_name="isin_code",
                    )
                ],
            )
        ],
    )

    ci = ConfidenceInterval(
        point=0.91,
        lower=0.85,
        upper=0.96,
        level=0.90,
        calibration_mode="analytic_logit_normal",
    )

    def _col(
        name: str,
        status: str,
        choices: list[CandidateMapping] | None = None,
        *,
        no_match_cat: str | None = None,
    ) -> ColumnGroundingResult:
        return ColumnGroundingResult(
            table_name="src_bond_schedule",
            column_name=name,
            status=status,  # type: ignore[arg-type]
            no_match_probability=0.05 if status == "MATCHED" else 0.70,
            no_match_category=no_match_cat,  # type: ignore[arg-type]
            steward_summary=f"Summary for {name}",
            execution_mode_used="agentic_reranked",
            selection_rule_applied="calibrator_top1",
            inferred_value_profile=InferredValueProfile(
                observed_sample_count=50,
                non_null_count=50,
                masked_count=0,
                declared_logical_type="UTF8_STRING",
                inferred_logical_type="UTF8_STRING",
                type_confidence=0.95,
                shape=ColumnShapeStats(profiled_non_null_count=50, distinct_ratio=0.5),
            ),
            choices=choices or [],
        )

    if seq_mode == "datatype_property":
        seq_col = _col(
            "seq_no",
            "MATCHED",
            [
                CandidateMapping(
                    rank=1,
                    entry_id="urn:fibo:seq",
                    label="sequence number",
                    entry_kind="datatype_property",
                    origin_kind="reference_standard",
                    suggested_predicate="skos:exactMatch",
                    confidence=ci,
                    semantic_context=SemanticContext(archetype="value_object"),
                )
            ],
        )
    elif seq_mode == "no_match_row9":
        seq_col = _col("seq_no", "NO_MATCH", no_match_cat="out_of_vocabulary_domain")
    else:
        seq_col = _col(
            "seq_no",
            "MATCHED",
            [
                CandidateMapping(
                    rank=1,
                    entry_id="urn:fibo:seqId",
                    label="sequence id",
                    entry_kind="datatype_property",
                    origin_kind="reference_standard",
                    suggested_predicate="skos:exactMatch",
                    confidence=ci,
                    semantic_context=SemanticContext(archetype="identifier"),
                )
            ],
        )

    t_profiles = [
        TableProfile(
            table_id="s1:src_bond_schedule",
            table_name="src_bond_schedule",
            row_count_est=200,
            columns=[
                ColumnProfile(
                    col_name="bond_fk",
                    sql_type="VARCHAR",
                    nullable=False,
                    is_pk=True,
                    null_ratio=0.0,
                    distinct_count_est=100,
                    fk_ref="src_bonds(isin_code)",
                ),
                ColumnProfile(
                    col_name="seq_no",
                    sql_type="INTEGER",
                    nullable=False,
                    is_pk=True,
                    null_ratio=0.0,
                    distinct_count_est=5,
                ),
                ColumnProfile(
                    col_name="leg_amt",
                    sql_type="DECIMAL(18,2)",
                    nullable=False,
                    is_pk=False,
                    null_ratio=0.0,
                    distinct_count_est=120,
                ),
            ],
        )
    ]

    resp = GroundingResponse(
        knowledge_source_id="fibo-core",
        knowledge_source_version="2026Q3",
        knowledge_source_sha256=pack_sha,
        engine_version="2.0",
        mode_used="agentic_reranked",
        tables=[
            TableGroundingResult(
                table_name="src_bond_schedule",
                table_id="s1:src_bond_schedule",
                columns=[
                    _col(
                        "bond_fk",
                        "MATCHED",
                        [
                            CandidateMapping(
                                rank=1,
                                entry_id=fibo_bond + "/bond",
                                label="bond ref",
                                entry_kind="object_property",
                                origin_kind="reference_standard",
                                suggested_predicate="skos:exactMatch",
                                confidence=ci,
                                semantic_context=SemanticContext(
                                    archetype="entity",
                                    target_entity_uri=fibo_bond,
                                ),
                            )
                        ],
                    ),
                    seq_col,
                    _col(
                        "leg_amt",
                        "MATCHED",
                        [
                            CandidateMapping(
                                rank=1,
                                entry_id="urn:fibo:legAmount",
                                label="leg amount",
                                entry_kind="datatype_property",
                                origin_kind="reference_standard",
                                suggested_predicate="skos:exactMatch",
                                confidence=ci,
                                semantic_context=SemanticContext(archetype="quantity_measure"),
                            )
                        ],
                    ),
                ],
            )
        ],
    )

    proj = project_grounding_to_deltas(
        resp,
        table_profiles=t_profiles,
        existing_ontology=existing_ont,
        table_id_map={},
        receipts_by_id={},
    )

    bindings = [d for d in proj.suggested_deltas if d.op_type == "AddTableBinding"]
    assert len(bindings) == 1
    assert bindings[0].concept_uri == bond_uri
    assert bindings[0].binding.table_id == "s1:src_bond_schedule"
    assert bindings[0].binding.col_name == "bond_fk"
    assert not any(d.binding.col_name == "seq_no" for d in bindings)

    assert any(
        d.op_type == "SetConceptGovernanceTags"
        and d.concept_uri == bond_uri
        and "s1:src_bond_schedule:seq_no" in d.governance_constraints.get("mas_cdks", [])
        for d in proj.suggested_deltas
    )

    rels = [d.relationship for d in proj.suggested_deltas if d.op_type == "AddRelationship"]
    assert rels == []

    assert not any(
        d.op_type in ("SetConceptKeyAuthority", "AddKeyAuthorityPattern")
        for d in proj.suggested_deltas
    )

    added_attrs = [d for d in proj.suggested_deltas if d.op_type == "AddAttribute"]
    assert not any(d.attribute.is_business_key for d in added_attrs)
    attr_by_name = {d.attribute.name: d.attribute for d in added_attrs}
    assert set(attr_by_name) == {"seq_no", "leg_amt"}
    assert attr_by_name["seq_no"].is_business_key is False
    assert attr_by_name["leg_amt"].is_business_key is False

    committed = compile_and_apply_deltas(existing_ont, proj.suggested_deltas, actor="agent")
    bond_c = next(c for c in committed.concepts if c.concept_uri == bond_uri)
    assert bond_c.key_authority_tier == "TIER_1_GLOBAL_STANDARD"
    assert {a.name for a in bond_c.attributes if a.is_business_key} == {"isin_code"}
    assert "leg_amt" in {a.name for a in bond_c.attributes}


